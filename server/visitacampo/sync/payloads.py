"""Validación **estricta y por operación** del payload de push (RD-03/RD-02).

Esta es la frontera de la que habla RD-03: nada de lo que llega en `ops[].payload`
toca la base de datos sin haber pasado por un tipo sellado de este módulo.
"Sellado" aquí significa literalmente `extra="forbid"`: un campo que el
contrato no declara **no se ignora en silencio**, se rechaza. Ignorarlo sería
la versión servidor del hallazgo H-02/H-03 de la auditoría (validar con un
`assert` que en release desaparece, o aceptar un dato y no usarlo): el agente
de campo creería haber guardado algo que el servidor nunca escribió.

**Nada de aquí lanza excepciones hacia arriba.** `parse_op` devuelve o un
`VisitWrite` (dato ya validado, listo para el CAS) o un `Rejection`
(estructurado, con `code`/`message`/`details` del contrato). Esa es la garantía
de "payload que no valida → `rejected`, nunca un 500" del criterio de salida:
no depende de que alguien se acuerde de poner un `try` alrededor.

**Condicionalidad por `op_type`**, tal y como el contrato la delega en el
servidor ("esa condicionalidad se valida en el servidor (Pydantic v2) y no se
expresa aquí para no romper la generación de modelos"):

- `create` exige `base_version: null` y el conjunto completo
  (`template_version_id`, `answers`, `captured_at`, `device_id`).
- `update` exige `base_version` conocido y admite solo los campos que cambian;
  se distingue "campo ausente" de "campo enviado como `null`" con
  `model_fields_set`, porque poner `assignment_id: null` es una escritura
  legítima (desligar la visita de su asignación) y no lo mismo que no tocarlo.
- `delete` exige `base_version` y **solo** `id`. Se rechaza un `delete` que
  traiga más campos en vez de ignorarlos: ver el párrafo de arriba. (Nota para
  el Paso 13: el cliente debe enviar `payload: {"id": ...}` a secas en los
  borrados.)

**Lo que se valida aquí no es solo "la forma del contrato", es "lo que la base
de datos puede aceptar".** Un dato bien formado según el contrato pero
imposible de escribir revienta el `INSERT`/`UPDATE` con un `IntegrityError` o
un `DataError` que —dentro de la transacción única del lote— se lleva por
delante **las 500 operaciones**, no solo la mala, y sale como `500`. Para el
cliente eso es un veneno: la outbox reintenta el mismo lote para siempre y el
agente de campo no ve nunca por qué. Es exactamente el criterio de salida
"`rejected` nunca es un 500 sin manejar", así que las tres puertas están aquí,
antes de que el dato exista para `sql.py`:

1. **`null` explícito en una columna `NOT NULL`** (`_NON_NULLABLE_ON_UPDATE`).
   `update` distingue ausente de `null` a propósito, pero esa distinción solo
   es legítima donde la columna admite `NULL` (`assignment_id`, `lat`, `lon`,
   `accuracy_m`, `altitude_m`). Un `template_version_id: null` es un
   `NotNullViolation`, no una escritura.
2. **`NaN`/`Infinity` dentro de `answers`.** `json.loads` de la librería
   estándar —la que usa Django Ninja— acepta esos literales aunque JSON no los
   defina; `jsonb` de PostgreSQL los rechaza.
3. **`U+0000` dentro de `answers`.** `jsonb` no puede representarlo
   ("unsupported Unicode escape sequence"), ni en una clave ni en un valor.
4. **Surrogates Unicode no emparejados dentro de `answers`** (revisión del Paso
   8). `json.loads` acepta `"\\ud800"` y produce un `str` de Python que contiene
   un surrogate suelto; como `sql.py` serializa con `json.dumps` por defecto
   (`ensure_ascii=True`), el surrogate viaja a PostgreSQL como el escape literal
   `\\ud800` y el cast `::jsonb` lo rechaza con "Unicode low surrogate must
   follow a high surrogate". Nótese que **no** revienta antes en Python: el
   `json.dumps` no falla, así que sin esta puerta el único sitio donde se nota
   es dentro de la transacción, en forma de `DataError`.
5. **`NaN`/`Infinity` en los campos `float` del propio payload** (`Finite`,
   revisión del Paso 8). Ver el comentario de `Finite`: `lat`/`lon` estaban a
   salvo por accidente y `accuracy_m`/`altitude_m` no lo estaban.

Los cinco se comprueban sobre la estructura ya parseada, **sin base de datos**,
y por eso `tests/test_payloads.py` sí los ejecuta en este entorno.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
)

from visitacampo.core.api_errors import redacted_validation_details
from visitacampo.core.models import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN

__all__ = [
    "CODE_NOT_FOUND",
    "CODE_VALIDATION_ERROR",
    "AttachmentWrite",
    "OpType",
    "Rejection",
    "VisitWrite",
    "parse_op",
]

#: Códigos de `components.schemas.ErrorCode` que este módulo emite.
CODE_VALIDATION_ERROR = "validation_error"
CODE_NOT_FOUND = "not_found"

OpType = Literal["create", "update", "delete"]

_OP_TYPES: frozenset[str] = frozenset({"create", "update", "delete"})

#: Entidades escribibles por el push. `visit` desde el Paso 8; `visit_attachment`
#: desde el Paso 22, porque una respuesta de tipo foto/firma guarda el `id` de un
#: adjunto y sin esta escritura ese `id` no apuntaría a nada. `assignment` y
#: `form_template_version` existen en el enum porque el **pull** las entrega;
#: una operación sobre ellas se rechaza con detalle, nunca se descarta en silencio.
WRITABLE_ENTITIES: frozenset[str] = frozenset({"visit", "visit_attachment"})

#: Operaciones admitidas sobre `visit_attachment`. Solo el alta: el adjunto se
#: crea junto a su visita y, en el MVP, nada en la app lo modifica ni lo borra
#: después (repetir una foto antes de guardar no llega a crear el primero).
#: `update`/`delete` se rechazan con detalle hasta que exista ese caso de uso.
ATTACHMENT_OP_TYPES: frozenset[str] = frozenset({"create"})


@dataclass(frozen=True, slots=True)
class Rejection:
    """Una operación rechazada, ya en la forma del cuerpo `Error` del contrato.

    `entity_id` es el identificador de destino cuando se pudo averiguar. Es
    `None` cuando el payload ni siquiera trae un `id` legible, y eso tiene una
    consecuencia visible en `services.py`: sin `entity_id` no se puede escribir
    la fila de `sync_op_log` (la columna es `NOT NULL`), así que ese rechazo
    concreto no queda registrado. No se pierde nada: rechazar es una función
    **pura** del payload, sin efectos secundarios, de modo que reenviar la
    misma operación vuelve a dar exactamente el mismo `rejected`.
    """

    code: str
    message: str
    details: dict[str, Any] | None = None
    entity_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class VisitWrite:
    """Operación de `visit` ya validada: lo único que `sql.py` acepta.

    `fields` usa los nombres del **contrato** (`assignment_id`,
    `template_version_id`, `device_id`…), no los del ORM; la traducción a
    columnas la hace `sql.py` a partir de `Visit._meta`, en un solo sitio.
    """

    entity_id: uuid.UUID
    op_type: OpType
    base_version: int | None
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AttachmentWrite:
    """Alta de `visit_attachment` ya validada (Paso 22).

    `fields` usa los nombres del contrato (`visit_id`, `sha256`, `role`,
    `caption`, `captured_at`, `order`). Que la visita exista **en esta
    organización** no se sabe aquí —es una consulta—: lo comprueba
    `services.py` en bloque, antes del bucle.
    """

    entity_id: uuid.UUID
    fields: dict[str, Any]
    op_type: OpType = "create"
    base_version: None = None


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    """`answers` tal y como `jsonb` puede almacenarlo, o `ValueError`.

    Recorre la estructura con una pila explícita y no por recursión: la
    profundidad la elige el cliente, y una `RecursionError` aquí sería otra
    forma del mismo 500 que esta función existe para impedir.

    Solo tres venenos son alcanzables. Los enteros gigantes —el cuarto límite de
    `numeric`— no lo son: CPython corta la conversión decimal en 4300 dígitos
    (`sys.set_int_max_str_digits`) y `json.loads` ya falla al parsear, mucho
    antes de los 131072 dígitos que `jsonb` no soportaría.

    El tercero (surrogates no emparejados) se detecta intentando codificar a
    UTF-8, que es exactamente la operación que PostgreSQL no podrá hacer: un
    `str` con un surrogate suelto es el **único** `str` de Python que
    `.encode("utf-8")` no sabe representar, así que no hace falta ningún
    recorrido de code points a mano.
    """
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                raise ValueError("answers cannot contain the U+0000 character.")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("answers cannot contain unpaired Unicode surrogates.") from exc
        elif isinstance(item, bool):
            continue  # `bool` es subclase de `int`, no de `float`: nada que mirar.
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("answers cannot contain NaN or Infinity.")
        elif isinstance(item, dict):
            for key, nested in item.items():
                stack.append(key)
                stack.append(nested)
        elif isinstance(item, list):
            stack.extend(item)
    return value


#: `answers` validado como JSON almacenable, no solo como "objeto".
AnswersJson = Annotated[dict[str, Any], AfterValidator(_json_safe)]

#: **Todo** campo `float` de este módulo, sin excepción (revisión del Paso 8).
#:
#: `NaN`/`±Infinity` son valores legales de `double precision` en PostgreSQL, así
#: que un `altitude_m: NaN` se escribía **con éxito** y la operación respondía
#: `applied`. La bomba estallaba después, lejos de su causa y sin nada que la
#: relacionara con ella:
#:
#: - si esa fila entraba luego en un `conflict`, `records.visit_record()` la
#:   serializaba a `sync_op_log.result` (`JSONField`→`jsonb`) y el encoder de
#:   Django usa `allow_nan=True`, con lo que emitía el literal desnudo `NaN`
#:   dentro del `jsonb` ⇒ `DataError` ⇒ 500 con pérdida del lote entero;
#: - y el pull (Paso 9) devolvería un cuerpo con `NaN` desnudo, que **no es
#:   JSON válido**: el `jsonDecode` de Dart lo rechaza y el dispositivo deja de
#:   poder sincronizar del todo.
#:
#: Se aplica a los cuatro campos y no solo a los dos que estaban rotos. `lat` y
#: `lon` ya rechazaban los no finitos, pero **por accidente**: sus guardas
#: `ge`/`le` existen para el rango de coordenadas y en Python toda comparación
#: con `NaN` es falsa. Confiar en ese accidente es lo que dejó pasar
#: `accuracy_m: Infinity` (`inf >= 0` sí es cierto) y `altitude_m`, que no
#: tiene rango que aplicar. Con `Finite` la propiedad "no entra un no finito"
#: deja de depender de que el campo tenga además una guarda de rango, así que
#: un campo `float` futuro no puede reintroducir el mismo defecto por omisión.
Finite = Annotated[float, Field(allow_inf_nan=False)]


def _text_safe(value: str) -> str:
    """Texto que una columna `varchar` de PostgreSQL puede guardar.

    Las mismas dos trampas que `_json_safe` caza dentro de `answers`, aquí en
    un campo de texto plano: `U+0000` y los surrogates sueltos revientan el
    `INSERT` con un `DataError` dentro de la transacción del lote.
    """
    if "\x00" in value:
        raise ValueError("text cannot contain the U+0000 character.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("text cannot contain unpaired Unicode surrogates.") from exc
    return value


#: `sha256` hexadecimal en minúsculas: la misma forma que la clave de `Blob`.
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

#: Pie de foto: `varchar(512)` sin los caracteres que PostgreSQL no admite.
Caption = Annotated[str, StringConstraints(max_length=512), AfterValidator(_text_safe)]

#: Mayor valor de `PositiveIntegerField` en PostgreSQL (`integer`).
_MAX_ORDER = 2_147_483_647


class _AttachmentCreatePayload(BaseModel):
    """Contrato `VisitAttachmentPayload` en su forma completa (alta)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    visit_id: uuid.UUID
    # El blob puede no estar subido todavía (contrato): la fila referencia el
    # hash y la subida llega después por el carril de adjuntos.
    sha256: Sha256Hex
    role: Literal["foto", "firma", "doc"]
    caption: Caption | None = None
    captured_at: AwareDatetime
    # `StrictInt`: `true` no es la posición 1.
    order: StrictInt = Field(default=0, ge=0, le=_MAX_ORDER)


#: Campos que un `update` puede omitir pero **no** enviar como `null`: su
#: columna es `NOT NULL` en `Visit` (Paso 5) y el contrato tampoco los declara
#: nulables. Los que faltan de esta lista (`assignment_id`, `lat`, `lon`,
#: `accuracy_m`, `altitude_m`) sí admiten `null` y ponerlo es una escritura
#: legítima.
_NON_NULLABLE_ON_UPDATE: tuple[str, ...] = (
    "template_version_id",
    "answers",
    "captured_at",
    "device_id",
)


class _VisitCreatePayload(BaseModel):
    """Contrato `VisitPayload` en su forma completa (alta)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    assignment_id: uuid.UUID | None = None
    template_version_id: uuid.UUID
    answers: AnswersJson
    lat: Finite | None = Field(default=None, ge=LAT_MIN, le=LAT_MAX)
    lon: Finite | None = Field(default=None, ge=LON_MIN, le=LON_MAX)
    accuracy_m: Finite | None = Field(default=None, ge=0)
    altitude_m: Finite | None = None
    # `AwareDatetime` y no `datetime`: el contrato exige RFC 3339 **con offset
    # explícito**. Un instante sin zona no es un instante, y escribirlo con
    # `USE_TZ=True` solo produciría un aviso y un dato ambiguo para siempre.
    captured_at: AwareDatetime
    device_id: uuid.UUID


class _VisitUpdatePayload(BaseModel):
    """Contrato `VisitPayload` en su forma parcial (modificación)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    assignment_id: uuid.UUID | None = None
    template_version_id: uuid.UUID | None = None
    answers: AnswersJson | None = None
    lat: Finite | None = Field(default=None, ge=LAT_MIN, le=LAT_MAX)
    lon: Finite | None = Field(default=None, ge=LON_MIN, le=LON_MAX)
    accuracy_m: Finite | None = Field(default=None, ge=0)
    altitude_m: Finite | None = None
    captured_at: AwareDatetime | None = None
    device_id: uuid.UUID | None = None

    @field_validator(*_NON_NULLABLE_ON_UPDATE)
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        """`null` explícito en una columna `NOT NULL` ⇒ `rejected`, no `500`.

        El tipo tiene que admitir `None` para que el campo pueda **faltar**
        (es lo que hace opcional a un campo de Pydantic), así que la
        distinción entre "ausente" y "enviado como `null`" no la puede
        expresar la anotación: la hace este validador, que Pydantic v2 **no**
        ejecuta sobre los valores por defecto (`validate_default` es `False`)
        y sí sobre todo lo que venga en el payload. Ausente pasa; `null` se
        rechaza con el `loc` del campo, así que el `details` redactado le dice
        al cliente exactamente cuál fue.
        """
        if value is None:
            raise ValueError("this field is not nullable; omit it instead of sending null.")
        return value


class _VisitDeletePayload(BaseModel):
    """Un borrado no lleva datos: solo a quién borra."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID


#: Campos que un `update` puede escribir. `id` queda fuera: es el destino de la
#: sentencia, no una columna a modificar (renombrar una fila no es un update).
_UPDATABLE_FIELDS: tuple[str, ...] = (
    "assignment_id",
    "template_version_id",
    "answers",
    "lat",
    "lon",
    "accuracy_m",
    "altitude_m",
    "captured_at",
    "device_id",
)


def _rejection(
    message: str,
    *,
    field: str,
    reason: str,
    entity_id: uuid.UUID | None = None,
    code: str = CODE_VALIDATION_ERROR,
) -> Rejection:
    return Rejection(
        code=code,
        message=message,
        details={"field": field, "reason": reason},
        entity_id=entity_id,
    )


def target_id(payload: Any) -> uuid.UUID | None:
    """`payload["id"]` si es un UUID legible; `None` si no (best-effort).

    Se usa para poder registrar en `sync_op_log` incluso una operación que no
    valida — el rechazo queda auditado y su reenvío devuelve el resultado
    guardado, igual que cualquier otro. No valida nada más: si el `id` está
    bien pero el resto del payload no, el rechazo lo produce el modelo sellado.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("id")
    if isinstance(raw, uuid.UUID):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class _Invalid:
    """Centinela de "no válido" para `_parse_base_version`.

    Hace falta un centinela propio porque `None` es un valor **legítimo** de
    `base_version` (es justo lo que exige `create`), así que no puede servir
    a la vez de marca de error.
    """


_INVALID = _Invalid()


def _parse_base_version(value: Any, op_type: str) -> int | None | _Invalid:
    """Normaliza `base_version` según `op_type`, o `_INVALID`."""
    if op_type == "create":
        return None if value is None else _INVALID
    # `bool` es subclase de `int` en Python: `True` colaría como versión 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _INVALID
    return value


def parse_op(
    entity: str,
    op_type: Any,
    base_version: Any,
    payload: Any,
) -> VisitWrite | AttachmentWrite | Rejection:
    """Valida una operación del lote. Nunca lanza; devuelve dato o rechazo."""
    entity_id = target_id(payload)

    if entity not in WRITABLE_ENTITIES:
        return _rejection(
            f"Entity '{entity}' is not writable by this server version.",
            field="entity",
            reason="unsupported_entity",
            entity_id=entity_id,
        )

    if not isinstance(op_type, str) or op_type not in _OP_TYPES:
        return _rejection(
            "op_type must be one of create, update or delete.",
            field="op_type",
            reason="unsupported_op_type",
            entity_id=entity_id,
        )

    parsed_base = _parse_base_version(base_version, op_type)
    if isinstance(parsed_base, _Invalid):
        return _rejection(
            "create requires base_version null; update and delete require the known version.",
            field="base_version",
            reason="invalid_for_op_type",
            entity_id=entity_id,
        )

    if not isinstance(payload, dict):
        return _rejection(
            "payload must be an object.",
            field="payload",
            reason="not_an_object",
            entity_id=entity_id,
        )

    if entity == "visit_attachment":
        return _parse_attachment(op_type, payload, entity_id)

    validated: _VisitCreatePayload | _VisitUpdatePayload | _VisitDeletePayload
    try:
        if op_type == "create":
            validated = _VisitCreatePayload.model_validate(payload)
        elif op_type == "update":
            validated = _VisitUpdatePayload.model_validate(payload)
        else:
            validated = _VisitDeletePayload.model_validate(payload)
    except ValidationError as exc:
        return Rejection(
            code=CODE_VALIDATION_ERROR,
            message="Operation payload failed schema validation.",
            # Sin el valor que falló (RD-12): solo dónde y por qué.
            details=redacted_validation_details(exc.errors()),
            entity_id=entity_id,
        )

    if isinstance(validated, _VisitCreatePayload):
        fields = validated.model_dump()
        fields.pop("id")
    elif isinstance(validated, _VisitUpdatePayload):
        fields = {
            name: getattr(validated, name)
            for name in _UPDATABLE_FIELDS
            if name in validated.model_fields_set
        }
        if not fields:
            return _rejection(
                "update carries no field to write.",
                field="payload",
                reason="empty_update",
                entity_id=validated.id,
            )
    else:
        fields = {}

    return VisitWrite(
        entity_id=validated.id,
        op_type=op_type,  # type: ignore[arg-type]  # ya comprobado contra _OP_TYPES
        base_version=parsed_base,
        fields=fields,
    )


def _parse_attachment(
    op_type: str, payload: dict[str, Any], entity_id: uuid.UUID | None
) -> AttachmentWrite | Rejection:
    """`visit_attachment`: solo `create` (ver `ATTACHMENT_OP_TYPES`)."""
    if op_type not in ATTACHMENT_OP_TYPES:
        return _rejection(
            "visit_attachment only supports create in this server version.",
            field="op_type",
            reason="unsupported_op_type",
            entity_id=entity_id,
        )
    try:
        validated = _AttachmentCreatePayload.model_validate(payload)
    except ValidationError as exc:
        return Rejection(
            code=CODE_VALIDATION_ERROR,
            message="Operation payload failed schema validation.",
            details=redacted_validation_details(exc.errors()),
            entity_id=entity_id,
        )
    fields = validated.model_dump()
    fields.pop("id")
    return AttachmentWrite(entity_id=validated.id, fields=fields)

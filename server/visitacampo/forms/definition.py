"""El **meta-schema**: qué es una plantilla de formulario bien formada (Paso 20, tarea 2).

Una "plantilla" es el contenido de `FormTemplateVersion.definition`: JSON
declarativo con el catálogo de campos del formulario. Este módulo responde a
una sola pregunta —*¿es esta definición una plantilla válida?*— y lo hace
**antes de publicar la versión**, que es el único momento en que la respuesta
puede corregirse sin tocar datos históricos (regla de inmutabilidad, ver
`services.py`).

**Forma de una definición**::

    {
      "dsl": "visitacampo/form@1",
      "title": {"es": "Inspección de cultivo"},
      "fields": [
        {"key": "tiene_planta", "type": "boolean",
         "label": {"es": "¿Hay planta?"}, "required": true},
        {"key": "altura_cm", "type": "number",
         "label": {"es": "Altura (cm)"}, "required": true,
         "min": 0, "max": 500,
         "visible_if": {"op": "eq", "field": "tiene_planta", "value": true}}
      ]
    }

**El catálogo de tipos de campo es cerrado** (`FIELD_TYPES`). Un tipo nuevo se
añade aquí *y* en el binario del cliente (ADR-012: "catálogo de tipos de campo
extensible **en el binario**"), nunca describiendo comportamiento nuevo desde
la propia plantilla. Cada tipo sabe validar su propia respuesta
(`check_answer`), de modo que la regla de un tipo vive en un solo sitio junto a
su declaración y no se puede añadir un tipo olvidándose de validarlo.

**El meta-schema es JSON Schema de verdad**: `META_SCHEMA` es el documento que
Pydantic deriva de estos modelos sellados. Se emite —en vez de escribirse a
mano— para que no pueda divergir del validador que realmente corre.

**Sin `eval`, sin regex, sin código**: ver el comentario de arquitectura de
`visitacampo/forms/__init__.py` y el de `visibility.py`. En particular, este
módulo **no** admite una validación de campo por expresión regular: la longitud
y el rango se expresan con números, y lo que no se pueda expresar así se
implementa en el binario y se referencia por nombre (ADR-012).
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from visitacampo.core.api_errors import redacted_validation_details
from visitacampo.core.models import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN
from visitacampo.forms.visibility import (
    MAX_EXPR_DEPTH,
    AndOr,
    Comparison,
    FieldKey,
    Membership,
    Not,
    Presence,
    VisibilityExpr,
    expression_depth,
    referenced_fields,
)

__all__ = [
    "DSL_VERSION",
    "FIELD_TYPES",
    "MAX_FIELDS",
    "MAX_JSON_DEPTH",
    "META_SCHEMA",
    "SUPPORTED_DSL_VERSIONS",
    "FormDefinition",
    "FormFieldUnion",
    "TemplateDefinitionError",
    "check_json_depth",
    "has_declared_schema",
    "validate_definition",
]

#: Dialecto de este paquete. Va **dentro** de la definición para que una
#: plantilla diga por sí misma qué vocabulario habla: un servidor futuro que
#: soporte `@2` podrá distinguir sin adivinar, y uno antiguo que reciba `@2`
#: rechazará la plantilla en vez de interpretarla a medias.
DSL_VERSION = "visitacampo/form@1"

#: Dialectos que **este** servidor sabe validar e interpretar.
SUPPORTED_DSL_VERSIONS: frozenset[str] = frozenset({DSL_VERSION})

#: Campos máximos por plantilla. Un formulario de campo con más de esto no es
#: un formulario, es un problema de diseño; y acotarlo mantiene acotado el
#: coste de validar `answers` dentro del bucle de push.
MAX_FIELDS = 300

#: Opciones máximas de un campo de selección.
MAX_OPTIONS = 200

#: Profundidad máxima del JSON crudo de una definición, comprobada **antes** de
#: parsearlo. Pydantic parsea recursivamente, así que un JSON con miles de
#: niveles agotaría la pila durante la validación; el mismo razonamiento (y la
#: misma solución con pila explícita) que `sync/payloads.py::_json_safe`.
MAX_JSON_DEPTH = 24

#: Longitud máxima de un campo de texto libre.
MAX_TEXT_LENGTH = 4096

LangCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
LabelText = Annotated[str, StringConstraints(min_length=1, max_length=256)]

#: Etiqueta i18n: `{"es": "...", "en": "..."}`. Al menos un idioma; el cliente
#: elige el suyo y cae al primero declarado si no está el de la interfaz.
I18nText = Annotated[dict[LangCode, LabelText], Field(min_length=1, max_length=16)]

#: Valor de una opción: identificador estable que se guarda en `answers`. La
#: parte legible es su `label` (i18n) — nunca este valor.
OptionValue = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]


class TemplateDefinitionError(Exception):
    """La definición no cumple el meta-schema. Trae el detalle ya redactado (RD-12).

    **No** hereda de `ValueError` a propósito: el manejador global de
    `ValueError` de `core/api_errors.py` traduce cualquier `ValueError` que
    llegue a la capa de API en un `400 validation_error` con el mensaje "el
    cuerpo no se pudo parsear", que aquí sería mentira. Esta excepción la
    consume código que sabe qué hacer con ella (el servicio de publicación, el
    `clean()` del modelo, el bucle de push), nunca el manejador genérico.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


def check_json_depth(value: Any, limit: int = MAX_JSON_DEPTH) -> None:
    """Rechaza un JSON más anidado que `limit`, **sin recursión**.

    Se ejecuta antes de que Pydantic toque el dato: el parseo de Pydantic sí es
    recursivo y un anidamiento extremo lo rompería con un `RecursionError` en
    vez de con un error de validación legible.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            raise TemplateDefinitionError(
                f"Template definition is nested deeper than {limit} levels.",
                {"field": "definition", "reason": "too_deep"},
            )
        if isinstance(item, dict):
            stack.extend((nested, depth + 1) for nested in item.values())
        elif isinstance(item, list):
            stack.extend((nested, depth + 1) for nested in item)


class _Sealed(BaseModel):
    """Todo modelo de este paquete: sellado (`extra="forbid"`) e inmutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChoiceOption(_Sealed):
    value: OptionValue
    label: I18nText


class _FieldBase(_Sealed):
    """Lo que todo campo tiene, sea del tipo que sea.

    `check_answer` devuelve `None` si el valor es aceptable para este campo, o
    un `reason` estable (el que viaja en `Error.details`) si no lo es. Nunca
    lanza: corre dentro del bucle de push, donde una excepción sería un 500.
    """

    key: FieldKey
    label: I18nText
    help: I18nText | None = None
    required: bool = False
    visible_if: VisibilityExpr | None = None
    default: Any = None

    @model_validator(mode="after")
    def _check_default(self) -> _FieldBase:
        if self.default is not None:
            if isinstance(self, (_AttachmentRefField, RepeatField)):
                raise ValueError("defaults are not supported for attachments or repeat groups")
            if self.check_answer(self.default) is not None:
                raise ValueError("default does not satisfy the field constraints")
        return self

    def check_answer(self, value: Any) -> str | None:  # pragma: no cover - abstracto
        raise NotImplementedError


class TextField(_FieldBase):
    type: Literal["text"]
    multiline: bool = False
    min_length: int | None = Field(default=None, ge=0, le=MAX_TEXT_LENGTH)
    max_length: int | None = Field(default=None, ge=1, le=MAX_TEXT_LENGTH)

    @model_validator(mode="after")
    def _check_bounds(self) -> TextField:
        if self.min_length is not None and self.max_length is not None:
            if self.min_length > self.max_length:
                raise ValueError("min_length cannot be greater than max_length")
        return self

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return "not_a_string"
        if len(value) > MAX_TEXT_LENGTH:
            return "too_long"
        if self.min_length is not None and len(value) < self.min_length:
            return "too_short"
        if self.max_length is not None and len(value) > self.max_length:
            return "too_long"
        return None


class NumberField(_FieldBase):
    type: Literal["number"]
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check_bounds(self) -> NumberField:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min cannot be greater than max")
        return self

    def check_answer(self, value: Any) -> str | None:
        # `bool` es subclase de `int`: `true` no es un número (ver `visibility`).
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "not_a_number"
        if not math.isfinite(value):
            return "not_finite"
        if self.min is not None and value < self.min:
            return "out_of_range"
        if self.max is not None and value > self.max:
            return "out_of_range"
        return None


class IntegerField(_FieldBase):
    type: Literal["integer"]
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> IntegerField:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min cannot be greater than max")
        return self

    def check_answer(self, value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return "not_an_integer"
        if self.min is not None and value < self.min:
            return "out_of_range"
        if self.max is not None and value > self.max:
            return "out_of_range"
        return None


class BooleanField(_FieldBase):
    type: Literal["boolean"]

    def check_answer(self, value: Any) -> str | None:
        return None if isinstance(value, bool) else "not_a_boolean"


class DateField(_FieldBase):
    type: Literal["date"]

    def check_answer(self, value: Any) -> str | None:
        """`YYYY-MM-DD` estricto — el formato que ordena léxicamente."""
        if not isinstance(value, str):
            return "not_a_string"
        if len(value) != 10:
            return "not_a_date"
        try:
            date.fromisoformat(value)
        except ValueError:
            return "not_a_date"
        return None


class DateTimeField(_FieldBase):
    type: Literal["datetime"]

    def check_answer(self, value: Any) -> str | None:
        """RFC 3339 **con offset explícito** (mismo criterio que el contrato)."""
        if not isinstance(value, str):
            return "not_a_string"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return "not_a_datetime"
        return None if parsed.tzinfo is not None else "naive_datetime"


class _ChoiceBase(_FieldBase):
    options: list[ChoiceOption] = Field(min_length=1, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def _check_options(self) -> _ChoiceBase:
        values = [option.value for option in self.options]
        if len(set(values)) != len(values):
            raise ValueError("option values must be unique")
        return self

    @property
    def option_values(self) -> set[str]:
        return {option.value for option in self.options}


class SingleChoiceField(_ChoiceBase):
    type: Literal["single_choice"]

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return "not_a_string"
        return None if value in self.option_values else "not_an_option"


class MultiChoiceField(_ChoiceBase):
    type: Literal["multi_choice"]
    min_selected: int | None = Field(default=None, ge=0, le=MAX_OPTIONS)
    max_selected: int | None = Field(default=None, ge=1, le=MAX_OPTIONS)

    @model_validator(mode="after")
    def _check_selection_bounds(self) -> MultiChoiceField:
        if self.min_selected is not None and self.max_selected is not None:
            if self.min_selected > self.max_selected:
                raise ValueError("min_selected cannot be greater than max_selected")
        return self

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, list):
            return "not_a_list"
        if len(value) > MAX_OPTIONS:
            return "too_many_selected"
        allowed = self.option_values
        for item in value:
            if not isinstance(item, str):
                return "not_a_string"
            if item not in allowed:
                return "not_an_option"
        if len(set(value)) != len(value):
            return "duplicate_selection"
        if self.min_selected is not None and len(value) < self.min_selected:
            return "too_few_selected"
        if self.max_selected is not None and len(value) > self.max_selected:
            return "too_many_selected"
        return None


class _AttachmentRefField(_FieldBase):
    """Campo cuya respuesta es el `id` de un `visit_attachment` (Paso 10/17).

    La respuesta guarda **el identificador**, no el binario: los adjuntos
    viajan por su propio camino (contenido direccionado por `sha256`, ADR-007)
    y `answers` no es sitio para bytes. Que ese adjunto exista de verdad no se
    comprueba aquí —sería I/O dentro del bucle de push, y el adjunto puede
    subirse *después* de la visita por diseño (outbox + cola de adjuntos)—,
    solo que la respuesta tenga la forma de un identificador.
    """

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return "not_a_string"
        try:
            uuid.UUID(value)
        except ValueError:
            return "not_a_uuid"
        return None


class PhotoField(_AttachmentRefField):
    type: Literal["photo"]


class SignatureField(_AttachmentRefField):
    type: Literal["signature"]


class LocationField(_FieldBase):
    type: Literal["location"]

    def check_answer(self, value: Any) -> str | None:
        """`{"lat": ..., "lon": ...}` y nada más, con los rangos de ADR-005."""
        if not isinstance(value, dict):
            return "not_an_object"
        if set(value) != {"lat", "lon"}:
            return "not_a_location"
        for key, low, high in (("lat", LAT_MIN, LAT_MAX), ("lon", LON_MIN, LON_MAX)):
            coordinate = value[key]
            if isinstance(coordinate, bool) or not isinstance(coordinate, int | float):
                return "not_a_number"
            if not math.isfinite(coordinate) or not low <= coordinate <= high:
                return "out_of_range"
        return None


#: Vértices máximos de una línea o un polígono. Un recorrido caminando con
#: un vértice cada pocos metros cabe holgado; el tope acota lo que llega al
#: bucle de push y al JSONB de `answers`.
MAX_VERTICES = 2000


def _check_positions(positions: Any, *, minimum: int) -> str | None:
    """Lista de posiciones GeoJSON `[lon, lat]` en WGS84, con los rangos de ADR-005."""
    if not isinstance(positions, list):
        return "not_a_geometry"
    if len(positions) < minimum:
        return "too_few_vertices"
    if len(positions) > MAX_VERTICES:
        return "too_many_vertices"
    for position in positions:
        if not isinstance(position, list) or len(position) != 2:
            return "not_a_geometry"
        for coordinate, low, high in zip(
            position, (LON_MIN, LAT_MIN), (LON_MAX, LAT_MAX), strict=True
        ):
            if isinstance(coordinate, bool) or not isinstance(coordinate, int | float):
                return "not_a_number"
            if not math.isfinite(coordinate) or not low <= coordinate <= high:
                return "out_of_range"
    return None


class LineField(_FieldBase):
    """Fase C: un recorrido o lindero abierto (`geotrace` en XLSForm).

    La respuesta es una geometría GeoJSON `LineString` en WGS84:
    `{"type": "LineString", "coordinates": [[lon, lat], ...]}`, con 2 o más
    vértices. Se guarda en 4326 como el resto; otros sistemas solo se usan
    para mostrar o exportar.
    """

    type: Literal["line"]

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, dict):
            return "not_an_object"
        if set(value) != {"type", "coordinates"} or value["type"] != "LineString":
            return "not_a_geometry"
        return _check_positions(value["coordinates"], minimum=2)


class PolygonField(_FieldBase):
    """Fase C: un área (lote, predio; `geoshape` en XLSForm).

    GeoJSON `Polygon` en WGS84 con **un solo anillo exterior** cerrado (el
    último vértice repite el primero) y al menos 3 vértices distintos:
    `{"type": "Polygon", "coordinates": [[[lon, lat], ..., [lon, lat]]]}`.
    Sin huecos en esta versión.
    """

    type: Literal["polygon"]

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, dict):
            return "not_an_object"
        if set(value) != {"type", "coordinates"} or value["type"] != "Polygon":
            return "not_a_geometry"
        rings = value["coordinates"]
        if not isinstance(rings, list) or len(rings) != 1:
            return "not_a_geometry"
        ring = rings[0]
        problem = _check_positions(ring, minimum=4)
        if problem is not None:
            return problem
        if ring[0] != ring[-1]:
            return "not_closed"
        return None


class QrField(TextField):
    """Texto de un QR; no se ejecuta ni se abre como enlace."""

    type: Literal["qr"]  # type: ignore[assignment]


class RepeatField(_FieldBase):
    type: Literal["repeat"]
    fields: list[FormFieldUnion] = Field(min_length=1, max_length=MAX_FIELDS)
    min_rows: int = Field(default=0, ge=0, le=100)
    max_rows: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def _check_rows(self) -> RepeatField:
        if self.min_rows > self.max_rows:
            raise ValueError("min_rows cannot be greater than max_rows")
        return self

    def check_answer(self, value: Any) -> str | None:
        if not isinstance(value, list):
            return "not_a_list"
        if len(value) < self.min_rows:
            return "too_few_rows"
        if len(value) > self.max_rows:
            return "too_many_rows"
        if any(not isinstance(row, dict) for row in value):
            return "not_an_object"
        return None


#: El catálogo cerrado, como unión discriminada por `type`.
FormFieldUnion = Annotated[
    TextField
    | NumberField
    | IntegerField
    | BooleanField
    | DateField
    | DateTimeField
    | SingleChoiceField
    | MultiChoiceField
    | PhotoField
    | SignatureField
    | LocationField
    | LineField
    | PolygonField
    | QrField
    | RepeatField,
    Field(discriminator="type"),
]

RepeatField.model_rebuild()

#: Los tipos del catálogo, derivados de la propia unión (no reescritos a mano).
FIELD_TYPES: frozenset[str] = frozenset(
    model.model_fields["type"].annotation.__args__[0]
    for model in FormFieldUnion.__origin__.__args__  # type: ignore[attr-defined]
)

#: Tipos sobre los que tiene sentido comparar por igualdad/pertenencia.
_EQUALITY_TYPES: frozenset[str] = frozenset(
    {"text", "qr", "number", "integer", "boolean", "date", "datetime", "single_choice"}
)

#: Tipos sobre los que tiene sentido ordenar (`gt`/`gte`/`lt`/`lte`).
#: `date`/`datetime` entran porque su formato ISO 8601 ordena léxicamente.
_ORDER_TYPES: frozenset[str] = frozenset({"number", "integer", "date", "datetime"})


def _literal_fits(field: Any, literal: Any) -> bool:
    """¿Puede el campo referenciado tomar alguna vez ese literal?

    Un `{"op": "eq", "field": "estado", "value": "aprovado"}` con una errata en
    el valor de una opción es una regla que **siempre** es falsa: el campo
    condicionado no aparecería jamás y nadie se enteraría hasta que un agente
    de campo reportara que "falta una pregunta". Se caza al publicar.
    """
    if literal is None:
        return True
    if field.type == "boolean":
        return isinstance(literal, bool)
    if field.type in {"number", "integer"}:
        return not isinstance(literal, bool) and isinstance(literal, int | float)
    if field.type == "single_choice":
        return isinstance(literal, str) and literal in field.option_values
    return isinstance(literal, str)


class FormDefinition(_Sealed):
    """Una plantilla de formulario bien formada. **Este es el meta-schema.**"""

    dsl: str = DSL_VERSION
    title: I18nText | None = None
    fields: list[FormFieldUnion] = Field(max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def _check_template(self) -> FormDefinition:
        if self.dsl not in SUPPORTED_DSL_VERSIONS:
            raise ValueError(
                f"unsupported DSL dialect {self.dsl!r} "
                f"(supported: {sorted(SUPPORTED_DSL_VERSIONS)})"
            )

        count = 0
        expanded = 0
        stack = [(self.fields, 0, 1)]
        while stack:
            fields, depth, multiplier = stack.pop()
            if depth > 3:
                raise ValueError("repeat groups exceed 3 levels")
            declared: dict[str, Any] = {}
            for field in fields:
                count += 1
                expanded += multiplier
                if expanded > 10_000:
                    raise ValueError("repeat capacity exceeds 10000 answer fields")
                if count > MAX_FIELDS:
                    raise ValueError("too many fields including repeated groups")
                if field.key in declared:
                    raise ValueError(f"duplicate field key {field.key!r}")
                if field.visible_if is not None:
                    self._check_visibility(field, declared)
                declared[field.key] = field
                if isinstance(field, RepeatField):
                    stack.append((field.fields, depth + 1, multiplier * field.max_rows))
        return self

    @staticmethod
    def _check_visibility(field: Any, declared: dict[str, Any]) -> None:
        """Las tres reglas del DSL que dependen del resto de la plantilla."""
        expr = field.visible_if
        if expression_depth(expr) > MAX_EXPR_DEPTH:
            raise ValueError(
                f"visible_if of {field.key!r} nests deeper than {MAX_EXPR_DEPTH} levels"
            )
        # Solo hacia atrás ⇒ grafo acíclico por construcción (ver `visibility.py`).
        for referenced in sorted(referenced_fields(expr)):
            if referenced not in declared:
                raise ValueError(
                    f"visible_if of {field.key!r} references {referenced!r}, which is not "
                    "a field declared before it"
                )
        for node in _walk(expr):
            target = declared[node.field]
            if isinstance(node, Presence):
                continue
            if isinstance(node, Comparison) and node.op in {"gt", "gte", "lt", "lte"}:
                if target.type not in _ORDER_TYPES:
                    raise ValueError(
                        f"operator {node.op!r} is not applicable to a {target.type!r} field"
                    )
            elif target.type not in _EQUALITY_TYPES:
                raise ValueError(
                    f"operator {node.op!r} is not applicable to a {target.type!r} field"
                )
            literals = node.value if isinstance(node, Membership) else [node.value]
            for literal in literals:
                if not _literal_fits(target, literal):
                    raise ValueError(
                        f"{literal!r} is not a value that field {node.field!r} can take"
                    )


def _walk(expr: VisibilityExpr) -> list[Comparison | Membership | Presence]:
    """Las hojas de la expresión (las que nombran un campo), en orden estable."""
    if isinstance(expr, Not):
        return _walk(expr.operand)
    if isinstance(expr, AndOr):
        return [leaf for operand in expr.operands for leaf in _walk(operand)]
    return [expr]


#: El meta-schema como documento **JSON Schema**, derivado de los modelos.
META_SCHEMA: dict[str, Any] = FormDefinition.model_json_schema()


def has_declared_schema(definition: Any) -> bool:
    """`True` si esta definición declara campos y por tanto restringe `answers`.

    Una definición vacía (`{}`) es lo que el Paso 5 dejó por defecto en la
    columna y lo que tienen las filas creadas antes de este paso: significa
    "esta versión no declara esquema", y sus `answers` no se restringen. La
    alternativa —tratar `{}` como plantilla inválida— convertiría en
    irrecuperables lotes de visitas ya sincronizadas contra versiones
    antiguas, que es exactamente el tipo de migración forzosa de datos
    históricos que ADR-012 existe para evitar. Al publicar por
    `services.publish_version()` la definición **siempre** cumple el
    meta-schema, así que esto solo cubre lo preexistente.
    """
    return isinstance(definition, dict) and "fields" in definition


def validate_definition(raw: Any) -> FormDefinition:
    """Valida una definición contra el meta-schema. `TemplateDefinitionError` si no."""
    if not isinstance(raw, dict):
        raise TemplateDefinitionError(
            "Template definition must be a JSON object.",
            {"field": "definition", "reason": "not_an_object"},
        )
    check_json_depth(raw)
    try:
        return FormDefinition.model_validate(raw)
    except ValidationError as exc:
        errors = exc.errors()
        # El `msg` de Pydantic (no el `input`, RD-12) sí se incluye: quien lee
        # este error es quien **escribe** la plantilla en el back-office, y
        # "algo falló en fields.3" sin decir qué es un mensaje inútil. El valor
        # que falló sigue sin viajar: eso es lo que `redacted_validation_details`
        # deja fuera.
        reason = str(errors[0].get("msg", "")) if errors else ""
        raise TemplateDefinitionError(
            f"Template definition failed meta-schema validation: {reason}",
            redacted_validation_details(errors),
        ) from exc

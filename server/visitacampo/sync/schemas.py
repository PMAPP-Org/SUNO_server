"""Formas del contrato de `/v1/sync/*` (`server/openapi/v1.yaml`), Pasos 8-9.

El contrato del Paso 1 es la fuente de verdad. Aquí no se inventa ningún campo
ni se renombra ninguno. Dos decisiones merecen explicación porque **se apartan
de la traducción literal del YAML**, y en ambos casos el propio contrato es
quien lo pide:

1. **`ops` no lleva `max_length=500`.** El esquema declara `maxItems: 500`,
   pero la descripción del endpoint dice que el servidor "responde `413` con
   `code: payload_too_large`" y que "el cliente parte el lote". Si el límite
   viviera en Pydantic, Ninja respondería `422` antes de llegar a la vista y el
   cliente no recibiría nunca el código que el contrato le promete. El límite
   se aplica en `api.py` (leyendo `serverinfo.capabilities`), no aquí. El
   `minItems: 1` sí se queda: un lote vacío no tiene resultado por operación
   que devolver, y `422` es la respuesta correcta.

2. **`RawSyncOp` es deliberadamente permisivo salvo en `op_uuid` y `entity`.**
   La razón es el propio `OpResult`, que declara `op_uuid`, `entity` y `status`
   como `required`: sin esos dos campos **no existe una forma contractual de
   decir "esta operación fue rechazada"**, así que un lote que los traiga rotos
   solo puede contestarse con un `422` global. Todo lo demás —`op_type`,
   `base_version` y `payload`— se valida **por operación** en `payloads.py` y
   produce `rejected`, no un `422` que tumbaría el lote entero por una sola
   operación mala (doc 08 §4.3: "rejected nunca se descarta en silencio").
   `entity` sí es un `Literal` cerrado por la misma razón: `OpResult.entity`
   es el enum `EntityType` del contrato y emitir ahí una cadena arbitraria
   sería devolver una respuesta que no valida contra el propio contrato.

`server_row` (push, `OpResultSchema`) se tipa como `VisitRecord` y no como la
unión `EntityRecord` porque `Visit` es la única entidad escribible del MVP
(brief del Paso 8, tarea 3). Es un estrechamiento compatible: todo
`VisitRecord` es un `EntityRecord` válido. La propia unión `EntityRecord` sí
existe desde el Paso 9, para `PullResponse.rows` — el pull, a diferencia del
push, **lee** las cuatro entidades sincronizables, no solo la que se puede
escribir.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from ninja import Schema
from pydantic import Field

from visitacampo.authn.schemas import Timestamp
from visitacampo.authn.schemas import UserProfile as AuthUserProfile

__all__ = [
    "AssignmentData",
    "AssignmentRecord",
    "BootstrapResponse",
    "EntityRecord",
    "EntityType",
    "FormTemplateVersionData",
    "FormTemplateVersionRecord",
    "OpErrorSchema",
    "OpResultSchema",
    "OpStatus",
    "OrganizationSchema",
    "PullResponse",
    "PushRequest",
    "PushResponse",
    "RawSyncOp",
    "TombstoneSchema",
    "VisitAttachmentData",
    "VisitAttachmentRecord",
    "VisitData",
    "VisitRecord",
]

#: Contrato `EntityType`. Cerrado a propósito: ver el punto 2 del docstring.
EntityType = Literal["visit", "visit_attachment", "assignment", "form_template_version"]

#: Contrato `OpStatus`.
OpStatus = Literal["applied", "conflict", "rejected"]


class RawSyncOp(Schema):
    """Contrato `SyncOp`, con el sobre validado y el payload aún sin abrir.

    `op_type`, `base_version` y `payload` se declaran `Any` **a propósito**:
    son exactamente los campos cuya invalidez el contrato quiere ver como
    `rejected` por operación, y tiparlos aquí los convertiría en un `422` del
    lote entero. La validación real —estricta y con tipos sellados (RD-03)—
    la hace `payloads.parse_op`, que es la única puerta por la que este dato
    llega a tocar la base de datos.
    """

    op_uuid: uuid.UUID
    entity: EntityType
    op_type: Any = None
    base_version: Any = None
    payload: Any = None


class PushRequest(Schema):
    """Contrato `PushRequest`.

    `batch_uuid` es telemetría y auditoría (`sync_op_log`), **no** clave de
    idempotencia: esa es `op_uuid`, operación a operación (ADR-003). Reenviar
    el mismo `batch_uuid` con otro conjunto de operaciones es legítimo.
    """

    schema_version: int
    device_id: uuid.UUID
    batch_uuid: uuid.UUID
    ops: Annotated[list[RawSyncOp], Field(min_length=1)]


class VisitData(Schema):
    """Contrato `Visit`: la fila autoritativa completa.

    **Todas las propiedades son `required`** (convención del contrato para
    entidades emitidas por el servidor): lo opcional lo es en el *valor*
    (`null`), nunca en la presencia del campo. Por eso ninguna lleva `default`.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    version: int
    change_seq: int
    deleted_at: Timestamp | None
    assignment_id: uuid.UUID | None
    template_version_id: uuid.UUID
    answers: dict[str, Any]
    lat: float | None
    lon: float | None
    accuracy_m: float | None
    altitude_m: float | None
    captured_at: Timestamp
    device_id: uuid.UUID
    created_by: uuid.UUID


class VisitRecord(Schema):
    """Contrato `VisitRecord` — la rama `visit` de `EntityRecord`."""

    entity: Literal["visit"] = "visit"
    data: VisitData


class OpErrorSchema(Schema):
    """Contrato `Error`, embebido en `OpResult.error`.

    `details` va **ya redactado** (RD-12): `field` y `reason`, nunca el valor
    que provocó el fallo. Lo garantiza `payloads.py`, que es quien lo produce.
    """

    code: str
    message: str
    details: dict[str, Any] | None = None


class OpResultSchema(Schema):
    """Contrato `OpResult`.

    `server_row` es **obligatorio en `conflict`** ("evita re-descargar todo") y
    opcional en `applied`. Este servidor no lo emite en `applied`: el cliente
    acaba de escribir esa fila y ya recibe `server_version`; devolverle su
    propio contenido multiplicaría por N el tamaño de la respuesta de un lote
    de 500 altas en una red móvil, que es justo lo que ADR-003 intenta evitar.
    """

    op_uuid: uuid.UUID
    entity: EntityType
    entity_id: uuid.UUID | None = None
    status: OpStatus
    server_version: int | None = None
    server_row: VisitRecord | None = None
    error: OpErrorSchema | None = None


class PushResponse(Schema):
    """Contrato `PushResponse`. Un resultado por operación, **en el mismo orden**."""

    schema_version: int
    server_time: Timestamp
    results: list[OpResultSchema]


# --------------------------------------------------------- pull / bootstrap ----
# Paso 9. `VisitData` se reutiliza tal cual para `BootstrapResponse.visits`: el
# contrato declara ahí el mismo `Visit` que en `VisitRecord.data`, sin envoltorio
# `{entity, data}` — el bootstrap ya sabe de qué tipo es cada array por su clave.


class VisitAttachmentData(Schema):
    """Contrato `VisitAttachment`. Ver la nota de `VisitData` sobre `required`."""

    id: uuid.UUID
    org_id: uuid.UUID
    version: int
    change_seq: int
    deleted_at: Timestamp | None
    visit_id: uuid.UUID
    sha256: str
    role: Literal["foto", "firma", "doc"]
    caption: str | None
    captured_at: Timestamp
    order: int


class AssignmentData(Schema):
    """Contrato `Assignment`."""

    id: uuid.UUID
    org_id: uuid.UUID
    version: int
    change_seq: int
    deleted_at: Timestamp | None
    template_version_id: uuid.UUID
    assigned_to: uuid.UUID
    title: str | None
    scheduled_for: Timestamp | None
    lat: float | None
    lon: float | None


class FormTemplateVersionData(Schema):
    """Contrato `FormTemplateVersion`. `definition` es JSON declarativo (ADR-012)."""

    id: uuid.UUID
    org_id: uuid.UUID
    version: int
    change_seq: int
    deleted_at: Timestamp | None
    template_id: uuid.UUID
    name: str | None
    version_number: int
    definition: dict[str, Any]
    published_at: Timestamp | None


class VisitAttachmentRecord(Schema):
    """Contrato `VisitAttachmentRecord` — la rama `visit_attachment` de `EntityRecord`."""

    entity: Literal["visit_attachment"] = "visit_attachment"
    data: VisitAttachmentData


class AssignmentRecord(Schema):
    """Contrato `AssignmentRecord` — la rama `assignment` de `EntityRecord`."""

    entity: Literal["assignment"] = "assignment"
    data: AssignmentData


class FormTemplateVersionRecord(Schema):
    """Contrato `FormTemplateVersionRecord` — la rama `form_template_version` de `EntityRecord`."""

    entity: Literal["form_template_version"] = "form_template_version"
    data: FormTemplateVersionData


#: Contrato `EntityRecord`: unión discriminada por `entity`. `Field(discriminator=...)`
#: hace que Pydantic elija la rama exacta por el valor de `entity` en vez de
#: probar cada `Schema` en orden hasta que uno valide — más rápido y, sobre
#: todo, sin el riesgo de que una forma más permissiva (p. ej. una con más
#: campos opcionales) "robe" una fila que en realidad era de otra rama.
EntityRecord = Annotated[
    VisitRecord | VisitAttachmentRecord | AssignmentRecord | FormTemplateVersionRecord,
    Field(discriminator="entity"),
]


class TombstoneSchema(Schema):
    """Contrato `Tombstone`. Sin `data`: le dice al cliente que borre, no qué poner."""

    entity: EntityType
    id: uuid.UUID
    change_seq: int
    deleted_at: Timestamp


class OrganizationSchema(Schema):
    """Contrato `Organization`."""

    id: uuid.UUID
    name: str


class BootstrapResponse(Schema):
    """Contrato `BootstrapResponse`: estado inicial completo, sin paginación en v1.

    `has_more` se fija siempre en `False`: es el campo **reservado** que el
    contrato documenta para poder paginar el bootstrap en el futuro sin romper
    a los clientes ya desplegados (`v1.yaml`, descripción de `BootstrapResponse`).
    """

    schema_version: int
    server_time: Timestamp
    cursor: str
    organization: OrganizationSchema
    user: AuthUserProfile
    form_template_versions: list[FormTemplateVersionData]
    assignments: list[AssignmentData]
    visits: list[VisitData]
    visit_attachments: list[VisitAttachmentData]
    has_more: bool = False


class PullResponse(Schema):
    """Contrato `PullResponse`: página de cambios en orden estricto de `change_seq`."""

    schema_version: int
    server_time: Timestamp
    rows: list[EntityRecord]
    tombstones: list[TombstoneSchema]
    next_cursor: str
    has_more: bool

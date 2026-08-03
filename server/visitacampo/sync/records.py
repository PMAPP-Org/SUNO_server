"""Una fila de la BD → la forma que el contrato llama `EntityRecord`.

Vive en su propio archivo porque tiene **dos** consumidores con requisitos
distintos y la traducción tiene que ser la misma para ambos:

1. El `server_row` de un `conflict` en el push (Paso 8, solo `visit`: es la
   única entidad que el push puede escribir en el MVP).
2. Las `rows` del pull (Paso 9), que emite las cuatro entidades sincronizables
   —`visit`, `visit_attachment`, `assignment`, `form_template_version`— con el
   mismo formato exacto que usaría un conflicto de push si esa entidad fuera
   escribible.

Todo se emite **ya en tipos JSON** (cadenas para UUID e instantes) y no en
objetos de Python. El motivo no es estético: el diccionario de `visit_record`
se guarda tal cual en `sync_op_log.result` (JSONB) y se devuelve byte a byte
idéntico cuando el cliente reenvía la operación. Si aquí salieran objetos y la
serialización ocurriera más tarde, el resultado guardado y el resultado
devuelto pasarían por caminos distintos y podrían divergir — que es justo lo
que la idempotencia promete que no pasa. Las otras tres entidades no pasan por
`sync_op_log`, pero se mantienen en la misma forma por consistencia: un solo
patrón de traducción fila→JSON en todo el módulo de sync.

**Ninguno de los cuatro navega una relación** (`visit.assignment`,
`attachment.visit`, `assignment.template_version`…): todos leen los `*_id`
que ya vienen en la propia fila. Seguir una relación aquí dispararía una
query perezosa por fila del pull —potencialmente cientos, la misma forma de
H-07 que RD-07 elimina en el push— reintroducida por la puerta de atrás.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from visitacampo.authn.schemas import rfc3339_utc
from visitacampo.core.models import Assignment, FormTemplateVersion, Visit, VisitAttachment

__all__ = [
    "RECORD_BUILDERS",
    "assignment_record",
    "form_template_version_record",
    "iso",
    "tombstone",
    "uuid_str",
    "visit_attachment_record",
    "visit_record",
]


def iso(value: datetime | None) -> str | None:
    """Instante en RFC 3339 UTC con `Z`, o `None` (contrato `Timestamp`)."""
    return None if value is None else rfc3339_utc(value)


def uuid_str(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def visit_record(visit: Visit) -> dict[str, Any]:
    """Contrato `VisitRecord`: `{entity: "visit", data: {...}}`.

    Se emiten **todas** las propiedades, incluidas las que valen `null`: la
    convención del contrato para entidades del servidor es que lo opcional lo
    sea en el valor y nunca en la presencia del campo, para que el test de
    deriva contrato↔backend detecte un campo omitido en vez de tragárselo.
    """
    return {
        "entity": "visit",
        "data": {
            "id": str(visit.pk),
            "org_id": str(visit.org_id),
            "version": visit.version,
            "change_seq": visit.change_seq,
            "deleted_at": iso(visit.deleted_at),
            "assignment_id": uuid_str(visit.assignment_id),
            "template_version_id": str(visit.template_version_id),
            "answers": visit.answers,
            "lat": visit.lat,
            "lon": visit.lon,
            "accuracy_m": visit.accuracy_m,
            "altitude_m": visit.altitude_m,
            "captured_at": iso(visit.captured_at),
            "device_id": str(visit.device_id),
            "created_by": str(visit.created_by_id),
        },
    }


def visit_attachment_record(attachment: VisitAttachment) -> dict[str, Any]:
    """Contrato `VisitAttachmentRecord`.

    `data.sha256` se lee de `attachment.blob_id` (el atributo `*_id` que
    Django genera para el campo `blob`, cuya columna real es `sha256` —
    `db_column="sha256"`, ver `core/models/visit.py`): es el valor de la FK
    sin `db_constraint`, así que sigue siendo correcto aunque el `Blob`
    referenciado todavía no exista (adjunto pendiente de subir, Paso 17/18).
    """
    return {
        "entity": "visit_attachment",
        "data": {
            "id": str(attachment.pk),
            "org_id": str(attachment.org_id),
            "version": attachment.version,
            "change_seq": attachment.change_seq,
            "deleted_at": iso(attachment.deleted_at),
            "visit_id": str(attachment.visit_id),
            "sha256": attachment.blob_id,
            "role": attachment.role,
            "caption": attachment.caption,
            "captured_at": iso(attachment.captured_at),
            "order": attachment.order,
        },
    }


def assignment_record(assignment: Assignment) -> dict[str, Any]:
    """Contrato `AssignmentRecord`."""
    return {
        "entity": "assignment",
        "data": {
            "id": str(assignment.pk),
            "org_id": str(assignment.org_id),
            "version": assignment.version,
            "change_seq": assignment.change_seq,
            "deleted_at": iso(assignment.deleted_at),
            "template_version_id": str(assignment.template_version_id),
            "assigned_to": str(assignment.assigned_to_id),
            "title": assignment.title,
            "scheduled_for": iso(assignment.scheduled_for),
            "lat": assignment.lat,
            "lon": assignment.lon,
        },
    }


def form_template_version_record(version: FormTemplateVersion) -> dict[str, Any]:
    """Contrato `FormTemplateVersionRecord`."""
    return {
        "entity": "form_template_version",
        "data": {
            "id": str(version.pk),
            "org_id": str(version.org_id),
            "version": version.version,
            "change_seq": version.change_seq,
            "deleted_at": iso(version.deleted_at),
            "template_id": str(version.template_id),
            "name": version.name,
            "version_number": version.version_number,
            "definition": version.definition,
            "published_at": iso(version.published_at),
        },
    }


def tombstone(
    *, entity: str, entity_id: uuid.UUID, change_seq: int, deleted_at: datetime
) -> dict[str, Any]:
    """Contrato `Tombstone`: el borrado lógico propagado por el pull.

    A diferencia de los `*_record`, no lleva `data` — el contrato solo exige
    `entity`, `id`, `change_seq`, `deleted_at`. No hace falta la fila
    completa: un tombstone le dice al cliente "borra esto de tu copia local",
    no "reemplaza esto por lo siguiente".
    """
    return {
        "entity": entity,
        "id": str(entity_id),
        "change_seq": change_seq,
        "deleted_at": iso(deleted_at),
    }


#: Qué constructor de `*_record` usar para cada entidad sincronizable, en el
#: mismo orden y con las mismas claves que `changeseq.SYNCED_MODELS`/contrato
#: `EntityType`. Vive aquí (no en `changeseq.py`) porque es una tabla de
#: *serialización*, no de *asignación de change_seq*: son preocupaciones
#: distintas que `sync/pull.py` (Paso 9) combina, cada una desde su módulo.
RECORD_BUILDERS: dict[str, Any] = {
    "visit": visit_record,
    "visit_attachment": visit_attachment_record,
    "assignment": assignment_record,
    "form_template_version": form_template_version_record,
}

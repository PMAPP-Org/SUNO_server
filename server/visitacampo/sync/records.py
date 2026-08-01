"""Una fila de la BD → la forma que el contrato llama `EntityRecord`.

Vive en su propio archivo porque tiene **dos** consumidores con requisitos
distintos y la traducción tiene que ser la misma para ambos:

1. El `server_row` de un `conflict` en el push (este paso). Ahí no es un
   adorno: el contrato lo declara obligatorio en `conflict` precisamente para
   que el cliente reemplace su fila local sin re-descargar el proyecto entero.
2. Las `rows` del pull (Paso 9), que emiten exactamente el mismo `Visit`.

Todo se emite **ya en tipos JSON** (cadenas para UUID e instantes) y no en
objetos de Python. El motivo no es estético: este mismo diccionario se guarda
tal cual en `sync_op_log.result` (JSONB) y se devuelve byte a byte idéntico
cuando el cliente reenvía la operación. Si aquí salieran objetos y la
serialización ocurriera más tarde, el resultado guardado y el resultado
devuelto pasarían por caminos distintos y podrían divergir — que es justo lo
que la idempotencia promete que no pasa.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from visitacampo.authn.schemas import rfc3339_utc
from visitacampo.core.models import Visit

__all__ = ["iso", "uuid_str", "visit_record"]


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

    No se navega ninguna relación (`visit.assignment`, `visit.device`…): se
    leen los `*_id` que ya vienen en la propia fila. Seguir una relación aquí
    dispararía una query perezosa por cada conflicto del lote — la forma de
    H-07 que RD-07 elimina, reintroducida por la puerta de atrás.
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

"""Las sentencias CAS del push: **una sentencia por operación, sin read-then-write**.

Mismo patrón que la rotación de refresh del Paso 7
(`authn/services.py::refresh_session`: `UPDATE ... WHERE rotated_at IS NULL`),
llevado al `version` del protocolo de sync: la guarda viaja **dentro** de la
sentencia, así que no existe ventana entre "leer la versión" y "escribir sobre
ella" en la que dos escritores puedan ganar los dos (ADR-003).

- `create` → `INSERT ... ON CONFLICT (id) DO NOTHING RETURNING`.
  Cero filas significa que el `id` ya existía, que para un alta es
  exactamente un fallo de CAS (`base_version: null` contra una fila que ya
  tiene versión) ⇒ `conflict`.
- `update`/`delete` → `UPDATE ... WHERE id = %s AND org_id = %s AND version = %s
  RETURNING`. Cero filas ⇒ `conflict`.
- `delete` es un `update` que escribe `deleted_at`: los borrados son lógicos
  (tombstones, doc 08 §4.4) y tienen que viajar por el pull como cualquier
  otro cambio, con su `version` y su `change_seq` nuevos.

**Por qué SQL a mano y no el ORM.** Django no sabe expresar
`ON CONFLICT ... DO UPDATE ... WHERE` (`bulk_create(update_conflicts=True)` no
admite condición), y `QuerySet.update()` no devuelve `RETURNING`, así que con
el ORM harían falta dos viajes —leer y luego escribir— que es justo lo que el
patrón CAS existe para evitar. Lo que **no** se pierde por bajar a SQL:

- `org_id = %s` aparece en la guarda de todas las sentencias, y en el `INSERT`
  se escribe siempre con el `org_id` del contexto. El aislamiento entre
  organizaciones (RD-11) no depende de que el llamador se acuerde: es parte de
  la sentencia. El test `test_sql.py` lo verifica sobre el texto generado.
- Los nombres de columna **no se escriben a mano**: salen de `Visit._meta`, así
  que renombrar un campo del modelo rompe estos tests en vez de generar SQL
  contra una columna que ya no existe.

Ningún valor se interpola en el texto: todo va por parámetros (`%s`). Lo único
que se concatena son identificadores obtenidos del modelo y citados con
`connection.ops.quote_name`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import connection

from visitacampo.core.models import INITIAL_VERSION, Visit, VisitAttachment

__all__ = ["Statement", "build_attachment_insert", "build_insert", "build_update"]


#: Nombre del contrato → nombre del campo en el modelo. Solo difieren los que
#: el ORM sufija con `_id` (o desufija, en `created_by`); el resto es identidad.
_MODEL_FIELD_BY_CONTRACT_NAME: dict[str, str] = {
    "id": "id",
    "org_id": "org",
    "version": "version",
    "change_seq": "change_seq",
    "deleted_at": "deleted_at",
    "assignment_id": "assignment",
    "template_version_id": "template_version",
    "answers": "answers",
    "lat": "lat",
    "lon": "lon",
    "accuracy_m": "accuracy_m",
    "altitude_m": "altitude_m",
    "captured_at": "captured_at",
    "device_id": "device",
    "created_by": "created_by",
}

#: Campos que el servidor escribe en un alta, en orden estable.
_INSERT_FIELDS: tuple[str, ...] = (
    "id",
    "org_id",
    "version",
    "change_seq",
    "deleted_at",
    "assignment_id",
    "template_version_id",
    "answers",
    "lat",
    "lon",
    "accuracy_m",
    "altitude_m",
    "captured_at",
    "device_id",
    "created_by",
)


# Las supresiones B608 de este módulo marcan falsos positivos revisados de
# Bandit: el texto SQL solo interpola lo que devuelven `column()`/`table()`
# —un nombre de contrato fuera de `_MODEL_FIELD_BY_CONTRACT_NAME` lanza
# `KeyError` antes de llegar al SQL— y placeholders `%s`; los valores, nunca.
def column(contract_name: str) -> str:
    """Columna citada de un campo del contrato, resuelta contra `Visit._meta`."""
    field = Visit._meta.get_field(_MODEL_FIELD_BY_CONTRACT_NAME[contract_name])
    return connection.ops.quote_name(field.column)


def table() -> str:
    return connection.ops.quote_name(Visit._meta.db_table)


@dataclass(frozen=True, slots=True)
class Statement:
    """SQL y parámetros, separados. Nunca se concatena un valor en el texto."""

    sql: str
    params: tuple[Any, ...]


def _placeholder_and_param(contract_name: str, value: Any) -> tuple[str, Any]:
    """`%s` y su valor, con el único caso especial que hay: `answers`.

    `answers` es `JSONB` y se envía ya serializado con un cast explícito. Django
    usa el cursor de *client-side binding* de psycopg 3, que adapta un `dict` de
    Python a `hstore`/texto según el contexto y no necesariamente a `jsonb`;
    `json.dumps(...)::jsonb` no deja esa elección al adaptador. Es además lo
    mismo que hace el propio `JSONField` del ORM al preparar el valor.
    """
    if contract_name == "answers":
        return "%s::jsonb", json.dumps(value)
    return "%s", value


def build_insert(
    *,
    org_id: uuid.UUID,
    visit_id: uuid.UUID,
    change_seq: int,
    created_by: uuid.UUID,
    fields: dict[str, Any],
) -> Statement:
    """Alta con guarda CAS implícita: `ON CONFLICT (id) DO NOTHING`.

    `version` arranca en `INITIAL_VERSION` (Paso 5) y `deleted_at` en `NULL`:
    una fila nace viva. `org_id` y `created_by` los pone el servidor —son
    server-authoritative y el contrato ni siquiera los admite en `VisitPayload`—,
    de modo que un cliente no puede crear una visita en otra organización ni
    atribuírsela a otro usuario.
    """
    values: dict[str, Any] = {
        "id": visit_id,
        "org_id": org_id,
        "version": INITIAL_VERSION,
        "change_seq": change_seq,
        "deleted_at": None,
        "created_by": created_by,
        **fields,
    }
    columns: list[str] = []
    placeholders: list[str] = []
    params: list[Any] = []
    for name in _INSERT_FIELDS:
        placeholder, param = _placeholder_and_param(name, values[name])
        columns.append(column(name))
        placeholders.append(placeholder)
        params.append(param)

    sql = (
        f"INSERT INTO {table()} ({', '.join(columns)}) "  # nosec B608
        f"VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT ({column('id')}) DO NOTHING "
        f"RETURNING {column('version')}, {column('change_seq')}"
    )
    return Statement(sql=sql, params=tuple(params))


def build_update(
    *,
    org_id: uuid.UUID,
    visit_id: uuid.UUID,
    base_version: int,
    change_seq: int,
    fields: dict[str, Any],
    deleted_at: datetime | None = None,
) -> Statement:
    """Modificación o borrado lógico con guarda CAS explícita.

    `version = version + 1` se calcula **en la base de datos** y no en Python:
    el valor nuevo no depende de nada que el proceso haya leído antes, así que
    tampoco puede quedarse obsoleto entre la lectura y la escritura.

    `deleted_at` se pasa aparte de `fields` porque no es un dato del cliente:
    el contrato lo declara server-authoritative y `payloads.py` lo rechazaría
    si viniera en el payload. Un `delete` es exactamente este `update` con
    `deleted_at` informado y `fields` vacío.
    """
    assignments: list[str] = []
    params: list[Any] = []
    for name, value in fields.items():
        placeholder, param = _placeholder_and_param(name, value)
        assignments.append(f"{column(name)} = {placeholder}")
        params.append(param)
    if deleted_at is not None:
        assignments.append(f"{column('deleted_at')} = %s")
        params.append(deleted_at)

    version_col = column("version")
    assignments.append(f"{version_col} = {version_col} + 1")
    assignments.append(f"{column('change_seq')} = %s")
    params.append(change_seq)

    sql = (
        f"UPDATE {table()} SET {', '.join(assignments)} "  # nosec B608
        f"WHERE {column('id')} = %s AND {column('org_id')} = %s AND {version_col} = %s "
        f"RETURNING {version_col}, {column('change_seq')}"
    )
    params.extend([visit_id, org_id, base_version])
    return Statement(sql=sql, params=tuple(params))


#: `visit_attachment` (Paso 22): nombre del contrato → campo del modelo.
_ATTACHMENT_FIELD_BY_CONTRACT_NAME: dict[str, str] = {
    "id": "id",
    "org_id": "org",
    "version": "version",
    "change_seq": "change_seq",
    "deleted_at": "deleted_at",
    "visit_id": "visit",
    "sha256": "blob",
    "role": "role",
    "caption": "caption",
    "captured_at": "captured_at",
    "order": "order",
}


def _attachment_column(contract_name: str) -> str:
    field = VisitAttachment._meta.get_field(_ATTACHMENT_FIELD_BY_CONTRACT_NAME[contract_name])
    return connection.ops.quote_name(field.column)


def build_attachment_insert(
    *,
    org_id: uuid.UUID,
    attachment_id: uuid.UUID,
    change_seq: int,
    fields: dict[str, Any],
) -> Statement:
    """Alta de `visit_attachment` con la misma guarda CAS que `build_insert`.

    `org_id` lo pone el servidor. La FK compuesta `(visit_id, org_id) → visit`
    (migración 0002) hace imposible apuntar a una visita de otra organización
    aunque la comprobación de `services.py` fallara; esa comprobación existe
    para contestar `rejected` en vez de dejar que el `INSERT` reviente el lote.
    """
    values: dict[str, Any] = {
        "id": attachment_id,
        "org_id": org_id,
        "version": INITIAL_VERSION,
        "change_seq": change_seq,
        "deleted_at": None,
        **fields,
    }
    names = tuple(_ATTACHMENT_FIELD_BY_CONTRACT_NAME)
    columns = ", ".join(_attachment_column(name) for name in names)
    placeholders = ", ".join("%s" for _ in names)
    table_name = connection.ops.quote_name(VisitAttachment._meta.db_table)
    sql = (
        f"INSERT INTO {table_name} ({columns}) "  # nosec B608
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({_attachment_column('id')}) DO NOTHING "
        f"RETURNING {_attachment_column('version')}, {_attachment_column('change_seq')}"
    )
    return Statement(sql=sql, params=tuple(values[name] for name in names))

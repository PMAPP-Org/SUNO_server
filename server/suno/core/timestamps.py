"""Instantes que llegan del cliente: con zona obligatoria y guardados en UTC.

El contrato exige RFC 3339 con desfase explícito (`Timestamp`). RFC 3339 admite
desfases de hasta ±23:59, pero `timestamptz` de PostgreSQL solo hasta ±15:59:
un `captured_at` con `-18:47` (válido, y que Schemathesis generó) llegaba hasta
la base de datos y devolvía un `500`, y en el push eso deja la outbox del
teléfono reintentando para siempre. Convertir a UTC al validar resuelve las dos
cosas: el instante es el mismo y siempre cabe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime

__all__ = ["UtcDatetime"]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


#: Fecha y hora con zona obligatoria (sin zona ⇒ `422`), normalizada a UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

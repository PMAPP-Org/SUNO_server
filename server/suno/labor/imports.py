"""Piezas de las tres importaciones de mano de obra (trabajadores, tareo y nómina), como el
catálogo de materiales (ADR-0036): la vista previa no guarda nada; las filas viajan firmadas una
hora con un nonce de un solo uso (`LaborImportToken`, con su `kind`); al aplicar se valida otra
vez. Todo o nada."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.core import signing
from django.db import IntegrityError, transaction

from suno.budgets.mapping import normalize_header
from suno.budgets.totals import parse_number
from suno.core.context import RequestContext
from suno.core.models import LaborImportToken
from suno.core.scoping import scoped_qs

from .common import LaborRejected

TOKEN_MAX_AGE = 3600
INVALID = "La vista previa no es válida: vuelve a subir el archivo."
ALREADY_APPLIED = "Esta vista previa ya se cargó: revisa los datos antes de importar otra vez."
MORE_ERRORS = "Hay más errores: corrige estos y vuelve a subir el archivo."


def cell_text(value: Any) -> str:
    """Texto de una celda. Un entero de Excel ("12") sale sin decimales; se quita el apóstrofo
    que `safe_cell` antepone a lo que empieza con = + - @."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def cell_day(value: Any) -> date | None:
    """Una fecha de Excel, "AAAA-MM-DD" o "DD/MM/AAAA"; `None` si está vacía o no es fecha."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = cell_text(value)
    try:
        if "/" in text:
            day, month, year = (int(part) for part in text.split("/"))
            return date(year, month, day)
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def cell_decimal(value: Any) -> Decimal | None:
    """Un número de celda o texto ("95.000", "1,5"); `None` si está vacío; `ValueError` si no es
    un número."""
    if isinstance(value, bool):
        raise ValueError("no es un número")
    return parse_number(value)


def columns(
    header: Sequence[Any], names: Sequence[str], required: Sequence[str]
) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in names}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in required if name not in found]


def row_texts(values: Sequence[Any], found: Mapping[str, int], names: Sequence[str]) -> list[Any]:
    """Los valores crudos de una fila en el orden de `names` (vacío si falta la columna)."""
    return [
        values[found[name]] if name in found and found[name] < len(values) else None
        for name in names
    ]


def sign(ctx: RequestContext, salt: str, body: Mapping[str, Any]) -> str:
    payload = {
        "org": str(ctx.org_id),
        "user": str(ctx.user_id),
        "nonce": secrets.token_urlsafe(24),
        **body,
    }
    return signing.dumps(payload, salt=salt, compress=True)


def load(ctx: RequestContext, salt: str, token: str) -> Mapping[str, Any]:
    try:
        payload = signing.loads(token, salt=salt, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise LaborRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise LaborRejected([INVALID]) from exc
    if payload.get("org") != str(ctx.org_id) or payload.get("user") != str(ctx.user_id):
        raise LaborRejected(["La vista previa no es tuya."])
    if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
        raise LaborRejected([INVALID])
    return payload


def consume(ctx: RequestContext, kind: str, nonce: str) -> None:
    """Marca la vista previa como cargada (dentro de la transacción de quien aplica)."""
    try:
        with transaction.atomic():
            scoped_qs(ctx, LaborImportToken).create(
                org_id=ctx.org_id, nonce=nonce, kind=kind, applied_by_id=ctx.user_id
            )
    except IntegrityError as exc:
        raise LaborRejected([ALREADY_APPLIED]) from exc

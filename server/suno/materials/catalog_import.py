"""Importar el catálogo de materiales desde Excel (costos, pieza 6c, spec 3.1).

- Hoja "Materiales" con el encabezado en la fila 1: Código, Nombre, Unidad y Categoría (se
  encuentran por su texto, sin importar mayúsculas ni tildes).
- Crea los códigos nuevos y actualiza nombre, unidad y categoría de los que ya existen (sin
  distinguir mayúsculas); las filas sin cambios se muestran y no se tocan.
- Como el libro (6a): la vista previa no guarda nada; las filas viajan firmadas una hora, con un
  nonce que se carga una sola vez (`MaterialImportToken`); al aplicar se valida otra vez. Todo
  o nada.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.mapping import normalize_header
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Material, MaterialImportToken
from suno.core.policies import can_manage_material_catalog
from suno.core.scoping import scoped_qs

from .catalog_services import (
    CODE_CONSTRAINT,
    UNIT_LOCKED,
    MaterialData,
    material_errors,
    with_movements,
)
from .common import MaterialRejected

SHEET = "Materiales"
H_CODE, H_NAME, H_UNIT, H_CATEGORY = "Código", "Nombre", "Unidad", "Categoría"
HEADER = [H_CODE, H_NAME, H_UNIT, H_CATEGORY]
REQUIRED = (H_CODE, H_NAME, H_UNIT)
MAX_IMPORT_ROWS = 5000
MAX_ERRORS = 50
#: Sal de la firma de la vista previa (no es un secreto: la clave es `SECRET_KEY`).
PREVIEW_SALT = "suno.materials.import"
TOKEN_MAX_AGE = 3600
NEW, UPDATE, SAME = "nuevo", "actualiza", "sin cambios"
ALREADY_APPLIED = "Esta vista previa ya se cargó: revisa el catálogo antes de importar otra vez."
RACE = "Otra persona cambió el catálogo mientras tanto: vuelve a subir el archivo."
INVALID = "La vista previa no es válida: vuelve a subir el archivo."


@dataclass(frozen=True)
class ImportRow:
    #: Fila del Excel (el encabezado es la 1).
    line: int
    data: MaterialData
    #: `NEW`, `UPDATE` o `SAME`.
    action: str


@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[ImportRow, ...] = ()
    errors: tuple[str, ...] = ()
    #: Vacío si hay errores: no se puede aplicar.
    token: str = ""

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _text(value: Any) -> str:
    """Texto de una celda. Un número entero de Excel ("12") sale sin decimales."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip()
    # Deshace el apóstrofo que `safe_cell` antepone a lo que empieza con = + - @.
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def _columns(header: Sequence[Any]) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in HEADER}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in REQUIRED if name not in found]


def _classify(data: MaterialData, current: Material | None, used: set[Any]) -> tuple[str, str]:
    """(acción, error) de una fila frente a lo que hay en el catálogo."""
    if current is None:
        return NEW, ""
    if current.unit != data.unit.strip() and current.pk in used:
        return "", UNIT_LOCKED.format(code=current.code)
    same = (current.name, current.unit, current.category) == (
        data.name.strip(),
        data.unit.strip(),
        data.category.strip(),
    )
    return (SAME if same else UPDATE), ""


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied


def preview(ctx: RequestContext, data: bytes) -> ImportPreview:
    _require_manager(ctx)
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, sheet, max_rows=MAX_IMPORT_ROWS + 1)
    except ExcelRejected as exc:
        return ImportPreview(errors=(str(exc),))
    if not table:
        return ImportPreview(errors=("El archivo está vacío.",))
    columns, missing = _columns(table[0])
    if missing:
        return ImportPreview(errors=tuple(missing))
    existing = {material.code.casefold(): material for material in scoped_qs(ctx, Material)}
    used = with_movements(ctx, [material.pk for material in existing.values()])
    rows: list[ImportRow] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    for line, values in enumerate(table[1:], start=2):
        texts = [
            _text(values[columns[name]]) if name in columns and columns[name] < len(values) else ""
            for name in HEADER
        ]
        if not any(texts):
            continue
        item = MaterialData(code=texts[0], name=texts[1], unit=texts[2], category=texts[3])
        problems = material_errors(item)
        key = item.code.strip().casefold()
        if key and key in seen:
            problems.append(f"El código «{item.code.strip()}» se repite (fila {seen[key]}).")
        seen.setdefault(key, line)
        current = existing.get(key)
        action = ""
        if not problems:
            action, problem = _classify(item, current, used)
            if problem:
                problems.append(problem)
        if problems:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            active = current.active if current is not None else True
            rows.append(ImportRow(line, replace(item, active=active), action))
        if len(errors) >= MAX_ERRORS:
            errors.append("Hay más errores: corrige estos y vuelve a subir el archivo.")
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de materiales.")
    if errors:
        return ImportPreview(rows=tuple(rows), errors=tuple(errors))
    return ImportPreview(rows=tuple(rows), token=_sign(ctx, rows))


def _sign(ctx: RequestContext, rows: Sequence[ImportRow]) -> str:
    payload = {
        "org": str(ctx.org_id),
        "user": str(ctx.user_id),
        #: Una vista previa se carga una sola vez (`MaterialImportToken`).
        "nonce": secrets.token_urlsafe(24),
        "rows": [
            [row.line, row.data.code, row.data.name, row.data.unit, row.data.category]
            for row in rows
        ],
    }
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def _load(ctx: RequestContext, token: str) -> Mapping[str, Any]:
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise MaterialRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise MaterialRejected([INVALID]) from exc
    if payload.get("org") != str(ctx.org_id) or payload.get("user") != str(ctx.user_id):
        raise MaterialRejected(["La vista previa no es tuya."])
    if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
        raise MaterialRejected([INVALID])
    return payload


def apply(ctx: RequestContext, token: str) -> tuple[int, int]:
    """Crea y actualiza todas las filas de la vista previa, o ninguna. (creados, actualizados)."""
    _require_manager(ctx)
    payload = _load(ctx, token)
    rows = [
        (int(line), MaterialData(code, name, unit, category))
        for line, code, name, unit, category in payload.get("rows", [])
    ]
    errors = [f"Fila {line}: {problem}" for line, data in rows for problem in material_errors(data)]
    if errors or not rows:
        raise MaterialRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    scoped_qs(ctx, MaterialImportToken).create(
                        org_id=ctx.org_id, nonce=payload["nonce"], applied_by_id=ctx.user_id
                    )
            except IntegrityError as exc:
                raise MaterialRejected([ALREADY_APPLIED]) from exc
            existing = {
                material.code.casefold(): material
                for material in scoped_qs(ctx, Material).select_for_update()
            }
            used = with_movements(ctx, [material.pk for material in existing.values()])
            created: list[Material] = []
            changed: list[Material] = []
            problems: list[str] = []
            now = timezone.now()
            for line, data in rows:
                current = existing.get(data.code.strip().casefold())
                action, problem = _classify(data, current, used)
                if problem:
                    problems.append(f"Fila {line}: {problem}")
                elif current is None:
                    created.append(
                        Material(
                            org_id=ctx.org_id,
                            code=data.code.strip(),
                            name=data.name.strip(),
                            unit=data.unit.strip(),
                            category=data.category.strip(),
                        )
                    )
                elif action == UPDATE:
                    current.name = data.name.strip()
                    current.unit = data.unit.strip()
                    current.category = data.category.strip()
                    current.updated_at = now
                    changed.append(current)
            if problems:
                raise MaterialRejected(problems[:MAX_ERRORS])
            scoped_qs(ctx, Material).bulk_create(created)
            scoped_qs(ctx, Material).bulk_update(
                changed, ["name", "unit", "category", "updated_at"]
            )
            audit.record(
                ctx,
                action=AuditAction.MATERIAL_IMPORTED,
                target=f"organization:{ctx.org_id}",
                metadata={"created": len(created), "updated": len(changed)},
            )
    except IntegrityError as exc:
        if CODE_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([RACE]) from exc
    return len(created), len(changed)

"""Catálogo de materiales de la organización (costos, pieza 6c, spec 1.1 y 3.1).

Lo comparten todos los proyectos y lo gestiona quien tiene `create` y `update` de Costos con
alcance de toda la organización (`can_manage_material_catalog`). Un material no se borra: se
desactiva (deja de ofrecerse en formularios nuevos, pero sigue en el kárdex y en los saldos).
La unidad de un material que ya tiene movimientos o líneas de compra no se cambia.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Material, MaterialPurchaseLine, StockMovement
from suno.core.policies import can_manage_material_catalog
from suno.core.scoping import scoped_qs

from .common import MaterialRejected

MAX_CODE = 40
MAX_NAME = 200
MAX_UNIT = 20
MAX_CATEGORY = 80
CODE_CONSTRAINT = "material_org_code_ci_uq"
UNIT_LOCKED = "{code} ya tiene movimientos: su unidad no se cambia (crea otro material)."


@dataclass(frozen=True)
class MaterialData:
    code: str
    name: str
    unit: str
    category: str = ""
    active: bool = True


def material_errors(data: MaterialData) -> list[str]:
    errors: list[str] = []
    for value, limit, missing, what in (
        (data.code, MAX_CODE, "Escribe el código.", "El código"),
        (data.name, MAX_NAME, "Escribe el nombre.", "El nombre"),
        (data.unit, MAX_UNIT, "Escribe la unidad (por ejemplo m3, kg o und).", "La unidad"),
    ):
        text = value.strip()
        if not text:
            errors.append(missing)
        elif len(text) > limit:
            errors.append(f"{what} pasa de {limit} caracteres.")
    if len(data.category.strip()) > MAX_CATEGORY:
        errors.append(f"La categoría pasa de {MAX_CATEGORY} caracteres.")
    return errors


def code_taken(ctx: RequestContext, code: str, exclude: UUID | None = None) -> bool:
    found = scoped_qs(ctx, Material).filter(code__iexact=code.strip())
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


def with_movements(ctx: RequestContext, material_ids: Iterable[UUID]) -> set[UUID]:
    """Los materiales que ya se usaron (movimientos de inventario o líneas de compra)."""
    ids = list(material_ids)
    used = set(
        scoped_qs(ctx, StockMovement)
        .filter(material_id__in=ids)
        .values_list("material_id", flat=True)
        .distinct()
    )
    return used | set(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(material_id__in=ids)
        .values_list("material_id", flat=True)
        .distinct()
    )


def material_metadata(material: Material) -> dict[str, Any]:
    return {
        "material": str(material.pk),
        "code": material.code,
        "unit": material.unit,
        "active": material.active,
    }


@contextmanager
def _unique_code(code: str) -> Iterator[None]:
    """Otra persona guardó el mismo código entre la comprobación y la escritura: la restricción
    lo detiene y aquí vuelve el mensaje de siempre (por fuera de `transaction.atomic`)."""
    try:
        yield
    except IntegrityError as exc:
        if CODE_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([f"Ya hay un material con el código {code.strip()}."]) from exc


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied


def _fields(data: MaterialData) -> dict[str, Any]:
    return {
        "code": data.code.strip(),
        "name": data.name.strip(),
        "unit": data.unit.strip(),
        "category": data.category.strip(),
        "active": data.active,
    }


def create_material(ctx: RequestContext, data: MaterialData) -> Material:
    _require_manager(ctx)
    errors = material_errors(data)
    if errors:
        raise MaterialRejected(errors)
    with _unique_code(data.code), transaction.atomic():
        if code_taken(ctx, data.code):
            raise MaterialRejected([f"Ya hay un material con el código {data.code.strip()}."])
        material = scoped_qs(ctx, Material).create(org_id=ctx.org_id, **_fields(data))
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_CREATED,
            target=f"material:{material.pk}",
            metadata=material_metadata(material),
        )
    return material


def update_material(ctx: RequestContext, material_id: UUID, data: MaterialData) -> Material:
    _require_manager(ctx)
    errors = material_errors(data)
    if errors:
        raise MaterialRejected(errors)
    with _unique_code(data.code), transaction.atomic():
        material = scoped_qs(ctx, Material).select_for_update().filter(pk=material_id).first()
        if material is None:
            raise Http404
        if code_taken(ctx, data.code, exclude=material.pk):
            raise MaterialRejected([f"Ya hay un material con el código {data.code.strip()}."])
        fields = _fields(data)
        if fields["unit"] != material.unit and with_movements(ctx, [material.pk]):
            raise MaterialRejected([UNIT_LOCKED.format(code=material.code)])
        for field, value in fields.items():
            setattr(material, field, value)
        material.save()
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_UPDATED,
            target=f"material:{material.pk}",
            metadata=material_metadata(material),
        )
    return material

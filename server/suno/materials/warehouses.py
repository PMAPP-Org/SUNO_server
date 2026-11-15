"""Bodegas del proyecto (costos, pieza 6c, spec 1.2 y 3.2 "Bodegas").

Crear pide `create` de Costos; renombrar, desactivar y activar, `can_update_cost` sobre la
bodega (el Colaborador, solo las que creó; decisión 3 del plan). Solo se desactiva una bodega
sin inventario (cantidad y valor en cero en todos sus materiales).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Project, StockBalance, Warehouse
from suno.core.policies import can_create_costs, can_update_cost
from suno.core.scoping import scoped_qs

from .common import MaterialRejected, lock_project, locked

MAX_NAME = 120
NAME_CONSTRAINT = "warehouse_project_name_ci_uq"
HAS_STOCK = "La bodega tiene inventario: trasládalo o ajústalo antes de desactivarla."


def _name(name: str) -> str:
    text = name.strip()
    if not text:
        raise MaterialRejected(["Escribe el nombre de la bodega."])
    if len(text) > MAX_NAME:
        raise MaterialRejected([f"El nombre pasa de {MAX_NAME} caracteres."])
    return text


def _taken_message(name: str) -> str:
    return f"Ya hay una bodega «{name}» en el proyecto."


def _taken(ctx: RequestContext, project: Project, name: str, exclude: UUID | None = None) -> bool:
    found = scoped_qs(ctx, Warehouse).filter(project=project, name__iexact=name)
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


@contextmanager
def _unique_name(name: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        if NAME_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([_taken_message(name)]) from exc


def _metadata(warehouse: Warehouse) -> dict[str, Any]:
    return {
        "project": str(warehouse.project_id),
        "warehouse": str(warehouse.pk),
        "name": warehouse.name,
        "active": warehouse.active,
    }


def create_warehouse(ctx: RequestContext, project: Project, name: str) -> Warehouse:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    text = _name(name)
    with _unique_name(text), transaction.atomic():
        lock_project(ctx, project)
        if _taken(ctx, project, text):
            raise MaterialRejected([_taken_message(text)])
        warehouse = scoped_qs(ctx, Warehouse).create(
            org_id=ctx.org_id,
            project=project,
            name=text,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_CREATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse


def rename_warehouse(
    ctx: RequestContext, project: Project, warehouse_id: UUID, name: str
) -> Warehouse:
    text = _name(name)
    with _unique_name(text), transaction.atomic():
        lock_project(ctx, project)
        warehouse = locked(ctx, Warehouse, project, warehouse_id)
        if not can_update_cost(ctx, warehouse):
            raise PermissionDenied
        if _taken(ctx, project, text, exclude=warehouse.pk):
            raise MaterialRejected([_taken_message(text)])
        warehouse.name = text
        warehouse.updated_by_id = ctx.user_id
        warehouse.save(update_fields=["name", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_UPDATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse


def set_warehouse_active(
    ctx: RequestContext, project: Project, warehouse_id: UUID, active: bool
) -> Warehouse:
    with transaction.atomic():
        lock_project(ctx, project)
        warehouse = locked(ctx, Warehouse, project, warehouse_id)
        if not can_update_cost(ctx, warehouse):
            raise PermissionDenied
        if not active and (
            scoped_qs(ctx, StockBalance)
            .filter(warehouse=warehouse)
            .filter(Q(quantity__gt=0) | Q(value__gt=0))
            .exists()
        ):
            raise MaterialRejected([HAS_STOCK])
        warehouse.active = active
        warehouse.updated_by_id = ctx.user_id
        warehouse.save(update_fields=["active", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_UPDATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse

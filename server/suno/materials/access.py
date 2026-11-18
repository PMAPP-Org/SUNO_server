"""Quién ve los materiales del proyecto y cómo se resuelven los objetos de la URL.

Son de la sección Costos (spec 3.5): el mismo 404 de la 6a para quien no los lee. Todo id de
otra organización, otro proyecto u otra bodega es 404."""

from __future__ import annotations

from uuid import UUID

from django.db import models
from django.http import Http404

from suno.core.context import RequestContext
from suno.core.models import Material, Project, Warehouse
from suno.core.scoping import scoped_qs
from suno.costs.access import get_costs_project


def get_materials_project(ctx: RequestContext, project_id: UUID) -> Project:
    return get_costs_project(ctx, project_id)


def get_document[M: models.Model](
    ctx: RequestContext, model: type[M], project: Project, pk: UUID, *related: str
) -> M:
    found = scoped_qs(ctx, model).select_related(*related).filter(pk=pk, project=project).first()
    if found is None:
        raise Http404
    return found


def get_warehouse(ctx: RequestContext, project: Project, warehouse_id: UUID) -> Warehouse:
    return get_document(ctx, Warehouse, project, warehouse_id)


def get_material(ctx: RequestContext, material_id: UUID) -> Material:
    found = scoped_qs(ctx, Material).filter(pk=material_id).first()
    if found is None:
        raise Http404
    return found

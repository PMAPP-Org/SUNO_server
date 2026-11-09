"""Quién ve presupuestos y cómo se resuelven el proyecto y el presupuesto de la URL."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from suno.core.access import Section
from suno.core.context import RequestContext
from suno.core.models import Budget, Project
from suno.core.policies import can_view_budgets
from suno.core.scoping import scoped_qs
from suno.projects.access import get_visible_project


def get_budget_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404: si el usuario no ve presupuestos (agente de campo),
    **antes** de mirar el proyecto, aunque lo vea por ser responsable de una
    tarea; si es de otra organización o no lo ve (RD-11)."""
    if not ctx.has_section_access(Section.BUDGETS) or not can_view_budgets(ctx, ctx.org_id):
        raise Http404
    return get_visible_project(ctx, project_id)


def get_budget(ctx: RequestContext, project: Project, budget_id: UUID) -> Budget:
    """El presupuesto del proyecto, o 404."""
    try:
        return scoped_qs(ctx, Budget).get(pk=budget_id, project=project)
    except Budget.DoesNotExist as exc:
        raise Http404 from exc

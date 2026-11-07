"""Quién ve y quién edita la matriz: los mismos roles que los presupuestos."""

from __future__ import annotations

from uuid import UUID

from django.core.exceptions import PermissionDenied

from suno.budgets.access import get_budget_project
from suno.core.context import RequestContext
from suno.core.models import Project
from suno.core.policies import can_manage_budgets


def get_matrix_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 si el usuario no ve presupuestos (antes de mirar el proyecto)."""
    return get_budget_project(ctx, project_id)


def require_manage(ctx: RequestContext) -> None:
    if not can_manage_budgets(ctx, ctx.org_id):
        raise PermissionDenied

"""Quién ve el libro de costos y cómo se resuelven el proyecto y el movimiento de la URL."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from suno.core.access import Section
from suno.core.context import RequestContext
from suno.core.models import CostEntry, Project
from suno.core.policies import can_read_costs
from suno.core.scoping import scoped_qs
from suno.projects.access import get_visible_project


def get_costs_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 (RD-11): sin la sección Costos, **antes** de mirar el proyecto; si es
    de otra organización o no se ve; o si el alcance de Costos no lo incluye."""
    if not ctx.has_section_access(Section.COSTS):
        raise Http404
    project = get_visible_project(ctx, project_id)
    if not can_read_costs(ctx, project):
        raise Http404
    return project


def get_entry(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    entry = (
        scoped_qs(ctx, CostEntry)
        .select_related("created_by", "updated_by", "decided_by", "chapter", "task", "reverses")
        .filter(pk=entry_id, project=project)
        .first()
    )
    if entry is None:
        raise Http404
    return entry

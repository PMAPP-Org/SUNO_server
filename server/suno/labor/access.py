"""Quién ve la mano de obra y cómo se resuelven los objetos de la URL (costos, pieza 6d).

- Pestaña "Tareo" del proyecto: sección Mano de obra con `read` en ese proyecto; si no, 404
  (RD-11), igual para ids de otra organización o de otro proyecto.
- Registro de la organización: `can_read_labor_registry` (404 si no); escribir,
  `can_manage_labor_registry` (403 si no lee nada más)."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest

from suno.core.access import Section
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import AppUser, PayrollImport, Project, Timesheet, Worker
from suno.core.policies import (
    can_manage_labor_registry,
    can_read_labor,
    can_read_labor_registry,
)
from suno.core.scoping import scoped_qs
from suno.projects.access import get_visible_project


def get_labor_project(ctx: RequestContext, project_id: UUID) -> Project:
    if not ctx.has_section_access(Section.LABOR):
        raise Http404
    project = get_visible_project(ctx, project_id)
    if not can_read_labor(ctx, project):
        raise Http404
    return project


def get_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, *related: str
) -> Timesheet:
    found = (
        scoped_qs(ctx, Timesheet)
        .select_related(*related)
        .filter(pk=timesheet_id, project=project)
        .first()
    )
    if found is None:
        raise Http404
    return found


def registry_ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not can_read_labor_registry(ctx, ctx.org_id):
        raise Http404
    return ctx


def registry_manager(request: HttpRequest) -> RequestContext:
    ctx = registry_ctx(request)
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    return ctx


def get_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    found = scoped_qs(ctx, Worker).filter(pk=worker_id).first()
    if found is None:
        raise Http404
    return found


def get_payroll(ctx: RequestContext, payroll_id: UUID) -> PayrollImport:
    found = scoped_qs(ctx, PayrollImport).filter(pk=payroll_id).first()
    if found is None:
        raise Http404
    return found

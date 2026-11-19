"""Una planilla de tareo: cuadrícula, decisión, documentos e importación (spec 3.2 a 3.4)."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from suno.projects.views import _ctx

from .access import get_labor_project, get_timesheet
from .views import page


@login_required(login_url="platform_login")
@require_GET
def timesheet_detail(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    return page(request, ctx, project, "suno/labor/timesheet_detail.html", sheet=sheet)

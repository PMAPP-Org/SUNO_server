"""Páginas del cronograma en /plataforma/proyectos/ (sesión de Django).

Mismo esquema de acceso que `core/views.py`: se exige sesión y membresía;
cada consulta pasa por `scoped_qs` y por `projects.access`. Un proyecto que
el usuario no ve responde 404 (RD-11); una acción para la que le falta el
rol responde 403.
"""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, Project, ScheduleImport, ScheduleImportStatus
from visitacampo.core.policies import can_export_schedule, can_manage_projects
from visitacampo.core.scoping import get_organization, scoped_qs

from .access import get_visible_project, visible_projects
from .schedule import build_schedule, filter_rows


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not ctx.is_member:
        raise PermissionDenied
    return ctx


def _render(
    request: HttpRequest, ctx: RequestContext, template: str, context: dict[str, object]
) -> HttpResponse:
    return render(request, template, {"organization": get_organization(ctx), **context})


class ProjectForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nombre")
    code = forms.CharField(max_length=50, required=False, label="Código")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def project_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    can_manage = can_manage_projects(ctx, ctx.org_id)
    form = ProjectForm(request.POST or None)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            if scoped_qs(ctx, Project).filter(name__iexact=name).exists():
                form.add_error("name", "Ya existe un proyecto con ese nombre.")
            else:
                project = scoped_qs(ctx, Project).create(
                    org_id=ctx.org_id,
                    name=name,
                    code=form.cleaned_data["code"].strip(),
                    description=form.cleaned_data["description"].strip(),
                    created_by_id=ctx.user_id,
                )
                audit.record(
                    ctx, action=AuditAction.PROJECT_CREATED, target=f"project:{project.pk}"
                )
                return redirect("projects:schedule", project.pk)

    versions = dict(
        scoped_qs(ctx, ScheduleImport)
        .filter(status=ScheduleImportStatus.APPLIED)
        .values("project_id")
        .annotate(n=Max("number"))
        .values_list("project_id", "n")
    )
    items = [
        {
            "project": project,
            "total": build_schedule(ctx, project).total,
            "version": versions.get(project.pk),
        }
        for project in visible_projects(ctx).order_by("name")
    ]
    return _render(
        request,
        ctx,
        "suno/projects/list.html",
        {"items": items, "form": form, "can_manage": can_manage},
    )


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _project_context(ctx: RequestContext, project: Project) -> dict[str, object]:
    return {
        "project": project,
        "can_manage": can_manage_projects(ctx, ctx.org_id),
        "can_export": can_export_schedule(ctx, ctx.org_id),
    }


@login_required(login_url="platform_login")
def schedule(request: HttpRequest, project_id: UUID, task_id: UUID | None = None) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    filters = {
        "etapa": request.GET.get("etapa", ""),
        "responsable": request.GET.get("responsable", ""),
        "criticas": request.GET.get("criticas") == "1",
        "atrasadas": request.GET.get("atrasadas") == "1",
    }
    rows = filter_rows(
        data.rows,
        stage_id=_parse_uuid(filters["etapa"] or None),
        responsible_id=_parse_uuid(filters["responsable"] or None),
        critical_only=bool(filters["criticas"]),
        delayed_only=bool(filters["atrasadas"]),
    )
    responsible_choices = sorted(
        {u.pk: u for row in data.rows for u in row.responsibles}.values(),
        key=lambda u: (u.display_name or u.username).lower(),
    )
    return _render(
        request,
        ctx,
        "suno/projects/schedule.html",
        {
            **_project_context(ctx, project),
            "schedule": data,
            "rows": rows,
            "filters": filters,
            "responsible_choices": responsible_choices,
        },
    )

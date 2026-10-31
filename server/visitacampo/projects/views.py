"""Páginas del cronograma en /plataforma/proyectos/ (sesión de Django).

Mismo esquema de acceso que `core/views.py`: se exige sesión y membresía;
cada consulta pasa por `scoped_qs` y por `projects.access`. Un proyecto que
el usuario no ve responde 404 (RD-11); una acción para la que le falta el
rol responde 403.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from visitacampo.backoffice.export import safe_cell, to_xlsx
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    Membership,
    Project,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
from visitacampo.core.policies import (
    can_assign_task_responsibles,
    can_export_schedule,
    can_manage_projects,
)
from visitacampo.core.scoping import get_organization, scoped_qs

from .access import effective_responsibles, get_visible_project, visible_projects
from .importer import ImportRejected, apply_import, discard_import, stage_import
from .mspdi import MAX_BYTES
from .schedule import ScheduleRow, build_schedule, filter_rows


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
def schedule(request: HttpRequest, project_id: UUID) -> HttpResponse:
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


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def task_detail(request: HttpRequest, project_id: UUID, task_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    try:
        task = scoped_qs(ctx, Task).select_related("stage").get(pk=task_id, project=project)
    except Task.DoesNotExist as exc:
        raise Http404 from exc
    can_assign = can_assign_task_responsibles(ctx, ctx.org_id)
    error = ""
    if request.method == "POST":
        if not can_assign:
            raise PermissionDenied
        user_id = _parse_uuid(request.POST.get("user"))
        is_member = (
            user_id is not None and scoped_qs(ctx, Membership).filter(user_id=user_id).exists()
        )
        if not is_member:
            error = "El usuario no pertenece a la organización."
        elif request.POST.get("action") == "remove":
            deleted, _ = scoped_qs(ctx, TaskResponsible).filter(task=task, user_id=user_id).delete()
            if deleted:
                audit.record(
                    ctx,
                    action=AuditAction.TASK_RESPONSIBLE_REMOVED,
                    target=f"task:{task.pk}",
                    metadata={"user": str(user_id)},
                )
            return redirect("projects:task", project.pk, task.pk)
        else:
            try:
                with transaction.atomic():
                    scoped_qs(ctx, TaskResponsible).create(
                        org_id=ctx.org_id, task=task, user_id=user_id, assigned_by_id=ctx.user_id
                    )
            except IntegrityError:
                messages.info(request, "Ya era responsable de esta tarea.")
            else:
                audit.record(
                    ctx,
                    action=AuditAction.TASK_RESPONSIBLE_ASSIGNED,
                    target=f"task:{task.pk}",
                    metadata={"user": str(user_id)},
                )
            return redirect("projects:task", project.pk, task.pk)

    direct = list(scoped_qs(ctx, TaskResponsible).filter(task=task).select_related("user"))
    direct_ids = {link.user_id for link in direct}
    inherited = [
        u for u in effective_responsibles(ctx, project).get(task.pk, []) if u.pk not in direct_ids
    ]
    members = [
        m.user for m in scoped_qs(ctx, Membership).select_related("user").order_by("user__username")
    ]
    return _render(
        request,
        ctx,
        "suno/projects/task.html",
        {
            **_project_context(ctx, project),
            "task": task,
            "direct": direct,
            "inherited": inherited,
            "members": members,
            "can_assign": can_assign,
            "error": error,
            "executors": list(
                scoped_qs(ctx, TaskResource)
                .filter(task=task)
                .values_list("resource__name", flat=True)
            ),
            "predecessors": scoped_qs(ctx, TaskDependency)
            .filter(successor=task)
            .select_related("predecessor"),
        },
    )


def _require_manage(ctx: RequestContext) -> None:
    if not can_manage_projects(ctx, ctx.org_id):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    _require_manage(ctx)
    error = ""
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            error = "Elige un archivo XML."
        elif upload.size is not None and upload.size > MAX_BYTES:
            error = "El archivo supera 50 MB."
        else:
            try:
                staged = stage_import(
                    ctx, project, file_name=upload.name or "cronograma.xml", data=upload.read()
                )
            except ImportRejected as exc:
                error = str(exc)
            else:
                return redirect("projects:import_preview", project.pk, staged.pk)
    return _render(
        request,
        ctx,
        "suno/projects/import.html",
        {**_project_context(ctx, project), "error": error},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    _require_manage(ctx)
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(pk=import_id, project=project)
    except ScheduleImport.DoesNotExist as exc:
        raise Http404 from exc
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "confirm":
                applied = apply_import(ctx, imp.pk)
                audit.record(
                    ctx,
                    action=AuditAction.SCHEDULE_IMPORTED,
                    target=f"project:{project.pk}",
                    metadata={"version": applied.number, "file": applied.file_name},
                )
                messages.success(request, f"Versión {applied.number} aplicada.")
                return redirect("projects:schedule", project.pk)
            if action == "discard":
                discard_import(ctx, imp.pk)
                audit.record(
                    ctx,
                    action=AuditAction.SCHEDULE_IMPORT_DISCARDED,
                    target=f"project:{project.pk}",
                    metadata={"file": imp.file_name},
                )
                messages.info(request, "Importación descartada.")
                return redirect("projects:schedule", project.pk)
        except ImportRejected as exc:
            messages.error(request, str(exc))
            return redirect("projects:import_preview", project.pk, imp.pk)
    return _render(
        request,
        ctx,
        "suno/projects/import_preview.html",
        {**_project_context(ctx, project), "imp": imp, "s": imp.summary},
    )


@login_required(login_url="platform_login")
def history(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    imports = (
        scoped_qs(ctx, ScheduleImport)
        .filter(project=project, status=ScheduleImportStatus.APPLIED)
        .select_related("uploaded_by")
        .order_by("-number")
    )
    return _render(
        request,
        ctx,
        "suno/projects/history.html",
        {**_project_context(ctx, project), "imports": imports},
    )


@login_required(login_url="platform_login")
def import_xml(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(
            pk=import_id, project=project, status=ScheduleImportStatus.APPLIED
        )
    except ScheduleImport.DoesNotExist as exc:
        raise Http404 from exc
    response = HttpResponse(gzip.decompress(bytes(imp.source_xml)), content_type="application/xml")
    safe_code = re.sub(r"[^A-Za-z0-9_.-]", "_", project.code or "proyecto")
    response["Content-Disposition"] = f'attachment; filename="{safe_code}-v{imp.number}.xml"'
    return response


@login_required(login_url="platform_login")
def gantt(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))

    def iso(value: date | None) -> str | None:
        return value.isoformat() if value else None

    rows = [
        {
            "id": str(row.task.pk),
            "wbs": row.task.wbs,
            "name": row.task.name,
            "level": row.task.outline_level,
            "start": iso(row.task.start),
            "finish": iso(row.task.finish),
            "bstart": iso(row.task.baseline_start),
            "bfinish": iso(row.task.baseline_finish),
            "summary": row.task.is_summary,
            "milestone": row.task.is_milestone,
            "critical": row.task.is_critical,
        }
        for row in data.rows
    ]
    ids = {r["id"] for r in rows}
    links = [
        {"from": str(a), "to": str(b), "type": t}
        for a, b, t in scoped_qs(ctx, TaskDependency)
        .filter(project=project)
        .values_list("predecessor_id", "successor_id", "type")
        if str(a) in ids and str(b) in ids
    ]
    # Los nombres vienen del XML: se escapan <, > y & para que el JSON dentro de
    # <script> no pueda cerrar la etiqueta (json.loads los decodifica igual).
    payload = (
        json.dumps({"cut": data.cut.isoformat(), "rows": rows, "links": links})
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return _render(
        request,
        ctx,
        "suno/projects/gantt.html",
        {**_project_context(ctx, project), "gantt_json": payload, "schedule": data},
    )


EXPORT_HEADER = [
    "edt", "tarea", "nivel", "etapa", "resumen", "hito", "critica",
    "ejecutores", "responsables", "inicio", "fin", "inicio_lb", "fin_lb",
    "pct_programado", "pct_suno", "pct_project",
    "atraso_pct_suno", "atraso_pct_project", "atraso_dias_suno", "atraso_dias_project",
    "holgura_total_dias", "holgura_libre_dias",
]  # fmt: skip


def _export_value(value: object) -> object:
    """`safe_cell` no conoce fechas ni Decimal: se convierten antes."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


@login_required(login_url="platform_login")
def export_schedule(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    if not can_export_schedule(ctx, ctx.org_id):
        raise PermissionDenied
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    stage_names = {s.pk: s.name for s in data.stages}

    def row_values(row: ScheduleRow) -> list[object]:
        t, ind = row.task, row.indicators
        return [
            safe_cell(_export_value(value))
            for value in (
                t.wbs, t.name, t.outline_level, stage_names.get(t.stage_id, ""),
                "sí" if t.is_summary else "no", "sí" if t.is_milestone else "no",
                "sí" if t.is_critical else "no",
                ", ".join(row.executors),
                ", ".join(u.display_name or u.username for u in row.responsibles),
                t.start, t.finish, t.baseline_start, t.baseline_finish,
                ind.planned_pct if ind else None,
                ind.suno.actual_pct if ind else None,
                ind.project.actual_pct if ind else None,
                ind.suno.delay_pct if ind else None,
                ind.project.delay_pct if ind else None,
                ind.suno.delay_days if ind else None,
                ind.project.delay_days if ind else None,
                t.total_slack_days, t.free_slack_days,
            )
        ]  # fmt: skip

    content = to_xlsx(EXPORT_HEADER, (row_values(r) for r in data.rows))
    audit.record(
        ctx,
        action=AuditAction.SCHEDULE_EXPORTED,
        target=f"project:{project.pk}",
        metadata={"cut": data.cut.isoformat(), "rows": len(data.rows)},
    )
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="cronograma-{data.cut.isoformat()}.xlsx"'
    )
    return response

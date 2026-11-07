"""Páginas del cronograma en /plataforma/proyectos/ (sesión de Django).

Mismo esquema de acceso que `core/views.py`: se exige sesión y membresía;
cada consulta pasa por `scoped_qs` y por `projects.access`. Un proyecto que
el usuario no ve responde 404 (RD-11); una acción para la que le falta el
rol responde 403.
"""

from __future__ import annotations

import gzip
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

from suno.backoffice.export import safe_cell, to_xlsx
from suno.budgets.totals import money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.download import ascii_filename
from suno.core.models import (
    AppUser,
    Budget,
    BudgetItemVersion,
    BudgetRowKind,
    Membership,
    Project,
    QuantityPlan,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
from suno.core.policies import (
    can_assign_task_responsibles,
    can_export_schedule,
    can_manage_budgets,
    can_manage_projects,
    can_view_budgets,
)
from suno.core.scoping import get_organization, scoped_qs
from suno.matrix.diff import code_key
from suno.matrix.edits import EditRejected, remove_plan, set_plan, set_task_location
from suno.matrix.importer import current_versions
from suno.matrix.locations import from_project, load_tree, project_levels
from suno.matrix.progress import item_pct
from suno.matrix.quantities import (
    executed_by_pair,
    item_facts,
    measured_progress,
    suno_pct_by_task,
    validated_records,
)

from .access import effective_responsibles, get_visible_project, visible_projects
from .field_progress import format_pct, progress_records
from .importer import ImportRejected, apply_import, discard_import, stage_import
from .mspdi import MAX_BYTES
from .schedule import ScheduleRow, build_schedule, filter_rows

#: Registros validados más recientes cuyo registro de cantidades muestra la ficha de tarea.
QUANTITY_HISTORY_RECORDS = 50


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
                try:
                    with transaction.atomic():
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
                except IntegrityError:
                    form.add_error("name", "Ya existe un proyecto con ese nombre.")
                else:
                    return redirect("projects:schedule", project.pk)

    versions = dict(
        scoped_qs(ctx, ScheduleImport)
        .filter(status=ScheduleImportStatus.APPLIED)
        .values("project_id")
        .annotate(n=Max("number"))
        .values_list("project_id", "n")
    )
    projects = list(visible_projects(ctx).order_by("name"))
    # Un solo cálculo del % SUNO para todos los proyectos de la página.
    ids = [project.pk for project in projects]
    measured = measured_progress(ctx, project_ids=ids)
    suno_pct = suno_pct_by_task(ctx, project_ids=ids, measured=measured)
    items = [
        {
            "project": project,
            "total": build_schedule(ctx, project, suno_pct=suno_pct, measured=measured).total,
            "version": versions.get(project.pk),
        }
        for project in projects
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


def _safe_code(project: Project) -> str:
    """Código del proyecto apto para nombres de archivo."""
    return ascii_filename(project.code or "proyecto", "proyecto")


def _project_context(ctx: RequestContext, project: Project) -> dict[str, object]:
    return {
        "project": project,
        "can_manage": can_manage_projects(ctx, ctx.org_id),
        "can_export": can_export_schedule(ctx, ctx.org_id),
        # Pestaña "Presupuestos" de la cabecera (costos, pieza 3).
        "can_view_budgets": can_view_budgets(ctx, ctx.org_id),
    }


@login_required(login_url="platform_login")
def schedule(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    filters = {
        "etapa": str(_parse_uuid(request.GET.get("etapa")) or ""),
        "responsable": str(_parse_uuid(request.GET.get("responsable")) or ""),
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
        task = (
            scoped_qs(ctx, Task)
            .select_related("stage", "retired_in")
            .get(pk=task_id, project=project)
        )
    except Task.DoesNotExist as exc:
        raise Http404 from exc
    can_assign = can_assign_task_responsibles(ctx, ctx.org_id)
    retired = task.retired_in_id is not None
    error = ""
    plan_error = ""
    plan_input: dict[str, str] = {}
    action = request.POST.get("action") if request.method == "POST" else None
    matrix_action = action in ("plan_set", "plan_remove", "location_set")
    if matrix_action:
        if not can_manage_budgets(ctx, ctx.org_id):
            raise PermissionDenied
        try:
            if action == "plan_set":
                budget_id = _parse_uuid(request.POST.get("budget"))
                if budget_id is None:
                    raise EditRejected("Elige un presupuesto.")
                quantity_text = request.POST.get("quantity", "")
                plan, guessed = set_plan(
                    ctx,
                    task,
                    budget_id=budget_id,
                    code=request.POST.get("code", ""),
                    quantity=quantity_text,
                )
                if guessed:
                    messages.info(
                        request,
                        f"La cantidad «{quantity_text.strip()}» se leyó como miles "
                        f"({plan.planned_quantity.normalize():f}); revisa que sea correcto.",
                    )
            elif action == "plan_remove":
                item_id = _parse_uuid(request.POST.get("item"))
                if item_id is not None and not remove_plan(ctx, task, item_id):
                    messages.info(request, "El ítem ya tiene cantidades ejecutadas: quedó en 0.")
            else:
                set_task_location(ctx, task, request.POST.get("location", ""))
        except EditRejected as exc:
            plan_error = str(exc)
            if action == "plan_set":
                # Para volver a mostrar lo que se escribió en "Agregar ítem".
                plan_input = {
                    key: request.POST.get(key, "") for key in ("budget", "code", "quantity")
                }
        else:
            return redirect("projects:task", project.pk, task.pk)
    if request.method == "POST" and not matrix_action:
        if not can_assign:
            raise PermissionDenied
        user_id = _parse_uuid(request.POST.get("user"))
        if request.POST.get("action") == "remove":
            if user_id is not None:
                with transaction.atomic():
                    deleted, _ = (
                        scoped_qs(ctx, TaskResponsible).filter(task=task, user_id=user_id).delete()
                    )
                    if deleted:
                        audit.record(
                            ctx,
                            action=AuditAction.TASK_RESPONSIBLE_REMOVED,
                            target=f"task:{task.pk}",
                            metadata={"user": str(user_id)},
                        )
            return redirect("projects:task", project.pk, task.pk)
        if retired:
            error = "La tarea está retirada: no se le pueden asignar responsables."
        elif user_id is None or not scoped_qs(ctx, Membership).filter(user_id=user_id).exists():
            error = "El usuario no pertenece a la organización."
        else:
            try:
                with transaction.atomic():
                    scoped_qs(ctx, TaskResponsible).create(
                        org_id=ctx.org_id, task=task, user_id=user_id, assigned_by_id=ctx.user_id
                    )
                    audit.record(
                        ctx,
                        action=AuditAction.TASK_RESPONSIBLE_ASSIGNED,
                        target=f"task:{task.pk}",
                        metadata={"user": str(user_id)},
                    )
            except IntegrityError:
                messages.info(request, "Ya era responsable de esta tarea.")
            return redirect("projects:task", project.pk, task.pk)

    direct = list(scoped_qs(ctx, TaskResponsible).filter(task=task).select_related("user"))
    direct_ids = {link.user_id for link in direct}
    inherited = [
        u for u in effective_responsibles(ctx, project).get(task.pk, []) if u.pk not in direct_ids
    ]
    members = (
        [
            m.user
            for m in scoped_qs(ctx, Membership).select_related("user").order_by("user__username")
        ]
        if can_assign and not retired
        else []
    )
    measured = measured_progress(ctx, task_ids=[task.pk])
    suno = suno_pct_by_task(ctx, task_ids=[task.pk], measured=measured).get(task.pk)
    suno_text = format_pct(suno) if suno is not None else "—"
    levels = project_levels(ctx, project)
    tree = load_tree(ctx, project)
    plans = list(scoped_qs(ctx, QuantityPlan).filter(task=task))
    executed = executed_by_pair(ctx, task_ids=[task.pk])
    facts = item_facts(ctx, [p.item_id for p in plans])
    show_amounts = can_view_budgets(ctx, ctx.org_id)

    def pct_text(planned: Decimal, done: Decimal) -> str:
        pct = item_pct(planned, done)
        return format_pct(pct) if pct is not None else "—"

    plan_rows = sorted(
        (
            {
                "item_id": p.item_id,
                "code": facts[p.item_id].code if p.item_id in facts else "?",
                "description": facts[p.item_id].description if p.item_id in facts else "",
                "unit": facts[p.item_id].unit if p.item_id in facts else "",
                "planned": p.planned_quantity,
                "executed": executed.get((task.pk, p.item_id), Decimal(0)),
                "pct_text": pct_text(
                    p.planned_quantity, executed.get((task.pk, p.item_id), Decimal(0))
                ),
                "value": (
                    money(p.planned_quantity * (facts[p.item_id].unit_price or 0))
                    if show_amounts and p.item_id in facts
                    else None
                ),
            }
            for p in plans
        ),
        key=lambda row: code_key(str(row["code"])),
    )
    # Ítems con cantidades validadas que no están en el plan de la tarea: no suman al %.
    unplanned = item_facts(ctx, {item_id for (_, item_id) in executed} - {p.item_id for p in plans})
    unplanned_executed = sorted(unplanned.values(), key=lambda f: code_key(f.code))
    # Ítems del plan que una reimportación volvió capítulos: la fila se conserva y lo que
    # el campo registre en ellos se acepta, pero no miden la tarea (`plan_lines`).
    chapter_plans = sorted(
        (
            facts[p.item_id]
            for p in plans
            if p.item_id in facts and facts[p.item_id].kind == BudgetRowKind.CHAPTER
        ),
        key=lambda f: code_key(f.code),
    )
    # Ítems del plan que ya no están en la versión vigente de su presupuesto: siguen
    # midiendo con el precio de la última versión que los tenía; se marcan.
    in_budget = set(
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=current_versions(ctx, project), item_id__in=[p.item_id for p in plans])
        .values_list("item_id", flat=True)
    )
    retired_plans = sorted(
        (facts[p.item_id] for p in plans if p.item_id in facts and p.item_id not in in_budget),
        key=lambda f: code_key(f.code),
    )
    # Historial: los ítems de lo validado ya están en `facts` o en `unplanned`.
    history, history_truncated = validated_records(ctx, task.pk, max_records=QUANTITY_HISTORY_RECORDS)
    known = {**facts, **unplanned}
    quantity_history = [
        {
            "record": entry.record,
            "code": known[entry.item_id].code if entry.item_id in known else "?",
            "unit": known[entry.item_id].unit if entry.item_id in known else "",
            # Sin ubicación precisada: la ubicación *actual* de la tarea (no la que tenía al
            # registrarse; si la tarea se movió, la fila muestra la nueva).
            "location": tree.label(entry.location_id or task.location_id),
            "quantity": entry.quantity,
            "note": entry.note,
        }
        for entry in history
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
            "retired": retired,
            "retired_version": task.retired_in.number if task.retired_in else None,
            "error": error,
            "executors": list(
                scoped_qs(ctx, TaskResource)
                .filter(task=task)
                .values_list("resource__name", flat=True)
            ),
            "predecessors": scoped_qs(ctx, TaskDependency)
            .filter(successor=task)
            .select_related("predecessor"),
            "suno_pct_text": suno_text,
            "measured_by_quantities": task.pk in measured,
            "field_records": progress_records(ctx, task),
            "plan_rows": plan_rows,
            "unplanned_executed": unplanned_executed,
            "chapter_plans": chapter_plans,
            "retired_plans": retired_plans,
            "quantity_history": quantity_history,
            "history_truncated": history_truncated,
            "history_records": QUANTITY_HISTORY_RECORDS,
            "show_amounts": show_amounts,
            "can_edit_plan": can_manage_budgets(ctx, ctx.org_id) and not task.is_summary,
            "plan_error": plan_error,
            "plan_input": plan_input,
            "location_label": tree.label(task.location_id),
            "location_editable": bool(levels) and not from_project(levels),
            "location_hint": " / ".join(level.name for level in levels),
            "location_choices": sorted(tree.label(pk) for pk in tree.nodes),
            "budget_choices": scoped_qs(ctx, Budget).filter(project=project).order_by("name"),
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
                with transaction.atomic():
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
                with transaction.atomic():
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
    if not can_export_schedule(ctx, ctx.org_id):
        raise PermissionDenied
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(
            pk=import_id, project=project, status=ScheduleImportStatus.APPLIED
        )
    except ScheduleImport.DoesNotExist as exc:
        raise Http404 from exc
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.SCHEDULE_SOURCE_DOWNLOADED,
            target=f"project:{project.pk}",
            metadata={"version": imp.number},
        )
    response = HttpResponse(gzip.decompress(bytes(imp.source_xml)), content_type="application/xml")
    response["Content-Disposition"] = (
        f'attachment; filename="{_safe_code(project)}-v{imp.number}.xml"'
    )
    return response


@login_required(login_url="platform_login")
def gantt(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    scale = request.GET.get("escala", "")
    if scale not in {"8", "18", "36"}:
        scale = "18"

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
    # Los nombres vienen del XML: la plantilla lo emite con `json_script`, que
    # escapa <, > y & para que el JSON no pueda cerrar la etiqueta <script>.
    payload = {"cut": data.cut.isoformat(), "rows": rows, "links": links}
    return _render(
        request,
        ctx,
        "suno/projects/gantt.html",
        {
            **_project_context(ctx, project),
            "gantt_data": payload,
            "schedule": data,
            "scale": scale,
        },
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
        f'attachment; filename="{_safe_code(project)}-cronograma-{data.cut.isoformat()}.xlsx"'
    )
    return response

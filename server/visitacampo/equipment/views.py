from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from visitacampo.core.access import Capability, Section, SectionRole
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    AuditEvent,
    Equipment,
    EquipmentDocument,
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentType,
    EquipmentUsageStatus,
    Membership,
    Project,
)
from visitacampo.core.navigation import platform_navigation
from visitacampo.core.scoping import get_organization, scoped_qs
from visitacampo.equipment.forms import (
    EquipmentDocumentForm,
    EquipmentForm,
    EquipmentOperatorForm,
    EquipmentProjectForm,
    EquipmentTypeForm,
    EquipmentUsageForm,
)
from visitacampo.equipment.services import (
    add_document,
    assign_operator,
    create_equipment,
    create_equipment_type,
    create_equipment_usage,
    get_visible_equipment,
    link_project,
    update_equipment,
    visible_equipment,
    visible_usage,
)


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not ctx.has_section_access(Section.EQUIPMENT):
        raise PermissionDenied
    return ctx


def _render(
    request: HttpRequest,
    ctx: RequestContext,
    template: str,
    context: dict[str, Any],
) -> HttpResponse:
    return render(
        request,
        template,
        {
            "organization": get_organization(ctx),
            "navigation": platform_navigation(ctx),
            **context,
        },
    )


def _message_validation(request: HttpRequest, exc: ValidationError) -> None:
    messages.error(request, " ".join(exc.messages))


def _project_ids(equipment: Equipment) -> list[UUID]:
    return list(
        equipment.project_links.filter(ended_on__isnull=True).values_list("project_id", flat=True)
    )


def _can(ctx: RequestContext, equipment: Equipment, capability: Capability) -> bool:
    project_ids = _project_ids(equipment)
    return ctx.has_capability(Section.EQUIPMENT, capability) or any(
        ctx.has_capability(Section.EQUIPMENT, capability, project_id)
        for project_id in project_ids
    )


def _can_schedule_any(ctx: RequestContext) -> bool:
    if ctx.has_capability(Section.EQUIPMENT, Capability.SCHEDULE):
        return True
    access = ctx.access_for(Section.EQUIPMENT)
    return bool(
        access
        and access.project_ids
        and any(
            ctx.has_capability(Section.EQUIPMENT, Capability.SCHEDULE, project_id)
            for project_id in access.project_ids
        )
    )


@login_required(login_url="platform_login")
def equipment_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    queryset = visible_equipment(ctx).select_related("equipment_type")
    query = request.GET.get("q", "").strip()
    selected_type = request.GET.get("type", "")
    selected_status = request.GET.get("status", "")
    selected_project = request.GET.get("project", "")
    if query:
        queryset = queryset.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(license_plate__icontains=query)
        )
    if selected_type:
        queryset = queryset.filter(equipment_type_id=selected_type)
    if selected_status:
        queryset = queryset.filter(status=selected_status)
    if selected_project:
        queryset = queryset.filter(project_links__project_id=selected_project).distinct()

    page = Paginator(queryset.order_by("code"), 25).get_page(request.GET.get("page"))
    items = []
    for equipment in page.object_list:
        current_project = (
            scoped_qs(ctx, EquipmentProject)
            .filter(equipment=equipment, ended_on__isnull=True)
            .select_related("project")
            .order_by("-started_on")
            .first()
        )
        items.append({"equipment": equipment, "project": current_project})
    return _render(
        request,
        ctx,
        "suno/equipment/list.html",
        {
            "items": items,
            "page": page,
            "query": query,
            "selected_type": selected_type,
            "selected_status": selected_status,
            "selected_project": selected_project,
            "types": scoped_qs(ctx, EquipmentType).filter(active=True).order_by("name"),
            "statuses": Equipment._meta.get_field("status").choices,
            "projects": scoped_qs(ctx, Project).order_by("name"),
            "can_create": ctx.has_capability(Section.EQUIPMENT, Capability.CREATE),
        },
    )


@login_required(login_url="platform_login")
def usage_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    queryset = visible_usage(ctx).select_related("equipment", "project", "operator")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    selected_project = request.GET.get("project", "")
    selected_equipment = request.GET.get("equipment", "")
    selected_operator = request.GET.get("operator", "")
    selected_status = request.GET.get("status", "")
    parsed_date_from = parse_date(date_from)
    parsed_date_to = parse_date(date_to)
    if parsed_date_from:
        queryset = queryset.filter(usage_date__gte=parsed_date_from)
    if parsed_date_to:
        queryset = queryset.filter(usage_date__lte=parsed_date_to)
    try:
        project_id = UUID(selected_project) if selected_project else None
        equipment_id = UUID(selected_equipment) if selected_equipment else None
        operator_id = UUID(selected_operator) if selected_operator else None
    except ValueError:
        project_id = equipment_id = operator_id = None
    if project_id:
        queryset = queryset.filter(project_id=project_id)
    if equipment_id:
        queryset = queryset.filter(equipment_id=equipment_id)
    if operator_id:
        queryset = queryset.filter(operator_id=operator_id)
    if selected_status:
        queryset = queryset.filter(status=selected_status)

    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return _render(
        request,
        ctx,
        "suno/equipment/usage_list.html",
        {
            "page": page,
            "date_from": date_from,
            "date_to": date_to,
            "selected_project": selected_project,
            "selected_equipment": selected_equipment,
            "selected_operator": selected_operator,
            "selected_status": selected_status,
            "projects": scoped_qs(ctx, Project).order_by("name"),
            "equipment_options": visible_equipment(ctx).order_by("code"),
            "operators": (
                scoped_qs(ctx, Membership)
                .filter(
                    user__section_grants__section=Section.EQUIPMENT,
                    user__section_grants__role=SectionRole.EQUIPMENT_OPERATOR,
                    user__section_grants__active=True,
                )
                .select_related("user")
                .order_by("user__username")
            ),
            "statuses": EquipmentUsageStatus.choices,
            "can_create": _can_schedule_any(ctx),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def usage_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not _can_schedule_any(ctx):
        raise PermissionDenied
    initial = {
        "usage_date": request.GET.get("date"),
        "equipment": request.GET.get("equipment"),
        "project": request.GET.get("project"),
    }
    form = EquipmentUsageForm(ctx, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        equipment = data.pop("equipment")
        project = data.pop("project")
        operator = data.pop("operator")
        try:
            create_equipment_usage(
                ctx,
                equipment_id=equipment.pk,
                project_id=project.pk,
                operator_id=operator.user_id,
                **data,
            )
        except (ValidationError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                form.add_error(None, " ".join(exc.messages))
            else:
                form.add_error(None, "El horario entra en conflicto con otro registro.")
        else:
            messages.success(request, "Uso del equipo registrado.")
            return redirect("equipment:usage_list")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Registrar uso de equipo"},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def equipment_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    form = EquipmentForm(ctx, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        equipment_type = data.pop("equipment_type")
        code = data.pop("code")
        name = data.pop("name")
        data["specifications"] = data.get("specifications") or {}
        try:
            equipment = create_equipment(
                ctx,
                equipment_type_id=equipment_type.pk,
                code=code,
                name=name,
                **data,
            )
        except (ValidationError, IntegrityError):
            form.add_error(
                None,
                "No fue posible registrar el equipo. Verifica código, serie y placa.",
            )
        else:
            messages.success(request, "Equipo registrado.")
            return redirect("equipment:detail", equipment.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Registrar equipo"},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def equipment_type_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    form = EquipmentTypeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_equipment_type(ctx, **form.cleaned_data)
        except (ValidationError, IntegrityError):
            form.add_error(None, "Ya existe un tipo con ese código o nombre.")
        else:
            messages.success(request, "Tipo de equipo creado.")
            return redirect("equipment:create")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nuevo tipo de equipo"},
    )


@login_required(login_url="platform_login")
def equipment_detail(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    today = date.today()
    tab = request.GET.get("tab", "technical")
    documents = list(
        scoped_qs(ctx, EquipmentDocument).filter(equipment=equipment).order_by("expires_on", "name")
    )
    project_links = list(
        scoped_qs(ctx, EquipmentProject)
        .filter(equipment=equipment)
        .select_related("project")
        .order_by("-started_on")
    )
    assignments = list(
        scoped_qs(ctx, EquipmentOperatorAssignment)
        .filter(equipment=equipment)
        .select_related("operator", "project")
        .order_by("-valid_from")
    )
    usage_records = list(
        visible_usage(ctx)
        .filter(equipment=equipment)
        .select_related("project", "operator")
        .order_by("-usage_date", "-start_time")[:50]
    )
    current_project = next((link for link in project_links if link.ended_on is None), None)
    current_operator = next(
        (
            item
            for item in assignments
            if item.valid_from <= today and (item.valid_until is None or item.valid_until >= today)
        ),
        None,
    )
    upcoming = [
        document
        for document in documents
        if document.expires_on and today <= document.expires_on <= today + timedelta(days=60)
    ]
    history = scoped_qs(ctx, AuditEvent).filter(
        Q(target=f"equipment:{equipment.pk}") | Q(metadata__equipment=str(equipment.pk))
    )[:50]
    return _render(
        request,
        ctx,
        "suno/equipment/detail.html",
        {
            "equipment": equipment,
            "tab": tab,
            "documents": documents,
            "project_links": project_links,
            "assignments": assignments,
            "usage_records": usage_records,
            "current_project": current_project,
            "current_operator": current_operator,
            "upcoming": upcoming,
            "history": history,
            "today": today,
            "can_edit": _can(ctx, equipment, Capability.UPDATE),
            "can_assign": _can(ctx, equipment, Capability.ASSIGN),
            "can_schedule": _can(ctx, equipment, Capability.SCHEDULE),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def equipment_edit(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can(ctx, equipment, Capability.UPDATE):
        raise PermissionDenied
    initial = {
        field: getattr(equipment, field)
        for field in (
            "equipment_type",
            "code",
            "name",
            "manufacturer",
            "model",
            "serial_number",
            "license_plate",
            "manufacture_year",
            "ownership",
            "owner_name",
            "status",
            "specifications",
            "notes",
        )
    }
    form = EquipmentForm(ctx, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        try:
            update_equipment(ctx, equipment.pk, **data)
        except (ValidationError, IntegrityError):
            form.add_error(None, "No fue posible guardar la ficha. Verifica los valores únicos.")
        else:
            messages.success(request, "Ficha técnica actualizada.")
            return redirect("equipment:detail", equipment.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Editar ficha", "equipment": equipment},
    )


def _related_form(
    request: HttpRequest,
    equipment_id: UUID,
    *,
    form_class: type[Any],
    title: str,
    capability: Capability,
    submit: Callable[[RequestContext, Equipment, dict[str, Any]], object],
) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can(ctx, equipment, capability):
        raise PermissionDenied
    if form_class in {EquipmentProjectForm, EquipmentOperatorForm}:
        form = form_class(ctx, request.POST or None)
    else:
        form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit(ctx, equipment, form.cleaned_data)
        except (ValidationError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                _message_validation(request, exc)
            else:
                messages.error(request, "El registro entra en conflicto con otro vigente.")
        else:
            messages.success(request, "Información agregada.")
            return redirect("equipment:detail", equipment.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": title, "equipment": equipment},
    )


def _submit_document(
    ctx: RequestContext, equipment: Equipment, data: dict[str, Any]
) -> object:
    return add_document(ctx, equipment_id=equipment.pk, **data)


def _submit_project(
    ctx: RequestContext, equipment: Equipment, data: dict[str, Any]
) -> object:
    values = data.copy()
    project = values.pop("project")
    return link_project(ctx, equipment_id=equipment.pk, project_id=project.pk, **values)


def _submit_operator(
    ctx: RequestContext, equipment: Equipment, data: dict[str, Any]
) -> object:
    values = data.copy()
    operator = values.pop("operator")
    project = values.pop("project")
    return assign_operator(
        ctx,
        equipment_id=equipment.pk,
        operator_id=operator.user_id,
        project_id=project.pk,
        **values,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def document_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    return _related_form(
        request,
        equipment_id,
        form_class=EquipmentDocumentForm,
        title="Agregar documento",
        capability=Capability.UPDATE,
        submit=_submit_document,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def project_link_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    return _related_form(
        request,
        equipment_id,
        form_class=EquipmentProjectForm,
        title="Vincular proyecto",
        capability=Capability.ASSIGN,
        submit=_submit_project,
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def operator_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    return _related_form(
        request,
        equipment_id,
        form_class=EquipmentOperatorForm,
        title="Asignar operador",
        capability=Capability.ASSIGN,
        submit=_submit_operator,
    )

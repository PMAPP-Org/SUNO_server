from __future__ import annotations

from base64 import b64encode
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import urlencode
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from suno.core.access import Capability, Section, SectionRole
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import (
    AppUser,
    AuditEvent,
    Equipment,
    EquipmentDocument,
    EquipmentMeterReading,
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentRate,
    EquipmentType,
    EquipmentUsageStatus,
    Membership,
    Project,
)
from suno.core.navigation import platform_navigation
from suno.core.scoping import get_organization, scoped_qs
from suno.equipment.charts import (
    CURRENCY,
    build_bullets,
    build_combo,
    build_hbars,
    donut_gradient,
    month_label,
    segments,
    short_money,
)
from suno.equipment.datasheet import (
    create_department,
    define_field,
    equipment_alerts,
    field_values_for,
    latest_meter_readings,
    record_meter_reading,
    set_field_value,
)
from suno.equipment.forms import (
    DepartmentForm,
    EquipmentCollectionForm,
    EquipmentDocumentForm,
    EquipmentForm,
    EquipmentOperatorForm,
    EquipmentProjectForm,
    EquipmentRateForm,
    EquipmentTypeForm,
    EquipmentUsageForm,
    FieldDefinitionForm,
    FieldValueForm,
    MeterReadingForm,
)
from suno.equipment.inspections import is_operator
from suno.equipment.maintenance import (
    maintenance_kpis,
    plan_status,
    preventive_history,
    visible_orders,
    visible_plans,
)
from suno.equipment.qr import (
    equipment_payload,
    parse_identifier,
    render_png,
    render_svg,
)
from suno.equipment.services import (
    add_document,
    assign_operator,
    can_view_costs,
    create_equipment,
    create_equipment_collection,
    create_equipment_type,
    create_equipment_usage,
    equipment_cost_summary,
    equipment_kpis,
    get_visible_collection,
    get_visible_equipment,
    link_project,
    recalculate_usage_costs,
    set_equipment_rate,
    update_equipment,
    update_equipment_collection,
    visible_collections,
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
        ctx.has_capability(Section.EQUIPMENT, capability, project_id) for project_id in project_ids
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


def _with_current_projects(ctx: RequestContext, queryset: Any) -> Any:
    return queryset.prefetch_related(
        Prefetch(
            "project_links",
            queryset=(
                scoped_qs(ctx, EquipmentProject)
                .filter(ended_on__isnull=True)
                .select_related("project")
                .order_by("-started_on")
            ),
            to_attr="current_project_links",
        )
    )


def _equipment_items(page: Any) -> list[dict[str, Any]]:
    return [
        {
            "equipment": equipment,
            "project": next(iter(equipment.current_project_links), None),
        }
        for equipment in page.object_list
    ]


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

    page = Paginator(_with_current_projects(ctx, queryset.order_by("code")), 25).get_page(
        request.GET.get("page")
    )
    collections = list(
        visible_collections(ctx)
        .prefetch_related("equipment_types")
        .annotate(
            equipment_count=Count(
                "equipment_types__equipment",
                filter=Q(equipment_types__equipment__in=visible_equipment(ctx)),
                distinct=True,
            )
        )
        .order_by("created_at", "name")
    )
    return _render(
        request,
        ctx,
        "suno/equipment/list.html",
        {
            "items": _equipment_items(page),
            "page": page,
            "collections": collections,
            "query": query,
            "selected_type": selected_type,
            "selected_status": selected_status,
            "selected_project": selected_project,
            "types": scoped_qs(ctx, EquipmentType).filter(active=True).order_by("name"),
            "statuses": Equipment._meta.get_field("status").choices,
            "projects": scoped_qs(ctx, Project).order_by("name"),
            "can_create": ctx.has_capability(Section.EQUIPMENT, Capability.CREATE),
            "can_update_collections": ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE),
            "can_view_costs": can_view_costs(ctx),
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
            "can_view_costs": can_view_costs(ctx),
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
@require_http_methods(["GET", "POST"])
def collection_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    form = EquipmentCollectionForm(ctx, request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        equipment_types = data.pop("equipment_types")
        try:
            collection = create_equipment_collection(
                ctx,
                equipment_type_ids=[item.pk for item in equipment_types],
                **data,
            )
        except (ValidationError, IntegrityError):
            form.add_error(None, "Ya existe una colección con ese nombre.")
        else:
            messages.success(request, "Colección creada.")
            return redirect("equipment:collection_detail", collection.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/collection_form.html",
        {"form": form, "title": "Nueva colección"},
    )


@login_required(login_url="platform_login")
def collection_detail(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    collection = get_visible_collection(ctx, collection_id)
    type_ids = collection.equipment_types.values_list("pk", flat=True)
    queryset = (
        visible_equipment(ctx)
        .filter(equipment_type_id__in=type_ids)
        .select_related("equipment_type")
        .order_by("code")
    )
    page = Paginator(_with_current_projects(ctx, queryset), 25).get_page(request.GET.get("page"))
    return _render(
        request,
        ctx,
        "suno/equipment/collection_detail.html",
        {
            "collection": collection,
            "items": _equipment_items(page),
            "page": page,
            "can_edit": ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def collection_edit(request: HttpRequest, collection_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    collection = get_visible_collection(ctx, collection_id)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE):
        raise PermissionDenied
    initial = {
        "name": collection.name,
        "description": collection.description,
        "equipment_types": list(collection.equipment_types.values_list("pk", flat=True)),
    }
    form = EquipmentCollectionForm(ctx, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data.copy()
        equipment_types = data.pop("equipment_types")
        try:
            collection = update_equipment_collection(
                ctx,
                collection.pk,
                equipment_type_ids=[item.pk for item in equipment_types],
                **data,
            )
        except (ValidationError, IntegrityError):
            form.add_error(None, "No fue posible guardar la colección.")
        else:
            messages.success(request, "Colección actualizada.")
            return redirect("equipment:collection_detail", collection.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/collection_form.html",
        {"form": form, "title": "Editar colección", "collection": collection},
    )


def _maintenance_context(ctx: RequestContext, equipment: Equipment, today: date) -> dict[str, Any]:
    kpis = maintenance_kpis(ctx, equipment, since=today - timedelta(days=90), until=today)
    return {
        "plans": [
            plan_status(ctx, plan, today=today)
            for plan in visible_plans(ctx).filter(equipment=equipment, active=True)
        ],
        "orders": list(visible_orders(ctx).filter(equipment=equipment).select_related("plan")[:15]),
        "history": preventive_history(ctx, equipment, limit=5),
        "kpis": kpis,
        "can_manage": (
            ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE) and not is_operator(ctx)
        ),
    }


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
    show_costs = can_view_costs(ctx)
    rates = (
        list(scoped_qs(ctx, EquipmentRate).filter(equipment=equipment).order_by("-valid_from"))
        if show_costs
        else []
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
    meter_readings = list(
        scoped_qs(ctx, EquipmentMeterReading)
        .filter(equipment=equipment)
        .select_related("recorded_by")
        .order_by("-read_at", "-created_at")[:30]
    )
    return _render(
        request,
        ctx,
        "suno/equipment/detail.html",
        {
            "equipment": equipment,
            "tab": tab,
            "maintenance": _maintenance_context(ctx, equipment, today)
            if tab == "maintenance"
            else {},
            "meters": latest_meter_readings(ctx, equipment),
            "meter_readings": meter_readings,
            "field_values": field_values_for(ctx, equipment),
            "alerts": equipment_alerts(ctx, equipment, today=today),
            "can_define_fields": ctx.has_capability(Section.EQUIPMENT, Capability.CREATE),
            "documents": documents,
            "project_links": project_links,
            "assignments": assignments,
            "usage_records": usage_records,
            "current_project": current_project,
            "current_operator": current_operator,
            "upcoming": upcoming,
            "history": history,
            "today": today,
            "can_view_costs": show_costs,
            "kpis": equipment_kpis(ctx, equipment, today=today),
            "rates": rates,
            "can_set_rates": show_costs
            and ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE),
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
            "department",
            "control_scheme",
            "primary_meter_unit",
            "secondary_meter_unit",
            "work_source",
            "nominal_fuel_yield",
            "fuel_tolerance_pct",
            "acquisition_value",
            "acquisition_date",
            "accumulated_depreciation",
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
    redirect_tab: str = "",
) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can(ctx, equipment, capability):
        raise PermissionDenied
    if form_class in {EquipmentProjectForm, EquipmentOperatorForm}:
        form = form_class(ctx, request.POST or None)
    elif form_class in {MeterReadingForm, FieldValueForm}:
        form = form_class(ctx, equipment, request.POST or None)
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
            target = reverse("equipment:detail", args=[equipment.pk])
            return redirect(f"{target}?tab={redirect_tab}" if redirect_tab else target)
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": title, "equipment": equipment},
    )


def _submit_document(ctx: RequestContext, equipment: Equipment, data: dict[str, Any]) -> object:
    return add_document(ctx, equipment_id=equipment.pk, **data)


def _submit_project(ctx: RequestContext, equipment: Equipment, data: dict[str, Any]) -> object:
    values = data.copy()
    project = values.pop("project")
    return link_project(ctx, equipment_id=equipment.pk, project_id=project.pk, **values)


def _submit_operator(ctx: RequestContext, equipment: Equipment, data: dict[str, Any]) -> object:
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


MAX_LABELS = 60


def _label_context(equipment: Equipment) -> dict[str, Any]:
    payload = equipment_payload(equipment)
    svg = render_svg(payload, scale=8, title=f"QR de {equipment.code}")
    return {
        "equipment": equipment,
        "payload": payload,
        # Un data URI evita insertar marcado sin escapar en la plantilla.
        "qr_data_uri": f"data:image/svg+xml;base64,{b64encode(svg).decode('ascii')}",
    }


@login_required(login_url="platform_login")
def equipment_identify(request: HttpRequest) -> HttpResponse:
    """Resuelve un QR escaneado o una búsqueda manual (código, serie, placa) a la ficha."""

    ctx = _ctx(request)
    raw = request.GET.get("q", "").strip()
    if not raw:
        return redirect("equipment:list")
    queryset = visible_equipment(ctx)
    identifier = parse_identifier(raw)
    if identifier is not None:
        match = queryset.filter(pk=identifier).first()
        if match is None:
            messages.error(request, "El código no corresponde a un equipo al que tengas acceso.")
            return redirect("equipment:list")
        return redirect("equipment:detail", equipment_id=match.pk)
    exact = list(
        queryset.filter(
            Q(code__iexact=raw) | Q(serial_number__iexact=raw) | Q(license_plate__iexact=raw)
        )[:2]
    )
    if len(exact) == 1:
        return redirect("equipment:detail", equipment_id=exact[0].pk)
    return redirect(f"{reverse('equipment:list')}?{urlencode({'q': raw})}")


@login_required(login_url="platform_login")
def equipment_qr(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    return _render(
        request,
        ctx,
        "suno/equipment/qr_label.html",
        {"labels": [_label_context(equipment)], "single": True},
    )


@login_required(login_url="platform_login")
def equipment_qr_image(request: HttpRequest, equipment_id: UUID, fmt: str) -> HttpResponse:
    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    payload = equipment_payload(equipment)
    if fmt not in ("svg", "png"):
        raise Http404
    if fmt == "png":
        body, content_type = render_png(payload), "image/png"
    else:
        body = render_svg(payload, title=f"QR de {equipment.code}")
        content_type = "image/svg+xml"
    response = HttpResponse(body, content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    if request.GET.get("download"):
        name = slugify(equipment.code) or "equipo"
        response["Content-Disposition"] = f'attachment; filename="qr-{name}.{fmt}"'
    return response


@login_required(login_url="platform_login")
def equipment_labels(request: HttpRequest) -> HttpResponse:
    """Hoja imprimible con las etiquetas QR de los equipos indicados en `ids`."""

    ctx = _ctx(request)
    ids: list[UUID] = []
    for value in request.GET.getlist("ids"):
        try:
            ids.append(UUID(value))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))[:MAX_LABELS]
    equipment = visible_equipment(ctx).select_related("equipment_type").filter(pk__in=ids)
    return _render(
        request,
        ctx,
        "suno/equipment/qr_label.html",
        {"labels": [_label_context(item) for item in equipment.order_by("code")], "single": False},
    )


def _submit_rate(ctx: RequestContext, equipment: Equipment, data: dict[str, Any]) -> object:
    return set_equipment_rate(ctx, equipment_id=equipment.pk, **data)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def rate_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    if not can_view_costs(ctx) or not ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE):
        raise PermissionDenied
    return _related_form(
        request,
        equipment_id,
        form_class=EquipmentRateForm,
        title="Registrar tarifa por hora",
        capability=Capability.UPDATE,
        submit=_submit_rate,
        redirect_tab="rates",
    )


@login_required(login_url="platform_login")
def equipment_costs(request: HttpRequest) -> HttpResponse:
    """Horas y costo de equipos por equipo y por partida, contra el presupuesto vigente."""

    ctx = _ctx(request)
    if not can_view_costs(ctx):
        raise PermissionDenied
    selected_project = request.GET.get("project", "")
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    include_scheduled = request.GET.get("scheduled") == "1"
    try:
        project_id = UUID(selected_project) if selected_project else None
    except ValueError:
        project_id = None
    summary = equipment_cost_summary(
        ctx,
        project_id=project_id,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
        include_scheduled=include_scheduled,
    )
    top = summary.rows[:8]
    hbars = build_hbars(
        [(r.name, r.code, r.cost, r.hours, r.equipment_id) for r in top], summary.total_cost
    )
    combo = build_combo(
        [(month_label(p.month), p.cost, p.scheduled_cost, p.hours) for p in summary.monthly]
    )
    parts = segments(
        [
            ("Propiedad", summary.breakdown.ownership),
            ("Operación", summary.breakdown.operation),
            ("Mantenimiento", summary.breakdown.maintenance),
            ("Operador", summary.breakdown.labor),
        ]
    )
    bullets = build_bullets(
        [(r.code, r.description, r.imputed, r.budgeted) for r in summary.budget_rows]
    )
    project_name = (
        scoped_qs(ctx, Project).filter(pk=project_id).values_list("name", flat=True).first() or ""
        if project_id
        else ""
    )
    period_label = (
        f"{month_label(summary.monthly[0].month)} a {month_label(summary.monthly[-1].month)}"
        if summary.monthly
        else "sin registros"
    )
    if date_from or date_to:
        period_label += f" (filtro: {date_from or 'inicio'} a {date_to or 'hoy'})"
    return _render(
        request,
        ctx,
        "suno/equipment/costs.html",
        {
            "summary": summary,
            "hbars": hbars,
            "bullets": bullets,
            "currency": CURRENCY,
            "total_label": short_money(summary.total_cost),
            "scope_label": f"Proyecto: {project_name}" if project_name else "Todos los proyectos",
            "period_label": period_label,
            "status_label": (
                "Incluye usos completados, en curso y programados; excluye cancelados."
                if include_scheduled
                else "Incluye usos completados o en curso; excluye programados y cancelados."
            ),
            "combo": combo,
            "scheduled_total": sum((p.scheduled_cost for p in summary.monthly), Decimal("0")),
            "donut_parts": parts,
            "donut_gradient": donut_gradient(parts),
            "average_cost_per_hour": (
                (summary.total_cost / summary.total_hours).quantize(Decimal("0.01"))
                if summary.total_hours
                else None
            ),
            "projects": scoped_qs(ctx, Project).order_by("name"),
            "selected_project": selected_project,
            "date_from": date_from,
            "date_to": date_to,
            "include_scheduled": include_scheduled,
            "can_recalculate": ctx.has_capability(Section.EQUIPMENT, Capability.UPDATE),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["POST"])
def costs_recalculate(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not can_view_costs(ctx):
        raise PermissionDenied
    updated = recalculate_usage_costs(ctx)
    if updated:
        messages.success(request, f"Se costearon {updated} uso(s) que no tenían tarifa.")
    else:
        messages.info(request, "No había usos pendientes con tarifa aplicable.")
    return redirect("equipment:costs")


def _submit_meter_reading(
    ctx: RequestContext, equipment: Equipment, data: dict[str, Any]
) -> object:
    values = {key: value for key, value in data.items() if value not in (None, "")}
    return record_meter_reading(ctx, equipment_id=equipment.pk, **values)


def _submit_field_value(ctx: RequestContext, equipment: Equipment, data: dict[str, Any]) -> object:
    return set_field_value(
        ctx,
        equipment_id=equipment.pk,
        definition_id=data["definition"].pk,
        value=data.get("value"),
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def meter_reading_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    return _related_form(
        request,
        equipment_id,
        form_class=MeterReadingForm,
        title="Registrar lectura de medidor",
        capability=Capability.UPDATE,
        submit=_submit_meter_reading,
        redirect_tab="meters",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def field_value_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    return _related_form(
        request,
        equipment_id,
        form_class=FieldValueForm,
        title="Registrar valor de campo adicional",
        capability=Capability.UPDATE,
        submit=_submit_field_value,
        redirect_tab="meters",
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def department_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_department(ctx, **form.cleaned_data)
        except (ValidationError, IntegrityError):
            form.add_error(None, "Ya existe un departamento con ese código o nombre.")
        else:
            messages.success(request, "Departamento de maquinaria creado.")
            return redirect("equipment:create")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nuevo departamento de maquinaria"},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def field_definition_create(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    if not ctx.has_capability(Section.EQUIPMENT, Capability.CREATE):
        raise PermissionDenied
    form = FieldDefinitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            define_field(ctx, **form.cleaned_data)
        except (ValidationError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                _message_validation(request, exc)
            form.add_error(None, "No fue posible crear el campo. Verifica el nombre y el tipo.")
        else:
            messages.success(request, "Campo adicional creado.")
            return redirect("equipment:list")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Nuevo campo adicional"},
    )

"""Servicios autorizados del catálogo y ficha técnica de equipos."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, QuerySet, Sum
from django.db.models.functions import TruncMonth
from django.http import Http404

from visitacampo.core import audit
from visitacampo.core.access import Capability, Section, SectionRole
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Equipment,
    EquipmentCollection,
    EquipmentCollectionType,
    EquipmentDocument,
    EquipmentMeterUnit,
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentRate,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Membership,
    Project,
)
from visitacampo.core.scoping import scoped_qs
from visitacampo.equipment.costing import (
    pct_of,
    usage_cost,
    usage_hours,
    utilization_pct,
)

__all__ = [
    "CostBreakdown",
    "MonthlyCost",
    "EquipmentKpis",
    "ProjectCostSummary",
    "equipment_kpis",
    "add_document",
    "can_view_costs",
    "equipment_cost_summary",
    "recalculate_usage_costs",
    "set_equipment_rate",
    "assign_operator",
    "create_equipment_collection",
    "create_equipment",
    "create_equipment_type",
    "create_equipment_usage",
    "get_visible_usage",
    "get_visible_collection",
    "get_visible_equipment",
    "link_project",
    "update_equipment",
    "update_equipment_collection",
    "visible_collections",
    "visible_equipment",
    "visible_usage",
]

_USAGE_LOCK_NAMESPACE = "equipment-usage"


def visible_collections(ctx: RequestContext) -> QuerySet[EquipmentCollection]:
    """Colecciones activas de la organización visibles en el módulo de equipos."""

    queryset = scoped_qs(ctx, EquipmentCollection).filter(active=True)
    if not ctx.has_section_access(Section.EQUIPMENT):
        return queryset.none()
    return queryset


def get_visible_collection(ctx: RequestContext, collection_id: UUID) -> EquipmentCollection:
    try:
        return visible_collections(ctx).prefetch_related("equipment_types").get(pk=collection_id)
    except EquipmentCollection.DoesNotExist as exc:
        raise Http404 from exc


def visible_equipment(ctx: RequestContext, *, on_date: date | None = None) -> QuerySet[Equipment]:
    """Equipos visibles según alcance y, para operadores, asignación vigente."""

    queryset = scoped_qs(ctx, Equipment)
    needs_distinct = False
    access = ctx.access_for(Section.EQUIPMENT)
    if access is None or not ctx.has_section_access(Section.EQUIPMENT):
        return queryset.none()
    if access.scope != "organization":
        queryset = queryset.filter(
            project_links__project_id__in=access.project_ids,
            project_links__ended_on__isnull=True,
        )
        needs_distinct = True
    if access.role == SectionRole.EQUIPMENT_OPERATOR:
        current = on_date or date.today()
        queryset = queryset.filter(
            operator_assignments__operator_id=ctx.user_id,
            operator_assignments__valid_from__lte=current,
        ).filter(
            Q(operator_assignments__valid_until__isnull=True)
            | Q(operator_assignments__valid_until__gte=current)
        )
        needs_distinct = True
    return queryset.distinct() if needs_distinct else queryset


def get_visible_equipment(ctx: RequestContext, equipment_id: UUID) -> Equipment:
    try:
        return visible_equipment(ctx).get(pk=equipment_id)
    except Equipment.DoesNotExist as exc:
        raise Http404 from exc


def visible_usage(ctx: RequestContext) -> QuerySet[EquipmentUsage]:
    """Registros visibles por alcance y, para operadores, solo los propios."""

    queryset = scoped_qs(ctx, EquipmentUsage).filter(equipment__in=visible_equipment(ctx))
    access = ctx.access_for(Section.EQUIPMENT)
    if access is not None and access.role == SectionRole.EQUIPMENT_OPERATOR:
        queryset = queryset.filter(operator_id=ctx.user_id)
    return queryset


def get_visible_usage(ctx: RequestContext, usage_id: UUID) -> EquipmentUsage:
    try:
        return visible_usage(ctx).get(pk=usage_id)
    except EquipmentUsage.DoesNotExist as exc:
        raise Http404 from exc


def _require(ctx: RequestContext, capability: Capability, project_id: UUID | None = None) -> None:
    if not ctx.has_capability(Section.EQUIPMENT, capability, project_id):
        raise PermissionDenied("No tienes permiso para esta operación de equipos.")


def _require_for_equipment(
    ctx: RequestContext,
    capability: Capability,
    equipment: Equipment,
) -> None:
    if ctx.has_capability(Section.EQUIPMENT, capability):
        return
    project_ids = equipment.project_links.filter(ended_on__isnull=True).values_list(
        "project_id", flat=True
    )
    if any(
        ctx.has_capability(Section.EQUIPMENT, capability, project_id) for project_id in project_ids
    ):
        return
    raise PermissionDenied("No tienes permiso para esta operación de equipos.")


def _acquire_usage_locks(*resource_ids: UUID) -> None:
    """Serializa reservas del mismo equipo u operador dentro de la transacción."""

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Los locks de uso requieren una transacción abierta.")
    keys = sorted(f"{_USAGE_LOCK_NAMESPACE}:{resource_id}" for resource_id in resource_ids)
    with connection.cursor() as cursor:
        for key in keys:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [key])


def _check_usage_context(
    ctx: RequestContext,
    *,
    equipment: Equipment,
    project: Project,
    operator: AppUser,
    usage_date: date,
) -> None:
    linked = (
        scoped_qs(ctx, EquipmentProject)
        .filter(
            equipment=equipment,
            project=project,
            started_on__lte=usage_date,
        )
        .filter(Q(ended_on__isnull=True) | Q(ended_on__gte=usage_date))
        .exists()
    )
    if not linked:
        raise ValidationError("El equipo no está vinculado al proyecto en esa fecha.")

    operator_ctx = resolve_request_context(Principal(user_id=operator.pk, org_id=ctx.org_id))
    operator_access = operator_ctx.access_for(Section.EQUIPMENT)
    if (
        operator_access is None
        or operator_access.role != SectionRole.EQUIPMENT_OPERATOR
        or not operator_access.covers_project(project.pk)
    ):
        raise ValidationError("El usuario no tiene rol de operador para el proyecto.")

    assigned = (
        scoped_qs(ctx, EquipmentOperatorAssignment)
        .filter(
            equipment=equipment,
            project=project,
            operator=operator,
            valid_from__lte=usage_date,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=usage_date))
        .exists()
    )
    if not assigned:
        raise ValidationError("El operador no está asignado al equipo en esa fecha.")


@transaction.atomic
def create_equipment_usage(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    project_id: UUID,
    operator_id: UUID,
    usage_date: date,
    start_time: Any,
    end_time: Any,
    activity: str,
    status: str = "scheduled",
    budget_item_code: str = "",
    **details: Any,
) -> EquipmentUsage:
    """Registra una jornada sin solapar equipo ni operador.

    Si el equipo tiene tarifa vigente en la fecha, guarda las horas, la tarifa y el
    costo; `budget_item_code` imputa el costo a una partida del presupuesto del proyecto.
    """

    _require(ctx, Capability.SCHEDULE, project_id)
    try:
        equipment = visible_equipment(ctx).get(pk=equipment_id)
        project = scoped_qs(ctx, Project).get(pk=project_id)
        operator = scoped_qs(ctx, Membership).select_related("user").get(user_id=operator_id).user
    except (Equipment.DoesNotExist, Project.DoesNotExist, Membership.DoesNotExist) as exc:
        raise Http404 from exc

    _acquire_usage_locks(equipment.pk, operator.pk)
    _check_usage_context(
        ctx,
        equipment=equipment,
        project=project,
        operator=operator,
        usage_date=usage_date,
    )

    if status != EquipmentUsageStatus.CANCELLED:
        conflict = (
            scoped_qs(ctx, EquipmentUsage)
            .filter(usage_date=usage_date)
            .exclude(status=EquipmentUsageStatus.CANCELLED)
            .filter(Q(equipment=equipment) | Q(operator=operator))
            .filter(start_time__lt=end_time, end_time__gt=start_time)
            .exists()
        )
        if conflict:
            raise ValidationError("El equipo o el operador ya tiene otro uso en ese horario.")

    budget_item = _resolve_budget_item(ctx, project, budget_item_code)
    hours = usage_hours(start_time, end_time)
    rate = _rate_on(ctx, equipment.pk, usage_date)
    usage = EquipmentUsage(
        org_id=ctx.org_id,
        budget_item=budget_item,
        rate=rate,
        billable_hours=hours,
        hourly_rate=rate.hourly_rate if rate else None,
        cost=usage_cost(hours, rate.hourly_rate) if rate else None,
        equipment=equipment,
        project=project,
        operator=operator,
        usage_date=usage_date,
        start_time=start_time,
        end_time=end_time,
        activity=activity.strip(),
        status=status,
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
        **details,
    )
    usage.full_clean()
    usage.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_USAGE_CREATED,
        target=f"equipment-usage:{usage.pk}",
        metadata={
            "equipment": str(equipment.pk),
            "project": str(project.pk),
            "operator": str(operator.pk),
            "usage_date": usage_date.isoformat(),
            **({"budget_item": budget_item.code} if budget_item else {}),
        },
    )
    return usage


@transaction.atomic
def create_equipment_type(
    ctx: RequestContext,
    *,
    code: str,
    name: str,
    description: str = "",
    preoperational_template_id: UUID | None = None,
) -> EquipmentType:
    _require(ctx, Capability.CREATE)
    equipment_type = EquipmentType(
        org_id=ctx.org_id,
        code=code.strip(),
        name=name.strip(),
        description=description.strip(),
        preoperational_template_id=preoperational_template_id,
    )
    equipment_type.full_clean()
    equipment_type.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_TYPE_CREATED,
        target=f"equipment-type:{equipment_type.pk}",
    )
    return equipment_type


def _collection_types(
    ctx: RequestContext, equipment_type_ids: Iterable[UUID]
) -> list[EquipmentType]:
    ids = list(dict.fromkeys(equipment_type_ids))
    equipment_types = list(
        scoped_qs(ctx, EquipmentType).filter(pk__in=ids, active=True).order_by("name")
    )
    if len(equipment_types) != len(ids):
        raise ValidationError("Una o más tipologías no existen o están inactivas.")
    return equipment_types


def _replace_collection_types(
    ctx: RequestContext, collection: EquipmentCollection, equipment_types: list[EquipmentType]
) -> None:
    collection.type_links.all().delete()
    links = [
        EquipmentCollectionType(
            org_id=collection.org_id,
            collection=collection,
            equipment_type=equipment_type,
        )
        for equipment_type in equipment_types
    ]
    for link in links:
        link.full_clean()
    scoped_qs(ctx, EquipmentCollectionType).bulk_create(links)


@transaction.atomic
def create_equipment_collection(
    ctx: RequestContext,
    *,
    name: str,
    description: str = "",
    equipment_type_ids: Iterable[UUID] = (),
) -> EquipmentCollection:
    _require(ctx, Capability.CREATE)
    equipment_types = _collection_types(ctx, equipment_type_ids)
    collection = EquipmentCollection(
        org_id=ctx.org_id,
        name=name.strip(),
        description=description.strip(),
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
    )
    collection.full_clean()
    collection.save()
    _replace_collection_types(ctx, collection, equipment_types)
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_COLLECTION_CREATED,
        target=f"equipment-collection:{collection.pk}",
        metadata={"equipment_types": [str(item.pk) for item in equipment_types]},
    )
    return collection


@transaction.atomic
def update_equipment_collection(
    ctx: RequestContext,
    collection_id: UUID,
    *,
    name: str,
    description: str = "",
    equipment_type_ids: Iterable[UUID] = (),
) -> EquipmentCollection:
    try:
        collection = visible_collections(ctx).select_for_update().get(pk=collection_id)
    except EquipmentCollection.DoesNotExist as exc:
        raise Http404 from exc
    _require(ctx, Capability.UPDATE)
    equipment_types = _collection_types(ctx, equipment_type_ids)
    before = {
        "name": collection.name,
        "description": collection.description,
        "equipment_types": [
            str(pk) for pk in collection.equipment_types.values_list("pk", flat=True)
        ],
    }
    collection.name = name.strip()
    collection.description = description.strip()
    collection.updated_by_id = ctx.user_id
    collection.full_clean()
    collection.save()
    _replace_collection_types(ctx, collection, equipment_types)
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_COLLECTION_UPDATED,
        target=f"equipment-collection:{collection.pk}",
        metadata={
            "before": before,
            "equipment_types": [str(item.pk) for item in equipment_types],
        },
    )
    return collection


@transaction.atomic
def create_equipment(
    ctx: RequestContext,
    *,
    equipment_type_id: UUID,
    code: str,
    name: str,
    **details: Any,
) -> Equipment:
    _require(ctx, Capability.CREATE)
    try:
        equipment_type = scoped_qs(ctx, EquipmentType).get(pk=equipment_type_id, active=True)
    except EquipmentType.DoesNotExist as exc:
        raise ValidationError("El tipo de equipo no existe o está inactivo.") from exc
    equipment = Equipment(
        org_id=ctx.org_id,
        equipment_type=equipment_type,
        code=code.strip(),
        name=name.strip(),
        created_by_id=ctx.user_id,
        updated_by_id=ctx.user_id,
        **details,
    )
    equipment.full_clean()
    equipment.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_CREATED,
        target=f"equipment:{equipment.pk}",
        metadata={"code": equipment.code},
    )
    return equipment


@transaction.atomic
def update_equipment(ctx: RequestContext, equipment_id: UUID, **changes: Any) -> Equipment:
    try:
        equipment = visible_equipment(ctx).select_for_update().get(pk=equipment_id)
    except Equipment.DoesNotExist as exc:
        raise Http404 from exc
    _require_for_equipment(ctx, Capability.UPDATE, equipment)
    allowed = {
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
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError(f"Campos no editables: {', '.join(sorted(unknown))}.")
    before = {field: getattr(equipment, field) for field in changes}
    for field, value in changes.items():
        setattr(equipment, field, value)
    equipment.updated_by_id = ctx.user_id
    equipment.full_clean()
    equipment.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_UPDATED,
        target=f"equipment:{equipment.pk}",
        metadata={"before": before, "fields": sorted(changes)},
    )
    return equipment


@transaction.atomic
def link_project(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    project_id: UUID,
    started_on: date,
    ended_on: date | None = None,
) -> EquipmentProject:
    _require(ctx, Capability.ASSIGN, project_id)
    try:
        equipment = visible_equipment(ctx).select_for_update().get(pk=equipment_id)
        project = scoped_qs(ctx, Project).get(pk=project_id)
    except (Equipment.DoesNotExist, Project.DoesNotExist) as exc:
        raise Http404 from exc
    end_filter = Q(started_on__lte=ended_on) if ended_on else Q()
    overlap = (
        scoped_qs(ctx, EquipmentProject)
        .filter(equipment=equipment)
        .filter(end_filter)
        .filter(Q(ended_on__isnull=True) | Q(ended_on__gte=started_on))
        .exists()
    )
    if overlap:
        raise ValidationError("El equipo ya está vinculado a un proyecto en ese periodo.")
    link = EquipmentProject(
        org_id=ctx.org_id,
        equipment=equipment,
        project=project,
        started_on=started_on,
        ended_on=ended_on,
        assigned_by_id=ctx.user_id,
    )
    link.full_clean()
    link.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_PROJECT_LINKED,
        target=f"equipment-project:{link.pk}",
        metadata={"equipment": str(equipment.pk), "project": str(project.pk)},
    )
    return link


@transaction.atomic
def assign_operator(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    operator_id: UUID,
    project_id: UUID,
    valid_from: date,
    valid_until: date | None = None,
) -> EquipmentOperatorAssignment:
    _require(ctx, Capability.ASSIGN, project_id)
    try:
        equipment = visible_equipment(ctx).select_for_update().get(pk=equipment_id)
        project = scoped_qs(ctx, Project).get(pk=project_id)
        operator = scoped_qs(ctx, Membership).select_related("user").get(user_id=operator_id).user
    except (Equipment.DoesNotExist, Project.DoesNotExist, Membership.DoesNotExist) as exc:
        raise Http404 from exc
    if (
        not scoped_qs(ctx, EquipmentProject)
        .filter(
            equipment=equipment,
            project=project,
            started_on__lte=valid_from,
        )
        .filter(Q(ended_on__isnull=True) | Q(ended_on__gte=valid_from))
        .exists()
    ):
        raise ValidationError("El equipo no está vinculado al proyecto en esa fecha.")

    operator_ctx = resolve_request_context(Principal(user_id=operator.pk, org_id=ctx.org_id))
    operator_access = operator_ctx.access_for(Section.EQUIPMENT)
    if (
        operator_access is None
        or operator_access.role != SectionRole.EQUIPMENT_OPERATOR
        or not operator_access.covers_project(project.pk)
    ):
        raise ValidationError("El usuario no tiene rol de operador para el proyecto.")

    end_filter = Q(valid_from__lte=valid_until) if valid_until else Q()
    overlap = (
        scoped_qs(ctx, EquipmentOperatorAssignment)
        .filter(equipment=equipment)
        .filter(end_filter)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=valid_from))
        .exists()
    )
    if overlap:
        raise ValidationError("La asignación se solapa con otra vigencia del operador.")

    assignment = EquipmentOperatorAssignment(
        org_id=ctx.org_id,
        equipment=equipment,
        operator=operator,
        project=project,
        valid_from=valid_from,
        valid_until=valid_until,
        assigned_by_id=ctx.user_id,
    )
    assignment.full_clean()
    assignment.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_OPERATOR_ASSIGNED,
        target=f"equipment-operator:{assignment.pk}",
        metadata={"equipment": str(equipment.pk), "operator": str(operator.pk)},
    )
    return assignment


@transaction.atomic
def add_document(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    document_type: str,
    name: str,
    number: str = "",
    issued_on: date | None = None,
    expires_on: date | None = None,
) -> EquipmentDocument:
    equipment = get_visible_equipment(ctx, equipment_id)
    _require_for_equipment(ctx, Capability.UPDATE, equipment)
    document = EquipmentDocument(
        org_id=ctx.org_id,
        equipment=equipment,
        document_type=document_type,
        name=name.strip(),
        number=number.strip(),
        issued_on=issued_on,
        expires_on=expires_on,
        created_by_id=ctx.user_id,
    )
    document.full_clean()
    document.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_DOCUMENT_ADDED,
        target=f"equipment-document:{document.pk}",
        metadata={"equipment": str(equipment.pk), "type": document.document_type},
    )
    return document


# ---------------------------------------------------------------------------
# Costeo por hora
# ---------------------------------------------------------------------------


def can_view_costs(ctx: RequestContext) -> bool:
    """Ver tarifas y costos exige `export` de Equipos a nivel organizacional."""

    return ctx.has_capability(Section.EQUIPMENT, Capability.EXPORT)


def _rate_on(ctx: RequestContext, equipment_id: UUID, on_date: date) -> EquipmentRate | None:
    return (
        scoped_qs(ctx, EquipmentRate)
        .filter(equipment_id=equipment_id, valid_from__lte=on_date)
        .order_by("-valid_from")
        .first()
    )


def _resolve_budget_item(ctx: RequestContext, project: Project, code: str) -> BudgetItem | None:
    code = code.strip()
    if not code:
        return None
    items = list(scoped_qs(ctx, BudgetItem).filter(budget__project=project, code__iexact=code)[:2])
    if not items:
        raise ValidationError(f"No existe la partida «{code}» en los presupuestos del proyecto.")
    if len(items) > 1:
        raise ValidationError(
            f"La partida «{code}» existe en más de un presupuesto del proyecto; "
            "usa un código único."
        )
    return items[0]


@transaction.atomic
def set_equipment_rate(
    ctx: RequestContext,
    *,
    equipment_id: UUID,
    valid_from: date,
    ownership_rate: Decimal = Decimal("0"),
    operation_rate: Decimal = Decimal("0"),
    maintenance_rate: Decimal = Decimal("0"),
    labor_rate: Decimal = Decimal("0"),
    notes: str = "",
) -> EquipmentRate:
    """Registra la tarifa por hora (suma de sus componentes) vigente desde `valid_from`.

    No edita las anteriores: el histórico es inmutable.
    """

    _require(ctx, Capability.UPDATE)
    equipment = get_visible_equipment(ctx, equipment_id)
    if scoped_qs(ctx, EquipmentRate).filter(equipment=equipment, valid_from=valid_from).exists():
        raise ValidationError("Ya existe una tarifa con esa fecha de vigencia; usa una posterior.")
    components = (ownership_rate, operation_rate, maintenance_rate, labor_rate)
    if any(value < 0 for value in components):
        raise ValidationError("Los componentes de la tarifa no pueden ser negativos.")
    rate = EquipmentRate(
        org_id=ctx.org_id,
        equipment=equipment,
        valid_from=valid_from,
        ownership_rate=ownership_rate,
        operation_rate=operation_rate,
        maintenance_rate=maintenance_rate,
        labor_rate=labor_rate,
        hourly_rate=sum(components, Decimal("0")),
        notes=notes.strip(),
        created_by_id=ctx.user_id,
    )
    rate.full_clean()
    rate.save()
    audit.record(
        ctx,
        action=AuditAction.EQUIPMENT_RATE_SET,
        target=f"equipment-rate:{rate.pk}",
        metadata={
            "equipment": str(equipment.pk),
            "valid_from": valid_from.isoformat(),
            "hourly_rate": str(rate.hourly_rate),
        },
    )
    return rate


@transaction.atomic
def recalculate_usage_costs(ctx: RequestContext, *, equipment_id: UUID | None = None) -> int:
    """Costea los usos que quedaron sin tarifa; no toca los ya costeados."""

    _require(ctx, Capability.UPDATE)
    pending = scoped_qs(ctx, EquipmentUsage).filter(cost__isnull=True)
    if equipment_id is not None:
        pending = pending.filter(equipment_id=equipment_id)
    updated = 0
    for usage in pending.select_for_update():
        rate = _rate_on(ctx, usage.equipment_id, usage.usage_date)
        if rate is None:
            continue
        hours = usage_hours(usage.start_time, usage.end_time)
        usage.rate = rate
        usage.billable_hours = hours
        usage.hourly_rate = rate.hourly_rate
        usage.cost = usage_cost(hours, rate.hourly_rate)
        usage.save(update_fields=["rate", "billable_hours", "hourly_rate", "cost", "updated_at"])
        updated += 1
    if updated:
        audit.record(
            ctx,
            action=AuditAction.EQUIPMENT_COSTS_RECALCULATED,
            target="equipment-usage:costs",
            metadata={"updated": updated},
        )
    return updated


@dataclass(frozen=True)
class EquipmentCostRow:
    equipment_id: UUID
    code: str
    name: str
    hours: Decimal
    cost: Decimal
    usages: int
    unpriced: int


@dataclass(frozen=True)
class BudgetItemCostRow:
    code: str
    description: str
    imputed: Decimal
    budgeted: Decimal | None
    pct_consumed: Decimal | None


@dataclass(frozen=True)
class MonthlyCost:
    month: date
    hours: Decimal
    cost: Decimal
    scheduled_cost: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class ProjectCostSummary:
    rows: list[EquipmentCostRow]
    budget_rows: list[BudgetItemCostRow]
    monthly: list[MonthlyCost]
    breakdown: CostBreakdown
    total_hours: Decimal
    total_cost: Decimal
    unpriced: int
    unassigned_cost: Decimal


ZERO = Decimal("0.00")


@dataclass(frozen=True)
class CostBreakdown:
    ownership: Decimal
    operation: Decimal
    maintenance: Decimal
    labor: Decimal


def _component_sum(field: str) -> Sum:
    return Sum(
        ExpressionWrapper(
            F("billable_hours") * F(f"rate__{field}"),
            output_field=DecimalField(max_digits=22, decimal_places=4),
        )
    )


def equipment_cost_summary(
    ctx: RequestContext,
    *,
    project_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_scheduled: bool = False,
) -> ProjectCostSummary:
    """Horas y costo de equipos por equipo y por partida del presupuesto.

    Cuenta usos completados o en curso; con `include_scheduled` suma también lo
    programado (proyección). Los cancelados nunca cuentan. La comparación por partida
    usa el total de la versión vigente (aplicada más reciente) del presupuesto.
    """

    if not can_view_costs(ctx):
        raise PermissionDenied
    statuses = [EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS]
    if include_scheduled:
        statuses.append(EquipmentUsageStatus.SCHEDULED)
    usage = visible_usage(ctx).filter(status__in=statuses)
    if project_id is not None:
        usage = usage.filter(project_id=project_id)
    if date_from:
        usage = usage.filter(usage_date__gte=date_from)
    if date_to:
        usage = usage.filter(usage_date__lte=date_to)

    per_equipment = (
        usage.values("equipment_id", "equipment__code", "equipment__name")
        .annotate(
            sum_hours=Sum("billable_hours"),
            sum_cost=Sum("cost"),
            usages=Count("id"),
            unpriced=Count("id", filter=Q(cost__isnull=True)),
        )
        .order_by("equipment__code")
    )
    rows = [
        EquipmentCostRow(
            equipment_id=row["equipment_id"],
            code=row["equipment__code"],
            name=row["equipment__name"],
            hours=row["sum_hours"] or ZERO,
            cost=row["sum_cost"] or ZERO,
            usages=row["usages"],
            unpriced=row["unpriced"],
        )
        for row in per_equipment
    ]
    rows.sort(key=lambda r: (-r.cost, r.code))

    per_item = list(
        usage.filter(budget_item__isnull=False)
        .values("budget_item_id", "budget_item__code")
        .annotate(imputed=Sum("cost"))
        .order_by("budget_item__code")
    )
    item_ids = [row["budget_item_id"] for row in per_item]
    budgeted: dict[UUID, tuple[str, Decimal]] = {}
    if item_ids:
        for value in scoped_qs(ctx, BudgetItemVersion).filter(
            item_id__in=item_ids, version_id__in=_current_version_ids(ctx, item_ids)
        ):
            budgeted[value.item_id] = (value.description, value.total)
    budget_rows = []
    for row in per_item:
        description, total = budgeted.get(row["budget_item_id"], ("", None))
        imputed = row["imputed"] or ZERO
        budget_rows.append(
            BudgetItemCostRow(
                code=row["budget_item__code"],
                description=description,
                imputed=imputed,
                budgeted=total,
                pct_consumed=pct_of(imputed, total) if total is not None else None,
            )
        )

    monthly = [
        MonthlyCost(
            month=row["month"],
            hours=row["sum_hours"] or ZERO,
            cost=row["sum_cost"] or ZERO,
            scheduled_cost=row["sum_scheduled"] or ZERO,
        )
        for row in usage.annotate(month=TruncMonth("usage_date"))
        .values("month")
        .annotate(
            sum_hours=Sum("billable_hours"),
            sum_cost=Sum("cost"),
            sum_scheduled=Sum("cost", filter=Q(status=EquipmentUsageStatus.SCHEDULED)),
        )
        .order_by("month")
    ]
    parts = usage.filter(rate__isnull=False).aggregate(
        ownership=_component_sum("ownership_rate"),
        operation=_component_sum("operation_rate"),
        maintenance=_component_sum("maintenance_rate"),
        labor=_component_sum("labor_rate"),
    )
    breakdown = CostBreakdown(
        **{key: (value or ZERO).quantize(Decimal("0.01")) for key, value in parts.items()}
    )

    no_item = usage.filter(budget_item__isnull=True).aggregate(total=Sum("cost"))["total"]
    return ProjectCostSummary(
        rows=rows,
        budget_rows=budget_rows,
        monthly=monthly,
        breakdown=breakdown,
        total_hours=sum((r.hours for r in rows), ZERO),
        total_cost=sum((r.cost for r in rows), ZERO),
        unpriced=sum(r.unpriced for r in rows),
        unassigned_cost=no_item or ZERO,
    )


def _current_version_ids(ctx: RequestContext, item_ids: list[UUID]) -> list[UUID]:
    """Id de la versión aplicada más reciente de cada presupuesto de las partidas."""

    budget_ids = set(
        scoped_qs(ctx, BudgetItem).filter(pk__in=item_ids).values_list("budget_id", flat=True)
    )
    current: list[UUID] = []
    for budget in scoped_qs(ctx, Budget).filter(pk__in=budget_ids):
        version = (
            scoped_qs(ctx, BudgetVersion)
            .filter(budget=budget, status=BudgetVersionStatus.APPLIED)
            .order_by("-number")
            .first()
        )
        if version is not None:
            current.append(version.pk)
    return current


@dataclass(frozen=True)
class EquipmentKpis:
    """Indicadores del equipo, calculados al vuelo desde sus registros de uso."""

    month_start: date
    hours_total: Decimal
    hours_month: Decimal
    hours_scheduled: Decimal
    usages_total: int
    days_used_month: int
    utilization_month: Decimal | None
    last_used_on: date | None
    last_meter: Decimal | None
    last_meter_unit: str
    # Solo con permiso de costos (`can_view_costs`); `None` si no se calcularon.
    cost_total: Decimal | None = None
    cost_month: Decimal | None = None
    cost_per_hour: Decimal | None = None
    breakdown: CostBreakdown | None = None
    unpriced: int = 0


def equipment_kpis(
    ctx: RequestContext, equipment: Equipment, *, today: date | None = None
) -> EquipmentKpis:
    """KPIs del equipo a partir de sus usos completados o en curso.

    Utilización del mes: horas usadas entre el día 1 y hoy sobre las horas disponibles
    (días de lunes a viernes × jornada estándar de 8 h). Los costos solo se calculan
    si quien consulta puede verlos.
    """

    today = today or date.today()
    month_start = today.replace(day=1)
    counted = [EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS]
    usage = visible_usage(ctx).filter(equipment=equipment)
    done = usage.filter(status__in=counted)
    month = done.filter(usage_date__gte=month_start, usage_date__lte=today)

    totals = done.aggregate(hours=Sum("billable_hours"), usages=Count("id"), last=Max("usage_date"))
    month_totals = month.aggregate(hours=Sum("billable_hours"))
    scheduled = usage.filter(
        status=EquipmentUsageStatus.SCHEDULED, usage_date__gte=today
    ).aggregate(hours=Sum("billable_hours"))
    last_meter_row = (
        done.filter(end_meter__isnull=False)
        .order_by("-usage_date", "-end_time")
        .values("end_meter", "meter_unit")
        .first()
    )
    hours_total = totals["hours"] or ZERO
    hours_month = month_totals["hours"] or ZERO
    values: dict[str, Any] = {}
    if can_view_costs(ctx):
        cost_total = done.aggregate(total=Sum("cost"))["total"] or ZERO
        priced_hours = done.filter(cost__isnull=False).aggregate(hours=Sum("billable_hours"))[
            "hours"
        ]
        parts = done.filter(rate__isnull=False).aggregate(
            ownership=_component_sum("ownership_rate"),
            operation=_component_sum("operation_rate"),
            maintenance=_component_sum("maintenance_rate"),
            labor=_component_sum("labor_rate"),
        )
        values = {
            "cost_total": cost_total,
            "cost_month": month.aggregate(total=Sum("cost"))["total"] or ZERO,
            "cost_per_hour": (
                (cost_total / priced_hours).quantize(Decimal("0.01")) if priced_hours else None
            ),
            "breakdown": CostBreakdown(
                **{key: (value or ZERO).quantize(Decimal("0.01")) for key, value in parts.items()}
            ),
            "unpriced": done.filter(cost__isnull=True).count(),
        }
    return EquipmentKpis(
        month_start=month_start,
        hours_total=hours_total,
        hours_month=hours_month,
        hours_scheduled=scheduled["hours"] or ZERO,
        usages_total=totals["usages"],
        days_used_month=month.values("usage_date").distinct().count(),
        utilization_month=utilization_pct(hours_month, month_start, today),
        last_used_on=totals["last"],
        last_meter=last_meter_row["end_meter"] if last_meter_row else None,
        last_meter_unit=(
            EquipmentMeterUnit(last_meter_row["meter_unit"]).label if last_meter_row else ""
        ),
        **values,
    )

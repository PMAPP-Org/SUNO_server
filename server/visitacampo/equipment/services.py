"""Servicios autorizados del catálogo y ficha técnica de equipos."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.http import Http404

from visitacampo.core import audit
from visitacampo.core.access import Capability, Section, SectionRole
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    Equipment,
    EquipmentCollection,
    EquipmentCollectionType,
    EquipmentDocument,
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Membership,
    Project,
)
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "add_document",
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
    **details: Any,
) -> EquipmentUsage:
    """Registra una jornada sin solapar equipo ni operador."""

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

    usage = EquipmentUsage(
        org_id=ctx.org_id,
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

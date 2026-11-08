"""Servicios de escritura para accesos funcionales por sección.

Las vistas, el Admin y futuros endpoints deben pasar por este módulo. Así
las invariantes de alcance, la protección del administrador de accesos y la
auditoría no dependen de cada interfaz.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from visitacampo.core import audit
from visitacampo.core.access import (
    AccessScope,
    Capability,
    ProjectScopeSource,
    Section,
    SectionRole,
    allowed_roles,
)
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext, system_context
from visitacampo.core.models import (
    AppUser,
    Membership,
    Project,
    SectionGrant,
    SectionProjectScope,
)
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "GrantResult",
    "provision_legacy_section_grants",
    "revoke_section_grant",
    "set_section_grant",
]


# Compatibilidad para membresías creadas después de ejecutar la migración
# 0014. La migración conserva su propia copia congelada de esta tabla.
LEGACY_GRANTS: dict[str, tuple[tuple[Any, Any, Any], ...]] = {
    "OWNER": (
        (Section.FIELD_RECORDS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.PROJECTS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.BUDGETS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.FORMS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.EQUIPMENT, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.GIS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.REPORTS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.USERS_ACCESS, SectionRole.ACCESS_ADMIN, AccessScope.ORGANIZATION),
        (Section.OPERATIONS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
    ),
    "ADMIN": (
        (Section.FIELD_RECORDS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.PROJECTS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.BUDGETS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.FORMS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.EQUIPMENT, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.GIS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.REPORTS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
        (Section.USERS_ACCESS, SectionRole.ACCESS_ADMIN, AccessScope.ORGANIZATION),
        (Section.OPERATIONS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
    ),
    "SUPERVISOR": (
        (Section.FIELD_RECORDS, SectionRole.REVIEWER, AccessScope.ORGANIZATION),
        (Section.PROJECTS, SectionRole.REVIEWER, AccessScope.ORGANIZATION),
        (Section.BUDGETS, SectionRole.REVIEWER, AccessScope.ORGANIZATION),
        (Section.GIS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
        (Section.REPORTS, SectionRole.REVIEWER, AccessScope.ORGANIZATION),
    ),
    "FIELD_AGENT": (
        (Section.FIELD_RECORDS, SectionRole.CONTRIBUTOR, AccessScope.ORGANIZATION),
        (Section.PROJECTS, SectionRole.CONTRIBUTOR, AccessScope.PROJECTS),
        (Section.GIS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
    ),
    "VIEWER": (
        (Section.FIELD_RECORDS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
        (Section.PROJECTS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
        (Section.BUDGETS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
        (Section.GIS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
        (Section.REPORTS, SectionRole.VIEWER, AccessScope.ORGANIZATION),
    ),
}


@dataclass(frozen=True, slots=True)
class GrantResult:
    grant: SectionGrant
    created: bool


def provision_legacy_section_grants(membership: Membership) -> None:
    """Crea el perfil inicial de una membresía nueva sin pisar decisiones.

    Solo usa ``get_or_create``: si la matriz ya configuró una sección, un
    cambio posterior del rol organizacional nunca la sobrescribe.
    """

    ctx = system_context(membership.org_id)
    for section, role, scope in LEGACY_GRANTS.get(membership.role, ()):
        scoped_qs(ctx, SectionGrant).get_or_create(
            org_id=membership.org_id,
            user_id=membership.user_id,
            section=section,
            defaults={"role": role, "scope": scope},
        )


def _require_access_admin(ctx: RequestContext) -> None:
    if not ctx.has_capability(Section.USERS_ACCESS, Capability.MANAGE_ACCESS):
        raise PermissionDenied("No tienes permiso para administrar accesos.")


def _target_member(ctx: RequestContext, user_id: UUID) -> AppUser:
    if user_id == ctx.user_id:
        raise ValidationError("No puedes modificar tus propios accesos.")
    try:
        membership = scoped_qs(ctx, Membership).select_related("user").get(user_id=user_id)
    except Membership.DoesNotExist as exc:
        # No revela si el identificador pertenece a otra organización.
        raise ValidationError("El usuario no pertenece a la organización.") from exc
    return membership.user


def _validated_projects(ctx: RequestContext, project_ids: Collection[UUID]) -> list[Project]:
    unique_ids = set(project_ids)
    projects = list(scoped_qs(ctx, Project).filter(pk__in=unique_ids).order_by("name"))
    if len(projects) != len(unique_ids):
        raise ValidationError("Uno o más proyectos no pertenecen a la organización.")
    return projects


def _snapshot(grant: SectionGrant | None) -> dict[str, Any] | None:
    if grant is None:
        return None
    return {
        "section": grant.section,
        "role": grant.role,
        "scope": grant.scope,
        "active": grant.active,
        "projects": sorted(
            str(value) for value in grant.project_scopes.values_list("project_id", flat=True)
        ),
    }


@transaction.atomic
def set_section_grant(
    ctx: RequestContext,
    *,
    user_id: UUID,
    section: str,
    role: str,
    scope: str,
    project_ids: Collection[UUID] = (),
) -> GrantResult:
    """Crea o reemplaza el rol de una celda de la matriz."""

    _require_access_admin(ctx)
    target = _target_member(ctx, user_id)
    if section not in Section.values:
        raise ValidationError("La sección no es válida.")
    if role not in allowed_roles(section):
        raise ValidationError("El rol no está permitido en esta sección.")
    if scope not in AccessScope.values:
        raise ValidationError("El alcance no es válido.")
    if section == Section.USERS_ACCESS and scope != AccessScope.ORGANIZATION:
        raise ValidationError("La administración de accesos debe abarcar la organización.")

    projects = _validated_projects(ctx, project_ids) if scope == AccessScope.PROJECTS else []
    if scope == AccessScope.PROJECTS and not projects:
        raise ValidationError("Selecciona al menos un proyecto para el alcance limitado.")

    current = (
        scoped_qs(ctx, SectionGrant)
        .select_for_update()
        .filter(user_id=target.pk, section=section)
        .first()
    )
    before = _snapshot(current)
    created = current is None
    if current is None:
        current = SectionGrant(
            org_id=ctx.org_id,
            user=target,
            section=section,
            granted_by_id=ctx.user_id,
        )
    current.role = role
    current.scope = scope
    current.active = True
    current.revoked_at = None
    current.updated_by_id = ctx.user_id
    current.full_clean()
    current.save()

    current.project_scopes.all().delete()
    scoped_qs(ctx, SectionProjectScope).bulk_create(
        [
            SectionProjectScope(
                org_id=ctx.org_id,
                grant=current,
                project=project,
                granted_by_id=ctx.user_id,
                source=ProjectScopeSource.MANUAL,
            )
            for project in projects
        ]
    )
    audit.record(
        ctx,
        action=(
            AuditAction.ACCESS_GRANTED
            if created or before is None or not before["active"]
            else AuditAction.ACCESS_CHANGED
        ),
        target=f"section-grant:{current.pk}",
        metadata={"user": str(target.pk), "before": before, "after": _snapshot(current)},
    )
    return GrantResult(grant=current, created=created)


@transaction.atomic
def revoke_section_grant(ctx: RequestContext, *, user_id: UUID, section: str) -> SectionGrant:
    """Revoca una celda conservando la fila y su trazabilidad."""

    _require_access_admin(ctx)
    target = _target_member(ctx, user_id)
    try:
        grant = (
            scoped_qs(ctx, SectionGrant)
            .select_for_update()
            .get(user_id=target.pk, section=section, active=True)
        )
    except SectionGrant.DoesNotExist as exc:
        raise ValidationError("El usuario no tiene un acceso activo en esa sección.") from exc

    if section == Section.USERS_ACCESS:
        remaining = (
            scoped_qs(ctx, SectionGrant)
            .select_for_update()
            .filter(
                section=Section.USERS_ACCESS,
                role=SectionRole.ACCESS_ADMIN,
                active=True,
            )
            .exclude(pk=grant.pk)
            .exists()
        )
        if not remaining:
            raise ValidationError("Debe permanecer al menos un administrador de accesos activo.")

    before = _snapshot(grant)
    grant.active = False
    grant.revoked_at = timezone.now()
    grant.updated_by_id = ctx.user_id
    grant.full_clean()
    grant.save(update_fields=["active", "revoked_at", "updated_by", "updated_at"])
    audit.record(
        ctx,
        action=AuditAction.ACCESS_REVOKED,
        target=f"section-grant:{grant.pk}",
        metadata={"user": str(target.pk), "before": before},
    )
    return grant

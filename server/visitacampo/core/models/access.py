"""Asignaciones persistentes del RBAC funcional por secciones (ACC-02)."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from visitacampo.core.access import (
    CAPABILITY_MATRIX,
    AccessScope,
    ProjectScopeSource,
    Section,
    SectionRole,
    allowed_roles,
)

from .base import OrgScopedModel


def section_role_pairs_q() -> models.Q:
    """Condición de BD que impide persistir pares sección/rol desconocidos."""
    condition: models.Q | None = None
    for section, roles in CAPABILITY_MATRIX.items():
        pair = models.Q(section=section, role__in=tuple(roles))
        condition = pair if condition is None else condition | pair
    if condition is None:  # pragma: no cover - la matriz tiene prueba de cobertura.
        return models.Q(pk__isnull=True)
    return condition


class SectionGrant(OrgScopedModel):
    """Un único rol funcional de un usuario dentro de una sección.

    ``scope`` hace explícita la diferencia entre un acceso organizacional y
    uno limitado a los proyectos de ``SectionProjectScope``. Un alcance de
    proyectos sin filas no significa "todos": el contexto lo interpreta como
    ningún proyecto, siguiendo deny-by-default.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="section_grants",
        verbose_name="usuario",
    )
    section = models.CharField(max_length=32, choices=Section.choices, verbose_name="sección")
    role = models.CharField(max_length=32, choices=SectionRole.choices, verbose_name="rol")
    scope = models.CharField(
        max_length=16,
        choices=AccessScope.choices,
        default=AccessScope.PROJECTS,
        verbose_name="alcance",
    )
    active = models.BooleanField(default=True, verbose_name="activo")
    granted_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="section_grants_created",
        verbose_name="concedido por",
    )
    granted_at = models.DateTimeField(auto_now_add=True, verbose_name="concedido en")
    updated_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="section_grants_updated",
        verbose_name="actualizado por",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="actualizado en")
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name="revocado en")

    class Meta:
        db_table = "section_grant"
        ordering = ["user__username", "section"]
        verbose_name = "acceso por sección"
        verbose_name_plural = "accesos por sección"
        constraints = [
            models.UniqueConstraint(
                fields=["org", "user", "section"],
                name="section_grant_org_user_section_uq",
            ),
            models.CheckConstraint(
                condition=section_role_pairs_q(),
                name="section_grant_role_allowed",
            ),
            models.CheckConstraint(
                condition=(models.Q(active=True, revoked_at__isnull=True))
                | (models.Q(active=False, revoked_at__isnull=False)),
                name="section_grant_active_revoked_pair",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "user", "active"], name="section_grant_user_active_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.role not in allowed_roles(self.section):
            errors["role"] = "El rol no está permitido en esta sección."
        if self.user_id and self.org_id and self.user.org_id != self.org_id:
            errors["user"] = "El usuario debe pertenecer a la organización del acceso."
        if self.granted_by_id and self.granted_by.org_id != self.org_id:
            errors["granted_by"] = "Quien concede debe pertenecer a la misma organización."
        if self.updated_by_id and self.updated_by.org_id != self.org_id:
            errors["updated_by"] = "Quien actualiza debe pertenecer a la misma organización."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.user_id}:{self.section}:{self.role}"


class SectionProjectScope(OrgScopedModel):
    """Proyecto incluido explícitamente en el alcance de un ``SectionGrant``."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grant = models.ForeignKey(
        SectionGrant,
        on_delete=models.PROTECT,
        related_name="project_scopes",
        verbose_name="acceso",
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="section_access_scopes",
        verbose_name="proyecto",
    )
    granted_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="section_project_scopes_created",
        verbose_name="concedido por",
    )
    source = models.CharField(
        max_length=16,
        choices=ProjectScopeSource.choices,
        default=ProjectScopeSource.MANUAL,
        verbose_name="origen",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="creado en")

    class Meta:
        db_table = "section_project_scope"
        ordering = ["project__name"]
        verbose_name = "proyecto en alcance"
        verbose_name_plural = "proyectos en alcance"
        constraints = [
            models.UniqueConstraint(
                fields=["grant", "project"],
                name="section_project_scope_grant_project_uq",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.grant_id:
            if self.grant.org_id != self.org_id:
                errors["grant"] = "El acceso debe pertenecer a la misma organización."
            if self.grant.scope != AccessScope.PROJECTS:
                errors["grant"] = "Solo un acceso limitado por proyectos admite este alcance."
        if self.project_id and self.project.org_id != self.org_id:
            errors["project"] = "El proyecto debe pertenecer a la misma organización."
        if self.granted_by_id and self.granted_by.org_id != self.org_id:
            errors["granted_by"] = "Quien concede debe pertenecer a la misma organización."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.grant_id}:{self.project_id}"

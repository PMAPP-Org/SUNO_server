"""Catálogo cerrado de autorización funcional por sección (ACC-01).

La pertenencia a la organización sigue viviendo en ``Membership``. Este
módulo responde una pregunta distinta: dentro de qué sección puede actuar la
persona y con qué capacidades. La matriz es código versionado, no datos
editables, para que una combinación nueva pase por revisión y pruebas.

Los helpers niegan valores desconocidos. Esto importa durante despliegues con
servidor y cliente en versiones distintas: una sección o rol futuro nunca se
interpreta como acceso amplio por accidente.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from django.db import models

__all__ = [
    "CAPABILITY_MATRIX",
    "AccessScope",
    "Capability",
    "Section",
    "SectionRole",
    "ProjectScopeSource",
    "allowed_roles",
    "capabilities_for",
    "role_has_capability",
]


class Section(models.TextChoices):
    FIELD_RECORDS = "FIELD_RECORDS", "Registros de campo"
    PROJECTS = "PROJECTS", "Proyectos"
    BUDGETS = "BUDGETS", "Presupuestos"
    FORMS = "FORMS", "Formularios"
    EQUIPMENT = "EQUIPMENT", "Equipos"
    GIS = "GIS", "Mapas y capas GIS"
    REPORTS = "REPORTS", "Reportes"
    USERS_ACCESS = "USERS_ACCESS", "Usuarios y accesos"
    OPERATIONS = "OPERATIONS", "Operación técnica"


class SectionRole(models.TextChoices):
    MANAGER = "MANAGER", "Gestor"
    REVIEWER = "REVIEWER", "Revisor"
    CONTRIBUTOR = "CONTRIBUTOR", "Colaborador"
    VIEWER = "VIEWER", "Consulta"
    EQUIPMENT_OPERATOR = "EQUIPMENT_OPERATOR", "Operador de equipo"
    SST_AUDITOR = "SST_AUDITOR", "Auditor SST"
    ACCESS_ADMIN = "ACCESS_ADMIN", "Administrador de accesos"


class AccessScope(models.TextChoices):
    ORGANIZATION = "organization", "Toda la organización"
    PROJECTS = "projects", "Proyectos seleccionados"


class ProjectScopeSource(models.TextChoices):
    MANUAL = "manual", "Asignación manual"
    TASK_ASSIGNMENT = "task_assignment", "Responsabilidad de tarea"


class Capability(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    REVIEW = "review"
    APPROVE = "approve"
    REJECT = "reject"
    ASSIGN = "assign"
    IMPORT = "import"
    PUBLISH = "publish"
    EXPORT = "export"
    SCHEDULE = "schedule"
    MANAGE_ACCESS = "manage_access"


def _caps(*values: Capability) -> frozenset[Capability]:
    return frozenset(values)


# Cada par sección/rol está declarado de forma explícita. Aunque dos roles
# compartan capacidades hoy, no se reutiliza una matriz global: una futura
# ampliación de Presupuestos no debe ampliar Equipos por accidente.
# ``TextChoices`` se comporta como ``str`` en ejecución, pero los stubs de
# Django exponen sus atributos como las tuplas declarativas del cuerpo de la
# clase. ``Any`` evita falsos positivos de mypy sin cambiar la frontera
# pública, cuyos helpers normalizan siempre con ``str``.
CAPABILITY_MATRIX: dict[Any, dict[Any, frozenset[Capability]]] = {
    Section.FIELD_RECORDS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.REVIEW,
            Capability.APPROVE,
            Capability.REJECT,
            Capability.ASSIGN,
            Capability.EXPORT,
        ),
        SectionRole.REVIEWER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.REVIEW,
            Capability.APPROVE,
            Capability.REJECT,
            Capability.ASSIGN,
            Capability.EXPORT,
        ),
        SectionRole.CONTRIBUTOR: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
        ),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.PROJECTS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.ASSIGN,
            Capability.IMPORT,
            Capability.EXPORT,
            Capability.SCHEDULE,
        ),
        SectionRole.REVIEWER: _caps(
            Capability.READ,
            Capability.ASSIGN,
            Capability.EXPORT,
            Capability.SCHEDULE,
        ),
        SectionRole.CONTRIBUTOR: _caps(Capability.READ),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.BUDGETS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.IMPORT,
            Capability.EXPORT,
        ),
        SectionRole.REVIEWER: _caps(Capability.READ, Capability.EXPORT),
        SectionRole.VIEWER: _caps(Capability.READ, Capability.EXPORT),
    },
    Section.FORMS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.IMPORT,
            Capability.PUBLISH,
        ),
        SectionRole.CONTRIBUTOR: _caps(Capability.READ, Capability.UPDATE),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.EQUIPMENT: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.ASSIGN,
            Capability.IMPORT,
            Capability.EXPORT,
            Capability.SCHEDULE,
        ),
        SectionRole.REVIEWER: _caps(
            Capability.READ,
            Capability.REVIEW,
            Capability.APPROVE,
            Capability.REJECT,
            Capability.EXPORT,
        ),
        SectionRole.EQUIPMENT_OPERATOR: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
        ),
        SectionRole.SST_AUDITOR: _caps(Capability.READ, Capability.EXPORT),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.GIS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.IMPORT,
        ),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.REPORTS: {
        SectionRole.MANAGER: _caps(Capability.READ, Capability.EXPORT),
        SectionRole.REVIEWER: _caps(Capability.READ, Capability.EXPORT),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
    Section.USERS_ACCESS: {
        SectionRole.ACCESS_ADMIN: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.MANAGE_ACCESS,
        ),
    },
    Section.OPERATIONS: {
        SectionRole.MANAGER: _caps(
            Capability.READ,
            Capability.UPDATE,
            Capability.EXPORT,
        ),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
}


def capabilities_for(section: Any, role: Any) -> frozenset[Capability]:
    """Capacidades del par conocido, o vacío para cualquier valor desconocido."""
    try:
        return CAPABILITY_MATRIX[str(section)][str(role)]
    except (KeyError, TypeError, ValueError):
        return frozenset()


def allowed_roles(section: Any) -> set[str]:
    """Roles válidos de una sección; nunca concede un comodín."""
    try:
        return set(CAPABILITY_MATRIX[str(section)])
    except (KeyError, TypeError, ValueError):
        return set()


def role_has_capability(section: Any, role: Any, capability: Any) -> bool:
    """Decisión pura y fail-closed para una capacidad funcional."""
    try:
        wanted = Capability(str(capability))
    except (TypeError, ValueError):
        return False
    return wanted in capabilities_for(section, role)

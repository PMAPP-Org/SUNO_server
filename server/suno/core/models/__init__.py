"""Modelo de datos núcleo (doc 08 §4.4).

Se organiza como paquete en vez de un `models.py` único porque son 13
tablas con bastante justificación escrita al lado; el agrupamiento sigue
al del propio doc 08:

- `base`     — enumeraciones, rangos y bases **abstractas** compartidas.
- `tenancy`  — `Organization`, `AppUser`, `Membership`, `Device`.
- `catalog`  — `FormTemplate`, `FormTemplateVersion`, `Assignment`.
- `visit`    — `Visit`, `Blob`, `VisitAttachment`.
- `ops`      — `SyncOpLog`, `Job`, `AuditEvent`, `SyncCursorFloor`.
- `project`  — `Project`, `ScheduleImport`, `Task` y sus tablas (costos, etapa 1).
- `budget`   — `Budget`, `BudgetVersion`, `BudgetItem` y sus tablas (costos, pieza 3).
- `access`   — roles funcionales por sección y alcance de proyecto.

Todos los modelos se reexportan aquí para que `from suno.core.models
import Visit` funcione igual que con un `models.py` plano.
"""

from .access import SectionGrant, SectionProjectScope
from .base import (
    ACTIVE_JOB_STATUSES,
    INITIAL_VERSION,
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    RETENTION_DAYS,
    AttachmentRole,
    JobStatus,
    MimeType,
    OrgScopedModel,
    Platform,
    Role,
    SyncedModel,
    SyncEntityType,
)
from .basemap import BasemapPackage, TileFormat
from .budget import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
)
from .catalog import Assignment, FormTemplate, FormTemplateVersion
from .equipment import (
    Equipment,
    EquipmentCollection,
    EquipmentCollectionType,
    EquipmentDocument,
    EquipmentDocumentType,
    EquipmentMeterUnit,
    EquipmentOperatorAssignment,
    EquipmentOwnership,
    EquipmentProject,
    EquipmentRate,
    EquipmentStatus,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
)
from .layer import ReferenceLayer
from .location import MAX_LEVELS, LocationLevel, LocationNode
from .ops import AuditEvent, Job, RateLimitBucket, SyncCursorFloor, SyncOpLog
from .privacy import PrivacyConsent, PrivacyPurpose
from .project import (
    DependencyType,
    Project,
    Resource,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
from .quantity import QuantityPlan, QuantityPlanImport, QuantityPlanImportStatus
from .tenancy import AppUser, Device, Membership, Organization
from .visit import Blob, Visit, VisitAttachment, VisitReview, VisitReviewStatus

__all__ = [
    "ACTIVE_JOB_STATUSES",
    "INITIAL_VERSION",
    "LAT_MAX",
    "LAT_MIN",
    "LON_MAX",
    "LON_MIN",
    "RETENTION_DAYS",
    "AppUser",
    "Assignment",
    "BasemapPackage",
    "Budget",
    "BudgetCharge",
    "BudgetItem",
    "BudgetItemVersion",
    "BudgetRowKind",
    "BudgetVersion",
    "BudgetVersionStatus",
    "AttachmentRole",
    "AuditEvent",
    "Blob",
    "Device",
    "Equipment",
    "EquipmentCollection",
    "EquipmentCollectionType",
    "EquipmentDocument",
    "EquipmentDocumentType",
    "EquipmentMeterUnit",
    "EquipmentOperatorAssignment",
    "EquipmentOwnership",
    "EquipmentProject",
    "EquipmentRate",
    "EquipmentStatus",
    "EquipmentType",
    "EquipmentUsage",
    "EquipmentUsageStatus",
    "DependencyType",
    "FormTemplate",
    "FormTemplateVersion",
    "Job",
    "JobStatus",
    "LocationLevel",
    "LocationNode",
    "MAX_LEVELS",
    "Membership",
    "MimeType",
    "OrgScopedModel",
    "Organization",
    "PrivacyConsent",
    "PrivacyPurpose",
    "Project",
    "QuantityPlan",
    "QuantityPlanImport",
    "QuantityPlanImportStatus",
    "Platform",
    "Role",
    "RateLimitBucket",
    "ReferenceLayer",
    "Resource",
    "ScheduleImport",
    "ScheduleImportStatus",
    "SectionGrant",
    "SectionProjectScope",
    "SyncCursorFloor",
    "SyncEntityType",
    "SyncOpLog",
    "SyncedModel",
    "Task",
    "TaskDependency",
    "TaskResource",
    "TaskResponsible",
    "TileFormat",
    "Visit",
    "VisitAttachment",
    "VisitReview",
    "VisitReviewStatus",
]

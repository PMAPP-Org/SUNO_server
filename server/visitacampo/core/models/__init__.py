"""Modelo de datos núcleo (doc 08 §4.4).

Se organiza como paquete en vez de un `models.py` único porque son 13
tablas con bastante justificación escrita al lado; el agrupamiento sigue
al del propio doc 08:

- `base`     — enumeraciones, rangos y bases **abstractas** compartidas.
- `tenancy`  — `Organization`, `AppUser`, `Membership`, `Device`.
- `catalog`  — `FormTemplate`, `FormTemplateVersion`, `Assignment`.
- `visit`    — `Visit`, `Blob`, `VisitAttachment`.
- `ops`      — `SyncOpLog`, `Job`, `AuditEvent`, `SyncCursorFloor`.

Todos los modelos se reexportan aquí para que `from visitacampo.core.models
import Visit` funcione igual que con un `models.py` plano.
"""

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
from .catalog import Assignment, FormTemplate, FormTemplateVersion
from .ops import AuditEvent, Job, SyncCursorFloor, SyncOpLog
from .tenancy import AppUser, Device, Membership, Organization
from .visit import Blob, Visit, VisitAttachment

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
    "AttachmentRole",
    "AuditEvent",
    "Blob",
    "Device",
    "FormTemplate",
    "FormTemplateVersion",
    "Job",
    "JobStatus",
    "Membership",
    "MimeType",
    "OrgScopedModel",
    "Organization",
    "Platform",
    "Role",
    "SyncCursorFloor",
    "SyncEntityType",
    "SyncOpLog",
    "SyncedModel",
    "Visit",
    "VisitAttachment",
]

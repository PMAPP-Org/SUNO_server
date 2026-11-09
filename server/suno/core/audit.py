"""Traza de auditoría: quién hizo qué, desde dónde (doc 08 §4.4, ISO 27001 A.8.15).

Una sola puerta de escritura sobre `AuditEvent`, por el mismo motivo que
`core/scrub.py` es uno solo: si cada módulo escribiera sus filas a mano, el
día que alguien olvidara redactar `metadata` o hashear la IP, el fallo
quedaría en un solo sitio y nadie lo vería.

**Qué se audita (autenticación, este paso)**

- `auth.login`: login correcto, sesión emitida para un dispositivo.
- `auth.login_denied`: credenciales correctas pero sin membresía
  (deny-by-default).
- `auth.device_conflict`: se presenta un `device_id` que es de otra cuenta.
- `auth.logout`: cierre de sesión con token auténtico, y cuántos refresh se
  revocaron.
- `auth.refresh_rejected`: refresh con `selector.verifier` **auténtico** pero
  rechazado. `reason` dice por qué: `wrong_device` (material robado),
  `not_usable` (revocado o caducado) o `no_membership`.
- `auth.refresh_grace_replay`: el mismo refresh repetido dentro de la
  ventana de gracia (reintento legítimo); se emitió otro par.
- `auth.refresh_reuse_detected`: un refresh ya rotado reutilizado fuera de
  la ventana de gracia: se revocó toda su familia (`revoked` tokens).

**Qué no, a propósito**

- **Contraseña incorrecta / usuario inexistente.** No hay organización a la
  que atribuir la fila (`AuditEvent` es `OrgScopedModel`), y escribirla solo
  cuando el usuario existe haría el camino "existe" medible por tiempo: el
  oráculo de enumeración que ADR-008 prohíbe. Esos intentos los registra
  django-axes en `AccessFailureLog` (`AXES_ENABLE_ACCESS_FAILURE_LOG`), que es
  además lo que decide el bloqueo.
- **Tokens malformados o desconocidos.** Mismo motivo: no hay a quién
  atribuirlos sin dar pistas.
- **Refresh correcto.** Ocurre cada ~15 min por dispositivo; sería ruido que
  enterraría las filas que importan.

**Datos personales**: la IP nunca se guarda; `ip_hash` es un HMAC-SHA256 con
una clave derivada de `SECRET_KEY` (sal de despliegue): permite correlacionar
eventos de la misma IP dentro de un despliegue sin permitir revertirla con
una tabla precalculada. Rotar `SECRET_KEY` rompe esa correlación hacia atrás,
que es aceptable. `metadata` pasa por `scrub()` (RD-12) antes de guardarse.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils.crypto import salted_hmac

from suno.core.context import RequestContext
from suno.core.models import AuditEvent
from suno.core.scoping import scoped_qs
from suno.core.scrub import scrub

__all__ = ["AuditAction", "hash_ip", "purge_expired", "record"]

_IP_HASH_SALT = "suno.core.audit.ip_hash"


class AuditAction:
    """Códigos de `AuditEvent.action`. Estables: el back-office filtra por ellos."""

    LOGIN = "auth.login"
    LOGIN_DENIED = "auth.login_denied"
    DEVICE_CONFLICT = "auth.device_conflict"
    LOGOUT = "auth.logout"
    REFRESH_REJECTED = "auth.refresh_rejected"
    REFRESH_GRACE_REPLAY = "auth.refresh_grace_replay"
    REFRESH_REUSE_DETECTED = "auth.refresh_reuse_detected"
    ADMIN_MFA_ENROLLED = "admin.mfa_enrolled"
    # Back-office (Paso 24): cada acción con efecto deja rastro.
    RECORD_REVIEWED = "backoffice.record_reviewed"
    DEVICE_REVOKED = "backoffice.device_revoked"
    JOB_RETRIED = "backoffice.job_retried"
    ASSIGNMENT_CREATED = "backoffice.assignment_created"
    TEMPLATE_PUBLISHED = "backoffice.template_published"
    RECORDS_EXPORTED = "backoffice.records_exported"
    PERSONAL_DATA_EXPORTED = "backoffice.personal_data_exported"
    PERSONAL_DATA_SUPPRESSED = "backoffice.personal_data_suppressed"
    PRIVACY_CONSENT_RECORDED = "privacy.consent_recorded"
    BASEMAP_IMPORTED = "basemaps.imported"
    BASEMAP_RETIRED = "basemaps.retired"
    LAYER_IMPORTED = "layers.imported"
    LAYER_RETIRED = "layers.retired"
    # Cronograma de proyectos (costos, etapa 1).
    PROJECT_CREATED = "projects.created"
    SCHEDULE_IMPORTED = "projects.schedule_imported"
    SCHEDULE_IMPORT_DISCARDED = "projects.schedule_import_discarded"
    TASK_RESPONSIBLE_ASSIGNED = "projects.responsible_assigned"
    TASK_RESPONSIBLE_REMOVED = "projects.responsible_removed"
    SCHEDULE_EXPORTED = "projects.schedule_exported"
    SCHEDULE_SOURCE_DOWNLOADED = "projects.schedule_source_downloaded"

    # Administración de accesos por sección.
    ACCESS_GRANTED = "access.granted"
    ACCESS_CHANGED = "access.changed"
    ACCESS_REVOKED = "access.revoked"

    # Equipos y ficha técnica (Hito B).
    EQUIPMENT_COLLECTION_CREATED = "equipment.collection_created"
    EQUIPMENT_COLLECTION_UPDATED = "equipment.collection_updated"
    EQUIPMENT_TYPE_CREATED = "equipment.type_created"
    EQUIPMENT_CREATED = "equipment.created"
    EQUIPMENT_UPDATED = "equipment.updated"
    EQUIPMENT_PROJECT_LINKED = "equipment.project_linked"
    EQUIPMENT_OPERATOR_ASSIGNED = "equipment.operator_assigned"
    EQUIPMENT_DOCUMENT_ADDED = "equipment.document_added"
    EQUIPMENT_USAGE_CREATED = "equipment.usage_created"
    EQUIPMENT_USAGE_UPDATED = "equipment.usage_updated"
    EQUIPMENT_RATE_SET = "equipment.rate_set"
    EQUIPMENT_COSTS_RECALCULATED = "equipment.costs_recalculated"
    EQUIPMENT_DEPARTMENT_CREATED = "equipment.department_created"
    EQUIPMENT_METER_READ = "equipment.meter_read"
    EQUIPMENT_FIELD_DEFINED = "equipment.field_defined"
    EQUIPMENT_FIELD_SET = "equipment.field_set"
    EQUIPMENT_CHECKLIST_CHANGED = "equipment.checklist_changed"
    EQUIPMENT_INSPECTION_SUBMITTED = "equipment.inspection_submitted"
    EQUIPMENT_INSPECTION_REVIEWED = "equipment.inspection_reviewed"
    EQUIPMENT_FAULT_CLOSED = "equipment.fault_closed"
    MAINTENANCE_PLAN_SAVED = "maintenance.plan_saved"
    WORK_ORDER_CREATED = "work_order.created"
    WORK_ORDER_APPROVED = "work_order.approved"
    WORK_ORDER_STARTED = "work_order.started"
    WORK_ORDER_CLOSED = "work_order.closed"
    WORK_ORDER_CANCELLED = "work_order.cancelled"
    WORK_ORDER_LINE_CHANGED = "work_order.line_changed"
    EQUIPMENT_LIFE_SHEET_EXPORTED = "equipment.life_sheet_exported"
    FUEL_CATALOG_CHANGED = "fuel.catalog_changed"
    FUEL_FILL_REGISTERED = "fuel.fill_registered"
    FUEL_LIQUIDATION_CREATED = "fuel.liquidation_created"
    FUEL_LIQUIDATION_CONFIRMED = "fuel.liquidation_confirmed"
    TRANSPORT_CATALOG_CHANGED = "transport.catalog_changed"
    TRANSPORT_TRIP_REGISTERED = "transport.trip_registered"
    TRANSPORT_SETTLEMENT_CREATED = "transport.settlement_created"
    TRANSPORT_SETTLEMENT_CONFIRMED = "transport.settlement_confirmed"

    # Presupuestos (costos, pieza 3).
    BUDGET_CREATED = "budgets.created"
    BUDGET_VERSION_APPLIED = "budgets.version_applied"
    BUDGET_VERSION_DISCARDED = "budgets.version_discarded"
    BUDGET_CONTRACTUAL_CHANGED = "budgets.contractual_changed"
    BUDGET_EXPORTED = "budgets.exported"
    BUDGET_SOURCE_DOWNLOADED = "budgets.source_downloaded"
    MATRIX_LEVELS_CHANGED = "matrix.levels_changed"
    MATRIX_IMPORT_APPLIED = "matrix.import_applied"
    MATRIX_IMPORT_DISCARDED = "matrix.import_discarded"
    MATRIX_PLAN_CHANGED = "matrix.plan_changed"
    MATRIX_TASK_LOCATION_CHANGED = "matrix.task_location_changed"


def hash_ip(ip: str | None) -> str | None:
    """HMAC-SHA256 (hex, 64 caracteres) de la IP, o `None` si no se conoce."""
    if not ip:
        return None
    return salted_hmac(_IP_HASH_SALT, ip, algorithm="sha256").hexdigest()


def record(
    ctx: RequestContext,
    *,
    action: str,
    target: str,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
    actor_id: UUID | None = None,
) -> AuditEvent:
    """Escribe un `AuditEvent` en la organización de `ctx`.

    `actor_id` es por defecto el usuario de `ctx`; se pasa explícitamente solo
    cuando el actor no es quien resolvió el contexto.
    """
    return scoped_qs(ctx, AuditEvent).create(
        org_id=ctx.org_id,
        actor_id=actor_id if actor_id is not None else ctx.user_id,
        action=action,
        target=target,
        metadata=scrub(metadata or {}),
        ip_hash=hash_ip(ip),
    )


def purge_expired(ctx: RequestContext, *, older_than: datetime) -> int:
    """Borra los `AuditEvent` de la organización de `ctx` anteriores a `older_than`.

    Es la única vía de borrado de la traza: el Admin no tiene permiso de
    borrado (`core/admin.py`) y lo invoca el periódico `security.purge_logs`
    (`jobs/periodic.py`) con el plazo de `AUDIT_EVENT_RETENTION_DAYS`.
    Devuelve cuántos se borraron.
    """
    deleted, _ = scoped_qs(ctx, AuditEvent).filter(created_at__lt=older_than).delete()
    return deleted

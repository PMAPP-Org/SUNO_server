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

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AuditEvent
from visitacampo.core.scoping import scoped_qs
from visitacampo.core.scrub import scrub

__all__ = ["AuditAction", "hash_ip", "purge_expired", "record"]

_IP_HASH_SALT = "visitacampo.core.audit.ip_hash"


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
    VISIT_REVIEWED = "backoffice.visit_reviewed"
    DEVICE_REVOKED = "backoffice.device_revoked"
    JOB_RETRIED = "backoffice.job_retried"
    ASSIGNMENT_CREATED = "backoffice.assignment_created"
    TEMPLATE_PUBLISHED = "backoffice.template_published"
    VISITS_EXPORTED = "backoffice.visits_exported"
    PERSONAL_DATA_EXPORTED = "backoffice.personal_data_exported"
    PERSONAL_DATA_SUPPRESSED = "backoffice.personal_data_suppressed"


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

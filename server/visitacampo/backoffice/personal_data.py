"""Derechos del titular sobre sus datos (Ley 1581 de 2012, Paso 24).

Los titulares de datos personales de la plataforma son los **usuarios** (los
agentes de campo y el personal): su nombre, usuario, correo, sus
dispositivos, la ubicación y la hora de sus visitas, y su rastro en la
auditoría. Ver `docs/seguridad/tratamiento-datos-personales.md`.

- **Consulta** ([export_personal_data]): todo lo anterior en un JSON legible,
  para entregárselo a la persona que lo pide.
- **Rectificación**: se edita el usuario en el Admin (nombre, correo).
- **Supresión** ([suppress_personal_data]): **anonimización**, no borrado
  físico. Las visitas son registros del cliente (la organización), con su
  propio plazo de retención (`docs/seguridad/retencion.md`), así que se
  conservan, pero dejan de apuntar a una persona identificable: el usuario
  pasa a llamarse `suprimido-<id>`, se vacían nombre, correo y nombres de
  dispositivo, se invalida la contraseña, se cierran todas sus sesiones y se
  borran sus segundos factores. No se puede deshacer.

Solo administración de la organización del usuario (`can_manage_personal_data`),
y las dos acciones quedan en la auditoría.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django_otp import devices_for_user

from visitacampo.authn.models import RefreshToken
from visitacampo.core.audit import AuditAction, record
from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, AuditEvent, Device, Membership, Visit
from visitacampo.core.policies import can_manage_personal_data
from visitacampo.core.scoping import scoped_qs

__all__ = ["SUPPRESSED_PREFIX", "export_personal_data", "suppress_personal_data"]

SUPPRESSED_PREFIX = "suprimido-"


def _check(ctx: RequestContext, user: AppUser) -> None:
    # La persona tiene que ser de la organización de quien atiende la
    # solicitud; si no, ni se confirma que exista (RD-11).
    if user.org_id != ctx.org_id or not can_manage_personal_data(ctx, user.org_id):
        raise PermissionDenied(
            "Solo la administración de la organización del usuario puede hacerlo."
        )


def export_personal_data(ctx: RequestContext, user: AppUser) -> dict[str, Any]:
    """Todos los datos personales de [user] en esta organización (consulta)."""
    _check(ctx, user)

    def iso(value: Any) -> str | None:
        return None if value is None else value.isoformat()

    data = {
        "generado_en": timezone.now().isoformat(),
        "usuario": {
            "id": str(user.pk),
            "usuario": user.get_username(),
            "nombre": user.first_name,
            "apellidos": user.last_name,
            "nombre_visible": user.display_name,
            "correo": user.email,
            "activo": user.is_active,
            "alta": iso(user.date_joined),
            "ultimo_acceso": iso(user.last_login),
        },
        "membresias": [
            {"rol": role, "desde": iso(created)}
            for role, created in scoped_qs(ctx, Membership)
            .filter(user=user)
            .values_list("role", "created_at")
        ],
        "dispositivos": [
            {
                "id": str(device.pk),
                "plataforma": device.platform,
                "modelo": device.model,
                "sistema": device.os_version,
                "version_app": device.app_version,
                "nombre": device.display_name,
                "alta": iso(device.created_at),
            }
            for device in scoped_qs(ctx, Device).filter(owner=user).order_by("created_at")
        ],
        # La ubicación y la hora de cada visita son datos personales del agente.
        "visitas": [
            {"id": str(pk), "capturada_en": iso(at), "lat": lat, "lon": lon}
            for pk, at, lat, lon in scoped_qs(ctx, Visit)
            .filter(created_by=user)
            .order_by("captured_at")
            .values_list("pk", "captured_at", "lat", "lon")
        ],
        "auditoria": [
            {"accion": action, "objetivo": target, "fecha": iso(at)}
            for action, target, at in scoped_qs(ctx, AuditEvent)
            .filter(actor=user)
            .order_by("created_at")
            .values_list("action", "target", "created_at")
        ],
    }
    record(ctx, action=AuditAction.PERSONAL_DATA_EXPORTED, target=f"user:{user.pk}")
    return data


def suppress_personal_data(ctx: RequestContext, user: AppUser, *, ip: str | None = None) -> None:
    """Anonimiza a [user] (supresión). Irreversible: la UI lo confirma antes."""
    _check(ctx, user)
    if user.pk == ctx.user_id:
        raise PermissionDenied(
            "Nadie suprime sus propios datos desde el Admin: lo hace otra persona."
        )
    with transaction.atomic():
        user.username = f"{SUPPRESSED_PREFIX}{user.pk.hex[:12]}"
        user.first_name = ""
        user.last_name = ""
        user.email = ""
        user.display_name = None
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
        scoped_qs(ctx, Device).filter(owner=user).update(display_name=None)
        revoked = (
            scoped_qs(ctx, RefreshToken)
            .filter(user=user, revoked_at__isnull=True)
            .update(revoked_at=timezone.now())
        )
        for device in devices_for_user(user, confirmed=None):
            device.delete()
        record(
            ctx,
            action=AuditAction.PERSONAL_DATA_SUPPRESSED,
            target=f"user:{user.pk}",
            metadata={"sessions_revoked": revoked},
            ip=ip,
        )

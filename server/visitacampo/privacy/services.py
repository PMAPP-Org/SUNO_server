"""Registro de la autorización del titular (Ley 1581, art. 9: prueba de la autorización)."""

from __future__ import annotations

import uuid
from datetime import datetime

from visitacampo.core.audit import AuditAction, record
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Device, PrivacyConsent
from visitacampo.core.policies import CODE_NOT_FOUND, PolicyDenied, check_record_consent
from visitacampo.core.scoping import scoped_qs

__all__ = ["DeviceNotFound", "record_consent"]


class DeviceNotFound(PolicyDenied):
    """`404 not_found`: el dispositivo no existe o no es del solicitante (RD-11)."""

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Device not found.")


def record_consent(
    ctx: RequestContext,
    *,
    device_id: uuid.UUID,
    notice_version: str,
    purposes: list[str],
    accepted_at: datetime,
    ip: str | None = None,
) -> PrivacyConsent:
    """Guarda la autorización del usuario de `ctx` como una fila nueva.

    El dispositivo tiene que ser **suyo** y de su organización: la prueba dice
    "este titular aceptó desde este teléfono", y no puede atribuirse a otro.
    Las finalidades se guardan sin duplicados y en orden estable.
    """
    check_record_consent(ctx, ctx.org_id)
    device = scoped_qs(ctx, Device).filter(pk=device_id, owner_id=ctx.user_id).first()
    if device is None:
        raise DeviceNotFound()
    consent = scoped_qs(ctx, PrivacyConsent).create(
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        device=device,
        notice_version=notice_version,
        purposes=sorted(set(purposes)),
        accepted_at=accepted_at,
    )
    record(
        ctx,
        action=AuditAction.PRIVACY_CONSENT_RECORDED,
        target=f"consent:{consent.pk}",
        metadata={"notice_version": notice_version, "purposes": consent.purposes},
        ip=ip,
    )
    return consent

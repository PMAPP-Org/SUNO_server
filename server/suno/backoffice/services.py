"""Acciones del back-office con efecto (Paso 24): revisar, revocar, reintentar.

Cada función: comprueba la política con el `ctx` de quien actúa, escribe solo
dentro de su organización (`scoped_qs`) y deja un `AuditEvent`. Ninguna confía
en que el Admin ya haya filtrado la fila: la política se evalúa aquí, contra la
fila concreta.
"""

from __future__ import annotations

from datetime import datetime

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from suno.authn.models import RefreshToken
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    AppUser,
    Assignment,
    Device,
    FormTemplateVersion,
    Job,
    JobStatus,
    Record,
    RecordReview,
    RecordReviewStatus,
)
from suno.core.policies import (
    can_assign_work,
    can_manage_device,
    can_read_job_output,
    can_review_record,
)
from suno.core.scoping import scoped_qs
from suno.sync.changeseq import acquire_org_lock, highest_change_seq

__all__ = [
    "RETRYABLE_JOB_STATUSES",
    "create_assignment",
    "retry_job",
    "review_record",
    "revoke_device_sessions",
]

#: Estados desde los que un job se puede relanzar a mano.
RETRYABLE_JOB_STATUSES = (JobStatus.FAILED, JobStatus.DEAD_LETTER)


def review_record(
    ctx: RequestContext, record: Record, *, status: str, comment: str | None = None
) -> RecordReview:
    """Valida o rechaza un registro. Cada decisión es una fila nueva (historial)."""
    if status not in RecordReviewStatus.values:
        raise ValueError(f"decisión desconocida: {status!r}")
    if not can_review_record(ctx, record):
        raise PermissionDenied("Solo supervisión de la organización puede revisar registros.")
    with transaction.atomic():
        # Mismo candado de organización que el push: una revisión no puede
        # colarse entre la comprobación de "registro validado" del push y su
        # UPDATE (ni al revés).
        acquire_org_lock(ctx.org_id)
        review = scoped_qs(ctx, RecordReview).create(
            org_id=ctx.org_id,
            record=record,
            status=status,
            comment=(comment or "").strip() or None,
            reviewer_id=ctx.user_id,
        )
        audit.record(
            ctx,
            action=AuditAction.RECORD_REVIEWED,
            target=f"record:{record.pk}",
            metadata={"status": status},
        )
    return review


def revoke_device_sessions(ctx: RequestContext, device: Device, *, ip: str | None = None) -> int:
    """Revoca **todas** las sesiones vivas del dispositivo (teléfono perdido).

    Mismo criterio que el logout (`authn/services.log_out_session`): por
    `device_id`, todas sus familias de refresh. El access token vigente caduca
    solo en minutos. No se borra nada del teléfono ni de su outbox: si aparece,
    el agente vuelve a iniciar sesión y su trabajo pendiente sigue ahí (ADR-008).
    """
    if not can_manage_device(ctx, device):
        raise PermissionDenied(
            "Solo la administración de la organización puede revocar dispositivos."
        )
    with transaction.atomic():
        revoked = (
            scoped_qs(ctx, RefreshToken)
            .filter(device_id=device.pk, revoked_at__isnull=True)
            .update(revoked_at=timezone.now())
        )
        audit.record(
            ctx,
            action=AuditAction.DEVICE_REVOKED,
            target=f"device:{device.pk}",
            metadata={"revoked": revoked, "owner_id": str(device.owner_id)},
            ip=ip,
        )
    return revoked


def create_assignment(
    ctx: RequestContext,
    *,
    template_version: FormTemplateVersion,
    assigned_to: AppUser,
    title: str | None = None,
    scheduled_for: datetime | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> Assignment:
    """Crea una asignación que el teléfono del agente recibirá por el pull.

    `Assignment` es una entidad sincronizada: se escribe **con `change_seq`,
    bajo el mismo lock de organización que el push** (mismo patrón que
    `forms/services.publish_version`). Crearla con el formulario estándar del
    Admin la dejaría sin `change_seq` y ningún dispositivo la vería nunca.
    """
    if not can_assign_work(ctx, ctx.org_id):
        raise PermissionDenied("Solo supervisión de la organización puede asignar trabajo.")
    if template_version.org_id != ctx.org_id or assigned_to.org_id != ctx.org_id:
        raise PermissionDenied("La plantilla y el agente tienen que ser de la misma organización.")
    if template_version.published_at is None or template_version.deleted_at is not None:
        raise ValueError("Solo se asignan versiones de plantilla publicadas y vigentes.")
    with transaction.atomic():
        acquire_org_lock(ctx.org_id)
        assignment = scoped_qs(ctx, Assignment).create(
            org_id=ctx.org_id,
            template_version=template_version,
            assigned_to=assigned_to,
            title=title or None,
            scheduled_for=scheduled_for,
            lat=lat,
            lon=lon,
            change_seq=highest_change_seq(ctx) + 1,
        )
        audit.record(
            ctx,
            action=AuditAction.ASSIGNMENT_CREATED,
            target=f"assignment:{assignment.pk}",
            metadata={"assigned_to": str(assigned_to.pk)},
        )
    return assignment


def retry_job(ctx: RequestContext, job: Job) -> bool:
    """Relanza un job fallido o en dead-letter. `False` si ya no estaba en ese estado.

    Se reinician los intentos y el lease; `error` se conserva hasta que el
    siguiente intento lo sustituya (es la pista de por qué falló).
    """
    if not can_read_job_output(ctx, job):
        raise PermissionDenied("Solo la administración de la organización puede relanzar jobs.")
    with transaction.atomic():
        updated = (
            scoped_qs(ctx, Job)
            .filter(pk=job.pk, status__in=RETRYABLE_JOB_STATUSES)
            .update(
                status=JobStatus.PENDING,
                attempts=0,
                run_after=None,
                locked_by=None,
                locked_until=None,
                finished_at=None,
            )
        )
        if updated:
            audit.record(
                ctx,
                action=AuditAction.JOB_RETRIED,
                target=f"job:{job.pk}",
                metadata={"type": job.type},
            )
    return bool(updated)

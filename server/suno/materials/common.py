"""Piezas compartidas de los servicios de materiales (costos, pieza 6c).

- Toda escritura bloquea primero la fila del proyecto (`lock_project`: `FOR NO KEY UPDATE`,
  que no frena a quien solo inserta filas que apuntan al proyecto), después el documento y al
  final los saldos (`kardex`). Así se serializan consecutivos, periodo cerrado y corte, y el
  orden de bloqueo es siempre proyecto → documento → saldos.
- Nadie aprueba ni rechaza lo que creó o editó por última vez (también la base lo exige).
- Los reversos del libro llevan la fecha de hoy y el mismo origen que el movimiento reversado.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import money, quantity
from suno.core.context import RequestContext
from suno.core.models import Blob, CostEntry, CostEntryItem, CostStatus, Project
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import can_read_costs
from suno.core.scoping import blob_registry, scoped_qs

FIRST_DAY = date(2000, 1, 1)
MAX_QUANTITY = Decimal("99999999999999.9999")
MAX_PRICE = Decimal("9999999999999999.99")
MAX_LINES = 200
MAX_NOTE = 500
MAX_FILE_NAME = 200
MAX_DESCRIPTION = 300
MAX_THIRD_PARTY = 200
MAX_DOCUMENT_NUMBER = 80
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."


class MaterialRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "Rechazado")


def lock_project(ctx: RequestContext, project: Project) -> None:
    found = (
        scoped_qs(ctx, Project)
        .select_for_update(no_key=True)
        .filter(pk=project.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if found is None:
        raise Http404


def next_number(ctx: RequestContext, model: type[models.Model], project: Project) -> int:
    """El consecutivo siguiente del proyecto (con el proyecto ya bloqueado)."""
    last = scoped_qs(ctx, model).filter(project=project).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


def locked[M: models.Model](ctx: RequestContext, model: type[M], project: Project, pk: UUID) -> M:
    found = scoped_qs(ctx, model).select_for_update().filter(pk=pk, project=project).first()
    if found is None:
        raise Http404
    return found


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise MaterialRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise MaterialRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un borrador o un rechazado; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise MaterialRejected(["Solo se edita o se borra un borrador o un rechazado."])
    if obj.status == "rejected" and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != "draft":
        raise MaterialRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise MaterialRejected([SELF_DECISION])


def quantity_ok(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and 0 < value <= MAX_QUANTITY
        and quantity(value) == value
    )


def price_ok(value: Decimal | None) -> bool:
    return (
        value is not None and value.is_finite() and 0 < value <= MAX_PRICE and money(value) == value
    )


def day_ok(day: date, today: date) -> bool:
    return FIRST_DAY <= day <= today


def stored_upload(upload: Upload | None) -> Blob | None:
    """Sube el soporte (antes de la transacción: no se tiene el bloqueo mientras se escribe)."""
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise MaterialRejected([str(exc)]) from exc


def attachment_url(ctx: RequestContext, project: Project, obj: Any) -> str:
    """URL prefirmada del soporte **después** de comprobar que se leen los costos del proyecto
    (el `Blob` no tiene organización), como `costs.services.support_url`."""
    if not can_read_costs(ctx, project) or obj.project_id != project.pk:
        raise Http404
    blob = blob_registry().filter(pk=obj.attachment_id).first() if obj.attachment_id else None
    if blob is None:
        raise Http404
    return get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )


def reverse_entries(
    ctx: RequestContext, project: Project, field: str, document: Any, note: str
) -> int:
    """Reversa con fecha de hoy y el motivo los movimientos del libro que escribió el documento
    (`field`: su origen en `CostEntry`), como al anular un acta (6b). Devuelve cuántos."""
    today, now = timezone.localdate(), timezone.now()
    originals = list(
        scoped_qs(ctx, CostEntry)
        .filter(**{field: document}, reverses__isnull=True)
        .prefetch_related("item_links")
    )
    reversals = [
        CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=today,
            kind=original.kind,
            description=f"Reverso: {original.description}"[:MAX_DESCRIPTION],
            amount=-original.amount,
            third_party=original.third_party,
            document_type=original.document_type,
            document_number=original.document_number,
            scope=original.scope,
            chapter_id=original.chapter_id,
            task_id=original.task_id,
            status=CostStatus.APPROVED,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            decision_note=note,
            reverses=original,
            **{field: document},
        )
        for original in originals
    ]
    scoped_qs(ctx, CostEntry).bulk_create(reversals)
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [
            CostEntryItem(org_id=ctx.org_id, entry=reversal, item_id=link.item_id)
            for reversal, original in zip(reversals, originals, strict=True)
            for link in original.item_links.all()
        ]
    )
    return len(reversals)

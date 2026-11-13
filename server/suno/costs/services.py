"""Libro de costos del proyecto (costos, pieza 6a): registro, aprobación y reverso.

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, secciones 1, 2 y 5.

- Todo se escribe en `transaction.atomic`, con `scoped_qs` y la auditoría en la misma
  transacción.
- Solo cuenta lo aprobado. Lo aprobado no se edita ni se borra: se reversa.
- La asignación se valida al registrar contra la versión vigente de cada presupuesto del
  proyecto (`catalog.load_catalog`).
- Nadie aprueba ni rechaza lo que registró o modificó por última vez.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Blob,
    BudgetRowKind,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Task,
)
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_read_costs,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import blob_registry, scoped_qs

from .catalog import Catalog, assignable_tasks, load_catalog

FIRST_DAY = date(2000, 1, 1)
MAX_AMOUNT = Decimal("9999999999999999.99")
MAX_DESCRIPTION = 300
MAX_THIRD_PARTY = 200
MAX_DOCUMENT_NUMBER = 80
MAX_NOTE = 500
MAX_FILE_NAME = 200
SELF_DECISION = (
    "Nadie aprueba ni rechaza un costo que registró o modificó: pídeselo a otra persona."
)
APPROVED_IS_FINAL = "Un costo aprobado no se cambia ni se borra: se reversa."


class CostRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0])


@dataclass(frozen=True)
class EntryData:
    incurred_on: date
    kind: str
    description: str
    amount: Decimal
    third_party: str
    document_type: str
    document_number: str
    scope: str
    item_ids: tuple[UUID, ...] = ()
    chapter_id: UUID | None = None
    task_id: UUID | None = None


@dataclass(frozen=True)
class Assignment:
    scope: str
    item_ids: tuple[UUID, ...] = ()
    #: El capítulo asignado o, en un `item`, el capítulo padre del ítem (su origen).
    chapter_id: UUID | None = None
    task_id: UUID | None = None


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _field_errors(data: EntryData, today: date) -> list[str]:
    errors: list[str] = []
    if not FIRST_DAY <= data.incurred_on <= today:
        errors.append("La fecha de causación no puede ser futura ni anterior a 2000.")
    if data.kind not in CostKind.values:
        errors.append("Elige el tipo de costo.")
    if data.document_type not in CostDocumentType.values:
        errors.append("Elige el tipo de documento.")
    description = data.description.strip()
    if not description:
        errors.append("Escribe la descripción.")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(f"La descripción pasa de {MAX_DESCRIPTION} caracteres.")
    if not 0 < data.amount <= MAX_AMOUNT:
        errors.append("El valor debe ser mayor que cero.")
    elif money(data.amount) != data.amount:
        errors.append("El valor va en pesos y centavos (dos decimales).")
    if len(data.third_party.strip()) > MAX_THIRD_PARTY:
        errors.append(f"El tercero pasa de {MAX_THIRD_PARTY} caracteres.")
    if len(data.document_number.strip()) > MAX_DOCUMENT_NUMBER:
        errors.append(f"El número de documento pasa de {MAX_DOCUMENT_NUMBER} caracteres.")
    return errors


def _assignment(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: EntryData
) -> tuple[list[str], Assignment | None]:
    scope = data.scope
    if scope not in CostScope.values:
        return ["Elige a qué se asigna el costo."], None
    if scope == CostScope.INDIRECT:
        if data.item_ids or data.chapter_id or data.task_id:
            return ["Un costo indirecto va al proyecto: sin ítem, capítulo ni tarea."], None
        return [], Assignment(scope)
    errors: list[str] = []
    task_id = data.task_id
    if task_id is not None and task_id not in tasks:
        errors.append("La tarea no es una tarea vigente del proyecto.")
    if scope == CostScope.ITEM:
        row = catalog.rows.get(data.item_ids[0]) if len(data.item_ids) == 1 else None
        if row is None or row.kind != BudgetRowKind.ITEM:
            errors.append(
                "Elige un ítem que esté como ítem en la versión vigente de su presupuesto."
            )
            return errors, None
        return errors, Assignment(scope, (row.item_id,), row.parent_id, task_id)
    chapter = catalog.rows.get(data.chapter_id) if data.chapter_id is not None else None
    if chapter is None or chapter.kind != BudgetRowKind.CHAPTER:
        errors.append("Elige un capítulo de la versión vigente del presupuesto.")
        return errors, None
    if scope == CostScope.CHAPTER:
        if data.item_ids:
            errors.append("Un capítulo completo no lleva ítems sueltos.")
        return errors, Assignment(scope, (), chapter.item_id, task_id)
    picked = tuple(dict.fromkeys(data.item_ids))
    inside = catalog.descendants(chapter.item_id)
    if len(picked) < 2 or any(item_id not in inside for item_id in picked):
        errors.append("Una selección lleva dos o más ítems del mismo capítulo.")
        return errors, None
    return errors, Assignment(scope, picked, chapter.item_id, task_id)


def validate_entry(
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    data: EntryData,
    *,
    today: date | None = None,
) -> tuple[list[str], Assignment | None]:
    """Errores para el usuario y, si no hay, la asignación lista para guardar."""
    errors = _field_errors(data, today or timezone.localdate())
    more, assignment = _assignment(catalog, tasks, data)
    errors.extend(more)
    return errors, None if errors else assignment


def entry_fields(data: EntryData, assignment: Assignment) -> dict[str, Any]:
    return {
        "incurred_on": data.incurred_on,
        "kind": data.kind,
        "description": data.description.strip(),
        "amount": data.amount,
        "third_party": data.third_party.strip(),
        "document_type": data.document_type,
        "document_number": data.document_number.strip(),
        "scope": assignment.scope,
        "chapter_id": assignment.chapter_id,
        "task_id": assignment.task_id,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: EntryData,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
) -> Assignment:
    errors, assignment = validate_entry(
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
        data,
    )
    if assignment is None:
        raise CostRejected(errors)
    return assignment


def _stored(upload: Upload | None) -> Blob | None:
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise CostRejected([str(exc)]) from exc


def _link_items(ctx: RequestContext, entry: CostEntry, item_ids: Sequence[UUID]) -> None:
    scoped_qs(ctx, CostEntryItem).filter(entry=entry).delete()
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id) for item_id in item_ids]
    )


def _metadata(entry: CostEntry) -> dict[str, Any]:
    return {
        "project": str(entry.project_id),
        "incurred_on": entry.incurred_on.isoformat(),
        "kind": entry.kind,
        "scope": entry.scope,
        "amount": str(entry.amount),
        "status": entry.status,
    }


def _locked(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    entry = (
        scoped_qs(ctx, CostEntry).select_for_update().filter(pk=entry_id, project=project).first()
    )
    if entry is None:
        raise Http404
    return entry


def _require_editable(
    ctx: RequestContext,
    entry: CostEntry,
    policy: Callable[[RequestContext, CostEntry], bool],
) -> None:
    if entry.status == CostStatus.APPROVED:
        raise CostRejected([APPROVED_IS_FINAL])
    if entry.status == CostStatus.REJECTED and entry.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, entry):
        raise PermissionDenied


def _require_decidable(ctx: RequestContext, entry: CostEntry) -> None:
    if entry.status != CostStatus.DRAFT:
        raise CostRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (entry.created_by_id, entry.updated_by_id):
        raise CostRejected([SELF_DECISION])


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def create_draft(
    ctx: RequestContext,
    project: Project,
    data: EntryData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> CostEntry:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    assignment = _checked(ctx, project, data, catalog, tasks)
    blob = _stored(upload)
    with transaction.atomic():
        entry = scoped_qs(ctx, CostEntry).create(
            org_id=ctx.org_id,
            project=project,
            status=CostStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            attachment=blob,
            attachment_name=name[:MAX_FILE_NAME] if blob is not None else "",
            **entry_fields(data, assignment),
        )
        _link_items(ctx, entry, assignment.item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_CREATED,
            target=f"cost:{entry.pk}",
            metadata=_metadata(entry),
        )
    return entry


def update_draft(
    ctx: RequestContext,
    project: Project,
    entry_id: UUID,
    data: EntryData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> CostEntry:
    """Guarda un borrador. Un rechazado (solo su autor) vuelve a borrador: eso es reenviarlo."""
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        _require_editable(ctx, entry, can_update_cost)
        assignment = _checked(ctx, project, data, catalog, tasks)
        blob = _stored(upload)
        resubmitted = entry.status == CostStatus.REJECTED
        for field, value in entry_fields(data, assignment).items():
            setattr(entry, field, value)
        if blob is not None:
            entry.attachment = blob
            entry.attachment_name = name[:MAX_FILE_NAME]
        entry.status = CostStatus.DRAFT
        entry.decided_by = None
        entry.decided_at = None
        entry.decision_note = ""
        entry.updated_by_id = ctx.user_id
        entry.save()
        _link_items(ctx, entry, assignment.item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_UPDATED,
            target=f"cost:{entry.pk}",
            metadata={**_metadata(entry), "resubmitted": resubmitted},
        )
    return entry


def delete_draft(ctx: RequestContext, project: Project, entry_id: UUID) -> None:
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        _require_editable(ctx, entry, can_delete_cost)
        target = f"cost:{entry.pk}"
        metadata = {
            **_metadata(entry),
            "description": entry.description,
            "third_party": entry.third_party,
            "document_number": entry.document_number,
        }
        scoped_qs(ctx, CostEntryItem).filter(entry=entry).delete()
        entry.delete()
        audit.record(ctx, action=AuditAction.COST_DELETED, target=target, metadata=metadata)


def approve(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        _require_decidable(ctx, entry)
        if entry.attachment_id is None:
            raise CostRejected(["Adjunta el soporte antes de aprobar."])
        entry.status = CostStatus.APPROVED
        entry.decided_by_id = ctx.user_id
        entry.decided_at = timezone.now()
        entry.decision_note = ""
        entry.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.COST_APPROVED,
            target=f"cost:{entry.pk}",
            metadata=_metadata(entry),
        )
    return entry


def reject(ctx: RequestContext, project: Project, entry_id: UUID, note: str) -> CostEntry:
    reason = note.strip()
    if not reason:
        raise CostRejected(["Escribe el motivo del rechazo."])
    if len(reason) > MAX_NOTE:
        raise CostRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        _require_decidable(ctx, entry)
        entry.status = CostStatus.REJECTED
        entry.decided_by_id = ctx.user_id
        entry.decided_at = timezone.now()
        entry.decision_note = reason
        entry.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.COST_REJECTED,
            target=f"cost:{entry.pk}",
            metadata={**_metadata(entry), "note": reason},
        )
    return entry


def reverse(ctx: RequestContext, project: Project, entry_id: UUID, reason: str) -> CostEntry:
    """Anula un aprobado con otro aprobado de signo contrario, con fecha de hoy (los periodos
    pasados no cambian), la misma asignación y el motivo. No pide soporte propio."""
    text = reason.strip()
    if not text:
        raise CostRejected(["Escribe el motivo del reverso."])
    if len(text) > MAX_NOTE:
        raise CostRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    with transaction.atomic():
        original = _locked(ctx, project, entry_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if original.status != CostStatus.APPROVED or original.reverses_id is not None:
            raise CostRejected(["Solo se reversa un costo aprobado que no sea un reverso."])
        if scoped_qs(ctx, CostEntry).filter(reverses=original).exists():
            raise CostRejected(["Este costo ya se reversó."])
        reversal = scoped_qs(ctx, CostEntry).create(
            org_id=ctx.org_id,
            project=project,
            incurred_on=timezone.localdate(),
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
            decided_at=timezone.now(),
            decision_note=text,
            reverses=original,
        )
        item_ids = list(
            scoped_qs(ctx, CostEntryItem).filter(entry=original).values_list("item_id", flat=True)
        )
        _link_items(ctx, reversal, item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_REVERSED,
            target=f"cost:{original.pk}",
            metadata={**_metadata(original), "reversal": str(reversal.pk), "note": text},
        )
    return reversal


def support_url(ctx: RequestContext, project: Project, entry: CostEntry) -> str:
    """URL prefirmada y corta del soporte, **después** de comprobar que se leen los costos del
    proyecto (el `Blob` no tiene organización: la autorización es la del movimiento)."""
    if not can_read_costs(ctx, project) or entry.project_id != project.pk:
        raise Http404
    blob = blob_registry().filter(pk=entry.attachment_id).first() if entry.attachment_id else None
    if blob is None:
        raise Http404
    return get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )

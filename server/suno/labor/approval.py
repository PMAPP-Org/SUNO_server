"""Aprobar, rechazar y anular planillas (costos, pieza 6d, spec 2.3 y 2.4).

- Aprobar pide `approve` de Mano de obra, la planilla firmada y que no la apruebe quien la creó
  ni quien la editó por última vez (también la base). Se recalcula con los jornales y el factor
  de hoy: si la huella cambió, se rechaza ("lo aprobado es lo firmado", ADR-0032); falta de
  jornal o factor, o una línea en $0, también. El tope diario se comprueba otra vez.
- Al aprobar se congelan jornal, factor y valor de cada línea y se escribe un costo aprobado de
  Mano de obra por destino (decisión 17 del plan).
- Anular pide `reverse`: reversos de hoy con el motivo (`materials.common.reverse_entries`).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    Project,
    Timesheet,
    TimesheetLine,
    TimesheetStatus,
)
from suno.core.policies import can_approve_labor, can_reject_labor, can_reverse_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, load_catalog
from suno.materials.common import lock_project, locked, reverse_entries

from .calc import problems
from .common import (
    MAX_DESCRIPTION,
    MAX_ERRORS,
    LaborRejected,
    clean_note,
    lock_factors,
    lock_workers,
    require_decidable,
    require_version,
)
from .timesheets import (
    Evaluation,
    StoredLine,
    cap_errors,
    day_totals,
    evaluate,
    timesheet_metadata,
)

CHANGED = "La planilla cambió desde que se imprimió; revísala y vuelve a cargar el soporte firmado."


def _require(allowed: bool) -> None:
    """La política se revisa antes de bloquear (quien no puede no toma `FOR UPDATE`) y otra vez
    con las filas bloqueadas."""
    if not allowed:
        raise PermissionDenied


def _post(ctx: RequestContext, project: Project, sheet: Timesheet, evaluation: Evaluation) -> int:
    """Un costo aprobado por destino: la suma de sus líneas ya redondeadas."""
    first: dict[str, StoredLine] = {}
    amounts: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        first.setdefault(line.key, line)
        amounts[line.key] += cost.amount or ZERO
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for key in sorted(first, key=lambda k: evaluation.labels.get(k, "")):
        line = first[key]
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=sheet.ends_on,
            kind=CostKind.LABOR,
            description=f"Tareo {sheet.code} · {evaluation.labels.get(key, '—')}"[:MAX_DESCRIPTION],
            amount=amounts[key],
            third_party="",
            document_type=CostDocumentType.TIMESHEET,
            document_number=sheet.code,
            scope=line.scope,
            chapter_id=line.chapter_id,
            task_id=line.task_id,
            status=CostStatus.APPROVED,
            created_by_id=sheet.created_by_id,
            updated_by_id=sheet.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=sheet.attachment_id,
            attachment_name=sheet.attachment_name,
            timesheet=sheet,
        )
        entries.append(entry)
        links += [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=i) for i in line.item_ids]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def _freeze(ctx: RequestContext, evaluation: Evaluation) -> None:
    rows = []
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        rows.append(
            TimesheetLine(pk=line.pk, daily_wage=cost.wage, factor=cost.factor, amount=cost.amount)
        )
    scoped_qs(ctx, TimesheetLine).bulk_update(
        rows, ["daily_wage", "factor", "amount"], batch_size=2000
    )


def approve_timesheet(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    *,
    catalog: Catalog | None = None,
    version: int | None = None,
) -> Timesheet:
    """`version`: la que vio el revisor junto a su código (el del PDF firmado)."""
    _require(can_approve_labor(ctx, project))
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require(can_approve_labor(ctx, project))
        require_decidable(ctx, sheet)
        require_version(sheet, version)
        if sheet.attachment_id is None:
            raise LaborRejected(["Sube la planilla firmada antes de aprobar."])
        worker_ids = set(
            scoped_qs(ctx, TimesheetLine)
            .filter(timesheet=sheet)
            .values_list("worker_id", flat=True)
        )
        if not worker_ids:
            raise LaborRejected(["La planilla no tiene líneas."])
        lock_workers(ctx, worker_ids)
        lock_factors(ctx)
        evaluation = evaluate(ctx, sheet, catalog)
        found = problems(evaluation.costs, evaluation.names)
        if found:
            raise LaborRejected(found[:MAX_ERRORS])
        if evaluation.summary.fingerprint() != (sheet.summary or {}).get("fingerprint"):
            raise LaborRejected([CHANGED])
        totals = day_totals(ctx, worker_ids, {line.day for line in evaluation.lines})
        errors = cap_errors(ctx, totals, sheet)
        if errors:
            raise LaborRejected(errors)
        _freeze(ctx, evaluation)
        entries = _post(ctx, project, sheet, evaluation)
        # El resumen guardado es el que imprimió el PDF firmado: no se reescribe.
        sheet.status = str(TimesheetStatus.APPROVED)
        sheet.decided_by_id = ctx.user_id
        sheet.decided_at = timezone.now()
        sheet.save(update_fields=["status", "decided_by", "decided_at"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_APPROVED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "entries": entries},
        )
    return sheet


def reject_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, note: str
) -> Timesheet:
    _require(can_reject_labor(ctx, project))
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require(can_reject_labor(ctx, project))
        require_decidable(ctx, sheet)
        sheet.status = str(TimesheetStatus.REJECTED)
        sheet.decided_by_id = ctx.user_id
        sheet.decided_at = timezone.now()
        sheet.decision_note = reason
        sheet.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_REJECTED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "note": reason},
        )
    return sheet


def reverse_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, note: str
) -> Timesheet:
    _require(can_reverse_labor(ctx, project))
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require(can_reverse_labor(ctx, project))
        if sheet.status != TimesheetStatus.APPROVED:
            raise LaborRejected(["Solo se anula una planilla aprobada."])
        reversals = reverse_entries(ctx, project, "timesheet", sheet, reason)
        sheet.status = str(TimesheetStatus.REVERSED)
        sheet.reversed_by_id = ctx.user_id
        sheet.reversed_at = timezone.now()
        sheet.reverse_note = reason
        sheet.save(update_fields=["status", "reversed_by", "reversed_at", "reverse_note"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_REVERSED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "note": reason, "reversals": reversals},
        )
    return sheet

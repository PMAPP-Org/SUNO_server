"""Corte de consumos (costos, pieza 6c, spec 2.6 y 2.7).

- Periodo: empieza el día siguiente al fin del último corte aprobado (sin cortes, en la primera
  salida o devolución sin corte) y termina en la fecha que se elige, nunca en el futuro. Un solo
  corte abierto (borrador o rechazado) por proyecto; el número se asigna al crearlo.
- Contenido: las salidas y devoluciones del proyecto con fecha en el periodo y sin corte,
  agrupadas por destino (`scope`, capítulo o ítems, y tarea); el valor de cada destino es lo
  que salió menos lo devuelto. La devolución va al destino de su salida.
- El borrador guarda un resumen (`summary`): la huella que se compara al aprobar y el detalle
  que imprime el PDF. Recalcular es editar: quita el reporte firmado. Subirlo no es editar.
- Al aprobar (otra persona, con el reporte firmado) se recalcula: si la huella cambió, se
  rechaza ("lo aprobado es lo firmado", como en 6b). Se asigna el corte a sus salidas y
  devoluciones y se escribe un costo aprobado por destino con valor (puede ser negativo), con
  fecha del fin del periodo. El periodo queda cerrado.
- Anular (`reverse`): solo el último aprobado y sin otro corte abierto; reversos con fecha de
  hoy, salidas y devoluciones liberadas y periodo reabierto.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Min, Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    MaterialIssue,
    MaterialIssueItem,
    MaterialIssueLine,
    MaterialReturn,
    MaterialReturnLine,
    MaterialStatus,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, load_catalog

from .common import (
    MAX_DESCRIPTION,
    MAX_FILE_NAME,
    MaterialRejected,
    clean_note,
    lock_project,
    locked,
    next_number,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationKey, destination_key, destination_label, key_text
from .movements import closed_until

CHANGED = (
    "Los consumos del periodo cambiaron; revisa el corte y vuelve a cargar el reporte firmado."
)
MOVED_START = (
    "Hay salidas o devoluciones sin cortar antes del inicio del corte; recalcula el corte."
)
OPEN_CUT = "Ya hay un corte abierto en el proyecto: termínalo o bórralo primero."
NOTHING = "No hay salidas ni devoluciones en el periodo."
QTY_ZERO = Decimal("0.0000")


@dataclass(frozen=True)
class MaterialLine:
    code: str
    name: str
    unit: str
    issued_quantity: Decimal
    issued_value: Decimal
    returned_quantity: Decimal
    returned_value: Decimal

    @property
    def net_quantity(self) -> Decimal:
        return self.issued_quantity - self.returned_quantity

    @property
    def net_value(self) -> Decimal:
        return self.issued_value - self.returned_value


@dataclass(frozen=True)
class CutGroup:
    key: DestinationKey
    label: str
    materials: tuple[MaterialLine, ...]

    @property
    def issued(self) -> Decimal:
        return sum((line.issued_value for line in self.materials), ZERO)

    @property
    def returned(self) -> Decimal:
        return sum((line.returned_value for line in self.materials), ZERO)

    @property
    def net(self) -> Decimal:
        return self.issued - self.returned


@dataclass(frozen=True)
class Proposal:
    start: date
    end: date
    groups: tuple[CutGroup, ...]
    issue_ids: tuple[UUID, ...]
    return_ids: tuple[UUID, ...]

    @property
    def issued(self) -> Decimal:
        return sum((group.issued for group in self.groups), ZERO)

    @property
    def returned(self) -> Decimal:
        return sum((group.returned for group in self.groups), ZERO)

    @property
    def total(self) -> Decimal:
        return self.issued - self.returned

    def fingerprint(self) -> dict[str, Any]:
        """Lo que se compara al aprobar: total, cuántas salidas y devoluciones, y el neto por
        destino."""
        return {
            "total": str(self.total),
            "issues": len(self.issue_ids),
            "returns": len(self.return_ids),
            "groups": {key_text(group.key): str(group.net) for group in self.groups},
        }

    def snapshot(self) -> dict[str, Any]:
        """La huella y el detalle que imprime el PDF (`ConsumptionCut.summary`)."""
        return {
            "fingerprint": self.fingerprint(),
            "groups": [
                {
                    "label": group.label,
                    "issued": str(group.issued),
                    "returned": str(group.returned),
                    "net": str(group.net),
                    "materials": [
                        {
                            "code": line.code,
                            "name": line.name,
                            "unit": line.unit,
                            "issued_quantity": str(line.issued_quantity),
                            "issued_value": str(line.issued_value),
                            "returned_quantity": str(line.returned_quantity),
                            "returned_value": str(line.returned_value),
                        }
                        for line in group.materials
                    ],
                }
                for group in self.groups
            ],
        }


def default_start(ctx: RequestContext, project: Project) -> date | None:
    """El inicio del próximo corte; `None` si no hay nada que cortar."""
    closed = closed_until(ctx, project)
    if closed is not None:
        return closed + timedelta(days=1)
    days = [
        scoped_qs(ctx, MaterialIssue)
        .filter(project=project, cut__isnull=True)
        .aggregate(day=Min("issued_on"))["day"],
        scoped_qs(ctx, MaterialReturn)
        .filter(project=project, cut__isnull=True)
        .aggregate(day=Min("returned_on"))["day"],
    ]
    found = [day for day in days if day is not None]
    return min(found) if found else None


def build_proposal(
    ctx: RequestContext, project: Project, start: date, end: date, catalog: Catalog
) -> Proposal:
    """Las salidas y devoluciones sin corte del periodo, por destino y material. Cinco
    consultas (salidas, devoluciones, ítems de sus destinos y las dos sumas por material)."""
    owners: dict[UUID, tuple[str, UUID | None, UUID | None, str]] = {}
    issue_ids: list[UUID] = []
    for pk, scope, chapter_id, task_id, outline, name in (
        scoped_qs(ctx, MaterialIssue)
        .filter(project=project, cut__isnull=True, issued_on__gte=start, issued_on__lte=end)
        .order_by("number")
        .values_list("pk", "scope", "chapter_id", "task_id", "task__outline_number", "task__name")
    ):
        owners[pk] = (scope, chapter_id, task_id, f"{outline} {name}" if task_id else "")
        issue_ids.append(pk)
    return_ids: list[UUID] = []
    for pk, issue_id, scope, chapter_id, task_id, outline, name in (
        scoped_qs(ctx, MaterialReturn)
        .filter(project=project, cut__isnull=True, returned_on__gte=start, returned_on__lte=end)
        .order_by("number")
        .values_list(
            "pk",
            "issue_id",
            "issue__scope",
            "issue__chapter_id",
            "issue__task_id",
            "issue__task__outline_number",
            "issue__task__name",
        )  # fmt: skip
    ):
        owners[issue_id] = (scope, chapter_id, task_id, f"{outline} {name}" if task_id else "")
        return_ids.append(pk)
    if not owners:
        return Proposal(start, end, (), (), ())
    items: dict[UUID, list[UUID]] = defaultdict(list)
    for issue_id, item_id in (
        scoped_qs(ctx, MaterialIssueItem)
        .filter(issue_id__in=list(owners))
        .values_list("issue_id", "item_id")
    ):
        items[issue_id].append(item_id)
    #: destino → material → [cantidad que salió, valor, cantidad devuelta, valor]
    sums: dict[DestinationKey, dict[UUID, list[Decimal]]] = defaultdict(dict)
    names: dict[UUID, tuple[str, str, str]] = {}
    labels: dict[DestinationKey, str] = {}

    def add(
        issue_id: UUID, material_id: UUID, offset: int, amount: Decimal, value: Decimal
    ) -> None:
        scope, chapter_id, task_id, task = owners[issue_id]
        key = destination_key(scope, chapter_id, items.get(issue_id, ()), task_id)
        labels.setdefault(key, destination_label(catalog, scope, chapter_id, key[2], task))
        row = sums[key].setdefault(material_id, [QTY_ZERO, ZERO, QTY_ZERO, ZERO])
        row[offset] += amount
        row[offset + 1] += value

    for issue_id, material_id, code, name, unit, amount, value in (
        scoped_qs(ctx, MaterialIssueLine)
        .filter(issue_id__in=issue_ids)
        .values("issue_id", "material_id", "material__code", "material__name", "material__unit")
        .annotate(qty_sum=Sum("quantity"), value_sum=Sum("amount"))
        .values_list(
            "issue_id",
            "material_id",
            "material__code",
            "material__name",
            "material__unit",
            "qty_sum",
            "value_sum",
        )
    ):
        names[material_id] = (code, name, unit)
        add(issue_id, material_id, 0, amount, value)
    for issue_id, material_id, code, name, unit, amount, value in (
        scoped_qs(ctx, MaterialReturnLine)
        .filter(material_return_id__in=return_ids)
        .values(
            "material_return__issue_id",
            "issue_line__material_id",
            "issue_line__material__code",
            "issue_line__material__name",
            "issue_line__material__unit",
        )
        .annotate(qty_sum=Sum("quantity"), value_sum=Sum("amount"))
        .values_list(
            "material_return__issue_id",
            "issue_line__material_id",
            "issue_line__material__code",
            "issue_line__material__name",
            "issue_line__material__unit",
            "qty_sum",
            "value_sum",
        )
    ):
        names[material_id] = (code, name, unit)
        add(issue_id, material_id, 2, amount, value)
    groups = [
        CutGroup(
            key,
            labels[key],
            tuple(
                sorted(
                    (MaterialLine(*names[material_id], *row) for material_id, row in rows.items()),
                    key=lambda line: line.code,
                )
            ),
        )
        for key, rows in sums.items()
    ]
    groups.sort(key=lambda group: group.label)
    return Proposal(start, end, tuple(groups), tuple(issue_ids), tuple(return_ids))


def cut_metadata(cut: ConsumptionCut) -> dict[str, Any]:
    return {
        "project": str(cut.project_id),
        "cut": str(cut.pk),
        "number": cut.number,
        "start": cut.period_start.isoformat(),
        "end": cut.period_end.isoformat(),
        "total": str(cut.total),
        "status": cut.status,
    }


def proposal_until(ctx: RequestContext, project: Project, end: date, catalog: Catalog) -> Proposal:
    """La propuesta desde el inicio que toca hasta `end`; rechaza un periodo imposible o vacío."""
    start = default_start(ctx, project)
    if start is None:
        raise MaterialRejected(["No hay salidas ni devoluciones sin cortar."])
    if not start <= end <= timezone.localdate():
        raise MaterialRejected(
            [f"El corte empieza el {start:%d/%m/%Y}: termina entre esa fecha y hoy."]
        )
    proposal = build_proposal(ctx, project, start, end, catalog)
    if not proposal.issue_ids and not proposal.return_ids:
        raise MaterialRejected([NOTHING])
    return proposal


def _store(cut: ConsumptionCut, proposal: Proposal) -> None:
    cut.period_start = proposal.start
    cut.period_end = proposal.end
    cut.summary = proposal.snapshot()
    cut.total = proposal.total
    cut.issue_count = len(proposal.issue_ids)
    cut.return_count = len(proposal.return_ids)


def _has_open(ctx: RequestContext, project: Project, exclude: UUID | None = None) -> bool:
    rows = scoped_qs(ctx, ConsumptionCut).filter(project=project, status__in=OPEN_STATES)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    return rows.exists()


def create_cut(
    ctx: RequestContext, project: Project, period_end: date, *, catalog: Catalog | None = None
) -> ConsumptionCut:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        if _has_open(ctx, project):
            raise MaterialRejected([OPEN_CUT])
        proposal = proposal_until(ctx, project, period_end, catalog)
        cut = ConsumptionCut(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, ConsumptionCut, project),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store(cut, proposal)
        cut.save()
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_CREATED,
            target=f"consumption_cut:{cut.pk}",
            metadata=cut_metadata(cut),
        )
    return cut


def recalculate_cut(
    ctx: RequestContext,
    project: Project,
    cut_id: UUID,
    period_end: date,
    *,
    catalog: Catalog | None = None,
) -> ConsumptionCut:
    """Recalcula y guarda el resumen. Es editar: quita el reporte firmado; un rechazado (solo
    su autor) vuelve a borrador."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        require_editable(ctx, cut, can_update_cost)
        proposal = proposal_until(ctx, project, period_end, catalog)
        resubmitted = cut.status == MaterialStatus.REJECTED
        signed_removed = cut.attachment_id is not None
        _store(cut, proposal)
        cut.status = str(MaterialStatus.DRAFT)
        cut.decided_by = None
        cut.decided_at = None
        cut.decision_note = ""
        cut.attachment = None
        cut.attachment_name = ""
        cut.updated_by_id = ctx.user_id
        cut.save()
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_UPDATED,
            target=f"consumption_cut:{cut.pk}",
            metadata={
                **cut_metadata(cut),
                "resubmitted": resubmitted,
                "signed_removed": signed_removed,
            },
        )
    return cut


def delete_cut(ctx: RequestContext, project: Project, cut_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        require_editable(ctx, cut, can_delete_cost)
        metadata = cut_metadata(cut)
        target = f"consumption_cut:{cut.pk}"
        cut.delete()
        audit.record(
            ctx, action=AuditAction.CONSUMPTION_CUT_DELETED, target=target, metadata=metadata
        )


def _require_signable(ctx: RequestContext, cut: ConsumptionCut) -> None:
    if cut.status != MaterialStatus.DRAFT:
        raise MaterialRejected(["El reporte firmado se sube a un corte en borrador."])
    if not can_update_cost(ctx, cut):
        raise PermissionDenied


def attach_signed_report(
    ctx: RequestContext, project: Project, cut_id: UUID, upload: Upload, name: str
) -> ConsumptionCut:
    """Sube el reporte firmado. No es editar: `updated_by` no cambia."""
    current = scoped_qs(ctx, ConsumptionCut).filter(pk=cut_id, project=project).first()
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        _require_signable(ctx, cut)
        cut.attachment = blob
        cut.attachment_name = name[:MAX_FILE_NAME]
        cut.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_UPDATED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "signed": True},
        )
    return cut


def _post(ctx: RequestContext, project: Project, cut: ConsumptionCut, proposal: Proposal) -> int:
    """Un costo aprobado por destino con neto distinto de cero (decisión 17 del plan)."""
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for group in proposal.groups:
        if group.net == 0:
            continue
        scope, chapter_id, item_ids, task_id = group.key
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=cut.period_end,
            kind=CostKind.MATERIALS,
            description=f"{cut.code} de consumos · {group.label}"[:MAX_DESCRIPTION],
            amount=group.net,
            third_party="",
            document_type=CostDocumentType.CONSUMPTION_CUT,
            document_number=cut.code,
            scope=scope,
            chapter_id=chapter_id,
            task_id=task_id,
            status=CostStatus.APPROVED,
            created_by_id=cut.created_by_id,
            updated_by_id=cut.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=cut.attachment_id,
            attachment_name=cut.attachment_name,
            consumption_cut=cut,
        )
        entries.append(entry)
        links += [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=i) for i in item_ids]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def approve_cut(
    ctx: RequestContext, project: Project, cut_id: UUID, *, catalog: Catalog | None = None
) -> ConsumptionCut:
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, cut)
        if cut.attachment_id is None:
            raise MaterialRejected(["Sube el reporte firmado antes de aprobar."])
        if default_start(ctx, project) != cut.period_start:
            raise MaterialRejected([MOVED_START])
        proposal = build_proposal(ctx, project, cut.period_start, cut.period_end, catalog)
        if proposal.fingerprint() != cut.summary.get("fingerprint"):
            raise MaterialRejected([CHANGED])
        scoped_qs(ctx, MaterialIssue).filter(pk__in=proposal.issue_ids).update(cut=cut)
        scoped_qs(ctx, MaterialReturn).filter(pk__in=proposal.return_ids).update(cut=cut)
        entries = _post(ctx, project, cut, proposal)
        cut.summary = proposal.snapshot()
        cut.status = str(MaterialStatus.APPROVED)
        cut.decided_by_id = ctx.user_id
        cut.decided_at = timezone.now()
        cut.save(update_fields=["summary", "status", "decided_by", "decided_at"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_APPROVED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "entries": entries},
        )
    return cut


def reject_cut(ctx: RequestContext, project: Project, cut_id: UUID, note: str) -> ConsumptionCut:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, cut)
        cut.status = str(MaterialStatus.REJECTED)
        cut.decided_by_id = ctx.user_id
        cut.decided_at = timezone.now()
        cut.decision_note = reason
        cut.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_REJECTED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "note": reason},
        )
    return cut


def void_cut(ctx: RequestContext, project: Project, cut_id: UUID, note: str) -> ConsumptionCut:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if cut.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula un corte aprobado."])
        if _has_open(ctx, project):
            raise MaterialRejected(["Hay un corte abierto: apruébalo o bórralo antes de anular."])
        last = (
            scoped_qs(ctx, ConsumptionCut)
            .filter(project=project, status=MaterialStatus.APPROVED)
            .order_by("-number")
            .first()
        )
        if last is None or last.pk != cut.pk:
            number = last.number if last is not None else "—"
            raise MaterialRejected([f"Solo se anula el último corte aprobado (el {number})."])
        reversals = reverse_entries(ctx, project, "consumption_cut", cut, reason)
        scoped_qs(ctx, MaterialIssue).filter(cut=cut).update(cut=None)
        scoped_qs(ctx, MaterialReturn).filter(cut=cut).update(cut=None)
        cut.status = str(MaterialStatus.VOIDED)
        cut.voided_by_id = ctx.user_id
        cut.voided_at = timezone.now()
        cut.void_note = reason
        cut.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_VOIDED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "note": reason, "reversals": reversals},
        )
    return cut

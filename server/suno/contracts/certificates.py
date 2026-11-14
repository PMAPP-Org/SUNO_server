"""Actas de avance y de liquidación (costos, pieza 6b, spec 2.4–2.7 y 3).

- Un acta se prepara en borrador con las cantidades propuestas desde campo (`proposal`), que se
  ajustan con justificación. Una sola abierta por contrato; el número se asigna al crearla.
- El acta firmada (escaneada) es obligatoria para aprobar. Subirla no es editar el acta (no
  cambia `updated_by`); editar el borrador la quita, porque ya no corresponde a lo impreso.
- Al aprobarla se escriben sus movimientos en el libro (6a): uno por línea con valor.
- Solo se anula la última aprobada, con reversos fechados hoy. La de liquidación cierra el
  contrato; anularla lo reabre.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import ZERO, item_total, quantity
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractCertificate,
    ContractLine,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_create_costs,
    can_delete_cost,
    can_read_costs,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import blob_registry, scoped_qs
from suno.costs.catalog import Catalog, load_catalog
from suno.costs.services import MAX_FILE_NAME

from .calc import Balances, CertificateTotals, LineInput, certificate_totals
from .proposal import Proposal, line_scopes, proposals
from .services import (
    FIRST_DAY,
    MAX_QUANTITY,
    ContractRejected,
    active_lines,
    charge_specs,
    clean_note,
    deduction_specs,
    locked_contract,
    require_decidable,
    require_editable,
    require_open,
)

MAX_JUSTIFICATION = 300


def quantity_text(value: Decimal) -> str:
    """Una cantidad para leer: `4.0000` → "4", `12.5000` → "12,5"."""
    return format(value.normalize(), "f").replace(".", ",")


@dataclass(frozen=True)
class CertificateLineData:
    line_id: UUID
    quantity: Decimal
    justification: str = ""


@dataclass(frozen=True)
class CertificateData:
    period_start: date
    period_end: date
    #: Por línea del contrato; una que falte va con su cantidad propuesta.
    lines: tuple[CertificateLineData, ...] = ()


@dataclass(frozen=True)
class DraftLine:
    line: ContractLine
    label: str
    proposal: Proposal
    quantity: Decimal
    justification: str
    amount: Decimal

    @property
    def differs(self) -> bool:
        return self.quantity != self.proposal.proposed

    @property
    def balance(self) -> Decimal:
        """Saldo de la línea antes de esta acta."""
        return self.line.current_quantity - self.proposal.paid

    @property
    def over_balance(self) -> bool:
        return self.quantity > self.balance


@dataclass(frozen=True)
class Draft:
    """Un acta calculada sin guardar: para el formulario ("Recalcular") y el servicio."""

    contract: Contract
    period_start: date
    period_end: date
    settlement: bool
    lines: tuple[DraftLine, ...]
    totals: CertificateTotals
    errors: tuple[str, ...]

    @property
    def over_balance(self) -> list[DraftLine]:
        return [line for line in self.lines if line.over_balance]


def default_period(
    ctx: RequestContext, contract: Contract, *, today: date | None = None
) -> tuple[date, date]:
    """Desde el día siguiente al fin de la última acta no anulada (o el inicio del contrato)
    hasta hoy."""
    today = today or timezone.localdate()
    last = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract)
        .exclude(status=CertificateStatus.VOIDED)
        .aggregate(end=Max("period_end"))["end"]
    )
    start = last + timedelta(days=1) if last else contract.starts_on
    return start, max(start, today)


def balances(ctx: RequestContext, contract: Contract) -> Balances:
    """Anticipo amortizado y retención de las actas aprobadas. Una consulta."""
    sums = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, status=CertificateStatus.APPROVED)
        .aggregate(
            amortized=Sum("amortization"),
            retained=Sum("retention"),
            returned=Sum("retention_returned"),
        )
    )
    return Balances(
        current_value=contract.current_value,
        advance_amount=contract.advance_amount,
        amortized=sums["amortized"] or ZERO,
        retained=sums["retained"] or ZERO,
        returned=sums["returned"] or ZERO,
    )


def period_conflicts(
    ctx: RequestContext,
    contract: Contract,
    start: date,
    end: date,
    *,
    exclude: UUID | None = None,
) -> list[str]:
    crossing = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, period_start__lte=end, period_end__gte=start)
        .exclude(status=CertificateStatus.VOIDED)
    )
    if exclude is not None:
        crossing = crossing.exclude(pk=exclude)
    return [
        f"El periodo se cruza con el acta {other.number} "
        f"({other.period_start:%d/%m/%Y} – {other.period_end:%d/%m/%Y})."
        for other in crossing.order_by("number")
    ]


def contract_lines(ctx: RequestContext, contract: Contract) -> list[ContractLine]:
    """Las líneas vigentes, en orden, con su ítem y sus tareas."""
    return list(
        active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
        .select_related("item")
        .prefetch_related("task_links")
        .order_by("order")
    )


def build_draft(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    data: CertificateData,
    *,
    settlement: bool,
    catalog: Catalog,
    exclude: UUID | None = None,
    today: date | None = None,
) -> Draft:
    today = today or timezone.localdate()
    lines = contract_lines(ctx, contract)
    found = proposals(
        ctx, project, line_scopes(lines), start=data.period_start, end=data.period_end
    )
    given = {entry.line_id: entry for entry in data.lines}
    errors: list[str] = []
    if not FIRST_DAY <= data.period_start <= data.period_end <= today:
        errors.append(
            "El periodo va desde 2000, «desde» no es posterior a «hasta» y no termina en el futuro."
        )
    else:
        errors += period_conflicts(
            ctx, contract, data.period_start, data.period_end, exclude=exclude
        )
    if set(given) - {line.pk for line in lines}:
        errors.append("Hay cantidades de líneas que no son de este contrato.")
    drafted: list[DraftLine] = []
    for line in lines:
        proposal = found[line.pk]
        entry = given.get(line.pk)
        qty = entry.quantity if entry is not None else proposal.proposed
        justification = entry.justification.strip() if entry is not None else ""
        label = catalog.label_of(line.item_id, line.item.code)
        if not 0 <= qty <= MAX_QUANTITY or quantity(qty) != qty:
            errors.append(f"{label}: la cantidad es cero o más, con máximo 4 decimales.")
            qty = proposal.proposed
        if qty != proposal.proposed and not justification:
            errors.append(
                f"{label}: la cantidad difiere de la propuesta "
                f"({quantity_text(proposal.proposed)}); escribe la justificación."
            )
        if len(justification) > MAX_JUSTIFICATION:
            errors.append(f"{label}: la justificación pasa de {MAX_JUSTIFICATION} caracteres.")
        drafted.append(
            DraftLine(
                line=line,
                label=label,
                proposal=proposal,
                quantity=qty,
                justification=justification,
                amount=item_total(qty, line.unit_price),
            )
        )
    totals = certificate_totals(
        [LineInput(d.line.pk, d.line.order, d.quantity, d.line.unit_price) for d in drafted],
        charge_specs(ctx, contract),
        deduction_specs(ctx, contract),
        retention_pct=contract.retention_pct,
        balances=balances(ctx, contract),
        settlement=settlement,
    )
    return Draft(
        contract=contract,
        period_start=data.period_start,
        period_end=data.period_end,
        settlement=settlement,
        lines=tuple(drafted),
        totals=totals,
        errors=tuple(dict.fromkeys(errors)),
    )


def certificate_metadata(certificate: ContractCertificate) -> dict[str, Any]:
    return {
        "contract": str(certificate.contract_id),
        "project": str(certificate.project_id),
        "certificate": str(certificate.pk),
        "number": certificate.number,
        "settlement": certificate.is_settlement,
        "gross": str(certificate.gross),
        "net": str(certificate.net),
        "status": certificate.status,
    }


def _locked_certificate(
    ctx: RequestContext, contract: Contract, certificate_id: UUID
) -> ContractCertificate:
    certificate = (
        scoped_qs(ctx, ContractCertificate)
        .select_for_update()
        .filter(pk=certificate_id, contract=contract)
        .first()
    )
    if certificate is None:
        raise Http404
    return certificate


def _store(certificate: ContractCertificate, draft: Draft) -> None:
    certificate.period_start = draft.period_start
    certificate.period_end = draft.period_end
    for field, value in draft.totals.stored().items():
        setattr(certificate, field, value)
    certificate.breakdown = draft.totals.breakdown()


def _write_lines(ctx: RequestContext, certificate: ContractCertificate, draft: Draft) -> None:
    scoped_qs(ctx, CertificateLine).filter(certificate=certificate).delete()
    scoped_qs(ctx, CertificateLine).bulk_create(
        [
            CertificateLine(
                org_id=ctx.org_id,
                certificate=certificate,
                line=line.line,
                proposed_quantity=line.proposal.proposed,
                quantity=line.quantity,
                justification=line.justification,
                amount=line.amount,
            )
            for line in draft.lines
        ]
    )


def create_certificate(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: CertificateData,
    *,
    settlement: bool = False,
    catalog: Catalog | None = None,
) -> ContractCertificate:
    """Un acta en borrador. La de liquidación la prepara el Gestor (`reverse`)."""
    if not can_create_costs(ctx, project) or (settlement and not can_reverse_costs(ctx, project)):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        require_open(contract)
        existing = scoped_qs(ctx, ContractCertificate).filter(contract=contract)
        if existing.filter(status__in=OPEN_STATES).exists():
            raise ContractRejected(["Ya hay un acta abierta en este contrato: termínala primero."])
        draft = build_draft(ctx, project, contract, data, settlement=settlement, catalog=catalog)
        if draft.errors:
            raise ContractRejected(list(draft.errors))
        certificate = ContractCertificate(
            org_id=ctx.org_id,
            contract=contract,
            project=project,
            number=(existing.aggregate(n=Max("number"))["n"] or 0) + 1,
            is_settlement=settlement,
            status=CertificateStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store(certificate, draft)
        certificate.save()
        _write_lines(ctx, certificate, draft)
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_CREATED,
            target=f"certificate:{certificate.pk}",
            metadata=certificate_metadata(certificate),
        )
    return certificate


def update_certificate(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    certificate_id: UUID,
    data: CertificateData,
    *,
    catalog: Catalog | None = None,
) -> ContractCertificate:
    """Guarda un acta abierta; una rechazada (solo su autor) vuelve a borrador. Quita el acta
    firmada: ya no corresponde a lo impreso."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        require_editable(ctx, certificate, can_update_cost)
        require_open(contract)
        draft = build_draft(
            ctx, project, contract, data, settlement=certificate.is_settlement,
            catalog=catalog, exclude=certificate.pk,
        )  # fmt: skip
        if draft.errors:
            raise ContractRejected(list(draft.errors))
        resubmitted = certificate.status == CertificateStatus.REJECTED
        unsigned = certificate.attachment_id is not None
        _store(certificate, draft)
        certificate.status = CertificateStatus.DRAFT
        certificate.decided_by = None
        certificate.decided_at = None
        certificate.decision_note = ""
        certificate.attachment = None
        certificate.attachment_name = ""
        certificate.updated_by_id = ctx.user_id
        certificate.save()
        _write_lines(ctx, certificate, draft)
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_UPDATED,
            target=f"certificate:{certificate.pk}",
            metadata={
                **certificate_metadata(certificate),
                "resubmitted": resubmitted,
                "signed_removed": unsigned,
            },
        )
    return certificate


def delete_certificate(
    ctx: RequestContext, project: Project, contract_id: UUID, certificate_id: UUID
) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        require_editable(ctx, certificate, can_delete_cost)
        metadata = certificate_metadata(certificate)
        target = f"certificate:{certificate.pk}"
        scoped_qs(ctx, CertificateLine).filter(certificate=certificate).delete()
        certificate.delete()
        audit.record(ctx, action=AuditAction.CERTIFICATE_DELETED, target=target, metadata=metadata)


def _require_signable(ctx: RequestContext, certificate: ContractCertificate) -> None:
    if certificate.status != CertificateStatus.DRAFT:
        raise ContractRejected(["El acta firmada se sube a un acta en borrador."])
    if not can_update_cost(ctx, certificate):
        raise PermissionDenied


def attach_signed(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    certificate_id: UUID,
    upload: Upload,
    name: str,
) -> ContractCertificate:
    """Sube el acta firmada (PDF o imagen). No es una edición: `updated_by` no cambia. El
    archivo se sube antes del bloqueo; permisos y estado se miran antes y bajo el bloqueo."""
    current = (
        scoped_qs(ctx, ContractCertificate)
        .filter(pk=certificate_id, contract_id=contract_id, project=project)
        .first()
    )
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    try:
        blob = store_upload(upload)
    except UploadRejected as exc:
        raise ContractRejected([str(exc)]) from exc
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        _require_signable(ctx, certificate)
        certificate.attachment = blob
        certificate.attachment_name = name[:MAX_FILE_NAME]
        certificate.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_UPDATED,
            target=f"certificate:{certificate.pk}",
            metadata={**certificate_metadata(certificate), "signed": True},
        )
    return certificate


def reject_certificate(
    ctx: RequestContext, project: Project, contract_id: UUID, certificate_id: UUID, note: str
) -> ContractCertificate:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, certificate)
        certificate.status = CertificateStatus.REJECTED
        certificate.decided_by_id = ctx.user_id
        certificate.decided_at = timezone.now()
        certificate.decision_note = reason
        certificate.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_REJECTED,
            target=f"certificate:{certificate.pk}",
            metadata={**certificate_metadata(certificate), "note": reason},
        )
    return certificate


def signed_url(ctx: RequestContext, project: Project, certificate: ContractCertificate) -> str:
    """URL prefirmada del acta firmada, **después** de comprobar que se leen los costos del
    proyecto (el `Blob` no tiene organización), como `costs.services.support_url`."""
    if not can_read_costs(ctx, project) or certificate.project_id != project.pk:
        raise Http404
    blob = (
        blob_registry().filter(pk=certificate.attachment_id).first()
        if certificate.attachment_id
        else None
    )
    if blob is None:
        raise Http404
    return get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )

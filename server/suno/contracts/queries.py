"""Consultas de la pestaña "Contratos" (costos, pieza 6b, spec 2.1, 2.3, 3.3 y 4).

Número fijo de consultas: la lista son 2 (contratos y agregados de sus actas aprobadas); la
ficha, 8 (el catálogo lo carga la vista); el documento de un acta, 2. Lo ejecutado de un
contrato es el bruto de sus actas aprobadas (las anuladas no cuentan).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Count, F, Prefetch, Q, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO, ChargeSpec, item_total
from suno.control.indices import share_of
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    AuditEvent,
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractAmendment,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog

from .proposal import NONE, line_scopes, paid_by_line, paid_by_project, validated_by_line
from .services import active_lines, charge_specs, deduction_specs

STALE_DAYS = 15
HISTORY_LIMIT = 200
PREFIXES = {str(ContractKind.SUBCONTRACT): "SC", str(ContractKind.PIECEWORK): "DT"}
ALL_TASKS = "Todas"
ACTION_LABELS = {
    AuditAction.CONTRACT_CREATED: "Contrato creado",
    AuditAction.CONTRACT_UPDATED: "Contrato editado",
    AuditAction.CONTRACT_APPROVED: "Contrato aprobado",
    AuditAction.CONTRACT_REJECTED: "Contrato rechazado",
    AuditAction.CONTRACT_VOIDED: "Contrato anulado",
    AuditAction.CONTRACT_SETTLED: "Contrato liquidado",
    AuditAction.CONTRACT_EXPORTED: "Contrato exportado",
    AuditAction.AMENDMENT_CREATED: "Otrosí creado",
    AuditAction.AMENDMENT_UPDATED: "Otrosí editado",
    AuditAction.AMENDMENT_DELETED: "Otrosí borrado",
    AuditAction.AMENDMENT_APPROVED: "Otrosí aprobado",
    AuditAction.AMENDMENT_REJECTED: "Otrosí rechazado",
    AuditAction.CERTIFICATE_CREATED: "Acta creada",
    AuditAction.CERTIFICATE_UPDATED: "Acta editada",
    AuditAction.CERTIFICATE_DELETED: "Acta borrada",
    AuditAction.CERTIFICATE_APPROVED: "Acta aprobada",
    AuditAction.CERTIFICATE_REJECTED: "Acta rechazada",
    AuditAction.CERTIFICATE_VOIDED: "Acta anulada",
    AuditAction.CERTIFICATE_EXPORTED: "PDF del acta descargado",
}


@dataclass(frozen=True)
class ListFilters:
    kind: str = ""
    status: str = ""

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> ListFilters:
        """Valores de la URL; uno desconocido se ignora."""
        kind, status = query.get("tipo", ""), query.get("estado", "")
        return cls(
            kind=kind if kind in ContractKind.values else "",
            status=status if status in ContractStatus.values else "",
        )

    @property
    def query(self) -> str:
        values = {"tipo": self.kind, "estado": self.status}
        return urlencode({key: value for key, value in values.items() if value})


@dataclass(frozen=True)
class Progress:
    """Lo aprobado de un contrato (sus actas aprobadas)."""

    executed: Decimal = ZERO
    amortized: Decimal = ZERO
    #: Retenido menos lo devuelto en la liquidación.
    retained: Decimal = ZERO
    certificates: int = 0


def progress_by_contract(ctx: RequestContext, project: Project) -> dict[UUID, Progress]:
    """Una consulta."""
    rows = (
        scoped_qs(ctx, ContractCertificate)
        .filter(project=project, status=CertificateStatus.APPROVED)
        .order_by()
        .values("contract_id")
        .annotate(
            executed=Sum("gross"),
            amortized=Sum("amortization"),
            retained=Sum(F("retention") - F("retention_returned")),
            n=Count("id"),
        )
        .values_list("contract_id", "executed", "amortized", "retained", "n")
    )
    return {
        contract_id: Progress(executed, amortized, retained, n)
        for contract_id, executed, amortized, retained, n in rows
    }


@dataclass(frozen=True)
class ContractRow:
    contract: Contract
    progress: Progress

    @property
    def executed_pct(self) -> Decimal | None:
        return share_of(self.progress.executed, self.contract.current_value)

    @property
    def advance_balance(self) -> Decimal:
        return max(self.contract.advance_amount - self.progress.amortized, ZERO)

    @property
    def retained(self) -> Decimal:
        return self.progress.retained


def contract_rows(ctx: RequestContext, project: Project, filters: ListFilters) -> list[ContractRow]:
    """La lista de la pestaña. Dos consultas."""
    contracts = scoped_qs(ctx, Contract).filter(project=project)
    if filters.kind:
        contracts = contracts.filter(kind=filters.kind)
    if filters.status:
        contracts = contracts.filter(status=filters.status)
    progress = progress_by_contract(ctx, project)
    return [
        ContractRow(contract, progress.get(contract.pk, Progress()))
        for contract in contracts.order_by("number")
    ]


def suggest_number(ctx: RequestContext, project: Project, kind: str) -> str:
    """`SC-001`, `DT-001`… el siguiente libre del prefijo del tipo. Una consulta."""
    prefix = PREFIXES.get(kind, "SC")
    pattern = re.compile(rf"{prefix}-(\d+)", re.IGNORECASE)
    used = [
        int(match.group(1))
        for number in scoped_qs(ctx, Contract)
        .filter(project=project, number__istartswith=f"{prefix}-")
        .values_list("number", flat=True)
        if (match := pattern.fullmatch(number))
    ]
    return f"{prefix}-{max(used, default=0) + 1:03d}"


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------


def _with_tasks(ctx: RequestContext, lines: Any) -> Any:
    return lines.select_related("item").prefetch_related(
        Prefetch(
            "task_links",
            queryset=scoped_qs(ctx, ContractLineTask)
            .select_related("task")
            .order_by("task__sort_order"),
        )
    )


def tasks_text(line: ContractLine) -> str:
    """Las tareas de la línea ("2.1 Columnas, 2.2 …") o "Todas"."""
    names = [f"{link.task.outline_number} {link.task.name}" for link in line.task_links.all()]
    return ", ".join(names) if names else ALL_TASKS


@dataclass(frozen=True)
class LineState:
    line: ContractLine
    label: str
    tasks: str
    paid: Decimal
    #: Validado en campo hasta hoy (contratos aprobados).
    validated: Decimal

    @property
    def contracted(self) -> Decimal:
        return self.line.current_quantity

    @property
    def balance(self) -> Decimal:
        return self.contracted - self.paid

    @property
    def contracted_value(self) -> Decimal:
        return item_total(self.contracted, self.line.unit_price)

    @property
    def paid_value(self) -> Decimal:
        return item_total(self.paid, self.line.unit_price)

    @property
    def balance_value(self) -> Decimal:
        return item_total(self.balance, self.line.unit_price)

    @property
    def unpaid_validated(self) -> Decimal:
        return max(self.validated - self.paid, NONE)


@dataclass(frozen=True)
class ContractSheet:
    contract: Contract
    lines: tuple[LineState, ...]
    charges: list[ChargeSpec]
    deductions: list[ChargeSpec]
    certificates: tuple[ContractCertificate, ...]
    amendments: tuple[ContractAmendment, ...]
    progress: Progress = field(default_factory=Progress)

    @property
    def direct_value(self) -> Decimal:
        return sum((line.contracted_value for line in self.lines), ZERO)

    @property
    def executed_pct(self) -> Decimal | None:
        return share_of(self.progress.executed, self.contract.current_value)

    @property
    def advance_balance(self) -> Decimal:
        return max(self.contract.advance_amount - self.progress.amortized, ZERO)

    @property
    def open_certificate(self) -> ContractCertificate | None:
        return next((c for c in self.certificates if c.status in OPEN_STATES), None)

    @property
    def open_amendment(self) -> ContractAmendment | None:
        return next((a for a in self.amendments if a.status in OPEN_STATES), None)

    @property
    def last_approved(self) -> ContractCertificate | None:
        approved = [c for c in self.certificates if c.status == CertificateStatus.APPROVED]
        return approved[-1] if approved else None


def contract_sheet(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    catalog: Catalog,
    *,
    today: date | None = None,
) -> ContractSheet:
    """La ficha: líneas vigentes con contratado, pagado, saldo y validado; cargos; descuentos;
    actas; otrosíes. Ocho consultas."""
    today = today or timezone.localdate()
    lines = list(
        _with_tasks(
            ctx, active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
        ).order_by("order")
    )
    # Una consulta también en un borrador (se ignora en la plantilla): número fijo.
    validated = validated_by_line(ctx, project, line_scopes(lines), until=today)
    paid = paid_by_line(ctx, [line.pk for line in lines])
    certificates = tuple(
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract)
        .select_related("created_by", "decided_by", "voided_by")
        .order_by("number")
    )
    amendments = tuple(
        scoped_qs(ctx, ContractAmendment)
        .filter(contract=contract)
        .select_related("created_by", "decided_by")
        .order_by("number")
    )
    approved = [c for c in certificates if c.status == CertificateStatus.APPROVED]
    return ContractSheet(
        contract=contract,
        lines=tuple(
            LineState(
                line=line,
                label=catalog.label_of(line.item_id, line.item.code),
                tasks=tasks_text(line),
                paid=paid.get(line.pk, NONE),
                validated=validated[line.pk],
            )
            for line in lines
        ),
        charges=charge_specs(ctx, contract),
        deductions=deduction_specs(ctx, contract),
        certificates=certificates,
        amendments=amendments,
        progress=Progress(
            executed=sum((c.gross for c in approved), ZERO),
            amortized=sum((c.amortization for c in approved), ZERO),
            retained=sum((c.retention - c.retention_returned for c in approved), ZERO),
            certificates=len(approved),
        ),
    )


def contract_history(ctx: RequestContext, contract: Contract) -> list[AuditEvent]:
    """Los eventos del contrato y de sus otrosíes y actas (todos llevan `metadata.contract`)."""
    return list(
        scoped_qs(ctx, AuditEvent)
        .filter(Q(target=f"contract:{contract.pk}") | Q(metadata__contract=str(contract.pk)))
        .select_related("actor")
        .order_by("-created_at")[:HISTORY_LIMIT]
    )


# ---------------------------------------------------------------------------
# Documento de un acta (ficha y PDF)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentLine:
    label: str
    unit: str
    unit_price: Decimal
    contracted: Decimal
    previous: Decimal
    this: Decimal
    proposed: Decimal
    justification: str
    amount: Decimal

    @property
    def accumulated(self) -> Decimal:
        return self.previous + self.this

    @property
    def balance(self) -> Decimal:
        return self.contracted - self.accumulated

    @property
    def contracted_value(self) -> Decimal:
        return item_total(self.contracted, self.unit_price)

    @property
    def previous_value(self) -> Decimal:
        return item_total(self.previous, self.unit_price)

    @property
    def accumulated_value(self) -> Decimal:
        return item_total(self.accumulated, self.unit_price)

    @property
    def balance_value(self) -> Decimal:
        return item_total(self.balance, self.unit_price)

    @property
    def differs(self) -> bool:
        return self.this != self.proposed


@dataclass(frozen=True)
class CertificateDocument:
    project: Project
    contract: Contract
    certificate: ContractCertificate
    lines: tuple[DocumentLine, ...]

    @property
    def charges(self) -> list[dict[str, Any]]:
        return list(self.certificate.breakdown.get("charges", []))

    @property
    def deductions(self) -> list[dict[str, Any]]:
        return list(self.certificate.breakdown.get("deductions", []))


def certificate_document(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    certificate: ContractCertificate,
    catalog: Catalog,
) -> CertificateDocument:
    """Por línea: contratado (vigente), anterior (actas aprobadas de número menor), esta acta,
    acumulado y saldo, en cantidad y valor. El ítem, como lo guardó el acta (#88). Dos
    consultas."""
    rows = list(
        scoped_qs(ctx, CertificateLine)
        .filter(certificate=certificate)
        .select_related("line__item")
        .order_by("line__order")
    )
    previous = paid_by_line(ctx, [row.line_id for row in rows], before_number=certificate.number)
    return CertificateDocument(
        project=project,
        contract=contract,
        certificate=certificate,
        lines=tuple(
            DocumentLine(
                label=row.label or catalog.label_of(row.line.item_id, row.line.item.code),
                unit=row.line.unit,
                unit_price=row.line.unit_price,
                contracted=row.line.current_quantity,
                previous=previous.get(row.line_id, NONE),
                this=row.quantity,
                proposed=row.proposed_quantity,
                justification=row.justification,
                amount=row.amount,
            )
            for row in rows
        ),
    )


# ---------------------------------------------------------------------------
# Avisos de la pestaña, comprometido, bandeja
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alert:
    #: `stale`, `unpaid`, `overdue`, `advance` u `overlap`.
    kind: str
    text: str
    contract_id: UUID | None = None


def _crosses(mine: frozenset[UUID], theirs: frozenset[UUID]) -> bool:
    return not mine or not theirs or bool(mine & theirs)


def tab_alerts(
    ctx: RequestContext, project: Project, catalog: Catalog, *, today: date | None = None
) -> list[Alert]:
    """Spec 4, "Avisos de la pestaña". Siete consultas."""
    today = today or timezone.localdate()
    alerts: list[Alert] = []
    stale = (
        scoped_qs(ctx, ContractCertificate)
        .filter(
            project=project,
            status=CertificateStatus.DRAFT,
            created_at__lt=timezone.now() - timedelta(days=STALE_DAYS),
        )
        .select_related("contract")
        .order_by("created_at")
    )
    for certificate in stale:
        alerts.append(
            Alert(
                "stale",
                f"Acta {certificate.number} de {certificate.contract.number}: en borrador desde "
                f"el {timezone.localtime(certificate.created_at):%d/%m/%Y}.",
                certificate.contract_id,
            )
        )
    contracts = {
        contract.pk: contract
        for contract in scoped_qs(ctx, Contract).filter(
            project=project, status__in=[ContractStatus.DRAFT, ContractStatus.APPROVED]
        )
    }
    lines = list(
        active_lines(scoped_qs(ctx, ContractLine).filter(contract_id__in=list(contracts)))
        .prefetch_related("task_links")
        .order_by("contract__number", "order")
    )
    live = [line for line in lines if contracts[line.contract_id].status == ContractStatus.APPROVED]
    validated = validated_by_line(ctx, project, line_scopes(live), until=today)
    paid = paid_by_project(ctx, project)
    for line in live:
        unpaid = validated[line.pk] - paid.get(line.pk, NONE)
        if unpaid > 0:
            number = contracts[line.contract_id].number
            alerts.append(
                Alert(
                    "unpaid",
                    f"{number} · {catalog.label_of(line.item_id)}: "
                    f"{format(unpaid.normalize(), 'f').replace('.', ',')} validado sin pagar.",
                    line.contract_id,
                )
            )
    progress = progress_by_contract(ctx, project)
    for contract in contracts.values():
        if contract.status != ContractStatus.APPROVED:
            continue
        if contract.ends_on < today:
            alerts.append(
                Alert(
                    "overdue",
                    f"{contract.number}: venció el {contract.ends_on:%d/%m/%Y} y no está "
                    "liquidado.",
                    contract.pk,
                )
            )
        done = progress.get(contract.pk, Progress())
        if (
            contract.current_value > 0
            and done.executed >= contract.current_value
            and contract.advance_amount > done.amortized
        ):
            alerts.append(
                Alert(
                    "advance",
                    f"{contract.number}: ejecutado al 100 % con anticipo por amortizar.",
                    contract.pk,
                )
            )
    by_item: dict[UUID, list[ContractLine]] = defaultdict(list)
    for line in lines:
        by_item[line.item_id].append(line)
    seen: set[tuple[UUID, str, str]] = set()
    for item_id, group in by_item.items():
        for n, first in enumerate(group):
            first_tasks = frozenset(link.task_id for link in first.task_links.all())
            for second in group[n + 1 :]:
                second_tasks = frozenset(link.task_id for link in second.task_links.all())
                if not _crosses(first_tasks, second_tasks):
                    continue
                a = contracts[first.contract_id].number
                b = contracts[second.contract_id].number
                key = (item_id, a, b)
                if key in seen:
                    continue
                seen.add(key)
                who = f"{a} y {b}" if a != b else f"dos líneas de {a}"
                alerts.append(
                    Alert(
                        "overlap",
                        f"{catalog.label_of(item_id)}: {who} lo cubren en las mismas tareas.",
                        first.contract_id,
                    )
                )
    return alerts


def committed_by_item(ctx: RequestContext, project: Project) -> dict[UUID, Decimal]:
    """Comprometido en contratos por ítem, en valor directo (comparable con el BAC del ítem):
    aprobados, cantidad vigente × precio; liquidados, lo pagado × precio. Dos consultas."""
    rows = list(
        active_lines(
            scoped_qs(ctx, ContractLine).filter(
                contract__project=project,
                contract__status__in=[ContractStatus.APPROVED, ContractStatus.SETTLED],
            )
        ).values_list("pk", "item_id", "current_quantity", "unit_price", "contract__status")
    )
    paid = paid_by_project(ctx, project)
    committed: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for pk, item_id, current, price, status in rows:
        quantity = current if status == ContractStatus.APPROVED else paid.get(pk, NONE)
        committed[item_id] += item_total(quantity, price)
    return {item_id: value for item_id, value in committed.items() if value}


def contracts_on_item(ctx: RequestContext, project: Project, item_id: UUID) -> list[str]:
    """Los contratos aprobados con una línea vigente del ítem. Una consulta."""
    return sorted(
        set(
            active_lines(
                scoped_qs(ctx, ContractLine).filter(
                    contract__project=project,
                    contract__status=ContractStatus.APPROVED,
                    item_id=item_id,
                )
            ).values_list("contract__number", flat=True)
        )
    )


@dataclass(frozen=True)
class InboxItems:
    contracts: list[Contract]
    amendments: list[ContractAmendment]
    certificates: list[ContractCertificate]

    @property
    def count(self) -> int:
        return len(self.contracts) + len(self.amendments) + len(self.certificates)


def _not_mine(ctx: RequestContext, rows: Any) -> Any:
    return rows.exclude(created_by_id=ctx.user_id).exclude(updated_by_id=ctx.user_id)


def _waiting(ctx: RequestContext, project: Project) -> tuple[Any, Any, Any]:
    """Borradores de otras personas; otrosíes y actas solo de contratos aprobados (de uno
    liquidado o anulado ya no se deciden)."""
    contracts = _not_mine(
        ctx,
        scoped_qs(ctx, Contract).filter(
            project=project, status=ContractStatus.DRAFT, rejected_by__isnull=True
        ),
    )
    amendments = _not_mine(
        ctx,
        scoped_qs(ctx, ContractAmendment).filter(
            project=project, status="draft", contract__status=ContractStatus.APPROVED
        ),
    )
    certificates = _not_mine(
        ctx,
        scoped_qs(ctx, ContractCertificate).filter(
            project=project, status="draft", contract__status=ContractStatus.APPROVED
        ),
    )
    return contracts, amendments, certificates


def inbox_items(ctx: RequestContext, project: Project) -> InboxItems:
    """Lo que otras personas prepararon y espera decisión (un contrato devuelto con motivo no:
    espera a su autor). Tres consultas."""
    contracts, amendments, certificates = _waiting(ctx, project)
    return InboxItems(
        contracts=list(contracts.select_related("created_by").order_by("created_at")),
        amendments=list(amendments.select_related("contract", "created_by").order_by("created_at")),
        certificates=list(
            certificates.select_related("contract", "created_by")
            .annotate(
                adjusted=Count("lines", filter=~Q(lines__quantity=F("lines__proposed_quantity")))
            )
            .order_by("created_at")
        ),
    )


def inbox_count(ctx: RequestContext, project: Project) -> int:
    """Para el contador de "Por aprobar" de Costos. Tres consultas."""
    return sum(rows.count() for rows in _waiting(ctx, project))

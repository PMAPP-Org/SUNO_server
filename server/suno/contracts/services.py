"""Contratos y otrosíes (costos, pieza 6b, spec 1, 2.2, 2.3 y 4).

- Todo se escribe en `transaction.atomic`, con `scoped_qs` y la auditoría en la misma
  transacción. Toda escritura sobre un contrato, sus otrosíes o sus actas bloquea antes la fila
  del contrato (`select_for_update`): así se serializan consecutivos, topes y periodos.
- Un contrato se edita solo en borrador; aprobado, cambia por otrosíes.
- Nadie aprueba ni rechaza lo que creó o editó por última vez.
- Permisos: los de la sección Costos (`core.policies`), sin políticas nuevas.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import (
    HUNDRED,
    PCT_STEP,
    ChargeError,
    ChargeSpec,
    money,
    quantity,
    validate_charges,
)
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    AmendmentStatus,
    BudgetRowKind,
    Contract,
    ContractAmendment,
    ContractAmendmentLine,
    ContractCertificate,
    ContractCharge,
    ContractDeduction,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Project,
    Task,
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
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog

from .calc import LineInput, contract_value, scaled, validate_deductions

MAX_LINES = 200
MAX_NUMBER = 30
MAX_PURPOSE = 300
MAX_CONTRACTOR = 200
MAX_UNIT = 30
MAX_NOTE = 500
FIRST_DAY = date(2000, 1, 1)
MAX_QUANTITY = Decimal("99999999999999.9999")
MAX_PRICE = Decimal("9999999999999999.99")
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."
APPROVED_IS_FINAL = "Un contrato aprobado solo cambia por otrosíes."


class ContractRejected(Exception):
    """No se puede guardar. `errors`: mensajes para el usuario. `overlaps`: traslapes que hay
    que confirmar (sin `errors`)."""

    def __init__(self, errors: Sequence[str], *, overlaps: Sequence[str] = ()) -> None:
        self.errors = list(errors)
        self.overlaps = list(overlaps)
        super().__init__((self.errors or self.overlaps or ["Traslape"])[0])


@dataclass(frozen=True)
class LineData:
    item_id: UUID
    #: Vacío: todas las tareas del ítem.
    task_ids: tuple[UUID, ...]
    unit: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class ContractData:
    kind: str
    number: str
    purpose: str
    contractor: str
    starts_on: date
    ends_on: date
    advance_pct: Decimal
    retention_pct: Decimal
    lines: tuple[LineData, ...]
    charges: tuple[ChargeSpec, ...] = ()
    deductions: tuple[ChargeSpec, ...] = ()
    confirm_overlap: bool = False


@dataclass(frozen=True)
class IncreaseData:
    line_id: UUID
    added_quantity: Decimal


@dataclass(frozen=True)
class AmendmentData:
    signed_on: date
    reason: str
    new_ends_on: date | None = None
    increases: tuple[IncreaseData, ...] = ()
    new_lines: tuple[LineData, ...] = ()
    confirm_overlap: bool = False


# ---------------------------------------------------------------------------
# Lecturas compartidas (también las usan `certificates` y `queries`)
# ---------------------------------------------------------------------------


def active_lines(lines: QuerySet[ContractLine]) -> QuerySet[ContractLine]:
    """Las líneas que cuentan: las del contrato y las de otrosíes aprobados."""
    return lines.filter(Q(amendment__isnull=True) | Q(amendment__status=AmendmentStatus.APPROVED))


def charge_specs(ctx: RequestContext, contract: Contract) -> list[ChargeSpec]:
    return [
        ChargeSpec(order, name, pct, base)
        for order, name, pct, base in scoped_qs(ctx, ContractCharge)
        .filter(contract=contract)
        .order_by("order")
        .values_list("order", "name", "pct", "base")
    ]


def deduction_specs(ctx: RequestContext, contract: Contract) -> list[ChargeSpec]:
    return [
        ChargeSpec(order, name, pct, base)
        for order, name, pct, base in scoped_qs(ctx, ContractDeduction)
        .filter(contract=contract)
        .order_by("order")
        .values_list("order", "name", "pct", "base")
    ]


def refresh_value(ctx: RequestContext, contract: Contract) -> Decimal:
    """Valor vigente: bruto directo de las líneas vigentes (cantidad vigente) más cargos."""
    rows = (
        active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
        .order_by("order")
        .values_list("pk", "order", "current_quantity", "unit_price")
    )
    return contract_value([LineInput(*row) for row in rows], charge_specs(ctx, contract)).total


def locked_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    contract = (
        scoped_qs(ctx, Contract).select_for_update().filter(pk=contract_id, project=project).first()
    )
    if contract is None:
        raise Http404
    return contract


def require_open(contract: Contract) -> None:
    """Solo un contrato aprobado admite otrosíes y actas."""
    if contract.status == ContractStatus.DRAFT:
        raise ContractRejected(["El contrato todavía no está aprobado."])
    if contract.status != ContractStatus.APPROVED:
        raise ContractRejected(["Un contrato liquidado o anulado no admite otrosíes ni actas."])


def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un otrosí o un acta abiertos; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise ContractRejected(["Solo se edita o se borra un borrador o un rechazado."])
    if obj.status == AmendmentStatus.REJECTED and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != AmendmentStatus.DRAFT:
        raise ContractRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise ContractRejected([SELF_DECISION])


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise ContractRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise ContractRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def contract_metadata(contract: Contract) -> dict[str, Any]:
    return {
        "contract": str(contract.pk),
        "project": str(contract.project_id),
        "number": contract.number,
        "kind": contract.kind,
        "value": str(contract.current_value),
        "status": contract.status,
    }


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _pct_ok(value: Decimal) -> bool:
    return value.is_finite() and 0 <= value <= HUNDRED and value == value.quantize(PCT_STEP)


def line_errors(
    catalog: Catalog, tasks: Mapping[UUID, Task], lines: Sequence[LineData], *, first: int = 1
) -> list[str]:
    errors: list[str] = []
    for n, line in enumerate(lines, start=first):
        row = catalog.rows.get(line.item_id)
        if row is None or row.kind != BudgetRowKind.ITEM:
            errors.append(f"Línea {n}: elige un ítem de la versión vigente del presupuesto.")
        if any(task_id not in tasks for task_id in line.task_ids):
            errors.append(f"Línea {n}: las tareas deben ser tareas vigentes del proyecto.")
        if not 0 < line.quantity <= MAX_QUANTITY or quantity(line.quantity) != line.quantity:
            errors.append(f"Línea {n}: la cantidad es mayor que cero, con máximo 4 decimales.")
        if not 0 < line.unit_price <= MAX_PRICE or money(line.unit_price) != line.unit_price:
            errors.append(f"Línea {n}: el precio va en pesos y centavos y es mayor que cero.")
        if len(line.unit.strip()) > MAX_UNIT:
            errors.append(f"Línea {n}: la unidad pasa de {MAX_UNIT} caracteres.")
    return errors


def validate_contract(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: ContractData
) -> list[str]:
    errors: list[str] = []
    if data.kind not in ContractKind.values:
        errors.append("Elige el tipo de contrato.")
    for value, label, limit in (
        (data.number, "El número", MAX_NUMBER),
        (data.purpose, "El objeto", MAX_PURPOSE),
        (data.contractor, "El contratista", MAX_CONTRACTOR),
    ):
        if not value.strip() or len(value.strip()) > limit:
            errors.append(f"{label} es obligatorio y tiene máximo {limit} caracteres.")
    if not FIRST_DAY <= data.starts_on <= data.ends_on:
        errors.append("Las fechas van desde 2000 y el fin no es anterior al inicio.")
    if not _pct_ok(data.advance_pct):
        errors.append("El anticipo es un porcentaje entre 0 y 100, con máximo 4 decimales.")
    if not _pct_ok(data.retention_pct):
        errors.append(
            "La retención de garantía es un porcentaje entre 0 y 100, con máximo 4 decimales."
        )
    if not data.lines:
        errors.append("Agrega al menos una línea.")
    elif len(data.lines) > MAX_LINES:
        errors.append(f"Máximo {MAX_LINES} líneas por contrato.")
    errors += line_errors(catalog, tasks, data.lines)
    try:
        validate_charges(data.charges)
    except ChargeError as exc:
        errors.append(f"Cargos: {exc}")
    try:
        validate_deductions(data.deductions, len(data.charges))
    except ChargeError as exc:
        errors.append(f"Descuentos: {exc}")
    return errors


def _crosses(mine: frozenset[UUID], theirs: frozenset[UUID]) -> bool:
    return not mine or not theirs or bool(mine & theirs)


def find_overlaps(
    ctx: RequestContext,
    project: Project,
    lines: Sequence[LineData],
    catalog: Catalog,
    *,
    exclude: Contract | None = None,
) -> list[str]:
    """Traslapes de `lines` entre sí y con las líneas vigentes de los contratos en borrador o
    aprobados del proyecto (sin `exclude`). Dos consultas."""
    others = active_lines(
        scoped_qs(ctx, ContractLine).filter(
            contract__project=project,
            contract__status__in=[ContractStatus.DRAFT, ContractStatus.APPROVED],
            item_id__in={line.item_id for line in lines},
        )
    )
    if exclude is not None:
        others = others.exclude(contract=exclude)
    existing = [
        (row.item_id, frozenset(link.task_id for link in row.task_links.all()), row.contract.number)
        for row in others.select_related("contract").prefetch_related("task_links")
    ]
    found: list[str] = []
    for n, line in enumerate(lines, start=1):
        mine = frozenset(line.task_ids)
        label = catalog.label_of(line.item_id, "Un ítem")
        for item_id, theirs, number in existing:
            if item_id == line.item_id and _crosses(mine, theirs):
                found.append(f"{label}: el contrato {number} ya lo cubre en las mismas tareas.")
        for m, other in enumerate(lines[: n - 1], start=1):
            if other.item_id == line.item_id and _crosses(mine, frozenset(other.task_ids)):
                found.append(f"{label}: las líneas {m} y {n} cubren las mismas tareas.")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------


def _write_lines(
    ctx: RequestContext,
    contract: Contract,
    lines: Sequence[LineData],
    *,
    first_order: int = 1,
    amendment: ContractAmendment | None = None,
) -> None:
    created = scoped_qs(ctx, ContractLine).bulk_create(
        [
            ContractLine(
                org_id=ctx.org_id,
                contract=contract,
                order=first_order + n,
                item_id=line.item_id,
                unit=line.unit.strip(),
                quantity=line.quantity,
                current_quantity=line.quantity,
                unit_price=line.unit_price,
                amendment=amendment,
            )
            for n, line in enumerate(lines)
        ]
    )
    scoped_qs(ctx, ContractLineTask).bulk_create(
        [
            ContractLineTask(org_id=ctx.org_id, line=row, task_id=task_id)
            for row, line in zip(created, lines, strict=True)
            for task_id in dict.fromkeys(line.task_ids)
        ]
    )


def _write_terms(ctx: RequestContext, contract: Contract, data: ContractData) -> None:
    scoped_qs(ctx, ContractCharge).filter(contract=contract).delete()
    scoped_qs(ctx, ContractDeduction).filter(contract=contract).delete()
    scoped_qs(ctx, ContractCharge).bulk_create(
        [
            ContractCharge(
                org_id=ctx.org_id,
                contract=contract,
                order=c.order,
                name=c.name.strip(),
                pct=c.pct,
                base=c.base,
            )  # fmt: skip
            for c in data.charges
        ]
    )
    scoped_qs(ctx, ContractDeduction).bulk_create(
        [
            ContractDeduction(
                org_id=ctx.org_id,
                contract=contract,
                order=d.order,
                name=d.name.strip(),
                pct=d.pct,
                base=d.base,
            )  # fmt: skip
            for d in data.deductions
        ]
    )


def _contract_fields(data: ContractData) -> dict[str, Any]:
    return {
        "kind": data.kind,
        "number": data.number.strip(),
        "purpose": data.purpose.strip(),
        "contractor": data.contractor.strip(),
        "starts_on": data.starts_on,
        "ends_on": data.ends_on,
        "advance_pct": data.advance_pct,
        "retention_pct": data.retention_pct,
    }


def _checked_contract(
    ctx: RequestContext,
    project: Project,
    data: ContractData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    *,
    exclude: Contract | None = None,
) -> None:
    errors = validate_contract(catalog, tasks, data)
    if errors:
        raise ContractRejected(errors)
    if not data.confirm_overlap:
        overlaps = find_overlaps(ctx, project, data.lines, catalog, exclude=exclude)
        if overlaps:
            raise ContractRejected([], overlaps=overlaps)


def _number_taken(
    ctx: RequestContext, project: Project, number: str, exclude: UUID | None = None
) -> bool:
    found = scoped_qs(ctx, Contract).filter(project=project, number__iexact=number.strip())
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


def _require_draft(
    ctx: RequestContext, contract: Contract, policy: Callable[[RequestContext, Any], bool]
) -> None:
    if contract.status != ContractStatus.DRAFT:
        raise ContractRejected([APPROVED_IS_FINAL])
    if not policy(ctx, contract):
        raise PermissionDenied


def create_contract(
    ctx: RequestContext,
    project: Project,
    data: ContractData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Contract:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    _checked_contract(ctx, project, data, catalog, tasks)
    with transaction.atomic():
        if _number_taken(ctx, project, data.number):
            raise ContractRejected([f"Ya hay un contrato {data.number.strip()} en el proyecto."])
        contract = scoped_qs(ctx, Contract).create(
            org_id=ctx.org_id,
            project=project,
            status=ContractStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **_contract_fields(data),
        )
        _write_lines(ctx, contract, data.lines)
        _write_terms(ctx, contract, data)
        contract.current_value = refresh_value(ctx, contract)
        contract.save(update_fields=["current_value"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_CREATED,
            target=f"contract:{contract.pk}",
            metadata=contract_metadata(contract),
        )
    return contract


def update_contract(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: ContractData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Contract:
    """Guarda un borrador. Si tenía un rechazo, se limpia (eso es "reenviarlo")."""
    current = scoped_qs(ctx, Contract).filter(pk=contract_id, project=project).first()
    if current is None:
        raise Http404
    _require_draft(ctx, current, can_update_cost)
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    _checked_contract(ctx, project, data, catalog, tasks, exclude=current)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        _require_draft(ctx, contract, can_update_cost)
        if _number_taken(ctx, project, data.number, exclude=contract.pk):
            raise ContractRejected([f"Ya hay un contrato {data.number.strip()} en el proyecto."])
        for field, value in _contract_fields(data).items():
            setattr(contract, field, value)
        # Un borrador no tiene actas ni otrosíes: sus líneas se reemplazan enteras.
        scoped_qs(ctx, ContractLine).filter(contract=contract).delete()
        _write_lines(ctx, contract, data.lines)
        _write_terms(ctx, contract, data)
        resubmitted = contract.rejected_by_id is not None
        contract.current_value = refresh_value(ctx, contract)
        contract.rejected_by = None
        contract.rejected_at = None
        contract.rejection_note = ""
        contract.updated_by_id = ctx.user_id
        contract.save()
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_UPDATED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "resubmitted": resubmitted},
        )
    return contract


def delete_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        _require_draft(ctx, contract, can_delete_cost)
        metadata = {**contract_metadata(contract), "contractor": contract.contractor}
        scoped_qs(ctx, ContractLine).filter(contract=contract).delete()
        scoped_qs(ctx, ContractCharge).filter(contract=contract).delete()
        scoped_qs(ctx, ContractDeduction).filter(contract=contract).delete()
        target = f"contract:{contract.pk}"
        contract.delete()
        audit.record(ctx, action=AuditAction.CONTRACT_DELETED, target=target, metadata=metadata)


def approve_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    """Aprueba y fija el anticipo (valor vigente × %)."""
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.DRAFT:
            raise ContractRejected(["Solo se aprueba un contrato en borrador."])
        if ctx.user_id in (contract.created_by_id, contract.updated_by_id):
            raise ContractRejected([SELF_DECISION])
        if not scoped_qs(ctx, ContractLine).filter(contract=contract).exists():
            raise ContractRejected(["El contrato no tiene líneas."])
        contract.current_value = refresh_value(ctx, contract)
        contract.advance_amount = scaled(contract.current_value, contract.advance_pct, HUNDRED)
        contract.status = ContractStatus.APPROVED
        contract.approved_by_id = ctx.user_id
        contract.approved_at = timezone.now()
        contract.rejected_by = None
        contract.rejected_at = None
        contract.rejection_note = ""
        contract.save(
            update_fields=[
                "current_value", "advance_amount", "status", "approved_by", "approved_at",
                "rejected_by", "rejected_at", "rejection_note",
            ]
        )  # fmt: skip
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_APPROVED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "advance": str(contract.advance_amount)},
        )
    return contract


def reject_contract(
    ctx: RequestContext, project: Project, contract_id: UUID, note: str
) -> Contract:
    """Devuelve el borrador con el motivo (el contrato no tiene estado "rechazado")."""
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.DRAFT:
            raise ContractRejected(["Solo se rechaza un contrato en borrador."])
        if ctx.user_id in (contract.created_by_id, contract.updated_by_id):
            raise ContractRejected([SELF_DECISION])
        contract.rejected_by_id = ctx.user_id
        contract.rejected_at = timezone.now()
        contract.rejection_note = reason
        contract.save(update_fields=["rejected_by", "rejected_at", "rejection_note"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_REJECTED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "note": reason},
        )
    return contract


def void_contract(ctx: RequestContext, project: Project, contract_id: UUID, note: str) -> Contract:
    """Anula un contrato aprobado sin actas aprobadas ni otrosíes o actas abiertos."""
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.APPROVED:
            raise ContractRejected(["Solo se anula un contrato aprobado; un borrador se borra."])
        certificates = scoped_qs(ctx, ContractCertificate).filter(contract=contract)
        if certificates.filter(status="approved").exists():
            raise ContractRejected(
                ["El contrato tiene actas aprobadas: anúlalas primero o liquídalo."]
            )
        if (
            certificates.filter(status__in=OPEN_STATES).exists()
            or scoped_qs(ctx, ContractAmendment)
            .filter(contract=contract, status__in=OPEN_STATES)
            .exists()
        ):
            raise ContractRejected(["Borra o decide primero los otrosíes y las actas abiertos."])
        contract.status = ContractStatus.VOIDED
        contract.closed_by_id = ctx.user_id
        contract.closed_at = timezone.now()
        contract.close_note = reason
        contract.save(update_fields=["status", "closed_by", "closed_at", "close_note"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_VOIDED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "note": reason},
        )
    return contract


# ---------------------------------------------------------------------------
# Otrosíes
# ---------------------------------------------------------------------------


def _amendment_metadata(amendment: ContractAmendment) -> dict[str, Any]:
    return {
        "contract": str(amendment.contract_id),
        "project": str(amendment.project_id),
        "number": amendment.number,
        "status": amendment.status,
    }


def _amendment_errors(
    contract: Contract,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    data: AmendmentData,
    lines: Mapping[UUID, ContractLine],
) -> list[str]:
    errors: list[str] = []
    if not FIRST_DAY <= data.signed_on <= timezone.localdate():
        errors.append("La fecha del otrosí no puede ser futura ni anterior a 2000.")
    if not data.reason.strip() or len(data.reason.strip()) > MAX_NOTE:
        errors.append(f"El motivo es obligatorio y tiene máximo {MAX_NOTE} caracteres.")
    if data.new_ends_on is not None and data.new_ends_on < contract.starts_on:
        errors.append("La nueva fecha de fin no puede ser anterior al inicio del contrato.")
    if not (data.increases or data.new_lines or data.new_ends_on):
        errors.append("Un otrosí cambia algo: cantidades, líneas nuevas o la fecha de fin.")
    seen: set[UUID] = set()
    for increase in data.increases:
        if increase.line_id not in lines or increase.line_id in seen:
            errors.append("Los aumentos son de líneas vigentes del contrato, una vez cada una.")
        seen.add(increase.line_id)
        added = increase.added_quantity
        if not 0 < added <= MAX_QUANTITY or quantity(added) != added:
            errors.append("Un aumento es una cantidad mayor que cero, con máximo 4 decimales.")
    errors += line_errors(catalog, tasks, data.new_lines, first=len(lines) + 1)
    if len(lines) + len(data.new_lines) > MAX_LINES:
        errors.append(f"Máximo {MAX_LINES} líneas por contrato.")
    return list(dict.fromkeys(errors))


def _apply_amendment_data(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    amendment: ContractAmendment | None,
    data: AmendmentData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> ContractAmendment:
    """Valida y escribe el otrosí (nuevo si `amendment` es None) con el contrato bloqueado."""
    lines = {
        line.pk: line
        for line in active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
    }
    errors = _amendment_errors(contract, catalog, tasks, data, lines)
    if errors:
        raise ContractRejected(errors)
    if not data.confirm_overlap:
        overlaps = find_overlaps(ctx, project, data.new_lines, catalog)
        if overlaps:
            raise ContractRejected([], overlaps=overlaps)
    fields = {
        "signed_on": data.signed_on,
        "reason": data.reason.strip(),
        "new_ends_on": data.new_ends_on,
    }
    if amendment is None:
        top = scoped_qs(ctx, ContractAmendment).filter(contract=contract).aggregate(n=Max("number"))
        amendment = scoped_qs(ctx, ContractAmendment).create(
            org_id=ctx.org_id,
            contract=contract,
            project=project,
            number=(top["n"] or 0) + 1,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **fields,
        )
    else:
        for field, value in fields.items():
            setattr(amendment, field, value)
        amendment.status = AmendmentStatus.DRAFT
        amendment.decided_by = None
        amendment.decided_at = None
        amendment.decision_note = ""
        amendment.updated_by_id = ctx.user_id
        amendment.save()
        scoped_qs(ctx, ContractAmendmentLine).filter(amendment=amendment).delete()
        scoped_qs(ctx, ContractLine).filter(amendment=amendment).delete()
    scoped_qs(ctx, ContractAmendmentLine).bulk_create(
        [
            ContractAmendmentLine(
                org_id=ctx.org_id,
                amendment=amendment,
                line_id=increase.line_id,
                added_quantity=increase.added_quantity,
            )
            for increase in data.increases
        ]
    )
    top_order = scoped_qs(ctx, ContractLine).filter(contract=contract).aggregate(n=Max("order"))
    _write_lines(
        ctx, contract, data.new_lines, first_order=(top_order["n"] or 0) + 1, amendment=amendment
    )
    return amendment


def _locked_amendment(
    ctx: RequestContext, contract: Contract, amendment_id: UUID
) -> ContractAmendment:
    amendment = (
        scoped_qs(ctx, ContractAmendment)
        .select_for_update()
        .filter(pk=amendment_id, contract=contract)
        .first()
    )
    if amendment is None:
        raise Http404
    return amendment


def create_amendment(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: AmendmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> ContractAmendment:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        require_open(contract)
        if (
            scoped_qs(ctx, ContractAmendment)
            .filter(contract=contract, status__in=OPEN_STATES)
            .exists()
        ):
            raise ContractRejected(
                ["Ya hay un otrosí abierto en este contrato: termínalo primero."]
            )
        amendment = _apply_amendment_data(ctx, project, contract, None, data, catalog, tasks)
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_CREATED,
            target=f"amendment:{amendment.pk}",
            metadata=_amendment_metadata(amendment),
        )
    return amendment


def update_amendment(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    amendment_id: UUID,
    data: AmendmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> ContractAmendment:
    """Guarda un otrosí abierto; un rechazado (solo su autor) vuelve a borrador."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        require_editable(ctx, amendment, can_update_cost)
        require_open(contract)
        resubmitted = amendment.status == AmendmentStatus.REJECTED
        amendment = _apply_amendment_data(ctx, project, contract, amendment, data, catalog, tasks)
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_UPDATED,
            target=f"amendment:{amendment.pk}",
            metadata={**_amendment_metadata(amendment), "resubmitted": resubmitted},
        )
    return amendment


def delete_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID
) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        require_editable(ctx, amendment, can_delete_cost)
        metadata = {**_amendment_metadata(amendment), "reason": amendment.reason}
        target = f"amendment:{amendment.pk}"
        scoped_qs(ctx, ContractAmendmentLine).filter(amendment=amendment).delete()
        scoped_qs(ctx, ContractLine).filter(amendment=amendment).delete()
        amendment.delete()
        audit.record(ctx, action=AuditAction.AMENDMENT_DELETED, target=target, metadata=metadata)


def approve_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID
) -> ContractAmendment:
    """Aplica el otrosí: aumentos, líneas nuevas (desde ya vigentes), fecha de fin y valor."""
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, amendment)
        require_open(contract)
        for line_id, added in (
            scoped_qs(ctx, ContractAmendmentLine)
            .filter(amendment=amendment)
            .values_list("line_id", "added_quantity")
        ):
            line = scoped_qs(ctx, ContractLine).get(pk=line_id)
            line.current_quantity += added
            line.save(update_fields=["current_quantity"])
        amendment.status = AmendmentStatus.APPROVED
        amendment.decided_by_id = ctx.user_id
        amendment.decided_at = timezone.now()
        amendment.save(update_fields=["status", "decided_by", "decided_at"])
        before = contract.current_value
        if amendment.new_ends_on is not None:
            contract.ends_on = amendment.new_ends_on
        contract.current_value = refresh_value(ctx, contract)
        contract.save(update_fields=["ends_on", "current_value"])
        amendment.value_after = contract.current_value
        amendment.save(update_fields=["value_after"])
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_APPROVED,
            target=f"amendment:{amendment.pk}",
            metadata={
                **_amendment_metadata(amendment),
                "value_before": str(before),
                "value_after": str(contract.current_value),
            },
        )
    return amendment


def reject_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID, note: str
) -> ContractAmendment:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, amendment)
        amendment.status = AmendmentStatus.REJECTED
        amendment.decided_by_id = ctx.user_id
        amendment.decided_at = timezone.now()
        amendment.decision_note = reason
        amendment.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_REJECTED,
            target=f"amendment:{amendment.pk}",
            metadata={**_amendment_metadata(amendment), "note": reason},
        )
    return amendment

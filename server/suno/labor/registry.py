"""Registro de trabajadores, jornales y factor prestacional (costos, pieza 6d, spec 1.1 y 3.1).

- Escribir pide `can_manage_labor_registry` (create y update de Mano de obra en toda la
  organización).
- Datos mínimos (Ley 1581): documento, nombre, cargo, ingreso y retiro.
- Un cambio de jornal o de factor es una fila nueva; una fila que ya usó una planilla aprobada
  o anulada no se edita ni se borra (decisión 7 del plan).
- La auditoría lleva ids y el documento enmascarado, nunca nombres (decisión 25).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    BenefitFactor,
    DocumentType,
    ProjectWorker,
    TimesheetLine,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
from suno.core.models.labor import mask_document
from suno.core.policies import can_manage_labor_registry
from suno.core.scoping import scoped_qs

from .common import FROZEN_STATES, MAX_NAME, MAX_NOTE, MAX_POSITION, LaborRejected, lock_factors

MIN_DOCUMENT, MAX_DOCUMENT = 3, 20
MAX_WAGE = Decimal("999999999999.99")
FIRST_DAY = date(2000, 1, 1)
DOCUMENT_CONSTRAINT = "worker_document_uq"
USED = "Este valor ya lo usó una planilla aprobada o anulada: registra un cambio con otra fecha."


@dataclass(frozen=True)
class WorkerData:
    document_type: str
    document_number: str
    full_name: str
    position: str
    hired_on: date


def clean_document(number: str) -> str:
    """ "1.012.345.678" → "1012345678"; sin espacios ni guiones, en mayúsculas."""
    return "".join(ch for ch in number if ch not in ". -").upper()


def worker_errors(data: WorkerData) -> list[str]:
    errors: list[str] = []
    if data.document_type not in DocumentType.values:
        errors.append("Elige el tipo de documento (CC, CE, PPT, PEP o PA).")
    number = clean_document(data.document_number)
    if not (MIN_DOCUMENT <= len(number) <= MAX_DOCUMENT and number.isascii() and number.isalnum()):
        errors.append(
            f"El número de documento lleva de {MIN_DOCUMENT} a {MAX_DOCUMENT} letras o números."
        )
    if not data.full_name.strip():
        errors.append("Escribe el nombre.")
    elif len(data.full_name.strip()) > MAX_NAME:
        errors.append(f"El nombre pasa de {MAX_NAME} caracteres.")
    if len(data.position.strip()) > MAX_POSITION:
        errors.append(f"El cargo pasa de {MAX_POSITION} caracteres.")
    if not FIRST_DAY <= data.hired_on <= timezone.localdate():
        errors.append("La fecha de ingreso no puede ser futura ni anterior a 2000.")
    return errors


def wage_ok(value: Decimal | None) -> bool:
    return (
        value is not None and value.is_finite() and 0 < value <= MAX_WAGE and money(value) == value
    )


def factor_ok(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and 1 <= value <= 3
        and value.quantize(Decimal("0.0001")) == value
    )


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def _metadata(worker: Worker) -> dict[str, Any]:
    return {
        "worker": str(worker.pk),
        "document": f"{worker.document_type} {mask_document(worker.document_number)}",
    }


def _locked_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    found = scoped_qs(ctx, Worker).select_for_update(no_key=True).filter(pk=worker_id).first()
    if found is None:
        raise Http404
    return found


def _duplicate(ctx: RequestContext, data: WorkerData, exclude: UUID | None = None) -> str:
    number = clean_document(data.document_number)
    rows = scoped_qs(ctx, Worker).filter(document_type=data.document_type, document_number=number)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    if rows.exists():
        return f"Ya hay un trabajador con {data.document_type} {mask_document(number)}."
    return ""


def create_worker(ctx: RequestContext, data: WorkerData, daily_wage: Decimal) -> Worker:
    """Alta con su primer jornal, vigente desde la fecha de ingreso."""
    _require_manager(ctx)
    errors = worker_errors(data)
    if not wage_ok(daily_wage):
        errors.append("El jornal básico va en pesos, mayor que cero y a centavos.")
    if not errors and (problem := _duplicate(ctx, data)):
        errors.append(problem)
    if errors:
        raise LaborRejected(errors)
    try:
        with transaction.atomic():
            worker = scoped_qs(ctx, Worker).create(
                org_id=ctx.org_id,
                document_type=data.document_type,
                document_number=clean_document(data.document_number),
                full_name=data.full_name.strip(),
                position=data.position.strip(),
                hired_on=data.hired_on,
            )
            scoped_qs(ctx, WorkerWage).create(
                org_id=ctx.org_id,
                worker=worker,
                valid_from=data.hired_on,
                daily_wage=daily_wage,
                created_by_id=ctx.user_id,
            )
            audit.record(
                ctx,
                action=AuditAction.WORKER_CREATED,
                target=f"worker:{worker.pk}",
                metadata={**_metadata(worker), "wage": str(daily_wage)},
            )
    except IntegrityError as exc:
        if DOCUMENT_CONSTRAINT not in str(exc):
            raise
        raise LaborRejected([_duplicate(ctx, data) or "Ese documento ya existe."]) from exc
    return worker


def update_worker(ctx: RequestContext, worker_id: UUID, data: WorkerData) -> Worker:
    """Corrige documento, nombre y cargo. La fecha de ingreso no pasa del primer jornal ni de la
    primera asignación."""
    _require_manager(ctx)
    errors = worker_errors(data)
    if errors:
        raise LaborRejected(errors)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if problem := _duplicate(ctx, data, exclude=worker.pk):
            raise LaborRejected([problem])
        first_wage = scoped_qs(ctx, WorkerWage).filter(worker=worker).order_by("valid_from").first()
        first_assignment = (
            scoped_qs(ctx, ProjectWorker).filter(worker=worker).order_by("starts_on").first()
        )
        limits = [
            row
            for row in (
                first_wage.valid_from if first_wage else None,
                first_assignment.starts_on if first_assignment else None,
            )
            if row is not None
        ]
        if limits and data.hired_on > min(limits):
            raise LaborRejected(
                [f"La fecha de ingreso no puede pasar del {min(limits):%d/%m/%Y} (primer jornal "
                 "o primera asignación)."]
            )  # fmt: skip
        worker.document_type = data.document_type
        worker.document_number = clean_document(data.document_number)
        worker.full_name = data.full_name.strip()
        worker.position = data.position.strip()
        worker.hired_on = data.hired_on
        worker.save()
        audit.record(
            ctx, action=AuditAction.WORKER_UPDATED, target=f"worker:{worker.pk}",
            metadata=_metadata(worker),
        )  # fmt: skip
    return worker


def _last_tareo(ctx: RequestContext, worker: Worker) -> date | None:
    last = (
        scoped_qs(ctx, TimesheetLine)
        .filter(worker=worker)
        .exclude(timesheet__status=TimesheetStatus.REVERSED)
        .aggregate(day=Max("worked_on"))["day"]
    )
    return last


def retire_worker(ctx: RequestContext, worker_id: UUID, retired_on: date) -> Worker:
    """Lo inactiva y cierra en esa fecha las asignaciones abiertas o que terminan después."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if retired_on < worker.hired_on:
            raise LaborRejected(["El retiro no puede ser antes del ingreso."])
        last = _last_tareo(ctx, worker)
        if last is not None and retired_on < last:
            raise LaborRejected(
                [f"Tiene tareo hasta el {last:%d/%m/%Y}: el retiro va desde esa fecha."]
            )
        scoped_qs(ctx, ProjectWorker).filter(worker=worker).filter(
            Q(ends_on__isnull=True) | Q(ends_on__gt=retired_on), starts_on__lte=retired_on
        ).update(ends_on=retired_on, updated_by_id=ctx.user_id, updated_at=timezone.now())
        later = scoped_qs(ctx, ProjectWorker).filter(worker=worker, starts_on__gt=retired_on)
        if later.exists():
            raise LaborRejected(["Tiene asignaciones que empiezan después del retiro: bórralas."])
        worker.retired_on = retired_on
        worker.is_active = False
        worker.save(update_fields=["retired_on", "is_active", "updated_at"])
        audit.record(
            ctx, action=AuditAction.WORKER_RETIRED, target=f"worker:{worker.pk}",
            metadata={**_metadata(worker), "retired_on": retired_on.isoformat()},
        )  # fmt: skip
    return worker


def reinstate_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    """Deshace un retiro (corrección). Las asignaciones cerradas se reabren a mano."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        worker.retired_on = None
        worker.is_active = True
        worker.save(update_fields=["retired_on", "is_active", "updated_at"])
        audit.record(
            ctx, action=AuditAction.WORKER_UPDATED, target=f"worker:{worker.pk}",
            metadata={**_metadata(worker), "reinstated": True},
        )  # fmt: skip
    return worker


def delete_worker(ctx: RequestContext, worker_id: UUID) -> None:
    """Solo sin tareo (en ninguna planilla, ni anulada); se van sus jornales y asignaciones."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if scoped_qs(ctx, TimesheetLine).filter(worker=worker).exists():
            raise LaborRejected(["Tiene tareo: no se borra; retíralo."])
        metadata = _metadata(worker)
        scoped_qs(ctx, ProjectWorker).filter(worker=worker).delete()
        scoped_qs(ctx, WorkerWage).filter(worker=worker).delete()
        worker.delete()
        audit.record(
            ctx, action=AuditAction.WORKER_DELETED, target=f"worker:{metadata['worker']}",
            metadata=metadata,
        )  # fmt: skip


def _next_start(rows: Any, valid_from: date) -> date | None:
    return (
        rows.filter(valid_from__gt=valid_from)
        .order_by("valid_from")
        .values_list("valid_from", flat=True)
        .first()
    )


def _used(ctx: RequestContext, lines: Any, valid_from: date, until: date | None) -> bool:
    found = lines.filter(timesheet__status__in=FROZEN_STATES, worked_on__gte=valid_from)
    if until is not None:
        found = found.filter(worked_on__lt=until)
    return bool(found.exists())


def _require_after_frozen(lines: Any, valid_from: date) -> None:
    """Un jornal o un factor nuevo no puede regir sobre días de planillas aprobadas o anuladas:
    cambiaría el valor vigente de lo que ya se congeló (decisión 7 del plan)."""
    last = (
        lines.filter(timesheet__status__in=FROZEN_STATES, worked_on__gte=valid_from)
        .order_by("-worked_on")
        .values_list("worked_on", flat=True)
        .first()
    )
    if last is not None:
        raise LaborRejected(
            [f"Hay planillas aprobadas hasta el {last:%d/%m/%Y}: el valor nuevo rige desde el día "
             "siguiente."]
        )  # fmt: skip


def wage_in_use(ctx: RequestContext, wage: WorkerWage) -> bool:
    rows = scoped_qs(ctx, WorkerWage).filter(worker_id=wage.worker_id)
    lines = scoped_qs(ctx, TimesheetLine).filter(worker_id=wage.worker_id)
    return _used(ctx, lines, wage.valid_from, _next_start(rows, wage.valid_from))


def factor_in_use(ctx: RequestContext, factor: BenefitFactor) -> bool:
    rows = scoped_qs(ctx, BenefitFactor)
    return _used(ctx, scoped_qs(ctx, TimesheetLine), factor.valid_from,
                 _next_start(rows, factor.valid_from))  # fmt: skip


def _wage_audit(ctx: RequestContext, worker: Worker, change: str, wage: WorkerWage) -> None:
    audit.record(
        ctx,
        action=AuditAction.WORKER_WAGE_CHANGED,
        target=f"worker:{worker.pk}",
        metadata={
            **_metadata(worker),
            "change": change,
            "valid_from": wage.valid_from.isoformat(),
            "wage": str(wage.daily_wage),
        },
    )


def add_wage(
    ctx: RequestContext, worker_id: UUID, valid_from: date, daily_wage: Decimal
) -> WorkerWage:
    _require_manager(ctx)
    if not wage_ok(daily_wage):
        raise LaborRejected(["El jornal básico va en pesos, mayor que cero y a centavos."])
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if valid_from < worker.hired_on:
            raise LaborRejected(["El jornal no puede regir antes del ingreso."])
        if scoped_qs(ctx, WorkerWage).filter(worker=worker, valid_from=valid_from).exists():
            raise LaborRejected([f"Ya hay un jornal desde el {valid_from:%d/%m/%Y}."])
        _require_after_frozen(scoped_qs(ctx, TimesheetLine).filter(worker=worker), valid_from)
        wage = scoped_qs(ctx, WorkerWage).create(
            org_id=ctx.org_id, worker=worker, valid_from=valid_from, daily_wage=daily_wage,
            created_by_id=ctx.user_id,
        )  # fmt: skip
        _wage_audit(ctx, worker, "added", wage)
    return wage


def _locked_wage(ctx: RequestContext, wage_id: UUID) -> tuple[Worker, WorkerWage]:
    found = (
        scoped_qs(ctx, WorkerWage).filter(pk=wage_id).values_list("worker_id", flat=True).first()
    )
    if found is None:
        raise Http404
    worker = _locked_worker(ctx, found)
    wage = scoped_qs(ctx, WorkerWage).select_for_update().get(pk=wage_id)
    return worker, wage


def change_wage(ctx: RequestContext, wage_id: UUID, daily_wage: Decimal) -> WorkerWage:
    _require_manager(ctx)
    if not wage_ok(daily_wage):
        raise LaborRejected(["El jornal básico va en pesos, mayor que cero y a centavos."])
    with transaction.atomic():
        worker, wage = _locked_wage(ctx, wage_id)
        if wage_in_use(ctx, wage):
            raise LaborRejected([USED])
        wage.daily_wage = daily_wage
        wage.save(update_fields=["daily_wage"])
        _wage_audit(ctx, worker, "changed", wage)
    return wage


def delete_wage(ctx: RequestContext, wage_id: UUID) -> None:
    """Un trabajador conserva siempre su primer jornal (el de la fecha de ingreso)."""
    _require_manager(ctx)
    with transaction.atomic():
        worker, wage = _locked_wage(ctx, wage_id)
        if wage_in_use(ctx, wage):
            raise LaborRejected([USED])
        if wage.valid_from <= worker.hired_on:
            raise LaborRejected(["El jornal de ingreso no se borra: cámbialo."])
        _wage_audit(ctx, worker, "deleted", wage)
        wage.delete()


def _factor_audit(ctx: RequestContext, change: str, factor: BenefitFactor) -> None:
    audit.record(
        ctx,
        action=AuditAction.BENEFIT_FACTOR_CHANGED,
        target=f"benefit_factor:{factor.pk}",
        metadata={
            "change": change,
            "valid_from": factor.valid_from.isoformat(),
            "factor": str(factor.factor),
        },
    )


def _factor_checks(value: Decimal, note: str) -> None:
    errors = []
    if not factor_ok(value):
        errors.append("El factor va entre 1 y 3, con 4 decimales como mucho.")
    if len(note.strip()) > MAX_NOTE:
        errors.append(f"La nota pasa de {MAX_NOTE} caracteres.")
    if errors:
        raise LaborRejected(errors)


def add_factor(ctx: RequestContext, valid_from: date, value: Decimal, note: str) -> BenefitFactor:
    _require_manager(ctx)
    _factor_checks(value, note)
    with transaction.atomic():
        lock_factors(ctx)
        if scoped_qs(ctx, BenefitFactor).filter(valid_from=valid_from).exists():
            raise LaborRejected([f"Ya hay un factor desde el {valid_from:%d/%m/%Y}."])
        _require_after_frozen(scoped_qs(ctx, TimesheetLine), valid_from)
        factor = scoped_qs(ctx, BenefitFactor).create(
            org_id=ctx.org_id, valid_from=valid_from, factor=value, note=note.strip(),
            created_by_id=ctx.user_id,
        )  # fmt: skip
        factor.refresh_from_db()
        _factor_audit(ctx, "added", factor)
    return factor


def _locked_factor(ctx: RequestContext, factor_id: UUID) -> BenefitFactor:
    lock_factors(ctx)
    found = scoped_qs(ctx, BenefitFactor).filter(pk=factor_id).first()
    if found is None:
        raise Http404
    return found


def change_factor(ctx: RequestContext, factor_id: UUID, value: Decimal, note: str) -> BenefitFactor:
    _require_manager(ctx)
    _factor_checks(value, note)
    with transaction.atomic():
        factor = _locked_factor(ctx, factor_id)
        if factor_in_use(ctx, factor):
            raise LaborRejected([USED])
        factor.factor = value
        factor.note = note.strip()
        factor.save(update_fields=["factor", "note"])
        _factor_audit(ctx, "changed", factor)
    return factor


def delete_factor(ctx: RequestContext, factor_id: UUID) -> None:
    _require_manager(ctx)
    with transaction.atomic():
        factor = _locked_factor(ctx, factor_id)
        if factor_in_use(ctx, factor):
            raise LaborRejected([USED])
        _factor_audit(ctx, "deleted", factor)
        factor.delete()


def previous_day(day: date) -> date:
    return day - timedelta(days=1)

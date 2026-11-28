"""Nómina importada y conciliación (costos, pieza 6d, spec 1.3 y 2.6).

- La nómina del periodo llega en Excel (hoja "Nómina": Tipo de documento, Número de documento,
  Total pagado; "Nombre" se ignora) en dos pasos con nonce. Del archivo se guardan nombre,
  tamaño y SHA-256 (decisión 21 del plan). No toca el libro.
- Conciliación, por trabajador: jornales y costo estimado en las planillas **aprobadas** del
  periodo (no las anuladas ni los borradores), lo pagado, la diferencia y el porcentaje.
  Marca a los pagados sin tareo y a los tareados sin pago; los documentos que no están en el
  registro van aparte. Por proyecto, lo pagado se prorratea por los jornales tareados en el
  proyecto sobre los de toda la organización (decisión 22).
- Borrar una importación (#103) pide `can_delete_payroll` (quien la importó o quien aprueba
  tareo, decisión del 2026-11-28): se van sus líneas y, con ellas, su
  conciliación (es solo un reporte). Reemplazar es borrar e importar otra vez.
- Importar pide `can_manage_labor_registry`; ver, `can_read_labor_registry`. Cuatro consultas
  (cinco si hay líneas que al importar no estaban en el registro: se buscan otra vez por
  documento, para reconocer a quien se registró después).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.totals import WIDE_PRECISION, ZERO, money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    DocumentType,
    LaborImportKind,
    PayrollImport,
    PayrollImportLine,
    Project,
    TimesheetLine,
    TimesheetStatus,
    Worker,
)
from suno.core.models.labor import mask_document
from suno.core.policies import (
    can_delete_payroll,
    can_manage_labor_registry,
    can_read_labor_registry,
)
from suno.core.scoping import scoped_qs

from . import imports
from .calc import NO_DAYS
from .common import MAX_ERRORS, MAX_FILE_NAME, LaborRejected
from .registry import clean_document, wage_ok

SHEET = "Nómina"
H_TYPE, H_NUMBER, H_NAME, H_TOTAL = (
    "Tipo de documento", "Número de documento", "Nombre", "Total pagado",
)  # fmt: skip
HEADER = [H_TYPE, H_NUMBER, H_NAME, H_TOTAL]
REQUIRED = (H_TYPE, H_NUMBER, H_TOTAL)
MAX_ROWS = 5000
SALT = "suno.labor.payroll"
PAID_WITHOUT_TAREO = "Pagado sin tareo"
TAREO_WITHOUT_PAY = "Tareado sin pago"


@dataclass(frozen=True)
class PayrollRow:
    line: int
    document_type: str
    document_number: str
    total: Decimal


@dataclass(frozen=True)
class PayrollPreview:
    rows: tuple[PayrollRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""
    #: Cuántas filas tienen un documento del registro.
    known: int = 0

    @property
    def total(self) -> Decimal:
        return sum((row.total for row in self.rows), ZERO)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def preview(
    ctx: RequestContext, data: bytes, start: date, end: date, file_name: str
) -> PayrollPreview:
    _require_manager(ctx)
    if end < start:
        raise LaborRejected(["El periodo de la nómina termina antes de empezar."])
    try:
        name = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, name, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return PayrollPreview(errors=(str(exc),))
    if not table:
        return PayrollPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return PayrollPreview(errors=tuple(missing))
    registry = set(scoped_qs(ctx, Worker).values_list("document_type", "document_number"))
    rows: list[PayrollRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, values in enumerate(table[1:], start=2):
        kind, number, _name, total_value = imports.row_texts(values, found, HEADER)
        texts = [imports.cell_text(v) for v in (kind, number, total_value)]
        if not any(texts):
            continue
        problems: list[str] = []
        key = (texts[0].upper(), clean_document(texts[1]))
        if key[0] not in DocumentType.values:
            problems.append("El tipo de documento va como CC, CE, PPT, PEP o PA.")
        if not key[1] or len(key[1]) > 20:
            problems.append("Falta el número de documento o pasa de 20 caracteres.")
        if key in seen:
            problems.append(f"El documento se repite (fila {seen[key]}).")
        seen.setdefault(key, line)
        try:
            total = imports.cell_decimal(total_value)
        except ValueError:
            total = None
        if not wage_ok(total):
            problems.append("El total pagado va en pesos, mayor que cero y a centavos.")
        if problems or total is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            rows.append(PayrollRow(line, key[0], key[1], total))
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de nómina.")
    known = sum(1 for row in rows if (row.document_type, row.document_number) in registry)
    if errors:
        return PayrollPreview(rows=tuple(rows), errors=tuple(errors), known=known)
    body = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "file_name": file_name[:MAX_FILE_NAME],
        "file_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": [[r.line, r.document_type, r.document_number, str(r.total)] for r in rows],
    }
    return PayrollPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body), known=known)


def apply(ctx: RequestContext, token: str) -> PayrollImport:
    _require_manager(ctx)
    payload = imports.load(ctx, SALT, token)
    rows = [
        PayrollRow(int(line), kind, number, Decimal(total))
        for line, kind, number, total in payload.get("rows", [])
    ]
    if not rows:
        raise LaborRejected(["La vista previa no tiene filas."])
    with transaction.atomic():
        imports.consume(ctx, str(LaborImportKind.PAYROLL), payload["nonce"])
        workers = {(w.document_type, w.document_number): w.pk for w in scoped_qs(ctx, Worker)}
        imported = scoped_qs(ctx, PayrollImport).create(
            org_id=ctx.org_id,
            starts_on=date.fromisoformat(payload["start"]),
            ends_on=date.fromisoformat(payload["end"]),
            file_name=payload["file_name"],
            file_size=int(payload["file_size"]),
            file_sha256=payload["sha256"],
            line_count=len(rows),
            total=sum((row.total for row in rows), ZERO),
            imported_by_id=ctx.user_id,
        )
        scoped_qs(ctx, PayrollImportLine).bulk_create(
            [
                PayrollImportLine(
                    org_id=ctx.org_id,
                    payroll=imported,
                    row=row.line,
                    document_type=row.document_type,
                    document_number=row.document_number,
                    worker_id=workers.get((row.document_type, row.document_number)),
                    total_paid=row.total,
                )  # fmt: skip
                for row in rows
            ]
        )
        audit.record(
            ctx,
            action=AuditAction.PAYROLL_IMPORTED,
            target=f"payroll_import:{imported.pk}",
            metadata={
                "start": payload["start"],
                "end": payload["end"],
                "lines": len(rows),
                "total": str(imported.total),
                "matched": sum(1 for row in rows
                               if (row.document_type, row.document_number) in workers),
                "sha256": payload["sha256"],
            },
        )  # fmt: skip
    return imported


PAYROLL_GONE = "La nómina ya no existe: alguien la borró."


def delete_payroll(ctx: RequestContext, payroll_id: UUID) -> None:
    """Borra la nómina importada y sus líneas, con la fila bloqueada; queda en la auditoría
    qué se borró (periodo, líneas, total y huella del archivo; no el nombre del archivo, que
    puede llevar datos personales). Si ya no existe (o es de otra organización),
    `LaborRejected`: la vista decide el 404."""
    current = scoped_qs(ctx, PayrollImport).filter(pk=payroll_id).first()
    if current is None:
        raise LaborRejected([PAYROLL_GONE])
    if not can_delete_payroll(ctx, current):  # antes del `FOR UPDATE` (M5 de la 6d)
        raise PermissionDenied
    with transaction.atomic():
        imported = scoped_qs(ctx, PayrollImport).select_for_update().filter(pk=payroll_id).first()
        if imported is None:
            raise LaborRejected([PAYROLL_GONE])
        metadata = {
            "start": imported.starts_on.isoformat(),
            "end": imported.ends_on.isoformat(),
            "lines": imported.line_count,
            "total": str(imported.total),
            "sha256": imported.file_sha256,
            "imported_by": str(imported.imported_by_id),
        }
        target = f"payroll_import:{imported.pk}"
        scoped_qs(ctx, PayrollImportLine).filter(payroll=imported).delete()
        imported.delete()
        audit.record(ctx, action=AuditAction.PAYROLL_DELETED, target=target, metadata=metadata)


@dataclass(frozen=True)
class ReconRow:
    worker_id: UUID
    label: str
    position: str
    days: Decimal
    estimated: Decimal
    #: `None`: no aparece en la nómina.
    paid: Decimal | None

    @property
    def difference(self) -> Decimal:
        return (self.paid or ZERO) - self.estimated

    @property
    def pct(self) -> Decimal | None:
        """Diferencia sobre lo tareado, en %, con un decimal."""
        if not self.estimated:
            return None
        with localcontext() as context:
            context.prec = WIDE_PRECISION
            return (self.difference * 100 / self.estimated).quantize(Decimal("0.1"))

    @property
    def flag(self) -> str:
        if self.days == 0:
            return PAID_WITHOUT_TAREO
        if self.paid is None:
            return TAREO_WITHOUT_PAY
        return ""


@dataclass(frozen=True)
class UnknownLine:
    row: int
    document: str
    paid: Decimal


@dataclass(frozen=True)
class Reconciliation:
    payroll: PayrollImport
    project: Project | None
    rows: tuple[ReconRow, ...]
    unknown: tuple[UnknownLine, ...]
    #: Por proyecto: pagados sin tareo en toda la organización (no se pueden prorratear).
    excluded: int = 0

    @property
    def days(self) -> Decimal:
        return sum((row.days for row in self.rows), NO_DAYS)

    @property
    def estimated(self) -> Decimal:
        return sum((row.estimated for row in self.rows), ZERO)

    @property
    def paid(self) -> Decimal:
        return sum((row.paid or ZERO for row in self.rows), ZERO)

    @property
    def difference(self) -> Decimal:
        return self.paid - self.estimated


def _tareo(
    ctx: RequestContext, start: date, end: date, project: Project | None
) -> dict[UUID, tuple[Decimal, Decimal]]:
    rows = scoped_qs(ctx, TimesheetLine).filter(
        timesheet__status=TimesheetStatus.APPROVED, worked_on__gte=start, worked_on__lte=end
    )
    if project is not None:
        rows = rows.filter(timesheet__project=project)
    return {
        worker_id: (days or NO_DAYS, amount or ZERO)
        for worker_id, days, amount in rows.values("worker_id")
        .annotate(days=Sum("fraction"), amount=Sum("amount"))
        .values_list("worker_id", "days", "amount")
    }


def _registered_later(
    ctx: RequestContext, documents: list[tuple[str, str]]
) -> dict[tuple[str, str], UUID]:
    """Las líneas que al importar quedaron fuera del registro se buscan otra vez por documento:
    quien se registró después aparece en la conciliación. Una consulta (ninguna si no hay)."""
    if not documents:
        return {}
    wanted = set(documents)
    return {
        (kind, number): pk
        for pk, kind, number in scoped_qs(ctx, Worker)
        .filter(document_number__in={number for _, number in wanted})
        .values_list("pk", "document_type", "document_number")
        if (kind, number) in wanted
    }


def reconcile(
    ctx: RequestContext, payroll: PayrollImport, project: Project | None = None
) -> Reconciliation:
    if not can_read_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    lines = list(
        scoped_qs(ctx, PayrollImportLine)
        .filter(payroll=payroll)
        .values_list("row", "document_type", "document_number", "worker_id", "total_paid")
    )
    paid: dict[UUID, Decimal] = {}
    unknown: list[UnknownLine] = []
    later = _registered_later(ctx, [(kind, number) for _, kind, number, worker, _ in lines
                                    if worker is None])  # fmt: skip
    for row, kind, number, worker_id, total in lines:
        worker_id = worker_id or later.get((kind, number))
        if worker_id is None:
            unknown.append(UnknownLine(row, f"{kind} {mask_document(number)}", total))
        else:
            paid[worker_id] = total
    everywhere = _tareo(ctx, payroll.starts_on, payroll.ends_on, None)
    here = _tareo(ctx, payroll.starts_on, payroll.ends_on, project) if project else everywhere
    excluded = 0
    shown: dict[UUID, Decimal | None] = {}
    for worker_id, total in paid.items():
        if project is None:
            shown[worker_id] = total
            continue
        org_days = everywhere.get(worker_id, (NO_DAYS, ZERO))[0]
        if not org_days:
            excluded += 1
            continue
        project_days = here.get(worker_id, (NO_DAYS, ZERO))[0]
        if project_days:
            with localcontext() as context:
                context.prec = WIDE_PRECISION
                shown[worker_id] = money(total * project_days / org_days)
    for worker_id in here:
        shown.setdefault(worker_id, None)
    workers: Mapping[UUID, Any] = {
        w.pk: w for w in scoped_qs(ctx, Worker).filter(pk__in=list(shown))
    }
    rows = [
        ReconRow(
            worker_id=worker_id,
            label=workers[worker_id].label,
            position=workers[worker_id].position,
            days=here.get(worker_id, (NO_DAYS, ZERO))[0],
            estimated=here.get(worker_id, (NO_DAYS, ZERO))[1],
            paid=amount,
        )
        for worker_id, amount in shown.items()
        if worker_id in workers
    ]
    rows.sort(key=lambda row: row.label)
    return Reconciliation(payroll, project, tuple(rows), tuple(unknown), excluded)

"""Importación de una versión de presupuesto desde Excel, en dos pasos.

1. `create_upload` guarda el `.xlsx` como versión pendiente. `prepare_version`
   lo lee con el mapeo de columnas, calcula el resumen de cambios contra la
   versión vigente, guarda el mapeo en el presupuesto y deja los cargos listos
   para editar (vista previa). `update_pending` guarda etiqueta y cargos.
2. `apply_version`: en una transacción, con el presupuesto bloqueado, vuelve a
   leer el archivo con el mapeo de la vista previa, crea los ítems nuevos y los
   valores de la versión, calcula cargos y totales y la numera. La primera
   aplicada queda como contractual. `discard_version` la descarta.

`mark_contractual` cambia la versión contractual. Todas las escrituras toman
el mismo bloqueo del presupuesto (`select_for_update(nowait=True)`): dos
importaciones simultáneas no se esperan, la segunda se rechaza con un mensaje.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from django.db import OperationalError, transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    QuantityPlan,
)
from visitacampo.core.scoping import scoped_qs

from .diff import Values, summarize_changes
from .excel import (
    MAX_BYTES,
    ExcelRejected,
    ParsedBudget,
    check_file_name,
    parse_budget,
    preview_sheet,
)
from .mapping import ColumnMapping, MappingError
from .totals import ChargeError, ChargeSpec, compute_charges, default_charges, validate_charges

#: SQLSTATE de `FOR UPDATE NOWAIT` cuando la fila ya está bloqueada.
_LOCK_NOT_AVAILABLE = "55P03"
BUSY = "Otra importación de este presupuesto está en curso; intenta de nuevo en unos segundos."


class ImportRejected(Exception):
    """La importación no se puede hacer; el mensaje es para el usuario."""


def create_upload(
    ctx: RequestContext, budget: Budget, *, file_name: str, data: bytes
) -> BudgetVersion:
    """Guarda el archivo como versión pendiente, todavía sin leer (sin `summary`)."""
    if budget.org_id != ctx.org_id:
        raise Http404("Presupuesto no encontrado.")
    if len(data) > MAX_BYTES:
        raise ImportRejected("El archivo supera 20 MB.")
    try:
        check_file_name(file_name)
        preview_sheet(data, rows=1)  # un .xlsx legible, antes de guardarlo
    except ExcelRejected as exc:
        raise ImportRejected(str(exc)) from exc
    return scoped_qs(ctx, BudgetVersion).create(
        org_id=ctx.org_id,
        budget=budget,
        file_name=file_name[:255],
        source_file=data,
        uploaded_by_id=ctx.user_id,
    )


def current_version(ctx: RequestContext, budget: Budget) -> BudgetVersion | None:
    """La versión vigente: la última aplicada (sin cargar el archivo)."""
    return (
        scoped_qs(ctx, BudgetVersion)
        .filter(budget=budget, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("-number")
        .first()
    )


def charges_of(ctx: RequestContext, version: BudgetVersion) -> list[ChargeSpec]:
    return [
        ChargeSpec(order=c.order, name=c.name, pct=c.pct, base=c.base)
        for c in scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order")
    ]


def _values_by_code(ctx: RequestContext, version: BudgetVersion | None) -> dict[str, Values]:
    if version is None:
        return {}
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version)
        .values_list("item__code", "kind", "quantity", "unit_price", "total", "description")
    )
    return {
        code: Values(kind, qty, price, total, description)
        for code, kind, qty, price, total, description in rows
    }


def _parse(data: bytes, mapping: ColumnMapping) -> ParsedBudget:
    try:
        return parse_budget(data, mapping)
    except ExcelRejected as exc:
        raise ImportRejected(str(exc)) from exc


def _locked_budget(ctx: RequestContext, budget_id: UUID) -> Budget:
    """El presupuesto bloqueado para esta transacción, sin esperar a otra."""
    try:
        return scoped_qs(ctx, Budget).select_for_update(nowait=True).get(pk=budget_id)
    except Budget.DoesNotExist as exc:
        raise Http404("Presupuesto no encontrado.") from exc
    except OperationalError as exc:
        if getattr(exc.__cause__, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise ImportRejected(BUSY) from exc
        raise


def _locked_pending(
    ctx: RequestContext, version_id: UUID, *, with_file: bool = False
) -> tuple[BudgetVersion, Budget]:
    """La versión pendiente y su presupuesto, bloqueados. Sin el archivo (hasta
    20 MB) salvo que haga falta leerlo."""
    versions = scoped_qs(ctx, BudgetVersion)
    try:
        budget_id = versions.values_list("budget_id", flat=True).get(pk=version_id)
    except BudgetVersion.DoesNotExist as exc:
        raise ImportRejected("La versión no existe.") from exc
    budget = _locked_budget(ctx, budget_id)
    locked = versions.select_for_update()
    version = (locked if with_file else locked.defer("source_file")).get(pk=version_id)
    if version.status != BudgetVersionStatus.PENDING:
        raise ImportRejected("Esta versión ya fue aplicada o descartada.")
    return version, budget


def _replace_charges(
    ctx: RequestContext, version: BudgetVersion, charges: Sequence[ChargeSpec]
) -> None:
    qs = scoped_qs(ctx, BudgetCharge)
    qs.filter(version=version).delete()
    qs.bulk_create(
        [
            BudgetCharge(
                org_id=ctx.org_id,
                version=version,
                order=c.order,
                name=c.name.strip(),
                pct=c.pct,
                base=c.base,
            )
            for c in charges
        ]
    )


def _matrix_warning(
    ctx: RequestContext, budget: Budget, current: Mapping[str, Values], parsed: ParsedBudget
) -> str | None:
    """Ítems de la versión vigente que la nueva quita y que están en la matriz o tienen
    cantidades validadas en campo (costos, pieza 4)."""
    # Import local: `matrix.quantities` importa `budgets` (vía `matrix.progress`); un
    # import de módulo cerraría el ciclo `budgets.importer` → `matrix` → `budgets`.
    from visitacampo.matrix.quantities import executed_by_pair

    new_codes = {row.code for row in parsed.rows}
    gone = {code for code, values in current.items() if values.kind == "item"} - new_codes
    if not gone:
        return None
    ids = dict(
        scoped_qs(ctx, BudgetItem).filter(budget=budget, code__in=gone).values_list("pk", "code")
    )
    used = set(
        scoped_qs(ctx, QuantityPlan).filter(item_id__in=ids).values_list("item_id", flat=True)
    )
    executed = {item_id for (_, item_id) in executed_by_pair(ctx, project_ids=[budget.project_id])}
    codes = sorted(code for pk, code in ids.items() if pk in used or pk in executed)
    if not codes:
        return None
    return (
        f"{len(codes)} ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: "
        + ", ".join(codes[:20])
        + ". Sus filas de la matriz se conservan marcadas como retiradas del presupuesto."
    )


def prepare_version(ctx: RequestContext, version_id: UUID, mapping: ColumnMapping) -> BudgetVersion:
    """Lee la versión pendiente con `mapping` y deja lista su vista previa."""
    with transaction.atomic():
        version, budget = _locked_pending(ctx, version_id, with_file=True)
        parsed = _parse(version.source_file, mapping)
        current = current_version(ctx, budget)
        known = set(scoped_qs(ctx, BudgetItem).filter(budget=budget).values_list("code", flat=True))
        values = _values_by_code(ctx, current)
        summary = summarize_changes(values, parsed, known)
        warning = _matrix_warning(ctx, budget, values, parsed)
        if warning is not None:
            summary.setdefault("warnings", []).append(warning)
        summary["mapping"] = mapping.to_json()
        # Versión sobre la que se calculó la vista previa; si cambia, hay que re-subir.
        summary["base_number"] = current.number if current else None
        summary["previous_total"] = (
            str(current.total) if current is not None and current.total is not None else None
        )
        version.summary = summary
        version.save(update_fields=["summary"])
        budget.column_mapping = mapping.to_json()
        budget.save(update_fields=["column_mapping"])
        if not scoped_qs(ctx, BudgetCharge).filter(version=version).exists():
            template = charges_of(ctx, current) if current is not None else default_charges()
            _replace_charges(ctx, version, template)
        return version


def update_pending(
    ctx: RequestContext, version_id: UUID, *, label: str, charges: Sequence[ChargeSpec]
) -> BudgetVersion:
    """Guarda la etiqueta y los cargos de una versión pendiente."""
    try:
        validate_charges(charges)
    except ChargeError as exc:
        raise ImportRejected(str(exc)) from exc
    with transaction.atomic():
        version, _ = _locked_pending(ctx, version_id)
        version.label = label.strip()[:200]
        version.save(update_fields=["label"])
        _replace_charges(ctx, version, charges)
        return version


def discard_version(ctx: RequestContext, version_id: UUID) -> BudgetVersion:
    with transaction.atomic():
        version, _ = _locked_pending(ctx, version_id)
        version.status = BudgetVersionStatus.DISCARDED
        version.save(update_fields=["status"])
        return version


def _ensure_items(
    ctx: RequestContext, budget: Budget, parsed: ParsedBudget
) -> dict[str, BudgetItem]:
    """Los `BudgetItem` de cada código: los existentes (aunque hayan sido
    eliminados en versiones anteriores, así vuelven con la misma identidad) y
    los nuevos."""
    qs = scoped_qs(ctx, BudgetItem)
    items = {item.code: item for item in qs.filter(budget=budget)}
    new = [
        BudgetItem(org_id=ctx.org_id, budget=budget, code=row.code)
        for row in parsed.rows
        if row.code not in items
    ]
    qs.bulk_create(new, batch_size=1000)
    items.update({item.code: item for item in new})
    return items


def apply_version(ctx: RequestContext, version_id: UUID) -> BudgetVersion:
    with transaction.atomic():
        version, budget = _locked_pending(ctx, version_id, with_file=True)
        summary = version.summary
        if "mapping" not in summary:
            raise ImportRejected("Falta confirmar el mapeo de columnas de esta versión.")
        current = current_version(ctx, budget)
        if (current.number if current else None) != summary.get("base_number"):
            raise ImportRejected(
                "Se aplicó otra versión de este presupuesto después de subir esta. "
                "Vuelve a subir el archivo para ver los cambios actualizados."
            )
        try:
            mapping = ColumnMapping.from_json(summary["mapping"])
        except MappingError as exc:
            raise ImportRejected(str(exc)) from exc
        parsed = _parse(version.source_file, mapping)
        items = _ensure_items(ctx, budget, parsed)
        scoped_qs(ctx, BudgetItemVersion).bulk_create(
            [
                BudgetItemVersion(
                    org_id=ctx.org_id,
                    version=version,
                    item=items[row.code],
                    kind=row.kind,
                    sort_order=row.sort_order,
                    description=row.description,
                    unit=row.unit,
                    quantity=row.quantity,
                    unit_price=row.unit_price,
                    total=row.total,
                    parent=items[row.parent_code] if row.parent_code else None,
                )
                for row in parsed.rows
            ],
            batch_size=1000,
        )

        charges = list(scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order"))
        try:
            result = compute_charges(
                parsed.direct_cost,
                [ChargeSpec(c.order, c.name, c.pct, c.base) for c in charges],
            )
        except ChargeError as exc:
            raise ImportRejected(str(exc)) from exc
        amounts = {line.order: line.amount for line in result.lines}
        for charge in charges:
            charge.amount = amounts[charge.order]
        scoped_qs(ctx, BudgetCharge).bulk_update(charges, ["amount"])

        versions = scoped_qs(ctx, BudgetVersion).filter(budget=budget)
        version.number = (versions.aggregate(n=Max("number"))["n"] or 0) + 1
        version.status = BudgetVersionStatus.APPLIED
        version.applied_at = timezone.now()
        version.direct_cost = result.direct_cost
        version.total = result.total
        version.save(update_fields=["number", "status", "applied_at", "direct_cost", "total"])
        if budget.contractual_version_id is None:
            budget.contractual_version = version
            budget.save(update_fields=["contractual_version"])
        return version


def mark_contractual(
    ctx: RequestContext, budget_id: UUID, version_id: UUID
) -> tuple[int | None, BudgetVersion]:
    """Hace contractual una versión aplicada. Devuelve el número de la anterior."""
    with transaction.atomic():
        budget = _locked_budget(ctx, budget_id)
        try:
            version = (
                scoped_qs(ctx, BudgetVersion)
                .defer("source_file")
                .get(pk=version_id, budget=budget, status=BudgetVersionStatus.APPLIED)
            )
        except BudgetVersion.DoesNotExist as exc:
            raise ImportRejected(
                "Solo una versión aplicada de este presupuesto puede ser la contractual."
            ) from exc
        previous: int | None = (
            scoped_qs(ctx, BudgetVersion)
            .filter(pk=budget.contractual_version_id)
            .values_list("number", flat=True)
            .first()
        )
        budget.contractual_version = version
        budget.save(update_fields=["contractual_version"])
        return previous, version

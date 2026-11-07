"""Lecturas del presupuesto para mostrar o exportar, con coste fijo en consultas.

- `build_comparison`: contractual contra vigente, fila a fila, con etiquetas,
  diferencias, cargos y totales. **3 consultas**, sin importar el tamaño
  (versiones aplicadas; filas de las dos versiones con `values()`; cargos).
- `filter_rows`: capítulo, "solo con cambios" y búsqueda (en memoria),
  conservando los capítulos ancestros de cada fila que pasa el filtro.
- `version_rows`: el contenido de una versión. 1 consulta.
- `budget_summaries`: los presupuestos de un proyecto con sus totales y el
  total del proyecto. 2 consultas.

Ninguna carga `source_file` (hasta 20 MB por versión).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from suno.core.context import RequestContext
from suno.core.models import (
    Budget,
    BudgetCharge,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
)
from suno.core.scoping import scoped_qs

from .diff import LABEL_CLASSES, Values, row_labels
from .excel import KIND_CHAPTER, KIND_ITEM
from .mapping import fold
from .totals import ZERO, describe_base, pct_change, pct_text

_ROW_FIELDS = (
    "version_id", "item_id", "item__code", "kind", "sort_order", "description",
    "unit", "quantity", "unit_price", "total", "parent_id",
)  # fmt: skip


@dataclass(frozen=True)
class Side:
    """Los valores de una fila en una de las dos versiones."""

    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal


@dataclass(frozen=True)
class ComparisonRow:
    item_id: UUID
    code: str
    kind: str
    description: str
    unit: str
    parent_id: UUID | None
    #: Capítulo de primer nivel que la contiene (ella misma si es de primer nivel).
    root_id: UUID
    depth: int
    contractual: Side | None
    current: Side | None
    diff: Decimal
    diff_pct: Decimal | None
    labels: tuple[str, ...]

    @property
    def badges(self) -> list[tuple[str, str]]:
        """(etiqueta, sufijo de clase CSS) para la plantilla."""
        return [(label, LABEL_CLASSES[label]) for label in self.labels]


@dataclass(frozen=True)
class FooterRow:
    label: str
    note: str
    contractual: Decimal | None
    current: Decimal | None
    diff: Decimal
    diff_pct: Decimal | None
    strong: bool = False


@dataclass(frozen=True)
class Comparison:
    contractual: BudgetVersion | None
    current: BudgetVersion | None
    rows: list[ComparisonRow]
    footer: list[FooterRow]

    @property
    def chapters(self) -> list[ComparisonRow]:
        return [r for r in self.rows if r.depth == 0 and r.kind == KIND_CHAPTER]

    @property
    def item_count(self) -> int:
        return sum(1 for r in self.rows if r.kind == KIND_ITEM)

    @property
    def changed_count(self) -> int:
        return sum(1 for r in self.rows if r.kind == KIND_ITEM and r.labels)

    @property
    def total(self) -> FooterRow | None:
        return self.footer[-1] if self.footer else None


@dataclass(frozen=True)
class VersionRow:
    item_id: UUID
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    parent_id: UUID | None
    depth: int


@dataclass(frozen=True)
class BudgetSummary:
    budget: Budget
    contractual_total: Decimal | None
    current_total: Decimal | None
    current_number: int | None

    @property
    def diff(self) -> Decimal | None:
        if self.contractual_total is None or self.current_total is None:
            return None
        return self.current_total - self.contractual_total

    @property
    def diff_pct(self) -> Decimal | None:
        if self.contractual_total is None or self.current_total is None:
            return None
        return pct_change(self.contractual_total, self.current_total)


@dataclass(frozen=True)
class ProjectBudgetTotals:
    contractual: Decimal
    current: Decimal

    @property
    def diff(self) -> Decimal:
        return self.current - self.contractual

    @property
    def diff_pct(self) -> Decimal | None:
        return pct_change(self.contractual, self.current)


def _tree(parents: Mapping[UUID, UUID | None]) -> tuple[dict[UUID, int], dict[UUID, UUID]]:
    """Profundidad y raíz de cada fila a partir de su padre (en memoria).

    Un padre que no está en `parents` cuenta como raíz; un ciclo (no debería
    haberlo: los padres salen de prefijos de código) también se corta.
    """
    depth: dict[UUID, int] = {}
    root: dict[UUID, UUID] = {}
    for node in parents:
        chain: list[UUID] = []
        seen: set[UUID] = set()
        current: UUID | None = node
        while current is not None and current not in depth and current not in seen:
            chain.append(current)
            seen.add(current)
            parent = parents.get(current)
            current = parent if parent is not None and parent in parents else None
        if current is None or current in seen:
            top = chain.pop()
            depth[top], root[top] = 0, top
            base_depth, base_root = 0, top
        else:
            base_depth, base_root = depth[current], root[current]
        for item in reversed(chain):
            base_depth += 1
            depth[item], root[item] = base_depth, base_root
    return depth, root


def _side(row: Mapping[str, Any] | None) -> Side | None:
    return None if row is None else Side(row["quantity"], row["unit_price"], row["total"])


def _values(row: Mapping[str, Any] | None) -> Values | None:
    if row is None:
        return None
    return Values(row["kind"], row["quantity"], row["unit_price"], row["total"])


def _footer_row(
    label: str,
    note: str,
    contractual: Decimal | None,
    current: Decimal | None,
    *,
    strong: bool = False,
) -> FooterRow:
    diff = (current or ZERO) - (contractual or ZERO)
    pct = pct_change(contractual, current or ZERO) if contractual is not None else None
    return FooterRow(label, note, contractual, current, diff, pct, strong)


def _charge_note(slot: Mapping[str, BudgetCharge], names: Mapping[UUID, dict[int, str]]) -> str:
    ref = slot.get("current") or slot["contractual"]
    base = describe_base(ref.base, names.get(ref.version_id, {}))
    pcts = [slot[key].pct for key in ("contractual", "current") if key in slot]
    if len(set(pcts)) > 1:
        return f"contractual {pct_text(pcts[0])} %, vigente {pct_text(pcts[1])} % sobre {base}"
    return f"{pct_text(ref.pct)} % sobre {base}"


def build_comparison(ctx: RequestContext, budget: Budget) -> Comparison:
    """Contractual contra vigente, en el orden de la vigente. 3 consultas."""
    applied = list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget=budget, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("number")
    )
    current = applied[-1] if applied else None
    contractual = next((v for v in applied if v.pk == budget.contractual_version_id), None)
    ids = {v.pk for v in (contractual, current) if v is not None}
    if not ids:
        return Comparison(contractual=None, current=None, rows=[], footer=[])

    con: dict[UUID, dict[str, Any]] = {}
    cur: dict[UUID, dict[str, Any]] = {}
    for row in (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version_id__in=ids)
        .order_by("sort_order")
        .values(*_ROW_FIELDS)
    ):
        if contractual is not None and row["version_id"] == contractual.pk:
            con[row["item_id"]] = row
        if current is not None and row["version_id"] == current.pk:
            cur[row["item_id"]] = row
    charges = list(scoped_qs(ctx, BudgetCharge).filter(version_id__in=ids).order_by("order"))

    # Orden: el de la vigente; una fila eliminada va tras su hermano anterior
    # en el orden contractual (o al principio si no lo tiene).
    keys: dict[UUID, tuple[int, int, int]] = {
        item_id: (row["sort_order"], 0, 0) for item_id, row in cur.items()
    }
    anchor = -1
    for item_id, row in con.items():
        if item_id in cur:
            anchor = cur[item_id]["sort_order"]
        else:
            keys[item_id] = (anchor, 1, row["sort_order"])
    ordered = sorted(keys, key=keys.__getitem__)
    parents = {item_id: (cur.get(item_id) or con[item_id])["parent_id"] for item_id in ordered}
    depth, root = _tree(parents)

    rows: list[ComparisonRow] = []
    for item_id in ordered:
        before, after = con.get(item_id), cur.get(item_id)
        base = after or con[item_id]
        old = before["total"] if before else ZERO
        new = after["total"] if after else ZERO
        rows.append(
            ComparisonRow(
                item_id=item_id,
                code=base["item__code"],
                kind=base["kind"],
                description=base["description"],
                unit=base["unit"],
                parent_id=parents[item_id],
                root_id=root[item_id],
                depth=depth[item_id],
                contractual=_side(before),
                current=_side(after),
                diff=new - old,
                diff_pct=pct_change(old, new) if before else None,
                labels=row_labels(_values(before), _values(after)),
            )
        )

    footer = [
        _footer_row(
            "Costo directo",
            "",
            contractual.direct_cost if contractual else None,
            current.direct_cost if current else None,
        )
    ]
    names: dict[UUID, dict[int, str]] = {}
    slots: dict[int, dict[str, BudgetCharge]] = {}
    for charge in charges:
        names.setdefault(charge.version_id, {})[charge.order] = charge.name
        slot = slots.setdefault(charge.order, {})
        if contractual is not None and charge.version_id == contractual.pk:
            slot["contractual"] = charge
        if current is not None and charge.version_id == current.pk:
            slot["current"] = charge
    for order in sorted(slots):
        slot = slots[order]
        ref = slot.get("current") or slot["contractual"]
        footer.append(
            _footer_row(
                ref.name,
                _charge_note(slot, names),
                slot["contractual"].amount if "contractual" in slot else None,
                slot["current"].amount if "current" in slot else None,
            )
        )
    footer.append(
        _footer_row(
            "Total",
            "",
            contractual.total if contractual else None,
            current.total if current else None,
            strong=True,
        )
    )
    return Comparison(contractual=contractual, current=current, rows=rows, footer=footer)


def filter_rows(
    rows: Sequence[ComparisonRow],
    *,
    chapter_id: UUID | None = None,
    changes_only: bool = False,
    query: str = "",
) -> list[ComparisonRow]:
    """Las filas que pasan los filtros, más los capítulos que las contienen."""
    needle = fold(query.strip())
    by_id = {r.item_id: r for r in rows}
    keep: set[UUID] = set()
    for r in rows:
        if chapter_id is not None and r.root_id != chapter_id:
            continue
        if changes_only and not r.labels:
            continue
        if needle and needle not in fold(f"{r.code} {r.description}"):
            continue
        keep.add(r.item_id)
        parent = r.parent_id
        while parent is not None and parent not in keep and parent in by_id:
            keep.add(parent)
            parent = by_id[parent].parent_id
    return [r for r in rows if r.item_id in keep]


def version_rows(ctx: RequestContext, version: BudgetVersion) -> list[VersionRow]:
    """El contenido de una versión, en su orden. 1 consulta."""
    values = list(
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version)
        .order_by("sort_order")
        .values(
            "item_id",
            "item__code",
            "kind",
            "description",
            "unit",
            "quantity",
            "unit_price",
            "total",
            "parent_id",
        )  # fmt: skip
    )
    depth, _ = _tree({v["item_id"]: v["parent_id"] for v in values})
    return [
        VersionRow(
            item_id=v["item_id"],
            code=v["item__code"],
            kind=v["kind"],
            description=v["description"],
            unit=v["unit"],
            quantity=v["quantity"],
            unit_price=v["unit_price"],
            total=v["total"],
            parent_id=v["parent_id"],
            depth=depth[v["item_id"]],
        )
        for v in values
    ]


def budget_summaries(
    ctx: RequestContext, project: Project
) -> tuple[list[BudgetSummary], ProjectBudgetTotals]:
    """Los presupuestos del proyecto con sus totales y el total del proyecto. 2 consultas."""
    budgets = list(scoped_qs(ctx, Budget).filter(project=project).order_by("name"))
    totals_by_version: dict[UUID, Decimal] = {}
    latest: dict[UUID, tuple[int, Decimal]] = {}
    for budget_id, version_id, number, total in (
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project=project, status=BudgetVersionStatus.APPLIED)
        .values_list("budget_id", "id", "number", "total")
    ):
        totals_by_version[version_id] = total
        if budget_id not in latest or number > latest[budget_id][0]:
            latest[budget_id] = (number, total)
    summaries = [
        BudgetSummary(
            budget=budget,
            contractual_total=totals_by_version.get(budget.contractual_version_id),
            current_total=latest[budget.pk][1] if budget.pk in latest else None,
            current_number=latest[budget.pk][0] if budget.pk in latest else None,
        )
        for budget in budgets
    ]
    project_totals = ProjectBudgetTotals(
        contractual=sum((s.contractual_total or ZERO for s in summaries), ZERO),
        current=sum((s.current_total or ZERO for s in summaries), ZERO),
    )
    return summaries, project_totals

"""Qué cambió entre dos versiones de un presupuesto (puro).

- `row_labels`: las etiquetas de una fila en la comparación contractual contra
  vigente (*Mayor cantidad*, *Menor cantidad*, *Cambio de precio*, *No
  previsto*, *Eliminado*). Un ítem puede llevar dos (cantidad y precio); un
  capítulo solo *No previsto* o *Eliminado*: su diferencia se ve en el total.
- `summarize_changes`: el resumen de la vista previa de importación, contra la
  versión vigente. Cuenta solo ítems; los capítulos son estructura.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .excel import KIND_ITEM, ParsedBudget
from .totals import ZERO

LABEL_MORE = "Mayor cantidad"
LABEL_LESS = "Menor cantidad"
LABEL_PRICE = "Cambio de precio"
LABEL_NEW = "No previsto"
LABEL_REMOVED = "Eliminado"
#: Sufijo de la clase CSS de cada etiqueta (`bg-label-<sufijo>`).
LABEL_CLASSES = {
    LABEL_MORE: "more",
    LABEL_LESS: "less",
    LABEL_PRICE: "price",
    LABEL_NEW: "new",
    LABEL_REMOVED: "removed",
}
MAX_EXAMPLES = 20
GROUPS = ("added", "reappeared", "removed", "more_quantity", "less_quantity", "price_changed")


@dataclass(frozen=True)
class Values:
    """Los valores de una fila en una versión."""

    kind: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    description: str = ""


def row_labels(before: Values | None, after: Values | None) -> tuple[str, ...]:
    if before is None and after is None:
        return ()
    if before is None:
        return (LABEL_NEW,)
    if after is None:
        return (LABEL_REMOVED,)
    if before.kind != KIND_ITEM or after.kind != KIND_ITEM:
        return ()
    labels: list[str] = []
    old_qty, new_qty = before.quantity or ZERO, after.quantity or ZERO
    if new_qty > old_qty:
        labels.append(LABEL_MORE)
    elif new_qty < old_qty:
        labels.append(LABEL_LESS)
    if before.unit_price != after.unit_price:
        labels.append(LABEL_PRICE)
    return tuple(labels)


def _label(code: str, description: str) -> str:
    return f"{code} {description[:80]}".strip()


def summarize_changes(
    previous: Mapping[str, Values], parsed: ParsedBudget, known_codes: Collection[str]
) -> dict[str, Any]:
    """Qué cambiaría al aplicar `parsed` sobre la versión vigente.

    `previous`: las filas de la vigente por código (vacío si es la primera).
    `known_codes`: todos los códigos que el presupuesto tuvo alguna vez
    (`BudgetItem`): un ítem que no está en la vigente pero sí aquí "vuelve".
    Los números van como texto: `summary` es JSON.
    """
    counts = dict.fromkeys(GROUPS, 0)
    examples: dict[str, list[str]] = {group: [] for group in GROUPS}

    def note(group: str, label: str) -> None:
        counts[group] += 1
        if len(examples[group]) < MAX_EXAMPLES:
            examples[group].append(label)

    items = [row for row in parsed.rows if row.kind == KIND_ITEM]
    parsed_items = {row.code for row in items}
    for row in items:
        label = _label(row.code, row.description)
        before = previous.get(row.code)
        if before is None or before.kind != KIND_ITEM:
            known = before is None and row.code in known_codes
            note("reappeared" if known else "added", label)
            continue
        after = Values(row.kind, row.quantity, row.unit_price, row.total, row.description)
        for tag in row_labels(before, after):
            if tag == LABEL_MORE:
                note("more_quantity", label)
            elif tag == LABEL_LESS:
                note("less_quantity", label)
            elif tag == LABEL_PRICE:
                note("price_changed", label)
    for code, before in previous.items():
        if before.kind == KIND_ITEM and code not in parsed_items:
            note("removed", _label(code, before.description))

    previous_direct = (
        sum((v.total for v in previous.values() if v.kind == KIND_ITEM), ZERO) if previous else None
    )
    return {
        "row_count": len(parsed.rows),
        "item_count": len(items),
        "chapter_count": len(parsed.rows) - len(items),
        **counts,
        "examples": examples,
        "ignored_rows": parsed.ignored_rows,
        "ignored_examples": list(parsed.ignored_examples),
        "total_mismatches": parsed.total_mismatches,
        "warnings": list(parsed.warnings),
        "direct_cost": str(parsed.direct_cost),
        "previous_direct_cost": None if previous_direct is None else str(previous_direct),
    }

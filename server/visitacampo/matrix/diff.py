"""Qué cambia en la matriz y cómo queda cada ítem frente al presupuesto. Puro.

Todo lo que va al `summary` (JSON) son textos: las cantidades como decimales con
4 decimales, para no perder exactitud.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from visitacampo.budgets.totals import QUANTITY_STEP

Pair = tuple[UUID, UUID]
MAX_EXAMPLES = 20
MAX_ITEM_ROWS = 300


def _q(value: Decimal) -> str:
    return str(value.quantize(QUANTITY_STEP))


def _plain(value: Decimal) -> str:
    """`Decimal("5.0000")` → "5"; `Decimal("2.5000")` → "2,5" (para los ejemplos)."""
    return f"{value.normalize():f}".replace(".", ",")


def summarize_plan(
    current: Mapping[Pair, Decimal],
    new: Mapping[Pair, Decimal],
    *,
    executed: Collection[Pair],
    label: Callable[[Pair], str],
) -> dict[str, Any]:
    """Pares nuevos, modificados y eliminados. Un par eliminado con ejecución se
    conserva con planeado 0 (`kept_zero`) en vez de borrarse."""
    added = sorted((p for p in new if p not in current), key=label)
    changed = sorted((p for p in new if p in current and new[p] != current[p]), key=label)
    gone = sorted((p for p in current if p not in new), key=label)
    kept = [p for p in gone if p in executed]
    removed = [p for p in gone if p not in executed]
    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "kept_zero": len(kept),
        "examples": {
            "added": [f"{label(p)}: {_plain(new[p])}" for p in added[:MAX_EXAMPLES]],
            "changed": [
                f"{label(p)}: {_plain(current[p])} → {_plain(new[p])}"
                for p in changed[:MAX_EXAMPLES]
            ],
            "removed": [label(p) for p in removed[:MAX_EXAMPLES]],
            "kept_zero": [label(p) for p in kept[:MAX_EXAMPLES]],
        },
    }


@dataclass(frozen=True)
class BudgetedItem:
    code: str
    description: str
    unit: str
    #: Cantidad en la versión vigente; None si el ítem ya no está en ella.
    quantity: Decimal | None


def item_totals(
    planned: Mapping[UUID, Decimal],
    budgeted: Mapping[UUID, BudgetedItem],
    retired: Mapping[UUID, BudgetedItem],
) -> dict[str, Any]:
    """Presupuestado / planeado / diferencia por ítem. Lista solo los que no cuadran.

    `budgeted`: los ítems de las versiones vigentes; `retired`: ítems con plan que ya
    no están en su versión vigente."""
    counts = {"ok": 0, "over": 0, "under": 0, "retired": 0, "unassigned": 0}
    rows: list[dict[str, Any]] = []
    for item_id, info in budgeted.items():
        quantity = info.quantity or Decimal(0)
        total = planned.get(item_id, Decimal(0))
        if total == 0 and quantity > 0:
            counts["unassigned"] += 1
        diff = total - quantity
        status = "over" if diff > 0 else "under" if diff < 0 else "ok"
        counts[status] += 1
        if status != "ok":
            rows.append(
                {
                    "code": info.code,
                    "description": info.description,
                    "unit": info.unit,
                    "budgeted": _q(quantity),
                    "planned": _q(total),
                    "diff": _q(diff),
                    "status": status,
                }
            )
    for item_id, info in retired.items():
        if planned.get(item_id, Decimal(0)) == 0:
            continue
        counts["retired"] += 1
        rows.append(
            {
                "code": info.code,
                "description": info.description,
                "unit": info.unit,
                "budgeted": None,
                "planned": _q(planned[item_id]),
                "diff": None,
                "status": "retired",
            }
        )
    rows.sort(key=lambda row: code_key(row["code"]))
    return {"counts": counts, "items": rows[:MAX_ITEM_ROWS]}


def code_key(code: str) -> list[tuple[int, int | str]]:
    """Orden por código: "2.10" después de "2.9"; segmentos con letras, después."""
    return [(0, int(p)) if p.isdigit() else (1, p) for p in code.split(".")]

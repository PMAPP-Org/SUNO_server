"""Avance de una tarea por cantidades (costos, pieza 4). Puro.

Valor planeado V = Σ planeada × precio; valor ejecutado E = Σ max(ejecutada, 0) ×
precio, sobre los ítems del plan de la tarea con cantidad planeada > 0.
% = min(100, 100·E/V). Sin valor (ítems sin precio), el promedio simple de
ejecutada/planeada por ítem. Una tarea sin ítems con cantidad no se mide así.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

from visitacampo.budgets.totals import HUNDRED, WIDE_PRECISION, money

PCT_STEP = Decimal("0.01")
_ZERO = Decimal(0)


@dataclass(frozen=True)
class PlanLine:
    task_id: UUID
    item_id: UUID
    planned: Decimal
    #: Suma de las cantidades de las visitas validadas (puede ser negativa).
    executed: Decimal
    unit_price: Decimal | None


@dataclass(frozen=True)
class QuantityProgress:
    #: 0..100, a dos decimales.
    pct: Decimal
    #: Valor planeado de la tarea (a centavos): el peso en la consolidación.
    value: Decimal


def task_progress(lines: Iterable[PlanLine]) -> dict[UUID, QuantityProgress]:
    by_task: dict[UUID, list[PlanLine]] = defaultdict(list)
    for plan_line in lines:
        if plan_line.planned > 0:
            by_task[plan_line.task_id].append(plan_line)
    result: dict[UUID, QuantityProgress] = {}
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        for task_id, own in by_task.items():
            value = sum((ln.planned * (ln.unit_price or _ZERO) for ln in own), _ZERO)
            if value > 0:
                earned = sum(
                    (max(ln.executed, _ZERO) * (ln.unit_price or _ZERO) for ln in own), _ZERO
                )
                pct = earned * HUNDRED / value
            else:
                ratios = (max(ln.executed, _ZERO) * HUNDRED / ln.planned for ln in own)
                pct = sum(ratios, _ZERO) / len(own)
            result[task_id] = QuantityProgress(
                pct=min(HUNDRED, pct).quantize(PCT_STEP, rounding=ROUND_HALF_UP),
                value=money(value),
            )
    return result

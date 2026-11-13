"""Etiqueta de una fila del presupuesto, la misma en el libro de costos y en el control.

"2.1 Concreto zapatas", y " · presupuesto" solo si el proyecto tiene dos o más presupuestos con
versión aplicada (uno sin aplicar no tiene filas que confundir).
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID


def several_budgets(budget_ids: Iterable[UUID]) -> bool:
    """¿Hay filas de dos o más presupuestos? `budget_ids`: el de cada versión vigente."""
    return len(set(budget_ids)) > 1


def row_label(code: str, description: str, budget: str, *, several: bool) -> str:
    text = f"{code} {description}".strip()
    return f"{text} · {budget}" if several else text


def with_budget(text: str, budget: str, *, several: bool) -> str:
    """Un texto que no es una fila ("Sin capítulo"), con el mismo sufijo."""
    return f"{text} · {budget}" if several else text

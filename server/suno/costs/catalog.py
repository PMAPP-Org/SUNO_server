"""Catálogo de asignación del libro de costos (costos, pieza 6a).

Las filas (ítems y capítulos) de la versión vigente de cada presupuesto del proyecto, con la
misma etiqueta que usa el control ("2.1 Concreto zapatas", y " · presupuesto" si el proyecto
tiene más de uno). Dos consultas, más una para las tareas asignables.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from uuid import UUID

from suno.budgets.labels import row_label, several_budgets
from suno.core.context import RequestContext
from suno.core.models import BudgetItemVersion, BudgetRowKind, Project, Task
from suno.core.scoping import scoped_qs
from suno.matrix.importer import current_versions


@dataclass(frozen=True)
class CatalogRow:
    item_id: UUID
    code: str
    description: str
    kind: str
    parent_id: UUID | None
    budget_id: UUID
    budget: str
    label: str


@dataclass(frozen=True)
class Catalog:
    #: En el orden del presupuesto (por nombre de presupuesto y posición en el Excel).
    rows: Mapping[UUID, CatalogRow]

    @cached_property
    def _children(self) -> dict[UUID, list[UUID]]:
        children: dict[UUID, list[UUID]] = defaultdict(list)
        for row in self.rows.values():
            if row.parent_id is not None:
                children[row.parent_id].append(row.item_id)
        return children

    @cached_property
    def _labels(self) -> dict[str, CatalogRow]:
        return {row.label: row for row in self.items()}

    def items(self) -> list[CatalogRow]:
        return [row for row in self.rows.values() if row.kind == BudgetRowKind.ITEM]

    def chapters(self) -> list[CatalogRow]:
        return [row for row in self.rows.values() if row.kind == BudgetRowKind.CHAPTER]

    def below(self, chapter_id: UUID) -> list[UUID]:
        """Todo lo que cuelga del capítulo, a cualquier profundidad (sin él)."""
        found: list[UUID] = []
        seen = {chapter_id}
        pending = list(self._children.get(chapter_id, ()))
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            found.append(current)
            pending.extend(self._children.get(current, ()))
        return found

    def descendants(self, chapter_id: UUID) -> frozenset[UUID]:
        """Los ítems del capítulo, a cualquier profundidad."""
        return frozenset(
            i for i in self.below(chapter_id) if self.rows[i].kind == BudgetRowKind.ITEM
        )

    def items_under(self, chapter_id: UUID) -> list[CatalogRow]:
        inside = self.descendants(chapter_id)
        return [row for row in self.items() if row.item_id in inside]

    def by_label(self, label: str) -> CatalogRow | None:
        return self._labels.get(label.strip())

    def by_code(self, code: str) -> list[CatalogRow]:
        wanted = code.strip().casefold()
        return [row for row in self.rows.values() if row.code.casefold() == wanted]

    def label_of(self, item_id: UUID | None, fallback: str = "") -> str:
        row = self.rows.get(item_id) if item_id is not None else None
        return row.label if row is not None else fallback


def load_catalog(ctx: RequestContext, project: Project) -> Catalog:
    versions = current_versions(ctx, project)
    if not versions:
        return Catalog({})
    several = several_budgets(version.budget_id for version in versions)
    rows: dict[UUID, CatalogRow] = {}
    for item_id, code, description, kind, parent_id, budget_id, budget in (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions)
        .order_by("version__budget__name", "sort_order")
        .values_list(
            "item_id",
            "item__code",
            "description",
            "kind",
            "parent_id",
            "version__budget_id",
            "version__budget__name",
        )
    ):
        rows[item_id] = CatalogRow(
            item_id=item_id,
            code=code,
            description=description,
            kind=kind,
            parent_id=parent_id,
            budget_id=budget_id,
            budget=budget,
            label=row_label(code, description, budget, several=several),
        )
    return Catalog(rows)


def assignable_tasks(ctx: RequestContext, project: Project) -> dict[UUID, Task]:
    """Las tareas a las que se puede asignar un costo: hojas vigentes (ni resumen ni retiradas)."""
    return {
        task.pk: task
        for task in scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True, is_summary=False)
        .order_by("sort_order")
    }

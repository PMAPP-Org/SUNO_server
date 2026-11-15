"""Destino de un documento de materiales (costos, pieza 6c, spec 1.2 "Destino").

Es el de la 6a —indirecto, un ítem, un capítulo completo o ítems de un capítulo, y la tarea
opcional— y se valida con `costs.services.check_assignment`. Sin JavaScript se escribe en un
solo campo de texto (decisión 4 del plan, `parse_destination`):

- vacío o "Indirecto": el proyecto;
- la etiqueta o el código de un ítem ("2.1 Concreto zapatas", "2.1");
- un capítulo: su código, o "Capítulo 2" (también con la etiqueta, "Capítulo 2 Estructura");
- varios códigos de ítems del mismo capítulo separados por comas ("2.1, 2.2").

La tarea va aparte. Los ítems se guardan en una tabla de enlaces por documento.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import models

from suno.core.context import RequestContext
from suno.core.models import BudgetRowKind, CostScope, Task
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, CatalogRow
from suno.costs.services import Assignment, check_assignment

INDIRECT_TEXT = "Indirecto"
INDIRECT_LABEL = "Indirecto (proyecto)"
CHAPTER_PREFIX = "Capítulo "
EMPTY_SELECTION = "Escribe el destino: un ítem, «Capítulo …» o varios códigos separados por comas."
_SPLIT = re.compile(r"\s*[,;]\s*")


@dataclass(frozen=True)
class DestinationData:
    scope: str
    item_ids: tuple[UUID, ...] = ()
    chapter_id: UUID | None = None
    task_id: UUID | None = None


INDIRECT = DestinationData(str(CostScope.INDIRECT))
#: (scope, capítulo, ítems ordenados, tarea): agrupa los consumos de un corte.
DestinationKey = tuple[str, UUID | None, tuple[UUID, ...], UUID | None]


def _chapter_id(catalog: Catalog, text: str) -> UUID | None:
    wanted = text.strip().casefold()
    for row in catalog.chapters():
        if wanted in (row.code.casefold(), row.label.casefold()):
            return row.item_id
    return None


def parse_destination(
    text: str, catalog: Catalog, task_id: UUID | None = None
) -> tuple[DestinationData | None, str]:
    """El destino escrito y, si no se entiende, el error para el usuario."""
    value = text.strip()
    if not value or value.casefold() in (INDIRECT_TEXT.casefold(), INDIRECT_LABEL.casefold()):
        return DestinationData(str(CostScope.INDIRECT), task_id=task_id), ""
    row = catalog.by_label(value)
    if row is not None:
        return DestinationData(str(CostScope.ITEM), (row.item_id,), task_id=task_id), ""
    if value.casefold().startswith(CHAPTER_PREFIX.casefold()):
        name = value[len(CHAPTER_PREFIX) :].strip()
        chapter_id = _chapter_id(catalog, name)
        if chapter_id is None:
            return None, f"No hay un capítulo «{name}» en la versión vigente del presupuesto."
        chapter = DestinationData(str(CostScope.CHAPTER), chapter_id=chapter_id, task_id=task_id)
        return chapter, ""
    rows: list[CatalogRow] = []
    for code in filter(None, _SPLIT.split(value)):
        found = catalog.by_code(code)
        if not found:
            return None, f"«{code}» no es un ítem ni un capítulo del presupuesto vigente."
        if len(found) > 1:
            return None, (
                f"El código «{code}» está en más de un presupuesto: elige el ítem de la lista."
            )
        rows.append(found[0])
    if not rows:
        return None, EMPTY_SELECTION
    if len(rows) == 1:
        only = rows[0]
        if only.kind == BudgetRowKind.CHAPTER:
            scope = str(CostScope.CHAPTER)
            return DestinationData(scope, chapter_id=only.item_id, task_id=task_id), ""
        return DestinationData(str(CostScope.ITEM), (only.item_id,), task_id=task_id), ""
    if any(row.kind != BudgetRowKind.ITEM for row in rows):
        return None, "Una selección lleva solo ítems (no capítulos), separados por comas."
    item_ids = tuple(dict.fromkeys(row.item_id for row in rows))
    return DestinationData(str(CostScope.SELECTION), item_ids, rows[0].parent_id, task_id), ""


def check_destination(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: DestinationData
) -> tuple[list[str], Assignment | None]:
    return check_assignment(
        catalog,
        tasks,
        scope=data.scope,
        item_ids=data.item_ids,
        chapter_id=data.chapter_id,
        task_id=data.task_id,
    )


def destination_fields(assignment: Assignment) -> dict[str, Any]:
    return {
        "scope": assignment.scope,
        "chapter_id": assignment.chapter_id,
        "task_id": assignment.task_id,
    }


def destination_key(
    scope: str, chapter_id: UUID | None, item_ids: Sequence[UUID], task_id: UUID | None
) -> DestinationKey:
    return (scope, chapter_id, tuple(sorted(item_ids, key=str)), task_id)


def key_text(key: DestinationKey) -> str:
    scope, chapter_id, item_ids, task_id = key
    return "|".join(
        (scope, str(chapter_id or ""), ",".join(str(i) for i in item_ids), str(task_id or ""))
    )


def task_label(task: Task | None) -> str:
    return f"{task.outline_number} {task.name}" if task is not None else ""


def destination_label(
    catalog: Catalog,
    scope: str,
    chapter_id: UUID | None,
    item_ids: Sequence[UUID],
    task: str = "",
) -> str:
    """ "2.1 Concreto zapatas · tarea 2.1 Columnas", "Capítulo 2 Estructura", "Ítems 2.1, 2.2 de
    2 Estructura" o "Indirecto (proyecto)"."""
    if scope == CostScope.INDIRECT:
        text = INDIRECT_LABEL
    elif scope == CostScope.ITEM:
        text = catalog.label_of(item_ids[0], "—") if item_ids else "—"
    elif scope == CostScope.CHAPTER:
        text = f"Capítulo {catalog.label_of(chapter_id, '—')}"
    else:
        codes = ", ".join(catalog.rows[i].code if i in catalog.rows else "—" for i in item_ids)
        text = f"Ítems {codes} de {catalog.label_of(chapter_id, '—')}"
    return f"{text} · tarea {task}" if task else text


def destination_text(
    catalog: Catalog, scope: str, chapter_id: UUID | None, item_ids: Sequence[UUID]
) -> str:
    """Lo que hay que escribir en el campo para volver a elegir el destino (al editar)."""
    if scope == CostScope.ITEM and item_ids:
        return catalog.label_of(item_ids[0])
    if scope == CostScope.CHAPTER and chapter_id is not None and chapter_id in catalog.rows:
        return f"{CHAPTER_PREFIX}{catalog.rows[chapter_id].code}"
    if scope == CostScope.SELECTION:
        return ", ".join(catalog.rows[i].code for i in item_ids if i in catalog.rows)
    return ""


def destination_options(catalog: Catalog) -> list[str]:
    """Las sugerencias del `datalist`: "Indirecto", los capítulos y los ítems."""
    return [
        INDIRECT_TEXT,
        *(f"{CHAPTER_PREFIX}{row.label}" for row in catalog.chapters()),
        *(row.label for row in catalog.items()),
    ]


def link_items(
    ctx: RequestContext,
    model: type[models.Model],
    owner_field: str,
    owner: Any,
    item_ids: Sequence[UUID],
) -> None:
    """Reemplaza los ítems enlazados de un documento o una línea."""
    scoped_qs(ctx, model).filter(**{owner_field: owner}).delete()
    scoped_qs(ctx, model).bulk_create(
        [model(org_id=ctx.org_id, item_id=item_id, **{owner_field: owner}) for item_id in item_ids]
    )

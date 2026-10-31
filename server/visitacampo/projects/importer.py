"""Importación de un XML de MS Project a un proyecto, en dos pasos.

1. `stage_import`: lee el XML, calcula el resumen de cambios contra las
   tareas actuales y guarda un `ScheduleImport` pendiente (vista previa).
2. `apply_import`: en una transacción, con el proyecto bloqueado, aplica el
   XML guardado. `discard_import` lo descarta.

Las tareas se identifican por `UniqueID` y nunca se borran: las que no
vienen en el XML quedan `retired_in`; si vuelven, se reactivan.
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping
from typing import Any

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Project, ScheduleImport, Task, TaskResponsible
from visitacampo.core.scoping import scoped_qs

from .mspdi import MspdiError, ParsedSchedule, parse_mspdi

#: Campos de `Task` que vienen del XML (mismo nombre en `ParsedTask`).
TASK_FIELDS = (
    "wbs",
    "outline_number",
    "outline_level",
    "sort_order",
    "name",
    "is_summary",
    "is_milestone",
    "start",
    "finish",
    "actual_finish",
    "duration_days",
    "baseline_start",
    "baseline_finish",
    "baseline_duration_days",
    "project_pct",
    "project_physical_pct",
    "total_slack_days",
    "free_slack_days",
    "is_critical",
)
BASELINE_FIELDS = ("baseline_start", "baseline_finish")
MAX_EXAMPLES = 20


class ImportRejected(Exception):
    """La importación no se puede hacer; el mensaje es para el usuario."""


def _label(outline: str, name: str) -> str:
    return f"{outline} {name}"


def summarize_changes(
    existing: Mapping[int, dict[str, Any]],
    parsed: ParsedSchedule,
    responsible_uids: set[int],
) -> dict[str, Any]:
    """Qué cambiaría al aplicar `parsed`.

    `existing` son las tareas del proyecto por `uid`: los `TASK_FIELDS` más
    `retired` (bool). `responsible_uids`: uids de tareas con responsables.
    """
    examples: dict[str, list[str]] = {"added": [], "retired": [], "reactivated": [], "modified": []}
    counts = dict.fromkeys(examples, 0)
    baseline_changed = False

    def note(group: str, label: str) -> None:
        counts[group] += 1
        if len(examples[group]) < MAX_EXAMPLES:
            examples[group].append(label)

    parsed_uids = set()
    for task in parsed.tasks:
        parsed_uids.add(task.uid)
        label = _label(task.outline_number, task.name)
        current = existing.get(task.uid)
        if current is None:
            note("added", label)
            continue
        if current["retired"]:
            note("reactivated", label)
        if any(current[f] != getattr(task, f) for f in TASK_FIELDS):
            note("modified", label)
        if any(current[f] != getattr(task, f) for f in BASELINE_FIELDS):
            baseline_changed = True

    retired_with_responsibles: list[str] = []
    for uid, current in existing.items():
        if uid in parsed_uids or current["retired"]:
            continue
        label = _label(current["outline_number"], current["name"])
        note("retired", label)
        if uid in responsible_uids:
            retired_with_responsibles.append(label)

    warnings: list[str] = []
    if parsed.tasks and not any(t.baseline_finish for t in parsed.tasks):
        warnings.append("Sin línea base: no se calcularán atrasos.")
    undated = sum(1 for t in parsed.tasks if t.start is None or t.finish is None)
    if undated:
        warnings.append(f"{undated} tarea(s) sin fechas de inicio o fin.")
    if retired_with_responsibles:
        warnings.append(
            f"{len(retired_with_responsibles)} tarea(s) retirada(s) con responsables: "
            + ", ".join(retired_with_responsibles[:MAX_EXAMPLES])
            + "."
        )
    return {
        "task_count": len(parsed.tasks),
        **counts,
        "baseline_changed": baseline_changed,
        "examples": examples,
        "warnings": warnings,
    }


def _existing(ctx: RequestContext, project: Project) -> dict[int, dict[str, Any]]:
    rows = scoped_qs(ctx, Task).filter(project=project).values("uid", "retired_in_id", *TASK_FIELDS)
    return {row["uid"]: {**row, "retired": row["retired_in_id"] is not None} for row in rows}


def _parse(data: bytes) -> ParsedSchedule:
    try:
        return parse_mspdi(data)
    except MspdiError as exc:
        raise ImportRejected(str(exc)) from exc


def stage_import(
    ctx: RequestContext, project: Project, *, file_name: str, data: bytes
) -> ScheduleImport:
    parsed = _parse(data)
    responsible_uids = set(
        scoped_qs(ctx, TaskResponsible)
        .filter(task__project=project)
        .values_list("task__uid", flat=True)
    )
    summary = summarize_changes(_existing(ctx, project), parsed, responsible_uids)
    return scoped_qs(ctx, ScheduleImport).create(
        org_id=ctx.org_id,
        project=project,
        file_name=file_name[:255],
        source_xml=gzip.compress(data),
        uploaded_by_id=ctx.user_id,
        status_date=parsed.status_date,
        summary=summary,
    )

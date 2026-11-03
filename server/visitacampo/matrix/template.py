"""Plantilla Excel de la matriz: escribirla y leerla. Puro.

Encabezado en la fila 1; las columnas se encuentran por su texto (sin importar
mayúsculas ni tildes), así que el usuario puede reordenarlas. Cada fila es un
par tarea × ítem con su cantidad planeada; una fila de una tarea sin ítem solo
sirve para su ubicación.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from visitacampo.backoffice.export import safe_cell, to_xlsx
from visitacampo.budgets.excel import (
    MAX_QUANTITY,
    ExcelRejected,
    check_file_name,
    preview_sheet,
    read_rows,
)
from visitacampo.budgets.mapping import normalize_header
from visitacampo.budgets.totals import parse_number_detail, quantity

from .locations import clean_name, node_key, path_values

SHEET = "Matriz"
H_UID = "UniqueID"
H_WBS = "EDT"
H_TASK = "Tarea"
H_BUDGET = "Presupuesto"
H_CODE = "Código de ítem"
H_DESCRIPTION = "Descripción"
H_UNIT = "Unidad"
H_QUANTITY = "Cantidad planeada"
MAX_PLAN_ROWS = 20_000
MAX_ERRORS = 50

__all__ = [
    "ItemRef",
    "ParsedPlan",
    "TaskRef",
    "TemplateRejected",
    "TemplateRow",
    "build_template",
    "check_file_name",
    "header",
    "parse_plan",
    "read_template",
]


class TemplateRejected(Exception):
    """El archivo no se puede aplicar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0])


def header(level_names: Sequence[str]) -> list[str]:
    return [
        H_UID,
        H_WBS,
        H_TASK,
        *level_names,
        H_BUDGET,
        H_CODE,
        H_DESCRIPTION,
        H_UNIT,
        H_QUANTITY,
    ]


@dataclass(frozen=True)
class TemplateRow:
    uid: int
    wbs: str
    task: str
    #: Un valor por nivel ("" si no hay).
    location: tuple[str, ...]
    budget: str
    code: str
    description: str
    unit: str
    quantity: Decimal | None


def build_template(level_names: Sequence[str], rows: Iterable[TemplateRow]) -> bytes:
    def values(row: TemplateRow) -> list[Any]:
        texts = [row.wbs, row.task, *row.location, row.budget, row.code, row.description, row.unit]
        return [
            row.uid,
            *(safe_cell(text) for text in texts),
            "" if row.quantity is None else float(row.quantity),
        ]

    return to_xlsx(header(level_names), (values(row) for row in rows))


def read_template(data: bytes) -> list[tuple[Any, ...]]:
    """Las filas de la hoja `Matriz` (o la primera), encabezado incluido."""
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        return read_rows(data, sheet, max_rows=MAX_PLAN_ROWS + 1)
    except ExcelRejected as exc:
        raise TemplateRejected([str(exc)]) from exc


@dataclass(frozen=True)
class TaskRef:
    id: UUID
    #: "1.2 Descapote", para los mensajes.
    label: str
    is_summary: bool
    retired: bool


@dataclass(frozen=True)
class ItemRef:
    id: UUID
    kind: str


@dataclass(frozen=True)
class ParsedPlan:
    pairs: dict[tuple[UUID, UUID], Decimal]
    #: Solo si la ubicación se asigna en SUNO: tarea → nombres de su ruta (vacía = sin ubicación).
    locations: dict[UUID, tuple[str, ...]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _uid(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _columns(first: Sequence[Any], level_names: Sequence[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, cell in enumerate(first):
        if _present(cell):
            found.setdefault(normalize_header(cell), index)
    wanted = {
        "uid": H_UID,
        "budget": H_BUDGET,
        "code": H_CODE,
        "quantity": H_QUANTITY,
    } | {f"level:{i}": name for i, name in enumerate(level_names)}
    columns = {
        key: found[normalize_header(name)]
        for key, name in wanted.items()
        if normalize_header(name) in found
    }
    missing = [wanted[key] for key in ("uid", "code", "quantity") if key not in columns]
    if missing:
        raise TemplateRejected([f"Falta la columna «{name}» en la fila 1." for name in missing])
    return columns


def parse_plan(
    rows: Sequence[Sequence[Any]],
    *,
    level_names: Sequence[str],
    editable_locations: bool,
    tasks: Mapping[int, TaskRef],
    items: Mapping[tuple[str, str], ItemRef],
    budget_names: Sequence[str],
) -> ParsedPlan:
    """Interpreta la plantilla. `items` va por (`node_key` del presupuesto, código)."""
    if not rows:
        raise TemplateRejected(["La hoja está vacía."])
    columns = _columns(rows[0], level_names)
    level_columns = [columns.get(f"level:{i}") for i in range(len(level_names))]
    single = node_key(budget_names[0]) if len(budget_names) == 1 else None

    def cell(row: Sequence[Any], key: str | int | None) -> Any:
        index = columns.get(key) if isinstance(key, str) else key
        return row[index] if index is not None and index < len(row) else None

    errors: list[str] = []
    pairs: dict[tuple[UUID, UUID], Decimal] = {}
    locations: dict[UUID, tuple[str, ...]] = {}
    dropped = dot_rows = truncated = 0

    for number, row in enumerate(rows[1:], start=2):
        if not any(_present(value) for value in row):
            continue
        uid = _uid(cell(row, "uid"))
        if uid is None:
            errors.append(f"Fila {number}: falta el UniqueID o no es un número entero.")
            continue
        task = tasks.get(uid)
        if task is None:
            errors.append(f"Fila {number}: la tarea con UniqueID {uid} no existe en el cronograma.")
            continue
        if task.retired:
            errors.append(f"Fila {number}: la tarea {task.label} está retirada del cronograma.")
            continue
        if task.is_summary:
            errors.append(
                f"Fila {number}: {task.label} es una tarea resumen; "
                "la matriz va en tareas de detalle."
            )
            continue
        if editable_locations:
            path = path_values([cell(row, index) for index in level_columns])
            if path.incomplete:
                errors.append(
                    f"Fila {number}: la ubicación tiene un nivel vacío antes de otro con valor."
                )
                continue
            truncated += path.truncated
            previous = locations.get(task.id)
            if previous is not None and [node_key(n) for n in previous] != [
                node_key(n) for n in path.names
            ]:
                errors.append(
                    f"Fila {number}: la tarea {task.label} tiene otra ubicación en una fila "
                    "anterior."
                )
                continue
            locations.setdefault(task.id, path.names)

        code = clean_name(cell(row, "code"))
        raw_quantity = cell(row, "quantity")
        if not code:
            if _present(raw_quantity):
                errors.append(f"Fila {number}: hay cantidad pero falta el código del ítem.")
            continue
        budget_name = clean_name(cell(row, "budget"))
        budget_key = node_key(budget_name) if budget_name else single
        if budget_key is None:
            errors.append(
                f"Fila {number}: falta el presupuesto (el proyecto tiene varios presupuestos)."
            )
            continue
        ref = items.get((budget_key, code))
        if ref is None:
            where = f"«{budget_name}»" if budget_name else "del proyecto"
            errors.append(
                f"Fila {number}: el ítem {code} no existe en el presupuesto vigente {where}."
            )
            continue
        if ref.kind != "item":
            errors.append(f"Fila {number}: {code} es un capítulo; la matriz va en ítems.")
            continue
        if not _present(raw_quantity):
            dropped += 1
            continue
        try:
            value, dot = parse_number_detail(raw_quantity)
        except ValueError:
            value, dot = None, False
        if value is None:
            errors.append(f"Fila {number}: la cantidad «{raw_quantity}» no es un número.")
            continue
        if value < 0:
            errors.append(f"Fila {number}: la cantidad no puede ser negativa.")
            continue
        if value >= MAX_QUANTITY:
            errors.append(f"Fila {number}: la cantidad es demasiado grande.")
            continue
        pair = (task.id, ref.id)
        if pair in pairs:
            errors.append(f"Fila {number}: el par {task.label} × {code} está repetido.")
            continue
        pairs[pair] = quantity(value)
        dot_rows += dot

    if errors:
        extra = len(errors) - MAX_ERRORS
        raise TemplateRejected(
            errors[:MAX_ERRORS] + ([f"… y {extra} error(es) más."] if extra > 0 else [])
        )
    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} fila(s) con ítem y sin cantidad: esos pares se eliminan de la matriz."
        )
    if dot_rows:
        warnings.append(
            f"{dot_rows} cantidad(es) escritas como texto con un punto seguido de 3 dígitos "
            'se leyeron como miles ("1.234" = 1234).'
        )
    if truncated:
        warnings.append(
            f"{truncated} nombre(s) de ubicación pasan de 120 caracteres y se recortan."
        )
    return ParsedPlan(pairs=pairs, locations=locations, warnings=tuple(warnings))

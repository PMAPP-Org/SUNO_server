"""Formularios de mano de obra (costos, pieza 6d). Sin JavaScript obligatorio (ADR-0020)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from django import forms
from django.http import QueryDict

from suno.budgets.totals import parse_number
from suno.core.models import DocumentType, Worker
from suno.costs.catalog import Catalog

from .common import MAX_NAME, MAX_NOTE, MAX_POSITION
from .registry import WorkerData
from .timesheets import LineData, read_destination


def date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class WorkerForm(forms.Form):
    document_type = forms.ChoiceField(label="Tipo de documento", choices=DocumentType.choices)
    document_number = forms.CharField(label="Número de documento", max_length=30)
    full_name = forms.CharField(label="Nombre", max_length=MAX_NAME)
    position = forms.CharField(
        label="Cargo", max_length=MAX_POSITION, required=False,
        widget=forms.TextInput(attrs={"list": "ta-positions", "autocomplete": "off"}),
    )  # fmt: skip
    hired_on = forms.DateField(label="Fecha de ingreso", widget=date_input())

    def worker_data(self) -> WorkerData:
        data = self.cleaned_data
        return WorkerData(data["document_type"], data["document_number"], data["full_name"],
                          data.get("position") or "", data["hired_on"])  # fmt: skip


class NewWorkerForm(WorkerForm):
    daily_wage = forms.DecimalField(
        label="Jornal básico", max_digits=14, decimal_places=2, min_value=0
    )


class WageForm(forms.Form):
    valid_from = forms.DateField(label="Desde", widget=date_input())
    daily_wage = forms.DecimalField(label="Jornal básico", max_digits=14, decimal_places=2,
                                    min_value=0)  # fmt: skip


class RetireForm(forms.Form):
    retired_on = forms.DateField(label="Fecha de retiro", widget=date_input())


class FactorForm(forms.Form):
    valid_from = forms.DateField(label="Desde", widget=date_input())
    factor = forms.DecimalField(label="Factor", max_digits=6, decimal_places=4, min_value=1,
                                max_value=3)  # fmt: skip
    note = forms.CharField(label="Composición", max_length=MAX_NOTE, required=False)


class AssignmentForm(forms.Form):
    worker = forms.ChoiceField(label="Trabajador")
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta (opcional)", required=False, widget=date_input())

    def __init__(self, *args: Any, workers: Sequence[Worker], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["worker"].choices = [("", "Elige un trabajador"),
                                         *((str(w.pk), w.label) for w in workers)]  # fmt: skip


class PeriodForm(forms.Form):
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta", widget=date_input())


#: Filas que acepta un formulario de la cuadrícula (decisión 11 del plan).
MAX_GRID_ROWS = 250


@dataclass(frozen=True)
class PostedRow:
    worker_id: UUID
    destination: str
    task: str
    #: Lo escrito en cada celda, por día ("" si vacía).
    cells: Mapping[date, str]


def cell_name(index: int, day: date) -> str:
    return f"f{index}-d{day.toordinal()}"


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def read_rows(post: QueryDict, days: Sequence[date]) -> list[PostedRow]:
    """Las filas enviadas; una fila sin trabajador válido se ignora."""
    try:
        count = min(int(post.get("filas") or 0), MAX_GRID_ROWS)
    except ValueError:
        count = 0
    rows: list[PostedRow] = []
    for index in range(count):
        worker = _uuid(post.get(f"f{index}-trabajador"))
        if worker is None:
            continue
        rows.append(PostedRow(
            worker,
            (post.get(f"f{index}-destino") or "")[:500],
            (post.get(f"f{index}-tarea") or "")[:20],
            {day: (post.get(cell_name(index, day)) or "").strip()[:12] for day in days},
        ))  # fmt: skip
    return rows


def to_lines(
    rows: Sequence[PostedRow],
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    names: Mapping[UUID, str],
) -> tuple[list[LineData], list[str]]:
    """Las líneas de las celdas escritas; "0" o vacío es no trabajó."""
    lines: list[LineData] = []
    errors: list[str] = []
    for row in rows:
        written = {day: text for day, text in row.cells.items() if text}
        if not written:
            continue
        who = names.get(row.worker_id, "—")
        destination, error = read_destination(row.destination, row.task, catalog, outlines)
        if destination is None:
            errors.append(f"{who}: {error}")
            continue
        for day, text in sorted(written.items()):
            try:
                value = parse_number(text)
            except ValueError:
                errors.append(f"{who}, {day:%d/%m/%Y}: «{text}» no es un número.")
                continue
            if value:
                lines.append(LineData(row.worker_id, day, destination, value))
    return lines, errors


def repeat_previous(rows: Sequence[PostedRow], day: date, days: Sequence[date]) -> list[PostedRow]:
    """ "Repetir el día anterior": copia en `day` lo de la columna de la izquierda."""
    if day not in days or days.index(day) == 0:
        return list(rows)
    before = days[days.index(day) - 1]
    return [replace(row, cells={**row.cells, day: row.cells.get(before, "")}) for row in rows]


def split_row(rows: Sequence[PostedRow], index: int, days: Sequence[date]) -> list[PostedRow]:
    """ "Partir la fila": otra fila del mismo trabajador, debajo, para repartir el día entre
    destinos."""
    if not 0 <= index < len(rows):
        return list(rows)
    copy = PostedRow(rows[index].worker_id, "", "", dict.fromkeys(days, ""))
    return [*rows[: index + 1], copy, *rows[index + 1 :]]


class PayrollForm(forms.Form):
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta", widget=date_input())

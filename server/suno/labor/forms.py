"""Formularios de mano de obra (costos, pieza 6d). Sin JavaScript obligatorio (ADR-0020)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django import forms

from suno.core.models import DocumentType, Worker

from .common import MAX_NAME, MAX_NOTE, MAX_POSITION
from .registry import WorkerData


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

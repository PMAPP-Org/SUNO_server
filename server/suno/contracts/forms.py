"""Formularios de la pestaña "Contratos" (costos, pieza 6b, spec 2.2–2.4).

Sin JavaScript (como el libro de la 6a): líneas, cargos y descuentos son filas de un formset y
"Agregar filas" vuelve a pintar el formulario con filas vacías de más. El ítem se elige con un
buscador `datalist` sobre las etiquetas del catálogo; las tareas se escriben como números de
esquema separados por comas (vacío: todas las del ítem).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.forms import formset_factory
from django.http import QueryDict

from suno.attachments.web import ACCEPT
from suno.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    MAX_CHARGES,
    ChargeSpec,
    line_base,
)
from suno.core.models import ContractAmendment, ContractKind, ContractLine, Task
from suno.costs.catalog import Catalog

from .calc import BASE_GROSS
from .certificates import CertificateLineData
from .services import AmendmentData, ContractData, IncreaseData, LineData

#: Filas vacías que agrega cada botón "Agregar filas" (`accion` = prefijo del formset).
ADD_ROWS = {"lineas": 3, "cargos": 1, "descuentos": 1}
NEW_LINE_ROWS = 3
_SPLIT = re.compile(r"[,;\s]+")
CHARGE_BASES = [
    (BASE_DIRECT, "Costo directo"),
    (BASE_SUBTOTAL, "Subtotal"),
    *((line_base(n), f"Cargo {n}") for n in range(1, MAX_CHARGES + 1)),
]
DEDUCTION_BASES = [
    (BASE_DIRECT, "Bruto directo"),
    (BASE_GROSS, "Bruto"),
    *((line_base(n), f"Cargo {n}") for n in range(1, MAX_CHARGES + 1)),
]


def _date() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def plain(value: Decimal) -> str:
    """Un decimal sin ceros de sobra ni notación científica, para un `<input>`: 10.0000 → 10."""
    return format(value.normalize(), "f")


class ContractForm(forms.Form):
    kind = forms.ChoiceField(label="Tipo", choices=ContractKind.choices)
    number = forms.CharField(label="Número", max_length=30)
    purpose = forms.CharField(label="Objeto", max_length=300)
    contractor = forms.CharField(label="Contratista", max_length=200)
    starts_on = forms.DateField(label="Inicio", widget=_date())
    ends_on = forms.DateField(label="Fin", widget=_date())
    advance_pct = forms.DecimalField(
        label="Anticipo (%)", max_digits=7, decimal_places=4, min_value=0, max_value=100
    )
    retention_pct = forms.DecimalField(
        label="Retención de garantía (%)",
        max_digits=7,
        decimal_places=4,
        min_value=0,
        max_value=100,
    )
    confirm_overlap = forms.BooleanField(label="Confirmo el traslape", required=False)


class LineForm(forms.Form):
    item = forms.CharField(
        label="Ítem",
        required=False,
        widget=forms.TextInput(attrs={"list": "ctr-items", "autocomplete": "off"}),
    )
    tasks = forms.CharField(
        label="Tareas",
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Todas (o 1.2, 2.1)"}),
    )
    unit = forms.CharField(label="Unidad", required=False, max_length=30)
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_price = forms.DecimalField(
        label="Precio unitario", required=False, max_digits=18, decimal_places=2, min_value=0
    )


class TermForm(forms.Form):
    """Un cargo o un descuento: nombre, % y base."""

    name = forms.CharField(label="Nombre", required=False, max_length=100)
    pct = forms.DecimalField(label="%", required=False, max_digits=7, decimal_places=4, min_value=0)
    base = forms.ChoiceField(label="Base", required=False)

    def __init__(self, *args: Any, bases: Sequence[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["base"].choices = list(bases)


LineFormSet = formset_factory(LineForm, extra=0)
TermFormSet = formset_factory(TermForm, extra=0)


def more_rows(data: QueryDict, action: str) -> QueryDict | None:
    """El POST con filas vacías de más si se pulsó "Agregar filas" (`action`); si no, None."""
    if action not in ADD_ROWS:
        return None
    copy = data.copy()
    key = f"{action}-TOTAL_FORMS"
    try:
        total = int(copy.get(key, "0"))
    except ValueError:
        total = 0
    copy[key] = str(total + ADD_ROWS[action])
    return copy


def contract_formsets(
    data: QueryDict | None,
    *,
    lines: Sequence[Mapping[str, Any]] = (),
    charges: Sequence[Mapping[str, Any]] = (),
    deductions: Sequence[Mapping[str, Any]] = (),
) -> tuple[Any, Any, Any]:
    return (
        LineFormSet(data, prefix="lineas", initial=list(lines)),
        TermFormSet(
            data, prefix="cargos", initial=list(charges), form_kwargs={"bases": CHARGE_BASES}
        ),
        TermFormSet(
            data,
            prefix="descuentos",
            initial=list(deductions),
            form_kwargs={"bases": DEDUCTION_BASES},
        ),
    )


def outline_index(tasks: Mapping[UUID, Task]) -> dict[str, UUID]:
    return {task.outline_number: task.pk for task in tasks.values()}


def task_ids_of(text: str, index: Mapping[str, UUID]) -> tuple[tuple[UUID, ...], list[str]]:
    """Los números de esquema escritos → tareas; y los que no existen."""
    found: list[UUID] = []
    unknown: list[str] = []
    for token in filter(None, _SPLIT.split(text.strip())):
        task_id = index.get(token)
        if task_id is None:
            unknown.append(token)
        else:
            found.append(task_id)
    return tuple(dict.fromkeys(found)), unknown


def read_lines(
    formset: Any, catalog: Catalog, index: Mapping[str, UUID]
) -> tuple[list[LineData], list[str]]:
    """Las filas con ítem (las vacías se ignoran). Formset ya validado."""
    lines: list[LineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        label = (data.get("item") or "").strip()
        if not label:
            continue
        row = catalog.by_label(label)
        task_ids, unknown = task_ids_of(data.get("tasks") or "", index)
        amount, price = data.get("quantity"), data.get("unit_price")
        if row is None:
            errors.append(
                f"Línea {n}: «{label}» no es un ítem del presupuesto vigente; elígelo de la lista."
            )
        if unknown:
            errors.append(f"Línea {n}: no hay tareas vigentes con número {', '.join(unknown)}.")
        if amount is None or price is None:
            errors.append(f"Línea {n}: escribe la cantidad y el precio.")
        if row is not None and not unknown and amount is not None and price is not None:
            lines.append(LineData(row.item_id, task_ids, data.get("unit") or "", amount, price))
    return lines, errors


def read_terms(formset: Any) -> list[ChargeSpec]:
    """Las filas con nombre, numeradas 1, 2, 3… en orden. Formset ya validado."""
    named = [
        form.cleaned_data for form in formset.forms if (form.cleaned_data.get("name") or "").strip()
    ]
    return [
        ChargeSpec(
            n, data["name"], data.get("pct") or Decimal("0"), data.get("base") or BASE_DIRECT
        )
        for n, data in enumerate(named, start=1)
    ]


def contract_data(
    form: ContractForm,
    lines: Any,
    charges: Any,
    deductions: Any,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> tuple[ContractData, list[str]]:
    rows, errors = read_lines(lines, catalog, outline_index(tasks))
    data = form.cleaned_data
    return (
        ContractData(
            kind=data["kind"],
            number=data["number"],
            purpose=data["purpose"],
            contractor=data["contractor"],
            starts_on=data["starts_on"],
            ends_on=data["ends_on"],
            advance_pct=data["advance_pct"],
            retention_pct=data["retention_pct"],
            lines=tuple(rows),
            charges=tuple(read_terms(charges)),
            deductions=tuple(read_terms(deductions)),
            confirm_overlap=data["confirm_overlap"],
        ),
        errors,
    )


def line_initial(lines: Sequence[ContractLine], catalog: Catalog) -> list[dict[str, Any]]:
    """Las líneas con `select_related("item")` y `prefetch_related("task_links__task")`."""
    return [
        {
            "item": catalog.label_of(line.item_id, line.item.code),
            "tasks": ", ".join(link.task.outline_number for link in line.task_links.all()),
            "unit": line.unit,
            "quantity": plain(line.quantity),
            "unit_price": line.unit_price,
        }
        for line in lines
    ]


def term_initial(specs: Sequence[ChargeSpec]) -> list[dict[str, Any]]:
    return [{"name": s.name, "pct": plain(s.pct), "base": s.base} for s in specs]


class AmendmentForm(forms.Form):
    signed_on = forms.DateField(label="Fecha del otrosí", widget=_date())
    reason = forms.CharField(
        label="Motivo", max_length=500, widget=forms.Textarea(attrs={"rows": 2})
    )
    new_ends_on = forms.DateField(
        label="Nueva fecha de fin (opcional)", required=False, widget=_date()
    )
    confirm_overlap = forms.BooleanField(label="Confirmo el traslape", required=False)

    def __init__(self, *args: Any, lines: Sequence[ContractLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"aumento_{line.pk}"] = forms.DecimalField(
                label="Aumento", required=False, max_digits=18, decimal_places=4, min_value=0
            )

    def increase_fields(self) -> list[tuple[ContractLine, Any]]:
        return [(line, self[f"aumento_{line.pk}"]) for line in self.lines]

    def amendment_data(self, new_lines: Sequence[LineData]) -> AmendmentData:
        data = self.cleaned_data
        return AmendmentData(
            signed_on=data["signed_on"],
            reason=data["reason"],
            new_ends_on=data.get("new_ends_on"),
            increases=tuple(
                IncreaseData(line.pk, data[f"aumento_{line.pk}"])
                for line in self.lines
                if data.get(f"aumento_{line.pk}")
            ),
            new_lines=tuple(new_lines),
            confirm_overlap=data["confirm_overlap"],
        )


def amendment_initial(amendment: ContractAmendment) -> dict[str, Any]:
    values: dict[str, Any] = {
        "signed_on": amendment.signed_on,
        "reason": amendment.reason,
        "new_ends_on": amendment.new_ends_on,
    }
    for increase in amendment.increases.all():
        values[f"aumento_{increase.line_id}"] = plain(increase.added_quantity)
    return values


class CertificateForm(forms.Form):
    """Cantidad y justificación por línea vigente; vacía = la propuesta."""

    def __init__(self, *args: Any, lines: Sequence[ContractLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"cantidad_{line.pk}"] = forms.DecimalField(
                label="Cantidad del acta", required=False, max_digits=18, decimal_places=4,
                min_value=0,
            )  # fmt: skip
            self.fields[f"justificacion_{line.pk}"] = forms.CharField(
                label="Justificación", required=False, max_length=300
            )

    def certificate_lines(self) -> tuple[CertificateLineData, ...]:
        data = self.cleaned_data
        return tuple(
            CertificateLineData(
                line.pk,
                data[f"cantidad_{line.pk}"],
                data.get(f"justificacion_{line.pk}") or "",
            )
            for line in self.lines
            if data.get(f"cantidad_{line.pk}") is not None
        )


class SignedForm(forms.Form):
    signed = forms.FileField(
        label="Acta firmada (PDF, JPG, PNG o WebP)",
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )

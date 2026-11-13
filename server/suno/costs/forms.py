"""Formulario de un movimiento del libro de costos (costos, pieza 6a, spec 2.2).

La asignación se elige en dos pasos, sin JavaScript: un formulario GET con el tipo de
asignación (y, para una selección, el capítulo) y luego este formulario con el ítem (campo con
`datalist` sobre las etiquetas del catálogo), el capítulo o las casillas de los ítems.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.utils import timezone

from suno.attachments.web import ACCEPT
from suno.core.models import CostDocumentType, CostEntry, CostKind, CostScope, Task

from .catalog import Catalog
from .services import EntryData


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Step:
    scope: str
    #: Solo en una selección: el capítulo cuyos ítems se ofrecen.
    chapter_id: UUID | None = None


def read_step(params: Mapping[str, Any], entry: CostEntry | None = None) -> Step:
    """El paso 1 (de la URL o de los campos ocultos); por defecto, el del movimiento o `item`."""
    scope = str(params.get("alcance") or (entry.scope if entry is not None else CostScope.ITEM))
    if scope not in CostScope.values:
        scope = str(CostScope.ITEM)
    chapter = _uuid(params.get("capitulo"))
    if chapter is None and entry is not None and entry.scope == scope:
        chapter = entry.chapter_id
    return Step(scope, chapter if scope == CostScope.SELECTION else None)


class EntryForm(forms.Form):
    item = forms.CharField(
        label="Ítem",
        required=False,
        widget=forms.TextInput(attrs={"list": "cst-items", "autocomplete": "off"}),
    )
    chapter = forms.ChoiceField(label="Capítulo", required=False)
    items = forms.MultipleChoiceField(
        label="Ítems del capítulo", required=False, widget=forms.CheckboxSelectMultiple
    )
    task = forms.ChoiceField(label="Tarea (opcional)", required=False)
    incurred_on = forms.DateField(
        label="Fecha de causación",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    kind = forms.ChoiceField(label="Tipo", choices=CostKind.choices)
    description = forms.CharField(label="Descripción", max_length=300)
    amount = forms.DecimalField(
        label="Valor con IVA", max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    third_party = forms.CharField(
        label="Tercero (proveedor, contratista o trabajador)", max_length=200, required=False
    )
    document_type = forms.ChoiceField(label="Tipo de documento", choices=CostDocumentType.choices)
    document_number = forms.CharField(label="Número de documento", max_length=80, required=False)
    support = forms.FileField(
        label="Soporte (PDF, JPG, PNG o WebP)",
        required=False,
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )

    def __init__(
        self,
        *args: Any,
        catalog: Catalog,
        tasks: Mapping[UUID, Task],
        step: Step,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.catalog = catalog
        self.step = step
        self.fields["chapter"].choices = [
            ("", "Elige un capítulo"),
            *((str(row.item_id), row.label) for row in catalog.chapters()),
        ]
        self.fields["items"].choices = (
            [(str(row.item_id), row.label) for row in catalog.items_under(step.chapter_id)]
            if step.chapter_id is not None
            else []
        )
        self.fields["task"].choices = [
            ("", "Sin tarea"),
            *((str(task.pk), f"{task.outline_number} {task.name}") for task in tasks.values()),
        ]

    def entry_data(self) -> EntryData:
        data = self.cleaned_data
        scope = self.step.scope
        item_ids: tuple[UUID, ...] = ()
        chapter_id: UUID | None = None
        if scope == CostScope.ITEM:
            row = self.catalog.by_label(data.get("item") or "")
            item_ids = (row.item_id,) if row is not None else ()
        elif scope == CostScope.CHAPTER:
            chapter_id = _uuid(data.get("chapter"))
        elif scope == CostScope.SELECTION:
            chapter_id = self.step.chapter_id
            item_ids = tuple(UUID(value) for value in data.get("items") or ())
        return EntryData(
            incurred_on=data["incurred_on"],
            kind=data["kind"],
            description=data["description"],
            amount=data["amount"],
            third_party=data.get("third_party") or "",
            document_type=data["document_type"],
            document_number=data.get("document_number") or "",
            scope=scope,
            item_ids=item_ids,
            chapter_id=chapter_id,
            task_id=_uuid(data.get("task")) if scope != CostScope.INDIRECT else None,
        )


def initial_from(entry: CostEntry, catalog: Catalog, *, copy: bool = False) -> dict[str, Any]:
    """Valores del formulario para editar un movimiento o copiarlo en uno nuevo (sin soporte;
    la copia con fecha de hoy)."""
    items = [link.item_id for link in entry.item_links.all()]
    return {
        "incurred_on": timezone.localdate() if copy else entry.incurred_on,
        "kind": entry.kind,
        "description": entry.description,
        "amount": entry.amount,
        "third_party": entry.third_party,
        "document_type": entry.document_type,
        "document_number": entry.document_number,
        "item": catalog.label_of(items[0]) if entry.scope == CostScope.ITEM and items else "",
        "chapter": str(entry.chapter_id) if entry.chapter_id else "",
        "items": [str(item_id) for item_id in items],
        "task": str(entry.task_id) if entry.task_id else "",
    }

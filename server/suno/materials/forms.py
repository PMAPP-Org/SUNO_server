"""Formularios de materiales (costos, pieza 6c, spec 3). Sin JavaScript, como 6a y 6b: las
líneas son filas de un formset y "Agregar líneas" vuelve a pintar el formulario con filas de
más; "Recalcular" calcula en el servidor sin guardar. El material se elige con un `datalist`
("CEM-01 · Cemento gris 50 kg", o solo su código); el destino es un texto
(`destination.parse_destination`) y la tarea de una fila, su número de esquema."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django import forms
from django.db.models import Q
from django.http import QueryDict

from suno.attachments.web import ACCEPT
from suno.core.context import RequestContext
from suno.core.models import Material
from suno.core.scoping import scoped_qs

from .catalog_services import MAX_CATEGORY, MAX_CODE, MAX_NAME, MAX_UNIT, MaterialData

#: Filas vacías que agrega "Agregar líneas" y filas de un formulario nuevo.
ADD_ROWS = 3
NEW_ROWS = 3
LINES = "lineas"


def date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def plain(value: Any) -> str:
    """Un decimal sin ceros de sobra para un `<input>`: 10.0000 → "10"."""
    return format(value.normalize(), "f") if value is not None else ""


class MaterialIndex:
    """Los materiales que se ofrecen: activos, más los que ya tiene el documento."""

    def __init__(self, materials: Sequence[Material]) -> None:
        self.materials = list(materials)
        self._labels = {material.label: material for material in self.materials}
        self._codes = {material.code.casefold(): material for material in self.materials}

    def find(self, text: str) -> Material | None:
        value = text.strip()
        return self._labels.get(value) or self._codes.get(value.casefold())


def material_index(ctx: RequestContext, include: Sequence[Any] = ()) -> MaterialIndex:
    return MaterialIndex(
        list(
            scoped_qs(ctx, Material)
            .filter(Q(active=True) | Q(pk__in=list(include)))
            .order_by("code")
        )
    )


def more_rows(data: QueryDict, action: str) -> QueryDict | None:
    """El POST con filas vacías de más si se pulsó "Agregar líneas"; si no, None."""
    if action != LINES:
        return None
    copy = data.copy()
    key = f"{LINES}-TOTAL_FORMS"
    try:
        total = int(copy.get(key, "0"))
    except ValueError:
        total = 0
    copy[key] = str(total + ADD_ROWS)
    return copy


class CatalogMaterialForm(forms.Form):
    code = forms.CharField(label="Código", max_length=MAX_CODE)
    name = forms.CharField(label="Nombre", max_length=MAX_NAME)
    unit = forms.CharField(
        label="Unidad",
        max_length=MAX_UNIT,
        widget=forms.TextInput(attrs={"placeholder": "m3, kg, und…"}),
    )
    category = forms.CharField(label="Categoría", max_length=MAX_CATEGORY, required=False)
    active = forms.BooleanField(label="Activo", required=False)

    def material_data(self) -> MaterialData:
        data = self.cleaned_data
        return MaterialData(
            data["code"], data["name"], data["unit"], data.get("category") or "", data["active"]
        )


class SupportForm(forms.Form):
    """Un soporte (factura, acta de conteo o reporte firmado)."""

    file = forms.FileField(
        label="Archivo (PDF, JPG, PNG o WebP)", widget=forms.FileInput(attrs={"accept": ACCEPT})
    )

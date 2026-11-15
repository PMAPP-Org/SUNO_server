"""Formularios de materiales (costos, pieza 6c, spec 3). Sin JavaScript, como 6a y 6b: las
líneas son filas de un formset y "Agregar líneas" vuelve a pintar el formulario con filas de
más; "Recalcular" calcula en el servidor sin guardar. El material se elige con un `datalist`
("CEM-01 · Cemento gris 50 kg", o solo su código); el destino es un texto
(`destination.parse_destination`) y la tarea de una fila, su número de esquema."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from django import forms
from django.db.models import Q
from django.forms import formset_factory
from django.http import QueryDict

from suno.attachments.web import ACCEPT
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    Material,
    MaterialIssue,
    MaterialIssueLine,
    MaterialPurchase,
    MaterialPurchaseLine,
    PurchaseKind,
    Task,
    Warehouse,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog

from .adjustments import AdjustmentData, AdjustmentLineData
from .catalog_services import MAX_CATEGORY, MAX_CODE, MAX_NAME, MAX_UNIT, MaterialData
from .destination import DestinationData, destination_text, parse_destination
from .movements import IssueData, IssueLineData, ReturnData, ReturnLineData, TransferData
from .purchases import PurchaseData, PurchaseLineData

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


class WarehouseForm(forms.Form):
    name = forms.CharField(label="Nombre", max_length=120)


def outline_index(tasks: Mapping[UUID, Task]) -> dict[str, UUID]:
    return {task.outline_number: task.pk for task in tasks.values()}


def warehouse_choices(warehouses: Sequence[Warehouse]) -> list[tuple[str, str]]:
    return [("", "Elige una bodega"), *((str(w.pk), w.name) for w in warehouses)]


def task_choices(tasks: Mapping[UUID, Task]) -> list[tuple[str, str]]:
    return [
        ("", "Sin tarea"),
        *((str(task.pk), f"{task.outline_number} {task.name}") for task in tasks.values()),
    ]


def material_field() -> forms.CharField:
    return forms.CharField(
        label="Material",
        required=False,
        widget=forms.TextInput(attrs={"list": "mat-materials", "autocomplete": "off"}),
    )


def destination_field(required: bool = False) -> forms.CharField:
    return forms.CharField(
        label="Destino",
        required=required,
        max_length=500,
        widget=forms.TextInput(
            attrs={"list": "mat-destinations", "autocomplete": "off", "placeholder": "Indirecto"}
        ),
    )


def read_destination(
    text: str, task_text: str, catalog: Catalog, outlines: Mapping[str, UUID]
) -> tuple[DestinationData | None, str]:
    """El destino de una fila: el texto y el número de esquema de la tarea (vacío: sin tarea)."""
    task_id = None
    task = task_text.strip()
    if task:
        task_id = outlines.get(task)
        if task_id is None:
            return None, f"No hay una tarea vigente con número {task}."
    return parse_destination(text, catalog, task_id)


class PurchaseForm(forms.Form):
    kind = forms.ChoiceField(label="Tipo", choices=PurchaseKind.choices)
    warehouse = forms.ChoiceField(label="Bodega (si es a bodega)", required=False)
    supplier = forms.CharField(label="Proveedor", max_length=200)
    supplier_tax_id = forms.CharField(label="NIT", max_length=40, required=False)
    document_type = forms.ChoiceField(label="Documento", choices=CostDocumentType.choices)
    document_number = forms.CharField(label="Número", max_length=80, required=False)
    purchased_on = forms.DateField(label="Fecha", widget=date_input())
    support = forms.FileField(
        label="Factura (PDF, JPG, PNG o WebP; opcional ahora)",
        required=False,
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )

    def __init__(self, *args: Any, warehouses: Sequence[Warehouse], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)


class PurchaseLineForm(forms.Form):
    material = material_field()
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_price = forms.DecimalField(
        label="Valor unitario (IVA incluido)", required=False, max_digits=18, decimal_places=2,
        min_value=0,
    )  # fmt: skip
    destination = destination_field()
    task = forms.CharField(
        label="Tarea", required=False, max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "2.1"}),
    )  # fmt: skip


PurchaseLineFormSet = formset_factory(PurchaseLineForm, extra=0)


def read_purchase_lines(
    formset: Any,
    index: MaterialIndex,
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    *,
    direct: bool,
) -> tuple[list[PurchaseLineData], list[str]]:
    """Las filas con material (las vacías se ignoran). Formset ya validado."""
    lines: list[PurchaseLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        text = (data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        amount, price = data.get("quantity"), data.get("unit_price")
        problems: list[str] = []
        if material is None:
            problems.append(f"«{text}» no está en el catálogo; elígelo de la lista.")
        if amount is None or price is None:
            problems.append("Escribe la cantidad y el valor unitario.")
        destination = None
        if direct:
            destination, error = read_destination(
                data.get("destination") or "", data.get("task") or "", catalog, outlines
            )
            if error:
                problems.append(error)
        errors += [f"Línea {n}: {problem}" for problem in problems]
        if not problems and material is not None and amount is not None and price is not None:
            lines.append(PurchaseLineData(material.pk, amount, price, destination))
    return lines, errors


def purchase_data(form: PurchaseForm, lines: Sequence[PurchaseLineData]) -> PurchaseData:
    data = form.cleaned_data
    warehouse = data.get("warehouse") or ""
    to_warehouse = bool(warehouse) and data["kind"] == PurchaseKind.WAREHOUSE
    return PurchaseData(
        kind=data["kind"],
        warehouse_id=UUID(warehouse) if to_warehouse else None,
        supplier=data["supplier"],
        supplier_tax_id=data.get("supplier_tax_id") or "",
        document_type=data["document_type"],
        document_number=data.get("document_number") or "",
        purchased_on=data["purchased_on"],
        lines=tuple(lines),
    )


def purchase_initial(
    purchase: MaterialPurchase, lines: Sequence[MaterialPurchaseLine], catalog: Catalog
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Encabezado y filas para editar (líneas con `select_related("material", "task")` y
    `prefetch_related("item_links")`)."""
    header = {
        "kind": purchase.kind,
        "warehouse": str(purchase.warehouse_id or ""),
        "supplier": purchase.supplier,
        "supplier_tax_id": purchase.supplier_tax_id,
        "document_type": purchase.document_type,
        "document_number": purchase.document_number,
        "purchased_on": purchase.purchased_on,
    }
    rows = [
        {
            "material": line.material.label,
            "quantity": plain(line.quantity),
            "unit_price": line.unit_price,
            "destination": destination_text(
                catalog,
                line.scope,
                line.chapter_id,
                [link.item_id for link in line.item_links.all()],
            ),
            "task": line.task.outline_number if line.task else "",
        }
        for line in lines
    ]
    return header, rows


class MaterialLineForm(forms.Form):
    """Una fila de material y cantidad (salidas y traslados)."""

    material = material_field()
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )


MaterialLineFormSet = formset_factory(MaterialLineForm, extra=0)


def read_material_lines(
    formset: Any, index: MaterialIndex
) -> tuple[list[IssueLineData], list[str]]:
    """Las filas con material (las vacías se ignoran). Formset ya validado."""
    lines: list[IssueLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        text = (form.cleaned_data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        amount = form.cleaned_data.get("quantity")
        if material is None:
            errors.append(f"Línea {n}: «{text}» no está en el catálogo; elígelo de la lista.")
        elif amount is None:
            errors.append(f"Línea {n}: Escribe la cantidad.")
        else:
            lines.append(IssueLineData(material.pk, amount))
    return lines, errors


def material_rows(lines: Sequence[Any]) -> list[dict[str, Any]]:
    """Filas para editar (líneas con `select_related("material")`)."""
    return [{"material": line.material.label, "quantity": plain(line.quantity)} for line in lines]


class IssueForm(forms.Form):
    warehouse = forms.ChoiceField(label="Bodega")
    issued_on = forms.DateField(label="Fecha", widget=date_input())
    received_by = forms.CharField(label="Recibe", max_length=120, required=False)
    destination = destination_field()
    task = forms.ChoiceField(label="Tarea", required=False)
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(
        self,
        *args: Any,
        warehouses: Sequence[Warehouse],
        tasks: Mapping[UUID, Task],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)
        self.fields["task"].choices = task_choices(tasks)

    def issue_data(
        self, lines: Sequence[IssueLineData], catalog: Catalog
    ) -> tuple[IssueData | None, str]:
        data = self.cleaned_data
        task = data.get("task") or ""
        destination, error = parse_destination(
            data.get("destination") or "", catalog, UUID(task) if task else None
        )
        if destination is None:
            return None, error
        return (
            IssueData(
                warehouse_id=UUID(data["warehouse"]) if data.get("warehouse") else None,
                issued_on=data["issued_on"],
                destination=destination,
                lines=tuple(lines),
                received_by=data.get("received_by") or "",
                notes=data.get("notes") or "",
            ),
            "",
        )


def issue_initial(issue: MaterialIssue, catalog: Catalog) -> dict[str, Any]:
    """El encabezado para editar (con `prefetch_related("item_links")`)."""
    return {
        "warehouse": str(issue.warehouse_id),
        "issued_on": issue.issued_on,
        "received_by": issue.received_by,
        "destination": destination_text(
            catalog,
            issue.scope,
            issue.chapter_id,
            [link.item_id for link in issue.item_links.all()],
        ),
        "task": str(issue.task_id or ""),
        "notes": issue.notes,
    }


class ReturnForm(forms.Form):
    returned_on = forms.DateField(label="Fecha de la devolución", widget=date_input())
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(self, *args: Any, lines: Sequence[MaterialIssueLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"devolver_{line.pk}"] = forms.DecimalField(
                label="Vuelve", required=False, max_digits=18, decimal_places=4, min_value=0
            )

    def line_fields(self) -> list[tuple[MaterialIssueLine, Any]]:
        return [(line, self[f"devolver_{line.pk}"]) for line in self.lines]

    def return_data(self) -> ReturnData:
        data = self.cleaned_data
        return ReturnData(
            returned_on=data["returned_on"],
            lines=tuple(
                ReturnLineData(line.pk, data[f"devolver_{line.pk}"])
                for line in self.lines
                if data.get(f"devolver_{line.pk}")
            ),
            notes=data.get("notes") or "",
        )


class TransferForm(forms.Form):
    source = forms.ChoiceField(label="Sale de")
    target = forms.ChoiceField(label="Entra a")
    moved_on = forms.DateField(label="Fecha", widget=date_input())
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(self, *args: Any, warehouses: Sequence[Warehouse], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["source"].choices = warehouse_choices(warehouses)
        self.fields["target"].choices = warehouse_choices(warehouses)

    def transfer_data(self, lines: Sequence[IssueLineData]) -> TransferData:
        data = self.cleaned_data
        return TransferData(
            source_id=UUID(data["source"]) if data.get("source") else None,
            target_id=UUID(data["target"]) if data.get("target") else None,
            moved_on=data["moved_on"],
            lines=tuple(lines),
            notes=data.get("notes") or "",
        )


class AdjustmentForm(forms.Form):
    warehouse = forms.ChoiceField(label="Bodega")
    counted_on = forms.DateField(label="Fecha del conteo", widget=date_input())
    destination = destination_field()
    task = forms.ChoiceField(label="Tarea", required=False)
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(
        self,
        *args: Any,
        warehouses: Sequence[Warehouse],
        tasks: Mapping[UUID, Task],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)
        self.fields["task"].choices = task_choices(tasks)

    def adjustment_data(
        self, lines: Sequence[AdjustmentLineData], catalog: Catalog
    ) -> tuple[AdjustmentData | None, str]:
        data = self.cleaned_data
        task = data.get("task") or ""
        destination, error = parse_destination(
            data.get("destination") or "", catalog, UUID(task) if task else None
        )
        if destination is None:
            return None, f"Destino del faltante: {error}"
        return (
            AdjustmentData(
                warehouse_id=UUID(data["warehouse"]) if data.get("warehouse") else None,
                counted_on=data["counted_on"],
                destination=destination,
                lines=tuple(lines),
                notes=data.get("notes") or "",
            ),
            "",
        )


class AdjustmentLineForm(forms.Form):
    material = material_field()
    counted = forms.DecimalField(
        label="Contado", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_cost = forms.DecimalField(
        label="Costo unitario (solo si se pide)", required=False, max_digits=18,
        decimal_places=2, min_value=0,
    )  # fmt: skip


AdjustmentLineFormSet = formset_factory(AdjustmentLineForm, extra=0)


def read_adjustment_lines(
    formset: Any, index: MaterialIndex
) -> tuple[list[AdjustmentLineData], list[str]]:
    lines: list[AdjustmentLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        text = (data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        if material is None:
            errors.append(f"Línea {n}: «{text}» no está en el catálogo; elígelo de la lista.")
        elif data.get("counted") is None:
            errors.append(f"Línea {n}: Escribe lo contado.")
        else:
            lines.append(AdjustmentLineData(material.pk, data["counted"], data.get("unit_cost")))
    return lines, errors


def adjustment_initial(
    adjustment: InventoryAdjustment,
    lines: Sequence[InventoryAdjustmentLine],
    catalog: Catalog,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Con `prefetch_related("item_links")` en el ajuste y `select_related("material")` en
    las líneas."""
    header = {
        "warehouse": str(adjustment.warehouse_id),
        "counted_on": adjustment.counted_on,
        "destination": destination_text(
            catalog,
            adjustment.scope,
            adjustment.chapter_id,
            [link.item_id for link in adjustment.item_links.all()],
        ),
        "task": str(adjustment.task_id or ""),
        "notes": adjustment.notes,
    }
    rows = [
        {
            "material": line.material.label,
            "counted": plain(line.counted),
            "unit_cost": line.given_unit_cost,
        }
        for line in lines
    ]
    return header, rows


class CutForm(forms.Form):
    hasta = forms.DateField(label="Hasta", widget=date_input())

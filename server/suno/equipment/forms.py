from __future__ import annotations

from typing import Any

from django import forms

from suno.core.access import Section, SectionRole
from suno.core.context import RequestContext
from suno.core.models import (
    Equipment,
    EquipmentControlScheme,
    EquipmentDocumentType,
    EquipmentFieldDefinition,
    EquipmentFieldType,
    EquipmentMeterUnit,
    EquipmentOperatorAssignment,
    EquipmentOwnership,
    EquipmentStatus,
    EquipmentType,
    EquipmentUsageStatus,
    EquipmentWorkSource,
    FuelSource,
    FuelStation,
    FuelStore,
    FuelSupplier,
    FuelType,
    MachineryDepartment,
    Membership,
    PlanKind,
    Project,
    TransportContractor,
    TransportMaterial,
    TransportRoute,
    TransportVehicle,
    WorkOrderKind,
    WorkOrderLineKind,
)
from suno.core.scoping import scoped_qs
from suno.equipment.services import visible_equipment


class MembershipChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Membership) -> str:
        return obj.user.display_name or obj.user.username


class EquipmentTypeForm(forms.Form):
    code = forms.CharField(max_length=40, label="Código")
    name = forms.CharField(max_length=120, label="Nombre")
    description = forms.CharField(
        required=False, label="Descripción", widget=forms.Textarea(attrs={"rows": 3})
    )


class EquipmentCollectionForm(forms.Form):
    name = forms.CharField(max_length=120, label="Nombre")
    description = forms.CharField(
        required=False,
        label="Descripción",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment_types"] = forms.ModelMultipleChoiceField(
            queryset=scoped_qs(ctx, EquipmentType).filter(active=True).order_by("name"),
            required=False,
            label="Etiquetas de tipología",
            help_text="Selecciona los tipos de equipo que deben aparecer dentro de esta colección.",
            widget=forms.CheckboxSelectMultiple,
        )
        self.order_fields(["name", "description", "equipment_types"])


class EquipmentForm(forms.Form):
    code = forms.CharField(max_length=50, label="Código interno")
    name = forms.CharField(max_length=160, label="Nombre")
    manufacturer = forms.CharField(max_length=120, required=False, label="Fabricante")
    model = forms.CharField(max_length=120, required=False, label="Modelo")
    serial_number = forms.CharField(max_length=120, required=False, label="Serie")
    license_plate = forms.CharField(max_length=40, required=False, label="Placa")
    manufacture_year = forms.IntegerField(required=False, min_value=1900, label="Año")
    ownership = forms.ChoiceField(choices=EquipmentOwnership.choices, label="Propiedad")
    owner_name = forms.CharField(max_length=160, required=False, label="Propietario")
    status = forms.ChoiceField(choices=EquipmentStatus.choices, label="Estado")
    control_scheme = forms.ChoiceField(
        choices=EquipmentControlScheme.choices,
        required=False,
        initial=EquipmentControlScheme.METER,
        label="Esquema de control",
    )
    primary_meter_unit = forms.ChoiceField(
        choices=[("", "—"), *EquipmentMeterUnit.choices],
        required=False,
        label="Medidor principal",
    )
    secondary_meter_unit = forms.ChoiceField(
        choices=[("", "Ninguno"), *EquipmentMeterUnit.choices],
        required=False,
        label="Medidor secundario",
    )
    work_source = forms.ChoiceField(
        choices=EquipmentWorkSource.choices,
        required=False,
        initial=EquipmentWorkSource.WORK_REPORTS,
        label="Fuente del trabajo",
    )
    nominal_fuel_yield = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=8,
        decimal_places=2,
        label="Rendimiento teórico de combustible",
        help_text="Unidades de medidor por galón.",
    )
    fuel_tolerance_pct = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        label="Tolerancia de consumo (%)",
    )
    acquisition_value = forms.DecimalField(
        required=False, min_value=0, max_digits=16, decimal_places=2, label="Valor de adquisición"
    )
    acquisition_date = forms.DateField(
        required=False,
        label="Fecha de adquisición",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    accumulated_depreciation = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=16,
        decimal_places=2,
        label="Depreciación acumulada",
    )
    specifications = forms.JSONField(
        required=False,
        label="Especificaciones (JSON)",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    notes = forms.CharField(
        required=False, label="Observaciones", widget=forms.Textarea(attrs={"rows": 4})
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        # Los campos nuevos de la ficha son opcionales: sin valor, los de siempre.
        data["control_scheme"] = data.get("control_scheme") or EquipmentControlScheme.METER
        data["work_source"] = data.get("work_source") or EquipmentWorkSource.WORK_REPORTS
        if data["control_scheme"] == EquipmentControlScheme.METER and not data.get(
            "primary_meter_unit"
        ):
            data["primary_meter_unit"] = EquipmentMeterUnit.HOURS
        return data

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment_type"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, EquipmentType).filter(active=True),
            label="Tipo",
        )
        self.fields["department"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, MachineryDepartment).filter(active=True),
            required=False,
            label="Departamento de maquinaria",
        )
        self.order_fields(
            [
                "equipment_type",
                "department",
                "code",
                "name",
                "manufacturer",
                "model",
                "serial_number",
                "license_plate",
                "manufacture_year",
                "ownership",
                "owner_name",
                "status",
                "control_scheme",
                "primary_meter_unit",
                "secondary_meter_unit",
                "work_source",
                "nominal_fuel_yield",
                "fuel_tolerance_pct",
                "acquisition_value",
                "acquisition_date",
                "accumulated_depreciation",
                "specifications",
                "notes",
            ]
        )


class EquipmentDocumentForm(forms.Form):
    document_type = forms.ChoiceField(choices=EquipmentDocumentType.choices, label="Tipo")
    name = forms.CharField(max_length=160, label="Nombre")
    number = forms.CharField(max_length=80, required=False, label="Número")
    issued_on = forms.DateField(
        required=False,
        label="Emisión",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expires_on = forms.DateField(
        required=False,
        label="Vencimiento",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class EquipmentProjectForm(forms.Form):
    started_on = forms.DateField(label="Inicio", widget=forms.DateInput(attrs={"type": "date"}))
    ended_on = forms.DateField(
        required=False,
        label="Fin",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["project"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, Project).order_by("name"),
            label="Proyecto",
        )
        self.order_fields(["project", "started_on", "ended_on"])


class EquipmentOperatorForm(forms.Form):
    valid_from = forms.DateField(
        label="Vigente desde",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    valid_until = forms.DateField(
        required=False,
        label="Vigente hasta",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["operator"] = MembershipChoiceField(
            queryset=(
                scoped_qs(ctx, Membership)
                .filter(
                    user__section_grants__section=Section.EQUIPMENT,
                    user__section_grants__role=SectionRole.EQUIPMENT_OPERATOR,
                    user__section_grants__active=True,
                )
                .select_related("user")
                .order_by("user__username")
            ),
            label="Operador",
        )
        self.fields["project"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, Project).order_by("name"),
            label="Proyecto",
        )
        self.order_fields(["operator", "project", "valid_from", "valid_until"])


class EquipmentUsageForm(forms.Form):
    usage_date = forms.DateField(
        label="Fecha",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    start_time = forms.TimeField(
        label="Hora de inicio",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    end_time = forms.TimeField(
        label="Hora de fin",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    activity = forms.CharField(max_length=200, label="Actividad o labor")
    location = forms.CharField(
        max_length=200,
        required=False,
        label="Ubicación o frente",
    )
    status = forms.ChoiceField(
        choices=EquipmentUsageStatus.choices,
        initial=EquipmentUsageStatus.SCHEDULED,
        label="Estado",
    )
    meter_unit = forms.ChoiceField(
        choices=EquipmentMeterUnit.choices,
        initial=EquipmentMeterUnit.HOURS,
        label="Tipo de medidor",
    )
    start_meter = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Lectura inicial",
    )
    end_meter = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Lectura final",
    )
    budget_item_code = forms.CharField(
        max_length=50,
        required=False,
        label="Partida del presupuesto",
        help_text="Código de la partida a la que se imputa el costo (opcional).",
    )
    notes = forms.CharField(
        required=False,
        label="Observaciones",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["project"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, Project).order_by("name"),
            label="Proyecto",
        )
        self.fields["equipment"] = forms.ModelChoiceField(
            queryset=(scoped_qs(ctx, Equipment).select_related("equipment_type").order_by("code")),
            label="Equipo",
        )
        self.fields["operator"] = MembershipChoiceField(
            queryset=(scoped_qs(ctx, Membership).select_related("user").order_by("user__username")),
            label="Operador",
        )
        self.order_fields(
            [
                "usage_date",
                "project",
                "equipment",
                "start_time",
                "end_time",
                "operator",
                "activity",
                "location",
                "status",
                "meter_unit",
                "start_meter",
                "end_meter",
                "notes",
            ]
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")
        if start_time and end_time and end_time <= start_time:
            self.add_error("end_time", "La hora final debe ser posterior al inicio.")
        start_meter = cleaned.get("start_meter")
        end_meter = cleaned.get("end_meter")
        if start_meter is not None and end_meter is not None and end_meter < start_meter:
            self.add_error("end_meter", "La lectura final no puede ser menor que la inicial.")
        return cleaned


def _rate_field(label: str, help_text: str) -> forms.DecimalField:
    return forms.DecimalField(
        min_value=0,
        max_digits=14,
        decimal_places=2,
        initial=0,
        label=label,
        help_text=help_text,
    )


class EquipmentRateForm(forms.Form):
    """Valores por hora definidos a mano; la tarifa total es su suma."""

    valid_from = forms.DateField(
        label="Vigente desde",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    ownership_rate = _rate_field(
        "Propiedad ($/h)", "Depreciación, seguro, financiación o canon de alquiler."
    )
    operation_rate = _rate_field(
        "Operación ($/h)", "Combustible, lubricantes, llantas y consumibles."
    )
    maintenance_rate = _rate_field("Mantenimiento ($/h)", "Mantenimiento preventivo y repuestos.")
    labor_rate = _rate_field(
        "Operador ($/h)", "Costo horario del operador, si se imputa al equipo."
    )
    notes = forms.CharField(max_length=300, required=False, label="Criterio o nota")


class DepartmentForm(forms.Form):
    cost_center_code = forms.CharField(max_length=40, label="Código de centro de costo")
    name = forms.CharField(max_length=120, label="Nombre")


class FieldDefinitionForm(forms.Form):
    name = forms.CharField(max_length=80, label="Nombre del campo")
    data_type = forms.ChoiceField(choices=EquipmentFieldType.choices, label="Tipo de dato")
    alert_days = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=3650,
        label="Días de aviso previo",
        help_text="Solo para campos de fecha: avisa cuando falten estos días o menos.",
    )
    required = forms.BooleanField(required=False, label="Obligatorio")


class MeterReadingForm(forms.Form):
    value = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, label="Lectura")
    read_at = forms.DateTimeField(
        required=False,
        label="Fecha y hora",
        help_text="Déjala vacía para usar el momento actual.",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    notes = forms.CharField(max_length=300, required=False, label="Observaciones")

    def __init__(
        self, ctx: RequestContext, equipment: Equipment, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        units = [
            unit for unit in (equipment.primary_meter_unit, equipment.secondary_meter_unit) if unit
        ]
        self.fields["meter_unit"] = forms.ChoiceField(
            choices=[(unit, EquipmentMeterUnit(unit).label) for unit in units],
            label="Medidor",
        )
        self.order_fields(["meter_unit", "value", "read_at", "notes"])


class FieldValueForm(forms.Form):
    value = forms.CharField(
        max_length=300,
        required=False,
        label="Valor",
        help_text="Número, fecha (AAAA-MM-DD) o texto según el tipo del campo.",
    )

    def __init__(
        self, ctx: RequestContext, equipment: Equipment, *args: Any, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["definition"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, EquipmentFieldDefinition).filter(active=True),
            label="Campo",
        )
        self.order_fields(["definition", "value"])


class PlanForm(forms.Form):
    name = forms.CharField(max_length=120, label="Rutina", help_text="Ej.: cambio de aceite.")
    kind = forms.ChoiceField(choices=PlanKind.choices, label="Se controla")
    meter_unit = forms.ChoiceField(
        choices=[("", "—"), *EquipmentMeterUnit.choices], required=False, label="Medidor"
    )
    interval_value = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Cada (horas o km)",
    )
    interval_days = forms.IntegerField(required=False, min_value=1, label="Cada (días)")
    alert_window = forms.DecimalField(
        min_value=0,
        max_digits=12,
        decimal_places=2,
        initial=0,
        label="Avisar con anticipación",
        help_text="Horas/km antes del vencimiento (plan por medidor) o días (plan por fecha).",
    )
    last_done_value = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Medidor en la última ejecución",
        help_text="Vacío: cuenta desde la lectura actual.",
    )
    last_done_on = forms.DateField(
        required=False,
        label="Fecha de la última ejecución",
        help_text="Vacío: cuenta desde hoy.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class WorkOrderForm(forms.Form):
    kind = forms.ChoiceField(choices=WorkOrderKind.choices, label="Tipo de orden")
    description = forms.CharField(
        label="Trabajo a realizar", widget=forms.Textarea(attrs={"rows": 4})
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["mechanics"] = forms.ModelMultipleChoiceField(
            queryset=scoped_qs(ctx, Membership).select_related("user").order_by("user__username"),
            required=False,
            label="Mecánicos",
            help_text="Opcional: se pueden asignar después, hasta el cierre técnico.",
            widget=forms.CheckboxSelectMultiple,
        )
        self.fields["mechanics"].label_from_instance = lambda obj: (
            obj.user.display_name or obj.user.username
        )
        self.order_fields(["kind", "description", "mechanics"])


class WorkOrderLineForm(forms.Form):
    kind = forms.ChoiceField(choices=WorkOrderLineKind.choices, label="Tipo")
    description = forms.CharField(max_length=200, label="Descripción")
    supplier = forms.CharField(
        max_length=160,
        required=False,
        label="Proveedor",
        help_text="Obligatorio en servicios externos.",
    )
    quantity = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, label="Cantidad")
    unit_cost = forms.DecimalField(
        min_value=0, max_digits=14, decimal_places=2, label="Costo unitario"
    )


class WorkOrderCloseForm(forms.Form):
    downtime_hours = forms.DecimalField(
        min_value=0,
        max_digits=8,
        decimal_places=2,
        label="Duración de la orden (horas de indisponibilidad)",
    )
    failed_part = forms.CharField(
        max_length=160,
        required=False,
        label="Parte afectada",
        help_text="Obligatoria en correctivas y overhaul.",
    )
    failure_cause = forms.CharField(
        max_length=200,
        required=False,
        label="Causa de la falla",
        help_text="Obligatoria en correctivas y overhaul.",
    )
    notes = forms.CharField(
        required=False, label="Notas de cierre", widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["mechanics"] = forms.ModelMultipleChoiceField(
            queryset=scoped_qs(ctx, Membership).select_related("user").order_by("user__username"),
            required=False,
            label="Mecánicos que ejecutaron",
            help_text="Se suman a los ya asignados; debe quedar al menos uno.",
            widget=forms.CheckboxSelectMultiple,
        )
        self.fields["mechanics"].label_from_instance = lambda obj: (
            obj.user.display_name or obj.user.username
        )
        self.order_fields(["downtime_hours", "failed_part", "failure_cause", "mechanics", "notes"])


def _today_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"})


class FuelSupplierForm(forms.Form):
    name = forms.CharField(max_length=160, label="Proveedor")
    tax_id = forms.CharField(max_length=40, required=False, label="NIT")


class FuelStationForm(forms.Form):
    name = forms.CharField(max_length=160, label="Estación")

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["supplier"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelSupplier).filter(active=True), label="Proveedor"
        )
        self.order_fields(["supplier", "name"])


class FuelRateForm(forms.Form):
    valid_from = forms.DateField(label="Vigente desde", widget=_today_input())
    price_per_gallon = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=2, label="Precio por galón"
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["station"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelStation)
            .filter(active=True)
            .select_related("supplier")
            .order_by("supplier__name", "name"),
            label="Estación",
        )
        self.fields["station"].label_from_instance = lambda obj: f"{obj.supplier.name} · {obj.name}"
        self.order_fields(["station", "valid_from", "price_per_gallon"])


class FuelStoreForm(forms.Form):
    name = forms.CharField(max_length=160, label="Tanque o almacén")

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["project"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, Project).order_by("name"), required=False, label="Proyecto"
        )
        self.order_fields(["name", "project"])


class FuelPurchaseForm(forms.Form):
    bought_on = forms.DateField(label="Fecha de compra", widget=_today_input())
    gallons = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, label="Galones")
    unit_cost = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=2, label="Costo por galón"
    )
    supplier_name = forms.CharField(max_length=160, required=False, label="Proveedor")
    notes = forms.CharField(max_length=300, required=False, label="Observaciones")

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["store"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelStore).filter(active=True), label="Tanque"
        )
        self.order_fields(["store", "bought_on", "gallons", "unit_cost", "supplier_name", "notes"])


class FuelFillForm(forms.Form):
    source = forms.ChoiceField(choices=FuelSource.choices, label="Origen")
    fuel_type = forms.ChoiceField(choices=FuelType.choices, initial=FuelType.ACPM, label="Insumo")
    voucher_number = forms.CharField(max_length=60, label="Número de vale")
    filled_at = forms.DateTimeField(
        required=False,
        label="Fecha y hora",
        help_text="Vacío: ahora.",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    gallons = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, label="Galones")
    meter_unit = forms.ChoiceField(
        choices=[("", "—"), *EquipmentMeterUnit.choices], required=False, label="Medidor"
    )
    meter_value = forms.DecimalField(
        required=False, min_value=0, max_digits=12, decimal_places=2, label="Lectura"
    )
    work_front = forms.CharField(max_length=160, required=False, label="Frente de obra")
    driver_name = forms.CharField(max_length=160, required=False, label="Conductor")
    driver_document = forms.CharField(max_length=40, required=False, label="Cédula")
    notes = forms.CharField(max_length=300, required=False, label="Observaciones")

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment"] = forms.ModelChoiceField(
            queryset=visible_equipment(ctx).exclude(status=EquipmentStatus.RETIRED),
            label="Equipo",
        )
        self.fields["station"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelStation).filter(active=True).select_related("supplier"),
            required=False,
            label="Estación",
        )
        self.fields["station"].label_from_instance = lambda obj: f"{obj.supplier.name} · {obj.name}"
        self.fields["store"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelStore).filter(active=True),
            required=False,
            label="Tanque propio",
        )
        self.order_fields(
            [
                "equipment",
                "source",
                "station",
                "store",
                "fuel_type",
                "voucher_number",
                "filled_at",
                "gallons",
                "meter_unit",
                "meter_value",
                "work_front",
                "driver_name",
                "driver_document",
                "notes",
            ]
        )


class FuelLiquidationForm(forms.Form):
    date_from = forms.DateField(label="Desde", widget=_today_input())
    date_to = forms.DateField(label="Hasta", widget=_today_input())

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["supplier"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, FuelSupplier).filter(active=True), label="Proveedor"
        )
        self.order_fields(["supplier", "date_from", "date_to"])


class FuelConfirmForm(forms.Form):
    advance_applied = forms.DecimalField(
        min_value=0,
        max_digits=16,
        decimal_places=2,
        initial=0,
        label="Anticipo a amortizar",
        help_text="Anticipo girado al proveedor que se descuenta de esta liquidación.",
    )


class TransportMaterialForm(forms.Form):
    name = forms.CharField(max_length=120, label="Material", help_text="Ej.: gravas, derrumbes.")


class TransportRouteForm(forms.Form):
    origin = forms.CharField(max_length=160, label="Origen")
    destination = forms.CharField(max_length=160, label="Destino")
    zone = forms.CharField(max_length=120, required=False, label="Zona de trabajo")
    distance_km = forms.DecimalField(
        min_value=0, max_digits=8, decimal_places=2, label="Distancia (km)"
    )


class TransportRateForm(forms.Form):
    valid_from = forms.DateField(label="Vigente desde", widget=_today_input())
    price_per_m3 = forms.DecimalField(
        min_value=0, max_digits=12, decimal_places=2, label="Precio por m³"
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["route"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportRoute).filter(active=True), label="Tramo"
        )
        self.fields["material"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportMaterial).filter(active=True), label="Material"
        )
        self.order_fields(["route", "material", "valid_from", "price_per_m3"])


class TransportContractorForm(forms.Form):
    name = forms.CharField(max_length=160, label="Empresa de transporte")
    tax_id = forms.CharField(max_length=40, required=False, label="NIT")


class TransportVehicleForm(forms.Form):
    plate = forms.CharField(max_length=20, label="Placa")
    capacity_m3 = forms.DecimalField(
        required=False, min_value=0, max_digits=6, decimal_places=2, label="Capacidad (m³)"
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["contractor"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportContractor).filter(active=True), label="Empresa"
        )
        self.order_fields(["contractor", "plate", "capacity_m3"])


class TripForm(forms.Form):
    recorded_at = forms.DateTimeField(
        required=False,
        label="Fecha y hora",
        help_text="Vacío: ahora.",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    volume_m3 = forms.DecimalField(
        min_value=0, max_digits=8, decimal_places=2, label="Volumen (m³)"
    )
    driver_name = forms.CharField(max_length=160, required=False, label="Conductor")
    driver_document = forms.CharField(max_length=40, required=False, label="Cédula")
    notes = forms.CharField(max_length=300, required=False, label="Observaciones")

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment"] = forms.ModelChoiceField(
            queryset=visible_equipment(ctx).exclude(status=EquipmentStatus.RETIRED),
            required=False,
            label="Equipo propio",
        )
        vehicles = scoped_qs(ctx, TransportVehicle).filter(active=True).select_related("contractor")
        self.fields["vehicle"] = forms.ModelChoiceField(
            queryset=vehicles, required=False, label="Vehículo de terceros"
        )
        self.fields["vehicle"].label_from_instance = lambda obj: (
            f"{obj.plate} · {obj.contractor.name}"
        )
        self.fields["route"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportRoute).filter(active=True), label="Tramo"
        )
        self.fields["route"].label_from_instance = lambda obj: (
            f"{obj.origin} → {obj.destination} · {obj.distance_km} km"
        )
        self.fields["material"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportMaterial).filter(active=True), label="Material"
        )
        for field, label in (
            ("requested_by", "Solicita"),
            ("dispatched_by", "Despacha"),
            ("received_by", "Recibe"),
        ):
            self.fields[field] = forms.ModelChoiceField(
                queryset=scoped_qs(ctx, Membership)
                .select_related("user")
                .order_by("user__username"),
                label=label,
                to_field_name="user",
            )
            self.fields[field].label_from_instance = lambda obj: (
                obj.user.display_name or obj.user.username
            )
        self.order_fields(
            [
                "equipment",
                "vehicle",
                "route",
                "material",
                "volume_m3",
                "recorded_at",
                "requested_by",
                "dispatched_by",
                "received_by",
                "driver_name",
                "driver_document",
                "notes",
            ]
        )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if bool(data.get("equipment")) == bool(data.get("vehicle")):
            raise forms.ValidationError(
                "Elige el equipo propio o el vehículo de terceros (uno solo)."
            )
        return data


class TransportSettlementForm(forms.Form):
    date_from = forms.DateField(label="Desde", widget=_today_input())
    date_to = forms.DateField(label="Hasta", widget=_today_input())

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["contractor"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, TransportContractor).filter(active=True), label="Empresa"
        )
        self.order_fields(["contractor", "date_from", "date_to"])


class UsageReportForm(forms.Form):
    """Reporte de uso del propio operador sobre un equipo que tiene asignado."""

    usage_date = forms.DateField(label="Fecha", widget=_today_input())
    start_time = forms.TimeField(
        label="Hora de inicio", widget=forms.TimeInput(attrs={"type": "time"})
    )
    end_time = forms.TimeField(label="Hora de fin", widget=forms.TimeInput(attrs={"type": "time"}))
    activity = forms.CharField(max_length=200, label="Actividad realizada")
    location = forms.CharField(max_length=200, required=False, label="Ubicación o frente de obra")
    start_meter = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Lectura inicial",
        help_text="Obligatoria en equipos controlados por medidor.",
    )
    end_meter = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        label="Lectura final",
        help_text="Obligatoria en equipos controlados por medidor.",
    )
    notes = forms.CharField(
        max_length=500,
        required=False,
        label="Observaciones",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment"] = forms.ModelChoiceField(
            queryset=visible_equipment(ctx).exclude(status=EquipmentStatus.RETIRED), label="Equipo"
        )
        mine = (
            scoped_qs(ctx, EquipmentOperatorAssignment)
            .filter(operator_id=ctx.user_id)
            .values("project_id")
        )
        self.fields["project"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, Project).filter(pk__in=mine).order_by("name"),
            label="Proyecto",
            help_text="Solo aparecen los proyectos donde tienes equipos asignados.",
        )
        self.order_fields(
            [
                "equipment",
                "project",
                "usage_date",
                "start_time",
                "end_time",
                "activity",
                "location",
                "start_meter",
                "end_meter",
                "notes",
            ]
        )

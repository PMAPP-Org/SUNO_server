from __future__ import annotations

from typing import Any

from django import forms

from suno.core.access import Section, SectionRole
from suno.core.context import RequestContext
from suno.core.models import (
    Equipment,
    EquipmentDocumentType,
    EquipmentMeterUnit,
    EquipmentOwnership,
    EquipmentStatus,
    EquipmentType,
    EquipmentUsageStatus,
    Membership,
    Project,
)
from suno.core.scoping import scoped_qs


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
    specifications = forms.JSONField(
        required=False,
        label="Especificaciones (JSON)",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    notes = forms.CharField(
        required=False, label="Observaciones", widget=forms.Textarea(attrs={"rows": 4})
    )

    def __init__(self, ctx: RequestContext, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["equipment_type"] = forms.ModelChoiceField(
            queryset=scoped_qs(ctx, EquipmentType).filter(active=True),
            label="Tipo",
        )
        self.order_fields(
            [
                "equipment_type",
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

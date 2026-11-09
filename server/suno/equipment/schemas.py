"""Esquemas de `/v1/equipment` (`components.schemas.FieldEquipment*` del contrato)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from ninja import Schema
from pydantic import Field

__all__ = [
    "FieldEquipment",
    "FieldEquipmentList",
    "InspectionAnswerIn",
    "InspectionIn",
    "InspectionOut",
    "MeterReadingIn",
    "MeterReadingOut",
]


class FieldEquipmentType(Schema):
    id: uuid.UUID
    code: str
    name: str
    preoperational_template_id: uuid.UUID | None


class FieldEquipmentDepartment(Schema):
    code: str
    name: str


class FieldEquipmentMeter(Schema):
    unit: Literal["hours", "kilometers"]
    label: str
    is_primary: bool
    value: float
    read_at: datetime


class FieldEquipmentDocument(Schema):
    id: uuid.UUID
    type: str
    name: str
    number: str
    expires_on: str | None


class FieldEquipmentField(Schema):
    name: str
    type: Literal["number", "date", "text"]
    value: str | float | None


class FieldEquipmentAlert(Schema):
    source: Literal["field", "document"]
    name: str
    expires_on: str
    days_left: int


class FieldChecklistItem(Schema):
    id: uuid.UUID
    label: str
    critical: bool


class FieldInspectionSummary(Schema):
    id: uuid.UUID
    captured_at: datetime
    status: Literal["submitted", "approved", "rejected", "correction_requested"]
    has_faults: bool
    review_comment: str


class FieldMaintenancePlan(Schema):
    id: uuid.UUID
    name: str
    kind: Literal["meter", "date"]
    meter_unit: Literal["hours", "kilometers"] | None
    state: Literal["ok", "due_soon", "overdue", "unknown"]
    remaining: float | None
    due_value: float | None
    due_on: str | None
    has_open_order: bool


class FieldWorkOrderSummary(Schema):
    id: uuid.UUID
    code: str
    kind: Literal["preventive", "corrective", "overhaul"]
    status: Literal["draft", "approved", "in_execution", "closed_technical", "cancelled"]
    description: str


class FieldUsageProject(Schema):
    id: uuid.UUID
    name: str


class FieldUsageSummary(Schema):
    id: uuid.UUID
    usage_date: str
    start_time: str
    end_time: str
    hours: float | None
    activity: str
    project_name: str
    status: Literal["scheduled", "in_progress", "completed", "cancelled"]


class FieldEquipment(Schema):
    id: uuid.UUID
    code: str
    name: str
    qr_identifier: str
    equipment_type: FieldEquipmentType
    status: str
    ownership: str
    license_plate: str
    manufacturer: str
    model: str
    serial_number: str
    manufacture_year: int | None
    department: FieldEquipmentDepartment | None
    control_scheme: Literal["meter", "date"]
    work_source: Literal["fuel", "work_reports"]
    current_project_id: uuid.UUID | None
    meters: list[FieldEquipmentMeter]
    meter_units: list[Literal["hours", "kilometers"]]
    documents: list[FieldEquipmentDocument]
    fields: list[FieldEquipmentField]
    alerts: list[FieldEquipmentAlert]
    checklist: list[FieldChecklistItem]
    recent_inspections: list[FieldInspectionSummary]
    usage_projects: list[FieldUsageProject]
    recent_usage: list[FieldUsageSummary]
    maintenance_plans: list[FieldMaintenancePlan]
    open_work_orders: list[FieldWorkOrderSummary]


class FieldEquipmentList(Schema):
    version: str = Field(max_length=64)
    changed: bool
    items: list[FieldEquipment]


class MeterReadingIn(Schema):
    """Lectura enviada por el teléfono. `id` lo genera el cliente: reenviarla es idempotente."""

    id: uuid.UUID
    meter_unit: Literal["hours", "kilometers"]
    value: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    read_at: datetime
    notes: str = Field(default="", max_length=300)


class MeterReadingOut(Schema):
    id: uuid.UUID
    meter_unit: Literal["hours", "kilometers"]
    value: float
    read_at: datetime


class InspectionAnswerIn(Schema):
    item_id: uuid.UUID
    ok: bool
    note: str = Field(default="", max_length=500)


class InspectionIn(Schema):
    """Preoperacional del teléfono. `id` lo genera el cliente: reenviarlo es idempotente."""

    id: uuid.UUID
    captured_at: datetime
    meter_unit: Literal["hours", "kilometers"] | None = None
    meter_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    items: list[InspectionAnswerIn] = Field(max_length=200)
    notes: str = Field(default="", max_length=1000)


class InspectionOut(Schema):
    id: uuid.UUID
    status: Literal["submitted", "approved", "rejected", "correction_requested"]
    has_faults: bool


class FuelStationItem(Schema):
    id: uuid.UUID
    name: str
    supplier: str


class FuelStoreItem(Schema):
    id: uuid.UUID
    name: str


class FuelCatalog(Schema):
    version: str = Field(max_length=64)
    changed: bool
    stations: list[FuelStationItem]
    stores: list[FuelStoreItem]


class FuelFillIn(Schema):
    """Tanqueo del teléfono. `id` lo genera el cliente: reenviarlo es idempotente."""

    id: uuid.UUID
    source: Literal["station", "store"]
    station_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    fuel_type: Literal["acpm", "gasoline", "other"] = "acpm"
    voucher_number: str = Field(min_length=1, max_length=60)
    filled_at: datetime
    gallons: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    meter_unit: Literal["hours", "kilometers"] | None = None
    meter_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    work_front: str = Field(default="", max_length=160)
    driver_name: str = Field(default="", max_length=160)
    driver_document: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=300)


class FuelFillOut(Schema):
    id: uuid.UUID
    voucher_number: str


class TransportRouteItem(Schema):
    id: uuid.UUID
    origin: str
    destination: str
    zone: str
    distance_km: float


class TransportMaterialItem(Schema):
    id: uuid.UUID
    name: str


class TransportVehicleItem(Schema):
    id: uuid.UUID
    plate: str
    contractor: str
    capacity_m3: float | None


class TransportPersonItem(Schema):
    id: uuid.UUID
    name: str


class TransportCatalog(Schema):
    version: str = Field(max_length=64)
    changed: bool
    routes: list[TransportRouteItem]
    materials: list[TransportMaterialItem]
    vehicles: list[TransportVehicleItem]
    people: list[TransportPersonItem]


class TripIn(Schema):
    """Viaje del teléfono. `id` lo genera el cliente: reenviarlo es idempotente."""

    id: uuid.UUID
    recorded_at: datetime
    route_id: uuid.UUID
    material_id: uuid.UUID
    volume_m3: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    equipment_id: uuid.UUID | None = None
    vehicle_id: uuid.UUID | None = None
    requested_by_id: uuid.UUID
    dispatched_by_id: uuid.UUID
    received_by_id: uuid.UUID
    driver_name: str = Field(default="", max_length=160)
    driver_document: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=300)


class TripOut(Schema):
    id: uuid.UUID
    code: str
    number: int


class UsageReportIn(Schema):
    """Reporte de uso del teléfono. `id` lo genera el cliente: reenviarlo es idempotente."""

    id: uuid.UUID
    project_id: uuid.UUID
    usage_date: date
    start_time: time
    end_time: time
    activity: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    start_meter: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    end_meter: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    notes: str = Field(default="", max_length=500)


class UsageReportOut(Schema):
    id: uuid.UUID
    hours: float | None
    status: Literal["scheduled", "in_progress", "completed", "cancelled"]

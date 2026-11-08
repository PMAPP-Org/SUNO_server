"""Transporte de materiales (M&E fase 5): rutas, tarifas, viajes y liquidación de fletes.

La unidad de control es el viaje. Cada viaje fija, al registrarse, la distancia del tramo
y la tarifa por m³ de su material (instantánea: una tarifa posterior no reescribe el
histórico), y exige tres usuarios: quien solicita, quien despacha y quien recibe.
Los fletes de flota de terceros se liquidan por contratista en un periodo.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel
from .fuel import LiquidationStatus


class TransportMaterial(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, verbose_name="material")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_material"
        ordering = ["name"]
        verbose_name = "material transportado"
        verbose_name_plural = "materiales transportados"
        constraints = [
            models.UniqueConstraint(Lower("name"), "org", name="transport_material_name_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class TransportRoute(OrgScopedModel):
    """Tramo origen–destino del mapa de distancias del proyecto."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    origin = models.CharField(max_length=160, verbose_name="origen")
    destination = models.CharField(max_length=160, verbose_name="destino")
    zone = models.CharField(max_length=120, blank=True, verbose_name="zona de trabajo")
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="distancia (km)")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_route"
        ordering = ["zone", "origin", "destination"]
        verbose_name = "tramo"
        verbose_name_plural = "tramos"
        constraints = [
            models.UniqueConstraint(
                Lower("origin"), Lower("destination"), "org", name="transport_route_pair_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(distance_km__gt=0), name="transport_route_distance_ok"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.origin} → {self.destination}"


class TransportRate(OrgScopedModel):
    """Tarifa de flete por m³ de un material en un tramo, vigente desde una fecha."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    route = models.ForeignKey(
        TransportRoute, on_delete=models.PROTECT, related_name="rates", verbose_name="tramo"
    )
    material = models.ForeignKey(
        TransportMaterial, on_delete=models.PROTECT, related_name="rates", verbose_name="material"
    )
    valid_from = models.DateField(verbose_name="vigente desde")
    price_per_m3 = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="precio por m³"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="transport_rates_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_rate"
        ordering = ["-valid_from"]
        verbose_name = "tarifa de flete"
        verbose_name_plural = "tarifas de flete"
        constraints = [
            models.UniqueConstraint(
                fields=["route", "material", "valid_from"], name="transport_rate_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_m3__gt=0), name="transport_rate_price_ok"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        for field in ("route", "material", "created_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})


class TransportContractor(OrgScopedModel):
    """Empresa de transporte de terceros (p. ej. Transportes TDR)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, verbose_name="empresa")
    tax_id = models.CharField(max_length=40, blank=True, verbose_name="NIT")
    active = models.BooleanField(default=True, verbose_name="activa")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_contractor"
        ordering = ["name"]
        verbose_name = "contratista de transporte"
        verbose_name_plural = "contratistas de transporte"
        constraints = [
            models.UniqueConstraint(Lower("name"), "org", name="transport_contractor_name_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class TransportVehicle(OrgScopedModel):
    """Vehículo subcontratado, identificado por su placa."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contractor = models.ForeignKey(
        TransportContractor,
        on_delete=models.PROTECT,
        related_name="vehicles",
        verbose_name="contratista",
    )
    plate = models.CharField(max_length=20, verbose_name="placa")
    capacity_m3 = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="capacidad (m³)"
    )
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_vehicle"
        ordering = ["contractor__name", "plate"]
        verbose_name = "vehículo de terceros"
        verbose_name_plural = "vehículos de terceros"
        constraints = [
            models.UniqueConstraint(Lower("plate"), "org", name="transport_vehicle_plate_uq"),
            models.CheckConstraint(
                condition=models.Q(capacity_m3__isnull=True) | models.Q(capacity_m3__gt=0),
                name="transport_vehicle_capacity_ok",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.contractor_id and self.org_id and self.contractor.org_id != self.org_id:
            raise ValidationError({"contractor": "Debe pertenecer a la organización."})

    def __str__(self) -> str:
        return self.plate


class TransportSettlement(OrgScopedModel):
    """Liquidación de fletes de un contratista: agrupa sus viajes de un periodo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(verbose_name="número")
    contractor = models.ForeignKey(
        TransportContractor,
        on_delete=models.PROTECT,
        related_name="settlements",
        verbose_name="contratista",
    )
    date_from = models.DateField(verbose_name="desde")
    date_to = models.DateField(verbose_name="hasta")
    status = models.CharField(
        max_length=10,
        choices=LiquidationStatus.choices,
        default=LiquidationStatus.DRAFT,
        verbose_name="estado",
    )
    total_m3 = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"), verbose_name="m³"
    )
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0"), verbose_name="valor total"
    )
    advance_applied = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0"), verbose_name="anticipo amortizado"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="transport_settlements_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transport_settlements_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "transport_settlement"
        ordering = ["-number"]
        verbose_name = "liquidación de fletes"
        verbose_name_plural = "liquidaciones de fletes"
        constraints = [
            models.UniqueConstraint(fields=["org", "number"], name="transport_settlement_number"),
            models.CheckConstraint(
                condition=models.Q(date_to__gte=models.F("date_from")),
                name="transport_settlement_dates_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft")
                | models.Q(advance_applied__gte=0, advance_applied__lte=models.F("total_amount")),
                name="transport_settlement_advance_ok",
            ),
        ]

    @property
    def code(self) -> str:
        return f"LF-{self.number:04d}"

    @property
    def net_payable(self) -> Decimal:
        return self.total_amount - self.advance_applied

    def clean(self) -> None:
        super().clean()
        for field in ("contractor", "created_by", "confirmed_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})


class Trip(OrgScopedModel):
    """Viaje de material. El `id` lo genera el cliente (idempotencia)."""

    id = models.UUIDField(primary_key=True, editable=False)
    number = models.PositiveIntegerField(verbose_name="consecutivo")
    recorded_at = models.DateTimeField(verbose_name="fecha y hora")
    equipment = models.ForeignKey(
        "core.Equipment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="trips",
        verbose_name="equipo propio",
    )
    vehicle = models.ForeignKey(
        TransportVehicle,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="trips",
        verbose_name="vehículo de terceros",
    )
    project = models.ForeignKey(
        "core.Project",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="trips",
        verbose_name="proyecto",
    )
    driver_name = models.CharField(max_length=160, blank=True, verbose_name="conductor")
    driver_document = models.CharField(max_length=40, blank=True, verbose_name="cédula")
    route = models.ForeignKey(
        TransportRoute, on_delete=models.PROTECT, related_name="trips", verbose_name="tramo"
    )
    distance_km = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="distancia (km)")
    material = models.ForeignKey(
        TransportMaterial, on_delete=models.PROTECT, related_name="trips", verbose_name="material"
    )
    volume_m3 = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="volumen (m³)")
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="precio por m³"
    )
    amount = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )
    requested_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="trips_requested",
        verbose_name="solicita",
    )
    dispatched_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="trips_dispatched",
        verbose_name="despacha",
    )
    received_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="trips_received",
        verbose_name="recibe",
    )
    settlement = models.ForeignKey(
        TransportSettlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="trips",
        verbose_name="liquidación",
    )
    notes = models.CharField(max_length=300, blank=True, verbose_name="observaciones")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="trips_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transport_trip"
        ordering = ["-number"]
        verbose_name = "viaje"
        verbose_name_plural = "viajes"
        constraints = [
            models.UniqueConstraint(fields=["org", "number"], name="trip_org_number_uq"),
            models.CheckConstraint(condition=models.Q(volume_m3__gt=0), name="trip_volume_ok"),
            models.CheckConstraint(
                condition=(
                    models.Q(equipment__isnull=False, vehicle__isnull=True)
                    | models.Q(equipment__isnull=True, vehicle__isnull=False)
                ),
                name="trip_exactly_one_vehicle",
            ),
            models.CheckConstraint(
                condition=~models.Q(dispatched_by=models.F("received_by")),
                name="trip_dispatcher_not_receiver",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "recorded_at"], name="trip_recorded_idx"),
            models.Index(fields=["org", "vehicle", "recorded_at"], name="trip_vehicle_idx"),
        ]

    @property
    def code(self) -> str:
        return f"TV-{self.number:06d}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in (
            "equipment",
            "vehicle",
            "project",
            "route",
            "material",
            "requested_by",
            "dispatched_by",
            "received_by",
            "settlement",
            "created_by",
        ):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if errors:
            raise ValidationError(errors)

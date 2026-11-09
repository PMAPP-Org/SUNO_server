"""Control de combustibles de equipos (M&E fase 4).

Dos formas de abastecer, como en SINCO M&E:
- Estación de terceros: el tanqueo se registra con su vale y no toca ningún inventario;
  se liquida por proveedor aplicando la tarifa por galón de cada estación.
- Tanque propio: se compra combustible al tanque y cada salida a un equipo lo descuenta
  del stock, al costo promedio ponderado de las compras.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel


class FuelType(models.TextChoices):
    ACPM = "acpm", "ACPM"
    GASOLINE = "gasoline", "Gasolina"
    OTHER = "other", "Otro"


class FuelSource(models.TextChoices):
    STATION = "station", "Estación de servicio"
    STORE = "store", "Tanque propio"


class LiquidationStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    CONFIRMED = "confirmed", "Confirmada"


class FuelSupplier(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, verbose_name="proveedor")
    tax_id = models.CharField(max_length=40, blank=True, verbose_name="NIT")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_supplier"
        ordering = ["name"]
        verbose_name = "proveedor de combustible"
        verbose_name_plural = "proveedores de combustible"
        constraints = [
            models.UniqueConstraint(Lower("name"), "org", name="fuel_supplier_org_name_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class FuelStation(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(
        FuelSupplier,
        on_delete=models.PROTECT,
        related_name="stations",
        verbose_name="proveedor",
    )
    name = models.CharField(max_length=160, verbose_name="estación")
    active = models.BooleanField(default=True, verbose_name="activa")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_station"
        ordering = ["supplier__name", "name"]
        verbose_name = "estación de servicio"
        verbose_name_plural = "estaciones de servicio"
        constraints = [
            models.UniqueConstraint(
                Lower("name"), "supplier", name="fuel_station_supplier_name_uq"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.supplier_id and self.org_id and self.supplier.org_id != self.org_id:
            raise ValidationError({"supplier": "Debe pertenecer a la organización."})

    def __str__(self) -> str:
        return f"{self.supplier_id}:{self.name}"


class FuelStationRate(OrgScopedModel):
    """Tarifa por galón de una estación, vigente desde una fecha (histórico inmutable)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    station = models.ForeignKey(
        FuelStation, on_delete=models.PROTECT, related_name="rates", verbose_name="estación"
    )
    valid_from = models.DateField(verbose_name="vigente desde")
    price_per_gallon = models.DecimalField(
        max_digits=12, decimal_places=2, verbose_name="precio por galón"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="fuel_rates_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_station_rate"
        ordering = ["-valid_from"]
        verbose_name = "tarifa de estación"
        verbose_name_plural = "tarifas de estaciones"
        constraints = [
            models.UniqueConstraint(
                fields=["station", "valid_from"], name="fuel_rate_station_date"
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_gallon__gt=0), name="fuel_rate_price_positive"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        for field in ("station", "created_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})


class FuelStore(OrgScopedModel):
    """Tanque o almacén propio de combustible (modelo 1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, verbose_name="tanque o almacén")
    project = models.ForeignKey(
        "core.Project",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fuel_stores",
        verbose_name="proyecto",
    )
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_store"
        ordering = ["name"]
        verbose_name = "tanque de combustible"
        verbose_name_plural = "tanques de combustible"
        constraints = [
            models.UniqueConstraint(Lower("name"), "org", name="fuel_store_org_name_uq"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.project_id and self.org_id and self.project.org_id != self.org_id:
            raise ValidationError({"project": "Debe pertenecer a la organización."})

    def __str__(self) -> str:
        return self.name


class FuelPurchase(OrgScopedModel):
    """Compra de combustible al tanque propio: suma al stock y fija su costo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(
        FuelStore, on_delete=models.PROTECT, related_name="purchases", verbose_name="tanque"
    )
    bought_on = models.DateField(verbose_name="fecha de compra")
    gallons = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="galones")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="costo por galón")
    supplier_name = models.CharField(max_length=160, blank=True, verbose_name="proveedor")
    notes = models.CharField(max_length=300, blank=True, verbose_name="observaciones")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="fuel_purchases_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_purchase"
        ordering = ["-bought_on", "-created_at"]
        verbose_name = "compra de combustible"
        verbose_name_plural = "compras de combustible"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gallons__gt=0, unit_cost__gte=0),
                name="fuel_purchase_amounts_ok",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        for field in ("store", "created_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})


class FuelLiquidation(OrgScopedModel):
    """Liquidación a un proveedor de estaciones: agrupa sus tanqueos de un periodo."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(verbose_name="número")
    supplier = models.ForeignKey(
        FuelSupplier,
        on_delete=models.PROTECT,
        related_name="liquidations",
        verbose_name="proveedor",
    )
    date_from = models.DateField(verbose_name="desde")
    date_to = models.DateField(verbose_name="hasta")
    status = models.CharField(
        max_length=10,
        choices=LiquidationStatus.choices,
        default=LiquidationStatus.DRAFT,
        verbose_name="estado",
    )
    total_gallons = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0"), verbose_name="galones"
    )
    total_amount = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0"), verbose_name="valor total"
    )
    advance_applied = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("0"), verbose_name="anticipo amortizado"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="fuel_liquidations_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fuel_liquidations_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "fuel_liquidation"
        ordering = ["-number"]
        verbose_name = "liquidación de combustible"
        verbose_name_plural = "liquidaciones de combustible"
        constraints = [
            models.UniqueConstraint(fields=["org", "number"], name="fuel_liquidation_number_uq"),
            models.CheckConstraint(
                condition=models.Q(date_to__gte=models.F("date_from")),
                name="fuel_liquidation_dates_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(advance_applied__gte=0)
                & models.Q(advance_applied__lte=models.F("total_amount"))
                | models.Q(status="draft"),
                name="fuel_liquidation_advance_ok",
            ),
        ]

    @property
    def code(self) -> str:
        return f"LQ-{self.number:04d}"

    @property
    def net_payable(self) -> Decimal:
        return self.total_amount - self.advance_applied

    def clean(self) -> None:
        super().clean()
        for field in ("supplier", "created_by", "confirmed_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                raise ValidationError({field: "Debe pertenecer a la organización."})

    def __str__(self) -> str:
        return self.code


class FuelFill(OrgScopedModel):
    """Tanqueo o salida de combustible a un equipo. El `id` lo genera el cliente."""

    id = models.UUIDField(primary_key=True, editable=False)
    equipment = models.ForeignKey(
        "core.Equipment", on_delete=models.PROTECT, related_name="fuel_fills", verbose_name="equipo"
    )
    project = models.ForeignKey(
        "core.Project",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fuel_fills",
        verbose_name="proyecto",
    )
    source = models.CharField(max_length=8, choices=FuelSource.choices, verbose_name="origen")
    station = models.ForeignKey(
        FuelStation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fills",
        verbose_name="estación",
    )
    store = models.ForeignKey(
        FuelStore,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fills",
        verbose_name="tanque",
    )
    fuel_type = models.CharField(
        max_length=10, choices=FuelType.choices, default=FuelType.ACPM, verbose_name="insumo"
    )
    voucher_number = models.CharField(max_length=60, verbose_name="número de vale")
    filled_at = models.DateTimeField(verbose_name="fecha y hora")
    gallons = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="galones")
    meter_unit = models.CharField(max_length=16, blank=True, verbose_name="medidor")
    meter_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="lectura"
    )
    work_front = models.CharField(max_length=160, blank=True, verbose_name="frente de obra")
    driver_name = models.CharField(max_length=160, blank=True, verbose_name="conductor")
    driver_document = models.CharField(max_length=40, blank=True, verbose_name="cédula")
    notes = models.CharField(max_length=300, blank=True, verbose_name="observaciones")
    # Instantánea del precio al registrar (tarifa de la estación o costo promedio del tanque).
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="precio por galón"
    )
    cost = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )
    liquidation = models.ForeignKey(
        FuelLiquidation,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fills",
        verbose_name="liquidación",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="fuel_fills_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fuel_fill"
        ordering = ["-filled_at"]
        verbose_name = "tanqueo"
        verbose_name_plural = "tanqueos"
        constraints = [
            models.CheckConstraint(condition=models.Q(gallons__gt=0), name="fuel_fill_gallons_ok"),
            models.CheckConstraint(
                condition=(
                    models.Q(source="station", station__isnull=False, store__isnull=True)
                    | models.Q(source="store", store__isnull=False, station__isnull=True)
                ),
                name="fuel_fill_source_consistent",
            ),
            models.UniqueConstraint(
                fields=["org", "station", "voucher_number"],
                condition=models.Q(station__isnull=False),
                name="fuel_fill_station_voucher_uq",
            ),
            models.UniqueConstraint(
                fields=["org", "store", "voucher_number"],
                condition=models.Q(store__isnull=False),
                name="fuel_fill_store_voucher_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "equipment", "filled_at"], name="fuel_fill_equipment_idx"),
            models.Index(fields=["org", "station", "filled_at"], name="fuel_fill_station_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        for field in ("equipment", "project", "station", "store", "liquidation", "created_by"):
            value = getattr(self, field, None)
            if value is not None and self.org_id and value.org_id != self.org_id:
                errors[field] = "Debe pertenecer a la organización."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.equipment_id}:{self.voucher_number}"

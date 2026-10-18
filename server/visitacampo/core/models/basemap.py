"""Paquetes de mapa base para trabajar sin red (Fase B).

Un paquete es un archivo **MBTiles ráster** (teselas PNG/JPEG/WebP en una base
SQLite): un extracto de OpenStreetMap de la zona del proyecto o una ortofoto o
vuelo de dron propio. Lo importa un administrador desde el servidor
(`manage.py import_basemap`) y los teléfonos de su organización lo descargan,
con wifi, por una URL prefirmada de vida corta (`/v1/basemaps`).

Por qué paquetes propios y no teselas de un tercero: en campo no hay red, y un
proveedor de teselas en línea sabría en cada momento dónde trabaja cada agente
(Ley 1581, minimización). El paquete viaja por el mismo canal autenticado y
cifrado que el resto, y el teléfono lo verifica con su `sha256`.

El archivo vive en el almacenamiento de objetos bajo `basemaps/{sha256}.mbtiles`
(`attachments/blobstore.py::basemap_key_for`), fuera del prefijo de adjuntos.
Retirar un paquete es un borrado lógico (`deleted_at`): deja de ofrecerse y
los teléfonos lo borran en su siguiente sincronización de mapas.
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .base import OrgScopedModel


class TileFormat(models.TextChoices):
    PNG = "png", "PNG"
    JPEG = "jpg", "JPEG"
    WEBP = "webp", "WebP"


class BasemapPackage(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("nombre", max_length=128)
    sha256 = models.CharField(max_length=64, editable=False)
    size = models.PositiveBigIntegerField(
        "tamaño (bytes)", editable=False, validators=[MinValueValidator(1)]
    )
    tile_format = models.CharField(
        "formato de tesela", max_length=8, choices=TileFormat.choices, editable=False
    )
    min_zoom = models.PositiveSmallIntegerField(editable=False, validators=[MaxValueValidator(24)])
    max_zoom = models.PositiveSmallIntegerField(editable=False, validators=[MaxValueValidator(24)])
    west = models.FloatField(
        editable=False, validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )
    south = models.FloatField(
        editable=False, validators=[MinValueValidator(-90), MaxValueValidator(90)]
    )
    east = models.FloatField(
        editable=False, validators=[MinValueValidator(-180), MaxValueValidator(180)]
    )
    north = models.FloatField(
        editable=False, validators=[MinValueValidator(-90), MaxValueValidator(90)]
    )
    uploaded_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="basemap_packages",
        verbose_name="importado por",
        editable=False,
    )
    created_at = models.DateTimeField("importado", auto_now_add=True)
    deleted_at = models.DateTimeField(
        "retirado", null=True, blank=True, default=None, editable=False
    )

    class Meta:
        db_table = "basemap_package"
        verbose_name = "mapa base"
        verbose_name_plural = "mapas base"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_zoom__lte=models.F("max_zoom")), name="basemap_zoom_order"
            ),
            models.CheckConstraint(
                condition=models.Q(south__lte=models.F("north")), name="basemap_lat_order"
            ),
        ]
        indexes = [models.Index(fields=["org", "deleted_at"], name="basemap_org_live_idx")]

    def __str__(self) -> str:
        return self.name

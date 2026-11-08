"""Capas de referencia por organización (Fase C), como las capas de un proyecto QField.

Una capa es un **GeoJSON** (RFC 7946, WGS84) con lo que el agente necesita ver
sobre el mapa base: predios, lotes, vías, puntos de muestreo. La importa un
administrador desde el servidor (`manage.py import_layer`) y los teléfonos de
su organización la descargan y la dibujan; al tocar una entidad se ven sus
atributos.

El archivo vive en el almacenamiento bajo `layers/{sha256}.geojson`, fuera del
prefijo de adjuntos. Retirarla es un borrado lógico (`deleted_at`).
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .base import OrgScopedModel


class ReferenceLayer(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("nombre", max_length=128)
    sha256 = models.CharField(max_length=64, editable=False)
    size = models.PositiveBigIntegerField(
        "tamaño (bytes)", editable=False, validators=[MinValueValidator(1)]
    )
    feature_count = models.PositiveIntegerField("entidades", editable=False)
    #: Tipos de geometría presentes (`["Point", "Polygon"]`…), ordenados.
    geometry_types = models.JSONField("geometrías", editable=False, default=list)
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
        related_name="reference_layers",
        verbose_name="importada por",
        editable=False,
    )
    created_at = models.DateTimeField("importada", auto_now_add=True)
    deleted_at = models.DateTimeField(
        "retirada", null=True, blank=True, default=None, editable=False
    )

    class Meta:
        db_table = "reference_layer"
        verbose_name = "capa de referencia"
        verbose_name_plural = "capas de referencia"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(south__lte=models.F("north")), name="layer_lat_order"
            ),
            models.CheckConstraint(
                condition=models.Q(west__lte=models.F("east")), name="layer_lon_order"
            ),
        ]
        indexes = [models.Index(fields=["org", "deleted_at"], name="layer_org_live_idx")]

    def __str__(self) -> str:
        return self.name

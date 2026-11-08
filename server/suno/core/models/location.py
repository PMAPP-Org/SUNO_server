"""Ubicación de un proyecto (costos, pieza 4): niveles y árbol de nodos.

Los niveles van precisando el lugar (Tramo → PR → Costado). Cada nodo cuelga
del nodo del nivel anterior; las tareas y las cantidades de campo apuntan a un
nodo. Los nodos nunca se borran: se retiran (`retired_at`).
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel

#: Profundidad máxima del árbol.
MAX_LEVELS = 6


class LocationLevel(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="location_levels"
    )
    name = models.CharField(max_length=60, verbose_name="nombre")
    depth = models.PositiveSmallIntegerField(verbose_name="profundidad")
    #: `FieldID` del campo personalizado de MS Project que lo alimenta
    #: (p. ej. "188743731" = Text1). Vacío: la ubicación se asigna en SUNO.
    source_field = models.CharField(max_length=20, blank=True, verbose_name="campo de Project")

    class Meta:
        db_table = "location_level"
        ordering = ["depth"]
        constraints = [
            models.UniqueConstraint(fields=["project", "depth"], name="location_level_depth_uq"),
            models.UniqueConstraint(Lower("name"), "project", name="location_level_name_uq"),
            models.CheckConstraint(
                condition=models.Q(depth__gte=1, depth__lte=MAX_LEVELS),
                name="location_level_depth_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class LocationNode(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="location_nodes"
    )
    level = models.ForeignKey(LocationLevel, on_delete=models.PROTECT, related_name="nodes")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    name = models.CharField(max_length=120, verbose_name="nombre")
    #: `name` normalizado (sin tildes, minúsculas, espacios colapsados): identidad.
    key = models.CharField(max_length=120)
    #: `/<hex del nivel 1>/…/<hex de este nodo>/`: un nodo está dentro de otro
    #: si su `path` empieza por el del otro.
    path = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "location_node"
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "parent", "key"],
                name="location_node_key_uq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name

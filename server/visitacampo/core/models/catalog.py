"""Catálogo: plantillas de formulario y asignaciones.

Ninguna de estas tablas está especificada campo a campo en doc 08 §4.4
(solo aparecen en el ER). La referencia usada para sus campos es el
**contrato de API del Paso 1** (`server/openapi/v1.yaml`,
`components.schemas.FormTemplateVersion` y `.Assignment`), que es el único
sitio donde su forma ya estaba decidida. Se mantienen deliberadamente
mínimas:

- `FormTemplate`/`FormTemplateVersion`: la **definición declarativa** y su
  validación JSON Schema son del Paso 20 (ADR-012). Aquí `definition` es
  un `JSONField` sin validación de contenido.
- `Assignment`: la planificación completa (rutas, reasignación, estados)
  **no es alcance del MVP estricto**. Este modelo es el placeholder que el
  brief autoriza: existe para que `Visit.assignment_id` sea una FK real y
  para que el pull pueda entregar asignaciones, nada más. Ampliarlo es una
  migración aditiva, no un rediseño.
"""

from __future__ import annotations

import uuid

from django.core.validators import MinValueValidator
from django.db import models

from .base import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    OrgScopedModel,
    SyncedModel,
    lat_validators,
    lon_validators,
)


class FormTemplate(OrgScopedModel):
    """Plantilla de formulario. Agrupa sus versiones inmutables.

    No es una entidad sincronizable (el contrato solo sincroniza
    `form_template_version`), así que no lleva `version`/`change_seq`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=256, verbose_name="nombre")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "form_template"
        verbose_name = "plantilla de formulario"
        verbose_name_plural = "plantillas de formulario"
        indexes = [
            models.Index(fields=["org", "name"], name="form_template_org_name_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class FormTemplateVersion(SyncedModel):
    """Versión **inmutable** de una plantilla (ADR-012).

    Cada visita referencia la versión exacta usada, de modo que las
    visitas históricas siguen siendo interpretables tras cambios de
    formulario. "Inmutable" es aquí una invariante de aplicación (no se
    reescribe una versión publicada, se publica otra): a diferencia de
    `Blob`, la fila sí participa del protocolo de sync y necesita
    `version`/`change_seq`/`deleted_at`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(
        FormTemplate,
        on_delete=models.PROTECT,
        related_name="versions",
        verbose_name="plantilla",
    )
    name = models.CharField(max_length=256, null=True, blank=True)
    version_number = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    # Definición declarativa; **nunca** contiene código ejecutable (ADR-012).
    # El JSON Schema que la valida se implementa en el Paso 20.
    definition = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "form_template_version"
        verbose_name = "versión de plantilla"
        verbose_name_plural = "versiones de plantilla"
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version_number"],
                name="ftv_template_version_number_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(version_number__gte=1),
                name="ftv_version_number_gte_1",
            ),
        ]
        indexes = [
            # Escaneo ordenado del cursor de pull (doc 08 §4.4).
            models.Index(fields=["org", "change_seq"], name="ftv_org_change_seq_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name or self.template_id} v{self.version_number}"


class Assignment(SyncedModel):
    """Trabajo planificado que origina visitas (placeholder mínimo del MVP).

    Campos tomados 1:1 del contrato `components.schemas.Assignment`. El
    cliente no la modifica: se descarga por el pull.
    """

    # UUID de servidor: las asignaciones las crea el back-office, no el
    # dispositivo (a diferencia de `Visit`/`VisitAttachment`).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template_version = models.ForeignKey(
        FormTemplateVersion,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="versión de plantilla",
    )
    assigned_to = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="asignada a",
    )
    title = models.CharField(max_length=256, null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, default=None)
    # Ubicación como columnas planas, igual que en `Visit` (ADR-005).
    lat = models.FloatField(null=True, blank=True, default=None, validators=lat_validators())
    lon = models.FloatField(null=True, blank=True, default=None, validators=lon_validators())

    class Meta:
        db_table = "assignment"
        verbose_name = "asignación"
        verbose_name_plural = "asignaciones"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(lat__isnull=True)
                | models.Q(lat__gte=LAT_MIN, lat__lte=LAT_MAX),
                name="assignment_lat_range",
            ),
            models.CheckConstraint(
                condition=models.Q(lon__isnull=True)
                | models.Q(lon__gte=LON_MIN, lon__lte=LON_MAX),
                name="assignment_lon_range",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "change_seq"], name="assignment_org_seq_idx"),
            models.Index(fields=["org", "assigned_to"], name="assignment_org_agent_idx"),
        ]

    def __str__(self) -> str:
        return self.title or str(self.id)

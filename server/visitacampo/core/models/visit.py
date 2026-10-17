"""Núcleo del dominio: `Visit`, `Blob` y `VisitAttachment` (doc 08 §4.4), más
`VisitReview` (validación del back-office, Paso 24)."""

from __future__ import annotations

import uuid

from django.core.validators import MinValueValidator
from django.db import models

from .base import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    AttachmentRole,
    MimeType,
    OrgScopedModel,
    SyncedModel,
    finite_q,
    lat_validators,
    lon_validators,
)


class Visit(SyncedModel):
    """Visita de campo. Tabla `visit` de doc 08 §4.4, campo por campo.

    - `id` **generado en el cliente** (UUIDv7): es lo que hace la
      idempotencia real del push (ADR-003). Por eso el campo **no tiene
      `default`**: el servidor nunca inventa el identificador de una visita.
    - `answers` en `JSONB`, validado contra el JSON Schema de
      `template_version_id` (Paso 20) — evita una migración por formulario.
    - Ubicación en **columnas planas**, no geometría (ADR-005), con `CHECK`
      de rango e índice B-tree compuesto `(org_id, lat, lon)` para
      *bounding box*.
    """

    id = models.UUIDField(primary_key=True, editable=False, verbose_name="id (cliente)")
    assignment = models.ForeignKey(
        "core.Assignment",
        on_delete=models.PROTECT,
        related_name="visits",
        null=True,
        blank=True,
        default=None,
        verbose_name="asignación",
    )
    template_version = models.ForeignKey(
        "core.FormTemplateVersion",
        on_delete=models.PROTECT,
        related_name="visits",
        verbose_name="versión de plantilla",
    )
    answers = models.JSONField(default=dict, blank=True, verbose_name="respuestas")

    lat = models.FloatField(null=True, blank=True, default=None, validators=lat_validators())
    lon = models.FloatField(null=True, blank=True, default=None, validators=lon_validators())
    accuracy_m = models.FloatField(
        null=True,
        blank=True,
        default=None,
        validators=[MinValueValidator(0)],
        verbose_name="precisión (m)",
    )
    altitude_m = models.FloatField(null=True, blank=True, default=None, verbose_name="altitud (m)")

    captured_at = models.DateTimeField(verbose_name="capturada en")
    device = models.ForeignKey(
        "core.Device",
        on_delete=models.PROTECT,
        related_name="visits",
        verbose_name="dispositivo",
    )
    created_by = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="visits",
        verbose_name="creada por",
    )

    class Meta:
        db_table = "visit"
        verbose_name = "visita"
        verbose_name_plural = "visitas"
        constraints = [
            # ADR-005: rango de coordenadas garantizado por la BD. La forma
            # `IS NULL OR (rango)` es necesaria porque la ubicación es
            # opcional (el contrato admite `lat`/`lon` nulos) y en SQL un
            # `NULL BETWEEN ...` evalúa a NULL, que un CHECK acepta — pero
            # dejarlo implícito sería frágil ante futuros cambios.
            models.CheckConstraint(
                condition=models.Q(lat__isnull=True) | models.Q(lat__gte=LAT_MIN, lat__lte=LAT_MAX),
                name="visit_lat_range",
            ),
            models.CheckConstraint(
                condition=models.Q(lon__isnull=True) | models.Q(lon__gte=LON_MIN, lon__lte=LON_MAX),
                name="visit_lon_range",
            ),
            # `accuracy_m` y `altitude_m`: no negativa la primera, y **finitas**
            # las dos (revisión del Paso 8, ver `finite_q`). El `>= 0` a secas
            # no bastaba: en PostgreSQL `NaN >= 0` e `Infinity >= 0` son las dos
            # `true`, así que la guarda anterior dejaba entrar los dos venenos
            # en `accuracy_m`; y `altitude_m`, sin ninguna guarda, los dejaba
            # entrar todos. `lat`/`lon` sí quedan cubiertas por su rango
            # (`NaN <= 90` es `false`), y por eso no se tocan.
            models.CheckConstraint(
                condition=models.Q(accuracy_m__isnull=True)
                | (models.Q(accuracy_m__gte=0) & finite_q("accuracy_m")),
                name="visit_accuracy_finite_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(altitude_m__isnull=True) | finite_q("altitude_m"),
                name="visit_altitude_finite",
            ),
            # Destino de la FK compuesta de `visit_attachment` (migración
            # 0002): Postgres exige un UNIQUE sobre exactamente las columnas
            # referenciadas. Es también la invariante que impide que una
            # visita "cambie de organización" sin cambiar de identidad.
            models.UniqueConstraint(fields=["id", "org"], name="visit_id_org_uq"),
        ]
        indexes = [
            # Bounding box (ADR-005).
            models.Index(fields=["org", "lat", "lon"], name="visit_org_lat_lon_idx"),
            # Escaneo ordenado del cursor de pull (doc 08 §4.4).
            models.Index(fields=["org", "change_seq"], name="visit_org_change_seq_idx"),
        ]

    def __str__(self) -> str:
        return str(self.id)


class Blob(models.Model):
    """Contenido inmutable direccionado por su hash. **RD-08 por construcción**.

    ⚠️ NO AÑADIR CAMPOS A ESTA TABLA SIN RELEER RD-08 (doc 08 §3).

    El hallazgo HIGH de la referencia (`File.latest_version`, lost-update)
    es *inexpresable* aquí porque no existe ningún puntero mutable: la
    clave primaria **es** el contenido (`sha256`), un blob nunca se
    sobrescribe y las cuatro columnas restantes (`size`, `mime`,
    `bucket_key`, `created_at`) quedan fijadas en el `INSERT`. Cualquier
    columna de tipo "última versión", "estado", "actualizado_en" o
    "referencias" reintroduciría exactamente la clase de bug que RD-08
    elimina.

    **Sin `org_id`, a propósito** (única excepción a ADR-011 junto con
    `Organization`, que es la raíz del scoping):

    - doc 08 §4.4 lista sus campos de forma cerrada y `org_id` no está.
    - La deduplicación entre dispositivos —y entre organizaciones— es el
      objetivo declarado ("un mismo blob puede estar en varias visitas sin
      duplicar bytes"); un `org_id` la rompería por definición.
    - No es una tabla de negocio: no contiene datos del tenant, solo un
      hash, un tamaño, un MIME y una clave de almacenamiento. La
      pertenencia a una organización vive en `visit_attachment`, que sí
      lleva `org_id`, y es ahí donde se aplica el aislamiento (RD-11).
    - Conocer el `sha256` de un blob implica poseer ya sus bytes exactos,
      así que la tabla no filtra nada por sí sola. La autorización de
      descarga se resuelve por `visit_attachment` (Paso 10), nunca
      consultando `blob` directamente.
    """

    sha256 = models.CharField(max_length=64, primary_key=True, editable=False)
    size = models.PositiveBigIntegerField(editable=False, validators=[MinValueValidator(1)])
    mime = models.CharField(max_length=64, choices=MimeType.choices, editable=False)
    # `blobs/{sha256[0:2]}/{sha256[2:4]}/{sha256}` (ADR-007). Se deriva del
    # hash, pero se persiste para no acoplar la lectura al esquema de rutas.
    bucket_key = models.TextField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "blob"
        verbose_name = "blob"
        verbose_name_plural = "blobs"
        constraints = [
            models.CheckConstraint(condition=models.Q(size__gte=1), name="blob_size_positive"),
        ]

    def __str__(self) -> str:
        return self.sha256


class VisitAttachment(SyncedModel):
    """Uso de un blob dentro de una visita (doc 08 §4.4).

    Separa "qué archivo" (`Blob`, inmutable y compartido) de "dónde se
    usa". Un mismo blob puede estar en varias visitas sin duplicar bytes.

    **`sha256` es FK a `blob` pero sin constraint en la BD**
    (`db_constraint=False`) — decisión no trivial, se explica entera:

    doc 08 §4.4 pide `sha256 FK→blob` y el contrato del Paso 1 declara
    `VisitAttachment.sha256` como `required` y **no nulo**. Pero el mismo
    contrato dice que "una visita se acepta aunque el blob todavía no esté
    subido: el adjunto queda pendiente y se confirma por el carril de
    adjuntos". Y una fila de `blob` no puede existir antes de la subida:
    `size`/`mime`/`bucket_key` solo se conocen cuando el servidor verifica
    el objeto, y RD-08 prohíbe añadirle a `blob` un campo de estado
    "pendiente". Es decir: la fila hija (`visit_attachment`) precede
    legítimamente a la padre (`blob`).

    Las tres salidas posibles eran (a) columna nullable — rompe el
    contrato, que emite `sha256` siempre; (b) crear un `blob` placeholder
    — rompe RD-08; (c) FK sin constraint física. Se elige (c): la columna
    es `NOT NULL` y conserva la relación en el ORM (`attachment.blob`,
    `attachment.blob_id` == el hash), pero Postgres no exige que el blob
    exista todavía. La integridad "el blob existe" se comprueba donde
    importa: al emitir la URL de descarga (Paso 10).

    **Aislamiento entre organizaciones (tarea 12 del brief)**: se aplica
    en la BD con una **FK compuesta** `(visit_id, org_id) → visit (id,
    org_id)`, añadida en la migración `0002` con `RunSQL` reversible
    (Django no sabe expresar FKs compuestas). Un `CHECK` no serviría: no
    puede leer otra tabla. Con esa FK, insertar un adjunto de la org A
    apuntando a una visita de la org B falla en Postgres, con
    independencia de lo que haga el código de aplicación. El `scoped_qs`
    del Paso 6 sigue siendo necesario para las *lecturas* (RD-11); esta
    constraint solo cubre las *escrituras*, que es donde el dato podría
    corromperse de forma permanente.
    """

    id = models.UUIDField(primary_key=True, editable=False, verbose_name="id (cliente)")
    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        related_name="attachments",
        verbose_name="visita",
    )
    blob = models.ForeignKey(
        Blob,
        on_delete=models.PROTECT,  # protección a nivel de ORM; ver docstring.
        related_name="attachments",
        db_column="sha256",
        db_constraint=False,
        verbose_name="blob",
    )
    role = models.CharField(max_length=16, choices=AttachmentRole.choices, verbose_name="rol")
    caption = models.CharField(max_length=512, null=True, blank=True, verbose_name="pie")
    captured_at = models.DateTimeField(verbose_name="capturado en")
    # `order` es palabra reservada en SQL; Django la cita siempre. Se
    # conserva el nombre del contrato en lugar de renombrar la columna.
    order = models.PositiveIntegerField(default=0, verbose_name="orden")

    class Meta:
        db_table = "visit_attachment"
        verbose_name = "adjunto de visita"
        verbose_name_plural = "adjuntos de visita"
        indexes = [
            models.Index(fields=["org", "change_seq"], name="attachment_org_seq_idx"),
            models.Index(fields=["visit", "order"], name="attachment_visit_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.role}:{self.id}"


class VisitReviewStatus(models.TextChoices):
    APPROVED = "approved", "Validada"
    REJECTED = "rejected", "Rechazada"


class VisitReview(OrgScopedModel):
    """Validación o rechazo de una visita por supervisión (Paso 24, back-office).

    **Tabla aparte y no un campo de `Visit`**, por dos razones:

    - `Visit` es una entidad sincronizada: un campo nuevo cambiaría el contrato
      que leen los teléfonos (`VisitRecord`), y la revisión es un dato del
      back-office que el agente no edita.
    - Editar una `Visit` desde el Admin la escribiría sin pasar por el push,
      sin `change_seq` nuevo: los teléfonos nunca recibirían el cambio. Por eso
      el Admin de `Visit` es de solo lectura, y la supervisión escribe aquí.

    Es un **historial**: cada decisión es una fila nueva, y la vigente es la
    más reciente. Rechazar y luego validar deja las dos decisiones registradas,
    con quién y cuándo (ISO 27001 A.8.15).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit = models.ForeignKey(
        Visit,
        on_delete=models.PROTECT,
        related_name="reviews",
        verbose_name="visita",
    )
    status = models.CharField(
        max_length=16, choices=VisitReviewStatus.choices, verbose_name="decisión"
    )
    comment = models.CharField(max_length=512, null=True, blank=True, verbose_name="comentario")
    reviewer = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="visit_reviews",
        verbose_name="revisada por",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha")

    class Meta:
        db_table = "visit_review"
        verbose_name = "revisión de visita"
        verbose_name_plural = "revisiones de visita"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["org", "visit", "-created_at"], name="review_org_visit_idx")
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()}:{self.visit_id}"

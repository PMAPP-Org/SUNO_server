"""Piezas compartidas del modelo de datos: enumeraciones y bases abstractas.

Referencias normativas:
- doc 08 §4.4 "Modelo de datos (servidor)" — tablas y campos.
- ADR-011 — `org_id NOT NULL` en toda tabla de negocio desde la primera
  migración; modelo de identidad **plano**, sin herencia multi-tabla.
- ADR-003 — `version` (CAS optimista) y `change_seq` (cursor de pull).
- ADR-005 — ubicación en columnas planas con `CHECK` de rango.

**Sobre la prohibición de herencia multi-tabla (ADR-011 / N-09)**: lo que
ADR-011 descarta es la *multi-table inheritance* de Django (el patrón
`User`→`Person`/`Organization`/`Team` de QFieldCloud), que crea una tabla
por subclase y un JOIN implícito en cada consulta. Las clases de este
módulo son **abstractas** (`Meta.abstract = True`): Django no les crea
tabla ni JOIN alguno; son reutilización de código a nivel de Python y
cada modelo concreto sigue siendo exactamente una tabla plana.
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# --- Rangos de coordenadas (ADR-005) --------------------------------------
LAT_MIN, LAT_MAX = -90.0, 90.0
LON_MIN, LON_MAX = -180.0, 180.0

# Retención del log de idempotencia y de los tombstones (doc 08 §4.4).
# Aquí solo se documenta el número; la purga es un job del Paso 19.
RETENTION_DAYS = 90

# Valor inicial de `version` en una fila recién creada (ADR-003).
#
# DECISIÓN (el brief delegaba elegir 0 o 1): **1**. Motivos:
#   - `version` se lee como "número de revisión": la primera revisión es la 1,
#     y `version` acaba siendo el número de escrituras aplicadas a la fila.
#   - El contrato (`components.schemas.Version`, `minimum: 0`) admite ambos;
#     no hay restricción externa que fuerce 0.
#   - El `create` del push envía `base_version: null` (nunca 0), así que 0 no
#     tiene ningún papel especial en el protocolo y usarlo como valor inicial
#     solo invitaría a confundir "sin versión" con "versión 0".
# Los Pasos 8-9 (`WHERE version = base_version` ... `SET version = version + 1`)
# dependen de este valor: no cambiarlo sin migrar los datos existentes.
INITIAL_VERSION = 1


class Role(models.TextChoices):
    """Roles de fase 1 (ADR-011). Fase 2 añadirá `TEAM` como agrupación."""

    OWNER = "OWNER", "Propietario"
    ADMIN = "ADMIN", "Administrador"
    SUPERVISOR = "SUPERVISOR", "Supervisor"
    FIELD_AGENT = "FIELD_AGENT", "Agente de campo"
    VIEWER = "VIEWER", "Observador"


class Platform(models.TextChoices):
    """Plataformas del cliente (contrato `DeviceInfo.platform`)."""

    ANDROID = "android", "Android"
    IOS = "ios", "iOS"


class MimeType(models.TextChoices):
    """Allow-list de MIME para adjuntos (ADR-007, contrato `Mime`)."""

    IMAGE_JPEG = "image/jpeg", "JPEG"
    IMAGE_PNG = "image/png", "PNG"
    IMAGE_WEBP = "image/webp", "WebP"
    APPLICATION_PDF = "application/pdf", "PDF"


class AttachmentRole(models.TextChoices):
    """Rol de un adjunto dentro de la visita (doc 08 §4.4, contrato)."""

    FOTO = "foto", "Foto"
    FIRMA = "firma", "Firma"
    DOC = "doc", "Documento"


class SyncEntityType(models.TextChoices):
    """Entidades que participan en la sincronización (contrato `EntityType`)."""

    VISIT = "visit", "Visita"
    VISIT_ATTACHMENT = "visit_attachment", "Adjunto de visita"
    ASSIGNMENT = "assignment", "Asignación"
    FORM_TEMPLATE_VERSION = "form_template_version", "Versión de plantilla"


class JobStatus(models.TextChoices):
    """Estados de la cola de jobs (contrato `JobStatus`, ADR-006)."""

    PENDING = "pending", "Pendiente"
    RUNNING = "running", "En ejecución"
    SUCCEEDED = "succeeded", "Completado"
    FAILED = "failed", "Fallido"
    DEAD_LETTER = "dead_letter", "Dead letter"


def lat_validators() -> list[object]:
    """Validadores Python del rango de latitud.

    Duplican a propósito el `CHECK` de la BD: el `CHECK` es la garantía
    real (se cumple venga la escritura de donde venga), y estos
    validadores permiten que `full_clean()` dé un `ValidationError` con
    nombre de campo *antes* de tocar la BD (mejor mensaje de error en el
    back-office del Paso 17). Nunca sustituyen al `CHECK`.
    """
    return [MinValueValidator(LAT_MIN), MaxValueValidator(LAT_MAX)]


def lon_validators() -> list[object]:
    """Validadores Python del rango de longitud (ver `lat_validators`)."""
    return [MinValueValidator(LON_MIN), MaxValueValidator(LON_MAX)]


class OrgScopedModel(models.Model):
    """Base abstracta de **toda tabla de negocio**: `org_id NOT NULL` (ADR-011).

    `on_delete=PROTECT` en lugar de `CASCADE` a propósito: borrar una
    organización nunca debe llevarse por delante sus datos en silencio.
    Los borrados de negocio son lógicos (`deleted_at`), no físicos.
    """

    org = models.ForeignKey(
        "core.Organization",
        on_delete=models.PROTECT,
        related_name="%(class)ss",
        verbose_name="organización",
    )

    class Meta:
        abstract = True


class SyncedModel(OrgScopedModel):
    """Base abstracta de las entidades que viajan por `/v1/sync/*`.

    Añade las tres columnas del protocolo (ADR-003):

    - `version`: guarda CAS optimista. El push hace
      `... WHERE version = base_version` y sube `version + 1` (Paso 8).
    - `change_seq`: cursor de pull. Se asigna **dentro** de la transacción
      de escritura mientras se sostiene `pg_advisory_xact_lock(org_id)`
      (doc 08 §4.4); esa lógica es del Paso 8, aquí solo existe la columna
      y el índice `(org_id, change_seq)` que la hace escaneable en orden.
      Deliberadamente **sin `default`**: una escritura que olvide asignar
      `change_seq` debe fallar ruidosamente (`NOT NULL`) en vez de crear
      una fila que el cursor de pull nunca devolvería.
    - `deleted_at`: tombstone (borrado lógico). Purga a los 90 días
      (`RETENTION_DAYS`), implementada como job en el Paso 19.

    No se declara `UNIQUE (org_id, change_seq)`: si un lote de push debe
    compartir o no un mismo `change_seq` entre filas es una decisión del
    Paso 8, y una unicidad puesta hoy la cerraría por adelantado. El
    índice no único ya cubre el escaneo por cursor.
    """

    version = models.PositiveIntegerField(
        default=INITIAL_VERSION,
        verbose_name="versión (CAS)",
    )
    change_seq = models.PositiveBigIntegerField(
        verbose_name="secuencia de cambio",
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        verbose_name="borrado lógico",
    )

    class Meta(OrgScopedModel.Meta):
        abstract = True

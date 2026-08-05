"""Límites operativos que el cliente lee del servidor (contrato `ServerCapabilities`).

El contrato lo dice explícitamente: estos valores "se leen del servidor en
lugar de codificarse en la app, para poder ajustarlos sin publicar una
release". Por eso viven aquí, en un módulo de constantes citadas, y no
repartidos por las vistas de sync que los aplicarán.

**Este módulo declara; no valida.** Quien tiene que rechazar un lote de 501
operaciones es el endpoint de push (Paso 8), leyendo de aquí. Si algún día
alguien cambia un número sin tocar el validador correspondiente, el cliente
creería tener un permiso que el servidor no concede — por eso los Pasos 8-10
deben importar estas constantes en vez de repetir el literal.
"""

from __future__ import annotations

from visitacampo.core.models import MimeType

__all__ = [
    "ATTACHMENT_ALLOWED_MIME",
    "ATTACHMENT_MAX_BYTES",
    "ATTACHMENT_MAX_BYTES_PER_VISIT",
    "MIN_SUPPORTED_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SYNC_PULL_DEFAULT_LIMIT",
    "SYNC_PULL_MAX_LIMIT",
    "SYNC_PUSH_MAX_BYTES",
    "SYNC_PUSH_MAX_OPS",
    "capabilities",
]

#: Versión del *payload* de sincronización (RD-03, contrato `SchemaVersion`).
#: Fase 0 emite la primera y única versión.
SCHEMA_VERSION = 1

#: Versión mínima que este servidor sigue aceptando. Hoy coincide con
#: `SCHEMA_VERSION` porque no hay ninguna anterior; cuando haya una v2, esto
#: es lo que permite seguir sirviendo a clientes que aún no se han
#: actualizado, y subirlo es la decisión explícita de dejar de hacerlo.
MIN_SUPPORTED_SCHEMA_VERSION = 1

#: ADR-003: máximo de operaciones por lote de push.
SYNC_PUSH_MAX_OPS = 500
#: ADR-003: máximo de bytes por lote **ya descomprimido** (5 MiB).
SYNC_PUSH_MAX_BYTES = 5 * 1024 * 1024
#: Máximo de entidades por página de pull (contrato: `maximum: 1000`).
SYNC_PULL_MAX_LIMIT = 1000
#: `limit` de `GET /v1/sync/pull` cuando el cliente no lo especifica
#: (contrato: `default: 500`). No se anuncia en `ServerCapabilities` —el
#: contrato solo declara el límite duro, no el valor por defecto de un query
#: param opcional— así que vive aquí como constante interna del servidor, no
#: como parte de la superficie que el cliente negocia.
SYNC_PULL_DEFAULT_LIMIT = 500

#: ADR-007: tamaño máximo por adjunto individual (25 MiB). El contrato lo deja
#: "configurable" y sin ejemplo, así que este es el valor de partida de Fase 0:
#: holgado para una foto de campo o un PDF firmado, y suficientemente acotado
#: para que una subida no monopolice el ancho de banda de un VPS pequeño
#: (S-01). Ajustarlo no requiere publicar una release del cliente, que es
#: justo la razón de que se anuncie aquí.
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

#: ADR-007 punto 5 / tarea 5 del Paso 17: tope **acumulado** por visita (200
#: MiB). El límite por archivo no basta: sin este, una sola visita podría
#: acumular miles de adjuntos de 25 MiB y convertir la cuota de
#: almacenamiento en un problema de una tarde. 200 MiB deja sitio de sobra
#: para el reportaje fotográfico completo de una visita de campo (≈ 8 fotos
#: en el peor caso, decenas en el realista) y sigue siendo un techo.
#:
#: **Se cuenta sobre blobs ya confirmados y adjuntos vivos**: los adjuntos con
#: tombstone no ocupan cuota, porque el agente que corrige una foto no debería
#: pagar dos veces por el mismo hueco.
ATTACHMENT_MAX_BYTES_PER_VISIT = 200 * 1024 * 1024

#: Allow-list de MIME (ADR-007). Se deriva de `MimeType` para que la lista que
#: se anuncia y la que el modelo acepta no puedan divergir.
ATTACHMENT_ALLOWED_MIME: tuple[str, ...] = tuple(MimeType.values)


def capabilities() -> dict[str, object]:
    """Las capacidades tal y como las espera el contrato."""
    return {
        "sync_push_max_ops": SYNC_PUSH_MAX_OPS,
        "sync_push_max_bytes": SYNC_PUSH_MAX_BYTES,
        "sync_pull_max_limit": SYNC_PULL_MAX_LIMIT,
        "attachment_max_bytes": ATTACHMENT_MAX_BYTES,
        "attachment_allowed_mime": list(ATTACHMENT_ALLOWED_MIME),
        # No está entre las `required` de `components.schemas.
        # ServerCapabilities`, pero el esquema declara
        # `additionalProperties: true`, así que anunciarlo es válido contra el
        # contrato. Y merece anunciarse: sin este número el cliente no puede
        # saber que una foto más va a ser rechazada hasta haberla subido, que
        # es exactamente el gasto de datos móviles que ADR-007 quiere evitar.
        "attachment_max_bytes_per_visit": ATTACHMENT_MAX_BYTES_PER_VISIT,
    }

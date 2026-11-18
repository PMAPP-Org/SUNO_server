# ADR-0021: Mapas base offline como paquetes MBTiles ráster por organización

- **Estado:** Aceptada (reemplaza el punto «PMTiles» de [ADR-0012](0012-formularios-declarativos.md))
- **Fecha:** 2026-10-18

## Contexto

En campo no hay red, pero el agente necesita ver dónde está y dónde están sus asignaciones. ADR-0012 dejaba el mapa base para la fase 2 con `flutter_map` y PMTiles.

## Decisión

- Mapas base como archivos **MBTiles ráster** (PNG/JPEG/WebP) por organización. Se importan con `manage.py import_basemap` (no por el Admin, para que archivos de cientos de MB no pasen por Django) y se validan: SQLite real, tablas `metadata` y `tiles`, zoom 0–24 y extensión. Los vectoriales se rechazan.
- Se guardan como `basemaps/<sha256>.mbtiles`, fuera de `blobs/` para que el GC de adjuntos no los toque, y se descargan por URL prefirmada.
- La app los descarga sola, reanuda con `Range`, verifica el `sha256` y los lee con `sqlite3` como proveedor de teselas de `flutter_map`.

## Consecuencias

- Sin motor SIG ni teselas vectoriales; la app reutiliza `sqlite3`, que ya estaba en el árbol.
- Los paquetes ráster pesan más que los vectoriales.

## Referencias

- PR #27 (`5792ce7`); SUNO_client PR #24, #26 y #27.
- [`../operacion/mapas-base.md`](../operacion/mapas-base.md), `server/suno/basemaps/mbtiles.py`.

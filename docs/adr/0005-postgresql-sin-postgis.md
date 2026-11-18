# ADR-0005: PostgreSQL 16 sin PostGIS

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

SUNO guarda un punto por registro (y, desde la Fase C, líneas y polígonos como respuestas de formulario). No hay edición vectorial ni consultas espaciales en el servidor.

## Decisión

PostgreSQL 16 puro. La ubicación va en columnas (`lat`, `lon`, `accuracy_m`, `altitude_m`) con `CHECK` de rango y finitud. PostGIS solo se habilita ante un disparador documentado: punto en polígono, clustering con más de 100 000 puntos o exportaciones con geometrías calculadas en la BD.

## Consecuencias

- Sirve cualquier Postgres gestionado; las actualizaciones no dependen de PostGIS.
- Las exportaciones GeoJSON y GeoPackage se arman en Python (`backoffice/spatial_export.py`), no en la BD.

## Referencias

- [Doc 08 §5, ADR-005](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `infra/docker-compose.yml` (`postgres:16`), `server/suno/core/models/record.py`.

# ADR-0007: Adjuntos en S3, direccionados por contenido y con subida prefirmada

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

Las fotos son casi todos los bytes y el mayor riesgo de costo. QFieldCloud versiona archivos y sufrió un lost-update en `latest_version`.

## Decisión

1. Almacenamiento S3-compatible desde el día 1 detrás del puerto `BlobStore` (`S3BlobStore`, `LocalFsBlobStore`).
2. Clave por contenido `blobs/aa/bb/<sha256>`; un blob nunca se sobrescribe.
3. Subida directa del teléfono con URL prefirmada; los bytes no pasan por el API.
4. Commit verificado con `HEAD`: tamaño y checksum certificados por el almacenamiento.
5. Allow-list de MIME, reconocimiento por los primeros bytes y nombre de archivo generado.
6. Descarga por URL prefirmada tras comprobar permisos.

## Consecuencias

- Deduplicación gratis; el lost-update es imposible por construcción.
- GC de subidas sin confirmar a las 24 h.
- El mismo puerto guarda mapas base (`basemaps/`), capas y los soportes de costos subidos desde la web (`put_blob`, pieza 6a).

## Referencias

- [Doc 08 §5, ADR-007](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/attachments/`. Paso 17 (`22db400`).

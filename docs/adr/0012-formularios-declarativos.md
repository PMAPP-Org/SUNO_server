# ADR-0012: Formularios como datos declarativos versionados

- **Estado:** Aceptada. El punto del mapa base («`flutter_map` + PMTiles») lo reemplaza [ADR-0021](0021-mapas-base-mbtiles-raster.md).
- **Fecha:** 2026-07-30

## Contexto

Los plugins QML de QField permitieron un path traversal con ejecución remota. SUNO necesita cambiar formularios sin publicar la app, pero no ejecutar código remoto.

## Decisión

`FormTemplate` + `FormTemplateVersion` con definición JSON declarativa y un DSL de visibilidad restringido y sin efectos. Cada registro apunta a la versión exacta. Doble validación (app y servidor) contra la misma definición. Las plantillas viajan por el pull.

## Consecuencias

- Desaparece la clase RCE/path traversal (RD-06).
- Lo que el DSL no expresa se implementa en el binario y se referencia por nombre.
- La paridad app ↔ servidor se prueba con un fixture común (`server/openapi/fixtures/forms_parity.json`). Desde C5 también se importan XLSForm acotados.

## Referencias

- [Doc 08 §5, ADR-012](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- `server/suno/forms/`. Paso 20 (`f623e9f`), PR #37 (C5).

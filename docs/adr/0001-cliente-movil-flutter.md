# ADR-0001: Cliente móvil en Flutter, un solo codebase

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

Hace falta Android e iOS, fluidez en gama media y baja, formularios densos, cámara, GPS y una base local robusta, con un solo desarrollador. No hay un motor SIG pesado que obligue a Qt/QGIS como en QField.

## Decisión

Flutter (canal estable, Dart 3) como único codebase. Capas `core/` (dominio y sync, sin UI), `data/` (Drift, HTTP, almacenamiento seguro) y `ui/`/`features/` con Riverpod. Modelos con `freezed` (RD-02/RD-03). Toda dependencia nueva se justifica en un presupuesto de dependencias.

## Consecuencias

- Una sola implementación del motor de sync, probada en Dart puro.
- Dependencia de la salud de Flutter y de paquetes de terceros, mitigada con `DEPENDENCIES.md` en SUNO_client.
- Hoy solo se publica el APK de Android; iOS no se distribuye.

## Referencias

- [Doc 08 §5, ADR-001](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records) (alternativas: React Native, KMP, nativo, Qt/QML, PWA).
- SUNO_client: `DEPENDENCIES.md` y `docs/adr/` (decisiones propias de la app).

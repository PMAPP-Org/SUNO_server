# ADR-0002: Almacén local SQLite con Drift y SQLCipher

- **Estado:** Aceptada
- **Fecha:** 2026-07-30

## Contexto

El patrón outbox exige guardar la fila de negocio y su operación de sync en la misma transacción, sobrevivir a cierres abruptos y cifrar en reposo: los teléfonos se pierden y los datos de campo son sensibles.

## Decisión

SQLite vía Drift, cifrado con SQLCipher y la llave en Keychain/Keystore. Modo WAL, `synchronous=NORMAL` y un único escritor (RD-04). La outbox es una tabla más.

## Consecuencias

- Atomicidad real entre dato y outbox; streams de Drift para la UI.
- Toda migración local lleva su prueba N-1 → N (`CONTRIBUTING.md` de SUNO_client).
- **Nota de implementación:** `sqlcipher_flutter_libs`, citado en doc 08, quedó descontinuado; SQLCipher se activa con los *hooks* de `sqlite3` 3.x (`pubspec.yaml`). La decisión no cambia, solo el empaquetado (ADR propio en SUNO_client).

## Referencias

- [Doc 08 §5, ADR-002](../08-arquitectura-propuesta-suno.md#5-architecture-decision-records).
- SUNO_client: `lib/data/local/database.dart`, `DEPENDENCIES.md`.

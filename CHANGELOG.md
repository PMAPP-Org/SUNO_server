# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/), versionado [SemVer](https://semver.org/lang/es/).

Cada release la genera automáticamente [release-please](https://github.com/googleapis/release-please) (`.github/workflows/release.yml`) a partir de los mensajes de commit en `master`, que siguen [Conventional Commits](https://www.conventionalcommits.org/es/v1.0.0/). No se edita a mano: lo que deba aparecer aquí se describe en el título del PR (`feat: …`, `fix: …`), que con *Squash and merge* es el mensaje del commit.

## [0.1.0] (2026-08-07)

Línea base de versionado: el estado del repositorio en el commit `dd2d536`, anterior a la línea base de seguridad ISO/IEC 27001 y OWASP Top 10:2025. No tiene tag publicado. La primera release automática enumerará, a partir de aquí, todos los cambios posteriores.

### Agregado

- Backend Django 5 + Django Ninja: autenticación con JWT EdDSA, refresh tokens hasheados (`selector.verifier`) con rotación y rate limiting de login (django-axes); sync push/pull con cursor incremental y tombstones; adjuntos direccionados por contenido con presign/commit verificado; formularios declarativos con meta-schema versionado; cola de trabajos con lease, heartbeat y reaper (Paso 19, sin revisión previa).
- App Flutter: almacén local cifrado (Drift + SQLCipher) con un único escritor, motor de sync (outbox, CAS, cursor), autenticación cliente con TLS estricto, login, captura con GPS y una foto, y sincronización manual.
- Infraestructura: Docker Compose con Caddy y PostgreSQL, imagen del backend, contrato OpenAPI 3.1 y CI inicial.

Para el detalle previo a esta versión, ver `git log`; no se reconstruye retroactivamente como changelog.

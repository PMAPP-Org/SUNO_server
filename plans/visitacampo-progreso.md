# Progreso de ejecución — VisitaCampo

**Seguimiento de**: [visitacampo-construccion.md](visitacampo-construccion.md)
**Última actualización**: 2026-08-01
**Cómo mantener este archivo**: al cerrar un paso (commit + comandos de verificación en verde), marcar su casilla aquí y anotar el/los commit(s) correspondientes. Si un paso se reabre por un hallazgo de revisión posterior, anotarlo como sub-ítem bajo el paso original en vez de crear una entrada nueva — el plan no numera "pasos de corrección" aparte.

---

## Fase 0 — Cimientos

### Track Infraestructura

- [x] **Paso 0** — Inicialización del monorepo y control de versiones
  `c98f177` Inicializa monorepo VisitaCampo: estructura app/server/infra/docs
- [x] **Paso 1** — Contrato de API v1 (OpenAPI 3.1 + esquemas Pydantic)
  `fccfe9d` Define el contrato de API v1 en OpenAPI 3.1 (Paso 1)
  `a39923e` Corrige hallazgos de revisión del Paso 1 (contrato API v1)
- [x] **Paso 2** — Docker Compose de desarrollo (Caddy, Postgres, esqueleto de servicios)
  `d16443f` Añade Docker Compose de desarrollo: Caddy, Postgres, esqueleto de servicios (Paso 2)
- [x] **Paso 3** — Pipelines de CI y gates de seguridad/calidad
  `07ff987` Añade pipelines de CI y gate RD-01 anti-bypass TLS (Paso 3)
  `e800a5c` Corrige alcance del allow-list de TrustStore en el lint RD-01 (Paso 3)
  `2e349af` Exige postgres real en CI: services block + VISITACAMPO_REQUIRE_DB=1

### Track Backend (Django)

- [x] **Paso 4** — Esqueleto del backend: Django 5 + Django Ninja + settings + Argon2
  `416100a` Crea el esqueleto del backend: Django 5 + Django Ninja + Argon2 (Paso 4)
- [x] **Paso 5** — Modelo de datos núcleo y migración inicial
  `90fd888` Añade el modelo de datos núcleo y la migración inicial (Paso 5)
- [x] **Paso 6** — RequestContext, funciones de política puras y `scoped_qs`
  `7c11875` Añade el núcleo de autorización: RequestContext, políticas y scoped_qs (Paso 6)
  `6e6b091` Evita que el caso negativo del test de arquitectura escriba en producción
- [x] **Paso 7** — Autenticación mínima: login + JWT + registro de dispositivo
  `f53017d` Añade autenticación mínima: login, JWT Ed25519 y refresh hasheado (Paso 7)
  `5f6a7f8` Cierra logout: revoca todas las familias vivas del dispositivo, no solo la presentada
- [x] **Paso 8** — Endpoint `POST /v1/sync/push` (aplicación transaccional síncrona)
  `fa0834a` Añade el motor de sincronización: POST /v1/sync/push (Paso 8)
  `6979f4e` Corrige hallazgos C-1/C-2/I-1 de la revisión del Paso 8 (surrogates Unicode no emparejados, floats no finitos en lat/lon/accuracy_m/altitude_m, manejadores de error para que ningún /v1/* responda el 500 desnudo de Django)
- [x] **Paso 9** — Endpoint `GET /v1/sync/pull` (cursor incremental, tombstones, bootstrap)
  `bdee2b0` Añade GET /v1/sync/pull y GET /v1/sync/bootstrap (Paso 9)

### Track Móvil (Flutter)

- [x] **Paso 10** — Esqueleto Flutter: arquitectura en capas, freezed, Riverpod, CI
  `82af44c` Crea el esqueleto Flutter: arquitectura en capas, freezed, Riverpod (Paso 10)
- [x] **Paso 11** — Almacén local: Drift + SQLCipher, escritor único, migraciones
  `169694a` Añade el almacén local: Drift + SQLCipher, escritor único, migraciones (Paso 11)
- [ ] **Paso 12** — `SyncEngine`: dominio puro del protocolo de sync (outbox, CAS, cursor) — **siguiente paso**
- [ ] **Paso 13** — Autenticación cliente mínima + base de TLS estricto

### Integración

- [ ] **Paso 14** — Integración end-to-end: cierre de Fase 0

---

## Fase 1 — MVP

### Backend

- [ ] **Paso 15** — Endurecimiento de autenticación: rotación, detección de reuso, rate limiting
- [ ] **Paso 17** — Adjuntos backend: `BlobStore`, presign/commit, verificación
- [ ] **Paso 19** — Cola de trabajos: `SKIP LOCKED` + lease + heartbeat + reaper
- [ ] **Paso 20** — Formularios declarativos: `form_template` + JSON Schema versionado

### Móvil

- [ ] **Paso 16** — Autenticación cliente completa: `TrustStore`, TOFU explícito, pinning SPKI
- [ ] **Paso 18** — Adjuntos móvil: subida en segundo plano con reintento
- [ ] **Paso 21** — Renderer dinámico de formularios en Flutter
- [ ] **Paso 22** — Captura de visita: UI de formulario + GPS + fotos + firma
- [ ] **Paso 23** — UI de conflictos: `quarantine` y reenvío

### Integración final y cierre

- [ ] **Paso 24** — Django Admin personalizado (back-office) + exportación CSV/Excel
- [ ] **Paso 25** — Suite de seguridad, prueba de carga y validación de criterios de aceptación MVP

---

## Fuera de alcance de este plan (roadmap, sin desglosar)

Ver [visitacampo-construccion.md §9](visitacampo-construccion.md#9-roadmap-de-alto-nivel--fase-2-y-fase-3-fuera-de-alcance-de-este-plan): Fase 2 (multi-tenant real, reportes PDF, push, mapa offline, SPA de back-office, Postgres gestionado, webhooks, miniaturas, PostGIS) y Fase 3 (réplica de lectura, CDN, observabilidad, blue/green, SSO/MFA, auditoría exportable, tracking GPS continuo, mTLS, extracción de servicio de ingesta). No se activan por calendario, solo por disparador observable — no forman parte de este seguimiento.

---

## Resumen

| Fase | Pasos completados | Total |
|---|---|---|
| Fase 0 | 12 (Pasos 0–11) | 15 (Pasos 0–14) |
| Fase 1 | 0 | 11 (Pasos 15–25) |
| **Total** | **12** | **26** |

**Estado de verificación del último paso cerrado (Paso 11)**: `flutter test app/` 10/10 verde (incluye el test de atomicidad transaccional visita+outbox — forzar una excepción a mitad de la transacción no deja huérfanos — y el test de cifrado real: un archivo `.sqlite` escrito con `createEncryptedExecutor` es ilegible con `sqlite3.open()` sin la clave), `flutter analyze app/` limpio, `ci_local.sh` **PASS=9 SKIP=0 FAIL=0**.

**Hallazgo de este paso, documentado en `app/DEPENDENCIES.md`**: `sqlcipher_flutter_libs`/`sqlite3_flutter_libs` (los paquetes que ADR-002 nombra literalmente) están descontinuados desde `sqlite3` v3.x — el cifrado SQLCipher se activa ahora vía `pubspec.yaml: hooks.user_defines.sqlite3.source: sqlcipher` (sistema de hooks/native-assets de Dart). La decisión arquitectónica de ADR-002 (SQLCipher) no cambia, solo el mecanismo de empaquetado — verificado funcionando de extremo a extremo en este host Windows.

**Entorno de desarrollo, actualizado en sesiones recientes**: Flutter 3.44.8 stable + Android SDK (cmdline-tools, licencias aceptadas) instalados y en el PATH de usuario; Docker Desktop instalado con el daemon corriendo; PATH de usuario también incluye el directorio de scripts de pip (`ruff`/`mypy`/`pytest` ejecutables directamente). `riverpod_generator` quedó fuera del stack móvil por un conflicto de versiones con `freezed`/macros — documentado en `app/DEPENDENCIES.md`. Sigue sin haber emulador/dispositivo Android configurado en esta máquina (relevante para Paso 14 en adelante).

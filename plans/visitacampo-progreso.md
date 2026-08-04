# Progreso de ejecución — VisitaCampo

**Seguimiento de**: [visitacampo-construccion.md](visitacampo-construccion.md)
**Última actualización**: 2026-08-03 (checkpoint 14a cerrado — commit `43e4c29`)
**Cómo mantener este archivo**: al cerrar un paso (commit + comandos de verificación en verde), marcar su casilla aquí y anotar el/los commit(s) correspondientes. Si un paso se reabre por un hallazgo de revisión posterior, anotarlo como sub-ítem bajo el paso original en vez de crear una entrada nueva — el plan no numera "pasos de corrección" aparte.

---

## Diagnóstico de estancamiento (2026-08-03)

La ejecución se detuvo entre el cierre del Paso 13 (2026-08-03) y el Paso 14: hay un archivo sin commitear (`server/visitacampo/core/admin.py`, la tarea 1 del Paso 14) pero ningún commit posterior. Causa raíz identificada: el Paso 14 original exige Docker completo + emulador Android + Flutter corriendo a la vez, y esta máquina tiene 7.4 GB de RAM totales con Docker limitado a ~900 MB/1 CPU y el AVD `visitacampo_e2e` reservando 2 GB él solo — combinación que no cabe en la memoria disponible (coincide con los `Out of memory`/hangs ya registrados en los Pasos 11–13 más abajo). Detalle completo, regla de secuenciación nueva, el Paso 14 reescrito en checkpoints (14a–14e) y las metas intermedias A–D: [visitacampo-construccion.md §12](visitacampo-construccion.md#12-ajuste-post-diagnóstico-2026-08-03--restricción-de-entorno-resecuenciación-y-metas-intermedias).

### Checklist de entorno (correr antes de cualquier checkpoint que use Docker o el emulador)

- [ ] ¿Cuánta RAM libre hay ahora mismo? (`Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory` en PowerShell). Si hay menos de ~2 GB libres, cerrar navegador/IDE pesado antes de continuar.
- [ ] ¿Está el checkpoint anterior realmente cerrado (Docker abajo / emulador cerrado según corresponda) antes de levantar el siguiente? Nunca Docker completo + emulador + `flutter test` a la vez.
- [ ] Si `flutter test`/`flutter analyze` falla con errores nativos de memoria: reintentar una vez, y si persiste usar `--concurrency=1` (mitigación ya validada en el Paso 13).

### Metas intermedias (Pasos 14–25)

- [ ] **Meta A — Fase 0 cerrada de verdad**: Paso 14 vía checkpoints 14a ✅ (commitear `admin.py`, `43e4c29`) → **14b (siguiente)** (backend solo) → 14c (cliente solo) → 14d (integración real) → 14e (documentar y cerrar).
- [ ] **Meta B — Demo interna funcional**: Pasos 17, 20 → 18, 21 → 22. No dependen de 15/16/19; se priorizan porque producen el primer resultado mostrable (formulario + foto + GPS + sync visible).
- [ ] **Meta C — Endurecimiento de seguridad**: Pasos 15, 19 → 16.
- [ ] **Meta D — Cierre MVP**: Pasos 23, 24 → 25 (prueba de carga de 50 dispositivos: ejecutar fuera de esta máquina, ver §12).

## Cómo continuar en una nueva sesión

**Modo de ejecución exigido: paso a paso, con confirmación explícita del usuario antes de pasar al siguiente paso.** No se debe encadenar la ejecución de varios pasos del plan en una sola tanda sin que el usuario apruebe cada transición — esto reemplaza el modo "continua sin parar" usado en sesiones anteriores. A partir de este ajuste, cada **checkpoint** (14a, 14b, ... no solo cada paso completo) es la unidad natural de confirmación cuando un paso está desglosado en checkpoints.

Para cada paso (o checkpoint) del plan (`visitacampo-construccion.md`):

1. Releer la sección correspondiente en `visitacampo-construccion.md` (tareas, comandos de verificación, criterios de salida — y §12 si el paso tiene una nota de secuenciación de entorno) — no asumir de memoria.
2. Correr el checklist de entorno de arriba si el checkpoint usa Docker y/o el emulador.
3. Implementar el paso/checkpoint completo (código + tests).
4. Correr los comandos de verificación propios **y** la suite completa relevante (`pytest`/`ruff`/`mypy` para `server/`, `flutter analyze`/`flutter test` para `app/`, `ci_local.sh` cuando toque ambos lados) — en verde de verdad, no asumido.
5. Hacer commit (mensaje descriptivo, ver convención en `README.md`).
6. Actualizar este archivo: marcar la casilla, anotar el/los commit(s), actualizar la tabla de "Resumen" y la fecha de "Última actualización".
7. **Parar y pedir confirmación al usuario antes de empezar el siguiente paso/checkpoint** — reportar qué se hizo, qué quedó verificado, y cuál es el próximo propuesto. No avanzar sin que el usuario confirme.

El **próximo paso a ejecutar es el Paso 14, checkpoint 14b** (backend solo, sin emulador: levantar `db`+`api` y verificar login→push→pull→Admin manualmente) — ver el Paso 14 reescrito en [visitacampo-construccion.md §12](visitacampo-construccion.md#12-ajuste-post-diagnóstico-2026-08-03--restricción-de-entorno-resecuenciación-y-metas-intermedias). Antes de arrancarlo, correr el checklist de entorno de arriba (usa Docker).

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
- [x] **Paso 12** — `SyncEngine`: dominio puro del protocolo de sync (outbox, CAS, cursor)
  `6841bc7` Añade el SyncEngine: dominio puro del protocolo de sync (Paso 12)
- [x] **Paso 13** — Autenticación cliente mínima + base de TLS estricto
  `c1e4b4d` Añade autenticación cliente mínima + base de TLS estricto (Paso 13)

### Integración

- [ ] **Paso 14** — Integración end-to-end: cierre de Fase 0 (checkpoints 14a–14e, ver §12 del plan)
  - [x] 14a — Registrar Visit/VisitAttachment/SyncOpLog en Admin
    `43e4c29` Registra Visit/VisitAttachment/SyncOpLog en Django Admin (Paso 14, checkpoint 14a)
  - [ ] 14b — Backend solo, sin emulador (siguiente)
  - [ ] 14c — Cliente solo, sin backend
  - [ ] 14d — Integración real (ambos a la vez)
  - [ ] 14e — Documentación y cierre

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
| Fase 0 | 14 (Pasos 0–13) | 15 (Pasos 0–14) |
| Fase 1 | 0 | 11 (Pasos 15–25) |
| **Total** | **14** | **26** |

**Estado de verificación del último paso cerrado (Paso 13)**: `flutter analyze app/` limpio; `flutter test app/` **51/51** verde (ejecutado con `--concurrency=1`, ver nota de entorno abajo) — incluye el consumo real de `/v1/auth/login|refresh|logout` y `/v1/server/info` (`AuthApiClient`), el dominio de sesión (`AuthController`: login/logout, refresh single-flight, un único reintento tras 401 sin loop) y el rechazo por defecto de un certificado autofirmado real sin ningún bypass (`tls_default_rejects_invalid_cert_test.dart`, servidor HTTPS real en loopback). `infra/scripts/lint_no_tls_bypass.sh` (RD-01) **OK**: 48 archivos `.dart` escaneados, sin bypass de TLS fuera de `TrustStore`. El archivo de diagnóstico temporal `_diag_socket_smoke_test.dart` (usado para depurar el hang de `bindSecure` con el fixture autofirmado) se eliminó tras confirmar que el test real ya cubre ese caso de forma estable. `ci_local.sh` completo no se corrió hasta el final esta vez: con concurrencia por defecto, el paso `flutter test app/` de ese script quedó colgado más de 15 minutos sin producir salida (mismo tipo de fallo de hilos que motivó el `--concurrency=1` — ver nota de entorno); se detuvo la ejecución en background y se dio por válida la verificación directa de los comandos propios del Paso 13, ya que este paso no toca `server/`.

**Estado de verificación del Paso 12 (referencia)**: `flutter test app/` **28/28** verde — incluye los 4 casos obligatorios de doc 08 §8 (idempotencia, backoff exponencial con reloj falso, conflicto sin merge de 3 vías, `409 cursor_expired` → bootstrap automático), la máquina de estados del outbox probada en aislamiento (Dart puro, sin BD), y la primera migración real del esquema local (v1→v2, con datos de ejemplo). `flutter analyze app/` limpio, `ci_local.sh` **PASS=9 SKIP=0 FAIL=0**.

**Hallazgos de este paso**: (1) `json_serializable` esperaba claves JSON camelCase en vez del snake_case real del contrato — el mismo bug ya existía sin detectar en el `Visit` del Paso 10 (su test de roundtrip solo se comparaba consigo mismo). Corregido globalmente con `field_rename: snake` en `app/build.yaml`, más un test de regresión que parsea JSON snake_case real. (2) `OutboxInflight` no arrastraba el contador de intentos de backoff al transicionar desde `pending`.

**Hallazgo del Paso 11** (referencia): `sqlcipher_flutter_libs`/`sqlite3_flutter_libs` (los paquetes que ADR-002 nombra literalmente) están descontinuados desde `sqlite3` v3.x — el cifrado SQLCipher se activa ahora vía `pubspec.yaml: hooks.user_defines.sqlite3.source: sqlcipher`. Verificado funcionando de extremo a extremo en este host Windows.

**Entorno de desarrollo, actualizado en sesiones recientes**: Flutter 3.44.8 stable + Android SDK (cmdline-tools, licencias aceptadas) instalados y en el PATH de usuario; Docker Desktop instalado con el daemon corriendo; PATH de usuario también incluye el directorio de scripts de pip (`ruff`/`mypy`/`pytest` ejecutables directamente). `riverpod_generator` quedó fuera del stack móvil por un conflicto de versiones con `freezed`/macros — documentado en `app/DEPENDENCIES.md`. Sigue sin haber emulador/dispositivo Android configurado en esta máquina (relevante para Paso 14 en adelante).

**Nota de entorno (Paso 13)**: esta máquina tiene poca RAM física (~7.4 GB, con frecuencia menos de 1 GB libre bajo carga normal de escritorio). `flutter analyze`/`flutter test` con concurrencia por defecto fallan intermitentemente con errores nativos del VM de Dart (`Out of memory` / `Could not start thread DartWorker`, a veces también al invocar `flutter`/`git` desde Git Bash con "el archivo de paginación es demasiado pequeño"). Mitigación verificada: reintentar una vez (a menudo transitorio) y, si persiste, usar `flutter test --concurrency=1`. Tenerlo en cuenta en los pasos siguientes con más tests (14+): preferir `--concurrency=1` o cerrar aplicaciones pesadas (IDE, navegador, WSL) antes de correr la suite completa.

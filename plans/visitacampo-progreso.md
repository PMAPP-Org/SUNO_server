# Progreso de ejecución — VisitaCampo

**Seguimiento de**: [visitacampo-construccion.md](visitacampo-construccion.md)
**Última actualización**: 2026-10-17 (línea base de seguridad ISO 27001 / OWASP, Paso 19 cerrado, plan de demo completado; ver «Estado al 2026-10-17» abajo. Anterior: 2026-08-05, Paso 17 cerrado)


## Estado al 2026-10-17

**Qué cambió desde agosto.** Antes de seguir con los pasos pendientes se paró a dejar el proyecto con buenas prácticas y alineado con ISO/IEC 27001:2022 y OWASP Top 10:2025. Todo está en una **cadena de PR apilados que se integran en orden**, cada uno con el CI en verde salvo *Dependency Review*, que necesita que un admin active el Dependency graph (issue #5):

| PR | Contenido |
|---|---|
| #1 | Línea base: CI real (antes Mobile CI fallaba y Backend CI nunca había corrido), CVE corregidas, lockfiles con hashes, gates de seguridad, fuga de `Bearer` en logs corregida |
| #2 | Traza de auditoría de autenticación; release Android publicable (faltaba `INTERNET`, firmaba con debug) |
| #4 | CodeQL, DAST (Schemathesis + ZAP) contra el stack de producción, grype, SBOM, releases automáticos con procedencia firmada |
| #6 | Endurecimiento de prod (`SECRET_KEY`, RD-15, `/admin/` por origen), backups cifrados con simulacro, retención, worker activado, monitoreo (ADR-014) |
| #7 | Semgrep OWASP, plantilla de PR, CODEOWNERS, CHANGELOG, actionlint |
| #8 | Sistema de diseño e internacionalización de la app |
| #9 | Documentación del SGSI (borradores) |
| este | Runbooks de operación, `SECURITY.md`, este seguimiento |

**Pendiente de terceros:** rol Admin y revisión independiente de los PR (issue #5); aprobación del SGSI por la dirección; validación jurídica de Ley 1581; VPS, dominio, buckets R2 y SMTP.

**Orden de trabajo acordado a continuación:** (1) runbooks y este seguimiento → (2) MFA en el Admin + Paso 15 → (3) aprovisionamiento del VPS + workflow de despliegue → (4) Pasos 21 → 22 → 18 → 23 → 24 → 25. El Paso 14 (checkpoints 14c y 14d en emulador) y el Paso 16 siguen pendientes.

**Entorno de desarrollo (máquina actual):** no tiene Python 3.12, Flutter ni Docker en marcha. Se verifica con herramientas montadas en un directorio temporal (Python 3.12 con uv, PostgreSQL 16 embebido, Flutter 3.44.9). Los stacks con Docker (DAST, simulacro de backup, monitoreo) solo se verifican en GitHub Actions.

---

## Fase actual: construcción de código continua (emulador diferido)

**Decisión de esta sesión**: dejar de intercalar verificación en emulador por checkpoint (14c se cerró parcialmente — el emulador llegó a booteear pero no había harness de debug para insertar una visita, y cada sesión de emulador cuesta reconquistar RAM manualmente). A partir de aquí: construir código de los pasos restantes con toda su verificación no-emulador (`pytest`/`ruff`/`mypy`, `dart analyze`/`flutter test` headless) y diferir todo lo que exija el emulador/dispositivo real a una fase de testeo dedicada, en bloque, más adelante. Además (continuación 2), se retira la confirmación obligatoria por paso: se ejecuta en continuo, commiteando cada paso verificado, pausando solo en los casos listados en el plan §12 (fallo de verificación no resuelto, ambigüedad de diseño real, o paso siguiente que exige emulador).

- **Paso 14**: 14a ✅ y 14b ✅ cerrados (backend real probado de punta a punta). **14c queda a medio camino** — el emulador booteó pero no se insertó ninguna visita (no existe todavía un harness de debug en `app/lib/main.dart`, que sigue siendo el demo `flutter create` sin modificar). 14c/14d se retoman en la fase de testeo. No bloquean el resto del plan (ver justificación en §12).
- **Grupo C completo desbloqueado**: Pasos 15 (auth hardening), 17 (adjuntos backend), 19 (cola de jobs) y 20 (formularios backend) ya no esperan a 14c/14d — su dependencia real es Paso 7/5/6, todos cerrados (plan §12, "continuación 2"). Los cuatro son construibles ahora, sin emulador.
- **Orden de construcción de esta sesión**: **Paso 20** primero (ya en curso al momento de este ajuste, cero fricción con los otros tres), seguido de 17, 19 y 15 — **20 y 17 ya cerrados; quedan 19 y 15** — cada uno commiteado por separado en cuanto su propia verificación esté en verde, sin pausar a confirmar entre pasos (ver "Fase actual" arriba).
**Cómo mantener este archivo**: al cerrar un paso (commit + comandos de verificación en verde), marcar su casilla aquí y anotar el/los commit(s) correspondientes. Si un paso se reabre por un hallazgo de revisión posterior, anotarlo como sub-ítem bajo el paso original en vez de crear una entrada nueva — el plan no numera "pasos de corrección" aparte.

---

## Diagnóstico de estancamiento (2026-08-03)

La ejecución se detuvo entre el cierre del Paso 13 (2026-08-03) y el Paso 14: hay un archivo sin commitear (`server/visitacampo/core/admin.py`, la tarea 1 del Paso 14) pero ningún commit posterior. Causa raíz identificada: el Paso 14 original exige Docker completo + emulador Android + Flutter corriendo a la vez, y esta máquina tiene 7.4 GB de RAM totales con Docker limitado a ~900 MB/1 CPU y el AVD `visitacampo_e2e` reservando 2 GB él solo — combinación que no cabe en la memoria disponible (coincide con los `Out of memory`/hangs ya registrados en los Pasos 11–13 más abajo). Detalle completo, regla de secuenciación nueva, el Paso 14 reescrito en checkpoints (14a–14e) y las metas intermedias A–D: [visitacampo-construccion.md §12](visitacampo-construccion.md#12-ajuste-post-diagnóstico-2026-08-03--restricción-de-entorno-resecuenciación-y-metas-intermedias).

### Checklist de entorno (correr antes de cualquier checkpoint que use Docker o el emulador)

- [ ] ¿Cuánta RAM libre hay ahora mismo? (`Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory` en PowerShell). Si hay menos de ~2 GB libres, cerrar navegador/IDE pesado antes de continuar.
- [ ] ¿Está el checkpoint anterior realmente cerrado (Docker abajo / emulador cerrado según corresponda) antes de levantar el siguiente? Nunca Docker completo + emulador + `flutter test` a la vez.
- [ ] Si `flutter test`/`flutter analyze` falla con errores nativos de memoria: reintentar una vez, y si persiste usar `--concurrency=1` (mitigación ya validada en el Paso 13).

### Metas intermedias (Pasos 14–25)

- [ ] **Meta A — Fase 0 cerrada de verdad**: Paso 14 vía checkpoints 14a ✅ (`43e4c29`) → 14b ✅ (`2a5ee36`) → **14c (siguiente)** (cliente solo) → 14d (integración real) → 14e (documentar y cerrar).
- [ ] **Meta B — Demo interna funcional**: Pasos 17, 20 → 18, 21 → 22. No dependen de 15/16/19; se priorizan porque producen el primer resultado mostrable (formulario + foto + GPS + sync visible).
- [ ] **Meta C — Endurecimiento de seguridad**: Pasos 15, 19 → 16.
- [ ] **Meta D — Cierre MVP**: Pasos 23, 24 → 25 (prueba de carga de 50 dispositivos: ejecutar fuera de esta máquina, ver §12).

## Cómo continuar en una nueva sesión

**Modo de ejecución vigente (2026-08-03, continuación 2): construcción continua, sin confirmación obligatoria por paso.** El modo "paso a paso, con confirmación explícita" de la revisión anterior aplicaba al riesgo de las sesiones de emulador/Docker (caras de RAM, valía la pena revisar antes de seguir); ese riesgo no existe en pasos de construcción pura de backend/Dart headless. Se mantiene la unidad de **checkpoint** (14a, 14b, ...) donde un paso esté desglosado, pero ya no se pausa a pedir aprobación entre pasos/checkpoints salvo los casos listados abajo.

Para cada paso (o checkpoint) del plan (`visitacampo-construccion.md`):

1. Releer la sección correspondiente en `visitacampo-construccion.md` (tareas, comandos de verificación, criterios de salida — y §12 si el paso tiene una nota de secuenciación de entorno) — no asumir de memoria. No hace falta releer el plan completo, solo el bloque del paso.
2. Correr el checklist de entorno de arriba si el checkpoint usa Docker y/o el emulador (en fase de construcción, normalmente no aplica).
3. Implementar el paso/checkpoint completo (código + tests) — delegar a un subagente con el bloque del paso como contexto autocontenido cuando el paso sea sustancial, para mantener esta sesión liviana en tokens (ver "harness engineering" en plan §12).
4. Correr los comandos de verificación propios **y** la suite relevante con alcance acotado al módulo tocado primero; la suite completa (`pytest`/`ruff`/`mypy`, `flutter analyze`/`flutter test`, `ci_local.sh`) se reserva para el cierre de una Meta — en verde de verdad, no asumido.
5. Hacer commit (mensaje descriptivo, ver convención en `README.md`).
6. Actualizar este archivo: marcar la casilla, anotar el/los commit(s), actualizar la tabla de "Resumen" y la fecha de "Última actualización".
7. **Continuar directamente con el siguiente paso/checkpoint** — se pausa a pedir confirmación solo si: el paso siguiente exige emulador/dispositivo real (se salta a la fase de testeo, no se pregunta), una verificación falla sin resolverse tras diagnóstico razonable, aparece una ambigüedad de diseño real no cubierta por doc 08, o se cierra una Meta completa (checkpoint natural de resumen).

El **orden de construcción vigente es Paso 20 → 17 → 19 → 15** (Grupo C completo, ver "Fase actual" arriba), seguido del Grupo D móvil (18/21/16) cuando corresponda. El Paso 14 (checkpoints 14c/14d) queda diferido a la fase de testeo en emulador.

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
  - [x] 14b — Backend solo, sin emulador
    `2a5ee36` Reduce memory_cost de Argon2 en dev.py para máquinas con RAM escasa (Paso 14, checkpoint 14b)
    Verificado con `manage.py runserver` (127.0.0.1:8000) contra el `db` de `docker compose` ya corriendo — **no** se activó el placeholder Docker del servicio `api` (sigue como placeholder de `infra/docker-compose.yml`, `sleep infinity`; construir su imagen es scope de un paso futuro si hace falta correrlo en contenedor, no bloqueante aquí). Flujo real por `curl`: `POST /v1/auth/login` → JWT real; `POST /v1/sync/push` de una visita → `status: applied`, `server_version: 1`; `GET /v1/sync/pull?cursor=c1:00…0` → devuelve la visita con `change_seq` correcto; confirmado en BD que `admin.site.is_registered(Visit)` es `True` y los datos persistidos coinciden. Bloqueo encontrado y resuelto en el camino: Argon2 fallaba con `HashingError: Memory allocation error` por falta de RAM real (~350-450 MB libres) — mitigado con `DevArgon2PasswordHasher` (memory_cost 19 MiB, solo dev.py, ver commit).
  - [~] 14c — Cliente solo, sin backend — **diferido a la fase de testeo**: el emulador `visitacampo_e2e` booteó correctamente (con Docker Desktop detenido para liberar RAM: ~290 MB → ~900 MB libres), pero no se insertó ninguna visita porque `app/lib/main.dart` sigue siendo el demo `flutter create` sin modificar (no existe el harness de debug que la tarea supone). Retomar en la fase de testeo, momento en el que ya debería existir código de dominio adicional (Meta B) que dar de alta ahí también.
  - [ ] 14d — Integración real (ambos a la vez) — diferido a la fase de testeo
  - [ ] 14e — Documentación y cierre — diferido a la fase de testeo

---

## Fase 1 — MVP

### Backend

- [x] **Paso 15** — Endurecimiento de autenticación: rotación, detección de reuso, rate limiting
  Rotación con `SELECT FOR UPDATE`, ventana de gracia de 30 s y revocación de toda la familia ante reuso (con auditoría); rate limiting sobre PostgreSQL en login, refresh y presign (`429` + `Retry-After`); trabajo de hash uniforme en el login; logs de auth sin secretos. Además, MFA obligatorio en el Admin (django-otp). Verificación: `test_rotation.py` (incluye concurrencia con hilos), `test_rate_limit.py`, `test_admin_mfa.py`; 701 tests en verde.
- [x] **Paso 17** — Adjuntos backend: `BlobStore`, presign/commit, verificación
  `076567e` Añade el carril de adjuntos: BlobStore, presign/commit verificado y GC (Paso 17)
- [x] **Paso 19** — Cola de trabajos: `SKIP LOCKED` + lease + heartbeat + reaper
  `3b06073` WIP: cola de trabajos (Paso 19) — commit sin revisión previa
  Revisado y cerrado en la línea base de seguridad: ruff/mypy y 5 tests que fallaban en el WIP corregidos (PR #1); el worker pasa de placeholder a servicio real en Compose, con re-aseguramiento periódico de los periódicos (bug: una organización creada tras arrancar el worker nunca los recibía) y el periódico nuevo `security.purge_logs` (PR #6). Criterios de salida verificados en CI: `test_kill9` (H-12), `test_toctou` (H-13, con el test endurecido contra intercalados), `test_scrub` (H-17) y el DAST, que comprueba que el worker procesa la cola en el stack de producción. Backups: decididos como tarea de despliegue (`infra/backup/`, con simulacro de restauración en CI), como prevé la tarea 9.
- [x] **Paso 20** — Formularios declarativos: `form_template` + JSON Schema versionado
  `1268240` Añade formularios declarativos: DSL restringido + meta-schema versionado (Paso 20)

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
| Fase 1 | 4 (Pasos 15, 17, 19, 20) | 11 (Pasos 15–25) |
| **Total** | **18** | **26** |

**Estado de verificación del Paso 17 (backend, último paso cerrado)**: `pytest server/` **553 passed** (+744 subtests) contra PostgreSQL real (452 antes de este paso: +101 tests nuevos), `ruff check server/` y `mypy server/ --config-file server/pyproject.toml` limpios, `manage.py makemigrations --check --dry-run` sin cambios y `manage.py check` sin incidencias — el paso **no añade ninguna tabla**: `Blob` y `VisitAttachment` ya existían desde el Paso 5. Los comandos de verificación del plan corren de verdad, en `attachments/tests/`: (a) presign de un `sha256` ya existente → `already_present: true`, sin URL y **sin tocar el object storage**; (b) commit con checksum que no coincide con el `HEAD` real → `422 attachment_checksum_mismatch` y el `Blob` **no** se registra (ídem para el tamaño); (c) commit válido → `Blob` creado, y doble commit idempotente devolviendo la misma fila byte a byte. Más: **test de traversal explícito** con 15 familias de entrada hostil (`../`, rutas absolutas POSIX y Windows, UNC, URL-encoding, byte nulo, mayúsculas, longitud incorrecta) contra `bucket_key_for()` y contra la resolución de ruta del adaptador local, comprobando además que no se crea nada en el filesystem; y **test de concurrencia** (`TransactionTestCase` + `ThreadPoolExecutor`) con dos y con ocho commits simultáneos del mismo `sha256`: todos resuelven sin error de constraint, queda **una** fila y **exactamente uno** reporta haberla creado.

**Criterios de salida del Paso 17, uno a uno**: **H-08 mitigado por construcción** — el registro es `bulk_create(ignore_conflicts=True)` (= `INSERT ... ON CONFLICT DO NOTHING`) sobre `sha256`, sin `select_for_update`, y un test comprueba sobre el modelo real que `blob` no tiene **ningún** campo mutable que perder (los cinco campos son los de doc 08 §4.4 y cuatro son `editable=False`); si alguien añadiera un `latest_version`/`estado`/`updated_at`, ese test lo detendría. **H-06/H-21 mitigados** — la única función que compone una ruta es `bucket_key_for()` y solo acepta 64 caracteres `[0-9a-f]`; el nombre de descarga se genera localmente (`{uuid}.{ext}` desde el MIME ya verificado) y el API **no tiene por dónde recibir un nombre de archivo**, porque el contrato no declara ese campo. **H-05 no reproducida** — los bytes nunca pasan por el API (`PUT` directo a URL prefirmada); el servidor solo hace `HEAD` y lee 16 bytes de prefijo para el sniffing, con el stream cerrado en `finally` (verificado por un doble que cuenta cierres).

**Decisiones de diseño registradas (Paso 17)**: (a) **paquete Python, no app de Django** (`server/visitacampo/attachments/`), mismo criterio que `forms/` del Paso 20 — no hay tablas nuevas que migrar; (b) se añade **`boto3`** a `requirements.txt` en vez de implementar SigV4 a mano: el resultado de una firma es un hash, o coincide byte a byte o el store devuelve `403` sin decir por qué, y una implementación propia sin un R2 real contra el que contrastarla daría un backend que **pasa en local y falla en producción** — además `generate_presigned_url` firma **sin red**, así que `S3BlobStore` se prueba offline de verdad (URL real, `X-Amz-SignedHeaders`, caducidad, firmas distintas para contenidos distintos) y solo `head`/`read_prefix`/`list_objects`/`delete` usan un doble del cliente; (c) **la verificación del checksum es real y no un gesto**: el presign firma `x-amz-checksum-sha256`, de modo que S3/R2 rechazan el `PUT` cuyo cuerpo no case y devuelven ese checksum en el `HEAD` (`ChecksumMode=ENABLED`) — el servidor compara contra un valor que calculó el object storage, no contra algo que le contó el cliente, y un `HEAD` **sin** checksum certificado se rechaza igual ("no he podido comprobarlo" no es "está bien"); (d) el puerto tiene **seis** métodos y no los tres del plan (`read_prefix` para el sniffing de la tarea 5, `list_objects`/`delete` para el GC de la tarea 7) y **todos hablan de `sha256`, nunca de claves**, que es lo que cierra H-06/H-21 por construcción; (e) se distingue `attachment_checksum_mismatch` (checksum/tamaño) de `validation_error` (MIME fuera de allow-list o magic bytes que no casan), como el propio contrato enumera en el `422` del commit — para el cliente son acciones distintas; (f) se añade `blob_registry()` a `core/scoping.py` como **tercera excepción documentada** de ADR-011 (junto a `get_organization`/`get_app_user`), la que el docstring de `NotOrgScopedError` ya anticipaba desde el Paso 6: `Blob` no tiene `org_id` a propósito (RD-08) y la autorización de descarga se resuelve por `visit_attachment`, que sí lo lleva — se deja **sin parámetro `ctx`** deliberadamente, para que la firma no sugiera un aislamiento que no aplica; (g) cuota **acumulada por visita** (200 MiB) además del tope por archivo (25 MiB), anunciada en `ServerCapabilities` (cuyo esquema declara `additionalProperties: true`) para que el cliente no gaste datos móviles en una foto que va a ser rechazada; los adjuntos con tombstone no ocupan cuota; (h) el GC separa la **decisión pura** (`unconfirmed_sha256`, probable sin store ni BD ni esperar 24 h) del cableado, y **nunca borra un objeto cuya clave no revierta a un `sha256` válido** — un backup que comparta bucket no es basura de este job.

**Diferido explícitamente del Paso 17**: el *rate limiting* que el contrato anota sobre `/v1/attachments/presign` ("Endpoint rate-limited, ADR-008") **no se implementa aquí**. El proyecto no tiene todavía ninguna infraestructura de throttling — ni siquiera para `login`, que es donde ADR-008 la exige primero y que es alcance del **Paso 15**—, y montarla solo en este endpoint dejaría la del login sin hacer y dispersaría la política en dos sitios. Queda anotado en el docstring de `attachments/api.py` y es trabajo del Paso 15.

**Estado de verificación del Paso 20 (backend, paso anterior)**: `pytest server/` **452 passed** (+634 subtests) contra PostgreSQL real, `ruff check server/` y `mypy server/ --config-file server/pyproject.toml` limpios, `manage.py makemigrations --check` sin cambios (el paso no altera ninguna columna: la validación vive en `FormTemplateVersion.clean()` y en el paquete nuevo `visitacampo/forms/`). Los tres comandos de verificación del plan corren de verdad: plantilla mal formada rechazada al publicar (`forms/tests/test_templates.py`), `answers` fuera del schema rechazada en el push con `code: validation_error` y `details {"field": "answers.<clave>", "reason": ...}` (`sync/tests/test_push_answers.py`), retrocompatibilidad histórica verificada con dos versiones incompatibles de la misma plantilla, y test de seguridad de H-06/RD-06 con 10 expresiones hostiles (`{"op": "eval"...}`, llamadas a función, acceso a atributos, regex) todas rechazadas al validar la plantilla y **nunca evaluadas**, más una comprobación por AST de que el paquete no contiene `eval`/`exec`/`compile`/`__import__` ni importa `os`/`subprocess`/`pickle`.

**Entorno**: se levantó **solo** el servicio `db` (`docker compose -f infra/docker-compose.yml -f infra/docker-compose.override.dev.yml up -d db`, patrón del Paso 14b), nunca el stack completo; RAM libre al arrancar ~1.3 GB. El contenedor `infra-db-1` se deja **corriendo** para los Pasos 19/15 (el Paso 17 lo reutilizó sin reiniciarlo). El Paso 17 **no levantó MinIO** pese a estar ya declarado en el override de desarrollo: `LocalFsBlobStore` cubre la suite de integración y `S3BlobStore` se firma sin red, así que un contenedor más habría costado RAM sin añadir cobertura. Las variables `POSTGRES_*` de `infra/.env` se exportan al entorno del shell con `POSTGRES_HOST=localhost` para que `pytest` alcance el contenedor (sin ellas, `dbsupport.SKIP_REASON` salta los tests de integración en silencio).

**Decisiones de diseño registradas (Paso 20)**: (a) el DSL de visibilidad es **JSON con discriminador `op`**, no un lenguaje de expresiones en texto — sin gramática no hay parser que desbordar; (b) referencias **solo hacia atrás**, lo que hace el grafo de dependencias acíclico por construcción; (c) se descarta `schema_unsupported` como código de rechazo de `answers` (en este contrato significa "no hablo tu versión del protocolo" y pararía la sincronización entera por un campo fuera de rango) en favor de `validation_error`; (d) **sin API pública de CRUD de plantillas en el MVP** — el contrato de Paso 1 no la declara y el back-office es el Django Admin del Paso 24 sobre `forms.services.publish_version()`; (e) sin expresiones regulares en el meta-schema (ReDoS); (f) una definición vacía (`{}`, las filas anteriores a este paso) significa "sin esquema declarado" y no restringe `answers`, para no invalidar dato histórico. Todas están escritas en el código, no solo aquí.

**Estado de verificación del último paso cerrado del carril móvil (Paso 13)**: `flutter analyze app/` limpio; `flutter test app/` **51/51** verde (ejecutado con `--concurrency=1`, ver nota de entorno abajo) — incluye el consumo real de `/v1/auth/login|refresh|logout` y `/v1/server/info` (`AuthApiClient`), el dominio de sesión (`AuthController`: login/logout, refresh single-flight, un único reintento tras 401 sin loop) y el rechazo por defecto de un certificado autofirmado real sin ningún bypass (`tls_default_rejects_invalid_cert_test.dart`, servidor HTTPS real en loopback). `infra/scripts/lint_no_tls_bypass.sh` (RD-01) **OK**: 48 archivos `.dart` escaneados, sin bypass de TLS fuera de `TrustStore`. El archivo de diagnóstico temporal `_diag_socket_smoke_test.dart` (usado para depurar el hang de `bindSecure` con el fixture autofirmado) se eliminó tras confirmar que el test real ya cubre ese caso de forma estable. `ci_local.sh` completo no se corrió hasta el final esta vez: con concurrencia por defecto, el paso `flutter test app/` de ese script quedó colgado más de 15 minutos sin producir salida (mismo tipo de fallo de hilos que motivó el `--concurrency=1` — ver nota de entorno); se detuvo la ejecución en background y se dio por válida la verificación directa de los comandos propios del Paso 13, ya que este paso no toca `server/`.

**Estado de verificación del Paso 12 (referencia)**: `flutter test app/` **28/28** verde — incluye los 4 casos obligatorios de doc 08 §8 (idempotencia, backoff exponencial con reloj falso, conflicto sin merge de 3 vías, `409 cursor_expired` → bootstrap automático), la máquina de estados del outbox probada en aislamiento (Dart puro, sin BD), y la primera migración real del esquema local (v1→v2, con datos de ejemplo). `flutter analyze app/` limpio, `ci_local.sh` **PASS=9 SKIP=0 FAIL=0**.

**Hallazgos de este paso**: (1) `json_serializable` esperaba claves JSON camelCase en vez del snake_case real del contrato — el mismo bug ya existía sin detectar en el `Visit` del Paso 10 (su test de roundtrip solo se comparaba consigo mismo). Corregido globalmente con `field_rename: snake` en `app/build.yaml`, más un test de regresión que parsea JSON snake_case real. (2) `OutboxInflight` no arrastraba el contador de intentos de backoff al transicionar desde `pending`.

**Hallazgo del Paso 11** (referencia): `sqlcipher_flutter_libs`/`sqlite3_flutter_libs` (los paquetes que ADR-002 nombra literalmente) están descontinuados desde `sqlite3` v3.x — el cifrado SQLCipher se activa ahora vía `pubspec.yaml: hooks.user_defines.sqlite3.source: sqlcipher`. Verificado funcionando de extremo a extremo en este host Windows.

**Entorno de desarrollo, actualizado en sesiones recientes**: Flutter 3.44.8 stable + Android SDK (cmdline-tools, licencias aceptadas) instalados y en el PATH de usuario; Docker Desktop instalado con el daemon corriendo; PATH de usuario también incluye el directorio de scripts de pip (`ruff`/`mypy`/`pytest` ejecutables directamente). `riverpod_generator` quedó fuera del stack móvil por un conflicto de versiones con `freezed`/macros — documentado en `app/DEPENDENCIES.md`. Sigue sin haber emulador/dispositivo Android configurado en esta máquina (relevante para Paso 14 en adelante).

**Nota de entorno (Paso 13)**: esta máquina tiene poca RAM física (~7.4 GB, con frecuencia menos de 1 GB libre bajo carga normal de escritorio). `flutter analyze`/`flutter test` con concurrencia por defecto fallan intermitentemente con errores nativos del VM de Dart (`Out of memory` / `Could not start thread DartWorker`, a veces también al invocar `flutter`/`git` desde Git Bash con "el archivo de paginación es demasiado pequeño"). Mitigación verificada: reintentar una vez (a menudo transitorio) y, si persiste, usar `flutter test --concurrency=1`. Tenerlo en cuenta en los pasos siguientes con más tests (14+): preferir `--concurrency=1` o cerrar aplicaciones pesadas (IDE, navegador, WSL) antes de correr la suite completa.

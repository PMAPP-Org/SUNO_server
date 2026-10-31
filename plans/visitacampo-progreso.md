# Progreso de ejecución — VisitaCampo

**Seguimiento de**: [visitacampo-construccion.md](visitacampo-construccion.md)
**Última actualización**: 2026-10-31 (Fases A, B y primera parte de C integradas; C4–C6 en PR abierto; ver «Estado al 2026-10-29» abajo. Anterior: 2026-10-17, línea base de seguridad y plan de demo completado)

## Módulo de costos — Etapa 1 (Cronograma), servidor — 2026-10-31

**PR**: (PR pendiente). Rama `feat/costos-etapa1-servidor`. Plan: [2026-10-31-costos-etapa1-servidor-plan.md](2026-10-31-costos-etapa1-servidor-plan.md).

**Qué entra (Tasks 1–14, más portada)**: modelos y migración (proyecto, importación, tarea, responsables); calendario laboral; lector MSPDI; cálculos de atraso y avance por fuente (SUNO / Project); permisos y visibilidad por rol; importación en dos pasos (vista previa y aplicar/descartar en una transacción bloqueada); armado del cronograma; páginas en `/plataforma/proyectos/` (lista, cronograma con filtros, ficha de tarea con responsables, importar, vista previa, historial y descarga del XML); Gantt SVG propio con línea base, ruta crítica, dependencias y fecha de corte; exportación a Excel auditada; la portada de la plataforma lista los proyectos visibles.

**Verificación (números reales)**: suite completa 995 tests: 991 pasan y 4 fallan en `sync/tests/test_load_script.py` por entorno (el contenedor de pruebas no monta `/infra/load/push_load.py`; en CI el repo completo sí está). Cobertura 93,12 % (suelo 90 %). `ruff check` y `mypy` limpios (219 archivos), `makemigrations --check` sin cambios. Pruebas nuevas en las últimas tasks: Gantt 2, Excel 2, portada 1. La prueba manual en navegador (Task 15 paso 3) queda pendiente.

**Desviaciones de la spec**: (1) la importación se calcula en la misma petición, sin la cola `jobs`; (2) Gantt en SVG propio en vez de Frappe Gantt desde CDN.

**Mejoras pendientes**: la lista de proyectos recalcula el cronograma de cada proyecto en cada petición (O(proyectos × tareas)); cachear o guardar el rollup cuando llegue el avance de campo.

**Pendiente**: avance desde campo (plan `2026-10-31-costos-etapa1-campo-plan.md`, por escribir).


## Estado al 2026-10-29

**Qué cambió desde el 18 de octubre.** Después de cerrar los pasos que no exigían hardware, el trabajo pasó a una serie de funcionalidades de campo agrupadas en **Fases A, B y C**. Esa hoja de ruta no está escrita en ningún documento de los repositorios; se reconstruye aquí a partir de los PR. Versiones publicadas: SUNO_server **0.8.0**, SUNO_client **0.9.0**.

| Fase | Qué cubre | PR |
|---|---|---|
| Cierre de pasos | Paso 16 (TrustStore), mapa H-01…H-21 y prueba de carga (Paso 25), rotación de la clave JWT, conformidad con el contrato, Ley 1581 (aviso y registro de la autorización), identidad visual SUNO | server #16–#26; client #8, #10–#13 |
| **A** — uso diario sin conexión | sesión offline tras reiniciar, sincronización automática con la app abierta, GPS preciso con promedio, detalle/búsqueda/paginación, edición y borrado offline, ajustes y traspaso seguro del dispositivo | client #14, #15, #17–#20 |
| **B** — mapa y asignaciones | asignaciones desde el servidor, mapa de visitas/asignaciones/posición, mapas base offline por organización (descarga automática y reanudable) | server #27; client #21, #22, #24, #26, #27 |
| **C** — geodatos | selección de CRS, campos línea y polígono, capas GeoJSON de referencia por organización | server #33, #35; client #30, #31, #34 |
| Transversal | solo el autor o un supervisor edita/borra una visita; rediseño web público y del espacio de trabajo | server #31, #36; client #29, #33 |

**Abierto, con CI en verde y sin fusionar (revisado el 2026-10-29):**

| PR | Contenido | Hallazgos de la revisión |
|---|---|---|
| [SUNO_client#36](https://github.com/PMAPP-Org/SUNO_client/pull/36) | **C4** navegación a la asignación | GPS apagado durante la sesión se muestra como «sin señal»; el mapa vuelve a pedir permiso de ubicación al volver de la navegación; la pantalla puede quedarse en «Comprobando el GPS…» sin reintento |
| [SUNO_server#37](https://github.com/PMAPP-Org/SUNO_server/pull/37) | **C5** repetibles, valores iniciales, QR, importación XLSForm; **C6** GeoJSON, GeoPackage y PDF | repetibles anidados siempre superan el tope de 10 000 campos (no se llega a los 3 niveles anunciados); `relevant` compuesto se importa como literal basura; fechas de Excel y `TypeError` en defaults rompen la importación (500); CSV/XLSX siguen exportando visitas borradas |
| [SUNO_client#37](https://github.com/PMAPP-Org/SUNO_client/pull/37) | **C5/C6** en la app | un repetible oculto con filas bloquea el guardado; errores del servidor dentro de filas no se pueden limpiar; PDF sin GPS muestra «null, null»; GeoJSON copia todas las respuestas en cada geometría |

Los dos #37 van juntos (el cliente fija el contrato a `SUNO_server@8546e55`). El plan de C5/C6 vive en `plans/fase-c5-c6.md` de la rama `feat/forms-and-exports` y entra con el PR.

**Sigue pendiente, sin cambios respecto a la tabla de abajo:** Pasos 18 (subida con la app cerrada), 23 (conflicto contra backend real), 25 (primera ejecución de la carga y mediciones en dispositivo) y la prueba en dispositivo del Paso 16; las pruebas físicas de C4–C6 (cámara QR, compartir, GPS); issues #7 (Admin) y #21 (sustituto de MinIO); validación jurídica del aviso y aprobación del SGSI.


## Estado al 2026-10-17

**Qué cambió desde agosto.** Antes de seguir con los pasos pendientes se paró a dejar el proyecto con buenas prácticas y alineado con ISO/IEC 27001:2022 y OWASP Top 10:2025. Todo está en una **cadena de PR apilados que se integran en orden**, cada uno con el CI en verde salvo *Dependency Review*, que necesita que un admin active el Dependency graph (issue #7):

| PR | Contenido |
|---|---|
| #1 | Línea base: CI real (antes Mobile CI fallaba y Backend CI nunca había corrido), CVE corregidas, lockfiles con hashes, gates de seguridad, fuga de `Bearer` en logs corregida |
| #2 | Traza de auditoría de autenticación; release Android publicable (faltaba `INTERNET`, firmaba con debug) |
| #4 | CodeQL, DAST (Schemathesis + ZAP) contra el stack de producción, grype, SBOM, releases automáticos con procedencia firmada |
| #6 | Endurecimiento de prod (`SECRET_KEY`, RD-15, `/admin/` por origen), backups cifrados con simulacro, retención, worker activado, monitoreo (ADR-014) |
| #7 | Semgrep OWASP, plantilla de PR, CODEOWNERS, CHANGELOG, actionlint |
| #8 | Sistema de diseño e internacionalización de la app |
| #9 | Documentación del SGSI (borradores) |
| #10 | Runbooks de operación, `SECURITY.md`, este seguimiento |
| #11 | MFA obligatorio en el Admin; Paso 15 (rotación con detección de reutilización) y límites de tasa en la API |
| #12 | Aprovisionamiento endurecido del VPS y despliegue automático con verificación de procedencia y rollback, ambos probados en CI |
| #13 | Paso 21: renderer dinámico de formularios en Flutter, con paridad servidor ↔ cliente comprobada por un fixture común |
| #21 | Paso 22: captura de visita completa offline (formulario, GPS, fotos, firma) y alta de `visit_attachment` en el push |

**Separación en dos repositorios (2026-10-17, tras v0.2.0):** el monorepo `PMAPP-Org/Seguimiento_Proyectos` queda archivado. El trabajo sigue en dos repositorios, cada uno con su historial y con la misma línea base de CI, seguridad, plantillas, Dependabot y release-please:

| Repositorio | Contenido | Pasos del plan |
|---|---|---|
| [PMAPP-Org/SUNO_server](https://github.com/PMAPP-Org/SUNO_server) (este) | `server/`, `infra/`, `docs/` (incluido el SGSI), `plans/` | backend, infraestructura e integración (24, 25 y la parte de servidor de cada paso) |
| [PMAPP-Org/SUNO_client](https://github.com/PMAPP-Org/SUNO_client) | la app Flutter en la raíz; `contract/` con la copia fijada del contrato; `tool/` con sus lints | carril móvil (14, 16, 18, 23) |

Las rutas `app/…` que citan los pasos anteriores corresponden ahora a la raíz de SUNO_client (`app/lib/…` → `lib/…`, `infra/scripts/lint_*.sh` → `tool/lint_*.sh`). Los dos repositorios se versionan por separado. La compatibilidad entre ellos la dan el contrato (`contract.yml` en el cliente) y la negociación de `schema_version` en `/v1/server/info`.

**Pendiente de terceros:** rol Admin y revisión independiente de los PR (issue #7); aprobación del SGSI por la dirección; validación jurídica de Ley 1581; VPS, dominio, buckets R2 y SMTP.

**Orden de trabajo acordado a continuación:** (1) runbooks y este seguimiento ✔ → (2) MFA en el Admin + Paso 15 ✔ → (3) aprovisionamiento del VPS + workflow de despliegue ✔ → (4) Pasos 21 ✔ → 22 ✔ → 18 (en curso) → 23 ✔ → 24 ✔ → 16 ✔ → 25.

### Lo que falta, verificado contra el código (2026-10-18)

Revisado paso a paso contra lo que hay de verdad en SUNO_server y SUNO_client, no contra este documento:

| Paso | Qué falta | ¿Se puede hacer sin hardware? |
|---|---|---|
| **23** Revisión de cuarentena | En [SUNO_client#4](https://github.com/PMAPP-Org/SUNO_client/pull/4): pantalla, corrección, reintento, descarte con confirmación y conflictos. El PR corrige además dos fallos: un conflicto pisaba la versión del agente, y el reintento reutilizaba el `op_uuid` y nunca podía funcionar. **Falta** el flujo de conflicto contra el backend real (entorno del Paso 14). | Sí, salvo esa prueba |
| **24** Back-office | Hecho en [SUNO_server#12](https://github.com/PMAPP-Org/SUNO_server/pull/12): Admin aislado por organización (probado por HTTP), revisión de visitas, revocación de dispositivos, asignaciones, plantillas, reintento de jobs, exportación CSV/XLSX con límite y derechos del titular. | — |
| **16** TLS y tokens del cliente | Hecho en [SUNO_client#8](https://github.com/PMAPP-Org/SUNO_client/pull/8): `TrustStore` (pinning SPKI con dos pines y caducidad, TOFU con diálogo y bloqueo ante cambio de certificado, verificación antes de enviar la petición), Keychain *ThisDeviceOnly* con migración, `network_security_config.xml`, ATS sin excepciones y re-login limpio ante familia revocada. Falta probarlo en dispositivo y fijar los pines reales cuando exista el dominio oficial. | La prueba en dispositivo, no |
| **25** Suite final | Ya hay tests de aislamiento, presupuesto de queries, traversal, checksum, reuso de token y spoof de `X-Forwarded-Proto`, pero dispersos. Faltan la suite consolidada, el **mapa H-01…H-21 → test** y un script de carga (no hay locust ni k6). | En parte: la carga y las mediciones de dispositivo van fuera de esta máquina |
| **18** (resto) | Subida con la app cerrada (WorkManager/BGTask) y medición en iOS. | No: dispositivo real |
| **14** E2E Fase 0 | Cerrado el 2026-10-29 (ver Paso 14 abajo). | — |
| **Ley 1581 en la app** | Mecánica hecha: aviso de privacidad antes del login con autorización por finalidad ([SUNO_client#13](https://github.com/PMAPP-Org/SUNO_client/pull/13)) y prueba guardada en el servidor (`POST /v1/consents`, [SUNO_server#25](https://github.com/PMAPP-Org/SUNO_server/pull/25)). **Falta** la validación jurídica del texto del aviso. | El texto, no: asesor jurídico |

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

- [x] **Paso 14** — Integración end-to-end: cierre de Fase 0 (checkpoints 14a–14e, ver §12 del plan). **Cerrado el 2026-10-29**: 14c–14e con la prueba automática de [docs/testing/e2e-fase0.md](../docs/testing/e2e-fase0.md) ([SUNO_server#43](https://github.com/PMAPP-Org/SUNO_server/pull/43) y la rama `feat/e2e-fase0` de SUNO_client). Una visita creada en modo avión en el emulador API 36 llega al servidor al reconectar, con TOFU real y settings de producción; dos ejecuciones seguidas en verde (`version=1`, `change_seq=2`). Lo que sigue debajo sobre 14c/14d describe el intento de agosto y queda como historia.
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

- [x] **Paso 16** — Autenticación cliente completa: `TrustStore`, TOFU explícito, pinning SPKI ([SUNO_client#8](https://github.com/PMAPP-Org/SUNO_client/pull/8); falta la prueba en dispositivo y los pines reales del dominio oficial)
- [ ] **Paso 18** — Adjuntos móvil: subida en segundo plano con reintento (en curso: cola, reintento y streaming hechos en [SUNO_client#3](https://github.com/PMAPP-Org/SUNO_client/pull/3); falta la subida con la app cerrada y su medición en dispositivo)
- [x] **Paso 21** — Renderer dinámico de formularios en Flutter
- [x] **Paso 22** — Captura de visita: UI de formulario + GPS + fotos + firma (falta la prueba manual en emulador)
- [ ] **Paso 23** — UI de conflictos: `quarantine` y reenvío (en curso: [SUNO_client#4](https://github.com/PMAPP-Org/SUNO_client/pull/4); falta el flujo contra el backend real)

### Integración final y cierre

- [x] **Paso 24** — Django Admin personalizado (back-office) + exportación CSV/Excel. Aislamiento por organización en todo el Admin (H-16 probado por HTTP), revisión de visitas (`VisitReview`), revocación de dispositivos, asignaciones con `change_seq`, publicación de plantillas validada, reintento de jobs, exportación CSV/XLSX con límite de 10 000 filas y neutralización de fórmulas, y derechos del titular (consulta y supresión por anonimización; ver `docs/seguridad/tratamiento-datos-personales.md`). Todas las acciones pasan por `backoffice/services.py` (política + auditoría).
- [ ] **Paso 25** — Suite de seguridad, prueba de carga y validación de criterios de aceptación MVP (en curso: suite y mapa H-01…H-21 automatizados, workflow de carga listo; faltan su primera ejecución y las mediciones en dispositivo, ver [`docs/seguridad/verificacion-mvp.md`](../docs/seguridad/verificacion-mvp.md))

---

## Fuera de alcance de este plan (roadmap, sin desglosar)

Ver [visitacampo-construccion.md §9](visitacampo-construccion.md#9-roadmap-de-alto-nivel--fase-2-y-fase-3-fuera-de-alcance-de-este-plan): Fase 2 (multi-tenant real, reportes PDF, push, mapa offline, SPA de back-office, Postgres gestionado, webhooks, miniaturas, PostGIS) y Fase 3 (réplica de lectura, CDN, observabilidad, blue/green, SSO/MFA, auditoría exportable, tracking GPS continuo, mTLS, extracción de servicio de ingesta). No se activan por calendario, solo por disparador observable — no forman parte de este seguimiento.

---

## Resumen

| Fase | Pasos completados | Total |
|---|---|---|
| Fase 0 | 15 (Pasos 0–14) | 15 (Pasos 0–14) |
| Fase 1 | 8 (Pasos 15, 16, 17, 19, 20, 21, 22, 24) | 11 (Pasos 15–25) |
| **Total** | **23** | **26** |

Las Fases A, B y C (posteriores a este plan) se siguen en «Estado al 2026-10-29», arriba.

**Estado del Paso 18 (móvil, en curso; [SUNO_client#3](https://github.com/PMAPP-Org/SUNO_client/pull/3))**:
- **Hecho y verificado** (`flutter test` 175 passed, cobertura 84,5 %):
  - cola de subida con estado persistente (esquema Drift v5, con test de migración v4→v5);
  - backoff exponencial con jitter, que sobrevive a reiniciar la app;
  - fallos transitorios separados de los permanentes;
  - recuperación de subidas interrumpidas;
  - `PUT` en streaming con el archivo cerrado siempre en `finally`. **H-05 queda mitigado en el cliente**, con un test que cuenta aperturas y cierres, incluido un cliente HTTP que abandona el cuerpo;
  - planificador en primer plano (al iniciar sesión, al volver a primer plano, cada minuto y al guardar);
  - independencia del outbox de datos, probada en los dos sentidos: **criterio de salida cumplido**.
- **Comandos de verificación del plan:** (a) subida correcta → `uploaded`; (b) fallo de red → backoff sin bloquear el resto de la cola; (c) `already_present` → `uploaded` sin subir bytes. Están en `test/core/attachments/` de SUNO_client.
- **Pendiente para cerrar el paso:**
  - la subida con la app cerrada o suspendida mucho tiempo (WorkManager en Android, BGTaskScheduler en iOS), que exige un plugin nativo;
  - la **medición en dispositivo real**, sobre todo en iOS (riesgo de doc 08 §9).

  Sin ellas, la cola se retoma en el siguiente arranque y no se pierde nada.
- **Decisiones:**
  - (a) No se envía `visit_id` al presign (es opcional en el contrato). Con él, la cuota por visita exigiría que la visita ya estuviera en el servidor, y eso ataría esta cola al push de datos.
  - (b) "Sincronizar" fuerza un intento inmediato (ignora el backoff y reintenta las fallidas). Los disparos automáticos respetan el backoff.
  - (c) 401/403 son transitorios: el token se renueva, y una URL firmada caducada se vuelve a pedir.

**Estado de verificación del Paso 22 (móvil + servidor, último paso cerrado)**:
- **Pruebas:** `flutter test` **159 passed**, cobertura 83,3 → 83,6 %; `dart analyze` y los tres lints limpios. Servidor: `pytest` completo **715 passed**, `ruff` y `mypy` limpios.
- **Atomicidad:** visita + adjuntos + outbox + cola de subida se guardan en una transacción. El test fuerza un fallo a mitad y comprueba que no queda nada (`test/core/capture/visit_capture_test.dart`).
- **UI optimista:** la visita aparece en la pantalla de inicio en cuanto se guarda, con estado *Pendiente* y sus adjuntos por subir, sin red.
- **Prueba preliminar de carga:** 50 visitas × 10 fotos (500 adjuntos, 550 operaciones) sin degradación. Se compara el tiempo medio de las 10 últimas contra las 10 primeras. La validación completa es el Paso 25.
- **Pendiente:** la prueba manual en emulador (sin emulador en esta máquina). La subida automática con reintento llegó después, en el Paso 18 (SUNO_client#3).

**Hallazgos del Paso 22** (corregidos):
- (1) **El servidor no creaba nunca un `visit_attachment`**: el push los rechazaba como `unsupported_entity` aunque el contrato los declara. Una respuesta de foto o firma guardaba un `id` que no apuntaba a nada. Ahora el push acepta su **alta** (`sync/payloads.py`, `services.py`, `sql.py`):
  - payload sellado;
  - la visita tiene que existir y estar viva en la organización, o crearse antes en el mismo lote;
  - si no, `not_found` sin tumbar el lote;
  - idempotencia, y conflicto con su `server_row`;
  - `update`/`delete` se rechazan con detalle.
- (2) **El cliente leía `server_row` como si fuera el `data` de la visita.** El contrato lo envía como `{entity, data}`, y los tests usaban la forma equivocada. Un `conflict` real lanzaba un `TypeError` dentro de la transacción y la operación quedaba atascada en `inflight`. Ahora se interpreta como `EntityRecord`; si no tiene esa forma, la op va a cuarentena con motivo.
- (3) **El outbox se ordenaba solo por `createdAt`**, que Drift guarda con precisión de segundos. Una visita y sus adjuntos empataban y el adjunto podía viajar antes que su visita. Desempata el `rowid` (orden de inserción).
- (4) **La subida de adjuntos fijaba `image/jpeg`**, y la firma es PNG: el servidor la habría rechazado por magic bytes. La cola guarda ahora el MIME de cada archivo (`attachment_queue.mime`, esquema v4).

**Decisiones de diseño registradas (Paso 22)**:
- (a) **Solo viajan las respuestas de campos visibles** y los adjuntos referenciados. Una foto repetida antes de guardar no se crea.
- (b) **La firma es un lienzo propio** (`SignaturePad`, PNG con tinta y papel fijos) y no un paquete externo: menos dependencias que auditar.
- (c) **Fotos adicionales** fuera de la plantilla, para el caso "10 fotos por visita".
- (d) **Tabla local `visit_attachments`** (esquema v4, con test de migración v3→v4) que enlaza cada adjunto con su fila de subida. Es lo que permite mostrar "sincronizada con fotos por subir".

**Estado de verificación del Paso 21 (móvil)**:
- **Pruebas:** `flutter test` **140 passed**; cobertura de líneas 83,3 % (gate 75 %). `dart analyze` limpio. En el servidor, `pytest visitacampo/forms visitacampo/sync` 236 passed.
- **Paridad servidor ↔ cliente:** `server/openapi/fixtures/forms_parity.json` es la fuente única de casos. Lo ejecutan `forms/tests/test_parity.py` y `app/test/core/forms/parity_test.dart`, y ambos coinciden en los 35 casos:
  - 2 plantillas válidas con 9 combinaciones de respuestas: visibilidad y violaciones idénticas;
  - 26 plantillas inválidas, rechazadas por los dos lados, entre ellas `op: eval`/`system`, claves extra, literal estructurado, referencia hacia delante o a sí mismo, regex, dialecto `@2` y profundidad excesiva.
- **Comandos de verificación del plan:**
  - render de cada tipo de campo (`test/ui/forms/`);
  - expresiones condicionales `eq`, `in`, `and(gt, ne)`, `not(is_empty)` y `or(gte, is_not_empty)` cambiando valores en pantalla;
  - plantilla mal formada → resultado tipado, nunca excepción (incluido un JSON de 5000 niveles);
  - inyección de expresión rechazada sin evaluarse;
  - **regresión de H-11** (`form_template_repository_test.dart`): al llegar la v2, una visita nueva usa la v2 y la caché de la plantilla se invalida explícitamente; una visita que referencia la v1 la sigue leyendo.
- **Criterio de salida "cero ejecución dinámica":** `infra/scripts/lint_no_dynamic_code.sh` es un gate nuevo en `mobile.yml` y `ci_local.sh`. Prohíbe `dart:mirrors`, `Function.apply`, `noSuchMethod`, `spawnUri` y la evaluación de JavaScript en toda `app/lib`.

**Decisiones de diseño registradas (Paso 21)**:
- (a) **Parser escrito a mano en vez de `freezed`.** El meta-schema necesita rechazar claves extra y no convertir tipos, y `json_serializable` no hace ninguna de las dos cosas. Los modelos son clases selladas inmutables, y el `switch` del renderer sobre ellas es exhaustivo: un tipo de campo nuevo no compila hasta tener su widget.
- (b) **El cliente es más estricto que Pydantic** (`"true"` no es `true`). Esto no rompe nada porque el servidor guarda la definición normalizada (`model_dump`).
- (c) **Caché indexada por `(org_id, template_version_id)`.** Se invalida al recibir cualquier versión de la plantilla. Los tombstones marcan `deleted_at` en vez de borrar, para que una visita histórica siga siendo interpretable.
- (d) **Una definición corrupta en disco se borra** y fuerza un pull completo (`resetCursor`). Una de un dialecto desconocido no se borra: la app pide actualizarse.
- (e) **Las filas `form_template_version` que no cumplen el contrato se ignoran** sin bloquear el pull, para no envenenar la sincronización de visitas.
- (f) **Foto, firma y ubicación se capturan fuera del renderer**, a través de `FormMediaDelegate`. El renderer solo guarda el identificador. La captura real y la integración en `CaptureScreen` son el Paso 22.

**Estado de verificación del Paso 17 (backend)**: `pytest server/` **553 passed** (+744 subtests) contra PostgreSQL real (452 antes de este paso: +101 tests nuevos), `ruff check server/` y `mypy server/ --config-file server/pyproject.toml` limpios, `manage.py makemigrations --check --dry-run` sin cambios y `manage.py check` sin incidencias — el paso **no añade ninguna tabla**: `Blob` y `VisitAttachment` ya existían desde el Paso 5. Los comandos de verificación del plan corren de verdad, en `attachments/tests/`: (a) presign de un `sha256` ya existente → `already_present: true`, sin URL y **sin tocar el object storage**; (b) commit con checksum que no coincide con el `HEAD` real → `422 attachment_checksum_mismatch` y el `Blob` **no** se registra (ídem para el tamaño); (c) commit válido → `Blob` creado, y doble commit idempotente devolviendo la misma fila byte a byte. Más: **test de traversal explícito** con 15 familias de entrada hostil (`../`, rutas absolutas POSIX y Windows, UNC, URL-encoding, byte nulo, mayúsculas, longitud incorrecta) contra `bucket_key_for()` y contra la resolución de ruta del adaptador local, comprobando además que no se crea nada en el filesystem; y **test de concurrencia** (`TransactionTestCase` + `ThreadPoolExecutor`) con dos y con ocho commits simultáneos del mismo `sha256`: todos resuelven sin error de constraint, queda **una** fila y **exactamente uno** reporta haberla creado.

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

# SUNO: "visita" pasa a "registro" y `visitacampo` pasa a `suno`

Fecha: 2026-11-06. Repos: `SUNO_server` (servidor Django) y `SUNO_client` (app Flutter).

## Contexto

El producto se llama **SUNO**, pero el código conserva el nombre de trabajo
`visitacampo` (paquete Django, paquete Dart, id de la app, base de datos, servicios,
variables de entorno). Además, el concepto central se llama "visita" en todas partes,
cuando lo que el agente hace en campo es un **registro**: desde la pieza 4 de costos,
cantidades ejecutadas por ítem y ubicación, no solo una visita.

Este cambio renombra las dos cosas, en el código, en la base de datos, en la API, en la
infraestructura y en los textos. **No hay producción con datos reales**, así que todo se
cambia en el sitio: no hay período de convivencia entre contratos, y todos los ambientes
se recrean.

La etapa 4b de costos (cantidades en la app) queda en pausa y se construye después sobre
los nombres nuevos.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Alcance | Texto visible **y** nombres internos: código, tablas, API, permisos, auditoría e infraestructura. |
| Nombre del sistema | `visitacampo` pasa a `suno` (paquete del servidor, paquete Dart, base de datos, servicios, volúmenes, variables `SUNO_*`). |
| Id de la app | `com.visitacampo.visitacampo` pasa a `com.suno.app` (Android `applicationId`/`namespace` e iOS bundle id). Es una app nueva para las tiendas; sin producción no se pierde nada. |
| Contrato | Se cambia en el sitio. `SCHEMA_VERSION` y `MIN_SUPPORTED_SCHEMA_VERSION` pasan a 2 al mismo tiempo; la app vieja deja de sincronizar. |
| Migraciones del servidor | Sin producción, las migraciones 0001–0014 se reescriben en el sitio con los nombres nuevos; no se agregan `RenameModel`. Todos los ambientes recrean la base. |
| Migraciones del servidor (2026-11-09) | **Reemplaza a la fila anterior.** Master ya se despliega a producción en cada commit, así que 0001–0026 quedan intactas y el renombre es aditivo: `0027_rename_visit_to_record.py` lleva una base poblada al esquema nuevo (tablas, columnas, constraints, índices, permisos, tipos de contenido, `sync_op_log`) y es reversible. La traza de auditoría conserva sus códigos históricos. |
| Migraciones de la app | Como el id de la app cambia, ningún teléfono actualiza desde la base vieja: el historial de Drift se reinicia en una versión 1 con los nombres nuevos. |
| Documentos | Los históricos de `plans/` no se tocan. Los vivos (`docs/`, `README`, `CONTRIBUTING`, registro de avance) se actualizan; el registro de avance pasa a `plans/suno-progreso.md`. |
| Repos de GitHub | No cambian: ya se llaman SUNO. |

## 1. Mapa de nombres

| Hoy | Nuevo |
|---|---|
| Paquete `visitacampo` (servidor) y paquete Dart `visitacampo` | `suno` |
| `com.visitacampo.visitacampo` | `com.suno.app` |
| `Visit`, `VisitAttachment`, `VisitReview`, `VisitReviewStatus` | `Record`, `RecordAttachment`, `RecordReview`, `RecordReviewStatus` |
| Tablas `visit`, `visit_attachment`, `visit_review` | `record`, `record_attachment`, `record_review` |
| Campos y claves `visit_id`, `visits`; motivo de rechazo `visit_validated` | `record_id`, `records`, `record_validated` |
| Rutas y esquemas de `openapi/v1.yaml` (`/sync/...visits...`, `Visit*`) | `records`, `Record*` (el prefijo `/api/v1` se queda) |
| Permisos `view_visit` (y demás), códigos de auditoría `visit.*` | `view_record`, `record.*` |
| Base de datos, servicios, volúmenes, variables `VISITACAMPO_*` | `suno`, `SUNO_*` |
| Texto visible "visita / visitas" | "registro / registros", con la concordancia corregida ("la visita validada" pasa a "el registro validado") |

Las clases Dart siguen la misma regla (`Visit` pasa a `Record`, tablas Drift `Visits` y
`VisitAttachments` pasan a `Records` y `RecordAttachments`). Si `Record` choca con un
nombre de Dart o de una dependencia (por ejemplo los *records* del lenguaje), se resuelve
con un prefijo de importación, no cambiando el nombre del concepto.

**Excepciones** (palabras que contienen "visit" pero no son el concepto, como `revisit` o
"visitante") se revisan una por una y quedan en la lista permitida de la prueba guardiana.

## 2. Entregas

Tres PR, en este orden:

1. **Servidor, paquete `visitacampo` pasa a `suno`.** `git mv`, imports, settings,
   Dockerfile, compose, `infra/` (provisión, despliegue, respaldos, monitoreo),
   `.github/workflows`, `.gitleaksignore`, `CODEOWNERS`, docs vivos. Sin cambios de
   concepto: si algo se rompe, es el paquete.
2. **Servidor, `Visit` pasa a `Record`.** Modelos y migraciones reescritas, sync y su SQL
   crudo, `openapi/v1.yaml` con `SCHEMA_VERSION = 2`, permisos, auditoría, Admin,
   plantillas, exportaciones, textos visibles y pruebas.
3. **App.** Paquete Dart, id de la app y concepto, más el reinicio del historial de Drift y
   los textos de l10n. Habla el contrato con esquema 2.

El servidor (PR 1 y 2) y la app (PR 3) se liberan juntos.

## 3. Técnica

- **Script de renombre** con reglas explícitas y ordenadas: las más largas primero
  (`VisitReviewStatus` antes de `VisitReview`, antes de `Visit`; `visit_id` antes de
  `visit`). Trabaja por palabra completa, y cubre plurales y concordancia del español
  ("la visita" pasa a "el registro", "las visitas" pasa a "los registros", "una visita"
  pasa a "un registro", y los adjetivos que concuerdan). Es una herramienta de trabajo:
  no se versiona.
- **Revisión manual** del resultado, archivo por archivo, sobre todo de los textos
  visibles y del SQL crudo de sync.
- **Recrear ambientes**: Docker local, la base de pruebas de CI y el despliegue de prueba.

## 4. Verificación

- **Suites completas**: pytest con cobertura, ruff, mypy, `makemigrations --check`,
  bandit, semgrep; `flutter analyze` y `flutter test`. CI en verde.
- **Prueba guardiana nueva**, en el servidor y en la app: falla si aparece "visit" o
  "visitacampo" en código, plantillas, l10n, `openapi/` o `infra/`, fuera de una lista
  corta de excepciones (`plans/` históricos y las palabras que solo contienen "visit").
- **Sincronización de punta a punta** app contra servidor, siguiendo
  `docs/testing/e2e-fase0.md`: crear un registro, sincronizar, revisar, validar.
- **Revisión visual** de la app y de la web a 1440 px y 390 px: redacción, concordancia
  y textos cortados.

## 5. Versiones

El contrato cambia de forma incompatible. release-please sube el servidor a 0.14.0 y la
app a 0.12.0. Ambos se publican juntos.

## Fuera de alcance

- Etapa 4b de costos (cantidades en la app). Sus decisiones ya tomadas se conservan y se
  retoman después de este cambio, sobre los nombres nuevos.
- Cambiar el nombre de los repos de GitHub.
- Reescribir los documentos históricos de `plans/`.

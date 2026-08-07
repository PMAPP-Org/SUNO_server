# Plan priorizado — Camino mínimo a un demo desplegable

**Generado**: 2026-08-06
**Relación con el plan general**: este documento **no reemplaza** [visitacampo-construccion.md](visitacampo-construccion.md) ni su seguimiento en [visitacampo-progreso.md](visitacampo-progreso.md) — los reordena y recorta temporalmente para llegar antes a un objetivo distinto: un demo interno mostrable desplegado en un VPS real, con la app móvil ejecutando un flujo fijo de punta a punta. Los pasos diferidos aquí siguen vigentes en el plan general y se retoman después de este objetivo.
**Fuente de verdad arquitectónica**: [docs/08-arquitectura-propuesta-visitacampo.md](../docs/08-arquitectura-propuesta-visitacampo.md) (ADRs, RD-01…RD-16) — ninguna decisión ahí se reabre.
**Alcance de ejecución**: **solo construcción de código** (backend + móvil) hasta dejarlo en estado desplegable y verificado localmente (`pytest`/`ruff`/`mypy`, `flutter analyze`/`flutter test`). El montaje en el VPS real (contratación, DNS, R2, `.env` de producción) y las pruebas en emulador/dispositivo las hace el usuario por su cuenta, fuera de este plan.

---

## 1. Objetivo y criterio de éxito

Poder ejecutar, sobre un VPS real con Docker Compose + Caddy (arquitectura ya cerrada en doc 08), el ciclo: **login → capturar una visita (formulario fijo, GPS, una foto) → sincronizar → ver la visita en el backend**. El criterio de éxito es que ese ciclo funcione real (no simulado) con el código que este plan produce; el propio despliegue y la prueba en emulador quedan fuera de esta ejecución.

## 2. Qué ya existe y no se reconstruye

- Backend: auth JWT (Paso 7), `POST /v1/sync/push` y `GET /v1/sync/pull` (Pasos 8–9), adjuntos con `BlobStore`/presign/commit (Paso 17), formularios declarativos (Paso 20). 553 tests backend en verde.
- Móvil: dominio de auth (`AuthController`, Paso 13), `SyncEngine` (Paso 12), almacén local Drift+SQLCipher con outbox (Paso 11). 51 tests móvil en verde.
- `infra/scripts/check_envvars.sh`: ya reescrito en Pasos 2/3 (equivalente propio del script de qfieldcloud) — no requiere cambios.
- Lo que falta no es lógica de dominio — es **empaquetado desplegable** (backend) y **UI mínima** (móvil) que use ese dominio ya construido.

## 3. Estrategia de reuso de código existente

Antes de escribir código nuevo, se revisaron los dos repos de referencia del monorepo para no duplicar lo que ya existe probado en producción real:

- **`qfieldcloud/` (MIT, mismo stack Django+Postgres) — reuso directo y concreto**:
  - `docker-app/Dockerfile`: patrón multi-stage (`builder` → `base` → `webserver_runtime`/`worker_runtime`) con `uv` para instalar dependencias, usuario `app` no-root, directorios `staticfiles`/`mediafiles`. Se adapta quitando las librerías de GDAL/GeoDjango (VisitaCampo no usa PostGIS, ADR-005) y sin el stage `worker_wrapper` que monta el socket Docker (VisitaCampo rechaza docker-in-docker por ADR-006).
  - `docker-app/entrypoint.sh`: patrón mínimo de espera-a-servicios + `exec "$@"`, adaptado a esperar Postgres antes de `migrate`/`collectstatic`/arrancar gunicorn.
  - **`django-axes`**: ya nombrada en ADR-004 como la pieza para rate limiting de login, nunca instalada. El login de VisitaCampo (`authn/services.py:250`) ya llama a `django.contrib.auth.authenticate()`, el mismo hook que usa qfieldcloud (`settings.py`: `AUTHENTICATION_BACKENDS`, middleware, `AXES_FAILURE_LIMIT=5`, `AXES_LOCKOUT_PARAMETERS`, `AXES_COOLOFF_TIME`, `AXES_RESET_ON_SUCCESS`). **Se instala y configura la librería en vez de escribir rate limiting a mano** — cierra la necesidad de "endurecimiento mínimo de login" sin código nuevo de lógica, solo config.
  - Separación de requirements por proceso (`requirements.txt` / `requirements_runtime.txt` con `gunicorn`) — patrón reusado para separar dependencias de app de las de runtime del contenedor (VisitaCampo ya usa `uvicorn` según ADR-004: gunicorn con workers uvicorn).
- **`QField/` (GPL-2.0, stack C++/Qt/QML) — sin reuso de código**: es un stack completamente distinto al Flutter/Dart de VisitaCampo (decisión cerrada en ADR-001, precisamente porque QGIS/Qt no aportan nada sin motor SIG embebido) y, al ser GPL-2.0, copiar cualquier fragmento (incluso scripts de build/CI) arrastraría obligaciones de copyleft sobre VisitaCampo. Lo que QField ya aportó es *patrón arquitectónico*, ya absorbido en docs 01/03 y cerrado en los ADRs de doc 08 — esa es la reutilización legítima de un proyecto GPL (ideas, no código), y ya está hecha. No se retoma nada nuevo de QField en este plan.

## 4. Alcance de este plan

### Backend — empaquetado desplegable

1. **`server/Dockerfile`** (no existe hoy): adaptación del multi-stage de qfieldcloud (ver §3) — sin GDAL, sin stage docker-in-docker. Activar el servicio `api` real en `infra/docker-compose.yml` (hoy placeholder `sleep infinity`, ver comentarios inline del propio archivo) usando esta imagen.
2. **`server/entrypoint.sh`**: adaptación del patrón de qfieldcloud (espera a `db` healthy, luego `migrate`/`collectstatic`, luego `exec` del comando real).
3. **Completar `config/settings/prod.py`**: hoy solo tiene `DEBUG=False` y `ALLOWED_HOSTS` (su propio docstring ya anticipa este trabajo como pendiente). Falta: gunicorn+uvicorn, cabeceras de proxy confiable detrás de Caddy (RD-15: `X-Forwarded-Proto`, `SECURE_SSL_REDIRECT`, HSTS — sin equivalente directo en qfieldcloud, que usa nginx no Caddy; se escribe siguiendo el checklist estándar de `manage.py check --deploy`, ya usado como gate en CI desde el Paso 3), y `STORAGES` apuntando a Cloudflare R2 (el `S3BlobStore` de código ya existe desde el Paso 17; falta cablearlo en settings de producción con credenciales por variable de entorno).
4. **`django-axes`**: instalar y configurar (ver §3) — reemplaza la construcción de rate limiting propio.

### Móvil — UI mínima de un flujo real (sin insumos reusables de QField, ver §3)

5. **Reemplazar `app/lib/main.dart`** (hoy el demo de `flutter create` sin modificar) por una app real con navegación mínima: login → captura → confirmación.
6. **Pantalla de login** conectada al `AuthController` ya existente.
7. **Pantalla de captura** con un formulario **fijo** (no el renderer dinámico de JSON Schema del Paso 21 — eso sigue pendiente), captura de GPS vía `LocationSource` y una foto vía cámara, persistidas en outbox/`attachment_queue` a través de los repositorios ya construidos.
8. **Sincronización manual**: un botón dispara `SyncEngine` (push+pull) y la subida del adjunto en primer plano (sin el reintento en segundo plano del Paso 18 completo, que sigue pendiente).
9. **TLS**: se apoya en la validación estándar ya existente (Paso 13, rechaza certificados inválidos) contra el certificado real que emitirá Caddy vía Let's Encrypt en el VPS. No se construye el flujo completo de pinning/TrustStore con consentimiento del Paso 16 — ese flujo existe para self-hosted con certificados no confiables por una CA pública, que no es el caso de un dominio real.

## 5. Explícitamente diferido (sigue en el plan general, no se pierde)

| Paso del plan general | Por qué se difiere aquí |
|---|---|
| Paso 15 (auth hardening completo) | Solo se instala `django-axes` (ver §3/§4.4); rotación/detección de reuso de refresh tokens no es necesaria para un demo controlado |
| Paso 16 (TrustStore/pinning completo) | Solo aplica a certificados no emitidos por una CA pública; el demo usa un dominio real con Let's Encrypt |
| Paso 18 (adjuntos móvil, reintento en segundo plano) | El demo sube en primer plano; el reintento en background es una mejora de robustez, no un bloqueante funcional |
| Paso 19 (cola de trabajos) | Nada del ciclo login→captura→sync depende de jobs asíncronos (PDF, exportaciones, notificaciones) |
| Paso 21 (renderer dinámico de formularios) | El demo usa un formulario fijo; el renderer dinámico es trabajo adicional no bloqueante para probar el ciclo |
| Paso 23 (UI de conflictos) | Los conflictos son raros por diseño (doc 08 ADR-003) y no se esperan en una prueba controlada de un solo agente |
| Paso 24 (back-office/export) | Verificar la visita puede hacerse por Django Admin (ya registrado desde el Paso 14a) sin necesitar exportación CSV/Excel |
| Paso 25 (seguridad/carga) | Prueba de carga y validación de aceptación MVP son objetivos de un release real, no de un demo interno |

## 6. Orden de construcción

Se construye **backend primero** porque el móvil necesita un servidor real desplegable contra el que probar en la fase de testeo del usuario:

1. §4.1 — Dockerfile (adaptado de qfieldcloud) + activación del servicio `api`
2. §4.2 — `entrypoint.sh` (adaptado de qfieldcloud)
3. §4.3 — `prod.py` completo (gunicorn+uvicorn, RD-15, `STORAGES`/R2)
4. §4.4 — `django-axes` instalado y configurado
5. §4.5–4.6 — `main.dart` real + pantalla de login
6. §4.7 — Pantalla de captura (formulario fijo + GPS + foto)
7. §4.8 — Sincronización manual (push/pull + subida de adjunto)

Cada punto se verifica con los mismos comandos ya en uso en el plan general (`pytest`/`ruff`/`mypy`, `flutter analyze`/`flutter test`) antes de pasar al siguiente, y se commitea por separado.

## 7. Verificación

Ninguna tarea de este plan requiere Docker completo + emulador a la vez (lección del diagnóstico de estancamiento del 2026-08-03, ver `visitacampo-progreso.md`). El backend se verifica con `pytest server/` contra el `db` de compose ya usado en Pasos 14b/17/20; el móvil con `flutter analyze`/`flutter test` (headless, `--concurrency=1` si hay problemas de RAM, mitigación ya validada en el Paso 13). El montaje real en el VPS y la prueba en emulador/dispositivo quedan fuera de esta ejecución.

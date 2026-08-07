# Demo desplegable VisitaCampo — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar VisitaCampo en estado desplegable en un VPS real (Docker Compose + Caddy) y con una app móvil capaz de ejecutar, de punta a punta, el ciclo login → capturar visita (formulario fijo + GPS + una foto) → sincronizar → ver la visita en el backend.

**Architecture:** Backend Django 5 + Django Ninja detrás de gunicorn (workers uvicorn) y Caddy (TLS automático), empaquetado en una imagen Docker propia adaptada del patrón multi-stage de `qfieldcloud/docker-app/Dockerfile` (sin GDAL/GeoDjango, sin stage de docker-in-docker). Rate limiting de login vía `django-axes` reutilizando el bloque de configuración de `qfieldcloud/docker-app/qfieldcloud/settings.py`. Móvil: Flutter, reemplazando el demo de `flutter create` por una app real que reutiliza el dominio ya construido (`AuthController`, `SyncEngine`, `AppDatabase`) y añade solo lo que falta (pantallas, GPS, cámara, clientes HTTP de sync/adjuntos).

**Tech Stack:** Django 5.2.16, django-ninja 1.6.2, PostgreSQL, gunicorn + `uvicorn_worker`, Caddy 2, Docker Compose. Flutter (Dart SDK ^3.12.2), Riverpod, Dio, Drift+SQLCipher, `geolocator`, `image_picker`, `crypto`.

## Global Constraints

- Python `>=3.12` (server/pyproject.toml) — la imagen base es `python:3.12-slim-bookworm`, no una versión más nueva no probada contra `Django==5.2.16`/`psycopg[binary]==3.3.4`.
- VisitaCampo **no** usa PostGIS/GeoDjango (`DATABASES.ENGINE = "django.db.backends.postgresql"`, sin `django.contrib.gis`) — ningún paquete GDAL/`libproj-dev`/`gdal-bin` se instala en la imagen.
- VisitaCampo **rechaza docker-in-docker** (ADR-006: worker sin `docker.sock`, sin Redis, sin Celery) — no se añade ningún stage que monte el socket de Docker.
- `psycopg[binary]==3.3.4` ya trae `libpq` empaquetado — no se instalan `libpq-dev`/`gcc`/`python3-dev` en la imagen (a diferencia de `qfieldcloud/docker-app/Dockerfile`, que sí los necesita porque no fija el extra `[binary]`).
- Ningún archivo de infraestructura puede introducir una variable de entorno nueva sin documentarla en `infra/.env.example` (regla ya vigente del repo, `infra/scripts/check_envvars.sh`).
- `ruff check server/`, `mypy --config-file server/pyproject.toml server/` y `pytest server/` deben seguir en verde tras cada tarea del track backend; `flutter analyze` y `flutter test` (desde `app/`) deben seguir en verde tras cada tarea del track móvil.
- Lint anti-TLS-bypass (`infra/scripts/lint_no_tls_bypass.sh`, RD-01/ADR-009): ningún archivo nuevo de `app/lib/data/http/` debe tocar la validación de certificados — todas las llamadas nuevas van sobre el `Dio` compartido ya construido, nunca sobre un `HttpClientAdapter` propio.
- `app/analysis_options.yaml` fija `strict-casts`, `strict-inference`, `strict-raw-types` y `avoid_dynamic_calls: true` — todo el código Dart nuevo declara tipos explícitos, nunca `dynamic` implícito.
- No se construye nada de la lista de diferidos del documento de diseño (`plans/visitacampo-despliegue-demo.md` §5): Paso 15 completo, Paso 16 completo (TrustStore/pinning), Paso 18 completo (reintento en segundo plano de adjuntos), Paso 19 (cola de jobs), Paso 21 (renderer dinámico de formularios), Paso 23 (UI de conflictos), Paso 24 (back-office/export), Paso 25 (seguridad/carga).
- El push de sincronización del servidor **solo acepta la entidad `visit`** (`visitacampo/sync/payloads.py::WRITABLE_ENTITIES = frozenset({"visit"})`) — ninguna tarea de este plan intenta crear una fila `VisitAttachment` vía `/v1/sync/push`; ese enlace queda fuera de alcance (ver la Decisión de diseño 6 más abajo).

---

## Decisiones de diseño tomadas en este plan (no cubiertas explícitamente por el documento de diseño)

1. **Imagen base**: `python:3.12-slim-bookworm` (no `python:3.14-slim-trixie` de qfieldcloud) — coherente con el piso `>=3.12` de `server/pyproject.toml`, sin asumir compatibilidad no verificada con una versión de Python más nueva.
2. **Gestor de paquetes de la imagen**: `pip` puro, no `uv`. qfieldcloud usa `uv`, pero introducir una herramienta de empaquetado nueva que ningún otro sitio del repo usa es desproporcionado para un solo Dockerfile de demo (YAGNI).
3. **Servidor ASGI**: `gunicorn` + `uvicorn_worker.UvicornWorker` (paquete `uvicorn-worker`), no `uvicorn.workers.UvicornWorker` — la clase de worker empaquetada dentro de `uvicorn` está deprecada/eliminada en versiones recientes de `uvicorn` en favor del paquete separado `uvicorn-worker`.
4. **Versiones ancladas nuevas** (sin acceso a PyPI/pub.dev para verificar la última versión exacta en este entorno — el ejecutor debe confirmarlas o subirlas antes de mergear): `django-axes==7.0.2`, `gunicorn==23.0.0`, `uvicorn[standard]==0.34.0`, `uvicorn-worker==0.3.0`, `geolocator: ^14.0.2`, `image_picker: ^1.1.2`, `crypto: ^3.0.6`.
5. **`django-axes` usa el cache `default` (locmem)**, no Redis/Memcached — coherente con ADR-006 ("sin Redis, sin Celery"). Con `gunicorn --workers 3` cada worker mantiene su propio contador de intentos fallidos (el límite real bajo carga es `AXES_FAILURE_LIMIT × workers`, no `AXES_FAILURE_LIMIT`). Aceptable para un demo interno de un solo agente; documentado como límite conocido, no un bug.
6. **No se crea ninguna fila `VisitAttachment`** desde el móvil en este plan. `visitacampo/sync/payloads.py::WRITABLE_ENTITIES` solo admite `visit` — un intento de `push` con `entity: "visit_attachment"` se rechaza con `rejected` (`"Entity 'visit_attachment' is not writable by this server version."`). El flujo de foto de este plan sube y confirma el **blob** (`presign` → `PUT` → `commit`, Paso 17 ya construido) sin crear el registro de enlace `visit_attachment`; el criterio de éxito del documento de diseño ("ver la visita en el backend") se satisface viendo la fila `Visit` en Django Admin y, si se quiere verificar la foto, el `Blob` confirmado (mismo Admin) o el objeto en el bucket. Enlazar el adjunto a la visita queda para cuando el push acepte esa entidad (fuera de este plan).
7. **`template_version_id` se captura como campo de texto** (UUID pegado a mano) en la pantalla de captura, no se resuelve con una lista desplegable ni se crea un flujo de asignación. El renderer dinámico de formularios (Paso 21) está diferido, y no existe ningún endpoint en este alcance para listar `FormTemplateVersion`. El operador del demo siembra una plantilla vía Django Admin antes de la prueba (ya registrado desde el Paso 14a) y pega su UUID en el formulario.
8. **Sin restauración de sesión al reiniciar la app**: la app siempre arranca en `LoginScreen`. Restaurar sesión leyendo el refresh token persistido es una mejora legítima pero no está pedida por el criterio de éxito ("login → capturar → sincronizar") y añade una máquina de estados reactiva (`AuthState` no es observable hoy, es un getter plano) que no hace falta para el demo.
9. **URL del backend configurable por `--dart-define`** (`VISITACAMPO_API_BASE_URL`), no hardcodeada ni con pantalla de ajustes. Es la forma más simple de apuntar la build de demo al VPS real sin construir UI de configuración.
10. **Composición de dependencias con un único objeto `AppDependencies`** expuesto por un solo `Provider` de Riverpod, no un grafo fino de providers por clase. `AuthController`/`SyncEngine` no son observables (no son `ChangeNotifier`/`StateNotifier`); construir un grafo reactivo de Riverpod alrededor de clases que no notifican cambios no aportaría nada al demo — la navegación es imperativa (`Navigator.pushReplacement` tras un resultado de `login()`).
11. **"`STORAGES` apuntando a Cloudflare R2" (documento de diseño §4.3) no significa el setting `STORAGES` de Django.** VisitaCampo no usa `django-storages` ni `FileField`/`ImageField`: los adjuntos se firman y verifican directamente con `boto3` en `visitacampo/attachments/s3.py` (Paso 17, ya construido), a través de las variables `STORAGE_BACKEND`/`STORAGE_S3_*` que `config/settings/base.py` ya lee íntegramente (líneas 206-224) desde el Paso 17. Lo único que faltaba para que R2 funcione en producción era que esas variables llegaran al contenedor `api` — la Tarea 1 las añade al servicio `api` de `infra/docker-compose.yml`. El `STORAGES` de Django que sí se completa en la Tarea 3 es el de `django.contrib.staticfiles` (CSS/JS de Django Admin), un setting distinto y sin relación con R2/adjuntos.
12. **`presign` se llama sin `visit_id`** (parámetro opcional del contrato). Pasarlo obligaría a esperar a que el `push` de la visita haya terminado con éxito en el servidor antes de poder subir la foto (si no, `_check_visit_quota` responde 404 porque la visita aún no existe server-side). Omitirlo evita esa dependencia de orden y no ejercita la cuota por visita (ADR-007), que no es necesaria para un demo de una sola foto.

---

## Track Backend

### Task 1: `server/Dockerfile` — imagen de la API + activación del servicio `api`

**Files:**
- Create: `server/Dockerfile`
- Create: `server/.dockerignore`
- Create: `server/entrypoint.sh` (versión mínima en esta tarea; la Tarea 2 la reemplaza por la versión completa)
- Create: `server/requirements-runtime.txt`
- Modify: `infra/docker-compose.yml:56-74` (servicio `api`)
- Modify: `infra/.env.example:35-40` (añadir `DJANGO_ALLOWED_HOSTS`, `DJANGO_SETTINGS_MODULE`)

**Interfaces:**
- Consumes: `server/requirements.txt` (Django 5.2.16, django-ninja, psycopg[binary], PyJWT, cryptography, boto3 — ya existente), `server/config/wsgi.py`/`server/config/asgi.py` (`application`, ya existentes), `visitacampo/health/api.py::healthz` (`GET /v1/healthz` → `{"status": "ok"}`, sin auth, ya existente).
- Produces: imagen Docker `server:latest` (tag local) que sirve `config.asgi:application` con gunicorn en el puerto `8000`; servicio `api` de `infra/docker-compose.yml` construido desde `server/Dockerfile` en vez del placeholder `python:3.12-slim` + `sleep infinity`. La Tarea 2 depende de que esta imagen ya construya y arranque.

- [ ] **Step 1: Escribir la aserción de que el servicio `api` de compose hoy es un placeholder (para verificar después que dejó de serlo)**

Ejecutar desde la raíz del repo y confirmar la salida esperada (todavía placeholder):

```bash
cd infra && cp -n .env.example .env; docker compose config --format json 2>/dev/null | python3 -c "import json,sys; c=json.load(sys.stdin); print(c['services']['api'].get('image'), c['services']['api'].get('command'))"
```

Expected: `python:3.12-slim ['sh', '-c', "echo 'api: placeholder...` (confirma que todavía no hay `build:` para `api`). Si `docker`/`docker compose` no está instalado en este entorno, anotar el resultado como "no verificado localmente, pendiente de un entorno con Docker" (mismo criterio de `infra/scripts/ci_local.sh`) y continuar con los pasos de implementación igualmente.

- [ ] **Step 2: Crear `server/.dockerignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.blobstore/
staticfiles/
*.sqlite3
.git/
```

- [ ] **Step 3: Crear el `entrypoint.sh` mínimo (placeholder de esta tarea, reemplazado por la Tarea 2)**

`server/entrypoint.sh`:

```sh
#!/bin/sh
# Placeholder de la Tarea 1 (plans/visitacampo-despliegue-demo-implementacion.md):
# solo ejecuta el comando del contenedor. La Tarea 2 sustituye este archivo por
# la versión completa (espera a Postgres, migrate, collectstatic).
set -eu

exec "$@"
```

- [ ] **Step 4: Crear `server/requirements-runtime.txt`**

Separación de dependencias de proceso servidor vs. dependencias de aplicación (patrón de `qfieldcloud/docker-app/requirements/requirements_runtime.txt`, citado en el documento de diseño §3):

```
# VisitaCampo backend — dependencias del proceso servidor (gunicorn + worker
# ASGI), separadas de server/requirements.txt (dependencias de la aplicación).
# Patrón adaptado de qfieldcloud/docker-app/requirements/requirements_runtime.txt.
gunicorn==23.0.0
uvicorn[standard]==0.34.0
uvicorn-worker==0.3.0
```

- [ ] **Step 5: Escribir `server/Dockerfile`**

```dockerfile
##########################
# BUILD                  #
##########################
# Instala las dependencias en un stage aparte para no arrastrar caché de pip
# ni artefactos de compilación a la imagen final.
FROM python:3.12-slim-bookworm AS build

WORKDIR /usr/src/app

COPY requirements.txt requirements-runtime.txt ./
# psycopg[binary]==3.3.4 (server/requirements.txt) ya empaqueta libpq: no se
# instala libpq-dev/gcc/python3-dev aquí (diferencia deliberada con
# qfieldcloud/docker-app/Dockerfile, que no fija el extra [binary]).
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt -r requirements-runtime.txt

##########################
# WEBSERVER RUNTIME      #
##########################
FROM python:3.12-slim-bookworm AS webserver_runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata: VisitaCampo fija TIME_ZONE="America/Bogota" (config/settings/base.py)
# y necesita la base de datos de zonas horarias del sistema para resolverla.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system app --ingroup app

WORKDIR /usr/src/app

COPY --from=build /install /usr/local

COPY . .
RUN mkdir -p staticfiles \
    && chmod +x entrypoint.sh \
    && chown -R app:app /usr/src/app

USER app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# uvicorn_worker.UvicornWorker (paquete uvicorn-worker) en vez de la clase
# uvicorn.workers.UvicornWorker empaquetada dentro de uvicorn (deprecada/
# retirada en versiones recientes de uvicorn).
CMD ["gunicorn", "config.asgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "3"]
```

- [ ] **Step 6: Añadir `DJANGO_ALLOWED_HOSTS` y `DJANGO_SETTINGS_MODULE` a `infra/.env.example`**

En la sección `# --- Django (servicios \`api\` / \`worker\`, esqueleto en Paso 4) ---` (línea 35 en adelante), añadir después de `DJANGO_DEBUG=true`:

```
# Módulo de settings de Django. "config.settings.dev" en desarrollo (el
# override.dev.yml lo fuerza igual); en producción/self-hosted debe ser
# "config.settings.prod" (Tarea 3 de plans/visitacampo-despliegue-demo-implementacion.md).
DJANGO_SETTINGS_MODULE=config.settings.prod
# Lista de hosts separados por coma que Django acepta en la cabecera Host
# (config/settings/prod.py). En desarrollo no se usa (dev.py fija "*"), pero
# debe existir en .env para que check_envvars.sh no falle. Ejemplo real:
# DJANGO_ALLOWED_HOSTS=visitas.tudominio.com
DJANGO_ALLOWED_HOSTS=localhost
```

- [ ] **Step 7: Activar el servicio `api` en `infra/docker-compose.yml`**

Reemplazar el bloque `api:` completo (líneas 56-74) por:

```yaml
  api:
    build:
      context: ../server
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}
      DJANGO_DEBUG: ${DJANGO_DEBUG}
      DJANGO_SETTINGS_MODULE: ${DJANGO_SETTINGS_MODULE}
      DJANGO_ALLOWED_HOSTS: ${DJANGO_ALLOWED_HOSTS}
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_HOST: ${POSTGRES_HOST}
      POSTGRES_PORT: ${POSTGRES_PORT}
      JWT_SIGNING_KEY: ${JWT_SIGNING_KEY}
      JWT_SIGNING_KEY_FILE: ${JWT_SIGNING_KEY_FILE}
      STORAGE_BACKEND: ${STORAGE_BACKEND}
      STORAGE_S3_ENDPOINT_URL: ${STORAGE_S3_ENDPOINT_URL}
      STORAGE_S3_ACCESS_KEY_ID: ${STORAGE_S3_ACCESS_KEY_ID}
      STORAGE_S3_SECRET_ACCESS_KEY: ${STORAGE_S3_SECRET_ACCESS_KEY}
      STORAGE_S3_BUCKET_NAME: ${STORAGE_S3_BUCKET_NAME}
      STORAGE_S3_REGION: ${STORAGE_S3_REGION}
      STORAGE_S3_USE_PATH_STYLE: ${STORAGE_S3_USE_PATH_STYLE}
    depends_on:
      db:
        condition: service_healthy

  worker:
    # PLACEHOLDER — sin código real hasta el Paso 19 (cola de trabajos SKIP LOCKED).
    # Fuera de alcance de plans/visitacampo-despliegue-demo-implementacion.md.
    image: python:3.12-slim
    restart: unless-stopped
    command: ["sh", "-c", "echo 'worker: placeholder sin codigo (activar en Paso 19, ver infra/docker-compose.yml)'; sleep infinity"]
    environment:
      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}
      DJANGO_DEBUG: ${DJANGO_DEBUG}
    depends_on:
      db:
        condition: service_healthy
```

(El bloque `worker:` no cambia de contenido — se reproduce completo aquí solo para que el diff de esta tarea quede inequívoco sobre dónde termina el bloque `api:` reemplazado.)

- [ ] **Step 8: Validar la interpolación de compose**

```bash
cd infra && docker compose config
```

Expected: YAML válido, `services.api.build.context` resuelve a `../server`, y ninguna variable aparece como `null`/vacía salvo las que ya eran placeholders de desarrollo en `.env.example`. Si Docker no está disponible en este entorno, anotar como no verificado (mismo criterio que `ci_local.sh`) y confiar en la revisión manual del YAML.

- [ ] **Step 9: Construir la imagen y verificar con un smoke test sin Postgres**

`healthz` (`visitacampo/health/api.py`) no toca la base de datos, así que sirve como humo sin levantar `db`:

```bash
docker build -t visitacampo-api:dev server/
docker run --rm -d --name visitacampo-api-smoke -p 8000:8000 \
  -e DJANGO_SECRET_KEY=smoke-test-key \
  -e DJANGO_SETTINGS_MODULE=config.settings.prod \
  -e DJANGO_ALLOWED_HOSTS=localhost \
  visitacampo-api:dev
sleep 2
curl -sf http://localhost:8000/v1/healthz
docker stop visitacampo-api-smoke
```

Expected: `curl` imprime `{"status":"ok"}` (el contenedor arranca gunicorn y Django importa `config.asgi:application` sin necesitar Postgres alcanzable, porque `DATABASES` se resuelve perezosamente). Si Docker no está disponible, marcar como pendiente de un entorno con Docker.

- [ ] **Step 10: Commit**

```bash
git add server/Dockerfile server/.dockerignore server/entrypoint.sh server/requirements-runtime.txt infra/docker-compose.yml infra/.env.example
git commit -m "infra: build server/Dockerfile and activate the api service in compose"
```

---

### Task 2: `server/entrypoint.sh` completo (espera a Postgres + migrate + collectstatic)

**Files:**
- Create: `server/wait_for_db.py`
- Modify: `server/entrypoint.sh` (reemplaza el placeholder de la Tarea 1)
- Create: `server/tests/__init__.py`
- Create: `server/tests/test_wait_for_db.py`

**Interfaces:**
- Consumes: variables de entorno `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` (ya documentadas en `infra/.env.example`, ya inyectadas al servicio `api` por la Tarea 1); `psycopg` (ya en `server/requirements.txt`, v3 — no `psycopg2` como en qfieldcloud).
- Produces: `wait_for_postgres(conninfo: dict[str, object], *, timeout_seconds: float = 30, retry_interval_seconds: float = 2, connect: Callable[..., object] = psycopg.connect) -> bool` — función pura e inyectable en `server/wait_for_db.py`, usada por la Tarea 2 y probada sin red real. `server/entrypoint.sh` ahora ejecuta, en orden: `wait_for_db.py`, `manage.py migrate --noinput`, `manage.py collectstatic --noinput`, `exec "$@"`.

- [ ] **Step 1: Escribir el test de `wait_for_postgres` (falla porque el módulo no existe todavía)**

`server/tests/__init__.py`:

```python
```

(vacío — solo hace de `tests/` un paquete importable, mismo patrón que `visitacampo/authn/tests/__init__.py`.)

`server/tests/test_wait_for_db.py`:

```python
"""Tests de `server/wait_for_db.py` (Tarea 2 del plan de despliegue del demo).

Sin red real: `connect` se inyecta como función falsa, así que estos tests
corren en cualquier entorno, con o sin PostgreSQL alcanzable — mismo criterio
que el resto de la suite (`server/visitacampo/**/tests/`), que evita
dependencias de red salvo cuando prueban explícitamente contra Postgres.
"""

from __future__ import annotations

import unittest

from wait_for_db import wait_for_postgres

_CONNINFO: dict[str, object] = {
    "dbname": "visitacampo",
    "user": "visitacampo",
    "password": "x",
    "host": "db",
    "port": "5432",
}


class _FakeOperationalError(Exception):
    pass


class WaitForPostgresTests(unittest.TestCase):
    def test_returns_true_immediately_when_the_first_connection_attempt_succeeds(self) -> None:
        calls: list[dict[str, object]] = []

        class _Conn:
            def __enter__(self) -> "_Conn":
                return self

            def __exit__(self, *exc_info: object) -> None:
                return None

        def fake_connect(**kwargs: object) -> _Conn:
            calls.append(kwargs)
            return _Conn()

        result = wait_for_postgres(
            _CONNINFO,
            timeout_seconds=5,
            retry_interval_seconds=0,
            connect=fake_connect,
            operational_error=_FakeOperationalError,
        )

        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["dbname"], "visitacampo")

    def test_retries_after_a_connection_failure_and_then_succeeds(self) -> None:
        attempts = {"count": 0}

        class _Conn:
            def __enter__(self) -> "_Conn":
                return self

            def __exit__(self, *exc_info: object) -> None:
                return None

        def flaky_connect(**kwargs: object) -> _Conn:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise _FakeOperationalError("not ready yet")
            return _Conn()

        result = wait_for_postgres(
            _CONNINFO,
            timeout_seconds=5,
            retry_interval_seconds=0,
            connect=flaky_connect,
            operational_error=_FakeOperationalError,
        )

        self.assertTrue(result)
        self.assertEqual(attempts["count"], 3)

    def test_returns_false_once_the_timeout_is_exceeded(self) -> None:
        def always_fails(**kwargs: object) -> object:
            raise _FakeOperationalError("still not ready")

        result = wait_for_postgres(
            _CONNINFO,
            timeout_seconds=0,
            retry_interval_seconds=0,
            connect=always_fails,
            operational_error=_FakeOperationalError,
        )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar el test y verificar que falla por import**

```bash
cd server && python -m pytest tests/test_wait_for_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'wait_for_db'`.

- [ ] **Step 3: Implementar `server/wait_for_db.py`**

```python
"""Espera a que PostgreSQL acepte conexiones antes de que el contenedor siga.

Adaptado de `qfieldcloud/docker-app/wait_for_services.py` (mismo patrón de
reintento con timeout), con dos diferencias deliberadas:

1. Usa `psycopg` v3 (el driver que declara `server/requirements.txt`), no
   `psycopg2` — VisitaCampo nunca instaló `psycopg2`.
2. `connect`/`operational_error` son inyectables: `entrypoint.sh` los deja en
   su valor por defecto (`psycopg.connect`/`psycopg.OperationalError`), pero
   `server/tests/test_wait_for_db.py` los sustituye por dobles sin red — el
   mismo principio de "dominio puro, transporte inyectado" que
   `visitacampo/attachments/services.py` aplica con su parámetro `store`.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from time import monotonic, sleep
from typing import Any

import psycopg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("wait_for_db")

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_INTERVAL_SECONDS = 2.0


def wait_for_postgres(
    conninfo: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    retry_interval_seconds: float = DEFAULT_RETRY_INTERVAL_SECONDS,
    connect: Callable[..., Any] = psycopg.connect,
    operational_error: type[Exception] = psycopg.OperationalError,
) -> bool:
    """`True` en cuanto una conexión tiene éxito; `False` si se agota `timeout_seconds`."""
    deadline = monotonic() + timeout_seconds
    while True:
        try:
            with connect(**conninfo):
                logger.info("PostgreSQL listo.")
                return True
        except operational_error as exc:
            if monotonic() >= deadline:
                logger.error("PostgreSQL no respondio en %s segundos: %s", timeout_seconds, exc)
                return False
            logger.info(
                "PostgreSQL no responde todavia (%s). Reintentando en %ss...",
                exc,
                retry_interval_seconds,
            )
            sleep(retry_interval_seconds)


def _conninfo_from_env() -> dict[str, Any]:
    return {
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "host": os.environ["POSTGRES_HOST"],
        "port": os.environ["POSTGRES_PORT"],
        "connect_timeout": int(DEFAULT_TIMEOUT_SECONDS),
    }


if __name__ == "__main__":
    if not wait_for_postgres(_conninfo_from_env()):
        sys.exit(1)
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

```bash
cd server && python -m pytest tests/test_wait_for_db.py -v
```

Expected: 3 tests, `PASSED`.

- [ ] **Step 5: Reemplazar `server/entrypoint.sh` por la versión completa**

```sh
#!/bin/sh
# VisitaCampo — entrypoint del contenedor `api`. Adaptado del patrón de
# `qfieldcloud/docker-app/entrypoint.sh` ("espera a servicios, luego exec"),
# con las dos tareas adicionales que qfieldcloud reparte en su CI/imagen y
# que aquí hacen falta en el propio arranque: migrar y recolectar estáticos.
set -eu

python wait_for_db.py

python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
```

- [ ] **Step 6: Verificar el ciclo completo contra un Postgres real vía compose**

```bash
cd infra
cp -n .env.example .env
docker compose up -d db
docker compose build api
docker compose up -d api
sleep 3
curl -sf http://localhost:8000/v1/healthz
docker compose logs api | tail -n 30
```

Expected: `curl` imprime `{"status":"ok"}`; los logs de `api` muestran `PostgreSQL listo.` seguido de la salida de `migrate` (tablas creadas) y de `collectstatic` (archivos copiados a `staticfiles/`), sin trazas de error. Si Docker no está disponible, marcar como pendiente.

- [ ] **Step 7: Commit**

```bash
git add server/wait_for_db.py server/entrypoint.sh server/tests/__init__.py server/tests/test_wait_for_db.py
git commit -m "infra: wait for postgres, migrate and collectstatic in the api entrypoint"
```

---

### Task 3: Completar `config/settings/prod.py` (RD-15 + estáticos)

**Files:**
- Modify: `server/config/settings/prod.py` (añade después de la línea 27)
- Test: `server/visitacampo/core/tests/test_prod_settings.py` (nuevo)

**Interfaces:**
- Consumes: `config/settings/base.py::BASE_DIR` (ya existente), `os.environ["DJANGO_ALLOWED_HOSTS"]` (ya leída en `prod.py`, ahora también usada indirectamente por `SECURE_SSL_REDIRECT`/HSTS).
- Produces: `config.settings.prod.STORAGES`, `STATIC_ROOT`, `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`, `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` — leídos por `manage.py check --deploy` (ya usado como gate en CI/`ci_local.sh`) y por Django en producción cuando `DJANGO_SETTINGS_MODULE=config.settings.prod` (activado por la Tarea 1).

- [ ] **Step 1: Escribir el test que falla (los settings de producción no existen todavía)**

`server/visitacampo/core/tests/test_prod_settings.py`:

```python
"""Verifica que `config.settings.prod` completa el endurecimiento de RD-15
(cabeceras de proxy confiable detrás de Caddy) y de estáticos (Tarea 3 de
plans/visitacampo-despliegue-demo-implementacion.md). No usa `manage.py
check --deploy` como test (ese comando solo emite warnings, nunca falla) —
en su lugar importa el módulo de settings directamente y comprueba los
valores, que es lo que un `check --deploy` verde depende de que sea cierto.
"""

from __future__ import annotations

import importlib
import unittest


class ProdSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prod = importlib.import_module("config.settings.prod")

    def test_trusts_the_x_forwarded_proto_header_set_by_caddy(self) -> None:
        self.assertEqual(self.prod.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_trusts_the_x_forwarded_host_header_set_by_caddy(self) -> None:
        self.assertTrue(self.prod.USE_X_FORWARDED_HOST)

    def test_redirects_plain_http_to_https(self) -> None:
        self.assertTrue(self.prod.SECURE_SSL_REDIRECT)

    def test_sends_hsts_for_a_full_year_including_subdomains(self) -> None:
        self.assertEqual(self.prod.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(self.prod.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_cookies_are_marked_secure(self) -> None:
        self.assertTrue(self.prod.SESSION_COOKIE_SECURE)
        self.assertTrue(self.prod.CSRF_COOKIE_SECURE)

    def test_static_root_is_set_for_collectstatic(self) -> None:
        self.assertTrue(str(self.prod.STATIC_ROOT).endswith("staticfiles"))

    def test_staticfiles_storage_uses_the_manifest_backend(self) -> None:
        self.assertEqual(
            self.prod.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

```bash
cd server && DJANGO_ALLOWED_HOSTS=localhost python -m pytest visitacampo/core/tests/test_prod_settings.py -v
```

Expected: `AttributeError: module 'config.settings.prod' has no attribute 'SECURE_PROXY_SSL_HEADER'` (o similar, para cada atributo que todavía no existe).

- [ ] **Step 3: Completar `server/config/settings/prod.py`**

Añadir al final del archivo (después de la línea 27, `ALLOWED_HOSTS = [...]`):

```python

# --- RD-15: cabeceras de proxy confiable detrás de Caddy (doc 08 §5, ADR-010) ---
# `infra/Caddyfile` sobrescribe INCONDICIONALMENTE X-Forwarded-Proto/Host/For
# con el valor real de la conexión entrante a Caddy (ver los comentarios de
# ese archivo) — Django puede confiar en estas cabeceras precisamente porque
# Caddy es el único borde de la red que las escribe; un cliente en HTTP plano
# no puede spoofear "https" porque Caddy la reemplaza, nunca la reenvía.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Redirige cualquier request HTTP plano que llegue a Django (no debería
# ocurrir nunca detrás de Caddy, que ya solo expone 80/443 con TLS
# automático, pero es la misma defensa en profundidad que `manage.py check
# --deploy` exige).
SECURE_SSL_REDIRECT = True

# HSTS: un año, incluye subdominios. Coherente con la cabecera que
# `infra/Caddyfile` ya envía a nivel de proxy (`Strict-Transport-Security`) —
# Django la envía también a nivel de aplicación por si algún día el proxy
# cambia sin que alguien recuerde tocar los dos sitios.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookies de sesión/CSRF solo por HTTPS. VisitaCampo autentica la API con
# JWT (ADR-008), no con cookies de sesión, pero Django Admin (back-office de
# fase 1, ADR-004) sí las usa.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# --- Estáticos (Django Admin: CSS/JS de `django.contrib.admin`) ------------
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

```bash
cd server && DJANGO_ALLOWED_HOSTS=localhost python -m pytest visitacampo/core/tests/test_prod_settings.py -v
```

Expected: 7 tests, `PASSED`.

- [ ] **Step 5: Verificar `manage.py check --deploy` contra `prod.py`**

```bash
cd server && DJANGO_ALLOWED_HOSTS=visitas.example.com DJANGO_SECRET_KEY=una-clave-de-prueba-suficientemente-larga python manage.py check --deploy --settings=config.settings.prod
```

Expected: ninguno de los warnings `security.W004` (HSTS), `security.W008` (SSL redirect), `security.W012` (session cookie secure) ni `security.W016` (CSRF cookie secure) aparece en la salida (pueden quedar otros warnings no relacionados con esta tarea, p. ej. sobre `SECRET_KEY` si se usa una de prueba — no son parte del alcance de esta tarea).

- [ ] **Step 6: Ejecutar la suite completa del backend para confirmar que nada se rompió**

```bash
cd server && ruff check . && mypy --config-file pyproject.toml . && pytest .
```

Expected: `ruff` y `mypy` sin errores; `pytest` en verde (553+ tests previos + los 7 nuevos de esta tarea + los 3 de la Tarea 2).

- [ ] **Step 7: Commit**

```bash
git add server/config/settings/prod.py server/visitacampo/core/tests/test_prod_settings.py
git commit -m "settings: harden config/settings/prod.py for RD-15 and static files"
```

---

### Task 4: `django-axes` — rate limiting de login

**Files:**
- Modify: `server/requirements.txt` (añade `django-axes==7.0.2`)
- Modify: `server/config/settings/base.py` (import `timedelta`, `INSTALLED_APPS`, `MIDDLEWARE`, nuevo bloque `AUTHENTICATION_BACKENDS`/`AXES_*`)
- Test: `server/visitacampo/authn/tests/test_axes_lockout.py` (nuevo)

**Interfaces:**
- Consumes: `django.contrib.auth.authenticate()` (ya invocado por `visitacampo/authn/services.py::log_in`, línea 250, sin cambios) — `axes.backends.AxesBackend` se antepone en `AUTHENTICATION_BACKENDS` y se activa transparentemente en esa misma llamada, sin tocar `authn/services.py`.
- Produces: tras `AXES_FAILURE_LIMIT` (5) intentos fallidos de login del mismo `(username, ip_address)`, cualquier intento posterior — incluso con la contraseña correcta — sigue devolviendo `401 invalid_credentials` (mismo cuerpo genérico de `InvalidCredentials`, sin código nuevo) hasta que pase `AXES_COOLOFF_TIME` (30 min) o el operador libere el bloqueo desde Django Admin (`axes.models.AccessAttempt`, registrado automáticamente por el paquete).

- [ ] **Step 1: Escribir el test que falla (axes no está instalado todavía)**

`server/visitacampo/authn/tests/test_axes_lockout.py`:

```python
"""`django-axes` — rate limiting de login (Tarea 4 de
plans/visitacampo-despliegue-demo-implementacion.md, ADR-004). Reemplaza la
construcción de rate limiting a mano: `authn/services.py::log_in` no cambia
una línea, `axes.backends.AxesBackend` se antepone en
`AUTHENTICATION_BACKENDS` y se activa dentro de la misma llamada a
`django.contrib.auth.authenticate()` que ya existía.
"""

from __future__ import annotations

import unittest

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase

from visitacampo.core.tests.dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

from .factories import PASSWORD, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LoginLockoutTests(TestCase):
    def setUp(self) -> None:
        # `axes` guarda los intentos fallidos en el cache 'default' (locmem,
        # ADR-006: sin Redis) — sin limpiarlo, el contador de una prueba
        # anterior en el mismo proceso de test contaminaría esta.
        cache.clear()
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()

    def _login(self, password: str):
        return post_json(
            self.client,
            "/v1/auth/login",
            {
                "username": "agente",
                "password": password,
                "device_info": {
                    "device_id": "11111111-1111-1111-1111-111111111111",
                    "platform": "android",
                    "app_version": "0.1.0",
                },
            },
        )

    def test_after_the_failure_limit_even_the_correct_password_is_rejected(self) -> None:
        limit = settings.AXES_FAILURE_LIMIT
        for _ in range(limit):
            response = self._login("contraseña-incorrecta")
            self.assertEqual(response.status_code, 401)

        locked_out_response = self._login(PASSWORD)

        self.assertEqual(locked_out_response.status_code, 401)
        self.assertEqual(locked_out_response.json()["code"], "invalid_credentials")

    def test_below_the_failure_limit_the_correct_password_still_succeeds(self) -> None:
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")

        response = self._login(PASSWORD)

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_successful_login_resets_the_failure_counter(self) -> None:
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")
        self.assertEqual(self._login(PASSWORD).status_code, 200)

        # Tras el éxito (AXES_RESET_ON_SUCCESS=True), el contador vuelve a
        # cero: (limit - 1) fallos más siguen sin bloquear la cuenta.
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")

        self.assertEqual(self._login(PASSWORD).status_code, 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

```bash
cd server && python -m pytest visitacampo/authn/tests/test_axes_lockout.py -v
```

Expected: falla en la primera aserción — sin `axes` instalado, `AUTHENTICATION_BACKENDS` no tiene rate limiting, así que el 6º intento con la contraseña correcta devuelve `200`, no `401`. (`settings.AXES_FAILURE_LIMIT` también falla con `AttributeError` hasta el Step 3.)

- [ ] **Step 3: Añadir la dependencia**

Añadir al final de `server/requirements.txt`:

```

# ADR-004: rate limiting de login (Paso de endurecimiento del demo — ver
# plans/visitacampo-despliegue-demo-implementacion.md). Se instala y
# configura la librería en vez de escribir rate limiting a mano: el login
# (visitacampo/authn/services.py::log_in) ya llama a
# django.contrib.auth.authenticate(), el mismo hook que axes necesita.
django-axes==7.0.2
```

Instalar en el entorno local de verificación:

```bash
pip install -r server/requirements.txt
```

- [ ] **Step 4: Configurar `django-axes` en `server/config/settings/base.py`**

Modificar el import del principio del archivo (línea 10-11):

```python
import os
from datetime import timedelta
from pathlib import Path
```

Añadir `"axes"` al final de `INSTALLED_APPS` (después de `"visitacampo.authn.apps.AuthnConfig",`, línea 46):

```python
    "visitacampo.authn.apps.AuthnConfig",
    # 3rd-party — rate limiting de login (ADR-004). Reutiliza el hook de
    # `django.contrib.auth.authenticate()` que `authn/services.py::log_in`
    # ya invoca; no requiere tocar la lógica de negocio.
    "axes",
]
```

Añadir `axes.middleware.AxesMiddleware` al final de `MIDDLEWARE` (después de `"django.middleware.clickjacking.XFrameOptionsMiddleware",`, línea 64):

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # django-axes exige ser el último middleware de la lista (documentación
    # oficial del paquete).
    "axes.middleware.AxesMiddleware",
]
```

Añadir, después del bloque `PASSWORD_HASHERS` (línea 165, antes de `# --- Autenticación JWT ---`):

```python
# --- django-axes: rate limiting de login (ADR-004) --------------------------
# Bloque adaptado de qfieldcloud/docker-app/qfieldcloud/settings.py (líneas
# ~72-77 AUTHENTICATION_BACKENDS, ~140 INSTALLED_APPS, ~169 MIDDLEWARE,
# ~588-602 AXES_*). `authn/services.py::log_in` no cambia: sigue llamando a
# `django.contrib.auth.authenticate()` tal cual; `AxesBackend`, antepuesto
# aquí, es quien decide si esa llamada debe siquiera intentar validar la
# contraseña o devolver "bloqueado" sin mirarla.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Intentos fallidos permitidos antes de bloquear. Igual que qfieldcloud.
AXES_FAILURE_LIMIT = 5
# Bloquea por la combinación (username, IP): un atacante con muchas IPs no
# agota el límite de una cuenta ajena tan rápido como con solo IP, y un
# usuario legítimo detrás de una IP compartida no bloquea a sus compañeros.
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
# Ventana de inactividad tras la que los intentos fallidos antiguos se
# olvidan solos.
AXES_COOLOFF_TIME = lambda _request: timedelta(minutes=30)  # noqa: E731
# Un login correcto reinicia el contador de fallos de esa cuenta/IP.
AXES_RESET_ON_SUCCESS = True
```

- [ ] **Step 5: Aplicar las migraciones de `axes`**

```bash
cd server && python manage.py migrate axes
```

Expected: crea las tablas `axes_accessattempt`, `axes_accesslog`, `axes_accessfailurelog` (o el subconjunto que la versión instalada declare) sin error.

- [ ] **Step 6: Ejecutar el test y verificar que pasa**

```bash
cd server && python -m pytest visitacampo/authn/tests/test_axes_lockout.py -v
```

Expected: 3 tests, `PASSED`.

- [ ] **Step 7: Ejecutar la suite completa del backend para confirmar que nada se rompió**

```bash
cd server && ruff check . && mypy --config-file pyproject.toml . && pytest .
```

Expected: verde. En particular, `visitacampo/authn/tests/test_login.py` (`LoginFailureTests`, que hace varios logins fallidos seguidos) debe seguir pasando — ninguno de sus tests individuales alcanza `AXES_FAILURE_LIMIT` (5) intentos fallidos del mismo `(username, ip_address)` dentro de un mismo test, así que `axes` no los bloquea. Si algún test de `test_login.py` empezara a fallar por esto, la causa sería fuga de estado del cache de `axes` entre tests de ese módulo — solucionarlo añadiendo `cache.clear()` en su `setUp`, no reduciendo `AXES_FAILURE_LIMIT`.

- [ ] **Step 8: Commit**

```bash
git add server/requirements.txt server/config/settings/base.py server/visitacampo/authn/tests/test_axes_lockout.py
git commit -m "auth: install and configure django-axes for login rate limiting"
```

---

## Track Móvil

### Task 5: `main.dart` real + pantalla de login

**Files:**
- Create: `app/lib/core/app_dependencies.dart`
- Create: `app/lib/core/providers.dart`
- Create: `app/lib/core/device/device_id_store.dart`
- Create: `app/lib/core/constants.dart`
- Create: `app/lib/features/auth/login_screen.dart`
- Create: `app/lib/features/home/home_screen.dart`
- Modify: `app/lib/main.dart` (reemplaza el demo de `flutter create` completo)
- Modify: `app/pubspec.yaml` (añade `flutter_riverpod` ya presente — sin cambios de dependencias en esta tarea)
- Modify: `app/test/widget_test.dart` (el test del contador ya no aplica)
- Test: `app/test/core/device/device_id_store_test.dart` (nuevo)
- Test: `app/test/features/auth/login_screen_test.dart` (nuevo)

**Interfaces:**
- Consumes: `AuthController` (`app/lib/core/auth/auth_controller.dart`, constructor `AuthController({required transport, required tokenStore, required deviceId, clock})`, método `Future<AuthResult<UserProfile>> login({required username, required password, required deviceInfo})`), `AuthApiClient implements AuthTransport` (`app/lib/data/http/api_client.dart`), `AuthInterceptor` (`app/lib/data/http/auth_interceptor.dart`, constructor `{required dio, required controller}`), `SecureTokenStore`/`EncryptionKeyStore` (`app/lib/core/auth/token_store.dart`, `app/lib/data/local/encryption_key_store.dart`), `AppDatabase.open`/`createEncryptedExecutor` (`app/lib/data/local/database.dart`), `DeviceInfo`/`AuthResult`/`AuthError` (`app/lib/core/auth/auth_models.dart`, `auth_result.dart`).
- Produces: `AppDependencies` (campos `database: AppDatabase`, `dio: Dio`, `authController: AuthController`, `deviceId: String`) y `Future<AppDependencies> AppDependencies.bootstrap()` — la Tarea 6 y la Tarea 7 **modifican** esta clase para añadir sus propios campos (`locationSource`/`photoCapture` en la Tarea 6; `syncEngine`/`attachmentUploadService` en la Tarea 7), así que su forma debe quedar fácil de extender. `appDependenciesProvider` (`Provider<AppDependencies>`, `app/lib/core/providers.dart`) — consumido por `LoginScreen`, `HomeScreen` y las pantallas de las Tareas 6/7. `HomeScreen` — destino tras un login exitoso; la Tarea 6 le añade un botón "Nueva visita" y la Tarea 7 un botón "Sincronizar".

- [ ] **Step 1: Escribir el test de `DeviceIdStore` (falla porque la clase no existe)**

`app/test/core/device/device_id_store_test.dart`:

```dart
// `DeviceIdStore` — identificador estable de este dispositivo (Tarea 5 del
// plan de despliegue del demo). Mismo patrón que `EncryptionKeyStore`
// (`data/local/encryption_key_store.dart`, Paso 11): genera una vez, persiste
// en el keychain/keystore del sistema, y en adelante siempre devuelve el
// mismo valor. Se prueba con un `FlutterSecureStorage` en memoria (fake),
// mismo criterio que `test/core/auth/fake_token_store.dart`.
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/device/device_id_store.dart';

class _InMemorySecureStorage {
  final Map<String, String> _values = {};

  Future<String?> read({required String key}) async => _values[key];

  Future<void> write({required String key, required String value}) async {
    _values[key] = value;
  }
}

void main() {
  group('DeviceIdStore.getOrCreateId', () {
    test('genera un id la primera vez y lo persiste', () async {
      final storage = _InMemorySecureStorage();
      final store = DeviceIdStore(read: storage.read, write: storage.write);

      final id = await store.getOrCreateId();

      expect(id, isNotEmpty);
      expect(await storage.read(key: 'visitacampo.device_id.v1'), id);
    });

    test('en llamadas posteriores devuelve siempre el mismo id', () async {
      final storage = _InMemorySecureStorage();
      final store = DeviceIdStore(read: storage.read, write: storage.write);

      final first = await store.getOrCreateId();
      final second = await store.getOrCreateId();

      expect(second, first);
    });
  });
}
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/core/device/device_id_store_test.dart
```

Expected: `Error: Error when reading 'lib/core/device/device_id_store.dart': No such file or directory`.

- [ ] **Step 3: Implementar `DeviceIdStore`**

`app/lib/core/device/device_id_store.dart`:

```dart
// `DeviceIdStore` — identificador estable de este dispositivo, generado una
// vez y persistido en el keychain/keystore del sistema (mismo mecanismo que
// `EncryptionKeyStore`, `data/local/encryption_key_store.dart`, Paso 11).
// `AuthController.deviceId` y `SyncEngine.deviceId` (Pasos 12/13) esperan un
// identificador estable entre sesiones de la app; sin esto, cada arranque
// generaría un dispositivo nuevo en el servidor (`authn/services.py::
// _register_device`).
//
// `read`/`write` se inyectan (en vez de recibir un `FlutterSecureStorage`
// concreto) para poder probar la lógica de "generar solo la primera vez" sin
// el method channel del plugin, mismo motivo que documenta
// `test/core/auth/fake_token_store.dart` para `TokenStore`.
import 'dart:math';

class DeviceIdStore {
  DeviceIdStore({
    required Future<String?> Function({required String key}) read,
    required Future<void> Function({required String key, required String value}) write,
    Random? random,
  }) : _read = read,
       _write = write,
       _random = random ?? Random.secure();

  static const _storageKey = 'visitacampo.device_id.v1';

  final Future<String?> Function({required String key}) _read;
  final Future<void> Function({required String key, required String value}) _write;
  final Random _random;

  Future<String> getOrCreateId() async {
    final existing = await _read(key: _storageKey);
    if (existing != null) {
      return existing;
    }
    final generated = _generateUuidV4();
    await _write(key: _storageKey, value: generated);
    return generated;
  }

  /// UUIDv4 sin dependencias externas: el paquete `uuid` (ya en
  /// `pubspec.yaml`) genera v4/v7, pero un identificador de dispositivo no
  /// necesita ser ordenable en el tiempo (a diferencia del `op_uuid` v7 del
  /// outbox) y evitar la dependencia aquí mantiene esta clase mínima.
  String _generateUuidV4() {
    final bytes = List<int>.generate(16, (_) => _random.nextInt(256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    String hex(int start, int end) =>
        bytes.sublist(start, end).map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex(0, 4)}-${hex(4, 6)}-${hex(6, 8)}-${hex(8, 10)}-${hex(10, 16)}';
  }
}
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/core/device/device_id_store_test.dart
```

Expected: 2 tests, `+2: All tests passed!`.

- [ ] **Step 5: Escribir el test de `LoginScreen` (falla porque no existe)**

`app/test/features/auth/login_screen_test.dart`:

```dart
// `LoginScreen` — Tarea 5 del plan de despliegue del demo. Reutiliza
// `FakeAuthTransport`/`FakeTokenStore` ya construidos para `AuthController`
// (Paso 13) en vez de levantar red real, mismo criterio que
// `test/core/auth/auth_controller_test.dart`.
import 'package:dio/dio.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/app_dependencies.dart';
import 'package:visitacampo/core/auth/auth_controller.dart';
import 'package:visitacampo/core/auth/auth_models.dart';
import 'package:visitacampo/core/auth/auth_result.dart';
import 'package:visitacampo/core/providers.dart';
import 'package:visitacampo/data/local/database.dart';
import 'package:visitacampo/features/auth/login_screen.dart';
import 'package:visitacampo/features/home/home_screen.dart';

import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';

TokenPair _tokenPair() => TokenPair(
  accessToken: 'access-1',
  tokenType: 'Bearer',
  expiresIn: 900,
  refreshToken: 'refresh-1',
  refreshExpiresIn: 5184000,
  serverTime: DateTime.utc(2026, 8, 6, 12),
);

const _user = UserProfile(id: 'u1', username: 'agente1', orgId: 'org1', roles: ['agent']);

AppDependencies _buildDependencies(FakeAuthTransport transport) {
  final controller = AuthController(
    transport: transport,
    tokenStore: FakeTokenStore(),
    deviceId: 'device-1',
  );
  return AppDependencies(
    database: AppDatabase.forTesting(NativeDatabase.memory()),
    dio: Dio(),
    authController: controller,
    deviceId: 'device-1',
  );
}

void main() {
  testWidgets('login exitoso navega a HomeScreen', (tester) async {
    final transport = FakeAuthTransport()
      ..loginResponses.add(AuthResult.ok(LoginResponse(tokens: _tokenPair(), user: _user)));
    final dependencies = _buildDependencies(transport);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: const MaterialApp(home: LoginScreen()),
      ),
    );

    await tester.enterText(find.byKey(const Key('login_username_field')), 'agente1');
    await tester.enterText(find.byKey(const Key('login_password_field')), 'secret');
    await tester.tap(find.byKey(const Key('login_submit_button')));
    await tester.pumpAndSettle();

    expect(find.byType(HomeScreen), findsOneWidget);
  });

  testWidgets('login fallido muestra un mensaje y se queda en LoginScreen', (tester) async {
    final transport = FakeAuthTransport()
      ..loginResponses.add(const AuthResult.err(AuthError.invalidCredentials()));
    final dependencies = _buildDependencies(transport);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: const MaterialApp(home: LoginScreen()),
      ),
    );

    await tester.enterText(find.byKey(const Key('login_username_field')), 'agente1');
    await tester.enterText(find.byKey(const Key('login_password_field')), 'mal');
    await tester.tap(find.byKey(const Key('login_submit_button')));
    await tester.pumpAndSettle();

    expect(find.byType(HomeScreen), findsNothing);
    expect(find.text('Usuario o contraseña incorrectos.'), findsOneWidget);
  });
}
```

- [ ] **Step 6: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/features/auth/login_screen_test.dart
```

Expected: falla en tiempo de análisis — `lib/core/app_dependencies.dart`, `lib/core/providers.dart`, `lib/features/auth/login_screen.dart` y `lib/features/home/home_screen.dart` no existen todavía.

- [ ] **Step 7: Implementar `app/lib/core/constants.dart`**

```dart
// Constantes de composición de la app (Tarea 5 del plan de despliegue del
// demo). `kAppVersion` va en el `DeviceInfo` del login/registro de
// dispositivo (contrato `DeviceInfo.app_version`) — se hardcodea en vez de
// leerla con `package_info_plus` porque el valor exacto no afecta al
// criterio de éxito del demo y añadir el paquete solo para esto sería
// sobre-construcción (YAGNI).
const String kAppVersion = '0.1.0-demo';

/// URL base de la API, configurable en tiempo de build/ejecución con
/// `flutter run --dart-define=VISITACAMPO_API_BASE_URL=https://tu-dominio`.
/// Por defecto apunta al Caddy local de `infra/docker-compose.override.dev.yml`
/// (HTTPS con certificado autofirmado — ver `infra/Caddyfile`).
const String kApiBaseUrl = String.fromEnvironment(
  'VISITACAMPO_API_BASE_URL',
  defaultValue: 'https://localhost',
);
```

- [ ] **Step 8: Implementar `app/lib/core/app_dependencies.dart`**

```dart
// Composición de dependencias de la app (Tarea 5 del plan de despliegue del
// demo). Un único objeto en vez de un grafo fino de providers de Riverpod:
// `AuthController`/`SyncEngine` no son observables (no son `ChangeNotifier`/
// `StateNotifier`, ver sus propios archivos), así que un grafo reactivo
// alrededor de ellos no aportaría nada — la navegación de esta app es
// imperativa (`Navigator.pushReplacement` tras el resultado de `login()`).
//
// Las Tareas 6 y 7 **añaden campos a esta misma clase** (`locationSource`/
// `photoCapture` en la 6; `syncEngine`/`attachmentUploadService` en la 7) en
// vez de crear objetos de dependencias paralelos.
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import 'auth/auth_controller.dart';
import 'auth/token_store.dart';
import 'constants.dart';
import 'device/device_id_store.dart';
import '../data/http/api_client.dart';
import '../data/http/auth_interceptor.dart';
import '../data/local/database.dart';
import '../data/local/encryption_key_store.dart';

class AppDependencies {
  AppDependencies({
    required this.database,
    required this.dio,
    required this.authController,
    required this.deviceId,
  });

  final AppDatabase database;
  final Dio dio;
  final AuthController authController;
  final String deviceId;

  static Future<AppDependencies> bootstrap() async {
    const secureStorage = FlutterSecureStorage();

    final deviceIdStore = DeviceIdStore(read: secureStorage.read, write: secureStorage.write);
    final deviceId = await deviceIdStore.getOrCreateId();

    final encryptionKeyStore = EncryptionKeyStore(secureStorage);
    final encryptionKey = await encryptionKeyStore.getOrCreateKey();
    final documentsDir = await getApplicationDocumentsDirectory();
    final dbFile = File(p.join(documentsDir.path, 'visitacampo.db'));
    final database = AppDatabase.open(
      createEncryptedExecutor(file: dbFile, encryptionKey: encryptionKey),
    );

    final dio = Dio(BaseOptions(baseUrl: kApiBaseUrl));
    final tokenStore = SecureTokenStore(secureStorage);
    final authController = AuthController(
      transport: AuthApiClient(dio),
      tokenStore: tokenStore,
      deviceId: deviceId,
    );
    dio.interceptors.add(AuthInterceptor(dio: dio, controller: authController));

    return AppDependencies(
      database: database,
      dio: dio,
      authController: authController,
      deviceId: deviceId,
    );
  }
}
```

- [ ] **Step 9: Implementar `app/lib/core/providers.dart`**

```dart
// Único punto de acceso a `AppDependencies` desde los widgets (Tarea 5 del
// plan de despliegue del demo). `main()` sobrescribe este provider con la
// instancia real construida por `AppDependencies.bootstrap()`; los tests de
// widgets lo sobrescriben con una instancia armada con fakes (mismo patrón
// en `test/features/auth/login_screen_test.dart`).
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app_dependencies.dart';

final appDependenciesProvider = Provider<AppDependencies>((ref) {
  throw UnimplementedError(
    'appDependenciesProvider debe sobrescribirse en main() (o en el ProviderScope '
    'de un test) con la instancia ya construida por AppDependencies.bootstrap().',
  );
});
```

- [ ] **Step 10: Implementar `app/lib/features/home/home_screen.dart` (placeholder de esta tarea)**

```dart
// Pantalla posterior al login. Esta tarea (5) solo deja el destino de la
// navegación; la Tarea 6 le añade el botón "Nueva visita" y la Tarea 7 el
// botón "Sincronizar".
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dependencies = ref.watch(appDependenciesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('VisitaCampo')),
      body: Center(
        child: Text('Sesión iniciada. Device: ${dependencies.deviceId}'),
      ),
    );
  }
}
```

- [ ] **Step 11: Implementar `app/lib/features/auth/login_screen.dart`**

```dart
// Pantalla de login (Tarea 5 del plan de despliegue del demo). Conecta la
// UI directamente con `AuthController.login()` (Paso 13, ya construido) —
// esta pantalla no valida ni decide nada, solo traduce el formulario a los
// parámetros del contrato y el `AuthResult` a un mensaje o una navegación.
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/auth/auth_models.dart';
import '../../core/auth/auth_result.dart';
import '../../core/constants.dart';
import '../../core/providers.dart';
import '../home/home_screen.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _isSubmitting = false;

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final dependencies = ref.read(appDependenciesProvider);
    setState(() => _isSubmitting = true);

    final platform = defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android';
    final result = await dependencies.authController.login(
      username: _usernameController.text,
      password: _passwordController.text,
      deviceInfo: DeviceInfo(
        deviceId: dependencies.deviceId,
        platform: platform,
        appVersion: kAppVersion,
      ),
    );

    if (!mounted) return;
    setState(() => _isSubmitting = false);

    result.when(
      ok: (_) {
        Navigator.of(context).pushReplacement(
          MaterialPageRoute<void>(builder: (_) => const HomeScreen()),
        );
      },
      err: (error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(_messageFor(error))),
        );
      },
    );
  }

  String _messageFor(AuthError error) => error.when(
    network: (message) => 'No se pudo conectar con el servidor.',
    invalidCredentials: () => 'Usuario o contraseña incorrectos.',
    unauthorized: () => 'Sesión no autorizada.',
    unexpectedStatus: (statusCode, body) => 'Error inesperado del servidor ($statusCode).',
    malformedResponse: (details) => 'Respuesta del servidor no reconocida.',
  );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('VisitaCampo — Iniciar sesión')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextField(
              key: const Key('login_username_field'),
              controller: _usernameController,
              decoration: const InputDecoration(labelText: 'Usuario'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('login_password_field'),
              controller: _passwordController,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Contraseña'),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              key: const Key('login_submit_button'),
              onPressed: _isSubmitting ? null : _submit,
              child: _isSubmitting
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Entrar'),
            ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 12: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/features/auth/login_screen_test.dart
```

Expected: 2 tests, `+2: All tests passed!`.

- [ ] **Step 13: Reemplazar `app/lib/main.dart`**

```dart
// VisitaCampo — punto de entrada real (Tarea 5 del plan de despliegue del
// demo). Reemplaza el demo de `flutter create` (contador) sin modificar.
// Sin restauración de sesión al reiniciar: la app siempre arranca en
// LoginScreen (decisión de diseño de esta tarea — ver el plan).
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/app_dependencies.dart';
import 'core/providers.dart';
import 'features/auth/login_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final dependencies = await AppDependencies.bootstrap();
  runApp(
    ProviderScope(
      overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
      child: const App(),
    ),
  );
}

class App extends StatelessWidget {
  const App({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'VisitaCampo',
      theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.green)),
      home: const LoginScreen(),
    );
  }
}
```

- [ ] **Step 14: Borrar el test del contador, que ya no aplica**

`app/test/widget_test.dart` — reemplazar todo el contenido (el widget `MyApp` del demo de `flutter create` ya no existe):

```dart
// El `MyApp` de este archivo era el demo del contador de `flutter create`.
// `app/lib/main.dart` ya no lo expone (Tarea 5 del plan de despliegue del
// demo) — la cobertura de `App`/`LoginScreen` vive en
// `test/features/auth/login_screen_test.dart`.
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('placeholder: la cobertura de App vive en features/auth/login_screen_test.dart', () {
    expect(true, isTrue);
  });
}
```

- [ ] **Step 15: Ejecutar `flutter analyze` y toda la suite móvil**

```bash
cd app && flutter analyze && flutter test
```

Expected: `flutter analyze`: "No issues found!"; `flutter test`: todos los tests en verde (los 51 previos + los nuevos de esta tarea), sin regresiones.

- [ ] **Step 16: Commit**

```bash
git add app/lib/main.dart app/lib/core/app_dependencies.dart app/lib/core/providers.dart app/lib/core/device/device_id_store.dart app/lib/core/constants.dart app/lib/features/auth/login_screen.dart app/lib/features/home/home_screen.dart app/test/widget_test.dart app/test/core/device/device_id_store_test.dart app/test/features/auth/login_screen_test.dart
git commit -m "app: replace the flutter create demo with a real login flow"
```

---

### Task 6: Pantalla de captura (formulario fijo + GPS + foto)

**Files:**
- Create: `app/lib/core/location/location_source.dart`
- Create: `app/lib/data/location/geolocator_location_source.dart`
- Create: `app/lib/core/capture/photo_capture.dart`
- Create: `app/lib/data/capture/image_picker_photo_capture.dart`
- Create: `app/lib/data/local/attachment_queue_repository.dart`
- Create: `app/lib/features/capture/capture_screen.dart`
- Modify: `app/lib/core/app_dependencies.dart` (añade `locationSource`/`photoCapture`)
- Modify: `app/lib/features/home/home_screen.dart` (añade el botón "Nueva visita")
- Modify: `app/pubspec.yaml` (añade `geolocator`, `image_picker`, `crypto`)
- Modify: `app/android/app/src/main/AndroidManifest.xml` (permisos de ubicación/cámara)
- Modify: `app/ios/Runner/Info.plist` (descripciones de uso de ubicación/cámara)
- Test: `app/test/data/local/attachment_queue_repository_test.dart` (nuevo)
- Test: `app/test/features/capture/capture_screen_test.dart` (nuevo)

**Interfaces:**
- Consumes: `AppDatabase`/`Visits`/`OutboxOps`/`AttachmentQueue` (`app/lib/data/local/database.dart`, `tables.dart`, ya existentes), `AppDependencies` (Tarea 5, se le añaden campos), `HomeScreen` (Tarea 5, se le añade un botón).
- Produces: `LocationSource` (interfaz, método `Future<LocationFix?> getCurrentFix()`, `LocationFix{lat, lon, accuracyM, altitudeM}`), `PhotoCapture` (interfaz, método `Future<CapturedPhoto?> takePhoto()`, `CapturedPhoto{file: File, sha256: String, sizeBytes: int, mime: String}`), `AttachmentQueueRepository.enqueue({required sha256, required localPath, required DateTime now})` — la Tarea 7 los consume para saber qué adjuntos subir. `CaptureScreen` — pantalla añadida a la navegación desde `HomeScreen`.

- [ ] **Step 1: Añadir las dependencias nuevas a `app/pubspec.yaml`**

En la sección `dependencies:` (después de `meta: ^1.18.0`, línea 46):

```yaml
  geolocator: ^14.0.2
  image_picker: ^1.1.2
  crypto: ^3.0.6
```

```bash
cd app && flutter pub get
```

- [ ] **Step 2: Permisos de Android**

Modificar `app/android/app/src/main/AndroidManifest.xml`: añadir, antes de la etiqueta `<application`(línea 2):

```xml
    <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
    <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>
    <uses-permission android:name="android.permission.CAMERA"/>
```

- [ ] **Step 3: Permisos de iOS**

Modificar `app/ios/Runner/Info.plist`: añadir, antes de `</dict>` de cierre (línea 69):

```xml
	<key>NSLocationWhenInUseUsageDescription</key>
	<string>VisitaCampo necesita tu ubicación para registrar dónde se capturó cada visita.</string>
	<key>NSCameraUsageDescription</key>
	<string>VisitaCampo necesita la cámara para adjuntar una foto a cada visita.</string>
```

- [ ] **Step 4: Escribir el test de `AttachmentQueueRepository` (falla porque no existe)**

`app/test/data/local/attachment_queue_repository_test.dart`:

```dart
// `AttachmentQueueRepository` — Tarea 6 del plan de despliegue del demo.
// Envuelve la tabla `AttachmentQueue` (`data/local/tables.dart`, Paso 11) que
// hasta ahora nadie escribía: la Tarea 7 (subida manual) leerá estas filas.
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/data/local/attachment_queue_repository.dart';
import 'package:visitacampo/data/local/database.dart';

void main() {
  late AppDatabase db;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() => db.close());

  test('enqueue inserta una fila en estado pending', () async {
    final repository = AttachmentQueueRepository(db);
    final now = DateTime.utc(2026, 8, 6);

    await repository.enqueue(sha256: 'a' * 64, localPath: '/tmp/foto.jpg', now: now);

    final rows = await db.select(db.attachmentQueue).get();
    expect(rows, hasLength(1));
    expect(rows.single.sha256, 'a' * 64);
    expect(rows.single.localPath, '/tmp/foto.jpg');
    expect(rows.single.state, 'pending');
    expect(rows.single.createdAt, now);
  });

  test('enqueue es idempotente para el mismo sha256 (mismo contenido, no duplica)', () async {
    final repository = AttachmentQueueRepository(db);
    final now = DateTime.utc(2026, 8, 6);

    await repository.enqueue(sha256: 'b' * 64, localPath: '/tmp/1.jpg', now: now);
    await repository.enqueue(sha256: 'b' * 64, localPath: '/tmp/2.jpg', now: now);

    final rows = await db.select(db.attachmentQueue).get();
    expect(rows, hasLength(1));
  });
}
```

- [ ] **Step 5: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/data/local/attachment_queue_repository_test.dart
```

Expected: `Error: Error when reading 'lib/data/local/attachment_queue_repository.dart': No such file or directory`.

- [ ] **Step 6: Implementar `AttachmentQueueRepository`**

`app/lib/data/local/attachment_queue_repository.dart`:

```dart
// Envuelve la tabla `AttachmentQueue` (`data/local/tables.dart`, Paso 11:
// "Adjuntos pendientes de subir"). Nadie la escribía todavía — esta tarea
// (6, captura de una foto) es su primer escritor; la Tarea 7 (subida
// manual) es su primer lector.
import 'package:drift/drift.dart';

import 'database.dart';

class AttachmentQueueRepository {
  const AttachmentQueueRepository(this._db);

  final AppDatabase _db;

  /// Encola un adjunto pendiente de subir. Idempotente por `sha256` (clave
  /// primaria de `AttachmentQueue`, igual que `Blob.sha256` en el servidor):
  /// el mismo contenido nunca se encola dos veces, aunque el usuario tome la
  /// "misma" foto en dos capturas distintas.
  Future<void> enqueue({
    required String sha256,
    required String localPath,
    required DateTime now,
  }) {
    return _db
        .into(_db.attachmentQueue)
        .insertOnConflictUpdate(
          AttachmentQueueCompanion.insert(
            sha256: sha256,
            localPath: localPath,
            state: 'pending',
            createdAt: now,
          ),
        );
  }
}
```

- [ ] **Step 7: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/data/local/attachment_queue_repository_test.dart
```

Expected: 2 tests, `+2: All tests passed!`.

- [ ] **Step 8: Implementar la interfaz `LocationSource` y su implementación real**

`app/lib/core/location/location_source.dart`:

```dart
// `LocationSource` — la mitad de GPS que `CaptureScreen` necesita (Tarea 6
// del plan de despliegue del demo). Interfaz + implementación real
// separadas, mismo patrón que `AuthTransport`/`SyncTransport`: los tests de
// `CaptureScreen` usan un fake, sin permisos de plataforma reales.
class LocationFix {
  const LocationFix({
    required this.lat,
    required this.lon,
    required this.accuracyM,
    this.altitudeM,
  });

  final double lat;
  final double lon;
  final double accuracyM;
  final double? altitudeM;
}

abstract class LocationSource {
  /// `null` si el usuario niega el permiso o el servicio de ubicación del
  /// sistema está apagado — `CaptureScreen` debe seguir permitiendo guardar
  /// la visita sin ubicación (`Visit.lat`/`lon` son opcionales en el
  /// contrato, `core/models/visit.py`).
  Future<LocationFix?> getCurrentFix();
}
```

`app/lib/data/location/geolocator_location_source.dart`:

```dart
// Implementación real de `LocationSource` sobre el paquete `geolocator`.
// Sin test unitario propio (envoltorio delgado de un plugin de plataforma,
// no probable con `flutter test` sin un dispositivo/emulador real) — mismo
// criterio que `SecureTokenStore` (`core/auth/token_store.dart`), que
// tampoco tiene test propio y se prueba a través de un fake en el dominio
// que sí lo consume (aquí, `CaptureScreen`).
import 'package:geolocator/geolocator.dart';

import '../../core/location/location_source.dart';

class GeolocatorLocationSource implements LocationSource {
  const GeolocatorLocationSource();

  @override
  Future<LocationFix?> getCurrentFix() async {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) return null;

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return null;
    }

    final position = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
    );
    return LocationFix(
      lat: position.latitude,
      lon: position.longitude,
      accuracyM: position.accuracy,
      altitudeM: position.altitude,
    );
  }
}
```

- [ ] **Step 9: Implementar la interfaz `PhotoCapture` y su implementación real**

`app/lib/core/capture/photo_capture.dart`:

```dart
// `PhotoCapture` — la mitad de cámara que `CaptureScreen` necesita (Tarea 6
// del plan de despliegue del demo). `sha256` se calcula aquí, en el
// dispositivo, porque es lo que el contrato de adjuntos exige como
// identidad del contenido (`attachments/schemas.py::Sha256Path`,
// `Blob.sha256` en el servidor) — la Tarea 7 lo usa tal cual en
// `presign`/`commit`, nunca lo recalcula.
import 'dart:io';

class CapturedPhoto {
  const CapturedPhoto({
    required this.file,
    required this.sha256,
    required this.sizeBytes,
    required this.mime,
  });

  final File file;
  final String sha256;
  final int sizeBytes;
  final String mime;
}

abstract class PhotoCapture {
  /// `null` si el usuario cancela la captura.
  Future<CapturedPhoto?> takePhoto();
}
```

`app/lib/data/capture/image_picker_photo_capture.dart`:

```dart
// Implementación real de `PhotoCapture` sobre `image_picker`. Sin test
// unitario propio, mismo motivo que `GeolocatorLocationSource` (envoltorio
// delgado de un plugin de plataforma) — se prueba a través de un fake en
// `test/features/capture/capture_screen_test.dart`.
import 'package:crypto/crypto.dart';
import 'package:image_picker/image_picker.dart';

import '../../core/capture/photo_capture.dart';

class ImagePickerPhotoCapture implements PhotoCapture {
  ImagePickerPhotoCapture({ImagePicker? picker}) : _picker = picker ?? ImagePicker();

  final ImagePicker _picker;

  @override
  Future<CapturedPhoto?> takePhoto() async {
    final xFile = await _picker.pickImage(source: ImageSource.camera);
    if (xFile == null) return null;

    final bytes = await xFile.readAsBytes();
    final digest = sha256.convert(bytes);
    return CapturedPhoto(
      file: File(xFile.path),
      sha256: digest.toString(),
      sizeBytes: bytes.length,
      // La cámara de `image_picker` siempre entrega JPEG en Android/iOS —
      // coincide con la allow-list del servidor (`core/models/base.py::
      // MimeType`, `attachments/schemas.py::Mime`).
      mime: 'image/jpeg',
    );
  }
}
```

(`import 'dart:io';` ya lo trae `photo_capture.dart` para el tipo `File` usado por `image_picker_photo_capture.dart`.)

- [ ] **Step 10: Extender `AppDependencies` con `locationSource`/`photoCapture`**

Modificar `app/lib/core/app_dependencies.dart`:

Añadir imports:

```dart
import 'capture/photo_capture.dart';
import 'location/location_source.dart';
import '../data/capture/image_picker_photo_capture.dart';
import '../data/location/geolocator_location_source.dart';
```

Modificar el constructor y los campos:

```dart
class AppDependencies {
  AppDependencies({
    required this.database,
    required this.dio,
    required this.authController,
    required this.deviceId,
    LocationSource? locationSource,
    PhotoCapture? photoCapture,
  }) : locationSource = locationSource ?? const GeolocatorLocationSource(),
       photoCapture = photoCapture ?? ImagePickerPhotoCapture();

  final AppDatabase database;
  final Dio dio;
  final AuthController authController;
  final String deviceId;
  final LocationSource locationSource;
  final PhotoCapture photoCapture;
```

(El resto de la clase —`bootstrap()`— no cambia: los valores por defecto de `locationSource`/`photoCapture` cubren el caso real; los tests que necesiten fakes los pasan explícitamente al constructor.)

- [ ] **Step 11: Escribir el test de `CaptureScreen` (falla porque no existe)**

`app/test/features/capture/capture_screen_test.dart`:

```dart
// `CaptureScreen` — Tarea 6 del plan de despliegue del demo. Formulario
// FIJO (no el renderer dinámico del Paso 21, diferido) + GPS + una foto,
// persistidos en `visits`/`outbox_ops`/`attachment_queue` a través de
// `SyncEngine.enqueueVisitOp` (Paso 12) y `AttachmentQueueRepository`
// (Tarea 6, Step 6) — ambos ya construidos antes de esta pantalla.
import 'package:dio/dio.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/app_dependencies.dart';
import 'package:visitacampo/core/auth/auth_controller.dart';
import 'package:visitacampo/core/capture/photo_capture.dart';
import 'package:visitacampo/core/location/location_source.dart';
import 'package:visitacampo/core/providers.dart';
import 'package:visitacampo/data/local/database.dart';
import 'package:visitacampo/features/capture/capture_screen.dart';

import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';

class _FakeLocationSource implements LocationSource {
  const _FakeLocationSource(this.fix);
  final LocationFix? fix;

  @override
  Future<LocationFix?> getCurrentFix() async => fix;
}

class _FakePhotoCapture implements PhotoCapture {
  _FakePhotoCapture(this.photo);
  final CapturedPhoto? photo;

  @override
  Future<CapturedPhoto?> takePhoto() async => photo;
}

void main() {
  late AppDatabase db;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() => db.close());

  AppDependencies buildDependencies({LocationFix? fix, CapturedPhoto? photo}) {
    final controller = AuthController(
      transport: FakeAuthTransport(),
      tokenStore: FakeTokenStore(),
      deviceId: 'device-1',
    );
    return AppDependencies(
      database: db,
      dio: Dio(),
      authController: controller,
      deviceId: 'device-1',
      locationSource: _FakeLocationSource(fix),
      photoCapture: _FakePhotoCapture(photo),
    );
  }

  testWidgets('guardar una visita con GPS y foto encola outbox + attachment_queue', (
    tester,
  ) async {
    final photoFile = File.fromUri(Uri.file('/tmp/foto-demo.jpg'));
    final dependencies = buildDependencies(
      fix: const LocationFix(lat: 4.6, lon: -74.1, accuracyM: 5),
      photo: CapturedPhoto(
        file: photoFile,
        sha256: 'c' * 64,
        sizeBytes: 1024,
        mime: 'image/jpeg',
      ),
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: const MaterialApp(home: CaptureScreen()),
      ),
    );

    await tester.enterText(
      find.byKey(const Key('capture_template_field')),
      '11111111-1111-1111-1111-111111111111',
    );
    await tester.enterText(find.byKey(const Key('capture_altura_field')), '12');
    await tester.enterText(find.byKey(const Key('capture_notas_field')), 'Todo en orden');

    await tester.tap(find.byKey(const Key('capture_get_location_button')));
    await tester.pumpAndSettle();
    expect(find.textContaining('4.6'), findsOneWidget);

    await tester.tap(find.byKey(const Key('capture_take_photo_button')));
    await tester.pumpAndSettle();
    expect(find.textContaining('foto-demo.jpg'), findsOneWidget);

    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();

    final visits = await db.select(db.visits).get();
    expect(visits, hasLength(1));
    expect(visits.single.lat, 4.6);
    expect(visits.single.templateVersionId, '11111111-1111-1111-1111-111111111111');

    final outboxOps = await db.select(db.outboxOps).get();
    expect(outboxOps, hasLength(1));
    expect(outboxOps.single.opType, 'create');
    expect(outboxOps.single.entity, 'visit');

    final attachmentQueue = await db.select(db.attachmentQueue).get();
    expect(attachmentQueue, hasLength(1));
    expect(attachmentQueue.single.sha256, 'c' * 64);
    expect(attachmentQueue.single.state, 'pending');
  });

  testWidgets('guardar una visita sin foto no toca attachment_queue', (tester) async {
    final dependencies = buildDependencies(fix: null, photo: null);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: const MaterialApp(home: CaptureScreen()),
      ),
    );

    await tester.enterText(
      find.byKey(const Key('capture_template_field')),
      '11111111-1111-1111-1111-111111111111',
    );
    await tester.enterText(find.byKey(const Key('capture_altura_field')), '9');

    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();

    final visits = await db.select(db.visits).get();
    expect(visits, hasLength(1));
    expect(visits.single.lat, isNull);

    final attachmentQueue = await db.select(db.attachmentQueue).get();
    expect(attachmentQueue, isEmpty);
  });
}
```

- [ ] **Step 12: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/features/capture/capture_screen_test.dart
```

Expected: `Error: Error when reading 'lib/features/capture/capture_screen.dart': No such file or directory`.

- [ ] **Step 13: Implementar `CaptureScreen`**

`app/lib/features/capture/capture_screen.dart`:

```dart
// Pantalla de captura — Tarea 6 del plan de despliegue del demo. Formulario
// FIJO (altura_cm + notas, no el renderer dinámico de JSON Schema del Paso
// 21, diferido), GPS y una foto, persistidos en una sola transacción local
// vía `SyncEngine.enqueueVisitOp` (Paso 12) + `AttachmentQueueRepository`
// (Step 6 de esta tarea). No sincroniza nada por red — eso es la Tarea 7.
//
// `template_version_id` se pega a mano (Decisión de diseño 7 del plan): el
// renderer dinámico y el flujo de asignación de plantillas están fuera de
// alcance; el operador del demo siembra una `FormTemplateVersion` vía Django
// Admin antes de la prueba y pega aquí su UUID.
import 'dart:convert';

import 'package:drift/drift.dart' show Value;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../core/auth/auth_state.dart';
import '../../core/capture/photo_capture.dart';
import '../../core/location/location_source.dart';
import '../../core/providers.dart';
import '../../data/local/attachment_queue_repository.dart';
import '../../data/local/database.dart';

class CaptureScreen extends ConsumerStatefulWidget {
  const CaptureScreen({super.key});

  @override
  ConsumerState<CaptureScreen> createState() => _CaptureScreenState();
}

class _CaptureScreenState extends ConsumerState<CaptureScreen> {
  final _templateController = TextEditingController();
  final _alturaController = TextEditingController();
  final _notasController = TextEditingController();

  LocationFix? _fix;
  CapturedPhoto? _photo;
  bool _isSaving = false;

  @override
  void dispose() {
    _templateController.dispose();
    _alturaController.dispose();
    _notasController.dispose();
    super.dispose();
  }

  Future<void> _getLocation() async {
    final dependencies = ref.read(appDependenciesProvider);
    final fix = await dependencies.locationSource.getCurrentFix();
    if (!mounted) return;
    setState(() => _fix = fix);
  }

  Future<void> _takePhoto() async {
    final dependencies = ref.read(appDependenciesProvider);
    final photo = await dependencies.photoCapture.takePhoto();
    if (!mounted) return;
    setState(() => _photo = photo);
  }

  Future<void> _save() async {
    final dependencies = ref.read(appDependenciesProvider);
    final authState = dependencies.authController.state;
    if (authState is! Authenticated) return;

    setState(() => _isSaving = true);

    final visitId = const Uuid().v7();
    final now = DateTime.now().toUtc();
    final capturedAt = now.toIso8601String();
    final answers = <String, dynamic>{
      'altura_cm': int.tryParse(_alturaController.text) ?? 0,
      'notas': _notasController.text,
    };

    final payload = <String, dynamic>{
      'id': visitId,
      'template_version_id': _templateController.text,
      'answers': answers,
      'captured_at': capturedAt,
      'device_id': dependencies.deviceId,
      if (_fix != null) 'lat': _fix!.lat,
      if (_fix != null) 'lon': _fix!.lon,
      if (_fix != null) 'accuracy_m': _fix!.accuracyM,
      if (_fix != null && _fix!.altitudeM != null) 'altitude_m': _fix!.altitudeM,
    };

    final visitCompanion = VisitsCompanion.insert(
      id: visitId,
      orgId: authState.user.orgId,
      // `INITIAL_VERSION` del servidor (`core/models/base.py`) es 1: el
      // cliente crea la fila local con la misma convención, aunque el
      // servidor es quien fija el valor autoritativo tras el push.
      version: 1,
      changeSeq: const Value(null),
      deletedAt: const Value(null),
      templateVersionId: _templateController.text,
      answersJson: jsonEncode(answers),
      lat: Value(_fix?.lat),
      lon: Value(_fix?.lon),
      accuracyM: Value(_fix?.accuracyM),
      altitudeM: Value(_fix?.altitudeM),
      capturedAt: now,
      deviceId: dependencies.deviceId,
      createdBy: authState.user.id,
    );

    // `SyncEngine` no forma parte de `AppDependencies` todavía (lo añade la
    // Tarea 7) — esta tarea solo necesita `AppDatabase.saveVisitWithOutboxOp`
    // directamente, con la misma forma de `OutboxOpsCompanion` que
    // `SyncEngine.enqueueVisitOp` construye (`core/sync/sync_engine.dart`).
    await dependencies.database.saveVisitWithOutboxOp(
      visit: visitCompanion,
      outboxOp: OutboxOpsCompanion.insert(
        opUuid: const Uuid().v7(),
        entity: 'visit',
        entityId: visitId,
        opType: 'create',
        baseVersion: const Value(null),
        payloadJson: jsonEncode(payload),
        state: 'pending',
        createdAt: now,
      ),
    );

    final photo = _photo;
    if (photo != null) {
      await AttachmentQueueRepository(
        dependencies.database,
      ).enqueue(sha256: photo.sha256, localPath: photo.file.path, now: now);
    }

    if (!mounted) return;
    setState(() => _isSaving = false);
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Visita guardada.')));
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Nueva visita')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              key: const Key('capture_template_field'),
              controller: _templateController,
              decoration: const InputDecoration(labelText: 'ID de plantilla (UUID)'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('capture_altura_field'),
              controller: _alturaController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Altura (cm)'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('capture_notas_field'),
              controller: _notasController,
              decoration: const InputDecoration(labelText: 'Notas'),
            ),
            const SizedBox(height: 24),
            ElevatedButton(
              key: const Key('capture_get_location_button'),
              onPressed: _getLocation,
              child: const Text('Obtener ubicación'),
            ),
            if (_fix != null) Text('lat: ${_fix!.lat}, lon: ${_fix!.lon}'),
            const SizedBox(height: 12),
            ElevatedButton(
              key: const Key('capture_take_photo_button'),
              onPressed: _takePhoto,
              child: const Text('Tomar foto'),
            ),
            if (_photo != null) Text('Foto: ${_photo!.file.path.split('/').last}'),
            const SizedBox(height: 24),
            ElevatedButton(
              key: const Key('capture_save_button'),
              onPressed: _isSaving ? null : _save,
              child: _isSaving
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Guardar visita'),
            ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 14: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/features/capture/capture_screen_test.dart
```

Expected: 2 tests, `+2: All tests passed!`.

- [ ] **Step 15: Añadir el botón "Nueva visita" a `HomeScreen`**

Modificar `app/lib/features/home/home_screen.dart` completo:

```dart
// Pantalla posterior al login. La Tarea 6 añade "Nueva visita"; la Tarea 7
// añade "Sincronizar".
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../capture/capture_screen.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dependencies = ref.watch(appDependenciesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('VisitaCampo')),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text('Sesión iniciada. Device: ${dependencies.deviceId}'),
            const SizedBox(height: 24),
            ElevatedButton(
              key: const Key('home_new_visit_button'),
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const CaptureScreen()),
                );
              },
              child: const Text('Nueva visita'),
            ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 16: Ejecutar `flutter analyze` y toda la suite móvil**

```bash
cd app && flutter analyze && flutter test
```

Expected: "No issues found!" y todos los tests en verde.

- [ ] **Step 17: Commit**

```bash
git add app/pubspec.yaml app/pubspec.lock app/android/app/src/main/AndroidManifest.xml app/ios/Runner/Info.plist app/lib/core/location app/lib/data/location app/lib/core/capture app/lib/data/capture app/lib/data/local/attachment_queue_repository.dart app/lib/features/capture app/lib/core/app_dependencies.dart app/lib/features/home/home_screen.dart app/test/data/local/attachment_queue_repository_test.dart app/test/features/capture/capture_screen_test.dart
git commit -m "app: add the fixed-form capture screen with GPS and one photo"
```

---

### Task 7: Sincronización manual (push/pull + subida de adjunto)

**Files:**
- Create: `app/lib/data/http/sync_api_client.dart`
- Create: `app/lib/core/attachments/attachment_result.dart`
- Create: `app/lib/core/attachments/attachment_transport.dart`
- Create: `app/lib/core/attachments/attachment_upload_service.dart`
- Create: `app/lib/data/http/attachment_api_client.dart`
- Modify: `app/lib/core/app_dependencies.dart` (añade `syncEngine`/`attachmentUploadService`)
- Modify: `app/lib/features/home/home_screen.dart` (añade el botón "Sincronizar")
- Test: `app/test/data/http/sync_api_client_test.dart` (nuevo)
- Test: `app/test/core/attachments/attachment_upload_service_test.dart` (nuevo)
- Test: `app/test/core/attachments/fake_attachment_transport.dart` (nuevo, fake reusado por el test anterior)
- Test: `app/test/features/home/home_screen_test.dart` (nuevo)

**Interfaces:**
- Consumes: `SyncEngine` (`app/lib/core/sync/sync_engine.dart`, constructor `{required db, required transport, required deviceId}`, métodos `Future<DrainReport> drainOutbox()`, `Future<Result<bool>> pull({int? limit})`), `SyncTransport` (`app/lib/core/sync/sync_transport.dart`), `AttachmentQueue`/`AttachmentQueueCompanion` (`app/lib/data/local/tables.dart`, `database.dart`), `AppDependencies` (Tareas 5/6, se le añaden campos), `AuthController.accessToken` (`app/lib/core/auth/auth_controller.dart`, ya existente).
- Produces: `SyncApiClient implements SyncTransport` — cliente Dio real para `/v1/sync/*` (no existía ningún cliente Dio de sync hasta esta tarea). `AttachmentTransport` (interfaz) + `AttachmentApiClient` (real) + `AttachmentUploadService.drainPending()` — orquesta `presign → PUT → commit` para cada fila `pending` de `AttachmentQueue`, marcándola `uploaded`/`failed`. El botón "Sincronizar" de `HomeScreen` llama, en orden, a `syncEngine.drainOutbox()`, `attachmentUploadService.drainPending()` y `syncEngine.pull()`.

- [ ] **Step 1: Escribir el test de `SyncApiClient` (falla porque no existe)**

`app/test/data/http/sync_api_client_test.dart`:

```dart
// `SyncApiClient` — Tarea 7 del plan de despliegue del demo. Primer cliente
// Dio real de `/v1/sync/*` (hasta ahora `SyncEngine`, Paso 12, solo se había
// probado contra `FakeSyncTransport`). Se prueba con `FakeHttpClientAdapter`
// (`test/core/auth/fake_http_client_adapter.dart`, ya construido en el Paso
// 13 para `AuthApiClient`/`AuthInterceptor`): recibe un `FakeHttpHandler`
// (`FutureOr<ResponseBody> Function(RequestOptions options)`) en el
// constructor y expone `requests: List<RequestOptions>` — no una cola de
// respuestas programable ni `lastRequestOptions` (esa forma pertenece a otro
// fake; esta es la real). `jsonResponseBody(data, statusCode)` construye la
// respuesta con el `Content-Type` que el transformer de Dio necesita.
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/sync/result.dart';
import 'package:visitacampo/core/sync/sync_transport.dart';
import 'package:visitacampo/data/http/sync_api_client.dart';

import '../../core/auth/fake_http_client_adapter.dart';

void main() {
  test('push envia el lote y parsea la respuesta', () async {
    final adapter = FakeHttpClientAdapter(
      (options) => jsonResponseBody({
        'schema_version': 1,
        'server_time': '2026-08-06T12:00:00.000Z',
        'results': [
          {'op_uuid': 'a0000000-0000-7000-8000-000000000000', 'entity': 'visit', 'status': 'applied'},
        ],
      }, 200),
    );
    final dio = Dio(BaseOptions(baseUrl: 'https://api.test'))..httpClientAdapter = adapter;
    final client = SyncApiClient(dio: dio, accessTokenProvider: () => 'access-1');

    final result = await client.push(
      const PushRequestBody(
        schemaVersion: 1,
        deviceId: 'device-1',
        batchUuid: 'b0000000-0000-7000-8000-000000000000',
        ops: [],
      ),
    );

    expect(result.isOk, isTrue);
    final response = (result as Ok<PushResponse>).value;
    expect(response.results.single.status, 'applied');
    expect(adapter.requests.single.headers['Authorization'], 'Bearer access-1');
    expect(adapter.requests.single.path, '/v1/sync/push');
  });

  test('pull con 409 cursor_expired se traduce a SyncError.cursorExpired', () async {
    final adapter = FakeHttpClientAdapter(
      (options) => jsonResponseBody({'code': 'cursor_expired', 'message': 'expired'}, 409),
    );
    final dio = Dio(BaseOptions(baseUrl: 'https://api.test'))..httpClientAdapter = adapter;
    final client = SyncApiClient(dio: dio, accessTokenProvider: () => 'access-1');

    final result = await client.pull(cursor: 'c1:00000000000000000000');

    expect(result, isA<Err<PullResponse>>());
    expect((result as Err<PullResponse>).error, isA<CursorExpiredError>());
  });
}
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/data/http/sync_api_client_test.dart
```

Expected: falla en análisis — `lib/data/http/sync_api_client.dart` no existe.

- [ ] **Step 3: Implementar `SyncApiClient`**

`app/lib/data/http/sync_api_client.dart`:

```dart
// `SyncApiClient` — implementación Dio real de `SyncTransport` (Tarea 7 del
// plan de despliegue del demo). Primer cliente real de `/v1/sync/*`; hasta
// ahora `SyncEngine` (Paso 12) solo se había ejercitado contra
// `FakeSyncTransport` en tests. Mismo patrón que `AuthApiClient`
// (`data/http/api_client.dart`, Paso 13): la cabecera `Authorization` se
// fija por request, no vía un interceptor global, porque no todos los
// clientes que comparten el `Dio` de la app necesitan token (el propio
// `AuthApiClient.login`/`refresh` no lo llevan).
import 'package:dio/dio.dart';

import '../../core/sync/result.dart';
import '../../core/sync/sync_transport.dart';

class SyncApiClient implements SyncTransport {
  SyncApiClient({required Dio dio, required String Function() accessTokenProvider})
    : _dio = dio,
      _accessTokenProvider = accessTokenProvider;

  final Dio _dio;
  final String Function() _accessTokenProvider;

  Options get _authOptions =>
      Options(headers: {'Authorization': 'Bearer ${_accessTokenProvider()}'});

  @override
  Future<Result<PushResponse>> push(PushRequestBody body) async {
    try {
      final response = await _dio.post<Map<String, dynamic>>(
        '/v1/sync/push',
        data: body.toJson(),
        options: _authOptions,
      );
      return parseJson(PushResponse.fromJson, response.data ?? const <String, dynamic>{});
    } on DioException catch (e) {
      return Result.err(_mapError(e));
    }
  }

  @override
  Future<Result<PullResponse>> pull({required String cursor, int? limit}) async {
    try {
      final response = await _dio.get<Map<String, dynamic>>(
        '/v1/sync/pull',
        queryParameters: {'cursor': cursor, if (limit != null) 'limit': limit},
        options: _authOptions,
      );
      return parseJson(PullResponse.fromJson, response.data ?? const <String, dynamic>{});
    } on DioException catch (e) {
      return Result.err(_mapError(e));
    }
  }

  @override
  Future<Result<BootstrapResponse>> bootstrap() async {
    try {
      final response = await _dio.get<Map<String, dynamic>>(
        '/v1/sync/bootstrap',
        options: _authOptions,
      );
      return parseJson(BootstrapResponse.fromJson, response.data ?? const <String, dynamic>{});
    } on DioException catch (e) {
      return Result.err(_mapError(e));
    }
  }

  SyncError _mapError(DioException e) {
    final status = e.response?.statusCode;
    if (status == null) {
      return SyncError.network(e.message ?? e.type.name);
    }
    final body = e.response?.data;
    if (status == 409 && body is Map<String, dynamic> && body['code'] == 'cursor_expired') {
      return const SyncError.cursorExpired();
    }
    return SyncError.unexpectedStatus(status, body == null ? '' : body.toString());
  }
}
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/data/http/sync_api_client_test.dart
```

Expected: 2 tests, `+2: All tests passed!`.

- [ ] **Step 5: Escribir el fake de `AttachmentTransport` y el test de `AttachmentUploadService` (fallan porque nada de esto existe)**

`app/test/core/attachments/fake_attachment_transport.dart`:

```dart
// Fake de `AttachmentTransport` — mismo patrón que `FakeSyncTransport`
// (`test/core/sync/fake_sync_transport.dart`): cola de respuestas
// programables, sin red real.
import 'package:visitacampo/core/attachments/attachment_result.dart';
import 'package:visitacampo/core/attachments/attachment_transport.dart';

class FakeAttachmentTransport implements AttachmentTransport {
  final List<AttachmentResult<PresignData>> presignResponses = [];
  final List<AttachmentResult<void>> uploadResponses = [];
  final List<AttachmentResult<void>> commitResponses = [];

  final List<String> presignedShas = [];
  final List<String> committedShas = [];

  @override
  Future<AttachmentResult<PresignData>> presign({
    required String sha256,
    required int size,
    required String mime,
  }) async {
    presignedShas.add(sha256);
    if (presignResponses.isEmpty) {
      throw StateError('FakeAttachmentTransport.presign() sin respuesta programada.');
    }
    return presignResponses.removeAt(0);
  }

  @override
  Future<AttachmentResult<void>> upload({
    required String uploadUrl,
    required Map<String, String> headers,
    required List<int> bytes,
  }) async {
    if (uploadResponses.isEmpty) {
      throw StateError('FakeAttachmentTransport.upload() sin respuesta programada.');
    }
    return uploadResponses.removeAt(0);
  }

  @override
  Future<AttachmentResult<void>> commit({
    required String sha256,
    required int size,
    required String mime,
  }) async {
    committedShas.add(sha256);
    if (commitResponses.isEmpty) {
      throw StateError('FakeAttachmentTransport.commit() sin respuesta programada.');
    }
    return commitResponses.removeAt(0);
  }
}
```

`app/test/core/attachments/attachment_upload_service_test.dart`:

```dart
// `AttachmentUploadService` — Tarea 7 del plan de despliegue del demo.
// Orquesta presign→PUT→commit para cada fila `pending` de `AttachmentQueue`
// (`data/local/tables.dart`, Paso 11, sin lector hasta ahora). Sin red real:
// `FakeAttachmentTransport` programa las tres respuestas.
import 'dart:io';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/attachments/attachment_result.dart';
import 'package:visitacampo/core/attachments/attachment_transport.dart';
import 'package:visitacampo/core/attachments/attachment_upload_service.dart';
import 'package:visitacampo/data/local/database.dart';

import 'fake_attachment_transport.dart';

void main() {
  late AppDatabase db;
  late FakeAttachmentTransport transport;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeAttachmentTransport();
  });

  tearDown(() => db.close());

  Future<List<int>> fakeReadBytes(String path) async => List<int>.filled(10, 1);

  test('sube y confirma un adjunto pending, y lo marca uploaded', () async {
    await db
        .into(db.attachmentQueue)
        .insert(
          AttachmentQueueCompanion.insert(
            sha256: 'd' * 64,
            localPath: '/tmp/foto.jpg',
            state: 'pending',
            createdAt: DateTime.utc(2026, 8, 6),
          ),
        );
    transport.presignResponses.add(
      AttachmentResult.ok(
        const PresignData(alreadyPresent: false, uploadUrl: 'https://blob.test/put', requiredHeaders: {}),
      ),
    );
    transport.uploadResponses.add(const AttachmentResult.ok(null));
    transport.commitResponses.add(const AttachmentResult.ok(null));

    final service = AttachmentUploadService(db: db, transport: transport, readBytes: fakeReadBytes);
    final report = await service.drainPending();

    expect(report.uploaded, 1);
    expect(report.failed, 0);
    final row = await (db.select(db.attachmentQueue)
          ..where((t) => t.sha256.equals('d' * 64)))
        .getSingle();
    expect(row.state, 'uploaded');
  });

  test('already_present salta la subida y confirma directo', () async {
    await db
        .into(db.attachmentQueue)
        .insert(
          AttachmentQueueCompanion.insert(
            sha256: 'e' * 64,
            localPath: '/tmp/foto2.jpg',
            state: 'pending',
            createdAt: DateTime.utc(2026, 8, 6),
          ),
        );
    transport.presignResponses.add(
      AttachmentResult.ok(const PresignData(alreadyPresent: true, uploadUrl: null, requiredHeaders: null)),
    );
    transport.commitResponses.add(const AttachmentResult.ok(null));

    final service = AttachmentUploadService(db: db, transport: transport, readBytes: fakeReadBytes);
    final report = await service.drainPending();

    expect(report.uploaded, 1);
    expect(transport.uploadResponses, isEmpty, reason: 'upload() no debe programarse ni consumirse');
  });

  test('un fallo de presign marca la fila failed sin detener el resto del lote', () async {
    await db
        .into(db.attachmentQueue)
        .insert(
          AttachmentQueueCompanion.insert(
            sha256: 'f' * 64,
            localPath: '/tmp/foto3.jpg',
            state: 'pending',
            createdAt: DateTime.utc(2026, 8, 6),
          ),
        );
    transport.presignResponses.add(
      const AttachmentResult.err(AttachmentError.network('sin conexion')),
    );

    final service = AttachmentUploadService(db: db, transport: transport, readBytes: fakeReadBytes);
    final report = await service.drainPending();

    expect(report.uploaded, 0);
    expect(report.failed, 1);
    final row = await (db.select(db.attachmentQueue)
          ..where((t) => t.sha256.equals('f' * 64)))
        .getSingle();
    expect(row.state, 'failed');
  });
}
```

- [ ] **Step 6: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/core/attachments/attachment_upload_service_test.dart
```

Expected: falla en análisis — ninguno de `lib/core/attachments/*.dart` existe todavía.

- [ ] **Step 7: Implementar `AttachmentResult`/`AttachmentError`**

`app/lib/core/attachments/attachment_result.dart`:

```dart
// `AttachmentResult<T>`/`AttachmentError` — mismo patrón que
// `AuthResult`/`SyncError` (Tarea 7 del plan de despliegue del demo):
// ninguna llamada a `/v1/attachments/*` produce una excepción no capturada.
import 'package:freezed_annotation/freezed_annotation.dart';

part 'attachment_result.freezed.dart';

@freezed
sealed class AttachmentResult<T> with _$AttachmentResult<T> {
  const factory AttachmentResult.ok(T value) = AttachmentOk<T>;
  const factory AttachmentResult.err(AttachmentError error) = AttachmentErr<T>;
}

extension AttachmentResultIsOk<T> on AttachmentResult<T> {
  bool get isOk => this is AttachmentOk<T>;
}

@freezed
sealed class AttachmentError with _$AttachmentError {
  const factory AttachmentError.network(String message) = AttachmentNetworkError;
  const factory AttachmentError.unexpectedStatus(int statusCode, String body) =
      AttachmentUnexpectedStatus;
  const factory AttachmentError.malformedResponse(String details) = AttachmentMalformedResponse;
}
```

- [ ] **Step 8: Implementar `AttachmentTransport` + `PresignData`**

`app/lib/core/attachments/attachment_transport.dart`:

```dart
// `AttachmentTransport` — la mitad de red que `AttachmentUploadService`
// necesita (Tarea 7 del plan de despliegue del demo). Implementada por
// `AttachmentApiClient` (Dio real, `data/http/attachment_api_client.dart`) y
// por `FakeAttachmentTransport` en los tests.
import 'attachment_result.dart';

class PresignData {
  const PresignData({required this.alreadyPresent, this.uploadUrl, this.requiredHeaders});

  final bool alreadyPresent;
  final String? uploadUrl;
  final Map<String, String>? requiredHeaders;
}

abstract class AttachmentTransport {
  /// `POST /v1/attachments/presign`. Sin `visit_id` (Decisión de diseño 11
  /// del plan): evita depender de que el `push` de la visita ya haya
  /// terminado en el servidor.
  Future<AttachmentResult<PresignData>> presign({
    required String sha256,
    required int size,
    required String mime,
  });

  /// `PUT` directo a `uploadUrl` (host del object storage, no de la API de
  /// VisitaCampo) con los `headers` que el servidor exigió al firmar.
  Future<AttachmentResult<void>> upload({
    required String uploadUrl,
    required Map<String, String> headers,
    required List<int> bytes,
  });

  /// `POST /v1/attachments/{sha256}/commit`.
  Future<AttachmentResult<void>> commit({
    required String sha256,
    required int size,
    required String mime,
  });
}
```

- [ ] **Step 9: Implementar `AttachmentUploadService`**

`app/lib/core/attachments/attachment_upload_service.dart`:

```dart
// `AttachmentUploadService` — orquesta presign→PUT→commit para cada fila
// `pending` de `AttachmentQueue` (Tarea 7 del plan de despliegue del demo).
// No crea ninguna fila `VisitAttachment` en el servidor (Decisión de diseño
// 6 del plan: `sync/payloads.py::WRITABLE_ENTITIES` solo acepta `visit`) —
// esto sube y confirma el **blob**, nada más.
import 'dart:io';

import 'package:drift/drift.dart';

import '../../data/local/database.dart';
import 'attachment_result.dart';
import 'attachment_transport.dart';

class AttachmentDrainReport {
  const AttachmentDrainReport({required this.uploaded, required this.failed});

  final int uploaded;
  final int failed;
}

class AttachmentUploadService {
  AttachmentUploadService({
    required this.db,
    required this.transport,
    Future<List<int>> Function(String path)? readBytes,
  }) : _readBytes = readBytes ?? _defaultReadBytes;

  final AppDatabase db;
  final AttachmentTransport transport;
  final Future<List<int>> Function(String path) _readBytes;

  static Future<List<int>> _defaultReadBytes(String path) => File(path).readAsBytes();

  Future<AttachmentDrainReport> drainPending() async {
    final pending =
        await (db.select(db.attachmentQueue)..where((t) => t.state.equals('pending'))).get();

    var uploaded = 0;
    var failed = 0;
    for (final row in pending) {
      final ok = await _drainOne(row);
      if (ok) {
        uploaded++;
      } else {
        failed++;
      }
    }
    return AttachmentDrainReport(uploaded: uploaded, failed: failed);
  }

  Future<bool> _drainOne(AttachmentQueueData row) async {
    final bytes = await _readBytes(row.localPath);
    final presignResult = await transport.presign(
      sha256: row.sha256,
      size: bytes.length,
      mime: 'image/jpeg',
    );

    return presignResult.when(
      ok: (presign) async {
        if (!presign.alreadyPresent) {
          final uploadUrl = presign.uploadUrl;
          if (uploadUrl == null) {
            return _markFailed(row.sha256);
          }
          final uploadResult = await transport.upload(
            uploadUrl: uploadUrl,
            headers: presign.requiredHeaders ?? const {},
            bytes: bytes,
          );
          final uploadOk = uploadResult.isOk;
          if (!uploadOk) {
            return _markFailed(row.sha256);
          }
        }
        final commitResult = await transport.commit(
          sha256: row.sha256,
          size: bytes.length,
          mime: 'image/jpeg',
        );
        return commitResult.isOk ? _markUploaded(row.sha256) : _markFailed(row.sha256);
      },
      err: (_) => _markFailed(row.sha256),
    );
  }

  Future<bool> _markUploaded(String sha256) async {
    await (db.update(db.attachmentQueue)..where((t) => t.sha256.equals(sha256))).write(
      const AttachmentQueueCompanion(state: Value('uploaded')),
    );
    return true;
  }

  Future<bool> _markFailed(String sha256) async {
    await (db.update(db.attachmentQueue)..where((t) => t.sha256.equals(sha256))).write(
      const AttachmentQueueCompanion(state: Value('failed')),
    );
    return false;
  }
}
```

- [ ] **Step 10: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/core/attachments/attachment_upload_service_test.dart
```

Expected: 3 tests, `+3: All tests passed!`.

- [ ] **Step 11: Implementar `AttachmentApiClient` (Dio real)**

`app/lib/data/http/attachment_api_client.dart`:

```dart
// `AttachmentApiClient` — implementación Dio real de `AttachmentTransport`
// (Tarea 7 del plan de despliegue del demo). `upload()` usa un `Dio` **sin**
// baseUrl ni cabeceras de autorización: `uploadUrl` es una URL prefirmada de
// un host de object storage (Cloudflare R2/MinIO), que autentica por firma
// en la propia URL, no con el bearer token de la API de VisitaCampo — mismo
// motivo por el que `AuthInterceptor` no debe interceptar esta llamada.
import 'package:dio/dio.dart';

import '../../core/attachments/attachment_result.dart';
import '../../core/attachments/attachment_transport.dart';

class AttachmentApiClient implements AttachmentTransport {
  AttachmentApiClient({required Dio dio, required String Function() accessTokenProvider})
    : _dio = dio,
      _accessTokenProvider = accessTokenProvider,
      _uploadDio = Dio();

  final Dio _dio;
  final Dio _uploadDio;
  final String Function() _accessTokenProvider;

  Options get _authOptions =>
      Options(headers: {'Authorization': 'Bearer ${_accessTokenProvider()}'});

  @override
  Future<AttachmentResult<PresignData>> presign({
    required String sha256,
    required int size,
    required String mime,
  }) async {
    try {
      final response = await _dio.post<Map<String, dynamic>>(
        '/v1/attachments/presign',
        data: {'sha256': sha256, 'size': size, 'mime': mime},
        options: _authOptions,
      );
      final body = response.data ?? const <String, dynamic>{};
      final requiredHeaders = body['required_headers'] as Map<String, dynamic>?;
      return AttachmentResult.ok(
        PresignData(
          alreadyPresent: body['already_present'] as bool? ?? false,
          uploadUrl: body['upload_url'] as String?,
          requiredHeaders: requiredHeaders?.map((key, value) => MapEntry(key, value as String)),
        ),
      );
    } on DioException catch (e) {
      return AttachmentResult.err(_mapError(e));
    }
  }

  @override
  Future<AttachmentResult<void>> upload({
    required String uploadUrl,
    required Map<String, String> headers,
    required List<int> bytes,
  }) async {
    try {
      await _uploadDio.put<void>(
        uploadUrl,
        data: bytes,
        options: Options(headers: headers),
      );
      return const AttachmentResult.ok(null);
    } on DioException catch (e) {
      return AttachmentResult.err(_mapError(e));
    }
  }

  @override
  Future<AttachmentResult<void>> commit({
    required String sha256,
    required int size,
    required String mime,
  }) async {
    try {
      await _dio.post<Map<String, dynamic>>(
        '/v1/attachments/$sha256/commit',
        data: {'size': size, 'mime': mime},
        options: _authOptions,
      );
      return const AttachmentResult.ok(null);
    } on DioException catch (e) {
      return AttachmentResult.err(_mapError(e));
    }
  }

  AttachmentError _mapError(DioException e) {
    final status = e.response?.statusCode;
    if (status == null) {
      return AttachmentError.network(e.message ?? e.type.name);
    }
    final body = e.response?.data;
    return AttachmentError.unexpectedStatus(status, body == null ? '' : body.toString());
  }
}
```

- [ ] **Step 12: Ejecutar `build_runner` para generar `attachment_result.freezed.dart`**

```bash
cd app && dart run build_runner build --delete-conflicting-outputs
```

Expected: termina sin error y genera `lib/core/attachments/attachment_result.freezed.dart`.

- [ ] **Step 13: Extender `AppDependencies` con `syncEngine`/`attachmentUploadService`**

Modificar `app/lib/core/app_dependencies.dart`:

Añadir imports:

```dart
import 'attachments/attachment_upload_service.dart';
import 'sync/sync_engine.dart';
import '../data/http/attachment_api_client.dart';
import '../data/http/sync_api_client.dart';
```

Modificar el constructor, los campos y `bootstrap()`:

```dart
class AppDependencies {
  AppDependencies({
    required this.database,
    required this.dio,
    required this.authController,
    required this.deviceId,
    LocationSource? locationSource,
    PhotoCapture? photoCapture,
    SyncEngine? syncEngine,
    AttachmentUploadService? attachmentUploadService,
  }) : locationSource = locationSource ?? const GeolocatorLocationSource(),
       photoCapture = photoCapture ?? ImagePickerPhotoCapture(),
       syncEngine =
           syncEngine ??
           SyncEngine(
             db: database,
             transport: SyncApiClient(dio: dio, accessTokenProvider: () => authController.accessToken ?? ''),
             deviceId: deviceId,
           ),
       attachmentUploadService =
           attachmentUploadService ??
           AttachmentUploadService(
             db: database,
             transport: AttachmentApiClient(
               dio: dio,
               accessTokenProvider: () => authController.accessToken ?? '',
             ),
           );

  final AppDatabase database;
  final Dio dio;
  final AuthController authController;
  final String deviceId;
  final LocationSource locationSource;
  final PhotoCapture photoCapture;
  final SyncEngine syncEngine;
  final AttachmentUploadService attachmentUploadService;
```

(`bootstrap()` no cambia: los nuevos campos usan sus valores por defecto, construidos a partir de `database`/`dio`/`authController`/`deviceId` ya presentes.)

- [ ] **Step 14: Escribir el test de `HomeScreen` con el botón "Sincronizar" (falla porque el botón no existe)**

`app/test/features/home/home_screen_test.dart`:

```dart
// `HomeScreen` — botón "Sincronizar" (Tarea 7 del plan de despliegue del
// demo). Verifica que tocar el botón llama, en orden, a
// `SyncEngine.drainOutbox()`, `AttachmentUploadService.drainPending()` y
// `SyncEngine.pull()` — sin necesidad de red real, con
// `FakeSyncTransport`/`FakeAttachmentTransport`.
import 'package:dio/dio.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/app_dependencies.dart';
import 'package:visitacampo/core/attachments/attachment_result.dart';
import 'package:visitacampo/core/attachments/attachment_upload_service.dart';
import 'package:visitacampo/core/auth/auth_controller.dart';
import 'package:visitacampo/core/providers.dart';
import 'package:visitacampo/core/sync/result.dart';
import 'package:visitacampo/core/sync/sync_engine.dart';
import 'package:visitacampo/data/local/database.dart';
import 'package:visitacampo/features/home/home_screen.dart';

import '../../core/attachments/fake_attachment_transport.dart';
import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';
import '../../core/sync/fake_sync_transport.dart';

void main() {
  testWidgets('Sincronizar drena el outbox, sube adjuntos pendientes y hace pull', (tester) async {
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(db.close);

    final syncTransport = FakeSyncTransport()
      ..pullResponses.add(
        Result.ok(
          const PullResponse(rows: [], tombstones: [], nextCursor: 'c1:00000000000000000001', hasMore: false),
        ),
      );
    final syncEngine = SyncEngine(db: db, transport: syncTransport, deviceId: 'device-1');

    final attachmentTransport = FakeAttachmentTransport();
    final attachmentUploadService = AttachmentUploadService(
      db: db,
      transport: attachmentTransport,
    );

    final authController = AuthController(
      transport: FakeAuthTransport(),
      tokenStore: FakeTokenStore(),
      deviceId: 'device-1',
    );

    final dependencies = AppDependencies(
      database: db,
      dio: Dio(),
      authController: authController,
      deviceId: 'device-1',
      syncEngine: syncEngine,
      attachmentUploadService: attachmentUploadService,
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: const MaterialApp(home: HomeScreen()),
      ),
    );

    await tester.tap(find.byKey(const Key('home_sync_button')));
    await tester.pumpAndSettle();

    expect(syncTransport.pullCalls, hasLength(1));
    expect(find.textContaining('Sincronización completa'), findsOneWidget);
  });
}
```

- [ ] **Step 15: Ejecutar el test y verificar que falla**

```bash
cd app && flutter test test/features/home/home_screen_test.dart
```

Expected: falla porque `find.byKey(const Key('home_sync_button'))` no encuentra ningún widget.

- [ ] **Step 16: Añadir el botón "Sincronizar" a `HomeScreen`**

Reemplazar `app/lib/features/home/home_screen.dart` completo:

```dart
// Pantalla posterior al login. Botón "Nueva visita" (Tarea 6) + "Sincronizar"
// (Tarea 7 del plan de despliegue del demo): dispara, en orden,
// `drainOutbox()` (push del outbox de visitas), `drainPending()` (subida de
// adjuntos pendientes) y `pull()` (trae cambios del servidor) — sin
// reintento en segundo plano (Paso 18, diferido): todo ocurre en primer
// plano mientras la pantalla muestra un indicador de progreso.
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../capture/capture_screen.dart';

class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  bool _isSyncing = false;

  Future<void> _sync() async {
    final dependencies = ref.read(appDependenciesProvider);
    setState(() => _isSyncing = true);

    final drainReport = await dependencies.syncEngine.drainOutbox();
    final attachmentReport = await dependencies.attachmentUploadService.drainPending();
    await dependencies.syncEngine.pull();

    if (!mounted) return;
    setState(() => _isSyncing = false);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Sincronización completa: ${drainReport.applied} visita(s) aplicadas, '
          '${attachmentReport.uploaded} adjunto(s) subido(s).',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final dependencies = ref.watch(appDependenciesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('VisitaCampo')),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text('Sesión iniciada. Device: ${dependencies.deviceId}'),
            const SizedBox(height: 24),
            ElevatedButton(
              key: const Key('home_new_visit_button'),
              onPressed: () {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(builder: (_) => const CaptureScreen()),
                );
              },
              child: const Text('Nueva visita'),
            ),
            const SizedBox(height: 12),
            ElevatedButton(
              key: const Key('home_sync_button'),
              onPressed: _isSyncing ? null : _sync,
              child: _isSyncing
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Sincronizar'),
            ),
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 17: Ejecutar el test y verificar que pasa**

```bash
cd app && flutter test test/features/home/home_screen_test.dart
```

Expected: 1 test, `+1: All tests passed!`.

- [ ] **Step 18: Ejecutar `flutter analyze` y toda la suite móvil**

```bash
cd app && flutter analyze && flutter test
```

Expected: "No issues found!" y todos los tests en verde (los previos + los de las Tareas 5, 6 y 7), sin regresiones.

- [ ] **Step 19: Commit**

```bash
git add app/lib/data/http/sync_api_client.dart app/lib/core/attachments app/lib/data/http/attachment_api_client.dart app/lib/core/app_dependencies.dart app/lib/features/home/home_screen.dart app/test/data/http/sync_api_client_test.dart app/test/core/attachments app/test/features/home/home_screen_test.dart
git commit -m "app: wire manual sync (push, attachment upload, pull) into HomeScreen"
```

---

## Verificación de cierre (todo el plan)

- [ ] **Backend completo**: `cd server && ruff check . && mypy --config-file pyproject.toml . && pytest .` — verde.
- [ ] **Móvil completo**: `cd app && flutter analyze && flutter test` — "No issues found!" y todos los tests en verde.
- [ ] **Compose completo**: `cd infra && docker compose config` válido; `docker compose up -d db api && curl -sf http://localhost:8000/v1/healthz` responde `{"status":"ok"}` (requiere Docker; si no está disponible en este entorno, ejecutar en el VPS real o en una máquina con Docker antes de dar el plan por cerrado).
- [ ] **CI local del repo**: `bash infra/scripts/ci_local.sh` — sin nuevos `FAIL` respecto al estado previo a este plan.

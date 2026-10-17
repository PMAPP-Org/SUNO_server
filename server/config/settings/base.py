"""Configuración base de Django, compartida por `dev.py` y `prod.py`.

VisitaCampo backend — Paso 4 del plan (esqueleto Django 5 + Django Ninja).
Variables de entorno leídas aquí consistentes con `infra/.env.example`
(Paso 2): mismos nombres, no se inventan variables nuevas.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

# server/ es la raíz del proyecto Django (BASE_DIR = server/, no el repo).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- Clave de firma ----------------------------------------------------
# En dev tiene un valor por defecto inseguro pero funcional para no exigir
# un .env solo para arrancar; dev.py/prod.py documentan que en producción
# DEBE venir de la variable de entorno (infra/.env.example DJANGO_SECRET_KEY).
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-solo-para-desarrollo-local-nunca-en-produccion",
)

# dev.py y prod.py fijan el valor real explícitamente; no se deja el
# comportamiento por defecto de Django (que también es False) a la suerte.
DEBUG = False

ALLOWED_HOSTS: list[str] = []

# --- Apps ----------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # App principal de dominio (Paso 4: solo aloja el endpoint de salud).
    "visitacampo.apps.VisitaCampoConfig",
    # Modelo de datos núcleo: doc 08 §4.4 + ADR-011 (Paso 5).
    "visitacampo.core.apps.CoreConfig",
    # Autenticación: login + JWT Ed25519 + refresh hasheado (ADR-008, Paso 7).
    # Se llama `authn` y no `auth` para no colisionar con `django.contrib.auth`
    # (ver el docstring de visitacampo.authn.apps.AuthnConfig).
    "visitacampo.authn.apps.AuthnConfig",
    # 3rd-party — rate limiting de login (ADR-004). Reutiliza el hook de
    # `django.contrib.auth.authenticate()` que `authn/services.py::log_in`
    # ya invoca (ver el bloque `AXES_*` más abajo para los dos ajustes que
    # sí necesitó ese archivo).
    "axes",
]

# --- Usuario ---------------------------------------------------------------
# ADR-011 (modelo de identidad plano): `AppUser` extiende `AbstractUser`, así
# que es UNA tabla (`app_user`), no una jerarquía. El swap se hace aquí, antes
# de la primera migración aplicada, que es el único momento barato de hacerlo.
# La justificación completa está en el docstring de `visitacampo.core.models.
# tenancy.AppUser`.
AUTH_USER_MODEL = "core.AppUser"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # whitenoise: sirve `/static/*` (CSS/JS de Django Admin) directamente
    # desde el proceso de gunicorn (hallazgo Importante de la revisión final
    # de despliegue del demo -- nada servía esos archivos en producción, ver
    # el comentario de `requirements.txt`). Va en `base.py` (compartido por
    # `dev.py`/`prod.py`) porque `STATIC_ROOT`/`STATIC_URL`, más abajo en
    # este mismo archivo, ya son compartidos por los dos entornos desde la
    # revisión del Task 2 de este plan ("hardcode STATIC_ROOT instead of
    # undocumented env override") -- inmediatamente después de
    # `SecurityMiddleware`, tal como exige la documentación oficial de
    # whitenoise (debe ejecutarse lo antes posible en la cadena).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        # Requerido por django.contrib.admin (back-office de fase 1, ADR-004).
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
# ADR-004: en producción se sirve con gunicorn + workers uvicorn sobre ASGI.
ASGI_APPLICATION = "config.asgi.application"

# --- Base de datos: PostgreSQL vía variables de entorno -------------------
# Nombres de variable idénticos a infra/.env.example (Paso 2, servicio `db`
# de infra/docker-compose.yml) — no se inventan nombres distintos.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "visitacampo"),
        "USER": os.environ.get("POSTGRES_USER", "visitacampo"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Timeout de conexión corto a propósito: sin esto, si Postgres no
        # está arriba (dev sin `docker compose up db`, o un host mal
        # configurado), cualquier comando de manage.py se cuelga varios
        # minutos en el timeout TCP por defecto del SO en vez de fallar
        # rápido con un error claro.
        "OPTIONS": {"connect_timeout": 5},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Tamaño máximo de cuerpo (ADR-003, Paso 8) -----------------------------
# El contrato admite lotes de push de hasta **5 MiB** descomprimidos
# (`visitacampo.serverinfo.capabilities.SYNC_PUSH_MAX_BYTES`). El valor por
# defecto de Django son 2,5 MiB, así que sin esto un lote perfectamente
# legítimo de 3 MiB moriría con el `400` de `RequestDataTooBig` antes de
# llegar a la vista, y el cliente no recibiría el `413 payload_too_large` que
# le dice que parta el lote.
#
# Se deja **exactamente** en el límite del contrato, no por encima: así el
# corte de Django y el del endpoint son el mismo número, y un cuerpo mayor se
# rechaza sin llegar a leerse entero en memoria. `RequestDataTooBig` se
# traduce a `413` en `visitacampo/core/api_errors.py`, de modo que los dos
# caminos dan la misma respuesta.
#
# El literal se repite aquí porque los settings se cargan **antes** que las
# apps y no pueden importar `visitacampo.*`. `visitacampo/sync/tests/
# test_limits.py` comprueba que ambos números siguen coincidiendo.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Contraseñas -----------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Argon2id primero en la lista (ADR-008, RD-09 "hashing de contraseñas").
# Requiere el paquete `argon2-cffi` (ver server/requirements.txt). Los
# hashers siguientes solo se mantienen para poder *leer* hashes preexistentes
# con otro algoritmo si algún día se migran datos; nunca se usan para crear
# hashes nuevos porque Argon2PasswordHasher es el primero de la lista.
#
# NOTA: el setting real de Django es `PASSWORD_HASHERS` (sin prefijo
# `AUTH_`) — a pesar de que la documentación del propio Paso 4 lo nombra
# `AUTH_PASSWORD_HASHERS`. Se verificó empíricamente: con el nombre
# `AUTH_PASSWORD_HASHERS` Django ignora la lista en silencio (no es un
# setting reconocido) y usa su lista por defecto (PBKDF2 primero). Usar el
# nombre correcto es lo único que realmente activa Argon2id como hasher
# primario, que es el objetivo real de ADR-008/RD-09.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

# --- django-axes: rate limiting de login (ADR-004) --------------------------
# Bloque adaptado de qfieldcloud/docker-app/qfieldcloud/settings.py (líneas
# ~72-77 AUTHENTICATION_BACKENDS, ~140 INSTALLED_APPS, ~169 MIDDLEWARE,
# ~588-602 AXES_*), con dos ajustes verificados empíricamente contra el login
# JWT propio de este proyecto (no el login basado en sesión de qfieldcloud):
#
# 1. **No se instala `axes.middleware.AxesMiddleware`**, a pesar de que la
#    documentación de axes lo pide. Ese middleware no participa en el
#    bloqueo (lo hace `AxesBackend.authenticate()`, más abajo); su único
#    efecto es *sustituir* la respuesta ya generada por un `429 Too Many
#    Requests` con cuerpo propio de axes cuando `request.axes_locked_out` es
#    verdadero — y ese flag se marca en la request que hace saltar el
#    límite, no solo en las posteriores. Con el middleware puesto, se
#    comprobó (ejecutando `test_axes_lockout.py` de verdad) que la 5ª
#    petición fallida devuelve `429` con un cuerpo distinto en vez del `401
#    invalid_credentials` que pide esta tarea; sin el middleware, el
#    bloqueo lo sigue haciendo `AxesBackend` exactamente igual (verificado:
#    sigue devolviendo `401` tras el límite), pero la respuesta la sigue
#    generando `authn/services.py::log_in` con su manejo de errores de
#    siempre — que es justo el comportamiento pedido.
# 2. **`authn/services.py::log_in` SÍ gana una línea** (no queda intacto del
#    todo, a pesar de lo que dice el plan): `AXES_RESET_ON_SUCCESS` solo se
#    dispara con la señal `user_logged_in`, que solo emite
#    `django.contrib.auth.login()` — función que este endpoint JWT sin
#    sesión nunca llama. Sin invocar la API pública `axes.utils.reset()` a
#    mano tras un login válido, un usuario legítimo no recupera nunca su
#    cupo de intentos (verificado: `test_a_successful_login_resets_the_
#    failure_counter` falla con 401 en vez de 200 sin esa línea). Ver el
#    comentario en `log_in` para el detalle; sigue sin ser rate limiting
#    propio, es la API pública de axes invocada en el único punto que le
#    falta una señal de Django para activarse sola.
#
# `AxesBackend`, antepuesto aquí, es quien decide si la llamada a
# `django.contrib.auth.authenticate()` que ya hacía `log_in` debe siquiera
# intentar validar la contraseña o devolver "bloqueado" sin mirarla.
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
# `axes.W002` ("falta AxesMiddleware") es la consecuencia directa y buscada
# del punto 1 de arriba. Se silencia por su código exacto —no con
# `--fail-level` más laxo— para que `check --deploy --fail-level WARNING`
# pueda ser un gate de CI estricto sin que este aviso conocido lo bloquee.
SILENCED_SYSTEM_CHECKS = ["axes.W002"]
# Una fila por intento fallido (usuario, IP, hora) en `AccessFailureLog`,
# además del contador que decide el bloqueo. Es la traza de los fallos de
# contraseña, que `AuditEvent` no registra a propósito (sin organización a la
# que atribuirlos y con riesgo de oráculo de enumeración; ver
# `visitacampo/core/audit.py`). ISO 27001 A.8.15.
AXES_ENABLE_ACCESS_FAILURE_LOG = True

# --- Autenticación JWT (ADR-008, Paso 7) -----------------------------------
# Clave de firma Ed25519 de los access tokens. Nombres consistentes con
# infra/.env.example (Paso 2): `JWT_SIGNING_KEY` ya estaba documentada allí y
# este paso fija su formato (PEM PKCS#8, o su base64 para que quepa en una
# línea de `.env`). `JWT_SIGNING_KEY_FILE` se añade aquí y es el camino
# recomendado por ADR-008 ("montada como secreto de archivo"): gana sobre la
# variable en línea cuando están las dos.
#
# **Sin valor por defecto a propósito**: una clave constante en el repositorio
# permitiría a cualquiera firmar un token válido para cualquier usuario. Sin
# clave, `visitacampo.authn.keys` falla en producción y genera una clave
# efímera en memoria (con aviso) solo cuando DEBUG=True.
JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY", "")
JWT_SIGNING_KEY_FILE = os.environ.get("JWT_SIGNING_KEY_FILE", "")

# TTL del access token: 15 min (ADR-008). Corto a propósito — es lo que acota
# el daño de un token robado, ya que el access token no se persiste y por
# tanto no se puede revocar; la revocación real vive en el refresh.
JWT_ACCESS_TTL_SECONDS = 900

# TTL del refresh: 60 días con uso (ADR-008). El dispositivo puede pasar
# semanas offline y no debe verse obligado a re-loguearse por ello.
JWT_REFRESH_TTL_SECONDS = 60 * 60 * 24 * 60

# --- Adjuntos: object storage S3-compatible (ADR-007, Paso 17) --------------
# Nombres de variable idénticos a infra/.env.example (Paso 2), que ya las
# documentaba para el MinIO del override de desarrollo — no se inventan
# nombres nuevos, solo se añade `STORAGE_BACKEND` para poder elegir adaptador.
#
# `s3` es el backend de producción (Cloudflare R2 por su API compatible) y
# también el de desarrollo con `docker compose` (MinIO). `localfs` es el
# adaptador sin red de ADR-007 punto 1: lo usan la suite de tests y quien
# desarrolle sin levantar contenedores.
#
# El valor por defecto se **deriva** en vez de fijarse a `s3` a secas: sin
# bucket configurado, un `s3` por defecto haría fallar cualquier `pytest` o
# `manage.py` local con un `ImproperlyConfigured` que no aporta nada. Con esta
# derivación, el entorno que tiene credenciales usa S3 y el que no las tiene
# cae al adaptador local — sin que ninguno de los dos tenga que declarar nada.
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND") or (
    "s3" if os.environ.get("STORAGE_S3_BUCKET_NAME") else "localfs"
)
STORAGE_S3_ENDPOINT_URL = os.environ.get("STORAGE_S3_ENDPOINT_URL", "")
STORAGE_S3_ACCESS_KEY_ID = os.environ.get("STORAGE_S3_ACCESS_KEY_ID", "")
STORAGE_S3_SECRET_ACCESS_KEY = os.environ.get("STORAGE_S3_SECRET_ACCESS_KEY", "")
STORAGE_S3_BUCKET_NAME = os.environ.get("STORAGE_S3_BUCKET_NAME", "")
# R2 ignora la región pero SigV4 la exige en la firma; `auto` es lo que
# documenta Cloudflare.
STORAGE_S3_REGION = os.environ.get("STORAGE_S3_REGION", "auto")
# MinIO necesita rutas `path-style`; R2 y S3 usan `virtual-hosted`.
STORAGE_S3_USE_PATH_STYLE = os.environ.get("STORAGE_S3_USE_PATH_STYLE", "").lower() in {
    "1",
    "true",
    "yes",
}
# Raíz del adaptador `localfs`. Bajo BASE_DIR y no en `/tmp` para que un
# desarrollador vea dónde han ido a parar sus adjuntos; `.gitignore` la excluye.
STORAGE_LOCAL_ROOT = os.environ.get("STORAGE_LOCAL_ROOT", str(BASE_DIR / ".blobstore"))

# Caducidad de las URLs prefirmadas. La de subida es la de ADR-007 (15 min):
# suficiente para una foto en 3G rural, corta para que una URL filtrada sirva
# de poco. La de descarga es mucho más corta porque el back-office la consume
# de inmediato: la URL solo tiene que sobrevivir al `redirect` del navegador.
ATTACHMENT_UPLOAD_URL_TTL_SECONDS = 900
ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS = 300

# ADR-007 (consecuencias): un objeto subido pero nunca confirmado es basura
# pasadas 24 h. El job que lo aplica lo engancha el Paso 19; la lógica vive en
# `visitacampo/attachments/services.py::collect_unconfirmed_uploads`.
ATTACHMENT_UNCONFIRMED_MAX_AGE_HOURS = 24

# --- Cola de trabajos (ADR-006, Paso 19) ------------------------------------
# El worker es `python manage.py run_worker`: un proceso Python normal, sin
# Redis, sin Celery y sin `docker.sock` (N-02 doc 08 §7.2).

# Duración del lease al tomar un job. Es, literalmente, **cuánto puede tardar
# el sistema en darse cuenta de que un worker murió**: hasta que `locked_until`
# no queda en el pasado, el reaper no toca la fila (RD-13). Un minuto es el
# equilibrio entre no reprocesar jobs vivos por una pausa del SO y no dejar
# trabajo parado tras un `kill -9`.
JOB_LEASE_SECONDS = 60

# El heartbeat renueva el lease **antes** de que caduque, con margen para que
# un latido perdido (una consulta lenta, un GC) no baste para perderlo: a un
# tercio del lease caben dos fallos seguidos sin consecuencias.
JOB_HEARTBEAT_INTERVAL_SECONDS = 20

# Sondeo cuando la cola está vacía. ADR-006 ya asume esta latencia ("polling
# añade latencia ~1 s (irrelevante para PDFs)"); subirlo ahorraría un `SELECT`
# por segundo que PostgreSQL sirve por índice sin despeinarse.
JOB_POLL_INTERVAL_SECONDS = 1.0

# Reintentos de un job antes de `dead_letter`. Es el valor por defecto de la
# columna `job.max_attempts` (Paso 5) repetido aquí porque quien encola puede
# pedir otro; los dos números tienen que coincidir y `test_queue.py` lo
# comprueba.
JOB_MAX_ATTEMPTS = 5

# Back-off exponencial entre reintentos: 5 s, 10 s, 20 s… con techo de 15 min.
JOB_BACKOFF_BASE_SECONDS = 5
JOB_BACKOFF_MAX_SECONDS = 900

# Cadencia de los tres periódicos (`visitacampo/jobs/periodic.py`). El reaper
# es el más frecuente porque es la red de seguridad de todos los demás; la
# purga de tombstones y el GC de blobs miden ventanas de 90 días y 24 h
# respectivamente, así que correrlos más a menudo no adelantaría nada.
JOB_REAP_INTERVAL_SECONDS = 60
# Cada cuánto el worker vuelve a asegurar los periódicos de organizaciones
# nuevas (`Worker.maybe_bootstrap`).
JOB_BOOTSTRAP_INTERVAL_SECONDS = int(os.environ.get("JOB_BOOTSTRAP_INTERVAL_SECONDS", "300"))
JOB_PURGE_TOMBSTONES_INTERVAL_SECONDS = 24 * 60 * 60
JOB_ATTACHMENT_GC_INTERVAL_SECONDS = 6 * 60 * 60
JOB_PURGE_SECURITY_LOGS_INTERVAL_SECONDS = 24 * 60 * 60

# --- Retención de trazas de seguridad (docs/seguridad/retencion.md) --------
# AuditEvent: un año, lo bastante para investigar un incidente y para la
# revisión anual del SGSI (ISO 27001 A.8.15). Los registros de axes guardan
# usuario e IP en claro: 90 días, lo mínimo útil para detectar ataques de
# fuerza bruta (principio de temporalidad, Ley 1581; A.8.10).
AUDIT_EVENT_RETENTION_DAYS = int(os.environ.get("AUDIT_EVENT_RETENTION_DAYS", "365"))
AXES_LOG_RETENTION_DAYS = int(os.environ.get("AXES_LOG_RETENTION_DAYS", "90"))

# --- Logging con redacción (RD-12, doc 08 §7.4) -----------------------------
# doc 08 RD-12 pide la redacción de claves conocidas **en los logs**, no solo
# en `job.output`; la tarea 7 del Paso 19 lo repite. Se instala aquí, en la
# configuración raíz, para que la cubra todo el backend: la API, el worker y
# cualquier librería que emita por `logging`.
#
# Dos piezas y no una, porque hay dos momentos en los que se puede escapar un
# secreto (ver `visitacampo/core/scrub.py`): el filtro redacta el mensaje y sus
# argumentos antes de que lo vea ningún handler; el formatter redacta el texto
# ya renderizado, que es lo único que ve el **traceback** de una excepción.
#
# `disable_existing_loggers: False` es obligatorio: a `False` los loggers que
# los módulos ya crearon (`api_errors`, `sync/services`, `jobs/*`) siguen
# funcionando; a `True` se quedarían mudos y la redacción sería perfecta
# porque no habría nada que redactar.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "scrub": {"()": "visitacampo.core.scrub.ScrubbingFilter"},
    },
    "formatters": {
        "scrubbed": {
            "()": "visitacampo.core.scrub.ScrubbingFormatter",
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["scrub"],
            "formatter": "scrubbed",
        },
    },
    # WARNING en la raíz para no ahogar la consola con el INFO de terceros;
    # los módulos propios que quieran ser visibles en operación (el worker,
    # sobre todo) se declaran uno a uno.
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "visitacampo": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# --- Internacionalización ---------------------------------------------------
LANGUAGE_CODE = "es"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# --- Estáticos ---------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

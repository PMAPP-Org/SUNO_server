"""Configuración base de Django, compartida por `dev.py` y `prod.py`.

VisitaCampo backend — Paso 4 del plan (esqueleto Django 5 + Django Ninja).
Variables de entorno leídas aquí consistentes con `infra/.env.example`
(Paso 2): mismos nombres, no se inventan variables nuevas.
"""

from __future__ import annotations

import os
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

# --- Internacionalización ---------------------------------------------------
LANGUAGE_CODE = "es"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# --- Estáticos ---------------------------------------------------------------
STATIC_URL = "static/"

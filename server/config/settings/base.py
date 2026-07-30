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
]

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

# --- Internacionalización ---------------------------------------------------
LANGUAGE_CODE = "es"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# --- Estáticos ---------------------------------------------------------------
STATIC_URL = "static/"

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

"""Arranque de la base de datos de test, compartido por los módulos que la necesitan.

El proyecto no usa `pytest-django` (ver `server/conftest.py`), así que cada
módulo de test que toque PostgreSQL tiene que crear y destruir la BD de test a
mano. `test_models.py` (Paso 5) lo hace en línea y documenta el patrón entero;
este módulo lo extrae para que los archivos del Paso 6 en adelante no lo
repitan. `test_models.py` se deja intacto a propósito: reescribirlo sin poder
ejecutar su mitad de integración en local sería un riesgo gratuito.

Contrato de uso, en un módulo de test::

    from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

    setUpModule = setup_test_database
    tearDownModule = teardown_test_database

    @unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
    class MisTests(TestCase): ...

**`VISITACAMPO_REQUIRE_DB=1`** (lo que fija CI, ver
`.github/workflows/backend.yml`): si no hay PostgreSQL alcanzable, importar
este módulo falla en tiempo de colección en vez de dejar que los tests se
salten en silencio. Un skip masivo y un verde real no pueden parecerse.
"""

from __future__ import annotations

import os

from django.db import connection
from django.test.utils import setup_test_environment, teardown_test_environment

REQUIRE_DB_ENV = "VISITACAMPO_REQUIRE_DB"


def _probe_database() -> str | None:
    """`None` si hay BD utilizable, o el motivo del skip si no la hay."""
    try:
        connection.ensure_connection()
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de conexión sirve
        return f"PostgreSQL no alcanzable ({type(exc).__name__}: {exc})"
    return None


#: `None` cuando hay base de datos; si no, el motivo legible del skip.
SKIP_REASON: str | None = _probe_database()

if SKIP_REASON is not None and os.environ.get(REQUIRE_DB_ENV) == "1":
    raise RuntimeError(
        f"{REQUIRE_DB_ENV}=1 exige PostgreSQL alcanzable y no lo está: {SKIP_REASON}. "
        "Revisa el servicio `postgres:16` y las variables POSTGRES_* del job "
        "lint-and-test en .github/workflows/backend.yml (o, en local, deja "
        f"{REQUIRE_DB_ENV} sin fijar para volver al modo con skip)."
    )

_STATE: dict[str, object] = {}


def setup_test_database() -> None:
    """Crea y migra la BD de test (`test_<NAME>`) una vez por módulo.

    `create_test_db()` devuelve el nombre de la BD de test **nueva** (p.ej.
    `test_visitacampo`), no el original — hay que capturar
    `connection.settings_dict["NAME"]` **antes** de llamarlo. Guardar el
    valor de retorno como si fuera "el nombre a restaurar" (como hacía este
    módulo antes de esta corrección) hace que `teardown_test_database()`
    "restaure" el nombre ya prefijado con `test_`, y el siguiente módulo que
    llame a `create_test_db()` le añade otro prefijo encima — con varios
    módulos de test en una sola sesión de pytest, el nombre acumula un
    `test_` por módulo hasta superar el límite de 63 caracteres de
    PostgreSQL.
    """
    if SKIP_REASON is not None:
        return
    setup_test_environment()
    _STATE["old_name"] = connection.settings_dict["NAME"]
    connection.creation.create_test_db(verbosity=0, autoclobber=True)


def teardown_test_database() -> None:
    """Destruye la BD de test. No deja residuo entre módulos."""
    if SKIP_REASON is not None:
        return
    connection.creation.destroy_test_db(_STATE["old_name"], verbosity=0)
    teardown_test_environment()

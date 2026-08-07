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

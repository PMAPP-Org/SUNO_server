"""Redacción de secretos (RD-12, corrige **H-17**): jobs y logs, misma función.

Tres bloques, y los tres son necesarios porque un secreto puede escaparse por
tres sitios distintos:

- `ScrubTests` — la función pura (`core/scrub.py`), sin BD ni logging.
- `JobOutputScrubbingTests` — **el comando de verificación literal del plan**:
  "forzar un job que falla con un secreto en el traceback (p. ej. una cadena de
  conexión con password) → `job.output` persistido **no contiene** el secreto".
  Con el worker de producción ejecutando un handler que revienta de verdad.
- `LoggingScrubbingTests` — la tarea 7 dice "aplicar **también** a los logs
  estructurados generales del backend, no solo a jobs". Un scrubber que solo
  cubriera `job.output` dejaría el secreto en `docker compose logs`, que es
  donde más gente lo va a ver.
"""

from __future__ import annotations

import logging
import unittest
from collections.abc import Iterator
from contextlib import contextmanager

from django.test import SimpleTestCase, TestCase

from visitacampo.core.models import Job, JobStatus
from visitacampo.core.scrub import (
    REDACTED,
    ScrubbingFilter,
    ScrubbingFormatter,
    is_sensitive_key,
    scrub,
    scrub_text,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.jobs import queue, registry
from visitacampo.jobs.worker import Worker, WorkerSettings

from .factories import make_org, reload_job

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: El secreto de los tests. Es una DSN completa porque es exactamente la forma
#: en que H-17 se materializa: un job que no puede conectarse a algo y arrastra
#: la cadena de conexión —con contraseña— hasta el mensaje de la excepción.
SECRET = "sup3r-s3cr3to-de-produccion"
DSN = f"postgresql://visitacampo:{SECRET}@db.interno:5432/visitacampo"


@contextmanager
def temporary_handler(job_type: str, handler) -> Iterator[None]:
    """Registra un handler solo mientras dura el test.

    Se toca `registry._HANDLERS` a propósito y solo desde aquí: el registro de
    producción no expone un `unregister` porque desregistrar un handler en
    producción no significa nada (un tipo sin handler es un `dead_letter`). En
    un test sí hace falta, o el handler de prueba se quedaría registrado para
    el resto de la sesión de pytest.
    """
    registry.register(job_type)(handler)
    try:
        yield
    finally:
        registry._HANDLERS.pop(job_type, None)


# --- 1. La función pura ------------------------------------------------------


class ScrubTests(SimpleTestCase):
    def test_a_sensitive_key_is_redacted_whatever_its_value(self) -> None:
        for key in ("password", "token", "authorization", "dsn", "secret"):
            with self.subTest(key=key):
                self.assertEqual(scrub({key: SECRET}), {key: REDACTED})

    def test_the_match_is_case_insensitive_and_by_substring(self) -> None:
        """`access_token`, `HTTP_AUTHORIZATION`, `DATABASE_DSN`… también caen."""
        redacted = scrub(
            {
                "access_token": SECRET,
                "HTTP_AUTHORIZATION": SECRET,
                "DATABASE_DSN": SECRET,
                "client_secret": SECRET,
                "user_password_hash": SECRET,
            }
        )
        self.assertEqual(set(redacted.values()), {REDACTED})

    def test_an_innocent_key_survives(self) -> None:
        self.assertEqual(scrub({"filas": 12, "org": "acme"}), {"filas": 12, "org": "acme"})

    def test_it_recurses_into_nested_structures(self) -> None:
        redacted = scrub({"config": {"db": {"password": SECRET}}, "lista": [{"token": SECRET}]})
        self.assertEqual(redacted["config"]["db"]["password"], REDACTED)
        self.assertEqual(redacted["lista"][0]["token"], REDACTED)

    def test_a_dsn_inside_a_free_text_string_is_redacted(self) -> None:
        """La regla que la redacción por clave **no** puede cubrir."""
        redacted = scrub_text(f"could not connect to {DSN}")
        self.assertNotIn(SECRET, redacted)
        self.assertIn(REDACTED, redacted)
        self.assertIn("visitacampo", redacted, "el usuario sí es diagnóstico útil")

    def test_key_value_pairs_inside_a_string_are_redacted(self) -> None:
        for text in (
            f"password={SECRET}",
            f'"token": "{SECRET}"',
            f"authorization: Bearer {SECRET}",
            f"DSN = {SECRET}",
        ):
            with self.subTest(text=text):
                self.assertNotIn(SECRET, scrub_text(text))

    def test_only_the_value_is_removed_not_the_rest_of_the_line(self) -> None:
        """Un log redactado tiene que seguir sirviendo para diagnosticar."""
        redacted = scrub_text(f"connect(host=db, password={SECRET}, port=5432)")
        self.assertIn("host=db", redacted)
        self.assertIn("port=5432", redacted)
        self.assertNotIn(SECRET, redacted)

    def test_numbers_and_booleans_pass_through(self) -> None:
        plain = {"n": 3, "ok": True, "nada": None}
        self.assertEqual(scrub(plain), plain)

    def test_the_result_is_json_serialisable(self) -> None:
        """`job.output` es una columna `jsonb`: lo que no serializa, revienta."""
        import json
        import uuid

        redacted = scrub({"id": uuid.uuid4(), "conjunto": {1, 2}, "tupla": (1, "a")})
        self.assertEqual(json.loads(json.dumps(redacted))["tupla"], [1, "a"])

    def test_deep_nesting_is_truncated_instead_of_leaking(self) -> None:
        deep: object = {"password": SECRET}
        for _ in range(40):
            deep = {"nivel": deep}
        self.assertNotIn(SECRET, str(scrub(deep)))

    def test_is_sensitive_key_ignores_non_strings(self) -> None:
        self.assertFalse(is_sensitive_key(3))
        self.assertFalse(is_sensitive_key(None))


# --- 2. El comando de verificación del plan ---------------------------------


@skip_without_db
class JobOutputScrubbingTests(TestCase):
    """Un job que revienta con un secreto dentro: no queda ni rastro en la fila."""

    def setUp(self) -> None:
        self.org = make_org("Scrub")

    def run_failing_job(self, job_type: str, handler) -> Job:
        job_id = queue.enqueue(org_id=self.org.pk, job_type=job_type, max_attempts=1)
        assert job_id is not None
        with temporary_handler(job_type, handler):
            Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False)).run_once()
        return reload_job(job_id)

    def test_a_secret_in_the_traceback_never_reaches_job_output(self) -> None:
        """**Regresión directa de H-17.**"""

        def _explota(record):
            raise ConnectionError(f"could not connect to server: {DSN}")

        job = self.run_failing_job("test.dsn_leak", _explota)

        self.assertEqual(job.status, JobStatus.DEAD_LETTER)
        serialized = f"{job.output!r} {job.error!r}"
        self.assertNotIn(SECRET, serialized, "el secreto se ha filtrado a la fila")
        self.assertIn(REDACTED, serialized, "…y algo se tiene que haber redactado")

    def test_the_diagnosis_survives_the_redaction(self) -> None:
        """Redactar no puede equivaler a borrar: el fallo sigue siendo legible."""

        def _explota(record):
            raise ConnectionError(f"could not connect to server: {DSN}")

        job = self.run_failing_job("test.dsn_diag", _explota)

        assert job.error is not None
        self.assertEqual(job.error["code"], "internal_error")
        self.assertEqual(job.error["details"]["exception"], "ConnectionError")
        assert job.output is not None
        self.assertIn("ConnectionError", job.output["traceback"])
        self.assertIn("db.interno", job.output["traceback"], "el host sí es diagnóstico")

    def test_a_secret_in_a_key_of_the_returned_output_is_redacted_too(self) -> None:
        """El camino feliz también pasa por `scrub()` (`queue.succeed`)."""

        def _devuelve_secreto(record):
            return {"informe": "ok", "token": SECRET}

        job_id = queue.enqueue(org_id=self.org.pk, job_type="test.output_key")
        assert job_id is not None
        with temporary_handler("test.output_key", _devuelve_secreto):
            Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False)).run_once()

        job = reload_job(job_id)
        self.assertEqual(job.status, JobStatus.SUCCEEDED)
        self.assertEqual(job.output, {"informe": "ok", "token": REDACTED})

    def test_a_failing_job_with_attempts_left_also_stores_a_scrubbed_output(self) -> None:
        def _explota(record):
            raise RuntimeError(f"secret={SECRET}")

        job_id = queue.enqueue(org_id=self.org.pk, job_type="test.retry_leak", max_attempts=3)
        assert job_id is not None
        with temporary_handler("test.retry_leak", _explota):
            Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False)).run_once()

        job = reload_job(job_id)
        self.assertEqual(job.status, JobStatus.PENDING, "le quedan intentos")
        self.assertNotIn(SECRET, f"{job.output!r} {job.error!r}")


# --- 3. Los logs generales (tarea 7) ----------------------------------------


class LoggingScrubbingTests(SimpleTestCase):
    """RD-12 en `logging`, que es donde acaba viéndolo más gente."""

    def _record(self, msg: str, *args: object, exc_info=None) -> logging.LogRecord:
        return logging.LogRecord(
            name="visitacampo.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=exc_info,
        )

    def test_the_filter_redacts_the_message(self) -> None:
        record = self._record(f"fallo al conectar a {DSN}")
        self.assertTrue(ScrubbingFilter().filter(record))
        self.assertNotIn(SECRET, record.getMessage())

    def test_the_filter_redacts_the_arguments(self) -> None:
        record = self._record("fallo al conectar a %s", DSN)
        ScrubbingFilter().filter(record)
        self.assertNotIn(SECRET, record.getMessage())

    def test_the_filter_redacts_dict_arguments(self) -> None:
        record = self._record("%(password)s", {"password": SECRET})
        ScrubbingFilter().filter(record)
        self.assertNotIn(SECRET, record.getMessage())

    def test_the_filter_never_drops_the_record(self) -> None:
        """Redactar, no censurar: perder la línea es peor que verla redactada."""
        self.assertTrue(ScrubbingFilter().filter(self._record("sin secretos")))

    def test_the_formatter_redacts_the_traceback(self) -> None:
        """Lo único que el filtro no puede alcanzar."""
        try:
            raise ConnectionError(f"could not connect to {DSN}")
        except ConnectionError:
            import sys

            record = self._record("falló el job", exc_info=sys.exc_info())

        formatted = ScrubbingFormatter("%(message)s").format(record)
        self.assertNotIn(SECRET, formatted)
        self.assertIn("ConnectionError", formatted)

    def test_the_project_logging_configuration_installs_both(self) -> None:
        """Que estén escritos no basta: tienen que estar **instalados**."""
        from django.conf import settings

        self.assertEqual(
            settings.LOGGING["filters"]["scrub"]["()"],
            "visitacampo.core.scrub.ScrubbingFilter",
        )
        self.assertEqual(
            settings.LOGGING["formatters"]["scrubbed"]["()"],
            "visitacampo.core.scrub.ScrubbingFormatter",
        )
        console = settings.LOGGING["handlers"]["console"]
        self.assertIn("scrub", console["filters"])
        self.assertEqual(console["formatter"], "scrubbed")

    def test_the_configured_handler_is_actually_wired_at_runtime(self) -> None:
        """`dictConfig` ya corrió: el handler real tiene el filtro puesto.

        Se buscan por nombre los handlers que declara `LOGGING`: pytest añade
        sus propios handlers de captura a este mismo logger y esos no son
        configuración del proyecto.
        """
        from django.conf import settings

        configured = set(settings.LOGGING["handlers"])
        handlers = [
            h for h in logging.getLogger("visitacampo").handlers if h.get_name() in configured
        ]
        self.assertTrue(handlers, "el logger `visitacampo` debe tener handler propio")
        for handler in handlers:
            with self.subTest(handler=handler):
                self.assertTrue(
                    any(isinstance(f, ScrubbingFilter) for f in handler.filters),
                    "el handler configurado no lleva el filtro de redacción",
                )
                self.assertIsInstance(handler.formatter, ScrubbingFormatter)

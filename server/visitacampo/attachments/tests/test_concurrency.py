"""Dos commits simultáneos del mismo `sha256`. **Criterio de salida: H-08 mitigado.**

El comando de verificación del plan, literal: "dos dispositivos suben el mismo
`sha256` casi simultáneamente → ambos commits resuelven **sin error de
constraint sin recuperación** (a diferencia del bug documentado en H-08),
gracias al `ON CONFLICT DO NOTHING` sobre una clave inmutable".

**Por qué este test sí puede ser concluyente aquí** (a diferencia del de
`sync/tests/test_concurrency.py`, que necesita un PostgreSQL real por
`pg_advisory_xact_lock`): también necesita PostgreSQL real, y por la misma
razón — `ignore_conflicts=True` se traduce a `ON CONFLICT DO NOTHING`, que es
sintaxis de PostgreSQL. Se salta si no hay base de datos, igual que el resto de
la suite de integración.

**Qué falla si la implementación se rompe.** Si `_register` usara
`get_or_create` —`SELECT` y luego `INSERT`, dos sentencias—, los dos hilos
pasarían por el `SELECT` vacío y el segundo `INSERT` levantaría
`IntegrityError`: exactamente la "carrera que necesita recuperación" que el
plan pide evitar. Con `bulk_create(ignore_conflicts=True)` la carrera la
resuelve PostgreSQL dentro de una sola sentencia y ninguno de los dos clientes
ve un error.

Requiere `TransactionTestCase` (no `TestCase`): con la transacción envolvente
de `TestCase` los hilos no verían los datos del test ni commitearían nada, así
que no habría concurrencia real que observar.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.db import connections
from django.test import TransactionTestCase, override_settings

from visitacampo.attachments import services
from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Blob, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import JPEG_BYTES, JPEG_MIME, build_scenario, digest_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

JPEG_SHA = digest_of(JPEG_BYTES)


def _run_isolated(work: Any) -> Any:
    """Ejecuta `work()` cerrando después la conexión propia del hilo.

    Django abre una conexión por hilo; si no se cierra, la BD de test no se
    puede destruir al terminar. Mismo patrón que `sync/tests/test_concurrency.py`.
    """
    try:
        return work()
    finally:
        connections.close_all()


@skip_without_db
class SimultaneousCommitTests(TransactionTestCase):
    """El mismo contenido confirmado por dos dispositivos a la vez."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.scenario = build_scenario(
            role=Role.FIELD_AGENT, username=f"agente-{uuid.uuid4().hex[:8]}"
        )
        # El objeto ya está en el store: es el estado real tras dos `PUT`
        # prefirmados del mismo contenido (que producen el mismo objeto, porque
        # la clave se deriva del hash — ADR-007 punto 2).
        LocalFsBlobStore(self._tmp.name).put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)

    def _ctx(self) -> RequestContext:
        return RequestContext(
            user_id=self.scenario.user.pk,
            org_id=self.scenario.org.pk,
            roles=frozenset({Role.FIELD_AGENT}),
        )

    def _commit(self) -> services.CommitResult:
        return _run_isolated(
            lambda: services.commit(
                self._ctx(), sha256=JPEG_SHA, size=len(JPEG_BYTES), mime=JPEG_MIME
            )
        )

    def test_two_simultaneous_commits_both_succeed_without_constraint_errors(self) -> None:
        """El comando de verificación del plan, tal cual.

        Que ninguna de las dos llamadas lance es la mitad del criterio; la otra
        es que las dos vean **la misma** fila. Un `IntegrityError` aquí sería el
        bug de la clase de H-08 reintroducido.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._commit) for _ in range(2)]
            results = [future.result() for future in futures]

        self.assertEqual(Blob.objects.filter(pk=JPEG_SHA).count(), 1)
        for result in results:
            self.assertEqual(result.blob.pk, JPEG_SHA)
            self.assertEqual(result.blob.size, len(JPEG_BYTES))
        # Los dos devuelven la fila idéntica, con el mismo `created_at`: no hay
        # dos versiones del mismo contenido, que es la propiedad de RD-08.
        self.assertEqual(results[0].blob.created_at, results[1].blob.created_at)

    def test_exactly_one_of_them_reports_having_created_the_row(self) -> None:
        """No es un detalle cosmético: es lo que prueba que hubo carrera real.

        Si los dos dijeran `created=True`, `_register` estaría mintiendo sobre
        quién escribió; si los dos dijeran `False`, la fila la habría escrito
        alguien más y este test no estaría observando lo que cree.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [pool.submit(self._commit) for _ in range(2)]]

        self.assertEqual(sum(1 for result in results if result.created), 1)

    def test_many_simultaneous_commits_still_resolve_cleanly(self) -> None:
        """Ocho a la vez: la carrera con margen, no por casualidad de dos hilos."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [future.result() for future in [pool.submit(self._commit) for _ in range(8)]]

        self.assertEqual(len(results), 8)
        self.assertEqual(Blob.objects.filter(pk=JPEG_SHA).count(), 1)
        self.assertEqual(sum(1 for result in results if result.created), 1)

    def test_the_blob_table_has_no_mutable_field_to_lose(self) -> None:
        """**H-08 por construcción** (RD-08), verificado sobre el modelo real.

        El lost-update de la referencia era un `UPDATE file SET latest_version =
        ...` sin bloqueo. Aquí no hay ningún campo que actualizar: los cinco
        campos de `blob` quedan fijados en el `INSERT`, y cuatro de ellos son
        `editable=False`. Si alguien añadiera un `latest_version`, un `estado` o
        un `updated_at` a esta tabla, este test es lo que se lo diría.
        """
        self.assertEqual(
            {field.name for field in Blob._meta.get_fields() if not field.is_relation},
            {"sha256", "size", "mime", "bucket_key", "created_at"},
        )
        for name in ("sha256", "size", "mime", "bucket_key"):
            with self.subTest(field=name):
                self.assertFalse(Blob._meta.get_field(name).editable)

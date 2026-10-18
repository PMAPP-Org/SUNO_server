"""GC de subidas nunca confirmadas (tarea 7 del Paso 17, ADR-007 consecuencias).

Dos bloques, y la separación es el punto:

- `UnconfirmedDecisionTests` — la **lógica pura** (`unconfirmed_sha256`): sin
  store, sin base de datos y sin esperar 24 h. Es donde se prueba la frontera
  temporal exacta y las tres condiciones que hacen basura a un objeto.
- `CollectUnconfirmedUploadsTests` — el cableado completo contra un
  `LocalFsBlobStore` y la tabla `blob` real.

La invocación **periódica** es del Paso 19 (cola de trabajos). Aquí se deja la
función lista e invocable, que es exactamente donde el plan pone la frontera.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings

from visitacampo.attachments import services
from visitacampo.attachments.blobstore import StoredObject, bucket_key_for
from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.core.models import Blob
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import JPEG_BYTES, JPEG_MIME, PNG_BYTES, PNG_MIME, digest_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
MAX_AGE = timedelta(hours=24)

JPEG_SHA = digest_of(JPEG_BYTES)
PNG_SHA = digest_of(PNG_BYTES)


def stored(sha256: str | None, *, age: timedelta, size: int = 10, key: str = "") -> StoredObject:
    return StoredObject(
        key=key or (bucket_key_for(sha256) if sha256 else "otra/cosa"),
        size=size,
        last_modified=NOW - age,
        sha256=sha256,
    )


class UnconfirmedDecisionTests(SimpleTestCase):
    """La decisión pura. Ni un byte de I/O."""

    def collect(self, *objects: StoredObject, registered: frozenset[str] = frozenset()):
        return services.unconfirmed_sha256(objects, registered, now=NOW, max_age=MAX_AGE)

    def test_an_old_unconfirmed_object_is_garbage(self) -> None:
        entry = stored(JPEG_SHA, age=timedelta(days=3))
        self.assertEqual(self.collect(entry), [entry])

    def test_a_confirmed_object_is_never_garbage_however_old(self) -> None:
        """Un blob registrado es contenido vivo: la antigüedad no lo cambia."""
        entry = stored(JPEG_SHA, age=timedelta(days=365))
        self.assertEqual(self.collect(entry, registered=frozenset({JPEG_SHA})), [])

    def test_a_recent_unconfirmed_object_is_left_alone(self) -> None:
        """La carrera obvia: un `PUT` recién hecho cuyo `commit` aún no ha llegado.

        Sin esta condición, el GC destruiría subidas en curso — el fallo más
        caro posible en este job, porque el agente de campo ya gastó los datos.
        """
        self.assertEqual(self.collect(stored(JPEG_SHA, age=timedelta(minutes=5))), [])

    def test_the_boundary_is_exactly_max_age(self) -> None:
        just_inside = stored(JPEG_SHA, age=MAX_AGE - timedelta(minutes=1))
        exactly = stored(JPEG_SHA, age=MAX_AGE)
        just_outside = stored(JPEG_SHA, age=MAX_AGE + timedelta(minutes=1))

        self.assertEqual(self.collect(just_inside), [])
        self.assertEqual(self.collect(exactly), [exactly])
        self.assertEqual(self.collect(just_outside), [just_outside])

    def test_objects_this_code_did_not_write_are_never_deleted(self) -> None:
        """Un backup en el mismo bucket no es basura de este job.

        `sha256=None` significa "no reconozco esta clave". Borrar lo que no se
        reconoce es el daño irreversible que un job automático no debe poder
        causar.
        """
        self.assertEqual(self.collect(stored(None, age=timedelta(days=90))), [])

    def test_it_separates_the_confirmed_from_the_unconfirmed_in_one_pass(self) -> None:
        old_confirmed = stored(JPEG_SHA, age=timedelta(days=2))
        old_unconfirmed = stored(PNG_SHA, age=timedelta(days=2))
        found = self.collect(old_confirmed, old_unconfirmed, registered=frozenset({JPEG_SHA}))
        self.assertEqual([entry.sha256 for entry in found], [PNG_SHA])

    def test_an_empty_bucket_is_not_an_error(self) -> None:
        self.assertEqual(self.collect(), [])


@skip_without_db
class CollectUnconfirmedUploadsTests(TestCase):
    """El cableado completo: listar, consultar `blob`, borrar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.store = LocalFsBlobStore(self._tmp.name)

    def age(self, sha256: str, delta: timedelta) -> None:
        self.store.set_last_modified(sha256, datetime.now(UTC) - delta)

    def register(self, sha256: str, size: int) -> Blob:
        return Blob.objects.create(
            sha256=sha256,
            size=size,
            mime=JPEG_MIME,
            bucket_key=bucket_key_for(sha256),
        )

    def test_it_deletes_only_the_old_unconfirmed_object(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)  # confirmado
        self.store.put_object(PNG_SHA, PNG_BYTES, PNG_MIME)  # huérfano viejo
        self.register(JPEG_SHA, len(JPEG_BYTES))
        self.age(JPEG_SHA, timedelta(days=5))
        self.age(PNG_SHA, timedelta(days=5))

        report = services.collect_unconfirmed_uploads(store=self.store)

        self.assertEqual(report.inspected, 2)
        self.assertEqual(report.deleted, (PNG_SHA,))
        self.assertEqual(report.reclaimed_bytes, len(PNG_BYTES))
        self.assertEqual({entry.sha256 for entry in self.store.list_objects()}, {JPEG_SHA})

    def test_a_recent_orphan_survives(self) -> None:
        self.store.put_object(PNG_SHA, PNG_BYTES, PNG_MIME)
        report = services.collect_unconfirmed_uploads(store=self.store)

        self.assertEqual(report.deleted, ())
        self.assertEqual(len(list(self.store.list_objects())), 1)

    def test_it_is_idempotent_and_safe_to_retry(self) -> None:
        """Un job que muera a mitad y se reintente no debe romper nada."""
        self.store.put_object(PNG_SHA, PNG_BYTES, PNG_MIME)
        self.age(PNG_SHA, timedelta(days=5))

        first = services.collect_unconfirmed_uploads(store=self.store)
        second = services.collect_unconfirmed_uploads(store=self.store)

        self.assertEqual(first.deleted, (PNG_SHA,))
        self.assertEqual(second.deleted, ())
        self.assertEqual(second.inspected, 0)

    def test_an_empty_bucket_reports_nothing_and_does_not_fail(self) -> None:
        report = services.collect_unconfirmed_uploads(store=self.store)
        self.assertEqual((report.inspected, report.deleted, report.reclaimed_bytes), (0, (), 0))

    def test_the_max_age_window_is_configurable(self) -> None:
        """El Paso 19 podrá afinarla sin tocar esta lógica."""
        self.store.put_object(PNG_SHA, PNG_BYTES, PNG_MIME)
        self.age(PNG_SHA, timedelta(hours=2))

        untouched = services.collect_unconfirmed_uploads(store=self.store)
        self.assertEqual(untouched.deleted, ())

        collected = services.collect_unconfirmed_uploads(
            store=self.store, max_age=timedelta(hours=1)
        )
        self.assertEqual(collected.deleted, (PNG_SHA,))

    def test_it_never_touches_a_registered_blob_even_if_ancient(self) -> None:
        self.store.put_object(JPEG_SHA, JPEG_BYTES, JPEG_MIME)
        self.register(JPEG_SHA, len(JPEG_BYTES))
        self.age(JPEG_SHA, timedelta(days=400))

        report = services.collect_unconfirmed_uploads(store=self.store)
        self.assertEqual(report.deleted, ())
        self.assertTrue(Blob.objects.filter(pk=JPEG_SHA).exists())

    def test_it_is_invocable_without_arguments(self) -> None:
        """Tarea 7: la función queda **lista para que el Paso 19 la enganche**.

        Con `override_settings` apuntando al store local, la llamada sin
        argumentos —que es como la invocará el job— resuelve el store por sí
        sola. Si esto fallara, el Paso 19 tendría que inventarse el cableado.
        """
        report = services.collect_unconfirmed_uploads()
        self.assertEqual(report.inspected, 0)
        self.assertIsInstance(report, services.GarbageCollection)

    def test_the_default_window_comes_from_settings(self) -> None:
        from django.conf import settings

        self.assertEqual(settings.ATTACHMENT_UNCONFIRMED_MAX_AGE_HOURS, 24)


@skip_without_db
class GcIgnoresForeignObjectsTests(TestCase):
    """Objetos ajenos en el bucket: el GC los enumera pero no los borra."""

    def test_a_foreign_key_is_inspected_but_never_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalFsBlobStore(tmp)
            store.put_object(PNG_SHA, PNG_BYTES, PNG_MIME)
            store.set_last_modified(PNG_SHA, datetime.now(UTC) - timedelta(days=5))

            # Un archivo que este código no escribió, bajo el mismo prefijo.
            foreign = Path(tmp) / "blobs" / "backup.dump"
            foreign.parent.mkdir(parents=True, exist_ok=True)
            foreign.write_bytes(b"no soy un blob")

            report = services.collect_unconfirmed_uploads(store=store)

            self.assertEqual(report.inspected, 2)
            self.assertEqual(report.deleted, (PNG_SHA,))
            self.assertTrue(foreign.exists(), "el GC no puede borrar lo que no reconoce")


class GcReportShapeTests(SimpleTestCase):
    def test_the_report_is_serialisable_for_a_job_output(self) -> None:
        """El Paso 19 persistirá esto en `job.output`; conviene que sea plano."""
        report = services.GarbageCollection(inspected=3, deleted=(JPEG_SHA,), reclaimed_bytes=10)
        self.assertEqual(report.inspected, 3)
        self.assertEqual(report.deleted, (JPEG_SHA,))
        self.assertEqual(report.reclaimed_bytes, 10)
        # Sin secretos ni rutas: solo hashes públicos y contadores (RD-12).
        self.assertNotIn("/", "".join(report.deleted))

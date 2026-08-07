"""H-13 / RD-14: dos workers a la vez, **un solo** job. Sin double-bookkeeping.

Comando de verificación del Paso 19, literal: *"dos workers arrancando casi
simultáneamente sobre el mismo tipo de job único → solo uno lo toma (verificado
con el constraint único parcial + `SKIP LOCKED`), sin doble-bookkeeping en dos
módulos distintos"*.

Son **dos** carreras distintas y las dos importan, así que las dos se prueban:

1. **La de encolar** (el índice único parcial). Dos workers arrancando a la vez
   llaman los dos a `periodic.bootstrap()`. En la referencia auditada, esto se
   resolvía con un "¿ya existe uno activo?" seguido de un `INSERT` — dos
   sentencias, y entre ellas la ventana TOCTOU que **es** H-13. Aquí es una
   sola sentencia y la unicidad la decide PostgreSQL.
2. **La de tomar** (`FOR UPDATE SKIP LOCKED`). Con la fila ya creada, N workers
   compiten por ejecutarla y exactamente uno se la lleva; los demás no esperan
   ni fallan, siguen a lo siguiente.

`TransactionTestCase` y no `TestCase`: los hilos necesitan ver los commits de
los demás, y `TestCase` envuelve cada test en una transacción que nadie más
puede leer. Mismo patrón que `attachments/tests/test_concurrency.py` (Paso 17)
y `sync/tests/test_concurrency.py` (Paso 8).

**Por qué hilos y no procesos aquí**: lo que se está probando es una carrera
*dentro de PostgreSQL* —dos conexiones, dos transacciones—, y dos hilos de
Python con conexión propia producen exactamente eso, a coste de RAM cero. El
único sitio donde hace falta un proceso de verdad es el `kill -9`, porque ahí
lo que se prueba es la muerte del proceso; eso está en `test_kill9.py`.
"""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase

from visitacampo.core.models import Job, JobStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.jobs import periodic, queue

from .factories import make_org

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

WORKERS = 8


def _in_thread(work):
    """Ejecuta `work` cerrando después la conexión que Django abrió para el hilo."""

    def _run(*args):
        try:
            return work(*args)
        finally:
            connection.close()

    return _run


@skip_without_db
class ConcurrentEnqueueTests(TransactionTestCase):
    """La carrera de **encolar**: el índice único parcial (RD-14)."""

    def setUp(self) -> None:
        self.org = make_org()

    def _race(self, count: int, work) -> list:
        gate = threading.Barrier(count)

        def _contestant(index: int):
            gate.wait()
            return work(index)

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(_in_thread(_contestant), range(count)))

    def test_only_one_of_eight_simultaneous_enqueues_creates_the_job(self) -> None:
        results = self._race(
            WORKERS,
            lambda _: queue.enqueue(
                org_id=self.org.pk, job_type="queue.reap", idempotency_key="queue.reap"
            ),
        )

        created = [job_id for job_id in results if job_id is not None]
        self.assertEqual(len(created), 1, "el índice parcial es el único árbitro")
        self.assertEqual(Job.objects.filter(org=self.org).count(), 1)

    def test_two_workers_bootstrapping_at_once_leave_one_set_of_periodics(self) -> None:
        """El caso real: `manage.py run_worker` arrancado dos veces a la vez."""
        results = self._race(2, lambda _: periodic.bootstrap())

        self.assertEqual(sorted(results), [0, len(periodic.PERIODIC_JOBS)])
        for definition in periodic.PERIODIC_JOBS:
            with self.subTest(job=definition.type):
                self.assertEqual(
                    Job.objects.filter(org=self.org, type=definition.type).count(), 1
                )

    def test_nobody_raises_an_integrity_error(self) -> None:
        """`ON CONFLICT DO NOTHING`, no "intenta y recupérate del error".

        Un `IntegrityError` capturado sería otra forma de resolverlo, pero deja
        la transacción abortada y obliga a envolver cada encolado en su propio
        `atomic()`. Aquí el `INSERT` que pierde la carrera **no es un error**:
        devuelve cero filas y ya está.
        """
        results = self._race(
            WORKERS,
            lambda _: queue.enqueue(
                org_id=self.org.pk, job_type="sync.purge_tombstones", idempotency_key="purga"
            ),
        )
        self.assertEqual(len(results), WORKERS)  # ninguno murió por el camino


@skip_without_db
class ConcurrentTakeTests(TransactionTestCase):
    """La carrera de **tomar**: `FOR UPDATE SKIP LOCKED`."""

    def setUp(self) -> None:
        self.org = make_org()

    def _take_from(self, count: int) -> list:
        gate = threading.Barrier(count)

        def _contestant(index: int):
            gate.wait()
            return queue.take(worker_id=f"worker-{index}", lease_seconds=60)

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(_in_thread(_contestant), range(count)))

    def test_a_single_job_is_taken_by_exactly_one_worker(self) -> None:
        job_id = queue.enqueue(
            org_id=self.org.pk, job_type="queue.reap", idempotency_key="queue.reap"
        )
        assert job_id is not None

        taken = [record for record in self._take_from(WORKERS) if record is not None]

        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0].id, job_id)
        self.assertEqual(taken[0].attempts, 1, "un solo incremento, un solo intento")

    def test_the_losers_get_none_instead_of_waiting_or_failing(self) -> None:
        """`SKIP LOCKED` frente a `FOR UPDATE` a secas: nadie se bloquea."""
        queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="k")
        results = self._take_from(WORKERS)
        self.assertEqual(sum(1 for record in results if record is None), WORKERS - 1)

    def test_three_jobs_and_eight_workers_end_up_one_each(self) -> None:
        """Nadie ejecuta el mismo job dos veces, y no se pierde ninguno."""
        expected = {
            queue.enqueue(org_id=self.org.pk, job_type=f"tipo-{index}") for index in range(3)
        }

        taken = [record for record in self._take_from(WORKERS) if record is not None]

        self.assertEqual({record.id for record in taken}, expected)
        self.assertEqual(len(taken), 3)
        self.assertEqual(
            Job.objects.filter(status=JobStatus.RUNNING).count(),
            3,
        )
        self.assertEqual(
            len({job.locked_by for job in Job.objects.filter(status=JobStatus.RUNNING)}),
            3,
            "tres jobs, tres dueños distintos",
        )


@skip_without_db
class SingleMechanismTests(TransactionTestCase):
    """RD-14 explícito: **un** mecanismo, no dos que puedan divergir."""

    def test_the_invariant_is_enforced_by_the_database_itself(self) -> None:
        """Saltarse `queue.enqueue` **no** permite saltarse el invariante.

        Se escribe una segunda fila activa con la misma clave por el camino más
        crudo posible —el ORM, sin pasar por el `ON CONFLICT` del módulo— y
        PostgreSQL la rechaza. Es la demostración de que la garantía vive en la
        base de datos y no en el código que normalmente encola: si estuviera en
        un `if` de Python (el double-bookkeeping que RD-14 prohíbe), esta
        escritura pasaría tan campante y el invariante sería una convención,
        no una garantía.
        """
        org = make_org()
        first = queue.enqueue(
            org_id=org.pk, job_type="queue.reap", idempotency_key="queue.reap"
        )
        self.assertIsNotNone(first)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Job.objects.create(
                    org=org,
                    type="queue.reap",
                    status=JobStatus.PENDING,
                    idempotency_key="queue.reap",
                )

    def test_the_same_key_is_writable_again_once_the_job_is_terminal(self) -> None:
        """El índice es **parcial**: el historial no bloquea el futuro."""
        org = make_org()
        first = queue.enqueue(
            org_id=org.pk, job_type="queue.reap", idempotency_key="queue.reap"
        )
        Job.objects.filter(pk=first).update(status=JobStatus.DEAD_LETTER)

        Job.objects.create(
            org=org,
            type="queue.reap",
            status=JobStatus.PENDING,
            idempotency_key="queue.reap",
        )
        self.assertEqual(Job.objects.filter(org=org).count(), 2)

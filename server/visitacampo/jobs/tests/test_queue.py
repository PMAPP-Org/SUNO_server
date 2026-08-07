"""La cola: tomar con lease, latir, terminar, fallar con back-off y `dead_letter`.

Todo contra PostgreSQL real, porque lo que se está probando **es** el
comportamiento de PostgreSQL: `FOR UPDATE SKIP LOCKED`, `ON CONFLICT` sobre un
índice parcial y `CASE WHEN` dentro de un `UPDATE` no tienen equivalente que
simular. Un doble de la BD aquí probaría el doble, no la cola.

El `kill -9` de ADR-006 y la carrera de dos workers viven en sus propios
archivos (`test_kill9.py`, `test_toctou.py`); aquí están las transiciones.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from visitacampo.core.models import ACTIVE_JOB_STATUSES, Job, JobStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.jobs import queue

from .factories import job_row, make_org, reload_job

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

WORKER = "worker-a"
OTHER_WORKER = "worker-b"


@skip_without_db
class EnqueueTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()

    def test_a_job_without_key_can_be_enqueued_twice(self) -> None:
        """Dos exportaciones pedidas a mano son dos trabajos, no uno."""
        first = queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        second = queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)

    def test_the_second_active_job_with_the_same_key_is_refused(self) -> None:
        """RD-14 en una línea: lo decide el índice, no un `if` de Python."""
        first = queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        second = queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(Job.objects.filter(org=self.org).count(), 1)

    def test_the_same_key_is_free_again_once_the_job_finished(self) -> None:
        """Si un job terminado siguiera ocupando la clave, un periódico moriría."""
        first = queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        assert first is not None
        Job.objects.filter(pk=first).update(status=JobStatus.SUCCEEDED)

        second = queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        self.assertIsNotNone(second)

    def test_the_key_is_scoped_to_the_organization(self) -> None:
        other = make_org("Otra")
        self.assertIsNotNone(
            queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        )
        self.assertIsNotNone(
            queue.enqueue(org_id=other.pk, job_type="queue.reap", idempotency_key="reap")
        )

    def test_a_running_job_still_holds_its_key(self) -> None:
        job_id = queue.enqueue(
            org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap"
        )
        assert job_id is not None
        queue.take(worker_id=WORKER, lease_seconds=60)

        self.assertEqual(reload_job(job_id).status, JobStatus.RUNNING)
        self.assertIsNone(
            queue.enqueue(org_id=self.org.pk, job_type="queue.reap", idempotency_key="reap")
        )

    def test_the_payload_is_scrubbed_before_it_is_stored(self) -> None:
        """RD-12 también al **entrar**: un payload no debería traer secretos
        (doc 08 N-16), y si los trae no se guardan en claro."""
        job_id = queue.enqueue(
            org_id=self.org.pk,
            job_type="export_visits_csv",
            payload={"destino": "s3://bucket", "token": "muy-secreto"},
        )
        assert job_id is not None
        stored = reload_job(job_id).payload
        self.assertEqual(stored["token"], "[REDACTED]")
        self.assertEqual(stored["destino"], "s3://bucket")

    def test_the_default_max_attempts_matches_the_model(self) -> None:
        """El default del settings y el de la columna tienen que ser el mismo."""
        job_id = queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        assert job_id is not None
        self.assertEqual(
            reload_job(job_id).max_attempts,
            Job._meta.get_field("max_attempts").default,
        )


@skip_without_db
class ActiveStatusesTests(TestCase):
    def test_the_literals_of_the_partial_index_match_the_enum(self) -> None:
        """`ACTIVE_JOB_STATUSES` va como literales para no ensuciar la migración."""
        self.assertEqual(
            set(ACTIVE_JOB_STATUSES),
            {JobStatus.PENDING.value, JobStatus.RUNNING.value},
        )


@skip_without_db
class TakeTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()

    def enqueue(self, **kwargs) -> object:
        return queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv", **kwargs)

    def test_an_empty_queue_returns_none(self) -> None:
        self.assertIsNone(queue.take(worker_id=WORKER, lease_seconds=60))

    def test_taking_sets_lease_and_increments_attempts(self) -> None:
        """Tarea 2, literal: `locked_by`, `locked_until` y `attempts += 1`."""
        job_id = self.enqueue()
        before = timezone.now()

        record = queue.take(worker_id=WORKER, lease_seconds=60)

        assert record is not None
        self.assertEqual(record.id, job_id)
        self.assertEqual(record.attempts, 1)
        self.assertFalse(record.is_retry)
        row = job_row(record.id)
        self.assertEqual(row["status"], JobStatus.RUNNING)
        self.assertEqual(row["locked_by"], WORKER)
        self.assertGreaterEqual(row["locked_until"], before + timedelta(seconds=59))

    def test_a_running_job_is_never_taken_again(self) -> None:
        self.enqueue()
        self.assertIsNotNone(queue.take(worker_id=WORKER, lease_seconds=60))
        self.assertIsNone(queue.take(worker_id=OTHER_WORKER, lease_seconds=60))

    def test_a_future_run_after_is_not_taken_yet(self) -> None:
        """El back-off tiene que servir de algo."""
        self.enqueue(run_after=timezone.now() + timedelta(minutes=5))
        self.assertIsNone(queue.take(worker_id=WORKER, lease_seconds=60))

    def test_a_past_run_after_is_taken(self) -> None:
        self.enqueue(run_after=timezone.now() - timedelta(minutes=5))
        self.assertIsNotNone(queue.take(worker_id=WORKER, lease_seconds=60))

    def test_the_oldest_eligible_job_goes_first(self) -> None:
        now = timezone.now()
        second = queue.enqueue(
            org_id=self.org.pk, job_type="b", run_after=now - timedelta(minutes=1)
        )
        first = queue.enqueue(
            org_id=self.org.pk, job_type="a", run_after=now - timedelta(minutes=10)
        )
        record = queue.take(worker_id=WORKER, lease_seconds=60)
        assert record is not None
        self.assertEqual(record.id, first)
        self.assertNotEqual(record.id, second)

    def test_a_worker_serves_every_organization(self) -> None:
        """Un worker no pertenece a ningún tenant (ver el docstring de queue.py)."""
        other = make_org("Otra")
        queue.enqueue(org_id=other.pk, job_type="export_visits_csv")
        record = queue.take(worker_id=WORKER, lease_seconds=60)
        assert record is not None
        self.assertEqual(record.org_id, other.pk)

    def test_the_payload_arrives_as_a_dict(self) -> None:
        """El cursor crudo devuelve `jsonb` como texto: hay que decodificarlo."""
        self.enqueue(payload={"limite": 500, "anidado": {"a": [1, 2]}})
        record = queue.take(worker_id=WORKER, lease_seconds=60)
        assert record is not None
        self.assertEqual(record.payload, {"limite": 500, "anidado": {"a": [1, 2]}})


@skip_without_db
class HeartbeatTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        self.record = queue.take(worker_id=WORKER, lease_seconds=10)
        assert self.record is not None

    def test_it_extends_the_lease(self) -> None:
        original = job_row(self.record.id)["locked_until"]
        self.assertTrue(
            queue.heartbeat(job_id=self.record.id, worker_id=WORKER, lease_seconds=600)
        )
        self.assertGreater(job_row(self.record.id)["locked_until"], original)

    def test_another_worker_cannot_extend_our_lease(self) -> None:
        self.assertFalse(
            queue.heartbeat(job_id=self.record.id, worker_id=OTHER_WORKER, lease_seconds=600)
        )

    def test_it_reports_false_once_the_job_was_reaped(self) -> None:
        """La mitad que se olvida del patrón: enterarse de que ya no es tuyo."""
        Job.objects.filter(pk=self.record.id).update(
            status=JobStatus.PENDING, locked_by=None, locked_until=None
        )
        self.assertFalse(
            queue.heartbeat(job_id=self.record.id, worker_id=WORKER, lease_seconds=600)
        )


@skip_without_db
class TerminalStateTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()

    def take(self, **kwargs):
        queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv", **kwargs)
        record = queue.take(worker_id=WORKER, lease_seconds=60)
        assert record is not None
        return record

    def test_success_clears_the_lease_and_stores_the_output(self) -> None:
        record = self.take()
        self.assertTrue(
            queue.succeed(job_id=record.id, worker_id=WORKER, output={"filas": 12})
        )
        row = job_row(record.id)
        self.assertEqual(row["status"], JobStatus.SUCCEEDED)
        self.assertEqual(row["output"], {"filas": 12})
        self.assertIsNone(row["locked_by"])
        self.assertIsNone(row["locked_until"])
        self.assertIsNotNone(row["finished_at"])

    def test_success_from_a_worker_that_lost_the_lease_writes_nothing(self) -> None:
        record = self.take()
        self.assertFalse(
            queue.succeed(job_id=record.id, worker_id=OTHER_WORKER, output={"filas": 12})
        )
        self.assertEqual(job_row(record.id)["status"], JobStatus.RUNNING)

    def test_a_failure_with_attempts_left_goes_back_to_pending_with_backoff(self) -> None:
        record = self.take(max_attempts=3)
        before = timezone.now()

        status = queue.fail(
            job_id=record.id, worker_id=WORKER, error={"code": "internal_error", "message": "x"}
        )

        self.assertEqual(status, JobStatus.PENDING)
        job = reload_job(record.id)
        self.assertIsNone(job.finished_at)
        self.assertIsNone(job.locked_by)
        assert job.run_after is not None
        self.assertGreater(job.run_after, before)

    def test_the_backoff_grows_with_the_attempts(self) -> None:
        self.assertLess(queue.backoff_seconds(1), queue.backoff_seconds(3))
        self.assertLessEqual(queue.backoff_seconds(50), queue.backoff_seconds(60))

    def test_the_backoff_is_capped(self) -> None:
        from django.conf import settings

        self.assertEqual(
            queue.backoff_seconds(64), float(settings.JOB_BACKOFF_MAX_SECONDS)
        )

    def test_the_last_attempt_goes_to_dead_letter(self) -> None:
        """Tarea 5: agotados los intentos, el job no vuelve a la cola."""
        record = self.take(max_attempts=1)
        status = queue.fail(
            job_id=record.id,
            worker_id=WORKER,
            error={"code": "internal_error", "message": "sin remedio"},
        )
        self.assertEqual(status, JobStatus.DEAD_LETTER)
        row = job_row(record.id)
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(row["error"]["message"], "sin remedio")

    def test_max_attempts_is_the_number_of_executions(self) -> None:
        """`max_attempts = 2` ⇒ se ejecuta dos veces, ni una más.

        ADR-006 enuncia el umbral como `attempts > max_attempts`; aquí se
        compara `>=` porque `attempts` sube **al tomar** (ver `queue.fail`).
        Este test fija esa decisión en un número observable: con `>` serían
        tres ejecuciones y el nombre del campo mentiría.

        El `run_after` se rebobina **después** de cada fallo, no antes: `fail`
        lo empuja al futuro con el back-off, que es justo lo que impediría al
        siguiente `take` verlo. Adelantar el reloj de esa forma es lo que evita
        que el test tenga que dormir 5 segundos entre intentos.
        """
        queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv", max_attempts=2)
        executions = 0
        for _ in range(5):
            record = queue.take(worker_id=WORKER, lease_seconds=60)
            if record is None:
                break
            executions += 1
            status = queue.fail(
                job_id=record.id, worker_id=WORKER, error={"code": "internal_error", "message": "x"}
            )
            if status == JobStatus.DEAD_LETTER:
                break
            Job.objects.filter(pk=record.id).update(run_after=timezone.now())
        self.assertEqual(executions, 2)

    def test_an_unknown_type_is_dead_lettered_without_burning_retries(self) -> None:
        record = self.take(max_attempts=5)
        self.assertTrue(
            queue.mark_dead_letter(
                job_id=record.id,
                worker_id=WORKER,
                error={"code": "internal_error", "message": "sin handler"},
            )
        )
        row = job_row(record.id)
        self.assertEqual(row["status"], JobStatus.DEAD_LETTER)
        self.assertEqual(row["attempts"], 1)


@skip_without_db
class ReaperTests(TestCase):
    """El reaper aislado. La versión con un proceso muerto de verdad: `test_kill9.py`."""

    def setUp(self) -> None:
        self.org = make_org()

    def take_and_expire(self, **kwargs):
        queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv", **kwargs)
        record = queue.take(worker_id=WORKER, lease_seconds=60)
        assert record is not None
        Job.objects.filter(pk=record.id).update(
            locked_until=timezone.now() - timedelta(seconds=1)
        )
        return record

    def test_an_expired_lease_goes_back_to_pending(self) -> None:
        record = self.take_and_expire()
        report = queue.reap_expired_leases()
        self.assertEqual((report.requeued, report.dead_lettered), (1, 0))
        row = job_row(record.id)
        self.assertEqual(row["status"], JobStatus.PENDING)
        self.assertIsNone(row["locked_by"])
        self.assertIsNone(row["locked_until"])
        self.assertEqual(row["attempts"], 1, "el reaper no vuelve a contar el intento")

    def test_a_live_lease_is_left_alone(self) -> None:
        queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        record = queue.take(worker_id=WORKER, lease_seconds=600)
        assert record is not None

        self.assertEqual(queue.reap_expired_leases().total, 0)
        self.assertEqual(job_row(record.id)["status"], JobStatus.RUNNING)

    def test_a_pending_job_is_not_touched(self) -> None:
        job_id = queue.enqueue(org_id=self.org.pk, job_type="export_visits_csv")
        assert job_id is not None
        self.assertEqual(queue.reap_expired_leases().total, 0)

    def test_an_exhausted_job_is_dead_lettered_instead_of_requeued(self) -> None:
        """Un job que mata al worker cada vez no puede reintentarse sin fin."""
        record = self.take_and_expire(max_attempts=1)
        report = queue.reap_expired_leases()
        self.assertEqual((report.requeued, report.dead_lettered), (0, 1))
        row = job_row(record.id)
        self.assertEqual(row["status"], JobStatus.DEAD_LETTER)
        self.assertIsNotNone(row["finished_at"])
        self.assertEqual(row["error"], queue.LEASE_EXPIRED_ERROR)

    def test_the_requeued_job_waits_for_its_backoff(self) -> None:
        record = self.take_and_expire()
        before = timezone.now()
        queue.reap_expired_leases()
        job = reload_job(record.id)
        assert job.run_after is not None
        self.assertGreater(job.run_after, before)

    def test_it_sweeps_every_organization_at_once(self) -> None:
        """Un lease es del worker, no del tenant."""
        other = make_org("Otra")
        self.take_and_expire()
        queue.enqueue(org_id=other.pk, job_type="export_visits_csv")
        second = queue.take(worker_id=OTHER_WORKER, lease_seconds=60)
        assert second is not None
        Job.objects.filter(pk=second.id).update(
            locked_until=timezone.now() - timedelta(seconds=1)
        )

        self.assertEqual(queue.reap_expired_leases().requeued, 2)

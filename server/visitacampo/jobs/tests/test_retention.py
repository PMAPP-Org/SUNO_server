"""Retención de trazas de seguridad (ISO 27001 A.8.10 / A.8.15, Ley 1581).

El periódico `security.purge_logs` borra lo que superó su plazo y nada más:

- `AuditEvent` más antiguos que `AUDIT_EVENT_RETENTION_DAYS`, **solo** de la
  organización del job (aislamiento ADR-011);
- `AccessFailureLog` y `AccessLog` de django-axes (usuario + IP en claro)
  más antiguos que `AXES_LOG_RETENTION_DAYS`.

Se prueba por el camino real: encolar → worker → handler.
"""

from __future__ import annotations

import unittest
from datetime import timedelta

from axes.models import AccessFailureLog
from django.test import TestCase, override_settings
from django.utils import timezone

from visitacampo.core.models import AuditEvent, JobStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.jobs import periodic, queue
from visitacampo.jobs.worker import Worker, WorkerSettings

from .factories import make_org, reload_job

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def _audit(org, *, age_days: int, action: str = "auth.login") -> AuditEvent:
    event = AuditEvent.objects.create(org=org, action=action, target="device:x", metadata={})
    AuditEvent.objects.filter(pk=event.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return event


def _failure(*, age_days: int) -> AccessFailureLog:
    row = AccessFailureLog.objects.create(username="agente", ip_address="203.0.113.7")
    AccessFailureLog.objects.filter(pk=row.pk).update(
        attempt_time=timezone.now() - timedelta(days=age_days)
    )
    return row


@skip_without_db
@override_settings(AUDIT_EVENT_RETENTION_DAYS=365, AXES_LOG_RETENTION_DAYS=90)
class SecurityLogRetentionTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Retención")
        self.other_org = make_org("Otra")

    def run_purge(self, org):
        job_id = queue.enqueue(org_id=org.pk, job_type=periodic.PURGE_SECURITY_LOGS)
        assert job_id is not None
        Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False)).run_once()
        return reload_job(job_id)

    def test_is_a_registered_periodic(self) -> None:
        self.assertIn(
            periodic.PURGE_SECURITY_LOGS, {p.type for p in periodic.PERIODIC_JOBS}
        )

    def test_purges_only_audit_events_past_retention(self) -> None:
        expired = _audit(self.org, age_days=400)
        kept = _audit(self.org, age_days=30)

        job = self.run_purge(self.org)

        self.assertEqual(job.status, JobStatus.SUCCEEDED)
        self.assertFalse(AuditEvent.objects.filter(pk=expired.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(pk=kept.pk).exists())
        assert job.output is not None
        self.assertEqual(job.output["audit_events"], 1)

    def test_never_touches_another_organizations_audit_trail(self) -> None:
        foreign = _audit(self.other_org, age_days=400)
        self.run_purge(self.org)
        self.assertTrue(AuditEvent.objects.filter(pk=foreign.pk).exists())

    def test_purges_axes_failure_logs_past_retention(self) -> None:
        expired = _failure(age_days=120)
        kept = _failure(age_days=10)

        job = self.run_purge(self.org)

        self.assertFalse(AccessFailureLog.objects.filter(pk=expired.pk).exists())
        self.assertTrue(AccessFailureLog.objects.filter(pk=kept.pk).exists())
        assert job.output is not None
        self.assertEqual(job.output["axes_failure_logs"], 1)


@skip_without_db
class PeriodicRebootstrapTests(TestCase):
    """Una organización creada después de arrancar el worker recibe sus periódicos.

    Regresión: `periodic.bootstrap()` solo corría al arrancar. En un despliegue
    nuevo el worker arranca con la BD vacía (la primera organización se crea
    después, con `bootstrap_demo` o desde el Admin) y nunca llegaba a encolar
    reaper, purgas ni GC hasta que alguien lo reiniciaba.
    """

    def test_due_rebootstrap_enqueues_periodics_for_a_new_org(self) -> None:
        worker = Worker(WorkerSettings(worker_id="w", bootstrap_interval=60.0))
        self.assertEqual(worker.maybe_bootstrap(now=0.0), 0)  # sin organizaciones

        make_org("Llega tarde")
        self.assertEqual(worker.maybe_bootstrap(now=30.0), 0)  # aún no toca
        self.assertEqual(worker.maybe_bootstrap(now=61.0), len(periodic.PERIODIC_JOBS))
        self.assertEqual(worker.maybe_bootstrap(now=200.0), 0)  # idempotente

    def test_rebootstrap_can_be_disabled(self) -> None:
        worker = Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False))
        make_org("Sin periódicos")
        self.assertEqual(worker.maybe_bootstrap(now=10_000.0), 0)

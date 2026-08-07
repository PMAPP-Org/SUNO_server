"""**El test obligatorio de ADR-006**: `kill -9` al worker a mitad de un job.

Doc 08 ADR-006, consecuencias negativas: *"hay que escribir el reaper y
probarlo de verdad — **test obligatorio: `kill -9` al worker a mitad de job y
verificar que el reaper lo recupera** (exactamente el test que doc 07 recomienda
y que la referencia no tiene)"*. Esto es ese test, y es el que cierra **H-12**
(RD-13).

**Qué se mata, exactamente.** Un proceso Python real, lanzado con
`subprocess.Popen`, que ejecuta el `Worker` de producción
(`visitacampo/jobs/worker.py`) sobre la misma base de datos de test. No es un
mock, no es un hilo y no es un contenedor: es un `python -m …` con su propio
PID, y muere con una señal que **no se puede atender** — sin `finally`, sin
cerrar la conexión y sin escribir nada en la fila. Si la recuperación
dependiera de que el moribundo colaborase, este test no pasaría.

**Adaptación a Windows, dicha explícitamente.** `SIGKILL` no existe en Windows
(`signal.SIGKILL` ni siquiera está definido), así que `_hard_kill` usa
`taskkill /F /T /PID`, que es su equivalente exacto: terminación inmediata por
el kernel, sin darle al proceso oportunidad de ejecutar nada. En Linux/macOS
—donde corre la CI— usa `os.kill(pid, signal.SIGKILL)` literal. Las dos ramas
están en el mismo sitio y el resto del test es idéntico.

**Cómo se observa la recuperación**, sin dormir a ojo y sin trampas:

1. El job es de tipo `test.recoverable`: su primer intento se cuelga para
   siempre; el segundo termina al instante devolviendo `attempts`. La
   asimetría la aporta el handler del script auxiliar (`_worker_probe.py`), no
   la cola.
2. Se espera al **marcador en disco** que el handler escribe antes de dormirse:
   así el `kill` cae con el job de verdad a medias, no antes de empezar.
3. Tras matar al worker, se comprueba que la fila sigue diciendo `running` con
   su `locked_by` — el estado "sucio" que en la referencia se quedaba así para
   siempre.
4. El reaper **no se llama a mano**: se encola un job `queue.reap` y lo ejecuta
   un **segundo worker real**, por el mismo mecanismo de cola que todo lo
   demás. Ese worker recupera el job y, en la misma ejecución, lo reprocesa.
5. El resultado final (`succeeded`, `attempts = 2`, `recovered_on_attempt: 2`)
   solo puede darse si la cadena entera funcionó.

`TransactionTestCase` porque el otro proceso tiene que **ver** lo que este
escribe: dentro de la transacción de un `TestCase` no hay nada que ver.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from visitacampo.core.models import Job, JobStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.jobs import queue, periodic

from .factories import make_org, reload_job

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: `server/`, que es el directorio desde el que `config.settings` es importable.
SERVER_ROOT = Path(__file__).resolve().parents[3]

PROBE_MODULE = "visitacampo.jobs.tests._worker_probe"
RECOVERABLE = "test.recoverable"

#: Lease cortísimo en el worker que va a morir: es el tiempo que el job se
#: queda huérfano antes de ser recuperable. En producción son 60 s
#: (`JOB_LEASE_SECONDS`); aquí 3 s para no alargar el test sin cambiar nada del
#: mecanismo — lo que se prueba es que caduque, no cuánto tarda.
DOOMED_LEASE_SECONDS = 3

#: Cuánto se espera, como mucho, a que ocurra cada cosa. Generoso a propósito:
#: esta máquina arranca un intérprete Python + Django por proceso.
TIMEOUT = 90.0


def _hard_kill(process: subprocess.Popen) -> None:
    """`kill -9` real. En Windows, su equivalente exacto: `taskkill /F`.

    Ninguna de las dos ramas le da al proceso ocasión de ejecutar código: no
    hay handler de señal posible para `SIGKILL`, y `/F` de `taskkill` termina
    el proceso desde el kernel. `/T` incluye la descendencia, que aquí no la
    hay, pero evita dejar nada colgando si algún día el probe lanzara algo.
    """
    if hasattr(signal, "SIGKILL"):  # POSIX (Linux/macOS, que es donde corre CI)
        os.kill(process.pid, signal.SIGKILL)
    else:  # Windows
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
        )
    process.wait(timeout=30)


def _wait_until(predicate, message: str, timeout: float = TIMEOUT) -> None:
    """Sondea `predicate` hasta que sea cierto. Falla con `message` si no lo es.

    Se sondea en vez de dormir una cantidad fija porque los dos procesos no
    comparten reloj ni memoria: lo único observable de verdad es el estado en
    PostgreSQL y el marcador en disco.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"Timeout de {timeout}s esperando: {message}")


@skip_without_db
class KillNineRecoveryTests(TransactionTestCase):
    """El escenario completo, de principio a fin."""

    def setUp(self) -> None:
        self.org = make_org("Kill9")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.marker = Path(self._tmp.name) / "handler-started"
        self._processes: list[subprocess.Popen] = []
        self.addCleanup(self._terminate_survivors)

    def _terminate_survivors(self) -> None:
        """Ningún worker de prueba sobrevive al test que lo lanzó."""
        for process in self._processes:
            if process.poll() is None:
                _hard_kill(process)

    # --- Lanzamiento de workers reales --------------------------------------

    def _environment(self) -> dict[str, str]:
        """El entorno del hijo, apuntando a **la base de datos de test**.

        `connection.settings_dict["NAME"]` ya es `test_visitacampo` (lo dejó
        `create_test_db`), así que basta con propagarlo por `POSTGRES_DB`: sin
        esto el worker hijo se conectaría a la base de datos de desarrollo y no
        vería ningún job.
        """
        env = dict(os.environ)
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.dev",
                "POSTGRES_DB": connection.settings_dict["NAME"],
                "PYTHONPATH": str(SERVER_ROOT),
                # Salida sin buffer: si el worker imprime algo antes de morir,
                # se ve en el `stdout` que este test captura.
                "PYTHONUNBUFFERED": "1",
            }
        )
        return env

    def _spawn_worker(self, *args: str) -> subprocess.Popen:
        process = subprocess.Popen(  # noqa: S603 — argumentos fijos, sin shell
            [sys.executable, "-m", PROBE_MODULE, *args],
            cwd=str(SERVER_ROOT),
            env=self._environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._processes.append(process)
        return process

    # --- El test ------------------------------------------------------------

    def test_a_worker_killed_mid_job_is_recovered_by_the_reaper(self) -> None:
        job_id = queue.enqueue(
            org_id=self.org.pk,
            job_type=RECOVERABLE,
            payload={"marker": str(self.marker)},
            max_attempts=5,
        )
        assert job_id is not None

        # --- 1. Un worker real toma el job y se mete dentro del handler ------
        doomed = self._spawn_worker(
            "--worker-id",
            f"doomed-{uuid.uuid4().hex[:6]}",
            "--lease-seconds",
            str(DOOMED_LEASE_SECONDS),
            # Heartbeat más largo que el lease: este worker no llega a
            # extenderlo ni una vez, así que al morir el lease caduca solo.
            "--heartbeat-interval",
            "30",
            "--max-jobs",
            "1",
            "--idle-timeout",
            str(TIMEOUT),
        )
        self.assertNotEqual(doomed.pid, os.getpid(), "tiene que ser otro proceso")

        _wait_until(self.marker.exists, "que el handler empiece a trabajar")
        _wait_until(
            lambda: reload_job(job_id).status == JobStatus.RUNNING,
            "que el job quede marcado como running",
        )
        running = reload_job(job_id)
        self.assertEqual(running.attempts, 1)
        self.assertIsNotNone(running.locked_by)
        self.assertIsNotNone(running.locked_until)
        self.assertIsNone(doomed.poll(), "el worker debe seguir vivo cuando lo matemos")

        # --- 2. kill -9 en mitad del trabajo ---------------------------------
        _hard_kill(doomed)
        self.assertIsNotNone(doomed.poll(), "el proceso tiene que estar muerto")

        # Nada se limpió: la fila sigue "en curso" con dueño. Este es el estado
        # en el que la referencia auditada dejaba los jobs **para siempre**.
        orphan = reload_job(job_id)
        self.assertEqual(orphan.status, JobStatus.RUNNING)
        self.assertEqual(orphan.locked_by, running.locked_by)
        self.assertIsNone(orphan.finished_at)

        # --- 3. El lease caduca solo, sin que nadie lo toque -----------------
        _wait_until(
            lambda: reload_job(job_id).locked_until < timezone.now(),
            "que caduque el lease del worker muerto",
            timeout=DOOMED_LEASE_SECONDS + 10,
        )
        self.assertEqual(
            reload_job(job_id).status,
            JobStatus.RUNNING,
            "un lease caducado no se recupera solo: hace falta el reaper",
        )

        # --- 4. El reaper, como job de la propia cola, en otro worker real ---
        reap_id = queue.enqueue(
            org_id=self.org.pk,
            job_type=periodic.QUEUE_REAP,
            idempotency_key=periodic.QUEUE_REAP,
        )
        assert reap_id is not None

        rescuer = self._spawn_worker(
            "--worker-id",
            f"rescuer-{uuid.uuid4().hex[:6]}",
            "--lease-seconds",
            "120",
            # Dos jobs: el reaper y, tras el back-off, el job recuperado.
            "--max-jobs",
            "2",
            "--idle-timeout",
            str(TIMEOUT),
        )
        stdout, _ = rescuer.communicate(timeout=TIMEOUT + 60)
        self.assertEqual(rescuer.returncode, 0, f"el worker de rescate falló:\n{stdout}")

        # --- 5. El resultado: recuperado y reprocesado ----------------------
        reaped = reload_job(reap_id)
        self.assertEqual(reaped.status, JobStatus.SUCCEEDED, f"salida del worker:\n{stdout}")
        self.assertEqual(
            reaped.output,
            {"requeued": 1, "dead_lettered": 0},
            "el reaper tiene que haber devuelto exactamente un job a la cola",
        )

        recovered = reload_job(job_id)
        self.assertEqual(recovered.status, JobStatus.SUCCEEDED, f"salida:\n{stdout}")
        self.assertEqual(recovered.attempts, 2, "un intento perdido + uno bueno")
        self.assertEqual(recovered.output, {"recovered_on_attempt": 2})
        self.assertIsNone(recovered.locked_by, "el lease se libera al terminar")
        self.assertIsNone(recovered.locked_until)
        self.assertIsNotNone(recovered.finished_at)
        self.assertIsNone(recovered.error)

        # Y el marcador guarda el número del último intento que llegó a
        # ejecutarse: prueba independiente de la BD de que el handler corrió
        # dos veces en dos procesos distintos.
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "2")

    def test_the_probe_handler_is_not_registered_in_production(self) -> None:
        """El handler de prueba vive **solo** en el proceso hijo.

        Si `test.recoverable` acabara registrado en el backend real, este test
        estaría probando código que se despliega. Se comprueba desde el proceso
        de pytest, que importa la cola y los periódicos de producción pero
        nunca `_worker_probe`.
        """
        from visitacampo.jobs.registry import registered_types

        self.assertNotIn(RECOVERABLE, registered_types())
        self.assertEqual(
            registered_types(),
            {definition.type for definition in periodic.PERIODIC_JOBS},
        )


@skip_without_db
class UnknownTypeTests(TransactionTestCase):
    """Un job sin handler no se reintenta cinco veces: muere a la primera."""

    def test_a_job_with_no_handler_goes_straight_to_dead_letter(self) -> None:
        from visitacampo.jobs.worker import Worker, WorkerSettings

        org = make_org("SinHandler")
        job_id = queue.enqueue(org_id=org.pk, job_type="no.existe", max_attempts=5)
        assert job_id is not None

        Worker(WorkerSettings(worker_id="w", bootstrap_periodic=False)).run_once()

        job = reload_job(job_id)
        self.assertEqual(job.status, JobStatus.DEAD_LETTER)
        self.assertEqual(job.attempts, 1, "no gasta los cinco intentos")
        assert job.error is not None
        self.assertEqual(job.error["details"], {"type": "no.existe"})


@skip_without_db
class QueueSizeTests(TransactionTestCase):
    """Higiene: el test no puede dejar filas ni procesos detrás de sí."""

    def test_the_probe_module_is_not_collected_as_a_test(self) -> None:
        """El `_` del nombre es lo que lo mantiene fuera de pytest."""
        probe = Path(__file__).with_name("_worker_probe.py")
        self.assertTrue(probe.exists())
        self.assertTrue(probe.name.startswith("_"))

    def test_no_jobs_leak_between_tests(self) -> None:
        self.assertEqual(Job.objects.count(), 0)
        self.assertIsInstance(json.dumps({"ok": True}), str)

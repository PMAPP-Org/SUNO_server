"""El bucle del worker: tomar, latir, ejecutar, escribir el resultado.

ADR-006: *"un proceso `worker` (mismo código, `python manage.py run_worker`)"*.
Ni Celery, ni Redis, ni `docker.sock` — este módulo no importa nada que no sea
Django y la biblioteca estándar, y el proceso que lo ejecuta es un Python
normal (N-02 de doc 08 §7.2).

**El orden de las operaciones es la mitad del diseño**, así que va explícito:

1. `queue.take` marca la fila como `running` con lease y sube `attempts` en una
   sola sentencia. A partir de ahí el job **es de este worker** hasta que el
   lease venza.
2. Arranca el heartbeat en un hilo aparte. Extiende `locked_until` mientras el
   handler trabaja; si alguna vez la extensión escribe cero filas, este worker
   ha perdido el lease.
3. Corre el handler. Si lanza, se captura **todo** (`BaseException` no: un
   `KeyboardInterrupt` debe poder parar el proceso).
4. Se escribe el resultado — **solo si el lease sigue siendo nuestro**. Si se
   perdió, no se escribe nada: otro worker ya está reprocesando el job y
   pisarle el resultado sería peor que perder este.

**Sobre el hilo del heartbeat.** Es la única concurrencia del módulo y está
acotada a propósito: no ejecuta lógica de negocio, solo un `UPDATE` de una
columna cada `JOB_HEARTBEAT_INTERVAL_SECONDS`. Django abre una conexión por
hilo, así que el hilo cierra la suya en su `finally`; si no lo hiciera, un
worker de larga vida iría dejando conexiones abiertas en PostgreSQL, una por
job ejecutado.

**Sobre `kill -9`.** No hay nada que hacer en este proceso: por definición no
se ejecuta ningún `finally`, no se cierra ninguna conexión y no se escribe
ningún estado. Esa es justamente la razón de ser del lease — la recuperación no
depende de que el moribundo colabore, sino de que `locked_until` caduque sola y
el reaper la recoja (`queue.reap_expired_leases`). El test obligatorio de
ADR-006 mata este proceso de verdad y comprueba que el job se reprocesa.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone

from visitacampo.core.scrub import scrub_text
from visitacampo.jobs import periodic, queue
from visitacampo.jobs.queue import JobRecord
from visitacampo.jobs.registry import handler_for

__all__ = ["Worker", "WorkerSettings", "default_worker_id"]

logger = logging.getLogger(__name__)

#: `code` del contrato con el que se registra un job que reventó. `Job.error`
#: guarda un cuerpo `Error` (ver el modelo), y `internal_error` es el único
#: código de `components.schemas.ErrorCode` que describe "falló el servidor".
JOB_FAILED_CODE = "internal_error"

#: Cuánto traceback se guarda en `job.output`. Un traceback de recursión
#: infinita ocupa megabytes y `job.output` se lee desde una API: se conserva la
#: **cola**, que es donde está la excepción original y su contexto inmediato.
MAX_TRACEBACK_CHARS = 4000


def default_worker_id() -> str:
    """Identificador legible y único del proceso: `host/pid/sufijo`.

    El sufijo aleatorio existe porque `hostname:pid` **no** es único a lo largo
    del tiempo: un contenedor reiniciado reutiliza el PID 1, y un worker nuevo
    con el identificador de uno muerto podría "heredar" el lease del anterior y
    escribir el resultado de un job que no ejecutó él. Con el sufijo, el
    `locked_by` de un proceso muerto no lo puede reclamar nadie.
    """
    return f"{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Parámetros de una ejecución del worker (los fija `run_worker`).

    `max_jobs` e `idle_timeout` existen para que un worker pueda **terminar
    solo**. En producción no se usan (el proceso vive indefinidamente), pero
    sin ellos un test que lance un worker de verdad tendría que matarlo siempre
    para recuperar el control, y entonces "el proceso terminó bien" nunca se
    probaría.
    """

    worker_id: str = field(default_factory=default_worker_id)
    poll_interval: float = 1.0
    lease_seconds: int = 60
    heartbeat_interval: float = 20.0
    max_jobs: int | None = None
    idle_timeout: float | None = None
    bootstrap_periodic: bool = True
    #: Cada cuánto se vuelven a asegurar los periódicos (segundos). Una
    #: organización creada después de arrancar el worker —la primera de un
    #: despliegue nuevo, siempre— recibe así reaper, purgas y GC sin
    #: reiniciar nada. Barato e idempotente: lo resuelve el índice parcial
    #: (RD-14), igual que dos workers arrancando a la vez.
    bootstrap_interval: float = 300.0

    @classmethod
    def from_settings(cls, **overrides: Any) -> WorkerSettings:
        defaults: dict[str, Any] = {
            "poll_interval": float(settings.JOB_POLL_INTERVAL_SECONDS),
            "lease_seconds": int(settings.JOB_LEASE_SECONDS),
            "heartbeat_interval": float(settings.JOB_HEARTBEAT_INTERVAL_SECONDS),
            "bootstrap_interval": float(settings.JOB_BOOTSTRAP_INTERVAL_SECONDS),
        }
        defaults.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**defaults)


class _Heartbeat:
    """Extiende el lease en segundo plano mientras el handler trabaja."""

    def __init__(self, record: JobRecord, worker: WorkerSettings) -> None:
        self._record = record
        self._worker = worker
        self._stop = threading.Event()
        self._lease_lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{record.id}",
            daemon=True,
        )

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._worker.heartbeat_interval + 5.0)

    def _run(self) -> None:
        try:
            while not self._stop.wait(self._worker.heartbeat_interval):
                alive = queue.heartbeat(
                    job_id=self._record.id,
                    worker_id=self._worker.worker_id,
                    lease_seconds=self._worker.lease_seconds,
                )
                if not alive:
                    # El reaper (o un operador) nos quitó el job. Se deja de
                    # latir y se avisa: seguir latiendo sobre una fila ajena
                    # sería alargarle el lease a otro worker.
                    self._lease_lost.set()
                    logger.warning(
                        "Lease perdido durante la ejecución del job %s (%s).",
                        self._record.id,
                        self._record.type,
                    )
                    return
        finally:
            # Django abre una conexión por hilo; sin esto, un worker de larga
            # vida acumularía una conexión por job ejecutado.
            connection.close()


class Worker:
    """Un proceso consumidor de la cola. Sin estado propio fuera de PostgreSQL."""

    def __init__(self, config: WorkerSettings | None = None) -> None:
        self.config = config or WorkerSettings.from_settings()
        self.processed = 0
        self._stopping = False
        self._last_bootstrap: float | None = None

    # --- Ciclo de vida -------------------------------------------------------

    def request_stop(self, *_: object) -> None:
        """Parada ordenada: se acaba el job en curso y no se toma otro."""
        self._stopping = True

    def install_signal_handlers(self) -> None:
        """`SIGTERM`/`SIGINT` → parada ordenada.

        `SIGTERM` es lo que envía `docker stop` (y `docker compose down`) antes
        de matar el contenedor: atenderlo es lo que hace que un despliegue no
        deje jobs a medias esperando a que caduque su lease. `SIGKILL` no se
        puede atender, y no hace falta — para eso está el reaper.
        """
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, self._on_signal)

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        logger.info("Señal %s recibida: el worker terminará el job en curso y parará.", signum)
        self.request_stop()

    # --- Bucle ---------------------------------------------------------------

    def run(self) -> int:
        """Consume la cola hasta que toque parar. Devuelve cuántos jobs ejecutó."""
        created = self.maybe_bootstrap(now=time.monotonic())
        if self.config.bootstrap_periodic:
            logger.info("Periódicos asegurados al arrancar: %s nuevos.", created)

        idle_since: float | None = None
        while not self._stopping:
            self.maybe_bootstrap(now=time.monotonic())
            if self.run_once():
                idle_since = None
                if self.config.max_jobs is not None and self.processed >= self.config.max_jobs:
                    break
                continue

            now = time.monotonic()
            idle_since = now if idle_since is None else idle_since
            if (
                self.config.idle_timeout is not None
                and now - idle_since >= self.config.idle_timeout
            ):
                break
            time.sleep(self.config.poll_interval)
        return self.processed

    def maybe_bootstrap(self, *, now: float) -> int:
        """Asegura los periódicos si toca (o si nunca se hizo). Devuelve los creados.

        `now` es un reloj monótono (`time.monotonic()`), inyectable para probar
        la cadencia sin dormir.
        """
        if not self.config.bootstrap_periodic:
            return 0
        last = self._last_bootstrap
        if last is not None and now - last < self.config.bootstrap_interval:
            return 0
        self._last_bootstrap = now
        created = periodic.bootstrap()
        if created and last is not None:
            logger.info("Periódicos asegurados para organizaciones nuevas: %s.", created)
        return created

    def run_once(self) -> bool:
        """Toma y ejecuta como mucho un job. `False` si la cola estaba vacía."""
        record = queue.take(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
        )
        if record is None:
            return False
        self._execute(record)
        self.processed += 1
        return True

    # --- Ejecución de un job -------------------------------------------------

    def _execute(self, record: JobRecord) -> None:
        handler = handler_for(record.type)
        if handler is None:
            # Sin handler no hay reintento que valga: ver `mark_dead_letter`.
            logger.error("Job %s de tipo desconocido %r.", record.id, record.type)
            queue.mark_dead_letter(
                job_id=record.id,
                worker_id=self.config.worker_id,
                error={
                    "code": JOB_FAILED_CODE,
                    "message": "No handler is registered for this job type.",
                    "details": {"type": record.type},
                },
            )
            self._schedule_next_occurrence(record)
            return

        logger.info("Ejecutando job %s (%s), intento %s.", record.id, record.type, record.attempts)
        with _Heartbeat(record, self.config) as beat:
            try:
                output = handler(record)
            except Exception as exc:  # noqa: BLE001 — un handler puede lanzar cualquier cosa
                self._record_failure(record, exc, lease_lost=beat.lease_lost)
            else:
                self._record_success(
                    record,
                    dict(output) if output is not None else None,
                    lease_lost=beat.lease_lost,
                )
        self._schedule_next_occurrence(record)

    def _record_success(
        self,
        record: JobRecord,
        output: dict[str, Any] | None,
        *,
        lease_lost: bool,
    ) -> None:
        if lease_lost:
            logger.warning(
                "Job %s terminó bien pero su lease ya no era nuestro: no se escribe el "
                "resultado (otro worker lo está reprocesando).",
                record.id,
            )
            return
        if not queue.succeed(
            job_id=record.id, worker_id=self.config.worker_id, output=output
        ):
            logger.warning("El resultado del job %s no se pudo escribir (lease ajeno).", record.id)

    def _record_failure(self, record: JobRecord, exc: Exception, *, lease_lost: bool) -> None:
        # `scrub_text` sobre el traceback **antes** de que salga de este
        # proceso, ni siquiera a un log: es donde acaba una cadena de conexión
        # con contraseña cuando falla la conexión (H-17 en su forma literal).
        # `queue.fail` vuelve a pasarlo por `scrub()` — la puerta única de la
        # columna—, y esta llamada de más no sobra: aquí es donde se decide
        # además cuánto traceback se guarda.
        detail = scrub_text("".join(traceback.format_exception(exc))[-MAX_TRACEBACK_CHARS:])
        message = scrub_text(f"{type(exc).__name__}: {exc}")
        logger.warning("Job %s (%s) falló: %s", record.id, record.type, message)

        if lease_lost:
            logger.warning(
                "Job %s falló con el lease ya perdido: no se escribe el error.", record.id
            )
            return

        status = queue.fail(
            job_id=record.id,
            worker_id=self.config.worker_id,
            error={
                "code": JOB_FAILED_CODE,
                "message": message,
                "details": {"exception": type(exc).__name__, "attempt": record.attempts},
            },
            output={"failed_at_attempt": record.attempts, "traceback": detail},
        )
        if status is None:
            logger.warning("El fallo del job %s no se pudo escribir (lease ajeno).", record.id)

    def _schedule_next_occurrence(self, record: JobRecord) -> None:
        """Recurrencia de los periódicos: la siguiente ocurrencia, ya.

        Se hace **después** del estado terminal a propósito — mientras el job
        está `running` ocupa su clave de idempotencia y el `INSERT` no haría
        nada (ver `periodic.schedule_next`). Un job que sigue `pending` tras
        fallar (le quedan intentos) tampoco genera ocurrencia nueva por la
        misma razón, que es la correcta: la ocurrencia de este ciclo todavía no
        ha terminado.
        """
        if periodic.periodic_for(record.type) is None:
            return
        periodic.schedule_next(org_id=record.org_id, job_type=record.type, now=timezone.now())

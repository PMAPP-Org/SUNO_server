"""`python manage.py run_worker` — el proceso consumidor de la cola (ADR-006).

Tarea 1 del Paso 19, literal: *"mismo código base que la API, proceso
separado"*. Un comando de gestión de Django es exactamente eso — arranca el
mismo `settings`, los mismos modelos y la misma configuración de logging que
`runserver`, sin duplicar bootstrap ni inventar un entrypoint paralelo que
mañana se desincronizaría.

**Por qué el comando vive en `visitacampo/management/` y la lógica en
`visitacampo/jobs/`**: Django descubre los comandos de cualquier app instalada,
y `visitacampo` ya lo está (`INSTALLED_APPS`), así que el paquete `jobs` no
necesita convertirse en app solo para esto. Este archivo es una capa fina de
CLI: parsear argumentos, construir `WorkerSettings` y ceder el control a
`Worker.run()`.

**Sin docker-in-docker (N-02, doc 08 §7.2)**: esto es un proceso Python. No
monta `/var/run/docker.sock`, no lanza contenedores y no lo necesita — la razón
de QFieldCloud para hacerlo (aislar QGIS headless) no existe aquí.

Uso típico::

    python manage.py run_worker                       # producción: no termina
    python manage.py run_worker --max-jobs 1          # ejecuta uno y sale
    python manage.py run_worker --idle-timeout 5      # sale tras 5 s sin trabajo
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from visitacampo.jobs.worker import Worker, WorkerSettings, default_worker_id


class Command(BaseCommand):
    help = "Consume la cola de trabajos (SKIP LOCKED + lease + heartbeat, ADR-006)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--worker-id",
            default=None,
            help=(
                "Identificador del worker en `job.locked_by`. Por defecto "
                "`host/pid/sufijo`; solo hay que fijarlo si se quiere reconocer "
                "un worker concreto en la tabla."
            ),
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=None,
            help="Segundos entre sondeos cuando la cola está vacía (JOB_POLL_INTERVAL_SECONDS).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=int,
            default=None,
            help=(
                "Duración del lease al tomar un job (JOB_LEASE_SECONDS). Es el "
                "tiempo máximo que un job puede quedar bloqueado tras la muerte "
                "abrupta del worker, antes de que el reaper lo recupere."
            ),
        )
        parser.add_argument(
            "--heartbeat-interval",
            type=float,
            default=None,
            help="Segundos entre extensiones del lease (JOB_HEARTBEAT_INTERVAL_SECONDS).",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Termina tras ejecutar N jobs. Sin esto, el worker no termina solo.",
        )
        parser.add_argument(
            "--idle-timeout",
            type=float,
            default=None,
            help="Termina tras N segundos sin nada que hacer. Útil en tests y en one-shots.",
        )
        parser.add_argument(
            "--no-bootstrap-periodic",
            action="store_true",
            help=(
                "No asegura los jobs periódicos al arrancar. Solo para depurar: "
                "sin ellos no hay reaper, y un job huérfano no se recupera."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        config = WorkerSettings.from_settings(
            worker_id=options["worker_id"] or default_worker_id(),
            poll_interval=options["poll_interval"],
            lease_seconds=options["lease_seconds"],
            heartbeat_interval=options["heartbeat_interval"],
            max_jobs=options["max_jobs"],
            idle_timeout=options["idle_timeout"],
            bootstrap_periodic=not options["no_bootstrap_periodic"],
        )
        worker = Worker(config)
        worker.install_signal_handlers()
        self.stdout.write(f"worker {config.worker_id}: consumiendo la cola…")
        processed = worker.run()
        self.stdout.write(self.style.SUCCESS(f"worker {config.worker_id}: {processed} jobs."))

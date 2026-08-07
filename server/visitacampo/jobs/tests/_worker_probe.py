"""Worker de verdad, en un proceso de verdad, para el test de `kill -9`.

**No es un módulo de test** (el prefijo `_` lo mantiene fuera de la colección
de pytest): es el script que `test_kill9.py` lanza con `subprocess.Popen`. Vive
aquí, junto al test que lo usa, porque su único motivo de existir es ese test
— y porque el plan del Paso 19 lo autoriza explícitamente ("un script auxiliar
invocado por el test").

Lo que aporta sobre `manage.py run_worker` es **un solo** handler de prueba
(`test.recoverable`), que no tiene sitio en el código de producción: un tipo de
job cuyo primer intento se queda colgado para siempre y cuyo segundo intento
termina al instante. Esa asimetría es lo que hace observable la recuperación:
si el job acaba en `succeeded` con `attempts = 2`, es que alguien lo devolvió a
la cola después de que su worker muriera.

Todo lo demás —tomar con `SKIP LOCKED`, el lease, el heartbeat, el reaper, la
escritura del resultado— es el código de producción tal cual
(`visitacampo.jobs.worker.Worker`), sin un solo doble.

Uso (lo invoca el test, no una persona)::

    python -m visitacampo.jobs.tests._worker_probe --lease-seconds 3 --max-jobs 1
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Tipo del job de prueba. Se registra **solo** en este proceso: el backend en
#: producción no lo conoce, y un job de este tipo encolado ahí acabaría en
#: `dead_letter` por falta de handler, que es lo correcto.
RECOVERABLE = "test.recoverable"


def _bootstrap_django() -> None:
    """`django.setup()` con el mismo `settings` que la API (ADR-006)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    import django

    django.setup()


def _register_probe_handler() -> None:
    from visitacampo.jobs.queue import JobRecord
    from visitacampo.jobs.registry import register

    @register(RECOVERABLE)
    def _recoverable(record: JobRecord) -> Mapping[str, Any]:
        """Primer intento: no vuelve nunca. Siguientes: termina al instante.

        Escribe el "marcador" antes de dormirse para que el test sepa —sin
        adivinar ni dormir a ojo— que el handler ya está dentro, y solo
        entonces mate el proceso. Matarlo antes probaría otra cosa (un worker
        que muere sin haber empezado el trabajo).
        """
        marker = Path(record.payload["marker"])
        marker.write_text(str(record.attempts), encoding="utf-8")
        if record.attempts <= 1:
            # El test manda un SIGKILL/`taskkill /F` aquí en medio. No hay
            # `finally`, ni cierre de conexión, ni escritura de estado: esa es
            # exactamente la situación que el lease existe para sobrevivir.
            time.sleep(600)
        return {"recovered_on_attempt": record.attempts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--heartbeat-interval", type=float, default=20.0)
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--idle-timeout", type=float, default=None)
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args(argv)

    _bootstrap_django()
    _register_probe_handler()

    from visitacampo.jobs.worker import Worker, WorkerSettings, default_worker_id

    worker = Worker(
        WorkerSettings(
            worker_id=args.worker_id or default_worker_id(),
            poll_interval=args.poll_interval,
            lease_seconds=args.lease_seconds,
            heartbeat_interval=args.heartbeat_interval,
            max_jobs=args.max_jobs,
            idle_timeout=args.idle_timeout,
            bootstrap_periodic=args.bootstrap,
        )
    )
    processed = worker.run()
    print(f"probe: {processed} jobs", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

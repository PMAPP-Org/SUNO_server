"""Los tres periódicos reales del MVP, y su recurrencia (Paso 19, tareas 4 y 9).

ADR-006: *"Periódicos con el mismo mecanismo (`run_after`), **sin Celery Beat
ni cron externo tipo `ofelia`**"*. Aquí no hay un planificador aparte: un
periódico es una fila `job` como cualquier otra, y la recurrencia es que al
terminar se encola la siguiente ocurrencia con `run_after = ahora + intervalo`.

Los tres:

1. `queue.reap` — el **reaper** de la tarea 4. Devuelve a `pending` los jobs
   cuyo lease venció (RD-13/H-12).
2. `sync.purge_tombstones` — la purga de tombstones que el Paso 9 dejó escrita
   y sin invocar (`sync/retention.purge_expired_tombstones`).
3. `attachments.gc_unconfirmed_uploads` — el GC de blobs subidos y nunca
   confirmados que el Paso 17 dejó igual
   (`attachments/services.collect_unconfirmed_uploads`).

Ninguno de los tres reimplementa nada: el Paso 19 aporta **cuándo** se llaman,
que es exactamente donde los pasos 9 y 17 pusieron la frontera.

---

**Backups (`pg_dump` cifrado → R2): fuera del alcance de este paso.** La tarea
9 del plan lo dejaba a decidir y esta es la decisión, escrita aquí y no solo en
el seguimiento. Motivo: un backup no es trabajo de aplicación sino de
despliegue. Necesita el binario `pg_dump` en la imagen, credenciales de R2 que
el proceso `worker` **no** tiene hoy (y que doc 08 N-16 prohíbe pasar por el
`payload` del job), una política de retención y rotación, y —sobre todo— una
verificación de restauración: un backup que nadie ha restaurado nunca no es un
backup. Meterlo aquí produciría un job que "pasa los tests" sin que nadie haya
comprobado jamás que el volcado se puede restaurar, que es la peor forma
posible de tener backups. Va al Paso 24/25 (operación y cierre de MVP), con la
tarea de restauración incluida. Lo que este paso sí deja hecho es el mecanismo:
cuando exista, será otro `PeriodicJob` de tres líneas en esta misma tupla.

---

**Sobre el alcance de cada periódico y `org_id`.** ADR-011 exige `org_id` en
toda fila de negocio y `job` no es excepción, así que los tres se encolan **una
vez por organización**. Para la purga de tombstones eso es literalmente
correcto (opera sobre los datos de una organización, vía `system_context`).
Para el reaper y para el GC de blobs no: los dos barren algo que no es de nadie
—leases de workers, objetos del bucket, que `Blob` no lleva `org_id` a
propósito (RD-08)— y su `org_id` es solo la atribución que exige ADR-011. En el
MVP hay **una** organización (S-09), así que hay exactamente un reaper y un GC.
Con varias, las pasadas de más son idempotentes y baratas (un `UPDATE` que no
casa nada, un `LIST` que no encuentra basura); si algún día dejaran de serlo,
lo que hay que cambiar es a qué organización se atribuyen, no lo que hacen.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from visitacampo.attachments.services import collect_unconfirmed_uploads
from visitacampo.core.context import system_context
from visitacampo.jobs import queue
from visitacampo.jobs.queue import JobRecord
from visitacampo.jobs.registry import register
from visitacampo.sync.retention import purge_expired_tombstones

__all__ = [
    "ATTACHMENT_GC",
    "PERIODIC_JOBS",
    "PURGE_TOMBSTONES",
    "QUEUE_REAP",
    "PeriodicJob",
    "bootstrap",
    "periodic_for",
    "schedule_next",
]

QUEUE_REAP = "queue.reap"
PURGE_TOMBSTONES = "sync.purge_tombstones"
ATTACHMENT_GC = "attachments.gc_unconfirmed_uploads"


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    """Definición de un periódico: qué tipo, cada cuánto y con cuántos intentos."""

    type: str
    interval: timedelta
    max_attempts: int

    @property
    def idempotency_key(self) -> str:
        """La clave del invariante "un job activo por tipo/entidad" (RD-14).

        Es el propio tipo: la "entidad" ya está en la primera columna del
        índice parcial (`org_id`), así que `(org_id, "queue.reap")` identifica
        el trabajo lógico "el reaper de esta organización". Encolar dos reapers
        activos a la vez no tiene sentido —el segundo no encontraría nada que
        hacer que el primero no fuera a hacer— y el índice lo impide sin que
        nadie tenga que comprobarlo antes.
        """
        return self.type


#: Los periódicos del MVP. El reaper con el intervalo más corto porque es la
#: red de seguridad de todos los demás: cuanto más tarde en pasar, más tiempo
#: puede quedarse un job huérfano tras la muerte de un worker (RD-13).
PERIODIC_JOBS: tuple[PeriodicJob, ...] = (
    PeriodicJob(
        type=QUEUE_REAP,
        interval=timedelta(seconds=settings.JOB_REAP_INTERVAL_SECONDS),
        # Un reaper que falla y se rinde deja el sistema sin red de seguridad,
        # así que se le dan más intentos que a nadie.
        max_attempts=10,
    ),
    PeriodicJob(
        type=PURGE_TOMBSTONES,
        interval=timedelta(seconds=settings.JOB_PURGE_TOMBSTONES_INTERVAL_SECONDS),
        max_attempts=3,
    ),
    PeriodicJob(
        type=ATTACHMENT_GC,
        interval=timedelta(seconds=settings.JOB_ATTACHMENT_GC_INTERVAL_SECONDS),
        max_attempts=3,
    ),
)

_BY_TYPE: dict[str, PeriodicJob] = {periodic.type: periodic for periodic in PERIODIC_JOBS}


def periodic_for(job_type: str) -> PeriodicJob | None:
    return _BY_TYPE.get(job_type)


# --- Encolado y recurrencia --------------------------------------------------


def bootstrap(now: datetime | None = None) -> int:
    """Asegura que cada organización tiene sus periódicos encolados. Idempotente.

    Se llama al arrancar un worker. Devuelve cuántas filas se crearon de verdad
    — con dos workers arrancando a la vez, uno creará N y el otro 0, y ninguno
    de los dos habrá tenido que preguntar antes si existían: eso lo decide el
    índice parcial (RD-14, y es el caso que prueba `test_toctou.py`).

    Los periódicos nuevos arrancan con `run_after = ahora`: el primer reaper
    debe correr ya, no dentro de un intervalo. Las ocurrencias siguientes las
    encola `schedule_next` con la espera correspondiente.
    """
    moment = now or timezone.now()
    created = 0
    for org_id in queue.organization_ids():
        for periodic in PERIODIC_JOBS:
            job_id = queue.enqueue(
                org_id=org_id,
                job_type=periodic.type,
                idempotency_key=periodic.idempotency_key,
                run_after=moment,
                max_attempts=periodic.max_attempts,
                now=moment,
            )
            created += int(job_id is not None)
    return created


def schedule_next(
    *,
    org_id: uuid.UUID,
    job_type: str,
    now: datetime | None = None,
) -> uuid.UUID | None:
    """Encola la siguiente ocurrencia de un periódico ya terminado.

    Lo llama `worker.py` **después** de que el job alcance un estado terminal,
    nunca antes: mientras la ocurrencia actual sigue `running` ocupa la clave
    de idempotencia, y este `INSERT` no haría nada.

    Se encola también cuando la ocurrencia acabó en `dead_letter`. Puede sonar
    raro, pero la alternativa es peor: un periódico que se rinde para siempre
    porque una vez falló cinco veces seguidas deja de purgar tombstones o de
    reapear leases **en silencio**, y nadie se entera hasta que el disco se
    llena. El `dead_letter` queda como registro visible en el Admin (Paso 24) y
    la función sigue ejecutándose.
    """
    periodic = periodic_for(job_type)
    if periodic is None:
        return None
    moment = now or timezone.now()
    return queue.enqueue(
        org_id=org_id,
        job_type=periodic.type,
        idempotency_key=periodic.idempotency_key,
        run_after=moment + periodic.interval,
        max_attempts=periodic.max_attempts,
        now=moment,
    )


# --- Los handlers ------------------------------------------------------------


@register(QUEUE_REAP)
def run_reaper(record: JobRecord) -> Mapping[str, Any]:
    """Tarea 4: el reaper, ejecutado **como job del mismo mecanismo**.

    Que el reaper sea un job de la propia cola es lo que pide ADR-006, y tiene
    una consecuencia que conviene mirar de frente: si el worker muere
    ejecutando *el reaper*, el reaper queda huérfano y no hay nadie que lo
    recupere hasta que otro worker arranque. Por eso `worker.py` llama a
    `periodic.bootstrap()` al arrancar: cualquier worker que se levante repone
    el reaper si falta. En un despliegue con `restart: unless-stopped`
    (`infra/docker-compose.yml`) eso ocurre solo.
    """
    report = queue.reap_expired_leases()
    return {"requeued": report.requeued, "dead_lettered": report.dead_lettered}


@register(PURGE_TOMBSTONES)
def run_purge_tombstones(record: JobRecord) -> Mapping[str, Any]:
    """Tarea 9: la purga de tombstones del Paso 9, ya invocada de verdad.

    `purge_expired_tombstones` pide un `RequestContext` porque su alcance es
    una organización (`scoped_qs`), y su docstring dejó dicho que "el Paso 19
    es quien decide … con qué `RequestContext` (o su equivalente de sistema)
    invoca esta función por cada una". Este es ese equivalente:
    `system_context(record.org_id)`, sin ningún rol (ver su docstring en
    `core/context.py`).
    """
    purged = purge_expired_tombstones(system_context(record.org_id))
    return {"purged": purged}


@register(ATTACHMENT_GC)
def run_attachment_gc(record: JobRecord) -> Mapping[str, Any]:
    """Tarea 9: el GC de subidas nunca confirmadas del Paso 17 (ADR-007).

    Se emite el **recuento** de objetos borrados y no la lista de `sha256`:
    caben miles y `job.output` se lee desde una API. Los hashes no son secretos
    (quien conoce uno ya tiene los bytes que lo producen, ver
    `scoping.blob_registry`), así que esto es economía, no redacción.
    """
    report = collect_unconfirmed_uploads()
    return {
        "inspected": report.inspected,
        "deleted": len(report.deleted),
        "reclaimed_bytes": report.reclaimed_bytes,
    }

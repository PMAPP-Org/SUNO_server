"""Las sentencias de la cola. **Una sentencia por transición**, sin read-then-write.

ADR-006 al pie de la letra: `SELECT ... FOR UPDATE SKIP LOCKED` para tomar,
lease (`locked_by`/`locked_until`) para no perder un job cuando el worker
muere, heartbeat para los largos, reaper para los leases vencidos, `dead_letter`
al agotar `max_attempts` y unicidad lógica con un **constraint único parcial**.

**Por qué SQL a mano y no el ORM**, exactamente el mismo argumento que
`sync/sql.py` documenta para el CAS del push, aplicado aquí:

- El `take` tiene que ser *una* sentencia. Con el ORM serían dos (`select_for_
  update(skip_locked=True)` y después `.update()`), y entre ambas cabe justo la
  ventana que el patrón existe para eliminar. Aquí es un `UPDATE ... FROM (CTE
  con FOR UPDATE SKIP LOCKED)`: PostgreSQL bloquea, salta lo bloqueado, marca y
  devuelve la fila en una sola operación atómica.
- El encolado idempotente es `INSERT ... ON CONFLICT (org_id, idempotency_key)
  WHERE status IN (...) DO NOTHING`, que Django no sabe expresar (`bulk_create
  (ignore_conflicts=True)` no permite inferir un índice **parcial**). Y es
  justo el índice parcial lo que hace que no haya double-bookkeeping (RD-14):
  la unicidad la decide PostgreSQL, no un `if` en Python.
- Toda transición de estado lleva su guarda **dentro** de la sentencia
  (`WHERE ... AND locked_by = %s AND status = 'running'`). Un worker cuyo lease
  ya reapeó el reaper escribe cero filas y se entera; no pisa el trabajo del
  worker que heredó el job.

Ningún valor se interpola en el texto: todo va por parámetros (`%s`). Los
identificadores salen de `Job._meta`, así que renombrar un campo del modelo
rompe los tests en vez de generar SQL contra una columna que ya no existe.

**Sobre el alcance por organización.** Este módulo es el único del backend que
consulta una tabla de negocio sin `scoped_qs`, y es deliberado: un worker no
pertenece a ninguna organización — atiende la cola entera del despliegue. El
aislamiento de ADR-011 no desaparece, se traslada: `job.org_id` viaja en el
`JobRecord` y el *handler* construye con él un `system_context(org_id)`
(`core/context.py`) antes de tocar ningún dato de negocio, de modo que la
lógica de dominio sigue leyendo por la puerta de siempre. Como aquí no se
toca ningún manager del ORM, el test de arquitectura (`core/tests/
test_architecture.py`) no necesita ninguna exención nueva para este paquete.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import connection
from django.utils import timezone

from visitacampo.core.models import ACTIVE_JOB_STATUSES, Job, JobStatus, Organization
from visitacampo.core.scrub import scrub

__all__ = [
    "JobRecord",
    "ReapReport",
    "backoff_seconds",
    "enqueue",
    "fail",
    "heartbeat",
    "mark_dead_letter",
    "organization_ids",
    "reap_expired_leases",
    "succeed",
    "take",
]


# --- Identificadores, resueltos contra el modelo ----------------------------


def _col(field_name: str) -> str:
    """Columna citada de un campo de `Job` (`org` → `"org_id"`)."""
    return connection.ops.quote_name(Job._meta.get_field(field_name).column)


def _table() -> str:
    return connection.ops.quote_name(Job._meta.db_table)


def _active_statuses_sql() -> tuple[str, tuple[str, ...]]:
    """`IN (%s, %s)` y sus parámetros, para el predicado del índice parcial."""
    placeholders = ", ".join(["%s"] * len(ACTIVE_JOB_STATUSES))
    return f"IN ({placeholders})", tuple(ACTIVE_JOB_STATUSES)


# --- Lo que el worker recibe -------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobRecord:
    """La fila tomada, congelada. Es **todo** lo que un handler puede ver.

    Deliberadamente sin `locked_by`/`locked_until`/`output`/`error`: el lease
    lo administra el worker y la salida la escribe él. Un handler que pudiera
    tocar esos campos podría, sin querer, invalidar la guarda de la sentencia
    que después escribe su resultado.

    `attempts` **ya viene incrementado** (el `take` lo sube en la misma
    sentencia), así que vale 1 en la primera ejecución. Los handlers lo usan
    para decidir si esto es un reintento — el de la recuperación tras `kill -9`
    lo hace explícitamente.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int

    @property
    def is_retry(self) -> bool:
        return self.attempts > 1


@dataclass(frozen=True, slots=True)
class ReapReport:
    """Qué hizo una pasada del reaper. Va tal cual a `job.output`."""

    requeued: int
    dead_lettered: int

    @property
    def total(self) -> int:
        return self.requeued + self.dead_lettered


# --- Back-off ----------------------------------------------------------------


def backoff_seconds(attempts: int) -> float:
    """Espera exponencial acotada antes del siguiente intento.

    `base * 2^(attempts-1)`, con techo. Sin *jitter* a propósito: el jitter
    sirve para dispersar una estampida de clientes, y aquí los consumidores son
    uno o dos workers que además se reparten el trabajo con `SKIP LOCKED` — el
    problema que resolvería no existe (S-01/S-02, doc 08).
    """
    base = float(settings.JOB_BACKOFF_BASE_SECONDS)
    cap = float(settings.JOB_BACKOFF_MAX_SECONDS)
    exponent = max(0, min(attempts - 1, 16))
    return min(base * (2.0**exponent), cap)


def _backoff_expression() -> tuple[str, tuple[Any, ...]]:
    """El back-off **calculado en la BD**, porque depende de `attempts` por fila.

    El reaper toca N filas con distinto número de intentos en una sola
    sentencia; traérselas a Python para calcular una fecha por fila sería
    volver al read-then-write que este módulo evita.
    """
    return (
        "%s::timestamptz + make_interval("
        "secs => LEAST(%s * POWER(2, LEAST(GREATEST(attempts - 1, 0), 16)), %s))",
        (
            float(settings.JOB_BACKOFF_BASE_SECONDS),
            float(settings.JOB_BACKOFF_MAX_SECONDS),
        ),
    )


# --- Encolar -----------------------------------------------------------------


def enqueue(
    *,
    org_id: uuid.UUID,
    job_type: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
    now: datetime | None = None,
) -> uuid.UUID | None:
    """Encola un job. Devuelve su `id`, o `None` si ya había uno activo igual.

    `None` **no es un error**: es la respuesta correcta a "asegúrate de que hay
    un reaper encolado" cuando ya lo hay. Quien llama no tiene que consultar
    antes (no debe: esa consulta previa sería la mitad TOCTOU del bug H-13);
    llama siempre y PostgreSQL decide con el índice parcial
    `job_active_idempotency_uq`.

    Con `idempotency_key=None` nunca hay conflicto —los `NULL` son distintos
    entre sí en un índice único de PostgreSQL— y el job se crea siempre. Es lo
    que quiere un job de usuario (dos exportaciones pedidas a mano son dos
    trabajos), no un periódico.
    """
    moment = now or timezone.now()
    condition, condition_params = _active_statuses_sql()
    job_id = uuid.uuid4()
    columns = (
        "id",
        "org",
        "type",
        "status",
        "payload",
        "attempts",
        "max_attempts",
        "run_after",
        "locked_by",
        "locked_until",
        "output",
        "error",
        "created_at",
        "finished_at",
        "idempotency_key",
    )
    sql = (
        f"INSERT INTO {_table()} ({', '.join(_col(name) for name in columns)}) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, 0, %s, %s, NULL, NULL, NULL, NULL, %s, NULL, %s) "
        f"ON CONFLICT ({_col('org')}, {_col('idempotency_key')}) "
        f"WHERE {_col('status')} {condition} DO NOTHING "
        f"RETURNING {_col('id')}"
    )
    params: tuple[Any, ...] = (
        job_id,
        org_id,
        job_type,
        JobStatus.PENDING,
        json.dumps(scrub(payload or {})),
        int(max_attempts if max_attempts is not None else settings.JOB_MAX_ATTEMPTS),
        run_after if run_after is not None else moment,
        moment,
        idempotency_key,
        *condition_params,
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return row[0] if row else None


# --- Tomar (el corazón del paso) --------------------------------------------


def take(*, worker_id: str, lease_seconds: int, now: datetime | None = None) -> JobRecord | None:
    """Toma **un** job pendiente y le pone lease. `None` si no hay nada que hacer.

    La sentencia, en una pieza::

        WITH candidate AS (
            SELECT id FROM job
             WHERE status = 'pending' AND (run_after IS NULL OR run_after <= %s)
             ORDER BY COALESCE(run_after, created_at), created_at
             FOR UPDATE SKIP LOCKED
             LIMIT 1
        )
        UPDATE job SET status='running', locked_by=%s, locked_until=%s,
                       attempts = attempts + 1
          FROM candidate WHERE job.id = candidate.id
        RETURNING ...

    `SKIP LOCKED` es lo que permite tener N workers sin coordinación externa: el
    segundo no espera al primero ni falla, simplemente ignora las filas que el
    primero ya bloqueó y se lleva la siguiente. Sin él, dos workers se
    serializarían en la misma fila (y con `NOWAIT`, uno moriría).

    `attempts` sube **al tomar** y no al fallar, y esa es la diferencia entre
    RD-13 y el bug que corrige: un worker que muere de `kill -9` no llega nunca
    a "fallar", así que un contador que solo subiera en el camino de error
    dejaría el job reintentándose para siempre — exactamente H-12.

    El orden `COALESCE(run_after, created_at)` hace que un job con back-off
    espere su turno y que, a igualdad de fecha, gane el más antiguo: FIFO sin
    inanición.
    """
    moment = now or timezone.now()
    until = moment + timedelta(seconds=lease_seconds)
    sql = (
        f"WITH candidate AS ("
        f" SELECT {_col('id')} FROM {_table()}"
        f" WHERE {_col('status')} = %s"
        f" AND ({_col('run_after')} IS NULL OR {_col('run_after')} <= %s)"
        f" ORDER BY COALESCE({_col('run_after')}, {_col('created_at')}), {_col('created_at')}"
        f" FOR UPDATE SKIP LOCKED LIMIT 1"
        f") "
        f"UPDATE {_table()} AS j SET "
        f"{_col('status')} = %s, {_col('locked_by')} = %s, {_col('locked_until')} = %s, "
        f"{_col('attempts')} = j.{_col('attempts')} + 1 "
        f"FROM candidate c WHERE j.{_col('id')} = c.{_col('id')} "
        f"RETURNING j.{_col('id')}, j.{_col('org')}, j.{_col('type')}, j.{_col('payload')}, "
        f"j.{_col('attempts')}, j.{_col('max_attempts')}"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            (JobStatus.PENDING, moment, JobStatus.RUNNING, worker_id, until),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return JobRecord(
        id=row[0],
        org_id=row[1],
        type=row[2],
        payload=_decode_payload(row[3]),
        attempts=row[4],
        max_attempts=row[5],
    )


def _decode_payload(raw: object) -> dict[str, Any]:
    """`jsonb` → `dict`. Hace falta porque el cursor crudo **no** lo parsea.

    El backend de PostgreSQL de Django registra un cargador de texto para
    `jsonb` y deja la deserialización a `JSONField.from_db_value`, de modo que
    un `SELECT payload` por cursor devuelve la cadena JSON, no el objeto. Sin
    esto, un handler recibiría `payload` como `str` y el primer `record.
    payload["algo"]` fallaría con `TypeError` — un fallo que además solo
    aparecería en ejecución, nunca en el tipo.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    return dict(raw) if isinstance(raw, Mapping) else {}


# --- Lease vivo --------------------------------------------------------------


def heartbeat(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Extiende el lease. `False` si este worker ya **no** es el dueño del job.

    Ese `False` es la mitad que se suele olvidar del patrón: sin él, un worker
    cuyo lease expiró (una pausa larga del SO, un `docker pause`, un disco
    lento) seguiría trabajando mientras otro worker reprocesa el mismo job, y
    los dos escribirían resultado. Con la guarda `locked_by = %s AND status =
    'running'` dentro de la sentencia, el que perdió el lease se entera en el
    siguiente latido y `worker.py` aborta la escritura del resultado.
    """
    moment = now or timezone.now()
    sql = (
        f"UPDATE {_table()} SET {_col('locked_until')} = %s "
        f"WHERE {_col('id')} = %s AND {_col('locked_by')} = %s AND {_col('status')} = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            (moment + timedelta(seconds=lease_seconds), job_id, worker_id, JobStatus.RUNNING),
        )
        return cursor.rowcount == 1


# --- Terminar ----------------------------------------------------------------


def succeed(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    output: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Marca el job como `succeeded`. `output` pasa por `scrub()` (RD-12).

    El scrubbing se hace **aquí** y no en cada handler a propósito: es el único
    punto por el que una salida puede llegar a la columna, así que un handler
    nuevo no puede olvidarse de redactar. Es la misma decisión de diseño que
    `scoped_qs` para el aislamiento: una puerta, no una convención.
    """
    moment = now or timezone.now()
    sql = (
        f"UPDATE {_table()} SET {_col('status')} = %s, {_col('output')} = %s::jsonb, "
        f"{_col('error')} = NULL, {_col('finished_at')} = %s, "
        f"{_col('locked_by')} = NULL, {_col('locked_until')} = NULL "
        f"WHERE {_col('id')} = %s AND {_col('locked_by')} = %s AND {_col('status')} = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            (
                JobStatus.SUCCEEDED,
                json.dumps(scrub(output)) if output is not None else None,
                moment,
                job_id,
                worker_id,
                JobStatus.RUNNING,
            ),
        )
        return cursor.rowcount == 1


def fail(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    error: dict[str, Any],
    output: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str | None:
    """El job falló: vuelve a `pending` con back-off, o cae a `dead_letter`.

    Devuelve el estado resultante, o `None` si la fila ya no era de este worker.

    **La decisión se toma en SQL** (`CASE WHEN attempts >= max_attempts`) y no
    en Python porque el valor de `attempts` que importa es el de la fila en ese
    instante, no el que este proceso leyó al tomarla — entre medias el reaper
    pudo haber intervenido.

    Sobre el umbral: ADR-006 lo enuncia como "`attempts > max_attempts` →
    `dead_letter`". Aquí se compara `>=` porque `attempts` se incrementa **al
    tomar**, así que en el momento de fallar ya cuenta el intento en curso: con
    `>=`, un job con `max_attempts = 5` se ejecuta exactamente cinco veces
    (`attempts` llega a 5 y muere), mientras que con `>` se ejecutaría seis. Se
    elige lo que el nombre del campo promete.

    Tanto `error` como `output` pasan por `scrub()`: el traceback de un job que
    reventó al conectarse a algo es el sitio más probable donde aparece una
    cadena de conexión con contraseña, que es la regresión literal de **H-17**.
    """
    moment = now or timezone.now()
    backoff_sql, backoff_params = _backoff_expression()
    exhausted = f"j.{_col('attempts')} >= j.{_col('max_attempts')}"
    sql = (
        f"UPDATE {_table()} AS j SET "
        f"{_col('status')} = CASE WHEN {exhausted} THEN %s ELSE %s END, "
        f"{_col('output')} = %s::jsonb, "
        f"{_col('error')} = %s::jsonb, "
        f"{_col('finished_at')} = CASE WHEN {exhausted} THEN %s ELSE NULL END, "
        f"{_col('run_after')} = CASE WHEN {exhausted} THEN j.{_col('run_after')} "
        f"ELSE {backoff_sql} END, "
        f"{_col('locked_by')} = NULL, {_col('locked_until')} = NULL "
        f"WHERE j.{_col('id')} = %s AND j.{_col('locked_by')} = %s AND j.{_col('status')} = %s "
        f"RETURNING j.{_col('status')}"
    )
    params: tuple[Any, ...] = (
        JobStatus.DEAD_LETTER,
        JobStatus.PENDING,
        json.dumps(scrub(output)) if output is not None else None,
        json.dumps(scrub(error)),
        moment,
        moment,
        *backoff_params,
        job_id,
        worker_id,
        JobStatus.RUNNING,
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return row[0] if row else None


def mark_dead_letter(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    error: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """A `dead_letter` **sin gastar reintentos**: el fallo no es transitorio.

    Se usa en un solo caso, y es el que importa: un `job.type` para el que no
    hay handler registrado. Reintentarlo cinco veces con back-off exponencial
    no lo va a arreglar —el código que falta no aparece por esperar— y mientras
    tanto el job vuelve a la cola una y otra vez ocupando al worker. Va directo
    a `dead_letter`, que es donde el Admin del Paso 24 lo hace visible.
    """
    moment = now or timezone.now()
    sql = (
        f"UPDATE {_table()} SET {_col('status')} = %s, {_col('error')} = %s::jsonb, "
        f"{_col('finished_at')} = %s, {_col('locked_by')} = NULL, {_col('locked_until')} = NULL "
        f"WHERE {_col('id')} = %s AND {_col('locked_by')} = %s AND {_col('status')} = %s"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            (
                JobStatus.DEAD_LETTER,
                json.dumps(scrub(error)),
                moment,
                job_id,
                worker_id,
                JobStatus.RUNNING,
            ),
        )
        return cursor.rowcount == 1


# --- El reaper (RD-13, corrige H-12) ----------------------------------------

#: `Error` del contrato con el que muere un job cuyo worker desapareció y que
#: ya no tiene reintentos. Sin datos del proceso muerto (ni hostname ni PID):
#: es un cuerpo que acaba en una respuesta de API (RD-12).
LEASE_EXPIRED_ERROR: dict[str, Any] = {
    "code": "internal_error",
    "message": "The worker holding this job stopped reporting and no attempts are left.",
}


def reap_expired_leases(now: datetime | None = None) -> ReapReport:
    """Devuelve a `pending` los jobs cuyo lease venció. **La red de seguridad.**

    Un job en `running` con `locked_until` en el pasado significa exactamente
    una cosa: el proceso que lo tenía ya no está latiendo. Puede haberse ido de
    `kill -9`, puede haberse caído la máquina o puede que el contenedor se haya
    reiniciado — desde la base de datos son indistinguibles, y da igual, porque
    la acción correcta es la misma en los tres casos.

    Ese es el bug **H-12** en una línea: en la referencia, un job cuyo worker
    moría se quedaba "en curso" para siempre y nadie volvía a mirarlo. Aquí el
    lease caduca solo y esta sentencia lo recoge.

    El que ya agotó `max_attempts` no vuelve a `pending` sino a `dead_letter`:
    un job que tumba al worker cada vez que se ejecuta (un `payload` que
    dispara un OOM, por ejemplo) reintentado sin límite es una forma elegante
    de no tener servicio nunca.

    **Barre todas las organizaciones a la vez** aunque el job que la invoca
    pertenezca a una: un lease es una propiedad del *worker*, no de un tenant.
    El `org_id` de la fila `queue.reap` es solo la atribución que ADR-011 exige
    para toda fila de negocio (ver `periodic.py`).
    """
    moment = now or timezone.now()
    backoff_sql, backoff_params = _backoff_expression()
    exhausted = f"j.{_col('attempts')} >= j.{_col('max_attempts')}"
    sql = (
        f"UPDATE {_table()} AS j SET "
        f"{_col('status')} = CASE WHEN {exhausted} THEN %s ELSE %s END, "
        f"{_col('run_after')} = CASE WHEN {exhausted} THEN j.{_col('run_after')} "
        f"ELSE {backoff_sql} END, "
        f"{_col('finished_at')} = CASE WHEN {exhausted} THEN %s "
        f"ELSE j.{_col('finished_at')} END, "
        f"{_col('error')} = CASE WHEN {exhausted} THEN %s::jsonb ELSE j.{_col('error')} END, "
        f"{_col('locked_by')} = NULL, {_col('locked_until')} = NULL "
        f"WHERE j.{_col('status')} = %s AND j.{_col('locked_until')} IS NOT NULL "
        f"AND j.{_col('locked_until')} < %s "
        f"RETURNING j.{_col('status')}"
    )
    params: tuple[Any, ...] = (
        JobStatus.DEAD_LETTER,
        JobStatus.PENDING,
        moment,
        *backoff_params,
        moment,
        json.dumps(LEASE_EXPIRED_ERROR),
        JobStatus.RUNNING,
        moment,
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        statuses = [row[0] for row in cursor.fetchall()]
    return ReapReport(
        requeued=sum(1 for status in statuses if status == JobStatus.PENDING),
        dead_lettered=sum(1 for status in statuses if status == JobStatus.DEAD_LETTER),
    )


# --- Organizaciones ----------------------------------------------------------


def organization_ids() -> list[uuid.UUID]:
    """Los `id` de todas las organizaciones, para encolar los periódicos.

    No puede pasar por `scoped_qs`: la pregunta es literalmente "¿qué
    organizaciones hay?", que es anterior a cualquier contexto (y
    `Organization` es la raíz del scoping, exenta por diseño — ver
    `core/scoping.NotOrgScopedError`). Se resuelve con la misma sentencia
    parametrizada que el resto del módulo en vez de tocando el manager, para no
    tener que abrir una exención nueva en el test de arquitectura.
    """
    table = connection.ops.quote_name(Organization._meta.db_table)
    column = connection.ops.quote_name(Organization._meta.get_field("id").column)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table} ORDER BY {column}")
        return [row[0] for row in cursor.fetchall()]

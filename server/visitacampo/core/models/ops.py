"""Tablas operativas: `SyncOpLog`, `Job`, `AuditEvent` y `SyncCursorFloor`.

Ninguna lleva lógica en este paso: la idempotencia del push es del Paso 8,
la cola con `SKIP LOCKED` + lease del Paso 19 y la escritura de auditoría
se va añadiendo desde el Paso 7. `SyncCursorFloor` es del Paso 9 — ver su
docstring para el porqué existe.
"""

from __future__ import annotations

import uuid

from django.core.validators import MinValueValidator
from django.db import models

from .base import ACTIVE_JOB_STATUSES, JobStatus, OrgScopedModel, SyncEntityType


class SyncOpLog(OrgScopedModel):
    """Registro de idempotencia del push (doc 08 §4.4, ADR-003).

    Reenviar el mismo `op_uuid` devuelve el resultado guardado en
    `result` **sin re-aplicar** la operación. Equivale al `Delta` de
    QFieldCloud pero sin máquina de estados asíncrona.

    **Retención: 90 días** (`base.RETENTION_DAYS`). La purga es un job
    periódico del Paso 19; aquí solo se modela la tabla y el índice
    `(received_at)` que esa purga necesitará para barrer por antigüedad.

    Lleva `org_id` aunque doc 08 §4.4 no lo liste en la tabla: ADR-011
    exige `org_id NOT NULL` en toda tabla de negocio, y sin él una purga o
    una consulta de diagnóstico no podrían scoparse por tenant.
    """

    # Generado en el dispositivo (UUIDv7): **es** la clave de idempotencia,
    # así que no puede tener default de servidor.
    op_uuid = models.UUIDField(primary_key=True, editable=False)
    device = models.ForeignKey(
        "core.Device",
        on_delete=models.PROTECT,
        related_name="sync_ops",
        verbose_name="dispositivo",
    )
    batch_uuid = models.UUIDField(verbose_name="lote")
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="recibida en")
    # Resultado ya *scrubbed* (RD-12) que se devuelve tal cual en un reenvío.
    result = models.JSONField(default=dict, blank=True, verbose_name="resultado")
    entity = models.CharField(max_length=32, choices=SyncEntityType.choices)
    entity_id = models.UUIDField()

    class Meta:
        db_table = "sync_op_log"
        verbose_name = "operación de sync"
        verbose_name_plural = "operaciones de sync"
        indexes = [
            # Purga por antigüedad (Paso 19).
            models.Index(fields=["received_at"], name="sync_op_received_at_idx"),
            # Reconstrucción del resultado de un lote completo.
            models.Index(fields=["org", "batch_uuid"], name="sync_op_org_batch_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.entity}:{self.entity_id}"


class Job(OrgScopedModel):
    """Trabajo asíncrono. Cola sobre Postgres con **lease** (ADR-006, RD-13).

    El Paso 5 creó las columnas; el Paso 19 añadió `idempotency_key` (abajo) y
    toda la lógica: `SELECT ... FOR UPDATE SKIP LOCKED`, lease con heartbeat,
    reaper, back-off y `dead_letter` viven en `visitacampo/jobs/queue.py`, y el
    proceso que las consume es `manage.py run_worker`. Sin Redis, sin Celery,
    sin docker-in-docker.

    **Por qué la lógica no está aquí**: la cola se manipula con sentencias SQL
    únicas y atómicas (el `take` es un `UPDATE ... FROM (SELECT ... FOR UPDATE
    SKIP LOCKED)`), que es precisamente lo que el ORM no sabe expresar — misma
    razón, y mismo patrón, que `sync/sql.py` documenta para el CAS del push.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=64, verbose_name="tipo")
    status = models.CharField(
        max_length=16,
        choices=JobStatus.choices,
        default=JobStatus.PENDING,
        verbose_name="estado",
    )
    payload = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1)])
    run_after = models.DateTimeField(null=True, blank=True, default=None)
    locked_by = models.CharField(max_length=128, null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True, default=None)
    # Salida ya *scrubbed* (RD-12): nunca contiene el payload crudo.
    output = models.JSONField(null=True, blank=True, default=None)
    # Cuerpo `Error` del contrato cuando el job acaba en `failed`/`dead_letter`.
    error = models.JSONField(null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True, default=None)
    # Unicidad lógica del Paso 19 (ADR-006, RD-14). Ver `ACTIVE_STATUSES`.
    idempotency_key = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        default=None,
        verbose_name="clave de idempotencia",
        help_text=(
            "Identifica el trabajo lógico (tipo + entidad). Mientras exista un job "
            "activo con esta clave en la organización, no se puede encolar otro. "
            "NULL = sin invariante de unicidad."
        ),
    )

    class Meta:
        db_table = "job"
        verbose_name = "job"
        verbose_name_plural = "jobs"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1),
                name="job_max_attempts_gte_1",
            ),
            # **El único** mecanismo del invariante "un job activo por
            # tipo/entidad" (RD-14, corrige H-13). No hay un segundo "check de
            # concurrencia" en Python que pueda desincronizarse con este: el
            # encolado es un `INSERT ... ON CONFLICT DO NOTHING` que se apoya
            # justo en este índice, así que dos workers arrancando a la vez no
            # tienen ventana TOCTOU entre "¿existe ya?" y "lo creo" — no hay
            # dos sentencias, hay una.
            #
            # Es **parcial** porque el invariante solo habla de jobs *activos*:
            # un job terminado (`succeeded`/`failed`/`dead_letter`) es
            # historial, y prohibir su reencolado impediría que un periódico
            # vuelva a ejecutarse nunca. `idempotency_key = NULL` queda fuera
            # del índice por la semántica de PostgreSQL (los NULL son
            # distintos entre sí), que es exactamente lo que se quiere: un job
            # sin clave no compite con nadie.
            models.UniqueConstraint(
                fields=["org", "idempotency_key"],
                condition=models.Q(status__in=ACTIVE_JOB_STATUSES),
                name="job_active_idempotency_uq",
            ),
        ]
        indexes = [
            # Índice del `take` de la cola (Paso 19): pendientes por org,
            # ordenados por cuándo pueden ejecutarse.
            models.Index(fields=["org", "status", "run_after"], name="job_org_status_run_idx"),
            # Reaper de leases vencidos (RD-13).
            models.Index(fields=["locked_until"], name="job_locked_until_idx"),
            # El `take` barre **todas** las organizaciones (un worker no es de
            # nadie), así que el índice de arriba —que empieza por `org`— no lo
            # sirve. Este sí: `WHERE status = 'pending' AND run_after <= now()`
            # ordenado por `run_after`.
            models.Index(fields=["status", "run_after"], name="job_status_run_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.type}:{self.status}"


class AuditEvent(OrgScopedModel):
    """Traza de "quién hizo qué" (doc 08 §4.4).

    Quién sincronizó qué, quién revocó qué token, quién exportó datos.
    `metadata` va **scrubbed** (RD-12) y la IP solo como hash (`ip_hash`),
    nunca en claro.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="audit_events",
        null=True,
        blank=True,
        default=None,
        verbose_name="actor",
        help_text="Nulo cuando la acción la ejecuta el propio sistema.",
    )
    action = models.CharField(max_length=64, verbose_name="acción")
    # Referencia libre al objeto afectado, p. ej. `visit:018f3c2a-...`.
    target = models.CharField(max_length=256, verbose_name="objetivo")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # SHA-256 hexadecimal de la IP (+ sal de despliegue). Nunca la IP cruda.
    ip_hash = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        db_table = "audit_event"
        verbose_name = "evento de auditoría"
        verbose_name_plural = "eventos de auditoría"
        indexes = [
            models.Index(fields=["org", "created_at"], name="audit_org_created_idx"),
            models.Index(fields=["org", "actor"], name="audit_org_actor_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} -> {self.target}"


class SyncCursorFloor(OrgScopedModel):
    """Por debajo de qué `change_seq` el pull ya **no puede** garantizar completitud.

    Existe por un problema muy concreto de doc 08 §4.4: la purga física de
    tombstones (`sync/retention.py`, Paso 9; el job periódico que la invoca es
    el Paso 19) borra filas cuyo `deleted_at` supera los 90 días
    (`base.RETENTION_DAYS`). Una vez borrada, esa fila **desaparece** de
    `WHERE change_seq > cursor` — no queda ni rastro de que existió. Un
    cliente cuyo cursor sea anterior al `change_seq` de esa fila nunca se
    enteraría de que algo se borró: la misma clase de pérdida silenciosa que
    `changeseq.py` documenta para el push, solo que por el lado del pull.

    La única forma de detectarlo sin reintroducir la fila es recordar, por
    organización, **hasta dónde ha llegado la última purga**. `floor` es
    monótono no decreciente —una purga solo puede subirlo, nunca bajarlo— y es
    la **única** fuente de verdad para decidir `409 cursor_expired`
    (`sync/retention.cursor_is_expired`): `cursor < floor` ⇒ expirado. No hay
    una segunda señal (p. ej. "han pasado 90 días desde tal fecha") con la que
    esta pueda desincronizarse — es exactamente el principio de RD-14 que
    `changeseq.py` ya aplica al lock de organización, trasladado al lado de
    lectura.

    Una fila por organización (`UniqueConstraint`), creada perezosamente por
    `retention.py` la primera vez que purga algo para esa organización: una
    organización que nunca ha purgado nada no la necesita (su `floor` implícito
    es 0, ningún cursor puede ser menor que eso).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    floor = models.PositiveBigIntegerField(
        default=0,
        verbose_name="piso de purga",
        help_text="Todo change_seq por debajo de este valor puede tener tombstones ya purgados.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sync_cursor_floor"
        verbose_name = "piso de cursor de sync"
        verbose_name_plural = "pisos de cursor de sync"
        constraints = [
            models.UniqueConstraint(fields=["org"], name="sync_cursor_floor_org_uq"),
        ]

    def __str__(self) -> str:
        return f"{self.org_id}:{self.floor}"


class RateLimitBucket(models.Model):
    """Contador de peticiones por clave y ventana de tiempo (Paso 15, ADR-008).

    Lo usa `visitacampo/core/ratelimit.py` con un único
    `INSERT … ON CONFLICT DO UPDATE … RETURNING`, atómico entre procesos y
    workers de gunicorn sin Redis (ADR-006). **No** es `OrgScopedModel` a
    propósito: el login y el refresh se limitan antes de saber a qué
    organización pertenece nadie. `key` nunca contiene la IP ni el
    identificador en claro: es `ámbito:HMAC(identidad)`. Las ventanas vencidas
    las purga el periódico `security.purge_logs`.
    """

    key = models.CharField(max_length=128)
    window_start = models.DateTimeField()
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rate_limit_bucket"
        verbose_name = "ventana de rate limiting"
        verbose_name_plural = "ventanas de rate limiting"
        constraints = [
            models.UniqueConstraint(
                fields=["key", "window_start"], name="rate_limit_bucket_key_window"
            ),
        ]
        indexes = [models.Index(fields=["window_start"], name="rate_limit_bucket_window_idx")]

    def __str__(self) -> str:
        return f"{self.key}@{self.window_start:%H:%M}: {self.hits}"

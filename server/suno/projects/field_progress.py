"""Avance de campo (costos, etapa 1): el % que reportan los registros vinculados
a tareas del cronograma.

- **% SUNO de una tarea hoja** = `progress_pct` del registro **validada** más
  reciente (por `captured_at`) vinculada a ella. "Validada" = su revisión
  vigente —la más reciente, `RecordReview` es un historial— es `approved`.
- **Menor que el último validado**: un registro cuyo % es menor que el de la
  registro validado más reciente de la misma tarea capturada **antes** que ella.
  Se calcula al leer: validar o rechazar después el registro de referencia
  cambia el aviso.

Las funciones reciben el queryset base de registros en vez de un contexto: la
plataforma les pasa `scoped_qs(ctx, Record)` y el Admin su propio queryset (el
superusuario ve todas las organizaciones). La decisión vigente es una subconsulta
sobre `reviews` (la más reciente).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import OuterRef, Q, QuerySet, Subquery

from suno.core.context import RequestContext
from suno.core.models import Task, Record, RecordReview, RecordReviewStatus
from suno.core.scoping import scoped_qs

APPROVED = str(RecordReviewStatus.APPROVED)
REJECTED = str(RecordReviewStatus.REJECTED)

REVIEW_LABELS = {APPROVED: "Validada", REJECTED: "Rechazada", None: "Sin revisar"}


def with_review(records: QuerySet[Record]) -> QuerySet[Record]:
    """Anota `last_review_status`: la decisión vigente, o `None` sin revisión.

    La vigente es la fila más reciente de `RecordReview` (`-created_at, -id`: el
    id desempata si dos decisiones comparten instante). La subconsulta se
    correlaciona por registro, así que sirve igual con el queryset de una
    organización que con el del Admin.
    """
    latest = (
        QuerySet(model=RecordReview)
        .filter(record=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .values("status")[:1]
    )
    return records.annotate(last_review_status=Subquery(latest))


def review_status(record: Record) -> str | None:
    """Decisión vigente de un registro anotado con `with_review`, o `None`."""
    status: str | None = getattr(record, "last_review_status", None)
    return status


def approved(records: QuerySet[Record]) -> QuerySet[Record]:
    """Los registros cuya revisión vigente es `approved`."""
    return with_review(records).filter(last_review_status=APPROVED)


def not_rejected(records: QuerySet[Record]) -> QuerySet[Record]:
    """Los registros validados o aún sin revisar (su revisión vigente no es `rejected`)."""
    return with_review(records).filter(
        Q(last_review_status__isnull=True) | Q(last_review_status=APPROVED)
    )


def validated_pct_by_task(
    records: QuerySet[Record],
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> dict[UUID, Decimal]:
    """% SUNO por tarea: el de su registro validado más reciente. Una query.

    Sin `task_ids` ni `project_ids`, todas las tareas del queryset."""
    linked = records.filter(task__isnull=False, progress_pct__isnull=False, deleted_at__isnull=True)
    if task_ids is not None:
        ids = list(task_ids)
        if not ids:
            return {}
        linked = linked.filter(task_id__in=ids)
    if project_ids is not None:
        projects = list(project_ids)
        if not projects:
            return {}
        linked = linked.filter(task__project_id__in=projects)
    rows = (
        approved(linked)
        .order_by("task_id", "-captured_at", "-change_seq")
        .distinct("task_id")
        .values_list("task_id", "progress_pct")
    )
    return dict(rows)


def with_previous_validated_pct(records: QuerySet[Record], base: QuerySet[Record]) -> QuerySet[Record]:
    """Anota `previous_validated_pct`: el % del registro validado más reciente
    de la misma tarea capturada antes. `base` es de dónde se buscan (la misma
    organización, o todas para el superusuario del Admin: la correlación por
    tarea ya restringe a una sola)."""
    previous = (
        approved(
            base.filter(
                task=OuterRef("task"),
                captured_at__lt=OuterRef("captured_at"),
                progress_pct__isnull=False,
                deleted_at__isnull=True,
            )
        )
        .order_by("-captured_at", "-change_seq")
        .values("progress_pct")[:1]
    )
    return records.annotate(previous_validated_pct=Subquery(previous))


def format_pct(value: Decimal) -> str:
    """`Decimal('40.50')` -> `40,5 %`: hasta dos decimales, sin ceros finales."""
    return f"{value.normalize():f}".replace(".", ",") + " %"


def is_regression(pct: Decimal | None, previous: Decimal | None) -> bool:
    return pct is not None and previous is not None and pct < previous


@dataclass(frozen=True)
class ProgressRecord:
    """Un registro vinculado a una tarea, para la ficha de la tarea."""

    record: Record
    status: str | None
    previous_validated_pct: Decimal | None

    @property
    def regression(self) -> bool:
        return is_regression(self.record.progress_pct, self.previous_validated_pct)

    @property
    def pct_text(self) -> str:
        pct = self.record.progress_pct
        return format_pct(pct) if pct is not None else "—"

    @property
    def previous_text(self) -> str:
        previous = self.previous_validated_pct
        return format_pct(previous) if previous is not None else "—"

    @property
    def status_label(self) -> str:
        return REVIEW_LABELS[self.status]


def progress_records(ctx: RequestContext, task: Task) -> list[ProgressRecord]:
    """Registros vivos vinculadas a `task`, la más reciente primero. Una query."""
    base = scoped_qs(ctx, Record)
    records = (
        with_previous_validated_pct(
            with_review(base.filter(task=task, deleted_at__isnull=True)), base
        )
        .select_related("created_by")
        .order_by("-captured_at", "-change_seq")
    )
    return [
        ProgressRecord(
            record=v,
            status=review_status(v),
            previous_validated_pct=getattr(v, "previous_validated_pct", None),
        )
        for v in records
    ]

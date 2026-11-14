"""Cantidad propuesta de un acta (costos, pieza 6b, spec 2.4 y 3.3).

Para cada línea: lo validado en campo (registros con revisión vigente aprobada, pieza 4b) para
su ítem y sus tareas (ninguna: todas las del ítem), capturado hasta el fin del periodo, menos
lo ya pagado en actas aprobadas de la línea, nunca menos de cero. Así lo capturado antes y
validado tarde entra en la siguiente acta, y nada se paga dos veces. Lo capturado dentro del
periodo se muestra aparte (informativo).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Sum

from suno.core.context import RequestContext
from suno.core.models import CertificateLine, CertificateStatus, ContractLine, Project
from suno.core.scoping import scoped_qs
from suno.matrix.quantities import executed_by_pair

NONE = Decimal("0.0000")


@dataclass(frozen=True)
class LineScope:
    line_id: UUID
    item_id: UUID
    #: Vacío: todas las tareas del ítem.
    task_ids: frozenset[UUID]

    def covers(self, task_id: UUID) -> bool:
        return not self.task_ids or task_id in self.task_ids


@dataclass(frozen=True)
class Proposal:
    #: Validado hasta el fin del periodo.
    validated: Decimal
    #: Validado y capturado dentro del periodo (informativo).
    in_period: Decimal
    #: Pagado en actas aprobadas.
    paid: Decimal

    @property
    def proposed(self) -> Decimal:
        return max(self.validated - self.paid, NONE)


def line_scopes(lines: Iterable[ContractLine]) -> list[LineScope]:
    """Las líneas, con `prefetch_related("task_links")`."""
    return [
        LineScope(line.pk, line.item_id, frozenset(link.task_id for link in line.task_links.all()))
        for line in lines
    ]


def validated_by_line(
    ctx: RequestContext,
    project: Project,
    scopes: Sequence[LineScope],
    *,
    until: date,
    since: date | None = None,
) -> dict[UUID, Decimal]:
    """Lo validado por línea, capturado entre `since` (opcional) y `until`. Una consulta."""
    wanted = {scope.item_id for scope in scopes}
    by_item: dict[UUID, list[tuple[UUID, Decimal]]] = defaultdict(list)
    pairs = executed_by_pair(ctx, project_ids=[project.pk], captured_from=since, captured_to=until)
    for (task_id, item_id), quantity in pairs.items():
        if item_id in wanted:
            by_item[item_id].append((task_id, quantity))
    return {
        scope.line_id: sum(
            (quantity for task_id, quantity in by_item[scope.item_id] if scope.covers(task_id)),
            NONE,
        )
        for scope in scopes
    }


def paid_by_line(
    ctx: RequestContext, line_ids: Iterable[UUID], *, before_number: int | None = None
) -> dict[UUID, Decimal]:
    """Lo pagado por línea en actas aprobadas; con `before_number`, solo en las de número menor
    (lo "anterior" del PDF). Una consulta; las líneas sin pagos no aparecen."""
    rows = scoped_qs(ctx, CertificateLine).filter(
        line_id__in=list(line_ids), certificate__status=CertificateStatus.APPROVED
    )
    if before_number is not None:
        rows = rows.filter(certificate__number__lt=before_number)
    return dict(
        rows.order_by()
        .values("line_id")
        .annotate(total=Sum("quantity"))
        .values_list("line_id", "total")
    )


def proposals(
    ctx: RequestContext,
    project: Project,
    scopes: Sequence[LineScope],
    *,
    start: date,
    end: date,
) -> dict[UUID, Proposal]:
    """La propuesta de cada línea para el periodo `[start, end]`. Tres consultas."""
    validated = validated_by_line(ctx, project, scopes, until=end)
    in_period = validated_by_line(ctx, project, scopes, until=end, since=start)
    paid = paid_by_line(ctx, [scope.line_id for scope in scopes])
    return {
        scope.line_id: Proposal(
            validated=validated[scope.line_id],
            in_period=in_period[scope.line_id],
            paid=paid.get(scope.line_id, NONE),
        )
        for scope in scopes
    }

"""Saldos y kárdex (costos, pieza 6c, spec 1.3 y 2.1).

Toda operación que mueve inventario crea un `Stock` con los pares (bodega, material) que toca:
bloquea sus saldos con `select_for_update` en orden de (bodega, material) —dos operaciones
nunca se bloquean cruzadas—, calcula cada movimiento sobre el saldo en memoria (`stock`) y, al
final, `save()` escribe los movimientos y los saldos en la misma transacción. El saldo es
siempre la suma de los movimientos de su par; la secuencia del par da el orden del kárdex.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from functools import reduce
from operator import or_
from typing import Any
from uuid import UUID

from django.db.models import Q

from suno.core.context import RequestContext
from suno.core.models import Project, StockBalance, StockMovement
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty

from . import stock
from .common import MaterialRejected

Pair = tuple[UUID, UUID]


def lock_balances(
    ctx: RequestContext, project: Project, pairs: Iterable[Pair]
) -> dict[Pair, StockBalance]:
    """Los saldos de los pares, bloqueados en orden. Los que no existen se crean antes en una
    sola sentencia (`ON CONFLICT DO NOTHING`: dos operaciones que crean el mismo par no
    chocan). Los pares tienen que ser de bodegas del proyecto (lo comprueba quien llama)."""
    wanted = sorted(set(pairs), key=lambda pair: (str(pair[0]), str(pair[1])))
    if not wanted:
        return {}
    scoped_qs(ctx, StockBalance).bulk_create(
        [
            StockBalance(org_id=ctx.org_id, project=project, warehouse_id=w, material_id=m)
            for w, m in wanted
        ],
        ignore_conflicts=True,
    )
    rows = (
        scoped_qs(ctx, StockBalance)
        .select_for_update(of=("self",))
        .select_related("warehouse", "material")
        .filter(reduce(or_, (Q(warehouse_id=w, material_id=m) for w, m in wanted)))
        .order_by("warehouse_id", "material_id")
    )
    return {(row.warehouse_id, row.material_id): row for row in rows}


class Stock:
    def __init__(self, ctx: RequestContext, project: Project, pairs: Iterable[Pair]) -> None:
        self.ctx = ctx
        self.project = project
        self._rows = lock_balances(ctx, project, pairs)
        self._new: list[StockMovement] = []
        self._gone: list[UUID] = []

    def balance(self, warehouse_id: UUID, material_id: UUID) -> stock.Balance:
        row = self._rows[(warehouse_id, material_id)]
        return stock.Balance(row.quantity, row.value)

    def shortage(self, warehouse_id: UUID, material_id: UUID, available: Decimal) -> str:
        row = self._rows[(warehouse_id, material_id)]
        return (
            f"Solo hay {qty(available) or '0'} {row.material.unit} de {row.material.name} "
            f"en {row.warehouse.name}."
        )

    def _post(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        move: stock.Move,
        day: date,
        origin: dict[str, Any],
    ) -> StockMovement:
        row = self._rows[(warehouse_id, material_id)]
        row.quantity += move.quantity
        row.value += move.value
        row.last_sequence += 1
        movement = StockMovement(
            org_id=self.ctx.org_id,
            project=self.project,
            warehouse_id=warehouse_id,
            material_id=material_id,
            moved_on=day,
            kind=kind,
            quantity=move.quantity,
            unit_cost=move.unit_cost,
            value=move.value,
            sequence=row.last_sequence,
            created_by_id=self.ctx.user_id,
            **origin,
        )
        self._new.append(movement)
        return movement

    def add(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        value: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Una entrada por su valor."""
        move = stock.entry(quantity, value)
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def take(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Una salida al promedio; si no alcanza, `MaterialRejected` con lo que hay."""
        try:
            move = stock.exit_at_average(self.balance(warehouse_id, material_id), quantity)
        except stock.Shortage as exc:
            raise MaterialRejected(
                [self.shortage(warehouse_id, material_id, exc.available)]
            ) from exc
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def withdraw(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        value: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Un retiro por anulación, por el valor con que entró (sin dejar el saldo negativo)."""
        try:
            move = stock.withdraw(self.balance(warehouse_id, material_id), quantity, value)
        except stock.Shortage as exc:
            raise MaterialRejected(
                [self.shortage(warehouse_id, material_id, exc.available)]
            ) from exc
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def restore(self, movements: Sequence[StockMovement]) -> None:
        """Deshace movimientos (los de una salida que se edita o se borra): el saldo recupera lo
        que se llevaron y los movimientos se borran al guardar."""
        for movement in movements:
            row = self._rows[(movement.warehouse_id, movement.material_id)]
            row.quantity -= movement.quantity
            row.value -= movement.value
            self._gone.append(movement.pk)

    def save(self) -> list[StockMovement]:
        if self._gone:
            scoped_qs(self.ctx, StockMovement).filter(pk__in=self._gone).delete()
        created = scoped_qs(self.ctx, StockMovement).bulk_create(self._new)
        scoped_qs(self.ctx, StockBalance).bulk_update(
            list(self._rows.values()), ["quantity", "value", "last_sequence"]
        )
        self._new, self._gone = [], []
        return created

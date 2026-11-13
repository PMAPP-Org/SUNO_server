"""Costo real del proyecto (costos, pieza 6a). Puro: sin BD.

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, sección 3.

- **AC** al corte: movimientos aprobados del libro con fecha hasta el corte, más los costos
  automáticos (equipos propios y transporte de terceros). Un reverso cae en su propia fecha.
- Tres bolsas: directo asignado (ítem, capítulo, selección; equipos con ítem), directo sin
  asignar (equipos sin ítem y transporte) e indirecto (al proyecto).
- `CPI = EV / AC directo`; `EAC = AC + (BAC − EV) / CPI`; `TCPI = (BAC − EV) / (BAC − AC)`.
- Administración ganada: EV de cada presupuesto × (línea Administración ÷ costo directo).
- Grupos: los ítems que reciben costo de un capítulo o de una selección comparten índice; si
  se traslapan, se unen. Sin reparto del costo del grupo entre sus ítems.

No importa `earned_value` (al revés sí): recibe del valor ganado solo lo que necesita.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from typing import Protocol
from uuid import UUID

from suno.budgets.totals import WIDE_PRECISION, money

from .indices import ratio_of, share_of, status_of

ZERO = Decimal(0)
#: Asignaciones de una línea (las del libro, más "sin asignar" para los automáticos).
INDIRECT = "indirect"
ITEM = "item"
CHAPTER = "chapter"
SELECTION = "selection"
UNASSIGNED = "unassigned"
#: Origen de una línea.
LEDGER = "ledger"
EQUIPMENT = "equipment"
TRANSPORT = "transport"
#: El mismo texto que `earned_value.NO_CHAPTER` (lo comprueba una prueba).
NO_CHAPTER = "Sin capítulo"
UNKNOWN = "Ítem desconocido"
ORIGIN_LABELS = {EQUIPMENT: "Equipos propios sin ítem", TRANSPORT: "Transporte de terceros"}
COST_LABELS = {
    "green": "En presupuesto",
    "amber": "Atención",
    "red": "Sobrecosto",
    "none": "Sin costo",
}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    """Un costo a una fecha: un movimiento aprobado o un costo automático del día."""

    day: date
    amount: Decimal
    scope: str
    #: Ítems del movimiento (`item`, `selection`) o del uso de equipo.
    items: tuple[UUID, ...] = ()
    #: Capítulo asignado (`chapter`, `selection`) o de origen (`item` del libro).
    chapter_id: UUID | None = None
    task_id: UUID | None = None
    origin: str = LEDGER


@dataclass(frozen=True)
class ChapterRef:
    """Un capítulo de la versión vigente."""

    label: str
    #: Capítulo de primer nivel que lo contiene (él mismo si es de primer nivel).
    top: str
    #: 0 en el primer nivel.
    depth: int
    #: Ítems con precio vigente que contiene, a cualquier profundidad.
    items: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class AdminLine:
    budget_id: UUID
    budget: str
    #: Monto de la línea Administración ÷ costo directo de la versión vigente; `None` sin línea.
    ratio: Decimal | None


@dataclass(frozen=True)
class CostInput:
    lines: Sequence[CostLine] = ()
    chapters: Mapping[UUID, ChapterRef] = field(default_factory=dict)
    admin: Sequence[AdminLine] = ()
    #: Borradores con fecha hasta el corte: no cuentan, se avisan.
    pending_count: int = 0
    pending_amount: Decimal = ZERO
    #: Usos de equipo y viajes sin valor (sin tarifa): no suman, se avisan.
    unpriced_usages: int = 0
    unpriced_trips: int = 0


class PricedLike(Protocol):
    """Lo que se usa de `earned_value.PricedItem`."""

    @property
    def label(self) -> str: ...

    @property
    def chapter(self) -> str: ...

    @property
    def budget_id(self) -> UUID | None: ...


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostMeasure:
    """AC y EV de una fila del desglose. En un ítem de grupo, el índice es el del grupo."""

    ac: Decimal
    ev: Decimal
    group: str = ""
    group_ev: Decimal = ZERO
    group_ac: Decimal = ZERO

    @property
    def cv(self) -> Decimal:
        return self.group_ev - self.group_ac if self.group else self.ev - self.ac

    @property
    def cpi(self) -> Decimal | None:
        return ratio_of(self.group_ev, self.group_ac) if self.group else ratio_of(self.ev, self.ac)

    @property
    def status(self) -> str:
        return status_of(self.cpi)

    @property
    def status_label(self) -> str:
        return COST_LABELS[self.status]


@dataclass(frozen=True)
class ItemCost:
    """Lo que el cálculo sabe de un ítem; el EV lo pone el desglose."""

    ac: Decimal
    group: str = ""
    group_ev: Decimal = ZERO
    group_ac: Decimal = ZERO


@dataclass(frozen=True)
class CostIndicators:
    bac: Decimal
    ev: Decimal
    ac_assigned: Decimal
    ac_unassigned: Decimal
    ac_indirect: Decimal
    #: `None` si ningún presupuesto tiene línea de Administración.
    admin_earned: Decimal | None

    @property
    def ac_direct(self) -> Decimal:
        return self.ac_assigned + self.ac_unassigned

    @property
    def empty(self) -> bool:
        """Sin costos al corte (ni directos ni indirectos)."""
        return self.ac_direct == 0 and self.ac_indirect == 0

    @property
    def ac_share(self) -> Decimal | None:
        return share_of(self.ac_direct, self.bac)

    @property
    def cv(self) -> Decimal:
        return self.ev - self.ac_direct

    @property
    def cpi(self) -> Decimal | None:
        if self.bac <= 0:  # sin pares que midan no hay con qué comparar
            return None
        return ratio_of(self.ev, self.ac_direct)

    @property
    def status(self) -> str:
        return status_of(self.cpi)

    @property
    def status_label(self) -> str:
        return COST_LABELS[self.status]

    @property
    def eac(self) -> Decimal | None:
        """`AC + (BAC − EV) / CPI`, con el cociente sin redondear. Vacío sin CPI o sin EV."""
        if self.cpi is None or self.ev <= 0:
            return None
        with localcontext() as context:
            context.prec = WIDE_PRECISION
            return money(self.ac_direct + (self.bac - self.ev) * self.ac_direct / self.ev)

    @property
    def vac(self) -> Decimal | None:
        eac = self.eac
        return None if eac is None else self.bac - eac

    @property
    def over_budget(self) -> bool:
        eac = self.eac
        return eac is not None and eac > self.bac

    @property
    def tcpi(self) -> Decimal | None:
        """Vacío si el presupuesto ya se agotó (`BAC − AC ≤ 0`)."""
        return ratio_of(self.bac - self.ev, self.bac - self.ac_direct)

    @property
    def exhausted(self) -> bool:
        return self.ac_direct > 0 and self.bac - self.ac_direct <= 0

    @property
    def admin_cpi(self) -> Decimal | None:
        return None if self.admin_earned is None else ratio_of(self.admin_earned, self.ac_indirect)

    @property
    def admin_status(self) -> str:
        return status_of(self.admin_cpi)

    @property
    def total_cpi(self) -> Decimal | None:
        return ratio_of(self.ev + (self.admin_earned or ZERO), self.ac_direct + self.ac_indirect)

    @property
    def total_status(self) -> str:
        return status_of(self.total_cpi)


@dataclass(frozen=True)
class UnassignedCost:
    origin: str
    amount: Decimal


@dataclass(frozen=True)
class RetiredCost:
    item: str
    chapter: str
    amount: Decimal


@dataclass(frozen=True)
class CostWarnings:
    unassigned: tuple[UnassignedCost, ...] = ()
    #: Presupuestos sin línea de Administración.
    no_admin: tuple[str, ...] = ()
    retired: tuple[RetiredCost, ...] = ()
    pending_count: int = 0
    pending_amount: Decimal = ZERO
    exhausted: bool = False
    #: Costo directo sin tarea (incluye los automáticos): se avisa en el desglose por tarea.
    no_task: Decimal = ZERO
    #: Costo directo de tareas que ya no están en el cronograma vigente.
    retired_tasks: Decimal = ZERO
    unpriced_usages: int = 0
    unpriced_trips: int = 0

    @property
    def unassigned_total(self) -> Decimal:
        return sum((u.amount for u in self.unassigned), ZERO)

    @property
    def count(self) -> int:
        return (
            len(self.unassigned)
            + len(self.no_admin)
            + len(self.retired)
            + (1 if self.pending_count else 0)
            + (1 if self.exhausted else 0)
            + (1 if self.retired_tasks else 0)
            + (1 if self.unpriced_usages or self.unpriced_trips else 0)
        )


@dataclass(frozen=True)
class CostResult:
    indicators: CostIndicators
    #: AC directo de cada periodo (sin acumular), alineado con los periodos del control.
    per_period: tuple[Decimal, ...]
    #: AC por capítulo de primer nivel (la misma etiqueta que `PricedItem.chapter`).
    by_chapter: Mapping[str, Decimal]
    by_task: Mapping[UUID, Decimal]
    by_item: Mapping[UUID, ItemCost]
    warnings: CostWarnings


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


def measure_costs(
    data: CostInput,
    *,
    cut: date,
    starts: Sequence[date],
    items: Mapping[UUID, PricedLike],
    item_labels: Mapping[UUID, str],
    ev_by_item: Mapping[UUID, Decimal],
    bac: Decimal,
    ev: Decimal,
    tasks: Collection[UUID] | None = None,
) -> CostResult:
    """`starts`: inicio de cada periodo del control (ya incluye el primer costo contado).
    `tasks`: las tareas vigentes; el costo de una tarea retirada va a su aviso (`None`: todas
    cuentan como vigentes)."""
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return _measure(data, cut, starts, items, item_labels, ev_by_item, bac, ev, tasks)


def _top(chapters: Mapping[UUID, ChapterRef], chapter_id: UUID | None) -> str:
    ref = chapters.get(chapter_id) if chapter_id is not None else None
    return ref.top if ref is not None else NO_CHAPTER


def _measure(
    data: CostInput,
    cut: date,
    starts: Sequence[date],
    items: Mapping[UUID, PricedLike],
    item_labels: Mapping[UUID, str],
    ev_by_item: Mapping[UUID, Decimal],
    bac: Decimal,
    ev: Decimal,
    tasks: Collection[UUID] | None,
) -> CostResult:
    per_period = [ZERO] * len(starts)
    assigned = unassigned = indirect = no_task = retired_tasks = ZERO
    by_origin: dict[str, Decimal] = defaultdict(Decimal)
    by_chapter: dict[str, Decimal] = defaultdict(Decimal)
    by_task: dict[UUID, Decimal] = defaultdict(Decimal)
    own: dict[UUID, Decimal] = defaultdict(Decimal)
    retired: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    #: Costo de cada conjunto de ítems de capítulo o de selección, con la profundidad y el
    #: nombre de su capítulo. Agrupado aquí para que el cálculo de grupos no dependa del
    #: número de movimientos.
    grouped: dict[tuple[tuple[UUID, ...], int, str], Decimal] = defaultdict(Decimal)
    chapter_members: dict[UUID, tuple[UUID, ...]] = {}
    for line in data.lines:
        if line.day > cut:
            continue
        amount = line.amount
        if line.scope == INDIRECT:
            indirect += amount
            continue
        index = bisect_right(starts, line.day) - 1
        if index >= 0:
            per_period[index] += amount
        if line.task_id is None:
            no_task += amount
        elif tasks is not None and line.task_id not in tasks:
            retired_tasks += amount
        else:
            by_task[line.task_id] += amount
        if line.scope == UNASSIGNED:
            unassigned += amount
            by_origin[line.origin] += amount
            continue
        assigned += amount
        if line.scope == ITEM:
            item_id = line.items[0] if line.items else None
            priced = items.get(item_id) if item_id is not None else None
            if item_id is not None and priced is not None:
                own[item_id] += amount
                by_chapter[priced.chapter] += amount
            else:
                chapter = _top(data.chapters, line.chapter_id)
                by_chapter[chapter] += amount
                label = item_labels.get(item_id, UNKNOWN) if item_id is not None else UNKNOWN
                retired[(label, chapter)] += amount
            continue
        ref = data.chapters.get(line.chapter_id) if line.chapter_id is not None else None
        if ref is None:
            label = (
                item_labels.get(line.chapter_id, UNKNOWN)
                if line.chapter_id is not None
                else UNKNOWN
            )
            by_chapter[NO_CHAPTER] += amount
            retired[(label, NO_CHAPTER)] += amount
            continue
        by_chapter[ref.top] += amount
        if line.scope == CHAPTER and line.chapter_id is not None:
            members = chapter_members.get(line.chapter_id)
            if members is None:
                members = chapter_members[line.chapter_id] = tuple(sorted(ref.items))
        else:
            members = tuple(i for i in line.items if i in items)
            gone = [i for i in line.items if i not in items]
            if gone:
                # Sin reparto entre ítems, la parte de los que ya no están vigentes es la de su
                # número: cuenta en el capítulo (arriba) y se avisa; el resto va al grupo.
                share = amount / len(line.items)
                for item_id in gone:
                    retired[(item_labels.get(item_id, UNKNOWN), ref.top)] += share
                amount -= share * len(gone)
        if members:
            grouped[(members, ref.depth, ref.label)] += amount
    admin_earned, no_admin = _admin(data.admin, items, ev_by_item)
    indicators = CostIndicators(
        bac=bac,
        ev=ev,
        ac_assigned=money(assigned),
        ac_unassigned=money(unassigned),
        ac_indirect=money(indirect),
        admin_earned=admin_earned,
    )
    warnings = CostWarnings(
        unassigned=tuple(
            UnassignedCost(ORIGIN_LABELS.get(origin, origin), money(amount))
            for origin, amount in sorted(by_origin.items())
            if amount
        ),
        no_admin=no_admin,
        retired=tuple(
            RetiredCost(item, chapter, money(amount))
            for (item, chapter), amount in sorted(retired.items())
        ),
        pending_count=data.pending_count,
        pending_amount=money(data.pending_amount),
        exhausted=indicators.exhausted,
        no_task=money(no_task),
        retired_tasks=money(retired_tasks),
        unpriced_usages=data.unpriced_usages,
        unpriced_trips=data.unpriced_trips,
    )
    return CostResult(
        indicators=indicators,
        per_period=tuple(per_period),
        by_chapter=dict(by_chapter),
        by_task=dict(by_task),
        by_item=_groups(grouped, own, ev_by_item),
        warnings=warnings,
    )


def _groups(
    grouped: Mapping[tuple[tuple[UUID, ...], int, str], Decimal],
    own: Mapping[UUID, Decimal],
    ev_by_item: Mapping[UUID, Decimal],
) -> dict[UUID, ItemCost]:
    """Unión de conjuntos sobre los ítems de los movimientos de capítulo y de selección."""
    parent: dict[UUID, UUID] = {}

    def find(node: UUID) -> UUID:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            following = parent[node]
            parent[node] = root
            node = following
        return root

    for members, _depth, _label in grouped:
        for member in members:
            parent.setdefault(member, member)
        first = find(members[0])
        for member in members[1:]:
            root = find(member)
            if root != first:
                parent[root] = first
    group_ac: dict[UUID, Decimal] = defaultdict(Decimal)
    names: dict[UUID, tuple[int, str]] = {}
    for (members, depth, label), amount in grouped.items():
        root = find(members[0])
        group_ac[root] += amount
        candidate = (depth, label)
        if root not in names or candidate < names[root]:
            names[root] = candidate
    components: dict[UUID, list[UUID]] = defaultdict(list)
    for member in parent:
        components[find(member)].append(member)
    result: dict[UUID, ItemCost] = {}
    for root, component in components.items():
        total_ev = sum((ev_by_item.get(m, ZERO) for m in component), ZERO)
        total_ac = group_ac[root] + sum((own.get(m, ZERO) for m in component), ZERO)
        for member in component:
            result[member] = ItemCost(
                ac=money(own.get(member, ZERO)),
                group=names[root][1],
                group_ev=money(total_ev),
                group_ac=money(total_ac),
            )
    for item_id, amount in own.items():
        result.setdefault(item_id, ItemCost(ac=money(amount)))
    return result


def _admin(
    lines: Sequence[AdminLine],
    items: Mapping[UUID, PricedLike],
    ev_by_item: Mapping[UUID, Decimal],
) -> tuple[Decimal | None, tuple[str, ...]]:
    by_budget: dict[UUID, Decimal] = defaultdict(Decimal)
    for item_id, value in ev_by_item.items():
        priced = items.get(item_id)
        if priced is not None and priced.budget_id is not None:
            by_budget[priced.budget_id] += value
    with_line = [line for line in lines if line.ratio is not None]
    earned = (
        money(
            sum(
                (by_budget.get(line.budget_id, ZERO) * (line.ratio or ZERO) for line in with_line),
                ZERO,
            )
        )
        if with_line
        else None
    )
    return earned, tuple(sorted(line.budget for line in lines if line.ratio is None))

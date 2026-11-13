"""Consultas del libro de costos (costos, pieza 6a, spec 2.1 y 2.7).

- Los movimientos se filtran en SQL y se ordenan por fecha de causación (y registro).
- Las líneas automáticas no se copian al libro: se leen agregadas (equipo propio por día,
  equipo e ítem; transporte de terceros por viaje) y se mezclan por fecha. Son de solo
  lectura y cuentan como aprobadas.
- Un filtro de estado distinto de "aprobado", o de tipo, deja fuera las automáticas; un filtro
  de capítulo o de ítem deja fuera el transporte (no tiene ítem).
- La página `n` lee como mucho `n × PAGE_SIZE` movimientos.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from math import ceil
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet, Sum
from django.utils import timezone

from suno.core.context import RequestContext
from suno.core.models import (
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    EquipmentOwnership,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    Trip,
)
from suno.core.scoping import scoped_qs

from .catalog import Catalog

PAGE_SIZE = 100
MAX_EXPORT_LINES = 50_000
ALL, LEDGER_ONLY, AUTOMATIC = "todos", "libro", "automaticos"
ORIGINS = {ALL: "Todos", LEDGER_ONLY: "Libro", AUTOMATIC: "Solo automáticos"}
LEDGER, EQUIPMENT, TRANSPORT = "libro", "equipo", "transporte"
ORIGIN_LABELS = {LEDGER: "Libro", EQUIPMENT: "Equipo propio", TRANSPORT: "Transporte"}
COUNTED_USAGE = (EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS)
INDIRECT_LABEL = "Indirecto (proyecto)"
UNASSIGNED_LABEL = "Sin asignar"
AUTOMATIC_LABEL = "Automático"


class LedgerTooLarge(Exception):
    """El libro filtrado pasa del máximo exportable."""


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class LedgerFilters:
    status: str = ""
    kind: str = ""
    date_from: date | None = None
    date_to: date | None = None
    chapter_id: UUID | None = None
    item_id: UUID | None = None
    origin: str = ALL

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> LedgerFilters:
        """Valores de la URL; un valor desconocido se ignora (sin error)."""
        status = query.get("estado", "")
        kind = query.get("tipo", "")
        origin = query.get("origen", "")
        return cls(
            status=status if status in CostStatus.values else "",
            kind=kind if kind in CostKind.values else "",
            date_from=_date(query.get("desde")),
            date_to=_date(query.get("hasta")),
            chapter_id=_uuid(query.get("capitulo")),
            item_id=_uuid(query.get("item")),
            origin=origin if origin in ORIGINS else ALL,
        )

    @property
    def query(self) -> str:
        values = {
            "estado": self.status,
            "tipo": self.kind,
            "desde": self.date_from.isoformat() if self.date_from else "",
            "hasta": self.date_to.isoformat() if self.date_to else "",
            "capitulo": str(self.chapter_id) if self.chapter_id else "",
            "item": str(self.item_id) if self.item_id else "",
            "origen": "" if self.origin == ALL else self.origin,
        }
        return urlencode({key: value for key, value in values.items() if value})

    @property
    def shows_ledger(self) -> bool:
        return self.origin != AUTOMATIC

    @property
    def shows_automatic(self) -> bool:
        return (
            self.origin != LEDGER_ONLY
            and not self.kind
            and self.status in ("", CostStatus.APPROVED)
        )


@dataclass(frozen=True)
class LedgerLine:
    day: date
    #: `LEDGER`, `EQUIPMENT` o `TRANSPORT`.
    origin: str
    kind: str
    description: str
    assignment: str
    third_party: str
    document: str
    amount: Decimal
    #: Un `CostStatus`, o "auto".
    status: str
    status_label: str
    created_by: str = ""
    decided_by: str = ""
    entry_id: UUID | None = None
    reversed: bool = False
    is_reversal: bool = False

    @property
    def automatic(self) -> bool:
        return self.origin != LEDGER


@dataclass(frozen=True)
class LedgerPage:
    lines: tuple[LedgerLine, ...]
    number: int
    pages: int
    count: int
    #: Aprobados por tipo, más los automáticos.
    totals: tuple[tuple[str, Decimal], ...]

    @property
    def total(self) -> Decimal:
        return sum((amount for _, amount in self.totals), Decimal(0))

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


def manual_entries(
    ctx: RequestContext, project: Project, filters: LedgerFilters, catalog: Catalog
) -> QuerySet[CostEntry]:
    entries = scoped_qs(ctx, CostEntry).filter(project=project)
    if filters.status:
        entries = entries.filter(status=filters.status)
    if filters.kind:
        entries = entries.filter(kind=filters.kind)
    if filters.date_from:
        entries = entries.filter(incurred_on__gte=filters.date_from)
    if filters.date_to:
        entries = entries.filter(incurred_on__lte=filters.date_to)
    links = scoped_qs(ctx, CostEntryItem)
    if filters.item_id:
        entries = entries.filter(pk__in=links.filter(item_id=filters.item_id).values("entry_id"))
    elif filters.chapter_id:
        below = catalog.below(filters.chapter_id)
        entries = entries.filter(
            Q(chapter_id__in=[filters.chapter_id, *below])
            | Q(pk__in=links.filter(item_id__in=below).values("entry_id"))
        )
    return entries.order_by("-incurred_on", "-created_at")


def _with_details(ctx: RequestContext, entries: QuerySet[CostEntry]) -> QuerySet[CostEntry]:
    return (
        entries.select_related("created_by", "decided_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .annotate(is_reversed=Exists(scoped_qs(ctx, CostEntry).filter(reverses=OuterRef("pk"))))
    )


def assignment_text(entry: CostEntry, catalog: Catalog) -> str:
    links = list(entry.item_links.all())
    chapter = catalog.label_of(entry.chapter_id, entry.chapter.code if entry.chapter else "")
    if entry.scope == CostScope.INDIRECT:
        text = INDIRECT_LABEL
    elif entry.scope == CostScope.ITEM:
        text = catalog.label_of(links[0].item_id, links[0].item.code) if links else "—"
    elif entry.scope == CostScope.CHAPTER:
        text = f"Capítulo {chapter}"
    else:
        text = f"{len(links)} ítems de {chapter}"
    if entry.task is not None:
        text = f"{text} · tarea {entry.task.outline_number} {entry.task.name}"
    return text


def _entry_line(entry: CostEntry, catalog: Catalog) -> LedgerLine:
    document = " ".join(
        part for part in (entry.get_document_type_display(), entry.document_number) if part
    )
    return LedgerLine(
        day=entry.incurred_on,
        origin=LEDGER,
        kind=entry.get_kind_display(),
        description=entry.description,
        assignment=assignment_text(entry, catalog),
        third_party=entry.third_party,
        document=document,
        amount=entry.amount,
        status=entry.status,
        status_label=entry.get_status_display(),
        created_by=str(entry.created_by),
        decided_by=str(entry.decided_by) if entry.decided_by else "",
        entry_id=entry.pk,
        reversed=bool(getattr(entry, "is_reversed", False)),
        is_reversal=entry.reverses_id is not None,
    )


def automatic_lines(
    ctx: RequestContext, project: Project, filters: LedgerFilters, catalog: Catalog
) -> list[LedgerLine]:
    if not filters.shows_automatic:
        return []
    usages = scoped_qs(ctx, EquipmentUsage).filter(
        project=project,
        status__in=COUNTED_USAGE,
        equipment__ownership=EquipmentOwnership.OWN,
        cost__isnull=False,
    )
    trips = scoped_qs(ctx, Trip).filter(
        project=project, vehicle__isnull=False, amount__isnull=False
    )
    if filters.date_from:
        usages = usages.filter(usage_date__gte=filters.date_from)
        trips = trips.filter(recorded_at__date__gte=filters.date_from)
    if filters.date_to:
        usages = usages.filter(usage_date__lte=filters.date_to)
        trips = trips.filter(recorded_at__date__lte=filters.date_to)
    if filters.item_id:
        usages = usages.filter(budget_item_id=filters.item_id)
        trips = trips.none()
    elif filters.chapter_id:
        usages = usages.filter(budget_item_id__in=catalog.descendants(filters.chapter_id))
        trips = trips.none()
    lines: list[LedgerLine] = []
    for day, code, name, item_id, hours, total in (
        usages.order_by()
        .values("usage_date", "equipment__code", "equipment__name", "budget_item_id")
        .annotate(hours=Sum("billable_hours"), total=Sum("cost"))
        .values_list(
            "usage_date", "equipment__code", "equipment__name", "budget_item_id", "hours", "total"
        )
    ):
        lines.append(
            LedgerLine(
                day=day,
                origin=EQUIPMENT,
                kind=ORIGIN_LABELS[EQUIPMENT],
                description=f"{code} · {name} ({hours or 0} h)",
                assignment=catalog.label_of(item_id, UNASSIGNED_LABEL)
                if item_id
                else UNASSIGNED_LABEL,
                third_party="",
                document="",
                amount=total,
                status="auto",
                status_label=AUTOMATIC_LABEL,
            )
        )
    for trip in trips.select_related("material", "route", "vehicle__contractor"):
        contractor = trip.vehicle.contractor.name if trip.vehicle else ""
        lines.append(
            LedgerLine(
                day=timezone.localtime(trip.recorded_at).date(),
                origin=TRANSPORT,
                kind=ORIGIN_LABELS[TRANSPORT],
                description=f"{trip.material.name} · {trip.volume_m3} m³ · {trip.route}",
                assignment=UNASSIGNED_LABEL,
                third_party=contractor,
                document=trip.code,
                amount=trip.amount,
                status="auto",
                status_label=AUTOMATIC_LABEL,
            )
        )
    lines.sort(key=lambda line: line.day, reverse=True)
    return lines


def _merge(manual: Sequence[LedgerLine], automatic: Sequence[LedgerLine]) -> list[LedgerLine]:
    """Por fecha, de la más reciente a la más antigua; en un mismo día, primero el libro."""
    return sorted([*manual, *automatic], key=lambda line: line.day, reverse=True)


def kind_totals(
    ctx: RequestContext,
    project: Project,
    filters: LedgerFilters,
    catalog: Catalog,
    automatic: Sequence[LedgerLine],
) -> tuple[tuple[str, Decimal], ...]:
    totals: list[tuple[str, Decimal]] = []
    if filters.shows_ledger and filters.status in ("", CostStatus.APPROVED):
        approved = manual_entries(
            ctx, project, replace(filters, status=str(CostStatus.APPROVED)), catalog
        )
        sums = dict(
            approved.order_by()
            .values("kind")
            .annotate(total=Sum("amount"))
            .values_list("kind", "total")
        )
        totals += [(label, sums[kind]) for kind, label in CostKind.choices if sums.get(kind)]
    for origin in (EQUIPMENT, TRANSPORT):
        amount = sum((line.amount for line in automatic if line.origin == origin), Decimal(0))
        if amount:
            totals.append((f"{ORIGIN_LABELS[origin]} (automático)", amount))
    return tuple(totals)


def ledger_page(
    ctx: RequestContext,
    project: Project,
    filters: LedgerFilters,
    *,
    page: int,
    catalog: Catalog,
) -> LedgerPage:
    entries = manual_entries(ctx, project, filters, catalog) if filters.shows_ledger else None
    automatic = automatic_lines(ctx, project, filters, catalog)
    count = (entries.count() if entries is not None else 0) + len(automatic)
    pages = max(1, ceil(count / PAGE_SIZE))
    number = min(max(page, 1), pages)
    end = number * PAGE_SIZE
    head = (
        [_entry_line(e, catalog) for e in _with_details(ctx, entries)[:end]]
        if entries is not None
        else []
    )
    lines = _merge(head, automatic[:end])[end - PAGE_SIZE : end]
    return LedgerPage(
        lines=tuple(lines),
        number=number,
        pages=pages,
        count=count,
        totals=kind_totals(ctx, project, filters, catalog, automatic),
    )


def export_lines(
    ctx: RequestContext, project: Project, filters: LedgerFilters, *, catalog: Catalog
) -> list[LedgerLine]:
    """Todo el libro filtrado (movimientos y automáticos). Lanza `LedgerTooLarge`."""
    entries = (
        list(
            _with_details(ctx, manual_entries(ctx, project, filters, catalog))[
                : MAX_EXPORT_LINES + 1
            ]
        )
        if filters.shows_ledger
        else []
    )
    automatic = automatic_lines(ctx, project, filters, catalog)
    if len(entries) + len(automatic) > MAX_EXPORT_LINES:
        raise LedgerTooLarge
    return _merge([_entry_line(e, catalog) for e in entries], automatic)

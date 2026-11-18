"""Consultas de la pestaña "Materiales" (costos, pieza 6c, spec 3.2 y 3.3).

Número fijo de consultas: inventario 2 (totales y la página), kárdex 1, cada lista 1, avisos 4,
bandeja 3, valor en bodega 1. Las listas muestran los `LIST_LIMIT` más recientes del filtro
(decisión 21 del plan); el inventario va paginado de a `PAGE_SIZE`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Count, Min, Q, QuerySet, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    CostScope,
    InventoryAdjustment,
    Material,
    MaterialIssue,
    MaterialPurchase,
    MaterialStatus,
    MaterialTransfer,
    Project,
    PurchaseKind,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.core.scoping import scoped_qs

from .stock import unit_cost

PAGE_SIZE = 100
LIST_LIMIT = 200
KARDEX_LIMIT = 1000
MAX_EXPORT_ROWS = 50_000
STALE_DAYS = 31
UNCUT, CUT = "sin", "con"


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


def _page(value: str | None) -> int:
    try:
        return max(int(value or 1), 1)
    except ValueError:
        return 1


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass(frozen=True)
class InventoryFilters:
    warehouse_id: UUID | None = None
    category: str = ""
    text: str = ""
    page: int = 1

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> InventoryFilters:
        return cls(
            warehouse_id=_uuid(query.get("bodega")),
            category=(query.get("categoria") or "").strip()[:80],
            text=(query.get("texto") or "").strip()[:100],
            page=_page(query.get("pagina")),
        )

    @property
    def query(self) -> str:
        values = {
            "bodega": str(self.warehouse_id) if self.warehouse_id else "",
            "categoria": self.category,
            "texto": self.text,
        }
        return urlencode({key: value for key, value in values.items() if value})


@dataclass(frozen=True)
class InventoryRow:
    balance: StockBalance

    @property
    def average(self) -> Decimal | None:
        balance = self.balance
        return unit_cost(balance.quantity, balance.value) if balance.quantity > 0 else None


@dataclass(frozen=True)
class InventoryPage:
    rows: tuple[InventoryRow, ...]
    number: int
    pages: int
    count: int
    value: Decimal

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


def _balances(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> QuerySet[StockBalance]:
    """Los pares con movimientos (aunque hoy estén en cero)."""
    rows = scoped_qs(ctx, StockBalance).filter(project=project, last_sequence__gt=0)
    if filters.warehouse_id:
        rows = rows.filter(warehouse_id=filters.warehouse_id)
    if filters.category:
        rows = rows.filter(material__category__iexact=filters.category)
    if filters.text:
        rows = rows.filter(
            Q(material__code__icontains=filters.text) | Q(material__name__icontains=filters.text)
        )
    return rows


def inventory_page(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> InventoryPage:
    rows = _balances(ctx, project, filters)
    totals = rows.aggregate(count=Count("pk"), value=Sum("value"))
    count = totals["count"] or 0
    pages = max(1, ceil(count / PAGE_SIZE))
    number = min(filters.page, pages)
    start = (number - 1) * PAGE_SIZE
    page = rows.select_related("warehouse", "material").order_by(
        "warehouse__name", "material__code"
    )[start : start + PAGE_SIZE]
    return InventoryPage(
        rows=tuple(InventoryRow(balance) for balance in page),
        number=number,
        pages=pages,
        count=count,
        value=totals["value"] or ZERO,
    )


def inventory_rows(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> list[InventoryRow]:
    """Todo el inventario filtrado, para el Excel (hasta `MAX_EXPORT_ROWS`)."""
    rows = (
        _balances(ctx, project, filters)
        .select_related("warehouse", "material")
        .order_by("warehouse__name", "material__code")[:MAX_EXPORT_ROWS]
    )
    return [InventoryRow(balance) for balance in rows]


def categories(ctx: RequestContext) -> list[str]:
    return list(
        scoped_qs(ctx, Material)
        .exclude(category="")
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )


def stock_value(ctx: RequestContext, project: Project) -> Decimal:
    """El valor en bodega del proyecto (spec 3.3): inventario, no costo."""
    total = scoped_qs(ctx, StockBalance).filter(project=project).aggregate(value=Sum("value"))
    return total["value"] or ZERO


def document_label(movement: StockMovement) -> str:
    """ "CM-001", "SA-003", "DV-001", "TR-002" o "AJ-001" (con `select_related` del origen)."""
    for field in ("purchase", "issue", "material_return", "transfer", "adjustment"):
        document = getattr(movement, field)
        if document is not None:
            return str(document.code)
    return ""


@dataclass(frozen=True)
class KardexRow:
    movement: StockMovement
    document: str
    entry: Decimal | None
    exit: Decimal | None
    balance_quantity: Decimal
    balance_value: Decimal

    @property
    def average(self) -> Decimal | None:
        if self.balance_quantity <= 0:
            return None
        return unit_cost(self.balance_quantity, self.balance_value)


@dataclass(frozen=True)
class Kardex:
    warehouse: Warehouse
    material: Material
    rows: tuple[KardexRow, ...]
    #: Hay más filas en el filtro de las que se muestran (se muestran las últimas).
    truncated: bool


def kardex(
    ctx: RequestContext,
    project: Project,
    warehouse: Warehouse,
    material: Material,
    *,
    start: date | None = None,
    end: date | None = None,
    limit: int = KARDEX_LIMIT,
) -> Kardex:
    """Los movimientos del par en el orden de su secuencia, con el saldo corrido. El filtro por
    fechas solo elige qué filas se muestran (decisión 6 del plan). Una consulta."""
    movements = (
        scoped_qs(ctx, StockMovement)
        .filter(project=project, warehouse=warehouse, material=material)
        .select_related("purchase", "issue", "material_return", "transfer", "adjustment")
        .order_by("sequence")
    )
    quantity, value = Decimal("0"), ZERO
    rows: list[KardexRow] = []
    for movement in movements:
        quantity += movement.quantity
        value += movement.value
        if (start and movement.moved_on < start) or (end and movement.moved_on > end):
            continue
        rows.append(
            KardexRow(
                movement=movement,
                document=document_label(movement),
                entry=movement.quantity if movement.quantity > 0 else None,
                exit=-movement.quantity if movement.quantity < 0 else None,
                balance_quantity=quantity,
                balance_value=value,
            )
        )
    return Kardex(warehouse, material, tuple(rows[-limit:]), len(rows) > limit)


@dataclass(frozen=True)
class DocumentFilters:
    kind: str = ""
    status: str = ""
    start: date | None = None
    end: date | None = None
    warehouse_id: UUID | None = None
    scope: str = ""
    cut: str = ""

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> DocumentFilters:
        kind, status = query.get("tipo", ""), query.get("estado", "")
        scope, cut = query.get("destino", ""), query.get("corte", "")
        return cls(
            kind=kind if kind in PurchaseKind.values else "",
            status=status if status in MaterialStatus.values else "",
            start=_date(query.get("desde")),
            end=_date(query.get("hasta")),
            warehouse_id=_uuid(query.get("bodega")),
            scope=scope if scope in CostScope.values else "",
            cut=cut if cut in (UNCUT, CUT) else "",
        )

    @property
    def query(self) -> str:
        values = {
            "tipo": self.kind,
            "estado": self.status,
            "desde": self.start.isoformat() if self.start else "",
            "hasta": self.end.isoformat() if self.end else "",
            "bodega": str(self.warehouse_id) if self.warehouse_id else "",
            "destino": self.scope,
            "corte": self.cut,
        }
        return urlencode({key: value for key, value in values.items() if value})


def _limited(rows: QuerySet[Any]) -> tuple[list[Any], bool]:
    found = list(rows[: LIST_LIMIT + 1])
    return found[:LIST_LIMIT], len(found) > LIST_LIMIT


def _dates(rows: QuerySet[Any], field: str, filters: DocumentFilters) -> QuerySet[Any]:
    if filters.start:
        rows = rows.filter(**{f"{field}__gte": filters.start})
    if filters.end:
        rows = rows.filter(**{f"{field}__lte": filters.end})
    return rows


def purchase_rows(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> tuple[list[MaterialPurchase], bool]:
    rows = scoped_qs(ctx, MaterialPurchase).filter(project=project)
    if filters.kind:
        rows = rows.filter(kind=filters.kind)
    if filters.status:
        rows = rows.filter(status=filters.status)
    rows = _dates(rows, "purchased_on", filters)
    return _limited(rows.select_related("warehouse", "created_by").order_by("-number"))


def issue_query(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> QuerySet[MaterialIssue]:
    rows = scoped_qs(ctx, MaterialIssue).filter(project=project)
    if filters.warehouse_id:
        rows = rows.filter(warehouse_id=filters.warehouse_id)
    if filters.scope:
        rows = rows.filter(scope=filters.scope)
    if filters.cut == UNCUT:
        rows = rows.filter(cut__isnull=True)
    elif filters.cut == CUT:
        rows = rows.filter(cut__isnull=False)
    return _dates(rows, "issued_on", filters)


def issue_rows(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> tuple[list[MaterialIssue], bool]:
    rows = issue_query(ctx, project, filters).select_related("warehouse", "cut", "task")
    return _limited(rows.order_by("-issued_on", "-number"))


def transfer_rows(ctx: RequestContext, project: Project) -> tuple[list[MaterialTransfer], bool]:
    rows = scoped_qs(ctx, MaterialTransfer).filter(project=project)
    return _limited(rows.select_related("source", "target").order_by("-moved_on", "-number"))


def adjustment_rows(
    ctx: RequestContext, project: Project
) -> tuple[list[InventoryAdjustment], bool]:
    rows = scoped_qs(ctx, InventoryAdjustment).filter(project=project)
    return _limited(rows.select_related("warehouse").order_by("-number"))


def cut_rows(ctx: RequestContext, project: Project) -> tuple[list[ConsumptionCut], bool]:
    return _limited(scoped_qs(ctx, ConsumptionCut).filter(project=project).order_by("-number"))


@dataclass(frozen=True)
class Alert:
    text: str
    #: Vista de la pestaña a la que lleva y su consulta ("?corte=sin").
    view: str
    query: str = ""


def tab_alerts(ctx: RequestContext, project: Project, *, today: date | None = None) -> list[Alert]:
    """Spec 3.2, "Avisos de la pestaña". Cuatro consultas."""
    today = today or timezone.localdate()
    alerts: list[Alert] = []
    stale = (
        scoped_qs(ctx, MaterialIssue)
        .filter(
            project=project,
            cut__isnull=True,
            issued_on__lt=today - timedelta(days=STALE_DAYS),
        )
        .aggregate(count=Count("pk"), oldest=Min("issued_on"))
    )
    if stale["count"]:
        alerts.append(
            Alert(
                f"{_plural(stale['count'], 'salida')} sin cortar con más de {STALE_DAYS} días "
                f"(la más vieja, del {stale['oldest']:%d/%m/%Y}).",
                "materials:issues",
                f"?corte={UNCUT}",
            )
        )
    for model, word, view, query in (
        (MaterialPurchase, "compra", "materials:purchases", "?estado=draft"),
        (InventoryAdjustment, "ajuste", "materials:adjustments", ""),
        (ConsumptionCut, "corte", "materials:cuts", ""),
    ):
        drafts = scoped_qs(ctx, model).filter(project=project, status="draft").count()
        if drafts:
            alerts.append(Alert(f"{_plural(drafts, word)} en borrador por aprobar.", view, query))
    return alerts


@dataclass(frozen=True)
class InboxItems:
    purchases: list[MaterialPurchase]
    adjustments: list[InventoryAdjustment]
    cuts: list[ConsumptionCut]

    @property
    def count(self) -> int:
        return len(self.purchases) + len(self.adjustments) + len(self.cuts)


def _waiting(ctx: RequestContext, project: Project) -> tuple[Any, Any, Any]:
    """Borradores de otras personas (ni creados ni editados por quien mira)."""

    def others(model: Any) -> Any:
        return (
            scoped_qs(ctx, model)
            .filter(project=project, status="draft")
            .exclude(created_by_id=ctx.user_id)
            .exclude(updated_by_id=ctx.user_id)
        )

    return others(MaterialPurchase), others(InventoryAdjustment), others(ConsumptionCut)


def inbox_items(ctx: RequestContext, project: Project) -> InboxItems:
    """Para la bandeja "Por aprobar" de Costos (spec 3.3). Tres consultas."""
    purchases, adjustments, cuts = _waiting(ctx, project)
    return InboxItems(
        purchases=list(purchases.select_related("created_by").order_by("number")),
        adjustments=list(adjustments.select_related("created_by", "warehouse").order_by("number")),
        cuts=list(cuts.select_related("created_by").order_by("number")),
    )


def inbox_count(ctx: RequestContext, project: Project) -> int:
    """Para el contador de "Por aprobar" de Costos. Tres consultas."""
    return sum(rows.count() for rows in _waiting(ctx, project))

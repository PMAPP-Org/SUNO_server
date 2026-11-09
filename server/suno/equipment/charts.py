"""Datos para los gráficos CSS del panel de costos (puro: sin BD ni HTML).

Todo se calcula en porcentajes y etiquetas ya formateadas para que la plantilla solo
dibuje: barras con `width`/`height` en %, ejes con marcas y una dona con
`conic-gradient`. Los valores salen de `Decimal` validados, nunca de texto del usuario,
así que el CSS en línea es seguro.

Estilo de los gráficos: analítico y sobrio (figuras numeradas, ejes con título y unidad,
marcas y cuadrícula, etiquetas de datos y fuente), pensado para leerse como un informe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal

#: Moneda de los importes mostrados. La plataforma aún no guarda moneda por organización.
CURRENCY = "COP"

MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

#: Paleta predeterminada de los dashboards (espejo de `--chart-*` en tokens.css), en el orden
#: en que se asignan las series categóricas: rojo corporativo primero y grafito al final.
PALETTE = {
    "red": "#9e2b2f",
    "petrol": "#007a99",
    "amber": "#f28c28",
    "steel": "#6787b7",
    "turquoise": "#19b7c6",
    "graphite": "#3b3b46",
}
SEGMENT_COLORS = tuple(PALETTE.values())

HUNDRED = Decimal(100)
TICK_STEPS = 4  # cuatro intervalos: 0, 25, 50, 75 y 100 % del máximo del eje


@dataclass(frozen=True)
class Segment:
    label: str
    value: Decimal
    pct: Decimal
    color: str
    value_label: str


def scale(values: list[Decimal]) -> list[Decimal]:
    """Alto/ancho relativo (0-100) de cada valor respecto al máximo."""

    top = max(values, default=Decimal(0))
    if top <= 0:
        return [Decimal(0) for _ in values]
    return [(value / top * HUNDRED).quantize(Decimal("0.1")) for value in values]


def share(value: Decimal, total: Decimal) -> Decimal:
    if total <= 0:
        return Decimal(0)
    return (value / total * HUNDRED).quantize(Decimal("0.1"))


def month_label(value: date) -> str:
    """`Nov-2026`, como en las figuras de un informe."""

    return f"{MONTHS_ES[value.month - 1].capitalize()}-{value.year}"


def segments(items: list[tuple[str, Decimal]]) -> list[Segment]:
    total = sum((value for _, value in items), Decimal(0))
    return [
        Segment(
            label=label,
            value=value,
            pct=share(value, total),
            color=SEGMENT_COLORS[index % len(SEGMENT_COLORS)],
            value_label=short_money(value),
        )
        for index, (label, value) in enumerate(items)
    ]


def donut_gradient(parts: list[Segment]) -> str:
    """`conic-gradient(...)` de los segmentos; gris neutro si no hay datos."""

    total = sum((part.value for part in parts), Decimal(0))
    if total <= 0:
        return "conic-gradient(#e9e9e7 0 100%)"
    stops: list[str] = []
    start = Decimal(0)
    for part in parts:
        if part.value <= 0:
            continue
        end = start + part.value / total * HUNDRED
        stops.append(f"{part.color} {start:.2f}% {end:.2f}%")
        start = end
    return f"conic-gradient({', '.join(stops)})"


# ---------------------------------------------------------------------------
# Formato y ejes
# ---------------------------------------------------------------------------


def nice_ceil(value: Decimal) -> Decimal:
    """Menor «número redondo» (1, 2, 2,5, 4, 5 o 8 × 10ⁿ) mayor o igual a `value`."""

    if value <= 0:
        return Decimal(1)
    exponent = Decimal(10) ** (value.adjusted())
    for factor in (Decimal(1), Decimal(2), Decimal("2.5"), Decimal(4), Decimal(5), Decimal(8)):
        if factor * exponent >= value:
            return factor * exponent
    return Decimal(10) * exponent


def fmt_number(number: Decimal) -> str:
    """Número con coma decimal y como mucho un decimal: `62,5`, `100`."""

    text = f"{number:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def short_money(value: Decimal) -> str:
    """Importe corto para etiquetas de datos: `$76,4 M`, `$850 mil`, `$900`."""

    if value >= 1_000_000:
        return f"${fmt_number(value / Decimal(1_000_000))} M"
    if value >= 1_000:
        return f"${fmt_number(value / Decimal(1_000))} mil"
    return f"${fmt_number(value)}"


@dataclass(frozen=True)
class Axis:
    maximum: Decimal
    unit: str  # «COP millones», «COP miles» o «COP»
    ticks: list[tuple[Decimal, str]]  # (posición %, etiqueta)


def money_axis(raw_max: Decimal) -> Axis:
    """Eje de importes con máximo redondo; las marcas van en millones o miles."""

    maximum = nice_ceil(raw_max)
    if maximum >= 1_000_000:
        divisor, unit = Decimal(1_000_000), f"{CURRENCY} millones"
    elif maximum >= 1_000:
        divisor, unit = Decimal(1_000), f"{CURRENCY} miles"
    else:
        divisor, unit = Decimal(1), CURRENCY
    ticks = [
        (Decimal(index * 100 // TICK_STEPS), fmt_number(maximum * index / TICK_STEPS / divisor))
        for index in range(TICK_STEPS + 1)
    ]
    return Axis(maximum=maximum, unit=unit, ticks=ticks)


def percent_axis(raw_max: Decimal) -> Axis:
    """Eje de porcentaje de 25 en 25 %, con tope en el múltiplo de 25 que cubre el dato."""

    top = max(HUNDRED, (raw_max / Decimal(25)).to_integral_value(rounding=ROUND_CEILING) * 25)
    count = int(top // 25)
    ticks = [(Decimal(i * 25) / top * HUNDRED, f"{i * 25} %") for i in range(count + 1)]
    return Axis(maximum=top, unit="% del presupuesto vigente", ticks=ticks)


# ---------------------------------------------------------------------------
# Figura 1: columnas (costo del mes) + línea (acumulado), dos ejes
# ---------------------------------------------------------------------------

#: Una columna más baja que esto muestra su etiqueta encima, no dentro.
LOW_BAR_PCT = Decimal(16)
#: Una etiqueta de la línea por encima de esto se dibuja debajo del punto.
HIGH_POINT_PCT = Decimal(84)


@dataclass(frozen=True)
class ComboPoint:
    label: str
    completed_h: Decimal  # alto % de la parte ejecutada (eje izquierdo)
    scheduled_h: Decimal  # alto % de la parte programada, apilada encima
    total_h: Decimal
    cumulative_y: Decimal  # posición % de la línea (eje derecho)
    x: Decimal  # centro de la columna, en %
    cost: Decimal
    scheduled: Decimal
    cumulative: Decimal
    hours: Decimal
    cost_label: str
    cumulative_label: str
    low_bar: bool
    label_below: bool


@dataclass(frozen=True)
class ComboChart:
    points: list[ComboPoint]
    left: Axis
    right: Axis
    polyline: str  # `x,y x,y …` en el viewBox 0-100


def build_combo(rows: list[tuple[str, Decimal, Decimal, Decimal]]) -> ComboChart:
    """`rows`: (etiqueta, costo del mes, de ese costo cuánto está programado, horas).

    Las columnas usan su propio eje (izquierdo) y la línea el acumulado en otro (derecho),
    cada uno con su máximo redondo, de modo que ambas series se leen a su escala.
    """

    count = len(rows)
    left = money_axis(max((cost for _, cost, _, _ in rows), default=Decimal(0)))
    total = sum((cost for _, cost, _, _ in rows), Decimal(0))
    right = money_axis(total)
    points: list[ComboPoint] = []
    running = Decimal(0)
    for index, (label, cost, scheduled, hours) in enumerate(rows):
        running += cost
        x = ((Decimal(index) + Decimal("0.5")) / count * HUNDRED).quantize(Decimal("0.01"))
        total_h = (cost / left.maximum * HUNDRED).quantize(Decimal("0.1"))
        y = (running / right.maximum * HUNDRED).quantize(Decimal("0.01"))
        points.append(
            ComboPoint(
                label=label,
                completed_h=((cost - scheduled) / left.maximum * HUNDRED).quantize(Decimal("0.1")),
                scheduled_h=(scheduled / left.maximum * HUNDRED).quantize(Decimal("0.1")),
                total_h=total_h,
                cumulative_y=y,
                x=x,
                cost=cost,
                scheduled=scheduled,
                cumulative=running,
                hours=hours,
                cost_label=short_money(cost),
                cumulative_label=short_money(running),
                low_bar=total_h < LOW_BAR_PCT,
                label_below=y > HIGH_POINT_PCT,
            )
        )
    polyline = " ".join(f"{p.x},{HUNDRED - p.cumulative_y}" for p in points)
    return ComboChart(points=points, left=left, right=right, polyline=polyline)


# ---------------------------------------------------------------------------
# Figura 3: barras horizontales por categoría, con eje de importes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HBar:
    label: str
    code: str
    width: Decimal
    value: Decimal
    value_label: str
    share: Decimal
    hours: Decimal
    ident: object


@dataclass(frozen=True)
class HBarChart:
    items: list[HBar]
    axis: Axis


def build_hbars(rows: list[tuple[str, str, Decimal, Decimal, object]], total: Decimal) -> HBarChart:
    """`rows`: (nombre, código, costo, horas, id para enlazar); `total` para el peso %."""

    axis = money_axis(max((cost for _, _, cost, _, _ in rows), default=Decimal(0)))
    items = [
        HBar(
            label=name,
            code=code,
            width=(cost / axis.maximum * HUNDRED).quantize(Decimal("0.1")),
            value=cost,
            value_label=short_money(cost),
            share=share(cost, total),
            hours=hours,
            ident=ident,
        )
        for name, code, cost, hours, ident in rows
    ]
    return HBarChart(items=items, axis=axis)


# ---------------------------------------------------------------------------
# Figura 4: consumo del presupuesto (porcentaje, con la marca del 100 %)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bullet:
    code: str
    description: str
    pct: Decimal | None
    width: Decimal
    over: bool
    imputed_label: str
    budget_label: str


@dataclass(frozen=True)
class BulletChart:
    items: list[Bullet]
    axis: Axis
    marker: Decimal  # posición % de la marca del 100 % del presupuesto
    step: Decimal  # separación % entre líneas de la cuadrícula (cada 25 %)


def build_bullets(rows: list[tuple[str, str, Decimal, Decimal | None]]) -> BulletChart:
    """`rows`: (código, descripción, imputado, presupuestado o `None` si no hay vigente)."""

    pcts = [
        (imputed / budgeted * HUNDRED) if budgeted else None for _, _, imputed, budgeted in rows
    ]
    axis = percent_axis(max((p for p in pcts if p is not None), default=Decimal(0)))
    items = [
        Bullet(
            code=code,
            description=description,
            pct=pct.quantize(Decimal("0.1")) if pct is not None else None,
            width=(pct / axis.maximum * HUNDRED).quantize(Decimal("0.1")) if pct else Decimal(0),
            over=bool(pct is not None and pct > HUNDRED),
            imputed_label=short_money(imputed),
            budget_label=short_money(budgeted) if budgeted is not None else "sin presupuesto",
        )
        for (code, description, imputed, budgeted), pct in zip(rows, pcts, strict=True)
    ]
    return BulletChart(
        items=items,
        axis=axis,
        marker=HUNDRED / axis.maximum * HUNDRED,
        step=Decimal(25) / axis.maximum * HUNDRED,
    )

"""Lector de MSPDI (el XML que exporta MS Project con "Guardar como → XML").

Puro: recibe bytes y devuelve un `ParsedSchedule`; no toca la base de datos.
Un lector futuro de `.mpp` (MPXJ) solo tiene que producir el mismo
`ParsedSchedule` para reutilizar todo lo demás.

Seguridad: el XML lo sube un usuario, así que se lee con `defusedxml`
(sin expansión de entidades ni entidades externas) y con un límite de
tamaño previo al parseo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .workdays import CalendarException, WorkCalendar

MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MINUTES_PER_DAY = 480
#: `PredecessorLink/Type` de MSPDI → código de `DependencyType`.
LINK_TYPES = {0: "FF", 1: "FS", 2: "SF", 3: "SS"}
#: `Assignment/ResourceUID` de una tarea sin recurso.
UNASSIGNED_RESOURCE = -65535
_DURATION = re.compile(
    r"^-?P(?:(?P<d>\d+(?:\.\d+)?)D)?T?(?:(?P<h>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$"
)


class MspdiError(ValueError):
    """El archivo no se puede importar; el mensaje es para el usuario."""


@dataclass(frozen=True)
class ParsedTask:
    uid: int
    sort_order: int
    name: str
    wbs: str
    outline_number: str
    outline_level: int
    is_summary: bool
    is_milestone: bool
    start: date | None
    finish: date | None
    actual_finish: date | None
    duration_days: Decimal | None
    baseline_start: date | None
    baseline_finish: date | None
    baseline_duration_days: Decimal | None
    project_pct: Decimal | None
    project_physical_pct: Decimal | None
    total_slack_days: Decimal | None
    free_slack_days: Decimal | None
    is_critical: bool


@dataclass(frozen=True)
class ParsedDependency:
    predecessor_uid: int
    successor_uid: int
    type: str
    lag_days: Decimal


@dataclass(frozen=True)
class ParsedResource:
    uid: int
    name: str


@dataclass(frozen=True)
class ParsedSchedule:
    status_date: date | None
    calendar: WorkCalendar
    tasks: tuple[ParsedTask, ...]
    dependencies: tuple[ParsedDependency, ...]
    resources: tuple[ParsedResource, ...]
    #: Pares (task_uid, resource_uid).
    assignments: tuple[tuple[int, int], ...]


class _Reader:
    """Acceso a hijos por nombre local, con o sin el namespace de MSPDI."""

    def __init__(self, root: Element) -> None:
        self.ns = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""

    def q(self, name: str) -> str:
        return f"{{{self.ns}}}{name}" if self.ns else name

    def path(self, *names: str) -> str:
        return "/".join(self.q(n) for n in names)

    def text(self, el: Element, name: str) -> str | None:
        child = el.find(self.q(name))
        if child is None or child.text is None:
            return None
        value = child.text.strip()
        return value or None

    def integer(self, el: Element, name: str) -> int | None:
        value = self.text(el, name)
        if value is None:
            return None
        try:
            return int(Decimal(value))
        except InvalidOperation as exc:
            raise MspdiError(f"Número inválido en <{name}>: {value}.") from exc

    def decimal(self, el: Element, name: str) -> Decimal | None:
        value = self.text(el, name)
        if value is None:
            return None
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise MspdiError(f"Número inválido en <{name}>: {value}.") from exc

    def flag(self, el: Element, name: str) -> bool:
        return self.text(el, name) in ("1", "true")


def _to_date(value: str | None, uid: int | str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise MspdiError(f"Fecha inválida en la tarea {uid}: {value}.") from exc


def _duration_days(value: str | None, minutes_per_day: int) -> Decimal | None:
    if value is None:
        return None
    match = _DURATION.match(value)
    if match is None:
        return None
    parts = {k: Decimal(v) if v else Decimal(0) for k, v in match.groupdict().items()}
    minutes = parts["d"] * minutes_per_day + parts["h"] * 60 + parts["m"] + parts["s"] / 60
    return (minutes / minutes_per_day).quantize(Decimal("0.01"))


def _tenths_to_days(value: Decimal | None, minutes_per_day: int) -> Decimal | None:
    if value is None:
        return None
    return (value / 10 / minutes_per_day).quantize(Decimal("0.01"))


def parse_mspdi(data: bytes, *, max_bytes: int = MAX_BYTES) -> ParsedSchedule:
    if len(data) > max_bytes:
        raise MspdiError(f"El archivo supera {max_bytes // (1024 * 1024)} MB.")
    try:
        root = fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise MspdiError("El archivo no es un XML válido.") from exc
    if root.tag.rsplit("}", 1)[-1] != "Project":
        raise MspdiError("El XML no es un proyecto de MS Project (formato MSPDI).")

    r = _Reader(root)
    minutes_per_day = r.integer(root, "MinutesPerDay") or DEFAULT_MINUTES_PER_DAY
    tasks, dependencies = _tasks(r, root, minutes_per_day)
    task_uids = {t.uid for t in tasks}
    dependencies = tuple(
        d for d in dependencies if d.predecessor_uid in task_uids and d.successor_uid in task_uids
    )
    resources = _resources(r, root)
    resource_uids = {res.uid for res in resources}
    assignments: list[tuple[int, int]] = []
    for el in root.iterfind(r.path("Assignments", "Assignment")):
        pair = (r.integer(el, "TaskUID"), r.integer(el, "ResourceUID"))
        if pair[0] in task_uids and pair[1] in resource_uids and pair not in assignments:
            assignments.append(pair)  # type: ignore[arg-type]
    return ParsedSchedule(
        status_date=_to_date(r.text(root, "StatusDate"), "del proyecto"),
        calendar=_calendar(r, root),
        tasks=tasks,
        dependencies=dependencies,
        resources=resources,
        assignments=tuple(assignments),
    )


def _tasks(
    r: _Reader, root: Element, minutes_per_day: int
) -> tuple[tuple[ParsedTask, ...], tuple[ParsedDependency, ...]]:
    tasks: list[ParsedTask] = []
    deps: list[ParsedDependency] = []
    seen: set[int] = set()
    for el in root.iterfind(r.path("Tasks", "Task")):
        if r.flag(el, "IsNull"):
            continue
        uid = r.integer(el, "UID")
        if uid is None:
            raise MspdiError("Hay una tarea sin UniqueID.")
        level = r.integer(el, "OutlineLevel") or 0
        if uid == 0 or level == 0:
            continue  # resumen del proyecto
        name = r.text(el, "Name")
        if not name:
            raise MspdiError(f"La tarea {uid} no tiene nombre.")
        if uid in seen:
            raise MspdiError(f"UniqueID repetido: {uid}.")
        seen.add(uid)
        outline = r.text(el, "OutlineNumber")
        if not outline:
            raise MspdiError(f"La tarea {uid} no tiene número de esquema (OutlineNumber).")
        baseline = _baseline(r, el)
        tasks.append(
            ParsedTask(
                uid=uid,
                sort_order=r.integer(el, "ID") or uid,
                name=name[:500],
                wbs=(r.text(el, "WBS") or outline)[:100],
                outline_number=outline[:100],
                outline_level=level,
                is_summary=r.flag(el, "Summary"),
                is_milestone=r.flag(el, "Milestone"),
                start=_to_date(r.text(el, "Start"), uid),
                finish=_to_date(r.text(el, "Finish"), uid),
                actual_finish=_to_date(r.text(el, "ActualFinish"), uid),
                duration_days=_duration_days(r.text(el, "Duration"), minutes_per_day),
                baseline_start=_to_date(r.text(baseline, "Start"), uid)
                if baseline is not None
                else None,
                baseline_finish=_to_date(r.text(baseline, "Finish"), uid)
                if baseline is not None
                else None,
                baseline_duration_days=(
                    _duration_days(r.text(baseline, "Duration"), minutes_per_day)
                    if baseline is not None
                    else None
                ),
                project_pct=r.decimal(el, "PercentComplete"),
                project_physical_pct=r.decimal(el, "PhysicalPercentComplete"),
                total_slack_days=_tenths_to_days(r.decimal(el, "TotalSlack"), minutes_per_day),
                free_slack_days=_tenths_to_days(r.decimal(el, "FreeSlack"), minutes_per_day),
                is_critical=r.flag(el, "Critical"),
            )
        )
        for link in el.iterfind(r.q("PredecessorLink")):
            pred = r.integer(link, "PredecessorUID")
            if pred is None:
                continue
            deps.append(
                ParsedDependency(
                    predecessor_uid=pred,
                    successor_uid=uid,
                    type=LINK_TYPES.get(r.integer(link, "Type") or 1, "FS"),
                    lag_days=_tenths_to_days(r.decimal(link, "LinkLag"), minutes_per_day)
                    or Decimal(0),
                )
            )
    return tuple(tasks), tuple(deps)


def _baseline(r: _Reader, el: Element) -> Element | None:
    for baseline in el.iterfind(r.q("Baseline")):
        if r.integer(baseline, "Number") == 0:
            return baseline
    return None


def _resources(r: _Reader, root: Element) -> tuple[ParsedResource, ...]:
    out: dict[int, ParsedResource] = {}
    for el in root.iterfind(r.path("Resources", "Resource")):
        uid, name = r.integer(el, "UID"), r.text(el, "Name")
        if uid is None or not name or uid == UNASSIGNED_RESOURCE:
            continue
        out[uid] = ParsedResource(uid=uid, name=name[:255])
    return tuple(out.values())


def _calendar(r: _Reader, root: Element) -> WorkCalendar:
    calendars: dict[str | None, Any] = {
        r.text(c, "UID"): c for c in root.iterfind(r.path("Calendars", "Calendar"))
    }
    if not calendars:
        return WorkCalendar()
    cal = calendars.get(r.text(root, "CalendarUID"))
    if cal is None:
        cal = next(iter(calendars.values()))
    chain = [cal]
    base = calendars.get(r.text(cal, "BaseCalendarUID"))
    if base is not None and base is not cal:
        chain.insert(0, base)
    weekdays = set(range(5))
    exceptions: list[CalendarException] = []
    for c in chain:
        for wd in c.iterfind(r.path("WeekDays", "WeekDay")):
            day_type = r.integer(wd, "DayType")
            working = r.flag(wd, "DayWorking")
            if day_type == 0:
                period = _period(r, wd, working)
                if period:
                    exceptions.append(period)
            elif day_type is not None and 1 <= day_type <= 7:
                python_weekday = (day_type + 5) % 7  # 1 = domingo → 6; 2 = lunes → 0
                (weekdays.add if working else weekdays.discard)(python_weekday)
        for ex in c.iterfind(r.path("Exceptions", "Exception")):
            period = _period(r, ex, r.flag(ex, "DayWorking"))
            if period:
                exceptions.append(period)
    try:
        return WorkCalendar(working_weekdays=weekdays, exceptions=exceptions)
    except ValueError as exc:
        raise MspdiError(str(exc)) from exc


def _period(r: _Reader, el: Element, working: bool) -> CalendarException | None:
    period = el.find(r.q("TimePeriod"))
    if period is None:
        return None
    start = _to_date(r.text(period, "FromDate"), "del calendario")
    end = _to_date(r.text(period, "ToDate"), "del calendario")
    if start is None or end is None or end < start:
        return None
    return CalendarException(start=start, end=end, working=working)

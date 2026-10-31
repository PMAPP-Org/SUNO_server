"""XML MSPDI de prueba, generados en código para que cada test diga qué cambia.

`v1()` y `v2()` son dos versiones del mismo cronograma (marzo de 2026, lunes
a viernes, festivo el lunes 23):

    v1                                      v2 (cambios)
    1   Preliminares (resumen, etapa)
    1.1 Localización   uid 2  100 %
    1.2 Descapote      uid 3   60 %         100 %, terminó el 18
    2   Estructura (resumen, etapa) uid 4
    2.1 Columnas       uid 5    0 %         retirada
                                            2.1 Vigas uid 7 (nueva)
    2.2 Fin de obra    uid 6  hito          línea base pasa del 26 al 27
"""

from __future__ import annotations

from xml.sax.saxutils import escape

NS = "http://schemas.microsoft.com/project"


def _el(tag: str, value: object) -> str:
    return f"<{tag}>{escape(str(value))}</{tag}>"


def task_xml(
    uid: int,
    name: str,
    outline: str,
    *,
    summary: bool = False,
    milestone: bool = False,
    start: str | None = None,
    finish: str | None = None,
    duration_h: int | None = None,
    baseline: tuple[str, str, int] | None = None,
    pct: int | None = None,
    physical_pct: int | None = None,
    actual_finish: str | None = None,
    critical: bool = False,
    total_slack: int = 0,
    free_slack: int = 0,
    preds: tuple[tuple[int, int], ...] = (),
    sort_id: int | None = None,
) -> str:
    level = 0 if outline == "0" else outline.count(".") + 1
    parts = [
        _el("UID", uid),
        _el("ID", uid if sort_id is None else sort_id),
        _el("Name", name),
        _el("WBS", outline),
        _el("OutlineNumber", outline),
        _el("OutlineLevel", level),
        _el("Summary", int(summary)),
        _el("Milestone", int(milestone)),
    ]
    if start:
        parts.append(_el("Start", f"{start}T08:00:00"))
    if finish:
        parts.append(_el("Finish", f"{finish}T17:00:00"))
    if duration_h is not None:
        parts.append(_el("Duration", f"PT{duration_h}H0M0S"))
    if actual_finish:
        parts.append(_el("ActualFinish", f"{actual_finish}T17:00:00"))
    if pct is not None:
        parts.append(_el("PercentComplete", pct))
    if physical_pct is not None:
        parts.append(_el("PhysicalPercentComplete", physical_pct))
    parts += [
        _el("Critical", int(critical)),
        _el("TotalSlack", total_slack),
        _el("FreeSlack", free_slack),
    ]
    for pred_uid, type_code in preds:
        parts.append(
            "<PredecessorLink>"
            f"{_el('PredecessorUID', pred_uid)}{_el('Type', type_code)}{_el('LinkLag', 0)}"
            "</PredecessorLink>"
        )
    if baseline:
        b_start, b_finish, b_hours = baseline
        parts.append(
            "<Baseline>"
            f"{_el('Number', 0)}{_el('Start', f'{b_start}T08:00:00')}"
            f"{_el('Finish', f'{b_finish}T17:00:00')}{_el('Duration', f'PT{b_hours}H0M0S')}"
            "</Baseline>"
        )
    return "<Task>" + "".join(parts) + "</Task>"


def _calendar_xml(holidays: tuple[str, ...]) -> str:
    weekdays = "".join(
        f"<WeekDay>{_el('DayType', d)}{_el('DayWorking', int(d not in (1, 7)))}</WeekDay>"
        for d in range(1, 8)
    )
    exceptions = "".join(
        "<Exception><TimePeriod>"
        f"{_el('FromDate', f'{h}T00:00:00')}{_el('ToDate', f'{h}T23:59:00')}"
        f"</TimePeriod>{_el('DayWorking', 0)}</Exception>"
        for h in holidays
    )
    return (
        "<Calendars><Calendar>"
        f"{_el('UID', 1)}{_el('Name', 'Estándar')}{_el('IsBaseCalendar', 1)}"
        f"<WeekDays>{weekdays}</WeekDays><Exceptions>{exceptions}</Exceptions>"
        "</Calendar></Calendars>"
    )


def project_xml(
    tasks: list[str],
    *,
    status_date: str | None = "2026-03-20",
    holidays: tuple[str, ...] = ("2026-03-23",),
    with_calendar: bool = True,
    resources: tuple[tuple[int, str], ...] = (),
    assignments: tuple[tuple[int, int], ...] = (),
    namespace: bool = True,
) -> bytes:
    head = f'<Project xmlns="{NS}">' if namespace else "<Project>"
    body = [_el("Name", "Obra demo"), _el("MinutesPerDay", 480), _el("CalendarUID", 1)]
    if status_date:
        body.append(_el("StatusDate", f"{status_date}T17:00:00"))
    if with_calendar:
        body.append(_calendar_xml(holidays))
    summary_task = task_xml(0, "Obra demo", "0", summary=True)
    body.append("<Tasks>" + summary_task + "".join(tasks) + "</Tasks>")
    body.append(
        "<Resources>"
        + "".join(f"<Resource>{_el('UID', u)}{_el('Name', n)}</Resource>" for u, n in resources)
        + "</Resources>"
    )
    body.append(
        "<Assignments>"
        + "".join(
            f"<Assignment>{_el('TaskUID', t)}{_el('ResourceUID', r)}</Assignment>"
            for t, r in assignments
        )
        + "</Assignments>"
    )
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + head + "".join(body)
    return (xml + "</Project>").encode("utf-8")


RESOURCES = ((1, "Cuadrilla A"), (2, "Topógrafo"))


def v1() -> bytes:
    return project_xml(
        [
            task_xml(
                1,
                "Preliminares",
                "1",
                summary=True,
                start="2026-03-02",
                finish="2026-03-17",
                baseline=("2026-03-02", "2026-03-13", 80),
                pct=76,
            ),
            task_xml(
                2,
                "Localización",
                "1.1",
                start="2026-03-02",
                finish="2026-03-06",
                duration_h=40,
                baseline=("2026-03-02", "2026-03-06", 40),
                pct=100,
                physical_pct=100,
                actual_finish="2026-03-06",
                total_slack=4800,
            ),
            task_xml(
                3,
                "Descapote",
                "1.2",
                start="2026-03-09",
                finish="2026-03-17",
                duration_h=56,
                baseline=("2026-03-09", "2026-03-13", 40),
                pct=60,
                physical_pct=50,
                critical=True,
                preds=((2, 1),),
            ),
            task_xml(
                4,
                "Estructura",
                "2",
                summary=True,
                start="2026-03-18",
                finish="2026-03-30",
                baseline=("2026-03-16", "2026-03-26", 64),
                pct=0,
            ),
            task_xml(
                5,
                "Columnas",
                "2.1",
                start="2026-03-18",
                finish="2026-03-30",
                duration_h=64,
                baseline=("2026-03-16", "2026-03-26", 64),
                pct=0,
                critical=True,
                preds=((3, 1),),
            ),
            task_xml(
                6,
                "Fin de obra",
                "2.2",
                milestone=True,
                start="2026-03-30",
                finish="2026-03-30",
                duration_h=0,
                baseline=("2026-03-26", "2026-03-26", 0),
                pct=0,
                critical=True,
                preds=((5, 1),),
            ),
        ],
        resources=RESOURCES,
        assignments=((2, 2), (3, 1), (5, 1), (6, -65535)),
    )


def v2() -> bytes:
    return project_xml(
        [
            task_xml(
                1,
                "Preliminares",
                "1",
                summary=True,
                start="2026-03-02",
                finish="2026-03-18",
                baseline=("2026-03-02", "2026-03-13", 80),
                pct=100,
            ),
            task_xml(
                2,
                "Localización",
                "1.1",
                start="2026-03-02",
                finish="2026-03-06",
                duration_h=40,
                baseline=("2026-03-02", "2026-03-06", 40),
                pct=100,
                actual_finish="2026-03-06",
            ),
            task_xml(
                3,
                "Descapote",
                "1.2",
                start="2026-03-09",
                finish="2026-03-18",
                duration_h=64,
                baseline=("2026-03-09", "2026-03-13", 40),
                pct=100,
                actual_finish="2026-03-18",
                critical=True,
                preds=((2, 1),),
            ),
            task_xml(
                4,
                "Estructura",
                "2",
                summary=True,
                start="2026-03-19",
                finish="2026-03-31",
                baseline=("2026-03-16", "2026-03-27", 72),
                pct=10,
            ),
            task_xml(
                7,
                "Vigas",
                "2.1",
                start="2026-03-19",
                finish="2026-03-31",
                duration_h=64,
                baseline=("2026-03-16", "2026-03-27", 72),
                pct=10,
                critical=True,
                preds=((3, 1),),
                sort_id=5,
            ),
            task_xml(
                6,
                "Fin de obra",
                "2.2",
                milestone=True,
                start="2026-03-31",
                finish="2026-03-31",
                duration_h=0,
                baseline=("2026-03-27", "2026-03-27", 0),
                pct=0,
                critical=True,
                preds=((7, 1),),
            ),
        ],
        status_date="2026-03-27",
        resources=RESOURCES,
        assignments=((2, 2), (3, 1), (7, 1)),
    )

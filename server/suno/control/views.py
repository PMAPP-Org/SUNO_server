"""Página "Control" del proyecto (costos, pieza 5): valor planeado, valor ganado y curva S.

Mismo acceso que la Matriz (`get_matrix_project`: quien ve presupuestos; el agente de campo
recibe 404). Exportar pide además `can_export_budgets` y queda en la auditoría
(`control.exported`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.download import content_disposition
from suno.core.policies import can_export_budgets
from suno.matrix.access import get_matrix_project
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from .chart import build_s_curve
from .earned_value import BreakdownRow, ControlResult, compute
from .export import control_workbook
from .loader import load_control_input
from .periods import DEFAULT_KIND, PERIOD_KINDS, PeriodRangeError

BREAKDOWN_TABS = {"capitulo": "Por capítulo", "tarea": "Por tarea", "ubicacion": "Por ubicación"}
DEFAULT_TAB = "capitulo"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class Options:
    cut: date
    kind: str
    tab: str

    @property
    def query(self) -> str:
        """Corte y periodo, para las pestañas del desglose y la exportación."""
        return urlencode({"corte": self.cut.isoformat(), "periodo": self.kind})


def _options(request: HttpRequest) -> Options:
    kind = request.GET.get("periodo", "")
    tab = request.GET.get("desglose", "")
    return Options(
        cut=_parse_date(request.GET.get("corte")) or timezone.localdate(),
        kind=kind if kind in PERIOD_KINDS else DEFAULT_KIND,
        tab=tab if tab in BREAKDOWN_TABS else DEFAULT_TAB,
    )


def _breakdown(result: ControlResult, tab: str) -> tuple[BreakdownRow, ...]:
    if tab == "tarea":
        return result.by_task
    if tab == "ubicacion":
        return result.by_location
    return result.by_chapter


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def dashboard(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    options = _options(request)
    result: ControlResult | None = None
    error = ""
    try:
        result = compute(load_control_input(ctx, project, cut=options.cut, kind=options.kind))
    except PeriodRangeError as exc:
        error = str(exc)
    return _render(
        request,
        ctx,
        "suno/control/dashboard.html",
        {
            **_project_context(ctx, project),
            "header_cut": options.cut,
            "options": options,
            "period_kinds": PERIOD_KINDS,
            "tabs": BREAKDOWN_TABS,
            "result": result,
            "rows": _breakdown(result, options.tab) if result else (),
            "curve": build_s_curve(result.periods, result.indicators.bac) if result else None,
            "can_export_control": can_export_budgets(ctx, ctx.org_id),
            "error": error,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def export(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Periodos y desglose del corte, en Excel (auditado, como la comparación del presupuesto)."""
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    options = _options(request)
    try:
        result = compute(load_control_input(ctx, project, cut=options.cut, kind=options.kind))
    except PeriodRangeError as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain; charset=utf-8")
    content = control_workbook(result)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTROL_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"cut": options.cut.isoformat(), "period": options.kind},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"control-{project.code or project.name}-{options.cut.isoformat()}",
        ".xlsx",
        fallback="control",
    )
    return response

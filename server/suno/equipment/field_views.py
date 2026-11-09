"""Registros de campo desde la web: reporte de uso propio y preoperacional.

Son los mismos servicios que usa la app móvil (`report_equipment_usage`,
`submit_inspection`), así que las reglas y los permisos son idénticos: el operador
reporta lo suyo sobre los equipos que tiene asignados.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from suno.core.access import Capability
from suno.equipment.forms import UsageReportForm
from suno.equipment.inspections import AnswerIn, checklist_for, submit_inspection
from suno.equipment.services import get_visible_equipment
from suno.equipment.usage_reports import report_equipment_usage
from suno.equipment.views import _can, _ctx, _message_validation, _render

__all__ = ["preoperational_create", "usage_report_create"]


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def usage_report_create(request: HttpRequest) -> HttpResponse:
    """Reporta la jornada propia con un equipo asignado (queda completada)."""

    ctx = _ctx(request)
    initial: dict[str, Any] = {"usage_date": date.today()}
    if request.GET.get("equipment"):
        initial["equipment"] = request.GET["equipment"]
    form = UsageReportForm(ctx, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            report_equipment_usage(
                ctx,
                usage_id=uuid.uuid4(),
                equipment_id=data["equipment"].pk,
                project_id=data["project"].pk,
                usage_date=data["usage_date"],
                start_time=data["start_time"],
                end_time=data["end_time"],
                activity=data["activity"],
                location=data["location"],
                start_meter=data["start_meter"],
                end_meter=data["end_meter"],
                notes=data["notes"],
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        except (Http404, PermissionDenied):
            form.add_error(None, "No tienes ese equipo asignado para reportar su uso.")
        else:
            messages.success(request, "Uso registrado.")
            return redirect(f"{reverse('equipment:detail', args=[data['equipment'].pk])}?tab=usage")
    return _render(
        request,
        ctx,
        "suno/equipment/form.html",
        {"form": form, "title": "Reportar mi uso del equipo"},
    )


def _decimal(raw: str) -> Decimal | None:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValidationError("La lectura del medidor debe ser un número.") from None


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def preoperational_create(request: HttpRequest, equipment_id: UUID) -> HttpResponse:
    """Preoperacional con la lista de chequeo vigente del tipo de equipo."""

    ctx = _ctx(request)
    equipment = get_visible_equipment(ctx, equipment_id)
    if not _can(ctx, equipment, Capability.CREATE):
        raise PermissionDenied
    checklist = checklist_for(ctx, equipment.equipment_type_id)
    needs_meter = equipment.control_scheme == "meter" and bool(equipment.primary_meter_unit)
    answers = request.POST if request.method == "POST" else {}
    if request.method == "POST" and checklist:
        post = request.POST
        try:
            missing = [
                item.label for item in checklist if post.get(f"ok_{item.pk}") not in ("yes", "no")
            ]
            if missing:
                raise ValidationError(f"Responde todos los ítems. Falta: {missing[0]}.")
            inspection = submit_inspection(
                ctx,
                inspection_id=uuid.uuid4(),
                equipment_id=equipment.pk,
                captured_at=timezone.now(),
                answers=[
                    AnswerIn(
                        item_id=item.pk,
                        ok=post.get(f"ok_{item.pk}") == "yes",
                        note=post.get(f"note_{item.pk}", ""),
                    )
                    for item in checklist
                ],
                meter_unit=equipment.primary_meter_unit if needs_meter else "",
                meter_value=_decimal(post.get("meter_value", "")) if needs_meter else None,
                notes=post.get("notes", ""),
            )
        except ValidationError as exc:
            _message_validation(request, exc)
        else:
            messages.success(
                request,
                "Preoperacional enviado con novedades."
                if inspection.has_faults
                else "Preoperacional enviado.",
            )
            return redirect("equipment:inspection_detail", inspection.pk)
    return _render(
        request,
        ctx,
        "suno/equipment/preoperational_form.html",
        {
            "equipment": equipment,
            "checklist": checklist,
            "rows": [
                {
                    "item": item,
                    "ok": answers.get(f"ok_{item.pk}", ""),
                    "note": answers.get(f"note_{item.pk}", ""),
                }
                for item in checklist
            ],
            "needs_meter": needs_meter,
            "meter_label": equipment.get_primary_meter_unit_display() if needs_meter else "",
            "answers": answers,
        },
    )

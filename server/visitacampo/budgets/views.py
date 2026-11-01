"""Páginas de presupuestos en /plataforma/proyectos/<id>/presupuestos/ (costos, pieza 3).

Mismo esquema que `projects/views.py`, del que se reutilizan la resolución del
contexto, el render y la cabecera del proyecto (nombres privados importados a
propósito: una sola cabecera y un solo contexto de proyecto). Quien no puede
ver presupuestos (el agente de campo) recibe 404 en todas estas URL antes de
mirar nada más (`access.get_budget_project`); una acción para la que falta el
rol responde 403.
"""

from __future__ import annotations

from uuid import UUID

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, Project
from visitacampo.core.policies import can_export_budgets, can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_budget, get_budget_project
from .comparison import budget_summaries, build_comparison, filter_rows

DUPLICATE_NAME = "Ya existe un presupuesto con ese nombre en este proyecto."


class BudgetForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nombre")
    code = forms.CharField(max_length=50, required=False, label="Código")
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Descripción"
    )


def _budget_context(
    ctx: RequestContext, project: Project, budget: Budget | None = None
) -> dict[str, object]:
    return {
        **_project_context(ctx, project),
        "budget": budget,
        "can_manage_budgets": can_manage_budgets(ctx, ctx.org_id),
        "can_export_budgets": can_export_budgets(ctx, ctx.org_id),
    }


def _require_manage(ctx: RequestContext) -> None:
    if not can_manage_budgets(ctx, ctx.org_id):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def budget_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    form = BudgetForm(request.POST or None)
    if request.method == "POST":
        _require_manage(ctx)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            budgets = scoped_qs(ctx, Budget).filter(project=project)
            if budgets.filter(name__iexact=name).exists():
                form.add_error("name", DUPLICATE_NAME)
            else:
                try:
                    with transaction.atomic():
                        budget = scoped_qs(ctx, Budget).create(
                            org_id=ctx.org_id,
                            project=project,
                            name=name,
                            code=form.cleaned_data["code"].strip(),
                            description=form.cleaned_data["description"].strip(),
                            created_by_id=ctx.user_id,
                        )
                        audit.record(
                            ctx,
                            action=AuditAction.BUDGET_CREATED,
                            target=f"budget:{budget.pk}",
                            metadata={"project": str(project.pk), "name": name},
                        )
                except IntegrityError:
                    form.add_error("name", DUPLICATE_NAME)
                else:
                    return redirect("budgets:detail", project.pk, budget.pk)
    summaries, totals = budget_summaries(ctx, project)
    return _render(
        request,
        ctx,
        "suno/budgets/list.html",
        {**_budget_context(ctx, project), "summaries": summaries, "totals": totals, "form": form},
    )


@login_required(login_url="platform_login")
def budget_detail(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    """Comparación contractual contra vigente (la página principal del presupuesto)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    data = build_comparison(ctx, budget)
    chapter = _parse_uuid(request.GET.get("capitulo"))
    query = (request.GET.get("q") or "").strip()[:100]
    changes_only = request.GET.get("cambios") == "1"
    rows = filter_rows(data.rows, chapter_id=chapter, changes_only=changes_only, query=query)
    return _render(
        request,
        ctx,
        "suno/budgets/compare.html",
        {
            **_budget_context(ctx, project, budget),
            "data": data,
            "rows": rows,
            "filters": {"capitulo": str(chapter or ""), "cambios": changes_only, "q": query},
        },
    )

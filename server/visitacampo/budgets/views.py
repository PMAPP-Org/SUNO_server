"""Páginas de presupuestos en /plataforma/proyectos/<id>/presupuestos/ (costos, pieza 3).

Mismo esquema que `projects/views.py`, del que se reutilizan la resolución del
contexto, el render y la cabecera del proyecto (nombres privados importados a
propósito: una sola cabecera y un solo contexto de proyecto). Quien no puede
ver presupuestos (el agente de campo) recibe 404 en todas estas URL antes de
mirar nada más (`access.get_budget_project`); una acción para la que falta el
rol responde 403.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from openpyxl.utils import get_column_letter

from visitacampo.backoffice.export import safe_cell, to_xlsx
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
)
from visitacampo.core.policies import can_export_budgets, can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_budget, get_budget_project
from .comparison import (
    ComparisonRow,
    budget_summaries,
    build_comparison,
    filter_rows,
    version_rows,
)
from .excel import KIND_CHAPTER, MAX_BYTES, ExcelRejected, preview_sheet
from .importer import (
    ImportRejected,
    apply_version,
    charges_of,
    create_upload,
    discard_version,
    mark_contractual,
    prepare_version,
    update_pending,
)
from .mapping import (
    FIELD_LABELS,
    FIELDS,
    MAX_COLUMNS,
    REQUIRED_FIELDS,
    ColumnMapping,
    MappingError,
    suggest_mapping,
)
from .totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    MAX_CHARGES,
    ChargeError,
    ChargeSpec,
    base_line_order,
    compute_charges,
    describe_base,
    line_base,
    parse_number,
    pct_text,
    validate_charges,
)

DUPLICATE_NAME = "Ya existe un presupuesto con ese nombre en este proyecto."
MIN_PREVIEW_COLUMNS = 6
CELL_LIMIT = 60
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT_HEADER = [
    "codigo", "descripcion", "unidad", "tipo", "estado",
    "cant_contractual", "vr_unitario_contractual", "total_contractual",
    "cant_vigente", "vr_unitario_vigente", "total_vigente",
    "diferencia", "diferencia_pct",
]  # fmt: skip


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


def _int(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 0


def _saved_mapping(budget: Budget) -> ColumnMapping | None:
    if not budget.column_mapping:
        return None
    try:
        return ColumnMapping.from_json(budget.column_mapping)
    except MappingError:
        return None


def _get_version(
    ctx: RequestContext,
    budget: Budget,
    version_id: UUID,
    *,
    status: str | None = None,
    with_file: bool = False,
) -> BudgetVersion:
    """La versión del presupuesto, o 404. Sin el archivo salvo que se pida."""
    versions = scoped_qs(ctx, BudgetVersion).filter(budget=budget)
    if status is not None:
        versions = versions.filter(status=status)
    if not with_file:
        versions = versions.defer("source_file")
    try:
        return versions.get(pk=version_id)
    except BudgetVersion.DoesNotExist as exc:
        raise Http404 from exc


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _display(value: Any) -> str:
    """Una celda de la hoja, para la tabla de la página de mapeo (con «…» si se corta)."""
    text = _text_of(value)
    return text if len(text) <= CELL_LIMIT else text[: CELL_LIMIT - 1] + "…"


def _last_column(row: Sequence[Any]) -> int:
    for index in range(len(row) - 1, -1, -1):
        if _display(row[index]).strip():
            return index + 1
    return 0


def _charges_from_post(data: QueryDict) -> list[ChargeSpec]:
    """Los cargos del formulario de la vista previa, renumerados 1..n.

    Una línea sin nombre se quita. Una base `line:k` se traduce al nuevo número
    de la línea k; si esa línea se quitó o no es anterior, queda inválida y
    `validate_charges` lo dice.
    """
    count = min(_int(data.get("charge_count")), MAX_CHARGES + 1)
    specs: list[ChargeSpec] = []
    renumber: dict[int, int] = {}
    for index in range(1, count + 1):
        name = (data.get(f"charge_{index}_name") or "").strip()
        if not name:
            continue
        try:
            pct = parse_number(data.get(f"charge_{index}_pct")) or Decimal(0)
        except ValueError:
            raise ImportRejected(f"Línea {index}: el porcentaje no es un número.") from None
        order = len(specs) + 1
        base = data.get(f"charge_{index}_base") or BASE_DIRECT
        ref = base_line_order(base)
        if ref is not None:
            base = line_base(renumber.get(ref, order))
        renumber[index] = order
        specs.append(ChargeSpec(order=order, name=name, pct=pct, base=base))
    try:
        validate_charges(specs)
    except ChargeError as exc:
        raise ImportRejected(str(exc)) from exc
    return specs


def _charge_form_rows(
    charges: Sequence[ChargeSpec], amounts: Mapping[int, Decimal]
) -> list[dict[str, Any]]:
    """Filas del formulario de cargos: las existentes y una vacía para añadir."""
    names = {c.order: c.name for c in charges}
    specs: list[ChargeSpec | None] = [*charges]
    if len(specs) < MAX_CHARGES:
        specs.append(None)
    rows = []
    for index, spec in enumerate(specs, start=1):
        options = [
            (BASE_DIRECT, "Costo directo"),
            (BASE_SUBTOTAL, "Subtotal (costo directo + líneas anteriores)"),
            *(
                (line_base(k), f"Línea {k} ({names[k]})" if k in names else f"Línea {k}")
                for k in range(1, index)
            ),
        ]
        rows.append(
            {
                "index": index,
                "name": spec.name if spec else "",
                "pct": pct_text(spec.pct) if spec else "",
                "base": spec.base if spec else BASE_DIRECT,
                "options": options,
                "amount": amounts.get(index),
            }
        )
    return rows


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    saved = _saved_mapping(budget)
    error = ""
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            error = "Elige un archivo .xlsx."
        elif upload.size is not None and upload.size > MAX_BYTES:
            error = "El archivo supera 20 MB."
        else:
            # Con mapeo guardado (y sin pedir cambiarlo) se va directo a la vista previa.
            mapping = None if request.POST.get("cambiar_mapeo") == "1" else saved
            try:
                with transaction.atomic():
                    version = create_upload(
                        ctx,
                        budget,
                        file_name=upload.name or "presupuesto.xlsx",
                        data=upload.read(),
                    )
                    if mapping is not None:
                        prepare_version(ctx, version.pk, mapping)
            except ImportRejected as exc:
                error = str(exc)
            else:
                target = "budgets:import_preview" if mapping else "budgets:import_mapping"
                return redirect(target, project.pk, budget.pk, version.pk)
    return _render(
        request,
        ctx,
        "suno/budgets/import.html",
        {**_budget_context(ctx, project, budget), "saved_mapping": saved, "error": error},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_mapping(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    version = _get_version(ctx, budget, version_id, with_file=True)
    if version.status != BudgetVersionStatus.PENDING:
        messages.info(request, "Esta versión ya fue aplicada o descartada.")
        return redirect("budgets:detail", project.pk, budget.pk)
    saved = _saved_mapping(budget)
    wanted = (
        request.POST.get("sheet") or request.GET.get("hoja") or (saved.sheet if saved else None)
    )
    try:
        preview = preview_sheet(bytes(version.source_file), wanted)
    except ExcelRejected as exc:
        messages.error(request, str(exc))
        return redirect("budgets:import", project.pk, budget.pk)
    if saved is not None and saved.sheet == preview.sheet:
        first_row, columns = saved.first_row, dict(saved.columns)
    else:
        suggestion = suggest_mapping(preview.rows)
        first_row, columns = suggestion.first_row, suggestion.columns
    error = ""
    if request.method == "POST":
        first_row = _int(request.POST.get("first_row"))
        columns = {field: request.POST.get(f"col_{field}") or None for field in FIELDS}
        try:
            mapping = ColumnMapping.from_json(
                {"sheet": preview.sheet, "first_row": first_row, "columns": columns}
            )
            prepare_version(ctx, version.pk, mapping)
        except (MappingError, ImportRejected) as exc:
            error = str(exc)
        else:
            return redirect("budgets:import_preview", project.pk, budget.pk, version.pk)
    width = min(
        MAX_COLUMNS, max([MIN_PREVIEW_COLUMNS, *(_last_column(row) for row in preview.rows)])
    )
    return _render(
        request,
        ctx,
        "suno/budgets/import_mapping.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "sheet_names": preview.sheet_names,
            "sheet": preview.sheet,
            "letters": [get_column_letter(i) for i in range(1, width + 1)],
            "preview_rows": [
                (number, [(_display(value), _text_of(value)[:500]) for value in row[:width]])
                for number, row in enumerate(preview.rows, start=1)
            ],
            "fields": [
                {
                    "name": field,
                    "label": FIELD_LABELS[field],
                    "required": field in REQUIRED_FIELDS,
                    "value": columns.get(field),
                }
                for field in FIELDS
            ],
            "first_row": first_row,
            "error": error,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    version = _get_version(ctx, budget, version_id)
    pending = version.status == BudgetVersionStatus.PENDING
    if request.method == "POST":
        if not pending:
            messages.info(request, "Esta versión ya fue aplicada o descartada.")
            return redirect("budgets:detail", project.pk, budget.pk)
        action = request.POST.get("action")
        try:
            if action == "discard":
                with transaction.atomic():
                    discard_version(ctx, version.pk)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_VERSION_DISCARDED,
                        target=f"budget:{budget.pk}",
                        metadata={"file": version.file_name},
                    )
                messages.info(request, "Versión descartada.")
                return redirect("budgets:detail", project.pk, budget.pk)
            label = request.POST.get("label") or ""
            charges = _charges_from_post(request.POST)
            if action == "confirm":
                with transaction.atomic():
                    update_pending(ctx, version.pk, label=label, charges=charges)
                    applied = apply_version(ctx, version.pk)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_VERSION_APPLIED,
                        target=f"budget:{budget.pk}",
                        metadata={
                            "version": applied.number,
                            "file": applied.file_name,
                            "label": applied.label,
                            "total": str(applied.total),
                        },
                    )
                messages.success(request, f"Versión {applied.number} aplicada.")
                return redirect("budgets:detail", project.pk, budget.pk)
            update_pending(ctx, version.pk, label=label, charges=charges)
            messages.success(request, "Etiqueta y cargos guardados.")
        except ImportRejected as exc:
            messages.error(request, str(exc))
        return redirect("budgets:import_preview", project.pk, budget.pk, version.pk)

    summary = version.summary
    if pending and "mapping" not in summary:
        return redirect("budgets:import_mapping", project.pk, budget.pk, version.pk)
    charges = charges_of(ctx, version)
    try:
        result = compute_charges(Decimal(summary.get("direct_cost") or "0"), charges)
    except ChargeError:
        result = None
    amounts = {line.order: line.amount for line in result.lines} if result else {}
    return _render(
        request,
        ctx,
        "suno/budgets/import_preview.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "s": summary,
            "pending": pending,
            "result": result,
            "charge_rows": _charge_form_rows(charges, amounts),
        },
    )


def _safe_name(budget: Budget) -> str:
    """Código (o nombre) del presupuesto apto para nombres de archivo."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", budget.code or budget.name)[:60] or "presupuesto"


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def history(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if request.method == "POST":
        _require_manage(ctx)
        version_id = _parse_uuid(request.POST.get("version"))
        if request.POST.get("action") == "contractual" and version_id is not None:
            try:
                with transaction.atomic():
                    previous, version = mark_contractual(ctx, budget.pk, version_id)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_CONTRACTUAL_CHANGED,
                        target=f"budget:{budget.pk}",
                        metadata={"from": previous, "to": version.number},
                    )
            except ImportRejected as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"La versión {version.number} es ahora la contractual.")
        return redirect("budgets:history", project.pk, budget.pk)

    versions = scoped_qs(ctx, BudgetVersion).defer("source_file").select_related("uploaded_by")
    can_manage = can_manage_budgets(ctx, ctx.org_id)
    return _render(
        request,
        ctx,
        "suno/budgets/history.html",
        {
            **_budget_context(ctx, project, budget),
            "versions": versions.filter(budget=budget, status=BudgetVersionStatus.APPLIED).order_by(
                "-number"
            ),
            "pending": (
                versions.filter(budget=budget, status=BudgetVersionStatus.PENDING).order_by(
                    "-uploaded_at"
                )
                if can_manage
                else []
            ),
        },
    )


@login_required(login_url="platform_login")
def version_detail(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    """El contenido de una versión aplicada, con sus cargos."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    version = _get_version(ctx, budget, version_id, status=str(BudgetVersionStatus.APPLIED))
    charges = list(scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order"))
    names = {c.order: c.name for c in charges}
    return _render(
        request,
        ctx,
        "suno/budgets/version.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "is_contractual": version.pk == budget.contractual_version_id,
            "rows": version_rows(ctx, version),
            "charges": [
                {
                    "charge": c,
                    "note": f"{pct_text(c.pct)} % sobre {describe_base(c.base, names)}",
                }
                for c in charges
            ],
        },
    )


@login_required(login_url="platform_login")
def version_source(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    """Descarga del Excel original de una versión aplicada (auditada)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    version = _get_version(
        ctx, budget, version_id, status=str(BudgetVersionStatus.APPLIED), with_file=True
    )
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.BUDGET_SOURCE_DOWNLOADED,
            target=f"budget:{budget.pk}",
            metadata={"version": version.number},
        )
    response = HttpResponse(bytes(version.source_file), content_type=XLSX_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{_safe_name(budget)}-v{version.number}.xlsx"'
    )
    return response


def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float: los montos van como float (2 decimales)."""
    return None if value is None else float(value)


@login_required(login_url="platform_login")
def export_comparison(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    """La comparación completa (sin filtros) con cargos y totales, en Excel (auditada)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    data = build_comparison(ctx, budget)

    def row_values(r: ComparisonRow) -> list[object]:
        c, v = r.contractual, r.current
        return [
            safe_cell(value)
            for value in (
                r.code, r.description, r.unit,
                "capítulo" if r.kind == KIND_CHAPTER else "ítem",
                ", ".join(r.labels),
                _num(c.quantity) if c else None, _num(c.unit_price) if c else None,
                _num(c.total) if c else None,
                _num(v.quantity) if v else None, _num(v.unit_price) if v else None,
                _num(v.total) if v else None,
                _num(r.diff), _num(r.diff_pct),
            )
        ]  # fmt: skip

    footer = [
        [
            safe_cell(value)
            for value in (
                "",
                f"{f.label} ({f.note})" if f.note else f.label,
                "",
                "",
                "",
                "",
                "",
                _num(f.contractual),
                "",
                "",
                _num(f.current),
                _num(f.diff),
                _num(f.diff_pct),
            )
        ]  # fmt: skip
        for f in data.footer
    ]
    content = to_xlsx(EXPORT_HEADER, [*(row_values(r) for r in data.rows), *footer])
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.BUDGET_EXPORTED,
            target=f"budget:{budget.pk}",
            metadata={"rows": len(data.rows)},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{_safe_name(budget)}-comparacion.xlsx"'
    )
    return response

"""Pestaña "Contratos" del proyecto (costos, pieza 6b): subcontratos y destajos.

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_contracts_project`). Quien
los lee pero no tiene la capacidad de una acción recibe 403. Las reglas de estado y de
autoaprobación viven en `services` y `certificates`: aquí solo se traducen a mensajes.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.totals import BASE_DIRECT, describe_base, pct_text
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import (
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractAmendment,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractStatus,
    CostEntry,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from . import certificates, services
from .access import get_amendment, get_certificate, get_contract, get_contracts_project
from .calc import (
    BASE_GROSS,
    CHARGE_TEMPLATES,
    DEDUCTION_TEMPLATES,
    charge_template,
    withholding_template,
)
from .certificates import (
    CertificateData,
    CertificateLineData,
    build_draft,
    contract_lines,
    default_period,
)
from .export import contract_workbook, contracts_workbook
from .forms import (
    NEW_LINE_ROWS,
    AmendmentForm,
    CertificateForm,
    ContractForm,
    LineFormSet,
    SignedForm,
    amendment_initial,
    contract_data,
    contract_formsets,
    line_initial,
    more_rows,
    outline_index,
    plain,
    read_lines,
    term_initial,
)
from .pdf import certificate_pdf
from .queries import (
    ACTION_LABELS,
    ContractSheet,
    ListFilters,
    certificate_document,
    contract_history,
    contract_rows,
    contract_sheet,
    suggest_number,
    tab_alerts,
)
from .services import ContractRejected

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def term_texts(sheet: ContractSheet) -> tuple[list[str], list[str]]:
    """Cargos y descuentos en palabras: "4. IVA sobre utilidad: 19 % sobre línea 3 (Utilidad)"."""
    names = {charge.order: charge.name for charge in sheet.charges}
    charges = [
        f"{c.order}. {c.name}: {pct_text(c.pct)} % sobre {describe_base(c.base, names)}"
        for c in sheet.charges
    ]
    bases = {BASE_DIRECT: "bruto directo", BASE_GROSS: "bruto"}
    deductions = [
        f"{d.order}. {d.name}: {pct_text(d.pct)} % sobre "
        f"{bases.get(d.base) or describe_base(d.base, names).replace('línea', 'cargo')}"
        for d in sheet.deductions
    ]
    return charges, deductions


TABS = {
    "resumen": "Resumen",
    "lineas": "Líneas",
    "actas": "Actas",
    "otrosies": "Otrosíes",
    "historial": "Historial",
}


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_contract": can_create_costs(ctx, project),
        "can_decide_contract": can_approve_costs(ctx, project),
        "can_close_contract": can_reverse_costs(ctx, project),
        "can_export_contract": can_export_costs(ctx, project),
    }


def _detail(project: Project, contract_id: UUID, tab: str = "") -> HttpResponse:
    url = reverse("contracts:detail", args=[project.pk, contract_id])
    return redirect(f"{url}?{urlencode({'pestana': tab})}" if tab else url)


def _back(request: HttpRequest, project: Project, contract_id: UUID, tab: str = "") -> HttpResponse:
    """A la bandeja "Por aprobar" de Costos si de allá vino; si no, a la ficha."""
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return _detail(project, contract_id, tab)


@login_required(login_url="platform_login")
@require_GET
def contract_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    filters = ListFilters.from_query(request.GET)
    return _render(
        request,
        ctx,
        "suno/contracts/list.html",
        {
            **_context(ctx, project),
            "filters": filters,
            "rows": contract_rows(ctx, project, filters),
            "alerts": tab_alerts(ctx, project, load_catalog(ctx, project)),
            "kinds": ContractKind.choices,
            "statuses": ContractStatus.choices,
        },
    )


def _edit_lines(ctx: RequestContext, contract: Contract) -> list[ContractLine]:
    return list(
        scoped_qs(ctx, ContractLine)
        .filter(contract=contract)
        .select_related("item")
        .prefetch_related("task_links__task")
        .order_by("order")
    )


def _contract_form(
    request: HttpRequest, ctx: RequestContext, project: Project, contract: Contract | None
) -> HttpResponse:
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    errors: list[str] = []
    overlaps: list[str] = []
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = ContractForm(data)
        lines, charges, deductions = contract_formsets(data)
        if action == "guardar":
            valid = [form.is_valid(), lines.is_valid(), charges.is_valid(), deductions.is_valid()]
            if all(valid):
                values, errors = contract_data(form, lines, charges, deductions, catalog, tasks)
                if not errors:
                    try:
                        if contract is None:
                            saved = services.create_contract(
                                ctx, project, values, catalog=catalog, tasks=tasks
                            )
                        else:
                            saved = services.update_contract(
                                ctx, project, contract.pk, values, catalog=catalog, tasks=tasks
                            )
                    except ContractRejected as exc:
                        errors, overlaps = exc.errors, exc.overlaps
                    else:
                        messages.success(request, "Contrato guardado en borrador.")
                        return _detail(project, saved.pk)
    elif contract is None:
        kind = request.GET.get("tipo", "")
        kind = kind if kind in ContractKind.values else str(ContractKind.SUBCONTRACT)
        piecework = kind == ContractKind.PIECEWORK
        template = request.GET.get("plantilla") or ("ninguno" if piecework else "aiu")
        withholdings = request.GET.get("descuentos") or ("ninguno" if piecework else "retenciones")
        charge_rows = charge_template(template)
        today = timezone.localdate()
        form = ContractForm(
            initial={
                "kind": kind,
                "number": suggest_number(ctx, project, kind),
                "starts_on": today,
                "ends_on": today,
                "advance_pct": 0,
                "retention_pct": 0,
            }
        )
        lines, charges, deductions = contract_formsets(
            None,
            lines=[{}] * NEW_LINE_ROWS,
            charges=term_initial(charge_rows),
            deductions=term_initial(
                withholding_template(charge_rows) if withholdings == "retenciones" else []
            ),
        )
    else:
        form = ContractForm(
            initial={
                "kind": contract.kind,
                "number": contract.number,
                "purpose": contract.purpose,
                "contractor": contract.contractor,
                "starts_on": contract.starts_on,
                "ends_on": contract.ends_on,
                "advance_pct": plain(contract.advance_pct),
                "retention_pct": plain(contract.retention_pct),
            }
        )
        lines, charges, deductions = contract_formsets(
            None,
            lines=line_initial(_edit_lines(ctx, contract), catalog),
            charges=term_initial(services.charge_specs(ctx, contract)),
            deductions=term_initial(services.deduction_specs(ctx, contract)),
        )
    return _render(
        request,
        ctx,
        "suno/contracts/form.html",
        {
            **_context(ctx, project),
            "form": form,
            "lines": lines,
            "charges": charges,
            "deductions": deductions,
            "catalog": catalog,
            "contract": contract,
            "errors": errors,
            "overlaps": overlaps,
            "show_errors": action == "guardar",
            "kinds": ContractKind.choices,
            "charge_templates": CHARGE_TEMPLATES,
            "deduction_templates": DEDUCTION_TEMPLATES,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_contract(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    return _contract_form(request, ctx, project, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    if contract.status != ContractStatus.DRAFT:
        messages.error(request, services.APPROVED_IS_FINAL)
        return _detail(project, contract.pk)
    if not can_update_cost(ctx, contract):
        raise PermissionDenied
    return _contract_form(request, ctx, project, contract)


@login_required(login_url="platform_login")
@require_GET
def contract_detail(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    tab = request.GET.get("pestana", "")
    tab = tab if tab in TABS else "resumen"
    sheet = contract_sheet(ctx, project, contract, load_catalog(ctx, project))
    charge_texts, deduction_texts = term_texts(sheet)
    approved = contract.status == ContractStatus.APPROVED
    draft = contract.status == ContractStatus.DRAFT
    mine = ctx.user_id in (contract.created_by_id, contract.updated_by_id)
    return _render(
        request,
        ctx,
        "suno/contracts/detail.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "sheet": sheet,
            "charge_texts": charge_texts,
            "deduction_texts": deduction_texts,
            "tabs": TABS,
            "tab": tab,
            "history": [
                (event, ACTION_LABELS.get(event.action, event.action))
                for event in (contract_history(ctx, contract) if tab == "historial" else [])
            ],
            "can_edit": draft and can_update_cost(ctx, contract),
            "can_delete": draft and can_delete_cost(ctx, contract),
            "can_decide": draft
            and contract.rejected_by_id is None
            and can_approve_costs(ctx, project)
            and not mine,
            "can_void": approved
            and can_reverse_costs(ctx, project)
            and sheet.progress.certificates == 0
            and sheet.open_certificate is None
            and sheet.open_amendment is None,
            "can_new_certificate": approved
            and sheet.open_certificate is None
            and can_create_costs(ctx, project),
            "can_settle": approved
            and sheet.open_certificate is None
            and can_reverse_costs(ctx, project),
            "can_new_amendment": approved
            and sheet.open_amendment is None
            and can_create_costs(ctx, project),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def delete_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.delete_contract(ctx, project, contract_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return _detail(project, contract_id)
    messages.success(request, "Borrador borrado.")
    return redirect("contracts:list", project.pk)


@login_required(login_url="platform_login")
@require_POST
def approve_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.approve_contract(ctx, project, contract_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato aprobado; el anticipo quedó fijado.")
    return _back(request, project, contract_id)


@login_required(login_url="platform_login")
@require_POST
def reject_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.reject_contract(ctx, project, contract_id, request.POST.get("motivo", ""))
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato devuelto a su autor con el motivo.")
    return _back(request, project, contract_id)


@login_required(login_url="platform_login")
@require_POST
def void_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.void_contract(ctx, project, contract_id, request.POST.get("motivo", ""))
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato anulado.")
    return _detail(project, contract_id)


def _amendment_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    amendment: ContractAmendment | None,
) -> HttpResponse:
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    lines = contract_lines(ctx, contract)
    errors: list[str] = []
    overlaps: list[str] = []
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = AmendmentForm(data, lines=lines)
        new_lines = LineFormSet(data, prefix="lineas")
        if action == "guardar" and all([form.is_valid(), new_lines.is_valid()]):
            rows, errors = read_lines(new_lines, catalog, outline_index(tasks))
            if not errors:
                try:
                    if amendment is None:
                        services.create_amendment(
                            ctx, project, contract.pk, form.amendment_data(rows),
                            catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                    else:
                        services.update_amendment(
                            ctx, project, contract.pk, amendment.pk, form.amendment_data(rows),
                            catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                except ContractRejected as exc:
                    errors, overlaps = exc.errors, exc.overlaps
                else:
                    messages.success(request, "Otrosí guardado en borrador.")
                    return _detail(project, contract.pk, "otrosies")
    elif amendment is None:
        form = AmendmentForm(lines=lines, initial={"signed_on": timezone.localdate()})
        new_lines = LineFormSet(prefix="lineas", initial=[{}] * NEW_LINE_ROWS)
    else:
        form = AmendmentForm(lines=lines, initial=amendment_initial(amendment))
        own = list(
            scoped_qs(ctx, ContractLine)
            .filter(amendment=amendment)
            .select_related("item")
            .prefetch_related("task_links__task")
            .order_by("order")
        )
        new_lines = LineFormSet(prefix="lineas", initial=line_initial(own, catalog))
    return _render(
        request,
        ctx,
        "suno/contracts/amendment_form.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "amendment": amendment,
            "form": form,
            "new_lines": new_lines,
            "catalog": catalog,
            "errors": errors,
            "overlaps": overlaps,
            "show_errors": action == "guardar",
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_amendment(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    if contract.status != ContractStatus.APPROVED:
        messages.error(request, "Solo un contrato aprobado admite otrosíes.")
        return _detail(project, contract.pk)
    return _amendment_form(request, ctx, project, contract, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    amendment = get_amendment(ctx, contract, amendment_id)
    try:
        services.require_editable(ctx, amendment, can_update_cost)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return _detail(project, contract.pk, "otrosies")
    return _amendment_form(request, ctx, project, contract, amendment)


@login_required(login_url="platform_login")
@require_POST
def delete_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.delete_amendment(ctx, project, contract_id, amendment_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí borrado.")
    return _detail(project, contract_id, "otrosies")


@login_required(login_url="platform_login")
@require_POST
def approve_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.approve_amendment(ctx, project, contract_id, amendment_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí aprobado y aplicado al contrato.")
    return _back(request, project, contract_id, "otrosies")


@login_required(login_url="platform_login")
@require_POST
def reject_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.reject_amendment(
            ctx, project, contract_id, amendment_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí rechazado; su autor lo verá con el motivo.")
    return _back(request, project, contract_id, "otrosies")


# ---------------------------------------------------------------------------
# Actas
# ---------------------------------------------------------------------------


def _certificate_url(project: Project, contract_id: UUID, certificate_id: UUID) -> str:
    return reverse("contracts:certificate", args=[project.pk, contract_id, certificate_id])


def _certificate_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    certificate: ContractCertificate | None,
    settlement: bool,
) -> HttpResponse:
    """Paso 1 (GET): el periodo. Paso 2 (POST): cantidades; "Recalcular" no guarda."""
    catalog = load_catalog(ctx, project)
    lines = contract_lines(ctx, contract)
    params = request.POST if request.method == "POST" else request.GET
    default = (
        (certificate.period_start, certificate.period_end)
        if certificate is not None
        else default_period(ctx, contract)
    )
    start = _parse_date(params.get("desde")) or default[0]
    end = _parse_date(params.get("hasta")) or default[1]
    initial: dict[str, Any] = {}
    stored: tuple[CertificateLineData, ...] = ()
    if certificate is not None and request.method != "POST":
        rows = list(scoped_qs(ctx, CertificateLine).filter(certificate=certificate))
        for row in rows:
            if row.quantity != row.proposed_quantity:
                initial[f"cantidad_{row.line_id}"] = plain(row.quantity)
                initial[f"justificacion_{row.line_id}"] = row.justification
        stored = tuple(
            CertificateLineData(row.line_id, row.quantity, row.justification)
            for row in rows
            if row.quantity != row.proposed_quantity
        )
    form = CertificateForm(request.POST or None, lines=lines, initial=initial)
    action = request.POST.get("accion", "") if request.method == "POST" else ""
    valid = form.is_valid() if request.method == "POST" else False
    given = form.certificate_lines() if valid else stored
    draft = build_draft(
        ctx,
        project,
        contract,
        CertificateData(start, end, given),
        settlement=settlement,
        catalog=catalog,
        exclude=certificate.pk if certificate is not None else None,
    )
    errors: list[str] = []
    if action == "guardar" and valid:
        data = CertificateData(start, end, given)
        try:
            if certificate is None:
                saved = certificates.create_certificate(
                    ctx, project, contract.pk, data, settlement=settlement, catalog=catalog
                )
            else:
                saved = certificates.update_certificate(
                    ctx, project, contract.pk, certificate.pk, data, catalog=catalog
                )
        except ContractRejected as exc:
            errors = exc.errors
        else:
            messages.success(
                request,
                "Acta guardada en borrador. Descarga el PDF, hazla firmar y súbela para aprobarla.",
            )
            return redirect(_certificate_url(project, contract.pk, saved.pk))
    return _render(
        request,
        ctx,
        "suno/contracts/certificate_form.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "certificate": certificate,
            "settlement": settlement,
            "draft": draft,
            "rows": [
                (line, form[f"cantidad_{line.line.pk}"], form[f"justificacion_{line.line.pk}"])
                for line in draft.lines
            ],
            "form": form,
            "errors": errors or (list(draft.errors) if action else []),
            "start": start,
            "end": end,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_certificate(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    params = request.POST if request.method == "POST" else request.GET
    settlement = params.get("liquidacion") == "1"
    if not can_create_costs(ctx, project) or (settlement and not can_reverse_costs(ctx, project)):
        raise PermissionDenied
    if contract.status != ContractStatus.APPROVED:
        messages.error(request, "Solo un contrato aprobado admite actas.")
        return _detail(project, contract.pk, "actas")
    return _certificate_form(request, ctx, project, contract, None, settlement)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    try:
        services.require_editable(ctx, certificate, can_update_cost)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect(_certificate_url(project, contract.pk, certificate.pk))
    return _certificate_form(
        request, ctx, project, contract, certificate, certificate.is_settlement
    )


def _is_editable(ctx: RequestContext, certificate: ContractCertificate, delete: bool) -> bool:
    if certificate.status not in OPEN_STATES:
        return False
    if (
        certificate.status == CertificateStatus.REJECTED
        and certificate.created_by_id != ctx.user_id
    ):
        return False
    return can_delete_cost(ctx, certificate) if delete else can_update_cost(ctx, certificate)


@login_required(login_url="platform_login")
@require_GET
def certificate_detail(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    document = certificate_document(ctx, project, contract, certificate, load_catalog(ctx, project))
    draft = certificate.status == CertificateStatus.DRAFT
    mine = ctx.user_id in (certificate.created_by_id, certificate.updated_by_id)
    last = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, status=CertificateStatus.APPROVED)
        .order_by("-number")
        .values_list("pk", flat=True)
        .first()
    )
    return _render(
        request,
        ctx,
        "suno/contracts/certificate_detail.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "certificate": certificate,
            "document": document,
            "signed_form": SignedForm(),
            "entries": list(
                scoped_qs(ctx, CostEntry)
                .filter(certificate=certificate)
                .order_by("incurred_on", "amount")
            ),
            "can_edit": _is_editable(ctx, certificate, delete=False),
            "can_delete": _is_editable(ctx, certificate, delete=True),
            "can_sign": draft and can_update_cost(ctx, certificate),
            "can_decide": draft and can_approve_costs(ctx, project) and not mine,
            "can_void": certificate.status == CertificateStatus.APPROVED
            and last == certificate.pk
            and can_reverse_costs(ctx, project),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def upload_signed(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    form = SignedForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Elige el archivo del acta firmada.")
    else:
        upload = form.cleaned_data["signed"]
        try:
            certificates.attach_signed(
                ctx, project, contract_id, certificate_id, upload, upload.name or "acta.pdf"
            )
        except ContractRejected as exc:
            messages.error(request, exc.errors[0])
        else:
            messages.success(request, "Acta firmada adjunta; ya se puede aprobar.")
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_GET
def signed_copy(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    """Como el soporte del libro (6a): URL prefirmada; `local://` se arma a mano."""
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    url = certificates.signed_url(ctx, project, get_certificate(ctx, contract, certificate_id))
    scheme = urlsplit(url).scheme
    if scheme == "local":
        response = HttpResponse(status=302)
        response["Location"] = url
        return response
    if scheme == "https" or (scheme == "http" and settings.DEBUG):
        return redirect(url)
    raise Http404


def _certificate_back(
    request: HttpRequest, project: Project, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_POST
def approve_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        approved = certificates.approve_certificate(ctx, project, contract_id, certificate_id)
    except ContractRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
    else:
        messages.success(
            request,
            "Acta aprobada; sus movimientos están en el libro."
            + (" El contrato quedó liquidado." if approved.is_settlement else ""),
        )
    return _certificate_back(request, project, contract_id, certificate_id)


@login_required(login_url="platform_login")
@require_POST
def reject_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.reject_certificate(
            ctx, project, contract_id, certificate_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Acta rechazada; su autor la verá con el motivo.")
    return _certificate_back(request, project, contract_id, certificate_id)


@login_required(login_url="platform_login")
@require_POST
def void_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.void_certificate(
            ctx, project, contract_id, certificate_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Acta anulada; sus reversos están en el libro con fecha de hoy.")
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_POST
def delete_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.delete_certificate(ctx, project, contract_id, certificate_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect(_certificate_url(project, contract_id, certificate_id))
    messages.success(request, "Borrador del acta borrado.")
    return _detail(project, contract_id, "actas")


# ---------------------------------------------------------------------------
# Documentos (auditados)
# ---------------------------------------------------------------------------


def _require_export(ctx: RequestContext, project: Project) -> None:
    if not can_export_costs(ctx, project):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_GET
def export_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    _require_export(ctx, project)
    filters = ListFilters.from_query(request.GET)
    rows = contract_rows(ctx, project, filters)
    content = contracts_workbook(rows)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"contracts": len(rows), "filters": filters.query},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"contratos-{project.code or project.name}", ".xlsx", fallback="contratos"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def export_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    _require_export(ctx, project)
    content = contract_workbook(contract_sheet(ctx, project, contract, load_catalog(ctx, project)))
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_EXPORTED,
            target=f"contract:{contract.pk}",
            metadata={"contract": str(contract.pk), "number": contract.number},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"contrato-{contract.number}", ".xlsx", fallback="contrato"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def certificate_document_pdf(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    """El acta para imprimir y firmar."""
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    _require_export(ctx, project)
    document = certificate_document(ctx, project, contract, certificate, load_catalog(ctx, project))
    content = certificate_pdf(document, generated_on=timezone.localdate())
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_EXPORTED,
            target=f"certificate:{certificate.pk}",
            metadata={
                "contract": str(contract.pk),
                "number": certificate.number,
                "status": certificate.status,
            },
        )
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition(
        f"acta-{contract.number}-{certificate.number}", ".pdf", fallback="acta"
    )
    return response

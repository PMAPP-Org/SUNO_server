"""Back-office de SUNO sobre Django Admin (Paso 24; doc 08 §8 Fase 1).

Cubre lo que pide el back-office del MVP:
- listar y filtrar visitas, verlas con sus adjuntos (vista previa por URL
  prefirmada) y validarlas o rechazarlas;
- gestionar plantillas (publicar versiones nuevas) y asignaciones;
- revocar dispositivos; ver jobs y dead-letters y relanzarlos;
- exportar visitas a CSV/Excel;
- atender los derechos del titular (consulta y supresión, Ley 1581).

**Reglas** (ver `suno/backoffice/`):
- Todo está aislado por organización (`OrgScopedAdmin`).
- Las entidades **sincronizadas** (`Visit`, `VisitAttachment`, `Assignment`,
  `FormTemplateVersion`) no se editan con el formulario estándar: se
  escribirían sin `change_seq` y los teléfonos nunca verían el cambio. Se
  consultan, y se crean por los servicios que sí lo asignan.
- Lo que cambia datos pasa por `backoffice/services.py` y
  `backoffice/personal_data.py`: política y auditoría en cada acción.
- La auditoría es de solo lectura para todos.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, QuerySet
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from suno.attachments.services import download_url
from suno.backoffice.admin_base import OrgScopedAdmin, ReadOnlyAdmin, run_action
from suno.backoffice.context import admin_context, is_platform_operator
from suno.backoffice.export import ExportTooLarge, to_csv, to_xlsx, visit_table
from suno.backoffice.personal_data import export_personal_data, suppress_personal_data
from suno.backoffice.services import (
    create_assignment,
    retry_job,
    review_visit,
    revoke_device_sessions,
)
from suno.backoffice.spatial_export import (
    ExportContentTooLarge,
    export_visits,
    to_geojson,
    to_geopackage,
    visit_features,
)
from suno.backoffice.visit_pdf import visit_pdf
from suno.backoffice.xlsform_admin import import_view
from suno.basemaps.services import retire as retire_basemap
from suno.core.access import Section
from suno.core.audit import AuditAction, record
from suno.core.context import system_context
from suno.core.models import (
    AppUser,
    Assignment,
    AuditEvent,
    BasemapPackage,
    Blob,
    BudgetItemVersion,
    BudgetVersionStatus,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Job,
    LocationNode,
    Membership,
    Organization,
    PrivacyConsent,
    QuantityPlan,
    ReferenceLayer,
    SyncOpLog,
    Task,
    Visit,
    VisitAttachment,
    VisitReview,
    VisitReviewStatus,
)
from suno.core.policies import (
    PolicyDenied,
    can_export_visits,
    can_publish_form_template,
    can_read_job_output,
)
from suno.forms.definition import TemplateDefinitionError, validate_definition
from suno.forms.services import publish_version
from suno.layers.services import retire as retire_layer
from suno.matrix.diff import plain_quantity
from suno.matrix.quantities import measured_progress
from suno.projects.field_progress import (
    approved,
    format_pct,
    is_regression,
    review_status,
    with_previous_validated_pct,
    with_review,
)

# --- Organización, usuarios y membresías -------------------------------------


@admin.register(Organization)
class OrganizationAdmin(OrgScopedAdmin):
    org_lookup = "pk"
    list_display = ("name", "id")
    search_fields = ("name",)

    def has_add_permission(self, request: Any) -> bool:
        # Dar de alta una organización es del operador de la plataforma.
        return is_platform_operator(request) and super().has_add_permission(request)

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(AppUser)
class AppUserAdmin(OrgScopedAdmin, UserAdmin):
    domain_section = Section.USERS_ACCESS
    list_display = ("username", "display_name", "email", "is_active", "is_staff", "org")
    list_filter = ("is_active", "is_staff")
    search_fields = ("username", "display_name", "email")
    ordering = ("username",)
    fieldsets = (
        *UserAdmin.fieldsets,
        ("SUNO", {"fields": ("org", "display_name")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "org", "password1", "password2")}),
    )
    actions = ("export_personal_data_action", "suppress_personal_data_action")

    def get_readonly_fields(self, request: Any, obj: Any = None) -> Any:
        readonly = list(super().get_readonly_fields(request, obj))
        if not is_platform_operator(request):
            # Nadie del personal de una organización se concede a sí mismo (ni
            # a otro) superusuario ni permisos sueltos: eso es de la plataforma.
            readonly += ["is_superuser", "groups", "user_permissions"]
        return readonly

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        # Los usuarios no se borran (PROTECT, auditoría): se suprimen sus datos.
        return False

    @admin.action(
        description="Derechos del titular: exportar sus datos (JSON)", permissions=["view"]
    )
    def export_personal_data_action(self, request: Any, queryset: QuerySet[AppUser]) -> Any:
        if queryset.count() != 1:
            self.message_user(request, "Selecciona exactamente un usuario.", messages.WARNING)
            return None
        user = queryset.get()
        try:
            data = export_personal_data(admin_context(request), user)
        except PermissionDenied as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return None
        response = HttpResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="datos-personales-{user.pk}.json"'
        return response

    @admin.action(
        description="Derechos del titular: suprimir sus datos (anonimizar)", permissions=["view"]
    )
    def suppress_personal_data_action(self, request: Any, queryset: QuerySet[AppUser]) -> Any:
        if queryset.count() != 1:
            self.message_user(request, "Selecciona exactamente un usuario.", messages.WARNING)
            return None
        user = queryset.get()
        if request.POST.get("confirm") != "yes":
            # Irreversible: página de confirmación explícita, como el borrado.
            return TemplateResponse(
                request,
                "admin/backoffice/confirm_suppression.html",
                {
                    **self.admin_site.each_context(request),
                    "title": "Suprimir datos personales",
                    "target": user,
                    "action": "suppress_personal_data_action",
                    "opts": self.model._meta,
                },
            )
        try:
            suppress_personal_data(admin_context(request), user, ip=request.META.get("REMOTE_ADDR"))
        except PermissionDenied as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return None
        self.message_user(
            request, "Datos personales suprimidos (usuario anonimizado).", messages.SUCCESS
        )
        return None


@admin.register(Membership)
class MembershipAdmin(OrgScopedAdmin):
    domain_section = Section.USERS_ACCESS
    list_display = ("user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username",)


@admin.register(Device)
class DeviceAdmin(ReadOnlyAdmin):
    domain_section = Section.OPERATIONS
    list_display = ("id", "owner", "platform", "model", "app_version", "created_at")
    list_filter = ("platform",)
    search_fields = ("owner__username", "model")
    list_select_related = ("owner",)
    actions = ("revoke_sessions",)

    @admin.action(
        description="Revocar las sesiones del dispositivo (teléfono perdido)", permissions=["view"]
    )
    def revoke_sessions(self, request: Any, queryset: QuerySet[Device]) -> None:
        ip = request.META.get("REMOTE_ADDR")
        run_action(
            self,
            request,
            queryset,
            lambda ctx, device: revoke_device_sessions(ctx, device, ip=ip),
            done="Dispositivos con sesiones revocadas",
        )


# --- Visitas y adjuntos --------------------------------------------------------


class ReviewStatusFilter(admin.SimpleListFilter):
    title = "revisión"
    parameter_name = "revision"

    def lookups(self, request: Any, model_admin: Any) -> Any:
        return (*VisitReviewStatus.choices, ("pending", "Sin revisar"))

    def queryset(self, request: Any, queryset: QuerySet[Visit]) -> QuerySet[Visit]:
        value = self.value()
        if value == "pending":
            return queryset.filter(last_review_status__isnull=True)
        if value in (VisitReviewStatus.APPROVED, VisitReviewStatus.REJECTED):
            return queryset.filter(last_review_status=value)
        return queryset


def _latest_review(visit: Visit) -> str | None:
    """Decisión vigente: la más reciente (`projects.field_progress.review_status`)."""
    return review_status(visit)


class ProgressRegressionFilter(admin.SimpleListFilter):
    """Costos, etapa 1: visitas vinculadas a una tarea y las que retroceden."""

    title = "avance de cronograma"
    parameter_name = "avance"

    def lookups(self, request: Any, model_admin: Any) -> Any:
        return (("tarea", "Vinculadas a una tarea"), ("menor", "Menor que el último validado"))

    def queryset(self, request: Any, queryset: QuerySet[Visit]) -> QuerySet[Visit]:
        if self.value() == "tarea":
            return queryset.filter(task__isnull=False)
        if self.value() == "menor":
            return queryset.filter(progress_pct__lt=F("previous_validated_pct"))
        return queryset


def _preview(request: Any, attachment: VisitAttachment) -> Any:
    """Vista previa por URL **prefirmada y con caducidad** (nunca el bucket)."""
    try:
        ticket = download_url(admin_context(request), sha256=attachment.blob_id)
    except PolicyDenied:
        return "Pendiente de subir o sin permiso"
    if ticket.mime.startswith("image/"):
        return format_html(
            '<a href="{0}" target="_blank" rel="noopener"><img src="{0}" height="120" alt=""></a>',
            ticket.url,
        )
    return format_html('<a href="{}" target="_blank" rel="noopener">Descargar</a>', ticket.url)


class VisitAttachmentInline(admin.TabularInline):
    model = VisitAttachment
    fields = ("role", "captured_at", "order", "preview")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def get_formset(self, request: Any, obj: Any = None, **kwargs: Any) -> Any:
        self._request = request
        return super().get_formset(request, obj, **kwargs)

    @admin.display(description="vista previa")
    def preview(self, attachment: VisitAttachment) -> Any:
        return _preview(self._request, attachment)


class VisitReviewInline(admin.TabularInline):
    model = VisitReview
    fields = ("status", "comment", "reviewer", "created_at")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(Visit)
class VisitAdmin(ReadOnlyAdmin):
    domain_section = Section.FIELD_RECORDS
    list_display = ("captured_at", "template", "created_by", "progress", "review", "version")
    list_filter = (
        ReviewStatusFilter,
        ProgressRegressionFilter,
        "captured_at",
        "template_version__template",
    )
    search_fields = ("created_by__username", "template_version__name")
    date_hierarchy = "captured_at"
    list_select_related = ("template_version", "created_by", "task")
    fields = (
        "id",
        "template_version",
        "created_by",
        "device",
        "captured_at",
        "lat",
        "lon",
        "accuracy_m",
        "altitude_m",
        "answers_pretty",
        "task",
        "progress",
        "quantities_pretty",
        "version",
        "change_seq",
        "deleted_at",
    )
    readonly_fields = fields
    inlines = (VisitAttachmentInline, VisitReviewInline)
    actions = (
        "approve",
        "reject",
        "export_csv",
        "export_xlsx",
        "export_geojson",
        "export_gpkg",
        "export_pdf",
    )

    def get_queryset(self, request: Any) -> QuerySet[Visit]:
        base = super().get_queryset(request)
        linked = base
        if request.GET.get(ProgressRegressionFilter.parameter_name) in ("tarea", "menor"):
            # Solo las vinculadas: la subconsulta del "último validado" no se
            # calcula para las demás visitas.
            linked = base.filter(task__isnull=False)
        return with_previous_validated_pct(with_review(linked), base)

    @admin.display(description="avance de cronograma")
    def progress(self, visit: Visit) -> str:
        if visit.task_id is None or visit.progress_pct is None:
            return "—"
        text = f"{visit.task} · {format_pct(visit.progress_pct)}"
        previous = getattr(visit, "previous_validated_pct", None)
        if previous is not None and is_regression(visit.progress_pct, previous):
            text += f" ⚠ menor que el último validado ({format_pct(previous)})"
        return text

    @admin.display(description="cantidades")
    def quantities_pretty(self, visit: Visit) -> Any:
        """Las cantidades de la visita, con avisos para la supervisión: ítem fuera del
        plan de la tarea (sin nota), ajuste negativo sin nota, ubicación fuera de la
        ubicación actual de la tarea, o lo validado más esta visita supera lo planeado.

        El push acepta todo eso a propósito (`quantities_rejection`): la oficina pudo
        cambiar el plan o la ubicación entre la captura sin conexión y la sincronización,
        y lo registrado nunca se pierde; aquí se resalta."""
        if not visit.quantities or visit.task_id is None:
            return "—"
        item_ids = [row["item_id"] for row in visit.quantities]
        # El Admin no tiene `RequestContext`: se acota a la organización de la visita.
        versions = (
            QuerySet(model=BudgetItemVersion)
            .filter(
                org_id=visit.org_id,
                item_id__in=item_ids,
                version__status=BudgetVersionStatus.APPLIED,
            )
            .order_by("item_id", "-version__number")
            .distinct("item_id")
            .values_list("item_id", "item__code", "description", "unit")
        )
        facts = {str(i): (code, desc, unit) for i, code, desc, unit in versions}
        planned = {
            str(item_id): quantity
            for item_id, quantity in QuerySet(model=QuantityPlan)
            .filter(org_id=visit.org_id, task_id=visit.task_id)
            .values_list("item_id", "planned_quantity")
        }
        validated: dict[str, Decimal] = {}
        others = (
            approved(
                QuerySet(model=Visit).filter(
                    org_id=visit.org_id, task_id=visit.task_id, deleted_at__isnull=True
                )
            )
            .exclude(pk=visit.pk)
            .exclude(quantities=[])
            .values_list("quantities", flat=True)
        )
        for rows in others:
            for row in rows:
                key = row["item_id"]
                validated[key] = validated.get(key, Decimal(0)) + Decimal(row["quantity"])
        node_paths = {
            str(pk): path
            for pk, path in QuerySet(model=LocationNode)
            .filter(
                org_id=visit.org_id,
                pk__in=[row["location_id"] for row in visit.quantities if row["location_id"]],
            )
            .values_list("pk", "path")
        }
        task_path = (
            QuerySet(model=Task)
            .filter(org_id=visit.org_id, pk=visit.task_id)
            .values_list("location__path", flat=True)
            .first()
        )
        lines: list[str] = []
        for row in visit.quantities:
            code, description, unit = facts.get(row["item_id"], ("?", "", ""))
            quantity = Decimal(row["quantity"])
            text = f"{code} {description}: {plain_quantity(quantity)} {unit}".rstrip()
            target = planned.get(row["item_id"])
            if target is None:
                text += " — fuera del plan" if row["note"] else " — no planeado y sin nota"
            else:
                total = validated.get(row["item_id"], Decimal(0)) + quantity
                if total > target:
                    text += (
                        f" — supera el plan ({plain_quantity(total)} de {plain_quantity(target)})"
                    )
            if quantity < 0 and not row["note"]:
                text += " — ajuste negativo sin nota"
            if row["location_id"]:
                path = node_paths.get(row["location_id"])
                if task_path is None or path is None or not path.startswith(task_path):
                    text += " — ubicación fuera de la ubicación actual de la tarea"
            if row["note"]:
                text += f" — nota: {row['note']}"
            lines.append(text)
        # Mide por cantidades con la misma regla que `measured_by` (sin capítulos ni filas en
        # 0). `system_context` solo aísla por organización: es una lectura, sin políticas.
        if visit.progress_pct is not None and visit.task_id in measured_progress(
            system_context(visit.org_id), task_ids=[visit.task_id]
        ):
            lines.append("% manual ignorado: la tarea se mide por cantidades.")
        # Cada línea se escapa: códigos, descripciones y notas los escribe un usuario.
        return format_html_join("", "<p>{}</p>", ((line,) for line in lines))

    @admin.display(description="plantilla")
    def template(self, visit: Visit) -> str:
        version = visit.template_version
        return f"{version.name or version.template_id} (v{version.version_number})"

    @admin.display(description="revisión")
    def review(self, visit: Visit) -> str:
        status = _latest_review(visit)
        return VisitReviewStatus(status).label if status else "Sin revisar"

    @admin.display(description="respuestas")
    def answers_pretty(self, visit: Visit) -> Any:
        return format_html("<pre>{}</pre>", json.dumps(visit.answers, ensure_ascii=False, indent=2))

    @admin.action(description="Validar las visitas seleccionadas", permissions=["view"])
    def approve(self, request: Any, queryset: QuerySet[Visit]) -> None:
        run_action(
            self,
            request,
            queryset,
            lambda ctx, visit: review_visit(ctx, visit, status=str(VisitReviewStatus.APPROVED)),
            done="Visitas validadas",
        )

    @admin.action(description="Rechazar las visitas seleccionadas", permissions=["view"])
    def reject(self, request: Any, queryset: QuerySet[Visit]) -> None:
        run_action(
            self,
            request,
            queryset,
            lambda ctx, visit: review_visit(ctx, visit, status=str(VisitReviewStatus.REJECTED)),
            done="Visitas rechazadas (para añadir un comentario, usa «Revisiones de visita»)",
        )

    def _export(self, request: Any, queryset: QuerySet[Visit], fmt: str) -> Any:
        ctx = admin_context(request)
        if not can_export_visits(ctx, ctx.org_id):
            self.message_user(request, "Solo supervisión puede exportar visitas.", messages.ERROR)
            return None
        try:
            header, rows = visit_table(ctx, queryset)
        except ExportTooLarge as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return None
        record(
            ctx,
            action=AuditAction.VISITS_EXPORTED,
            target=f"org:{ctx.org_id}",
            metadata={"rows": len(rows), "format": fmt},
            ip=request.META.get("REMOTE_ADDR"),
        )
        stamp = timezone.now().strftime("%Y%m%d-%H%M")
        if fmt == "xlsx":
            response = HttpResponse(
                to_xlsx(header, rows),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            response = HttpResponse(to_csv(header, rows), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="visitas-{stamp}.{fmt}"'
        return response

    @admin.action(description="Exportar a CSV", permissions=["view"])
    def export_csv(self, request: Any, queryset: QuerySet[Visit]) -> Any:
        return self._export(request, queryset, "csv")

    @admin.action(description="Exportar a Excel (.xlsx)", permissions=["view"])
    def export_xlsx(self, request: Any, queryset: QuerySet[Visit]) -> Any:
        return self._export(request, queryset, "xlsx")

    def _document_export(self, request: Any, queryset: QuerySet[Visit], fmt: str) -> Any:
        ctx = admin_context(request)
        try:
            visits = export_visits(ctx, queryset)
        except (ExportTooLarge, PermissionDenied) as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return None
        if fmt == "pdf" and len(visits) != 1:
            self.message_user(
                request, "Selecciona exactamente una visita para su informe PDF.", messages.ERROR
            )
            return None
        if fmt == "pdf":
            data, mime = visit_pdf(visits[0]), "application/pdf"
        else:
            try:
                collection = visit_features(visits)
            except ExportContentTooLarge as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return None
            data, mime = (
                (to_geojson(collection), "application/geo+json")
                if fmt == "geojson"
                else (to_geopackage(collection), "application/geopackage+sqlite3")
            )
        record(
            ctx,
            action=AuditAction.VISITS_EXPORTED,
            target=f"org:{ctx.org_id}",
            metadata={"rows": len(visits), "format": fmt},
            ip=request.META.get("REMOTE_ADDR"),
        )
        response = HttpResponse(data, content_type=mime)
        response["Content-Disposition"] = (
            f'attachment; filename="visitas-{timezone.now():%Y%m%d-%H%M}.{fmt}"'
        )
        response["Cache-Control"] = "private, no-store"
        return response

    @admin.action(description="Exportar a GeoJSON (EPSG:4326)", permissions=["view"])
    def export_geojson(self, request: Any, queryset: QuerySet[Visit]) -> Any:
        return self._document_export(request, queryset, "geojson")

    @admin.action(description="Exportar a GeoPackage (.gpkg)", permissions=["view"])
    def export_gpkg(self, request: Any, queryset: QuerySet[Visit]) -> Any:
        return self._document_export(request, queryset, "gpkg")

    @admin.action(description="Informe PDF de una visita", permissions=["view"])
    def export_pdf(self, request: Any, queryset: QuerySet[Visit]) -> Any:
        return self._document_export(request, queryset, "pdf")


@admin.register(VisitAttachment)
class VisitAttachmentAdmin(ReadOnlyAdmin):
    domain_section = Section.FIELD_RECORDS
    list_display = ("id", "visit", "role", "captured_at")
    list_filter = ("role",)
    fields = ("id", "visit", "role", "caption", "captured_at", "order", "blob", "preview")
    readonly_fields = fields

    def get_form(self, request: Any, obj: Any = None, change: bool = False, **kwargs: Any) -> Any:
        self._request = request
        return super().get_form(request, obj, change, **kwargs)

    @admin.display(description="vista previa")
    def preview(self, attachment: VisitAttachment) -> Any:
        return _preview(self._request, attachment)


class VisitReviewForm(forms.ModelForm):
    class Meta:
        model = VisitReview
        fields = ("visit", "status", "comment")


@admin.register(VisitReview)
class VisitReviewAdmin(OrgScopedAdmin):
    domain_section = Section.FIELD_RECORDS
    """Historial de decisiones. Se añaden (con comentario); nunca se editan."""

    form = VisitReviewForm
    list_display = ("created_at", "visit", "status", "reviewer", "comment")
    list_filter = ("status",)
    list_select_related = ("reviewer",)

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def save_model(self, request: Any, obj: VisitReview, form: Any, change: bool) -> None:
        created = review_visit(
            admin_context(request),
            form.cleaned_data["visit"],
            status=form.cleaned_data["status"],
            comment=form.cleaned_data.get("comment"),
        )
        obj.pk, obj.org_id, obj.reviewer_id, obj.created_at = (
            created.pk,
            created.org_id,
            created.reviewer_id,
            created.created_at,
        )


# --- Plantillas y asignaciones ---------------------------------------------------


@admin.register(FormTemplate)
class FormTemplateAdmin(OrgScopedAdmin):
    domain_section = Section.FORMS
    change_form_template = "admin/core/formtemplate/change_form.html"
    list_display = ("name", "created_at")
    search_fields = ("name",)

    def get_urls(self) -> Any:
        return [
            path(
                "<uuid:object_id>/import-xlsform/",
                self.admin_site.admin_view(self.import_xlsform),
                name="core_formtemplate_import_xlsform",
            ),
            *super().get_urls(),
        ]

    def import_xlsform(self, request: Any, object_id: str) -> Any:
        return import_view(self, request, object_id)

    def render_change_form(
        self, request: Any, context: Any, add: bool = False, change: bool = False, **kwargs: Any
    ) -> Any:
        # El mismo criterio que import_view: supervisión puede editar la
        # plantilla, pero no publicar versiones, así que no ve el enlace.
        obj = kwargs.get("obj")
        context["can_import_xlsform"] = (
            obj is not None
            and self.has_change_permission(request, obj)
            and can_publish_form_template(admin_context(request), obj)
        )
        return super().render_change_form(request, context, add, change, **kwargs)

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


class PublishVersionForm(forms.ModelForm):
    """Publicar una versión nueva: la definición se valida con el meta-schema."""

    class Meta:
        model = FormTemplateVersion
        fields = ("template", "name", "definition")

    def clean_definition(self) -> Any:
        definition = self.cleaned_data["definition"]
        try:
            validate_definition(definition)
        except TemplateDefinitionError as exc:
            raise ValidationError(exc.message) from exc
        return definition


@admin.register(FormTemplateVersion)
class FormTemplateVersionAdmin(OrgScopedAdmin):
    domain_section = Section.FORMS
    """Versiones inmutables: se publica una nueva, nunca se edita una publicada."""

    form = PublishVersionForm
    list_display = ("template", "version_number", "name", "published_at")
    list_filter = ("template",)
    list_select_related = ("template",)

    def get_readonly_fields(self, request: Any, obj: Any = None) -> Any:
        if obj is not None:
            return (
                "template",
                "name",
                "version_number",
                "definition",
                "published_at",
                "change_seq",
            )
        return ()

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        # Ver sí (detalle de solo lectura); guardar cambios no.
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def save_model(self, request: Any, obj: FormTemplateVersion, form: Any, change: bool) -> None:
        ctx = admin_context(request)
        try:
            published = publish_version(
                ctx,
                template_id=form.cleaned_data["template"].pk,
                definition=form.cleaned_data["definition"],
                name=form.cleaned_data.get("name"),
            )
        except PolicyDenied as exc:
            raise PermissionDenied(
                "Solo la administración de la organización publica plantillas."
            ) from exc
        record(
            ctx,
            action=AuditAction.TEMPLATE_PUBLISHED,
            target=f"form_template_version:{published.pk}",
            metadata={"version_number": published.version_number},
        )
        obj.pk, obj.org_id, obj.version_number, obj.published_at = (
            published.pk,
            published.org_id,
            published.version_number,
            published.published_at,
        )


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ("template_version", "assigned_to", "title", "scheduled_for", "lat", "lon")


@admin.register(Assignment)
class AssignmentAdmin(OrgScopedAdmin):
    domain_section = Section.FIELD_RECORDS
    """Se crean por `create_assignment` (con `change_seq`); no se editan."""

    form = AssignmentForm
    list_display = ("title", "assigned_to", "template_version", "scheduled_for")
    list_select_related = ("assigned_to", "template_version")
    search_fields = ("title", "assigned_to__username")

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def formfield_for_foreignkey(self, db_field: Any, request: Any, **kwargs: Any) -> Any:
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if field is not None and db_field.name == "template_version":
            field.queryset = field.queryset.filter(
                published_at__isnull=False, deleted_at__isnull=True
            )
        return field

    def save_model(self, request: Any, obj: Assignment, form: Any, change: bool) -> None:
        data = form.cleaned_data
        try:
            created = create_assignment(
                admin_context(request),
                template_version=data["template_version"],
                assigned_to=data["assigned_to"],
                title=data.get("title"),
                scheduled_for=data.get("scheduled_for"),
                lat=data.get("lat"),
                lon=data.get("lon"),
            )
        except ValueError as exc:
            raise PermissionDenied(str(exc)) from exc
        obj.pk, obj.org_id, obj.change_seq = created.pk, created.org_id, created.change_seq


# --- Operación: jobs, sync y auditoría --------------------------------------------


@admin.register(Job)
class JobAdmin(ReadOnlyAdmin):
    domain_section = Section.OPERATIONS
    list_display = ("type", "status", "attempts", "max_attempts", "created_at", "finished_at")
    list_filter = ("status", "type")
    actions = ("retry",)

    def get_fields(self, request: Any, obj: Any = None) -> Any:
        fields = [
            "id",
            "type",
            "status",
            "attempts",
            "max_attempts",
            "run_after",
            "created_at",
            "finished_at",
        ]
        # `payload`, `output` y `error` solo para administración (ADR-006, RD-12).
        if obj is not None and can_read_job_output(admin_context(request), obj):
            fields += ["payload", "output", "error"]
        return fields

    def get_readonly_fields(self, request: Any, obj: Any = None) -> Any:
        return self.get_fields(request, obj)

    @admin.action(description="Relanzar (fallidos y dead-letter)", permissions=["view"])
    def retry(self, request: Any, queryset: QuerySet[Job]) -> None:
        run_action(self, request, queryset, retry_job, done="Jobs relanzados")


@admin.register(SyncOpLog)
class SyncOpLogAdmin(ReadOnlyAdmin):
    domain_section = Section.OPERATIONS
    list_display = ("received_at", "entity", "entity_id", "device")
    list_filter = ("entity",)
    date_hierarchy = "received_at"


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    domain_section = Section.OPERATIONS
    """Traza de auditoría: consultable, nunca editable (ISO 27001 A.8.15).

    Un registro que el propio back-office puede corregir o borrar no prueba
    nada: sin alta, cambio ni borrado para nadie, superusuarios incluidos. La
    única puerta de escritura es `core/audit.py::record`.
    """

    list_display = ("created_at", "action", "actor", "target")
    list_filter = ("action",)
    search_fields = ("target", "actor__username")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(PrivacyConsent)
class PrivacyConsentAdmin(ReadOnlyAdmin):
    domain_section = Section.USERS_ACCESS
    """Prueba de la autorización de datos personales (Ley 1581, art. 9).

    Solo lectura, como la auditoría: una prueba que el back-office pudiera
    editar no probaría nada. Se escribe únicamente desde `POST /v1/consents`.
    """

    list_display = ("recorded_at", "user", "notice_version", "purposes", "device")
    list_filter = ("notice_version",)
    search_fields = ("user__username",)
    date_hierarchy = "recorded_at"
    ordering = ("-recorded_at",)


@admin.register(BasemapPackage)
class BasemapPackageAdmin(ReadOnlyAdmin):
    domain_section = Section.GIS
    """Mapas base offline (Fase B). Se importan desde el servidor
    (`manage.py import_basemap`, archivos de cientos de MB que no deben pasar
    por la web); aquí se ven y se retiran."""

    list_display = (
        "name",
        "tile_format",
        "min_zoom",
        "max_zoom",
        "size",
        "created_at",
        "deleted_at",
    )
    list_filter = ("tile_format",)
    search_fields = ("name",)
    ordering = ("-created_at",)
    actions = ("retire_packages",)

    @admin.action(description="Retirar (deja de ofrecerse a los teléfonos)", permissions=["view"])
    def retire_packages(self, request: Any, queryset: QuerySet[BasemapPackage]) -> None:
        run_action(
            self,
            request,
            queryset.filter(deleted_at__isnull=True),
            lambda ctx, package: retire_basemap(ctx, package.pk),
            done="Mapas retirados",
        )


@admin.register(ReferenceLayer)
class ReferenceLayerAdmin(ReadOnlyAdmin):
    domain_section = Section.GIS
    """Capas de referencia GeoJSON (Fase C). Se importan con `manage.py
    import_layer`; aquí se ven y se retiran."""

    list_display = ("name", "feature_count", "geometry_types", "size", "created_at", "deleted_at")
    search_fields = ("name",)
    ordering = ("-created_at",)
    actions = ("retire_layers",)

    @admin.action(description="Retirar (deja de ofrecerse a los teléfonos)", permissions=["view"])
    def retire_layers(self, request: Any, queryset: QuerySet[ReferenceLayer]) -> None:
        run_action(
            self,
            request,
            queryset.filter(deleted_at__isnull=True),
            lambda ctx, layer: retire_layer(ctx, layer.pk),
            done="Capas retiradas",
        )


@admin.register(Blob)
class BlobAdmin(admin.ModelAdmin):
    """`Blob` no tiene organización (dedup entre tenants, RD-08): solo el
    operador de la plataforma lo ve. El acceso a contenido va por los adjuntos."""

    list_display = ("sha256", "mime", "size", "created_at")

    def has_module_permission(self, request: Any) -> bool:
        return is_platform_operator(request)

    def has_view_permission(self, request: Any, obj: Any = None) -> bool:
        return is_platform_operator(request)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False

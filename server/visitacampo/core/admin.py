"""Back-office de VisitaCampo sobre Django Admin (Paso 24; doc 08 §8 Fase 1).

Cubre lo que pide el back-office del MVP:
- listar y filtrar visitas, verlas con sus adjuntos (vista previa por URL
  prefirmada) y validarlas o rechazarlas;
- gestionar plantillas (publicar versiones nuevas) y asignaciones;
- revocar dispositivos; ver jobs y dead-letters y relanzarlos;
- exportar visitas a CSV/Excel;
- atender los derechos del titular (consulta y supresión, Ley 1581).

**Reglas** (ver `visitacampo/backoffice/`):
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
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, Max, Q, QuerySet
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html

from visitacampo.attachments.services import download_url
from visitacampo.backoffice.admin_base import OrgScopedAdmin, ReadOnlyAdmin, run_action
from visitacampo.backoffice.context import admin_context, is_platform_operator
from visitacampo.backoffice.export import ExportTooLarge, to_csv, to_xlsx, visit_table
from visitacampo.backoffice.personal_data import export_personal_data, suppress_personal_data
from visitacampo.backoffice.services import (
    create_assignment,
    retry_job,
    review_visit,
    revoke_device_sessions,
)
from visitacampo.core.audit import AuditAction, record
from visitacampo.core.models import (
    AppUser,
    Assignment,
    AuditEvent,
    Blob,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Job,
    Membership,
    Organization,
    PrivacyConsent,
    SyncOpLog,
    Visit,
    VisitAttachment,
    VisitReview,
    VisitReviewStatus,
)
from visitacampo.core.policies import PolicyDenied, can_export_visits, can_read_job_output
from visitacampo.forms.definition import TemplateDefinitionError, validate_definition
from visitacampo.forms.services import publish_version

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
    list_display = ("username", "display_name", "email", "is_active", "is_staff", "org")
    list_filter = ("is_active", "is_staff")
    search_fields = ("username", "display_name", "email")
    ordering = ("username",)
    fieldsets = (
        *UserAdmin.fieldsets,
        ("VisitaCampo", {"fields": ("org", "display_name")}),
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
    list_display = ("user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username",)


@admin.register(Device)
class DeviceAdmin(ReadOnlyAdmin):
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
            return queryset.filter(last_review_at__isnull=True)
        if value == VisitReviewStatus.APPROVED:
            return queryset.filter(
                last_review_at__isnull=False, last_approved_at=F("last_review_at")
            )
        if value == VisitReviewStatus.REJECTED:
            return queryset.filter(last_review_at__isnull=False).exclude(
                last_approved_at=F("last_review_at")
            )
        return queryset


def _latest_review(visit: Visit) -> str | None:
    """Decisión vigente: la más reciente. Sin subconsultas: dos `Max` anotados."""
    last = getattr(visit, "last_review_at", None)
    if last is None:
        return None
    approved = getattr(visit, "last_approved_at", None)
    return str(VisitReviewStatus.APPROVED) if approved == last else str(VisitReviewStatus.REJECTED)


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
    list_display = ("captured_at", "template", "created_by", "review", "version")
    list_filter = (ReviewStatusFilter, "captured_at", "template_version__template")
    search_fields = ("created_by__username", "template_version__name")
    date_hierarchy = "captured_at"
    list_select_related = ("template_version", "created_by")
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
        "version",
        "change_seq",
        "deleted_at",
    )
    readonly_fields = fields
    inlines = (VisitAttachmentInline, VisitReviewInline)
    actions = ("approve", "reject", "export_csv", "export_xlsx")

    def get_queryset(self, request: Any) -> QuerySet[Visit]:
        return (
            super()
            .get_queryset(request)
            .annotate(
                last_review_at=Max("reviews__created_at"),
                last_approved_at=Max(
                    "reviews__created_at",
                    filter=Q(reviews__status=str(VisitReviewStatus.APPROVED)),
                ),
            )
        )

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


@admin.register(VisitAttachment)
class VisitAttachmentAdmin(ReadOnlyAdmin):
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
    list_display = ("name", "created_at")
    search_fields = ("name",)

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
    list_display = ("received_at", "entity", "entity_id", "device")
    list_filter = ("entity",)
    date_hierarchy = "received_at"


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
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
    """Prueba de la autorización de datos personales (Ley 1581, art. 9).

    Solo lectura, como la auditoría: una prueba que el back-office pudiera
    editar no probaría nada. Se escribe únicamente desde `POST /v1/consents`.
    """

    list_display = ("recorded_at", "user", "notice_version", "purposes", "device")
    list_filter = ("notice_version",)
    search_fields = ("user__username",)
    date_hierarchy = "recorded_at"
    ordering = ("-recorded_at",)


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

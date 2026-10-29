"""XLSForm upload through the scoped Django admin."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from visitacampo.backoffice.context import admin_context
from visitacampo.core.audit import AuditAction, record
from visitacampo.core.policies import PolicyDenied, check_publish_form_template
from visitacampo.forms.services import publish_version
from visitacampo.forms.xlsform import MAX_FILE_BYTES, InvalidXlsForm, import_xlsform


class XlsFormUpload(forms.Form):
    workbook = forms.FileField(label="XLSForm (.xlsx)")
    name = forms.CharField(label="Nombre de la versión", max_length=200, required=False)

    def clean_workbook(self) -> Any:
        upload = self.cleaned_data["workbook"]
        if upload.size > MAX_FILE_BYTES:
            raise forms.ValidationError("El XLSForm supera 5 MiB.")
        try:
            return import_xlsform(upload.read(MAX_FILE_BYTES + 1))
        except InvalidXlsForm as exc:
            raise forms.ValidationError(str(exc)) from exc


def import_view(model_admin: Any, request: Any, object_id: str) -> Any:
    obj = model_admin.get_object(request, str(object_id))
    if obj is None:
        raise Http404
    if not model_admin.has_change_permission(request, obj):
        raise PermissionDenied
    ctx = admin_context(request)
    try:
        check_publish_form_template(ctx, obj)
    except PolicyDenied as exc:
        raise PermissionDenied("Solo la administración puede publicar formularios.") from exc
    form = XlsFormUpload(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            published = publish_version(
                ctx,
                template_id=obj.pk,
                definition=form.cleaned_data["workbook"].model_dump(mode="json", exclude_none=True),
                name=form.cleaned_data["name"] or obj.name,
            )
            record(
                ctx,
                action=AuditAction.TEMPLATE_PUBLISHED,
                target=f"form_template_version:{published.pk}",
                metadata={"version_number": published.version_number, "source": "xlsform"},
                ip=request.META.get("REMOTE_ADDR"),
            )
        model_admin.message_user(
            request, f"Publicada versión {published.version_number}.", messages.SUCCESS
        )
        return HttpResponseRedirect(
            reverse("admin:core_formtemplateversion_change", args=[published.pk])
        )
    return TemplateResponse(
        request,
        "admin/core/formtemplate/import_xlsform.html",
        {
            **model_admin.admin_site.each_context(request),
            "opts": model_admin.model._meta,
            "title": f"Importar XLSForm: {obj.name}",
            "form": form,
            "original": obj,
        },
    )

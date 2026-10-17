"""Bases del Admin con aislamiento por organización (Paso 24, H-16 en el back-office).

Django Admin, por defecto, enseña **todas** las filas de una tabla a cualquiera
con permiso sobre el modelo. En un SaaS multi-organización eso sería el H-16 de
la referencia (fuga entre tenants) dentro del propio back-office. Estas bases:

- **filtran** cada lista y cada detalle a la organización de quien mira
  (`get_queryset`); una fila de otra organización ni se lista ni se abre (404);
- exigen, además del permiso de Django, ser **miembro** de la organización;
- limitan las **claves foráneas** de los formularios a la organización (no se
  puede crear una asignación con la plantilla de otra);
- **fuerzan** la organización al crear: no se elige, es la de quien crea.

El superusuario (operador de la plataforma) ve todas las organizaciones; sus
acciones siguen pasando por las políticas (ver `backoffice/__init__.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Model, QuerySet

from visitacampo.backoffice.context import admin_context, is_platform_operator
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Organization

__all__ = ["OrgScopedAdmin", "ReadOnlyAdmin", "run_action"]


class OrgScopedAdmin(admin.ModelAdmin):
    """Admin de una tabla de negocio (`org_id`), aislado por organización."""

    #: Campo por el que se aísla. `Organization` usa su propia `pk`.
    org_lookup = "org_id"

    def _org_of(self, obj: Model) -> Any:
        return getattr(obj, self.org_lookup)

    def _member_of_own_org(self, request: Any) -> bool:
        return is_platform_operator(request) or admin_context(request).is_member

    def _can_touch(self, request: Any, obj: Model | None) -> bool:
        if is_platform_operator(request):
            return True
        return self._member_of_own_org(request) and (
            obj is None or self._org_of(obj) == admin_context(request).org_id
        )

    def get_queryset(self, request: Any) -> QuerySet[Any]:
        queryset = super().get_queryset(request)
        if is_platform_operator(request):
            return queryset
        return queryset.filter(**{self.org_lookup: admin_context(request).org_id})

    def get_list_filter(self, request: Any) -> Any:
        filters = list(super().get_list_filter(request))
        # El filtro por organización solo tiene sentido para quien ve varias.
        if is_platform_operator(request) and self.org_lookup == "org_id":
            filters.insert(0, "org")
        return filters

    def get_readonly_fields(self, request: Any, obj: Model | None = None) -> Any:
        readonly = list(super().get_readonly_fields(request, obj))
        # La organización no se elige: es la de quien crea (`save_model`).
        if (
            not is_platform_operator(request)
            and self.org_lookup == "org_id"
            and "org" not in readonly
        ):
            readonly.append("org")
        return readonly

    def has_module_permission(self, request: Any) -> bool:
        return super().has_module_permission(request) and self._member_of_own_org(request)

    def has_view_permission(self, request: Any, obj: Model | None = None) -> bool:
        return super().has_view_permission(request, obj) and self._can_touch(request, obj)

    def has_change_permission(self, request: Any, obj: Model | None = None) -> bool:
        return super().has_change_permission(request, obj) and self._can_touch(request, obj)

    def has_delete_permission(self, request: Any, obj: Model | None = None) -> bool:
        return super().has_delete_permission(request, obj) and self._can_touch(request, obj)

    def has_add_permission(self, request: Any) -> bool:
        return super().has_add_permission(request) and self._member_of_own_org(request)

    def formfield_for_foreignkey(self, db_field: Any, request: Any, **kwargs: Any) -> Any:
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if field is None or is_platform_operator(request):
            return field
        org_id = admin_context(request).org_id
        related = field.queryset.model
        if related is Organization:
            field.queryset = field.queryset.filter(pk=org_id)
        elif any(f.name == "org" for f in related._meta.get_fields()):
            field.queryset = field.queryset.filter(org_id=org_id)
        return field

    def save_model(self, request: Any, obj: Model, form: Any, change: bool) -> None:
        if not change and hasattr(obj, "org_id") and not is_platform_operator(request):
            obj.org_id = admin_context(request).org_id
        super().save_model(request, obj, form, change)


class ReadOnlyAdmin(OrgScopedAdmin):
    """Solo consulta: ni alta, ni edición, ni borrado. Las acciones con efecto
    van por `backoffice/services.py` (políticas y auditoría)."""

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Model | None = None) -> bool:
        return False


def run_action(
    modeladmin: admin.ModelAdmin,
    request: Any,
    queryset: QuerySet[Any],
    action: Callable[[RequestContext, Any], Any],
    *,
    done: str,
) -> None:
    """Aplica [action] a cada fila e informa de cuántas salieron y cuántas no.

    Una fila sobre la que la política no deja actuar no corta las demás: se
    cuenta y se informa (el superusuario puede ver filas de otras
    organizaciones sin poder actuar sobre ellas).
    """
    ctx = admin_context(request)
    ok = denied = 0
    for obj in queryset:
        try:
            if action(ctx, obj) is not False:
                ok += 1
        except PermissionDenied:
            denied += 1
    if ok:
        modeladmin.message_user(request, f"{done}: {ok}.", messages.SUCCESS)
    if denied:
        modeladmin.message_user(
            request,
            f"Sin permiso sobre {denied} de las filas seleccionadas (otra organización o rol).",
            messages.WARNING,
        )

from collections.abc import Callable
from typing import Any

from django.urls import path
from django.views.decorators.clickjacking import xframe_options_sameorigin

from suno.equipment import views

app_name = "equipment"


def _framed(route: str, view: Callable[..., Any], name: str) -> Any:
    """Las vistas del módulo se muestran en el panel flotante (iframe del mismo origen)."""
    return path(route, xframe_options_sameorigin(view), name=name)


urlpatterns = [
    _framed("", views.equipment_list, name="list"),
    _framed("usos/", views.usage_list, name="usage_list"),
    _framed("usos/nuevo/", views.usage_create, name="usage_create"),
    _framed("identificar/", views.equipment_identify, name="identify"),
    _framed("etiquetas/", views.equipment_labels, name="labels"),
    _framed("costos/", views.equipment_costs, name="costs"),
    _framed("costos/recalcular/", views.costs_recalculate, name="costs_recalculate"),
    _framed("nuevo/", views.equipment_create, name="create"),
    _framed("tipos/nuevo/", views.equipment_type_create, name="type_create"),
    _framed("colecciones/nueva/", views.collection_create, name="collection_create"),
    _framed(
        "colecciones/<uuid:collection_id>/",
        views.collection_detail,
        name="collection_detail",
    ),
    _framed(
        "colecciones/<uuid:collection_id>/editar/",
        views.collection_edit,
        name="collection_edit",
    ),
    _framed("<uuid:equipment_id>/", views.equipment_detail, name="detail"),
    _framed("<uuid:equipment_id>/editar/", views.equipment_edit, name="edit"),
    _framed("<uuid:equipment_id>/tarifas/nueva/", views.rate_create, name="rate_create"),
    _framed("<uuid:equipment_id>/qr/", views.equipment_qr, name="qr"),
    _framed(
        "<uuid:equipment_id>/qr.<str:fmt>",
        views.equipment_qr_image,
        name="qr_image",
    ),
    _framed("<uuid:equipment_id>/documentos/nuevo/", views.document_create, name="document_create"),
    _framed(
        "<uuid:equipment_id>/proyectos/nuevo/", views.project_link_create, name="project_create"
    ),
    _framed("<uuid:equipment_id>/operadores/nuevo/", views.operator_create, name="operator_create"),
]

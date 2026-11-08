from collections.abc import Callable
from typing import Any

from django.urls import path
from django.views.decorators.clickjacking import xframe_options_sameorigin

from suno.equipment import (
    fuel_views,
    inspection_views,
    maintenance_views,
    transport_views,
    views,
)

app_name = "equipment"


def _framed(route: str, view: Callable[..., Any], name: str) -> Any:
    """Las vistas del módulo se muestran en el panel flotante (iframe del mismo origen)."""
    return path(route, xframe_options_sameorigin(view), name=name)


urlpatterns = [
    _framed("", views.equipment_list, name="list"),
    _framed("usos/", views.usage_list, name="usage_list"),
    _framed("transporte/", transport_views.transport_panel, name="transport"),
    _framed("transporte/viajes/nuevo/", transport_views.trip_create, name="transport_trip_create"),
    _framed(
        "transporte/materiales/nuevo/",
        transport_views.material_create,
        name="transport_material_create",
    ),
    _framed(
        "transporte/tramos/nuevo/", transport_views.route_create, name="transport_route_create"
    ),
    _framed("transporte/tarifas/nueva/", transport_views.rate_create, name="transport_rate_create"),
    _framed(
        "transporte/empresas/nueva/",
        transport_views.contractor_create,
        name="transport_contractor_create",
    ),
    _framed(
        "transporte/vehiculos/nuevo/",
        transport_views.vehicle_create,
        name="transport_vehicle_create",
    ),
    _framed(
        "transporte/liquidaciones/nueva/",
        transport_views.settlement_create,
        name="transport_settlement_create",
    ),
    _framed(
        "transporte/liquidaciones/<uuid:settlement_id>/",
        transport_views.settlement_detail,
        name="transport_settlement",
    ),
    _framed(
        "transporte/liquidaciones/<uuid:settlement_id>/confirmar/",
        transport_views.settlement_confirm,
        name="transport_settlement_confirm",
    ),
    _framed("combustible/", fuel_views.fuel_panel, name="fuel"),
    _framed("combustible/tanqueo/nuevo/", fuel_views.fill_create, name="fuel_fill_create"),
    _framed(
        "combustible/proveedores/nuevo/", fuel_views.supplier_create, name="fuel_supplier_create"
    ),
    _framed("combustible/estaciones/nueva/", fuel_views.station_create, name="fuel_station_create"),
    _framed("combustible/tarifas/nueva/", fuel_views.rate_create, name="fuel_rate_create"),
    _framed("combustible/tanques/nuevo/", fuel_views.store_create, name="fuel_store_create"),
    _framed("combustible/compras/nueva/", fuel_views.purchase_create, name="fuel_purchase_create"),
    _framed(
        "combustible/liquidaciones/nueva/",
        fuel_views.liquidation_create,
        name="fuel_liquidation_create",
    ),
    _framed(
        "combustible/liquidaciones/<uuid:liquidation_id>/",
        fuel_views.liquidation_detail,
        name="fuel_liquidation",
    ),
    _framed(
        "combustible/liquidaciones/<uuid:liquidation_id>/confirmar/",
        fuel_views.liquidation_confirm,
        name="fuel_liquidation_confirm",
    ),
    _framed("<uuid:equipment_id>/rendimiento/", fuel_views.yield_view, name="fuel_yield"),
    _framed("mantenimiento/", maintenance_views.maintenance_panel, name="maintenance"),
    _framed(
        "mantenimiento/ordenes/<uuid:order_id>/",
        maintenance_views.order_detail,
        name="order_detail",
    ),
    _framed(
        "mantenimiento/ordenes/<uuid:order_id>/<str:action>/",
        maintenance_views.order_action,
        name="order_action",
    ),
    _framed(
        "mantenimiento/planes/<uuid:plan_id>/retirar/",
        maintenance_views.plan_retire,
        name="plan_retire",
    ),
    _framed("inspecciones/", inspection_views.inspection_list, name="inspection_list"),
    _framed(
        "inspecciones/<uuid:inspection_id>/",
        inspection_views.inspection_detail,
        name="inspection_detail",
    ),
    _framed(
        "inspecciones/<uuid:inspection_id>/revisar/",
        inspection_views.inspection_review,
        name="inspection_review",
    ),
    _framed("novedades/<uuid:fault_id>/cerrar/", inspection_views.fault_close, name="fault_close"),
    _framed("tipos/<uuid:type_id>/lista/", inspection_views.checklist_edit, name="checklist"),
    _framed("usos/nuevo/", views.usage_create, name="usage_create"),
    _framed("identificar/", views.equipment_identify, name="identify"),
    _framed("etiquetas/", views.equipment_labels, name="labels"),
    _framed("costos/", views.equipment_costs, name="costs"),
    _framed("costos/recalcular/", views.costs_recalculate, name="costs_recalculate"),
    _framed("nuevo/", views.equipment_create, name="create"),
    _framed("tipos/nuevo/", views.equipment_type_create, name="type_create"),
    _framed("departamentos/nuevo/", views.department_create, name="department_create"),
    _framed("campos/nuevo/", views.field_definition_create, name="field_definition_create"),
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
    _framed("<uuid:equipment_id>/lecturas/nueva/", views.meter_reading_create, name="meter_create"),
    _framed(
        "<uuid:equipment_id>/campos/valor/", views.field_value_create, name="field_value_create"
    ),
    _framed(
        "<uuid:equipment_id>/ordenes/nueva/", maintenance_views.order_create, name="order_create"
    ),
    _framed("<uuid:equipment_id>/planes/nuevo/", maintenance_views.plan_create, name="plan_create"),
    _framed(
        "<uuid:equipment_id>/hoja-de-vida.pdf", maintenance_views.hoja_de_vida, name="life_sheet"
    ),
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

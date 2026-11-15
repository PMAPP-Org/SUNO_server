"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/materiales/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "materials"

K = "kardex/<uuid:warehouse_id>/<uuid:material_id>/"

urlpatterns = [
    path("", views.inventory, name="inventory"),
    path("inventario.xlsx", views.export_inventory, name="export_inventory"),
    path(K, views.kardex_page, name="kardex"),
    path(K + "kardex.xlsx", views.export_kardex, name="export_kardex"),
    path("bodegas/", views.warehouse_list, name="warehouses"),
    path("bodegas/<uuid:warehouse_id>/", views.warehouse_update, name="warehouse_update"),
]

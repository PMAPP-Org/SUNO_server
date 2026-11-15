"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/materiales/` (config/urls.py)."""

from django.urls import path

from . import views, views_purchases

app_name = "materials"

K = "kardex/<uuid:warehouse_id>/<uuid:material_id>/"
P = "compras/<uuid:purchase_id>/"

urlpatterns = [
    path("", views.inventory, name="inventory"),
    path("inventario.xlsx", views.export_inventory, name="export_inventory"),
    path(K, views.kardex_page, name="kardex"),
    path(K + "kardex.xlsx", views.export_kardex, name="export_kardex"),
    path("bodegas/", views.warehouse_list, name="warehouses"),
    path("bodegas/<uuid:warehouse_id>/", views.warehouse_update, name="warehouse_update"),
    path("compras/", views_purchases.purchase_list, name="purchases"),
    path("compras/nueva/", views_purchases.purchase_new, name="new_purchase"),
    path(P, views_purchases.purchase_detail, name="purchase"),
    path(P + "editar/", views_purchases.purchase_edit, name="edit_purchase"),
    path(P + "borrar/", views_purchases.purchase_delete, name="delete_purchase"),
    path(P + "soporte/", views_purchases.purchase_support, name="purchase_support"),
    path(P + "soporte/ver/", views_purchases.purchase_support_file, name="purchase_file"),
    path(P + "aprobar/", views_purchases.purchase_approve, name="approve_purchase"),
    path(P + "rechazar/", views_purchases.purchase_reject, name="reject_purchase"),
    path(P + "anular/", views_purchases.purchase_void, name="void_purchase"),
]

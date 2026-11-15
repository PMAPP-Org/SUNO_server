"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/materiales/` (config/urls.py)."""

from django.urls import path

from . import views, views_movements, views_purchases

app_name = "materials"

K = "kardex/<uuid:warehouse_id>/<uuid:material_id>/"
P = "compras/<uuid:purchase_id>/"
S = "salidas/<uuid:issue_id>/"

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
    path("salidas/", views_movements.issue_list, name="issues"),
    path("salidas.xlsx", views_movements.export_issues, name="export_issues"),
    path(S, views_movements.issue_detail, name="issue"),
    path(S + "editar/", views_movements.issue_edit, name="edit_issue"),
    path(S + "borrar/", views_movements.issue_delete, name="delete_issue"),
    path(S + "devolver/", views_movements.issue_return, name="return_issue"),
    path("traslados/", views_movements.transfer_list, name="transfers"),
    path("traslados/<uuid:transfer_id>/", views_movements.transfer_detail, name="transfer"),
]

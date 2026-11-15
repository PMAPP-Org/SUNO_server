"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/contratos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "contracts"

C = "<uuid:contract_id>/"
A = C + "otrosies/<uuid:amendment_id>/"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("nuevo/", views.new_contract, name="new"),
    path(C, views.contract_detail, name="detail"),
    path(C + "editar/", views.edit_contract, name="edit"),
    path(C + "borrar/", views.delete_contract, name="delete"),
    path(C + "aprobar/", views.approve_contract, name="approve"),
    path(C + "rechazar/", views.reject_contract, name="reject"),
    path(C + "anular/", views.void_contract, name="void"),
    path(C + "otrosies/nuevo/", views.new_amendment, name="new_amendment"),
    path(A + "editar/", views.edit_amendment, name="edit_amendment"),
    path(A + "borrar/", views.delete_amendment, name="delete_amendment"),
    path(A + "aprobar/", views.approve_amendment, name="approve_amendment"),
    path(A + "rechazar/", views.reject_amendment, name="reject_amendment"),
]

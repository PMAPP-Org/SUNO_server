"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/contratos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "contracts"

C = "<uuid:contract_id>/"
A = C + "otrosies/<uuid:amendment_id>/"
R = C + "actas/<uuid:certificate_id>/"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("contratos.xlsx", views.export_list, name="export"),
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
    path(C + "actas/nueva/", views.new_certificate, name="new_certificate"),
    path(R, views.certificate_detail, name="certificate"),
    path(R + "editar/", views.edit_certificate, name="edit_certificate"),
    path(R + "borrar/", views.delete_certificate, name="delete_certificate"),
    path(R + "firmada/", views.upload_signed, name="upload_signed"),
    path(R + "soporte/", views.signed_copy, name="signed_copy"),
    path(R + "aprobar/", views.approve_certificate, name="approve_certificate"),
    path(R + "rechazar/", views.reject_certificate, name="reject_certificate"),
    path(R + "anular/", views.void_certificate, name="void_certificate"),
    path(C + "contrato.xlsx", views.export_contract, name="export_contract"),
    path(R + "acta.pdf", views.certificate_document_pdf, name="certificate_pdf"),
]

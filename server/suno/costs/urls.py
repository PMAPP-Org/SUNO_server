"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/costos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "costs"

urlpatterns = [
    path("", views.ledger_list, name="list"),
    path("nuevo/", views.new, name="new"),
    path("<uuid:entry_id>/", views.detail, name="detail"),
    path("<uuid:entry_id>/editar/", views.edit, name="edit"),
    path("<uuid:entry_id>/borrar/", views.delete, name="delete"),
    path("<uuid:entry_id>/soporte/", views.support, name="support"),
    path("por-aprobar/", views.inbox, name="inbox"),
    path("rechazados/", views.rejected, name="rejected"),
    path("importar/", views.import_upload, name="import"),
    path("importar/aplicar/", views.import_apply, name="import_apply"),
    path("plantilla.xlsx", views.template_download, name="template"),
    path("libro.xlsx", views.export, name="export"),
    path("<uuid:entry_id>/aprobar/", views.approve, name="approve"),
    path("<uuid:entry_id>/rechazar/", views.reject, name="reject"),
    path("<uuid:entry_id>/reversar/", views.reverse_entry, name="reverse"),
]

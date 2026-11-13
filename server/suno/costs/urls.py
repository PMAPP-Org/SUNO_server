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
]

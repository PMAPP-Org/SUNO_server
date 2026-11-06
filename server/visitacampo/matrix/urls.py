"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/matriz/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "matrix"

urlpatterns = [
    path("", views.items, name="items"),
    path("ubicacion/", views.locations, name="locations"),
    path("niveles/", views.levels, name="levels"),
    path("plantilla.xlsx", views.template_download, name="template"),
    path("importar/", views.import_upload, name="import"),
    path("importar/<uuid:import_id>/", views.import_preview, name="import_preview"),
    path("historial/", views.history, name="history"),
]

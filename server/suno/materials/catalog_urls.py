"""Montadas bajo `plataforma/materiales/` (config/urls.py)."""

from django.urls import path

from . import catalog_views

app_name = "materials_catalog"

urlpatterns = [
    path("", catalog_views.catalog_list, name="list"),
    path("nuevo/", catalog_views.catalog_new, name="new"),
    path("<uuid:material_id>/editar/", catalog_views.catalog_edit, name="edit"),
    path("importar/", catalog_views.catalog_import_upload, name="import"),
    path("importar/aplicar/", catalog_views.catalog_import_apply, name="import_apply"),
    path("plantilla.xlsx", catalog_views.catalog_template, name="template"),
]

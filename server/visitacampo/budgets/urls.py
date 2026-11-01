"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/presupuestos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget_list, name="list"),
    path("<uuid:budget_id>/", views.budget_detail, name="detail"),
    path("<uuid:budget_id>/importar/", views.import_upload, name="import"),
    path(
        "<uuid:budget_id>/importar/<uuid:version_id>/",
        views.import_preview,
        name="import_preview",
    ),
    path(
        "<uuid:budget_id>/importar/<uuid:version_id>/mapeo/",
        views.import_mapping,
        name="import_mapping",
    ),
]

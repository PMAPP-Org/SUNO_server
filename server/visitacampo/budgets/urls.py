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
    path("<uuid:budget_id>/historial/", views.history, name="history"),
    path(
        "<uuid:budget_id>/versiones/<uuid:version_id>/",
        views.version_detail,
        name="version",
    ),
    path(
        "<uuid:budget_id>/versiones/<uuid:version_id>/excel",
        views.version_source,
        name="version_source",
    ),
    path("<uuid:budget_id>/exportar.xlsx", views.export_comparison, name="export"),
]

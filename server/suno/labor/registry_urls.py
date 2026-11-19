"""Montadas bajo `plataforma/trabajadores/` (config/urls.py). La conciliación (Task 16) se
agrega aquí también."""

from django.urls import path

from . import registry_views

app_name = "labor_registry"

W = "<uuid:worker_id>/"

urlpatterns = [
    path("", registry_views.worker_list, name="list"),
    path("nuevo/", registry_views.worker_new, name="new"),
    path(W, registry_views.worker_detail, name="worker"),
    path(W + "editar/", registry_views.worker_edit, name="edit"),
    path(W + "jornal/", registry_views.worker_wage, name="wage"),
    path(W + "jornal/<uuid:wage_id>/", registry_views.wage_update, name="wage_update"),
    path(W + "retirar/", registry_views.worker_retire, name="retire"),
    path(W + "borrar/", registry_views.worker_delete, name="delete"),
    path("importar/", registry_views.import_upload, name="import"),
    path("importar/aplicar/", registry_views.import_apply, name="import_apply"),
    path("plantilla.xlsx", registry_views.import_template, name="template"),
    path("factor/", registry_views.factor_list, name="factors"),
    path("factor/<uuid:factor_id>/", registry_views.factor_update, name="factor_update"),
]

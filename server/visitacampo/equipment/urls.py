from django.urls import path

from visitacampo.equipment import views

app_name = "equipment"

urlpatterns = [
    path("", views.equipment_list, name="list"),
    path("usos/", views.usage_list, name="usage_list"),
    path("usos/nuevo/", views.usage_create, name="usage_create"),
    path("nuevo/", views.equipment_create, name="create"),
    path("tipos/nuevo/", views.equipment_type_create, name="type_create"),
    path("colecciones/nueva/", views.collection_create, name="collection_create"),
    path(
        "colecciones/<uuid:collection_id>/",
        views.collection_detail,
        name="collection_detail",
    ),
    path(
        "colecciones/<uuid:collection_id>/editar/",
        views.collection_edit,
        name="collection_edit",
    ),
    path("<uuid:equipment_id>/", views.equipment_detail, name="detail"),
    path("<uuid:equipment_id>/editar/", views.equipment_edit, name="edit"),
    path("<uuid:equipment_id>/documentos/nuevo/", views.document_create, name="document_create"),
    path("<uuid:equipment_id>/proyectos/nuevo/", views.project_link_create, name="project_create"),
    path("<uuid:equipment_id>/operadores/nuevo/", views.operator_create, name="operator_create"),
]

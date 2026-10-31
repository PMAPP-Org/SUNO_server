from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("<uuid:project_id>/", views.schedule, name="schedule"),
    path("<uuid:project_id>/gantt/", views.schedule, name="gantt"),
    path("<uuid:project_id>/historial/", views.history, name="history"),
    path(
        "<uuid:project_id>/historial/<uuid:import_id>/xml",
        views.import_xml,
        name="import_xml",
    ),
    path("<uuid:project_id>/importar/", views.import_upload, name="import"),
    path(
        "<uuid:project_id>/importar/<uuid:import_id>/",
        views.import_preview,
        name="import_preview",
    ),
    path("<uuid:project_id>/tareas/<uuid:task_id>/", views.task_detail, name="task"),
    path("<uuid:project_id>/exportar.xlsx", views.schedule, name="export"),
]

from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("<uuid:project_id>/", views.schedule, name="schedule"),
    path("<uuid:project_id>/gantt/", views.schedule, name="gantt"),
    path("<uuid:project_id>/historial/", views.schedule, name="history"),
    path("<uuid:project_id>/importar/", views.schedule, name="import"),
    path("<uuid:project_id>/tareas/<uuid:task_id>/", views.schedule, name="task"),
    path("<uuid:project_id>/exportar.xlsx", views.schedule, name="export"),
]

"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/tareo/` (config/urls.py)."""

from django.urls import path

from . import views, views_timesheets

app_name = "labor"

T = "<uuid:timesheet_id>/"

urlpatterns = [
    path("", views.timesheet_list, name="timesheets"),
    path("nueva/", views.timesheet_create, name="new_timesheet"),
    path("asignaciones/", views.assignment_list, name="assignments"),
    path("asignaciones/<uuid:assignment_id>/", views.assignment_update, name="assignment"),
    path(T, views_timesheets.timesheet_detail, name="timesheet"),
]

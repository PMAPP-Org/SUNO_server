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
    path(T + "guardar/", views_timesheets.timesheet_save, name="save_timesheet"),
    path(T + "borrar/", views_timesheets.timesheet_delete, name="delete_timesheet"),
    path(T + "firmada/", views_timesheets.timesheet_signed, name="timesheet_signed"),
    path(T + "firmada/ver/", views_timesheets.timesheet_signed_file, name="timesheet_file"),
    path(T + "aprobar/", views_timesheets.timesheet_approve, name="approve_timesheet"),
    path(T + "rechazar/", views_timesheets.timesheet_reject, name="reject_timesheet"),
    path(T + "anular/", views_timesheets.timesheet_reverse, name="reverse_timesheet"),
    path(T + "planilla.pdf", views_timesheets.timesheet_document, name="timesheet_pdf"),
    path(T + "planilla.xlsx", views_timesheets.timesheet_sheet, name="timesheet_xlsx"),
    path(T + "importar/", views_timesheets.timesheet_import_upload, name="import_timesheet"),
    path(
        T + "importar/aplicar/",
        views_timesheets.timesheet_import_apply,
        name="import_timesheet_apply",
    ),
    path("plantilla.xlsx", views_timesheets.timesheet_import_template, name="template"),
]

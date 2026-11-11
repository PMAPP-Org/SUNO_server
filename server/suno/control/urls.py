"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/control/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "control"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("control.xlsx", views.export, name="export"),
]

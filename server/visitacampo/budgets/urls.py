"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/presupuestos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget_list, name="list"),
    path("<uuid:budget_id>/", views.budget_detail, name="detail"),
]

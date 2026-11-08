from django.urls import path

from suno.core.access_views import access_matrix

app_name = "access_control"

urlpatterns = [path("", access_matrix, name="matrix")]

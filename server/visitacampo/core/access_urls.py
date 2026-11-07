from django.urls import path

from visitacampo.core.access_views import access_matrix

app_name = "access_control"

urlpatterns = [path("", access_matrix, name="matrix")]

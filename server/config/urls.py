"""URLConf raíz. La API versionada vive bajo `/v1/` (Ninja, `config/api.py`);
`/admin/` es el back-office de fase 1 decidido en ADR-004 (se personaliza a
partir de los pasos que añaden modelos de dominio)."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from config.api import api
from visitacampo.core.views import platform_home, platform_login, platform_logout

urlpatterns = [
    path("", TemplateView.as_view(template_name="suno/home.html"), name="home"),
    path("plataforma/", platform_home, name="platform_home"),
    path("plataforma/ingresar/", platform_login, name="platform_login"),
    path("plataforma/salir/", platform_logout, name="platform_logout"),
    path("plataforma/proyectos/", include("visitacampo.projects.urls")),
    path("admin/", admin.site.urls),
    path("v1/", api.urls),
]

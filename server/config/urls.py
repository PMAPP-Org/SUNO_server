"""URLConf raíz. La API versionada vive bajo `/v1/` (Ninja, `config/api.py`);
`/admin/` es el back-office de fase 1 decidido en ADR-004 (se personaliza a
partir de los pasos que añaden modelos de dominio)."""

from django.contrib import admin
from django.urls import path

from config.api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("v1/", api.urls),
]

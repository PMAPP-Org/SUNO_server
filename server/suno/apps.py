from django.apps import AppConfig


class SunoConfig(AppConfig):
    """App principal de SUNO (Paso 4: scaffolding).

    Los modelos de dominio (visitas, plantillas, dispositivos, jobs...) se
    añaden a partir del Paso 5 en adelante. Por ahora esta app solo aloja el
    endpoint de salud (`suno/health/`).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "suno"
    verbose_name = "SUNO"

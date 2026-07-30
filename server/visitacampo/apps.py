from django.apps import AppConfig


class VisitaCampoConfig(AppConfig):
    """App principal de VisitaCampo (Paso 4: scaffolding).

    Los modelos de dominio (visitas, plantillas, dispositivos, jobs...) se
    añaden a partir del Paso 5 en adelante. Por ahora esta app solo aloja el
    endpoint de salud (`visitacampo/health/`).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "visitacampo"
    verbose_name = "VisitaCampo"

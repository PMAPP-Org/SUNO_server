from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuración de la app `core` (Paso 5: modelo de datos núcleo).

    **Elección de nombre**: el brief permitía `core` o `tenancy`. Se elige
    `core` porque la app no aloja solo el andamiaje multi-tenant
    (`Organization`/`Membership`), sino también el dominio (`Visit`,
    `Blob`, `Job`, `AuditEvent`...). `tenancy` habría sido un nombre
    engañoso para una app que contiene toda la tabla §4.4.

    **Ubicación**: vive dentro del paquete `visitacampo/` en lugar de en
    `server/core/` para respetar la convención ya establecida en el Paso 4
    (todo el código de dominio cuelga de `server/visitacampo/`). El
    `label` sigue siendo `core`, así que los comandos del brief funcionan
    tal cual (`manage.py makemigrations core`, `manage.py migrate core zero`).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "visitacampo.core"
    label = "core"
    verbose_name = "Núcleo VisitaCampo"

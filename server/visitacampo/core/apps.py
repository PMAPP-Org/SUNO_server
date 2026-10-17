from django.apps import AppConfig
from django.contrib.admin.apps import AdminConfig


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


class OtpAdminConfig(AdminConfig):
    """Django Admin con segundo factor obligatorio (riesgo R-20, A.8.5).

    Sustituye a `django.contrib.admin` en `INSTALLED_APPS`. `default_site` es la
    forma documentada por Django de cambiar el sitio por defecto, de modo que
    `admin.site` **es** un `OTPAdminSite` desde que arranca el proceso: su
    formulario de login pide usuario, contraseña y código TOTP (o de respaldo),
    y una sesión sin segundo factor verificado no entra. No hay ajuste para
    desactivarlo. El alta: `python manage.py enroll_admin_totp <usuario>`.
    """

    default_site = "visitacampo.core.admin_site.VisitaCampoOTPAdminSite"

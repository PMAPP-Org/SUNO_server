from django.apps import AppConfig


class AuthnConfig(AppConfig):
    """App de autenticación (Paso 7: login + JWT + registro de dispositivo).

    **Elección de nombre** (la nota del controlador la dejaba abierta entre
    `auth` y `authn`): **`authn`**. `auth` colisiona con `django.contrib.auth`
    en dos sitios que importan: el `label` de la app —que es la clave de
    `INSTALLED_APPS`, de `makemigrations <label>` y de la tabla
    `django_migrations`— y el nombre del paquete importable, que convertiría
    cualquier `from auth import ...` en una ambigüedad. `authn` (del inglés
    *authentication*, frente a *authz*/autorización, que es el Paso 6) deja
    claro además el reparto: aquí se decide **quién eres**; en
    `visitacampo/core/policies.py`, **qué puedes hacer**.

    **Ubicación**: dentro de `visitacampo/` como el resto del código de
    dominio (`visitacampo/core/`, `visitacampo/health/`), no en `server/auth/`
    como decía el texto original del brief — misma corrección que ya se aplicó
    en los Pasos 5 y 6.

    **Por qué es una app de Django y no un paquete a secas** (como
    `visitacampo/health/` o `visitacampo/serverinfo/`): tiene un modelo
    propio, `RefreshToken`, y por tanto necesita `label` y migraciones
    propias. Vive separada de `core` a propósito: `core` es el modelo de datos
    de negocio (doc 08 §4.4) y `refresh_token` es infraestructura de sesión,
    con un ciclo de vida y una criticidad de seguridad distintos.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "visitacampo.authn"
    label = "authn"
    verbose_name = "Autenticación VisitaCampo"

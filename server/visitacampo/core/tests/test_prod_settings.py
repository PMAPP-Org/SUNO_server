"""Verifica que `config.settings.prod` completa el endurecimiento de RD-15
(cabeceras de proxy confiable detrás de Caddy) y de estáticos (Tarea 3 de
plans/visitacampo-despliegue-demo-implementacion.md). No usa `manage.py
check --deploy` como test (ese comando solo emite warnings, nunca falla) —
en su lugar importa el módulo de settings directamente y comprueba los
valores, que es lo que un `check --deploy` verde depende de que sea cierto.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from types import ModuleType
from unittest import mock

from django.core.exceptions import ImproperlyConfigured

STRONG_KEY = "k" * 10 + "Zq8#vL2@pX9!mR4$wT7&yN1*bC6^hJ3%fD5" + "e" * 10


def _load_prod(secret_key: str | None) -> ModuleType:
    """Importa `config.settings.prod` desde cero con `DJANGO_SECRET_KEY` dado."""
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SECRET_KEY"}
    if secret_key is not None:
        env["DJANGO_SECRET_KEY"] = secret_key
    with mock.patch.dict(os.environ, env, clear=True):
        for name in ("config.settings.prod", "config.settings.base"):
            sys.modules.pop(name, None)
        return importlib.import_module("config.settings.prod")


class SecretKeyTests(unittest.TestCase):
    """Producción no arranca con una SECRET_KEY ausente, débil o de plantilla.

    `base.py` cae a un valor `django-insecure-…` para que el desarrollo local
    funcione sin configurar nada; heredar ese valor en producción permitiría
    falsificar cookies de sesión y tokens firmados por Django (OWASP A02/A04).
    """

    def tearDown(self) -> None:
        _load_prod(STRONG_KEY)

    def test_a_strong_key_is_accepted(self) -> None:
        self.assertEqual(_load_prod(STRONG_KEY).SECRET_KEY, STRONG_KEY)

    def test_a_missing_key_refuses_to_start(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            _load_prod(None)

    def test_the_insecure_development_default_refuses_to_start(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            _load_prod("django-insecure-" + "x" * 60)

    def test_the_env_example_placeholder_refuses_to_start(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            _load_prod("CAMBIAR_EN_PRODUCCION_django_secret_key" + "0123456789abcdef")

    def test_a_short_key_refuses_to_start(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            _load_prod("Zq8#vL2@pX9!mR4$")

    def test_a_low_entropy_key_refuses_to_start(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            _load_prod("ab" * 40)


class ProdSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prod = _load_prod(STRONG_KEY)

    def test_trusts_the_x_forwarded_proto_header_set_by_caddy(self) -> None:
        self.assertEqual(self.prod.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_does_not_trust_x_forwarded_host(self) -> None:
        """RD-15: `USE_X_FORWARDED_HOST=False`.

        Caddy ya entrega el `Host` original (`header_up Host {host}`) y Django
        lo valida contra `ALLOWED_HOSTS`; confiar además en
        `X-Forwarded-Host` añadiría una segunda fuente de verdad que, si algún
        día hubiera otro salto delante de Caddy, permitiría envenenar las URLs
        absolutas que genera Django (enlaces de reseteo, redirecciones).
        """
        self.assertFalse(self.prod.USE_X_FORWARDED_HOST)

    def test_redirects_plain_http_to_https(self) -> None:
        self.assertTrue(self.prod.SECURE_SSL_REDIRECT)

    def test_sends_hsts_for_a_full_year_including_subdomains(self) -> None:
        self.assertEqual(self.prod.SECURE_HSTS_SECONDS, 31536000)
        self.assertTrue(self.prod.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_cookies_are_marked_secure(self) -> None:
        self.assertTrue(self.prod.SESSION_COOKIE_SECURE)
        self.assertTrue(self.prod.CSRF_COOKIE_SECURE)

    def test_static_root_is_set_for_collectstatic(self) -> None:
        self.assertTrue(str(self.prod.STATIC_ROOT).endswith("staticfiles"))

    def test_staticfiles_storage_uses_whitenoise_compressed_manifest_backend(self) -> None:
        # Hallazgo Importante de la revisión final: nada servía `/static/*` en
        # producción (sin whitenoise, sin `file_server` en Caddy). El backend
        # de staticfiles pasa de `ManifestStaticFilesStorage` (Tarea 3) al de
        # whitenoise, que además comprime y es el que `WhiteNoiseMiddleware`
        # (`config/settings/base.py`) espera para servir los archivos ya
        # comprimidos.
        self.assertEqual(
            self.prod.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )

    def test_whitenoise_middleware_is_installed_right_after_security_middleware(self) -> None:
        security_index = self.prod.MIDDLEWARE.index("django.middleware.security.SecurityMiddleware")
        self.assertEqual(
            self.prod.MIDDLEWARE[security_index + 1],
            "whitenoise.middleware.WhiteNoiseMiddleware",
        )


if __name__ == "__main__":
    unittest.main()

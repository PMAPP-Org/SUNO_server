"""Verifica que `config.settings.prod` completa el endurecimiento de RD-15
(cabeceras de proxy confiable detrás de Caddy) y de estáticos (Tarea 3 de
plans/visitacampo-despliegue-demo-implementacion.md). No usa `manage.py
check --deploy` como test (ese comando solo emite warnings, nunca falla) —
en su lugar importa el módulo de settings directamente y comprueba los
valores, que es lo que un `check --deploy` verde depende de que sea cierto.
"""

from __future__ import annotations

import importlib
import unittest


class ProdSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prod = importlib.import_module("config.settings.prod")

    def test_trusts_the_x_forwarded_proto_header_set_by_caddy(self) -> None:
        self.assertEqual(self.prod.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_trusts_the_x_forwarded_host_header_set_by_caddy(self) -> None:
        self.assertTrue(self.prod.USE_X_FORWARDED_HOST)

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

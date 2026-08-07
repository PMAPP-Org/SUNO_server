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

    def test_staticfiles_storage_uses_the_manifest_backend(self) -> None:
        self.assertEqual(
            self.prod.STORAGES["staticfiles"]["BACKEND"],
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        )


if __name__ == "__main__":
    unittest.main()

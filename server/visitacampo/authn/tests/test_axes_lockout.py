"""`django-axes` — rate limiting de login (Tarea 4 de
plans/visitacampo-despliegue-demo-implementacion.md, ADR-004). Reemplaza la
construcción de rate limiting a mano: el bloqueo lo decide
`axes.backends.AxesBackend`, antepuesto en `AUTHENTICATION_BACKENDS`, dentro
de la misma llamada a `django.contrib.auth.authenticate()` que
`authn/services.py::log_in` ya hacía. `log_in` sí gana una línea (llamada a
`axes.utils.reset()`, API pública del paquete) para que
`AXES_RESET_ON_SUCCESS` funcione en este endpoint JWT sin sesión — ver el
comentario en `services.py` y en `config/settings/base.py` para el porqué.
"""

from __future__ import annotations

import unittest

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import PASSWORD, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LoginLockoutTests(TestCase):
    def setUp(self) -> None:
        # `axes` guarda los intentos fallidos en el cache 'default' (locmem,
        # ADR-006: sin Redis) — sin limpiarlo, el contador de una prueba
        # anterior en el mismo proceso de test contaminaría esta.
        cache.clear()
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()

    def _login(self, password: str):
        return post_json(
            self.client,
            "/v1/auth/login",
            {
                "username": "agente",
                "password": password,
                "device_info": {
                    "device_id": "11111111-1111-1111-1111-111111111111",
                    "platform": "android",
                    "app_version": "0.1.0",
                },
            },
        )

    def test_after_the_failure_limit_even_the_correct_password_is_rejected(self) -> None:
        limit = settings.AXES_FAILURE_LIMIT
        for _ in range(limit):
            response = self._login("contraseña-incorrecta")
            self.assertEqual(response.status_code, 401)

        locked_out_response = self._login(PASSWORD)

        self.assertEqual(locked_out_response.status_code, 401)
        self.assertEqual(locked_out_response.json()["code"], "invalid_credentials")

    def test_below_the_failure_limit_the_correct_password_still_succeeds(self) -> None:
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")

        response = self._login(PASSWORD)

        self.assertEqual(response.status_code, 200, response.content)

    def test_a_successful_login_resets_the_failure_counter(self) -> None:
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")
        self.assertEqual(self._login(PASSWORD).status_code, 200)

        # Tras el éxito (AXES_RESET_ON_SUCCESS=True), el contador vuelve a
        # cero: (limit - 1) fallos más siguen sin bloquear la cuenta.
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._login("contraseña-incorrecta")

        self.assertEqual(self._login(PASSWORD).status_code, 200)


if __name__ == "__main__":
    unittest.main()

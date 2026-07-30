"""Tests del endpoint de salud (Paso 4 — scaffolding).

Usa `SimpleTestCase` a propósito: `/healthz` y `/readyz` en este paso no
tocan la base de datos, y así el test corre en CI/local sin necesitar una
Postgres real levantada (`.github/workflows/backend.yml` no levanta un
servicio de BD para el job de lint-and-test).
"""

from django.test import Client, SimpleTestCase


class HealthzTests(SimpleTestCase):
    def test_returns_200_ok(self) -> None:
        response = Client().get("/v1/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_does_not_leak_information(self) -> None:
        """doc 08 §6 / ADR-004: sin versión, hostname ni estado de dependencias."""
        response = Client().get("/v1/healthz")
        self.assertEqual(set(response.json().keys()), {"status"})

    def test_does_not_require_authentication(self) -> None:
        response = Client().get("/v1/healthz", HTTP_AUTHORIZATION="")
        self.assertEqual(response.status_code, 200)


class ReadyzTests(SimpleTestCase):
    def test_placeholder_returns_200_ok(self) -> None:
        response = Client().get("/v1/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

"""Tests de `POST /v1/auth/logout` (Paso 7, tarea 4).

Dos invariantes propias de este endpoint, además de la revocación:

- **Autenticado**: el contrato no le pone `security: []` (a diferencia de
  login/refresh), así que hereda el `bearerAuth` global.
- **No borra la outbox del cliente**: el servidor no toca ni el dispositivo ni
  ningún dato de negocio; el trabajo pendiente del agente vive en el móvil y es
  el Paso 13 quien debe conservarlo (ADR-008).
"""

from __future__ import annotations

import unittest
import uuid

from django.test import Client, TestCase

from visitacampo.authn.models import RefreshToken
from visitacampo.core.models import Device
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import device_info, login, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LogoutTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.device_id = uuid.uuid4()
        self.client = Client()
        tokens = login(self.client, device=device_info(self.device_id)).json()["tokens"]
        self.refresh_token = tokens["refresh_token"]
        self.access_token = tokens["access_token"]

    def logout(self, refresh_token: str | None = None, access_token: str | None = None):
        headers = {}
        token = self.access_token if access_token is None else access_token
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return post_json(
            self.client,
            "/v1/auth/logout",
            {"refresh_token": self.refresh_token if refresh_token is None else refresh_token},
            **headers,
        )

    def test_revokes_the_token_and_answers_204(self) -> None:
        response = self.logout()
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(response.content, b"")
        self.assertIsNotNone(RefreshToken.objects.get().revoked_at)

    def test_a_revoked_token_can_no_longer_refresh(self) -> None:
        self.logout()
        response = post_json(
            self.client,
            "/v1/auth/refresh",
            {"refresh_token": self.refresh_token, "device_id": str(self.device_id)},
        )
        self.assertEqual(response.status_code, 401)

    def test_revokes_the_whole_family_of_the_device(self) -> None:
        """Contrato: "revoca la familia de refresh del dispositivo indicado"."""
        rotated = post_json(
            self.client,
            "/v1/auth/refresh",
            {"refresh_token": self.refresh_token, "device_id": str(self.device_id)},
        ).json()["refresh_token"]

        response = self.logout(refresh_token=rotated)
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(RefreshToken.objects.filter(revoked_at__isnull=True).count(), 0)

    def test_is_idempotent(self) -> None:
        self.assertEqual(self.logout().status_code, 204)
        first_revoked_at = RefreshToken.objects.get().revoked_at
        self.assertEqual(self.logout().status_code, 204)
        self.assertEqual(RefreshToken.objects.get().revoked_at, first_revoked_at)

    def test_an_unknown_token_also_answers_204_without_revoking_anything(self) -> None:
        """Idempotente y sin oráculo: no dice si el token existía."""
        response = self.logout(refresh_token=f"{'z' * 22}.{'y' * 43}")
        self.assertEqual(response.status_code, 204, response.content)
        self.assertIsNone(RefreshToken.objects.get().revoked_at)

    def test_does_not_delete_the_device_nor_any_client_data(self) -> None:
        """ADR-008: la outbox del cliente es responsabilidad del cliente (Paso 13)."""
        self.logout()
        self.assertEqual(Device.objects.filter(pk=self.device_id).count(), 1)
        self.assertEqual(RefreshToken.objects.count(), 1)

    def test_requires_authentication(self) -> None:
        response = self.logout(access_token="")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthenticated")
        self.assertIsNone(RefreshToken.objects.get().revoked_at)

    def test_rejects_a_forged_access_token(self) -> None:
        response = self.logout(access_token="no-es-un-jwt")
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(RefreshToken.objects.get().revoked_at)


@skip_without_db
class LogoutIsolationTests(TestCase):
    """Nadie cierra la sesión de otro (RD-11)."""

    def setUp(self) -> None:
        self.org_a = make_org("A")
        self.org_b = make_org("B")
        make_user(self.org_a, username="a")
        make_user(self.org_b, username="b")

        self.client_a = Client()
        self.client_b = Client()
        self.tokens_a = login(self.client_a, username="a", device=device_info()).json()["tokens"]
        self.tokens_b = login(self.client_b, username="b", device=device_info()).json()["tokens"]

    def test_a_cannot_revoke_the_refresh_token_of_b(self) -> None:
        response = post_json(
            self.client_a,
            "/v1/auth/logout",
            {"refresh_token": self.tokens_b["refresh_token"]},
            HTTP_AUTHORIZATION=f"Bearer {self.tokens_a['access_token']}",
        )
        self.assertEqual(response.status_code, 204, response.content)

        selector_b = self.tokens_b["refresh_token"].split(".", 1)[0]
        self.assertIsNone(RefreshToken.objects.get(selector=selector_b).revoked_at)

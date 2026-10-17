"""Tests de `POST /v1/auth/refresh` (Paso 7, tarea 3).

Alcance de Fase 0 (ver `authn/services.py`): validar `selector.verifier`,
**rotar** —el contrato responde un `TokenPair` completo, así que el refresh
nuevo hay que emitirlo— y reemitir el access token. La **detección de reuso**
con revocación de familia y la ventana de gracia de 30 s (Paso 15) se prueban
en `test_rotation.py`.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from visitacampo.authn import tokens
from visitacampo.authn.models import RefreshToken
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
class RefreshTestsBase(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.device_id = uuid.uuid4()
        self.client = Client()
        body = login(self.client, device=device_info(self.device_id)).json()
        self.refresh_token = body["tokens"]["refresh_token"]
        self.access_token = body["tokens"]["access_token"]

    def refresh(self, token: str | None = None, device_id: uuid.UUID | None = None):
        return post_json(
            self.client,
            "/v1/auth/refresh",
            {
                "refresh_token": self.refresh_token if token is None else token,
                "device_id": str(device_id or self.device_id),
            },
        )


class RefreshSuccessTests(RefreshTestsBase):
    def test_returns_a_token_pair(self) -> None:
        response = self.refresh()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            set(response.json()),
            {
                "access_token",
                "token_type",
                "expires_in",
                "refresh_token",
                "refresh_expires_in",
                "server_time",
            },
        )

    def test_issues_a_usable_access_token_with_the_same_identity(self) -> None:
        body = self.refresh().json()
        claims = tokens.decode_access_token(body["access_token"])
        self.assertIsNotNone(claims)
        assert claims is not None
        self.assertEqual(claims.user_id, self.user.id)
        self.assertEqual(claims.org_id, self.org.id)
        self.assertEqual(claims.device_id, self.device_id)

    def test_rotates_the_refresh_token(self) -> None:
        body = self.refresh().json()
        self.assertNotEqual(body["refresh_token"], self.refresh_token)

        old_selector = self.refresh_token.split(".", 1)[0]
        old = RefreshToken.objects.get(selector=old_selector)
        self.assertIsNotNone(old.rotated_at)

        new_selector = body["refresh_token"].split(".", 1)[0]
        new = RefreshToken.objects.get(selector=new_selector)
        self.assertIsNone(new.rotated_at)
        self.assertIsNone(new.revoked_at)

    def test_the_rotated_token_stays_in_the_same_family(self) -> None:
        """La familia es lo que el Paso 15 revocará al detectar reuso."""
        body = self.refresh().json()
        old = RefreshToken.objects.get(selector=self.refresh_token.split(".", 1)[0])
        new = RefreshToken.objects.get(selector=body["refresh_token"].split(".", 1)[0])
        self.assertEqual(new.family_id, old.family_id)
        self.assertEqual(new.device_id, old.device_id)
        self.assertEqual(new.user_id, old.user_id)
        self.assertEqual(new.org_id, old.org_id)

    def test_the_new_refresh_token_works_in_turn(self) -> None:
        rotated = self.refresh().json()["refresh_token"]
        response = self.refresh(token=rotated)
        self.assertEqual(response.status_code, 200, response.content)

    def test_does_not_require_authentication(self) -> None:
        """Contrato: `/v1/auth/refresh` declara `security: []`."""
        response = post_json(
            Client(),
            "/v1/auth/refresh",
            {"refresh_token": self.refresh_token, "device_id": str(self.device_id)},
        )
        self.assertEqual(response.status_code, 200, response.content)


class RefreshRejectionTests(RefreshTestsBase):
    GENERIC = {"code": "invalid_credentials", "message": "Invalid credentials."}

    def assert_rejected(self, response) -> None:
        self.assertEqual(response.status_code, 401, response.content)
        self.assertEqual(response.json(), self.GENERIC)

    def test_rejects_a_wrong_verifier(self) -> None:
        selector, verifier = self.refresh_token.split(".", 1)
        tampered = f"{selector}.{'A' * len(verifier)}"
        self.assert_rejected(self.refresh(token=tampered))

    def test_rejects_an_unknown_selector(self) -> None:
        _selector, verifier = self.refresh_token.split(".", 1)
        self.assert_rejected(self.refresh(token=f"{'z' * 22}.{verifier}"))

    def test_rejects_a_malformed_token(self) -> None:
        for malformed in ("", "sinpunto", "a.b.c", "."):
            with self.subTest(value=malformed):
                self.assert_rejected(self.refresh(token=malformed))

    def test_rejects_a_device_id_that_does_not_match(self) -> None:
        """El refresh está ligado al dispositivo (ADR-008, "un refresh por dispositivo")."""
        self.assert_rejected(self.refresh(device_id=uuid.uuid4()))

    def test_rejects_a_revoked_token(self) -> None:
        RefreshToken.objects.update(revoked_at=timezone.now())
        self.assert_rejected(self.refresh())

    def test_rejects_an_expired_token(self) -> None:
        RefreshToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_rejected(self.refresh())

    def test_rejects_a_token_that_was_rotated_outside_the_grace_window(self) -> None:
        """Reuso (detalle de la revocación de familia en `test_rotation.py`)."""
        self.refresh()
        RefreshToken.objects.filter(rotated_at__isnull=False).update(
            rotated_at=timezone.now() - timedelta(seconds=31)
        )
        self.assert_rejected(self.refresh())

    def test_rejects_the_token_of_a_user_who_lost_all_memberships(self) -> None:
        """Sin roles no hay contexto que emitir (deny-by-default, ADR-013)."""
        self.user.memberships.all().delete()
        self.assert_rejected(self.refresh())

    def test_a_rejected_refresh_creates_no_new_token(self) -> None:
        before = RefreshToken.objects.count()
        self.refresh(token="basura")
        self.assertEqual(RefreshToken.objects.count(), before)

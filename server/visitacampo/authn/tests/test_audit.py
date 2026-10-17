"""Tests de la traza de auditoría de autenticación (doc 08 §4.4, ISO 27001 A.8.15).

Qué se audita y qué no, y por qué, está en `visitacampo/core/audit.py`. Aquí se
fija el contrato observable:

- Un evento por login correcto, login denegado, conflicto de dispositivo,
  logout y refresh rechazado **con material de token válido**.
- **Ningún** `AuditEvent` por credenciales incorrectas ni por tokens basura:
  esos casos no tienen organización a la que atribuirse, y escribir una fila
  solo cuando el usuario existe abriría un oráculo de enumeración por
  tiempo. Los fallos de contraseña los registra django-axes
  (`AccessFailureLog`).
- La IP nunca se guarda en claro.
"""

from __future__ import annotations

import unittest
import uuid

from axes.models import AccessFailureLog
from django.contrib import admin
from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from visitacampo.core.audit import AuditAction, hash_ip
from visitacampo.core.models import AuditEvent
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import device_info, login, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

CLIENT_IP = "127.0.0.1"  # REMOTE_ADDR del test client de Django


class HashIpTests(SimpleTestCase):
    def test_is_deterministic_so_events_can_be_correlated(self) -> None:
        self.assertEqual(hash_ip("203.0.113.7"), hash_ip("203.0.113.7"))

    def test_never_contains_the_raw_ip(self) -> None:
        digest = hash_ip("203.0.113.7")
        assert digest is not None
        self.assertNotIn("203.0.113.7", digest)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_distinguishes_addresses(self) -> None:
        self.assertNotEqual(hash_ip("203.0.113.7"), hash_ip("203.0.113.8"))

    def test_no_ip_means_no_hash(self) -> None:
        self.assertIsNone(hash_ip(None))
        self.assertIsNone(hash_ip(""))


class AuditAdminTests(SimpleTestCase):
    """El back-office puede leer la traza, nunca escribirla ni borrarla."""

    def test_audit_events_are_visible_but_read_only_in_admin(self) -> None:
        self.assertTrue(admin.site.is_registered(AuditEvent))
        model_admin = admin.site._registry[AuditEvent]
        request = RequestFactory().get("/admin/")
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))


@skip_without_db
class LoginAuditTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()

    def test_a_successful_login_records_who_from_which_device(self) -> None:
        device_id = uuid.uuid4()
        response = login(self.client, device=device_info(device_id))
        self.assertEqual(response.status_code, 200, response.content)

        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.LOGIN)
        self.assertEqual(event.org_id, self.org.pk)
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.target, f"device:{device_id}")
        self.assertEqual(event.metadata["platform"], "android")
        self.assertEqual(event.ip_hash, hash_ip(CLIENT_IP))

    def test_a_wrong_password_writes_no_audit_event_but_axes_logs_it(self) -> None:
        response = login(self.client, password="otra-cosa")
        self.assertEqual(response.status_code, 401)

        self.assertFalse(AuditEvent.objects.exists())
        self.assertTrue(AccessFailureLog.objects.filter(username="agente").exists())

    def test_an_unknown_user_writes_no_audit_event(self) -> None:
        response = login(self.client, username="nadie")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AuditEvent.objects.exists())

    def test_correct_credentials_without_membership_are_recorded_as_denied(self) -> None:
        make_user(self.org, username="sin_rol", with_membership=False)
        response = login(self.client, username="sin_rol")
        self.assertEqual(response.status_code, 401)

        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.LOGIN_DENIED)
        self.assertEqual(event.metadata["reason"], "no_membership")

    def test_presenting_another_users_device_id_is_recorded(self) -> None:
        device_id = uuid.uuid4()
        login(self.client, device=device_info(device_id))
        intruder = make_user(self.org, username="otro")

        response = login(Client(), username="otro", device=device_info(device_id))
        self.assertEqual(response.status_code, 422)

        event = AuditEvent.objects.get(action=AuditAction.DEVICE_CONFLICT)
        self.assertEqual(event.actor_id, intruder.pk)
        self.assertEqual(event.target, f"device:{device_id}")


@skip_without_db
class SessionAuditTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.device_id = uuid.uuid4()
        self.client = Client()
        tokens = login(self.client, device=device_info(self.device_id)).json()["tokens"]
        self.refresh_token = tokens["refresh_token"]
        self.access_token = tokens["access_token"]
        AuditEvent.objects.all().delete()  # solo interesan los eventos posteriores

    def refresh(self, token: str, device_id: uuid.UUID):
        return post_json(
            self.client,
            "/v1/auth/refresh",
            {"refresh_token": token, "device_id": str(device_id)},
        )

    def test_logout_is_recorded_with_the_number_of_revoked_tokens(self) -> None:
        response = post_json(
            self.client,
            "/v1/auth/logout",
            {"refresh_token": self.refresh_token},
            HTTP_AUTHORIZATION=f"Bearer {self.access_token}",
        )
        self.assertEqual(response.status_code, 204)

        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.LOGOUT)
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.target, f"device:{self.device_id}")
        self.assertEqual(event.metadata["revoked"], 1)

    def test_a_successful_refresh_is_not_audited(self) -> None:
        """Ocurre cada ~15 min por dispositivo: sería ruido, no traza."""
        response = self.refresh(self.refresh_token, self.device_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(AuditEvent.objects.exists())

    def test_a_valid_token_presented_from_another_device_is_recorded(self) -> None:
        """Material robado: el token es auténtico pero no es su dispositivo."""
        other_device = uuid.uuid4()
        response = self.refresh(self.refresh_token, other_device)
        self.assertEqual(response.status_code, 401)

        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.REFRESH_REJECTED)
        self.assertEqual(event.metadata["reason"], "wrong_device")
        self.assertEqual(event.metadata["presented_device_id"], str(other_device))
        self.assertEqual(event.target, f"device:{self.device_id}")

    def test_reusing_an_already_rotated_token_is_recorded(self) -> None:
        """La señal que el Paso 15 convertirá en revocación de familia."""
        self.assertEqual(self.refresh(self.refresh_token, self.device_id).status_code, 200)
        AuditEvent.objects.all().delete()

        response = self.refresh(self.refresh_token, self.device_id)
        self.assertEqual(response.status_code, 401)

        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.REFRESH_REJECTED)
        self.assertEqual(event.metadata["reason"], "not_usable")

    def test_a_garbage_token_writes_no_audit_event(self) -> None:
        response = self.refresh("no-es-un-token", self.device_id)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AuditEvent.objects.exists())

    def test_audit_metadata_never_contains_the_refresh_token(self) -> None:
        self.refresh(self.refresh_token, uuid.uuid4())
        event = AuditEvent.objects.get()
        self.assertNotIn(self.refresh_token, str(event.metadata))
        self.assertNotIn(self.refresh_token.split(".")[-1], str(event.metadata))

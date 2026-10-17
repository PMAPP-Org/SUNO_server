"""Paso 15: rotación con detección de reuso y ventana de gracia (ADR-008, RD-10).

Contrato:

- Primer uso de un refresh → rota y emite uno nuevo en la misma familia.
- El mismo token otra vez **dentro de la ventana de gracia** (30 s) desde el
  mismo dispositivo → reintento legítimo (p. ej. se perdió la respuesta de
  red): se emite otro par en la misma familia y se audita.
- El mismo token **fuera de la ventana** → reuso de un token robado: se revoca
  **toda la familia** (también los tokens ya emitidos a partir de él), se
  audita y se responde el 401 genérico.
- La decisión se toma con la fila bloqueada (`SELECT … FOR UPDATE`): dos
  peticiones simultáneas no pueden decidir las dos con datos viejos.
"""

from __future__ import annotations

import threading
import unittest
import uuid
from datetime import timedelta

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from visitacampo.authn.models import RefreshToken
from visitacampo.authn.services import REFRESH_REUSE_GRACE
from visitacampo.core.audit import AuditAction
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


class _RefreshMixin:
    client: Client
    device_id: uuid.UUID

    def refresh(self, token: str):
        return post_json(
            self.client,
            "/v1/auth/refresh",
            {"refresh_token": token, "device_id": str(self.device_id)},
        )


def _age_rotation(seconds: float) -> None:
    """Simula que la rotación ocurrió hace `seconds` segundos."""
    RefreshToken.objects.filter(rotated_at__isnull=False).update(
        rotated_at=timezone.now() - timedelta(seconds=seconds)
    )


@skip_without_db
class ReuseDetectionTests(_RefreshMixin, TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.device_id = uuid.uuid4()
        self.client = Client()
        body = login(self.client, device=device_info(self.device_id)).json()
        self.original = body["tokens"]["refresh_token"]
        self.family = RefreshToken.objects.get().family_id

    def test_the_grace_window_is_thirty_seconds(self) -> None:
        self.assertEqual(REFRESH_REUSE_GRACE, timedelta(seconds=30))

    def test_a_replay_inside_the_grace_window_is_a_legitimate_retry(self) -> None:
        first = self.refresh(self.original)
        replay = self.refresh(self.original)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertNotEqual(first.json()["refresh_token"], replay.json()["refresh_token"])
        self.assertEqual(
            set(RefreshToken.objects.values_list("family_id", flat=True)), {self.family}
        )
        self.assertFalse(RefreshToken.objects.filter(revoked_at__isnull=False).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.REFRESH_GRACE_REPLAY).exists())

    def test_reuse_after_the_window_revokes_the_whole_family(self) -> None:
        child = self.refresh(self.original).json()["refresh_token"]
        _age_rotation(REFRESH_REUSE_GRACE.total_seconds() + 1)

        response = self.refresh(self.original)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credentials")
        family = RefreshToken.objects.filter(family_id=self.family)
        self.assertEqual(family.count(), 2)
        self.assertFalse(
            family.filter(revoked_at__isnull=True).exists(), "toda la familia revocada"
        )
        # El token que el atacante (o la víctima) obtuvo a partir del robado
        # también muere: re-login obligatorio.
        self.assertEqual(self.refresh(child).status_code, 401)

    def test_reuse_is_audited_with_the_number_of_revoked_tokens(self) -> None:
        self.refresh(self.original)
        _age_rotation(REFRESH_REUSE_GRACE.total_seconds() + 1)
        self.refresh(self.original)

        event = AuditEvent.objects.get(action=AuditAction.REFRESH_REUSE_DETECTED)
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.metadata["family_id"], str(self.family))
        # El original (rotado pero sin revocar) y su hijo vivo: toda la familia.
        self.assertEqual(event.metadata["revoked"], 2)

    def test_another_family_of_the_same_user_is_not_touched(self) -> None:
        """Revocar la familia, no todas las sesiones del usuario: otro login sigue vivo."""
        other_device = uuid.uuid4()
        other = login(Client(), device=device_info(other_device)).json()["tokens"]["refresh_token"]
        self.refresh(self.original)
        _age_rotation(REFRESH_REUSE_GRACE.total_seconds() + 1)
        self.refresh(self.original)

        response = post_json(
            Client(),
            "/v1/auth/refresh",
            {"refresh_token": other, "device_id": str(other_device)},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_revoked_token_is_rejected_without_new_revocations(self) -> None:
        RefreshToken.objects.update(revoked_at=timezone.now())
        response = self.refresh(self.original)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(AuditEvent.objects.filter(action=AuditAction.REFRESH_REUSE_DETECTED).exists())


@skip_without_db
class ConcurrentRefreshTests(_RefreshMixin, TransactionTestCase):
    """RD-10 (H-15): dos refrescos simultáneos del mismo token."""

    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.device_id = uuid.uuid4()
        self.client = Client()
        body = login(self.client, device=device_info(self.device_id)).json()
        self.original = body["tokens"]["refresh_token"]

    def _concurrent_refreshes(self, n: int) -> list[int]:
        barrier = threading.Barrier(n)
        codes: list[int] = []
        lock = threading.Lock()

        def run() -> None:
            try:
                barrier.wait()
                code = self.refresh(self.original).status_code
                with lock:
                    codes.append(code)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return codes

    def test_simultaneous_refreshes_inside_the_window_all_succeed(self) -> None:
        codes = self._concurrent_refreshes(2)

        self.assertEqual(sorted(codes), [200, 200])
        self.assertFalse(RefreshToken.objects.filter(revoked_at__isnull=False).exists())
        self.assertEqual(RefreshToken.objects.filter(rotated_at__isnull=False).count(), 1)

    def test_a_late_third_use_is_treated_as_reuse(self) -> None:
        self._concurrent_refreshes(2)
        _age_rotation(REFRESH_REUSE_GRACE.total_seconds() + 1)

        self.assertEqual(self.refresh(self.original).status_code, 401)
        self.assertFalse(RefreshToken.objects.filter(revoked_at__isnull=True).exists())

"""`POST /v1/consents`: prueba de la autorización del titular (Ley 1581, art. 9).

Por HTTP, con un login real (que registra el dispositivo): lo que se guarda es
exactamente lo que el titular aceptó, desde su propio teléfono, y nada de otra
persona u organización puede colarse en la prueba.
"""

from __future__ import annotations

import typing
import unittest
import uuid

from django.test import Client, TestCase

from visitacampo.authn.tests.factories import device_info, login, make_org, make_user, post_json
from visitacampo.backoffice.personal_data import export_personal_data
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import AuditEvent, PrivacyConsent, PrivacyPurpose, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.privacy.schemas import Purpose

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

URL = "/v1/consents"


def body(device_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_id": str(device_id),
        "notice_version": "2026-10-18",
        "purposes": ["photos", "account", "location"],
        "accepted_at": "2026-10-18T09:30:00.000Z",
    }
    payload.update(overrides)
    return payload


class PurposeContractTests(unittest.TestCase):
    def test_the_schema_and_the_model_list_the_same_purposes(self) -> None:
        self.assertEqual(set(typing.get_args(Purpose)), set(PrivacyPurpose.values))


@skip_without_db
class ConsentTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org, username="agente")
        self.client = Client()
        self.device_id = uuid.uuid4()
        tokens = login(self.client, device=device_info(self.device_id)).json()["tokens"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}

    def post(self, payload: dict[str, object], **auth: str):
        return post_json(self.client, URL, payload, **(auth or self.auth))

    def test_records_what_was_accepted_and_audits_it(self) -> None:
        response = self.post(body(self.device_id))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(set(response.json()), {"id", "recorded_at"})

        consent = PrivacyConsent.objects.get(pk=response.json()["id"])
        self.assertEqual(consent.user_id, self.user.pk)
        self.assertEqual(consent.org_id, self.org.pk)
        self.assertEqual(consent.device_id, self.device_id)
        self.assertEqual(consent.notice_version, "2026-10-18")
        self.assertEqual(consent.purposes, ["account", "location", "photos"], "orden estable")
        self.assertTrue(
            AuditEvent.objects.filter(action=AuditAction.PRIVACY_CONSENT_RECORDED).exists()
        )

    def test_every_acceptance_is_a_new_row(self) -> None:
        self.post(body(self.device_id))
        self.post(body(self.device_id, notice_version="2027-01-15"))
        versions = list(
            PrivacyConsent.objects.order_by("recorded_at").values_list("notice_version", flat=True)
        )
        self.assertEqual(versions, ["2026-10-18", "2027-01-15"])

    def test_someone_elses_device_is_a_404(self) -> None:
        colleague = make_user(self.org, username="colega")
        other_device = uuid.uuid4()
        login(Client(), username=colleague.username, device=device_info(other_device))
        for device in (other_device, uuid.uuid4()):
            with self.subTest(device=device):
                self.assertEqual(self.post(body(device)).status_code, 404)
        self.assertFalse(PrivacyConsent.objects.exists())

    def test_invalid_bodies_are_rejected(self) -> None:
        for label, payload in (
            ("sin finalidades", body(self.device_id, purposes=[])),
            ("finalidad desconocida", body(self.device_id, purposes=["marketing"])),
            ("finalidad repetida", body(self.device_id, purposes=["photos", "photos"])),
            ("campo desconocido", {**body(self.device_id), "extra": 1}),
            ("versión vacía", body(self.device_id, notice_version="")),
            ("versión con espacios", body(self.device_id, notice_version="v 1")),
            ("fecha sin zona horaria", body(self.device_id, accepted_at="2026-10-18T09:30:00")),
        ):
            with self.subTest(case=label):
                self.assertEqual(self.post(payload).status_code, 422)
        self.assertFalse(PrivacyConsent.objects.exists())

    def test_an_extreme_but_valid_offset_is_stored_in_utc(self) -> None:
        """Schemathesis encontró un 500: `timestamptz` no admite un desfase de
        -18:47 aunque RFC 3339 sí. Se normaliza a UTC antes de guardar."""
        response = self.post(body(self.device_id, accepted_at="1655-02-26T04:07:58-18:47"))
        self.assertEqual(response.status_code, 201, response.content)
        consent = PrivacyConsent.objects.get()
        self.assertEqual(consent.accepted_at.isoformat(), "1655-02-26T22:54:58+00:00")

    def test_requires_authentication(self) -> None:
        response = post_json(Client(), URL, body(self.device_id))
        self.assertEqual(response.status_code, 401)

    def test_appears_in_the_personal_data_export(self) -> None:
        self.post(body(self.device_id))
        admin = make_user(self.org, username="admin", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=self.org.pk))
        exported = export_personal_data(ctx, self.user)["autorizaciones"]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["finalidades"], ["account", "location", "photos"])

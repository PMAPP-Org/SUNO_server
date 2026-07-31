"""Tests de `GET /v1/server/info` (Paso 7, tarea 6).

Es el endpoint con el que el cliente **negocia compatibilidad antes de
sincronizar** (doc 08 §6), así que los tests fijan exactamente los campos que
el Paso 13 va a leer y el hecho de que el contrato lo declara autenticado (no
lleva `security: []`, a diferencia de login/refresh).
"""

from __future__ import annotations

import unittest
import uuid

from django.test import Client, TestCase

from visitacampo.authn.tests.factories import device_info, login, make_org, make_user
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ServerInfoTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()
        tokens = login(self.client, device=device_info(uuid.uuid4())).json()["tokens"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}

    def test_returns_the_contract_shape(self) -> None:
        response = self.client.get("/v1/server/info", **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "api_version",
                "schema_version",
                "min_supported_schema_version",
                "server_time",
                "capabilities",
            },
        )
        self.assertEqual(body["api_version"], "v1")
        self.assertTrue(body["server_time"].endswith("Z"))

    def test_schema_version_range_is_coherent(self) -> None:
        body = self.client.get("/v1/server/info", **self.auth).json()
        self.assertGreaterEqual(body["schema_version"], 1)
        self.assertLessEqual(body["min_supported_schema_version"], body["schema_version"])

    def test_capabilities_carry_the_limits_the_client_needs(self) -> None:
        capabilities = self.client.get("/v1/server/info", **self.auth).json()["capabilities"]
        self.assertEqual(
            set(capabilities),
            {
                "sync_push_max_ops",
                "sync_push_max_bytes",
                "sync_pull_max_limit",
                "attachment_max_bytes",
                "attachment_allowed_mime",
            },
        )
        self.assertEqual(capabilities["sync_push_max_ops"], 500)
        self.assertEqual(capabilities["sync_push_max_bytes"], 5 * 1024 * 1024)
        self.assertIn("image/jpeg", capabilities["attachment_allowed_mime"])

    def test_requires_authentication(self) -> None:
        response = self.client.get("/v1/server/info")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthenticated")

    def test_rejects_a_forged_token(self) -> None:
        response = self.client.get("/v1/server/info", HTTP_AUTHORIZATION="Bearer no-es-un-jwt")
        self.assertEqual(response.status_code, 401)

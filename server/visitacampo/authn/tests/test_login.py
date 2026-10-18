"""Tests de `POST /v1/auth/login` (Paso 7, tareas 2 y la verificación de H-14).

Incluye la **regresión de H-14/RD-09**: `PlaintextRefreshTokenRegressionTests`
lee la tabla `refresh_token` con SQL crudo —no con el ORM, no con un atributo
de Python que pudiera calcularse al vuelo— y comprueba que el `verifier`
devuelto en la respuesta no aparece en ninguna columna de ninguna fila.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid

from django.db import connection
from django.test import Client, TestCase

from visitacampo.authn import tokens
from visitacampo.core.models import Device, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import PASSWORD, device_info, login, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LoginSuccessTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()

    def test_a_multipart_body_without_boundary_is_a_400_not_a_500(self) -> None:
        """Schemathesis (issue #6): django-axes lee `request.POST` y Django
        intentaba parsear el cuerpo como multipart; salía un 500."""
        response = self.client.post(
            "/v1/auth/login",
            data=json.dumps({"username": "agente", "password": "x", "device_info": device_info()}),
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_a_nul_character_is_a_422_not_a_500(self) -> None:
        """Schemathesis: PostgreSQL no guarda `U+0000`; el intento fallido que
        registra django-axes salía como `DataError` → 500 (`NoNulStr`)."""
        for body in (
            {"username": "a\u0000b", "password": "x", "device_info": device_info()},
            {
                "username": "agente",
                "password": "x",
                "device_info": device_info(model="Pixel\u0000"),
            },
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    "/v1/auth/login", data=json.dumps(body), content_type="application/json"
                )
                self.assertEqual(response.status_code, 422, response.content)
                self.assertEqual(response.json()["code"], "validation_error")

    def test_returns_the_contract_shape(self) -> None:
        response = login(self.client)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"tokens", "user"})
        self.assertEqual(
            set(body["tokens"]),
            {
                "access_token",
                "token_type",
                "expires_in",
                "refresh_token",
                "refresh_expires_in",
                "server_time",
            },
        )
        self.assertEqual(
            set(body["user"]),
            {"id", "username", "display_name", "org_id", "roles"},
        )
        self.assertEqual(body["tokens"]["token_type"], "Bearer")
        self.assertEqual(body["tokens"]["expires_in"], 900)
        self.assertTrue(body["tokens"]["server_time"].endswith("Z"))

    def test_returns_the_user_profile_of_the_contract(self) -> None:
        body = login(self.client).json()["user"]
        self.assertEqual(body["id"], str(self.user.id))
        self.assertEqual(body["username"], "agente")
        self.assertEqual(body["org_id"], str(self.org.id))
        self.assertEqual(body["roles"], [str(Role.FIELD_AGENT)])

    def test_access_token_carries_the_contract_claims(self) -> None:
        device_uuid = uuid.uuid4()
        body = login(self.client, device=device_info(device_uuid)).json()
        claims = tokens.decode_access_token(body["tokens"]["access_token"])
        self.assertIsNotNone(claims)
        assert claims is not None
        self.assertEqual(claims.user_id, self.user.id)
        self.assertEqual(claims.org_id, self.org.id)
        self.assertEqual(claims.device_id, device_uuid)
        self.assertEqual(claims.roles, (str(Role.FIELD_AGENT),))

    def test_registers_the_device(self) -> None:
        device_uuid = uuid.uuid4()
        login(self.client, device=device_info(device_uuid, display_name="Tablet 9"))
        device = Device.objects.get(pk=device_uuid)
        self.assertEqual(device.owner_id, self.user.id)
        self.assertEqual(device.org_id, self.org.id)
        self.assertEqual(device.platform, "android")
        self.assertEqual(device.display_name, "Tablet 9")
        self.assertEqual(device.app_version, "0.1.0")

    def test_second_login_updates_the_device_instead_of_duplicating_it(self) -> None:
        device_uuid = uuid.uuid4()
        login(self.client, device=device_info(device_uuid, app_version="0.1.0"))
        login(self.client, device=device_info(device_uuid, app_version="0.2.0"))
        self.assertEqual(Device.objects.filter(pk=device_uuid).count(), 1)
        self.assertEqual(Device.objects.get(pk=device_uuid).app_version, "0.2.0")

    def test_each_login_issues_a_different_refresh_token(self) -> None:
        first = login(self.client).json()["tokens"]["refresh_token"]
        second = login(self.client).json()["tokens"]["refresh_token"]
        self.assertNotEqual(first, second)

    def test_does_not_require_authentication(self) -> None:
        """Contrato: `/v1/auth/login` declara `security: []`."""
        response = login(Client(), device=device_info())
        self.assertEqual(response.status_code, 200, response.content)


@skip_without_db
class PlaintextRefreshTokenRegressionTests(TestCase):
    """**H-14 / RD-09**: la BD nunca contiene un token de refresco utilizable.

    La comprobación se hace con `connection.cursor()` y `SELECT *` a propósito:
    leer con el ORM comprobaría el modelo de Python (donde un `verifier` podría
    ser una propiedad calculada, o el atributo podría no existir siquiera),
    mientras que lo que el hallazgo describe es **lo que hay escrito en la
    tabla**. Aquí se lee la fila entera, columna por columna, tal y como la
    vería quien robase un backup.
    """

    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.client = Client()

    def _all_rows(self) -> tuple[list[str], list[tuple[object, ...]]]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM refresh_token")
            columns = [column[0] for column in cursor.description]
            return columns, cursor.fetchall()

    def test_no_column_of_any_row_contains_the_verifier(self) -> None:
        refresh_token = login(self.client).json()["tokens"]["refresh_token"]
        selector, verifier = refresh_token.split(".", 1)

        columns, rows = self._all_rows()
        self.assertEqual(len(rows), 1, "el login debe haber creado exactamente una fila")

        for row in rows:
            for column, value in zip(columns, row, strict=True):
                text = "" if value is None else str(value)
                with self.subTest(column=column):
                    self.assertNotEqual(text, verifier)
                    self.assertNotIn(verifier, text)
                    self.assertNotEqual(text, refresh_token)
                    self.assertNotIn(refresh_token, text)

    def test_the_stored_value_is_the_sha256_of_the_verifier(self) -> None:
        refresh_token = login(self.client).json()["tokens"]["refresh_token"]
        _selector, verifier = refresh_token.split(".", 1)
        expected = hashlib.sha256(verifier.encode("ascii")).hexdigest()

        with connection.cursor() as cursor:
            cursor.execute("SELECT verifier_hash FROM refresh_token")
            stored = cursor.fetchone()[0]

        self.assertNotEqual(stored, verifier)
        self.assertEqual(stored, expected)

    def test_the_selector_is_stored_in_clear_because_it_is_not_a_secret(self) -> None:
        """El `selector` **sí** se guarda: es la clave de búsqueda (ADR-008)."""
        refresh_token = login(self.client).json()["tokens"]["refresh_token"]
        selector, _verifier = refresh_token.split(".", 1)

        with connection.cursor() as cursor:
            cursor.execute("SELECT selector FROM refresh_token")
            self.assertEqual(cursor.fetchone()[0], selector)

    def test_the_verifier_survives_no_rotation_either(self) -> None:
        """Tras un refresh, tampoco el token nuevo queda en claro."""
        first = login(self.client).json()["tokens"]["refresh_token"]
        rotated = post_json(
            self.client,
            "/v1/auth/refresh",
            {
                "refresh_token": first,
                "device_id": str(Device.objects.get().pk),
            },
        ).json()["refresh_token"]

        columns, rows = self._all_rows()
        for row in rows:
            for column, value in zip(columns, row, strict=True):
                text = "" if value is None else str(value)
                with self.subTest(column=column):
                    self.assertNotIn(rotated.split(".", 1)[1], text)
                    self.assertNotIn(first.split(".", 1)[1], text)


@skip_without_db
class LoginFailureTests(TestCase):
    """Credenciales inválidas: misma respuesta, sin revelar si el usuario existe."""

    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)
        self.inactive = make_user(self.org, username="baja", is_active=False)
        self.sin_membresia = make_user(self.org, username="huerfano", with_membership=False)
        self.client = Client()

    def _generic_body(self) -> dict[str, object]:
        return {"code": "invalid_credentials", "message": "Invalid credentials."}

    def test_wrong_password_is_rejected_generically(self) -> None:
        response = login(self.client, password="otra-cosa")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), self._generic_body())

    def test_unknown_user_gets_exactly_the_same_response(self) -> None:
        """Sin enumeración de usuarios: byte a byte la misma respuesta."""
        wrong_password = login(self.client, username="agente", password="otra-cosa")
        unknown_user = login(self.client, username="no-existe", password=PASSWORD)
        self.assertEqual(unknown_user.status_code, wrong_password.status_code)
        self.assertEqual(unknown_user.content, wrong_password.content)

    def test_inactive_user_gets_the_same_response(self) -> None:
        response = login(self.client, username="baja")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), self._generic_body())

    def test_user_without_membership_gets_the_same_response(self) -> None:
        """Deny-by-default (ADR-013): sin rol resuelto no se emite token."""
        response = login(self.client, username="huerfano")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), self._generic_body())

    def test_a_failed_login_issues_no_token(self) -> None:
        login(self.client, password="otra-cosa")
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM refresh_token")
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertEqual(Device.objects.count(), 0)

    def test_a_malformed_body_is_rejected_with_the_uniform_error(self) -> None:
        response = post_json(self.client, "/v1/auth/login", {"username": "agente"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")


@skip_without_db
class DeviceIsolationTests(TestCase):
    """Un `device_id` ajeno no se roba ni cruza de organización (RD-11)."""

    def setUp(self) -> None:
        self.org_a = make_org("A")
        self.org_b = make_org("B")
        self.user_a = make_user(self.org_a, username="a")
        self.user_b = make_user(self.org_b, username="b")
        self.client = Client()

    def test_a_device_id_registered_in_another_org_is_refused(self) -> None:
        device_uuid = uuid.uuid4()
        first = login(self.client, username="a", device=device_info(device_uuid))
        self.assertEqual(first.status_code, 200, first.content)

        second = login(self.client, username="b", device=device_info(device_uuid))
        self.assertEqual(second.status_code, 422, second.content)

        device = Device.objects.get(pk=device_uuid)
        self.assertEqual(device.org_id, self.org_a.id)
        self.assertEqual(device.owner_id, self.user_a.id)

    def test_a_device_id_of_another_user_in_the_same_org_is_refused(self) -> None:
        device_uuid = uuid.uuid4()
        otro = make_user(self.org_a, username="a2")
        login(self.client, username="a", device=device_info(device_uuid))

        response = login(self.client, username="a2", device=device_info(device_uuid))
        self.assertEqual(response.status_code, 422, response.content)
        self.assertNotEqual(Device.objects.get(pk=device_uuid).owner_id, otro.id)

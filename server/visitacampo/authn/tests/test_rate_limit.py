"""Paso 15: rate limiting, tiempos uniformes y logs sin secretos.

- El limitador común (`core/ratelimit.py`) en login, refresh y presign:
  `429` con `code: rate_limited` y `Retry-After`, como declara el contrato.
- El bloqueo por cuenta tras fallos de contraseña lo prueba
  `test_axes_lockout.py` (django-axes); aquí, el volumen por IP/token/usuario.
- Tiempos uniformes: el plan pide comparar tiempos entre "usuario inexistente"
  y "contraseña incorrecta". Medir con un cronómetro en CI es inestable; se
  comprueba la causa en vez del síntoma: los dos caminos hacen **exactamente
  un** cálculo de hash de contraseña.
- Ningún log del flujo de autenticación contiene la contraseña ni el token.
"""

from __future__ import annotations

import logging
import unittest
import uuid
from datetime import timedelta

from django.contrib.auth.hashers import MD5PasswordHasher
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from visitacampo.core import ratelimit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import PASSWORD, device_info, login, make_org, make_user, post_json

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

LIMITS = {
    "login:ip": (2, 60),
    "refresh:ip": (1000, 60),
    "refresh:token": (2, 60),
    "presign:user": (1, 60),
    "presign:ip": (1000, 60),
}


@skip_without_db
@override_settings(RATE_LIMITS=LIMITS)
class LimiterTests(TestCase):
    def test_allows_up_to_the_limit_then_rejects_with_retry_after(self) -> None:
        now = timezone.now().replace(second=10, microsecond=0)
        ratelimit.hit("login:ip", "203.0.113.7", now=now)
        ratelimit.hit("login:ip", "203.0.113.7", now=now)
        with self.assertRaises(ratelimit.RateLimited) as caught:
            ratelimit.hit("login:ip", "203.0.113.7", now=now)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, 50)  # hasta el final del minuto

    def test_identities_and_scopes_are_counted_separately(self) -> None:
        for _ in range(2):
            ratelimit.hit("login:ip", "203.0.113.7")
        ratelimit.hit("login:ip", "203.0.113.8")  # otra IP: no le afecta
        ratelimit.hit("refresh:token", "203.0.113.7")  # otro ámbito: tampoco

    def test_a_new_window_starts_from_zero(self) -> None:
        now = timezone.now()
        for _ in range(2):
            ratelimit.hit("login:ip", "203.0.113.7", now=now)
        ratelimit.hit("login:ip", "203.0.113.7", now=now + timedelta(seconds=61))

    def test_an_unknown_identity_is_not_limited(self) -> None:
        for _ in range(5):
            ratelimit.hit("login:ip", None)

    def test_the_identity_is_never_stored_in_clear(self) -> None:
        ratelimit.hit("login:ip", "203.0.113.7")
        with connection.cursor() as cursor:
            cursor.execute("SELECT key FROM rate_limit_bucket")
            keys = [row[0] for row in cursor.fetchall()]
        self.assertEqual(len(keys), 1)
        self.assertNotIn("203.0.113.7", keys[0])
        self.assertTrue(keys[0].startswith("login:ip:"))

    def test_expired_windows_are_purged(self) -> None:
        ratelimit.hit("login:ip", "203.0.113.7", now=timezone.now() - timedelta(days=2))
        ratelimit.hit("login:ip", "203.0.113.7")
        self.assertEqual(ratelimit.purge_expired(older_than=timezone.now() - timedelta(days=1)), 1)


@skip_without_db
@override_settings(RATE_LIMITS=LIMITS)
class EndpointLimitTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.user = make_user(self.org)

    def assert_rate_limited(self, response) -> None:
        self.assertEqual(response.status_code, 429, response.content)
        self.assertEqual(response.json()["code"], "rate_limited")
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_login_is_limited_per_ip(self) -> None:
        client = Client()
        login(client, password="otra-cosa")
        login(client, password="otra-cosa")
        self.assert_rate_limited(login(client))

    def test_refresh_is_limited_per_token(self) -> None:
        client = Client()
        device_id = uuid.uuid4()
        token = login(client, device=device_info(device_id)).json()["tokens"]["refresh_token"]
        body = {"refresh_token": token, "device_id": str(device_id)}
        post_json(client, "/v1/auth/refresh", body)
        post_json(client, "/v1/auth/refresh", body)
        self.assert_rate_limited(post_json(client, "/v1/auth/refresh", body))

    def test_presign_is_limited_per_user(self) -> None:
        client = Client()
        access = login(client).json()["tokens"]["access_token"]
        body = {
            "sha256": "a" * 64,
            "size": 10,
            "mime": "image/jpeg",
            "visit_id": str(uuid.uuid4()),
        }
        auth = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
        post_json(client, "/v1/attachments/presign", body, **auth)
        self.assert_rate_limited(post_json(client, "/v1/attachments/presign", body, **auth))


class _CountingHasher(MD5PasswordHasher):
    """MD5 solo por velocidad en el test; cuenta cada cálculo de hash."""

    algorithm = "counting_md5"
    calls = 0

    def encode(self, password, salt):
        type(self).calls += 1
        return super().encode(password, salt).replace("md5$", "counting_md5$", 1)

    def verify(self, password, encoded):
        type(self).calls += 1
        return super().verify(password, encoded.replace("counting_md5$", "md5$", 1))


@skip_without_db
@override_settings(PASSWORD_HASHERS=[f"{__name__}._CountingHasher"])
class UniformLoginWorkTests(TestCase):
    """Sin oráculo de enumeración por tiempo (ADR-008): el mismo trabajo en ambos caminos."""

    def setUp(self) -> None:
        self.org = make_org()
        make_user(self.org)
        _CountingHasher.calls = 0

    def hashes_for(self, username: str, password: str) -> int:
        _CountingHasher.calls = 0
        response = login(Client(), username=username, password=password)
        self.assertEqual(response.status_code, 401)
        return _CountingHasher.calls

    def test_unknown_user_and_wrong_password_compute_one_hash_each(self) -> None:
        unknown = self.hashes_for("nadie", PASSWORD)
        wrong = self.hashes_for("agente", "otra-cosa")
        self.assertEqual(unknown, 1)
        self.assertEqual(wrong, 1)


@skip_without_db
class AuthLogsTests(TestCase):
    """RD-12 en el módulo de autenticación: nada utilizable en los logs."""

    def test_no_log_contains_the_password_or_the_tokens(self) -> None:
        org = make_org()
        make_user(org)
        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(self.format(record))

        handler = _Capture(level=logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            client = Client()
            device_id = uuid.uuid4()
            login(client, password="otra-cosa")
            tokens = login(client, device=device_info(device_id)).json()["tokens"]
            body = {"refresh_token": tokens["refresh_token"], "device_id": str(device_id)}
            post_json(client, "/v1/auth/refresh", body)
            post_json(client, "/v1/auth/refresh", {**body, "refresh_token": "x.y"})
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)

        text = "\n".join(records)
        verifier = tokens["refresh_token"].split(".")[-1]
        for secret in (PASSWORD, "otra-cosa", verifier, tokens["access_token"]):
            self.assertNotIn(secret, text)

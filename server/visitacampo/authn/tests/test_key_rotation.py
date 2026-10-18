"""Rotación de la llave JWT con dos claves vivas (ADR-008, Paso 15).

El procedimiento (`docs/operacion/incidentes.md`, "Rotar la llave JWT"):

1. Se genera una llave nueva; la actual pasa a `JWT_PREVIOUS_KEY` (basta su
   clave pública) y la nueva a `JWT_SIGNING_KEY`.
2. Los access tokens ya emitidos siguen valiendo hasta que caducan: los
   teléfonos no notan el cambio.
3. Pasados 15 minutos se vacía `JWT_PREVIOUS_KEY`: los tokens de la llave
   anterior dejan de valer.

Estos tests recorren ese procedimiento y fijan lo que no puede romperse: la
anterior **nunca firma**, el `kid` no decide nada por sí solo, y una llave
anterior mal escrita impide arrancar en vez de ignorarse.
"""

from __future__ import annotations

import base64
import tempfile
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from visitacampo.authn import keys
from visitacampo.authn.tokens import decode_access_token, issue_access_token


def _issue() -> str:
    return issue_access_token(
        user_id=uuid.uuid4(), org_id=uuid.uuid4(), device_id=None, roles=["field_agent"]
    ).token


def _public_pem(private_pem: str) -> str:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


class KeyRotationTests(SimpleTestCase):
    def setUp(self) -> None:
        keys.reset_key_cache()
        self.addCleanup(keys.reset_key_cache)
        self.old = keys.signing_key_to_pem(keys.generate_signing_key())
        self.new = keys.signing_key_to_pem(keys.generate_signing_key())

    def _config(self, signing: str, previous: str = "", previous_file: str = ""):
        keys.reset_key_cache()
        return override_settings(
            JWT_SIGNING_KEY=signing,
            JWT_SIGNING_KEY_FILE="",
            JWT_PREVIOUS_KEY=previous,
            JWT_PREVIOUS_KEY_FILE=previous_file,
        )

    def test_the_whole_procedure(self) -> None:
        with self._config(self.old):
            old_token = _issue()
            self.assertIsNotNone(decode_access_token(old_token))

        # 1-2. Rotación: la nueva firma, la anterior (solo la pública) verifica.
        with self._config(self.new, previous=_public_pem(self.old)):
            self.assertIsNotNone(decode_access_token(old_token), "el token vivo sigue valiendo")
            new_token = _issue()
            self.assertIsNotNone(decode_access_token(new_token))

        # 3. Retirada de la anterior: sus tokens dejan de valer, los nuevos no.
        with self._config(self.new):
            self.assertIsNone(decode_access_token(old_token))
            self.assertIsNotNone(decode_access_token(new_token))

    def test_the_previous_key_never_signs(self) -> None:
        with self._config(self.new, previous=self.old):
            token = _issue()
        with self._config(self.old):
            self.assertIsNone(decode_access_token(token), "lo firmó la anterior")

    def test_every_token_names_its_key(self) -> None:
        with self._config(self.new, previous=self.old):
            kid = jwt.get_unverified_header(_issue())["kid"]
            current = keys.verification_keys()[0]
        self.assertEqual(kid, keys.key_id(current))
        self.assertRegex(kid, r"^[0-9a-f]{16}$")

    def test_a_forged_kid_does_not_bypass_the_signature(self) -> None:
        """Un token firmado con una llave ajena que dice ser de la actual."""
        stranger = keys.generate_signing_key()
        with self._config(self.new, previous=_public_pem(self.old)):
            kid = keys.key_id(keys.verification_keys()[0])
            forged = jwt.encode(
                {
                    "sub": str(uuid.uuid4()),
                    "org": str(uuid.uuid4()),
                    "roles": [],
                    "jti": "x",
                    "exp": 4_102_444_800,
                },
                stranger,
                algorithm="EdDSA",
                headers={"kid": kid},
            )
            self.assertIsNone(decode_access_token(forged))

    def test_tokens_without_kid_still_verify_during_the_transition(self) -> None:
        """Los emitidos antes de esta versión no llevan `kid`."""
        legacy = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "org": str(uuid.uuid4()),
                "roles": [],
                "jti": "x",
                "exp": 4_102_444_800,
            },
            self.old,
            algorithm="EdDSA",
        )
        with self._config(self.new, previous=_public_pem(self.old)):
            self.assertIsNotNone(decode_access_token(legacy))

    def test_previous_key_from_a_file_or_base64(self) -> None:
        encoded = base64.b64encode(_public_pem(self.old).encode()).decode()
        with self._config(self.new, previous=encoded):
            self.assertEqual(len(keys.verification_keys()), 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "previous.pem"
            path.write_text(_public_pem(self.old), encoding="utf-8")
            with self._config(self.new, previous_file=str(path)):
                self.assertEqual(len(keys.verification_keys()), 2)

    def test_the_same_key_as_previous_is_not_counted_twice(self) -> None:
        with self._config(self.new, previous=self.new):
            self.assertEqual(len(keys.verification_keys()), 1)

    def test_an_unreadable_previous_key_stops_the_server(self) -> None:
        with self._config(self.new, previous="no-es-una-llave"):
            with self.assertRaises(ImproperlyConfigured):
                keys.verification_keys()

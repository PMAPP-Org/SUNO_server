"""Tests de las primitivas criptográficas de `authn.tokens` (sin BD).

Se prueban aquí, aisladas del HTTP y de la base de datos, las dos piezas de
las que depende toda la seguridad del Paso 7:

- el **secreto de refresco** `selector.verifier` y su hash (RD-09/H-14);
- el **access token JWT Ed25519** y, sobre todo, lo que debe **rechazar**.

`SimpleTestCase` a propósito: nada de esto toca PostgreSQL, así que corre
igual en una máquina sin base de datos (mismo criterio que
`health/tests/test_health.py`).
"""

from __future__ import annotations

import hashlib
import uuid

import jwt
from django.test import SimpleTestCase, override_settings

from visitacampo.authn import tokens
from visitacampo.authn.keys import generate_signing_key, signing_key_to_pem


class RefreshSecretTests(SimpleTestCase):
    def test_has_selector_and_verifier_separated_by_a_dot(self) -> None:
        secret = tokens.generate_refresh_secret()
        self.assertEqual(f"{secret.selector}.{secret.verifier}", secret.token)
        self.assertEqual(secret.token.count("."), 1)

    def test_selector_and_verifier_are_url_safe(self) -> None:
        secret = tokens.generate_refresh_secret()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        self.assertTrue(set(secret.selector) <= allowed, secret.selector)
        self.assertTrue(set(secret.verifier) <= allowed, secret.verifier)

    def test_verifier_carries_at_least_256_bits(self) -> None:
        """ADR-008: "opaco, 256 bits"."""
        self.assertGreaterEqual(tokens.VERIFIER_BYTES * 8, 256)

    def test_every_secret_is_different(self) -> None:
        secrets_seen = {tokens.generate_refresh_secret().token for _ in range(50)}
        self.assertEqual(len(secrets_seen), 50)

    def test_selector_fits_the_column(self) -> None:
        """Si esto falla, la columna `selector` truncaría (o reventaría) en Postgres."""
        for _ in range(20):
            self.assertEqual(len(tokens.generate_refresh_secret().selector), tokens.SELECTOR_CHARS)


class VerifierHashTests(SimpleTestCase):
    def test_is_the_sha256_hex_of_the_verifier(self) -> None:
        secret = tokens.generate_refresh_secret()
        expected = hashlib.sha256(secret.verifier.encode("ascii")).hexdigest()
        self.assertEqual(tokens.hash_verifier(secret.verifier), expected)

    def test_hash_is_not_the_verifier(self) -> None:
        """La propiedad que H-14 exige, comprobada sobre la primitiva."""
        secret = tokens.generate_refresh_secret()
        digest = tokens.hash_verifier(secret.verifier)
        self.assertNotEqual(digest, secret.verifier)
        self.assertNotIn(secret.verifier, digest)

    def test_hash_fits_the_column(self) -> None:
        digest = tokens.hash_verifier(tokens.generate_refresh_secret().verifier)
        self.assertEqual(len(digest), tokens.VERIFIER_HASH_CHARS)

    def test_verify_accepts_the_right_verifier_and_rejects_any_other(self) -> None:
        secret = tokens.generate_refresh_secret()
        digest = tokens.hash_verifier(secret.verifier)
        self.assertTrue(tokens.verifier_matches(secret.verifier, digest))
        self.assertFalse(tokens.verifier_matches(secret.verifier + "x", digest))
        self.assertFalse(tokens.verifier_matches("", digest))
        self.assertFalse(tokens.verifier_matches(secret.verifier, ""))


class ParseRefreshTokenTests(SimpleTestCase):
    def test_splits_a_well_formed_token(self) -> None:
        secret = tokens.generate_refresh_secret()
        parsed = tokens.parse_refresh_token(secret.token)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed, (secret.selector, secret.verifier))

    def test_returns_none_for_anything_malformed(self) -> None:
        for malformed in ("", ".", "sinpunto", "a.b.c", "  ", ".verifier", "selector."):
            with self.subTest(value=malformed):
                self.assertIsNone(tokens.parse_refresh_token(malformed))


class AccessTokenTests(SimpleTestCase):
    def setUp(self) -> None:
        self.user_id = uuid.uuid4()
        self.org_id = uuid.uuid4()
        self.device_id = uuid.uuid4()

    def _issue(self) -> tokens.AccessToken:
        return tokens.issue_access_token(
            user_id=self.user_id,
            org_id=self.org_id,
            device_id=self.device_id,
            roles=["FIELD_AGENT"],
        )

    def test_roundtrip_carries_the_contract_claims(self) -> None:
        """Contrato `securitySchemes.bearerAuth`: sub, org, device, roles, jti, exp."""
        issued = self._issue()
        claims = tokens.decode_access_token(issued.token)
        self.assertIsNotNone(claims)
        assert claims is not None
        self.assertEqual(claims.user_id, self.user_id)
        self.assertEqual(claims.org_id, self.org_id)
        self.assertEqual(claims.device_id, self.device_id)
        self.assertEqual(claims.roles, ("FIELD_AGENT",))
        self.assertTrue(claims.jti)

    def test_expires_in_fifteen_minutes(self) -> None:
        """ADR-008: TTL 15 min."""
        self.assertEqual(self._issue().expires_in, 900)

    def test_is_signed_with_ed25519(self) -> None:
        header = jwt.get_unverified_header(self._issue().token)
        self.assertEqual(header["alg"], "EdDSA")

    def test_every_token_has_its_own_jti(self) -> None:
        first = tokens.decode_access_token(self._issue().token)
        second = tokens.decode_access_token(self._issue().token)
        assert first is not None and second is not None
        self.assertNotEqual(first.jti, second.jti)

    def test_rejects_garbage(self) -> None:
        for value in ("", "not-a-jwt", "a.b.c", "Bearer x"):
            with self.subTest(value=value):
                self.assertIsNone(tokens.decode_access_token(value))

    def test_rejects_a_tampered_payload(self) -> None:
        head, payload, signature = self._issue().token.split(".")
        other = tokens.issue_access_token(
            user_id=uuid.uuid4(),
            org_id=self.org_id,
            device_id=self.device_id,
            roles=["OWNER"],
        )
        forged = f"{head}.{other.token.split('.')[1]}.{signature}"
        self.assertIsNone(tokens.decode_access_token(forged))

    def test_rejects_a_token_signed_with_another_key(self) -> None:
        """Confusión de claves: firmar con otra Ed25519 no debe colar."""
        issued = self._issue()
        with override_settings(JWT_SIGNING_KEY=signing_key_to_pem(generate_signing_key())):
            tokens.reset_key_cache()
            self.assertIsNone(tokens.decode_access_token(issued.token))
        tokens.reset_key_cache()

    def test_rejects_the_alg_none_downgrade(self) -> None:
        """El clásico: cambiar `alg` a `none` y quitar la firma."""
        claims = tokens.decode_access_token(self._issue().token)
        assert claims is not None
        unsigned = jwt.encode({"sub": str(claims.user_id)}, key="", algorithm="none")
        self.assertIsNone(tokens.decode_access_token(unsigned))

    def test_rejects_an_hmac_token_signed_with_the_public_key(self) -> None:
        """Confusión de algoritmo EdDSA→HS256 usando la clave pública como secreto.

        El JWT se construye **a mano** (base64url + HMAC) porque `jwt.encode`
        se niega a usar un PEM como secreto HMAC: si se dejara que PyJWT lo
        montara, el test pasaría por la excepción de la librería y no por lo
        que aquí se quiere demostrar, que es que **el verificador** rechaza el
        token aunque llegue perfectamente formado desde la red.
        """
        import base64
        import hashlib
        import hmac as hmac_module
        import json

        def b64(raw: bytes) -> bytes:
            return base64.urlsafe_b64encode(raw).rstrip(b"=")

        public_pem = tokens.public_key_pem().encode("ascii")
        header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = b64(
            json.dumps(
                {
                    "sub": str(self.user_id),
                    "org": str(self.org_id),
                    "roles": ["OWNER"],
                    "jti": "forjado",
                    "exp": 4102444800,
                }
            ).encode()
        )
        signing_input = header + b"." + payload
        signature = b64(hmac_module.new(public_pem, signing_input, hashlib.sha256).digest())
        forged = (signing_input + b"." + signature).decode("ascii")

        self.assertIsNone(tokens.decode_access_token(forged))

    def test_rejects_an_expired_token(self) -> None:
        with override_settings(JWT_ACCESS_TTL_SECONDS=-1):
            expired = self._issue().token
        self.assertIsNone(tokens.decode_access_token(expired))

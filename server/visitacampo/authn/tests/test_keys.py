"""Tests de la gestión de la clave de firma Ed25519 (`authn.keys`).

Tarea 6.5 del brief: "gestionar la clave de firma Ed25519 como secreto de
archivo/variable de entorno (**no hardcodeada**)". Estos tests fijan las tres
propiedades que hacen que eso sea cierto y siga siéndolo:

1. la clave se lee de `JWT_SIGNING_KEY` / `JWT_SIGNING_KEY_FILE`;
2. sin clave configurada y con `DEBUG=False` (producción), el arranque
   **falla** en vez de firmar con algo por defecto;
3. no hay ninguna clave privada escrita en el código fuente.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from visitacampo.authn import keys


class LoadSigningKeyTests(SimpleTestCase):
    def setUp(self) -> None:
        keys.reset_key_cache()
        self.addCleanup(keys.reset_key_cache)
        self.key = keys.generate_signing_key()
        self.pem = keys.signing_key_to_pem(self.key)

    def _public_bytes(self, key: Ed25519PrivateKey) -> bytes:
        from cryptography.hazmat.primitives import serialization

        return key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def test_reads_a_pem_from_the_setting(self) -> None:
        with override_settings(JWT_SIGNING_KEY=self.pem, JWT_SIGNING_KEY_FILE=""):
            loaded = keys.get_signing_key()
        self.assertEqual(self._public_bytes(loaded), self._public_bytes(self.key))

    def test_reads_a_base64_wrapped_pem_from_the_setting(self) -> None:
        """`.env` es de una línea: el PEM se admite también en base64."""
        encoded = base64.b64encode(self.pem.encode("ascii")).decode("ascii")
        with override_settings(JWT_SIGNING_KEY=encoded, JWT_SIGNING_KEY_FILE=""):
            loaded = keys.get_signing_key()
        self.assertEqual(self._public_bytes(loaded), self._public_bytes(self.key))

    def test_reads_a_file_secret(self) -> None:
        """El camino recomendado por ADR-008: secreto montado como archivo."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jwt_ed25519.pem"
            path.write_text(self.pem, encoding="utf-8")
            with override_settings(JWT_SIGNING_KEY="", JWT_SIGNING_KEY_FILE=str(path)):
                loaded = keys.get_signing_key()
        self.assertEqual(self._public_bytes(loaded), self._public_bytes(self.key))

    def test_the_file_wins_over_the_inline_value(self) -> None:
        other = keys.generate_signing_key()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jwt_ed25519.pem"
            path.write_text(keys.signing_key_to_pem(other), encoding="utf-8")
            with override_settings(JWT_SIGNING_KEY=self.pem, JWT_SIGNING_KEY_FILE=str(path)):
                loaded = keys.get_signing_key()
        self.assertEqual(self._public_bytes(loaded), self._public_bytes(other))

    def test_rejects_a_key_that_is_not_ed25519(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_pem = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            .private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            .decode("ascii")
        )
        with override_settings(JWT_SIGNING_KEY=rsa_pem, JWT_SIGNING_KEY_FILE=""):
            with self.assertRaises(ImproperlyConfigured):
                keys.get_signing_key()

    def test_rejects_an_unparseable_value(self) -> None:
        with override_settings(JWT_SIGNING_KEY="esto-no-es-una-clave", JWT_SIGNING_KEY_FILE=""):
            with self.assertRaises(ImproperlyConfigured):
                keys.get_signing_key()

    def test_missing_file_fails_loudly(self) -> None:
        with override_settings(JWT_SIGNING_KEY="", JWT_SIGNING_KEY_FILE="/no/existe.pem"):
            with self.assertRaises(ImproperlyConfigured):
                keys.get_signing_key()


class NoKeyConfiguredTests(SimpleTestCase):
    def setUp(self) -> None:
        keys.reset_key_cache()
        self.addCleanup(keys.reset_key_cache)

    def test_production_without_a_key_refuses_to_sign(self) -> None:
        """Fail-closed: sin clave y sin DEBUG no se firma nada."""
        with override_settings(JWT_SIGNING_KEY="", JWT_SIGNING_KEY_FILE="", DEBUG=False):
            with self.assertRaises(ImproperlyConfigured) as raised:
                keys.get_signing_key()
        self.assertIn("JWT_SIGNING_KEY", str(raised.exception))

    def test_development_falls_back_to_an_ephemeral_key_with_a_warning(self) -> None:
        """En dev se genera una clave efímera *en memoria*, nunca una constante."""
        with override_settings(JWT_SIGNING_KEY="", JWT_SIGNING_KEY_FILE="", DEBUG=True):
            with self.assertWarns(RuntimeWarning):
                first = keys.get_signing_key()
            second = keys.get_signing_key()  # cacheada dentro del proceso
        self.assertIs(first, second)

    def test_the_ephemeral_key_is_not_shared_between_processes(self) -> None:
        """Reiniciar el proceso invalida los tokens: eso es lo que la hace efímera."""
        with override_settings(JWT_SIGNING_KEY="", JWT_SIGNING_KEY_FILE="", DEBUG=True):
            with self.assertWarns(RuntimeWarning):
                first = keys.get_signing_key()
            keys.reset_key_cache()
            with self.assertWarns(RuntimeWarning):
                second = keys.get_signing_key()
        self.assertNotEqual(
            first.private_bytes_raw(),
            second.private_bytes_raw(),
        )


class NoHardcodedKeyTests(SimpleTestCase):
    """Ninguna clave privada vive en el árbol de código de producción de `authn`.

    Los marcadores se componen en tiempo de ejecución en vez de escribirse
    enteros: si estuvieran literales, este mismo archivo sería un falso
    positivo de sí mismo (y `tests/` queda fuera del recorrido justamente para
    que este test no tenga que conocer sus propias excepciones).
    """

    MARKERS = tuple(f"BEGIN {kind}PRIVATE KEY" for kind in ("", "OPENSSH ", "EC ", "RSA "))

    def test_no_production_module_contains_a_private_key_block(self) -> None:
        package_root = Path(keys.__file__).resolve().parent
        scanned = 0
        for path in sorted(package_root.rglob("*.py")):
            if "tests" in path.relative_to(package_root).parts:
                continue
            scanned += 1
            source = path.read_text(encoding="utf-8")
            for marker in self.MARKERS:
                with self.subTest(module=path.name, marker=marker):
                    self.assertNotIn(marker, source)
        self.assertGreater(scanned, 0, "el recorrido no puede pasar por vacuidad")

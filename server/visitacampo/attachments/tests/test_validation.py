"""Allow-list de MIME, magic bytes y nombre generado (tarea 5-6 del Paso 17).

Sin base de datos: `validation.py` es puro.
"""

from __future__ import annotations

from typing import get_args

from django.test import SimpleTestCase

from visitacampo.attachments import validation
from visitacampo.attachments.schemas import Mime
from visitacampo.core.models import MimeType
from visitacampo.serverinfo import capabilities as caps

from .factories import (
    DISGUISED_BYTES,
    JPEG_BYTES,
    JPEG_MIME,
    PDF_BYTES,
    PDF_MIME,
    PNG_BYTES,
    PNG_MIME,
    WEBP_BYTES,
    WEBP_MIME,
)

BYTES_BY_MIME = {
    JPEG_MIME: JPEG_BYTES,
    PNG_MIME: PNG_BYTES,
    WEBP_MIME: WEBP_BYTES,
    PDF_MIME: PDF_BYTES,
}


class ContractMimeTests(SimpleTestCase):
    """Las **cuatro** listas de MIME del proyecto son la misma lista.

    Existen cuatro por razones distintas y legítimas —el modelo
    (`MimeType`), lo que el servidor anuncia (`capabilities`), lo que valida el
    esquema del contrato (`schemas.Mime`, que tiene que ser un `Literal` de
    cadenas para que mypy pueda comprobarlo) y las constantes de estos tests—,
    y **una divergencia entre ellas sería un fallo silencioso**: un tipo que el
    modelo acepta pero el esquema rechaza, o al revés. Esta clase es lo que
    convierte esa divergencia en una build roja.
    """

    def test_the_model_and_the_contract_literal_agree(self) -> None:
        self.assertEqual(set(get_args(Mime)), set(MimeType.values))

    def test_the_test_constants_agree_with_the_model(self) -> None:
        self.assertEqual({JPEG_MIME, PNG_MIME, WEBP_MIME, PDF_MIME}, set(MimeType.values))

    def test_the_magic_signature_table_covers_exactly_the_allow_list(self) -> None:
        """Un tipo permitido sin firma sería un tipo que el sniffing no sabe validar."""
        self.assertEqual(set(validation._MAGIC_SIGNATURES), set(MimeType.values))

    def test_the_extension_table_covers_exactly_the_allow_list(self) -> None:
        self.assertEqual(set(validation.EXTENSION_BY_MIME), set(MimeType.values))


class AllowListTests(SimpleTestCase):
    def test_the_allow_list_is_exactly_the_four_types_of_the_contract(self) -> None:
        self.assertEqual(
            set(validation.allowed_mime_types()),
            {"image/jpeg", "image/png", "image/webp", "application/pdf"},
        )

    def test_it_matches_what_the_server_announces(self) -> None:
        """La lista anunciada y la que se aplica no pueden divergir."""
        self.assertEqual(set(validation.allowed_mime_types()), set(caps.ATTACHMENT_ALLOWED_MIME))

    def test_every_allowed_type_has_an_extension(self) -> None:
        for mime in validation.allowed_mime_types():
            with self.subTest(mime=mime):
                self.assertIn(mime, validation.EXTENSION_BY_MIME)

    def test_common_dangerous_types_are_rejected(self) -> None:
        for mime in (
            "application/octet-stream",
            "text/html",
            "image/svg+xml",  # SVG es XML ejecutable en un navegador
            "application/x-msdownload",
            "image/jpeg; charset=utf-8",  # con parámetros: no es el valor del enum
            "IMAGE/JPEG",
            "",
        ):
            with self.subTest(mime=mime):
                self.assertFalse(validation.is_allowed_mime(mime))
                with self.assertRaises(validation.MimeRejected):
                    validation.check_mime_allowed(mime)


class MagicBytesTests(SimpleTestCase):
    def test_each_type_recognises_its_own_bytes(self) -> None:
        for mime, data in BYTES_BY_MIME.items():
            with self.subTest(mime=mime):
                self.assertTrue(validation.magic_bytes_match(data[: validation.SNIFF_LENGTH], mime))

    def test_no_type_recognises_another_types_bytes(self) -> None:
        """La matriz completa: cada firma solo casa con la suya."""
        for mime, data in BYTES_BY_MIME.items():
            for other in BYTES_BY_MIME:
                if other == mime:
                    continue
                with self.subTest(bytes_de=mime, declarado=other):
                    self.assertFalse(
                        validation.magic_bytes_match(data[: validation.SNIFF_LENGTH], other)
                    )

    def test_an_executable_declared_as_an_image_is_rejected(self) -> None:
        """El caso que justifica el sniffing: el `Content-Type` es del cliente."""
        self.assertFalse(validation.magic_bytes_match(DISGUISED_BYTES, JPEG_MIME))

    def test_empty_and_truncated_prefixes_never_match(self) -> None:
        for prefix in (b"", b"\xff", b"\xff\xd8", b"%PD"):
            for mime in BYTES_BY_MIME:
                with self.subTest(prefix=prefix, mime=mime):
                    self.assertFalse(validation.magic_bytes_match(prefix, mime))

    def test_a_type_outside_the_allow_list_never_matches(self) -> None:
        self.assertFalse(validation.magic_bytes_match(b"<svg", "image/svg+xml"))

    def test_sniff_length_covers_the_longest_signature(self) -> None:
        """WebP necesita 12 bytes (`RIFF` + tamaño + `WEBP`); leer menos no bastaría."""
        self.assertGreaterEqual(validation.SNIFF_LENGTH, 12)
        self.assertTrue(
            validation.magic_bytes_match(WEBP_BYTES[: validation.SNIFF_LENGTH], WEBP_MIME)
        )


class GeneratedFilenameTests(SimpleTestCase):
    def test_extension_comes_from_the_verified_mime(self) -> None:
        for mime, extension in validation.EXTENSION_BY_MIME.items():
            with self.subTest(mime=mime):
                self.assertTrue(validation.generated_filename(mime).endswith(f".{extension}"))

    def test_a_rejected_mime_produces_no_name_at_all(self) -> None:
        with self.assertRaises(validation.MimeRejected):
            validation.generated_filename("text/html")

    def test_names_are_unique_and_path_free(self) -> None:
        names = {validation.generated_filename(PDF_MIME) for _ in range(50)}
        self.assertEqual(len(names), 50)
        for name in names:
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertNotIn("..", name)

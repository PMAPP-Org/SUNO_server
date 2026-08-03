"""`sync/cursor.py`: codificación/decodificación del cursor opaco. Sin BD."""

from __future__ import annotations

from django.test import SimpleTestCase

from visitacampo.sync.cursor import CURSOR_ZERO, InvalidCursor, decode_cursor, encode_cursor


class RoundTripTests(SimpleTestCase):
    def test_encode_then_decode_returns_the_original_value(self) -> None:
        for value in (0, 1, 41, 102394, 2**40):
            with self.subTest(value=value):
                self.assertEqual(decode_cursor(encode_cursor(value)), value)

    def test_cursor_zero_decodes_to_zero(self) -> None:
        self.assertEqual(decode_cursor(CURSOR_ZERO), 0)

    def test_encoded_form_carries_the_documented_prefix(self) -> None:
        self.assertTrue(encode_cursor(102394).startswith("c1:"))

    def test_two_different_values_never_encode_to_the_same_cursor(self) -> None:
        self.assertNotEqual(encode_cursor(1), encode_cursor(2))

    def test_a_negative_change_seq_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_cursor(-1)


class InvalidCursorTests(SimpleTestCase):
    """El contrato exige rechazar, nunca adivinar, un cursor con mala forma."""

    def test_a_string_without_the_prefix_is_rejected(self) -> None:
        with self.assertRaises(InvalidCursor):
            decode_cursor("102394")

    def test_a_hand_built_cursor_with_the_right_prefix_but_wrong_width_is_rejected(self) -> None:
        with self.assertRaises(InvalidCursor):
            decode_cursor("c1:1")

    def test_non_digit_payload_is_rejected(self) -> None:
        with self.assertRaises(InvalidCursor):
            decode_cursor("c1:" + "x" * 20)

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(InvalidCursor):
            decode_cursor("")

    def test_a_truncated_cursor_is_rejected_by_length_not_silently_shortened(self) -> None:
        """La razón documentada del ancho fijo: un truncado no debe decodificar."""
        truncated = encode_cursor(102394)[:-1]
        with self.assertRaises(InvalidCursor):
            decode_cursor(truncated)

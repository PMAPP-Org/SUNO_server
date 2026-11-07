"""Mapeo de columnas del Excel y su sugerencia por encabezados (puro)."""

from __future__ import annotations

import unittest

from suno.budgets.mapping import (
    FIELDS,
    ColumnMapping,
    MappingError,
    fold,
    suggest_mapping,
)

COLUMNS: dict[str, str | None] = {
    "code": "A",
    "description": "B",
    "unit": "C",
    "quantity": "D",
    "unit_price": "E",
    "total": None,
}
VALID: dict[str, object] = {"sheet": "Presupuesto", "first_row": 3, "columns": COLUMNS}


def with_columns(**changes: str | None) -> dict[str, object]:
    return {**VALID, "columns": {**COLUMNS, **changes}}


class ColumnMappingTests(unittest.TestCase):
    def test_round_trip_and_indexes(self) -> None:
        mapping = ColumnMapping.from_json(VALID)
        self.assertEqual(mapping.to_json(), VALID)
        self.assertEqual(mapping.index("code"), 0)
        self.assertEqual(mapping.index("unit_price"), 4)
        self.assertIsNone(mapping.index("total"))

    def test_letters_are_normalised(self) -> None:
        mapping = ColumnMapping.from_json(with_columns(code=" a ", total="f"))
        self.assertEqual((mapping.columns["code"], mapping.columns["total"]), ("A", "F"))

    def test_rejects_invalid_mappings(self) -> None:
        bad = {
            "falta unidad": with_columns(unit=None),
            "letra inválida": with_columns(code="A1"),
            "tres letras": with_columns(code="AAA"),
            "más allá de BH": with_columns(total="BI"),
            "columna repetida": with_columns(description="A"),
            "fila 0": {**VALID, "first_row": 0},
            "fila booleana": {**VALID, "first_row": True},
            "sin hoja": {**VALID, "sheet": " "},
            "no es objeto": "hola",
        }
        for case, data in bad.items():
            with self.subTest(case=case), self.assertRaises(MappingError):
                ColumnMapping.from_json(data)


class SuggestMappingTests(unittest.TestCase):
    def test_finds_the_header_row_below_titles(self) -> None:
        rows = [
            ["PRESUPUESTO DE OBRA"],
            [],
            ["Ítem", "Descripción", "Und", "Cant.", "Vr. unitario", "Vr. total"],
            ["1", "Preliminares"],
        ]
        suggestion = suggest_mapping(rows)
        self.assertEqual((suggestion.header_row, suggestion.first_row), (3, 4))
        self.assertEqual(
            suggestion.columns,
            dict(zip(FIELDS, ["A", "B", "C", "D", "E", "F"], strict=True)),
        )

    def test_other_wordings_and_order(self) -> None:
        rows = [
            ["Descripción de la actividad", "Código", "Cantidad", "Unidad", "Valor Unitario ($)"],
        ]
        columns = suggest_mapping(rows).columns
        self.assertEqual(
            columns,
            {
                "code": "B",
                "description": "A",
                "unit": "D",
                "quantity": "C",
                "unit_price": "E",
                "total": None,
            },
        )

    def test_without_headers_suggests_nothing(self) -> None:
        suggestion = suggest_mapping([["1", "Excavación", "m3", 10, 5000]])
        self.assertIsNone(suggestion.header_row)
        self.assertEqual(suggestion.first_row, 1)
        self.assertEqual(set(suggestion.columns.values()), {None})

    def test_fold(self) -> None:
        self.assertEqual(fold("Descripción ÍTEM Nº"), "descripcion item no")

"""Mapeo de columnas del Excel de presupuesto y su sugerencia (puro).

El mapeo dice en qué hoja, desde qué fila y en qué columna (letra de Excel)
está cada dato. Se guarda por presupuesto (`Budget.column_mapping`) con la
forma de la spec::

    {"sheet": str, "first_row": int,
     "columns": {"code": "A", "description": "B", "unit": "C",
                 "quantity": "D", "unit_price": "E", "total": "F" | None}}

La sugerencia busca, en las primeras filas, la que más se parece a una fila de
encabezados ("Ítem", "Descripción", "Und", "Cant.", "Vr. unitario"…).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

#: Columnas que se leen de cada fila (A–BH): acota el ancho de un archivo hostil.
MAX_COLUMNS = 60
MAX_FIRST_ROW = 20_000
FIELDS = ("code", "description", "unit", "quantity", "unit_price", "total")
REQUIRED_FIELDS = ("code", "description", "unit", "quantity", "unit_price")
FIELD_LABELS = {
    "code": "Código",
    "description": "Descripción",
    "unit": "Unidad",
    "quantity": "Cantidad",
    "unit_price": "Valor unitario",
    "total": "Valor total (opcional, para verificar)",
}

_LETTERS = re.compile(r"[A-Z]{1,2}")

#: Encabezados conocidos, ya normalizados con `normalize_header`.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "code": ("item", "items", "it", "cod", "codigo", "no", "n", "num", "numero", "id"),
    "description": (
        "descripcion", "actividad", "actividades", "concepto", "detalle", "nombre",
    ),
    "unit": ("und", "un", "unid", "unidad", "u", "ud", "uds", "um", "unidad de medida"),
    "quantity": ("cant", "cantidad", "cantidades", "cant total", "qty"),
    "unit_price": (
        "vr unitario", "valor unitario", "precio unitario", "v unitario", "vlr unitario",
        "vr unit", "v unit", "vlr unit", "p unitario", "pu", "costo unitario", "unitario",
        "precio",
    ),
    "total": (
        "vr total", "valor total", "precio total", "vlr total", "v total", "total",
        "valor parcial", "vr parcial", "parcial", "costo total", "importe",
    ),
}  # fmt: skip
_EXACT = {synonym: field for field, synonyms in _SYNONYMS.items() for synonym in synonyms}
#: Si no hay coincidencia exacta: una palabra que empiece así (en este orden).
_KEYWORDS = (
    ("unit_price", "unitari"),
    ("description", "descrip"),
    ("quantity", "cant"),
    ("unit", "unid"),
    ("code", "codig"),
    ("total", "total"),
)


class MappingError(ValueError):
    """El mapeo no es válido; el mensaje es para el usuario."""


def fold(value: object) -> str:
    """Texto en minúsculas y sin tildes, para comparar sin importar cómo se escribió."""
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def normalize_header(value: object) -> str:
    """`"Vr. Unitario ($)"` → `"vr unitario"`."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(value)).split())


@dataclass(frozen=True)
class ColumnMapping:
    sheet: str
    first_row: int
    columns: Mapping[str, str | None]

    def index(self, field: str) -> int | None:
        """Posición (desde 0) de la columna de `field` en una fila; None si no se mapeó."""
        letter = self.columns.get(field)
        return column_index_from_string(letter) - 1 if letter else None

    def to_json(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "first_row": self.first_row,
            "columns": {field: self.columns.get(field) for field in FIELDS},
        }

    @classmethod
    def from_json(cls, data: object) -> ColumnMapping:
        if not isinstance(data, dict):
            raise MappingError("El mapeo de columnas no es válido.")
        sheet = data.get("sheet")
        if not isinstance(sheet, str) or not sheet.strip():
            raise MappingError("Elige la hoja del presupuesto.")
        first_row = data.get("first_row")
        if (
            isinstance(first_row, bool)
            or not isinstance(first_row, int)
            or not 1 <= first_row <= MAX_FIRST_ROW
        ):
            raise MappingError("La fila donde empiezan los datos debe estar entre 1 y 20.000.")
        raw = data.get("columns")
        if not isinstance(raw, dict):
            raise MappingError("El mapeo de columnas no es válido.")
        columns: dict[str, str | None] = {}
        for field in FIELDS:
            value = raw.get(field)
            letter = value.strip().upper() if isinstance(value, str) else value
            if not letter:
                if field in REQUIRED_FIELDS:
                    raise MappingError(f"Elige la columna de {FIELD_LABELS[field].lower()}.")
                columns[field] = None
                continue
            if (
                not isinstance(letter, str)
                or not _LETTERS.fullmatch(letter)
                or column_index_from_string(letter) > MAX_COLUMNS
            ):
                raise MappingError(f"{FIELD_LABELS[field]}: columna no válida.")
            columns[field] = letter
        used = [letter for letter in columns.values() if letter]
        if len(used) != len(set(used)):
            raise MappingError("Una misma columna no puede tener dos significados.")
        return cls(sheet=sheet, first_row=first_row, columns=columns)


@dataclass(frozen=True)
class Suggestion:
    #: Fila de Excel (desde 1) con los encabezados; None si no se encontró.
    header_row: int | None
    first_row: int
    columns: dict[str, str | None]


def header_field(value: object) -> str | None:
    """Qué dato anuncia un encabezado, o None."""
    if value is None:
        return None
    text = normalize_header(value)
    if not text:
        return None
    if text in _EXACT:
        return _EXACT[text]
    words = text.split()
    for field, keyword in _KEYWORDS:
        if any(word.startswith(keyword) for word in words):
            return field
    return None


def suggest_mapping(rows: Sequence[Sequence[object]]) -> Suggestion:
    """La fila con más encabezados reconocidos (mínimo 3) y sus columnas."""
    best: tuple[int, int, dict[str, str]] | None = None
    for number, row in enumerate(rows, start=1):
        columns: dict[str, str] = {}
        for index, value in enumerate(row[:MAX_COLUMNS]):
            field = header_field(value)
            if field is not None and field not in columns:
                columns[field] = get_column_letter(index + 1)
        if len(columns) >= 3 and (best is None or len(columns) > best[0]):
            best = (len(columns), number, columns)
    if best is None:
        return Suggestion(header_row=None, first_row=1, columns=dict.fromkeys(FIELDS))
    _, number, found = best
    return Suggestion(
        header_row=number,
        first_row=number + 1,
        columns={field: found.get(field) for field in FIELDS},
    )

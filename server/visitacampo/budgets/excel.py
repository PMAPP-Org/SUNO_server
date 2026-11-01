"""Lectura del Excel de presupuesto (pura: sin BD).

Primera parte — el archivo:

- Solo `.xlsx` (`.xlsm` y `.xls` se rechazan por nombre, con su motivo) y un
  `vbaProject.bin` dentro (macros) se rechaza aunque el archivo se llame `.xlsx`.
- Límites antes de que openpyxl mire nada: 20 MB, ZIP de máximo 1.000 entradas
  y 200 MB descomprimidos. openpyxl parsea el XML con `defusedxml` (instalado).
- `read_only=True, data_only=True`: se lee en streaming y el valor **calculado**
  de las fórmulas (el que guardó Excel). En `read_only` las celdas combinadas
  no se exponen: el valor está en la celda superior izquierda y el resto vacío.
- Se ignora la dimensión que declara la hoja (`reset_dimensions`), que puede
  anunciar un millón de filas vacías, y cada fila se lee hasta la columna 60.
- Máximo 20.000 filas: si hay datos más abajo se rechaza, no se trunca.

Segunda parte — interpretación de filas: ver más abajo (`interpret_rows`).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .mapping import MAX_COLUMNS

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 1_000
PREVIEW_ROWS = 30

_NOT_XLSX = "El archivo no es un archivo .xlsx válido."


class ExcelRejected(Exception):
    """El archivo no se puede usar; el mensaje es para el usuario."""


def check_file_name(name: str) -> None:
    lower = name.lower()
    if lower.endswith(".xlsm"):
        raise ExcelRejected(
            "Los libros con macros (.xlsm) no se admiten: guárdalo como .xlsx."
        )
    if lower.endswith(".xls"):
        raise ExcelRejected(
            "El formato .xls (Excel 97-2003) no se admite: guárdalo como .xlsx."
        )
    if not lower.endswith(".xlsx"):
        raise ExcelRejected("Solo se admiten archivos .xlsx.")


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _check_archive(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise ExcelRejected(f"El archivo supera {max_bytes // (1024 * 1024)} MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ExcelRejected(_NOT_XLSX) from exc
    if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
        raise ExcelRejected("El libro es demasiado grande una vez descomprimido.")
    if any("vbaProject" in entry.filename for entry in entries):
        raise ExcelRejected("El libro contiene macros: guárdalo como .xlsx sin macros.")


def _open(data: bytes, max_bytes: int) -> Any:
    _check_archive(data, max_bytes)
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (InvalidFileException, KeyError, ValueError, TypeError, OSError) as exc:
        raise ExcelRejected(_NOT_XLSX) from exc


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


@dataclass(frozen=True)
class SheetPreview:
    sheet_names: tuple[str, ...]
    sheet: str
    rows: tuple[tuple[Any, ...], ...]


def preview_sheet(
    data: bytes,
    sheet: str | None = None,
    *,
    rows: int = PREVIEW_ROWS,
    max_bytes: int = MAX_BYTES,
) -> SheetPreview:
    """Las primeras `rows` filas de `sheet` (o de la primera hoja si no existe)."""
    workbook = _open(data, max_bytes)
    try:
        names = tuple(workbook.sheetnames)
        if not names:
            raise ExcelRejected("El libro no tiene hojas.")
        name = sheet if sheet in names else names[0]
        worksheet = workbook[name]
        worksheet.reset_dimensions()
        values = tuple(
            tuple(row)
            for row in worksheet.iter_rows(
                min_row=1, max_row=rows, max_col=MAX_COLUMNS, values_only=True
            )
        )
        return SheetPreview(sheet_names=names, sheet=name, rows=values)
    finally:
        workbook.close()


def read_rows(
    data: bytes,
    sheet: str,
    *,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
) -> list[tuple[Any, ...]]:
    """Todas las filas de `sheet` (la 1 de Excel es la posición 0), de 60 columnas."""
    workbook = _open(data, max_bytes)
    try:
        if sheet not in workbook.sheetnames:
            raise ExcelRejected(
                f"El libro no tiene la hoja «{sheet}». Cambia el mapeo de columnas."
            )
        worksheet = workbook[sheet]
        worksheet.reset_dimensions()
        rows: list[tuple[Any, ...]] = []
        for number, row in enumerate(
            worksheet.iter_rows(min_row=1, max_col=MAX_COLUMNS, values_only=True), start=1
        ):
            if number <= max_rows:
                rows.append(tuple(row))
            elif any(_present(value) for value in row):
                raise ExcelRejected(
                    f"La hoja tiene datos después de la fila {_thousands(max_rows)}: "
                    f"el máximo es {_thousands(max_rows)} filas."
                )
        return rows
    finally:
        workbook.close()

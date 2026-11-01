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

Segunda parte — interpretación de filas (§2 de la spec):

- Código, cantidad y valor unitario → ítem. Código y descripción sin cantidad
  ni valor unitario → capítulo. Sin código, o con texto tipo "Subtotal" /
  "Total" en el código o la descripción → se ignora y se cuenta.
- Código repetido, cantidad o valor unitario no numéricos o negativos, o solo
  uno de los dos → se rechaza nombrando la fila.
- Padre: el capítulo prefijo más largo ("1.2" para "1.2.3"); si no hay y la
  fila es un ítem, el último capítulo encima de él.
- Cantidad a 4 decimales y valor unitario a centavos; el total del ítem es
  cantidad × valor unitario a centavos (el del Excel solo se compara y avisa);
  el del capítulo, la suma de sus hijos.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .mapping import FIELDS, MAX_COLUMNS, ColumnMapping, fold
from .totals import ZERO, item_total, money, parse_number, quantity

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 1_000
PREVIEW_ROWS = 30
MAX_CODE_LENGTH = 50
MAX_UNIT_LENGTH = 30
MAX_DESCRIPTION_LENGTH = 2_000
MAX_EXAMPLES = 20
#: Topes de `numeric(18,4)` (cantidad) y `numeric(18,2)` (dinero).
MAX_QUANTITY = Decimal("1e14")
MAX_MONEY = Decimal("1e16")

#: Tipos de fila; los mismos valores que `core.models.BudgetRowKind`.
KIND_CHAPTER = "chapter"
KIND_ITEM = "item"

#: "Subtotal", "Sub-total", "TOTAL COSTO DIRECTO", "Totales"…, ya sin tildes ni mayúsculas.
_TOTAL_LABEL = re.compile(r"(sub\s*-?\s*)?totale?s?\b")

_NOT_XLSX = "El archivo no es un archivo .xlsx válido."


class ExcelRejected(Exception):
    """El archivo no se puede usar; el mensaje es para el usuario."""


def check_file_name(name: str) -> None:
    lower = name.lower()
    if lower.endswith(".xlsm"):
        raise ExcelRejected("Los libros con macros (.xlsm) no se admiten: guárdalo como .xlsx.")
    if lower.endswith(".xls"):
        raise ExcelRejected("El formato .xls (Excel 97-2003) no se admite: guárdalo como .xlsx.")
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
        name: str = sheet if sheet is not None and sheet in names else names[0]
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


@dataclass(frozen=True)
class ParsedRow:
    #: Fila de Excel (desde 1).
    row: int
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    parent_code: str | None
    #: Posición entre las filas aceptadas (0, 1, 2…).
    sort_order: int


@dataclass(frozen=True)
class ParsedBudget:
    rows: tuple[ParsedRow, ...]
    direct_cost: Decimal
    ignored_rows: int
    ignored_examples: tuple[int, ...]
    total_mismatches: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Found:
    row: int
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    excel_total: Decimal | None


def _cell(row: Sequence[Any], index: int | None) -> Any:
    return row[index] if index is not None and index < len(row) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _code(value: Any) -> str:
    return _text(value).rstrip(".").strip()


def _is_total_label(text: str) -> bool:
    return bool(text) and _TOTAL_LABEL.match(fold(text).strip()) is not None


def _number(value: Any, number: int, code: str, label: str, limit: Decimal) -> Decimal | None:
    try:
        parsed = parse_number(value)
    except ValueError:
        raise ExcelRejected(
            f"Fila {number} (código {code}): {label} «{_text(value)[:40]}» no es un número."
        ) from None
    if parsed is None:
        return None
    if parsed < 0:
        raise ExcelRejected(
            f"Fila {number} (código {code}): {label} tiene un valor negativo ({_text(value)})."
        )
    if parsed >= limit:
        raise ExcelRejected(f"Fila {number} (código {code}): {label} es demasiado grande.")
    return parsed


def _optional_number(value: Any) -> Decimal | None:
    try:
        return parse_number(value)
    except ValueError:
        return None  # el total del Excel solo se usa para avisar


def _prefix_parent(code: str, chapters: Collection[str]) -> str | None:
    parts = code.split(".")
    for size in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in chapters:
            return candidate
    return None


def _parents(found: Sequence[_Found]) -> dict[str, str | None]:
    chapters = {f.code for f in found if f.kind == KIND_CHAPTER}
    parents: dict[str, str | None] = {}
    last_chapter: str | None = None
    for f in found:
        parent = _prefix_parent(f.code, chapters)
        if parent is None and f.kind == KIND_ITEM:
            parent = last_chapter
        parents[f.code] = parent
        if f.kind == KIND_CHAPTER:
            last_chapter = f.code
    return parents


def _totals(found: Sequence[_Found], parents: dict[str, str | None]) -> dict[str, Decimal]:
    """Total de cada fila. Los padres de un capítulo son prefijos estrictos de su
    código, así que subir por la cadena siempre termina."""
    totals = {f.code: ZERO for f in found}
    for f in found:
        if f.kind != KIND_ITEM or f.quantity is None or f.unit_price is None:
            continue
        value = item_total(f.quantity, f.unit_price)
        if value >= MAX_MONEY:
            raise ExcelRejected(f"Fila {f.row} (código {f.code}): el total es demasiado grande.")
        totals[f.code] = value
        parent = parents[f.code]
        while parent is not None:
            totals[parent] += value
            parent = parents[parent]
    return totals


def _examples(values: Sequence[str]) -> str:
    text = ", ".join(values[:MAX_EXAMPLES])
    return text + ("…" if len(values) > MAX_EXAMPLES else "")


def interpret_rows(rows: Sequence[Sequence[Any]], mapping: ColumnMapping) -> ParsedBudget:
    """Filas de la hoja (la 1 de Excel en `rows[0]`) → capítulos e ítems."""
    col = {field: mapping.index(field) for field in FIELDS}
    found: list[_Found] = []
    first_seen: dict[str, int] = {}
    ignored: list[int] = []
    for number in range(mapping.first_row, len(rows) + 1):
        row = rows[number - 1]
        if not any(_present(value) for value in row):
            continue
        code = _code(_cell(row, col["code"]))
        description = _text(_cell(row, col["description"]))
        if not code or _is_total_label(code) or _is_total_label(description):
            ignored.append(number)
            continue
        if len(code) > MAX_CODE_LENGTH:
            raise ExcelRejected(
                f"Fila {number}: el código «{code[:20]}…» tiene más de "
                f"{MAX_CODE_LENGTH} caracteres."
            )
        qty = _number(_cell(row, col["quantity"]), number, code, "la cantidad", MAX_QUANTITY)
        price = _number(_cell(row, col["unit_price"]), number, code, "el valor unitario", MAX_MONEY)
        if qty is None and price is None:
            if not description:
                ignored.append(number)
                continue
            kind = KIND_CHAPTER
        elif qty is None or price is None:
            has, missing = ("valor unitario", "la cantidad")
            if price is None:
                has, missing = ("cantidad", "el valor unitario")
            raise ExcelRejected(
                f"Fila {number} (código {code}): tiene {has} pero le falta {missing}."
            )
        else:
            kind = KIND_ITEM
        if code in first_seen:
            raise ExcelRejected(
                f"El código {code} está repetido (filas {first_seen[code]} y {number})."
            )
        first_seen[code] = number
        found.append(
            _Found(
                row=number,
                code=code,
                kind=kind,
                description=description[:MAX_DESCRIPTION_LENGTH],
                unit=_text(_cell(row, col["unit"]))[:MAX_UNIT_LENGTH],
                quantity=quantity(qty) if qty is not None else None,
                unit_price=money(price) if price is not None else None,
                excel_total=(
                    _optional_number(_cell(row, col["total"])) if kind == KIND_ITEM else None
                ),
            )
        )
    if not any(f.kind == KIND_ITEM for f in found):
        raise ExcelRejected(
            f"No se encontró ningún ítem (código, cantidad y valor unitario) desde la fila "
            f"{mapping.first_row}. Revisa el mapeo de columnas."
        )

    parents = _parents(found)
    totals = _totals(found, parents)
    direct_cost = sum((totals[f.code] for f in found if f.kind == KIND_ITEM), ZERO)
    if direct_cost >= MAX_MONEY:
        raise ExcelRejected("El costo directo del presupuesto es demasiado grande.")
    mismatches = [
        f for f in found if f.excel_total is not None and money(f.excel_total) != totals[f.code]
    ]
    warnings: list[str] = []
    if ignored:
        warnings.append(
            f"{len(ignored)} fila(s) ignorada(s) por no tener código o ser de subtotal/total: "
            f"{_examples([str(n) for n in ignored])}."
        )
    if mismatches:
        warnings.append(
            f"{len(mismatches)} ítem(s) cuyo total del Excel no coincide con cantidad × valor "
            "unitario (se usa el calculado): "
            + _examples(
                [
                    f"{f.code} (fila {f.row}: {money(f.excel_total or ZERO)} en el Excel, "
                    f"{totals[f.code]} calculado)"
                    for f in mismatches
                ]
            )
            + "."
        )
    return ParsedBudget(
        rows=tuple(
            ParsedRow(
                row=f.row,
                code=f.code,
                kind=f.kind,
                description=f.description,
                unit=f.unit,
                quantity=f.quantity,
                unit_price=f.unit_price,
                total=totals[f.code],
                parent_code=parents[f.code],
                sort_order=index,
            )
            for index, f in enumerate(found)
        ),
        direct_cost=direct_cost,
        ignored_rows=len(ignored),
        ignored_examples=tuple(ignored[:MAX_EXAMPLES]),
        total_mismatches=len(mismatches),
        warnings=tuple(warnings),
    )


def parse_budget(
    data: bytes,
    mapping: ColumnMapping,
    *,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
) -> ParsedBudget:
    """Lee el `.xlsx` con `mapping`. Lanza `ExcelRejected` con un mensaje para el usuario."""
    return interpret_rows(
        read_rows(data, mapping.sheet, max_rows=max_rows, max_bytes=max_bytes), mapping
    )

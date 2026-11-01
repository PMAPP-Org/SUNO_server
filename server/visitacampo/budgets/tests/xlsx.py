"""Libros `.xlsx` de prueba, construidos en memoria con openpyxl."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from openpyxl import Workbook

HEADER = ["Ítem", "Descripción", "Und", "Cant.", "Vr. unitario", "Vr. total"]


def build_xlsx(
    rows: Iterable[Sequence[Any]],
    *,
    header: Sequence[Any] | None = HEADER,
    sheet: str = "Presupuesto",
    title_rows: Sequence[Sequence[Any]] = (),
    merges: Sequence[str] = (),
    extra_sheets: Sequence[str] = (),
) -> bytes:
    """Una hoja con `title_rows`, el encabezado y `rows`, en ese orden desde la fila 1."""
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet
    for row in title_rows:
        worksheet.append(list(row))
    if header is not None:
        worksheet.append(list(header))
    for row in rows:
        worksheet.append(list(row))
    for cell_range in merges:
        worksheet.merge_cells(cell_range)
    for name in extra_sheets:
        workbook.create_sheet(name)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rewrite_entry(data: bytes, name: str, change: Callable[[str], str]) -> bytes:
    """El mismo ZIP con la entrada `name` transformada por `change`."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == name:
                content = change(content.decode()).encode()
            target.writestr(info.filename, content)
    return buffer.getvalue()


def with_cached_formula(
    data: bytes, cell: str, formula: str, value: str, *, sheet_xml: str = "xl/worksheets/sheet1.xml"
) -> bytes:
    """Pone en `cell` una fórmula **con su valor calculado**, como la guarda Excel.

    openpyxl escribe fórmulas sin valor calculado; Excel y LibreOffice guardan
    los dos. Se reescribe el XML de la celda para imitarlos.
    """
    pattern = re.compile(rf'<c r="{cell}"[^>]*?(?:/>|>.*?</c>)', re.S)
    replacement = f'<c r="{cell}"><f>{formula}</f><v>{value}</v></c>'

    def change(xml: str) -> str:
        new, count = pattern.subn(replacement, xml, count=1)
        assert count == 1, f"la celda {cell} no está en la hoja"
        return new

    return rewrite_entry(data, sheet_xml, change)


def with_extra_entry(data: bytes, name: str, content: bytes = b"") -> bytes:
    """El mismo ZIP con una entrada más (p. ej. `xl/vbaProject.bin`)."""
    buffer = io.BytesIO(data)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()

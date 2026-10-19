"""Bounded XLSForm importer. Unsupported rules fail explicitly, never disappear."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, iterparse
from openpyxl import load_workbook

from visitacampo.forms.definition import (
    FormDefinition,
    TemplateDefinitionError,
    validate_definition,
)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 20 * 1024 * 1024
MAX_SHEET_ROWS = 2000
_TYPES = {
    "text": "text",
    "integer": "integer",
    "decimal": "number",
    "date": "date",
    "dateTime": "datetime",
    "geopoint": "location",
    "geotrace": "line",
    "geoshape": "polygon",
    "image": "photo",
    "barcode": "qr",
    "select_one": "single_choice",
    "select_multiple": "multi_choice",
    "begin_repeat": "repeat",
}


class InvalidXlsForm(ValueError):
    pass


def _rows(sheet: Any, allowed: set[str]) -> list[tuple[int, dict[str, Any]]]:
    # Actual XML row/column references were bounded before openpyxl sees them.
    # Ignore the dimension hint: it can claim a billion empty rows.
    sheet.reset_dimensions()
    rows = sheet.iter_rows(max_row=MAX_SHEET_ROWS + 1)
    header = next(rows, ())
    names = [str(c.value).strip() if c.value is not None else "" for c in header]
    if len(names) > 32:
        raise InvalidXlsForm(f"{sheet.title}: máximo 32 columnas.")
    nonempty = [n for n in names if n]
    if len(nonempty) != len(set(nonempty)):
        raise InvalidXlsForm(f"{sheet.title}: columnas duplicadas.")
    result = []
    for number, cells in enumerate(rows, 2):
        if not any(c.value is not None for c in cells):
            continue
        if number > MAX_SHEET_ROWS or len(cells) > 32:
            raise InvalidXlsForm(f"{sheet.title}: límite de filas/columnas excedido.")
        row = {}
        for index, cell in enumerate(cells):
            name = names[index] if index < len(names) else ""
            if cell.value is None or cell.value == "":
                continue
            if cell.data_type == "f":
                raise InvalidXlsForm(f"{sheet.title}, fila {number}: fórmulas Excel no admitidas.")
            if name not in allowed:
                raise InvalidXlsForm(f"{sheet.title}, fila {number}: columna no admitida {name!r}.")
            row[name] = cell.value
        result.append((number, row))
    return result


def _check_xml_bounds(archive: zipfile.ZipFile) -> None:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    for entry in archive.infolist():
        if not entry.filename.endswith(".xml"):
            continue
        rows = cells = 0
        # Includes nonstandard worksheet paths referenced by workbook relations.
        with archive.open(entry) as source:
            for _, element in iterparse(source, events=("end",), forbid_dtd=True):
                if element.tag == namespace + "c":
                    cells += 1
                    reference = element.get("r")
                    if reference:
                        match = re.fullmatch(r"([A-Z]{1,2})([1-9][0-9]{0,9})", reference)
                        if not match:
                            raise InvalidXlsForm("Referencia de celda fuera de límites.")
                        letters, number = match.groups()
                        column = 0
                        for letter in letters:
                            column = column * 26 + ord(letter) - ord("A") + 1
                        if column > 32 or int(number) > MAX_SHEET_ROWS:
                            raise InvalidXlsForm("Referencia de celda fuera de límites.")
                elif element.tag == namespace + "row":
                    rows += 1
                    if (
                        rows > MAX_SHEET_ROWS
                        or int(element.get("r", rows)) > MAX_SHEET_ROWS
                        or cells > 32
                    ):
                        raise InvalidXlsForm("La hoja supera el límite de filas/columnas.")
                    cells = 0
                element.clear()


def _relevant(text: Any) -> dict[str, Any]:
    match = re.fullmatch(r"\$\{([a-zA-Z][a-zA-Z0-9_]*)\}\s*(=|!=|>=|<=|>|<)\s*(.+)", str(text))
    if not match:
        raise InvalidXlsForm("relevant: solo se admite ${campo} operador literal.")
    key, operator, literal = match.groups()
    if len(literal) >= 2 and literal[0] == literal[-1] and literal[0] in "\"'":
        value: Any = literal[1:-1]
    elif re.fullmatch(r"-?\d+(\.\d+)?", literal):
        value = float(literal) if "." in literal else int(literal)
    else:
        raise InvalidXlsForm("relevant: literal no admitido.")
    return {
        "op": {"=": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[operator],
        "field": key,
        "value": value,
    }


def import_xlsform(data: bytes) -> FormDefinition:
    if len(data) > MAX_FILE_BYTES:
        raise InvalidXlsForm("El XLSForm supera 5 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 100 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise InvalidXlsForm("El contenido expandido supera el límite.")
            if any(
                "vbaProject" in e.filename or e.filename.startswith("xl/externalLinks/")
                for e in entries
            ):
                raise InvalidXlsForm("No se admiten macros ni enlaces externos.")
            _check_xml_bounds(archive)
        workbook = load_workbook(
            io.BytesIO(data), read_only=True, data_only=False, keep_links=False
        )
        try:
            return _convert(workbook)
        finally:
            workbook.close()
    except InvalidXlsForm:
        raise
    except (
        TemplateDefinitionError,
        ValueError,
        KeyError,
        OSError,
        zipfile.BadZipFile,
        ParseError,
        DefusedXmlException,
    ) as exc:
        raise InvalidXlsForm(f"XLSForm inválido: {exc}") from exc


def _convert(workbook: Any) -> FormDefinition:
    if "survey" not in workbook.sheetnames:
        raise InvalidXlsForm("Falta la hoja survey.")
    if set(workbook.sheetnames) - {"survey", "choices", "settings"}:
        raise InvalidXlsForm("Solo se admiten hojas survey, choices y settings.")
    choices: dict[str, list[dict[str, Any]]] = {}
    if "choices" in workbook.sheetnames:
        for number, row in _rows(workbook["choices"], {"list_name", "name", "label"}):
            if not all(k in row for k in ("list_name", "name", "label")):
                raise InvalidXlsForm(f"choices, fila {number}: faltan lista, nombre o etiqueta.")
            choices.setdefault(str(row["list_name"]), []).append(
                {"value": str(row["name"]), "label": {"es": str(row["label"])}}
            )
    definition: dict[str, Any] = {"dsl": "visitacampo/form@1", "fields": []}
    if "settings" in workbook.sheetnames:
        settings = _rows(workbook["settings"], {"form_title", "form_id", "version"})
        if len(settings) > 1:
            raise InvalidXlsForm("settings: debe contener una sola fila.")
        if settings and settings[0][1].get("form_title"):
            definition["title"] = {"es": str(settings[0][1]["form_title"])}
    stack = [definition["fields"]]
    for number, row in _rows(
        workbook["survey"],
        {"type", "name", "label", "hint", "required", "default", "relevant", "appearance"},
    ):
        try:
            kind = (
                str(row.get("type", ""))
                .strip()
                .replace("begin repeat", "begin_repeat")
                .replace("end repeat", "end_repeat")
            )
            if kind == "end_repeat":
                if len(stack) == 1 or set(row) != {"type"}:
                    raise InvalidXlsForm("end_repeat sin grupo o con propiedades no admitidas.")
                stack.pop()
                continue
            parts = kind.split()
            if not parts or parts[0] not in _TYPES:
                raise InvalidXlsForm(f"tipo no admitido: {kind!r}.")
            field: dict[str, Any] = {
                "key": str(row.get("name", "")),
                "type": _TYPES[parts[0]],
                "label": {"es": str(row.get("label", ""))},
            }
            if field["type"] in ("single_choice", "multi_choice"):
                if len(parts) != 2 or parts[1] not in choices:
                    raise InvalidXlsForm("lista de opciones inexistente.")
                field["options"] = choices[parts[1]]
            elif len(parts) != 1:
                raise InvalidXlsForm("parámetros de tipo no admitidos.")
            if row.get("appearance"):
                if kind == "image" and row["appearance"] == "signature":
                    field["type"] = "signature"
                else:
                    raise InvalidXlsForm("appearance no admitido.")
            if "hint" in row:
                field["help"] = {"es": str(row["hint"])}
            required = str(row.get("required", "no")).lower()
            if required not in ("yes", "no", "true", "false"):
                raise InvalidXlsForm("required debe ser yes/no; no admite expresiones.")
            field["required"] = required in ("yes", "true")
            if "relevant" in row:
                field["visible_if"] = _relevant(row["relevant"])
            if "default" in row:
                value = row["default"]
                if field["type"] in ("integer", "number"):
                    value = float(value)
                    if field["type"] == "integer" and value.is_integer():
                        value = int(value)
                elif field["type"] == "multi_choice":
                    value = str(value).split()
                else:
                    value = str(value)
                    if "${" in value or re.search(r"\w+\s*\(", value):
                        raise InvalidXlsForm("default solo admite valores literales.")
                field["default"] = value
            stack[-1].append(field)
            if field["type"] == "repeat":
                field["fields"] = []
                stack.append(field["fields"])
                if len(stack) > 4:
                    raise InvalidXlsForm("máximo tres niveles de grupos repetibles.")
        except (ValueError, InvalidXlsForm) as exc:
            raise InvalidXlsForm(f"survey, fila {number}: {exc}") from exc
    if len(stack) != 1:
        raise InvalidXlsForm("Falta end_repeat.")
    return validate_definition(definition)

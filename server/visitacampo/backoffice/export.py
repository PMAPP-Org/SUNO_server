"""Exportación de visitas a CSV y Excel (Paso 24).

- **Síncrona y con límite**: hasta [MAX_EXPORT_ROWS] filas. Más que eso no se
  trunca en silencio ni se deja correr hasta el timeout: se rechaza con un
  mensaje que pide filtrar ([ExportTooLarge]). 10 000 filas son meses de
  trabajo de un equipo de campo y se generan en segundos; por encima, el
  camino correcto es un job asíncrono (ADR-006), fuera del MVP.
- **Inyección de fórmulas** (CSV/formula injection, OWASP): una respuesta de
  texto que empiece por `=`, `+`, `-`, `@`, tabulador o retorno de carro la
  interpretaría Excel como fórmula al abrir el archivo (`=HYPERLINK(...)`
  exfiltrando datos, por ejemplo). Esas celdas se prefijan con `'` y se
  exportan como texto. Los números se exportan como números.
- **Columnas**: los datos de la visita, la última revisión y una columna por
  respuesta (`answers.<clave>`), en orden estable.
- **Excel sin dependencias nuevas**: `.xlsx` es un ZIP con XML. Se escribe la
  hoja mínima válida a mano en vez de añadir `openpyxl` (otra dependencia en
  la cadena de suministro, OWASP A03) para una tabla plana.
- **Coste fijo en queries**: `select_related` y la última revisión como
  subconsulta; cero queries por fila.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterable, Sequence
from typing import Any

from django.db.models import OuterRef, QuerySet, Subquery

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Visit, VisitReview
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "MAX_EXPORT_ROWS",
    "ExportTooLarge",
    "exportable_visits",
    "safe_cell",
    "to_csv",
    "to_xlsx",
    "visit_table",
]

MAX_EXPORT_ROWS = 10_000

#: Tipos de relación OOXML.
_REL_TYPES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: Caracteres que XML 1.0 no admite ni escapados (controles salvo \t \n \r):
#: uno solo en una respuesta dejaría el `.xlsx` ilegible.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _xml_text(value: str) -> str:
    """Texto de un nodo XML. Solo se **escribe** XML, nunca se parsea: no hace
    falta `xml.sax` (ni `defusedxml`) para esto."""
    value = _XML_INVALID.sub("", value)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_BASE_COLUMNS = (
    "id",
    "capturada_en",
    "plantilla",
    "version_plantilla",
    "creada_por",
    "dispositivo",
    "lat",
    "lon",
    "precision_m",
    "revision",
)


class ExportTooLarge(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(
            f"La selección tiene {count} visitas y el máximo exportable es {MAX_EXPORT_ROWS}. "
            "Filtra por fecha, plantilla u organización y vuelve a exportar."
        )
        self.count = count


def safe_cell(value: Any) -> Any:
    """Valor listo para una celda: números tal cual, el resto como texto seguro."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, int | float):
        return value
    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def exportable_visits(ctx: RequestContext, queryset: QuerySet[Visit]) -> QuerySet[Visit]:
    """Las visitas vivas de [queryset] **de la organización de [ctx]** (las de
    otra se ignoran: nunca salen en un archivo ajeno; las borradas tampoco),
    en orden estable. Lanza [ExportTooLarge]. Común a todos los formatos."""
    queryset = queryset.filter(org_id=ctx.org_id, deleted_at__isnull=True)
    count = queryset.count()
    if count > MAX_EXPORT_ROWS:
        raise ExportTooLarge(count)
    return queryset.select_related("template_version", "created_by").order_by("captured_at", "id")


def visit_table(
    ctx: RequestContext, queryset: QuerySet[Visit]
) -> tuple[list[str], list[list[Any]]]:
    """Cabecera y filas de [exportable_visits]. Lanza [ExportTooLarge]."""
    latest_review = (
        scoped_qs(ctx, VisitReview)
        .filter(visit=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    visits = list(exportable_visits(ctx, queryset).annotate(review_status=Subquery(latest_review)))
    answer_keys = sorted({key for visit in visits for key in (visit.answers or {})})
    header = [*_BASE_COLUMNS, *(f"answers.{key}" for key in answer_keys)]
    rows = [
        [
            safe_cell(value)
            for value in (
                str(visit.pk),
                visit.captured_at.isoformat(),
                visit.template_version.name or str(visit.template_version.template_id),
                visit.template_version.version_number,
                visit.created_by.get_username(),
                str(visit.device_id),
                visit.lat,
                visit.lon,
                visit.accuracy_m,
                visit.review_status,
                *((visit.answers or {}).get(key) for key in answer_keys),
            )
        ]
        for visit in visits
    ]
    return header, rows


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    """CSV UTF-8 con BOM: Excel lo abre con acentos correctos sin asistente."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _xlsx_cell(ref: str, value: Any) -> str:
    if isinstance(value, bool):
        value = "sí" if value else "no"
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value!r}</v></c>'
    text = _xml_text(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def to_xlsx(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    """Libro `.xlsx` mínimo válido: una hoja, cadenas en línea y números."""
    sheet_rows = []
    for row_index, row in enumerate([list(header), *rows], start=1):
        cells = "".join(
            _xlsx_cell(f"{_column_name(col)}{row_index}", value) for col, value in enumerate(row)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            f'Type="{_REL_TYPES}/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Visitas" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            f'Type="{_REL_TYPES}/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()

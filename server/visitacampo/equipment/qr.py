"""Identificación QR de equipos.

El QR codifica una ficha de texto legible con cualquier lector (cámara del móvil
incluida): la primera línea es el identificador estable `suno:eq:<uuid>` y las
siguientes repiten los datos de identificación del equipo (código, nombre, tipo,
fabricante, modelo, serie, placa, año, propiedad y propietario). Nunca incluye
secretos, notas ni especificaciones libres.

SUNO resuelve el equipo solo por el UUID de la primera línea y siempre exige sesión
y alcance vigente (`visible_equipment`); los datos impresos son una copia de cortesía
al momento de imprimir y pueden quedar desactualizados si la ficha cambia, mientras
que el UUID sigue apuntando a la ficha viva. Regenera la etiqueta si cambian.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING
from uuid import UUID

import segno

if TYPE_CHECKING:
    from visitacampo.core.models import Equipment

PAYLOAD_PREFIX = "suno:eq:"
MAX_SCAN_LENGTH = 2048

# Paleta de la plataforma (tokens.css): tinta sobre papel blanco para máximo
# contraste de lectura; el rojo de marca se reserva para la etiqueta, no para los
# módulos del QR (un módulo rojo baja el contraste en cámaras con poca luz).
INK = "#171717"
PAPER = "#ffffff"

# Nivel Q (~25 % de redundancia): tolera suciedad y desgaste de obra sin hacer
# el QR tan denso que cueste leerlo a distancia.
ERROR_LEVEL = "q"
QUIET_ZONE = 4  # módulos; mínimo exigido por la norma ISO/IEC 18004

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def equipment_identifier(equipment_id: UUID) -> str:
    return f"{PAYLOAD_PREFIX}{equipment_id}"


def _line(value: object) -> str:
    return " ".join(str(value).split())


def equipment_payload(equipment: Equipment) -> str:
    """Texto completo del QR: identificador estable + datos de la ficha."""

    fields = [
        ("Código", equipment.code),
        ("Nombre", equipment.name),
        ("Tipo", equipment.equipment_type.name),
        ("Fabricante", equipment.manufacturer),
        ("Modelo", equipment.model),
        ("Serie", equipment.serial_number),
        ("Placa", equipment.license_plate),
        ("Año", equipment.manufacture_year or ""),
        ("Propiedad", equipment.get_ownership_display()),
        ("Propietario", equipment.owner_name),
    ]
    lines = [equipment_identifier(equipment.pk)]
    lines += [f"{label}: {_line(value)}" for label, value in fields if _line(value)]
    return "\n".join(lines)


_ID_RE = re.compile(r"suno:eq:(" + _UUID_RE.pattern + ")", re.IGNORECASE)


def parse_identifier(raw: str) -> UUID | None:
    """Extrae el UUID de un QR escaneado, un UUID suelto o una URL de ficha.

    Del texto del QR solo cuenta el primer `suno:eq:<uuid>`: lo demás es informativo.
    Devuelve `None` si no hay identificador; entonces se trata como búsqueda manual.
    """

    text = raw.strip()
    if not text or len(text) > MAX_SCAN_LENGTH:
        return None
    match = _ID_RE.search(text)
    if match:
        return UUID(match.group(1))
    if len(text) > 512:
        return None
    try:
        return UUID(text)
    except ValueError:
        pass
    if "/equipos/" in text:
        url_match = _UUID_RE.search(text)
        if url_match:
            return UUID(url_match.group(0))
    return None


def _qr(payload: str) -> segno.QRCode:
    return segno.make(payload, error=ERROR_LEVEL, micro=False, boost_error=False)


def render_svg(payload: str, *, scale: int = 8, title: str = "") -> bytes:
    """SVG con `viewBox`: escala sin recortes al fijar su tamaño por CSS."""

    buffer = io.BytesIO()
    qr = _qr(payload)
    qr.save(
        buffer,
        kind="svg",
        scale=1,
        border=QUIET_ZONE,
        dark=INK,
        light=PAPER,
        xmldecl=False,
        svgns=True,
        nl=False,
        title=title or None,
        svgclass=None,
        lineclass=None,
    )
    side = qr.symbol_size(border=QUIET_ZONE)[0]
    svg = buffer.getvalue().decode("utf-8")
    svg = re.sub(
        r'width="\d+" height="\d+"',
        f'viewBox="0 0 {side} {side}" width="{side * scale}" height="{side * scale}"',
        svg,
        count=1,
    )
    return svg.encode("utf-8")


def render_png(payload: str, *, scale: int = 12) -> bytes:
    buffer = io.BytesIO()
    _qr(payload).save(
        buffer,
        kind="png",
        scale=scale,
        border=QUIET_ZONE,
        dark=INK,
        light=PAPER,
    )
    return buffer.getvalue()

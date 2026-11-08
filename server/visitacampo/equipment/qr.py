"""Identificación QR de equipos.

El QR codifica únicamente un identificador estable (`suno:eq:<uuid>`): no lleva
secretos, ni la organización, ni datos de la ficha. Escanearlo no concede
acceso; quien lo lee debe estar autenticado y el equipo debe estar dentro de su
alcance (`visible_equipment`). Si el equipo se edita (código, placa, estado) el
QR impreso sigue siendo válido porque solo depende del UUID.
"""

from __future__ import annotations

import io
import re
from uuid import UUID

import segno

PAYLOAD_PREFIX = "suno:eq:"

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


def equipment_payload(equipment_id: UUID) -> str:
    return f"{PAYLOAD_PREFIX}{equipment_id}"


def parse_identifier(raw: str) -> UUID | None:
    """Extrae el UUID de un payload escaneado, un UUID suelto o una URL de ficha.

    Devuelve `None` si el texto no contiene un identificador de equipo; en ese caso
    quien llama debe tratarlo como búsqueda manual (código, serie o placa).
    """

    text = raw.strip()
    if not text or len(text) > 512:
        return None
    if text.lower().startswith(PAYLOAD_PREFIX):
        text = text[len(PAYLOAD_PREFIX) :]
        try:
            return UUID(text)
        except ValueError:
            return None
    try:
        return UUID(text)
    except ValueError:
        pass
    if "/equipos/" in text:
        match = _UUID_RE.search(text)
        if match:
            return UUID(match.group(0))
    return None


def _qr(payload: str) -> segno.QRCode:
    return segno.make(payload, error=ERROR_LEVEL, micro=False, boost_error=False)


def render_svg(payload: str, *, scale: int = 8, title: str = "") -> bytes:
    buffer = io.BytesIO()
    _qr(payload).save(
        buffer,
        kind="svg",
        scale=scale,
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
    return buffer.getvalue()


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

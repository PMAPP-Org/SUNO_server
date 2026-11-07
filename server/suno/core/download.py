"""Nombres de archivo seguros y cabecera `Content-Disposition` para descargas."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

_ASCII_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_UNICODE_UNSAFE = re.compile(r"[^\w.-]")


def ascii_filename(text: str, fallback: str = "archivo", *, limit: int = 60) -> str:
    """Texto apto para un nombre de archivo ASCII (sin tildes ni símbolos)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _ASCII_UNSAFE.sub("_", plain)[:limit] or fallback


def unicode_filename(text: str, fallback: str = "archivo", *, limit: int = 60) -> str:
    """Como `ascii_filename`, pero conserva letras y dígitos de cualquier idioma."""
    return _UNICODE_UNSAFE.sub("_", text)[:limit] or fallback


def content_disposition(stem: str, suffix: str, *, fallback: str = "archivo") -> str:
    """`attachment` con `filename=` ASCII y, si el nombre trae letras no ASCII,
    `filename*=UTF-8''…` (RFC 5987) para que se conserven."""
    ascii_name = f"{ascii_filename(stem, fallback)}{suffix}"
    header = f'attachment; filename="{ascii_name}"'
    full = f"{unicode_filename(stem, fallback)}{suffix}"
    if full != ascii_name:
        header += f"; filename*=UTF-8''{quote(full, safe='')}"
    return header

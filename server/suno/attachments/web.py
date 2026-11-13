"""Soportes subidos desde la plataforma web (costos, pieza 6a).

El flujo del teléfono (presign → `PUT` → commit) no sirve en un formulario web: aquí el
archivo llega al servidor, se reconoce por sus primeros bytes (la misma lista de tipos que los
adjuntos de campo), se calcula su `sha256`, se sube con `BlobStore.put_blob` y se registra el
`Blob` con el mismo `INSERT … ON CONFLICT DO NOTHING` del commit.

**No autoriza**: `Blob` no tiene organización. Quien llama ya comprobó que el usuario puede
escribir el objeto al que lo va a vincular (un movimiento del libro de costos).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from typing import Protocol

from suno.attachments import validation
from suno.attachments.blobstore import BlobStore
from suno.attachments.services import register_server_upload
from suno.attachments.storage import get_blob_store
from suno.core.models import Blob
from suno.core.scoping import blob_registry
from suno.serverinfo import capabilities as caps

__all__ = ["ACCEPT", "MAX_BYTES", "Upload", "UploadRejected", "sniff_mime", "store_upload"]

MAX_BYTES = caps.ATTACHMENT_MAX_BYTES
#: Para el `accept` del `<input type="file">`.
ACCEPT = "application/pdf,image/jpeg,image/png,image/webp"


class Upload(Protocol):
    """Lo que se usa de un `UploadedFile` de Django."""

    size: int | None

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]: ...


class UploadRejected(ValueError):
    """El archivo no se acepta; el mensaje es para el usuario."""


def sniff_mime(prefix: bytes) -> str | None:
    """El tipo permitido con el que empiezan los bytes, o `None`."""
    for mime in validation.allowed_mime_types():
        if validation.magic_bytes_match(prefix, mime):
            return str(mime)
    return None


def store_upload(upload: Upload, *, store: BlobStore | None = None) -> Blob:
    size = upload.size or 0
    if size <= 0:
        raise UploadRejected("El soporte está vacío.")
    if size > MAX_BYTES:
        raise UploadRejected(f"El soporte pesa más de {MAX_BYTES // (1024 * 1024)} MB.")
    digest = hashlib.sha256()
    head = b""
    handle, path = tempfile.mkstemp(prefix="suno-soporte-")
    try:
        with os.fdopen(handle, "wb") as target:
            for chunk in upload.chunks():
                if len(head) < validation.SNIFF_LENGTH:
                    head += chunk[: validation.SNIFF_LENGTH - len(head)]
                digest.update(chunk)
                target.write(chunk)
        mime = sniff_mime(head)
        if mime is None:
            raise UploadRejected("El soporte debe ser un PDF o una imagen JPG, PNG o WebP.")
        sha256 = digest.hexdigest()
        existing = blob_registry().filter(pk=sha256).first()
        if existing is not None:
            return existing
        (store or get_blob_store()).put_blob(sha256=sha256, path=path, size=size, mime=mime)
        return register_server_upload(sha256, size=size, mime=mime).blob
    finally:
        os.unlink(path)

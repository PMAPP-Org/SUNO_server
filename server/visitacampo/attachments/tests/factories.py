"""Contenido de ejemplo y escenario compartido por los tests del Paso 17.

Los bytes de ejemplo son **de verdad** los magic bytes de cada tipo, no
cadenas cualquiera: los tests de commit tienen que atravesar el sniffing real,
y una fixture de `b"contenido"` probaría el camino de rechazo en todos los
casos sin que nadie se diera cuenta.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from visitacampo.sync.tests.factories import Scenario, build_scenario

__all__ = [
    "JPEG_BYTES",
    "JPEG_MIME",
    "PDF_BYTES",
    "PDF_MIME",
    "PNG_BYTES",
    "PNG_MIME",
    "WEBP_BYTES",
    "WEBP_MIME",
    "Scenario",
    "build_scenario",
    "commit_url",
    "digest_of",
    "download_url",
    "make_visit",
    "presign_body",
]

PRESIGN_URL = "/v1/attachments/presign"

# Los MIME como **literales de cadena** y no como `MimeType.X`: mypy lee los
# miembros de un `models.TextChoices` de Django como la tupla del cuerpo de la
# clase, no como el `str` que son en ejecución (ver `validation.MimeValue`), y
# los adaptadores del puerto declaran `mime: str` con toda la razón — un object
# storage no sabe nada de la allow-list del dominio.
# `test_validation.py::ContractMimeTests` comprueba que estos cuatro valores
# son exactamente los de `MimeType`, así que no pueden divergir en silencio.
JPEG_MIME = "image/jpeg"
PNG_MIME = "image/png"
WEBP_MIME = "image/webp"
PDF_MIME = "application/pdf"

#: JPEG mínimo viable: SOI + APP0 (JFIF) + EOI. Empieza por `FF D8 FF`.
JPEG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010100000100010000") + b"\xff\xd9"

#: PNG: la firma de 8 bytes de la especificación + un IHDR de relleno.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(24)

#: WebP: contenedor RIFF — `RIFF`, 4 bytes de tamaño, `WEBP`.
WEBP_BYTES = b"RIFF" + (32).to_bytes(4, "little") + b"WEBPVP8 " + bytes(20)

#: PDF: cabecera de versión + un poco de cuerpo.
PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"

#: Bytes que no son ninguno de los cuatro tipos: para el test de sniffing.
DISGUISED_BYTES = b"MZ\x90\x00\x03" + bytes(16)  # cabecera de un .exe de Windows


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def presign_body(
    data: bytes = JPEG_BYTES,
    mime: str = JPEG_MIME,
    visit_id: uuid.UUID | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sha256": digest_of(data),
        "size": len(data),
        "mime": mime,
    }
    if visit_id is not None:
        body["visit_id"] = str(visit_id)
    body.update(overrides)
    return body


def commit_url(sha256: str) -> str:
    return f"/v1/attachments/{sha256}/commit"


def download_url(sha256: str) -> str:
    return f"/v1/attachments/{sha256}/url"


def make_visit(scenario: Scenario, visit_id: uuid.UUID | None = None):
    """Una visita mínima de la organización del escenario, escrita directamente.

    Se crea con el ORM en vez de por `POST /v1/sync/push` a propósito: lo que
    estos tests ejercitan es el carril de adjuntos, y hacerlo depender del
    formato del push convertiría cualquier cambio de aquel en un fallo de aquí.
    """
    from visitacampo.core.models import Device, Visit

    # El `login()` del escenario ya registró el dispositivo (`_register_device`
    # de authn/services.py), así que aquí se recupera; crearlo otra vez con
    # valores inventados haría divergir la fixture del alta real.
    device = Device.objects.get(pk=scenario.device_id)
    return Visit.objects.create(
        id=visit_id or uuid.uuid4(),
        org=scenario.org,
        template_version=scenario.template_version,
        answers={"altura_cm": 10},
        captured_at="2026-07-30T14:05:09.123Z",
        device=device,
        created_by=scenario.user,
        change_seq=100,
    )

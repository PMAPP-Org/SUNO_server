"""Formas del contrato de `/v1/attachments/*` (`server/openapi/v1.yaml`).

El contrato del Paso 1 es la fuente de verdad: aquí no se inventa ningún campo
ni se renombra ninguno. Dos decisiones se apartan de la traducción literal del
YAML y las dos las pide el propio contrato:

1. **`size` no lleva el tope máximo en Pydantic**, solo `ge=1`. El esquema
   declara `minimum: 1` y nada más, y el tope por archivo vive en
   `serverinfo.capabilities` porque el contrato lo describe como configurable
   ("se leen del servidor en lugar de codificarse en la app"). Si el tope
   viviera aquí, Ninja respondería `422` y el cliente no recibiría el
   `413 payload_too_large` que le dice que ese archivo no cabe — exactamente el
   mismo razonamiento que `sync/schemas.py` aplica al tamaño del lote.

2. **`mime` es un `Literal` cerrado y no un `str`.** Aquí sí interesa el `422`
   de Pydantic: `components.schemas.Mime` es un `enum` y un valor fuera de él
   no es "un adjunto que no cabe" sino una petición que no valida contra el
   contrato. La allow-list se importa de `MimeType` en vez de repetirse para
   que el modelo, el contrato y el validador no puedan divergir.

`Sha256Path` merece su propio tipo: el `sha256` llega por la **ruta**, que es
el único punto de este paquete donde una cadena del cliente entra sin haber
pasado por un cuerpo JSON validado. Se le aplica el mismo `pattern` que el
contrato (`^[0-9a-f]{64}$`), de modo que un intento de traversal muere en el
borde HTTP con un `422` y no llega ni al servicio.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from ninja import Schema
from pydantic import Field

from visitacampo.authn.schemas import Timestamp

__all__ = [
    "SHA256_REGEX",
    "BlobSchema",
    "CommitRequest",
    "DownloadUrlResponse",
    "Mime",
    "PresignRequest",
    "PresignResponse",
    "Sha256",
]

#: `components.schemas.Sha256`, literal.
SHA256_REGEX = r"^[0-9a-f]{64}$"

Sha256 = Annotated[str, Field(pattern=SHA256_REGEX)]

#: `components.schemas.Mime`.
#:
#: **Los cuatro valores van como literales de cadena y no como `MimeType.X`**, a
#: diferencia del resto del paquete, y no por gusto: `Literal[...]` exige
#: valores constantes en tiempo de análisis, y los miembros de un
#: `models.TextChoices` no lo son para mypy (los lee como la tupla del cuerpo
#: de la clase — la misma limitación que documenta `validation.MimeValue`).
#: Escribirlos aquí es la única forma de que Pydantic genere el `enum` del
#: contrato **y** el tipo sea comprobable.
#:
#: La divergencia que esto podría introducir —que alguien añada un tipo a
#: `MimeType` y se olvide de este `Literal`— la cierra
#: `tests/test_validation.py::ContractMimeTests`, que compara los dos
#: conjuntos y falla la build si dejan de coincidir.
Mime = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]


class PresignRequest(Schema):
    """`components.schemas.PresignRequest`."""

    sha256: Sha256
    size: int = Field(ge=1)
    mime: Mime
    #: Opcional en el contrato: el dispositivo puede pedir la firma antes de
    #: decidir a qué visita adjunta la foto. Cuando viene, el servicio
    #: comprueba además permisos sobre esa visita y su cuota acumulada.
    visit_id: uuid.UUID | None = None


class PresignResponse(Schema):
    """`components.schemas.PresignResponse`.

    Los tres campos opcionales son `null` **a la vez** cuando
    `already_present` es `true`: el contrato lo describe así y el cliente lo
    usa como señal para marcar el adjunto como subido sin gastar un byte.
    """

    already_present: bool
    upload_url: str | None = None
    expires_in: int | None = None
    required_headers: dict[str, str] | None = None
    server_time: Timestamp


class CommitRequest(Schema):
    """`components.schemas.CommitRequest`. El `sha256` va en la ruta, no aquí."""

    size: int = Field(ge=1)
    mime: Mime


class BlobSchema(Schema):
    """`components.schemas.Blob`.

    Sin `bucket_key`: es un detalle interno del almacenamiento y el contrato no
    lo declara. Filtrarlo al cliente le daría una ruta de objeto que no puede
    usar (el bucket no es público) y que ataría el cliente a un esquema de
    rutas que este servidor debe poder cambiar.
    """

    sha256: str
    size: int
    mime: Mime
    created_at: Timestamp


class DownloadUrlResponse(Schema):
    """`components.schemas.DownloadUrlResponse`."""

    url: str
    expires_in: int
    mime: Mime
    size: int
    server_time: Timestamp

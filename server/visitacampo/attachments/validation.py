"""Allow-list de MIME, *sniffing* de magic bytes y nombre generado localmente.

Tarea 5 y tarea 6 del Paso 17 (ADR-007 punto 5, RD-06). Módulo **puro**: sin
I/O, sin base de datos, sin settings. Recibe bytes y cadenas, devuelve
decisiones; quien los consigue es `services.py`.

**Por qué no se confía en el `Content-Type` declarado.** Es un dato del
cliente: lo elige él en el `PUT`, y el object storage lo almacena tal cual sin
mirarlo. Un `.exe` anunciado como `image/jpeg` pasaría la allow-list, quedaría
registrado con `mime='image/jpeg'` y el back-office lo serviría con ese tipo.
Los *magic bytes* son lo único que habla de los bytes de verdad, y por eso el
commit exige que **las tres** fuentes coincidan: lo que declara el cuerpo del
commit, lo que guardó el store (`Content-Type` del `HEAD`) y lo que dicen los
primeros bytes del objeto.

**Sniffing acotado a propósito.** No se intenta reconocer "qué es realmente"
un archivo desconocido —eso es un detector de tipos, con su propia superficie
de bugs—, sino solo confirmar que los bytes empiezan como el tipo declarado.
Un archivo que no case se rechaza; no se "corrige" su MIME. Deny-by-default
también aquí.
"""

from __future__ import annotations

import uuid
from typing import Any

from visitacampo.core.models import MimeType

__all__ = [
    "EXTENSION_BY_MIME",
    "SNIFF_LENGTH",
    "MimeRejected",
    "MimeValue",
    "allowed_mime_types",
    "check_mime_allowed",
    "generated_filename",
    "is_allowed_mime",
    "magic_bytes_match",
]

# Tipo de un MIME de la allow-list. En ejecución es siempre el `str` del valor
# de `MimeType` (`"image/jpeg"`…), que es lo que guarda la columna `blob.mime`.
# Se declara como `Any` por la **misma razón exacta** que `RoleValue` en
# `core/context.py`, y conviene no volver a descubrirla: mypy no reconoce
# `models.TextChoices` y lee `MimeType.IMAGE_JPEG` como la tupla
# `("image/jpeg", "JPEG")` del cuerpo de la clase, no como el `str` que
# realmente es. Tiparlo como `str` obligaría a sembrar de `# type: ignore` cada
# uso del enum —ruido que ocultaría los errores de tipo de verdad— o a repetir
# los cuatro literales aquí, que es justo la divergencia que derivarlos de
# `MimeType` evita.
MimeValue = Any

#: Bytes que hay que leer del objeto para poder decidir. 16 son suficientes
#: para el más largo de los patrones (WebP: `RIFF` + 4 bytes de tamaño +
#: `WEBP`, 12 en total) con margen, y son lo bastante pocos como para que la
#: lectura no se parezca en nada a descargar el adjunto (H-05).
SNIFF_LENGTH = 16

#: Extensión canónica por MIME. **No** se deriva del nombre que traiga el
#: cliente —no hay tal nombre en el contrato— sino del MIME ya verificado.
EXTENSION_BY_MIME: dict[MimeValue, str] = {
    MimeType.IMAGE_JPEG: "jpg",
    MimeType.IMAGE_PNG: "png",
    MimeType.IMAGE_WEBP: "webp",
    MimeType.APPLICATION_PDF: "pdf",
}

#: Firmas por MIME, como lista de `(offset, bytes esperados)`. Un tipo casa si
#: **alguna** de sus firmas casa entera.
#:
#: - JPEG: `FF D8 FF` (SOI + primer marcador). Los tres bytes son comunes a
#:   JFIF, Exif y raw.
#: - PNG: la firma de 8 bytes de la especificación, que incluye el `\r\n`
#:   y el `\x1a` justamente para detectar transferencias corrompidas.
#: - WebP: contenedor RIFF — `RIFF` en 0 y `WEBP` en 8; los cuatro bytes de
#:   en medio son el tamaño y varían.
#: - PDF: `%PDF-` al principio. Se exige en offset 0 (la especificación admite
#:   basura previa, pero un PDF que la traiga no es lo que un agente de campo
#:   acaba de generar, y aceptarla abriría la puerta a poliglotas).
_MAGIC_SIGNATURES: dict[MimeValue, tuple[tuple[tuple[int, bytes], ...], ...]] = {
    MimeType.IMAGE_JPEG: (((0, b"\xff\xd8\xff"),),),
    MimeType.IMAGE_PNG: (((0, b"\x89PNG\r\n\x1a\n"),),),
    MimeType.IMAGE_WEBP: (((0, b"RIFF"), (8, b"WEBP")),),
    MimeType.APPLICATION_PDF: (((0, b"%PDF-"),),),
}


class MimeRejected(ValueError):
    """El MIME no está en la allow-list del contrato (`components.schemas.Mime`)."""


def allowed_mime_types() -> tuple[MimeValue, ...]:
    """La allow-list, derivada de `MimeType` para que no pueda divergir del modelo."""
    return tuple(MimeType.values)


def is_allowed_mime(mime: MimeValue) -> bool:
    return mime in EXTENSION_BY_MIME


def check_mime_allowed(mime: MimeValue) -> MimeValue:
    """Devuelve el MIME si está permitido; si no, lanza `MimeRejected`."""
    if not is_allowed_mime(mime):
        raise MimeRejected(
            f"MIME no permitido para adjuntos: {mime!r}. "
            f"Permitidos: {', '.join(allowed_mime_types())}."
        )
    return mime


def magic_bytes_match(prefix: bytes, mime: MimeValue) -> bool:
    """`True` si `prefix` empieza como un objeto de tipo `mime`.

    Un MIME fuera de la allow-list devuelve `False` en vez de lanzar: esta
    función responde "¿casan los bytes?", y para un tipo que no se acepta la
    respuesta honesta es que no. Quien tenga que explicar *por qué* se rechaza
    un tipo es `check_mime_allowed`, y el commit lo llama antes.
    """
    signatures = _MAGIC_SIGNATURES.get(mime)
    if signatures is None:
        return False
    return any(
        all(prefix[offset : offset + len(expected)] == expected for offset, expected in signature)
        for signature in signatures
    )


def generated_filename(mime: MimeValue) -> str:
    """`{uuid}.{ext}` — **RD-06**, tarea 6 del Paso 17.

    El nombre con el que el navegador del back-office guardará el archivo se
    genera aquí, entero, a partir de un UUID nuevo y de la extensión que
    corresponde al MIME **ya verificado**. No hay ningún parámetro por el que
    entre texto del cliente, que es la forma más simple de garantizar que
    H-06/H-21 no tienen superficie: no es que el nombre del cliente se sanee,
    es que no existe.

    Nótese que este nombre **no** es la clave del objeto: esa la deriva
    `blobstore.bucket_key_for()` del hash, y es estable e inmutable. Este es
    solo la sugerencia del `Content-Disposition`, y que cambie en cada descarga
    es irrelevante.
    """
    return f"{uuid.uuid4().hex}.{EXTENSION_BY_MIME[check_mime_allowed(mime)]}"

"""El **puerto** `BlobStore` y la derivación de claves de objeto (ADR-007, C-13).

Aquí no hay ni una línea de I/O: solo los tipos de datos que cruzan la
frontera, los errores que los adaptadores pueden lanzar, y la función que
convierte un `sha256` en una clave de objeto. Los adaptadores viven en `s3.py`
(producción) y `localfs.py` (tests/desarrollo sin red).

**Por qué el `Protocol` tiene seis métodos y no los tres que nombra el plan**
(`presign_upload`, `head`, `presign_download`). Los tres son el flujo del
contrato; los otros tres existen porque dos tareas del propio paso los exigen y
no se pueden cumplir sin ellos:

- `read_prefix` — la tarea 5 pide *sniffing de magic bytes en servidor*. No se
  puede sniffear sin bytes, y un `HEAD` solo devuelve metadatos. Se lee un
  prefijo diminuto (`validation.SNIFF_LENGTH` bytes), nunca el objeto entero:
  el punto 1 del `__init__` de este paquete sigue intacto.
- `list_objects` / `delete` — la tarea 7 pide el GC de subidas nunca
  confirmadas. Enumerar y borrar es literalmente lo que un GC hace.

**Por qué todos los métodos hablan de `sha256` y no de claves.** Es la decisión
que cierra H-06/H-21 por construcción: si el puerto aceptara una clave, cada
adaptador tendría que acordarse de validarla, y "acordarse" es exactamente lo
que falla. Aceptando solo un `sha256` —que `normalize_sha256()` obliga a ser 64
caracteres `[0-9a-f]`— **no existe** ninguna entrada del cliente capaz de
expresar `../`, una ruta absoluta, un byte nulo o una unidad de Windows. La
única función que compone una ruta es `bucket_key_for()`, y su entrada ya está
validada. `list_objects` es la excepción inevitable (el store devuelve claves
que él mismo escribió); por eso `StoredObject.sha256` es `str | None` y el GC
**nunca borra** un objeto cuya clave no revierta a un `sha256` válido.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "BASEMAP_KEY_PREFIX",
    "LAYER_KEY_PREFIX",
    "BLOB_KEY_PREFIX",
    "SHA256_PATTERN",
    "BlobStore",
    "BlobStoreError",
    "InvalidSha256",
    "ObjectHead",
    "ObjectNotFound",
    "PresignedUpload",
    "StoredObject",
    "basemap_key_for",
    "layer_key_for",
    "bucket_key_for",
    "checksum_header_value",
    "normalize_sha256",
    "sha256_from_bucket_key",
    "sha256_from_checksum_header",
]

#: `components.schemas.Sha256` del contrato, palabra por palabra.
SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")

#: Prefijo de todas las claves de blob (ADR-007). Aislar los blobs bajo un
#: prefijo propio es lo que permite que el GC enumere sin tropezar con backups
#: u otros objetos que puedan compartir bucket.
BLOB_KEY_PREFIX = "blobs/"

#: Paquetes de mapa base (Fase B). Fuera de `BLOB_KEY_PREFIX` **a propósito**:
#: el GC de adjuntos borra todo lo de ese prefijo que no esté en la tabla
#: `blob`, y un mapa no es un adjunto (ni cabe en su allow-list de MIME).
BASEMAP_KEY_PREFIX = "basemaps/"

#: Capas de referencia GeoJSON (Fase C). Mismo motivo que los mapas base:
#: fuera de `BLOB_KEY_PREFIX`, el GC de adjuntos no las toca.
LAYER_KEY_PREFIX = "layers/"


class BlobStoreError(RuntimeError):
    """Fallo del almacenamiento de objetos. La raíz de todo lo que este puerto lanza."""


class ObjectNotFound(BlobStoreError):
    """El objeto no existe en el store.

    Se distingue de `BlobStoreError` porque el commit la traduce a `404` —"aún
    no has subido nada"— mientras que cualquier otro fallo del store es `500`:
    problema del servidor, el cliente debe reintentar.
    """


class InvalidSha256(ValueError):
    """El valor no es un SHA-256 hexadecimal en minúsculas de 64 caracteres.

    Hereda de `ValueError` para que un despiste que la deje escapar hasta la
    capa HTTP acabe en el `400` uniforme de `core/api_errors.py` y no en una
    página de error de Django. Pero el camino previsto es que ni la vista ni el
    servicio la vean nunca: el `sha256` de la ruta se valida en el esquema de
    Ninja con el mismo patrón del contrato.
    """


@dataclass(frozen=True, slots=True)
class ObjectHead:
    """Lo que un `HEAD` devuelve del objeto **realmente almacenado**.

    `sha256` es el checksum que **certifica el propio store**, no el que el
    cliente dijo: en S3/R2 viene de `x-amz-checksum-sha256`, que el servicio
    calcula sobre los bytes recibidos y con el que rechaza el `PUT` si no
    coincide con el declarado en la firma. Es `None` cuando el store no lo
    certifica (objeto subido sin checksum, o adaptador que no lo soporta), y
    ese `None` **se rechaza** en el commit: verificar es verificar, y "no pude
    comprobarlo" no es "está bien".
    """

    size: int
    content_type: str | None = None
    sha256: str | None = None
    last_modified: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Una entrada del listado del bucket, para el GC.

    `sha256` es `None` cuando la clave no tiene la forma que `bucket_key_for()`
    produce. El GC deja esos objetos en paz: borrar algo que este código no
    escribió sería exactamente el tipo de daño irreversible que un job
    automático no debe poder causar.
    """

    key: str
    size: int
    last_modified: datetime
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """URL prefirmada de `PUT` + las cabeceras que forman parte de la firma.

    `required_headers` se emite tal cual en `PresignResponse.required_headers`:
    el cliente debe enviarlas **exactamente**, porque el store recalcula la
    firma con ellas y rechaza el `PUT` si difieren. Ahí está la mitad de la
    verificación: el tamaño y el checksum quedan fijados **antes** de que exista
    un solo byte subido.
    """

    url: str
    expires_in: int
    required_headers: dict[str, str]


def normalize_sha256(value: str) -> str:
    """Devuelve el `sha256` validado, o lanza `InvalidSha256`.

    **No normaliza a minúsculas**: rechaza las mayúsculas. Aceptarlas obligaría
    a decidir qué pasa cuando el mismo contenido llega con dos grafías (dos
    claves de objeto para un blob, o un `already_present` que no dedupe), y el
    contrato ya cierra la pregunta declarando `^[0-9a-f]{64}$`. Una entrada
    fuera de esa forma es un cliente roto, no un caso a acomodar.
    """
    if not isinstance(value, str) or SHA256_PATTERN.match(value) is None:
        raise InvalidSha256(
            "El sha256 debe ser hexadecimal en minúsculas de 64 caracteres "
            "(components.schemas.Sha256 del contrato)."
        )
    return value


def bucket_key_for(sha256: str) -> str:
    """`blobs/{sha256[0:2]}/{sha256[2:4]}/{sha256}` — la única constructora de rutas.

    El particionado en dos niveles es el de ADR-007: reparte los objetos en
    65 536 prefijos, que es lo que evita que un listado por prefijo degenere
    cuando el bucket crece.

    La validación va **antes** de componer nada, y por eso este paquete no
    necesita ningún "saneado" de rutas: no hay entrada del cliente que
    sobreviva a `normalize_sha256()` y contenga un separador.
    """
    digest = normalize_sha256(sha256)
    return f"{BLOB_KEY_PREFIX}{digest[0:2]}/{digest[2:4]}/{digest}"


def basemap_key_for(sha256: str) -> str:
    """`basemaps/{sha256}.mbtiles`. Misma garantía que `bucket_key_for()`: la
    entrada se valida antes de componer, así que no hay ruta que inyectar."""
    return f"{BASEMAP_KEY_PREFIX}{normalize_sha256(sha256)}.mbtiles"


def layer_key_for(sha256: str) -> str:
    """`layers/{sha256}.geojson`, con la misma validación previa."""
    return f"{LAYER_KEY_PREFIX}{normalize_sha256(sha256)}.geojson"


def sha256_from_bucket_key(key: str) -> str | None:
    """El `sha256` de una clave con la forma de `bucket_key_for()`, o `None`.

    Deliberadamente estricta: exige el prefijo, los dos niveles de partición y
    que además **coincidan** con el hash. Una clave `blobs/ff/ff/<hash que
    empieza por aa>` devuelve `None`, porque no la escribió `bucket_key_for()`
    y el GC no tiene por qué saber qué es.
    """
    if not key.startswith(BLOB_KEY_PREFIX):
        return None
    parts = key[len(BLOB_KEY_PREFIX) :].split("/")
    if len(parts) != 3:
        return None
    first, second, digest = parts
    if SHA256_PATTERN.match(digest) is None:
        return None
    if first != digest[0:2] or second != digest[2:4]:
        return None
    return digest


def checksum_header_value(sha256: str) -> str:
    """El `sha256` hexadecimal en la forma que exige `x-amz-checksum-sha256`: base64.

    S3 (y R2) transportan los checksums como base64 de los bytes crudos del
    digest, no como hexadecimal. Convertir en un solo sitio evita que la firma
    y la verificación usen codificaciones distintas — un fallo que se
    manifestaría como "todos los commits dan mismatch" y costaría horas.
    """
    return base64.b64encode(bytes.fromhex(normalize_sha256(sha256))).decode("ascii")


def sha256_from_checksum_header(value: str) -> str | None:
    """Inversa de `checksum_header_value`, tolerante a basura.

    Devuelve `None` en vez de lanzar: un store que responda un checksum con
    otra forma (o de otro algoritmo, o troceado como `...-3` de una subida
    multiparte) no es un error de programación, es simplemente un checksum que
    no se puede comparar — y el commit ya sabe qué hacer con eso: rechazar.
    """
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 32:
        return None
    return raw.hex()


class BlobStore(Protocol):
    """El puerto. Dos adaptadores: `S3BlobStore` y `LocalFsBlobStore`.

    Se declara como `Protocol` y no como clase base abstracta a propósito: los
    adaptadores no comparten ni una línea de implementación (uno firma URLs con
    SigV4, el otro escribe archivos), así que una base común solo serviría para
    heredar de ella. Con un `Protocol`, además, un doble de test es un objeto
    cualquiera que tenga los métodos, sin importar nada de este módulo.
    """

    def presign_upload(
        self, *, sha256: str, size: int, mime: str, expires_in: int
    ) -> PresignedUpload:
        """URL de `PUT` con tamaño, tipo y checksum **fijados en la firma**."""
        ...

    def head(self, sha256: str) -> ObjectHead:
        """Metadatos del objeto ya subido. Lanza `ObjectNotFound` si no existe."""
        ...

    def presign_download(self, *, sha256: str, expires_in: int, mime: str, filename: str) -> str:
        """URL de `GET` de corta duración. `filename` lo **genera el servidor**."""
        ...

    def read_prefix(self, sha256: str, length: int) -> bytes:
        """Los primeros `length` bytes del objeto, para el sniffing de magic bytes."""
        ...

    def list_objects(self) -> Iterator[StoredObject]:
        """Enumera los objetos bajo `BLOB_KEY_PREFIX`. Solo lo usa el GC."""
        ...

    def delete(self, sha256: str) -> None:
        """Borra el objeto. Idempotente: borrar lo que no existe no es un error."""
        ...

    # --- mapas base (Fase B) ---------------------------------------------------

    def put_basemap(self, *, sha256: str, path: str, size: int) -> None:
        """Sube un paquete de mapa **desde el servidor** (comando de importación).

        El store verifica el `sha256` al recibirlo (checksum en la petición):
        lo que queda guardado es exactamente lo que se validó.
        """
        ...

    def presign_basemap_download(self, *, sha256: str, expires_in: int, filename: str) -> str:
        """URL de `GET` de corta duración del paquete."""
        ...

    # --- capas de referencia (Fase C) -------------------------------------------

    def put_layer(self, *, sha256: str, path: str, size: int) -> None:
        """Sube una capa GeoJSON desde el servidor, verificada por `sha256`."""
        ...

    def presign_layer_download(self, *, sha256: str, expires_in: int, filename: str) -> str:
        """URL de `GET` de corta duración de la capa."""
        ...

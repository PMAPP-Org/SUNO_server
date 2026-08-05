"""`LocalFsBlobStore`: el adaptador de tests y desarrollo sin red (ADR-007 punto 1).

**Qué es y qué no es.** Es una implementación completa del puerto sobre el
filesystem, que permite ejercitar offline **todo el lado servidor** del flujo
de adjuntos: derivación de claves, verificación de `HEAD` (tamaño, checksum,
`Content-Type`), lectura del prefijo para los magic bytes, registro idempotente
del blob y GC de subidas huérfanas.

Lo que **no** es: un servidor HTTP. `presign_upload`/`presign_download`
devuelven una URL **sintáctica**, con la forma y la caducidad correctas pero
sin nada escuchando al otro lado — el contrato exige un `format: uri` y esto lo
respeta, pero un cliente real no puede subir a ella. Se documenta aquí en vez
de disimularlo: en desarrollo con red se usa el MinIO que ya trae
`infra/docker-compose.override.dev.yml` (`STORAGE_BACKEND=s3`), y en tests los
bytes se depositan con `put_object()`, que es un método **de prueba** y no
forma parte del puerto.

Que `put_object()` acepte bytes que **no** casan con el `sha256` de la clave es
deliberado, no un descuido: es lo que permite escribir el test de "commit con
checksum que no coincide → rechazado y sin registrar el blob". S3/R2 rechazan
ese `PUT` en origen gracias al `x-amz-checksum-sha256` firmado, pero la
verificación del commit no puede depender de eso: un objeto puede haber
llegado al bucket por otra vía.

`head()` calcula el SHA-256 **leyendo el archivo por trozos**, no cargándolo en
memoria (RD-05). Es el equivalente local a que S3 certifique el checksum: el
valor que devuelve viene de los bytes reales, nunca de la clave.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from visitacampo.attachments.blobstore import (
    BLOB_KEY_PREFIX,
    BlobStoreError,
    ObjectHead,
    ObjectNotFound,
    PresignedUpload,
    StoredObject,
    bucket_key_for,
    checksum_header_value,
    normalize_sha256,
    sha256_from_bucket_key,
)

__all__ = ["LocalFsBlobStore"]

#: Trozo de lectura del hash. 64 KiB: ni una syscall por byte ni un archivo
#: entero en RAM.
_CHUNK = 64 * 1024

#: Sufijo del sidecar con los metadatos que el filesystem no guarda
#: (`Content-Type`). Va **junto** al objeto y no en una tabla porque el
#: adaptador tiene que poder responder un `HEAD` completo por sí solo.
_META_SUFFIX = ".meta.json"


class LocalFsBlobStore:
    """Adaptador del puerto `BlobStore` sobre un directorio local."""

    def __init__(self, root: str | os.PathLike[str], base_url: str = "local://blobs/") -> None:
        self._root = Path(root).resolve()
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"

    # --- rutas ---------------------------------------------------------------

    def _path(self, sha256: str) -> Path:
        """Ruta absoluta del objeto, con guarda de contención redundante.

        `bucket_key_for()` ya garantiza que la clave son 64 caracteres
        hexadecimales repartidos en dos niveles, así que esta comprobación
        **no puede fallar** hoy. Se mantiene igualmente, y a propósito: es la
        recomendación literal de H-21 (defensa en profundidad en el punto donde
        una ruta se resuelve de verdad), y su coste es una comparación de
        cadenas. Si algún día alguien relajara `normalize_sha256`, esto sería lo
        que impidiera que la relajación se convirtiera en un escape del
        directorio.
        """
        candidate = (self._root / bucket_key_for(sha256)).resolve()
        if not candidate.is_relative_to(self._root):
            raise BlobStoreError(
                "La ruta resuelta cae fuera del directorio de blobs; "
                "se aborta antes de tocar el filesystem."
            )
        return candidate

    # --- subida --------------------------------------------------------------

    def presign_upload(
        self, *, sha256: str, size: int, mime: str, expires_in: int
    ) -> PresignedUpload:
        return PresignedUpload(
            url=self._url(sha256, expires_in),
            expires_in=expires_in,
            required_headers={
                "Content-Length": str(size),
                "Content-Type": mime,
                "x-amz-checksum-sha256": checksum_header_value(sha256),
            },
        )

    def put_object(self, sha256: str, data: bytes, mime: str) -> None:
        """**Solo para tests/desarrollo**: deposita los bytes como haría el `PUT`.

        No pertenece al puerto: `services.py` no lo llama ni puede llamarlo a
        través de la interfaz. Existe para que un test pueda simular la subida
        directa del dispositivo sin levantar un object storage.
        """
        path = self._path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + _META_SUFFIX).write_text(
            json.dumps({"content_type": mime}), encoding="utf-8"
        )

    def set_last_modified(self, sha256: str, moment: datetime) -> None:
        """**Solo para tests**: envejece un objeto para poder ejercitar el GC."""
        stamp = moment.timestamp()
        os.utime(self._path(sha256), (stamp, stamp))

    # --- verificación ---------------------------------------------------------

    def head(self, sha256: str) -> ObjectHead:
        path = self._path(sha256)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise ObjectNotFound(f"El objeto {bucket_key_for(sha256)} no existe.") from exc
        return ObjectHead(
            size=stat.st_size,
            content_type=self._content_type(path),
            sha256=_digest_of(path),
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def read_prefix(self, sha256: str, length: int) -> bytes:
        path = self._path(sha256)
        try:
            with path.open("rb") as handle:  # el `with` cierra también si `read` falla
                return handle.read(length)
        except FileNotFoundError as exc:
            raise ObjectNotFound(f"El objeto {bucket_key_for(sha256)} no existe.") from exc

    # --- descarga -------------------------------------------------------------

    def presign_download(self, *, sha256: str, expires_in: int, mime: str, filename: str) -> str:
        return self._url(sha256, expires_in, filename=filename)

    # --- GC --------------------------------------------------------------------

    def list_objects(self) -> Iterator[StoredObject]:
        blobs_root = self._root / BLOB_KEY_PREFIX.rstrip("/")
        if not blobs_root.is_dir():
            return
        for path in sorted(blobs_root.rglob("*")):
            if not path.is_file() or path.name.endswith(_META_SUFFIX):
                continue
            key = path.relative_to(self._root).as_posix()
            stat = path.stat()
            yield StoredObject(
                key=key,
                size=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                sha256=sha256_from_bucket_key(key),
            )

    def delete(self, sha256: str) -> None:
        path = self._path(normalize_sha256(sha256))
        path.unlink(missing_ok=True)
        path.with_name(path.name + _META_SUFFIX).unlink(missing_ok=True)

    # --- interno ----------------------------------------------------------------

    def _url(self, sha256: str, expires_in: int, filename: str | None = None) -> str:
        query = f"expires={int(time.time()) + expires_in}"
        if filename is not None:
            query = f"{query}&filename={quote(filename)}"
        return f"{self._base_url}{bucket_key_for(sha256)}?{query}"

    @staticmethod
    def _content_type(path: Path) -> str | None:
        sidecar = path.with_name(path.name + _META_SUFFIX)
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = payload.get("content_type")
        return str(value) if isinstance(value, str) else None


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()

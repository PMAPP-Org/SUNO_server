"""`S3BlobStore`: el adaptador de producción (Cloudflare R2 por su API S3).

**Por qué `boto3` y no una implementación propia de SigV4.** Firmar una URL es
un algoritmo de una página (canonical request → string to sign → cadena de
HMACs) y escribirlo a mano habría evitado una dependencia. Se descartó: el
resultado de una firma es un hash: o coincide byte a byte o el store devuelve
`403`, y no hay forma de saber *por qué*. Una implementación propia sin acceso
a un R2 real solo se podría verificar contra vectores de prueba copiados a
mano, y cualquier detalle mal recordado —el orden de las cabeceras canónicas,
el `UNSIGNED-PAYLOAD`, el escapado del path— produciría un backend que **falla
en producción y pasa en local**. `boto3`/`botocore` es el cliente canónico de
la API S3, lo mantiene AWS, R2 lo documenta como el cliente recomendado, y
—decisivo aquí— **firma sin red**: `generate_presigned_url` es puro cálculo
local, así que los tests de este adaptador se ejecutan offline de verdad, no
con un mock que confirma lo que uno mismo escribió.

**Qué se firma en la subida** (ADR-007 punto 3-4, y la mitad de la
verificación del commit):

- `Content-Length` — el tamaño exacto. Un `PUT` con otro tamaño no valida.
- `Content-Type` — el MIME de la allow-list.
- `x-amz-checksum-sha256` — el checksum en base64. Esto es lo que hace real la
  promesa de "verificar el checksum con un `HEAD`": S3/R2 calculan el SHA-256
  de los bytes recibidos, **rechazan el `PUT`** si no coincide con el firmado,
  y luego devuelven ese checksum en el `HEAD` (con `ChecksumMode=ENABLED`). El
  servidor compara contra un valor que calculó el object storage, no contra
  algo que le contó el cliente.

**Configuración**: `signature_version='s3v4'` explícito (R2 solo soporta v4) y
`addressing_style` seleccionable, porque MinIO en el compose de desarrollo
necesita `path` mientras que R2 usa `virtual`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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
    sha256_from_checksum_header,
)

__all__ = ["S3BlobStore", "S3Config"]

#: Errores del store que significan "no está" y no "algo va mal".
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


@dataclass(frozen=True, slots=True)
class S3Config:
    """Lo que hace falta para hablar con el bucket. Se construye desde settings."""

    bucket: str
    endpoint_url: str | None = None
    access_key_id: str = ""
    secret_access_key: str = ""
    region: str = "auto"
    #: MinIO del compose de dev necesita `path`; R2 y S3 usan `virtual`.
    use_path_style: bool = False


class S3BlobStore:
    """Adaptador S3/R2 del puerto `BlobStore`.

    El cliente de `boto3` se construye en el `__init__` y se reutiliza: los
    clientes de botocore son caros de crear (cargan el modelo JSON del
    servicio) y seguros de compartir entre llamadas del mismo hilo. Quien
    decide su ciclo de vida es `storage.get_blob_store()`, que lo cachea por
    configuración.
    """

    def __init__(self, config: S3Config, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    # --- subida -------------------------------------------------------------

    def presign_upload(
        self, *, sha256: str, size: int, mime: str, expires_in: int
    ) -> PresignedUpload:
        key = bucket_key_for(sha256)
        checksum = checksum_header_value(sha256)
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._config.bucket,
                "Key": key,
                "ContentLength": size,
                "ContentType": mime,
                "ChecksumSHA256": checksum,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
        return PresignedUpload(
            url=url,
            expires_in=expires_in,
            # Estas tres cabeceras **son** la firma: el store recalcula con
            # ellas y devuelve 403 si el cliente cambia una. Se emiten
            # explícitamente en vez de leerse de `X-Amz-SignedHeaders` porque
            # `host` también está firmado y el cliente no debe fijarlo a mano.
            required_headers={
                "Content-Length": str(size),
                "Content-Type": mime,
                "x-amz-checksum-sha256": checksum,
            },
        )

    # --- verificación --------------------------------------------------------

    def head(self, sha256: str) -> ObjectHead:
        key = bucket_key_for(sha256)
        try:
            response = self._client.head_object(
                Bucket=self._config.bucket,
                Key=key,
                # Sin esto, S3/R2 **no** devuelven `ChecksumSHA256` y el commit
                # rechazaría todo por "checksum no certificado".
                ChecksumMode="ENABLED",
            )
        except Exception as exc:  # noqa: BLE001 — se reclasifica justo debajo
            raise _translate(exc, key) from exc

        checksum = response.get("ChecksumSHA256")
        return ObjectHead(
            size=int(response["ContentLength"]),
            content_type=response.get("ContentType"),
            sha256=sha256_from_checksum_header(checksum) if checksum else None,
            last_modified=response.get("LastModified"),
        )

    def read_prefix(self, sha256: str, length: int) -> bytes:
        """Los primeros `length` bytes, con `Range` — nunca el objeto entero."""
        key = bucket_key_for(sha256)
        try:
            response = self._client.get_object(
                Bucket=self._config.bucket, Key=key, Range=f"bytes=0-{max(length - 1, 0)}"
            )
            body = response["Body"]
            try:
                return bytes(body.read(length))
            finally:
                # RD-05: el stream se cierra en `finally` aunque `read` falle.
                # Es la misma disciplina que H-05 echaba en falta en el
                # cliente; aquí el stream es de 16 bytes, pero la regla no
                # depende del tamaño.
                body.close()
        except Exception as exc:  # noqa: BLE001 — se reclasifica justo debajo
            raise _translate(exc, key) from exc

    # --- descarga ------------------------------------------------------------

    def presign_download(self, *, sha256: str, expires_in: int, mime: str, filename: str) -> str:
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._config.bucket,
                    "Key": bucket_key_for(sha256),
                    # Se fija el tipo y el nombre en la propia URL firmada para
                    # que el navegador del back-office no tenga que adivinarlos
                    # y para que `filename` sea siempre el generado por el
                    # servidor (RD-06).
                    "ResponseContentType": mime,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=expires_in,
                HttpMethod="GET",
            )
        )

    # --- GC -------------------------------------------------------------------

    def list_objects(self) -> Iterator[StoredObject]:
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._config.bucket, Prefix=BLOB_KEY_PREFIX):
                for entry in page.get("Contents", ()):
                    key = entry["Key"]
                    yield StoredObject(
                        key=key,
                        size=int(entry["Size"]),
                        last_modified=entry["LastModified"],
                        sha256=sha256_from_bucket_key(key),
                    )
        except Exception as exc:  # noqa: BLE001 — se reclasifica justo debajo
            raise _translate(exc, BLOB_KEY_PREFIX) from exc

    def delete(self, sha256: str) -> None:
        key = bucket_key_for(normalize_sha256(sha256))
        try:
            self._client.delete_object(Bucket=self._config.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 — se reclasifica justo debajo
            translated = _translate(exc, key)
            # Borrar lo que ya no está es el resultado deseado, no un fallo:
            # el GC debe poder reintentarse sin cuidado.
            if isinstance(translated, ObjectNotFound):
                return
            raise translated from exc


def _translate(exc: Exception, key: str) -> BlobStoreError:
    """Convierte un error de botocore en el vocabulario del puerto.

    Se mira el `Error.Code` de la respuesta y no la clase de la excepción
    porque botocore genera las clases de error dinámicamente por servicio
    (`client.exceptions.NoSuchKey`), lo que obligaría a tener el cliente a mano
    para poder capturarlas — justo lo que impide escribir esta función como
    utilidad de módulo y probarla con un doble.

    El mensaje del store **no** se propaga al cliente HTTP: `services.py` lo
    traduce a un `500` genérico, porque un error de S3 puede incluir la clave,
    el bucket y hasta parte de las credenciales en el `RequestId` (RD-12).
    """
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    if code in _NOT_FOUND_CODES or status == 404:
        return ObjectNotFound(f"El objeto {key} no existe en el bucket.")
    return BlobStoreError(f"Fallo del object storage sobre {key}: {type(exc).__name__}")


def _build_client(config: S3Config) -> Any:
    """Cliente de boto3 configurado para R2/MinIO.

    El import va dentro de la función para que `boto3` (que carga varios MB de
    modelos JSON) no se importe en el arranque de un proceso que use el
    adaptador local — que es el caso de toda la suite de tests y del desarrollo
    sin red.
    """
    import boto3
    from botocore.config import Config

    session = boto3.session.Session(
        aws_access_key_id=config.access_key_id or None,
        aws_secret_access_key=config.secret_access_key or None,
        region_name=config.region or "auto",
    )
    return session.client(
        "s3",
        endpoint_url=config.endpoint_url or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if config.use_path_style else "virtual"},
        ),
    )

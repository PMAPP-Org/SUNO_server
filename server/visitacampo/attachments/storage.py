"""La fábrica que elige adaptador de `BlobStore` según los settings.

Un solo sitio decide qué implementación corre, y lo hace leyendo
configuración, no importando condicionalmente por ahí. Así `services.py` no
sabe —ni puede saber— si detrás hay R2, MinIO o un directorio temporal, que es
lo que hace que la misma lógica de verificación se pruebe offline y corra en
producción sin ramas.

**El caché y por qué es un `lru_cache` sobre los valores y no sobre nada.**
Construir un cliente de botocore cuesta cargar el modelo JSON del servicio
(decenas de ms), así que crear uno por request sería un impuesto absurdo. Pero
cachear "el store" a secas rompería `override_settings` en los tests: el
segundo test recibiría el store del primero. Cacheando por la **tupla de
settings efectivos**, un `override_settings` produce una clave distinta y por
tanto un store distinto, sin que ningún test tenga que acordarse de invalidar
nada. Es la misma propiedad que se busca en producción (un store por
configuración) expresada de forma que los tests la hereden gratis.
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from visitacampo.attachments.blobstore import BlobStore
from visitacampo.attachments.localfs import LocalFsBlobStore
from visitacampo.attachments.s3 import S3BlobStore, S3Config

__all__ = ["BACKEND_LOCALFS", "BACKEND_S3", "get_blob_store"]

BACKEND_S3 = "s3"
BACKEND_LOCALFS = "localfs"


def get_blob_store() -> BlobStore:
    """El `BlobStore` de este proceso, según `settings.STORAGE_BACKEND`."""
    backend = str(getattr(settings, "STORAGE_BACKEND", BACKEND_S3)).strip().lower()
    if backend == BACKEND_LOCALFS:
        return _local_store(str(settings.STORAGE_LOCAL_ROOT))
    if backend == BACKEND_S3:
        return _s3_store(
            bucket=str(settings.STORAGE_S3_BUCKET_NAME),
            endpoint_url=str(settings.STORAGE_S3_ENDPOINT_URL or ""),
            access_key_id=str(settings.STORAGE_S3_ACCESS_KEY_ID or ""),
            secret_access_key=str(settings.STORAGE_S3_SECRET_ACCESS_KEY or ""),
            region=str(settings.STORAGE_S3_REGION or "auto"),
            use_path_style=bool(settings.STORAGE_S3_USE_PATH_STYLE),
        )
    raise ImproperlyConfigured(
        f"STORAGE_BACKEND={backend!r} no es un backend de adjuntos conocido. "
        f"Valores admitidos: {BACKEND_S3!r} (Cloudflare R2 / MinIO) o "
        f"{BACKEND_LOCALFS!r} (tests y desarrollo sin red)."
    )


@lru_cache(maxsize=8)
def _local_store(root: str) -> LocalFsBlobStore:
    return LocalFsBlobStore(root)


@lru_cache(maxsize=8)
def _s3_store(
    *,
    bucket: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    use_path_style: bool,
) -> S3BlobStore:
    if not bucket:
        raise ImproperlyConfigured(
            "STORAGE_S3_BUCKET_NAME es obligatorio con STORAGE_BACKEND=s3 "
            "(ver infra/.env.example). Sin bucket no hay dónde firmar una subida."
        )
    return S3BlobStore(
        S3Config(
            bucket=bucket,
            endpoint_url=endpoint_url or None,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
            use_path_style=use_path_style,
        )
    )

"""Importar, listar, descargar y retirar mapas base (Fase B).

Toda operación pasa por su política (`check_*_basemaps`) y por `scoped_qs`:
un miembro solo ve y descarga los de su organización, y un paquete de otra
responde igual que uno inexistente (`404`, RD-11).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from visitacampo.attachments.storage import get_blob_store
from visitacampo.basemaps.mbtiles import InvalidMbtiles, inspect
from visitacampo.core.audit import AuditAction, record
from visitacampo.core.context import RequestContext
from visitacampo.core.models import BasemapPackage
from visitacampo.core.policies import (
    CODE_NOT_FOUND,
    PolicyDenied,
    check_manage_basemaps,
    check_read_basemaps,
)
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "BasemapDownload",
    "BasemapNotFound",
    "InvalidMbtiles",
    "download",
    "import_basemap",
    "live_basemaps",
    "retire",
]

_CHUNK = 1024 * 1024


class BasemapNotFound(PolicyDenied):
    """`404 not_found`: no existe, está retirado o es de otra organización."""

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Basemap not found.")


@dataclass(frozen=True, slots=True)
class BasemapDownload:
    url: str
    expires_in: int
    sha256: str
    size: int


def import_basemap(
    ctx: RequestContext, *, path: str | Path, name: str | None = None
) -> BasemapPackage:
    """Valida el MBTiles, lo sube al almacenamiento y lo ofrece a la organización."""
    check_manage_basemaps(ctx, ctx.org_id)
    source = Path(path)
    size = source.stat().st_size
    if size > settings.BASEMAP_MAX_BYTES:
        raise InvalidMbtiles(f"Pesa {size} bytes; el máximo es {settings.BASEMAP_MAX_BYTES}.")
    info = inspect(source)
    sha256 = _sha256_of(source)
    get_blob_store().put_basemap(sha256=sha256, path=str(source), size=size)
    package = scoped_qs(ctx, BasemapPackage).create(
        org_id=ctx.org_id,
        name=(name or info.name or source.stem)[:128],
        sha256=sha256,
        size=size,
        tile_format=info.tile_format,
        min_zoom=info.min_zoom,
        max_zoom=info.max_zoom,
        west=info.west,
        south=info.south,
        east=info.east,
        north=info.north,
        uploaded_by_id=ctx.user_id,
    )
    record(
        ctx,
        action=AuditAction.BASEMAP_IMPORTED,
        target=f"basemap:{package.pk}",
        metadata={"sha256": sha256, "size": size, "zoom": [info.min_zoom, info.max_zoom]},
    )
    return package


def live_basemaps(ctx: RequestContext) -> QuerySet[BasemapPackage]:
    check_read_basemaps(ctx, ctx.org_id)
    return (
        scoped_qs(ctx, BasemapPackage)
        .filter(deleted_at__isnull=True)
        .order_by("name", "created_at")
    )


def download(ctx: RequestContext, basemap_id: uuid.UUID) -> BasemapDownload:
    package = live_basemaps(ctx).filter(pk=basemap_id).first()
    if package is None:
        raise BasemapNotFound()
    expires_in = int(settings.BASEMAP_DOWNLOAD_URL_TTL_SECONDS)
    url = get_blob_store().presign_basemap_download(
        sha256=package.sha256,
        expires_in=expires_in,
        # El nombre lo genera el servidor (RD-06), nunca el del operador.
        filename=f"basemap-{package.pk}.mbtiles",
    )
    return BasemapDownload(url=url, expires_in=expires_in, sha256=package.sha256, size=package.size)


def retire(ctx: RequestContext, basemap_id: uuid.UUID) -> None:
    """Deja de ofrecer el paquete; los teléfonos lo borran al sincronizar mapas.

    El archivo **se conserva** en el almacenamiento: su clave es el `sha256`, así
    que otra organización puede estar ofreciendo el mismo contenido, y desde
    aquí solo se ve la propia (RD-11). Borrar a ciegas le rompería su mapa."""
    check_manage_basemaps(ctx, ctx.org_id)
    package = scoped_qs(ctx, BasemapPackage).filter(pk=basemap_id, deleted_at__isnull=True).first()
    if package is None:
        raise BasemapNotFound()
    package.deleted_at = timezone.now()
    package.save(update_fields=["deleted_at"])
    record(ctx, action=AuditAction.BASEMAP_RETIRED, target=f"basemap:{package.pk}")


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()

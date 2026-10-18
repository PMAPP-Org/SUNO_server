"""Importar, listar, descargar y retirar capas de referencia (Fase C).

Mismas reglas que los mapas base (`basemaps/services.py`): política primero,
`scoped_qs` siempre, y una capa de otra organización responde `404` (RD-11).
Usa las políticas de mapas (`can_read_basemaps` / `can_manage_basemaps`):
mapa base y capas son el mismo material de trabajo de campo.
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
from visitacampo.core.audit import AuditAction, record
from visitacampo.core.context import RequestContext
from visitacampo.core.models import ReferenceLayer
from visitacampo.core.policies import (
    CODE_NOT_FOUND,
    PolicyDenied,
    check_manage_basemaps,
    check_read_basemaps,
)
from visitacampo.core.scoping import scoped_qs
from visitacampo.layers.geojson import InvalidLayer, inspect

__all__ = [
    "InvalidLayer",
    "LayerDownload",
    "LayerNotFound",
    "download",
    "import_layer",
    "live_layers",
    "retire",
]

_CHUNK = 1024 * 1024


class LayerNotFound(PolicyDenied):
    """`404 not_found`: no existe, está retirada o es de otra organización."""

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Layer not found.")


@dataclass(frozen=True, slots=True)
class LayerDownload:
    url: str
    expires_in: int
    sha256: str
    size: int


def import_layer(ctx: RequestContext, *, path: str | Path, name: str) -> ReferenceLayer:
    """Valida el GeoJSON, lo sube al almacenamiento y lo ofrece a la organización."""
    check_manage_basemaps(ctx, ctx.org_id)
    source = Path(path)
    size = source.stat().st_size
    if size > settings.LAYER_MAX_BYTES:
        raise InvalidLayer(f"Pesa {size} bytes; el máximo es {settings.LAYER_MAX_BYTES}.")
    info = inspect(source)
    sha256 = _sha256_of(source)
    get_blob_store().put_layer(sha256=sha256, path=str(source), size=size)
    layer = scoped_qs(ctx, ReferenceLayer).create(
        org_id=ctx.org_id,
        name=name[:128],
        sha256=sha256,
        size=size,
        feature_count=info.feature_count,
        geometry_types=list(info.geometry_types),
        west=info.west,
        south=info.south,
        east=info.east,
        north=info.north,
        uploaded_by_id=ctx.user_id,
    )
    record(
        ctx,
        action=AuditAction.LAYER_IMPORTED,
        target=f"layer:{layer.pk}",
        metadata={"sha256": sha256, "size": size, "features": info.feature_count},
    )
    return layer


def live_layers(ctx: RequestContext) -> QuerySet[ReferenceLayer]:
    check_read_basemaps(ctx, ctx.org_id)
    return (
        scoped_qs(ctx, ReferenceLayer)
        .filter(deleted_at__isnull=True)
        .order_by("name", "created_at")
    )


def download(ctx: RequestContext, layer_id: uuid.UUID) -> LayerDownload:
    layer = live_layers(ctx).filter(pk=layer_id).first()
    if layer is None:
        raise LayerNotFound()
    expires_in = int(settings.BASEMAP_DOWNLOAD_URL_TTL_SECONDS)
    url = get_blob_store().presign_layer_download(
        sha256=layer.sha256,
        expires_in=expires_in,
        filename=f"layer-{layer.pk}.geojson",
    )
    return LayerDownload(url=url, expires_in=expires_in, sha256=layer.sha256, size=layer.size)


def retire(ctx: RequestContext, layer_id: uuid.UUID) -> None:
    """Deja de ofrecerla; los teléfonos la borran al sincronizar. El archivo se
    conserva, por la misma razón que en los mapas base."""
    check_manage_basemaps(ctx, ctx.org_id)
    layer = scoped_qs(ctx, ReferenceLayer).filter(pk=layer_id, deleted_at__isnull=True).first()
    if layer is None:
        raise LayerNotFound()
    layer.deleted_at = timezone.now()
    layer.save(update_fields=["deleted_at"])
    record(ctx, action=AuditAction.LAYER_RETIRED, target=f"layer:{layer.pk}")


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()

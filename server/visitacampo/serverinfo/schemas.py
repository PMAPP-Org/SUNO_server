"""Esquemas de `GET /v1/server/info` (contrato `ServerInfo`/`ServerCapabilities`)."""

from __future__ import annotations

from typing import Literal

from ninja import Schema

from visitacampo.authn.schemas import Timestamp

__all__ = ["ServerCapabilitiesSchema", "ServerInfoSchema"]


class ServerCapabilitiesSchema(Schema):
    """Contrato `ServerCapabilities`. Valores en `serverinfo/capabilities.py`."""

    sync_push_max_ops: int
    sync_push_max_bytes: int
    sync_pull_max_limit: int
    attachment_max_bytes: int
    attachment_allowed_mime: list[str]


class ServerInfoSchema(Schema):
    """Contrato `ServerInfo`.

    `server_time` no es decorativo: el cliente lo usa para corregir el desfase
    del reloj del dispositivo, que es lo que hace utilizables las URLs
    prefirmadas de adjuntos (ADR-007) en un móvil con la hora mal puesta.
    """

    api_version: Literal["v1"] = "v1"
    schema_version: int
    min_supported_schema_version: int
    server_time: Timestamp
    capabilities: ServerCapabilitiesSchema

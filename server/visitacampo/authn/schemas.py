"""Esquemas de `/v1/auth/*`, calcados del contrato `server/openapi/v1.yaml`.

El contrato del Paso 1 es la fuente de verdad: es lo que el cliente Flutter
del Paso 13 va a generar y esperar. Aquí no se inventa ningún campo, ni se
renombra ninguno, ni se hace opcional lo que allí es `required`.

`server_time` se serializa con el sufijo `Z` (y no `+00:00`) porque el
contrato lo especifica así (`components.schemas.Timestamp`: "el servidor emite
siempre UTC, `Z`"). Django Ninja renderiza con `NinjaJSONEncoder`, que hereda
de `DjangoJSONEncoder` y ya hace esa conversión para instantes UTC; el
serializador explícito de aquí lo garantiza aunque el renderizador cambie, y
es lo que los tests comprueban.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from ninja import Schema
from pydantic import Field, PlainSerializer

__all__ = [
    "DeviceInfoSchema",
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "RefreshRequest",
    "TokenPair",
    "UserProfile",
    "rfc3339_utc",
]


def rfc3339_utc(value: datetime) -> str:
    """RFC 3339 en UTC con `Z`, como exige `components.schemas.Timestamp`.

    Público desde el Paso 8: el push guarda su respuesta ya serializada en
    `sync_op_log.result` (JSONB) para devolverla idéntica en un reenvío, así
    que necesita el mismo formateador que usa el renderizador de respuestas.
    Dos implementaciones del formato de instante acabarían divergiendo en el
    `timespec`, y el cliente vería dos formatos distintos para la misma fecha.
    """
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


#: Instante tal y como lo emite el servidor en el contrato.
Timestamp = Annotated[datetime, PlainSerializer(rfc3339_utc, return_type=str)]


class DeviceInfoSchema(Schema):
    """Contrato `DeviceInfo`. `device_id` lo genera el cliente (ADR-008)."""

    device_id: uuid.UUID
    platform: Literal["android", "ios"]
    app_version: str = Field(max_length=32)
    os_version: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(Schema):
    """Contrato `LoginRequest`.

    Los límites de longitud son los del contrato y no son cosmética: acotan lo
    que llega a `authenticate()` (y por tanto a Argon2) antes de tocar la BD.
    """

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    device_info: DeviceInfoSchema


class TokenPair(Schema):
    """Contrato `TokenPair`. El `refresh_token` viaja aquí y **nunca más**."""

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    server_time: Timestamp


class UserProfile(Schema):
    """Contrato `UserProfile`.

    Todas las propiedades son `required` (convención del contrato para
    entidades emitidas por el servidor): lo opcional lo es en el **valor**
    (`null`), nunca en la presencia del campo.
    """

    id: uuid.UUID
    username: str
    display_name: str | None
    org_id: uuid.UUID
    roles: list[str]


class LoginResponse(Schema):
    """Contrato `LoginResponse`."""

    tokens: TokenPair
    user: UserProfile


class RefreshRequest(Schema):
    """Contrato `RefreshRequest`.

    `device_id` es obligatorio en el contrato aunque el refresh ya identifique
    la fila por sí solo: sirve para que un token presentado desde otro
    dispositivo se rechace explícitamente (ADR-008, "un refresh por
    dispositivo").
    """

    refresh_token: str
    device_id: uuid.UUID


class LogoutRequest(Schema):
    """Contrato `LogoutRequest`.

    Solo el token: el dispositivo se deduce de él. Cerrar sesión **no** borra
    la outbox del cliente (ADR-008) — ver `services.log_out_session`.
    """

    refresh_token: str

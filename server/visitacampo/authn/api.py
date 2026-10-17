"""Router de Ninja para `/v1/auth/*` (contrato `server/openapi/v1.yaml`).

ADR-004: las vistas no tienen lógica. Aquí solo se traduce el JSON del
contrato a los tipos del servicio y de vuelta; toda la decisión vive en
`authn/services.py`.

**Autenticación de cada operación**, tal y como la declara el contrato:
`login` y `refresh` llevan `security: []` (no puede exigirse un token para
obtener un token) y por eso declaran `auth=None`; `logout` **no** lo lleva,
así que hereda el `bearerAuth` global montado en `config/api.py` — cerrar la
sesión de un dispositivo es una operación de un usuario ya identificado.
"""

from __future__ import annotations

from axes.helpers import get_client_ip_address
from ninja import Router, Status

from visitacampo.authn import services
from visitacampo.authn.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
)
from visitacampo.core.context import get_request_context

router = Router(tags=["auth"])


def _token_pair(session: services.IssuedSession) -> dict[str, object]:
    return {
        "access_token": session.access_token,
        "token_type": "Bearer",  # nosec B105
        "expires_in": session.expires_in,
        "refresh_token": session.refresh_token,
        "refresh_expires_in": session.refresh_expires_in,
        "server_time": session.server_time,
    }


@router.post(
    "/auth/login",
    auth=None,
    response={200: LoginResponse},
    summary="Inicia sesión y registra el dispositivo",
    operation_id="authLogin",
)
def login(request, payload: LoginRequest):
    """`POST /v1/auth/login`.

    La respuesta contiene el `refresh_token` en claro **por única vez**: el
    servidor solo guarda `sha256(verifier)` y no puede volver a emitirlo
    (RD-09/H-14). El cliente debe almacenarlo en el llavero del sistema
    (`flutter_secure_storage`, ADR-008), nunca en preferencias planas (C-09).
    """
    info = services.DeviceRegistration(
        device_id=payload.device_info.device_id,
        platform=payload.device_info.platform,
        app_version=payload.device_info.app_version,
        os_version=payload.device_info.os_version,
        model=payload.device_info.model,
        display_name=payload.device_info.display_name,
    )
    result = services.log_in(request, payload.username, payload.password, info)
    return {
        "tokens": _token_pair(result.session),
        "user": {
            "id": result.user.pk,
            "username": result.user.get_username(),
            "display_name": result.user.display_name,
            "org_id": result.org_id,
            "roles": list(result.roles),
        },
    }


@router.post(
    "/auth/refresh",
    auth=None,
    response={200: TokenPair},
    summary="Rota el refresh token del dispositivo",
    operation_id="authRefresh",
)
def refresh(request, payload: RefreshRequest):
    """`POST /v1/auth/refresh`. Rota y reemite (detección de reuso: Paso 15)."""
    session = services.refresh_session(
        payload.refresh_token, payload.device_id, client_ip=get_client_ip_address(request)
    )
    return _token_pair(session)


@router.post(
    "/auth/logout",
    response={204: None},
    summary="Revoca el refresh token del dispositivo",
    operation_id="authLogout",
)
def logout(request, payload: LogoutRequest):
    """`POST /v1/auth/logout`.

    Siempre `204`, exista o no el token (idempotente y sin oráculo). **No
    borra la outbox local del cliente**: el trabajo pendiente del agente se
    conserva en el dispositivo y el Paso 13 debe respetarlo (ADR-008).
    """
    services.log_out_session(
        get_request_context(request),
        payload.refresh_token,
        client_ip=get_client_ip_address(request),
    )
    # `Status(...)` y no la tupla `(204, None)`: Ninja 1.6 marca la tupla como
    # deprecada y avisa en cada llamada.
    return Status(204, None)

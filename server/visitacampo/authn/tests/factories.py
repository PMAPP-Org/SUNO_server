"""Datos mínimos de prueba para los tests de autenticación.

Los tests **sí** pueden usar `Model.objects` (el test de arquitectura excluye
`tests/`): crear el escenario es precisamente lo que no pasa por `scoped_qs`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.test import Client

from visitacampo.core.context import RoleValue
from visitacampo.core.models import AppUser, Membership, Organization, Role

#: Contraseña de los usuarios de prueba. Larga a propósito: los validadores
#: de `AUTH_PASSWORD_VALIDATORS` corren en el back-office, no aquí, pero una
#: contraseña realista evita sorpresas si algún día se validan también aquí.
PASSWORD = "contraseña-de-prueba-larga-42"


def make_org(name: str = "Org de prueba") -> Organization:
    return Organization.objects.create(name=name)


def make_user(
    org: Organization,
    username: str = "agente",
    role: RoleValue = Role.FIELD_AGENT,
    password: str = PASSWORD,
    is_active: bool = True,
    with_membership: bool = True,
) -> AppUser:
    user = AppUser.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=password,
        org=org,
        is_active=is_active,
    )
    if with_membership:
        Membership.objects.create(org=org, user=user, role=role)
    return user


def device_info(device_id: uuid.UUID | None = None, **overrides: Any) -> dict[str, Any]:
    """Payload `DeviceInfo` del contrato (`server/openapi/v1.yaml`)."""
    payload: dict[str, Any] = {
        "device_id": str(device_id or uuid.uuid4()),
        "platform": "android",
        "os_version": "14",
        "model": "Pixel 7a",
        "app_version": "0.1.0",
        "display_name": "Tablet campo 3",
    }
    payload.update(overrides)
    return payload


def post_json(client: Client, url: str, payload: dict[str, Any], **extra: Any):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        **extra,
    )


def login(
    client: Client,
    username: str = "agente",
    password: str = PASSWORD,
    device: dict[str, Any] | None = None,
):
    return post_json(
        client,
        "/v1/auth/login",
        {
            "username": username,
            "password": password,
            "device_info": device if device is not None else device_info(),
        },
    )

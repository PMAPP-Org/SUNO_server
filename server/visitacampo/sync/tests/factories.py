"""Escenario mínimo para ejercitar `POST /v1/sync/push` de extremo a extremo.

Los tests **sí** pueden usar `Model.objects` (el test de arquitectura excluye
`tests/`). Se reutilizan las factorías del Paso 7 para el alta de organización,
usuario y sesión: el token que usan estos tests es un JWT emitido por el login
real, no uno fabricado a mano, así que la cadena `JwtAuth` →
`RequestContextAuth` → `RequestContext` es la de producción.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.test import Client

from visitacampo.authn.tests.factories import device_info, login, make_org, make_user, post_json
from visitacampo.core.context import RoleValue
from visitacampo.core.models import (
    AppUser,
    FormTemplate,
    FormTemplateVersion,
    Organization,
    Role,
    Visit,
)
from visitacampo.serverinfo import capabilities as caps

PUSH_URL = "/v1/sync/push"
PULL_URL = "/v1/sync/pull"
BOOTSTRAP_URL = "/v1/sync/bootstrap"

#: `change_seq` de la `FormTemplateVersion` del escenario. No es 0 a propósito:
#: obliga a que el asignador de `change_seq` mire también las entidades que no
#: son `visit` (doc 08 §4.4, cursor único por organización).
TEMPLATE_CHANGE_SEQ = 41

#: Definición de la plantilla del escenario (Paso 20). Declara de verdad el
#: `altura_cm` que usan los payloads de estos tests: desde el Paso 20 el push
#: valida `answers` contra la definición de su versión de plantilla, así que un
#: escenario con `{"fields": []}` estaría probando el camino "plantilla sin
#: campos declarados" y no el real.
SCENARIO_DEFINITION: dict[str, Any] = {
    "dsl": "visitacampo/form@1",
    "fields": [
        {
            "key": "altura_cm",
            "type": "number",
            "label": {"es": "Altura (cm)"},
            "min": 0,
            "max": 500,
        }
    ],
}


@dataclass(frozen=True)
class Scenario:
    org: Organization
    user: AppUser
    device_id: uuid.UUID
    token: str
    template_version: FormTemplateVersion
    client: Client

    def auth(self) -> dict[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}


def build_scenario(
    role: RoleValue = Role.FIELD_AGENT,
    username: str = "agente",
    org: Organization | None = None,
) -> Scenario:
    organization = org if org is not None else make_org()
    user = make_user(organization, username=username, role=role)
    client = Client()
    device_id = uuid.uuid4()
    body = login(client, username=username, device=device_info(device_id)).json()

    template = FormTemplate.objects.create(org=organization, name="Inspección")
    template_version = FormTemplateVersion.objects.create(
        org=organization,
        template=template,
        version_number=1,
        definition=SCENARIO_DEFINITION,
        change_seq=TEMPLATE_CHANGE_SEQ,
    )
    return Scenario(
        org=organization,
        user=user,
        device_id=device_id,
        token=body["tokens"]["access_token"],
        template_version=template_version,
        client=client,
    )


def create_op(
    scenario: Scenario,
    visit_id: uuid.UUID | None = None,
    op_uuid: uuid.UUID | None = None,
    **payload_overrides: Any,
) -> dict[str, Any]:
    """Operación `create` de `visit` con el conjunto completo que exige el contrato."""
    payload: dict[str, Any] = {
        "id": str(visit_id or uuid.uuid4()),
        "template_version_id": str(scenario.template_version.pk),
        "answers": {"altura_cm": 12},
        "captured_at": "2026-07-30T14:05:09.123Z",
        "device_id": str(scenario.device_id),
    }
    payload.update(payload_overrides)
    return {
        "op_uuid": str(op_uuid or uuid.uuid4()),
        "entity": "visit",
        "op_type": "create",
        "base_version": None,
        "payload": payload,
    }


def update_op(
    visit_id: uuid.UUID,
    base_version: int,
    op_uuid: uuid.UUID | None = None,
    **payload_overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": str(visit_id), "lat": 4.61, "lon": -74.08}
    payload.update(payload_overrides)
    return {
        "op_uuid": str(op_uuid or uuid.uuid4()),
        "entity": "visit",
        "op_type": "update",
        "base_version": base_version,
        "payload": payload,
    }


def delete_op(
    visit_id: uuid.UUID,
    base_version: int,
    op_uuid: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "op_uuid": str(op_uuid or uuid.uuid4()),
        "entity": "visit",
        "op_type": "delete",
        "base_version": base_version,
        "payload": {"id": str(visit_id)},
    }


def push(
    scenario: Scenario,
    ops: list[dict[str, Any]],
    batch_uuid: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    schema_version: int | None = None,
):
    return post_json(
        scenario.client,
        PUSH_URL,
        {
            "schema_version": caps.SCHEMA_VERSION if schema_version is None else schema_version,
            "device_id": str(device_id or scenario.device_id),
            "batch_uuid": str(batch_uuid or uuid.uuid4()),
            "ops": ops,
        },
        **scenario.auth(),
    )


def visit_of(visit_id: uuid.UUID) -> Visit:
    return Visit.objects.get(pk=visit_id)


def pull(scenario: Scenario, cursor: str, limit: int | None = None):
    params: dict[str, Any] = {"cursor": cursor}
    if limit is not None:
        params["limit"] = limit
    return scenario.client.get(PULL_URL, params, **scenario.auth())


def bootstrap(scenario: Scenario):
    return scenario.client.get(BOOTSTRAP_URL, **scenario.auth())

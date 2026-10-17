"""Datos de prueba del back-office: dos organizaciones, su personal y sesión del Admin.

El Admin exige segundo factor (Paso 15): `admin_client` entra con usuario y
contraseña y deja la sesión con un TOTP verificado, igual que tras un login
real, sin pasar por el formulario en cada test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, RequestContext, RoleValue, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Organization,
    Role,
    Visit,
)

DEFINITION = {
    "fields": [
        {"key": "altura_cm", "type": "number", "label": {"es": "Altura"}},
        {"key": "notas", "type": "text", "label": {"es": "Notas"}},
    ]
}


@dataclass
class Tenant:
    org: Organization
    staff: AppUser
    agent: AppUser
    device: Device
    version: FormTemplateVersion

    def ctx(self, user: AppUser | None = None) -> RequestContext:
        who = user or self.staff
        return resolve_request_context(Principal(user_id=who.pk, org_id=who.org_id))

    def visit(self, **answers: object) -> Visit:
        return Visit.objects.create(
            id=uuid.uuid4(),
            org=self.org,
            version=1,
            change_seq=1,
            template_version=self.version,
            answers=answers or {"altura_cm": 10},
            lat=4.6,
            lon=-74.08,
            captured_at=timezone.now(),
            device=self.device,
            created_by=self.agent,
        )


def make_tenant(name: str, staff_role: RoleValue = Role.ADMIN) -> Tenant:
    org = make_org(name)
    slug = name.lower().replace(" ", "-")
    staff = make_user(org, username=f"staff-{slug}", role=staff_role)
    staff.is_staff = True
    staff.save()
    # Permisos de modelo de Django sobre todo el núcleo: el aislamiento lo
    # tiene que dar el back-office, no la falta de permisos.
    staff.user_permissions.set(Permission.objects.filter(content_type__app_label="core"))
    agent = make_user(org, username=f"agente-{slug}", role=Role.FIELD_AGENT)
    device = Device.objects.create(
        id=uuid.uuid4(),
        org=org,
        owner=agent,
        platform="android",
        app_version="0.3.0",
        display_name=f"Tel. de {slug}",
    )
    template = FormTemplate.objects.create(org=org, name=f"Inspección {name}")
    version = FormTemplateVersion.objects.create(
        org=org,
        template=template,
        name=f"Inspección {name}",
        version_number=1,
        definition=DEFINITION,
        change_seq=1,
        published_at=timezone.now(),
    )
    return Tenant(org=org, staff=staff, agent=agent, device=device, version=version)


def admin_client(user: AppUser) -> Client:
    """Cliente dentro del Admin: sesión iniciada y segundo factor verificado."""
    client = Client()
    client.force_login(user)
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client

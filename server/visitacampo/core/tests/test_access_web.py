"""Administración web y seguridad de los accesos por sección."""

from __future__ import annotations

import unittest

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.access import AccessScope, Section, SectionRole
from visitacampo.core.access_services import revoke_section_grant
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import AuditEvent, Project, Role, SectionGrant
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class AccessMatrixWebTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora Norte")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="operador", role=Role.FIELD_AGENT)
        self.project = Project.objects.create(
            org=self.org,
            name="Frente vial",
            created_by=self.admin,
        )
        self.url = f"/plataforma/accesos/?usuario={self.agent.pk}"

    def test_access_admin_sees_user_section_and_capability_matrices(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secciones disponibles por usuario")
        self.assertContains(response, "Roles de operador")
        self.assertContains(response, "Capacidades efectivas")
        self.assertContains(response, "Frente vial")

    def test_non_access_admin_gets_forbidden(self) -> None:
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get("/plataforma/accesos/").status_code, 403)

    def test_assigns_project_scoped_equipment_role_and_audits_it(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/accesos/",
            {
                "action": "set",
                "user_id": str(self.agent.pk),
                "section": Section.EQUIPMENT,
                "role": SectionRole.EQUIPMENT_OPERATOR,
                "scope": AccessScope.PROJECTS,
                "projects": [str(self.project.pk)],
            },
        )

        self.assertRedirects(response, self.url)
        grant = SectionGrant.objects.get(user=self.agent, section=Section.EQUIPMENT)
        self.assertEqual(grant.role, SectionRole.EQUIPMENT_OPERATOR)
        self.assertEqual(grant.scope, AccessScope.PROJECTS)
        self.assertEqual(
            list(grant.project_scopes.values_list("project", flat=True)),
            [self.project.pk],
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="access.granted", actor=self.admin).exists()
        )

    def test_rejects_self_escalation(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/accesos/",
            {
                "action": "set",
                "user_id": str(self.admin.pk),
                "section": Section.EQUIPMENT,
                "role": SectionRole.MANAGER,
                "scope": AccessScope.ORGANIZATION,
            },
            follow=True,
        )

        self.assertContains(response, "No puedes modificar tus propios accesos")
        self.assertEqual(
            SectionGrant.objects.get(
                user=self.admin,
                section=Section.EQUIPMENT,
            ).role,
            SectionRole.MANAGER,
        )

    def test_revoke_hides_the_section_on_the_next_request(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/accesos/",
            {
                "action": "revoke",
                "user_id": str(self.agent.pk),
                "section": Section.PROJECTS,
            },
        )
        self.assertRedirects(response, self.url)
        self.assertTrue(
            AuditEvent.objects.filter(action="access.revoked", actor=self.admin).exists()
        )

        self.client.force_login(self.agent)
        home = self.client.get("/plataforma/")
        self.assertNotContains(home, 'href="/plataforma/proyectos/"')
        self.assertEqual(self.client.get("/plataforma/proyectos/").status_code, 403)

    def test_other_organization_user_is_not_modified(self) -> None:
        other = make_org("Otra")
        outsider = make_user(other, username="externo", role=Role.VIEWER)
        self.client.force_login(self.admin)
        response = self.client.post(
            "/plataforma/accesos/",
            {
                "action": "set",
                "user_id": str(outsider.pk),
                "section": Section.EQUIPMENT,
                "role": SectionRole.VIEWER,
                "scope": AccessScope.ORGANIZATION,
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            SectionGrant.objects.filter(user=outsider, section=Section.EQUIPMENT).exists()
        )

    def test_last_access_administrator_cannot_be_revoked(self) -> None:
        second_admin = make_user(self.org, username="admin-2", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        SectionGrant.objects.filter(
            user=self.admin,
            section=Section.USERS_ACCESS,
        ).update(active=False, revoked_at=timezone.now())

        with self.assertRaisesMessage(
            ValidationError,
            "Debe permanecer al menos un administrador de accesos activo",
        ):
            revoke_section_grant(
                ctx,
                user_id=second_admin.pk,
                section=Section.USERS_ACCESS,
            )


@skip_without_db
class MembershipProvisioningTests(TestCase):
    def test_new_membership_receives_compatible_section_profile(self) -> None:
        org = make_org("Nueva")
        viewer = make_user(org, role=Role.VIEWER)

        sections = set(
            SectionGrant.objects.filter(user=viewer, active=True).values_list("section", flat=True)
        )
        self.assertEqual(
            sections,
            {
                Section.FIELD_RECORDS,
                Section.PROJECTS,
                Section.BUDGETS,
                Section.GIS,
                Section.REPORTS,
            },
        )

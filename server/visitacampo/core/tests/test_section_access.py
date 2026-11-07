"""Persistencia y resolución del RBAC por secciones (ACC-02/ACC-03)."""

from __future__ import annotations

import unittest
import uuid

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.access import AccessScope, Capability, Section, SectionRole
from visitacampo.core.context import (
    Principal,
    RequestContext,
    SectionAccess,
    resolve_request_context,
)
from visitacampo.core.models import Project, Role, SectionGrant, SectionProjectScope

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

setUpModule = setup_test_database
tearDownModule = teardown_test_database


class InMemorySectionAccessTests(SimpleTestCase):
    def setUp(self) -> None:
        self.project_id = uuid.uuid4()
        self.ctx = RequestContext(
            user_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
            roles=frozenset({Role.FIELD_AGENT}),
            section_access=(
                SectionAccess(
                    section=Section.PROJECTS,
                    role=SectionRole.CONTRIBUTOR,
                    scope=str(AccessScope.PROJECTS),
                    project_ids=frozenset({self.project_id}),
                ),
            ),
        )

    def test_project_scoped_capability_requires_the_project(self) -> None:
        self.assertTrue(
            self.ctx.has_capability(Section.PROJECTS, Capability.READ, self.project_id)
        )
        self.assertFalse(
            self.ctx.has_capability(Section.PROJECTS, Capability.READ, uuid.uuid4())
        )
        self.assertFalse(self.ctx.has_capability(Section.PROJECTS, Capability.READ))

    def test_section_visibility_does_not_grant_org_wide_access(self) -> None:
        self.assertTrue(self.ctx.has_section_access(Section.PROJECTS))
        self.assertFalse(self.ctx.has_section_access(Section.BUDGETS))

    def test_unknown_capability_is_denied(self) -> None:
        self.assertFalse(self.ctx.has_capability(Section.PROJECTS, "unknown", self.project_id))


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class SectionGrantPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Accesos")
        self.user = make_user(self.org, username="gestora", role=Role.ADMIN)
        # Este caso prueba la persistencia directamente; elimina el perfil
        # compatible que la señal crea para una membresía real nueva.
        SectionGrant.objects.filter(user=self.user).delete()
        self.project = Project.objects.create(
            org=self.org,
            name="Proyecto A",
            created_by=self.user,
        )

    def _grant(self, **overrides: object) -> SectionGrant:
        values: dict[str, object] = {
            "org": self.org,
            "user": self.user,
            "section": Section.PROJECTS,
            "role": SectionRole.MANAGER,
            "scope": AccessScope.ORGANIZATION,
            "granted_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        return SectionGrant.objects.create(**values)

    def test_one_grant_per_user_and_section(self) -> None:
        self._grant()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._grant(role=SectionRole.VIEWER)

    def test_database_rejects_a_role_not_allowed_in_the_section(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._grant(section=Section.USERS_ACCESS, role=SectionRole.VIEWER)

    def test_project_scope_is_persisted_explicitly(self) -> None:
        grant = self._grant(scope=AccessScope.PROJECTS)
        scope = SectionProjectScope.objects.create(
            org=self.org,
            grant=grant,
            project=self.project,
            granted_by=self.user,
        )
        self.assertEqual(scope.project_id, self.project.pk)

    def test_resolution_loads_roles_grants_and_project_scope_in_two_queries(self) -> None:
        grant = self._grant(scope=AccessScope.PROJECTS)
        SectionProjectScope.objects.create(
            org=self.org,
            grant=grant,
            project=self.project,
            granted_by=self.user,
        )
        with self.assertNumQueries(2):
            ctx = resolve_request_context(Principal(user_id=self.user.pk, org_id=self.org.pk))
        self.assertTrue(
            ctx.has_capability(Section.PROJECTS, Capability.READ, self.project.pk)
        )

    def test_user_without_membership_does_not_load_grants(self) -> None:
        outsider = make_user(
            self.org,
            username="sin-membresia",
            with_membership=False,
        )
        with self.assertNumQueries(1):
            ctx = resolve_request_context(Principal(user_id=outsider.pk, org_id=self.org.pk))
        self.assertFalse(ctx.is_member)
        self.assertEqual(ctx.section_access, ())

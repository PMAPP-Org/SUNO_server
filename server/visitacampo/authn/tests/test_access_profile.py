"""GET /v1/auth/access — autorización funcional vigente (ACC-05)."""

from __future__ import annotations

import unittest

from django.test import Client, TestCase
from django.utils import timezone

from visitacampo.authn.tests.factories import login, make_org, make_user
from visitacampo.core.access import AccessScope, Capability, Section, SectionRole
from visitacampo.core.models import Project, Role, SectionGrant, SectionProjectScope
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class AccessProfileApiTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Accesos API")
        self.user = make_user(self.org, username="revisora", role=Role.SUPERVISOR)
        self.project = Project.objects.create(
            org=self.org,
            name="Proyecto Norte",
            created_by=self.user,
        )
        SectionGrant.objects.filter(user=self.user).update(
            active=False,
            revoked_at=timezone.now(),
        )
        self.grant, _ = SectionGrant.objects.update_or_create(
            org=self.org,
            user=self.user,
            section=Section.EQUIPMENT,
            defaults={
                "role": SectionRole.REVIEWER,
                "scope": AccessScope.PROJECTS,
                "granted_by": self.user,
                "updated_by": self.user,
                "active": True,
                "revoked_at": None,
            },
        )
        SectionProjectScope.objects.create(
            org=self.org,
            grant=self.grant,
            project=self.project,
            granted_by=self.user,
        )
        self.client = Client()
        response = login(self.client, username=self.user.username)
        self.assertEqual(response.status_code, 200, response.content)
        self.token = response.json()["tokens"]["access_token"]

    def get(self):
        return self.client.get(
            "/v1/auth/access",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_returns_role_capabilities_and_project_scope(self) -> None:
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {
                "sections": [
                    {
                        "section": Section.EQUIPMENT,
                        "role": SectionRole.REVIEWER,
                        "scope": AccessScope.PROJECTS,
                        "project_ids": [str(self.project.pk)],
                        "capabilities": sorted(
                            capability.value
                            for capability in {
                                Capability.READ,
                                Capability.REVIEW,
                                Capability.APPROVE,
                                Capability.REJECT,
                                Capability.EXPORT,
                            }
                        ),
                    }
                ]
            },
        )

    def test_revocation_applies_with_the_same_access_token(self) -> None:
        self.assertEqual(len(self.get().json()["sections"]), 1)
        SectionGrant.objects.filter(pk=self.grant.pk).update(
            active=False,
            revoked_at=timezone.now(),
        )
        response = self.get()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"sections": []})

    def test_requires_authentication(self) -> None:
        response = self.client.get("/v1/auth/access")
        self.assertEqual(response.status_code, 401)

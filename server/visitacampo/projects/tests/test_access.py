"""Quién ve qué proyecto y quién es responsable de qué tarea."""

from __future__ import annotations

import unittest

from django.http import Http404
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, Project, Role, Task, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.access import (
    effective_responsibles,
    get_visible_project,
    visible_projects,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def ctx_for(user: AppUser) -> RequestContext:
    return resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))


@skip_without_db
class VisibilityTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.p1 = Project.objects.create(org=self.org, name="Obra 1", created_by=self.admin)
        self.p2 = Project.objects.create(org=self.org, name="Obra 2", created_by=self.admin)
        self.stage = Task.objects.create(
            org=self.org,
            project=self.p1,
            uid=1,
            outline_number="1",
            outline_level=1,
            sort_order=1,
            name="Etapa",
            is_summary=True,
        )
        self.leaf = Task.objects.create(
            org=self.org,
            project=self.p1,
            uid=2,
            outline_number="1.1",
            outline_level=2,
            sort_order=2,
            name="Hoja",
            parent=self.stage,
        )
        other = make_org("Otra")
        other_admin = make_user(other, username="otro", role=Role.ADMIN)
        self.foreign = Project.objects.create(org=other, name="Ajena", created_by=other_admin)

    def test_admin_and_viewer_see_all_projects_of_their_org_only(self) -> None:
        for user in (self.admin, self.viewer):
            self.assertEqual(
                set(visible_projects(ctx_for(user))), {self.p1, self.p2}, user.username
            )

    def test_field_agent_sees_only_projects_where_responsible(self) -> None:
        self.assertEqual(list(visible_projects(ctx_for(self.agent))), [])
        TaskResponsible.objects.create(
            org=self.org, task=self.leaf, user=self.agent, assigned_by=self.admin
        )
        self.assertEqual(list(visible_projects(ctx_for(self.agent))), [self.p1])

    def test_get_visible_project_hides_foreign_and_invisible(self) -> None:
        with self.assertRaises(Http404):
            get_visible_project(ctx_for(self.admin), self.foreign.pk)
        with self.assertRaises(Http404):
            get_visible_project(ctx_for(self.agent), self.p1.pk)
        self.assertEqual(get_visible_project(ctx_for(self.admin), self.p1.pk), self.p1)

    def test_responsible_of_a_summary_covers_its_branch(self) -> None:
        TaskResponsible.objects.create(
            org=self.org, task=self.stage, user=self.agent, assigned_by=self.admin
        )
        responsibles = effective_responsibles(ctx_for(self.admin), self.p1)
        self.assertEqual(responsibles[self.leaf.pk], [self.agent])
        self.assertEqual(responsibles[self.stage.pk], [self.agent])

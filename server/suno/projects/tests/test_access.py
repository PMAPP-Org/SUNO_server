"""Quién ve qué proyecto y quién es responsable de qué tarea."""

from __future__ import annotations

import unittest

from django.http import Http404
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import AppUser, Project, Role, Task, TaskResponsible
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.access import (
    covers,
    effective_responsibles,
    get_visible_project,
    responsible_outlines,
    visible_projects,
)
from suno.projects.importer import apply_import, stage_import

from .factories import make_field_world
from .samples import v2

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


class CoversTests(unittest.TestCase):
    """Pura: el número de esquema es la ruta de la tarea en la EDT."""

    def test_a_task_covers_itself_and_its_branch(self) -> None:
        self.assertTrue(covers(["1.2"], "1.2"))
        self.assertTrue(covers(["1.2"], "1.2.3.4"))
        self.assertTrue(covers(["3", "1"], "1.1"))

    def test_a_prefix_without_dot_is_not_an_ancestor(self) -> None:
        self.assertFalse(covers(["1.1"], "1.10"))
        self.assertFalse(covers(["1.2"], "1"))
        self.assertFalse(covers([], "1"))


@skip_without_db
class ResponsibleOutlinesTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def test_by_project_only_current_tasks(self) -> None:
        self.world.responsible(1)
        self.world.responsible(5)
        ctx = self.world.ctx()
        self.assertEqual(
            responsible_outlines(ctx, user_id=self.world.agent.pk),
            {self.world.project.pk: ["1", "2.1"]},
        )
        # v2 retira la 2.1: su responsabilidad deja de contar.
        apply_import(ctx, stage_import(ctx, self.world.project, file_name="v2.xml", data=v2()).pk)
        self.assertEqual(
            responsible_outlines(ctx, user_id=self.world.agent.pk),
            {self.world.project.pk: ["1"]},
        )
        self.assertEqual(responsible_outlines(ctx, user_id=self.world.agent.pk, project_ids=[]), {})

    def test_matches_effective_responsibles(self) -> None:
        """La cobertura por esquema y la herencia por `parent` dicen lo mismo."""
        self.world.responsible(1)
        self.world.responsible(6)
        ctx = self.world.ctx()
        outlines = responsible_outlines(ctx, user_id=self.world.agent.pk)[self.world.project.pk]
        by_parent = effective_responsibles(ctx, self.world.project)
        for task in Task.objects.filter(project=self.world.project):
            with self.subTest(task=task.outline_number):
                self.assertEqual(
                    covers(outlines, task.outline_number),
                    self.world.agent in by_parent[task.pk],
                )

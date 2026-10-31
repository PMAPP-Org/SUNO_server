"""Páginas /plataforma/proyectos/: acceso por rol y contenido."""

from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import AppUser, Project, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import

from .samples import v1

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/proyectos/"


class ViewTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.supervisor = make_user(self.org, username="super", role=Role.SUPERVISOR)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.project = Project.objects.create(
            org=self.org, name="Obra Norte", created_by=self.admin
        )

    def as_user(self, user: AppUser) -> None:
        self.client.force_login(user)

    def import_v1(self) -> None:
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        apply_import(ctx, stage_import(ctx, self.project, file_name="v1.xml", data=v1()).pk)
        self.project.refresh_from_db()


@skip_without_db
class ProjectListTests(ViewTestCase):
    def test_requires_login(self) -> None:
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/plataforma/ingresar/", response["Location"])

    def test_admin_sees_projects_and_create_form(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(BASE)
        self.assertContains(response, "Obra Norte")
        self.assertContains(response, 'name="name"')

    def test_viewer_sees_projects_without_create_form(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(BASE)
        self.assertContains(response, "Obra Norte")
        self.assertNotContains(response, 'name="name"')

    def test_agent_without_tasks_sees_empty_list(self) -> None:
        self.as_user(self.agent)
        self.assertNotContains(self.client.get(BASE), "Obra Norte")

    def test_admin_creates_project(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(BASE, {"name": "Obra Sur", "code": "OS-1", "description": ""})
        project = Project.objects.get(name="Obra Sur")
        self.assertRedirects(response, f"{BASE}{project.pk}/", fetch_redirect_response=False)
        self.assertEqual(project.org_id, self.org.pk)

    def test_duplicate_name_shows_error(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(BASE, {"name": "Obra Norte", "code": "", "description": ""})
        self.assertContains(response, "Ya existe un proyecto con ese nombre")

    def test_supervisor_cannot_create(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(BASE, {"name": "X", "code": "", "description": ""})
        self.assertEqual(response.status_code, 403)

    def test_list_shows_progress_once_imported(self) -> None:
        self.import_v1()
        self.as_user(self.admin)
        response = self.client.get(BASE)
        self.assertContains(response, "83,3")  # % programado del proyecto

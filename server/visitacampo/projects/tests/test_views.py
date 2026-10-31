"""Páginas /plataforma/proyectos/: acceso por rol y contenido."""

from __future__ import annotations

import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    AuditEvent,
    Project,
    Role,
    ScheduleImport,
    Task,
    TaskResponsible,
)
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
        self.assertRedirects(response, f"{BASE}{project.pk}/")
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


@skip_without_db
class SchedulePageTests(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_v1()
        self.url = f"{BASE}{self.project.pk}/"

    def test_shows_wbs_with_both_progress_columns(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, "Descapote")
        self.assertContains(response, "% SUNO")
        self.assertContains(response, "% Project")
        self.assertContains(response, "Cuadrilla A")
        self.assertContains(response, 'class="pj-badge"', count=3)  # 3 tareas críticas

    def test_filters_by_critical(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"criticas": "1"})
        self.assertNotContains(response, "Localización")
        self.assertContains(response, "Descapote")

    def test_cut_date_parameter(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"corte": "2026-03-06"})
        self.assertContains(response, 'value="2026-03-06"')

    def test_bad_cut_date_falls_back_to_status_date(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"corte": "ayer"})
        self.assertContains(response, 'value="2026-03-20"')

    def test_foreign_or_invisible_project_is_404(self) -> None:
        self.as_user(self.agent)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        other = make_org("Otra")
        outsider = make_user(other, username="otro", role=Role.ADMIN)
        self.as_user(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)


@skip_without_db
class TaskPageTests(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_v1()
        self.task = self.project.tasks.get(uid=3)
        self.url = f"{BASE}{self.project.pk}/tareas/{self.task.pk}/"

    def test_shows_task_data(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, "Descapote")
        self.assertContains(response, "Cuadrilla A")
        self.assertNotContains(response, 'name="user"')  # el observador no asigna

    def test_supervisor_assigns_and_removes_responsible(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(self.url, {"action": "add", "user": str(self.agent.pk)})
        self.assertRedirects(response, self.url)
        self.assertTrue(TaskResponsible.objects.filter(task=self.task, user=self.agent).exists())
        self.assertTrue(AuditEvent.objects.filter(action="projects.responsible_assigned").exists())
        self.client.post(self.url, {"action": "remove", "user": str(self.agent.pk)})
        self.assertFalse(TaskResponsible.objects.filter(task=self.task).exists())

    def test_cannot_assign_user_from_another_org(self) -> None:
        other = make_org("Otra")
        outsider = make_user(other, username="otro")
        self.as_user(self.admin)
        response = self.client.post(self.url, {"action": "add", "user": str(outsider.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TaskResponsible.objects.exists())

    def test_viewer_cannot_assign(self) -> None:
        self.as_user(self.viewer)
        response = self.client.post(self.url, {"action": "add", "user": str(self.agent.pk)})
        self.assertEqual(response.status_code, 403)

    def test_task_of_other_project_is_404(self) -> None:
        other = Project.objects.create(org=self.org, name="Otra obra", created_by=self.admin)
        self.as_user(self.admin)
        url = f"{BASE}{other.pk}/tareas/{self.task.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)


@skip_without_db
class ImportPagesTests(ViewTestCase):
    def upload(self, data: bytes, name: str = "obra.xml"):
        return self.client.post(
            f"{BASE}{self.project.pk}/importar/",
            {"file": SimpleUploadedFile(name, data, content_type="text/xml")},
        )

    def test_upload_shows_preview_then_confirm_applies(self) -> None:
        self.as_user(self.admin)
        response = self.upload(v1())
        staged = ScheduleImport.objects.get(project=self.project)
        preview_url = f"{BASE}{self.project.pk}/importar/{staged.pk}/"
        self.assertRedirects(response, preview_url)
        preview = self.client.get(preview_url)
        self.assertContains(preview, "6 nuevas")
        response = self.client.post(preview_url, {"action": "confirm"})
        self.assertRedirects(response, f"{BASE}{self.project.pk}/")
        self.assertEqual(self.project.tasks.count(), 6)
        self.assertTrue(AuditEvent.objects.filter(action="projects.schedule_imported").exists())

    def test_discard(self) -> None:
        self.as_user(self.admin)
        self.upload(v1())
        staged = ScheduleImport.objects.get(project=self.project)
        self.client.post(f"{BASE}{self.project.pk}/importar/{staged.pk}/", {"action": "discard"})
        staged.refresh_from_db()
        self.assertEqual(staged.status, "discarded")
        self.assertEqual(self.project.tasks.count(), 0)

    def test_invalid_file_shows_error(self) -> None:
        self.as_user(self.admin)
        response = self.upload(b"<html/>")
        self.assertContains(response, "MSPDI")
        self.assertFalse(ScheduleImport.objects.exists())

    def test_supervisor_cannot_import(self) -> None:
        self.as_user(self.supervisor)
        self.assertEqual(self.client.get(f"{BASE}{self.project.pk}/importar/").status_code, 403)
        self.assertEqual(self.upload(v1()).status_code, 403)

    def test_history_lists_applied_imports_and_downloads_xml(self) -> None:
        self.import_v1()
        self.as_user(self.viewer)
        response = self.client.get(f"{BASE}{self.project.pk}/historial/")
        self.assertContains(response, "v1.xml")
        imp = ScheduleImport.objects.get(project=self.project)
        download = self.client.get(f"{BASE}{self.project.pk}/historial/{imp.pk}/xml")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, v1())
        self.assertIn("attachment", download["Content-Disposition"])


@skip_without_db
class GanttPageTests(ViewTestCase):
    def test_embeds_rows_links_and_cut(self) -> None:
        import json

        self.import_v1()
        self.as_user(self.viewer)
        response = self.client.get(f"{BASE}{self.project.pk}/gantt/")
        self.assertContains(response, 'id="gantt-data"')
        payload = json.loads(response.context["gantt_json"])
        self.assertEqual(payload["cut"], "2026-03-20")
        self.assertEqual(len(payload["rows"]), 6)
        columns = next(r for r in payload["rows"] if r["name"] == "Columnas")
        self.assertEqual(
            (columns["start"], columns["finish"], columns["bstart"], columns["bfinish"]),
            ("2026-03-18", "2026-03-30", "2026-03-16", "2026-03-26"),
        )
        self.assertTrue(columns["critical"])
        self.assertEqual(len(payload["links"]), 3)

    def test_payload_escapes_markup(self) -> None:
        self.import_v1()
        task = Task.objects.filter(project=self.project).first()
        assert task is not None
        task.name = "</script><b>&"
        task.save()
        self.as_user(self.viewer)
        response = self.client.get(f"{BASE}{self.project.pk}/gantt/")
        self.assertNotContains(response, "</script><b>")


@skip_without_db
class ExportTests(ViewTestCase):
    def test_supervisor_exports_xlsx_and_it_is_audited(self) -> None:
        import io
        import zipfile

        self.import_v1()
        self.as_user(self.supervisor)
        response = self.client.get(f"{BASE}{self.project.pk}/exportar.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Descapote", sheet)
        self.assertIn("pct_suno", sheet)
        self.assertIn("2026-03-18", sheet)
        self.assertTrue(AuditEvent.objects.filter(action="projects.schedule_exported").exists())

    def test_viewer_cannot_export(self) -> None:
        self.import_v1()
        self.as_user(self.viewer)
        self.assertEqual(self.client.get(f"{BASE}{self.project.pk}/exportar.xlsx").status_code, 403)

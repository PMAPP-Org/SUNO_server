"""Pestaña "Tareo" (spec 3.2 y 3.5): quién la ve, asignaciones y planillas."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.test import TestCase

from suno.core.access import AccessScope, SectionRole
from suno.core.models import Project, ProjectWorker, Timesheet
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, bare_timesheet, grant_labor, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TabTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.base = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/"

    def test_who_sees_the_tab(self) -> None:
        sheet = bare_timesheet(self.lw)
        for user, status in (
            (self.lw.world.admin, 200),
            (self.lw.viewer, 200),
            (self.lw.accountant, 404),  # Costos sin Mano de obra
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.base).status_code, status)
                self.assertEqual(self.client.get(f"{self.base}{sheet.pk}/").status_code, status)
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(f"/plataforma/proyectos/{self.lw.project.pk}/costos/"),
                            f'href="{self.base}"', html=False)  # fmt: skip
        self.client.force_login(self.lw.accountant)
        self.assertNotContains(
            self.client.get(f"/plataforma/proyectos/{self.lw.project.pk}/costos/"), "Tareo"
        )

    def test_ids_of_other_projects_or_organizations_are_404(self) -> None:
        other = Project.objects.create(
            org=self.lw.world.org, name="Otra", created_by=self.lw.world.admin
        )
        sheet = bare_timesheet(self.lw, project=other)
        foreign = labor_world("Ajena")
        foreign_sheet = bare_timesheet(foreign)
        self.client.force_login(self.lw.world.admin)
        for url in (f"{self.base}{sheet.pk}/", f"{self.base}{foreign_sheet.pk}/",
                    f"/plataforma/proyectos/{foreign.project.pk}/tareo/"):  # fmt: skip
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_project_scope_limits_the_tab(self) -> None:
        other = Project.objects.create(
            org=self.lw.world.org, name="Otra", created_by=self.lw.world.admin
        )
        user = self.lw.cw.contributor
        grant_labor(self.lw.world, user, SectionRole.CONTRIBUTOR, scope=AccessScope.PROJECTS,
                    projects=[self.lw.project])  # fmt: skip
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(
            self.client.get(f"/plataforma/proyectos/{other.pk}/tareo/").status_code, 404
        )

    def test_assignments_show_masked_documents_and_are_created_here(self) -> None:
        luis = make_worker(self.lw.world.org, "70111222", "Luis Torres", hired=date(2026, 2, 1))
        self.client.force_login(self.lw.contributor)
        page = self.client.get(f"{self.base}asignaciones/")
        self.assertContains(page, "CC ***5678")
        self.assertNotContains(page, "1012345678")
        self.assertNotContains(page, "80.000")  # sin jornales en la pestaña
        created = self.client.post(f"{self.base}asignaciones/", {
            "worker": str(luis.pk), "starts_on": "2026-02-01", "ends_on": "",
        })  # fmt: skip
        self.assertRedirects(created, f"{self.base}asignaciones/")
        self.assertTrue(ProjectWorker.objects.filter(worker=luis, project=self.lw.project).exists())
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.post(f"{self.base}asignaciones/", {
            "worker": str(luis.pk), "starts_on": "2026-04-01"}).status_code, 403)  # fmt: skip

    def test_list_and_create(self) -> None:
        self.client.force_login(self.lw.contributor)
        listed = self.client.get(self.base)
        self.assertContains(listed, 'value="2026-04-06"', html=False)  # semana anterior
        created = self.client.post(f"{self.base}nueva/", {"starts_on": "2026-04-06",
                                                         "ends_on": "2026-04-12"})  # fmt: skip
        sheet = Timesheet.objects.get(project=self.lw.project)
        self.assertRedirects(created, f"{self.base}{sheet.pk}/")
        again = self.client.post(
            f"{self.base}nueva/", {"starts_on": "2026-04-06", "ends_on": "2026-04-12"}, follow=True
        )
        self.assertContains(again, "Se cruza con la planilla TA-001")
        self.assertContains(self.client.get(self.base), "TA-001")

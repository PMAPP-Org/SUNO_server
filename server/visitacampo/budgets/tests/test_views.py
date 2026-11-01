"""Páginas de presupuestos: acceso por rol, lista, comparación, importación, historial."""

from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    AuditEvent,
    Budget,
    BudgetItem,
    Project,
    Role,
    Task,
    TaskResponsible,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.samples import v1 as schedule_v1

from .factories import apply_rows, make_budget
from .samples import AIU, V1_ROWS, V2_ROWS

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class BudgetViewTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.supervisor = make_user(self.org, username="super", role=Role.SUPERVISOR)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.project = Project.objects.create(
            org=self.org, name="Obra Norte", created_by=self.admin
        )
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.base = f"/plataforma/proyectos/{self.project.pk}/presupuestos/"

    def as_user(self, user: AppUser) -> None:
        self.client.force_login(user)

    def budget_v1_v2(self) -> Budget:
        """Contractual v1 (con AIU) y vigente v2 (ver `samples.py`)."""
        budget = make_budget(self.project, self.admin)
        apply_rows(self.ctx, budget, V1_ROWS, label="Contrato", charges=AIU)
        apply_rows(self.ctx, budget, V2_ROWS, label="Otrosí 1")
        budget.refresh_from_db()
        return budget

    def budget_urls(self, budget: Budget) -> list[str]:
        """Todas las URL de presupuestos (las tareas siguientes añaden las suyas)."""
        return [self.base, f"{self.base}{budget.pk}/"]

    def make_agent_see_the_project(self) -> None:
        """El agente ve el proyecto (es responsable de una tarea del cronograma)."""
        staged = stage_import(self.ctx, self.project, file_name="v1.xml", data=schedule_v1())
        apply_import(self.ctx, staged.pk)
        task = Task.objects.filter(project=self.project).order_by("sort_order").first()
        TaskResponsible.objects.create(
            org=self.org, task=task, user=self.agent, assigned_by=self.admin
        )


@skip_without_db
class AccessTests(BudgetViewTestCase):
    def test_field_agent_gets_404_everywhere_and_has_no_tab(self) -> None:
        self.make_agent_see_the_project()
        budget = self.budget_v1_v2()
        self.as_user(self.agent)
        schedule = self.client.get(f"/plataforma/proyectos/{self.project.pk}/")
        self.assertEqual(schedule.status_code, 200)
        self.assertNotContains(schedule, "/presupuestos/")
        for url in self.budget_urls(budget):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(self.base, {"name": "X"}).status_code, 404)

    def test_the_other_roles_see_the_tab(self) -> None:
        for user in (self.admin, self.supervisor, self.viewer):
            self.as_user(user)
            with self.subTest(user=user.username):
                page = self.client.get(f"/plataforma/proyectos/{self.project.pk}/")
                self.assertContains(page, self.base)

    def test_budget_of_another_project_or_organization_is_404(self) -> None:
        budget = self.budget_v1_v2()
        other = Project.objects.create(org=self.org, name="Otra obra", created_by=self.admin)
        self.as_user(self.admin)
        url = f"/plataforma/proyectos/{other.pk}/presupuestos/{budget.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}{uuid4()}/").status_code, 404)
        outsider_org = make_org("Otra")
        self.as_user(make_user(outsider_org, username="otro", role=Role.ADMIN))
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}{budget.pk}/").status_code, 404)

    def test_requires_login(self) -> None:
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/plataforma/ingresar/", response["Location"])


@skip_without_db
class BudgetListTests(BudgetViewTestCase):
    def test_lists_budgets_with_totals_and_the_project_total(self) -> None:
        self.budget_v1_v2()
        self.as_user(self.viewer)
        response = self.client.get(self.base)
        for text in (
            "Contrato de obra",
            "11.042.735,00",
            "10.510.555,00",
            "-532.180,00",
            "-4,82 %",
            "Total del proyecto",
            "v2",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, 'name="name"')

    def test_admin_creates_a_budget_and_it_is_audited(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(
            self.base, {"name": "Interventoría", "code": "I-01", "description": ""}
        )
        budget = Budget.objects.get(name="Interventoría")
        self.assertRedirects(response, f"{self.base}{budget.pk}/")
        self.assertEqual(
            (budget.project_id, budget.org_id, budget.created_by_id),
            (self.project.pk, self.org.pk, self.admin.pk),
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="budgets.created", target=f"budget:{budget.pk}")
        )

    def test_duplicate_name_in_the_project_shows_an_error(self) -> None:
        make_budget(self.project, self.admin, name="Contrato")
        self.as_user(self.admin)
        response = self.client.post(self.base, {"name": "contrato", "code": "", "description": ""})
        self.assertContains(response, "Ya existe un presupuesto con ese nombre")

    def test_supervisor_cannot_create(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(self.base, {"name": "X", "code": "", "description": ""})
        self.assertEqual(response.status_code, 403)


@skip_without_db
class ComparisonPageTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = self.budget_v1_v2()
        self.url = f"{self.base}{self.budget.pk}/"

    def test_rows_labels_totals_and_folding(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        for text in (
            "Formaleta",
            "No previsto",
            "Eliminado",
            "Mayor cantidad",
            "Menor cantidad",
            "Cambio de precio",
            "11.042.735,00",
            "10.510.555,00",
            "IVA sobre utilidad",
            "19 % sobre línea 3 (Utilidad)",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(response, "data-toggle", count=2)
        self.assertContains(response, "data-parent=", count=6)

    def test_filters(self) -> None:
        self.as_user(self.viewer)
        changes = self.client.get(self.url, {"cambios": "1"})
        self.assertContains(changes, "Formaleta")
        self.assertNotContains(changes, "Curado")
        search = self.client.get(self.url, {"q": "acero"})
        self.assertContains(search, "Acero de refuerzo")
        self.assertNotContains(search, "Formaleta")
        chapter = BudgetItem.objects.get(budget=self.budget, code="1")
        by_chapter = self.client.get(self.url, {"capitulo": str(chapter.pk)})
        self.assertContains(by_chapter, "Cerramiento provisional")
        self.assertNotContains(by_chapter, "Formaleta")
        self.assertEqual(self.client.get(self.url, {"capitulo": "basura"}).status_code, 200)

    def test_empty_budget(self) -> None:
        empty = make_budget(self.project, self.admin, name="Vacío")
        self.as_user(self.viewer)
        response = self.client.get(f"{self.base}{empty.pk}/")
        self.assertContains(response, "Todavía no hay versiones aplicadas")

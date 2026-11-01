"""Páginas de presupuestos: acceso por rol, lista, comparación, importación, historial."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    AuditEvent,
    Budget,
    BudgetItem,
    BudgetVersion,
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

from .factories import apply_rows, make_budget, stage_rows
from .samples import AIU, V1_ROWS, V2_ROWS, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
        some = uuid4()
        return [
            self.base,
            f"{self.base}{budget.pk}/",
            f"{self.base}{budget.pk}/importar/",
            f"{self.base}{budget.pk}/importar/{some}/",
            f"{self.base}{budget.pk}/importar/{some}/mapeo/",
        ]

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


@skip_without_db
class ImportPagesTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = make_budget(self.project, self.admin)
        self.url = f"{self.base}{self.budget.pk}/importar/"

    def upload(self, rows: list[list[Any]], name: str = "v1.xlsx", **extra: str) -> Any:
        upload = SimpleUploadedFile(name, xlsx(rows), content_type=XLSX)
        return self.client.post(self.url, {"file": upload, **extra})

    def pending(self) -> BudgetVersion:
        return BudgetVersion.objects.get(budget=self.budget, status="pending")

    @staticmethod
    def mapping_post(**changes: str) -> dict[str, str]:
        data = {
            "sheet": "Presupuesto",
            "first_row": "2",
            "col_code": "A",
            "col_description": "B",
            "col_unit": "C",
            "col_quantity": "D",
            "col_unit_price": "E",
            "col_total": "F",
        }
        return {**data, **changes}

    @staticmethod
    def charges_post(action: str = "confirm", label: str = "") -> dict[str, str]:
        """El formulario de la vista previa con AIU 10 / 5 / 5 / IVA 19 % sobre la utilidad."""
        data = {"action": action, "label": label, "charge_count": "5"}
        lines = [
            ("Administración", "10", "direct"),
            ("Imprevistos", "5", "direct"),
            ("Utilidad", "5", "direct"),
            ("IVA sobre utilidad", "19", "line:3"),
        ]
        for index, (name, pct, base) in enumerate(lines, start=1):
            data[f"charge_{index}_name"] = name
            data[f"charge_{index}_pct"] = pct
            data[f"charge_{index}_base"] = base
        return data

    def test_first_upload_goes_through_mapping_and_preview_and_applies(self) -> None:
        self.as_user(self.admin)
        response = self.upload(V1_ROWS)
        version = self.pending()
        mapping_url = f"{self.url}{version.pk}/mapeo/"
        self.assertRedirects(response, mapping_url)

        page = self.client.get(mapping_url)
        self.assertContains(page, "Vr. unitario")
        fields = {f["name"]: f["value"] for f in page.context["fields"]}
        self.assertEqual(
            fields,
            {
                "code": "A",
                "description": "B",
                "unit": "C",
                "quantity": "D",
                "unit_price": "E",
                "total": "F",
            },
        )
        self.assertEqual(page.context["first_row"], 2)

        preview_url = f"{self.url}{version.pk}/"
        self.assertRedirects(self.client.post(mapping_url, self.mapping_post()), preview_url)
        preview = self.client.get(preview_url)
        self.assertContains(preview, "5 no previstos")
        self.assertContains(preview, "9.130.000,00")
        self.assertContains(preview, 'name="charge_4_base"')

        response = self.client.post(preview_url, self.charges_post(label="Contrato"))
        self.assertRedirects(response, f"{self.base}{self.budget.pk}/")
        version.refresh_from_db()
        self.assertEqual(
            (version.number, version.label, version.total), (1, "Contrato", D("11042735.00"))
        )
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, version.pk)
        event = AuditEvent.objects.get(action="budgets.version_applied")
        self.assertEqual(event.metadata["version"], 1)

    def test_second_upload_reuses_the_saved_mapping(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        self.as_user(self.admin)
        response = self.upload(V2_ROWS, name="v2.xlsx")
        version = self.pending()
        self.assertRedirects(response, f"{self.url}{version.pk}/")
        preview = self.client.get(f"{self.url}{version.pk}/")
        for text in (
            "1 no previstos",
            "1 eliminados",
            "1 con mayor cantidad",
            "1 con menor cantidad",
            "1 con cambio de precio",
            "10.510.555,00",
        ):
            with self.subTest(text=text):
                self.assertContains(preview, text)

    def test_asking_to_change_the_mapping(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        self.as_user(self.admin)
        response = self.upload(V2_ROWS, cambiar_mapeo="1")
        self.assertRedirects(response, f"{self.url}{self.pending().pk}/mapeo/")

    def test_mapping_errors_are_shown_and_nothing_is_saved(self) -> None:
        self.as_user(self.admin)
        self.upload(V1_ROWS)
        mapping_url = f"{self.url}{self.pending().pk}/mapeo/"
        response = self.client.post(mapping_url, self.mapping_post(col_description="A"))
        self.assertContains(response, "Una misma columna no puede tener dos significados")
        response = self.client.post(mapping_url, self.mapping_post(first_row="1"))
        self.assertContains(response, "Fila 1 (código Ítem)")
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.column_mapping, {})

    def test_xlsm_is_rejected(self) -> None:
        self.as_user(self.admin)
        response = self.upload(V1_ROWS, name="v1.xlsm")
        self.assertContains(response, "macros")
        self.assertFalse(BudgetVersion.objects.exists())

    def test_save_shows_amounts_bad_charges_show_an_error_and_discard(self) -> None:
        self.as_user(self.admin)
        self.upload(V1_ROWS)
        version = self.pending()
        self.client.post(f"{self.url}{version.pk}/mapeo/", self.mapping_post())
        preview_url = f"{self.url}{version.pk}/"

        saved = self.client.post(
            preview_url, self.charges_post(action="save", label="Contrato"), follow=True
        )
        self.assertContains(saved, "913.000,00")
        version.refresh_from_db()
        self.assertEqual(version.label, "Contrato")

        bad = {**self.charges_post(action="save"), "charge_4_base": "line:4"}
        self.assertContains(self.client.post(preview_url, bad, follow=True), "la base debe ser")

        response = self.client.post(preview_url, {"action": "discard"})
        self.assertRedirects(response, f"{self.base}{self.budget.pk}/")
        version.refresh_from_db()
        self.assertEqual(version.status, "discarded")
        self.assertTrue(AuditEvent.objects.filter(action="budgets.version_discarded"))

    def test_stale_preview_is_rejected_with_a_message(self) -> None:
        old = stage_rows(self.ctx, self.budget, V1_ROWS)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        self.as_user(self.admin)
        response = self.client.post(f"{self.url}{old.pk}/", self.charges_post(), follow=True)
        self.assertContains(response, "Vuelve a subir el archivo")
        old.refresh_from_db()
        self.assertEqual(old.status, "pending")

    def test_supervisor_cannot_import(self) -> None:
        self.as_user(self.supervisor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.upload(V1_ROWS).status_code, 403)

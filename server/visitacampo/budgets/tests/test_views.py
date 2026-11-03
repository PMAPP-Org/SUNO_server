"""Páginas de presupuestos: acceso por rol, lista, comparación, importación, historial."""

from __future__ import annotations

import io
import unittest
import zipfile
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
from .samples import AIU, V1_ROWS, V2_ROWS, V3_ROWS, xlsx

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
            f"{self.base}{budget.pk}/historial/",
            f"{self.base}{budget.pk}/versiones/{budget.contractual_version_id}/",
            f"{self.base}{budget.pk}/versiones/{budget.contractual_version_id}/excel",
            f"{self.base}{budget.pk}/exportar.xlsx",
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
        self.assertContains(preview, "<strong>5 no previstos</strong>")
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
            "<strong>1 no previsto</strong>",
            "<strong>1 eliminado</strong>",
            "1 con mayor cantidad",
            "1 con menor cantidad",
            "1 con cambio de precio",
            "10.510.555,00",
        ):
            with self.subTest(text=text):
                self.assertContains(preview, text)

    def test_reappeared_item_uses_the_singular(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        self.as_user(self.admin)
        self.upload(V3_ROWS, name="v3.xlsx")
        preview = self.client.get(f"{self.url}{self.pending().pk}/")
        self.assertContains(preview, "<strong>1 vuelve</strong>")

    def test_long_cells_in_the_mapping_page_get_an_ellipsis_and_a_title(self) -> None:
        long_text = "Suministro e instalación de " + "x" * 80
        self.as_user(self.admin)
        self.upload([["1", long_text, "m2", 1, 100, 100]])
        page = self.client.get(f"{self.url}{self.pending().pk}/mapeo/")
        self.assertContains(page, f'title="{long_text}"')
        self.assertContains(page, "…</td>")
        self.assertNotContains(page, f">{long_text}<")

    def test_discard_button_sits_in_the_mapping_form(self) -> None:
        self.as_user(self.admin)
        self.upload(V1_ROWS)
        version = self.pending()
        page = self.client.get(f"{self.url}{version.pk}/mapeo/")
        self.assertContains(page, f'formaction="{self.url}{version.pk}/"')
        response = self.client.post(
            f"{self.url}{version.pk}/", {**self.mapping_post(), "action": "discard"}
        )
        self.assertRedirects(response, f"{self.base}{self.budget.pk}/")
        version.refresh_from_db()
        self.assertEqual(version.status, "discarded")

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


@skip_without_db
class HistoryAndVersionTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = self.budget_v1_v2()
        self.v1 = BudgetVersion.objects.get(budget=self.budget, number=1)
        self.v2 = BudgetVersion.objects.get(budget=self.budget, number=2)
        self.history = f"{self.base}{self.budget.pk}/historial/"

    def version_url(self, version: BudgetVersion, suffix: str = "") -> str:
        return f"{self.base}{self.budget.pk}/versiones/{version.pk}/{suffix}"

    def test_history_lists_versions_and_marks_the_contractual(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.history)
        for text in ("Otrosí 1", "contractual", "11.042.735,00", "10.510.555,00"):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(response, self.version_url(self.v1, "excel"))
        self.assertNotContains(response, "Marcar como contractual")

    def test_admin_marks_another_version_as_contractual(self) -> None:
        self.as_user(self.admin)
        self.assertContains(self.client.get(self.history), "Marcar como contractual", count=1)
        response = self.client.post(
            self.history, {"action": "contractual", "version": str(self.v2.pk)}
        )
        self.assertRedirects(response, self.history)
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, self.v2.pk)
        event = AuditEvent.objects.get(action="budgets.contractual_changed")
        self.assertEqual(event.metadata, {"from": 1, "to": 2})

    def test_supervisor_cannot_mark_contractual(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(
            self.history, {"action": "contractual", "version": str(self.v2.pk)}
        )
        self.assertEqual(response.status_code, 403)

    def test_version_page_shows_its_content_and_charges(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.version_url(self.v1))
        for text in ("Cerramiento provisional", "Administración", "913.000,00", "11.042.735,00"):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, "Formaleta")

    def test_pending_or_foreign_versions_are_404(self) -> None:
        pending = stage_rows(self.ctx, self.budget, V1_ROWS)
        other = make_budget(self.project, self.admin, name="Interventoría")
        foreign = apply_rows(self.ctx, other, V1_ROWS)
        self.as_user(self.admin)
        for version in (pending, foreign):
            for suffix in ("", "excel"):
                with self.subTest(version=version.pk, suffix=suffix):
                    url = self.version_url(version, suffix)
                    self.assertEqual(self.client.get(url).status_code, 404)

    def test_viewer_downloads_the_original_excel_and_it_is_audited(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.version_url(self.v1, "excel"))
        self.assertEqual(response.status_code, 200)
        stored = BudgetVersion.objects.get(pk=self.v1.pk).source_file
        self.assertEqual(response.content, bytes(stored))
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="C-01-v1.xlsx"')
        event = AuditEvent.objects.get(action="budgets.source_downloaded")
        self.assertEqual(event.metadata, {"version": 1})


@skip_without_db
class ExportTests(BudgetViewTestCase):
    def test_viewer_exports_the_full_comparison_and_it_is_audited(self) -> None:
        budget = self.budget_v1_v2()
        self.as_user(self.viewer)
        page = self.client.get(f"{self.base}{budget.pk}/")
        self.assertContains(page, "exportar.xlsx")
        response = self.client.get(f"{self.base}{budget.pk}/exportar.xlsx", {"cambios": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="C-01-comparacion.xlsx"'
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        for text in (
            "codigo",
            "total_vigente",
            "Formaleta",
            "Curado",  # sin filtros: la exportación es completa
            "No previsto",
            "Mayor cantidad",
            "IVA sobre utilidad",
            "11042735.0",
            "10510555.0",
        ):
            with self.subTest(text=text):
                self.assertIn(text, sheet)
        event = AuditEvent.objects.get(action="budgets.exported")
        self.assertEqual(event.metadata, {"rows": 8})

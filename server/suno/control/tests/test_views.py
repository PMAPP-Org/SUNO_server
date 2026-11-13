"""Página "Control" del proyecto (costos, pieza 5)."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from unittest import mock

from django.http import HttpResponse
from django.test import TestCase

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS
from suno.core.models import AuditEvent, Budget, CostEntry, CostScope, CostStatus
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import cost_world, equipment, usage
from suno.costs.tests.factories import entry as cost_entry
from suno.matrix.tests.factories import (
    budget_world,
    import_schedule,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import make_field_world
from suno.projects.tests.samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

AT_CUT = "?corte=2026-03-20"
#: "Hoy" fijo para las pruebas del corte por defecto: la vista usa `timezone.localdate()`.
TODAY = date(2026, 3, 20)


@skip_without_db
class ControlPageTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}control/"
        self.client.force_login(self.world.admin)

    def test_field_agent_gets_404(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_another_organization_gets_404(self) -> None:
        other, _ = budget_world("Otra constructora")
        self.client.force_login(other.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_tab_in_the_project_header(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Control</a>")
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Control</a>')

    def test_indicators_at_the_cut(self) -> None:
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, 'value="2026-03-20"')
        self.assertContains(response, "$5.400.000,00")  # BAC
        self.assertContains(response, "$4.050.000,00")  # PV
        self.assertContains(response, "$900.000,00")  # EV
        self.assertContains(response, "−$3.150.000,00")  # SV, con el signo antes del símbolo
        self.assertNotContains(response, "$-")
        self.assertContains(response, "SPI 0,22")
        self.assertContains(response, "Atrasado")
        self.assertContains(response, "75,0 % del BAC")
        self.assertContains(response, "Costo directo contractual: $9.130.000,00")
        self.assertContains(response, "<polyline")

    def test_partial_contractual_reference(self) -> None:
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.world.ctx(), other, V1_ROWS)
        Budget.objects.filter(pk=other.pk).update(contractual_version=None)
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, "Costo directo contractual: $9.130.000,00")
        self.assertContains(response, "(parcial: 1 de 2 presupuestos)")

    def test_defaults_and_unknown_values(self) -> None:
        with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
            response = self.client.get(self.base + "?corte=no-es-fecha&periodo=dia&desglose=x")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="2026-03-20"')
        self.assertContains(response, "$900.000,00")  # EV al corte por defecto (hoy)
        self.assertContains(response, '<option value="mes" selected>')
        self.assertContains(response, 'aria-current="page">Por capítulo</a>')

    def test_weekly_and_monthly_periods(self) -> None:
        weekly = self.client.get(self.base + AT_CUT + "&periodo=semana")
        self.assertContains(weekly, "09-mar-2026")
        self.assertContains(weekly, "16-mar-2026 (tras el corte)")
        monthly = self.client.get(self.base + AT_CUT + "&periodo=mes")
        self.assertContains(monthly, "Mar-2026")
        self.assertNotContains(monthly, "09-mar-2026")

    def test_breakdown_tabs(self) -> None:
        by_chapter = self.client.get(self.base + AT_CUT)
        self.assertContains(by_chapter, "2 Estructura")
        self.assertContains(by_chapter, "desglose=tarea")
        by_task = self.client.get(self.base + AT_CUT + "&desglose=tarea")
        task = self.world.task(5)
        self.assertContains(by_task, f'href="{self.project_url}tareas/{task.pk}/"')
        self.assertContains(by_task, "2.1 Columnas")
        by_location = self.client.get(self.base + AT_CUT + "&desglose=ubicacion")
        self.assertContains(by_location, "Tramo 1")
        self.assertContains(by_location, "Sin ubicación")

    def test_warnings(self) -> None:
        steel = item(self.budget, "2.2")
        self.world.approve(quantity_record(self.world, 3, quantity_row(steel, "10"), hours=1))
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, "Ejecutado fuera del plan (1)")
        self.assertContains(response, "2.2 Acero de refuerzo")
        self.assertContains(response, "Tareas medidas por % (1)")
        self.assertContains(response, "1.1 Localización")

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/")
        self.assertContains(response, "no tiene un presupuesto aplicado")
        self.assertNotContains(response, "<polyline")

    def test_project_without_plan(self) -> None:
        world, _ = budget_world("Sin plan")
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/")
        self.assertContains(response, "no tiene cantidades planeadas")

    def test_plans_only_on_retired_tasks_count_as_no_plan(self) -> None:
        world, budget = budget_world("Tarea retirada")
        plan(world, 5, item(budget, "2.1"), "8")
        import_schedule(world, v2())
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/")
        self.assertContains(response, "no tiene cantidades planeadas")
        self.assertNotContains(response, "Revisa los avisos")

    def test_dates_out_of_range_show_a_banner(self) -> None:
        response = self.client.get(self.base + "?corte=9999-12-31&periodo=semana")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "más de 1000 periodos")
        self.assertNotContains(response, "<polyline")


@skip_without_db
class ControlExportTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        concrete = item(self.budget, "2.1")
        plan(self.world, 3, concrete, "4")
        plan(self.world, 5, concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(concrete, "2")))
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/control/control.xlsx"
        self.client.force_login(self.world.supervisor)

    def test_export_has_both_sheets_and_is_audited(self) -> None:
        response = self.client.get(self.url + "?corte=2026-03-20&periodo=semana")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("2026-03-20", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            periods = book.read("xl/worksheets/sheet1.xml").decode()
            breakdown = book.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn(">09-mar-2026<", periods)
        self.assertIn(">2.1 Columnas<", breakdown)
        event = AuditEvent.objects.get(action="control.exported")
        self.assertEqual(event.target, f"project:{self.world.project.pk}")
        self.assertEqual(event.metadata, {"cut": "2026-03-20", "period": "semana"})

    def test_link_on_the_page(self) -> None:
        with mock.patch("django.utils.timezone.localdate", return_value=TODAY):
            page = self.client.get(f"/plataforma/proyectos/{self.world.project.pk}/control/")
        self.assertContains(page, "Exportar a Excel")
        self.assertContains(page, "control.xlsx?corte=2026-03-20&amp;periodo=mes")

    def test_field_agent_gets_404(self) -> None:
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(AuditEvent.objects.filter(action="control.exported").exists())

    def test_dates_out_of_range_are_a_400(self) -> None:
        response = self.client.get(self.url + "?corte=9999-12-31&periodo=semana")
        self.assertEqual(response.status_code, 400)
        self.assertIn("más de 1000 periodos", response.content.decode())
        self.assertFalse(AuditEvent.objects.filter(action="control.exported").exists())

    def test_without_export_permission_gets_403(self) -> None:
        # Hoy ningún rol lee presupuestos sin poder exportarlos (matriz de capacidades):
        # se simula la política para cubrir la rama de la vista.
        with mock.patch("suno.control.views.can_export_budgets", return_value=False):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuditEvent.objects.filter(action="control.exported").exists())


@skip_without_db
class ControlCostTests(TestCase):
    """Costos, pieza 6a: fila de costo, serie AC, columnas, orden, avisos y Excel.

    EV 900.000 (2 m3 × 450.000); AC directo 1.200.000; indirecto 100.000; BAC 5.400.000;
    Administración 10 % (AIU de `cost_world`)."""

    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.concrete = item(self.budget, "2.1")
        self.structure = item(self.budget, "2")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))
        cost_entry(
            self.world,
            amount="1200000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=self.world.task(5),
        )
        cost_entry(self.world, amount="100000.00", status=CostStatus.APPROVED)
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/control/"
        self.client.force_login(self.world.admin)

    def test_cost_row_curve_and_columns(self) -> None:
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, "Costo real directo (AC)")
        self.assertContains(response, "$1.200.000,00")
        self.assertContains(response, "CPI 0,75 · Sobrecosto")
        self.assertContains(response, "$7.200.000,00")  # EAC
        self.assertContains(response, 'class="ctl-over"')
        self.assertContains(response, "Rendimiento necesario para terminar en presupuesto")
        self.assertContains(response, "CPI adm. 0,90")
        self.assertContains(response, "CPI total 0,76")
        self.assertContains(response, 'class="ctl-ac"')
        self.assertContains(response, "AC acumulado")
        self.assertContains(response, '<th class="num">CPI</th>')

    def test_hidden_without_costs_permission(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.base + AT_CUT + "&orden=cpi")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SPI 0,22")
        self.assertNotContains(response, "Costo real")
        self.assertNotContains(response, 'class="ctl-ac"')
        self.assertNotContains(response, "Ordenar por")
        self.assertContains(response, "De peor a mejor SPI")

    def test_order_by_cpi_and_the_item_tab(self) -> None:
        cost_entry(
            self.world,
            amount="50000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        by_cpi = self.client.get(self.base + AT_CUT + "&orden=cpi")
        self.assertContains(by_cpi, "De peor a mejor CPI")
        self.assertContains(by_cpi, '<option value="cpi" selected>')
        self.assertContains(by_cpi, "orden=cpi&amp;desglose=tarea")
        items = self.client.get(self.base + AT_CUT + "&desglose=item&orden=cpi")
        self.assertContains(items, 'aria-current="page">Por ítem</a>')
        self.assertContains(items, "2.1 Concreto zapatas")
        self.assertContains(items, "Índice del grupo: 2 Estructura")

    def test_empty_state_and_warnings(self) -> None:
        CostEntry.objects.all().delete()
        cost_entry(self.world, amount="700.00")  # borrador: no cuenta
        empty = self.client.get(self.base + AT_CUT)
        self.assertContains(empty, "Sin costos aprobados al corte")
        self.assertContains(empty, f"/plataforma/proyectos/{self.world.project.pk}/costos/")
        self.assertContains(empty, "Borradores por aprobar hasta el corte (1")
        usage(self.world, equipment(self.world), date(2026, 3, 10), "500.00")
        warned = self.client.get(self.base + AT_CUT)
        self.assertContains(warned, "Costo directo sin asignar")
        self.assertContains(warned, "Equipos propios sin ítem")

    def test_excel_cost_columns_only_with_permission(self) -> None:
        url = self.base + "control.xlsx?corte=2026-03-20&periodo=mes"

        def sheets(response: HttpResponse) -> tuple[str, str]:
            with zipfile.ZipFile(io.BytesIO(response.content)) as book:
                return (
                    book.read("xl/worksheets/sheet1.xml").decode(),
                    book.read("xl/worksheets/sheet3.xml").decode(),
                )

        periods, indicators = sheets(self.client.get(url))
        self.assertIn(">ac_acumulado<", periods)
        self.assertIn(">CPI<", indicators)
        self.client.force_login(self.world.supervisor)
        periods, indicators = sheets(self.client.get(url))
        self.assertNotIn(">ac_acumulado<", periods)
        self.assertIn(">SPI<", indicators)
        self.assertNotIn(">CPI<", indicators)

    def test_cost_is_reported_without_plan_pairs(self) -> None:
        world, _ = cost_world("Sin matriz")
        cost_entry(world, amount="250000.00", status=CostStatus.APPROVED)
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/" + AT_CUT)
        self.assertContains(response, "La matriz no tiene cantidades planeadas")
        self.assertContains(response, "Costo real al corte")
        self.assertContains(response, "$250.000,00")

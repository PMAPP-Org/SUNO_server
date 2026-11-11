"""Página "Control" del proyecto (costos, pieza 5)."""

from __future__ import annotations

import unittest

from django.test import TestCase
from django.utils import timezone

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.tests.factories import (
    budget_world,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

AT_CUT = "?corte=2026-03-20"


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
        self.assertContains(response, "-3.150.000,00")  # SV
        self.assertContains(response, "SPI 0,22")
        self.assertContains(response, "Atrasado")
        self.assertContains(response, "75,0 % del BAC")
        self.assertContains(response, "Costo directo contractual: $9.130.000,00")
        self.assertContains(response, "<polyline")

    def test_defaults_and_unknown_values(self) -> None:
        response = self.client.get(self.base + "?corte=no-es-fecha&periodo=dia&desglose=x")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')
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

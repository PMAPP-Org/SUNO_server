from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.models import QuantityPlan
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan, quantity_row, quantity_visit, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TaskPageTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(self.concrete, "4")))
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"

    def test_block_with_amounts_for_budget_viewers(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Cantidades planeadas")
        self.assertContains(response, "Concreto zapatas")
        self.assertContains(response, "4.500.000,00")  # valor planeado
        self.assertNotContains(response, "Agregar ítem")  # supervisión no edita

    def test_field_agent_sees_quantities_without_amounts(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        response = self.client.get(self.url)
        self.assertContains(response, "Concreto zapatas")
        self.assertNotContains(response, "4.500.000,00")

    def test_admin_edits_plan_and_location(self) -> None:
        self.client.force_login(self.world.admin)
        self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "2.2", "quantity": "50"},
        )
        self.assertEqual(QuantityPlan.objects.count(), 2)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "9.9", "quantity": "1"},
        )
        self.assertContains(response, "no existe")
        self.client.post(self.url, {"action": "location_set", "location": "Tramo 4"})
        self.assertContains(self.client.get(self.url), "Tramo 4")
        self.client.post(self.url, {"action": "plan_remove", "item": item(self.budget, "2.2").pk})
        self.assertEqual(QuantityPlan.objects.count(), 1)

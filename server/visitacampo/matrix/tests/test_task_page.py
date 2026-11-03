from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.budgets.tests.factories import apply_rows, make_budget
from visitacampo.budgets.tests.samples import V1_ROWS
from visitacampo.core.models import Project, QuantityPlan, ScheduleImport
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
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "2.2", "quantity": "50"},
        )
        self.assertRedirects(response, self.url)
        self.assertEqual(QuantityPlan.objects.count(), 2)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "9.9", "quantity": "1"},
        )
        self.assertContains(response, "no existe")
        response = self.client.post(self.url, {"action": "location_set", "location": "Tramo 4"})
        self.assertRedirects(response, self.url)
        self.assertContains(self.client.get(self.url), "Tramo 4")
        response = self.client.post(
            self.url, {"action": "plan_remove", "item": item(self.budget, "2.2").pk}
        )
        self.assertRedirects(response, self.url)
        self.assertEqual(QuantityPlan.objects.count(), 1)

    def test_only_budget_managers_edit(self) -> None:
        self.world.responsible(3)
        posts = [
            {"action": "plan_set", "budget": self.budget.pk, "code": "2.2", "quantity": "50"},
            {"action": "plan_remove", "item": self.concrete.pk},
            {"action": "location_set", "location": "Tramo 4"},
        ]
        for user in (self.world.supervisor, self.world.agent):
            self.client.force_login(user)
            for data in posts:
                with self.subTest(user=user.username, action=data["action"]):
                    self.assertEqual(self.client.post(self.url, data).status_code, 403)
        plans = QuantityPlan.objects.values_list("item__code", "planned_quantity")
        self.assertEqual(list(plans), [("2.1", Decimal("10.0000"))])
        self.assertIsNone(self.world.task(3).location_id)

    def test_foreign_budget_or_item_changes_nothing(self) -> None:
        other = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        foreign = make_budget(other, self.world.admin, code="C-02")
        apply_rows(self.world.ctx(), foreign, V1_ROWS)
        self.client.force_login(self.world.admin)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": foreign.pk, "code": "2.2", "quantity": "50"},
        )
        self.assertContains(response, "no existe en el presupuesto vigente")
        # Un ítem que no está en la tarea: no hay nada que quitar.
        response = self.client.post(
            self.url, {"action": "plan_remove", "item": item(self.budget, "2.2").pk}
        )
        self.assertRedirects(response, self.url)
        plans = QuantityPlan.objects.values_list("item_id", "planned_quantity")
        self.assertEqual(list(plans), [(self.concrete.pk, Decimal("10.0000"))])

    def test_retired_task_hides_edit_forms(self) -> None:
        task = self.world.task(3)
        task.retired_in = ScheduleImport.objects.get(project=self.world.project, number=1)
        task.save(update_fields=["retired_in"])
        self.client.force_login(self.world.admin)
        page = self.client.get(self.url)
        self.assertNotContains(page, 'value="location_set"')
        self.assertNotContains(page, 'value="plan_set"')

    def test_plan_error_keeps_what_was_typed(self) -> None:
        self.client.force_login(self.world.admin)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "9.9", "quantity": "7,5"},
        )
        self.assertContains(response, 'name="code" value="9.9"')
        self.assertContains(response, 'name="quantity" value="7,5"')

    def test_thousands_guess_is_reported(self) -> None:
        self.client.force_login(self.world.admin)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "2.2", "quantity": "1.234"},
            follow=True,
        )
        self.assertContains(response, "se leyó como miles")
        planned = QuantityPlan.objects.get(item__code="2.2").planned_quantity
        self.assertEqual(planned, Decimal("1234"))

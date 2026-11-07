from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import SPLIT_ROWS, V1_ROWS, V2_ROWS
from suno.core.models import Project, QuantityPlan, ScheduleImport, Visit
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import (
    budget_world,
    item,
    locate,
    plan,
    quantity_row,
    quantity_visit,
    suno_levels,
)

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

    def test_planned_item_that_became_a_chapter_is_highlighted(self) -> None:
        apply_rows(self.world.ctx(), self.budget, SPLIT_ROWS)
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "hoy son capítulos del presupuesto (no suman al %): 2.1")
        self.assertContains(response, "Concreto zapatas")  # la fila del plan se conserva

    def test_planned_item_retired_from_the_budget_is_marked(self) -> None:
        plan(self.world, 3, item(self.budget, "1.2"), "5")
        apply_rows(self.world.ctx(), self.budget, V2_ROWS)  # quita el 1.2
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Ítem retirado del presupuesto: 1.2 Cerramiento provisional")
        self.assertNotContains(response, "Ítem retirado del presupuesto: 2.1")

    def test_unplanned_executed_items_are_sorted_by_code(self) -> None:
        rows = [
            *V1_ROWS,
            ["2.9", "Juntas", "ml", 5, 100, 500],
            ["2.10", "Sellos", "ml", 5, 100, 500],
        ]
        apply_rows(self.world.ctx(), self.budget, rows)
        self.world.approve(
            quantity_visit(
                self.world,
                3,
                quantity_row(item(self.budget, "2.10"), "1", note="x"),
                quantity_row(item(self.budget, "2.9"), "1", note="x"),
                hours=1,
            )
        )
        self.client.force_login(self.world.supervisor)
        self.assertContains(self.client.get(self.url), "2.9 Juntas; 2.10 Sellos")

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


@skip_without_db
class TaskPageQuantityHistoryTests(TestCase):
    """% por ítem e historial de registros validados en el bloque *Cantidades planeadas*."""

    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 2")
        self.pr = locate(self.world, 5, "Tramo 2", "PR 1").location_id
        self.concrete, self.steel = item(self.budget, "2.1"), item(self.budget, "2.2")
        plan(self.world, 3, self.concrete, "10")
        plan(self.world, 3, self.steel, "800")
        self.world.approve(
            quantity_visit(
                self.world,
                3,
                quantity_row(self.concrete, "4"),
                quantity_row(self.steel, "900", location=self.pr),
            )
        )
        self.world.approve(
            quantity_visit(self.world, 3, quantity_row(self.concrete, "2.5"), hours=2)
        )
        # Sin validar y rechazada: no salen en el historial ni suman.
        quantity_visit(self.world, 3, quantity_row(self.concrete, "111"), hours=3)
        self.world.reject(
            quantity_visit(self.world, 3, quantity_row(self.concrete, "222"), hours=4)
        )
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"

    def _history(self, html: str) -> str:
        start = html.index('id="mx-history"')
        return html[start : html.index("</table>", start)]

    def test_item_pct_is_capped_at_100(self) -> None:
        self.client.force_login(self.world.supervisor)
        html = self.client.get(self.url).content.decode()
        self.assertIn('<th class="num">%</th>', html)
        self.assertIn("65 %", html)  # 6,5 de 10
        self.assertIn("100 %", html)  # 900 de 800
        self.assertNotIn("112,5 %", html)

    def test_history_lists_validated_records(self) -> None:
        self.client.force_login(self.world.supervisor)
        history = self._history(self.client.get(self.url).content.decode())
        self.assertEqual(history.count("<tr>"), 4)  # encabezado y tres registros
        self.assertIn("Tramo 2 / PR 1", history)
        self.assertIn("Tramo 2</td>", history)  # sin ubicación precisada: la de la tarea
        self.assertIn("2,5", history)
        self.assertIn("900", history)
        self.assertNotIn("111", history)
        self.assertNotIn("222", history)
        # La más reciente primero.
        self.assertLess(history.index("2,5"), history.index("900"))
        self.assertNotIn("/admin/core/visit/", history)  # sin permiso del Admin, sin enlace

    def test_history_links_to_the_visit_with_admin_permission(self) -> None:
        user = self.world.supervisor
        user.user_permissions.add(Permission.objects.get(codename="view_visit"))
        self.client.force_login(user)
        visit = Visit.objects.filter(task=self.world.task(3)).order_by("captured_at").first()
        assert visit is not None
        history = self._history(self.client.get(self.url).content.decode())
        self.assertIn(f"/admin/core/visit/{visit.pk}/change/", history)

    def test_field_agent_sees_pct_and_history(self) -> None:
        # Como el resto del bloque: lo ve quien ve la tarea; solo los montos se ocultan.
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        html = self.client.get(self.url).content.decode()
        self.assertIn("65 %", html)
        self.assertIn("Tramo 2 / PR 1", self._history(html))
        self.assertNotIn("4.500.000,00", html)

    def test_outsider_gets_404(self) -> None:
        self.client.force_login(self.world.agent)  # sin tareas a su cargo
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_queries_do_not_grow_with_the_history(self) -> None:
        self.client.force_login(self.world.supervisor)
        self.client.get(self.url)  # calienta las cachés de Django (tipos de contenido)
        with CaptureQueriesContext(connection) as before:
            self.client.get(self.url)
        for hours in range(5, 10):
            self.world.approve(
                quantity_visit(self.world, 3, quantity_row(self.concrete, "1"), hours=hours)
            )
        with CaptureQueriesContext(connection) as after:
            self.client.get(self.url)
        self.assertEqual(len(after), len(before))

    def test_history_is_bounded(self) -> None:
        self.client.force_login(self.world.supervisor)
        with mock.patch("suno.projects.views.QUANTITY_HISTORY_VISITS", 1):
            html = self.client.get(self.url).content.decode()
        history = self._history(html)
        self.assertEqual(history.count("<tr>"), 2)  # la visita más reciente: una fila
        self.assertIn("2,5", history)
        self.assertIn("Se muestran las 1 visitas validadas más recientes.", html)

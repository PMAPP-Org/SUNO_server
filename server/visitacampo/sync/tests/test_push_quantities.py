"""Push de visitas con cantidades (costos, pieza 4). Requiere PostgreSQL."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Any

from django.test import TestCase
from django.utils import timezone

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets.tests.factories import apply_rows, make_budget
from visitacampo.budgets.tests.samples import V1_ROWS
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    BudgetItem,
    Organization,
    Project,
    QuantityPlan,
    Role,
    Task,
    TaskResponsible,
    VisitReview,
    VisitReviewStatus,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import LevelSpec, load_tree, project_levels, save_levels
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.samples import v1
from visitacampo.sync.cursor import CURSOR_ZERO

from .factories import build_scenario, create_op, pull, push, update_op, visit_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class QuantityPushTests(TestCase):
    """El agente del escenario es responsable de la tarea 1.2 Descapote (uid 3)."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        org = self.scenario.org
        self.admin = make_user(org, username="admin-cantidades", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=org.pk))
        self.project = Project.objects.create(org=org, name="Obra", created_by=self.admin)
        staged = stage_import(self.ctx, self.project, file_name="x.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        TaskResponsible.objects.create(
            org=org, task=self.task(3), user=self.scenario.user, assigned_by=self.admin
        )
        budget = make_budget(self.project, self.admin)
        apply_rows(self.ctx, budget, V1_ROWS)
        self.concrete = BudgetItem.objects.get(budget=budget, code="2.1")
        self.steel = BudgetItem.objects.get(budget=budget, code="2.2")
        save_levels(self.ctx, self.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        levels = project_levels(self.ctx, self.project)
        tree = load_tree(self.ctx, self.project)
        self.tramo, _ = tree.ensure(self.ctx, levels, ("Tramo 2",))
        self.pr, _ = tree.ensure(self.ctx, levels, ("Tramo 2", "PR 1"))
        self.other, _ = tree.ensure(self.ctx, levels, ("Tramo 3",))
        task = self.task(3)
        task.location = self.tramo
        task.save(update_fields=["location"])
        QuantityPlan.objects.create(
            org=org,
            project=self.project,
            task=task,
            item=self.concrete,
            planned_quantity=Decimal("10"),
            updated_by=self.admin,
            updated_at=timezone.now(),
        )

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def row(self, item: BudgetItem, quantity: object, **extra: object) -> dict[str, Any]:
        return {"item_id": str(item.pk), "quantity": quantity, **extra}

    def push_one(self, op: dict[str, Any]) -> dict[str, Any]:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def create(self, *rows: dict[str, Any], visit_id: uuid.UUID | None = None) -> dict[str, Any]:
        return create_op(
            self.scenario,
            visit_id=visit_id,
            task_id=str(self.task(3).pk),
            progress_pct=None,
            quantities=list(rows),
        )

    def assertRejected(self, result: dict[str, Any], reason: str) -> None:
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["code"], "validation_error")
        self.assertEqual(result["error"]["details"], {"field": "quantities", "reason": reason})

    def test_planned_item_with_refined_location(self) -> None:
        visit_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 4.5, location_id=str(self.pr.pk)), visit_id=visit_id
            )
        )
        self.assertEqual(result["status"], "applied", result)
        visit = visit_of(visit_id)
        self.assertEqual(visit.quantities[0]["quantity"], "4.5000")
        self.assertEqual(visit.quantities[0]["location_id"], str(self.pr.pk))
        self.assertIsNone(visit.progress_pct)
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(r["data"] for r in rows if r["entity"] == "visit")
        self.assertEqual(
            data["quantities"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "location_id": str(self.pr.pk),
                    "quantity": 4.5,
                    "note": "",
                }
            ],
        )

    def test_task_location_and_no_location_are_accepted(self) -> None:
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.tramo.pk)),
                self.row(self.concrete, 2),
            )
        )
        self.assertEqual(result["status"], "applied", result)

    def test_location_outside_the_task(self) -> None:
        result = self.push_one(
            self.create(self.row(self.concrete, 1, location_id=str(self.other.pk)))
        )
        self.assertRejected(result, "location_outside_task")

    def test_task_without_location_rejects_a_location(self) -> None:
        task = self.task(3)
        task.location = None
        task.save(update_fields=["location"])
        result = self.push_one(self.create(self.row(self.concrete, 1, location_id=str(self.pr.pk))))
        self.assertRejected(result, "location_outside_task")

    def test_unknown_location(self) -> None:
        result = self.push_one(
            self.create(self.row(self.concrete, 1, location_id=str(uuid.uuid4())))
        )
        self.assertRejected(result, "location_outside_task")

    def test_item_of_another_project(self) -> None:
        result = self.push_one(self.create({"item_id": str(uuid.uuid4()), "quantity": 1}))
        self.assertRejected(result, "item_not_in_project")

    def test_unplanned_item_and_negative_need_a_note(self) -> None:
        self.assertRejected(self.push_one(self.create(self.row(self.steel, 3))), "note_required")
        self.assertRejected(
            self.push_one(self.create(self.row(self.concrete, -1))), "note_required"
        )
        result = self.push_one(self.create(self.row(self.steel, 3, note="no estaba en el plan")))
        self.assertEqual(result["status"], "applied", result)
        result = self.push_one(self.create(self.row(self.concrete, -1, note="ajuste")))
        self.assertEqual(result["status"], "applied", result)

    def test_validated_visit_quantities_cannot_change(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), visit_id=visit_id))
        VisitReview.objects.create(
            org=self.scenario.org,
            visit=visit_of(visit_id),
            status=str(VisitReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(
                visit_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 5)],
            )
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(
            result["error"]["details"], {"field": "quantities", "reason": "visit_validated"}
        )
        self.assertEqual(visit_of(visit_id).quantities[0]["quantity"], "4.0000")

        # Mandar las mismas cantidades no es un cambio.
        same = update_op(
            visit_id,
            version,
            task_id=str(self.task(3).pk),
            progress_pct=None,
            quantities=[self.row(self.concrete, 4)],
        )
        self.assertEqual(self.push_one(same)["status"], "applied")

    def test_validated_visit_quantities_cannot_be_cleared(self) -> None:
        """Un cliente anterior manda tarea y % sin `quantities`: eso vaciaría la lista."""
        visit_id = uuid.uuid4()
        op = create_op(
            self.scenario,
            visit_id=visit_id,
            task_id=str(self.task(3).pk),
            progress_pct=30,
            quantities=[self.row(self.concrete, 4)],
        )
        self.push_one(op)
        VisitReview.objects.create(
            org=self.scenario.org,
            visit=visit_of(visit_id),
            status=str(VisitReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(visit_id, version, task_id=str(self.task(3).pk), progress_pct=30)
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["details"]["reason"], "visit_validated")

    def test_unreviewed_visit_can_be_corrected(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), visit_id=visit_id))
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(
                visit_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 6)],
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(visit_of(visit_id).quantities[0]["quantity"], "6.0000")

    def test_update_with_the_pair_alone_clears_the_quantities(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), visit_id=visit_id))
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(visit_id, version, task_id=str(self.task(3).pk), progress_pct=25)
        )
        self.assertEqual(result["status"], "applied", result)
        visit = visit_of(visit_id)
        self.assertEqual(visit.quantities, [])
        self.assertEqual(visit.progress_pct, Decimal("25.00"))

    def _other_project(self, org: Organization | None = None) -> tuple[Project, Any]:
        """Otro proyecto con el presupuesto `V1_ROWS` aplicado, y su contexto."""
        if org is None:
            org, ctx = self.scenario.org, self.ctx
        else:
            admin = make_user(org, username="admin-otra", role=Role.ADMIN)
            ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        user = AppUser.objects.get(pk=ctx.user_id)
        project = Project.objects.create(org=org, name="Otra obra", created_by=user)
        apply_rows(ctx, make_budget(project, user, code="C-09"), V1_ROWS)
        return project, ctx

    def test_chapter_is_rejected_even_with_a_note(self) -> None:
        chapter = BudgetItem.objects.get(budget__project=self.project, code="2")
        result = self.push_one(self.create(self.row(chapter, 1, note="capítulo")))
        self.assertRejected(result, "item_not_in_project")

    def test_real_item_of_another_project_in_the_org(self) -> None:
        project, _ = self._other_project()
        foreign = BudgetItem.objects.get(budget__project=project, code="2.1")
        result = self.push_one(self.create(self.row(foreign, 1, note="otra obra")))
        self.assertRejected(result, "item_not_in_project")

    def test_real_item_of_another_org(self) -> None:
        project, _ = self._other_project(make_org("Otra organización"))
        foreign = BudgetItem.objects.get(budget__project=project, code="2.1")
        result = self.push_one(self.create(self.row(foreign, 1, note="otra org")))
        self.assertRejected(result, "item_not_in_project")

    def test_real_location_of_another_project(self) -> None:
        project, ctx = self._other_project()
        save_levels(ctx, project, [LevelSpec("Tramo"), LevelSpec("PR")])
        node, _ = load_tree(ctx, project).ensure(ctx, project_levels(ctx, project), ("Tramo 2",))
        result = self.push_one(self.create(self.row(self.concrete, 1, location_id=str(node.pk))))
        self.assertRejected(result, "location_outside_task")

    def test_retired_location_is_accepted(self) -> None:
        self.pr.retired_at = timezone.now()
        self.pr.save(update_fields=["retired_at"])
        result = self.push_one(self.create(self.row(self.concrete, 1, location_id=str(self.pr.pk))))
        self.assertEqual(result["status"], "applied", result)

    def test_repeated_item_and_location_are_kept_as_separate_rows(self) -> None:
        """Dos filas con el mismo ítem y ubicación se aceptan tal cual: no se suman ni se
        deduplican al guardar; lo ejecutado las suma al leer."""
        visit_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.pr.pk)),
                self.row(self.concrete, 2, location_id=str(self.pr.pk)),
                visit_id=visit_id,
            )
        )
        self.assertEqual(result["status"], "applied", result)
        rows = visit_of(visit_id).quantities
        self.assertEqual([r["quantity"] for r in rows], ["1.0000", "2.0000"])
        self.assertEqual({r["location_id"] for r in rows}, {str(self.pr.pk)})

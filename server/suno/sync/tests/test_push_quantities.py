"""Push de registros con cantidades (costos, pieza 4). Requiere PostgreSQL."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Any

from django.test import TestCase
from django.utils import timezone

from suno.authn.tests.factories import make_org, make_user
from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import MERGED_ROWS, SPLIT_ROWS, V1_ROWS, V2_ROWS
from suno.core.admin import RecordAdmin
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AppUser,
    BudgetItem,
    Organization,
    Project,
    QuantityPlan,
    Record,
    RecordReview,
    RecordReviewStatus,
    Role,
    Task,
    TaskResponsible,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.locations import LevelSpec, load_tree, project_levels, save_levels
from suno.projects.importer import apply_import, stage_import
from suno.projects.tests.samples import v1
from suno.sync.cursor import CURSOR_ZERO

from .factories import build_scenario, create_op, pull, push, record_of, update_op

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

    def create(self, *rows: dict[str, Any], record_id: uuid.UUID | None = None) -> dict[str, Any]:
        return create_op(
            self.scenario,
            record_id=record_id,
            task_id=str(self.task(3).pk),
            progress_pct=None,
            quantities=list(rows),
        )

    def assertRejected(self, result: dict[str, Any], reason: str) -> None:
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["code"], "validation_error")
        self.assertEqual(result["error"]["details"], {"field": "quantities", "reason": reason})

    def test_planned_item_with_refined_location(self) -> None:
        record_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 4.5, location_id=str(self.pr.pk)), record_id=record_id
            )
        )
        self.assertEqual(result["status"], "applied", result)
        record = record_of(record_id)
        self.assertEqual(record.quantities[0]["quantity"], "4.5000")
        self.assertEqual(record.quantities[0]["location_id"], str(self.pr.pk))
        self.assertIsNone(record.progress_pct)
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(r["data"] for r in rows if r["entity"] == "record")
        self.assertEqual(
            data["quantities"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "location_id": str(self.pr.pk),
                    "quantity": 4.5,
                    "note": "",
                    "client_id": None,
                    "captured_at": None,
                    "gps": None,
                    "seen": None,
                    "location_mode": None,
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

    def warnings(self, record_id: uuid.UUID) -> str:
        return str(RecordAdmin(Record, None).quantities_pretty(record_of(record_id)))

    def test_location_of_the_project_outside_the_task_is_accepted_and_flagged(self) -> None:
        record_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.other.pk)), record_id=record_id
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertIn(
            "ubicación fuera de la ubicación actual de la tarea", self.warnings(record_id)
        )

    def test_task_relocated_after_the_capture(self) -> None:
        """Se capturó en Tramo 2 > PR 1 y la oficina movió la tarea a Tramo 3: se acepta
        y se resalta; nunca se pierde."""
        task = self.task(3)
        task.location = self.other
        task.save(update_fields=["location"])
        record_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.pr.pk)), record_id=record_id
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertIn(
            "ubicación fuera de la ubicación actual de la tarea", self.warnings(record_id)
        )

    def test_task_location_cleared_after_the_capture(self) -> None:
        task = self.task(3)
        task.location = None
        task.save(update_fields=["location"])
        record_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.pr.pk)), record_id=record_id
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertIn(
            "ubicación fuera de la ubicación actual de la tarea", self.warnings(record_id)
        )
        # Un nodo de otro proyecto sigue siendo un error estructural.
        project, ctx = self._other_project()
        save_levels(ctx, project, [LevelSpec("Tramo")])
        node, _ = load_tree(ctx, project).ensure(ctx, project_levels(ctx, project), ("Tramo 2",))
        result = self.push_one(self.create(self.row(self.concrete, 1, location_id=str(node.pk))))
        self.assertRejected(result, "location_outside_task")

    def test_location_inside_the_task_is_not_flagged(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.pr.pk)), record_id=record_id
            )
        )
        self.assertNotIn("ubicación fuera", self.warnings(record_id))

    def test_unknown_location(self) -> None:
        result = self.push_one(
            self.create(self.row(self.concrete, 1, location_id=str(uuid.uuid4())))
        )
        self.assertRejected(result, "location_outside_task")

    def test_item_of_another_project(self) -> None:
        result = self.push_one(self.create({"item_id": str(uuid.uuid4()), "quantity": 1}))
        self.assertRejected(result, "item_not_in_project")

    def test_unplanned_item_and_negative_without_note_are_accepted_and_flagged(self) -> None:
        """La nota la exige la app al capturar (pieza 4b); el servidor no pierde lo
        registrado sin ella: lo acepta y la revisión lo ve resaltado."""
        unplanned, negative = uuid.uuid4(), uuid.uuid4()
        result = self.push_one(self.create(self.row(self.steel, 3), record_id=unplanned))
        self.assertEqual(result["status"], "applied", result)
        self.assertIn("no planeado y sin nota", self.warnings(unplanned))
        result = self.push_one(self.create(self.row(self.concrete, -1), record_id=negative))
        self.assertEqual(result["status"], "applied", result)
        self.assertIn("ajuste negativo sin nota", self.warnings(negative))
        noted = uuid.uuid4()
        result = self.push_one(
            self.create(self.row(self.steel, 3, note="no estaba en el plan"), record_id=noted)
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertNotIn("sin nota", self.warnings(noted))

    def test_plan_removed_between_capture_and_push(self) -> None:
        """El 2.1 estaba en el plan cuando se capturó sin nota; la oficina lo quitó antes
        de la sincronización."""
        QuantityPlan.objects.filter(task=self.task(3), item=self.concrete).delete()
        record_id = uuid.uuid4()
        result = self.push_one(self.create(self.row(self.concrete, 4), record_id=record_id))
        self.assertEqual(result["status"], "applied", result)
        self.assertIn("2.1", self.warnings(record_id))
        self.assertIn("no planeado y sin nota", self.warnings(record_id))

    def test_validated_record_quantities_cannot_change(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), record_id=record_id))
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status=str(RecordReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = record_of(record_id).version
        result = self.push_one(
            update_op(
                record_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 5)],
            )
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(
            result["error"]["details"], {"field": "quantities", "reason": "record_validated"}
        )
        self.assertEqual(record_of(record_id).quantities[0]["quantity"], "4.0000")

        # Mandar las mismas cantidades no es un cambio.
        same = update_op(
            record_id,
            version,
            task_id=str(self.task(3).pk),
            progress_pct=None,
            quantities=[self.row(self.concrete, 4)],
        )
        self.assertEqual(self.push_one(same)["status"], "applied")

    def test_validated_record_quantities_cannot_be_cleared(self) -> None:
        """Un cliente anterior manda tarea y % sin `quantities`: eso vaciaría la lista."""
        record_id = uuid.uuid4()
        op = create_op(
            self.scenario,
            record_id=record_id,
            task_id=str(self.task(3).pk),
            progress_pct=30,
            quantities=[self.row(self.concrete, 4)],
        )
        self.push_one(op)
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status=str(RecordReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = record_of(record_id).version
        result = self.push_one(
            update_op(record_id, version, task_id=str(self.task(3).pk), progress_pct=30)
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["details"]["reason"], "record_validated")

    def test_unreviewed_record_can_be_corrected(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), record_id=record_id))
        version = record_of(record_id).version
        result = self.push_one(
            update_op(
                record_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 6)],
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(record_of(record_id).quantities[0]["quantity"], "6.0000")

    def test_update_with_the_pair_alone_clears_the_quantities(self) -> None:
        record_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), record_id=record_id))
        version = record_of(record_id).version
        result = self.push_one(
            update_op(record_id, version, task_id=str(self.task(3).pk), progress_pct=25)
        )
        self.assertEqual(result["status"], "applied", result)
        record = record_of(record_id)
        self.assertEqual(record.quantities, [])
        self.assertEqual(record.progress_pct, Decimal("25.00"))

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

    def _reimport(self, rows: list[list[Any]]) -> None:
        apply_rows(self.ctx, self.concrete.budget, rows)

    def test_item_retired_in_the_latest_version_is_accepted(self) -> None:
        """V2 quita el 1.2: cuenta su tipo en la última versión aplicada que lo contiene."""
        self._reimport(V2_ROWS)
        removed = BudgetItem.objects.get(budget__project=self.project, code="1.2")
        result = self.push_one(self.create(self.row(removed, 1, note="ya no está")))
        self.assertEqual(result["status"], "applied", result)

    def test_chapter_that_became_an_item_is_accepted(self) -> None:
        self._reimport(MERGED_ROWS)
        merged = BudgetItem.objects.get(budget__project=self.project, code="2")
        result = self.push_one(self.create(self.row(merged, 1, note="global")))
        self.assertEqual(result["status"], "applied", result)

    def test_item_that_became_a_chapter_without_plan_is_rejected(self) -> None:
        QuantityPlan.objects.filter(item=self.concrete).delete()
        self._reimport(SPLIT_ROWS)
        result = self.push_one(self.create(self.row(self.concrete, 1, note="capítulo")))
        self.assertRejected(result, "item_not_in_project")

    def test_planned_item_that_became_a_chapter_is_accepted(self) -> None:
        """El 2.1 se le ofreció al campo en la tarea 1.2 antes de partirse: lo registrado
        sin conexión no se pierde. En otra tarea sin plan sigue siendo un capítulo."""
        self._reimport(SPLIT_ROWS)
        result = self.push_one(self.create(self.row(self.concrete, 2)))
        self.assertEqual(result["status"], "applied", result)
        TaskResponsible.objects.create(
            org=self.scenario.org,
            task=self.task(2),
            user=self.scenario.user,
            assigned_by=self.admin,
        )
        other = create_op(
            self.scenario,
            task_id=str(self.task(2).pk),
            progress_pct=None,
            quantities=[self.row(self.concrete, 2, note="otra tarea")],
        )
        self.assertRejected(self.push_one(other), "item_not_in_project")

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
        record_id = uuid.uuid4()
        result = self.push_one(
            self.create(
                self.row(self.concrete, 1, location_id=str(self.pr.pk)),
                self.row(self.concrete, 2, location_id=str(self.pr.pk)),
                record_id=record_id,
            )
        )
        self.assertEqual(result["status"], "applied", result)
        rows = record_of(record_id).quantities
        self.assertEqual([r["quantity"] for r in rows], ["1.0000", "2.0000"])
        self.assertEqual({r["location_id"] for r in rows}, {str(self.pr.pk)})

    def capture_row(self, quantity: object, **extra: object) -> dict[str, Any]:
        """Una fila como la envía la app 0.13 (pieza 4b), con sus datos automáticos."""
        return self.row(
            self.concrete,
            quantity,
            client_id=str(uuid.uuid4()),
            captured_at="2026-07-30T09:00:00-05:00",
            gps={"lat": 4.61, "lon": -74.08, "accuracy_m": 12.0},
            seen={"planned": 10.0, "validated": 0.0, "location_id": str(self.tramo.pk)},
            location_mode="task",
            **extra,
        )

    def test_capture_fields_round_trip(self) -> None:
        record_id = uuid.uuid4()
        row = self.capture_row(3)
        result = self.push_one(self.create(row, record_id=record_id))
        self.assertEqual(result["status"], "applied", result)
        stored = record_of(record_id).quantities[0]
        self.assertEqual(stored["client_id"], row["client_id"])
        self.assertEqual(stored["captured_at"], "2026-07-30T14:00:00.000Z")
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(r["data"] for r in rows if r["data"].get("id") == str(record_id))
        self.assertEqual(
            data["quantities"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "location_id": None,
                    "quantity": 3.0,
                    "note": "",
                    "client_id": row["client_id"],
                    "captured_at": "2026-07-30T14:00:00.000Z",
                    "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 12.0},
                    "seen": {
                        "planned": 10.0,
                        "validated": 0.0,
                        "location_id": str(self.tramo.pk),
                    },
                    "location_mode": "task",
                }
            ],
        )

    def test_validated_record_capture_fields_cannot_change(self) -> None:
        record_id = uuid.uuid4()
        row = self.capture_row(4)
        self.push_one(self.create(row, record_id=record_id))
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status=str(RecordReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = record_of(record_id).version
        moved = {**row, "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 30.0}}
        result = self.push_one(
            update_op(
                record_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[moved],
            )
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(
            result["error"]["details"], {"field": "quantities", "reason": "record_validated"}
        )
        same = update_op(
            record_id, version, task_id=str(self.task(3).pk), progress_pct=None, quantities=[row]
        )
        self.assertEqual(self.push_one(same)["status"], "applied")

    def test_duplicate_client_id_is_rejected(self) -> None:
        row = self.capture_row(1)
        result = self.push_one(self.create(row, {**row, "quantity": 2}))
        self.assertRejected(result, "duplicate_client_id")

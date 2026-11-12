"""Prueba de carga del control (spec, sección 3): ~5000 pares tarea × ítem en menos de 1 s.

500 tareas × 10 ítems = 5000 pares; 1000 registros validados de 5 filas; periodos semanales
(los más) y desglose por tarea (la tabla más larga)."""

from __future__ import annotations

import itertools
import time
import unittest
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    QuantityPlan,
    Record,
    RecordReview,
    RecordReviewStatus,
    Task,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.tests.factories import FieldWorld, make_field_world, make_record

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TASKS = 500
ITEMS_PER_TASK = 10
CHAPTERS = 5
ITEMS_PER_CHAPTER = 10
RECORDS_PER_TASK = 2
ROWS_PER_RECORD = 5
LIMIT_SECONDS = 1.0
#: Peticiones medidas tras la de calentamiento; cuenta la más rápida.
MEASUREMENTS = 3
FIRST_START = date(2025, 1, 6)


def build_large_project(world: FieldWorld) -> None:
    org, project, admin = world.org, world.project, world.admin
    budget = Budget.objects.create(
        org=org, project=project, name="Carga", code="CG", created_by=admin
    )
    version = BudgetVersion.objects.create(
        org=org,
        budget=budget,
        number=1,
        status=BudgetVersionStatus.APPLIED,
        file_name="carga.xlsx",
        source_file=b"",
        uploaded_by=admin,
        applied_at=timezone.now(),
    )
    chapters = BudgetItem.objects.bulk_create(
        [BudgetItem(org=org, budget=budget, code=str(c)) for c in range(1, CHAPTERS + 1)]
    )
    items = BudgetItem.objects.bulk_create(
        [
            BudgetItem(org=org, budget=budget, code=f"{c}.{i}")
            for c in range(1, CHAPTERS + 1)
            for i in range(1, ITEMS_PER_CHAPTER + 1)
        ]
    )
    rows = [
        BudgetItemVersion(
            org=org,
            version=version,
            item=chapter,
            kind=BudgetRowKind.CHAPTER,
            sort_order=n,
            description=f"Capítulo {chapter.code}",
            total=Decimal(0),
        )
        for n, chapter in enumerate(chapters)
    ]
    rows += [
        BudgetItemVersion(
            org=org,
            version=version,
            item=budget_item,
            kind=BudgetRowKind.ITEM,
            sort_order=100 + n,
            description=f"Ítem {budget_item.code}",
            unit="m3",
            quantity=Decimal(1000),
            unit_price=Decimal(1000 + n),
            total=Decimal(1000 * (1000 + n)),
            parent=chapters[n // ITEMS_PER_CHAPTER],
        )
        for n, budget_item in enumerate(items)
    ]
    BudgetItemVersion.objects.bulk_create(rows)
    tasks = Task.objects.bulk_create(
        [
            Task(
                org=org,
                project=project,
                uid=1000 + n,
                wbs=f"9.{n}",
                outline_number=f"9.{n}",
                outline_level=2,
                sort_order=1000 + n,
                name=f"Tarea de carga {n}",
                start=FIRST_START + timedelta(days=n),
                finish=FIRST_START + timedelta(days=n + 60),
                baseline_start=FIRST_START + timedelta(days=n),
                baseline_finish=FIRST_START + timedelta(days=n + 60),
            )
            for n in range(TASKS)
        ]
    )
    planned: dict[uuid.UUID, list[BudgetItem]] = {}
    plans = []
    for n, task in enumerate(tasks):
        own = [items[(n * 7 + k) % len(items)] for k in range(ITEMS_PER_TASK)]
        planned[task.pk] = own
        plans += [
            QuantityPlan(
                org=org,
                project=project,
                task=task,
                item=budget_item,
                planned_quantity=Decimal(100),
                updated_by=admin,
            )
            for budget_item in own
        ]
    QuantityPlan.objects.bulk_create(plans)
    # Sin tarea: solo da la plantilla y el dispositivo que reutilizan los registros de carga.
    seed = make_record(org, world.agent)
    sequence = itertools.count(10_000_000)
    records = [
        Record(
            id=uuid.uuid4(),
            org=org,
            change_seq=next(sequence),
            template_version_id=seed.template_version_id,
            answers={},
            captured_at=datetime.combine(
                FIRST_START + timedelta(days=n + 10 + 20 * r), day_time(15), tzinfo=UTC
            ),
            device_id=seed.device_id,
            created_by=world.agent,
            task=task,
            quantities=[
                {
                    "item_id": str(budget_item.pk),
                    "location_id": None,
                    "quantity": "30.0000",
                    "note": "",
                }
                for budget_item in planned[task.pk][:ROWS_PER_RECORD]
            ],
        )
        for n, task in enumerate(tasks)
        for r in range(RECORDS_PER_TASK)
    ]
    Record.objects.bulk_create(records)
    RecordReview.objects.bulk_create(
        [
            RecordReview(
                org=org,
                record=record,
                status=RecordReviewStatus.APPROVED,
                reviewer=world.supervisor,
            )
            for record in records
        ]
    )


@skip_without_db
class ControlLoadTests(TestCase):
    world: FieldWorld

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga del control")
        build_large_project(cls.world)

    def test_page_with_5000_pairs_renders_under_a_second(self) -> None:
        self.assertEqual(
            QuantityPlan.objects.filter(project=self.world.project).count(),
            TASKS * ITEMS_PER_TASK,
        )
        self.client.force_login(self.world.admin)
        url = (
            f"/plataforma/proyectos/{self.world.project.pk}/control/"
            "?corte=2026-01-30&periodo=semana&desglose=tarea"
        )
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        # La mejor de varias mediciones: un CI lento o con vecinos ruidosos no debe fallar por
        # una pausa ajena a la página; el objetivo de 1 s sigue siendo el mismo.
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Tarea de carga 499")
        best = min(timings)
        self.assertLess(best, LIMIT_SECONDS, f"la página tardó {best:.3f} s (mejor de {timings})")

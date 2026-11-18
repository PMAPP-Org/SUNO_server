"""Prueba de carga de la pestaña "Materiales" (spec, 4 "Carga"): 2.000 materiales, 5 bodegas y
50.000 movimientos (10.000 entradas de compra y 40.000 salidas). Inventario, kárdex y propuesta
del corte en menos de 1 s (2 s en CI)."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from suno.control.tests.test_performance import LIMIT_SECONDS, MEASUREMENTS
from suno.core.access import SectionRole
from suno.core.models import (
    ConsumptionCut,
    Material,
    MaterialIssue,
    MaterialIssueLine,
    MaterialPurchase,
    MaterialPurchaseLine,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs, support_blob
from suno.projects.tests.factories import FieldWorld, make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MATERIALS = 2000
WAREHOUSES = 5
ISSUES = 8000
LINES_PER_ISSUE = 5
#: Las salidas usan los primeros 200 materiales: así un par tiene unas 40 salidas en su kárdex.
ISSUED_MATERIALS = 200
FIRST_DAY = date(2025, 1, 6)
CUT_END = date(2026, 2, 28)
TODAY = date(2026, 4, 15)
PURCHASED = D("1000")


def load(world: FieldWorld) -> Warehouse:
    org, project, admin = world.org, world.project, world.admin
    now = timezone.now()
    blob = support_blob("carga de materiales")
    materials = Material.objects.bulk_create(
        [Material(org=org, code=f"MAT-{n:04d}", name=f"Material de carga {n}", unit="und",
                  category=f"Categoría {n % 20}") for n in range(MATERIALS)]
    )  # fmt: skip
    warehouses = Warehouse.objects.bulk_create(
        [Warehouse(org=org, project=project, name=f"Bodega {w}", created_by=admin,
                   updated_by=admin) for w in range(WAREHOUSES)]
    )  # fmt: skip
    price = {m.pk: D(1000 + n) for n, m in enumerate(materials)}
    purchases: list[MaterialPurchase] = []
    lines: list[MaterialPurchaseLine] = []
    movements: list[StockMovement] = []
    sequence: dict[tuple[object, object], int] = defaultdict(int)
    quantity: dict[tuple[object, object], Decimal] = defaultdict(Decimal)
    number = 0
    for warehouse in warehouses:
        for chunk in range(0, MATERIALS, 200):
            number += 1
            purchase = MaterialPurchase(
                org=org, project=project, number=number, kind="warehouse", warehouse=warehouse,
                supplier="Proveedor de carga", document_type="invoice", purchased_on=FIRST_DAY,
                status="approved", created_by=admin, updated_by=admin,
                decided_by=world.supervisor, decided_at=now, attachment=blob,
            )  # fmt: skip
            purchases.append(purchase)
            for order, material in enumerate(materials[chunk : chunk + 200], start=1):
                amount = PURCHASED * price[material.pk]
                lines.append(MaterialPurchaseLine(
                    org=org, purchase=purchase, order=order, material=material,
                    quantity=PURCHASED, unit_price=price[material.pk], amount=amount, scope="",
                ))  # fmt: skip
                pair = (warehouse.pk, material.pk)
                sequence[pair] += 1
                quantity[pair] += PURCHASED
                movements.append(StockMovement(
                    org=org, project=project, warehouse=warehouse, material=material,
                    moved_on=FIRST_DAY, kind="purchase", quantity=PURCHASED,
                    unit_cost=price[material.pk], value=amount, sequence=sequence[pair],
                    created_by=admin, purchase=purchase,
                ))  # fmt: skip
    MaterialPurchase.objects.bulk_create(purchases)
    MaterialPurchaseLine.objects.bulk_create(lines, batch_size=5000)
    cut = ConsumptionCut.objects.create(
        org=org, project=project, number=1, period_start=FIRST_DAY, period_end=CUT_END,
        status="approved", created_by=admin, updated_by=admin, decided_by=world.supervisor,
        decided_at=now, attachment=blob,
    )  # fmt: skip
    issues: list[MaterialIssue] = []
    issue_lines: list[MaterialIssueLine] = []
    for n in range(ISSUES):
        warehouse = warehouses[n % WAREHOUSES]
        day = FIRST_DAY + timedelta(days=n * 449 // ISSUES)
        issue = MaterialIssue(
            org=org, project=project, number=n + 1, warehouse=warehouse, issued_on=day,
            scope="indirect", created_by=admin, updated_by=admin,
            cut=cut if day <= CUT_END else None,
        )  # fmt: skip
        issues.append(issue)
        for k in range(LINES_PER_ISSUE):
            material = materials[(n * LINES_PER_ISSUE + k) % ISSUED_MATERIALS]
            pair = (warehouse.pk, material.pk)
            unit = price[material.pk]
            issue_lines.append(MaterialIssueLine(
                org=org, issue=issue, order=k + 1, material=material, quantity=D(1),
                unit_cost=unit, amount=unit,
            ))  # fmt: skip
            sequence[pair] += 1
            quantity[pair] -= 1
            movements.append(StockMovement(
                org=org, project=project, warehouse=warehouse, material=material, moved_on=day,
                kind="issue", quantity=D(-1), unit_cost=unit, value=-unit,
                sequence=sequence[pair], created_by=admin, issue=issue,
            ))  # fmt: skip
    MaterialIssue.objects.bulk_create(issues, batch_size=5000)
    MaterialIssueLine.objects.bulk_create(issue_lines, batch_size=5000)
    StockMovement.objects.bulk_create(movements, batch_size=5000)
    StockBalance.objects.bulk_create(
        [
            StockBalance(org=org, project=project, warehouse_id=w, material_id=m,
                         quantity=quantity[(w, m)], value=quantity[(w, m)] * price[m],
                         last_sequence=sequence[(w, m)])
            for w, m in sequence
        ],
        batch_size=5000,
    )  # fmt: skip
    # Sin estadísticas (datos recién insertados en la transacción de la prueba) el planificador
    # puede elegir un nested loop; en producción las pone el autovacuum (como en 6b).
    with connection.cursor() as cursor:
        for table in ("material", "warehouse", "material_issue", "material_issue_line",
                      "stock_movement", "stock_balance", "material_return"):  # fmt: skip
            cursor.execute(f"ANALYZE {table}")
    return warehouses[0]


@skip_without_db
class MaterialsLoadTests(TestCase):
    world: FieldWorld
    warehouse: Warehouse

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga de materiales")
        grant_costs(cls.world, cls.world.supervisor, SectionRole.REVIEWER)
        cls.warehouse = load(cls.world)

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def best_of(self, url: str, expected: str) -> float:
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)
        return min(timings)

    def test_inventory_kardex_and_cut_proposal_render_under_the_limit(self) -> None:
        project = self.world.project
        self.assertEqual(StockMovement.objects.filter(project=project).count(), 50_000)
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{project.pk}/materiales/"
        material = Material.objects.get(org=self.world.org, code="MAT-0000")
        for url, expected in (
            (base, "MAT-0000"),
            (f"{base}kardex/{self.warehouse.pk}/{material.pk}/", "SA-"),
            (f"{base}cortes/?hasta=2026-03-31", "Indirecto (proyecto)"),
        ):
            with self.subTest(url=url):
                best = self.best_of(url, expected)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")

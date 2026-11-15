"""Restricciones de la base de materiales (spec, 1) y del libro (spec, 1.4)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    ConsumptionCut,
    CostDocumentType,
    CostEntry,
    CostStatus,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    MaterialPurchase,
    MaterialTransfer,
    StockBalance,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import entry, support_blob

from .factories import (
    bare_cut,
    bare_issue,
    draft_purchase,
    make_material,
    make_warehouse,
    material_world,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class CatalogAndStockTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_codes_and_warehouse_names_are_unique_ignoring_case(self) -> None:
        self.broken(lambda: make_material(self.world.org, "cem-01", "Otro", "und"))
        self.broken(lambda: make_warehouse(self.world, "almacén PRINCIPAL"))
        other = material_world("Otra constructora")
        self.assertEqual(other.cement.code, "CEM-01")  # otra organización, mismo código

    def balance(self, **fields: Any) -> StockBalance:
        values: dict[str, Any] = {
            "org": self.world.org,
            "project": self.mw.project,
            "warehouse": self.mw.warehouse,
            "material": self.mw.cement,
        }
        values.update(fields)
        return StockBalance.objects.create(**values)

    def test_a_balance_is_never_negative_nor_has_value_without_quantity(self) -> None:
        for fields in ({"quantity": D("-1")}, {"value": D("-0.01")},
                       {"quantity": D("0"), "value": D("1.00")}):  # fmt: skip
            with self.subTest(fields=fields):
                self.broken(lambda fields=fields: self.balance(**fields))
        self.assertEqual(self.balance(quantity=D("2"), value=D("0.00")).value, D("0.00"))
        self.broken(lambda: self.balance())  # el par (bodega, material) es único

    def movement(self, **fields: Any) -> StockMovement:
        values: dict[str, Any] = {
            "org": self.world.org,
            "project": self.mw.project,
            "warehouse": self.mw.warehouse,
            "material": self.mw.cement,
            "moved_on": date(2026, 3, 2),
            "kind": "purchase",
            "quantity": D("10"),
            "unit_cost": D("100"),
            "value": D("1000.00"),
            "sequence": 1,
            "created_by": self.world.admin,
        }
        values.update(fields)
        return StockMovement.objects.create(**values)

    def test_a_movement_has_the_sign_and_the_origin_of_its_kind(self) -> None:
        purchase = draft_purchase(self.mw)
        issue = bare_issue(self.mw)
        self.movement(purchase=purchase)
        self.broken(lambda: self.movement(purchase=purchase))  # secuencia repetida
        self.broken(lambda: self.movement(sequence=2))  # sin documento de origen
        self.broken(lambda: self.movement(sequence=2, purchase=purchase, issue=issue))
        self.broken(lambda: self.movement(sequence=2, kind="issue", purchase=purchase))
        self.broken(lambda: self.movement(sequence=2, kind="issue", issue=issue))  # entra
        self.movement(sequence=2, kind="issue", issue=issue, quantity=D("-3"),
                      value=D("-300.00"))  # fmt: skip
        self.broken(lambda: self.movement(sequence=3, purchase=purchase, quantity=D("-1"),
                                          value=D("-100.00")))  # fmt: skip


@skip_without_db
class DocumentConstraintTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_a_purchase_has_a_warehouse_only_when_it_goes_to_one(self) -> None:
        self.broken(lambda: draft_purchase(self.mw, warehouse=None))
        self.broken(lambda: draft_purchase(self.mw, kind="direct"))
        draft_purchase(self.mw, kind="direct", warehouse=None)
        self.broken(lambda: draft_purchase(self.mw, number=1, kind="direct", warehouse=None))

    def test_a_purchase_is_approved_by_someone_else_and_with_support(self) -> None:
        purchase = draft_purchase(self.mw)
        rows = MaterialPurchase.objects.filter(pk=purchase.pk)
        now = timezone.now()
        self.broken(lambda: rows.update(status="approved", decided_by=self.world.supervisor,
                                        decided_at=now))  # sin soporte  # fmt: skip
        self.broken(lambda: rows.update(status="approved", decided_by=self.world.admin,
                                        decided_at=now, attachment=support_blob()))  # fmt: skip
        self.broken(lambda: rows.update(status="rejected", decided_by=self.world.supervisor,
                                        decided_at=now))  # sin motivo  # fmt: skip
        rows.update(status="approved", decided_by=self.world.supervisor, decided_at=now,
                    attachment=support_blob())  # fmt: skip
        self.broken(lambda: rows.update(status="voided"))  # sin quién ni motivo

    def test_only_one_open_cut_per_project_and_its_period_in_order(self) -> None:
        bare_cut(self.mw, number=1, status="approved")
        bare_cut(self.mw, number=2, start=date(2026, 4, 1), end=date(2026, 4, 10))
        self.broken(lambda: bare_cut(self.mw, number=3, status="rejected"))
        self.broken(lambda: bare_cut(self.mw, number=2, status="approved"))
        ConsumptionCut.objects.filter(number=2).delete()
        self.broken(lambda: bare_cut(self.mw, number=2, start=date(2026, 4, 2),
                                     end=date(2026, 4, 1)))  # fmt: skip

    def test_a_transfer_goes_between_two_warehouses(self) -> None:
        def transfer(target: Any) -> MaterialTransfer:
            return MaterialTransfer.objects.create(
                org=self.world.org, project=self.mw.project, number=1, source=self.mw.warehouse,
                target=target, moved_on=date(2026, 3, 5), created_by=self.world.admin,
                updated_by=self.world.admin,
            )  # fmt: skip

        self.broken(lambda: transfer(self.mw.warehouse))
        transfer(make_warehouse(self.world, "Frente norte"))

    def test_an_adjustment_line_keeps_its_difference(self) -> None:
        adjustment = InventoryAdjustment.objects.create(
            org=self.world.org, project=self.mw.project, number=1, warehouse=self.mw.warehouse,
            counted_on=date(2026, 3, 31), scope="indirect", created_by=self.world.admin,
            updated_by=self.world.admin,
        )  # fmt: skip

        def line(**fields: Any) -> InventoryAdjustmentLine:
            values: dict[str, Any] = {
                "org": self.world.org, "adjustment": adjustment, "order": 1,
                "material": self.mw.cement, "expected": D("10"), "counted": D("8"),
                "difference": D("-2"),
            }  # fmt: skip
            values.update(fields)
            return InventoryAdjustmentLine.objects.create(**values)

        self.broken(lambda: line(difference=D("2")))
        self.broken(lambda: line(given_unit_cost=D("0")))
        line()
        self.broken(lambda: line(order=2))  # un material por ajuste


@skip_without_db
class LedgerOriginTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def test_only_a_cut_writes_a_negative_cost(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        rows = CostEntry.objects.filter(pk=cost.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            rows.update(amount=D("-5.00"))
        cut = bare_cut(self.mw, status="approved")
        rows.update(amount=D("-5.00"), consumption_cut=cut)
        with self.assertRaises(IntegrityError), transaction.atomic():
            rows.update(amount=D("0.00"))
        self.assertTrue(CostEntry.objects.get(pk=cost.pk).from_document)

    def test_a_cost_has_at_most_one_origin(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        self.assertFalse(cost.from_document)
        purchase = draft_purchase(self.mw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=cost.pk).update(
                material_purchase=purchase, consumption_cut=bare_cut(self.mw)
            )
        CostEntry.objects.filter(pk=cost.pk).update(material_purchase=purchase)
        self.assertIn(CostDocumentType.CONSUMPTION_CUT, CostDocumentType.values)
        self.assertIn(CostDocumentType.INVENTORY_COUNT, CostDocumentType.values)

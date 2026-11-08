"""Restricciones de BD del modelo de presupuestos."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BudgetModelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.user = make_user(self.org, username="admin", role=Role.ADMIN)
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.user)
        self.budget = Budget.objects.create(
            org=self.org, project=self.project, name="Contrato", created_by=self.user
        )

    def _version(self, number: int | None) -> BudgetVersion:
        return BudgetVersion.objects.create(
            org=self.org,
            budget=self.budget,
            number=number,
            status=BudgetVersionStatus.APPLIED if number else BudgetVersionStatus.PENDING,
            file_name="p.xlsx",
            source_file=b"",
            uploaded_by=self.user,
        )

    def test_budget_name_is_unique_per_project_ignoring_case(self) -> None:
        other = Project.objects.create(org=self.org, name="Otra", created_by=self.user)
        Budget.objects.create(org=self.org, project=other, name="Contrato", created_by=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Budget.objects.create(
                org=self.org, project=self.project, name="CONTRATO", created_by=self.user
            )

    def test_version_number_is_unique_per_budget_but_pending_has_none(self) -> None:
        self._version(None)
        self._version(None)
        self._version(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._version(1)

    def test_number_is_set_if_and_only_if_applied(self) -> None:
        def make(status: str, number: int | None) -> BudgetVersion:
            return BudgetVersion.objects.create(
                org=self.org,
                budget=self.budget,
                number=number,
                status=status,
                file_name="p.xlsx",
                source_file=b"",
                uploaded_by=self.user,
            )

        for status, number in (
            (BudgetVersionStatus.APPLIED, None),
            (BudgetVersionStatus.PENDING, 5),
            (BudgetVersionStatus.DISCARDED, 6),
        ):
            with self.subTest(status=str(status), number=number):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    make(str(status), number)
        make(str(BudgetVersionStatus.DISCARDED), None)

    def test_item_code_is_unique_per_budget(self) -> None:
        BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")

    def test_row_values_follow_their_kind(self) -> None:
        version = self._version(1)
        item = BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")

        def row(kind: Any, quantity: str | None, price: str | None) -> BudgetItemVersion:
            return BudgetItemVersion.objects.create(
                org=self.org,
                version=version,
                item=item,
                kind=kind,
                sort_order=0,
                description="Excavación",
                quantity=None if quantity is None else Decimal(quantity),
                unit_price=None if price is None else Decimal(price),
                total=Decimal("0"),
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.ITEM, None, "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.CHAPTER, "1", None)
        row(BudgetRowKind.ITEM, "2", "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.ITEM, "3", "10")  # (versión, ítem) único

    def test_charge_pct_is_not_negative_and_order_is_unique(self) -> None:
        version = self._version(1)

        def charge(order: int, pct: str) -> BudgetCharge:
            return BudgetCharge.objects.create(
                org=self.org,
                version=version,
                order=order,
                name="A",
                pct=Decimal(pct),
                base="direct",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            charge(1, "-1")
        charge(1, "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            charge(1, "5")


class RowKindValuesTests(unittest.TestCase):
    def test_reader_and_model_use_the_same_values(self) -> None:
        from suno.budgets.excel import KIND_CHAPTER, KIND_ITEM

        self.assertEqual((KIND_CHAPTER, KIND_ITEM), (BudgetRowKind.CHAPTER, BudgetRowKind.ITEM))

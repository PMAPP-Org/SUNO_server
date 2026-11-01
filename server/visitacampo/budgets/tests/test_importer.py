"""Importación de versiones de presupuesto: subida, vista previa, aplicación."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.http import Http404
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets import importer
from visitacampo.budgets.importer import (
    ImportRejected,
    apply_version,
    charges_of,
    create_upload,
    discard_version,
    mark_contractual,
    prepare_version,
    update_pending,
)
from visitacampo.budgets.mapping import ColumnMapping
from visitacampo.budgets.totals import ChargeSpec, default_charges
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import apply_rows, make_budget, stage_rows
from .samples import AIU, MAPPING, V1_ROWS, V2_ROWS, V3_ROWS, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class ImporterTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        self.budget = make_budget(self.project, self.admin)


@skip_without_db
class UploadAndPrepareTests(ImporterTestCase):
    def test_upload_keeps_the_file_as_a_pending_version_without_summary(self) -> None:
        data = xlsx(V1_ROWS)
        version = create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=data)
        self.assertEqual(version.status, BudgetVersionStatus.PENDING)
        self.assertIsNone(version.number)
        self.assertEqual(version.summary, {})
        self.assertEqual(bytes(BudgetVersion.objects.get(pk=version.pk).source_file), data)

    def test_upload_rejects_bad_files(self) -> None:
        for name, data, words in (
            ("v1.xlsm", xlsx(V1_ROWS), "macros"),
            ("v1.xlsx", b"hola", "no es un archivo .xlsx"),
        ):
            with self.subTest(name=name), self.assertRaises(ImportRejected) as caught:
                create_upload(self.ctx, self.budget, file_name=name, data=data)
            self.assertIn(words, str(caught.exception))
        with mock.patch.object(importer, "MAX_BYTES", 10), self.assertRaises(ImportRejected):
            create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))
        self.assertFalse(BudgetVersion.objects.exists())

    def test_upload_to_a_budget_of_another_organization_is_404(self) -> None:
        other = make_org("Otra")
        outsider = make_user(other, username="otro", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=outsider.pk, org_id=other.pk))
        with self.assertRaises(Http404):
            create_upload(ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))

    def test_prepare_the_first_version(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        summary = version.summary
        self.assertEqual(
            (summary["added"], summary["item_count"], summary["chapter_count"]), (5, 5, 2)
        )
        self.assertIsNone(summary["base_number"])
        self.assertIsNone(summary["previous_total"])
        self.assertEqual(summary["mapping"], MAPPING.to_json())
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.column_mapping, MAPPING.to_json())
        self.assertEqual(charges_of(self.ctx, version), default_charges())
        self.assertFalse(BudgetItemVersion.objects.exists())  # nada aplicado aún

    def test_a_mapping_that_does_not_read_is_not_saved(self) -> None:
        version = create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))
        wrong = ColumnMapping(sheet="Hoja1", first_row=2, columns=MAPPING.columns)
        with self.assertRaises(ImportRejected) as caught:
            prepare_version(self.ctx, version.pk, wrong)
        self.assertIn("no tiene la hoja «Hoja1»", str(caught.exception))
        self.budget.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual((self.budget.column_mapping, version.summary), ({}, {}))

    def test_update_pending_saves_label_and_charges(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        update_pending(self.ctx, version.pk, label="  Otrosí 1 ", charges=AIU)
        version.refresh_from_db()
        self.assertEqual(version.label, "Otrosí 1")
        self.assertEqual(charges_of(self.ctx, version), AIU)

    def test_invalid_charges_change_nothing(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        with self.assertRaises(ImportRejected):
            update_pending(
                self.ctx,
                version.pk,
                label="X",
                charges=[ChargeSpec(1, "A", D("1"), "line:1")],
            )
        version.refresh_from_db()
        self.assertEqual(version.label, "")
        self.assertEqual(charges_of(self.ctx, version), default_charges())

    def test_preparing_again_keeps_the_edited_charges(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        update_pending(self.ctx, version.pk, label="", charges=AIU[:2])
        prepare_version(self.ctx, version.pk, MAPPING)
        self.assertEqual(charges_of(self.ctx, version), AIU[:2])

    def test_discard(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        discard_version(self.ctx, version.pk)
        version.refresh_from_db()
        self.assertEqual(version.status, BudgetVersionStatus.DISCARDED)
        with self.assertRaises(ImportRejected):
            update_pending(self.ctx, version.pk, label="", charges=[])


@skip_without_db
class ApplyVersionTests(ImporterTestCase):
    def test_first_version_becomes_contractual_with_items_totals_and_charges(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS, label="Contrato", charges=AIU)
        self.assertEqual((v1.number, v1.status, v1.label), (1, "applied", "Contrato"))
        self.assertIsNotNone(v1.applied_at)
        self.assertEqual((v1.direct_cost, v1.total), (D("9130000.00"), D("11042735.00")))
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v1.pk)
        rows = {
            r.item.code: r
            for r in BudgetItemVersion.objects.filter(version=v1).select_related("item", "parent")
        }
        self.assertEqual(len(rows), 7)
        self.assertEqual((rows["1"].total, rows["2"].total), (D("1150000.00"), D("7980000.00")))
        self.assertEqual(rows["1.1"].parent.code, "1")
        self.assertIsNone(rows["1"].parent)
        self.assertEqual(rows["2.2"].quantity, D("800"))
        amounts = list(
            BudgetCharge.objects.filter(version=v1)
            .order_by("order")
            .values_list("amount", flat=True)
        )
        self.assertEqual(amounts, [D("913000"), D("456500"), D("456500"), D("86735")])

    def test_second_version_keeps_the_contractual_and_copies_the_charges(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        staged = stage_rows(self.ctx, self.budget, V2_ROWS)
        summary = staged.summary
        self.assertEqual(
            [
                summary[key]
                for key in (
                    "added",
                    "removed",
                    "more_quantity",
                    "less_quantity",
                    "price_changed",
                )
            ],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual((summary["base_number"], summary["previous_total"]), (1, "11042735.00"))
        self.assertEqual(charges_of(self.ctx, staged), AIU)
        v2 = apply_version(self.ctx, staged.pk)
        self.assertEqual(
            (v2.number, v2.direct_cost, v2.total), (2, D("8690000.00"), D("10510555.00"))
        )
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v1.pk)
        self.assertEqual(BudgetItem.objects.filter(budget=self.budget).count(), 8)
        self.assertFalse(BudgetItemVersion.objects.filter(version=v2, item__code="1.2").exists())

    def test_a_removed_item_comes_back_with_the_same_identity(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        item = BudgetItem.objects.get(budget=self.budget, code="1.2")
        staged = stage_rows(self.ctx, self.budget, V3_ROWS)
        self.assertEqual((staged.summary["added"], staged.summary["reappeared"]), (0, 1))
        v3 = apply_version(self.ctx, staged.pk)
        self.assertTrue(BudgetItemVersion.objects.filter(version=v3, item=item).exists())

    def test_cannot_apply_twice_nor_without_mapping(self) -> None:
        staged = stage_rows(self.ctx, self.budget, V1_ROWS)
        apply_version(self.ctx, staged.pk)
        with self.assertRaises(ImportRejected):
            apply_version(self.ctx, staged.pk)
        raw = create_upload(self.ctx, self.budget, file_name="v2.xlsx", data=xlsx(V2_ROWS))
        with self.assertRaises(ImportRejected) as caught:
            apply_version(self.ctx, raw.pk)
        self.assertIn("mapeo", str(caught.exception))

    def test_stale_preview_is_rejected(self) -> None:
        old = stage_rows(self.ctx, self.budget, V1_ROWS)
        newer = stage_rows(self.ctx, self.budget, V2_ROWS)
        apply_version(self.ctx, newer.pk)
        with self.assertRaises(ImportRejected) as caught:
            apply_version(self.ctx, old.pk)
        self.assertIn("Vuelve a subir", str(caught.exception))
        old.refresh_from_db()
        self.assertEqual(old.status, BudgetVersionStatus.PENDING)

    def test_mark_contractual(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        v2 = apply_rows(self.ctx, self.budget, V2_ROWS)
        previous, version = mark_contractual(self.ctx, self.budget.pk, v2.pk)
        self.assertEqual((previous, version.pk), (1, v2.pk))
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v2.pk)
        pending = stage_rows(self.ctx, self.budget, V3_ROWS)
        with self.assertRaises(ImportRejected):
            mark_contractual(self.ctx, self.budget.pk, pending.pk)
        other = make_budget(self.project, self.admin, name="Interventoría")
        foreign = apply_rows(self.ctx, other, V1_ROWS)
        with self.assertRaises(ImportRejected):
            mark_contractual(self.ctx, self.budget.pk, foreign.pk)


@skip_without_db
class ConcurrentImportTests(TransactionTestCase):
    """Otra transacción tiene bloqueado el presupuesto: se rechaza sin esperar."""

    def test_apply_is_rejected_while_the_budget_is_locked(self) -> None:
        org = make_org("Constructora")
        admin = make_user(org, username="admin", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        project = Project.objects.create(org=org, name="Obra", created_by=admin)
        budget = make_budget(project, admin)
        staged = stage_rows(ctx, budget, V1_ROWS)

        other = connections.create_connection("default")
        other.set_autocommit(False)
        try:
            with other.cursor() as cursor:
                table = connection.ops.quote_name(Budget._meta.db_table)
                cursor.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", [budget.pk])
            with self.assertRaises(ImportRejected) as caught:
                apply_version(ctx, staged.pk)
            self.assertIn("en curso", str(caught.exception))
        finally:
            other.rollback()
            other.close()
        staged.refresh_from_db()
        self.assertEqual(staged.status, BudgetVersionStatus.PENDING)

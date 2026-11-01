"""Importación de versiones de presupuesto: subida, vista previa, aplicación."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.http import Http404
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets import importer
from visitacampo.budgets.importer import (
    ImportRejected,
    charges_of,
    create_upload,
    discard_version,
    prepare_version,
    update_pending,
)
from visitacampo.budgets.mapping import ColumnMapping
from visitacampo.budgets.totals import ChargeSpec, default_charges
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
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

from .factories import make_budget, stage_rows
from .samples import AIU, MAPPING, V1_ROWS, xlsx

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

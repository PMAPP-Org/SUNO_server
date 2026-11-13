"""Importación del libro de costos (spec, 2.6): plantilla, errores por fila, todo o nada."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import AIU, V1_ROWS
from suno.budgets.tests.xlsx import build_xlsx
from suno.core.access import SectionRole
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent, CostEntry, CostStatus, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.importer import HEADER, SHEET, apply, preview, template_bytes
from suno.costs.services import CostRejected

from .factories import cost_world, grant_costs

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
DAY = date(2026, 3, 10)


def row(**values: Any) -> list[Any]:
    base: dict[str, Any] = {
        "date": DAY,
        "kind": "Materiales",
        "code": "2.1",
        "task": "",
        "description": "Concreto premezclado",
        "amount": 1500000,
        "third": "Concretos SA",
        "doc": "Factura",
        "number": "F-101",
    }
    base.update(values)
    return list(base.values())


@skip_without_db
class CostImportTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.project = self.world.project
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.ctx = self.world.ctx(self.contributor)

    def book(self, *rows: list[Any], header: list[str] = HEADER) -> bytes:
        return build_xlsx(rows, header=header, sheet=SHEET)

    def test_template_lists_kinds_codes_and_tasks(self) -> None:
        data = template_bytes(self.world.ctx(), self.project)
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            workbook = book.read("xl/workbook.xml").decode()
            first = book.read("xl/worksheets/sheet1.xml").decode()
            codes = book.read("xl/worksheets/sheet4.xml").decode()
            tasks = book.read("xl/worksheets/sheet5.xml").decode()
        for name in ("Costos", "Tipos", "Documentos", "Códigos", "Tareas"):
            self.assertIn(f'name="{name}"', workbook)
        self.assertIn(">Código de ítem o capítulo<", first)
        self.assertIn(">2.1<", codes)
        self.assertIn(">Concreto zapatas<", codes)
        self.assertIn(">1.2<", tasks)

    def test_preview_reads_items_chapters_indirect_and_tasks(self) -> None:
        data = self.book(
            row(task="2.1"),
            row(
                date=date(2026, 3, 11),
                kind="mano de obra",
                code="2",
                description="Cuadrilla de estructura",
                amount="2.400.000",
                third="",
                doc="cuenta de cobro",
                number="",
            ),
            row(date="2026-03-12", kind="Nómina", code="", description="Residente", doc="Nómina"),
        )
        result = preview(self.ctx, self.project, data)
        self.assertEqual(result.errors, ())
        self.assertEqual([r.data.scope for r in result.rows], ["item", "chapter", "indirect"])
        self.assertEqual(result.rows[0].task, "2.1 Columnas")
        self.assertEqual(result.rows[0].assignment, "2.1 Concreto zapatas")
        self.assertEqual(result.rows[1].assignment, "Capítulo 2 Estructura")
        self.assertEqual(result.rows[1].data.amount, D("2400000"))
        self.assertEqual(result.total, D("5400000"))
        self.assertTrue(result.token)
        self.assertFalse(CostEntry.objects.exists())

    def test_errors_by_row(self) -> None:
        data = self.book(
            row(date="10-03-2026"),
            row(kind="Gasolina"),
            row(code="9.9"),
            row(amount="cien"),
            row(code="", task="2.1"),
            row(task="7.7"),
            row(),
        )
        result = preview(self.ctx, self.project, data)
        self.assertEqual(result.token, "")
        self.assertEqual(
            [error.split(":")[0] for error in result.errors],
            ["Fila 2", "Fila 3", "Fila 4", "Fila 5", "Fila 6", "Fila 7"],
        )

    def test_code_in_two_budgets_and_missing_columns(self) -> None:
        from suno.budgets.tests.factories import make_budget

        other = make_budget(self.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.world.ctx(), other, V1_ROWS, charges=AIU)
        twice = preview(self.ctx, self.project, self.book(row()))
        self.assertIn("más de un presupuesto", twice.errors[0])
        missing = preview(self.ctx, self.project, self.book(row()[:5], header=HEADER[:5]))
        self.assertIn("Falta la columna «Valor».", missing.errors)

    def test_apply_creates_every_row_in_draft(self) -> None:
        result = preview(self.ctx, self.project, self.book(row(), row(code="2", amount=10)))
        self.assertEqual(apply(self.ctx, self.project, result.token), 2)
        drafts = CostEntry.objects.filter(
            project=self.project, status=CostStatus.DRAFT, created_by=self.contributor
        )
        self.assertEqual(drafts.count(), 2)
        self.assertFalse(drafts.filter(attachment__isnull=False).exists())
        event = AuditEvent.objects.get(action=AuditAction.COST_IMPORTED)
        self.assertEqual(event.metadata["rows"], 2)

    def test_tampered_foreign_or_stale_previews_are_refused(self) -> None:
        token = preview(self.ctx, self.project, self.book(row(code="1.2"))).token
        with self.assertRaises(CostRejected):
            apply(self.ctx, self.project, token + "x")
        with self.assertRaises(CostRejected):  # la vista previa es de otro usuario
            apply(self.world.ctx(), self.project, token)
        from suno.budgets.tests.samples import V2_ROWS

        apply_rows(self.world.ctx(), self.budget, V2_ROWS, charges=AIU)  # quita el 1.2
        with self.assertRaises(CostRejected):
            apply(self.ctx, self.project, token)
        self.assertFalse(CostEntry.objects.exists())

    def test_only_roles_that_import(self) -> None:
        with self.assertRaises(PermissionDenied):
            preview(self.world.ctx(self.world.supervisor), self.project, self.book(row()))

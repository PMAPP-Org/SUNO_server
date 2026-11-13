"""Lista del libro de costos (spec, 2.1 y 2.7): filtros, automáticos, totales, Excel."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.core.models import CostEntry, CostKind, CostScope, CostStatus, EquipmentOwnership
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import ledger
from suno.costs.catalog import load_catalog
from suno.costs.export import ledger_workbook
from suno.costs.ledger import LedgerFilters, LedgerPage, LedgerTooLarge, export_lines, ledger_page

from .factories import cost_world, entry, equipment, item, trip, usage

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class LedgerTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")
        self.structure = item(self.budget, "2")
        self.approved = entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=self.world.task(5),
        )
        entry(self.world, day=date(2026, 3, 12), amount="500.00", description="Papelería")
        entry(
            self.world,
            day=date(2026, 3, 11),
            status=CostStatus.REJECTED,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        entry(
            self.world,
            day=date(2026, 3, 9),
            amount="700.00",
            status=CostStatus.APPROVED,
            kind=CostKind.LABOR,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        machine = equipment(self.world)
        usage(self.world, machine, date(2026, 3, 10), "800.00", budget_item=self.concrete)
        usage(self.world, machine, date(2026, 3, 10), "200.00")
        usage(self.world, machine, date(2026, 3, 11), None)  # sin tarifa: no suma
        rented = equipment(self.world, "AL-01", ownership=EquipmentOwnership.RENTED)
        usage(self.world, rented, date(2026, 3, 10), "999.00")  # alquilado: va al libro
        trip(self.world, date(2026, 3, 8), "1400.00")
        trip(self.world, date(2026, 3, 8), "1400.00", own=machine)  # ya está en sus horas
        self.catalog = load_catalog(self.ctx, self.world.project)

    def page(self, number: int = 1, **query: Any) -> LedgerPage:
        return ledger_page(
            self.ctx,
            self.world.project,
            LedgerFilters.from_query(query),
            page=number,
            catalog=self.catalog,
        )

    def test_combined_list_by_date(self) -> None:
        page = self.page()
        self.assertEqual(page.count, 7)
        self.assertEqual(
            [(line.day.day, line.origin) for line in page.lines],
            [
                (12, "libro"),
                (11, "libro"),
                (10, "libro"),
                (10, "equipo"),
                (10, "equipo"),
                (9, "libro"),
                (8, "transporte"),
            ],
        )
        assignments = [line.assignment for line in page.lines]
        self.assertEqual(assignments[0], "Indirecto (proyecto)")  # Papelería, del 12
        self.assertIn("2.1 Concreto zapatas · tarea 2.1 Columnas", assignments)
        self.assertIn("Capítulo 2 Estructura", assignments)
        equipment_lines = [line for line in page.lines if line.origin == "equipo"]
        self.assertEqual(
            {line.assignment for line in equipment_lines}, {"2.1 Concreto zapatas", "Sin asignar"}
        )
        self.assertTrue(all(line.automatic for line in equipment_lines))

    def test_filters(self) -> None:
        self.assertEqual(self.page(estado="draft").count, 1)
        self.assertEqual(self.page(estado="approved").count, 5)  # 2 del libro y 3 automáticos
        self.assertEqual(self.page(tipo="labor").count, 1)
        self.assertEqual(self.page(origen="automaticos").count, 3)
        self.assertEqual(self.page(origen="libro").count, 4)
        self.assertEqual(self.page(capitulo=str(self.structure.pk)).count, 4)
        self.assertEqual(self.page(item=str(self.concrete.pk)).count, 2)
        self.assertEqual(self.page(desde="2026-03-10", hasta="2026-03-11").count, 4)
        self.assertEqual(self.page(estado="no-existe").count, 7)

    def test_totals_by_kind(self) -> None:
        self.assertEqual(
            self.page().totals,
            (
                ("Materiales", D("1000.00")),
                ("Mano de obra", D("700.00")),
                ("Equipo propio (automático)", D("1000.00")),
                ("Transporte (automático)", D("1400.00")),
            ),
        )

    def test_pagination(self) -> None:
        for n in range(150):
            entry(self.world, day=date(2026, 2, 1), description=f"Gasto {n}")
        first, second = self.page(1), self.page(2)
        self.assertEqual((first.pages, len(first.lines), len(second.lines)), (2, 100, 57))
        self.assertTrue(first.has_next and second.has_previous)
        self.assertEqual(self.page(9).number, 2)

    def test_reversal_marks(self) -> None:
        entry(
            self.world,
            day=date(2026, 3, 13),
            amount="-1000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            reverses=self.approved,
            decided_by=self.world.admin,
        )
        lines = {line.entry_id: line for line in self.page().lines if line.entry_id}
        self.assertTrue(lines[self.approved.pk].reversed)
        self.assertTrue(lines[CostEntry.objects.get(reverses=self.approved).pk].is_reversal)

    def test_export(self) -> None:
        entry(self.world, description="=SUMA(A1)")
        lines = export_lines(self.ctx, self.world.project, LedgerFilters(), catalog=self.catalog)
        with zipfile.ZipFile(io.BytesIO(ledger_workbook(lines))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn(">Concreto premezclado<", sheet)
        self.assertIn(">'=SUMA(A1)<", sheet)
        self.assertIn(">Equipo propio<", sheet)
        with mock.patch.object(ledger, "MAX_EXPORT_LINES", 2), self.assertRaises(LedgerTooLarge):
            export_lines(self.ctx, self.world.project, LedgerFilters(), catalog=self.catalog)

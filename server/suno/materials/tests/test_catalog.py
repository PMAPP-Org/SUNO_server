"""Catálogo de la organización (spec, 1.1 y 3.1): servicios, permisos e importación."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase

from suno.backoffice.export import to_xlsx_book
from suno.core.access import AccessScope, SectionRole
from suno.core.models import AuditEvent, Material, MovementKind
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs
from suno.materials import catalog_import
from suno.materials.catalog_services import MaterialData, create_material, update_material
from suno.materials.common import MaterialRejected
from suno.materials.kardex import Stock

from .factories import draft_purchase, material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Código", "Nombre", "Unidad", "Categoría"]


def workbook(rows: Sequence[Sequence[Any]], header: Sequence[str] = HEADER) -> bytes:
    return to_xlsx_book([("Materiales", list(header), [list(row) for row in rows])])


@skip_without_db
class CatalogServiceTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world
        self.admin = self.mw.ctx()

    def test_create_and_update_a_material(self) -> None:
        brick = create_material(self.admin, MaterialData(" LAD-01 ", "Ladrillo tolete", "und"))
        self.assertEqual((brick.code, brick.active), ("LAD-01", True))
        self.assertTrue(AuditEvent.objects.filter(action="material.created").exists())
        updated = update_material(
            self.admin,
            brick.pk,
            MaterialData("LAD-01", "Ladrillo tolete macizo", "und", "Mampostería", False),
        )
        self.assertEqual((updated.name, updated.category, updated.active),
                         ("Ladrillo tolete macizo", "Mampostería", False))  # fmt: skip

    def test_codes_are_unique_ignoring_case_and_fields_are_required(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            create_material(self.admin, MaterialData("cem-01", "Otro", "und"))
        self.assertEqual(caught.exception.errors, ["Ya hay un material con el código cem-01."])
        with self.assertRaises(MaterialRejected) as caught:
            create_material(self.admin, MaterialData(" ", "", "x" * 21))
        self.assertEqual(
            caught.exception.errors,
            ["Escribe el código.", "Escribe el nombre.", "La unidad pasa de 20 caracteres."],
        )
        with self.assertRaises(MaterialRejected):
            update_material(self.admin, self.mw.sand.pk, MaterialData("CEM-01", "Arena", "m3"))

    def test_the_unit_of_a_material_with_movements_does_not_change(self) -> None:
        purchase = draft_purchase(self.mw)
        with transaction.atomic():
            stock = Stock(self.admin, self.mw.project, [(self.mw.warehouse.pk, self.mw.cement.pk)])
            stock.add(str(MovementKind.PURCHASE), self.mw.warehouse.pk, self.mw.cement.pk,
                      D("1"), D("1.00"), date(2026, 3, 2), purchase=purchase)  # fmt: skip
            stock.save()
        with self.assertRaises(MaterialRejected) as caught:
            update_material(self.admin, self.mw.cement.pk, MaterialData("CEM-01", "Cemento", "kg"))
        self.assertEqual(
            caught.exception.errors,
            ["CEM-01 ya tiene movimientos: su unidad no se cambia (crea otro material)."],
        )
        update_material(self.admin, self.mw.cement.pk, MaterialData("CEM-01", "Cemento", "und"))

    def test_who_manages_the_catalog(self) -> None:
        data = MaterialData("LAD-01", "Ladrillo", "und")
        create_material(self.mw.ctx(self.mw.cw.contributor), data)  # Colaborador de toda la org.
        with self.assertRaises(PermissionDenied):
            create_material(self.mw.ctx(self.mw.cw.viewer), MaterialData("X-1", "X", "und"))
        grant_costs(self.world, self.world.supervisor, SectionRole.MANAGER,
                    scope=AccessScope.PROJECTS, projects=[self.mw.project])  # fmt: skip
        with self.assertRaises(PermissionDenied):
            create_material(self.mw.ctx(self.world.supervisor), MaterialData("X-2", "X", "und"))


@skip_without_db
class CatalogImportTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.admin = self.mw.ctx()

    def test_preview_classifies_and_apply_creates_and_updates_once(self) -> None:
        result = catalog_import.preview(
            self.admin,
            workbook([
                ["CEM-01", "Cemento gris 50 kg", "und", "Cementos"],
                ["are-01", "Arena lavada", "m3", "Agregados"],
                ["LAD-01", "Ladrillo tolete", "und", "Mampostería"],
                ["", "", "", ""],
            ]),
        )  # fmt: skip
        self.assertEqual(result.errors, ())
        self.assertEqual([(row.line, row.action) for row in result.rows],
                         [(2, "sin cambios"), (3, "actualiza"), (4, "nuevo")])  # fmt: skip
        self.assertEqual(catalog_import.apply(self.admin, result.token), (1, 1))
        self.assertEqual(Material.objects.get(code="ARE-01").name, "Arena lavada")
        self.assertEqual(Material.objects.filter(org=self.mw.world.org).count(), 4)
        with self.assertRaises(MaterialRejected) as caught:
            catalog_import.apply(self.admin, result.token)
        self.assertEqual(caught.exception.errors, [catalog_import.ALREADY_APPLIED])
        self.assertTrue(AuditEvent.objects.filter(action="material.imported").exists())

    def test_errors_of_the_file(self) -> None:
        result = catalog_import.preview(
            self.admin,
            workbook([["LAD-01", "Ladrillo", "und", ""], ["lad-01", "Ladrillo 2", "und", ""],
                      ["BLO-01", "Bloque", "", ""], ["CEM-01", "Cemento", "kg", ""]]),
        )  # fmt: skip
        self.assertEqual(
            result.errors,
            (
                "Fila 3: El código «lad-01» se repite (fila 2).",
                "Fila 4: Escribe la unidad (por ejemplo m3, kg o und).",
            ),
        )
        self.assertEqual(result.token, "")
        missing = catalog_import.preview(self.admin, workbook([], header=["Código", "Nombre"]))
        self.assertEqual(missing.errors, ("Falta la columna «Unidad».",))

    def test_only_its_author_applies_a_preview(self) -> None:
        result = catalog_import.preview(self.admin, workbook([["LAD-01", "Ladrillo", "und", ""]]))
        with self.assertRaises(MaterialRejected):
            catalog_import.apply(self.mw.ctx(self.mw.cw.contributor), result.token)
        with self.assertRaises(PermissionDenied):
            catalog_import.preview(self.mw.ctx(self.mw.cw.viewer), workbook([]))
        self.assertTrue(catalog_import.template_bytes().startswith(b"PK"))

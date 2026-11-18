"""Bodegas (spec, 1.2 y 3.2 "Bodegas"): crear, renombrar, desactivar."""

from __future__ import annotations

from datetime import date
from unittest import mock, skipUnless

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected
from suno.materials.warehouses import (
    HAS_STOCK,
    create_warehouse,
    rename_warehouse,
    set_warehouse_active,
)

from .factories import material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class WarehouseTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=date(2026, 4, 15))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)

    def test_create_and_rename_with_unique_names(self) -> None:
        north = create_warehouse(self.contributor, self.project, " Frente norte ")
        self.assertEqual(north.name, "Frente norte")
        with self.assertRaises(MaterialRejected) as caught:
            create_warehouse(self.contributor, self.project, "almacén principal")
        self.assertEqual(caught.exception.errors,
                         ["Ya hay una bodega «almacén principal» en el proyecto."])  # fmt: skip
        renamed = rename_warehouse(self.contributor, self.project, north.pk, "Frente norte 2")
        self.assertEqual(renamed.name, "Frente norte 2")
        self.assertEqual(AuditEvent.objects.filter(action__startswith="warehouse.").count(), 2)
        with self.assertRaises(PermissionDenied):  # el Colaborador, solo las suyas
            rename_warehouse(self.contributor, self.project, self.mw.warehouse.pk, "Otra")
        with self.assertRaises(PermissionDenied):
            create_warehouse(self.mw.ctx(self.mw.cw.viewer), self.project, "Otra")

    def test_a_warehouse_with_stock_is_not_deactivated(self) -> None:
        admin = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "1", "32000.00")])
        with self.assertRaises(MaterialRejected) as caught:
            set_warehouse_active(admin, self.project, self.mw.warehouse.pk, False)
        self.assertEqual(caught.exception.errors, [HAS_STOCK])
        empty = create_warehouse(admin, self.project, "Frente sur")
        self.assertFalse(set_warehouse_active(admin, self.project, empty.pk, False).active)
        self.assertTrue(set_warehouse_active(admin, self.project, empty.pk, True).active)

"""El comando de datos de ejemplo siembra un tablero coherente y es seguro de repetir."""

from __future__ import annotations

import unittest
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.context import Principal, resolve_request_context
from suno.core.models import EquipmentUsage, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.equipment.services import (
    create_equipment,
    create_equipment_type,
    equipment_cost_summary,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class SeedEquipmentDemoTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Org demo equipos")
        self.admin = make_user(self.org, username="admin-seed", role=Role.ADMIN)
        Project.objects.create(org=self.org, name="Obra demo", created_by=self.admin)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        etype = create_equipment_type(ctx, code="EXC", name="Excavadora")
        for code in ("EQ-001", "EQ-002", "EQ-003"):
            create_equipment(ctx, equipment_type_id=etype.pk, code=code, name=f"Equipo {code}")
        self.ctx = ctx

    def seed(self, *flags: str) -> None:
        call_command(
            "seed_equipment_demo",
            "--org-name=Org demo equipos",
            "--allow-production",
            *flags,
            stdout=StringIO(),
        )

    def test_seeds_a_dashboard_with_priced_unpriced_and_budgeted_usage(self) -> None:
        self.seed()
        summary = equipment_cost_summary(self.ctx)
        self.assertEqual(len(summary.rows), 5)
        self.assertGreater(summary.total_cost, Decimal("0"))
        self.assertGreater(summary.unpriced, 0)  # EQ-005 tiene tarifa solo desde hace 24 días
        self.assertGreaterEqual(len(summary.monthly), 3)
        self.assertEqual({row.code for row in summary.budget_rows}, {"2.1", "2.2", "3.1"})
        self.assertTrue(
            any(row.pct_consumed and row.pct_consumed > 100 for row in summary.budget_rows)
        )

    def test_refuses_to_duplicate_unless_reset(self) -> None:
        self.seed()
        count = EquipmentUsage.objects.filter(org=self.org).count()
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(EquipmentUsage.objects.filter(org=self.org).count(), count)
        self.seed("--reset")
        self.assertEqual(EquipmentUsage.objects.filter(org=self.org).count(), count)  # determinista

    def test_unknown_organization_is_an_error(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "seed_equipment_demo",
                "--org-name=No existe",
                "--allow-production",
                stdout=StringIO(),
            )

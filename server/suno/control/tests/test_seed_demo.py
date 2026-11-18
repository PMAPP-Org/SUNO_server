"""`seed_control_demo` siembra un proyecto que la página "Control" puede mostrar entero."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase, override_settings

from suno.authn.tests.factories import make_org, make_user
from suno.control.earned_value import compute
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    CertificateStatus,
    ConsumptionCut,
    Contract,
    ContractAmendment,
    ContractCertificate,
    CostEntry,
    CostKind,
    CostStatus,
    InventoryAdjustment,
    Material,
    MaterialIssue,
    MaterialPurchase,
    MaterialReturn,
    MaterialTransfer,
    Project,
    Role,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.management.commands.seed_control_demo import PROJECT_NAME

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: "Hoy" fijo: el comando ancla la demo a `timezone.localdate()` (seis meses hacia atrás) y la
#: prueba la calcula con el mismo corte; fijarlo evita depender del día (y del cambio de día
#: entre sembrar y calcular).
TODAY = date(2026, 11, 12)


@skip_without_db
class SeedControlDemoTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Org demo control")
        self.admin = make_user(self.org, username="admin-control", role=Role.ADMIN)
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)

    def seed(self, *flags: str) -> None:
        call_command(
            "seed_control_demo",
            "--org-name=Org demo control",
            "--allow-production",
            *flags,
            stdout=StringIO(),
        )

    def test_seeds_every_case_of_the_page(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        result = compute(load_control_input(ctx, project, cut=TODAY, kind=MONTH))
        self.assertTrue(result.has_budget and result.has_plan)
        self.assertGreater(result.indicators.bac, Decimal(0))
        self.assertGreater(result.indicators.at_cut.ev, Decimal(0))
        self.assertGreater(len(result.periods), 6)
        self.assertIsNotNone(result.indicators.contractual_direct_cost)
        warnings = result.warnings
        self.assertEqual(warnings.no_baseline, ("4.2 Cámaras de inspección",))
        self.assertEqual(len(warnings.overrun), 1)
        self.assertEqual(len(warnings.outside_plan), 1)
        self.assertEqual(
            [(p.label, p.pct) for p in warnings.percent_tasks],
            [("1.3 Trámites y permisos", Decimal("60.00"))],
        )
        self.assertEqual({r.label for r in result.by_location}, {"Frente norte", "Frente sur"})

    def test_refuses_to_seed_twice(self) -> None:
        self.seed()
        with self.assertRaises(CommandError):
            self.seed()
        self.seed("--project-name=Otra obra de ejemplo")

    def test_needs_an_existing_organization(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "seed_control_demo", "--org-name=No existe", "--allow-production", stdout=StringIO()
            )

    def test_seeds_the_cost_ledger(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        result = compute(load_control_input(ctx, project, cut=TODAY, kind=MONTH, costs=True))
        cost, warnings = result.cost, result.cost_warnings
        assert cost is not None and warnings is not None
        self.assertGreater(cost.ac_direct, Decimal(0))
        self.assertGreater(cost.ac_indirect, Decimal(0))
        self.assertIsNotNone(cost.cpi)
        self.assertIsNotNone(cost.admin_earned)  # Administración 8 %
        self.assertEqual(warnings.pending_count, 1)
        self.assertEqual(
            {u.origin for u in warnings.unassigned},
            {"Equipos propios sin ítem", "Transporte de terceros"},
        )
        entries = CostEntry.objects.filter(project=project)
        self.assertEqual(
            set(entries.values_list("scope", flat=True)),
            {"indirect", "item", "chapter", "selection"},
        )
        self.assertTrue(entries.filter(status=CostStatus.REJECTED).exists())
        self.assertTrue(entries.filter(reverses__isnull=False).exists())
        self.assertFalse(
            entries.filter(
                status=CostStatus.APPROVED, reverses__isnull=True, attachment=None
            ).exists()
        )

    def test_seeds_contracts_amendment_certificates_and_piecework(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        contracts = Contract.objects.filter(project=project)
        self.assertEqual(
            set(contracts.values_list("number", "status")),
            {("SC-001", "approved"), ("DT-001", "approved"), ("SC-002", "settled"),
             ("SC-003", "draft")},
        )  # fmt: skip
        self.assertEqual(contracts.get(number="DT-001").kind, "piecework")
        self.assertTrue(
            ContractAmendment.objects.filter(contract__number="SC-001", status="approved").exists()
        )
        certificates = ContractCertificate.objects.filter(project=project)
        self.assertTrue(certificates.filter(status=CertificateStatus.VOIDED).exists())
        self.assertTrue(certificates.filter(is_settlement=True, status="approved").exists())
        self.assertTrue(certificates.filter(status="draft").exclude(attachment=None).exists())
        entries = CostEntry.objects.filter(project=project, certificate__isnull=False)
        self.assertTrue(entries.filter(reverses__isnull=False).exists())
        self.assertTrue(entries.filter(kind=CostKind.LABOR).exists())
        self.assertFalse(
            CostEntry.objects.filter(project=project, description__startswith="Acta 1: ").exists()
        )

    def test_seeds_materials_with_a_cut_in_the_ledger(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        self.assertEqual(
            set(Material.objects.filter(org=self.org).values_list("code", flat=True)),
            {"CEM-01", "ARE-01", "ACE-12", "TUB-06"},
        )
        self.assertEqual(Warehouse.objects.filter(project=project).count(), 2)
        purchases = MaterialPurchase.objects.filter(project=project)
        self.assertEqual(set(purchases.values_list("kind", "status")),
                         {("warehouse", "approved"), ("direct", "approved"),
                          ("warehouse", "draft")})  # fmt: skip
        self.assertTrue(MaterialReturn.objects.filter(project=project).exists())
        self.assertTrue(MaterialTransfer.objects.filter(project=project).exists())
        self.assertTrue(InventoryAdjustment.objects.filter(project=project, status="approved")
                        .exists())  # fmt: skip
        cut = ConsumptionCut.objects.get(project=project)
        self.assertEqual(cut.status, "approved")
        self.assertTrue(CostEntry.objects.filter(consumption_cut=cut).exists())
        self.assertTrue(MaterialIssue.objects.filter(project=project, cut__isnull=True).exists())
        for row in StockBalance.objects.filter(project=project):
            sums = StockMovement.objects.filter(
                warehouse=row.warehouse, material=row.material
            ).aggregate(q=Sum("quantity"), v=Sum("value"))
            self.assertEqual((row.quantity, row.value), (sums["q"], sums["v"]))

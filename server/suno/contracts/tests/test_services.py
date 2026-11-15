"""Contratos y otrosíes (spec, 1, 2.2, 2.3 y 4): validación, traslapes, ciclo, permisos."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.budgets.tests.samples import AIU
from suno.budgets.totals import BASE_SUBTOTAL, ChargeSpec
from suno.contracts import services
from suno.contracts.services import (
    AmendmentData,
    ContractData,
    ContractRejected,
    IncreaseData,
    LineData,
)
from suno.core.access import AccessScope, SectionRole
from suno.core.audit import AuditAction
from suno.core.models import (
    AmendmentStatus,
    AppUser,
    AuditEvent,
    Contract,
    ContractKind,
    ContractLine,
    ContractStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs, item

from .factories import (
    WITHHOLDINGS,
    contract_line,
    contract_world,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 5, 15)


@skip_without_db
class ContractServiceTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project

    def line(self, code: str, quantity: str, price: str, *uids: int) -> LineData:
        return LineData(
            item_id=item(self.cw.budget, code).pk,
            task_ids=tuple(self.world.task(uid).pk for uid in uids),
            unit="m3",
            quantity=D(quantity),
            unit_price=D(price),
        )

    def data(self, **overrides: Any) -> ContractData:
        values: dict[str, Any] = {
            "kind": ContractKind.SUBCONTRACT,
            "number": "SC-001",
            "purpose": "Estructura en concreto",
            "contractor": "Estructuras del Norte SAS",
            "starts_on": date(2026, 3, 1),
            "ends_on": date(2026, 8, 31),
            "advance_pct": D("30"),
            "retention_pct": D("5"),
            "lines": (self.line("2.1", "10", "400000.00", 5), self.line("2.2", "800", "3500.00")),
            "charges": tuple(AIU),
            "deductions": tuple(WITHHOLDINGS),
        }
        values.update(overrides)
        return ContractData(**values)

    def create(self, user: AppUser | None = None, **overrides: Any) -> Contract:
        return services.create_contract(self.world.ctx(user), self.project, self.data(**overrides))

    def test_create_a_draft_with_value_terms_and_audit(self) -> None:
        contract = self.create()
        self.assertEqual(contract.status, ContractStatus.DRAFT)
        self.assertEqual(contract.current_value, D("8224600.00"))
        self.assertEqual(contract.advance_amount, D("0.00"))
        lines = list(contract.lines.order_by("order"))
        self.assertEqual([(line.order, line.current_quantity) for line in lines],
                         [(1, D("10")), (2, D("800"))])  # fmt: skip
        self.assertEqual(list(lines[0].tasks.all()), [self.world.task(5)])
        self.assertEqual(contract.charges.count(), 4)
        self.assertEqual(contract.deductions.count(), 3)
        event = AuditEvent.objects.get(action=AuditAction.CONTRACT_CREATED)
        self.assertEqual(event.metadata["contract"], str(contract.pk))

    def test_validation_messages(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "ítem": {"lines": (self.line("2", "1", "1.00"),)},
            "al menos una línea": {"lines": ()},
            "anticipo": {"advance_pct": D("101")},
            "fechas": {"ends_on": date(2026, 2, 1)},
            "cantidad": {"lines": (self.line("2.1", "0", "1.00"),)},
            "precio": {"lines": (self.line("2.1", "1", "0.001"),)},
            "Descuentos": {
                "deductions": (ChargeSpec(1, "Mal", D("1"), BASE_SUBTOTAL),),
            },
        }
        for fragment, overrides in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ContractRejected) as refused:
                self.create(**overrides)
            self.assertIn(fragment, " ".join(refused.exception.errors))

    def test_number_is_unique_ignoring_case(self) -> None:
        self.create()
        with self.assertRaises(ContractRejected):
            self.create(number="sc-001")

    def test_an_overlap_needs_confirmation(self) -> None:
        make_contract(self.cw, number="SC-009", lines=(("2.1", "5", "1.00", (5,)),))
        with self.assertRaises(ContractRejected) as refused:
            self.create(lines=(self.line("2.1", "1", "1.00"),))
        self.assertEqual(refused.exception.errors, [])
        self.assertIn("SC-009", refused.exception.overlaps[0])
        # Otras tareas: no se traslapan.
        self.create(number="SC-002", lines=(self.line("2.1", "1", "1.00", 3),))
        # Confirmado: se guarda.
        self.create(number="SC-003", lines=(self.line("2.1", "1", "1.00"),), confirm_overlap=True)
        # Dentro del mismo contrato también se avisa.
        with self.assertRaises(ContractRejected) as twice:
            self.create(
                number="SC-004",
                lines=(self.line("2.2", "1", "1.00"), self.line("2.2", "2", "1.00", 3)),
            )
        self.assertIn("líneas 1 y 2", twice.exception.overlaps[0])

    def test_roles_and_own_drafts(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.create(self.cw.viewer)
        own = self.create(self.cw.contributor)
        other = self.create(number="SC-002", confirm_overlap=True)
        services.update_contract(
            self.world.ctx(self.cw.contributor),
            self.project,
            own.pk,
            self.data(purpose="Otro", confirm_overlap=True),
        )
        with self.assertRaises(PermissionDenied):
            services.update_contract(
                self.world.ctx(self.cw.contributor), self.project, other.pk,
                self.data(number="SC-002"),
            )  # fmt: skip
        with self.assertRaises(PermissionDenied):
            services.delete_contract(self.world.ctx(self.cw.contributor), self.project, other.pk)

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(self.world, scoped, SectionRole.MANAGER, scope=AccessScope.PROJECTS,
                    projects=(elsewhere,))  # fmt: skip
        with self.assertRaises(PermissionDenied):
            self.create(scoped)

    def test_approval_fixes_the_advance_and_needs_someone_else(self) -> None:
        contract = self.create()
        with self.assertRaises(ContractRejected) as own:
            services.approve_contract(self.world.ctx(), self.project, contract.pk)
        self.assertIn("otra persona", own.exception.errors[0])
        approved = services.approve_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk
        )
        self.assertEqual(approved.status, ContractStatus.APPROVED)
        self.assertEqual(approved.advance_amount, D("2467380.00"))
        self.assertEqual(approved.approved_by, self.world.supervisor)
        with self.assertRaises(ContractRejected):
            services.update_contract(self.world.ctx(), self.project, contract.pk, self.data())
        with self.assertRaises(PermissionDenied):
            services.approve_contract(
                self.world.ctx(self.cw.contributor),
                self.project,
                self.create(number="SC-002", confirm_overlap=True).pk,
            )

    def test_the_last_editor_does_not_approve(self) -> None:
        contract = self.create(self.cw.contributor)
        services.update_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk, self.data()
        )
        with self.assertRaises(ContractRejected):
            services.approve_contract(self.world.ctx(self.world.supervisor), self.project,
                                      contract.pk)  # fmt: skip
        services.approve_contract(self.world.ctx(), self.project, contract.pk)

    def test_a_rejection_keeps_the_draft_and_editing_clears_it(self) -> None:
        contract = self.create()
        with self.assertRaises(ContractRejected):
            services.reject_contract(self.world.ctx(self.world.supervisor), self.project,
                                     contract.pk, " ")  # fmt: skip
        rejected = services.reject_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk, "Falta la póliza"
        )
        self.assertEqual((rejected.status, rejected.rejection_note), ("draft", "Falta la póliza"))
        edited = services.update_contract(self.world.ctx(), self.project, contract.pk, self.data())
        self.assertEqual((edited.rejection_note, edited.rejected_by_id), ("", None))
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.CONTRACT_UPDATED, metadata__resubmitted=True
            ).exists()
        )

    def test_only_a_draft_is_deleted(self) -> None:
        draft = self.create()
        services.delete_contract(self.world.ctx(), self.project, draft.pk)
        self.assertFalse(Contract.objects.filter(pk=draft.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_DELETED).exists())
        with self.assertRaises(ContractRejected):
            services.delete_contract(self.world.ctx(), self.project, make_contract(self.cw).pk)

    def test_void_only_without_approved_certificates(self) -> None:
        busy = make_contract(self.cw)
        make_certificate(self.cw, busy, quantities={"2.1": "1"})
        with self.assertRaises(ContractRejected):
            services.void_contract(self.world.ctx(), self.project, busy.pk, "Ya no va")
        idle = make_contract(self.cw, number="SC-002")
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            services.void_contract(self.world.ctx(self.world.supervisor), self.project, idle.pk,
                                   "Ya no va")  # fmt: skip
        with self.assertRaises(ContractRejected):
            services.void_contract(self.world.ctx(), self.project, idle.pk, "")
        voided = services.void_contract(self.world.ctx(), self.project, idle.pk, "Ya no va")
        self.assertEqual((voided.status, voided.close_note), ("voided", "Ya no va"))
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_VOIDED).exists())

    # Revisión de código, M-1 y M-3.

    def test_a_returned_contract_is_not_approved_until_fixed(self) -> None:
        contract = self.create()
        services.reject_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk, "Falta la póliza"
        )
        with self.assertRaises(ContractRejected) as refused:
            services.approve_contract(self.world.ctx(self.world.supervisor), self.project,
                                      contract.pk)  # fmt: skip
        self.assertIn("devuelto", refused.exception.errors[0])

    def test_a_number_taken_in_a_race_is_a_rejection(self) -> None:
        self.create()
        with (
            mock.patch.object(services, "_number_taken", return_value=False),
            self.assertRaises(ContractRejected) as refused,
        ):
            self.create(number="sc-001", confirm_overlap=True)
        self.assertIn("Ya hay un contrato sc-001", refused.exception.errors[0])
        other = self.create(number="SC-002", confirm_overlap=True)
        with (
            mock.patch.object(services, "_number_taken", return_value=False),
            self.assertRaises(ContractRejected),
        ):
            services.update_contract(self.world.ctx(), self.project, other.pk,
                                     self.data(number="SC-001", confirm_overlap=True))  # fmt: skip


@skip_without_db
class AmendmentServiceTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")

    def data(self, **overrides: Any) -> AmendmentData:
        values: dict[str, Any] = {
            "signed_on": date(2026, 5, 2),
            "reason": "Mayor cantidad de concreto y curado",
            "new_ends_on": date(2026, 10, 31),
            "increases": (IncreaseData(self.concrete.pk, D("2")),),
            "new_lines": (
                LineData(item(self.cw.budget, "2.4").pk, (), "m2", D("40"), D("3000.00")),
            ),
        }
        values.update(overrides)
        return AmendmentData(**values)

    def test_approval_applies_increases_new_lines_and_end_date(self) -> None:
        amendment = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk, self.data()
        )
        self.assertEqual(amendment.number, 1)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.current_value, D("8224600.00"))  # aún no cuenta
        services.approve_amendment(
            self.world.ctx(self.world.supervisor), self.project, self.contract.pk, amendment.pk
        )
        self.contract.refresh_from_db()
        self.concrete.refresh_from_db()
        self.assertEqual(self.concrete.current_quantity, D("12"))
        self.assertEqual(self.concrete.quantity, D("10"))
        self.assertEqual(self.contract.ends_on, date(2026, 10, 31))
        self.assertEqual(self.contract.current_value, D("9337340.00"))
        self.assertEqual(self.contract.advance_amount, D("2467380.00"))
        amendment.refresh_from_db()
        self.assertEqual((amendment.status, amendment.value_after),
                         (AmendmentStatus.APPROVED, D("9337340.00")))  # fmt: skip
        new_line = ContractLine.objects.get(contract=self.contract, item__code="2.4")
        self.assertEqual((new_line.order, new_line.amendment_id), (3, amendment.pk))
        event = AuditEvent.objects.get(action=AuditAction.AMENDMENT_APPROVED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_one_open_amendment_only_on_approved_contracts(self) -> None:
        services.create_amendment(self.world.ctx(), self.project, self.contract.pk, self.data())
        with self.assertRaises(ContractRejected):
            services.create_amendment(self.world.ctx(), self.project, self.contract.pk,
                                      self.data())  # fmt: skip
        draft = make_contract(self.cw, number="SC-002", status=ContractStatus.DRAFT)
        with self.assertRaises(ContractRejected):
            services.create_amendment(self.world.ctx(), self.project, draft.pk,
                                      self.data(increases=(), new_lines=()))  # fmt: skip

    def test_validation(self) -> None:
        other = contract_line(make_contract(self.cw, number="SC-002"), "2.1")
        for overrides in (
            {"increases": (), "new_lines": (), "new_ends_on": None},
            {"increases": (IncreaseData(self.concrete.pk, D("-1")),), "new_lines": ()},
            {"increases": (IncreaseData(other.pk, D("1")),), "new_lines": ()},
            {"reason": " "},
            {"signed_on": date(2026, 6, 1)},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ContractRejected):
                services.create_amendment(self.world.ctx(), self.project, self.contract.pk,
                                          self.data(**overrides))  # fmt: skip

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        amendment = services.create_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, self.data()
        )
        services.reject_amendment(self.world.ctx(self.world.supervisor), self.project,
                                  self.contract.pk, amendment.pk, "Sin soporte")  # fmt: skip
        with self.assertRaises(PermissionDenied):
            services.update_amendment(self.world.ctx(), self.project, self.contract.pk,
                                      amendment.pk, self.data())  # fmt: skip
        again = services.update_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, amendment.pk,
            self.data(reason="Con soporte"),
        )  # fmt: skip
        self.assertEqual((again.status, again.decision_note), ("draft", ""))
        services.delete_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, amendment.pk
        )
        self.assertFalse(ContractLine.objects.filter(contract=self.contract,
                                                     item__code="2.4").exists())  # fmt: skip

    def test_the_end_date_only_moves_later(self) -> None:  # revisión de código, M-4
        with self.assertRaises(ContractRejected) as refused:
            services.create_amendment(
                self.world.ctx(), self.project, self.contract.pk,
                self.data(new_ends_on=date(2026, 8, 30)),
            )  # fmt: skip
        self.assertIn("fin vigente", " ".join(refused.exception.errors))
        same = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk,
            self.data(new_ends_on=date(2026, 8, 31)),
        )  # fmt: skip
        self.assertEqual(same.new_ends_on, date(2026, 8, 31))

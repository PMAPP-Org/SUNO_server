"""Cantidad propuesta de un acta (spec, 2.4 y 3.3): periodo, tareas, pagado, validación tardía."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.test import TestCase

from suno.contracts.proposal import (
    LineScope,
    line_scopes,
    paid_by_line,
    paid_by_project,
    proposals,
)
from suno.core.models import CertificateStatus, ContractLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item
from suno.matrix.quantities import executed_by_pair

from .factories import (
    contract_line,
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MARCH_1, MARCH_15, MARCH_16, MARCH_31 = (
    date(2026, 3, 1), date(2026, 3, 15), date(2026, 3, 16), date(2026, 3, 31),
)  # fmt: skip


@skip_without_db
class ProposalTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.ctx = self.cw.world.ctx()
        # 2.1 solo en la tarea 2.1 Columnas (uid 5); 2.2 en todas.
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        self.steel = contract_line(self.contract, "2.2")
        field_quantity(self.cw, 5, "2.1", "3", date(2026, 3, 5))
        field_quantity(self.cw, 5, "2.1", "2", date(2026, 3, 20))
        field_quantity(self.cw, 3, "2.1", "7", date(2026, 3, 6))  # otra tarea: no es de la línea
        self.late = field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 12), validated=False)
        field_quantity(self.cw, 3, "2.2", "100", date(2026, 3, 8))
        field_quantity(self.cw, 5, "2.2", "50", date(2026, 3, 9))

    def scopes(self) -> list[LineScope]:
        lines = ContractLine.objects.filter(contract=self.contract).prefetch_related("task_links")
        return line_scopes(lines)

    def test_period_and_tasks_of_the_line(self) -> None:
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_1, end=MARCH_15)
        concrete, steel = found[self.concrete.pk], found[self.steel.pk]
        self.assertEqual(
            (concrete.validated, concrete.in_period, concrete.paid), (D(3), D(3), D(0))
        )
        self.assertEqual(concrete.proposed, D(3))
        self.assertEqual(steel.proposed, D(150))

    def test_paid_is_discounted_and_a_late_validation_enters_the_next_one(self) -> None:
        make_certificate(
            self.cw, self.contract, start=MARCH_1, end=MARCH_15,
            quantities={"2.1": "3", "2.2": "150"},
        )  # fmt: skip
        self.cw.world.approve(self.late)  # capturado el 12, validado después del acta 1
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_16, end=MARCH_31)
        concrete = found[self.concrete.pk]
        self.assertEqual((concrete.validated, concrete.paid), (D(9), D(3)))
        self.assertEqual(concrete.in_period, D(2))
        self.assertEqual(concrete.proposed, D(6))
        self.assertEqual(found[self.steel.pk].proposed, D(0))

    def test_only_approved_certificates_count_as_paid(self) -> None:
        make_certificate(self.cw, self.contract, quantities={"2.1": "3"},
                         status=CertificateStatus.VOIDED)  # fmt: skip
        make_certificate(self.cw, self.contract, number=2, start=date(2026, 4, 1),
                         end=date(2026, 4, 30), quantities={"2.1": "1"},
                         status=CertificateStatus.DRAFT)  # fmt: skip
        self.assertEqual(paid_by_line(self.ctx, [self.concrete.pk]), {})

    def test_never_negative(self) -> None:
        make_certificate(self.cw, self.contract, quantities={"2.1": "8"})
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_1, end=MARCH_31)
        self.assertEqual(found[self.concrete.pk].proposed, D(0))

    def test_executed_by_pair_by_capture_date(self) -> None:
        pair = (self.cw.world.task(5).pk, item(self.cw.budget, "2.1").pk)
        project_ids = [self.cw.project.pk]
        self.assertEqual(executed_by_pair(self.ctx, project_ids=project_ids)[pair], D(5))
        first_half = executed_by_pair(
            self.ctx, project_ids=project_ids, captured_from=MARCH_1, captured_to=MARCH_15
        )
        self.assertEqual(first_half[pair], D(3))
        self.assertNotIn(pair, executed_by_pair(self.ctx, project_ids=project_ids,
                                                captured_to=MARCH_1))  # fmt: skip

    def test_three_queries(self) -> None:
        scopes = self.scopes()
        with self.assertNumQueries(3):
            proposals(self.ctx, self.cw.project, scopes, start=MARCH_1, end=MARCH_31)

    def test_paid_by_project_without_an_id_list(self) -> None:  # revisión de código, I-3
        make_certificate(self.cw, self.contract, quantities={"2.1": "3", "2.2": "10"})
        make_certificate(self.cw, self.contract, number=2, start=date(2026, 4, 1),
                         end=date(2026, 4, 30), quantities={"2.1": "1"},
                         status=CertificateStatus.VOIDED)  # fmt: skip
        with self.assertNumQueries(1):
            paid = paid_by_project(self.ctx, self.cw.project)
        self.assertEqual(paid, {self.concrete.pk: D(3), self.steel.pk: D(10)})
        self.assertEqual(paid, paid_by_line(self.ctx, [self.concrete.pk, self.steel.pk]))

"""Prueba de carga de la pestaña "Contratos" (spec, 5): 200 contratos, 2000 líneas, 5000 actas
aprobadas (50 000 líneas de acta). La lista y la ficha, en menos de 1 s (2 s en CI)."""

from __future__ import annotations

import time
import unittest
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from suno.control.tests.test_performance import (
    FIRST_START,
    LIMIT_SECONDS,
    MEASUREMENTS,
    build_large_project,
)
from suno.core.models import (
    BudgetItem,
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Task,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import support_blob
from suno.projects.tests.factories import FieldWorld, make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
CONTRACTS = 200
LINES = 10
CERTIFICATES = 25
QUANTITY = D("1000")
PER_CERTIFICATE = D("4")


def add_contracts(world: FieldWorld) -> None:
    org, project = world.org, world.project
    items = list(BudgetItem.objects.filter(budget__project=project, code__contains=".")
                 .order_by("code"))  # fmt: skip
    tasks = list(Task.objects.filter(project=project, uid__gte=1000).order_by("sort_order"))
    blob = support_blob("carga de contratos")
    now = timezone.now()
    prices = [D(1000 + k) for k in range(LINES)]
    direct = sum((QUANTITY * price for price in prices), D(0))
    contracts = Contract.objects.bulk_create(
        [
            Contract(
                org=org, project=project,
                kind=ContractKind.PIECEWORK if n % 4 == 0 else ContractKind.SUBCONTRACT,
                number=f"SC-{n:03d}", purpose=f"Contrato de carga {n}",
                contractor=f"Contratista de carga {n}", starts_on=FIRST_START,
                ends_on=FIRST_START + timedelta(days=400), status=ContractStatus.APPROVED,
                advance_pct=D("10"), advance_amount=direct / 10, retention_pct=D("5"),
                current_value=direct, created_by=world.admin, updated_by=world.admin,
                approved_by=world.supervisor, approved_at=now,
            )
            for n in range(CONTRACTS)
        ]
    )  # fmt: skip
    lines: list[ContractLine] = []
    links: list[ContractLineTask] = []
    for n, contract in enumerate(contracts):
        for k in range(LINES):
            j = n * LINES + k
            line = ContractLine(
                org=org, contract=contract, order=k + 1,
                item=items[(j // len(tasks) * 7 + j) % len(items)], unit="m3",
                quantity=QUANTITY, current_quantity=QUANTITY, unit_price=prices[k],
            )  # fmt: skip
            lines.append(line)
            links.append(ContractLineTask(org=org, line=line, task=tasks[j % len(tasks)]))
    ContractLine.objects.bulk_create(lines, batch_size=2000)
    ContractLineTask.objects.bulk_create(links, batch_size=2000)
    gross = sum((PER_CERTIFICATE * price for price in prices), D(0))
    certificates: list[ContractCertificate] = []
    certificate_lines: list[CertificateLine] = []
    for n, contract in enumerate(contracts):
        own = lines[n * LINES : (n + 1) * LINES]
        for c in range(CERTIFICATES):
            start = FIRST_START + timedelta(days=7 * c)
            certificate = ContractCertificate(
                org=org, contract=contract, project=project, number=c + 1,
                period_start=start, period_end=start + timedelta(days=6),
                status=CertificateStatus.APPROVED, created_by=world.admin,
                updated_by=world.admin, decided_by=world.supervisor, decided_at=now,
                attachment=blob, attachment_name="acta.pdf", direct=gross, gross=gross,
                amortization=gross / 10, retention=gross / 20, net=gross * D("0.85"),
            )  # fmt: skip
            certificates.append(certificate)
            certificate_lines += [
                CertificateLine(
                    org=org,
                    certificate=certificate,
                    line=line,
                    proposed_quantity=PER_CERTIFICATE,
                    quantity=PER_CERTIFICATE,
                    amount=PER_CERTIFICATE * line.unit_price,
                )  # fmt: skip
                for line in own
            ]
    ContractCertificate.objects.bulk_create(certificates, batch_size=2000)
    CertificateLine.objects.bulk_create(certificate_lines, batch_size=5000)
    # Lo cargado en la transacción de la prueba no tiene estadísticas (en producción las pone
    # el autovacuum): sin ellas el planificador estima 8 actas aprobadas en vez de 5000 y elige
    # un nested loop que hace la lista 100 veces más lenta.
    with connection.cursor() as cursor:
        for table in ("contract", "contract_line", "contract_line_task", "contract_certificate",
                      "certificate_line"):  # fmt: skip
            cursor.execute(f"ANALYZE {table}")


@skip_without_db
class ContractLoadTests(TestCase):
    world: FieldWorld

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga de contratos")
        build_large_project(cls.world)
        add_contracts(cls.world)

    def best_of(self, url: str, expected: str) -> float:
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)
        return min(timings)

    def test_list_and_sheet_render_under_the_limit(self) -> None:
        project = self.world.project
        self.assertEqual(Contract.objects.filter(project=project).count(), CONTRACTS)
        self.assertEqual(ContractLine.objects.filter(contract__project=project).count(),
                         CONTRACTS * LINES)  # fmt: skip
        self.assertEqual(ContractCertificate.objects.filter(project=project).count(),
                         CONTRACTS * CERTIFICATES)  # fmt: skip
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{project.pk}/contratos/"
        contract = Contract.objects.get(project=project, number="SC-199")
        for url, expected in (
            (base, "Contrato de carga 199"),
            (f"{base}{contract.pk}/?pestana=lineas", "Ítem"),
            (f"{base}{contract.pk}/?pestana=actas", "Acta 25"),
        ):
            with self.subTest(url=url):
                best = self.best_of(url, expected)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")

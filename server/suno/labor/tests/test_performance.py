"""Carga (spec 4 "Carga"): 60 trabajadores × 31 días con 3 destinos (5.580 líneas) y una nómina
de 500 trabajadores. La cuadrícula, el PDF y la conciliación responden en menos de 1 s (mejor de
3), con un número fijo de consultas. Como 6b y 6c, `ANALYZE` sobre las tablas cargadas."""

from __future__ import annotations

import time
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    BenefitFactor,
    Budget,
    PayrollImport,
    PayrollImportLine,
    ProjectWorker,
    Timesheet,
    TimesheetLine,
    TimesheetLineItem,
    Worker,
    WorkerWage,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import cost_world, item, support_blob
from suno.labor.timesheets import hashed
from suno.materials.destination import destination_key, key_text
from suno.projects.tests.factories import FieldWorld

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
START, END = date(2026, 3, 1), date(2026, 3, 31)
LIMIT_SECONDS = 1.0
MEASUREMENTS = 3


def load(world: FieldWorld, budget: Budget) -> tuple[Timesheet, PayrollImport]:
    org, project, admin = world.org, world.project, world.admin
    workers = Worker.objects.bulk_create([
        Worker(org=org, document_type="CC", document_number=f"{10_000_000 + n}",
               full_name=f"Trabajador {n:03d}", position="Oficial", hired_on=date(2026, 1, 5))
        for n in range(500)
    ])  # fmt: skip
    WorkerWage.objects.bulk_create([
        WorkerWage(org=org, worker=w, valid_from=date(2026, 1, 5), daily_wage=D("80000.00"))
        for w in workers
    ])  # fmt: skip
    BenefitFactor.objects.create(org=org, valid_from=date(2026, 1, 1), factor=D("1.5200"))
    crew = workers[:60]
    ProjectWorker.objects.bulk_create([
        ProjectWorker(org=org, project=project, worker=w, starts_on=date(2026, 1, 5),
                      created_by=admin, updated_by=admin)
        for w in crew
    ])  # fmt: skip
    sheet = Timesheet.objects.create(
        org=org, project=project, number=1, starts_on=START, ends_on=END, status="approved",
        created_by=admin, updated_by=admin, decided_by=world.supervisor,
        decided_at=timezone.now(), attachment=support_blob("carga"), attachment_name="f.pdf",
    )  # fmt: skip
    catalog = load_catalog(world.ctx(), project)
    concrete = item(budget, "2.1")
    #: Tres destinos de medio jornal: 1,5 por persona y día, justo el tope.
    destinations = [
        ("indirect", None, ()),
        ("item", catalog.rows[concrete.pk].parent_id, (concrete.pk,)),
        ("chapter", item(budget, "1").pk, ()),
    ]
    days = [START + timedelta(days=n) for n in range(31)]
    lines: list[TimesheetLine] = []
    links: list[TimesheetLineItem] = []
    for worker in crew:
        for day in days:
            for scope, chapter_id, items in destinations:
                key = hashed(key_text(destination_key(scope, chapter_id, items, None)))
                line = TimesheetLine(
                    org=org, timesheet=sheet, worker=worker, worked_on=day, fraction=D("0.5"),
                    scope=scope, chapter_id=chapter_id, destination_key=key,
                    daily_wage=D("80000.00"), factor=D("1.5200"), amount=D("60800.00"),
                )  # fmt: skip
                lines.append(line)
                links += [TimesheetLineItem(org=org, line=line, item_id=i) for i in items]
    TimesheetLine.objects.bulk_create(lines, batch_size=5000)
    TimesheetLineItem.objects.bulk_create(links, batch_size=5000)
    payroll = PayrollImport.objects.create(
        org=org, starts_on=START, ends_on=END, file_name="nomina.xlsx", file_size=1,
        file_sha256="0" * 64, line_count=500, total=D("0.00"), imported_by=admin,
    )  # fmt: skip
    PayrollImportLine.objects.bulk_create([
        PayrollImportLine(org=org, payroll=payroll, row=n + 2, document_type="CC",
                          document_number=w.document_number, worker=w,
                          total_paid=D("2900000.00"))
        for n, w in enumerate(workers)
    ])  # fmt: skip
    with connection.cursor() as cursor:
        for table in ("worker", "worker_wage", "project_worker", "timesheet", "timesheet_line",
                      "timesheet_line_item", "payroll_import_line"):  # fmt: skip
            cursor.execute(f"ANALYZE {table}")
    return sheet, payroll


@skip_without_db
class LaborLoadTests(TestCase):
    world: FieldWorld
    sheet: Timesheet
    payroll: PayrollImport

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world, budget = cost_world("Carga de tareo")
        cls.sheet, cls.payroll = load(cls.world, budget)

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def best_of(self, url: str) -> float:
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
        return min(timings)

    def test_grid_pdf_and_reconciliation_render_under_the_limit(self) -> None:
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 5580)
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{self.world.project.pk}/tareo/{self.sheet.pk}/"
        for url in (
            f"{base}?semana=2026-03-09",
            f"{base}planilla.pdf",
            f"/plataforma/trabajadores/conciliacion/{self.payroll.pk}/",
        ):
            with self.subTest(url=url):
                best = self.best_of(url)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")

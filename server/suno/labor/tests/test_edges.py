"""Bordes de los servicios de mano de obra que no cubrían las demás pruebas (revisión de la 6d,
"Cobertura"): validaciones, ids inexistentes, carreras traducidas a mensajes e importaciones
con archivos que no se pueden leer."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock
from uuid import uuid4

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, QueryDict
from django.test import TestCase

from suno.core.models import BenefitFactor, ProjectWorker, Worker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import pdf_upload
from suno.labor import (
    approval,
    assignments,
    common,
    forms,
    grid,
    payroll,
    queries,
    registry,
    registry_import,
    timesheet_import,
    timesheets,
)
from suno.labor.assignments import AssignmentData
from suno.labor.common import LaborRejected
from suno.labor.registry import WorkerData
from suno.materials.destination import DestinationData

from .factories import (
    HIRED,
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    make_worker,
    other_project,
    xlsx,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]
WORKERS = ["Tipo de documento", "Número de documento", "Nombre", "Cargo", "Fecha de ingreso",
           "Jornal", "Jornal desde"]  # fmt: skip
PAYROLL = ["Tipo de documento", "Número de documento", "Total pagado"]
TAREO = ["Tipo de documento", "Número de documento", "Fecha", "Destino", "Tarea", "Fracción"]
FROZEN = ("80000.00", "1.5200", "121600.00")


class ImportCellTests(unittest.TestCase):
    def test_cells_of_every_kind(self) -> None:
        from datetime import datetime

        from suno.labor import imports

        self.assertEqual(imports.cell_text(None), "")
        self.assertEqual(imports.cell_text(True), "")
        self.assertEqual(imports.cell_text(12.0), "12")
        self.assertEqual(imports.cell_text(1.5), "1.5")
        self.assertEqual(imports.cell_text(datetime(2026, 3, 2, 8)), "2026-03-02")
        self.assertEqual(imports.cell_text(MON), "2026-03-02")
        self.assertEqual(imports.cell_text("'=1+1"), "=1+1")
        self.assertEqual(imports.cell_day(datetime(2026, 3, 2, 8)), MON)
        self.assertEqual(imports.cell_day(MON), MON)
        self.assertEqual(imports.cell_day("02/03/2026"), MON)
        self.assertIsNone(imports.cell_day("31/02/2026"))
        with self.assertRaises(ValueError):
            imports.cell_decimal(True)


class FormHelperTests(unittest.TestCase):
    def test_rows_with_bad_counts_or_workers_are_skipped(self) -> None:
        self.assertEqual(forms.read_rows(QueryDict("filas=abc"), [MON]), [])
        post = QueryDict(f"filas=2&f0-trabajador=no&f1-trabajador={uuid4()}")
        self.assertEqual(len(forms.read_rows(post, [MON])), 1)

    def test_repeat_and_split_ignore_what_is_out_of_range(self) -> None:
        row = forms.PostedRow(uuid4(), "", "", {MON: "1"})
        self.assertEqual(forms.repeat_previous([row], date(2030, 1, 1), [MON]), [row])
        self.assertEqual(forms.repeat_previous([row], MON, [MON]), [row])  # el primer día
        self.assertEqual(forms.split_row([row], 5, [MON]), [row])
        many = [row] * common.MAX_GRID_ROWS
        self.assertEqual(len(forms.split_row(many, 0, [MON])), common.MAX_GRID_ROWS)

    def test_worker_errors_for_long_texts(self) -> None:
        data = WorkerData("CC", "123456", "N" * 201, "C" * 81, HIRED)
        errors = registry.worker_errors(data)
        self.assertIn("El nombre pasa de 200 caracteres.", errors)
        self.assertIn("El cargo pasa de 80 caracteres.", errors)

    def test_a_long_note_is_refused(self) -> None:
        with self.assertRaises(LaborRejected):
            common.clean_note("x" * 501, "del rechazo")


@skip_without_db
class ServiceEdgeTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.pedro = self.lw.pedro

    def test_ids_that_do_not_exist_are_404(self) -> None:
        missing = uuid4()
        for name, action in (
            ("retire", lambda: registry.retire_worker(self.ctx, missing, TODAY)),
            ("wage", lambda: registry.change_wage(self.ctx, missing, D("1"))),
            ("factor", lambda: registry.delete_factor(self.ctx, missing)),
            ("assignment", lambda: assignments.get_assignment(self.ctx, self.lw.project, missing)),
            ("signed", lambda: timesheets.attach_signed(self.ctx, self.lw.project, missing,
                                                        pdf_upload(), "f.pdf")),
        ):  # fmt: skip
            with self.subTest(name), self.assertRaises(Http404):
                action()

    def test_worker_rules(self) -> None:
        data = WorkerData("CC", "52123456", "María", "", HIRED)
        with mock.patch("suno.labor.registry._duplicate", return_value=""):
            with self.assertRaises(LaborRejected) as raced:  # otra alta entre la revisión y él
                registry.create_worker(self.ctx, data, D("1000"))
        self.assertEqual(raced.exception.errors, ["Ese documento ya existe."])
        with self.assertRaises(LaborRejected):
            registry.update_worker(self.ctx, self.pedro.pk, WorkerData("XX", "1", "", "", HIRED))
        with self.assertRaises(LaborRejected) as taken:
            registry.update_worker(self.ctx, self.pedro.pk, data)
        self.assertIn("Ya hay un trabajador con CC ***3456.", taken.exception.errors)
        with self.assertRaises(LaborRejected):
            registry.retire_worker(self.ctx, self.pedro.pk, date(2025, 1, 1))
        other = other_project(self.lw)
        ProjectWorker.objects.filter(project=other).update(starts_on=date(2026, 4, 1))
        with self.assertRaises(LaborRejected) as later:
            registry.retire_worker(self.ctx, self.pedro.pk, date(2026, 3, 1))
        self.assertIn("empiezan después del retiro", later.exception.errors[0])
        self.assertTrue(Worker.objects.get(pk=self.pedro.pk).is_active)

    def test_wage_and_factor_rules(self) -> None:
        for action in (
            lambda: registry.add_wage(self.ctx, self.pedro.pk, TODAY, D("0")),
            lambda: registry.change_wage(self.ctx, uuid4(), D("0.001")),
            lambda: registry.add_factor(self.ctx, TODAY, D("1.5"), "x" * 501),
        ):
            with self.assertRaises(LaborRejected):
                action()
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, self.pedro, MON, frozen=FROZEN)
        factor = BenefitFactor.objects.get(org=self.lw.world.org)
        with self.assertRaises(LaborRejected) as used:
            registry.delete_factor(self.ctx, factor.pk)
        self.assertEqual(used.exception.errors, [registry.USED])

    def test_assignment_rules(self) -> None:
        with self.assertRaises(LaborRejected):
            assignments.create_assignment(self.ctx, self.lw.project,
                                          AssignmentData(uuid4(), HIRED, None))  # fmt: skip
        luis = make_worker(self.lw.world.org, "70111222", "Luis Torres")
        row = assignments.create_assignment(
            self.ctx, self.lw.project, AssignmentData(luis.pk, HIRED, date(2026, 3, 31))
        )
        with self.assertRaises(LaborRejected):
            assignments.update_assignment(
                self.ctx, self.lw.project, row.pk, AssignmentData(self.pedro.pk, HIRED, None)
            )
        with self.assertRaises(PermissionDenied):
            assignments.update_assignment(self.lw.ctx(self.lw.viewer), self.lw.project, row.pk,
                                          AssignmentData(luis.pk, HIRED, None))  # fmt: skip
        assignments.delete_assignment(self.ctx, self.lw.project, row.pk)  # con fin: sin tareo
        self.assertFalse(ProjectWorker.objects.filter(pk=row.pk).exists())

    def test_timesheet_rules(self) -> None:
        with self.assertRaises(LaborRejected) as early:
            timesheets.create_timesheet(self.ctx, self.lw.project, date(1999, 12, 30),
                                        date(2000, 1, 2))  # fmt: skip
        self.assertIn("La planilla no puede empezar antes de 2000.", early.exception.errors)
        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        with mock.patch("suno.labor.timesheets.MAX_LINES", 1):
            with self.assertRaises(LaborRejected):
                timesheets.save_lines(self.ctx, self.lw.project, sheet.pk,
                                      [line_data(self.pedro, MON)] * 2, [MON])  # fmt: skip
        wrong = [
            timesheets.LineData(uuid4(), MON, DestinationData("indirect"), D("1")),
            line_data(self.pedro, date(2026, 3, 20)),
            line_data(self.pedro, MON, "1", DestinationData("item")),  # ítem sin ítems
        ]
        with self.assertRaises(LaborRejected) as bad:
            timesheets.save_lines(self.ctx, self.lw.project, sheet.pk, wrong,
                                  [MON, date(2026, 3, 3)])  # fmt: skip
        text = " ".join(bad.exception.errors)
        self.assertIn("no está en el registro", text)
        with mock.patch("suno.labor.timesheets.MAX_ERRORS", 1):
            with self.assertRaises(LaborRejected) as cut:
                timesheets.save_lines(self.ctx, self.lw.project, sheet.pk, wrong, [MON])
        self.assertEqual(len(cut.exception.errors), 1)
        timesheets.attach_signed(self.ctx, self.lw.project, sheet.pk, pdf_upload(), "f.pdf")
        with self.assertRaises(LaborRejected):  # sin líneas no se aprueba
            approval.approve_timesheet(self.lw.ctx(self.lw.world.supervisor), self.lw.project,
                                       sheet.pk)  # fmt: skip
        with self.assertRaises(PermissionDenied):
            timesheets.attach_signed(self.lw.ctx(self.lw.viewer), self.lw.project, sheet.pk,
                                     pdf_upload(), "f.pdf")  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheets.attach_signed(self.ctx, self.lw.project, sheet.pk,
                                     SimpleUploadedFile("x.exe", b"MZ"), "x.exe")  # fmt: skip
        approved = bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 3, 9),
                                  end=date(2026, 3, 15))  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheets.attach_signed(self.ctx, self.lw.project, approved.pk, pdf_upload(), "f")
        with self.assertRaises(LaborRejected):
            approval.approve_timesheet(self.lw.ctx(self.lw.world.supervisor), self.lw.project,
                                       approved.pk)  # fmt: skip

    def test_grid_day_outside_the_week_and_position_filter(self) -> None:
        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        catalog = load_catalog(self.ctx, self.lw.project)
        built = grid.build_grid(self.ctx, self.lw.project, sheet, catalog, {}, MON)
        self.assertEqual(grid.day_rows(built, date(2026, 3, 20)), [])
        page = queries.worker_page(self.ctx, queries.WorkerFilters(position="ayudante"), page=1)
        self.assertEqual([w.full_name for w in page], ["María Gómez"])

    def test_imports_that_cannot_be_read(self) -> None:
        bad = b"no es un excel"
        self.assertTrue(registry_import.preview(self.ctx, bad).errors)
        self.assertTrue(payroll.preview(self.ctx, bad, MON, MON, "n.xlsx").errors)
        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.assertTrue(timesheet_import.preview(self.ctx, self.lw.project, sheet, bad).errors)
        for preview in (
            registry_import.preview(self.ctx, xlsx("Trabajadores", WORKERS, [])),
            payroll.preview(self.ctx, xlsx("Nómina", PAYROLL, []), MON, MON, "n.xlsx"),
            timesheet_import.preview(self.ctx, self.lw.project, sheet, xlsx("Tareo", TAREO, [])),
        ):
            with self.subTest(preview=type(preview).__name__):
                self.assertIn("no tiene filas", preview.errors[0])
        self.assertTrue(payroll.preview(self.ctx, xlsx("Nómina", ["Nombre"], [["A"]]), MON, MON,
                                        "n.xlsx").errors)  # fmt: skip
        self.assertTrue(
            timesheet_import.preview(
                self.ctx, self.lw.project, sheet, xlsx("Tareo", ["Nombre"], [["A"]])
            ).errors
        )
        approved = bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 3, 9),
                                  end=date(2026, 3, 15))  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheet_import.preview(self.ctx, self.lw.project, approved,
                                     xlsx("Tareo", TAREO, []))  # fmt: skip

    def test_tokens_with_bad_contents_are_refused(self) -> None:
        from suno.labor import imports

        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        rows = [[2, str(self.pedro.pk), MON.isoformat(), "Capítulo 99", "", "1"]]
        token = imports.sign(self.ctx, timesheet_import.SALT,
                             {"timesheet": str(sheet.pk), "rows": rows})  # fmt: skip
        with self.assertRaises(LaborRejected) as bad:
            timesheet_import.apply(self.ctx, self.lw.project, sheet.pk, token)
        self.assertTrue(bad.exception.errors[0].startswith("Fila 2:"))
        empty = imports.sign(self.ctx, timesheet_import.SALT,
                             {"timesheet": str(sheet.pk), "rows": []})  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, sheet.pk, empty)
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, uuid4(), empty)
        for salt, action in (
            (registry_import.SALT, lambda t: registry_import.apply(self.ctx, t)),
            (payroll.SALT, lambda t: payroll.apply(self.ctx, t)),
        ):
            with self.subTest(salt), self.assertRaises(LaborRejected):
                action(imports.sign(self.ctx, salt, {"rows": []}))
        forged = signing_dumps({"org": str(self.lw.world.org.pk), "user": str(self.ctx.user_id),
                                "nonce": ""}, registry_import.SALT)  # fmt: skip
        with self.assertRaises(LaborRejected):
            registry_import.apply(self.ctx, forged)

    def test_payroll_rows_with_problems(self) -> None:
        rows: list[list[Any]] = [["", "", ""], ["CC", "1" * 21, "abc"]]
        rows += [["XX", str(n), "1"] for n in range(60)]
        preview = payroll.preview(self.ctx, xlsx("Nómina", PAYROLL, rows), MON, MON, "n.xlsx")
        text = " ".join(preview.errors)
        self.assertIn("pasa de 20 caracteres", text)
        self.assertIn("El total pagado va en pesos", text)
        self.assertEqual(preview.errors[-1], "Hay más errores: corrige estos y vuelve a subir el "
                         "archivo.")  # fmt: skip

    def test_worker_rows_with_problems(self) -> None:
        rows: list[list[Any]] = [
            ["CC", "1012345678", "Pedro", "", "2026-01-05", 90000, "2026-01-01"],  # antes
            ["CC", "70111222", "Ana", "", "2026-02-01", 70000, "ayer"],
            ["CC", "x", "", "", "", "", ""],
            ["CC", "x", "", "", "", "", ""],
        ]
        rows += [["CC", f"9{n:05d}", "", "", "", "", ""] for n in range(60)]
        preview = registry_import.preview(self.ctx, xlsx("Trabajadores", WORKERS, rows))
        text = " ".join(preview.errors)
        self.assertIn("El jornal no puede regir antes del ingreso.", text)
        self.assertIn("«Jornal desde» no es una fecha.", text)
        self.assertIn("se repite", text)
        self.assertEqual(preview.errors[-1], "Hay más errores: corrige estos y vuelve a subir el "
                         "archivo.")  # fmt: skip


def signing_dumps(payload: dict[str, Any], salt: str) -> str:
    return signing.dumps(payload, salt=salt, compress=True)

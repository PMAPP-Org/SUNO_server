"""Planilla en la web (spec 2.3, 2.4, 2.5, 3.4 y 3.5): firma, aprobación, rechazo, anulación,
borrado, importación, PDF y Excel."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock
from uuid import UUID

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from suno.authn.tests.factories import make_user
from suno.core.access import SectionRole
from suno.core.models import (
    AuditEvent,
    BenefitFactor,
    CostEntry,
    Role,
    Timesheet,
    TimesheetLine,
    Worker,
    WorkerWage,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs, pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import LaborRejected
from suno.labor.pdf import timesheet_pdf

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    grant_labor,
    labor_world,
    line_data,
    xlsx,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@skip_without_db
class TimesheetFlowTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.lw = labor_world()
        ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(ctx, self.lw.project, *WEEK)
        timesheets.save_lines(ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, WEEK[0])], [WEEK[0]])  # fmt: skip
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"

    def test_print_sign_approve_export_and_reverse(self) -> None:
        self.client.force_login(self.lw.contributor)
        document = self.client.get(self.url + "planilla.pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.exported").exists())
        version = str(Timesheet.objects.get(pk=self.sheet.pk).version)
        code = timesheets.version_code(Timesheet.objects.get(pk=self.sheet.pk))
        self.client.post(
            self.url + "firmada/", {"file": pdf_upload(), "version": version, "codigo": code}
        )
        self.client.force_login(self.lw.world.supervisor)
        self.assertRedirects(
            self.client.post(self.url + "aprobar/", {"version": version}), self.url
        )
        self.assertEqual(CostEntry.objects.filter(timesheet=self.sheet).count(), 1)
        sheet = self.client.get(self.url + "planilla.xlsx")
        self.assertEqual(sheet["Content-Type"], XLSX)
        self.assertTrue(AuditEvent.objects.filter(action="labor.exported").exists())
        self.client.force_login(self.lw.world.admin)
        self.client.post(self.url + "anular/", {"motivo": "Planilla duplicada"})
        self.assertEqual(Timesheet.objects.get(pk=self.sheet.pk).status, "reversed")
        self.assertEqual(CostEntry.objects.filter(timesheet=self.sheet).count(), 2)

    def test_the_signed_scan_is_an_export(self) -> None:
        """M3: Consulta no exporta (decisión 2): tampoco baja el escaneo firmado."""
        version = str(Timesheet.objects.get(pk=self.sheet.pk).version)
        self.client.force_login(self.lw.contributor)
        code = timesheets.version_code(Timesheet.objects.get(pk=self.sheet.pk))
        self.client.post(
            self.url + "firmada/", {"file": pdf_upload(), "version": version, "codigo": code}
        )
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(self.url + "firmada/ver/").status_code, 403)
        self.assertNotContains(self.client.get(self.url), "firmada/ver/")
        self.client.force_login(self.lw.contributor)
        self.assertEqual(self.client.get(self.url + "firmada/ver/").status_code, 302)
        event = AuditEvent.objects.filter(action="timesheet.exported").latest("created_at")
        self.assertEqual(event.metadata["what"], "planilla firmada")

    def test_viewer_cannot_print_and_reviewer_cannot_reverse(self) -> None:
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(self.url + "planilla.pdf").status_code, 403)
        self.assertEqual(self.client.get(self.url + "planilla.xlsx").status_code, 403)
        self.client.force_login(self.lw.world.supervisor)
        self.assertNotContains(self.client.get(self.url), "Anular")

    def test_reject_and_delete(self) -> None:
        self.client.force_login(self.lw.world.supervisor)
        self.client.post(self.url + "rechazar/", {"motivo": "Falta la firma del maestro"})
        self.assertEqual(Timesheet.objects.get(pk=self.sheet.pk).status, "rejected")
        self.client.force_login(self.lw.contributor)
        self.assertContains(self.client.get(self.url), "Falta la firma del maestro")
        self.assertRedirects(self.client.post(self.url + "borrar/"),
                             f"/plataforma/proyectos/{self.lw.project.pk}/tareo/")  # fmt: skip
        self.assertFalse(Timesheet.objects.filter(pk=self.sheet.pk).exists())

    def test_import_through_the_web(self) -> None:
        self.client.force_login(self.lw.contributor)
        header = ["Tipo de documento", "Número de documento", "Fecha", "Destino", "Tarea",
                  "Fracción"]  # fmt: skip
        book = xlsx("Tareo", header, [["CC", "52123456", "2026-03-03", "Indirecto", "", "1"]])
        upload = SimpleUploadedFile("tareo.xlsx", book, content_type=XLSX)
        preview = self.client.post(self.url + "importar/", {"file": upload})
        self.assertContains(preview, "María Gómez · CC ***3456")
        token = preview.context["result"].token
        self.assertRedirects(self.client.post(self.url + "importar/aplicar/", {"token": token}),
                             self.url)  # fmt: skip
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 2)
        self.assertEqual(
            self.client.get(
                f"/plataforma/proyectos/{self.lw.project.pk}/tareo/plantilla.xlsx"
            ).status_code,
            200,
        )

    def test_pdf_escapes_user_text(self) -> None:
        Worker.objects.filter(pk=self.lw.pedro.pk).update(full_name="<b>Pedro</b> & Cía")
        content = timesheet_pdf(
            self.lw.ctx(),
            self.lw.project,
            Timesheet.objects.get(pk=self.sheet.pk),
            generated_on=TODAY,
        )
        self.assertTrue(content.startswith(b"%PDF"))


class PdfDayLabelTests(unittest.TestCase):
    def test_day_columns_are_in_spanish(self) -> None:
        from datetime import date

        from suno.labor.pdf import day_label

        self.assertEqual(day_label(date(2026, 3, 2)), "lun 02")
        self.assertEqual(day_label(date(2026, 3, 8)), "dom 08")


@skip_without_db
class TimesheetListStaleTests(TestCase):
    """#106: la lista marca «Desactualizada» los borradores cuyo resumen guardado ya no es el
    recálculo de hoy (cambió un jornal o el factor), con la comparación de la aprobación y un
    número de consultas que no crece con la página."""

    WEEKS = (WEEK, (date(2026, 3, 9), date(2026, 3, 15)), (date(2026, 3, 16), date(2026, 3, 22)))

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/"
        self.sheets = [self.draft(start, end) for start, end in self.WEEKS[:1]]

    def draft(self, start: date, end: date) -> Timesheet:
        sheet = timesheets.create_timesheet(self.ctx, self.lw.project, start, end)
        timesheets.save_lines(self.ctx, self.lw.project, sheet.pk, [
            line_data(self.lw.pedro, start), line_data(self.lw.maria, start, "0.5"),
        ], [start])  # fmt: skip
        return sheet

    def listed(self) -> tuple[set[UUID], int]:
        self.client.force_login(self.lw.contributor)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return set(response.context["stale"]), len(queries)

    def test_a_wage_change_marks_the_drafts_it_touches(self) -> None:
        self.sheets += [self.draft(start, end) for start, end in self.WEEKS[1:]]
        frozen = bare_timesheet(self.lw, number=9, start=date(2026, 3, 23),
                                end=date(2026, 3, 29), status="approved")  # fmt: skip
        bare_line(frozen, self.lw.pedro, date(2026, 3, 23),
                  frozen=("80000.00", "1.5200", "121600.00"))  # fmt: skip
        self.assertEqual(self.listed()[0], set())
        WorkerWage.objects.create(
            org=self.lw.world.org, worker=self.lw.pedro, valid_from=date(2026, 3, 9),
            daily_wage=D("90000.00"),
        )  # fmt: skip
        stale, _ = self.listed()
        self.assertEqual(stale, {self.sheets[1].pk, self.sheets[2].pk})
        page = self.client.get(self.url)
        self.assertContains(page, "Desactualizada", count=2)
        # Es la misma comparación que la aprobación: firmada, no se aprueba.
        timesheets.attach_signed(self.ctx, self.lw.project, self.sheets[1].pk, pdf_upload(), "f")
        with self.assertRaises(LaborRejected):
            approval.approve_timesheet(
                self.lw.ctx(self.lw.world.supervisor), self.lw.project, self.sheets[1].pk
            )

    def test_a_factor_change_marks_them_too(self) -> None:
        BenefitFactor.objects.create(org=self.lw.world.org, valid_from=WEEK[0],
                                     factor=D("1.6000"), note="Nuevo")  # fmt: skip
        self.assertEqual(self.listed()[0], {self.sheets[0].pk})

    def test_the_queries_do_not_grow_with_the_page(self) -> None:
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro,
                                  valid_from=WEEK[0], daily_wage=D("90000.00"))  # fmt: skip
        stale, one = self.listed()
        self.assertEqual(len(stale), 1)
        self.sheets += [self.draft(start, end) for start, end in self.WEEKS[1:]]
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.maria,
                                  valid_from=WEEK[0], daily_wage=D("110000.00"))  # fmt: skip
        stale, three = self.listed()
        self.assertEqual(len(stale), 3)
        self.assertEqual(three, one)


@skip_without_db
class TimesheetInboxTests(TestCase):
    """#107: la bandeja «Por aprobar» de Costos lista las planillas firmadas que esperan
    aprobación, solo a quien aprueba tareo en el proyecto, sin nombres ni jornales."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.lw = labor_world()
        ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(ctx, self.lw.project, *WEEK)
        timesheets.save_lines(ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, WEEK[0])], [WEEK[0]])  # fmt: skip
        timesheets.attach_signed(ctx, self.lw.project, self.sheet.pk, pdf_upload(), "f.pdf")
        # Un borrador sin firmar todavía no está por aprobar.
        timesheets.create_timesheet(ctx, self.lw.project, date(2026, 3, 9), date(2026, 3, 15))
        self.inbox = f"/plataforma/proyectos/{self.lw.project.pk}/costos/por-aprobar/"
        self.link = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"

    def test_the_labor_approver_sees_number_period_project_and_total(self) -> None:
        self.client.force_login(self.lw.world.supervisor)
        page = self.client.get(self.inbox)
        self.assertContains(page, "Por aprobar (1)")
        self.assertContains(page, f'href="{self.link}"')
        self.assertContains(page, "Planilla TA-001")
        self.assertContains(page, "02/03/2026 – 08/03/2026")
        self.assertContains(page, self.lw.project.name)
        self.assertContains(page, "121.600,00")
        self.assertNotContains(page, "TA-002")
        for private in ("Pedro", "1012345678", "80.000", "Oficial"):
            self.assertNotContains(page, private)

    def test_only_who_approves_labor_and_not_the_author(self) -> None:
        costs_only = make_user(self.lw.world.org, username="solo-costos", role=Role.VIEWER)
        grant_costs(self.lw.world, costs_only, SectionRole.REVIEWER)
        self.client.force_login(costs_only)
        page = self.client.get(self.inbox)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "TA-001")
        self.assertContains(page, "Por aprobar (0)")
        project_viewer = make_user(self.lw.world.org, username="tareo-consulta", role=Role.VIEWER)
        grant_costs(self.lw.world, project_viewer, SectionRole.REVIEWER)
        grant_labor(self.lw.world, project_viewer, SectionRole.VIEWER)  # lee tareo, no aprueba
        self.client.force_login(project_viewer)
        self.assertNotContains(self.client.get(self.inbox), "TA-001")
        # Quien la editó no la aprueba: no le aparece.
        Timesheet.objects.filter(pk=self.sheet.pk).update(updated_by=self.lw.world.supervisor)
        self.client.force_login(self.lw.world.supervisor)
        self.assertNotContains(self.client.get(self.inbox), "TA-001")

    def test_a_stale_signed_sheet_is_marked_without_extra_queries_per_sheet(self) -> None:
        """M2 (#107): la bandeja no la presenta como aprobable si cambió un jornal o el factor."""
        self.client.force_login(self.lw.world.supervisor)
        self.assertNotContains(self.client.get(self.inbox), "Desactualizada")
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.inbox)
        ctx = self.lw.ctx(self.lw.contributor)
        other = timesheets.create_timesheet(ctx, self.lw.project, date(2026, 3, 16),
                                            date(2026, 3, 22))  # fmt: skip
        timesheets.save_lines(ctx, self.lw.project, other.pk,
                              [line_data(self.lw.maria, date(2026, 3, 16))],
                              [date(2026, 3, 16)])  # fmt: skip
        timesheets.attach_signed(ctx, self.lw.project, other.pk, pdf_upload(), "f.pdf")
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro, valid_from=WEEK[0],
                                  daily_wage=D("90000.00"))  # fmt: skip
        with CaptureQueriesContext(connection) as two:
            page = self.client.get(self.inbox)
        self.assertContains(page, "TA-003")
        self.assertContains(page, "Desactualizada", count=1)
        self.assertEqual(len(two), len(one))

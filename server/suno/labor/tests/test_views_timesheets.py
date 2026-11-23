"""Planilla en la web (spec 2.3, 2.4, 2.5, 3.4 y 3.5): firma, aprobación, rechazo, anulación,
borrado, importación, PDF y Excel."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.core.models import AuditEvent, CostEntry, Timesheet, TimesheetLine, Worker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import timesheets
from suno.labor.pdf import timesheet_pdf

from .factories import TODAY, WEEK, labor_world, line_data, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

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

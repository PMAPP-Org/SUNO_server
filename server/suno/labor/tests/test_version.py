"""Versión de la planilla (I3 y C3 de la revisión): control de concurrencia optimista en la
cuadrícula y el soporte firmado atado a la versión que se imprimió."""

from __future__ import annotations

import io
import tempfile
import unittest
from unittest import mock

from django.test import TestCase, override_settings
from pypdf import PdfReader

from suno.core.models import Timesheet, TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import STALE, LaborRejected
from suno.labor.pdf import timesheet_pdf
from suno.labor.timesheets import CODE_CHANGED

from .factories import TODAY, WEEK, labor_world, line_data

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

MON = WEEK[0]


@skip_without_db
class VersionTests(TestCase):
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
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"

    def save(self, fraction: str, version: int | None) -> Timesheet:
        return timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk,
                                     [line_data(self.lw.pedro, MON, fraction)], [MON],
                                     version=version)  # fmt: skip

    def test_every_edit_bumps_the_version_and_a_stale_one_is_rejected(self) -> None:
        self.assertEqual(self.sheet.version, 1)
        self.assertEqual(self.save("1", 1).version, 2)
        with self.assertRaises(LaborRejected) as caught:
            self.save("0.5", 1)  # otra persona abrió la versión 1
        self.assertEqual(caught.exception.errors, [STALE])
        self.assertEqual(TimesheetLine.objects.get().fraction, 1)
        with self.assertRaises(LaborRejected):
            timesheets.recalculate(self.ctx, self.lw.project, self.sheet.pk, version=1)
        self.assertEqual(timesheets.recalculate(self.ctx, self.lw.project, self.sheet.pk,
                                                version=2).version, 3)  # fmt: skip

    def test_the_code_follows_the_version_and_the_fingerprint(self) -> None:
        first = timesheets.version_code(self.sheet)
        self.assertRegex(first, r"^[0-9A-F]{8}$")
        sheet = self.save("1", 1)
        self.assertNotEqual(timesheets.version_code(sheet), first)
        text = "".join(
            page.extract_text()
            for page in PdfReader(
                io.BytesIO(timesheet_pdf(self.ctx, self.lw.project, sheet, generated_on=TODAY))
            ).pages
        )
        self.assertIn(f"Versión {timesheets.version_code(sheet)}", text)

    def test_the_signed_support_is_tied_to_the_printed_version(self) -> None:
        self.save("1", 1)
        with self.assertRaises(LaborRejected) as caught:
            timesheets.attach_signed(self.ctx, self.lw.project, self.sheet.pk, pdf_upload(),
                                     "f.pdf", version=1)  # fmt: skip
        self.assertEqual(caught.exception.errors, [STALE])
        self.assertIsNone(Timesheet.objects.get(pk=self.sheet.pk).attachment_id)
        sheet = timesheets.attach_signed(self.ctx, self.lw.project, self.sheet.pk, pdf_upload(),
                                         "f.pdf", version=2)  # fmt: skip
        self.assertIsNotNone(sheet.attachment_id)
        self.assertEqual(sheet.version, 2)  # cargar el soporte no es editar

    def test_the_support_needs_the_code_printed_on_it(self) -> None:
        """C3: se imprime la versión 1, alguien edita (versión 2), quien sube recarga la página
        (ve la versión 2) y sube el papel de la versión 1: se rechaza por el código impreso."""
        printed = timesheets.version_code(Timesheet.objects.get(pk=self.sheet.pk))
        self.save("1", 1)  # la edición después de imprimir
        self.client.force_login(self.lw.contributor)
        page = self.client.get(self.url)
        self.assertContains(page, 'name="codigo"', html=False)
        self.assertContains(page, "Código impreso en la planilla")
        refused = self.client.post(self.url + "firmada/", {
            "file": pdf_upload(), "version": "2", "codigo": printed}, follow=True)  # fmt: skip
        self.assertContains(refused, CODE_CHANGED)
        self.assertIsNone(Timesheet.objects.get(pk=self.sheet.pk).attachment_id)
        missing = self.client.post(self.url + "firmada/", {"file": pdf_upload(), "version": "2"},
                                   follow=True)  # fmt: skip
        self.assertContains(missing, CODE_CHANGED)
        current = timesheets.version_code(Timesheet.objects.get(pk=self.sheet.pk))
        typed = f" {current[:4].lower()} {current[4:]} "  # mayúsculas y espacios no importan
        self.client.post(self.url + "firmada/", {"file": pdf_upload(), "version": "2",
                                                 "codigo": typed})  # fmt: skip
        self.assertIsNotNone(Timesheet.objects.get(pk=self.sheet.pk).attachment_id)

    def test_approve_checks_the_version_the_reviewer_saw(self) -> None:
        self.save("1", 1)
        timesheets.attach_signed(self.ctx, self.lw.project, self.sheet.pk, pdf_upload(),
                                 "f.pdf", version=2)  # fmt: skip
        approver = self.lw.ctx(self.lw.world.supervisor)
        with self.assertRaises(LaborRejected):
            approval.approve_timesheet(approver, self.lw.project, self.sheet.pk, version=1)
        sheet = approval.approve_timesheet(approver, self.lw.project, self.sheet.pk, version=2)
        self.assertEqual(sheet.status, "approved")

    def test_pages_send_the_version_and_show_the_code(self) -> None:
        self.save("1", 1)
        self.client.force_login(self.lw.contributor)
        page = self.client.get(self.url)
        self.assertContains(page, '<input type="hidden" name="version" value="2">', html=False)
        # Una segunda pestaña guarda; la primera ya no puede pisarla.
        self.save("0.5", 2)
        data = {"semana": "2026-03-02", "filas": "1", "version": "2", "accion": "guardar",
                "f0-trabajador": str(self.lw.pedro.pk), "f0-destino": "", "f0-tarea": "",
                f"f0-d{MON.toordinal()}": "1"}  # fmt: skip
        page = self.client.post(self.url + "guardar/", data)
        self.assertContains(page, STALE)
        self.assertEqual(TimesheetLine.objects.get().fraction, 0.5)
        without = self.client.post(self.url + "guardar/", {**data, "version": ""}, follow=True)
        self.assertContains(without, STALE)
        code = timesheets.version_code(Timesheet.objects.get(pk=self.sheet.pk))
        stale = self.client.post(self.url + "firmada/", {"file": pdf_upload(), "version": "2",
                                                         "codigo": code},
                                 follow=True)  # fmt: skip
        self.assertContains(stale, STALE)
        self.client.post(
            self.url + "firmada/", {"file": pdf_upload(), "version": "3", "codigo": code}
        )
        self.client.force_login(self.lw.world.supervisor)
        sheet = Timesheet.objects.get(pk=self.sheet.pk)
        page = self.client.get(self.url)
        self.assertContains(page, timesheets.version_code(sheet))
        self.assertRedirects(self.client.post(self.url + "aprobar/", {"version": "3"}), self.url)
        self.assertEqual(Timesheet.objects.get(pk=self.sheet.pk).status, "approved")

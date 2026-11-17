"""Cortes en la web (spec, 3.2 "Cortes" y 3.4): propuesta, PDF, firmado, aprobación, Excel."""

from __future__ import annotations

import tempfile
from datetime import date
from unittest import mock, skipUnless

from django.test import TestCase, override_settings

from suno.core.models import AuditEvent, ConsumptionCut, CostScope, Material, Task
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item, pdf_upload
from suno.materials.destination import DestinationData

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class CutPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/cortes/"
        self.client.force_login(self.mw.cw.contributor)

    def test_proposal_draft_pdf_signature_approval_and_excel(self) -> None:
        proposal = self.client.get(self.base + "?hasta=2026-03-31")
        self.assertContains(proposal, "Indirecto (proyecto)")
        self.assertContains(proposal, "224.000,00")
        self.assertContains(proposal, 'value="2026-03-10"', html=False)  # el inicio que toca
        created = self.client.post(self.base + "nuevo/", {"hasta": "2026-03-31"})
        cut = ConsumptionCut.objects.get()
        url = f"{self.base}{cut.pk}/"
        self.assertRedirects(created, url)
        document = self.client.get(url + "corte.pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(AuditEvent.objects.filter(action="consumption_cut.exported").exists())
        self.client.post(url + "firmado/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        cut.refresh_from_db()
        self.assertEqual(cut.status, "approved")
        sheet = self.client.get(url + "corte.xlsx")
        self.assertTrue(sheet.content.startswith(b"PK"))

    def test_the_sheet_warns_when_the_period_changed(self) -> None:
        self.client.post(self.base + "nuevo/", {"hasta": "2026-03-31"})
        cut = ConsumptionCut.objects.get()
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 20))
        page = self.client.get(f"{self.base}{cut.pk}/")
        self.assertContains(page, "Los consumos del periodo cambiaron desde que se guardó el corte")
        self.client.post(f"{self.base}{cut.pk}/recalcular/", {"hasta": "2026-03-31"})
        cut.refresh_from_db()
        self.assertEqual(cut.issue_count, 2)

    def test_the_pdf_escapes_markup_in_labels(self) -> None:
        markup = "Columnas <b>norte & sur</b> <img src=x>"
        Task.objects.filter(pk=self.mw.world.task(5).pk).update(name=markup)
        Material.objects.filter(pk=self.mw.cement.pk).update(name=f"Cemento {markup}")
        concrete = DestinationData(
            str(CostScope.ITEM), (item(self.mw.cw.budget, "2.1").pk,),
            task_id=self.mw.world.task(5).pk,
        )  # fmt: skip
        issue(self.mw, [(self.mw.cement, "3")], day=date(2026, 3, 12), destination=concrete)
        self.client.post(self.base + "nuevo/", {"hasta": "2026-03-31"})
        cut = ConsumptionCut.objects.get()
        self.assertIn("<b>norte & sur</b>", str(cut.summary["groups"]))
        document = self.client.get(f"{self.base}{cut.pk}/corte.pdf")
        self.assertEqual(document.status_code, 200)
        self.assertTrue(document.content.startswith(b"%PDF"))

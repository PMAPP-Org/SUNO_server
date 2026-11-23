"""Registro y conciliación en la web: los casos que faltaban (revisión de código de la 6d,
"Cobertura"): ediciones rechazadas, jornales de otro trabajador, reincorporar, corregir el
factor, tokens vencidos o reutilizados, plantillas, permisos de solo lectura, archivos inválidos
y proyectos ajenos en la conciliación."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.core.models import BenefitFactor, PayrollImport, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import registry

from .factories import TODAY, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
BASE = "/plataforma/trabajadores/"
PAYROLLS = BASE + "conciliacion/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORKERS = ["Tipo de documento", "Número de documento", "Nombre", "Cargo", "Fecha de ingreso",
           "Jornal"]  # fmt: skip
PAYROLL = ["Tipo de documento", "Número de documento", "Total pagado"]


def upload(content: bytes, name: str = "archivo.xlsx") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=XLSX)


def texts(response: Any) -> list[str]:
    return [str(m) for m in response.context["messages"]] if response.context else []


@skip_without_db
class RegistryCasesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.pedro = self.lw.pedro
        self.client.force_login(self.lw.world.admin)

    def worker_url(self, worker: Worker | None = None, suffix: str = "") -> str:
        return f"{BASE}{(worker or self.pedro).pk}/{suffix}"

    def test_new_and_edit_forms(self) -> None:
        self.assertEqual(self.client.get(BASE + "nuevo/").status_code, 200)
        page = self.client.post(BASE + "nuevo/", {
            "document_type": "CC", "document_number": "1012345678", "full_name": "Otro",
            "hired_on": "2026-02-01", "daily_wage": "70000"})  # fmt: skip
        self.assertContains(page, "Ya hay un trabajador con CC ***5678")
        self.assertEqual(self.client.get(self.worker_url(suffix="editar/")).status_code, 200)
        later = self.client.post(self.worker_url(suffix="editar/"), {
            "document_type": "CC", "document_number": "1012345678", "full_name": "Pedro Pérez",
            "position": "Oficial", "hired_on": "2026-02-01"})  # fmt: skip
        self.assertContains(later, "no puede pasar del 05/01/2026")
        self.assertEqual(Worker.objects.get(pk=self.pedro.pk).hired_on, date(2026, 1, 5))
        moved = self.client.post(self.worker_url(suffix="editar/"), {
            "document_type": "CC", "document_number": "1012345678", "full_name": "Pedro P. Ruiz",
            "position": "Maestro", "hired_on": "2026-01-02"})  # fmt: skip
        self.assertRedirects(moved, self.worker_url())
        self.assertEqual(Worker.objects.get(pk=self.pedro.pk).full_name, "Pedro P. Ruiz")

    def test_change_and_delete_a_wage_and_ignore_another_workers_wage(self) -> None:
        wage = registry.add_wage(self.lw.ctx(), self.pedro.pk, date(2026, 4, 1), D("90000"))
        url = self.worker_url(suffix=f"jornal/{wage.pk}/")
        self.client.post(url, {"valid_from": "2026-04-01", "daily_wage": "95000"})
        self.assertEqual(WorkerWage.objects.get(pk=wage.pk).daily_wage, D("95000.00"))
        self.client.post(url, {"valid_from": "2026-04-01", "daily_wage": ""})  # inválido: nada
        self.assertEqual(WorkerWage.objects.get(pk=wage.pk).daily_wage, D("95000.00"))
        # El jornal de María por la ruta de Pedro: no se toca.
        maria = WorkerWage.objects.get(worker=self.lw.maria)
        other = self.client.post(self.worker_url(suffix=f"jornal/{maria.pk}/"),
                                 {"accion": "borrar"})  # fmt: skip
        self.assertRedirects(other, self.worker_url())
        self.assertTrue(WorkerWage.objects.filter(pk=maria.pk).exists())
        self.client.post(url, {"accion": "borrar"})
        self.assertFalse(WorkerWage.objects.filter(pk=wage.pk).exists())
        first = WorkerWage.objects.get(worker=self.pedro)
        refused = self.client.post(self.worker_url(suffix=f"jornal/{first.pk}/"),
                                   {"accion": "borrar"}, follow=True)  # fmt: skip
        self.assertIn("El jornal de ingreso no se borra: cámbialo.", texts(refused))
        bad = self.client.post(self.worker_url(suffix="jornal/"), {"valid_from": ""}, follow=True)
        self.assertIn("Escribe la fecha y el jornal.", texts(bad))

    def test_retire_reinstate_and_refuse_deleting_with_tareo(self) -> None:
        self.client.post(self.worker_url(suffix="retirar/"), {"retired_on": "2026-04-10"})
        self.assertFalse(Worker.objects.get(pk=self.pedro.pk).is_active)
        self.client.post(self.worker_url(suffix="retirar/"), {"accion": "reincorporar"})
        pedro = Worker.objects.get(pk=self.pedro.pk)
        self.assertEqual((pedro.is_active, pedro.retired_on), (True, None))
        self.client.post(self.worker_url(suffix="retirar/"), {"retired_on": "no"})  # nada
        self.assertTrue(Worker.objects.get(pk=self.pedro.pk).is_active)
        with mock.patch("suno.labor.registry.delete_worker",
                        side_effect=registry.LaborRejected(["Tiene tareo"])):  # fmt: skip
            kept = self.client.post(self.worker_url(suffix="borrar/"))
        self.assertRedirects(kept, self.worker_url())

    def test_change_and_delete_a_factor(self) -> None:
        factor = BenefitFactor.objects.get(org=self.lw.world.org)
        url = f"{BASE}factor/{factor.pk}/"
        self.client.post(url, {"valid_from": "2026-01-01", "factor": "1.6", "note": "Nueva"})
        factor.refresh_from_db()
        self.assertEqual((factor.factor, factor.note), (D("1.6000"), "Nueva"))
        self.client.post(url, {"valid_from": "2026-01-01", "factor": "9"})  # fuera de rango
        factor.refresh_from_db()
        self.assertEqual(factor.factor, D("1.6000"))
        refused = self.client.post(f"{BASE}factor/", {"valid_from": "2026-01-01",
                                                      "factor": "1.7"})  # fmt: skip
        self.assertContains(refused, "Ya hay un factor desde el 01/01/2026.")
        self.assertRedirects(self.client.post(url, {"accion": "borrar"}), f"{BASE}factor/")
        self.assertFalse(BenefitFactor.objects.filter(pk=factor.pk).exists())

    def preview(self) -> str:
        book = xlsx("Trabajadores", WORKERS, [["CC", "70111222", "Ana", "", "2026-02-01", 70000]])
        page = self.client.post(BASE + "importar/", {"file": upload(book)})
        token: str = page.context["result"].token
        return token

    def test_import_tokens_expire_and_are_used_once(self) -> None:
        token = self.preview()
        with mock.patch("suno.labor.imports.TOKEN_MAX_AGE", -1):
            expired = self.client.post(BASE + "importar/aplicar/", {"token": token}, follow=True)
        self.assertIn("La vista previa caducó: vuelve a subir el archivo.", texts(expired))
        self.assertRedirects(self.client.post(BASE + "importar/aplicar/", {"token": token}), BASE)
        again = self.client.post(BASE + "importar/aplicar/", {"token": token}, follow=True)
        self.assertIn("Esta vista previa ya se cargó", texts(again)[0])
        self.assertEqual(Worker.objects.filter(document_number="70111222").count(), 1)

    def test_import_upload_rejects_bad_files(self) -> None:
        self.assertEqual(self.client.get(BASE + "importar/").status_code, 200)
        self.assertContains(self.client.post(BASE + "importar/", {}), "Elige un archivo .xlsx.")
        with mock.patch("suno.labor.registry_views.MAX_BYTES", 10):
            big = self.client.post(BASE + "importar/", {"file": upload(b"x" * 11)})
        self.assertContains(big, "El archivo supera 20 MB.")
        named = self.client.post(BASE + "importar/", {"file": upload(b"x", "datos.csv")})
        self.assertEqual(named.context["result"], None)
        self.assertTrue(named.context["errors"])

    def test_templates_download(self) -> None:
        for path in ("plantilla.xlsx", "conciliacion/plantilla.xlsx"):
            with self.subTest(path=path):
                response = self.client.get(BASE + path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], XLSX)
                self.assertIn("plantilla-", response["Content-Disposition"])

    def test_read_only_users_get_403_on_every_write(self) -> None:
        self.client.force_login(self.lw.viewer)  # Consulta con alcance de organización
        wage = WorkerWage.objects.get(worker=self.pedro)
        factor = BenefitFactor.objects.get(org=self.lw.world.org)
        gets = ["nuevo/", f"{self.pedro.pk}/editar/", "importar/", "plantilla.xlsx",
                "conciliacion/plantilla.xlsx"]  # fmt: skip
        posts = ["nuevo/", f"{self.pedro.pk}/editar/", f"{self.pedro.pk}/jornal/",
                 f"{self.pedro.pk}/jornal/{wage.pk}/", f"{self.pedro.pk}/retirar/",
                 f"{self.pedro.pk}/borrar/", "importar/", "importar/aplicar/", "factor/",
                 f"factor/{factor.pk}/", "conciliacion/importar/",
                 "conciliacion/importar/aplicar/"]  # fmt: skip
        for path in gets:
            with self.subTest(get=path):
                self.assertEqual(self.client.get(BASE + path).status_code, 403)
        for path in posts:
            with self.subTest(post=path):
                self.assertEqual(self.client.post(BASE + path, {}).status_code, 403)
        self.assertEqual(self.client.get(BASE).status_code, 200)  # sí lee
        self.assertEqual(Worker.objects.get(pk=self.pedro.pk).full_name, "Pedro Pérez")


@skip_without_db
class PayrollCasesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.client.force_login(self.lw.world.admin)
        self.book = xlsx("Nómina", PAYROLL, [["CC", "1012345678", "130000"]])

    def post(self, **data: Any) -> Any:
        return self.client.post(PAYROLLS + "importar/", {
            "starts_on": "2026-03-01", "ends_on": "2026-03-31", **data})  # fmt: skip

    def test_upload_rejects_what_it_cannot_read(self) -> None:
        self.assertContains(self.post(), "Elige un archivo .xlsx.")
        self.assertContains(self.client.post(PAYROLLS + "importar/", {"file": upload(self.book)}),
                            "Escribe el periodo de la nómina.")  # fmt: skip
        with mock.patch("suno.labor.reconciliation_views.MAX_BYTES", 10):
            self.assertContains(self.post(file=upload(b"x" * 11)), "El archivo supera 20 MB.")
        named = self.post(file=upload(self.book, "nomina.csv"))
        self.assertIsNone(named.context["result"])
        self.assertTrue(named.context["errors"])
        inverted = self.post(file=upload(self.book), starts_on="2026-03-31",
                             ends_on="2026-03-01")  # fmt: skip
        self.assertContains(inverted, "El periodo de la nómina termina antes de empezar.")
        self.assertFalse(PayrollImport.objects.exists())

    def test_apply_with_a_bad_token_goes_back_with_the_message(self) -> None:
        response = self.client.post(PAYROLLS + "importar/aplicar/", {"token": "x"}, follow=True)
        self.assertRedirects(response, PAYROLLS)
        self.assertIn("La vista previa no es válida", texts(response)[0])

    def test_projects_of_other_organizations_or_bad_ids_are_404(self) -> None:
        preview = self.post(file=upload(self.book))
        self.client.post(PAYROLLS + "importar/aplicar/", {"token": preview.context["result"].token})
        payroll = PayrollImport.objects.get()
        stranger = labor_world("Otra Constructora")
        for suffix in ("", "conciliacion.xlsx"):
            url = f"{PAYROLLS}{payroll.pk}/{suffix}"
            for value in ("no-es-un-uuid", str(stranger.project.pk)):
                with self.subTest(url=url, value=value):
                    self.assertEqual(self.client.get(url, {"proyecto": value}).status_code, 404)
            self.assertEqual(self.client.get(url, {"proyecto": str(self.lw.project.pk)})
                             .status_code, 200)  # fmt: skip
        self.client.force_login(stranger.world.admin)
        self.assertEqual(self.client.get(f"{PAYROLLS}{payroll.pk}/").status_code, 404)

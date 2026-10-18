"""H-16 en el back-office (Paso 24): el Admin respeta el aislamiento por organización.

Se prueba por HTTP, entrando al Admin con el personal de cada organización
(con permisos de Django sobre **todos** los modelos: el aislamiento tiene que
darlo el back-office, no la falta de permisos):

- cada lista muestra solo filas de la propia organización;
- el detalle de una fila ajena no se abre;
- las claves foráneas de los formularios solo ofrecen filas propias;
- al crear, la organización es siempre la de quien crea;
- el superusuario (operador) ve todas, pero no actúa donde no es miembro.
"""

from __future__ import annotations

import unittest
import uuid

from django.test import TestCase
from django.utils import timezone

from visitacampo.core.audit import record
from visitacampo.core.models import (
    Assignment,
    AuditEvent,
    Blob,
    FormTemplate,
    Job,
    Membership,
    PrivacyConsent,
    Role,
    VisitAttachment,
    VisitReview,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import admin_client, make_tenant

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

SHA = "ab" * 32


@skip_without_db
class AdminScopingTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")
        self.rows = {}
        Blob.objects.create(sha256=SHA, size=4, mime="image/jpeg", bucket_key=f"blobs/ab/ab/{SHA}")
        for tenant in (self.a, self.b):
            visit = tenant.visit(altura_cm=1)
            attachment = VisitAttachment.objects.create(
                id=uuid.uuid4(),
                org=tenant.org,
                version=1,
                change_seq=2,
                visit=visit,
                blob_id=SHA,
                role="foto",
                captured_at=timezone.now(),
            )
            job = Job.objects.create(org=tenant.org, type="test.job", status="dead_letter")
            review = VisitReview.objects.create(
                org=tenant.org, visit=visit, status="approved", reviewer=tenant.staff
            )
            assignment = Assignment.objects.create(
                org=tenant.org,
                version=1,
                change_seq=3,
                template_version=tenant.version,
                assigned_to=tenant.agent,
            )
            audit = record(tenant.ctx(), action="test.action", target=f"marca-{tenant.org.name}")
            consent = PrivacyConsent.objects.create(
                org=tenant.org,
                user=tenant.agent,
                device=tenant.device,
                notice_version="2026-10-18",
                purposes=["account"],
                accepted_at=timezone.now(),
            )
            self.rows[tenant.org.name] = {
                "visit": visit,
                "visitattachment": attachment,
                "job": job,
                "visitreview": review,
                "assignment": assignment,
                "auditevent": audit,
                "privacyconsent": consent,
                "device": tenant.device,
                "formtemplateversion": tenant.version,
                "formtemplate": tenant.version.template,
                "membership": Membership.objects.get(user=tenant.agent),
                "appuser": tenant.agent,
                "organization": tenant.org,
            }

    def test_every_list_shows_only_the_own_organization(self) -> None:
        client = admin_client(self.a.staff)
        for model, own in self.rows["Org A"].items():
            foreign = self.rows["Org B"][model]
            with self.subTest(model=model):
                response = client.get(f"/admin/core/{model}/")
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertIn(str(own.pk), content)
                self.assertNotIn(
                    str(foreign.pk), content, "una fila de otra organización en la lista"
                )

    def test_a_foreign_detail_page_does_not_open(self) -> None:
        client = admin_client(self.a.staff)
        for model, foreign in self.rows["Org B"].items():
            with self.subTest(model=model):
                response = client.get(f"/admin/core/{model}/{foreign.pk}/change/")
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn(str(foreign.pk), response.content.decode())

    def test_foreign_keys_only_offer_own_rows(self) -> None:
        client = admin_client(self.a.staff)
        content = client.get("/admin/core/assignment/add/").content.decode()
        self.assertIn(str(self.a.version.pk), content)
        self.assertIn(str(self.a.agent.pk), content)
        self.assertNotIn(str(self.b.version.pk), content)
        self.assertNotIn(str(self.b.agent.pk), content)

    def test_creating_forces_the_own_organization(self) -> None:
        client = admin_client(self.a.staff)
        response = client.post(
            "/admin/core/formtemplate/add/", {"name": "Nueva plantilla", "org": self.b.org.pk}
        )
        self.assertEqual(response.status_code, 302)
        created = FormTemplate.objects.get(name="Nueva plantilla")
        self.assertEqual(
            created.org_id, self.a.org.pk, "nunca la organización que llega en el formulario"
        )

    def test_a_member_without_a_role_in_the_org_sees_nothing(self) -> None:
        Membership.objects.filter(user=self.a.staff).delete()
        client = admin_client(self.a.staff)
        self.assertNotEqual(client.get("/admin/core/visit/").status_code, 200)

    def test_staff_cannot_grant_themselves_superuser(self) -> None:
        client = admin_client(self.a.staff)
        content = client.get(f"/admin/core/appuser/{self.a.staff.pk}/change/").content.decode()
        self.assertNotIn('name="is_superuser"', content, "el campo se muestra como solo lectura")

    def test_the_audit_trail_is_read_only_even_for_its_org(self) -> None:
        client = admin_client(self.a.staff)
        audit = self.rows["Org A"]["auditevent"]
        response = client.post(f"/admin/core/auditevent/{audit.pk}/delete/", {"post": "yes"})
        self.assertNotEqual(response.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(pk=audit.pk).exists())


@skip_without_db
class PlatformOperatorTests(TestCase):
    def setUp(self) -> None:
        self.a = make_tenant("Org A")
        self.b = make_tenant("Org B")
        self.operator = self.a.staff
        self.operator.is_superuser = True
        self.operator.save()

    def test_sees_every_organization(self) -> None:
        visit_a, visit_b = self.a.visit(), self.b.visit()
        content = admin_client(self.operator).get("/admin/core/visit/").content.decode()
        self.assertIn(str(visit_a.pk), content)
        self.assertIn(str(visit_b.pk), content)

    def test_but_cannot_act_where_it_is_not_a_member(self) -> None:
        visit_b = self.b.visit()
        admin_client(self.operator).post(
            "/admin/core/visit/", {"action": "approve", "_selected_action": [str(visit_b.pk)]}
        )
        self.assertFalse(VisitReview.objects.filter(visit=visit_b).exists(), "ver no es actuar")

    def test_blob_is_only_for_the_operator(self) -> None:
        self.assertEqual(admin_client(self.operator).get("/admin/core/blob/").status_code, 200)
        self.assertNotEqual(admin_client(self.b.staff).get("/admin/core/blob/").status_code, 200)


@skip_without_db
class RoleTests(TestCase):
    def test_a_supervisor_can_review_but_not_revoke_devices(self) -> None:
        tenant = make_tenant("Org S", staff_role=Role.SUPERVISOR)
        visit = tenant.visit()
        client = admin_client(tenant.staff)
        client.post(
            "/admin/core/visit/", {"action": "approve", "_selected_action": [str(visit.pk)]}
        )
        self.assertTrue(VisitReview.objects.filter(visit=visit, status="approved").exists())

        from visitacampo.authn.models import RefreshToken

        RefreshToken.objects.create(
            org=tenant.org,
            selector="s" * 22,
            verifier_hash="h" * 64,
            device=tenant.device,
            user=tenant.agent,
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        client.post(
            "/admin/core/device/",
            {"action": "revoke_sessions", "_selected_action": [str(tenant.device.pk)]},
        )
        self.assertTrue(
            RefreshToken.objects.filter(revoked_at__isnull=True).exists(),
            "solo administración revoca",
        )

    def test_export_from_the_admin_is_a_download_of_own_rows(self) -> None:
        tenant, other = make_tenant("Org E", staff_role=Role.SUPERVISOR), make_tenant("Org F")
        own, foreign = tenant.visit(), other.visit()
        response = admin_client(tenant.staff).post(
            "/admin/core/visit/",
            {"action": "export_csv", "_selected_action": [str(own.pk), str(foreign.pk)]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8-sig")
        self.assertIn(str(own.pk), body)
        self.assertNotIn(str(foreign.pk), body)

    def test_suppression_asks_for_confirmation_first(self) -> None:
        tenant = make_tenant("Org G")
        client = admin_client(tenant.staff)
        selection = {
            "action": "suppress_personal_data_action",
            "_selected_action": [str(tenant.agent.pk)],
        }
        response = client.post("/admin/core/appuser/", selection)
        self.assertEqual(response.status_code, 200)
        tenant.agent.refresh_from_db()
        self.assertTrue(tenant.agent.is_active, "sin confirmar no se suprime")
        client.post("/admin/core/appuser/", {**selection, "confirm": "yes"})
        tenant.agent.refresh_from_db()
        self.assertFalse(tenant.agent.is_active)

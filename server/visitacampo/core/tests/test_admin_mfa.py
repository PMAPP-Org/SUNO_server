"""MFA del back-office (riesgo R-20, ISO 27001 A.8.5).

Django Admin exige usuario + contraseña + segundo factor (TOTP o código de
respaldo). Se prueba el login real del Admin, no solo la configuración.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.core.checks import run_checks
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django_otp.admin import OTPAdminSite
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from visitacampo.authn.tests.factories import PASSWORD, make_org, make_user
from visitacampo.core.audit import AuditAction
from visitacampo.core.models import AuditEvent
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

LOGIN_URL = "/admin/login/?next=/admin/"

#: `server/`, para que el subproceso importe `config` aunque pytest corra desde la raíz.
SERVER_DIR = Path(__file__).resolve().parents[3]


class AdminSiteRequiresOtpTests(unittest.TestCase):
    def test_the_admin_site_is_the_otp_one(self) -> None:
        self.assertIsInstance(admin.site, OTPAdminSite)
        self.assertIn("visitacampo.core.apps.OtpAdminConfig", settings.INSTALLED_APPS)
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)

    def test_otp_is_required_unless_switched_off_explicitly(self) -> None:
        """EXC-003: solo un `false` explícito lo apaga; ausente o raro, sigue activo."""
        self.assertTrue(settings.ADMIN_REQUIRE_OTP)
        script = "from django.conf import settings; print(settings.ADMIN_REQUIRE_OTP)"
        for value, expected in (
            (None, "True"),
            ("", "True"),
            ("true", "True"),
            ("desactivado", "True"),
            ("false", "False"),
            (" FALSE ", "False"),
            ("0", "False"),
        ):
            env = {k: v for k, v in os.environ.items() if k != "DJANGO_ADMIN_REQUIRE_OTP"}
            if value is not None:
                env["DJANGO_ADMIN_REQUIRE_OTP"] = value
            with self.subTest(value=value):
                result = subprocess.run(
                    [sys.executable, "-c", f"import django; django.setup(); {script}"],
                    env=env,
                    cwd=SERVER_DIR,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(result.stdout.strip(), expected)

    def test_the_deploy_check_flags_it_while_switched_off(self) -> None:
        with override_settings(ADMIN_REQUIRE_OTP=False):
            ids = [m.id for m in run_checks(include_deployment_checks=True)]
        self.assertIn("visitacampo.W001", ids)
        ids = [m.id for m in run_checks(include_deployment_checks=True)]
        self.assertNotIn("visitacampo.W001", ids)


@skip_without_db
class AdminMfaLoginTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.staff = make_user(self.org, username="backoffice")
        self.staff.is_staff = True
        self.staff.is_superuser = True
        self.staff.save()
        self.client = Client()

    def enroll(self) -> str:
        out = StringIO()
        call_command("enroll_admin_totp", "backoffice", stdout=out)
        return out.getvalue()

    def current_code(self) -> str:
        device = TOTPDevice.objects.get(user=self.staff)
        return f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):06d}"

    def admin_login(self, otp_token: str):
        return self.client.post(
            LOGIN_URL,
            {"username": "backoffice", "password": PASSWORD, "otp_token": otp_token},
        )

    def assert_inside_admin(self) -> None:
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def assert_outside_admin(self) -> None:
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_the_correct_password_alone_does_not_open_the_admin(self) -> None:
        self.enroll()
        response = self.admin_login("")
        self.assertEqual(response.status_code, 200)  # vuelve el formulario con error
        self.assert_outside_admin()

    def test_a_staff_user_without_a_second_factor_cannot_enter(self) -> None:
        self.admin_login("123456")
        self.assert_outside_admin()

    def test_password_plus_a_valid_totp_code_opens_the_admin(self) -> None:
        self.enroll()
        response = self.admin_login(self.current_code())
        self.assertEqual(response.status_code, 302, response.content[:300])
        self.assert_inside_admin()

    def test_a_wrong_totp_code_is_rejected(self) -> None:
        self.enroll()
        good = self.current_code()
        wrong = f"{(int(good) + 1) % 1_000_000:06d}"
        self.admin_login(wrong)
        self.assert_outside_admin()

    def test_a_backup_code_works_once(self) -> None:
        output = self.enroll()
        code = StaticToken.objects.filter(device__user=self.staff).first().token
        self.assertIn(code, output)

        self.admin_login(code)
        self.assert_inside_admin()

        self.client.logout()
        self.admin_login(code)
        self.assert_outside_admin()


@skip_without_db
@override_settings(ADMIN_REQUIRE_OTP=False)
class AdminWithoutOtpTests(TestCase):
    """EXC-003: con el MFA apagado entra usuario + contraseña, y nada más se relaja."""

    def setUp(self) -> None:
        self.org = make_org()
        self.staff = make_user(self.org, username="backoffice")
        self.staff.is_staff = True
        self.staff.save()

    def login(self, password: str = PASSWORD):
        return self.client.post(LOGIN_URL, {"username": "backoffice", "password": password})

    def test_password_alone_opens_the_admin_and_warns_in_the_header(self) -> None:
        response = self.login()
        self.assertEqual(response.status_code, 302, response.content[:300])
        page = self.client.get("/admin/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "EXC-003")

    def test_the_login_form_no_longer_asks_for_a_code(self) -> None:
        self.assertNotContains(self.client.get(LOGIN_URL), 'name="otp_token"')

    def test_a_wrong_password_still_fails(self) -> None:
        self.login("no-es-la-clave")
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_non_staff_still_cannot_enter(self) -> None:
        self.staff.is_staff = False
        self.staff.save()
        self.login()
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_switching_it_back_on_closes_the_unverified_session(self) -> None:
        self.login()
        with override_settings(ADMIN_REQUIRE_OTP=True):
            self.assertEqual(self.client.get("/admin/").status_code, 302)


@skip_without_db
class EnrollCommandTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org()
        self.staff = make_user(self.org, username="backoffice")
        self.staff.is_staff = True
        self.staff.save()

    def test_creates_a_confirmed_totp_device_and_ten_backup_codes(self) -> None:
        out = StringIO()
        call_command("enroll_admin_totp", "backoffice", stdout=out)

        self.assertTrue(TOTPDevice.objects.get(user=self.staff).confirmed)
        self.assertEqual(StaticToken.objects.filter(device__user=self.staff).count(), 10)
        self.assertIn("otpauth://totp/", out.getvalue())
        self.assertIn("issuer=VisitaCampo", out.getvalue())
        event = AuditEvent.objects.get(action=AuditAction.ADMIN_MFA_ENROLLED)
        self.assertEqual(event.actor_id, self.staff.pk)

    def test_refuses_to_overwrite_an_existing_factor_without_replace(self) -> None:
        call_command("enroll_admin_totp", "backoffice", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("enroll_admin_totp", "backoffice", stdout=StringIO())

    def test_replace_discards_the_old_device(self) -> None:
        call_command("enroll_admin_totp", "backoffice", stdout=StringIO())
        old_key = TOTPDevice.objects.get(user=self.staff).key
        time.sleep(0)  # el comando no depende del reloj; solo se cambia la clave
        call_command("enroll_admin_totp", "backoffice", "--replace", stdout=StringIO())
        self.assertNotEqual(TOTPDevice.objects.get(user=self.staff).key, old_key)
        self.assertEqual(StaticToken.objects.filter(device__user=self.staff).count(), 10)

    def test_refuses_users_who_are_not_staff(self) -> None:
        make_user(self.org, username="agente_campo")
        with self.assertRaises(CommandError):
            call_command("enroll_admin_totp", "agente_campo", stdout=StringIO())

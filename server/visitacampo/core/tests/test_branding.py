"""Brand integration must preserve access control and the OTP login form."""

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver"])
class BrandingTests(SimpleTestCase):
    def test_home_prioritizes_public_platform_and_keeps_admin_restricted(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SUNO")
        self.assertContains(response, 'href="/plataforma/ingresar/"')
        self.assertContains(response, 'href="/admin/"')
        self.assertContains(response, "Administración")
        self.assertNotContains(response, 'width="1280"')
        self.assertContains(response, 'lang="es"')
        self.assertContains(response, "suno/css/web.css")
        self.assertRedirects(
            self.client.get("/admin/"), "/admin/login/?next=/admin/", fetch_redirect_response=False
        )

    def test_public_platform_has_its_own_login(self):
        response = self.client.get("/plataforma/ingresar/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ACCESO A LA PLATAFORMA")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, "Acceso restringido para administración")
        self.assertNotContains(response, "Logo_Principal.png")

    @override_settings(ADMIN_REQUIRE_OTP=True)
    def test_branded_login_keeps_csrf_password_and_otp(self):
        response = self.client.get("/admin/login/")
        self.assertContains(response, "suno/css/admin.css")
        self.assertNotContains(response, "suno/brand/Logo_Principal.png")
        for field in ("username", "password", "otp_token", "csrfmiddlewaretoken"):
            self.assertContains(response, f'name="{field}"')

    @override_settings(ADMIN_REQUIRE_OTP=False)
    def test_temporary_mfa_exception_remains_visible(self):
        response = self.client.get("/admin/login/")
        self.assertContains(response, "EXC-003")
        self.assertNotContains(response, 'name="otp_token"')

    def test_brand_assets_are_discoverable_by_collectstatic(self):
        for asset in (
            "css/tokens.css",
            "css/web.css",
            "css/admin.css",
            "fonts/InterVariable.woff2",
            "fonts/LICENSE.txt",
            "brand/Logo_Principal.png",
            "brand/Logo_CabezaSinCirculo.png",
            "brand/Icon_App.png",
        ):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(f"suno/{asset}"))

"""The public platform login is separate from the staff-only admin."""

from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.authn.tests.factories import PASSWORD, make_org, make_user
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class PlatformAccessTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Territorio Norte")
        self.user = make_user(self.org)

    def test_member_can_enter_platform_without_admin_permission(self) -> None:
        response = self.client.post(
            "/plataforma/ingresar/",
            {"username": self.user.username, "password": PASSWORD},
        )
        self.assertRedirects(response, "/plataforma/")

        workspace = self.client.get("/plataforma/")
        self.assertContains(workspace, "Territorio Norte")
        self.assertContains(workspace, self.user.username)

        admin = self.client.get("/admin/")
        self.assertRedirects(
            admin,
            "/admin/login/?next=/admin/",
            fetch_redirect_response=False,
        )

    def test_user_without_membership_is_not_admitted(self) -> None:
        outsider = make_user(
            self.org,
            username="sin-membresia",
            with_membership=False,
        )
        response = self.client.post(
            "/plataforma/ingresar/",
            {"username": outsider.username, "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cuenta sin acceso activo")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_session_expiry_follows_remember_choice(self) -> None:
        self.client.post(
            "/plataforma/ingresar/",
            {"username": self.user.username, "password": PASSWORD},
        )
        self.assertTrue(self.client.session.get_expire_at_browser_close())

        self.client.post("/plataforma/salir/")
        self.client.post(
            "/plataforma/ingresar/",
            {
                "username": self.user.username,
                "password": PASSWORD,
                "remember": "on",
            },
        )
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_logout_requires_post(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/plataforma/salir/").status_code, 405)
        self.assertRedirects(self.client.post("/plataforma/salir/"), "/")
        self.assertNotIn("_auth_user_id", self.client.session)

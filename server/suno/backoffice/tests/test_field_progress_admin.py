"""Admin de registros: la supervisión ve la tarea, el % y el aviso de retroceso."""

from __future__ import annotations

import unittest

from django.contrib.auth.models import Permission
from django.test import TestCase

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.tests.factories import make_field_world

from .factories import admin_client

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

URL = "/admin/core/record/"


@skip_without_db
class RecordAdminFieldProgressTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        staff = self.world.supervisor
        staff.is_staff = True
        staff.save()
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label="core"))
        self.client_ = admin_client(staff)
        self.high = self.world.record(3, "60", hours=1)
        self.world.approve(self.high)
        self.low = self.world.record(3, "40", hours=2)

    def test_list_shows_task_progress_and_regression(self) -> None:
        content = self.client_.get(URL).content.decode()
        self.assertIn("1.2 Descapote · 40 %", content)
        self.assertIn("menor que el último validado (60 %)", content)

    def test_filter_lists_only_regressions(self) -> None:
        content = self.client_.get(URL, {"avance": "menor"}).content.decode()
        self.assertIn(str(self.low.pk), content)
        self.assertNotIn(str(self.high.pk), content)

    def test_detail_shows_the_warning(self) -> None:
        content = self.client_.get(f"{URL}{self.low.pk}/change/").content.decode()
        self.assertIn("menor que el último validado (60 %)", content)

    def test_percentages_use_up_to_two_decimals(self) -> None:
        self.world.record(3, "35.5", hours=3)
        content = self.client_.get(URL).content.decode()
        self.assertIn("1.2 Descapote · 35,5 %", content)

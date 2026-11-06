from __future__ import annotations

import unittest

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.access import get_matrix_project, require_manage
from visitacampo.projects.tests.factories import make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class AccessTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.world.responsible(3)  # el agente ve el proyecto por ser responsable

    def test_field_agent_gets_404_even_seeing_the_project(self) -> None:
        with self.assertRaises(Http404):
            get_matrix_project(self.world.ctx(self.world.agent), self.world.project.pk)

    def test_supervisor_sees_but_cannot_manage(self) -> None:
        ctx = self.world.ctx(self.world.supervisor)
        self.assertEqual(get_matrix_project(ctx, self.world.project.pk), self.world.project)
        with self.assertRaises(PermissionDenied):
            require_manage(ctx)

    def test_admin_manages(self) -> None:
        require_manage(self.world.ctx())

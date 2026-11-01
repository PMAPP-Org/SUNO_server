"""Avance de campo: restricciones de `Visit` y el % validado de cada tarea."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import make_field_world, make_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class VisitTaskProgressConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def test_a_visit_without_task_has_no_progress(self) -> None:
        make_visit(self.world.org, self.world.agent)  # sin tarea ni %: válida
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, pct="10")

    def test_a_linked_visit_needs_its_progress(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, task=self.world.task(3))

    def test_progress_is_between_0_and_100(self) -> None:
        self.world.visit(3, "0")
        self.world.visit(3, "100")
        for pct in ("-0.01", "100.01"):
            with self.subTest(pct=pct), self.assertRaises(IntegrityError), transaction.atomic():
                self.world.visit(3, pct)

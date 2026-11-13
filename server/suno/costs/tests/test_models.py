"""Restricciones de la base del libro de costos (spec, 1.2)."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import CostEntry, CostScope, CostStatus
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import cost_world, entry, item, support_blob

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class CostEntryConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()

    def broken(self, **fields: object) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry(self.world, **fields)  # type: ignore[arg-type]

    def test_amount_is_positive_except_in_a_reversal(self) -> None:
        self.broken(amount="0")
        self.broken(amount="-5.00")
        original = entry(self.world, status=CostStatus.APPROVED)
        reversal = entry(
            self.world,
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )
        self.assertIsNone(reversal.attachment_id)  # un reverso no pide soporte propio
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry(
                self.world,
                amount="1000.00",
                status=CostStatus.APPROVED,
                reverses=entry(self.world, status=CostStatus.APPROVED),
                decided_by=self.world.admin,
            )

    def test_an_entry_is_reversed_once(self) -> None:
        original = entry(self.world, status=CostStatus.APPROVED)
        entry(
            self.world,
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )
        self.broken(
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )

    def test_an_approved_entry_needs_support_and_another_approver(self) -> None:
        draft = entry(self.world)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=draft.pk).update(
                status=CostStatus.APPROVED,
                decided_by=self.world.supervisor,
                decided_at=timezone.now(),
            )
        self.broken(status=CostStatus.APPROVED, decided_by=self.world.admin)
        self.assertEqual(entry(self.world, status=CostStatus.APPROVED).status, "approved")

    def test_a_rejection_needs_its_reason(self) -> None:
        rejected = entry(self.world, status=CostStatus.REJECTED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=rejected.pk).update(decision_note="")

    def test_a_draft_has_no_decision(self) -> None:
        self.broken(decided_by=self.world.supervisor)

    def test_scope_fields(self) -> None:
        chapter = item(self.budget, "2")
        self.broken(scope=CostScope.INDIRECT, chapter=chapter)
        self.broken(scope=CostScope.INDIRECT, task=self.world.task(3))
        self.broken(scope=CostScope.CHAPTER)
        self.broken(scope=CostScope.SELECTION)
        self.assertEqual(
            entry(self.world, scope=CostScope.CHAPTER, chapter=chapter).scope, "chapter"
        )

    def test_the_last_editor_does_not_approve(self) -> None:
        draft = entry(self.world, attachment=support_blob())
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=draft.pk).update(
                updated_by=self.world.supervisor,
                status=CostStatus.APPROVED,
                decided_by=self.world.supervisor,
                decided_at=timezone.now(),
            )

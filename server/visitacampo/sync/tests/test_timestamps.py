"""Fechas del cliente en el push: con zona obligatoria y guardadas en UTC.

Regresión de un `500` que encontró Schemathesis: RFC 3339 admite un desfase de
`-18:47`, `timestamptz` no. Un `500` en el push deja la outbox del teléfono
reintentando para siempre (`core/timestamps.py`).
"""

from __future__ import annotations

import unittest
import uuid

from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, push, visit_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class CapturedAtTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_an_extreme_but_valid_offset_is_applied_in_utc(self) -> None:
        visit_id = uuid.uuid4()
        op = create_op(self.scenario, visit_id=visit_id, captured_at="2026-07-30T04:07:58-18:47")
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["results"][0]["status"], "applied")
        self.assertEqual(visit_of(visit_id).captured_at.isoformat(), "2026-07-30T22:54:58+00:00")

    def test_a_timestamp_without_offset_is_rejected(self) -> None:
        op = create_op(self.scenario, captured_at="2026-07-30T14:05:09")
        result = push(self.scenario, [op]).json()["results"][0]
        self.assertEqual(result["status"], "rejected")

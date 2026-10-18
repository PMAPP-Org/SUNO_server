"""Paso 25, suite de seguridad: el pull, contra otra organización y contra el tamaño.

- **Aislamiento (H-16, tarea 1)**: lo que sincroniza un dispositivo de la
  organización A nunca incluye filas de la B, ni en `pull` ni en `bootstrap`,
  aunque B tenga más cambios y `change_seq` más altos.
- **Presupuesto de queries (H-07, tarea 2)**: un pull de 1 fila y uno de 50
  hacen el mismo número de consultas. Ni permisos ni registros se resuelven
  fila a fila (el patrón N+1 de QFieldCloud).

Requiere PostgreSQL, como el resto de la integración de sync.
"""

from __future__ import annotations

import unittest
import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.sync.cursor import CURSOR_ZERO

from .factories import bootstrap, build_scenario, create_op, pull, push

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def _push_visits(scenario, count: int) -> list[str]:
    ids = [str(uuid.uuid4()) for _ in range(count)]
    response = push(scenario, [create_op(scenario, visit_id=uuid.UUID(i)) for i in ids])
    assert response.status_code == 200, response.content
    return ids


def _ids(body: dict) -> set[str]:
    return {row["data"]["id"] for row in body.get("rows", [])} | {
        tomb["id"] for tomb in body.get("tombstones", [])
    }


@skip_without_db
class PullIsolationTests(TestCase):
    def setUp(self) -> None:
        self.a = build_scenario(username="agente-a")
        self.b = build_scenario(username="agente-b")
        self.own = set(_push_visits(self.a, 2))
        # B con más cambios: sus `change_seq` superan a los de A.
        self.foreign = set(_push_visits(self.b, 5))

    def test_pull_returns_only_the_own_organization(self) -> None:
        response = pull(self.a, CURSOR_ZERO)
        self.assertEqual(response.status_code, 200, response.content)
        ids = _ids(response.json())
        self.assertTrue(self.own <= ids)
        self.assertFalse(ids & self.foreign, "filas de otra organización en el pull")

    def test_bootstrap_returns_only_the_own_organization(self) -> None:
        response = bootstrap(self.a)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["organization"]["id"], str(self.a.org.pk))
        visits = {visit["id"] for visit in body["visits"]}
        templates = {version["id"] for version in body["form_template_versions"]}
        self.assertEqual(visits, self.own)
        self.assertNotIn(str(self.b.template_version.pk), templates)

    def test_the_template_of_another_organization_is_not_synced(self) -> None:
        body = pull(self.a, CURSOR_ZERO).json()
        templates = {
            row["data"]["id"] for row in body["rows"] if row["entity"] == "form_template_version"
        }
        self.assertIn(str(self.a.template_version.pk), templates)
        self.assertNotIn(str(self.b.template_version.pk), templates)


@skip_without_db
class PullQueryBudgetTests(TestCase):
    def _queries_for_pull_of(self, rows: int) -> int:
        scenario = build_scenario(username=f"agente-{rows}")
        _push_visits(scenario, rows)
        with CaptureQueriesContext(connection) as captured:
            response = pull(scenario, CURSOR_ZERO)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreaterEqual(len(response.json()["rows"]), rows)
        return len(captured.captured_queries)

    def test_the_number_of_queries_does_not_grow_with_the_rows(self) -> None:
        self.assertEqual(self._queries_for_pull_of(1), self._queries_for_pull_of(50))

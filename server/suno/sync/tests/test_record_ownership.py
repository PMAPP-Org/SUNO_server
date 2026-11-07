"""Editar o borrar un registro existente: su autor o supervisión (`can_modify_record`).

Antes, cualquier miembro con permiso de escritura podía cambiar o borrar por la
API el registro de otro agente de su organización; la app solo lo impedía en
pantalla. ISO 27001 A.5.15 (mínimo privilegio).
"""

from __future__ import annotations

import unittest
import uuid

from django.test import TestCase

from suno.core.models import Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, delete_op, push, update_op, record_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class RecordOwnershipTests(TestCase):
    def setUp(self) -> None:
        self.author = build_scenario(username="autora")
        self.record_id = uuid.uuid4()
        response = push(self.author, [create_op(self.author, record_id=self.record_id)])
        self.assertEqual(response.json()["results"][0]["status"], "applied", response.content)

    def result(self, scenario, op):
        response = push(scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def test_another_agent_cannot_edit_or_delete_it(self) -> None:
        other = build_scenario(username="otro", org=self.author.org)
        for op in (
            update_op(self.record_id, base_version=1),
            delete_op(self.record_id, base_version=1),
        ):
            with self.subTest(op=op["op_type"]):
                result = self.result(other, op)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["error"]["code"], "forbidden")
        self.assertIsNone(record_of(self.record_id).deleted_at)
        self.assertEqual(record_of(self.record_id).version, 1, "nada cambió")

    def test_the_author_can(self) -> None:
        self.assertEqual(
            self.result(self.author, update_op(self.record_id, base_version=1))["status"], "applied"
        )

    def test_a_supervisor_can(self) -> None:
        supervisor = build_scenario(
            username="supervisora", role=Role.SUPERVISOR, org=self.author.org
        )
        self.assertEqual(
            self.result(supervisor, update_op(self.record_id, base_version=1))["status"], "applied"
        )

    def test_create_and_edit_in_the_same_batch(self) -> None:
        record_id = uuid.uuid4()
        response = push(
            self.author,
            [create_op(self.author, record_id=record_id), update_op(record_id, base_version=1)],
        )
        self.assertEqual([r["status"] for r in response.json()["results"]], ["applied", "applied"])

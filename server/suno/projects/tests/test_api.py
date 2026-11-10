"""`GET /v1/projects/tasks`: las tareas que el agente puede vincular a un registro."""

from __future__ import annotations

import unittest
from typing import Any

from django.test import TestCase

from suno.authn.tests.factories import login
from suno.core.models import TaskResponsible
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.importer import apply_import, stage_import

from .factories import make_field_world
from .samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

URL = "/v1/projects/tasks"


@skip_without_db
class FieldTasksApiTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        token = login(self.client, username=self.world.agent.username).json()["tokens"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def get(self, **params: str) -> dict[str, Any]:
        response = self.client.get(URL, params, **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def names(self, body: dict[str, Any]) -> list[str]:
        return [item["name"] for item in body["items"]]

    def test_requires_authentication(self) -> None:
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_without_responsibilities_the_list_is_empty(self) -> None:
        body = self.get()
        self.assertEqual((body["changed"], body["items"]), (True, []))
        self.assertEqual(len(body["version"]), 64)

    def test_branch_lists_current_detail_tasks_with_last_validated_pct(self) -> None:
        w = self.world
        w.responsible(1)
        w.approve(w.record(3, "40", hours=1))
        w.record(3, "70", hours=2)  # sin validar
        body = self.get()
        self.assertEqual(
            [(i["wbs"], i["name"], i["last_validated_pct"]) for i in body["items"]],
            [("1.1", "Localización", None), ("1.2", "Descapote", 40.0)],
        )
        item = body["items"][1]
        self.assertEqual(item["id"], str(w.task(3).pk))
        self.assertEqual(item["project_id"], str(w.project.pk))
        self.assertEqual(item["project_name"], w.project.name)
        self.assertEqual(item["outline_number"], "1.2")

    def test_same_version_answers_unchanged(self) -> None:
        self.world.responsible(1)
        version = self.get()["version"]
        body = self.get(version=version)
        self.assertEqual((body["changed"], body["items"], body["version"]), (False, [], version))

    def test_reimport_retire_and_rename_change_the_list(self) -> None:
        w = self.world
        w.responsible(4)
        before = self.get()
        self.assertEqual(self.names(before), ["Columnas", "Fin de obra"])
        ctx = w.ctx()
        apply_import(ctx, stage_import(ctx, w.project, file_name="v2.xml", data=v2()).pk)
        after = self.get(version=before["version"])
        self.assertTrue(after["changed"])
        self.assertEqual(self.names(after), ["Vigas", "Fin de obra"])

    def test_a_validation_changes_the_version(self) -> None:
        w = self.world
        w.responsible(1)
        record = w.record(3, "40", hours=1)
        before = self.get()["version"]
        w.approve(record)
        self.assertTrue(self.get(version=before)["changed"])

    def test_removing_the_responsibility_empties_the_list(self) -> None:
        self.world.responsible(1)
        self.assertEqual(len(self.get()["items"]), 2)
        TaskResponsible.objects.all().delete()
        self.assertEqual(self.get()["items"], [])

    def test_another_organization_s_tasks_never_appear(self) -> None:
        other = make_field_world("Otra")
        other.responsible(1)
        self.world.responsible(5)
        self.assertEqual(self.names(self.get()), ["Columnas"])

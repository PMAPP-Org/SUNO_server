"""El script de carga (`infra/load/push_load.py`, Paso 25) habla el contrato real.

La prueba de carga solo corre en su workflow manual (`load.yml`). Si el lote
que construye dejara de ser válido (un campo nuevo obligatorio, un cambio en la
plantilla de `bootstrap_demo`), se descubriría allí, tarde y con 50 hilos. Aquí
se comprueba en cada PR: el lote de `batch()` se aplica entero, operación por
operación, contra una organización sembrada con el mismo `bootstrap_demo`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

from django.core.management import call_command
from django.test import Client, TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

SCRIPT = Path(__file__).resolve().parents[4] / "infra" / "load" / "push_load.py"
PASSWORD = "una-contraseña-de-carga-larga-42"


def _load_script():
    spec = importlib.util.spec_from_file_location("push_load", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclass` resuelve las anotaciones buscando el módulo en sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@skip_without_db
class LoadScriptContractTests(TestCase):
    def setUp(self) -> None:
        self.script = _load_script()
        call_command(
            "bootstrap_demo",
            "--org-name=Org carga contrato",
            "--username=carga-contrato",
            f"--password={PASSWORD}",
            "--extra-agents=1",
            stdout=io.StringIO(),
        )
        self.client = Client()

    def _post(self, path: str, body: object, token: str | None = None):
        extra = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(
            path, data=json.dumps(body), content_type="application/json", **extra
        )

    def test_the_schema_version_matches_the_server(self) -> None:
        from visitacampo.serverinfo.capabilities import SCHEMA_VERSION

        self.assertEqual(self.script.SCHEMA_VERSION, SCHEMA_VERSION)

    def test_the_usernames_match_bootstrap_demo(self) -> None:
        self.assertEqual(self.script.username_for("carga", 0), "carga")
        self.assertEqual(self.script.username_for("carga", 1), "carga-01")

    def test_a_batch_of_100_is_applied_entirely(self) -> None:
        username = self.script.username_for("carga-contrato", 1)
        device_id = "6f1d5a7e-0b3c-4d2e-9f80-1a2b3c4d5e6f"
        login = self._post(
            "/v1/auth/login",
            {
                "username": username,
                "password": PASSWORD,
                "device_info": {
                    "device_id": device_id,
                    "platform": "android",
                    "app_version": "load-test",
                },
            },
        )
        self.assertEqual(login.status_code, 200, login.content)
        token = login.json()["tokens"]["access_token"]
        bootstrap = self.client.get(
            "/v1/sync/bootstrap", HTTP_AUTHORIZATION=f"Bearer {token}"
        ).json()
        template_version_id = bootstrap["form_template_versions"][0]["id"]

        device = self.script.Device(username=username, device_id=device_id, token=token)
        response = self._post(
            "/v1/sync/push", self.script.batch(device, template_version_id, 100), token
        )

        self.assertEqual(response.status_code, 200, response.content)
        statuses = {result["status"] for result in response.json()["results"]}
        self.assertEqual(len(response.json()["results"]), 100)
        self.assertEqual(statuses, {"applied"}, response.content[:500])

    def test_percentile_is_nearest_rank(self) -> None:
        values = [float(n) for n in range(1, 101)]
        self.assertEqual(self.script.percentile(values, 95), 95.0)
        self.assertEqual(self.script.percentile([0.4], 95), 0.4)

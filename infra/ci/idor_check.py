"""Acceso cruzado entre organizaciones (IDOR, H-16) contra el stack real (DAST).

Los tests de integración ya prueban el aislamiento contra la base de datos de
test. Esto lo prueba de punta a punta, como lo vería un atacante: por HTTPS,
a través de Caddy, con la imagen y los settings de producción, y con dos
cuentas reales de dos organizaciones.

La organización B crea una visita. Con las credenciales de A se intenta:

1. verla en `GET /v1/sync/pull` y en `GET /v1/sync/bootstrap`;
2. sobrescribirla con un `create` sobre su id y modificarla con un `update`:
   ninguna operación puede aplicarse ni devolver la fila de B;
3. pedir una URL de subida ligada a ella: `404` (RD-11, no `403`);
4. reutilizar el `op_uuid` de B: se procesa como operación nueva de A, sin
   devolver el resultado guardado de B.

Sale con 1 y la lista de fallos si alguno ocurre. Solo biblioteca estándar.

Uso (lo llama .github/workflows/dast.yml):
    IDOR_PASSWORD=… python infra/ci/idor_check.py --base-url https://host \
        --cafile caddy-root.crt --user-a agente-demo --user-b agente-b
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

SCHEMA_VERSION = 1
CAPTURED_AT = "2026-07-30T14:05:09.123Z"


class Client:
    def __init__(self, base_url: str, cafile: str | None) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("la URL base tiene que ser https://")
        self.base_url = base_url.rstrip("/")
        self.context = ssl.create_default_context(cafile=cafile)
        self.token = ""
        self.device_id = str(uuid.uuid4())

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            # Esquema https validado en __init__; TLS verificado con la CA dada.
            with urllib.request.urlopen(  # nosec B310  # nosemgrep: dynamic-urllib-use-detected
                request, context=self.context, timeout=30
            ) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except ValueError:
                return error.code, raw.decode(errors="replace")[:300]

    def log_in(self, username: str, password: str) -> None:
        status, body = self.call(
            "POST",
            "/v1/auth/login",
            {
                "username": username,
                "password": password,
                "device_info": {
                    "device_id": self.device_id,
                    "platform": "android",
                    "app_version": "dast-idor",
                },
            },
        )
        if status != 200:
            raise SystemExit(f"login de {username} falló: {status} {body}")
        self.token = body["tokens"]["access_token"]

    def template_version_id(self) -> str:
        status, body = self.call("GET", "/v1/sync/bootstrap")
        if status != 200 or not body["form_template_versions"]:
            raise SystemExit(f"bootstrap sin plantilla: {status} {body}")
        return str(body["form_template_versions"][0]["id"])

    def push(self, ops: list[dict[str, Any]]) -> tuple[int, Any]:
        return self.call(
            "POST",
            "/v1/sync/push",
            {
                "schema_version": SCHEMA_VERSION,
                "device_id": self.device_id,
                "batch_uuid": str(uuid.uuid4()),
                "ops": ops,
            },
        )


def create_op(client: Client, template_version_id: str, visit_id: str, op_uuid: str) -> dict:
    return {
        "op_uuid": op_uuid,
        "entity": "visit",
        "op_type": "create",
        "base_version": None,
        "payload": {
            "id": visit_id,
            "template_version_id": template_version_id,
            "answers": {"notas": "secreto de la organización B"},
            "captured_at": CAPTURED_AT,
            "device_id": client.device_id,
        },
    }


def visit_ids(body: Any) -> set[str]:
    rows = body.get("rows", []) if isinstance(body, dict) else []
    visits = body.get("visits", []) if isinstance(body, dict) else []
    return {str(r["data"]["id"]) for r in rows} | {str(v["id"]) for v in visits}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cafile")
    parser.add_argument("--user-a", required=True)
    parser.add_argument("--user-b", required=True)
    args = parser.parse_args()
    password = os.environ.get("IDOR_PASSWORD")
    if not password:
        parser.error("falta IDOR_PASSWORD en el entorno")

    a, b = Client(args.base_url, args.cafile), Client(args.base_url, args.cafile)
    a.log_in(args.user_a, password)
    b.log_in(args.user_b, password)
    template_a, template_b = a.template_version_id(), b.template_version_id()

    failures: list[str] = []

    # La visita de B.
    their_visit, their_op = str(uuid.uuid4()), str(uuid.uuid4())
    status, body = b.push([create_op(b, template_b, their_visit, their_op)])
    if status != 200 or body["results"][0]["status"] != "applied":
        raise SystemExit(f"B no pudo crear su visita: {status} {body}")

    # 1. A no la ve.
    for path in ("/v1/sync/pull?cursor=c1:00000000000000000000", "/v1/sync/bootstrap"):
        status, body = a.call("GET", path)
        if status != 200:
            failures.append(f"{path}: HTTP {status}")
        elif their_visit in visit_ids(body):
            failures.append(f"{path}: devuelve la visita de otra organización")
        elif "secreto de la organización B" in json.dumps(body, ensure_ascii=False):
            failures.append(f"{path}: devuelve datos de otra organización")

    # 2. A no puede escribirla ni recibe su fila.
    update = {
        "op_uuid": str(uuid.uuid4()),
        "entity": "visit",
        "op_type": "update",
        "base_version": 1,
        "payload": {"id": their_visit, "answers": {"notas": "pisado por A"}},
    }
    for label, op in (
        ("create sobre su id", create_op(a, template_a, their_visit, str(uuid.uuid4()))),
        ("update de su visita", update),
    ):
        status, body = a.push([op])
        if status != 200:
            failures.append(f"push ({label}): HTTP {status}")
            continue
        result = body["results"][0]
        if result["status"] == "applied":
            failures.append(f"push ({label}): se aplicó sobre otra organización")
        if result.get("server_row"):
            failures.append(f"push ({label}): devuelve la fila de otra organización")

    # 3. URL de subida ligada a la visita de B: 404, no 403.
    status, _ = a.call(
        "POST",
        "/v1/attachments/presign",
        {"sha256": "a" * 64, "size": 10, "mime": "image/jpeg", "visit_id": their_visit},
    )
    if status != 404:
        failures.append(f"presign con la visita de B: HTTP {status} (se esperaba 404)")

    # 4. El op_uuid de B no devuelve su resultado guardado.
    mine = str(uuid.uuid4())
    status, body = a.push([create_op(a, template_a, mine, their_op)])
    if status != 200:
        failures.append(f"push con el op_uuid de B: HTTP {status}")
    else:
        result = body["results"][0]
        if result.get("entity_id") == their_visit or result["status"] != "applied":
            failures.append("push con el op_uuid de B: devuelve el resultado de B")

    # Y B sigue teniendo su visita intacta.
    status, body = b.call("GET", "/v1/sync/bootstrap")
    notes = [v["answers"].get("notas") for v in body.get("visits", []) if v["id"] == their_visit]
    if notes != ["secreto de la organización B"]:
        failures.append(f"la visita de B cambió o desapareció: {notes}")

    if failures:
        print("IDOR: fallos de aislamiento entre organizaciones:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("OK: A no alcanzó datos de B (pull, bootstrap, push, presign, op_uuid).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

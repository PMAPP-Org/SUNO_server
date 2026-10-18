"""Prueba de carga de `POST /v1/sync/push` (Paso 25, tareas 9-11; riesgo S-05).

N dispositivos de la **misma organización** vacían su outbox a la vez: cada
ronda, todos esperan en una barrera y envían un lote de `--ops` operaciones al
mismo tiempo. Es el peor caso del `pg_advisory_xact_lock(org_id)`, que
serializa los pushes de una organización. Mide la latencia de cada push y
falla si el p95 supera `--p95-max` (criterio de aceptación del MVP: 100
operaciones por lote, p95 < 1,5 s; doc 08 §8).

Comprueba también que el trabajo fue real: cada respuesta es 200 y todas las
operaciones quedan `applied`. Una latencia buena con operaciones rechazadas no
cuenta.

Uso (contra el stack de `infra/docker-compose.yml`, como en `.github/workflows/load.yml`):

    LOAD_PASSWORD=… python infra/load/push_load.py \
        --base-url https://api.visitacampo.localhost --cafile caddy-root.crt \
        --username carga --devices 50 --ops 100 --rounds 3 --report load-report.json

Los agentes los crea `manage.py bootstrap_demo --username carga --extra-agents 49`
(`carga`, `carga-01` … `carga-49`). La contraseña va por variable de entorno, no
por argumento (no aparece en la lista de procesos).

Solo biblioteca estándar: nada que instalar en el runner ni en el servidor.
TLS siempre verificado: con la CA interna de Caddy se pasa `--cafile`; no hay
opción para desactivar la verificación.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Debe coincidir con `visitacampo/serverinfo/capabilities.py::SCHEMA_VERSION`.
SCHEMA_VERSION = 1


@dataclass
class Device:
    username: str
    device_id: str
    token: str


class Api:
    def __init__(self, base_url: str, cafile: str | None, timeout: float) -> None:
        # Solo https: `urlopen` aceptaría también `file:` u otros esquemas.
        if not base_url.startswith("https://"):
            raise ValueError("la URL base tiene que ser https://")
        self.base_url = base_url.rstrip("/")
        self.context = ssl.create_default_context(cafile=cafile)
        self.timeout = timeout

    def call(
        self, method: str, path: str, body: Any = None, token: str | None = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            # El esquema es siempre https (validado en __init__) y TLS se verifica.
            with urllib.request.urlopen(  # nosec B310  # nosemgrep: dynamic-urllib-use-detected
                request, context=self.context, timeout=self.timeout
            ) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(errors="replace")[:500]


def username_for(prefix: str, index: int) -> str:
    return prefix if index == 0 else f"{prefix}-{index:02d}"


def log_in(api: Api, username: str, password: str) -> Device:
    device_id = str(uuid.uuid4())
    status, body = api.call(
        "POST",
        "/v1/auth/login",
        {
            "username": username,
            "password": password,
            "device_info": {
                "device_id": device_id,
                "platform": "android",
                "app_version": "load-test",
            },
        },
    )
    if status != 200:
        raise SystemExit(f"login de {username} falló: {status} {body}")
    return Device(username=username, device_id=device_id, token=body["tokens"]["access_token"])


def batch(device: Device, template_version_id: str, ops: int) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device.device_id,
        "batch_uuid": str(uuid.uuid4()),
        "ops": [
            {
                "op_uuid": str(uuid.uuid4()),
                "entity": "visit",
                "op_type": "create",
                "base_version": None,
                "payload": {
                    "id": str(uuid.uuid4()),
                    "template_version_id": template_version_id,
                    "answers": {"altura_cm": n, "notas": "prueba de carga"},
                    "captured_at": now,
                    "device_id": device.device_id,
                    "lat": 4.6,
                    "lon": -74.08,
                },
            }
            for n in range(ops)
        ],
    }


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[rank]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cafile", help="CA en la que confiar (p. ej. la raíz interna de Caddy)")
    parser.add_argument("--username", default="carga")
    parser.add_argument("--devices", type=int, default=50)
    parser.add_argument("--ops", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--p95-max", type=float, default=1.5, help="segundos")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--report", help="archivo JSON con el resultado")
    args = parser.parse_args()
    if not args.base_url.startswith("https://"):
        parser.error("--base-url tiene que ser https://")
    password = os.environ.get("LOAD_PASSWORD")
    if not password:
        parser.error("falta LOAD_PASSWORD en el entorno")

    api = Api(args.base_url, args.cafile, args.timeout)
    devices = [log_in(api, username_for(args.username, i), password) for i in range(args.devices)]
    status, body = api.call("GET", "/v1/sync/bootstrap", token=devices[0].token)
    if status != 200 or not body["form_template_versions"]:
        raise SystemExit(f"bootstrap sin plantilla publicada: {status} {body}")
    template_version_id = body["form_template_versions"][0]["id"]

    latencies: list[float] = []
    failures: list[str] = []
    lock = threading.Lock()

    def push(device: Device, barrier: threading.Barrier) -> None:
        payload = batch(device, template_version_id, args.ops)
        barrier.wait()
        started = time.perf_counter()
        status, response = api.call("POST", "/v1/sync/push", payload, token=device.token)
        elapsed = time.perf_counter() - started
        with lock:
            latencies.append(elapsed)
            if status != 200:
                failures.append(f"{device.username}: HTTP {status} {response}")
                return
            not_applied = [r for r in response["results"] if r["status"] != "applied"]
            if len(response["results"]) != args.ops or not_applied:
                failures.append(f"{device.username}: {len(not_applied)} operaciones sin aplicar")

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.devices) as pool:
        for round_number in range(1, args.rounds + 1):
            barrier = threading.Barrier(args.devices)
            list(pool.map(lambda d, b=barrier: push(d, b), devices))
            print(
                f"ronda {round_number}/{args.rounds}: {len(latencies)} pushes medidos", flush=True
            )
    wall = time.perf_counter() - wall_started

    p95 = percentile(latencies, 95)
    result = {
        "devices": args.devices,
        "ops_per_push": args.ops,
        "rounds": args.rounds,
        "pushes": len(latencies),
        "operations": len(latencies) * args.ops,
        "p50_s": round(statistics.median(latencies), 3),
        "p95_s": round(p95, 3),
        "p99_s": round(percentile(latencies, 99), 3),
        "max_s": round(max(latencies), 3),
        "wall_s": round(wall, 3),
        "throughput_ops_s": round(len(latencies) * args.ops / wall, 1),
        "p95_max_s": args.p95_max,
        "failures": failures,
        "passed": p95 < args.p95_max and not failures,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as report:
            json.dump(result, report, indent=2, ensure_ascii=False)
    if failures:
        print(f"FALLO: {len(failures)} pushes con error", file=sys.stderr)
    if p95 >= args.p95_max:
        print(f"FALLO: p95 {p95:.3f} s ≥ {args.p95_max} s (riesgo S-05)", file=sys.stderr)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

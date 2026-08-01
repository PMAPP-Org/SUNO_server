"""Serialización por `pg_advisory_xact_lock(org_id)` bajo concurrencia real.

⚠️ **ESTE ARCHIVO NO SE HA EJECUTADO NUNCA.** No hay PostgreSQL disponible en
el entorno donde se escribió el Paso 8, y `pg_advisory_xact_lock` es una
primitiva de PostgreSQL sin equivalente en SQLite: no existe ninguna
aproximación local honesta. Está escrito para que corra en CI —donde
`.github/workflows/backend.yml` levanta un `postgres:16` real y fija
`VISITACAMPO_REQUIRE_DB=1`— y hasta que corra allí, **la garantía de
serialización bajo concurrencia queda sin verificar**. Es el riesgo residual
declarado de este paso, el mismo que arrastran los Pasos 5-7 para su mitad de
integración.

Deliberadamente **no** se ha escrito un sustituto con `threading` sobre SQLite
ni con mocks del cursor: ejercitaría el código de Python alrededor del lock sin
ejercitar el lock, que es exactamente lo que hay que probar. Un test así daría
una sensación de cobertura falsa, que es peor que un skip visible.

**Qué comprueban estos tests, y por qué serían concluyentes.** La asignación
de `change_seq` es `MAX(change_seq) + 1` leído dentro de la transacción
(`changeseq.py`). Sin el lock, dos transacciones concurrentes de la misma
organización leerían **el mismo** `MAX` —cada una en su propio snapshot, sin
ver las filas aún no commiteadas de la otra— y repartirían números solapados:
dos filas con el mismo `change_seq`, que es la corrupción silenciosa del
cursor que describe doc 08 §4.4. Con el lock, la segunda transacción no llega
a leer hasta que la primera ha commiteado. Por eso "todos los `change_seq` son
distintos" no es una comprobación indirecta: es exactamente la invariante que
el lock existe para sostener, y falla de forma determinista si el lock se
quita, se toma demasiado tarde o se toma fuera de la transacción.

Requiere `TransactionTestCase` (no `TestCase`): con la transacción envolvente
de `TestCase`, los hilos no verían los datos del test ni commitearían nada, y
el lock lo tendría tomado la propia transacción del test.
"""

from __future__ import annotations

import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.db import connections
from django.test import TransactionTestCase

from visitacampo.core.models import SyncOpLog, Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, push, update_op

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: Operaciones por lote concurrente. Suficientes para que las dos
#: transacciones se solapen de verdad y no se turnen por casualidad.
OPS_PER_BATCH = 25


def _run_isolated(work: Any) -> Any:
    """Ejecuta `work()` cerrando después la conexión propia del hilo.

    Django abre una conexión por hilo; si no se cierra, la BD de test no se
    puede destruir al terminar (`destroy_test_db` falla con conexiones vivas).
    """
    try:
        return work()
    finally:
        connections.close_all()


@skip_without_db
class AdvisoryLockSerializationTests(TransactionTestCase):
    """Dos pushes simultáneos a la misma organización."""

    def _push_in_parallel(self, first_ops: list[dict], second_ops: list[dict]) -> list[Any]:
        """Lanza los dos lotes a la vez, con una barrera para maximizar el solape."""
        scenario = self.scenario
        barrier = threading.Barrier(2)

        def work(ops: list[dict]) -> Any:
            def run() -> Any:
                barrier.wait(timeout=30)
                return push(scenario, ops)

            return _run_isolated(run)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(work, first_ops), pool.submit(work, second_ops)]
            return [future.result(timeout=60) for future in futures]

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_no_operation_is_lost(self) -> None:
        first = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]
        second = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]

        responses = self._push_in_parallel(first, second)
        for response in responses:
            self.assertEqual(response.status_code, 200, response.content)
            statuses = {result["status"] for result in response.json()["results"]}
            self.assertEqual(statuses, {"applied"})

        self.assertEqual(Visit.objects.count(), 2 * OPS_PER_BATCH)
        self.assertEqual(SyncOpLog.objects.count(), 2 * OPS_PER_BATCH)

    def test_no_two_rows_share_a_change_seq(self) -> None:
        """La invariante que el lock sostiene (doc 08 §4.4).

        Sin el lock, las dos transacciones leen el mismo `MAX(change_seq)` y
        reparten números solapados; el cursor del pull entregaría una de las
        dos filas empatadas y perdería la otra para siempre.
        """
        first = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]
        second = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]
        self._push_in_parallel(first, second)

        seqs = list(Visit.objects.values_list("change_seq", flat=True))
        self.assertEqual(len(seqs), 2 * OPS_PER_BATCH)
        self.assertEqual(len(set(seqs)), len(seqs), "hay `change_seq` repetidos")

    def test_the_two_batches_do_not_interleave_their_change_seq(self) -> None:
        """Serializadas de verdad: cada lote ocupa un tramo contiguo.

        Es la diferencia entre "no colisionan" y "están serializadas". Si el
        lock se tomara por operación en vez de por lote, los números de los dos
        lotes se entrelazarían aunque ninguno se repitiera.
        """
        first = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]
        second = [create_op(self.scenario) for _ in range(OPS_PER_BATCH)]
        responses = self._push_in_parallel(first, second)

        for response in responses:
            ids = [uuid.UUID(result["entity_id"]) for result in response.json()["results"]]
            seqs = sorted(Visit.objects.filter(pk__in=ids).values_list("change_seq", flat=True))
            self.assertEqual(
                seqs,
                list(range(seqs[0], seqs[0] + len(seqs))),
                "el lote no ocupó un tramo contiguo: el lock no serializó el lote entero",
            )

    def test_the_same_op_uuid_sent_twice_at_once_is_applied_once(self) -> None:
        """La carrera de idempotencia: el lock cierra la ventana check-then-apply.

        Sin el lock tomado **antes** de leer `sync_op_log`, las dos requests
        leerían "no está" y aplicarían las dos.
        """
        visit_id = uuid.uuid4()
        op_uuid = uuid.uuid4()
        op = create_op(self.scenario, visit_id=visit_id, op_uuid=op_uuid)

        responses = self._push_in_parallel([op], [dict(op)])
        statuses = [response.json()["results"][0]["status"] for response in responses]

        self.assertEqual(sorted(statuses), ["applied", "applied"])
        self.assertEqual(Visit.objects.filter(pk=visit_id).count(), 1)
        self.assertEqual(SyncOpLog.objects.filter(op_uuid=op_uuid).count(), 1)
        # Aplicada **una** vez: la segunda devolvió el resultado guardado.
        visit = Visit.objects.get(pk=visit_id)
        self.assertEqual(visit.version, 1)
        self.assertEqual(
            [response.json()["results"][0] for response in responses][0],
            [response.json()["results"][0] for response in responses][1],
        )

    def test_concurrent_updates_of_one_row_leave_exactly_one_winner(self) -> None:
        """El CAS por `version`: dos escrituras sobre la misma base, un ganador."""
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        base = Visit.objects.get(pk=visit_id).version
        responses = self._push_in_parallel(
            [update_op(visit_id, base_version=base, lat=1.0)],
            [update_op(visit_id, base_version=base, lat=2.0)],
        )
        statuses = sorted(response.json()["results"][0]["status"] for response in responses)
        self.assertEqual(statuses, ["applied", "conflict"])
        self.assertEqual(Visit.objects.get(pk=visit_id).version, base + 1)

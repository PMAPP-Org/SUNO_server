"""`GET /v1/sync/bootstrap` y `GET /v1/sync/pull` — los casos del Paso 9.

(a) pull con `cursor=0` tras varios pushes → orden estricto por `change_seq`
(b) pull tras un borrado lógico → tombstone en `tombstones[]`, no en `rows[]`
(c) cursor anterior a la ventana de retención (purga simulada) → `409 cursor_expired`
(d) bootstrap tras "cursor expirado" → estado completo + cursor válido
(e) `limit`/`has_more`/`next_cursor`: paginación real, no una aproximación
(f) dos pushes concurrentes seguidos de un pull → ninguna fila se pierde

**Requieren PostgreSQL** (mismo motivo que `test_push.py`: `pg_advisory_xact_lock`
no existe en SQLite) salvo `EntityCoverageTests`, que es pura.
"""

from __future__ import annotations

import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Role, Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.sync.changeseq import SYNCED_MODELS
from visitacampo.sync.cursor import CURSOR_ZERO, decode_cursor, encode_cursor
from visitacampo.sync.pull import _ENTITY_MODELS
from visitacampo.sync.records import RECORD_BUILDERS
from visitacampo.sync.retention import purge_expired_tombstones

from .factories import (
    TEMPLATE_CHANGE_SEQ,
    bootstrap,
    build_scenario,
    create_op,
    delete_op,
    pull,
    push,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: `build_scenario()` (Paso 8) siempre crea una `FormTemplateVersion` con
#: `change_seq=TEMPLATE_CHANGE_SEQ` — a propósito, para que el asignador de
#: `change_seq` del push se pruebe contra una organización que ya tiene
#: entidades no-`visit` (ver el docstring de la constante en `factories.py`).
#: Un pull real desde `CURSOR_ZERO` la incluiría con toda razón (es una
#: entidad sincronizable más, con `change_seq > 0`); los tests de este
#: archivo que solo quieren ver "lo que se acaba de pushear" arrancan
#: entonces desde este cursor —el estado justo después del `setUp`—, no
#: desde cero. `CursorExpiredTests`/`AuthorizationTests` siguen usando
#: `CURSOR_ZERO` a propósito: no les importa el contenido exacto de `rows`.
BASELINE_CURSOR = encode_cursor(TEMPLATE_CHANGE_SEQ)


class EntityCoverageTests(SimpleTestCase):
    """`_ENTITY_MODELS`, `SYNCED_MODELS` y `RECORD_BUILDERS` nombran lo mismo.

    Sin esta comprobación, una entidad nueva podría añadirse a una de las tres
    listas y quedar fuera de las otras dos sin que ningún test lo note —
    exactamente el defecto que rompería la asignación de `change_seq` o la
    serialización de una sola entidad, en silencio.
    """

    def test_the_three_lists_name_the_same_entities(self) -> None:
        from_entity_models = {entity for entity, _ in _ENTITY_MODELS}
        from_record_builders = set(RECORD_BUILDERS)
        from_synced_models = {model._meta.db_table for model in SYNCED_MODELS}
        model_tables = {model._meta.db_table for _, model in _ENTITY_MODELS}

        self.assertEqual(from_entity_models, from_record_builders)
        self.assertEqual(model_tables, from_synced_models)


def _ctx_for(scenario) -> Any:
    """Un `RequestContext` real (roles resueltos de `Membership`) para el escenario."""
    return resolve_request_context(
        Principal(user_id=scenario.user.pk, org_id=scenario.org.pk, device_id=scenario.device_id)
    )


@skip_without_db
class PullOrderTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_rows_come_back_in_the_order_they_were_created(self) -> None:
        visit_ids = [uuid.uuid4() for _ in range(3)]
        response = push(self.scenario, [create_op(self.scenario, visit_id=v) for v in visit_ids])
        self.assertEqual(response.status_code, 200, response.content)

        page = pull(self.scenario, BASELINE_CURSOR)
        self.assertEqual(page.status_code, 200, page.content)
        body = page.json()

        self.assertEqual(
            set(body),
            {"schema_version", "server_time", "rows", "tombstones", "next_cursor", "has_more"},
        )
        self.assertEqual(body["tombstones"], [])
        self.assertEqual([row["data"]["id"] for row in body["rows"]], [str(v) for v in visit_ids])
        seqs = [row["data"]["change_seq"] for row in body["rows"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)), "change_seq repetido")
        self.assertFalse(body["has_more"])
        self.assertEqual(decode_cursor(body["next_cursor"]), seqs[-1])

    def test_every_row_matches_the_visit_record_shape_of_a_push_conflict(self) -> None:
        """El pull y un `conflict` de push emiten la misma forma para `visit` (`records.py`)."""
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id, lat=4.6, lon=-74.1)])

        body = pull(self.scenario, BASELINE_CURSOR).json()
        row = body["rows"][0]
        self.assertEqual(row["entity"], "visit")
        data = row["data"]
        self.assertEqual(data["lat"], 4.6)
        self.assertEqual(data["lon"], -74.1)
        self.assertEqual(data["org_id"], str(self.scenario.org.pk))
        self.assertIsNone(data["deleted_at"])


@skip_without_db
class TombstoneTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_a_deleted_visit_appears_as_a_tombstone_not_a_row(self) -> None:
        visit_id = uuid.uuid4()
        response = push(
            self.scenario,
            [create_op(self.scenario, visit_id=visit_id), delete_op(visit_id, base_version=1)],
        )
        self.assertEqual(response.status_code, 200, response.content)
        statuses = [r["status"] for r in response.json()["results"]]
        self.assertEqual(statuses, ["applied", "applied"])

        body = pull(self.scenario, BASELINE_CURSOR).json()
        self.assertEqual(body["rows"], [])
        self.assertEqual(len(body["tombstones"]), 1)
        tombstone = body["tombstones"][0]
        self.assertEqual(tombstone["entity"], "visit")
        self.assertEqual(tombstone["id"], str(visit_id))
        self.assertIsNotNone(tombstone["deleted_at"])

    def test_a_tombstone_never_appears_in_a_bootstrap(self) -> None:
        """El bootstrap solo entrega estado vivo (doc 08 §6)."""
        visit_id = uuid.uuid4()
        push(
            self.scenario,
            [create_op(self.scenario, visit_id=visit_id), delete_op(visit_id, base_version=1)],
        )
        body = bootstrap(self.scenario).json()
        self.assertEqual(body["visits"], [])


@skip_without_db
class CursorExpiredTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()
        self.ctx = _ctx_for(self.scenario)

    def test_a_cursor_older_than_a_purge_is_rejected_with_409(self) -> None:
        visit_id = uuid.uuid4()
        push(
            self.scenario,
            [create_op(self.scenario, visit_id=visit_id), delete_op(visit_id, base_version=1)],
        )
        # Simula que el tombstone ya superó la ventana de retención de 90 días.
        Visit.objects.filter(pk=visit_id).update(deleted_at=timezone.now() - timedelta(days=91))
        purged = purge_expired_tombstones(self.ctx, cutoff=timezone.now() - timedelta(days=90))
        self.assertEqual(purged, 1)

        response = pull(self.scenario, CURSOR_ZERO)
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        self.assertEqual(body["code"], "cursor_expired")

    def test_a_cursor_at_or_after_the_purge_floor_still_works(self) -> None:
        """El piso no rechaza de más: solo los cursores estrictamente anteriores."""
        visit_id = uuid.uuid4()
        push(
            self.scenario,
            [create_op(self.scenario, visit_id=visit_id), delete_op(visit_id, base_version=1)],
        )
        purged_change_seq = Visit.objects.get(pk=visit_id).change_seq
        Visit.objects.filter(pk=visit_id).update(deleted_at=timezone.now() - timedelta(days=91))
        purge_expired_tombstones(self.ctx, cutoff=timezone.now() - timedelta(days=90))

        response = pull(self.scenario, encode_cursor(purged_change_seq))
        self.assertEqual(response.status_code, 200, response.content)

    def test_bootstrap_after_a_cursor_expired_gives_a_full_state_and_a_usable_cursor(self) -> None:
        visit_id = uuid.uuid4()
        push(
            self.scenario,
            [create_op(self.scenario, visit_id=visit_id), delete_op(visit_id, base_version=1)],
        )
        Visit.objects.filter(pk=visit_id).update(deleted_at=timezone.now() - timedelta(days=91))
        purge_expired_tombstones(self.ctx, cutoff=timezone.now() - timedelta(days=90))
        self.assertEqual(pull(self.scenario, CURSOR_ZERO).status_code, 409)

        boot = bootstrap(self.scenario)
        self.assertEqual(boot.status_code, 200, boot.content)
        cursor = boot.json()["cursor"]

        response = pull(self.scenario, cursor)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["rows"], [])


@skip_without_db
class PaginationTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_limit_splits_the_result_into_pages_without_losing_or_repeating_rows(self) -> None:
        visit_ids = [uuid.uuid4() for _ in range(3)]
        push(self.scenario, [create_op(self.scenario, visit_id=v) for v in visit_ids])

        first = pull(self.scenario, BASELINE_CURSOR, limit=2).json()
        self.assertEqual(len(first["rows"]), 2)
        self.assertTrue(first["has_more"])

        second = pull(self.scenario, first["next_cursor"], limit=2).json()
        self.assertEqual(len(second["rows"]), 1)
        self.assertFalse(second["has_more"])

        seen_ids = [row["data"]["id"] for row in first["rows"] + second["rows"]]
        self.assertEqual(seen_ids, [str(v) for v in visit_ids])

    def test_an_out_of_range_limit_is_a_422(self) -> None:
        response = pull(self.scenario, CURSOR_ZERO, limit=1001)
        self.assertEqual(response.status_code, 422, response.content)

    def test_a_malformed_cursor_is_a_400(self) -> None:
        response = pull(self.scenario, "not-a-real-cursor")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "validation_error")


@skip_without_db
class AuthorizationTests(TestCase):
    def test_a_viewer_can_still_pull_and_bootstrap(self) -> None:
        """`can_read_visit` admite cualquier rol miembro, incluido `VIEWER`."""
        scenario = build_scenario(role=Role.VIEWER)
        self.assertEqual(pull(scenario, CURSOR_ZERO).status_code, 200)
        self.assertEqual(bootstrap(scenario).status_code, 200)


def _run_isolated(work: Any) -> Any:
    try:
        return work()
    finally:
        connections.close_all()


@skip_without_db
class PullConcurrencyTests(TransactionTestCase):
    """Dos pushes simultáneos, seguidos de un pull: ninguna fila se pierde.

    Contraparte de lectura de `test_concurrency.py`: aquella suite comprueba
    que el `advisory lock` sirve `change_seq` sin huecos ni repeticiones bajo
    escritura concurrente; esta comprueba que el **pull** —que mezcla varias
    tablas por `change_seq`— entrega exactamente ese mismo conjunto, en el
    mismo orden, sin saltarse ninguna.
    """

    OPS_PER_BATCH = 20

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_pull_sees_every_row_from_both_batches_in_order(self) -> None:
        first = [create_op(self.scenario) for _ in range(self.OPS_PER_BATCH)]
        second = [create_op(self.scenario) for _ in range(self.OPS_PER_BATCH)]
        barrier = threading.Barrier(2)

        def work(ops: list[dict]) -> Any:
            def run() -> Any:
                barrier.wait(timeout=30)
                return push(self.scenario, ops)

            return _run_isolated(run)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(work, first), pool.submit(work, second)]
            responses = [future.result(timeout=60) for future in futures]
        for response in responses:
            self.assertEqual(response.status_code, 200, response.content)

        total = 2 * self.OPS_PER_BATCH
        body = pull(self.scenario, BASELINE_CURSOR, limit=total).json()
        self.assertEqual(len(body["rows"]), total)
        seqs = [row["data"]["change_seq"] for row in body["rows"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), total, "el pull perdió o repitió una fila")
        self.assertFalse(body["has_more"])

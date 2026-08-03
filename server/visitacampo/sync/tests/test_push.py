"""`POST /v1/sync/push` de extremo a extremo — los cinco casos del brief.

(a) operación nueva con `base_version` correcto → `applied`
(b) reenvío del mismo `op_uuid` → resultado guardado, **sin reaplicar**
(c) `base_version` desactualizado → `conflict` con `server_row` completo
(d) payload que no valida → `rejected`, nunca un 500
(e) lote de 501 operaciones → `413 payload_too_large`

**Requieren PostgreSQL** y por eso van marcados `skip_without_db`, igual que la
mitad de integración de los Pasos 5-7: `pg_advisory_xact_lock`,
`ON CONFLICT ... RETURNING` y `UPDATE ... RETURNING` no existen en SQLite, así
que no hay "aproximación local" honesta que ejecutar. La parte que sí se pudo
escribir como función pura se ejecuta siempre (`test_payloads.py`,
`test_sql.py`, `test_changeseq.py`, `test_limits.py`).
"""

from __future__ import annotations

import re
import unittest
import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from visitacampo.core.models import INITIAL_VERSION, Role, SyncOpLog, Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.serverinfo import capabilities as caps

from .factories import (
    TEMPLATE_CHANGE_SEQ,
    build_scenario,
    create_op,
    delete_op,
    push,
    update_op,
    visit_of,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


#: `INSERT INTO "visit"` / `UPDATE "visit"`: la tabla en posición de tabla,
#: no en cualquier otra parte de la sentencia.
_VISIT_TABLE_WRITE = re.compile(r'(?:INSERT INTO|UPDATE)\s+"visit"\b')


def visit_writes(captured: CaptureQueriesContext) -> list[str]:
    """Sentencias que **escriben** en la tabla `visit`. El "contador de escrituras" del brief.

    Ancla la búsqueda a `INSERT INTO "visit"`/`UPDATE "visit"` (la tabla,
    entrecomillada por Postgres, justo tras el verbo) y no a la subcadena
    `visit` en cualquier parte: un `INSERT INTO "sync_op_log"` para una
    operación rechazada guarda `result` como `jsonb`, y ese JSON serializado
    contiene literalmente `"entity": "visit"` — la subcadena `"visit"`
    (incluso ya entrecomillada) aparece ahí como **dato**, no como nombre de
    tabla, y un helper menos anclado la contaría por error.
    """
    return [
        query["sql"]
        for query in captured.captured_queries
        if _VISIT_TABLE_WRITE.search(query["sql"])
    ]


@skip_without_db
class AppliedTests(TestCase):
    """(a) Operación nueva con `base_version` correcto."""

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_a_new_create_is_applied(self) -> None:
        visit_id = uuid.uuid4()
        response = push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(set(body), {"schema_version", "server_time", "results"})
        self.assertEqual(body["schema_version"], caps.SCHEMA_VERSION)
        self.assertTrue(body["server_time"].endswith("Z"))

        result = body["results"][0]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entity"], "visit")
        self.assertEqual(result["entity_id"], str(visit_id))
        self.assertEqual(result["server_version"], INITIAL_VERSION)
        self.assertIsNone(result["error"])

    def test_the_row_lands_with_the_server_authoritative_columns(self) -> None:
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id, lat=4.6, lon=-74.1)])

        visit = visit_of(visit_id)
        self.assertEqual(visit.org_id, self.scenario.org.pk)
        self.assertEqual(visit.created_by_id, self.scenario.user.pk)
        self.assertEqual(visit.version, INITIAL_VERSION)
        self.assertIsNone(visit.deleted_at)
        self.assertEqual(visit.answers, {"altura_cm": 12})
        self.assertEqual(visit.lat, 4.6)

    def test_change_seq_continues_after_every_synced_entity(self) -> None:
        """El cursor es uno por organización, no uno por tabla (doc 08 §4.4)."""
        push(self.scenario, [create_op(self.scenario, visit_id=(first := uuid.uuid4()))])
        self.assertEqual(visit_of(first).change_seq, TEMPLATE_CHANGE_SEQ + 1)

    def test_each_applied_row_of_a_batch_gets_its_own_change_seq(self) -> None:
        """Empatar dos filas haría que una página del pull se saltara una."""
        ids = [uuid.uuid4() for _ in range(5)]
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id) for visit_id in ids])
        seqs = [visit_of(visit_id).change_seq for visit_id in ids]
        self.assertEqual(len(set(seqs)), 5)
        self.assertEqual(seqs, sorted(seqs))
        self.assertGreater(min(seqs), TEMPLATE_CHANGE_SEQ)

    def test_an_update_with_the_right_base_version_is_applied(self) -> None:
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        before = visit_of(visit_id)

        response = push(self.scenario, [update_op(visit_id, base_version=before.version)])
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["server_version"], before.version + 1)

        after = visit_of(visit_id)
        self.assertEqual(after.version, before.version + 1)
        self.assertGreater(after.change_seq, before.change_seq)
        self.assertEqual(after.lat, 4.61)
        # Un `update` parcial no borra lo que no envía.
        self.assertEqual(after.answers, {"altura_cm": 12})

    def test_a_delete_writes_a_tombstone_and_bumps_the_cursor(self) -> None:
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        before = visit_of(visit_id)

        response = push(self.scenario, [delete_op(visit_id, base_version=before.version)])
        self.assertEqual(response.json()["results"][0]["status"], "applied")

        after = visit_of(visit_id)
        self.assertIsNotNone(after.deleted_at)
        self.assertEqual(after.version, before.version + 1)
        self.assertGreater(after.change_seq, before.change_seq)

    def test_results_keep_the_order_of_the_ops(self) -> None:
        ops = [create_op(self.scenario) for _ in range(4)]
        results = push(self.scenario, ops).json()["results"]
        self.assertEqual(
            [result["op_uuid"] for result in results], [op["op_uuid"] for op in ops]
        )

    def test_every_operation_is_recorded_in_the_op_log(self) -> None:
        op = create_op(self.scenario)
        batch_uuid = uuid.uuid4()
        push(self.scenario, [op], batch_uuid=batch_uuid)

        row = SyncOpLog.objects.get(op_uuid=uuid.UUID(op["op_uuid"]))
        self.assertEqual(row.org_id, self.scenario.org.pk)
        self.assertEqual(row.device_id, self.scenario.device_id)
        self.assertEqual(row.batch_uuid, batch_uuid)
        self.assertEqual(row.entity, "visit")
        self.assertEqual(row.result["status"], "applied")


@skip_without_db
class IdempotencyTests(TestCase):
    """(b) Reenviar el mismo `op_uuid` devuelve lo guardado y **no reaplica**."""

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_a_resent_op_returns_the_saved_result_without_writing_again(self) -> None:
        visit_id = uuid.uuid4()
        op = create_op(self.scenario, visit_id=visit_id)

        first = push(self.scenario, [op]).json()["results"][0]
        after_first = visit_of(visit_id)

        with CaptureQueriesContext(connection) as captured:
            second = push(self.scenario, [op]).json()["results"][0]

        # El contador de escrituras que pide el brief: ni una sola sentencia
        # de escritura sobre `visit` en el reenvío.
        self.assertEqual(visit_writes(captured), [])
        self.assertEqual(second, first)

        after_second = visit_of(visit_id)
        self.assertEqual(after_second.version, after_first.version)
        self.assertEqual(after_second.change_seq, after_first.change_seq)
        self.assertEqual(SyncOpLog.objects.filter(op_uuid=uuid.UUID(op["op_uuid"])).count(), 1)

    def test_a_resent_update_does_not_bump_the_version_twice(self) -> None:
        """El caso que de verdad duele: una escritura idempotente re-aplicada."""
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        op = update_op(visit_id, base_version=visit_of(visit_id).version)

        push(self.scenario, [op])
        expected = visit_of(visit_id).version

        push(self.scenario, [op])
        self.assertEqual(visit_of(visit_id).version, expected)

    def test_a_duplicated_op_inside_one_batch_is_applied_once(self) -> None:
        visit_id = uuid.uuid4()
        op = create_op(self.scenario, visit_id=visit_id)
        results = push(self.scenario, [op, op]).json()["results"]

        self.assertEqual([result["status"] for result in results], ["applied", "applied"])
        self.assertEqual(results[0], results[1])
        self.assertEqual(visit_of(visit_id).version, INITIAL_VERSION)

    def test_the_batch_uuid_is_not_the_idempotency_key(self) -> None:
        """ADR-003: la unidad de idempotencia es la operación, nunca el lote."""
        batch_uuid = uuid.uuid4()
        first = create_op(self.scenario)
        second = create_op(self.scenario)

        push(self.scenario, [first], batch_uuid=batch_uuid)
        results = push(self.scenario, [second], batch_uuid=batch_uuid).json()["results"]
        self.assertEqual(results[0]["status"], "applied")

    def test_a_resent_rejection_is_also_idempotent(self) -> None:
        """`rejected` tampoco se descarta en silencio al reenviarse (doc 08 §4.3)."""
        op = create_op(self.scenario)
        op["payload"]["answers"] = "no-es-un-objeto"

        first = push(self.scenario, [op]).json()["results"][0]
        second = push(self.scenario, [op]).json()["results"][0]
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(second, first)


@skip_without_db
class ConflictTests(TestCase):
    """(c) `base_version` desactualizado → `conflict` con la fila autoritativa."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        self.visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=self.visit_id)])
        # Alguien más ya modificó la fila: la versión del cliente queda vieja.
        push(self.scenario, [update_op(self.visit_id, base_version=INITIAL_VERSION)])

    def test_a_stale_base_version_conflicts(self) -> None:
        response = push(self.scenario, [update_op(self.visit_id, base_version=INITIAL_VERSION)])
        self.assertEqual(response.status_code, 200, response.content)

        result = response.json()["results"][0]
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["error"]["code"], "conflict")
        self.assertEqual(result["server_version"], visit_of(self.visit_id).version)

    def test_the_conflict_carries_the_full_authoritative_row(self) -> None:
        result = push(
            self.scenario, [update_op(self.visit_id, base_version=INITIAL_VERSION)]
        ).json()["results"][0]

        self.assertEqual(result["server_row"]["entity"], "visit")
        data = result["server_row"]["data"]
        self.assertEqual(
            set(data),
            {
                "id",
                "org_id",
                "version",
                "change_seq",
                "deleted_at",
                "assignment_id",
                "template_version_id",
                "answers",
                "lat",
                "lon",
                "accuracy_m",
                "altitude_m",
                "captured_at",
                "device_id",
                "created_by",
            },
        )
        visit = visit_of(self.visit_id)
        self.assertEqual(data["id"], str(visit.pk))
        self.assertEqual(data["version"], visit.version)
        self.assertEqual(data["change_seq"], visit.change_seq)
        self.assertEqual(data["org_id"], str(self.scenario.org.pk))
        self.assertEqual(data["created_by"], str(self.scenario.user.pk))
        self.assertTrue(data["captured_at"].endswith("Z"))

    def test_a_conflict_leaves_the_row_untouched(self) -> None:
        before = visit_of(self.visit_id)
        push(self.scenario, [update_op(self.visit_id, base_version=INITIAL_VERSION)])
        after = visit_of(self.visit_id)
        self.assertEqual((after.version, after.change_seq), (before.version, before.change_seq))

    def test_a_create_over_an_existing_id_conflicts(self) -> None:
        """Un alta sobre un `id` que ya existe es un fallo de CAS, no un 500."""
        result = push(
            self.scenario, [create_op(self.scenario, visit_id=self.visit_id)]
        ).json()["results"][0]
        self.assertEqual(result["status"], "conflict")
        self.assertIsNotNone(result["server_row"])

    def test_a_conflict_does_not_abort_the_rest_of_the_batch(self) -> None:
        fresh = uuid.uuid4()
        results = push(
            self.scenario,
            [
                update_op(self.visit_id, base_version=INITIAL_VERSION),
                create_op(self.scenario, visit_id=fresh),
            ],
        ).json()["results"]
        self.assertEqual([result["status"] for result in results], ["conflict", "applied"])
        self.assertTrue(Visit.objects.filter(pk=fresh).exists())


@skip_without_db
class RejectionTests(TestCase):
    """(d) Payload que no valida → `rejected` estructurado, **nunca un 500**."""

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def _reject(self, op: dict) -> dict:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "rejected", result)
        self.assertIsNotNone(result["error"])
        self.assertTrue(result["error"]["code"])
        return result

    def test_a_missing_mandatory_field_is_rejected(self) -> None:
        op = create_op(self.scenario)
        del op["payload"]["template_version_id"]
        result = self._reject(op)
        self.assertEqual(result["error"]["code"], "validation_error")

    def test_an_unknown_field_is_rejected_not_ignored(self) -> None:
        op = create_op(self.scenario, org_id=str(uuid.uuid4()))
        self._reject(op)

    def test_an_out_of_range_coordinate_is_rejected(self) -> None:
        self._reject(create_op(self.scenario, lat=91.0))

    def test_a_payload_that_is_not_an_object_is_rejected(self) -> None:
        op = create_op(self.scenario)
        op["payload"] = "no-soy-un-objeto"
        self._reject(op)

    def test_an_unknown_op_type_is_rejected(self) -> None:
        op = create_op(self.scenario)
        op["op_type"] = "truncate"
        self._reject(op)

    def test_an_unsupported_entity_is_rejected(self) -> None:
        op = create_op(self.scenario)
        op["entity"] = "visit_attachment"
        self._reject(op)

    def test_a_reference_to_another_organization_is_rejected(self) -> None:
        """RD-11: las FK son globales; el aislamiento lo pone la aplicación."""
        other = build_scenario(username="ajeno")
        op = create_op(self.scenario, template_version_id=str(other.template_version.pk))
        result = self._reject(op)
        self.assertEqual(result["error"]["code"], "not_found")
        self.assertEqual(result["error"]["details"]["field"], "template_version_id")

    def test_an_update_on_a_row_that_does_not_exist_is_rejected(self) -> None:
        result = self._reject(update_op(uuid.uuid4(), base_version=1))
        self.assertEqual(result["error"]["code"], "not_found")

    def test_nulling_a_not_null_column_is_rejected_not_a_500(self) -> None:
        """El `NotNullViolation` que se llevaría el lote entero por delante.

        `SET template_version_id = NULL` es un error de PostgreSQL, no un
        conflicto: dentro de la transacción única del lote abortaría **todas**
        las operaciones con un `500`. Se ataja en `payloads.py`.
        """
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        base = visit_of(visit_id).version

        result = self._reject(
            update_op(visit_id, base_version=base, template_version_id=None)
        )
        self.assertEqual(result["error"]["code"], "validation_error")
        # La fila no se tocó y el lote siguió vivo.
        self.assertEqual(visit_of(visit_id).version, base)

    def test_an_answers_value_that_jsonb_cannot_store_is_rejected_not_a_500(self) -> None:
        """`NaN` y `U+0000` son JSON para `json.loads` y `DataError` para `jsonb`."""
        for answers in ({"a": float("nan")}, {"a": "antes\x00despues"}):
            with self.subTest(answers=answers):
                self._reject(create_op(self.scenario, answers=answers))

    def test_a_poisoned_op_does_not_take_the_rest_of_the_batch_down(self) -> None:
        """Lo que de verdad se protege: las otras 499 operaciones del lote."""
        good = uuid.uuid4()
        results = push(
            self.scenario,
            [
                create_op(self.scenario, answers={"a": "\x00"}),
                create_op(self.scenario, visit_id=good),
            ],
        ).json()["results"]
        self.assertEqual([result["status"] for result in results], ["rejected", "applied"])
        self.assertTrue(Visit.objects.filter(pk=good).exists())

    def test_a_rejected_op_writes_nothing(self) -> None:
        visit_id = uuid.uuid4()
        op = create_op(self.scenario, visit_id=visit_id, lat=91.0)
        with CaptureQueriesContext(connection) as captured:
            push(self.scenario, [op])
        self.assertEqual(visit_writes(captured), [])
        self.assertFalse(Visit.objects.filter(pk=visit_id).exists())

    def test_a_rejected_op_does_not_abort_the_rest_of_the_batch(self) -> None:
        good = uuid.uuid4()
        results = push(
            self.scenario,
            [create_op(self.scenario, lat=91.0), create_op(self.scenario, visit_id=good)],
        ).json()["results"]
        self.assertEqual([result["status"] for result in results], ["rejected", "applied"])
        self.assertTrue(Visit.objects.filter(pk=good).exists())

    def test_the_error_details_never_echo_the_offending_value(self) -> None:
        """RD-12: el cuerpo de error no devuelve el dato que falló."""
        secreto = "dato-sensible-que-no-debe-volver"
        op = create_op(self.scenario, answers=secreto)
        response = push(self.scenario, [op])
        self.assertNotIn(secreto, response.content.decode())


@skip_without_db
class BatchLimitTests(TestCase):
    """(e) Lote de 501 operaciones → `413 payload_too_large`."""

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_a_batch_over_the_limit_is_413(self) -> None:
        ops = [create_op(self.scenario) for _ in range(caps.SYNC_PUSH_MAX_OPS + 1)]
        response = push(self.scenario, ops)
        self.assertEqual(response.status_code, 413, response.content)

        body = response.json()
        self.assertEqual(body["code"], "payload_too_large")
        self.assertEqual(Visit.objects.count(), 0, "un lote rechazado no escribe nada")

    def test_a_batch_exactly_at_the_limit_is_accepted(self) -> None:
        """El límite es inclusivo: 500 operaciones son legales."""
        ops = [create_op(self.scenario) for _ in range(caps.SYNC_PUSH_MAX_OPS)]
        response = push(self.scenario, ops)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Visit.objects.count(), caps.SYNC_PUSH_MAX_OPS)

    def test_an_empty_batch_is_422(self) -> None:
        response = push(self.scenario, [])
        self.assertEqual(response.status_code, 422, response.content)


@skip_without_db
class BatchGateTests(TestCase):
    """Lo que se decide **antes** del bucle: versión de esquema, identidad, permisos."""

    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_an_unsupported_schema_version_is_422(self) -> None:
        response = push(self.scenario, [create_op(self.scenario)], schema_version=99)
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()["code"], "schema_unsupported")

    def test_a_batch_from_another_device_is_403(self) -> None:
        response = push(self.scenario, [create_op(self.scenario)], device_id=uuid.uuid4())
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json()["code"], "forbidden")

    def test_a_viewer_cannot_push(self) -> None:
        viewer = build_scenario(role=Role.VIEWER, username="observador")
        response = push(viewer, [create_op(viewer)])
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(Visit.objects.count(), 0)

    def test_without_a_token_it_is_401(self) -> None:
        response = self.scenario.client.post(
            "/v1/sync/push", data="{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 401)


@skip_without_db
class TenantIsolationTests(TestCase):
    """RD-11: nada de una organización es alcanzable desde otra."""

    def setUp(self) -> None:
        self.mine = build_scenario(username="propio")
        self.theirs = build_scenario(username="ajeno")

    def test_a_create_over_an_id_owned_by_another_org_is_rejected_without_leaking(self) -> None:
        their_visit = uuid.uuid4()
        push(self.theirs, [create_op(self.theirs, visit_id=their_visit)])

        result = push(
            self.mine, [create_op(self.mine, visit_id=their_visit)]
        ).json()["results"][0]
        # `rejected` y no `conflict`: un `conflict` tendría que adjuntar la
        # fila autoritativa, es decir la fila de la otra organización.
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["server_row"])
        self.assertEqual(visit_of(their_visit).org_id, self.theirs.org.pk)

    def test_an_op_uuid_of_another_org_does_not_return_its_result(self) -> None:
        op = create_op(self.theirs)
        push(self.theirs, [op])

        mine = create_op(self.mine, op_uuid=uuid.UUID(op["op_uuid"]))
        result = push(self.mine, [mine]).json()["results"][0]
        # No se le devuelve el resultado guardado de la otra organización: se
        # procesa como una operación nueva y suya.
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["entity_id"], mine["payload"]["id"])

"""Presupuesto de queries de un push de 200 operaciones — **regresión de H-07**.

Doc 08 ADR-013 lo cita literalmente como "el test que la referencia no tenía".
El hallazgo H-07 (HIGH) de la auditoría es que QFieldCloud llamaba a
`permissions_utils.can_create_delta(user, delta)` **dentro** del bucle de
deltas, y cada llamada re-consultaba la vista SQL de roles: un lote de 500
deltas costaba 500 consultas de permisos. RD-07 exige resolver los roles una
vez, antes del bucle, y que las políticas sean evaluación en memoria.

Un test que solo contara "queries totales" no serviría: crecerían igualmente
con el tamaño del lote (una sentencia CAS por operación, que es el trabajo
real). Lo que aquí se fija es más fuerte y más específico:

1. **El coste marginal de una operación es exactamente una query.** Se mide un
   lote de 1 y otro de 200 y se comprueba que la diferencia son 199 queries:
   ni una sola consulta adicional se ha colado en el bucle.
2. **Las consultas de permisos son una, sea el lote de 1 o de 200** — la del
   `RequestContext`, que `RequestContextAuth` hace antes de la vista.
3. Ninguna consulta del bucle toca las tablas de identidad.

Requiere PostgreSQL (`skip_without_db`), como el resto de la integración.
"""

from __future__ import annotations

import unittest
import uuid

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from visitacampo.core.models import Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, push

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

#: Tamaño del lote que cita el brief.
BATCH = 200

#: Tablas cuya consulta es, por definición, una consulta de permisos/identidad.
PERMISSION_TABLES = ("membership", "app_user")


def queries_of(scenario, size: int) -> list[str]:
    ops = [create_op(scenario, visit_id=uuid.uuid4()) for _ in range(size)]
    with CaptureQueriesContext(connection) as captured:
        response = push(scenario, ops)
    assert response.status_code == 200, response.content
    return [query["sql"] for query in captured.captured_queries]


def count_touching(queries: list[str], table: str) -> int:
    return sum(1 for sql in queries if f'"{table}"' in sql)


@skip_without_db
class QueryBudgetTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def test_the_marginal_cost_of_an_operation_is_one_query(self) -> None:
        one = queries_of(self.scenario, 1)
        many = queries_of(self.scenario, BATCH)
        # `assertNumQueries` con el presupuesto **derivado** del lote de 1, en
        # vez de un literal: el número exacto de queries fijas depende de
        # detalles de Django (savepoints de `TestCase`) que no son lo que este
        # test protege, y clavarlos a mano lo haría frágil sin hacerlo más
        # estricto. Lo que se fija es la pendiente, que es lo que H-07 rompía.
        self.assertEqual(
            len(many),
            len(one) + (BATCH - 1),
            "Una operación adicional debe costar exactamente una query "
            f"(lote de 1: {len(one)}; lote de {BATCH}: {len(many)}).",
        )

    def test_the_batch_of_200_matches_its_derived_budget(self) -> None:
        """La forma `assertNumQueries` que pide el brief, con el mismo presupuesto."""
        baseline = len(queries_of(self.scenario, 1))
        ops = [create_op(self.scenario, visit_id=uuid.uuid4()) for _ in range(BATCH)]
        with self.assertNumQueries(baseline + BATCH - 1):
            push(self.scenario, ops)

    def test_permission_queries_are_constant(self) -> None:
        """Una consulta de roles, la del `RequestContext`, cueste el lote 1 o 200."""
        for size in (1, BATCH):
            with self.subTest(size=size):
                queries = queries_of(self.scenario, size)
                self.assertEqual(count_touching(queries, "membership"), 1)

    def test_no_identity_table_is_queried_more_than_once(self) -> None:
        queries = queries_of(self.scenario, BATCH)
        for table in PERMISSION_TABLES:
            with self.subTest(table=table):
                self.assertLessEqual(count_touching(queries, table), 1)

    def test_reference_resolution_does_not_grow_with_the_batch(self) -> None:
        """Las referencias del lote se resuelven en bloque, no una por operación."""
        one = queries_of(self.scenario, 1)
        many = queries_of(self.scenario, BATCH)
        for table in ("form_template_version", "assignment", "device"):
            with self.subTest(table=table):
                self.assertEqual(
                    count_touching(many, table),
                    count_touching(one, table),
                )

    def test_the_op_log_is_read_once_and_written_once(self) -> None:
        queries = queries_of(self.scenario, BATCH)
        reads = [sql for sql in queries if "sync_op_log" in sql and sql.startswith("SELECT")]
        writes = [sql for sql in queries if "sync_op_log" in sql and "INSERT" in sql]
        self.assertEqual(len(reads), 1, "la idempotencia se comprueba en una sola consulta")
        self.assertEqual(len(writes), 1, "el log del lote se escribe en un solo INSERT")

    def test_the_advisory_lock_is_taken_once_per_request(self) -> None:
        queries = queries_of(self.scenario, BATCH)
        locks = [sql for sql in queries if "pg_advisory_xact_lock" in sql]
        self.assertEqual(len(locks), 1)

    def test_the_lock_is_taken_before_any_write(self) -> None:
        """Si el lock llegara después del primer UPSERT no serializaría nada."""
        queries = queries_of(self.scenario, BATCH)
        lock_at = next(i for i, sql in enumerate(queries) if "pg_advisory_xact_lock" in sql)
        first_write = next(
            i for i, sql in enumerate(queries) if sql.startswith("INSERT INTO") and "visit" in sql
        )
        self.assertLess(lock_at, first_write)

    def test_all_the_rows_land(self) -> None:
        """Un presupuesto de queries que se cumple sin escribir nada no valdría."""
        queries_of(self.scenario, BATCH)
        self.assertEqual(Visit.objects.count(), BATCH)

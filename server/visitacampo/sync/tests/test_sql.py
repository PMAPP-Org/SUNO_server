"""Las sentencias CAS, verificadas sobre su **texto**. Sin base de datos.

Que estas comprobaciones sean textuales no las hace cosméticas: lo que
verifican son invariantes que, de romperse, no producen ningún error visible.
Un `UPDATE` al que se le caiga el `org_id = %s` sigue funcionando —y sigue
pasando todos los tests funcionales, porque los datos "correctos" también se
escriben— mientras abre una fuga entre organizaciones (RD-11). Un `UPDATE` al
que se le caiga el `version = %s` sigue funcionando y borra en silencio el
trabajo de otro agente (el lost-update de H-08). Un `RETURNING` que
desaparezca convierte todo conflicto en un `applied` falso.

Son exactamente las tres cosas que este archivo fija.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from django.test import SimpleTestCase

from visitacampo.core.models import INITIAL_VERSION
from visitacampo.sync import sql

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
VISIT = uuid.UUID("22222222-2222-2222-2222-222222222222")
USER = uuid.UUID("33333333-3333-3333-3333-333333333333")
TEMPLATE = uuid.UUID("44444444-4444-4444-4444-444444444444")
DEVICE = uuid.UUID("55555555-5555-5555-5555-555555555555")

CAPTURED_AT = datetime(2026, 7, 30, 14, 5, 9, tzinfo=UTC)


def create_fields() -> dict[str, object]:
    return {
        "assignment_id": None,
        "template_version_id": TEMPLATE,
        "answers": {"altura_cm": 12},
        "lat": None,
        "lon": None,
        "accuracy_m": None,
        "altitude_m": None,
        "captured_at": CAPTURED_AT,
        "device_id": DEVICE,
    }


class ColumnMappingTests(SimpleTestCase):
    """Los nombres de columna salen del modelo, no de una constante a mano."""

    def test_maps_the_contract_names_to_the_orm_columns(self) -> None:
        self.assertEqual(sql.column("created_by"), '"created_by_id"')
        self.assertEqual(sql.column("device_id"), '"device_id"')
        self.assertEqual(sql.column("template_version_id"), '"template_version_id"')
        self.assertEqual(sql.column("org_id"), '"org_id"')
        self.assertEqual(sql.table(), '"visit"')


class InsertTests(SimpleTestCase):
    def setUp(self) -> None:
        self.statement = sql.build_insert(
            org_id=ORG,
            visit_id=VISIT,
            change_seq=42,
            created_by=USER,
            fields=create_fields(),
        )

    def test_is_an_upsert_guarded_by_the_primary_key(self) -> None:
        self.assertIn('INSERT INTO "visit"', self.statement.sql)
        self.assertIn('ON CONFLICT ("id") DO NOTHING', self.statement.sql)

    def test_returns_the_applied_version_and_change_seq(self) -> None:
        """Sin `RETURNING` no habría forma de distinguir `applied` de `conflict`."""
        self.assertTrue(self.statement.sql.endswith('RETURNING "version", "change_seq"'))

    def test_the_server_authoritative_columns_are_written_by_the_server(self) -> None:
        params = self.statement.params
        self.assertIn(ORG, params)
        self.assertIn(USER, params)
        self.assertIn(INITIAL_VERSION, params)
        self.assertIn(42, params)

    def test_answers_is_serialized_and_cast_to_jsonb(self) -> None:
        self.assertIn("%s::jsonb", self.statement.sql)
        self.assertIn(json.dumps({"altura_cm": 12}), self.statement.params)

    def test_no_value_is_interpolated_into_the_text(self) -> None:
        for value in (str(ORG), str(VISIT), str(USER), "altura_cm"):
            with self.subTest(value=value):
                self.assertNotIn(value, self.statement.sql)

    def test_every_column_has_exactly_one_placeholder(self) -> None:
        columns = self.statement.sql.split("(", 1)[1].split(")", 1)[0].count(",") + 1
        self.assertEqual(columns, len(self.statement.params))


class UpdateTests(SimpleTestCase):
    def test_carries_the_cas_guard_and_the_org_isolation(self) -> None:
        statement = sql.build_update(
            org_id=ORG,
            visit_id=VISIT,
            base_version=3,
            change_seq=99,
            fields={"lat": 4.6},
        )
        self.assertIn(
            'WHERE "id" = %s AND "org_id" = %s AND "version" = %s',
            statement.sql,
        )
        # …y en ese orden en los parámetros.
        self.assertEqual(statement.params[-3:], (VISIT, ORG, 3))

    def test_increments_the_version_in_the_database(self) -> None:
        """`version + 1` calculado en SQL: no depende de nada leído antes."""
        statement = sql.build_update(
            org_id=ORG, visit_id=VISIT, base_version=3, change_seq=99, fields={"lat": 4.6}
        )
        self.assertIn('"version" = "version" + 1', statement.sql)

    def test_assigns_the_new_change_seq(self) -> None:
        statement = sql.build_update(
            org_id=ORG, visit_id=VISIT, base_version=3, change_seq=99, fields={"lat": 4.6}
        )
        self.assertIn('"change_seq" = %s', statement.sql)
        self.assertIn(99, statement.params)

    def test_returns_the_resulting_row_version(self) -> None:
        statement = sql.build_update(
            org_id=ORG, visit_id=VISIT, base_version=3, change_seq=99, fields={"lat": 4.6}
        )
        self.assertTrue(statement.sql.endswith('RETURNING "version", "change_seq"'))

    def test_only_the_fields_sent_are_written(self) -> None:
        statement = sql.build_update(
            org_id=ORG, visit_id=VISIT, base_version=3, change_seq=99, fields={"lat": 4.6}
        )
        for untouched in ("template_version_id", "captured_at", "answers", "device_id"):
            with self.subTest(column=untouched):
                self.assertNotIn(f'"{untouched}" =', statement.sql)

    def test_a_delete_is_an_update_that_writes_the_tombstone(self) -> None:
        """Los borrados son lógicos y viajan por el pull (doc 08 §4.4)."""
        deleted_at = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
        statement = sql.build_update(
            org_id=ORG,
            visit_id=VISIT,
            base_version=3,
            change_seq=99,
            fields={},
            deleted_at=deleted_at,
        )
        self.assertIn('"deleted_at" = %s', statement.sql)
        self.assertIn(deleted_at, statement.params)
        self.assertIn('"version" = "version" + 1', statement.sql)
        self.assertIn('AND "version" = %s', statement.sql)

    def test_a_delete_never_writes_client_data(self) -> None:
        statement = sql.build_update(
            org_id=ORG,
            visit_id=VISIT,
            base_version=3,
            change_seq=99,
            fields={},
            deleted_at=datetime(2026, 7, 31, 9, 0, tzinfo=UTC),
        )
        self.assertNotIn("::jsonb", statement.sql)

    def test_updates_answers_with_the_jsonb_cast(self) -> None:
        statement = sql.build_update(
            org_id=ORG,
            visit_id=VISIT,
            base_version=3,
            change_seq=99,
            fields={"answers": {"a": 1}},
        )
        self.assertIn('"answers" = %s::jsonb', statement.sql)
        self.assertIn(json.dumps({"a": 1}), statement.params)

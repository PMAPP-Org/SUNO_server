"""Alta de `visit_attachment` por el push (Paso 22).

Una respuesta de tipo foto/firma guarda el `id` de un adjunto; sin esta
escritura ese `id` no apuntaría a nada. Se fija aquí:

- la visita y sus adjuntos viajan en el **mismo lote** (visita primero) y se
  aplican juntos, aunque el blob todavía no esté subido (contrato);
- un adjunto que apunta a una visita inexistente, borrada o de otra
  organización se rechaza con `not_found`, **sin tumbar el lote**;
- el payload es sellado (sin claves extra, `sha256` en minúsculas, `role`
  cerrado, `order` entero estricto);
- reenviar el alta es idempotente, y un `id` repetido es `conflict` con la fila;
- `update`/`delete` de adjuntos se rechazan con detalle (fuera del MVP);
- el adjunto aparece en el pull.

Requiere PostgreSQL, como el resto de la integración de sync.
"""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from django.test import TestCase

from visitacampo.core.models import VisitAttachment
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, delete_op, pull, push

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

SHA = "ab" * 32


def attachment_op(
    visit_id: uuid.UUID,
    attachment_id: uuid.UUID | None = None,
    op_uuid: uuid.UUID | None = None,
    op_type: str = "create",
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(attachment_id or uuid.uuid4()),
        "visit_id": str(visit_id),
        "sha256": SHA,
        "role": "foto",
        "caption": None,
        "captured_at": "2026-10-17T10:00:00-05:00",
        "order": 0,
    }
    payload.update(overrides)
    return {
        "op_uuid": str(op_uuid or uuid.uuid4()),
        "entity": "visit_attachment",
        "op_type": op_type,
        "base_version": None if op_type == "create" else 1,
        "payload": payload,
    }


@skip_without_db
class AttachmentPushTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def _results(self, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = push(self.scenario, ops)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"]

    def test_visit_and_its_attachments_in_the_same_batch(self) -> None:
        visit_id = uuid.uuid4()
        photo, signature = uuid.uuid4(), uuid.uuid4()
        results = self._results(
            [
                create_op(self.scenario, visit_id=visit_id),
                attachment_op(visit_id, photo),
                attachment_op(visit_id, signature, role="firma", sha256="cd" * 32, order=1),
            ]
        )
        self.assertEqual([r["status"] for r in results], ["applied"] * 3)
        rows = VisitAttachment.objects.filter(visit_id=visit_id).order_by("order")
        self.assertEqual(
            [(r.pk, r.role, r.blob_id) for r in rows],
            [
                (photo, "foto", SHA),
                (signature, "firma", "cd" * 32),
            ],
        )
        self.assertEqual(rows[0].org_id, self.scenario.org.pk, "org_id lo pone el servidor")
        self.assertIsNotNone(rows[0].change_seq)

    def test_an_attachment_for_an_existing_visit_in_a_later_batch(self) -> None:
        visit_id = uuid.uuid4()
        self._results([create_op(self.scenario, visit_id=visit_id)])
        self.assertEqual(self._results([attachment_op(visit_id)])[0]["status"], "applied")

    def test_an_unknown_visit_is_rejected_without_taking_the_batch_down(self) -> None:
        visit_id = uuid.uuid4()
        results = self._results(
            [
                attachment_op(uuid.uuid4()),
                create_op(self.scenario, visit_id=visit_id),
                attachment_op(visit_id),
            ]
        )
        self.assertEqual([r["status"] for r in results], ["rejected", "applied", "applied"])
        self.assertEqual(results[0]["error"]["code"], "not_found")
        self.assertEqual(
            results[0]["error"]["details"], {"field": "visit_id", "reason": "unknown_reference"}
        )

    def test_an_attachment_sent_before_its_visit_is_rejected(self) -> None:
        """El orden importa: la app envía la visita antes que sus adjuntos."""
        visit_id = uuid.uuid4()
        results = self._results(
            [attachment_op(visit_id), create_op(self.scenario, visit_id=visit_id)]
        )
        self.assertEqual([r["status"] for r in results], ["rejected", "applied"])

    def test_a_deleted_visit_does_not_accept_attachments(self) -> None:
        visit_id = uuid.uuid4()
        self._results([create_op(self.scenario, visit_id=visit_id)])
        self._results([delete_op(visit_id, base_version=1)])
        self.assertEqual(self._results([attachment_op(visit_id)])[0]["status"], "rejected")

    def test_a_visit_of_another_organization_is_not_found(self) -> None:
        """RD-11: ni se escribe ni se confirma que la visita ajena exista."""
        other = build_scenario(username="ajeno")
        foreign_visit = uuid.uuid4()
        response = push(other, [create_op(other, visit_id=foreign_visit)])
        self.assertEqual(response.json()["results"][0]["status"], "applied")

        result = self._results([attachment_op(foreign_visit)])[0]
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "not_found")
        self.assertFalse(VisitAttachment.objects.filter(visit_id=foreign_visit).exists())

    def test_resending_the_same_op_is_idempotent(self) -> None:
        visit_id, attachment_id, op_uuid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self._results([create_op(self.scenario, visit_id=visit_id)])
        first = self._results([attachment_op(visit_id, attachment_id, op_uuid=op_uuid)])
        again = self._results([attachment_op(visit_id, attachment_id, op_uuid=op_uuid)])
        self.assertEqual(first, again)
        self.assertEqual(VisitAttachment.objects.filter(pk=attachment_id).count(), 1)

    def test_a_reused_id_is_a_conflict_with_the_server_row(self) -> None:
        visit_id, attachment_id = uuid.uuid4(), uuid.uuid4()
        self._results(
            [create_op(self.scenario, visit_id=visit_id), attachment_op(visit_id, attachment_id)]
        )
        result = self._results([attachment_op(visit_id, attachment_id, sha256="ef" * 32)])[0]
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["server_row"]["entity"], "visit_attachment")
        self.assertEqual(result["server_row"]["data"]["sha256"], SHA)

    def test_update_and_delete_are_not_supported_yet(self) -> None:
        visit_id = uuid.uuid4()
        self._results([create_op(self.scenario, visit_id=visit_id)])
        for op_type in ("update", "delete"):
            with self.subTest(op_type=op_type):
                result = self._results([attachment_op(visit_id, op_type=op_type)])[0]
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(
                    result["error"]["details"],
                    {"field": "op_type", "reason": "unsupported_op_type"},
                )

    def test_the_payload_is_sealed(self) -> None:
        visit_id = uuid.uuid4()
        self._results([create_op(self.scenario, visit_id=visit_id)])
        cases: dict[str, dict[str, Any]] = {
            "clave_extra": {"bucket_key": "../../etc/passwd"},
            "sha_en_mayusculas": {"sha256": "AB" * 32},
            "sha_corto": {"sha256": "ab"},
            "rol_desconocido": {"role": "script"},
            "orden_negativo": {"order": -1},
            "orden_booleano": {"order": True},
            "orden_gigante": {"order": 2**40},
            "fecha_sin_offset": {"captured_at": "2026-10-17T10:00:00"},
            "pie_con_nul": {"caption": "a\x00b"},
            "pie_demasiado_largo": {"caption": "x" * 513},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                result = self._results([attachment_op(visit_id, **overrides)])[0]
                self.assertEqual(result["status"], "rejected", result)
                self.assertEqual(result["error"]["code"], "validation_error")
        self.assertFalse(VisitAttachment.objects.filter(visit_id=visit_id).exists())

    def test_the_attachment_reaches_the_pull(self) -> None:
        visit_id, attachment_id = uuid.uuid4(), uuid.uuid4()
        self._results(
            [create_op(self.scenario, visit_id=visit_id), attachment_op(visit_id, attachment_id)]
        )
        response = pull(self.scenario, "c1:00000000000000000000")
        rows = [r for r in response.json()["rows"] if r["entity"] == "visit_attachment"]
        self.assertEqual([r["data"]["id"] for r in rows], [str(attachment_id)])
        self.assertEqual(rows[0]["data"]["visit_id"], str(visit_id))

"""Los límites de lote de ADR-003, y su coherencia con lo que se anuncia.

Sin base de datos: son propiedades del transporte y de la configuración.

El caso (e) del brief —lote de 501 operaciones ⇒ `413`— se prueba de extremo a
extremo en `test_push.py`; aquí se fija lo que ese test no puede ver: que el
número que el servidor aplica sea **el mismo** que anuncia en
`GET /v1/server/info` (si divergieran, el cliente partiría sus lotes con un
tamaño que el servidor rechaza), y que Django no corte el cuerpo antes con su
propio límite, que respondería un `400` en vez del `413` que el contrato
promete.
"""

from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase

from visitacampo.core.api_errors import CODE_BY_STATUS
from visitacampo.serverinfo import capabilities as caps
from visitacampo.sync.api import PayloadTooLarge, SchemaVersionUnsupported


class ContractLimitsTests(SimpleTestCase):
    def test_the_limits_are_the_ones_of_adr_003(self) -> None:
        self.assertEqual(caps.SYNC_PUSH_MAX_OPS, 500)
        self.assertEqual(caps.SYNC_PUSH_MAX_BYTES, 5 * 1024 * 1024)

    def test_what_the_server_announces_is_what_it_applies(self) -> None:
        announced = caps.capabilities()
        self.assertEqual(announced["sync_push_max_ops"], caps.SYNC_PUSH_MAX_OPS)
        self.assertEqual(announced["sync_push_max_bytes"], caps.SYNC_PUSH_MAX_BYTES)


class DjangoBodyLimitTests(SimpleTestCase):
    """Django no puede cortar por debajo del límite del contrato."""

    def test_data_upload_limit_is_not_below_the_contract_limit(self) -> None:
        self.assertGreaterEqual(
            settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
            caps.SYNC_PUSH_MAX_BYTES,
            "Con el valor por defecto de Django (2,5 MiB) un lote legítimo de "
            "3 MiB moriría con un 400 antes de llegar a la vista.",
        )

    def test_a_too_big_body_maps_to_the_contract_code(self) -> None:
        self.assertEqual(CODE_BY_STATUS[413], "payload_too_large")


class ErrorShapeTests(SimpleTestCase):
    """Los dos rechazos de lote llevan el `code` del contrato, no uno inventado."""

    def test_payload_too_large_carries_the_contract_code(self) -> None:
        error = PayloadTooLarge("demasiado")
        self.assertEqual(error.status_code, 413)
        self.assertEqual(error.code, "payload_too_large")

    def test_schema_unsupported_is_a_422_with_its_code(self) -> None:
        error = SchemaVersionUnsupported(99)
        self.assertEqual(error.status_code, 422)
        self.assertEqual(error.code, "schema_unsupported")
        self.assertIn("99", str(error))

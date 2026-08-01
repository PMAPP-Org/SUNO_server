"""Validación por operación (RD-03/RD-02). **Sin base de datos**: es puro.

Estos son los tests que sostienen el criterio de salida "`rejected` nunca es un
500 sin manejar": si `parse_op` puede lanzar ante *cualquier* entrada, el
endpoint puede devolver un 500 ante cualquier entrada. Por eso el caso (d) del
brief se prueba dos veces —aquí sobre la función pura y en `test_push.py` sobre
el endpoint completo— y aquí se prueba además con entradas que ni siquiera son
objetos JSON.
"""

from __future__ import annotations

import json
import uuid

from django.test import SimpleTestCase

from visitacampo.sync.payloads import Rejection, VisitWrite, parse_op, target_id


def _create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "template_version_id": str(uuid.uuid4()),
        "answers": {"altura_cm": 12},
        "captured_at": "2026-07-30T14:05:09.123Z",
        "device_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return payload


class CreateTests(SimpleTestCase):
    def test_accepts_the_full_contract_payload(self) -> None:
        payload = _create_payload(lat=4.6, lon=-74.1, accuracy_m=8.0, altitude_m=2600.0)
        parsed = parse_op("visit", "create", None, payload)
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.op_type, "create")
        self.assertIsNone(parsed.base_version)
        self.assertEqual(parsed.entity_id, uuid.UUID(str(payload["id"])))
        # El alta escribe el conjunto completo, con los ausentes en `None`.
        self.assertEqual(
            set(parsed.fields),
            {
                "assignment_id",
                "template_version_id",
                "answers",
                "lat",
                "lon",
                "accuracy_m",
                "altitude_m",
                "captured_at",
                "device_id",
            },
        )
        self.assertIsNone(parsed.fields["assignment_id"])

    def test_rejects_a_create_that_sends_base_version(self) -> None:
        parsed = parse_op("visit", "create", 3, _create_payload())
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.details, {"field": "base_version", "reason": "invalid_for_op_type"})

    def test_rejects_a_create_without_the_mandatory_set(self) -> None:
        payload = _create_payload()
        del payload["template_version_id"]
        parsed = parse_op("visit", "create", None, payload)
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.code, "validation_error")
        fields = [error["field"] for error in (parsed.details or {})["errors"]]
        self.assertIn("template_version_id", fields)

    def test_rejects_an_unknown_field_instead_of_ignoring_it(self) -> None:
        """Tipos **sellados** (RD-03): lo que el contrato no declara, no entra."""
        parsed = parse_op("visit", "create", None, _create_payload(org_id=str(uuid.uuid4())))
        assert isinstance(parsed, Rejection)
        fields = [error["field"] for error in (parsed.details or {})["errors"]]
        self.assertIn("org_id", fields)

    def test_rejects_a_naive_captured_at(self) -> None:
        """El contrato exige RFC 3339 con offset explícito."""
        payload = _create_payload(captured_at="2026-07-30T14:05:09")
        self.assertIsInstance(parse_op("visit", "create", None, payload), Rejection)

    def test_rejects_coordinates_out_of_range(self) -> None:
        parsed = parse_op("visit", "create", None, _create_payload(lat=91.0))
        assert isinstance(parsed, Rejection)
        fields = [error["field"] for error in (parsed.details or {})["errors"]]
        self.assertIn("lat", fields)

    def test_details_never_carry_the_offending_value(self) -> None:
        """RD-12: `field` y `reason`, nunca el dato que falló."""
        secreto = "valor-que-no-debe-volver-nunca"
        parsed = parse_op("visit", "create", None, _create_payload(answers=secreto))
        assert isinstance(parsed, Rejection)
        self.assertNotIn(secreto, repr(parsed))


class UpdateTests(SimpleTestCase):
    def test_writes_only_the_fields_present(self) -> None:
        visit_id = uuid.uuid4()
        parsed = parse_op("visit", "update", 3, {"id": str(visit_id), "lat": 4.6})
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.base_version, 3)
        self.assertEqual(parsed.fields, {"lat": 4.6})

    def test_distinguishes_an_explicit_null_from_an_absent_field(self) -> None:
        """Desligar la visita de su asignación es una escritura, no un olvido."""
        parsed = parse_op("visit", "update", 3, {"id": str(uuid.uuid4()), "assignment_id": None})
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.fields, {"assignment_id": None})

    def test_rejects_an_update_without_base_version(self) -> None:
        parsed = parse_op("visit", "update", None, {"id": str(uuid.uuid4()), "lat": 1.0})
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.details, {"field": "base_version", "reason": "invalid_for_op_type"})

    def test_rejects_a_boolean_base_version(self) -> None:
        """`True` es un `int` en Python y colaría como versión 1."""
        parsed = parse_op("visit", "update", True, {"id": str(uuid.uuid4()), "lat": 1.0})
        self.assertIsInstance(parsed, Rejection)

    def test_rejects_a_negative_base_version(self) -> None:
        parsed = parse_op("visit", "update", -1, {"id": str(uuid.uuid4()), "lat": 1.0})
        self.assertIsInstance(parsed, Rejection)

    def test_rejects_an_update_with_nothing_to_write(self) -> None:
        parsed = parse_op("visit", "update", 3, {"id": str(uuid.uuid4())})
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.details, {"field": "payload", "reason": "empty_update"})


class NotNullOnUpdateTests(SimpleTestCase):
    """Un `null` explícito sobre una columna `NOT NULL` ⇒ `rejected`, no `500`.

    Sin esta puerta, `SET template_version_id = NULL` llega a PostgreSQL,
    salta un `NotNullViolation` dentro de la **única** transacción del lote y
    se pierden las 500 operaciones con un `500` — el veneno que deja la outbox
    del cliente reintentando el mismo lote para siempre.
    """

    NOT_NULLABLE = ("template_version_id", "answers", "captured_at", "device_id")

    #: Columnas que sí admiten `NULL` en `Visit` (Paso 5): ponerlas a `null`
    #: es una escritura legítima y tiene que seguir funcionando.
    NULLABLE = ("assignment_id", "lat", "lon", "accuracy_m", "altitude_m")

    def test_an_explicit_null_on_a_not_null_column_is_rejected(self) -> None:
        for field in self.NOT_NULLABLE:
            with self.subTest(field=field):
                parsed = parse_op("visit", "update", 3, {"id": str(uuid.uuid4()), field: None})
                assert isinstance(parsed, Rejection)
                fields = [error["field"] for error in (parsed.details or {})["errors"]]
                self.assertEqual(fields, [field])

    def test_an_explicit_null_on_a_nullable_column_is_still_a_write(self) -> None:
        for field in self.NULLABLE:
            with self.subTest(field=field):
                parsed = parse_op("visit", "update", 3, {"id": str(uuid.uuid4()), field: None})
                assert isinstance(parsed, VisitWrite)
                self.assertEqual(parsed.fields, {field: None})

    def test_omitting_a_not_null_field_is_not_the_same_as_nulling_it(self) -> None:
        """La distinción ausente/`null` que `model_fields_set` sostiene."""
        parsed = parse_op("visit", "update", 3, {"id": str(uuid.uuid4()), "lat": 1.0})
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.fields, {"lat": 1.0})


class AnswersJsonTests(SimpleTestCase):
    """`answers` tiene que ser almacenable en `jsonb`, no solo "un objeto".

    Las dos entradas de aquí son JSON válido para `json.loads` de la librería
    estándar —la que usa Django Ninja para leer el cuerpo— y **no** son
    almacenables en `jsonb`. Si llegaran a `sql.py` producirían un `DataError`
    que aborta el lote entero con un `500`.
    """

    def _create(self, answers: object) -> object:
        return parse_op("visit", "create", None, _create_payload(answers=answers))

    def test_rejects_non_finite_numbers(self) -> None:
        """`json.loads` acepta los literales `NaN`/`Infinity`; `jsonb` no."""
        for answers in (
            {"a": float("nan")},
            {"a": float("inf")},
            {"a": [{"b": float("-inf")}]},
        ):
            with self.subTest(answers=answers):
                self.assertIsInstance(self._create(answers), Rejection)

    def test_rejects_the_nul_character_in_a_value_or_in_a_key(self) -> None:
        """`jsonb` no puede representar `U+0000` ("unsupported Unicode escape")."""
        for answers in ({"a": "antes\x00despues"}, {"clave\x00": "v"}, {"a": ["x\x00"]}):
            with self.subTest(answers=answers):
                self.assertIsInstance(self._create(answers), Rejection)

    def test_accepts_the_shapes_a_form_really_produces(self) -> None:
        answers = {"altura_cm": 12, "sano": True, "notas": None, "fotos": [{"n": 1.5}], "obs": ""}
        parsed = self._create(answers)
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.fields["answers"], answers)

    def test_the_same_gate_applies_to_an_update(self) -> None:
        parsed = parse_op(
            "visit", "update", 3, {"id": str(uuid.uuid4()), "answers": {"a": float("nan")}}
        )
        self.assertIsInstance(parsed, Rejection)

    def test_a_deeply_nested_payload_does_not_blow_the_stack(self) -> None:
        """El recorrido es iterativo: la profundidad la elige el cliente."""
        answers: dict[str, object] = {"leaf": "ok"}
        for _ in range(5_000):
            answers = {"nested": answers}
        self.assertIsInstance(self._create(answers), VisitWrite)


class UnpairedSurrogateTests(SimpleTestCase):
    """Hallazgo C-1 de la revisión del Paso 8.

    `json.loads` acepta `"\\ud800"` y devuelve un `str` de Python con un
    surrogate suelto. No revienta en Python en ningún punto del camino: como
    `sql.py` serializa con `json.dumps` y su `ensure_ascii=True` por defecto, el
    surrogate llega a PostgreSQL como el escape literal `\\ud800` y es el cast
    `::jsonb` el que lo rechaza —"Unicode low surrogate must follow a high
    surrogate"— con un `DataError` **dentro de la transacción del lote**, es
    decir un 500 que se lleva por delante las otras 499 operaciones.

    Los payloads se construyen con `json.loads`, no con literales de Python, para
    probar exactamente el camino que recorre un cuerpo de request real.
    """

    def _create(self, answers: object) -> object:
        return parse_op("visit", "create", None, _create_payload(answers=answers))

    #: `\\ud800` es un high surrogate suelto y `\\udfff` un low surrogate suelto:
    #: los dos extremos del rango, en valor, en clave y anidados en una lista.
    HOSTILE_JSON = (
        '{"a": "\\ud800"}',
        '{"a": "\\udfff"}',
        '{"a": "antes\\ud800despues"}',
        '{"clave\\ud800": "v"}',
        '{"a": ["x\\udc00"]}',
        '{"a": {"b": [{"c": "\\udbff"}]}}',
    )

    def test_json_loads_really_does_accept_them(self) -> None:
        """La premisa del test, comprobada y no supuesta."""
        for raw in self.HOSTILE_JSON:
            with self.subTest(raw=raw):
                self.assertIsInstance(json.loads(raw), dict)

    def test_an_unpaired_surrogate_is_rejected(self) -> None:
        for raw in self.HOSTILE_JSON:
            with self.subTest(raw=raw):
                self.assertIsInstance(self._create(json.loads(raw)), Rejection)

    def test_the_same_gate_applies_to_an_update(self) -> None:
        parsed = parse_op(
            "visit",
            "update",
            3,
            {"id": str(uuid.uuid4()), "answers": json.loads('{"a": "\\ud800"}')},
        )
        self.assertIsInstance(parsed, Rejection)

    def test_legitimate_non_ascii_text_still_passes(self) -> None:
        """La puerta no puede cerrarse sobre el español ni sobre los emoji.

        El tercer caso es un par de surrogates **bien formado** (`\\ud83c\\udf3d`,
        una mazorca): `json.loads` lo combina en un solo code point, así que es
        texto perfectamente almacenable y tiene que pasar.
        """
        for raw in (
            '{"observaciones": "plantación en ladera, ñ y tildes: áéíóú"}',
            '{"estado": "🌽 sano"}',
            '{"cultivo": "\\ud83c\\udf3d"}',
        ):
            with self.subTest(raw=raw):
                parsed = self._create(json.loads(raw))
                assert isinstance(parsed, VisitWrite)
                # Y sigue siendo serializable, que es de lo que se trataba.
                json.dumps(parsed.fields["answers"]).encode("utf-8")


class FiniteFloatTests(SimpleTestCase):
    """Hallazgo C-2 de la revisión del Paso 8: ningún `float` no finito entra.

    `NaN`/`±Infinity` son valores legales de `double precision` en PostgreSQL,
    así que no fallan al escribirse: la operación responde `applied` y la fila
    queda envenenada. El daño llega después y lejos de su causa —al serializar
    esa fila a `sync_op_log.result` (`jsonb` no admite el literal `NaN` que
    emite el encoder de Django) o al devolverla en el pull, donde `NaN` desnudo
    no es JSON válido y el `jsonDecode` de Dart lo rechaza, dejando al
    dispositivo sin poder sincronizar.

    Se cubren los cuatro campos, no solo los dos que estaban rotos: `lat`/`lon`
    los rechazaban por accidente (toda comparación con `NaN` es falsa, así que
    su guarda de rango los pillaba de rebote) y de ese accidente no se puede
    depender.
    """

    FLOAT_FIELDS = ("lat", "lon", "accuracy_m", "altitude_m")
    NON_FINITE = (("nan", float("nan")), ("inf", float("inf")), ("-inf", float("-inf")))

    def test_no_float_field_accepts_a_non_finite_value_on_create(self) -> None:
        for field in self.FLOAT_FIELDS:
            for label, value in self.NON_FINITE:
                with self.subTest(field=field, value=label):
                    parsed = parse_op("visit", "create", None, _create_payload(**{field: value}))
                    assert isinstance(parsed, Rejection)
                    fields = [error["field"] for error in (parsed.details or {})["errors"]]
                    self.assertEqual(fields, [field])

    def test_no_float_field_accepts_a_non_finite_value_on_update(self) -> None:
        for field in self.FLOAT_FIELDS:
            for label, value in self.NON_FINITE:
                with self.subTest(field=field, value=label):
                    payload = {"id": str(uuid.uuid4()), field: value}
                    self.assertIsInstance(parse_op("visit", "update", 3, payload), Rejection)

    def test_the_two_cases_that_used_to_slip_through(self) -> None:
        """Regresión literal del hallazgo, por si alguien quita `Finite`.

        `accuracy_m: Infinity` pasaba porque `inf >= 0` es cierto, y
        `altitude_m` pasaba con los tres porque no tenía guarda ninguna: era el
        único `float` del contrato sin `ge`/`le`.
        """
        for field, value in (
            ("accuracy_m", float("inf")),
            ("altitude_m", float("nan")),
            ("altitude_m", float("inf")),
            ("altitude_m", float("-inf")),
        ):
            with self.subTest(field=field, value=value):
                parsed = parse_op("visit", "create", None, _create_payload(**{field: value}))
                self.assertIsInstance(parsed, Rejection)

    def test_finite_values_and_nulls_are_untouched(self) -> None:
        """La puerta no puede cerrarse sobre una ubicación legítima."""
        payload = _create_payload(lat=4.60971, lon=-74.08175, accuracy_m=8.0, altitude_m=2625.0)
        parsed = parse_op("visit", "create", None, payload)
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.fields["altitude_m"], 2625.0)
        self.assertEqual(parsed.fields["accuracy_m"], 8.0)

        # Incluidas las altitudes que un rango "razonable" habría descartado:
        # bajo el nivel del mar (mar Muerto) y en vuelo.
        for altitude in (-430.0, 0.0, 11_000.0):
            with self.subTest(altitude=altitude):
                parsed = parse_op(
                    "visit", "create", None, _create_payload(altitude_m=altitude)
                )
                self.assertIsInstance(parsed, VisitWrite)

    def test_the_range_guards_still_work(self) -> None:
        """`Finite` se **suma** a `ge`/`le`, no los reemplaza."""
        for field, value in (("lat", 91.0), ("lon", -181.0), ("accuracy_m", -1.0)):
            with self.subTest(field=field, value=value):
                parsed = parse_op("visit", "create", None, _create_payload(**{field: value}))
                self.assertIsInstance(parsed, Rejection)


class DeleteTests(SimpleTestCase):
    def test_accepts_id_only(self) -> None:
        visit_id = uuid.uuid4()
        parsed = parse_op("visit", "delete", 2, {"id": str(visit_id)})
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.op_type, "delete")
        self.assertEqual(parsed.entity_id, visit_id)
        self.assertEqual(parsed.fields, {})

    def test_rejects_a_delete_carrying_data(self) -> None:
        parsed = parse_op("visit", "delete", 2, {"id": str(uuid.uuid4()), "lat": 1.0})
        self.assertIsInstance(parsed, Rejection)


class HostileInputTests(SimpleTestCase):
    """Nada de esto puede lanzar: si lanzara, el endpoint devolvería un 500."""

    HOSTILE: tuple[object, ...] = (
        None,
        "",
        0,
        [],
        [{"id": 1}],
        {"id": None},
        {"id": "no-es-un-uuid"},
        {"id": {"nested": True}},
        {},
    )

    def test_every_hostile_payload_is_a_structured_rejection(self) -> None:
        for op_type in ("create", "update", "delete"):
            for payload in self.HOSTILE:
                with self.subTest(op_type=op_type, payload=payload):
                    parsed = parse_op("visit", op_type, None if op_type == "create" else 1, payload)
                    assert isinstance(parsed, Rejection)
                    self.assertTrue(parsed.code)
                    self.assertTrue(parsed.message)

    def test_every_hostile_op_type_is_a_structured_rejection(self) -> None:
        for op_type in (None, "", "CREATE", "drop", 1, True, ["create"]):
            with self.subTest(op_type=op_type):
                parsed = parse_op("visit", op_type, None, {"id": str(uuid.uuid4())})
                assert isinstance(parsed, Rejection)
                self.assertEqual(
                    parsed.details, {"field": "op_type", "reason": "unsupported_op_type"}
                )

    def test_a_non_writable_entity_is_rejected_not_crashed(self) -> None:
        """`Visit` es la única entidad escribible del MVP (brief, tarea 3)."""
        parsed = parse_op("visit_attachment", "create", None, {"id": str(uuid.uuid4())})
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.details, {"field": "entity", "reason": "unsupported_entity"})


class TargetIdTests(SimpleTestCase):
    """El `entity_id` que permite dejar registrado incluso un rechazo."""

    def test_extracts_a_valid_uuid(self) -> None:
        visit_id = uuid.uuid4()
        self.assertEqual(target_id({"id": str(visit_id)}), visit_id)

    def test_returns_none_when_there_is_nothing_to_extract(self) -> None:
        payloads: tuple[object, ...] = (None, "x", [], {}, {"id": 3}, {"id": "abc"})
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(target_id(payload))

    def test_a_rejection_keeps_the_target_when_the_id_is_readable(self) -> None:
        visit_id = uuid.uuid4()
        parsed = parse_op("visit", "create", None, {"id": str(visit_id)})
        assert isinstance(parsed, Rejection)
        self.assertEqual(parsed.entity_id, visit_id)

"""Tests de `validate_answers` (Paso 20, tarea 3) — sin base de datos.

Es la mitad pura del comando de verificación del paso: qué acepta y qué
rechaza la validación de `answers` contra la definición de su plantilla. La
mitad que atraviesa el push de verdad (código de error del contrato, lote que
no se cae, etc.) está en `sync/tests/test_push_answers.py`, que sí necesita
PostgreSQL.

Incluye el **test de retrocompatibilidad histórica** del paso: una visita
capturada con la versión 1 sigue siendo válida contra la versión 1 aunque
exista una versión 2 incompatible.
"""

from __future__ import annotations

import uuid

from django.test import SimpleTestCase

from visitacampo.forms.answers import MAX_VIOLATIONS, validate_answers, violation_details
from visitacampo.forms.definition import validate_definition

from .factories import INSPECCION_V1, INSPECCION_V2, label, with_fields

V1 = validate_definition(INSPECCION_V1)
V2 = validate_definition(INSPECCION_V2)

#: Respuestas capturadas con la versión 1 de la plantilla.
ANSWERS_V1 = {"tiene_planta": True, "altura_cm": 42, "estado": "sano"}


def reasons(template, answers) -> list[str]:
    return [violation.reason for violation in validate_answers(template, answers)]


class ValidAnswersTests(SimpleTestCase):
    def test_a_complete_form_passes(self) -> None:
        self.assertEqual(validate_answers(V1, ANSWERS_V1), [])

    def test_an_optional_field_can_be_absent(self) -> None:
        self.assertEqual(validate_answers(V1, {"tiene_planta": True, "altura_cm": 1}), [])

    def test_a_hidden_required_field_is_not_demanded(self) -> None:
        """`altura_cm` es obligatorio **solo si** hay planta."""
        self.assertEqual(validate_answers(V1, {"tiene_planta": False}), [])

    def test_a_hidden_field_that_still_carries_a_value_is_accepted(self) -> None:
        """Decisión documentada: no se atasca la outbox por una rama que se ocultó."""
        self.assertEqual(validate_answers(V1, {"tiene_planta": False, "altura_cm": 3}), [])

    def test_a_hidden_field_with_a_badly_typed_value_is_still_rejected(self) -> None:
        """Admitir una respuesta huérfana no es admitir dato corrupto."""
        self.assertEqual(
            reasons(V1, {"tiene_planta": False, "altura_cm": "alta"}), ["not_a_number"]
        )


class RequiredAndUnknownTests(SimpleTestCase):
    def test_a_missing_required_visible_field_is_rejected(self) -> None:
        self.assertEqual(reasons(V1, {"tiene_planta": True}), ["required"])

    def test_null_counts_as_missing(self) -> None:
        self.assertEqual(reasons(V1, {"tiene_planta": True, "altura_cm": None}), ["required"])

    def test_the_conditional_required_field_appears_with_its_trigger(self) -> None:
        """La regla `in` sobre la selección: elegir `plaga` exige describirla."""
        self.assertEqual(
            reasons(V1, {"tiene_planta": True, "altura_cm": 1, "estado": "plaga"}), ["required"]
        )
        self.assertEqual(
            validate_answers(
                V1,
                {
                    "tiene_planta": True,
                    "altura_cm": 1,
                    "estado": "plaga",
                    "plaga_observada": "roya",
                },
            ),
            [],
        )

    def test_a_key_the_template_does_not_declare_is_rejected(self) -> None:
        """`answers` no es un saco de JSON arbitrario (mismo criterio que `extra=forbid`)."""
        violations = validate_answers(V1, {**ANSWERS_V1, "colado": "x"})
        self.assertEqual([v.reason for v in violations], ["unknown_field"])
        self.assertEqual(violations[0].field, "answers.colado")

    def test_violations_are_capped(self) -> None:
        template = validate_definition(
            with_fields(
                *(
                    {"key": f"campo_{index}", "type": "number", "label": label(f"c{index}")}
                    for index in range(MAX_VIOLATIONS + 10)
                )
            )
        )
        answers = {f"campo_{index}": "no-es-numero" for index in range(MAX_VIOLATIONS + 10)}
        self.assertEqual(len(validate_answers(template, answers)), MAX_VIOLATIONS)


class FieldTypeTests(SimpleTestCase):
    """El catálogo de tipos, cada uno con lo que acepta y lo que no."""

    def _template(self, field: dict) -> object:
        return validate_definition(with_fields(field))

    def _check(self, field: dict, value: object) -> list[str]:
        return reasons(self._template(field), {field["key"]: value})

    def test_text(self) -> None:
        field = {"key": "nota", "type": "text", "label": label("n"), "max_length": 4}
        self.assertEqual(self._check(field, "abc"), [])
        self.assertEqual(self._check(field, "abcde"), ["too_long"])
        self.assertEqual(self._check(field, 3), ["not_a_string"])

    def test_number_range_and_finiteness(self) -> None:
        field = {"key": "n", "type": "number", "label": label("n"), "min": 0, "max": 10}
        self.assertEqual(self._check(field, 5), [])
        self.assertEqual(self._check(field, 5.5), [])
        self.assertEqual(self._check(field, 11), ["out_of_range"])
        self.assertEqual(self._check(field, float("nan")), ["not_finite"])
        self.assertEqual(self._check(field, True), ["not_a_number"])

    def test_integer(self) -> None:
        field = {"key": "n", "type": "integer", "label": label("n"), "min": 1}
        self.assertEqual(self._check(field, 2), [])
        self.assertEqual(self._check(field, 2.5), ["not_an_integer"])
        self.assertEqual(self._check(field, 0), ["out_of_range"])

    def test_boolean(self) -> None:
        field = {"key": "b", "type": "boolean", "label": label("b")}
        self.assertEqual(self._check(field, True), [])
        self.assertEqual(self._check(field, 1), ["not_a_boolean"])

    def test_date_and_datetime(self) -> None:
        date_field = {"key": "d", "type": "date", "label": label("d")}
        self.assertEqual(self._check(date_field, "2026-08-03"), [])
        self.assertEqual(self._check(date_field, "03/08/2026"), ["not_a_date"])
        self.assertEqual(self._check(date_field, "2026-13-01"), ["not_a_date"])

        stamp = {"key": "t", "type": "datetime", "label": label("t")}
        self.assertEqual(self._check(stamp, "2026-08-03T14:05:09+00:00"), [])
        self.assertEqual(self._check(stamp, "2026-08-03T14:05:09"), ["naive_datetime"])

    def test_single_choice(self) -> None:
        field = {
            "key": "e",
            "type": "single_choice",
            "label": label("e"),
            "options": [{"value": "a", "label": label("A")}],
        }
        self.assertEqual(self._check(field, "a"), [])
        self.assertEqual(self._check(field, "b"), ["not_an_option"])

    def test_multi_choice(self) -> None:
        field = {
            "key": "m",
            "type": "multi_choice",
            "label": label("m"),
            "max_selected": 2,
            "options": [
                {"value": "a", "label": label("A")},
                {"value": "b", "label": label("B")},
                {"value": "c", "label": label("C")},
            ],
        }
        self.assertEqual(self._check(field, ["a", "b"]), [])
        self.assertEqual(self._check(field, ["a", "b", "c"]), ["too_many_selected"])
        self.assertEqual(self._check(field, ["a", "a"]), ["duplicate_selection"])
        self.assertEqual(self._check(field, ["z"]), ["not_an_option"])
        self.assertEqual(self._check(field, "a"), ["not_a_list"])

    def test_attachment_references(self) -> None:
        for kind in ("photo", "signature"):
            field = {"key": "f", "type": kind, "label": label("f")}
            with self.subTest(kind=kind):
                self.assertEqual(self._check(field, str(uuid.uuid4())), [])
                self.assertEqual(self._check(field, "../../etc/passwd"), ["not_a_uuid"])

    def test_location(self) -> None:
        field = {"key": "p", "type": "location", "label": label("p")}
        self.assertEqual(self._check(field, {"lat": 4.6, "lon": -74.1}), [])
        self.assertEqual(self._check(field, {"lat": 91.0, "lon": 0.0}), ["out_of_range"])
        self.assertEqual(self._check(field, {"lat": 4.6}), ["not_a_location"])
        self.assertEqual(self._check(field, {"lat": 4.6, "lon": 0.0, "alt": 1}), ["not_a_location"])


class HistoricalCompatibilityTests(SimpleTestCase):
    """**Retrocompatibilidad histórica** (criterio de salida del Paso 20).

    Una visita antigua se valida contra la definición de **su** versión, no
    contra la última publicada. Publicar una versión nueva —aunque renombre
    campos y endurezca la obligatoriedad— no puede invalidar dato histórico ni
    forzar una migración de datos.
    """

    def test_answers_of_version_one_stay_valid_against_version_one(self) -> None:
        self.assertEqual(validate_answers(V1, ANSWERS_V1), [])

    def test_the_newer_version_is_genuinely_incompatible(self) -> None:
        """Si V2 aceptara lo mismo que V1, el test de arriba no probaría nada."""
        self.assertNotEqual(validate_answers(V2, ANSWERS_V1), [])

    def test_each_version_validates_its_own_answers(self) -> None:
        answers_v2 = {"tiene_planta": True, "altura_m": 1.2, "estado": "sano"}
        self.assertEqual(validate_answers(V2, answers_v2), [])
        self.assertNotEqual(validate_answers(V1, answers_v2), [])


class ViolationDetailsTests(SimpleTestCase):
    def test_details_use_the_contract_shape(self) -> None:
        """El contrato: `{"field": "answers.altura_cm", "reason": "out_of_range"}`."""
        violations = validate_answers(V1, {"tiene_planta": True, "altura_cm": 900})
        self.assertEqual(
            violation_details(violations),
            {"errors": [{"field": "answers.altura_cm", "reason": "out_of_range"}]},
        )

    def test_details_never_echo_the_offending_value(self) -> None:
        secreto = "dato-sensible-que-no-debe-volver"
        violations = validate_answers(V1, {**ANSWERS_V1, "altura_cm": secreto})
        self.assertNotIn(secreto, str(violation_details(violations)))

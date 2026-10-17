"""`answers` contra el schema de su plantilla, en el camino real del push (Paso 20).

Es el **comando de verificación 2** del Paso 20 ejecutado de punta a punta:
`visit.answers` que no cumple el schema de su `template_version_id` se rechaza
en `POST /v1/sync/push`, con el código del contrato y sin tumbar el lote.
Requiere PostgreSQL, como el resto de la integración de sync.

Lo que estos tests fijan y `forms/tests/test_answers.py` no puede fijar:

- que la validación **ocurre en el push** (el servidor es la autoridad, RD-03:
  el cliente podría no haber validado nada, o haber sido modificado);
- que el rechazo es `rejected` con `code: validation_error` y `details` en la
  forma del contrato, **no** un 500 ni un lote perdido;
- que una operación rechazada por esto **no escribe nada** y no impide que el
  resto del lote se aplique;
- que la validación es contra la versión que la visita referencia, también
  cuando existe una versión más nueva de la misma plantilla
  (retrocompatibilidad histórica, criterio de salida del paso).
"""

from __future__ import annotations

import unittest
import uuid

from django.test import TestCase

from visitacampo.core.models import FormTemplateVersion, Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import build_scenario, create_op, push, update_op, visit_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def _label(text: str) -> dict[str, str]:
    return {"es": text}


#: Versión 2 de la misma plantilla: renombra `altura_cm` a `altura_m` y la hace
#: obligatoria. Deliberadamente **incompatible** con las respuestas de la v1.
DEFINITION_V2 = {
    "dsl": "visitacampo/form@1",
    "fields": [
        {
            "key": "altura_m",
            "type": "number",
            "label": _label("Altura (m)"),
            "required": True,
            "min": 0,
            "max": 5,
        }
    ],
}


@skip_without_db
class AnswersValidationTests(TestCase):
    def setUp(self) -> None:
        self.scenario = build_scenario()

    def _result(self, op: dict) -> dict:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def test_answers_out_of_range_are_rejected_in_the_push(self) -> None:
        visit_id = uuid.uuid4()
        result = self._result(
            create_op(self.scenario, visit_id=visit_id, answers={"altura_cm": 900})
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "validation_error")
        self.assertEqual(
            result["error"]["details"],
            {"errors": [{"field": "answers.altura_cm", "reason": "out_of_range"}]},
        )
        self.assertFalse(Visit.objects.filter(pk=visit_id).exists())

    def test_a_badly_typed_answer_is_rejected(self) -> None:
        result = self._result(create_op(self.scenario, answers={"altura_cm": "muy alta"}))
        self.assertEqual(result["error"]["details"]["errors"][0]["reason"], "not_a_number")

    def test_a_key_the_template_does_not_declare_is_rejected(self) -> None:
        result = self._result(create_op(self.scenario, answers={"altura_cm": 1, "colado": "x"}))
        self.assertEqual(
            result["error"]["details"],
            {"errors": [{"field": "answers.colado", "reason": "unknown_field"}]},
        )

    def test_valid_answers_are_applied(self) -> None:
        visit_id = uuid.uuid4()
        result = self._result(
            create_op(self.scenario, visit_id=visit_id, answers={"altura_cm": 12})
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(visit_of(visit_id).answers, {"altura_cm": 12})

    def test_an_invalid_operation_does_not_take_the_batch_down(self) -> None:
        good = uuid.uuid4()
        results = push(
            self.scenario,
            [
                create_op(self.scenario, answers={"altura_cm": 900}),
                create_op(self.scenario, visit_id=good),
            ],
        ).json()["results"]
        self.assertEqual([result["status"] for result in results], ["rejected", "applied"])
        self.assertTrue(Visit.objects.filter(pk=good).exists())

    def test_the_error_body_never_echoes_the_offending_value(self) -> None:
        """RD-12, también por este camino."""
        secreto = "dato-sensible-que-no-debe-volver"
        response = push(self.scenario, [create_op(self.scenario, answers={"altura_cm": secreto})])
        self.assertNotIn(secreto, response.content.decode())

    def test_an_update_that_rewrites_answers_is_validated_too(self) -> None:
        """Sin `template_version_id` en el payload: se resuelve de la fila."""
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        base = visit_of(visit_id).version

        result = self._result(update_op(visit_id, base_version=base, answers={"altura_cm": 900}))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "validation_error")
        self.assertEqual(visit_of(visit_id).version, base)

    def test_an_update_with_valid_answers_is_applied(self) -> None:
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        base = visit_of(visit_id).version

        result = self._result(update_op(visit_id, base_version=base, answers={"altura_cm": 7}))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(visit_of(visit_id).answers, {"altura_cm": 7})

    def test_a_version_without_a_declared_schema_accepts_any_answers(self) -> None:
        """Retrocompatibilidad de las filas anteriores al Paso 20 (`definition = {}`)."""
        legacy = FormTemplateVersion.objects.create(
            org=self.scenario.org,
            template=self.scenario.template_version.template,
            version_number=99,
            definition={},
            change_seq=self.scenario.template_version.change_seq + 1,
        )
        result = self._result(
            create_op(
                self.scenario,
                template_version_id=str(legacy.pk),
                answers={"lo_que_sea": [1, 2, 3]},
            )
        )
        self.assertEqual(result["status"], "applied")

    def test_a_stored_definition_that_is_broken_rejects_instead_of_accepting(self) -> None:
        """ "No puedo comprobarlo" nunca puede significar "adelante"."""
        broken = FormTemplateVersion.objects.create(
            org=self.scenario.org,
            template=self.scenario.template_version.template,
            version_number=98,
            # Solo alcanzable escribiendo la fila a mano: `publish_version()` y
            # `clean()` rechazan esto. Se prueba porque la fila existe en la BD
            # y el push no puede fiarse de que nadie la haya tocado.
            definition={"fields": [{"key": "a", "type": "qml", "label": {"es": "a"}}]},
            change_seq=self.scenario.template_version.change_seq + 2,
        )
        with self.assertLogs("visitacampo.sync.services", level="ERROR"):
            result = self._result(
                create_op(self.scenario, template_version_id=str(broken.pk), answers={"a": 1})
            )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["details"]["reason"], "invalid_template")


@skip_without_db
class HistoricalCompatibilityTests(TestCase):
    """**Criterio de salida**: publicar una versión nueva no invalida lo antiguo."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        self.v2 = FormTemplateVersion.objects.create(
            org=self.scenario.org,
            template=self.scenario.template_version.template,
            version_number=2,
            definition=DEFINITION_V2,
            change_seq=self.scenario.template_version.change_seq + 1,
        )

    def test_a_visit_on_the_old_version_is_still_accepted(self) -> None:
        visit_id = uuid.uuid4()
        result = push(self.scenario, [create_op(self.scenario, visit_id=visit_id)]).json()[
            "results"
        ][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(visit_of(visit_id).answers, {"altura_cm": 12})

    def test_the_new_version_is_genuinely_incompatible(self) -> None:
        """Si la v2 aceptara las respuestas de la v1, el test de arriba no probaría nada."""
        result = push(
            self.scenario,
            [create_op(self.scenario, template_version_id=str(self.v2.pk))],
        ).json()["results"][0]
        self.assertEqual(result["status"], "rejected")

    def test_an_update_of_a_historical_visit_uses_its_own_version(self) -> None:
        """Reeditar una visita vieja se valida contra la plantilla con la que se capturó."""
        visit_id = uuid.uuid4()
        push(self.scenario, [create_op(self.scenario, visit_id=visit_id)])
        base = visit_of(visit_id).version

        result = push(
            self.scenario, [update_op(visit_id, base_version=base, answers={"altura_cm": 30})]
        ).json()["results"][0]
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(visit_of(visit_id).answers, {"altura_cm": 30})

    def test_a_visit_on_the_new_version_uses_the_new_schema(self) -> None:
        visit_id = uuid.uuid4()
        result = push(
            self.scenario,
            [
                create_op(
                    self.scenario,
                    visit_id=visit_id,
                    template_version_id=str(self.v2.pk),
                    answers={"altura_m": 1.2},
                )
            ],
        ).json()["results"][0]
        self.assertEqual(result["status"], "applied", result)

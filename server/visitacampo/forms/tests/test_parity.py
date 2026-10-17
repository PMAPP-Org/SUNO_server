"""Paridad servidor ↔ cliente del DSL de formularios (Paso 21).

`server/openapi/fixtures/forms_parity.json` es la fuente única de casos: este
test y `app/test/core/forms/parity_test.dart` ejecutan **los mismos**. Si el
servidor y el renderer de Flutter llegaran a discrepar sobre qué plantilla es
válida, qué campo se ve o qué respuesta se rechaza, el agente vería en pantalla
un formulario distinto del que el servidor valida (ADR-012).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.test import SimpleTestCase

from visitacampo.forms.answers import validate_answers
from visitacampo.forms.definition import TemplateDefinitionError, validate_definition
from visitacampo.forms.visibility import evaluate

FIXTURE = Path(__file__).resolve().parents[3] / "openapi" / "fixtures" / "forms_parity.json"
CASES: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


class ParityTests(SimpleTestCase):
    def test_the_fixture_has_cases(self) -> None:
        self.assertGreaterEqual(len(CASES["valid_definitions"]), 1)
        self.assertGreaterEqual(len(CASES["invalid_definitions"]), 10)

    def test_valid_definitions_and_their_answers(self) -> None:
        for template in CASES["valid_definitions"]:
            definition = validate_definition(template["definition"])
            for case in template["cases"]:
                with self.subTest(template=template["name"], case=case["name"]):
                    answers = case["answers"]
                    visible = [f.key for f in definition.fields if evaluate(f.visible_if, answers)]
                    self.assertEqual(visible, case["visible"])
                    violations = [
                        [v.field.removeprefix("answers."), v.reason]
                        for v in validate_answers(definition, answers)
                    ]
                    self.assertEqual(violations, case["violations"])

    def test_invalid_definitions_are_rejected(self) -> None:
        for template in CASES["invalid_definitions"]:
            with self.subTest(template=template["name"]):
                with self.assertRaises(TemplateDefinitionError):
                    validate_definition(template["definition"])

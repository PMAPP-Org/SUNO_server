"""Tests del meta-schema, del DSL y de la inmutabilidad de versión (Paso 20).

Los tres comandos de verificación del paso viven aquí y en `test_answers.py`:

1. plantilla mal formada ⇒ **rechazada al publicar** (`MetaSchemaTests`,
   `PublishVersionTests`);
2. `answers` que no cumple el schema de su plantilla ⇒ rechazada en el push
   (`sync/tests/test_push_answers.py`, que necesita PostgreSQL);
3. **test de seguridad**: una expresión fuera del vocabulario del DSL se
   rechaza al validar la plantilla y **nunca se ejecuta**
   (`DslSecurityTests`) — la regresión de H-06 (RD-06).

La mayoría no toca la base de datos: validar una plantilla es una función pura
del dato, así que corren siempre (también en una máquina sin Docker levantado).
Solo `PublishVersionTests`/`ImmutabilityTests` necesitan PostgreSQL.
"""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from visitacampo.core.context import RequestContext
from visitacampo.core.models import FormTemplate, FormTemplateVersion, Organization, Role
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.forms import visibility
from visitacampo.forms.definition import (
    DSL_VERSION,
    FIELD_TYPES,
    MAX_JSON_DEPTH,
    META_SCHEMA,
    FormDefinition,
    TemplateDefinitionError,
    has_declared_schema,
    validate_definition,
)
from visitacampo.forms.services import publish_version

from .factories import INSPECCION_V1, INSPECCION_V2, label, text_field, with_fields

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class MetaSchemaTests(SimpleTestCase):
    """Tarea 2: el meta-schema acepta lo bien formado y rechaza lo demás."""

    def test_the_reference_template_is_valid(self) -> None:
        parsed = validate_definition(INSPECCION_V1)
        self.assertIsInstance(parsed, FormDefinition)
        self.assertEqual([field.key for field in parsed.fields][0], "tiene_planta")

    def test_the_dsl_dialect_defaults_to_the_supported_one(self) -> None:
        self.assertEqual(validate_definition(with_fields(text_field())).dsl, DSL_VERSION)

    def test_an_unsupported_dialect_is_rejected(self) -> None:
        """Un dialecto futuro no se interpreta a medias: se rechaza entero."""
        with self.assertRaises(TemplateDefinitionError):
            validate_definition({"dsl": "visitacampo/form@99", "fields": []})

    def test_a_definition_that_is_not_an_object_is_rejected(self) -> None:
        raws: list[Any] = [[], "fields", 3, None]
        for raw in raws:
            with self.subTest(raw=raw), self.assertRaises(TemplateDefinitionError):
                validate_definition(raw)

    def test_an_unknown_field_type_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(with_fields({"key": "a", "type": "qml", "label": label("a")}))

    def test_an_unknown_key_inside_a_field_is_rejected(self) -> None:
        """`extra="forbid"`: nada que la plantilla no declare se guarda "por si acaso"."""
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(with_fields(text_field(onchange="alert(1)")))

    def test_a_field_without_a_label_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(with_fields({"key": "a", "type": "text"}))

    def test_an_empty_i18n_label_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(with_fields({"key": "a", "type": "text", "label": {}}))

    def test_a_field_key_outside_the_identifier_charset_is_rejected(self) -> None:
        for key in ("Altura", "altura cm", "__proto__", "a.b", "1a", ""):
            with self.subTest(key=key), self.assertRaises(TemplateDefinitionError):
                validate_definition(with_fields({"key": key, "type": "text", "label": label("x")}))

    def test_duplicate_field_keys_are_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(with_fields(text_field("nota"), text_field("nota")))

    def test_inverted_bounds_are_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                with_fields(
                    {"key": "n", "type": "number", "label": label("n"), "min": 10, "max": 1}
                )
            )

    def test_duplicate_option_values_are_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                with_fields(
                    {
                        "key": "e",
                        "type": "single_choice",
                        "label": label("e"),
                        "options": [
                            {"value": "a", "label": label("a")},
                            {"value": "a", "label": label("otra a")},
                        ],
                    }
                )
            )

    def test_a_definition_nested_beyond_the_limit_is_rejected_before_parsing(self) -> None:
        """Sin este corte, el parseo recursivo de Pydantic sería un `RecursionError`."""
        nested: object = "hoja"
        for _ in range(MAX_JSON_DEPTH + 5):
            nested = {"x": nested}
        with self.assertRaises(TemplateDefinitionError) as raised:
            validate_definition({"fields": [], "title": nested})
        self.assertEqual(raised.exception.details, {"field": "definition", "reason": "too_deep"})

    def test_the_error_details_say_where_without_echoing_the_value(self) -> None:
        """RD-12: `field` y `reason`, nunca el dato que falló."""
        secreto = "valor-que-no-debe-volver"
        with self.assertRaises(TemplateDefinitionError) as raised:
            validate_definition(with_fields({"key": secreto, "type": "text", "label": label("x")}))
        self.assertNotIn(secreto, str(raised.exception.details))

    def test_the_meta_schema_is_a_json_schema_document(self) -> None:
        """Se emite de verdad: es lo que el back-office puede publicar/documentar."""
        self.assertEqual(META_SCHEMA["type"], "object")
        self.assertIn("fields", META_SCHEMA["properties"])
        self.assertIn("$defs", META_SCHEMA)

    def test_the_field_catalog_is_the_documented_one(self) -> None:
        """El catálogo es cerrado: ampliarlo obliga a tocar este test (ADR-012)."""
        self.assertEqual(
            FIELD_TYPES,
            {
                "text",
                "number",
                "integer",
                "boolean",
                "date",
                "datetime",
                "single_choice",
                "multi_choice",
                "photo",
                "signature",
                "location",
                "line",
                "polygon",
            },
        )

    def test_an_empty_definition_declares_no_schema(self) -> None:
        """Las filas anteriores al Paso 20 (`{}`) no restringen `answers`."""
        self.assertFalse(has_declared_schema({}))
        self.assertTrue(has_declared_schema({"fields": []}))


class DslSecurityTests(SimpleTestCase):
    """**Test de seguridad del paso** (RD-06 / H-06): fuera del vocabulario, no se ejecuta.

    Lo que se prueba no es "el intérprete es cuidadoso" sino que **no hay
    intérprete**: una expresión que no encaja en la allow-list muere al validar
    la plantilla, en el back-office, antes de que exista un dispositivo que la
    pueda mirar.
    """

    #: Intentos de salirse del vocabulario, cada uno con la forma que tendría
    #: el ataque real (llamada a función, código embebido, acceso a atributos,
    #: ruta del sistema de ficheros, operador no listado).
    HOSTILE = (
        {"op": "eval", "code": "__import__('os').system('id')"},
        {"op": "exec", "field": "a", "value": "1"},
        {"op": "call", "fn": "open", "args": ["/etc/passwd"]},
        {"op": "eq", "field": "a", "value": {"__class__": "os.system"}},
        {"op": "eq", "field": "a", "value": True, "then": {"op": "eval", "code": "1"}},
        {"op": "matches", "field": "a", "value": "(a+)+$"},
        {"op": "eq", "field": "a.__class__", "value": 1},
        {"op": "and", "operands": [{"op": "eval", "code": "1"}, {"op": "eval", "code": "2"}]},
        "tiene_planta == true",
        ["eq", "a", 1],
    )

    def test_every_hostile_expression_is_rejected_when_validating_the_template(self) -> None:
        for expression in self.HOSTILE:
            with self.subTest(expression=expression), self.assertRaises(TemplateDefinitionError):
                validate_definition(
                    with_fields(
                        {"key": "a", "type": "boolean", "label": label("a")},
                        {"key": "b", "type": "text", "label": label("b"), "visible_if": expression},
                    )
                )

    def test_the_hostile_expressions_are_not_even_representable(self) -> None:
        """No llegan a existir como expresión: el tipo mismo las rechaza."""
        adapter: TypeAdapter[Any] = TypeAdapter(visibility.VisibilityExpr)
        for expression in self.HOSTILE:
            with self.subTest(expression=expression), self.assertRaises(PydanticValidationError):
                adapter.validate_python(expression)

    def test_the_package_contains_no_dynamic_execution_primitive(self) -> None:
        """La regla de arquitectura del paquete, comprobada y no solo escrita.

        Con `ast` y no con `grep`, por el mismo motivo que
        `core/tests/test_architecture.py`: los docstrings de este paquete
        **mencionan** `eval`/`exec` continuamente —son los que explican la
        prohibición— y un `grep` no distingue explicar de hacer. El árbol
        sintáctico sí.
        """
        import ast
        import pathlib

        package = pathlib.Path(visibility.__file__).parent
        forbidden_calls = {"eval", "exec", "compile", "__import__", "getattr", "setattr"}
        forbidden_imports = {"pickle", "marshal", "subprocess", "os", "importlib", "ctypes"}

        for module in sorted(package.rglob("*.py")):
            if "tests" in module.parts:
                continue
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=module.name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    with self.subTest(module=module.name, call=node.func.id):
                        self.assertNotIn(node.func.id, forbidden_calls)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        with self.subTest(module=module.name, imported=alias.name):
                            self.assertNotIn(alias.name.split(".")[0], forbidden_imports)
                if isinstance(node, ast.ImportFrom) and node.module:
                    with self.subTest(module=module.name, imported=node.module):
                        self.assertNotIn(node.module.split(".")[0], forbidden_imports)

    def test_the_vocabulary_is_exactly_the_documented_one(self) -> None:
        self.assertEqual(
            visibility.OPERATORS,
            {
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "is_empty",
                "is_not_empty",
                "not",
                "and",
                "or",
            },
        )


class VisibilityRuleTests(SimpleTestCase):
    """Las reglas del DSL que dependen del resto de la plantilla."""

    def _template(self, visible_if: object) -> dict[str, object]:
        return with_fields(
            {"key": "tiene_planta", "type": "boolean", "label": label("p"), "required": True},
            {"key": "altura_cm", "type": "number", "label": label("a"), "visible_if": visible_if},
        )

    def test_a_forward_reference_is_rejected(self) -> None:
        """Solo hacia atrás ⇒ el grafo de dependencias no puede tener ciclos."""
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                with_fields(
                    {
                        "key": "a",
                        "type": "text",
                        "label": label("a"),
                        "visible_if": {"op": "is_not_empty", "field": "b"},
                    },
                    {"key": "b", "type": "text", "label": label("b")},
                )
            )

    def test_a_self_reference_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                with_fields(
                    {
                        "key": "a",
                        "type": "text",
                        "label": label("a"),
                        "visible_if": {"op": "is_not_empty", "field": "a"},
                    }
                )
            )

    def test_a_reference_to_an_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(self._template({"op": "eq", "field": "no_existe", "value": 1}))

    def test_a_literal_that_the_field_can_never_take_is_rejected(self) -> None:
        """Una regla siempre falsa es un campo que nunca aparece: se caza al publicar."""
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(self._template({"op": "eq", "field": "tiene_planta", "value": 1}))

    def test_an_option_typo_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                with_fields(
                    {
                        "key": "estado",
                        "type": "single_choice",
                        "label": label("e"),
                        "options": [{"value": "sano", "label": label("Sano")}],
                    },
                    {
                        "key": "nota",
                        "type": "text",
                        "label": label("n"),
                        "visible_if": {"op": "eq", "field": "estado", "value": "snao"},
                    },
                )
            )

    def test_ordering_a_boolean_is_rejected(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(self._template({"op": "gt", "field": "tiene_planta", "value": 1}))

    def test_an_expression_nested_beyond_the_limit_is_rejected(self) -> None:
        expression: object = {"op": "eq", "field": "tiene_planta", "value": True}
        for _ in range(visibility.MAX_EXPR_DEPTH):
            expression = {"op": "not", "operand": expression}
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(self._template(expression))

    def test_too_many_operands_are_rejected(self) -> None:
        operand = {"op": "eq", "field": "tiene_planta", "value": True}
        with self.assertRaises(TemplateDefinitionError):
            validate_definition(
                self._template({"op": "and", "operands": [operand] * (visibility.MAX_OPERANDS + 1)})
            )

    def test_a_valid_nested_expression_is_accepted(self) -> None:
        parsed = validate_definition(
            self._template(
                {
                    "op": "and",
                    "operands": [
                        {"op": "eq", "field": "tiene_planta", "value": True},
                        {"op": "not", "operand": {"op": "is_empty", "field": "tiene_planta"}},
                    ],
                }
            )
        )
        self.assertIsNotNone(parsed.fields[1].visible_if)


class EvaluatorTests(SimpleTestCase):
    """El evaluador: puro, total y sin sorpresas de tipos."""

    def _expr(self, raw: object) -> Any:
        adapter: TypeAdapter[Any] = TypeAdapter(visibility.VisibilityExpr)
        return adapter.validate_python(raw)

    def test_no_condition_means_always_visible(self) -> None:
        self.assertTrue(visibility.evaluate(None, {}))

    def test_the_three_rule_shapes(self) -> None:
        cases: tuple[Any, ...] = (
            ({"op": "eq", "field": "a", "value": 3}, {"a": 3}, True),
            ({"op": "ne", "field": "a", "value": 3}, {"a": 4}, True),
            ({"op": "gt", "field": "a", "value": 3}, {"a": 4}, True),
            ({"op": "gte", "field": "a", "value": 3}, {"a": 3}, True),
            ({"op": "lt", "field": "a", "value": 3}, {"a": 4}, False),
            ({"op": "lte", "field": "a", "value": 3}, {"a": 3}, True),
            ({"op": "in", "field": "a", "value": ["x", "y"]}, {"a": "y"}, True),
            ({"op": "not_in", "field": "a", "value": ["x"]}, {"a": "y"}, True),
            ({"op": "is_empty", "field": "a"}, {}, True),
            ({"op": "is_not_empty", "field": "a"}, {"a": "x"}, True),
        )
        for raw, answers, expected in cases:
            with self.subTest(raw=raw):
                self.assertIs(visibility.evaluate(self._expr(raw), answers), expected)

    def test_a_boolean_is_not_the_number_one(self) -> None:
        """En Python `True == 1`; en este DSL no, y es a propósito."""
        self.assertFalse(
            visibility.evaluate(self._expr({"op": "eq", "field": "a", "value": 1}), {"a": True})
        )
        self.assertTrue(
            visibility.evaluate(self._expr({"op": "eq", "field": "a", "value": True}), {"a": True})
        )

    def test_false_and_zero_are_answers_not_blanks(self) -> None:
        for value in (False, 0, 0.0):
            with self.subTest(value=value):
                self.assertFalse(visibility.is_blank(value))

    def test_comparing_incompatible_types_is_false_not_an_exception(self) -> None:
        """Total: dentro del bucle de push, una excepción sería un 500."""
        for answers in ({"a": "texto"}, {"a": None}, {"a": [1]}, {"a": {"x": 1}}, {}):
            with self.subTest(answers=answers):
                self.assertFalse(
                    visibility.evaluate(self._expr({"op": "gt", "field": "a", "value": 3}), answers)
                )

    def test_and_or_not(self) -> None:
        expr = self._expr(
            {
                "op": "or",
                "operands": [
                    {"op": "eq", "field": "a", "value": 1},
                    {"op": "not", "operand": {"op": "is_empty", "field": "b"}},
                ],
            }
        )
        self.assertTrue(visibility.evaluate(expr, {"a": 1}))
        self.assertTrue(visibility.evaluate(expr, {"b": "x"}))
        self.assertFalse(visibility.evaluate(expr, {"a": 2}))


@skip_without_db
class PublishVersionTests(TestCase):
    """Tarea 1/4: publicar valida contra el meta-schema y numera la versión."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Org")
        self.template = FormTemplate.objects.create(org=self.org, name="Inspección")
        self.ctx = RequestContext(
            user_id=uuid.uuid4(), org_id=self.org.pk, roles=frozenset({Role.ADMIN})
        )

    def test_publishing_a_valid_definition_creates_version_one(self) -> None:
        version = publish_version(
            self.ctx, template_id=self.template.pk, definition=INSPECCION_V1, name="v1"
        )
        self.assertEqual(version.version_number, 1)
        self.assertIsNotNone(version.published_at)
        self.assertEqual(version.org_id, self.org.pk)
        self.assertGreater(version.change_seq, 0)
        self.assertEqual(len(version.definition["fields"]), 4)

    def test_the_stored_definition_is_the_normalized_one(self) -> None:
        """Lo que el pull entrega es exactamente lo que este servidor validó."""
        version = publish_version(self.ctx, template_id=self.template.pk, definition={"fields": []})
        self.assertEqual(version.definition["dsl"], DSL_VERSION)
        validate_definition(version.definition)

    def test_a_malformed_definition_is_rejected_at_publish_time(self) -> None:
        """**Comando de verificación del paso**: no llega a existir la fila."""
        with self.assertRaises(TemplateDefinitionError):
            publish_version(
                self.ctx,
                template_id=self.template.pk,
                definition=with_fields({"key": "a", "type": "qml", "label": label("a")}),
            )
        self.assertEqual(FormTemplateVersion.objects.count(), 0)

    def test_a_hostile_dsl_expression_is_rejected_at_publish_time(self) -> None:
        with self.assertRaises(TemplateDefinitionError):
            publish_version(
                self.ctx,
                template_id=self.template.pk,
                definition=with_fields(
                    {"key": "a", "type": "boolean", "label": label("a")},
                    {
                        "key": "b",
                        "type": "text",
                        "label": label("b"),
                        "visible_if": {"op": "eval", "code": "1"},
                    },
                ),
            )
        self.assertEqual(FormTemplateVersion.objects.count(), 0)

    def test_versions_are_numbered_consecutively(self) -> None:
        first = publish_version(self.ctx, template_id=self.template.pk, definition=INSPECCION_V1)
        second = publish_version(self.ctx, template_id=self.template.pk, definition=INSPECCION_V2)
        self.assertEqual((first.version_number, second.version_number), (1, 2))
        self.assertGreater(second.change_seq, first.change_seq)
        # La versión vieja sigue intacta: publicar no reescribe nada.
        first.refresh_from_db()
        self.assertEqual(first.definition["fields"][1]["key"], "altura_cm")

    def test_a_template_of_another_organization_is_not_found(self) -> None:
        other_org = Organization.objects.create(name="Otra")
        other_template = FormTemplate.objects.create(org=other_org, name="Ajena")
        with self.assertRaises(PolicyDenied) as raised:
            publish_version(self.ctx, template_id=other_template.pk, definition=INSPECCION_V1)
        self.assertEqual(raised.exception.status_code, 404)

    def test_a_field_agent_cannot_publish(self) -> None:
        agent = RequestContext(
            user_id=uuid.uuid4(), org_id=self.org.pk, roles=frozenset({Role.FIELD_AGENT})
        )
        with self.assertRaises(PolicyDenied) as raised:
            publish_version(agent, template_id=self.template.pk, definition=INSPECCION_V1)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(FormTemplateVersion.objects.count(), 0)


@skip_without_db
class ImmutabilityTests(TestCase):
    """Criterio de salida: una versión publicada no se edita, se publica otra."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Org")
        self.template = FormTemplate.objects.create(org=self.org, name="Inspección")
        self.ctx = RequestContext(
            user_id=uuid.uuid4(), org_id=self.org.pk, roles=frozenset({Role.ADMIN})
        )
        self.version = publish_version(
            self.ctx, template_id=self.template.pk, definition=INSPECCION_V1
        )

    def test_editing_the_definition_of_a_published_version_is_rejected(self) -> None:
        self.version.definition = INSPECCION_V2
        with self.assertRaises(ValidationError) as raised:
            self.version.full_clean()
        self.assertIn("definition", raised.exception.error_dict)

    def test_editing_the_version_number_of_a_published_version_is_rejected(self) -> None:
        self.version.version_number = 7
        with self.assertRaises(ValidationError):
            self.version.full_clean()

    def test_renaming_a_published_version_is_allowed(self) -> None:
        """`name` no cambia cómo se interpreta ninguna visita."""
        self.version.name = "Inspección (2026)"
        self.version.full_clean()

    def test_a_draft_version_can_still_be_corrected(self) -> None:
        """La inmutabilidad empieza al **publicar**, no al crear la fila."""
        draft = FormTemplateVersion.objects.create(
            org=self.org,
            template=self.template,
            version_number=99,
            definition=INSPECCION_V1,
            change_seq=self.version.change_seq + 1,
        )
        draft.definition = INSPECCION_V2
        draft.full_clean()

    def test_clean_rejects_a_definition_that_does_not_meet_the_meta_schema(self) -> None:
        """La red del Admin (Paso 24), que valida por `ModelForm`."""
        draft = FormTemplateVersion(
            org=self.org,
            template=self.template,
            version_number=50,
            definition=with_fields({"key": "a", "type": "qml", "label": label("a")}),
            change_seq=self.version.change_seq + 1,
        )
        with self.assertRaises(ValidationError) as raised:
            draft.full_clean()
        self.assertIn("definition", raised.exception.error_dict)

    def test_a_legacy_empty_definition_still_validates(self) -> None:
        """Retrocompatibilidad: las filas anteriores al Paso 20 no se rompen."""
        legacy = FormTemplateVersion(
            org=self.org,
            template=self.template,
            version_number=51,
            definition={},
            change_seq=self.version.change_seq + 2,
        )
        legacy.full_clean()

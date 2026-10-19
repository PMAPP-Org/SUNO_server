"""Validación de `visit.answers` contra la definición de **su propia** versión de plantilla.

Paso 20, tarea 3. **El servidor es la autoridad** (RD-03, ADR-012): el cliente
valida en el dispositivo para dar UX inmediata, pero lo que decide si una
respuesta entra en la base de datos es esta función, ejecutada en el camino de
`POST /v1/sync/push` (ver `sync/services.py`) antes de aceptar la operación.

**Retrocompatibilidad histórica** — la propiedad que ADR-012 pide y que este
módulo hace cierta por construcción: se valida contra la definición de la
`template_version_id` **que la propia visita referencia**, no contra "la última
versión de la plantilla". Publicar una versión nueva, por tanto, no puede
invalidar una visita antigua ni obliga a migrar dato histórico alguno: son
definiciones distintas, cada una viva en su fila inmutable. Esta función ni
siquiera sabe si existe una versión más nueva, y no tiene por dónde enterarse.

**Reglas de validación** (y por qué cada una es como es):

1. **Clave no declarada ⇒ rechazo.** Mismo criterio que `extra="forbid"` en
   `sync/payloads.py`: aceptar y guardar lo que la plantilla no declara sería
   convertir `answers` en un saco de JSON arbitrario que ningún informe podría
   interpretar después — y la puerta por la que se cuela dato que nadie valida.
2. **Campo obligatorio y visible ⇒ tiene que venir contestado.**
3. **Campo no visible ⇒ no se le exige respuesta**, pero si trae valor **sí se
   comprueba su tipo**. Lo primero es obvio (exigir lo que el agente no vio en
   pantalla sería absurdo); lo segundo es la decisión no obvia: se prefiere
   **admitir** el valor de un campo que quedó oculto —tipándolo igual— a
   rechazar el lote. Rechazarlo convertiría un cambio de respuesta que oculta
   una rama ("¿hay planta? → no", con la altura ya escrita) en un lote que la
   outbox reintentaría **para siempre** sin que el agente de campo entienda por
   qué; ese modo de fallo es el que `sync/payloads.py` documenta como veneno y
   pesa más que la pulcritud de no guardar una respuesta huérfana. Lo que no se
   admite en ningún caso es un valor **mal tipado**: eso sí es dato corrupto.
4. **Se acumulan varias violaciones**, hasta `MAX_VIOLATIONS`: un formulario con
   tres errores se corrige de una vez y no en tres viajes. El tope acota el
   tamaño del cuerpo de error.

**Función pura y total**: sin I/O, sin excepciones. Corre dentro de la
transacción del push, donde una excepción no controlada se llevaría por delante
las otras 499 operaciones del lote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from visitacampo.forms.definition import FormDefinition, FormFieldUnion, RepeatField
from visitacampo.forms.visibility import evaluate, is_blank

__all__ = ["MAX_VIOLATIONS", "AnswerViolation", "validate_answers", "violation_details"]

#: Violaciones máximas reportadas en un solo rechazo.
MAX_VIOLATIONS = 20


@dataclass(frozen=True, slots=True)
class AnswerViolation:
    """Una respuesta que no cumple. `field`/`reason`, la forma de `Error.details`.

    El contrato usa literalmente este par como ejemplo de `details`
    (`{"field": "answers.altura_cm", "reason": "out_of_range"}`), así que el
    `field` se emite ya prefijado con `answers.` — es la ruta dentro del
    payload de la operación, que es lo que el cliente necesita para señalar el
    campo en pantalla.
    """

    field: str
    reason: str

    @classmethod
    def of(cls, key: str, reason: str) -> AnswerViolation:
        return cls(field=f"answers.{key}", reason=reason)


def validate_answers(template: FormDefinition, answers: dict[str, Any]) -> list[AnswerViolation]:
    """Violaciones de `answers` frente a `template`. Lista vacía ⇒ válido."""
    return _validate_fields(template.fields, answers)


def _validate_fields(
    fields: list[FormFieldUnion],
    answers: dict[str, Any],
    prefix: str = "",
    *,
    required: bool = True,
) -> list[AnswerViolation]:
    violations: list[AnswerViolation] = []
    declared = {field.key for field in fields}

    for key in answers:
        if key not in declared:
            violations.append(AnswerViolation.of(prefix + key, "unknown_field"))
            if len(violations) >= MAX_VIOLATIONS:
                return violations

    for field in fields:
        value = answers.get(field.key)
        visible = required and evaluate(field.visible_if, answers)
        path = prefix + field.key
        if is_blank(value):
            # Un campo en blanco solo es un problema si se le exigía respuesta.
            if field.required and visible:
                violations.append(AnswerViolation.of(path, "required"))
            elif isinstance(field, RepeatField) and field.min_rows > 0 and visible:
                violations.append(AnswerViolation.of(path, "too_few_rows"))
        else:
            reason = field.check_answer(value)
            if reason is not None:
                violations.append(AnswerViolation.of(path, reason))
            elif isinstance(field, RepeatField) and isinstance(value, list):
                for index, row in enumerate(value):
                    violations.extend(
                        _validate_fields(field.fields, row, f"{path}.{index}.", required=visible)
                    )
                    if len(violations) >= MAX_VIOLATIONS:
                        break
        if len(violations) >= MAX_VIOLATIONS:
            break

    return violations[:MAX_VIOLATIONS]


def violation_details(violations: list[AnswerViolation]) -> dict[str, Any]:
    """`Error.details` a partir de las violaciones (RD-12: sin el valor que falló).

    Se envuelve en `{"errors": [...]}` —igual que
    `core/api_errors.redacted_validation_details`— porque `Error.details` es un
    objeto en el contrato, no un array, y porque así un cliente que ya sabe
    leer los errores de validación de un payload sabe leer estos sin un segundo
    camino de código.
    """
    return {
        "errors": [
            {"field": violation.field, "reason": violation.reason} for violation in violations
        ]
    }

"""Definiciones de plantilla de ejemplo, compartidas por los tests del Paso 20.

Se escriben como `dict` crudos (JSON, no modelos ya parseados) a propósito: lo
que se está probando es la puerta de entrada, y una fixture ya tipada se
saltaría justo la parte que importa.
"""

from __future__ import annotations

from typing import Any


def label(text: str) -> dict[str, str]:
    return {"es": text}


#: La plantilla de referencia: un booleano que condiciona un número, una
#: selección única y un texto opcional. Cubre los tres tipos de regla del DSL
#: (comparación, pertenencia y presencia) sin ser un monstruo.
INSPECCION_V1: dict[str, Any] = {
    "dsl": "visitacampo/form@1",
    "title": label("Inspección de cultivo"),
    "fields": [
        {"key": "tiene_planta", "type": "boolean", "label": label("¿Hay planta?"),
         "required": True},
        {
            "key": "altura_cm",
            "type": "number",
            "label": label("Altura (cm)"),
            "required": True,
            "min": 0,
            "max": 500,
            "visible_if": {"op": "eq", "field": "tiene_planta", "value": True},
        },
        {
            "key": "estado",
            "type": "single_choice",
            "label": label("Estado sanitario"),
            "options": [
                {"value": "sano", "label": label("Sano")},
                {"value": "plaga", "label": label("Con plaga")},
                {"value": "muerto", "label": label("Muerto")},
            ],
        },
        {
            "key": "plaga_observada",
            "type": "text",
            "label": label("¿Qué plaga?"),
            "required": True,
            "max_length": 200,
            "visible_if": {"op": "in", "field": "estado", "value": ["plaga"]},
        },
    ],
}

#: La versión siguiente de la **misma** plantilla: renombra el campo de altura
#: a `altura_m` y hace obligatorio el estado. Sirve para el test de
#: retrocompatibilidad: una visita capturada con `INSPECCION_V1` no cumple esta
#: definición, y eso da igual — se valida contra la suya.
INSPECCION_V2: dict[str, Any] = {
    "dsl": "visitacampo/form@1",
    "title": label("Inspección de cultivo"),
    "fields": [
        {"key": "tiene_planta", "type": "boolean", "label": label("¿Hay planta?"),
         "required": True},
        {
            "key": "altura_m",
            "type": "number",
            "label": label("Altura (m)"),
            "required": True,
            "min": 0,
            "max": 5,
            "visible_if": {"op": "eq", "field": "tiene_planta", "value": True},
        },
        {
            "key": "estado",
            "type": "single_choice",
            "label": label("Estado sanitario"),
            "required": True,
            "options": [
                {"value": "sano", "label": label("Sano")},
                {"value": "plaga", "label": label("Con plaga")},
            ],
        },
    ],
}


def with_fields(*fields: dict[str, Any]) -> dict[str, Any]:
    """Definición mínima con los campos dados."""
    return {"fields": list(fields)}


def text_field(key: str = "nota", **extra: Any) -> dict[str, Any]:
    return {"key": key, "type": "text", "label": label(key), **extra}

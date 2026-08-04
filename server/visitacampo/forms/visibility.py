"""El DSL de visibilidad condicional: **vocabulario cerrado, sin efectos secundarios**.

Este archivo es la decisión de diseño central del Paso 20 y la que cierra por
construcción la clase de vulnerabilidad de H-06. Léase junto al comentario de
arquitectura de `visitacampo/forms/__init__.py`.

**Qué es una expresión aquí**: un objeto JSON con una clave `op` que pertenece
a una allow-list cerrada, y nada más. *No* es una cadena de texto que alguien
parsea. Esa diferencia es la propiedad de seguridad entera:

- No hay lenguaje ⇒ no hay gramática ⇒ no hay parser ⇒ no hay
  desbordamiento de parser, ni escape de comillas, ni inyección.
- No hay identificadores libres ⇒ no hay resolución de nombres ⇒ nada que
  pueda apuntar a una función, un módulo, un atributo o una ruta del sistema.
- No hay llamadas ⇒ no hay `eval`, `exec`, `getattr` dinámico ni acceso a I/O.

Un `{"op": "system"}`, un `{"op": "eval", "code": "..."}` o un
`{"op": "eq", "field": "x", "value": {"__class__": "..."}}` **no se evalúan
nunca**: fallan al validar la plantilla (Pydantic, discriminador cerrado sobre
`op` + `extra="forbid"` + literales restringidos a escalares JSON), que ocurre
al **publicar**, muchísimo antes de que ningún dispositivo vea la plantilla.

--------------------------------------------------------------------------
VOCABULARIO MÍNIMO (v1) — esto es exhaustivo; lo que no está, no existe
--------------------------------------------------------------------------

    {"op": "eq"|"ne",                 "field": <clave>, "value": <escalar>}
    {"op": "gt"|"gte"|"lt"|"lte",     "field": <clave>, "value": <escalar>}
    {"op": "in"|"not_in",             "field": <clave>, "value": [<escalar>, ...]}
    {"op": "is_empty"|"is_not_empty", "field": <clave>}
    {"op": "not", "operand": <expresión>}
    {"op": "and"|"or", "operands": [<expresión>, <expresión>, ...]}

donde:

- `<clave>` es la clave de **otro campo de la misma plantilla**, declarado
  **antes** que el campo que la referencia (`definition.py` lo comprueba). La
  restricción "solo hacia atrás" no es cosmética: hace el grafo de dependencias
  acíclico **por construcción**, así que evaluar la visibilidad de un
  formulario es un recorrido lineal y no puede haber ciclos, ni punto fijo, ni
  no-terminación. Un `A visible si B` + `B visible si A` es imposible de
  expresar, no algo que haya que detectar.
- `<escalar>` es un literal JSON escalar: string, número, booleano o `null`.
  **No** se admiten objetos ni arrays anidados como valor (salvo el array plano
  de `in`/`not_in`), porque un literal estructurado es lo primero que se usaría
  para colar carga útil que alguien acabe interpretando.
- El anidamiento está acotado (`MAX_EXPR_DEPTH`) y el número de operandos
  también (`MAX_OPERANDS`): el coste de evaluar una expresión es acotado y
  conocido antes de evaluarla.

**Lo que deliberadamente NO está en v1**, y por qué:

- Aritmética (`a + b`, `sum(...)`) y funciones de cualquier clase: es el primer
  escalón hacia un intérprete. ADR-012 ya fija la salida cuando hace falta
  lógica que el DSL no expresa: se implementa **en el binario** (Dart/Python) y
  se referencia por nombre desde un catálogo cerrado — una release, no un
  intérprete.
- Expresiones regulares (ni aquí ni como validación de campo en
  `definition.py`): una regex es un programa, con su coste de ejecución
  dependiente del dato (ReDoS), y la plantilla la escribe un back-office cuyo
  contenido acabaría evaluándose en el teléfono del agente de campo.
- Referencias a datos fuera del formulario (usuario, fecha del sistema,
  ubicación): la evaluación tiene que ser una **función pura de las respuestas
  del propio formulario**, reproducible en el servidor y en el cliente y
  auditable a posteriori sobre una visita histórica.

Ampliar el vocabulario es una decisión consciente que se toma aquí, con su
justificación escrita, y que obliga a versionar el dialecto
(`definition.DSL_VERSION`) — no algo que se cuele en un PR de otra cosa.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

__all__ = [
    "MAX_EXPR_DEPTH",
    "MAX_OPERANDS",
    "OPERATORS",
    "AndOr",
    "Comparison",
    "FieldKey",
    "Membership",
    "Not",
    "Presence",
    "VisibilityExpr",
    "evaluate",
    "expression_depth",
    "is_blank",
    "referenced_fields",
]

#: Clave de campo: `snake_case` ASCII, acotada. Es un **identificador**, no
#: texto para el usuario (eso es `label`, i18n), así que restringirlo no le
#: quita expresividad a nadie y sí quita de en medio claves con espacios,
#: puntos, `$`/`__` o unicode confundible que luego alguien intentaría
#: interpretar como una ruta de acceso.
FieldKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

#: Literal admitido en una comparación. `bool` va primero a propósito: en
#: Python `bool` es subclase de `int` y un orden distinto podría normalizar
#: `true` a `1`.
JsonScalar = bool | int | float | str | None

#: Profundidad máxima de anidamiento de una expresión (`and`/`or`/`not`).
MAX_EXPR_DEPTH = 6

#: Operandos máximos de un `and`/`or`.
MAX_OPERANDS = 8

#: Valores máximos en un `in`/`not_in`.
MAX_MEMBERSHIP_VALUES = 64


class _Expr(BaseModel):
    """Base de toda expresión: sellada, inmutable y sin campos extra.

    `extra="forbid"` es parte de la propiedad de seguridad, no una manía:
    una clave desconocida dentro de una expresión —`{"op": "eq", ...,
    "exec": "..."}`— se **rechaza**, en vez de ignorarse en silencio y quedar
    ahí esperando a que una versión futura del renderer la mire.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Comparison(_Expr):
    """`campo <op> literal` con los seis operadores de comparación."""

    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    field: FieldKey
    value: JsonScalar


class Membership(_Expr):
    """`campo ∈ conjunto` / `campo ∉ conjunto` sobre un array plano de escalares."""

    op: Literal["in", "not_in"]
    field: FieldKey
    value: list[JsonScalar] = Field(min_length=1, max_length=MAX_MEMBERSHIP_VALUES)


class Presence(_Expr):
    """"¿tiene respuesta?" — el único predicado unario sobre un campo."""

    op: Literal["is_empty", "is_not_empty"]
    field: FieldKey


class Not(_Expr):
    op: Literal["not"]
    operand: VisibilityExpr


class AndOr(_Expr):
    op: Literal["and", "or"]
    operands: list[VisibilityExpr] = Field(min_length=2, max_length=MAX_OPERANDS)


#: **La** allow-list. Pydantic la aplica como unión discriminada por `op`, así
#: que un `op` que no esté aquí no produce "una expresión rara": produce un
#: error de validación de la plantilla, con el `loc` exacto.
VisibilityExpr = Annotated[
    Comparison | Membership | Presence | Not | AndOr,
    Field(discriminator="op"),
]

Not.model_rebuild()
AndOr.model_rebuild()

#: Los operadores del vocabulario, para tests y documentación. Se derivan de
#: los propios modelos: una allow-list escrita dos veces es una allow-list que
#: acabará divergiendo.
OPERATORS: frozenset[str] = frozenset(
    value
    for model in (Comparison, Membership, Presence, Not, AndOr)
    for value in model.model_fields["op"].annotation.__args__  # type: ignore[union-attr]
)


# --- Inspección estructural (pura) -----------------------------------------


def expression_depth(expr: VisibilityExpr) -> int:
    """Profundidad de anidamiento de la expresión (una comparación vale 1).

    Recursiva y no iterativa a propósito: se aplica **después** de que
    `definition.check_json_depth` haya acotado la profundidad del JSON crudo,
    así que aquí el árbol ya está acotado y no hay `RecursionError` posible.
    """
    if isinstance(expr, Not):
        return 1 + expression_depth(expr.operand)
    if isinstance(expr, AndOr):
        return 1 + max(expression_depth(operand) for operand in expr.operands)
    return 1


def referenced_fields(expr: VisibilityExpr) -> set[str]:
    """Claves de campo que la expresión lee. Base de la regla "solo hacia atrás"."""
    if isinstance(expr, Not):
        return referenced_fields(expr.operand)
    if isinstance(expr, AndOr):
        return {key for operand in expr.operands for key in referenced_fields(operand)}
    return {expr.field}


# --- Evaluación (pura, total, sin excepciones) ------------------------------


def is_blank(value: Any) -> bool:
    """"Sin responder": `None`, cadena vacía, lista vacía u objeto vacío.

    `False` y `0` **no** son vacíos: son respuestas legítimas de un campo
    booleano o numérico, y tratarlas como ausencia es el clásico bug de
    "truthiness" que hace desaparecer un campo obligatorio contestado con 0.
    """
    return value is None or value == "" or value == [] or value == {}


def _same_kind(left: Any, right: Any) -> bool:
    """`True` si ambos valores son del mismo "tipo JSON" comparable.

    Separa `bool` de los números a mano: en Python `True == 1`, y sin esto un
    `{"op": "eq", "field": "activo", "value": 1}` sobre un booleano daría
    cierto. Los tipos de JSON no se comparan entre sí.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if isinstance(left, int | float) or isinstance(right, int | float):
        return isinstance(left, int | float) and isinstance(right, int | float)
    return type(left) is type(right)


def _equals(left: Any, right: Any) -> bool:
    return _same_kind(left, right) and left == right


def _ordered(op: str, left: Any, right: Any) -> bool:
    """`gt`/`gte`/`lt`/`lte` sobre pares comparables; `False` en cualquier otro caso.

    Comparables son los números entre sí y las cadenas entre sí (que es lo que
    hace que `date`/`datetime` en ISO 8601 —el formato que este DSL exige—
    ordenen correctamente por comparación léxica). Cualquier otra combinación
    devuelve `False` en vez de lanzar: el evaluador es **total**, porque corre
    dentro del bucle de push y una excepción aquí sería un 500 provocable con
    una respuesta mal tipada.
    """
    if not _same_kind(left, right) or isinstance(left, bool) or left is None:
        return False
    if not isinstance(left, int | float | str):
        return False
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    return left <= right


def evaluate(expr: VisibilityExpr | None, answers: dict[str, Any]) -> bool:
    """¿Es visible el campo cuya condición es `expr`, dadas estas respuestas?

    Función **pura y total**: sin I/O, sin efectos secundarios, sin excepciones
    y determinista. `None` (campo sin condición) es visible siempre.

    `answers` se lee tal y como llegó, sin normalizar: la visibilidad decide
    qué campos son exigibles y no puede depender de que el resto de la
    validación haya pasado ya.
    """
    if expr is None:
        return True
    if isinstance(expr, AndOr):
        results = (evaluate(operand, answers) for operand in expr.operands)
        return all(results) if expr.op == "and" else any(results)
    if isinstance(expr, Not):
        return not evaluate(expr.operand, answers)

    value = answers.get(expr.field)
    if isinstance(expr, Presence):
        blank = is_blank(value)
        return blank if expr.op == "is_empty" else not blank
    if isinstance(expr, Membership):
        found = any(_equals(value, candidate) for candidate in expr.value)
        return found if expr.op == "in" else not found
    if expr.op == "eq":
        return _equals(value, expr.value)
    if expr.op == "ne":
        return not _equals(value, expr.value)
    return _ordered(expr.op, value, expr.value)

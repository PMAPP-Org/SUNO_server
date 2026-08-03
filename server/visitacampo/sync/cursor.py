"""El cursor opaco del pull (contrato `components.schemas.Cursor`).

El contrato es explícito: "Cursor opaco... el cliente lo almacena y lo
reenvía tal cual, sin interpretarlo." Este módulo es la única puerta por la
que un `change_seq` (entero, la fuente real de verdad de `changeseq.py`) se
convierte en la cadena que el cliente ve, y viceversa.

**Por qué codificar en vez de emitir el `int` desnudo**: el propio contrato
declara `Cursor` como `type: string` con un ejemplo (`'c1:000000000000102394'`)
que no es solo un número — y "opaco" es una promesa deliberada: si un
`change_seq` desnudo viajara como cursor, nada impediría a un cliente
construir uno a mano (`cursor=999999`) para explorar el espacio de otra
organización o saltarse el `409 cursor_expired`. El prefijo `c1:` ata además
el formato del cursor a la versión del *protocolo* de sync (`schema_version`,
no la de la entidad): si algún día el cursor deja de ser un `change_seq`
simple —el *escape hatch* de snapshot que doc 08 §4.4 documenta—, un cursor
`c1:` viejo se reconoce y se rechaza explícitamente en vez de interpretarse
mal.
"""

from __future__ import annotations

__all__ = ["CURSOR_ZERO", "InvalidCursor", "decode_cursor", "encode_cursor"]

#: Prefijo del formato de cursor vigente ("c1" = versión 1 del formato de
#: cursor, no `schema_version` del payload — hoy coinciden en valor por
#: casualidad, no por diseño; son dos números que versionan cosas distintas).
_PREFIX = "c1:"

#: Ancho fijo del campo numérico. `change_seq` es `PositiveBigIntegerField`
#: (BIGINT, máximo 9223372036854775807, 19 dígitos); 20 deja un dígito de
#: margen. El relleno con ceros no es cosmético: mantiene todos los cursores
#: de un mismo prefijo con la misma longitud, así que un cursor truncado a
#: mitad de transporte (un proxy que corta cadenas, un log que trunca) se
#: detecta por longitud en vez de decodificarse silenciosamente a un valor
#: menor.
_WIDTH = 20


class InvalidCursor(ValueError):
    """El cursor no tiene la forma que este servidor pudo haber emitido.

    Cubre tanto un cursor ajeno a este formato (un cliente que lo construyó a
    mano, o el *escape hatch* de un formato de cursor futuro) como uno
    truncado o corrupto. En los dos casos la respuesta correcta es la misma:
    rechazar explícitamente, nunca adivinar un `change_seq`.

    Hereda de `ValueError` mecánicamente (para poder distinguir "esto no es un
    cursor válido" de otros defectos con una guarda de tipo), pero **no** se
    deja caer hasta el manejador global de `ValueError` de `api_errors.py`
    —ese manejador es para cuerpos JSON ilegibles y su mensaje sería
    engañoso—: `sync/api.py` la atrapa y la traduce a un `400` con un mensaje
    específico de cursor.
    """


def encode_cursor(change_seq: int) -> str:
    """El `change_seq` como cursor opaco (`c1:` + `change_seq` con ceros a la izquierda)."""
    if change_seq < 0:
        raise ValueError(f"change_seq no puede ser negativo: {change_seq}")
    return f"{_PREFIX}{change_seq:0{_WIDTH}d}"


#: El cursor de un cliente que nunca ha sincronizado nada (equivalente a
#: `change_seq=0`, por debajo del primer valor real que asigna `changeseq.py`
#: — ver `INITIAL_VERSION`/`change_seq` en `core/models/base.py`).
CURSOR_ZERO = encode_cursor(0)


def decode_cursor(cursor: str) -> int:
    """El `change_seq` que codifica `cursor`, o `InvalidCursor` si no lo es."""
    digits = cursor.removeprefix(_PREFIX)
    if digits == cursor or len(digits) != _WIDTH or not digits.isdigit():
        raise InvalidCursor(f"Cursor is not a recognized value: {cursor!r}.")
    return int(digits)

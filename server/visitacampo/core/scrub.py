"""Redacción de secretos (RD-12): una implementación, dos consumidores.

Doc 08 RD-12 lo pide en dos sitios a la vez y con las mismas claves: *"Toda
salida de job pasa por un `scrubber` con allow-list de campos antes de
persistirse… Logging estructurado con redacción de claves conocidas
(`password`, `token`, `authorization`, `dsn`, `secret`)"*. El Paso 19 (tarea 7)
lo repite: **también** a los logs generales del backend, no solo a
`job.output`. Por eso esto vive en `core/` y no en `jobs/`: si el scrubber de
los jobs fuera uno y el de los logs otro, el día que alguien añadiera una
clave sensible la añadiría en uno de los dos, y el hallazgo **H-17** volvería
por el lado que se quedó atrás.

Es el mismo espíritu que `api_errors.redacted_validation_details` (RD-12 en el
borde HTTP): allí se decide *no emitir* el valor que falló; aquí se decide
*sustituirlo* cuando ya viaja dentro de una estructura o de un texto libre.

**Dos mecanismos, porque hay dos formas de filtrar un secreto:**

1. **Por clave** (`scrub`): un `dict` con `{"password": "hunter2"}` se redacta
   por el nombre de la clave, mire lo que mire su valor. La coincidencia es
   por *subcadena* y sin distinguir mayúsculas, así que `access_token`,
   `HTTP_AUTHORIZATION`, `DATABASE_DSN` y `client_secret` caen igual que las
   cinco claves canónicas. Se prefiere el falso positivo: redactar de más un
   contador llamado `tokens_emitidos` es barato; no redactar un
   `refresh_token`, no.
2. **Por texto** (`scrub_text`): un traceback es una cadena, no un `dict`, y
   el secreto que el Paso 19 tiene que demostrar que no se persiste es
   exactamente ese —una cadena de conexión con contraseña dentro de un
   `str(exc)`—. La redacción por clave no lo vería nunca. Aquí se atacan las
   dos formas en que un secreto aparece dentro de un texto: `clave=valor` /
   `"clave": "valor"`, y el *userinfo* de una URL
   (`postgres://user:secreto@host/db`).

**Lo que este módulo no promete**: no es un detector universal de secretos. Un
token suelto en medio de una frase, sin clave que lo nombre ni URL que lo
contenga, sale intacto. La defensa real contra eso es la de doc 08 N-16 —los
workers **no reciben secretos en el payload**, los leen de la configuración
del proceso—, y este módulo es la segunda línea, no la primera.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "MAX_DEPTH",
    "REDACTED",
    "SENSITIVE_KEY_PARTS",
    "ScrubbingFilter",
    "ScrubbingFormatter",
    "is_sensitive_key",
    "scrub",
    "scrub_text",
]

#: El marcador que sustituye a un valor redactado. Constante y reconocible: un
#: test puede afirmar "aquí hubo algo y se redactó", que es distinto de "aquí
#: no había nada".
REDACTED = "[REDACTED]"

#: Las cinco claves que nombran doc 08 RD-12 y el Paso 19 (tarea 7), literales.
#: Se comparan como subcadena en minúsculas — ver el docstring del módulo.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "token",
    "authorization",
    "dsn",
    "secret",
)

#: Profundidad máxima que se recorre. Una estructura más anidada que esto no se
#: emite a medio redactar: se sustituye entera por el marcador. Fallar hacia el
#: lado que no filtra es la única opción defendible cuando el dato es hostil
#: (mismo criterio que `RecursionError` → `400` en `api_errors.py`).
MAX_DEPTH = 12

#: `clave = valor` / `"clave": "valor"` dentro de un texto libre. El valor se
#: toma hasta el primer separador razonable para no comerse el resto de la
#: línea (un traceback tiene mucho texto detrás del secreto).
_KEY_VALUE_RE = re.compile(
    r"(?i)"
    r"(['\"]?\b(?:" + "|".join(SENSITIVE_KEY_PARTS) + r")[a-z0-9_\-]*['\"]?)"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&)\]}>]+)"
)

#: *Userinfo* de una URL: `esquema://usuario:secreto@host`. El usuario se
#: conserva (es diagnóstico útil y no es el secreto); la contraseña, no.
_URL_CREDENTIALS_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@]+):([^/\s@]+)@")


def is_sensitive_key(key: object) -> bool:
    """`True` si el nombre de clave contiene alguna de las partes sensibles."""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_text(text: str) -> str:
    """Redacta secretos **dentro de una cadena** (traceback, mensaje, SQL…).

    Se aplica primero la regla de URL y después la de `clave=valor`: una DSN
    completa (`postgresql://u:p@h/db`) pierde así la contraseña aunque venga
    además introducida por `dsn=`, y no al revés.
    """
    redacted = _URL_CREDENTIALS_RE.sub(rf"\1\2:{REDACTED}@", text)
    return _KEY_VALUE_RE.sub(rf"\1\2{REDACTED}", redacted)


def scrub(value: Any, *, _depth: int = 0) -> Any:
    """Devuelve `value` con los secretos redactados y **serializable a JSON**.

    Las dos garantías van juntas a propósito, porque el destino de esto es una
    columna `jsonb` (`job.output`, `job.error`): un valor que no sepa
    serializarse se convierte en su `repr()` ya redactado en vez de reventar el
    `INSERT` del worker en el peor momento posible (al intentar guardar el
    resultado de un job que **ya se ejecutó**, cuyo trabajo se perdería).

    Reglas, en orden:

    - `dict`: la clave sensible se redacta sin mirar su valor; el resto
      recurre. Las claves se emiten siempre como `str` porque JSON no admite
      otra cosa.
    - `list`/`tuple`/`set`: recurre elemento a elemento (siempre sale `list`).
    - `str`: pasa por `scrub_text`.
    - `bool`/`int`/`float`/`None`: intactos — un número no puede esconder una
      cadena de conexión, y redactarlos haría ilegible cualquier contador.
    - cualquier otra cosa: `scrub_text(repr(value))`.
    """
    if _depth > MAX_DEPTH:
        return REDACTED

    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (REDACTED if is_sensitive_key(key) else scrub(item, _depth=_depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, set | frozenset):
        return [scrub(item, _depth=_depth + 1) for item in sorted(value, key=repr)]
    if isinstance(value, Sequence):
        return [scrub(item, _depth=_depth + 1) for item in value]
    return scrub_text(repr(value))


class ScrubbingFilter(logging.Filter):
    """Redacta el mensaje y los argumentos de cada `LogRecord` (RD-12).

    Se instala como *filter* y no como *formatter* porque un filtro corre
    **una vez por registro**, antes que cualquier handler: aunque mañana haya
    dos handlers con formatos distintos (consola y Sentry, doc 08 §7.4), los
    dos reciben el registro ya redactado y ninguno puede olvidarse.

    Lo que un filtro **no** puede alcanzar es el traceback: `exc_info` no se
    renderiza hasta que un formatter lo pide. De eso se ocupa
    `ScrubbingFormatter`, y por eso los dos se instalan juntos en `LOGGING`
    (`config/settings/base.py`). Un secreto en un traceback es exactamente el
    caso de H-17, así que no basta con uno de los dos.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_text(record.msg)
        elif record.msg is not None:
            record.msg = scrub(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(scrub(arg) for arg in record.args)
        elif isinstance(record.args, Mapping):
            record.args = scrub(record.args)

        # Nunca se descarta un registro: este filtro redacta, no censura.
        # Perder la línea entera dejaría a quien opera el servidor sin la
        # señal de que algo pasó, que es peor que verla redactada.
        return True


class ScrubbingFormatter(logging.Formatter):
    """`logging.Formatter` que redacta el texto **ya renderizado**.

    Es la única capa que ve el traceback formateado, la mezcla de `msg % args`
    y el `stack_info` en una sola cadena. Redactar aquí cuesta una pasada de
    regex por línea de log emitida (no por línea escrita en el código: los
    niveles por debajo del umbral ni llegan).
    """

    def format(self, record: logging.LogRecord) -> str:
        return scrub_text(super().format(record))

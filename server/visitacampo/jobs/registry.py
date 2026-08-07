"""De `job.type` a la función que lo ejecuta. Un diccionario, nada más.

**Por qué un registro explícito y no importar por nombre de módulo.** Un
despachador que hiciera `import_module(job.type)` convertiría una columna de la
base de datos en un nombre de módulo a importar: exactamente la clase de
vulnerabilidad que ADR-012 elimina de los formularios (RD-06, "los datos nunca
son código"). Aquí un `type` desconocido no importa nada, no ejecuta nada y
acaba en `dead_letter` con un error legible.

El registro se puebla al importar `visitacampo.jobs.periodic`; `worker.py` lo
importa por eso. Un handler recibe un `JobRecord` (inmutable) y devuelve el
`dict` que se persistirá como `job.output` —ya redactado por `queue.succeed`—
o `None` si no tiene nada que contar.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from visitacampo.jobs.queue import JobRecord

__all__ = ["JobHandler", "handler_for", "register", "registered_types"]

JobHandler = Callable[[JobRecord], Mapping[str, Any] | None]

_HANDLERS: dict[str, JobHandler] = {}


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorador que asocia un `job.type` con su handler.

    Registrar dos veces el mismo tipo es un error de programación, no una
    sustitución silenciosa: dos handlers para un tipo significa que uno de los
    dos nunca se ejecuta y nadie lo sabría hasta que fallara en producción.
    """

    def _decorate(handler: JobHandler) -> JobHandler:
        if job_type in _HANDLERS and _HANDLERS[job_type] is not handler:
            raise RuntimeError(
                f"Ya hay un handler registrado para el job {job_type!r} "
                f"({_HANDLERS[job_type]!r}). Un tipo, un handler."
            )
        _HANDLERS[job_type] = handler
        return handler

    return _decorate


def handler_for(job_type: str) -> JobHandler | None:
    """El handler de `job_type`, o `None` si no hay ninguno registrado."""
    return _HANDLERS.get(job_type)


def registered_types() -> frozenset[str]:
    return frozenset(_HANDLERS)

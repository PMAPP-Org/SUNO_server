"""Forma del contrato para `GET /v1/jobs/{id}` (`server/openapi/v1.yaml`).

El contrato es la fuente de verdad y aquí no se inventa ni se renombra nada. Lo
que sí conviene decir en voz alta es **lo que no está**: `payload`,
`locked_by`, `locked_until` e `idempotency_key` existen en la tabla y no salen
por la API. El contrato lo declara así ("no expone `payload`, `locked_by` ni
`locked_until`: son detalle interno del worker") y es además la mitad barata de
RD-12: el `payload` es lo único de la fila que no ha pasado por `scrub()` en el
camino de salida —se redacta al encolar—, y el `locked_by` lleva el hostname y
el PID de una máquina interna, que no es asunto de ningún cliente.

Todas las propiedades son `required` (convención del contrato para entidades
emitidas por el servidor): la opcionalidad se expresa con `null` en el valor,
nunca con la ausencia del campo.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from ninja import Schema

from visitacampo.authn.schemas import Timestamp

__all__ = ["JobSchema"]

#: `components.schemas.JobStatus`. Literal cerrado por la misma razón que el
#: `Mime` de adjuntos: los miembros de un `TextChoices` de Django no son
#: constantes en tiempo de análisis para mypy. `jobs/tests/test_api.py`
#: comprueba que estos cinco valores son exactamente los de `JobStatus`.
JobStatusLiteral = Literal["pending", "running", "succeeded", "failed", "dead_letter"]


class JobSchema(Schema):
    """`components.schemas.Job`."""

    id: uuid.UUID
    type: str
    status: JobStatusLiteral
    attempts: int
    max_attempts: int
    run_after: Timestamp | None
    created_at: Timestamp
    finished_at: Timestamp | None
    # `output` viaja como objeto libre: es la salida del job, cuya forma
    # depende del tipo. Lo que sí está garantizado es que ya viene redactada
    # (`queue.succeed` la pasa por `scrub()` antes de persistirla, RD-12).
    output: dict[str, Any] | None
    # `components.schemas.Error` cuando el job acabó en `failed`/`dead_letter`.
    error: dict[str, Any] | None

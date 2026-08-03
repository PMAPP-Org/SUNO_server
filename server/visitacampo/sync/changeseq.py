"""El lock por organización y la asignación de `change_seq` (doc 08 §4.4).

Este archivo es el núcleo de corrección del Paso 8. Doc 08 §4.4, "Cursor de
pull correcto (detalle que suele salir mal)", lo dice sin rodeos:

    `change_seq` se asigna dentro de la transacción de escritura **mientras se
    sostiene `pg_advisory_xact_lock(org_id)`**. Sin ese lock, una secuencia de
    Postgres puede commitear fuera de orden (la tx con `seq=9` commitea
    después que la de `seq=10`) y un cliente que ya avanzó su cursor a 10
    **perdería para siempre** la fila 9.

Es decir: el problema **no** es que dos transacciones se asignen el mismo
número —una secuencia de Postgres ya lo impide—, sino que se asignen números
en un orden distinto al de sus `COMMIT`. Un pull que corra en medio ve la 10
publicada y la 9 todavía no, avanza el cursor a 10, y la 9 aparece después
por debajo del cursor: invisible para siempre. Nada en la fila delata la
pérdida, así que tampoco hay forma de detectarla a posteriori.

El lock lo cierra porque es **`xact`**: se libera en el `COMMIT`, no antes. Al
serializar por organización todo el intervalo "asignar `change_seq` … commitear",
el orden de asignación y el orden de publicación son el mismo orden por
construcción. Ese mismo lock es el **único** mecanismo de invariante de
concurrencia del push (RD-14, corrige H-13): no hay un segundo contador, ni
una tabla de estado paralela, ni un trigger que haya que mantener de acuerdo
con nada.

**De dónde sale el número siguiente.** De los propios datos:
`MAX(change_seq) + 1` sobre las tablas sincronizadas de la organización. No de
una columna contador en `organization`, que sería exactamente el
"double-bookkeeping" que RD-14 prohíbe (dos fuentes de verdad que pueden
divergir, y una migración para crearla). Bajo el lock, ese `MAX` es exacto:
cualquier transacción anterior de la misma organización ya commiteó —soltó el
lock al hacerlo— y sus filas son visibles para la sentencia que lo lee.

**Por qué el `MAX` recorre todas las entidades sincronizadas y no solo
`visit`.** El cursor de pull es uno solo por organización y ordena filas de
las cuatro entidades juntas (contrato `PullResponse.rows`, "orden estricto
ascendente de `change_seq`"). Si el push asignara `MAX(visit.change_seq) + 1`,
podría repetir un número ya usado por un `assignment`, y una página del pull
que cortara justo ahí se saltaría una de las dos filas empatadas.

**Un `change_seq` distinto por fila aplicada, no uno por lote** (`models/base.py`
dejó la decisión abierta a este paso). Motivo: el pull pagina con
`change_seq > cursor ... LIMIT n`. Si 500 filas compartieran número y el
límite cayera en medio del empate, el cliente guardaría el cursor y no
volvería a ver el resto — la misma pérdida silenciosa que el lock evita, por
otra puerta. Con un número por fila, `change_seq` es un orden **total** y el
cursor no puede cortar por dentro de nada.

*Escape hatch* (doc 08 §4.4): si la contención llegara a importar, la salida
documentada es un cursor basado en snapshot (`pg_snapshot_xmin(
pg_current_snapshot())`) o particionar el lock — no quitar el lock.
"""

from __future__ import annotations

import hashlib
import uuid

from django.db import connection, models, transaction

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Assignment, FormTemplateVersion, Visit, VisitAttachment
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "SYNCED_MODELS",
    "SYNC_LOCK_NAMESPACE",
    "ChangeSeqAllocator",
    "acquire_org_lock",
    "highest_change_seq",
    "org_lock_key",
]

#: Las entidades que comparten el cursor de pull de una organización, es decir
#: las subclases concretas de `SyncedModel`. Se listan explícitamente en vez de
#: descubrirlas con `__subclasses__()` para que añadir una entidad sincronizada
#: obligue a pasar por aquí; `tests/test_changeseq.py` comprueba que la lista
#: coincide con las subclases reales, así que la fricción no puede olvidarse.
SYNCED_MODELS: tuple[type[models.Model], ...] = (
    Visit,
    VisitAttachment,
    Assignment,
    FormTemplateVersion,
)

#: Primera clave del advisory lock: el "espacio de nombres" de este lock.
#: `pg_advisory_xact_lock(int, int)` comparte un único espacio global en todo
#: el clúster, así que sin un prefijo propio este lock podría colisionar con
#: cualquier otro uso futuro de advisory locks (la cola de jobs del Paso 19,
#: por ejemplo) y serializarlos entre sí sin que nadie entendiera por qué.
#: `0x56430801` = "VC" (0x5643) + paso 8, operación 1 (push). Cabe en un
#: `int32` con signo (1 447 166 465 < 2³¹−1), que es lo que exige la firma
#: `pg_advisory_xact_lock(int, int)`.
SYNC_LOCK_NAMESPACE = 0x56430801


def org_lock_key(org_id: uuid.UUID) -> int:
    """Segunda clave del lock: la organización, plegada a un `int32` con signo.

    `pg_advisory_xact_lock` no acepta UUID. Se usa BLAKE2b y no `hash()` de
    Python porque `hash()` de un `str`/`bytes` depende de `PYTHONHASHSEED` y
    cambiaría entre procesos: dos workers de gunicorn tomarían **locks
    distintos** para la misma organización y la serialización desaparecería
    justo donde importa, sin síntoma visible.

    Una colisión de 32 bits entre dos organizaciones hace que compartan lock:
    se serializan entre sí de más. Cuesta rendimiento, nunca corrección — el
    riesgo inverso (dos transacciones de la *misma* organización con locks
    distintos) es imposible porque la clave es una función pura del `org_id`.
    """
    digest = hashlib.blake2b(org_id.bytes, digest_size=4).digest()
    return int.from_bytes(digest, "big", signed=True)


def acquire_org_lock(org_id: uuid.UUID) -> None:
    """`SELECT pg_advisory_xact_lock(...)`. **Debe** correr dentro de la transacción.

    La comprobación de `in_atomic_block` no es defensiva por gusto: un
    `pg_advisory_xact_lock` ejecutado en autocommit se adquiere y se libera en
    la misma sentencia, de modo que el código *parecería* estar serializado y
    no lo estaría en absoluto. Es un fallo que ningún test funcional detecta y
    que solo se manifiesta como una fila perdida en producción, así que se
    convierte en un error ruidoso aquí.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "acquire_org_lock() exige una transacción abierta: un "
            "pg_advisory_xact_lock en autocommit se libera de inmediato y no "
            "serializa nada (doc 08 §4.4). Envuelve la llamada en "
            "transaction.atomic()."
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [SYNC_LOCK_NAMESPACE, org_lock_key(org_id)],
        )


def highest_change_seq(ctx: RequestContext) -> int:
    """`MAX(change_seq)` sobre las entidades sincronizadas de la org, o 0.

    Una query por tabla (cuatro hoy), servidas por el índice `(org_id,
    change_seq)` que el Paso 5 creó para el pull: un `MAX` sobre un B-tree es
    un descenso por el extremo, no un escaneo. Es la misma cuenta que necesita
    `ChangeSeqAllocator.for_org` (el siguiente número a repartir) y el
    bootstrap del pull (Paso 9: el cursor inicial es "hasta dónde ya se ha
    entregado todo", es decir, exactamente este valor).
    """
    highest = 0
    for model in SYNCED_MODELS:
        current = scoped_qs(ctx, model).aggregate(top=models.Max("change_seq"))["top"]
        if current is not None and current > highest:
            highest = current
    return highest


class ChangeSeqAllocator:
    """Reparte `change_seq` consecutivos dentro de un lote ya bajo el lock.

    Se construye una vez por request (`for_org`) y a partir de ahí `take()` es
    aritmética en memoria: cero queries dentro del bucle, igual que las
    políticas (RD-07). Que sea seguro hacerlo en memoria es consecuencia
    directa del lock — nadie más de esta organización puede estar repartiendo
    números a la vez.
    """

    __slots__ = ("_next",)

    def __init__(self, start: int) -> None:
        self._next = start

    @property
    def next_value(self) -> int:
        """El número que devolvería el siguiente `take()` (para tests y logs)."""
        return self._next

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value

    @classmethod
    def for_org(cls, ctx: RequestContext) -> ChangeSeqAllocator:
        """`MAX(change_seq) + 1` sobre las entidades sincronizadas de la org."""
        return cls(highest_change_seq(ctx) + 1)

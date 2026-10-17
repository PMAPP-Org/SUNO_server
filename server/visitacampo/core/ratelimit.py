"""Rate limiting por ventana fija sobre PostgreSQL (Paso 15, ADR-008, OWASP A07).

django-axes ya bloquea una **cuenta** tras 5 fallos de contraseña desde la
misma IP, pero no limita el volumen: no frena el relleno de credenciales
repartido entre muchos usuarios, el abuso de `/auth/refresh` ni el de
`/attachments/presign`. Esto sí.

**Cómo cuenta**: una fila por `(clave, inicio de ventana)` y un único
`INSERT … ON CONFLICT DO UPDATE SET hits = hits + 1 RETURNING hits`. Es atómico
entre todos los procesos que comparten la BD, sin Redis (ADR-006), y no tiene
carrera entre leer y escribir. Ventana fija: en el borde entre dos ventanas
puede pasar hasta el doble del límite durante un instante, aceptable para
frenar abuso, que es la intención.

**Privacidad**: la clave es `ámbito:HMAC(identidad)`; nunca se guarda la IP ni
el identificador en claro (mismo criterio que `core/audit.py`).

**Límites**: en `settings.RATE_LIMITS`. Los límites por IP son holgados a
propósito: una cuadrilla de campo comparte IP detrás del NAT de su operador
móvil, y un límite por IP estricto bloquearía a todo el equipo por culpa de
uno. Los límites estrictos van por usuario o por token.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.crypto import salted_hmac
from ninja.errors import HttpError

from visitacampo.core.models import RateLimitBucket

__all__ = ["Limit", "RateLimited", "hit", "limit_for", "purge_expired"]

_KEY_SALT = "visitacampo.core.ratelimit.key"


@dataclass(frozen=True, slots=True)
class Limit:
    """`max_hits` peticiones por `window` para un `scope` (p. ej. `login:ip`)."""

    scope: str
    max_hits: int
    window: timedelta


class RateLimited(HttpError):
    """429 del contrato (`code: rate_limited`, cabecera `Retry-After`)."""

    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__(429, "Too many requests.")
        self.retry_after = retry_after


def limit_for(scope: str) -> Limit:
    """El `Limit` configurado para `scope` en `settings.RATE_LIMITS`."""
    max_hits, window_seconds = settings.RATE_LIMITS[scope]
    return Limit(scope=scope, max_hits=int(max_hits), window=timedelta(seconds=window_seconds))


def _key(scope: str, identity: str) -> str:
    digest = salted_hmac(_KEY_SALT, identity, algorithm="sha256").hexdigest()
    return f"{scope}:{digest}"


def _window_start(now: datetime, window: timedelta) -> datetime:
    seconds = window.total_seconds()
    return datetime.fromtimestamp(math.floor(now.timestamp() / seconds) * seconds, tz=now.tzinfo)


def hit(scope: str, identity: str | None, *, now: datetime | None = None) -> None:
    """Cuenta una petición; lanza `RateLimited` si supera el límite de `scope`.

    Sin identidad (`None`, p. ej. una IP que no se pudo determinar) no se
    cuenta: es preferible no limitar a limitar a todos bajo la misma clave.
    """
    if not identity:
        return
    limit = limit_for(scope)
    moment = now or timezone.now()
    start = _window_start(moment, limit.window)
    table = connection.ops.quote_name(RateLimitBucket._meta.db_table)
    with connection.cursor() as cursor:
        # Identificadores fijos del modelo; todos los valores van en params.
        cursor.execute(
            f"INSERT INTO {table} (key, window_start, hits) VALUES (%s, %s, 1) "  # nosec B608
            f"ON CONFLICT (key, window_start) DO UPDATE SET hits = {table}.hits + 1 "  # nosec B608
            "RETURNING hits",
            [_key(scope, identity), start],
        )
        (hits,) = cursor.fetchone()
    if hits > limit.max_hits:
        retry_after = math.ceil((start + limit.window - moment).total_seconds())
        raise RateLimited(retry_after=max(retry_after, 1))


def purge_expired(*, older_than: datetime) -> int:
    """Borra las ventanas que empezaron antes de `older_than`. Devuelve cuántas."""
    table = connection.ops.quote_name(RateLimitBucket._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {table} WHERE window_start < %s", [older_than])  # nosec B608
        return cursor.rowcount

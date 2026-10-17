"""Capa de servicios de autenticación: login, refresh y logout.

ADR-004 ("la lógica vive en la capa de servicios, no en las vistas"): las
vistas de `authn/api.py` traducen JSON y nada más; todo lo que decide vive
aquí. Referencias: **ADR-008** (esquema completo de autenticación),
**ADR-013** (`RequestContext` resuelto una vez, antes de cualquier bucle),
**RD-09/H-14** (nada utilizable en la BD), **RD-10** (rotación sin carrera),
**RD-11** (aislamiento por organización vía `scoped_qs`).

**Alcance de Fase 0** (el brief lo delimita explícitamente): este paso
implementa login, emisión de JWT, registro de dispositivo, rotación de refresh
y logout. **No** implementa —y el Paso 15 sí— la detección de reuso con
revocación de familia y la ventana de gracia de 30 s para reintentos
concurrentes. Lo que se hace hoy con un token reutilizado es **rechazarlo** y
dejar un `AuditEvent` `auth.refresh_rejected` (línea base ISO 27001, A.8.15;
qué se audita y qué no, en `core/audit.py`): es el comportamiento seguro,
solo que todavía no reacciona revocando la familia. El rate limiting de login
lo hace `django-axes` (ver `log_in`).

**Por qué el patrón `selector.verifier` se implementa ya y no en el Paso 15**:
diferirlo obligaría a escribir primero la versión con el token en claro, que
es literalmente el hallazgo H-14. Un hallazgo introducido "temporalmente"
sigue siendo un hallazgo, y sigue estando en los backups.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from axes.helpers import get_client_ip_address
from axes.utils import reset as reset_axes_attempts
from django.conf import settings
from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.utils import timezone
from ninja.errors import HttpError

from visitacampo.authn import tokens
from visitacampo.authn.lookups import find_refresh_token_by_selector
from visitacampo.authn.models import RefreshToken
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, Device
from visitacampo.core.scoping import scoped_qs

__all__ = [
    "DeviceRegistration",
    "InvalidCredentials",
    "IssuedSession",
    "LoginResult",
    "log_out_session",
    "log_in",
    "refresh_session",
]

DEFAULT_REFRESH_TTL_SECONDS = 60 * 60 * 24 * 60  # 60 días con uso (ADR-008)

#: Hash de relleno contra el que comparar cuando el `selector` no existe, para
#: que el camino "token desconocido" haga el mismo trabajo criptográfico que
#: el camino "verifier incorrecto". Es el equivalente en el refresh del hash
#: "dummy" que `ModelBackend` ejecuta en el login (ADR-008, "respuestas y
#: tiempos uniformes"). Su valor no es un secreto: es imposible que coincida
#: con `sha256(x)` de nada porque no es el hash de ningún verifier conocido.
_DUMMY_VERIFIER_HASH = "0" * tokens.VERIFIER_HASH_CHARS


class InvalidCredentials(HttpError):
    """401 **genérico**: no dice qué falló, ni si el usuario existe.

    Es la única excepción que login y refresh pueden lanzar ante material
    inválido. Mensaje y código idénticos en todos los casos (usuario
    inexistente, contraseña incorrecta, cuenta desactivada, sin membresía,
    token malformado, verifier incorrecto, token caducado, revocado o ya
    rotado): distinguirlos daría un oráculo de enumeración, que es
    exactamente lo que ADR-008 prohíbe.
    """

    code = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__(401, "Invalid credentials.")


class DeviceUnavailable(HttpError):
    """422: ese `device_id` ya está registrado y no es de quien lo presenta.

    Fail-closed. El `device_id` lo genera el cliente, así que dos usuarios
    podrían presentar el mismo (colisión imposible en la práctica con UUID, o
    intento deliberado). Antes que reasignar el dispositivo —lo que movería
    una fila entre propietarios, y potencialmente entre organizaciones— se
    rechaza el login. El mensaje no confirma a quién pertenece.
    """

    code = "validation_error"

    def __init__(self) -> None:
        super().__init__(422, "The provided device_id is not available for this account.")


@dataclass(frozen=True, slots=True)
class DeviceRegistration:
    """`DeviceInfo` del contrato, ya validado por el esquema de la vista."""

    device_id: uuid.UUID
    platform: str
    app_version: str
    os_version: str | None = None
    model: str | None = None
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Lo que se devuelve al cliente. `refresh_token` viaja **una sola vez**."""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    server_time: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    session: IssuedSession
    user: AppUser
    org_id: uuid.UUID
    roles: tuple[str, ...]


def _refresh_ttl_seconds() -> int:
    return int(getattr(settings, "JWT_REFRESH_TTL_SECONDS", DEFAULT_REFRESH_TTL_SECONDS))


def _sorted_roles(ctx: RequestContext) -> tuple[str, ...]:
    """Roles en orden estable.

    `ctx.roles` es un `frozenset` (pertenencia O(1) para las políticas), y su
    orden de iteración depende del hash de los `str`, que varía entre procesos
    por `PYTHONHASHSEED`. Un JWT y un `UserProfile` cuyo array de roles cambie
    de orden entre peticiones sería ruido innecesario en logs, en tests y en
    cualquier caché del cliente.
    """
    return tuple(sorted(str(role) for role in ctx.roles))


def _issue_session(
    ctx: RequestContext,
    *,
    device: Device | uuid.UUID,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    now: datetime,
) -> IssuedSession:
    """Crea la fila de refresco y firma el access token.

    El `verifier` existe en memoria el tiempo de esta función: se hashea para
    la BD y se devuelve al cliente. **Nunca** se escribe en la fila, ni se
    registra en un log (RD-09/H-14, RD-12).
    """
    secret = tokens.generate_refresh_secret()
    refresh_ttl = _refresh_ttl_seconds()

    row = RefreshToken(
        org_id=ctx.org_id,
        selector=secret.selector,
        verifier_hash=tokens.hash_verifier(secret.verifier),
        device_id=device.pk if isinstance(device, Device) else device,
        user_id=user_id,
        family_id=family_id,
        expires_at=now + timedelta(seconds=refresh_ttl),
    )
    row.save(force_insert=True)

    access = tokens.issue_access_token(
        user_id=user_id,
        org_id=ctx.org_id,
        device_id=row.device_id,
        roles=_sorted_roles(ctx),
    )
    return IssuedSession(
        access_token=access.token,
        expires_in=access.expires_in,
        refresh_token=secret.token,
        refresh_expires_in=refresh_ttl,
        server_time=now,
    )


def _register_device(ctx: RequestContext, user: AppUser, info: DeviceRegistration) -> Device:
    """Crea o actualiza el `Device` del contrato (`DeviceInfo`).

    La búsqueda va por `scoped_qs` (RD-11): un dispositivo de otra
    organización sencillamente no se ve, así que no hay forma de actualizarlo
    por accidente. Que "no se vea" no significa que se pueda crear encima: la
    PK es global, y el `IntegrityError` de la inserción es lo que convierte
    ese caso en un 422 en vez de en una fila robada.
    """
    existing = scoped_qs(ctx, Device).filter(pk=info.device_id).first()
    if existing is not None:
        if existing.owner_id != user.pk:
            raise DeviceUnavailable
        existing.platform = info.platform
        existing.os_version = info.os_version
        existing.model = info.model
        existing.app_version = info.app_version
        existing.display_name = info.display_name
        existing.save(
            update_fields=["platform", "os_version", "model", "app_version", "display_name"]
        )
        return existing

    device = Device(
        id=info.device_id,
        org_id=ctx.org_id,
        owner_id=user.pk,
        platform=info.platform,
        os_version=info.os_version,
        model=info.model,
        app_version=info.app_version,
        display_name=info.display_name,
    )
    try:
        # `atomic()` anidado: sin este savepoint, el `IntegrityError` dejaría
        # la transacción de la request rota y el 422 se convertiría en un 500.
        with transaction.atomic():
            device.save(force_insert=True)
    except IntegrityError as exc:
        raise DeviceUnavailable from exc
    return device


def log_in(request, username: str, password: str, info: DeviceRegistration) -> LoginResult:
    """`POST /v1/auth/login` — valida credenciales, registra el dispositivo y emite tokens.

    Se usa `django.contrib.auth.authenticate()` en vez de buscar el usuario a
    mano por dos motivos: verifica la contraseña con el hasher configurado
    (Argon2id, Paso 4) y, cuando el usuario **no existe**, ejecuta igualmente
    un hash "dummy" — la uniformidad de temporización que ADR-008 exige contra
    la enumeración de usuarios. `ModelBackend` rechaza además a los usuarios
    inactivos, sin que haya que acordarse de comprobarlo.

    **No** se aplica `policies.can_manage_device` aquí, aunque el dispositivo
    se cree en esta función. Esa política responde "¿puede este actor
    administrar *este* dispositivo?" (renombrarlo, revocarlo desde el
    back-office del Paso 17) y deja fuera a `VIEWER`; usarla como puerta del
    login dejaría a un observador sin poder entrar en la app siquiera para
    consultar, que no es lo que ADR-011 describe para ese rol. Lo que sí se
    exige —y es la comprobación de autorización real de este endpoint— es
    tener **alguna** membresía en la organización: sin roles no hay contexto
    que emitir y se deniega (deny-by-default, ADR-013).
    """
    user = authenticate(request, username=username, password=password)
    if not isinstance(user, AppUser):
        raise InvalidCredentials

    # django-axes (ADR-004): `AXES_RESET_ON_SUCCESS` solo se dispara con la
    # señal `django.contrib.auth.signals.user_logged_in`, que únicamente
    # emite `django.contrib.auth.login()`. Este endpoint es JWT sin sesión y
    # nunca llama a esa función, así que sin esta línea axes jamás olvidaría
    # los intentos fallidos de una cuenta/IP tras un login legítimo — el
    # contador crecería para siempre y un usuario real podría acabar
    # bloqueado por fallos de tecleo antiguos ya sin relevancia. Se usa la
    # API pública de axes (`axes.utils.reset`), no lógica de rate limiting
    # propia; verificado en `authn/tests/test_axes_lockout.py`
    # (`test_a_successful_login_resets_the_failure_counter`).
    client_ip = get_client_ip_address(request)
    if settings.AXES_RESET_ON_SUCCESS:
        reset_axes_attempts(ip=client_ip, username=user.get_username())

    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not ctx.is_member:
        audit.record(
            ctx,
            action=AuditAction.LOGIN_DENIED,
            target=f"user:{user.pk}",
            metadata={"reason": "no_membership"},
            ip=client_ip,
        )
        raise InvalidCredentials

    now = timezone.now()
    try:
        with transaction.atomic():
            device = _register_device(ctx, user, info)
            session = _issue_session(
                ctx,
                device=device,
                user_id=user.pk,
                # Login nuevo ⇒ familia nueva. Las familias anteriores del mismo
                # dispositivo se dejan como están: revocarlas aquí echaría al
                # propio usuario de una sesión que quizá sigue usando, y la
                # revocación explícita es lo que hace el logout — que revoca por
                # `device_id`, así que cualquier familia anterior del mismo
                # dispositivo cae con ella (ver `log_out_session`).
                family_id=uuid.uuid4(),
                now=now,
            )
            # Dentro de la misma transacción: no existe una sesión emitida
            # sin su traza (si la escritura de auditoría falla, falla el login).
            audit.record(
                ctx,
                action=AuditAction.LOGIN,
                target=f"device:{info.device_id}",
                metadata={"platform": info.platform, "app_version": info.app_version},
                ip=client_ip,
            )
    except DeviceUnavailable:
        # Fuera de la transacción: la que lanzó la excepción ya se deshizo y
        # se llevaría la fila consigo.
        audit.record(
            ctx,
            action=AuditAction.DEVICE_CONFLICT,
            target=f"device:{info.device_id}",
            ip=client_ip,
        )
        raise
    return LoginResult(session=session, user=user, org_id=ctx.org_id, roles=_sorted_roles(ctx))


def _reject_refresh(
    ctx: RequestContext,
    row: RefreshToken,
    reason: str,
    *,
    presented_device_id: uuid.UUID,
    client_ip: str | None,
) -> InvalidCredentials:
    """Audita un refresh con material auténtico rechazado y devuelve el 401.

    Solo se llama con `selector.verifier` ya verificado: quien lo presenta
    tiene el token de verdad, así que la fila no da pistas a nadie que no las
    tuviera ya, y es justo la señal de robo o reuso que interesa conservar.
    """
    audit.record(
        ctx,
        action=AuditAction.REFRESH_REJECTED,
        target=f"device:{row.device_id}",
        actor_id=row.user_id,
        metadata={
            "reason": reason,
            "presented_device_id": str(presented_device_id),
            "family_id": str(row.family_id),
        },
        ip=client_ip,
    )
    return InvalidCredentials()


def refresh_session(
    raw_token: str, device_id: uuid.UUID, *, client_ip: str | None = None
) -> IssuedSession:
    """`POST /v1/auth/refresh` — valida `selector.verifier`, **rota** y reemite.

    Se rota (y no solo se reemite el access token) porque el contrato responde
    un `TokenPair` completo, con `refresh_token` obligatorio: el cliente
    espera un refresco nuevo. Lo que **no** se hace todavía es la detección de
    reuso de ADR-008 —si llega un `verifier` ya rotado se revoca toda la
    familia, se emite un `AuditEvent` y se avisa al usuario, con una ventana
    de gracia de 30 s para reintentos concurrentes—: eso es el Paso 15. Aquí
    un token ya rotado se rechaza con el 401 genérico.

    **La carrera de RD-10** sí está cerrada desde ya: la rotación es un
    `UPDATE ... WHERE rotated_at IS NULL AND revoked_at IS NULL`, es decir un
    compare-and-set atómico. Si dos refrescos concurrentes presentan el mismo
    token, exactamente uno actualiza una fila y el otro ve `0` y se rechaza;
    no hay ventana entre "leer" y "marcar" en la que ambos ganen.
    """
    parsed = tokens.parse_refresh_token(raw_token)
    if parsed is None:
        raise InvalidCredentials
    selector, verifier = parsed

    row = find_refresh_token_by_selector(selector)
    if row is None:
        # Mismo trabajo que en el camino "existe pero no coincide".
        tokens.verifier_matches(verifier, _DUMMY_VERIFIER_HASH)
        raise InvalidCredentials

    if not tokens.verifier_matches(verifier, row.verifier_hash):
        raise InvalidCredentials

    now = timezone.now()
    ctx = resolve_request_context(
        Principal(user_id=row.user_id, org_id=row.org_id, device_id=row.device_id)
    )

    def reject(reason: str) -> InvalidCredentials:
        return _reject_refresh(
            ctx, row, reason, presented_device_id=device_id, client_ip=client_ip
        )

    # Un refresh está atado a su dispositivo (ADR-008, "un refresh por
    # dispositivo"): presentarlo desde otro `device_id` es material robado.
    if row.device_id != device_id:
        raise reject("wrong_device")
    # Ya rotado, revocado o caducado: con un token ya rotado es reuso, la
    # señal que el Paso 15 convertirá en revocación de toda la familia.
    if not row.is_usable(now):
        raise reject("not_usable")
    # Quitarle las membresías a alguien tiene que cortarle la sesión en cuanto
    # caduque su access token (15 min), sin esperar a los 60 días del refresh.
    if not ctx.is_member:
        raise reject("no_membership")

    with transaction.atomic():
        rotated = (
            scoped_qs(ctx, RefreshToken)
            .filter(pk=row.pk, rotated_at__isnull=True, revoked_at__isnull=True)
            .update(rotated_at=now)
        )
        if rotated == 1:
            return _issue_session(
                ctx,
                device=row.device_id,
                user_id=row.user_id,
                family_id=row.family_id,
                now=now,
            )
    # Otro refresh concurrente ganó el compare-and-set (RD-10). Se audita
    # fuera de la transacción para no depender de su resultado.
    raise reject("rotation_race")


def log_out_session(
    ctx: RequestContext, raw_token: str, *, client_ip: str | None = None
) -> None:
    """`POST /v1/auth/logout` — revoca todo refresh vivo del dispositivo.

    **Idempotente y sin oráculo**: sale en silencio si el token está
    malformado, si no existe, si no es de quien lo presenta o si su verifier
    no coincide. La vista responde `204` en todos los casos, así que un
    atacante con un token ajeno no aprende nada sobre él.

    **Se revoca por `device_id`, no por `family_id`.** `log_in` empieza una
    familia nueva en cada login y deja vivas las anteriores del mismo
    dispositivo a propósito (comentario en `log_in`); si el logout solo
    revocara la familia del token presentado, un segundo login en el mismo
    dispositivo dejaría la familia del primero —viva hasta sus 60 días de TTL—
    fuera del alcance de "cerrar sesión". Eso contradice ADR-008 ("un refresh
    por dispositivo", la misma regla que `refresh_session` ya hace cumplir
    rechazando un token presentado desde otro `device_id`) y el contrato
    ("revoca... del dispositivo indicado"). Revocar por `device_id` cierra
    **todas** las familias vivas del dispositivo —incluida la presentada, y
    cualquier fila ya rotada de la cadena— con una sola consulta indexada
    (`refresh_token_device_idx`).

    **No se toca nada más**: ni el `Device`, ni ningún dato de negocio. La
    outbox del cliente es responsabilidad del cliente y **no se borra** al
    cerrar sesión (ADR-008); el Paso 13 debe conservar el trabajo pendiente
    del agente y reenviarlo tras el siguiente login.
    """
    parsed = tokens.parse_refresh_token(raw_token)
    if parsed is None:
        return
    selector, verifier = parsed

    row = (
        scoped_qs(ctx, RefreshToken).filter(selector=selector, user_id=ctx.user_id).first()
    )
    if row is None:
        return
    if not tokens.verifier_matches(verifier, row.verifier_hash):
        return

    with transaction.atomic():
        revoked = (
            scoped_qs(ctx, RefreshToken)
            .filter(device_id=row.device_id, user_id=ctx.user_id, revoked_at__isnull=True)
            .update(revoked_at=timezone.now())
        )
        audit.record(
            ctx,
            action=AuditAction.LOGOUT,
            target=f"device:{row.device_id}",
            metadata={"revoked": revoked},
            ip=client_ip,
        )

"""Primitivas de token: access JWT Ed25519 y secreto de refresco `selector.verifier`.

Este módulo es **puro** en el sentido que importa aquí: no toca la base de
datos ni el HTTP. Genera y valida material criptográfico y nada más, para que
la parte delicada de ADR-008 se pueda razonar (y testear) sin escenario.

Dos piezas, dos decisiones:

**Access token (JWT, EdDSA/Ed25519, 15 min)** — ADR-008 lo elige para no pagar
un `SELECT` por request en una app que sincroniza a ráfagas. No se persiste:
un volcado de la BD no entrega ninguna sesión. La contrapartida asumida es que
un access token robado vale 15 minutos; por eso la revocación real vive en el
refresh, que sí está en la BD.

**Secreto de refresco (`selector.verifier`)** — corrige **H-14/RD-09**
("tokens en plaintext"). El `selector` es la clave de búsqueda (no es secreto,
se indexa y se guarda en claro) y el `verifier` es el secreto (solo se guarda
`sha256(verifier)`, y se compara en **tiempo constante**). Partir el token en
dos evita el dilema clásico: buscar por el hash obligaría a hashear con sal
fija o a un índice sobre el secreto, y buscar por el secreto en claro es
justo el hallazgo. Con selector/verifier la búsqueda es por índice y la
comparación es criptográfica.

**Por qué SHA-256 y no Argon2 para el verifier**: Argon2 protege secretos de
**baja entropía** (contraseñas humanas) frente a fuerza bruta offline. El
verifier son 256 bits de `secrets.token_urlsafe`: no hay diccionario que
recorrer, así que un KDF lento solo añadiría ~100 ms a cada refresh sin
comprar seguridad. Es la misma decisión que toma ADR-008 al escribir
literalmente `sha256(verifier)`.

**Por qué PyJWT y no otra librería** (decisión del Paso 7, punto 2 de la nota
del controlador): PyJWT soporta `EdDSA` cuando `cryptography` está instalado
—que es la implementación de Ed25519 de referencia en Python, la misma que
usan `paramiko` o `certbot`—, es la librería JWT con mayor uso en el
ecosistema Django, no arrastra dependencias extra y su API permite **fijar el
algoritmo aceptado** (`algorithms=["EdDSA"]`), que es lo que cierra la
confusión de algoritmo (`alg: none`, EdDSA→HS256 con la clave pública como
secreto HMAC). Alternativas descartadas: `python-jose` (sin releases desde
2021, CVEs de confusión de algoritmo abiertas), `authlib` (correcta, pero trae
todo un framework OAuth/OIDC que este paso no usa) y `djangorestframework-
simplejwt` (acoplada a DRF, que ADR-004 descarta explícitamente).
"""

from __future__ import annotations

import hmac
import re
import secrets
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import jwt
from django.conf import settings

from visitacampo.authn.keys import (
    get_signing_key,
    key_id,
    public_key_pem,
    reset_key_cache,
    verification_keys,
)

__all__ = [
    "SELECTOR_CHARS",
    "VERIFIER_BYTES",
    "VERIFIER_HASH_CHARS",
    "AccessToken",
    "AccessTokenClaims",
    "RefreshSecret",
    "decode_access_token",
    "generate_refresh_secret",
    "hash_verifier",
    "issue_access_token",
    "parse_refresh_token",
    "public_key_pem",
    "reset_key_cache",
    "verifier_matches",
]

# --- Refresh (`selector.verifier`) ------------------------------------------

#: Bytes del selector. 128 bits es de sobra para que no haya colisiones en la
#: columna `UNIQUE` sin necesidad de reintentar la inserción.
SELECTOR_BYTES = 16
#: Longitud del selector ya codificado (`token_urlsafe` = base64url sin
#: relleno ⇒ `ceil(bytes * 4 / 3)`). Es lo que mide la columna `selector`.
SELECTOR_CHARS = 22

#: Bytes del verifier. ADR-008: "opaco, **256 bits**".
VERIFIER_BYTES = 32
#: Longitud del hash hexadecimal de SHA-256. Es lo que mide `verifier_hash`.
VERIFIER_HASH_CHARS = 64

#: Separador del token opaco. Un carácter fuera del alfabeto base64url, así
#: que nunca aparece dentro del selector ni del verifier y `split` es exacto.
REFRESH_SEPARATOR = "."

#: Alfabeto de `secrets.token_urlsafe`: lo único válido en selector y verifier.
_URLSAFE = re.compile(r"[A-Za-z0-9_-]+")

# --- Access token (JWT) -----------------------------------------------------

#: Único algoritmo aceptado, al firmar y al verificar (ADR-008: EdDSA/Ed25519).
#: Que sea una lista de **un** elemento es lo que impide la confusión de
#: algoritmo: PyJWT rechaza cualquier token cuyo `alg` no esté aquí, incluidos
#: `none` y `HS256`.
JWT_ALGORITHM = "EdDSA"
JWT_ALGORITHMS: Sequence[str] = (JWT_ALGORITHM,)

DEFAULT_ACCESS_TTL_SECONDS = 900  # 15 min (ADR-008)


@dataclass(frozen=True, slots=True)
class RefreshSecret:
    """Secreto de refresco recién generado. El `verifier` **solo existe aquí**.

    Se devuelve entero (`token`) para la respuesta HTTP y despiezado para la
    fila de BD. Quien lo persista debe guardar `selector` y
    `hash_verifier(verifier)`, nunca `verifier` ni `token` (RD-09/H-14).
    """

    selector: str
    verifier: str

    @property
    def token(self) -> str:
        """`selector.verifier` — lo que viaja al cliente una sola vez."""
        return f"{self.selector}{REFRESH_SEPARATOR}{self.verifier}"


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_in: int
    expires_at: datetime
    jti: str


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Claims ya validados de un access token (contrato `bearerAuth`)."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    device_id: uuid.UUID | None
    roles: tuple[str, ...]
    jti: str


def generate_refresh_secret() -> RefreshSecret:
    """Genera un `selector.verifier` nuevo con `secrets` (CSPRNG del sistema)."""
    return RefreshSecret(
        selector=secrets.token_urlsafe(SELECTOR_BYTES),
        verifier=secrets.token_urlsafe(VERIFIER_BYTES),
    )


def hash_verifier(verifier: str) -> str:
    """`sha256(verifier)` en hexadecimal — lo único que se persiste (RD-09)."""
    return sha256(verifier.encode("ascii")).hexdigest()


def verifier_matches(verifier: str, stored_hash: str) -> bool:
    """Compara en **tiempo constante** (ADR-008).

    `hmac.compare_digest` no cortocircuita en el primer byte distinto: sin
    esto, medir el tiempo de respuesta permitiría reconstruir el hash byte a
    byte. Un `stored_hash` vacío (fila inexistente) devuelve `False` habiendo
    hecho el mismo trabajo, para no regalar un oráculo de existencia.
    """
    return hmac.compare_digest(hash_verifier(verifier), stored_hash)


def parse_refresh_token(raw: str) -> tuple[str, str] | None:
    """Despieza `selector.verifier`, o `None` si no tiene esa forma.

    Devolver `None` en vez de lanzar es deliberado: un token malformado es una
    credencial inválida más, y debe acabar en el **mismo** 401 genérico que un
    verifier incorrecto, no en un 500 ni en un mensaje distinto que sirva de
    oráculo.
    """
    parts = raw.split(REFRESH_SEPARATOR)
    if len(parts) != 2:
        return None
    selector, verifier = parts
    # Solo el alfabeto con el que se generan (`token_urlsafe`). Sin esto, un
    # carácter no ASCII llegaba hasta `hash_verifier` y salía como un 400
    # "cuerpo no parseable" en vez del 401 genérico (hallazgo de Schemathesis).
    if not _URLSAFE.fullmatch(selector) or not _URLSAFE.fullmatch(verifier):
        return None
    return selector, verifier


def _access_ttl_seconds() -> int:
    return int(getattr(settings, "JWT_ACCESS_TTL_SECONDS", DEFAULT_ACCESS_TTL_SECONDS))


def issue_access_token(
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    device_id: uuid.UUID | None,
    roles: Iterable[str],
) -> AccessToken:
    """Firma un access token con los claims mínimos del contrato.

    Claims: `sub`, `org`, `device`, `roles`, `jti`, `exp` (contrato
    `securitySchemes.bearerAuth`) más `iat`. `iat` no está en la lista mínima
    pero se incluye porque es lo que permitirá al Paso 15 invalidar en bloque
    los tokens emitidos *antes* de una revocación, sin persistir ninguno.

    Lo que **no** lleva: nombre, correo ni ningún dato personal. Un JWT viaja
    en cada request y acaba en logs y crash reports; ahí solo deben ir
    identificadores (RD-12).
    """
    now = datetime.now(UTC)
    ttl = _access_ttl_seconds()
    expires_at = now + timedelta(seconds=ttl)
    jti = str(uuid.uuid4())
    claims = {
        "sub": str(user_id),
        "org": str(org_id),
        "device": str(device_id) if device_id is not None else None,
        "roles": list(roles),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    signing_key = get_signing_key()
    token = jwt.encode(
        claims,
        signing_key,
        algorithm=JWT_ALGORITHM,
        headers={"kid": key_id(signing_key.public_key())},
    )
    return AccessToken(token=token, expires_in=ttl, expires_at=expires_at, jti=jti)


def _as_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def decode_access_token(token: str) -> AccessTokenClaims | None:
    """Valida firma, algoritmo, expiración y forma de los claims.

    Devuelve `None` ante **cualquier** problema, sin distinguir el motivo: el
    llamante (la clase de auth de Ninja) responde 401 y no hay forma de que un
    atacante deduzca si falló la firma, la expiración o el formato. Distinguir
    causas aquí solo serviría para construir ese oráculo.

    Durante una rotación (ADR-008) hay dos claves aceptadas: se prueba primero
    la que nombra el `kid` de la cabecera. El `kid` solo ordena, no decide:
    la firma se verifica igual, y un `kid` falso o ausente cae en el resto.
    """
    if not token:
        return None
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.InvalidTokenError:
        return None
    keys = sorted(verification_keys(), key=lambda k: key_id(k) != kid)
    for key in keys:
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=list(JWT_ALGORITHMS),
                options={"require": ["sub", "org", "roles", "jti", "exp"]},
            )
        except jwt.InvalidTokenError:
            continue

        user_id = _as_uuid(payload.get("sub"))
        org_id = _as_uuid(payload.get("org"))
        roles = payload.get("roles")
        jti = payload.get("jti")
        if user_id is None or org_id is None or not isinstance(roles, list) or not jti:
            # Firma válida pero claims inservibles: se trata como token
            # inválido, no se "arregla" con valores por defecto.
            return None
        if not all(isinstance(role, str) for role in roles):
            return None

        return AccessTokenClaims(
            user_id=user_id,
            org_id=org_id,
            device_id=_as_uuid(payload.get("device")),
            roles=tuple(roles),
            jti=str(jti),
        )
    return None

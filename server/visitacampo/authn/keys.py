"""Carga de la clave de firma **Ed25519** de los access tokens (ADR-008).

Tarea 5 del Paso 7: "gestionar la clave de firma Ed25519 como secreto de
archivo/variable de entorno (**no hardcodeada**)". Este módulo es el único
sitio del backend que sabe de dónde sale esa clave, y su contrato es corto:

- `JWT_SIGNING_KEY_FILE` — ruta a un PEM montado como **secreto de archivo**.
  Es el camino que ADR-008 recomienda ("montada como secreto de archivo") y
  el que gana si están las dos variables: un archivo no aparece en `ps`, ni
  en `docker inspect`, ni en el entorno heredado por los subprocesos.
- `JWT_SIGNING_KEY` — el PEM en línea, para despliegues cuyo gestor de
  secretos solo inyecta variables. Se admite además **base64 del PEM**
  porque `infra/.env` es un archivo de una línea por variable y un PEM
  multilínea no cabe ahí sin escaparlo.

**Por qué no hay clave por defecto**: una constante en el código sería una
clave pública de facto (está en el repositorio) con la que cualquiera podría
firmar un token válido para cualquier usuario y organización. Sin clave
configurada:

- en producción (`DEBUG=False`) se lanza `ImproperlyConfigured` — fail-closed;
- en desarrollo (`DEBUG=True`) se genera una clave **efímera en memoria** y se
  avisa con un `RuntimeWarning`. Efímera de verdad: muere con el proceso, así
  que reiniciar el servidor invalida los tokens emitidos. Eso es una molestia
  aceptable en dev y una imposibilidad en producción, que es exactamente el
  incentivo que se busca.

**Generar una clave**::

    python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import \\
        Ed25519PrivateKey; from cryptography.hazmat.primitives import serialization; \\
        print(Ed25519PrivateKey.generate().private_bytes( \\
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, \\
            serialization.NoEncryption()).decode())"

**Rotación con dos claves vivas** (ADR-008, "rotable con periodo de gracia de
dos claves"): la clave de firma es siempre una sola; la **anterior** se
configura aparte (`JWT_PREVIOUS_KEY_FILE` o `JWT_PREVIOUS_KEY`) y solo
**verifica**, nunca firma. Basta su clave pública (PEM `PUBLIC KEY`): no hace
falta seguir guardando el secreto retirado. Cada token lleva en la cabecera
`kid` la huella de la clave que lo firmó, así que se verifica directamente con
la suya. Procedimiento en `docs/operacion/incidentes.md` ("Rotar la llave
JWT"): la anterior se retira cuando ya no quedan access tokens suyos vivos
(15 min por defecto, `JWT_ACCESS_TTL_SECONDS`).
"""

from __future__ import annotations

import base64
import binascii
import warnings
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "generate_signing_key",
    "get_signing_key",
    "key_id",
    "public_key_pem",
    "reset_key_cache",
    "signing_key_to_pem",
    "verification_keys",
]

_PEM_HEADER = "-----BEGIN"

#: Cache por proceso. Parsear un PEM en cada request sería trabajo repetido
#: y, sobre todo, la clave efímera de dev tiene que ser **la misma** durante
#: toda la vida del proceso o los tokens dejarían de validarse solos.
_CACHE: dict[str, object] = {}


def reset_key_cache() -> None:
    """Vacía el cache. Lo necesitan los tests que usan `override_settings`."""
    _CACHE.clear()


def generate_signing_key() -> Ed25519PrivateKey:
    """Genera una clave Ed25519 nueva (para dev, tests y el comando de arranque)."""
    return Ed25519PrivateKey.generate()


def signing_key_to_pem(key: Ed25519PrivateKey) -> str:
    """Serializa una clave privada a PEM PKCS#8 sin cifrar."""
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _read_key_file(path_value: str) -> str:
    path = Path(path_value)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        # El mensaje incluye la ruta pero **nunca** el contenido (RD-12).
        raise ImproperlyConfigured(
            f"JWT_SIGNING_KEY_FILE apunta a {path_value!r} y no se puede leer: {exc}"
        ) from exc


def _decode_key_material(material: str) -> Ed25519PrivateKey:
    """Convierte PEM (o base64 de un PEM/DER) en una clave Ed25519.

    Nunca se incluye el material en el mensaje de error: un traceback con la
    clave privada dentro sería exactamente la fuga que el módulo evita (RD-12).
    """
    text = material.strip()
    if text.startswith(_PEM_HEADER):
        raw = text.encode("ascii")
    else:
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImproperlyConfigured(
                "La clave de firma no es un PEM PKCS#8 ni su base64. Genera una con "
                "`Ed25519PrivateKey.generate()` (ver el docstring de authn/keys.py)."
            ) from exc
        if raw.lstrip().startswith(_PEM_HEADER.encode("ascii")):
            raw = raw.lstrip()

    loader = (
        serialization.load_pem_private_key
        if raw.lstrip().startswith(_PEM_HEADER.encode("ascii"))
        else serialization.load_der_private_key
    )
    try:
        key = loader(raw, password=None)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "La clave de firma no se puede cargar como clave privada PKCS#8."
        ) from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise ImproperlyConfigured(
            f"ADR-008 exige Ed25519 (EdDSA) y la clave configurada es "
            f"{type(key).__name__}. Regenera la clave con Ed25519PrivateKey.generate()."
        )
    return key


def _ephemeral_key() -> Ed25519PrivateKey:
    warnings.warn(
        "JWT_SIGNING_KEY/JWT_SIGNING_KEY_FILE sin configurar: se ha generado una "
        "clave Ed25519 EFÍMERA en memoria. Vale para desarrollo (los tokens dejan "
        "de ser válidos al reiniciar el proceso) y es imposible en producción, "
        "donde DEBUG=False hace que esto sea un error. Ver infra/.env.example.",
        RuntimeWarning,
        stacklevel=3,
    )
    return generate_signing_key()


def get_signing_key() -> Ed25519PrivateKey:
    """Clave con la que se **firman** los access tokens."""
    cached = _CACHE.get("signing")
    if isinstance(cached, Ed25519PrivateKey):
        return cached

    key_file = getattr(settings, "JWT_SIGNING_KEY_FILE", "") or ""
    inline = getattr(settings, "JWT_SIGNING_KEY", "") or ""

    if key_file:
        key = _decode_key_material(_read_key_file(key_file))
    elif inline:
        key = _decode_key_material(inline)
    elif settings.DEBUG:
        key = _ephemeral_key()
    else:
        raise ImproperlyConfigured(
            "No hay clave de firma JWT: define JWT_SIGNING_KEY_FILE (recomendado, "
            "secreto de archivo) o JWT_SIGNING_KEY. Ver infra/.env.example y el "
            "docstring de visitacampo/authn/keys.py (ADR-008)."
        )

    _CACHE["signing"] = key
    return key


def _decode_public_material(material: str) -> Ed25519PublicKey:
    """La clave anterior: una clave **pública** Ed25519 (PEM o base64 del PEM),
    o la privada retirada, de la que solo se usa la parte pública."""
    text = material.strip()
    raw = text.encode("ascii") if text.startswith(_PEM_HEADER) else None
    if raw is None:
        try:
            raw = base64.b64decode(text, validate=True).lstrip()
        except (binascii.Error, ValueError) as exc:
            raise ImproperlyConfigured("La clave JWT anterior no es un PEM ni su base64.") from exc
    if b"PUBLIC KEY" in raw.split(b"\n", 1)[0]:
        try:
            public = serialization.load_pem_public_key(raw)
        except ValueError as exc:
            raise ImproperlyConfigured("La clave JWT anterior no se puede cargar.") from exc
        if not isinstance(public, Ed25519PublicKey):
            raise ImproperlyConfigured("La clave JWT anterior tiene que ser Ed25519.")
        return public
    return _decode_key_material(raw.decode("ascii")).public_key()


def _previous_key() -> Ed25519PublicKey | None:
    key_file = getattr(settings, "JWT_PREVIOUS_KEY_FILE", "") or ""
    inline = getattr(settings, "JWT_PREVIOUS_KEY", "") or ""
    if key_file:
        return _decode_public_material(_read_key_file(key_file))
    if inline:
        return _decode_public_material(inline)
    return None


def _raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )


def key_id(key: Ed25519PublicKey) -> str:
    """Huella corta de una clave pública (cabecera `kid` del JWT).

    No es secreta ni identifica a nadie: sirve para verificar con la clave
    correcta durante una rotación y para saber, en un incidente, con qué
    clave se firmó un token.
    """
    return sha256(_raw_public(key)).hexdigest()[:16]


def verification_keys() -> list[Ed25519PublicKey]:
    """Claves públicas aceptadas al **verificar**: la actual y, durante una
    rotación, la anterior. Nunca más de dos."""
    cached = _CACHE.get("verification")
    if isinstance(cached, list):
        return cached
    current = get_signing_key().public_key()
    keys = [current]
    previous = _previous_key()
    if previous is not None and _raw_public(previous) != _raw_public(current):
        keys.append(previous)
    _CACHE["verification"] = keys
    return keys


def public_key_pem() -> str:
    """PEM de la clave **pública**. Es lo que verifica la firma; no es secreto."""
    return (
        get_signing_key()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

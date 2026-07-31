"""`RefreshToken`: la fila de sesión que **nunca** contiene un token utilizable.

Referencias normativas:
- **ADR-008** — refresh opaco de 256 bits, patrón `selector.verifier`, un
  refresh **por dispositivo**, rotación con detección de reuso por familia.
- **RD-09** (hallazgo **H-14**) — qfieldcloud guardaba los tokens en claro:
  un volcado de la BD entregaba sesiones activas listas para usar. Aquí la
  columna del secreto es `verifier_hash` y **no existe ninguna columna que
  contenga el verifier**; el test
  `authn/tests/test_login.py::PlaintextRefreshTokenRegressionTests` lo
  comprueba leyendo la tabla con SQL crudo.
- **RD-10** — la referencia invalidaba sesiones con una carrera; aquí la
  rotación ocurre dentro de `transaction.atomic()` (ver `authn/services.py`).

**Por qué hereda de `OrgScopedModel`** (ADR-011): aunque una sesión no es
"dato de negocio" en sentido estricto, sí es una fila que pertenece a una
organización, y heredar es lo que permite consultarla con
`scoped_qs(ctx, RefreshToken)` en vez de con el manager desnudo. Sin `org_id`,
`scoped_qs` la rechazaría (`NotOrgScopedError`) y el logout tendría que leer
la tabla sin aislamiento — exactamente lo que RD-11 evita.
"""

from __future__ import annotations

import uuid

from django.db import models

from visitacampo.authn.tokens import SELECTOR_CHARS, VERIFIER_HASH_CHARS
from visitacampo.core.models import OrgScopedModel


class RefreshToken(OrgScopedModel):
    """Un refresh emitido a un dispositivo. Vive hasta rotarse, revocarse o caducar."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Clave de búsqueda, **no secreta** (ADR-008). Va en claro y con índice
    # único: es lo que permite localizar la fila sin buscar por el secreto.
    selector = models.CharField(
        max_length=SELECTOR_CHARS,
        unique=True,
        editable=False,
        verbose_name="selector",
    )

    # El secreto, hasheado. `sha256(verifier)` en hexadecimal — 64 caracteres
    # fijos. Comparación en tiempo constante en `tokens.verifier_matches`.
    verifier_hash = models.CharField(
        max_length=VERIFIER_HASH_CHARS,
        editable=False,
        verbose_name="hash del verifier",
    )

    device = models.ForeignKey(
        "core.Device",
        on_delete=models.PROTECT,
        related_name="refresh_tokens",
        verbose_name="dispositivo",
    )

    # `user` explícito y no `device.owner`: un mismo dispositivo puede cambiar
    # de propietario en el back-office (Paso 17), y entonces resolver el
    # usuario por el dispositivo convertiría un token viejo en un token del
    # **usuario nuevo** — escalada de privilegios silenciosa. El token se ata
    # al usuario para el que se emitió y a nada más.
    user = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="refresh_tokens",
        verbose_name="usuario",
    )

    # Familia de rotación (ADR-008): todos los tokens que descienden de un
    # mismo login la comparten. Detectar el reuso de un token ya rotado debe
    # revocar la familia **entera**, no solo esa fila — esa parte la
    # implementa el Paso 15; la columna existe desde ya para que el Paso 15
    # sea código, no una migración de datos.
    family_id = models.UUIDField(default=uuid.uuid4, editable=False, verbose_name="familia")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(verbose_name="caduca")

    # Los tres estados terminales se modelan como **instantes**, no como un
    # `status`: cuándo pasó es información de auditoría que un enum perdería,
    # y `NULL` significa inequívocamente "todavía no". Un token es utilizable
    # solo si los dos son `NULL` y no ha caducado (`services.py`).
    rotated_at = models.DateTimeField(null=True, blank=True, default=None, verbose_name="rotado")
    revoked_at = models.DateTimeField(null=True, blank=True, default=None, verbose_name="revocado")

    class Meta:
        db_table = "refresh_token"
        verbose_name = "token de refresco"
        verbose_name_plural = "tokens de refresco"
        indexes = [
            # La detección de reuso del Paso 15 revoca la familia entera del
            # token reutilizado: una consulta por `family_id` dentro de la
            # organización. Sin este índice sería un escaneo de toda la tabla
            # de sesiones del despliegue.
            models.Index(fields=["org", "family_id"], name="refresh_token_family_idx"),
            # "Cerrar sesión en el dispositivo X": lo usan tanto el logout
            # propio (`authn/services.py::log_out_session`, que revoca **todo**
            # refresh vivo del dispositivo, no solo la familia presentada) como
            # el back-office del Paso 17 sobre el dispositivo de otro.
            models.Index(fields=["org", "device"], name="refresh_token_device_idx"),
        ]

    def __str__(self) -> str:
        # Nunca el hash ni nada derivado del secreto en un `__str__`: acaba en
        # logs y en el admin (RD-12). El selector no es secreto.
        return f"refresh {self.selector} (device {self.device_id})"

    def is_usable(self, now) -> bool:
        """`True` solo si no está rotado, ni revocado, ni caducado.

        Deny-by-default: cualquier estado que no sea "los tres bien" es `False`.
        """
        return self.rotated_at is None and self.revoked_at is None and self.expires_at > now

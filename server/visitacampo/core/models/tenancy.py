"""Identidad y multi-tenancy: `Organization`, `AppUser`, `Membership`, `Device`.

ADR-011: esquema compartido con `org_id` desde el día 1, modelo de
identidad **plano** (`app_user` + `membership(org_id, user_id, role)`),
sin herencia multi-tabla y sin vistas SQL no gestionadas.
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from .base import OrgScopedModel, Platform, Role


class Organization(models.Model):
    """Tenant. En el MVP existe **una** sola fila (ADR-011, S-09).

    No hereda de `OrgScopedModel`: es la propia raíz del scoping, no una
    tabla scopeada. Es la única tabla de negocio sin `org_id` junto con
    `Blob` (ver la nota de RD-08 en `visit.py`).
    """

    # Generado en el servidor: una organización no nace en un dispositivo.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=256, verbose_name="nombre")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organization"
        verbose_name = "organización"
        verbose_name_plural = "organizaciones"

    def __str__(self) -> str:
        return self.name


class AppUser(AbstractUser):
    """Usuario de la aplicación. Es el `AUTH_USER_MODEL` del proyecto.

    DECISIÓN (delegada por el brief, tarea 2): **extender
    `AbstractUser`** en vez de crear un modelo propio con FK a
    `auth.User`. Razones, en orden de peso:

    1. *Simplicidad y planitud* (ADR-011): un modelo propio con FK a
       `auth.User` son **dos tablas y un JOIN** para responder "quién es
       este usuario" — exactamente el coste que ADR-011 rechaza en la
       herencia multi-tabla de la referencia, solo que escrito a mano.
       `AbstractUser` deja **una** tabla plana (`app_user`).
    2. Se conserva gratis toda la infraestructura de `django.contrib.auth`
       que los pasos siguientes necesitan: hashing Argon2id ya configurado
       en el Paso 4 (`PASSWORD_HASHERS`), `check_password`, permisos y
       `django.contrib.admin` para el back-office del Paso 17 (ADR-004).
       Con un modelo paralelo habría que reimplementar todo eso.
    3. `AbstractUser` (no `AbstractBaseUser`) porque `username` +
       `password` es exactamente lo que pide el contrato
       (`LoginRequest`), y no hay requisito de login por email.

    Este es el **último momento barato** para hacer el swap de
    `AUTH_USER_MODEL`: se hace antes de la primera migración aplicada, tal
    como recomienda la documentación de Django.

    Nota sobre `org` y `Membership` (redundancia deliberada, documentada):
    doc 08 §4.4 modela `ORGANIZATION ||--o{ APP_USER` *y* la tabla
    `membership`. `AppUser.org` es la organización **de origen** (la que
    el cliente sincroniza; es lo que emite `UserProfile.org_id` del
    contrato) y `Membership` es la fuente autoritativa del **rol**. La
    invariante "existe una `Membership(org=user.org, user=user)`" se
    refuerza a nivel de aplicación al resolver el `RequestContext`
    (Pasos 6-7): no hay forma de expresarla como constraint de una sola
    tabla, y un trigger sería el "double-bookkeeping" que RD-14 prohíbe.
    """

    # UUID generado en servidor (los usuarios se crean en el back-office,
    # no en el dispositivo). El contrato (`UserProfile.id`) lo espera UUID.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    org = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="users",
        verbose_name="organización de origen",
    )
    # Contrato `UserProfile.display_name`: `string | null`. `null=True` (y no
    # cadena vacía) porque el contrato distingue explícitamente ausencia de
    # valor; ver la nota de DJ001 en `server/pyproject.toml`.
    display_name = models.CharField(max_length=128, null=True, blank=True)

    # `createsuperuser` preguntará también por la organización. Implica que
    # debe existir una `Organization` antes que el primer usuario: es el
    # orden natural del ER de doc 08 §4.4 y el Paso 17 añadirá el comando de
    # arranque que cree ambas cosas de una vez.
    REQUIRED_FIELDS = ["email", "org"]

    class Meta:
        db_table = "app_user"
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def __str__(self) -> str:
        return self.display_name or self.get_username()


class Membership(OrgScopedModel):
    """Rol de un usuario dentro de una organización (ADR-011).

    doc 08 §4.4 la describe con PK compuesta `(org_id, user_id)`. Aquí se
    modela como PK sustituta + `UNIQUE (org_id, user_id)`, que da
    **exactamente la misma garantía de unicidad** y evita
    `models.CompositePrimaryKey` (Django 5.2), cuyas limitaciones
    documentadas —sin soporte en `django.contrib.admin`, sin poder ser
    destino de una FK— chocarían con el back-office del Paso 17.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="memberships",
        verbose_name="usuario",
    )
    role = models.CharField(max_length=16, choices=Role.choices, verbose_name="rol")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "membership"
        verbose_name = "membresía"
        verbose_name_plural = "membresías"
        constraints = [
            models.UniqueConstraint(
                fields=["org", "user"],
                name="membership_org_user_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.org_id}:{self.role}"


class Device(OrgScopedModel):
    """Instalación de la app en un dispositivo (contrato `DeviceInfo`).

    El `id` lo genera el cliente y es estable durante toda la vida de la
    instalación: el refresh token y la outbox son **por dispositivo**
    (ADR-008). El modelo `RefreshToken` (selector/verifier) es del Paso 7;
    aquí solo se deja reservada la FK `owner`, que es su punto de anclaje.
    """

    # Generado en el cliente (`DeviceInfo.device_id`): sin `default`, para
    # que el servidor nunca invente uno por su cuenta.
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="devices",
        verbose_name="propietario",
    )
    platform = models.CharField(max_length=16, choices=Platform.choices)
    os_version = models.CharField(max_length=64, null=True, blank=True)
    model = models.CharField(max_length=128, null=True, blank=True)
    app_version = models.CharField(max_length=32)
    display_name = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "device"
        verbose_name = "dispositivo"
        verbose_name_plural = "dispositivos"
        indexes = [
            models.Index(fields=["org", "owner"], name="device_org_owner_idx"),
        ]

    def __str__(self) -> str:
        return self.display_name or str(self.id)

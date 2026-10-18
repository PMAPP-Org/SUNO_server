"""Autorización del titular para el tratamiento de sus datos (Ley 1581 de 2012).

El artículo 9 exige autorización **previa, expresa e informada**, y que el
responsable **conserve prueba** de ella. La app muestra el aviso de privacidad
antes del primer login y pide aceptar cada finalidad por separado; esta tabla
es la prueba: quién, desde qué dispositivo, qué versión del aviso, qué
finalidades y cuándo.

Es un **historial**: cada aceptación (por ejemplo, la de una versión nueva del
aviso) es una fila nueva; nunca se edita ni se borra desde la API. Se conserva
mientras exista la cuenta, porque es lo que demuestra que el tratamiento fue
autorizado (`docs/seguridad/tratamiento-datos-personales.md`).
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class PrivacyPurpose(models.TextChoices):
    """Finalidades que el aviso enumera y el titular acepta una a una."""

    ACCOUNT = "account", "Cuenta y uso de la aplicación"
    LOCATION = "location", "Ubicación de las visitas"
    PHOTOS = "photos", "Fotos y firmas de las visitas"


class PrivacyConsent(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "core.AppUser",
        on_delete=models.PROTECT,
        related_name="privacy_consents",
        verbose_name="titular",
    )
    device = models.ForeignKey(
        "core.Device",
        on_delete=models.PROTECT,
        related_name="privacy_consents",
        verbose_name="dispositivo",
    )
    notice_version = models.CharField(max_length=32, verbose_name="versión del aviso")
    purposes = models.JSONField(verbose_name="finalidades aceptadas")
    #: Cuándo aceptó, según el teléfono (puede ser sin red, horas antes).
    accepted_at = models.DateTimeField(verbose_name="aceptado (dispositivo)")
    #: Cuándo llegó al servidor. La prueba de que existía a esa hora.
    recorded_at = models.DateTimeField(auto_now_add=True, verbose_name="registrado")

    class Meta:
        db_table = "privacy_consent"
        verbose_name = "autorización de datos personales"
        verbose_name_plural = "autorizaciones de datos personales"
        ordering = ("-recorded_at",)
        indexes = [
            models.Index(fields=["org", "user", "-recorded_at"], name="consent_org_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.notice_version} · {', '.join(self.purposes)}"

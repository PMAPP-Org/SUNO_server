"""Sitio de Django Admin con segundo factor obligatorio (riesgo R-20, A.8.5).

`OTPAdminSite` de django-otp, con un único ajuste de usabilidad: el usuario no
tiene que elegir el dispositivo en un desplegable.

django-otp exige un dispositivo concreto cuando el usuario tiene varios (aquí,
la app TOTP y los códigos de respaldo), y lo hace **a propósito**: probar el
código contra todos registraría un intento fallido en cada uno y acabaría
bloqueando los códigos de respaldo sin que se usaran. Respetando eso, el
dispositivo se elige por el **formato** del código, que no es ambiguo:

- 6 dígitos → la app de autenticación (TOTP);
- cualquier otro formato → los códigos de respaldo (8 caracteres base32).

Así el código se verifica contra un único dispositivo, como pide la librería,
con su throttling intacto.
"""

from __future__ import annotations

import re
from typing import Any

from django_otp import devices_for_user
from django_otp.admin import OTPAdminAuthenticationForm, OTPAdminSite
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

__all__ = ["VisitaCampoOTPAdminSite"]

_TOTP_CODE = re.compile(r"\d{6}")


class _DeviceByCodeFormatForm(OTPAdminAuthenticationForm):
    def _chosen_device(self, user: Any) -> Any:
        chosen = super()._chosen_device(user)
        if chosen is not None:
            return chosen
        token = (self.cleaned_data.get("otp_token") or "").strip()
        kind = TOTPDevice if _TOTP_CODE.fullmatch(token) else StaticDevice
        # Solo dispositivos confirmados del propio usuario (la misma garantía
        # que el `_chosen_device` original da para un id elegido a mano).
        return next(
            (d for d in devices_for_user(user, confirmed=True) if isinstance(d, kind)),
            None,
        )


class VisitaCampoOTPAdminSite(OTPAdminSite):
    login_form = _DeviceByCodeFormatForm

"""`python manage.py enroll_admin_totp <usuario>` — alta del segundo factor del Admin.

El back-office exige TOTP (`ADMIN_REQUIRE_OTP`, `config/urls.py`). El alta se
hace **en el servidor**, por línea de comandos, a propósito: dar de alta un
segundo factor exige ya tener acceso al servidor, así que robar una contraseña
del Admin no basta para registrar un dispositivo propio, y el secreto TOTP no
viaja nunca por la web.

Muestra **una sola vez**:
- la URI `otpauth://` y el secreto en base32, para la app de autenticación
  (Google Authenticator, Aegis, 1Password…; escanear la URI como QR con
  cualquier generador local, o escribir el secreto a mano);
- 10 códigos de respaldo de un solo uso, para cuando se pierda el teléfono.

`--replace` borra los dispositivos anteriores del usuario (teléfono perdido o
cambiado). Cada alta queda en la traza de auditoría (`admin.mfa_enrolled`).
"""

from __future__ import annotations

import base64
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import system_context

BACKUP_CODES = 10


class Command(BaseCommand):
    help = "Da de alta el segundo factor (TOTP + códigos de respaldo) de un usuario del Admin."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("username")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Borra los dispositivos anteriores del usuario (teléfono perdido o cambiado).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        try:
            user = user_model._default_manager.get(username=options["username"])
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No existe el usuario {options['username']!r}.") from exc
        if not user.is_staff:
            raise CommandError("El usuario no es staff: no puede entrar al Admin.")

        with transaction.atomic():
            existing = TOTPDevice.objects.devices_for_user(user, confirmed=None).exists()
            if existing and not options["replace"]:
                raise CommandError(
                    "El usuario ya tiene un segundo factor. Usa --replace para sustituirlo."
                )
            if options["replace"]:
                TOTPDevice.objects.filter(user=user).delete()
                StaticDevice.objects.filter(user=user).delete()

            totp = TOTPDevice.objects.create(
                user=user, name="App de autenticación", confirmed=True
            )
            static = StaticDevice.objects.create(
                user=user, name="Códigos de respaldo", confirmed=True
            )
            codes = [StaticToken.random_token() for _ in range(BACKUP_CODES)]
            StaticToken.objects.bulk_create(
                StaticToken(device=static, token=code) for code in codes
            )
            audit.record(
                system_context(user.org_id),
                action=AuditAction.ADMIN_MFA_ENROLLED,
                target=f"user:{user.pk}",
                actor_id=user.pk,
                metadata={"replaced": bool(options["replace"])},
            )

        secret = base64.b32encode(totp.bin_key).decode("ascii")
        self.stdout.write("Segundo factor dado de alta. Esto se muestra UNA sola vez:\n")
        self.stdout.write(f"  URI para la app de autenticación: {totp.config_url}")
        self.stdout.write(f"  Secreto (alta manual):            {secret}\n")
        self.stdout.write("  Códigos de respaldo (un solo uso cada uno):")
        for code in codes:
            self.stdout.write(f"    {code}")
        self.stdout.write(
            "\nGuarda los códigos de respaldo fuera del teléfono (gestor de contraseñas)."
        )

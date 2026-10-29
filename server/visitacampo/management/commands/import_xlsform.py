"""Publish a new immutable template version from a supported XLSForm workbook."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from visitacampo.core.audit import AuditAction, record
from visitacampo.core.context import Principal, resolve_request_context, system_context
from visitacampo.core.models import FormTemplate, Membership
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.scoping import scoped_qs
from visitacampo.forms.services import publish_version
from visitacampo.forms.xlsform import MAX_FILE_BYTES, InvalidXlsForm, import_xlsform


class Command(BaseCommand):
    help = "Importa un XLSForm y publica una nueva versión de una plantilla existente."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path")
        parser.add_argument("--org", required=True, type=uuid.UUID)
        parser.add_argument("--by", required=True)
        parser.add_argument("--template", required=True, type=uuid.UUID)
        parser.add_argument("--name")
        parser.add_argument("--validate-only", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        membership = (
            scoped_qs(system_context(options["org"]), Membership)
            .filter(user__username=options["by"], user__is_active=True)
            .first()
        )
        if membership is None:
            raise CommandError("No hay un usuario activo con ese nombre en la organización.")
        ctx = resolve_request_context(Principal(user_id=membership.user_id, org_id=options["org"]))
        try:
            with Path(options["path"]).open("rb") as source:
                parsed = import_xlsform(source.read(MAX_FILE_BYTES + 1))
            if options["validate_only"]:
                self.stdout.write(
                    self.style.SUCCESS("XLSForm válido; no se publicó ninguna versión.")
                )
                return
            template_name = (
                scoped_qs(ctx, FormTemplate)
                .filter(pk=options["template"])
                .values_list("name", flat=True)
                .first()
            )
            # Igual que la importación desde el Admin: versión y auditoría juntas.
            with transaction.atomic():
                published = publish_version(
                    ctx,
                    template_id=options["template"],
                    definition=parsed.model_dump(mode="json", exclude_none=True),
                    name=options["name"] or template_name,
                )
                record(
                    ctx,
                    action=AuditAction.TEMPLATE_PUBLISHED,
                    target=f"form_template_version:{published.pk}",
                    metadata={"version_number": published.version_number, "source": "xlsform"},
                )
        except (InvalidXlsForm, OSError, PolicyDenied) as exc:
            raise CommandError(f"Importación rechazada: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(f"Publicada versión {published.version_number}: {published.pk}")
        )

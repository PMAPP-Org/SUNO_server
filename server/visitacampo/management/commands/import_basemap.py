"""`manage.py import_basemap --org <uuid> --by <usuario> [--name ...] archivo.mbtiles`

Importa un paquete de mapa base para una organización (Fase B). Se hace desde
el servidor y no subiendo el archivo por el Admin: un MBTiles pesa cientos de
MB, y pasarlo por el proxy y por Django sería abrir la puerta a peticiones
enormes contra la aplicación web.

`--by` es el administrador que lo importa: la acción queda en su traza de
auditoría y se aplica su política (`can_manage_basemaps`), igual que si lo
hiciera desde la web. No hay un "usuario del sistema" con permisos.

Ejemplo en el VPS (el archivo copiado antes con `scp` al directorio montado):

    docker compose exec api python manage.py import_basemap \\
        --org 1b0c... --by admin --name "Finca El Roble 2026" /data/imports/roble.mbtiles
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from visitacampo.basemaps import services
from visitacampo.core.context import Principal, resolve_request_context, system_context
from visitacampo.core.models import Membership
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.scoping import scoped_qs


class Command(BaseCommand):
    help = "Importa un mapa base MBTiles (ráster) para una organización."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", help="Archivo .mbtiles (PNG, JPEG o WebP).")
        parser.add_argument("--org", required=True, type=uuid.UUID, help="Organización destino.")
        parser.add_argument(
            "--by", required=True, help="Usuario administrador que hace la importación."
        )
        parser.add_argument("--name", default=None, help="Nombre que verán los agentes.")

    def handle(self, *args: Any, **options: Any) -> None:
        org_id: uuid.UUID = options["org"]
        # Buscado por su **membresía** en la organización destino (contexto sin
        # roles: solo aísla por `org_id`): el administrador de la org A no
        # puede importar en la B. Sus permisos los decide después la política.
        membership = (
            scoped_qs(system_context(org_id), Membership)
            .filter(user__username=options["by"])
            .first()
        )
        if membership is None:
            raise CommandError("No hay un usuario con ese nombre en esa organización.")
        ctx = resolve_request_context(Principal(user_id=membership.user_id, org_id=org_id))
        try:
            package = services.import_basemap(ctx, path=options["path"], name=options["name"])
        except services.InvalidMbtiles as exc:
            raise CommandError(f"Paquete rechazado: {exc}") from exc
        except PolicyDenied as exc:
            raise CommandError(
                "Ese usuario no puede administrar los mapas de la organización."
            ) from exc
        except FileNotFoundError as exc:
            raise CommandError(f"No existe el archivo {options['path']}.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Importado «{package.name}» ({package.size} bytes, "
                f"zoom {package.min_zoom}–{package.max_zoom}, {package.tile_format}). "
                f"id={package.pk}"
            )
        )

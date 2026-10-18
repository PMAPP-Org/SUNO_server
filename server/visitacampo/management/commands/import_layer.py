"""`manage.py import_layer --org <uuid> --by <usuario> --name "..." capa.geojson`

Importa una capa de referencia GeoJSON (RFC 7946, WGS84) para una organización
(Fase C). Como `import_basemap`: desde el servidor, en nombre de un
administrador con nombre (política + auditoría), nunca por la web.

Desde QGIS: clic derecho en la capa → Exportar → Guardar objetos como… →
formato GeoJSON, SRC **EPSG:4326**.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from visitacampo.core.context import Principal, resolve_request_context, system_context
from visitacampo.core.models import Membership
from visitacampo.core.policies import PolicyDenied
from visitacampo.core.scoping import scoped_qs
from visitacampo.layers import services


class Command(BaseCommand):
    help = "Importa una capa de referencia GeoJSON (EPSG:4326) para una organización."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", help="Archivo .geojson en EPSG:4326.")
        parser.add_argument("--org", required=True, type=uuid.UUID, help="Organización destino.")
        parser.add_argument(
            "--by", required=True, help="Usuario administrador que hace la importación."
        )
        parser.add_argument("--name", required=True, help="Nombre que verán los agentes.")

    def handle(self, *args: Any, **options: Any) -> None:
        org_id: uuid.UUID = options["org"]
        membership = (
            scoped_qs(system_context(org_id), Membership)
            .filter(user__username=options["by"])
            .first()
        )
        if membership is None:
            raise CommandError("No hay un usuario con ese nombre en esa organización.")
        ctx = resolve_request_context(Principal(user_id=membership.user_id, org_id=org_id))
        try:
            layer = services.import_layer(ctx, path=options["path"], name=options["name"])
        except services.InvalidLayer as exc:
            raise CommandError(f"Capa rechazada: {exc}") from exc
        except PolicyDenied as exc:
            raise CommandError(
                "Ese usuario no puede administrar los mapas de la organización."
            ) from exc
        except FileNotFoundError as exc:
            raise CommandError(f"No existe el archivo {options['path']}.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Importada «{layer.name}» ({layer.feature_count} entidades, "
                f"{', '.join(layer.geometry_types)}). id={layer.pk}"
            )
        )

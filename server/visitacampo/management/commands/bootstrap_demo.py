"""`python manage.py bootstrap_demo` — siembra el mínimo dato que un despliegue
nuevo necesita para ejecutar el ciclo login -> capturar visita -> sincronizar.

**Por qué existe (hallazgo Crítico de la revisión final de**
`plans/visitacampo-despliegue-demo-implementacion.md`**)**: un VPS recién
desplegado no tiene ninguna `Organization`, ningún `AppUser`, ninguna
`Membership` ni ninguna `FormTemplateVersion` publicada. Sin esas cuatro
filas, `POST /v1/auth/login` (`authn/services.py::log_in`) falla siempre con
un `401 invalid_credentials` sin explicación (no hay usuario con el que
autenticar), y aunque lo hubiera, la pantalla de captura
(la pantalla de captura de PMAPP-Org/SUNO_client) exige pegar el UUID de una
`FormTemplateVersion` que tampoco existiría (Decisión de diseño 7 del plan:
"el operador del demo siembra una plantilla vía Django Admin antes de la
prueba"). `core/models/tenancy.py` ya dejaba anotado que este comando
"llegaría en el Paso 17" y nunca llegó — este archivo es exactamente eso.

**Qué siembra, y en qué orden** (una sola transacción, todo o nada):

1. `Organization` (`--org-name`, por defecto "VisitaCampo Demo").
2. `AppUser` con rol `FIELD_AGENT` (`core/models/base.py::Role`): es quien
   ejecuta el ciclo de campo real (captura + sincroniza), no un admin.
3. `Membership` que liga los dos anteriores — sin ella, `log_in` seguiría
   devolviendo `401` aunque el usuario y la contraseña fueran correctos
   (`ctx.is_member` depende de que exista esta fila, `core/context.py`).
4. `FormTemplate` + `FormTemplateVersion` publicada, con una `definition`
   que declara exactamente los dos campos que `capture_screen.dart` envía en
   `answers` (`altura_cm`: number, `notas`: text) — validada contra el mismo
   meta-schema que `FormTemplateVersion.clean()` exige
   (`visitacampo/forms/definition.py`), y con su `change_seq` asignado bajo
   el mismo `pg_advisory_xact_lock` que exige `sync/changeseq.py` (es una
   entidad sincronizable: viaja por el pull).

**Contraseña**: nunca hardcodeada en el código fuente. Se toma de
`--password` o, si se omite, se genera con `secrets.choice` y se imprime una
única vez al final — el operador del demo la copia de la salida del comando,
igual que haría con la de `createsuperuser` si esta la generara.

**Idempotencia**: `get_or_create`/inserción condicionada al mismo criterio
que el resto del proyecto usa para "no duplicar si ya existe" (ver
`AttachmentQueueRepository.enqueue`, que hace `insertOnConflictUpdate` sobre
su propia clave). Aquí la clave natural es `(org_name, username)`: si
cualquiera de las dos ya existe, el comando se detiene con `CommandError`
**antes** de escribir nada, en vez de crear una segunda organización o un
segundo usuario con el mismo nombre — correr el comando dos veces contra el
mismo entorno debe ser seguro (falla con un mensaje claro), nunca duplicar
en silencio. Un segundo demo en el mismo entorno pasa `--org-name`/
`--username` distintos.
"""

from __future__ import annotations

import secrets
import string
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    FormTemplate,
    FormTemplateVersion,
    Membership,
    Organization,
    Role,
)
from visitacampo.forms.definition import DSL_VERSION, validate_definition
from visitacampo.sync.changeseq import acquire_org_lock, highest_change_seq

__all__ = ["Command"]

#: El formulario fijo que `capture_screen.dart` envía (ver el docstring del
#: módulo del import de `forms/definition.py::FormFieldUnion` para el
#: catálogo completo de tipos): `altura_cm` (number) + `notas` (text).
#: Ninguno de los dos campos es `required` a propósito -- la pantalla de
#: captura permite guardar con `altura_cm` en 0
#: (`int.tryParse(_alturaController.text) ?? 0`) y `notas` vacío, así que
#: exigirlos aquí rechazaría exactamente la visita que la app real produce.
DEMO_FORM_DEFINITION: dict[str, Any] = {
    "dsl": DSL_VERSION,
    "title": {"es": "Visita de campo (demo)"},
    "fields": [
        {
            "key": "altura_cm",
            "type": "number",
            "label": {"es": "Altura (cm)"},
            "required": False,
            "min": 0,
        },
        {
            "key": "notas",
            "type": "text",
            "label": {"es": "Notas"},
            "required": False,
            "multiline": True,
        },
    ],
}

_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def _generate_password(length: int = 24) -> str:
    """Contraseña aleatoria criptográficamente segura (`secrets`, no `random`)."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


class Command(BaseCommand):
    help = (
        "Siembra una Organization, un AppUser (rol FIELD_AGENT), su Membership "
        "y una FormTemplateVersion publicada compatible con capture_screen.dart "
        "-- el minimo dato que un despliegue nuevo necesita para poder ejecutar "
        "login -> capturar visita -> sincronizar. Falla si ya existen (idempotente "
        "por rechazo, no duplica)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--org-name",
            default="VisitaCampo Demo",
            help="Nombre de la Organization a crear (por defecto 'VisitaCampo Demo').",
        )
        parser.add_argument(
            "--username",
            default="agente-demo",
            help="Username del AppUser de demo (por defecto 'agente-demo').",
        )
        parser.add_argument(
            "--email",
            default="agente-demo@example.com",
            help="Email del AppUser de demo.",
        )
        parser.add_argument(
            "--password",
            default=None,
            help=(
                "Contraseña del AppUser de demo. Si se omite, se genera una "
                "aleatoriamente y se imprime al final (nunca se hardcodea)."
            ),
        )
        parser.add_argument(
            "--template-name",
            default="Visita de campo (demo)",
            help="Nombre de la FormTemplate/FormTemplateVersion a publicar.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        org_name: str = options["org_name"]
        username: str = options["username"]
        email: str = options["email"]
        template_name: str = options["template_name"]
        password_was_given = options["password"] is not None
        password: str = options["password"] or _generate_password()

        app_user_model = get_user_model()

        # Comprobado ANTES de tocar la BD: fallar ruidoso y sin escribir nada
        # es lo que hace seguro correr el comando dos veces por error contra
        # el mismo entorno (ver docstring del módulo, "Idempotencia").
        if Organization.objects.filter(name=org_name).exists():
            raise CommandError(
                f"Ya existe una Organization llamada {org_name!r}. bootstrap_demo no "
                "vuelve a sembrar sobre un entorno ya inicializado (evitaria duplicar "
                "org/usuario/plantilla). Pasa --org-name distinto para un segundo demo."
            )
        if app_user_model.objects.filter(username=username).exists():
            raise CommandError(
                f"Ya existe un AppUser con username {username!r}. bootstrap_demo no "
                "vuelve a sembrar sobre un entorno ya inicializado. Pasa --username "
                "distinto para un segundo demo."
            )

        # Validado contra el mismo meta-schema que `FormTemplateVersion.clean()`
        # exige (Paso 20, ADR-012): si `capture_screen.dart` cambiara de forma
        # sin que alguien actualizara esta plantilla, el comando falla aquí con
        # un mensaje claro en vez de publicar una plantilla inconsistente.
        validate_definition(DEMO_FORM_DEFINITION)

        with transaction.atomic():
            org = Organization.objects.create(name=org_name)
            user = app_user_model.objects.create_user(
                username=username,
                email=email,
                password=password,
                org=org,
            )
            Membership.objects.create(org=org, user=user, role=Role.FIELD_AGENT)

            # `FormTemplateVersion` es sincronizable (SyncedModel): su
            # `change_seq` se asigna bajo el mismo `pg_advisory_xact_lock` que
            # el resto del protocolo exige (`sync/changeseq.py`), nunca a mano
            # fuera de ese mecanismo -- aunque en una organización recién
            # creada `highest_change_seq` ya daría 0 sin el lock, adquirirlo
            # de todas formas evita que este comando sea la única excepción
            # documentada a la invariante "ningún change_seq sin lock".
            acquire_org_lock(org.id)
            ctx = RequestContext(
                user_id=user.id,
                org_id=org.id,
                roles=frozenset({Role.FIELD_AGENT}),
            )
            template = FormTemplate.objects.create(org=org, name=template_name)
            template_version = FormTemplateVersion.objects.create(
                org=org,
                template=template,
                name=template_name,
                version_number=1,
                definition=DEMO_FORM_DEFINITION,
                change_seq=highest_change_seq(ctx) + 1,
                published_at=timezone.now(),
            )

        self.stdout.write(self.style.SUCCESS("Demo inicializado correctamente:"))
        self.stdout.write(f"  Organization id: {org.id}")
        self.stdout.write(f"  AppUser username: {user.username}  (email: {user.email})")
        if not password_was_given:
            self.stdout.write(
                self.style.WARNING(
                    f"  AppUser password (generada, copiala ahora -- no se vuelve a mostrar): "
                    f"{password}"
                )
            )
        self.stdout.write(
            f"  FormTemplateVersion id (UUID a pegar en 'ID de plantilla' de la app): "
            f"{template_version.id}"
        )

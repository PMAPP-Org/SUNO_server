"""Publicar una versión de plantilla, y la **regla de inmutabilidad** (Paso 20).

**La regla, en una frase**: una versión publicada no se edita nunca; para
cambiar un formulario se **publica otra versión**. Es lo que hace que una
visita histórica siga siendo interpretable años después (ADR-012) y también la
estrategia de rollback del propio paso: un error en una plantilla no puede
estropear las visitas ya capturadas con la versión anterior, porque esa versión
sigue existiendo intacta y es la que ellas referencian.

Dónde se hace cumplir, en dos capas y a propósito:

1. **Aquí**: `publish_version()` solo **inserta**. No hay ninguna función en
   este paquete que actualice el `definition` de una versión existente; el
   camino no existe, no es que esté vigilado.
2. **En el modelo** (`core/models/catalog.py::FormTemplateVersion.clean`): un
   `clean()`/`full_clean()` sobre una versión ya publicada cuyo `definition`,
   `version_number` o `template` cambien lanza `ValidationError`. Es la capa
   que cubre el Django Admin del Paso 24, que edita por `ModelForm`.

Lo que **no** se blinda, dicho explícitamente: un `.save()` crudo desde un
shell de Django se salta `clean()`, igual que se salta cualquier otra
validación de modelo en Django. Blindarlo exigiría un trigger en la base de
datos; se documenta la invariante y se deja la puerta del shell abierta con el
mismo criterio que el resto del proyecto (la BD garantiza forma —`CHECK`,
`UNIQUE`, `NOT NULL`—, la aplicación garantiza reglas de negocio).

**Superficie de API**: ninguna. Ver la decisión completa en
`visitacampo/forms/__init__.py` — el CRUD de plantillas es del Django Admin
(Paso 24) y esta función es lo que ese Admin llamará. El contrato de Paso 1 no
declara rutas de escritura de plantillas y este paso no las inventa.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import FormTemplate, FormTemplateVersion
from visitacampo.core.policies import CODE_NOT_FOUND, PolicyDenied, check_publish_form_template
from visitacampo.core.scoping import scoped_qs
from visitacampo.forms.definition import FormDefinition, validate_definition
from visitacampo.sync.changeseq import acquire_org_lock, highest_change_seq

__all__ = ["PUBLISHED_IMMUTABLE_FIELDS", "TemplateNotFound", "publish_version"]

#: Lo que queda congelado en cuanto una versión está publicada. `name` no está:
#: corregir una errata en el nombre de una versión no cambia ni una respuesta ni
#: cómo se interpreta una visita histórica. `definition` sí, obviamente; y
#: `version_number`/`template` porque son la identidad de la versión, es decir,
#: exactamente lo que `Visit.template_version_id` promete haber usado.
PUBLISHED_IMMUTABLE_FIELDS: tuple[str, ...] = (
    "definition",
    "version_number",
    "template_id",
    "org_id",
)


class TemplateNotFound(PolicyDenied):
    """404: la plantilla no existe **en esta organización** (RD-11).

    No se distingue de "no existe en absoluto" a propósito: confirmar la
    existencia de una plantilla ajena sería el oráculo de enumeración de H-16.
    """

    def __init__(self) -> None:
        super().__init__(404, CODE_NOT_FOUND, "Form template not found.")


@dataclass(slots=True)
class _OrgResource:
    """Ver `sync/services.py::_OrgResource` — misma forma, mismo motivo."""

    org_id: uuid.UUID


def publish_version(
    ctx: RequestContext,
    *,
    template_id: uuid.UUID,
    definition: Any,
    name: str | None = None,
) -> FormTemplateVersion:
    """Valida la definición y publica una **versión nueva** de la plantilla.

    Orden, y por qué es ese:

    1. **Validar antes de tocar la BD.** El meta-schema es una función pura del
       dato; comprobarlo dentro de la transacción solo alargaría el lock.
    2. **Autorizar antes de abrir la transacción** (ADR-013): la política es
       evaluación en memoria sobre el `ctx` ya resuelto, cero queries.
    3. **Lock de organización + `change_seq` dentro de la transacción**: una
       `form_template_version` es una entidad **sincronizable** (viaja por el
       pull, `sync/pull.py::_ENTITY_MODELS`), así que su `change_seq` se asigna
       bajo el mismo `pg_advisory_xact_lock` que el push. Sin ese lock, esta
       escritura podría commitear fuera del orden de asignación y un
       dispositivo que ya hubiera avanzado su cursor **no vería nunca la
       plantilla nueva** (doc 08 §4.4). Es el mismo mecanismo único de RD-14:
       no hay un segundo contador para las plantillas.
    4. **`version_number` correlativo por plantilla**, calculado bajo el lock:
       la `UniqueConstraint (template, version_number)` del Paso 5 es la red,
       no el mecanismo.
    """
    parsed: FormDefinition = validate_definition(definition)
    check_publish_form_template(ctx, _OrgResource(ctx.org_id))

    with transaction.atomic():
        acquire_org_lock(ctx.org_id)

        template = scoped_qs(ctx, FormTemplate).filter(pk=template_id).first()
        if template is None:
            raise TemplateNotFound

        highest = scoped_qs(ctx, FormTemplateVersion).filter(template_id=template_id).aggregate(
            top=models.Max("version_number")
        )["top"]

        return scoped_qs(ctx, FormTemplateVersion).create(
            org_id=ctx.org_id,
            template=template,
            name=name,
            version_number=(highest or 0) + 1,
            # `model_dump(mode="json")` y no el `definition` crudo: lo que se
            # guarda es la definición **normalizada por el meta-schema** (con
            # los valores por defecto explícitos, `dsl` incluido), de modo que
            # lo que el pull entrega al dispositivo es exactamente lo que este
            # servidor validó, y no un JSON parecido.
            definition=parsed.model_dump(mode="json", exclude_none=True),
            change_seq=highest_change_seq(ctx) + 1,
            published_at=timezone.now(),
        )

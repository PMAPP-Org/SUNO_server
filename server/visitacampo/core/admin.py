"""Registro mínimo en Django Admin (Paso 14: cierre de integración E2E Fase 0).

Doc 08 §8 Fase 0 exige verificar que "una visita creada offline en un
emulador aparece en el Django Admin tras reconectar". Para eso basta con que
`Visit` (y las tablas que la acompañan en el camino de sync/adjuntos,
`VisitAttachment` y `SyncOpLog`) sean visibles en `/admin/` con el registro
por defecto de Django (`admin.site.register`), sin personalización. El
back-office personalizado (list_display, filtros, exportación) es Paso 24,
fuera de alcance aquí.

**Ampliado en la revisión final de despliegue del demo (hallazgo Crítico)**:
`Organization`, `AppUser`, `Membership`, `FormTemplateVersion` y `Blob`
también se registran aquí, con el mismo criterio de registro por defecto sin
personalizar. Sin ellos, un operador con acceso a Django Admin no tiene
ninguna forma de crear una organización, dar de alta un usuario, asignarle
una membresía o publicar una plantilla de formulario en un despliegue nuevo
-- exactamente lo que `bootstrap_demo` (`visitacampo/management/commands/
bootstrap_demo.py`) siembra por línea de comandos, pero un operador puede
preferir/necesitar hacerlo (o corregirlo) a mano desde el Admin. `FormTemplate`
no se registra: no lo edita nadie fuera de `forms/services.py::publish_version`
o de crear una `FormTemplateVersion` con una `FormTemplate` ya existente, y el
Admin de `FormTemplateVersion` ya permite elegir/crear la `FormTemplate` desde
su propio formulario (FK). `Assignment`, `Device`, `Job` y `AuditEvent` quedan
fuera de este alcance: ninguno es necesario para el ciclo login -> capturar ->
sincronizar -> ver la visita, que es lo único que esta revisión final cierra.
"""

from __future__ import annotations

from django.contrib import admin

from visitacampo.core.models import (
    AppUser,
    Blob,
    FormTemplateVersion,
    Membership,
    Organization,
    SyncOpLog,
    Visit,
    VisitAttachment,
)

admin.site.register(Visit)
admin.site.register(VisitAttachment)
admin.site.register(SyncOpLog)
admin.site.register(Organization)
admin.site.register(AppUser)
admin.site.register(Membership)
admin.site.register(FormTemplateVersion)
admin.site.register(Blob)

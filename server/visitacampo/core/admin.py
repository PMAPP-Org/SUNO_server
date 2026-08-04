"""Registro mínimo en Django Admin (Paso 14: cierre de integración E2E Fase 0).

Doc 08 §8 Fase 0 exige verificar que "una visita creada offline en un
emulador aparece en el Django Admin tras reconectar". Para eso basta con que
`Visit` (y las tablas que la acompañan en el camino de sync/adjuntos,
`VisitAttachment` y `SyncOpLog`) sean visibles en `/admin/` con el registro
por defecto de Django (`admin.site.register`), sin personalización. El
back-office personalizado (list_display, filtros, exportación) es Paso 24,
fuera de alcance aquí.
"""

from __future__ import annotations

from django.contrib import admin

from visitacampo.core.models import SyncOpLog, Visit, VisitAttachment

admin.site.register(Visit)
admin.site.register(VisitAttachment)
admin.site.register(SyncOpLog)

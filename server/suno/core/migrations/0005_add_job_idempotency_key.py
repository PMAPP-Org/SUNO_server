"""`job.idempotency_key` + índice único parcial: el invariante de RD-14.

Paso 19 (tarea 6), ADR-006: *"unicidad lógica con `idempotency_key` y
**constraint único parcial**, no con 'check de concurrencia' duplicado en dos
módulos"*. Es la corrección de **H-13** (TOCTOU en "un job activo por
proyecto"): con el índice, el encolado es un `INSERT ... ON CONFLICT DO
NOTHING` de una sola sentencia y no existe ventana entre comprobar y crear.

Tres operaciones y ninguna es cosmética:

- **la columna** — `NULL` por defecto, que en un índice único de PostgreSQL
  significa "este job no compite con nadie" (los `NULL` son distintos entre
  sí). Los jobs de usuario la dejan vacía; los periódicos la usan;
- **el índice único parcial** sobre `(org_id, idempotency_key)` restringido a
  `status IN ('pending','running')` — solo los jobs *activos* ocupan la clave,
  o un periódico no podría volver a encolarse nunca;
- **el índice `(status, run_after)`** — el `take` del worker barre todas las
  organizaciones (un worker no es de ningún tenant), así que el índice
  existente `(org_id, status, run_after)` del Paso 5 no lo sirve.

Migración aditiva y reversible: sobre una tabla `job` con filas, la columna
nace `NULL` en todas y el índice parcial no puede fallar por datos previos
(ninguna fila existente tiene clave que colisionar).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_add_sync_cursor_floor'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='idempotency_key',
            field=models.CharField(blank=True, default=None, help_text='Identifica el trabajo lógico (tipo + entidad). Mientras exista un job activo con esta clave en la organización, no se puede encolar otro. NULL = sin invariante de unicidad.', max_length=200, null=True, verbose_name='clave de idempotencia'),
        ),
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['status', 'run_after'], name='job_status_run_idx'),
        ),
        migrations.AddConstraint(
            model_name='job',
            constraint=models.UniqueConstraint(condition=models.Q(('status__in', ('pending', 'running'))), fields=('org', 'idempotency_key'), name='job_active_idempotency_uq'),
        ),
    ]

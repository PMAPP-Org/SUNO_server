"""`sync_cursor_floor`: una fila por organización, piso de purga de tombstones.

Paso 9, hallazgo de diseño (no de revisión): `GET /v1/sync/pull` necesita
poder responder `409 cursor_expired` cuando el cursor del cliente es anterior
a una purga de tombstones ya ejecutada (`sync/retention.py`) — si no, el
cliente perdería en silencio la existencia de filas borradas y ya purgadas
físicamente. Ver el docstring de `core/models/ops.py::SyncCursorFloor` para
el razonamiento completo (es el mismo principio que `changeseq.py` aplica al
lock de organización, RD-14: una sola fuente de verdad, monótona).

Tabla vacía hasta la primera purga de cada organización (`retention.py` la
crea perezosamente); una organización sin purgas no la necesita, su piso
implícito es 0.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_add_visit_float_finiteness_checks'),
    ]

    operations = [
        migrations.CreateModel(
            name='SyncCursorFloor',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('floor', models.PositiveBigIntegerField(default=0, help_text='Todo change_seq por debajo de este valor puede tener tombstones ya purgados.', verbose_name='piso de purga')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('org', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='%(class)ss', to='core.organization', verbose_name='organización')),
            ],
            options={
                'verbose_name': 'piso de cursor de sync',
                'verbose_name_plural': 'pisos de cursor de sync',
                'db_table': 'sync_cursor_floor',
                'constraints': [models.UniqueConstraint(fields=('org',), name='sync_cursor_floor_org_uq')],
            },
        ),
    ]

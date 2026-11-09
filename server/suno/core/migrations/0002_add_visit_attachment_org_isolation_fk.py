"""Aislamiento entre organizaciones de `visit_attachment` (tarea 12 del Paso 5).

Añade la **FK compuesta** `visit_attachment (visit_id, org_id) → visit (id,
org_id)`, que hace imposible en la BD que un adjunto de la organización A
apunte a una visita de la organización B.

**Por qué `RunSQL` y no una constraint declarativa**: Django no sabe
expresar FKs compuestas (`ForeignKey` es siempre de una columna, y
`CheckConstraint` no puede leer otra tabla). Es el caso previsto por el
patrón **C-18** de doc 08 §7.1: SQL crudo *siempre* con su contraparte
reversa, de modo que `migrate core 0001` deshaga limpiamente esta
migración (criterio de aceptación "migración reversible" del brief).

**Destino de la FK**: `visit.Meta.constraints` ya declara
`UniqueConstraint(fields=["id", "org"], name="visit_id_org_uq")` — creado
en `0001` —, porque PostgreSQL exige que las columnas referenciadas por una
FK sean exactamente las de una constraint UNIQUE (o PK) no diferible. El
UNIQUE es redundante para la unicidad (`id` ya es PK), pero es la única
forma de tener ese destino; y expresa además la invariante "una visita no
cambia de organización sin cambiar de identidad".

**Nombres de columna**: verificados contra `Model._meta` (no adivinados):
`visit_attachment` → `visit_id`, `org_id`; `visit` → `id`, `org_id`.

**`DEFERRABLE INITIALLY DEFERRED`**: es exactamente el modo en que Django
crea *todas* las demás FKs en PostgreSQL. Se mantiene el mismo régimen para
no dejar una única constraint con semántica distinta al resto del esquema
(un `SET CONSTRAINTS ALL DEFERRED` —lo que hace `loaddata` internamente—
ignoraría una FK `NOT DEFERRABLE` y fallaría solo en esta). La garantía no
se debilita: una transacción que viole la FK no puede confirmarse. Lo único
que cambia es *cuándo* salta el error (en el `COMMIT`, no en el `INSERT`);
el test correspondiente fuerza la comprobación con `SET CONSTRAINTS ALL
IMMEDIATE`.

**Alcance**: esta constraint cubre las *escrituras*. Las *lecturas* siguen
necesitando el `scoped_qs` del Paso 6 (RD-11); son garantías
complementarias, no sustitutas.
"""

from django.db import migrations

# El nombre de la constraint (`visit_attachment_visit_org_fk`) se escribe
# literal en ambos sentidos a propósito: el SQL de una migración debe poder
# leerse y ejecutarse tal cual, sin interpolaciones de Python.
ADD_COMPOSITE_FK = """
ALTER TABLE "visit_attachment"
    ADD CONSTRAINT "visit_attachment_visit_org_fk"
    FOREIGN KEY ("visit_id", "org_id")
    REFERENCES "visit" ("id", "org_id")
    DEFERRABLE INITIALLY DEFERRED;
"""

DROP_COMPOSITE_FK = """
ALTER TABLE "visit_attachment"
    DROP CONSTRAINT "visit_attachment_visit_org_fk";
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ADD_COMPOSITE_FK,
            reverse_sql=DROP_COMPOSITE_FK,
            # La FK no cambia el estado del modelo que Django conoce (no hay
            # campo nuevo), así que `state_operations` queda vacío a
            # propósito: `makemigrations --check` no debe detectar deriva por
            # esta migración.
            state_operations=[],
        ),
    ]

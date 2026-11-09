"""`visit.accuracy_m` y `visit.altitude_m` no pueden ser `NaN` ni `±Infinity`.

Revisión del Paso 8, hallazgo C-2. `NaN` e `Infinity` son valores **legales**
de `double precision` en PostgreSQL: se escribían con éxito y solo envenenaban
la fila más tarde, al serializarla a `jsonb` (`sync_op_log.result`, cuyo
encoder emite el literal desnudo `NaN` que `jsonb` rechaza ⇒ `DataError` ⇒ 500
con pérdida del lote entero) o al devolverla en el pull del Paso 9, donde `NaN`
desnudo no es JSON válido y el `jsonDecode` de Dart lo rechaza.

La guarda primaria vive en `sync/payloads.py` (`Finite`, `allow_inf_nan=False`),
que es donde cuesta cero queries. Esto es la **defensa en profundidad**: cubre
toda escritura que no venga del push (back-office del Paso 17, shell, carga de
datos). Ver `core/models/base.py::finite_q` para por qué la condición es
"es finito" y no un rango de altitud arbitrario.

`lat`/`lon` no aparecen aquí: sus `CHECK` de rango ya rechazan los tres
valores (en PostgreSQL `NaN` se ordena como **mayor que todo**, así que
`NaN <= 90` es `false`). `accuracy_m` sí aparece porque `>= 0` no bastaba:
tanto `NaN >= 0` como `Infinity >= 0` son `true`.

**SQL revisado a mano** (no se pudo aplicar: no hay PostgreSQL ni Docker en el
entorno de desarrollo, misma limitación que en los Pasos 5-8; y `sqlmigrate`
tampoco corre offline porque el `SchemaEditor` de PostgreSQL pasa cada
sentencia por `mogrify()`, que exige un cursor vivo). DDL generado::

    -- forwards
    ALTER TABLE "visit" DROP CONSTRAINT "visit_accuracy_non_negative";
    ALTER TABLE "visit" ADD CONSTRAINT "visit_accuracy_finite_non_negative"
        CHECK (("accuracy_m" IS NULL OR ("accuracy_m" >= 0.0
                AND "accuracy_m" > '-Infinity'::float8
                AND "accuracy_m" < 'Infinity'::float8)));
    ALTER TABLE "visit" ADD CONSTRAINT "visit_altitude_finite"
        CHECK (("altitude_m" IS NULL OR ("altitude_m" > '-Infinity'::float8
                AND "altitude_m" < 'Infinity'::float8)));

    -- backwards (reversible, patrón C-18)
    ALTER TABLE "visit" DROP CONSTRAINT "visit_altitude_finite";
    ALTER TABLE "visit" DROP CONSTRAINT "visit_accuracy_finite_non_negative";
    ALTER TABLE "visit" ADD CONSTRAINT "visit_accuracy_non_negative"
        CHECK (("accuracy_m" IS NULL OR "accuracy_m" >= 0.0));

El `backwards` restituye exactamente la constraint que creó `0001`, carácter
por carácter.

**Sobre los datos existentes**: `ADD CONSTRAINT` valida las filas ya presentes,
así que si alguna base de datos tuviera ya un `NaN` escrito por el push previo
a este fix, la migración **fallaría en vez de dejar el veneno dentro**. Es el
comportamiento correcto: esa fila hay que limpiarla a mano, no ignorarla.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_add_visit_attachment_org_isolation_fk'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='visit',
            name='visit_accuracy_non_negative',
        ),
        migrations.AddConstraint(
            model_name='visit',
            constraint=models.CheckConstraint(condition=models.Q(('accuracy_m__isnull', True), models.Q(('accuracy_m__gte', 0), ('accuracy_m__gt', float("-inf")), ('accuracy_m__lt', float("inf"))), _connector='OR'), name='visit_accuracy_finite_non_negative'),
        ),
        migrations.AddConstraint(
            model_name='visit',
            constraint=models.CheckConstraint(condition=models.Q(('altitude_m__isnull', True), models.Q(('altitude_m__gt', float("-inf")), ('altitude_m__lt', float("inf"))), _connector='OR'), name='visit_altitude_finite'),
        ),
    ]

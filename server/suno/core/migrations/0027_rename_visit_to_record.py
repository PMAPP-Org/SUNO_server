"""Renombre aditivo de la visita al registro, sobre una base de datos con datos reales.

Producción tiene aplicadas 0001-0026 con los nombres viejos, así que el renombre va en
una migración nueva en lugar de reescribir las anteriores.

**Esquema**: con `lock_timeout` de 10 s, para fallar rápido si otra sesión retiene una
tabla. Todo son `RENAME`, sin reescribir tablas ni revalidar constraints. Por eso va
en un `SeparateDatabaseAndState` con el SQL escrito a mano: `RenameField` sobre una FK
borra la constraint y la vuelve a crear, lo que revalida la tabla entera. Los nombres
generados por Django (`visit_device_id_eb2ac8a1_fk_device_id`) se renombran igual, leídos
del catálogo: son distintos en cada base y no están en el estado de las migraciones.

**Datos guardados que llevan el nombre viejo** (`rename_stored_data`):

- `django_content_type` y `auth_permission`. Los permisos ya asignados se conservan: sus
  filas se renombran en el sitio, sin tocar las tablas intermedias. Si ya existiera la
  fila nueva, se funden las asignaciones en ella.
- `sync_op_log`: el resultado guardado se devuelve tal cual en un reenvío, así que debe
  hablar el contrato de ahora (`entity`, `server_row`, `error.details`, mensajes). Se
  reescribe con `UPDATE` de conjunto en SQL, no fila a fila: la tabla guarda 90 días.
- `form_template_version.definition.dsl`: el dialecto `visitacampo/form@1` lo dejó de
  aceptar el renombre del paquete y hace rechazar las respuestas contra esas versiones.
  Esta corrección solo va hacia delante: el código anterior tampoco acepta el dialecto
  viejo, así que deshacerla volvería a romperlas.

**Lo que no se toca, a propósito**: `audit_event`. La traza es un registro de lo ocurrido
(ISO 27001 A.8.15, 5.33 de la declaración de aplicabilidad: sin cambios ni borrado, salvo
la purga por política), y las filas conservan el código y el objetivo con que se
escribieron (`backoffice.visit_reviewed`, `visit:<uuid>`). Quien las lee no filtra por
esos códigos: el Admin y la exportación de datos personales las muestran tal cual.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

TABLES = (
    ("visit", "record"),
    ("visit_attachment", "record_attachment"),
    ("visit_review", "record_review"),
)
#: Tablas (con el nombre nuevo) cuya columna `visit_id` pasa a `record_id`.
TABLES_WITH_FK = ("record_attachment", "record_review")

#: Modelo viejo, modelo nuevo, `verbose_name` viejo y nuevo (para el nombre del permiso).
MODELS = (
    ("visit", "record", "visita", "registro"),
    ("visitattachment", "recordattachment", "adjunto de visita", "adjunto de registro"),
    ("visitreview", "recordreview", "revisión de visita", "revisión de registro"),
)
ACTIONS = ("add", "change", "delete", "view")

ENTITIES = {"visit": "record", "visit_attachment": "record_attachment"}
REASONS = {"visit_validated": "record_validated"}
MESSAGES = {
    "The visit was validated: it cannot be deleted.": (
        "The record was validated: it cannot be deleted."
    ),
    "The visit was validated: its task, progress and quantities cannot change.": (
        "The record was validated: its task, progress and quantities cannot change."
    ),
    "Referenced visit_id does not exist in this organization.": (
        "Referenced record_id does not exist in this organization."
    ),
    "Only the visit's author or a supervisor can modify it.": (
        "Only the record's author or a supervisor can modify it."
    ),
    "A visit linked to a task needs progress_pct or at least one quantity.": (
        "A record linked to a task needs progress_pct or at least one quantity."
    ),
    "visit_attachment only supports create in this server version.": (
        "record_attachment only supports create in this server version."
    ),
}


def _names(cursor, query, table):
    cursor.execute(query, [table])
    return [row[0] for row in cursor.fetchall()]


def _rename_catalog(cursor, table, old_word, new_word):
    """Renombra constraints e índices de `table`; un `UNIQUE` o PK arrastra su índice."""
    constraints = _names(
        cursor, "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", table
    )
    for name in constraints:
        if old_word in name:
            cursor.execute(
                f'ALTER TABLE "{table}" RENAME CONSTRAINT '
                f'"{name}" TO "{name.replace(old_word, new_word)}"'
            )
    indexes = _names(
        cursor,
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s",
        table,
    )
    for name in indexes:
        if old_word in name:
            cursor.execute(f'ALTER INDEX "{name}" RENAME TO "{name.replace(old_word, new_word)}"')


def rename_schema(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        for old_table, new_table in TABLES:
            cursor.execute(f'ALTER TABLE "{old_table}" RENAME TO "{new_table}"')
        for table in TABLES_WITH_FK:
            cursor.execute(f'ALTER TABLE "{table}" RENAME COLUMN "visit_id" TO "record_id"')
        for _, new_table in TABLES:
            _rename_catalog(cursor, new_table, "visit", "record")


def unrename_schema(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        for _, new_table in TABLES:
            _rename_catalog(cursor, new_table, "record", "visit")
        for table in TABLES_WITH_FK:
            cursor.execute(f'ALTER TABLE "{table}" RENAME COLUMN "record_id" TO "visit_id"')
        for old_table, new_table in TABLES:
            cursor.execute(f'ALTER TABLE "{new_table}" RENAME TO "{old_table}"')


def _rename_content_type(content_types, old_model, new_model):
    """Renombra el tipo y devuelve la fila vigente, o `None` si no había ninguna."""
    source = content_types.filter(app_label="core", model=old_model).first()
    target = content_types.filter(app_label="core", model=new_model).first()
    if target is not None:
        return target
    if source is None:
        return None
    source.model = new_model
    source.save(update_fields=["model"])
    return source


def _merge_grants(through, field, source, target):
    """Pasa a `target` las asignaciones de `source` que aún no tiene."""
    already = through.objects.filter(permission=target).values_list(field, flat=True)
    through.objects.filter(permission=source).exclude(**{f"{field}__in": already}).update(
        permission=target
    )
    through.objects.filter(permission=source).delete()


def _rename_permission(apps, content_type, old_code, new_code, new_name):
    permissions = apps.get_model("auth", "Permission").objects
    group_grants = apps.get_model("auth", "Group").permissions.through
    user_grants = apps.get_model("core", "AppUser").user_permissions.through
    for permission in permissions.filter(content_type__app_label="core", codename=old_code):
        target = (
            permissions.filter(content_type=content_type, codename=new_code)
            .exclude(pk=permission.pk)
            .first()
        )
        if target is None:
            permission.content_type = content_type
            permission.codename = new_code
            permission.name = new_name
            permission.save(update_fields=["content_type", "codename", "name"])
            continue
        _merge_grants(group_grants, "group_id", permission, target)
        _merge_grants(user_grants, "appuser_id", permission, target)
        permission.delete()


def _rename_permissions(apps, *, forwards):
    content_types = apps.get_model("contenttypes", "ContentType").objects
    for old_model, new_model, old_verbose, new_verbose in MODELS:
        source, target = (old_model, new_model) if forwards else (new_model, old_model)
        verbose = new_verbose if forwards else old_verbose
        content_type = _rename_content_type(content_types, source, target)
        if content_type is None:
            continue
        for action in ACTIONS:
            _rename_permission(
                apps,
                content_type,
                f"{action}_{source}",
                f"{action}_{target}",
                f"Can {action} {verbose}",
            )


#: Rutas de `result` cuyo valor se cambia solo si coincide exactamente con uno del mapa.
#: Las de adjuntos (`attachment_only`) son las que nombran la FK: en un registro, un
#: `field` puede ser la ruta de un campo del formulario, que escribe el usuario.
def _value_rules(entities, reasons, messages, old_key, new_key):
    return (
        (("entity",), entities, False),
        (("server_row", "entity"), entities, False),
        (("error", "message"), messages, False),
        (("error", "details", "reason"), reasons, False),
        (("error", "details", "field"), {old_key: new_key}, True),
    )


def _rewrite_op_log(cursor, entities, reasons, messages, old_key, new_key):
    """Reescribe `sync_op_log` con `UPDATE` de conjunto: una pasada por regla, en SQL.

    Cada `UPDATE` filtra por la entidad vieja, así que la columna `entity` va la última.
    """
    attachment = next(old for old in entities if old.endswith("_attachment"))
    rows = "entity IN (%s, %s)"
    olds = list(entities)
    for path, mapping, attachment_only in _value_rules(
        entities, reasons, messages, old_key, new_key
    ):
        cases = " ".join("WHEN %s THEN %s" for _ in mapping)
        values = [item for pair in mapping.items() for item in pair]
        where, where_params = ("entity = %s", [attachment]) if attachment_only else (rows, olds)
        cursor.execute(
            "UPDATE sync_op_log SET result = jsonb_set(result, %s::text[], "
            f"to_jsonb(CASE result #>> %s::text[] {cases} END)) "
            f"WHERE {where} AND jsonb_typeof(result #> %s::text[]) = 'string' "
            f"AND result #>> %s::text[] IN ({', '.join(['%s'] * len(mapping))})",
            [list(path), list(path), *values, *where_params, list(path), list(path), *mapping],
        )
    # La FK dentro de la fila del servidor de un conflicto de adjunto: se renombra la clave.
    cursor.execute(
        "UPDATE sync_op_log SET result = jsonb_set(result, '{server_row,data}', "
        "(result #> '{server_row,data}') - %s "
        "|| jsonb_build_object(%s, result #> ARRAY['server_row', 'data', %s])) "
        "WHERE entity = %s AND jsonb_typeof(result #> '{server_row,data}') = 'object' "
        "AND jsonb_exists(result #> '{server_row,data}', %s)",
        [old_key, new_key, old_key, attachment, old_key],
    )
    # Errores de validación del payload (`api_errors.redacted_validation_details`).
    cursor.execute(
        "UPDATE sync_op_log SET result = jsonb_set(result, '{error,details,errors}', "
        "(SELECT jsonb_agg(CASE WHEN e ->> 'field' = %s "
        "THEN jsonb_set(e, '{field}', to_jsonb(%s::text)) ELSE e END ORDER BY i) "
        "FROM jsonb_array_elements(result #> '{error,details,errors}') "
        "WITH ORDINALITY AS t(e, i))) "
        "WHERE entity = %s AND jsonb_typeof(result #> '{error,details,errors}') = 'array' "
        "AND EXISTS (SELECT 1 FROM jsonb_array_elements(result #> '{error,details,errors}') e "
        "WHERE e ->> 'field' = %s)",
        [old_key, new_key, attachment, old_key],
    )
    cases = " ".join("WHEN %s THEN %s" for _ in entities)
    cursor.execute(
        f"UPDATE sync_op_log SET entity = CASE entity {cases} END WHERE {rows}",
        [item for pair in entities.items() for item in pair] + olds,
    )


def _invert(mapping):
    return {new: old for old, new in mapping.items()}


def rename_stored_data(apps, schema_editor):
    _rename_permissions(apps, forwards=True)
    with schema_editor.connection.cursor() as cursor:
        _rewrite_op_log(cursor, ENTITIES, REASONS, MESSAGES, "visit_id", "record_id")
        cursor.execute(
            "UPDATE form_template_version "
            "SET definition = jsonb_set(definition, '{dsl}', '\"suno/form@1\"') "
            "WHERE definition->>'dsl' = 'visitacampo/form@1'"
        )


def unrename_stored_data(apps, schema_editor):
    _rename_permissions(apps, forwards=False)
    with schema_editor.connection.cursor() as cursor:
        _rewrite_op_log(
            cursor,
            _invert(ENTITIES),
            _invert(REASONS),
            _invert(MESSAGES),
            "record_id",
            "visit_id",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0026_equipment_transport"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(rename_schema, unrename_schema)],
            state_operations=[
                migrations.RenameModel("Visit", "Record"),
                migrations.RenameModel("VisitAttachment", "RecordAttachment"),
                migrations.RenameModel("VisitReview", "RecordReview"),
                migrations.AlterModelTable("record", "record"),
                migrations.AlterModelTable("recordattachment", "record_attachment"),
                migrations.AlterModelTable("recordreview", "record_review"),
                migrations.RenameField("recordattachment", "visit", "record"),
                migrations.RenameField("recordreview", "visit", "record"),
                migrations.RenameIndex(
                    "record", new_name="record_org_lat_lon_idx", old_name="visit_org_lat_lon_idx"
                ),
                migrations.RenameIndex(
                    "record",
                    new_name="record_org_change_seq_idx",
                    old_name="visit_org_change_seq_idx",
                ),
                migrations.RenameIndex(
                    "record",
                    new_name="record_task_captured_idx",
                    old_name="visit_task_captured_idx",
                ),
                migrations.RemoveIndex("recordattachment", "attachment_visit_order_idx"),
                migrations.RemoveIndex("recordreview", "review_org_visit_idx"),
                migrations.RemoveConstraint("record", "visit_lat_range"),
                migrations.RemoveConstraint("record", "visit_lon_range"),
                migrations.RemoveConstraint("record", "visit_id_org_uq"),
                migrations.RemoveConstraint("record", "visit_accuracy_finite_non_negative"),
                migrations.RemoveConstraint("record", "visit_altitude_finite"),
                migrations.RemoveConstraint("record", "visit_task_progress_pair"),
                migrations.AlterModelOptions(
                    name="record",
                    options={"verbose_name": "registro", "verbose_name_plural": "registros"},
                ),
                migrations.AlterModelOptions(
                    name="recordattachment",
                    options={
                        "verbose_name": "adjunto de registro",
                        "verbose_name_plural": "adjuntos de registro",
                    },
                ),
                migrations.AlterModelOptions(
                    name="recordreview",
                    options={
                        "ordering": ("-created_at",),
                        "verbose_name": "revisión de registro",
                        "verbose_name_plural": "revisiones de registro",
                    },
                ),
                migrations.AlterField(
                    model_name="record",
                    name="assignment",
                    field=models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="records",
                        to="core.assignment",
                        verbose_name="asignación",
                    ),
                ),
                migrations.AlterField(
                    model_name="record",
                    name="captured_at",
                    field=models.DateTimeField(verbose_name="capturado en"),
                ),
                migrations.AlterField(
                    model_name="record",
                    name="created_by",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="records",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="creado por",
                    ),
                ),
                migrations.AlterField(
                    model_name="record",
                    name="device",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="records",
                        to="core.device",
                        verbose_name="dispositivo",
                    ),
                ),
                migrations.AlterField(
                    model_name="record",
                    name="task",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="field_records",
                        to="core.task",
                        verbose_name="tarea del cronograma",
                    ),
                ),
                migrations.AlterField(
                    model_name="record",
                    name="template_version",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="records",
                        to="core.formtemplateversion",
                        verbose_name="versión de plantilla",
                    ),
                ),
                migrations.AlterField(
                    model_name="recordattachment",
                    name="record",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attachments",
                        to="core.record",
                        verbose_name="registro",
                    ),
                ),
                migrations.AlterField(
                    model_name="recordreview",
                    name="record",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviews",
                        to="core.record",
                        verbose_name="registro",
                    ),
                ),
                migrations.AlterField(
                    model_name="recordreview",
                    name="reviewer",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="record_reviews",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="revisada por",
                    ),
                ),
                migrations.AlterField(
                    model_name="recordreview",
                    name="status",
                    field=models.CharField(
                        choices=[("approved", "Validado"), ("rejected", "Rechazado")],
                        max_length=16,
                        verbose_name="decisión",
                    ),
                ),
                migrations.AlterField(
                    model_name="syncoplog",
                    name="entity",
                    field=models.CharField(
                        choices=[
                            ("record", "Registro"),
                            ("record_attachment", "Adjunto de registro"),
                            ("assignment", "Asignación"),
                            ("form_template_version", "Versión de plantilla"),
                        ],
                        max_length=32,
                    ),
                ),
                migrations.AddIndex(
                    "recordattachment",
                    models.Index(fields=["record", "order"], name="attachment_record_order_idx"),
                ),
                migrations.AddIndex(
                    "recordreview",
                    models.Index(
                        fields=["org", "record", "-created_at"], name="review_org_record_idx"
                    ),
                ),
                migrations.AddConstraint(
                    "record",
                    models.CheckConstraint(
                        condition=models.Q(
                            ("lat__isnull", True),
                            models.Q(("lat__gte", -90.0), ("lat__lte", 90.0)),
                            _connector="OR",
                        ),
                        name="record_lat_range",
                    ),
                ),
                migrations.AddConstraint(
                    "record",
                    models.CheckConstraint(
                        condition=models.Q(
                            ("lon__isnull", True),
                            models.Q(("lon__gte", -180.0), ("lon__lte", 180.0)),
                            _connector="OR",
                        ),
                        name="record_lon_range",
                    ),
                ),
                migrations.AddConstraint(
                    "record",
                    models.CheckConstraint(
                        condition=models.Q(
                            ("accuracy_m__isnull", True),
                            models.Q(
                                ("accuracy_m__gte", 0),
                                ("accuracy_m__gt", float("-inf")),
                                ("accuracy_m__lt", float("inf")),
                            ),
                            _connector="OR",
                        ),
                        name="record_accuracy_finite_non_negative",
                    ),
                ),
                migrations.AddConstraint(
                    "record",
                    models.CheckConstraint(
                        condition=models.Q(
                            ("altitude_m__isnull", True),
                            models.Q(
                                ("altitude_m__gt", float("-inf")),
                                ("altitude_m__lt", float("inf")),
                            ),
                            _connector="OR",
                        ),
                        name="record_altitude_finite",
                    ),
                ),
                migrations.AddConstraint(
                    "record",
                    models.UniqueConstraint(fields=("id", "org"), name="record_id_org_uq"),
                ),
                migrations.AddConstraint(
                    "record",
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("task__isnull", True),
                                ("progress_pct__isnull", True),
                                ("quantities", []),
                            ),
                            models.Q(
                                ("task__isnull", False),
                                models.Q(
                                    models.Q(
                                        ("progress_pct__gte", 0),
                                        ("progress_pct__isnull", False),
                                        ("progress_pct__lte", 100),
                                    ),
                                    models.Q(
                                        ("progress_pct__isnull", True),
                                        models.Q(("quantities", []), _negated=True),
                                    ),
                                    _connector="OR",
                                ),
                            ),
                            _connector="OR",
                        ),
                        name="record_task_progress_pair",
                    ),
                ),
            ],
        ),
        migrations.RunPython(rename_stored_data, unrename_stored_data),
    ]

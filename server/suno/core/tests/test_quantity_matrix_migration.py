"""La migración 0014 (matriz de cantidades) se deshace y se vuelve a aplicar con datos.

La reversa tiene lógica propia (`unlink_quantity_only_visits`): una visita con tarea y
solo cantidades no cabe en el CHECK de 0013 y se desvincula de la tarea. Se comprueba
contra PostgreSQL con `MigrationExecutor`, como `test_rename_visit_migration.py`: las
filas se crean con los modelos históricos de 0014, se migra a 0013 y de vuelta a 0014.

**Una base aparte y vacía.** Deshacer desde las hojas del grafo hasta 0013 y volver
costaba ~5 min (17 migraciones de ida y vuelta, cada una reconstruyendo el estado). Aquí
se crea `test_<NAME>_m0014`, vacía, se migra solo hasta 0014 (lo que exige el grafo) y se
borra al terminar. La BD de test del módulo y la plantilla de `dbsupport` no se tocan: la
conexión `default` apunta a la base aparte solo durante la prueba (como hace
`dbsupport._create_from_template`) y vuelve a la suya en la limpieza. Se usa la conexión
`default` y no otro alias porque la función de la reversa consulta sin `using()`.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

setUpModule = setup_test_database
tearDownModule = teardown_test_database

BEFORE = [("core", "0013_add_budgets")]
AFTER = [("core", "0014_add_quantity_matrix")]
NEW_TABLES = {"location_level", "location_node", "quantity_plan", "quantity_plan_import"}
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def migrate_to(targets):
    """Migra y devuelve los modelos históricos de ese punto del grafo."""
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


def tables() -> set[str]:
    return set(connection.introspection.table_names())


def columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return {
            column.name for column in connection.introspection.get_table_description(cursor, table)
        }


def _admin(*statements: str) -> None:
    """CREATE/DROP DATABASE fuera de cualquier base de test (sin conexiones abiertas)."""
    connection.close()
    with connection.creation._nodb_cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def _use(name: str) -> None:
    connection.close()
    settings.DATABASES[connection.alias]["NAME"] = name
    connection.settings_dict["NAME"] = name


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class QuantityMatrixMigrationTests(unittest.TestCase):
    def setUp(self):
        home = connection.settings_dict["NAME"]
        probe = f"{home}_m0014"
        quoted = connection.ops.quote_name(probe)
        _admin(f"DROP DATABASE IF EXISTS {quoted}", f"CREATE DATABASE {quoted}")
        self.addCleanup(_admin, f"DROP DATABASE IF EXISTS {quoted}")
        self.addCleanup(_use, home)
        _use(probe)
        self.apps = migrate_to(AFTER)
        self._seed()

    def _seed(self):
        apps = self.apps
        org = apps.get_model("core", "Organization").objects.create(name="Obra")
        user = apps.get_model("core", "AppUser").objects.create(
            username="residente", password="x", org=org
        )
        device = apps.get_model("core", "Device").objects.create(
            id=uuid.uuid4(), org=org, owner=user, platform="android", app_version="1"
        )
        template = apps.get_model("core", "FormTemplate").objects.create(org=org, name="F")
        version = apps.get_model("core", "FormTemplateVersion").objects.create(
            org=org,
            template=template,
            version_number=1,
            change_seq=1,
            definition={"dsl": "suno/form@1", "fields": []},
        )
        project = apps.get_model("core", "Project").objects.create(
            org=org, name="Edificio", created_by=user
        )
        task = apps.get_model("core", "Task").objects.create(
            org=org,
            project=project,
            uid=1,
            outline_number="1",
            outline_level=1,
            sort_order=1,
            name="Muros",
        )
        level = apps.get_model("core", "LocationLevel").objects.create(
            org=org, project=project, name="Piso", depth=1
        )
        apps.get_model("core", "LocationNode").objects.create(
            org=org, project=project, level=level, name="Piso 1", key="p1", path="p1"
        )

        visits = apps.get_model("core", "Visit").objects
        common = {
            "org": org,
            "template_version": version,
            "device": device,
            "created_by": user,
            "captured_at": NOW,
            "answers": {},
        }
        quantities = [{"item": str(uuid.uuid4()), "quantity": "3.5"}]
        self.with_pct = visits.create(
            id=uuid.uuid4(),
            change_seq=2,
            task=task,
            progress_pct=Decimal("40"),
            quantities=quantities,
            **common,
        ).pk
        self.only_quantities = visits.create(
            id=uuid.uuid4(), change_seq=3, task=task, quantities=quantities, **common
        ).pk
        self.without_task = visits.create(id=uuid.uuid4(), change_seq=4, **common).pk
        self.task_id = task.pk

    def test_backwards_and_forwards_keep_the_schema_and_the_rows_that_fit(self):
        self.assertLessEqual(NEW_TABLES, tables())
        self.assertIn("quantities", columns("visit"))
        self.assertIn("location_id", columns("project_task"))

        old_apps = migrate_to(BEFORE)
        self.assertFalse(NEW_TABLES & tables())
        self.assertNotIn("quantities", columns("visit"))
        self.assertNotIn("location_id", columns("project_task"))
        visits = old_apps.get_model("core", "Visit").objects
        self.assertEqual(visits.count(), 3)
        kept = visits.get(pk=self.with_pct)
        self.assertEqual((kept.task_id, kept.progress_pct), (self.task_id, Decimal("40")))
        # La de solo cantidades no cabe en el CHECK de 0013: se desvincula de la tarea.
        unlinked = visits.get(pk=self.only_quantities)
        self.assertEqual((unlinked.task_id, unlinked.progress_pct), (None, None))
        self.assertIsNone(visits.get(pk=self.without_task).task_id)

        apps = migrate_to(AFTER)
        self.assertLessEqual(NEW_TABLES, tables())
        self.assertIn("location_id", columns("project_task"))
        visits = apps.get_model("core", "Visit").objects
        again = visits.get(pk=self.with_pct)
        self.assertEqual((again.progress_pct, again.quantities), (Decimal("40"), []))
        self.assertIsNone(visits.get(pk=self.only_quantities).task_id)

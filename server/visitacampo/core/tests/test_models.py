"""Tests del modelo de datos núcleo (Paso 5, doc 08 §4.4).

El archivo tiene **dos mitades deliberadamente separadas**:

1. **Tests de introspección** (`SimpleTestCase`): no tocan la base de datos.
   Verifican por `Model._meta` y por el grafo de migraciones los criterios de
   aceptación que son estructurales — `org_id NOT NULL` en toda tabla de
   negocio (ADR-011), `Blob` sin ningún campo mutable (RD-08), campos de
   `visit` según doc 08 §4.4, y reversibilidad de toda operación de migración
   (patrón C-18). Corren **siempre**, también en CI.

2. **Tests de integración contra PostgreSQL** (`TestCase`): comprueban lo que
   solo la BD puede garantizar — que una `Visit` válida persiste, que un
   `lat`/`lon` fuera de rango lo rechaza el `CHECK` (ADR-005) y que un
   `VisitAttachment` cruzado entre organizaciones lo rechaza la FK compuesta
   de la migración `0002` (tarea 12 del brief). Estos **necesitan una
   PostgreSQL alcanzable**.

**Por qué el bloque de skip por defecto**: el proyecto no usa `pytest-django`
(ver `server/conftest.py`: CI instala `pytest` a secas) y, hasta la revisión
que introdujo `VISITACAMPO_REQUIRE_DB` (ver más abajo),
`.github/workflows/backend.yml` no levantaba ningún servicio de base de
datos para el job de lint-and-test. Sin guard, añadir este archivo habría
dejado el gate de CI del Paso 3 en rojo permanente. Con él, el
comportamiento por defecto (`VISITACAMPO_REQUIRE_DB` sin fijar, o fijado a
`0`) es:

- sin PostgreSQL alcanzable → la mitad 1 corre, la mitad 2 se **salta con un
  motivo explícito** (nunca "pasa" en falso);
- con PostgreSQL alcanzable (`docker compose up db` del Paso 2, o el servicio
  `postgres:16` de CI) → se crea una base de datos de test aparte
  (`test_<NAME>`), se le **aplican las migraciones reales** —que es de paso
  la verificación de `manage.py migrate` del brief— y la mitad 2 corre de
  verdad.

**`VISITACAMPO_REQUIRE_DB=1` — modo estricto (hallazgo "Important" de la
revisión del Paso 5)**: el skip silencioso de arriba tiene un defecto en CI —
convierte *cualquier* problema de conexión (contraseña incorrecta, host mal
configurado, Postgres caído) en el mismo verde que "todavía no hay Postgres
disponible", ambos indistinguibles desde el resultado de la suite. Con esta
variable en `1`, si al importar este módulo la BD no está alcanzable, el
archivo entero **falla en la colección** (`RuntimeError`, no `unittest.skip`)
en vez de reportar verde. `.github/workflows/backend.yml` levanta ahora un
servicio `postgres:16` para el job de lint-and-test y fija esta variable a
`1` en el paso `pytest server/` — así, un futuro skip de la mitad 2 en CI es
un fallo duro, no un verde falso. Sin la variable (o con `0`, el valor por
defecto), el desarrollo local sin PostgreSQL sigue funcionando exactamente
como antes: mitad 1 en verde, mitad 2 saltada con motivo.

Cada test de la mitad 2 se ejecuta dentro de una transacción que `TestCase`
revierte al terminar: no deja residuo.
"""

from __future__ import annotations

import os
import unittest
import uuid

from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.special import RunPython, RunSQL
from django.test import SimpleTestCase, TestCase
from django.test.utils import setup_test_environment, teardown_test_environment
from django.utils import timezone

from visitacampo.core.models import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    AppUser,
    AttachmentRole,
    Blob,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Membership,
    MimeType,
    Organization,
    Platform,
    Role,
    Visit,
    VisitAttachment,
)

# --- Detección de PostgreSQL ------------------------------------------------
# `OPTIONS.connect_timeout = 5` (settings/base.py) acota esta sonda: sin
# servidor, falla en segundos en vez de colgar el arranque de la suite.

# Hallazgo "Important" de la revisión del Paso 5: por defecto, cualquier
# problema de conexión (sin servidor, contraseña mal, host mal configurado)
# se traducía siempre en un skip silencioso — indistinguible de un verde
# real, en cualquier entorno, incluida CI. Esta variable lo hace
# opt-in-estricto: en CI (donde ahora sí hay un servicio `postgres:16`, ver
# `.github/workflows/backend.yml`) se fija a `1` y convierte ese mismo
# problema en un fallo duro de colección en vez de un skip.
_REQUIRE_DB_ENV = "VISITACAMPO_REQUIRE_DB"


def _probe_database() -> str | None:
    """Devuelve `None` si hay BD utilizable, o el motivo del skip si no."""
    try:
        connection.ensure_connection()
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de conexión sirve
        return f"PostgreSQL no alcanzable ({type(exc).__name__}: {exc})"
    return None


def _require_db() -> bool:
    """`VISITACAMPO_REQUIRE_DB=1`: ausencia de BD debe fallar, no saltarse."""
    return os.environ.get(_REQUIRE_DB_ENV) == "1"


_SKIP_REASON = _probe_database()

if _SKIP_REASON is not None and _require_db():
    # Fallo duro en tiempo de colección — no "un test falla", sino que la
    # suite entera se niega a arrancar. Intencional: con
    # VISITACAMPO_REQUIRE_DB=1 no existe un "verde parcial" aceptable; un
    # wrong-password o wrong-host debe ser tan ruidoso como "no hay Postgres".
    raise RuntimeError(
        f"{_REQUIRE_DB_ENV}=1 exige PostgreSQL alcanzable y no lo está: "
        f"{_SKIP_REASON}. Revisa el servicio `postgres:16` y las variables "
        "POSTGRES_* del job lint-and-test en .github/workflows/backend.yml "
        "(o, en local, exporta VISITACAMPO_REQUIRE_DB=0/sin fijar para "
        "volver al modo con skip)."
    )

_DB_STATE: dict[str, object] = {}


def setUpModule() -> None:  # noqa: N802 — nombre fijado por unittest/pytest
    """Crea y migra la BD de test una sola vez, si hay PostgreSQL."""
    if _SKIP_REASON is not None:
        return
    setup_test_environment()
    _DB_STATE["old_name"] = connection.creation.create_test_db(
        verbosity=0,
        autoclobber=True,
    )


def tearDownModule() -> None:  # noqa: N802 — nombre fijado por unittest/pytest
    if _SKIP_REASON is not None:
        return
    connection.creation.destroy_test_db(_DB_STATE["old_name"], verbosity=0)
    teardown_test_environment()


# --- 1. Introspección: criterios de aceptación estructurales ----------------


class BlobImmutabilityTests(SimpleTestCase):
    """RD-08 "satisfecha por construcción": `blob` no tiene campo mutable."""

    # Los cinco campos que doc 08 §4.4 fija para `blob`, y ninguno más.
    EXPECTED_FIELDS = {"sha256", "size", "mime", "bucket_key", "created_at"}

    # Nombres que reintroducirían el hallazgo H-08 (lost update sobre un
    # puntero mutable, `File.latest_version` de la referencia) o cualquier
    # variante de "estado que cambia después del INSERT".
    FORBIDDEN_FIELDS = {
        "latest_version",
        "current_version",
        "version",
        "status",
        "state",
        "updated_at",
        "modified_at",
        "last_modified",
        "ref_count",
        "reference_count",
        "deleted_at",
    }

    def test_has_exactly_the_documented_fields(self) -> None:
        names = {f.name for f in Blob._meta.fields}
        self.assertEqual(names, self.EXPECTED_FIELDS)

    def test_has_no_mutable_pointer_field(self) -> None:
        names = {f.name for f in Blob._meta.fields}
        self.assertEqual(
            names & self.FORBIDDEN_FIELDS,
            set(),
            "RD-08: `blob` debe ser inmutable; ningún campo tipo "
            "`latest_version`/`status`/`updated_at` puede existir.",
        )

    def test_every_field_is_non_editable(self) -> None:
        """Nada en `blob` se edita tras el INSERT (ni siquiera vía formulario)."""
        mutables = [f.name for f in Blob._meta.fields if f.editable]
        self.assertEqual(mutables, [])

    def test_no_auto_now_field(self) -> None:
        """`auto_now` reescribiría la fila en cada `save()` — prohibido aquí."""
        auto_now = [f.name for f in Blob._meta.fields if getattr(f, "auto_now", False)]
        self.assertEqual(auto_now, [])

    def test_primary_key_is_the_content_hash(self) -> None:
        self.assertEqual(Blob._meta.pk.name, "sha256")


class OrgIsolationSchemaTests(SimpleTestCase):
    """ADR-011: `org_id NOT NULL` en toda tabla de negocio, desde el día 1."""

    # Excepciones documentadas, ambas justificadas en el código:
    #   - `Organization` es la raíz del scoping (no se scopea a sí misma).
    #   - `Blob` es contenido direccionado por hash, deduplicado a propósito
    #     entre organizaciones (docstring de `models/visit.py`).
    #   - `RateLimitBucket` cuenta peticiones de login y refresh **antes** de
    #     saber a qué organización pertenece nadie; solo guarda un HMAC de la
    #     identidad, nunca datos de negocio (docstring de `models/ops.py`).
    EXEMPT = {"Organization", "Blob", "RateLimitBucket"}

    def _business_models(self) -> list[type[models.Model]]:
        from django.apps import apps

        return [
            model
            for model in apps.get_app_config("core").get_models()
            if model.__name__ not in self.EXEMPT
        ]

    def test_every_business_model_has_a_non_null_org(self) -> None:
        for model in self._business_models():
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("org")
                self.assertFalse(field.null, f"{model.__name__}.org debe ser NOT NULL")
                self.assertEqual(field.column, "org_id")

    def test_exempt_models_are_only_the_documented_ones(self) -> None:
        """Que nadie añada una tabla sin `org_id` sin tocar también este test."""
        from django.apps import apps

        without_org = {
            model.__name__
            for model in apps.get_app_config("core").get_models()
            if "org" not in {f.name for f in model._meta.fields}
        }
        self.assertEqual(without_org, self.EXEMPT)


class Doc08TableInventoryTests(SimpleTestCase):
    """Todas las tablas de doc 08 §4.4 existen con el nombre esperado."""

    EXPECTED_TABLES = {
        "organization",
        "app_user",
        "membership",
        "device",
        "form_template",
        "form_template_version",
        "assignment",
        "visit",
        "blob",
        "visit_attachment",
        "sync_op_log",
        "job",
        "audit_event",
    }

    def test_all_documented_tables_exist(self) -> None:
        from django.apps import apps

        tables = {model._meta.db_table for model in apps.get_app_config("core").get_models()}
        self.assertEqual(tables & self.EXPECTED_TABLES, self.EXPECTED_TABLES)


class VisitSchemaTests(SimpleTestCase):
    """`visit` con **exactamente** los campos de doc 08 §4.4 (tarea 4 del brief)."""

    def test_has_every_documented_column(self) -> None:
        columns = {f.column for f in Visit._meta.fields}
        expected = {
            "id",
            "org_id",
            "version",
            "change_seq",
            "deleted_at",
            "assignment_id",
            "template_version_id",
            "answers",
            "lat",
            "lon",
            "accuracy_m",
            "altitude_m",
            "captured_at",
            "device_id",
            "created_by_id",
        }
        self.assertEqual(columns, expected)

    def test_id_has_no_server_side_default(self) -> None:
        """ADR-003: el UUID lo genera el cliente; el servidor nunca lo inventa."""
        from django.db.models.fields import NOT_PROVIDED

        self.assertIs(Visit._meta.pk.default, NOT_PROVIDED)

    def test_declares_lat_lon_check_constraints(self) -> None:
        names = {c.name for c in Visit._meta.constraints}
        self.assertIn("visit_lat_range", names)
        self.assertIn("visit_lon_range", names)

    def test_declares_bounding_box_index(self) -> None:
        """ADR-005: índice B-tree compuesto `(org_id, lat, lon)`."""
        by_name = {i.name: i.fields for i in Visit._meta.indexes}
        self.assertEqual(by_name.get("visit_org_lat_lon_idx"), ["org", "lat", "lon"])

    def test_declares_composite_unique_for_org_isolation_fk(self) -> None:
        """Destino obligatorio de la FK compuesta de la migración 0002."""
        names = {c.name for c in Visit._meta.constraints}
        self.assertIn("visit_id_org_uq", names)


class VisitFloatFinitenessTests(SimpleTestCase):
    """Ningún `float` de `visit` puede almacenar `NaN` ni `±Infinity` (C-2).

    Sin base de datos: se comprueba el **texto** del `CHECK` que Django genera,
    igual que `sync/tests/test_sql.py` hace con las sentencias del push. Es lo
    máximo verificable en este entorno (no hay PostgreSQL) y no es poco: la
    condición se compila de verdad, así que un `finite_q` mal escrito falla
    aquí y no en la primera migración de CI.

    El equivalente que sí ejercita a PostgreSQL —`IntegrityError` al insertar—
    está en `VisitPersistenceTests`, y como el resto de esa clase requiere BD.
    """

    #: `NaN` no aparece en el SQL a propósito: en PostgreSQL se ordena como
    #: **mayor que cualquier valor**, así que `NaN < 'Infinity'` ya es `false`
    #: y la cota superior lo rechaza sin nombrarlo.
    FINITE_BOUNDS = ("> '-Infinity'::float8", "< 'Infinity'::float8")

    def _check_sql(self, constraint_name: str) -> str:
        constraint = next(c for c in Visit._meta.constraints if c.name == constraint_name)
        # `collect_sql=True` + `atomic=False`: genera DDL sin abrir conexión.
        with connection.schema_editor(collect_sql=True, atomic=False) as editor:
            return str(constraint.create_sql(Visit, editor))

    def test_altitude_and_accuracy_declare_a_finiteness_check(self) -> None:
        for name, column in (
            ("visit_altitude_finite", "altitude_m"),
            ("visit_accuracy_finite_non_negative", "accuracy_m"),
        ):
            with self.subTest(constraint=name):
                sql = self._check_sql(name)
                self.assertIn(f'"{column}" IS NULL', sql)
                for bound in self.FINITE_BOUNDS:
                    self.assertIn(f'"{column}" {bound}', sql)

    def test_accuracy_keeps_its_non_negative_guard(self) -> None:
        """La finitud se **suma** al `>= 0` de la migración 0001, no lo sustituye."""
        self.assertIn('"accuracy_m" >= 0', self._check_sql("visit_accuracy_finite_non_negative"))

    def test_latitude_and_longitude_are_covered_by_their_range(self) -> None:
        """Por qué `lat`/`lon` no llevan `finite_q` y no es un olvido.

        Sus dos cotas ya rechazan los tres valores no finitos: `NaN <= 90` es
        `false` (NaN se ordena por encima de todo), `Infinity <= 90` también, y
        `-Infinity >= -90` igual. Añadirles `finite_q` sería redundante.
        """
        for name, column in (("visit_lat_range", "lat"), ("visit_lon_range", "lon")):
            with self.subTest(constraint=name):
                sql = self._check_sql(name)
                self.assertIn(f'"{column}" >=', sql)
                self.assertIn(f'"{column}" <=', sql)

    def test_every_nullable_float_column_of_visit_has_a_finiteness_guard(self) -> None:
        """La red que impide que un `float` futuro se quede sin guarda.

        Un campo `FloatField` nuevo sin `CHECK` que lo acote reproduce
        exactamente el defecto C-2, y se notaría meses después en forma de un
        `jsonb` imposible de escribir. Este test obliga a pasar por aquí.
        """
        float_columns = {
            field.column for field in Visit._meta.fields if isinstance(field, models.FloatField)
        }
        self.assertEqual(float_columns, {"lat", "lon", "accuracy_m", "altitude_m"})

        guarded = " ".join(self._check_sql(c.name) for c in Visit._meta.constraints)
        for column in float_columns:
            with self.subTest(column=column):
                self.assertIn(f'"{column}"', guarded)


class MigrationReversibilityTests(SimpleTestCase):
    """Criterio de aceptación: la migración es reversible (patrón C-18)."""

    def _core_migrations(self) -> list[object]:
        # `MigrationLoader(None)` lee los archivos del disco sin consultar
        # `django_migrations` — por tanto sin necesitar conexión.
        loader = MigrationLoader(None, ignore_no_migrations=True)
        return [
            migration
            for (app_label, _name), migration in sorted(loader.disk_migrations.items())
            if app_label == "core"
        ]

    def test_there_are_core_migrations_to_check(self) -> None:
        self.assertGreaterEqual(len(self._core_migrations()), 2)

    def test_every_raw_sql_operation_has_a_reverse(self) -> None:
        for migration in self._core_migrations():
            for operation in migration.operations:  # type: ignore[attr-defined]
                if isinstance(operation, (RunSQL, RunPython)):
                    with self.subTest(migration=migration.name, op=type(operation).__name__):  # type: ignore[attr-defined]
                        self.assertTrue(
                            operation.reversible,
                            "Todo RunSQL/RunPython necesita su contraparte reversa "
                            "(doc 08 §7.1, patrón C-18).",
                        )

    def test_every_operation_is_reducible_to_a_reverse(self) -> None:
        for migration in self._core_migrations():
            for operation in migration.operations:  # type: ignore[attr-defined]
                with self.subTest(migration=migration.name, op=type(operation).__name__):  # type: ignore[attr-defined]
                    self.assertTrue(operation.reversible)


# --- 2. Integración contra PostgreSQL ---------------------------------------


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class VisitPersistenceTests(TestCase):
    """Alta de `Organization`/`AppUser`/`Membership`/`Device` + `Visit` válida."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org = Organization.objects.create(name="Org de pruebas")
        cls.user = AppUser.objects.create_user(
            username="agente",
            email="agente@example.test",
            password="contrasena-de-prueba",
            org=cls.org,
        )
        cls.membership = Membership.objects.create(
            org=cls.org,
            user=cls.user,
            role=Role.FIELD_AGENT,
        )
        cls.device = Device.objects.create(
            id=uuid.uuid4(),
            org=cls.org,
            owner=cls.user,
            platform=Platform.ANDROID,
            app_version="1.0.0",
        )
        cls.template = FormTemplate.objects.create(org=cls.org, name="Visita estándar")
        cls.template_version = FormTemplateVersion.objects.create(
            org=cls.org,
            template=cls.template,
            version_number=1,
            change_seq=1,
        )

    def _visit_kwargs(self, **overrides: object) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "id": uuid.uuid4(),
            "org": self.org,
            "change_seq": 10,
            "template_version": self.template_version,
            "device": self.device,
            "created_by": self.user,
            "captured_at": timezone.now(),
            "answers": {"pregunta_1": "sí"},
            "lat": 4.60971,
            "lon": -74.08175,
            "accuracy_m": 8.5,
        }
        kwargs.update(overrides)
        return kwargs

    def test_supporting_rows_persist(self) -> None:
        self.assertEqual(Organization.objects.count(), 1)
        self.assertEqual(AppUser.objects.filter(org=self.org).count(), 1)
        self.assertEqual(Membership.objects.get(pk=self.membership.pk).role, Role.FIELD_AGENT)
        self.assertEqual(Device.objects.get(pk=self.device.pk).org_id, self.org.pk)

    def test_valid_visit_persists(self) -> None:
        visit = Visit.objects.create(**self._visit_kwargs())
        stored = Visit.objects.get(pk=visit.pk)
        self.assertEqual(stored.org_id, self.org.pk)
        self.assertEqual(stored.version, 1)
        self.assertEqual(stored.change_seq, 10)
        self.assertIsNone(stored.deleted_at)
        self.assertEqual(stored.answers, {"pregunta_1": "sí"})

    def test_visit_without_location_persists(self) -> None:
        """La ubicación es opcional: el `CHECK` es `IS NULL OR (rango)`."""
        visit = Visit.objects.create(**self._visit_kwargs(lat=None, lon=None, accuracy_m=None))
        self.assertIsNone(Visit.objects.get(pk=visit.pk).lat)

    def test_boundary_coordinates_are_accepted(self) -> None:
        visit = Visit.objects.create(**self._visit_kwargs(lat=LAT_MAX, lon=LON_MIN))
        self.assertEqual(Visit.objects.get(pk=visit.pk).lat, LAT_MAX)

    def test_latitude_out_of_range_is_rejected_by_check(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.create(**self._visit_kwargs(lat=LAT_MAX + 1))

    def test_negative_latitude_out_of_range_is_rejected_by_check(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.create(**self._visit_kwargs(lat=LAT_MIN - 1))

    def test_longitude_out_of_range_is_rejected_by_check(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.create(**self._visit_kwargs(lon=LON_MAX + 1))

    def test_negative_accuracy_is_rejected_by_check(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Visit.objects.create(**self._visit_kwargs(accuracy_m=-1))

    def test_non_finite_floats_are_rejected_by_check(self) -> None:
        """C-2: `NaN`/`±Infinity` son legales en `double precision`; aquí no.

        Este es el test que **solo** PostgreSQL puede decidir, porque lo que se
        afirma es una propiedad de PostgreSQL: que `NaN` se ordene por encima
        de `Infinity` es justo lo que hace que la cota superior lo rechace.
        """
        for field in ("lat", "lon", "accuracy_m", "altitude_m"):
            for label, value in (
                ("nan", float("nan")),
                ("inf", float("inf")),
                ("-inf", float("-inf")),
            ):
                with self.subTest(field=field, value=label):
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        Visit.objects.create(**self._visit_kwargs(**{field: value}))

    def test_a_finite_altitude_still_persists(self) -> None:
        """Incluidas las que un rango de altitud arbitrario habría descartado."""
        for altitude in (-430.0, 0.0, 2625.0, 11_000.0):
            with self.subTest(altitude=altitude):
                visit = Visit.objects.create(**self._visit_kwargs(altitude_m=altitude))
                self.assertEqual(Visit.objects.get(pk=visit.pk).altitude_m, altitude)


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class CrossOrgAttachmentTests(TestCase):
    """Tarea 12 del brief: la FK compuesta impide cruzar organizaciones."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org_a = Organization.objects.create(name="Org A")
        cls.org_b = Organization.objects.create(name="Org B")
        cls.user_a = AppUser.objects.create_user(
            username="agente-a",
            email="a@example.test",
            password="contrasena-de-prueba",
            org=cls.org_a,
        )
        cls.device_a = Device.objects.create(
            id=uuid.uuid4(),
            org=cls.org_a,
            owner=cls.user_a,
            platform=Platform.ANDROID,
            app_version="1.0.0",
        )
        template_a = FormTemplate.objects.create(org=cls.org_a, name="Plantilla A")
        cls.template_version_a = FormTemplateVersion.objects.create(
            org=cls.org_a,
            template=template_a,
            version_number=1,
            change_seq=1,
        )
        cls.visit_a = Visit.objects.create(
            id=uuid.uuid4(),
            org=cls.org_a,
            change_seq=2,
            template_version=cls.template_version_a,
            device=cls.device_a,
            created_by=cls.user_a,
            captured_at=timezone.now(),
        )
        cls.blob = Blob.objects.create(
            sha256="a" * 64,
            size=1024,
            mime=MimeType.IMAGE_JPEG,
            bucket_key="blobs/aa/aa/" + "a" * 64,
        )

    def _attachment_kwargs(self, **overrides: object) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "id": uuid.uuid4(),
            "org": self.org_a,
            "change_seq": 3,
            "visit": self.visit_a,
            "blob": self.blob,
            "role": AttachmentRole.FOTO,
            "captured_at": timezone.now(),
            "order": 0,
        }
        kwargs.update(overrides)
        return kwargs

    @staticmethod
    def _force_deferred_constraint_check() -> None:
        """La FK compuesta es `DEFERRABLE INITIALLY DEFERRED`.

        Igual que el resto de FKs que crea Django en PostgreSQL, se comprueba
        en el `COMMIT`. Dentro de un test, la transacción nunca se confirma, así
        que hay que pedir la comprobación explícitamente.
        """
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    def test_same_org_attachment_is_accepted(self) -> None:
        attachment = VisitAttachment.objects.create(**self._attachment_kwargs())
        self._force_deferred_constraint_check()
        self.assertEqual(VisitAttachment.objects.get(pk=attachment.pk).org_id, self.org_a.pk)

    def test_cross_org_attachment_is_rejected_by_composite_fk(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            VisitAttachment.objects.create(**self._attachment_kwargs(org=self.org_b))
            self._force_deferred_constraint_check()

    def test_the_composite_fk_actually_exists_in_the_database(self) -> None:
        """Sin esta constraint el test anterior pasaría por la razón equivocada."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'visit_attachment'::regclass
                  AND contype = 'f'
                  AND conname = 'visit_attachment_visit_org_fk'
                """
            )
            self.assertIsNotNone(cursor.fetchone())

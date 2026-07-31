"""Tests de `scoped_qs` (Paso 6, tarea 4 — ADR-011/ADR-013, RD-11).

`scoped_qs(ctx, Model)` es la **única** puerta de lectura a una tabla de
negocio. Lo que se verifica aquí:

- que aplica `org_id = ctx.org_id` de verdad (SQL y filas, no intención);
- que **rechaza** los modelos que no son tenant-scoped, en vez de devolver un
  queryset sin filtrar que parecería seguro (deny-by-default);
- que el filtro sobrevive al encadenar (`.filter(...)`, `.exclude(...)`), que
  es como lo usarán los servicios de los Pasos 8-10.

La mitad sin BD compila el SQL —Django lo hace sin conectarse— y la mitad con
PostgreSQL comprueba lo único que el SQL por sí solo no demuestra: que un
usuario de la organización A no ve ni una fila de la B.
"""

from __future__ import annotations

import unittest
import uuid

from django.apps import apps
from django.db.models import QuerySet
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    AppUser,
    Blob,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Membership,
    Organization,
    OrgScopedModel,
    Platform,
    Role,
    Visit,
)
from visitacampo.core.scoping import NotOrgScopedError, scoped_qs

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

setUpModule = setup_test_database
tearDownModule = teardown_test_database


def _ctx(org_id: uuid.UUID) -> RequestContext:
    return RequestContext(user_id=uuid.uuid4(), org_id=org_id, roles=frozenset({Role.ADMIN}))


def _bound(params: tuple[object, ...]) -> list[str]:
    """Parámetros del SQL, normalizados.

    Cómo se serializa un UUID lo decide el backend (psycopg lo pasa tal cual;
    otros, como hex sin guiones), y lo que se está probando aquí es *qué* valor
    se ata al `WHERE`, no cómo lo escribe el driver.
    """
    return [str(p).replace("-", "") for p in params]


class ScopedQuerysetTests(SimpleTestCase):
    """El filtro por organización está en el SQL, no en la buena intención."""

    def setUp(self) -> None:
        self.org_id = uuid.uuid4()
        self.ctx = _ctx(self.org_id)

    def test_returns_a_queryset_of_the_requested_model(self) -> None:
        qs = scoped_qs(self.ctx, Visit)
        self.assertIsInstance(qs, QuerySet)
        self.assertIs(qs.model, Visit)

    def test_filters_by_the_context_organization(self) -> None:
        sql, params = scoped_qs(self.ctx, Visit).query.sql_with_params()
        self.assertIn('"visit"."org_id" = %s', sql)
        self.assertEqual(_bound(params), [self.org_id.hex])

    def test_every_org_scoped_model_of_the_app_can_be_scoped(self) -> None:
        """Un modelo tenant nuevo queda cubierto sin tocar nada más."""
        scopables = [
            model
            for model in apps.get_app_config("core").get_models()
            if issubclass(model, OrgScopedModel)
        ]
        self.assertGreaterEqual(len(scopables), 10)
        for model in scopables:
            with self.subTest(model=model.__name__):
                sql, params = scoped_qs(self.ctx, model).query.sql_with_params()
                self.assertIn(f'"{model._meta.db_table}"."org_id" = %s', sql)
                self.assertEqual(_bound(params), [self.org_id.hex])

    def test_chaining_preserves_the_organization_filter(self) -> None:
        qs = scoped_qs(self.ctx, Visit).filter(deleted_at__isnull=True).exclude(lat=None)
        sql, params = qs.query.sql_with_params()
        self.assertIn('"visit"."org_id" = %s', sql)
        self.assertEqual(_bound(params), [self.org_id.hex])

    def test_the_organization_is_the_only_filter_applied(self) -> None:
        """El pull entrega tombstones (ADR-003): filtrarlos aquí los perdería.

        Se mira la cláusula `WHERE`, no el SQL entero: `deleted_at` aparece
        siempre en la lista de columnas seleccionadas, que no filtra nada.
        """
        sql, params = scoped_qs(self.ctx, Visit).query.sql_with_params()
        where = sql.split(" WHERE ", maxsplit=1)[1]
        self.assertEqual(where, '"visit"."org_id" = %s')
        self.assertEqual(_bound(params), [self.org_id.hex])


class UnscopableModelTests(SimpleTestCase):
    """Deny-by-default: si no se puede scopear, se falla; no se devuelve todo."""

    def setUp(self) -> None:
        self.ctx = _ctx(uuid.uuid4())

    def test_rejects_blob(self) -> None:
        """`blob` es contenido deduplicado entre organizaciones (RD-08)."""
        with self.assertRaises(NotOrgScopedError):
            scoped_qs(self.ctx, Blob)

    def test_rejects_organization(self) -> None:
        """`organization` es la raíz del scoping, no una tabla scopeada."""
        with self.assertRaises(NotOrgScopedError):
            scoped_qs(self.ctx, Organization)

    def test_rejects_a_model_of_another_app(self) -> None:
        from django.contrib.contenttypes.models import ContentType

        with self.assertRaises(NotOrgScopedError):
            scoped_qs(self.ctx, ContentType)

    def test_rejects_something_that_is_not_a_model(self) -> None:
        with self.assertRaises(TypeError):
            scoped_qs(self.ctx, dict)

    def test_rejects_a_model_instance_instead_of_the_class(self) -> None:
        with self.assertRaises(TypeError):
            scoped_qs(self.ctx, Visit(id=uuid.uuid4()))


@unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
class CrossOrgVisibilityTests(TestCase):
    """RD-11: la organización ajena no existe para quien no es de ella."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org_a = Organization.objects.create(name="Org A")
        cls.org_b = Organization.objects.create(name="Org B")
        cls.visit_a = cls._make_visit(cls.org_a, "a")
        cls.visit_b = cls._make_visit(cls.org_b, "b")

    @classmethod
    def _make_visit(cls, org: Organization, suffix: str) -> Visit:
        user = AppUser.objects.create_user(
            username=f"agente-{suffix}",
            email=f"{suffix}@example.test",
            password="contrasena-de-prueba",
            org=org,
        )
        Membership.objects.create(org=org, user=user, role=Role.FIELD_AGENT)
        device = Device.objects.create(
            id=uuid.uuid4(),
            org=org,
            owner=user,
            platform=Platform.ANDROID,
            app_version="1.0.0",
        )
        template = FormTemplate.objects.create(org=org, name=f"Plantilla {suffix}")
        template_version = FormTemplateVersion.objects.create(
            org=org,
            template=template,
            version_number=1,
            change_seq=1,
        )
        return Visit.objects.create(
            id=uuid.uuid4(),
            org=org,
            change_seq=2,
            template_version=template_version,
            device=device,
            created_by=user,
            captured_at=timezone.now(),
        )

    def test_only_the_own_organization_rows_are_visible(self) -> None:
        visible = list(scoped_qs(_ctx(self.org_a.pk), Visit))
        self.assertEqual([v.pk for v in visible], [self.visit_a.pk])

    def test_a_foreign_row_is_invisible_even_by_primary_key(self) -> None:
        """Base del `404` (y no `403`) de RD-11: la fila no aparece siquiera."""
        with self.assertRaises(Visit.DoesNotExist):
            scoped_qs(_ctx(self.org_a.pk), Visit).get(pk=self.visit_b.pk)

    def test_unscoped_access_would_have_seen_both(self) -> None:
        """Sin esta comprobación, los tests de arriba pasarían por vacuidad."""
        self.assertEqual(Visit.objects.count(), 2)

    def test_scoping_also_applies_to_memberships(self) -> None:
        """La tabla del hallazgo H-16: enumerar miembros ajenos no es posible."""
        self.assertEqual(scoped_qs(_ctx(self.org_a.pk), Membership).count(), 1)

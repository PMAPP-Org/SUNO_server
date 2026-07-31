"""Tests del `RequestContext` (Paso 6, ADR-013).

Dos mitades, con el mismo criterio que `test_models.py`:

1. **Sin base de datos** (`SimpleTestCase`): el `RequestContext` es un objeto
   inmutable de Python y su comportamiento —inmutabilidad, `has_any_role`
   deny-by-default, memoización de la entidad de contexto (RD-16)— se prueba
   entero en memoria.

2. **Con PostgreSQL** (`TestCase`): lo que solo la BD puede demostrar — que
   resolver el contexto cuesta **exactamente una query** (`assertNumQueries(1)`,
   RD-07 / hallazgo H-07) y que autorizar N recursos después cuesta **cero**.

El arranque de la BD de test vive en `dbsupport.py` (mismo patrón que
`test_models.py`: el proyecto no usa `pytest-django` y CI fija
`VISITACAMPO_REQUIRE_DB=1` para que la ausencia de PostgreSQL sea un fallo
duro y no un verde falso).
"""

from __future__ import annotations

import dataclasses
import unittest
import uuid

from django.test import RequestFactory, SimpleTestCase, TestCase

from visitacampo.core.context import (
    Principal,
    RequestContext,
    RequestContextAuth,
    RequestContextMissing,
    get_request_context,
    resolve_request_context,
)
from visitacampo.core.models import AppUser, Membership, Organization, Role, Visit
from visitacampo.core.policies import can_read_visit

from .dbsupport import SKIP_REASON, setup_test_database, teardown_test_database

_SKIP_REASON = SKIP_REASON
setUpModule = setup_test_database
tearDownModule = teardown_test_database


def _ctx(*roles: object, org_id: uuid.UUID | None = None) -> RequestContext:
    """Contexto sintético, sin BD: es un objeto de datos puro."""
    return RequestContext(
        user_id=uuid.uuid4(),
        org_id=org_id or uuid.uuid4(),
        roles=frozenset(roles),
    )


# --- 1. Comportamiento del objeto (sin BD) ----------------------------------


class RequestContextShapeTests(SimpleTestCase):
    """ADR-013: el contexto es **inmutable** y trae los campos documentados."""

    def test_has_the_documented_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(RequestContext)}
        self.assertEqual(names, {"user_id", "org_id", "roles", "device_id", "entities"})

    def test_is_frozen(self) -> None:
        ctx = _ctx(Role.ADMIN)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.org_id = uuid.uuid4()  # type: ignore[misc]

    def test_roles_are_an_immutable_collection(self) -> None:
        ctx = _ctx(Role.ADMIN)
        self.assertIsInstance(ctx.roles, frozenset)
        with self.assertRaises(AttributeError):
            ctx.roles.add(Role.OWNER)  # type: ignore[attr-defined]

    def test_device_id_is_optional(self) -> None:
        self.assertIsNone(_ctx(Role.VIEWER).device_id)


class RoleQueryTests(SimpleTestCase):
    """`has_any_role` es la primitiva de toda política: deny-by-default."""

    def test_true_when_one_role_matches(self) -> None:
        self.assertTrue(_ctx(Role.FIELD_AGENT).has_any_role({Role.ADMIN, Role.FIELD_AGENT}))

    def test_false_when_no_role_matches(self) -> None:
        self.assertFalse(_ctx(Role.VIEWER).has_any_role({Role.ADMIN}))

    def test_false_for_a_user_without_memberships(self) -> None:
        """Caso borde explícito: cero membresías ⇒ ningún permiso."""
        ctx = _ctx()
        self.assertFalse(ctx.is_member)
        self.assertFalse(ctx.has_any_role(set(Role.values)))

    def test_false_for_an_empty_required_role_set(self) -> None:
        """Pedir "ningún rol en concreto" no puede conceder acceso."""
        self.assertFalse(_ctx(Role.OWNER).has_any_role(set()))


class ContextEntityCacheTests(SimpleTestCase):
    """RD-16 / H-20: la entidad de contexto se resuelve **una vez** por request."""

    def test_loader_runs_once_for_repeated_lookups(self) -> None:
        ctx = _ctx(Role.ADMIN)
        calls: list[int] = []

        def loader() -> str:
            calls.append(1)
            return "assignment-1"

        first = ctx.resolve_entity("assignment", loader)
        second = ctx.resolve_entity("assignment", loader)

        self.assertEqual(first, "assignment-1")
        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_different_keys_are_cached_independently(self) -> None:
        ctx = _ctx(Role.ADMIN)
        self.assertEqual(ctx.resolve_entity("a", lambda: 1), 1)
        self.assertEqual(ctx.resolve_entity("b", lambda: 2), 2)
        self.assertEqual(ctx.resolve_entity("a", lambda: 99), 1)

    def test_a_cached_none_is_not_re_resolved(self) -> None:
        """"No existe" también es una respuesta que no debe re-consultarse."""
        ctx = _ctx(Role.ADMIN)
        calls: list[int] = []

        def loader() -> None:
            calls.append(1)
            return None

        ctx.resolve_entity("missing", loader)
        ctx.resolve_entity("missing", loader)
        self.assertEqual(len(calls), 1)

    def test_cache_does_not_affect_equality(self) -> None:
        """La identidad del contexto son sus ids y roles, no lo que cacheó."""
        user_id, org_id = uuid.uuid4(), uuid.uuid4()
        a = RequestContext(user_id=user_id, org_id=org_id, roles=frozenset({Role.ADMIN}))
        b = RequestContext(user_id=user_id, org_id=org_id, roles=frozenset({Role.ADMIN}))
        a.resolve_entity("assignment", lambda: object())
        self.assertEqual(a, b)


class RequestAttachmentTests(SimpleTestCase):
    """`get_request_context` nunca devuelve `None` (deny-by-default)."""

    def test_raises_when_no_context_was_attached(self) -> None:
        request = RequestFactory().get("/v1/sync/pull")
        with self.assertRaises(RequestContextMissing):
            get_request_context(request)

    def test_returns_the_attached_context(self) -> None:
        request = RequestFactory().get("/v1/sync/pull")
        ctx = _ctx(Role.ADMIN)
        request.ctx = ctx
        self.assertIs(get_request_context(request), ctx)

    def test_rejects_an_impostor_object(self) -> None:
        request = RequestFactory().get("/v1/sync/pull")
        request.ctx = {"org_id": uuid.uuid4(), "roles": ["OWNER"]}
        with self.assertRaises(RequestContextMissing):
            get_request_context(request)


# --- 2. Presupuesto de queries (necesita PostgreSQL) ------------------------


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class ContextResolutionTests(TestCase):
    """H-07/RD-07: resolver roles cuesta **una** query, siempre."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org = Organization.objects.create(name="Org A")
        cls.other_org = Organization.objects.create(name="Org B")
        cls.user = AppUser.objects.create_user(
            username="supervisora",
            email="supervisora@example.test",
            password="contrasena-de-prueba",
            org=cls.org,
        )
        Membership.objects.create(org=cls.org, user=cls.user, role=Role.SUPERVISOR)
        # Membresía en otra organización: la resolución no debe mezclarlas.
        Membership.objects.create(org=cls.other_org, user=cls.user, role=Role.OWNER)
        cls.outsider = AppUser.objects.create_user(
            username="sin-membresia",
            email="sin@example.test",
            password="contrasena-de-prueba",
            org=cls.org,
        )
        cls.device_id = uuid.uuid4()

    def _principal(self, user: AppUser, org: Organization) -> Principal:
        return Principal(user_id=user.pk, org_id=org.pk, device_id=self.device_id)

    def test_resolution_costs_exactly_one_query(self) -> None:
        with self.assertNumQueries(1):
            ctx = resolve_request_context(self._principal(self.user, self.org))
        # El presupuesto solo es significativo si la query hizo su trabajo:
        # el rol correcto, el de esta organización y no el de la otra.
        self.assertEqual(ctx.roles, frozenset({Role.SUPERVISOR}))
        self.assertEqual(ctx.org_id, self.org.pk)
        self.assertEqual(ctx.user_id, self.user.pk)
        self.assertEqual(ctx.device_id, self.device_id)

    def test_resolution_is_scoped_to_the_principal_organization(self) -> None:
        ctx = resolve_request_context(self._principal(self.user, self.other_org))
        self.assertEqual(ctx.roles, frozenset({Role.OWNER}))

    def test_user_without_membership_resolves_to_no_roles(self) -> None:
        with self.assertNumQueries(1):
            ctx = resolve_request_context(self._principal(self.outsider, self.org))
        self.assertEqual(ctx.roles, frozenset())
        self.assertFalse(ctx.is_member)

    def test_the_context_entity_is_fetched_once_per_request(self) -> None:
        """RD-16/H-20 con queries reales, no con un contador de llamadas."""
        ctx = resolve_request_context(self._principal(self.user, self.org))

        def load_org() -> Organization:
            return Organization.objects.get(pk=self.org.pk)

        with self.assertNumQueries(1):
            first = ctx.resolve_entity("org", load_org)
            for _ in range(4):
                self.assertIs(ctx.resolve_entity("org", load_org), first)

        self.assertTrue(ctx.entities.is_cached("org"))

    def test_reading_roles_after_resolution_costs_nothing(self) -> None:
        ctx = resolve_request_context(self._principal(self.user, self.org))
        with self.assertNumQueries(0):
            self.assertTrue(ctx.has_any_role({Role.SUPERVISOR}))
            self.assertTrue(ctx.is_member)


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class QueryBudgetTests(TestCase):
    """Tarea 6 del brief: el coste de autorizar N recursos es constante.

    Caso sintético (el real llega en el Paso 8, con el loop de push): 200
    recursos —el tamaño máximo de lote del contrato— autorizados en memoria.
    Es exactamente el bucle que en QFieldCloud producía 200 queries idénticas.
    """

    BATCH_SIZE = 200

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org = Organization.objects.create(name="Org de presupuesto")
        cls.user = AppUser.objects.create_user(
            username="agente-lote",
            email="lote@example.test",
            password="contrasena-de-prueba",
            org=cls.org,
        )
        Membership.objects.create(org=cls.org, user=cls.user, role=Role.FIELD_AGENT)

    def test_authorizing_a_batch_costs_one_query_in_total(self) -> None:
        principal = Principal(user_id=self.user.pk, org_id=self.org.pk, device_id=uuid.uuid4())
        # Recursos en memoria (no persistidos): la política solo mira `org_id`,
        # así que un `Visit` sin guardar es un recurso perfectamente real para
        # este test — y si alguna política tocara una FK (`visit.org.id`), el
        # presupuesto de 0 queries lo delataría al instante.
        visits = [Visit(id=uuid.uuid4(), org_id=self.org.pk) for _ in range(self.BATCH_SIZE)]

        with self.assertNumQueries(1):
            ctx = resolve_request_context(principal)
            for visit in visits:
                self.assertTrue(can_read_visit(ctx, visit))

    def test_the_budget_does_not_depend_on_the_batch_size(self) -> None:
        principal = Principal(user_id=self.user.pk, org_id=self.org.pk)
        ctx = resolve_request_context(principal)
        for size in (1, self.BATCH_SIZE, self.BATCH_SIZE * 2):
            visits = [Visit(id=uuid.uuid4(), org_id=self.org.pk) for _ in range(size)]
            with self.subTest(size=size), self.assertNumQueries(0):
                self.assertTrue(all(can_read_visit(ctx, visit) for visit in visits))


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class RequestContextAuthTests(TestCase):
    """La dependencia de Django Ninja construye el ctx **antes** de la vista."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.org = Organization.objects.create(name="Org de auth")
        cls.user = AppUser.objects.create_user(
            username="agente-auth",
            email="auth@example.test",
            password="contrasena-de-prueba",
            org=cls.org,
        )
        Membership.objects.create(org=cls.org, user=cls.user, role=Role.ADMIN)

    def _auth(self, principal: Principal | None) -> RequestContextAuth:
        class _Auth(RequestContextAuth):
            def resolve_principal(self, request: object, token: str) -> Principal | None:
                return principal

        return _Auth()

    def test_authenticate_returns_and_attaches_the_context(self) -> None:
        request = RequestFactory().post("/v1/sync/push")
        principal = Principal(user_id=self.user.pk, org_id=self.org.pk)

        with self.assertNumQueries(1):
            ctx = self._auth(principal).authenticate(request, "token-opaco")

        assert ctx is not None  # para mypy; el aserto real es el de abajo
        self.assertIsInstance(ctx, RequestContext)
        self.assertEqual(ctx.roles, frozenset({Role.ADMIN}))
        # La vista lo recoge con `get_request_context(request)`, sin re-consultar.
        self.assertIs(get_request_context(request), ctx)

    def test_an_unresolvable_principal_yields_no_context(self) -> None:
        request = RequestFactory().post("/v1/sync/push")

        with self.assertNumQueries(0):
            self.assertIsNone(self._auth(None).authenticate(request, "token-invalido"))

        with self.assertRaises(RequestContextMissing):
            get_request_context(request)

    def test_the_bearer_header_drives_the_whole_chain(self) -> None:
        """De cabecera HTTP a contexto resuelto, por el camino real de Ninja.

        `HttpBearer.__call__` es lo que invoca el framework antes de la vista:
        parsea `Authorization`, extrae el token y llama a `authenticate`.
        """
        request = RequestFactory().post(
            "/v1/sync/push",
            headers={"authorization": "Bearer token-opaco"},
        )
        principal = Principal(user_id=self.user.pk, org_id=self.org.pk)

        with self.assertNumQueries(1):
            self._auth(principal)(request)

        self.assertEqual(get_request_context(request).roles, frozenset({Role.ADMIN}))

    def test_a_request_without_the_header_never_reaches_the_resolver(self) -> None:
        """Sin cabecera no hay principal: 0 queries y 401 (Ninja), sin contexto."""
        request = RequestFactory().post("/v1/sync/push")
        principal = Principal(user_id=self.user.pk, org_id=self.org.pk)

        with self.assertNumQueries(0):
            self.assertIsNone(self._auth(principal)(request))

        with self.assertRaises(RequestContextMissing):
            get_request_context(request)

    def test_the_base_class_cannot_be_used_without_resolving_a_principal(self) -> None:
        """Nadie puede montar la dependencia "a medias": el hook es abstracto."""
        with self.assertRaises(TypeError):
            RequestContextAuth()  # type: ignore[abstract]

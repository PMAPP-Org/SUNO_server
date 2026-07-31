"""Tests de las funciones de política (Paso 6, ADR-013).

Todo el archivo es `SimpleTestCase`: las políticas son **puras** (sin I/O),
así que no necesitan base de datos — y como `SimpleTestCase` prohíbe tocar la
BD, cualquier query que se colase en una política haría fallar estos tests.
Esa prohibición es parte de lo que se está verificando.

Los recursos son instancias **reales** de los modelos, sin guardar: la política
solo lee `org_id`/`owner_id`, así que un `Visit(...)` en memoria es un recurso
tan real como uno persistido, y evita montar una BD para probar aritmética de
conjuntos.

Hallazgo cubierto de forma explícita: **H-16** (org-membership disclosure).
`can_read_members` de QFieldCloud hacía `return True` sin comprobar membresía;
aquí se prueba que devuelve `False` para un no-miembro, y además
`NoRoleGrantsNothingTests` lo generaliza a **todas** las políticas del módulo.
"""

from __future__ import annotations

import uuid

from django.test import SimpleTestCase
from ninja.errors import HttpError

from visitacampo.core import policies
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Device, Role, Visit
from visitacampo.core.policies import (
    can_manage_device,
    can_read_members,
    can_read_visit,
    can_write_visit,
    check_manage_device,
    check_read_members,
    check_read_visit,
    check_write_visit,
    user_has_organization_roles,
)

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
USER = uuid.uuid4()
OTHER_USER = uuid.uuid4()


def ctx(*roles: object, org_id: uuid.UUID = ORG, user_id: uuid.UUID = USER) -> RequestContext:
    return RequestContext(user_id=user_id, org_id=org_id, roles=frozenset(roles))


def visit(org_id: uuid.UUID = ORG) -> Visit:
    return Visit(id=uuid.uuid4(), org_id=org_id)


def device(org_id: uuid.UUID = ORG, owner_id: uuid.UUID = USER) -> Device:
    return Device(id=uuid.uuid4(), org_id=org_id, owner_id=owner_id)


# --- La primitiva -----------------------------------------------------------


class UserHasOrganizationRolesTests(SimpleTestCase):
    """`user_has_organization_roles` es la única puerta: org **y** rol."""

    def test_true_for_a_member_with_the_role(self) -> None:
        self.assertTrue(user_has_organization_roles(ctx(Role.ADMIN), ORG, {Role.ADMIN}))

    def test_false_for_the_right_role_in_the_wrong_organization(self) -> None:
        self.assertFalse(user_has_organization_roles(ctx(Role.OWNER), OTHER_ORG, {Role.OWNER}))

    def test_false_for_a_member_without_the_role(self) -> None:
        self.assertFalse(user_has_organization_roles(ctx(Role.VIEWER), ORG, {Role.ADMIN}))

    def test_false_without_memberships(self) -> None:
        self.assertFalse(user_has_organization_roles(ctx(), ORG, set(Role.values)))


# --- H-16: membresías -------------------------------------------------------


class CanReadMembersTests(SimpleTestCase):
    """Regresión explícita del hallazgo **H-16** (RD-11)."""

    def test_false_for_a_user_who_is_not_a_member(self) -> None:
        """El bug original: `return True` sin comprobar nada."""
        self.assertFalse(can_read_members(ctx(), ORG))

    def test_false_for_a_member_of_another_organization(self) -> None:
        """Enumerar los miembros de la org ajena era el exploit descrito."""
        self.assertFalse(can_read_members(ctx(Role.OWNER, org_id=OTHER_ORG), ORG))

    def test_true_for_a_member(self) -> None:
        for role in Role.values:
            with self.subTest(role=role):
                self.assertTrue(can_read_members(ctx(role), ORG))

    def test_check_raises_404_for_a_non_member(self) -> None:
        """RD-11: denegación por inexistencia, no por prohibición."""
        with self.assertRaises(HttpError) as raised:
            check_read_members(ctx(), ORG)
        self.assertEqual(raised.exception.status_code, 404)

    def test_check_passes_for_a_member(self) -> None:
        check_read_members(ctx(Role.VIEWER), ORG)  # no debe lanzar


# --- Visitas ----------------------------------------------------------------


class CanReadVisitTests(SimpleTestCase):
    def test_true_for_any_member_role(self) -> None:
        for role in Role.values:
            with self.subTest(role=role):
                self.assertTrue(can_read_visit(ctx(role), visit()))

    def test_false_without_memberships(self) -> None:
        self.assertFalse(can_read_visit(ctx(), visit()))

    def test_false_for_a_visit_of_another_organization(self) -> None:
        self.assertFalse(can_read_visit(ctx(Role.OWNER), visit(org_id=OTHER_ORG)))

    def test_check_passes_for_a_viewer(self) -> None:
        check_read_visit(ctx(Role.VIEWER), visit())  # no debe lanzar

    def test_check_raises_404_across_organizations(self) -> None:
        with self.assertRaises(HttpError) as raised:
            check_read_visit(ctx(Role.OWNER), visit(org_id=OTHER_ORG))
        self.assertEqual(raised.exception.status_code, 404)

    def test_check_raises_404_for_a_non_member(self) -> None:
        """Sin membresía no se distingue "no puedes" de "no existe" (RD-11)."""
        with self.assertRaises(HttpError) as raised:
            check_read_visit(ctx(), visit())
        self.assertEqual(raised.exception.status_code, 404)


class CanWriteVisitTests(SimpleTestCase):
    def test_true_for_roles_that_capture_or_supervise(self) -> None:
        for role in (Role.OWNER, Role.ADMIN, Role.SUPERVISOR, Role.FIELD_AGENT):
            with self.subTest(role=role):
                self.assertTrue(can_write_visit(ctx(role), visit()))

    def test_false_for_a_viewer(self) -> None:
        self.assertFalse(can_write_visit(ctx(Role.VIEWER), visit()))

    def test_false_without_memberships(self) -> None:
        self.assertFalse(can_write_visit(ctx(), visit()))

    def test_false_for_a_visit_of_another_organization(self) -> None:
        self.assertFalse(can_write_visit(ctx(Role.OWNER), visit(org_id=OTHER_ORG)))

    def test_check_raises_403_for_a_viewer_in_the_same_organization(self) -> None:
        with self.assertRaises(HttpError) as raised:
            check_write_visit(ctx(Role.VIEWER), visit())
        self.assertEqual(raised.exception.status_code, 403)

    def test_check_raises_404_across_organizations(self) -> None:
        """Un 403 confirmaría que el recurso existe (RD-11)."""
        with self.assertRaises(HttpError) as raised:
            check_write_visit(ctx(Role.OWNER), visit(org_id=OTHER_ORG))
        self.assertEqual(raised.exception.status_code, 404)

    def test_check_passes_for_a_field_agent(self) -> None:
        check_write_visit(ctx(Role.FIELD_AGENT), visit())  # no debe lanzar


# --- Dispositivos -----------------------------------------------------------


class CanManageDeviceTests(SimpleTestCase):
    def test_true_for_the_owner_of_the_device(self) -> None:
        self.assertTrue(can_manage_device(ctx(Role.FIELD_AGENT), device()))

    def test_false_for_someone_elses_device(self) -> None:
        agent = ctx(Role.FIELD_AGENT)
        self.assertFalse(can_manage_device(agent, device(owner_id=OTHER_USER)))

    def test_false_for_a_supervisor_over_someone_elses_device(self) -> None:
        """Supervisar visitas no es administrar dispositivos ajenos."""
        self.assertFalse(can_manage_device(ctx(Role.SUPERVISOR), device(owner_id=OTHER_USER)))

    def test_true_for_administrators_over_any_device_of_their_org(self) -> None:
        for role in (Role.OWNER, Role.ADMIN):
            with self.subTest(role=role):
                self.assertTrue(can_manage_device(ctx(role), device(owner_id=OTHER_USER)))

    def test_false_for_a_viewer_even_over_its_own_device(self) -> None:
        self.assertFalse(can_manage_device(ctx(Role.VIEWER), device()))

    def test_false_across_organizations_even_for_an_owner(self) -> None:
        self.assertFalse(can_manage_device(ctx(Role.OWNER), device(org_id=OTHER_ORG)))

    def test_false_across_organizations_for_the_same_user_id(self) -> None:
        """Coincidir en `owner_id` no puede saltarse el aislamiento por org."""
        self.assertFalse(
            can_manage_device(ctx(Role.FIELD_AGENT), device(org_id=OTHER_ORG, owner_id=USER))
        )

    def test_check_raises_403_within_the_organization(self) -> None:
        with self.assertRaises(HttpError) as raised:
            check_manage_device(ctx(Role.FIELD_AGENT), device(owner_id=OTHER_USER))
        self.assertEqual(raised.exception.status_code, 403)

    def test_check_raises_404_across_organizations(self) -> None:
        with self.assertRaises(HttpError) as raised:
            check_manage_device(ctx(Role.OWNER), device(org_id=OTHER_ORG))
        self.assertEqual(raised.exception.status_code, 404)


# --- Deny-by-default, para toda política presente y futura ------------------


class NoRoleGrantsNothingTests(SimpleTestCase):
    """ADR-013: **ninguna** política concede acceso sin comprobar membresía.

    El registro de abajo debe cubrir todas las funciones `can_*` del módulo;
    el propio test lo verifica. Así, añadir una política nueva obliga a
    declarar aquí con qué recurso se prueba, y esa política queda
    automáticamente sujeta a los dos invariantes de H-16/RD-11: un usuario sin
    membresías no obtiene nada, y un miembro de otra organización tampoco.
    """

    def _registry(self) -> dict[str, object]:
        return {
            "can_read_visit": visit(),
            "can_write_visit": visit(),
            "can_read_members": ORG,
            "can_manage_device": device(),
        }

    def test_registry_covers_every_policy_of_the_module(self) -> None:
        exported = {name for name in policies.__all__ if name.startswith("can_")}
        self.assertEqual(exported, set(self._registry()))

    def test_a_user_without_memberships_gets_nothing(self) -> None:
        actor = ctx()
        for name, resource in self._registry().items():
            with self.subTest(policy=name):
                self.assertFalse(getattr(policies, name)(actor, resource))

    def test_a_member_of_another_organization_gets_nothing(self) -> None:
        """Todos los roles de otra org, contra todos los recursos de esta."""
        for role in Role.values:
            actor = ctx(role, org_id=OTHER_ORG)
            for name, resource in self._registry().items():
                with self.subTest(policy=name, role=role):
                    self.assertFalse(getattr(policies, name)(actor, resource))

    def test_every_policy_has_its_check_counterpart(self) -> None:
        """ADR-013: cada `can_x` con su `check_x` que lanza el error HTTP."""
        for name in self._registry():
            with self.subTest(policy=name):
                self.assertTrue(callable(getattr(policies, name.replace("can_", "check_", 1))))

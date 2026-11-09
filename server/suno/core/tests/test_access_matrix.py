"""Matriz cerrada de autorización por sección (ACC-01).

Estas pruebas son puras: la matriz debe poder auditarse sin base de datos y
debe negar cualquier sección, rol o capacidad que no conozca.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from suno.core.access import (
    CAPABILITY_MATRIX,
    AccessScope,
    Capability,
    Section,
    SectionRole,
    allowed_roles,
    capabilities_for,
    role_has_capability,
)


class AccessCatalogTests(SimpleTestCase):
    def test_every_section_has_an_explicit_role_matrix(self) -> None:
        self.assertEqual(set(CAPABILITY_MATRIX), set(Section.values))
        for section in Section.values:
            with self.subTest(section=section):
                self.assertTrue(allowed_roles(section))

    def test_every_declared_role_has_at_least_one_capability(self) -> None:
        for section, roles in CAPABILITY_MATRIX.items():
            for role, capabilities in roles.items():
                with self.subTest(section=section, role=role):
                    self.assertTrue(capabilities)
                    self.assertTrue(capabilities <= frozenset(Capability))

    def test_unknown_values_are_denied(self) -> None:
        self.assertEqual(capabilities_for("unknown", SectionRole.MANAGER), frozenset())
        self.assertEqual(capabilities_for(Section.PROJECTS, "unknown"), frozenset())
        self.assertFalse(role_has_capability("unknown", "unknown", Capability.READ))
        self.assertFalse(role_has_capability(Section.PROJECTS, SectionRole.MANAGER, "unknown"))

    def test_access_administration_uses_its_dedicated_role(self) -> None:
        self.assertEqual(allowed_roles(Section.USERS_ACCESS), {SectionRole.ACCESS_ADMIN})
        self.assertTrue(
            role_has_capability(
                Section.USERS_ACCESS,
                SectionRole.ACCESS_ADMIN,
                Capability.MANAGE_ACCESS,
            )
        )

    def test_equipment_profiles_match_the_approved_design(self) -> None:
        self.assertEqual(
            allowed_roles(Section.EQUIPMENT),
            {
                SectionRole.MANAGER,
                SectionRole.REVIEWER,
                SectionRole.EQUIPMENT_OPERATOR,
                SectionRole.SST_AUDITOR,
                SectionRole.VIEWER,
            },
        )
        self.assertTrue(
            role_has_capability(
                Section.EQUIPMENT,
                SectionRole.EQUIPMENT_OPERATOR,
                Capability.CREATE,
            )
        )
        self.assertFalse(
            role_has_capability(
                Section.EQUIPMENT,
                SectionRole.EQUIPMENT_OPERATOR,
                Capability.REVIEW,
            )
        )
        self.assertTrue(
            role_has_capability(
                Section.EQUIPMENT,
                SectionRole.SST_AUDITOR,
                Capability.EXPORT,
            )
        )

    def test_scope_values_are_explicit(self) -> None:
        self.assertEqual(set(AccessScope.values), {"organization", "projects"})

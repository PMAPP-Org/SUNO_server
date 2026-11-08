"""Contrato de compatibilidad de la migración inicial de accesos (ACC-04)."""

from __future__ import annotations

from importlib import import_module

from django.test import SimpleTestCase

from suno.core.access import AccessScope, Section, SectionRole
from suno.core.models import Role

MIGRATION = import_module("suno.core.migrations.0015_add_section_access")


class LegacyGrantMappingTests(SimpleTestCase):
    def test_every_legacy_role_has_a_mapping(self) -> None:
        self.assertEqual(set(MIGRATION.LEGACY_GRANTS), set(Role.values))

    def test_owner_and_admin_keep_equivalent_access(self) -> None:
        self.assertEqual(MIGRATION.LEGACY_GRANTS[Role.OWNER], MIGRATION.LEGACY_GRANTS[Role.ADMIN])

    def test_only_administration_receives_access_administration(self) -> None:
        expected = (Section.USERS_ACCESS, SectionRole.ACCESS_ADMIN, AccessScope.ORGANIZATION)
        for role, grants in MIGRATION.LEGACY_GRANTS.items():
            with self.subTest(role=role):
                self.assertEqual(expected in grants, role in {Role.OWNER, Role.ADMIN})

    def test_field_agent_projects_are_explicitly_project_scoped(self) -> None:
        self.assertIn(
            (Section.PROJECTS, SectionRole.CONTRIBUTOR, AccessScope.PROJECTS),
            MIGRATION.LEGACY_GRANTS[Role.FIELD_AGENT],
        )

    def test_field_agent_does_not_gain_budget_access(self) -> None:
        sections = {section for section, _role, _scope in MIGRATION.LEGACY_GRANTS[Role.FIELD_AGENT]}
        self.assertNotIn(Section.BUDGETS, sections)

"""La migración de mano de obra da la sección a la administración que ya existía."""

from __future__ import annotations

import unittest
from importlib import import_module

from django.apps import apps
from django.test import SimpleTestCase, TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.models import Role, SectionGrant
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

MIGRATION = import_module("suno.core.migrations.0031_labor")

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class LaborGrantMappingTests(SimpleTestCase):
    def test_only_owner_and_admin_receive_labor(self) -> None:
        expected = (Section.LABOR, SectionRole.MANAGER, AccessScope.ORGANIZATION)
        self.assertEqual(MIGRATION.LABOR_GRANTS, {Role.OWNER: expected, Role.ADMIN: expected})


@skip_without_db
class LaborGrantBackfillTests(TestCase):
    def test_backfill_is_idempotent_and_only_for_administration(self) -> None:
        org = make_org("Previa")
        admin = make_user(org, username="admin-previa", role=Role.ADMIN)
        supervisor = make_user(org, username="super-previa", role=Role.SUPERVISOR)
        # Una membresía anterior a la migración no tenía la sección.
        SectionGrant.objects.filter(section=Section.LABOR).delete()
        MIGRATION.forwards_labor_grants(apps, None)
        MIGRATION.forwards_labor_grants(apps, None)
        grants = SectionGrant.objects.filter(section=Section.LABOR)
        self.assertEqual(
            list(grants.values_list("user_id", "role", "scope")),
            [(admin.pk, SectionRole.MANAGER, AccessScope.ORGANIZATION)],
        )
        self.assertFalse(grants.filter(user=supervisor).exists())
        MIGRATION.reverse_labor_grants(apps, None)
        self.assertFalse(SectionGrant.objects.filter(section=Section.LABOR).exists())

"""La migración del libro de costos da Costos a la administración que ya existía."""

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

MIGRATION = import_module("suno.core.migrations.0028_costs_ledger")

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class CostsGrantMappingTests(SimpleTestCase):
    def test_only_owner_and_admin_receive_costs(self) -> None:
        expected = (Section.COSTS, SectionRole.MANAGER, AccessScope.ORGANIZATION)
        self.assertEqual(MIGRATION.COSTS_GRANTS, {Role.OWNER: expected, Role.ADMIN: expected})


@skip_without_db
class CostsGrantBackfillTests(TestCase):
    def test_backfill_is_idempotent_and_only_for_administration(self) -> None:
        org = make_org("Previa")
        admin = make_user(org, username="admin-previa", role=Role.ADMIN)
        supervisor = make_user(org, username="super-previa", role=Role.SUPERVISOR)
        # Una membresía anterior a la migración no tenía la sección.
        SectionGrant.objects.filter(section=Section.COSTS).delete()
        MIGRATION.forwards_costs_grants(apps, None)
        MIGRATION.forwards_costs_grants(apps, None)
        grants = SectionGrant.objects.filter(section=Section.COSTS)
        self.assertEqual(
            list(grants.values_list("user_id", "role", "scope")),
            [(admin.pk, SectionRole.MANAGER, AccessScope.ORGANIZATION)],
        )
        self.assertFalse(grants.filter(user=supervisor).exists())

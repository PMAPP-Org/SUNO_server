"""Servicios del libro de costos (spec, 1.2 y 5): asignación, ciclo, autoaprobación, reverso."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from suno.authn.tests.factories import make_user
from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import AIU, V2_ROWS
from suno.core.access import AccessScope, SectionRole
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    CostDocumentType,
    CostEntry,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.costs.services import CostRejected, EntryData

from .factories import cost_world, entry, grant_costs, item, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class CostServiceTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        self.project = self.world.project
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.concrete = item(self.budget, "2.1")

    def data(self, **overrides: Any) -> EntryData:
        values: dict[str, Any] = {
            "incurred_on": date(2026, 3, 10),
            "kind": CostKind.MATERIALS,
            "description": "Concreto premezclado",
            "amount": D("1500000.00"),
            "third_party": "Concretos SA",
            "document_type": CostDocumentType.INVOICE,
            "document_number": "F-101",
            "scope": CostScope.ITEM,
            "item_ids": (self.concrete.pk,),
        }
        values.update(overrides)
        return EntryData(**values)

    def create(
        self, user: AppUser | None = None, *, support: bool = True, **overrides: Any
    ) -> CostEntry:
        return services.create_draft(
            self.world.ctx(user),
            self.project,
            self.data(**overrides),
            upload=pdf_upload() if support else None,
            name="factura.pdf" if support else "",
        )

    def test_create_item_draft_keeps_origin_chapter_items_and_audit(self) -> None:
        cost = self.create(task_id=self.world.task(5).pk)
        self.assertEqual(cost.status, CostStatus.DRAFT)
        self.assertEqual(cost.chapter_id, item(self.budget, "2").pk)
        self.assertEqual(list(cost.items.values_list("code", flat=True)), ["2.1"])
        self.assertEqual(cost.attachment_name, "factura.pdf")
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.COST_CREATED, target=f"cost:{cost.pk}"
            ).exists()
        )

    def test_assignment_rules(self) -> None:
        other, other_budget = cost_world("Otra constructora")
        chapter = item(self.budget, "2").pk
        steel = item(self.budget, "2.2").pk
        cases: dict[str, dict[str, Any]] = {
            "ítem de otro proyecto": {"item_ids": (item(other_budget, "2.1").pk,)},
            "capítulo como ítem": {"item_ids": (chapter,)},
            "selección de dos capítulos": {
                "scope": CostScope.SELECTION,
                "chapter_id": chapter,
                "item_ids": (self.concrete.pk, item(self.budget, "1.1").pk),
            },
            "selección de un ítem": {
                "scope": CostScope.SELECTION,
                "chapter_id": chapter,
                "item_ids": (self.concrete.pk,),
            },
            "tarea ajena": {"task_id": other.task(5).pk},
            "tarea resumen": {"task_id": self.world.task(4).pk},
            "indirecto con tarea": {
                "scope": CostScope.INDIRECT,
                "item_ids": (),
                "task_id": self.world.task(5).pk,
            },
            "fecha futura": {"incurred_on": timezone.localdate().replace(year=2100)},
            "valor cero": {"amount": D("0")},
            "valor con tres decimales": {"amount": D("10.005")},
            "sin descripción": {"description": "  "},
        }
        for name, overrides in cases.items():
            with self.subTest(name), self.assertRaises(CostRejected):
                self.create(**overrides)
        selection = self.create(
            scope=CostScope.SELECTION, chapter_id=chapter, item_ids=(self.concrete.pk, steel)
        )
        self.assertEqual(selection.items.count(), 2)
        whole = self.create(scope=CostScope.CHAPTER, chapter_id=chapter, item_ids=())
        self.assertEqual(whole.chapter_id, chapter)
        self.assertEqual(self.create(scope=CostScope.INDIRECT, item_ids=()).scope, "indirect")

    def test_an_item_retired_from_the_current_version_is_rejected(self) -> None:
        apply_rows(self.world.ctx(), self.budget, V2_ROWS, charges=AIU)  # quita el 1.2
        with self.assertRaises(CostRejected) as rejected:
            self.create(item_ids=(item(self.budget, "1.2").pk,))
        self.assertIn("versión vigente", rejected.exception.errors[0])

    def test_approval_needs_support_and_someone_else(self) -> None:
        reviewer = self.world.ctx(self.world.supervisor)
        without = self.create(support=False)
        with self.assertRaises(CostRejected):
            services.approve(reviewer, self.project, without.pk)
        cost = self.create()
        with self.assertRaises(CostRejected) as own:
            services.approve(self.world.ctx(), self.project, cost.pk)
        self.assertIn("Nadie aprueba", own.exception.errors[0])
        approved = services.approve(reviewer, self.project, cost.pk)
        self.assertEqual(
            (approved.status, approved.decided_by_id), ("approved", self.world.supervisor.pk)
        )
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.COST_APPROVED).exists())

    def test_the_last_editor_does_not_decide(self) -> None:
        cost = self.create()
        reviewer = self.world.ctx(self.world.supervisor)
        services.update_draft(reviewer, self.project, cost.pk, self.data(amount=D("9.00")))
        with self.assertRaises(CostRejected):
            services.approve(reviewer, self.project, cost.pk)
        third = make_user(self.world.org, username="otro-revisor", role=Role.VIEWER)
        grant_costs(self.world, third, SectionRole.REVIEWER)
        approved = services.approve(self.world.ctx(third), self.project, cost.pk)
        self.assertEqual(approved.status, "approved")

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        cost = self.create(self.contributor)
        reviewer = self.world.ctx(self.world.supervisor)
        with self.assertRaises(CostRejected):
            services.reject(reviewer, self.project, cost.pk, "  ")
        rejected = services.reject(reviewer, self.project, cost.pk, "Falta el NIT")
        self.assertEqual((rejected.status, rejected.decision_note), ("rejected", "Falta el NIT"))
        with self.assertRaises(PermissionDenied):  # solo su autor lo corrige
            services.update_draft(self.world.ctx(), self.project, cost.pk, self.data())
        again = services.update_draft(
            self.world.ctx(self.contributor),
            self.project,
            cost.pk,
            self.data(third_party="NIT 900"),
        )
        self.assertEqual(
            (again.status, again.decided_by_id, again.decision_note), ("draft", None, "")
        )
        event = AuditEvent.objects.get(action=AuditAction.COST_UPDATED)
        self.assertTrue(event.metadata["resubmitted"])

    def test_roles(self) -> None:
        mine = self.create(self.contributor)
        theirs = self.create()
        contributor = self.world.ctx(self.contributor)
        with self.assertRaises(PermissionDenied):
            services.update_draft(contributor, self.project, theirs.pk, self.data())
        with self.assertRaises(PermissionDenied):
            services.delete_draft(contributor, self.project, theirs.pk)
        with self.assertRaises(PermissionDenied):
            services.approve(contributor, self.project, theirs.pk)
        with self.assertRaises(PermissionDenied):
            self.create(self.viewer)
        services.delete_draft(contributor, self.project, mine.pk)
        self.assertFalse(CostEntry.objects.filter(pk=mine.pk).exists())
        deleted = AuditEvent.objects.get(action=AuditAction.COST_DELETED)
        self.assertEqual(deleted.metadata["description"], "Concreto premezclado")

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(
            self.world,
            scoped,
            SectionRole.CONTRIBUTOR,
            scope=AccessScope.PROJECTS,
            projects=(elsewhere,),
        )
        with self.assertRaises(PermissionDenied):
            self.create(scoped)

    def test_approved_is_neither_edited_nor_deleted(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        with self.assertRaises(CostRejected):
            services.update_draft(self.world.ctx(), self.project, cost.pk, self.data())
        with self.assertRaises(CostRejected):
            services.delete_draft(self.world.ctx(), self.project, cost.pk)

    def test_reverse_once_on_its_own_date(self) -> None:
        cost = entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=item(self.budget, "2"),
        )
        manager = self.world.ctx()
        with self.assertRaises(PermissionDenied):  # el Revisor no reversa
            services.reverse(self.world.ctx(self.world.supervisor), self.project, cost.pk, "x")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, cost.pk, " ")
        reversal = services.reverse(manager, self.project, cost.pk, "Factura duplicada")
        self.assertEqual(reversal.amount, D("-1000.00"))
        self.assertEqual(reversal.incurred_on, timezone.localdate())
        self.assertEqual((reversal.status, reversal.reverses_id), ("approved", cost.pk))
        self.assertEqual(reversal.decision_note, "Factura duplicada")
        self.assertEqual(list(reversal.items.all()), [self.concrete])
        self.assertTrue(reversal.description.startswith("Reverso: "))
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, cost.pk, "Otra vez")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, reversal.pk, "Reverso del reverso")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, entry(self.world).pk, "Borrador")
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.COST_REVERSED).exists())

    def test_support_url_after_reading_permission(self) -> None:
        cost = self.create()
        url = services.support_url(self.world.ctx(self.viewer), self.project, cost)
        self.assertIn(str(cost.attachment_id), url)

    def test_catalog(self) -> None:
        catalog = load_catalog(self.world.ctx(), self.project)
        found = catalog.by_label("2.1 Concreto zapatas")
        self.assertIsNotNone(found)
        self.assertEqual(found.item_id if found else None, self.concrete.pk)
        self.assertEqual(
            {row.code for row in catalog.items_under(item(self.budget, "2").pk)},
            {"2.1", "2.2", "2.4"},
        )
        self.assertEqual([row.code for row in catalog.chapters()], ["1", "2"])
        self.assertEqual(len(catalog.by_code("2.1")), 1)
        tasks = assignable_tasks(self.world.ctx(), self.project)
        self.assertNotIn(self.world.task(4).pk, tasks)  # resumen
        self.assertIn(self.world.task(5).pk, tasks)

    def test_the_support_is_stored_outside_the_row_lock(self) -> None:
        cost = self.create()
        depth: list[int] = []
        real = services.store_upload

        def spy(upload: Any) -> Any:
            depth.append(len(connection.atomic_blocks))
            return real(upload)

        outer = len(connection.atomic_blocks)
        with mock.patch.object(services, "store_upload", side_effect=spy):
            services.update_draft(
                self.world.ctx(), self.project, cost.pk, self.data(), upload=pdf_upload()
            )
        self.assertEqual(depth, [outer])

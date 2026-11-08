from __future__ import annotations

import unittest

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from suno.core.models import AuditEvent, LocationLevel, LocationNode
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.locations import (
    LevelError,
    LevelSpec,
    clean_name,
    from_project,
    load_tree,
    node_key,
    path_values,
    project_levels,
    save_levels,
)
from suno.projects.tests.factories import make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class NamesTests(SimpleTestCase):
    def test_clean_name(self) -> None:
        self.assertEqual(clean_name("  Tramo   2 "), "Tramo 2")
        self.assertEqual(clean_name(2.0), "2")
        self.assertEqual(clean_name(12.5), "12.5")
        self.assertEqual(clean_name(None), "")

    def test_node_key_ignores_case_accents_and_spaces(self) -> None:
        self.assertEqual(node_key("Costado  IZQUIERDO"), node_key("costado izquierdo"))
        self.assertEqual(node_key("Puente Nº 3 — Sección"), node_key("puente nº 3 — seccion"))

    def test_path_values_stop_at_first_gap(self) -> None:
        values = path_values(["Tramo 2", "", "Izquierdo"])
        self.assertEqual(values.names, ("Tramo 2",))
        self.assertTrue(values.incomplete)
        self.assertFalse(values.truncated)

    def test_path_values_truncate_long_names(self) -> None:
        values = path_values(["x" * 130])
        self.assertEqual(len(values.names[0]), 120)
        self.assertTrue(values.truncated)

    def test_path_values_empty(self) -> None:
        values = path_values([None, " "])
        self.assertEqual(values.names, ())
        self.assertFalse(values.incomplete)


@skip_without_db
class LevelsTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()

    def test_save_and_rename_levels(self) -> None:
        save_levels(self.ctx, self.world.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        save_levels(self.ctx, self.world.project, [LevelSpec("PR"), LevelSpec("Tramo")])
        names = [lvl.name for lvl in project_levels(self.ctx, self.world.project)]
        self.assertEqual(names, ["PR", "Tramo"])
        self.assertEqual(AuditEvent.objects.filter(action="matrix.levels_changed").count(), 2)

    def test_all_or_none_from_project(self) -> None:
        with self.assertRaisesMessage(LevelError, "todos los niveles"):
            save_levels(
                self.ctx,
                self.world.project,
                [LevelSpec("Tramo", "188743731"), LevelSpec("PR")],
            )
        save_levels(
            self.ctx,
            self.world.project,
            [LevelSpec("Tramo", "188743731"), LevelSpec("PR", "188743734")],
        )
        self.assertTrue(from_project(project_levels(self.ctx, self.world.project)))

    def test_rejects_duplicates_empty_and_too_many(self) -> None:
        with self.assertRaisesMessage(LevelError, "repetido"):
            save_levels(self.ctx, self.world.project, [LevelSpec("Tramo"), LevelSpec("tramo")])
        with self.assertRaisesMessage(LevelError, "nombre"):
            save_levels(self.ctx, self.world.project, [LevelSpec("  ")])
        with self.assertRaisesMessage(LevelError, "6"):
            save_levels(self.ctx, self.world.project, [LevelSpec(f"N{i}") for i in range(7)])

    def test_removes_an_unused_trailing_level(self) -> None:
        save_levels(self.ctx, self.world.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        save_levels(self.ctx, self.world.project, [LevelSpec("Tramo")])
        names = [lvl.name for lvl in project_levels(self.ctx, self.world.project)]
        self.assertEqual(names, ["Tramo"])

    def test_rejects_invalid_project_field_id(self) -> None:
        for bad in ("12a", "²"):
            with self.assertRaisesMessage(LevelError, "no es válido"):
                save_levels(self.ctx, self.world.project, [LevelSpec("Tramo", bad)])

    def test_cannot_remove_a_level_in_use(self) -> None:
        save_levels(self.ctx, self.world.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        levels = project_levels(self.ctx, self.world.project)
        load_tree(self.ctx, self.world.project).ensure(self.ctx, levels, ("Tramo 1", "PR 1"))
        with self.assertRaisesMessage(LevelError, "PR"):
            save_levels(self.ctx, self.world.project, [LevelSpec("Tramo")])


@skip_without_db
class TreeTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()
        save_levels(self.ctx, self.world.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        self.levels = project_levels(self.ctx, self.world.project)

    def test_ensure_creates_and_reuses_nodes(self) -> None:
        tree = load_tree(self.ctx, self.world.project)
        node, created = tree.ensure(self.ctx, self.levels, ("Tramo 2", "PR 12+300"))
        self.assertEqual(len(created), 2)
        again, created_again = tree.ensure(self.ctx, self.levels, ("tramo  2", "pr 12+300"))
        self.assertEqual(again.pk, node.pk)
        self.assertEqual(created_again, [])
        self.assertEqual(tree.label(node.pk), "Tramo 2 / PR 12+300")
        parent = LocationNode.objects.get(name="Tramo 2")
        self.assertEqual(node.parent_id, parent.pk)
        self.assertEqual(node.path, f"/{parent.pk.hex}/{node.pk.hex}/")
        self.assertEqual(node.level.depth, 2)

    def test_ensure_reuses_a_retired_node_and_clears_retired_at(self) -> None:
        tree = load_tree(self.ctx, self.world.project)
        node, _ = tree.ensure(self.ctx, self.levels, ("Tramo 2",))
        LocationNode.objects.filter(pk=node.pk).update(retired_at=timezone.now())
        fresh = load_tree(self.ctx, self.world.project)
        again, created = fresh.ensure(self.ctx, self.levels, ("tramo 2",))
        self.assertEqual(again.pk, node.pk)
        self.assertEqual(created, [])
        self.assertIsNone(LocationNode.objects.get(pk=node.pk).retired_at)

    def test_find_and_descendants(self) -> None:
        tree = load_tree(self.ctx, self.world.project)
        tramo, _ = tree.ensure(self.ctx, self.levels, ("Tramo 2",))
        pr_a, _ = tree.ensure(self.ctx, self.levels, ("Tramo 2", "PR 2"))
        pr_b, _ = tree.ensure(self.ctx, self.levels, ("Tramo 2", "PR 1"))
        tree.ensure(self.ctx, self.levels, ("Tramo 3", "PR 9"))
        fresh = load_tree(self.ctx, self.world.project)
        self.assertEqual(fresh.find(("TRAMO 2", "pr 1")), pr_b.pk)
        self.assertIsNone(fresh.find(("Tramo 9",)))
        self.assertEqual(fresh.descendants(tramo.pk), [tramo.pk, pr_b.pk, pr_a.pk])
        self.assertTrue(fresh.contains(tramo.pk, pr_a.pk))
        self.assertFalse(fresh.contains(pr_a.pk, tramo.pk))

    def test_ensure_rejects_more_values_than_levels(self) -> None:
        tree = load_tree(self.ctx, self.world.project)
        with self.assertRaises(LevelError):
            tree.ensure(self.ctx, self.levels, ("a", "b", "c"))

    def test_other_project_nodes_are_not_loaded(self) -> None:
        other = make_field_world("Otra")
        save_levels(other.ctx(), other.project, [LevelSpec("Tramo")])
        load_tree(other.ctx(), other.project).ensure(
            other.ctx(), project_levels(other.ctx(), other.project), ("Tramo 2",)
        )
        self.assertIsNone(load_tree(self.ctx, self.world.project).find(("Tramo 2",)))
        self.assertEqual(LocationLevel.objects.filter(project=other.project).count(), 1)

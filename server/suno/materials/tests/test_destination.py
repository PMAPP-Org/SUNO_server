"""El destino escrito como texto (decisión 4 del plan) y validado como en la 6a."""

from __future__ import annotations

import unittest
from typing import Any

from django.test import TestCase

from suno.core.models import CostScope, MaterialIssueItem
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.costs.tests.factories import item
from suno.materials.destination import (
    DestinationData,
    check_destination,
    destination_key,
    destination_label,
    destination_options,
    destination_text,
    link_items,
    parse_destination,
)

from .factories import bare_issue, material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class DestinationTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.ctx = self.mw.ctx()
        self.catalog = load_catalog(self.ctx, self.mw.project)
        self.tasks = assignable_tasks(self.ctx, self.mw.project)
        budget = self.mw.cw.budget
        self.concrete, self.steel = item(budget, "2.1"), item(budget, "2.2")
        self.structure = item(budget, "2")

    def parsed(self, text: str) -> Any:
        data, error = parse_destination(text, self.catalog)
        self.assertEqual(error, "", text)
        return data

    def test_texts_that_name_a_destination(self) -> None:
        indirect = DestinationData(str(CostScope.INDIRECT))
        self.assertEqual(self.parsed(""), indirect)
        self.assertEqual(self.parsed(" indirecto "), indirect)
        item_only = DestinationData(str(CostScope.ITEM), (self.concrete.pk,))
        self.assertEqual(self.parsed("2.1 Concreto zapatas"), item_only)
        self.assertEqual(self.parsed("2.1"), item_only)
        chapter = DestinationData(str(CostScope.CHAPTER), chapter_id=self.structure.pk)
        for text in ("2", "Capítulo 2", "capítulo 2 Estructura"):
            with self.subTest(text=text):
                self.assertEqual(self.parsed(text), chapter)
        self.assertEqual(
            self.parsed("2.1, 2.2"),
            DestinationData(
                str(CostScope.SELECTION), (self.concrete.pk, self.steel.pk), self.structure.pk
            ),
        )

    def test_texts_that_do_not(self) -> None:
        for text, error in (
            ("9.9", "«9.9» no es un ítem ni un capítulo del presupuesto vigente."),
            ("2, 2.1", "Una selección lleva solo ítems (no capítulos), separados por comas."),
            ("Capítulo 9", "No hay un capítulo «9» en la versión vigente del presupuesto."),
            (
                ",",
                "Escribe el destino: un ítem, «Capítulo …» o varios códigos separados por comas.",
            ),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_destination(text, self.catalog), (None, error))

    def test_the_check_is_the_one_of_the_ledger(self) -> None:
        found = check_destination(self.catalog, self.tasks, self.parsed("2.1, 1.1"))
        self.assertEqual(found, (["Una selección lleva dos o más ítems del mismo capítulo."], None))
        task = self.mw.world.task(5)
        errors, assignment = check_destination(
            self.catalog,
            self.tasks,
            DestinationData(str(CostScope.ITEM), (self.concrete.pk,), task_id=task.pk),
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            (assignment.chapter_id, assignment.task_id),  # type: ignore[union-attr]
            (self.structure.pk, task.pk),
        )
        errors, _ = check_destination(
            self.catalog, self.tasks, DestinationData(str(CostScope.INDIRECT), task_id=task.pk)
        )
        self.assertEqual(
            errors, ["Un costo indirecto va al proyecto: sin ítem, capítulo ni tarea."]
        )

    def test_labels_texts_and_keys(self) -> None:
        item_scope, sel = str(CostScope.ITEM), str(CostScope.SELECTION)
        chapter_scope = str(CostScope.CHAPTER)
        self.assertEqual(
            destination_label(self.catalog, str(CostScope.INDIRECT), None, ()),
            "Indirecto (proyecto)",
        )
        self.assertEqual(
            destination_label(
                self.catalog, item_scope, self.structure.pk, (self.concrete.pk,), "2.1 Columnas"
            ),
            "2.1 Concreto zapatas · tarea 2.1 Columnas",
        )
        self.assertEqual(
            destination_label(self.catalog, chapter_scope, self.structure.pk, ()),
            "Capítulo 2 Estructura",
        )
        pair = (self.steel.pk, self.concrete.pk)
        self.assertEqual(
            destination_label(self.catalog, sel, self.structure.pk, pair),
            "Ítems 2.2, 2.1 de 2 Estructura",
        )
        self.assertEqual(destination_text(self.catalog, sel, self.structure.pk, pair), "2.2, 2.1")
        self.assertEqual(
            destination_text(self.catalog, chapter_scope, self.structure.pk, ()), "Capítulo 2"
        )
        self.assertEqual(
            destination_key(sel, self.structure.pk, pair, None),
            destination_key(sel, self.structure.pk, pair[::-1], None),
        )
        options = destination_options(self.catalog)
        self.assertEqual(options[:2], ["Indirecto", "Capítulo 1 Preliminares"])
        self.assertIn("2.1 Concreto zapatas", options)

    def test_link_items_replaces_the_links(self) -> None:
        issue = bare_issue(self.mw)
        link_items(self.ctx, MaterialIssueItem, "issue", issue, [self.concrete.pk, self.steel.pk])
        link_items(self.ctx, MaterialIssueItem, "issue", issue, [self.steel.pk])
        self.assertEqual(
            list(MaterialIssueItem.objects.filter(issue=issue).values_list("item_id", flat=True)),
            [self.steel.pk],
        )

"""Comparación contractual contra vigente, filtros y totales (coste fijo en consultas)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from django.test import TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.budgets.comparison import (
    Comparison,
    budget_summaries,
    build_comparison,
    filter_rows,
    version_rows,
)
from suno.budgets.diff import (
    LABEL_LESS,
    LABEL_MORE,
    LABEL_NEW,
    LABEL_PRICE,
    LABEL_REMOVED,
)
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import apply_rows, make_budget
from .samples import AIU, V1_ROWS, V2_ROWS

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class ComparisonTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        self.budget = make_budget(self.project, self.admin)

    def compare_v1_v2(self) -> Comparison:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        self.budget.refresh_from_db()
        return build_comparison(self.ctx, self.budget)


@skip_without_db
class BuildComparisonTests(ComparisonTestCase):
    def test_rows_follow_the_current_order_with_removed_rows_in_place(self) -> None:
        data = self.compare_v1_v2()
        self.assertEqual(
            [r.code for r in data.rows], ["1", "1.1", "1.2", "2", "2.1", "2.2", "2.3", "2.4"]
        )
        self.assertEqual(
            {r.code: r.labels for r in data.rows},
            {
                "1": (),
                "1.1": (LABEL_MORE,),
                "1.2": (LABEL_REMOVED,),
                "2": (),
                "2.1": (LABEL_PRICE,),
                "2.2": (LABEL_LESS,),
                "2.3": (LABEL_NEW,),
                "2.4": (),
            },
        )
        self.assertEqual([r.depth for r in data.rows], [0, 1, 1, 0, 1, 1, 1, 1])

    def test_values_and_differences(self) -> None:
        rows = {r.code: r for r in self.compare_v1_v2().rows}
        moved = rows["1.1"]
        assert moved.contractual is not None and moved.current is not None
        self.assertEqual((moved.contractual.quantity, moved.current.quantity), (D("100"), D("120")))
        self.assertEqual((moved.diff, moved.diff_pct), (D("30000.00"), D("20.00")))
        removed = rows["1.2"]
        self.assertIsNone(removed.current)
        self.assertEqual((removed.diff, removed.diff_pct), (D("-1000000.00"), D("-100.00")))
        new = rows["2.3"]
        self.assertIsNone(new.contractual)
        self.assertEqual((new.diff, new.diff_pct), (D("750000.00"), None))
        self.assertEqual(rows["1"].diff, D("-970000.00"))
        self.assertEqual(rows["1.2"].parent_id, rows["1"].item_id)
        self.assertEqual(rows["2.3"].root_id, rows["2"].item_id)
        self.assertEqual(rows["2.3"].badges, [("No previsto", "new")])

    def test_footer_with_charges_and_totals(self) -> None:
        footer = self.compare_v1_v2().footer
        self.assertEqual(
            [f.label for f in footer],
            [
                "Costo directo",
                "Administración",
                "Imprevistos",
                "Utilidad",
                "IVA sobre utilidad",
                "Total",
            ],
        )
        self.assertEqual(
            (footer[0].contractual, footer[0].current, footer[0].diff),
            (D("9130000.00"), D("8690000.00"), D("-440000.00")),
        )
        self.assertEqual(footer[1].note, "10 % sobre costo directo")
        self.assertEqual(footer[4].note, "19 % sobre línea 3 (Utilidad)")
        total = footer[-1]
        self.assertEqual(
            (total.contractual, total.current, total.diff, total.diff_pct, total.strong),
            (D("11042735.00"), D("10510555.00"), D("-532180.00"), D("-4.82"), True),
        )

    def test_counts_and_chapters(self) -> None:
        data = self.compare_v1_v2()
        self.assertEqual([r.code for r in data.chapters], ["1", "2"])
        self.assertEqual((data.item_count, data.changed_count), (6, 5))

    def test_a_single_version_has_no_changes(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        self.budget.refresh_from_db()
        data = build_comparison(self.ctx, self.budget)
        assert data.contractual is not None and data.current is not None
        self.assertEqual(data.contractual.pk, data.current.pk)
        self.assertTrue(all(not r.labels for r in data.rows))
        self.assertEqual(data.footer[-1].diff, D("0.00"))

    def test_without_versions(self) -> None:
        data = build_comparison(self.ctx, self.budget)
        self.assertEqual(
            (data.contractual, data.current, data.rows, data.footer), (None, None, [], [])
        )

    def test_filters_keep_the_chapters_of_what_matches(self) -> None:
        rows = self.compare_v1_v2().rows

        def codes(**filters: Any) -> list[str]:
            return [r.code for r in filter_rows(rows, **filters)]

        chapter_one = next(r.item_id for r in rows if r.code == "1")
        self.assertEqual(codes(), [r.code for r in rows])
        self.assertEqual(codes(changes_only=True), ["1", "1.1", "1.2", "2", "2.1", "2.2", "2.3"])
        self.assertEqual(codes(query="ACERO"), ["2", "2.2"])
        self.assertEqual(codes(query="localizacion"), ["1", "1.1"])
        self.assertEqual(codes(query="2.3"), ["2", "2.3"])
        self.assertEqual(codes(chapter_id=chapter_one), ["1", "1.1", "1.2"])
        self.assertEqual(
            codes(chapter_id=chapter_one, changes_only=True, query="cerramiento"), ["1", "1.2"]
        )

    def test_version_rows(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS)
        rows = version_rows(self.ctx, v1)
        self.assertEqual(
            [(r.code, r.depth) for r in rows],
            [("1", 0), ("1.1", 1), ("1.2", 1), ("2", 0), ("2.1", 1), ("2.2", 1), ("2.4", 1)],
        )
        self.assertEqual(rows[0].total, D("1150000.00"))

    def test_comparison_costs_three_queries_whatever_the_size(self) -> None:
        versions = [
            BudgetVersion.objects.create(
                org=self.org,
                budget=self.budget,
                number=number,
                status=BudgetVersionStatus.APPLIED,
                file_name="p.xlsx",
                source_file=b"",
                uploaded_by=self.admin,
                direct_cost=D("0"),
                total=D("0"),
            )
            for number in (1, 2)
        ]
        items = BudgetItem.objects.bulk_create(
            [BudgetItem(org=self.org, budget=self.budget, code=str(i)) for i in range(3000)]
        )
        for version in versions:
            BudgetItemVersion.objects.bulk_create(
                [
                    BudgetItemVersion(
                        org=self.org,
                        version=version,
                        item=item,
                        kind="item",
                        sort_order=i,
                        description=f"Ítem {i}",
                        unit="u",
                        quantity=D(i % 7 + version.number),
                        unit_price=D("10.00"),
                        total=D(i % 7 + version.number) * 10,
                    )
                    for i, item in enumerate(items)
                ],
                batch_size=1000,
            )
        self.budget.contractual_version = versions[0]
        self.budget.save(update_fields=["contractual_version"])
        with self.assertNumQueries(3):
            data = build_comparison(self.ctx, self.budget)
        self.assertEqual(len(data.rows), 3000)
        self.assertTrue(all(r.labels == (LABEL_MORE,) for r in data.rows))


@skip_without_db
class BudgetSummariesTests(ComparisonTestCase):
    def test_totals_per_budget_and_for_the_project_in_two_queries(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        other = make_budget(self.project, self.admin, name="Interventoría", code="I-01")
        apply_rows(self.ctx, other, V1_ROWS)  # cargos en 0: total = costo directo
        make_budget(self.project, self.admin, name="Adiciones", code="A-01")
        with self.assertNumQueries(2):
            summaries, totals = budget_summaries(self.ctx, self.project)
        self.assertEqual(
            [s.budget.name for s in summaries], ["Adiciones", "Contrato de obra", "Interventoría"]
        )
        main = summaries[1]
        self.assertEqual(
            (main.contractual_total, main.current_total, main.current_number),
            (D("11042735.00"), D("10510555.00"), 2),
        )
        self.assertEqual((main.diff, main.diff_pct), (D("-532180.00"), D("-4.82")))
        empty = summaries[0]
        self.assertEqual(
            (empty.contractual_total, empty.current_total, empty.diff, empty.diff_pct),
            (None, None, None, None),
        )
        self.assertEqual(
            (totals.contractual, totals.current, totals.diff, totals.diff_pct),
            (D("20172735.00"), D("19640555.00"), D("-532180.00"), D("-2.64")),
        )

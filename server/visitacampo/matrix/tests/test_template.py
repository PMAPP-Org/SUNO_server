from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.matrix.template import (
    ItemRef,
    ParsedPlan,
    TaskRef,
    TemplateRejected,
    TemplateRow,
    build_template,
    header,
    parse_plan,
    read_template,
)

T3, T5, T4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
I11, I21, CH1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TASKS = {
    3: TaskRef(T3, "1.2 Descapote", is_summary=False, retired=False),
    5: TaskRef(T5, "2.1 Columnas", is_summary=False, retired=False),
    4: TaskRef(T4, "2 Estructura", is_summary=True, retired=False),
}
ITEMS = {
    ("contrato de obra", "1.1"): ItemRef(I11, "item"),
    ("contrato de obra", "2.1"): ItemRef(I21, "item"),
    ("contrato de obra", "1"): ItemRef(CH1, "chapter"),
}
HEADER = header(["Tramo", "PR"])


def parse(
    rows: Sequence[Sequence[object]], *, editable: bool = True, budgets: int = 1
) -> ParsedPlan:
    names = ["Contrato de obra"] + ["Otro"] * (budgets - 1)
    data = build_xlsx(rows, header=HEADER, sheet="Matriz")
    return parse_plan(
        read_template(data),
        level_names=["Tramo", "PR"],
        editable_locations=editable,
        tasks=TASKS,
        items=ITEMS,
        budget_names=names,
    )


class TemplateTests(SimpleTestCase):
    def test_round_trip(self) -> None:
        data = build_template(
            ["Tramo", "PR"],
            [
                TemplateRow(
                    3,
                    "1.2",
                    "Descapote",
                    ("Tramo 1", ""),
                    "Contrato de obra",
                    "1.1",
                    "Localización",
                    "m2",
                    Decimal("12.5"),
                ),
                TemplateRow(5, "2.1", "Columnas", ("Tramo 2", "PR 1"), "", "", "", "", None),
            ],
        )
        rows = read_template(data)
        self.assertEqual(list(rows[0][:10]), HEADER)
        parsed = parse_plan(
            rows,
            level_names=["Tramo", "PR"],
            editable_locations=True,
            tasks=TASKS,
            items=ITEMS,
            budget_names=["Contrato de obra"],
        )
        self.assertEqual(parsed.pairs, {(T3, I11): Decimal("12.5000")})
        self.assertEqual(parsed.locations, {T3: ("Tramo 1",), T5: ("Tramo 2", "PR 1")})

    def test_columns_found_by_header_text(self) -> None:
        reordered = ["cantidad planeada", "CÓDIGO DE ÍTEM", "uniqueid"]
        data = build_xlsx([[3, "1.1", 3]], header=reordered, sheet="Matriz")
        parsed = parse_plan(
            read_template(data),
            level_names=["Tramo", "PR"],
            editable_locations=False,
            tasks=TASKS,
            items=ITEMS,
            budget_names=["Contrato de obra"],
        )
        self.assertEqual(parsed.pairs, {(T3, I11): Decimal("3.0000")})
        self.assertEqual(parsed.locations, {})

    def test_missing_required_column(self) -> None:
        data = build_xlsx([[3]], header=["UniqueID"], sheet="Matriz")
        with self.assertRaisesMessage(TemplateRejected, "Código de ítem"):
            parse_plan(
                read_template(data),
                level_names=[],
                editable_locations=False,
                tasks=TASKS,
                items=ITEMS,
                budget_names=["Contrato de obra"],
            )

    def test_row_errors_are_listed(self) -> None:
        rows: list[list[object]] = [
            [None, "", "", "", "", "", "1.1", "", "", 1],
            [99, "", "", "", "", "", "1.1", "", "", 1],
            [4, "", "", "", "", "", "1.1", "", "", 1],
            [3, "", "", "", "", "", "9.9", "", "", 1],
            [3, "", "", "", "", "", "1", "", "", 1],
            [3, "", "", "", "", "", "1.1", "", "", "abc"],
            [3, "", "", "", "", "", "1.1", "", "", -2],
            [5, "", "", "", "", "", "", "", "", 3],
            [5, "", "", "", "PR 1", "", "2.1", "", "", 1],
        ]
        with self.assertRaises(TemplateRejected) as caught:
            parse(rows)
        errors = caught.exception.errors
        self.assertEqual(len(errors), 9)
        self.assertIn("Fila 2: falta el UniqueID", errors[0])
        self.assertIn("Fila 3: la tarea con UniqueID 99 no existe", errors[1])
        self.assertIn("Fila 4: 2 Estructura es una tarea resumen", errors[2])
        self.assertIn("Fila 5: el ítem 9.9 no existe", errors[3])
        self.assertIn("Fila 6: 1 es un capítulo", errors[4])
        self.assertIn("Fila 7: la cantidad «abc» no es un número", errors[5])
        self.assertIn("Fila 8: la cantidad no puede ser negativa", errors[6])
        self.assertIn("Fila 9: hay cantidad pero falta el código del ítem", errors[7])
        self.assertIn("Fila 10: la ubicación tiene un nivel vacío", errors[8])

    def test_duplicates_and_conflicting_locations(self) -> None:
        rows: list[list[object]] = [
            [3, "", "", "Tramo 1", "", "", "1.1", "", "", 1],
            [3, "", "", "Tramo 2", "", "", "2.1", "", "", 1],
            [3, "", "", "tramo 1", "", "", "1.1", "", "", 2],
        ]
        with self.assertRaises(TemplateRejected) as caught:
            parse(rows)
        self.assertIn("tiene otra ubicación en una fila anterior", caught.exception.errors[0])
        self.assertIn("el par 1.2 Descapote × 1.1 está repetido", caught.exception.errors[1])

    def test_item_without_quantity_is_dropped_with_warning(self) -> None:
        parsed = parse([[3, "", "", "", "", "", "1.1", "", "", None]])
        self.assertEqual(parsed.pairs, {})
        self.assertIn("1 fila(s) con ítem y sin cantidad", parsed.warnings[0])

    def test_dot_thousands_warning_and_colombian_numbers(self) -> None:
        parsed = parse(
            [
                [3, "", "", "", "", "", "1.1", "", "", "1.234"],
                [5, "", "", "", "", "", "2.1", "", "", "2,5"],
            ]
        )
        self.assertEqual(parsed.pairs[(T3, I11)], Decimal("1234.0000"))
        self.assertEqual(parsed.pairs[(T5, I21)], Decimal("2.5000"))
        self.assertTrue(any("punto" in w for w in parsed.warnings))

    def test_budget_required_with_several_budgets(self) -> None:
        with self.assertRaises(TemplateRejected) as caught:
            parse([[3, "", "", "", "", "", "1.1", "", "", 1]], budgets=2)
        self.assertIn("falta el presupuesto", caught.exception.errors[0])

    def test_error_list_is_capped(self) -> None:
        rows = [[99, "", "", "", "", "", "1.1", "", "", 1]] * 80
        with self.assertRaises(TemplateRejected) as caught:
            parse(rows)
        self.assertEqual(len(caught.exception.errors), 51)
        self.assertEqual(caught.exception.errors[-1], "… y 30 error(es) más.")


class ReviewFixTests(SimpleTestCase):
    def test_formula_escape_is_undone_on_read(self) -> None:
        item = uuid.uuid4()
        data = build_template(
            ["Tramo", "PR"],
            [
                TemplateRow(
                    3, "1.2", "Descapote", ("-1", ""), "+Obra", "1.1", "x", "m2", Decimal("2")
                )
            ],
        )
        parsed = parse_plan(
            read_template(data),
            level_names=["Tramo", "PR"],
            editable_locations=True,
            tasks=TASKS,
            items={("+obra", "1.1"): ItemRef(item, "item")},
            budget_names=["+Obra"],
        )
        self.assertEqual(parsed.pairs, {(T3, item): Decimal("2.0000")})
        self.assertEqual(parsed.locations, {T3: ("-1",)})

    def test_suno_mode_without_level_columns_ignores_locations(self) -> None:
        data = build_xlsx(
            [[3, "1.1", 3]], header=["UniqueID", "Código de ítem", "Cantidad planeada"]
        )
        parsed = parse_plan(
            read_template(data),
            level_names=["Tramo", "PR"],
            editable_locations=True,
            tasks=TASKS,
            items=ITEMS,
            budget_names=["Contrato de obra"],
        )
        self.assertEqual(parsed.locations, {})
        self.assertEqual(len(parsed.pairs), 1)

    def test_suno_mode_with_some_level_columns_is_rejected(self) -> None:
        data = build_xlsx(
            [[3, "Tramo 1", "1.1", 3]],
            header=["UniqueID", "Tramo", "Código de ítem", "Cantidad planeada"],
        )
        with self.assertRaisesMessage(TemplateRejected, "Falta la columna «PR» en la fila 1."):
            parse_plan(
                read_template(data),
                level_names=["Tramo", "PR"],
                editable_locations=True,
                tasks=TASKS,
                items=ITEMS,
                budget_names=["Contrato de obra"],
            )

    def test_quantity_rounding_up_to_the_limit_is_rejected(self) -> None:
        with self.assertRaises(TemplateRejected) as caught:
            parse([[3, "", "", "", "", "", "1.1", "", "", "99999999999999.99999"]])
        self.assertIn("demasiado grande", caught.exception.errors[0])

    def test_retired_items_only_where_already_planned(self) -> None:
        gone = uuid.uuid4()
        items = {**ITEMS, ("contrato de obra", "1.3"): ItemRef(gone, "retired")}

        def run(rows: list[list[object]], current: set[tuple[uuid.UUID, uuid.UUID]]) -> ParsedPlan:
            data = build_xlsx(rows, header=HEADER, sheet="Matriz")
            return parse_plan(
                read_template(data),
                level_names=["Tramo", "PR"],
                editable_locations=True,
                tasks=TASKS,
                items=items,
                budget_names=["Contrato de obra"],
                current_pairs=current,
            )

        row = [3, "", "", "", "", "Contrato de obra", "1.3", "", "", 4]
        self.assertEqual(run([row], {(T3, gone)}).pairs, {(T3, gone): Decimal("4.0000")})
        with self.assertRaises(TemplateRejected) as caught:
            run([row], set())
        self.assertIn("fue retirado del presupuesto vigente", caught.exception.errors[0])
        removed = run([[*row[:9], None]], set())
        self.assertEqual(removed.pairs, {})
        self.assertIn("sin cantidad", removed.warnings[0])

    def test_ambiguous_budget_name(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        items = {
            ("edificación", "1.1"): ItemRef(a, "item"),
            ("edificacion", "1.1"): ItemRef(b, "item"),
        }

        def run(name: str) -> ParsedPlan:
            data = build_xlsx(
                [[3, "", "", "", "", name, "1.1", "", "", 1]], header=HEADER, sheet="Matriz"
            )
            return parse_plan(
                read_template(data),
                level_names=["Tramo", "PR"],
                editable_locations=True,
                tasks=TASKS,
                items=items,
                budget_names=["Edificación", "Edificacion"],
            )

        self.assertEqual(list(run("EDIFICACIÓN").pairs), [(T3, a)])
        with self.assertRaises(TemplateRejected) as caught:
            run("Édificacion")
        self.assertIn("es ambiguo", caught.exception.errors[0])

    def test_numeric_code_hint(self) -> None:
        with self.assertRaises(TemplateRejected) as caught:
            parse([[3, "", "", "", "", "", 9.9, "", "", 1]])
        self.assertIn("formato de texto", caught.exception.errors[0])

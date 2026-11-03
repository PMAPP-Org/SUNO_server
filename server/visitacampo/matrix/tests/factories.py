"""Datos de prueba de la matriz. Los tests sí pueden usar `Model.objects`."""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from visitacampo.budgets.tests.factories import apply_rows, make_budget
from visitacampo.budgets.tests.samples import V1_ROWS
from visitacampo.core.models import Budget, BudgetItem, QuantityPlan, Task
from visitacampo.matrix.locations import LevelSpec, load_tree, project_levels, save_levels
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.factories import FieldWorld, make_field_world
from visitacampo.projects.tests.samples import TEXT1, TEXT2, project_xml, task_xml


def located_xml(*, tramo_3: str = "Tramo 1", tramo_5: str = "Tramo 2") -> bytes:
    """El cronograma de `samples.v1()` reducido, con Tramo (Text1) y PR (Text2) en las hojas:
    1 Preliminares > 1.1 Localización (uid 2, sin ubicación), 1.2 Descapote (uid 3);
    2 Estructura (uid 4) > 2.1 Columnas (uid 5)."""
    base = ("2026-03-02", "2026-03-13", 80)
    return project_xml(
        [
            task_xml(1, "Preliminares", "1", summary=True, baseline=base),
            task_xml(2, "Localización", "1.1", baseline=base),
            task_xml(3, "Descapote", "1.2", baseline=base, custom=((TEXT1, tramo_3),)),
            task_xml(4, "Estructura", "2", summary=True, baseline=base),
            task_xml(
                5,
                "Columnas",
                "2.1",
                baseline=base,
                custom=((TEXT1, tramo_5), (TEXT2, "PR 12+300")),
            ),
        ],
        custom_fields=((TEXT1, "Text1", "Tramo"), (TEXT2, "Text2", "PR")),
    )


def project_levels_from_xml(world: FieldWorld) -> None:
    save_levels(world.ctx(), world.project, [LevelSpec("Tramo", TEXT1), LevelSpec("PR", TEXT2)])


def suno_levels(world: FieldWorld) -> None:
    save_levels(world.ctx(), world.project, [LevelSpec("Tramo"), LevelSpec("PR")])


def locate(world: FieldWorld, uid: int, *names: str) -> Task:
    """Pone la tarea `uid` en la ruta `names` (creando los nodos)."""
    ctx = world.ctx()
    node, _ = load_tree(ctx, world.project).ensure(ctx, project_levels(ctx, world.project), names)
    task = world.task(uid)
    task.location = node
    task.save(update_fields=["location"])
    return task


def import_schedule(world: FieldWorld, data: bytes) -> None:
    ctx = world.ctx()
    apply_import(ctx, stage_import(ctx, world.project, file_name="c.xml", data=data).pk)


def budget_world(name: str = "Constructora") -> tuple[FieldWorld, Budget]:
    """`make_field_world` con el presupuesto `samples.V1_ROWS` aplicado."""
    world = make_field_world(name)
    budget = make_budget(world.project, world.admin)
    apply_rows(world.ctx(), budget, V1_ROWS)
    return world, budget


def item(budget: Budget, code: str) -> BudgetItem:
    return BudgetItem.objects.get(budget=budget, code=code)


def plan(world: FieldWorld, uid: int, budget_item: BudgetItem, quantity: str) -> QuantityPlan:
    return QuantityPlan.objects.create(
        org=world.org,
        project=world.project,
        task=world.task(uid),
        item=budget_item,
        planned_quantity=Decimal(quantity),
        updated_by=world.admin,
        updated_at=timezone.now(),
    )


def quantity_row(
    budget_item: BudgetItem, quantity: str, *, location: object = None, note: str = ""
) -> dict[str, object]:
    """Una fila de `Visit.quantities` en el formato guardado."""
    return {
        "item_id": str(budget_item.pk),
        "location_id": None if location is None else str(location),
        "quantity": str(Decimal(quantity).quantize(Decimal("0.0001"))),
        "note": note,
    }

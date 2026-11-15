"""Datos de prueba de materiales. Las pruebas sí pueden usar `Model.objects`.

Sobre `contracts.tests.factories.contract_world`: presupuesto `samples.V1_ROWS` con AIU (ítems
1.1, 1.2, 2.1, 2.2 y 2.4; capítulos 1 y 2) y cronograma `samples.v1()` (tareas hoja uid 2
"1.1 Localización", 3 "1.2 Descapote" y 5 "2.1 Columnas"). La administración es Gestora de
Costos (señal), la supervisión Revisora; hay un Colaborador y un Consulta.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from suno.contracts.tests.factories import ContractWorld, contract_world
from suno.core.context import RequestContext
from suno.core.models import (
    AppUser,
    ConsumptionCut,
    Material,
    MaterialIssue,
    MaterialPurchase,
    Organization,
    Project,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.costs.tests.factories import support_blob
from suno.projects.tests.factories import FieldWorld

D = Decimal
#: Fecha de las compras de ejemplo (las pruebas fijan "hoy" en abril de 2026).
BOUGHT = date(2026, 3, 2)


@dataclass
class MaterialWorld:
    cw: ContractWorld
    warehouse: Warehouse
    cement: Material
    sand: Material
    steel: Material

    @property
    def world(self) -> FieldWorld:
        return self.cw.world

    @property
    def project(self) -> Project:
        return self.cw.world.project

    def ctx(self, user: AppUser | None = None) -> RequestContext:
        return self.cw.world.ctx(user)


def make_material(
    org: Organization, code: str, name: str, unit: str, category: str = ""
) -> Material:
    return Material.objects.create(org=org, code=code, name=name, unit=unit, category=category)


def make_warehouse(world: FieldWorld, name: str, project: Project | None = None) -> Warehouse:
    return Warehouse.objects.create(
        org=world.org,
        project=project or world.project,
        name=name,
        created_by=world.admin,
        updated_by=world.admin,
    )


def material_world(name: str = "Constructora") -> MaterialWorld:
    """`contract_world` con una bodega ("Almacén principal") y tres materiales del catálogo:
    cemento (und), arena (m3) y varilla (kg)."""
    cw = contract_world(name)
    org = cw.world.org
    return MaterialWorld(
        cw=cw,
        warehouse=make_warehouse(cw.world, "Almacén principal"),
        cement=make_material(org, "CEM-01", "Cemento gris 50 kg", "und", "Cementos"),
        sand=make_material(org, "ARE-01", "Arena de río", "m3", "Agregados"),
        steel=make_material(org, "ACE-12", "Varilla de 1/2 pulgada", "kg", "Aceros"),
    )


def draft_purchase(mw: MaterialWorld, **fields: Any) -> MaterialPurchase:
    """Una compra a bodega escrita directo en la BD (sin líneas ni servicio)."""
    world = mw.world
    values: dict[str, Any] = {
        "org": world.org,
        "project": mw.project,
        "number": 1,
        "kind": "warehouse",
        "warehouse": mw.warehouse,
        "supplier": "Ferretería El Constructor",
        "document_type": "invoice",
        "purchased_on": BOUGHT,
        "created_by": world.admin,
        "updated_by": world.admin,
    }
    values.update(fields)
    return MaterialPurchase.objects.create(**values)


def bare_issue(
    mw: MaterialWorld, *, number: int = 1, day: date = date(2026, 3, 10), **fields: Any
) -> MaterialIssue:
    """Una salida indirecta escrita directo en la BD (sin líneas ni movimientos)."""
    world = mw.world
    values: dict[str, Any] = {
        "org": world.org,
        "project": mw.project,
        "number": number,
        "warehouse": mw.warehouse,
        "issued_on": day,
        "scope": "indirect",
        "created_by": world.admin,
        "updated_by": world.admin,
    }
    values.update(fields)
    return MaterialIssue.objects.create(**values)


def bare_cut(
    mw: MaterialWorld,
    *,
    number: int = 1,
    status: str = "draft",
    start: date = date(2026, 3, 1),
    end: date = date(2026, 3, 31),
) -> ConsumptionCut:
    """Un corte escrito directo en la BD. Aprobado o anulado: con soporte y por la supervisión."""
    world = mw.world
    decided = status != "draft"
    return ConsumptionCut.objects.create(
        org=world.org,
        project=mw.project,
        number=number,
        period_start=start,
        period_end=end,
        status=status,
        created_by=world.admin,
        updated_by=world.admin,
        decided_by=world.supervisor if decided else None,
        decided_at=timezone.now() if decided else None,
        decision_note="Faltan soportes" if status == "rejected" else "",
        voided_by=world.admin if status == "voided" else None,
        voided_at=timezone.now() if status == "voided" else None,
        void_note="Anulado en la prueba" if status == "voided" else "",
        attachment=support_blob("reporte firmado") if decided else None,
        attachment_name="reporte-firmado.pdf" if decided else "",
    )


def balance_of(
    mw: MaterialWorld, material: Material, warehouse: Warehouse | None = None
) -> tuple[Decimal, Decimal]:
    """(cantidad, valor) del saldo; (0, 0) si el par no tiene saldo."""
    row = StockBalance.objects.filter(
        warehouse=warehouse or mw.warehouse, material=material
    ).first()
    return (row.quantity, row.value) if row is not None else (D("0"), D("0.00"))


def assert_consistent(test: unittest.TestCase, mw: MaterialWorld) -> None:
    """Cada saldo del proyecto es la suma de los movimientos de su par (spec 1.3)."""
    for row in StockBalance.objects.filter(project=mw.project):
        moves = StockMovement.objects.filter(warehouse=row.warehouse, material=row.material)
        sums = moves.aggregate(q=Sum("quantity"), v=Sum("value"))
        test.assertEqual((row.quantity, row.value), (sums["q"] or D("0"), sums["v"] or D("0.00")))
        last = moves.order_by("-sequence").values_list("sequence", flat=True).first()
        test.assertGreaterEqual(row.last_sequence, last or 0)

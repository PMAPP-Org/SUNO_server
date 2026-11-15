"""Datos de prueba de contratos y actas. Las pruebas sí pueden usar `Model.objects`.

El presupuesto es `samples.V1_ROWS` con AIU (ver `costs.tests.factories.cost_world`): ítems 1.1,
1.2, 2.1, 2.2 y 2.4. El cronograma es `samples.v1()`: tareas hoja 1.1 Localización (uid 2),
1.2 Descapote (uid 3) y 2.1 Columnas (uid 5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from django.utils import timezone

from suno.authn.tests.factories import make_user
from suno.budgets.tests.samples import AIU
from suno.budgets.totals import (
    BASE_DIRECT,
    ChargeSpec,
    compute_charges,
    item_total,
    line_base,
    money,
)
from suno.core.access import SectionRole
from suno.core.models import (
    AppUser,
    Blob,
    Budget,
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractCertificate,
    ContractCharge,
    ContractDeduction,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Project,
    Record,
    Role,
)
from suno.costs.tests.factories import cost_world, grant_costs, item, support_blob
from suno.projects.tests.factories import FieldWorld, make_record

D = Decimal
#: Retenciones de ejemplo: 2 % y 0,966 % sobre el directo; ReteIVA 15 % del IVA (cargo 4 del AIU).
WITHHOLDINGS = [
    ChargeSpec(1, "Retención en la fuente", D("2"), BASE_DIRECT),
    ChargeSpec(2, "ReteICA", D("0.966"), BASE_DIRECT),
    ChargeSpec(3, "ReteIVA", D("15"), line_base(4)),
]
#: (código del ítem, cantidad, precio unitario, uids de las tareas; vacío = todas).
LineSpec = tuple[str, str, str, tuple[int, ...]]
#: 2.1: 10 m3 × 400.000 = 4.000.000; 2.2: 800 kg × 3.500 = 2.800.000. Directo 6.800.000; con
#: AIU (10/5/5, IVA 19 % sobre la utilidad) los cargos son 1.424.600 y el valor 8.224.600.
LINES: tuple[LineSpec, ...] = (("2.1", "10", "400000.00", (5,)), ("2.2", "800", "3500.00", ()))


@dataclass
class ContractWorld:
    world: FieldWorld
    budget: Budget
    #: Colaborador de Costos (solo lo suyo) y Consulta.
    contributor: AppUser
    viewer: AppUser

    @property
    def project(self) -> Project:
        return self.world.project


def contract_world(name: str = "Constructora") -> ContractWorld:
    """`cost_world` con permisos: la administración es Gestora de Costos (señal), la supervisión
    Revisora, más un Colaborador y un Consulta."""
    world, budget = cost_world(name)
    slug = name.lower().replace(" ", "-")
    grant_costs(world, world.supervisor, SectionRole.REVIEWER)
    contributor = make_user(world.org, username=f"colaborador-{slug}", role=Role.VIEWER)
    grant_costs(world, contributor, SectionRole.CONTRIBUTOR)
    viewer = make_user(world.org, username=f"consulta-{slug}", role=Role.VIEWER)
    grant_costs(world, viewer, SectionRole.VIEWER)
    return ContractWorld(world, budget, contributor, viewer)


def make_contract(
    cw: ContractWorld,
    *,
    number: str = "SC-001",
    kind: Any = ContractKind.SUBCONTRACT,
    status: Any = ContractStatus.APPROVED,
    lines: Sequence[LineSpec] = LINES,
    charges: Sequence[ChargeSpec] = AIU,
    deductions: Sequence[ChargeSpec] = WITHHOLDINGS,
    advance_pct: str = "30",
    retention_pct: str = "5",
    starts_on: date = date(2026, 3, 1),
    ends_on: date = date(2026, 8, 31),
    created_by: AppUser | None = None,
) -> Contract:
    """Un contrato escrito directo en la BD (sin el servicio), con su valor vigente calculado.
    Aprobado: por la supervisión, con el anticipo fijado."""
    world = cw.world
    author = created_by or world.admin
    direct = sum((item_total(D(q), D(p)) for _, q, p, _ in lines), D("0.00"))
    value = compute_charges(direct, list(charges)).total
    approved = status != ContractStatus.DRAFT
    contract = Contract.objects.create(
        org=world.org,
        project=world.project,
        kind=kind,
        number=number,
        purpose="Estructura en concreto",
        contractor="Estructuras del Norte SAS",
        starts_on=starts_on,
        ends_on=ends_on,
        status=status,
        advance_pct=D(advance_pct),
        advance_amount=money(value * D(advance_pct) / 100) if approved else D("0.00"),
        retention_pct=D(retention_pct),
        current_value=value,
        created_by=author,
        updated_by=author,
        approved_by=world.supervisor if approved else None,
        approved_at=timezone.now() if approved else None,
        closed_by=world.admin
        if status in (ContractStatus.SETTLED, ContractStatus.VOIDED)
        else None,
        closed_at=timezone.now()
        if status in (ContractStatus.SETTLED, ContractStatus.VOIDED)
        else None,
        close_note="Anulado en la prueba" if status == ContractStatus.VOIDED else "",
    )
    for order, (code, quantity, price, uids) in enumerate(lines, start=1):
        line = ContractLine.objects.create(
            org=world.org,
            contract=contract,
            order=order,
            item=item(cw.budget, code),
            unit="m3",
            quantity=D(quantity),
            current_quantity=D(quantity),
            unit_price=D(price),
        )
        for uid in uids:
            ContractLineTask.objects.create(org=world.org, line=line, task=world.task(uid))
    for model, specs in ((ContractCharge, charges), (ContractDeduction, deductions)):
        for spec in specs:
            model.objects.create(
                org=world.org,
                contract=contract,
                order=spec.order,
                name=spec.name,
                pct=spec.pct,
                base=spec.base,
            )
    return contract


def contract_line(contract: Contract, code: str) -> ContractLine:
    return ContractLine.objects.get(contract=contract, item__code=code)


def make_certificate(
    cw: ContractWorld,
    contract: Contract,
    *,
    number: int = 1,
    start: date = date(2026, 3, 1),
    end: date = date(2026, 3, 31),
    quantities: Mapping[str, str] | None = None,
    status: Any = CertificateStatus.APPROVED,
    settlement: bool = False,
    totals: Mapping[str, str] | None = None,
    attachment: Blob | None = None,
) -> ContractCertificate:
    """Un acta escrita directo en la BD: una línea por línea del contrato (`quantities` por
    código; las que falten, en 0) y los totales que se pasen (el resto en 0; el bruto directo
    y el bruto, la suma de las líneas). Aprobada: con soporte y por la supervisión."""
    world = cw.world
    decided = status != CertificateStatus.DRAFT
    lines = list(contract.lines.select_related("item").order_by("order"))
    amounts = {
        line.pk: item_total(D((quantities or {}).get(line.item.code, "0")), line.unit_price)
        for line in lines
    }
    direct = sum(amounts.values(), D("0.00"))
    values = {"direct": direct, "gross": direct}
    values.update({key: D(value) for key, value in (totals or {}).items()})
    certificate = ContractCertificate.objects.create(
        org=world.org,
        contract=contract,
        project=contract.project,
        number=number,
        period_start=start,
        period_end=end,
        is_settlement=settlement,
        status=status,
        created_by=world.admin,
        updated_by=world.admin,
        decided_by=world.supervisor if decided else None,
        decided_at=timezone.now() if decided else None,
        decision_note="Cantidades sin soporte" if status == CertificateStatus.REJECTED else "",
        voided_by=world.admin if status == CertificateStatus.VOIDED else None,
        voided_at=timezone.now() if status == CertificateStatus.VOIDED else None,
        void_note="Anulada en la prueba" if status == CertificateStatus.VOIDED else "",
        attachment=attachment or (support_blob("acta firmada") if decided else None),
        attachment_name="acta-firmada.pdf" if decided else "",
        **values,
    )
    for line in lines:
        quantity = D((quantities or {}).get(line.item.code, "0"))
        CertificateLine.objects.create(
            org=world.org,
            certificate=certificate,
            line=line,
            proposed_quantity=quantity,
            quantity=quantity,
            amount=amounts[line.pk],
        )
    return certificate


def field_quantity(
    cw: ContractWorld,
    uid: int,
    code: str,
    quantity: str,
    day: date,
    *,
    validated: bool = True,
) -> Record:
    """Un registro de campo con una cantidad del ítem `code` en la tarea `uid`, capturado al
    mediodía de `day` en Colombia; validado salvo que se diga lo contrario."""
    world = cw.world
    record = make_record(
        world.org,
        world.agent,
        task=world.task(uid),
        at=timezone.make_aware(datetime.combine(day, time(12))),
        quantities=[
            {
                "item_id": str(item(cw.budget, code).pk),
                "location_id": None,
                "quantity": f"{D(quantity):.4f}",
                "note": "",
            }
        ],
    )
    if validated:
        world.approve(record)
    return record

"""Datos de prueba de mano de obra. Las pruebas sí pueden usar `Model.objects`.

Sobre `contracts.tests.factories.contract_world`: presupuesto `samples.V1_ROWS` con AIU (ítems
1.1, 1.2, 2.1, 2.2 y 2.4; capítulos 1 y 2) y cronograma `samples.v1()` (tareas hoja uid 2
"1.1 Localización", 3 "1.2 Descapote" y 5 "2.1 Columnas"). La administración es Gestora de
Costos y de Mano de obra (señal); en Mano de obra la supervisión es Revisora y hay un
Colaborador y un Consulta. `accountant` solo tiene Costos (Consulta): no ve Mano de obra.

Factor 1,5200 desde 2026-01-01. Pedro Pérez (CC 1012345678, jornal 80.000,00) y María Gómez
(CC 52123456, jornal 100.000,00), asignados al proyecto desde el 2026-01-05: un jornal de
Pedro cuesta 121.600,00 y medio de María 76.000,00.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.utils import timezone

from suno.authn.tests.factories import make_user
from suno.backoffice.export import to_xlsx_book
from suno.contracts.tests.factories import ContractWorld, contract_world
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import RequestContext
from suno.core.models import (
    AppUser,
    BenefitFactor,
    CostScope,
    Organization,
    Project,
    ProjectWorker,
    Role,
    Timesheet,
    TimesheetLine,
    Worker,
    WorkerWage,
)
from suno.costs.tests.factories import grant_costs, support_blob
from suno.projects.tests.factories import FieldWorld

D = Decimal
TODAY = date(2026, 4, 15)
HIRED = date(2026, 1, 5)
#: La semana de las planillas de ejemplo (lunes 2 a domingo 8 de marzo de 2026).
WEEK = (date(2026, 3, 2), date(2026, 3, 8))


@dataclass
class LaborWorld:
    cw: ContractWorld
    pedro: Worker
    maria: Worker
    #: Costos (Consulta) sin Mano de obra.
    accountant: AppUser

    @property
    def world(self) -> FieldWorld:
        return self.cw.world

    @property
    def project(self) -> Project:
        return self.cw.world.project

    @property
    def contributor(self) -> AppUser:
        return self.cw.contributor

    @property
    def viewer(self) -> AppUser:
        return self.cw.viewer

    def ctx(self, user: AppUser | None = None) -> RequestContext:
        return self.cw.world.ctx(user)


def grant_labor(
    world: FieldWorld,
    user: AppUser,
    role: Any,
    *,
    scope: Any = AccessScope.ORGANIZATION,
    projects: Sequence[Project] = (),
) -> None:
    set_section_grant(
        world.ctx(),
        user_id=user.pk,
        section=Section.LABOR,
        role=role,
        scope=scope,
        project_ids=[p.pk for p in projects],
    )


def make_worker(
    org: Organization,
    number: str,
    name: str,
    *,
    wage: str = "80000.00",
    hired: date = HIRED,
    position: str = "Oficial",
    document_type: str = "CC",
) -> Worker:
    worker = Worker.objects.create(
        org=org,
        document_type=document_type,
        document_number=number,
        full_name=name,
        position=position,
        hired_on=hired,
    )
    WorkerWage.objects.create(org=org, worker=worker, valid_from=hired, daily_wage=D(wage))
    return worker


def assign(
    lw: LaborWorld, worker: Worker, start: date = HIRED, end: date | None = None
) -> ProjectWorker:
    world = lw.world
    return ProjectWorker.objects.create(
        org=world.org,
        project=lw.project,
        worker=worker,
        starts_on=start,
        ends_on=end,
        created_by=world.admin,
        updated_by=world.admin,
    )


def labor_world(name: str = "Constructora") -> LaborWorld:
    cw = contract_world(name)
    world = cw.world
    grant_labor(world, world.supervisor, SectionRole.REVIEWER)
    grant_labor(world, cw.contributor, SectionRole.CONTRIBUTOR)
    grant_labor(world, cw.viewer, SectionRole.VIEWER)
    slug = name.lower().replace(" ", "-")
    accountant = make_user(world.org, username=f"contador-{slug}", role=Role.VIEWER)
    grant_costs(world, accountant, SectionRole.VIEWER)
    BenefitFactor.objects.create(
        org=world.org, valid_from=date(2026, 1, 1), factor=D("1.5200"), note="Prestaciones"
    )
    lw = LaborWorld(
        cw=cw,
        pedro=make_worker(world.org, "1012345678", "Pedro Pérez"),
        maria=make_worker(
            world.org, "52123456", "María Gómez", wage="100000.00", position="Ayudante"
        ),  # fmt: skip
        accountant=accountant,
    )
    assign(lw, lw.pedro)
    assign(lw, lw.maria)
    return lw


def bare_timesheet(
    lw: LaborWorld,
    *,
    number: int = 1,
    start: date = WEEK[0],
    end: date = WEEK[1],
    status: str = "draft",
    project: Project | None = None,
    created_by: AppUser | None = None,
) -> Timesheet:
    """Una planilla escrita directo en la BD. Aprobada, rechazada o anulada: decidida por la
    supervisión y, si está aprobada o anulada, con el soporte firmado."""
    world = lw.world
    author = created_by or world.admin
    decided = status != "draft"
    signed = status in ("approved", "reversed")
    return Timesheet.objects.create(
        org=world.org,
        project=project or lw.project,
        number=number,
        starts_on=start,
        ends_on=end,
        status=status,
        created_by=author,
        updated_by=author,
        decided_by=world.supervisor if decided else None,
        decided_at=timezone.now() if decided else None,
        decision_note="Faltan firmas" if status == "rejected" else "",
        reversed_by=world.admin if status == "reversed" else None,
        reversed_at=timezone.now() if status == "reversed" else None,
        reverse_note="Anulada en la prueba" if status == "reversed" else "",
        attachment=support_blob("planilla firmada") if signed else None,
        attachment_name="planilla-firmada.pdf" if signed else "",
    )


def bare_line(
    timesheet: Timesheet,
    worker: Worker,
    day: date,
    fraction: str = "1",
    *,
    frozen: tuple[str, str, str] | None = None,
) -> TimesheetLine:
    """Una línea indirecta escrita directo en la BD; `frozen` = (jornal, factor, valor)."""
    wage, factor, amount = frozen if frozen else (None, None, None)
    return TimesheetLine.objects.create(
        org=timesheet.org,
        timesheet=timesheet,
        worker=worker,
        worked_on=day,
        fraction=D(fraction),
        scope=CostScope.INDIRECT,
        destination_key="indirecto",
        daily_wage=D(wage) if wage else None,
        factor=D(factor) if factor else None,
        amount=D(amount) if amount else None,
    )


def xlsx(sheet: str, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """Un libro de una hoja, con el escritor del repositorio (las celdas de texto que empiezan
    con = + - @ salen con apóstrofo, como en un exporte real)."""
    return to_xlsx_book([(sheet, list(header), [list(row) for row in rows])])

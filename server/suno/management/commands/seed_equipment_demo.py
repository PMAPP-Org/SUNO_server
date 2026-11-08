"""`python manage.py seed_equipment_demo` — datos de ejemplo para el panel de costos de equipos.

Solo para desarrollo y demos: genera equipos, operadores, tarifas por hora, un
presupuesto con partidas y ~14 semanas de registros de uso (completados, en curso,
programados y cancelados), de modo que el tablero de seguimiento tenga qué mostrar.

Usa una organización **existente** (`--org-name`, por defecto «SUNO Equipos Local»), su
primer proyecto y a su administrador. Es aprovisionamiento offline: escribe sin
`RequestContext` y por eso figura en `ALLOWED` de `test_architecture.py`.

Es determinista (semilla fija) y seguro de repetir: si la organización ya tiene datos de
ejemplo (partida `DEMO-` o más de 5 usos) se detiene, salvo con `--reset`, que borra solo
los usos, tarifas y el presupuesto de ejemplo antes de volver a sembrar.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AppUser,
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Equipment,
    EquipmentMeterUnit,
    EquipmentOperatorAssignment,
    EquipmentProject,
    EquipmentRate,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Membership,
    Organization,
    Project,
    Role,
)
from suno.equipment.costing import usage_cost, usage_hours

BUDGET_NAME = "Presupuesto de obra (datos de ejemplo)"
HISTORY_DAYS = 98  # 14 semanas

#: code, name, type code, type name, manufacturer, model, year, ownership
EXTRA_EQUIPMENT = [
    ("EQ-004", "Volqueta 12 m³", "VOL", "Volqueta", "Kenworth", "T800", 2019, "own"),
    ("EQ-005", "Compactador vibratorio", "COM", "Compactador", "Dynapac", "CA25", 2021, "own"),
]

#: partida: descripción, total presupuestado (se elige para ver consumos distintos).
BUDGET_ITEMS = {
    "2.1": ("Excavación mecánica en material común", Decimal("185000000.00")),
    "2.2": ("Transporte y disposición de material sobrante", Decimal("66000000.00")),
    "3.1": ("Compactación de rellenos", Decimal("26000000.00")),
}

#: code: (propiedad, operación, mantenimiento, operador) en $/h, y fecha de vigencia.
RATES: dict[str, list[tuple[int, tuple[int, int, int, int]]]] = {
    # fecha como días atrás desde hoy
    "EQ-001": [(HISTORY_DAYS, (85000, 120000, 45000, 38000)), (38, (85000, 138000, 45000, 38000))],
    "EQ-002": [(HISTORY_DAYS, (12000, 18000, 6000, 0))],
    "EQ-003": [(HISTORY_DAYS, (70000, 95000, 40000, 35000))],
    "EQ-004": [(HISTORY_DAYS, (60000, 90000, 30000, 32000))],
    # Sin tarifa hasta hace 24 días: sus usos previos quedan «sin tarifa» (demuestra el aviso).
    "EQ-005": [(24, (48000, 52000, 22000, 30000))],
}

#: probabilidad de uso por día hábil, actividad, partida y medidor.
PROFILE: dict[str, dict[str, Any]] = {
    "EQ-001": {"p": 0.86, "activity": "Excavación de zanjas", "item": "2.1", "meter": "hours"},
    "EQ-002": {
        "p": 0.62,
        "activity": "Supervisión de frentes",
        "item": None,
        "meter": "kilometers",
    },
    "EQ-003": {"p": 0.5, "activity": "Excavación de apoyo", "item": "2.1", "meter": "hours"},
    "EQ-004": {"p": 0.78, "activity": "Transporte de material", "item": "2.2", "meter": "hours"},
    "EQ-005": {"p": 0.58, "activity": "Compactación de relleno", "item": "3.1", "meter": "hours"},
}


def _weekdays(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [d for d in (start + timedelta(n) for n in range(days)) if d.weekday() < 5]


class Command(BaseCommand):
    help = (
        "Siembra datos de ejemplo (equipos, tarifas, presupuesto y usos) para el panel de costos."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--org-name", default="SUNO Equipos Local")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Borra usos, tarifas y el presupuesto de ejemplo de la organización y resiembra.",
        )
        parser.add_argument("--allow-production", action="store_true")

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "Solo para desarrollo (DEBUG=False). Usa --allow-production si lo quieres."
            )
        org = Organization.objects.filter(name=options["org_name"]).first()
        if org is None:
            raise CommandError(f"No existe la organización «{options['org_name']}».")
        project = Project.objects.filter(org=org).order_by("created_at").first()
        if project is None:
            raise CommandError("La organización no tiene proyectos.")
        admin = (
            AppUser.objects.filter(org=org, memberships__role__in=[Role.OWNER, Role.ADMIN])
            .order_by("date_joined")
            .first()
        )
        if admin is None:
            raise CommandError("La organización no tiene un administrador.")

        already = Budget.objects.filter(org=org, name=BUDGET_NAME).exists()
        if already or EquipmentUsage.objects.filter(org=org).count() > 5:
            if not options["reset"]:
                raise CommandError("Ya hay datos de ejemplo. Usa --reset para resembrarlos.")
            self._reset(org)

        today = date.today()
        start = today - timedelta(days=HISTORY_DAYS)
        rng = random.Random(20261108)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))

        equipment = self._equipment(org, admin)
        operators = self._operators(org, ctx, project, equipment, start)
        for code, entries in RATES.items():
            for days_ago, (own, ope, mnt, lab) in entries:
                valid_from = today - timedelta(days=days_ago)
                total = Decimal(own + ope + mnt + lab)
                EquipmentRate.objects.get_or_create(
                    equipment=equipment[code],
                    valid_from=valid_from,
                    defaults={
                        "org": org,
                        "ownership_rate": Decimal(own),
                        "operation_rate": Decimal(ope),
                        "maintenance_rate": Decimal(mnt),
                        "labor_rate": Decimal(lab),
                        "hourly_rate": total,
                        "notes": "Tarifa de ejemplo",
                        "created_by": admin,
                    },
                )
        items = self._budget(org, project, admin)
        created = self._usage(org, project, admin, equipment, operators, items, start, today, rng)
        self.stdout.write(
            self.style.SUCCESS(
                f"Datos de ejemplo listos en «{org.name}»: {len(equipment)} equipos, "
                f"{len(operators)} operadores, {created} registros de uso."
            )
        )

    # ------------------------------------------------------------------ pasos
    def _reset(self, org: Organization) -> None:
        EquipmentUsage.objects.filter(org=org).delete()
        EquipmentRate.objects.filter(org=org).delete()
        BudgetItemVersion.objects.filter(org=org, version__budget__name=BUDGET_NAME).delete()
        BudgetItem.objects.filter(org=org, budget__name=BUDGET_NAME).delete()
        for budget in Budget.objects.filter(org=org, name=BUDGET_NAME):
            budget.contractual_version = None
            budget.save(update_fields=["contractual_version"])
        BudgetVersion.objects.filter(org=org, budget__name=BUDGET_NAME).delete()
        Budget.objects.filter(org=org, name=BUDGET_NAME).delete()

    def _equipment(self, org: Organization, admin: AppUser) -> dict[str, Equipment]:
        found = {e.code: e for e in Equipment.objects.filter(org=org)}
        for code, name, tcode, tname, maker, model, year, ownership in EXTRA_EQUIPMENT:
            if code in found:
                continue
            etype, _ = EquipmentType.objects.get_or_create(
                org=org,
                code=tcode,
                defaults={"name": tname},
            )
            found[code] = Equipment.objects.create(
                org=org,
                code=code,
                name=name,
                equipment_type=etype,
                manufacturer=maker,
                model=model,
                manufacture_year=year,
                ownership=ownership,
                created_by=admin,
                updated_by=admin,
            )
        missing = [c for c in PROFILE if c not in found]
        if missing:
            raise CommandError(f"Faltan los equipos {', '.join(missing)} en la organización.")
        return {code: found[code] for code in PROFILE}

    def _operators(
        self,
        org: Organization,
        ctx: Any,
        project: Project,
        equipment: dict[str, Equipment],
        start: date,
    ) -> dict[str, AppUser]:
        existing = list(
            EquipmentOperatorAssignment.objects.filter(org=org).select_related("operator")
        )
        by_equipment = {a.equipment_id: a.operator for a in existing}
        pool = list(AppUser.objects.filter(org=org, username__startswith="operador."))
        names = ["operador.rios", "operador.mejia", "operador.torres", "operador.vargas"]
        result: dict[str, AppUser] = {}
        for code, item in equipment.items():
            operator = by_equipment.get(item.pk)
            if operator is None:
                username = names.pop(0) if names else f"operador.{code.lower()}"
                operator = next((u for u in pool if u.username == username), None) or (
                    AppUser.objects.create_user(
                        username=username,
                        email=f"{username}@example.com",
                        password=None,
                        org=org,
                        display_name=username.split(".")[1].capitalize(),
                    )
                )
                Membership.objects.get_or_create(
                    org=org, user=operator, defaults={"role": Role.FIELD_AGENT}
                )
                set_section_grant(
                    ctx,
                    user_id=operator.pk,
                    section=Section.EQUIPMENT,
                    role=SectionRole.EQUIPMENT_OPERATOR,
                    scope=AccessScope.PROJECTS,
                    project_ids=[project.pk],
                )
                EquipmentOperatorAssignment.objects.create(
                    org=org,
                    equipment=item,
                    operator=operator,
                    project=project,
                    valid_from=start,
                    assigned_by=ctx_user(ctx),
                )
            else:
                # Historial coherente: la vigencia de ejemplo arranca al inicio del periodo.
                EquipmentOperatorAssignment.objects.filter(
                    org=org, equipment=item, operator=operator, valid_from__gt=start
                ).update(valid_from=start)
            if not EquipmentProject.objects.filter(org=org, equipment=item, ended_on=None).exists():
                EquipmentProject.objects.create(
                    org=org,
                    equipment=item,
                    project=project,
                    started_on=start,
                    assigned_by=ctx_user(ctx),
                )
            else:
                EquipmentProject.objects.filter(
                    org=org, equipment=item, started_on__gt=start
                ).update(started_on=start)
            result[code] = operator
        return result

    def _budget(self, org: Organization, project: Project, admin: AppUser) -> dict[str, BudgetItem]:
        budget = Budget.objects.create(
            org=org, project=project, name=BUDGET_NAME, code="DEMO-EQ", created_by=admin
        )
        version = BudgetVersion.objects.create(
            org=org,
            budget=budget,
            number=1,
            status=BudgetVersionStatus.APPLIED,
            file_name="datos-de-ejemplo.xlsx",
            source_file=b"",
            uploaded_by=admin,
        )
        items: dict[str, BudgetItem] = {}
        for order, (code, (description, total)) in enumerate(BUDGET_ITEMS.items()):
            item = BudgetItem.objects.create(org=org, budget=budget, code=code)
            BudgetItemVersion.objects.create(
                org=org,
                version=version,
                item=item,
                kind=BudgetRowKind.ITEM,
                sort_order=order,
                description=description,
                unit="h",
                quantity=Decimal("1.0000"),
                unit_price=total,
                total=total,
            )
            items[code] = item
        budget.contractual_version = version
        budget.save(update_fields=["contractual_version"])
        return items

    def _usage(
        self,
        org: Organization,
        project: Project,
        admin: AppUser,
        equipment: dict[str, Equipment],
        operators: dict[str, AppUser],
        items: dict[str, BudgetItem],
        start: date,
        today: date,
        rng: random.Random,
    ) -> int:
        rates: dict[str, list[EquipmentRate]] = {
            code: list(EquipmentRate.objects.filter(org=org, equipment=item).order_by("valid_from"))
            for code, item in equipment.items()
        }
        busy = {
            (u.equipment_id, u.usage_date, u.start_time)
            for u in EquipmentUsage.objects.filter(org=org)
        }
        future = _weekdays(today + timedelta(days=1), today + timedelta(days=7))
        created = 0
        for code, item in equipment.items():
            profile = PROFILE[code]
            meter = Decimal(rng.randint(1200, 4800))
            maintenance_since = today - timedelta(days=12) if code == "EQ-003" else None
            days = [(d, "past") for d in _weekdays(start, today)]
            if code in {"EQ-001", "EQ-004", "EQ-002"}:
                days += [(d, "future") for d in future if rng.random() < 0.8]
            for day, kind in days:
                if maintenance_since and day >= maintenance_since and kind == "past":
                    continue
                if rng.random() > profile["p"]:
                    continue
                begin = time(rng.choice([6, 7, 7, 8]), rng.choice([0, 0, 30]))
                length = Decimal(str(rng.choice([4, 5, 6, 7, 7.5, 8, 8.5, 9])))
                end = (datetime.combine(day, begin) + timedelta(hours=float(length))).time()
                if (item.pk, day, begin) in busy:
                    continue
                if kind == "future":
                    status = EquipmentUsageStatus.SCHEDULED
                elif day == today:
                    status = EquipmentUsageStatus.IN_PROGRESS
                elif rng.random() < 0.04:
                    status = EquipmentUsageStatus.CANCELLED
                else:
                    status = EquipmentUsageStatus.COMPLETED
                hours = usage_hours(begin, end)
                rate = next((r for r in reversed(rates[code]) if r.valid_from <= day), None)
                cost = usage_cost(hours, rate.hourly_rate) if rate else None
                start_meter = end_meter = None
                is_km = profile["meter"] == "kilometers"
                if status == EquipmentUsageStatus.COMPLETED:
                    advance = hours * (Decimal(32) if is_km else Decimal(1))
                    start_meter, meter = meter, meter + advance
                    end_meter = meter
                with_item = profile["item"] and rng.random() < 0.9
                EquipmentUsage.objects.create(
                    org=org,
                    equipment=item,
                    project=project,
                    operator=operators[code],
                    usage_date=day,
                    start_time=begin,
                    end_time=end,
                    activity=profile["activity"],
                    status=status,
                    meter_unit=EquipmentMeterUnit.KILOMETERS if is_km else EquipmentMeterUnit.HOURS,
                    start_meter=start_meter,
                    end_meter=end_meter,
                    budget_item=items[profile["item"]] if with_item else None,
                    rate=rate,
                    billable_hours=hours,
                    hourly_rate=rate.hourly_rate if rate else None,
                    cost=cost,
                    created_by=admin,
                    updated_by=admin,
                )
                busy.add((item.pk, day, begin))
                created += 1
        return created


def ctx_user(ctx: Any) -> AppUser:
    return AppUser.objects.get(pk=ctx.user_id)

"""`python manage.py seed_control_demo` — un proyecto de ejemplo para la página "Control".

Solo para desarrollo y la revisión visual de la pieza 5 de costos: en una organización
**existente** (`--org-name`) y con su administrador, crea un proyecto con cronograma (línea
base de unos diez meses alrededor de hoy), frentes de obra como ubicación, un presupuesto
aplicado (que queda como contractual), la matriz y registros de avance validados semana a
semana. Incluye los casos de los avisos: una tarea sin línea base, una tarea medida por %,
sobreejecución, un ajuste negativo, una cantidad fuera del plan y un registro sin revisar
(que no cuenta).

Escribe sin pasar por las importaciones (como `bootstrap_demo`): lo que interesa es el estado
final. Es aprovisionamiento offline y por eso figura en `ALLOWED` de `test_architecture.py`.
Determinista respecto de la fecha de hoy. Si el proyecto ya existe, se detiene.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, NamedTuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from suno.budgets.totals import money
from suno.core.context import Principal, resolve_request_context
from suno.core.models import (
    AppUser,
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Organization,
    Project,
    QuantityPlan,
    Record,
    RecordReview,
    RecordReviewStatus,
    Role,
    Task,
)
from suno.management.commands.bootstrap_demo import DEMO_FORM_DEFINITION
from suno.matrix.locations import LevelSpec, load_tree, project_levels, save_levels
from suno.sync.changeseq import acquire_org_lock, highest_change_seq

PROJECT_NAME = "Obra de ejemplo: control"
BUDGET_NAME = "Presupuesto de ejemplo (control)"
FORM_NAME = "Avance de obra (ejemplo del control)"
QUANTITY = Decimal("0.0001")

#: (código, descripción, [(código, descripción, unidad, cantidad, precio unitario)])
CHAPTERS: list[tuple[str, str, list[tuple[str, str, str, str, str]]]] = [
    ("1", "Preliminares", [
        ("1.1", "Localización y replanteo", "m2", "4000", "3200"),
        ("1.2", "Descapote y limpieza", "m2", "4000", "5400"),
    ]),
    ("2", "Movimiento de tierras", [
        ("2.1", "Excavación mecánica", "m3", "2600", "28000"),
        ("2.2", "Relleno compactado", "m3", "1800", "46000"),
    ]),
    ("3", "Estructura", [
        ("3.1", "Concreto de zapatas", "m3", "240", "690000"),
        ("3.2", "Acero de refuerzo", "kg", "21000", "5200"),
    ]),
    ("4", "Redes", [
        ("4.1", "Tubería PVC de 6 pulgadas", "ml", "900", "84000"),
        ("4.2", "Cámaras de inspección", "un", "18", "2450000"),
    ]),
]  # fmt: skip

STAGES = ["Preliminares", "Movimiento de tierras", "Estructura", "Redes"]


class Leaf(NamedTuple):
    stage: int
    name: str
    front: str
    #: Días desde el inicio del proyecto y duración en días calendario.
    offset: int
    duration: int
    #: (código del ítem, cantidad planeada); vacío: tarea medida por %.
    plan: tuple[tuple[str, str], ...]
    #: Parte de lo planeado que se ejecuta (1,10 = sobreejecución).
    performance: Decimal = Decimal(1)
    baseline: bool = True
    #: % validado de una tarea sin plan.
    pct: str | None = None


NORTH, SOUTH = "Frente norte", "Frente sur"
LEAVES = [
    Leaf(0, "Localización y replanteo", NORTH, 0, 20, (("1.1", "4000"),)),
    Leaf(0, "Descapote", NORTH, 14, 28, (("1.2", "4000"),), Decimal("0.95")),
    Leaf(0, "Trámites y permisos", NORTH, 0, 30, (), pct="60"),
    Leaf(1, "Excavación frente norte", NORTH, 35, 50, (("2.1", "1400"),), Decimal("0.90")),
    Leaf(1, "Excavación frente sur", SOUTH, 49, 50, (("2.1", "1200"),), Decimal("1.10")),
    Leaf(1, "Rellenos", SOUTH, 84, 60, (("2.2", "1800"),), Decimal("0.75")),
    Leaf(2, "Zapatas eje A", NORTH, 98, 45, (("3.1", "130"), ("3.2", "11000")), Decimal("0.85")),
    Leaf(2, "Zapatas eje B", SOUTH, 126, 45, (("3.1", "110"), ("3.2", "10000")), Decimal("0.70")),
    Leaf(3, "Tubería principal", NORTH, 154, 60, (("4.1", "900"),), Decimal("0.80")),
    Leaf(3, "Cámaras de inspección", SOUTH, 196, 60, (("4.2", "18"),), baseline=False),
]


def _row(item: BudgetItem, quantity: Decimal, note: str = "") -> dict[str, Any]:
    return {
        "item_id": str(item.pk),
        "location_id": None,
        "quantity": str(quantity.quantize(QUANTITY)),
        "note": note,
    }


class _Seeder:
    def __init__(self, org: Organization, admin: AppUser, today: date) -> None:
        self.org = org
        self.admin = admin
        self.today = today
        self.ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        self.seq = highest_change_seq(self.ctx)
        self.uid = 0
        self.records = 0

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def run(self, name: str) -> Project:
        start = self.today - timedelta(days=182)
        start -= timedelta(days=start.weekday())
        project = Project.objects.create(
            org=self.org, name=name, code="CTL", created_by=self.admin, status_date=self.today
        )
        save_levels(self.ctx, project, [LevelSpec("Frente")])
        items = self._budget(project)
        template, _ = FormTemplate.objects.get_or_create(org=self.org, name=FORM_NAME)
        self.version, _ = FormTemplateVersion.objects.get_or_create(
            org=self.org,
            template=template,
            version_number=1,
            defaults={
                "name": FORM_NAME,
                "definition": DEMO_FORM_DEFINITION,
                "change_seq": self._next_seq(),
                "published_at": timezone.now(),
            },
        )
        self.device = Device.objects.create(
            id=uuid.uuid4(),
            org=self.org,
            owner=self.admin,
            platform="android",
            app_version="0.16.0",
        )
        tasks = self._schedule(project, start, items)
        # Los casos de los avisos.
        self._approved(
            tasks["Rellenos"],
            start + timedelta(days=100),
            [_row(items["2.2"], Decimal("-15"), "Ajuste por recálculo de topografía")],
        )
        self._approved(
            tasks["Excavación frente norte"],
            start + timedelta(days=44),
            [_row(items["2.2"], Decimal("40"), "Relleno puntual no planeado")],
        )
        self._record(tasks["Zapatas eje A"], self.today, [_row(items["3.1"], Decimal("5"))])
        return project

    def _budget(self, project: Project) -> dict[str, BudgetItem]:
        budget = Budget.objects.create(
            org=self.org, project=project, name=BUDGET_NAME, code="CTL", created_by=self.admin
        )
        direct = sum(
            (Decimal(q) * Decimal(p) for _, _, rows in CHAPTERS for _, _, _, q, p in rows),
            Decimal(0),
        )
        version = BudgetVersion.objects.create(
            org=self.org,
            budget=budget,
            number=1,
            status=BudgetVersionStatus.APPLIED,
            label="Contractual",
            file_name="ejemplo-control.xlsx",
            source_file=b"",
            uploaded_by=self.admin,
            applied_at=timezone.now(),
            direct_cost=money(direct),
            total=money(direct),
        )
        budget.contractual_version = version
        budget.save(update_fields=["contractual_version"])
        items: dict[str, BudgetItem] = {}
        values: list[BudgetItemVersion] = []
        for code, description, rows in CHAPTERS:
            chapter = BudgetItem.objects.create(org=self.org, budget=budget, code=code)
            values.append(
                BudgetItemVersion(
                    org=self.org,
                    version=version,
                    item=chapter,
                    kind=BudgetRowKind.CHAPTER,
                    sort_order=len(values),
                    description=description,
                    total=money(sum((Decimal(q) * Decimal(p) for *_, q, p in rows), Decimal(0))),
                )
            )
            for item_code, item_description, unit, quantity, price in rows:
                item = BudgetItem.objects.create(org=self.org, budget=budget, code=item_code)
                items[item_code] = item
                values.append(
                    BudgetItemVersion(
                        org=self.org,
                        version=version,
                        item=item,
                        kind=BudgetRowKind.ITEM,
                        sort_order=len(values),
                        description=item_description,
                        unit=unit,
                        quantity=Decimal(quantity),
                        unit_price=Decimal(price),
                        total=money(Decimal(quantity) * Decimal(price)),
                        parent=chapter,
                    )
                )
        BudgetItemVersion.objects.bulk_create(values)
        return items

    def _task(
        self,
        project: Project,
        outline: str,
        name: str,
        *,
        start: date,
        finish: date,
        parent: Task | None = None,
        summary: bool = False,
        milestone: bool = False,
        baseline: bool = True,
        location: Any = None,
    ) -> Task:
        self.uid += 1
        task = Task.objects.create(
            org=self.org,
            project=project,
            uid=self.uid,
            sort_order=self.uid,
            wbs=outline,
            outline_number=outline,
            outline_level=outline.count(".") + 1,
            parent=parent,
            stage=parent,
            name=name,
            is_summary=summary,
            is_milestone=milestone,
            start=start,
            finish=finish,
            baseline_start=start if baseline else None,
            baseline_finish=finish if baseline else None,
            location=location,
        )
        if parent is None:
            task.stage = task
            task.save(update_fields=["stage"])
        return task

    def _schedule(
        self, project: Project, start: date, items: dict[str, BudgetItem]
    ) -> dict[str, Task]:
        levels = project_levels(self.ctx, project)
        tasks: dict[str, Task] = {}
        end = start
        for index, stage_name in enumerate(STAGES):
            own = [leaf for leaf in LEAVES if leaf.stage == index]
            stage = self._task(
                project,
                str(index + 1),
                stage_name,
                start=start + timedelta(days=min(leaf.offset for leaf in own)),
                finish=start + timedelta(days=max(leaf.offset + leaf.duration for leaf in own)),
                summary=True,
            )
            for number, leaf in enumerate(own, start=1):
                begin = start + timedelta(days=leaf.offset)
                finish = begin + timedelta(days=leaf.duration)
                end = max(end, finish)
                node, _ = load_tree(self.ctx, project).ensure(self.ctx, levels, (leaf.front,))
                task = self._task(
                    project,
                    f"{index + 1}.{number}",
                    leaf.name,
                    start=begin,
                    finish=finish,
                    parent=stage,
                    baseline=leaf.baseline,
                    location=node,
                )
                for code, quantity in leaf.plan:
                    QuantityPlan.objects.create(
                        org=self.org,
                        project=project,
                        task=task,
                        item=items[code],
                        planned_quantity=Decimal(quantity),
                        updated_by=self.admin,
                    )
                self._progress(task, leaf, items, begin, finish)
                tasks[leaf.name] = task
        self._task(
            project, str(len(STAGES) + 1), "Entrega de obra", start=end, finish=end, milestone=True
        )
        return tasks

    def _progress(
        self, task: Task, leaf: Leaf, items: dict[str, BudgetItem], begin: date, end: date
    ) -> None:
        """Avance validado cada semana, al ritmo de la línea base por el rendimiento."""
        if leaf.pct is not None:
            if begin <= self.today:
                self._approved(
                    task, min(self.today, begin + timedelta(days=10)), [], Decimal(leaf.pct)
                )
            return
        targets = {code: Decimal(quantity) * leaf.performance for code, quantity in leaf.plan}
        weeks = max(1, (end - begin).days // 7)
        done = dict.fromkeys(targets, Decimal(0))
        day = begin + timedelta(days=2)
        while day <= self.today and any(done[code] < targets[code] for code in targets):
            rows = []
            for code, target in targets.items():
                step = min(target / weeks, target - done[code]).quantize(QUANTITY)
                if step > 0:
                    done[code] += step
                    rows.append(_row(items[code], step))
            if rows:
                self._approved(task, day, rows)
            day += timedelta(days=7)

    def _record(
        self, task: Task, day: date, rows: list[dict[str, Any]], pct: Decimal | None = None
    ) -> Record:
        self.records += 1
        return Record.objects.create(
            id=uuid.uuid4(),
            org=self.org,
            change_seq=self._next_seq(),
            template_version=self.version,
            answers={},
            captured_at=timezone.make_aware(datetime.combine(day, time(10))),
            device=self.device,
            created_by=self.admin,
            task=task,
            progress_pct=pct,
            quantities=rows,
        )

    def _approved(
        self, task: Task, day: date, rows: list[dict[str, Any]], pct: Decimal | None = None
    ) -> None:
        RecordReview.objects.create(
            org=self.org,
            record=self._record(task, day, rows, pct),
            status=RecordReviewStatus.APPROVED,
            reviewer=self.admin,
        )


class Command(BaseCommand):
    help = (
        "Siembra un proyecto de ejemplo para la página Control (costos, pieza 5) en una "
        "organización existente. Solo desarrollo y demos."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--org-name", default="SUNO Demo", help="Organización existente.")
        parser.add_argument(
            "--project-name", default=PROJECT_NAME, help="Nombre del proyecto a crear."
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Permite correrlo con DEBUG=False.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "Solo para desarrollo (DEBUG=False). Usa --allow-production si lo quieres."
            )
        org = Organization.objects.filter(name=options["org_name"]).first()
        if org is None:
            raise CommandError(f"No existe la organización «{options['org_name']}».")
        admin = (
            AppUser.objects.filter(org=org, memberships__role__in=[Role.OWNER, Role.ADMIN])
            .order_by("date_joined")
            .first()
        )
        if admin is None:
            raise CommandError("La organización no tiene un administrador.")
        name = options["project_name"]
        if Project.objects.filter(org=org, name__iexact=name).exists():
            raise CommandError(f"Ya existe el proyecto «{name}». Usa --project-name.")
        with transaction.atomic():
            acquire_org_lock(org.pk)
            seeder = _Seeder(org, admin, timezone.localdate())
            project = seeder.run(name)
        self.stdout.write(
            self.style.SUCCESS(
                f"Proyecto de ejemplo listo: «{project.name}» ({seeder.records} registros). "
                f"Página: /plataforma/proyectos/{project.pk}/control/"
            )
        )

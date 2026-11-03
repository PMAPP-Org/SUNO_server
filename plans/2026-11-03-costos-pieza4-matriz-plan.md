# Costos — Pieza 4a (Matriz de homologación y ubicación), servidor: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada proyecto defina niveles de ubicación (Tramo → PR → Costado…) y un árbol de nodos; que las tareas tengan ubicación (leída de campos personalizados de MS Project o asignada en SUNO); que la oficina cargue la matriz planeada ítem × tarea → cantidad desde una plantilla Excel con vista previa; que la app registre cantidades ejecutadas dentro de la visita; y que el % de las tareas con plan salga de esas cantidades, ponderadas por costo.

**Architecture:** Modelos nuevos en `core/models/location.py` (`LocationLevel`, `LocationNode`) y `core/models/quantity.py` (`QuantityPlan`, `QuantityPlanImport`), más `Task.location` y `Visit.quantities` (JSONB). Lógica en el paquete nuevo `visitacampo/matrix/`: árbol de ubicación (`locations.py`), avance por cantidades puro (`progress.py`) y sus lecturas con BD (`quantities.py`), plantilla Excel (`template.py`), diferencias (`diff.py`), importación en dos pasos (`importer.py`), ediciones web (`edits.py`), lecturas para pantalla (`reports.py`), acceso (`access.py`) y vistas (`views.py`, `urls.py`). Se integra con el cronograma (`projects/`), el push y el pull de visitas (`sync/`), la API de tareas de campo, el Admin de visitas y la vista previa de presupuestos.

**Tech Stack:** Django 5.2, PostgreSQL 16, Ninja, Pydantic v2, `openpyxl` 3.1.5 (lectura) y `backoffice.export.to_xlsx` (escritura), `defusedxml`, `Decimal` con `ROUND_HALF_UP`.

**Spec:** `plans/2026-11-03-costos-pieza4-matriz-design.md` (aprobada). Planes de referencia: `plans/2026-11-01-costos-pieza3-presupuesto-plan.md` (importación Excel en dos pasos) y `plans/2026-10-31-costos-etapa1-campo-plan.md` (campos nuevos de la visita en el sync).

**Rama:** `feat/costos-matriz`, en el worktree `C:\Users\gabri\APP\wt-server-matriz`. Todos los comandos se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Se anotan en la spec en la Task 0.

1. **Las cantidades viajan dentro de la visita, no como entidad sincronizable aparte.** `Visit.quantities` es una lista JSONB de `{item_id, location_id, quantity, note}`. Motivo: el sync (push con SQL propio, versiones, `change_seq`, pull) ya resuelve la visita; una entidad nueva duplicaría todo eso. Se conserva lo que la spec pide de `VisitQuantity`: va con la visita, cuenta solo si la visita está validada, y no cambia después de validada (la misma regla `visit_validated` que ya protege `task_id` y `progress_pct`). Antes de validarse, la visita se puede corregir como hoy (el `update` reemplaza la lista completa). Lo ejecutado se suma en Python desde las visitas validadas (`matrix/quantities.py`).
2. **Formato guardado de cada cantidad:** `{"item_id": "<uuid>", "location_id": "<uuid>" | null, "quantity": "120.5000", "note": "…"}`; la cantidad como texto decimal con 4 decimales (exacto). En el pull sale como número JSON. Máximo 200 filas por visita; `|cantidad| < 10¹⁴`, hasta 4 decimales, distinta de 0; nota hasta 500 caracteres. `location_id` nulo = la ubicación de la tarea.
3. **Regla del trío en el push** (`task_id`, `progress_pct`, `quantities`): sin tarea, ni % ni cantidades; con tarea, % o al menos una cantidad (o ambos). En un `update`, `task_id` y `progress_pct` siguen viajando juntos; `quantities` solo puede viajar con ellos, y si no viaja la lista queda vacía (un cliente anterior a la pieza 4 nunca tiene cantidades; uno nuevo siempre manda los tres). Rechazos nuevos: `quantities_without_task`, `item_not_in_project`, `location_outside_task`, `note_required` (cantidad negativa, o ítem fuera del plan de la tarea).
4. **Fuente de la ubicación: todo o nada.** Si algún nivel tiene campo de Project, todos deben tenerlo (modo Project); si ninguno, la ubicación se asigna en SUNO (plantilla o web). Mezclar complicaría saber quién manda en cada nivel y no hay un caso que lo pida.
5. **Campos personalizados de Project.** Se leen los `ExtendedAttribute` de cada tarea (`FieldID` + `Value`) y las definiciones del proyecto (`FieldID`, `FieldName`, `Alias`). Los códigos de esquema con tabla de valores (que guardan `ValueID` sin `Value`) no se soportan: esas tareas quedan sin valor y la vista previa lo cuenta como ruta incompleta. La lista de campos disponibles se guarda en `ScheduleImport.summary["custom_fields"]` al subir el XML; la página de niveles la lee de la última importación aplicada.
6. **La ubicación desde Project se asigna al aplicar el cronograma y solo a tareas hoja.** Cambiar los niveles no reubica nada hasta la siguiente importación (spec).
7. **Hasta 6 niveles.** Cada nodo guarda `path` = `/<hex1>/<hex2>/…/` (sus ancestros y él), para saber en una comparación de texto si un nodo está dentro de otro (validación del push y consolidación por nodo). `key` es el nombre normalizado (`fold` + espacios colapsados), único por (proyecto, padre).
8. **Permisos: se reutilizan las políticas de presupuestos.** Ver la matriz = `can_view_budgets`; editar niveles, matriz y ubicación = `can_manage_budgets`. Son exactamente los roles de la spec; una política nueva con los mismos roles no agregaría nada. El agente de campo recibe 404 en `/matriz/` antes de mirar el proyecto (como en presupuestos).
9. **Plantilla.** Hoja `Matriz`, encabezado en la fila 1: `UniqueID`, `EDT`, `Tarea`, un encabezado por nivel, `Presupuesto`, `Código de ítem`, `Descripción`, `Unidad`, `Cantidad planeada`. Al leerla las columnas se ubican por el texto del encabezado (normalizado), así que el usuario puede reordenarlas. Ítem = (nombre del presupuesto, código); si el proyecto tiene un solo presupuesto, `Presupuesto` puede ir vacío. Fila con ítem y sin cantidad = par eliminado (se cuenta como aviso). En modo Project las columnas de nivel son de referencia y se ignoran al leer.
10. **Vista previa obsoleta.** `summary["base"]` guarda el número de la última importación aplicada del cronograma, de la matriz y de cada presupuesto vigente; al aplicar, si cambió alguno se pide subir de nuevo. Subir un archivo nuevo descarta la importación pendiente anterior del proyecto. Aplicar bloquea la fila del proyecto (`select_for_update(nowait=True)`), el mismo bloqueo que la importación del cronograma.
11. **Avance por cantidades.** Por tarea hoja con al menos un ítem de plan con cantidad > 0: valor planeado `V = Σ planeada × precio`, valor ejecutado `E = Σ max(ejecutada, 0) × precio`; `% = min(100, 100·E/V)` a 2 decimales. Si `V = 0`, `% = min(100, promedio de 100·max(ejecutada,0)/planeada)`. Las cantidades sobre ítems fuera del plan de la tarea se muestran pero no suman. Precio = valor unitario de la última versión aplicada que contiene el ítem (la vigente si sigue en ella; si se retiró, su último precio conocido).
12. **Consolidación por valor.** `rollup` pondera por valor planeado cuando **todos** los hijos lo tienen y la suma es > 0; si no, por días hábiles de línea base, como hoy. El valor de un resumen es la suma de sus hijos solo si todos lo tienen.
13. **API de tareas de campo.** `FieldTask` gana `measured_by` (`pct` o `quantities`), `location` (`{id, label}` o `null`), `location_options` (el nodo de la tarea y sus descendientes vigentes) y `planned_items` (`item_id`, `code`, `description`, `unit`, `planned_quantity`, `executed_quantity`); `last_validated_pct` pasa a ser el % SUNO (por cantidades cuando hay plan). Descripción y unidad, de la misma "última versión aplicada que contiene el ítem".
14. **Vista previa de presupuestos.** Al preparar una versión nueva, los ítems que desaparecen y tienen plan o ejecución se agregan a `summary["warnings"]`.
15. **URL.** `plataforma/proyectos/<uuid:project_id>/matriz/` con `app_name = "matrix"`; pestaña **Matriz** junto a **Presupuestos**, visible con `can_view_budgets`.

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test` (no `pytest-django`). Los que tocan BD usan `visitacampo/core/tests/dbsupport.py`:
  ```python
  from visitacampo.core.tests.dbsupport import SKIP_REASON, setup_test_database, teardown_test_database
  setUpModule = setup_test_database
  tearDownModule = teardown_test_database
  skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
  ```
- Fábricas existentes: `visitacampo.projects.tests.factories.make_field_world()` (organización, admin, supervisor, agente y el cronograma `samples.v1()` aplicado; `world.task(uid)`, `world.visit(...)`, `world.approve(visit)`, `world.ctx(user)`), `make_visit(...)`; `visitacampo.budgets.tests.factories.make_budget(project, user)` y `apply_rows(ctx, budget, rows)`; `visitacampo.budgets.tests.samples.V1_ROWS`.
- Ningún módulo de producción usa `Model.objects`: siempre `scoped_qs(ctx, Model)`; al crear, `org_id=ctx.org_id`. Los tests sí pueden usar `Model.objects` (lo vigila `core/tests/test_architecture.py`).
- Auditoría: `audit.record(ctx, action=AuditAction.X, target=..., metadata=...)` dentro de `transaction.atomic()` junto con el cambio.
- Nada de `assert` en producción (bandit B101) ni `import xml.etree` (semgrep/bandit B405): para errores de XML se usa `defusedxml.ElementTree.ParseError`.
- Comentarios y textos de interfaz en español; código en inglés. Commits en inglés, estilo conventional, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Líneas de 100 caracteres (ruff); `ruff check --fix` ordena imports.

## Cómo correr las pruebas

No hay Python 3.12 en la máquina: todo corre en Docker con `infra/scripts/test_docker.sh` (Docker Desktop arrancado). El contenedor trabaja en `server/`.

```bash
infra/scripts/test_docker.sh visitacampo/matrix                    # un paquete
infra/scripts/test_docker.sh visitacampo/matrix/tests/test_progress.py
infra/scripts/test_docker.sh                                       # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pip install -q bandit && bandit -q -r visitacampo config -x '*/tests/*,*/migrations/*'"
infra/scripts/test_docker.sh --down                                # al terminar
```

Las 4 fallas conocidas de `sync/tests/test_load_script.py` (dependen del entorno) no cuentan.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/visitacampo/core/models/location.py` | `LocationLevel`, `LocationNode` |
| `server/visitacampo/core/models/quantity.py` | `QuantityPlan`, `QuantityPlanImport`, `QuantityPlanImportStatus` |
| `server/visitacampo/core/models/project.py` | `Task.location` |
| `server/visitacampo/core/models/visit.py` | `Visit.quantities` y el CHECK `visit_task_progress_pair` ampliado |
| `server/visitacampo/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/visitacampo/core/migrations/0014_add_quantity_matrix.py` | Generada |
| `server/visitacampo/core/audit.py` | Códigos `matrix.*` |
| `server/visitacampo/matrix/access.py` | 404 para quien no ve presupuestos; exige gestión |
| `server/visitacampo/matrix/locations.py` | Niveles, nombres normalizados, rutas, etiquetas, descendientes |
| `server/visitacampo/matrix/progress.py` | Avance por cantidades (puro) |
| `server/visitacampo/matrix/quantities.py` | Ejecutado validado, precios, % SUNO combinado (con BD) |
| `server/visitacampo/matrix/template.py` | Plantilla Excel: escribir y leer (puro salvo la descarga) |
| `server/visitacampo/matrix/diff.py` | Diferencias de la matriz y totales por ítem (puro) |
| `server/visitacampo/matrix/importer.py` | Subir, aplicar, descartar |
| `server/visitacampo/matrix/edits.py` | Ediciones web fila a fila y ubicación de una tarea |
| `server/visitacampo/matrix/reports.py` | Vista por ítems y vista por ubicación |
| `server/visitacampo/matrix/views.py`, `urls.py` | Páginas `/plataforma/proyectos/<id>/matriz/` |
| `server/visitacampo/matrix/tests/…` | Pruebas y `factories.py` |
| `server/visitacampo/projects/mspdi.py` | Campos personalizados de tarea y del proyecto |
| `server/visitacampo/projects/importer.py` | Ubicación desde Project en la vista previa y al aplicar |
| `server/visitacampo/projects/progress.py` | Consolidación por valor |
| `server/visitacampo/projects/schedule.py` | % SUNO combinado y valor por tarea |
| `server/visitacampo/projects/views.py` | Lista de proyectos y ficha de tarea con el % combinado y el bloque de cantidades |
| `server/visitacampo/projects/field_tasks.py`, `schemas.py` | Campos nuevos de `FieldTask` |
| `server/visitacampo/sync/payloads.py`, `sql.py`, `records.py`, `schemas.py`, `services.py` | `quantities` en el push y el pull |
| `server/openapi/v1.yaml` | Contrato: `VisitQuantity`, `Visit.quantities`, `VisitPayload.quantities`, `FieldTask` |
| `server/visitacampo/core/admin.py` | Cantidades en el Admin de visitas |
| `server/visitacampo/budgets/importer.py` | Aviso de ítems eliminados con plan o ejecución |
| `server/config/urls.py` | Monta las URL de la matriz |
| `server/visitacampo/core/templates/suno/projects/_header.html`, `task.html` | Pestaña **Matriz**; bloque de cantidades |
| `server/visitacampo/core/templates/suno/matrix/*.html` | Plantillas del módulo |
| `server/visitacampo/core/static/suno/css/projects.css` | Estilos (prefijo `mx-`) |

---

### Task 0: Rama y notas en la spec

**Files:**
- Modify: `plans/2026-11-03-costos-pieza4-matriz-design.md` (al final)

- [ ] **Step 1: Comprobar la rama**

Run: `git -C C:/Users/gabri/APP/wt-server-matriz branch --show-current`
Expected: `feat/costos-matriz`

- [ ] **Step 2: Anotar en la spec las decisiones tomadas al planear**

Agregar al final de la spec:

```markdown
## Decisiones tomadas al planear

Precisiones del plan `2026-11-03-costos-pieza4-matriz-plan.md`; no cambian lo aprobado.

- Las cantidades de campo viajan dentro de la visita (`Visit.quantities`, JSONB) en vez de
  una tabla sincronizable aparte: el sync ya resuelve la visita, y así heredan la regla
  "una visita validada no cambia". Cuentan solo con la visita validada; antes se pueden
  corregir como cualquier dato de la visita.
- La ubicación sale toda de Project o toda de SUNO: si un nivel tiene campo de Project,
  todos deben tenerlo.
- Hasta 6 niveles. Los códigos de esquema de Project con tabla de valores no se leen.
- Ver la matriz y editarla usan las políticas de presupuestos (`can_view_budgets`,
  `can_manage_budgets`): son los mismos roles.
- La plantilla identifica el ítem por (presupuesto, código); con un solo presupuesto la
  columna Presupuesto puede ir vacía. Una fila con ítem y sin cantidad elimina el par.
- El % por cantidades usa el precio de la última versión aplicada que contiene el ítem.
```

- [ ] **Step 3: Commit**

```bash
git add plans/2026-11-03-costos-pieza4-matriz-design.md plans/2026-11-03-costos-pieza4-matriz-plan.md
git commit -m "docs(plan): plan de la pieza 4a, matriz de homologación (servidor)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Modelos y migración

**Files:**
- Create: `server/visitacampo/core/models/location.py`
- Create: `server/visitacampo/core/models/quantity.py`
- Modify: `server/visitacampo/core/models/project.py` (clase `Task`)
- Modify: `server/visitacampo/core/models/visit.py` (campo `quantities` y CHECK `visit_task_progress_pair`)
- Modify: `server/visitacampo/core/models/__init__.py`
- Create: `server/visitacampo/core/migrations/0014_add_quantity_matrix.py` (generada)
- Create: `server/visitacampo/matrix/__init__.py`, `server/visitacampo/matrix/tests/__init__.py`
- Test: `server/visitacampo/matrix/tests/test_models.py`

- [ ] **Step 1: Crear los paquetes vacíos**

`server/visitacampo/matrix/__init__.py`:

```python
"""Matriz de homologación y ubicación (costos, pieza 4)."""
```

`server/visitacampo/matrix/tests/__init__.py`: vacío.

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_models.py`:

```python
"""Restricciones de la matriz y de la ubicación en la base de datos."""

from __future__ import annotations

import unittest
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.budgets.tests.factories import apply_rows, make_budget
from visitacampo.budgets.tests.samples import V1_ROWS
from visitacampo.core.models import (
    BudgetItem,
    LocationLevel,
    LocationNode,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    Visit,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.tests.factories import make_field_world, make_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class LocationModelTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.level = LocationLevel.objects.create(
            org=self.world.org, project=self.world.project, name="Tramo", depth=1
        )

    def test_depth_is_unique_per_project(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="Otro", depth=1
            )

    def test_depth_between_one_and_six(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="Siete", depth=7
            )

    def test_level_name_is_unique_ignoring_case(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationLevel.objects.create(
                org=self.world.org, project=self.world.project, name="TRAMO", depth=2
            )

    def test_node_key_is_unique_per_parent_also_at_the_root(self) -> None:
        LocationNode.objects.create(
            org=self.world.org,
            project=self.world.project,
            level=self.level,
            name="Tramo 2",
            key="tramo 2",
            path="/a/",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            LocationNode.objects.create(
                org=self.world.org,
                project=self.world.project,
                level=self.level,
                name="TRAMO 2",
                key="tramo 2",
                path="/b/",
            )

    def test_task_location(self) -> None:
        node = LocationNode.objects.create(
            org=self.world.org,
            project=self.world.project,
            level=self.level,
            name="Tramo 1",
            key="tramo 1",
            path="/a/",
        )
        task = self.world.task(3)
        task.location = node
        task.save(update_fields=["location"])
        self.assertEqual(node.tasks.get(), task)


@skip_without_db
class QuantityPlanModelTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        budget = make_budget(self.world.project, self.world.admin)
        apply_rows(self.world.ctx(), budget, V1_ROWS)
        self.item = BudgetItem.objects.get(budget=budget, code="1.1")

    def _plan(self, quantity: str) -> QuantityPlan:
        return QuantityPlan.objects.create(
            org=self.world.org,
            project=self.world.project,
            task=self.world.task(3),
            item=self.item,
            planned_quantity=Decimal(quantity),
            updated_by=self.world.admin,
        )

    def test_pair_is_unique(self) -> None:
        self._plan("10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plan("5")

    def test_planned_quantity_not_negative(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._plan("-1")

    def test_import_number_iff_applied(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityPlanImport.objects.create(
                org=self.world.org,
                project=self.world.project,
                status=QuantityPlanImportStatus.PENDING,
                number=1,
                file_name="m.xlsx",
                source_file=b"x",
                uploaded_by=self.world.admin,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuantityPlanImport.objects.create(
                org=self.world.org,
                project=self.world.project,
                status=QuantityPlanImportStatus.APPLIED,
                file_name="m.xlsx",
                source_file=b"x",
                uploaded_by=self.world.admin,
            )


@skip_without_db
class VisitQuantitiesConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def _save(self, visit: Visit, **values: object) -> None:
        for name, value in values.items():
            setattr(visit, name, value)
        with transaction.atomic():
            visit.save()

    def test_quantities_default_to_empty_list(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        self.assertEqual(visit.quantities, [])

    def test_task_with_quantities_and_no_pct_is_valid(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        row = {"item_id": "x", "location_id": None, "quantity": "1.0000", "note": ""}
        self._save(visit, task=self.world.task(3), progress_pct=None, quantities=[row])

    def test_task_without_pct_or_quantities_is_rejected(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        with self.assertRaises(IntegrityError):
            self._save(visit, task=self.world.task(3), progress_pct=None, quantities=[])

    def test_quantities_without_task_are_rejected(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        row = {"item_id": "x", "location_id": None, "quantity": "1.0000", "note": ""}
        with self.assertRaises(IntegrityError):
            self._save(visit, quantities=[row])

    def test_pct_range_still_checked(self) -> None:
        visit = make_visit(self.world.org, self.world.agent)
        with self.assertRaises(IntegrityError):
            self._save(visit, task=self.world.task(3), progress_pct=Decimal("101"))
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_models.py`
Expected: FAIL con `ImportError: cannot import name 'LocationLevel'`.

- [ ] **Step 4: Escribir los modelos**

`server/visitacampo/core/models/location.py`:

```python
"""Ubicación de un proyecto (costos, pieza 4): niveles y árbol de nodos.

Los niveles van precisando el lugar (Tramo → PR → Costado). Cada nodo cuelga
del nodo del nivel anterior; las tareas y las cantidades de campo apuntan a un
nodo. Los nodos nunca se borran: se retiran (`retired_at`).
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel

#: Profundidad máxima del árbol.
MAX_LEVELS = 6


class LocationLevel(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="location_levels"
    )
    name = models.CharField(max_length=60, verbose_name="nombre")
    depth = models.PositiveSmallIntegerField(verbose_name="profundidad")
    #: `FieldID` del campo personalizado de MS Project que lo alimenta
    #: (p. ej. "188743731" = Text1). Vacío: la ubicación se asigna en SUNO.
    source_field = models.CharField(max_length=20, blank=True, verbose_name="campo de Project")

    class Meta:
        db_table = "location_level"
        ordering = ["depth"]
        constraints = [
            models.UniqueConstraint(fields=["project", "depth"], name="location_level_depth_uq"),
            models.UniqueConstraint(
                Lower("name"), "project", name="location_level_name_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(depth__gte=1, depth__lte=MAX_LEVELS),
                name="location_level_depth_range",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class LocationNode(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="location_nodes"
    )
    level = models.ForeignKey(LocationLevel, on_delete=models.PROTECT, related_name="nodes")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    name = models.CharField(max_length=120, verbose_name="nombre")
    #: `name` normalizado (sin tildes, minúsculas, espacios colapsados): identidad.
    key = models.CharField(max_length=120)
    #: `/<hex del nivel 1>/…/<hex de este nodo>/`: un nodo está dentro de otro
    #: si su `path` empieza por el del otro.
    path = models.CharField(max_length=255)
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "location_node"
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "parent", "key"],
                name="location_node_key_uq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name
```

`server/visitacampo/core/models/quantity.py`:

```python
"""Matriz planeada (costos, pieza 4): ítem del presupuesto × tarea → cantidad.

Cuelga de `Task` y `BudgetItem`, que son estables entre versiones, así que
sobrevive a nuevas importaciones del cronograma y del presupuesto.
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class QuantityPlan(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    task = models.ForeignKey(
        "core.Task", on_delete=models.PROTECT, related_name="quantity_plans"
    )
    item = models.ForeignKey(
        "core.BudgetItem", on_delete=models.PROTECT, related_name="quantity_plans"
    )
    planned_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, verbose_name="cantidad planeada"
    )
    updated_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "quantity_plan"
        constraints = [
            models.UniqueConstraint(fields=["task", "item"], name="quantity_plan_pair_uq"),
            models.CheckConstraint(
                condition=models.Q(planned_quantity__gte=0),
                name="quantity_plan_not_negative",
            ),
        ]


class QuantityPlanImportStatus(models.TextChoices):
    PENDING = "pending", "Pendiente"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class QuantityPlanImport(OrgScopedModel):
    """Una carga de la plantilla de la matriz, en dos pasos (como el presupuesto)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="quantity_plan_imports"
    )
    #: Consecutivo por proyecto; solo las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=10,
        choices=QuantityPlanImportStatus.choices,
        default=QuantityPlanImportStatus.PENDING,
    )
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    source_file = models.BinaryField(verbose_name="archivo original")
    #: Diferencias, totales por ítem, avisos y la base sobre la que se calculó.
    summary = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "quantity_plan_import"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(status="applied", number__isnull=False)
                    | (~models.Q(status="applied") & models.Q(number__isnull=True))
                ),
                name="quantity_plan_import_number_iff_applied",
            ),
            models.UniqueConstraint(
                fields=["project", "number"], name="quantity_plan_import_number_uq"
            ),
        ]
```

En `core/models/project.py`, dentro de `class Task`, después de `is_critical`:

```python
    #: Costos, pieza 4: dónde está la tarea (solo hojas). Desde Project o asignada en SUNO.
    location = models.ForeignKey(
        "core.LocationNode",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="tasks",
        verbose_name="ubicación",
    )
```

En `core/models/visit.py`, dentro de `class Visit`, después de `progress_pct`:

```python
    #: Costos, pieza 4: cantidades ejecutadas en esta visita, por ítem del
    #: presupuesto. Lista de `{item_id, location_id, quantity, note}`, con la
    #: cantidad como texto decimal de 4 decimales (`sync/payloads.py`). Cuentan
    #: para el avance cuando la visita está validada (`matrix/quantities.py`).
    quantities = models.JSONField(default=list, blank=True, verbose_name="cantidades")
```

y reemplazar el CHECK `visit_task_progress_pair` (mismo nombre) por:

```python
            # Costos, etapa 1 y pieza 4: sin tarea, ni % ni cantidades; con
            # tarea, un % en 0..100 o al menos una cantidad (o ambos).
            # `payloads.py` lo rechaza antes con detalle; esto es la red.
            models.CheckConstraint(
                condition=(
                    models.Q(task__isnull=True)
                    & models.Q(progress_pct__isnull=True)
                    & models.Q(quantities=[])
                )
                | (
                    models.Q(task__isnull=False)
                    & (
                        # `NULL >= 0` es NULL y un CHECK acepta NULL: por eso el
                        # `isnull=False` explícito.
                        models.Q(
                            progress_pct__isnull=False,
                            progress_pct__gte=0,
                            progress_pct__lte=100,
                        )
                        | (models.Q(progress_pct__isnull=True) & ~models.Q(quantities=[]))
                    )
                ),
                name="visit_task_progress_pair",
            ),
```

- [ ] **Step 5: Reexportar en `core/models/__init__.py`**

Agregar los imports junto a los de `budget` y sumarlos a `__all__` en orden alfabético:

```python
from .location import MAX_LEVELS, LocationLevel, LocationNode
from .quantity import QuantityPlan, QuantityPlanImport, QuantityPlanImportStatus
```

`__all__` gana `"LocationLevel"`, `"LocationNode"`, `"MAX_LEVELS"`, `"QuantityPlan"`, `"QuantityPlanImport"`, `"QuantityPlanImportStatus"`.

- [ ] **Step 6: Generar la migración**

Run: `infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name add_quantity_matrix"`
Expected: `core/migrations/0014_add_quantity_matrix.py` con `CreateModel` de los cuatro modelos, `AddField` de `task.location` y `visit.quantities`, y `RemoveConstraint` + `AddConstraint` de `visit_task_progress_pair`.

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_models.py visitacampo/projects visitacampo/sync`
Expected: PASS (las 4 fallas conocidas de `test_load_script.py` aparte).

- [ ] **Step 8: Commit**

```bash
git add server/visitacampo/core/models server/visitacampo/core/migrations/0014_add_quantity_matrix.py server/visitacampo/matrix
git commit -m "feat(matrix): location tree, quantity plan and visit quantities models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Códigos de auditoría y acceso

**Files:**
- Modify: `server/visitacampo/core/audit.py` (enumeración `AuditAction`)
- Create: `server/visitacampo/matrix/access.py`
- Test: `server/visitacampo/matrix/tests/test_access.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_access.py`:

```python
from __future__ import annotations

import unittest

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.access import get_matrix_project, require_manage
from visitacampo.projects.tests.factories import make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class AccessTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.world.responsible(3)  # el agente ve el proyecto por ser responsable

    def test_field_agent_gets_404_even_seeing_the_project(self) -> None:
        with self.assertRaises(Http404):
            get_matrix_project(self.world.ctx(self.world.agent), self.world.project.pk)

    def test_supervisor_sees_but_cannot_manage(self) -> None:
        ctx = self.world.ctx(self.world.supervisor)
        self.assertEqual(get_matrix_project(ctx, self.world.project.pk), self.world.project)
        with self.assertRaises(PermissionDenied):
            require_manage(ctx)

    def test_admin_manages(self) -> None:
        require_manage(self.world.ctx())
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_access.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.matrix.access'`.

- [ ] **Step 3: Escribir el módulo y los códigos**

`server/visitacampo/matrix/access.py`:

```python
"""Quién ve y quién edita la matriz: los mismos roles que los presupuestos."""

from __future__ import annotations

from uuid import UUID

from django.core.exceptions import PermissionDenied

from visitacampo.budgets.access import get_budget_project
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Project
from visitacampo.core.policies import can_manage_budgets


def get_matrix_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 si el usuario no ve presupuestos (antes de mirar el proyecto)."""
    return get_budget_project(ctx, project_id)


def require_manage(ctx: RequestContext) -> None:
    if not can_manage_budgets(ctx, ctx.org_id):
        raise PermissionDenied
```

En `core/audit.py`, dentro de `AuditAction`, después de los códigos `BUDGET_*`:

```python
    MATRIX_LEVELS_CHANGED = "matrix.levels_changed"
    MATRIX_IMPORT_APPLIED = "matrix.import_applied"
    MATRIX_IMPORT_DISCARDED = "matrix.import_discarded"
    MATRIX_PLAN_CHANGED = "matrix.plan_changed"
    MATRIX_TASK_LOCATION_CHANGED = "matrix.task_location_changed"
```

Si `AuditAction` tiene una prueba que enumera los códigos (`core/tests/test_audit.py`), agregarlos allí también.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_access.py visitacampo/core/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/core/audit.py server/visitacampo/matrix
git commit -m "feat(matrix): access helpers and audit codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Árbol de ubicación (`locations.py`)

**Files:**
- Create: `server/visitacampo/matrix/locations.py`
- Test: `server/visitacampo/matrix/tests/test_locations.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_locations.py`:

```python
from __future__ import annotations

import unittest

from django.test import SimpleTestCase, TestCase

from visitacampo.core.models import AuditEvent, LocationLevel, LocationNode
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import (
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
from visitacampo.projects.tests.factories import make_field_world

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
        self.assertEqual(
            AuditEvent.objects.filter(action="matrix.levels_changed").count(), 2
        )

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
```

(`AuditEvent`, de `core/models/ops.py`, es el modelo que escribe `audit.record`.)

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_locations.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.matrix.locations'`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/locations.py`:

```python
"""Árbol de ubicación de un proyecto: niveles, nombres, rutas y etiquetas.

Un nodo se identifica por su padre y su nombre normalizado (`node_key`), así
que "Tramo 2" y "tramo  2" son el mismo lugar. `Tree` carga todos los nodos del
proyecto en una query y resuelve rutas, etiquetas y descendientes en memoria.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models.functions import Lower

from visitacampo.budgets.mapping import fold
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import MAX_LEVELS, LocationLevel, LocationNode, Project
from visitacampo.core.scoping import scoped_qs

MAX_NAME = 120
MAX_LEVEL_NAME = 60
SEPARATOR = " / "


class LevelError(ValueError):
    """Los niveles no se pueden guardar así; el mensaje es para el usuario."""


def clean_name(value: object) -> str:
    """Texto de una celda o un campo como nombre: espacios colapsados; 2.0 → "2"."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, Decimal) and value == value.to_integral_value():
        value = int(value)
    return " ".join(str(value).split())


def node_key(name: str) -> str:
    return " ".join(fold(name).split())


@dataclass(frozen=True)
class PathValues:
    #: Los nombres desde el nivel 1 hasta el último con valor sin huecos.
    names: tuple[str, ...]
    #: Había un valor después de un nivel vacío (se ignoró).
    incomplete: bool
    #: Algún nombre pasaba de 120 caracteres y se recortó.
    truncated: bool


def path_values(values: Sequence[object]) -> PathValues:
    names: list[str] = []
    incomplete = truncated = False
    gap = False
    for raw in values:
        name = clean_name(raw)
        if not name:
            gap = True
            continue
        if gap:
            incomplete = True
            break
        if len(name) > MAX_NAME:
            name, truncated = name[:MAX_NAME], True
        names.append(name)
    return PathValues(tuple(names), incomplete, truncated)


@dataclass(frozen=True)
class LevelSpec:
    name: str
    source_field: str = ""


def project_levels(ctx: RequestContext, project: Project) -> list[LocationLevel]:
    return list(scoped_qs(ctx, LocationLevel).filter(project=project).order_by("depth"))


def from_project(levels: Sequence[LocationLevel]) -> bool:
    """La ubicación sale de MS Project (todos los niveles tienen campo)."""
    return bool(levels) and all(level.source_field for level in levels)


def _validate(specs: Sequence[LevelSpec]) -> list[LevelSpec]:
    if len(specs) > MAX_LEVELS:
        raise LevelError(f"Se admiten hasta {MAX_LEVELS} niveles.")
    cleaned: list[LevelSpec] = []
    seen: set[str] = set()
    for spec in specs:
        name = clean_name(spec.name)
        if not name:
            raise LevelError("Cada nivel necesita un nombre.")
        if len(name) > MAX_LEVEL_NAME:
            raise LevelError(f"El nombre «{name[:20]}…» pasa de {MAX_LEVEL_NAME} caracteres.")
        key = node_key(name)
        if key in seen:
            raise LevelError(f"El nivel «{name}» está repetido.")
        seen.add(key)
        source = spec.source_field.strip()
        if source and not (source.isdigit() and len(source) <= 20):
            raise LevelError(f"El campo de Project del nivel «{name}» no es válido.")
        cleaned.append(LevelSpec(name, source))
    sourced = [bool(spec.source_field) for spec in cleaned]
    if any(sourced) and not all(sourced):
        raise LevelError(
            "Si un nivel se lee de MS Project, todos los niveles deben tener su campo."
        )
    return cleaned


def save_levels(ctx: RequestContext, project: Project, specs: Sequence[LevelSpec]) -> None:
    """Reemplaza los niveles. Quitar un nivel solo se permite si ningún nodo lo usa."""
    cleaned = _validate(specs)
    with transaction.atomic():
        scoped_qs(ctx, Project).select_for_update().get(pk=project.pk)
        levels = scoped_qs(ctx, LocationLevel).filter(project=project)
        existing = {level.depth: level for level in levels}
        for depth, level in sorted(existing.items()):
            if depth > len(cleaned):
                if scoped_qs(ctx, LocationNode).filter(level=level).exists():
                    raise LevelError(
                        f"El nivel «{level.name}» tiene ubicaciones: no se puede quitar."
                    )
                level.delete()
        # Dos pasadas: intercambiar nombres ("Tramo" ↔ "PR") no choca con el UNIQUE.
        kept = [existing[d] for d in range(1, len(cleaned) + 1) if d in existing]
        for level in kept:
            level.name = f"~{level.depth}~{uuid.uuid4().hex[:8]}"
            level.save(update_fields=["name"])
        for depth, spec in enumerate(cleaned, start=1):
            level = existing.get(depth)
            if level is None:
                scoped_qs(ctx, LocationLevel).create(
                    org_id=ctx.org_id,
                    project=project,
                    depth=depth,
                    name=spec.name,
                    source_field=spec.source_field,
                )
            else:
                level.name, level.source_field = spec.name, spec.source_field
                level.save(update_fields=["name", "source_field"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_LEVELS_CHANGED,
            target=f"project:{project.pk}",
            metadata={
                "levels": [spec.name for spec in cleaned],
                "from_project": bool(cleaned) and bool(cleaned[0].source_field),
            },
        )


def level_named(levels: Iterable[LocationLevel], name: str) -> LocationLevel | None:
    key = node_key(name)
    return next((level for level in levels if node_key(level.name) == key), None)


@dataclass
class Tree:
    """Los nodos de un proyecto, en memoria."""

    project: Project
    nodes: dict[UUID, LocationNode] = field(default_factory=dict)
    _children: dict[tuple[UUID | None, str], UUID] = field(default_factory=dict)

    def _add(self, node: LocationNode) -> None:
        self.nodes[node.pk] = node
        self._children[(node.parent_id, node.key)] = node.pk

    def find(self, names: Sequence[str]) -> UUID | None:
        parent: UUID | None = None
        for name in names:
            found = self._children.get((parent, node_key(name)))
            if found is None:
                return None
            parent = found
        return parent

    def ensure(
        self, ctx: RequestContext, levels: Sequence[LocationLevel], names: Sequence[str]
    ) -> tuple[LocationNode, list[LocationNode]]:
        """El nodo de la ruta `names`, creando los que falten. Al menos un nombre."""
        if not names or len(names) > len(levels):
            raise LevelError("La ruta no coincide con los niveles del proyecto.")
        created: list[LocationNode] = []
        parent: LocationNode | None = None
        for level, name in zip(levels, names, strict=False):
            key = node_key(name)
            found = self._children.get((parent.pk if parent else None, key))
            if found is not None:
                node = self.nodes[found]
                if node.retired_at is not None:
                    node.retired_at = None
                    node.save(update_fields=["retired_at"])
            else:
                node_id = uuid.uuid4()
                node = scoped_qs(ctx, LocationNode).create(
                    id=node_id,
                    org_id=ctx.org_id,
                    project=self.project,
                    level=level,
                    parent=parent,
                    name=name[:MAX_NAME],
                    key=key[:MAX_NAME],
                    path=f"{parent.path if parent else '/'}{node_id.hex}/",
                )
                self._add(node)
                created.append(node)
            parent = node
        if parent is None:  # `names` no vacío: no ocurre
            raise LevelError("La ruta está vacía.")
        return parent, created

    def names(self, node_id: UUID) -> list[str]:
        node = self.nodes[node_id]
        ids = [UUID(hex=part) for part in node.path.strip("/").split("/")]
        return [self.nodes[i].name for i in ids if i in self.nodes]

    def label(self, node_id: UUID | None) -> str:
        if node_id is None or node_id not in self.nodes:
            return ""
        return SEPARATOR.join(self.names(node_id))

    def contains(self, ancestor_id: UUID, node_id: UUID) -> bool:
        """`node_id` es `ancestor_id` o está dentro de él."""
        ancestor, node = self.nodes.get(ancestor_id), self.nodes.get(node_id)
        return ancestor is not None and node is not None and node.path.startswith(ancestor.path)

    def descendants(self, node_id: UUID) -> list[UUID]:
        """El nodo y los vigentes que cuelgan de él, por etiqueta."""
        root = self.nodes[node_id]
        found = [
            n.pk
            for n in self.nodes.values()
            if n.path.startswith(root.path) and (n.retired_at is None or n.pk == node_id)
        ]
        return sorted(found, key=lambda pk: [fold(name) for name in self.names(pk)])


def load_tree(ctx: RequestContext, project: Project) -> Tree:
    tree = Tree(project=project)
    for node in (
        scoped_qs(ctx, LocationNode)
        .filter(project=project)
        .select_related("level")
        .order_by(Lower("name"))
    ):
        tree._add(node)
    return tree
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_locations.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): location levels and node tree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Campos personalizados de MS Project (`mspdi.py`)

**Files:**
- Modify: `server/visitacampo/projects/mspdi.py`
- Modify: `server/visitacampo/projects/tests/samples.py` (`task_xml`, `project_xml`)
- Test: `server/visitacampo/projects/tests/test_mspdi.py` (clase nueva al final)

- [ ] **Step 1: Ampliar los XML de prueba**

En `samples.py`, constantes nuevas después de `NS`:

```python
#: `FieldID` de los campos de texto personalizados de tarea de MS Project.
TEXT1, TEXT2, TEXT3 = "188743731", "188743734", "188743737"
```

`task_xml` gana el parámetro `custom: tuple[tuple[str, str], ...] = ()` (al final de la firma) y, justo antes de `return`, agrega:

```python
    for field_id, value in custom:
        parts.append(
            "<ExtendedAttribute>"
            f"{_el('FieldID', field_id)}{_el('Value', value)}"
            "</ExtendedAttribute>"
        )
```

`project_xml` gana `custom_fields: tuple[tuple[str, str, str], ...] = ()` (FieldID, FieldName, Alias) y, después de `MinutesPerDay`/`CalendarUID` y antes del calendario:

```python
    if custom_fields:
        body.append(
            "<ExtendedAttributes>"
            + "".join(
                "<ExtendedAttribute>"
                f"{_el('FieldID', fid)}{_el('FieldName', fname)}{_el('Alias', alias)}"
                "</ExtendedAttribute>"
                for fid, fname, alias in custom_fields
            )
            + "</ExtendedAttributes>"
        )
```

- [ ] **Step 2: Escribir la prueba que falla**

Al final de `projects/tests/test_mspdi.py`:

```python
class CustomFieldsTests(SimpleTestCase):
    def test_reads_definitions_and_task_values(self) -> None:
        from .samples import TEXT1, TEXT2, project_xml, task_xml

        data = project_xml(
            [
                task_xml(1, "Tramo 2", "1", summary=True),
                task_xml(2, "Excavación", "1.1", custom=((TEXT1, "Tramo 2"), (TEXT2, "PR 12+300"))),
            ],
            custom_fields=((TEXT1, "Text1", "Tramo"), (TEXT2, "Text2", "")),
        )
        parsed = parse_mspdi(data)
        self.assertEqual(
            [(f.field_id, f.label) for f in parsed.custom_fields],
            [(TEXT1, "Text1 (Tramo)"), (TEXT2, "Text2")],
        )
        excavation = next(t for t in parsed.tasks if t.uid == 2)
        self.assertEqual(
            dict(excavation.custom_values), {TEXT1: "Tramo 2", TEXT2: "PR 12+300"}
        )
        self.assertEqual(next(t for t in parsed.tasks if t.uid == 1).custom_values, ())

    def test_ignores_values_without_field_or_value(self) -> None:
        from .samples import project_xml

        task = (
            "<Task><UID>2</UID><ID>2</ID><Name>X</Name><OutlineNumber>1</OutlineNumber>"
            "<OutlineLevel>1</OutlineLevel>"
            "<ExtendedAttribute><FieldID>188743731</FieldID><ValueID>7</ValueID>"
            "</ExtendedAttribute>"
            "<ExtendedAttribute><Value>sin campo</Value></ExtendedAttribute></Task>"
        )
        parsed = parse_mspdi(project_xml([task]))
        self.assertEqual(parsed.tasks[0].custom_values, ())
        self.assertEqual(parsed.custom_fields, ())
```

(`SimpleTestCase` y `parse_mspdi` ya están importados en ese archivo; si no, importarlos.)

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_mspdi.py`
Expected: FAIL con `AttributeError: 'ParsedSchedule' object has no attribute 'custom_fields'`.

- [ ] **Step 4: Leer los campos en `mspdi.py`**

Después de `ParsedResource`:

```python
@dataclass(frozen=True)
class CustomField:
    """Un campo personalizado de tarea definido en el proyecto (p. ej. Text1 "Tramo")."""

    field_id: str
    field_name: str
    alias: str

    @property
    def label(self) -> str:
        return f"{self.field_name} ({self.alias})" if self.alias else self.field_name
```

`ParsedTask` gana, al final (con valor por defecto para no tocar a quien la construye):

```python
    #: Pares (FieldID, Value) de los campos personalizados con valor (costos, pieza 4).
    custom_values: tuple[tuple[str, str], ...] = ()
```

`ParsedSchedule` gana, al final:

```python
    #: Campos personalizados de tarea definidos en el proyecto, en el orden del archivo.
    custom_fields: tuple[CustomField, ...] = ()
```

Funciones nuevas (antes de `_resources`):

```python
#: Largo máximo que se conserva de un valor personalizado (la ubicación recorta a 120).
MAX_CUSTOM_VALUE = 255


def _custom_values(r: _Reader, el: Element) -> tuple[tuple[str, str], ...]:
    """Los `ExtendedAttribute` de una tarea con `FieldID` y `Value` (sin `ValueID` solo)."""
    values: list[tuple[str, str]] = []
    for attr in el.iterfind(r.q("ExtendedAttribute")):
        field_id, value = r.text(attr, "FieldID"), r.text(attr, "Value")
        if field_id and value:
            values.append((field_id, value[:MAX_CUSTOM_VALUE]))
    return tuple(values)


def _custom_fields(r: _Reader, root: Element) -> tuple[CustomField, ...]:
    fields: list[CustomField] = []
    seen: set[str] = set()
    for el in root.iterfind(r.path("ExtendedAttributes", "ExtendedAttribute")):
        field_id = r.text(el, "FieldID")
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)
        fields.append(
            CustomField(
                field_id=field_id,
                field_name=(r.text(el, "FieldName") or field_id)[:60],
                alias=(r.text(el, "Alias") or "")[:60],
            )
        )
    return tuple(fields)
```

En `_tasks`, dentro del `ParsedTask(...)`, agregar como último argumento:

```python
                custom_values=_custom_values(r, el),
```

En `parse_mspdi`, en el `return ParsedSchedule(...)`, agregar:

```python
        custom_fields=_custom_fields(r, root),
```

(`r.text` devuelve el texto sin espacios de los extremos o `None`; ver `_Reader.text`.)

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_mspdi.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects
git commit -m "feat(projects): read MS Project custom task fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Ubicación desde Project al importar el cronograma

**Files:**
- Modify: `server/visitacampo/projects/importer.py`
- Create: `server/visitacampo/matrix/tests/factories.py`
- Test: `server/visitacampo/matrix/tests/test_schedule_locations.py`

- [ ] **Step 1: Fábrica con ubicación**

`server/visitacampo/matrix/tests/factories.py`:

```python
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
    node, _ = load_tree(ctx, world.project).ensure(
        ctx, project_levels(ctx, world.project), names
    )
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
```

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_schedule_locations.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.models import ScheduleImport
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import load_tree
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.factories import make_field_world
from visitacampo.projects.tests.samples import TEXT1, project_xml, task_xml

from .factories import import_schedule, located_xml, plan, project_levels_from_xml, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ScheduleLocationTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()

    def _label(self, uid: int) -> str:
        task = self.world.task(uid)
        return load_tree(self.ctx, self.world.project).label(task.location_id)

    def test_summary_lists_custom_fields(self) -> None:
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=located_xml())
        self.assertEqual(
            staged.summary["custom_fields"],
            [
                {"field_id": TEXT1, "label": "Text1 (Tramo)"},
                {"field_id": "188743734", "label": "Text2 (PR)"},
            ],
        )

    def test_apply_assigns_leaf_locations(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        self.assertEqual(self._label(3), "Tramo 1")
        self.assertEqual(self._label(5), "Tramo 2 / PR 12+300")
        self.assertEqual(self._label(2), "")
        self.assertIsNone(self.world.task(4).location_id)  # resumen

    def test_preview_warns_changes_and_incomplete_paths(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        data = located_xml(tramo_3="Tramo 9", tramo_5="")
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=data)
        warnings = " ".join(staged.summary["warnings"])
        self.assertIn("2 tarea(s) cambian de ubicación", warnings)
        self.assertIn("1.2 Descapote: Tramo 1 → Tramo 9", warnings)
        self.assertIn("1 tarea(s) con la ubicación incompleta", warnings)
        apply_import(self.ctx, staged.pk)
        self.assertEqual(self._label(3), "Tramo 9")
        self.assertEqual(self._label(5), "")  # sin Tramo, el PR solo no sirve

    def test_missing_field_keeps_previous_locations(self) -> None:
        project_levels_from_xml(self.world)
        import_schedule(self.world, located_xml())
        plain = project_xml([task_xml(3, "Descapote", "1")])
        staged = stage_import(self.ctx, self.world.project, file_name="c.xml", data=plain)
        self.assertIn(
            "El archivo no trae los campos de ubicación Tramo, PR",
            " ".join(staged.summary["warnings"]),
        )
        apply_import(self.ctx, staged.pk)
        self.assertEqual(self._label(3), "Tramo 1")

    def test_suno_levels_do_not_touch_locations(self) -> None:
        suno_levels(self.world)
        import_schedule(self.world, located_xml())
        self.assertEqual(self._label(5), "")

    def test_retired_task_with_plan_is_warned(self) -> None:
        from .factories import budget_world, item

        world, budget = budget_world("Con plan")
        plan(world, 5, item(budget, "2.1"), "10")
        data = project_xml([task_xml(3, "Descapote", "1")])
        staged = stage_import(world.ctx(), world.project, file_name="c.xml", data=data)
        self.assertIn(
            "tarea(s) retirada(s) con cantidades planeadas: 2.1 Columnas",
            " ".join(staged.summary["warnings"]),
        )
        self.assertEqual(ScheduleImport.objects.filter(project=world.project).count(), 2)
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_schedule_locations.py`
Expected: FAIL con `KeyError: 'custom_fields'`.

- [ ] **Step 4: Ubicación en `projects/importer.py`**

Imports nuevos:

```python
from visitacampo.core.models import LocationLevel, QuantityPlan
from visitacampo.matrix.locations import (
    PathValues,
    Tree,
    from_project,
    load_tree,
    path_values,
    project_levels,
)
```

(`QuantityPlan` y `LocationLevel` se suman a la lista de `visitacampo.core.models` ya existente.)

Funciones nuevas, después de `summarize_changes`:

```python
def _leaf_paths(
    parsed: ParsedSchedule, levels: list[LocationLevel]
) -> dict[int, PathValues]:
    """Ruta de cada tarea hoja según los campos de Project de cada nivel."""
    return {
        task.uid: path_values([dict(task.custom_values).get(lvl.source_field) for lvl in levels])
        for task in parsed.tasks
        if not task.is_summary
    }


def _missing_fields(parsed: ParsedSchedule, levels: list[LocationLevel]) -> list[str]:
    defined = {f.field_id for f in parsed.custom_fields}
    used = {fid for task in parsed.tasks for fid, _ in task.custom_values}
    return [lvl.name for lvl in levels if lvl.source_field not in defined | used]


def location_warnings(
    ctx: RequestContext, project: Project, parsed: ParsedSchedule
) -> list[str]:
    """Avisos de ubicación para la vista previa (solo si la ubicación sale de Project)."""
    levels = project_levels(ctx, project)
    if not from_project(levels):
        return []
    missing = _missing_fields(parsed, levels)
    if missing:
        return [
            f"El archivo no trae los campos de ubicación {', '.join(missing)}: "
            "se conserva la ubicación actual de las tareas."
        ]
    tree = load_tree(ctx, project)
    current = dict(
        scoped_qs(ctx, Task).filter(project=project).values_list("uid", "location_id")
    )
    names = {t.uid: _label(t.outline_number, t.name) for t in parsed.tasks}
    changes: list[str] = []
    incomplete = truncated = 0
    for uid, path in _leaf_paths(parsed, levels).items():
        incomplete += path.incomplete
        truncated += path.truncated
        before = tree.label(current.get(uid))
        after = " / ".join(path.names)
        if before != after:
            changes.append(f"{names[uid]}: {before or 'sin ubicación'} → {after or 'sin ubicación'}")
    warnings: list[str] = []
    if changes:
        warnings.append(
            f"{len(changes)} tarea(s) cambian de ubicación: "
            + "; ".join(changes[:MAX_EXAMPLES])
            + "."
        )
    if incomplete:
        warnings.append(
            f"{incomplete} tarea(s) con la ubicación incompleta (un nivel vacío antes de otro "
            "con valor): quedan en el último nivel con valor."
        )
    if truncated:
        warnings.append(f"{truncated} nombre(s) de ubicación pasan de 120 caracteres y se recortan.")
    return warnings


def _apply_locations(
    ctx: RequestContext, project: Project, parsed: ParsedSchedule, tree: Tree
) -> None:
    levels = project_levels(ctx, project)
    if not from_project(levels) or _missing_fields(parsed, levels):
        return
    by_uid = {t.uid: t for t in scoped_qs(ctx, Task).filter(project=project)}
    changed: list[Task] = []
    for uid, path in _leaf_paths(parsed, levels).items():
        node_id = tree.ensure(ctx, levels, path.names)[0].pk if path.names else None
        task = by_uid[uid]
        if task.location_id != node_id:
            task.location_id = node_id
            changed.append(task)
    scoped_qs(ctx, Task).bulk_update(changed, ["location"], batch_size=500)
```

En `summarize_changes`, firma nueva con `plan_uids: Collection[int] = frozenset()` al final; junto a `retired_with_progress`:

```python
    retired_with_plan: list[str] = []
    ...
        if uid in plan_uids:
            retired_with_plan.append(label)
    ...
    if retired_with_plan:
        warnings.append(
            f"{len(retired_with_plan)} tarea(s) retirada(s) con cantidades planeadas: "
            + ", ".join(retired_with_plan[:MAX_EXAMPLES])
            + "."
        )
```

En `stage_import`, antes de crear el `ScheduleImport`:

```python
    plan_uids = set(
        scoped_qs(ctx, QuantityPlan)
        .filter(task__project=project)
        .values_list("task__uid", flat=True)
    )
    summary = summarize_changes(
        _existing(ctx, project), parsed, responsible_uids, progress_uids, plan_uids
    )
    summary["warnings"].extend(location_warnings(ctx, project, parsed))
    summary["custom_fields"] = [
        {"field_id": f.field_id, "label": f.label} for f in parsed.custom_fields
    ]
```

(reemplaza la línea `summary = summarize_changes(...)` actual).

En `apply_import`, después de `_apply_links(ctx, project, parsed)`:

```python
        _apply_locations(ctx, project, parsed, load_tree(ctx, project))
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix visitacampo/projects`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects/importer.py server/visitacampo/matrix
git commit -m "feat(projects): task locations from MS Project custom fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: Avance por cantidades (`progress.py` y `quantities.py`)

**Files:**
- Create: `server/visitacampo/matrix/progress.py`
- Create: `server/visitacampo/matrix/quantities.py`
- Modify: `server/visitacampo/projects/field_progress.py` (el % manual ignora visitas sin %)
- Modify: `server/visitacampo/projects/tests/factories.py` (`make_visit` acepta `quantities`)
- Modify: `server/visitacampo/matrix/tests/factories.py` (`quantity_visit`)
- Test: `server/visitacampo/matrix/tests/test_progress.py`, `server/visitacampo/matrix/tests/test_quantities.py`

- [ ] **Step 1: Fábricas de visitas con cantidades**

En `projects/tests/factories.py`, `make_visit` gana `quantities: list[dict[str, object]] | None = None` (al final de la firma) y en el `Visit.objects.create(...)` agrega `quantities=quantities or [],`.

Al final de `matrix/tests/factories.py`:

```python
def quantity_visit(
    world: FieldWorld,
    uid: int,
    *rows: dict[str, object],
    pct: str | None = None,
    hours: int = 0,
) -> Visit:
    """Visita del agente a la tarea `uid` con las cantidades `rows` (y opcionalmente un %)."""
    return make_visit(
        world.org,
        world.agent,
        task=world.task(uid),
        pct=pct,
        at=T0 + timedelta(hours=hours),
        quantities=list(rows),
    )
```

con los imports `from datetime import timedelta`, `from visitacampo.core.models import Visit` y `from visitacampo.projects.tests.factories import T0, make_visit`.

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/visitacampo/matrix/tests/test_progress.py`:

```python
from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.matrix.progress import PlanLine, task_progress

T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B = uuid.uuid4(), uuid.uuid4()


def line(task: uuid.UUID, item: uuid.UUID, planned: str, executed: str, price: str | None) -> PlanLine:
    return PlanLine(
        task_id=task,
        item_id=item,
        planned=Decimal(planned),
        executed=Decimal(executed),
        unit_price=None if price is None else Decimal(price),
    )


class TaskProgressTests(SimpleTestCase):
    def test_weighted_by_cost(self) -> None:
        # Concreto 10 m3 a 450.000 (4,5 M) y señales 100 m2 a 5.000 (0,5 M).
        result = task_progress(
            [line(T1, A, "10", "5", "450000"), line(T1, B, "100", "0", "5000")]
        )
        self.assertEqual(result[T1].pct, Decimal("45.00"))
        self.assertEqual(result[T1].value, Decimal("5000000.00"))

    def test_capped_at_100_and_negative_executed_counts_as_zero(self) -> None:
        result = task_progress([line(T1, A, "10", "15", "1"), line(T2, A, "10", "-3", "1")])
        self.assertEqual(result[T1].pct, Decimal("100.00"))
        self.assertEqual(result[T2].pct, Decimal("0.00"))

    def test_without_prices_uses_simple_average(self) -> None:
        result = task_progress([line(T1, A, "10", "5", None), line(T1, B, "4", "4", "0")])
        self.assertEqual(result[T1].pct, Decimal("75.00"))
        self.assertEqual(result[T1].value, Decimal("0.00"))

    def test_zero_planned_lines_do_not_measure_the_task(self) -> None:
        result = task_progress([line(T1, A, "0", "5", "100")])
        self.assertNotIn(T1, result)

    def test_rounding(self) -> None:
        result = task_progress([line(T1, A, "3", "1", "1")])
        self.assertEqual(result[T1].pct, Decimal("33.33"))
```

`server/visitacampo/matrix/tests/test_quantities.py`:

```python
from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.budgets.tests.factories import apply_rows
from visitacampo.budgets.tests.samples import V2_ROWS
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.quantities import (
    executed_by_pair,
    item_facts,
    measured_progress,
    suno_pct_by_task,
)

from .factories import budget_world, item, plan, quantity_row, quantity_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class QuantitiesTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")  # 450.000 / m3 en V1
        self.steel = item(self.budget, "2.2")  # 4.200 / kg en V1
        plan(self.world, 3, self.concrete, "10")
        plan(self.world, 3, self.steel, "500")

    def test_only_validated_visits_count(self) -> None:
        approved = quantity_visit(self.world, 3, quantity_row(self.concrete, "4"))
        self.world.approve(approved)
        rejected = quantity_visit(self.world, 3, quantity_row(self.concrete, "3"), hours=1)
        self.world.reject(rejected)
        quantity_visit(self.world, 3, quantity_row(self.concrete, "2"), hours=2)  # sin revisar
        executed = executed_by_pair(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(executed, {(self.world.task(3).pk, self.concrete.pk): Decimal("4")})

    def test_adjustments_add_up(self) -> None:
        for hours, qty in enumerate(("4", "-1")):
            visit = quantity_visit(
                self.world, 3, quantity_row(self.concrete, qty, note="ajuste"), hours=hours
            )
            self.world.approve(visit)
        executed = executed_by_pair(self.ctx, task_ids=[self.world.task(3).pk])
        self.assertEqual(executed[(self.world.task(3).pk, self.concrete.pk)], Decimal("3"))

    def test_measured_progress_and_suno_pct(self) -> None:
        visit = quantity_visit(
            self.world, 3, quantity_row(self.concrete, "5"), quantity_row(self.steel, "500")
        )
        self.world.approve(visit)
        manual = self.world.visit(2, "40")  # tarea sin plan: % manual
        self.world.approve(manual)
        # (5 × 450.000 + 500 × 4.200) / (10 × 450.000 + 500 × 4.200) = 4.350.000 / 6.600.000
        progress = measured_progress(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(progress[self.world.task(3).pk].pct, Decimal("65.91"))
        pct = suno_pct_by_task(self.ctx, project_ids=[self.world.project.pk])
        self.assertEqual(pct[self.world.task(3).pk], Decimal("65.91"))
        self.assertEqual(pct[self.world.task(2).pk], Decimal("40"))

    def test_quantities_override_manual_pct_on_planned_tasks(self) -> None:
        self.world.approve(self.world.visit(3, "90"))
        pct = suno_pct_by_task(self.ctx, task_ids=[self.world.task(3).pk])
        self.assertEqual(pct[self.world.task(3).pk], Decimal("0.00"))

    def test_item_facts_use_last_applied_version_containing_the_item(self) -> None:
        apply_rows(self.ctx, self.budget, V2_ROWS)  # 2.1 sube a 470.000; 1.2 se elimina
        removed = item(self.budget, "1.2")
        facts = item_facts(self.ctx, [self.concrete.pk, removed.pk])
        self.assertEqual(facts[self.concrete.pk].unit_price, Decimal("470000.00"))
        self.assertEqual(facts[removed.pk].unit_price, Decimal("20000.00"))
        self.assertEqual(facts[removed.pk].code, "1.2")

    def test_query_count(self) -> None:
        with self.assertNumQueries(4):
            suno_pct_by_task(self.ctx, project_ids=[self.world.project.pk])

    def test_empty_ids(self) -> None:
        self.assertEqual(suno_pct_by_task(self.ctx, task_ids=[]), {})
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_progress.py visitacampo/matrix/tests/test_quantities.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.matrix.progress'`.

- [ ] **Step 4: `progress.py` (puro)**

```python
"""Avance de una tarea por cantidades (costos, pieza 4). Puro.

Valor planeado V = Σ planeada × precio; valor ejecutado E = Σ max(ejecutada, 0) ×
precio, sobre los ítems del plan de la tarea con cantidad planeada > 0.
% = min(100, 100·E/V). Sin valor (ítems sin precio), el promedio simple de
ejecutada/planeada por ítem. Una tarea sin ítems con cantidad no se mide así.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

from visitacampo.budgets.totals import HUNDRED, WIDE_PRECISION, money

PCT_STEP = Decimal("0.01")
_ZERO = Decimal(0)


@dataclass(frozen=True)
class PlanLine:
    task_id: UUID
    item_id: UUID
    planned: Decimal
    #: Suma de las cantidades de las visitas validadas (puede ser negativa).
    executed: Decimal
    unit_price: Decimal | None


@dataclass(frozen=True)
class QuantityProgress:
    #: 0..100, a dos decimales.
    pct: Decimal
    #: Valor planeado de la tarea (a centavos): el peso en la consolidación.
    value: Decimal


def task_progress(lines: Iterable[PlanLine]) -> dict[UUID, QuantityProgress]:
    by_task: dict[UUID, list[PlanLine]] = defaultdict(list)
    for plan_line in lines:
        if plan_line.planned > 0:
            by_task[plan_line.task_id].append(plan_line)
    result: dict[UUID, QuantityProgress] = {}
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        for task_id, own in by_task.items():
            value = sum((ln.planned * (ln.unit_price or _ZERO) for ln in own), _ZERO)
            if value > 0:
                earned = sum(
                    (max(ln.executed, _ZERO) * (ln.unit_price or _ZERO) for ln in own), _ZERO
                )
                pct = earned * HUNDRED / value
            else:
                ratios = (max(ln.executed, _ZERO) * HUNDRED / ln.planned for ln in own)
                pct = sum(ratios, _ZERO) / len(own)
            result[task_id] = QuantityProgress(
                pct=min(HUNDRED, pct).quantize(PCT_STEP, rounding=ROUND_HALF_UP),
                value=money(value),
            )
    return result
```

(`HUNDRED`, `WIDE_PRECISION` y `money` existen en `budgets/totals.py`.)

- [ ] **Step 5: `quantities.py` (con BD)**

```python
"""Lecturas de la matriz con BD: lo ejecutado validado, los datos de cada ítem y el
% SUNO combinado (por cantidades donde hay plan, % manual en el resto)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import QuerySet

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    BudgetItemVersion,
    BudgetVersionStatus,
    QuantityPlan,
    Visit,
)
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.field_progress import approved, validated_pct_by_task

from .progress import PlanLine, QuantityProgress, task_progress

Pair = tuple[UUID, UUID]


def _ids(values: Iterable[UUID] | None) -> list[UUID] | None:
    return None if values is None else list(values)


def _by_scope(
    qs: QuerySet, task_ids: list[UUID] | None, project_ids: list[UUID] | None, *, task: str
) -> QuerySet:
    if task_ids is not None:
        qs = qs.filter(**{f"{task}_id__in": task_ids})
    if project_ids is not None:
        qs = qs.filter(**{f"{task}__project_id__in": project_ids})
    return qs


def executed_by_pair(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> dict[Pair, Decimal]:
    """(tarea, ítem) → suma de las cantidades de sus visitas validadas. Una query."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return {}
    visits = (
        scoped_qs(ctx, Visit)
        .filter(task__isnull=False, deleted_at__isnull=True)
        .exclude(quantities=[])
    )
    visits = _by_scope(visits, tasks, projects, task="task")
    totals: dict[Pair, Decimal] = defaultdict(Decimal)
    for task_id, rows in approved(visits).values_list("task_id", "quantities"):
        for row in rows:
            totals[(task_id, UUID(row["item_id"]))] += Decimal(row["quantity"])
    return dict(totals)


@dataclass(frozen=True)
class ItemFacts:
    code: str
    kind: str
    description: str
    unit: str
    unit_price: Decimal | None


def item_facts(ctx: RequestContext, item_ids: Iterable[UUID]) -> dict[UUID, ItemFacts]:
    """Datos de cada ítem en la última versión aplicada que lo contiene. Una query."""
    ids = list(set(item_ids))
    if not ids:
        return {}
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(item_id__in=ids, version__status=BudgetVersionStatus.APPLIED)
        .order_by("item_id", "-version__number")
        .distinct("item_id")
        .values_list("item_id", "item__code", "kind", "description", "unit", "unit_price")
    )
    return {
        item_id: ItemFacts(code, kind, description, unit, price)
        for item_id, code, kind, description, unit, price in rows
    }


def plan_lines(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> list[PlanLine]:
    """Las filas del plan con lo ejecutado validado y el precio. Tres queries."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return []
    plans = _by_scope(scoped_qs(ctx, QuantityPlan), tasks, projects, task="task")
    rows = list(plans.values_list("task_id", "item_id", "planned_quantity"))
    if not rows:
        return []
    executed = executed_by_pair(ctx, task_ids=tasks, project_ids=projects)
    facts = item_facts(ctx, (item_id for _, item_id, _ in rows))
    return [
        PlanLine(
            task_id=task_id,
            item_id=item_id,
            planned=planned,
            executed=executed.get((task_id, item_id), Decimal(0)),
            unit_price=facts[item_id].unit_price if item_id in facts else None,
        )
        for task_id, item_id, planned in rows
    ]


def measured_progress(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
) -> dict[UUID, QuantityProgress]:
    return task_progress(plan_lines(ctx, task_ids=task_ids, project_ids=project_ids))


def suno_pct_by_task(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
    measured: dict[UUID, QuantityProgress] | None = None,
) -> dict[UUID, Decimal]:
    """% SUNO: por cantidades en las tareas con plan; el último % validado en el resto.

    `measured`: el avance por cantidades ya calculado (para no repetir las queries)."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return {}
    manual = validated_pct_by_task(scoped_qs(ctx, Visit), task_ids=tasks, project_ids=projects)
    if measured is None:
        measured = measured_progress(ctx, task_ids=tasks, project_ids=projects)
    return {**manual, **{task_id: p.pct for task_id, p in measured.items()}}
```

- [ ] **Step 6: El % manual ignora las visitas sin %**

Una visita solo con cantidades tiene `progress_pct` nulo: no debe borrar el último % manual. En `projects/field_progress.py`:

- en `validated_pct_by_task`, `linked = visits.filter(task__isnull=False, deleted_at__isnull=True)` pasa a `visits.filter(task__isnull=False, progress_pct__isnull=False, deleted_at__isnull=True)`;
- en `with_previous_validated_pct`, dentro de `base.filter(...)`, agregar `progress_pct__isnull=False,`.

Agregar en `projects/tests/test_field_progress.py` una prueba: una visita validada con %, luego otra validada solo con cantidades (crearla con `make_visit(..., task=..., pct=None, quantities=[{"item_id": str(uuid.uuid4()), "location_id": None, "quantity": "1.0000", "note": ""}])`) → `validated_pct_by_task` sigue devolviendo el primer %.

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix visitacampo/projects`
Expected: PASS. Si `assertNumQueries(4)` da otro número, revisar que `plan_lines` haga 3 queries (plan, ejecutado, ítems) y `validated_pct_by_task` 1.

- [ ] **Step 8: Commit**

```bash
git add server/visitacampo/matrix server/visitacampo/projects
git commit -m "feat(matrix): quantity-based progress from validated visits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: % SUNO combinado y consolidación por valor en el cronograma

**Files:**
- Modify: `server/visitacampo/projects/progress.py` (`TaskInput`, `TaskIndicators`, `rollup`, `_leaf`)
- Modify: `server/visitacampo/projects/schedule.py` (`build_schedule`)
- Modify: `server/visitacampo/projects/views.py` (`project_list`, `task_detail`)
- Modify: `server/visitacampo/projects/field_tasks.py` (`last_validated_pct`)
- Test: `server/visitacampo/projects/tests/test_progress.py`, `server/visitacampo/matrix/tests/test_schedule_progress.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `projects/tests/test_progress.py` (usa los mismos ayudantes del archivo para el calendario y la fecha de corte; si no hay, construir `TaskIndicators` directamente como abajo):

```python
class RollupByValueTests(SimpleTestCase):
    def _child(self, actual: float, weight: int, value: float | None) -> TaskIndicators:
        source = SourceIndicators(actual_pct=actual, delay_pct=0.0, delay_days=None)
        return TaskIndicators(
            planned_pct=50.0, weight=weight, suno=source, project=source, value=value
        )

    def test_weights_by_value_when_every_child_has_it(self) -> None:
        total = rollup([self._child(0.0, 10, 900.0), self._child(100.0, 10, 100.0)])
        assert total is not None
        self.assertEqual(total.suno.actual_pct, 10.0)
        self.assertEqual(total.value, 1000.0)
        self.assertEqual(total.weight, 20)

    def test_falls_back_to_days_if_a_child_has_no_value(self) -> None:
        total = rollup([self._child(0.0, 10, 900.0), self._child(100.0, 30, None)])
        assert total is not None
        self.assertEqual(total.suno.actual_pct, 75.0)
        self.assertIsNone(total.value)
```

(`assert` en pruebas está permitido: bandit excluye `*/tests/*`.)

`server/visitacampo/matrix/tests/test_schedule_progress.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.field_tasks import field_tasks
from visitacampo.projects.schedule import build_schedule

from .factories import budget_world, item, plan, quantity_row, quantity_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ScheduleProgressTests(TestCase):
    def setUp(self) -> None:
        self.world, budget = budget_world()
        self.concrete = item(budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        visit = quantity_visit(self.world, 3, quantity_row(self.concrete, "4"))
        self.world.approve(visit)

    def test_schedule_uses_quantity_pct(self) -> None:
        schedule = build_schedule(self.world.ctx(), self.world.project)
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 40.0)
        self.assertEqual(row.indicators.value, 4500000.0)

    def test_field_tasks_report_quantity_pct(self) -> None:
        self.world.responsible(3)
        items = field_tasks(self.world.ctx(self.world.agent))
        self.assertEqual(items[0]["last_validated_pct"], 40.0)

    def test_task_page_shows_quantity_pct(self) -> None:
        self.client.force_login(self.world.admin)
        response = self.client.get(
            f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"
        )
        self.assertContains(response, "40 %")
        self.assertContains(response, "por cantidades")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_progress.py visitacampo/matrix/tests/test_schedule_progress.py`
Expected: FAIL con `TypeError: TaskIndicators.__init__() got an unexpected keyword argument 'value'`.

- [ ] **Step 3: Consolidación por valor (`projects/progress.py`)**

`TaskInput` gana, al final:

```python
    #: Valor planeado por cantidades (costos, pieza 4); None si la tarea no tiene plan.
    value: float | None = None
```

`TaskIndicators` gana, al final:

```python
    #: Valor planeado: suma de los hijos cuando todos lo tienen; None si no.
    value: float | None = None
```

`rollup` queda:

```python
def rollup(children: Sequence[TaskIndicators]) -> TaskIndicators | None:
    """Promedio ponderado de los hijos: por valor planeado si todos lo tienen (y suma
    más que 0), si no por días hábiles de línea base."""
    if not children:
        return None
    values = [c.value for c in children]
    by_value = all(v is not None and v > 0 for v in values)
    weights = [float(v or 0) for v in values] if by_value else [float(c.weight) for c in children]
    total = sum(weights)

    def avg(items: list[float]) -> float:
        if total == 0:
            return sum(items) / len(items)
        return sum(v * w for v, w in zip(items, weights, strict=True)) / total

    planned = avg([c.planned_pct for c in children])

    def source(name: str) -> SourceIndicators:
        parts = [getattr(c, name) for c in children]
        actual = avg([p.actual_pct for p in parts])
        delays = [p.delay_days for p in parts if p.delay_days is not None]
        return SourceIndicators(
            actual_pct=round(actual, 1),
            delay_pct=round(planned - actual, 1),
            delay_days=max(delays) if delays else None,
        )

    return TaskIndicators(
        planned_pct=round(planned, 1),
        weight=sum(c.weight for c in children),
        suno=source("suno"),
        project=source("project"),
        value=sum(weights) if by_value else None,
    )
```

En `_leaf`, el `return TaskIndicators(...)` gana `value=task.value,`.

- [ ] **Step 4: `build_schedule` con el % combinado y el valor**

En `projects/schedule.py`, reemplazar el import de `validated_pct_by_task` por:

```python
from visitacampo.matrix.quantities import measured_progress, suno_pct_by_task
```

y el bloque desde `inputs = [` hasta `suno = {...}` por:

```python
    measured = measured_progress(ctx, project_ids=[project.pk])
    inputs = [
        TaskInput(
            key=str(t.pk),
            parent_key=str(t.parent_id) if t.parent_id else None,
            is_milestone=t.is_milestone,
            baseline_start=t.baseline_start,
            baseline_finish=t.baseline_finish,
            finish=t.finish,
            actual_finish=t.actual_finish,
            project_pct=float(t.project_pct) if t.project_pct is not None else None,
            value=float(measured[t.pk].value) if t.pk in measured else None,
        )
        for t in tasks
    ]
    if suno_pct is None:
        suno_pct = suno_pct_by_task(ctx, project_ids=[project.pk], measured=measured)
    suno = {str(k): float(v) for k, v in suno_pct.items()}
```

Actualizar el docstring: "Sin él se calcula con `matrix.quantities.suno_pct_by_task` (por cantidades donde hay plan, el último % validado en el resto)". Quitar el import de `Visit` si queda sin uso.

- [ ] **Step 5: Lista de proyectos, ficha de tarea y tareas de campo**

En `projects/views.py`:

- import: `from visitacampo.matrix.quantities import measured_progress, suno_pct_by_task` y `validated_pct_by_task` sale del import de `.field_progress`.
- `project_list`: `suno_pct = validated_pct_by_task(scoped_qs(ctx, Visit), project_ids=[...])` pasa a `suno_pct = suno_pct_by_task(ctx, project_ids=[project.pk for project in projects])`.
- `task_detail`: reemplazar las dos líneas de `suno`/`suno_text` por:

```python
    measured = measured_progress(ctx, task_ids=[task.pk])
    suno = suno_pct_by_task(ctx, task_ids=[task.pk], measured=measured).get(task.pk)
    suno_text = format_pct(suno) if suno is not None else "—"
```

y sumar al contexto `"measured_by_quantities": task.pk in measured,`.

En `core/templates/suno/projects/task.html`, la línea del % pasa a:

```html
  <p>% SUNO ({% if measured_by_quantities %}por cantidades{% else %}última visita validada{% endif %}): <strong>{{ suno_pct_text }}</strong></p>
```

En `projects/field_tasks.py`: `from visitacampo.matrix.quantities import suno_pct_by_task` reemplaza al import de `validated_pct_by_task`, y `pct = validated_pct_by_task(scoped_qs(ctx, Visit), task_ids=[t.pk for t in tasks])` pasa a `pct = suno_pct_by_task(ctx, task_ids=[t.pk for t in tasks])` (quitar el import de `Visit` si queda sin uso).

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects visitacampo/matrix`
Expected: PASS. Las pruebas existentes de `assertNumQueries` en `projects/tests/test_api.py` y `test_views.py` suben en 3 queries (las de `plan_lines`): actualizar esos números y anotar en el commit el motivo.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/projects server/visitacampo/matrix server/visitacampo/core/templates
git commit -m "feat(projects): SUNO progress by quantities and value-weighted rollup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: Plantilla Excel — escribir y leer (`template.py`)

**Files:**
- Create: `server/visitacampo/matrix/template.py`
- Test: `server/visitacampo/matrix/tests/test_template.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_template.py`:

```python
from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.matrix.template import (
    ItemRef,
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


def parse(rows: list[list[object]], *, editable: bool = True, budgets: int = 1) -> object:
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
                TemplateRow(3, "1.2", "Descapote", ("Tramo 1", ""), "Contrato de obra",
                            "1.1", "Localización", "m2", Decimal("12.5")),
                TemplateRow(5, "2.1", "Columnas", ("Tramo 2", "PR 1"), "", "", "", "", None),
            ],
        )
        rows = read_template(data)
        self.assertEqual(list(rows[0][:11]), HEADER)
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
        rows = [
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
        rows = [
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
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_template.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.matrix.template'`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/template.py`:

```python
"""Plantilla Excel de la matriz: escribirla y leerla. Puro.

Encabezado en la fila 1; las columnas se encuentran por su texto (sin importar
mayúsculas ni tildes), así que el usuario puede reordenarlas. Cada fila es un
par tarea × ítem con su cantidad planeada; una fila de una tarea sin ítem solo
sirve para su ubicación.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from visitacampo.backoffice.export import safe_cell, to_xlsx
from visitacampo.budgets.excel import (
    MAX_QUANTITY,
    ExcelRejected,
    check_file_name,
    preview_sheet,
    read_rows,
)
from visitacampo.budgets.mapping import normalize_header
from visitacampo.budgets.totals import parse_number_detail, quantity

from .locations import clean_name, node_key, path_values

SHEET = "Matriz"
H_UID = "UniqueID"
H_WBS = "EDT"
H_TASK = "Tarea"
H_BUDGET = "Presupuesto"
H_CODE = "Código de ítem"
H_DESCRIPTION = "Descripción"
H_UNIT = "Unidad"
H_QUANTITY = "Cantidad planeada"
MAX_PLAN_ROWS = 20_000
MAX_ERRORS = 50

__all__ = [
    "ItemRef",
    "ParsedPlan",
    "TaskRef",
    "TemplateRejected",
    "TemplateRow",
    "build_template",
    "check_file_name",
    "header",
    "parse_plan",
    "read_template",
]


class TemplateRejected(Exception):
    """El archivo no se puede aplicar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0])


def header(level_names: Sequence[str]) -> list[str]:
    return [
        H_UID,
        H_WBS,
        H_TASK,
        *level_names,
        H_BUDGET,
        H_CODE,
        H_DESCRIPTION,
        H_UNIT,
        H_QUANTITY,
    ]


@dataclass(frozen=True)
class TemplateRow:
    uid: int
    wbs: str
    task: str
    #: Un valor por nivel ("" si no hay).
    location: tuple[str, ...]
    budget: str
    code: str
    description: str
    unit: str
    quantity: Decimal | None


def build_template(level_names: Sequence[str], rows: Iterable[TemplateRow]) -> bytes:
    def values(row: TemplateRow) -> list[Any]:
        texts = [row.wbs, row.task, *row.location, row.budget, row.code, row.description, row.unit]
        return [
            row.uid,
            *(safe_cell(text) for text in texts),
            "" if row.quantity is None else float(row.quantity),
        ]

    return to_xlsx(header(level_names), (values(row) for row in rows))


def read_template(data: bytes) -> list[tuple[Any, ...]]:
    """Las filas de la hoja `Matriz` (o la primera), encabezado incluido."""
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        return read_rows(data, sheet, max_rows=MAX_PLAN_ROWS + 1)
    except ExcelRejected as exc:
        raise TemplateRejected([str(exc)]) from exc


@dataclass(frozen=True)
class TaskRef:
    id: UUID
    #: "1.2 Descapote", para los mensajes.
    label: str
    is_summary: bool
    retired: bool


@dataclass(frozen=True)
class ItemRef:
    id: UUID
    kind: str


@dataclass(frozen=True)
class ParsedPlan:
    pairs: dict[tuple[UUID, UUID], Decimal]
    #: Solo si la ubicación se asigna en SUNO: tarea → nombres de su ruta (vacía = sin ubicación).
    locations: dict[UUID, tuple[str, ...]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _uid(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _columns(first: Sequence[Any], level_names: Sequence[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, cell in enumerate(first):
        if _present(cell):
            found.setdefault(normalize_header(cell), index)
    wanted = {
        "uid": H_UID,
        "budget": H_BUDGET,
        "code": H_CODE,
        "quantity": H_QUANTITY,
    } | {f"level:{i}": name for i, name in enumerate(level_names)}
    columns = {
        key: found[normalize_header(name)]
        for key, name in wanted.items()
        if normalize_header(name) in found
    }
    missing = [wanted[key] for key in ("uid", "code", "quantity") if key not in columns]
    if missing:
        raise TemplateRejected(
            [f"Falta la columna «{name}» en la fila 1." for name in missing]
        )
    return columns


def parse_plan(
    rows: Sequence[Sequence[Any]],
    *,
    level_names: Sequence[str],
    editable_locations: bool,
    tasks: Mapping[int, TaskRef],
    items: Mapping[tuple[str, str], ItemRef],
    budget_names: Sequence[str],
) -> ParsedPlan:
    """Interpreta la plantilla. `items` va por (`node_key` del presupuesto, código)."""
    if not rows:
        raise TemplateRejected(["La hoja está vacía."])
    columns = _columns(rows[0], level_names)
    level_columns = [columns.get(f"level:{i}") for i in range(len(level_names))]
    single = node_key(budget_names[0]) if len(budget_names) == 1 else None

    def cell(row: Sequence[Any], key: str | int | None) -> Any:
        index = columns.get(key) if isinstance(key, str) else key
        return row[index] if index is not None and index < len(row) else None

    errors: list[str] = []
    pairs: dict[tuple[UUID, UUID], Decimal] = {}
    locations: dict[UUID, tuple[str, ...]] = {}
    dropped = dot_rows = truncated = 0

    for number, row in enumerate(rows[1:], start=2):
        if not any(_present(value) for value in row):
            continue
        uid = _uid(cell(row, "uid"))
        if uid is None:
            errors.append(f"Fila {number}: falta el UniqueID o no es un número entero.")
            continue
        task = tasks.get(uid)
        if task is None:
            errors.append(f"Fila {number}: la tarea con UniqueID {uid} no existe en el cronograma.")
            continue
        if task.retired:
            errors.append(f"Fila {number}: la tarea {task.label} está retirada del cronograma.")
            continue
        if task.is_summary:
            errors.append(
                f"Fila {number}: {task.label} es una tarea resumen; "
                "la matriz va en tareas de detalle."
            )
            continue
        if editable_locations:
            path = path_values([cell(row, index) for index in level_columns])
            if path.incomplete:
                errors.append(
                    f"Fila {number}: la ubicación tiene un nivel vacío antes de otro con valor."
                )
                continue
            truncated += path.truncated
            previous = locations.get(task.id)
            if previous is not None and [node_key(n) for n in previous] != [
                node_key(n) for n in path.names
            ]:
                errors.append(
                    f"Fila {number}: la tarea {task.label} tiene otra ubicación en una fila "
                    "anterior."
                )
                continue
            locations.setdefault(task.id, path.names)

        code = clean_name(cell(row, "code"))
        raw_quantity = cell(row, "quantity")
        if not code:
            if _present(raw_quantity):
                errors.append(f"Fila {number}: hay cantidad pero falta el código del ítem.")
            continue
        budget_name = clean_name(cell(row, "budget"))
        budget_key = node_key(budget_name) if budget_name else single
        if budget_key is None:
            errors.append(
                f"Fila {number}: falta el presupuesto (el proyecto tiene varios presupuestos)."
            )
            continue
        ref = items.get((budget_key, code))
        if ref is None:
            where = f"«{budget_name}»" if budget_name else "del proyecto"
            errors.append(
                f"Fila {number}: el ítem {code} no existe en el presupuesto vigente {where}."
            )
            continue
        if ref.kind != "item":
            errors.append(f"Fila {number}: {code} es un capítulo; la matriz va en ítems.")
            continue
        if not _present(raw_quantity):
            dropped += 1
            continue
        value, dot = parse_number_detail(raw_quantity)
        if value is None:
            errors.append(f"Fila {number}: la cantidad «{raw_quantity}» no es un número.")
            continue
        if value < 0:
            errors.append(f"Fila {number}: la cantidad no puede ser negativa.")
            continue
        if value >= MAX_QUANTITY:
            errors.append(f"Fila {number}: la cantidad es demasiado grande.")
            continue
        pair = (task.id, ref.id)
        if pair in pairs:
            errors.append(f"Fila {number}: el par {task.label} × {code} está repetido.")
            continue
        pairs[pair] = quantity(value)
        dot_rows += dot

    if errors:
        extra = len(errors) - MAX_ERRORS
        raise TemplateRejected(
            errors[:MAX_ERRORS] + ([f"… y {extra} error(es) más."] if extra > 0 else [])
        )
    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} fila(s) con ítem y sin cantidad: esos pares se eliminan de la matriz."
        )
    if dot_rows:
        warnings.append(
            f"{dot_rows} cantidad(es) escritas como texto con un punto seguido de 3 dígitos "
            "se leyeron como miles (\"1.234\" = 1234)."
        )
    if truncated:
        warnings.append(f"{truncated} nombre(s) de ubicación pasan de 120 caracteres y se recortan.")
    return ParsedPlan(pairs=pairs, locations=locations, warnings=tuple(warnings))
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_template.py`
Expected: PASS. Si `to_xlsx` nombra la hoja distinto de `Matriz`, `read_template` toma la primera hoja (por eso el `round_trip` funciona igual).

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): Excel template writer and reader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Diferencias y totales por ítem (`diff.py`)

**Files:**
- Create: `server/visitacampo/matrix/diff.py`
- Test: `server/visitacampo/matrix/tests/test_diff.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_diff.py`:

```python
from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.matrix.diff import BudgetedItem, item_totals, summarize_plan

T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B, C, R = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NAMES = {T1: "1.2 Descapote", T2: "2.1 Columnas", A: "1.1", B: "2.1", C: "2.2", R: "1.2"}


def label(pair: tuple[uuid.UUID, uuid.UUID]) -> str:
    return f"{NAMES[pair[0]]} × {NAMES[pair[1]]}"


class SummarizeTests(SimpleTestCase):
    def test_groups(self) -> None:
        current = {(T1, A): Decimal("10"), (T1, B): Decimal("5"), (T2, B): Decimal("1"),
                   (T2, C): Decimal("3")}
        new = {(T1, A): Decimal("10"), (T1, B): Decimal("6"), (T2, A): Decimal("2")}
        summary = summarize_plan(current, new, executed={(T2, C)}, label=label)
        self.assertEqual(
            (summary["added"], summary["changed"], summary["removed"], summary["kept_zero"]),
            (1, 1, 1, 1),
        )
        self.assertEqual(summary["examples"]["changed"], ["1.2 Descapote × 2.1: 5 → 6"])
        self.assertEqual(summary["examples"]["kept_zero"], ["2.1 Columnas × 2.2"])
        self.assertEqual(summary["examples"]["removed"], ["2.1 Columnas × 2.1"])


class ItemTotalsTests(SimpleTestCase):
    def test_statuses(self) -> None:
        budgeted = {
            A: BudgetedItem("1.1", "Localización", "m2", Decimal("100")),
            B: BudgetedItem("2.1", "Concreto", "m3", Decimal("10")),
            C: BudgetedItem("2.2", "Acero", "kg", Decimal("800")),
        }
        retired = {R: BudgetedItem("1.2", "Cerramiento", "ml", None)}
        totals = item_totals(
            {A: Decimal("100"), B: Decimal("12"), R: Decimal("5")}, budgeted, retired
        )
        self.assertEqual(
            totals["counts"], {"ok": 1, "over": 1, "under": 1, "retired": 1, "unassigned": 1}
        )
        rows = {row["code"]: row for row in totals["items"]}
        self.assertEqual(rows["2.1"]["status"], "over")
        self.assertEqual(rows["2.1"]["diff"], "2.0000")
        self.assertEqual(rows["2.2"]["status"], "under")
        self.assertEqual(rows["2.2"]["planned"], "0.0000")
        self.assertEqual(rows["1.2"]["status"], "retired")
        self.assertNotIn("1.1", rows)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_diff.py`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/diff.py`:

```python
"""Qué cambia en la matriz y cómo queda cada ítem frente al presupuesto. Puro.

Todo lo que va al `summary` (JSON) son textos: las cantidades como decimales con
4 decimales, para no perder exactitud.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from visitacampo.budgets.totals import QUANTITY_STEP

Pair = tuple[UUID, UUID]
MAX_EXAMPLES = 20
MAX_ITEM_ROWS = 300


def _q(value: Decimal) -> str:
    return str(value.quantize(QUANTITY_STEP))


def _plain(value: Decimal) -> str:
    """`Decimal("5.0000")` → "5"; `Decimal("2.5000")` → "2,5" (para los ejemplos)."""
    return f"{value.normalize():f}".replace(".", ",")


def summarize_plan(
    current: Mapping[Pair, Decimal],
    new: Mapping[Pair, Decimal],
    *,
    executed: Collection[Pair],
    label: Callable[[Pair], str],
) -> dict[str, Any]:
    """Pares nuevos, modificados y eliminados. Un par eliminado con ejecución se
    conserva con planeado 0 (`kept_zero`) en vez de borrarse."""
    added = sorted((p for p in new if p not in current), key=label)
    changed = sorted((p for p in new if p in current and new[p] != current[p]), key=label)
    gone = sorted((p for p in current if p not in new), key=label)
    kept = [p for p in gone if p in executed]
    removed = [p for p in gone if p not in executed]
    return {
        "added": len(added),
        "changed": len(changed),
        "removed": len(removed),
        "kept_zero": len(kept),
        "examples": {
            "added": [f"{label(p)}: {_plain(new[p])}" for p in added[:MAX_EXAMPLES]],
            "changed": [
                f"{label(p)}: {_plain(current[p])} → {_plain(new[p])}"
                for p in changed[:MAX_EXAMPLES]
            ],
            "removed": [label(p) for p in removed[:MAX_EXAMPLES]],
            "kept_zero": [label(p) for p in kept[:MAX_EXAMPLES]],
        },
    }


@dataclass(frozen=True)
class BudgetedItem:
    code: str
    description: str
    unit: str
    #: Cantidad en la versión vigente; None si el ítem ya no está en ella.
    quantity: Decimal | None


def item_totals(
    planned: Mapping[UUID, Decimal],
    budgeted: Mapping[UUID, BudgetedItem],
    retired: Mapping[UUID, BudgetedItem],
) -> dict[str, Any]:
    """Presupuestado / planeado / diferencia por ítem. Lista solo los que no cuadran.

    `budgeted`: los ítems de las versiones vigentes; `retired`: ítems con plan que ya
    no están en su versión vigente."""
    counts = {"ok": 0, "over": 0, "under": 0, "retired": 0, "unassigned": 0}
    rows: list[dict[str, Any]] = []
    for item_id, info in budgeted.items():
        quantity = info.quantity or Decimal(0)
        total = planned.get(item_id, Decimal(0))
        if total == 0 and quantity > 0:
            counts["unassigned"] += 1
        diff = total - quantity
        status = "over" if diff > 0 else "under" if diff < 0 else "ok"
        counts[status] += 1
        if status != "ok":
            rows.append(
                {
                    "code": info.code,
                    "description": info.description,
                    "unit": info.unit,
                    "budgeted": _q(quantity),
                    "planned": _q(total),
                    "diff": _q(diff),
                    "status": status,
                }
            )
    for item_id, info in retired.items():
        if planned.get(item_id, Decimal(0)) == 0:
            continue
        counts["retired"] += 1
        rows.append(
            {
                "code": info.code,
                "description": info.description,
                "unit": info.unit,
                "budgeted": None,
                "planned": _q(planned[item_id]),
                "diff": None,
                "status": "retired",
            }
        )
    rows.sort(key=lambda row: code_key(row["code"]))
    return {"counts": counts, "items": rows[:MAX_ITEM_ROWS]}


def code_key(code: str) -> list[tuple[int, int | str]]:
    """Orden por código: "2.10" después de "2.9"; segmentos con letras, después."""
    return [(0, int(p)) if p.isdigit() else (1, p) for p in code.split(".")]
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_diff.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): plan diff and per-item totals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Importación de la matriz — subir, aplicar, descartar, descargar plantilla

**Files:**
- Create: `server/visitacampo/matrix/importer.py`
- Test: `server/visitacampo/matrix/tests/test_importer.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_importer.py`:

```python
from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.budgets.tests.factories import apply_rows
from visitacampo.budgets.tests.samples import V2_ROWS
from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.core.models import (
    AuditEvent,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.importer import (
    PlanRejected,
    apply_plan,
    discard_plan,
    stage_plan,
    template_bytes,
)
from visitacampo.matrix.locations import load_tree
from visitacampo.matrix.template import header, read_template

from .factories import (
    budget_world,
    import_schedule,
    item,
    plan,
    quantity_row,
    quantity_visit,
    suno_levels,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

HEADER = header(["Tramo", "PR"])


def sheet(*rows: list[object]) -> bytes:
    return build_xlsx(rows, header=HEADER, sheet="Matriz")


@skip_without_db
class ImporterTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)

    def _stage(self, data: bytes) -> QuantityPlanImport:
        return stage_plan(self.ctx, self.world.project, file_name="m.xlsx", data=data)

    def test_stage_and_apply(self) -> None:
        staged = self._stage(
            sheet(
                [3, "", "", "Tramo 1", "", "", "1.1", "", "", 60],
                [3, "", "", "Tramo 1", "", "", "2.1", "", "", 4],
                [2, "", "", "Tramo 2", "PR 1", "", "", "", "", None],
            )
        )
        self.assertEqual(staged.status, QuantityPlanImportStatus.PENDING)
        self.assertEqual(staged.summary["added"], 2)
        self.assertEqual(staged.summary["new_nodes"], 3)
        self.assertEqual(staged.summary["item_counts"]["under"], 5)
        applied = apply_plan(self.ctx, staged.pk)
        self.assertEqual(applied.number, 1)
        self.assertEqual(
            dict(QuantityPlan.objects.values_list("item__code", "planned_quantity")),
            {"1.1": Decimal("60.0000"), "2.1": Decimal("4.0000")},
        )
        tree = load_tree(self.ctx, self.world.project)
        self.assertEqual(tree.label(self.world.task(2).location_id), "Tramo 2 / PR 1")
        self.assertEqual(tree.label(self.world.task(3).location_id), "Tramo 1")
        self.assertTrue(AuditEvent.objects.filter(action="matrix.import_applied").exists())

    def test_replace_keeps_executed_pairs_at_zero(self) -> None:
        concrete, steel = item(self.budget, "2.1"), item(self.budget, "2.2")
        plan(self.world, 3, concrete, "10")
        plan(self.world, 3, steel, "100")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(concrete, "2")))
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        self.assertEqual(
            (staged.summary["added"], staged.summary["removed"], staged.summary["kept_zero"]),
            (1, 1, 1),
        )
        apply_plan(self.ctx, staged.pk)
        self.assertEqual(
            dict(QuantityPlan.objects.values_list("item__code", "planned_quantity")),
            {"1.1": Decimal("1.0000"), "2.1": Decimal("0.0000")},
        )

    def test_errors_reject_the_upload(self) -> None:
        with self.assertRaises(PlanRejected) as caught:
            self._stage(sheet([99, "", "", "", "", "", "1.1", "", "", 1]))
        self.assertIn("UniqueID 99", caught.exception.errors[0])
        self.assertFalse(QuantityPlanImport.objects.exists())

    def test_not_xlsx(self) -> None:
        with self.assertRaises(PlanRejected):
            stage_plan(self.ctx, self.world.project, file_name="m.xls", data=b"x")

    def test_new_upload_discards_previous_pending(self) -> None:
        first = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 2]))
        first.refresh_from_db()
        self.assertEqual(first.status, QuantityPlanImportStatus.DISCARDED)

    def test_stale_preview(self) -> None:
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        apply_rows(self.ctx, self.budget, V2_ROWS)
        with self.assertRaisesMessage(PlanRejected, "Vuelve a subir"):
            apply_plan(self.ctx, staged.pk)

    def test_discard(self) -> None:
        staged = self._stage(sheet([3, "", "", "", "", "", "1.1", "", "", 1]))
        discard_plan(self.ctx, staged.pk)
        staged.refresh_from_db()
        self.assertEqual(staged.status, QuantityPlanImportStatus.DISCARDED)
        with self.assertRaisesMessage(PlanRejected, "ya fue aplicada o descartada"):
            apply_plan(self.ctx, staged.pk)

    def test_template_round_trip_changes_nothing(self) -> None:
        plan(self.world, 3, item(self.budget, "2.1"), "4")
        data = template_bytes(self.ctx, self.world.project)
        rows = read_template(data)
        self.assertEqual(list(rows[0][:11]), HEADER)
        uids = [row[0] for row in rows[1:]]
        self.assertEqual(uids, [2, 3, 5, 6])  # hojas vigentes en orden del Project
        staged = self._stage(data)
        self.assertEqual(
            (staged.summary["added"], staged.summary["changed"], staged.summary["removed"]),
            (0, 0, 0),
        )

    def test_project_mode_ignores_level_columns(self) -> None:
        from .factories import located_xml, project_levels_from_xml

        world, _ = budget_world("Modo Project")
        project_levels_from_xml(world)
        import_schedule(world, located_xml())
        staged = stage_plan(
            world.ctx(),
            world.project,
            file_name="m.xlsx",
            data=sheet([3, "", "", "Otro", "", "", "1.1", "", "", 1]),
        )
        apply_plan(world.ctx(), staged.pk)
        tree = load_tree(world.ctx(), world.project)
        self.assertEqual(tree.label(world.task(3).location_id), "Tramo 1")
```

(`suno_levels` crea los niveles Tramo y PR sin campo de Project; `budget_world` aplica `samples.v1()`: hojas vigentes uid 2, 3, 5 y 6.)

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_importer.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.matrix.importer'`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/importer.py`:

```python
"""Carga de la matriz desde la plantilla Excel, en dos pasos.

1. `stage_plan`: lee el archivo, calcula las diferencias contra la matriz actual
   y guarda una `QuantityPlanImport` pendiente (vista previa). Descarta la
   pendiente anterior del proyecto.
2. `apply_plan`: con el proyecto bloqueado, vuelve a leer el archivo guardado y
   reemplaza la matriz. Un par ausente se borra, salvo que tenga ejecución: ese
   queda con planeado 0. `discard_plan` la descarta.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import DatabaseError, transaction
from django.db.models import Max
from django.utils import timezone

from visitacampo.budgets.excel import ExcelRejected
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    LocationLevel,
    Project,
    QuantityPlan,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
)
from visitacampo.core.scoping import scoped_qs

from .diff import BudgetedItem, item_totals, summarize_plan
from .locations import Tree, from_project, load_tree, node_key, project_levels
from .quantities import executed_by_pair, item_facts
from .template import (
    ItemRef,
    ParsedPlan,
    TaskRef,
    TemplateRejected,
    TemplateRow,
    build_template,
    check_file_name,
    parse_plan,
    read_template,
)

BUSY = "Otra importación de este proyecto está en curso; intenta de nuevo en unos segundos."
STALE = (
    "Cambió el cronograma, un presupuesto o la matriz después de subir este archivo. "
    "Vuelve a subirlo para ver los cambios actualizados."
)

Pair = tuple[UUID, UUID]


class PlanRejected(Exception):
    """No se puede subir o aplicar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(errors[0])


@dataclass
class _Context:
    levels: list[LocationLevel]
    editable: bool
    tree: Tree
    tasks: dict[int, Task]
    budgets: list[Budget]
    #: Ítems de las versiones vigentes: id → (presupuesto, datos).
    budgeted: dict[UUID, tuple[Budget, BudgetedItem]]
    item_refs: dict[tuple[str, str], ItemRef]
    current: dict[Pair, QuantityPlan]


def current_versions(ctx: RequestContext, project: Project) -> list[BudgetVersion]:
    return list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project=project, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("budget_id", "-number")
        .distinct("budget_id")
    )


def _load(ctx: RequestContext, project: Project) -> _Context:
    levels = project_levels(ctx, project)
    budgets = {b.pk: b for b in scoped_qs(ctx, Budget).filter(project=project).order_by("name")}
    versions = current_versions(ctx, project)
    budgeted: dict[UUID, tuple[Budget, BudgetedItem]] = {}
    refs: dict[tuple[str, str], ItemRef] = {}
    for row in scoped_qs(ctx, BudgetItemVersion).filter(version__in=versions).values(
        "item_id", "item__code", "kind", "description", "unit", "quantity", "version__budget_id"
    ):
        budget = budgets[row["version__budget_id"]]
        refs[(node_key(budget.name), row["item__code"])] = ItemRef(row["item_id"], row["kind"])
        if row["kind"] == "item":
            budgeted[row["item_id"]] = (
                budget,
                BudgetedItem(row["item__code"], row["description"], row["unit"], row["quantity"]),
            )
    return _Context(
        levels=levels,
        editable=bool(levels) and not from_project(levels),
        tree=load_tree(ctx, project),
        tasks={t.uid: t for t in scoped_qs(ctx, Task).filter(project=project)},
        budgets=list(budgets.values()),
        budgeted=budgeted,
        item_refs=refs,
        current={
            (p.task_id, p.item_id): p
            for p in scoped_qs(ctx, QuantityPlan).filter(project=project)
        },
    )


def _parse(data: bytes, context: _Context) -> ParsedPlan:
    task_refs = {
        uid: TaskRef(
            id=t.pk,
            label=f"{t.outline_number} {t.name}",
            is_summary=t.is_summary,
            retired=t.retired_in_id is not None,
        )
        for uid, t in context.tasks.items()
    }
    try:
        return parse_plan(
            read_template(data),
            level_names=[level.name for level in context.levels],
            editable_locations=context.editable,
            tasks=task_refs,
            items=context.item_refs,
            budget_names=[b.name for b in context.budgets],
        )
    except TemplateRejected as exc:
        raise PlanRejected(exc.errors) from exc


def _base(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Lo vigente al preparar la vista previa; si cambia, hay que volver a subir."""
    schedule = scoped_qs(ctx, ScheduleImport).filter(
        project=project, status=ScheduleImportStatus.APPLIED
    )
    matrix = scoped_qs(ctx, QuantityPlanImport).filter(
        project=project, status=QuantityPlanImportStatus.APPLIED
    )
    return {
        "schedule": schedule.aggregate(n=Max("number"))["n"],
        "matrix": matrix.aggregate(n=Max("number"))["n"],
        "budgets": {str(v.budget_id): v.number for v in current_versions(ctx, project)},
    }


def _summary(
    ctx: RequestContext, project: Project, parsed: ParsedPlan, context: _Context
) -> dict[str, Any]:
    executed = set(executed_by_pair(ctx, project_ids=[project.pk]))
    by_id = {t.pk: t for t in context.tasks.values()}
    codes = {item_id: info.code for item_id, (_, info) in context.budgeted.items()}
    missing_codes = {i for _, i in [*parsed.pairs, *context.current] if i not in codes}
    for item_id, facts in item_facts(ctx, missing_codes).items():
        codes[item_id] = facts.code

    def label(pair: Pair) -> str:
        task = by_id[pair[0]]
        return f"{task.outline_number} {task.name} × {codes.get(pair[1], '?')}"

    current = {pair: p.planned_quantity for pair, p in context.current.items()}
    summary = summarize_plan(current, parsed.pairs, executed=executed, label=label)

    planned: dict[UUID, Decimal] = defaultdict(Decimal)
    for (_, item_id), value in parsed.pairs.items():
        planned[item_id] += value
    retired = {
        item_id: BudgetedItem(f.code, f.description, f.unit, None)
        for item_id, f in item_facts(
            ctx, [i for i in planned if i not in context.budgeted]
        ).items()
    }
    totals = item_totals(
        planned, {i: info for i, (_, info) in context.budgeted.items()}, retired
    )

    location_changes: list[str] = []
    new_paths: set[tuple[str, ...]] = set()
    if context.editable:
        for task_id, names in parsed.locations.items():
            before = context.tree.label(by_id[task_id].location_id)
            after = " / ".join(names)
            if before != after:
                task = by_id[task_id]
                location_changes.append(
                    f"{task.outline_number} {task.name}: {before or 'sin ubicación'} → "
                    f"{after or 'sin ubicación'}"
                )
            for depth in range(1, len(names) + 1):
                prefix = tuple(node_key(n) for n in names[:depth])
                if context.tree.find(names[:depth]) is None:
                    new_paths.add(prefix)
    return {
        **summary,
        "item_counts": totals["counts"],
        "items": totals["items"],
        "location_changes": len(location_changes),
        "location_examples": location_changes[:20],
        "new_nodes": len(new_paths),
        "warnings": list(parsed.warnings),
        "pairs": len(parsed.pairs),
        "base": _base(ctx, project),
    }


def stage_plan(
    ctx: RequestContext, project: Project, *, file_name: str, data: bytes
) -> QuantityPlanImport:
    try:
        check_file_name(file_name)
    except ExcelRejected as exc:
        raise PlanRejected([str(exc)]) from exc
    context = _load(ctx, project)
    parsed = _parse(data, context)
    summary = _summary(ctx, project, parsed, context)
    with transaction.atomic():
        scoped_qs(ctx, QuantityPlanImport).filter(
            project=project, status=QuantityPlanImportStatus.PENDING
        ).update(status=QuantityPlanImportStatus.DISCARDED)
        return scoped_qs(ctx, QuantityPlanImport).create(
            org_id=ctx.org_id,
            project=project,
            file_name=file_name[:255],
            source_file=data,
            uploaded_by_id=ctx.user_id,
            summary=summary,
        )


def _locked_pending(
    ctx: RequestContext, import_id: UUID
) -> tuple[QuantityPlanImport, Project]:
    imp = (
        scoped_qs(ctx, QuantityPlanImport)
        .defer("source_file")
        .filter(pk=import_id)
        .first()
    )
    if imp is None:
        raise PlanRejected(["La importación no existe."])
    try:
        project = scoped_qs(ctx, Project).select_for_update(nowait=True).get(pk=imp.project_id)
    except DatabaseError as exc:
        raise PlanRejected([BUSY]) from exc
    imp = scoped_qs(ctx, QuantityPlanImport).select_for_update().get(pk=import_id)
    if imp.status != QuantityPlanImportStatus.PENDING:
        raise PlanRejected(["Esta importación ya fue aplicada o descartada."])
    return imp, project


def apply_plan(ctx: RequestContext, import_id: UUID) -> QuantityPlanImport:
    with transaction.atomic():
        imp, project = _locked_pending(ctx, import_id)
        if imp.summary.get("base") != _base(ctx, project):
            raise PlanRejected([STALE])
        context = _load(ctx, project)
        parsed = _parse(bytes(imp.source_file), context)
        executed = set(executed_by_pair(ctx, project_ids=[project.pk]))
        now = timezone.now()
        plans = scoped_qs(ctx, QuantityPlan)

        new: list[QuantityPlan] = []
        changed: list[QuantityPlan] = []
        for (task_id, item_id), value in parsed.pairs.items():
            current = context.current.get((task_id, item_id))
            if current is None:
                new.append(
                    QuantityPlan(
                        org_id=ctx.org_id,
                        project=project,
                        task_id=task_id,
                        item_id=item_id,
                        planned_quantity=value,
                        updated_by_id=ctx.user_id,
                        updated_at=now,
                    )
                )
            elif current.planned_quantity != value:
                current.planned_quantity, current.updated_by_id, current.updated_at = (
                    value,
                    ctx.user_id,
                    now,
                )
                changed.append(current)
        delete_ids: list[UUID] = []
        for pair, current in context.current.items():
            if pair in parsed.pairs:
                continue
            if pair in executed:
                if current.planned_quantity != 0:
                    current.planned_quantity = Decimal(0)
                    current.updated_by_id, current.updated_at = ctx.user_id, now
                    changed.append(current)
            else:
                delete_ids.append(current.pk)
        plans.filter(pk__in=delete_ids).delete()
        plans.bulk_create(new, batch_size=500)
        plans.bulk_update(
            changed, ["planned_quantity", "updated_by", "updated_at"], batch_size=500
        )

        if context.editable:
            by_id = {t.pk: t for t in context.tasks.values()}
            moved: list[Task] = []
            for task_id, names in parsed.locations.items():
                node_id = (
                    context.tree.ensure(ctx, context.levels, names)[0].pk if names else None
                )
                task = by_id[task_id]
                if task.location_id != node_id:
                    task.location_id = node_id
                    moved.append(task)
            scoped_qs(ctx, Task).bulk_update(moved, ["location"], batch_size=500)

        applied = scoped_qs(ctx, QuantityPlanImport).filter(
            project=project, status=QuantityPlanImportStatus.APPLIED
        )
        imp.number = (applied.aggregate(n=Max("number"))["n"] or 0) + 1
        imp.status = QuantityPlanImportStatus.APPLIED
        imp.applied_at = now
        imp.save(update_fields=["number", "status", "applied_at"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_IMPORT_APPLIED,
            target=f"project:{project.pk}",
            metadata={
                "import": str(imp.pk),
                "number": imp.number,
                "added": len(new),
                "changed": len(changed),
                "removed": len(delete_ids),
            },
        )
        return imp


def discard_plan(ctx: RequestContext, import_id: UUID) -> QuantityPlanImport:
    with transaction.atomic():
        imp, project = _locked_pending(ctx, import_id)
        imp.status = QuantityPlanImportStatus.DISCARDED
        imp.save(update_fields=["status"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_IMPORT_DISCARDED,
            target=f"project:{project.pk}",
            metadata={"import": str(imp.pk)},
        )
        return imp


def template_bytes(ctx: RequestContext, project: Project) -> bytes:
    """La plantilla con la matriz y la ubicación actuales: una fila por par, y una por
    tarea hoja vigente sin plan."""
    context = _load(ctx, project)
    facts = item_facts(ctx, {item_id for _, item_id in context.current})
    budget_names = {
        item_id: budget.name for item_id, (budget, _) in context.budgeted.items()
    }
    by_task: dict[UUID, list[QuantityPlan]] = defaultdict(list)
    for (task_id, _), plan in context.current.items():
        by_task[task_id].append(plan)
    width = len(context.levels)
    rows: list[TemplateRow] = []
    leaves = sorted(
        (t for t in context.tasks.values() if not t.is_summary and t.retired_in_id is None),
        key=lambda t: t.sort_order,
    )
    for task in leaves:
        names = context.tree.names(task.location_id) if task.location_id else []
        location = tuple([*names, *[""] * width][:width])
        own = sorted(by_task.get(task.pk, []), key=lambda p: facts[p.item_id].code)
        if not own:
            rows.append(
                TemplateRow(task.uid, task.wbs, task.name, location, "", "", "", "", None)
            )
        for plan in own:
            info = facts[plan.item_id]
            rows.append(
                TemplateRow(
                    task.uid,
                    task.wbs,
                    task.name,
                    location,
                    budget_names.get(plan.item_id, ""),
                    info.code,
                    info.description,
                    info.unit,
                    plan.planned_quantity,
                )
            )
    return build_template([level.name for level in context.levels], rows)
```

Nota: un ítem retirado del presupuesto que sigue en el plan no tiene presupuesto en `budget_names`: la plantilla deja la columna vacía, y al volver a subirla ese par falla ("el ítem no existe en el presupuesto vigente"). Es lo correcto: un ítem retirado no se puede planear de nuevo; el usuario borra la fila o la cantidad.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix`
Expected: PASS. En `test_stage_and_apply`, `under` = 5: los ítems de V1 son 1.1, 1.2, 2.1, 2.2, 2.4; 1.1 (60 < 100) y 2.1 (4 < 10) quedan cortos y los otros tres sin asignar (también cortos); `unassigned` = 3.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): two-step template import and template download

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: Ediciones web fila a fila (`edits.py`)

**Files:**
- Create: `server/visitacampo/matrix/edits.py`
- Test: `server/visitacampo/matrix/tests/test_edits.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_edits.py`:

```python
from __future__ import annotations

import unittest
from decimal import Decimal

from django.test import TestCase

from visitacampo.core.models import AuditEvent, QuantityPlan
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.edits import EditRejected, remove_plan, set_plan, set_task_location
from visitacampo.matrix.locations import load_tree

from .factories import (
    budget_world,
    item,
    project_levels_from_xml,
    quantity_row,
    quantity_visit,
    suno_levels,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class EditsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()

    def test_set_and_update_plan(self) -> None:
        task = self.world.task(3)
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="4,5")
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="6")
        plan = QuantityPlan.objects.get()
        self.assertEqual(plan.planned_quantity, Decimal("6.0000"))
        events = AuditEvent.objects.filter(action="matrix.plan_changed").order_by("created_at")
        self.assertEqual(
            [(e.metadata["before"], e.metadata["after"]) for e in events],
            [(None, "4.5000"), ("4.5000", "6.0000")],
        )

    def test_rejections(self) -> None:
        summary = self.world.task(1)
        with self.assertRaisesMessage(EditRejected, "tareas de detalle"):
            set_plan(self.ctx, summary, budget_id=self.budget.pk, code="2.1", quantity="1")
        task = self.world.task(3)
        with self.assertRaisesMessage(EditRejected, "no existe"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="9.9", quantity="1")
        with self.assertRaisesMessage(EditRejected, "capítulo"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2", quantity="1")
        with self.assertRaisesMessage(EditRejected, "número"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="x")
        with self.assertRaisesMessage(EditRejected, "negativa"):
            set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="-1")

    def test_remove_keeps_executed_at_zero(self) -> None:
        concrete = item(self.budget, "2.1")
        task = self.world.task(3)
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.1", quantity="4")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(concrete, "1")))
        self.assertFalse(remove_plan(self.ctx, task, concrete.pk))
        self.assertEqual(QuantityPlan.objects.get().planned_quantity, Decimal("0"))
        set_plan(self.ctx, task, budget_id=self.budget.pk, code="2.2", quantity="4")
        self.assertTrue(remove_plan(self.ctx, task, item(self.budget, "2.2").pk))
        self.assertEqual(QuantityPlan.objects.count(), 1)

    def test_task_location_in_suno_mode(self) -> None:
        suno_levels(self.world)
        task = self.world.task(3)
        set_task_location(self.ctx, task, "Tramo 2 / PR 12+300")
        task.refresh_from_db()
        label = load_tree(self.ctx, self.world.project).label(task.location_id)
        self.assertEqual(label, "Tramo 2 / PR 12+300")
        set_task_location(self.ctx, task, "")
        task.refresh_from_db()
        self.assertIsNone(task.location_id)
        with self.assertRaisesMessage(EditRejected, "3 niveles"):
            set_task_location(self.ctx, task, "a / b / c")

    def test_task_location_blocked_in_project_mode(self) -> None:
        project_levels_from_xml(self.world)
        with self.assertRaisesMessage(EditRejected, "MS Project"):
            set_task_location(self.ctx, self.world.task(3), "Tramo 1")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_edits.py`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/edits.py`:

```python
"""Ediciones de la matriz desde la web, fila a fila, y la ubicación de una tarea.

Cada cambio va con su registro de auditoría en la misma transacción."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from visitacampo.budgets.excel import MAX_QUANTITY
from visitacampo.budgets.importer import current_version
from visitacampo.budgets.totals import parse_number
from visitacampo.budgets.totals import quantity as quantity_of
from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, BudgetItemVersion, QuantityPlan, Task
from visitacampo.core.scoping import scoped_qs

from .locations import LevelError, from_project, load_tree, path_values, project_levels
from .quantities import executed_by_pair


class EditRejected(Exception):
    """El cambio no se puede hacer; el mensaje es para el usuario."""


def _check_leaf(task: Task) -> None:
    if task.is_summary:
        raise EditRejected("La matriz va en tareas de detalle, no en resúmenes.")
    if task.retired_in_id is not None:
        raise EditRejected("La tarea está retirada del cronograma.")


def set_plan(
    ctx: RequestContext, task: Task, *, budget_id: UUID, code: str, quantity: str
) -> QuantityPlan:
    """Crea o cambia la cantidad planeada de un ítem en la tarea."""
    _check_leaf(task)
    budget = scoped_qs(ctx, Budget).filter(pk=budget_id, project_id=task.project_id).first()
    version = current_version(ctx, budget) if budget is not None else None
    row = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version, item__code=code.strip())
        .values("item_id", "kind")
        .first()
        if version is not None
        else None
    )
    if row is None:
        raise EditRejected(f"El ítem {code} no existe en el presupuesto vigente.")
    if row["kind"] != "item":
        raise EditRejected(f"{code} es un capítulo; la matriz va en ítems.")
    value = parse_number(quantity)
    if value is None:
        raise EditRejected("La cantidad no es un número.")
    if value < 0:
        raise EditRejected("La cantidad no puede ser negativa.")
    if value >= MAX_QUANTITY:
        raise EditRejected("La cantidad es demasiado grande.")
    value = quantity_of(value)
    with transaction.atomic():
        plan = (
            scoped_qs(ctx, QuantityPlan)
            .select_for_update()
            .filter(task=task, item_id=row["item_id"])
            .first()
        )
        before = None if plan is None else str(plan.planned_quantity)
        if plan is None:
            plan = scoped_qs(ctx, QuantityPlan).create(
                org_id=ctx.org_id,
                project_id=task.project_id,
                task=task,
                item_id=row["item_id"],
                planned_quantity=value,
                updated_by_id=ctx.user_id,
                updated_at=timezone.now(),
            )
        else:
            plan.planned_quantity, plan.updated_by_id, plan.updated_at = (
                value,
                ctx.user_id,
                timezone.now(),
            )
            plan.save(update_fields=["planned_quantity", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_PLAN_CHANGED,
            target=f"task:{task.pk}",
            metadata={"item": str(row["item_id"]), "before": before, "after": str(value)},
        )
        return plan


def remove_plan(ctx: RequestContext, task: Task, item_id: UUID) -> bool:
    """Quita el ítem del plan de la tarea. Si ya tiene ejecución, lo deja en 0 y
    devuelve False (la ejecución no se pierde de vista)."""
    with transaction.atomic():
        plan = (
            scoped_qs(ctx, QuantityPlan)
            .select_for_update()
            .filter(task=task, item_id=item_id)
            .first()
        )
        if plan is None:
            return True
        before = str(plan.planned_quantity)
        executed = (task.pk, item_id) in executed_by_pair(ctx, task_ids=[task.pk])
        if executed:
            plan.planned_quantity, plan.updated_by_id, plan.updated_at = (
                quantity_of(0),
                ctx.user_id,
                timezone.now(),
            )
            plan.save(update_fields=["planned_quantity", "updated_by", "updated_at"])
        else:
            plan.delete()
        audit.record(
            ctx,
            action=AuditAction.MATRIX_PLAN_CHANGED,
            target=f"task:{task.pk}",
            metadata={
                "item": str(item_id),
                "before": before,
                "after": "0.0000" if executed else None,
            },
        )
        return not executed


def set_task_location(ctx: RequestContext, task: Task, path_text: str) -> None:
    """Ubica la tarea en la ruta "Tramo 2 / PR 12+300" (creando los nodos que falten);
    vacío la deja sin ubicación. Solo si la ubicación se asigna en SUNO."""
    _check_leaf(task)
    levels = project_levels(ctx, task.project)
    if not levels:
        raise EditRejected("El proyecto no tiene niveles de ubicación.")
    if from_project(levels):
        raise EditRejected("La ubicación de este proyecto se lee de MS Project.")
    parts = [part for part in path_text.split("/")]
    if path_text.strip() and len(parts) > len(levels):
        raise EditRejected(
            f"La ruta tiene {len(parts)} niveles y el proyecto tiene {len(levels)}."
        )
    path = path_values(parts if path_text.strip() else [])
    if path.incomplete:
        raise EditRejected("La ruta tiene un nivel vacío.")
    with transaction.atomic():
        before = task.location_id
        try:
            node_id = (
                load_tree(ctx, task.project).ensure(ctx, levels, path.names)[0].pk
                if path.names
                else None
            )
        except LevelError as exc:
            raise EditRejected(str(exc)) from exc
        if node_id == before:
            return
        task.location_id = node_id
        task.save(update_fields=["location"])
        audit.record(
            ctx,
            action=AuditAction.MATRIX_TASK_LOCATION_CHANGED,
            target=f"task:{task.pk}",
            metadata={
                "before": str(before) if before else None,
                "after": str(node_id) if node_id else None,
            },
        )
```

En la prueba `"3 niveles"`: la ruta "a / b / c" tiene 3 partes y el proyecto 2 niveles; el mensaje es "La ruta tiene 3 niveles y el proyecto tiene 2."

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_edits.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): web edits for plan rows and task location

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Lecturas para pantalla (`reports.py`)

**Files:**
- Create: `server/visitacampo/matrix/reports.py`
- Test: `server/visitacampo/matrix/tests/test_reports.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_reports.py`:

```python
from __future__ import annotations

import unittest
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import load_tree
from visitacampo.matrix.reports import filter_item_rows, item_rows, location_rows

from .factories import budget_world, item, locate, plan, quantity_row, quantity_visit, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ReportsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        locate(self.world, 5, "Tramo 2", "PR 1")
        self.concrete = item(self.budget, "2.1")  # 10 m3 a 450.000
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(
            quantity_visit(self.world, 5, quantity_row(self.concrete, "2"))
        )

    def test_item_rows(self) -> None:
        rows = {row.code: row for row in item_rows(self.ctx, self.world.project)}
        concrete = rows["2.1"]
        self.assertEqual(concrete.budgeted, Decimal("10.0000"))
        self.assertEqual(concrete.planned, Decimal("12.0000"))
        self.assertEqual(concrete.executed, Decimal("2"))
        self.assertEqual(concrete.status, "over")
        self.assertEqual(
            [(t.task.uid, t.location) for t in concrete.tasks],
            [(3, "Tramo 1"), (5, "Tramo 2 / PR 1")],
        )
        self.assertEqual(rows["1.1"].status, "under")  # sin asignar
        self.assertEqual(rows["1.1"].planned, Decimal(0))

    def test_filters(self) -> None:
        rows = item_rows(self.ctx, self.world.project)
        tramo_2 = load_tree(self.ctx, self.world.project).find(("Tramo 2",))
        filtered = filter_item_rows(rows, location_id=tramo_2, tree=load_tree(self.ctx, self.world.project))
        self.assertEqual([r.code for r in filtered], ["2.1"])
        self.assertEqual(filtered[0].planned, Decimal("8.0000"))
        over = filter_item_rows(rows, status="over")
        self.assertEqual([r.code for r in over], ["2.1"])
        chapter = filter_item_rows(rows, chapter_code="1")
        self.assertEqual([r.code for r in chapter], ["1.1", "1.2"])

    def test_location_rows(self) -> None:
        rows = {row.label: row for row in location_rows(self.ctx, self.world.project)}
        self.assertEqual(rows["Tramo 2"].planned_value, Decimal("3600000.00"))
        self.assertEqual(rows["Tramo 2"].executed_value, Decimal("900000.00"))
        self.assertEqual(rows["Tramo 2"].pct, Decimal("25.00"))
        self.assertEqual(rows["Tramo 2 / PR 1"].depth, 2)
        self.assertEqual(rows["Tramo 1"].pct, Decimal("0.00"))
        self.assertEqual(rows["Total"].planned_value, Decimal("5400000.00"))

    def test_query_count_does_not_grow(self) -> None:
        with CaptureQueriesContext(connection) as small:
            item_rows(self.ctx, self.world.project)
            location_rows(self.ctx, self.world.project)
        for code in ("1.1", "1.2", "2.2", "2.4"):
            plan(self.world, 2, item(self.budget, code), "1")
            plan(self.world, 6, item(self.budget, code), "1")
        with CaptureQueriesContext(connection) as large:
            item_rows(self.ctx, self.world.project)
            location_rows(self.ctx, self.world.project)
        self.assertEqual(len(small), len(large))
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_reports.py`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/matrix/reports.py`:

```python
"""Lo que muestran las páginas de la matriz: por ítem y por ubicación.

Coste fijo en queries, sin importar el tamaño de la matriz."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from visitacampo.budgets.totals import HUNDRED, money
from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetItemVersion,
    Project,
    QuantityPlan,
    Task,
)
from visitacampo.core.scoping import scoped_qs

from .diff import code_key
from .importer import current_versions
from .locations import SEPARATOR, Tree, load_tree
from .progress import PCT_STEP
from .quantities import executed_by_pair, item_facts

_ZERO = Decimal(0)

STATUS_LABELS = {
    "over": "Con exceso",
    "under": "Sin asignar completo",
    "ok": "Cuadra",
    "retired": "Retirado del presupuesto",
}


@dataclass(frozen=True)
class TaskQuantity:
    task: Task
    location: str
    planned: Decimal
    executed: Decimal


@dataclass(frozen=True)
class ItemRow:
    item_id: UUID
    budget: Budget | None
    code: str
    description: str
    unit: str
    #: Cantidad en la versión vigente; None si el ítem se retiró del presupuesto.
    budgeted: Decimal | None
    planned: Decimal
    executed: Decimal
    #: "ok", "over", "under" o "retired" (sobre el total del ítem, sin filtros).
    status: str
    #: Capítulos que lo contienen (sus ids), para el filtro por capítulo.
    chapters: frozenset[UUID]
    tasks: tuple[TaskQuantity, ...]

    @property
    def pct_executed(self) -> Decimal | None:
        if self.planned <= 0:
            return None
        return (max(self.executed, _ZERO) * HUNDRED / self.planned).quantize(
            PCT_STEP, rounding=ROUND_HALF_UP
        )

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def unassigned(self) -> Decimal | None:
        if self.budgeted is None:
            return None
        return max(self.budgeted - self.planned, _ZERO)


def item_rows(ctx: RequestContext, project: Project) -> list[ItemRow]:
    budgets = {b.pk: b for b in scoped_qs(ctx, Budget).filter(project=project)}
    versions = current_versions(ctx, project)
    version_rows = list(
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions)
        .values(
            "item_id",
            "item__code",
            "kind",
            "description",
            "unit",
            "quantity",
            "parent_id",
            "version__budget_id",
        )
    )
    parents = {row["item_id"]: row["parent_id"] for row in version_rows}

    def chapters_of(item_id: UUID) -> frozenset[UUID]:
        found: set[UUID] = set()
        parent = parents.get(item_id)
        while parent is not None and parent not in found:
            found.add(parent)
            parent = parents.get(parent)
        return frozenset(found)

    plans = list(
        scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .select_related("task")
        .order_by("task__sort_order")
    )
    executed = executed_by_pair(ctx, project_ids=[project.pk])
    tree = load_tree(ctx, project)
    by_item: dict[UUID, list[TaskQuantity]] = defaultdict(list)
    for plan in plans:
        by_item[plan.item_id].append(
            TaskQuantity(
                task=plan.task,
                location=tree.label(plan.task.location_id),
                planned=plan.planned_quantity,
                executed=executed.get((plan.task_id, plan.item_id), _ZERO),
            )
        )

    def build(
        item_id: UUID,
        budget: Budget | None,
        code: str,
        description: str,
        unit: str,
        budgeted: Decimal | None,
    ) -> ItemRow:
        tasks = tuple(by_item.get(item_id, ()))
        planned = sum((t.planned for t in tasks), _ZERO)
        if budgeted is None:
            status = "retired"
        else:
            status = "over" if planned > budgeted else "under" if planned < budgeted else "ok"
        return ItemRow(
            item_id=item_id,
            budget=budget,
            code=code,
            description=description,
            unit=unit,
            budgeted=budgeted,
            planned=planned,
            executed=sum((t.executed for t in tasks), _ZERO),
            status=status,
            chapters=chapters_of(item_id),
            tasks=tasks,
        )

    rows = [
        build(
            row["item_id"],
            budgets[row["version__budget_id"]],
            row["item__code"],
            row["description"],
            row["unit"],
            row["quantity"],
        )
        for row in version_rows
        if row["kind"] == "item"
    ]
    current_ids = {row.item_id for row in rows}
    retired = [i for i in by_item if i not in current_ids]
    for item_id, facts in item_facts(ctx, retired).items():
        rows.append(build(item_id, None, facts.code, facts.description, facts.unit, None))
    rows.sort(key=lambda r: (r.budget.name.lower() if r.budget else "~", code_key(r.code)))
    return rows


def chapter_choices(ctx: RequestContext, project: Project) -> list[tuple[UUID, str]]:
    """Capítulos de las versiones vigentes: (id del ítem-capítulo, "código descripción")."""
    versions = current_versions(ctx, project)
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions, kind="chapter")
        .values_list("item_id", "item__code", "description")
    )
    return sorted(
        ((item_id, f"{code} {description}") for item_id, code, description in rows),
        key=lambda choice: code_key(choice[1].split(" ", 1)[0]),
    )


def filter_item_rows(
    rows: Sequence[ItemRow],
    *,
    budget_id: UUID | None = None,
    chapter_id: UUID | None = None,
    chapter_code: str | None = None,
    location_id: UUID | None = None,
    tree: Tree | None = None,
    status: str | None = None,
) -> list[ItemRow]:
    """Filtra. Con ubicación, cada ítem queda con sus tareas de ese lugar y sus sumas."""
    result: list[ItemRow] = []
    for row in rows:
        if budget_id is not None and (row.budget is None or row.budget.pk != budget_id):
            continue
        if chapter_id is not None and chapter_id not in row.chapters:
            continue
        if chapter_code is not None and not row.code.startswith(f"{chapter_code}."):
            continue
        if status is not None and row.status != status:
            continue
        if location_id is not None and tree is not None:
            tasks = tuple(
                t
                for t in row.tasks
                if t.task.location_id is not None and tree.contains(location_id, t.task.location_id)
            )
            if not tasks:
                continue
            row = replace(
                row,
                tasks=tasks,
                planned=sum((t.planned for t in tasks), _ZERO),
                executed=sum((t.executed for t in tasks), _ZERO),
            )
        result.append(row)
    return result


@dataclass(frozen=True)
class NodeRow:
    label: str
    depth: int
    planned_value: Decimal
    executed_value: Decimal

    @property
    def pct(self) -> Decimal | None:
        if self.planned_value <= 0:
            return None
        value = min(HUNDRED, self.executed_value * HUNDRED / self.planned_value)
        return value.quantize(PCT_STEP, rounding=ROUND_HALF_UP)


def location_rows(ctx: RequestContext, project: Project) -> list[NodeRow]:
    """Valor planeado y ejecutado ($) por nodo, consolidado hacia arriba; al final las
    tareas sin ubicación y el total."""
    plans = list(
        scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .values_list("task_id", "item_id", "planned_quantity", "task__location_id")
    )
    executed = executed_by_pair(ctx, project_ids=[project.pk])
    prices = {
        item_id: facts.unit_price or _ZERO
        for item_id, facts in item_facts(ctx, {item_id for _, item_id, _, _ in plans}).items()
    }
    tree = load_tree(ctx, project)
    planned: dict[UUID | None, Decimal] = defaultdict(Decimal)
    done: dict[UUID | None, Decimal] = defaultdict(Decimal)
    total_planned = total_done = _ZERO
    for task_id, item_id, quantity, location_id in plans:
        price = prices.get(item_id, _ZERO)
        p = quantity * price
        e = max(executed.get((task_id, item_id), _ZERO), _ZERO) * price
        total_planned += p
        total_done += e
        if location_id is None or location_id not in tree.nodes:
            planned[None] += p
            done[None] += e
            continue
        for part in tree.nodes[location_id].path.strip("/").split("/"):
            node_id = UUID(hex=part)
            planned[node_id] += p
            done[node_id] += e
    roots = [n.pk for n in tree.nodes.values() if n.parent_id is None]
    ordered: list[UUID] = []
    for root in sorted(roots, key=lambda pk: tree.label(pk).lower()):
        ordered.extend(tree.descendants(root))
    rows = [
        NodeRow(
            label=tree.label(node_id),
            depth=tree.nodes[node_id].level.depth,
            planned_value=money(planned[node_id]),
            executed_value=money(done[node_id]),
        )
        for node_id in ordered
        if planned[node_id] > 0 or done[node_id] > 0
    ]
    if planned[None] > 0 or done[None] > 0:
        rows.append(NodeRow("Sin ubicación", 1, money(planned[None]), money(done[None])))
    rows.append(NodeRow("Total", 0, money(total_planned), money(total_done)))
    return rows


__all__ = [
    "SEPARATOR",
    "STATUS_LABELS",
    "ItemRow",
    "NodeRow",
    "TaskQuantity",
    "chapter_choices",
    "filter_item_rows",
    "item_rows",
    "location_rows",
]
```

Notas:

- El filtro por capítulo en la página usa `chapter_id` (los ancestros reales en la versión vigente). `chapter_code` queda para las pruebas y para códigos jerárquicos; ambos filtros conviven.
- Con la fábrica, el capítulo "1" contiene 1.1 y 1.2: `chapter_code="1"` devuelve esos dos.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix`
Expected: PASS. Cifras de `test_location_rows`: Tramo 2 = 8 × 450.000 = 3.600.000 planeado y 2 × 450.000 = 900.000 ejecutado (25 %); total = 12 × 450.000 = 5.400.000.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/matrix
git commit -m "feat(matrix): item and location reports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Páginas de la matriz (vistas, URL, plantillas, pestaña)

**Files:**
- Create: `server/visitacampo/matrix/views.py`, `server/visitacampo/matrix/urls.py`
- Modify: `server/config/urls.py`
- Modify: `server/visitacampo/core/templates/suno/projects/_header.html`
- Create: `server/visitacampo/core/templates/suno/matrix/_nav.html`, `items.html`, `locations.html`, `levels.html`, `import.html`, `import_preview.html`, `history.html`
- Modify: `server/visitacampo/core/static/suno/css/projects.css`
- Test: `server/visitacampo/matrix/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_views.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.budgets.tests.xlsx import build_xlsx
from visitacampo.core.models import LocationLevel, QuantityPlan, QuantityPlanImport
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.template import header

from .factories import budget_world, item, locate, plan, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class MatrixViewsTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        plan(self.world, 3, item(self.budget, "2.1"), "4")
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/matriz/"
        self.client.force_login(self.world.admin)

    def test_field_agent_gets_404(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_supervisor_sees_but_cannot_import(self) -> None:
        self.client.force_login(self.world.supervisor)
        self.assertContains(self.client.get(self.base), "Concreto zapatas")
        self.assertEqual(self.client.get(self.base + "importar/").status_code, 403)
        self.assertEqual(self.client.get(self.base + "niveles/").status_code, 403)

    def test_items_page_and_tab(self) -> None:
        response = self.client.get(self.base)
        self.assertContains(response, ">Matriz</a>")
        self.assertContains(response, "1.2 Descapote")
        self.assertContains(response, "Tramo 1")
        self.assertContains(response, "Con exceso")
        filtered = self.client.get(self.base + "?estado=over")
        self.assertNotContains(filtered, "Concreto zapatas")

    def test_locations_page(self) -> None:
        response = self.client.get(self.base + "ubicacion/")
        self.assertContains(response, "Tramo 1")
        self.assertContains(response, "1.800.000,00")

    def test_levels_page(self) -> None:
        response = self.client.post(
            self.base + "niveles/",
            {"name_1": "Tramo", "name_2": "PR", "name_3": "Costado"},
        )
        self.assertRedirects(response, self.base + "niveles/")
        self.assertEqual(LocationLevel.objects.filter(project=self.world.project).count(), 3)
        bad = self.client.post(self.base + "niveles/", {"name_1": "Tramo", "name_2": "tramo"})
        self.assertContains(bad, "repetido", status_code=200)

    def test_template_download(self) -> None:
        response = self.client.get(self.base + "plantilla.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_import_preview_apply(self) -> None:
        data = build_xlsx(
            [[3, "", "", "Tramo 1", "", "", "2.1", "", "", 6]],
            header=header(["Tramo", "PR"]),
            sheet="Matriz",
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("m.xlsx", data)
        response = self.client.post(self.base + "importar/", {"file": upload})
        staged = QuantityPlanImport.objects.get()
        self.assertRedirects(response, f"{self.base}importar/{staged.pk}/")
        preview = self.client.get(f"{self.base}importar/{staged.pk}/")
        self.assertContains(preview, "1.2 Descapote × 2.1: 4 → 6")
        self.client.post(f"{self.base}importar/{staged.pk}/", {"action": "apply"})
        self.assertEqual(str(QuantityPlan.objects.get().planned_quantity), "6.0000")
        self.assertContains(self.client.get(self.base + "historial/"), "m.xlsx")

    def test_import_errors_are_listed(self) -> None:
        data = build_xlsx(
            [[99, "", "", "", "", "", "2.1", "", "", 6]],
            header=header(["Tramo", "PR"]),
            sheet="Matriz",
        )
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            self.base + "importar/", {"file": SimpleUploadedFile("m.xlsx", data)}
        )
        self.assertContains(response, "UniqueID 99")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_views.py`
Expected: FAIL (404 en todas las URL).

- [ ] **Step 3: Vistas y URL**

`server/visitacampo/matrix/urls.py`:

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/matriz/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "matrix"

urlpatterns = [
    path("", views.items, name="items"),
    path("ubicacion/", views.locations, name="locations"),
    path("niveles/", views.levels, name="levels"),
    path("plantilla.xlsx", views.template_download, name="template"),
    path("importar/", views.import_upload, name="import"),
    path("importar/<uuid:import_id>/", views.import_preview, name="import_preview"),
    path("historial/", views.history, name="history"),
]
```

En `server/config/urls.py`, después del include de presupuestos:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/matriz/",
        include("visitacampo.matrix.urls"),
    ),
```

`server/visitacampo/matrix/views.py`:

```python
"""Páginas de la matriz de homologación (costos, pieza 4)."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from visitacampo.budgets.excel import MAX_BYTES
from visitacampo.core.context import RequestContext
from visitacampo.core.download import content_disposition
from visitacampo.core.models import (
    MAX_LEVELS,
    Budget,
    Project,
    QuantityPlanImport,
    QuantityPlanImportStatus,
    ScheduleImport,
    ScheduleImportStatus,
)
from visitacampo.core.policies import can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_matrix_project, require_manage
from .importer import PlanRejected, apply_plan, discard_plan, stage_plan, template_bytes
from .locations import LevelError, LevelSpec, from_project, load_tree, project_levels, save_levels
from .reports import (
    STATUS_LABELS,
    chapter_choices,
    filter_item_rows,
    item_rows,
    location_rows,
)


def _context(ctx: RequestContext, project: Project) -> dict[str, object]:
    return {
        **_project_context(ctx, project),
        "can_manage_matrix": can_manage_budgets(ctx, ctx.org_id),
    }


@login_required(login_url="platform_login")
def items(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    tree = load_tree(ctx, project)
    status = request.GET.get("estado") or None
    filters = {
        "presupuesto": str(_parse_uuid(request.GET.get("presupuesto")) or ""),
        "capitulo": str(_parse_uuid(request.GET.get("capitulo")) or ""),
        "ubicacion": str(_parse_uuid(request.GET.get("ubicacion")) or ""),
        "estado": status if status in STATUS_LABELS else "",
    }
    location_id = _parse_uuid(filters["ubicacion"] or None)
    rows = filter_item_rows(
        item_rows(ctx, project),
        budget_id=_parse_uuid(filters["presupuesto"] or None),
        chapter_id=_parse_uuid(filters["capitulo"] or None),
        location_id=location_id if location_id in tree.nodes else None,
        tree=tree,
        status=filters["estado"] or None,
    )
    return _render(
        request,
        ctx,
        "suno/matrix/items.html",
        {
            **_context(ctx, project),
            "rows": rows,
            "filters": filters,
            "status_labels": STATUS_LABELS,
            "budgets": scoped_qs(ctx, Budget).filter(project=project).order_by("name"),
            "chapters": chapter_choices(ctx, project),
            "nodes": sorted(
                ((pk, tree.label(pk)) for pk in tree.nodes), key=lambda n: n[1].lower()
            ),
        },
    )


@login_required(login_url="platform_login")
def locations(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    return _render(
        request,
        ctx,
        "suno/matrix/locations.html",
        {**_context(ctx, project), "rows": location_rows(ctx, project)},
    )


def _custom_fields(ctx: RequestContext, project: Project) -> list[dict[str, str]]:
    """Campos personalizados del último XML aplicado (para elegir la fuente de cada nivel)."""
    last = (
        scoped_qs(ctx, ScheduleImport)
        .filter(project=project, status=ScheduleImportStatus.APPLIED)
        .defer("source_xml")
        .order_by("-number")
        .first()
    )
    fields: list[dict[str, str]] = list(last.summary.get("custom_fields", [])) if last else []
    return fields


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def levels(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    error = ""
    current = project_levels(ctx, project)
    rows = [
        {
            "depth": depth,
            "name": current[depth - 1].name if depth <= len(current) else "",
            "field": current[depth - 1].source_field if depth <= len(current) else "",
        }
        for depth in range(1, MAX_LEVELS + 1)
    ]
    if request.method == "POST":
        specs: list[LevelSpec] = []
        for depth in range(1, MAX_LEVELS + 1):
            name = request.POST.get(f"name_{depth}", "").strip()
            if not name:
                break
            specs.append(LevelSpec(name, request.POST.get(f"field_{depth}", "").strip()))
        try:
            save_levels(ctx, project, specs)
        except LevelError as exc:
            error = str(exc)
            rows = [
                {
                    "depth": depth,
                    "name": request.POST.get(f"name_{depth}", ""),
                    "field": request.POST.get(f"field_{depth}", ""),
                }
                for depth in range(1, MAX_LEVELS + 1)
            ]
        else:
            messages.success(request, "Niveles de ubicación guardados.")
            return redirect("matrix:levels", project.pk)
    return _render(
        request,
        ctx,
        "suno/matrix/levels.html",
        {
            **_context(ctx, project),
            "rows": rows,
            "fields": _custom_fields(ctx, project),
            "from_project": from_project(current),
            "error": error,
        },
    )


@login_required(login_url="platform_login")
def template_download(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    response = HttpResponse(
        template_bytes(ctx, project),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = content_disposition(
        f"matriz-{project.code or project.name}", ".xlsx", fallback="matriz"
    )
    return response


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    errors: list[str] = []
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Elige un archivo .xlsx."]
        elif upload.size is not None and upload.size > MAX_BYTES:
            errors = ["El archivo supera 20 MB."]
        else:
            try:
                staged = stage_plan(
                    ctx, project, file_name=upload.name or "matriz.xlsx", data=upload.read()
                )
            except PlanRejected as exc:
                errors = exc.errors
            else:
                return redirect("matrix:import_preview", project.pk, staged.pk)
    return _render(
        request,
        ctx,
        "suno/matrix/import.html",
        {
            **_context(ctx, project),
            "errors": errors,
            "levels": project_levels(ctx, project),
            "from_project": from_project(project_levels(ctx, project)),
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    require_manage(ctx)
    imp = (
        scoped_qs(ctx, QuantityPlanImport)
        .defer("source_file")
        .filter(pk=import_id, project=project)
        .first()
    )
    if imp is None:
        raise Http404
    errors: list[str] = []
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "apply":
                applied = apply_plan(ctx, imp.pk)
                messages.success(request, f"Matriz aplicada (versión {applied.number}).")
                return redirect("matrix:items", project.pk)
            if action == "discard":
                discard_plan(ctx, imp.pk)
                messages.info(request, "Importación descartada.")
                return redirect("matrix:import", project.pk)
        except PlanRejected as exc:
            errors = exc.errors
    return _render(
        request,
        ctx,
        "suno/matrix/import_preview.html",
        {
            **_context(ctx, project),
            "imp": imp,
            "s": imp.summary,
            "pending": imp.status == QuantityPlanImportStatus.PENDING,
            "status_labels": STATUS_LABELS,
            "errors": errors,
        },
    )


@login_required(login_url="platform_login")
def history(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    imports = (
        scoped_qs(ctx, QuantityPlanImport)
        .filter(project=project)
        .defer("source_file")
        .select_related("uploaded_by")
        .order_by("-uploaded_at")
    )
    return _render(
        request,
        ctx,
        "suno/matrix/history.html",
        {**_context(ctx, project), "imports": imports},
    )
```

- [ ] **Step 4: Pestaña y plantillas**

En `_header.html`, después del enlace a Presupuestos:

```html
      {% if can_view_budgets %}<a href="{% url 'matrix:items' project.pk %}" {% if tab == "matrix" %}aria-current="page"{% endif %}>Matriz</a>{% endif %}
```

`core/templates/suno/matrix/_nav.html`:

```html
<nav class="pj-tabs mx-nav" aria-label="Secciones de la matriz">
  <a href="{% url 'matrix:items' project.pk %}" {% if sub == "items" %}aria-current="page"{% endif %}>Por ítem</a>
  <a href="{% url 'matrix:locations' project.pk %}" {% if sub == "locations" %}aria-current="page"{% endif %}>Por ubicación</a>
  <a href="{% url 'matrix:history' project.pk %}" {% if sub == "history" %}aria-current="page"{% endif %}>Historial</a>
  <a href="{% url 'matrix:template' project.pk %}">Descargar plantilla</a>
  {% if can_manage_matrix %}
  <a href="{% url 'matrix:import' project.pk %}" {% if sub == "import" %}aria-current="page"{% endif %}>Importar Excel</a>
  <a href="{% url 'matrix:levels' project.pk %}" {% if sub == "levels" %}aria-current="page"{% endif %}>Niveles de ubicación</a>
  {% endif %}
</nav>
```

`core/templates/suno/matrix/items.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Matriz · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="items" %}
<section class="pj-panel">
  <form method="get" class="pj-filters">
    <label>Presupuesto <select name="presupuesto"><option value="">Todos</option>{% for b in budgets %}<option value="{{ b.pk }}" {% if filters.presupuesto == b.pk|stringformat:"s" %}selected{% endif %}>{{ b.name }}</option>{% endfor %}</select></label>
    <label>Capítulo <select name="capitulo"><option value="">Todos</option>{% for pk, label in chapters %}<option value="{{ pk }}" {% if filters.capitulo == pk|stringformat:"s" %}selected{% endif %}>{{ label|truncatechars:60 }}</option>{% endfor %}</select></label>
    <label>Ubicación <select name="ubicacion"><option value="">Todas</option>{% for pk, label in nodes %}<option value="{{ pk }}" {% if filters.ubicacion == pk|stringformat:"s" %}selected{% endif %}>{{ label|truncatechars:60 }}</option>{% endfor %}</select></label>
    <label>Estado <select name="estado"><option value="">Todos</option>{% for key, label in status_labels.items %}<option value="{{ key }}" {% if filters.estado == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Filtrar</button>
  </form>
</section>
<section class="pj-panel">
  <div class="pj-scroll">
  <table class="pj-table mx-items">
    <thead><tr><th>Ítem</th><th>Descripción</th><th>Und</th><th class="num">Presupuestado</th><th class="num">Planeado</th><th class="num">Ejecutado</th><th class="num">% ejec.</th><th class="num">Sin asignar</th><th>Estado</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr class="pj-stage">
        <td>{{ row.code }}</td>
        <td class="pj-name">{{ row.description }}{% if row.budget and budgets|length > 1 %} <small class="pj-muted">{{ row.budget.name }}</small>{% endif %}</td>
        <td>{{ row.unit }}</td>
        <td class="num">{{ row.budgeted|floatformat:"-4g"|default:"—" }}</td>
        <td class="num">{{ row.planned|floatformat:"-4g" }}</td>
        <td class="num">{{ row.executed|floatformat:"-4g" }}</td>
        <td class="num">{% if row.pct_executed is not None %}{{ row.pct_executed|floatformat:"-2" }} %{% else %}—{% endif %}</td>
        <td class="num">{{ row.unassigned|floatformat:"-4g"|default:"—" }}</td>
        <td><span class="bg-label mx-{{ row.status }}">{{ row.status_label }}</span></td>
      </tr>
      {% for t in row.tasks %}
      <tr>
        <td></td>
        <td class="pj-name"><a href="{% url 'projects:task' project.pk t.task.pk %}">{{ t.task.outline_number }} {{ t.task.name }}</a>{% if t.location %} <small class="pj-muted">· {{ t.location }}</small>{% endif %}</td>
        <td></td><td></td>
        <td class="num">{{ t.planned|floatformat:"-4g" }}</td>
        <td class="num">{{ t.executed|floatformat:"-4g" }}</td>
        <td></td><td></td><td></td>
      </tr>
      {% endfor %}
    {% empty %}
      <tr><td colspan="9" class="pj-empty">No hay ítems con esos filtros. Carga un presupuesto y la plantilla de la matriz.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endblock %}
```

`core/templates/suno/matrix/locations.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Matriz por ubicación · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="locations" %}
<section class="pj-panel">
  <h2>Valor planeado y ejecutado por ubicación</h2>
  <p class="pj-muted">Cantidades validadas por el valor unitario vigente de cada ítem.</p>
  <div class="pj-scroll">
  <table class="pj-table">
    <thead><tr><th>Ubicación</th><th class="num">Planeado ($)</th><th class="num">Ejecutado ($)</th><th class="num">%</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr class="{% if row.depth == 0 %}bg-total{% elif row.depth == 1 %}pj-stage{% endif %}">
        <td class="pj-name mx-depth-{{ row.depth }}">{{ row.label }}</td>
        <td class="num">{{ row.planned_value|floatformat:"2g" }}</td>
        <td class="num">{{ row.executed_value|floatformat:"2g" }}</td>
        <td class="num">{% if row.pct is not None %}{{ row.pct|floatformat:"-2" }} %{% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endblock %}
```

`core/templates/suno/matrix/levels.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Niveles de ubicación · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="levels" %}
<section class="pj-panel">
  <h2>Niveles de ubicación</h2>
  <p>Del más general al más preciso (p. ej. Tramo → PR → Costado). Deja vacíos los niveles que no uses.</p>
  <p class="pj-muted">Si eliges un campo de MS Project para un nivel, elígelo para todos: la ubicación de las tareas se leerá del archivo en la siguiente importación del cronograma. Sin campos, la ubicación se asigna en la plantilla de la matriz o en cada tarea.</p>
  {% if error %}<p class="pj-late" role="alert">{{ error }}</p>{% endif %}
  <form method="post" class="pj-form">
    {% csrf_token %}
    {% for row in rows %}
    <fieldset class="mx-level">
      <legend>Nivel {{ row.depth }}</legend>
      <label>Nombre <input type="text" name="name_{{ row.depth }}" value="{{ row.name }}" maxlength="60"></label>
      <label>Campo de MS Project <select name="field_{{ row.depth }}"><option value="">Ninguno (se asigna en SUNO)</option>{% for f in fields %}<option value="{{ f.field_id }}" {% if row.field == f.field_id %}selected{% endif %}>{{ f.label }}</option>{% endfor %}{% if row.field and not fields %}<option value="{{ row.field }}" selected>{{ row.field }}</option>{% endif %}</select></label>
    </fieldset>
    {% endfor %}
    {% if not fields %}<p class="pj-muted">El último XML importado no trae campos personalizados de tarea.</p>{% endif %}
    <button class="button" type="submit">Guardar niveles</button>
  </form>
</section>
{% endblock %}
```

`core/templates/suno/matrix/import.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Importar matriz · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Importar la matriz</h2>
  <p><a href="{% url 'matrix:template' project.pk %}">Descarga la plantilla</a> con la matriz actual, completa la columna <strong>Cantidad planeada</strong> (una fila por tarea e ítem; copia filas para agregar ítems) y súbela. Verás los cambios antes de aplicarlos.</p>
  <ul class="pj-muted">
    <li>Los ítems se buscan por presupuesto y código en la versión vigente.</li>
    <li>Una fila con ítem y sin cantidad quita ese ítem de la tarea. Lo que no venga en el archivo se quita de la matriz, salvo lo que ya tiene cantidades ejecutadas, que queda en 0.</li>
    {% if from_project %}<li>La ubicación se lee de MS Project: las columnas de ubicación son solo de referencia.</li>{% elif levels %}<li>Las columnas {% for l in levels %}«{{ l.name }}»{% if not forloop.last %}, {% endif %}{% endfor %} asignan la ubicación de cada tarea.</li>{% else %}<li>Sin <a href="{% url 'matrix:levels' project.pk %}">niveles de ubicación</a>, la plantilla no lleva columnas de ubicación.</li>{% endif %}
  </ul>
  <form method="post" enctype="multipart/form-data" class="pj-form">
    {% csrf_token %}
    <label>Archivo .xlsx <input type="file" name="file" accept=".xlsx" required></label>
    {% if errors %}<div class="pj-late" role="alert"><p>No se puede aplicar el archivo:</p><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
    <button class="button" type="submit">Ver cambios</button>
  </form>
</section>
{% endblock %}
```

`core/templates/suno/matrix/import_preview.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Vista previa de la matriz · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Cambios de {{ imp.file_name }}</h2>
  <p>{{ s.pairs }} par{{ s.pairs|pluralize:"es" }} tarea × ítem en el archivo.</p>
  <ul>
    <li><strong>{{ s.added }} nuevo{{ s.added|pluralize }}</strong>{% if s.examples.added %}: {{ s.examples.added|join:"; " }}{% endif %}</li>
    <li><strong>{{ s.changed }} con otra cantidad</strong>{% if s.examples.changed %}: {{ s.examples.changed|join:"; " }}{% endif %}</li>
    <li><strong>{{ s.removed }} se quita{{ s.removed|pluralize:"n" }}</strong>{% if s.examples.removed %}: {{ s.examples.removed|join:"; " }}{% endif %}</li>
    <li><strong>{{ s.kept_zero }} queda{{ s.kept_zero|pluralize:"n" }} en 0</strong> (no vienen en el archivo pero ya tienen ejecución){% if s.examples.kept_zero %}: {{ s.examples.kept_zero|join:"; " }}{% endif %}</li>
    {% if s.location_changes %}<li><strong>{{ s.location_changes }} tarea{{ s.location_changes|pluralize }} cambia{{ s.location_changes|pluralize:"n" }} de ubicación</strong>: {{ s.location_examples|join:"; " }}</li>{% endif %}
    {% if s.new_nodes %}<li><strong>{{ s.new_nodes }} ubicaci{{ s.new_nodes|pluralize:"ón nueva,ones nuevas" }}</strong></li>{% endif %}
  </ul>
  {% for warning in s.warnings %}<p class="pj-warning">{{ warning }}</p>{% endfor %}
  {% if errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
  {% if pending %}
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <button class="button" type="submit" name="action" value="apply">Aplicar</button>
    <button class="text-link" type="submit" name="action" value="discard">Descartar</button>
  </form>
  {% else %}<p class="pj-muted">Esta importación ya fue {{ imp.get_status_display|lower }}.</p>{% endif %}
</section>
<section class="pj-panel">
  <h2>Ítems frente al presupuesto</h2>
  <p>{{ s.item_counts.ok }} cuadra{{ s.item_counts.ok|pluralize:"n" }} · {{ s.item_counts.over }} con exceso · {{ s.item_counts.under }} sin asignar completo ({{ s.item_counts.unassigned }} sin ninguna tarea) · {{ s.item_counts.retired }} retirado{{ s.item_counts.retired|pluralize }} del presupuesto</p>
  {% if s.items %}
  <div class="pj-scroll">
  <table class="pj-table">
    <thead><tr><th>Ítem</th><th>Descripción</th><th>Und</th><th class="num">Presupuestado</th><th class="num">Planeado</th><th class="num">Diferencia</th><th>Estado</th></tr></thead>
    <tbody>
    {% for row in s.items %}
      <tr><td>{{ row.code }}</td><td class="pj-name">{{ row.description }}</td><td>{{ row.unit }}</td>
        <td class="num">{{ row.budgeted|floatformat:"-4g"|default:"—" }}</td>
        <td class="num">{{ row.planned|floatformat:"-4g" }}</td>
        <td class="num">{{ row.diff|floatformat:"-4g"|default:"—" }}</td>
        <td><span class="bg-label mx-{{ row.status }}">{% if row.status == "over" %}Con exceso{% elif row.status == "under" %}Sin asignar completo{% else %}Retirado{% endif %}</span></td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
</section>
{% endblock %}
```

`core/templates/suno/matrix/history.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Historial de la matriz · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="matrix" %}
{% include "suno/matrix/_nav.html" with sub="history" %}
<section class="pj-panel">
  <h2>Importaciones de la matriz</h2>
  <table class="pj-table">
    <thead><tr><th>Versión</th><th>Archivo</th><th>Subida</th><th>Por</th><th>Estado</th></tr></thead>
    <tbody>
    {% for imp in imports %}
      <tr>
        <td>{% if imp.number %}v{{ imp.number }}{% else %}—{% endif %}</td>
        <td class="pj-wrap">{% if can_manage_matrix %}<a href="{% url 'matrix:import_preview' project.pk imp.pk %}">{{ imp.file_name }}</a>{% else %}{{ imp.file_name }}{% endif %}</td>
        <td>{{ imp.uploaded_at|date:"d/m/Y H:i" }}</td>
        <td>{{ imp.uploaded_by.display_name|default:imp.uploaded_by.username }}</td>
        <td>{{ imp.get_status_display }}</td>
      </tr>
    {% empty %}
      <tr><td colspan="5" class="pj-empty">Todavía no se ha importado la matriz.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

Al final de `projects.css`:

```css
/* Matriz de homologación (costos, pieza 4). */
.mx-nav { margin: 16px 0 0; }
.mx-items td.pj-name { min-width: 260px; max-width: 420px; }
.bg-label.mx-over { background: var(--suno-amber); color: var(--suno-ink); }
.bg-label.mx-under { background: var(--suno-topography); color: var(--suno-ink); }
.bg-label.mx-ok { background: var(--suno-green); }
.bg-label.mx-retired { background: var(--suno-red); }
.mx-depth-2 { padding-left: 24px !important; }
.mx-depth-3 { padding-left: 40px !important; }
.mx-depth-4, .mx-depth-5, .mx-depth-6 { padding-left: 56px !important; }
.mx-level { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); border: 1px solid var(--suno-line); padding: 12px; }
@media (max-width: 767px) { .mx-items td.pj-name { min-width: 180px; max-width: 240px; } }
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix visitacampo/projects visitacampo/budgets`
Expected: PASS. En `test_locations_page`, Tramo 1 = 4 × 450.000 = 1.800.000,00.

- [ ] **Step 6: Commit**

```bash
git add server/config/urls.py server/visitacampo/matrix server/visitacampo/core/templates server/visitacampo/core/static
git commit -m "feat(matrix): matrix pages, levels, template download and import

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Ficha de tarea — cantidades planeadas, edición y ubicación

**Files:**
- Modify: `server/visitacampo/projects/views.py` (`task_detail`)
- Modify: `server/visitacampo/core/templates/suno/projects/task.html`
- Test: `server/visitacampo/matrix/tests/test_task_page.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_task_page.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.models import QuantityPlan
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan, quantity_row, quantity_visit, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TaskPageTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        self.world.approve(quantity_visit(self.world, 3, quantity_row(self.concrete, "4")))
        self.url = (
            f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"
        )

    def test_block_with_amounts_for_budget_viewers(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Cantidades planeadas")
        self.assertContains(response, "Concreto zapatas")
        self.assertContains(response, "4.500.000,00")  # valor planeado
        self.assertNotContains(response, "Agregar ítem")  # supervisión no edita

    def test_field_agent_sees_quantities_without_amounts(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        response = self.client.get(self.url)
        self.assertContains(response, "Concreto zapatas")
        self.assertNotContains(response, "4.500.000,00")

    def test_admin_edits_plan_and_location(self) -> None:
        self.client.force_login(self.world.admin)
        self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "2.2", "quantity": "50"},
        )
        self.assertEqual(QuantityPlan.objects.count(), 2)
        response = self.client.post(
            self.url,
            {"action": "plan_set", "budget": self.budget.pk, "code": "9.9", "quantity": "1"},
        )
        self.assertContains(response, "no existe")
        self.client.post(self.url, {"action": "location_set", "location": "Tramo 4"})
        self.assertContains(self.client.get(self.url), "Tramo 4")
        self.client.post(
            self.url, {"action": "plan_remove", "item": item(self.budget, "2.2").pk}
        )
        self.assertEqual(QuantityPlan.objects.count(), 1)
```

(El agente ve el proyecto, y por tanto la ficha, cuando es responsable de una tarea: `projects.access.visible_projects`.)

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_task_page.py`
Expected: FAIL (`Cantidades planeadas` no está en la página).

- [ ] **Step 3: Datos y acciones en `task_detail`**

En `projects/views.py`, imports:

```python
from visitacampo.core.policies import can_manage_budgets, can_view_budgets
from visitacampo.matrix.edits import EditRejected, remove_plan, set_plan, set_task_location
from visitacampo.matrix.locations import from_project, load_tree, project_levels
from visitacampo.matrix.quantities import executed_by_pair, item_facts
```

(`can_view_budgets` ya se importa para `_project_context`; sumar `can_manage_budgets`.)

Dentro de `task_detail`, al comienzo del `if request.method == "POST":` (antes del manejo de responsables):

```python
        action = request.POST.get("action")
        if action in ("plan_set", "plan_remove", "location_set"):
            if not can_manage_budgets(ctx, ctx.org_id):
                raise PermissionDenied
            try:
                if action == "plan_set":
                    budget_id = _parse_uuid(request.POST.get("budget"))
                    if budget_id is None:
                        raise EditRejected("Elige un presupuesto.")
                    set_plan(
                        ctx,
                        task,
                        budget_id=budget_id,
                        code=request.POST.get("code", ""),
                        quantity=request.POST.get("quantity", ""),
                    )
                elif action == "plan_remove":
                    item_id = _parse_uuid(request.POST.get("item"))
                    if item_id is not None and not remove_plan(ctx, task, item_id):
                        messages.info(
                            request, "El ítem ya tiene cantidades ejecutadas: quedó en 0."
                        )
                else:
                    set_task_location(ctx, task, request.POST.get("location", ""))
            except EditRejected as exc:
                plan_error = str(exc)
            else:
                return redirect("projects:task", project.pk, task.pk)
```

con `plan_error = ""` declarado junto a `error = ""`.

Antes del `return _render(...)`:

```python
    levels = project_levels(ctx, project)
    tree = load_tree(ctx, project)
    plans = list(scoped_qs(ctx, QuantityPlan).filter(task=task))
    executed = executed_by_pair(ctx, task_ids=[task.pk])
    facts = item_facts(ctx, [p.item_id for p in plans])
    show_amounts = can_view_budgets(ctx, ctx.org_id)
    plan_rows = sorted(
        (
            {
                "item_id": p.item_id,
                "code": facts[p.item_id].code if p.item_id in facts else "?",
                "description": facts[p.item_id].description if p.item_id in facts else "",
                "unit": facts[p.item_id].unit if p.item_id in facts else "",
                "planned": p.planned_quantity,
                "executed": executed.get((task.pk, p.item_id), Decimal(0)),
                "value": (
                    money(p.planned_quantity * (facts[p.item_id].unit_price or 0))
                    if show_amounts and p.item_id in facts
                    else None
                ),
            }
            for p in plans
        ),
        key=lambda row: str(row["code"]),
    )
    # Ítems con cantidades validadas que no están en el plan de la tarea: no suman al %.
    unplanned = item_facts(
        ctx, {item_id for (_, item_id) in executed} - {p.item_id for p in plans}
    )
    unplanned_executed = sorted(unplanned.values(), key=lambda f: f.code)
```

y en el contexto:

```python
            "plan_rows": plan_rows,
            "unplanned_executed": unplanned_executed,
            "show_amounts": show_amounts,
            "can_edit_plan": can_manage_budgets(ctx, ctx.org_id) and not task.is_summary,
            "plan_error": plan_error,
            "location_label": tree.label(task.location_id),
            "location_editable": bool(levels) and not from_project(levels),
            "location_hint": " / ".join(level.name for level in levels),
            "location_choices": sorted(tree.label(pk) for pk in tree.nodes),
            "budget_choices": scoped_qs(ctx, Budget).filter(project=project).order_by("name"),
```

Imports nuevos: `from decimal import Decimal`, `from visitacampo.budgets.totals import money`, y `Budget`, `QuantityPlan` en el import de `visitacampo.core.models`.

- [ ] **Step 4: Bloque en `task.html`**

Después de la sección "Avance de campo":

```html
{% if not task.is_summary %}
<section class="pj-panel">
  <h2>Cantidades planeadas</h2>
  <p>Ubicación: <strong>{{ location_label|default:"sin ubicación" }}</strong>
  {% if can_edit_plan and location_editable %}
  </p>
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <input type="hidden" name="action" value="location_set">
    <label>Ruta ({{ location_hint }}) <input type="text" name="location" value="{{ location_label }}" list="mx-locations" size="40"></label>
    <datalist id="mx-locations">{% for label in location_choices %}<option value="{{ label }}">{% endfor %}</datalist>
    <button class="button" type="submit">Cambiar ubicación</button>
  </form>
  {% else %}</p>{% endif %}
  {% if plan_rows %}
  <div class="pj-scroll">
  <table class="pj-table">
    <thead><tr><th>Ítem</th><th>Descripción</th><th>Und</th><th class="num">Planeado</th><th class="num">Ejecutado</th>{% if show_amounts %}<th class="num">Valor planeado</th>{% endif %}{% if can_edit_plan %}<th></th>{% endif %}</tr></thead>
    <tbody>
    {% for row in plan_rows %}
      <tr>
        <td>{{ row.code }}</td><td class="pj-name">{{ row.description }}</td><td>{{ row.unit }}</td>
        <td class="num">{{ row.planned|floatformat:"-4g" }}</td>
        <td class="num">{{ row.executed|floatformat:"-4g" }}</td>
        {% if show_amounts %}<td class="num">{{ row.value|floatformat:"2g"|default:"—" }}</td>{% endif %}
        {% if can_edit_plan %}<td><form method="post">{% csrf_token %}<input type="hidden" name="action" value="plan_remove"><input type="hidden" name="item" value="{{ row.item_id }}"><button class="text-link" type="submit" aria-label="Quitar {{ row.code }}">Quitar</button></form></td>{% endif %}
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-muted">La tarea no tiene ítems planeados: su avance es el % de las visitas.</p>{% endif %}
  {% if unplanned_executed %}<p class="pj-warning">Cantidades registradas en ítems fuera del plan (no suman al %): {% for f in unplanned_executed %}{{ f.code }} {{ f.description }}{% if not forloop.last %}; {% endif %}{% endfor %}.</p>{% endif %}
  {% if plan_error %}<p class="pj-late" role="alert">{{ plan_error }}</p>{% endif %}
  {% if can_edit_plan and not retired %}
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <input type="hidden" name="action" value="plan_set">
    <label>Presupuesto <select name="budget">{% for b in budget_choices %}<option value="{{ b.pk }}">{{ b.name }}</option>{% endfor %}</select></label>
    <label>Código <input type="text" name="code" maxlength="50" size="10" required></label>
    <label>Cantidad <input type="text" name="quantity" inputmode="decimal" size="10" required></label>
    <button class="button" type="submit">Agregar ítem</button>
    <small class="pj-muted">Si el ítem ya está, se cambia su cantidad.</small>
  </form>
  {% endif %}
</section>
{% endif %}
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix visitacampo/projects`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects/views.py server/visitacampo/core/templates server/visitacampo/matrix
git commit -m "feat(projects): planned quantities block on the task page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 15: Vista previa de presupuestos — ítems eliminados con plan o ejecución

**Files:**
- Modify: `server/visitacampo/budgets/importer.py` (`prepare_version`)
- Test: `server/visitacampo/matrix/tests/test_budget_warning.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_budget_warning.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.budgets.tests.factories import stage_rows
from visitacampo.budgets.tests.samples import V2_ROWS
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BudgetPreviewWarningTests(TestCase):
    def test_removed_item_in_matrix_is_warned(self) -> None:
        world, budget = budget_world()
        plan(world, 3, item(budget, "1.2"), "5")  # V2 elimina el 1.2
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertIn(
            "1 ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: 1.2",
            " ".join(version.summary["warnings"]),
        )

    def test_no_warning_without_matrix(self) -> None:
        world, budget = budget_world()
        version = stage_rows(world.ctx(), budget, V2_ROWS)
        self.assertFalse(any("matriz" in w for w in version.summary["warnings"]))
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_budget_warning.py`
Expected: FAIL (el aviso no está).

- [ ] **Step 3: El aviso**

En `budgets/importer.py`, función nueva antes de `prepare_version`:

```python
def _matrix_warning(
    ctx: RequestContext, budget: Budget, current: Mapping[str, Values], parsed: ParsedBudget
) -> str | None:
    """Ítems de la versión vigente que la nueva quita y que están en la matriz o tienen
    cantidades registradas en campo (costos, pieza 4)."""
    new_codes = {row.code for row in parsed.rows}
    gone = {code for code, values in current.items() if values.kind == "item"} - new_codes
    if not gone:
        return None
    ids = dict(
        scoped_qs(ctx, BudgetItem).filter(budget=budget, code__in=gone).values_list("pk", "code")
    )
    used = set(
        scoped_qs(ctx, QuantityPlan).filter(item_id__in=ids).values_list("item_id", flat=True)
    )
    executed = {item_id for (_, item_id) in executed_by_pair(ctx, project_ids=[budget.project_id])}
    codes = sorted(ids[i] for i in ids if i in used or i in executed)
    if not codes:
        return None
    return (
        f"{len(codes)} ítem(s) eliminado(s) están en la matriz o tienen cantidades ejecutadas: "
        + ", ".join(codes[:20])
        + ". Sus filas de la matriz se conservan marcadas como retiradas del presupuesto."
    )
```

Imports: `from collections.abc import Mapping` (si no está), `QuantityPlan` en el import de `visitacampo.core.models`, y `from visitacampo.matrix.quantities import executed_by_pair` **dentro de la función** (import local: `matrix.quantities` importa `budgets` a través de `matrix.progress` → `budgets.totals`, y un import de módulo crearía un ciclo `budgets.importer` → `matrix` → `budgets`). `ParsedBudget.rows`, `ParsedRow.code` y `Values.kind` son los de `budgets/excel.py` y `budgets/diff.py`.

En `prepare_version`, después de `summary = summarize_changes(...)`:

```python
        values = _values_by_code(ctx, current)
        warning = _matrix_warning(ctx, budget, values, parsed)
        if warning is not None:
            summary.setdefault("warnings", []).append(warning)
```

(y pasar `values` a `summarize_changes` en lugar de volver a llamar a `_values_by_code`).

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_budget_warning.py visitacampo/budgets`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/budgets server/visitacampo/matrix
git commit -m "feat(budgets): warn about removed items used by the quantity matrix

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: `quantities` en el contrato, el payload, el SQL y el pull

**Files:**
- Modify: `server/openapi/v1.yaml` (`Visit`, `VisitPayload`, schemas nuevos `VisitQuantity` y `VisitQuantityPayload`)
- Modify: `server/visitacampo/sync/payloads.py`
- Modify: `server/visitacampo/sync/sql.py`
- Modify: `server/visitacampo/sync/records.py`, `server/visitacampo/sync/schemas.py`
- Test: `server/visitacampo/sync/tests/test_payloads.py`, `server/visitacampo/sync/tests/test_sql.py` (clases nuevas)

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `sync/tests/test_payloads.py` (reusa `_create_payload` y `parse_op` de ese archivo):

```python
class QuantitiesPayloadTests(SimpleTestCase):
    ITEM = str(uuid.uuid4())
    TASK = str(uuid.uuid4())

    def _create(self, **fields: object) -> VisitWrite | Rejection:
        parsed = parse_op("visit", "create", None, _create_payload(**fields))
        assert not isinstance(parsed, AttachmentWrite)
        return parsed

    def _update(self, **fields: object) -> VisitWrite | Rejection:
        payload = {"id": str(uuid.uuid4()), **fields}
        parsed = parse_op("visit", "update", 1, payload)
        assert not isinstance(parsed, AttachmentWrite)
        return parsed

    def _row(self, **values: object) -> dict[str, object]:
        return {"item_id": self.ITEM, "quantity": 12.5, **values}

    def test_quantities_are_normalized(self) -> None:
        write = self._create(task_id=self.TASK, progress_pct=None, quantities=[self._row()])
        assert isinstance(write, VisitWrite)
        self.assertEqual(
            write.fields["quantities"],
            [{"item_id": self.ITEM, "location_id": None, "quantity": "12.5000", "note": ""}],
        )

    def test_defaults_to_empty_list(self) -> None:
        write = self._create()
        assert isinstance(write, VisitWrite)
        self.assertEqual(write.fields["quantities"], [])

    def test_task_needs_pct_or_quantities(self) -> None:
        rejection = self._create(task_id=self.TASK, progress_pct=None)
        assert isinstance(rejection, Rejection)
        self.assertEqual(rejection.details["reason"], "task_progress_pair")

    def test_quantities_need_a_task(self) -> None:
        rejection = self._create(quantities=[self._row()])
        assert isinstance(rejection, Rejection)
        self.assertEqual(
            rejection.details, {"field": "quantities", "reason": "quantities_without_task"}
        )

    def test_quantity_shape(self) -> None:
        for bad in (0, "3", True, 1e15, 1.23456, float("nan")):
            rejection = self._create(
                task_id=self.TASK, progress_pct=None, quantities=[self._row(quantity=bad)]
            )
            assert isinstance(rejection, Rejection), bad
            self.assertEqual(rejection.code, "validation_error", bad)
        too_many = [self._row()] * 201
        rejection = self._create(task_id=self.TASK, progress_pct=None, quantities=too_many)
        assert isinstance(rejection, Rejection)
        self.assertEqual(rejection.code, "validation_error")

    def test_update_without_quantities_clears_them(self) -> None:
        write = self._update(task_id=self.TASK, progress_pct=20)
        assert isinstance(write, VisitWrite)
        self.assertEqual(write.fields["quantities"], [])

    def test_update_with_quantities_alone_is_rejected(self) -> None:
        rejection = self._update(quantities=[self._row()])
        assert isinstance(rejection, Rejection)
        self.assertEqual(rejection.details["reason"], "task_progress_pair")
```

(`AttachmentWrite` se suma al import de `visitacampo.sync.payloads` del archivo de pruebas.)

Al final de `sync/tests/test_sql.py`, una prueba de que `build_insert` y `build_update` envían `quantities` como `%s::jsonb` con el JSON serializado (seguir el estilo de la prueba existente de `answers`).

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/sync/tests/test_payloads.py visitacampo/sync/tests/test_sql.py`
Expected: FAIL (`quantities` no es un campo permitido: `extra_forbidden`).

- [ ] **Step 3: `payloads.py`**

Tipos nuevos, después de `ProgressPct`:

```python
#: Costos, pieza 4: cantidad ejecutada de un ítem. `numeric(18,4)`: hasta 14
#: dígitos enteros (|q| < 10¹⁴) y 4 decimales; distinta de 0 (validador).
QuantityValue = Annotated[Decimal, BeforeValidator(_json_number)]

#: Nota de una cantidad: obligatoria para ajustes negativos e ítems fuera del plan.
QuantityNote = Annotated[str, StringConstraints(max_length=500), AfterValidator(_text_safe)]

#: Filas de `quantities` por visita.
MAX_QUANTITY_ROWS = 200

_QUANTITY_STEP = Decimal("0.0001")


class _QuantityPayload(BaseModel):
    """Contrato `VisitQuantityPayload`."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    location_id: uuid.UUID | None = None
    quantity: QuantityValue = Field(max_digits=18, decimal_places=4)
    note: QuantityNote = ""

    @field_validator("quantity")
    @classmethod
    def _non_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("must not be zero.")
        return value


def stored_quantities(rows: list[Any]) -> list[dict[str, Any]]:
    """La forma guardada en `Visit.quantities`: ids como texto y la cantidad como
    decimal de 4 decimales en texto (exacto). Es también la forma con la que se
    compara una visita validada (`services._References.validated_rejection`)."""
    stored: list[dict[str, Any]] = []
    for row in rows:
        data = row.model_dump() if isinstance(row, BaseModel) else row
        location = data.get("location_id")
        stored.append(
            {
                "item_id": str(data["item_id"]),
                "location_id": None if location is None else str(location),
                "quantity": str(Decimal(data["quantity"]).quantize(_QUANTITY_STEP)),
                "note": str(data.get("note") or "").strip(),
            }
        )
    return stored
```

En `_VisitCreatePayload`, después de `progress_pct`:

```python
    # Costos, pieza 4. Opcional: un cliente anterior no lo envía.
    quantities: list[_QuantityPayload] = Field(
        default_factory=list, max_length=MAX_QUANTITY_ROWS
    )
```

En `_VisitUpdatePayload`, después de `progress_pct`:

```python
    quantities: list[_QuantityPayload] | None = Field(
        default=None, max_length=MAX_QUANTITY_ROWS
    )
```

`"quantities"` se suma a `_NON_NULLABLE_ON_UPDATE` (se omite, no se manda `null`) y a `_UPDATABLE_FIELDS`.

`_task_pair_rejection` se reemplaza por:

```python
def _task_pair_rejection(fields: dict[str, Any], entity_id: uuid.UUID) -> Rejection | None:
    """Regla del trío (`task_id`, `progress_pct`, `quantities`).

    - `task_id` y `progress_pct` viajan siempre juntos (también en `update`), y
      `quantities` solo viaja con ellos.
    - Sin tarea: ni % ni cantidades. Desvincular vacía las cantidades.
    - Con tarea: un % o al menos una cantidad (o ambos).
    - Un `update` con tarea y % pero sin `quantities` deja la lista vacía: un
      cliente anterior a la pieza 4 nunca tiene cantidades.

    Lo exige también la BD (`visit_task_progress_pair`), pero allí sería un
    `IntegrityError` que tumba el lote entero. Normaliza `fields["quantities"]`.
    """
    has_task, has_pct = "task_id" in fields, "progress_pct" in fields
    if not has_task and not has_pct and "quantities" not in fields:
        return None
    if not (has_task and has_pct):
        return _rejection(
            "task_id and progress_pct must be sent together (also with quantities).",
            field="progress_pct" if has_task else "task_id",
            reason="task_progress_pair",
            entity_id=entity_id,
        )
    task_id, pct = fields["task_id"], fields["progress_pct"]
    quantities = stored_quantities(fields.get("quantities") or [])
    fields["quantities"] = quantities
    if task_id is None:
        if pct is not None:
            return _rejection(
                "progress_pct needs a task_id.",
                field="progress_pct",
                reason="task_progress_pair",
                entity_id=entity_id,
            )
        if quantities:
            return _rejection(
                "quantities need a task_id.",
                field="quantities",
                reason="quantities_without_task",
                entity_id=entity_id,
            )
        return None
    if pct is None and not quantities:
        return _rejection(
            "A visit linked to a task needs progress_pct or at least one quantity.",
            field="progress_pct",
            reason="task_progress_pair",
            entity_id=entity_id,
        )
    return None
```

Las pruebas existentes de `task_progress_pair` siguen pasando: tarea sin % (y sin cantidades) se rechaza; % sin tarea se rechaza; un `update` con solo uno de los dos se rechaza.

- [ ] **Step 4: `sql.py`**

- `_MODEL_FIELD_BY_CONTRACT_NAME` gana `"quantities": "quantities",`.
- `_INSERT_FIELDS` gana `"quantities",` al final.
- `build_insert`: el diccionario `values` gana `"quantities": [],` junto a `task_id`/`progress_pct`.
- `_placeholder_and_param`: `if contract_name == "answers":` pasa a `if contract_name in ("answers", "quantities"):` y el docstring menciona las dos columnas JSONB.

- [ ] **Step 5: `records.py` y `schemas.py`**

En `records.py`, función nueva y campo nuevo en `visit_record`:

```python
def quantity_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`Visit.quantities` como lo publica el contrato: la cantidad como número JSON."""
    return [
        {
            "item_id": row["item_id"],
            "location_id": row["location_id"],
            "quantity": float(Decimal(row["quantity"])),
            "note": row["note"],
        }
        for row in rows
    ]
```

y en `visit_record`, después de `progress_pct`:

```python
            "quantities": quantity_records(visit.quantities),
```

(import `from decimal import Decimal`).

En `sync/schemas.py`, antes del esquema del registro de visita:

```python
class VisitQuantityRecord(Schema):
    """Contrato `VisitQuantity`."""

    item_id: uuid.UUID
    location_id: uuid.UUID | None
    quantity: float
    note: str
```

y el esquema de datos de la visita gana `quantities: list[VisitQuantityRecord]` después de `progress_pct`.

- [ ] **Step 6: Contrato `v1.yaml`**

En `components.schemas.Visit`: `quantities` al final de `required`, y en `properties` después de `progress_pct`:

```yaml
        quantities:
          type: array
          description: |
            Costos, pieza 4: cantidades ejecutadas en esta visita por ítem del presupuesto. Cuentan
            para el avance de la tarea cuando la visita está validada. Lista vacía si no hay.
          items:
            $ref: '#/components/schemas/VisitQuantity'
```

En `components.schemas.VisitPayload.properties`, después de `progress_pct`:

```yaml
        quantities:
          type: array
          maxItems: 200
          description: |
            Opcional (un cliente anterior no lo envía). Reemplaza la lista completa. Solo viaja con
            `task_id` y `progress_pct`; con tarea, `progress_pct` puede ser `null` si hay al menos
            una cantidad. Un `update` con `task_id` y `progress_pct` sin `quantities` deja la lista
            vacía. Rechazos (`validation_error`): `quantities_without_task`, `task_progress_pair`,
            `item_not_in_project` (el ítem no es de un presupuesto del proyecto de la tarea),
            `location_outside_task` (la ubicación no es la de la tarea ni está dentro de ella),
            `note_required` (cantidad negativa, o ítem fuera del plan de la tarea, sin nota) y
            `visit_validated` (la visita validada no cambia sus cantidades).
          items:
            $ref: '#/components/schemas/VisitQuantityPayload'
```

Schemas nuevos, después de `Visit`:

```yaml
    VisitQuantity:
      type: object
      description: Cantidad ejecutada de un ítem del presupuesto en una visita (costos, pieza 4).
      required: [item_id, location_id, quantity, note]
      properties:
        item_id:
          $ref: '#/components/schemas/Uuid'
        location_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'
          description: Ubicación precisada en campo; `null` = la ubicación de la tarea.
        quantity:
          type: number
          description: Distinta de 0, hasta 4 decimales. Negativa solo como ajuste.
        note:
          type: string
          maxLength: 500

    VisitQuantityPayload:
      type: object
      additionalProperties: false
      required: [item_id, quantity]
      properties:
        item_id:
          $ref: '#/components/schemas/Uuid'
        location_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'
          description: El nodo de la tarea o uno de sus descendientes (`FieldTask.location_options`).
        quantity:
          type: number
          description: Distinta de 0, |cantidad| < 10^14, hasta 4 decimales.
        note:
          type: string
          maxLength: 500
          description: Obligatoria si la cantidad es negativa o el ítem no está en el plan de la tarea.
```

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/sync visitacampo/core/tests`
Expected: PASS, incluido el test de deriva contrato↔backend (si falla por un nombre de schema, alinear el nombre del `Schema` de Ninja con el del contrato). Las 4 fallas conocidas de `test_load_script.py` aparte.

- [ ] **Step 8: Commit**

```bash
git add server/openapi/v1.yaml server/visitacampo/sync
git commit -m "feat(sync): visit quantities in push payload, SQL and pull records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Validar las cantidades en el push

**Files:**
- Modify: `server/visitacampo/sync/services.py` (`_TaskRef`, `_References`, `_task_refs`, `_validated_progress`, `_resolve_references`, el bucle de validación)
- Test: `server/visitacampo/sync/tests/test_push_quantities.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/sync/tests/test_push_quantities.py`:

```python
"""Push de visitas con cantidades (costos, pieza 4). Requiere PostgreSQL."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Any

from django.test import TestCase
from django.utils import timezone

from visitacampo.authn.tests.factories import make_user
from visitacampo.budgets.tests.factories import apply_rows, make_budget
from visitacampo.budgets.tests.samples import V1_ROWS
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    BudgetItem,
    Project,
    QuantityPlan,
    Role,
    Task,
    TaskResponsible,
    VisitReview,
    VisitReviewStatus,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.matrix.locations import LevelSpec, load_tree, project_levels, save_levels
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.samples import v1
from visitacampo.sync.cursor import CURSOR_ZERO

from .factories import build_scenario, create_op, pull, push, update_op, visit_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class QuantityPushTests(TestCase):
    """El agente del escenario es responsable de la tarea 1.2 Descapote (uid 3)."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        org = self.scenario.org
        self.admin = make_user(org, username="admin-cantidades", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=org.pk))
        self.project = Project.objects.create(org=org, name="Obra", created_by=self.admin)
        staged = stage_import(self.ctx, self.project, file_name="x.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        TaskResponsible.objects.create(
            org=org, task=self.task(3), user=self.scenario.user, assigned_by=self.admin
        )
        budget = make_budget(self.project, self.admin)
        apply_rows(self.ctx, budget, V1_ROWS)
        self.concrete = BudgetItem.objects.get(budget=budget, code="2.1")
        self.steel = BudgetItem.objects.get(budget=budget, code="2.2")
        save_levels(self.ctx, self.project, [LevelSpec("Tramo"), LevelSpec("PR")])
        levels = project_levels(self.ctx, self.project)
        tree = load_tree(self.ctx, self.project)
        self.tramo, _ = tree.ensure(self.ctx, levels, ("Tramo 2",))
        self.pr, _ = tree.ensure(self.ctx, levels, ("Tramo 2", "PR 1"))
        self.other, _ = tree.ensure(self.ctx, levels, ("Tramo 3",))
        task = self.task(3)
        task.location = self.tramo
        task.save(update_fields=["location"])
        QuantityPlan.objects.create(
            org=org,
            project=self.project,
            task=task,
            item=self.concrete,
            planned_quantity=Decimal("10"),
            updated_by=self.admin,
            updated_at=timezone.now(),
        )

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def row(self, item: BudgetItem, quantity: object, **extra: object) -> dict[str, Any]:
        return {"item_id": str(item.pk), "quantity": quantity, **extra}

    def push_one(self, op: dict[str, Any]) -> dict[str, Any]:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def create(self, *rows: dict[str, Any], visit_id: uuid.UUID | None = None) -> dict[str, Any]:
        return create_op(
            self.scenario,
            visit_id=visit_id,
            task_id=str(self.task(3).pk),
            progress_pct=None,
            quantities=list(rows),
        )

    def assertRejected(self, result: dict[str, Any], reason: str) -> None:
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["details"], {"field": "quantities", "reason": reason})

    def test_planned_item_with_refined_location(self) -> None:
        visit_id = uuid.uuid4()
        result = self.push_one(
            self.create(self.row(self.concrete, 4.5, location_id=str(self.pr.pk)), visit_id=visit_id)
        )
        self.assertEqual(result["status"], "applied", result)
        visit = visit_of(visit_id)
        self.assertEqual(visit.quantities[0]["quantity"], "4.5000")
        self.assertIsNone(visit.progress_pct)
        records = pull(self.scenario, CURSOR_ZERO).json()["records"]
        data = next(r["data"] for r in records if r["entity"] == "visit")
        self.assertEqual(data["quantities"][0]["quantity"], 4.5)

    def test_location_outside_the_task(self) -> None:
        result = self.push_one(self.create(self.row(self.concrete, 1, location_id=str(self.other.pk))))
        self.assertRejected(result, "location_outside_task")

    def test_item_of_another_project(self) -> None:
        result = self.push_one(self.create({"item_id": str(uuid.uuid4()), "quantity": 1}))
        self.assertRejected(result, "item_not_in_project")

    def test_unplanned_item_and_negative_need_a_note(self) -> None:
        self.assertRejected(self.push_one(self.create(self.row(self.steel, 3))), "note_required")
        self.assertRejected(
            self.push_one(self.create(self.row(self.concrete, -1))), "note_required"
        )
        result = self.push_one(self.create(self.row(self.steel, 3, note="no estaba en el plan")))
        self.assertEqual(result["status"], "applied", result)

    def test_validated_visit_quantities_cannot_change(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), visit_id=visit_id))
        VisitReview.objects.create(
            org=self.scenario.org,
            visit=visit_of(visit_id),
            status=str(VisitReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(
                visit_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 5)],
            )
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["details"]["reason"], "visit_validated")
        self.assertEqual(visit_of(visit_id).quantities[0]["quantity"], "4.0000")

    def test_unreviewed_visit_can_be_corrected(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.create(self.row(self.concrete, 4), visit_id=visit_id))
        version = visit_of(visit_id).version
        result = self.push_one(
            update_op(
                visit_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[self.row(self.concrete, 6)],
            )
        )
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(visit_of(visit_id).quantities[0]["quantity"], "6.0000")
```

(`build_scenario` crea el usuario del agente con su dispositivo; en `samples.v1()`, 1.2 Descapote es uid 3.)

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/sync/tests/test_push_quantities.py`
Expected: FAIL: `location_outside_task`, `item_not_in_project` y `note_required` llegan como `applied`.

- [ ] **Step 3: Referencias de las cantidades**

En `services.py`, `_TaskRef` gana:

```python
    project_id: uuid.UUID
    #: Ubicación de la tarea (costos, pieza 4): su `path`, o None si no tiene.
    location_path: str | None
```

`_task_refs` agrega `"location__path"` al `values_list` y construye `_TaskRef(..., project_id=project_id, location_path=location_path)`.

`_References` gana:

```python
    #: Costos, pieza 4: ítem → proyecto de su presupuesto.
    items: dict[uuid.UUID, uuid.UUID]
    #: Nodo de ubicación → (proyecto, path).
    nodes: dict[uuid.UUID, tuple[uuid.UUID, str]]
    #: Pares (tarea, ítem) que están en la matriz.
    planned: set[tuple[uuid.UUID, uuid.UUID]]
```

y `validated` pasa a `dict[uuid.UUID, tuple[uuid.UUID | None, Decimal | None, list[Any], int]]`.

Función nueva:

```python
def _quantity_refs(
    ctx: RequestContext, writes: list[VisitWrite]
) -> tuple[
    dict[uuid.UUID, uuid.UUID],
    dict[uuid.UUID, tuple[uuid.UUID, str]],
    set[tuple[uuid.UUID, uuid.UUID]],
]:
    """Ítems, nodos y pares del plan que nombran las cantidades del lote.
    Tres queries para todo el lote; ninguna si no hay cantidades."""
    wanted = [(w.fields.get("task_id"), w.fields.get("quantities") or []) for w in writes]
    item_ids = {uuid.UUID(row["item_id"]) for _, rows in wanted for row in rows}
    if not item_ids:
        return {}, {}, set()
    items = dict(
        scoped_qs(ctx, BudgetItem).filter(pk__in=item_ids).values_list("pk", "budget__project_id")
    )
    node_ids = {
        uuid.UUID(row["location_id"])
        for _, rows in wanted
        for row in rows
        if row["location_id"] is not None
    }
    nodes = {
        pk: (project_id, path)
        for pk, project_id, path in scoped_qs(ctx, LocationNode)
        .filter(pk__in=node_ids)
        .values_list("pk", "project_id", "path")
    }
    task_ids = {task_id for task_id, rows in wanted if task_id is not None and rows}
    planned = set(
        scoped_qs(ctx, QuantityPlan)
        .filter(task_id__in=task_ids, item_id__in=item_ids)
        .values_list("task_id", "item_id")
    )
    return items, nodes, planned
```

`_resolve_references` la llama (`items, nodes, planned = _quantity_refs(ctx, writes)`) y pasa los tres al construir `_References`.

Método nuevo de `_References`:

```python
    def quantities_rejection(self, parsed: VisitWrite) -> Rejection | None:
        """Cada cantidad: ítem de un presupuesto del proyecto de la tarea, ubicación en
        la de la tarea (o dentro), y nota si es un ajuste negativo o el ítem no está en
        el plan. `payloads` ya garantizó que hay tarea si hay cantidades."""
        rows = parsed.fields.get("quantities") or []
        task_id = parsed.fields.get("task_id")
        if not rows or task_id is None:
            return None
        ref = self.tasks[task_id]

        def reject(message: str, reason: str) -> Rejection:
            return Rejection(
                code=CODE_VALIDATION_ERROR,
                message=message,
                details={"field": "quantities", "reason": reason},
                entity_id=parsed.entity_id,
            )

        for row in rows:
            item_id = uuid.UUID(row["item_id"])
            if self.items.get(item_id) != ref.project_id:
                return reject(
                    "The item is not in a budget of the task's project.", "item_not_in_project"
                )
            if row["location_id"] is not None:
                node = self.nodes.get(uuid.UUID(row["location_id"]))
                inside = (
                    node is not None
                    and node[0] == ref.project_id
                    and ref.location_path is not None
                    and node[1].startswith(ref.location_path)
                )
                if not inside:
                    return reject(
                        "The location is not the task's location nor inside it.",
                        "location_outside_task",
                    )
            planned = (task_id, item_id) in self.planned
            if (Decimal(row["quantity"]) < 0 or not planned) and not row["note"]:
                return reject(
                    "A note is required for negative adjustments and unplanned items.",
                    "note_required",
                )
        return None
```

En el bucle de validación, después de `task_problem`:

```python
    # Costos, pieza 4: cantidades coherentes con la tarea, su ubicación y su plan.
    quantity_problem = references.quantities_rejection(parsed)
    if quantity_problem is not None:
        return _rejected_result(op, quantity_problem)
```

- [ ] **Step 4: Una visita validada no cambia sus cantidades**

En `_validated_progress`:

- la condición del `update` pasa a `("task_id" in w.fields or "progress_pct" in w.fields or "quantities" in w.fields)`;
- `.values_list("pk", "task_id", "progress_pct", "quantities", "version")` y el diccionario `{pk: (task_id, pct, quantities, version) for pk, task_id, pct, quantities, version in rows}`.

En `validated_rejection`: `task_id, pct, quantities, version = current` y el bucle:

```python
        for field, stored in (
            ("task_id", task_id),
            ("progress_pct", pct),
            ("quantities", quantities),
        ):
```

con el mensaje "The visit was validated: its task, progress and quantities cannot change." Actualizar los docstrings ("cambia de tarea, % o cantidades").

Imports en `services.py`: `BudgetItem`, `LocationNode`, `QuantityPlan` desde `visitacampo.core.models`; `Any` si no estaba.

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/sync`
Expected: PASS. `test_query_budget.py` no cambia: un lote sin cantidades no hace las queries nuevas.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/sync
git commit -m "feat(sync): validate visit quantities against task, location and plan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: API de tareas de campo — ubicación e ítems planeados

**Files:**
- Modify: `server/visitacampo/matrix/locations.py` (`load_trees`)
- Modify: `server/visitacampo/projects/field_tasks.py`, `server/visitacampo/projects/schemas.py`
- Modify: `server/openapi/v1.yaml` (`FieldTask`, schemas nuevos `FieldLocation`, `FieldPlannedItem`)
- Test: `server/visitacampo/matrix/tests/test_field_tasks.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_field_tasks.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.field_tasks import field_tasks, list_version

from .factories import budget_world, item, locate, plan, quantity_row, quantity_visit, suno_levels

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class FieldTasksMatrixTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 2")
        locate(self.world, 5, "Tramo 2", "PR 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "10")
        self.world.responsible(3)
        self.world.responsible(2)

    def _items(self) -> dict[int, dict[str, object]]:
        """Las tareas del agente por uid."""
        ids = {str(self.world.task(uid).pk): uid for uid in (2, 3)}
        return {ids[i["id"]]: i for i in field_tasks(self.world.ctx(self.world.agent))}

    def test_planned_task(self) -> None:
        self.world.approve(quantity_visit(self.world, 3, quantity_row(self.concrete, "4")))
        task = self._items()[3]
        self.assertEqual(task["measured_by"], "quantities")
        self.assertEqual(task["location"]["label"], "Tramo 2")
        self.assertEqual(
            [o["label"] for o in task["location_options"]], ["Tramo 2", "Tramo 2 / PR 1"]
        )
        self.assertEqual(
            task["planned_items"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "code": "2.1",
                    "description": "Concreto zapatas",
                    "unit": "m3",
                    "planned_quantity": 10.0,
                    "executed_quantity": 4.0,
                }
            ],
        )
        self.assertNotIn("unit_price", str(task))

    def test_task_without_plan(self) -> None:
        task = self._items()[2]
        self.assertEqual(task["measured_by"], "pct")
        self.assertIsNone(task["location"])
        self.assertEqual(task["location_options"], [])
        self.assertEqual(task["planned_items"], [])

    def test_version_changes_with_the_plan(self) -> None:
        before = list_version(field_tasks(self.world.ctx(self.world.agent)))
        plan(self.world, 3, item(self.budget, "2.2"), "5")
        after = list_version(field_tasks(self.world.ctx(self.world.agent)))
        self.assertNotEqual(before, after)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_field_tasks.py`
Expected: FAIL con `KeyError: 'measured_by'`.

- [ ] **Step 3: Árboles de varios proyectos en una query**

En `matrix/locations.py`, después de `load_tree`:

```python
def load_trees(ctx: RequestContext, project_ids: Iterable[UUID]) -> dict[UUID, Tree]:
    """Los árboles de varios proyectos en una query (para la API de campo)."""
    ids = set(project_ids)
    trees: dict[UUID, Tree] = {}
    if not ids:
        return trees
    for node in (
        scoped_qs(ctx, LocationNode)
        .filter(project_id__in=ids)
        .select_related("level", "project")
        .order_by(Lower("name"))
    ):
        trees.setdefault(node.project_id, Tree(project=node.project))._add(node)
    return trees
```

- [ ] **Step 4: `field_tasks.py`**

Imports:

```python
from visitacampo.core.models import QuantityPlan, Task
from visitacampo.matrix.locations import load_trees
from visitacampo.matrix.quantities import (
    executed_by_pair,
    item_facts,
    measured_progress,
    suno_pct_by_task,
)
```

Después de obtener `tasks`, reemplazar el cálculo de `pct` y el `return` por:

```python
    ids = [t.pk for t in tasks]
    measured = measured_progress(ctx, task_ids=ids)
    pct = suno_pct_by_task(ctx, task_ids=ids, measured=measured)
    plans = list(
        scoped_qs(ctx, QuantityPlan)
        .filter(task_id__in=ids)
        .values_list("task_id", "item_id", "planned_quantity")
    )
    executed = executed_by_pair(ctx, task_ids=ids)
    facts = item_facts(ctx, {item_id for _, item_id, _ in plans})
    planned_items: dict[Any, list[dict[str, Any]]] = {}
    for task_id, item_id, planned in plans:
        info = facts.get(item_id)
        if info is None:
            continue
        planned_items.setdefault(task_id, []).append(
            {
                "item_id": str(item_id),
                "code": info.code,
                "description": info.description,
                "unit": info.unit,
                "planned_quantity": float(planned),
                "executed_quantity": float(executed.get((task_id, item_id), 0)),
            }
        )
    trees = load_trees(ctx, {t.project_id for t in tasks if t.location_id is not None})

    def location(t: Task) -> dict[str, str] | None:
        tree = trees.get(t.project_id)
        if t.location_id is None or tree is None or t.location_id not in tree.nodes:
            return None
        return {"id": str(t.location_id), "label": tree.label(t.location_id)}

    def options(t: Task) -> list[dict[str, str]]:
        tree = trees.get(t.project_id)
        if t.location_id is None or tree is None or t.location_id not in tree.nodes:
            return []
        return [
            {"id": str(node_id), "label": tree.label(node_id)}
            for node_id in tree.descendants(t.location_id)
        ]

    return [
        {
            "id": str(t.pk),
            "project_id": str(t.project_id),
            "project_name": t.project.name,
            "wbs": t.wbs,
            "outline_number": t.outline_number,
            "name": t.name,
            "last_validated_pct": float(pct[t.pk]) if t.pk in pct else None,
            "measured_by": "quantities" if t.pk in measured else "pct",
            "location": location(t),
            "location_options": options(t),
            "planned_items": sorted(planned_items.get(t.pk, []), key=lambda i: i["code"]),
        }
        for t in tasks
    ]
```

Actualizar el docstring de `field_tasks` ("Contrato `FieldTask` … Nueve queries como mucho"). Si hay una prueba con `assertNumQueries` en `projects/tests/test_api.py`, actualizar el número.

- [ ] **Step 5: Esquemas de Ninja y contrato**

`projects/schemas.py`:

```python
class FieldLocation(Schema):
    id: uuid.UUID
    label: str


class FieldPlannedItem(Schema):
    item_id: uuid.UUID
    code: str
    description: str
    unit: str
    planned_quantity: float
    executed_quantity: float
```

y `FieldTask` gana:

```python
    measured_by: Literal["pct", "quantities"]
    location: FieldLocation | None
    location_options: list[FieldLocation]
    planned_items: list[FieldPlannedItem]
```

(`from typing import Literal`; sumar los dos nombres a `__all__`).

En `v1.yaml`, `FieldTask.required` gana `measured_by, location, location_options, planned_items`, y sus `properties`:

```yaml
        measured_by:
          type: string
          enum: [pct, quantities]
          description: |
            `quantities` si la tarea tiene ítems planeados (costos, pieza 4): la app registra
            cantidades y no pide %; `pct` en el resto.
        location:
          oneOf:
            - $ref: '#/components/schemas/FieldLocation'
            - type: 'null'
        location_options:
          type: array
          description: La ubicación de la tarea y sus descendientes vigentes, para precisar en campo.
          items:
            $ref: '#/components/schemas/FieldLocation'
        planned_items:
          type: array
          description: Ítems planeados de la tarea, sin precios ni montos.
          items:
            $ref: '#/components/schemas/FieldPlannedItem'
```

y la descripción de `last_validated_pct` pasa a "% SUNO de la tarea: por cantidades validadas si tiene ítems planeados; si no, el de la visita validada más reciente. `null` si no hay." Schemas nuevos, después de `FieldTaskList`:

```yaml
    FieldLocation:
      type: object
      required: [id, label]
      properties:
        id:
          $ref: '#/components/schemas/Uuid'
        label:
          type: string
          description: Ruta completa, p. ej. "Tramo 2 / PR 12+300".

    FieldPlannedItem:
      type: object
      required: [item_id, code, description, unit, planned_quantity, executed_quantity]
      properties:
        item_id:
          $ref: '#/components/schemas/Uuid'
        code:
          type: string
          maxLength: 50
        description:
          type: string
        unit:
          type: string
          maxLength: 30
        planned_quantity:
          type: number
        executed_quantity:
          type: number
          description: Suma de las cantidades de las visitas validadas (puede pasar lo planeado).
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix visitacampo/projects visitacampo/core/tests`
Expected: PASS, incluido el test de deriva del contrato.

- [ ] **Step 7: Commit**

```bash
git add server/openapi/v1.yaml server/visitacampo/projects server/visitacampo/matrix
git commit -m "feat(projects): field tasks expose location and planned items

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Admin de visitas — cantidades y "% manual ignorado"

**Files:**
- Modify: `server/visitacampo/core/admin.py` (`VisitAdmin`)
- Test: `server/visitacampo/matrix/tests/test_admin.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/matrix/tests/test_admin.py`:

```python
from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.core.admin import VisitAdmin
from visitacampo.core.models import Visit
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import budget_world, item, plan, quantity_row, quantity_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class VisitAdminQuantitiesTests(TestCase):
    def test_quantities_are_listed_with_flags(self) -> None:
        world, budget = budget_world()
        concrete, steel = item(budget, "2.1"), item(budget, "2.2")
        plan(world, 3, concrete, "10")
        world.approve(quantity_visit(world, 3, quantity_row(concrete, "8")))
        visit = quantity_visit(
            world,
            3,
            quantity_row(concrete, "5"),
            quantity_row(steel, "2", note="no planeado"),
            pct="30",
            hours=1,
        )
        admin = VisitAdmin(Visit, None)  # type: ignore[arg-type]
        text = str(admin.quantities_pretty(visit))
        self.assertIn("2.1 Concreto zapatas: 5 m3", text)
        self.assertIn("supera el plan (13 de 10)", text)
        self.assertIn("2.2 Acero de refuerzo: 2 kg", text)
        self.assertIn("fuera del plan", text)
        self.assertIn("no planeado", text)
        self.assertIn("% manual ignorado", text)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_admin.py`
Expected: FAIL con `AttributeError: 'VisitAdmin' object has no attribute 'quantities_pretty'`.

- [ ] **Step 3: El campo en el Admin**

En `core/admin.py`, `VisitAdmin.fields` gana `"quantities_pretty"` después de `"progress"`. Método nuevo:

```python
    @admin.display(description="cantidades")
    def quantities_pretty(self, visit: Visit) -> Any:
        """Las cantidades de la visita, con avisos para la supervisión: ítem fuera del
        plan de la tarea, o lo validado más esta visita supera lo planeado."""
        if not visit.quantities or visit.task_id is None:
            return "—"
        item_ids = [row["item_id"] for row in visit.quantities]
        # El Admin no tiene `RequestContext`: se acota a la organización de la visita.
        versions = (
            QuerySet(model=BudgetItemVersion)
            .filter(
                org_id=visit.org_id,
                item_id__in=item_ids,
                version__status=BudgetVersionStatus.APPLIED,
            )
            .order_by("item_id", "-version__number")
            .distinct("item_id")
            .values_list("item_id", "item__code", "description", "unit")
        )
        facts = {str(i): (code, desc, unit) for i, code, desc, unit in versions}
        planned = {
            str(item_id): quantity
            for item_id, quantity in QuerySet(model=QuantityPlan)
            .filter(org_id=visit.org_id, task_id=visit.task_id)
            .values_list("item_id", "planned_quantity")
        }
        validated: dict[str, Decimal] = {}
        others = approved(
            QuerySet(model=Visit).filter(
                org_id=visit.org_id, task_id=visit.task_id, deleted_at__isnull=True
            ).exclude(pk=visit.pk)
        ).values_list("quantities", flat=True)
        for rows in others:
            for row in rows:
                validated[row["item_id"]] = validated.get(row["item_id"], Decimal(0)) + Decimal(
                    row["quantity"]
                )
        lines: list[str] = []
        for row in visit.quantities:
            code, description, unit = facts.get(row["item_id"], ("?", "", ""))
            quantity = Decimal(row["quantity"])
            text = f"{code} {description}: {format_quantity(quantity)} {unit}".rstrip()
            target = planned.get(row["item_id"])
            if target is None:
                text += " — fuera del plan"
            else:
                total = validated.get(row["item_id"], Decimal(0)) + quantity
                if total > target:
                    text += (
                        f" — supera el plan ({format_quantity(total)} de "
                        f"{format_quantity(target)})"
                    )
            if row["note"]:
                text += f" — nota: {row['note']}"
            lines.append(text)
        if visit.progress_pct is not None and any(q > 0 for q in planned.values()):
            lines.append("% manual ignorado: la tarea se mide por cantidades.")
        # Cada línea se escapa: códigos, descripciones y notas los escribe un usuario.
        return format_html_join("", "<p>{}</p>", ((line,) for line in lines))
```

Función auxiliar a nivel de módulo:

```python
def format_quantity(value: Decimal) -> str:
    """`Decimal("5.0000")` → "5"; `Decimal("2.5000")` → "2,5"."""
    return f"{value.normalize():f}".replace(".", ",")
```

Imports: `from decimal import Decimal`; `from django.utils.html import format_html_join`; `from django.db.models import QuerySet` (si no está); `BudgetItemVersion`, `BudgetVersionStatus`, `QuantityPlan` desde `visitacampo.core.models`; `approved` desde `visitacampo.projects.field_progress` (ya se importan otras funciones de ese módulo). `QuerySet(model=…)` filtrado por `org_id` es el mismo patrón que usa `field_progress.with_review` para no pasar por `Model.objects`.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/matrix/tests/test_admin.py visitacampo/core/tests`
Expected: PASS. En la prueba: lo validado (8) más esta visita (5) = 13 > 10.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/core/admin.py server/visitacampo/matrix
git commit -m "feat(admin): show visit quantities with plan warnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Verificación completa, revisión visual, registro y PR

**Files:**
- Modify: `plans/visitacampo-progreso.md`

- [ ] **Step 1: Suite completa y compuertas**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pip install -q bandit && bandit -q -r visitacampo config -x '*/tests/*,*/migrations/*'"
infra/scripts/test_docker.sh --cmd "coverage run -m pytest -q -p no:cacheprovider && coverage report | tail -1"
```

Expected: todo verde salvo las 4 fallas conocidas de `sync/tests/test_load_script.py`; cobertura ≥ la de `master` (93,7 %). Correr cada comando con `set -o pipefail` si se encadena con `|`.

- [ ] **Step 2: Revisión visual**

Renderizar con datos largos (nombres de proyecto, tareas, ítems y ubicaciones de 100+ caracteres; 6 niveles; montos de 12 dígitos) las páginas: matriz por ítem, por ubicación, niveles, importar, vista previa (con errores y con cambios), historial y la ficha de tarea con el bloque de cantidades. Capturas a 1440 px y 390 px (Edge headless; a 390 px dentro de un iframe, porque el mínimo de Edge es 496 px). Corregir textos cortados, superpuestos o desbordados antes del PR.

- [ ] **Step 3: Registro de avance**

En `plans/visitacampo-progreso.md`, sección nueva "Costos, pieza 4a — matriz de homologación (servidor)": qué se entregó, decisiones del plan, números de la suite (pruebas, cobertura), pendientes conocidos (app 4b; valor ganado en pieza 5; etiquetas transversales) y "PR pendiente".

- [ ] **Step 4: Commit, push y PR**

```bash
git add plans/visitacampo-progreso.md
git commit -m "docs(plan): progress log for cost module piece 4a

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/costos-matriz
gh pr create --title "feat(matrix): matriz de homologación y ubicación (servidor)" --body "<resumen, decisiones, pruebas>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Esperar el CI (bandit, semgrep, CodeQL, pruebas). Merge y release solo con aprobación del usuario.

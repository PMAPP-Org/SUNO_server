# Costos — Etapa 1 (Cronograma), parte servidor: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que una organización cree proyectos, importe su cronograma de MS Project (MSPDI XML) por versiones, y vea en `/plataforma/proyectos/` la EDT con etapas, % Project, atrasos en días hábiles y %, holguras, ruta crítica, Gantt, responsables y exportación a Excel.

**Architecture:** Modelos nuevos en `visitacampo/core/models/project.py` (todas las tablas de negocio viven en `core`, ADR-011). Lógica en el paquete nuevo `visitacampo/projects/`: un lector MSPDI puro (`mspdi.py`), un calendario laboral puro (`workdays.py`), los cálculos de atraso puros (`progress.py`), la importación con base de datos (`importer.py`), el armado del cronograma (`schedule.py`), el acceso por rol (`access.py`) y las vistas web (`views.py`, `urls.py`). Plantillas en `core/templates/suno/projects/`. Todo acceso a modelos pasa por `scoped_qs` (lo exige `core/tests/test_architecture.py`).

**Tech Stack:** Django 5.2, PostgreSQL 16, `defusedxml` (ya en `requirements.txt`), plantillas Django, JavaScript sin dependencias para el Gantt (SVG propio).

**Spec:** `plans/2026-10-30-costos-etapa1-cronograma-design.md`.

**Alcance de este plan:** todo el servidor de la etapa 1 **excepto** el avance desde campo (campos `Visit.task`/`progress_pct`, API `/v1/projects/tasks/`, sincronización y app Flutter). Eso va en un segundo plan (`2026-10-31-costos-etapa1-campo-plan.md`). Mientras tanto la columna **% SUNO** existe y vale 0 en todas las tareas (regla de la spec: sin dato = 0). `schedule.build_schedule` recibe el mapa de % SUNO como parámetro para que el segundo plan solo tenga que alimentarlo.

**Desviaciones de la spec, decididas al planear (Task 0 las anota en la spec):**

1. **Importación síncrona en dos pasos, sin la cola `jobs`.** Leer 5.000 tareas toma ~1 s; la cola obligaría a una pantalla de espera con sondeo. La subida crea un `ScheduleImport` en estado `pending` con el XML (gzip) y el resumen de cambios; la vista previa lo muestra; confirmar lo aplica en una transacción. El límite de 50 MB se mantiene.
2. **Gantt propio en SVG** (`suno/js/gantt.js`) en lugar de Frappe Gantt por CDN: el proyecto no carga nada de terceros en el navegador (el mapa de `/plataforma/` también es propio) y así se controla la línea base, la ruta crítica y la línea de corte.

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test` (no `pytest-django`). Los que tocan BD usan `visitacampo/core/tests/dbsupport.py`:
  ```python
  setUpModule = setup_test_database
  tearDownModule = teardown_test_database
  skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
  ```
- Fábricas: `visitacampo.authn.tests.factories.make_org(name)` y `make_user(org, username=..., role=Role.X)`; contraseña `PASSWORD`.
- Ningún módulo de producción usa `Model.objects`: siempre `scoped_qs(ctx, Model)`. Al crear, pasar `org_id=ctx.org_id`.
- Roles: `Role.OWNER/ADMIN/SUPERVISOR/FIELD_AGENT/VIEWER`; conjuntos en `core/policies.py` (`ADMIN_ROLES`, `SUPERVISION_ROLES`).
- Auditoría: `visitacampo.core.audit.record(ctx, action=..., target=..., metadata=...)` con códigos en `AuditAction`.
- Excel: `visitacampo.backoffice.export.to_xlsx(header, rows)` y `safe_cell(value)`.
- Comentarios y textos de interfaz en español; nombres de código en inglés.

## Cómo correr las pruebas

No hay Python 3.12 en la máquina de desarrollo: todo corre en Docker con `infra/scripts/test_docker.sh` (Task 0). Docker Desktop debe estar arrancado.

```bash
infra/scripts/test_docker.sh visitacampo/projects          # pytest de un paquete
infra/scripts/test_docker.sh                              # suite completa
infra/scripts/test_docker.sh --cmd "ruff check ."         # lint
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                       # al terminar: borra Postgres y red
```

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `infra/scripts/test_docker.sh` | Ejecutor de pruebas en Docker (nuevo) |
| `server/visitacampo/core/models/project.py` | `Project`, `ScheduleImport`, `Task`, `TaskDependency`, `Resource`, `TaskResource`, `TaskResponsible` |
| `server/visitacampo/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/visitacampo/core/migrations/0011_add_projects.py` | Generada |
| `server/visitacampo/core/policies.py` | Roles y políticas de proyectos |
| `server/visitacampo/core/audit.py` | Códigos de auditoría de proyectos |
| `server/visitacampo/projects/workdays.py` | Calendario laboral (puro) |
| `server/visitacampo/projects/mspdi.py` | Lector MSPDI (puro) |
| `server/visitacampo/projects/progress.py` | % programado, atrasos, consolidación (puro) |
| `server/visitacampo/projects/access.py` | Proyectos visibles por rol, responsables efectivos |
| `server/visitacampo/projects/importer.py` | Resumen de cambios, preparar/aplicar/descartar importación |
| `server/visitacampo/projects/schedule.py` | Filas del cronograma con indicadores y filtros |
| `server/visitacampo/projects/views.py`, `urls.py` | Páginas `/plataforma/proyectos/` |
| `server/visitacampo/projects/tests/…` | Pruebas |
| `server/config/urls.py` | Monta `plataforma/proyectos/` |
| `server/visitacampo/core/templates/suno/_sidebar.html` | Barra lateral compartida (extraída de `platform.html`) |
| `server/visitacampo/core/templates/suno/projects/*.html` | Plantillas del módulo |
| `server/visitacampo/core/static/suno/css/projects.css` | Estilos del módulo |
| `server/visitacampo/core/static/suno/js/gantt.js` | Gantt SVG |

---

### Task 0: Ejecutor de pruebas en Docker y notas en la spec

**Files:**
- Create: `infra/scripts/test_docker.sh`
- Modify: `plans/2026-10-30-costos-etapa1-cronograma-design.md`

- [ ] **Step 1: Crear el script**

```bash
#!/usr/bin/env bash
# Corre pytest (u otro comando) del servidor en Docker, sin Python local:
# un PostgreSQL desechable y python:3.12-slim con requirements-dev.lock
# instalado en un volumen reutilizable (se reinstala si el lock cambia).
#
#   infra/scripts/test_docker.sh [args de pytest]       por defecto: toda la suite
#   infra/scripts/test_docker.sh --cmd "ruff check ."    otro comando dentro de server/
#   infra/scripts/test_docker.sh --down                  borra el Postgres y la red
set -euo pipefail
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER_DIR="$(cd "$ROOT/server" && { pwd -W 2>/dev/null || pwd; })"
NET=suno-test
DB=suno-test-db
VENV=suno-test-venv

if [ "${1:-}" = "--down" ]; then
	docker rm -f "$DB" >/dev/null 2>&1 || true
	docker network rm "$NET" >/dev/null 2>&1 || true
	exit 0
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
if [ -z "$(docker ps -q -f "name=^${DB}$")" ]; then
	docker rm -f "$DB" >/dev/null 2>&1 || true
	docker run -d --name "$DB" --network "$NET" \
		-e POSTGRES_DB=visitacampo -e POSTGRES_USER=visitacampo -e POSTGRES_PASSWORD=test \
		postgres:16-alpine >/dev/null
	# El entrypoint reinicia Postgres una vez tras inicializar: esperar a que
	# responda dos veces seguidas evita conectar justo antes del reinicio.
	until docker exec "$DB" pg_isready -U visitacampo -q && sleep 2 &&
		docker exec "$DB" pg_isready -U visitacampo -q; do sleep 1; done
fi

if [ "${1:-}" = "--cmd" ]; then
	CMD="$2"
else
	CMD="pytest -q -p no:cacheprovider $*"
fi

docker run --rm --network "$NET" -v "$SERVER_DIR:/app" -v "$VENV:/venv" -w /app \
	-e POSTGRES_HOST="$DB" -e POSTGRES_PASSWORD=test -e VISITACAMPO_REQUIRE_DB=1 \
	-e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
	python:3.12-slim sh -c '
		if ! cmp -s requirements-dev.lock /venv/.lock; then
			python -m venv /venv &&
			pip install -q --require-hashes -r requirements-dev.lock &&
			cp requirements-dev.lock /venv/.lock
		fi
		'"$CMD"
```

- [ ] **Step 2: Probarlo**

Run: `chmod +x infra/scripts/test_docker.sh && infra/scripts/test_docker.sh visitacampo/core/tests/test_platform_access.py`
Expected: `4 passed`.

- [ ] **Step 3: Anotar las desviaciones en la spec**

En `plans/2026-10-30-costos-etapa1-cronograma-design.md`, sección "## 2. Importación", reemplazar el punto 2 de la lista numerada:

```markdown
2. Un trabajo de la cola existente (`jobs`) lo lee y calcula la vista previa: nuevas,
   retiradas, reactivadas, modificadas, cambio de línea base y advertencias (tareas
   retiradas con responsables o con avance de campo).
```

por:

```markdown
2. El servidor lo lee en la misma petición (≈1 s para 5.000 tareas), guarda un
   `ScheduleImport` en estado `pending` con el XML y calcula la vista previa: nuevas,
   retiradas, reactivadas, modificadas, cambio de línea base y advertencias (tareas
   retiradas con responsables o con avance de campo). *Decidido al planear: sin la cola
   `jobs`, que obligaría a una pantalla de espera con sondeo.*
```

y en "## 4. Pantallas", reemplazar `línea de fecha de corte. Frappe Gantt desde CDN con versión fija.` por `línea de fecha de corte. SVG propio (\`suno/js/gantt.js\`), sin librerías de terceros en el navegador.`

En "## 1. Modelo de datos", en la viñeta de **ScheduleImport**, reemplazar `` `source_file` (XML original) `` por `` `status` (pending / applied / discarded), `file_name`, `source_xml` (XML original comprimido con gzip) ``.

- [ ] **Step 4: Commit**

```bash
git add infra/scripts/test_docker.sh plans/2026-10-30-costos-etapa1-cronograma-design.md
git commit -m "chore(test): docker test runner; note planning decisions in stage 1 spec"
```

---

### Task 1: Modelos y migración

**Files:**
- Create: `server/visitacampo/core/models/project.py`
- Modify: `server/visitacampo/core/models/__init__.py`
- Create (generada): `server/visitacampo/core/migrations/0011_add_projects.py`
- Create: `server/visitacampo/projects/__init__.py`, `server/visitacampo/projects/tests/__init__.py`
- Test: `server/visitacampo/projects/tests/test_models.py`

- [ ] **Step 1: Crear los paquetes vacíos**

`server/visitacampo/projects/__init__.py`:

```python
"""Módulo de costos y presupuesto — etapa 1: cronograma (MS Project)."""
```

`server/visitacampo/projects/tests/__init__.py`: archivo vacío.

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_models.py`:

```python
"""Restricciones de BD del modelo de proyectos."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.models import Project, Role, ScheduleImport, Task, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ProjectModelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.user = make_user(self.org, username="admin", role=Role.ADMIN)
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.user)

    def _task(self, uid: int) -> Task:
        return Task.objects.create(
            org=self.org,
            project=self.project,
            uid=uid,
            outline_number=str(uid),
            outline_level=1,
            sort_order=uid,
            name=f"Tarea {uid}",
        )

    def test_task_uid_is_unique_per_project(self) -> None:
        self._task(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._task(1)

    def test_project_name_is_unique_per_org(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Project.objects.create(org=self.org, name="Obra", created_by=self.user)

    def test_import_number_is_unique_per_project_but_pending_has_none(self) -> None:
        def make(number: int | None) -> ScheduleImport:
            return ScheduleImport.objects.create(
                org=self.org,
                project=self.project,
                number=number,
                file_name="v.xml",
                source_xml=b"",
                uploaded_by=self.user,
            )

        make(None)
        make(None)
        make(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make(1)

    def test_responsible_is_unique_per_task_and_user(self) -> None:
        task = self._task(1)
        TaskResponsible.objects.create(org=self.org, task=task, user=self.user, assigned_by=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskResponsible.objects.create(
                org=self.org, task=task, user=self.user, assigned_by=self.user
            )
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_models.py`
Expected: FAIL con `ImportError: cannot import name 'Project'`.

- [ ] **Step 4: Escribir los modelos**

`server/visitacampo/core/models/project.py`:

```python
"""Cronograma de proyectos (módulo de costos, etapa 1).

Diseño: plans/2026-10-30-costos-etapa1-cronograma-design.md.

- `Task` es estable por el `UniqueID` de MS Project: cada importación
  actualiza sus campos en sitio y **nunca la borra**; si desaparece del XML
  se marca `retired_in`. Lo que es de SUNO —responsables y, en etapas
  siguientes, avance de campo, homologación y llaves contextualizadoras—
  cuelga de `Task` y sobrevive así a todas las versiones del Project.
- `ScheduleImport` es el registro de cada XML subido: el archivo original
  (gzip) y el resumen de cambios. No hay fotografía de tareas por versión.
- `TaskDependency`, `Resource` y `TaskResource` son datos del XML y se
  reemplazan en cada importación.
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class Project(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, verbose_name="nombre")
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    description = models.TextField(blank=True, verbose_name="descripción")
    #: Calendario laboral del Project, en el formato de
    #: `projects.workdays.WorkCalendar.to_json`. Se reemplaza en cada
    #: importación; vacío equivale a lunes a viernes sin festivos.
    calendar = models.JSONField(default=dict, blank=True, verbose_name="calendario")
    #: `StatusDate` de la última importación aplicada (fecha de corte).
    status_date = models.DateField(null=True, blank=True, verbose_name="fecha de corte")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project"
        ordering = ["name"]
        verbose_name = "proyecto"
        verbose_name_plural = "proyectos"
        constraints = [
            models.UniqueConstraint(fields=["org", "name"], name="project_org_name_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class ScheduleImportStatus(models.TextChoices):
    PENDING = "pending", "Pendiente de confirmar"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class ScheduleImport(OrgScopedModel):
    """Un XML de MS Project subido a un proyecto.

    Nace `pending` con el XML y el resumen de cambios (la vista previa); al
    confirmarse pasa a `applied` y recibe su `number` consecutivo. Las
    descartadas se conservan como rastro.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="imports", verbose_name="proyecto"
    )
    #: Consecutivo por proyecto; solo lo tienen las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=16,
        choices=ScheduleImportStatus.choices,
        default=ScheduleImportStatus.PENDING,
        verbose_name="estado",
    )
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    source_xml = models.BinaryField(verbose_name="XML original (gzip)")
    uploaded_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="subido por"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    status_date = models.DateField(null=True, blank=True, verbose_name="fecha de corte")
    #: Resumen de cambios: ver `projects.importer.summarize_changes`.
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "schedule_import"
        ordering = ["-uploaded_at"]
        verbose_name = "importación de cronograma"
        verbose_name_plural = "importaciones de cronograma"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"],
                condition=models.Q(number__isnull=False),
                name="schedule_import_project_number_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project} v{self.number or '?'}"


class Task(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name="tasks", verbose_name="proyecto"
    )
    #: `UniqueID` de MS Project: la identidad entre versiones.
    uid = models.IntegerField(verbose_name="UniqueID")
    wbs = models.CharField(max_length=100, blank=True, verbose_name="EDT")
    outline_number = models.CharField(max_length=100, verbose_name="número de esquema")
    outline_level = models.PositiveSmallIntegerField(verbose_name="nivel")
    #: Orden de la fila en el Project (`ID`).
    sort_order = models.IntegerField(verbose_name="orden")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    #: Tarea de nivel 1 que contiene a esta (ella misma si es de nivel 1).
    stage = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=500, verbose_name="nombre")
    is_summary = models.BooleanField(default=False, verbose_name="resumen")
    is_milestone = models.BooleanField(default=False, verbose_name="hito")
    start = models.DateField(null=True, blank=True, verbose_name="inicio")
    finish = models.DateField(null=True, blank=True, verbose_name="fin")
    actual_finish = models.DateField(null=True, blank=True, verbose_name="fin real")
    duration_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    baseline_start = models.DateField(null=True, blank=True, verbose_name="inicio línea base")
    baseline_finish = models.DateField(null=True, blank=True, verbose_name="fin línea base")
    baseline_duration_days = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    project_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="% Project"
    )
    project_physical_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="% físico Project"
    )
    total_slack_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    free_slack_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_critical = models.BooleanField(default=False, verbose_name="crítica")
    #: Importación en la que dejó de venir en el XML; nula = vigente.
    retired_in = models.ForeignKey(
        ScheduleImport, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "task"
        ordering = ["sort_order"]
        verbose_name = "tarea"
        verbose_name_plural = "tareas"
        constraints = [
            models.UniqueConstraint(fields=["project", "uid"], name="task_project_uid_uq"),
        ]
        indexes = [
            models.Index(fields=["project", "sort_order"], name="task_project_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.wbs or self.outline_number} {self.name}"


class DependencyType(models.TextChoices):
    FS = "FS", "Fin a comienzo"
    SS = "SS", "Comienzo a comienzo"
    FF = "FF", "Fin a fin"
    SF = "SF", "Comienzo a fin"


class TaskDependency(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="+")
    predecessor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="successor_links")
    successor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="predecessor_links")
    type = models.CharField(max_length=2, choices=DependencyType.choices, default=DependencyType.FS)
    lag_days = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "task_dependency"
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor"], name="task_dependency_pair_uq"
            ),
        ]


class Resource(OrgScopedModel):
    """Recurso del Project (ejecutor). Es un dato, no un usuario de SUNO."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="resources")
    uid = models.IntegerField()
    name = models.CharField(max_length=255)

    class Meta:
        db_table = "resource"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["project", "uid"], name="resource_project_uid_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class TaskResource(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="resource_links")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="task_links")

    class Meta:
        db_table = "task_resource"
        constraints = [
            models.UniqueConstraint(fields=["task", "resource"], name="task_resource_pair_uq"),
        ]


class TaskResponsible(OrgScopedModel):
    """Usuario SUNO responsable de una tarea; en una resumen cubre toda la rama."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="responsibles")
    user = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="task_responsibilities"
    )
    assigned_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_responsible"
        constraints = [
            models.UniqueConstraint(fields=["task", "user"], name="task_responsible_pair_uq"),
        ]
```

- [ ] **Step 5: Reexportar en `core/models/__init__.py`**

En el docstring, añadir tras la línea de `ops`:

```
- `project`  — `Project`, `ScheduleImport`, `Task` y sus tablas (costos, etapa 1).
```

Tras `from .privacy import PrivacyConsent, PrivacyPurpose` añadir:

```python
from .project import (
    DependencyType,
    Project,
    Resource,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
```

y en `__all__` añadir, en orden alfabético dentro de la lista: `"DependencyType"`, `"Project"`, `"Resource"`, `"ScheduleImport"`, `"ScheduleImportStatus"`, `"Task"`, `"TaskDependency"`, `"TaskResource"`, `"TaskResponsible"`.

- [ ] **Step 6: Generar la migración**

Run: `infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core -n add_projects"`
Expected: `Migrations for 'core': visitacampo/core/migrations/0011_add_projects.py` con las 7 tablas.

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_models.py`
Expected: `4 passed`.

- [ ] **Step 8: Commit**

```bash
git add server/visitacampo/core/models server/visitacampo/core/migrations/0011_add_projects.py server/visitacampo/projects
git commit -m "feat(projects): schedule data model (project, import, task, responsibles)"
```

---

### Task 2: Calendario laboral

**Files:**
- Create: `server/visitacampo/projects/workdays.py`
- Test: `server/visitacampo/projects/tests/test_workdays.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_workdays.py`:

```python
"""Días hábiles según el calendario del Project."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from visitacampo.projects.workdays import CalendarException, WorkCalendar

D = date
# Marzo de 2026: el lunes 2 es el primer día hábil; el lunes 23 es festivo.
HOLIDAY = CalendarException(start=D(2026, 3, 23), end=D(2026, 3, 23), working=False)


class WorkCalendarTests(SimpleTestCase):
    def test_monday_to_friday_by_default(self) -> None:
        cal = WorkCalendar()
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 6)), 5)
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 13)), 10)
        self.assertEqual(cal.count_inclusive(D(2026, 3, 7), D(2026, 3, 8)), 0)

    def test_end_before_start_counts_zero(self) -> None:
        self.assertEqual(WorkCalendar().count_inclusive(D(2026, 3, 6), D(2026, 3, 2)), 0)

    def test_holiday_is_not_counted(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        self.assertFalse(cal.is_working(D(2026, 3, 23)))
        self.assertEqual(cal.count_inclusive(D(2026, 3, 16), D(2026, 3, 26)), 8)

    def test_working_saturday_exception_is_counted(self) -> None:
        saturday = CalendarException(start=D(2026, 3, 28), end=D(2026, 3, 28), working=True)
        cal = WorkCalendar(exceptions=[HOLIDAY, saturday])
        self.assertEqual(cal.count_inclusive(D(2026, 3, 23), D(2026, 3, 29)), 5)

    def test_later_exception_wins_on_overlap(self) -> None:
        week_off = CalendarException(start=D(2026, 3, 2), end=D(2026, 3, 6), working=False)
        back = CalendarException(start=D(2026, 3, 4), end=D(2026, 3, 4), working=True)
        cal = WorkCalendar(exceptions=[week_off, back])
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 6)), 1)

    def test_custom_week(self) -> None:
        cal = WorkCalendar(working_weekdays=range(6))  # lunes a sábado
        self.assertEqual(cal.count_inclusive(D(2026, 3, 2), D(2026, 3, 8)), 6)

    def test_signed_gap(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        self.assertEqual(cal.signed_gap(D(2026, 3, 13), D(2026, 3, 17)), 2)
        self.assertEqual(cal.signed_gap(D(2026, 3, 17), D(2026, 3, 13)), -2)
        self.assertEqual(cal.signed_gap(D(2026, 3, 13), D(2026, 3, 13)), 0)
        self.assertEqual(cal.signed_gap(D(2026, 3, 20), D(2026, 3, 24)), 1)

    def test_long_range_matches_day_by_day_count(self) -> None:
        cal = WorkCalendar(exceptions=[HOLIDAY])
        start, end = D(2025, 1, 1), D(2027, 12, 31)
        expected = sum(
            1
            for n in range((end - start).days + 1)
            if cal.is_working(date.fromordinal(start.toordinal() + n))
        )
        self.assertEqual(cal.count_inclusive(start, end), expected)

    def test_json_round_trip(self) -> None:
        cal = WorkCalendar(working_weekdays=[0, 1, 2, 3, 4, 5], exceptions=[HOLIDAY])
        again = WorkCalendar.from_json(cal.to_json())
        self.assertEqual(again.working_weekdays, cal.working_weekdays)
        self.assertEqual(again.exceptions, cal.exceptions)

    def test_empty_json_is_monday_to_friday(self) -> None:
        cal = WorkCalendar.from_json({})
        self.assertEqual(cal.working_weekdays, frozenset(range(5)))

    def test_rejects_absurdly_long_exception(self) -> None:
        huge = CalendarException(start=D(2000, 1, 1), end=D(2100, 1, 1), working=False)
        with self.assertRaises(ValueError):
            WorkCalendar(exceptions=[huge])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_workdays.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.workdays'`.

- [ ] **Step 3: Implementar**

`server/visitacampo/projects/workdays.py`:

```python
"""Calendario laboral del Project: qué días se trabajan y cuántos hay entre dos fechas.

Los atrasos se cuentan en días hábiles de este calendario (spec, sección 3).
Contar día a día sería O(días) por tarea; con miles de tareas y proyectos de
varios años eso se nota en cada carga de página. Por eso el conteo es
aritmético: semanas completas × días laborales de la semana, más el resto, y
luego se corrige con las excepciones (festivos o días extra) que caen en el
rango, buscadas con `bisect` sobre una lista ordenada con sumas prefijas.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ONE_DAY = timedelta(days=1)
#: Una excepción más larga que esto es un error del archivo, no un calendario.
MAX_EXCEPTION_DAYS = 3660
DEFAULT_WEEKDAYS = frozenset(range(5))  # lunes (0) a viernes (4)


@dataclass(frozen=True)
class CalendarException:
    """Rango de días (inclusivo) que se trabajan o no, contra la semana normal."""

    start: date
    end: date
    working: bool


class WorkCalendar:
    def __init__(
        self,
        working_weekdays: Iterable[int] = DEFAULT_WEEKDAYS,
        exceptions: Iterable[CalendarException] = (),
    ) -> None:
        self.working_weekdays = frozenset(working_weekdays)
        self.exceptions = tuple(exceptions)
        overrides: dict[date, bool] = {}
        for exc in self.exceptions:
            if (exc.end - exc.start).days > MAX_EXCEPTION_DAYS:
                raise ValueError(
                    f"Excepción de calendario demasiado larga: {exc.start} a {exc.end}."
                )
            day = exc.start
            while day <= exc.end:
                overrides[day] = exc.working  # la posterior gana si se solapan
                day += ONE_DAY
        deltas = sorted(
            (day, int(working) - int(day.weekday() in self.working_weekdays))
            for day, working in overrides.items()
        )
        deltas = [(day, delta) for day, delta in deltas if delta != 0]
        self._delta_days = [day for day, _ in deltas]
        self._prefix = [0]
        for _, delta in deltas:
            self._prefix.append(self._prefix[-1] + delta)
        self._overrides = overrides

    def is_working(self, day: date) -> bool:
        if day in self._overrides:
            return self._overrides[day]
        return day.weekday() in self.working_weekdays

    def count_inclusive(self, start: date, end: date) -> int:
        """Días hábiles en [start, end]; 0 si end < start."""
        if end < start:
            return 0
        days = (end - start).days + 1
        full_weeks, rest = divmod(days, 7)
        count = full_weeks * len(self.working_weekdays)
        first = start.weekday()
        count += sum(1 for i in range(rest) if (first + i) % 7 in self.working_weekdays)
        lo = bisect_left(self._delta_days, start)
        hi = bisect_right(self._delta_days, end)
        return count + self._prefix[hi] - self._prefix[lo]

    def signed_gap(self, frm: date, to: date) -> int:
        """Días hábiles de `frm` a `to`: positivo si `to` es posterior.

        Cuenta los días en (frm, to]: de un viernes al lunes siguiente hay 1.
        """
        if to > frm:
            return self.count_inclusive(frm + ONE_DAY, to)
        if to < frm:
            return -self.count_inclusive(to + ONE_DAY, frm)
        return 0

    def to_json(self) -> dict[str, Any]:
        return {
            "working_weekdays": sorted(self.working_weekdays),
            "exceptions": [
                {"start": e.start.isoformat(), "end": e.end.isoformat(), "working": e.working}
                for e in self.exceptions
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> WorkCalendar:
        if not data:
            return cls()
        return cls(
            working_weekdays=data.get("working_weekdays", DEFAULT_WEEKDAYS),
            exceptions=[
                CalendarException(
                    start=date.fromisoformat(e["start"]),
                    end=date.fromisoformat(e["end"]),
                    working=bool(e["working"]),
                )
                for e in data.get("exceptions", [])
            ],
        )
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_workdays.py`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/workdays.py server/visitacampo/projects/tests/test_workdays.py
git commit -m "feat(projects): working-day calendar with holidays and exceptions"
```

---

### Task 3: Lector MSPDI

**Files:**
- Create: `server/visitacampo/projects/mspdi.py`
- Create: `server/visitacampo/projects/tests/samples.py`
- Test: `server/visitacampo/projects/tests/test_mspdi.py`

Formato MSPDI (lo que exporta Project con "Guardar como → XML"), solo lo que se lee:

- Raíz `<Project xmlns="http://schemas.microsoft.com/project">` con `StatusDate`, `MinutesPerDay` (por defecto 480), `CalendarUID`.
- `Calendars/Calendar`: `UID`, `BaseCalendarUID`, `WeekDays/WeekDay` (`DayType` 1 = domingo … 7 = sábado, `DayWorking` 0/1; `DayType` 0 = excepción con `TimePeriod/FromDate,ToDate`), y `Exceptions/Exception` (`TimePeriod/FromDate,ToDate`, `DayWorking`).
- `Tasks/Task`: `UID`, `ID`, `Name`, `WBS`, `OutlineNumber`, `OutlineLevel`, `Summary`, `Milestone`, `Start`, `Finish`, `ActualFinish`, `Duration` (`PT40H0M0S`), `PercentComplete`, `PhysicalPercentComplete`, `Critical`, `TotalSlack` y `FreeSlack` (décimas de minuto), `IsNull`, `PredecessorLink` (`PredecessorUID`, `Type` 0 = FF, 1 = FS, 2 = SF, 3 = SS, `LinkLag` en décimas de minuto) y `Baseline` (`Number` 0, `Start`, `Finish`, `Duration`). La tarea `UID` 0 / nivel 0 es el resumen del proyecto y se ignora.
- `Resources/Resource` (`UID`, `Name`) y `Assignments/Assignment` (`TaskUID`, `ResourceUID`; `-65535` = sin recurso).

- [ ] **Step 1: Crear el generador de XML de prueba**

`server/visitacampo/projects/tests/samples.py`:

```python
"""XML MSPDI de prueba, generados en código para que cada test diga qué cambia.

`v1()` y `v2()` son dos versiones del mismo cronograma (marzo de 2026, lunes
a viernes, festivo el lunes 23):

    v1                                      v2 (cambios)
    1   Preliminares (resumen, etapa)
    1.1 Localización   uid 2  100 %
    1.2 Descapote      uid 3   60 %         100 %, terminó el 18
    2   Estructura (resumen, etapa) uid 4
    2.1 Columnas       uid 5    0 %         retirada
                                            2.1 Vigas uid 7 (nueva)
    2.2 Fin de obra    uid 6  hito          línea base pasa del 26 al 27
"""

from __future__ import annotations

from xml.sax.saxutils import escape

NS = "http://schemas.microsoft.com/project"


def _el(tag: str, value: object) -> str:
    return f"<{tag}>{escape(str(value))}</{tag}>"


def task_xml(
    uid: int,
    name: str,
    outline: str,
    *,
    summary: bool = False,
    milestone: bool = False,
    start: str | None = None,
    finish: str | None = None,
    duration_h: int | None = None,
    baseline: tuple[str, str, int] | None = None,
    pct: int | None = None,
    physical_pct: int | None = None,
    actual_finish: str | None = None,
    critical: bool = False,
    total_slack: int = 0,
    free_slack: int = 0,
    preds: tuple[tuple[int, int], ...] = (),
    sort_id: int | None = None,
) -> str:
    level = 0 if outline == "0" else outline.count(".") + 1
    parts = [
        _el("UID", uid),
        _el("ID", uid if sort_id is None else sort_id),
        _el("Name", name),
        _el("WBS", outline),
        _el("OutlineNumber", outline),
        _el("OutlineLevel", level),
        _el("Summary", int(summary)),
        _el("Milestone", int(milestone)),
    ]
    if start:
        parts.append(_el("Start", f"{start}T08:00:00"))
    if finish:
        parts.append(_el("Finish", f"{finish}T17:00:00"))
    if duration_h is not None:
        parts.append(_el("Duration", f"PT{duration_h}H0M0S"))
    if actual_finish:
        parts.append(_el("ActualFinish", f"{actual_finish}T17:00:00"))
    if pct is not None:
        parts.append(_el("PercentComplete", pct))
    if physical_pct is not None:
        parts.append(_el("PhysicalPercentComplete", physical_pct))
    parts += [
        _el("Critical", int(critical)),
        _el("TotalSlack", total_slack),
        _el("FreeSlack", free_slack),
    ]
    for pred_uid, type_code in preds:
        parts.append(
            "<PredecessorLink>"
            f"{_el('PredecessorUID', pred_uid)}{_el('Type', type_code)}{_el('LinkLag', 0)}"
            "</PredecessorLink>"
        )
    if baseline:
        b_start, b_finish, b_hours = baseline
        parts.append(
            "<Baseline>"
            f"{_el('Number', 0)}{_el('Start', f'{b_start}T08:00:00')}"
            f"{_el('Finish', f'{b_finish}T17:00:00')}{_el('Duration', f'PT{b_hours}H0M0S')}"
            "</Baseline>"
        )
    return "<Task>" + "".join(parts) + "</Task>"


def _calendar_xml(holidays: tuple[str, ...]) -> str:
    weekdays = "".join(
        f"<WeekDay>{_el('DayType', d)}{_el('DayWorking', int(d not in (1, 7)))}</WeekDay>"
        for d in range(1, 8)
    )
    exceptions = "".join(
        "<Exception><TimePeriod>"
        f"{_el('FromDate', f'{h}T00:00:00')}{_el('ToDate', f'{h}T23:59:00')}"
        f"</TimePeriod>{_el('DayWorking', 0)}</Exception>"
        for h in holidays
    )
    return (
        "<Calendars><Calendar>"
        f"{_el('UID', 1)}{_el('Name', 'Estándar')}{_el('IsBaseCalendar', 1)}"
        f"<WeekDays>{weekdays}</WeekDays><Exceptions>{exceptions}</Exceptions>"
        "</Calendar></Calendars>"
    )


def project_xml(
    tasks: list[str],
    *,
    status_date: str | None = "2026-03-20",
    holidays: tuple[str, ...] = ("2026-03-23",),
    with_calendar: bool = True,
    resources: tuple[tuple[int, str], ...] = (),
    assignments: tuple[tuple[int, int], ...] = (),
    namespace: bool = True,
) -> bytes:
    head = f'<Project xmlns="{NS}">' if namespace else "<Project>"
    body = [_el("Name", "Obra demo"), _el("MinutesPerDay", 480), _el("CalendarUID", 1)]
    if status_date:
        body.append(_el("StatusDate", f"{status_date}T17:00:00"))
    if with_calendar:
        body.append(_calendar_xml(holidays))
    summary_task = task_xml(0, "Obra demo", "0", summary=True)
    body.append("<Tasks>" + summary_task + "".join(tasks) + "</Tasks>")
    body.append(
        "<Resources>"
        + "".join(f"<Resource>{_el('UID', u)}{_el('Name', n)}</Resource>" for u, n in resources)
        + "</Resources>"
    )
    body.append(
        "<Assignments>"
        + "".join(
            f"<Assignment>{_el('TaskUID', t)}{_el('ResourceUID', r)}</Assignment>"
            for t, r in assignments
        )
        + "</Assignments>"
    )
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + head + "".join(body)
    return (xml + "</Project>").encode("utf-8")


RESOURCES = ((1, "Cuadrilla A"), (2, "Topógrafo"))


def v1() -> bytes:
    return project_xml(
        [
            task_xml(1, "Preliminares", "1", summary=True, start="2026-03-02",
                     finish="2026-03-17", baseline=("2026-03-02", "2026-03-13", 80), pct=76),
            task_xml(2, "Localización", "1.1", start="2026-03-02", finish="2026-03-06",
                     duration_h=40, baseline=("2026-03-02", "2026-03-06", 40), pct=100,
                     physical_pct=100, actual_finish="2026-03-06", total_slack=4800),
            task_xml(3, "Descapote", "1.2", start="2026-03-09", finish="2026-03-17",
                     duration_h=56, baseline=("2026-03-09", "2026-03-13", 40), pct=60,
                     physical_pct=50, critical=True, preds=((2, 1),)),
            task_xml(4, "Estructura", "2", summary=True, start="2026-03-18",
                     finish="2026-03-30", baseline=("2026-03-16", "2026-03-26", 64), pct=0),
            task_xml(5, "Columnas", "2.1", start="2026-03-18", finish="2026-03-30",
                     duration_h=64, baseline=("2026-03-16", "2026-03-26", 64), pct=0,
                     critical=True, preds=((3, 1),)),
            task_xml(6, "Fin de obra", "2.2", milestone=True, start="2026-03-30",
                     finish="2026-03-30", duration_h=0,
                     baseline=("2026-03-26", "2026-03-26", 0), pct=0, critical=True,
                     preds=((5, 1),)),
        ],
        resources=RESOURCES,
        assignments=((2, 2), (3, 1), (5, 1), (6, -65535)),
    )


def v2() -> bytes:
    return project_xml(
        [
            task_xml(1, "Preliminares", "1", summary=True, start="2026-03-02",
                     finish="2026-03-18", baseline=("2026-03-02", "2026-03-13", 80), pct=100),
            task_xml(2, "Localización", "1.1", start="2026-03-02", finish="2026-03-06",
                     duration_h=40, baseline=("2026-03-02", "2026-03-06", 40), pct=100,
                     actual_finish="2026-03-06"),
            task_xml(3, "Descapote", "1.2", start="2026-03-09", finish="2026-03-18",
                     duration_h=64, baseline=("2026-03-09", "2026-03-13", 40), pct=100,
                     actual_finish="2026-03-18", critical=True, preds=((2, 1),)),
            task_xml(4, "Estructura", "2", summary=True, start="2026-03-19",
                     finish="2026-03-31", baseline=("2026-03-16", "2026-03-27", 72), pct=10),
            task_xml(7, "Vigas", "2.1", start="2026-03-19", finish="2026-03-31",
                     duration_h=64, baseline=("2026-03-16", "2026-03-27", 72), pct=10,
                     critical=True, preds=((3, 1),), sort_id=5),
            task_xml(6, "Fin de obra", "2.2", milestone=True, start="2026-03-31",
                     finish="2026-03-31", duration_h=0,
                     baseline=("2026-03-27", "2026-03-27", 0), pct=0, critical=True,
                     preds=((7, 1),)),
        ],
        status_date="2026-03-27",
        resources=RESOURCES,
        assignments=((2, 2), (3, 1), (7, 1)),
    )
```

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_mspdi.py`:

```python
"""Lector MSPDI: qué se lee y qué se rechaza."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from visitacampo.projects.mspdi import MspdiError, parse_mspdi

from .samples import project_xml, task_xml, v1


class ParseV1Tests(SimpleTestCase):
    def setUp(self) -> None:
        self.schedule = parse_mspdi(v1())
        self.by_uid = {t.uid: t for t in self.schedule.tasks}

    def test_reads_project_level_data(self) -> None:
        self.assertEqual(self.schedule.status_date, date(2026, 3, 20))
        self.assertEqual(sorted(self.by_uid), [1, 2, 3, 4, 5, 6])  # sin el uid 0

    def test_reads_task_fields(self) -> None:
        t = self.by_uid[3]
        self.assertEqual(t.name, "Descapote")
        self.assertEqual(t.wbs, "1.2")
        self.assertEqual(t.outline_number, "1.2")
        self.assertEqual(t.outline_level, 2)
        self.assertEqual(t.sort_order, 3)
        self.assertEqual((t.start, t.finish), (date(2026, 3, 9), date(2026, 3, 17)))
        self.assertEqual(t.duration_days, Decimal("7"))
        self.assertEqual(t.baseline_start, date(2026, 3, 9))
        self.assertEqual(t.baseline_finish, date(2026, 3, 13))
        self.assertEqual(t.baseline_duration_days, Decimal("5"))
        self.assertEqual(t.project_pct, Decimal("60"))
        self.assertEqual(t.project_physical_pct, Decimal("50"))
        self.assertTrue(t.is_critical)
        self.assertFalse(t.is_summary)

    def test_slack_is_converted_to_days(self) -> None:
        self.assertEqual(self.by_uid[2].total_slack_days, Decimal("1"))
        self.assertEqual(self.by_uid[2].free_slack_days, Decimal("0"))

    def test_summary_milestone_and_actual_finish(self) -> None:
        self.assertTrue(self.by_uid[1].is_summary)
        self.assertTrue(self.by_uid[6].is_milestone)
        self.assertEqual(self.by_uid[2].actual_finish, date(2026, 3, 6))
        self.assertIsNone(self.by_uid[3].actual_finish)

    def test_dependencies(self) -> None:
        deps = {(d.predecessor_uid, d.successor_uid, d.type) for d in self.schedule.dependencies}
        self.assertEqual(deps, {(2, 3, "FS"), (3, 5, "FS"), (5, 6, "FS")})

    def test_resources_and_assignments_skip_unassigned(self) -> None:
        self.assertEqual(
            {(r.uid, r.name) for r in self.schedule.resources},
            {(1, "Cuadrilla A"), (2, "Topógrafo")},
        )
        self.assertEqual(set(self.schedule.assignments), {(2, 2), (3, 1), (5, 1)})

    def test_calendar(self) -> None:
        cal = self.schedule.calendar
        self.assertEqual(cal.working_weekdays, frozenset(range(5)))
        self.assertFalse(cal.is_working(date(2026, 3, 23)))
        self.assertTrue(cal.is_working(date(2026, 3, 24)))


class ParseVariantsTests(SimpleTestCase):
    def test_without_namespace(self) -> None:
        data = project_xml([task_xml(1, "A", "1")], namespace=False)
        self.assertEqual([t.uid for t in parse_mspdi(data).tasks], [1])

    def test_without_baseline_calendar_or_status_date(self) -> None:
        data = project_xml([task_xml(1, "A", "1")], with_calendar=False, status_date=None)
        schedule = parse_mspdi(data)
        self.assertIsNone(schedule.tasks[0].baseline_start)
        self.assertIsNone(schedule.status_date)
        self.assertEqual(schedule.calendar.working_weekdays, frozenset(range(5)))

    def test_null_rows_are_skipped(self) -> None:
        data = project_xml([task_xml(1, "A", "1"), "<Task><UID>9</UID><IsNull>1</IsNull></Task>"])
        self.assertEqual([t.uid for t in parse_mspdi(data).tasks], [1])

    def test_dependency_to_unknown_task_is_dropped(self) -> None:
        data = project_xml([task_xml(1, "A", "1", preds=((99, 1),))])
        self.assertEqual(parse_mspdi(data).dependencies, ())


class RejectionTests(SimpleTestCase):
    def assertRejected(self, data: bytes, fragment: str) -> None:
        with self.assertRaises(MspdiError) as caught:
            parse_mspdi(data)
        self.assertIn(fragment, str(caught.exception))

    def test_not_xml(self) -> None:
        self.assertRejected(b"esto no es xml", "no es un XML")

    def test_truncated_xml(self) -> None:
        self.assertRejected(v1()[:200], "no es un XML")

    def test_other_root(self) -> None:
        self.assertRejected(b"<html><body/></html>", "MSPDI")

    def test_task_without_name(self) -> None:
        bad = "<Task><UID>5</UID><OutlineNumber>1</OutlineNumber><OutlineLevel>1</OutlineLevel></Task>"
        self.assertRejected(project_xml([bad]), "La tarea 5 no tiene nombre")

    def test_task_without_uid(self) -> None:
        bad = "<Task><Name>X</Name><OutlineLevel>1</OutlineLevel></Task>"
        self.assertRejected(project_xml([bad]), "sin UniqueID")

    def test_duplicated_uid(self) -> None:
        data = project_xml([task_xml(1, "A", "1"), task_xml(1, "B", "2")])
        self.assertRejected(data, "UniqueID repetido: 1")

    def test_missing_outline_number(self) -> None:
        bad = "<Task><UID>5</UID><Name>X</Name><OutlineLevel>1</OutlineLevel></Task>"
        self.assertRejected(project_xml([bad]), "OutlineNumber")

    def test_bad_date(self) -> None:
        bad = task_xml(1, "A", "1").replace("</Task>", "<Start>ayer</Start></Task>")
        self.assertRejected(project_xml([bad]), "Fecha inválida en la tarea 1")

    def test_too_big(self) -> None:
        with self.assertRaises(MspdiError) as caught:
            parse_mspdi(v1(), max_bytes=100)
        self.assertIn("supera", str(caught.exception))

    def test_entity_expansion_is_refused(self) -> None:
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><Project>&b;</Project>'
        )
        self.assertRejected(bomb, "no es un XML")
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_mspdi.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.mspdi'`.

- [ ] **Step 4: Implementar**

`server/visitacampo/projects/mspdi.py`:

```python
"""Lector de MSPDI (el XML que exporta MS Project con "Guardar como → XML").

Puro: recibe bytes y devuelve un `ParsedSchedule`; no toca la base de datos.
Un lector futuro de `.mpp` (MPXJ) solo tiene que producir el mismo
`ParsedSchedule` para reutilizar todo lo demás.

Seguridad: el XML lo sube un usuario, así que se lee con `defusedxml`
(sin expansión de entidades ni entidades externas) y con un límite de
tamaño previo al parseo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .workdays import CalendarException, WorkCalendar

MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MINUTES_PER_DAY = 480
#: `PredecessorLink/Type` de MSPDI → código de `DependencyType`.
LINK_TYPES = {0: "FF", 1: "FS", 2: "SF", 3: "SS"}
#: `Assignment/ResourceUID` de una tarea sin recurso.
UNASSIGNED_RESOURCE = -65535
_DURATION = re.compile(
    r"^-?P(?:(?P<d>\d+(?:\.\d+)?)D)?T?(?:(?P<h>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$"
)


class MspdiError(ValueError):
    """El archivo no se puede importar; el mensaje es para el usuario."""


@dataclass(frozen=True)
class ParsedTask:
    uid: int
    sort_order: int
    name: str
    wbs: str
    outline_number: str
    outline_level: int
    is_summary: bool
    is_milestone: bool
    start: date | None
    finish: date | None
    actual_finish: date | None
    duration_days: Decimal | None
    baseline_start: date | None
    baseline_finish: date | None
    baseline_duration_days: Decimal | None
    project_pct: Decimal | None
    project_physical_pct: Decimal | None
    total_slack_days: Decimal | None
    free_slack_days: Decimal | None
    is_critical: bool


@dataclass(frozen=True)
class ParsedDependency:
    predecessor_uid: int
    successor_uid: int
    type: str
    lag_days: Decimal


@dataclass(frozen=True)
class ParsedResource:
    uid: int
    name: str


@dataclass(frozen=True)
class ParsedSchedule:
    status_date: date | None
    calendar: WorkCalendar
    tasks: tuple[ParsedTask, ...]
    dependencies: tuple[ParsedDependency, ...]
    resources: tuple[ParsedResource, ...]
    #: Pares (task_uid, resource_uid).
    assignments: tuple[tuple[int, int], ...]


class _Reader:
    """Acceso a hijos por nombre local, con o sin el namespace de MSPDI."""

    def __init__(self, root: Element) -> None:
        self.ns = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""

    def q(self, name: str) -> str:
        return f"{{{self.ns}}}{name}" if self.ns else name

    def path(self, *names: str) -> str:
        return "/".join(self.q(n) for n in names)

    def text(self, el: Element, name: str) -> str | None:
        child = el.find(self.q(name))
        if child is None or child.text is None:
            return None
        value = child.text.strip()
        return value or None

    def integer(self, el: Element, name: str) -> int | None:
        value = self.text(el, name)
        if value is None:
            return None
        try:
            return int(Decimal(value))
        except InvalidOperation as exc:
            raise MspdiError(f"Número inválido en <{name}>: {value}.") from exc

    def decimal(self, el: Element, name: str) -> Decimal | None:
        value = self.text(el, name)
        if value is None:
            return None
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise MspdiError(f"Número inválido en <{name}>: {value}.") from exc

    def flag(self, el: Element, name: str) -> bool:
        return self.text(el, name) in ("1", "true")


def _to_date(value: str | None, uid: int | str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise MspdiError(f"Fecha inválida en la tarea {uid}: {value}.") from exc


def _duration_days(value: str | None, minutes_per_day: int) -> Decimal | None:
    if value is None:
        return None
    match = _DURATION.match(value)
    if match is None:
        return None
    parts = {k: Decimal(v) if v else Decimal(0) for k, v in match.groupdict().items()}
    minutes = parts["d"] * minutes_per_day + parts["h"] * 60 + parts["m"] + parts["s"] / 60
    return (minutes / minutes_per_day).quantize(Decimal("0.01"))


def _tenths_to_days(value: Decimal | None, minutes_per_day: int) -> Decimal | None:
    if value is None:
        return None
    return (value / 10 / minutes_per_day).quantize(Decimal("0.01"))


def parse_mspdi(data: bytes, *, max_bytes: int = MAX_BYTES) -> ParsedSchedule:
    if len(data) > max_bytes:
        raise MspdiError(f"El archivo supera {max_bytes // (1024 * 1024)} MB.")
    try:
        root = fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise MspdiError("El archivo no es un XML válido.") from exc
    if root.tag.rsplit("}", 1)[-1] != "Project":
        raise MspdiError("El XML no es un proyecto de MS Project (formato MSPDI).")

    r = _Reader(root)
    minutes_per_day = r.integer(root, "MinutesPerDay") or DEFAULT_MINUTES_PER_DAY
    tasks, dependencies = _tasks(r, root, minutes_per_day)
    task_uids = {t.uid for t in tasks}
    dependencies = tuple(
        d for d in dependencies if d.predecessor_uid in task_uids and d.successor_uid in task_uids
    )
    resources = _resources(r, root)
    resource_uids = {res.uid for res in resources}
    assignments: list[tuple[int, int]] = []
    for el in root.iterfind(r.path("Assignments", "Assignment")):
        pair = (r.integer(el, "TaskUID"), r.integer(el, "ResourceUID"))
        if pair[0] in task_uids and pair[1] in resource_uids and pair not in assignments:
            assignments.append(pair)  # type: ignore[arg-type]
    return ParsedSchedule(
        status_date=_to_date(r.text(root, "StatusDate"), "del proyecto"),
        calendar=_calendar(r, root),
        tasks=tasks,
        dependencies=dependencies,
        resources=resources,
        assignments=tuple(assignments),
    )


def _tasks(
    r: _Reader, root: Element, minutes_per_day: int
) -> tuple[tuple[ParsedTask, ...], tuple[ParsedDependency, ...]]:
    tasks: list[ParsedTask] = []
    deps: list[ParsedDependency] = []
    seen: set[int] = set()
    for el in root.iterfind(r.path("Tasks", "Task")):
        if r.flag(el, "IsNull"):
            continue
        uid = r.integer(el, "UID")
        if uid is None:
            raise MspdiError("Hay una tarea sin UniqueID.")
        level = r.integer(el, "OutlineLevel") or 0
        if uid == 0 or level == 0:
            continue  # resumen del proyecto
        name = r.text(el, "Name")
        if not name:
            raise MspdiError(f"La tarea {uid} no tiene nombre.")
        if uid in seen:
            raise MspdiError(f"UniqueID repetido: {uid}.")
        seen.add(uid)
        outline = r.text(el, "OutlineNumber")
        if not outline:
            raise MspdiError(f"La tarea {uid} no tiene número de esquema (OutlineNumber).")
        baseline = _baseline(r, el)
        tasks.append(
            ParsedTask(
                uid=uid,
                sort_order=r.integer(el, "ID") or uid,
                name=name[:500],
                wbs=(r.text(el, "WBS") or outline)[:100],
                outline_number=outline[:100],
                outline_level=level,
                is_summary=r.flag(el, "Summary"),
                is_milestone=r.flag(el, "Milestone"),
                start=_to_date(r.text(el, "Start"), uid),
                finish=_to_date(r.text(el, "Finish"), uid),
                actual_finish=_to_date(r.text(el, "ActualFinish"), uid),
                duration_days=_duration_days(r.text(el, "Duration"), minutes_per_day),
                baseline_start=_to_date(r.text(baseline, "Start"), uid) if baseline else None,
                baseline_finish=_to_date(r.text(baseline, "Finish"), uid) if baseline else None,
                baseline_duration_days=(
                    _duration_days(r.text(baseline, "Duration"), minutes_per_day)
                    if baseline is not None
                    else None
                ),
                project_pct=r.decimal(el, "PercentComplete"),
                project_physical_pct=r.decimal(el, "PhysicalPercentComplete"),
                total_slack_days=_tenths_to_days(r.decimal(el, "TotalSlack"), minutes_per_day),
                free_slack_days=_tenths_to_days(r.decimal(el, "FreeSlack"), minutes_per_day),
                is_critical=r.flag(el, "Critical"),
            )
        )
        for link in el.iterfind(r.q("PredecessorLink")):
            pred = r.integer(link, "PredecessorUID")
            if pred is None:
                continue
            link_type = r.integer(link, "Type")
            deps.append(
                ParsedDependency(
                    predecessor_uid=pred,
                    successor_uid=uid,
                    type=LINK_TYPES.get(1 if link_type is None else link_type, "FS"),
                    lag_days=_tenths_to_days(r.decimal(link, "LinkLag"), minutes_per_day)
                    or Decimal(0),
                )
            )
    return tuple(tasks), tuple(deps)


def _baseline(r: _Reader, el: Element) -> Element | None:
    for baseline in el.iterfind(r.q("Baseline")):
        if r.integer(baseline, "Number") == 0:
            return baseline
    return None


def _resources(r: _Reader, root: Element) -> tuple[ParsedResource, ...]:
    out: dict[int, ParsedResource] = {}
    for el in root.iterfind(r.path("Resources", "Resource")):
        uid, name = r.integer(el, "UID"), r.text(el, "Name")
        if uid is None or not name or uid == UNASSIGNED_RESOURCE:
            continue
        out[uid] = ParsedResource(uid=uid, name=name[:255])
    return tuple(out.values())


def _calendar(r: _Reader, root: Element) -> WorkCalendar:
    calendars: dict[str | None, Any] = {
        r.text(c, "UID"): c for c in root.iterfind(r.path("Calendars", "Calendar"))
    }
    if not calendars:
        return WorkCalendar()
    cal = calendars.get(r.text(root, "CalendarUID")) or next(iter(calendars.values()))
    chain = [cal]
    base = calendars.get(r.text(cal, "BaseCalendarUID"))
    if base is not None and base is not cal:
        chain.insert(0, base)
    weekdays = set(range(5))
    exceptions: list[CalendarException] = []
    for c in chain:
        for wd in c.iterfind(r.path("WeekDays", "WeekDay")):
            day_type = r.integer(wd, "DayType")
            working = r.flag(wd, "DayWorking")
            if day_type == 0:
                period = _period(r, wd, working)
                if period:
                    exceptions.append(period)
            elif day_type is not None and 1 <= day_type <= 7:
                python_weekday = (day_type + 5) % 7  # 1 = domingo → 6; 2 = lunes → 0
                (weekdays.add if working else weekdays.discard)(python_weekday)
        for ex in c.iterfind(r.path("Exceptions", "Exception")):
            period = _period(r, ex, r.flag(ex, "DayWorking"))
            if period:
                exceptions.append(period)
    try:
        return WorkCalendar(working_weekdays=weekdays, exceptions=exceptions)
    except ValueError as exc:
        raise MspdiError(str(exc)) from exc


def _period(r: _Reader, el: Element, working: bool) -> CalendarException | None:
    period = el.find(r.q("TimePeriod"))
    if period is None:
        return None
    start = _to_date(r.text(period, "FromDate"), "del calendario")
    end = _to_date(r.text(period, "ToDate"), "del calendario")
    if start is None or end is None or end < start:
        return None
    return CalendarException(start=start, end=end, working=working)
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_mspdi.py`
Expected: `21 passed`. Si `test_entity_expansion_is_refused` falla con otra excepción, revisa que `DefusedXmlException` esté en el `except`: `EntitiesForbidden` hereda de ella.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects/mspdi.py server/visitacampo/projects/tests/samples.py server/visitacampo/projects/tests/test_mspdi.py
git commit -m "feat(projects): MSPDI reader (tasks, baseline, slack, links, resources, calendar)"
```

---

### Task 4: Cálculos de atraso

**Files:**
- Create: `server/visitacampo/projects/progress.py`
- Test: `server/visitacampo/projects/tests/test_progress.py`

Reglas (spec, sección 3), con la fecha de corte `cut`:

- **% programado** de una hoja con línea base: hito → 0 antes del fin de la línea base y 100 desde él; si no, días hábiles de `baseline_start` a `min(cut, baseline_finish)` ÷ días hábiles de la línea base × 100, en [0, 100].
- **% real**: SUNO = el valor del mapa (0 si falta); Project = `project_pct` (0 si falta).
- **Fin proyectado** por fuente: si el % real ≥ 100 → `actual_finish` o, si falta, `finish`; si no → `finish`, y como mínimo `cut` cuando `cut` ya pasó el fin de la línea base.
- **Atraso %** = programado − real. **Atraso en días** = `signed_gap(baseline_finish, fin proyectado)`.
- **Peso** = días hábiles de la línea base de la propia tarea (si no tiene línea base, la suma de sus hijas).
- **Resumen**: programado y real ponderados por el peso de las hijas con indicadores; atraso en días = el mayor de las hijas. Si todas pesan 0, promedio simple.
- Sin línea base y sin hijas con indicadores → sin indicadores (`None`).

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_progress.py`:

```python
"""Atrasos con los datos de `samples.v1()` y fecha de corte el viernes 20 de marzo."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from visitacampo.projects.mspdi import parse_mspdi
from visitacampo.projects.progress import TaskInput, compute_indicators, rollup

from .samples import v1

CUT = date(2026, 3, 20)


def inputs_from_v1() -> tuple[list[TaskInput], object]:
    schedule = parse_mspdi(v1())
    by_outline = {t.outline_number: t for t in schedule.tasks}
    tasks = []
    for t in schedule.tasks:
        parent = t.outline_number.rsplit(".", 1)[0] if "." in t.outline_number else None
        tasks.append(
            TaskInput(
                key=str(t.uid),
                parent_key=str(by_outline[parent].uid) if parent else None,
                is_milestone=t.is_milestone,
                baseline_start=t.baseline_start,
                baseline_finish=t.baseline_finish,
                finish=t.finish,
                actual_finish=t.actual_finish,
                project_pct=float(t.project_pct) if t.project_pct is not None else None,
            )
        )
    return tasks, schedule.calendar


class LeafIndicatorTests(SimpleTestCase):
    def setUp(self) -> None:
        tasks, calendar = inputs_from_v1()
        self.ind = compute_indicators(tasks, {}, calendar, CUT)

    def test_finished_on_time_by_project_but_zero_in_suno(self) -> None:
        t = self.ind["2"]
        self.assertEqual(t.planned_pct, 100.0)
        self.assertEqual((t.project.actual_pct, t.project.delay_pct, t.project.delay_days), (100.0, 0.0, 0))
        # SUNO sin reportes: 0 %, fin proyectado = corte → 10 días hábiles tras el 6.
        self.assertEqual((t.suno.actual_pct, t.suno.delay_pct, t.suno.delay_days), (0.0, 100.0, 10))

    def test_late_task_projects_to_cut_date(self) -> None:
        t = self.ind["3"]
        self.assertEqual(t.planned_pct, 100.0)
        self.assertEqual((t.project.delay_pct, t.project.delay_days), (40.0, 5))

    def test_running_task_uses_its_finish_and_skips_holiday(self) -> None:
        t = self.ind["5"]
        self.assertEqual(t.planned_pct, 62.5)  # 5 de 8 días hábiles (el 23 es festivo)
        self.assertEqual(t.weight, 8)
        self.assertEqual((t.project.delay_pct, t.project.delay_days), (62.5, 2))

    def test_milestone(self) -> None:
        t = self.ind["6"]
        self.assertEqual((t.planned_pct, t.weight), (0.0, 0))
        self.assertEqual(t.project.delay_days, 2)

    def test_suno_progress_is_used_when_given(self) -> None:
        tasks, calendar = inputs_from_v1()
        ind = compute_indicators(tasks, {"3": 100.0}, calendar, CUT)
        self.assertEqual(ind["3"].suno.delay_pct, 0.0)
        # Al 100 % sin fin real, el fin proyectado es el `finish` del Project (17).
        self.assertEqual(ind["3"].suno.delay_days, 2)


class RollupTests(SimpleTestCase):
    def setUp(self) -> None:
        tasks, calendar = inputs_from_v1()
        self.ind = compute_indicators(tasks, {}, calendar, CUT)

    def test_stage_one(self) -> None:
        stage = self.ind["1"]
        self.assertEqual(stage.planned_pct, 100.0)
        self.assertEqual(stage.weight, 10)  # su propia línea base: 2 al 13
        self.assertEqual((stage.project.actual_pct, stage.project.delay_pct), (80.0, 20.0))
        self.assertEqual(stage.project.delay_days, 5)

    def test_stage_two_ignores_zero_weight_milestone_in_average(self) -> None:
        stage = self.ind["4"]
        self.assertEqual(stage.planned_pct, 62.5)
        self.assertEqual(stage.project.delay_days, 2)

    def test_project_total(self) -> None:
        total = rollup([self.ind["1"], self.ind["4"]])
        assert total is not None
        self.assertEqual(total.planned_pct, 83.3)
        self.assertEqual(total.project.actual_pct, 44.4)
        self.assertEqual(total.project.delay_days, 5)

    def test_rollup_of_nothing_is_none(self) -> None:
        self.assertIsNone(rollup([]))


class NoBaselineTests(SimpleTestCase):
    def test_task_without_baseline_has_no_indicators(self) -> None:
        task = TaskInput(
            key="1", parent_key=None, is_milestone=False, baseline_start=None,
            baseline_finish=None, finish=date(2026, 3, 6), actual_finish=None, project_pct=50.0,
        )
        from visitacampo.projects.workdays import WorkCalendar

        self.assertIsNone(compute_indicators([task], {}, WorkCalendar(), CUT)["1"])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_progress.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.progress'`.

- [ ] **Step 3: Implementar**

`server/visitacampo/projects/progress.py`:

```python
"""% programado y atrasos por tarea, con las dos fuentes de avance (spec, sección 3).

Puro: recibe tareas, el % SUNO por tarea, el calendario y la fecha de corte.
No se guarda nada; se calcula al consultar. Cada indicador sale dos veces,
una con el % SUNO (avance de campo validado) y otra con el % del Project;
una fuente sin dato vale 0.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from .workdays import WorkCalendar


@dataclass(frozen=True)
class TaskInput:
    key: str
    parent_key: str | None
    is_milestone: bool
    baseline_start: date | None
    baseline_finish: date | None
    finish: date | None
    actual_finish: date | None
    project_pct: float | None


@dataclass(frozen=True)
class SourceIndicators:
    actual_pct: float
    delay_pct: float
    delay_days: int | None


@dataclass(frozen=True)
class TaskIndicators:
    planned_pct: float
    #: Días hábiles de la línea base: el peso en la consolidación.
    weight: int
    suno: SourceIndicators
    project: SourceIndicators


def compute_indicators(
    tasks: Sequence[TaskInput],
    suno_pct: Mapping[str, float],
    calendar: WorkCalendar,
    cut: date,
) -> dict[str, TaskIndicators | None]:
    children: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        if t.parent_key is not None:
            children[t.parent_key].append(t.key)
    by_key = {t.key: t for t in tasks}
    result: dict[str, TaskIndicators | None] = {}

    def visit(key: str) -> TaskIndicators | None:
        if key in result:
            return result[key]
        task = by_key[key]
        if children.get(key):
            own = rollup([i for c in children[key] if (i := visit(c)) is not None])
            if own is not None and task.baseline_start and task.baseline_finish:
                own = replace(
                    own, weight=calendar.count_inclusive(task.baseline_start, task.baseline_finish)
                )
            result[key] = own
        else:
            result[key] = _leaf(task, suno_pct.get(key, 0.0), calendar, cut)
        return result[key]

    for t in tasks:
        visit(t.key)
    return result


def rollup(children: Sequence[TaskIndicators]) -> TaskIndicators | None:
    if not children:
        return None
    total = sum(c.weight for c in children)

    def avg(values: list[float]) -> float:
        if total == 0:
            return sum(values) / len(values)
        return sum(v * c.weight for v, c in zip(values, children, strict=True)) / total

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
        weight=total,
        suno=source("suno"),
        project=source("project"),
    )


def _leaf(task: TaskInput, suno: float, calendar: WorkCalendar, cut: date) -> TaskIndicators | None:
    start, finish = task.baseline_start, task.baseline_finish
    if start is None or finish is None:
        return None
    weight = 0 if task.is_milestone else calendar.count_inclusive(start, finish)
    if weight == 0:
        planned = 100.0 if cut >= finish else 0.0
    elif cut < start:
        planned = 0.0
    else:
        planned = min(100.0, 100.0 * calendar.count_inclusive(start, min(cut, finish)) / weight)
    return TaskIndicators(
        planned_pct=round(planned, 1),
        weight=weight,
        suno=_source(task, planned, suno, calendar, cut),
        project=_source(task, planned, task.project_pct or 0.0, calendar, cut),
    )


def _source(
    task: TaskInput, planned: float, actual: float, calendar: WorkCalendar, cut: date
) -> SourceIndicators:
    assert task.baseline_finish is not None
    if actual >= 100:
        projected = task.actual_finish or task.finish
    else:
        projected = task.finish
        if cut > task.baseline_finish and (projected is None or projected < cut):
            projected = cut
    delay_days = (
        calendar.signed_gap(task.baseline_finish, projected) if projected is not None else None
    )
    return SourceIndicators(
        actual_pct=round(actual, 1),
        delay_pct=round(planned - actual, 1),
        delay_days=delay_days,
    )
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_progress.py`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/progress.py server/visitacampo/projects/tests/test_progress.py
git commit -m "feat(projects): planned %, delay in % and working days, WBS roll-up"
```

---

### Task 5: Permisos y acceso

**Files:**
- Modify: `server/visitacampo/core/policies.py`
- Modify: `server/visitacampo/core/audit.py`
- Create: `server/visitacampo/projects/access.py`
- Test: `server/visitacampo/projects/tests/test_access.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_access.py`:

```python
"""Quién ve qué proyecto y quién es responsable de qué tarea."""

from __future__ import annotations

import unittest

from django.http import Http404
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import AppUser, Project, Role, Task, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.access import effective_responsibles, get_visible_project, visible_projects

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


def ctx_for(user: AppUser):
    return resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))


@skip_without_db
class VisibilityTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.p1 = Project.objects.create(org=self.org, name="Obra 1", created_by=self.admin)
        self.p2 = Project.objects.create(org=self.org, name="Obra 2", created_by=self.admin)
        self.stage = Task.objects.create(
            org=self.org, project=self.p1, uid=1, outline_number="1", outline_level=1,
            sort_order=1, name="Etapa", is_summary=True,
        )
        self.leaf = Task.objects.create(
            org=self.org, project=self.p1, uid=2, outline_number="1.1", outline_level=2,
            sort_order=2, name="Hoja", parent=self.stage,
        )
        other = make_org("Otra")
        other_admin = make_user(other, username="otro", role=Role.ADMIN)
        self.foreign = Project.objects.create(org=other, name="Ajena", created_by=other_admin)

    def test_admin_and_viewer_see_all_projects_of_their_org_only(self) -> None:
        for user in (self.admin, self.viewer):
            self.assertEqual(
                set(visible_projects(ctx_for(user))), {self.p1, self.p2}, user.username
            )

    def test_field_agent_sees_only_projects_where_responsible(self) -> None:
        self.assertEqual(list(visible_projects(ctx_for(self.agent))), [])
        TaskResponsible.objects.create(
            org=self.org, task=self.leaf, user=self.agent, assigned_by=self.admin
        )
        self.assertEqual(list(visible_projects(ctx_for(self.agent))), [self.p1])

    def test_get_visible_project_hides_foreign_and_invisible(self) -> None:
        with self.assertRaises(Http404):
            get_visible_project(ctx_for(self.admin), self.foreign.pk)
        with self.assertRaises(Http404):
            get_visible_project(ctx_for(self.agent), self.p1.pk)
        self.assertEqual(get_visible_project(ctx_for(self.admin), self.p1.pk), self.p1)

    def test_responsible_of_a_summary_covers_its_branch(self) -> None:
        TaskResponsible.objects.create(
            org=self.org, task=self.stage, user=self.agent, assigned_by=self.admin
        )
        responsibles = effective_responsibles(ctx_for(self.admin), self.p1)
        self.assertEqual(responsibles[self.leaf.pk], [self.agent])
        self.assertEqual(responsibles[self.stage.pk], [self.agent])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_access.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.access'`.

- [ ] **Step 3: Políticas y auditoría**

En `server/visitacampo/core/policies.py`, tras la definición de `SUPERVISION_ROLES`, añadir:

```python

#: Cronograma de proyectos (costos, etapa 1): quién ve **todos** los proyectos
#: de la organización. Un `FIELD_AGENT` solo ve aquellos donde es responsable
#: de alguna tarea (`projects.access.visible_projects`).
PROJECT_READ_ALL_ROLES: frozenset[RoleValue] = frozenset(
    {Role.OWNER, Role.ADMIN, Role.SUPERVISOR, Role.VIEWER}
)
```

y tras `can_export_visits` añadir:

```python
def can_manage_projects(ctx: RequestContext, org_id: UUID) -> bool:
    """Crear proyectos e importar cronogramas: administración de la organización."""
    return user_has_organization_roles(ctx, org_id, ADMIN_ROLES)


def can_assign_task_responsibles(ctx: RequestContext, org_id: UUID) -> bool:
    """Asignar o quitar responsables de tareas: supervisión."""
    return user_has_organization_roles(ctx, org_id, SUPERVISION_ROLES)


def can_export_schedule(ctx: RequestContext, org_id: UUID) -> bool:
    """Exportar el cronograma a Excel: el archivo sale del control de la app."""
    return user_has_organization_roles(ctx, org_id, SUPERVISION_ROLES)
```

En `server/visitacampo/core/audit.py`, dentro de `AuditAction`, al final de la clase añadir:

```python
    # Cronograma de proyectos (costos, etapa 1).
    PROJECT_CREATED = "projects.created"
    SCHEDULE_IMPORTED = "projects.schedule_imported"
    SCHEDULE_IMPORT_DISCARDED = "projects.schedule_import_discarded"
    TASK_RESPONSIBLE_ASSIGNED = "projects.responsible_assigned"
    TASK_RESPONSIBLE_REMOVED = "projects.responsible_removed"
    SCHEDULE_EXPORTED = "projects.schedule_exported"
```

- [ ] **Step 4: Implementar `access.py`**

`server/visitacampo/projects/access.py`:

```python
"""Qué proyectos ve cada usuario y quién responde por cada tarea."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from django.db.models import QuerySet
from django.http import Http404

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Project, Task, TaskResponsible
from visitacampo.core.policies import PROJECT_READ_ALL_ROLES
from visitacampo.core.scoping import scoped_qs


def visible_projects(ctx: RequestContext) -> QuerySet[Project]:
    projects = scoped_qs(ctx, Project)
    if ctx.has_any_role(PROJECT_READ_ALL_ROLES):
        return projects
    return projects.filter(
        tasks__responsibles__user_id=ctx.user_id, tasks__retired_in__isnull=True
    ).distinct()


def get_visible_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 si es de otra organización o el usuario no lo ve (RD-11)."""
    try:
        return visible_projects(ctx).get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise Http404 from exc


def effective_responsibles(ctx: RequestContext, project: Project) -> dict[UUID, list[AppUser]]:
    """Responsables de cada tarea: los suyos más los heredados de sus ancestros."""
    parents = dict(
        scoped_qs(ctx, Task).filter(project=project).values_list("id", "parent_id")
    )
    direct: dict[UUID, list[AppUser]] = defaultdict(list)
    for link in (
        scoped_qs(ctx, TaskResponsible)
        .filter(task__project=project)
        .select_related("user")
        .order_by("user__username")
    ):
        direct[link.task_id].append(link.user)

    memo: dict[UUID, list[AppUser]] = {}

    def resolve(task_id: UUID) -> list[AppUser]:
        if task_id not in memo:
            parent = parents.get(task_id)
            inherited = resolve(parent) if parent is not None else []
            seen = {u.pk for u in direct.get(task_id, [])}
            memo[task_id] = direct.get(task_id, []) + [u for u in inherited if u.pk not in seen]
        return memo[task_id]

    return {task_id: resolve(task_id) for task_id in parents}
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_access.py visitacampo/core/tests/test_architecture.py visitacampo/core/tests/test_policies.py`
Expected: todo pasa (`test_architecture` confirma que no se usa `.objects` en producción).

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/core/policies.py server/visitacampo/core/audit.py server/visitacampo/projects/access.py server/visitacampo/projects/tests/test_access.py
git commit -m "feat(projects): role-based project visibility and inherited responsibles"
```

---

### Task 6: Importación — vista previa (resumen de cambios)

**Files:**
- Create: `server/visitacampo/projects/importer.py`
- Test: `server/visitacampo/projects/tests/test_importer.py`

`summary` (lo que guarda `ScheduleImport.summary`):

```json
{
  "task_count": 6,
  "added": 1, "retired": 1, "reactivated": 0, "modified": 4,
  "baseline_changed": true,
  "examples": {"added": ["2.1 Vigas"], "retired": ["2.1 Columnas"], "reactivated": [], "modified": ["…"]},
  "warnings": ["1 tarea retirada tiene responsables: 2.1 Columnas."]
}
```

`examples` lleva como máximo 20 nombres por grupo.

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_importer.py`:

```python
"""Importación de cronogramas: vista previa y aplicación."""

from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Project, Role, ScheduleImportStatus
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import ImportRejected, stage_import
from visitacampo.projects.mspdi import parse_mspdi
from visitacampo.projects.importer import summarize_changes

from .samples import project_xml, task_xml, v1

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class SummarizeChangesTests(unittest.TestCase):
    def test_first_import_is_all_added(self) -> None:
        summary = summarize_changes({}, parse_mspdi(v1()), set())
        self.assertEqual(
            (summary["task_count"], summary["added"], summary["retired"], summary["modified"]),
            (6, 6, 0, 0),
        )
        self.assertFalse(summary["baseline_changed"])
        self.assertEqual(summary["warnings"], [])

    def test_no_baseline_warning(self) -> None:
        summary = summarize_changes({}, parse_mspdi(project_xml([task_xml(1, "A", "1")])), set())
        self.assertIn("Sin línea base: no se calcularán atrasos.", summary["warnings"])


@skip_without_db
class StageImportTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)

    def test_stage_creates_pending_import_with_summary(self) -> None:
        imp = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        self.assertEqual(imp.status, ScheduleImportStatus.PENDING)
        self.assertIsNone(imp.number)
        self.assertEqual(imp.summary["added"], 6)
        self.assertEqual(str(imp.status_date), "2026-03-20")
        self.assertEqual(self.project.tasks.count(), 0)  # nada aplicado aún

    def test_stage_rejects_invalid_file(self) -> None:
        with self.assertRaises(ImportRejected) as caught:
            stage_import(self.ctx, self.project, file_name="x.xml", data=b"nada")
        self.assertIn("no es un XML", str(caught.exception))
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_importer.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.importer'`.

- [ ] **Step 3: Implementar la vista previa**

`server/visitacampo/projects/importer.py`:

```python
"""Importación de un XML de MS Project a un proyecto, en dos pasos.

1. `stage_import`: lee el XML, calcula el resumen de cambios contra las
   tareas actuales y guarda un `ScheduleImport` pendiente (vista previa).
2. `apply_import`: en una transacción, con el proyecto bloqueado, aplica el
   XML guardado. `discard_import` lo descarta.

Las tareas se identifican por `UniqueID` y nunca se borran: las que no
vienen en el XML quedan `retired_in`; si vuelven, se reactivan.
"""

from __future__ import annotations

import gzip
from collections.abc import Mapping
from typing import Any

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Project, ScheduleImport, Task, TaskResponsible
from visitacampo.core.scoping import scoped_qs

from .mspdi import MspdiError, ParsedSchedule, parse_mspdi

#: Campos de `Task` que vienen del XML (mismo nombre en `ParsedTask`).
TASK_FIELDS = (
    "wbs",
    "outline_number",
    "outline_level",
    "sort_order",
    "name",
    "is_summary",
    "is_milestone",
    "start",
    "finish",
    "actual_finish",
    "duration_days",
    "baseline_start",
    "baseline_finish",
    "baseline_duration_days",
    "project_pct",
    "project_physical_pct",
    "total_slack_days",
    "free_slack_days",
    "is_critical",
)
BASELINE_FIELDS = ("baseline_start", "baseline_finish")
MAX_EXAMPLES = 20


class ImportRejected(Exception):
    """La importación no se puede hacer; el mensaje es para el usuario."""


def _label(outline: str, name: str) -> str:
    return f"{outline} {name}"


def summarize_changes(
    existing: Mapping[int, dict[str, Any]],
    parsed: ParsedSchedule,
    responsible_uids: set[int],
) -> dict[str, Any]:
    """Qué cambiaría al aplicar `parsed`.

    `existing` son las tareas del proyecto por `uid`: los `TASK_FIELDS` más
    `retired` (bool). `responsible_uids`: uids de tareas con responsables.
    """
    examples: dict[str, list[str]] = {"added": [], "retired": [], "reactivated": [], "modified": []}
    counts = dict.fromkeys(examples, 0)
    baseline_changed = False

    def note(group: str, label: str) -> None:
        counts[group] += 1
        if len(examples[group]) < MAX_EXAMPLES:
            examples[group].append(label)

    parsed_uids = set()
    for task in parsed.tasks:
        parsed_uids.add(task.uid)
        label = _label(task.outline_number, task.name)
        current = existing.get(task.uid)
        if current is None:
            note("added", label)
            continue
        if current["retired"]:
            note("reactivated", label)
        if any(current[f] != getattr(task, f) for f in TASK_FIELDS):
            note("modified", label)
        if any(current[f] != getattr(task, f) for f in BASELINE_FIELDS):
            baseline_changed = True

    retired_with_responsibles: list[str] = []
    for uid, current in existing.items():
        if uid in parsed_uids or current["retired"]:
            continue
        label = _label(current["outline_number"], current["name"])
        note("retired", label)
        if uid in responsible_uids:
            retired_with_responsibles.append(label)

    warnings: list[str] = []
    if parsed.tasks and not any(t.baseline_finish for t in parsed.tasks):
        warnings.append("Sin línea base: no se calcularán atrasos.")
    undated = sum(1 for t in parsed.tasks if t.start is None or t.finish is None)
    if undated:
        warnings.append(f"{undated} tarea(s) sin fechas de inicio o fin.")
    if retired_with_responsibles:
        warnings.append(
            f"{len(retired_with_responsibles)} tarea(s) retirada(s) con responsables: "
            + ", ".join(retired_with_responsibles[:MAX_EXAMPLES])
            + "."
        )
    return {
        "task_count": len(parsed.tasks),
        **counts,
        "baseline_changed": baseline_changed,
        "examples": examples,
        "warnings": warnings,
    }


def _existing(ctx: RequestContext, project: Project) -> dict[int, dict[str, Any]]:
    rows = scoped_qs(ctx, Task).filter(project=project).values("uid", "retired_in_id", *TASK_FIELDS)
    return {row["uid"]: {**row, "retired": row["retired_in_id"] is not None} for row in rows}


def _parse(data: bytes) -> ParsedSchedule:
    try:
        return parse_mspdi(data)
    except MspdiError as exc:
        raise ImportRejected(str(exc)) from exc


def stage_import(
    ctx: RequestContext, project: Project, *, file_name: str, data: bytes
) -> ScheduleImport:
    parsed = _parse(data)
    responsible_uids = set(
        scoped_qs(ctx, TaskResponsible)
        .filter(task__project=project)
        .values_list("task__uid", flat=True)
    )
    summary = summarize_changes(_existing(ctx, project), parsed, responsible_uids)
    return scoped_qs(ctx, ScheduleImport).create(
        org_id=ctx.org_id,
        project=project,
        file_name=file_name[:255],
        source_xml=gzip.compress(data),
        uploaded_by_id=ctx.user_id,
        status_date=parsed.status_date,
        summary=summary,
    )
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_importer.py`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/importer.py server/visitacampo/projects/tests/test_importer.py
git commit -m "feat(projects): stage schedule import with change summary preview"
```

---

### Task 7: Importación — aplicar y descartar

**Files:**
- Modify: `server/visitacampo/projects/importer.py`
- Modify: `server/visitacampo/projects/tests/test_importer.py`

- [ ] **Step 1: Añadir las pruebas que fallan**

En `test_importer.py`, cambiar el import de `importer` por:

```python
from visitacampo.projects.importer import (
    ImportRejected,
    apply_import,
    discard_import,
    stage_import,
    summarize_changes,
)
```

(y borrar la línea suelta `from visitacampo.projects.importer import summarize_changes`), importar también `v2` de `.samples`, y `TaskResponsible`, `TaskDependency`, `TaskResource` de `visitacampo.core.models`. Añadir al final:

```python
@skip_without_db
class ApplyImportTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)

    def _import(self, data: bytes):
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=data)
        return apply_import(self.ctx, staged.pk)

    def test_first_apply_creates_tasks_hierarchy_and_links(self) -> None:
        imp = self._import(v1())
        self.assertEqual((imp.number, imp.status), (1, ScheduleImportStatus.APPLIED))
        tasks = {t.uid: t for t in self.project.tasks.all()}
        self.assertEqual(sorted(tasks), [1, 2, 3, 4, 5, 6])
        self.assertEqual(tasks[3].parent_id, tasks[1].pk)
        self.assertEqual(tasks[3].stage_id, tasks[1].pk)
        self.assertEqual(tasks[1].stage_id, tasks[1].pk)
        self.assertIsNone(tasks[1].parent_id)
        self.assertEqual(TaskDependency.objects.filter(project=self.project).count(), 3)
        self.assertEqual(
            {(l.task.uid, l.resource.name) for l in TaskResource.objects.filter(task__project=self.project)},
            {(2, "Topógrafo"), (3, "Cuadrilla A"), (5, "Cuadrilla A")},
        )
        self.project.refresh_from_db()
        self.assertEqual(str(self.project.status_date), "2026-03-20")
        self.assertEqual(self.project.calendar["exceptions"][0]["start"], "2026-03-23")

    def test_second_version_updates_retires_and_keeps_responsibles(self) -> None:
        self._import(v1())
        columns = self.project.tasks.get(uid=5)
        TaskResponsible.objects.create(
            org=self.org, task=columns, user=self.agent, assigned_by=self.admin
        )
        staged = stage_import(self.ctx, self.project, file_name="v2.xml", data=v2())
        self.assertEqual((staged.summary["added"], staged.summary["retired"]), (1, 1))
        self.assertTrue(staged.summary["baseline_changed"])
        self.assertIn("2.1 Columnas", staged.summary["warnings"][-1])
        imp = apply_import(self.ctx, staged.pk)
        self.assertEqual(imp.number, 2)
        columns.refresh_from_db()
        self.assertEqual(columns.retired_in_id, imp.pk)
        self.assertTrue(TaskResponsible.objects.filter(task=columns, user=self.agent).exists())
        self.assertEqual(str(self.project.tasks.get(uid=3).project_pct), "100.00")
        self.assertEqual(self.project.tasks.get(uid=7).parent.uid, 4)

    def test_retired_task_is_reactivated_when_it_comes_back(self) -> None:
        self._import(v1())
        self._import(v2())
        staged = stage_import(self.ctx, self.project, file_name="v3.xml", data=v1())
        self.assertEqual(staged.summary["reactivated"], 1)
        apply_import(self.ctx, staged.pk)
        self.assertIsNone(self.project.tasks.get(uid=5).retired_in_id)
        self.assertIsNotNone(self.project.tasks.get(uid=7).retired_in_id)

    def test_cannot_apply_twice(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        with self.assertRaises(ImportRejected):
            apply_import(self.ctx, staged.pk)

    def test_stale_preview_is_rejected(self) -> None:
        old = stage_import(self.ctx, self.project, file_name="a.xml", data=v1())
        newer = stage_import(self.ctx, self.project, file_name="b.xml", data=v2())
        apply_import(self.ctx, newer.pk)
        with self.assertRaises(ImportRejected) as caught:
            apply_import(self.ctx, old.pk)
        self.assertIn("Vuelve a subir", str(caught.exception))

    def test_discard(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="obra.xml", data=v1())
        discard_import(self.ctx, staged.pk)
        staged.refresh_from_db()
        self.assertEqual(staged.status, ScheduleImportStatus.DISCARDED)
        with self.assertRaises(ImportRejected):
            apply_import(self.ctx, staged.pk)


@skip_without_db
class ConcurrentImportTests(TransactionTestCase):
    """Otra transacción tiene bloqueado el proyecto: se rechaza sin esperar."""

    def test_apply_is_rejected_while_project_is_locked(self) -> None:
        org = make_org("Constructora")
        admin = make_user(org, username="admin", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        project = Project.objects.create(org=org, name="Obra", created_by=admin)
        staged = stage_import(ctx, project, file_name="obra.xml", data=v1())

        other = connections.create_connection("default")
        other.set_autocommit(False)
        try:
            with other.cursor() as cursor:
                table = connection.ops.quote_name(Project._meta.db_table)
                cursor.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", [project.pk])
            with self.assertRaises(ImportRejected) as caught:
                apply_import(ctx, staged.pk)
            self.assertIn("en curso", str(caught.exception))
        finally:
            other.rollback()
            other.close()
        staged.refresh_from_db()
        self.assertEqual(staged.status, ScheduleImportStatus.PENDING)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_importer.py`
Expected: FAIL con `ImportError: cannot import name 'apply_import'`.

- [ ] **Step 3: Implementar**

Al principio de `importer.py`, sustituir los imports por:

```python
import gzip
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from django.db import DatabaseError, transaction
from django.db.models import Max
from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Project,
    Resource,
    ScheduleImport,
    ScheduleImportStatus,
    Task,
    TaskDependency,
    TaskResource,
    TaskResponsible,
)
from visitacampo.core.scoping import scoped_qs

from .mspdi import MspdiError, ParsedSchedule, parse_mspdi
```

y añadir al final:

```python
def _locked_pending(ctx: RequestContext, import_id: UUID) -> tuple[ScheduleImport, Project]:
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(pk=import_id)
    except ScheduleImport.DoesNotExist as exc:
        raise ImportRejected("La importación no existe.") from exc
    try:
        project = scoped_qs(ctx, Project).select_for_update(nowait=True).get(pk=imp.project_id)
    except DatabaseError as exc:
        raise ImportRejected(
            "Otra importación de este proyecto está en curso; intenta de nuevo en unos segundos."
        ) from exc
    imp = scoped_qs(ctx, ScheduleImport).select_for_update().get(pk=import_id)
    if imp.status != ScheduleImportStatus.PENDING:
        raise ImportRejected("Esta importación ya fue aplicada o descartada.")
    return imp, project


def apply_import(ctx: RequestContext, import_id: UUID) -> ScheduleImport:
    with transaction.atomic():
        imp, project = _locked_pending(ctx, import_id)
        imports = scoped_qs(ctx, ScheduleImport).filter(project=project)
        if imports.filter(
            status=ScheduleImportStatus.APPLIED, applied_at__gt=imp.uploaded_at
        ).exists():
            raise ImportRejected(
                "Se aplicó otra versión después de subir esta. "
                "Vuelve a subir el archivo para ver los cambios actualizados."
            )
        parsed = _parse(gzip.decompress(bytes(imp.source_xml)))
        _apply_tasks(ctx, project, imp, parsed)
        _apply_links(ctx, project, parsed)

        imp.number = (imports.aggregate(n=Max("number"))["n"] or 0) + 1
        imp.status = ScheduleImportStatus.APPLIED
        imp.applied_at = timezone.now()
        imp.save(update_fields=["number", "status", "applied_at"])
        project.calendar = parsed.calendar.to_json()
        project.status_date = parsed.status_date
        project.save(update_fields=["calendar", "status_date"])
        return imp


def discard_import(ctx: RequestContext, import_id: UUID) -> ScheduleImport:
    with transaction.atomic():
        imp, _ = _locked_pending(ctx, import_id)
        imp.status = ScheduleImportStatus.DISCARDED
        imp.save(update_fields=["status"])
        return imp


def _apply_tasks(
    ctx: RequestContext, project: Project, imp: ScheduleImport, parsed: ParsedSchedule
) -> None:
    tasks = scoped_qs(ctx, Task).filter(project=project)
    by_uid = {t.uid: t for t in tasks}
    new: list[Task] = []
    changed: list[Task] = []
    for p in parsed.tasks:
        values = {f: getattr(p, f) for f in TASK_FIELDS}
        task = by_uid.get(p.uid)
        if task is None:
            task = Task(org_id=ctx.org_id, project=project, uid=p.uid, **values)
            new.append(task)
            by_uid[p.uid] = task
        else:
            for field, value in values.items():
                setattr(task, field, value)
            task.retired_in = None
            changed.append(task)
    tasks.bulk_create(new)
    tasks.bulk_update(changed, [*TASK_FIELDS, "retired_in"], batch_size=500)

    parsed_uids = {p.uid for p in parsed.tasks}
    tasks.filter(retired_in__isnull=True).exclude(uid__in=parsed_uids).update(retired_in=imp)

    by_outline = {p.outline_number: by_uid[p.uid] for p in parsed.tasks}
    hierarchy: list[Task] = []
    for p in parsed.tasks:
        task = by_uid[p.uid]
        parent_outline = p.outline_number.rsplit(".", 1)[0] if "." in p.outline_number else None
        task.parent = by_outline.get(parent_outline) if parent_outline else None
        task.stage = by_outline.get(p.outline_number.split(".", 1)[0])
        hierarchy.append(task)
    tasks.bulk_update(hierarchy, ["parent", "stage"], batch_size=500)


def _apply_links(ctx: RequestContext, project: Project, parsed: ParsedSchedule) -> None:
    task_ids = dict(
        scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True)
        .values_list("uid", "id")
    )
    deps = scoped_qs(ctx, TaskDependency)
    deps.filter(project=project).delete()
    seen_pairs: set[tuple[int, int]] = set()
    new_deps = []
    for d in parsed.dependencies:
        pair = (d.predecessor_uid, d.successor_uid)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        new_deps.append(
            TaskDependency(
                org_id=ctx.org_id,
                project=project,
                predecessor_id=task_ids[d.predecessor_uid],
                successor_id=task_ids[d.successor_uid],
                type=d.type,
                lag_days=d.lag_days,
            )
        )
    deps.bulk_create(new_deps)

    scoped_qs(ctx, TaskResource).filter(task__project=project).delete()
    resources = scoped_qs(ctx, Resource).filter(project=project)
    existing = {r.uid: r for r in resources}
    for parsed_resource in parsed.resources:
        current = existing.get(parsed_resource.uid)
        if current is None:
            existing[parsed_resource.uid] = resources.create(
                org_id=ctx.org_id,
                project=project,
                uid=parsed_resource.uid,
                name=parsed_resource.name,
            )
        elif current.name != parsed_resource.name:
            current.name = parsed_resource.name
            current.save(update_fields=["name"])
    resources.exclude(uid__in=[r.uid for r in parsed.resources]).delete()
    scoped_qs(ctx, TaskResource).bulk_create(
        [
            TaskResource(
                org_id=ctx.org_id,
                task_id=task_ids[task_uid],
                resource_id=existing[resource_uid].pk,
            )
            for task_uid, resource_uid in parsed.assignments
        ]
    )
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_importer.py`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/importer.py server/visitacampo/projects/tests/test_importer.py
git commit -m "feat(projects): apply or discard a staged import in one locked transaction"
```

---

### Task 8: Armado del cronograma

**Files:**
- Create: `server/visitacampo/projects/schedule.py`
- Test: `server/visitacampo/projects/tests/test_schedule.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_schedule.py`:

```python
"""Filas del cronograma con indicadores, responsables y ejecutores."""

from __future__ import annotations

import unittest
from datetime import date

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Project, Role, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.schedule import build_schedule, filter_rows

from .samples import v1, v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BuildScheduleTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        staged = stage_import(self.ctx, self.project, file_name="v1.xml", data=v1())
        apply_import(self.ctx, staged.pk)
        self.project.refresh_from_db()

    def test_rows_in_project_order_with_indicators(self) -> None:
        schedule = build_schedule(self.ctx, self.project)
        self.assertEqual(schedule.cut, date(2026, 3, 20))
        self.assertEqual([r.task.uid for r in schedule.rows], [1, 2, 3, 4, 5, 6])
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.project.delay_days, 5)
        self.assertEqual(row.executors, ["Cuadrilla A"])
        assert schedule.total is not None
        self.assertEqual(schedule.total.planned_pct, 83.3)
        self.assertEqual([s.uid for s in schedule.stages], [1, 4])

    def test_cut_date_can_be_overridden(self) -> None:
        schedule = build_schedule(self.ctx, self.project, cut=date(2026, 3, 6))
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.planned_pct, 0.0)

    def test_suno_progress_map_is_applied(self) -> None:
        task = self.project.tasks.get(uid=3)
        schedule = build_schedule(self.ctx, self.project, suno_pct={task.pk: 100.0})
        row = next(r for r in schedule.rows if r.task.uid == 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 100.0)

    def test_retired_tasks_are_not_listed(self) -> None:
        staged = stage_import(self.ctx, self.project, file_name="v2.xml", data=v2())
        apply_import(self.ctx, staged.pk)
        self.project.refresh_from_db()
        uids = [r.task.uid for r in build_schedule(self.ctx, self.project).rows]
        self.assertNotIn(5, uids)
        self.assertIn(7, uids)

    def test_filters(self) -> None:
        stage = self.project.tasks.get(uid=4)
        TaskResponsible.objects.create(
            org=self.org, task=stage, user=self.agent, assigned_by=self.admin
        )
        rows = build_schedule(self.ctx, self.project).rows
        self.assertEqual([r.task.uid for r in filter_rows(rows, stage_id=stage.pk)], [4, 5, 6])
        self.assertEqual(
            [r.task.uid for r in filter_rows(rows, responsible_id=self.agent.pk)], [4, 5, 6]
        )
        self.assertEqual(
            [r.task.uid for r in filter_rows(rows, critical_only=True)], [3, 5, 6]
        )
        # Con la fuente Project, la 2 va al día; todas las demás van atrasadas.
        self.assertNotIn(2, [r.task.uid for r in filter_rows(rows, delayed_only=True, source="project")])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_schedule.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.projects.schedule'`.

- [ ] **Step 3: Implementar**

`server/visitacampo/projects/schedule.py`:

```python
"""El cronograma vigente de un proyecto, listo para mostrar o exportar."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.utils import timezone

from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Project, Task, TaskResource
from visitacampo.core.scoping import scoped_qs

from .access import effective_responsibles
from .progress import TaskIndicators, TaskInput, compute_indicators, rollup
from .workdays import WorkCalendar


@dataclass(frozen=True)
class ScheduleRow:
    task: Task
    responsibles: list[AppUser]
    executors: list[str]
    indicators: TaskIndicators | None


@dataclass(frozen=True)
class Schedule:
    project: Project
    cut: date
    rows: list[ScheduleRow]
    stages: list[Task]
    total: TaskIndicators | None


def build_schedule(
    ctx: RequestContext,
    project: Project,
    *,
    cut: date | None = None,
    suno_pct: Mapping[UUID, float] | None = None,
) -> Schedule:
    """Tareas vigentes en el orden del Project, con sus indicadores.

    `suno_pct`: % SUNO por id de tarea (avance de campo validado). Lo
    alimenta la parte de campo de la etapa 1; sin él, todo vale 0.
    """
    cut = cut or project.status_date or timezone.localdate()
    tasks = list(
        scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True)
        .order_by("sort_order")
    )
    executors: dict[UUID, list[str]] = defaultdict(list)
    for task_id, name in (
        scoped_qs(ctx, TaskResource)
        .filter(task__project=project)
        .order_by("resource__name")
        .values_list("task_id", "resource__name")
    ):
        executors[task_id].append(name)
    responsibles = effective_responsibles(ctx, project)

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
        )
        for t in tasks
    ]
    suno = {str(k): v for k, v in (suno_pct or {}).items()}
    indicators = compute_indicators(
        inputs, suno, WorkCalendar.from_json(project.calendar), cut
    )
    rows = [
        ScheduleRow(
            task=t,
            responsibles=responsibles.get(t.pk, []),
            executors=executors.get(t.pk, []),
            indicators=indicators[str(t.pk)],
        )
        for t in tasks
    ]
    stages = [t for t in tasks if t.outline_level == 1]
    total = rollup([i for s in stages if (i := indicators[str(s.pk)]) is not None])
    return Schedule(project=project, cut=cut, rows=rows, stages=stages, total=total)


def _is_delayed(row: ScheduleRow, source: str | None) -> bool:
    if row.indicators is None:
        return False
    sources = [source] if source else ["suno", "project"]
    for name in sources:
        part = getattr(row.indicators, name)
        if part.delay_pct > 0 or (part.delay_days or 0) > 0:
            return True
    return False


def filter_rows(
    rows: Sequence[ScheduleRow],
    *,
    stage_id: UUID | None = None,
    responsible_id: UUID | None = None,
    critical_only: bool = False,
    delayed_only: bool = False,
    source: str | None = None,
) -> list[ScheduleRow]:
    """`source` = "suno" o "project" limita "atrasadas" a esa fuente; sin él, cualquiera."""
    out = []
    for row in rows:
        if stage_id and row.task.stage_id != stage_id:
            continue
        if responsible_id and responsible_id not in {u.pk for u in row.responsibles}:
            continue
        if critical_only and not row.task.is_critical:
            continue
        if delayed_only and not _is_delayed(row, source):
            continue
        out.append(row)
    return out
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_schedule.py`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/schedule.py server/visitacampo/projects/tests/test_schedule.py
git commit -m "feat(projects): build the current schedule with indicators and filters"
```

---

### Task 9: Base web — plantilla, barra lateral, lista y creación de proyectos

**Files:**
- Create: `server/visitacampo/core/templates/suno/_sidebar.html`
- Modify: `server/visitacampo/core/templates/suno/platform.html`
- Create: `server/visitacampo/core/templates/suno/projects/base.html`
- Create: `server/visitacampo/core/templates/suno/projects/list.html`
- Create: `server/visitacampo/core/static/suno/css/projects.css`
- Create: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Modify: `server/config/urls.py`
- Test: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_views.py`:

```python
"""Páginas /plataforma/proyectos/: acceso por rol y contenido."""

from __future__ import annotations

import unittest

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import AppUser, Project, Role
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import

from .samples import v1

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/proyectos/"


class ViewTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.supervisor = make_user(self.org, username="super", role=Role.SUPERVISOR)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.project = Project.objects.create(org=self.org, name="Obra Norte", created_by=self.admin)

    def as_user(self, user: AppUser) -> None:
        self.client.force_login(user)

    def import_v1(self) -> None:
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        apply_import(ctx, stage_import(ctx, self.project, file_name="v1.xml", data=v1()).pk)
        self.project.refresh_from_db()


@skip_without_db
class ProjectListTests(ViewTestCase):
    def test_requires_login(self) -> None:
        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/plataforma/ingresar/", response["Location"])

    def test_admin_sees_projects_and_create_form(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(BASE)
        self.assertContains(response, "Obra Norte")
        self.assertContains(response, 'name="name"')

    def test_viewer_sees_projects_without_create_form(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(BASE)
        self.assertContains(response, "Obra Norte")
        self.assertNotContains(response, 'name="name"')

    def test_agent_without_tasks_sees_empty_list(self) -> None:
        self.as_user(self.agent)
        self.assertNotContains(self.client.get(BASE), "Obra Norte")

    def test_admin_creates_project(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(BASE, {"name": "Obra Sur", "code": "OS-1", "description": ""})
        project = Project.objects.get(name="Obra Sur")
        self.assertRedirects(response, f"{BASE}{project.pk}/")
        self.assertEqual(project.org_id, self.org.pk)

    def test_duplicate_name_shows_error(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(BASE, {"name": "Obra Norte", "code": "", "description": ""})
        self.assertContains(response, "Ya existe un proyecto con ese nombre")

    def test_supervisor_cannot_create(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(BASE, {"name": "X", "code": "", "description": ""})
        self.assertEqual(response.status_code, 403)

    def test_list_shows_progress_once_imported(self) -> None:
        self.import_v1()
        self.as_user(self.admin)
        response = self.client.get(BASE)
        self.assertContains(response, "83,3")  # % programado del proyecto
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: FAIL (`404` en todas las rutas).

- [ ] **Step 3: Extraer la barra lateral**

Crear `server/visitacampo/core/templates/suno/_sidebar.html` con el bloque `<aside class="sidebar" …> … </aside>` de `platform.html` (líneas 17 a 36 del archivo actual), cambiando las dos primeras entradas del `<nav>` por:

```django
        <a {% if active == "home" %}class="active" {% endif %}href="{% url 'platform_home' %}"><svg class="ui-icon" aria-hidden="true"><use href="{{ suno_icons }}#home"></use></svg> Inicio</a>
        <a {% if active == "projects" %}class="active" {% endif %}href="{% url 'projects:list' %}"><svg class="ui-icon" aria-hidden="true"><use href="{{ suno_icons }}#folder"></use></svg> Proyectos</a>
```

y las entradas de "Mapa", "Actividades", "Registros" y "Capas GIS", que hoy apuntan a anclas (`#mapa`, `#actividad`), por `{% url 'platform_home' %}#mapa` y `{% url 'platform_home' %}#actividad` para que funcionen desde cualquier página. La primera línea del archivo es `{% load static %}` y la segunda `{% static 'suno/brand/icons.svg' as suno_icons %}` (un `include` no hereda variables de `{% static … as %}`).

En `platform.html`, reemplazar ese bloque `<aside …> … </aside>` por:

```django
    {% include "suno/_sidebar.html" with active="home" %}
```

- [ ] **Step 4: Plantilla base del módulo**

`server/visitacampo/core/templates/suno/projects/base.html`:

```django
{% load static %}
{% static 'suno/brand/icons.svg' as suno_icons %}
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#faf9f2">
  <title>{% block title %}Proyectos{% endblock %} · SUNO</title>
  <link rel="icon" type="image/png" href="{% static 'suno/brand/Icon_App.png' %}">
  <link rel="stylesheet" href="{% static 'suno/css/tokens.css' %}">
  <link rel="stylesheet" href="{% static 'suno/css/web.css' %}">
  <link rel="stylesheet" href="{% static 'suno/css/projects.css' %}">
  <script src="{% static 'suno/js/web.js' %}" defer></script>
  {% block head %}{% endblock %}
</head>
<body class="workspace-page">
  <a class="skip-link" href="#workspace-content">Saltar al contenido</a>
  <div class="app-shell">
    {% include "suno/_sidebar.html" with active="projects" %}
    <div class="workspace-frame">
      <header class="workspace-topbar">
        <button class="sidebar-toggle" type="button" aria-controls="workspace-sidebar" aria-expanded="false" data-sidebar-toggle><svg class="ui-icon" aria-hidden="true"><use href="{{ suno_icons }}#menu"></use></svg><span class="sr-only">Abrir navegación</span></button>
        <div class="project-selector"><span>Organización</span><strong>{{ organization.name }}</strong></div>
      </header>
      <main id="workspace-content" class="workspace-content">
        {% if messages %}<ul class="pj-messages">{% for message in messages %}<li class="pj-message pj-message-{{ message.tags }}">{{ message }}</li>{% endfor %}</ul>{% endif %}
        {% block content %}{% endblock %}
      </main>
    </div>
  </div>
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 5: Lista de proyectos**

`server/visitacampo/core/templates/suno/projects/list.html`:

```django
{% extends "suno/projects/base.html" %}
{% block content %}
<section class="workspace-heading">
  <div><p class="editorial-label">Costos y presupuesto</p><h1>Proyectos</h1><p>Cronogramas importados de MS Project, con avance y atrasos.</p></div>
</section>

<section class="pj-panel">
  <table class="pj-table">
    <thead><tr><th>Proyecto</th><th>Fecha de corte</th><th class="num">% programado</th><th class="num">% SUNO</th><th class="num">% Project</th><th class="num">Atraso (días hábiles)<br><small>SUNO / Project</small></th><th class="num">Versión</th></tr></thead>
    <tbody>
    {% for item in items %}
      <tr>
        <td><a href="{% url 'projects:schedule' item.project.pk %}"><strong>{{ item.project.name }}</strong></a>{% if item.project.code %} <small class="pj-muted">{{ item.project.code }}</small>{% endif %}</td>
        <td>{{ item.project.status_date|date:"d/m/Y"|default:"—" }}</td>
        {% if item.total %}
          <td class="num">{{ item.total.planned_pct|floatformat:1 }}</td>
          <td class="num">{{ item.total.suno.actual_pct|floatformat:1 }}</td>
          <td class="num">{{ item.total.project.actual_pct|floatformat:1 }}</td>
          <td class="num">{{ item.total.suno.delay_days|default_if_none:"—" }} / {{ item.total.project.delay_days|default_if_none:"—" }}</td>
        {% else %}
          <td class="num" colspan="4">Sin cronograma con línea base</td>
        {% endif %}
        <td class="num">{{ item.version|default:"—" }}</td>
      </tr>
    {% empty %}
      <tr><td colspan="7" class="pj-empty">No hay proyectos visibles para ti.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>

{% if can_manage %}
<section class="pj-panel">
  <h2>Nuevo proyecto</h2>
  <form method="post" class="pj-form">
    {% csrf_token %}
    {{ form.non_field_errors }}
    <label>Nombre {{ form.name }}</label>{{ form.name.errors }}
    <label>Código {{ form.code }}</label>
    <label>Descripción {{ form.description }}</label>
    <button class="button" type="submit">Crear proyecto</button>
  </form>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Estilos**

`server/visitacampo/core/static/suno/css/projects.css`:

```css
/* Módulo de proyectos (costos, etapa 1): tablas densas y Gantt. */
.pj-panel { margin: 24px 0; padding: 20px; border: 1px solid var(--suno-line); background: var(--suno-white); overflow-x: auto; }
.pj-panel h2 { margin: 0 0 16px; font-size: 18px; }
.pj-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.pj-table th, .pj-table td { padding: 6px 8px; border-bottom: 1px solid var(--suno-line); text-align: left; vertical-align: top; white-space: nowrap; }
.pj-table th { position: sticky; top: 0; background: var(--suno-paper); font-weight: 600; }
.pj-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.pj-table tr.pj-stage td { background: var(--suno-paper); font-weight: 650; }
.pj-table tr.pj-summary td { font-weight: 600; }
.pj-table tr.pj-critical td:first-child { box-shadow: inset 3px 0 0 var(--suno-red); }
.pj-table td.pj-name { white-space: normal; min-width: 240px; }
.pj-late { color: var(--suno-red); font-weight: 600; }
.pj-ok { color: var(--suno-green); }
.pj-muted { color: var(--suno-muted); }
.pj-empty { padding: 24px; text-align: center; color: var(--suno-muted); }
.pj-badge { display: inline-block; padding: 1px 6px; border-radius: var(--suno-radius); background: var(--suno-red); color: var(--suno-white); font-size: 11px; }
.pj-form { display: grid; gap: 12px; max-width: 520px; }
.pj-form label { display: grid; gap: 4px; font-size: 13px; }
.pj-form input, .pj-form textarea, .pj-form select { padding: 8px; border: 1px solid var(--suno-line); font: inherit; }
.pj-filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; font-size: 13px; }
.pj-filters label { display: grid; gap: 4px; }
.pj-tabs { display: flex; gap: 16px; margin: 8px 0 0; font-size: 13px; }
.pj-tabs a[aria-current="page"] { font-weight: 700; color: var(--suno-red); }
.pj-messages { list-style: none; padding: 0; }
.pj-message { padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid var(--suno-green); background: var(--suno-white); }
.pj-message-error { border-color: var(--suno-red); }
.pj-warning { padding: 10px 14px; border-left: 3px solid var(--suno-amber); background: var(--suno-white); }
.pj-gantt { overflow-x: auto; }
.pj-gantt svg { display: block; font-family: var(--suno-font); font-size: 11px; }
.pj-gantt .bar { fill: var(--suno-muted); }
.pj-gantt .bar.critical { fill: var(--suno-red); }
.pj-gantt .bar.summary { fill: var(--suno-ink); }
.pj-gantt .baseline { fill: var(--suno-topography); opacity: .55; }
.pj-gantt .milestone { fill: var(--suno-ink); }
.pj-gantt .milestone.critical { fill: var(--suno-red); }
.pj-gantt .link { stroke: var(--suno-muted); fill: none; }
.pj-gantt .cut { stroke: var(--suno-amber); stroke-width: 2; stroke-dasharray: 4 3; }
.pj-gantt .grid { stroke: var(--suno-line); }
```

- [ ] **Step 7: Vistas y URLs**

`server/visitacampo/projects/views.py`:

```python
"""Páginas del cronograma en /plataforma/proyectos/ (sesión de Django).

Mismo esquema de acceso que `core/views.py`: se exige sesión y membresía;
cada consulta pasa por `scoped_qs` y por `projects.access`. Un proyecto que
el usuario no ve responde 404 (RD-11); una acción para la que le falta el
rol responde 403.
"""

from __future__ import annotations

from typing import cast

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import AppUser, Project, ScheduleImport, ScheduleImportStatus
from visitacampo.core.policies import can_manage_projects
from visitacampo.core.scoping import get_organization, scoped_qs

from .access import visible_projects
from .schedule import build_schedule


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not ctx.is_member:
        raise PermissionDenied
    return ctx


def _render(
    request: HttpRequest, ctx: RequestContext, template: str, context: dict[str, object]
) -> HttpResponse:
    return render(
        request, template, {"organization": get_organization(ctx), **context}
    )


class ProjectForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nombre")
    code = forms.CharField(max_length=50, required=False, label="Código")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def project_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    can_manage = can_manage_projects(ctx, ctx.org_id)
    form = ProjectForm(request.POST or None)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            if scoped_qs(ctx, Project).filter(name__iexact=name).exists():
                form.add_error("name", "Ya existe un proyecto con ese nombre.")
            else:
                project = scoped_qs(ctx, Project).create(
                    org_id=ctx.org_id,
                    name=name,
                    code=form.cleaned_data["code"].strip(),
                    description=form.cleaned_data["description"].strip(),
                    created_by_id=ctx.user_id,
                )
                audit.record(
                    ctx, action=AuditAction.PROJECT_CREATED, target=f"project:{project.pk}"
                )
                return redirect("projects:schedule", project.pk)

    versions = dict(
        scoped_qs(ctx, ScheduleImport)
        .filter(status=ScheduleImportStatus.APPLIED)
        .values("project_id")
        .annotate(n=Max("number"))
        .values_list("project_id", "n")
    )
    items = [
        {
            "project": project,
            "total": build_schedule(ctx, project).total,
            "version": versions.get(project.pk),
        }
        for project in visible_projects(ctx).order_by("name")
    ]
    return _render(
        request,
        ctx,
        "suno/projects/list.html",
        {"items": items, "form": form, "can_manage": can_manage},
    )
```

> `get_organization` ya existe en `core/scoping.py` (`get_organization(ctx) -> Organization`).
> `list.html` muestra los decimales con `floatformat`, que usa la coma decimal del idioma activo (`es`): la prueba busca `83,3`. Si `LANGUAGE_CODE`/`USE_L10N` del proyecto no producen coma, cambia la aserción a `"83.3"` y anótalo en el commit.

`server/visitacampo/projects/urls.py`:

```python
from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
]
```

En `server/config/urls.py`, cambiar `from django.urls import path` por `from django.urls import include, path` y añadir antes de `path("admin/", …)`:

```python
    path("plataforma/proyectos/", include("visitacampo.projects.urls")),
```

Como la prueba `test_admin_creates_project` redirige a `projects:schedule`, añade ya en `urls.py` una ruta provisional que la Task 10 reemplaza:

```python
    path("<uuid:project_id>/", views.project_list, name="schedule"),
```

y en `test_admin_creates_project` usa `assertRedirects(..., fetch_redirect_response=False)`.

- [ ] **Step 8: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py visitacampo/core/tests/test_platform_access.py visitacampo/core/tests/test_branding.py`
Expected: todo pasa.

- [ ] **Step 9: Commit**

```bash
git add server/config/urls.py server/visitacampo/projects server/visitacampo/core/templates server/visitacampo/core/static/suno/css/projects.css
git commit -m "feat(projects): project list and creation under /plataforma/proyectos/"
```

---

### Task 10: Página del cronograma

**Files:**
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Create: `server/visitacampo/core/templates/suno/projects/_header.html`
- Create: `server/visitacampo/core/templates/suno/projects/schedule.html`
- Modify: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Añadir las pruebas que fallan**

Al final de `test_views.py`:

```python
@skip_without_db
class SchedulePageTests(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_v1()
        self.url = f"{BASE}{self.project.pk}/"

    def test_shows_wbs_with_both_progress_columns(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, "Descapote")
        self.assertContains(response, "% SUNO")
        self.assertContains(response, "% Project")
        self.assertContains(response, "Cuadrilla A")
        self.assertContains(response, 'class="pj-badge"', count=3)  # 3 tareas críticas

    def test_filters_by_critical(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"criticas": "1"})
        self.assertNotContains(response, "Localización")
        self.assertContains(response, "Descapote")

    def test_cut_date_parameter(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"corte": "2026-03-06"})
        self.assertContains(response, 'value="2026-03-06"')

    def test_bad_cut_date_falls_back_to_status_date(self) -> None:
        self.as_user(self.admin)
        response = self.client.get(self.url, {"corte": "ayer"})
        self.assertContains(response, 'value="2026-03-20"')

    def test_foreign_or_invisible_project_is_404(self) -> None:
        self.as_user(self.agent)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        other = make_org("Otra")
        outsider = make_user(other, username="otro", role=Role.ADMIN)
        self.as_user(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: FAIL en `SchedulePageTests` (la ruta provisional muestra la lista).

- [ ] **Step 3: Cabecera común del proyecto**

`server/visitacampo/core/templates/suno/projects/_header.html`:

```django
<section class="workspace-heading">
  <div>
    <p class="editorial-label"><a href="{% url 'projects:list' %}">Proyectos</a></p>
    <h1>{{ project.name }}</h1>
    <nav class="pj-tabs" aria-label="Secciones del proyecto">
      <a href="{% url 'projects:schedule' project.pk %}" {% if tab == "schedule" %}aria-current="page"{% endif %}>Cronograma</a>
      <a href="{% url 'projects:gantt' project.pk %}" {% if tab == "gantt" %}aria-current="page"{% endif %}>Gantt</a>
      <a href="{% url 'projects:history' project.pk %}" {% if tab == "history" %}aria-current="page"{% endif %}>Historial</a>
      {% if can_manage %}<a href="{% url 'projects:import' project.pk %}" {% if tab == "import" %}aria-current="page"{% endif %}>Importar XML</a>{% endif %}
    </nav>
  </div>
  <p class="workspace-scope">Fecha de corte<br><span>{{ project.status_date|date:"d/m/Y"|default:"sin importar" }}</span></p>
</section>
```

Como la cabecera enlaza a `gantt`, `history` e `import`, añade ya en `urls.py` las tres rutas apuntando provisionalmente a `views.schedule`; las Tasks 12 y 13 las reemplazan:

```python
    path("<uuid:project_id>/gantt/", views.schedule, name="gantt"),
    path("<uuid:project_id>/historial/", views.schedule, name="history"),
    path("<uuid:project_id>/importar/", views.schedule, name="import"),
```

- [ ] **Step 4: Plantilla del cronograma**

`server/visitacampo/core/templates/suno/projects/schedule.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}{{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="schedule" %}

<section class="pj-panel">
  <form method="get" class="pj-filters">
    <label>Fecha de corte <input type="date" name="corte" value="{{ schedule.cut|date:'Y-m-d' }}"></label>
    <label>Etapa <select name="etapa"><option value="">Todas</option>{% for stage in schedule.stages %}<option value="{{ stage.pk }}" {% if filters.etapa == stage.pk|stringformat:"s" %}selected{% endif %}>{{ stage.outline_number }} {{ stage.name }}</option>{% endfor %}</select></label>
    <label>Responsable <select name="responsable"><option value="">Todos</option>{% for user in responsible_choices %}<option value="{{ user.pk }}" {% if filters.responsable == user.pk|stringformat:"s" %}selected{% endif %}>{{ user.display_name|default:user.username }}</option>{% endfor %}</select></label>
    <label><span><input type="checkbox" name="criticas" value="1" {% if filters.criticas %}checked{% endif %}> Solo críticas</span></label>
    <label><span><input type="checkbox" name="atrasadas" value="1" {% if filters.atrasadas %}checked{% endif %}> Solo atrasadas</span></label>
    <button class="button" type="submit">Aplicar</button>
    {% if can_export %}<a class="text-link" href="{% url 'projects:export' project.pk %}?corte={{ schedule.cut|date:'Y-m-d' }}">Exportar a Excel</a>{% endif %}
  </form>
</section>

{% if schedule.total %}
<section class="metrics-strip" aria-label="Avance del proyecto">
  <div><span>% programado</span><strong>{{ schedule.total.planned_pct|floatformat:1 }}</strong></div>
  <div><span>% SUNO</span><strong>{{ schedule.total.suno.actual_pct|floatformat:1 }}</strong></div>
  <div><span>% Project</span><strong>{{ schedule.total.project.actual_pct|floatformat:1 }}</strong></div>
  <div><span>Atraso SUNO / Project</span><strong>{{ schedule.total.suno.delay_days|default_if_none:"—" }} / {{ schedule.total.project.delay_days|default_if_none:"—" }} días</strong></div>
</section>
{% endif %}

<section class="pj-panel">
  <table class="pj-table">
    <thead>
      <tr>
        <th>EDT</th><th>Tarea</th><th>Ejecutores</th><th>Responsables</th>
        <th>Inicio</th><th>Fin</th><th>Inicio LB</th><th>Fin LB</th>
        <th class="num">% prog.</th><th class="num">% SUNO</th><th class="num">% Project</th>
        <th class="num">Atraso %<br><small>SUNO / Project</small></th>
        <th class="num">Atraso días<br><small>SUNO / Project</small></th>
        <th class="num">Holgura total</th><th class="num">Holgura libre</th>
      </tr>
    </thead>
    <tbody>
    {% for row in rows %}
      {% with t=row.task ind=row.indicators %}
      <tr class="{% if t.outline_level == 1 %}pj-stage{% elif t.is_summary %}pj-summary{% endif %}{% if t.is_critical %} pj-critical{% endif %}">
        <td>{{ t.wbs }}</td>
        <td class="pj-name" style="padding-left: {{ t.outline_level }}em"><a href="{% url 'projects:task' project.pk t.pk %}">{{ t.name }}</a>{% if t.is_milestone %} ◆{% endif %}{% if t.is_critical %} <span class="pj-badge">crítica</span>{% endif %}</td>
        <td>{{ row.executors|join:", "|default:"—" }}</td>
        <td>{% for u in row.responsibles %}{{ u.display_name|default:u.username }}{% if not forloop.last %}, {% endif %}{% empty %}—{% endfor %}</td>
        <td>{{ t.start|date:"d/m/y"|default:"—" }}</td>
        <td>{{ t.finish|date:"d/m/y"|default:"—" }}</td>
        <td>{{ t.baseline_start|date:"d/m/y"|default:"—" }}</td>
        <td>{{ t.baseline_finish|date:"d/m/y"|default:"—" }}</td>
        {% if ind %}
          <td class="num">{{ ind.planned_pct|floatformat:1 }}</td>
          <td class="num">{{ ind.suno.actual_pct|floatformat:1 }}</td>
          <td class="num">{{ ind.project.actual_pct|floatformat:1 }}</td>
          <td class="num"><span class="{% if ind.suno.delay_pct > 0 %}pj-late{% endif %}">{{ ind.suno.delay_pct|floatformat:1 }}</span> / <span class="{% if ind.project.delay_pct > 0 %}pj-late{% endif %}">{{ ind.project.delay_pct|floatformat:1 }}</span></td>
          <td class="num"><span class="{% if ind.suno.delay_days > 0 %}pj-late{% endif %}">{{ ind.suno.delay_days|default_if_none:"—" }}</span> / <span class="{% if ind.project.delay_days > 0 %}pj-late{% endif %}">{{ ind.project.delay_days|default_if_none:"—" }}</span></td>
        {% else %}
          <td class="num" colspan="5">—</td>
        {% endif %}
        <td class="num">{{ t.total_slack_days|floatformat:1|default:"—" }}</td>
        <td class="num">{{ t.free_slack_days|floatformat:1|default:"—" }}</td>
      </tr>
      {% endwith %}
    {% empty %}
      <tr><td colspan="15" class="pj-empty">{% if can_manage %}Todavía no hay cronograma: <a href="{% url 'projects:import' project.pk %}">importa el XML de MS Project</a>.{% else %}Todavía no hay cronograma importado.{% endif %}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 5: Vista**

En `views.py` añadir los imports:

```python
from datetime import date
from uuid import UUID

from visitacampo.core.policies import can_export_schedule, can_manage_projects
from .access import get_visible_project, visible_projects
from .schedule import build_schedule, filter_rows
```

(sustituyendo las líneas equivalentes) y las funciones:

```python
def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _project_context(ctx: RequestContext, project: Project) -> dict[str, object]:
    return {
        "project": project,
        "can_manage": can_manage_projects(ctx, ctx.org_id),
        "can_export": can_export_schedule(ctx, ctx.org_id),
    }


@login_required(login_url="platform_login")
def schedule(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    filters = {
        "etapa": request.GET.get("etapa", ""),
        "responsable": request.GET.get("responsable", ""),
        "criticas": request.GET.get("criticas") == "1",
        "atrasadas": request.GET.get("atrasadas") == "1",
    }
    rows = filter_rows(
        data.rows,
        stage_id=_parse_uuid(filters["etapa"] or None),
        responsible_id=_parse_uuid(filters["responsable"] or None),
        critical_only=bool(filters["criticas"]),
        delayed_only=bool(filters["atrasadas"]),
    )
    responsible_choices = sorted(
        {u.pk: u for row in data.rows for u in row.responsibles}.values(),
        key=lambda u: (u.display_name or u.username).lower(),
    )
    return _render(
        request,
        ctx,
        "suno/projects/schedule.html",
        {
            **_project_context(ctx, project),
            "schedule": data,
            "rows": rows,
            "filters": filters,
            "responsible_choices": responsible_choices,
        },
    )
```

En `urls.py`, reemplazar la ruta provisional `schedule` por:

```python
    path("<uuid:project_id>/", views.schedule, name="schedule"),
```

La plantilla enlaza a `projects:task` y `projects:export`; añade también rutas provisionales a `views.schedule` con esos nombres (`"<uuid:project_id>/tareas/<uuid:task_id>/"` y `"<uuid:project_id>/exportar.xlsx"`) para que el `{% url %}` resuelva; las Tasks 11 y 14 las reemplazan. Para que la ruta de tarea funcione con la vista provisional, dale a `schedule` el parámetro opcional `task_id: UUID | None = None` (sin usarlo) hasta la Task 11, que crea su propia vista.

En `test_admin_creates_project` (Task 9) ya puedes quitar `fetch_redirect_response=False`.

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/projects server/visitacampo/core/templates/suno/projects
git commit -m "feat(projects): schedule page with WBS, dual progress, delays, slack and filters"
```

---

### Task 11: Ficha de tarea y responsables

**Files:**
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Create: `server/visitacampo/core/templates/suno/projects/task.html`
- Modify: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Añadir las pruebas que fallan**

Al final de `test_views.py` (añade `TaskResponsible` y `AuditEvent` al import de `visitacampo.core.models`):

```python
@skip_without_db
class TaskPageTests(ViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_v1()
        self.task = self.project.tasks.get(uid=3)
        self.url = f"{BASE}{self.project.pk}/tareas/{self.task.pk}/"

    def test_shows_task_data(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        self.assertContains(response, "Descapote")
        self.assertContains(response, "Cuadrilla A")
        self.assertNotContains(response, 'name="user"')  # el observador no asigna

    def test_supervisor_assigns_and_removes_responsible(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(self.url, {"action": "add", "user": str(self.agent.pk)})
        self.assertRedirects(response, self.url)
        self.assertTrue(TaskResponsible.objects.filter(task=self.task, user=self.agent).exists())
        self.assertTrue(
            AuditEvent.objects.filter(action="projects.responsible_assigned").exists()
        )
        self.client.post(self.url, {"action": "remove", "user": str(self.agent.pk)})
        self.assertFalse(TaskResponsible.objects.filter(task=self.task).exists())

    def test_cannot_assign_user_from_another_org(self) -> None:
        other = make_org("Otra")
        outsider = make_user(other, username="otro")
        self.as_user(self.admin)
        response = self.client.post(self.url, {"action": "add", "user": str(outsider.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TaskResponsible.objects.exists())

    def test_viewer_cannot_assign(self) -> None:
        self.as_user(self.viewer)
        response = self.client.post(self.url, {"action": "add", "user": str(self.agent.pk)})
        self.assertEqual(response.status_code, 403)

    def test_task_of_other_project_is_404(self) -> None:
        other = Project.objects.create(org=self.org, name="Otra obra", created_by=self.admin)
        self.as_user(self.admin)
        url = f"{BASE}{other.pk}/tareas/{self.task.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: FAIL en `TaskPageTests`.

- [ ] **Step 3: Plantilla**

`server/visitacampo/core/templates/suno/projects/task.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}{{ task.name }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="" %}

<section class="pj-panel">
  <h2>{{ task.wbs }} {{ task.name }}{% if task.is_critical %} <span class="pj-badge">crítica</span>{% endif %}</h2>
  <table class="pj-table">
    <tbody>
      <tr><th>Etapa</th><td>{{ task.stage.name|default:"—" }}</td></tr>
      <tr><th>Inicio / fin</th><td>{{ task.start|date:"d/m/Y"|default:"—" }} → {{ task.finish|date:"d/m/Y"|default:"—" }}</td></tr>
      <tr><th>Línea base</th><td>{{ task.baseline_start|date:"d/m/Y"|default:"—" }} → {{ task.baseline_finish|date:"d/m/Y"|default:"—" }}</td></tr>
      <tr><th>% Project / % físico Project</th><td>{{ task.project_pct|floatformat:1|default:"—" }} / {{ task.project_physical_pct|floatformat:1|default:"—" }}</td></tr>
      <tr><th>Holgura total / libre (días)</th><td>{{ task.total_slack_days|floatformat:1|default:"—" }} / {{ task.free_slack_days|floatformat:1|default:"—" }}</td></tr>
      <tr><th>Ejecutores (Project)</th><td>{{ executors|join:", "|default:"—" }}</td></tr>
      <tr><th>Predecesoras</th><td>{% for link in predecessors %}{{ link.predecessor.wbs }} {{ link.predecessor.name }} ({{ link.type }}){% if not forloop.last %}, {% endif %}{% empty %}—{% endfor %}</td></tr>
    </tbody>
  </table>
</section>

<section class="pj-panel">
  <h2>Responsables</h2>
  <ul>
    {% for link in direct %}
      <li>{{ link.user.display_name|default:link.user.username }}
        {% if can_assign %}<form method="post" style="display:inline">{% csrf_token %}<input type="hidden" name="action" value="remove"><input type="hidden" name="user" value="{{ link.user.pk }}"><button type="submit" class="text-link">Quitar</button></form>{% endif %}
      </li>
    {% endfor %}
    {% for user in inherited %}<li>{{ user.display_name|default:user.username }} <small class="pj-muted">(por la rama superior)</small></li>{% endfor %}
    {% if not direct and not inherited %}<li class="pj-muted">Sin responsables.</li>{% endif %}
  </ul>
  {% if can_assign %}
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <input type="hidden" name="action" value="add">
    <label>Asignar a <select name="user">{% for member in members %}<option value="{{ member.pk }}">{{ member.display_name|default:member.username }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Asignar</button>
    {% if task.is_summary %}<small class="pj-muted">Asignar en una tarea resumen cubre toda su rama.</small>{% endif %}
  </form>
  {% if error %}<p class="pj-late">{{ error }}</p>{% endif %}
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 4: Vista**

En `views.py` añadir a los imports: `from django.contrib import messages`, `from django.db import IntegrityError, transaction`, `from django.http import Http404`, `from visitacampo.core.models import Membership, Task, TaskDependency, TaskResource, TaskResponsible` (junto a los existentes), `from visitacampo.core.policies import can_assign_task_responsibles`, y `from .access import effective_responsibles`. Luego:

```python
@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def task_detail(request: HttpRequest, project_id: UUID, task_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    try:
        task = (
            scoped_qs(ctx, Task).select_related("stage").get(pk=task_id, project=project)
        )
    except Task.DoesNotExist as exc:
        raise Http404 from exc
    can_assign = can_assign_task_responsibles(ctx, ctx.org_id)
    error = ""
    if request.method == "POST":
        if not can_assign:
            raise PermissionDenied
        user_id = _parse_uuid(request.POST.get("user"))
        is_member = (
            user_id is not None
            and scoped_qs(ctx, Membership).filter(user_id=user_id).exists()
        )
        if not is_member:
            error = "El usuario no pertenece a la organización."
        elif request.POST.get("action") == "remove":
            deleted, _ = (
                scoped_qs(ctx, TaskResponsible).filter(task=task, user_id=user_id).delete()
            )
            if deleted:
                audit.record(
                    ctx,
                    action=AuditAction.TASK_RESPONSIBLE_REMOVED,
                    target=f"task:{task.pk}",
                    metadata={"user": str(user_id)},
                )
            return redirect("projects:task", project.pk, task.pk)
        else:
            try:
                with transaction.atomic():
                    scoped_qs(ctx, TaskResponsible).create(
                        org_id=ctx.org_id, task=task, user_id=user_id, assigned_by_id=ctx.user_id
                    )
            except IntegrityError:
                messages.info(request, "Ya era responsable de esta tarea.")
            else:
                audit.record(
                    ctx,
                    action=AuditAction.TASK_RESPONSIBLE_ASSIGNED,
                    target=f"task:{task.pk}",
                    metadata={"user": str(user_id)},
                )
            return redirect("projects:task", project.pk, task.pk)

    direct = list(
        scoped_qs(ctx, TaskResponsible).filter(task=task).select_related("user")
    )
    direct_ids = {link.user_id for link in direct}
    inherited = [
        u for u in effective_responsibles(ctx, project).get(task.pk, []) if u.pk not in direct_ids
    ]
    members = [
        m.user
        for m in scoped_qs(ctx, Membership).select_related("user").order_by("user__username")
    ]
    return _render(
        request,
        ctx,
        "suno/projects/task.html",
        {
            **_project_context(ctx, project),
            "task": task,
            "direct": direct,
            "inherited": inherited,
            "members": members,
            "can_assign": can_assign,
            "error": error,
            "executors": list(
                scoped_qs(ctx, TaskResource)
                .filter(task=task)
                .values_list("resource__name", flat=True)
            ),
            "predecessors": scoped_qs(ctx, TaskDependency)
            .filter(successor=task)
            .select_related("predecessor"),
        },
    )
```

En `urls.py`, reemplazar la ruta provisional de tarea por:

```python
    path("<uuid:project_id>/tareas/<uuid:task_id>/", views.task_detail, name="task"),
```

y quitar el parámetro `task_id` provisional de `schedule`.

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py visitacampo/core/tests/test_architecture.py`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects server/visitacampo/core/templates/suno/projects/task.html
git commit -m "feat(projects): task page with responsible assignment (audited)"
```

---

### Task 12: Importar, vista previa e historial

**Files:**
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Create: `server/visitacampo/core/templates/suno/projects/import.html`, `import_preview.html`, `history.html`
- Modify: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Añadir las pruebas que fallan**

Al final de `test_views.py` (añade `from django.core.files.uploadedfile import SimpleUploadedFile` y `ScheduleImport` al import de modelos; `v2` al de `.samples`):

```python
@skip_without_db
class ImportPagesTests(ViewTestCase):
    def upload(self, data: bytes, name: str = "obra.xml"):
        return self.client.post(
            f"{BASE}{self.project.pk}/importar/",
            {"file": SimpleUploadedFile(name, data, content_type="text/xml")},
        )

    def test_upload_shows_preview_then_confirm_applies(self) -> None:
        self.as_user(self.admin)
        response = self.upload(v1())
        staged = ScheduleImport.objects.get(project=self.project)
        preview_url = f"{BASE}{self.project.pk}/importar/{staged.pk}/"
        self.assertRedirects(response, preview_url)
        preview = self.client.get(preview_url)
        self.assertContains(preview, "6 nuevas")
        response = self.client.post(preview_url, {"action": "confirm"})
        self.assertRedirects(response, f"{BASE}{self.project.pk}/")
        self.assertEqual(self.project.tasks.count(), 6)
        self.assertTrue(AuditEvent.objects.filter(action="projects.schedule_imported").exists())

    def test_discard(self) -> None:
        self.as_user(self.admin)
        self.upload(v1())
        staged = ScheduleImport.objects.get(project=self.project)
        self.client.post(f"{BASE}{self.project.pk}/importar/{staged.pk}/", {"action": "discard"})
        staged.refresh_from_db()
        self.assertEqual(staged.status, "discarded")
        self.assertEqual(self.project.tasks.count(), 0)

    def test_invalid_file_shows_error(self) -> None:
        self.as_user(self.admin)
        response = self.upload(b"<html/>")
        self.assertContains(response, "MSPDI")
        self.assertFalse(ScheduleImport.objects.exists())

    def test_supervisor_cannot_import(self) -> None:
        self.as_user(self.supervisor)
        self.assertEqual(self.client.get(f"{BASE}{self.project.pk}/importar/").status_code, 403)
        self.assertEqual(self.upload(v1()).status_code, 403)

    def test_history_lists_applied_imports_and_downloads_xml(self) -> None:
        self.import_v1()
        self.as_user(self.viewer)
        response = self.client.get(f"{BASE}{self.project.pk}/historial/")
        self.assertContains(response, "v1.xml")
        imp = ScheduleImport.objects.get(project=self.project)
        download = self.client.get(f"{BASE}{self.project.pk}/historial/{imp.pk}/xml")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, v1())
        self.assertIn("attachment", download["Content-Disposition"])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: FAIL en `ImportPagesTests`.

- [ ] **Step 3: Plantillas**

`server/visitacampo/core/templates/suno/projects/import.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Importar · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="import" %}
<section class="pj-panel">
  <h2>Importar cronograma</h2>
  <p>En MS Project: <strong>Archivo → Guardar como → XML</strong>. Máximo 50 MB. Antes de aplicar verás qué cambia.</p>
  <form method="post" enctype="multipart/form-data" class="pj-form">
    {% csrf_token %}
    <label>Archivo XML <input type="file" name="file" accept=".xml" required></label>
    {% if error %}<p class="pj-late">{{ error }}</p>{% endif %}
    <button class="button" type="submit">Ver cambios</button>
  </form>
</section>
{% endblock %}
```

`server/visitacampo/core/templates/suno/projects/import_preview.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Vista previa · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="import" %}
<section class="pj-panel">
  <h2>Cambios de {{ imp.file_name }}</h2>
  <p>{{ s.task_count }} tareas en el archivo · fecha de corte {{ imp.status_date|date:"d/m/Y"|default:"no indicada" }}</p>
  <ul>
    <li><strong>{{ s.added }} nuevas</strong>{% if s.examples.added %}: {{ s.examples.added|join:", " }}{% endif %}</li>
    <li><strong>{{ s.retired }} retiradas</strong>{% if s.examples.retired %}: {{ s.examples.retired|join:", " }}{% endif %}</li>
    <li><strong>{{ s.reactivated }} reactivadas</strong>{% if s.examples.reactivated %}: {{ s.examples.reactivated|join:", " }}{% endif %}</li>
    <li><strong>{{ s.modified }} modificadas</strong>{% if s.examples.modified %}: {{ s.examples.modified|join:", " }}{% endif %}</li>
  </ul>
  {% if s.baseline_changed %}<p class="pj-warning">Esta versión <strong>cambia la línea base</strong>: los atrasos se medirán contra la nueva.</p>{% endif %}
  {% for warning in s.warnings %}<p class="pj-warning">{{ warning }}</p>{% endfor %}
  {% if imp.status == "pending" %}
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <button class="button" type="submit" name="action" value="confirm">Aplicar esta versión</button>
    <button class="text-link" type="submit" name="action" value="discard">Descartar</button>
  </form>
  {% else %}
  <p class="pj-muted">Esta importación está {{ imp.get_status_display|lower }}.</p>
  {% endif %}
</section>
{% endblock %}
```

`server/visitacampo/core/templates/suno/projects/history.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Historial · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="history" %}
<section class="pj-panel">
  <table class="pj-table">
    <thead><tr><th>Versión</th><th>Archivo</th><th>Subido por</th><th>Aplicado</th><th>Corte</th><th class="num">Nuevas</th><th class="num">Retiradas</th><th class="num">Modificadas</th><th>Línea base</th><th></th></tr></thead>
    <tbody>
    {% for imp in imports %}
      <tr>
        <td>v{{ imp.number }}</td>
        <td>{{ imp.file_name }}</td>
        <td>{{ imp.uploaded_by.display_name|default:imp.uploaded_by.username }}</td>
        <td>{{ imp.applied_at|date:"d/m/Y H:i" }}</td>
        <td>{{ imp.status_date|date:"d/m/Y"|default:"—" }}</td>
        <td class="num">{{ imp.summary.added }}</td>
        <td class="num">{{ imp.summary.retired }}</td>
        <td class="num">{{ imp.summary.modified }}</td>
        <td>{% if imp.summary.baseline_changed %}<span class="pj-late">cambió</span>{% else %}igual{% endif %}</td>
        <td><a href="{% url 'projects:import_xml' project.pk imp.pk %}">XML</a></td>
      </tr>
    {% empty %}
      <tr><td colspan="10" class="pj-empty">Sin importaciones aplicadas.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 4: Vistas**

En `views.py` añadir a los imports `import gzip`, `from .importer import ImportRejected, apply_import, discard_import, stage_import`, `from .mspdi import MAX_BYTES`. Luego:

```python
def _require_manage(ctx: RequestContext) -> None:
    if not can_manage_projects(ctx, ctx.org_id):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    _require_manage(ctx)
    error = ""
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            error = "Elige un archivo XML."
        elif upload.size is not None and upload.size > MAX_BYTES:
            error = "El archivo supera 50 MB."
        else:
            try:
                staged = stage_import(
                    ctx, project, file_name=upload.name or "cronograma.xml", data=upload.read()
                )
            except ImportRejected as exc:
                error = str(exc)
            else:
                return redirect("projects:import_preview", project.pk, staged.pk)
    return _render(
        request,
        ctx,
        "suno/projects/import.html",
        {**_project_context(ctx, project), "error": error},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    _require_manage(ctx)
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(pk=import_id, project=project)
    except ScheduleImport.DoesNotExist as exc:
        raise Http404 from exc
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "confirm":
                applied = apply_import(ctx, imp.pk)
                audit.record(
                    ctx,
                    action=AuditAction.SCHEDULE_IMPORTED,
                    target=f"project:{project.pk}",
                    metadata={"version": applied.number, "file": applied.file_name},
                )
                messages.success(request, f"Versión {applied.number} aplicada.")
                return redirect("projects:schedule", project.pk)
            if action == "discard":
                discard_import(ctx, imp.pk)
                audit.record(
                    ctx,
                    action=AuditAction.SCHEDULE_IMPORT_DISCARDED,
                    target=f"project:{project.pk}",
                    metadata={"file": imp.file_name},
                )
                messages.info(request, "Importación descartada.")
                return redirect("projects:schedule", project.pk)
        except ImportRejected as exc:
            messages.error(request, str(exc))
            return redirect("projects:import_preview", project.pk, imp.pk)
    return _render(
        request,
        ctx,
        "suno/projects/import_preview.html",
        {**_project_context(ctx, project), "imp": imp, "s": imp.summary},
    )


@login_required(login_url="platform_login")
def history(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    imports = (
        scoped_qs(ctx, ScheduleImport)
        .filter(project=project, status=ScheduleImportStatus.APPLIED)
        .select_related("uploaded_by")
        .order_by("-number")
    )
    return _render(
        request,
        ctx,
        "suno/projects/history.html",
        {**_project_context(ctx, project), "imports": imports},
    )


@login_required(login_url="platform_login")
def import_xml(request: HttpRequest, project_id: UUID, import_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    try:
        imp = scoped_qs(ctx, ScheduleImport).get(
            pk=import_id, project=project, status=ScheduleImportStatus.APPLIED
        )
    except ScheduleImport.DoesNotExist as exc:
        raise Http404 from exc
    response = HttpResponse(gzip.decompress(bytes(imp.source_xml)), content_type="application/xml")
    response["Content-Disposition"] = f'attachment; filename="{project.code or "proyecto"}-v{imp.number}.xml"'
    return response
```

En `urls.py`, reemplazar las rutas provisionales de `history` e `import` y añadir:

```python
    path("<uuid:project_id>/importar/", views.import_upload, name="import"),
    path(
        "<uuid:project_id>/importar/<uuid:import_id>/",
        views.import_preview,
        name="import_preview",
    ),
    path("<uuid:project_id>/historial/", views.history, name="history"),
    path(
        "<uuid:project_id>/historial/<uuid:import_id>/xml",
        views.import_xml,
        name="import_xml",
    ),
```

> El nombre del archivo de descarga usa `project.code`, que escribe un administrador: limítalo a caracteres seguros con `re.sub(r"[^A-Za-z0-9_.-]", "_", project.code or "proyecto")` antes de ponerlo en la cabecera.

Revisa también `DATA_UPLOAD_MAX_MEMORY_SIZE`/`FILE_UPLOAD_MAX_MEMORY_SIZE` en `config/settings/base.py` y el `request_body` de Caddy (`infra/Caddyfile`): si alguno limita por debajo de 50 MB las subidas a `/plataforma/`, anótalo en el commit y en la spec en lugar de cambiar el límite global sin pedirlo.

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects server/visitacampo/core/templates/suno/projects
git commit -m "feat(projects): XML upload with change preview, confirm/discard, history"
```

---

### Task 13: Gantt

**Files:**
- Create: `server/visitacampo/core/static/suno/js/gantt.js`
- Create: `server/visitacampo/core/templates/suno/projects/gantt.html`
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Modify: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Añadir la prueba que falla**

```python
@skip_without_db
class GanttPageTests(ViewTestCase):
    def test_embeds_rows_links_and_cut(self) -> None:
        import json

        self.import_v1()
        self.as_user(self.viewer)
        response = self.client.get(f"{BASE}{self.project.pk}/gantt/")
        self.assertContains(response, 'id="gantt-data"')
        payload = json.loads(response.context["gantt_json"])
        self.assertEqual(payload["cut"], "2026-03-20")
        self.assertEqual(len(payload["rows"]), 6)
        columns = next(r for r in payload["rows"] if r["name"] == "Columnas")
        self.assertEqual(
            (columns["start"], columns["finish"], columns["bstart"], columns["bfinish"]),
            ("2026-03-18", "2026-03-30", "2026-03-16", "2026-03-26"),
        )
        self.assertTrue(columns["critical"])
        self.assertEqual(len(payload["links"]), 3)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py -k Gantt`
Expected: FAIL.

- [ ] **Step 3: Vista y plantilla**

En `views.py` (añade `import json` y `TaskDependency` si no está):

```python
@login_required(login_url="platform_login")
def gantt(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))

    def iso(value: date | None) -> str | None:
        return value.isoformat() if value else None

    rows = [
        {
            "id": str(row.task.pk),
            "wbs": row.task.wbs,
            "name": row.task.name,
            "level": row.task.outline_level,
            "start": iso(row.task.start),
            "finish": iso(row.task.finish),
            "bstart": iso(row.task.baseline_start),
            "bfinish": iso(row.task.baseline_finish),
            "summary": row.task.is_summary,
            "milestone": row.task.is_milestone,
            "critical": row.task.is_critical,
        }
        for row in data.rows
    ]
    ids = {r["id"] for r in rows}
    links = [
        {"from": str(a), "to": str(b), "type": t}
        for a, b, t in scoped_qs(ctx, TaskDependency)
        .filter(project=project)
        .values_list("predecessor_id", "successor_id", "type")
        if str(a) in ids and str(b) in ids
    ]
    payload = json.dumps({"cut": data.cut.isoformat(), "rows": rows, "links": links})
    return _render(
        request,
        ctx,
        "suno/projects/gantt.html",
        {**_project_context(ctx, project), "gantt_json": payload, "schedule": data},
    )
```

`server/visitacampo/core/templates/suno/projects/gantt.html`:

```django
{% extends "suno/projects/base.html" %}
{% load static %}
{% block title %}Gantt · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="gantt" %}
<section class="pj-panel">
  <form method="get" class="pj-filters">
    <label>Fecha de corte <input type="date" name="corte" value="{{ schedule.cut|date:'Y-m-d' }}"></label>
    <label>Escala <select data-gantt-scale><option value="8">Mes</option><option value="18" selected>Semana</option><option value="36">Día</option></select></label>
    <button class="button" type="submit">Aplicar</button>
    <span class="pj-muted">Rojo: ruta crítica · barra clara: línea base · línea punteada: fecha de corte</span>
  </form>
  <div class="pj-gantt" data-gantt></div>
</section>
<script type="application/json" id="gantt-data">{{ gantt_json|safe }}</script>
{% endblock %}
{% block scripts %}<script src="{% static 'suno/js/gantt.js' %}" defer></script>{% endblock %}
```

> `gantt_json` sale de `json.dumps` sobre datos del XML (nombres de tareas). `|safe` dentro de `<script>` permitiría cerrar la etiqueta con un nombre que contenga `</script>`. Por eso, en la vista, escapa antes de pasarlo: `payload = json.dumps(...).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")`. La prueba sigue funcionando porque `json.loads` decodifica esos escapes.

URL: reemplaza la ruta provisional por `path("<uuid:project_id>/gantt/", views.gantt, name="gantt"),`.

- [ ] **Step 4: El Gantt en SVG**

`server/visitacampo/core/static/suno/js/gantt.js`:

```javascript
// Gantt del cronograma: SVG propio, sin librerías. Lee #gantt-data
// ({cut, rows, links}) y dibuja una fila por tarea con su barra programada,
// la línea base debajo, los hitos como rombos, las dependencias fin→comienzo
// y la fecha de corte. Rojo = ruta crítica (dato del Project).
(() => {
  const host = document.querySelector('[data-gantt]');
  const source = document.querySelector('#gantt-data');
  if (!host || !source) return;
  const data = JSON.parse(source.textContent || '{}');
  const rows = data.rows || [];
  const scaleSelect = document.querySelector('[data-gantt-scale]');
  const NS = 'http://www.w3.org/2000/svg';
  const DAY = 86400000;
  const ROW = 26;
  const LABEL = 300;
  const HEAD = 30;

  const toTime = (iso) => (iso ? Date.parse(iso + 'T00:00:00Z') : null);
  const el = (tag, attrs, text) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text) node.textContent = text;
    return node;
  };

  function draw() {
    host.textContent = '';
    if (!rows.length) {
      host.textContent = 'Todavía no hay cronograma importado.';
      return;
    }
    const pxPerDay = Number(scaleSelect ? scaleSelect.value : 18);
    const times = rows.flatMap((r) => [r.start, r.finish, r.bstart, r.bfinish].map(toTime)).filter((t) => t !== null);
    const cut = toTime(data.cut);
    if (cut !== null) times.push(cut);
    const min = Math.min(...times) - 2 * DAY;
    const max = Math.max(...times) + 3 * DAY;
    const x = (t) => LABEL + ((t - min) / DAY) * pxPerDay;
    const width = x(max) + 20;
    const height = HEAD + rows.length * ROW + 10;
    const svg = el('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Diagrama de Gantt' });

    // Encabezado: una marca por lunes (o por día 1 en escala mensual).
    for (let t = min; t <= max; t += DAY) {
      const d = new Date(t);
      const monthly = pxPerDay < 12;
      if ((monthly && d.getUTCDate() === 1) || (!monthly && d.getUTCDay() === 1)) {
        svg.appendChild(el('line', { x1: x(t), x2: x(t), y1: HEAD - 6, y2: height, class: 'grid' }));
        const label = monthly ? d.toISOString().slice(0, 7) : d.toISOString().slice(5, 10);
        svg.appendChild(el('text', { x: x(t) + 3, y: HEAD - 10 }, label));
      }
    }

    const index = new Map(rows.map((r, i) => [r.id, i]));
    rows.forEach((r, i) => {
      const y = HEAD + i * ROW;
      const name = `${r.wbs} ${r.name}`;
      svg.appendChild(el('text', { x: 4 + (r.level - 1) * 12, y: y + 16, 'font-weight': r.summary ? 600 : 400 }, name.length > 44 ? name.slice(0, 43) + '…' : name));
      const bs = toTime(r.bstart);
      const bf = toTime(r.bfinish);
      if (bs !== null && bf !== null && !r.milestone) {
        svg.appendChild(el('rect', { x: x(bs), y: y + 17, width: Math.max(2, x(bf + DAY) - x(bs)), height: 4, class: 'baseline' }));
      }
      const s = toTime(r.start);
      const f = toTime(r.finish);
      if (s === null || f === null) return;
      const cls = `${r.summary ? 'summary' : ''} ${r.critical ? 'critical' : ''}`;
      if (r.milestone) {
        const cx = x(f);
        svg.appendChild(el('path', { d: `M${cx} ${y + 4} l7 7 l-7 7 l-7 -7 z`, class: `milestone ${cls}` }));
      } else {
        const bar = el('rect', { x: x(s), y: y + (r.summary ? 8 : 5), width: Math.max(2, x(f + DAY) - x(s)), height: r.summary ? 6 : 11, class: `bar ${cls}` });
        bar.appendChild(el('title', {}, `${name}\n${r.start} → ${r.finish}${r.bfinish ? `\nLínea base: ${r.bstart} → ${r.bfinish}` : ''}`));
        svg.appendChild(bar);
      }
    });

    (data.links || []).forEach((link) => {
      const a = index.get(link.from);
      const b = index.get(link.to);
      if (a === undefined || b === undefined) return;
      const from = rows[a];
      const to = rows[b];
      if (!from.finish || !to.start) return;
      const x1 = x(toTime(from.finish) + DAY);
      const y1 = HEAD + a * ROW + 10;
      const x2 = x(toTime(to.start));
      const y2 = HEAD + b * ROW + 10;
      const mid = Math.max(x1 + 6, x2 - 6);
      svg.appendChild(el('path', { d: `M${x1} ${y1} H${mid} V${y2} H${x2}`, class: 'link' }));
    });

    if (cut !== null) {
      svg.appendChild(el('line', { x1: x(cut + DAY), x2: x(cut + DAY), y1: HEAD - 6, y2: height, class: 'cut' }));
    }
    host.appendChild(svg);
  }

  scaleSelect?.addEventListener('change', draw);
  draw();
})();
```

- [ ] **Step 5: Correr las pruebas y revisar a mano**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: todo pasa.

El JavaScript no tiene prueba automática (el repo no tiene ejecutor de JS). Revísalo en el navegador en la Task 15, paso 3.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects server/visitacampo/core/templates/suno/projects/gantt.html server/visitacampo/core/static/suno/js/gantt.js
git commit -m "feat(projects): SVG Gantt with baseline, critical path, links and cut line"
```

---

### Task 14: Exportar a Excel

**Files:**
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/projects/urls.py`
- Modify: `server/visitacampo/projects/tests/test_views.py`

- [ ] **Step 1: Añadir la prueba que falla**

```python
@skip_without_db
class ExportTests(ViewTestCase):
    def test_supervisor_exports_xlsx_and_it_is_audited(self) -> None:
        import io
        import zipfile

        self.import_v1()
        self.as_user(self.supervisor)
        response = self.client.get(f"{BASE}{self.project.pk}/exportar.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Descapote", sheet)
        self.assertIn("pct_suno", sheet)
        self.assertTrue(AuditEvent.objects.filter(action="projects.schedule_exported").exists())

    def test_viewer_cannot_export(self) -> None:
        self.import_v1()
        self.as_user(self.viewer)
        self.assertEqual(
            self.client.get(f"{BASE}{self.project.pk}/exportar.xlsx").status_code, 403
        )
```

> Antes de escribir la aserción de `sheet1.xml`, abre `backoffice/export.py::to_xlsx` y confirma el nombre de la hoja y si los textos van en línea (`inlineStr`) o en `sharedStrings.xml`; ajusta la ruta leída para que la prueba busque donde de verdad quedan los textos.

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py -k Export`
Expected: FAIL.

- [ ] **Step 3: Implementar**

En `views.py` (añade `from visitacampo.backoffice.export import safe_cell, to_xlsx`):

```python
EXPORT_HEADER = [
    "edt", "tarea", "nivel", "etapa", "resumen", "hito", "critica",
    "ejecutores", "responsables", "inicio", "fin", "inicio_lb", "fin_lb",
    "pct_programado", "pct_suno", "pct_project",
    "atraso_pct_suno", "atraso_pct_project", "atraso_dias_suno", "atraso_dias_project",
    "holgura_total_dias", "holgura_libre_dias",
]


@login_required(login_url="platform_login")
def export_schedule(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_visible_project(ctx, project_id)
    if not can_export_schedule(ctx, ctx.org_id):
        raise PermissionDenied
    data = build_schedule(ctx, project, cut=_parse_date(request.GET.get("corte")))
    stage_names = {s.pk: s.name for s in data.stages}

    def row_values(row: ScheduleRow) -> list[object]:
        t, ind = row.task, row.indicators
        return [
            safe_cell(value)
            for value in (
                t.wbs, t.name, t.outline_level, stage_names.get(t.stage_id, ""),
                "sí" if t.is_summary else "no", "sí" if t.is_milestone else "no",
                "sí" if t.is_critical else "no",
                ", ".join(row.executors),
                ", ".join(u.display_name or u.username for u in row.responsibles),
                t.start, t.finish, t.baseline_start, t.baseline_finish,
                ind.planned_pct if ind else None,
                ind.suno.actual_pct if ind else None,
                ind.project.actual_pct if ind else None,
                ind.suno.delay_pct if ind else None,
                ind.project.delay_pct if ind else None,
                ind.suno.delay_days if ind else None,
                ind.project.delay_days if ind else None,
                t.total_slack_days, t.free_slack_days,
            )
        ]

    content = to_xlsx(EXPORT_HEADER, (row_values(r) for r in data.rows))
    audit.record(
        ctx,
        action=AuditAction.SCHEDULE_EXPORTED,
        target=f"project:{project.pk}",
        metadata={"cut": data.cut.isoformat(), "rows": len(data.rows)},
    )
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="cronograma-{data.cut.isoformat()}.xlsx"'
    )
    return response
```

`ScheduleRow` se importa de `.schedule`. Si `safe_cell` no acepta `date`/`Decimal` (revisa su implementación), convierte antes: fechas a `isoformat()`, `Decimal` a `float`.

URL: reemplaza la ruta provisional por `path("<uuid:project_id>/exportar.xlsx", views.export_schedule, name="export"),`.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_views.py`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects
git commit -m "feat(projects): export the schedule with indicators to Excel (audited)"
```

---

### Task 15: Portada, verificación completa y registro de avance

**Files:**
- Modify: `server/visitacampo/core/views.py`, `server/visitacampo/core/templates/suno/platform.html`
- Modify: `server/visitacampo/core/tests/test_platform_access.py`
- Modify: `plans/visitacampo-progreso.md`

- [ ] **Step 1: La portada muestra los proyectos reales**

Prueba nueva en `test_platform_access.py` (añade `Project` al import de modelos):

```python
    def test_home_lists_visible_projects(self) -> None:
        admin = make_user(self.org, username="admin-home", role=Role.ADMIN)
        Project.objects.create(org=self.org, name="Obra Portada", created_by=admin)
        self.client.force_login(admin)
        response = self.client.get("/plataforma/")
        self.assertContains(response, "Obra Portada")
        self.assertContains(response, "/plataforma/proyectos/")
```

(importa `Role` de `visitacampo.core.models` si no está). En `core/views.py::platform_home` añade al contexto:

```python
        "projects": list(visible_projects(request_context).order_by("name")[:6]),
```

con `from visitacampo.projects.access import visible_projects`. En `platform.html`, reemplaza la fila vacía del módulo de proyectos:

```django
              <div class="empty-row" role="row"><div><strong>Sin proyectos configurados</strong><p>El modelo de proyectos todavía no forma parte de este entorno. Los registros y el mapa muestran únicamente información existente.</p></div></div>
```

por:

```django
              {% for project in projects %}
              <div class="activity-row" role="row"><span><a href="{% url 'projects:schedule' project.pk %}"><strong>{{ project.name }}</strong></a></span><span>{% if project.status_date %}Corte {{ project.status_date|date:"d/m/Y" }}{% else %}Sin cronograma{% endif %}</span><span></span><span><a class="text-link" href="{% url 'projects:schedule' project.pk %}">Abrir</a></span></div>
              {% empty %}
              <div class="empty-row" role="row"><div><strong>Sin proyectos</strong><p>Crea un proyecto e importa su cronograma de MS Project en <a href="{% url 'projects:list' %}">Proyectos</a>.</p></div></div>
              {% endfor %}
```

Run: `infra/scripts/test_docker.sh visitacampo/core/tests/test_platform_access.py`
Expected: todo pasa.

- [ ] **Step 2: Verificación completa**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check visitacampo/projects"
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pytest -q -p no:cacheprovider --cov --cov-config=pyproject.toml --cov-report=term"
```

Expected: suite completa en verde, ruff y mypy limpios, sin migraciones pendientes, cobertura ≥ 90 % (el suelo de `pyproject.toml`). Si `ruff format --check` no forma parte de la CI (revisa `.github/workflows/backend.yml`), omítelo.

- [ ] **Step 3: Prueba manual en el navegador**

Levanta el stack de desarrollo (ver `infra/README.md`), crea un proyecto con un administrador, importa el XML de `samples.v1()` (guárdalo con `infra/scripts/test_docker.sh --cmd "python -c 'from visitacampo.projects.tests.samples import v1; open(\"/app/v1.xml\",\"wb\").write(v1())'"` y bórralo después) y revisa: tabla del cronograma, filtros, ficha de tarea, Gantt (barras, línea base, rojo crítico, flechas, corte), importar `v2` y ver la vista previa con la advertencia de línea base, historial, descarga de XML y Excel. Al terminar: `docker compose … down` e `infra/scripts/test_docker.sh --down`.

- [ ] **Step 4: Registrar el avance**

En `plans/visitacampo-progreso.md`, añade una sección "Módulo de costos — Etapa 1 (Cronograma), servidor" con: fecha, PR, qué entra (lista de las Tasks 1–14), resultado de la verificación del paso 2 (números reales), las dos desviaciones de la spec, y "Pendiente: avance desde campo (plan `2026-10-31-costos-etapa1-campo-plan.md`)".

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/core plans/visitacampo-progreso.md
git commit -m "feat(projects): list projects on the platform home; record stage 1 server progress"
```

# Costos — Etapa 1 (Cronograma), avance desde campo: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el agente vincule una visita a una tarea del cronograma y reporte su % de avance acumulado desde la app (sin conexión, por el outbox), que el servidor lo valide (organización, tarea vigente de detalle, responsable efectivo, 0–100), que la visita **validada** más reciente alimente el **% SUNO** del cronograma, y que el supervisor vea marcado un % menor que el último validado.

**Architecture:**
- **Servidor (PR 1, compatible hacia atrás):** dos columnas nuevas en `Visit` (`task`, `progress_pct`) con `CHECK` de par; viajan como campos opcionales/nulables del contrato de sync (`VisitPayload`, `Visit`). La validación de forma vive en `sync/payloads.py` (pura) y la de referencias en `sync/services.py` (en bloque, antes del bucle). La lógica de avance de campo vive en `projects/field_progress.py` (último % validado por tarea, "menor que el último validado"), la cobertura de responsables en `projects/access.py`, y la lista de tareas para el teléfono en `projects/field_tasks.py` + `projects/api.py` (`GET /v1/projects/tasks`). `build_schedule` calcula el % SUNO por defecto, así que lo reciben todas sus llamadas (lista, cronograma, Gantt, Excel).
- **App (PR 2):** tabla Drift `schedule_tasks` (+ estado de versión) en `schemaVersion` 9, sincronizada tras cada pull por `ScheduleTaskSync` (mismo ciclo que las capas: lista completa que reemplaza la local); `Visits` gana `taskId`/`progressPct`; la pantalla de captura muestra "Tarea del cronograma" (búsqueda por EDT o nombre) y "% avance acumulado" con el último validado como referencia; todo se guarda offline y viaja por el outbox.

**Tech Stack:** Django 5.2 + Django Ninja + Pydantic v2, PostgreSQL 16; Flutter, Riverpod, Drift (SQLCipher), Dio.

**Spec:** `plans/2026-10-30-costos-etapa1-cronograma-design.md` (§1 modelo, §5 app móvil y API). Plan anterior (servidor del cronograma): `plans/2026-10-31-costos-etapa1-servidor-plan.md`.

**Repositorios y PRs:**
1. `SUNO_server`, rama `feat/costos-campo-servidor` → Parte A. Se despliega primero.
2. `SUNO_client`, rama `feat/costos-campo-app` → Parte B. Empieza actualizando su copia fijada del contrato (`contract/`) al commit de merge del PR 1.

---

## Riesgos y decisiones tomadas al planear

Ninguno de estos puntos reabre las decisiones 1–7 del encargo; precisan cómo se cumplen. Los que cambian la spec se anotan en ella en la Task A0.

1. **Descarga de tareas: endpoint propio con lista completa + `version`, no una entidad del pull por `change_seq`.** El código tiene dos patrones para bajar datos al teléfono:
   - *Pull por `change_seq`* (`sync/pull.py`): solo para subclases de `SyncedModel` (`visit`, `visit_attachment`, `assignment`, `form_template_version`), con **un cursor por organización**, `change_seq` asignado bajo `pg_advisory_xact_lock` y autorización **de organización** (`check_read_visit`: todo miembro recibe todo).
   - *Lista completa y reconciliación en el cliente* (`/v1/layers`, `/v1/basemaps` → `LayerManager.refresh`, `BasemapManager`): el material de referencia de campo. Lo retirado deja de venir y el teléfono lo borra.

   Las tareas encajan en el segundo, por tres motivos que el primero no puede resolver sin rehacer el protocolo: (a) la visibilidad es **por usuario y derivada** (responsable directo o por rama): quitar un responsable en una tarea resumen cambia la lista de un usuario sin que cambie ninguna fila de `Task`, y el pull no tiene tombstones por usuario; (b) el "último % validado" sale de `VisitReview`, que no es sincronizada y no tiene `change_seq`; (c) hacer `Task` sincronizable obligaría a cada importación (miles de filas) a tomar el lock del push y a repartir `change_seq`, y el pull entregaría todo el cronograma a todos los miembros. Para que sea **incremental** en lo que importa (no repetir la descarga), la respuesta lleva `version` = SHA-256 del contenido; el cliente la reenvía y, si no cambió, recibe `changed: false` sin ítems. Consecuencia buscada: **no hay cursor que mover** en el importador, en la asignación de responsables ni en la revisión de visitas: cualquier cambio que altere la lista cambia la huella sola. La spec decía "incremental con `change_seq`, como capas y formularios"; las capas no usan `change_seq`. Se anota en la spec.
2. **Tamaño de la lista.** Un agente responsable de una etapa grande (≈5.000 hojas) recibiría ≈1 MB de JSON (≈100 KB con el `encode zstd gzip` de `infra/Caddyfile`), solo cuando la lista cambia. Si llegara a pesar, el siguiente paso es paginar o un `since`; no hace falta en la etapa 1.
3. **Responsable efectivo por número de esquema.** Para no recorrer la cadena de `parent` en el push ni en el endpoint, se usa que el importador deriva `parent` de `outline_number` (`importer._apply_tasks`): ser responsable de "1.2" cubre "1.2" y todo "1.2.*" (no "1.20"). Solo cuentan responsabilidades sobre tareas **vigentes**. Una prueba fija la equivalencia con `effective_responsibles` (Task A4).
4. **Solo tareas de detalle.** El % SUNO se calcula en hojas y se consolida hacia arriba (`progress.compute_indicators` ignora el valor de una resumen). La app solo ofrece tareas vigentes no resumen y el push rechaza una resumen con `task_is_summary`.
5. **Códigos de rechazo sin ampliar `ErrorCode`.** Se usan los existentes con `details.reason` estable: `not_found`/`unknown_reference` (tarea de otra organización o inexistente), `validation_error`/`task_retired`, `validation_error`/`task_is_summary`, `forbidden`/`not_responsible`, `validation_error`/`task_progress_pair` (falta uno de los dos campos) y los motivos de Pydantic para el rango (`greater_than_equal`, `less_than_equal`, `decimal_max_places`…). Todos con `field` = `task_id` o `progress_pct`.
6. **El par viaja junto también en `update`.** `task_id` y `progress_pct` se envían los dos (con valor o los dos `null`) o ninguno. Se exige en `payloads.py`: si llegara a la BD, el `CHECK` daría un `IntegrityError` que tumba el lote entero (500), justo lo que `payloads.py` existe para impedir.
7. **Quién puede vincular.** Regla estricta del encargo: quien **pone** una tarea tiene que ser responsable efectivo, sea agente o supervisor. **Desvincular** (`null`/`null`) no exige responsabilidad: basta con poder modificar la visita (`can_modify_visit`, ya aplicado).
8. **"Menor que el último validado" se calcula al leer, no se guarda.** Referencia: el `progress_pct` de la visita validada más reciente de la misma tarea **capturada antes** que esta. Motivo: la validación llega después del push, y rechazar más tarde la visita de referencia debe cambiar el aviso. Se muestra en el Admin de visitas (columna, filtro y detalle) y en la ficha de la tarea. El push acepta el valor sin más.
9. **Compatibilidad.** Un cliente anterior no envía los campos (son opcionales en el push) e ignora las claves nuevas de las filas del pull (`_upsertVisitFromServerRow` lee claves sueltas). El cliente nuevo omite los dos campos cuando no hay tarea, por si hablara con un servidor anterior (`extra="forbid"` los rechazaría). La migración Drift v9 reinicia el cursor del pull (como la v8) para que las visitas ya bajadas recojan su tarea.
10. **Ventana offline.** Una visita capturada sin red puede llegar cuando su tarea ya fue retirada o al agente le quitaron la responsabilidad → `rejected` → cuarentena. La pantalla "Para revisar" gana la acción **"Quitar la tarea y reenviar"** (Task B8) para no perder la visita.
11. **Precisión.** `numeric(5,2)`. El cliente acepta hasta dos decimales (coma o punto); el servidor convierte el número JSON con `Decimal(str(valor))`.
12. **Superusuario en el Admin.** La subconsulta del "último validado" parte del queryset del propio Admin (todas las organizaciones para el operador) correlacionada por `task_id`; como una tarea pertenece a una sola organización, no mezcla datos.
13. **Ruta.** `GET /v1/projects/tasks` sin barra final, como el resto de `/v1/*` (la spec decía `/v1/projects/tasks/`).
14. **Fuera de alcance:** columnas de tarea/% en las exportaciones de visitas, mostrar la tarea en el detalle de visita del teléfono, FK compuesta `(task_id, org_id)` (el aislamiento lo da la validación del push con `scoped_qs`, igual que `assignment_id`).
15. **Una visita validada no cambia su avance sin una nueva revisión (decisión tomada en revisión).** Un `update` que cambia `task_id` o `progress_pct` (valor distinto, vincular o desvincular) sobre una visita cuya última `VisitReview` es `approved` se rechaza con `validation_error` y `details = {"field": "task_id" | "progress_pct", "reason": "visit_validated"}`. Reenviar los valores guardados se acepta; una visita rechazada o sin revisión sí puede cambiar. Se resuelve en bloque con una query por lote (como `_task_refs`). Sin esto, el agente podía alterar un % ya validado y la supervisión no lo veía.

---

## Convenciones del repositorio que hay que respetar

**Servidor**
- Tests con `unittest`/`django.test`; los que tocan BD usan `visitacampo/core/tests/dbsupport.py`:
  ```python
  setUpModule = setup_test_database
  tearDownModule = teardown_test_database
  skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
  ```
- Fábricas: `visitacampo.authn.tests.factories.make_org/make_user/login`, `visitacampo.sync.tests.factories.build_scenario/create_op/update_op/push/pull/visit_of`, `visitacampo.backoffice.tests.factories.admin_client`.
- Ningún módulo de producción usa `Model.objects` (lo vigila `core/tests/test_architecture.py`): `scoped_qs(ctx, Model)` o un queryset recibido. Los tests sí pueden.
- Toda política nueva `can_*` se registra en `core/tests/test_policies.py::NoRoleGrantsNothingTests._registry`.
- Comentarios y textos de interfaz en español; código en inglés. Commits en inglés, estilo conventional.

**App**
- El código generado (`*.g.dart`, `*.freezed.dart`, `lib/l10n/app_localizations*.dart`) **no se versiona**: `dart run build_runner build --delete-conflicting-outputs` y `flutter gen-l10n` tras tocar tablas, modelos o `.arb`.
- Pantallas sin textos, colores ni medidas sueltas (`tool/lint_ui_tokens.sh`): textos en `lib/l10n/app_es.arb`, medidas en `AppSpacing`/`AppGap`.
- Toda migración Drift con su prueba N-1→N en `test/data/local/migration_test.dart`, con el esquema anterior escrito a mano.
- `contract/` es copia fijada del servidor: no se edita a mano.

## Cómo correr las pruebas

**Servidor** (sin Python local; Docker Desktop arrancado), desde la raíz de `SUNO_server`:

```bash
infra/scripts/test_docker.sh visitacampo/projects                 # un paquete
infra/scripts/test_docker.sh visitacampo/sync/tests/test_push_tasks.py
infra/scripts/test_docker.sh                                      # suite completa
infra/scripts/test_docker.sh --cmd "ruff check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                               # al terminar
```

Validar el contrato (el validador no está en `requirements-dev.lock`; contenedor desechable):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/server/openapi:/o" python:3.12-slim \
  sh -c "pip install -q openapi-spec-validator && python -m openapi_spec_validator /o/v1.yaml"
```

**App**, desde la raíz de `SUNO_client`:

```bash
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
flutter test test/core/schedule/                  # una carpeta
flutter test --concurrency=1                      # suite completa (como CI)
dart analyze
bash tool/lint_ui_tokens.sh
bash tool/e2e.sh                                  # E2E en el emulador (Task B9)
```

## Mapa de archivos

**Parte A — servidor (`SUNO_server`)**

| Archivo | Responsabilidad |
|---|---|
| `plans/2026-10-30-costos-etapa1-cronograma-design.md` | Notas de las decisiones de este plan |
| `server/visitacampo/core/models/visit.py` | `Visit.task`, `Visit.progress_pct`, `CHECK` e índice |
| `server/visitacampo/core/migrations/0012_add_visit_task_progress.py` | Generada |
| `server/openapi/v1.yaml` | `Visit`/`VisitPayload` + `GET /v1/projects/tasks` |
| `server/visitacampo/sync/payloads.py` | Forma y par de `task_id`/`progress_pct` |
| `server/visitacampo/sync/sql.py` | Columnas nuevas en el INSERT/UPDATE |
| `server/visitacampo/sync/records.py`, `sync/schemas.py` | Campos nuevos en `VisitRecord` |
| `server/visitacampo/projects/access.py` | `covers`, `responsible_outlines` |
| `server/visitacampo/sync/services.py` | Validación de la tarea en el push |
| `server/visitacampo/projects/field_progress.py` | Último % validado, "menor que el último validado" (nuevo) |
| `server/visitacampo/projects/schedule.py` | % SUNO por defecto en `build_schedule` |
| `server/visitacampo/core/policies.py` | `can_read_field_tasks` |
| `server/visitacampo/projects/field_tasks.py`, `schemas.py`, `api.py` | `GET /v1/projects/tasks` (nuevos) |
| `server/config/api.py` | Monta el router |
| `server/visitacampo/projects/views.py`, `core/templates/suno/projects/task.html` | Visitas vinculadas en la ficha de tarea |
| `server/visitacampo/core/admin.py` | Avance en el Admin de visitas |
| `server/visitacampo/projects/importer.py` | Aviso de retiradas con avance de campo |
| `server/visitacampo/management/commands/bootstrap_demo.py` | `--with-schedule` para la E2E |
| `docs/testing/e2e-fase0.md` | Procedimiento E2E |
| `server/visitacampo/projects/tests/factories.py` | Datos de prueba del avance de campo (nuevo) |
| `server/visitacampo/*/tests/…` | Pruebas |

**Parte B — app (`SUNO_client`)**

| Archivo | Responsabilidad |
|---|---|
| `contract/v1.yaml`, `contract/SOURCE` | Copia del contrato al commit del PR 1 |
| `lib/data/local/tables.dart`, `lib/data/local/database.dart` | `ScheduleTasks`, `ScheduleTaskState`, columnas de `Visits`, migración v9 |
| `lib/core/schedule/schedule_task_sync.dart` | Modelo, transporte y sincronización de tareas (nuevo) |
| `lib/core/schedule/progress_input.dart` | Leer y mostrar el % (puro, nuevo) |
| `lib/data/http/schedule_task_api_client.dart` | Cliente Dio de `/v1/projects/tasks` (nuevo) |
| `lib/data/local/schedule_task_repository.dart` | Lectura y búsqueda local (nuevo) |
| `lib/core/sync/sync_engine.dart` | Guarda tarea y % de las filas del servidor |
| `lib/core/sync/quarantine_service.dart` | `unlinkTask`, par en `resendMine` |
| `lib/core/sync/sync_coordinator.dart` | Refresca tareas en cada ciclo |
| `lib/core/app_dependencies.dart` | Composición |
| `lib/core/session/device_handover.dart` | Borra tareas al cambiar de organización |
| `lib/core/capture/visit_capture.dart` | `TaskProgress` en alta y edición |
| `lib/features/capture/task_progress_section.dart` | Selector y campo de % (nuevo) |
| `lib/features/capture/capture_screen.dart` | Integra la sección |
| `lib/features/review/review_screen.dart`, `lib/ui/forms/dynamic_form.dart` | Acción "Quitar la tarea", motivos nuevos |
| `lib/l10n/app_es.arb` | Textos |
| `integration_test/e2e_fase0_test.dart`, `tool/e2e.sh` | Paso E2E |
| `test/…` | Pruebas |

---

# Parte A — Servidor (`SUNO_server`)

### Task A0: Rama y notas en la spec

**Files:**
- Modify: `plans/2026-10-30-costos-etapa1-cronograma-design.md`

- [ ] **Step 1: Rama**

```bash
git checkout master && git pull && git checkout -b feat/costos-campo-servidor
```

- [ ] **Step 2: Anotar las decisiones en la spec**

En "## 5. App móvil y API", reemplazar:

```markdown
- `GET /v1/projects/tasks/`: tareas vigentes donde el usuario es responsable (directo o
  por rama), con EDT, nombre, proyecto y último % validado. Sincronización incremental con
  `change_seq`, como capas y formularios.
- El push de visitas acepta `task_id` y `progress_pct`. El servidor valida organización,
  que el usuario sea responsable de la tarea y el rango 0–100. Un % menor que el último
  validado se acepta y se marca para el supervisor.
```

por:

```markdown
- `GET /v1/projects/tasks`: tareas vigentes **de detalle** donde el usuario es responsable
  (directo o por rama), con EDT, nombre, proyecto y último % validado. *Decidido al planear
  (`plans/2026-10-31-costos-etapa1-campo-plan.md`):* lista completa con una huella `version`
  (si no cambió, `changed: false` sin ítems), como las capas de referencia, en vez de una
  entidad del pull por `change_seq`: la visibilidad es por usuario y derivada de la rama, y el
  último % validado sale de `VisitReview`, que no tiene `change_seq`. Lo que deja de venir se
  borra del teléfono.
- El push de visitas acepta `task_id` y `progress_pct` (los dos o ninguno, también en
  `update`). El servidor valida organización, que la tarea esté vigente y no sea resumen, que
  el usuario sea responsable de ella y el rango 0–100. Un % menor que el último validado se
  acepta; se marca al leer (Admin de visitas y ficha de la tarea), comparando con la visita
  validada más reciente de la misma tarea capturada antes.
```

- [ ] **Step 3: Commit**

```bash
git add plans/2026-10-30-costos-etapa1-cronograma-design.md
git commit -m "docs(projects): note field progress planning decisions in stage 1 spec"
```

---

### Task A1: `Visit.task` y `Visit.progress_pct` con su restricción

**Files:**
- Modify: `server/visitacampo/core/models/visit.py`
- Create (generada): `server/visitacampo/core/migrations/0012_add_visit_task_progress.py`
- Create: `server/visitacampo/projects/tests/factories.py`
- Test: `server/visitacampo/projects/tests/test_field_progress.py`

- [ ] **Step 1: Fábricas del avance de campo**

`server/visitacampo/projects/tests/factories.py`:

```python
"""Datos de prueba del avance de campo: un proyecto con el cronograma `v1`
importado, visitas vinculadas a tareas y sus revisiones.

Los tests sí pueden usar `Model.objects` (el test de arquitectura excluye
`tests/`).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, RequestContext, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    Device,
    FormTemplate,
    FormTemplateVersion,
    Organization,
    Project,
    Role,
    Task,
    TaskResponsible,
    Visit,
    VisitReview,
    VisitReviewStatus,
)
from visitacampo.projects.importer import apply_import, stage_import

from .samples import v1

_seq = itertools.count(1000)

#: Instante de referencia: las visitas se ordenan por `captured_at`.
T0 = datetime(2026, 3, 10, 9, tzinfo=UTC)


def make_visit(
    org: Organization,
    author: AppUser,
    *,
    task: Task | None = None,
    pct: str | None = None,
    at: datetime | None = None,
    deleted: bool = False,
) -> Visit:
    """Una visita mínima, con o sin tarea. `pct` como texto (`"35.5"`)."""
    template, _ = FormTemplate.objects.get_or_create(org=org, name="Avance de obra")
    version, _ = FormTemplateVersion.objects.get_or_create(
        org=org,
        template=template,
        version_number=1,
        defaults={"definition": {"fields": []}, "change_seq": next(_seq)},
    )
    device, _ = Device.objects.get_or_create(
        org=org,
        owner=author,
        defaults={"id": uuid.uuid4(), "platform": "android", "app_version": "0.11.0"},
    )
    moment = at or timezone.now()
    return Visit.objects.create(
        id=uuid.uuid4(),
        org=org,
        change_seq=next(_seq),
        template_version=version,
        answers={},
        captured_at=moment,
        device=device,
        created_by=author,
        task=task,
        progress_pct=None if pct is None else Decimal(pct),
        deleted_at=moment if deleted else None,
    )


@dataclass
class FieldWorld:
    org: Organization
    admin: AppUser
    supervisor: AppUser
    agent: AppUser
    project: Project

    def ctx(self, user: AppUser | None = None) -> RequestContext:
        who = user or self.admin
        return resolve_request_context(Principal(user_id=who.pk, org_id=who.org_id))

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def responsible(self, uid: int, user: AppUser | None = None) -> TaskResponsible:
        return TaskResponsible.objects.create(
            org=self.org, task=self.task(uid), user=user or self.agent, assigned_by=self.admin
        )

    def visit(
        self,
        uid: int,
        pct: str,
        *,
        hours: int = 0,
        author: AppUser | None = None,
        deleted: bool = False,
    ) -> Visit:
        return make_visit(
            self.org,
            author or self.agent,
            task=self.task(uid),
            pct=pct,
            at=T0 + timedelta(hours=hours),
            deleted=deleted,
        )

    def review(self, visit: Visit, status: str) -> VisitReview:
        return VisitReview.objects.create(
            org=self.org, visit=visit, status=status, reviewer=self.supervisor
        )

    def approve(self, visit: Visit) -> VisitReview:
        return self.review(visit, VisitReviewStatus.APPROVED)

    def reject(self, visit: Visit) -> VisitReview:
        return self.review(visit, VisitReviewStatus.REJECTED)


def make_field_world(name: str = "Constructora") -> FieldWorld:
    """Organización con administración, supervisión y un agente, y el cronograma
    `samples.v1()` aplicado: 1 Preliminares (resumen) > 1.1 Localización (uid 2),
    1.2 Descapote (uid 3); 2 Estructura (resumen, uid 4) > 2.1 Columnas (uid 5),
    2.2 Fin de obra (hito, uid 6)."""
    org = make_org(name)
    slug = name.lower().replace(" ", "-")
    admin = make_user(org, username=f"admin-{slug}", role=Role.ADMIN)
    supervisor = make_user(org, username=f"super-{slug}", role=Role.SUPERVISOR)
    agent = make_user(org, username=f"agente-{slug}", role=Role.FIELD_AGENT)
    project = Project.objects.create(org=org, name=f"Obra {name}", created_by=admin)
    ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
    apply_import(ctx, stage_import(ctx, project, file_name="v1.xml", data=v1()).pk)
    project.refresh_from_db()
    return FieldWorld(org=org, admin=admin, supervisor=supervisor, agent=agent, project=project)
```

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/projects/tests/test_field_progress.py`:

```python
"""Avance de campo: restricciones de `Visit` y el % validado de cada tarea."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import make_field_world, make_visit

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class VisitTaskProgressConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def test_a_visit_without_task_has_no_progress(self) -> None:
        make_visit(self.world.org, self.world.agent)  # sin tarea ni %: válida
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, pct="10")

    def test_a_linked_visit_needs_its_progress(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_visit(self.world.org, self.world.agent, task=self.world.task(3))

    def test_progress_is_between_0_and_100(self) -> None:
        self.world.visit(3, "0")
        self.world.visit(3, "100")
        for pct in ("-0.01", "100.01"):
            with self.subTest(pct=pct), self.assertRaises(IntegrityError), transaction.atomic():
                self.world.visit(3, pct)
```

- [ ] **Step 3: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: FAIL — `TypeError: Visit() got unexpected keyword arguments: 'task', 'progress_pct'`.

- [ ] **Step 4: Añadir los campos**

En `server/visitacampo/core/models/visit.py`, dentro de `class Visit`, después del campo `created_by` (antes de `class Meta:`):

```python
    #: Costos, etapa 1: la tarea del cronograma cuyo avance reporta la visita y
    #: el % acumulado observado en campo. Los dos o ninguno
    #: (`visit_task_progress_pair`). Cuenta para el % SUNO de la tarea cuando
    #: la revisión vigente de la visita es `approved` (`projects.field_progress`).
    #: `PROTECT`: las tareas nunca se borran (se retiran), y así sigue siendo.
    task = models.ForeignKey(
        "core.Task",
        on_delete=models.PROTECT,
        related_name="field_visits",
        null=True,
        blank=True,
        default=None,
        verbose_name="tarea del cronograma",
    )
    progress_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
        verbose_name="% avance acumulado",
    )
```

En `Visit.Meta.constraints`, al final de la lista (después de `visit_id_org_uq`):

```python
            # Costos, etapa 1: la tarea y su % van juntos, y el % en 0..100.
            # `payloads.py` lo rechaza antes con detalle; esto es la red.
            models.CheckConstraint(
                condition=(models.Q(task__isnull=True) & models.Q(progress_pct__isnull=True))
                | (
                    models.Q(task__isnull=False)
                    & models.Q(progress_pct__gte=0)
                    & models.Q(progress_pct__lte=100)
                ),
                name="visit_task_progress_pair",
            ),
```

En `Visit.Meta.indexes`, al final:

```python
            # % SUNO: la visita más reciente de cada tarea (`field_progress`).
            models.Index(fields=["task", "captured_at"], name="visit_task_captured_idx"),
```

- [ ] **Step 5: Generar la migración**

Run: `infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name add_visit_task_progress"`
Expected: `server/visitacampo/core/migrations/0012_add_visit_task_progress.py` con `AddField` ×2, `AddConstraint` y `AddIndex`. Revisarla: no debe tocar nada más.

- [ ] **Step 6: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/core/models/visit.py server/visitacampo/core/migrations/0012_add_visit_task_progress.py server/visitacampo/projects/tests/factories.py server/visitacampo/projects/tests/test_field_progress.py
git commit -m "feat(projects): link visits to schedule tasks with accumulated progress"
```

---

### Task A2: Contrato `v1.yaml`

**Files:**
- Modify: `server/openapi/v1.yaml`

- [ ] **Step 1: Etiqueta**

En `tags:`, después de la entrada `layers`:

```yaml
  - name: projects
    description: Cronograma de proyectos (costos, etapa 1) — avance desde campo.
```

- [ ] **Step 2: `Visit`**

En `components.schemas.Visit.required`, reemplazar:

```yaml
        - device_id
        - created_by
      properties:
        id:
          $ref: '#/components/schemas/Uuid'
        org_id:
```

por:

```yaml
        - device_id
        - created_by
        - task_id
        - progress_pct
      properties:
        id:
          $ref: '#/components/schemas/Uuid'
        org_id:
```

(Si el ancla no es única, aplicar el cambio dentro de `Visit:` — es la única que lista `created_by` justo antes de `properties`.)

Y al final de `Visit.properties`, reemplazar:

```yaml
        created_by:
          $ref: '#/components/schemas/Uuid'

    VisitAttachment:
```

por:

```yaml
        created_by:
          $ref: '#/components/schemas/Uuid'
        task_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'
          description: |
            Tarea del cronograma (costos, etapa 1) cuyo avance reporta la visita. Va siempre con
            `progress_pct`: los dos con valor o los dos `null`.
        progress_pct:
          type: [number, 'null']
          minimum: 0
          maximum: 100
          description: '% de avance acumulado de `task_id` observado en campo, hasta dos decimales.'

    VisitAttachment:
```

- [ ] **Step 3: `VisitPayload`**

En `components.schemas.VisitPayload.properties`, reemplazar:

```yaml
        captured_at:
          $ref: '#/components/schemas/Timestamp'
        device_id:
          $ref: '#/components/schemas/Uuid'

    VisitAttachmentPayload:
```

por:

```yaml
        captured_at:
          $ref: '#/components/schemas/Timestamp'
        device_id:
          $ref: '#/components/schemas/Uuid'
        task_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'
          description: |
            Opcional (un cliente anterior no lo envía). Tarea **vigente y de detalle** de la que quien
            envía es responsable (directo o por una tarea superior). Viaja **siempre junto** con
            `progress_pct`, también en `update`: los dos con valor, los dos `null` (desvincular) o
            ninguno. Rechazos: `not_found` (`unknown_reference`), `validation_error`
            (`task_retired`, `task_is_summary`, `task_progress_pair`), `forbidden` (`not_responsible`).
        progress_pct:
          type: [number, 'null']
          minimum: 0
          maximum: 100
          description: |
            % de avance acumulado de `task_id`, hasta dos decimales. Un valor menor que el último
            validado se acepta y se marca para la supervisión.

    VisitAttachmentPayload:
```

- [ ] **Step 4: Ruta**

Antes de la línea `  # -------------------------------------------------------------- health ----`, insertar:

```yaml
  # ------------------------------------------------------------ projects ----

  /v1/projects/tasks:
    get:
      operationId: projectsTasksList
      tags: [projects]
      summary: Tareas del cronograma de las que quien llama es responsable
      description: |
        Tareas **de detalle vigentes** (ni resumen ni retiradas) de las que quien llama es
        responsable, directamente o por una tarea superior de la EDT, con su último % validado (la
        visita validada más reciente vinculada a la tarea). Es lo que la app ofrece para vincular
        una visita a una tarea (`VisitPayload.task_id`).

        Se entrega la lista **completa** y el cliente reemplaza la suya: una tarea retirada o una
        responsabilidad quitada desaparecen sin tombstones. Para no repetir la descarga, el cliente
        envía la `version` de la última lista; si nada cambió, la respuesta trae `changed: false`
        e `items` vacío.
      parameters:
        - name: version
          in: query
          required: false
          schema:
            type: string
            maxLength: 64
          description: '`version` de la última lista descargada.'
      responses:
        '200':
          description: Lista de tareas, o "sin cambios".
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/FieldTaskList'
        '401': { $ref: '#/components/responses/Unauthorized' }
        '403': { $ref: '#/components/responses/Forbidden' }
        '422': { $ref: '#/components/responses/UnprocessableEntity' }
        '500': { $ref: '#/components/responses/InternalError' }

```

- [ ] **Step 5: Esquemas**

Antes de `    HealthStatus:` (en `components.schemas`), insertar:

```yaml
    FieldTask:
      type: object
      description: Una tarea del cronograma a la que la app puede vincular una visita.
      required: [id, project_id, project_name, wbs, outline_number, name, last_validated_pct]
      properties:
        id:
          $ref: '#/components/schemas/Uuid'
        project_id:
          $ref: '#/components/schemas/Uuid'
        project_name:
          type: string
          maxLength: 200
        wbs:
          type: string
          maxLength: 100
          description: EDT del Project; puede venir vacía (se muestra `outline_number`).
        outline_number:
          type: string
          maxLength: 100
        name:
          type: string
          maxLength: 500
        last_validated_pct:
          type: [number, 'null']
          minimum: 0
          maximum: 100
          description: '% de la visita validada más reciente de la tarea; `null` si no hay.'

    FieldTaskList:
      type: object
      required: [version, changed, items]
      properties:
        version:
          type: string
          maxLength: 64
          description: Huella de la lista. Se reenvía en `?version=` la próxima vez.
        changed:
          type: boolean
          description: '`false` si la lista no cambió desde `?version=`; entonces `items` va vacío.'
        items:
          type: array
          items:
            $ref: '#/components/schemas/FieldTask'

```

- [ ] **Step 6: Validar**

Run (desde la raíz de `SUNO_server`):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd)/server/openapi:/o" python:3.12-slim \
  sh -c "pip install -q openapi-spec-validator && python -m openapi_spec_validator /o/v1.yaml"
```

Expected: `/o/v1.yaml: OK`.

- [ ] **Step 7: Commit**

```bash
git add server/openapi/v1.yaml
git commit -m "feat(api): add task progress to the visit contract and GET /v1/projects/tasks"
```

---

### Task A3: Forma del payload, SQL y `VisitRecord`

**Files:**
- Modify: `server/visitacampo/sync/payloads.py`, `server/visitacampo/sync/sql.py`, `server/visitacampo/sync/records.py`, `server/visitacampo/sync/schemas.py`
- Test: `server/visitacampo/sync/tests/test_payloads.py`, `server/visitacampo/sync/tests/test_sql.py`

- [ ] **Step 1: Pruebas que fallan (puras)**

En `server/visitacampo/sync/tests/test_payloads.py`, en `CreateTests.test_accepts_the_full_contract_payload`, el conjunto esperado de `parsed.fields` pasa a incluir los dos campos nuevos:

```python
        self.assertEqual(
            set(parsed.fields),
            {
                "assignment_id",
                "template_version_id",
                "answers",
                "lat",
                "lon",
                "accuracy_m",
                "altitude_m",
                "captured_at",
                "device_id",
                "task_id",
                "progress_pct",
            },
        )
        self.assertIsNone(parsed.fields["assignment_id"])
        self.assertIsNone(parsed.fields["task_id"])
        self.assertIsNone(parsed.fields["progress_pct"])
```

Y al final del archivo:

```python
class TaskProgressTests(SimpleTestCase):
    """Costos, etapa 1: `task_id` y `progress_pct` (forma y par; sin BD)."""

    TASK = str(uuid.uuid4())

    def _reasons(self, parsed: object) -> set[tuple[str, str]]:
        assert isinstance(parsed, Rejection)
        details = parsed.details or {}
        errors = details.get("errors", [details])
        return {(e["field"], e["reason"]) for e in errors}

    def test_accepts_task_and_progress_as_decimal(self) -> None:
        parsed = parse_op(
            "visit", "create", None, _create_payload(task_id=self.TASK, progress_pct=35.5)
        )
        assert isinstance(parsed, VisitWrite)
        self.assertEqual(parsed.fields["task_id"], uuid.UUID(self.TASK))
        self.assertEqual(parsed.fields["progress_pct"], Decimal("35.5"))

    def test_integers_and_the_bounds_are_valid(self) -> None:
        for pct in (0, 100, 99.99):
            with self.subTest(pct=pct):
                parsed = parse_op(
                    "visit", "create", None, _create_payload(task_id=self.TASK, progress_pct=pct)
                )
                self.assertIsInstance(parsed, VisitWrite)

    def test_rejects_out_of_range_and_too_many_decimals(self) -> None:
        for pct in (-0.01, 100.01, 150, 33.333):
            with self.subTest(pct=pct):
                parsed = parse_op(
                    "visit", "create", None, _create_payload(task_id=self.TASK, progress_pct=pct)
                )
                self.assertIn("progress_pct", {field for field, _ in self._reasons(parsed)})

    def test_rejects_what_is_not_a_json_number(self) -> None:
        for pct in (True, "35", float("nan"), float("inf")):
            with self.subTest(pct=pct):
                parsed = parse_op(
                    "visit", "create", None, _create_payload(task_id=self.TASK, progress_pct=pct)
                )
                self.assertIn("progress_pct", {field for field, _ in self._reasons(parsed)})

    def test_task_and_progress_travel_together_on_create(self) -> None:
        only_task = parse_op("visit", "create", None, _create_payload(task_id=self.TASK))
        self.assertEqual(self._reasons(only_task), {("progress_pct", "task_progress_pair")})
        only_pct = parse_op("visit", "create", None, _create_payload(progress_pct=10))
        self.assertEqual(self._reasons(only_pct), {("task_id", "task_progress_pair")})

    def test_update_carries_both_or_neither(self) -> None:
        visit_id = str(uuid.uuid4())
        both = parse_op(
            "visit", "update", 3, {"id": visit_id, "task_id": self.TASK, "progress_pct": 50}
        )
        assert isinstance(both, VisitWrite)
        self.assertEqual(both.fields, {"task_id": uuid.UUID(self.TASK), "progress_pct": Decimal("50")})

        unlink = parse_op("visit", "update", 3, {"id": visit_id, "task_id": None, "progress_pct": None})
        assert isinstance(unlink, VisitWrite)
        self.assertEqual(unlink.fields, {"task_id": None, "progress_pct": None})

        only_pct = parse_op("visit", "update", 3, {"id": visit_id, "progress_pct": 50})
        self.assertEqual(self._reasons(only_pct), {("task_id", "task_progress_pair")})

        half_null = parse_op(
            "visit", "update", 3, {"id": visit_id, "task_id": self.TASK, "progress_pct": None}
        )
        self.assertEqual(self._reasons(half_null), {("progress_pct", "task_progress_pair")})
```

Añadir `from decimal import Decimal` a los imports del archivo.

En `server/visitacampo/sync/tests/test_sql.py`, en `ColumnMappingTests.test_maps_the_contract_names_to_the_orm_columns`, añadir:

```python
        self.assertEqual(sql.column("task_id"), '"task_id"')
        self.assertEqual(sql.column("progress_pct"), '"progress_pct"')
```

y en `InsertTests`:

```python
    def test_task_progress_is_written_as_null_when_absent(self) -> None:
        """Un alta sin tarea (cliente anterior) escribe NULL en las dos columnas."""
        self.assertIn('"task_id"', self.statement.sql)
        self.assertIn('"progress_pct"', self.statement.sql)
```

- [ ] **Step 2: Verificar que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/sync/tests/test_payloads.py visitacampo/sync/tests/test_sql.py`
Expected: FAIL (campo extra prohibido `task_id`; `KeyError: 'task_id'` en `sql.column`).

- [ ] **Step 3: `payloads.py`**

Imports: añadir `from decimal import Decimal` y `BeforeValidator` a la importación de `pydantic`:

```python
from decimal import Decimal
```

```python
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
)
```

Después de la definición de `Finite = Annotated[float, Field(allow_inf_nan=False)]`:

```python
def _json_number(value: Any) -> Any:
    """`progress_pct` viaja como número JSON: ni `true`, ni texto, ni NaN/Infinity.

    `Decimal(str(value))` y no `Decimal(value)`: `Decimal(35.1)` arrastraría la
    representación binaria del float (35.1000000000000014…) y no cumpliría
    `decimal_places=2`; el texto del float es lo que el cliente quiso decir.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a JSON number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("must be finite.")
    return Decimal(str(value))


#: % de avance acumulado de una tarea (costos, etapa 1): `numeric(5,2)` en 0..100.
ProgressPct = Annotated[Decimal, BeforeValidator(_json_number)]
```

En `_VisitCreatePayload`, después de `device_id: uuid.UUID`:

```python
    # Costos, etapa 1. Opcionales: un cliente anterior no los envía.
    task_id: uuid.UUID | None = None
    progress_pct: ProgressPct | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
```

En `_VisitUpdatePayload`, después de `device_id: uuid.UUID | None = None`:

```python
    task_id: uuid.UUID | None = None
    progress_pct: ProgressPct | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
```

En `_UPDATABLE_FIELDS`, añadir al final de la tupla:

```python
    "task_id",
    "progress_pct",
```

Después de la función `_rejection(...)`:

```python
def _task_pair_rejection(fields: dict[str, Any], entity_id: uuid.UUID) -> Rejection | None:
    """`task_id` y `progress_pct` van juntos: los dos con valor o los dos `null`.

    Lo exige también la BD (`visit_task_progress_pair`), pero allí sería un
    `IntegrityError` que tumba el lote entero (ver el docstring del módulo). En
    un `update` tienen que viajar los dos: aceptar uno solo obligaría a leer la
    fila para saber si el par queda completo.
    """
    has_task, has_pct = "task_id" in fields, "progress_pct" in fields
    if not has_task and not has_pct:
        return None
    task_id, pct = fields.get("task_id"), fields.get("progress_pct")
    if has_task and has_pct and (task_id is None) == (pct is None):
        return None
    return _rejection(
        "task_id and progress_pct must be sent together, both set or both null.",
        field="progress_pct" if task_id is not None else "task_id",
        reason="task_progress_pair",
        entity_id=entity_id,
    )
```

En `parse_op`, justo antes del `return VisitWrite(...)` final:

```python
    pair = _task_pair_rejection(fields, validated.id)
    if pair is not None:
        return pair

```

- [ ] **Step 4: `sql.py`**

En `_MODEL_FIELD_BY_CONTRACT_NAME`, al final:

```python
    "task_id": "task",
    "progress_pct": "progress_pct",
```

En `_INSERT_FIELDS`, al final de la tupla:

```python
    "task_id",
    "progress_pct",
```

En `build_insert`, el diccionario `values` pasa a:

```python
    values: dict[str, Any] = {
        "id": visit_id,
        "org_id": org_id,
        "version": INITIAL_VERSION,
        "change_seq": change_seq,
        "deleted_at": None,
        "created_by": created_by,
        # Costos, etapa 1: un alta sin tarea las escribe en NULL.
        "task_id": None,
        "progress_pct": None,
        **fields,
    }
```

- [ ] **Step 5: `records.py` y `schemas.py`**

En `records.py::visit_record`, en `data`, después de `"created_by": ...`:

```python
            "task_id": uuid_str(visit.task_id),
            # `numeric` → número JSON (el contrato dice `number`, no texto).
            "progress_pct": None if visit.progress_pct is None else float(visit.progress_pct),
```

En `schemas.py::VisitData`, después de `created_by: uuid.UUID`:

```python
    task_id: uuid.UUID | None
    progress_pct: float | None
```

- [ ] **Step 6: Verificar que pasan**

Run: `infra/scripts/test_docker.sh visitacampo/sync`
Expected: todo pasa (las pruebas existentes de push/pull siguen verdes: un alta sin tarea escribe NULL).

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/sync
git commit -m "feat(sync): accept task_id and progress_pct in visit pushes and emit them in records"
```

---

### Task A4: Responsable efectivo por rama (`covers`, `responsible_outlines`)

**Files:**
- Modify: `server/visitacampo/projects/access.py`
- Test: `server/visitacampo/projects/tests/test_access.py`

- [ ] **Step 1: Pruebas que fallan**

Al final de `server/visitacampo/projects/tests/test_access.py`:

```python
class CoversTests(unittest.TestCase):
    """Pura: el número de esquema es la ruta de la tarea en la EDT."""

    def test_a_task_covers_itself_and_its_branch(self) -> None:
        self.assertTrue(covers(["1.2"], "1.2"))
        self.assertTrue(covers(["1.2"], "1.2.3.4"))
        self.assertTrue(covers(["3", "1"], "1.1"))

    def test_a_prefix_without_dot_is_not_an_ancestor(self) -> None:
        self.assertFalse(covers(["1.1"], "1.10"))
        self.assertFalse(covers(["1.2"], "1"))
        self.assertFalse(covers([], "1"))


@skip_without_db
class ResponsibleOutlinesTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def test_by_project_only_current_tasks(self) -> None:
        self.world.responsible(1)
        self.world.responsible(5)
        ctx = self.world.ctx()
        self.assertEqual(
            responsible_outlines(ctx, user_id=self.world.agent.pk),
            {self.world.project.pk: ["1", "2.1"]},
        )
        # v2 retira la 2.1: su responsabilidad deja de contar.
        apply_import(ctx, stage_import(ctx, self.world.project, file_name="v2.xml", data=v2()).pk)
        self.assertEqual(
            responsible_outlines(ctx, user_id=self.world.agent.pk),
            {self.world.project.pk: ["1"]},
        )
        self.assertEqual(
            responsible_outlines(ctx, user_id=self.world.agent.pk, project_ids=[]), {}
        )

    def test_matches_effective_responsibles(self) -> None:
        """La cobertura por esquema y la herencia por `parent` dicen lo mismo."""
        self.world.responsible(1)
        self.world.responsible(6)
        ctx = self.world.ctx()
        outlines = responsible_outlines(ctx, user_id=self.world.agent.pk)[self.world.project.pk]
        by_parent = effective_responsibles(ctx, self.world.project)
        for task in Task.objects.filter(project=self.world.project):
            with self.subTest(task=task.outline_number):
                self.assertEqual(
                    covers(outlines, task.outline_number),
                    self.world.agent in by_parent[task.pk],
                )
```

Imports a añadir arriba del archivo:

```python
from visitacampo.projects.access import covers, responsible_outlines
from visitacampo.projects.importer import apply_import, stage_import

from .factories import make_field_world
from .samples import v2
```

(`effective_responsibles`, `Task`, `TestCase` y `unittest` ya están importados.)

- [ ] **Step 2: Verificar que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_access.py`
Expected: FAIL — `ImportError: cannot import name 'covers'`.

- [ ] **Step 3: Implementar**

En `server/visitacampo/projects/access.py`, imports:

```python
from collections import defaultdict
from collections.abc import Collection, Iterable
from uuid import UUID
```

Y al final del archivo:

```python
def covers(outlines: Iterable[str], outline: str) -> bool:
    """¿Alguno de `outlines` es `outline` o un ancestro suyo en la EDT?

    El número de esquema de MS Project ("1.2.3") es la ruta de la tarea en el
    árbol: el importador deriva `parent` de él (`importer._apply_tasks`), así que
    ser ancestro equivale a ser prefijo seguido de un punto ("1.1" no es
    ancestro de "1.10"). Permite saber si alguien es responsable efectivo sin
    recorrer la cadena de `parent` (una query por nivel).
    """
    return any(outline == o or outline.startswith(f"{o}.") for o in outlines)


def responsible_outlines(
    ctx: RequestContext, *, user_id: UUID, project_ids: Collection[UUID] | None = None
) -> dict[UUID, list[str]]:
    """Por proyecto, los números de esquema de las tareas **vigentes** de las que
    `user_id` es responsable directo. Con `covers`, decide el responsable efectivo.
    Una sola query."""
    links = scoped_qs(ctx, TaskResponsible).filter(
        user_id=user_id, task__retired_in__isnull=True
    )
    if project_ids is not None:
        links = links.filter(task__project_id__in=list(project_ids))
    out: dict[UUID, list[str]] = defaultdict(list)
    for project_id, outline in links.order_by("task__sort_order").values_list(
        "task__project_id", "task__outline_number"
    ):
        out[project_id].append(outline)
    return dict(out)
```

- [ ] **Step 4: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_access.py`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/access.py server/visitacampo/projects/tests/test_access.py
git commit -m "feat(projects): resolve effective task responsibility by WBS outline"
```

---

### Task A5: Validar la tarea en el push

**Files:**
- Modify: `server/visitacampo/sync/services.py`
- Test: `server/visitacampo/sync/tests/test_push_tasks.py`

- [ ] **Step 1: Pruebas que fallan**

`server/visitacampo/sync/tests/test_push_tasks.py`:

```python
"""Push de visitas con avance de campo (costos, etapa 1): referencias, permisos
y que el pull devuelva lo guardado. Requiere PostgreSQL."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from typing import Any

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import Project, Role, Task, TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.samples import v1, v2
from visitacampo.sync.cursor import CURSOR_ZERO

from .factories import build_scenario, create_op, pull, push, update_op, visit_of

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TaskProgressPushTests(TestCase):
    """El agente del escenario es responsable de la etapa 1 (uid 1): cubre 1.1 y 1.2."""

    def setUp(self) -> None:
        self.scenario = build_scenario()
        self.admin = make_user(self.scenario.org, username="admin-cronograma", role=Role.ADMIN)
        self.admin_ctx = resolve_request_context(
            Principal(user_id=self.admin.pk, org_id=self.scenario.org.pk)
        )
        self.project = Project.objects.create(
            org=self.scenario.org, name="Obra", created_by=self.admin
        )
        self.apply(v1())
        self.assign(1)

    def apply(self, data: bytes) -> None:
        staged = stage_import(self.admin_ctx, self.project, file_name="x.xml", data=data)
        apply_import(self.admin_ctx, staged.pk)

    def assign(self, uid: int) -> None:
        TaskResponsible.objects.create(
            org=self.scenario.org,
            task=self.task(uid),
            user=self.scenario.user,
            assigned_by=self.admin,
        )

    def task(self, uid: int) -> Task:
        return Task.objects.get(project=self.project, uid=uid)

    def push_one(self, op: dict[str, Any]) -> dict[str, Any]:
        response = push(self.scenario, [op])
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"][0]

    def linked(self, uid: int, pct: object, visit_id: uuid.UUID | None = None) -> dict[str, Any]:
        return create_op(
            self.scenario, visit_id=visit_id, task_id=str(self.task(uid).pk), progress_pct=pct
        )

    def assertRejected(self, result: dict[str, Any], code: str, field: str, reason: str) -> None:
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(result["error"]["code"], code)
        self.assertEqual(result["error"]["details"], {"field": field, "reason": reason})

    def test_responsible_by_branch_reports_progress(self) -> None:
        visit_id = uuid.uuid4()
        result = self.push_one(self.linked(3, 35.5, visit_id))
        self.assertEqual(result["status"], "applied", result)
        visit = visit_of(visit_id)
        self.assertEqual((visit.task_id, visit.progress_pct), (self.task(3).pk, Decimal("35.50")))

    def test_direct_responsible_reports_progress(self) -> None:
        self.assign(5)
        self.assertEqual(self.push_one(self.linked(5, 10))["status"], "applied")

    def test_someone_else_s_task_is_forbidden(self) -> None:
        self.assertRejected(self.push_one(self.linked(5, 10)), "forbidden", "task_id", "not_responsible")

    def test_a_summary_task_is_rejected(self) -> None:
        self.assertRejected(
            self.push_one(self.linked(1, 10)), "validation_error", "task_id", "task_is_summary"
        )

    def test_a_retired_task_is_rejected(self) -> None:
        self.assign(4)
        columnas = self.task(5)
        self.apply(v2())  # retira la 2.1 Columnas
        op = create_op(self.scenario, task_id=str(columnas.pk), progress_pct=10)
        self.assertRejected(self.push_one(op), "validation_error", "task_id", "task_retired")

    def test_a_task_of_another_organization_does_not_exist(self) -> None:
        other = make_org("Ajena")
        owner = make_user(other, username="admin-ajena", role=Role.ADMIN)
        project = Project.objects.create(org=other, name="Ajena", created_by=owner)
        foreign = Task.objects.create(
            org=other,
            project=project,
            uid=1,
            outline_number="1",
            outline_level=1,
            sort_order=1,
            name="Ajena",
        )
        op = create_op(self.scenario, task_id=str(foreign.pk), progress_pct=10)
        self.assertRejected(self.push_one(op), "not_found", "task_id", "unknown_reference")

    def test_progress_out_of_range_is_rejected(self) -> None:
        result = self.push_one(self.linked(3, 100.01))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"]["code"], "validation_error")
        fields = {e["field"] for e in result["error"]["details"]["errors"]}
        self.assertIn("progress_pct", fields)

    def test_a_task_without_progress_is_rejected(self) -> None:
        op = create_op(self.scenario, task_id=str(self.task(3).pk))
        self.assertRejected(
            self.push_one(op), "validation_error", "progress_pct", "task_progress_pair"
        )

    def test_update_moves_the_progress_and_needs_the_pair(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, visit_id))
        version = visit_of(visit_id).version
        both = update_op(visit_id, version, task_id=str(self.task(3).pk), progress_pct=50)
        self.assertEqual(self.push_one(both)["status"], "applied")
        self.assertEqual(visit_of(visit_id).progress_pct, Decimal("50.00"))

        only_pct = update_op(visit_id, version + 1, progress_pct=60)
        self.assertRejected(
            self.push_one(only_pct), "validation_error", "task_id", "task_progress_pair"
        )

    def test_unlinking_needs_no_responsibility(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.linked(3, 20, visit_id))
        TaskResponsible.objects.filter(user=self.scenario.user).delete()
        version = visit_of(visit_id).version
        result = self.push_one(update_op(visit_id, version, task_id=None, progress_pct=None))
        self.assertEqual(result["status"], "applied", result)
        visit = visit_of(visit_id)
        self.assertEqual((visit.task_id, visit.progress_pct), (None, None))

    def test_pull_returns_task_and_progress(self) -> None:
        visit_id = uuid.uuid4()
        self.push_one(self.linked(3, 35.5, visit_id))
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(r["data"] for r in rows if r["entity"] == "visit" and r["data"]["id"] == str(visit_id))
        self.assertEqual((data["task_id"], data["progress_pct"]), (str(self.task(3).pk), 35.5))

    def test_linked_operations_cost_one_query_each(self) -> None:
        """La resolución de tareas va en bloque: no crece con el lote (RD-07)."""

        def queries(size: int) -> int:
            ops = [self.linked(3, 10) for _ in range(size)]
            with CaptureQueriesContext(connection) as captured:
                response = push(self.scenario, ops)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(all(r["status"] == "applied" for r in response.json()["results"]))
            return len(captured.captured_queries)

        self.assertEqual(queries(200), queries(1) + 199)
```

- [ ] **Step 2: Verificar que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/sync/tests/test_push_tasks.py`
Expected: FAIL — `test_someone_else_s_task_is_forbidden` y los demás rechazos devuelven `applied` (o un 500 por la FK de otra organización / el `CHECK`).

- [ ] **Step 3: Implementar**

En `server/visitacampo/sync/services.py`:

Imports — añadir `Task` a la importación de `visitacampo.core.models` y:

```python
from visitacampo.projects.access import covers, responsible_outlines
```

Antes de `class _References`:

```python
@dataclass(frozen=True, slots=True)
class _TaskRef:
    """Lo que el push necesita saber de una tarea referenciada (costos, etapa 1)."""

    retired: bool
    is_summary: bool
    #: Quien envía el lote es responsable efectivo (directo o por la rama).
    covered: bool
```

En `_References`, añadir el campo después de `visit_template_versions`:

```python
    tasks: dict[uuid.UUID, _TaskRef]
```

En `_References.missing`, la tupla de campos pasa a:

```python
        for field, known in (
            ("template_version_id", self.template_versions),
            ("assignment_id", self.assignments),
            ("device_id", self.devices),
            ("task_id", self.tasks),
        ):
```

Y dentro de `_References`, después de `missing`:

```python
    def task_rejection(self, parsed: VisitWrite) -> Rejection | None:
        """La tarea existe (lo comprobó `missing`); ¿se le puede reportar avance?

        Solo cuando se **pone** una tarea: desvincular (`null`) no exige ser
        responsable, basta con poder modificar la visita (ya comprobado).
        """
        task_id = parsed.fields.get("task_id")
        if task_id is None:
            return None
        ref = self.tasks[task_id]
        if ref.retired:
            return Rejection(
                code=CODE_VALIDATION_ERROR,
                message="The task was retired from the schedule.",
                details={"field": "task_id", "reason": "task_retired"},
                entity_id=parsed.entity_id,
            )
        if ref.is_summary:
            return Rejection(
                code=CODE_VALIDATION_ERROR,
                message="Progress is reported on detail tasks, not on summaries.",
                details={"field": "task_id", "reason": "task_is_summary"},
                entity_id=parsed.entity_id,
            )
        if not ref.covered:
            return Rejection(
                code=CODE_FORBIDDEN,
                message="Only a responsible of the task can report its progress.",
                details={"field": "task_id", "reason": "not_responsible"},
                entity_id=parsed.entity_id,
            )
        return None
```

Antes de `_resolve_references`:

```python
def _task_refs(ctx: RequestContext, writes: list[VisitWrite]) -> dict[uuid.UUID, _TaskRef]:
    """Tareas que el lote vincula: dos queries para todo el lote, cero si ninguna."""
    ids = {w.fields["task_id"] for w in writes if w.fields.get("task_id") is not None}
    if not ids:
        return {}
    rows = list(
        scoped_qs(ctx, Task)
        .filter(pk__in=ids)
        .values_list("pk", "project_id", "outline_number", "retired_in_id", "is_summary")
    )
    outlines = responsible_outlines(
        ctx, user_id=ctx.user_id, project_ids={project_id for _, project_id, *_ in rows}
    )
    return {
        pk: _TaskRef(
            retired=retired_in is not None,
            is_summary=is_summary,
            covered=covers(outlines.get(project_id, ()), outline),
        )
        for pk, project_id, outline, retired_in, is_summary in rows
    }
```

En `_resolve_references`, el `return` pasa a:

```python
    return _References(
        template_versions=definitions,
        assignments=_existing_ids(ctx, Assignment, wanted["assignment_id"]),
        devices=_existing_ids(ctx, Device, wanted["device_id"]),
        visit_template_versions=visit_template_versions,
        tasks=_task_refs(ctx, writes),
    )
```

En `_process`, justo después del bloque `missing = references.missing(...)` / `if missing is not None: return ...`:

```python
    # Costos, etapa 1: tarea vigente, de detalle y de la que quien envía es
    # responsable. Antes de tomar `change_seq`, como las respuestas.
    task_problem = references.task_rejection(parsed)
    if task_problem is not None:
        return _rejected_result(op, task_problem)
```

- [ ] **Step 4: Verificar que pasa (y que el presupuesto del push sigue)**

Run: `infra/scripts/test_docker.sh visitacampo/sync`
Expected: todo pasa, incluido `test_query_budget.py` (un lote sin tareas no hace queries nuevas).

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/sync/services.py server/visitacampo/sync/tests/test_push_tasks.py
git commit -m "feat(sync): validate schedule task links on visit push"
```

---

### Task A6: `field_progress` — último % validado y "menor que el último validado"

**Files:**
- Create: `server/visitacampo/projects/field_progress.py`
- Test: `server/visitacampo/projects/tests/test_field_progress.py`

- [ ] **Step 1: Pruebas que fallan**

Al final de `server/visitacampo/projects/tests/test_field_progress.py`:

```python
@skip_without_db
class ValidatedPctTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.ctx = self.world.ctx()

    def pct(self, **kwargs: object) -> dict[UUID, Decimal]:
        return validated_pct_by_task(scoped_qs(self.ctx, Visit), **kwargs)  # type: ignore[arg-type]

    def test_latest_approved_by_capture_time_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        w.approve(w.visit(3, "60", hours=2))
        w.visit(3, "80", hours=3)  # sin revisar: no cuenta
        w.approve(w.visit(2, "100", hours=1))
        self.assertEqual(self.pct(), {w.task(3).pk: Decimal("60.00"), w.task(2).pk: Decimal("100.00")})

    def test_the_latest_decision_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        later = w.visit(3, "60", hours=2)
        w.approve(later)
        w.reject(later)  # rechazada después de validarla: sale
        self.assertEqual(self.pct(), {w.task(3).pk: Decimal("30.00")})

    def test_deleted_visits_and_other_filters(self) -> None:
        w = self.world
        w.approve(w.visit(3, "30", hours=1))
        w.approve(w.visit(3, "90", hours=2, deleted=True))
        w.approve(w.visit(5, "10", hours=1))
        self.assertEqual(self.pct(task_ids=[w.task(3).pk]), {w.task(3).pk: Decimal("30.00")})
        self.assertEqual(self.pct(task_ids=[]), {})
        self.assertEqual(len(self.pct(project_id=w.project.pk)), 2)

    def test_previous_validated_and_regression(self) -> None:
        w = self.world
        first = w.visit(3, "60", hours=1)
        w.approve(first)
        lower = w.visit(3, "40", hours=2)
        higher = w.visit(3, "70", hours=3)
        rows = {row.visit.pk: row for row in progress_visits(self.ctx, w.task(3))}
        self.assertIsNone(rows[first.pk].previous_validated_pct)
        self.assertEqual(rows[lower.pk].previous_validated_pct, Decimal("60.00"))
        self.assertTrue(rows[lower.pk].regression)
        self.assertFalse(rows[higher.pk].regression)
        self.assertEqual(rows[first.pk].status, "approved")
        self.assertIsNone(rows[lower.pk].status)
        # Más reciente primero.
        self.assertEqual(
            [r.visit.pk for r in progress_visits(self.ctx, w.task(3))],
            [higher.pk, lower.pk, first.pk],
        )

    def test_is_regression_is_pure(self) -> None:
        self.assertTrue(is_regression(Decimal("40"), Decimal("60")))
        self.assertFalse(is_regression(Decimal("60"), Decimal("60")))
        self.assertFalse(is_regression(Decimal("40"), None))
        self.assertFalse(is_regression(None, Decimal("60")))
```

Imports a añadir arriba del archivo:

```python
from decimal import Decimal
from uuid import UUID

from visitacampo.core.models import Visit
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.field_progress import (
    is_regression,
    progress_visits,
    validated_pct_by_task,
)
```

- [ ] **Step 2: Verificar que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: FAIL — `ModuleNotFoundError: visitacampo.projects.field_progress`.

- [ ] **Step 3: Implementar**

`server/visitacampo/projects/field_progress.py`:

```python
"""Avance de campo (costos, etapa 1): el % que reportan las visitas vinculadas
a tareas del cronograma.

- **% SUNO de una tarea hoja** = `progress_pct` de la visita **validada** más
  reciente (por `captured_at`) vinculada a ella. "Validada" = su revisión
  vigente —la más reciente, `VisitReview` es un historial— es `approved`.
- **Menor que el último validado**: una visita cuyo % es menor que el de la
  visita validada más reciente de la misma tarea capturada **antes** que ella.
  Se calcula al leer: validar o rechazar después la visita de referencia
  cambia el aviso.

Las funciones reciben el queryset base de visitas en vez de un contexto: la
plataforma les pasa `scoped_qs(ctx, Visit)` y el Admin su propio queryset (el
superusuario ve todas las organizaciones). La decisión vigente se calcula con
dos `Max` sobre la relación `reviews`, como el filtro de revisión del Admin.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from django.db.models import F, Max, OuterRef, Q, QuerySet, Subquery

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Task, Visit, VisitReviewStatus
from visitacampo.core.scoping import scoped_qs

APPROVED = str(VisitReviewStatus.APPROVED)
REJECTED = str(VisitReviewStatus.REJECTED)

REVIEW_LABELS = {APPROVED: "Validada", REJECTED: "Rechazada", None: "Sin revisar"}


def with_review(visits: QuerySet[Visit]) -> QuerySet[Visit]:
    """Anota `last_review_at` y `last_approved_at` (sin subconsultas)."""
    return visits.annotate(
        last_review_at=Max("reviews__created_at"),
        last_approved_at=Max("reviews__created_at", filter=Q(reviews__status=APPROVED)),
    )


def review_status(visit: Visit) -> str | None:
    """Decisión vigente de una visita anotada con `with_review`, o `None`."""
    last = getattr(visit, "last_review_at", None)
    if last is None:
        return None
    return APPROVED if getattr(visit, "last_approved_at", None) == last else REJECTED


def approved(visits: QuerySet[Visit]) -> QuerySet[Visit]:
    """Las visitas cuya revisión vigente es `approved`."""
    return with_review(visits).filter(
        last_review_at__isnull=False, last_approved_at=F("last_review_at")
    )


def validated_pct_by_task(
    visits: QuerySet[Visit],
    *,
    task_ids: Iterable[UUID] | None = None,
    project_id: UUID | None = None,
) -> dict[UUID, Decimal]:
    """% SUNO por tarea: el de su visita validada más reciente. Una query."""
    linked = visits.filter(task__isnull=False, deleted_at__isnull=True)
    if task_ids is not None:
        ids = list(task_ids)
        if not ids:
            return {}
        linked = linked.filter(task_id__in=ids)
    if project_id is not None:
        linked = linked.filter(task__project_id=project_id)
    rows = (
        approved(linked)
        .order_by("task_id", "-captured_at", "-change_seq")
        .values_list("task_id", "progress_pct")
    )
    result: dict[UUID, Decimal] = {}
    for task_id, pct in rows:
        result.setdefault(task_id, pct)
    return result


def with_previous_validated_pct(
    visits: QuerySet[Visit], base: QuerySet[Visit]
) -> QuerySet[Visit]:
    """Anota `previous_validated_pct`: el % de la visita validada más reciente
    de la misma tarea capturada antes. `base` es de dónde se buscan (la misma
    organización, o todas para el superusuario del Admin: la correlación por
    tarea ya restringe a una sola)."""
    previous = (
        approved(
            base.filter(
                task=OuterRef("task"),
                captured_at__lt=OuterRef("captured_at"),
                deleted_at__isnull=True,
            )
        )
        .order_by("-captured_at", "-change_seq")
        .values("progress_pct")[:1]
    )
    return visits.annotate(previous_validated_pct=Subquery(previous))


def is_regression(pct: Decimal | None, previous: Decimal | None) -> bool:
    return pct is not None and previous is not None and pct < previous


@dataclass(frozen=True)
class ProgressVisit:
    """Una visita vinculada a una tarea, para la ficha de la tarea."""

    visit: Visit
    status: str | None
    previous_validated_pct: Decimal | None

    @property
    def regression(self) -> bool:
        return is_regression(self.visit.progress_pct, self.previous_validated_pct)

    @property
    def status_label(self) -> str:
        return REVIEW_LABELS[self.status]


def progress_visits(ctx: RequestContext, task: Task) -> list[ProgressVisit]:
    """Visitas vivas vinculadas a `task`, la más reciente primero. Una query."""
    base = scoped_qs(ctx, Visit)
    visits = (
        with_previous_validated_pct(
            with_review(base.filter(task=task, deleted_at__isnull=True)), base
        )
        .select_related("created_by")
        .order_by("-captured_at", "-change_seq")
    )
    return [
        ProgressVisit(
            visit=v,
            status=review_status(v),
            previous_validated_pct=getattr(v, "previous_validated_pct", None),
        )
        for v in visits
    ]
```

- [ ] **Step 4: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/field_progress.py server/visitacampo/projects/tests/test_field_progress.py
git commit -m "feat(projects): compute validated field progress per task and regressions"
```

---

### Task A7: % SUNO en todo el cronograma

**Files:**
- Modify: `server/visitacampo/projects/schedule.py`
- Test: `server/visitacampo/projects/tests/test_field_progress.py`

- [ ] **Step 1: Prueba que falla**

Al final de `test_field_progress.py`:

```python
@skip_without_db
class ScheduleSunoPctTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()

    def row(self, schedule: Schedule, uid: int) -> ScheduleRow:
        return next(r for r in schedule.rows if r.task.uid == uid)

    def test_build_schedule_reads_validated_field_progress(self) -> None:
        w = self.world
        w.approve(w.visit(3, "50", hours=1))
        w.visit(3, "90", hours=2)  # sin validar
        row = self.row(build_schedule(w.ctx(), w.project), 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 50.0)

    def test_an_explicit_map_still_wins(self) -> None:
        w = self.world
        w.approve(w.visit(3, "50", hours=1))
        row = self.row(build_schedule(w.ctx(), w.project, suno_pct={}), 3)
        assert row.indicators is not None
        self.assertEqual(row.indicators.suno.actual_pct, 0.0)
```

Imports:

```python
from visitacampo.projects.schedule import Schedule, ScheduleRow, build_schedule
```

- [ ] **Step 2: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: FAIL — `0.0 != 50.0`.

- [ ] **Step 3: Implementar**

En `server/visitacampo/projects/schedule.py`, imports: añadir `Visit` a `visitacampo.core.models` y

```python
from .field_progress import validated_pct_by_task
```

El docstring y el cálculo de `suno` de `build_schedule` pasan a:

```python
    """Tareas vigentes en el orden del Project, con sus indicadores.

    `suno_pct`: % SUNO por id de tarea. Sin él se calcula del avance de campo
    validado (`field_progress.validated_pct_by_task`), así que lo reciben todas
    las páginas que llaman aquí: lista, cronograma, Gantt y Excel.
    """
```

```python
    if suno_pct is None:
        suno_pct = validated_pct_by_task(scoped_qs(ctx, Visit), project_id=project.pk)
    suno = {str(k): float(v) for k, v in suno_pct.items()}
```

(reemplaza la línea `suno = {str(k): float(v) for k, v in (suno_pct or {}).items()}`). Ajustar la anotación del parámetro a `suno_pct: Mapping[UUID, float | Decimal] | None = None` e importar `from decimal import Decimal`.

- [ ] **Step 4: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects`
Expected: todo pasa (las pruebas de `test_schedule.py` siguen: sin visitas, % SUNO = 0).

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/schedule.py server/visitacampo/projects/tests/test_field_progress.py
git commit -m "feat(projects): feed validated field progress into the schedule"
```

---

### Task A8: `GET /v1/projects/tasks`

**Files:**
- Modify: `server/visitacampo/core/policies.py`, `server/visitacampo/core/tests/test_policies.py`, `server/config/api.py`
- Create: `server/visitacampo/projects/field_tasks.py`, `server/visitacampo/projects/schemas.py`, `server/visitacampo/projects/api.py`
- Test: `server/visitacampo/projects/tests/test_api.py`

- [ ] **Step 1: Pruebas que fallan**

En `server/visitacampo/core/tests/test_policies.py`, en `NoRoleGrantsNothingTests._registry`, después de `"can_export_schedule": ORG,`:

```python
            # Avance de campo: cualquier miembro pide sus tareas.
            "can_read_field_tasks": ORG,
```

`server/visitacampo/projects/tests/test_api.py`:

```python
"""`GET /v1/projects/tasks`: las tareas que el agente puede vincular a una visita."""

from __future__ import annotations

import unittest
from typing import Any

from django.test import TestCase

from visitacampo.authn.tests.factories import login
from visitacampo.core.models import TaskResponsible
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import

from .factories import make_field_world
from .samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

URL = "/v1/projects/tasks"


@skip_without_db
class FieldTasksApiTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        token = login(self.client, username=self.world.agent.username).json()["tokens"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token['access_token']}"}

    def get(self, **params: str) -> dict[str, Any]:
        response = self.client.get(URL, params, **self.auth)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def names(self, body: dict[str, Any]) -> list[str]:
        return [item["name"] for item in body["items"]]

    def test_requires_authentication(self) -> None:
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_without_responsibilities_the_list_is_empty(self) -> None:
        body = self.get()
        self.assertEqual((body["changed"], body["items"]), (True, []))
        self.assertEqual(len(body["version"]), 64)

    def test_branch_lists_current_detail_tasks_with_last_validated_pct(self) -> None:
        w = self.world
        w.responsible(1)
        w.approve(w.visit(3, "40", hours=1))
        w.visit(3, "70", hours=2)  # sin validar
        body = self.get()
        self.assertEqual(
            [(i["wbs"], i["name"], i["last_validated_pct"]) for i in body["items"]],
            [("1.1", "Localización", None), ("1.2", "Descapote", 40.0)],
        )
        item = body["items"][1]
        self.assertEqual(item["id"], str(w.task(3).pk))
        self.assertEqual(item["project_id"], str(w.project.pk))
        self.assertEqual(item["project_name"], w.project.name)
        self.assertEqual(item["outline_number"], "1.2")

    def test_same_version_answers_unchanged(self) -> None:
        self.world.responsible(1)
        version = self.get()["version"]
        body = self.get(version=version)
        self.assertEqual((body["changed"], body["items"], body["version"]), (False, [], version))

    def test_reimport_retire_and_rename_change_the_list(self) -> None:
        w = self.world
        w.responsible(4)
        before = self.get()
        self.assertEqual(self.names(before), ["Columnas", "Fin de obra"])
        ctx = w.ctx()
        apply_import(ctx, stage_import(ctx, w.project, file_name="v2.xml", data=v2()).pk)
        after = self.get(version=before["version"])
        self.assertTrue(after["changed"])
        self.assertEqual(self.names(after), ["Vigas", "Fin de obra"])

    def test_a_validation_changes_the_version(self) -> None:
        w = self.world
        w.responsible(1)
        visit = w.visit(3, "40", hours=1)
        before = self.get()["version"]
        w.approve(visit)
        self.assertTrue(self.get(version=before)["changed"])

    def test_removing_the_responsibility_empties_the_list(self) -> None:
        self.world.responsible(1)
        self.assertEqual(len(self.get()["items"]), 2)
        TaskResponsible.objects.all().delete()
        self.assertEqual(self.get()["items"], [])

    def test_another_organization_s_tasks_never_appear(self) -> None:
        other = make_field_world("Otra")
        other.responsible(1)
        self.world.responsible(5)
        self.assertEqual(self.names(self.get()), ["Columnas"])
```

- [ ] **Step 2: Verificar que fallan**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_api.py visitacampo/core/tests/test_policies.py`
Expected: FAIL (404 en la ruta; `can_read_field_tasks` no existe).

- [ ] **Step 3: Política**

En `server/visitacampo/core/policies.py`, en `__all__` añadir (en orden alfabético) `"can_read_field_tasks"` y `"check_read_field_tasks"`. Después de `can_export_schedule`:

```python
def can_read_field_tasks(ctx: RequestContext, org_id: UUID) -> bool:
    """Pedir las tareas del cronograma propias (avance de campo, costos etapa 1).

    Cualquier miembro: la lista ya se limita a las tareas de las que quien
    llama es responsable, y el push vuelve a comprobarlo al vincular.
    """
    return user_has_organization_roles(ctx, org_id, MEMBER_ROLES)
```

Y después de `check_export_schedule`:

```python
def check_read_field_tasks(ctx: RequestContext, org_id: UUID) -> None:
    if not can_read_field_tasks(ctx, org_id):
        raise _denial(ctx, org_id)
```

- [ ] **Step 4: Servicio**

`server/visitacampo/projects/field_tasks.py`:

```python
"""Las tareas del cronograma que el agente puede vincular a una visita.

Tareas **vigentes de detalle** de las que quien llama es responsable efectivo
(directo o por la rama, `access.covers`), con su último % validado. Se entrega
la lista completa con una huella (`list_version`): el teléfono la reemplaza, y
cualquier cambio que la altere —importación, responsables, revisiones— cambia
la huella sin que nadie tenga que mover un cursor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db.models import Q

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Task, Visit
from visitacampo.core.policies import check_read_field_tasks
from visitacampo.core.scoping import scoped_qs

from .access import responsible_outlines
from .field_progress import validated_pct_by_task


def field_tasks(ctx: RequestContext) -> list[dict[str, Any]]:
    """Contrato `FieldTask`, en el orden de proyecto y del Project. Tres queries."""
    check_read_field_tasks(ctx, ctx.org_id)
    roots = responsible_outlines(ctx, user_id=ctx.user_id)
    if not roots:
        return []
    cover = Q()
    for project_id, outlines in roots.items():
        branch = Q()
        for outline in outlines:
            branch |= Q(outline_number=outline) | Q(outline_number__startswith=f"{outline}.")
        cover |= Q(project_id=project_id) & branch
    tasks = list(
        scoped_qs(ctx, Task)
        .filter(cover, retired_in__isnull=True, is_summary=False)
        .select_related("project")
        .order_by("project__name", "project_id", "sort_order")
    )
    pct = validated_pct_by_task(scoped_qs(ctx, Visit), task_ids=[t.pk for t in tasks])
    return [
        {
            "id": str(t.pk),
            "project_id": str(t.project_id),
            "project_name": t.project.name,
            "wbs": t.wbs,
            "outline_number": t.outline_number,
            "name": t.name,
            "last_validated_pct": float(pct[t.pk]) if t.pk in pct else None,
        }
        for t in tasks
    ]


def list_version(items: list[dict[str, Any]]) -> str:
    """Huella SHA-256 de la lista: cambia si cambia cualquier dato entregado."""
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Esquemas y router**

`server/visitacampo/projects/schemas.py`:

```python
"""Esquemas de `/v1/projects/*` (`components.schemas.FieldTask*` del contrato)."""

from __future__ import annotations

import uuid

from ninja import Schema

__all__ = ["FieldTask", "FieldTaskList"]


class FieldTask(Schema):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    wbs: str
    outline_number: str
    name: str
    last_validated_pct: float | None


class FieldTaskList(Schema):
    version: str
    changed: bool
    items: list[FieldTask]
```

`server/visitacampo/projects/api.py`:

```python
"""Router de `/v1/projects/*` (avance de campo). Solo traducción."""

from __future__ import annotations

from ninja import Query, Router

from visitacampo.core.context import get_request_context

from . import field_tasks
from .schemas import FieldTaskList

__all__ = ["router"]

router = Router(tags=["projects"])


@router.get(
    "/projects/tasks",
    response={200: FieldTaskList},
    summary="Tareas del cronograma de las que quien llama es responsable",
    operation_id="projectsTasksList",
)
def list_field_tasks(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/projects/tasks?version=`: la lista, o "sin cambios" si `version` coincide."""
    items = field_tasks.field_tasks(get_request_context(request))
    current = field_tasks.list_version(items)
    if version == current:
        return {"version": current, "changed": False, "items": []}
    return {"version": current, "changed": True, "items": items}
```

En `server/config/api.py`, junto a los demás imports de routers:

```python
from visitacampo.projects.api import router as projects_router
```

y después de `api.add_router("", layers_router)`:

```python
api.add_router("", projects_router)
```

- [ ] **Step 6: Verificar que pasan**

Run: `infra/scripts/test_docker.sh visitacampo/projects visitacampo/core/tests/test_policies.py visitacampo/core/tests/test_architecture.py`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/core/policies.py server/visitacampo/core/tests/test_policies.py server/config/api.py server/visitacampo/projects/field_tasks.py server/visitacampo/projects/schemas.py server/visitacampo/projects/api.py server/visitacampo/projects/tests/test_api.py
git commit -m "feat(projects): list the caller's schedule tasks for field progress (GET /v1/projects/tasks)"
```

---

### Task A9: Ficha de tarea — visitas vinculadas

**Files:**
- Modify: `server/visitacampo/projects/views.py`, `server/visitacampo/core/templates/suno/projects/task.html`
- Test: `server/visitacampo/projects/tests/test_field_progress.py`

- [ ] **Step 1: Prueba que falla**

Al final de `test_field_progress.py`:

```python
@skip_without_db
class TaskPageFieldProgressTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/tareas/{self.world.task(3).pk}/"

    def test_shows_linked_visits_with_status_and_regression(self) -> None:
        w = self.world
        w.approve(w.visit(3, "60", hours=1))
        w.visit(3, "40", hours=2)
        self.client.force_login(w.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Avance de campo")
        self.assertContains(response, "% SUNO (última visita validada): <strong>60 %</strong>", html=False)
        self.assertContains(response, "Validada")
        self.assertContains(response, "Sin revisar")
        self.assertContains(response, "Menor que el último validado (60 %)")
        self.assertContains(response, w.agent.username)

    def test_without_visits(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.url)
        self.assertContains(response, "Sin visitas vinculadas.")
```

- [ ] **Step 2: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: FAIL — no aparece "Avance de campo".

- [ ] **Step 3: Vista**

En `server/visitacampo/projects/views.py`, imports: añadir `Visit` a `visitacampo.core.models` y

```python
from .field_progress import progress_visits, validated_pct_by_task
```

En `task_detail`, en el diccionario de contexto del `_render` final, añadir:

```python
            "suno_pct": validated_pct_by_task(
                scoped_qs(ctx, Visit), task_ids=[task.pk]
            ).get(task.pk),
            "field_visits": progress_visits(ctx, task),
```

- [ ] **Step 4: Plantilla**

En `server/visitacampo/core/templates/suno/projects/task.html`, antes de `{% endblock %}`:

```django
<section class="pj-panel">
  <h2>Avance de campo</h2>
  <p>% SUNO (última visita validada): <strong>{% if suno_pct is not None %}{{ suno_pct|floatformat:"-2" }} %{% else %}—{% endif %}</strong></p>
  {% if field_visits %}
  <table class="pj-table">
    <thead><tr><th>Fecha</th><th>Autor</th><th>% acumulado</th><th>Revisión</th><th>Aviso</th></tr></thead>
    <tbody>
      {% for row in field_visits %}
      <tr>
        <td>{{ row.visit.captured_at|date:"d/m/Y H:i" }}</td>
        <td>{{ row.visit.created_by.display_name|default:row.visit.created_by.username }}</td>
        <td>{{ row.visit.progress_pct|floatformat:"-2" }} %</td>
        <td>{% if request.user.is_staff %}<a href="{% url 'admin:core_visit_change' row.visit.pk %}">{{ row.status_label }}</a>{% else %}{{ row.status_label }}{% endif %}</td>
        <td>{% if row.regression %}<span class="pj-late">Menor que el último validado ({{ row.previous_validated_pct|floatformat:"-2" }} %)</span>{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="pj-muted">Sin visitas vinculadas.</p>
  {% endif %}
</section>
```

- [ ] **Step 5: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/projects/views.py server/visitacampo/core/templates/suno/projects/task.html server/visitacampo/projects/tests/test_field_progress.py
git commit -m "feat(projects): show linked field visits and regressions on the task page"
```

---

### Task A10: Admin de visitas — tarea, % y aviso para la supervisión

**Files:**
- Modify: `server/visitacampo/core/admin.py`
- Test: `server/visitacampo/backoffice/tests/test_field_progress_admin.py`

- [ ] **Step 1: Prueba que falla**

`server/visitacampo/backoffice/tests/test_field_progress_admin.py`:

```python
"""Admin de visitas: la supervisión ve la tarea, el % y el aviso de retroceso."""

from __future__ import annotations

import unittest

from django.contrib.auth.models import Permission
from django.test import TestCase

from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.tests.factories import make_field_world

from .factories import admin_client

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

URL = "/admin/core/visit/"


@skip_without_db
class VisitAdminFieldProgressTests(TestCase):
    def setUp(self) -> None:
        self.world = make_field_world()
        staff = self.world.supervisor
        staff.is_staff = True
        staff.save()
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label="core"))
        self.client_ = admin_client(staff)
        self.high = self.world.visit(3, "60", hours=1)
        self.world.approve(self.high)
        self.low = self.world.visit(3, "40", hours=2)

    def test_list_shows_task_progress_and_regression(self) -> None:
        content = self.client_.get(URL).content.decode()
        self.assertIn("1.2 Descapote · 40 %", content)
        self.assertIn("menor que el último validado (60 %)", content)

    def test_filter_lists_only_regressions(self) -> None:
        content = self.client_.get(URL, {"avance": "menor"}).content.decode()
        self.assertIn(str(self.low.pk), content)
        self.assertNotIn(str(self.high.pk), content)

    def test_detail_shows_the_warning(self) -> None:
        content = self.client_.get(f"{URL}{self.low.pk}/change/").content.decode()
        self.assertIn("menor que el último validado (60 %)", content)
```

- [ ] **Step 2: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/backoffice/tests/test_field_progress_admin.py`
Expected: FAIL — el texto no aparece.

- [ ] **Step 3: Implementar**

En `server/visitacampo/core/admin.py`:

Imports: añadir `from decimal import Decimal` y

```python
from visitacampo.projects.field_progress import (
    is_regression,
    review_status,
    with_previous_validated_pct,
    with_review,
)
```

(`Max` y `Q` dejan de usarse en `VisitAdmin.get_queryset`; quitarlos de la importación si `ruff` los marca sin uso. `F` sigue usándose en `ReviewStatusFilter`.)

Reemplazar el cuerpo de `_latest_review` por:

```python
def _latest_review(visit: Visit) -> str | None:
    """Decisión vigente: la más reciente (`projects.field_progress.review_status`)."""
    return review_status(visit)
```

Después de `_latest_review`, añadir:

```python
def _pct(value: Decimal) -> str:
    """`Decimal('40.50')` → `40,5 %`."""
    return f"{value.normalize():f}".replace(".", ",") + " %"


class ProgressRegressionFilter(admin.SimpleListFilter):
    """Costos, etapa 1: visitas vinculadas a una tarea y las que retroceden."""

    title = "avance de cronograma"
    parameter_name = "avance"

    def lookups(self, request: Any, model_admin: Any) -> Any:
        return (("tarea", "Vinculadas a una tarea"), ("menor", "Menor que el último validado"))

    def queryset(self, request: Any, queryset: QuerySet[Visit]) -> QuerySet[Visit]:
        if self.value() == "tarea":
            return queryset.filter(task__isnull=False)
        if self.value() == "menor":
            return queryset.filter(progress_pct__lt=F("previous_validated_pct"))
        return queryset
```

En `VisitAdmin`:

```python
    list_display = ("captured_at", "template", "created_by", "progress", "review", "version")
    list_filter = (
        ReviewStatusFilter,
        ProgressRegressionFilter,
        "captured_at",
        "template_version__template",
    )
    list_select_related = ("template_version", "created_by", "task")
```

En `fields`, después de `"answers_pretty",` añadir `"task", "progress",` (quedan en `readonly_fields` por ser `fields`).

`get_queryset` pasa a:

```python
    def get_queryset(self, request: Any) -> QuerySet[Visit]:
        base = super().get_queryset(request)
        return with_previous_validated_pct(with_review(base), base)
```

Y un método nuevo junto a `review`:

```python
    @admin.display(description="avance de cronograma")
    def progress(self, visit: Visit) -> str:
        if visit.task_id is None or visit.progress_pct is None:
            return "—"
        text = f"{visit.task} · {_pct(visit.progress_pct)}"
        previous = getattr(visit, "previous_validated_pct", None)
        if is_regression(visit.progress_pct, previous):
            text += f" ⚠ menor que el último validado ({_pct(previous)})"
        return text
```

- [ ] **Step 4: Verificar que pasa (y que el Admin existente sigue)**

Run: `infra/scripts/test_docker.sh visitacampo/backoffice visitacampo/core`
Expected: todo pasa (incluidos `test_admin_scoping.py` y el filtro de revisión).

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/core/admin.py server/visitacampo/backoffice/tests/test_field_progress_admin.py
git commit -m "feat(backoffice): show task progress and flag regressions in visit review"
```

---

### Task A11: Vista previa de importación — retiradas con avance de campo

**Files:**
- Modify: `server/visitacampo/projects/importer.py`
- Test: `server/visitacampo/projects/tests/test_field_progress.py`

- [ ] **Step 1: Prueba que falla**

Al final de `test_field_progress.py`:

```python
@skip_without_db
class ImportPreviewFieldProgressTests(TestCase):
    def test_warns_about_retired_tasks_with_field_progress(self) -> None:
        w = make_field_world()
        w.visit(5, "10", hours=1)
        staged = stage_import(w.ctx(), w.project, file_name="v2.xml", data=v2())
        self.assertIn(
            "1 tarea(s) retirada(s) con avance de campo: 2.1 Columnas.", staged.summary["warnings"]
        )

    def test_summarize_changes_keeps_its_old_signature(self) -> None:
        summary = summarize_changes({}, parse_mspdi(v2()), set())
        self.assertNotIn("avance de campo", " ".join(summary["warnings"]))
```

Imports:

```python
from visitacampo.projects.importer import stage_import, summarize_changes
from visitacampo.projects.mspdi import parse_mspdi

from .samples import v2
```

- [ ] **Step 2: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/projects/tests/test_field_progress.py`
Expected: FAIL — el aviso no está.

- [ ] **Step 3: Implementar**

En `server/visitacampo/projects/importer.py`:

- `from collections.abc import Collection, Mapping` y añadir `Visit` a `visitacampo.core.models`.
- Firma de `summarize_changes`:

```python
def summarize_changes(
    existing: Mapping[int, dict[str, Any]],
    parsed: ParsedSchedule,
    responsible_uids: set[int],
    progress_uids: Collection[int] = frozenset(),
) -> dict[str, Any]:
    """Qué cambiaría al aplicar `parsed`.

    `existing` son las tareas del proyecto por `uid`: los `TASK_FIELDS` más
    `retired` (bool). `responsible_uids`: uids de tareas con responsables.
    `progress_uids`: uids de tareas con visitas vinculadas (avance de campo).
    """
```

- En el bucle de retiradas, junto a `retired_with_responsibles`:

```python
    retired_with_responsibles: list[str] = []
    retired_with_progress: list[str] = []
    for uid, current in existing.items():
        if uid in parsed_uids or current["retired"]:
            continue
        label = _label(current["outline_number"], current["name"])
        note("retired", label)
        if uid in responsible_uids:
            retired_with_responsibles.append(label)
        if uid in progress_uids:
            retired_with_progress.append(label)
```

- Después del aviso de responsables:

```python
    if retired_with_progress:
        warnings.append(
            f"{len(retired_with_progress)} tarea(s) retirada(s) con avance de campo: "
            + ", ".join(retired_with_progress[:MAX_EXAMPLES])
            + "."
        )
```

- En `stage_import`, después de calcular `responsible_uids`:

```python
    progress_uids = set(
        scoped_qs(ctx, Visit)
        .filter(task__project=project, deleted_at__isnull=True)
        .values_list("task__uid", flat=True)
    )
    summary = summarize_changes(_existing(ctx, project), parsed, responsible_uids, progress_uids)
```

(reemplaza la línea `summary = summarize_changes(_existing(ctx, project), parsed, responsible_uids)`).

- [ ] **Step 4: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/projects`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/projects/importer.py server/visitacampo/projects/tests/test_field_progress.py
git commit -m "feat(projects): warn when an import retires tasks with field progress"
```

---

### Task A12: `bootstrap_demo --with-schedule` para la E2E

**Files:**
- Modify: `server/visitacampo/management/commands/bootstrap_demo.py`, `docs/testing/e2e-fase0.md`
- Test: `server/visitacampo/core/tests/test_bootstrap_demo.py`

Qué necesita la E2E del servidor: además de la organización, el agente y la plantilla, **un proyecto con una tarea de detalle vigente de la que el agente sea responsable**. Se siembra la responsabilidad en la **etapa** (resumen) para que la E2E ejercite también la herencia por rama:

- `Project` "Obra E2E <org>" (código `E2E`), creado por el agente;
- `Task` uid 1, `1` "Preliminares", resumen, nivel 1 (su propia etapa);
- `Task` uid 2, `1.1` "Excavación", detalle, hija y de la etapa 1;
- `TaskResponsible` (etapa 1 ↔ agente).

- [ ] **Step 1: Prueba que falla**

Al final de `BootstrapDemoTests` en `server/visitacampo/core/tests/test_bootstrap_demo.py`:

```python
    def test_with_schedule_seeds_a_task_the_agent_can_report(self) -> None:
        out = io.StringIO()
        call_command(
            "bootstrap_demo",
            "--org-name=Org cronograma test",
            "--username=agente-cronograma",
            "--password=una-contraseña-de-prueba-larga-42",
            "--with-schedule",
            stdout=out,
        )
        org = Organization.objects.get(name="Org cronograma test")
        user = get_user_model().objects.get(username="agente-cronograma")
        project = Project.objects.get(org=org)
        leaf = Task.objects.get(project=project, uid=2)
        self.assertEqual((leaf.wbs, leaf.name, leaf.is_summary), ("1.1", "Excavación", False))
        self.assertEqual(leaf.parent.uid, 1)
        self.assertTrue(TaskResponsible.objects.filter(task__uid=1, task__project=project, user=user).exists())
        self.assertIn(str(leaf.id), out.getvalue())

    def test_without_the_flag_there_is_no_project(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org sin cronograma",
            "--username=agente-sin-cronograma",
            "--password=una-contraseña-de-prueba-larga-42",
            stdout=io.StringIO(),
        )
        self.assertFalse(Project.objects.filter(org__name="Org sin cronograma").exists())
```

Imports: `from visitacampo.core.models import FormTemplateVersion, Membership, Organization, Project, Role, Task, TaskResponsible`.

- [ ] **Step 2: Verificar que falla**

Run: `infra/scripts/test_docker.sh visitacampo/core/tests/test_bootstrap_demo.py`
Expected: FAIL — `unrecognized arguments: --with-schedule`.

- [ ] **Step 3: Implementar**

En `bootstrap_demo.py`, imports: añadir `Project, Task, TaskResponsible` a `visitacampo.core.models`. En `add_arguments`, al final:

```python
        parser.add_argument(
            "--with-schedule",
            action="store_true",
            help=(
                "Siembra además un proyecto con la etapa 1 'Preliminares' y la tarea "
                "1.1 'Excavación'; el agente es responsable de la etapa (hereda la "
                "tarea). Lo usa la E2E del avance de campo."
            ),
        )
```

En `handle`, dentro del `with transaction.atomic():`, después de crear `template_version`:

```python
            leaf = None
            if options["with_schedule"]:
                project = Project.objects.create(
                    org=org, name=f"Obra E2E {org_name}"[:200], code="E2E", created_by=user
                )
                stage = Task.objects.create(
                    org=org,
                    project=project,
                    uid=1,
                    wbs="1",
                    outline_number="1",
                    outline_level=1,
                    sort_order=1,
                    name="Preliminares",
                    is_summary=True,
                )
                stage.stage = stage
                stage.save(update_fields=["stage"])
                leaf = Task.objects.create(
                    org=org,
                    project=project,
                    uid=2,
                    wbs="1.1",
                    outline_number="1.1",
                    outline_level=2,
                    sort_order=2,
                    parent=stage,
                    stage=stage,
                    name="Excavación",
                )
                TaskResponsible.objects.create(org=org, task=stage, user=user, assigned_by=user)
```

Y al final de `handle`:

```python
        if leaf is not None:
            self.stdout.write(f"  Tarea del cronograma (E2E): 1.1 Excavación ({leaf.id})")
```

Actualizar el docstring del módulo: un punto 5 "Con `--with-schedule`: un `Project` con la etapa 1 y la tarea 1.1, y la responsabilidad del agente sobre la etapa (avance de campo, costos etapa 1)".

- [ ] **Step 4: Procedimiento E2E**

En `docs/testing/e2e-fase0.md`, reemplazar:

```markdown
3. Siembra una organización y un usuario únicos (`bootstrap_demo`).
```

por:

```markdown
3. Siembra una organización y un usuario únicos (`bootstrap_demo --with-schedule`): además de
   la plantilla, un proyecto con la etapa 1 "Preliminares" (el agente es su responsable) y la
   tarea de detalle 1.1 "Excavación", que hereda la responsabilidad.
```

En el punto 4, después de "captura una visita con una marca única," añadir "la vincula a la tarea 1.1 con 35 % de avance acumulado,", y el punto 5 pasa a "Verifica en el servidor que la visita existe en su organización con `version` y `change_seq` asignados, vinculada a la tarea 1.1 con `progress_pct` 35."

- [ ] **Step 5: Verificar que pasa**

Run: `infra/scripts/test_docker.sh visitacampo/core/tests/test_bootstrap_demo.py`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/management/commands/bootstrap_demo.py server/visitacampo/core/tests/test_bootstrap_demo.py docs/testing/e2e-fase0.md
git commit -m "feat(demo): seed a schedule task for the field progress E2E"
```

---

### Task A13: Verificación completa, registro y PR del servidor

**Files:**
- Modify: `plans/visitacampo-progreso.md`

- [ ] **Step 1: Verificación completa**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pytest -q -p no:cacheprovider --cov --cov-config=pyproject.toml --cov-report=term"
```

Expected: suite en verde, ruff y mypy limpios, sin migraciones pendientes, cobertura ≥ el suelo de `pyproject.toml`. Validar de nuevo el contrato (comando de "Cómo correr las pruebas").

- [ ] **Step 2: Prueba manual**

Con el stack de desarrollo (`infra/README.md`): importar `samples.v1()` en un proyecto, asignar un agente a la etapa 1, crear dos visitas vinculadas por push (o desde la app con la Parte B), validar la de mayor % en el Admin y comprobar: columna y filtro "avance de cronograma" en el Admin, aviso en el detalle, sección "Avance de campo" en la ficha de la tarea, % SUNO en cronograma, lista, Gantt y Excel, y el aviso al importar `v2` con avance en la 2.1. `curl` a `/v1/projects/tasks` con el token del agente, dos veces (la segunda con `?version=`). Al terminar, `infra/scripts/test_docker.sh --down`.

- [ ] **Step 3: Registrar el avance**

En `plans/visitacampo-progreso.md`, sección "Módulo de costos — Etapa 1 (Cronograma), avance desde campo — servidor": fecha, PR, Tasks A1–A12, números reales de la verificación, las decisiones de "Riesgos y decisiones tomadas al planear" que cambian la spec (1, 4, 7, 8, 13) y "Pendiente: app (Parte B del mismo plan)".

- [ ] **Step 4: Commit y PR**

```bash
git add plans/visitacampo-progreso.md
git commit -m "docs: record stage 1 field progress server work"
git push -u origin feat/costos-campo-servidor
gh pr create --base master --title "feat(projects): field progress from visits, stage 1 (server)" --body-file -
```

Cuerpo del PR (resumen, compatibilidad hacia atrás —campos opcionales, clientes anteriores sin cambios—, migración `0012`, contrato, cómo probar), terminando con la línea de atribución de PRs indicada en la sesión. Tras el merge, anotar el **SHA del merge**: la Parte B fija su `contract/` a ese commit.

---

# Parte B — App (`SUNO_client`)

Requisito: PR 1 fusionado y desplegado en el entorno contra el que se prueba. Todas las rutas son relativas a la raíz de `SUNO_client`.

### Task B0: Rama y contrato

**Files:**
- Modify: `contract/v1.yaml`, `contract/SOURCE` (y `contract/fixtures/forms_parity.json` si cambió)

- [ ] **Step 1: Rama y copia del contrato** (procedimiento de `contract/README.md`)

```bash
git checkout master && git pull && git checkout -b feat/costos-campo-app
REF=<SHA del merge del PR 1 en SUNO_server>
for f in v1.yaml fixtures/forms_parity.json; do
  curl -fsSL "https://raw.githubusercontent.com/PMAPP-Org/SUNO_server/${REF}/server/openapi/${f}" -o "contract/${f}"
done
sed -i "s|^ref=.*|ref=${REF}|" contract/SOURCE
flutter test test/core/forms/
```

Expected: `git diff contract/v1.yaml` muestra solo `task_id`/`progress_pct` y `/v1/projects/tasks`; la paridad pasa. (`REF` es el único valor que se conoce al ejecutar: el SHA anotado en la Task A13.)

- [ ] **Step 2: Commit**

```bash
git add contract
git commit -m "chore(contract): pin the server contract with field progress"
```

---

### Task B1: Esquema Drift v9

**Files:**
- Modify: `lib/data/local/tables.dart`, `lib/data/local/database.dart`
- Test: `test/data/local/migration_test.dart`

- [ ] **Step 1: Prueba que falla**

Al final de `main()` en `test/data/local/migration_test.dart`:

```dart
  test('v8 → v9: las visitas siguen sin tarea, nacen las tablas de tareas y el pull vuelve a empezar', () async {
    final raw = sqlite3.sqlite3.openInMemory();
    addTearDown(raw.close);

    // Esquema v8 (Fase B): v7 + `visits.assignment_id` y `assignments`.
    raw.execute('''
      CREATE TABLE visits (
        id TEXT NOT NULL PRIMARY KEY, org_id TEXT NOT NULL, version INTEGER NOT NULL,
        change_seq INTEGER NULL, deleted_at INTEGER NULL, template_version_id TEXT NOT NULL,
        answers_json TEXT NOT NULL, lat REAL NULL, lon REAL NULL, accuracy_m REAL NULL,
        altitude_m REAL NULL, captured_at INTEGER NOT NULL, device_id TEXT NOT NULL,
        created_by TEXT NOT NULL, server_data_json TEXT NULL, assignment_id TEXT NULL
      );
      CREATE TABLE outbox_ops (
        op_uuid TEXT NOT NULL PRIMARY KEY, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
        op_type TEXT NOT NULL, base_version INTEGER NULL, payload_json TEXT NOT NULL,
        state TEXT NOT NULL, created_at INTEGER NOT NULL, error_message TEXT NULL,
        error_code TEXT NULL, error_fields_json TEXT NULL, server_row_json TEXT NULL
      );
      CREATE TABLE attachment_queue (
        sha256 TEXT NOT NULL PRIMARY KEY, local_path TEXT NOT NULL, state TEXT NOT NULL,
        created_at INTEGER NOT NULL, mime TEXT NOT NULL DEFAULT 'image/jpeg',
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NULL, last_error TEXT NULL
      );
      CREATE TABLE sync_cursor (id INTEGER NOT NULL PRIMARY KEY, cursor TEXT NOT NULL, updated_at INTEGER NOT NULL);
      CREATE TABLE form_template_versions (
        id TEXT NOT NULL PRIMARY KEY, org_id TEXT NOT NULL, template_id TEXT NOT NULL,
        version_number INTEGER NOT NULL, name TEXT NULL, definition_json TEXT NOT NULL,
        published_at INTEGER NULL, deleted_at INTEGER NULL, version INTEGER NOT NULL,
        change_seq INTEGER NULL
      );
      CREATE TABLE visit_attachments (
        id TEXT NOT NULL PRIMARY KEY, org_id TEXT NOT NULL, visit_id TEXT NOT NULL,
        sha256 TEXT NOT NULL, role TEXT NOT NULL, caption TEXT NULL, captured_at INTEGER NOT NULL,
        position INTEGER NOT NULL, version INTEGER NULL, change_seq INTEGER NULL
      );
      CREATE TABLE assignments (
        id TEXT NOT NULL PRIMARY KEY, org_id TEXT NOT NULL, version INTEGER NOT NULL,
        change_seq INTEGER NULL, template_version_id TEXT NOT NULL, assigned_to TEXT NOT NULL,
        title TEXT NULL, scheduled_for INTEGER NULL, lat REAL NULL, lon REAL NULL
      );
    ''');
    raw.execute('PRAGMA user_version = 8;');
    raw.execute(
      "INSERT INTO visits (id, org_id, version, template_version_id, answers_json, captured_at, device_id, created_by, assignment_id) "
      "VALUES ('visit-1', 'org-1', 2, 'tv-1', '{}', 1767225600, 'device-1', 'user-1', 'asg-1');",
    );
    raw.execute("INSERT INTO sync_cursor (id, cursor, updated_at) VALUES (0, 'c1:00000000000000000099', 1767225600);");

    final db = AppDatabase.forTesting(NativeDatabase.opened(raw));
    addTearDown(db.close);

    final visit = await db.select(db.visits).getSingle();
    expect((visit.assignmentId, visit.taskId, visit.progressPct), ('asg-1', null, null));
    expect(await db.select(db.scheduleTasks).get(), isEmpty);
    expect(await db.readScheduleTaskState(), isNull);
    expect(await db.readCursor(), isNull, reason: 'el pull vuelve a traer las visitas, ahora con su tarea');
  });
```

- [ ] **Step 2: Verificar que falla**

Run: `flutter test test/data/local/migration_test.dart`
Expected: FAIL de compilación (`taskId`, `scheduleTasks`, `readScheduleTaskState` no existen).

- [ ] **Step 3: Tablas**

En `lib/data/local/tables.dart`, dentro de `class Visits`, después de `assignmentId`:

```dart
  /// Costos, etapa 1 (`schemaVersion` 9): la tarea del cronograma cuyo avance
  /// reporta la visita y el % acumulado. Los dos o ninguno, como en el
  /// servidor (`visit_task_progress_pair`).
  TextColumn get taskId => text().nullable()();
  RealColumn get progressPct => real().nullable()();
```

Al final del archivo:

```dart
/// Avance de campo (costos, etapa 1): tareas del cronograma de las que el
/// usuario con sesión es responsable, tal como las entrega
/// `GET /v1/projects/tasks`. `ScheduleTaskSync` las reemplaza enteras cuando la
/// lista cambia: lo que deja de venir (tarea retirada, responsabilidad
/// quitada) desaparece. Añadida en `schemaVersion` 9.
@DataClassName('ScheduleTaskRow')
class ScheduleTasks extends Table {
  TextColumn get id => text()();
  TextColumn get orgId => text()();

  /// De quién es la lista: en un teléfono compartido solo se ofrecen las del
  /// usuario con sesión (`ScheduleTaskRepository.forUser`).
  TextColumn get userId => text()();
  TextColumn get projectId => text()();
  TextColumn get projectName => text()();
  TextColumn get wbs => text()();
  TextColumn get outlineNumber => text()();
  TextColumn get name => text()();

  /// % de la última visita validada de la tarea; `null` si no hay.
  RealColumn get lastValidatedPct => real().nullable()();

  /// Orden en que llegó (proyecto y orden del Project).
  IntColumn get position => integer()();

  @override
  Set<Column> get primaryKey => {id};
}

/// `version` de la última lista de tareas descargada (fila única, `id` = 0):
/// se reenvía para que el servidor conteste "sin cambios". `schemaVersion` 9.
@DataClassName('ScheduleTaskListState')
class ScheduleTaskState extends Table {
  IntColumn get id => integer().clientDefault(() => 0)();
  TextColumn get orgId => text()();
  TextColumn get userId => text()();
  TextColumn get version => text()();
  DateTimeColumn get updatedAt => dateTime()();

  @override
  Set<Column> get primaryKey => {id};
}
```

- [ ] **Step 4: Base de datos y migración**

En `lib/data/local/database.dart`:

```dart
@DriftDatabase(
  tables: [
    Visits,
    OutboxOps,
    AttachmentQueue,
    SyncCursor,
    FormTemplateVersions,
    VisitAttachments,
    Assignments,
    ScheduleTasks,
    ScheduleTaskState,
  ],
)
```

`schemaVersion` pasa a `9`. En `onUpgrade`, después del bloque `if (from < 8) {...}`:

```dart
      // v8 → v9 (costos, etapa 1): tarea y % de cada visita, y la lista de
      // tareas del cronograma del agente. El cursor se reinicia por lo mismo
      // que en la v8: las visitas ya bajadas no traían su tarea.
      if (from < 9) {
        await migrator.addColumn(visits, visits.taskId);
        await migrator.addColumn(visits, visits.progressPct);
        await migrator.createTable(scheduleTasks);
        await migrator.createTable(scheduleTaskState);
        await delete(syncCursor).go();
      }
```

Después de `resetCursor()`:

```dart
  /// La versión de la última lista de tareas del cronograma, si hay.
  Future<ScheduleTaskListState?> readScheduleTaskState() =>
      (select(scheduleTaskState)..where((t) => t.id.equals(0))).getSingleOrNull();

  /// Reemplaza la lista de tareas del cronograma y su versión (costos, etapa
  /// 1) en una sola transacción: nunca queda media lista.
  Future<void> replaceScheduleTasks({
    required String orgId,
    required String userId,
    required String version,
    required List<ScheduleTasksCompanion> rows,
    required DateTime now,
  }) {
    return transaction(() async {
      await delete(scheduleTasks).go();
      await batch((b) => b.insertAll(scheduleTasks, rows));
      await into(scheduleTaskState).insertOnConflictUpdate(
        ScheduleTaskStateCompanion.insert(orgId: orgId, userId: userId, version: version, updatedAt: now),
      );
    });
  }
```

- [ ] **Step 5: Generar y verificar**

Run: `dart run build_runner build --delete-conflicting-outputs && flutter test test/data/local/`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add lib/data/local/tables.dart lib/data/local/database.dart test/data/local/migration_test.dart
git commit -m "feat(db): schema v9 with schedule tasks and visit task progress"
```

---

### Task B2: `TaskProgress` y el pull guarda la tarea; "Para revisar" la entiende

**Files:**
- Modify: `lib/core/capture/visit_capture.dart` (solo la clase `TaskProgress`), `lib/core/sync/sync_engine.dart`, `lib/core/sync/quarantine_service.dart`
- Test: `test/core/sync/pull_task_progress_test.dart`, `test/core/sync/quarantine_task_test.dart`

- [ ] **Step 1: `TaskProgress`**

En `lib/core/capture/visit_capture.dart`, después de `class CaptureIdentity {...}`:

```dart
/// Costos, etapa 1: la tarea del cronograma y el % acumulado que reporta una
/// visita. Viajan siempre juntos (`task_id` + `progress_pct`).
@immutable
class TaskProgress {
  const TaskProgress({required this.taskId, required this.pct});
  final String taskId;
  final double pct;
}
```

- [ ] **Step 2: Pruebas que fallan**

`test/core/sync/pull_task_progress_test.dart`:

```dart
// Costos, etapa 1: las filas `visit` del servidor traen `task_id` y
// `progress_pct`; el teléfono las guarda (una fila de un servidor anterior, sin
// esas claves, deja la visita sin tarea).
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/sync/result.dart';
import 'package:visitacampo/core/sync/sync_engine.dart';
import 'package:visitacampo/core/sync/sync_transport.dart';
import 'package:visitacampo/data/local/database.dart';

import 'fake_sync_transport.dart';

Map<String, dynamic> _row() => {
  'id': 'visit-1',
  'org_id': 'org-1',
  'version': 1,
  'change_seq': 42,
  'deleted_at': null,
  'assignment_id': null,
  'template_version_id': 'tv-1',
  'answers': <String, dynamic>{},
  'lat': null,
  'lon': null,
  'accuracy_m': null,
  'altitude_m': null,
  'captured_at': '2026-11-01T10:00:00.000Z',
  'device_id': 'device-1',
  'created_by': 'user-1',
};

void main() {
  late AppDatabase db;
  late FakeSyncTransport transport;
  late SyncEngine engine;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeSyncTransport();
    engine = SyncEngine(db: db, transport: transport, deviceId: 'device-1');
  });
  tearDown(() => db.close());

  Future<Visit> pullRow(Map<String, dynamic> row) async {
    transport.pullResponses.add(
      Result.ok(
        PullResponse(
          rows: [PullRow(entity: 'visit', data: row)],
          tombstones: const [],
          nextCursor: 'c1:00000000000000000042',
          hasMore: false,
        ),
      ),
    );
    await engine.pull();
    return db.select(db.visits).getSingle();
  }

  test('guarda la tarea y el % de la visita', () async {
    final visit = await pullRow({..._row(), 'task_id': 'task-1', 'progress_pct': 40});
    expect((visit.taskId, visit.progressPct), ('task-1', 40.0));
  });

  test('una fila sin esas claves (servidor anterior) deja la visita sin tarea', () async {
    final visit = await pullRow(_row());
    expect((visit.taskId, visit.progressPct), (null, null));
  });
}
```

`test/core/sync/quarantine_task_test.dart`:

```dart
// Costos, etapa 1: una visita rechazada por su tarea (retirada, ya no es
// responsable…) se reenvía sin tarea; "enviar la mía" manda el par completo.
import 'dart:convert';

import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/capture/visit_capture.dart';
import 'package:visitacampo/core/forms/form_definition.dart';
import 'package:visitacampo/core/sync/quarantine_service.dart';
import 'package:visitacampo/core/sync/result.dart';
import 'package:visitacampo/core/sync/sync_engine.dart';
import 'package:visitacampo/core/sync/sync_transport.dart';
import 'package:visitacampo/data/local/database.dart';

import 'fake_sync_transport.dart';

final _definition =
    (parseFormDefinition({
              'fields': [
                {
                  'key': 'notas',
                  'type': 'text',
                  'label': {'es': 'Notas'},
                },
              ],
            })
            as DefinitionOk)
        .definition;

void main() {
  late AppDatabase db;
  late FakeSyncTransport transport;
  late SyncEngine engine;
  late QuarantineService service;
  var ids = 0;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeSyncTransport();
    ids = 0;
    engine = SyncEngine(db: db, transport: transport, deviceId: 'device-1', idGenerator: () => 'op-${ids++}');
    service = QuarantineService(db: db, engine: engine, clock: () => DateTime.utc(2026, 11, 2));
  });
  tearDown(() => db.close());

  Future<void> pushWith(PushOpOutcome Function(OutboxOp op) outcome) async {
    final pending = await db.select(db.outboxOps).get();
    transport.pushResponses.add(
      Result.ok(PushResponse(schemaVersion: 1, results: [for (final op in pending) outcome(op)])),
    );
    await engine.drainOutbox();
  }

  test('rechazo por la tarea: se quita la tarea y se reenvía', () async {
    final visitId = await VisitCapture(db: db, idGenerator: () => 'cap-${ids++}').save(
      identity: const CaptureIdentity(orgId: 'org-1', userId: 'user-1', deviceId: 'device-1'),
      templateVersionId: 'tv-1',
      definition: _definition,
      answers: {'notas': 'x'},
      captured: const {},
      progress: const TaskProgress(taskId: 'task-1', pct: 35),
    );
    await pushWith(
      (op) => PushOpOutcome(
        opUuid: op.opUuid,
        status: 'rejected',
        error: const PushOpError(
          code: 'validation_error',
          message: 'The task was retired from the schedule.',
          details: {'field': 'task_id', 'reason': 'task_retired'},
        ),
      ),
    );
    final item = (await service.list(orgId: 'org-1')).single;
    expect(item.taskRejected, isTrue);

    await service.unlinkTask(item);

    final op = await db.select(db.outboxOps).getSingle();
    expect(op.state, 'pending');
    final payload = jsonDecode(op.payloadJson) as Map<String, dynamic>;
    expect(payload.containsKey('task_id'), isFalse);
    expect(payload.containsKey('progress_pct'), isFalse);
    final visit = await (db.select(db.visits)..where((t) => t.id.equals(visitId))).getSingle();
    expect((visit.taskId, visit.progressPct), (null, null));
  });

  test('conflicto: "enviar la mía" manda tarea y % juntos aunque solo cambie el %', () async {
    await db
        .into(db.visits)
        .insert(
          VisitsCompanion.insert(
            id: 'visit-1',
            orgId: 'org-1',
            version: 1,
            templateVersionId: 'tv-1',
            answersJson: '{}',
            capturedAt: DateTime.utc(2026, 11, 1),
            deviceId: 'device-1',
            createdBy: 'user-1',
            taskId: const Value('task-1'),
            progressPct: const Value(50.0),
          ),
        );
    await engine.enqueueVisitOp(
      entityId: 'visit-1',
      opType: 'update',
      baseVersion: 1,
      payload: {'id': 'visit-1', 'task_id': 'task-1', 'progress_pct': 50.0},
    );
    await pushWith(
      (op) => PushOpOutcome(
        opUuid: op.opUuid,
        status: 'conflict',
        serverVersion: 3,
        serverRow: {
          'entity': 'visit',
          'data': {
            'id': 'visit-1',
            'org_id': 'org-1',
            'version': 3,
            'change_seq': 9,
            'deleted_at': null,
            'assignment_id': null,
            'template_version_id': 'tv-1',
            'answers': <String, dynamic>{},
            'lat': null,
            'lon': null,
            'accuracy_m': null,
            'altitude_m': null,
            'captured_at': '2026-11-01T00:00:00.000Z',
            'device_id': 'device-1',
            'created_by': 'user-1',
            'task_id': 'task-1',
            'progress_pct': 30.0,
          },
        },
      ),
    );
    final item = (await service.list(orgId: 'org-1')).single;

    await service.resendMine(item);

    final op = await db.select(db.outboxOps).getSingle();
    expect(op.baseVersion, 3);
    expect(jsonDecode(op.payloadJson), {'id': 'visit-1', 'task_id': 'task-1', 'progress_pct': 50.0});
  });
}
```

- [ ] **Step 3: Verificar que fallan**

Run: `flutter test test/core/sync/pull_task_progress_test.dart test/core/sync/quarantine_task_test.dart`
Expected: FAIL (compilación: `save(progress:)` llega en B5; `taskRejected`, `unlinkTask` no existen). Implementar los pasos 4–5 ahora; estas pruebas quedan en verde al terminar B5 (allí se vuelven a correr).

- [ ] **Step 4: `SyncEngine`**

En `lib/core/sync/sync_engine.dart::_upsertVisitFromServerRow`, en `VisitsCompanion.insert(...)`, después de `assignmentId: ...`:

```dart
      // Costos, etapa 1. Un servidor anterior no envía las claves: `null`.
      taskId: Value(serverRow['task_id'] as String?),
      progressPct: Value((serverRow['progress_pct'] as num?)?.toDouble()),
```

- [ ] **Step 5: `QuarantineService`**

En `lib/core/sync/quarantine_service.dart`, dentro de `QuarantinedVisit`, después de `canCorrect`:

```dart
  /// El servidor rechazó la tarea del cronograma (retirada, ya no es
  /// responsable, % fuera de rango…): se puede reenviar sin ella.
  bool get taskRejected =>
      kind == QuarantineKind.rejected && violations.any((v) => v.field == 'task_id' || v.field == 'progress_pct');
```

En `resendMine`, el bucle de campos pasa a incluir el par, y el par viaja completo:

```dart
    for (final (key, mine) in [
      ('lat', visit.lat),
      ('lon', visit.lon),
      ('accuracy_m', visit.accuracyM),
      ('altitude_m', visit.altitudeM),
      ('task_id', visit.taskId),
      ('progress_pct', visit.progressPct),
    ]) {
      if (!_sameJson(mine, server[key])) changed[key] = mine;
    }
    // El servidor exige `task_id` y `progress_pct` juntos.
    if (changed.containsKey('task_id') || changed.containsKey('progress_pct')) {
      changed['task_id'] = visit.taskId;
      changed['progress_pct'] = visit.progressPct;
    }
```

Y un método nuevo después de `correct`:

```dart
  /// Rechazo por la tarea: la visita se queda sin tarea y se reenvía. En un
  /// alta los dos campos se quitan; en una edición viajan en `null`
  /// (desvincular, que no exige ser responsable).
  Future<void> unlinkTask(QuarantinedVisit item) => db.transaction(() async {
    await (db.update(db.visits)..where((t) => t.id.equals(item.visit.id))).write(
      const VisitsCompanion(taskId: Value(null), progressPct: Value(null)),
    );
    for (final op in item.ops) {
      if (op.entity == 'visit' && op.opType != 'delete') {
        final payload = (jsonDecode(op.payloadJson) as Map).cast<String, Object?>();
        if (op.opType == 'create') {
          payload
            ..remove('task_id')
            ..remove('progress_pct');
        } else if (payload.containsKey('task_id') || payload.containsKey('progress_pct')) {
          payload['task_id'] = null;
          payload['progress_pct'] = null;
        }
        await (db.update(db.outboxOps)..where((t) => t.opUuid.equals(op.opUuid))).write(
          OutboxOpsCompanion(payloadJson: Value(jsonEncode(payload))),
        );
      }
      await engine.retryQuarantined(op.opUuid);
    }
  });
```

- [ ] **Step 6: Commit** (las pruebas de este task se cierran en B5)

```bash
git add lib/core/sync/sync_engine.dart lib/core/sync/quarantine_service.dart lib/core/capture/visit_capture.dart test/core/sync/pull_task_progress_test.dart test/core/sync/quarantine_task_test.dart
git commit -m "feat(sync): keep visit task progress from the server and unlink rejected tasks"
```

---

### Task B3: Descarga de tareas (`ScheduleTaskSync`), cliente HTTP y repositorio

**Files:**
- Create: `lib/core/schedule/progress_input.dart`, `lib/core/schedule/schedule_task_sync.dart`, `lib/data/http/schedule_task_api_client.dart`, `lib/data/local/schedule_task_repository.dart`
- Test: `test/core/schedule/progress_input_test.dart`, `test/core/schedule/fake_schedule_task_transport.dart`, `test/core/schedule/schedule_task_sync_test.dart`, `test/data/http/schedule_task_api_client_test.dart`, `test/data/local/schedule_task_repository_test.dart`

- [ ] **Step 1: Pruebas que fallan**

`test/core/schedule/progress_input_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/schedule/progress_input.dart';

void main() {
  test('acepta 0..100 con hasta dos decimales, con coma o punto', () {
    expect(parseProgressPct('0'), 0);
    expect(parseProgressPct('35'), 35);
    expect(parseProgressPct(' 35,5 '), 35.5);
    expect(parseProgressPct('99.99'), 99.99);
    expect(parseProgressPct('100'), 100);
  });

  test('rechaza vacío, negativos, más de 100, tres decimales y texto', () {
    for (final text in ['', '-1', '100.01', '101', '33.333', 'abc', '1e2', '35%']) {
      expect(parseProgressPct(text), isNull, reason: text);
    }
  });

  test('se muestra sin ceros de más y con coma', () {
    expect(formatProgressPct(40), '40');
    expect(formatProgressPct(40.5), '40,5');
    expect(formatProgressPct(33.25), '33,25');
    expect(formatProgressPct(100), '100');
    expect(formatProgressPct(0), '0');
  });
}
```

`test/core/schedule/fake_schedule_task_transport.dart`:

```dart
import 'package:visitacampo/core/schedule/schedule_task_sync.dart';

class FakeScheduleTaskTransport implements ScheduleTaskTransport {
  /// `null` = sin red.
  final List<ScheduleTaskPage?> responses = [];
  final List<String?> versions = [];

  @override
  Future<ScheduleTaskPage?> fetch({String? version}) async {
    versions.add(version);
    if (responses.isEmpty) {
      throw StateError('FakeScheduleTaskTransport.fetch() sin respuesta programada.');
    }
    return responses.removeAt(0);
  }
}
```

`test/core/schedule/schedule_task_sync_test.dart`:

```dart
// Tareas del cronograma (costos, etapa 1): la lista se reemplaza entera cuando
// cambia, "sin cambios" no toca nada y sin red se conserva la anterior.
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/auth/auth_models.dart';
import 'package:visitacampo/core/schedule/schedule_task_sync.dart';
import 'package:visitacampo/data/local/database.dart';
import 'package:visitacampo/data/local/schedule_task_repository.dart';

import 'fake_schedule_task_transport.dart';

const _ana = UserProfile(id: 'ana', username: 'ana', orgId: 'org-1', roles: ['FIELD_AGENT']);
const _beto = UserProfile(id: 'beto', username: 'beto', orgId: 'org-1', roles: ['FIELD_AGENT']);

ScheduleTaskInfo _task(String id, String wbs, String name, {double? pct}) => ScheduleTaskInfo(
  id: id,
  projectId: 'p-1',
  projectName: 'Obra Norte',
  wbs: wbs,
  outlineNumber: wbs,
  name: name,
  lastValidatedPct: pct,
);

ScheduleTaskPage _page(String version, List<ScheduleTaskInfo> items) =>
    ScheduleTaskPage(version: version, changed: true, items: items);

const _unchanged = ScheduleTaskPage(version: 'v1', changed: false, items: []);

void main() {
  late AppDatabase db;
  late FakeScheduleTaskTransport transport;
  late ScheduleTaskSync sync;
  UserProfile? current;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeScheduleTaskTransport();
    current = _ana;
    sync = ScheduleTaskSync(
      db: db,
      transport: transport,
      currentUser: () => current,
      clock: () => DateTime.utc(2026, 11, 2),
    );
  });
  tearDown(() => db.close());

  Future<List<String>> labels(UserProfile user) async => [
    for (final t in await ScheduleTaskRepository(db).forUser(orgId: user.orgId, userId: user.id)) t.label,
  ];

  test('primera descarga: guarda la lista en orden, con su versión', () async {
    transport.responses.add(
      _page('v1', [_task('t-2', '1.1', 'Localización'), _task('t-3', '1.2', 'Descapote', pct: 40)]),
    );
    expect(await sync.refresh(), isTrue);
    expect(transport.versions, [null]);
    expect(await labels(_ana), ['1.1 Localización', '1.2 Descapote']);
    final items = await ScheduleTaskRepository(db).forUser(orgId: 'org-1', userId: 'ana');
    expect(items.last.lastValidatedPct, 40);
    expect((await db.readScheduleTaskState())!.version, 'v1');
  });

  test('sin cambios: envía la versión y no toca la lista', () async {
    transport.responses
      ..add(_page('v1', [_task('t-2', '1.1', 'Localización')]))
      ..add(_unchanged);
    await sync.refresh();
    expect(await sync.refresh(), isTrue);
    expect(transport.versions, [null, 'v1']);
    expect(await labels(_ana), ['1.1 Localización']);
  });

  test('lo que deja de venir desaparece (tarea retirada o responsabilidad quitada)', () async {
    transport.responses
      ..add(_page('v1', [_task('t-2', '1.1', 'Localización'), _task('t-5', '2.1', 'Columnas')]))
      ..add(_page('v2', [_task('t-2', '1.1', 'Localización (replanteo)')]));
    await sync.refresh();
    await sync.refresh();
    expect(await labels(_ana), ['1.1 Localización (replanteo)']);
  });

  test('sin red no se toca nada', () async {
    transport.responses
      ..add(_page('v1', [_task('t-2', '1.1', 'Localización')]))
      ..add(null);
    await sync.refresh();
    expect(await sync.refresh(), isFalse);
    expect(await labels(_ana), ['1.1 Localización']);
  });

  test('otro usuario en el mismo teléfono pide la lista completa', () async {
    transport.responses
      ..add(_page('v1', [_task('t-2', '1.1', 'Localización')]))
      ..add(_page('v9', [_task('t-6', '2.2', 'Fin de obra')]));
    await sync.refresh();
    current = _beto;
    expect(await labels(_beto), isEmpty, reason: 'la lista guardada es de ana');
    await sync.refresh();
    expect(transport.versions, [null, null]);
    expect(await labels(_beto), ['2.2 Fin de obra']);
    expect(await labels(_ana), isEmpty);
  });

  test('sin sesión no pide nada', () async {
    current = null;
    expect(await sync.refresh(), isFalse);
    expect(transport.versions, isEmpty);
  });
}
```

`test/data/http/schedule_task_api_client_test.dart`:

```dart
// `ScheduleTaskApiClient` contra un servidor HTTP local.
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/data/http/schedule_task_api_client.dart';

void main() {
  late HttpServer server;
  final seen = <({Map<String, String> query, String? auth})>[];
  var status = 200;
  Object body = const <String, dynamic>{};

  setUp(() async {
    seen.clear();
    status = 200;
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      seen.add((query: request.uri.queryParameters, auth: request.headers.value('authorization')));
      request.response
        ..statusCode = status
        ..headers.contentType = ContentType.json
        ..write(jsonEncode(body));
      await request.response.close();
    });
  });
  tearDown(() => server.close(force: true));

  ScheduleTaskApiClient client() => ScheduleTaskApiClient(
    dio: Dio(BaseOptions(baseUrl: 'http://127.0.0.1:${server.port}')),
    accessTokenProvider: () => 'token-1',
  );

  test('lee la lista y envía la versión conocida con el bearer', () async {
    body = {
      'version': 'v2',
      'changed': true,
      'items': [
        {
          'id': 't-3',
          'project_id': 'p-1',
          'project_name': 'Obra Norte',
          'wbs': '1.2',
          'outline_number': '1.2',
          'name': 'Descapote',
          'last_validated_pct': 40,
        },
      ],
    };
    final page = await client().fetch(version: 'v1');
    expect(seen.single.query, {'version': 'v1'});
    expect(seen.single.auth, 'Bearer token-1');
    expect(
      (page!.version, page.changed, page.items.single.name, page.items.single.lastValidatedPct),
      ('v2', true, 'Descapote', 40.0),
    );
  });

  test('sin versión no envía el parámetro', () async {
    body = {'version': 'v1', 'changed': true, 'items': <Object>[]};
    await client().fetch();
    expect(seen.single.query, isEmpty);
  });

  test('error del servidor o respuesta rara: null (no se toca la lista)', () async {
    status = 500;
    expect(await client().fetch(), isNull);
    status = 200;
    body = {'version': 'v1'};
    expect(await client().fetch(), isNull);
  });
}
```

`test/data/local/schedule_task_repository_test.dart`:

```dart
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/data/local/database.dart';
import 'package:visitacampo/data/local/schedule_task_repository.dart';

const _items = [
  ScheduleTaskItem(id: 'a', projectName: 'Obra Norte', code: '1.2', name: 'Descapote', lastValidatedPct: null),
  ScheduleTaskItem(id: 'b', projectName: 'Obra Sur', code: '2.1', name: 'Excavación de zanjas', lastValidatedPct: 10),
];

void main() {
  test('busca por EDT, nombre o proyecto, sin tildes ni mayúsculas', () {
    expect(searchScheduleTasks(_items, '  '), _items);
    expect(searchScheduleTasks(_items, 'EXCAVACION').single.id, 'b');
    expect(searchScheduleTasks(_items, '1.2').single.id, 'a');
    expect(searchScheduleTasks(_items, 'sur').single.id, 'b');
    expect(searchScheduleTasks(_items, 'zzz'), isEmpty);
  });

  test('forUser: solo las del usuario y su organización, en el orden recibido', () async {
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    addTearDown(db.close);
    ScheduleTasksCompanion row(String id, String user, int position, {String org = 'org-1'}) =>
        ScheduleTasksCompanion.insert(
          id: id,
          orgId: org,
          userId: user,
          projectId: 'p-1',
          projectName: 'Obra Norte',
          wbs: '',
          outlineNumber: '1.$position',
          name: 'Tarea $id',
          lastValidatedPct: const Value(null),
          position: position,
        );
    await db.batch(
      (b) => b.insertAll(db.scheduleTasks, [
        row('z', 'ana', 2),
        row('y', 'ana', 1),
        row('x', 'beto', 0),
        row('w', 'ana', 0, org: 'org-2'),
      ]),
    );
    final items = await ScheduleTaskRepository(db).forUser(orgId: 'org-1', userId: 'ana');
    expect([for (final i in items) i.label], ['1.1 Tarea y', '1.2 Tarea z'], reason: 'sin EDT se usa el número de esquema');
  });
}
```

- [ ] **Step 2: Verificar que fallan**

Run: `flutter test test/core/schedule/ test/data/http/schedule_task_api_client_test.dart test/data/local/schedule_task_repository_test.dart`
Expected: FAIL de compilación (archivos inexistentes).

- [ ] **Step 3: `progress_input.dart`**

`lib/core/schedule/progress_input.dart`:

```dart
// El % de avance acumulado que escribe el agente (costos, etapa 1): número
// entre 0 y 100 con hasta dos decimales, con coma o punto — lo mismo que el
// servidor guarda (`numeric(5,2)`). Puro.

final _pattern = RegExp(r'^\d{1,3}([.,]\d{1,2})?$');

/// El % escrito, o `null` si no es un número entre 0 y 100 con hasta dos decimales.
double? parseProgressPct(String text) {
  final value = text.trim();
  if (!_pattern.hasMatch(value)) return null;
  final pct = double.parse(value.replaceAll(',', '.'));
  return pct <= 100 ? pct : null;
}

/// `40.0` → `40`; `40.5` → `40,5`; `33.25` → `33,25`.
String formatProgressPct(double pct) =>
    pct.toStringAsFixed(2).replaceFirst(RegExp(r'\.?0+$'), '').replaceAll('.', ',');
```

- [ ] **Step 4: `schedule_task_sync.dart`**

`lib/core/schedule/schedule_task_sync.dart`:

```dart
// Tareas del cronograma (costos, etapa 1) que el agente puede vincular a una
// visita: las vigentes de detalle de las que es responsable, con su último %
// validado.
//
// Mismo ciclo que las capas de referencia (`layers/layer_manager.dart`): el
// servidor entrega la lista **entera** (`GET /v1/projects/tasks`) y el
// teléfono la reemplaza, así que lo que ya no viene (tarea retirada,
// responsabilidad quitada) desaparece sin tombstones. Para no bajarla en cada
// ciclo se envía la `version` de la última lista: si no cambió, el servidor
// contesta `changed: false` sin ítems. Sin red no se toca nada: la captura
// sigue con la última lista descargada.
import 'package:drift/drift.dart' show Value;
import 'package:meta/meta.dart';

import '../../data/local/database.dart';
import '../auth/auth_models.dart';

/// Contrato `FieldTask`.
@immutable
class ScheduleTaskInfo {
  const ScheduleTaskInfo({
    required this.id,
    required this.projectId,
    required this.projectName,
    required this.wbs,
    required this.outlineNumber,
    required this.name,
    this.lastValidatedPct,
  });

  factory ScheduleTaskInfo.fromJson(Map<String, dynamic> json) => ScheduleTaskInfo(
    id: json['id'] as String,
    projectId: json['project_id'] as String,
    projectName: json['project_name'] as String,
    wbs: json['wbs'] as String,
    outlineNumber: json['outline_number'] as String,
    name: json['name'] as String,
    lastValidatedPct: (json['last_validated_pct'] as num?)?.toDouble(),
  );

  final String id;
  final String projectId;
  final String projectName;
  final String wbs;
  final String outlineNumber;
  final String name;
  final double? lastValidatedPct;
}

/// Contrato `FieldTaskList`.
@immutable
class ScheduleTaskPage {
  const ScheduleTaskPage({required this.version, required this.changed, required this.items});

  factory ScheduleTaskPage.fromJson(Map<String, dynamic> json) => ScheduleTaskPage(
    version: json['version'] as String,
    changed: json['changed'] as bool,
    items: [
      for (final item in json['items'] as List<dynamic>) ScheduleTaskInfo.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String version;
  final bool changed;
  final List<ScheduleTaskInfo> items;
}

abstract class ScheduleTaskTransport {
  /// `null` sin red o ante una respuesta que no se entiende.
  Future<ScheduleTaskPage?> fetch({String? version});
}

class ScheduleTaskSync {
  ScheduleTaskSync({
    required this.db,
    required this.transport,
    required this.currentUser,
    DateTime Function()? clock,
  }) : _now = clock ?? DateTime.now;

  final AppDatabase db;
  final ScheduleTaskTransport transport;

  /// Quién tiene la sesión; `null` sin sesión.
  final UserProfile? Function() currentUser;
  final DateTime Function() _now;

  Future<bool>? _running;

  /// Trae la lista si cambió. `true` si el teléfono quedó al día. Una a la vez.
  Future<bool> refresh() => _running ??= _refresh().whenComplete(() => _running = null);

  Future<bool> _refresh() async {
    final user = currentUser();
    if (user == null) return false;
    final state = await db.readScheduleTaskState();
    // La versión guardada solo vale si la lista es de este usuario.
    final known = state != null && state.userId == user.id && state.orgId == user.orgId ? state.version : null;
    final page = await transport.fetch(version: known);
    if (page == null) return false;
    if (!page.changed && known != null) return true;
    await db.replaceScheduleTasks(
      orgId: user.orgId,
      userId: user.id,
      version: page.version,
      now: _now().toUtc(),
      rows: [
        for (final (position, task) in page.items.indexed)
          ScheduleTasksCompanion.insert(
            id: task.id,
            orgId: user.orgId,
            userId: user.id,
            projectId: task.projectId,
            projectName: task.projectName,
            wbs: task.wbs,
            outlineNumber: task.outlineNumber,
            name: task.name,
            lastValidatedPct: Value(task.lastValidatedPct),
            position: position,
          ),
      ],
    );
    return true;
  }
}
```

- [ ] **Step 5: Cliente HTTP**

`lib/data/http/schedule_task_api_client.dart`:

```dart
// `ScheduleTaskApiClient` — implementación Dio de `ScheduleTaskTransport`
// (costos, etapa 1). Como `LayerApiClient`: bearer por request y `null` sin red
// o ante una respuesta que no se entiende (la lista local se conserva).
import 'package:dio/dio.dart';

import '../../core/schedule/schedule_task_sync.dart';

class ScheduleTaskApiClient implements ScheduleTaskTransport {
  ScheduleTaskApiClient({required this.dio, required this.accessTokenProvider});

  final Dio dio;
  final String Function() accessTokenProvider;

  @override
  Future<ScheduleTaskPage?> fetch({String? version}) async {
    try {
      final response = await dio.get<Map<String, dynamic>>(
        '/v1/projects/tasks',
        queryParameters: {'version': ?version},
        options: Options(headers: {'Authorization': 'Bearer ${accessTokenProvider()}'}),
      );
      final data = response.data;
      return data == null ? null : ScheduleTaskPage.fromJson(data);
    } on DioException {
      return null;
    } on TypeError {
      return null;
    }
  }
}
```

- [ ] **Step 6: Repositorio**

`lib/data/local/schedule_task_repository.dart`:

```dart
// Tareas del cronograma del agente (costos, etapa 1), desde la BD local: lo
// que ofrece el selector "Tarea del cronograma" de la captura, sin red.
import 'package:drift/drift.dart';
import 'package:meta/meta.dart';

import 'database.dart';

@immutable
class ScheduleTaskItem {
  const ScheduleTaskItem({
    required this.id,
    required this.projectName,
    required this.code,
    required this.name,
    required this.lastValidatedPct,
  });

  factory ScheduleTaskItem.fromRow(ScheduleTaskRow row) => ScheduleTaskItem(
    id: row.id,
    projectName: row.projectName,
    // La EDT del Project; si viene vacía, el número de esquema.
    code: row.wbs.isEmpty ? row.outlineNumber : row.wbs,
    name: row.name,
    lastValidatedPct: row.lastValidatedPct,
  );

  final String id;
  final String projectName;
  final String code;
  final String name;
  final double? lastValidatedPct;

  String get label => '$code $name';
}

class ScheduleTaskRepository {
  const ScheduleTaskRepository(this.db);

  final AppDatabase db;

  /// Las tareas de [userId] en [orgId], en el orden en que llegaron.
  Future<List<ScheduleTaskItem>> forUser({required String orgId, required String userId}) async {
    final rows =
        await (db.select(db.scheduleTasks)
              ..where((t) => t.orgId.equals(orgId) & t.userId.equals(userId))
              ..orderBy([(t) => OrderingTerm.asc(t.position)]))
            .get();
    return [for (final row in rows) ScheduleTaskItem.fromRow(row)];
  }
}

/// Filtra por EDT, nombre o proyecto, sin distinguir mayúsculas ni tildes.
List<ScheduleTaskItem> searchScheduleTasks(List<ScheduleTaskItem> items, String query) {
  final needle = foldForSearch(query.trim());
  if (needle.isEmpty) return items;
  return [
    for (final task in items)
      if (foldForSearch('${task.code} ${task.name} ${task.projectName}').contains(needle)) task,
  ];
}

String foldForSearch(String text) {
  const from = 'áàäâãéèëêíìïîóòöôõúùüûñç';
  const to = 'aaaaaeeeeiiiiooooouuuunc';
  final buffer = StringBuffer();
  for (final char in text.toLowerCase().split('')) {
    final index = from.indexOf(char);
    buffer.write(index < 0 ? char : to[index]);
  }
  return buffer.toString();
}
```

- [ ] **Step 7: Verificar que pasan**

Run: `flutter test test/core/schedule/ test/data/http/schedule_task_api_client_test.dart test/data/local/schedule_task_repository_test.dart`
Expected: todo pasa.

- [ ] **Step 8: Commit**

```bash
git add lib/core/schedule lib/data/http/schedule_task_api_client.dart lib/data/local/schedule_task_repository.dart test/core/schedule test/data/http/schedule_task_api_client_test.dart test/data/local/schedule_task_repository_test.dart
git commit -m "feat(schedule): download the agent's schedule tasks for offline linking"
```

---

### Task B4: Composición — ciclo de sincronización y cambio de usuario

**Files:**
- Modify: `lib/core/sync/sync_coordinator.dart`, `lib/core/app_dependencies.dart`, `lib/core/session/device_handover.dart`
- Test: `test/core/sync/sync_coordinator_tasks_test.dart`, `test/core/session/device_handover_test.dart`

- [ ] **Step 1: Pruebas que fallan**

`test/core/sync/sync_coordinator_tasks_test.dart`:

```dart
// Costos, etapa 1: cada ciclo refresca las tareas del cronograma tras el pull;
// si falla (sin red), el ciclo termina igual.
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/attachments/attachment_upload_service.dart';
import 'package:visitacampo/core/sync/result.dart';
import 'package:visitacampo/core/sync/sync_coordinator.dart';
import 'package:visitacampo/core/sync/sync_engine.dart';

SyncCoordinator _coordinator(List<String> calls, Future<bool> Function() refreshTasks) => SyncCoordinator(
  drainOutbox: () async {
    calls.add('drain');
    return DrainReport.empty;
  },
  uploadAttachments: ({required force}) async {
    calls.add('upload');
    return AttachmentDrainReport.empty;
  },
  outstandingAttachments: () async => 0,
  pull: () async {
    calls.add('pull');
    return Result.ok(true);
  },
  isAuthenticated: () => true,
  refreshTasks: refreshTasks,
  periodic: (_, _) => Timer(const Duration(days: 1), () {}),
);

void main() {
  test('tras el pull refresca las tareas del cronograma', () async {
    final calls = <String>[];
    await _coordinator(calls, () async {
      calls.add('tasks');
      return true;
    }).syncNow();
    expect(calls, ['drain', 'upload', 'pull', 'tasks']);
  });

  test('si falla, el ciclo termina sin incidencias nuevas', () async {
    final report = await _coordinator(<String>[], () async => throw StateError('sin red')).syncNow();
    expect(report!.hasIssues, isFalse);
  });
}
```

En `test/core/session/device_handover_test.dart`, al final de `main()`:

```dart
  test('al cambiar de organización también se borran las tareas del cronograma', () async {
    await db
        .into(db.visits)
        .insert(
          VisitsCompanion.insert(
            id: 'v-ana',
            orgId: 'org-1',
            version: 1,
            templateVersionId: 'tv-1',
            answersJson: '{}',
            capturedAt: DateTime.utc(2026, 11, 1),
            deviceId: 'device-1',
            createdBy: 'ana',
          ),
        );
    await db.replaceScheduleTasks(
      orgId: 'org-1',
      userId: 'ana',
      version: 'v1',
      now: DateTime.utc(2026, 11, 1),
      rows: [
        ScheduleTasksCompanion.insert(
          id: 't-1',
          orgId: 'org-1',
          userId: 'ana',
          projectId: 'p-1',
          projectName: 'Obra',
          wbs: '1.1',
          outlineNumber: '1.1',
          name: 'Excavación',
          position: 0,
        ),
      ],
    );

    final result = await handover.prepare(_carla);

    expect(result, isA<HandoverReady>().having((r) => r.wiped, 'wiped', isTrue));
    expect(await db.select(db.scheduleTasks).get(), isEmpty);
    expect(await db.readScheduleTaskState(), isNull);
  });
```

- [ ] **Step 2: Verificar que fallan**

Run: `flutter test test/core/sync/sync_coordinator_tasks_test.dart test/core/session/device_handover_test.dart`
Expected: FAIL (`refreshTasks` no existe; las tareas no se borran).

- [ ] **Step 3: `SyncCoordinator`**

En el constructor, después de `required this.isAuthenticated,`:

```dart
    this.refreshTasks,
```

Campo, después de `isAuthenticated`:

```dart
  /// Costos, etapa 1: refresca las tareas del cronograma del agente tras el
  /// pull (`ScheduleTaskSync.refresh`). Es una lista pequeña y la captura la
  /// necesita al día; por eso va dentro del ciclo y no aparte, como los mapas.
  final Future<bool> Function()? refreshTasks;
```

En `_cycle`, después del `try { pullOk = ... } on Object { pullOk = false; }`:

```dart
    final refresh = refreshTasks;
    if (refresh != null) {
      try {
        await refresh();
      } on Object {
        // Sin red o respuesta rara: queda la lista anterior; el próximo ciclo reintenta.
      }
    }
```

- [ ] **Step 4: `AppDependencies`**

Imports:

```dart
import 'schedule/schedule_task_sync.dart';
import '../data/http/schedule_task_api_client.dart';
import '../data/local/schedule_task_repository.dart';
```

Parámetro del constructor (junto a `ConsentService? consent,`): `ScheduleTaskSync? scheduleTasks,`. En la lista de inicialización, después de `consent = ...`:

```dart
       scheduleTasks =
           scheduleTasks ??
           ScheduleTaskSync(
             db: database,
             transport: ScheduleTaskApiClient(dio: dio, accessTokenProvider: () => authController.accessToken ?? ''),
             currentUser: () => switch (authController.state) {
               Authenticated(:final user) => user,
               _ => null,
             },
           ),
```

Campo y getter (junto a `assignments`):

```dart
  /// Avance de campo (costos, etapa 1): descarga de las tareas del cronograma.
  final ScheduleTaskSync scheduleTasks;

  /// Las tareas guardadas, para la captura.
  ScheduleTaskRepository get scheduleTaskRepository => ScheduleTaskRepository(database);
```

En `syncCoordinator`, después de `isAuthenticated: ...`:

```dart
    refreshTasks: scheduleTasks.refresh,
```

- [ ] **Step 5: `DeviceHandover`**

En `prepare`, dentro de la transacción de borrado, después de `await db.delete(db.formTemplateVersions).go();`:

```dart
      // Costos, etapa 1: la lista de tareas también es de la otra organización.
      await db.delete(db.scheduleTasks).go();
      await db.delete(db.scheduleTaskState).go();
```

- [ ] **Step 6: Verificar que pasan**

Run: `flutter test test/core/sync/sync_coordinator_test.dart test/core/sync/sync_coordinator_tasks_test.dart test/core/session/`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add lib/core/sync/sync_coordinator.dart lib/core/app_dependencies.dart lib/core/session/device_handover.dart test/core/sync/sync_coordinator_tasks_test.dart test/core/session/device_handover_test.dart
git commit -m "feat(sync): refresh schedule tasks every sync cycle and wipe them on org change"
```

---

### Task B5: `VisitCapture` guarda y edita la tarea y el %

**Files:**
- Modify: `lib/core/capture/visit_capture.dart`
- Test: `test/core/capture/visit_task_progress_test.dart`

- [ ] **Step 1: Prueba que falla**

`test/core/capture/visit_task_progress_test.dart`:

```dart
// Costos, etapa 1: la visita guarda su tarea y su % sin red, y viajan juntos
// por el outbox; en una edición solo viajan si cambian.
import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/core/capture/visit_capture.dart';
import 'package:visitacampo/core/forms/form_definition.dart';
import 'package:visitacampo/data/local/database.dart';

const _me = CaptureIdentity(orgId: 'org-1', userId: 'user-1', deviceId: 'device-1');

final _definition =
    (parseFormDefinition({
              'fields': [
                {
                  'key': 'notas',
                  'type': 'text',
                  'label': {'es': 'Notas'},
                },
              ],
            })
            as DefinitionOk)
        .definition;

void main() {
  late AppDatabase db;
  late VisitCapture capture;
  var ids = 0;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    ids = 0;
    capture = VisitCapture(db: db, idGenerator: () => 'id-${ids++}', clock: () => DateTime.utc(2026, 11, 2, 10));
  });
  tearDown(() => db.close());

  Future<Map<String, dynamic>> lastPayload() async {
    final ops = await db.select(db.outboxOps).get();
    return jsonDecode(ops.last.payloadJson) as Map<String, dynamic>;
  }

  Future<Visit> visitOf(String id) => (db.select(db.visits)..where((t) => t.id.equals(id))).getSingle();

  Future<String> saveLinked() => capture.save(
    identity: _me,
    templateVersionId: 'tv-1',
    definition: _definition,
    answers: {'notas': 'x'},
    captured: const {},
    progress: const TaskProgress(taskId: 'task-1', pct: 35.5),
  );

  Future<VisitChange> edit(String visitId, {({TaskProgress? value})? progress}) => capture.update(
    identity: _me,
    visitId: visitId,
    definition: _definition,
    answers: {'notas': 'x'},
    captured: const {},
    progress: progress,
  );

  test('alta: la tarea y el % se guardan y viajan juntos', () async {
    final visitId = await saveLinked();
    final visit = await visitOf(visitId);
    expect((visit.taskId, visit.progressPct), ('task-1', 35.5));
    final payload = await lastPayload();
    expect((payload['task_id'], payload['progress_pct']), ('task-1', 35.5));
  });

  test('alta sin tarea: el payload no lleva los campos', () async {
    await capture.save(
      identity: _me,
      templateVersionId: 'tv-1',
      definition: _definition,
      answers: {'notas': 'x'},
      captured: const {},
    );
    final payload = await lastPayload();
    expect(payload.containsKey('task_id'), isFalse);
    expect(payload.containsKey('progress_pct'), isFalse);
  });

  test('edición: cambiar el % envía el par; repetirlo no envía nada', () async {
    final visitId = await saveLinked();
    expect(await edit(visitId, progress: (value: const TaskProgress(taskId: 'task-1', pct: 50))), VisitChange.saved);
    expect(await lastPayload(), {'id': visitId, 'task_id': 'task-1', 'progress_pct': 50.0});
    expect(await edit(visitId, progress: (value: const TaskProgress(taskId: 'task-1', pct: 50))), VisitChange.unchanged);
  });

  test('edición: sin `progress` no se toca; con `null` se desvincula', () async {
    final visitId = await saveLinked();
    expect(await edit(visitId), VisitChange.unchanged);
    expect(await edit(visitId, progress: (value: null)), VisitChange.saved);
    expect(await lastPayload(), {'id': visitId, 'task_id': null, 'progress_pct': null});
    final visit = await visitOf(visitId);
    expect((visit.taskId, visit.progressPct), (null, null));
  });
}
```

- [ ] **Step 2: Verificar que falla**

Run: `flutter test test/core/capture/visit_task_progress_test.dart`
Expected: FAIL de compilación (`progress` no existe en `save`/`update`).

- [ ] **Step 3: `save`**

Firma: después de `String? assignmentId,` añadir `TaskProgress? progress,`. En el `payload`, después de `'assignment_id': ?assignmentId,`:

```dart
      // Costos, etapa 1. Sin tarea no se envían: un servidor anterior los rechazaría.
      if (progress != null) ...{'task_id': progress.taskId, 'progress_pct': progress.pct},
```

En `VisitsCompanion.insert(...)` de la llamada a `saveVisitCapture`, después de `assignmentId: Value(assignmentId),`:

```dart
        taskId: Value(progress?.taskId),
        progressPct: Value(progress?.pct),
```

- [ ] **Step 4: `update`**

Firma: después de `LocationFix? fix,` añadir:

```dart
    /// Costos, etapa 1. `null`: no se toca. `(value: null)`: desvincular.
    /// `(value: TaskProgress(...))`: poner o cambiar la tarea y su %.
    ({TaskProgress? value})? progress,
```

Dentro de la transacción, `kept` y el mapa `changed` pasan a:

```dart
    final kept = keptAnswers(definition, answers);
    final link = progress?.value;
    final progressChanged =
        progress != null && (link?.taskId != visit.taskId || link?.pct != visit.progressPct);
    final changed = <String, Object?>{
      if (jsonEncode(kept) != visit.answersJson) 'answers': kept,
      if (fix != null) ...{'lat': fix.lat, 'lon': fix.lon, 'accuracy_m': fix.accuracyM, 'altitude_m': fix.altitudeM},
      // El servidor exige el par completo, también en `update`.
      if (progressChanged) ...{'task_id': link?.taskId, 'progress_pct': link?.pct},
    };
```

Y en la escritura de la fila (`VisitsCompanion(answersJson: ..., lat: ..., ...)` dentro de `if (changed.isNotEmpty)`), añadir:

```dart
          taskId: progressChanged ? Value(link?.taskId) : const Value.absent(),
          progressPct: progressChanged ? Value(link?.pct) : const Value.absent(),
```

- [ ] **Step 5: Verificar que pasan (también las de B2)**

Run: `flutter test test/core/capture/ test/core/sync/`
Expected: todo pasa, incluidas `pull_task_progress_test.dart` y `quarantine_task_test.dart`.

- [ ] **Step 6: Commit**

```bash
git add lib/core/capture/visit_capture.dart test/core/capture/visit_task_progress_test.dart
git commit -m "feat(capture): save and edit a visit's schedule task and progress offline"
```

---

### Task B6: Textos y sección "Avance del cronograma"

**Files:**
- Modify: `lib/l10n/app_es.arb`, `lib/ui/forms/dynamic_form.dart`
- Create: `lib/features/capture/task_progress_section.dart`
- Test: `test/features/capture/task_progress_section_test.dart`

- [ ] **Step 1: Textos**

En `lib/l10n/app_es.arb`, reemplazar la última entrada:

```json
  "visitExportVersion": "Versión del formulario"
```

por:

```json
  "visitExportVersion": "Versión del formulario",
  "captureTaskSection": "Avance del cronograma",
  "captureTaskLabel": "Tarea del cronograma",
  "captureTaskNone": "Sin tarea (opcional)",
  "captureTaskUnavailable": "Tarea vinculada (ya no está en tu lista)",
  "captureProgressLabel": "% avance acumulado",
  "captureProgressSuffix": "%",
  "captureProgressReference": "Último validado: {pct} %",
  "@captureProgressReference": {
    "placeholders": {
      "pct": {
        "type": "String"
      }
    }
  },
  "captureProgressNoReference": "Sin avance validado todavía.",
  "captureProgressBelowValidated": "Es menor que el último validado ({pct} %). Se guardará y la supervisión lo verá marcado.",
  "@captureProgressBelowValidated": {
    "placeholders": {
      "pct": {
        "type": "String"
      }
    }
  },
  "captureProgressRequired": "Indica el % de avance acumulado.",
  "captureProgressInvalid": "Escribe un número entre 0 y 100, con hasta dos decimales.",
  "taskPickerSearch": "Buscar por EDT o nombre",
  "taskPickerNone": "Sin tarea",
  "taskPickerEmpty": "No hay tareas que coincidan.",
  "taskPickerSubtitle": "{project} · último validado {pct} %",
  "@taskPickerSubtitle": {
    "placeholders": {
      "project": {
        "type": "String"
      },
      "pct": {
        "type": "String"
      }
    }
  },
  "formErrorTaskRetired": "La tarea fue retirada del cronograma.",
  "formErrorTaskIsSummary": "Elige una tarea de detalle, no un resumen.",
  "formErrorNotResponsible": "Ya no eres responsable de esta tarea.",
  "formErrorTaskProgressPair": "La tarea y el % de avance van juntos.",
  "reviewUnlinkTask": "Quitar la tarea y reenviar"
```

En `lib/ui/forms/dynamic_form.dart::formErrorMessage`, antes de `_ => l10n.formErrorInvalid,`:

```dart
  // Costos, etapa 1: rechazos del push por la tarea del cronograma.
  'task_retired' => l10n.formErrorTaskRetired,
  'task_is_summary' => l10n.formErrorTaskIsSummary,
  'not_responsible' => l10n.formErrorNotResponsible,
  'task_progress_pair' => l10n.formErrorTaskProgressPair,
  'greater_than_equal' || 'less_than_equal' => l10n.formErrorOutOfRange,
```

Run: `flutter gen-l10n`

- [ ] **Step 2: Prueba que falla**

`test/features/capture/task_progress_section_test.dart`:

```dart
// Costos, etapa 1: selector "Tarea del cronograma" (búsqueda por EDT o nombre)
// y "% avance acumulado" con el último validado como referencia.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:visitacampo/data/local/schedule_task_repository.dart';
import 'package:visitacampo/features/capture/task_progress_section.dart';

import '../../support/test_app.dart';

const _tasks = [
  ScheduleTaskItem(id: 't-2', projectName: 'Obra Norte', code: '1.1', name: 'Localización', lastValidatedPct: null),
  ScheduleTaskItem(id: 't-3', projectName: 'Obra Norte', code: '1.2', name: 'Descapote', lastValidatedPct: 40),
];

void main() {
  late TextEditingController controller;
  String? selected;
  final picks = <String?>[];

  setUp(() {
    controller = TextEditingController();
    selected = null;
    picks.clear();
  });
  tearDown(() => controller.dispose());

  Future<void> pump(WidgetTester tester, {String? errorText}) async {
    await tester.pumpWidget(
      testApp(
        Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => ListView(
              children: [
                TaskProgressSection(
                  tasks: _tasks,
                  selectedId: selected,
                  controller: controller,
                  errorText: errorText,
                  onSelected: (id) => setState(() {
                    selected = id;
                    picks.add(id);
                  }),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('sin tarea: es opcional y no pide %', (tester) async {
    await pump(tester);
    expect(find.text('Sin tarea (opcional)'), findsOneWidget);
    expect(find.byKey(const Key('capture_progress_field')), findsNothing);
  });

  testWidgets('busca por EDT o nombre y elige la tarea', (tester) async {
    await pump(tester);
    await tester.tap(find.byKey(const Key('capture_task_picker')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('task_picker_search')), 'descap');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('task_picker_item-t-2')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('task_picker_item-t-3')));
    await tester.pumpAndSettle();
    expect(picks, ['t-3']);
    expect(find.text('1.2 Descapote'), findsOneWidget);
    expect(find.text('Último validado: 40 %'), findsOneWidget);
  });

  testWidgets('un % menor que el último validado se avisa', (tester) async {
    selected = 't-3';
    await pump(tester);
    await tester.enterText(find.byKey(const Key('capture_progress_field')), '35');
    await tester.pump();
    expect(find.byKey(const Key('capture_progress_below')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('capture_progress_field')), '45');
    await tester.pump();
    expect(find.byKey(const Key('capture_progress_below')), findsNothing);
  });

  testWidgets('"Sin tarea" la quita', (tester) async {
    selected = 't-2';
    await pump(tester);
    expect(find.text('Sin avance validado todavía.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('capture_task_picker')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('task_picker_none')));
    await tester.pumpAndSettle();
    expect(picks, [null]);
  });

  testWidgets('muestra el error que le pasa la pantalla', (tester) async {
    selected = 't-2';
    await pump(tester, errorText: 'Indica el % de avance acumulado.');
    expect(find.text('Indica el % de avance acumulado.'), findsOneWidget);
  });
}
```

- [ ] **Step 3: Verificar que falla**

Run: `flutter test test/features/capture/task_progress_section_test.dart`
Expected: FAIL de compilación (`task_progress_section.dart` no existe).

- [ ] **Step 4: Implementar**

`lib/features/capture/task_progress_section.dart`:

```dart
// Avance de campo (costos, etapa 1): en la captura, "Tarea del cronograma"
// (opcional, búsqueda por EDT o nombre) y "% avance acumulado" con el último
// validado como referencia. Un % menor se acepta pero se avisa: la supervisión
// lo verá marcado. Todo local: las tareas vienen de la BD del teléfono.
import 'package:flutter/material.dart';

import '../../core/schedule/progress_input.dart';
import '../../data/local/schedule_task_repository.dart';
import '../../l10n/l10n.dart';
import '../../ui/theme/app_tokens.dart';

class TaskProgressSection extends StatelessWidget {
  const TaskProgressSection({
    super.key,
    required this.tasks,
    required this.selectedId,
    required this.controller,
    required this.onSelected,
    this.errorText,
  });

  final List<ScheduleTaskItem> tasks;

  /// La tarea elegida; puede no estar en [tasks] al editar una visita cuya
  /// tarea ya no está en la lista del agente.
  final String? selectedId;
  final TextEditingController controller;
  final ValueChanged<String?> onSelected;
  final String? errorText;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final selected = tasks.where((t) => t.id == selectedId).firstOrNull;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(l10n.captureTaskSection, style: theme.textTheme.titleSmall),
        ListTile(
          key: const Key('capture_task_picker'),
          contentPadding: EdgeInsets.zero,
          title: Text(
            selectedId == null ? l10n.captureTaskNone : selected?.label ?? l10n.captureTaskUnavailable,
          ),
          subtitle: selected == null ? null : Text(selected.projectName),
          trailing: const Icon(Icons.arrow_drop_down),
          onTap: () async {
            final pick = await showTaskPicker(context, tasks);
            if (pick != null) onSelected(pick.task?.id);
          },
        ),
        if (selectedId != null)
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: controller,
            builder: (context, value, _) {
              final reference = selected?.lastValidatedPct;
              final pct = parseProgressPct(value.text);
              final below = reference != null && pct != null && pct < reference;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    key: const Key('capture_progress_field'),
                    controller: controller,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(
                      labelText: l10n.captureProgressLabel,
                      suffixText: l10n.captureProgressSuffix,
                      errorText: errorText,
                      helperText: reference == null
                          ? l10n.captureProgressNoReference
                          : l10n.captureProgressReference(formatProgressPct(reference)),
                    ),
                  ),
                  if (below)
                    Text(
                      l10n.captureProgressBelowValidated(formatProgressPct(reference)),
                      key: const Key('capture_progress_below'),
                      style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                    ),
                ],
              );
            },
          ),
      ],
    );
  }
}

/// Lo elegido en el selector: `task` nula = "Sin tarea". El `Future` da
/// `null` si se cerró sin elegir.
typedef TaskPick = ({ScheduleTaskItem? task});

Future<TaskPick?> showTaskPicker(BuildContext context, List<ScheduleTaskItem> tasks) =>
    showModalBottomSheet<TaskPick>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _TaskPickerSheet(tasks: tasks),
    );

class _TaskPickerSheet extends StatefulWidget {
  const _TaskPickerSheet({required this.tasks});

  final List<ScheduleTaskItem> tasks;

  @override
  State<_TaskPickerSheet> createState() => _TaskPickerSheetState();
}

class _TaskPickerSheetState extends State<_TaskPickerSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final visible = searchScheduleTasks(widget.tasks, _query);
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
        child: SizedBox(
          height: MediaQuery.sizeOf(context).height * 0.8,
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: TextField(
                  key: const Key('task_picker_search'),
                  autofocus: true,
                  decoration: InputDecoration(labelText: l10n.taskPickerSearch, prefixIcon: const Icon(Icons.search)),
                  onChanged: (value) => setState(() => _query = value),
                ),
              ),
              ListTile(
                key: const Key('task_picker_none'),
                title: Text(l10n.taskPickerNone),
                onTap: () => Navigator.of(context).pop<TaskPick>((task: null)),
              ),
              const Divider(),
              Expanded(
                child: visible.isEmpty
                    ? Center(child: Text(l10n.taskPickerEmpty))
                    : ListView.builder(
                        itemCount: visible.length,
                        itemBuilder: (context, index) {
                          final task = visible[index];
                          final pct = task.lastValidatedPct;
                          return ListTile(
                            key: ValueKey('task_picker_item-${task.id}'),
                            title: Text(task.label),
                            subtitle: Text(
                              pct == null
                                  ? task.projectName
                                  : l10n.taskPickerSubtitle(task.projectName, formatProgressPct(pct)),
                            ),
                            onTap: () => Navigator.of(context).pop<TaskPick>((task: task)),
                          );
                        },
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 5: Verificar que pasa**

Run: `flutter test test/features/capture/task_progress_section_test.dart && bash tool/lint_ui_tokens.sh`
Expected: todo pasa; el lint no reporta nada.

- [ ] **Step 6: Commit**

```bash
git add lib/l10n/app_es.arb lib/ui/forms/dynamic_form.dart lib/features/capture/task_progress_section.dart test/features/capture/task_progress_section_test.dart
git commit -m "feat(capture): schedule task picker with search and accumulated progress field"
```

---

### Task B7: Pantalla de captura

**Files:**
- Modify: `lib/features/capture/capture_screen.dart`
- Test: `test/features/capture/capture_screen_test.dart`

- [ ] **Step 1: Pruebas que fallan**

En `test/features/capture/capture_screen_test.dart`, al final de `main()`:

```dart
  Future<void> addTask({double? lastValidated}) => db.replaceScheduleTasks(
    orgId: 'org-1',
    userId: 'user-1',
    version: 'v1',
    now: DateTime.utc(2026, 11, 2),
    rows: [
      ScheduleTasksCompanion.insert(
        id: 'task-1',
        orgId: 'org-1',
        userId: 'user-1',
        projectId: 'p-1',
        projectName: 'Obra Norte',
        wbs: '1.2',
        outlineNumber: '1.2',
        name: 'Descapote',
        lastValidatedPct: Value(lastValidated),
        position: 0,
      ),
    ],
  );

  Future<void> pickTask(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('capture_task_picker')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('task_picker_item-task-1')));
    await tester.pumpAndSettle();
  }

  testWidgets('avance de campo: vincula la visita a una tarea con su % (costos, etapa 1)', (tester) async {
    final dependencies = await buildDependencies();
    await addTask(lastValidated: 40);
    await pumpCapture(tester, dependencies);

    await pickTask(tester);
    await tester.enterText(find.byKey(const Key('capture_progress_field')), '35,5');
    await tester.pump();
    expect(find.byKey(const Key('capture_progress_below')), findsOneWidget);

    await tester.tap(find.widgetWithText(ChoiceChip, 'No'));
    await tester.pump();
    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();

    final visit = await db.select(db.visits).getSingle();
    expect((visit.taskId, visit.progressPct), ('task-1', 35.5));
    final payload = jsonDecode((await db.select(db.outboxOps).getSingle()).payloadJson) as Map<String, dynamic>;
    expect((payload['task_id'], payload['progress_pct']), ('task-1', 35.5));
  });

  testWidgets('con tarea elegida, sin un % válido no guarda', (tester) async {
    final dependencies = await buildDependencies();
    await addTask();
    await pumpCapture(tester, dependencies);

    await pickTask(tester);
    await tester.tap(find.widgetWithText(ChoiceChip, 'No'));
    await tester.pump();
    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();
    expect(find.text('Indica el % de avance acumulado.'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('capture_progress_field')), '120');
    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();
    expect(find.text('Escribe un número entre 0 y 100, con hasta dos decimales.'), findsOneWidget);
    expect(await db.select(db.visits).get(), isEmpty);
  });

  testWidgets('sin tareas asignadas no aparece la sección', (tester) async {
    final dependencies = await buildDependencies();
    await pumpCapture(tester, dependencies);
    expect(find.byKey(const Key('capture_task_picker')), findsNothing);
  });
```

- [ ] **Step 2: Verificar que fallan**

Run: `flutter test test/features/capture/capture_screen_test.dart`
Expected: FAIL — no existe `capture_task_picker`.

- [ ] **Step 3: Implementar**

En `lib/features/capture/capture_screen.dart`:

Imports:

```dart
import '../../core/schedule/progress_input.dart';
import '../../data/local/schedule_task_repository.dart';
import '../../l10n/app_localizations.dart';
import 'task_progress_section.dart';
```

(Si `l10n.dart` ya reexporta `AppLocalizations`, `dart analyze` marcará el import como innecesario: quitarlo.)

Estado, después de `final List<PendingAttachment> _extraPhotos = [];`:

```dart
  /// Costos, etapa 1: tareas del cronograma del agente y la elegida.
  List<ScheduleTaskItem> _tasks = const [];
  String? _taskId;
  final _progressController = TextEditingController();
  String? _progressError;
```

En `initState`, al final:

```dart
    _loadTasks();
```

Métodos nuevos (junto a `_loadTemplates`):

```dart
  Future<void> _loadTasks() async {
    final dependencies = ref.read(appDependenciesProvider);
    final state = dependencies.authController.state;
    if (state is! Authenticated) return;
    final tasks = await dependencies.scheduleTaskRepository.forUser(orgId: state.user.orgId, userId: state.user.id);
    if (mounted) setState(() => _tasks = tasks);
  }

  /// Sin tarea: `ok` y nada que enviar. Con tarea: el % leído, o el error en pantalla.
  ({bool ok, TaskProgress? value}) _readProgress(AppLocalizations l10n) {
    final taskId = _taskId;
    if (taskId == null) {
      setState(() => _progressError = null);
      return (ok: true, value: null);
    }
    final text = _progressController.text.trim();
    final pct = parseProgressPct(text);
    final error = text.isEmpty
        ? l10n.captureProgressRequired
        : pct == null
        ? l10n.captureProgressInvalid
        : null;
    setState(() => _progressError = error);
    return error == null ? (ok: true, value: TaskProgress(taskId: taskId, pct: pct!)) : (ok: false, value: null);
  }
```

En `_loadForEdit`, dentro del `setState`, al final:

```dart
      _taskId = visit.taskId;
      if (visit.progressPct != null) _progressController.text = formatProgressPct(visit.progressPct!);
```

En `dispose`, antes de `super.dispose()`:

```dart
    _progressController.dispose();
```

En `_save`, reemplazar:

```dart
    final l10n = context.l10n;
    if (!form.validate()) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.captureFixErrors)));
      return;
    }
```

por:

```dart
    final l10n = context.l10n;
    final formOk = form.validate();
    final progress = _readProgress(l10n);
    if (!formOk || !progress.ok) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.captureFixErrors)));
      return;
    }
```

En la llamada a `dependencies.visitCapture.update(...)`, añadir `progress: (value: progress.value),`; en la de `dependencies.visitCapture.save(...)`, añadir `progress: progress.value,`.

En `build`, después de `DynamicForm(controller: form, mediaDelegate: this, formatLocation: _crs.format),` y antes del `const AppGap.sm(),` que la sigue:

```dart
              if (_tasks.isNotEmpty || _taskId != null) ...[
                const AppGap.sm(),
                TaskProgressSection(
                  tasks: _tasks,
                  selectedId: _taskId,
                  controller: _progressController,
                  errorText: _progressError,
                  onSelected: (id) => setState(() {
                    _taskId = id;
                    _progressError = null;
                    if (id == null) _progressController.clear();
                  }),
                ),
              ],
```

- [ ] **Step 4: Verificar que pasan**

Run: `flutter test test/features/capture/ && bash tool/lint_ui_tokens.sh`
Expected: todo pasa (las pruebas anteriores de captura siguen: sin tareas la sección no aparece).

- [ ] **Step 5: Commit**

```bash
git add lib/features/capture/capture_screen.dart test/features/capture/capture_screen_test.dart
git commit -m "feat(capture): link a visit to a schedule task with its accumulated progress"
```

---

### Task B8: "Para revisar" — rechazos por la tarea

**Files:**
- Modify: `lib/features/review/review_screen.dart`
- Test: `test/features/review/review_screen_test.dart`

- [ ] **Step 1: Prueba que falla**

En `test/features/review/review_screen_test.dart`, al final de `main()`:

```dart
  testWidgets('rechazo por la tarea: nombra el campo y ofrece quitarla', (tester) async {
    await quarantineWith(
      (opUuid) => PushOpOutcome(
        opUuid: opUuid,
        status: 'rejected',
        error: const PushOpError(
          code: 'forbidden',
          message: 'Only a responsible of the task can report its progress.',
          details: {'field': 'task_id', 'reason': 'not_responsible'},
        ),
      ),
    );
    await pump(tester);

    expect(find.text('Tarea del cronograma: Ya no eres responsable de esta tarea.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('review_unlink_task')));
    await tester.pumpAndSettle();

    expect((await db.select(db.outboxOps).getSingle()).state, 'pending');
    expect(find.text('Listo: se enviará en la próxima sincronización.'), findsOneWidget);
  });
```

- [ ] **Step 2: Verificar que falla**

Run: `flutter test test/features/review/review_screen_test.dart`
Expected: FAIL — el campo aparece como `task_id` y no hay `review_unlink_task`.

- [ ] **Step 3: Implementar**

En `_ReviewCard`: nuevo parámetro `required this.onUnlinkTask,` y campo `final VoidCallback onUnlinkTask;`. En `build`, después de la función local `label`:

```dart
    String violationLabel(String field) => switch (field) {
      'task_id' => l10n.captureTaskLabel,
      'progress_pct' => l10n.captureProgressLabel,
      _ when field.startsWith('answers.') => label(field.substring('answers.'.length)),
      _ => field,
    };
```

y el bucle de violaciones pasa a:

```dart
            for (final v in item.violations)
              Text(l10n.reviewViolation(violationLabel(v.field), formErrorMessage(l10n, v.reason))),
```

En la rama de rechazo de los botones (`] else ...[`), antes de `if (item.canCorrect)`:

```dart
                  if (item.taskRejected)
                    FilledButton(
                      key: const Key('review_unlink_task'),
                      onPressed: busy ? null : onUnlinkTask,
                      child: Text(l10n.reviewUnlinkTask),
                    ),
```

En `ReviewScreen.build`, al construir `_ReviewCard`, después de `onRetry: ...`:

```dart
                onUnlinkTask: () => _run(() => ref.read(appDependenciesProvider).quarantine.unlinkTask(item), l10n.reviewQueued),
```

- [ ] **Step 4: Verificar que pasa**

Run: `flutter test test/features/review/ && bash tool/lint_ui_tokens.sh`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add lib/features/review/review_screen.dart test/features/review/review_screen_test.dart
git commit -m "feat(review): unlink the schedule task of a visit rejected for it"
```

---

### Task B9: Paso E2E — la visita llega vinculada a su tarea con su %

**Files:**
- Modify: `integration_test/e2e_fase0_test.dart`, `tool/e2e.sh`

Lo que debe crear el arranque del servidor: `bootstrap_demo --with-schedule` (Task A12) — proyecto "Obra E2E …", etapa 1 "Preliminares" con el agente como responsable y la tarea de detalle **1.1 "Excavación"**, que la hereda. Así la E2E prueba de punta a punta la descarga de tareas, la herencia por rama, el guardado offline, el push y la validación del servidor.

- [ ] **Step 1: Sembrado**

En `tool/e2e.sh`, la llamada a `bootstrap_demo` pasa a:

```bash
log "Sembrando organización, usuario y tarea del cronograma $RUN_ID"
"${COMPOSE[@]}" exec -T api python manage.py bootstrap_demo \
  --org-name "E2E $RUN_ID" --username "$RUN_ID" --email "$RUN_ID@example.com" \
  --password "$PASSWORD" --with-schedule >/dev/null
```

- [ ] **Step 2: Paso en la prueba**

En `integration_test/e2e_fase0_test.dart`, después de `await tester.enterText(notas, _marker);` y antes de tocar `capture_save_button`:

```dart
    // Avance de campo (costos, etapa 1): la tarea 1.1 que sembró
    // `bootstrap_demo --with-schedule` llegó en la sincronización inicial.
    await _tapVisible(tester, find.byKey(const Key('capture_task_picker')));
    await _pumpUntil(tester, find.byKey(const Key('task_picker_search')));
    await tester.enterText(find.byKey(const Key('task_picker_search')), 'excav');
    await tester.pump();
    await _tapVisible(tester, find.text('1.1 Excavación'));
    await _pumpUntil(tester, find.byKey(const Key('capture_progress_field')));
    await tester.enterText(find.byKey(const Key('capture_progress_field')), '35');
    await tester.pump();
```

En el comentario de cabecera, después de `//   E2E:SYNCED             → el script verifica en el servidor`, añadir la línea `//   (la visita lleva la tarea 1.1 con 35 % de avance acumulado)`.

- [ ] **Step 3: Verificación en el servidor**

En `tool/e2e.sh`, el bloque `python manage.py shell -c '...'` pasa a:

```bash
"${COMPOSE[@]}" exec -T -e E2E_MARKER="$RUN_ID" api python manage.py shell -c '
import os, sys
from decimal import Decimal
from visitacampo.core.models import Visit
marker = os.environ["E2E_MARKER"]
visits = list(Visit.objects.filter(answers__notas=marker, deleted_at__isnull=True).select_related("org", "task"))
ok = (
    len(visits) == 1
    and visits[0].org.name == f"E2E {marker}"
    and visits[0].version >= 1
    and visits[0].change_seq
    and visits[0].task is not None
    and visits[0].task.wbs == "1.1"
    and visits[0].progress_pct == Decimal("35")
)
for v in visits:
    print(f"visita {v.pk} org={v.org.name!r} version={v.version} change_seq={v.change_seq} tarea={v.task} avance={v.progress_pct}")
sys.exit(0 if ok else 1)
' || die "La visita $RUN_ID no está en el servidor como se esperaba (con la tarea 1.1 al 35 %)."
```

y el mensaje de éxito de `cleanup` pasa a `log "OK: la visita $RUN_ID llegó al servidor con su tarea y su avance."`.

- [ ] **Step 4: Ejecutar la E2E**

Con el emulador arrancado (`emulator -avd suno_api36`) y Docker Desktop, y `SUNO_server` local en el commit del PR 1 o posterior:

Run: `SUNO_SERVER_DIR=../SUNO_server bash tool/e2e.sh`
Expected: `[e2e] OK: la visita e2e-… llegó al servidor con su tarea y su avance.`

- [ ] **Step 5: Commit**

```bash
git add integration_test/e2e_fase0_test.dart tool/e2e.sh
git commit -m "test(e2e): a visit linked to a schedule task arrives with its progress"
```

---

### Task B10: Verificación completa y PR de la app

- [ ] **Step 1: Verificación completa**

```bash
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
dart analyze
flutter test --concurrency=1 --coverage
bash tool/lint_ui_tokens.sh
bash tool/lint_no_tls_bypass.sh
bash tool/lint_no_dynamic_code.sh
```

Expected: análisis limpio, toda la suite en verde, lints sin violaciones; cobertura no por debajo del umbral de `mobile.yml` (`tool/check_lcov.py`).

- [ ] **Step 2: Prueba manual en el emulador**

Contra el servidor de desarrollo con el PR 1: login como agente responsable de una etapa; sincronizar; nueva visita → "Tarea del cronograma", buscar por EDT y por nombre, elegir, ver "Último validado"; escribir un % menor y ver el aviso; guardar en modo avión; reconectar y comprobar en el Admin la columna "avance de cronograma". En el servidor, quitar la responsabilidad y sincronizar: la tarea desaparece del selector. Editar la visita y cambiar el %: llega como `update` con el par.

- [ ] **Step 3: PR**

```bash
git push -u origin feat/costos-campo-app
gh pr create --base master --title "feat(capture): report schedule task progress from field visits, stage 1 (app)" --body-file -
```

Cuerpo: resumen, dependencia del PR del servidor (SHA fijado en `contract/SOURCE`), migración Drift v9 (reinicia el cursor del pull), pruebas, cómo correr la E2E; terminar con la línea de atribución de PRs indicada en la sesión.

- [ ] **Step 4: Registro**

En `SUNO_server/plans/visitacampo-progreso.md` (en el siguiente PR del servidor), completar la sección del avance desde campo con el PR de la app y el resultado de la E2E.

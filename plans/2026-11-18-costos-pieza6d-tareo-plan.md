# Costos — Pieza 6d (mano de obra por jornal: tareo y conciliación de nómina): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Llevar en SUNO el tareo nominal de la mano de obra por jornal (registro de trabajadores con jornal y factor prestacional con vigencia, asignaciones por proyecto y planillas por periodo con tope diario de 1,5 jornales entre obras), publicar al libro de costos de la 6a el costo estimado de cada planilla aprobada (un movimiento de Mano de obra por destino) y conciliar lo tareado con la nómina real importada desde Excel, sin liquidar nómina.

**Architecture:** Diez modelos nuevos en `core/models/labor.py`, la FK de origen `CostEntry.timesheet` y la sección `LABOR` de la matriz fija, en una migración `0031_labor` (aditiva, con un `RunPython` que da la sección a la administración existente, como la `0028`). Un paquete nuevo `server/suno/labor/` lleva el cálculo puro (`calc.py`: vigencias, costo por línea, resumen y tope), las piezas comunes y el orden de bloqueo (`common.py`), los servicios con auditoría (`registry.py`, `assignments.py`, `timesheets.py`, `approval.py`, `payroll.py`), las importaciones en dos pasos con nonce (`imports.py`, `registry_import.py`, `timesheet_import.py`), las consultas (`queries.py`, `grid.py`), el PDF y los Excel (`pdf.py`, `export.py`) y la web: el registro de la organización en `plataforma/trabajadores/` (trabajadores, factor y conciliación) y la pestaña "Tareo" del proyecto (asignaciones, planillas, cuadrícula semanal y vista por día). El libro (6a) gana el origen "Tareo" (sin nombres ni salarios; el enlace a la planilla solo para quien lee Mano de obra). El control no cambia: los movimientos aprobados de Mano de obra ya entran al AC y al CPI.

**Tech Stack:** Django 5.2, PostgreSQL 16, plantillas de Django con CSS propio y un JavaScript opcional (`tareo.js`, solo totales en vivo), reportlab, `unittest`/`django.test` corridos en Docker.

**Spec:** `plans/2026-11-18-costos-pieza6d-tareo-design.md` (aprobada). Plan de referencia de formato y código base: `plans/2026-11-15-costos-pieza6c-materiales-plan.md`.

**Rama y worktree:** `feat/costos-tareo` en `C:\Users\gabri\APP\wt-server-tareo` (base `master` con la 6c, v0.20.0; spec en `d2bee37`). Todos los comandos se corren desde la raíz de ese worktree.

Las decisiones tomadas al planear están en la sección final del plan (⚑ = conviene que el usuario las revise).

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `tearDownModule = teardown_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; `blob_registry()` para `Blob`; las pruebas y los comandos de `ALLOWED` sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`, con un código nuevo en `AuditAction`. Nada de `assert` en producción (bandit B101) ni `import xml.etree`. Ruff a 100 columnas (`E, F, I, UP, B, C4, DJ`; B008: sin llamadas en valores por defecto).
- mypy sin el plugin de Django: en producción, los miembros de `TextChoices` que van a un `str` se envuelven con `str(...)`; en las pruebas, `Any` donde haga falta.
- `core/policies.py` es el único lugar donde se decide quién puede qué. Toda política nueva va en `__all__`, con su `check_*` y en el registro de `core/tests/test_policies.py`.
- Comentarios y textos de interfaz en español; identificadores en inglés. Commits en inglés, estilo conventional, terminados en una línea en blanco y `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La guardia de nombres (`infra/scripts/check_naming.py concept`) rechaza "visit"/"visita" en código y plantillas.
- Si cambia `projects.css`, se sube su `?v=` en `server/suno/core/templates/suno/projects/base.html` (hoy `20261116-materials-v2`; esta pieza lo deja en `20261118-labor-v1`).
- Todo texto que escribe un usuario y va a un Excel pasa por `safe_cell`; todo texto que va al PDF, por `html.escape`.
- Los formularios POST largos, donde `{% csrf_token %}` va en la línea siguiente al `<form>`, llevan el comentario del repositorio: `<!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->`. Un `href` armado en el servidor con `reverse()` y pintado desde una variable lleva al final de la línea `{# nosemgrep: generic.html-templates.security.var-in-href.var-in-href -- href hecho en el servidor con reverse() (ruta de la app), nunca texto del usuario #}`.
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura. No hay Python local: todo corre en Docker.

## Cómo correr las pruebas

```bash
# Desde C:/Users/gabri/APP/wt-server-tareo, con Docker Desktop arrancado (el contenedor trabaja en server/)
bash infra/scripts/test_docker.sh suno/labor/tests/test_calc.py        # un archivo
bash infra/scripts/test_docker.sh suno/labor suno/costs suno/control    # paquetes
bash infra/scripts/test_docker.sh                                       # suite completa (~1 h)
bash infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
bash infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
bash infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
bash infra/scripts/test_docker.sh --down                                # al terminar

# Guardia de nombres (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-tareo -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Fallas conocidas, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y `healthcheck.py` en `ruff format --check` (comprobarlo en la Task 0 y anotarlo).

Fechas de las pruebas: los servicios usan `timezone.localdate()`; las pruebas lo fijan en **2026-04-15** con `mock.patch("django.utils.timezone.localdate", return_value=TODAY)`. Los datos de ejemplo: factor 1,5200 desde 2026-01-01; Pedro Pérez (CC 1012345678, jornal 80.000,00) y María Gómez (CC 52123456, jornal 100.000,00), asignados al proyecto desde el 2026-01-05. Un jornal de Pedro cuesta 121.600,00; medio de María, 76.000,00.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/suno/core/access.py` | `Section.LABOR` y su fila de la matriz |
| `server/suno/core/access_services.py` | `LEGACY_GRANTS`: Mano de obra para `OWNER` y `ADMIN` |
| `server/suno/core/policies.py` | `can_*_labor`, `can_read_labor_registry`, `can_manage_labor_registry`, `can_export_labor_registry` y sus `check_*` |
| `server/suno/core/audit.py` | `WORKER_*`, `BENEFIT_FACTOR_CHANGED`, `LABOR_ASSIGNMENT_CHANGED`, `TIMESHEET_*`, `PAYROLL_IMPORTED`, `LABOR_RECONCILIATION_VIEWED`, `LABOR_EXPORTED` |
| `server/suno/core/navigation.py` | Clave `LABOR_REGISTRY` para la barra lateral |
| `server/suno/core/models/labor.py` | `Worker`, `WorkerWage`, `BenefitFactor`, `ProjectWorker`, `Timesheet`, `TimesheetLine`, `TimesheetLineItem`, `PayrollImport`, `PayrollImportLine`, `LaborImportToken` y sus enumeraciones |
| `server/suno/core/models/cost.py` | `CostEntry.timesheet`, `CostDocumentType.TIMESHEET`, `ORIGIN_FIELDS` |
| `server/suno/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/suno/core/migrations/0031_labor.py` | Generada, más el `RunPython` de la sección |
| `server/config/settings/base.py` | `DATA_UPLOAD_MAX_NUMBER_FIELDS = 4000` |
| `server/suno/labor/calc.py` | Vigencias, costo por línea, resumen, huella y tope (puro) |
| `server/suno/labor/common.py` | `LaborRejected`, límites, máscara, bloqueo de trabajadores y factor, historiales, edición y decisión, soporte |
| `server/suno/labor/registry.py` | Trabajadores, jornales y factor |
| `server/suno/labor/assignments.py` | Asignaciones al proyecto |
| `server/suno/labor/timesheets.py` | Planillas: crear, guardar líneas (asignación y tope), recalcular, borrar, soporte; resumen |
| `server/suno/labor/approval.py` | Aprobar (libro), rechazar y anular |
| `server/suno/labor/imports.py` | Celdas de Excel, firma de la vista previa y nonce compartidos |
| `server/suno/labor/registry_import.py`, `timesheet_import.py`, `payroll.py` | Importaciones de trabajadores, tareo y nómina; conciliación |
| `server/suno/labor/queries.py`, `grid.py` | Listas, ficha, cuadrícula semanal y vista por día |
| `server/suno/labor/access.py` | Proyecto, planilla, asignación y trabajador de la URL (404) |
| `server/suno/labor/forms.py` | Formularios y lectura de la cuadrícula enviada |
| `server/suno/labor/pdf.py`, `export.py` | PDF de la planilla y Excel de la planilla y de la conciliación |
| `server/suno/labor/views.py`, `views_timesheets.py`, `urls.py` | Pestaña "Tareo" |
| `server/suno/labor/registry_views.py`, `reconciliation_views.py`, `registry_urls.py` | Registro de la organización y conciliación |
| `server/config/urls.py` | Monta `plataforma/trabajadores/` y `plataforma/proyectos/<uuid:project_id>/tareo/` |
| `server/suno/core/templates/suno/labor/*.html` | Plantillas |
| `server/suno/core/templates/suno/projects/_header.html`, `suno/_sidebar.html`, `projects/base.html`, `projects/views.py` | Pestaña, enlace lateral, versión del CSS, `can_view_labor` |
| `server/suno/core/static/suno/css/projects.css`, `static/suno/js/tareo.js` | Estilos `ta-*` y totales en vivo |
| `server/suno/costs/ledger.py`, `services.py`, `access.py`, `views.py`, `core/templates/suno/costs/{list,detail}.html` | Origen "Tareo", reverso, enlace condicionado |
| `server/suno/management/commands/seed_control_demo.py` | Tareo de ejemplo |
| Pruebas | `core/tests/{test_access_matrix,test_policies,test_labor_migration}.py`; `labor/tests/{__init__,factories,test_models,test_calc,test_registry,test_registry_import,test_assignments,test_timesheets,test_approval,test_concurrency,test_timesheet_import,test_payroll,test_grid,test_views_registry,test_views,test_views_grid,test_views_timesheets,test_views_reconciliation,test_integration,test_performance}.py`; `control/tests/test_seed_demo.py` |
| `docs/adr/0039-…`, `docs/adr/0040-…`, `docs/adr/README.md`, `docs/arquitectura/{componentes,modelo-datos-costos}.md`, `docs/seguridad/{tratamiento-datos-personales,inventario-activos,retencion}.md` | Documentación (Task 20) |
| `plans/suno-progreso.md`, spec | Registro de avance y decisiones (Task 21) |

---

### Task 0: Línea base

**Files:** ninguno.

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-tareo status --short --branch`
Expected: `## feat/costos-tareo...origin/feat/costos-tareo` sin cambios pendientes; los dos últimos commits son el de este plan y el de la spec (`d2bee37`).

- [ ] **Step 2: Línea base**

```bash
bash infra/scripts/test_docker.sh 2>&1 | tail -5
bash infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
bash infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
bash infra/scripts/test_docker.sh suno/materials/tests/test_performance.py suno/control/tests/test_performance.py -s 2>&1 | tail -5
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`; `ruff format --check` solo marca `healthcheck.py`; `No changes detected`; las pruebas de carga pasan. Anotar el número de pruebas y los tiempos (para el registro de avance). No hay commit en esta tarea.

---

### Task 1: Sección "Mano de obra" en la matriz, políticas y acciones de auditoría

**Files:**
- Modify: `server/suno/core/access.py`, `server/suno/core/access_services.py`, `server/suno/core/policies.py`, `server/suno/core/audit.py`
- Test: `server/suno/core/tests/test_access_matrix.py`, `server/suno/core/tests/test_policies.py`

Todo es puro (`SimpleTestCase`): la migración llega en la Task 2. Hasta entonces `makemigrations --check` marca el cambio de `choices` de `section_grant.section`; es lo esperado.

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/core/tests/test_access_matrix.py`, al final de `AccessCatalogTests`:

```python
    def test_labor_profiles_match_the_approved_design(self) -> None:
        """Costos, pieza 6d (decisión 2 del plan): Consulta solo lee; nada de `import`."""
        c = Capability
        expected = {
            SectionRole.MANAGER: {
                c.CREATE, c.READ, c.UPDATE, c.DELETE, c.APPROVE, c.REJECT, c.REVERSE, c.EXPORT,
            },
            SectionRole.REVIEWER: {
                c.CREATE, c.READ, c.UPDATE, c.DELETE, c.APPROVE, c.REJECT, c.EXPORT,
            },
            SectionRole.CONTRIBUTOR: {c.CREATE, c.READ, c.UPDATE, c.DELETE, c.EXPORT},
            SectionRole.VIEWER: {c.READ},
        }  # fmt: skip
        self.assertEqual(Section.LABOR.label, "Mano de obra")
        self.assertEqual(allowed_roles(Section.LABOR), set(expected))
        for role, capabilities in expected.items():
            with self.subTest(role=role):
                self.assertEqual(capabilities_for(Section.LABOR, role), frozenset(capabilities))
        # Costos no cambia: la mano de obra es otra sección.
        self.assertTrue(role_has_capability(Section.COSTS, SectionRole.VIEWER, Capability.EXPORT))
        self.assertFalse(role_has_capability(Section.LABOR, SectionRole.VIEWER, Capability.EXPORT))
```

En `server/suno/core/tests/test_policies.py`:

1. Después de `cost_entry` (el modelo `Timesheet` llega en la Task 2, que cambia este
   ayudante; las políticas solo leen `org_id`, `project_id` y `created_by_id`, que `CostEntry`
   también tiene):

```python
def timesheet(org_id: uuid.UUID = ORG, created_by_id: uuid.UUID = USER) -> CostEntry:
    """Una planilla como recurso de política. Provisional: la Task 2 la vuelve `Timesheet`."""
    return CostEntry(org_id=org_id, project_id=uuid.uuid4(), created_by_id=created_by_id)
```

2. En `_registry()` de `NoRoleGrantsNothingTests`, después de las del catálogo de materiales:

```python
            # Costos, pieza 6d: mano de obra por proyecto y registro de la organización.
            "can_read_labor": project(),
            "can_create_labor": project(),
            "can_approve_labor": project(),
            "can_reject_labor": project(),
            "can_reverse_labor": project(),
            "can_export_labor": project(),
            "can_update_labor": timesheet(),
            "can_delete_labor": timesheet(),
            "can_read_labor_registry": ORG,
            "can_manage_labor_registry": ORG,
            "can_export_labor_registry": ORG,
```

3. Una clase nueva al final del archivo:

```python
class LaborPolicyTests(SimpleTestCase):
    """Costos, pieza 6d (decisiones 2 a 4 del plan)."""

    def actor(
        self,
        role: Any,
        *,
        scope: Any = AccessScope.ORGANIZATION,
        projects: frozenset[uuid.UUID] = frozenset(),
    ) -> RequestContext:
        return RequestContext(
            user_id=USER,
            org_id=ORG,
            roles=frozenset({Role.SUPERVISOR}),
            section_access=(
                SectionAccess(section=Section.LABOR, role=role, scope=scope, project_ids=projects),
            ),
        )

    def test_administration_receives_labor_and_nobody_else(self) -> None:
        for role in (Role.OWNER, Role.ADMIN):
            with self.subTest(role=role):
                self.assertTrue(policies.can_reverse_labor(ctx(role), project()))
                self.assertTrue(policies.can_manage_labor_registry(ctx(role), ORG))
        for role in (Role.SUPERVISOR, Role.FIELD_AGENT, Role.VIEWER):
            with self.subTest(role=role):
                self.assertFalse(policies.can_read_labor(ctx(role), project()))

    def test_role_matrix(self) -> None:
        site = project()
        #: crear, aprobar, rechazar, anular, exportar
        cases = {
            SectionRole.MANAGER: (True, True, True, True, True),
            SectionRole.REVIEWER: (True, True, True, False, True),
            SectionRole.CONTRIBUTOR: (True, False, False, False, True),
            SectionRole.VIEWER: (False, False, False, False, False),
        }
        for role, expected in cases.items():
            actor = self.actor(role)
            with self.subTest(role=role):
                self.assertTrue(policies.can_read_labor(actor, site))
                self.assertEqual(
                    (
                        policies.can_create_labor(actor, site),
                        policies.can_approve_labor(actor, site),
                        policies.can_reject_labor(actor, site),
                        policies.can_reverse_labor(actor, site),
                        policies.can_export_labor(actor, site),
                    ),
                    expected,
                )

    def test_contributor_updates_and_deletes_only_its_own(self) -> None:
        own, other = timesheet(created_by_id=USER), timesheet(created_by_id=OTHER_USER)
        contributor = self.actor(SectionRole.CONTRIBUTOR)
        self.assertTrue(policies.can_update_labor(contributor, own))
        self.assertTrue(policies.can_delete_labor(contributor, own))
        self.assertFalse(policies.can_update_labor(contributor, other))
        self.assertFalse(policies.can_delete_labor(contributor, other))
        self.assertTrue(policies.can_update_labor(self.actor(SectionRole.REVIEWER), other))
        self.assertFalse(policies.can_update_labor(self.actor(SectionRole.VIEWER), own))

    def test_costs_do_not_open_labor(self) -> None:
        costs_manager = RequestContext(
            user_id=USER,
            org_id=ORG,
            roles=frozenset({Role.SUPERVISOR}),
            section_access=(
                SectionAccess(
                    section=Section.COSTS,
                    role=SectionRole.MANAGER,
                    scope=AccessScope.ORGANIZATION,
                ),
            ),
        )
        self.assertTrue(policies.can_read_costs(costs_manager, project()))
        self.assertFalse(policies.can_read_labor(costs_manager, project()))
        self.assertFalse(policies.can_read_labor_registry(costs_manager, ORG))

    def test_project_scope_does_not_open_the_registry(self) -> None:
        site = project()
        limited = self.actor(
            SectionRole.MANAGER, scope=AccessScope.PROJECTS, projects=frozenset({site.id})
        )
        self.assertTrue(policies.can_read_labor(limited, site))
        self.assertTrue(policies.can_approve_labor(limited, site))
        self.assertFalse(policies.can_read_labor(limited, project()))
        self.assertFalse(policies.can_read_labor_registry(limited, ORG))
        self.assertFalse(policies.can_manage_labor_registry(limited, ORG))
        self.assertFalse(policies.can_export_labor_registry(limited, ORG))

    def test_registry_by_role(self) -> None:
        #: leer, gestionar, exportar (con alcance de organización)
        cases = {
            SectionRole.MANAGER: (True, True, True),
            SectionRole.REVIEWER: (True, True, True),
            SectionRole.CONTRIBUTOR: (True, True, True),
            SectionRole.VIEWER: (True, False, False),
        }
        for role, expected in cases.items():
            actor = self.actor(role)
            with self.subTest(role=role):
                self.assertEqual(
                    (
                        policies.can_read_labor_registry(actor, ORG),
                        policies.can_manage_labor_registry(actor, ORG),
                        policies.can_export_labor_registry(actor, ORG),
                    ),
                    expected,
                )
        with self.assertRaises(HttpError) as denied:
            policies.check_manage_labor_registry(self.actor(SectionRole.VIEWER), ORG)
        self.assertEqual(denied.exception.status_code, 403)
```

`project()` construye `Project(org_id=org_id)`: su `id` es un UUID nuevo por llamada (default del campo), por eso `project()` y `site` son proyectos distintos.

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/core/tests/test_access_matrix.py suno/core/tests/test_policies.py`
Expected: FAIL (`type object 'Section' has no attribute 'LABOR'`; `module 'suno.core.policies' has no attribute 'can_read_labor'`; el registro ya no cubre el módulo).

- [ ] **Step 3: La sección en la matriz**

En `server/suno/core/access.py`, en `Section`, después de `COSTS`:

```python
    LABOR = "LABOR", "Mano de obra"
```

Y en `CAPABILITY_MATRIX`, después del bloque de `Section.COSTS`:

```python
    # Mano de obra por jornal (costos, pieza 6d, spec 3.5): tareo nominal, separado de Costos
    # porque lleva nombres y salarios. Consulta no exporta (decisión 2 del plan). "Solo
    # propios" del Colaborador lo decide `policies.can_update_labor`/`can_delete_labor`.
    Section.LABOR: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.APPROVE,
            Capability.REJECT,
            Capability.REVERSE,
            Capability.EXPORT,
        ),
        SectionRole.REVIEWER: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.APPROVE,
            Capability.REJECT,
            Capability.EXPORT,
        ),
        SectionRole.CONTRIBUTOR: _caps(
            Capability.CREATE,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.EXPORT,
        ),
        SectionRole.VIEWER: _caps(Capability.READ),
    },
```

En `server/suno/core/access_services.py`, en `LEGACY_GRANTS["OWNER"]` y `LEGACY_GRANTS["ADMIN"]`, después de la línea de `Section.COSTS`:

```python
        (Section.LABOR, SectionRole.MANAGER, AccessScope.ORGANIZATION),
```

- [ ] **Step 4: Las políticas**

En `server/suno/core/policies.py`:

1. En `__all__`, en su lugar alfabético: `"can_approve_labor"`, `"can_create_labor"`, `"can_delete_labor"`, `"can_export_labor"`, `"can_export_labor_registry"`, `"can_manage_labor_registry"`, `"can_read_labor"`, `"can_read_labor_registry"`, `"can_reject_labor"`, `"can_reverse_labor"`, `"can_update_labor"` y sus once `"check_…"`.
2. Después de `can_manage_material_catalog`:

```python
def _labor(ctx: RequestContext, org_id: UUID, project_id: UUID, capability: Capability) -> bool:
    """Mano de obra (costos, pieza 6d): capacidad de la sección **en ese proyecto**."""
    return user_has_section_capability(
        ctx, org_id, Section.LABOR, capability, project_id=project_id
    )


def can_read_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """Ver la pestaña Tareo (asignaciones y planillas): todos los roles de Mano de obra."""
    return _labor(ctx, project.org_id, project.id, Capability.READ)


def can_create_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """Asignar trabajadores y crear planillas: Gestor, Revisor y Colaborador."""
    return _labor(ctx, project.org_id, project.id, Capability.CREATE)


def can_approve_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """Aprobar planillas: Gestor y Revisor. Que nadie apruebe lo suyo lo exige el servicio (y
    la base): esta política responde solo por el rol."""
    return _labor(ctx, project.org_id, project.id, Capability.APPROVE)


def can_reject_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """Rechazar planillas, con motivo: Gestor y Revisor."""
    return _labor(ctx, project.org_id, project.id, Capability.REJECT)


def can_reverse_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """Anular una planilla aprobada: solo el Gestor."""
    return _labor(ctx, project.org_id, project.id, Capability.REVERSE)


def can_export_labor(ctx: RequestContext, project: ProjectResource) -> bool:
    """PDF y Excel de una planilla (nombres y jornales): todos menos Consulta."""
    return _labor(ctx, project.org_id, project.id, Capability.EXPORT)


def _own_or_any_labor(
    ctx: RequestContext, resource: CostEntryResource, capability: Capability
) -> bool:
    if not _labor(ctx, resource.org_id, resource.project_id, capability):
        return False
    return resource.created_by_id == ctx.user_id or _labor(
        ctx, resource.org_id, resource.project_id, Capability.APPROVE
    )


def can_update_labor(ctx: RequestContext, resource: CostEntryResource) -> bool:
    """Editar una planilla o una asignación: Gestor y Revisor, la de cualquiera; Colaborador,
    solo las suyas. Lo aprobado nunca se edita: eso lo comprueba el servicio."""
    return _own_or_any_labor(ctx, resource, Capability.UPDATE)


def can_delete_labor(ctx: RequestContext, resource: CostEntryResource) -> bool:
    """Borrar una planilla en borrador o una asignación: como editar."""
    return _own_or_any_labor(ctx, resource, Capability.DELETE)


def can_read_labor_registry(ctx: RequestContext, org_id: UUID) -> bool:
    """Ver el registro de trabajadores (documento completo y jornales), el factor y la
    conciliación: `read` de Mano de obra con alcance de **toda la organización** (sin
    `project_id`, `has_capability` solo lo concede a ese alcance)."""
    return user_has_section_capability(ctx, org_id, Section.LABOR, Capability.READ)


def can_manage_labor_registry(ctx: RequestContext, org_id: UUID) -> bool:
    """Altas, jornales, factor, retiros, borrado e importaciones de trabajadores y de nómina:
    `create` y `update` de Mano de obra con alcance de toda la organización (spec 3.5)."""
    return user_has_section_capability(
        ctx, org_id, Section.LABOR, Capability.CREATE
    ) and user_has_section_capability(ctx, org_id, Section.LABOR, Capability.UPDATE)


def can_export_labor_registry(ctx: RequestContext, org_id: UUID) -> bool:
    """Excel de la conciliación: `export` de Mano de obra con alcance de toda la organización."""
    return user_has_section_capability(ctx, org_id, Section.LABOR, Capability.EXPORT)
```

3. Después de `check_manage_material_catalog`, los once `check_*` con la forma de siempre (por proyecto `raise _denial(ctx, project.org_id)`, por recurso `raise _denial(ctx, resource.org_id)`, por organización `raise _denial(ctx, org_id)`):

```python
def check_read_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_read_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_create_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_create_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_approve_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_approve_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_reject_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_reject_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_reverse_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_reverse_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_export_labor(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_export_labor(ctx, project):
        raise _denial(ctx, project.org_id)


def check_update_labor(ctx: RequestContext, resource: CostEntryResource) -> None:
    if not can_update_labor(ctx, resource):
        raise _denial(ctx, resource.org_id)


def check_delete_labor(ctx: RequestContext, resource: CostEntryResource) -> None:
    if not can_delete_labor(ctx, resource):
        raise _denial(ctx, resource.org_id)


def check_read_labor_registry(ctx: RequestContext, org_id: UUID) -> None:
    if not can_read_labor_registry(ctx, org_id):
        raise _denial(ctx, org_id)


def check_manage_labor_registry(ctx: RequestContext, org_id: UUID) -> None:
    if not can_manage_labor_registry(ctx, org_id):
        raise _denial(ctx, org_id)


def check_export_labor_registry(ctx: RequestContext, org_id: UUID) -> None:
    if not can_export_labor_registry(ctx, org_id):
        raise _denial(ctx, org_id)
```

- [ ] **Step 5: Las acciones de auditoría**

En `server/suno/core/audit.py`, dentro de `AuditAction`, después de `MATERIALS_EXPORTED`:

```python
    # Mano de obra (costos, pieza 6d). Metadata con ids y documento enmascarado, nunca nombres
    # (decisión 25 del plan); `metadata.project` en todos los del proyecto.
    WORKER_CREATED = "worker.created"
    WORKER_UPDATED = "worker.updated"
    WORKER_RETIRED = "worker.retired"
    WORKER_DELETED = "worker.deleted"
    WORKER_WAGE_CHANGED = "worker.wage_changed"
    WORKERS_IMPORTED = "workers.imported"
    BENEFIT_FACTOR_CHANGED = "benefit_factor.changed"
    LABOR_ASSIGNMENT_CHANGED = "labor_assignment.changed"
    TIMESHEET_CREATED = "timesheet.created"
    TIMESHEET_UPDATED = "timesheet.updated"
    TIMESHEET_DELETED = "timesheet.deleted"
    TIMESHEET_APPROVED = "timesheet.approved"
    TIMESHEET_REJECTED = "timesheet.rejected"
    TIMESHEET_REVERSED = "timesheet.reversed"
    TIMESHEET_IMPORTED = "timesheet.imported"
    TIMESHEET_EXPORTED = "timesheet.exported"
    PAYROLL_IMPORTED = "payroll.imported"
    LABOR_RECONCILIATION_VIEWED = "labor_reconciliation.viewed"
    LABOR_EXPORTED = "labor.exported"
```

- [ ] **Step 6: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/core/tests/test_access_matrix.py suno/core/tests/test_policies.py suno/core/tests/test_audit.py`
Expected: PASS.

- [ ] **Step 7: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/core && ruff check suno/core && mypy suno/core --config-file pyproject.toml"
git add server/suno/core/access.py server/suno/core/access_services.py server/suno/core/policies.py server/suno/core/audit.py server/suno/core/tests/test_access_matrix.py server/suno/core/tests/test_policies.py
git commit -m "feat(labor): labor section in the access matrix, policies and audit actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Modelos de mano de obra, migración `0031_labor` y datos de prueba

**Files:**
- Create: `server/suno/core/models/labor.py`
- Modify: `server/suno/core/models/cost.py`, `server/suno/core/models/__init__.py`
- Create (generada y completada a mano): `server/suno/core/migrations/0031_labor.py`
- Create: `server/suno/labor/__init__.py`, `server/suno/labor/tests/__init__.py` (vacíos), `server/suno/labor/tests/factories.py`
- Modify: `server/suno/core/tests/test_policies.py` (el ayudante `timesheet`)
- Test: `server/suno/labor/tests/test_models.py`, `server/suno/core/tests/test_labor_migration.py`

- [ ] **Step 1: Paquete y datos de prueba**

Crear vacíos `server/suno/labor/__init__.py` y `server/suno/labor/tests/__init__.py` (como `suno/materials/`: sin modelos ni `apps.py`).

`server/suno/labor/tests/factories.py` (las tareas siguientes le agregan ayudantes):

```python
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
        maria=make_worker(world.org, "52123456", "María Gómez", wage="100000.00",
                          position="Ayudante"),  # fmt: skip
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
```

- [ ] **Step 2: Pruebas del modelo**

`server/suno/labor/tests/test_models.py`:

```python
"""Restricciones de la base de mano de obra (spec 1 y 4 "Restricciones de la base de datos")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.test import TestCase

from suno.core.models import (
    BenefitFactor,
    CostDocumentType,
    CostEntry,
    CostStatus,
    LaborImportToken,
    PayrollImport,
    PayrollImportLine,
    ProjectWorker,
    Timesheet,
    Worker,
    WorkerWage,
)
from suno.core.models.cost import ORIGIN_FIELDS
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import entry
from suno.materials.tests.factories import bare_cut, material_world

from .factories import WEEK, bare_line, bare_timesheet, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class RegistryConstraintTests(TestCase):
    def setUp(self) -> None:
        self.lw = labor_world()
        self.org = self.lw.world.org

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_document_is_unique_per_organization(self) -> None:
        self.broken(lambda: make_worker(self.org, "1012345678", "Otro"))
        other = labor_world("Otra constructora")
        self.assertEqual(other.pedro.document_number, "1012345678")
        # Otro tipo de documento con el mismo número es otra persona.
        make_worker(self.org, "1012345678", "Extranjero", document_type="CE")

    def test_retirement_and_activity_go_together(self) -> None:
        worker = self.lw.pedro
        worker.retired_on = date(2026, 1, 1)  # antes del ingreso
        worker.is_active = False
        self.broken(worker.save)
        worker.retired_on = None  # inactivo sin fecha
        self.broken(worker.save)
        worker.retired_on, worker.is_active = date(2026, 5, 1), True  # activo con fecha
        self.broken(worker.save)
        worker.retired_on, worker.is_active = date(2026, 5, 1), False
        worker.save()

    def test_wages_are_positive_and_unique_per_day(self) -> None:
        pedro = self.lw.pedro
        self.broken(lambda: WorkerWage.objects.create(
            org=self.org, worker=pedro, valid_from=date(2026, 2, 1), daily_wage=D("0.00")))  # fmt: skip
        self.broken(lambda: WorkerWage.objects.create(
            org=self.org, worker=pedro, valid_from=pedro.hired_on, daily_wage=D("90000.00")))  # fmt: skip

    def test_factor_is_between_one_and_three_and_unique_per_day(self) -> None:
        for value in ("0.9999", "3.0001"):
            with self.subTest(value=value):
                self.broken(lambda value=value: BenefitFactor.objects.create(
                    org=self.org, valid_from=date(2026, 6, 1), factor=D(value)))  # fmt: skip
        self.broken(lambda: BenefitFactor.objects.create(
            org=self.org, valid_from=date(2026, 1, 1), factor=D("1.6")))  # fmt: skip
        BenefitFactor.objects.create(org=self.org, valid_from=date(2026, 6, 1), factor=D("3"))

    def test_assignment_period_order(self) -> None:
        world = self.lw.world
        self.broken(lambda: ProjectWorker.objects.create(
            org=self.org, project=self.lw.project, worker=self.lw.pedro,
            starts_on=date(2026, 3, 1), ends_on=date(2026, 2, 1),
            created_by=world.admin, updated_by=world.admin))  # fmt: skip


@skip_without_db
class TimesheetConstraintTests(TestCase):
    def setUp(self) -> None:
        self.lw = labor_world()

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_period_is_ordered_and_at_most_31_days(self) -> None:
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 8), end=date(2026, 3, 2)))
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 1), end=date(2026, 4, 1)))
        bare_timesheet(self.lw, start=date(2026, 3, 1), end=date(2026, 3, 31))  # 31 días

    def test_number_is_unique_per_project(self) -> None:
        bare_timesheet(self.lw)
        self.broken(lambda: bare_timesheet(self.lw, start=date(2026, 3, 9), end=date(2026, 3, 15)))
        other = labor_world("Otra")
        self.assertEqual(bare_timesheet(other).code, "TA-001")

    def test_approval_needs_support_and_someone_else(self) -> None:
        sheet = bare_timesheet(self.lw, status="approved")
        sheet.attachment = None
        self.broken(sheet.save)
        sheet.refresh_from_db()
        sheet.decided_by = sheet.created_by
        self.broken(sheet.save)
        sheet.refresh_from_db()
        sheet.decided_by = self.lw.world.supervisor
        sheet.updated_by = self.lw.world.supervisor  # quien la editó por última vez
        self.broken(sheet.save)

    def test_rejection_and_reversal_need_their_note(self) -> None:
        rejected = bare_timesheet(self.lw, status="rejected")
        rejected.decision_note = ""
        self.broken(rejected.save)
        reversed_sheet = bare_timesheet(self.lw, number=2, status="reversed",
                                        start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        reversed_sheet.reverse_note = ""
        self.broken(reversed_sheet.save)

    def test_line_fraction_frozen_values_and_uniqueness(self) -> None:
        sheet = bare_timesheet(self.lw)
        pedro, day = self.lw.pedro, WEEK[0]
        for fraction in ("0", "1.5001", "-1"):
            with self.subTest(fraction=fraction):
                self.broken(lambda fraction=fraction: bare_line(sheet, pedro, day, fraction))
        self.broken(lambda: bare_line(sheet, pedro, day, frozen=("80000.00", "1.52", "0.00")))
        self.broken(lambda: bare_line(sheet, pedro, day, frozen=("80000.00", "", "")))
        bare_line(sheet, pedro, day, "1.5")
        self.broken(lambda: bare_line(sheet, pedro, day, "0.5"))  # mismo destino y día
        bare_line(sheet, pedro, date(2026, 3, 3), frozen=("80000.00", "1.52", "121600.00"))


@skip_without_db
class LedgerOriginTests(TestCase):
    def test_the_timesheet_is_one_more_single_origin(self) -> None:
        lw = labor_world()
        self.assertIn("timesheet", ORIGIN_FIELDS)
        self.assertEqual(CostDocumentType.TIMESHEET.label, "Planilla de tareo")
        sheet = bare_timesheet(lw, status="approved")
        cost: CostEntry = entry(lw.world, status=CostStatus.APPROVED)
        cost.timesheet = sheet
        cost.save()
        self.assertTrue(cost.from_document)
        # Un corte de otra organización basta: el CHECK mira la fila, no la organización.
        cost.consumption_cut = bare_cut(material_world("Materiales"), status="approved")
        with self.assertRaises(IntegrityError), transaction.atomic():
            cost.save()

    def test_payroll_and_tokens(self) -> None:
        lw = labor_world()
        org = lw.world.org
        payroll = PayrollImport.objects.create(
            org=org, starts_on=date(2026, 3, 1), ends_on=date(2026, 3, 31),
            file_name="nomina.xlsx", file_size=10, file_sha256="a" * 64,
            imported_by=lw.world.admin,
        )  # fmt: skip
        PayrollImportLine.objects.create(org=org, payroll=payroll, row=2, document_type="CC",
                                         document_number="1", total_paid=D("10.00"))  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollImportLine.objects.create(org=org, payroll=payroll, row=3, document_type="CC",
                                             document_number="1", total_paid=D("5.00"))  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollImportLine.objects.create(org=org, payroll=payroll, row=4, document_type="CC",
                                             document_number="2", total_paid=D("0.00"))  # fmt: skip
        LaborImportToken.objects.create(org=org, nonce="n1", kind="payroll",
                                        applied_by=lw.world.admin)  # fmt: skip
        with self.assertRaises(IntegrityError), transaction.atomic():
            LaborImportToken.objects.create(org=org, nonce="n1", kind="workers",
                                            applied_by=lw.world.admin)  # fmt: skip
        self.assertEqual(Worker.objects.filter(org=org).count(), 2)
        self.assertEqual(Timesheet.objects.count(), 0)
```

`server/suno/core/tests/test_labor_migration.py`:

```python
"""La migración de mano de obra da la sección a la administración que ya existía."""

from __future__ import annotations

import unittest
from importlib import import_module

from django.apps import apps
from django.test import SimpleTestCase, TestCase

from suno.authn.tests.factories import make_org, make_user
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.models import Role, SectionGrant
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

MIGRATION = import_module("suno.core.migrations.0031_labor")

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class LaborGrantMappingTests(SimpleTestCase):
    def test_only_owner_and_admin_receive_labor(self) -> None:
        expected = (Section.LABOR, SectionRole.MANAGER, AccessScope.ORGANIZATION)
        self.assertEqual(MIGRATION.LABOR_GRANTS, {Role.OWNER: expected, Role.ADMIN: expected})


@skip_without_db
class LaborGrantBackfillTests(TestCase):
    def test_backfill_is_idempotent_and_only_for_administration(self) -> None:
        org = make_org("Previa")
        admin = make_user(org, username="admin-previa", role=Role.ADMIN)
        supervisor = make_user(org, username="super-previa", role=Role.SUPERVISOR)
        # Una membresía anterior a la migración no tenía la sección.
        SectionGrant.objects.filter(section=Section.LABOR).delete()
        MIGRATION.forwards_labor_grants(apps, None)
        MIGRATION.forwards_labor_grants(apps, None)
        grants = SectionGrant.objects.filter(section=Section.LABOR)
        self.assertEqual(
            list(grants.values_list("user_id", "role", "scope")),
            [(admin.pk, SectionRole.MANAGER, AccessScope.ORGANIZATION)],
        )
        self.assertFalse(grants.filter(user=supervisor).exists())
        MIGRATION.reverse_labor_grants(apps, None)
        self.assertFalse(SectionGrant.objects.filter(section=Section.LABOR).exists())
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_models.py suno/core/tests/test_labor_migration.py`
Expected: FAIL (`cannot import name 'BenefitFactor' from 'suno.core.models'`).

- [ ] **Step 4: El modelo**

`server/suno/core/models/labor.py`:

```python
"""Mano de obra por jornal: registro de trabajadores, tareo y nómina importada (costos,
pieza 6d).

Diseño: plans/2026-11-18-costos-pieza6d-tareo-design.md, sección 1.

- `Worker`: trabajador de la organización con datos personales mínimos (Ley 1581): documento,
  nombre, cargo, ingreso y retiro. Nada de dirección, teléfono, correo, cuenta, EPS ni fondo.
- `WorkerWage` y `BenefitFactor`: jornal básico por trabajador y factor prestacional de la
  organización, con vigencia ("desde"); el vigente un día es el de mayor "desde" ≤ ese día.
- `ProjectWorker`: asignación de un trabajador a un proyecto entre dos fechas.
- `Timesheet` (TA-001) y `TimesheetLine`: planilla de tareo por periodo; cada línea es la
  fracción de jornal de un trabajador un día en un destino de la 6a. Al aprobar se congelan
  jornal, factor y valor y se escribe un costo de Mano de obra por destino.
- `PayrollImport` y `PayrollImportLine`: la nómina real del periodo, solo para conciliar.
- `LaborImportToken`: nonce de un solo uso de las tres importaciones (ADR-0036).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import models

from .base import OrgScopedModel
from .cost import CostScope

#: Fracción máxima de una línea y tope de un trabajador en un día (spec 1.2 y 2.2).
MAX_FRACTION = Decimal("1.5")
#: Un periodo de 31 días como mucho: el fin no pasa del inicio + 30.
MAX_PERIOD_DAYS = 31


class DocumentType(models.TextChoices):
    CC = "CC", "Cédula de ciudadanía"
    CE = "CE", "Cédula de extranjería"
    PPT = "PPT", "Permiso por protección temporal"
    PEP = "PEP", "Permiso especial de permanencia"
    PA = "PA", "Pasaporte"


class TimesheetStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    REVERSED = "reversed", "Anulada"


class LaborImportKind(models.TextChoices):
    WORKERS = "workers", "Trabajadores"
    TIMESHEET = "timesheet", "Tareo"
    PAYROLL = "payroll", "Nómina"


def mask_document(number: str) -> str:
    """`***4567`: el documento en listas, planillas, PDF y Excel (spec 3.6)."""
    return f"***{number[-4:]}" if number else ""


class Worker(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(
        max_length=4, choices=DocumentType.choices, verbose_name="tipo de documento"
    )
    #: Limpio: sin puntos, espacios ni guiones, en mayúsculas (`labor.registry.clean_document`).
    document_number = models.CharField(max_length=20, verbose_name="número de documento")
    full_name = models.CharField(max_length=200, verbose_name="nombre")
    position = models.CharField(max_length=80, blank=True, verbose_name="cargo")
    hired_on = models.DateField(verbose_name="fecha de ingreso")
    retired_on = models.DateField(null=True, blank=True, verbose_name="fecha de retiro")
    is_active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "worker"
        ordering = ["full_name"]
        verbose_name = "trabajador"
        verbose_name_plural = "trabajadores"
        constraints = [
            models.UniqueConstraint(
                fields=["org", "document_type", "document_number"], name="worker_document_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(retired_on__isnull=True)
                | models.Q(retired_on__gte=models.F("hired_on")),
                name="worker_retired_after_hired",
            ),
            models.CheckConstraint(
                condition=models.Q(is_active=True, retired_on__isnull=True)
                | models.Q(is_active=False, retired_on__isnull=False),
                name="worker_active_retired",
            ),
        ]
        indexes = [models.Index(fields=["org", "position"], name="worker_position_idx")]

    @property
    def masked_document(self) -> str:
        return f"{self.document_type} {mask_document(self.document_number)}"

    @property
    def label(self) -> str:
        """ "Pedro Pérez · CC ***5678": como se muestra en listas, planillas, PDF y Excel."""
        return f"{self.full_name} · {self.masked_document}"

    def __str__(self) -> str:
        return self.label


class WorkerWage(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="wages", verbose_name="trabajador"
    )
    valid_from = models.DateField(verbose_name="desde")
    daily_wage = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="jornal básico")
    created_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "worker_wage"
        ordering = ["worker", "valid_from"]
        verbose_name = "jornal"
        verbose_name_plural = "jornales"
        constraints = [
            models.UniqueConstraint(fields=["worker", "valid_from"], name="worker_wage_day_uq"),
            models.CheckConstraint(
                condition=models.Q(daily_wage__gt=0), name="worker_wage_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.worker_id}:{self.valid_from}"


class BenefitFactor(OrgScopedModel):
    """Prestaciones sociales, seguridad social, parafiscales y dotación sobre el jornal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    valid_from = models.DateField(verbose_name="desde")
    factor = models.DecimalField(max_digits=6, decimal_places=4, verbose_name="factor")
    note = models.CharField(max_length=500, blank=True, verbose_name="composición")
    created_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "benefit_factor"
        ordering = ["valid_from"]
        verbose_name = "factor prestacional"
        verbose_name_plural = "factores prestacionales"
        constraints = [
            models.UniqueConstraint(fields=["org", "valid_from"], name="benefit_factor_day_uq"),
            models.CheckConstraint(
                condition=models.Q(factor__gte=1, factor__lte=3), name="benefit_factor_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.valid_from}:{self.factor}"


class ProjectWorker(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="labor_assignments",
        verbose_name="proyecto",
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="assignments", verbose_name="trabajador"
    )
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(null=True, blank=True, verbose_name="hasta")
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_worker"
        ordering = ["worker__full_name", "starts_on"]
        verbose_name = "asignación de trabajador"
        verbose_name_plural = "asignaciones de trabajadores"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__isnull=True)
                | models.Q(ends_on__gte=models.F("starts_on")),
                name="project_worker_period_order",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "worker", "starts_on"], name="project_worker_idx")
        ]

    def __str__(self) -> str:
        return f"{self.project_id}:{self.worker_id}:{self.starts_on}"


class Timesheet(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="timesheets", verbose_name="proyecto"
    )
    number = models.PositiveIntegerField(verbose_name="número")
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(verbose_name="hasta")
    status = models.CharField(
        max_length=10,
        choices=TimesheetStatus.choices,
        default=TimesheetStatus.DRAFT,
        verbose_name="estado",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="aprobó o rechazó",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    reversed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="anuló",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reverse_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    #: La planilla firmada (maestro y residente). Obligatoria para aprobar.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="planilla firmada",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")
    #: `fingerprint` (lo que se compara al aprobar) y `groups` (el resumen por destino que
    #: imprime el PDF), guardados en cada edición (`suno.labor.calc.Summary.snapshot`).
    summary = models.JSONField(default=dict, blank=True, verbose_name="resumen")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="costo estimado"
    )
    days = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0"), verbose_name="jornales"
    )
    line_count = models.PositiveIntegerField(default=0, verbose_name="líneas")

    class Meta:
        db_table = "timesheet"
        ordering = ["-number"]
        verbose_name = "planilla de tareo"
        verbose_name_plural = "planillas de tareo"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="timesheet_number_uq"),
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="timesheet_period_order",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    ends_on__lte=models.F("starts_on") + timedelta(days=MAX_PERIOD_DAYS - 1)
                ),
                name="timesheet_period_max_31",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status="draft", decided_by__isnull=True, decided_at__isnull=True
                )
                | models.Q(
                    status__in=["approved", "rejected", "reversed"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="timesheet_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["approved", "reversed"])
                | (
                    models.Q(attachment__isnull=False)
                    & ~models.Q(decided_by=models.F("created_by"))
                    & ~models.Q(decided_by=models.F("updated_by"))
                ),
                name="timesheet_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="timesheet_rejection_note",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="reversed")
                | (
                    models.Q(reversed_by__isnull=False, reversed_at__isnull=False)
                    & ~models.Q(reverse_note="")
                ),
                name="timesheet_reverse_note",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "project", "status"], name="timesheet_status_idx"),
            models.Index(fields=["project", "starts_on", "ends_on"], name="timesheet_period_idx"),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def code(self) -> str:
        return f"TA-{self.number:03d}"


class TimesheetLine(OrgScopedModel):
    """La fracción de jornal de un trabajador un día en un destino (el de la 6a)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timesheet = models.ForeignKey(
        Timesheet, on_delete=models.CASCADE, related_name="lines", verbose_name="planilla"
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.PROTECT, related_name="timesheet_lines", verbose_name="trabajador"
    )
    worked_on = models.DateField(verbose_name="día")
    fraction = models.DecimalField(max_digits=6, decimal_places=4, verbose_name="fracción")
    scope = models.CharField(max_length=12, choices=CostScope.choices, verbose_name="destino")
    #: `chapter`/`selection`: el capítulo. `item`: el capítulo padre del ítem (su origen).
    chapter = models.ForeignKey(
        "core.BudgetItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="capítulo",
    )
    task = models.ForeignKey(
        "core.Task",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="tarea",
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="TimesheetLineItem",
        related_name="timesheet_lines",
        verbose_name="ítems",
    )
    #: SHA-256 de `materials.destination.key_text`: identifica el destino en la unicidad.
    destination_key = models.CharField(max_length=64, verbose_name="clave del destino")
    #: Congelados al aprobar; nulos en un borrador (decisión 15 del plan).
    daily_wage = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name="jornal"
    )
    factor = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True, verbose_name="factor"
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )

    class Meta:
        db_table = "timesheet_line"
        ordering = ["worked_on"]
        verbose_name = "línea de tareo"
        verbose_name_plural = "líneas de tareo"
        constraints = [
            models.UniqueConstraint(
                fields=["timesheet", "worker", "worked_on", "destination_key"],
                name="timesheet_line_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(fraction__gt=0, fraction__lte=MAX_FRACTION),
                name="timesheet_line_fraction",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    daily_wage__isnull=True, factor__isnull=True, amount__isnull=True
                )
                | models.Q(daily_wage__gt=0, factor__gt=0, amount__gt=0),
                name="timesheet_line_frozen",
            ),
            models.CheckConstraint(
                condition=models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
                | models.Q(scope="item")
                | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False),
                name="timesheet_line_destination",
            ),
        ]
        indexes = [
            models.Index(fields=["worker", "worked_on"], name="timesheet_line_worker_day_idx"),
            models.Index(fields=["timesheet", "worked_on"], name="timesheet_line_day_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.timesheet_id}:{self.worker_id}:{self.worked_on}"


class TimesheetLineItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(TimesheetLine, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "timesheet_line_item"
        constraints = [
            models.UniqueConstraint(fields=["line", "item"], name="timesheet_line_item_uq"),
        ]


class PayrollImport(OrgScopedModel):
    """La nómina real de un periodo, importada para conciliar. No toca el libro. Del archivo
    se guardan nombre, tamaño y huella, no el archivo (decisión 21 del plan)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    starts_on = models.DateField(verbose_name="desde")
    ends_on = models.DateField(verbose_name="hasta")
    file_name = models.CharField(max_length=200, verbose_name="archivo")
    file_size = models.PositiveIntegerField(verbose_name="tamaño")
    file_sha256 = models.CharField(max_length=64, verbose_name="huella del archivo")
    line_count = models.PositiveIntegerField(default=0, verbose_name="líneas")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total pagado"
    )
    imported_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="importó"
    )
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payroll_import"
        ordering = ["-starts_on", "-imported_at"]
        verbose_name = "nómina importada"
        verbose_name_plural = "nóminas importadas"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="payroll_import_period_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.starts_on}:{self.ends_on}"


class PayrollImportLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payroll = models.ForeignKey(
        PayrollImport, on_delete=models.CASCADE, related_name="lines", verbose_name="nómina"
    )
    #: Fila del Excel (el encabezado es la 1).
    row = models.PositiveIntegerField(verbose_name="fila")
    document_type = models.CharField(max_length=4, verbose_name="tipo de documento")
    document_number = models.CharField(max_length=20, verbose_name="número de documento")
    #: El trabajador del registro con ese documento, si lo hay.
    worker = models.ForeignKey(
        Worker,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payroll_lines",
        verbose_name="trabajador",
    )
    #: Costo total del trabajador en el periodo según la nómina (devengado, aportes y
    #: provisiones).
    total_paid = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="total pagado")

    class Meta:
        db_table = "payroll_import_line"
        ordering = ["row"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll", "document_type", "document_number"],
                name="payroll_import_line_document_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(total_paid__gt=0), name="payroll_import_line_positive"
            ),
        ]


class LaborImportToken(OrgScopedModel):
    """Una vista previa de importación ya cargada (como `MaterialImportToken`): su `nonce` va
    firmado en la vista previa y es único."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nonce = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=10, choices=LaborImportKind.choices)
    applied_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labor_import_token"
        verbose_name = "importación de mano de obra aplicada"
        verbose_name_plural = "importaciones de mano de obra aplicadas"
```

- [ ] **Step 5: El libro conoce la planilla**

En `server/suno/core/models/cost.py`:

1. En `CostDocumentType`, antes de `OTHER`: `TIMESHEET = "timesheet", "Planilla de tareo"`.
2. `ORIGIN_FIELDS` queda:

```python
#: Los documentos que escriben movimientos del libro (6b, 6c y 6d). Un movimiento con uno de
#: ellos no se edita ni se reversa desde el libro: se anula el documento.
ORIGIN_FIELDS = (
    "certificate",
    "material_purchase",
    "consumption_cut",
    "inventory_adjustment",
    "timesheet",
)
```

3. Después del campo `inventory_adjustment`:

```python
    #: Pieza 6d: la planilla de tareo aprobada que lo generó (un movimiento por destino). Se
    #: anula la planilla, no el movimiento.
    timesheet = models.ForeignKey(
        "core.Timesheet",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="planilla de tareo",
    )
```

4. El docstring de `from_document` pasa a "Viene de un acta, una compra, un corte, un ajuste o una planilla de tareo." y el del módulo agrega: "La planilla de tareo (6d) también."

- [ ] **Step 6: Reexportar**

En `server/suno/core/models/__init__.py`, en el docstring: `- \`labor\` — trabajadores, jornales, factor prestacional, asignaciones, planillas de tareo y nómina importada (costos, pieza 6d).`; después del bloque `from .layer import …` (orden alfabético):

```python
from .labor import (
    BenefitFactor,
    DocumentType,
    LaborImportKind,
    LaborImportToken,
    PayrollImport,
    PayrollImportLine,
    ProjectWorker,
    Timesheet,
    TimesheetLine,
    TimesheetLineItem,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
```

y los mismos 13 nombres en `__all__`, en su lugar alfabético. (`mask_document` no se reexporta: se importa de `suno.core.models.labor`.)

En `server/suno/core/tests/test_policies.py`, el ayudante de la Task 1 pasa a usar el modelo real: agregar `Timesheet` al import de `suno.core.models` y

```python
def timesheet(org_id: uuid.UUID = ORG, created_by_id: uuid.UUID = USER) -> Timesheet:
    return Timesheet(org_id=org_id, project_id=uuid.uuid4(), created_by_id=created_by_id)
```

- [ ] **Step 7: Generar la migración y agregarle la sección**

```bash
bash infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name labor"
```

Expected: `server/suno/core/migrations/0031_labor.py` con `dependencies = [('core', '0030_materials')]`, los `CreateModel` de las 10 tablas, el `AddField` de `costentry.timesheet`, el `AlterField` de `costentry.document_type` y de `sectiongrant.section`, `RemoveConstraint`/`AddConstraint` de `section_grant_role_allowed` (con `LABOR`) y de `cost_entry_single_origin` (ahora con `timesheet`).

Agregarle a mano, arriba de `class Migration` (con `from django.utils import timezone` en los imports):

```python
#: Como la 0028 con Costos: la administración que ya existía recibe Mano de obra.
LABOR_GRANTS = {
    "OWNER": ("LABOR", "MANAGER", "organization"),
    "ADMIN": ("LABOR", "MANAGER", "organization"),
}


def forwards_labor_grants(apps, schema_editor):
    """Idempotente: no toca a quien ya tiene la sección (como `LEGACY_GRANTS`)."""
    Membership = apps.get_model("core", "Membership")
    SectionGrant = apps.get_model("core", "SectionGrant")
    now = timezone.now()
    existing = set(
        SectionGrant.objects.filter(section="LABOR").values_list("org_id", "user_id")
    )
    grants = []
    for org_id, user_id, role in Membership.objects.filter(
        role__in=list(LABOR_GRANTS)
    ).values_list("org_id", "user_id", "role"):
        if (org_id, user_id) in existing:
            continue
        section, grant_role, scope = LABOR_GRANTS[role]
        existing.add((org_id, user_id))
        grants.append(
            SectionGrant(
                org_id=org_id,
                user_id=user_id,
                section=section,
                role=grant_role,
                scope=scope,
                active=True,
                granted_at=now,
                updated_at=now,
            )
        )
    SectionGrant.objects.bulk_create(grants)


def reverse_labor_grants(apps, schema_editor):
    apps.get_model("core", "SectionProjectScope").objects.filter(grant__section="LABOR").delete()
    apps.get_model("core", "SectionGrant").objects.filter(section="LABOR").delete()
```

y como **última** operación de la lista: `migrations.RunPython(forwards_labor_grants, reverse_labor_grants),` (después del `AddConstraint` de `section_grant_role_allowed`, para que la restricción nueva ya admita `LABOR`). En la reversa, Django deshace en orden inverso: primero borra los grants de `LABOR` y después restaura la restricción vieja, que ya no los ve. Revisar que la migración no nombre la del renombre (la guardia de nombres no necesita lista blanca).

- [ ] **Step 8: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_models.py suno/core/tests/test_labor_migration.py suno/core/tests/test_costs_migration.py suno/core/tests/test_policies.py suno/core/tests/test_access_web.py suno/costs/tests/test_models.py suno/materials/tests/test_models.py`
Expected: PASS. (La matriz de accesos en la web pinta una columna "Mano de obra" más, porque recorre `Section.values`.)

- [ ] **Step 9: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/core suno/labor && ruff check suno/core suno/labor && python manage.py makemigrations --check --dry-run"
git add server/suno/core/models server/suno/core/migrations/0031_labor.py server/suno/core/tests/test_policies.py server/suno/core/tests/test_labor_migration.py server/suno/labor
git commit -m "feat(labor): workers, wages, benefit factor, assignments, timesheets and payroll models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Cálculo puro: vigencias, costo por línea, resumen y tope

**Files:**
- Create: `server/suno/labor/calc.py`
- Test: `server/suno/labor/tests/test_calc.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_calc.py`:

```python
"""Cálculo del tareo (spec 2.1 y 2.2, 4 "Cálculo"). Puro: `SimpleTestCase`."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.labor.calc import (
    DAY_CAP,
    History,
    LineCost,
    fraction_ok,
    line_amount,
    over_cap,
    price,
    problems,
    summarize,
)

D = Decimal
PEDRO, MARIA = uuid.uuid4(), uuid.uuid4()
MON, TUE = date(2026, 3, 2), date(2026, 3, 3)


class HistoryTests(SimpleTestCase):
    def test_the_value_in_force_is_the_latest_start_not_after_the_day(self) -> None:
        wages = History.of([(date(2026, 3, 3), D("90000.00")), (date(2026, 1, 5), D("80000.00"))])
        self.assertIsNone(wages.on(date(2026, 1, 4)))
        self.assertEqual(wages.on(date(2026, 1, 5)), D("80000.00"))
        self.assertEqual(wages.on(MON), D("80000.00"))
        self.assertEqual(wages.on(TUE), D("90000.00"))  # el día exacto del cambio
        self.assertEqual(wages.on(date(2027, 1, 1)), D("90000.00"))
        self.assertIsNone(History().on(MON))


class AmountTests(SimpleTestCase):
    def test_line_amount_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(line_amount(D("1"), D("80000.00"), D("1.5200")), D("121600.00"))
        self.assertEqual(line_amount(D("0.5"), D("100000.00"), D("1.52")), D("76000.00"))
        # 0,3333 × 75.000,00 × 1,5213 = 38.028,69675: 38.028,70 (se redondea cada línea)
        self.assertEqual(line_amount(D("0.3333"), D("75000.00"), D("1.5213")), D("38028.70"))
        # 0,0001 × 1,00 × 1 = 0,0001: cero centavos (no se aprueba, decisión 15)
        self.assertEqual(line_amount(D("0.0001"), D("1.00"), D("1")), D("0.00"))
        self.assertEqual(line_amount(D("0.125"), D("80000.05"), D("1")), D("10000.01"))

    def test_fraction_rules(self) -> None:
        for value, ok in (("1", True), ("1.5", True), ("0.0001", True), ("0", False),
                          ("1.5001", False), ("-0.5", False), ("0.00001", False)):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(fraction_ok(D(value)), ok)
        self.assertFalse(fraction_ok(None))
        self.assertFalse(fraction_ok(D("NaN")))


class SummaryTests(SimpleTestCase):
    def priced(self) -> list[LineCost]:
        wages = {
            PEDRO: History.of([(date(2026, 1, 5), D("80000.00")), (TUE, D("90000.00"))]),
            MARIA: History.of([(date(2026, 1, 5), D("100000.00"))]),
        }
        factors = History.of([(date(2026, 1, 1), D("1.5200"))])
        return price(
            [
                (PEDRO, MON, "a", D("1")),
                (PEDRO, TUE, "a", D("0.5")),
                (PEDRO, TUE, "b", D("0.5")),
                (MARIA, MON, "b", D("0.3333")),
            ],
            wages,
            factors,
        )

    def test_destination_cost_is_the_sum_of_rounded_lines(self) -> None:
        summary = summarize(self.priced())
        # Pedro: 121.600,00 + 68.400,00 (jornal nuevo desde el martes) + 68.400,00
        # María: 0,3333 × 100.000 × 1,52 = 50.661,60
        self.assertEqual(summary.groups["a"].amount, D("190000.00"))
        self.assertEqual(summary.groups["b"].amount, D("119061.60"))
        self.assertEqual(summary.total, D("309061.60"))
        self.assertEqual(summary.days, D("2.3333"))
        self.assertEqual(summary.workers[str(PEDRO)].days, D("2.0000"))
        self.assertEqual(summary.lines, 4)
        self.assertTrue(summary.complete)
        self.assertEqual(
            sum(group.amount for group in summary.groups.values()), summary.total
        )  # cuadra al peso

    def test_fingerprint_changes_with_a_wage_and_snapshot_has_labels(self) -> None:
        before = summarize(self.priced()).fingerprint()
        wages = {
            PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))]),
            MARIA: History.of([(date(2026, 1, 5), D("100000.00"))]),
        }
        after = summarize(
            price([(PEDRO, TUE, "a", D("0.5")), (PEDRO, MON, "a", D("1")),
                   (PEDRO, TUE, "b", D("0.5")), (MARIA, MON, "b", D("0.3333"))],
                  wages, History.of([(date(2026, 1, 1), D("1.5200"))]))  # fmt: skip
        ).fingerprint()
        self.assertNotEqual(before, after)
        snapshot = summarize(self.priced()).snapshot({"a": "2.1 Concreto", "b": "Indirecto"})
        self.assertEqual(snapshot["fingerprint"], before)
        self.assertEqual([g["label"] for g in snapshot["groups"]], ["2.1 Concreto", "Indirecto"])
        self.assertEqual(snapshot["groups"][0]["total"], "190000.00")

    def test_missing_wage_or_factor_and_zero_cost_are_problems(self) -> None:
        costs = price(
            [(PEDRO, date(2026, 1, 2), "a", D("1")), (MARIA, MON, "a", D("0.0001"))],
            {PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))]),
             MARIA: History.of([(date(2025, 1, 1), D("1.00"))])},
            History.of([(date(2026, 1, 1), D("1"))]),
        )  # fmt: skip
        summary = summarize(costs)
        self.assertFalse(summary.complete)
        self.assertEqual(
            problems(costs, {PEDRO: "Pedro Pérez · CC ***5678", MARIA: "María Gómez · CC ***3456"}),
            [
                "Pedro Pérez · CC ***5678 no tiene jornal vigente el 02/01/2026.",
                "La línea de María Gómez · CC ***3456 del 02/03/2026 cuesta $0: revisa la "
                "fracción.",
            ],
        )
        no_factor = price([(PEDRO, MON, "a", D("1"))],
                          {PEDRO: History.of([(date(2026, 1, 5), D("80000.00"))])}, History())  # fmt: skip
        self.assertEqual(
            problems(no_factor, {PEDRO: "Pedro"}),
            ["No hay factor prestacional vigente el 02/03/2026."],
        )


class CapTests(SimpleTestCase):
    def test_over_cap_lists_worker_days_above_one_and_a_half(self) -> None:
        self.assertEqual(DAY_CAP, D("1.5"))
        totals = {(PEDRO, MON): D("1.5"), (PEDRO, TUE): D("1.5001"), (MARIA, MON): D("2")}
        found = over_cap(totals)
        self.assertEqual(len(found), 2)
        self.assertIn((PEDRO, TUE, D("1.5001")), found)
        self.assertIn((MARIA, MON, D("2")), found)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_calc.py`
Expected: FAIL (`No module named 'suno.labor.calc'`).

- [ ] **Step 3: El cálculo**

`server/suno/labor/calc.py`:

```python
"""Costo estimado del tareo (costos, pieza 6d, spec 2.1 y 2.2). Puro: sin BD.

- Vigente un día: el valor de mayor "desde" que no pasa de ese día (jornal y factor).
- Costo de una línea = fracción × jornal vigente × factor vigente, a centavos (ROUND_HALF_UP).
  El de un destino es la suma de sus líneas ya redondeadas: la planilla y el libro cuadran al
  peso.
- Tope: nadie suma más de 1,5 jornales un mismo día entre todas las planillas no anuladas.
- La huella (`Summary.fingerprint`) es lo que se compara al aprobar: líneas, jornales, total y,
  por destino y por trabajador, jornales y costo (decisión 14 del plan).
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from suno.budgets.totals import WIDE_PRECISION, ZERO, money, quantity
from suno.core.models.labor import MAX_FRACTION

DAY_CAP = MAX_FRACTION
NO_DAYS = Decimal("0.0000")


@dataclass(frozen=True)
class History:
    """Valores con vigencia, ordenados por su "desde"."""

    starts: tuple[date, ...] = ()
    values: tuple[Decimal, ...] = ()

    @classmethod
    def of(cls, rows: Iterable[tuple[date, Decimal]]) -> History:
        ordered = sorted(rows, key=lambda row: row[0])
        return cls(tuple(row[0] for row in ordered), tuple(row[1] for row in ordered))

    def on(self, day: date) -> Decimal | None:
        index = bisect_right(self.starts, day)
        return self.values[index - 1] if index else None


def fraction_ok(value: Decimal | None) -> bool:
    """0 < fracción ≤ 1,5, con 4 decimales como mucho."""
    return (
        value is not None
        and value.is_finite()
        and 0 < value <= MAX_FRACTION
        and quantity(value) == value
    )


def line_amount(fraction: Decimal, wage: Decimal, factor: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return money(fraction * wage * factor)


@dataclass(frozen=True)
class LineCost:
    worker_id: UUID
    day: date
    #: Identifica el destino (en la BD, `TimesheetLine.destination_key`).
    key: str
    fraction: Decimal
    wage: Decimal | None
    factor: Decimal | None

    @property
    def amount(self) -> Decimal | None:
        if self.wage is None or self.factor is None:
            return None
        return line_amount(self.fraction, self.wage, self.factor)


def price(
    lines: Iterable[tuple[UUID, date, str, Decimal]],
    wages: Mapping[UUID, History],
    factors: History,
) -> list[LineCost]:
    """Cada línea (trabajador, día, destino, fracción) con el jornal y el factor de su día."""
    empty = History()
    return [
        LineCost(worker, day, key, fraction, wages.get(worker, empty).on(day), factors.on(day))
        for worker, day, key, fraction in lines
    ]


def problems(costs: Sequence[LineCost], names: Mapping[UUID, str]) -> list[str]:
    """Lo que impide aprobar: falta de jornal o de factor vigente, o una línea en $0."""
    found: list[str] = []
    for cost in sorted(costs, key=lambda c: (c.day, names.get(c.worker_id, ""))):
        name = names.get(cost.worker_id, "—")
        if cost.wage is None:
            text = f"{name} no tiene jornal vigente el {cost.day:%d/%m/%Y}."
        elif cost.factor is None:
            text = f"No hay factor prestacional vigente el {cost.day:%d/%m/%Y}."
        elif cost.amount == ZERO:
            text = (
                f"La línea de {name} del {cost.day:%d/%m/%Y} cuesta $0: revisa la fracción."
            )
        else:
            continue
        if text not in found:
            found.append(text)
    return found


@dataclass(frozen=True)
class Totals:
    days: Decimal
    amount: Decimal


@dataclass(frozen=True)
class Summary:
    lines: int
    days: Decimal
    total: Decimal
    #: Todas las líneas tienen jornal y factor y cuestan más de $0.
    complete: bool
    groups: Mapping[str, Totals]
    workers: Mapping[str, Totals]

    def fingerprint(self) -> dict[str, Any]:
        return {
            "lines": self.lines,
            "days": str(self.days),
            "total": str(self.total),
            "complete": self.complete,
            "groups": {k: [str(v.days), str(v.amount)] for k, v in sorted(self.groups.items())},
            "workers": {k: [str(v.days), str(v.amount)] for k, v in sorted(self.workers.items())},
        }

    def snapshot(self, labels: Mapping[str, str]) -> dict[str, Any]:
        """La huella y el resumen por destino que imprime el PDF (`Timesheet.summary`)."""
        groups = [
            {"label": labels.get(key, "—"), "days": str(v.days), "total": str(v.amount)}
            for key, v in self.groups.items()
        ]
        groups.sort(key=lambda group: group["label"])
        return {"fingerprint": self.fingerprint(), "groups": groups}


def _add(bucket: dict[str, list[Decimal]], key: str, days: Decimal, amount: Decimal) -> None:
    row = bucket.setdefault(key, [NO_DAYS, ZERO])
    row[0] += days
    row[1] += amount


def summarize(costs: Sequence[LineCost]) -> Summary:
    groups: dict[str, list[Decimal]] = {}
    workers: dict[str, list[Decimal]] = {}
    for cost in costs:
        amount = cost.amount if cost.amount is not None else ZERO
        _add(groups, cost.key, cost.fraction, amount)
        _add(workers, str(cost.worker_id), cost.fraction, amount)
    return Summary(
        lines=len(costs),
        days=quantity(sum((cost.fraction for cost in costs), NO_DAYS)),
        total=sum((row[1] for row in groups.values()), ZERO),
        complete=all(cost.amount is not None and cost.amount > 0 for cost in costs),
        groups={key: Totals(quantity(d), a) for key, (d, a) in groups.items()},
        workers={key: Totals(quantity(d), a) for key, (d, a) in workers.items()},
    )


def over_cap(totals: Mapping[tuple[UUID, date], Decimal]) -> list[tuple[UUID, date, Decimal]]:
    """Los (trabajador, día, jornales) que pasan del tope, por día y trabajador."""
    found = [(worker, day, total) for (worker, day), total in totals.items() if total > DAY_CAP]
    found.sort(key=lambda row: (row[1], str(row[0])))
    return found


def days_text(value: Decimal) -> str:
    """ "1,75": jornales con coma decimal y sin ceros de sobra."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def totals_by_day(rows: Iterable[tuple[UUID, date, Decimal]]) -> dict[tuple[UUID, date], Decimal]:
    totals: dict[tuple[UUID, date], Decimal] = defaultdict(lambda: NO_DAYS)
    for worker, day, fraction in rows:
        totals[(worker, day)] += fraction
    return dict(totals)
```

- [ ] **Step 4: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_calc.py`
Expected: PASS.

- [ ] **Step 5: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor/calc.py server/suno/labor/tests/test_calc.py
git commit -m "feat(labor): wage and factor validity, line cost, summary fingerprint and daily cap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Piezas comunes y registro de trabajadores, jornales y factor (servicios)

**Files:**
- Create: `server/suno/labor/common.py`, `server/suno/labor/registry.py`
- Test: `server/suno/labor/tests/test_registry.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_registry.py`:

```python
"""Registro de trabajadores, jornales y factor (spec 1.1 y 3.1; decisiones 3 y 6 a 8)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, BenefitFactor, ProjectWorker, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import registry
from suno.labor.common import LaborRejected
from suno.labor.registry import WorkerData

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


def data(**fields: object) -> WorkerData:
    values: dict[str, object] = {
        "document_type": "CC",
        "document_number": "1.023.456.789",
        "full_name": "  Luis Torres ",
        "position": "Oficial",
        "hired_on": date(2026, 2, 1),
    }
    values.update(fields)
    return WorkerData(**values)  # type: ignore[arg-type]


@skip_without_db
class WorkerTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_create_cleans_the_document_and_starts_the_wage_at_hiring(self) -> None:
        worker = registry.create_worker(self.ctx, data(), D("85000.00"))
        self.assertEqual(worker.document_number, "1023456789")
        self.assertEqual(worker.full_name, "Luis Torres")
        wage = WorkerWage.objects.get(worker=worker)
        self.assertEqual((wage.valid_from, wage.daily_wage), (date(2026, 2, 1), D("85000.00")))
        event = AuditEvent.objects.get(action="worker.created")
        self.assertEqual(event.metadata["document"], "CC ***6789")
        self.assertNotIn("Luis", str(event.metadata))  # sin nombres en la auditoría

    def test_invalid_data_and_duplicates_are_rejected(self) -> None:
        for fields, message in (
            ({"document_number": "12"}, "El número de documento"),
            ({"document_number": "ABC-12$"}, "El número de documento"),
            ({"document_type": "XX"}, "Elige el tipo de documento"),
            ({"full_name": " "}, "Escribe el nombre"),
            ({"hired_on": date(2026, 5, 1)}, "no puede ser futura"),
            ({"document_number": "1012345678"}, "Ya hay un trabajador con CC ***5678"),
        ):
            with self.subTest(fields=fields), self.assertRaises(LaborRejected) as rejected:
                registry.create_worker(self.ctx, data(**fields), D("85000.00"))
            self.assertIn(message, rejected.exception.errors[0])
        with self.assertRaises(LaborRejected):
            registry.create_worker(self.ctx, data(), D("0"))

    def test_only_organization_managers_write_the_registry(self) -> None:
        limited = self.lw.ctx(self.lw.viewer)  # Consulta
        with self.assertRaises(PermissionDenied):
            registry.create_worker(limited, data(), D("85000.00"))
        accountant = self.lw.ctx(self.lw.accountant)  # solo Costos
        with self.assertRaises(PermissionDenied):
            registry.add_wage(accountant, self.lw.pedro.pk, date(2026, 4, 1), D("90000.00"))

    def test_retire_closes_assignments_and_respects_tareo(self) -> None:
        sheet = bare_timesheet(self.lw)
        bare_line(sheet, self.lw.pedro, WEEK[1])
        with self.assertRaises(LaborRejected) as early:
            registry.retire_worker(self.ctx, self.lw.pedro.pk, WEEK[0])
        self.assertIn("08/03/2026", early.exception.errors[0])
        worker = registry.retire_worker(self.ctx, self.lw.pedro.pk, date(2026, 3, 31))
        self.assertFalse(worker.is_active)
        assignment = ProjectWorker.objects.get(worker=worker)
        self.assertEqual(assignment.ends_on, date(2026, 3, 31))
        registry.reinstate_worker(self.ctx, worker.pk)
        worker.refresh_from_db()
        self.assertTrue(worker.is_active)
        self.assertIsNone(worker.retired_on)

    def test_delete_only_without_tareo(self) -> None:
        sheet = bare_timesheet(self.lw)
        bare_line(sheet, self.lw.pedro, WEEK[0])
        with self.assertRaises(LaborRejected):
            registry.delete_worker(self.ctx, self.lw.pedro.pk)
        registry.delete_worker(self.ctx, self.lw.maria.pk)  # sin tareo: se borra con su historial
        self.assertFalse(Worker.objects.filter(pk=self.lw.maria.pk).exists())
        self.assertFalse(WorkerWage.objects.filter(worker_id=self.lw.maria.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action="worker.deleted").exists())


@skip_without_db
class WageAndFactorTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_a_change_is_a_new_row_and_used_rows_are_frozen(self) -> None:
        pedro = self.lw.pedro
        new = registry.add_wage(self.ctx, pedro.pk, date(2026, 4, 1), D("90000.00"))
        self.assertEqual(WorkerWage.objects.filter(worker=pedro).count(), 2)
        with self.assertRaises(LaborRejected):  # mismo día
            registry.add_wage(self.ctx, pedro.pk, date(2026, 4, 1), D("95000.00"))
        with self.assertRaises(LaborRejected):  # antes del ingreso
            registry.add_wage(self.ctx, pedro.pk, date(2025, 12, 1), D("95000.00"))
        registry.change_wage(self.ctx, new.pk, D("91000.00"))  # nadie la usó
        first = WorkerWage.objects.get(worker=pedro, valid_from=pedro.hired_on)
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))
        with self.assertRaises(LaborRejected) as retroactive:  # cambiaría lo congelado
            registry.add_wage(self.ctx, pedro.pk, date(2026, 3, 1), D("95000.00"))
        self.assertIn("02/03/2026", retroactive.exception.errors[0])
        with self.assertRaises(LaborRejected) as used:
            registry.change_wage(self.ctx, first.pk, D("81000.00"))
        self.assertIn("planilla aprobada", used.exception.errors[0])
        with self.assertRaises(LaborRejected):
            registry.delete_wage(self.ctx, first.pk)
        registry.delete_wage(self.ctx, new.pk)  # la del 1 de abril no la usa nadie
        event = AuditEvent.objects.filter(action="worker.wage_changed").latest("created_at")
        self.assertEqual(event.metadata["worker"], str(pedro.pk))

    def test_a_draft_does_not_freeze_a_wage(self) -> None:
        first = WorkerWage.objects.get(worker=self.lw.pedro)
        bare_line(bare_timesheet(self.lw), self.lw.pedro, WEEK[0])
        registry.change_wage(self.ctx, first.pk, D("81000.00"))
        first.refresh_from_db()
        self.assertEqual(first.daily_wage, D("81000.00"))

    def test_factor_history(self) -> None:
        factor = registry.add_factor(self.ctx, date(2026, 4, 1), D("1.55"), "Dotación nueva")
        self.assertEqual(factor.factor, D("1.5500"))
        for value in ("0.99", "3.01", "1.55555"):
            with self.subTest(value=value), self.assertRaises(LaborRejected):
                registry.add_factor(self.ctx, date(2026, 5, 1), D(value), "")
        first = BenefitFactor.objects.get(org=self.lw.world.org, valid_from=date(2026, 1, 1))
        sheet = bare_timesheet(self.lw, status="reversed")
        bare_line(sheet, self.lw.maria, WEEK[0], frozen=("100000.00", "1.5200", "152000.00"))
        with self.assertRaises(LaborRejected):  # una anulada también la usó
            registry.change_factor(self.ctx, first.pk, D("1.6"), "")
        registry.change_factor(self.ctx, factor.pk, D("1.56"), "Corrección")
        registry.delete_factor(self.ctx, factor.pk)
        self.assertTrue(AuditEvent.objects.filter(action="benefit_factor.changed").exists())
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_registry.py`
Expected: FAIL (`No module named 'suno.labor.registry'`).

- [ ] **Step 3: Las piezas comunes**

`server/suno/labor/common.py`:

```python
"""Piezas compartidas de los servicios de mano de obra (costos, pieza 6d).

- Orden de bloqueo (ADR-0035, decisión 16 del plan): la fila del proyecto
  (`materials.common.lock_project`, `FOR NO KEY UPDATE`), la planilla (`FOR UPDATE`), las filas
  de los trabajadores en orden de `pk` (`FOR NO KEY UPDATE`) y, al aprobar, las del factor.
  Cambiar un jornal bloquea solo la fila del trabajador; cambiar el factor, las del factor. Así
  dos aprobaciones en obras distintas no dejan a nadie por encima del tope y no hay ciclos.
- Nadie aprueba ni rechaza lo que creó o editó por última vez (también la base lo exige).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied

from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.core.context import RequestContext
from suno.core.models import BenefitFactor, Blob, TimesheetStatus, Worker, WorkerWage
from suno.core.scoping import scoped_qs

from .calc import History

MAX_NOTE = 500
MAX_NAME = 200
MAX_POSITION = 80
MAX_FILE_NAME = 200
MAX_DESCRIPTION = 300
MAX_ERRORS = 50
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."
#: Las planillas que se editan (un rechazado, solo su autor) y las que cuentan para el tope.
OPEN_STATES = (str(TimesheetStatus.DRAFT), str(TimesheetStatus.REJECTED))
COUNTED_STATES = (*OPEN_STATES, str(TimesheetStatus.APPROVED))
#: Las que congelaron jornal y factor (decisión 7 del plan).
FROZEN_STATES = (str(TimesheetStatus.APPROVED), str(TimesheetStatus.REVERSED))


class LaborRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "Rechazado")


def lock_workers(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, Worker]:
    """Bloquea las filas de los trabajadores en orden de `pk` y las devuelve."""
    ids = list(set(worker_ids))
    if not ids:
        return {}
    rows = (
        scoped_qs(ctx, Worker).select_for_update(no_key=True).filter(pk__in=ids).order_by("pk")
    )
    return {worker.pk: worker for worker in rows}


def lock_factors(ctx: RequestContext) -> None:
    """Bloquea las filas del factor de la organización en orden de `pk`."""
    list(
        scoped_qs(ctx, BenefitFactor)
        .select_for_update(no_key=True)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def wage_histories(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, History]:
    rows: dict[UUID, list[Any]] = defaultdict(list)
    for worker_id, valid_from, wage in (
        scoped_qs(ctx, WorkerWage)
        .filter(worker_id__in=list(set(worker_ids)))
        .values_list("worker_id", "valid_from", "daily_wage")
    ):
        rows[worker_id].append((valid_from, wage))
    return {worker_id: History.of(found) for worker_id, found in rows.items()}


def factor_history(ctx: RequestContext) -> History:
    return History.of(scoped_qs(ctx, BenefitFactor).values_list("valid_from", "factor"))


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise LaborRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise LaborRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un borrador o un rechazado; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise LaborRejected(["Solo se edita o se borra una planilla en borrador o rechazada."])
    if obj.status == TimesheetStatus.REJECTED and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != TimesheetStatus.DRAFT:
        raise LaborRejected(["Solo se aprueba o se rechaza una planilla en borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise LaborRejected([SELF_DECISION])


def stored_upload(upload: Upload | None) -> Blob | None:
    """Sube el soporte antes de la transacción: no se tiene el bloqueo mientras se escribe."""
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise LaborRejected([str(exc)]) from exc
```

- [ ] **Step 4: Los servicios del registro**

`server/suno/labor/registry.py`:

```python
"""Registro de trabajadores, jornales y factor prestacional (costos, pieza 6d, spec 1.1 y 3.1).

- Escribir pide `can_manage_labor_registry` (create y update de Mano de obra en toda la
  organización).
- Datos mínimos (Ley 1581): documento, nombre, cargo, ingreso y retiro.
- Un cambio de jornal o de factor es una fila nueva; una fila que ya usó una planilla aprobada
  o anulada no se edita ni se borra (decisión 7 del plan).
- La auditoría lleva ids y el documento enmascarado, nunca nombres (decisión 25).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    BenefitFactor,
    DocumentType,
    ProjectWorker,
    TimesheetLine,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
from suno.core.models.labor import mask_document
from suno.core.policies import can_manage_labor_registry
from suno.core.scoping import scoped_qs

from .common import FROZEN_STATES, MAX_NAME, MAX_NOTE, MAX_POSITION, LaborRejected, lock_factors

MIN_DOCUMENT, MAX_DOCUMENT = 3, 20
MAX_WAGE = Decimal("999999999999.99")
FIRST_DAY = date(2000, 1, 1)
DOCUMENT_CONSTRAINT = "worker_document_uq"
USED = "Este valor ya lo usó una planilla aprobada o anulada: registra un cambio con otra fecha."


@dataclass(frozen=True)
class WorkerData:
    document_type: str
    document_number: str
    full_name: str
    position: str
    hired_on: date


def clean_document(number: str) -> str:
    """ "1.012.345.678" → "1012345678"; sin espacios ni guiones, en mayúsculas."""
    return "".join(ch for ch in number if ch not in ". -").upper()


def worker_errors(data: WorkerData) -> list[str]:
    errors: list[str] = []
    if data.document_type not in DocumentType.values:
        errors.append("Elige el tipo de documento (CC, CE, PPT, PEP o PA).")
    number = clean_document(data.document_number)
    if not (MIN_DOCUMENT <= len(number) <= MAX_DOCUMENT and number.isascii() and number.isalnum()):
        errors.append(
            f"El número de documento lleva de {MIN_DOCUMENT} a {MAX_DOCUMENT} letras o números."
        )
    if not data.full_name.strip():
        errors.append("Escribe el nombre.")
    elif len(data.full_name.strip()) > MAX_NAME:
        errors.append(f"El nombre pasa de {MAX_NAME} caracteres.")
    if len(data.position.strip()) > MAX_POSITION:
        errors.append(f"El cargo pasa de {MAX_POSITION} caracteres.")
    if not FIRST_DAY <= data.hired_on <= timezone.localdate():
        errors.append("La fecha de ingreso no puede ser futura ni anterior a 2000.")
    return errors


def wage_ok(value: Decimal | None) -> bool:
    return value is not None and value.is_finite() and 0 < value <= MAX_WAGE and money(value) == value


def factor_ok(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and 1 <= value <= 3
        and value.quantize(Decimal("0.0001")) == value
    )


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def _metadata(worker: Worker) -> dict[str, Any]:
    return {
        "worker": str(worker.pk),
        "document": f"{worker.document_type} {mask_document(worker.document_number)}",
    }


def _locked_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    found = scoped_qs(ctx, Worker).select_for_update(no_key=True).filter(pk=worker_id).first()
    if found is None:
        raise Http404
    return found


def _duplicate(ctx: RequestContext, data: WorkerData, exclude: UUID | None = None) -> str:
    number = clean_document(data.document_number)
    rows = scoped_qs(ctx, Worker).filter(document_type=data.document_type, document_number=number)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    if rows.exists():
        return f"Ya hay un trabajador con {data.document_type} {mask_document(number)}."
    return ""


def create_worker(ctx: RequestContext, data: WorkerData, daily_wage: Decimal) -> Worker:
    """Alta con su primer jornal, vigente desde la fecha de ingreso."""
    _require_manager(ctx)
    errors = worker_errors(data)
    if not wage_ok(daily_wage):
        errors.append("El jornal básico va en pesos, mayor que cero y a centavos.")
    if not errors and (problem := _duplicate(ctx, data)):
        errors.append(problem)
    if errors:
        raise LaborRejected(errors)
    try:
        with transaction.atomic():
            worker = scoped_qs(ctx, Worker).create(
                org_id=ctx.org_id,
                document_type=data.document_type,
                document_number=clean_document(data.document_number),
                full_name=data.full_name.strip(),
                position=data.position.strip(),
                hired_on=data.hired_on,
            )
            scoped_qs(ctx, WorkerWage).create(
                org_id=ctx.org_id,
                worker=worker,
                valid_from=data.hired_on,
                daily_wage=daily_wage,
                created_by_id=ctx.user_id,
            )
            audit.record(
                ctx,
                action=AuditAction.WORKER_CREATED,
                target=f"worker:{worker.pk}",
                metadata={**_metadata(worker), "wage": str(daily_wage)},
            )
    except IntegrityError as exc:
        if DOCUMENT_CONSTRAINT not in str(exc):
            raise
        raise LaborRejected([_duplicate(ctx, data) or "Ese documento ya existe."]) from exc
    return worker


def update_worker(ctx: RequestContext, worker_id: UUID, data: WorkerData) -> Worker:
    """Corrige documento, nombre y cargo. La fecha de ingreso no pasa del primer jornal ni de la
    primera asignación."""
    _require_manager(ctx)
    errors = worker_errors(data)
    if errors:
        raise LaborRejected(errors)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if problem := _duplicate(ctx, data, exclude=worker.pk):
            raise LaborRejected([problem])
        first_wage = (
            scoped_qs(ctx, WorkerWage).filter(worker=worker).order_by("valid_from").first()
        )
        first_assignment = (
            scoped_qs(ctx, ProjectWorker).filter(worker=worker).order_by("starts_on").first()
        )
        limits = [
            row
            for row in (
                first_wage.valid_from if first_wage else None,
                first_assignment.starts_on if first_assignment else None,
            )
            if row is not None
        ]
        if limits and data.hired_on > min(limits):
            raise LaborRejected(
                [f"La fecha de ingreso no puede pasar del {min(limits):%d/%m/%Y} (primer jornal "
                 "o primera asignación)."]
            )  # fmt: skip
        worker.document_type = data.document_type
        worker.document_number = clean_document(data.document_number)
        worker.full_name = data.full_name.strip()
        worker.position = data.position.strip()
        worker.hired_on = data.hired_on
        worker.save()
        audit.record(
            ctx, action=AuditAction.WORKER_UPDATED, target=f"worker:{worker.pk}",
            metadata=_metadata(worker),
        )  # fmt: skip
    return worker


def _last_tareo(ctx: RequestContext, worker: Worker) -> date | None:
    last = (
        scoped_qs(ctx, TimesheetLine)
        .filter(worker=worker)
        .exclude(timesheet__status=TimesheetStatus.REVERSED)
        .aggregate(day=Max("worked_on"))["day"]
    )
    return last


def retire_worker(ctx: RequestContext, worker_id: UUID, retired_on: date) -> Worker:
    """Lo inactiva y cierra en esa fecha las asignaciones abiertas o que terminan después."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if retired_on < worker.hired_on:
            raise LaborRejected(["El retiro no puede ser antes del ingreso."])
        last = _last_tareo(ctx, worker)
        if last is not None and retired_on < last:
            raise LaborRejected(
                [f"Tiene tareo hasta el {last:%d/%m/%Y}: el retiro va desde esa fecha."]
            )
        scoped_qs(ctx, ProjectWorker).filter(worker=worker).filter(
            Q(ends_on__isnull=True) | Q(ends_on__gt=retired_on), starts_on__lte=retired_on
        ).update(ends_on=retired_on, updated_by_id=ctx.user_id, updated_at=timezone.now())
        later = scoped_qs(ctx, ProjectWorker).filter(worker=worker, starts_on__gt=retired_on)
        if later.exists():
            raise LaborRejected(["Tiene asignaciones que empiezan después del retiro: bórralas."])
        worker.retired_on = retired_on
        worker.is_active = False
        worker.save(update_fields=["retired_on", "is_active", "updated_at"])
        audit.record(
            ctx, action=AuditAction.WORKER_RETIRED, target=f"worker:{worker.pk}",
            metadata={**_metadata(worker), "retired_on": retired_on.isoformat()},
        )  # fmt: skip
    return worker


def reinstate_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    """Deshace un retiro (corrección). Las asignaciones cerradas se reabren a mano."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        worker.retired_on = None
        worker.is_active = True
        worker.save(update_fields=["retired_on", "is_active", "updated_at"])
        audit.record(
            ctx, action=AuditAction.WORKER_UPDATED, target=f"worker:{worker.pk}",
            metadata={**_metadata(worker), "reinstated": True},
        )  # fmt: skip
    return worker


def delete_worker(ctx: RequestContext, worker_id: UUID) -> None:
    """Solo sin tareo (en ninguna planilla, ni anulada); se van sus jornales y asignaciones."""
    _require_manager(ctx)
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if scoped_qs(ctx, TimesheetLine).filter(worker=worker).exists():
            raise LaborRejected(["Tiene tareo: no se borra; retíralo."])
        metadata = _metadata(worker)
        scoped_qs(ctx, ProjectWorker).filter(worker=worker).delete()
        scoped_qs(ctx, WorkerWage).filter(worker=worker).delete()
        worker.delete()
        audit.record(
            ctx, action=AuditAction.WORKER_DELETED, target=f"worker:{metadata['worker']}",
            metadata=metadata,
        )  # fmt: skip


def _next_start(rows: Any, valid_from: date) -> date | None:
    return rows.filter(valid_from__gt=valid_from).order_by("valid_from").values_list(
        "valid_from", flat=True
    ).first()


def _used(ctx: RequestContext, lines: Any, valid_from: date, until: date | None) -> bool:
    found = lines.filter(timesheet__status__in=FROZEN_STATES, worked_on__gte=valid_from)
    if until is not None:
        found = found.filter(worked_on__lt=until)
    return bool(found.exists())


def _require_after_frozen(lines: Any, valid_from: date) -> None:
    """Un jornal o un factor nuevo no puede regir sobre días de planillas aprobadas o anuladas:
    cambiaría el valor vigente de lo que ya se congeló (decisión 7 del plan)."""
    last = (
        lines.filter(timesheet__status__in=FROZEN_STATES, worked_on__gte=valid_from)
        .order_by("-worked_on")
        .values_list("worked_on", flat=True)
        .first()
    )
    if last is not None:
        raise LaborRejected(
            [f"Hay planillas aprobadas hasta el {last:%d/%m/%Y}: el valor nuevo rige desde el día "
             "siguiente."]
        )  # fmt: skip


def wage_in_use(ctx: RequestContext, wage: WorkerWage) -> bool:
    rows = scoped_qs(ctx, WorkerWage).filter(worker_id=wage.worker_id)
    lines = scoped_qs(ctx, TimesheetLine).filter(worker_id=wage.worker_id)
    return _used(ctx, lines, wage.valid_from, _next_start(rows, wage.valid_from))


def factor_in_use(ctx: RequestContext, factor: BenefitFactor) -> bool:
    rows = scoped_qs(ctx, BenefitFactor)
    return _used(ctx, scoped_qs(ctx, TimesheetLine), factor.valid_from,
                 _next_start(rows, factor.valid_from))  # fmt: skip


def _wage_audit(ctx: RequestContext, worker: Worker, change: str, wage: WorkerWage) -> None:
    audit.record(
        ctx,
        action=AuditAction.WORKER_WAGE_CHANGED,
        target=f"worker:{worker.pk}",
        metadata={
            **_metadata(worker),
            "change": change,
            "valid_from": wage.valid_from.isoformat(),
            "wage": str(wage.daily_wage),
        },
    )


def add_wage(
    ctx: RequestContext, worker_id: UUID, valid_from: date, daily_wage: Decimal
) -> WorkerWage:
    _require_manager(ctx)
    if not wage_ok(daily_wage):
        raise LaborRejected(["El jornal básico va en pesos, mayor que cero y a centavos."])
    with transaction.atomic():
        worker = _locked_worker(ctx, worker_id)
        if valid_from < worker.hired_on:
            raise LaborRejected(["El jornal no puede regir antes del ingreso."])
        if scoped_qs(ctx, WorkerWage).filter(worker=worker, valid_from=valid_from).exists():
            raise LaborRejected([f"Ya hay un jornal desde el {valid_from:%d/%m/%Y}."])
        _require_after_frozen(scoped_qs(ctx, TimesheetLine).filter(worker=worker), valid_from)
        wage = scoped_qs(ctx, WorkerWage).create(
            org_id=ctx.org_id, worker=worker, valid_from=valid_from, daily_wage=daily_wage,
            created_by_id=ctx.user_id,
        )  # fmt: skip
        _wage_audit(ctx, worker, "added", wage)
    return wage


def _locked_wage(ctx: RequestContext, wage_id: UUID) -> tuple[Worker, WorkerWage]:
    found = scoped_qs(ctx, WorkerWage).filter(pk=wage_id).values_list("worker_id", flat=True).first()
    if found is None:
        raise Http404
    worker = _locked_worker(ctx, found)
    wage = scoped_qs(ctx, WorkerWage).select_for_update().get(pk=wage_id)
    return worker, wage


def change_wage(ctx: RequestContext, wage_id: UUID, daily_wage: Decimal) -> WorkerWage:
    _require_manager(ctx)
    if not wage_ok(daily_wage):
        raise LaborRejected(["El jornal básico va en pesos, mayor que cero y a centavos."])
    with transaction.atomic():
        worker, wage = _locked_wage(ctx, wage_id)
        if wage_in_use(ctx, wage):
            raise LaborRejected([USED])
        wage.daily_wage = daily_wage
        wage.save(update_fields=["daily_wage"])
        _wage_audit(ctx, worker, "changed", wage)
    return wage


def delete_wage(ctx: RequestContext, wage_id: UUID) -> None:
    """Un trabajador conserva siempre su primer jornal (el de la fecha de ingreso)."""
    _require_manager(ctx)
    with transaction.atomic():
        worker, wage = _locked_wage(ctx, wage_id)
        if wage_in_use(ctx, wage):
            raise LaborRejected([USED])
        if wage.valid_from <= worker.hired_on:
            raise LaborRejected(["El jornal de ingreso no se borra: cámbialo."])
        _wage_audit(ctx, worker, "deleted", wage)
        wage.delete()


def _factor_audit(ctx: RequestContext, change: str, factor: BenefitFactor) -> None:
    audit.record(
        ctx,
        action=AuditAction.BENEFIT_FACTOR_CHANGED,
        target=f"benefit_factor:{factor.pk}",
        metadata={
            "change": change,
            "valid_from": factor.valid_from.isoformat(),
            "factor": str(factor.factor),
        },
    )


def _factor_checks(value: Decimal, note: str) -> None:
    errors = []
    if not factor_ok(value):
        errors.append("El factor va entre 1 y 3, con 4 decimales como mucho.")
    if len(note.strip()) > MAX_NOTE:
        errors.append(f"La nota pasa de {MAX_NOTE} caracteres.")
    if errors:
        raise LaborRejected(errors)


def add_factor(ctx: RequestContext, valid_from: date, value: Decimal, note: str) -> BenefitFactor:
    _require_manager(ctx)
    _factor_checks(value, note)
    with transaction.atomic():
        lock_factors(ctx)
        if scoped_qs(ctx, BenefitFactor).filter(valid_from=valid_from).exists():
            raise LaborRejected([f"Ya hay un factor desde el {valid_from:%d/%m/%Y}."])
        _require_after_frozen(scoped_qs(ctx, TimesheetLine), valid_from)
        factor = scoped_qs(ctx, BenefitFactor).create(
            org_id=ctx.org_id, valid_from=valid_from, factor=value, note=note.strip(),
            created_by_id=ctx.user_id,
        )  # fmt: skip
        factor.refresh_from_db()
        _factor_audit(ctx, "added", factor)
    return factor


def _locked_factor(ctx: RequestContext, factor_id: UUID) -> BenefitFactor:
    lock_factors(ctx)
    found = scoped_qs(ctx, BenefitFactor).filter(pk=factor_id).first()
    if found is None:
        raise Http404
    return found


def change_factor(
    ctx: RequestContext, factor_id: UUID, value: Decimal, note: str
) -> BenefitFactor:
    _require_manager(ctx)
    _factor_checks(value, note)
    with transaction.atomic():
        factor = _locked_factor(ctx, factor_id)
        if factor_in_use(ctx, factor):
            raise LaborRejected([USED])
        factor.factor = value
        factor.note = note.strip()
        factor.save(update_fields=["factor", "note"])
        _factor_audit(ctx, "changed", factor)
    return factor


def delete_factor(ctx: RequestContext, factor_id: UUID) -> None:
    _require_manager(ctx)
    with transaction.atomic():
        factor = _locked_factor(ctx, factor_id)
        if factor_in_use(ctx, factor):
            raise LaborRejected([USED])
        _factor_audit(ctx, "deleted", factor)
        factor.delete()


def previous_day(day: date) -> date:
    return day - timedelta(days=1)
```

(`previous_day` la usa la ficha para mostrar "hasta" de cada fila del historial.)

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_registry.py`
Expected: PASS.

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): worker registry, wage history and benefit factor services

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Importaciones en dos pasos (piezas compartidas) e importación de trabajadores

**Files:**
- Create: `server/suno/labor/imports.py`, `server/suno/labor/registry_import.py`
- Modify: `server/suno/labor/tests/factories.py` (`xlsx`)
- Test: `server/suno/labor/tests/test_registry_import.py`

- [ ] **Step 1: Ayuda de prueba**

Al final de `server/suno/labor/tests/factories.py` (con `from suno.backoffice.export import to_xlsx_book` en los imports):

```python
def xlsx(sheet: str, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """Un libro de una hoja, con el escritor del repositorio (las celdas de texto que empiezan
    con = + - @ salen con apóstrofo, como en un exporte real)."""
    return to_xlsx_book([(sheet, list(header), [list(row) for row in rows])])
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_registry_import.py`:

```python
"""Importar trabajadores desde Excel (spec 3.1; decisión 8 del plan), con un Excel adversarial
(spec 4 "Importaciones")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, LaborImportToken, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import registry_import
from suno.labor.common import LaborRejected

from .factories import TODAY, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Nombre", "Cargo", "Fecha de ingreso",
          "Jornal", "Jornal desde"]  # fmt: skip


@skip_without_db
class WorkerImportTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()

    def test_preview_classifies_and_apply_writes_everything_once(self) -> None:
        book = xlsx("Trabajadores", HEADER, [
            ["CC", "1.023.456.789", "Luis Torres", "Oficial", "2026-02-01", 85000, ""],
            ["CC", 1012345678, "Pedro Pérez Ruiz", "Maestro", "05/01/2026", "95.000", "01/04/2026"],
            ["CC", "52123456", "María Gómez", "Ayudante", "2026-01-05", "100000", ""],
            ["", "", "", "", "", "", ""],
        ])  # fmt: skip
        preview = registry_import.preview(self.ctx, book)
        self.assertEqual(preview.errors, ())
        self.assertEqual(
            [(row.line, row.action) for row in preview.rows],
            [(2, "nuevo"), (3, "actualiza"), (4, "sin cambios")],
        )
        created, updated = registry_import.apply(self.ctx, preview.token)
        self.assertEqual((created, updated), (1, 1))
        luis = Worker.objects.get(document_number="1023456789")
        self.assertEqual(WorkerWage.objects.get(worker=luis).valid_from, date(2026, 2, 1))
        pedro = Worker.objects.get(pk=self.lw.pedro.pk)
        self.assertEqual((pedro.full_name, pedro.position), ("Pedro Pérez Ruiz", "Maestro"))
        self.assertEqual(pedro.hired_on, date(2026, 1, 5))  # el ingreso de uno existente no cambia
        new_wage = WorkerWage.objects.get(worker=pedro, valid_from=date(2026, 4, 1))
        self.assertEqual(new_wage.daily_wage, D("95000.00"))
        self.assertEqual(LaborImportToken.objects.get().kind, "workers")
        event = AuditEvent.objects.get(action="workers.imported")
        self.assertEqual(event.metadata, {"created": 1, "updated": 1})
        with self.assertRaises(LaborRejected) as again:
            registry_import.apply(self.ctx, preview.token)
        self.assertIn("ya se cargó", again.exception.errors[0])

    def test_a_new_wage_without_its_date_starts_today(self) -> None:
        book = xlsx("Trabajadores", HEADER[:6],
                    [["CC", "1012345678", "Pedro Pérez", "Oficial", "2026-01-05", "90000"]])  # fmt: skip
        registry_import.apply(self.ctx, registry_import.preview(self.ctx, book).token)
        self.assertTrue(WorkerWage.objects.filter(worker=self.lw.pedro, valid_from=TODAY).exists())

    def test_adversarial_file_lists_every_bad_row_and_applies_nothing(self) -> None:
        book = xlsx("Trabajadores", HEADER, [
            ["CC", "=1+1", "Fórmula", "", "2026-02-01", 85000, ""],        # texto con =
            ["CC", "70999888", "Fecha rara", "", "31/02/2026", 85000, ""],
            ["CC", "70111222", "Repetido", "", "2026-02-01", 85000, ""],
            ["CC", "70111222", "Repetido", "", "2026-02-01", 85000, ""],
            ["XX", "80111222", "Tipo malo", "", "2026-02-01", 85000, ""],
            ["CC", "90111222", "Sin jornal", "", "2026-02-01", "", ""],
            ["CC", "91111222", "Jornal texto", "", "2026-02-01", "mucho", ""],
            ["CC", "92111222", "Futuro", "", "2026-12-01", 85000, ""],
        ])  # fmt: skip
        preview = registry_import.preview(self.ctx, book)
        self.assertEqual(preview.token, "")
        text = "\n".join(preview.errors)
        for line in (2, 3, 5, 6, 7, 8, 9):
            with self.subTest(line=line):
                self.assertIn(f"Fila {line}:", text)
        self.assertNotIn("Fila 4:", text)  # la primera de las repetidas está bien
        self.assertIn("se repite (fila 4)", text)
        self.assertEqual(Worker.objects.count(), 2)

    def test_missing_columns_wrong_sheet_and_permissions(self) -> None:
        preview = registry_import.preview(self.ctx, xlsx("Trabajadores", ["Nombre"], [["Ana"]]))
        self.assertIn("Falta la columna «Tipo de documento».", preview.errors)
        with self.assertRaises(PermissionDenied):
            registry_import.preview(self.lw.ctx(self.lw.viewer), xlsx("Trabajadores", HEADER, []))
        token = registry_import.preview(self.ctx, xlsx("Trabajadores", HEADER, [
            ["CC", "70111222", "Ana", "", "2026-02-01", 85000, ""]])).token  # fmt: skip
        with self.assertRaises(LaborRejected):  # la vista previa es de quien la generó
            registry_import.apply(self.lw.ctx(self.lw.world.supervisor), token)
        with self.assertRaises(LaborRejected):
            registry_import.apply(self.ctx, token + "x")
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_registry_import.py`
Expected: FAIL (`No module named 'suno.labor.registry_import'`).

- [ ] **Step 4: Las piezas compartidas de las importaciones**

`server/suno/labor/imports.py`:

```python
"""Piezas de las tres importaciones de mano de obra (trabajadores, tareo y nómina), como el
catálogo de materiales (ADR-0036): la vista previa no guarda nada; las filas viajan firmadas una
hora con un nonce de un solo uso (`LaborImportToken`, con su `kind`); al aplicar se valida otra
vez. Todo o nada."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.core import signing
from django.db import IntegrityError, transaction

from suno.budgets.mapping import normalize_header
from suno.budgets.totals import parse_number
from suno.core.context import RequestContext
from suno.core.models import LaborImportToken
from suno.core.scoping import scoped_qs

from .common import LaborRejected

TOKEN_MAX_AGE = 3600
INVALID = "La vista previa no es válida: vuelve a subir el archivo."
ALREADY_APPLIED = "Esta vista previa ya se cargó: revisa los datos antes de importar otra vez."
MORE_ERRORS = "Hay más errores: corrige estos y vuelve a subir el archivo."


def cell_text(value: Any) -> str:
    """Texto de una celda. Un entero de Excel ("12") sale sin decimales; se quita el apóstrofo
    que `safe_cell` antepone a lo que empieza con = + - @."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def cell_day(value: Any) -> date | None:
    """Una fecha de Excel, "AAAA-MM-DD" o "DD/MM/AAAA"; `None` si está vacía o no es fecha."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = cell_text(value)
    try:
        if "/" in text:
            day, month, year = (int(part) for part in text.split("/"))
            return date(year, month, day)
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def cell_decimal(value: Any) -> Decimal | None:
    """Un número de celda o texto ("95.000", "1,5"); `None` si está vacío; `ValueError` si no es
    un número."""
    if isinstance(value, bool):
        raise ValueError("no es un número")
    return parse_number(value)


def columns(
    header: Sequence[Any], names: Sequence[str], required: Sequence[str]
) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in names}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in required if name not in found]


def row_texts(values: Sequence[Any], found: Mapping[str, int], names: Sequence[str]) -> list[Any]:
    """Los valores crudos de una fila en el orden de `names` (vacío si falta la columna)."""
    return [
        values[found[name]] if name in found and found[name] < len(values) else None
        for name in names
    ]


def sign(ctx: RequestContext, salt: str, body: Mapping[str, Any]) -> str:
    payload = {
        "org": str(ctx.org_id),
        "user": str(ctx.user_id),
        "nonce": secrets.token_urlsafe(24),
        **body,
    }
    return signing.dumps(payload, salt=salt, compress=True)


def load(ctx: RequestContext, salt: str, token: str) -> Mapping[str, Any]:
    try:
        payload = signing.loads(token, salt=salt, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise LaborRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise LaborRejected([INVALID]) from exc
    if payload.get("org") != str(ctx.org_id) or payload.get("user") != str(ctx.user_id):
        raise LaborRejected(["La vista previa no es tuya."])
    if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
        raise LaborRejected([INVALID])
    return payload


def consume(ctx: RequestContext, kind: str, nonce: str) -> None:
    """Marca la vista previa como cargada (dentro de la transacción de quien aplica)."""
    try:
        with transaction.atomic():
            scoped_qs(ctx, LaborImportToken).create(
                org_id=ctx.org_id, nonce=nonce, kind=kind, applied_by_id=ctx.user_id
            )
    except IntegrityError as exc:
        raise LaborRejected([ALREADY_APPLIED]) from exc
```

- [ ] **Step 5: La importación de trabajadores**

`server/suno/labor/registry_import.py`:

```python
"""Importar trabajadores desde Excel (costos, pieza 6d, spec 3.1; decisión 8 del plan).

Hoja "Trabajadores" con el encabezado en la fila 1: Tipo de documento, Número de documento,
Nombre, Cargo, Fecha de ingreso, Jornal y Jornal desde (opcional). Un documento nuevo crea al
trabajador con su jornal desde el ingreso; uno existente actualiza nombre y cargo y, si el
jornal difiere del vigente hoy, agrega una fila desde "Jornal desde" (sin ella, desde hoy).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import LaborImportKind, Worker, WorkerWage
from suno.core.policies import can_manage_labor_registry
from suno.core.scoping import scoped_qs

from . import imports
from .calc import History
from .common import MAX_ERRORS, LaborRejected
from .registry import WorkerData, clean_document, wage_ok, worker_errors

SHEET = "Trabajadores"
H_TYPE, H_NUMBER, H_NAME, H_POSITION = "Tipo de documento", "Número de documento", "Nombre", "Cargo"
H_HIRED, H_WAGE, H_FROM = "Fecha de ingreso", "Jornal", "Jornal desde"
HEADER = [H_TYPE, H_NUMBER, H_NAME, H_POSITION, H_HIRED, H_WAGE, H_FROM]
REQUIRED = (H_TYPE, H_NUMBER, H_NAME, H_HIRED, H_WAGE)
MAX_ROWS = 2000
SALT = "suno.labor.workers"
NEW, UPDATE, SAME = "nuevo", "actualiza", "sin cambios"


@dataclass(frozen=True)
class WorkerRow:
    line: int
    data: WorkerData
    wage: Decimal
    wage_from: date | None
    action: str


@dataclass(frozen=True)
class WorkerPreview:
    rows: tuple[WorkerRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def _existing(ctx: RequestContext) -> dict[tuple[str, str], Worker]:
    return {(w.document_type, w.document_number): w for w in scoped_qs(ctx, Worker)}


def _wages(ctx: RequestContext) -> dict[Any, History]:
    rows: dict[Any, list[Any]] = {}
    for worker_id, valid_from, wage in scoped_qs(ctx, WorkerWage).values_list(
        "worker_id", "valid_from", "daily_wage"
    ):
        rows.setdefault(worker_id, []).append((valid_from, wage))
    return {key: History.of(value) for key, value in rows.items()}


def _classify(row: WorkerRow, current: Worker | None, wages: dict[Any, History]) -> tuple[str, str]:
    if current is None:
        return NEW, ""
    today = timezone.localdate()
    wage_from = row.wage_from or today
    same_wage = wages.get(current.pk, History()).on(wage_from) == row.wage
    if not same_wage and wage_from < current.hired_on:
        return "", "El jornal no puede regir antes del ingreso."
    same = (current.full_name, current.position) == (row.data.full_name.strip(),
                                                     row.data.position.strip())  # fmt: skip
    return (SAME if same and same_wage else UPDATE), ""


def _parse(line: int, raw: list[Any]) -> tuple[WorkerRow | None, list[str]]:
    kind, number, name, position, hired, wage_text, wage_from = raw
    problems: list[str] = []
    hired_on = imports.cell_day(hired)
    if hired_on is None:
        problems.append("La fecha de ingreso no es una fecha (AAAA-MM-DD o DD/MM/AAAA).")
    try:
        wage = imports.cell_decimal(wage_text)
    except ValueError:
        wage = None
    if not wage_ok(wage):
        problems.append("El jornal va en pesos, mayor que cero y a centavos.")
    starts = imports.cell_day(wage_from)
    if imports.cell_text(wage_from) and starts is None:
        problems.append("«Jornal desde» no es una fecha.")
    data = WorkerData(
        document_type=imports.cell_text(kind).upper(),
        document_number=imports.cell_text(number),
        full_name=imports.cell_text(name),
        position=imports.cell_text(position),
        hired_on=hired_on or date(2000, 1, 1),
    )
    problems += worker_errors(data) if hired_on is not None else [
        p for p in worker_errors(data) if "ingreso" not in p
    ]
    if problems or wage is None:
        return None, problems
    return WorkerRow(line, data, wage, starts, ""), []


def preview(ctx: RequestContext, data: bytes) -> WorkerPreview:
    _require_manager(ctx)
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, sheet, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return WorkerPreview(errors=(str(exc),))
    if not table:
        return WorkerPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return WorkerPreview(errors=tuple(missing))
    existing, wages = _existing(ctx), _wages(ctx)
    rows: list[WorkerRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, values in enumerate(table[1:], start=2):
        raw = imports.row_texts(values, found, HEADER)
        if not any(imports.cell_text(value) for value in raw):
            continue
        row, problems = _parse(line, raw)
        if row is not None:
            key = (row.data.document_type, clean_document(row.data.document_number))
            if key in seen:
                problems.append(f"El documento se repite (fila {seen[key]}).")
            seen.setdefault(key, line)
            action, problem = _classify(row, existing.get(key), wages)
            if problem:
                problems.append(problem)
            row = WorkerRow(row.line, row.data, row.wage, row.wage_from, action)
        else:
            number = clean_document(imports.cell_text(raw[1]))
            key = (imports.cell_text(raw[0]).upper(), number)
            if number and key in seen:
                problems.append(f"El documento se repite (fila {seen[key]}).")
            seen.setdefault(key, line)
        if problems:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        elif row is not None:
            rows.append(row)
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de trabajadores.")
    if errors:
        return WorkerPreview(rows=tuple(rows), errors=tuple(errors))
    body = {
        "rows": [
            [r.line, r.data.document_type, r.data.document_number, r.data.full_name,
             r.data.position, r.data.hired_on.isoformat(), str(r.wage),
             r.wage_from.isoformat() if r.wage_from else ""]
            for r in rows
        ]
    }  # fmt: skip
    return WorkerPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body))


def apply(ctx: RequestContext, token: str) -> tuple[int, int]:
    """(creados, actualizados). Todo o nada; los trabajadores existentes se bloquean en orden de
    `pk` (ADR-0035)."""
    _require_manager(ctx)
    payload = imports.load(ctx, SALT, token)
    parsed = [
        WorkerRow(int(line), WorkerData(kind, number, name, position, date.fromisoformat(hired)),
                  Decimal(wage), date.fromisoformat(since) if since else None, "")
        for line, kind, number, name, position, hired, wage, since in payload.get("rows", [])
    ]  # fmt: skip
    if not parsed:
        raise LaborRejected(["La vista previa no tiene filas."])
    today = timezone.localdate()
    with transaction.atomic():
        imports.consume(ctx, str(LaborImportKind.WORKERS), payload["nonce"])
        existing = {
            (w.document_type, w.document_number): w
            for w in scoped_qs(ctx, Worker).select_for_update(no_key=True).order_by("pk")
        }
        wages = _wages(ctx)
        created: list[Worker] = []
        changed: list[Worker] = []
        new_wages: list[WorkerWage] = []
        problems: list[str] = []
        for row in parsed:
            key = (row.data.document_type, clean_document(row.data.document_number))
            current = existing.get(key)
            errors = worker_errors(row.data)
            action, problem = _classify(row, current, wages)
            if problem:
                errors.append(problem)
            if errors:
                problems += [f"Fila {row.line}: {error}" for error in errors]
                continue
            if current is None:
                worker = Worker(
                    org_id=ctx.org_id, document_type=key[0], document_number=key[1],
                    full_name=row.data.full_name.strip(), position=row.data.position.strip(),
                    hired_on=row.data.hired_on,
                )  # fmt: skip
                created.append(worker)
                new_wages.append(WorkerWage(org_id=ctx.org_id, worker=worker,
                                            valid_from=row.data.hired_on, daily_wage=row.wage,
                                            created_by_id=ctx.user_id))  # fmt: skip
            elif action == UPDATE:
                current.full_name = row.data.full_name.strip()
                current.position = row.data.position.strip()
                changed.append(current)
                since = row.wage_from or today
                if wages.get(current.pk, History()).on(since) != row.wage:
                    new_wages.append(WorkerWage(org_id=ctx.org_id, worker=current,
                                                valid_from=since, daily_wage=row.wage,
                                                created_by_id=ctx.user_id))  # fmt: skip
        if problems:
            raise LaborRejected(problems[:MAX_ERRORS])
        scoped_qs(ctx, Worker).bulk_create(created)
        scoped_qs(ctx, Worker).bulk_update(changed, ["full_name", "position"])
        clash = {(w.worker_id, w.valid_from) for w in new_wages} & set(
            scoped_qs(ctx, WorkerWage)
            .filter(worker_id__in=[w.worker_id for w in new_wages])
            .values_list("worker_id", "valid_from")
        )
        if clash:
            raise LaborRejected(["Ya hay un jornal con esa fecha: cambia «Jornal desde»."])
        scoped_qs(ctx, WorkerWage).bulk_create(new_wages)
        audit.record(
            ctx,
            action=AuditAction.WORKERS_IMPORTED,
            target=f"organization:{ctx.org_id}",
            metadata={"created": len(created), "updated": len(changed)},
        )
    return len(created), len(changed)
```

(En `_parse`, cuando la fecha de ingreso no se lee se usa un valor de relleno solo para validar el resto, y se descarta el error de "ingreso" de `worker_errors` para no repetirlo; la fila igual queda con error.)

- [ ] **Step 6: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_registry_import.py`
Expected: PASS. Si "31/02/2026" llega del Excel como texto, `cell_day` devuelve `None` (`ValueError` de `date`) y la fila 3 lleva "La fecha de ingreso no es una fecha".

- [ ] **Step 7: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): two-step worker import with signed preview and one-time nonce

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Asignaciones al proyecto (servicios)

**Files:**
- Create: `server/suno/labor/assignments.py`
- Test: `server/suno/labor/tests/test_assignments.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_assignments.py`:

```python
"""Asignaciones de trabajadores al proyecto (spec 1.2 y 3.2 "Asignaciones"; decisión 9)."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, Project, ProjectWorker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import assignments
from suno.labor.assignments import AssignmentData
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class AssignmentTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.luis = make_worker(self.lw.world.org, "70111222", "Luis Torres",
                                hired=date(2026, 2, 1))  # fmt: skip
        self.ctx = self.lw.ctx(self.lw.contributor)

    def test_create_within_employment_and_without_overlaps(self) -> None:
        project = self.lw.project
        row = assignments.create_assignment(
            self.ctx, project, AssignmentData(self.luis.pk, date(2026, 2, 1), date(2026, 3, 31))
        )
        self.assertEqual(row.created_by, self.lw.contributor)
        for data, message in (
            (AssignmentData(self.luis.pk, date(2026, 3, 1), None), "Se cruza"),
            (AssignmentData(self.luis.pk, date(2026, 1, 15), None), "antes del ingreso"),
            (AssignmentData(self.luis.pk, date(2026, 4, 10), date(2026, 4, 1)), "termina antes"),
        ):
            with self.subTest(data=data), self.assertRaises(LaborRejected) as rejected:
                assignments.create_assignment(self.ctx, project, data)
            self.assertIn(message, rejected.exception.errors[0])
        assignments.create_assignment(
            self.ctx, project, AssignmentData(self.luis.pk, date(2026, 4, 1), None)
        )
        self.assertEqual(ProjectWorker.objects.filter(worker=self.luis).count(), 2)
        self.assertEqual(AuditEvent.objects.filter(action="labor_assignment.changed").count(), 2)

    def test_a_worker_may_work_in_several_projects(self) -> None:
        other = Project.objects.create(
            org=self.lw.world.org, name="Obra 2", created_by=self.lw.world.admin
        )
        assignments.create_assignment(
            self.lw.ctx(), other, AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), None)
        )
        self.assertEqual(ProjectWorker.objects.filter(worker=self.lw.pedro).count(), 2)

    def test_retired_worker_and_permissions(self) -> None:
        self.luis.retired_on, self.luis.is_active = date(2026, 3, 15), False
        self.luis.save()
        with self.assertRaises(LaborRejected) as rejected:
            assignments.create_assignment(
                self.ctx, self.lw.project, AssignmentData(self.luis.pk, date(2026, 3, 1), None)
            )
        self.assertIn("retiro", rejected.exception.errors[0])
        with self.assertRaises(PermissionDenied):
            assignments.create_assignment(
                self.lw.ctx(self.lw.viewer), self.lw.project,
                AssignmentData(self.lw.maria.pk, date(2026, 6, 1), None),
            )  # fmt: skip

    def test_edit_and_delete_keep_tareo_inside(self) -> None:
        pedro_row = ProjectWorker.objects.get(worker=self.lw.pedro)
        bare_line(bare_timesheet(self.lw), self.lw.pedro, WEEK[1])
        admin = self.lw.ctx()
        with self.assertRaises(LaborRejected) as cut:
            assignments.update_assignment(
                admin, self.lw.project, pedro_row.pk,
                AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), date(2026, 3, 5)),
            )  # fmt: skip
        self.assertIn("08/03/2026", cut.exception.errors[0])
        with self.assertRaises(LaborRejected):
            assignments.delete_assignment(admin, self.lw.project, pedro_row.pk)
        assignments.update_assignment(
            admin, self.lw.project, pedro_row.pk,
            AssignmentData(self.lw.pedro.pk, date(2026, 1, 5), date(2026, 3, 31)),
        )  # fmt: skip
        maria_row = ProjectWorker.objects.get(worker=self.lw.maria)
        assignments.delete_assignment(admin, self.lw.project, maria_row.pk)
        self.assertFalse(ProjectWorker.objects.filter(pk=maria_row.pk).exists())

    def test_contributor_only_edits_its_own(self) -> None:
        pedro_row = ProjectWorker.objects.get(worker=self.lw.pedro)  # la creó la administración
        with self.assertRaises(PermissionDenied):
            assignments.delete_assignment(self.ctx, self.lw.project, pedro_row.pk)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_assignments.py`
Expected: FAIL (`No module named 'suno.labor.assignments'`).

- [ ] **Step 3: Las asignaciones**

`server/suno/labor/assignments.py`:

```python
"""Asignaciones de trabajadores al proyecto (costos, pieza 6d, spec 1.2; decisión 9 del plan).

- Crear pide `can_create_labor`; editar y borrar, `can_update_labor`/`can_delete_labor` (el
  Colaborador, solo las suyas).
- Dentro del vínculo (ingreso ≤ inicio; fin ≤ retiro) y sin cruces en el mismo proyecto: se
  comprueba con el proyecto bloqueado (`lock_project`).
- Editar o borrar no deja fuera días ya tareados en planillas no anuladas del proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Project, ProjectWorker, TimesheetLine, TimesheetStatus, Worker
from suno.core.models.labor import mask_document
from suno.core.policies import can_create_labor, can_delete_labor, can_update_labor
from suno.core.scoping import scoped_qs
from suno.materials.common import lock_project, locked

from .common import LaborRejected


@dataclass(frozen=True)
class AssignmentData:
    worker_id: UUID
    starts_on: date
    ends_on: date | None


def _worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    found = scoped_qs(ctx, Worker).filter(pk=worker_id).first()
    if found is None:
        raise LaborRejected(["Elige un trabajador del registro."])
    return found


def _check(
    ctx: RequestContext, project: Project, data: AssignmentData, exclude: UUID | None = None
) -> Worker:
    worker = _worker(ctx, data.worker_id)
    errors: list[str] = []
    if data.ends_on is not None and data.ends_on < data.starts_on:
        errors.append("La asignación termina antes de empezar.")
    if data.starts_on < worker.hired_on:
        errors.append(f"No puede empezar antes del ingreso ({worker.hired_on:%d/%m/%Y}).")
    if worker.retired_on is not None and (data.ends_on is None or data.ends_on > worker.retired_on):
        errors.append(f"Termina después del retiro ({worker.retired_on:%d/%m/%Y}).")
    others = scoped_qs(ctx, ProjectWorker).filter(project=project, worker=worker).filter(
        Q(ends_on__isnull=True) | Q(ends_on__gte=data.starts_on)
    )
    if data.ends_on is not None:
        others = others.filter(starts_on__lte=data.ends_on)
    if exclude is not None:
        others = others.exclude(pk=exclude)
    clash = others.first()
    if clash is not None:
        until = f"{clash.ends_on:%d/%m/%Y}" if clash.ends_on else "sin fin"
        errors.append(f"Se cruza con la asignación del {clash.starts_on:%d/%m/%Y} ({until}).")
    if errors:
        raise LaborRejected(errors)
    return worker


def _outside(
    ctx: RequestContext, project: Project, row: ProjectWorker, data: AssignmentData | None
) -> date | None:
    """El primer día tareado (planillas no anuladas del proyecto) que la asignación cubría y la
    nueva (o ninguna, al borrar) ya no cubre."""
    lines = (
        scoped_qs(ctx, TimesheetLine)
        .filter(timesheet__project=project, worker_id=row.worker_id, worked_on__gte=row.starts_on)
        .exclude(timesheet__status=TimesheetStatus.REVERSED)
    )
    if row.ends_on is not None:
        lines = lines.filter(worked_on__lte=row.ends_on)
    if data is not None:
        keep = Q(worked_on__gte=data.starts_on)
        if data.ends_on is not None:
            keep &= Q(worked_on__lte=data.ends_on)
        lines = lines.exclude(keep)
    return lines.order_by("worked_on").values_list("worked_on", flat=True).first()


def _metadata(row: ProjectWorker, worker: Worker, change: str) -> dict[str, Any]:
    return {
        "project": str(row.project_id),
        "assignment": str(row.pk),
        "worker": str(worker.pk),
        "document": f"{worker.document_type} {mask_document(worker.document_number)}",
        "change": change,
        "starts_on": row.starts_on.isoformat(),
        "ends_on": row.ends_on.isoformat() if row.ends_on else "",
    }


def create_assignment(
    ctx: RequestContext, project: Project, data: AssignmentData
) -> ProjectWorker:
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        lock_project(ctx, project)
        worker = _check(ctx, project, data)
        row = scoped_qs(ctx, ProjectWorker).create(
            org_id=ctx.org_id, project=project, worker=worker, starts_on=data.starts_on,
            ends_on=data.ends_on, created_by_id=ctx.user_id, updated_by_id=ctx.user_id,
        )  # fmt: skip
        audit.record(ctx, action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
                     target=f"project_worker:{row.pk}", metadata=_metadata(row, worker, "created"))  # fmt: skip
    return row


def update_assignment(
    ctx: RequestContext, project: Project, assignment_id: UUID, data: AssignmentData
) -> ProjectWorker:
    with transaction.atomic():
        lock_project(ctx, project)
        row = locked(ctx, ProjectWorker, project, assignment_id)
        if not can_update_labor(ctx, row):
            raise PermissionDenied
        if data.worker_id != row.worker_id:
            raise LaborRejected(["El trabajador de una asignación no cambia: crea otra."])
        worker = _check(ctx, project, data, exclude=row.pk)
        day = _outside(ctx, project, row, data)
        if day is not None:
            raise LaborRejected([f"Hay tareo el {day:%d/%m/%Y} fuera de las nuevas fechas."])
        row.starts_on, row.ends_on = data.starts_on, data.ends_on
        row.updated_by_id = ctx.user_id
        row.save()
        audit.record(ctx, action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
                     target=f"project_worker:{row.pk}", metadata=_metadata(row, worker, "updated"))  # fmt: skip
    return row


def delete_assignment(ctx: RequestContext, project: Project, assignment_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        row = locked(ctx, ProjectWorker, project, assignment_id)
        if not can_delete_labor(ctx, row):
            raise PermissionDenied
        day = _outside(ctx, project, row, None)
        if day is not None:
            raise LaborRejected([f"Hay tareo el {day:%d/%m/%Y} en esta asignación."])
        worker = scoped_qs(ctx, Worker).get(pk=row.worker_id)
        metadata = _metadata(row, worker, "deleted")
        row.delete()
        audit.record(ctx, action=AuditAction.LABOR_ASSIGNMENT_CHANGED,
                     target=f"project_worker:{assignment_id}", metadata=metadata)  # fmt: skip


def get_assignment(ctx: RequestContext, project: Project, assignment_id: UUID) -> ProjectWorker:
    found = scoped_qs(ctx, ProjectWorker).filter(pk=assignment_id, project=project).first()
    if found is None:
        raise Http404
    return found
```

- [ ] **Step 4: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_assignments.py`
Expected: PASS.

- [ ] **Step 5: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): project assignments within employment and without overlaps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Planillas: crear, guardar líneas con asignación y tope, recalcular, borrar y soporte (servicios)

**Files:**
- Create: `server/suno/labor/timesheets.py`
- Modify: `server/suno/labor/tests/factories.py` (`other_project`, `indirect`, `to_item`)
- Test: `server/suno/labor/tests/test_timesheets.py`

- [ ] **Step 1: Ayudas de prueba**

Al final de `server/suno/labor/tests/factories.py` (con `from suno.costs.tests.factories import item`, `from suno.labor.timesheets import LineData` y `from suno.materials.destination import INDIRECT, DestinationData` en los imports):

```python
def other_project(lw: LaborWorld, name: str = "Obra 2") -> Project:
    """Otro proyecto de la misma organización, con Pedro asignado desde su ingreso."""
    world = lw.world
    project = Project.objects.create(org=world.org, name=name, created_by=world.admin)
    ProjectWorker.objects.create(
        org=world.org, project=project, worker=lw.pedro, starts_on=HIRED,
        created_by=world.admin, updated_by=world.admin,
    )  # fmt: skip
    return project


def to_item(lw: LaborWorld, code: str = "2.1") -> DestinationData:
    return DestinationData(str(CostScope.ITEM), (item(lw.cw.budget, code).pk,))


def line_data(
    worker: Worker, day: date, fraction: str = "1", destination: DestinationData | None = None
) -> LineData:
    return LineData(worker.pk, day, destination or INDIRECT, D(fraction))
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_timesheets.py`:

```python
"""Planillas de tareo (spec 1.2, 2.2, 2.3 y 3.2): periodo, líneas, asignación, tope diario,
resumen guardado y edición."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, Timesheet, TimesheetLine, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import timesheets
from suno.labor.common import LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    make_worker,
    other_project,
    to_item,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


class PeriodTests(unittest.TestCase):
    def test_default_period_is_last_week(self) -> None:
        self.assertEqual(
            timesheets.default_period(date(2026, 4, 15)), (date(2026, 4, 6), date(2026, 4, 12))
        )
        self.assertEqual(
            timesheets.default_period(date(2026, 4, 13)), (date(2026, 4, 6), date(2026, 4, 12))
        )


@skip_without_db
class CreateTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)

    def test_number_period_rules_and_overlaps(self) -> None:
        first = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.assertEqual((first.code, first.status), ("TA-001", "draft"))
        for start, end, message in (
            (date(2026, 3, 8), date(2026, 3, 2), "termina antes"),
            (date(2026, 3, 9), date(2026, 4, 9), "31 días"),
            (date(2026, 4, 13), date(2026, 4, 19), "futuro"),
            (date(2026, 3, 5), date(2026, 3, 11), "Se cruza con la planilla TA-001"),
        ):
            with self.subTest(start=start), self.assertRaises(LaborRejected) as rejected:
                timesheets.create_timesheet(self.ctx, self.lw.project, start, end)
            self.assertIn(message, rejected.exception.errors[0])
        # Una anulada no bloquea: para corregir se hace otra con el mismo periodo.
        Timesheet.objects.filter(pk=first.pk).delete()
        bare_timesheet(self.lw, status="reversed")
        again = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.assertEqual(again.code, "TA-002")
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.created").exists())

    def test_viewer_cannot_create(self) -> None:
        with self.assertRaises(PermissionDenied):
            timesheets.create_timesheet(self.lw.ctx(self.lw.viewer), self.lw.project, *WEEK)


@skip_without_db
class SaveLinesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)

    def save(self, lines: list[Any], days: list[date] | None = None, ctx: Any = None) -> Timesheet:
        return timesheets.save_lines(
            ctx or self.ctx, self.lw.project, self.sheet.pk, lines, days or [MON, TUE]
        )

    def test_save_writes_lines_and_the_summary(self) -> None:
        sheet = self.save([
            line_data(self.lw.pedro, MON),
            line_data(self.lw.maria, MON, "0.5", to_item(self.lw)),
            line_data(self.lw.maria, MON, "0.25", to_item(self.lw)),  # misma línea: se suma
        ])  # fmt: skip
        self.assertEqual(TimesheetLine.objects.filter(timesheet=sheet).count(), 2)
        maria = TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.maria)
        self.assertEqual((maria.fraction, maria.scope), (D("0.7500"), "item"))
        self.assertIsNone(maria.amount)  # en borrador no se congela
        self.assertEqual(sheet.total, D("235600.00"))  # 121.600 + 0,75 × 100.000 × 1,52
        self.assertEqual(sheet.days, D("1.7500"))
        self.assertEqual(sheet.line_count, 2)
        self.assertEqual(sheet.summary["fingerprint"]["total"], "235600.00")
        self.assertEqual(len(sheet.summary["groups"]), 2)
        event = AuditEvent.objects.filter(action="timesheet.updated").latest("created_at")
        self.assertEqual(event.metadata["lines"], 2)

    def test_saving_some_days_keeps_the_others(self) -> None:
        self.save([line_data(self.lw.pedro, MON), line_data(self.lw.pedro, TUE)])
        self.save([line_data(self.lw.pedro, TUE, "0.5")], days=[TUE])
        days = dict(TimesheetLine.objects.values_list("worked_on", "fraction"))
        self.assertEqual(days, {MON: D("1.0000"), TUE: D("0.5000")})
        self.save([], days=[TUE])  # vaciar un día
        self.assertEqual(TimesheetLine.objects.count(), 1)

    def test_line_rules(self) -> None:
        luis = make_worker(self.lw.world.org, "70111222", "Luis Torres")  # sin asignación
        for lines, days, message in (
            ([line_data(luis, MON)], None, "no está asignado al proyecto"),
            ([line_data(self.lw.pedro, MON, "1.6")], None, "la fracción"),
            ([line_data(self.lw.pedro, MON, "1"), line_data(self.lw.pedro, MON, "0.75")], None,
             "la misma línea suma 1,75"),
            ([line_data(self.lw.pedro, date(2026, 3, 9))], [date(2026, 3, 9)], "fuera del periodo"),
            ([line_data(self.lw.pedro, TUE)], [MON], "el día no está"),
        ):  # fmt: skip
            with self.subTest(message=message), self.assertRaises(LaborRejected) as rejected:
                self.save(lines, days)
            self.assertIn(message, "\n".join(rejected.exception.errors))

    def test_daily_cap_counts_other_projects_and_names_them(self) -> None:
        other = other_project(self.lw)
        elsewhere = bare_timesheet(self.lw, project=other)
        bare_line(elsewhere, self.lw.pedro, MON, "1")
        with self.assertRaises(LaborRejected) as rejected:
            self.save([line_data(self.lw.pedro, MON, "0.75")])
        text = rejected.exception.errors[0]
        self.assertIn("Pedro Pérez · CC ***5678 suma 1,75 jornales el 02/03/2026", text)
        self.assertIn("TA-001 (Obra 2)", text)
        self.save([line_data(self.lw.pedro, MON, "0.5")])  # 1,5 exacto pasa
        # Una planilla anulada no cuenta.
        reversed_sheet = bare_timesheet(self.lw, number=2, project=other, status="reversed",
                                        start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        bare_line(reversed_sheet, self.lw.pedro, date(2026, 3, 9), "1.5")
        self.assertEqual(
            timesheets.day_totals(self.lw.ctx(), {self.lw.pedro.pk}, {date(2026, 3, 9)}), {}
        )

    def test_editing_a_rejected_sheet_returns_it_to_draft_and_drops_the_signature(self) -> None:
        self.save([line_data(self.lw.pedro, MON)])
        timesheets.attach_signed(self.ctx, self.lw.project, self.sheet.pk, pdf_upload(), "f.pdf")
        Timesheet.objects.filter(pk=self.sheet.pk).update(
            status="rejected", decided_by=self.lw.world.supervisor, decided_at=self.sheet.created_at,
            decision_note="Falta María",
        )  # fmt: skip
        with self.assertRaises(PermissionDenied):  # un rechazado lo edita solo su autor
            self.save([line_data(self.lw.maria, MON)], ctx=self.lw.ctx(self.lw.world.supervisor))
        sheet = self.save([line_data(self.lw.pedro, MON), line_data(self.lw.maria, MON)])
        self.assertEqual(sheet.status, "draft")
        self.assertIsNone(sheet.attachment_id)
        self.assertIsNone(sheet.decided_by_id)
        event = AuditEvent.objects.filter(action="timesheet.updated").latest("created_at")
        self.assertTrue(event.metadata["resubmitted"])
        self.assertTrue(event.metadata["signed_removed"])

    def test_signature_is_not_an_edit_and_recalculate_is(self) -> None:
        self.save([line_data(self.lw.pedro, MON)])
        sheet = timesheets.attach_signed(
            self.lw.ctx(), self.lw.project, self.sheet.pk, pdf_upload(), "firmada.pdf"
        )
        self.assertEqual(sheet.updated_by, self.lw.contributor)  # subirla no es editar
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro,
                                  valid_from=MON, daily_wage=D("90000.00"))  # fmt: skip
        sheet = timesheets.recalculate(self.lw.ctx(), self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.total, D("136800.00"))
        self.assertIsNone(sheet.attachment_id)
        self.assertEqual(sheet.updated_by, self.lw.world.admin)

    def test_delete_only_open_sheets_by_their_author_or_a_reviewer(self) -> None:
        with self.assertRaises(PermissionDenied):
            timesheets.delete_timesheet(self.lw.ctx(self.lw.viewer), self.lw.project, self.sheet.pk)
        timesheets.delete_timesheet(self.ctx, self.lw.project, self.sheet.pk)
        self.assertFalse(Timesheet.objects.filter(pk=self.sheet.pk).exists())
        approved = bare_timesheet(self.lw, status="approved")
        with self.assertRaises(LaborRejected):
            timesheets.delete_timesheet(self.lw.ctx(), self.lw.project, approved.pk)
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_timesheets.py`
Expected: FAIL (`cannot import name 'timesheets' from 'suno.labor'`).

- [ ] **Step 4: Las planillas**

`server/suno/labor/timesheets.py`:

```python
"""Planillas de tareo (costos, pieza 6d, spec 1.2, 2.2, 2.3 y 3.2).

- Una planilla cubre de 1 a 31 días, nunca en el futuro; las no anuladas de un proyecto no se
  cruzan (con el proyecto bloqueado). Número `TA-001` por proyecto, al crearla.
- Las líneas se escriben por días: guardar la semana de la cuadrícula (o el día de la vista
  móvil) reemplaza las líneas de esos días (decisión 11 del plan). Una línea igual (trabajador,
  día, destino) se suma.
- Cada día de un trabajador cae en una asignación al proyecto, y nadie pasa de 1,5 jornales
  un día entre todas las planillas no anuladas de la organización (con los trabajadores
  bloqueados en orden de `pk`, ADR-0035).
- Guardar es editar: un rechazado (solo su autor) vuelve a borrador y se quita el soporte
  firmado. El resumen (`summary`) se guarda en cada edición; "Recalcular el resumen" también es
  una edición. Subir el soporte no es editar.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Project,
    ProjectWorker,
    Task,
    Timesheet,
    TimesheetLine,
    TimesheetLineItem,
    TimesheetStatus,
    Worker,
)
from suno.core.models.labor import MAX_FRACTION, MAX_PERIOD_DAYS
from suno.core.policies import can_create_labor, can_delete_labor, can_update_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.common import lock_project, locked, next_number
from suno.materials.destination import (
    DestinationData,
    check_destination,
    destination_key,
    destination_label,
    key_text,
)

from .calc import LineCost, Summary, days_text, fraction_ok, over_cap, price, summarize
from .common import (
    COUNTED_STATES,
    MAX_ERRORS,
    MAX_FILE_NAME,
    LaborRejected,
    factor_history,
    lock_workers,
    require_editable,
    stored_upload,
    wage_histories,
)

FIRST_DAY = date(2000, 1, 1)
#: 60 trabajadores × 31 días × 3 destinos son 5.580; el resto es margen.
MAX_LINES = 20_000


@dataclass(frozen=True)
class LineData:
    worker_id: UUID
    day: date
    destination: DestinationData
    fraction: Decimal


@dataclass(frozen=True)
class StoredLine:
    pk: UUID
    worker_id: UUID
    day: date
    #: `TimesheetLine.destination_key` (SHA-256 del destino).
    key: str
    scope: str
    chapter_id: UUID | None
    item_ids: tuple[UUID, ...]
    task_id: UUID | None
    task_label: str
    #: El número de esquema de la tarea ("2.1"), para volver a escribirla en la cuadrícula.
    task_outline: str
    fraction: Decimal

    @property
    def destination(self) -> DestinationData:
        return DestinationData(self.scope, self.item_ids, self.chapter_id, self.task_id)


@dataclass(frozen=True)
class Placed:
    worker_id: UUID
    day: date
    key: str
    scope: str
    chapter_id: UUID | None
    item_ids: tuple[UUID, ...]
    task_id: UUID | None
    fraction: Decimal


@dataclass(frozen=True)
class Evaluation:
    """Las líneas de una planilla con su costo de hoy (jornal y factor vigentes)."""

    lines: tuple[StoredLine, ...]
    costs: tuple[LineCost, ...]
    summary: Summary
    labels: Mapping[str, str]
    names: Mapping[UUID, str]


def hashed(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def default_period(today: date) -> tuple[date, date]:
    """La semana anterior, de lunes a domingo (spec 3.2 "Crear")."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def period_days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def timesheet_metadata(sheet: Timesheet) -> dict[str, Any]:
    return {
        "project": str(sheet.project_id),
        "timesheet": str(sheet.pk),
        "number": sheet.number,
        "start": sheet.starts_on.isoformat(),
        "end": sheet.ends_on.isoformat(),
        "status": sheet.status,
        "total": str(sheet.total),
    }


def worker_names(ctx: RequestContext, worker_ids: Iterable[UUID]) -> dict[UUID, str]:
    return {w.pk: w.label for w in scoped_qs(ctx, Worker).filter(pk__in=list(set(worker_ids)))}


def load_lines(ctx: RequestContext, sheet: Timesheet) -> list[StoredLine]:
    """Las líneas con su destino. Dos consultas."""
    items: dict[UUID, list[UUID]] = defaultdict(list)
    for line_id, item_id in (
        scoped_qs(ctx, TimesheetLineItem)
        .filter(line__timesheet=sheet)
        .values_list("line_id", "item_id")
    ):
        items[line_id].append(item_id)
    return [
        StoredLine(
            pk, worker_id, day, key, scope, chapter_id,
            tuple(sorted(items.get(pk, ()), key=str)), task_id,
            f"{outline} {name}" if task_id else "", outline or "", fraction,
        )  # fmt: skip
        for pk, worker_id, day, key, scope, chapter_id, task_id, outline, name, fraction in (
            scoped_qs(ctx, TimesheetLine)
            .filter(timesheet=sheet)
            .order_by("worked_on", "worker_id")
            .values_list(
                "pk", "worker_id", "worked_on", "destination_key", "scope", "chapter_id",
                "task_id", "task__outline_number", "task__name", "fraction",
            )  # fmt: skip
        )
    ]


def labels_for(catalog: Catalog, lines: Iterable[StoredLine]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in lines:
        if line.key not in labels:
            labels[line.key] = destination_label(
                catalog, line.scope, line.chapter_id, line.item_ids, line.task_label
            )
    return labels


def evaluate(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> Evaluation:
    """Cinco consultas: líneas, ítems, nombres, jornales y factor."""
    lines = load_lines(ctx, sheet)
    workers = {line.worker_id for line in lines}
    costs = price(
        [(line.worker_id, line.day, line.key, line.fraction) for line in lines],
        wage_histories(ctx, workers),
        factor_history(ctx),
    )
    return Evaluation(
        tuple(lines), tuple(costs), summarize(costs), labels_for(catalog, lines),
        worker_names(ctx, workers),
    )  # fmt: skip


def _store_summary(sheet: Timesheet, evaluation: Evaluation) -> None:
    summary = evaluation.summary
    sheet.summary = summary.snapshot(evaluation.labels)
    sheet.total = summary.total
    sheet.days = summary.days
    sheet.line_count = summary.lines


def _period_errors(
    ctx: RequestContext, project: Project, start: date, end: date, exclude: UUID | None = None
) -> list[str]:
    if end < start:
        return ["La planilla termina antes de empezar."]
    errors: list[str] = []
    if (end - start).days >= MAX_PERIOD_DAYS:
        errors.append(f"Una planilla cubre como mucho {MAX_PERIOD_DAYS} días.")
    if end > timezone.localdate():
        errors.append("La planilla no puede terminar en el futuro.")
    if start < FIRST_DAY:
        errors.append("La planilla no puede empezar antes de 2000.")
    others = (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, starts_on__lte=end, ends_on__gte=start)
        .exclude(status=TimesheetStatus.REVERSED)
    )
    if exclude is not None:
        others = others.exclude(pk=exclude)
    clash = others.order_by("number").first()
    if clash is not None:
        errors.append(
            f"Se cruza con la planilla {clash.code} "
            f"({clash.starts_on:%d/%m/%Y} – {clash.ends_on:%d/%m/%Y})."
        )
    return errors


def create_timesheet(ctx: RequestContext, project: Project, start: date, end: date) -> Timesheet:
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        lock_project(ctx, project)
        errors = _period_errors(ctx, project, start, end)
        if errors:
            raise LaborRejected(errors)
        sheet = Timesheet(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, Timesheet, project),
            starts_on=start,
            ends_on=end,
            status=str(TimesheetStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store_summary(sheet, Evaluation((), (), summarize([]), {}, {}))
        sheet.save()
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_CREATED,
            target=f"timesheet:{sheet.pk}",
            metadata=timesheet_metadata(sheet),
        )
    return sheet


def _coverage(
    ctx: RequestContext, project: Project, worker_ids: Iterable[UUID]
) -> dict[UUID, list[tuple[date, date | None]]]:
    found: dict[UUID, list[tuple[date, date | None]]] = defaultdict(list)
    for worker_id, start, end in (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project, worker_id__in=list(set(worker_ids)))
        .values_list("worker_id", "starts_on", "ends_on")
    ):
        found[worker_id].append((start, end))
    return found


def _covered(periods: Sequence[tuple[date, date | None]], day: date) -> bool:
    return any(start <= day and (end is None or day <= end) for start, end in periods)


def _validate(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    lines: Sequence[LineData],
    replaced: frozenset[date],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> list[Placed]:
    if len(lines) > MAX_LINES:
        raise LaborRejected([f"Una planilla lleva como mucho {MAX_LINES} líneas."])
    workers = {
        w.pk: w for w in scoped_qs(ctx, Worker).filter(pk__in={line.worker_id for line in lines})
    }
    coverage = _coverage(ctx, project, workers)
    errors: list[str] = []
    merged: dict[tuple[UUID, date, str], Placed] = {}
    for line in lines:
        worker = workers.get(line.worker_id)
        if worker is None:
            errors.append("Hay un trabajador que no está en el registro.")
            continue
        who = f"{worker.label}, {line.day:%d/%m/%Y}"
        if not sheet.starts_on <= line.day <= sheet.ends_on:
            errors.append(f"{who}: el día está fuera del periodo de la planilla.")
        elif line.day not in replaced:
            errors.append(f"{who}: el día no está en lo que se guarda.")
        elif not fraction_ok(line.fraction):
            errors.append(f"{who}: la fracción va de 0,0001 a 1,5 con cuatro decimales.")
        elif not _covered(coverage.get(worker.pk, ()), line.day):
            errors.append(f"{who}: no está asignado al proyecto ese día.")
        else:
            problems, assignment = check_destination(catalog, tasks, line.destination)
            if assignment is None or problems:
                errors.append(f"{who}: {problems[0] if problems else 'el destino no es válido.'}")
            else:
                text = key_text(destination_key(assignment.scope, assignment.chapter_id,
                                                assignment.item_ids, assignment.task_id))  # fmt: skip
                key = (worker.pk, line.day, text)
                before = merged.get(key)
                fraction = line.fraction + (before.fraction if before else 0)
                if fraction > MAX_FRACTION:
                    errors.append(f"{who}: la misma línea suma {days_text(fraction)} jornales "
                                  "(máximo 1,5).")  # fmt: skip
                else:
                    merged[key] = Placed(
                        worker.pk, line.day, hashed(text), assignment.scope,
                        assignment.chapter_id, tuple(sorted(assignment.item_ids, key=str)),
                        assignment.task_id, fraction,
                    )  # fmt: skip
        if len(errors) >= MAX_ERRORS:
            break
    if errors:
        raise LaborRejected(errors[:MAX_ERRORS])
    return list(merged.values())


def day_totals(
    ctx: RequestContext,
    worker_ids: Iterable[UUID],
    days: Iterable[date],
    *,
    skip: Timesheet | None = None,
    skip_days: Iterable[date] = (),
) -> dict[tuple[UUID, date], Decimal]:
    """Jornales por (trabajador, día) en las planillas que cuentan (no anuladas) de toda la
    organización; `skip`/`skip_days`: las líneas que se van a reemplazar."""
    rows = scoped_qs(ctx, TimesheetLine).filter(
        worker_id__in=list(set(worker_ids)),
        worked_on__in=list(set(days)),
        timesheet__status__in=COUNTED_STATES,
    )
    if skip is not None:
        rows = rows.exclude(timesheet=skip, worked_on__in=list(skip_days))
    return {
        (worker_id, day): total
        for worker_id, day, total in rows.values("worker_id", "worked_on")
        .annotate(total=Sum("fraction"))
        .values_list("worker_id", "worked_on", "total")
    }


def cap_errors(
    ctx: RequestContext, totals: Mapping[tuple[UUID, date], Decimal], current: Timesheet
) -> list[str]:
    """Quién, qué día, cuánto suma y en qué otras planillas (número y proyecto) está el resto."""
    found = over_cap(totals)
    if not found:
        return []
    names = worker_names(ctx, {worker for worker, _, _ in found})
    elsewhere: dict[tuple[UUID, date], set[str]] = defaultdict(set)
    for worker_id, day, number, project in (
        scoped_qs(ctx, TimesheetLine)
        .filter(
            worker_id__in=[w for w, _, _ in found],
            worked_on__in=[d for _, d, _ in found],
            timesheet__status__in=COUNTED_STATES,
        )
        .exclude(timesheet=current)
        .values_list("worker_id", "worked_on", "timesheet__number", "timesheet__project__name")
    ):
        elsewhere[(worker_id, day)].add(f"TA-{number:03d} ({project})")
    errors = []
    for worker_id, day, total in found[:MAX_ERRORS]:
        text = (
            f"{names.get(worker_id, '—')} suma {days_text(total)} jornales el {day:%d/%m/%Y} "
            "(máximo 1,5)"
        )
        others = sorted(elsewhere.get((worker_id, day), ()))
        errors.append(f"{text}; el resto está en {', '.join(others)}." if others else f"{text}.")
    return errors


def _mark_edited(ctx: RequestContext, sheet: Timesheet) -> dict[str, bool]:
    flags = {
        "resubmitted": sheet.status == TimesheetStatus.REJECTED,
        "signed_removed": sheet.attachment_id is not None,
    }
    sheet.status = str(TimesheetStatus.DRAFT)
    sheet.decided_by = None
    sheet.decided_at = None
    sheet.decision_note = ""
    sheet.attachment = None
    sheet.attachment_name = ""
    sheet.updated_by_id = ctx.user_id
    return flags


def _write(ctx: RequestContext, sheet: Timesheet, placed: Sequence[Placed],
           replaced: frozenset[date]) -> None:  # fmt: skip
    scoped_qs(ctx, TimesheetLine).filter(timesheet=sheet, worked_on__in=list(replaced)).delete()
    rows = [
        TimesheetLine(
            org_id=ctx.org_id, timesheet=sheet, worker_id=p.worker_id, worked_on=p.day,
            fraction=p.fraction, scope=p.scope, chapter_id=p.chapter_id, task_id=p.task_id,
            destination_key=p.key,
        )  # fmt: skip
        for p in placed
    ]
    scoped_qs(ctx, TimesheetLine).bulk_create(rows, batch_size=2000)
    scoped_qs(ctx, TimesheetLineItem).bulk_create(
        [
            TimesheetLineItem(org_id=ctx.org_id, line=row, item_id=item_id)
            for row, p in zip(rows, placed, strict=True)
            for item_id in p.item_ids
        ],
        batch_size=2000,
    )


def _defaults(
    ctx: RequestContext,
    project: Project,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
) -> tuple[Catalog, Mapping[UUID, Task]]:
    return (
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
    )


def _edit(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    build: Callable[[Timesheet], tuple[Sequence[LineData], frozenset[date]] | None],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    action: str,
) -> Timesheet:
    """Bloquea, valida, escribe las líneas y el resumen y audita. `build` devuelve las líneas y
    los días que reemplazan, o `None` para solo recalcular el resumen."""
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        require_editable(ctx, sheet, can_update_labor)
        built = build(sheet)
        placed: list[Placed] = []
        replaced: frozenset[date] = frozenset()
        if built is not None:
            lines, replaced = built
            outside = [day for day in replaced if not sheet.starts_on <= day <= sheet.ends_on]
            if outside:
                raise LaborRejected(["Hay días fuera del periodo de la planilla."])
            placed = _validate(ctx, project, sheet, lines, replaced, catalog, tasks)
            lock_workers(ctx, {p.worker_id for p in placed})
            totals = day_totals(ctx, {p.worker_id for p in placed}, {p.day for p in placed},
                                skip=sheet, skip_days=replaced)  # fmt: skip
            for p in placed:
                totals[(p.worker_id, p.day)] = totals.get((p.worker_id, p.day), 0) + p.fraction
            errors = cap_errors(ctx, totals, sheet)
            if errors:
                raise LaborRejected(errors)
        flags = _mark_edited(ctx, sheet)
        if built is not None:
            _write(ctx, sheet, placed, replaced)
        _store_summary(sheet, evaluate(ctx, sheet, catalog))
        sheet.save()
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_IMPORTED if action == "import"
            else AuditAction.TIMESHEET_UPDATED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), **flags, "change": action,
                      "days": len(replaced), "lines": len(placed)},
        )  # fmt: skip
    return sheet


def save_lines(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    lines: Sequence[LineData],
    days: Iterable[date],
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Timesheet:
    """Reemplaza las líneas de `days` por `lines` (la semana de la cuadrícula o un día)."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    replaced = frozenset(days)
    return _edit(ctx, project, timesheet_id, lambda _sheet: (lines, replaced), catalog, tasks,
                 "save")  # fmt: skip


def import_lines(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    lines: Sequence[LineData],
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Timesheet:
    """Agrega líneas a un borrador (spec 2.5): las que coinciden con una existente se suman."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)

    def build(sheet: Timesheet) -> tuple[Sequence[LineData], frozenset[date]]:
        existing = [
            LineData(line.worker_id, line.day, line.destination, line.fraction)
            for line in load_lines(ctx, sheet)
        ]
        return [*existing, *lines], frozenset(period_days(sheet.starts_on, sheet.ends_on))

    return _edit(ctx, project, timesheet_id, build, catalog, tasks, "import")


def recalculate(
    ctx: RequestContext, project: Project, timesheet_id: UUID, *, catalog: Catalog | None = None
) -> Timesheet:
    """Guarda otra vez el resumen con los jornales y el factor de hoy. Es editar."""
    catalog, tasks = _defaults(ctx, project, catalog, {})
    return _edit(ctx, project, timesheet_id, lambda _sheet: None, catalog, tasks, "recalculate")


def delete_timesheet(ctx: RequestContext, project: Project, timesheet_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        require_editable(ctx, sheet, can_delete_labor)
        metadata = timesheet_metadata(sheet)
        target = f"timesheet:{sheet.pk}"
        sheet.delete()
        audit.record(ctx, action=AuditAction.TIMESHEET_DELETED, target=target, metadata=metadata)


def _require_signable(ctx: RequestContext, sheet: Timesheet) -> None:
    if sheet.status != TimesheetStatus.DRAFT:
        raise LaborRejected(["La planilla firmada se sube a una planilla en borrador."])
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied


def attach_signed(
    ctx: RequestContext, project: Project, timesheet_id: UUID, upload: Upload, name: str
) -> Timesheet:
    """Sube la planilla firmada. No es editar: `updated_by` no cambia."""
    current = scoped_qs(ctx, Timesheet).filter(pk=timesheet_id, project=project).first()
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require_signable(ctx, sheet)
        sheet.attachment = blob
        sheet.attachment_name = name[:MAX_FILE_NAME]
        sheet.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_UPDATED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "signed": True},
        )
    return sheet
```

Notas:
- `_validate` revisa primero que el día esté en el periodo y después que esté en lo que se guarda, para que el mensaje "fuera del periodo" gane cuando las dos cosas fallan.
- `recalculate` pasa `tasks={}` porque no valida destinos (no hay `build`).
- En `_edit`, el `AuditAction` según `action`: `"import"` audita `timesheet.imported`; lo demás `timesheet.updated` con `change` (`save` o `recalculate`).

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_timesheets.py`
Expected: PASS.

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): timesheets with assignment coverage, daily cap across projects and stored summary

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Aprobar (al libro), rechazar y anular; concurrencia del tope

**Files:**
- Create: `server/suno/labor/approval.py`
- Test: `server/suno/labor/tests/test_approval.py`, `server/suno/labor/tests/test_concurrency.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_approval.py`:

```python
"""Aprobación, rechazo y anulación (spec 2.3 y 2.4): lo aprobado es lo firmado, sin
autoaprobación, valores congelados y costos por destino en el libro."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    AuditEvent,
    BenefitFactor,
    CostEntry,
    Timesheet,
    TimesheetLine,
    WorkerWage,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import SELF_DECISION, LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    line_data,
    other_project,
    to_item,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]


@skip_without_db
class ApprovalTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.author = self.lw.ctx(self.lw.contributor)
        self.approver = self.lw.ctx(self.lw.world.supervisor)
        self.sheet = timesheets.create_timesheet(self.author, self.lw.project, *WEEK)
        timesheets.save_lines(self.author, self.lw.project, self.sheet.pk, [
            line_data(self.lw.pedro, MON),
            line_data(self.lw.maria, MON, "0.5", to_item(self.lw)),
        ], [MON])  # fmt: skip

    def sign(self) -> None:
        timesheets.attach_signed(self.author, self.lw.project, self.sheet.pk, pdf_upload(), "f.pdf")

    def test_approval_freezes_lines_and_posts_one_cost_per_destination(self) -> None:
        self.sign()
        sheet = approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.status, "approved")
        pedro = TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.pedro)
        self.assertEqual(
            (pedro.daily_wage, pedro.factor, pedro.amount),
            (D("80000.00"), D("1.5200"), D("121600.00")),
        )
        entries = CostEntry.objects.filter(timesheet=sheet).order_by("amount")
        self.assertEqual([e.amount for e in entries], [D("76000.00"), D("121600.00")])
        indirect = entries[1]
        self.assertEqual(indirect.description, "Tareo TA-001 · Indirecto (proyecto)")
        self.assertEqual(
            (indirect.kind, indirect.document_type, indirect.document_number),
            ("labor", "timesheet", "TA-001"),
        )
        self.assertEqual(indirect.incurred_on, WEEK[1])
        self.assertEqual(indirect.third_party, "")  # sin nombres en el libro
        self.assertEqual(indirect.created_by, self.lw.contributor)
        self.assertEqual(indirect.decided_by, self.lw.world.supervisor)
        self.assertEqual(indirect.attachment_id, sheet.attachment_id)
        self.assertEqual(entries[0].scope, "item")
        self.assertEqual(sum(e.amount for e in entries), sheet.total)  # cuadra al peso
        event = AuditEvent.objects.get(action="timesheet.approved")
        self.assertEqual(event.metadata["entries"], 2)

    def test_no_self_approval_and_signature_required(self) -> None:
        with self.assertRaises(LaborRejected) as unsigned:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("firmada", unsigned.exception.errors[0])
        self.sign()
        with self.assertRaises(PermissionDenied):  # el Colaborador no aprueba
            approval.approve_timesheet(self.author, self.lw.project, self.sheet.pk)
        timesheets.recalculate(self.approver, self.lw.project, self.sheet.pk)  # el revisor edita
        timesheets.attach_signed(self.approver, self.lw.project, self.sheet.pk, pdf_upload(), "f")
        with self.assertRaises(LaborRejected) as own:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(own.exception.errors, [SELF_DECISION])
        approval.approve_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk)

    def test_a_wage_change_after_printing_blocks_approval(self) -> None:
        self.sign()
        WorkerWage.objects.create(org=self.lw.world.org, worker=self.lw.pedro, valid_from=MON,
                                  daily_wage=D("90000.00"))  # fmt: skip
        with self.assertRaises(LaborRejected) as changed:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(
            changed.exception.errors,
            ["La planilla cambió desde que se imprimió; revísala y vuelve a cargar el soporte "
             "firmado."],
        )  # fmt: skip
        timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.sign()
        sheet = approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.total, D("212800.00"))  # 136.800 + 76.000

    def test_missing_factor_blocks_approval(self) -> None:
        BenefitFactor.objects.filter(org=self.lw.world.org).delete()
        timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.sign()
        with self.assertRaises(LaborRejected) as missing:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("No hay factor prestacional vigente el 02/03/2026.", missing.exception.errors)

    def test_cap_is_checked_again_at_approval(self) -> None:
        """Un borrador escrito por fuera del servicio (como si llegara de otra vía) que rompe el
        tope impide aprobar."""
        elsewhere = bare_timesheet(self.lw, project=other_project(self.lw))
        bare_line(elsewhere, self.lw.pedro, MON, "1")
        self.sign()
        with self.assertRaises(LaborRejected) as over:
            approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        self.assertIn("suma 2 jornales el 02/03/2026", over.exception.errors[0])

    def test_reject_needs_a_note_and_editing_returns_to_draft(self) -> None:
        with self.assertRaises(LaborRejected):
            approval.reject_timesheet(self.approver, self.lw.project, self.sheet.pk, "  ")
        sheet = approval.reject_timesheet(self.approver, self.lw.project, self.sheet.pk, "Falta X")
        self.assertEqual((sheet.status, sheet.decision_note), ("rejected", "Falta X"))
        sheet = timesheets.recalculate(self.author, self.lw.project, self.sheet.pk)
        self.assertEqual(sheet.status, "draft")

    def test_reverse_writes_todays_reversals_and_keeps_frozen_values(self) -> None:
        self.sign()
        approval.approve_timesheet(self.approver, self.lw.project, self.sheet.pk)
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            approval.reverse_timesheet(self.approver, self.lw.project, self.sheet.pk, "Error")
        sheet = approval.reverse_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk, "Error")
        self.assertEqual(sheet.status, "reversed")
        reversals = CostEntry.objects.filter(timesheet=sheet, reverses__isnull=False)
        self.assertEqual(reversals.count(), 2)
        self.assertTrue(all(r.incurred_on == TODAY and r.amount < 0 for r in reversals))
        self.assertEqual(
            TimesheetLine.objects.get(timesheet=sheet, worker=self.lw.pedro).amount,
            D("121600.00"),
        )
        with self.assertRaises(LaborRejected):
            approval.reverse_timesheet(self.lw.ctx(), self.lw.project, self.sheet.pk, "Otra vez")
        # Para corregir: otra planilla con el mismo periodo.
        again = timesheets.create_timesheet(self.author, self.lw.project, *WEEK)
        self.assertEqual(again.code, "TA-002")
        self.assertEqual(Timesheet.objects.filter(project=self.lw.project).count(), 2)
```

`server/suno/labor/tests/test_concurrency.py`:

```python
"""El tope diario con dos escrituras a la vez en obras distintas (spec 2.2 y 4 "Tope diario").
Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase` los hilos no verían
los datos ni habría concurrencia real (como `materials/tests/test_concurrency.py`).

Como los borradores cuentan para el tope, dos aprobaciones no pueden sumar por encima entre sí
(lo que aprueban ya contaba al guardarse). Las carreras que importan son dos guardados a la vez
y un guardado contra una aprobación: las dos se serializan por las filas de los trabajadores."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from unittest import mock, skipUnless

from django.db import connections
from django.db.models import Sum
from django.test import TransactionTestCase

from suno.core.models import TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import approval, timesheets
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, labor_world, line_data, other_project

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON = WEEK[0]


@skip_without_db
class DailyCapRaceTests(TransactionTestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.other = other_project(self.lw)
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.first = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        self.second = timesheets.create_timesheet(self.ctx, self.other, *WEEK)

    def race(self, *actions: Callable[[], object]) -> list[str]:
        barrier = threading.Barrier(len(actions))

        def run(action: Callable[[], object]) -> str:
            try:
                barrier.wait(timeout=10)
                action()
                return "ok"
            except LaborRejected as exc:
                return exc.errors[0]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return sorted(f.result() for f in [pool.submit(run, a) for a in actions])

    def total(self) -> Decimal:
        found = TimesheetLine.objects.filter(worker=self.lw.pedro, worked_on=MON).aggregate(
            total=Sum("fraction")
        )["total"]
        return found or D("0")

    def save(self, project: Any, sheet: Any) -> Callable[[], object]:
        return lambda: timesheets.save_lines(
            self.ctx, project, sheet.pk, [line_data(self.lw.pedro, MON)], [MON]
        )

    def test_two_saves_in_two_projects_end_with_one_refused(self) -> None:
        results = self.race(self.save(self.lw.project, self.first),
                            self.save(self.other, self.second))  # fmt: skip
        self.assertEqual(results[1], "ok")
        self.assertIn("suma 2 jornales el 02/03/2026", results[0])
        self.assertEqual(self.total(), D("1"))

    def test_a_save_against_an_approval_ends_with_one_refused(self) -> None:
        self.save(self.lw.project, self.first)()
        timesheets.attach_signed(self.ctx, self.lw.project, self.first.pk, pdf_upload(), "f.pdf")
        approve = lambda: approval.approve_timesheet(  # noqa: E731
            self.lw.ctx(self.lw.world.supervisor), self.lw.project, self.first.pk
        )
        results = self.race(approve, self.save(self.other, self.second))
        # La aprobación ya contaba el borrador: el otro guardado es el que se rechaza.
        self.assertEqual(results[1], "ok")
        self.assertIn("suma 2 jornales", results[0])
        self.assertEqual(self.total(), D("1"))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_approval.py suno/labor/tests/test_concurrency.py`
Expected: FAIL (`cannot import name 'approval' from 'suno.labor'`).

- [ ] **Step 3: La aprobación**

`server/suno/labor/approval.py`:

```python
"""Aprobar, rechazar y anular planillas (costos, pieza 6d, spec 2.3 y 2.4).

- Aprobar pide `approve` de Mano de obra, la planilla firmada y que no la apruebe quien la creó
  ni quien la editó por última vez (también la base). Se recalcula con los jornales y el factor
  de hoy: si la huella cambió, se rechaza ("lo aprobado es lo firmado", ADR-0032); falta de
  jornal o factor, o una línea en $0, también. El tope diario se comprueba otra vez.
- Al aprobar se congelan jornal, factor y valor de cada línea y se escribe un costo aprobado de
  Mano de obra por destino (decisión 17 del plan).
- Anular pide `reverse`: reversos de hoy con el motivo (`materials.common.reverse_entries`).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    Project,
    Timesheet,
    TimesheetLine,
    TimesheetStatus,
)
from suno.core.policies import can_approve_labor, can_reject_labor, can_reverse_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, load_catalog
from suno.materials.common import lock_project, locked, reverse_entries

from .calc import problems
from .common import (
    MAX_DESCRIPTION,
    MAX_ERRORS,
    LaborRejected,
    clean_note,
    lock_factors,
    lock_workers,
    require_decidable,
)
from .timesheets import (
    Evaluation,
    StoredLine,
    cap_errors,
    day_totals,
    evaluate,
    timesheet_metadata,
)

CHANGED = (
    "La planilla cambió desde que se imprimió; revísala y vuelve a cargar el soporte firmado."
)


def _post(
    ctx: RequestContext, project: Project, sheet: Timesheet, evaluation: Evaluation
) -> int:
    """Un costo aprobado por destino: la suma de sus líneas ya redondeadas."""
    first: dict[str, StoredLine] = {}
    amounts: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        first.setdefault(line.key, line)
        amounts[line.key] += cost.amount or ZERO
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for key in sorted(first, key=lambda k: evaluation.labels.get(k, "")):
        line = first[key]
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=sheet.ends_on,
            kind=CostKind.LABOR,
            description=f"Tareo {sheet.code} · {evaluation.labels.get(key, '—')}"[
                :MAX_DESCRIPTION
            ],
            amount=amounts[key],
            third_party="",
            document_type=CostDocumentType.TIMESHEET,
            document_number=sheet.code,
            scope=line.scope,
            chapter_id=line.chapter_id,
            task_id=line.task_id,
            status=CostStatus.APPROVED,
            created_by_id=sheet.created_by_id,
            updated_by_id=sheet.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=sheet.attachment_id,
            attachment_name=sheet.attachment_name,
            timesheet=sheet,
        )
        entries.append(entry)
        links += [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=i) for i in line.item_ids]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def _freeze(ctx: RequestContext, evaluation: Evaluation) -> None:
    rows = []
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        rows.append(
            TimesheetLine(pk=line.pk, daily_wage=cost.wage, factor=cost.factor, amount=cost.amount)
        )
    scoped_qs(ctx, TimesheetLine).bulk_update(
        rows, ["daily_wage", "factor", "amount"], batch_size=2000
    )


def approve_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, *, catalog: Catalog | None = None
) -> Timesheet:
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        if not can_approve_labor(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, sheet)
        if sheet.attachment_id is None:
            raise LaborRejected(["Sube la planilla firmada antes de aprobar."])
        worker_ids = set(
            scoped_qs(ctx, TimesheetLine).filter(timesheet=sheet).values_list("worker_id", flat=True)
        )
        if not worker_ids:
            raise LaborRejected(["La planilla no tiene líneas."])
        lock_workers(ctx, worker_ids)
        lock_factors(ctx)
        evaluation = evaluate(ctx, sheet, catalog)
        found = problems(evaluation.costs, evaluation.names)
        if found:
            raise LaborRejected(found[:MAX_ERRORS])
        if evaluation.summary.fingerprint() != (sheet.summary or {}).get("fingerprint"):
            raise LaborRejected([CHANGED])
        totals = day_totals(ctx, worker_ids, {line.day for line in evaluation.lines})
        errors = cap_errors(ctx, totals, sheet)
        if errors:
            raise LaborRejected(errors)
        _freeze(ctx, evaluation)
        entries = _post(ctx, project, sheet, evaluation)
        # El resumen guardado es el que imprimió el PDF firmado: no se reescribe.
        sheet.status = str(TimesheetStatus.APPROVED)
        sheet.decided_by_id = ctx.user_id
        sheet.decided_at = timezone.now()
        sheet.save(update_fields=["status", "decided_by", "decided_at"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_APPROVED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "entries": entries},
        )
    return sheet


def reject_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, note: str
) -> Timesheet:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        if not can_reject_labor(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, sheet)
        sheet.status = str(TimesheetStatus.REJECTED)
        sheet.decided_by_id = ctx.user_id
        sheet.decided_at = timezone.now()
        sheet.decision_note = reason
        sheet.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_REJECTED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "note": reason},
        )
    return sheet


def reverse_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, note: str
) -> Timesheet:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        if not can_reverse_labor(ctx, project):
            raise PermissionDenied
        if sheet.status != TimesheetStatus.APPROVED:
            raise LaborRejected(["Solo se anula una planilla aprobada."])
        reversals = reverse_entries(ctx, project, "timesheet", sheet, reason)
        sheet.status = str(TimesheetStatus.REVERSED)
        sheet.reversed_by_id = ctx.user_id
        sheet.reversed_at = timezone.now()
        sheet.reverse_note = reason
        sheet.save(update_fields=["status", "reversed_by", "reversed_at", "reverse_note"])
        audit.record(
            ctx,
            action=AuditAction.TIMESHEET_REVERSED,
            target=f"timesheet:{sheet.pk}",
            metadata={**timesheet_metadata(sheet), "note": reason, "reversals": reversals},
        )
    return sheet
```

`reverse_entries` copia `kind`, documento, destino e ítems de cada movimiento y les pone `timesheet=sheet` (el mismo origen): `cost_entry_single_origin` se cumple.

- [ ] **Step 4: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_approval.py suno/labor/tests/test_concurrency.py suno/labor/tests/test_timesheets.py`
Expected: PASS. Si la prueba de carrera tarda (los hilos esperan el bloqueo), es lo esperado: dura menos de 2 s.

- [ ] **Step 5: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): approve timesheets into the cost ledger, reject and reverse them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Importar tareo a un borrador (servicio)

**Files:**
- Create: `server/suno/labor/timesheet_import.py`
- Modify: `server/suno/labor/timesheets.py` (`outline_index`, `read_destination`)
- Test: `server/suno/labor/tests/test_timesheet_import.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_timesheet_import.py`:

```python
"""Importar tareo desde Excel a una planilla en borrador (spec 2.5): dos pasos con nonce,
errores por fila, nada se aplica si hay errores y una línea igual se suma."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, LaborImportToken, TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import timesheet_import, timesheets
from suno.labor.common import LaborRejected

from .factories import TODAY, WEEK, labor_world, line_data, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Fecha", "Destino", "Tarea", "Fracción"]


@skip_without_db
class TimesheetImportTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, *WEEK)
        timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, WEEK[0], "0.5")], [WEEK[0]])  # fmt: skip

    def preview(self, rows: list[list[object]]) -> timesheet_import.TareoPreview:
        self.sheet.refresh_from_db()
        return timesheet_import.preview(self.ctx, self.lw.project, self.sheet, xlsx("Tareo", HEADER, rows))

    def test_rows_are_added_and_equal_lines_are_summed(self) -> None:
        preview = self.preview([
            ["CC", "1012345678", "2026-03-02", "Indirecto", "", "0,5"],
            ["CC", "52123456", "03/03/2026", "2.1", "2.1", 1],
            ["cc", 52123456, "2026-03-04", "Capítulo 1", "", "0.25"],
        ])  # fmt: skip
        self.assertEqual(preview.errors, ())
        self.assertEqual(len(preview.rows), 3)
        added = timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)
        self.assertEqual(added, 3)
        pedro = TimesheetLine.objects.get(timesheet=self.sheet, worker=self.lw.pedro)
        self.assertEqual(pedro.fraction, D("1.0000"))  # 0,5 de antes + 0,5 importado
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 3)
        self.assertEqual(LaborImportToken.objects.get().kind, "timesheet")
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.imported").exists())
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)

    def test_adversarial_rows_apply_nothing(self) -> None:
        preview = self.preview([
            ["CC", "999", "2026-03-02", "Indirecto", "", "1"],        # no está en el registro
            ["CC", "1012345678", "2026-03-09", "Indirecto", "", "1"],  # fuera del periodo
            ["CC", "1012345678", "30/02/2026", "Indirecto", "", "1"],  # fecha imposible
            ["CC", "1012345678", "2026-03-03", "9.9", "", "1"],        # destino que no existe
            ["CC", "1012345678", "2026-03-03", "2.1", "7.7", "1"],     # tarea que no existe
            ["CC", "1012345678", "2026-03-03", "Indirecto", "", "=A1"],  # fórmula como texto
            ["CC", "1012345678", "2026-03-03", "Indirecto", "", "0.500"],  # 500: pasa del tope
            ["", "", "", "", "", ""],
        ])  # fmt: skip
        self.assertEqual(preview.token, "")
        text = "\n".join(preview.errors)
        for line in range(2, 9):
            with self.subTest(line=line):
                self.assertIn(f"Fila {line}:", text)
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 1)

    def test_cap_is_validated_when_applying(self) -> None:
        preview = self.preview([["CC", "1012345678", "2026-03-02", "2.1", "", "1.25"]])
        with self.assertRaises(LaborRejected) as over:
            timesheet_import.apply(self.ctx, self.lw.project, self.sheet.pk, preview.token)
        self.assertIn("suma 1,75 jornales", over.exception.errors[0])
        self.assertFalse(LaborImportToken.objects.exists())  # todo o nada, también el nonce

    def test_token_is_for_this_sheet_and_viewer_cannot_import(self) -> None:
        preview = self.preview([["CC", "1012345678", "2026-03-03", "Indirecto", "", "1"]])
        other = timesheets.create_timesheet(self.ctx, self.lw.project, date(2026, 3, 9),
                                            date(2026, 3, 15))  # fmt: skip
        with self.assertRaises(LaborRejected):
            timesheet_import.apply(self.ctx, self.lw.project, other.pk, preview.token)
        with self.assertRaises(PermissionDenied):
            timesheet_import.preview(self.lw.ctx(self.lw.viewer), self.lw.project, self.sheet,
                                     xlsx("Tareo", HEADER, []))  # fmt: skip
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_timesheet_import.py`
Expected: FAIL (`cannot import name 'timesheet_import' from 'suno.labor'`).

- [ ] **Step 3: El destino escrito con su tarea**

En `server/suno/labor/timesheets.py`, después de `labels_for` (con `parse_destination` en el import de `suno.materials.destination`):

```python
def outline_index(tasks: Mapping[UUID, Task]) -> dict[str, UUID]:
    return {task.outline_number: task.pk for task in tasks.values()}


def read_destination(
    text: str, task_text: str, catalog: Catalog, outlines: Mapping[str, UUID]
) -> tuple[DestinationData | None, str]:
    """El destino escrito de una fila (cuadrícula o Excel) y la tarea por su número de esquema
    (vacío: sin tarea); como `materials.forms.read_destination`."""
    task_id = None
    task = task_text.strip()
    if task:
        task_id = outlines.get(task)
        if task_id is None:
            return None, f"No hay una tarea vigente con número {task}."
    return parse_destination(text, catalog, task_id)
```

- [ ] **Step 4: La importación**

`server/suno/labor/timesheet_import.py`:

```python
"""Importar tareo a una planilla en borrador (costos, pieza 6d, spec 2.5).

Hoja "Tareo" con el encabezado en la fila 1: Tipo de documento, Número de documento, Fecha,
Destino ("Indirecto", "2.1", "Capítulo 2" o "2.1, 2.2"), Tarea (número de esquema, opcional) y
Fracción. La vista previa valida cada fila (trabajador del registro, fecha del periodo,
destino, tarea y fracción) sin guardar; al aplicar se valida todo otra vez, con las
asignaciones y el tope (`timesheets.import_lines`), y una línea igual a una existente se suma.
Todo o nada, incluido el nonce.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.core.context import RequestContext
from suno.core.models import LaborImportKind, Project, Task, Timesheet, Worker
from suno.core.policies import can_update_labor
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.common import lock_project, locked

from . import imports, timesheets
from .calc import fraction_ok
from .common import MAX_ERRORS, OPEN_STATES, LaborRejected
from .registry import clean_document

SHEET = "Tareo"
H_TYPE, H_NUMBER, H_DAY = "Tipo de documento", "Número de documento", "Fecha"
H_DESTINATION, H_TASK, H_FRACTION = "Destino", "Tarea", "Fracción"
HEADER = [H_TYPE, H_NUMBER, H_DAY, H_DESTINATION, H_TASK, H_FRACTION]
REQUIRED = (H_TYPE, H_NUMBER, H_DAY, H_FRACTION)
MAX_ROWS = 5000
SALT = "suno.labor.timesheet"


@dataclass(frozen=True)
class TareoRow:
    line: int
    worker_id: UUID
    worker: str
    day: date
    destination: str
    task: str
    fraction: Decimal


@dataclass(frozen=True)
class TareoPreview:
    rows: tuple[TareoRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_editable(ctx: RequestContext, sheet: Timesheet) -> None:
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    if sheet.status not in OPEN_STATES:
        raise LaborRejected(["Solo se importa a una planilla en borrador o rechazada."])


def _parse(
    raw: list[Any],
    sheet: Timesheet,
    workers: Mapping[tuple[str, str], Worker],
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    tasks: Mapping[UUID, Task],
) -> tuple[tuple[Worker, date, str, str, Decimal] | None, list[str]]:
    kind, number, day_value, destination, task, fraction_value = raw
    problems: list[str] = []
    worker = workers.get(
        (imports.cell_text(kind).upper(), clean_document(imports.cell_text(number)))
    )
    if worker is None:
        problems.append("El documento no está en el registro de trabajadores.")
    day = imports.cell_day(day_value)
    if day is None:
        problems.append("La fecha no es una fecha (AAAA-MM-DD o DD/MM/AAAA).")
    elif not sheet.starts_on <= day <= sheet.ends_on:
        problems.append(
            f"La fecha está fuera de la planilla ({sheet.starts_on:%d/%m/%Y} – "
            f"{sheet.ends_on:%d/%m/%Y})."
        )
    try:
        fraction = imports.cell_decimal(fraction_value)
    except ValueError:
        fraction = None
    if not fraction_ok(fraction):
        problems.append("La fracción va de 0,0001 a 1,5 con cuatro decimales.")
    text, task_text = imports.cell_text(destination), imports.cell_text(task)
    data, error = timesheets.read_destination(text, task_text, catalog, outlines)
    if data is None:
        problems.append(error)
    if problems or worker is None or day is None or fraction is None:
        return None, problems
    return (worker, day, text, task_text, fraction), []


def preview(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    data: bytes,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> TareoPreview:
    _require_editable(ctx, sheet)
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    try:
        name = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, name, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return TareoPreview(errors=(str(exc),))
    if not table:
        return TareoPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return TareoPreview(errors=tuple(missing))
    workers = {(w.document_type, w.document_number): w for w in scoped_qs(ctx, Worker)}
    outlines = timesheets.outline_index(tasks)
    rows: list[TareoRow] = []
    errors: list[str] = []
    for line, values in enumerate(table[1:], start=2):
        raw = imports.row_texts(values, found, HEADER)
        if not any(imports.cell_text(value) for value in raw):
            continue
        parsed, problems = _parse(raw, sheet, workers, catalog, outlines, tasks)
        if parsed is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            worker, day, text, task_text, fraction = parsed
            rows.append(TareoRow(line, worker.pk, worker.label, day, text, task_text, fraction))
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de tareo.")
    if errors:
        return TareoPreview(rows=tuple(rows), errors=tuple(errors))
    body = {
        "timesheet": str(sheet.pk),
        "rows": [
            [r.line, str(r.worker_id), r.day.isoformat(), r.destination, r.task, str(r.fraction)]
            for r in rows
        ],
    }
    return TareoPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body))


def apply(
    ctx: RequestContext,
    project: Project,
    timesheet_id: UUID,
    token: str,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> int:
    payload = imports.load(ctx, SALT, token)
    if payload.get("timesheet") != str(timesheet_id):
        raise LaborRejected([imports.INVALID])
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    outlines = timesheets.outline_index(tasks)
    lines: list[timesheets.LineData] = []
    errors: list[str] = []
    for line, worker_id, day, text, task_text, fraction in payload.get("rows", []):
        data, error = timesheets.read_destination(text, task_text, catalog, outlines)
        if data is None:
            errors.append(f"Fila {line}: {error}")
            continue
        lines.append(timesheets.LineData(UUID(worker_id), date.fromisoformat(day), data,
                                         Decimal(fraction)))  # fmt: skip
    if errors or not lines:
        raise LaborRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    # Orden de bloqueo proyecto → planilla (ADR-0035); `import_lines` vuelve a pedir los mismos
    # bloqueos dentro de la misma transacción, lo que en PostgreSQL no espera.
    with transaction.atomic():
        lock_project(ctx, project)
        sheet = locked(ctx, Timesheet, project, timesheet_id)
        _require_editable(ctx, sheet)
        imports.consume(ctx, str(LaborImportKind.TIMESHEET), payload["nonce"])
        timesheets.import_lines(ctx, project, timesheet_id, lines, catalog=catalog, tasks=tasks)
    return len(lines)
```

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_timesheet_import.py`
Expected: PASS. ("0.500" se lee como 500 por la regla de miles de `parse_number`: es una fracción inválida, que es lo que se prueba.)

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): two-step timesheet import into a draft, summing equal lines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Nómina importada y conciliación (servicios)

**Files:**
- Create: `server/suno/labor/payroll.py`
- Test: `server/suno/labor/tests/test_payroll.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_payroll.py`:

```python
"""Nómina importada y conciliación (spec 1.3 y 2.6; decisiones 21 y 22 del plan)."""

from __future__ import annotations

import hashlib
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent, CostEntry, PayrollImport, PayrollImportLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import payroll
from suno.labor.common import LaborRejected

from .factories import (
    TODAY,
    WEEK,
    bare_line,
    bare_timesheet,
    labor_world,
    make_worker,
    other_project,
    xlsx,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Tipo de documento", "Número de documento", "Nombre", "Total pagado"]
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@skip_without_db
class PayrollTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.luis = make_worker(self.lw.world.org, "70111222", "Luis Torres")  # pagado sin tareo
        # Tareo aprobado: Pedro 1 + 1 en el proyecto y 2 en la Obra 2; María 0,5.
        here = bare_timesheet(self.lw, status="approved")
        bare_line(here, self.lw.pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))
        bare_line(here, self.lw.pedro, date(2026, 3, 3), frozen=("80000.00", "1.5200", "121600.00"))
        bare_line(here, self.lw.maria, WEEK[0], "0.5", frozen=("100000.00", "1.5200", "76000.00"))
        self.other = other_project(self.lw)
        there = bare_timesheet(self.lw, project=self.other, status="approved")
        for day in (date(2026, 3, 4), date(2026, 3, 5)):
            bare_line(there, self.lw.pedro, day, frozen=("80000.00", "1.5200", "121600.00"))
        # Una anulada y un borrador no cuentan.
        gone = bare_timesheet(self.lw, number=2, status="reversed",
                              start=date(2026, 3, 9), end=date(2026, 3, 15))  # fmt: skip
        bare_line(gone, self.lw.maria, date(2026, 3, 9), frozen=("100000.00", "1.5200", "152000.00"))
        draft = bare_timesheet(self.lw, number=3, start=date(2026, 3, 16), end=date(2026, 3, 22))
        bare_line(draft, self.lw.maria, date(2026, 3, 16))

    def imported(self) -> PayrollImport:
        book = xlsx("Nómina", HEADER, [
            ["CC", "1.012.345.678", "Pedro", "500.000"],
            ["CC", "70111222", "Luis", 300000],
            ["CE", "123456", "Desconocido", "50000"],
        ])  # fmt: skip
        preview = payroll.preview(self.ctx, book, *MARCH, "nomina-marzo.xlsx")
        self.assertEqual(preview.errors, ())
        return payroll.apply(self.ctx, preview.token)

    def test_import_keeps_lines_and_the_file_fingerprint_not_the_file(self) -> None:
        book = xlsx("Nómina", HEADER, [["CC", "1012345678", "Pedro", "500000"]])
        preview = payroll.preview(self.ctx, book, *MARCH, "nomina.xlsx")
        imported = payroll.apply(self.ctx, preview.token)
        self.assertEqual(imported.file_sha256, hashlib.sha256(book).hexdigest())
        self.assertEqual((imported.file_size, imported.line_count), (len(book), 1))
        self.assertEqual(imported.total, D("500000.00"))
        line = PayrollImportLine.objects.get(payroll=imported)
        self.assertEqual(line.worker, self.lw.pedro)
        self.assertEqual(CostEntry.objects.count(), 0)  # no toca el libro
        event = AuditEvent.objects.get(action="payroll.imported")
        self.assertNotIn("Pedro", str(event.metadata))

    def test_reconciliation_of_the_organization(self) -> None:
        result = payroll.reconcile(self.ctx, self.imported())
        rows = {row.label.split(" · ")[0]: row for row in result.rows}
        pedro = rows["Pedro Pérez"]
        self.assertEqual((pedro.days, pedro.estimated, pedro.paid), (D("4"), D("486400.00"),
                                                                     D("500000.00")))  # fmt: skip
        self.assertEqual(pedro.difference, D("13600.00"))
        self.assertEqual(pedro.pct, D("2.8"))
        self.assertEqual(rows["Luis Torres"].flag, "Pagado sin tareo")
        self.assertEqual(rows["María Gómez"].flag, "Tareado sin pago")
        self.assertEqual(rows["María Gómez"].estimated, D("76000.00"))  # sin la anulada ni el borrador
        self.assertEqual([u.document for u in result.unknown], ["CE ***3456"])
        self.assertEqual(result.paid, D("800000.00"))  # sin la línea desconocida

    def test_reconciliation_of_a_project_prorates_what_was_paid(self) -> None:
        result = payroll.reconcile(self.ctx, self.imported(), project=self.lw.project)
        rows = {row.label.split(" · ")[0]: row for row in result.rows}
        # Pedro: 2 de sus 4 jornales en este proyecto → la mitad de 500.000.
        self.assertEqual(rows["Pedro Pérez"].paid, D("250000.00"))
        self.assertEqual(rows["Pedro Pérez"].estimated, D("243200.00"))
        self.assertNotIn("Luis Torres", rows)  # pagado sin tareo: no se prorratea
        self.assertEqual(result.excluded, 1)

    def test_bad_files_and_permissions(self) -> None:
        book = xlsx("Nómina", HEADER, [
            ["CC", "1012345678", "Pedro", "0"],
            ["CC", "1012345678", "Pedro", "10"],
            ["XX", "1", "Nadie", "10"],
        ])  # fmt: skip
        preview = payroll.preview(self.ctx, book, *MARCH, "mala.xlsx")
        self.assertEqual(preview.token, "")
        self.assertEqual(len(preview.errors), 3)
        with self.assertRaises(LaborRejected):
            payroll.preview(self.ctx, book, date(2026, 3, 31), date(2026, 3, 1), "x.xlsx")
        with self.assertRaises(PermissionDenied):
            payroll.preview(self.lw.ctx(self.lw.viewer), book, *MARCH, "x.xlsx")
        with self.assertRaises(PermissionDenied):
            payroll.reconcile(self.lw.ctx(self.lw.accountant), self.imported())
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_payroll.py`
Expected: FAIL (`cannot import name 'payroll' from 'suno.labor'`).

- [ ] **Step 3: La nómina y la conciliación**

`server/suno/labor/payroll.py`:

```python
"""Nómina importada y conciliación (costos, pieza 6d, spec 1.3 y 2.6).

- La nómina del periodo llega en Excel (hoja "Nómina": Tipo de documento, Número de documento,
  Total pagado; "Nombre" se ignora) en dos pasos con nonce. Del archivo se guardan nombre,
  tamaño y SHA-256 (decisión 21 del plan). No toca el libro.
- Conciliación, por trabajador: jornales y costo estimado en las planillas **aprobadas** del
  periodo (no las anuladas ni los borradores), lo pagado, la diferencia y el porcentaje.
  Marca a los pagados sin tareo y a los tareados sin pago; los documentos que no están en el
  registro van aparte. Por proyecto, lo pagado se prorratea por los jornales tareados en el
  proyecto sobre los de toda la organización (decisión 22).
- Importar pide `can_manage_labor_registry`; ver, `can_read_labor_registry`. Cuatro consultas.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.totals import WIDE_PRECISION, ZERO, money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    DocumentType,
    LaborImportKind,
    PayrollImport,
    PayrollImportLine,
    Project,
    TimesheetLine,
    TimesheetStatus,
    Worker,
)
from suno.core.models.labor import mask_document
from suno.core.policies import can_manage_labor_registry, can_read_labor_registry
from suno.core.scoping import scoped_qs

from . import imports
from .calc import NO_DAYS
from .common import MAX_ERRORS, MAX_FILE_NAME, LaborRejected
from .registry import clean_document, wage_ok

SHEET = "Nómina"
H_TYPE, H_NUMBER, H_NAME, H_TOTAL = (
    "Tipo de documento", "Número de documento", "Nombre", "Total pagado",
)  # fmt: skip
HEADER = [H_TYPE, H_NUMBER, H_NAME, H_TOTAL]
REQUIRED = (H_TYPE, H_NUMBER, H_TOTAL)
MAX_ROWS = 5000
SALT = "suno.labor.payroll"
PAID_WITHOUT_TAREO = "Pagado sin tareo"
TAREO_WITHOUT_PAY = "Tareado sin pago"


@dataclass(frozen=True)
class PayrollRow:
    line: int
    document_type: str
    document_number: str
    total: Decimal


@dataclass(frozen=True)
class PayrollPreview:
    rows: tuple[PayrollRow, ...] = ()
    errors: tuple[str, ...] = ()
    token: str = ""
    #: Cuántas filas tienen un documento del registro.
    known: int = 0

    @property
    def total(self) -> Decimal:
        return sum((row.total for row in self.rows), ZERO)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied


def preview(
    ctx: RequestContext, data: bytes, start: date, end: date, file_name: str
) -> PayrollPreview:
    _require_manager(ctx)
    if end < start:
        raise LaborRejected(["El periodo de la nómina termina antes de empezar."])
    try:
        name = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, name, max_rows=MAX_ROWS + 1)
    except ExcelRejected as exc:
        return PayrollPreview(errors=(str(exc),))
    if not table:
        return PayrollPreview(errors=("El archivo está vacío.",))
    found, missing = imports.columns(table[0], HEADER, REQUIRED)
    if missing:
        return PayrollPreview(errors=tuple(missing))
    registry = set(scoped_qs(ctx, Worker).values_list("document_type", "document_number"))
    rows: list[PayrollRow] = []
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for line, values in enumerate(table[1:], start=2):
        kind, number, _name, total_value = imports.row_texts(values, found, HEADER)
        texts = [imports.cell_text(v) for v in (kind, number, total_value)]
        if not any(texts):
            continue
        problems: list[str] = []
        key = (texts[0].upper(), clean_document(texts[1]))
        if key[0] not in DocumentType.values:
            problems.append("El tipo de documento va como CC, CE, PPT, PEP o PA.")
        if not key[1] or len(key[1]) > 20:
            problems.append("Falta el número de documento o pasa de 20 caracteres.")
        if key in seen:
            problems.append(f"El documento se repite (fila {seen[key]}).")
        seen.setdefault(key, line)
        try:
            total = imports.cell_decimal(total_value)
        except ValueError:
            total = None
        if not wage_ok(total):
            problems.append("El total pagado va en pesos, mayor que cero y a centavos.")
        if problems or total is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            rows.append(PayrollRow(line, key[0], key[1], total))
        if len(errors) >= MAX_ERRORS:
            errors.append(imports.MORE_ERRORS)
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de nómina.")
    known = sum(1 for row in rows if (row.document_type, row.document_number) in registry)
    if errors:
        return PayrollPreview(rows=tuple(rows), errors=tuple(errors), known=known)
    body = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "file_name": file_name[:MAX_FILE_NAME],
        "file_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": [[r.line, r.document_type, r.document_number, str(r.total)] for r in rows],
    }
    return PayrollPreview(rows=tuple(rows), token=imports.sign(ctx, SALT, body), known=known)


def apply(ctx: RequestContext, token: str) -> PayrollImport:
    _require_manager(ctx)
    payload = imports.load(ctx, SALT, token)
    rows = [
        PayrollRow(int(line), kind, number, Decimal(total))
        for line, kind, number, total in payload.get("rows", [])
    ]
    if not rows:
        raise LaborRejected(["La vista previa no tiene filas."])
    with transaction.atomic():
        imports.consume(ctx, str(LaborImportKind.PAYROLL), payload["nonce"])
        workers = {
            (w.document_type, w.document_number): w.pk for w in scoped_qs(ctx, Worker)
        }
        imported = scoped_qs(ctx, PayrollImport).create(
            org_id=ctx.org_id,
            starts_on=date.fromisoformat(payload["start"]),
            ends_on=date.fromisoformat(payload["end"]),
            file_name=payload["file_name"],
            file_size=int(payload["file_size"]),
            file_sha256=payload["sha256"],
            line_count=len(rows),
            total=sum((row.total for row in rows), ZERO),
            imported_by_id=ctx.user_id,
        )
        scoped_qs(ctx, PayrollImportLine).bulk_create(
            [
                PayrollImportLine(
                    org_id=ctx.org_id, payroll=imported, row=row.line,
                    document_type=row.document_type, document_number=row.document_number,
                    worker_id=workers.get((row.document_type, row.document_number)),
                    total_paid=row.total,
                )  # fmt: skip
                for row in rows
            ]
        )
        audit.record(
            ctx,
            action=AuditAction.PAYROLL_IMPORTED,
            target=f"payroll_import:{imported.pk}",
            metadata={
                "start": payload["start"],
                "end": payload["end"],
                "lines": len(rows),
                "total": str(imported.total),
                "matched": sum(1 for row in rows
                               if (row.document_type, row.document_number) in workers),
                "sha256": payload["sha256"],
            },
        )  # fmt: skip
    return imported


@dataclass(frozen=True)
class ReconRow:
    worker_id: UUID
    label: str
    position: str
    days: Decimal
    estimated: Decimal
    #: `None`: no aparece en la nómina.
    paid: Decimal | None

    @property
    def difference(self) -> Decimal:
        return (self.paid or ZERO) - self.estimated

    @property
    def pct(self) -> Decimal | None:
        """Diferencia sobre lo tareado, en %, con un decimal."""
        if not self.estimated:
            return None
        with localcontext() as context:
            context.prec = WIDE_PRECISION
            return (self.difference * 100 / self.estimated).quantize(Decimal("0.1"))

    @property
    def flag(self) -> str:
        if self.days == 0:
            return PAID_WITHOUT_TAREO
        if self.paid is None:
            return TAREO_WITHOUT_PAY
        return ""


@dataclass(frozen=True)
class UnknownLine:
    row: int
    document: str
    paid: Decimal


@dataclass(frozen=True)
class Reconciliation:
    payroll: PayrollImport
    project: Project | None
    rows: tuple[ReconRow, ...]
    unknown: tuple[UnknownLine, ...]
    #: Por proyecto: pagados sin tareo en toda la organización (no se pueden prorratear).
    excluded: int = 0

    @property
    def days(self) -> Decimal:
        return sum((row.days for row in self.rows), NO_DAYS)

    @property
    def estimated(self) -> Decimal:
        return sum((row.estimated for row in self.rows), ZERO)

    @property
    def paid(self) -> Decimal:
        return sum((row.paid or ZERO for row in self.rows), ZERO)

    @property
    def difference(self) -> Decimal:
        return self.paid - self.estimated


def _tareo(
    ctx: RequestContext, start: date, end: date, project: Project | None
) -> dict[UUID, tuple[Decimal, Decimal]]:
    rows = scoped_qs(ctx, TimesheetLine).filter(
        timesheet__status=TimesheetStatus.APPROVED, worked_on__gte=start, worked_on__lte=end
    )
    if project is not None:
        rows = rows.filter(timesheet__project=project)
    return {
        worker_id: (days or NO_DAYS, amount or ZERO)
        for worker_id, days, amount in rows.values("worker_id")
        .annotate(days=Sum("fraction"), amount=Sum("amount"))
        .values_list("worker_id", "days", "amount")
    }


def reconcile(
    ctx: RequestContext, payroll: PayrollImport, project: Project | None = None
) -> Reconciliation:
    if not can_read_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    lines = list(
        scoped_qs(ctx, PayrollImportLine)
        .filter(payroll=payroll)
        .values_list("row", "document_type", "document_number", "worker_id", "total_paid")
    )
    paid: dict[UUID, Decimal] = {}
    unknown: list[UnknownLine] = []
    for row, kind, number, worker_id, total in lines:
        if worker_id is None:
            unknown.append(UnknownLine(row, f"{kind} {mask_document(number)}", total))
        else:
            paid[worker_id] = total
    everywhere = _tareo(ctx, payroll.starts_on, payroll.ends_on, None)
    here = _tareo(ctx, payroll.starts_on, payroll.ends_on, project) if project else everywhere
    excluded = 0
    shown: dict[UUID, Decimal | None] = {}
    for worker_id, total in paid.items():
        if project is None:
            shown[worker_id] = total
            continue
        org_days = everywhere.get(worker_id, (NO_DAYS, ZERO))[0]
        if not org_days:
            excluded += 1
            continue
        project_days = here.get(worker_id, (NO_DAYS, ZERO))[0]
        if project_days:
            with localcontext() as context:
                context.prec = WIDE_PRECISION
                shown[worker_id] = money(total * project_days / org_days)
    for worker_id in here:
        shown.setdefault(worker_id, None)
    workers: Mapping[UUID, Any] = {
        w.pk: w for w in scoped_qs(ctx, Worker).filter(pk__in=list(shown))
    }
    rows = [
        ReconRow(
            worker_id=worker_id,
            label=workers[worker_id].label,
            position=workers[worker_id].position,
            days=here.get(worker_id, (NO_DAYS, ZERO))[0],
            estimated=here.get(worker_id, (NO_DAYS, ZERO))[1],
            paid=amount,
        )
        for worker_id, amount in shown.items()
        if worker_id in workers
    ]
    rows.sort(key=lambda row: row.label)
    return Reconciliation(payroll, project, tuple(rows), tuple(unknown), excluded)
```

Revisar en la prueba de la organización: Pedro tiene 4 jornales (2 aquí y 2 en la Obra 2): estimado 4 × 121.600 = 486.400; pagado 500.000; diferencia 13.600; 13.600 / 486.400 = 2,796… % → `2.8`.

- [ ] **Step 4: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_payroll.py`
Expected: PASS.

- [ ] **Step 5: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): payroll import and reconciliation against approved timesheets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Consultas: listas, ficha del trabajador y cuadrícula semanal

**Files:**
- Create: `server/suno/labor/queries.py`, `server/suno/labor/grid.py`
- Test: `server/suno/labor/tests/test_grid.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_grid.py`:

```python
"""Cuadrícula semanal y vista por día (spec 3.2), listas y consultas de número fijo."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.labor import grid, queries, timesheets

from .factories import TODAY, WEEK, bare_timesheet, labor_world, line_data, to_item

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


@skip_without_db
class GridTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.ctx = self.lw.ctx()
        self.sheet = timesheets.create_timesheet(self.ctx, self.lw.project, date(2026, 3, 4),
                                                 date(2026, 3, 17))  # fmt: skip
        timesheets.save_lines(self.ctx, self.lw.project, self.sheet.pk, [
            line_data(self.lw.pedro, date(2026, 3, 4)),
            line_data(self.lw.pedro, date(2026, 3, 5), "0.5"),
            line_data(self.lw.pedro, date(2026, 3, 5), "0.5", to_item(self.lw)),
            line_data(self.lw.pedro, date(2026, 3, 10), "1"),
        ], timesheets.period_days(date(2026, 3, 4), date(2026, 3, 17)))  # fmt: skip
        self.sheet.refresh_from_db()
        self.catalog = load_catalog(self.ctx, self.lw.project)
        self.tasks = assignable_tasks(self.ctx, self.lw.project)

    def test_weeks_are_monday_to_sunday_clipped_to_the_period(self) -> None:
        first = grid.week_of(self.sheet, None)
        self.assertEqual(first.days[0], date(2026, 3, 4))  # miércoles: empieza la planilla
        self.assertEqual(first.days[-1], date(2026, 3, 8))  # domingo
        self.assertIsNone(first.previous)
        self.assertEqual(first.next, date(2026, 3, 9))
        last = grid.week_of(self.sheet, date(2026, 3, 16))
        self.assertEqual(last.days, (date(2026, 3, 16), date(2026, 3, 17)))
        self.assertEqual(last.previous, date(2026, 3, 9))
        self.assertIsNone(last.next)
        self.assertEqual(grid.week_of(self.sheet, date(2027, 1, 1)).days[0], date(2026, 3, 4))

    def test_rows_by_worker_and_destination_with_totals(self) -> None:
        with self.assertNumQueries(6):
            built = grid.build_grid(self.ctx, self.lw.project, self.sheet, self.catalog,
                                    self.tasks, date(2026, 3, 2))  # fmt: skip
        rows = [(r.worker.split(" · ")[0], r.destination, r.cells) for r in built.rows]
        self.assertEqual(rows, [
            ("María Gómez", "", ("", "", "", "", "")),        # asignada, sin tareo esta semana
            ("Pedro Pérez", "", ("1", "0,5", "", "", "")),     # indirecto
            ("Pedro Pérez", built.rows[2].destination, ("", "0,5", "", "", "")),
        ])  # fmt: skip
        self.assertTrue(built.rows[2].destination.startswith("2.1"))
        self.assertEqual(built.day_totals, (D("1"), D("1"), D("0"), D("0"), D("0")))
        # Totales del periodo completo (también la semana siguiente).
        self.assertEqual(built.days, D("3.0000"))
        self.assertEqual(built.total, D("364800.00"))
        self.assertEqual(dict((label.split(" · ")[0], days) for label, days, _ in built.by_worker),
                         {"Pedro Pérez": D("3.0000")})  # fmt: skip
        self.assertFalse(built.changed)

    def test_day_view_lists_the_rows_of_one_day(self) -> None:
        built = grid.build_grid(self.ctx, self.lw.project, self.sheet, self.catalog, self.tasks,
                                date(2026, 3, 2))  # fmt: skip
        day = grid.day_rows(built, date(2026, 3, 5))
        self.assertEqual([(r.worker.split(" · ")[0], value) for r, value in day],
                         [("María Gómez", ""), ("Pedro Pérez", "0,5"), ("Pedro Pérez", "0,5")])  # fmt: skip

    def test_lists(self) -> None:
        bare_timesheet(self.lw, number=2, status="approved", start=date(2026, 2, 2),
                       end=date(2026, 2, 8))  # fmt: skip
        with self.assertNumQueries(2):
            page = queries.timesheet_page(self.ctx, self.lw.project, page=1)
            codes = [row.code for row in page]
        self.assertEqual(codes, ["TA-002", "TA-001"])
        self.assertEqual(queries.pending_count(self.ctx, self.lw.project), 0)
        with self.assertNumQueries(1):
            assigned = list(queries.assignment_rows(self.ctx, self.lw.project))
        self.assertEqual([a.worker.full_name for a in assigned], ["María Gómez", "Pedro Pérez"])
        result = queries.worker_page(self.ctx, queries.WorkerFilters(text="pedro"), page=1)
        self.assertEqual([w.full_name for w in result], ["Pedro Pérez"])
        self.assertEqual(queries.positions(self.ctx), ["Ayudante", "Oficial"])
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_grid.py`
Expected: FAIL (`cannot import name 'grid' from 'suno.labor'`).

- [ ] **Step 3: Las listas**

`server/suno/labor/queries.py`:

```python
"""Consultas de mano de obra para la web (costos, pieza 6d, spec 3.1 y 3.2). Número fijo de
consultas: las listas, una o dos; la ficha del trabajador, tres."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.core.paginator import Page, Paginator
from django.db.models import Q, QuerySet

from suno.core.context import RequestContext
from suno.core.models import (
    PayrollImport,
    Project,
    ProjectWorker,
    Timesheet,
    TimesheetStatus,
    Worker,
    WorkerWage,
)
from suno.core.scoping import scoped_qs

PAGE_SIZE = 100
STATES = {"activos": "Activos", "retirados": "Retirados", "todos": "Todos"}


def timesheet_page(ctx: RequestContext, project: Project, *, page: int | str | None) -> Page[Any]:
    """Las planillas del proyecto, de la más nueva a la más vieja (borradores por aprobar
    primero no: el orden es el número; la cabecera dice cuántas hay por aprobar)."""
    rows = (
        scoped_qs(ctx, Timesheet)
        .filter(project=project)
        .select_related("created_by")
        .order_by("-number")
    )
    return Paginator(rows, PAGE_SIZE).get_page(page)


def pending_count(ctx: RequestContext, project: Project) -> int:
    """Planillas en borrador con la planilla firmada: listas para aprobar (decisión 19)."""
    return (
        scoped_qs(ctx, Timesheet)
        .filter(project=project, status=TimesheetStatus.DRAFT, attachment__isnull=False)
        .count()
    )


def assignment_rows(ctx: RequestContext, project: Project) -> QuerySet[ProjectWorker]:
    return (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project)
        .select_related("worker")
        .order_by("worker__full_name", "starts_on")
    )


def active_workers(ctx: RequestContext) -> QuerySet[Worker]:
    """Los que se pueden asignar (el formulario muestra nombre y documento enmascarado)."""
    return scoped_qs(ctx, Worker).filter(is_active=True).order_by("full_name")


@dataclass(frozen=True)
class WorkerFilters:
    text: str = ""
    position: str = ""
    state: str = "activos"

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> WorkerFilters:
        state = query.get("estado") or "activos"
        return cls(
            text=(query.get("texto") or "").strip()[:100],
            position=(query.get("cargo") or "").strip()[:80],
            state=state if state in STATES else "activos",
        )


def worker_page(ctx: RequestContext, filters: WorkerFilters, *, page: int | str | None) -> Page[Any]:
    rows = scoped_qs(ctx, Worker).order_by("full_name")
    if filters.text:
        rows = rows.filter(Q(full_name__icontains=filters.text)
                           | Q(document_number__icontains=filters.text))  # fmt: skip
    if filters.position:
        rows = rows.filter(position__iexact=filters.position)
    if filters.state != "todos":
        rows = rows.filter(is_active=filters.state == "activos")
    return Paginator(rows, PAGE_SIZE).get_page(page)


def positions(ctx: RequestContext) -> list[str]:
    """Los cargos ya usados (sugerencias del `datalist`)."""
    return list(
        scoped_qs(ctx, Worker)
        .exclude(position="")
        .order_by("position")
        .values_list("position", flat=True)
        .distinct()
    )


@dataclass(frozen=True)
class WorkerSheet:
    worker: Worker
    wages: tuple[WorkerWage, ...]
    assignments: tuple[ProjectWorker, ...]


def worker_sheet(ctx: RequestContext, worker: Worker) -> WorkerSheet:
    return WorkerSheet(
        worker,
        tuple(scoped_qs(ctx, WorkerWage).filter(worker=worker).order_by("-valid_from")),
        tuple(
            scoped_qs(ctx, ProjectWorker)
            .filter(worker=worker)
            .select_related("project")
            .order_by("-starts_on")
        ),
    )


def payroll_rows(ctx: RequestContext) -> QuerySet[PayrollImport]:
    return scoped_qs(ctx, PayrollImport).select_related("imported_by").order_by("-starts_on")[:200]
```

(En la prueba, `timesheet_page` cuenta y trae la página: dos consultas; el `select_related` evita una por fila.)

- [ ] **Step 4: La cuadrícula**

`server/suno/labor/grid.py`:

```python
"""Cuadrícula semanal de una planilla y vista por día (costos, pieza 6d, spec 3.2).

- Una fila por trabajador y destino, una columna por día de la semana (lunes a domingo,
  recortada al periodo) y la fracción en cada celda. Los trabajadores asignados en el periodo
  sin tareo aparecen con una fila vacía (destino Indirecto).
- Totales por día (de la semana) y, del periodo completo, por persona y por destino, con el
  costo estimado de hoy; `changed` avisa si difiere del resumen guardado (lo que se firmó).
- Seis consultas: las cinco de `timesheets.evaluate` y las asignaciones del periodo.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Q

from suno.budgets.totals import ZERO
from suno.core.context import RequestContext
from suno.core.models import Project, ProjectWorker, Task, Timesheet
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog
from suno.materials.destination import INDIRECT_LABEL, destination_text

from .calc import NO_DAYS, days_text
from .calc import problems as find_problems
from .timesheets import evaluate


@dataclass(frozen=True)
class Week:
    days: tuple[date, ...]
    start: date
    previous: date | None
    next: date | None


@dataclass(frozen=True)
class GridRow:
    worker_id: UUID
    #: "Pedro Pérez · CC ***5678"
    worker: str
    position: str
    #: Lo que va en el campo "Destino" ("" = Indirecto) y en "Tarea" (número de esquema).
    destination: str
    task: str
    #: La etiqueta del destino ("Indirecto (proyecto)", "2.1 Concreto · tarea 2.1 Columnas").
    label: str
    #: Una por día de la semana: "1", "0,5" o "".
    cells: tuple[str, ...]
    days: Decimal
    cost: Decimal | None


@dataclass(frozen=True)
class Grid:
    sheet: Timesheet
    week: Week
    rows: tuple[GridRow, ...]
    day_totals: tuple[Decimal, ...]
    #: Del periodo completo: (etiqueta, jornales, costo).
    by_worker: tuple[tuple[str, Decimal, Decimal], ...]
    by_destination: tuple[tuple[str, Decimal, Decimal], ...]
    days: Decimal
    total: Decimal
    problems: tuple[str, ...]
    #: Lo de hoy difiere del resumen guardado (cambió un jornal o el factor).
    changed: bool


def cell_text(value: Decimal | None) -> str:
    return days_text(value) if value else ""


def week_of(sheet: Timesheet, start: date | None) -> Week:
    """La semana (lunes a domingo) que contiene `start`, recortada al periodo; si la semana de
    `start` no toca el periodo, la primera."""
    if start is None or not sheet.starts_on - timedelta(days=6) <= start <= sheet.ends_on:
        start = sheet.starts_on
    monday = start - timedelta(days=start.weekday())
    if monday + timedelta(days=6) < sheet.starts_on:
        monday = sheet.starts_on - timedelta(days=sheet.starts_on.weekday())
    first = max(monday, sheet.starts_on)
    last = min(monday + timedelta(days=6), sheet.ends_on)
    days = tuple(first + timedelta(days=n) for n in range((last - first).days + 1))
    previous = monday - timedelta(days=7) if monday > sheet.starts_on else None
    following = monday + timedelta(days=7)
    return Week(days, monday, previous, following if following <= sheet.ends_on else None)


def build_grid(
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    catalog: Catalog,
    tasks: dict[UUID, Task],
    start: date | None,
) -> Grid:
    week = week_of(sheet, start)
    evaluation = evaluate(ctx, sheet, catalog)
    names = dict(evaluation.names)
    positions: dict[UUID, str] = {}
    for row in (
        scoped_qs(ctx, ProjectWorker)
        .filter(project=project, starts_on__lte=sheet.ends_on)
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=sheet.starts_on))
        .select_related("worker")
    ):
        names.setdefault(row.worker_id, row.worker.label)
        positions[row.worker_id] = row.worker.position
    index = {day: n for n, day in enumerate(week.days)}
    grouped: dict[tuple[UUID, str], dict[str, Any]] = {}
    by_worker: dict[UUID, list[Decimal]] = defaultdict(lambda: [NO_DAYS, ZERO])
    by_key: dict[str, list[Decimal]] = defaultdict(lambda: [NO_DAYS, ZERO])
    day_totals = [NO_DAYS] * len(week.days)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        amount = cost.amount or ZERO
        by_worker[line.worker_id][0] += line.fraction
        by_worker[line.worker_id][1] += amount
        by_key[line.key][0] += line.fraction
        by_key[line.key][1] += amount
        entry = grouped.setdefault((line.worker_id, line.key), {
            "line": line, "cells": [None] * len(week.days), "days": NO_DAYS, "cost": ZERO,
            "complete": True,
        })  # fmt: skip
        if line.day in index:
            entry["cells"][index[line.day]] = line.fraction
            entry["days"] += line.fraction
            entry["cost"] += amount
            entry["complete"] &= cost.amount is not None
            day_totals[index[line.day]] += line.fraction
    rows: list[GridRow] = []
    for (worker_id, key), entry in grouped.items():
        line = entry["line"]
        rows.append(GridRow(
            worker_id, names.get(worker_id, "—"), positions.get(worker_id, ""),
            destination_text(catalog, line.scope, line.chapter_id, line.item_ids),
            line.task_outline, evaluation.labels.get(key, "—"),
            tuple(cell_text(value) for value in entry["cells"]), entry["days"],
            entry["cost"] if entry["complete"] else None,
        ))  # fmt: skip
    with_rows = {row.worker_id for row in rows}
    for worker_id in positions:
        if worker_id not in with_rows:
            rows.append(GridRow(worker_id, names[worker_id], positions[worker_id], "", "",
                                INDIRECT_LABEL, ("",) * len(week.days), NO_DAYS, ZERO))  # fmt: skip
    rows.sort(key=lambda row: (row.worker, row.destination != "", row.label))
    stored = (sheet.summary or {}).get("fingerprint")
    return Grid(
        sheet=sheet,
        week=week,
        rows=tuple(rows),
        day_totals=tuple(day_totals),
        by_worker=tuple(sorted(((names.get(w, "—"), d, a) for w, (d, a) in by_worker.items()),
                               key=lambda r: r[0])),
        by_destination=tuple(sorted(((evaluation.labels.get(k, "—"), d, a)
                                     for k, (d, a) in by_key.items()), key=lambda r: r[0])),
        days=evaluation.summary.days,
        total=evaluation.summary.total,
        problems=tuple(find_problems(evaluation.costs, evaluation.names)),
        changed=stored is not None and evaluation.summary.fingerprint() != stored,
    )  # fmt: skip


def day_rows(built: Grid, day: date) -> list[tuple[GridRow, str]]:
    """La vista por día (390 px): cada fila con la fracción de ese día."""
    if day not in built.week.days:
        return []
    position = built.week.days.index(day)
    return [(row, row.cells[position]) for row in built.rows]
```

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_grid.py`
Expected: PASS. Si el número de consultas difiere por las del `select_related`, ajustar la consulta (no la prueba): son seis.

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor
git commit -m "feat(labor): weekly grid, day view and list queries with a fixed number of queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: Registro de trabajadores y factor en la web (organización) y enlace lateral

**Files:**
- Create: `server/suno/labor/access.py`, `server/suno/labor/forms.py`, `server/suno/labor/registry_views.py`, `server/suno/labor/registry_urls.py`
- Create: `server/suno/core/templates/suno/labor/{_registry_nav,workers,worker_form,worker_detail,workers_import,factors}.html`
- Modify: `server/config/urls.py`, `server/suno/core/navigation.py`, `server/suno/core/templates/suno/_sidebar.html`
- Test: `server/suno/labor/tests/test_views_registry.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_views_registry.py`:

```python
"""Registro de trabajadores y factor en la web (spec 3.1 y 3.6; decisión 3 del plan)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.core.access import AccessScope, SectionRole
from suno.core.models import AuditEvent, BenefitFactor, Worker, WorkerWage
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, grant_labor, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
BASE = "/plataforma/trabajadores/"


@skip_without_db
class RegistryPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()

    def test_list_masks_documents_and_the_sheet_shows_them_whole(self) -> None:
        self.client.force_login(self.lw.world.admin)
        page = self.client.get(BASE)
        self.assertContains(page, "Pedro Pérez")
        self.assertContains(page, "CC ***5678")
        self.assertNotContains(page, "1012345678")
        sheet = self.client.get(f"{BASE}{self.lw.pedro.pk}/")
        self.assertContains(sheet, "1012345678")
        self.assertContains(sheet, "80.000,00")
        self.assertContains(self.client.get(BASE), 'href="/plataforma/trabajadores/"', html=False)

    def test_who_sees_the_registry(self) -> None:
        for user, status in (
            (self.lw.accountant, 404),  # solo Costos
            (self.lw.world.agent, 404),
            (self.lw.viewer, 200),  # Consulta con alcance de organización: lee
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(BASE).status_code, status)
        limited = self.lw.cw.contributor
        grant_labor(self.lw.world, limited, SectionRole.MANAGER, scope=AccessScope.PROJECTS,
                    projects=[self.lw.project])  # fmt: skip
        self.client.force_login(limited)
        self.assertEqual(self.client.get(BASE).status_code, 404)
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(f"{BASE}nuevo/").status_code, 403)
        self.assertNotContains(self.client.get(BASE), "Nuevo trabajador")

    def test_create_change_wage_retire_and_delete(self) -> None:
        self.client.force_login(self.lw.world.admin)
        created = self.client.post(f"{BASE}nuevo/", {
            "document_type": "CC", "document_number": "1.023.456.789", "full_name": "Luis Torres",
            "position": "Oficial", "hired_on": "2026-02-01", "daily_wage": "85000",
        })  # fmt: skip
        luis = Worker.objects.get(document_number="1023456789")
        self.assertRedirects(created, f"{BASE}{luis.pk}/")
        self.client.post(f"{BASE}{luis.pk}/jornal/", {"valid_from": "2026-04-01",
                                                      "daily_wage": "90000"})  # fmt: skip
        self.assertEqual(WorkerWage.objects.filter(worker=luis).count(), 2)
        self.client.post(f"{BASE}{luis.pk}/retirar/", {"retired_on": "2026-04-10"})
        luis.refresh_from_db()
        self.assertFalse(luis.is_active)
        self.assertRedirects(self.client.post(f"{BASE}{luis.pk}/borrar/"), BASE)
        self.assertFalse(Worker.objects.filter(pk=luis.pk).exists())

    def test_import_in_two_steps(self) -> None:
        self.client.force_login(self.lw.world.admin)
        header = ["Tipo de documento", "Número de documento", "Nombre", "Cargo",
                  "Fecha de ingreso", "Jornal"]  # fmt: skip
        book = xlsx("Trabajadores", header, [["CC", "70111222", "Ana", "Ayudante", "2026-02-01",
                                              "70000"]])  # fmt: skip
        preview = self.client.post(f"{BASE}importar/", {"file": _file(book)})
        self.assertContains(preview, "nuevo")
        token = preview.context["result"].token
        applied = self.client.post(f"{BASE}importar/aplicar/", {"token": token})
        self.assertRedirects(applied, BASE)
        self.assertTrue(Worker.objects.filter(document_number="70111222").exists())
        template = self.client.get(f"{BASE}plantilla.xlsx")
        self.assertEqual(template.status_code, 200)

    def test_factor_history(self) -> None:
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(f"{BASE}factor/"), "1,5200")
        self.client.post(f"{BASE}factor/", {"valid_from": "2026-04-01", "factor": "1.55",
                                            "note": "Dotación"})  # fmt: skip
        self.assertTrue(
            BenefitFactor.objects.filter(org=self.lw.world.org, factor=D("1.5500")).exists()
        )
        self.assertTrue(AuditEvent.objects.filter(action="benefit_factor.changed").exists())


def _file(content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "trabajadores.xlsx",
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_registry.py`
Expected: FAIL (404 en `/plataforma/trabajadores/`: la URL no existe).

- [ ] **Step 3: Acceso**

`server/suno/labor/access.py`:

```python
"""Quién ve la mano de obra y cómo se resuelven los objetos de la URL (costos, pieza 6d).

- Pestaña "Tareo" del proyecto: sección Mano de obra con `read` en ese proyecto; si no, 404
  (RD-11), igual para ids de otra organización o de otro proyecto.
- Registro de la organización: `can_read_labor_registry` (404 si no); escribir,
  `can_manage_labor_registry` (403 si no lee nada más)."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest

from suno.core.access import Section
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.models import AppUser, PayrollImport, Project, Timesheet, Worker
from suno.core.policies import (
    can_manage_labor_registry,
    can_read_labor,
    can_read_labor_registry,
)
from suno.core.scoping import scoped_qs
from suno.projects.access import get_visible_project


def get_labor_project(ctx: RequestContext, project_id: UUID) -> Project:
    if not ctx.has_section_access(Section.LABOR):
        raise Http404
    project = get_visible_project(ctx, project_id)
    if not can_read_labor(ctx, project):
        raise Http404
    return project


def get_timesheet(
    ctx: RequestContext, project: Project, timesheet_id: UUID, *related: str
) -> Timesheet:
    found = (
        scoped_qs(ctx, Timesheet)
        .select_related(*related)
        .filter(pk=timesheet_id, project=project)
        .first()
    )
    if found is None:
        raise Http404
    return found


def registry_ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not can_read_labor_registry(ctx, ctx.org_id):
        raise Http404
    return ctx


def registry_manager(request: HttpRequest) -> RequestContext:
    ctx = registry_ctx(request)
    if not can_manage_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    return ctx


def get_worker(ctx: RequestContext, worker_id: UUID) -> Worker:
    found = scoped_qs(ctx, Worker).filter(pk=worker_id).first()
    if found is None:
        raise Http404
    return found


def get_payroll(ctx: RequestContext, payroll_id: UUID) -> PayrollImport:
    found = scoped_qs(ctx, PayrollImport).filter(pk=payroll_id).first()
    if found is None:
        raise Http404
    return found
```

- [ ] **Step 4: Formularios del registro**

`server/suno/labor/forms.py` (la Task 13 y la 14 le agregan los del proyecto y la cuadrícula):

```python
"""Formularios de mano de obra (costos, pieza 6d). Sin JavaScript obligatorio (ADR-0020)."""

from __future__ import annotations

from django import forms

from suno.core.models import DocumentType

from .common import MAX_NAME, MAX_NOTE, MAX_POSITION
from .registry import WorkerData


def date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class WorkerForm(forms.Form):
    document_type = forms.ChoiceField(label="Tipo de documento", choices=DocumentType.choices)
    document_number = forms.CharField(label="Número de documento", max_length=30)
    full_name = forms.CharField(label="Nombre", max_length=MAX_NAME)
    position = forms.CharField(
        label="Cargo", max_length=MAX_POSITION, required=False,
        widget=forms.TextInput(attrs={"list": "ta-positions", "autocomplete": "off"}),
    )  # fmt: skip
    hired_on = forms.DateField(label="Fecha de ingreso", widget=date_input())

    def worker_data(self) -> WorkerData:
        data = self.cleaned_data
        return WorkerData(data["document_type"], data["document_number"], data["full_name"],
                          data.get("position") or "", data["hired_on"])  # fmt: skip


class NewWorkerForm(WorkerForm):
    daily_wage = forms.DecimalField(
        label="Jornal básico", max_digits=14, decimal_places=2, min_value=0
    )


class WageForm(forms.Form):
    valid_from = forms.DateField(label="Desde", widget=date_input())
    daily_wage = forms.DecimalField(label="Jornal básico", max_digits=14, decimal_places=2,
                                    min_value=0)  # fmt: skip


class RetireForm(forms.Form):
    retired_on = forms.DateField(label="Fecha de retiro", widget=date_input())


class FactorForm(forms.Form):
    valid_from = forms.DateField(label="Desde", widget=date_input())
    factor = forms.DecimalField(label="Factor", max_digits=6, decimal_places=4, min_value=1,
                                max_value=3)  # fmt: skip
    note = forms.CharField(label="Composición", max_length=MAX_NOTE, required=False)
```

- [ ] **Step 5: Vistas del registro**

`server/suno/labor/registry_views.py`:

```python
"""Registro de trabajadores y factor prestacional en la web (costos, pieza 6d, spec 3.1).

Ver pide `can_read_labor_registry` (404 si no); escribir, `can_manage_labor_registry` (403).
Las reglas viven en los servicios: aquí solo se traducen a mensajes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import BenefitFactor, WorkerWage
from suno.core.policies import can_export_labor_registry, can_manage_labor_registry
from suno.core.scoping import scoped_qs
from suno.projects.views import _render

from . import registry, registry_import
from .access import get_worker, registry_ctx, registry_manager
from .common import LaborRejected
from .forms import FactorForm, NewWorkerForm, RetireForm, WageForm, WorkerForm
from .queries import STATES, WorkerFilters, positions, worker_page, worker_sheet

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def page(request: HttpRequest, ctx: RequestContext, template: str, **context: Any) -> HttpResponse:
    return _render(
        request,
        ctx,
        template,
        {
            "sidebar_active": "labor",
            "can_manage_registry": can_manage_labor_registry(ctx, ctx.org_id),
            "can_export_registry": can_export_labor_registry(ctx, ctx.org_id),
            **context,
        },
    )


def run(request: HttpRequest, action: Callable[[], Any], success: str) -> bool:
    try:
        action()
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


@login_required(login_url="platform_login")
@require_GET
def worker_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    filters = WorkerFilters.from_query(request.GET)
    query = urlencode({k: v for k, v in (("texto", filters.text), ("cargo", filters.position),
                                         ("estado", filters.state)) if v})  # fmt: skip
    return page(request, ctx, "suno/labor/workers.html", filters=filters, states=STATES,
                positions=positions(ctx), query=query,
                page=worker_page(ctx, filters, page=request.GET.get("pagina")))  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def worker_new(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    form = NewWorkerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        created: list[Any] = []
        if run(request, lambda: created.append(registry.create_worker(
                ctx, form.worker_data(), form.cleaned_data["daily_wage"])),
               "Trabajador registrado."):  # fmt: skip
            return redirect("labor_registry:worker", created[0].pk)
    return page(request, ctx, "suno/labor/worker_form.html", form=form, worker=None,
                positions=positions(ctx))  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def worker_detail(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    sheet = worker_sheet(ctx, get_worker(ctx, worker_id))
    wages = [
        (wage, registry.wage_in_use(ctx, wage)) for wage in sheet.wages
    ]  # una consulta por fila: el historial de un trabajador es corto
    return page(request, ctx, "suno/labor/worker_detail.html", sheet=sheet, wages=wages,
                wage_form=WageForm(), retire_form=RetireForm())  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def worker_edit(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    worker = get_worker(ctx, worker_id)
    initial = {"document_type": worker.document_type, "document_number": worker.document_number,
               "full_name": worker.full_name, "position": worker.position,
               "hired_on": worker.hired_on}  # fmt: skip
    form = WorkerForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid() and run(
        request, lambda: registry.update_worker(ctx, worker.pk, form.worker_data()),
        "Trabajador actualizado.",
    ):
        return redirect("labor_registry:worker", worker.pk)
    return page(request, ctx, "suno/labor/worker_form.html", form=form, worker=worker,
                positions=positions(ctx))  # fmt: skip


def _back(worker_id: UUID) -> HttpResponse:
    return redirect("labor_registry:worker", worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_wage(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    form = WageForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        run(request, lambda: registry.add_wage(ctx, worker_id, data["valid_from"],
                                               data["daily_wage"]), "Jornal registrado.")  # fmt: skip
    else:
        messages.error(request, "Escribe la fecha y el jornal.")
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def wage_update(request: HttpRequest, worker_id: UUID, wage_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    found = scoped_qs(ctx, WorkerWage).filter(pk=wage_id, worker_id=worker_id).exists()
    if not found:
        return _back(worker_id)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: registry.delete_wage(ctx, wage_id), "Jornal borrado.")
    else:
        form = WageForm(request.POST)
        if form.is_valid():
            run(request, lambda: registry.change_wage(ctx, wage_id, form.cleaned_data["daily_wage"]),
                "Jornal corregido.")  # fmt: skip
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_retire(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if request.POST.get("accion") == "reincorporar":
        run(request, lambda: registry.reinstate_worker(ctx, worker_id), "Retiro deshecho.")
        return _back(worker_id)
    form = RetireForm(request.POST)
    if form.is_valid():
        run(request, lambda: registry.retire_worker(ctx, worker_id, form.cleaned_data["retired_on"]),
            "Trabajador retirado.")  # fmt: skip
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_POST
def worker_delete(request: HttpRequest, worker_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if run(request, lambda: registry.delete_worker(ctx, worker_id), "Trabajador borrado."):
        return redirect("labor_registry:list")
    return _back(worker_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    errors: list[str] = []
    result = None
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Elige un archivo .xlsx."]
        elif upload.size is not None and upload.size > MAX_BYTES:
            errors = ["El archivo supera 20 MB."]
        else:
            try:
                check_file_name(upload.name or "")
            except ExcelRejected as exc:
                errors = [str(exc)]
            else:
                result = registry_import.preview(ctx, upload.read())
    actions = (registry_import.NEW, registry_import.UPDATE, registry_import.SAME)
    counts = [(action, result.count(action)) for action in actions] if result else []
    return page(request, ctx, "suno/labor/workers_import.html", errors=errors, result=result,
                counts=counts)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def import_apply(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    try:
        created, updated = registry_import.apply(ctx, request.POST.get("token", ""))
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("labor_registry:import")
    messages.success(request, f"Trabajadores importados: {created} nuevos y {updated} actualizados.")
    return redirect("labor_registry:list")


@login_required(login_url="platform_login")
@require_GET
def import_template(request: HttpRequest) -> HttpResponse:
    registry_manager(request)
    response = HttpResponse(registry_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-trabajadores", ".xlsx", fallback="plantilla-trabajadores"
    )
    return response


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def factor_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    form = FactorForm(request.POST or None)
    if request.method == "POST":
        ctx = registry_manager(request)
        if form.is_valid():
            data = form.cleaned_data
            if run(request, lambda: registry.add_factor(ctx, data["valid_from"], data["factor"],
                                                        data.get("note") or ""),
                   "Factor registrado."):  # fmt: skip
                return redirect("labor_registry:factors")
    rows = [(f, registry.factor_in_use(ctx, f))
            for f in scoped_qs(ctx, BenefitFactor).order_by("-valid_from")]  # fmt: skip
    return page(request, ctx, "suno/labor/factors.html", form=form, rows=rows)


@login_required(login_url="platform_login")
@require_POST
def factor_update(request: HttpRequest, factor_id: UUID) -> HttpResponse:
    ctx = registry_manager(request)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: registry.delete_factor(ctx, factor_id), "Factor borrado.")
    else:
        form = FactorForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            run(request, lambda: registry.change_factor(ctx, factor_id, data["factor"],
                                                        data.get("note") or ""),
                "Factor corregido.")  # fmt: skip
    return redirect("labor_registry:factors")
```

`server/suno/labor/registry_urls.py`:

```python
"""Montadas bajo `plataforma/trabajadores/` (config/urls.py). La conciliación (Task 16) se
agrega aquí también."""

from django.urls import path

from . import registry_views

app_name = "labor_registry"

W = "<uuid:worker_id>/"

urlpatterns = [
    path("", registry_views.worker_list, name="list"),
    path("nuevo/", registry_views.worker_new, name="new"),
    path(W, registry_views.worker_detail, name="worker"),
    path(W + "editar/", registry_views.worker_edit, name="edit"),
    path(W + "jornal/", registry_views.worker_wage, name="wage"),
    path(W + "jornal/<uuid:wage_id>/", registry_views.wage_update, name="wage_update"),
    path(W + "retirar/", registry_views.worker_retire, name="retire"),
    path(W + "borrar/", registry_views.worker_delete, name="delete"),
    path("importar/", registry_views.import_upload, name="import"),
    path("importar/aplicar/", registry_views.import_apply, name="import_apply"),
    path("plantilla.xlsx", registry_views.import_template, name="template"),
    path("factor/", registry_views.factor_list, name="factors"),
    path("factor/<uuid:factor_id>/", registry_views.factor_update, name="factor_update"),
]
```

En `server/config/urls.py`, después de la línea de `plataforma/materiales/`:

```python
    path("plataforma/trabajadores/", include("suno.labor.registry_urls")),
```

En `server/suno/core/navigation.py`:

```python
"""Estado de navegación derivado del mismo RequestContext que autoriza."""

from __future__ import annotations

from suno.core.access import Section
from suno.core.context import RequestContext
from suno.core.policies import can_read_labor_registry


def platform_navigation(ctx: RequestContext) -> dict[str, bool]:
    found = {section: ctx.has_section_access(section) for section in Section.values}
    # "Trabajadores" (costos, pieza 6d): solo con Mano de obra en toda la organización.
    found["LABOR_REGISTRY"] = can_read_labor_registry(ctx, ctx.org_id)
    return found
```

En `server/suno/core/templates/suno/_sidebar.html`, después de la línea de "Materiales":

```html
        {% if navigation.LABOR_REGISTRY %}<a {% if active == "labor" %}class="active" {% endif %}href="{% url 'labor_registry:list' %}"><svg class="ui-icon" aria-hidden="true"><use href="{{ suno_icons }}#team"></use></svg> Trabajadores</a>{% endif %}
```

(`projects/base.html` incluye la barra con `active=sidebar_active|default:"projects"`; las vistas del registro pasan `sidebar_active="labor"`.)

- [ ] **Step 6: Plantillas del registro**

`server/suno/core/templates/suno/labor/_registry_nav.html`:

```html
<nav class="pj-tabs ta-nav" aria-label="Mano de obra">
  <a href="{% url 'labor_registry:list' %}" {% if sub == "workers" %}aria-current="page"{% endif %}>Trabajadores</a>
  <a href="{% url 'labor_registry:factors' %}" {% if sub == "factors" %}aria-current="page"{% endif %}>Factor prestacional</a>
</nav>
```

(La Task 16 le agrega el enlace a la conciliación.)

`server/suno/core/templates/suno/labor/workers.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Trabajadores{% endblock %}
{% block content %}
<section class="workspace-heading">
  <div><p class="editorial-label">Mano de obra</p><h1>Trabajadores</h1><p>Datos mínimos para el tareo: documento, nombre, cargo, ingreso y jornal.</p></div>
</section>
{% include "suno/labor/_registry_nav.html" with sub="workers" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros de trabajadores">
    <label>Nombre o documento <input name="texto" value="{{ filters.text }}" maxlength="100"></label>
    <label>Cargo <select name="cargo"><option value="">Todos</option>{% for p in positions %}<option value="{{ p }}" {% if p == filters.position %}selected{% endif %}>{{ p }}</option>{% endfor %}</select></label>
    <label>Estado <select name="estado">{% for key, label in states.items %}<option value="{{ key }}" {% if key == filters.state %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Filtrar</button>
  </form>
  {% if can_manage_registry %}<p class="ctr-actions"><a class="button" href="{% url 'labor_registry:new' %}">Nuevo trabajador</a> <a class="text-link" href="{% url 'labor_registry:import' %}">Importar Excel</a></p>{% endif %}
</section>
<section class="pj-panel">
  {% if page.object_list %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <caption class="sr-only">Trabajadores de la organización</caption>
    <thead><tr><th>Nombre</th><th>Documento</th><th>Cargo</th><th>Ingreso</th><th>Estado</th></tr></thead>
    <tbody>
    {% for w in page.object_list %}
      <tr>
        <td class="pj-name"><a href="{% url 'labor_registry:worker' w.pk %}">{{ w.full_name }}</a></td>
        <td>{{ w.masked_document }}</td>
        <td class="pj-wrap">{{ w.position|default:"—" }}</td>
        <td>{{ w.hired_on|date:"d/m/Y" }}</td>
        <td>{% if w.is_active %}Activo{% else %}<span class="pj-muted">Retirado el {{ w.retired_on|date:"d/m/Y" }}</span>{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% if page.has_other_pages %}<p class="pj-pages">{% if page.has_previous %}<a href="?{{ query }}&amp;pagina={{ page.previous_page_number }}">Anterior</a>{% endif %} Página {{ page.number }} de {{ page.paginator.num_pages }} {% if page.has_next %}<a href="?{{ query }}&amp;pagina={{ page.next_page_number }}">Siguiente</a>{% endif %}</p>{% endif %}
  {% else %}<p class="pj-empty">No hay trabajadores con ese filtro.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/labor/worker_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}{% if worker %}Editar trabajador{% else %}Nuevo trabajador{% endif %}{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'labor_registry:list' %}">Trabajadores</a></p><h1>{% if worker %}{{ worker.full_name }}{% else %}Nuevo trabajador{% endif %}</h1></div></section>
<section class="pj-panel">
  <p class="pj-muted">Solo datos mínimos (Ley 1581): no se guardan dirección, teléfono, correo, cuenta bancaria, EPS ni fondo de pensión.</p>
  <form method="post" class="pj-form">
    {% csrf_token %}
    {{ form.as_div }}
    <datalist id="ta-positions">{% for p in positions %}<option value="{{ p }}">{% endfor %}</datalist>
    <button class="button" type="submit">Guardar</button>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/labor/worker_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{{ sheet.worker.full_name }}{% endblock %}
{% block content %}
{% with w=sheet.worker %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'labor_registry:list' %}">Trabajadores</a></p><h1>{{ w.full_name }}</h1><p>{{ w.get_document_type_display }} {{ w.document_number }} · {{ w.position|default:"sin cargo" }} · ingreso {{ w.hired_on|date:"d/m/Y" }}{% if w.retired_on %} · retiro {{ w.retired_on|date:"d/m/Y" }}{% endif %}</p></div></section>
<section class="pj-panel">
  <h2>Jornal básico</h2>
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Desde</th><th class="num">Jornal</th><th>Uso</th>{% if can_manage_registry %}<th>Corregir</th>{% endif %}</tr></thead>
    <tbody>{% for wage, used in wages %}<tr>
      <td>{{ wage.valid_from|date:"d/m/Y" }}</td><td class="num">{{ wage.daily_wage|money }}</td>
      <td>{% if used %}Usado en planillas aprobadas{% else %}<span class="pj-muted">Sin usar</span>{% endif %}</td>
      {% if can_manage_registry %}<td>{% if not used %}<form method="post" action="{% url 'labor_registry:wage_update' w.pk wage.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="valid_from" value="{{ wage.valid_from|date:'Y-m-d' }}"><input name="daily_wage" inputmode="decimal" aria-label="Jornal corregido" value="{{ wage.daily_wage }}" size="10"><button class="text-link" type="submit">Corregir</button><button class="text-link" type="submit" name="accion" value="borrar">Borrar</button></form>{% endif %}</td>{% endif %}
    </tr>{% endfor %}</tbody>
  </table>
  </div>
  {% if can_manage_registry and w.is_active %}
  <form method="post" action="{% url 'labor_registry:wage' w.pk %}" class="pj-filters" aria-label="Nuevo jornal">{% csrf_token %}{{ wage_form.as_div }}<button class="button" type="submit">Registrar cambio de jornal</button></form>
  {% endif %}
</section>
<section class="pj-panel">
  <h2>Asignaciones</h2>
  {% if sheet.assignments %}<ul>{% for a in sheet.assignments %}<li>{{ a.project.name }}: {{ a.starts_on|date:"d/m/Y" }} – {% if a.ends_on %}{{ a.ends_on|date:"d/m/Y" }}{% else %}sin fin{% endif %}</li>{% endfor %}</ul>{% else %}<p class="pj-empty">Sin asignaciones.</p>{% endif %}
</section>
{% if can_manage_registry %}
<section class="pj-panel">
  <h2>Acciones</h2>
  <p class="ctr-actions"><a class="text-link" href="{% url 'labor_registry:edit' w.pk %}">Editar datos</a></p>
  {% if w.is_active %}
  <form method="post" action="{% url 'labor_registry:retire' w.pk %}" class="pj-filters" aria-label="Retiro">{% csrf_token %}{{ retire_form.as_div }}<button class="button" type="submit">Retirar</button></form>
  {% else %}
  <form method="post" action="{% url 'labor_registry:retire' w.pk %}">{% csrf_token %}<button class="text-link" type="submit" name="accion" value="reincorporar">Deshacer el retiro</button></form>
  {% endif %}
  <form method="post" action="{% url 'labor_registry:delete' w.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar (solo sin tareo)</button></form>
</section>
{% endif %}
{% endwith %}
{% endblock %}
```

`server/suno/core/templates/suno/labor/workers_import.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Importar trabajadores{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'labor_registry:list' %}">Trabajadores</a></p><h1>Importar desde Excel</h1></div></section>
<section class="pj-panel">
  <p><a href="{% url 'labor_registry:template' %}">Descarga la plantilla</a>: hoja «Trabajadores» con Tipo de documento, Número de documento, Nombre, Cargo, Fecha de ingreso, Jornal y Jornal desde (opcional). Un documento nuevo se registra; uno existente actualiza nombre y cargo y, si el jornal cambió, agrega un jornal desde «Jornal desde» (sin ella, desde hoy).</p>
  <form method="post" enctype="multipart/form-data" class="pj-form">
    {% csrf_token %}
    <label>Archivo .xlsx <input type="file" name="file" accept=".xlsx" required></label>
    {% if errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
    <button class="button" type="submit">Ver vista previa</button>
  </form>
</section>
{% if result %}
<section class="pj-panel">
  <h2>Vista previa</h2>
  {% if result.errors %}<div class="pj-late" role="alert"><p>No se puede cargar el archivo:</p><ul>{% for e in result.errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
  {% if result.rows %}
  <p>{% for action, n in counts %}{{ n }} {{ action }}{% if not forloop.last %} · {% endif %}{% endfor %}</p>
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Fila</th><th>Documento</th><th>Nombre</th><th>Cargo</th><th>Ingreso</th><th class="num">Jornal</th><th>Qué pasa</th></tr></thead>
    <tbody>{% for row in result.rows %}<tr><td>{{ row.line }}</td><td>{{ row.data.document_type }} ***{{ row.data.document_number|slice:"-4:" }}</td><td class="pj-name">{{ row.data.full_name }}</td><td class="pj-wrap">{{ row.data.position|default:"—" }}</td><td>{{ row.data.hired_on|date:"d/m/Y" }}</td><td class="num">{{ row.wage|money }}</td><td>{{ row.action }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% endif %}
  {% if result.token %}
  <form method="post" action="{% url 'labor_registry:import_apply' %}">{% csrf_token %}<input type="hidden" name="token" value="{{ result.token }}"><button class="button" type="submit">Cargar al registro</button></form>
  {% endif %}
</section>
{% endif %}
{% endblock %}
```

(El número de documento en la vista previa se enmascara: los últimos cuatro caracteres del número limpio de la fila.)

`server/suno/core/templates/suno/labor/factors.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Factor prestacional{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label">Mano de obra</p><h1>Factor prestacional</h1><p>Prestaciones sociales, seguridad social, parafiscales y dotación sobre el jornal básico. Rige desde su fecha; un cambio es una fila nueva.</p></div></section>
{% include "suno/labor/_registry_nav.html" with sub="factors" %}
<section class="pj-panel">
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Desde</th><th class="num">Factor</th><th>Composición</th><th>Uso</th>{% if can_manage_registry %}<th>Corregir</th>{% endif %}</tr></thead>
    <tbody>{% for f, used in rows %}<tr>
      <td>{{ f.valid_from|date:"d/m/Y" }}</td><td class="num">{{ f.factor|floatformat:"4g" }}</td><td class="pj-wrap">{{ f.note|default:"—" }}</td>
      <td>{% if used %}Usado en planillas aprobadas{% else %}<span class="pj-muted">Sin usar</span>{% endif %}</td>
      {% if can_manage_registry %}<td>{% if not used %}<form method="post" action="{% url 'labor_registry:factor_update' f.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="valid_from" value="{{ f.valid_from|date:'Y-m-d' }}"><input name="factor" inputmode="decimal" value="{{ f.factor }}" size="7" aria-label="Factor corregido"><input name="note" value="{{ f.note }}" maxlength="500" aria-label="Composición"><button class="text-link" type="submit">Corregir</button><button class="text-link" type="submit" name="accion" value="borrar">Borrar</button></form>{% endif %}</td>{% endif %}
    </tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-warning">No hay factor prestacional: ninguna planilla se puede aprobar sin él.</p>{% endif %}
  {% if can_manage_registry %}
  <form method="post" class="pj-filters" aria-label="Nuevo factor">{% csrf_token %}{{ form.as_div }}<button class="button" type="submit">Registrar factor</button></form>
  {% endif %}
</section>
{% endblock %}
```

(`floatformat:"4g"` pinta "1,5200" con la configuración regional del proyecto; la prueba busca ese texto.)

- [ ] **Step 7: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_registry.py suno/core/tests/test_access_web.py`
Expected: PASS.

- [ ] **Step 8: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/labor suno/core --config-file pyproject.toml"
git add server/config/urls.py server/suno/core/navigation.py server/suno/core/templates server/suno/labor
git commit -m "feat(labor): organization worker registry, wages, benefit factor and worker import pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Pestaña "Tareo": cabecera, asignaciones, lista y creación de planillas

**Files:**
- Create: `server/suno/labor/views.py`, `server/suno/labor/urls.py`
- Create: `server/suno/core/templates/suno/labor/{_nav,timesheets,assignments}.html`
- Modify: `server/suno/labor/forms.py`, `server/config/urls.py`, `server/suno/projects/views.py`, `server/suno/core/templates/suno/projects/_header.html`
- Test: `server/suno/labor/tests/test_views.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_views.py`:

```python
"""Pestaña "Tareo" (spec 3.2 y 3.5): quién la ve, asignaciones y planillas."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.test import TestCase

from suno.core.access import AccessScope, SectionRole
from suno.core.models import Project, ProjectWorker, Timesheet
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, bare_timesheet, grant_labor, labor_world, make_worker

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class TabTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.base = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/"

    def test_who_sees_the_tab(self) -> None:
        sheet = bare_timesheet(self.lw)
        for user, status in (
            (self.lw.world.admin, 200),
            (self.lw.viewer, 200),
            (self.lw.accountant, 404),  # Costos sin Mano de obra
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.base).status_code, status)
                self.assertEqual(self.client.get(f"{self.base}{sheet.pk}/").status_code, status)
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(f"/plataforma/proyectos/{self.lw.project.pk}/costos/"),
                            f'href="{self.base}"', html=False)  # fmt: skip
        self.client.force_login(self.lw.accountant)
        self.assertNotContains(
            self.client.get(f"/plataforma/proyectos/{self.lw.project.pk}/costos/"), "Tareo"
        )

    def test_ids_of_other_projects_or_organizations_are_404(self) -> None:
        other = Project.objects.create(org=self.lw.world.org, name="Otra", created_by=self.lw.world.admin)
        sheet = bare_timesheet(self.lw, project=other)
        foreign = labor_world("Ajena")
        foreign_sheet = bare_timesheet(foreign)
        self.client.force_login(self.lw.world.admin)
        for url in (f"{self.base}{sheet.pk}/", f"{self.base}{foreign_sheet.pk}/",
                    f"/plataforma/proyectos/{foreign.project.pk}/tareo/"):  # fmt: skip
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_project_scope_limits_the_tab(self) -> None:
        other = Project.objects.create(org=self.lw.world.org, name="Otra", created_by=self.lw.world.admin)
        user = self.lw.cw.contributor
        grant_labor(self.lw.world, user, SectionRole.CONTRIBUTOR, scope=AccessScope.PROJECTS,
                    projects=[self.lw.project])  # fmt: skip
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(f"/plataforma/proyectos/{other.pk}/tareo/").status_code, 404)

    def test_assignments_show_masked_documents_and_are_created_here(self) -> None:
        luis = make_worker(self.lw.world.org, "70111222", "Luis Torres", hired=date(2026, 2, 1))
        self.client.force_login(self.lw.contributor)
        page = self.client.get(f"{self.base}asignaciones/")
        self.assertContains(page, "CC ***5678")
        self.assertNotContains(page, "1012345678")
        self.assertNotContains(page, "80.000")  # sin jornales en la pestaña
        created = self.client.post(f"{self.base}asignaciones/", {
            "worker": str(luis.pk), "starts_on": "2026-02-01", "ends_on": "",
        })  # fmt: skip
        self.assertRedirects(created, f"{self.base}asignaciones/")
        self.assertTrue(ProjectWorker.objects.filter(worker=luis, project=self.lw.project).exists())
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.post(f"{self.base}asignaciones/", {
            "worker": str(luis.pk), "starts_on": "2026-04-01"}).status_code, 403)  # fmt: skip

    def test_list_and_create(self) -> None:
        self.client.force_login(self.lw.contributor)
        listed = self.client.get(self.base)
        self.assertContains(listed, 'value="2026-04-06"', html=False)  # semana anterior
        created = self.client.post(f"{self.base}nueva/", {"starts_on": "2026-04-06",
                                                         "ends_on": "2026-04-12"})  # fmt: skip
        sheet = Timesheet.objects.get(project=self.lw.project)
        self.assertRedirects(created, f"{self.base}{sheet.pk}/")
        again = self.client.post(f"{self.base}nueva/", {"starts_on": "2026-04-06",
                                                       "ends_on": "2026-04-12"}, follow=True)  # fmt: skip
        self.assertContains(again, "Se cruza con la planilla TA-001")
        self.assertContains(self.client.get(self.base), "TA-001")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views.py`
Expected: FAIL (404: la URL no existe).

- [ ] **Step 3: Formularios, vistas, URL y cabecera**

Al final de `server/suno/labor/forms.py` (con `from collections.abc import Sequence` y `from suno.core.models import Worker` en los imports):

```python
class AssignmentForm(forms.Form):
    worker = forms.ChoiceField(label="Trabajador")
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta (opcional)", required=False, widget=date_input())

    def __init__(self, *args: Any, workers: Sequence[Worker], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["worker"].choices = [("", "Elige un trabajador"),
                                         *((str(w.pk), w.label) for w in workers)]  # fmt: skip


class PeriodForm(forms.Form):
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta", widget=date_input())
```

(con `from typing import Any`).

`server/suno/labor/views.py`:

```python
"""Pestaña "Tareo" del proyecto (costos, pieza 6d, spec 3.2): asignaciones y planillas, y las
piezas que comparten las vistas de la planilla (`views_timesheets`).

Quien no lee Mano de obra en el proyecto recibe 404 (`access.get_labor_project`); quien la lee
pero no tiene la capacidad de una acción, 403. Las reglas viven en los servicios."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project
from suno.core.policies import (
    can_approve_labor,
    can_create_labor,
    can_export_labor,
    can_reverse_labor,
    can_update_labor,
)
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from . import assignments, timesheets
from .access import get_labor_project
from .assignments import AssignmentData, get_assignment
from .common import LaborRejected
from .forms import AssignmentForm, PeriodForm
from .queries import active_workers, assignment_rows, pending_count, timesheet_page

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def tab_context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_labor": can_create_labor(ctx, project),
        "can_approve_labor": can_approve_labor(ctx, project),
        "can_reverse_labor": can_reverse_labor(ctx, project),
        "can_export_labor": can_export_labor(ctx, project),
        "labor_pending": pending_count(ctx, project),
    }


def page(
    request: HttpRequest, ctx: RequestContext, project: Project, template: str, **context: Any
) -> HttpResponse:
    return _render(request, ctx, template, {**tab_context(ctx, project), **context})


def run(request: HttpRequest, action: Callable[[], Any], success: str) -> bool:
    try:
        action()
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


def require_export(ctx: RequestContext, project: Project) -> None:
    """Antes de armar un Excel o un PDF: sin permiso de exportar, 403 sin leer los datos."""
    if not can_export_labor(ctx, project):
        raise PermissionDenied


def download(
    ctx: RequestContext,
    project: Project,
    content: bytes,
    *,
    stem: str,
    suffix: str,
    content_type: str,
    action: str,
    target: str,
    **metadata: Any,
) -> HttpResponse:
    """Un PDF o un Excel auditado."""
    require_export(ctx, project)
    with transaction.atomic():
        audit.record(ctx, action=action, target=target,
                     metadata={"project": str(project.pk), **metadata})  # fmt: skip
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = content_disposition(stem, suffix, fallback=stem)
    return response


@login_required(login_url="platform_login")
@require_GET
def timesheet_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    start, end = timesheets.default_period(timezone.localdate())
    return page(request, ctx, project, "suno/labor/timesheets.html",
                page=timesheet_page(ctx, project, page=request.GET.get("pagina")),
                form=PeriodForm(initial={"starts_on": start, "ends_on": end}))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_create(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    if not can_create_labor(ctx, project):
        raise PermissionDenied
    form = PeriodForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Escribe las fechas de la planilla.")
        return redirect("labor:timesheets", project.pk)
    created: list[Any] = []
    if run(request, lambda: created.append(timesheets.create_timesheet(
            ctx, project, form.cleaned_data["starts_on"], form.cleaned_data["ends_on"])),
           "Planilla creada: llena la cuadrícula y guárdala."):  # fmt: skip
        return redirect("labor:timesheet", project.pk, created[0].pk)
    return redirect("labor:timesheets", project.pk)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def assignment_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    form = AssignmentForm(request.POST or None, workers=list(active_workers(ctx)))
    if request.method == "POST":
        if not can_create_labor(ctx, project):
            raise PermissionDenied
        if form.is_valid() and run(request, lambda: assignments.create_assignment(
                ctx, project, AssignmentData(UUID(form.cleaned_data["worker"]),
                                             form.cleaned_data["starts_on"],
                                             form.cleaned_data.get("ends_on"))),
                "Trabajador asignado."):  # fmt: skip
            return redirect("labor:assignments", project.pk)
    rows = list(assignment_rows(ctx, project))
    return page(request, ctx, project, "suno/labor/assignments.html", form=form,
                rows=[(row, can_update_labor(ctx, row)) for row in rows])  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def assignment_update(request: HttpRequest, project_id: UUID, assignment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    row = get_assignment(ctx, project, assignment_id)
    if request.POST.get("accion") == "borrar":
        run(request, lambda: assignments.delete_assignment(ctx, project, row.pk),
            "Asignación borrada.")  # fmt: skip
    else:
        starts = _parse_date(request.POST.get("starts_on"))
        ends = _parse_date(request.POST.get("ends_on"))  # vacío: sin fin
        if starts is None:
            messages.error(request, "Escribe desde cuándo.")
        else:
            data = AssignmentData(row.worker_id, starts, ends)
            run(request, lambda: assignments.update_assignment(ctx, project, row.pk, data),
                "Asignación actualizada.")  # fmt: skip
    return redirect("labor:assignments", project.pk)
```

`server/suno/labor/urls.py` (la Task 14 y la 15 completan las rutas de la planilla):

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/tareo/` (config/urls.py)."""

from django.urls import path

from . import views, views_timesheets

app_name = "labor"

T = "<uuid:timesheet_id>/"

urlpatterns = [
    path("", views.timesheet_list, name="timesheets"),
    path("nueva/", views.timesheet_create, name="new_timesheet"),
    path("asignaciones/", views.assignment_list, name="assignments"),
    path("asignaciones/<uuid:assignment_id>/", views.assignment_update, name="assignment"),
    path(T, views_timesheets.timesheet_detail, name="timesheet"),
]
```

`server/suno/labor/views_timesheets.py` (mínima en esta tarea; la Task 14 la reemplaza):

```python
"""Una planilla de tareo: cuadrícula, decisión, documentos e importación (spec 3.2 a 3.4)."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET

from suno.projects.views import _ctx

from .access import get_labor_project, get_timesheet
from .views import page


@login_required(login_url="platform_login")
@require_GET
def timesheet_detail(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    return page(request, ctx, project, "suno/labor/timesheet_detail.html", sheet=sheet)
```

y una plantilla provisional `server/suno/core/templates/suno/labor/timesheet_detail.html`, que la Task 14 reemplaza entera:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{{ sheet.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="labor" %}
{% include "suno/labor/_nav.html" with sub="timesheets" %}
<section class="pj-panel">
  <h2>Planilla {{ sheet.code }} <span class="bg-label ta-{{ sheet.status }}">{{ sheet.get_status_display }}</span></h2>
  <p>{{ sheet.starts_on|date:"d/m/Y" }} – {{ sheet.ends_on|date:"d/m/Y" }} · {{ sheet.days|qty }} jornales · costo estimado {{ sheet.total|money }}</p>
</section>
{% endblock %}
```

En `server/config/urls.py`, después del bloque de `materiales/` del proyecto:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/tareo/",
        include("suno.labor.urls"),
    ),
```

En `server/suno/projects/views.py`, `_project_context` agrega (con `can_read_labor` en el import de políticas):

```python
        # Pestaña "Tareo" (costos, pieza 6d): Mano de obra en ese proyecto.
        "can_view_labor": can_read_labor(ctx, project),
```

En `server/suno/core/templates/suno/projects/_header.html`, después de la pestaña "Materiales":

```html
      {% if can_view_labor %}<a href="{% url 'labor:timesheets' project.pk %}" {% if tab == "labor" %}aria-current="page"{% endif %}>Tareo</a>{% endif %}
```

- [ ] **Step 4: Plantillas**

`server/suno/core/templates/suno/labor/_nav.html`:

```html
<nav class="pj-tabs ta-nav" aria-label="Tareo">
  <a href="{% url 'labor:timesheets' project.pk %}" {% if sub == "timesheets" %}aria-current="page"{% endif %}>Planillas{% if labor_pending %} <span class="bg-label ta-pending">{{ labor_pending }} por aprobar</span>{% endif %}</a>
  <a href="{% url 'labor:assignments' project.pk %}" {% if sub == "assignments" %}aria-current="page"{% endif %}>Asignaciones</a>
  {% if navigation.LABOR_REGISTRY %}<a href="{% url 'labor_registry:list' %}">Trabajadores</a>{% endif %}
</nav>
```

`server/suno/core/templates/suno/labor/timesheets.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Tareo · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="labor" %}
{% include "suno/labor/_nav.html" with sub="timesheets" %}
{% if can_create_labor %}
<section class="pj-panel">
  <h2>Nueva planilla</h2>
  <form method="post" action="{% url 'labor:new_timesheet' project.pk %}" class="pj-filters" aria-label="Periodo de la planilla">
    {% csrf_token %}
    <label>Desde {{ form.starts_on }}</label>
    <label>Hasta {{ form.ends_on }}</label>
    <button class="button" type="submit">Crear planilla</button>
  </form>
  <p class="pj-muted">Hasta 31 días, nunca en el futuro. Trae a los trabajadores asignados en el periodo.</p>
</section>
{% endif %}
<section class="pj-panel">
  <h2>Planillas</h2>
  {% if page.object_list %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Planilla</th><th>Periodo</th><th class="num">Jornales</th><th class="num">Costo estimado</th><th>Estado</th><th>Firmada</th></tr></thead>
    <tbody>{% for t in page.object_list %}<tr>
      <td><a href="{% url 'labor:timesheet' project.pk t.pk %}">{{ t.code }}</a></td>
      <td>{{ t.starts_on|date:"d/m/Y" }} – {{ t.ends_on|date:"d/m/Y" }}</td>
      <td class="num">{{ t.days|qty }}</td><td class="num">{{ t.total|money }}</td>
      <td><span class="bg-label ta-{{ t.status }}">{{ t.get_status_display }}</span></td>
      <td>{% if t.attachment_id %}Sí{% else %}<span class="pj-muted">No</span>{% endif %}</td>
    </tr>{% endfor %}</tbody>
  </table>
  </div>
  {% if page.has_other_pages %}<p class="pj-pages">{% if page.has_previous %}<a href="?pagina={{ page.previous_page_number }}">Anterior</a>{% endif %} Página {{ page.number }} de {{ page.paginator.num_pages }} {% if page.has_next %}<a href="?pagina={{ page.next_page_number }}">Siguiente</a>{% endif %}</p>{% endif %}
  {% else %}<p class="pj-empty">Todavía no hay planillas.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/labor/assignments.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Asignaciones · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="labor" %}
{% include "suno/labor/_nav.html" with sub="assignments" %}
{% if can_create_labor %}
<section class="pj-panel">
  <h2>Asignar un trabajador</h2>
  <form method="post" class="pj-filters" aria-label="Nueva asignación">{% csrf_token %}{{ form.as_div }}<button class="button" type="submit">Asignar</button></form>
  {% if not form.fields.worker.choices|length > 1 %}<p class="pj-muted">No hay trabajadores activos en el registro.</p>{% endif %}
</section>
{% endif %}
<section class="pj-panel">
  <h2>Quién trabaja en el proyecto</h2>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Trabajador</th><th>Cargo</th><th>Desde</th><th>Hasta</th><th>Cambiar</th></tr></thead>
    <tbody>{% for a, editable in rows %}<tr>
      <td class="pj-name">{{ a.worker.label }}</td><td class="pj-wrap">{{ a.worker.position|default:"—" }}</td>
      <td>{{ a.starts_on|date:"d/m/Y" }}</td><td>{% if a.ends_on %}{{ a.ends_on|date:"d/m/Y" }}{% else %}sin fin{% endif %}</td>
      <td>{% if editable %}<form method="post" action="{% url 'labor:assignment' project.pk a.pk %}" class="cst-reject">{% csrf_token %}<input type="date" name="starts_on" value="{{ a.starts_on|date:'Y-m-d' }}" aria-label="Desde"><input type="date" name="ends_on" value="{{ a.ends_on|date:'Y-m-d' }}" aria-label="Hasta"><button class="text-link" type="submit">Guardar</button><button class="text-link" type="submit" name="accion" value="borrar">Borrar</button></form>{% endif %}</td>
    </tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">Nadie asignado todavía.</p>{% endif %}
</section>
{% endblock %}
```

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views.py suno/costs/tests/test_views.py suno/materials/tests/test_views.py`
Expected: PASS (la cabecera gana la pestaña "Tareo" solo para quien lee Mano de obra).

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/labor suno/projects --config-file pyproject.toml"
git add server/config/urls.py server/suno/projects/views.py server/suno/core/templates server/suno/labor
git commit -m "feat(labor): project tareo tab with assignments, timesheet list and creation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 14: Cuadrícula semanal y vista por día en la web

**Files:**
- Modify: `server/suno/labor/forms.py`, `server/suno/labor/views_timesheets.py`, `server/suno/labor/urls.py`
- Create: `server/suno/core/templates/suno/labor/{timesheet_detail,_grid,_day}.html`, `server/suno/core/static/suno/js/tareo.js`
- Modify: `server/suno/core/static/suno/css/projects.css`, `server/suno/core/templates/suno/projects/base.html` (`?v=20261118-labor-v1`), `server/config/settings/base.py`
- Test: `server/suno/labor/tests/test_views_grid.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_views_grid.py`:

```python
"""Cuadrícula semanal y vista por día en la web (spec 3.2 "Cuadrícula semanal"; decisiones 11 y
12 del plan): guardar la semana o el día, repetir el día anterior y partir una fila sin guardar."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.conf import settings
from django.test import TestCase

from suno.core.models import TimesheetLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.labor import timesheets

from .factories import TODAY, WEEK, labor_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MON, TUE = WEEK[0], date(2026, 3, 3)


@skip_without_db
class GridPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        self.sheet = timesheets.create_timesheet(self.lw.ctx(self.lw.contributor), self.lw.project,
                                                 *WEEK)  # fmt: skip
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"
        self.client.force_login(self.lw.contributor)
        page = self.client.get(self.url)
        self.rows = {row["row"].worker.split(" · ")[0]: row["index"]
                     for row in page.context["inputs"]}  # fmt: skip

    def form(self, **cells: str) -> dict[str, Any]:
        """Los campos de la cuadrícula tal como los envía el navegador (todas las celdas)."""
        data: dict[str, Any] = {"semana": "2026-03-02", "filas": "2"}
        for name, index in self.rows.items():
            worker = self.lw.pedro if name == "Pedro Pérez" else self.lw.maria
            data[f"f{index}-trabajador"] = str(worker.pk)
            data[f"f{index}-destino"] = ""
            data[f"f{index}-tarea"] = ""
            for n in range(7):
                data[f"f{index}-d{(MON.toordinal() + n)}"] = ""
        data.update(cells)
        return data

    def cell(self, worker: str, day: date) -> str:
        return f"f{self.rows[worker]}-d{day.toordinal()}"

    def test_the_grid_lists_assigned_workers_with_masked_documents(self) -> None:
        page = self.client.get(self.url)
        self.assertContains(page, "Pedro Pérez · CC ***5678")
        self.assertNotContains(page, "1012345678")
        self.assertContains(page, 'class="ta-day', html=False)  # la vista por día está
        self.assertContains(page, "suno/js/tareo.js")
        self.assertEqual(settings.DATA_UPLOAD_MAX_NUMBER_FIELDS, 4000)

    def test_save_the_week(self) -> None:
        data = self.form(**{self.cell("Pedro Pérez", MON): "1", self.cell("María Gómez", TUE): "0,5",
                            "accion": "guardar"})  # fmt: skip
        self.assertRedirects(self.client.post(self.url + "guardar/", data),
                             self.url + "?semana=2026-03-02")  # fmt: skip
        saved = dict(TimesheetLine.objects.values_list("worker__full_name", "fraction"))
        self.assertEqual(saved, {"Pedro Pérez": D("1.0000"), "María Gómez": D("0.5000")})
        page = self.client.get(self.url)
        self.assertContains(page, "197.600,00")  # 121.600 + 76.000

    def test_repeat_and_split_redraw_without_saving(self) -> None:
        data = self.form(**{self.cell("Pedro Pérez", MON): "1",
                            "accion": f"repetir-{TUE.isoformat()}"})  # fmt: skip
        page = self.client.post(self.url + "guardar/", data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'name="{self.cell("Pedro Pérez", TUE)}" value="1"', html=False)
        self.assertFalse(TimesheetLine.objects.exists())
        split = self.client.post(self.url + "guardar/", {**data, "accion": f"partir-{self.rows['Pedro Pérez']}"})
        self.assertEqual(len(split.context["inputs"]), 3)
        self.assertFalse(TimesheetLine.objects.exists())

    def test_bad_cells_keep_what_was_typed(self) -> None:
        data = self.form(**{self.cell("Pedro Pérez", MON): "abc", "accion": "guardar"})
        page = self.client.post(self.url + "guardar/", data)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "no es un número")
        self.assertContains(page, 'value="abc"', html=False)
        self.assertFalse(TimesheetLine.objects.exists())

    def test_day_view_saves_only_that_day(self) -> None:
        self.client.post(self.url + "guardar/", self.form(**{
            self.cell("Pedro Pérez", MON): "1", self.cell("Pedro Pérez", TUE): "1",
            "accion": "guardar"}))  # fmt: skip
        day = {"semana": "2026-03-02", "dia": TUE.isoformat(), "filas": "2", "accion": "guardar-dia"}
        for name, index in self.rows.items():
            worker = self.lw.pedro if name == "Pedro Pérez" else self.lw.maria
            day[f"f{index}-trabajador"] = str(worker.pk)
            day[f"f{index}-destino"] = ""
            day[f"f{index}-tarea"] = ""
            day[f"f{index}-d{TUE.toordinal()}"] = "0,5" if name == "Pedro Pérez" else ""
        self.client.post(self.url + "guardar/", day)
        saved = dict(TimesheetLine.objects.values_list("worked_on", "fraction"))
        self.assertEqual(saved, {MON: D("1.0000"), TUE: D("0.5000")})

    def test_viewer_cannot_save(self) -> None:
        self.client.force_login(self.lw.viewer)
        self.assertEqual(
            self.client.post(self.url + "guardar/", self.form(accion="guardar")).status_code, 403
        )
```

Los nombres de las celdas usan el ordinal del día (`f0-d739677`): es estable, corto y no lleva guiones que confundan con el número de fila.

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_grid.py`
Expected: FAIL (`KeyError: 'inputs'` en el contexto y 404 en `guardar/`).

- [ ] **Step 3: Leer la cuadrícula enviada**

Al final de `server/suno/labor/forms.py` (con `from dataclasses import dataclass, replace`, `from datetime import date`, `from uuid import UUID`, `from collections.abc import Mapping`, `from django.http import QueryDict`, `from suno.budgets.totals import parse_number`, `from suno.costs.catalog import Catalog` y `from .timesheets import LineData, read_destination`):

```python
#: Filas que acepta un formulario de la cuadrícula (decisión 11 del plan).
MAX_GRID_ROWS = 250


@dataclass(frozen=True)
class PostedRow:
    worker_id: UUID
    destination: str
    task: str
    #: Lo escrito en cada celda, por día ("" si vacía).
    cells: Mapping[date, str]


def cell_name(index: int, day: date) -> str:
    return f"f{index}-d{day.toordinal()}"


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def read_rows(post: QueryDict, days: Sequence[date]) -> list[PostedRow]:
    """Las filas enviadas; una fila sin trabajador válido se ignora."""
    try:
        count = min(int(post.get("filas") or 0), MAX_GRID_ROWS)
    except ValueError:
        count = 0
    rows: list[PostedRow] = []
    for index in range(count):
        worker = _uuid(post.get(f"f{index}-trabajador"))
        if worker is None:
            continue
        rows.append(PostedRow(
            worker,
            (post.get(f"f{index}-destino") or "")[:500],
            (post.get(f"f{index}-tarea") or "")[:20],
            {day: (post.get(cell_name(index, day)) or "").strip()[:12] for day in days},
        ))  # fmt: skip
    return rows


def to_lines(
    rows: Sequence[PostedRow],
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    names: Mapping[UUID, str],
) -> tuple[list[LineData], list[str]]:
    """Las líneas de las celdas escritas; "0" o vacío es no trabajó."""
    lines: list[LineData] = []
    errors: list[str] = []
    for row in rows:
        written = {day: text for day, text in row.cells.items() if text}
        if not written:
            continue
        who = names.get(row.worker_id, "—")
        destination, error = read_destination(row.destination, row.task, catalog, outlines)
        if destination is None:
            errors.append(f"{who}: {error}")
            continue
        for day, text in sorted(written.items()):
            try:
                value = parse_number(text)
            except ValueError:
                errors.append(f"{who}, {day:%d/%m/%Y}: «{text}» no es un número.")
                continue
            if value:
                lines.append(LineData(row.worker_id, day, destination, value))
    return lines, errors


def repeat_previous(rows: Sequence[PostedRow], day: date, days: Sequence[date]) -> list[PostedRow]:
    """ "Repetir el día anterior": copia en `day` lo de la columna de la izquierda."""
    if day not in days or days.index(day) == 0:
        return list(rows)
    before = days[days.index(day) - 1]
    return [replace(row, cells={**row.cells, day: row.cells.get(before, "")}) for row in rows]


def split_row(rows: Sequence[PostedRow], index: int, days: Sequence[date]) -> list[PostedRow]:
    """ "Partir la fila": otra fila del mismo trabajador, debajo, para repartir el día entre
    destinos."""
    if not 0 <= index < len(rows):
        return list(rows)
    copy = PostedRow(rows[index].worker_id, "", "", {day: "" for day in days})
    return [*rows[: index + 1], copy, *rows[index + 1 :]]
```

- [ ] **Step 4: Vistas de la cuadrícula**

`server/suno/labor/views_timesheets.py` queda (las vistas de decisión, documentos e importación llegan en la Task 15):

```python
"""Una planilla de tareo: cuadrícula semanal y vista por día (spec 3.2), decisión, documentos e
importación (Task 15)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from suno.core.context import RequestContext
from suno.core.models import Project, Timesheet
from suno.core.policies import can_delete_labor, can_update_labor
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.destination import destination_options
from suno.projects.views import _ctx, _parse_date

from . import timesheets
from .access import get_labor_project, get_timesheet
from .calc import NO_DAYS
from .common import OPEN_STATES
from .forms import PostedRow, cell_name, read_rows, repeat_previous, split_row, to_lines
from .grid import Grid, GridRow, build_grid, week_of
from .views import page, run


def _editable(ctx: RequestContext, sheet: Timesheet) -> bool:
    return (
        sheet.status in OPEN_STATES
        and can_update_labor(ctx, sheet)
        and (sheet.status == "draft" or sheet.created_by_id == ctx.user_id)
    )


def _posted_grid_rows(
    rows: Sequence[PostedRow], names: dict[UUID, str], days: Sequence[date]
) -> list[GridRow]:
    return [
        GridRow(row.worker_id, names.get(row.worker_id, "—"), "", row.destination, row.task, "",
                tuple(row.cells.get(day, "") for day in days), NO_DAYS, None)
        for row in rows
    ]  # fmt: skip


def _render_sheet(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    sheet: Timesheet,
    *,
    week_start: date | None,
    day: date | None,
    posted: Sequence[PostedRow] | None = None,
    catalog: Catalog | None = None,
) -> HttpResponse:
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    built: Grid = build_grid(ctx, project, sheet, catalog, tasks, week_start)
    days = built.week.days
    names = {row.worker_id: row.worker for row in built.rows}
    rows = list(built.rows) if posted is None else _posted_grid_rows(posted, names, days)
    chosen = day if day in days else days[0]
    position = days.index(chosen)
    url = reverse("labor:timesheet", args=[project.pk, sheet.pk])
    return page(
        request, ctx, project, "suno/labor/timesheet_detail.html",
        sheet=sheet, grid=built, editable=_editable(ctx, sheet), unsaved=posted is not None,
        inputs=[{"index": i, "row": row,
                 "cells": [(cell_name(i, d), v) for d, v in zip(days, row.cells, strict=True)]}
                for i, row in enumerate(rows)],
        day=chosen,
        day_inputs=[{"index": i, "row": row, "name": cell_name(i, chosen),
                     "value": row.cells[position]} for i, row in enumerate(rows)],
        destinations=destination_options(catalog), tasks=list(tasks.values()),
        can_delete=sheet.status in OPEN_STATES and can_delete_labor(ctx, sheet),
        urls={"save": url + "guardar/", "self": url},
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def timesheet_detail(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id, "created_by", "updated_by", "decided_by",
                          "reversed_by")  # fmt: skip
    return _render_sheet(request, ctx, project, sheet,
                         week_start=_parse_date(request.GET.get("semana")),
                         day=_parse_date(request.GET.get("dia")))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_save(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    """Guardar la semana (`guardar`) o el día (`guardar-dia`); `repetir-AAAA-MM-DD` y
    `partir-<fila>` vuelven a pintar sin guardar; `recalcular` guarda el resumen otra vez."""
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    action = request.POST.get("accion", "guardar")
    week = week_of(sheet, _parse_date(request.POST.get("semana")))
    back = f"{reverse('labor:timesheet', args=[project.pk, sheet.pk])}?semana={week.start}"
    if action == "recalcular":
        run(request, lambda: timesheets.recalculate(ctx, project, sheet.pk),
            "Resumen recalculado: vuelve a imprimir la planilla y a cargarla firmada.")  # fmt: skip
        return redirect(back)
    day = _parse_date(request.POST.get("dia"))
    by_day = action == "guardar-dia" and day is not None and day in week.days
    days = [day] if by_day and day is not None else list(week.days)
    rows = read_rows(request.POST, days)
    if action.startswith("repetir-"):
        target = _parse_date(action.removeprefix("repetir-"))
        rows = repeat_previous(rows, target, week.days) if target else rows
        return _render_sheet(request, ctx, project, sheet, week_start=week.start, day=day, posted=rows)
    if action.startswith("partir-"):
        try:
            index = int(action.removeprefix("partir-"))
        except ValueError:
            index = -1
        rows = split_row(rows, index, week.days)
        return _render_sheet(request, ctx, project, sheet, week_start=week.start, day=day, posted=rows)
    catalog = load_catalog(ctx, project)
    outlines = timesheets.outline_index(assignable_tasks(ctx, project))
    names = timesheets.worker_names(ctx, {row.worker_id for row in rows})
    lines, errors = to_lines(rows, catalog, outlines, names)
    if not errors and run(request, lambda: timesheets.save_lines(ctx, project, sheet.pk, lines,
                                                                 days, catalog=catalog),
                          "Día guardado." if by_day else "Semana guardada."):  # fmt: skip
        return redirect(f"{back}&dia={day}" if by_day else back)
    for error in errors[:5]:
        messages.error(request, error)
    if by_day:
        return redirect(f"{back}&dia={day}")
    sheet.refresh_from_db()
    return _render_sheet(request, ctx, project, sheet, week_start=week.start, day=day,
                         posted=rows, catalog=catalog)  # fmt: skip
```

`run` deja los mensajes del servicio en el `messages` de la petición; al volver a pintar sin redirigir (errores de la semana), la plantilla los muestra igual porque `base.html` recorre `messages`.

En `server/suno/labor/urls.py` agregar `path(T + "guardar/", views_timesheets.timesheet_save, name="save_timesheet"),`.

- [ ] **Step 5: El límite de campos**

En `server/config/settings/base.py`, después de `DATA_UPLOAD_MAX_MEMORY_SIZE`:

```python
# La cuadrícula del tareo (costos, pieza 6d, decisión 11 del plan) envía una semana completa:
# 60 trabajadores × 3 destinos × 7 días son 1.260 celdas más 720 campos de fila. El valor de
# Django (1.000) la rechazaría con un 400; 4.000 deja margen y sigue acotando la petición.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 4000
```

- [ ] **Step 6: Plantillas, JavaScript opcional y estilos**

`server/suno/core/templates/suno/labor/timesheet_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load static money_tags %}
{% block title %}{{ sheet.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="labor" %}
{% include "suno/labor/_nav.html" with sub="timesheets" %}
<section class="pj-panel">
  <h2>Planilla {{ sheet.code }} <span class="bg-label ta-{{ sheet.status }}">{{ sheet.get_status_display }}</span></h2>
  <p>{{ sheet.starts_on|date:"d/m/Y" }} – {{ sheet.ends_on|date:"d/m/Y" }} · {{ grid.days|qty }} jornales · costo estimado {{ grid.total|money }}</p>
  {% if sheet.status == "rejected" %}<p class="pj-warning">Rechazada: {{ sheet.decision_note }}</p>{% endif %}
  {% if sheet.status == "reversed" %}<p class="pj-warning">Anulada: {{ sheet.reverse_note }}</p>{% endif %}
  {% if grid.changed %}<p class="pj-warning">Cambió un jornal o el factor desde que se guardó: recalcula el resumen y vuelve a imprimir la planilla antes de aprobarla.</p>{% endif %}
  {% if grid.problems %}<div class="pj-late" role="alert"><p>No se puede aprobar todavía:</p><ul>{% for p in grid.problems|slice:":10" %}<li>{{ p }}</li>{% endfor %}</ul></div>{% endif %}
  {% block decision %}{% endblock %}
</section>
<section class="pj-panel">
  <h2>Semana del {{ grid.week.days.0|date:"d/m" }} al {{ grid.week.days|last|date:"d/m/Y" }}</h2>
  <p class="ctr-actions">{% if grid.week.previous %}<a class="text-link" href="?semana={{ grid.week.previous|date:'Y-m-d' }}">← Semana anterior</a>{% endif %} {% if grid.week.next %}<a class="text-link" href="?semana={{ grid.week.next|date:'Y-m-d' }}">Semana siguiente →</a>{% endif %}</p>
  {% if unsaved %}<p class="pj-warning">Sin guardar: revisa y guarda la semana.</p>{% endif %}
  {% include "suno/labor/_grid.html" %}
  {% include "suno/labor/_day.html" %}
  <datalist id="ta-destinations">{% for d in destinations %}<option value="{{ d }}">{% endfor %}</datalist>
  <datalist id="ta-tasks">{% for t in tasks %}<option value="{{ t.outline_number }}">{{ t.name }}</option>{% endfor %}</datalist>
</section>
<section class="pj-panel ta-totals">
  <h2>Totales del periodo</h2>
  <div class="pj-scroll">
  <table class="pj-table ta-table"><caption>Por persona</caption>
    <thead><tr><th>Trabajador</th><th class="num">Jornales</th><th class="num">Costo estimado</th></tr></thead>
    <tbody>{% for label, days, amount in grid.by_worker %}<tr><td class="pj-name">{{ label }}</td><td class="num">{{ days|qty }}</td><td class="num">{{ amount|money }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  <div class="pj-scroll">
  <table class="pj-table ta-table"><caption>Por destino</caption>
    <thead><tr><th>Destino</th><th class="num">Jornales</th><th class="num">Costo estimado</th></tr></thead>
    <tbody>{% for label, days, amount in grid.by_destination %}<tr><td class="pj-name">{{ label }}</td><td class="num">{{ days|qty }}</td><td class="num">{{ amount|money }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
</section>
{% endblock %}
{% block scripts %}<script src="{% static 'suno/js/tareo.js' %}" defer></script>{% endblock %}
```

`server/suno/core/templates/suno/labor/_grid.html`:

```html
<form method="post" action="{{ urls.save }}" class="ta-week" aria-label="Cuadrícula de la semana">
  <!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
  {% csrf_token %}
  <input type="hidden" name="semana" value="{{ grid.week.start|date:'Y-m-d' }}">
  <input type="hidden" name="filas" value="{{ inputs|length }}">
  <div class="pj-scroll">
  <table class="pj-table ta-grid" data-ta-grid>
    <thead><tr>
      <th scope="col">Trabajador</th><th scope="col">Destino</th><th scope="col">Tarea</th>
      {% for day in grid.week.days %}<th scope="col" class="num">{{ day|date:"D j" }}{% if editable and not forloop.first %}<br><button class="text-link ta-repeat" type="submit" name="accion" value="repetir-{{ day|date:'Y-m-d' }}" title="Copiar aquí el día anterior">Repetir</button>{% endif %}</th>{% endfor %}
      <th scope="col" class="num">Jornales</th>{% if editable %}<th scope="col"><span class="sr-only">Partir</span></th>{% endif %}
    </tr></thead>
    <tbody>
    {% for item in inputs %}
      <tr data-ta-row>
        <th scope="row" class="pj-name">{{ item.row.worker }}<input type="hidden" name="f{{ item.index }}-trabajador" value="{{ item.row.worker_id }}"></th>
        <td><input class="ta-destination" name="f{{ item.index }}-destino" value="{{ item.row.destination }}" list="ta-destinations" placeholder="Indirecto" autocomplete="off" aria-label="Destino de {{ item.row.worker }}"{% if not editable %} disabled{% endif %}></td>
        <td><input class="ta-task" name="f{{ item.index }}-tarea" value="{{ item.row.task }}" list="ta-tasks" autocomplete="off" aria-label="Tarea de {{ item.row.worker }}"{% if not editable %} disabled{% endif %}></td>
        {% for name, value in item.cells %}<td class="num"><input class="ta-cell" data-ta-cell="{{ forloop.counter0 }}" name="{{ name }}" value="{{ value }}" inputmode="decimal" maxlength="6" aria-label="Fracción de {{ item.row.worker }}, día {{ forloop.counter }}"{% if not editable %} disabled{% endif %}></td>{% endfor %}
        <td class="num" data-ta-row-total>{{ item.row.days|qty }}</td>
        {% if editable %}<td><button class="text-link" type="submit" name="accion" value="partir-{{ item.index }}" title="Otra fila de esta persona para repartir el día">Partir</button></td>{% endif %}
      </tr>
    {% endfor %}
    </tbody>
    <tfoot><tr><th scope="row" colspan="3">Jornales del día</th>{% for total in grid.day_totals %}<td class="num" data-ta-day-total="{{ forloop.counter0 }}">{{ total|qty }}</td>{% endfor %}<td></td>{% if editable %}<td></td>{% endif %}</tr></tfoot>
  </table>
  </div>
  {% if editable %}<p class="ctr-actions"><button class="button" type="submit" name="accion" value="guardar">Guardar la semana</button> <button class="text-link" type="submit" name="accion" value="recalcular">Recalcular el resumen</button></p>{% endif %}
</form>
```

`server/suno/core/templates/suno/labor/_day.html`:

```html
<div class="ta-day">
  <form method="get" class="pj-filters" aria-label="Día">
    <input type="hidden" name="semana" value="{{ grid.week.start|date:'Y-m-d' }}">
    <label>Día <select name="dia">{% for d in grid.week.days %}<option value="{{ d|date:'Y-m-d' }}" {% if d == day %}selected{% endif %}>{{ d|date:"l j/m" }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Ver</button>
  </form>
  <form method="post" action="{{ urls.save }}" aria-label="Tareo del día">
    <!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    <input type="hidden" name="semana" value="{{ grid.week.start|date:'Y-m-d' }}">
    <input type="hidden" name="dia" value="{{ day|date:'Y-m-d' }}">
    <input type="hidden" name="filas" value="{{ day_inputs|length }}">
    <ul class="ta-day-list">
    {% for item in day_inputs %}
      <li>
        <strong>{{ item.row.worker }}</strong>
        <input type="hidden" name="f{{ item.index }}-trabajador" value="{{ item.row.worker_id }}">
        <label>Destino <input name="f{{ item.index }}-destino" value="{{ item.row.destination }}" list="ta-destinations" placeholder="Indirecto" autocomplete="off"{% if not editable %} disabled{% endif %}></label>
        <label>Tarea <input name="f{{ item.index }}-tarea" value="{{ item.row.task }}" list="ta-tasks" autocomplete="off"{% if not editable %} disabled{% endif %}></label>
        <label>Fracción <input name="{{ item.name }}" value="{{ item.value }}" inputmode="decimal" maxlength="6"{% if not editable %} disabled{% endif %}></label>
      </li>
    {% endfor %}
    </ul>
    {% if editable %}<button class="button" type="submit" name="accion" value="guardar-dia">Guardar el día</button>{% endif %}
  </form>
</div>
```

`server/suno/core/static/suno/js/tareo.js`:

```javascript
// Totales de jornales en vivo en la cuadrícula del tareo. Comodidad: sin este archivo la
// página funciona igual (los totales se recalculan al guardar). Sin dependencias.
(() => {
  const grid = document.querySelector('[data-ta-grid]');
  if (!grid) return;
  const parse = (text) => {
    const value = Number.parseFloat(String(text).trim().replace(',', '.'));
    return Number.isFinite(value) ? value : 0;
  };
  const show = (value) => (Math.round(value * 10000) / 10000).toString().replace('.', ',');

  function render() {
    const days = new Map();
    grid.querySelectorAll('tbody tr[data-ta-row]').forEach((row) => {
      let total = 0;
      row.querySelectorAll('[data-ta-cell]').forEach((cell) => {
        const value = parse(cell.value);
        total += value;
        const key = cell.dataset.taCell;
        days.set(key, (days.get(key) || 0) + value);
      });
      const target = row.querySelector('[data-ta-row-total]');
      if (target) target.textContent = show(total);
    });
    grid.querySelectorAll('[data-ta-day-total]').forEach((cell) => {
      const total = days.get(cell.dataset.taDayTotal) || 0;
      cell.textContent = show(total);
    });
  }

  grid.addEventListener('input', (event) => {
    if (event.target.matches('[data-ta-cell]')) render();
  });
})();
```

En `server/suno/core/static/suno/css/projects.css`, al final:

```css
/* Tareo (costos, pieza 6d) */
.ta-nav { margin: 0 0 16px; }
.ta-table td.pj-name { min-width: 200px; max-width: 320px; }
.ta-grid th.pj-name { min-width: 180px; max-width: 240px; text-align: left; font-weight: 600; overflow-wrap: anywhere; position: sticky; left: 0; background: var(--suno-white); z-index: 1; }
.ta-grid input { min-height: var(--suno-control-height); padding: 4px 6px; border: 1px solid var(--suno-line-strong); border-radius: var(--suno-radius-sm); font: inherit; }
.ta-grid .ta-destination { min-width: 180px; width: 100%; }
.ta-grid .ta-task { width: 64px; }
.ta-grid .ta-cell { width: 56px; text-align: right; }
.ta-grid .ta-repeat { font-size: 12px; }
.ta-day { display: none; }
.ta-day-list { list-style: none; margin: 12px 0; padding: 0; display: grid; gap: 12px; }
.ta-day-list li { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--suno-line); border-radius: var(--suno-radius-sm); }
.ta-day-list strong { overflow-wrap: anywhere; }
.ta-day-list input { width: 100%; min-width: 0; min-height: var(--suno-control-height); padding: 6px 8px; border: 1px solid var(--suno-line-strong); border-radius: var(--suno-radius-sm); font: inherit; }
.ta-totals .pj-scroll + .pj-scroll { margin-top: 16px; }
.bg-label.ta-draft { background: var(--suno-line-strong); color: var(--suno-ink); }
.bg-label.ta-approved { background: var(--suno-green); }
.bg-label.ta-rejected, .bg-label.ta-reversed { background: var(--suno-red); }
.bg-label.ta-pending { background: var(--suno-amber); }
@media (max-width: 700px) {
  .ta-week { display: none; }
  .ta-day { display: block; }
  .ta-table td.pj-name { min-width: 160px; max-width: 200px; }
}
```

(Variables de `tokens.css`: `--suno-white`, `--suno-line`, `--suno-line-strong`, `--suno-green`, `--suno-red`, `--suno-amber`, `--suno-ink`, `--suno-radius-sm`, `--suno-control-height`.)

En `server/suno/core/templates/suno/projects/base.html`, subir la versión de `projects.css` a `?v=20261118-labor-v1`.

- [ ] **Step 7: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_grid.py suno/labor/tests/test_views.py`
Expected: PASS.

- [ ] **Step 8: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno config && ruff check suno config && mypy suno/labor --config-file pyproject.toml"
git add server/config/settings/base.py server/suno/core/templates server/suno/core/static server/suno/labor
git commit -m "feat(labor): weekly grid and day view with repeat and split, optional live totals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Aprobar, rechazar, anular, firmar, borrar e importar en la web; PDF y Excel de la planilla

**Files:**
- Create: `server/suno/labor/pdf.py`, `server/suno/labor/export.py`, `server/suno/core/templates/suno/labor/timesheet_import.html`
- Modify: `server/suno/labor/views_timesheets.py`, `server/suno/labor/urls.py`, `server/suno/core/templates/suno/labor/timesheet_detail.html`
- Test: `server/suno/labor/tests/test_views_timesheets.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_views_timesheets.py`:

```python
"""Planilla en la web (spec 2.3, 2.4, 2.5, 3.4 y 3.5): firma, aprobación, rechazo, anulación,
borrado, importación, PDF y Excel."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.core.models import AuditEvent, CostEntry, Timesheet, TimesheetLine, Worker
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload
from suno.labor import timesheets
from suno.labor.pdf import timesheet_pdf

from .factories import TODAY, WEEK, labor_world, line_data, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@skip_without_db
class TimesheetFlowTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.lw = labor_world()
        ctx = self.lw.ctx(self.lw.contributor)
        self.sheet = timesheets.create_timesheet(ctx, self.lw.project, *WEEK)
        timesheets.save_lines(ctx, self.lw.project, self.sheet.pk,
                              [line_data(self.lw.pedro, WEEK[0])], [WEEK[0]])  # fmt: skip
        self.url = f"/plataforma/proyectos/{self.lw.project.pk}/tareo/{self.sheet.pk}/"

    def test_print_sign_approve_export_and_reverse(self) -> None:
        self.client.force_login(self.lw.contributor)
        document = self.client.get(self.url + "planilla.pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(AuditEvent.objects.filter(action="timesheet.exported").exists())
        self.client.post(self.url + "firmada/", {"file": pdf_upload()})
        self.client.force_login(self.lw.world.supervisor)
        self.assertRedirects(self.client.post(self.url + "aprobar/"), self.url)
        self.assertEqual(CostEntry.objects.filter(timesheet=self.sheet).count(), 1)
        sheet = self.client.get(self.url + "planilla.xlsx")
        self.assertEqual(sheet["Content-Type"], XLSX)
        self.assertTrue(AuditEvent.objects.filter(action="labor.exported").exists())
        self.client.force_login(self.lw.world.admin)
        self.client.post(self.url + "anular/", {"motivo": "Planilla duplicada"})
        self.assertEqual(Timesheet.objects.get(pk=self.sheet.pk).status, "reversed")
        self.assertEqual(CostEntry.objects.filter(timesheet=self.sheet).count(), 2)

    def test_viewer_cannot_print_and_reviewer_cannot_reverse(self) -> None:
        self.client.force_login(self.lw.viewer)
        self.assertEqual(self.client.get(self.url + "planilla.pdf").status_code, 403)
        self.assertEqual(self.client.get(self.url + "planilla.xlsx").status_code, 403)
        self.client.force_login(self.lw.world.supervisor)
        self.assertNotContains(self.client.get(self.url), "Anular")

    def test_reject_and_delete(self) -> None:
        self.client.force_login(self.lw.world.supervisor)
        self.client.post(self.url + "rechazar/", {"motivo": "Falta la firma del maestro"})
        self.assertEqual(Timesheet.objects.get(pk=self.sheet.pk).status, "rejected")
        self.client.force_login(self.lw.contributor)
        self.assertContains(self.client.get(self.url), "Falta la firma del maestro")
        self.assertRedirects(self.client.post(self.url + "borrar/"),
                             f"/plataforma/proyectos/{self.lw.project.pk}/tareo/")  # fmt: skip
        self.assertFalse(Timesheet.objects.filter(pk=self.sheet.pk).exists())

    def test_import_through_the_web(self) -> None:
        self.client.force_login(self.lw.contributor)
        header = ["Tipo de documento", "Número de documento", "Fecha", "Destino", "Tarea",
                  "Fracción"]  # fmt: skip
        book = xlsx("Tareo", header, [["CC", "52123456", "2026-03-03", "Indirecto", "", "1"]])
        upload = SimpleUploadedFile("tareo.xlsx", book, content_type=XLSX)
        preview = self.client.post(self.url + "importar/", {"file": upload})
        self.assertContains(preview, "María Gómez · CC ***3456")
        token = preview.context["result"].token
        self.assertRedirects(self.client.post(self.url + "importar/aplicar/", {"token": token}),
                             self.url)  # fmt: skip
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 2)
        self.assertEqual(self.client.get(
            f"/plataforma/proyectos/{self.lw.project.pk}/tareo/plantilla.xlsx").status_code, 200)

    def test_pdf_escapes_user_text(self) -> None:
        Worker.objects.filter(pk=self.lw.pedro.pk).update(full_name="<b>Pedro</b> & Cía")
        content = timesheet_pdf(self.lw.ctx(), self.lw.project, Timesheet.objects.get(pk=self.sheet.pk),
                                generated_on=TODAY)  # fmt: skip
        self.assertTrue(content.startswith(b"%PDF"))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_timesheets.py`
Expected: FAIL (`No module named 'suno.labor.pdf'`).

- [ ] **Step 3: La tabla del periodo, el PDF y el Excel**

En `server/suno/labor/grid.py`, al final:

```python
@dataclass(frozen=True)
class PeriodTable:
    """La cuadrícula del periodo completo (PDF y Excel): una fila por persona y destino."""

    days: tuple[date, ...]
    #: (trabajador, destino, celdas por día, jornales, costo)
    rows: tuple[tuple[str, str, tuple[str, ...], Decimal, Decimal], ...]
    day_totals: tuple[Decimal, ...]


def period_table(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> PeriodTable:
    """Cinco consultas (`evaluate`)."""
    evaluation = evaluate(ctx, sheet, catalog)
    days = tuple(sheet.starts_on + timedelta(days=n)
                 for n in range((sheet.ends_on - sheet.starts_on).days + 1))  # fmt: skip
    index = {day: n for n, day in enumerate(days)}
    grouped: dict[tuple[UUID, str], list[Any]] = {}
    totals = [NO_DAYS] * len(days)
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        entry = grouped.setdefault((line.worker_id, line.key), [[None] * len(days), NO_DAYS, ZERO])
        entry[0][index[line.day]] = line.fraction
        entry[1] += line.fraction
        entry[2] += cost.amount or ZERO
        totals[index[line.day]] += line.fraction
    rows = [
        (evaluation.names.get(worker, "—"), evaluation.labels.get(key, "—"),
         tuple(cell_text(v) for v in cells), days_total, amount)
        for (worker, key), (cells, days_total, amount) in grouped.items()
    ]  # fmt: skip
    rows.sort(key=lambda row: (row[0], row[1]))
    return PeriodTable(days, tuple(rows), tuple(totals))
```

`server/suno/labor/pdf.py`:

```python
"""Planilla de tareo para firmar (costos, pieza 6d, spec 3.4), con reportlab como el corte de
6c. A4 horizontal (decisión 24 del plan): encabezado del proyecto, la cuadrícula del periodo
en bloques de hasta 16 días, totales, el resumen guardado por destino (lo que se compara al
aprobar) y firmas. Documento enmascarado; todo texto de usuario se escapa."""

from __future__ import annotations

import io
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from suno.core.context import RequestContext
from suno.core.models import Project, Timesheet
from suno.core.templatetags.money_tags import money, qty
from suno.costs.catalog import load_catalog

from .grid import period_table

__all__ = ["timesheet_pdf"]

PAGE = landscape(A4)
ACCENT = colors.HexColor("#8C2633")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")
BLOCK_DAYS = 16


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def timesheet_pdf(
    ctx: RequestContext, project: Project, sheet: Timesheet, *, generated_on: date
) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("TaBody", parent=styles["BodyText"], fontName="SunoReport",
                          fontSize=7, leading=9)  # fmt: skip
    heading = ParagraphStyle("TaHeading", parent=body, fontSize=15, leading=20, spaceAfter=4,
                             textColor=ACCENT)  # fmt: skip
    section = ParagraphStyle("TaSection", parent=body, fontSize=10, leading=13, spaceBefore=8,
                             spaceAfter=4, keepWithNext=True)  # fmt: skip

    def table(rows: list[list[str]], widths: list[float], *, header: bool = True) -> Table:
        built = Table([[Paragraph(escape(c), body) for c in row] for row in rows],
                      colWidths=widths, repeatRows=int(header))  # fmt: skip
        style: list[Any] = [("GRID", (0, 0), (-1, -1), 0.4, GRID),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]  # fmt: skip
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), HEAD))
        built.setStyle(TableStyle(style))
        return built

    def grid_table(rows: list[list[str]], widths: list[float]) -> Table:
        """La cuadrícula: nombre y destino como `Paragraph` escapado (texto de usuario que se
        parte en líneas); las cifras como texto plano, que reportlab no interpreta y pinta mucho
        más rápido (60 trabajadores × 3 destinos × 31 días, en menos de 1 s)."""
        built = Table(
            [[Paragraph(escape(row[0]), body), Paragraph(escape(row[1]), body), *row[2:]]
             for row in rows],
            colWidths=widths, repeatRows=1,
        )  # fmt: skip
        built.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), HEAD),
            ("FONTNAME", (2, 0), (-1, -1), "SunoReport"),
            ("FONTSIZE", (2, 0), (-1, -1), 7),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ]))  # fmt: skip
        return built

    width = PAGE[0] - 72
    data = period_table(ctx, sheet, load_catalog(ctx, project))
    summary = sheet.summary or {}
    fingerprint = summary.get("fingerprint", {})
    story: list[Any] = [
        Paragraph(f"Planilla de tareo {sheet.code}", heading),
        table([["Proyecto", project.name],
               ["Periodo", f"{sheet.starts_on:%d/%m/%Y} – {sheet.ends_on:%d/%m/%Y}"],
               ["Líneas y jornales", f"{fingerprint.get('lines', 0)} líneas · "
                                     f"{qty(fingerprint.get('days', '0'))} jornales"]],
              [width * 0.2, width * 0.8], header=False),
    ]  # fmt: skip
    for start in range(0, len(data.days), BLOCK_DAYS):
        days = data.days[start : start + BLOCK_DAYS]
        fixed = width * 0.36
        cell = (width - fixed - width * 0.08) / len(days)
        story.append(Paragraph(f"Días {days[0]:%d/%m} a {days[-1]:%d/%m}", section))
        story.append(grid_table(
            [["Trabajador", "Destino", *[f"{d:%a %d}" for d in days], "Jornales"],
             *[[worker, label, *cells[start : start + BLOCK_DAYS], qty(total)]
               for worker, label, cells, total, _amount in data.rows],
             ["Total del día", "", *[qty(t) for t in data.day_totals[start : start + BLOCK_DAYS]],
              qty(sum(data.day_totals[start : start + BLOCK_DAYS]))]],
            [width * 0.18, width * 0.18, *[cell] * len(days), width * 0.08],
        ))  # fmt: skip
    story.append(Paragraph("Resumen por destino (es lo que se compara al aprobar)", section))
    story.append(table(
        [["Destino", "Jornales", "Costo estimado"],
         *[[g["label"], qty(g["days"]), money(g["total"])] for g in summary.get("groups", [])],
         ["Total", qty(fingerprint.get("days", "0")), money(fingerprint.get("total", "0"))]],
        [width * 0.6, width * 0.15, width * 0.25],
    ))  # fmt: skip
    story += [
        Spacer(1, 28),
        table([["", "", ""], ["Maestro de obra", "Residente", "Aprobó"]], [width / 3] * 3,
              header=False),
        Spacer(1, 6),
        Paragraph(f"Costo estimado = fracción × jornal × factor prestacional. Generado por SUNO "
                  f"el {generated_on:%d/%m/%Y}.", body),
    ]  # fmt: skip

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {project.name} · {sheet.code}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=PAGE, leftMargin=36, rightMargin=36, topMargin=30,
                      bottomMargin=36, title=f"Planilla {sheet.code}", author="SUNO",
                      ).build(story, onFirstPage=footer, onLaterPages=footer)  # fmt: skip
    return buffer.getvalue()
```

(`drawString` del pie no interpreta marcado: el nombre del proyecto va tal cual. Los `Paragraph` sí, por eso todo pasa por `escape`.)

`server/suno/labor/export.py`:

```python
"""Excel de mano de obra (costos, pieza 6d, spec 3.4): planilla y conciliación. Todo texto de
usuario pasa por `safe_cell`; valores como número y fechas en ISO; documento enmascarado."""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx
from suno.core.context import RequestContext
from suno.core.models import Timesheet, TimesheetLine
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog

from .payroll import Reconciliation
from .timesheets import evaluate

TIMESHEET_HEADER = [
    "trabajador", "cargo", "fecha", "destino", "fraccion", "jornal", "factor", "valor",
]  # fmt: skip
RECONCILIATION_HEADER = [
    "trabajador", "cargo", "jornales", "costo_tareado", "pagado", "diferencia", "porcentaje",
    "marca",
]  # fmt: skip


def number(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def timesheet_workbook(ctx: RequestContext, sheet: Timesheet, catalog: Catalog) -> bytes:
    """Una fila por línea. En una aprobada o anulada, los valores congelados; en un borrador,
    el costo estimado de hoy."""
    evaluation = evaluate(ctx, sheet, catalog)
    frozen = {
        pk: (wage, factor, amount)
        for pk, wage, factor, amount in scoped_qs(ctx, TimesheetLine)
        .filter(timesheet=sheet, amount__isnull=False)
        .values_list("pk", "daily_wage", "factor", "amount")
    }
    positions = dict(
        scoped_qs(ctx, TimesheetLine).filter(timesheet=sheet)
        .values_list("worker_id", "worker__position").distinct()
    )  # fmt: skip
    rows = []
    for line, cost in zip(evaluation.lines, evaluation.costs, strict=True):
        wage, factor, amount = frozen.get(line.pk, (cost.wage, cost.factor, cost.amount))
        rows.append([
            safe_cell(evaluation.names.get(line.worker_id, "—")),
            safe_cell(positions.get(line.worker_id, "")),
            line.day.isoformat(),
            safe_cell(evaluation.labels.get(line.key, "—")),
            number(line.fraction), number(wage), number(factor), number(amount),
        ])  # fmt: skip
    return to_xlsx(TIMESHEET_HEADER, rows, sheet_name=sheet.code)


def reconciliation_workbook(result: Reconciliation) -> bytes:
    rows: list[list[object]] = [
        [safe_cell(r.label), safe_cell(r.position), number(r.days), number(r.estimated),
         number(r.paid), number(r.difference), number(r.pct), r.flag]
        for r in result.rows
    ]  # fmt: skip
    rows += [
        [safe_cell(f"Fuera del registro: {u.document} (fila {u.row})"), "", "", "", number(u.paid),
         "", "", "Documento desconocido"]
        for u in result.unknown
    ]  # fmt: skip
    return to_xlsx(RECONCILIATION_HEADER, rows, sheet_name="Conciliación")
```

- [ ] **Step 4: Vistas**

En `server/suno/labor/views_timesheets.py`, el bloque de imports queda:

```python
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project, Timesheet
from suno.core.policies import (
    can_approve_labor,
    can_delete_labor,
    can_reverse_labor,
    can_update_labor,
)
from suno.core.scoping import blob_registry
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.materials.destination import destination_options
from suno.materials.views import download as materials_download
from suno.projects.views import _ctx, _parse_date

from . import approval, timesheet_import, timesheets
from .access import get_labor_project, get_timesheet
from .calc import NO_DAYS
from .common import OPEN_STATES, LaborRejected
from .export import timesheet_workbook
from .forms import PostedRow, cell_name, read_rows, repeat_previous, split_row, to_lines
from .grid import Grid, GridRow, build_grid, week_of
from .pdf import timesheet_pdf
from .views import XLSX_TYPE, download, page, require_export, run
```

y se agregan estas vistas:

```python
def _detail(project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    return redirect("labor:timesheet", project_id, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_delete(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    if run(request, lambda: timesheets.delete_timesheet(ctx, project, timesheet_id),
           "Planilla borrada."):  # fmt: skip
        return redirect("labor:timesheets", project.pk)
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_signed(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    upload = request.FILES.get("file")
    if upload is not None:
        name = upload.name or "planilla-firmada.pdf"
        run(request, lambda: timesheets.attach_signed(ctx, project, timesheet_id, upload, name),
            "Planilla firmada adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_signed_file(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    """La planilla firmada, por URL prefirmada, después de comprobar que se lee la pestaña (el
    `Blob` no tiene organización), como `materials.common.attachment_url`."""
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    blob = blob_registry().filter(pk=sheet.attachment_id).first() if sheet.attachment_id else None
    if blob is None:
        raise Http404
    url = get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )
    return materials_download(url)


@login_required(login_url="platform_login")
@require_POST
def timesheet_approve(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    run(request, lambda: approval.approve_timesheet(ctx, project, timesheet_id),
        "Planilla aprobada: su costo de mano de obra está en el libro.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_reject(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: approval.reject_timesheet(ctx, project, timesheet_id, note),
        "Planilla rechazada; su autor la verá con el motivo.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_POST
def timesheet_reverse(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: approval.reverse_timesheet(ctx, project, timesheet_id, note),
        "Planilla anulada: sus reversos están en el libro. Para corregir, crea otra con el mismo "
        "periodo.")  # fmt: skip
    return _detail(project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_document(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    require_export(ctx, project)
    sheet = get_timesheet(ctx, project, timesheet_id)
    content = timesheet_pdf(ctx, project, sheet, generated_on=timezone.localdate())
    return download(ctx, project, content, stem=f"planilla-{sheet.code}", suffix=".pdf",
                    content_type="application/pdf", action=AuditAction.TIMESHEET_EXPORTED,
                    target=f"timesheet:{sheet.pk}", number=sheet.number, status=sheet.status)  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def timesheet_sheet(request: HttpRequest, project_id: UUID, timesheet_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    require_export(ctx, project)
    sheet = get_timesheet(ctx, project, timesheet_id)
    content = timesheet_workbook(ctx, sheet, load_catalog(ctx, project))
    return download(ctx, project, content, stem=f"planilla-{sheet.code}", suffix=".xlsx",
                    content_type=XLSX_TYPE, action=AuditAction.LABOR_EXPORTED,
                    target=f"timesheet:{sheet.pk}", what="planilla")  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def timesheet_import_upload(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    sheet = get_timesheet(ctx, project, timesheet_id)
    if not can_update_labor(ctx, sheet):
        raise PermissionDenied
    errors: list[str] = []
    result = None
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            errors = ["Elige un archivo .xlsx."]
        elif upload.size is not None and upload.size > MAX_BYTES:
            errors = ["El archivo supera 20 MB."]
        else:
            try:
                check_file_name(upload.name or "")
                result = timesheet_import.preview(ctx, project, sheet, upload.read())
            except ExcelRejected as exc:
                errors = [str(exc)]
            except LaborRejected as exc:
                errors = exc.errors
    return page(request, ctx, project, "suno/labor/timesheet_import.html", sheet=sheet,
                errors=errors, result=result)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def timesheet_import_apply(
    request: HttpRequest, project_id: UUID, timesheet_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_labor_project(ctx, project_id)
    added: list[int] = []
    if run(request, lambda: added.append(timesheet_import.apply(
            ctx, project, timesheet_id, request.POST.get("token", ""))), "Tareo importado."):  # fmt: skip
        return _detail(project.pk, timesheet_id)
    return redirect("labor:import_timesheet", project.pk, timesheet_id)


@login_required(login_url="platform_login")
@require_GET
def timesheet_import_template(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    get_labor_project(ctx, project_id)
    response = HttpResponse(timesheet_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-tareo", ".xlsx", fallback="plantilla-tareo"
    )
    return response
```

`materials_download` es `from suno.materials.views import download as materials_download` (arma el `302` a la URL prefirmada; `local://` en desarrollo). `XLSX_TYPE`, `download`, `require_export` vienen de `.views`.

En `_render_sheet`, agregar al contexto los permisos y URL de la decisión:

```python
        can_attach=sheet.status == "draft" and can_update_labor(ctx, sheet),
        can_decide=sheet.status == "draft" and can_approve_labor(ctx, project)
        and ctx.user_id not in (sheet.created_by_id, sheet.updated_by_id),
        can_reverse=sheet.status == "approved" and can_reverse_labor(ctx, project),
        needs="" if sheet.attachment_id else "Falta la planilla firmada",
```

y en `urls`: `"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/", "upload": url + "firmada/", "view": url + "firmada/ver/", "pdf": url + "planilla.pdf", "xlsx": url + "planilla.xlsx", "import": url + "importar/", "delete": url + "borrar/"`.

En `server/suno/labor/urls.py`, después de la de `guardar/`:

```python
    path(T + "borrar/", views_timesheets.timesheet_delete, name="delete_timesheet"),
    path(T + "firmada/", views_timesheets.timesheet_signed, name="timesheet_signed"),
    path(T + "firmada/ver/", views_timesheets.timesheet_signed_file, name="timesheet_file"),
    path(T + "aprobar/", views_timesheets.timesheet_approve, name="approve_timesheet"),
    path(T + "rechazar/", views_timesheets.timesheet_reject, name="reject_timesheet"),
    path(T + "anular/", views_timesheets.timesheet_reverse, name="reverse_timesheet"),
    path(T + "planilla.pdf", views_timesheets.timesheet_document, name="timesheet_pdf"),
    path(T + "planilla.xlsx", views_timesheets.timesheet_sheet, name="timesheet_xlsx"),
    path(T + "importar/", views_timesheets.timesheet_import_upload, name="import_timesheet"),
    path(T + "importar/aplicar/", views_timesheets.timesheet_import_apply,
         name="import_timesheet_apply"),
    path("plantilla.xlsx", views_timesheets.timesheet_import_template, name="template"),
```

- [ ] **Step 5: Plantillas**

En `timesheet_detail.html`, reemplazar `{% block decision %}{% endblock %}` por:

```html
  <p class="ctr-actions">
    {% if can_export_labor %}<a class="button" href="{{ urls.pdf }}">Planilla para firmar (PDF)</a> <a class="text-link" href="{{ urls.xlsx }}">Excel</a>{% endif %}
    {% if editable %}<a class="text-link" href="{{ urls.import }}">Importar Excel</a>{% endif %}
  </p>{# nosemgrep: generic.html-templates.security.var-in-href.var-in-href -- href hecho en el servidor con reverse() (ruta de la app), nunca texto del usuario #}
  {% include "suno/materials/_support.html" with doc=sheet label="Planilla firmada" view=urls.view upload=urls.upload %}
  {% include "suno/materials/_decide.html" with approve=urls.approve reject=urls.reject void=urls.void can_void=can_reverse back="" %}
  {% if can_delete %}<form method="post" action="{{ urls.delete }}">{% csrf_token %}<button class="text-link" type="submit">Borrar la planilla</button></form>{% endif %}
  <p class="pj-muted">Registró {{ sheet.created_by }}{% if sheet.updated_by_id != sheet.created_by_id %} · modificó {{ sheet.updated_by }}{% endif %}{% if sheet.decided_by %} · {{ sheet.get_status_display|lower }} por {{ sheet.decided_by }} el {{ sheet.decided_at|date:"d/m/Y" }}{% endif %}{% if sheet.reversed_by %} · anuló {{ sheet.reversed_by }}{% endif %}</p>
```

(Los dos `include` de 6c reciben `can_attach`, `can_decide` y `needs` del contexto. `_decide.html` muestra "Anular" con `can_void`.)

`server/suno/core/templates/suno/labor/timesheet_import.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Importar tareo · {{ sheet.code }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="labor" %}
<section class="pj-panel">
  <h2>Importar tareo a {{ sheet.code }} ({{ sheet.starts_on|date:"d/m/Y" }} – {{ sheet.ends_on|date:"d/m/Y" }})</h2>
  <p><a href="{% url 'labor:template' project.pk %}">Descarga la plantilla</a>: hoja «Tareo» con Tipo de documento, Número de documento, Fecha, Destino («Indirecto», «2.1», «Capítulo 2» o «2.1, 2.2»), Tarea (número de esquema, opcional) y Fracción. Se agrega a lo que ya tiene la planilla; una línea igual se suma.</p>
  <form method="post" enctype="multipart/form-data" class="pj-form">
    {% csrf_token %}
    <label>Archivo .xlsx <input type="file" name="file" accept=".xlsx" required></label>
    {% if errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
    <button class="button" type="submit">Ver vista previa</button>
  </form>
</section>
{% if result %}
<section class="pj-panel">
  <h2>Vista previa</h2>
  {% if result.errors %}<div class="pj-late" role="alert"><p>No se puede cargar el archivo:</p><ul>{% for e in result.errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
  {% if result.rows %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Fila</th><th>Trabajador</th><th>Fecha</th><th>Destino</th><th>Tarea</th><th class="num">Fracción</th></tr></thead>
    <tbody>{% for r in result.rows %}<tr><td>{{ r.line }}</td><td class="pj-name">{{ r.worker }}</td><td>{{ r.day|date:"d/m/Y" }}</td><td class="pj-wrap">{{ r.destination|default:"Indirecto" }}</td><td>{{ r.task|default:"—" }}</td><td class="num">{{ r.fraction|qty }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% endif %}
  {% if result.token %}<form method="post" action="{% url 'labor:import_timesheet_apply' project.pk sheet.pk %}">{% csrf_token %}<input type="hidden" name="token" value="{{ result.token }}"><button class="button" type="submit">Agregar a la planilla</button></form>{% endif %}
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_timesheets.py suno/labor/tests/test_views_grid.py`
Expected: PASS.

- [ ] **Step 7: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/labor --config-file pyproject.toml"
git add server/suno/core/templates server/suno/labor
git commit -m "feat(labor): timesheet decisions, signed support, import, printable PDF and Excel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Conciliación de nómina en la web (organización)

**Files:**
- Create: `server/suno/labor/reconciliation_views.py`, `server/suno/core/templates/suno/labor/{payrolls,reconciliation}.html`
- Modify: `server/suno/labor/registry_urls.py`, `server/suno/labor/forms.py`, `server/suno/core/templates/suno/labor/_registry_nav.html`
- Test: `server/suno/labor/tests/test_views_reconciliation.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_views_reconciliation.py`:

```python
"""Conciliación de nómina en la web (spec 2.6 y 3.3): importar, ver, filtrar por proyecto,
exportar; cada consulta queda en la auditoría."""

from __future__ import annotations

import unittest
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.core.models import AuditEvent, PayrollImport
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import TODAY, WEEK, bare_line, bare_timesheet, labor_world, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/trabajadores/conciliacion/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@skip_without_db
class ReconciliationPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        sheet = bare_timesheet(self.lw, status="approved")
        bare_line(sheet, self.lw.pedro, WEEK[0], frozen=("80000.00", "1.5200", "121600.00"))

    def upload(self) -> PayrollImport:
        book = xlsx("Nómina", ["Tipo de documento", "Número de documento", "Total pagado"],
                    [["CC", "1012345678", "130000"]])  # fmt: skip
        preview = self.client.post(BASE + "importar/", {
            "starts_on": "2026-03-01", "ends_on": "2026-03-31",
            "file": SimpleUploadedFile("nomina.xlsx", book, content_type=XLSX),
        })  # fmt: skip
        self.assertContains(preview, "1 de 1")  # documentos del registro
        token = preview.context["result"].token
        self.client.post(BASE + "importar/aplicar/", {"token": token})
        return PayrollImport.objects.get()

    def test_import_view_filter_and_export(self) -> None:
        self.client.force_login(self.lw.world.admin)
        payroll = self.upload()
        page = self.client.get(f"{BASE}{payroll.pk}/")
        self.assertContains(page, "Pedro Pérez · CC ***5678")
        self.assertContains(page, "8.400,00")  # 130.000 − 121.600
        self.assertEqual(AuditEvent.objects.filter(action="labor_reconciliation.viewed").count(), 1)
        filtered = self.client.get(f"{BASE}{payroll.pk}/?proyecto={self.lw.project.pk}")
        self.assertEqual(filtered.status_code, 200)
        export = self.client.get(f"{BASE}{payroll.pk}/conciliacion.xlsx")
        self.assertEqual(export["Content-Type"], XLSX)
        self.assertIn("conciliacion-2026-03-01-2026-03-31", export["Content-Disposition"])
        self.assertTrue(AuditEvent.objects.filter(action="labor.exported").exists())

    def test_permissions(self) -> None:
        self.client.force_login(self.lw.world.admin)
        payroll = self.upload()
        self.client.force_login(self.lw.viewer)  # lee el registro, no importa ni exporta
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/conciliacion.xlsx").status_code, 403)
        self.assertEqual(self.client.post(BASE + "importar/", {}).status_code, 403)
        self.client.force_login(self.lw.accountant)
        self.assertEqual(self.client.get(BASE).status_code, 404)
        other = labor_world("Ajena")
        self.client.force_login(other.world.admin)
        self.assertEqual(self.client.get(f"{BASE}{payroll.pk}/").status_code, 404)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_reconciliation.py`
Expected: FAIL (404: la URL no existe).

- [ ] **Step 3: Formulario y vistas**

Al final de `server/suno/labor/forms.py`:

```python
class PayrollForm(forms.Form):
    starts_on = forms.DateField(label="Desde", widget=date_input())
    ends_on = forms.DateField(label="Hasta", widget=date_input())
```

`server/suno/labor/reconciliation_views.py`:

```python
"""Conciliación de nómina (costos, pieza 6d, spec 2.6 y 3.3): importar la nómina del periodo,
ver la tabla por trabajador (de la organización o de un proyecto) y exportarla. Ver pide
`can_read_labor_registry` (404); importar, `can_manage_labor_registry` (403); exportar,
`can_export_labor_registry` (403). Cada consulta y cada exporte quedan en la auditoría."""

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project
from suno.core.policies import can_export_labor_registry
from suno.core.scoping import scoped_qs

from . import payroll
from .access import get_payroll, registry_ctx, registry_manager
from .common import LaborRejected
from .export import reconciliation_workbook
from .forms import PayrollForm
from .queries import payroll_rows
from .registry_views import XLSX_TYPE, page


@login_required(login_url="platform_login")
@require_GET
def payroll_list(request: HttpRequest) -> HttpResponse:
    ctx = registry_ctx(request)
    return page(request, ctx, "suno/labor/payrolls.html", rows=payroll_rows(ctx),
                form=PayrollForm(), result=None, errors=[])  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def payroll_upload(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    form = PayrollForm(request.POST)
    upload = request.FILES.get("file")
    errors: list[str] = []
    result = None
    if not form.is_valid():
        errors = ["Escribe el periodo de la nómina."]
    elif upload is None:
        errors = ["Elige un archivo .xlsx."]
    elif upload.size is not None and upload.size > MAX_BYTES:
        errors = ["El archivo supera 20 MB."]
    else:
        try:
            check_file_name(upload.name or "")
            result = payroll.preview(ctx, upload.read(), form.cleaned_data["starts_on"],
                                     form.cleaned_data["ends_on"], upload.name or "nomina.xlsx")  # fmt: skip
        except ExcelRejected as exc:
            errors = [str(exc)]
        except LaborRejected as exc:
            errors = exc.errors
    return page(request, ctx, "suno/labor/payrolls.html", rows=payroll_rows(ctx), form=form,
                result=result, errors=errors)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def payroll_apply(request: HttpRequest) -> HttpResponse:
    ctx = registry_manager(request)
    try:
        imported = payroll.apply(ctx, request.POST.get("token", ""))
    except LaborRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("labor_registry:payrolls")
    messages.success(request, "Nómina importada.")
    return redirect("labor_registry:reconciliation", imported.pk)


def _project(ctx: RequestContext, value: str | None) -> Project | None:
    try:
        wanted = UUID(value) if value else None
    except ValueError:
        return None
    if wanted is None:
        return None
    return scoped_qs(ctx, Project).filter(pk=wanted).first()


@login_required(login_url="platform_login")
@require_GET
def reconciliation(request: HttpRequest, payroll_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    imported = get_payroll(ctx, payroll_id)
    project = _project(ctx, request.GET.get("proyecto"))
    result = payroll.reconcile(ctx, imported, project)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.LABOR_RECONCILIATION_VIEWED,
            target=f"payroll_import:{imported.pk}",
            metadata={"project": str(project.pk) if project else "", "rows": len(result.rows)},
        )
    projects = scoped_qs(ctx, Project).order_by("name").values_list("pk", "name")
    return page(request, ctx, "suno/labor/reconciliation.html", result=result,
                projects=list(projects), project=project)  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def reconciliation_sheet(request: HttpRequest, payroll_id: UUID) -> HttpResponse:
    ctx = registry_ctx(request)
    if not can_export_labor_registry(ctx, ctx.org_id):
        raise PermissionDenied
    imported = get_payroll(ctx, payroll_id)
    project = _project(ctx, request.GET.get("proyecto"))
    result = payroll.reconcile(ctx, imported, project)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.LABOR_EXPORTED,
            target=f"payroll_import:{imported.pk}",
            metadata={"what": "conciliacion", "project": str(project.pk) if project else "",
                      "rows": len(result.rows)},
        )  # fmt: skip
    stem = f"conciliacion-{imported.starts_on}-{imported.ends_on}"
    response = HttpResponse(reconciliation_workbook(result), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(stem, ".xlsx", fallback="conciliacion")
    return response


@login_required(login_url="platform_login")
@require_GET
def payroll_template(request: HttpRequest) -> HttpResponse:
    registry_manager(request)
    response = HttpResponse(payroll.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-nomina", ".xlsx", fallback="plantilla-nomina"
    )
    return response
```

En `server/suno/labor/registry_urls.py`, al final de `urlpatterns`:

```python
    path("conciliacion/", reconciliation_views.payroll_list, name="payrolls"),
    path("conciliacion/importar/", reconciliation_views.payroll_upload, name="payroll_upload"),
    path("conciliacion/importar/aplicar/", reconciliation_views.payroll_apply,
         name="payroll_apply"),
    path("conciliacion/plantilla.xlsx", reconciliation_views.payroll_template,
         name="payroll_template"),
    path("conciliacion/<uuid:payroll_id>/", reconciliation_views.reconciliation,
         name="reconciliation"),
    path("conciliacion/<uuid:payroll_id>/conciliacion.xlsx",
         reconciliation_views.reconciliation_sheet, name="reconciliation_xlsx"),
```

(con `from . import reconciliation_views, registry_views`).

En `_registry_nav.html`, agregar el tercer enlace:

```html
  <a href="{% url 'labor_registry:payrolls' %}" {% if sub == "payroll" %}aria-current="page"{% endif %}>Conciliación de nómina</a>
```

- [ ] **Step 4: Plantillas**

`server/suno/core/templates/suno/labor/payrolls.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Conciliación de nómina{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label">Mano de obra</p><h1>Conciliación de nómina</h1><p>Compara lo tareado (costo estimado) con lo que pagó la nómina. SUNO no liquida nómina ni ajusta el libro con esto.</p></div></section>
{% include "suno/labor/_registry_nav.html" with sub="payroll" %}
{% if can_manage_registry %}
<section class="pj-panel">
  <h2>Importar la nómina de un periodo</h2>
  <p><a href="{% url 'labor_registry:payroll_template' %}">Descarga la plantilla</a>: hoja «Nómina» con Tipo de documento, Número de documento y Total pagado (devengado, aportes y provisiones del trabajador en el periodo). SUNO guarda las líneas, no el archivo.</p>
  <form method="post" action="{% url 'labor_registry:payroll_upload' %}" enctype="multipart/form-data" class="pj-filters">
    {% csrf_token %}
    <label>Desde {{ form.starts_on }}</label><label>Hasta {{ form.ends_on }}</label>
    <label>Archivo .xlsx <input type="file" name="file" accept=".xlsx" required></label>
    <button class="button" type="submit">Ver vista previa</button>
  </form>
  {% if errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
  {% if result %}
    {% if result.errors %}<div class="pj-late" role="alert"><p>No se puede cargar el archivo:</p><ul>{% for e in result.errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
    {% if result.rows %}<p>{{ result.rows|length }} líneas por {{ result.total|money }} · {{ result.known }} de {{ result.rows|length }} documentos están en el registro.</p>{% endif %}
    {% if result.token %}<form method="post" action="{% url 'labor_registry:payroll_apply' %}">{% csrf_token %}<input type="hidden" name="token" value="{{ result.token }}"><button class="button" type="submit">Importar la nómina</button></form>{% endif %}
  {% endif %}
</section>
{% endif %}
<section class="pj-panel">
  <h2>Nóminas importadas</h2>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Periodo</th><th>Archivo</th><th class="num">Líneas</th><th class="num">Total pagado</th><th>Importó</th></tr></thead>
    <tbody>{% for p in rows %}<tr><td><a href="{% url 'labor_registry:reconciliation' p.pk %}">{{ p.starts_on|date:"d/m/Y" }} – {{ p.ends_on|date:"d/m/Y" }}</a></td><td class="pj-wrap">{{ p.file_name }}</td><td class="num">{{ p.line_count }}</td><td class="num">{{ p.total|money }}</td><td class="pj-wrap">{{ p.imported_by }} · {{ p.imported_at|date:"d/m/Y" }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">Todavía no hay nóminas importadas.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/labor/reconciliation.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Conciliación {{ result.payroll.starts_on|date:"d/m/Y" }}{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'labor_registry:payrolls' %}">Conciliación de nómina</a></p><h1>{{ result.payroll.starts_on|date:"d/m/Y" }} – {{ result.payroll.ends_on|date:"d/m/Y" }}</h1><p>Tareado: planillas aprobadas del periodo. Diferencia = pagado − tareado.</p></div></section>
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Proyecto">
    <label>Proyecto <select name="proyecto"><option value="">Toda la organización</option>{% for pk, name in projects %}<option value="{{ pk }}" {% if project and project.pk == pk %}selected{% endif %}>{{ name }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Ver</button>
    {% if can_export_registry %}<a class="text-link" href="{% url 'labor_registry:reconciliation_xlsx' result.payroll.pk %}{% if project %}?proyecto={{ project.pk }}{% endif %}">Excel</a>{% endif %}
  </form>
  {% if project %}<p class="pj-muted">Lo pagado se prorratea por los jornales tareados en el proyecto.{% if result.excluded %} {{ result.excluded }} pagado{{ result.excluded|pluralize }} sin tareo en ningún proyecto no se puede{{ result.excluded|pluralize:"n" }} prorratear: está{{ result.excluded|pluralize:"n" }} en la vista de toda la organización.{% endif %}</p>{% endif %}
  <p>{{ result.days|qty }} jornales · tareado {{ result.estimated|money }} · pagado {{ result.paid|money }} · <strong>diferencia {{ result.difference|signed }}</strong></p>
  {% if result.rows %}
  <div class="pj-scroll">
  <table class="pj-table ta-table">
    <thead><tr><th>Trabajador</th><th>Cargo</th><th class="num">Jornales</th><th class="num">Tareado</th><th class="num">Pagado</th><th class="num">Diferencia</th><th class="num">%</th><th>Marca</th></tr></thead>
    <tbody>{% for r in result.rows %}<tr>
      <td class="pj-name">{{ r.label }}</td><td class="pj-wrap">{{ r.position|default:"—" }}</td>
      <td class="num">{{ r.days|qty }}</td><td class="num">{{ r.estimated|money }}</td>
      <td class="num">{% if r.paid is not None %}{{ r.paid|money }}{% else %}—{% endif %}</td>
      <td class="num">{{ r.difference|signed }}</td><td class="num">{% if r.pct is not None %}{{ r.pct }}{% else %}—{% endif %}</td>
      <td>{% if r.flag %}<span class="pj-late">{{ r.flag }}</span>{% endif %}</td>
    </tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay tareo aprobado ni pagos en el periodo.</p>{% endif %}
  {% if result.unknown and not project %}
  <h3>Documentos que no están en el registro</h3>
  <ul>{% for u in result.unknown %}<li>Fila {{ u.row }}: {{ u.document }} · {{ u.paid|money }}</li>{% endfor %}</ul>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_views_reconciliation.py suno/labor/tests/test_views_registry.py`
Expected: PASS.

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/labor --config-file pyproject.toml"
git add server/suno/core/templates server/suno/labor
git commit -m "feat(labor): payroll import and reconciliation pages with audited views and export

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 17: El libro y el control conocen el tareo

**Files:**
- Modify: `server/suno/costs/ledger.py`, `server/suno/costs/services.py`, `server/suno/costs/access.py`, `server/suno/costs/views.py`
- Modify: `server/suno/core/templates/suno/costs/{list,detail}.html`
- Test: `server/suno/labor/tests/test_integration.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/labor/tests/test_integration.py`:

```python
"""Costos y Control con el tareo (spec 1.4, 2.1 y 3.5): origen "Tareo" en el libro sin nombres
ni salarios, enlace solo para quien lee Mano de obra, sin reverso desde el libro, y la mano de
obra en el costo real del control."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.models import CostEntry
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.services import CostRejected
from suno.costs.tests.factories import entry, pdf_upload
from suno.labor import approval, timesheets

from .factories import TODAY, WEEK, labor_world, line_data, to_item

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class LedgerAndControlTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lw = labor_world()
        author = self.lw.ctx(self.lw.contributor)
        sheet = timesheets.create_timesheet(author, self.lw.project, *WEEK)
        timesheets.save_lines(author, self.lw.project, sheet.pk, [
            line_data(self.lw.pedro, WEEK[0]),
            line_data(self.lw.maria, WEEK[0], "0.5", to_item(self.lw)),
        ], [WEEK[0]])  # fmt: skip
        timesheets.attach_signed(author, self.lw.project, sheet.pk, pdf_upload(), "f.pdf")
        self.sheet = approval.approve_timesheet(self.lw.ctx(self.lw.world.supervisor),
                                                self.lw.project, sheet.pk)  # fmt: skip
        entry(self.lw.world, amount="5000.00", status="approved")  # un movimiento manual
        self.costs = f"/plataforma/proyectos/{self.lw.project.pk}/costos/"

    def test_ledger_shows_the_origin_without_names_or_wages(self) -> None:
        self.client.force_login(self.lw.accountant)  # Costos (Consulta), sin Mano de obra
        page = self.client.get(self.costs)
        self.assertContains(page, "Tareo TA-001")
        self.assertNotContains(page, "/tareo/")  # sin enlace a la planilla
        for private in ("Pedro", "María", "80.000", "***5678"):
            with self.subTest(private=private):
                self.assertNotContains(page, private)
        only = self.client.get(self.costs + "?origen=tareo")
        self.assertContains(only, "Tareo TA-001")
        self.assertNotContains(only, "Concreto premezclado")  # el manual queda fuera
        self.client.force_login(self.lw.world.admin)
        self.assertContains(self.client.get(self.costs),
                            f"/tareo/{self.sheet.pk}/")  # fmt: skip

    def test_detail_offers_no_reversal_and_the_service_refuses(self) -> None:
        cost = CostEntry.objects.filter(timesheet=self.sheet).first()
        assert cost is not None
        self.client.force_login(self.lw.world.admin)
        detail = self.client.get(f"{self.costs}{cost.pk}/")
        self.assertContains(detail, "se anula desde la pestaña Tareo")
        self.assertNotContains(detail, "Reversar")
        with self.assertRaises(CostRejected) as refused:
            cost_services.reverse(self.lw.ctx(), self.lw.project, cost.pk, "No")
        self.assertIn("planilla de tareo", refused.exception.errors[0])

    def test_labor_is_actual_cost_in_control(self) -> None:
        data = load_control_input(self.lw.ctx(), self.lw.project, cut=date(2026, 3, 31),
                                  kind=MONTH, costs=True)  # fmt: skip
        assert data.costs is not None
        labor = sorted(line.amount for line in data.costs.lines if line.day == WEEK[1])
        self.assertEqual(labor, [D("76000.00"), D("121600.00")])
        scopes = {line.amount: line.scope for line in data.costs.lines if line.day == WEEK[1]}
        self.assertEqual(scopes[D("121600.00")], "indirect")  # va al CPI de administración
        self.assertEqual(scopes[D("76000.00")], "item")  # al CPI directo del ítem
```

(El `assert` en una prueba es válido: bandit solo revisa producción.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_integration.py`
Expected: FAIL (no aparece "Tareo TA-001"; el reverso da el mensaje de materiales).

- [ ] **Step 3: El libro**

En `server/suno/costs/ledger.py`:

1. Constantes:

```python
CERTIFICATES, PURCHASES, CUTS, ADJUSTMENTS = "actas", "compras", "cortes", "ajustes"
TIMESHEETS = "tareo"
ORIGINS = {
    ALL: "Todos",
    LEDGER_ONLY: "Manual",
    CERTIFICATES: "Actas",
    PURCHASES: "Compras",
    CUTS: "Cortes",
    ADJUSTMENTS: "Ajustes",
    TIMESHEETS: "Tareo",
    AUTOMATIC: "Solo automáticos",
}
#: El campo de origen de `CostEntry` de cada filtro de documento (6b, 6c y 6d).
DOCUMENT_ORIGINS = {
    CERTIFICATES: "certificate",
    PURCHASES: "material_purchase",
    CUTS: "consumption_cut",
    ADJUSTMENTS: "inventory_adjustment",
    TIMESHEETS: "timesheet",
}
SOURCE_LABELS = {
    "certificate": "Acta",
    "purchase": "Compra",
    "cut": "Corte",
    "adjustment": "Ajuste",
    "timesheet": "Tareo",
}
```

2. `_with_details`: `select_related(…, "inventory_adjustment", "timesheet")`.
3. `entry_source` gana el parámetro y la rama nueva, antes del `return "", "", ""`:

```python
def entry_source(entry: CostEntry, *, labor_links: bool = True) -> tuple[str, str, str]:
    """(tipo, etiqueta, URL) del documento que escribió el movimiento; vacío si es manual. La
    planilla de tareo (6d) solo se enlaza para quien lee Mano de obra (`labor_links`): los
    demás ven la etiqueta sin enlace (spec 3.5)."""
    ...
    if entry.timesheet is not None:
        sheet = entry.timesheet
        url = reverse("labor:timesheet", args=[project_id, sheet.pk]) if labor_links else ""
        return "timesheet", f"Tareo {sheet.code}", url
    return "", "", ""
```

4. `_entry_line(entry, catalog, labor_links=True)` llama `entry_source(entry, labor_links=labor_links)`; `ledger_page(…, labor_links: bool = True)` lo pasa a `_entry_line`. `export_lines` no cambia (el Excel lleva la etiqueta, no el enlace).

En `server/suno/costs/services.py`, después de `FROM_MATERIALS`:

```python
FROM_TIMESHEET = (
    "Este costo viene de una planilla de tareo: se anula la planilla desde la pestaña Tareo."
)
```

y en `reverse`:

```python
        if original.from_document:
            if original.certificate_id is not None:
                message = FROM_CERTIFICATE
            elif original.timesheet_id is not None:
                message = FROM_TIMESHEET
            else:
                message = FROM_MATERIALS
            raise CostRejected([message])
```

En `server/suno/costs/access.py`, `get_entry` agrega `"timesheet"` a su `select_related`.

En `server/suno/costs/views.py` (con `can_read_labor` en el import de políticas):

1. En la lista, `ledger_page(…, labor_links=can_read_labor(ctx, project))`.
2. En `detail`, `"source": entry_source(entry, labor_links=can_read_labor(ctx, project))`.

En `server/suno/core/templates/suno/costs/list.html`, en la celda de la descripción, reemplazar `{% if line.source_url %}<br><a class="cst-mark" href="{{ line.source_url }}">{{ line.source_label }}</a>{% endif %}` por:

```html
{% if line.source_url %}<br><a class="cst-mark" href="{{ line.source_url }}">{{ line.source_label }}</a>{% elif line.source_label %}<br><span class="cst-mark">{{ line.source_label }}</span>{% endif %}
```

(manteniendo el comentario `nosemgrep … var-in-href` al final de la línea). En `server/suno/core/templates/suno/costs/detail.html`, la línea del origen queda:

```html
  {% if source.1 %}<p class="pj-muted">{% if source.0 == "certificate" %}Viene del acta <a href="{{ source.2 }}">{{ source.1 }}</a>: se anula desde el contrato.{% elif source.0 == "timesheet" %}Viene de la planilla {% if source.2 %}<a href="{{ source.2 }}">{{ source.1 }}</a>{% else %}{{ source.1 }}{% endif %}: se anula desde la pestaña Tareo.{% else %}Viene de <a href="{{ source.2 }}">{{ source.1 }}</a>: se anula desde el documento en la pestaña Materiales.{% endif %}</p>{% endif %}{# nosemgrep: generic.html-templates.security.var-in-href.var-in-href -- href hecho en el servidor con reverse() (ruta de la app), nunca texto del usuario #}
```

- [ ] **Step 4: El control**

No cambia el cálculo (decisión del spec 2.1: la mano de obra entra al AC y al CPI como cualquier movimiento aprobado; lo indirecto, al CPI de administración). La prueba de arriba lo deja fijado.

- [ ] **Step 5: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_integration.py suno/costs suno/contracts suno/materials/tests/test_integration.py suno/control`
Expected: PASS (las pruebas de 6b y 6c sobre orígenes siguen verdes).

- [ ] **Step 6: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno --config-file pyproject.toml"
git add server/suno/costs server/suno/core/templates server/suno/labor
git commit -m "feat(costs): timesheet origin in the ledger, linked only for labor readers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Prueba de carga (60 trabajadores × 31 días × 3 destinos; conciliación de 500)

**Files:**
- Test: `server/suno/labor/tests/test_performance.py`

- [ ] **Step 1: Escribir la prueba**

`server/suno/labor/tests/test_performance.py`:

```python
"""Carga (spec 4 "Carga"): 60 trabajadores × 31 días con 3 destinos (5.580 líneas) y una nómina
de 500 trabajadores. La cuadrícula, el PDF y la conciliación responden en menos de 1 s (mejor de
3), con un número fijo de consultas. Como 6b y 6c, `ANALYZE` sobre las tablas cargadas."""

from __future__ import annotations

import time
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    BenefitFactor,
    Budget,
    PayrollImport,
    PayrollImportLine,
    ProjectWorker,
    Timesheet,
    TimesheetLine,
    TimesheetLineItem,
    Worker,
    WorkerWage,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import cost_world, item, support_blob
from suno.labor.timesheets import hashed
from suno.materials.destination import destination_key, key_text
from suno.projects.tests.factories import FieldWorld

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
START, END = date(2026, 3, 1), date(2026, 3, 31)
LIMIT_SECONDS = 1.0
MEASUREMENTS = 3


def load(world: FieldWorld, budget: Budget) -> tuple[Timesheet, PayrollImport]:
    org, project, admin = world.org, world.project, world.admin
    workers = Worker.objects.bulk_create([
        Worker(org=org, document_type="CC", document_number=f"{10_000_000 + n}",
               full_name=f"Trabajador {n:03d}", position="Oficial", hired_on=date(2026, 1, 5))
        for n in range(500)
    ])  # fmt: skip
    WorkerWage.objects.bulk_create([
        WorkerWage(org=org, worker=w, valid_from=date(2026, 1, 5), daily_wage=D("80000.00"))
        for w in workers
    ])  # fmt: skip
    BenefitFactor.objects.create(org=org, valid_from=date(2026, 1, 1), factor=D("1.5200"))
    crew = workers[:60]
    ProjectWorker.objects.bulk_create([
        ProjectWorker(org=org, project=project, worker=w, starts_on=date(2026, 1, 5),
                      created_by=admin, updated_by=admin)
        for w in crew
    ])  # fmt: skip
    sheet = Timesheet.objects.create(
        org=org, project=project, number=1, starts_on=START, ends_on=END, status="approved",
        created_by=admin, updated_by=admin, decided_by=world.supervisor,
        decided_at=timezone.now(), attachment=support_blob("carga"), attachment_name="f.pdf",
    )  # fmt: skip
    catalog = load_catalog(world.ctx(), project)
    concrete = item(budget, "2.1")
    #: Tres destinos de medio jornal: 1,5 por persona y día, justo el tope.
    destinations = [
        ("indirect", None, ()),
        ("item", catalog.rows[concrete.pk].parent_id, (concrete.pk,)),
        ("chapter", item(budget, "1").pk, ()),
    ]
    days = [START + timedelta(days=n) for n in range(31)]
    lines: list[TimesheetLine] = []
    links: list[TimesheetLineItem] = []
    for worker in crew:
        for day in days:
            for scope, chapter_id, items in destinations:
                key = hashed(key_text(destination_key(scope, chapter_id, items, None)))
                line = TimesheetLine(
                    org=org, timesheet=sheet, worker=worker, worked_on=day, fraction=D("0.5"),
                    scope=scope, chapter_id=chapter_id, destination_key=key,
                    daily_wage=D("80000.00"), factor=D("1.5200"), amount=D("60800.00"),
                )  # fmt: skip
                lines.append(line)
                links += [TimesheetLineItem(org=org, line=line, item_id=i) for i in items]
    TimesheetLine.objects.bulk_create(lines, batch_size=5000)
    TimesheetLineItem.objects.bulk_create(links, batch_size=5000)
    payroll = PayrollImport.objects.create(
        org=org, starts_on=START, ends_on=END, file_name="nomina.xlsx", file_size=1,
        file_sha256="0" * 64, line_count=500, total=D("0.00"), imported_by=admin,
    )  # fmt: skip
    PayrollImportLine.objects.bulk_create([
        PayrollImportLine(org=org, payroll=payroll, row=n + 2, document_type="CC",
                          document_number=w.document_number, worker=w,
                          total_paid=D("2900000.00"))
        for n, w in enumerate(workers)
    ])  # fmt: skip
    with connection.cursor() as cursor:
        for table in ("worker", "worker_wage", "project_worker", "timesheet", "timesheet_line",
                      "timesheet_line_item", "payroll_import_line"):  # fmt: skip
            cursor.execute(f"ANALYZE {table}")
    return sheet, payroll
```

La clase:

```python
@skip_without_db
class LaborLoadTests(TestCase):
    world: FieldWorld
    sheet: Timesheet
    payroll: PayrollImport

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world, budget = cost_world("Carga de tareo")
        cls.sheet, cls.payroll = load(cls.world, budget)

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def best_of(self, url: str) -> float:
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
        return min(timings)

    def test_grid_pdf_and_reconciliation_render_under_the_limit(self) -> None:
        self.assertEqual(TimesheetLine.objects.filter(timesheet=self.sheet).count(), 5580)
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{self.world.project.pk}/tareo/{self.sheet.pk}/"
        for url in (
            f"{base}?semana=2026-03-09",
            f"{base}planilla.pdf",
            f"/plataforma/trabajadores/conciliacion/{self.payroll.pk}/",
        ):
            with self.subTest(url=url):
                best = self.best_of(url)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")
```

(La administración de `cost_world` es Gestora de Mano de obra por la señal, así que ve las tres páginas. Las consultas fijas de cada página ya las prueban `test_grid.py` y `test_payroll.py`; aquí se mide el tiempo.)

- [ ] **Step 2: Correr**

Run: `bash infra/scripts/test_docker.sh suno/labor/tests/test_performance.py -s`
Expected: PASS. Anotar los tres tiempos (para el registro de avance).

- [ ] **Step 3: Si pasa del límite**

Perfilar antes de tocar (`python -m cProfile` dentro del contenedor sobre la vista). Lo probable: el PDF (reportlab con miles de celdas) o la cuadrícula (5.580 líneas evaluadas para los totales del periodo). Para el PDF, que las celdas de cifras sigan siendo texto plano (no `Paragraph`, Task 15). Para la cuadrícula, que `evaluate` no haga una consulta por fila (son cinco). No subir el límite.

- [ ] **Step 4: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno/labor && ruff check suno/labor && mypy suno/labor --config-file pyproject.toml"
git add server/suno/labor/tests/test_performance.py
git commit -m "test(labor): load test for the grid, the PDF and a 500-worker reconciliation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Tareo en los datos de ejemplo (`seed_control_demo`)

**Files:**
- Modify: `server/suno/management/commands/seed_control_demo.py`
- Test: `server/suno/control/tests/test_seed_demo.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/suno/control/tests/test_seed_demo.py`, después de `test_seeds_materials_with_a_cut_in_the_ledger` (con `PayrollImport`, `Timesheet`, `Worker` en el import de modelos):

```python
    def test_seeds_labor_with_an_approved_timesheet_and_a_payroll(self) -> None:
        self.seed()
        org = self.org
        self.assertGreaterEqual(Worker.objects.filter(org=org).count(), 4)
        sheet = Timesheet.objects.get(project__org=org, status="approved")
        self.assertTrue(CostEntry.objects.filter(timesheet=sheet, kind="labor").exists())
        self.assertTrue(PayrollImport.objects.filter(org=org).exists())
        self.assertTrue(Timesheet.objects.filter(project__org=org, status="draft").exists())
```

- [ ] **Step 2: Correr y ver que falla**

Run: `bash infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: FAIL (`Worker.DoesNotExist`/conteo 0).

- [ ] **Step 3: Implementar**

En `server/suno/management/commands/seed_control_demo.py`:

1. Docstring del módulo: "Desde la pieza 6d también siembra tareo: cuatro trabajadores con jornal, factor prestacional, asignaciones, una planilla aprobada (en el libro como Mano de obra) y otra en borrador, y la nómina del mes importada."
2. Imports:

```python
from suno.backoffice.export import to_xlsx_book
from suno.labor import approval as labor_approval
from suno.labor import assignments as labor_assignments
from suno.labor import payroll as labor_payroll
from suno.labor import registry as labor_registry
from suno.labor import timesheets as labor_timesheets
from suno.labor.assignments import AssignmentData
from suno.labor.registry import WorkerData
from suno.labor.timesheets import LineData
```

(y `Worker` en el import de `suno.core.models`).

3. Una constante con los trabajadores, junto a `MATERIALS`:

```python
#: (documento, nombre, cargo, jornal) de los trabajadores de ejemplo (pieza 6d).
WORKERS = (
    ("1012345678", "Pedro Pérez (ejemplo)", "Oficial", "85000.00"),
    ("52123456", "María Gómez (ejemplo)", "Ayudante", "65000.00"),
    ("80111222", "Luis Torres (ejemplo)", "Oficial", "85000.00"),
    ("79333444", "Ana Rojas (ejemplo)", "Ayudante", "65000.00"),
)
```

4. En `run`, después de `self._materials(...)`: `self._labor(project, start, items, tasks)`.
5. El método:

```python
    def _labor(
        self,
        project: Project,
        start: date,
        items: dict[str, BudgetItem],
        tasks: dict[str, Task],
    ) -> None:
        """Tareo por los servicios: el administrador lleva el registro; el residente (Revisor de
        Mano de obra) asigna, llena y firma; el administrador aprueba. El registro es de la
        organización: se reutiliza si ya existe."""
        resident = self._resident()
        set_section_grant(
            self.ctx, user_id=resident.pk, section=Section.LABOR, role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )  # fmt: skip
        office = resolve_request_context(Principal(user_id=resident.pk, org_id=self.org.pk))
        admin = self.ctx
        hired = start - timedelta(days=14)
        if not BenefitFactor.objects.filter(org=self.org).exists():
            labor_registry.add_factor(admin, hired, Decimal("1.5200"),
                                      "Prestaciones, seguridad social, parafiscales y dotación")  # fmt: skip
        crew = []
        for number, name, position, wage in WORKERS:
            worker = Worker.objects.filter(org=self.org, document_type="CC",
                                           document_number=number).first()  # fmt: skip
            if worker is None:
                worker = labor_registry.create_worker(
                    admin, WorkerData("CC", number, name, position, hired), Decimal(wage)
                )
            labor_assignments.create_assignment(office, project,
                                                AssignmentData(worker.pk, hired, None))  # fmt: skip
            crew.append(worker)
        monday = start + timedelta(days=98)
        week = (monday, monday + timedelta(days=6))
        footing = DestinationData(str(CostScope.ITEM), (items["3.1"].pk,),
                                  task_id=tasks["Zapatas eje A"].pk)  # fmt: skip
        approved = labor_timesheets.create_timesheet(office, project, *week)
        days = [monday + timedelta(days=n) for n in range(6)]  # lunes a sábado
        lines = [
            LineData(worker.pk, day, footing if n % 2 == 0 else INDIRECT,
                     Decimal("1") if day.weekday() < 5 else Decimal("0.5"))
            for n, worker in enumerate(crew)
            for day in days
        ]  # fmt: skip
        labor_timesheets.save_lines(office, project, approved.pk, lines, days)
        labor_timesheets.attach_signed(office, project, approved.pk, self._upload(),
                                       "planilla-firmada.pdf")  # fmt: skip
        labor_approval.approve_timesheet(admin, project, approved.pk)
        # La semana siguiente, en borrador (sin firmar).
        following = (week[0] + timedelta(days=7), week[1] + timedelta(days=7))
        draft = labor_timesheets.create_timesheet(office, project, *following)
        labor_timesheets.save_lines(
            office, project, draft.pk,
            [LineData(crew[0].pk, following[0], INDIRECT, Decimal("1"))], [following[0]],
        )  # fmt: skip
        # La nómina del mes de la planilla aprobada (pagado un poco más que lo tareado).
        month_start = monday.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        book = to_xlsx_book([("Nómina", ["Tipo de documento", "Número de documento",
                                         "Total pagado"],
                              [["CC", number, "800000"] for number, *_ in WORKERS])])  # fmt: skip
        preview = labor_payroll.preview(admin, book, month_start, month_end, "nomina-ejemplo.xlsx")
        labor_payroll.apply(admin, preview.token)
```

`BenefitFactor` va en el import de `suno.core.models`; `INDIRECT`, `DestinationData`, `CostScope`, `AccessScope`, `Section`, `SectionRole`, `set_section_grant`, `Principal` y `resolve_request_context` ya están importados por las piezas anteriores. El mes de la nómina queda en el pasado (la planilla es de hace unos 84 días), así que `end <= today` se cumple.

- [ ] **Step 4: Correr las pruebas**

Run: `bash infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: PASS.

- [ ] **Step 5: Formato y commit**

```bash
bash infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/management --config-file pyproject.toml"
git add server/suno/management/commands/seed_control_demo.py server/suno/control/tests/test_seed_demo.py
git commit -m "feat(labor): seed demo workers, assignments, an approved timesheet and a payroll

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Documentación: dos ADR, arquitectura, seguridad y problemas conocidos

**Files:**
- Create: `docs/adr/0039-tareo-nominal-costo-estimado-nomina-solo-se-concilia.md`, `docs/adr/0040-seccion-mano-de-obra-separada-de-costos.md`
- Modify: `docs/adr/README.md`, `docs/arquitectura/componentes.md`, `docs/arquitectura/modelo-datos-costos.md`, `docs/seguridad/tratamiento-datos-personales.md`, `docs/seguridad/inventario-activos.md`, `docs/seguridad/retencion.md`

Regla del repositorio (`CONTRIBUTING.md`, "Documentación"): cada pieza entrega sus ADR, actualiza `docs/arquitectura/` y deja la lista de issues `known-issue`. Antes de escribir, comprobar que 0039 y 0040 siguen libres (`ls docs/adr`); si otra rama ya los tomó, usar los siguientes.

- [ ] **Step 1: ADR-0039**

`docs/adr/0039-tareo-nominal-costo-estimado-nomina-solo-se-concilia.md`:

```markdown
# ADR-0039: Tareo nominal con costo estimado; la nómina solo se concilia

- **Estado:** Aceptada
- **Fecha:** 2026-11-18

## Contexto

Hasta la pieza 6c, la mano de obra por jornal solo entraba al libro de costos como un movimiento manual de "Mano de obra" o "Nómina", sin saber quién trabajó ni en qué. Las constructoras llevan el tareo (quién trabajó, qué día y en qué frente) en papel o Excel y la nómina en su software de nómina, que liquida prestaciones, seguridad social, PILA y nómina electrónica.

## Decisión

- SUNO lleva el **tareo nominal**: un registro de trabajadores de la organización con datos personales mínimos (documento, nombre, cargo, ingreso y retiro), su jornal básico y un factor prestacional de la organización, ambos con vigencia.
- El **costo es estimado**: fracción de jornal × jornal vigente ese día × factor vigente ese día, redondeado por línea; el de un destino es la suma de sus líneas. Las horas extra se registran como fracción adicional (hasta 1,5 jornales por persona y día entre todas las obras).
- La **planilla** (por proyecto y periodo de hasta 31 días) se aprueba con el soporte firmado, por alguien distinto de quien la hizo, y "lo aprobado es lo firmado" ([ADR-0032](0032-aprobado-es-lo-firmado.md)). Al aprobar se congelan jornal, factor y valor y se escribe un costo de Mano de obra por destino con origen `timesheet` ([ADR-0031](0031-origen-unico-del-movimiento-del-libro.md)). Se corrige anulando la planilla (reversos de hoy) y haciendo otra.
- **SUNO no liquida nómina.** La nómina real se importa desde Excel solo para un **reporte de conciliación** (tareado contra pagado, por trabajador y por proyecto con prorrateo); no ajusta el libro.
- El tope diario se serializa con el orden de bloqueos de [ADR-0035](0035-orden-de-bloqueos.md): proyecto, planilla y filas de los trabajadores en orden de `pk`. Las tres importaciones (trabajadores, tareo y nómina) siguen [ADR-0036](0036-importaciones-en-dos-pasos.md) con un solo token (`LaborImportToken`).

## Consecuencias

- El costo real de la mano de obra en Control (AC y CPI) es una estimación con el factor de la organización; la diferencia con lo pagado se ve en la conciliación, no en el libro.
- SUNO pasa a tratar datos personales de trabajadores que no son usuarios (documento y salario): ver `docs/seguridad/tratamiento-datos-personales.md` (T-10) y [ADR-0040](0040-seccion-mano-de-obra-separada-de-costos.md).
- La captura en la app móvil, las novedades (incapacidades, vacaciones) y los recargos por tipo quedan fuera.

## Referencias

- Spec y plan de la pieza 6d: `plans/2026-11-18-costos-pieza6d-tareo-design.md`, `plans/2026-11-18-costos-pieza6d-tareo-plan.md`.
- `server/suno/labor/calc.py`, `timesheets.py`, `approval.py`, `payroll.py`; `server/suno/core/models/labor.py`.
```

- [ ] **Step 2: ADR-0040**

`docs/adr/0040-seccion-mano-de-obra-separada-de-costos.md`:

```markdown
# ADR-0040: Sección "Mano de obra" separada de Costos

- **Estado:** Aceptada (amplía [ADR-0022](0022-matriz-de-permisos-por-seccion.md))
- **Fecha:** 2026-11-18

## Contexto

El tareo de la pieza 6d lleva nombres, documentos y salarios. La sección Costos la tienen quienes ven y aprueban costos de obra (contadores, residentes, interventoría), que no necesitan ver cuánto gana cada trabajador. Usar Costos para el tareo habría abierto esos datos a todos ellos.

## Decisión

- Sección nueva **`LABOR` ("Mano de obra")** en la matriz fija de `core/access.py`: Gestor (crear, leer, editar, borrar, aprobar, rechazar, anular, exportar), Revisor (sin anular), Colaborador (crear, leer, editar, borrar y exportar; solo lo suyo) y Consulta (solo leer: los exportes llevan nombres y salarios).
- La migración `0031` da la sección a la administración existente (`OWNER`, `ADMIN`) como Gestora con alcance de organización, igual que la `0028` con Costos; `LEGACY_GRANTS` hace lo mismo con las membresías nuevas. A los demás se la da el administrador de accesos.
- El **registro** (trabajadores, jornales, factor y conciliación) pide la sección con alcance de **toda la organización**: leer para verlo, `create` y `update` para gestionarlo. Un alcance por proyectos solo abre la pestaña Tareo de esos proyectos, con el documento enmascarado.
- En Costos y Control la mano de obra aparece como costos por destino ("Tareo TA-001"), **sin nombres ni salarios**; el enlace a la planilla solo se muestra a quien lee Mano de obra en el proyecto.

## Consecuencias

- Quien aprueba costos no ve salarios salvo que también tenga Mano de obra.
- Una sección más en la matriz de accesos de la web y en el perfil de sesión (`sections`) que recibe la app; la app ignora secciones que no conoce.

## Referencias

- Spec 6d, sección 3.5; `server/suno/core/access.py`, `server/suno/core/policies.py` (`can_*_labor`, `can_*_labor_registry`), `server/suno/core/migrations/0031_labor.py`.
```

- [ ] **Step 3: README de los ADR**

En `docs/adr/README.md`, al final de la tabla:

```markdown
| [0039](0039-tareo-nominal-costo-estimado-nomina-solo-se-concilia.md) | Tareo nominal con costo estimado; la nómina solo se concilia | Aceptada |
| [0040](0040-seccion-mano-de-obra-separada-de-costos.md) | Sección "Mano de obra" separada de Costos | Aceptada |
```

El ADR-0022 no se toca: un ADR aceptado solo cambia de estado si se reemplaza (ADR-0000), y el 0040 lo amplía sin reemplazarlo (lo dice su propio estado).

- [ ] **Step 4: Arquitectura**

`docs/arquitectura/componentes.md`:

1. En el diagrama, dentro de `subgraph costos`, agregar `labor["labor<br/>pieza 6d (tareo)"]`; en las flechas: `web --> … & labor`, `labor --> costs & budgets & attachments & materials`.
2. En la tabla "Módulo de costos", la fila:

```markdown
| 6d | `labor` | Registro de trabajadores (datos mínimos), jornal y factor prestacional con vigencia, asignaciones, planillas de tareo con cuadrícula semanal y vista por día, tope de 1,5 jornales entre obras, aprobación con PDF firmado y costo estimado por destino en el libro; importaciones de trabajadores, tareo y nómina; conciliación con la nómina real (sin tocar el libro). | `2026-11-18-costos-pieza6d-tareo-design.md` |
```

3. El párrafo de debajo: "Todas las páginas del módulo usan la sección **Costos** o **Presupuestos** de la matriz de acceso; el tareo usa la sección **Mano de obra** ([ADR-0040](../adr/0040-seccion-mano-de-obra-separada-de-costos.md)). … Las decisiones que dan forma al módulo están en los ADR 0026–0036, 0039 y 0040."

`docs/arquitectura/modelo-datos-costos.md`:

1. En el `erDiagram`:

```text
    WORKER ||--o{ WORKER_WAGE : "jornal con vigencia (6d)"
    PROJECT ||--o{ PROJECT_WORKER : "asignaciones (6d)"
    WORKER ||--o{ PROJECT_WORKER : ""
    PROJECT ||--o{ TIMESHEET : "planillas de tareo (6d)"
    TIMESHEET ||--o{ TIMESHEET_LINE : "trabajador x día x destino"
    WORKER ||--o{ TIMESHEET_LINE : ""
    TIMESHEET ||--o{ COST_ENTRY : "origen: planilla (6d)"
    PAYROLL_IMPORT ||--o{ PAYROLL_IMPORT_LINE : "nómina para conciliar (6d)"
```

2. En "Por pieza", la fila:

```markdown
| 6d | `Worker`, `WorkerWage`, `BenefitFactor`, `ProjectWorker`, `Timesheet`, `TimesheetLine(+Item)`, `PayrollImport(+Line)`, `LaborImportToken` (`labor.py`); `CostEntry.timesheet`; `SectionGrant` gana la sección `LABOR` | Costo estimado = fracción × jornal × factor vigentes, congelado al aprobar ([ADR-0039](../adr/0039-tareo-nominal-costo-estimado-nomina-solo-se-concilia.md)); la nómina solo se concilia; datos en una sección aparte ([ADR-0040](../adr/0040-seccion-mano-de-obra-separada-de-costos.md)). |
```

3. En "Invariantes en la base de datos":

```markdown
| `cost_entry_single_origin` (6c; 6d agrega `timesheet`) | `CostEntry` | Como mucho un documento de origen (acta, compra, corte, ajuste o planilla de tareo). |
| `worker_document_uq`, `worker_active_retired` (6d) | `Worker` | Un documento por organización; activo si y solo si no tiene fecha de retiro. |
| `timesheet_period_max_31`, `timesheet_approval_ok`, `timesheet_reverse_note` (6d) | `Timesheet` | Periodo de hasta 31 días; aprobada con la planilla firmada y por otra persona; anulada con motivo. |
| `timesheet_line_fraction`, `timesheet_line_frozen`, `timesheet_line_uq` (6d) | `TimesheetLine` | 0 < fracción ≤ 1,5; jornal, factor y valor nulos o los tres > 0; una línea por trabajador, día y destino. |
| `benefit_factor_range` (6d) | `BenefitFactor` | 1 ≤ factor ≤ 3. |
```

(reemplazando la fila de `cost_entry_single_origin` que ya existe).

- [ ] **Step 5: Seguridad**

`docs/seguridad/tratamiento-datos-personales.md`, una fila nueva en "Tratamientos":

```markdown
| T-10 | **Trabajadores de obra**: tipo y número de documento, nombre, cargo, fechas de ingreso y retiro, jornal básico con su historial, días tareados y su costo; en la conciliación, el total pagado por la nómina | Trabajadores de la organización cliente (no son usuarios de SUNO) | Tareo y costo estimado de la mano de obra de cada obra; conciliación con la nómina (pieza 6d) | PostgreSQL (`worker`, `worker_wage`, `timesheet_line`, `payroll_import_line`) → backups | El tareo aprobado se conserva según la ley laboral (ver [`retencion.md`](retencion.md)); un trabajador sin tareo se puede borrar | Ninguno; Cloudflare R2 para los backups (**transferencia internacional**) | Datos mínimos (sin dirección, teléfono, correo, cuenta, EPS ni fondo); sección **Mano de obra** separada de Costos (ADR-0040); documento enmascarado (`***4567`) en listas, planillas, PDF y Excel; auditoría de altas, cambios de jornal, importaciones, exportes y consultas de la conciliación (sin nombres en la metadata); del Excel de nómina solo se guardan nombre, tamaño y SHA-256 |
```

y en "Pendientes para cumplir", un punto: "**Trabajadores de obra (T-10)**: la consulta y la supresión del titular que no es usuario no tienen flujo propio en el back-office; hoy se atienden con el registro (ficha y borrado de quien no tiene tareo). Ver el issue `known-issue` correspondiente."

`docs/seguridad/inventario-activos.md`, en "Activos de información", una fila con el formato de la tabla:

```markdown
| Registro de trabajadores y tareo (pieza 6d) | **Confidencial** (datos personales y salariales de terceros) | PostgreSQL (`worker`, `worker_wage`, `benefit_factor`, `project_worker`, `timesheet`, `timesheet_line`, `payroll_import`, `payroll_import_line`) y backups; planillas firmadas en el R2 de adjuntos | Sección Mano de obra (ADR-0040); aislamiento por organización; documento enmascarado fuera de la ficha; auditoría de exportes y consultas | Producto (Mano de obra) |
```

`docs/seguridad/retencion.md`, filas nuevas:

```markdown
| Tareo aprobado o anulado (`timesheet`, `timesheet_line`) y sus costos | Trabajador: documento, días y costo | Mientras exista la organización: es soporte contable y laboral (la ley laboral exige conservar los registros de la relación laboral; el plazo exacto lo define el cliente) | — | Sin purga automática; no se borra desde la aplicación |
| Trabajador sin tareo (`worker`, `worker_wage`, `project_worker`) | Documento, nombre, cargo, jornal | Mientras el cliente lo mantenga | — | Se borra desde su ficha (solo si no tiene tareo) |
| Nómina importada (`payroll_import`, `payroll_import_line`) | Documento y total pagado | Mientras el cliente la mantenga | — | **Sin borrado en la aplicación** (ver issue `known-issue`); el archivo original no se guarda |
```

- [ ] **Step 6: Lista de issues `known-issue` (no crearlos)**

Anotar en el registro de avance (Task 21) y en la descripción de la PR, para que el controlador o el usuario los cree con la plantilla de Bug y la etiqueta `known-issue`:

1. **S3** — La captura del tareo en la app móvil no existe: el tareo solo se registra en la web o por Excel (spec 5).
2. **S3** — Sin flujo de derechos del titular (consulta y supresión) para trabajadores que no son usuarios (T-10); hoy se atiende desde la ficha y el borrado sin tareo.
3. **S3** — Una nómina importada no se puede borrar ni reemplazar desde la aplicación (se importa otra del mismo periodo y se usa la nueva).
4. **S4** — Recargos por tipo de hora extra, novedades (incapacidades, vacaciones, licencias) y nómina electrónica fuera de alcance: las horas extra son fracción adicional de jornal (spec 5).
5. **S4** — La conciliación por proyecto deja fuera a quien fue pagado sin tareo en ningún proyecto (no se puede prorratear); se ve solo en la vista de la organización (decisión 22).
6. **S4** — Un cambio de factor o de jornal después de imprimir la planilla obliga a recalcular y volver a firmar; no hay aviso proactivo en la lista de planillas (solo en la ficha).
7. **S4** — La bandeja "Por aprobar" de Costos no muestra planillas de tareo (decisión 19).

- [ ] **Step 7: Commit**

```bash
git add docs
git commit -m "docs: ADRs, architecture, data protection and retention for cost piece 6d

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Verificación completa, revisión visual y registro de avance (push y PR los hace el controlador)

**Files:**
- Modify: `plans/suno-progreso.md`, `plans/2026-11-18-costos-pieza6d-tareo-design.md` (las decisiones del plan al final)

- [ ] **Step 1: Suite completa y compuertas**

```bash
bash infra/scripts/test_docker.sh
bash infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
bash infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
bash infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
bash infra/scripts/test_docker.sh --cmd "pip install -q bandit && bandit -q -r suno config -x '*/tests/*,*/migrations/*'"
bash infra/scripts/test_docker.sh --cmd "set -o pipefail; coverage run -m pytest -q -p no:cacheprovider && coverage report | tail -1"
```

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y `healthcheck.py` en el formato; `makemigrations --check` sin cambios (la `0031` lo cubre todo); bandit sin hallazgos (nada de `assert` ni `xml.etree` en producción); cobertura ≥ la de `master` (93 %), con `suno/labor` cerca del 100 %. La suite completa tarda cerca de una hora: correrla en segundo plano.

- [ ] **Step 2: Migración hacia atrás**

```bash
bash infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py migrate core 0030 -v 0 && python manage.py migrate -v 0"
```

Expected: sin errores (la reversa borra los grants de `LABOR`, restaura la restricción `section_grant_role_allowed` y `cost_entry_single_origin` anteriores, quita la columna `cost_entry.timesheet_id` y las tablas; la ida las crea otra vez y vuelve a dar la sección a la administración). Correrla sobre una base sin sembrar.

- [ ] **Step 3: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-tareo -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0.

- [ ] **Step 4: Revisión visual a 1440 y 390 px**

Levantar la app contra el Postgres de pruebas (sigue vivo hasta `--down`), sembrar y abrir:

```bash
bash infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py bootstrap_demo --org-name 'SUNO Demo' --username agente-demo --password 'Revision-visual-2026' && python manage.py shell -c \"from suno.core.models import AppUser, Membership, Organization, Role; org = Organization.objects.get(name='SUNO Demo'); user = AppUser.objects.create_user(username='admin-demo', email='admin-demo@example.com', password='Revision-visual-2026', org=org); Membership.objects.create(org=org, user=user, role=Role.ADMIN)\" && python manage.py seed_control_demo"
MSYS_NO_PATHCONV=1 docker run --rm -d --name suno-labor-web --network suno-test -p 8000:8000 \
  -v "$(cd server && pwd -W):/app" -v suno-test-venv:/venv -w /app \
  -e POSTGRES_HOST=suno-test-db -e POSTGRES_PASSWORD=test -e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
  python:3.12-slim sh -c "python manage.py runserver 0.0.0.0:8000"
```

Entrar en `http://localhost:8000/plataforma/ingresar/` como `admin-demo` (para entrar también como el residente, darle contraseña con `docker exec suno-labor-web python manage.py shell -c "…set_password…"`) y recorrer, con capturas de Playwright (o Edge headless) a 1440 px y a 390 px (a 390 px dentro de un iframe si el navegador no baja de 496 px):

- Barra lateral "Trabajadores": lista con filtros y documento enmascarado, ficha (documento completo, historial de jornales, asignaciones, retirar), formulario, importación con vista previa, factor prestacional, conciliación (importar, tabla, filtro por proyecto, Excel).
- Pestaña "Tareo": planillas (con "por aprobar"), nueva planilla, asignaciones (editar fechas), cuadrícula semanal (semana anterior/siguiente, "Repetir", "Partir", "Recalcular el resumen", totales en vivo con y sin JavaScript), **vista por día a 390 px** (la cuadrícula se oculta y aparece la lista del día), ficha con PDF (abrirlo: A4 horizontal, bloques de días, resumen y firmas), firmada, aprobar, rechazar con motivo, anular, Excel e importación.
- Costos: libro con el origen "Tareo" (como `admin-demo`, con enlace; como un usuario solo de Costos, sin enlace y sin nombres) y la ficha del movimiento (sin "Reversar").
- Textos largos: con `python manage.py shell -c`, alargar a 200 caracteres el nombre de un trabajador, a 80 el cargo y a 120 el nombre del proyecto; un jornal de 12 cifras.

Revisar: **ningún texto cortado ni solapes**; la cuadrícula se desplaza dentro de su panel a 1440 (columna de nombres fija) y no desborda la página; los botones "Repetir" no rompen el encabezado; en 390 la lista del día ocupa el ancho sin desbordes; los formularios de asignación y de corrección de jornal no se pisan. Corregir antes de cerrar (CSS en `projects.css`, subiendo `?v=` en `base.html`).

Al terminar: `docker rm -f suno-labor-web` y `bash infra/scripts/test_docker.sh --down`.

- [ ] **Step 5: Decisiones en la spec y registro de avance**

1. Al final de `plans/2026-11-18-costos-pieza6d-tareo-design.md`, una sección "Decisiones tomadas al planear" con el resumen de la sección del mismo nombre de este plan (las ⚑ marcadas).
2. En `plans/suno-progreso.md`, sección nueva al principio de la parte de costos: "Módulo de costos — Pieza 6d (mano de obra por jornal: tareo y conciliación de nómina), servidor — <fecha>", con: PR y rama (la PR la abre el controlador: dejar el número para que lo complete); qué se entregó (sección `LABOR` y migración `0031` con el grant de la administración, registro de trabajadores con jornal y factor con vigencia, asignaciones, planillas con cuadrícula semanal y vista por día, tope de 1,5 jornales entre obras con bloqueo de filas, aprobación con PDF firmado y costo por destino en el libro, anulación, importaciones de trabajadores, tareo y nómina, conciliación, origen "Tareo" en el libro sin nombres ni salarios, `seed_control_demo`); las decisiones del plan (con las ⚑); los números (pruebas, cobertura, consultas fijas, tiempos de la prueba de carga); lo que se corrigió en la revisión visual; las **limitaciones conocidas** (la lista de la Task 20, Step 6, con la nota "issues `known-issue` por crear"); y lo que sigue (captura del tareo en la app). Actualizar "Última actualización" del encabezado.

- [ ] **Step 6: Commit (sin push)**

```bash
git add plans/suno-progreso.md plans/2026-11-18-costos-pieza6d-tareo-design.md
git commit -m "docs(plan): progress log and planning decisions for cost piece 6d

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

El push de `feat/costos-tareo` y la PR (título sugerido: "feat(labor): labour timesheets with estimated cost in the ledger and payroll reconciliation"; cuerpo con resumen, qué entra, decisiones ⚑, permisos, pruebas, cobertura, tiempos de carga, capturas, la lista de issues `known-issue` por crear y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)") **los hace el controlador**. No integrar la PR: el merge es del usuario.

---

## Decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Las marcadas con ⚑ conviene que el usuario las mire (al terminar se copian al final de la spec).

1. **Una migración `0031_labor`.** Tablas `worker`, `worker_wage`, `benefit_factor`, `project_worker`, `timesheet`, `timesheet_line`, `timesheet_line_item`, `payroll_import`, `payroll_import_line` y `labor_import_token`; columna nula `cost_entry.timesheet_id` (entra en `cost_entry_single_origin` vía `ORIGIN_FIELDS`); opción nueva de `CostDocumentType` ("Planilla de tareo", solo `choices`); `section_grant.section` gana `LABOR` y la restricción `section_grant_role_allowed` se reescribe desde la matriz. ⚑ Lleva un `RunPython` (`forwards_labor_grants`, idempotente y reversible) que da **Mano de obra / Gestor / toda la organización** a las membresías `OWNER` y `ADMIN` que ya existen, igual que la `0028` hizo con Costos; `LEGACY_GRANTS` hace lo mismo con las membresías nuevas. Nadie más recibe la sección sin que el administrador de accesos la conceda.
2. ⚑ **Matriz de `LABOR` por rol** (la spec fija las capacidades por acción, no por rol): Gestor `create, read, update, delete, approve, reject, reverse, export`; Revisor `create, read, update, delete, approve, reject, export`; Colaborador `create, read, update, delete, export`; **Consulta solo `read`** (sin `export`, a diferencia de Costos: los exportes llevan nombres y salarios). Sin `import`: importar trabajadores o nómina pide gestionar el registro; importar tareo a una planilla es editarla.
3. ⚑ **Registro de la organización.** Ver la lista y la ficha de trabajadores (documento completo y jornales), el factor y la conciliación pide `read` de Mano de obra con alcance de **toda la organización** (`can_read_labor_registry`); dar de alta, cambiar jornales, el factor, retirar, borrar e importar trabajadores o nómina pide `create` **y** `update` con ese alcance (`can_manage_labor_registry`, la frase de la spec 3.5); el Excel de la conciliación pide `export` con ese alcance (`can_export_labor_registry`). Quien tiene Mano de obra limitada a algunos proyectos ve a los trabajadores solo dentro de esos proyectos (asignaciones y planillas, con el documento enmascarado).
4. **Políticas del proyecto** (nuevas, en `core/policies.py`, con su `check_*`): `can_read_labor`, `can_create_labor`, `can_update_labor` y `can_delete_labor` (estructurales: el Colaborador, solo lo que creó, como `can_update_cost`), `can_approve_labor`, `can_reject_labor`, `can_reverse_labor`, `can_export_labor`. Quien no lee Mano de obra en el proyecto recibe 404 en toda la pestaña (RD-11); quien la lee pero no tiene la capacidad, 403.
5. **"App nueva `labor`" = paquete `server/suno/labor/`** sin `apps.py`, con los modelos en `core/models/labor.py` y las plantillas en `core/templates/suno/labor/`, como `contracts` y `materials`. Reutiliza de la 6c el destino escrito (`materials.destination`) y las piezas de bloqueo y reverso (`materials.common.lock_project`, `next_number`, `locked`, `reverse_entries`); el diagrama de componentes gana la flecha `labor → materials`.
6. ⚑ **Trabajador.** `is_active` y `retired_on` van juntos (check `worker_active_retired`: activo sin fecha de retiro, inactivo con ella). Retirar pide una fecha ≥ ingreso y ≥ el último día tareado en planillas no anuladas, y cierra en esa fecha las asignaciones abiertas o que terminan después; "Reincorporar" (borra la fecha) existe para corregir un retiro por error. El número de documento se guarda limpio (sin puntos, espacios ni guiones, en mayúsculas; 3 a 20 caracteres alfanuméricos): "1.012.345.678" → "1012345678". Tipos: CC, CE, PPT, PEP y PA (pasaporte).
7. ⚑ **Jornal y factor "usados".** Una fila de jornal o de factor no se edita ni se borra si alguna línea de una planilla **aprobada o anulada** cae en su vigencia (desde su `valid_from` hasta el día anterior a la fila siguiente). La spec lo dice del jornal; se aplica igual al factor para que el valor congelado nunca quede sin su fila. Un cambio es una fila nueva, y tampoco puede regir sobre días ya congelados: un jornal o un factor nuevo con fecha anterior o igual al último día de una planilla aprobada o anulada que lo afecte se rechaza ("rige desde el día siguiente"). El primer jornal de un trabajador rige desde su fecha de ingreso.
8. ⚑ **Importación de trabajadores.** Hoja "Trabajadores": Tipo de documento, Número de documento, Nombre, Cargo, Fecha de ingreso, Jornal y Jornal desde (opcional). Un documento nuevo crea al trabajador con su jornal desde la fecha de ingreso; uno existente actualiza nombre y cargo y, si el jornal difiere del vigente, agrega una fila de jornal desde "Jornal desde" (sin esa columna, desde hoy). La fecha de ingreso de uno existente no cambia. Hasta 2.000 filas.
9. **Asignaciones.** Dentro del vínculo (ingreso ≤ inicio; fin ≤ retiro si lo hay); no se cruzan en el mismo proyecto (servicio, con el proyecto bloqueado). Editar o borrar una asignación no puede dejar fuera días ya tareados en planillas no anuladas del proyecto.
10. ⚑ **Destino de una fila**: el campo de texto de la 6c ("Indirecto", "2.1", "Capítulo 2", "2.1, 2.2") y la tarea por su número de esquema, validados con `check_assignment`. La unicidad (`timesheet`, `worker`, `worked_on`, destino) usa una columna `destination_key` con el SHA-256 de `materials.destination.key_text` (el destino incluye los ítems de una selección, que la BD no puede indexar como M2M).
11. ⚑ **Cuadrícula semanal**: muestra una semana del periodo (lunes a domingo, recortada al periodo; con "semana anterior/siguiente") y al guardar **reemplaza las líneas de esos días**. La vista por día (para 390 px) es otro formulario en la misma página, que CSS muestra en pantallas angostas, y guarda **solo ese día**. Hasta 250 filas por formulario; `DATA_UPLOAD_MAX_NUMBER_FIELDS` sube de 1.000 a 4.000 (60 trabajadores × 3 destinos × 7 días son 1.260 celdas más 720 campos de fila).
12. ⚑ **"Repetir el día anterior" y "Partir la fila" son botones del formulario** (sin JavaScript, ADR-0020): vuelven a pintar la cuadrícula con el cambio, **sin guardar**, como "Recalcular" de 6b y 6c. El JavaScript (`tareo.js`, opcional) solo actualiza en vivo los totales de jornales por fila y por día.
13. **Estados** `draft`, `approved`, `rejected`, `reversed` con etiquetas "Borrador", "Aprobada", "Rechazada" y "Anulada". La decisión se guarda en `decided_by`/`decided_at` (aprobó o rechazó, como 6c) en lugar de `approved_by`/`approved_at`; la anulación en `reversed_by`/`reversed_at`/`reverse_note`.
14. ⚑ **Huella ("lo aprobado es lo firmado")**: número de líneas, jornales, total, jornales y costo **por destino** (la spec) y además **por trabajador**, y si el costo está completo (todas las líneas con jornal y factor). Se guarda en `summary` cada vez que se guardan líneas; "Recalcular el resumen" (para cuando cambió un jornal o el factor) es una edición: quita el soporte firmado. La ficha compara lo vivo con lo guardado y avisa antes de aprobar.
15. ⚑ **Valores congelados de la línea.** La BD no ve desde la línea el estado de su planilla, así que el check es "los tres nulos o los tres > 0" (`timesheet_line_frozen`); que una línea de una planilla aprobada o anulada los tenga y una de un borrador no, lo garantiza el servicio (y una prueba). Una línea cuyo costo redondea a $0 no se aprueba ("revisa la fracción").
16. **Tope diario y bloqueos** (ADR-0035): cuentan borradores, rechazadas y aprobadas de toda la organización (no las anuladas). Al guardar y al aprobar: fila del proyecto (`FOR NO KEY UPDATE`), la planilla (`FOR UPDATE`) y las filas de los trabajadores en orden de `pk` (`FOR NO KEY UPDATE`, que no choca con las inserciones de líneas que apuntan al trabajador). Al aprobar, además las filas del factor en orden de `pk`, para que un cambio de factor no se cuele entre el cálculo y el congelado. Cambiar un jornal bloquea la fila del trabajador; cambiar el factor, las filas del factor.
17. **Al aprobar**: un `CostEntry` aprobado por destino (Mano de obra; documento "Planilla de tareo" `TA-001`; fecha = fin del periodo; descripción "Tareo TA-001 · 3.2 Muros · tarea 3.2 Muros eje A"; tercero vacío; registró y modificó = los de la planilla; aprobó = quien aprueba; soporte = la planilla firmada). El destino no se revalida contra el presupuesto al aprobar (como 6b y 6c).
18. **Anular**: cualquier planilla aprobada (spec 2.4), con motivo, reversos de hoy (`reverse_entries`, origen `timesheet`). La línea conserva sus valores congelados.
19. ⚑ **La bandeja "Por aprobar" de Costos no muestra planillas** (son de la sección Mano de obra, que esa bandeja no exige): la pestaña Tareo muestra cuántas hay por aprobar y las lista primero.
20. **Libro (6a)**: filtro de origen "Tareo"; etiqueta "Tareo TA-001"; el enlace a la planilla solo para quien tiene `read` de Mano de obra en el proyecto (los demás ven la etiqueta sin enlace); `costs.services.reverse` rechaza un movimiento de tareo ("se anula la planilla en la pestaña Tareo"). Ni nombres ni jornales salen en Costos ni en Control.
21. ⚑ **Archivo de la nómina**: SUNO guarda el nombre, el tamaño y el SHA-256 del Excel importado, no el archivo. Los adjuntos solo aceptan PDF e imágenes (`attachments.web.store_upload`) y guardar una copia más de la nómina va contra la minimización; las líneas importadas (documento y total pagado) quedan en la BD.
22. ⚑ **Conciliación por proyecto**: lo pagado se prorratea por los jornales tareados en el proyecto sobre los tareados en toda la organización en el periodo; quien fue pagado y no tiene tareo en ningún proyecto no se puede prorratear y solo aparece en la vista de toda la organización (la vista por proyecto dice cuántos quedaron fuera).
23. **Una tabla de nonce** (`LaborImportToken`, con `kind`: `workers`, `timesheet`, `payroll`) para las tres importaciones en dos pasos (ADR-0036).
24. ⚑ **PDF de la planilla**: A4 horizontal; si el periodo pasa de 16 días, la cuadrícula se parte en bloques de hasta 16 días; documento enmascarado; firmas "Maestro de obra", "Residente" y "Aprobó"; imprime el resumen guardado (por destino) que se compara al aprobar.
25. ⚑ **Auditoría y datos personales**: la metadata lleva ids, el documento enmascarado y cantidades, **nunca nombres**; los cambios de jornal y factor llevan el valor (es lo que se audita). Cada consulta de la conciliación se audita (`labor_reconciliation.viewed`), igual que los exportes (`labor.exported`, `timesheet.exported`).
26. **Límites**: nombre 200, cargo 80, notas y motivos 500, nombre de archivo 200; importación de tareo hasta 5.000 filas, de trabajadores 2.000, de nómina 5.000; listas de planillas y de trabajadores paginadas de a 100.
27. **Navegación**: "Trabajadores" en la barra lateral (Gestión) para quien lee el registro (`navigation.LABOR_REGISTRY`); pestaña "Tareo" del proyecto para quien lee Mano de obra en él (`can_view_labor`).
28. **Archivos**: `planilla-TA-001.pdf`, `planilla-TA-001.xlsx`, `conciliacion-<desde>-<hasta>.xlsx` y las plantillas `plantilla-trabajadores.xlsx`, `plantilla-tareo.xlsx` y `plantilla-nomina.xlsx`.
29. ⚑ **Prueba de concurrencia del tope.** Como los borradores ya cuentan para el tope (spec 2.2: "planillas no anuladas"), dos aprobaciones no pueden sumar por encima entre sí: lo que aprueban ya contaba al guardarse. Las carreras que se prueban con hilos (`TransactionTestCase`) son **dos guardados en obras distintas** y **un guardado contra una aprobación**; la aprobación vuelve a validar el tope igual (para datos que lleguen por otra vía).
30. **Fechas en las pruebas**: hoy = 2026-04-15; planilla de ejemplo del lunes 2 al domingo 8 de marzo de 2026; factor 1,5200; Pedro 80.000 y María 100.000 (un jornal de Pedro = 121.600,00).

## Ronda de correcciones de la revisión de código (2026-11-19 a 2026-11-23)

PR #___. Detalle en `plans/suno-progreso.md` (pieza 6d, "Correcciones de la revisión de código"). Cada hallazgo con prueba de regresión primero.

| Hallazgo | Commit | Corrección |
|---|---|---|
| C1 | `80b6ea6` | Soporte del costo de tareo solo para quien lee Mano de obra (decisión 17, ADR-0040; usuario 2026-11-19) |
| C2 | `f90aadd` | Importación respeta días congelados; aprobadas y anuladas muestran lo congelado |
| C3 | `8131743`, `2628d58` | Versión y código impreso; la carga del soporte pide el código (decisión 14, ADR-0032; usuario 2026-11-19) |
| I1 | `af76272` | Trabajador en una nómina: se retira, no se borra |
| I2 | `d1b3741` | Más de 250 filas: rechazo sin borrar nada |
| I3 | `8131743` | Concurrencia optimista con `Timesheet.version` (en la `0031`) |
| I4 | `16f3a62` | Destino que ya no está: fila de solo lectura, nunca cambia en silencio |
| I5 | `abfcff9` | Orden de bloqueos trabajador/asignaciones (ADR-0035) |
| M1 | `657aa5e` | "otro proyecto" en el mensaje del tope (usuario 2026-11-19) |
| M2 | `a0c0757` | Selector de asignación con todos los activos: decisión aceptada (ADR-0040; usuario 2026-11-19) |
| M3 | `8d8f357` | Escaneo firmado = exportar (usuario 2026-11-19) |
| M4 | `f2b25a3` | Enmascarado de documentos cortos y del número limpio |
| M5 | `b50c4b6` | Políticas antes de bloquear |
| M6 | `6d12496` | Factor duplicado concurrente → mensaje |
| M7 | `986c595` | Auditoría por cambio de jornal importado |
| M8 | `4ce4c24` | Nómina resuelta por documento al conciliar |
| M9 | `949f381` | Nóminas de a 100 |
| M10 | `b298126` | Convención de montos de los exportes |
| Detalle | `ca6a98b` | "1 jornal" |
| Cobertura | `5dd41c9`, `0052670` | `suno/labor` 89 % → 98 % |

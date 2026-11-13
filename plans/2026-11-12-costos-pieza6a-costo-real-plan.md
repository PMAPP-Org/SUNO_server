# Costos — Pieza 6a (libro de costos y control de costo): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registrar el costo real de cada proyecto en un libro de costos con aprobación y reverso (formulario y Excel), sumarle el costo automático de equipos propios y transporte, y mostrar en la página "Control" el AC, CV, CPI, EAC, VAC, TCPI, la administración ganada y la curva S con la serie AC.

**Architecture:** Dos modelos nuevos (`CostEntry`, `CostEntryItem`) en `core/models/cost.py` con una migración aditiva que además agrega la sección `COSTS` al RBAC por secciones (`core/access.py`) y la concede a Propietario y Administrador. Un paquete nuevo `server/suno/costs/` lleva el libro: catálogo de asignación sobre la versión vigente (`catalog.py`), servicios con auditoría (`services.py`), importación con vista previa firmada (`importer.py`), consultas y líneas automáticas (`ledger.py`), Excel (`export.py`) y la pestaña "Costos" (`views.py`, `forms.py`). El soporte se sube al almacén de adjuntos existente con un `BlobStore.put_blob` nuevo (`attachments/web.py`). El cálculo vive en `control/actual_cost.py` (puro) y se engancha en `earned_value.compute` a través de `ControlInput.costs`, que el cargador llena con cinco consultas más solo si el usuario lee costos.

**Tech Stack:** Django 5.2, PostgreSQL 16, plantillas de Django con SVG y CSS propios (sin JavaScript nuevo), `unittest`/`django.test` corridos en Docker.

**Spec:** `plans/2026-11-12-costos-pieza6a-costo-real-design.md` (aprobada; sus "Decisiones tomadas al planear" ya están al final). Plan de referencia de formato: `plans/2026-11-11-costos-pieza5-control-plan.md`.

**Rama y worktree:** `feat/costos-libro` en `C:\Users\gabri\APP\wt-server-costos` (base `master` `600d746`, spec en `843f61a`). Todos los comandos se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Ya están anotadas al final de la spec.

1. **Una sola migración** (`0028_costs_ledger`): crea `cost_entry` y `cost_entry_item`, cambia las opciones de `SectionGrant.section` y la restricción `section_grant_role_allowed` (que se deriva de `CAPABILITY_MATRIX`), y con `RunPython` concede `COSTS`/`MANAGER`/organización a cada membresía `OWNER` o `ADMIN` que no la tenga. Las membresías nuevas la reciben por `LEGACY_GRANTS` (`core/access_services.py`). Supervisor y Observador no reciben acceso automático. Sin la migración, el `CHECK` rechazaría el grant de `COSTS` que la señal crea para cada administrador nuevo, así que modelo, matriz y migración van en la misma tarea.
2. **Políticas por proyecto.** `can_read_costs`, `can_create_costs`, `can_import_costs`, `can_export_costs`, `can_approve_costs`, `can_reject_costs` y `can_reverse_costs` reciben el proyecto y pasan su `id` a `user_has_section_capability` (un acceso limitado por proyectos solo vale en los suyos). `can_update_cost` y `can_delete_cost` reciben el movimiento: Gestor y Revisor (los que tienen `APPROVE`), cualquiera; Colaborador, solo los suyos. El estado (borrador, rechazado, aprobado) y la autoaprobación los decide el servicio.
3. **404 y 403 en la web.** Quien no lee costos del proyecto (sin la sección, sin la capacidad o con un alcance que no lo incluye) recibe 404 (`costs.access.get_costs_project`, como `get_budget_project`). Quien lo lee y no tiene la capacidad de una acción recibe 403 (`PermissionDenied`), como en el control.
4. **`chapter` también en `item`:** el capítulo padre del ítem en la versión vigente al registrar. Es el "capítulo de origen" de la spec (3.5) cuando el ítem deja de ser `item`, sin consultar versiones viejas.
5. **Restricciones de la base** (`cost_entry`): valor positivo salvo en un reverso, que es negativo; aprobado y no reverso ⇒ soporte, decisor y decisor ≠ autor; rechazado ⇒ motivo; borrador ⇔ sin decisión; reverso ⇒ aprobado; indirecto ⇒ sin capítulo ni tarea; capítulo o selección ⇒ con capítulo; `reverses` es `OneToOneField` (único).
6. **Autoaprobación:** además de quien registró, tampoco decide quien hizo la última modificación (`updated_by`), para que nadie apruebe un valor que cambió él mismo.
7. **Rechazados:** solo su autor los edita o borra; al guardarlos vuelven a borrador ("reenviar") y se limpian decisión y motivo (la auditoría los conserva). Borrar un borrador lo elimina de la tabla; `cost.deleted` guarda sus datos.
8. **Soporte:** PDF, JPG, PNG o WebP, reconocidos por los primeros bytes (`attachments.validation`), hasta `ATTACHMENT_MAX_BYTES` (25 MB). Se guarda en el mismo almacén (`blobs/…`) con `BlobStore.put_blob` (nuevo en el puerto y en los dos adaptadores) y se registra el `Blob` con el mismo `INSERT … ON CONFLICT DO NOTHING` del commit. La descarga es una URL prefirmada que se entrega después de comprobar `can_read_costs`; la respuesta 302 se arma a mano porque `HttpResponseRedirect` no acepta el esquema `local://` del adaptador local.
9. **Formulario sin JavaScript:** la asignación se elige en dos pasos con un formulario GET (tipo de asignación y, para una selección, el capítulo) y luego el formulario POST con el ítem (campo con `datalist` sobre las etiquetas del catálogo), el capítulo (lista) o los ítems del capítulo (casillas).
10. **Importación sin tabla de preparación:** la vista previa firma las filas validadas (`django.core.signing`, sal propia, una hora, comprimido) en un campo oculto; aplicar verifica firma, proyecto y usuario, valida otra vez todo y crea todo o nada. Hasta 2000 filas.
11. **Libro combinado:** 100 líneas por página. Las automáticas (equipo por día y equipo, transporte por viaje) se leen agregadas y se mezclan por fecha con los movimientos; la página `n` lee como mucho `n × 100` movimientos.
12. **Automáticos:** usos de equipos con `ownership = own`, completados o en curso, con el `cost` que se guardó al registrar el uso (horas × tarifa vigente ese día: el mismo cálculo y la misma columna de `equipment_cost_summary`). Viajes con `vehicle` (tercero) y `amount`; los de equipo propio ya están en sus horas. Los usos y viajes sin valor se cuentan en un aviso.
13. **Cálculo:** `control/actual_cost.py` es puro y no importa `earned_value` (al revés sí); los índices compartidos pasan a `control/indices.py` (`ratio_of`, `status_of`, `share_of`) y `earned_value` conserva `spi_of`, `status_of` y `share_of` con los mismos nombres. AC de la curva y de los periodos = AC directo. La serie empieza en el primer costo directo contado si es anterior al primer inicio (un indirecto no la mueve).
14. **Grupos (desglose por ítem):** unión de conjuntos sobre los ítems de cada movimiento de capítulo (sus ítems con precio a cualquier profundidad) y de selección; el nombre es el del capítulo de menor profundidad; el AC del grupo suma sus movimientos y los movimientos de ítem de sus miembros.
15. **Cargador:** 14 consultas con costos (las 9 de la pieza 5 más cargos de las versiones vigentes, movimientos aprobados y borradores, ítems de los aprobados, usos de equipo agregados por día e ítem, viajes agregados por día); 12 sin presupuesto aplicado; 9 sin permiso de costos. Las agregaciones llevan `.order_by()` para que el `ordering` del modelo no entre en el `GROUP BY`.
16. **Página Control:** pestaña nueva "Por ítem" (`desglose=item`), parámetro `orden` (`spi` por defecto o `cpi`, que solo vale con permiso de costos), segunda fila de indicadores, serie AC en la curva y la tabla, columnas AC, CV y CPI, avisos de costo y hoja "Indicadores" en el Excel.
17. **Prueba de carga:** reutiliza `build_large_project` de la pieza 5 (500 tareas × 10 ítems) y suma 20 000 movimientos aprobados con `bulk_create` (uno de cada cuatro a un ítem); mide la segunda petición de la página semanal por tarea con `orden=cpi`.
18. **Datos de ejemplo:** `seed_control_demo` (ya en `ALLOWED` de `test_architecture.py`) suma cargos AIU, un residente que registra, movimientos de todos los tipos y asignaciones, un borrador, un rechazado, un reverso, un equipo propio con y sin ítem y un viaje de terceros; el soporte de ejemplo se sube de verdad al almacén configurado.

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `tearDownModule = teardown_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; `blob_registry()` para `Blob`; las pruebas y los comandos de `ALLOWED` sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`, con un código nuevo en `AuditAction`. Nada de `assert` en producción (bandit B101) ni `import xml.etree`. Ruff a 100 columnas (`E, F, I, UP, B, C4, DJ`; B008: sin llamadas en valores por defecto).
- `core/policies.py` es el único lugar donde se decide quién puede qué; cada `can_x` nuevo lleva su `check_x` y su entrada en el registro de `NoRoleGrantsNothingTests` (`core/tests/test_policies.py`).
- Comentarios y textos de interfaz en español; identificadores en inglés. Commits en inglés, estilo conventional, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La guardia de nombres (`infra/scripts/check_naming.py concept`) rechaza "visit"/"visita" en código y plantillas.
- Si cambia `projects.css`, se sube su `?v=` en `server/suno/core/templates/suno/projects/base.html`.
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura. No hay Python local: todo corre en Docker.

## Cómo correr las pruebas

```bash
# Desde C:/Users/gabri/APP/wt-server-costos, con Docker Desktop arrancado (el contenedor trabaja en server/)
infra/scripts/test_docker.sh suno/costs/tests/test_services.py      # un archivo
infra/scripts/test_docker.sh suno/costs suno/control                 # paquetes
infra/scripts/test_docker.sh                                         # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                  # al terminar

# Guardia de nombres (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-costos -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Fallas conocidas, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y `healthcheck.py` en `ruff format --check` (comprobarlo en la Task 0 y anotarlo).

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/suno/core/access.py` | `Section.COSTS`, `Capability.REVERSE`, matriz de Costos |
| `server/suno/core/access_services.py` | `LEGACY_GRANTS`: Costos/Gestor para `OWNER` y `ADMIN` |
| `server/suno/core/policies.py` | `can_*_costs`, `can_update_cost`, `can_delete_cost` y sus `check_*` |
| `server/suno/core/models/cost.py` | `CostEntry`, `CostEntryItem` y sus enumeraciones |
| `server/suno/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/suno/core/migrations/0028_costs_ledger.py` | Tablas, sección `COSTS` y grants de administración |
| `server/suno/core/audit.py` | `COST_CREATED` … `COST_EXPORTED` |
| `server/suno/attachments/blobstore.py`, `localfs.py`, `s3.py` | `put_blob` en el puerto y los adaptadores |
| `server/suno/attachments/services.py` | `register_server_upload` (público) |
| `server/suno/attachments/web.py` | `store_upload`: soporte subido desde la web |
| `server/suno/costs/__init__.py` | Paquete nuevo (sin modelos; plantillas en `core/templates/suno/costs/`) |
| `server/suno/costs/catalog.py` | `Catalog`, `load_catalog`, `assignable_tasks` |
| `server/suno/costs/access.py` | `get_costs_project`, `get_entry` |
| `server/suno/costs/services.py` | `EntryData`, `validate_entry`, crear, actualizar, borrar, aprobar, rechazar, reversar, `support_url` |
| `server/suno/costs/importer.py` | Plantilla, vista previa firmada y carga todo o nada |
| `server/suno/costs/ledger.py` | Filtros, líneas del libro y automáticas, página y totales |
| `server/suno/costs/export.py` | `ledger_workbook` con `safe_cell` |
| `server/suno/costs/forms.py` | `EntryForm`, pasos de la asignación, valores iniciales |
| `server/suno/costs/views.py`, `urls.py` | Pestaña "Costos" |
| `server/config/urls.py` | Monta `plataforma/proyectos/<uuid:project_id>/costos/` |
| `server/suno/core/templates/suno/costs/*.html` | Lista, formulario, ficha, bandeja, rechazados, reverso, importación |
| `server/suno/projects/views.py`, `core/templates/suno/projects/_header.html` | Pestaña "Costos" en la cabecera |
| `server/suno/control/indices.py` | `ratio_of`, `status_of`, `share_of` (puros, compartidos) |
| `server/suno/control/actual_cost.py` | Entrada y cálculo del costo real (puro) |
| `server/suno/control/earned_value.py` | `ControlInput.costs`, desglose por ítem, AC en periodos y filas |
| `server/suno/control/loader.py` | `costs=True`: cinco consultas más |
| `server/suno/control/chart.py`, `export.py`, `views.py` | Serie AC, hoja "Indicadores", orden y permisos |
| `server/suno/core/templates/suno/control/dashboard.html`, `_cost_warnings.html` | Fila de costo, columnas y avisos |
| `server/suno/core/static/suno/css/projects.css`, `projects/base.html` | Estilos `cst-*` y `ctl-*`; versión del CSS |
| `server/suno/management/commands/seed_control_demo.py` | Costos de ejemplo |
| Pruebas | `core/tests/{test_access_matrix,test_policies,test_costs_migration}.py`, `attachments/tests/test_web_upload.py`, `costs/tests/{__init__,factories,test_models,test_services,test_importer,test_ledger,test_views,test_views_actions}.py`, `control/tests/{test_actual_cost,test_loader,test_chart,test_export,test_views,test_performance,test_seed_demo}.py` |
| `plans/suno-progreso.md` | Registro de avance (Task 13) |

---

### Task 0: Línea base

**Files:** ninguno.

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-costos status --short --branch`
Expected: `## feat/costos-libro` sin cambios pendientes; el último commit es el de este plan.

- [ ] **Step 2: Línea base**

```bash
infra/scripts/test_docker.sh 2>&1 | tail -5
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`; `ruff format --check` solo marca `healthcheck.py`; `No changes detected`. Anotar el número de pruebas (para el registro de avance). No hay commit en esta tarea.

---

### Task 1: Sección "Costos", políticas, modelo del libro y migración

**Files:**
- Modify: `server/suno/core/access.py` (`Section`, `Capability`, `CAPABILITY_MATRIX`)
- Modify: `server/suno/core/access_services.py` (`LEGACY_GRANTS`)
- Modify: `server/suno/core/policies.py` (`__all__`, protocolos, políticas, `check_*`)
- Create: `server/suno/core/models/cost.py`
- Modify: `server/suno/core/models/__init__.py`
- Create (generada y completada a mano): `server/suno/core/migrations/0028_costs_ledger.py`
- Create: `server/suno/costs/__init__.py`, `server/suno/costs/tests/__init__.py` (vacíos), `server/suno/costs/tests/factories.py`
- Test: `server/suno/core/tests/test_access_matrix.py`, `server/suno/core/tests/test_policies.py`, `server/suno/core/tests/test_costs_migration.py`, `server/suno/costs/tests/test_models.py`

- [ ] **Step 1: Pruebas de la matriz**

Al final de `AccessCatalogTests` en `server/suno/core/tests/test_access_matrix.py`:

```python
    def test_costs_profiles_match_the_approved_design(self) -> None:
        """Costos, pieza 6a (spec, sección 5)."""
        c = Capability
        expected = {
            SectionRole.MANAGER: {
                c.CREATE, c.IMPORT, c.READ, c.UPDATE, c.DELETE,
                c.APPROVE, c.REJECT, c.REVERSE, c.EXPORT,
            },
            SectionRole.REVIEWER: {
                c.CREATE, c.READ, c.UPDATE, c.DELETE, c.APPROVE, c.REJECT, c.EXPORT,
            },
            SectionRole.CONTRIBUTOR: {c.CREATE, c.IMPORT, c.READ, c.UPDATE, c.DELETE, c.EXPORT},
            SectionRole.VIEWER: {c.READ, c.EXPORT},
        }  # fmt: skip
        self.assertEqual(allowed_roles(Section.COSTS), set(expected))
        for role, capabilities in expected.items():
            with self.subTest(role=role):
                self.assertEqual(capabilities_for(Section.COSTS, role), frozenset(capabilities))
        self.assertTrue(role_has_capability(Section.COSTS, SectionRole.MANAGER, "reverse"))
        self.assertFalse(role_has_capability(Section.BUDGETS, SectionRole.MANAGER, "reverse"))
```

- [ ] **Step 2: Pruebas de las políticas**

En `server/suno/core/tests/test_policies.py`:

1. Agregar a los imports `from suno.core.access import AccessScope, Section, SectionRole` (lo que falte) y, en el import de `suno.core.models`, `CostEntry` y `Project`.
2. Después de `def job(...)`, agregar:

```python
def project(org_id: uuid.UUID = ORG) -> Project:
    return Project(org_id=org_id)


def cost_entry(org_id: uuid.UUID = ORG, created_by_id: uuid.UUID = USER) -> CostEntry:
    return CostEntry(org_id=org_id, project_id=uuid.uuid4(), created_by_id=created_by_id)
```

3. En `NoRoleGrantsNothingTests._registry`, al final del diccionario:

```python
            # Costos, pieza 6a: libro de costos, por proyecto.
            "can_read_costs": project(),
            "can_create_costs": project(),
            "can_import_costs": project(),
            "can_export_costs": project(),
            "can_approve_costs": project(),
            "can_reject_costs": project(),
            "can_reverse_costs": project(),
            "can_update_cost": cost_entry(),
            "can_delete_cost": cost_entry(),
```

4. Al final del archivo:

```python
class CostPolicyTests(SimpleTestCase):
    """Costos, pieza 6a: matriz por rol, alcance por proyecto y "solo propios"."""

    def actor(
        self,
        role: str,
        *,
        scope: str = AccessScope.ORGANIZATION,
        projects: frozenset[uuid.UUID] = frozenset(),
    ) -> RequestContext:
        return RequestContext(
            user_id=USER,
            org_id=ORG,
            roles=frozenset({Role.SUPERVISOR}),
            section_access=(
                SectionAccess(
                    section=Section.COSTS, role=role, scope=scope, project_ids=projects
                ),
            ),
        )

    def test_legacy_profiles(self) -> None:
        for role in (Role.OWNER, Role.ADMIN):
            with self.subTest(role=role):
                self.assertTrue(policies.can_reverse_costs(ctx(role), project()))
        for role in (Role.SUPERVISOR, Role.FIELD_AGENT, Role.VIEWER):
            with self.subTest(role=role):
                self.assertFalse(policies.can_read_costs(ctx(role), project()))

    def test_role_matrix(self) -> None:
        site = project()
        #: crear, importar, aprobar, reversar, exportar
        cases = {
            SectionRole.MANAGER: (True, True, True, True, True),
            SectionRole.REVIEWER: (True, False, True, False, True),
            SectionRole.CONTRIBUTOR: (True, True, False, False, True),
            SectionRole.VIEWER: (False, False, False, False, True),
        }
        for role, expected in cases.items():
            actor = self.actor(role)
            with self.subTest(role=role):
                self.assertTrue(policies.can_read_costs(actor, site))
                self.assertEqual(
                    (
                        policies.can_create_costs(actor, site),
                        policies.can_import_costs(actor, site),
                        policies.can_approve_costs(actor, site),
                        policies.can_reverse_costs(actor, site),
                        policies.can_export_costs(actor, site),
                    ),
                    expected,
                )
                self.assertEqual(policies.can_reject_costs(actor, site), expected[2])

    def test_contributor_updates_and_deletes_only_its_own(self) -> None:
        own, other = cost_entry(created_by_id=USER), cost_entry(created_by_id=OTHER_USER)
        contributor = self.actor(SectionRole.CONTRIBUTOR)
        self.assertTrue(policies.can_update_cost(contributor, own))
        self.assertTrue(policies.can_delete_cost(contributor, own))
        self.assertFalse(policies.can_update_cost(contributor, other))
        self.assertFalse(policies.can_delete_cost(contributor, other))
        for role in (SectionRole.MANAGER, SectionRole.REVIEWER):
            with self.subTest(role=role):
                self.assertTrue(policies.can_update_cost(self.actor(role), other))
                self.assertTrue(policies.can_delete_cost(self.actor(role), other))
        self.assertFalse(policies.can_update_cost(self.actor(SectionRole.VIEWER), own))

    def test_project_scope(self) -> None:
        inside, outside = project(), project()
        actor = self.actor(
            SectionRole.MANAGER, scope=AccessScope.PROJECTS, projects=frozenset({inside.id})
        )
        self.assertTrue(policies.can_read_costs(actor, inside))
        self.assertFalse(policies.can_read_costs(actor, outside))
        entry = CostEntry(org_id=ORG, project_id=outside.id, created_by_id=USER)
        self.assertFalse(policies.can_update_cost(actor, entry))

    def test_check_variants_follow_rd11(self) -> None:
        with self.assertRaises(HttpError) as denied:
            policies.check_approve_costs(self.actor(SectionRole.VIEWER), project())
        self.assertEqual(denied.exception.status_code, 403)
        with self.assertRaises(HttpError) as hidden:
            policies.check_read_costs(ctx(Role.ADMIN, org_id=OTHER_ORG), project())
        self.assertEqual(hidden.exception.status_code, 404)
```

- [ ] **Step 3: Paquete y datos de prueba del libro**

Crear vacíos `server/suno/costs/__init__.py` y `server/suno/costs/tests/__init__.py` (como `suno/matrix/` y `suno/control/`: sin modelos ni `apps.py`).

`server/suno/costs/tests/factories.py` (lo usan todas las tareas siguientes):

```python
"""Datos de prueba del libro de costos. Las pruebas sí pueden usar `Model.objects`."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Max
from django.utils import timezone

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import AIU, V1_ROWS
from suno.core.access import AccessScope, Section
from suno.core.access_services import set_section_grant
from suno.core.models import (
    AppUser,
    Blob,
    Budget,
    BudgetItem,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Equipment,
    EquipmentOwnership,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    Task,
    TransportContractor,
    TransportMaterial,
    TransportRoute,
    TransportVehicle,
    Trip,
)
from suno.projects.tests.factories import FieldWorld, make_field_world

#: Un PDF mínimo: lo que se revisa son sus primeros bytes (`%PDF-`).
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
#: Una "imagen" PNG: la firma de 8 bytes y relleno.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
D = Decimal


def cost_world(name: str = "Constructora") -> tuple[FieldWorld, Budget]:
    """`make_field_world` con `samples.V1_ROWS` aplicado con AIU (Administración 10 % del
    costo directo de 9.130.000,00 = 913.000,00). Capítulo 1 (1.1, 1.2) y capítulo 2 (2.1,
    2.2, 2.4). El administrador es Gestor de Costos (grant de la migración y la señal)."""
    world = make_field_world(name)
    budget = make_budget(world.project, world.admin)
    apply_rows(world.ctx(), budget, V1_ROWS, charges=AIU)
    return world, budget


def item(budget: Budget, code: str) -> BudgetItem:
    return BudgetItem.objects.get(budget=budget, code=code)


def grant_costs(
    world: FieldWorld,
    user: AppUser,
    role: str,
    *,
    scope: str = AccessScope.ORGANIZATION,
    projects: Sequence[Project] = (),
) -> None:
    set_section_grant(
        world.ctx(),
        user_id=user.pk,
        section=Section.COSTS,
        role=role,
        scope=scope,
        project_ids=[p.pk for p in projects],
    )


def support_blob(seed: str = "soporte") -> Blob:
    """Un `Blob` registrado (sin bytes en el almacén): basta para las restricciones y el
    cálculo. Las pruebas de subida y descarga usan `pdf_upload` con un almacén temporal."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    blob, _ = Blob.objects.get_or_create(
        sha256=digest,
        defaults={
            "size": len(PDF),
            "mime": "application/pdf",
            "bucket_key": f"blobs/{digest[:2]}/{digest[2:4]}/{digest}",
        },
    )
    return blob


def pdf_upload(name: str = "factura.pdf", content: bytes = PDF) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/pdf")


def entry(
    world: FieldWorld,
    *,
    amount: str = "1000.00",
    day: date = date(2026, 3, 10),
    scope: str = CostScope.INDIRECT,
    status: str = CostStatus.DRAFT,
    items: Sequence[BudgetItem] = (),
    chapter: BudgetItem | None = None,
    task: Task | None = None,
    kind: str = CostKind.MATERIALS,
    created_by: AppUser | None = None,
    decided_by: AppUser | None = None,
    attachment: Blob | None = None,
    reverses: CostEntry | None = None,
    note: str = "",
    description: str = "Concreto premezclado",
) -> CostEntry:
    """Un movimiento escrito directo en la BD (sin el servicio): para el cálculo, el cargador
    y las restricciones. Aprobado: con soporte y aprobado por la supervisión."""
    author = created_by or world.admin
    decided = status != CostStatus.DRAFT
    approver = decided_by or (world.supervisor if decided else None)
    if attachment is None and status == CostStatus.APPROVED and reverses is None:
        attachment = support_blob()
    row = CostEntry.objects.create(
        org=world.org,
        project=world.project,
        incurred_on=day,
        kind=kind,
        description=description,
        amount=D(amount),
        document_type=CostDocumentType.INVOICE,
        scope=scope,
        chapter=chapter,
        task=task,
        status=status,
        created_by=author,
        updated_by=author,
        decided_by=approver,
        decided_at=timezone.now() if decided else None,
        decision_note=note or ("Sin soporte válido" if status == CostStatus.REJECTED else ""),
        attachment=attachment,
        reverses=reverses,
    )
    for budget_item in items:
        CostEntryItem.objects.create(org=world.org, entry=row, item=budget_item)
    return row


def equipment(
    world: FieldWorld, code: str = "RE-01", *, ownership: str = EquipmentOwnership.OWN
) -> Equipment:
    kind, _ = EquipmentType.objects.get_or_create(
        org=world.org, code="RET", defaults={"name": "Retroexcavadora"}
    )
    return Equipment.objects.create(
        org=world.org,
        code=code,
        name=f"Retroexcavadora {code}",
        equipment_type=kind,
        ownership=ownership,
        created_by=world.admin,
        updated_by=world.admin,
    )


def usage(
    world: FieldWorld,
    machine: Equipment,
    day: date,
    cost: str | None,
    *,
    budget_item: BudgetItem | None = None,
    status: str = EquipmentUsageStatus.COMPLETED,
    hours: str = "8.00",
) -> EquipmentUsage:
    """Un uso con su costo ya calculado (como lo deja `_create_usage`); `cost=None`: sin tarifa."""
    priced = cost is not None
    return EquipmentUsage.objects.create(
        org=world.org,
        equipment=machine,
        project=world.project,
        operator=world.agent,
        usage_date=day,
        start_time=time(7),
        end_time=time(15),
        activity="Excavación",
        status=status,
        budget_item=budget_item,
        billable_hours=D(hours) if priced else None,
        hourly_rate=(D(cost) / D(hours)).quantize(D("0.01")) if cost is not None else None,
        cost=D(cost) if cost is not None else None,
        created_by=world.admin,
        updated_by=world.admin,
    )


def trip(
    world: FieldWorld, day: date, amount: str | None, *, own: Equipment | None = None
) -> Trip:
    """Un viaje de 14 m³; de un vehículo de terceros salvo que se pase `own` (equipo propio)."""
    material, _ = TransportMaterial.objects.get_or_create(org=world.org, name="Gravas")
    route, _ = TransportRoute.objects.get_or_create(
        org=world.org, origin="Cantera", destination="Obra", defaults={"distance_km": D("8.5")}
    )
    vehicle = None
    if own is None:
        contractor, _ = TransportContractor.objects.get_or_create(
            org=world.org, name="Transportes TDR"
        )
        vehicle, _ = TransportVehicle.objects.get_or_create(
            org=world.org, plate="ABC123", defaults={"contractor": contractor}
        )
    number = (Trip.objects.filter(org=world.org).aggregate(top=Max("number"))["top"] or 0) + 1
    return Trip.objects.create(
        id=uuid.uuid4(),
        org=world.org,
        number=number,
        recorded_at=timezone.make_aware(datetime.combine(day, time(10))),
        equipment=own,
        vehicle=vehicle,
        project=world.project,
        route=route,
        distance_km=route.distance_km,
        material=material,
        volume_m3=D("14"),
        unit_price=(D(amount) / D("14")).quantize(D("0.01")) if amount is not None else None,
        amount=D(amount) if amount is not None else None,
        requested_by=world.agent,
        dispatched_by=world.supervisor,
        received_by=world.admin,
        created_by=world.admin,
    )
```

- [ ] **Step 4: Pruebas del modelo y de la migración**

`server/suno/costs/tests/test_models.py`:

```python
"""Restricciones de la base del libro de costos (spec, 1.2)."""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import CostEntry, CostScope, CostStatus
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import cost_world, entry, item

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class CostEntryConstraintTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()

    def broken(self, **fields: object) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry(self.world, **fields)  # type: ignore[arg-type]

    def test_amount_is_positive_except_in_a_reversal(self) -> None:
        self.broken(amount="0")
        self.broken(amount="-5.00")
        original = entry(self.world, status=CostStatus.APPROVED)
        reversal = entry(
            self.world,
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )
        self.assertIsNone(reversal.attachment_id)  # un reverso no pide soporte propio
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry(
                self.world,
                amount="1000.00",
                status=CostStatus.APPROVED,
                reverses=entry(self.world, status=CostStatus.APPROVED),
                decided_by=self.world.admin,
            )

    def test_an_entry_is_reversed_once(self) -> None:
        original = entry(self.world, status=CostStatus.APPROVED)
        entry(
            self.world,
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )
        self.broken(
            amount="-1000.00",
            status=CostStatus.APPROVED,
            reverses=original,
            decided_by=self.world.admin,
        )

    def test_an_approved_entry_needs_support_and_another_approver(self) -> None:
        draft = entry(self.world)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=draft.pk).update(
                status=CostStatus.APPROVED,
                decided_by=self.world.supervisor,
                decided_at=timezone.now(),
            )
        self.broken(status=CostStatus.APPROVED, decided_by=self.world.admin)
        self.assertEqual(entry(self.world, status=CostStatus.APPROVED).status, "approved")

    def test_a_rejection_needs_its_reason(self) -> None:
        rejected = entry(self.world, status=CostStatus.REJECTED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=rejected.pk).update(decision_note="")

    def test_a_draft_has_no_decision(self) -> None:
        self.broken(decided_by=self.world.supervisor)

    def test_scope_fields(self) -> None:
        chapter = item(self.budget, "2")
        self.broken(scope=CostScope.INDIRECT, chapter=chapter)
        self.broken(scope=CostScope.INDIRECT, task=self.world.task(3))
        self.broken(scope=CostScope.CHAPTER)
        self.broken(scope=CostScope.SELECTION)
        self.assertEqual(entry(self.world, scope=CostScope.CHAPTER, chapter=chapter).scope, "chapter")
```

`server/suno/core/tests/test_costs_migration.py`:

```python
"""La migración del libro de costos da Costos a la administración que ya existía."""

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

MIGRATION = import_module("suno.core.migrations.0028_costs_ledger")

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


class CostsGrantMappingTests(SimpleTestCase):
    def test_only_owner_and_admin_receive_costs(self) -> None:
        expected = (Section.COSTS, SectionRole.MANAGER, AccessScope.ORGANIZATION)
        self.assertEqual(MIGRATION.COSTS_GRANTS, {Role.OWNER: expected, Role.ADMIN: expected})


@skip_without_db
class CostsGrantBackfillTests(TestCase):
    def test_backfill_is_idempotent_and_only_for_administration(self) -> None:
        org = make_org("Previa")
        admin = make_user(org, username="admin-previa", role=Role.ADMIN)
        supervisor = make_user(org, username="super-previa", role=Role.SUPERVISOR)
        # Una membresía anterior a la migración no tenía la sección.
        SectionGrant.objects.filter(section=Section.COSTS).delete()
        MIGRATION.forwards_costs_grants(apps, None)
        MIGRATION.forwards_costs_grants(apps, None)
        grants = SectionGrant.objects.filter(section=Section.COSTS)
        self.assertEqual(
            list(grants.values_list("user_id", "role", "scope")),
            [(admin.pk, SectionRole.MANAGER, AccessScope.ORGANIZATION)],
        )
        self.assertFalse(grants.filter(user=supervisor).exists())
```

- [ ] **Step 5: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/core/tests/test_access_matrix.py suno/core/tests/test_policies.py`
Expected: FAIL/ERROR (`Section` no tiene `COSTS`; `CostEntry` no existe).

- [ ] **Step 6: Matriz, grants por defecto y políticas**

En `server/suno/core/access.py`:

```python
class Section(models.TextChoices):
    FIELD_RECORDS = "FIELD_RECORDS", "Registros de campo"
    PROJECTS = "PROJECTS", "Proyectos"
    BUDGETS = "BUDGETS", "Presupuestos"
    COSTS = "COSTS", "Costos"
    FORMS = "FORMS", "Formularios"
    ...
```

En `Capability`, después de `REJECT = "reject"`:

```python
    #: Anular un movimiento aprobado del libro de costos con otro de signo contrario.
    REVERSE = "reverse"
```

En `CAPABILITY_MATRIX`, después de la entrada `Section.BUDGETS`:

```python
    # Libro de costos (costos, pieza 6a, spec sección 5). "Solo propios" del Colaborador al
    # actualizar o borrar lo decide `policies.can_update_cost`/`can_delete_cost`.
    Section.COSTS: {
        SectionRole.MANAGER: _caps(
            Capability.CREATE,
            Capability.IMPORT,
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
            Capability.IMPORT,
            Capability.READ,
            Capability.UPDATE,
            Capability.DELETE,
            Capability.EXPORT,
        ),
        SectionRole.VIEWER: _caps(Capability.READ, Capability.EXPORT),
    },
```

En `server/suno/core/access_services.py`, en `LEGACY_GRANTS["OWNER"]` y `LEGACY_GRANTS["ADMIN"]`, después de la línea de `Section.BUDGETS`:

```python
        (Section.COSTS, SectionRole.MANAGER, AccessScope.ORGANIZATION),
```

En `server/suno/core/policies.py`:

1. En `__all__`, en orden alfabético: `"can_approve_costs"`, `"can_create_costs"`, `"can_delete_cost"`, `"can_export_costs"`, `"can_import_costs"`, `"can_read_costs"`, `"can_reject_costs"`, `"can_reverse_costs"`, `"can_update_cost"` y sus `check_*`.
2. Después de `class AuthoredResource(...)`:

```python
class ProjectResource(OrgScopedResource, Protocol):
    """Un proyecto ya resuelto: su `id` decide el alcance por proyectos."""

    id: UUID


class CostEntryResource(OrgScopedResource, Protocol):
    """Un movimiento del libro de costos ya resuelto (sin navegar relaciones)."""

    project_id: UUID
    created_by_id: UUID
```

3. Después de `can_manage_personal_data`:

```python
def _costs(ctx: RequestContext, org_id: UUID, project_id: UUID, capability: Capability) -> bool:
    """Libro de costos (costos, pieza 6a): capacidad de la sección Costos **en ese proyecto**,
    para que un acceso limitado a algunos proyectos solo valga en ellos."""
    return user_has_section_capability(
        ctx, org_id, Section.COSTS, capability, project_id=project_id
    )


def can_read_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Ver el libro de costos y el control de costo del proyecto: todos los roles de Costos."""
    return _costs(ctx, project.org_id, project.id, Capability.READ)


def can_create_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Registrar un costo en borrador: Gestor, Revisor y Colaborador."""
    return _costs(ctx, project.org_id, project.id, Capability.CREATE)


def can_import_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Importar costos desde Excel (en borrador): Gestor y Colaborador."""
    return _costs(ctx, project.org_id, project.id, Capability.IMPORT)


def can_export_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Exportar el libro a Excel (queda en la auditoría): todos los roles de Costos."""
    return _costs(ctx, project.org_id, project.id, Capability.EXPORT)


def can_approve_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Aprobar borradores: Gestor y Revisor. Que nadie apruebe lo suyo lo exige el servicio
    (y la base): esta política responde solo por el rol."""
    return _costs(ctx, project.org_id, project.id, Capability.APPROVE)


def can_reject_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Rechazar borradores, con motivo: Gestor y Revisor."""
    return _costs(ctx, project.org_id, project.id, Capability.REJECT)


def can_reverse_costs(ctx: RequestContext, project: ProjectResource) -> bool:
    """Reversar un costo aprobado: solo el Gestor."""
    return _costs(ctx, project.org_id, project.id, Capability.REVERSE)


def _own_or_any_cost(
    ctx: RequestContext, entry: CostEntryResource, capability: Capability
) -> bool:
    if not _costs(ctx, entry.org_id, entry.project_id, capability):
        return False
    return entry.created_by_id == ctx.user_id or _costs(
        ctx, entry.org_id, entry.project_id, Capability.APPROVE
    )


def can_update_cost(ctx: RequestContext, entry: CostEntryResource) -> bool:
    """Actualizar un borrador: Gestor y Revisor, el de cualquiera; Colaborador, solo los suyos.

    Lo aprobado nunca se actualiza: eso lo comprueba el servicio, que conoce el estado."""
    return _own_or_any_cost(ctx, entry, Capability.UPDATE)


def can_delete_cost(ctx: RequestContext, entry: CostEntryResource) -> bool:
    """Borrar un borrador: Gestor y Revisor, el de cualquiera; Colaborador, solo los suyos."""
    return _own_or_any_cost(ctx, entry, Capability.DELETE)
```

4. Al final del archivo:

```python
def check_read_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_read_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_create_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_create_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_import_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_import_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_export_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_export_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_approve_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_approve_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_reject_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_reject_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_reverse_costs(ctx: RequestContext, project: ProjectResource) -> None:
    if not can_reverse_costs(ctx, project):
        raise _denial(ctx, project.org_id)


def check_update_cost(ctx: RequestContext, entry: CostEntryResource) -> None:
    if not can_update_cost(ctx, entry):
        raise _denial(ctx, entry.org_id)


def check_delete_cost(ctx: RequestContext, entry: CostEntryResource) -> None:
    if not can_delete_cost(ctx, entry):
        raise _denial(ctx, entry.org_id)
```

- [ ] **Step 7: El modelo**

`server/suno/core/models/cost.py`:

```python
"""Libro de costos del proyecto (costos, pieza 6a).

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, sección 1.

- `CostEntry`: un movimiento del libro. Nace en borrador; se aprueba o se rechaza (con
  motivo). Solo cuenta lo aprobado. Lo aprobado no se edita ni se borra: se reversa con otro
  movimiento, aprobado y de signo contrario, en la fecha del reverso.
- `CostEntryItem`: los ítems de un movimiento `item` (uno) o `selection` (dos o más del mismo
  capítulo), por su identidad estable (`BudgetItem`), no por versión.

El valor lleva IVA incluido, como los precios del presupuesto.
"""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrgScopedModel


class CostKind(models.TextChoices):
    MATERIALS = "materials", "Materiales"
    LABOR = "labor", "Mano de obra"
    PAYROLL = "payroll", "Nómina"
    SUBCONTRACT = "subcontract", "Subcontrato"
    RENTED_EQUIPMENT = "rented_equipment", "Equipo alquilado"
    OTHER = "other", "Otros gastos"


class CostDocumentType(models.TextChoices):
    INVOICE = "invoice", "Factura"
    COLLECTION_ACCOUNT = "collection_account", "Cuenta de cobro"
    PAYROLL = "payroll", "Nómina"
    RECEIPT = "receipt", "Recibo"
    OTHER = "other", "Otro"


class CostScope(models.TextChoices):
    INDIRECT = "indirect", "Indirecto (proyecto)"
    ITEM = "item", "Un ítem"
    CHAPTER = "chapter", "Un capítulo completo"
    SELECTION = "selection", "Ítems de un capítulo"


class CostStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"


class CostEntry(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="proyecto",
    )
    #: Fecha de causación: define el periodo del control.
    incurred_on = models.DateField(verbose_name="fecha de causación")
    kind = models.CharField(max_length=24, choices=CostKind.choices, verbose_name="tipo")
    description = models.CharField(max_length=300, verbose_name="descripción")
    #: Con IVA incluido. Negativo solo en un reverso.
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    third_party = models.CharField(max_length=200, blank=True, verbose_name="tercero")
    document_type = models.CharField(
        max_length=24, choices=CostDocumentType.choices, verbose_name="tipo de documento"
    )
    document_number = models.CharField(max_length=80, blank=True, verbose_name="número")
    scope = models.CharField(max_length=12, choices=CostScope.choices, verbose_name="asignación")
    #: `chapter`/`selection`: el capítulo asignado. `item`: el capítulo padre del ítem en la
    #: versión vigente al registrarlo (su capítulo de origen). `indirect`: nulo.
    chapter = models.ForeignKey(
        "core.BudgetItem",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="capítulo",
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="CostEntryItem",
        related_name="cost_entries",
        verbose_name="ítems",
    )
    task = models.ForeignKey(
        "core.Task",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="tarea",
    )
    status = models.CharField(
        max_length=10,
        choices=CostStatus.choices,
        default=CostStatus.DRAFT,
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
    #: Motivo del rechazo o del reverso.
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    #: El movimiento que este anula. Uno a uno: un movimiento se reversa una sola vez.
    reverses = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal",
        verbose_name="reverso de",
    )
    #: Soporte (módulo de adjuntos). Obligatorio para aprobar; un reverso no lleva.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="soporte",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")

    class Meta:
        db_table = "cost_entry"
        ordering = ["-incurred_on", "-created_at"]
        verbose_name = "movimiento de costo"
        verbose_name_plural = "movimientos de costo"
        indexes = [
            models.Index(fields=["org", "project", "incurred_on"], name="cost_entry_day_idx"),
            models.Index(fields=["org", "project", "status"], name="cost_entry_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reverses__isnull=True, amount__gt=0)
                | models.Q(reverses__isnull=False, amount__lt=0),
                name="cost_entry_amount_sign",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status="draft", decided_by__isnull=True, decided_at__isnull=True
                )
                | models.Q(
                    status__in=["approved", "rejected"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="cost_entry_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="approved")
                | models.Q(reverses__isnull=False)
                | (
                    models.Q(attachment__isnull=False)
                    & ~models.Q(decided_by=models.F("created_by"))
                ),
                name="cost_entry_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="cost_entry_rejection_note",
            ),
            models.CheckConstraint(
                condition=models.Q(reverses__isnull=True) | models.Q(status="approved"),
                name="cost_entry_reversal_approved",
            ),
            models.CheckConstraint(
                condition=models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
                | models.Q(scope="item")
                | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False),
                name="cost_entry_scope_fields",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.incurred_on}:{self.description}"


class CostEntryItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(CostEntry, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey(
        "core.BudgetItem", on_delete=models.PROTECT, related_name="cost_links"
    )

    class Meta:
        db_table = "cost_entry_item"
        constraints = [
            models.UniqueConstraint(fields=["entry", "item"], name="cost_entry_item_uq"),
        ]
```

En `server/suno/core/models/__init__.py`: agregar `- \`cost\`     — \`CostEntry\`, \`CostEntryItem\` (costos, pieza 6a).` a la lista del docstring, el import

```python
from .cost import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
)
```

y esos seis nombres en `__all__` (en su orden alfabético).

- [ ] **Step 8: La migración**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name costs_ledger"
```

Expected: `server/suno/core/migrations/0028_costs_ledger.py` con, en este orden aproximado: `AlterField` de `sectiongrant.section` (opción `('COSTS', 'Costos')`), `RemoveConstraint`/`AddConstraint` de `section_grant_role_allowed` (con `('section', 'COSTS')`), `CreateModel` de `CostEntry` y de `CostEntryItem`, `AddField` de `costentry.items`, dos `AddIndex` y las siete restricciones. Revisar que no aparezca nada más (ningún modelo ajeno).

Completar el archivo a mano. Después de los imports:

```python
from django.utils import timezone

# Copia congelada: la migración no importa `suno.core.access_services`.
COSTS_GRANTS = {
    "OWNER": ("COSTS", "MANAGER", "organization"),
    "ADMIN": ("COSTS", "MANAGER", "organization"),
}


def forwards_costs_grants(apps, schema_editor):
    """La administración que ya existía recibe el libro de costos (como `LEGACY_GRANTS`)."""
    Membership = apps.get_model("core", "Membership")
    SectionGrant = apps.get_model("core", "SectionGrant")
    now = timezone.now()
    existing = set(
        SectionGrant.objects.filter(section="COSTS").values_list("org_id", "user_id")
    )
    grants = []
    for org_id, user_id, role in Membership.objects.filter(
        role__in=list(COSTS_GRANTS)
    ).values_list("org_id", "user_id", "role"):
        if (org_id, user_id) in existing:
            continue
        section, grant_role, scope = COSTS_GRANTS[role]
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


def reverse_costs_grants(apps, schema_editor):
    apps.get_model("core", "SectionProjectScope").objects.filter(grant__section="COSTS").delete()
    apps.get_model("core", "SectionGrant").objects.filter(section="COSTS").delete()
```

y como última operación de `operations`:

```python
        migrations.RunPython(forwards_costs_grants, reverse_costs_grants),
```

(Va al final: la restricción nueva tiene que existir antes de insertar grants de `COSTS`.)

- [ ] **Step 9: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/core suno/costs && ruff check suno && mypy suno/core suno/costs --config-file pyproject.toml && python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh suno/core suno/costs suno/budgets suno/equipment
```

Expected: PASS y `No changes detected` (la matriz de accesos, `test_models.py` de `core` con `CostEntryItem` con `org`, el aislamiento de `test_scoping.py` y las pruebas de equipos que crean administradores siguen verdes). Si `ruff format` deshace el `# fmt: skip` de la prueba de la matriz, dejarlo como lo formatee.

- [ ] **Step 10: Commit**

```bash
git add server/suno/core server/suno/costs
git commit -m "feat(costs): cost ledger models, costs section and policies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Soporte subido desde la web al almacén de adjuntos

**Files:**
- Modify: `server/suno/attachments/blobstore.py` (`BlobStore`: `put_blob`)
- Modify: `server/suno/attachments/localfs.py`, `server/suno/attachments/s3.py` (`put_blob`)
- Modify: `server/suno/attachments/services.py` (`register_server_upload` y `__all__`)
- Create: `server/suno/attachments/web.py`
- Test: `server/suno/attachments/tests/test_web_upload.py`

El flujo del teléfono (presign → `PUT` → commit) no sirve para un formulario web. Hoy solo los mapas base y las capas suben archivos desde el servidor (`put_basemap`, `put_layer`); esta tarea agrega lo mismo para los adjuntos (`blobs/…`) y una función que valida, sube y registra el `Blob`.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/attachments/tests/test_web_upload.py`:

```python
"""Soportes subidos desde la web (costos, pieza 6a): `put_blob` y `store_upload`."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from suno.attachments.blobstore import bucket_key_for, checksum_header_value
from suno.attachments.localfs import LocalFsBlobStore
from suno.attachments.s3 import S3BlobStore, S3Config
from suno.attachments.storage import get_blob_store
from suno.attachments.web import MAX_BYTES, UploadRejected, sniff_mime, store_upload
from suno.core.models import Blob
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import PDF, PNG, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

PDF_SHA = hashlib.sha256(PDF).hexdigest()


class PutBlobTests(SimpleTestCase):
    def test_localfs_keeps_bytes_and_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as work:
            source = Path(work) / "soporte.pdf"
            source.write_bytes(PDF)
            store = LocalFsBlobStore(root)
            store.put_blob(
                sha256=PDF_SHA, path=str(source), size=len(PDF), mime="application/pdf"
            )
            head = store.head(PDF_SHA)
        self.assertEqual(
            (head.size, head.sha256, head.content_type), (len(PDF), PDF_SHA, "application/pdf")
        )

    def test_s3_puts_with_checksum_and_type(self) -> None:
        client = mock.MagicMock()
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "soporte.pdf"
            source.write_bytes(PDF)
            S3BlobStore(S3Config(bucket="suno"), client=client).put_blob(
                sha256=PDF_SHA, path=str(source), size=len(PDF), mime="application/pdf"
            )
        kwargs = client.put_object.call_args.kwargs
        self.assertEqual(kwargs["Key"], bucket_key_for(PDF_SHA))
        self.assertEqual(kwargs["ContentType"], "application/pdf")
        self.assertEqual(kwargs["ChecksumSHA256"], checksum_header_value(PDF_SHA))

    def test_sniff_mime(self) -> None:
        self.assertEqual(sniff_mime(PDF[:16]), "application/pdf")
        self.assertEqual(sniff_mime(PNG[:16]), "image/png")
        self.assertIsNone(sniff_mime(b"MZ\x90\x00"))


class _Huge:
    """Un archivo que dice pesar más del límite (no se llega a leer)."""

    size = MAX_BYTES + 1

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]:
        yield b""


@skip_without_db
class StoreUploadTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

    def test_stores_registers_and_deduplicates(self) -> None:
        blob = store_upload(pdf_upload())
        self.assertEqual(
            (blob.sha256, blob.mime, blob.size), (PDF_SHA, "application/pdf", len(PDF))
        )
        self.assertEqual(get_blob_store().head(PDF_SHA).sha256, PDF_SHA)
        self.assertEqual(store_upload(pdf_upload("copia.pdf")).pk, blob.pk)
        self.assertEqual(Blob.objects.filter(pk=PDF_SHA).count(), 1)

    def test_rejects_other_types_empty_and_huge_files(self) -> None:
        for upload in (
            pdf_upload("virus.exe", b"MZ\x90\x00" + b"\x00" * 40),
            pdf_upload("vacio.pdf", b""),
            _Huge(),
        ):
            with self.subTest(upload=upload), self.assertRaises(UploadRejected):
                store_upload(upload)
        self.assertFalse(Blob.objects.exists())
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/attachments/tests/test_web_upload.py`
Expected: ERROR de import (`No module named 'suno.attachments.web'`).

- [ ] **Step 3: `put_blob` en el puerto y los adaptadores**

En `server/suno/attachments/blobstore.py`, al final de `class BlobStore(Protocol)`:

```python
    # --- soportes subidos desde la web (costos, pieza 6a) -----------------------

    def put_blob(self, *, sha256: str, path: str, size: int, mime: str) -> None:
        """Sube un adjunto **desde el servidor** a su clave de siempre (`bucket_key_for`).

        Como `put_layer`: el store verifica el `sha256` al recibirlo, y guarda el
        `Content-Type` para que el `HEAD` y la descarga lo devuelvan.
        """
        ...
```

En `server/suno/attachments/localfs.py`, antes de `# --- interno ---`:

```python
    # --- soportes subidos desde la web (costos, pieza 6a) -----------------------

    def put_blob(self, *, sha256: str, path: str, size: int, mime: str) -> None:
        digest = normalize_sha256(sha256)
        self._put_verified(bucket_key_for(digest), sha256=digest, path=path, size=size)
        target = self._path(digest)
        target.with_name(target.name + _META_SUFFIX).write_text(
            json.dumps({"content_type": mime}), encoding="utf-8"
        )
```

En `server/suno/attachments/s3.py`, antes de `# --- archivos del servidor (mapas base, capas) ---`:

```python
    # --- soportes subidos desde la web (costos, pieza 6a) -----------------------

    def put_blob(self, *, sha256: str, path: str, size: int, mime: str) -> None:
        self._put_verified(
            bucket_key_for(sha256), sha256=sha256, path=path, size=size, mime=mime
        )
```

En `server/suno/attachments/services.py`, agregar `"register_server_upload",` a `__all__` y, después de `_register`:

```python
def register_server_upload(digest: str, *, size: int, mime: str) -> CommitResult:
    """Registra el `Blob` de un archivo que el **servidor** ya subió y verificó
    (`attachments.web.store_upload`). Mismo `INSERT … ON CONFLICT DO NOTHING` que el commit."""
    return _register(normalize_sha256(digest), size=size, mime=mime)
```

- [ ] **Step 4: `store_upload`**

`server/suno/attachments/web.py`:

```python
"""Soportes subidos desde la plataforma web (costos, pieza 6a).

El flujo del teléfono (presign → `PUT` → commit) no sirve en un formulario web: aquí el
archivo llega al servidor, se reconoce por sus primeros bytes (la misma lista de tipos que los
adjuntos de campo), se calcula su `sha256`, se sube con `BlobStore.put_blob` y se registra el
`Blob` con el mismo `INSERT … ON CONFLICT DO NOTHING` del commit.

**No autoriza**: `Blob` no tiene organización. Quien llama ya comprobó que el usuario puede
escribir el objeto al que lo va a vincular (un movimiento del libro de costos).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from typing import Protocol

from suno.attachments import validation
from suno.attachments.blobstore import BlobStore
from suno.attachments.services import register_server_upload
from suno.attachments.storage import get_blob_store
from suno.core.models import Blob
from suno.core.scoping import blob_registry
from suno.serverinfo import capabilities as caps

__all__ = ["ACCEPT", "MAX_BYTES", "Upload", "UploadRejected", "sniff_mime", "store_upload"]

MAX_BYTES = caps.ATTACHMENT_MAX_BYTES
#: Para el `accept` del `<input type="file">`.
ACCEPT = "application/pdf,image/jpeg,image/png,image/webp"


class Upload(Protocol):
    """Lo que se usa de un `UploadedFile` de Django."""

    size: int | None

    def chunks(self, chunk_size: int | None = None) -> Iterator[bytes]: ...


class UploadRejected(ValueError):
    """El archivo no se acepta; el mensaje es para el usuario."""


def sniff_mime(prefix: bytes) -> str | None:
    """El tipo permitido con el que empiezan los bytes, o `None`."""
    for mime in validation.allowed_mime_types():
        if validation.magic_bytes_match(prefix, mime):
            return str(mime)
    return None


def store_upload(upload: Upload, *, store: BlobStore | None = None) -> Blob:
    size = upload.size or 0
    if size <= 0:
        raise UploadRejected("El soporte está vacío.")
    if size > MAX_BYTES:
        raise UploadRejected(f"El soporte pesa más de {MAX_BYTES // (1024 * 1024)} MB.")
    digest = hashlib.sha256()
    head = b""
    handle, path = tempfile.mkstemp(prefix="suno-soporte-")
    try:
        with os.fdopen(handle, "wb") as target:
            for chunk in upload.chunks():
                if len(head) < validation.SNIFF_LENGTH:
                    head += chunk[: validation.SNIFF_LENGTH - len(head)]
                digest.update(chunk)
                target.write(chunk)
        mime = sniff_mime(head)
        if mime is None:
            raise UploadRejected("El soporte debe ser un PDF o una imagen JPG, PNG o WebP.")
        sha256 = digest.hexdigest()
        existing = blob_registry().filter(pk=sha256).first()
        if existing is not None:
            return existing
        (store or get_blob_store()).put_blob(sha256=sha256, path=path, size=size, mime=mime)
        return register_server_upload(sha256, size=size, mime=mime).blob
    finally:
        os.unlink(path)
```

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/attachments && ruff check suno/attachments && mypy suno/attachments --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/attachments suno/basemaps suno/layers
```

Expected: PASS (presign, commit, GC, mapas base y capas siguen igual).

- [ ] **Step 6: Commit**

```bash
git add server/suno/attachments
git commit -m "feat(attachments): store supports uploaded from the web

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Catálogo de asignación y servicios del libro

**Files:**
- Create: `server/suno/costs/catalog.py`, `server/suno/costs/access.py`, `server/suno/costs/services.py`
- Modify: `server/suno/core/audit.py` (`COST_*`)
- Test: `server/suno/costs/tests/test_services.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/costs/tests/test_services.py`:

```python
"""Servicios del libro de costos (spec, 1.2 y 5): asignación, ciclo, autoaprobación, reverso."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from suno.authn.tests.factories import make_user
from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import AIU, V2_ROWS
from suno.core.access import AccessScope, SectionRole
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    CostDocumentType,
    CostEntry,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.costs.services import CostRejected, EntryData

from .factories import cost_world, entry, grant_costs, item, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class CostServiceTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        self.project = self.world.project
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.concrete = item(self.budget, "2.1")

    def data(self, **overrides: Any) -> EntryData:
        values: dict[str, Any] = {
            "incurred_on": date(2026, 3, 10),
            "kind": CostKind.MATERIALS,
            "description": "Concreto premezclado",
            "amount": D("1500000.00"),
            "third_party": "Concretos SA",
            "document_type": CostDocumentType.INVOICE,
            "document_number": "F-101",
            "scope": CostScope.ITEM,
            "item_ids": (self.concrete.pk,),
        }
        values.update(overrides)
        return EntryData(**values)

    def create(
        self, user: AppUser | None = None, *, support: bool = True, **overrides: Any
    ) -> CostEntry:
        return services.create_draft(
            self.world.ctx(user),
            self.project,
            self.data(**overrides),
            upload=pdf_upload() if support else None,
            name="factura.pdf" if support else "",
        )

    def test_create_item_draft_keeps_origin_chapter_items_and_audit(self) -> None:
        cost = self.create(task_id=self.world.task(5).pk)
        self.assertEqual(cost.status, CostStatus.DRAFT)
        self.assertEqual(cost.chapter_id, item(self.budget, "2").pk)
        self.assertEqual(list(cost.items.values_list("code", flat=True)), ["2.1"])
        self.assertEqual(cost.attachment_name, "factura.pdf")
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.COST_CREATED, target=f"cost:{cost.pk}"
            ).exists()
        )

    def test_assignment_rules(self) -> None:
        other, other_budget = cost_world("Otra constructora")
        chapter = item(self.budget, "2").pk
        steel = item(self.budget, "2.2").pk
        cases: dict[str, dict[str, Any]] = {
            "ítem de otro proyecto": {"item_ids": (item(other_budget, "2.1").pk,)},
            "capítulo como ítem": {"item_ids": (chapter,)},
            "selección de dos capítulos": {
                "scope": CostScope.SELECTION,
                "chapter_id": chapter,
                "item_ids": (self.concrete.pk, item(self.budget, "1.1").pk),
            },
            "selección de un ítem": {
                "scope": CostScope.SELECTION,
                "chapter_id": chapter,
                "item_ids": (self.concrete.pk,),
            },
            "tarea ajena": {"task_id": other.task(5).pk},
            "tarea resumen": {"task_id": self.world.task(4).pk},
            "indirecto con tarea": {
                "scope": CostScope.INDIRECT,
                "item_ids": (),
                "task_id": self.world.task(5).pk,
            },
            "fecha futura": {"incurred_on": timezone.localdate().replace(year=2100)},
            "valor cero": {"amount": D("0")},
            "valor con tres decimales": {"amount": D("10.005")},
            "sin descripción": {"description": "  "},
        }
        for name, overrides in cases.items():
            with self.subTest(name), self.assertRaises(CostRejected):
                self.create(**overrides)
        selection = self.create(
            scope=CostScope.SELECTION, chapter_id=chapter, item_ids=(self.concrete.pk, steel)
        )
        self.assertEqual(selection.items.count(), 2)
        whole = self.create(scope=CostScope.CHAPTER, chapter_id=chapter, item_ids=())
        self.assertEqual(whole.chapter_id, chapter)
        self.assertEqual(self.create(scope=CostScope.INDIRECT, item_ids=()).scope, "indirect")

    def test_an_item_retired_from_the_current_version_is_rejected(self) -> None:
        apply_rows(self.world.ctx(), self.budget, V2_ROWS, charges=AIU)  # quita el 1.2
        with self.assertRaises(CostRejected) as rejected:
            self.create(item_ids=(item(self.budget, "1.2").pk,))
        self.assertIn("versión vigente", rejected.exception.errors[0])

    def test_approval_needs_support_and_someone_else(self) -> None:
        reviewer = self.world.ctx(self.world.supervisor)
        without = self.create(support=False)
        with self.assertRaises(CostRejected):
            services.approve(reviewer, self.project, without.pk)
        cost = self.create()
        with self.assertRaises(CostRejected) as own:
            services.approve(self.world.ctx(), self.project, cost.pk)
        self.assertIn("Nadie aprueba", own.exception.errors[0])
        approved = services.approve(reviewer, self.project, cost.pk)
        self.assertEqual(
            (approved.status, approved.decided_by_id), ("approved", self.world.supervisor.pk)
        )
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.COST_APPROVED).exists())

    def test_the_last_editor_does_not_decide(self) -> None:
        cost = self.create()
        reviewer = self.world.ctx(self.world.supervisor)
        services.update_draft(reviewer, self.project, cost.pk, self.data(amount=D("9.00")))
        with self.assertRaises(CostRejected):
            services.approve(reviewer, self.project, cost.pk)
        third = make_user(self.world.org, username="otro-revisor", role=Role.VIEWER)
        grant_costs(self.world, third, SectionRole.REVIEWER)
        approved = services.approve(self.world.ctx(third), self.project, cost.pk)
        self.assertEqual(approved.status, "approved")

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        cost = self.create(self.contributor)
        reviewer = self.world.ctx(self.world.supervisor)
        with self.assertRaises(CostRejected):
            services.reject(reviewer, self.project, cost.pk, "  ")
        rejected = services.reject(reviewer, self.project, cost.pk, "Falta el NIT")
        self.assertEqual((rejected.status, rejected.decision_note), ("rejected", "Falta el NIT"))
        with self.assertRaises(PermissionDenied):  # solo su autor lo corrige
            services.update_draft(self.world.ctx(), self.project, cost.pk, self.data())
        again = services.update_draft(
            self.world.ctx(self.contributor),
            self.project,
            cost.pk,
            self.data(third_party="NIT 900"),
        )
        self.assertEqual(
            (again.status, again.decided_by_id, again.decision_note), ("draft", None, "")
        )
        event = AuditEvent.objects.get(action=AuditAction.COST_UPDATED)
        self.assertTrue(event.metadata["resubmitted"])

    def test_roles(self) -> None:
        mine = self.create(self.contributor)
        theirs = self.create()
        contributor = self.world.ctx(self.contributor)
        with self.assertRaises(PermissionDenied):
            services.update_draft(contributor, self.project, theirs.pk, self.data())
        with self.assertRaises(PermissionDenied):
            services.delete_draft(contributor, self.project, theirs.pk)
        with self.assertRaises(PermissionDenied):
            services.approve(contributor, self.project, theirs.pk)
        with self.assertRaises(PermissionDenied):
            self.create(self.viewer)
        services.delete_draft(contributor, self.project, mine.pk)
        self.assertFalse(CostEntry.objects.filter(pk=mine.pk).exists())
        deleted = AuditEvent.objects.get(action=AuditAction.COST_DELETED)
        self.assertEqual(deleted.metadata["description"], "Concreto premezclado")

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(
            self.world,
            scoped,
            SectionRole.CONTRIBUTOR,
            scope=AccessScope.PROJECTS,
            projects=(elsewhere,),
        )
        with self.assertRaises(PermissionDenied):
            self.create(scoped)

    def test_approved_is_neither_edited_nor_deleted(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        with self.assertRaises(CostRejected):
            services.update_draft(self.world.ctx(), self.project, cost.pk, self.data())
        with self.assertRaises(CostRejected):
            services.delete_draft(self.world.ctx(), self.project, cost.pk)

    def test_reverse_once_on_its_own_date(self) -> None:
        cost = entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=item(self.budget, "2"),
        )
        manager = self.world.ctx()
        with self.assertRaises(PermissionDenied):  # el Revisor no reversa
            services.reverse(self.world.ctx(self.world.supervisor), self.project, cost.pk, "x")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, cost.pk, " ")
        reversal = services.reverse(manager, self.project, cost.pk, "Factura duplicada")
        self.assertEqual(reversal.amount, D("-1000.00"))
        self.assertEqual(reversal.incurred_on, timezone.localdate())
        self.assertEqual((reversal.status, reversal.reverses_id), ("approved", cost.pk))
        self.assertEqual(reversal.decision_note, "Factura duplicada")
        self.assertEqual(list(reversal.items.all()), [self.concrete])
        self.assertTrue(reversal.description.startswith("Reverso: "))
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, cost.pk, "Otra vez")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, reversal.pk, "Reverso del reverso")
        with self.assertRaises(CostRejected):
            services.reverse(manager, self.project, entry(self.world).pk, "Borrador")
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.COST_REVERSED).exists())

    def test_support_url_after_reading_permission(self) -> None:
        cost = self.create()
        url = services.support_url(self.world.ctx(self.viewer), self.project, cost)
        self.assertIn(str(cost.attachment_id), url)

    def test_catalog(self) -> None:
        catalog = load_catalog(self.world.ctx(), self.project)
        found = catalog.by_label("2.1 Concreto zapatas")
        self.assertIsNotNone(found)
        self.assertEqual(found.item_id if found else None, self.concrete.pk)
        self.assertEqual(
            {row.code for row in catalog.items_under(item(self.budget, "2").pk)},
            {"2.1", "2.2", "2.4"},
        )
        self.assertEqual([row.code for row in catalog.chapters()], ["1", "2"])
        self.assertEqual(len(catalog.by_code("2.1")), 1)
        tasks = assignable_tasks(self.world.ctx(), self.project)
        self.assertNotIn(self.world.task(4).pk, tasks)  # resumen
        self.assertIn(self.world.task(5).pk, tasks)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_services.py`
Expected: ERROR de import (`No module named 'suno.costs.services'`).

- [ ] **Step 3: Códigos de auditoría**

En `server/suno/core/audit.py`, después de `CONTROL_EXPORTED`:

```python
    # Libro de costos (costos, pieza 6a).
    COST_CREATED = "cost.created"
    COST_UPDATED = "cost.updated"
    COST_DELETED = "cost.deleted"
    COST_APPROVED = "cost.approved"
    COST_REJECTED = "cost.rejected"
    COST_REVERSED = "cost.reversed"
    COST_IMPORTED = "cost.imported"
    COST_EXPORTED = "cost.exported"
```

- [ ] **Step 4: El catálogo**

`server/suno/costs/catalog.py`:

```python
"""Catálogo de asignación del libro de costos (costos, pieza 6a).

Las filas (ítems y capítulos) de la versión vigente de cada presupuesto del proyecto, con la
misma etiqueta que usa el control ("2.1 Concreto zapatas", y " · presupuesto" si el proyecto
tiene más de uno). Dos consultas, más una para las tareas asignables.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from uuid import UUID

from suno.core.context import RequestContext
from suno.core.models import BudgetItemVersion, BudgetRowKind, Project, Task
from suno.core.scoping import scoped_qs
from suno.matrix.importer import current_versions


@dataclass(frozen=True)
class CatalogRow:
    item_id: UUID
    code: str
    description: str
    kind: str
    parent_id: UUID | None
    budget_id: UUID
    budget: str
    label: str


@dataclass(frozen=True)
class Catalog:
    #: En el orden del presupuesto (por nombre de presupuesto y posición en el Excel).
    rows: Mapping[UUID, CatalogRow]

    @cached_property
    def _children(self) -> dict[UUID, list[UUID]]:
        children: dict[UUID, list[UUID]] = defaultdict(list)
        for row in self.rows.values():
            if row.parent_id is not None:
                children[row.parent_id].append(row.item_id)
        return children

    @cached_property
    def _labels(self) -> dict[str, CatalogRow]:
        return {row.label: row for row in self.items()}

    def items(self) -> list[CatalogRow]:
        return [row for row in self.rows.values() if row.kind == BudgetRowKind.ITEM]

    def chapters(self) -> list[CatalogRow]:
        return [row for row in self.rows.values() if row.kind == BudgetRowKind.CHAPTER]

    def below(self, chapter_id: UUID) -> list[UUID]:
        """Todo lo que cuelga del capítulo, a cualquier profundidad (sin él)."""
        found: list[UUID] = []
        seen = {chapter_id}
        pending = list(self._children.get(chapter_id, ()))
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            found.append(current)
            pending.extend(self._children.get(current, ()))
        return found

    def descendants(self, chapter_id: UUID) -> frozenset[UUID]:
        """Los ítems del capítulo, a cualquier profundidad."""
        return frozenset(
            i for i in self.below(chapter_id) if self.rows[i].kind == BudgetRowKind.ITEM
        )

    def items_under(self, chapter_id: UUID) -> list[CatalogRow]:
        inside = self.descendants(chapter_id)
        return [row for row in self.items() if row.item_id in inside]

    def by_label(self, label: str) -> CatalogRow | None:
        return self._labels.get(label.strip())

    def by_code(self, code: str) -> list[CatalogRow]:
        wanted = code.strip().casefold()
        return [row for row in self.rows.values() if row.code.casefold() == wanted]

    def label_of(self, item_id: UUID | None, fallback: str = "") -> str:
        row = self.rows.get(item_id) if item_id is not None else None
        return row.label if row is not None else fallback


def load_catalog(ctx: RequestContext, project: Project) -> Catalog:
    versions = current_versions(ctx, project)
    if not versions:
        return Catalog({})
    several = len(versions) > 1
    rows: dict[UUID, CatalogRow] = {}
    for item_id, code, description, kind, parent_id, budget_id, budget in (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version__in=versions)
        .order_by("version__budget__name", "sort_order")
        .values_list(
            "item_id",
            "item__code",
            "description",
            "kind",
            "parent_id",
            "version__budget_id",
            "version__budget__name",
        )
    ):
        text = f"{code} {description}".strip()
        rows[item_id] = CatalogRow(
            item_id=item_id,
            code=code,
            description=description,
            kind=kind,
            parent_id=parent_id,
            budget_id=budget_id,
            budget=budget,
            label=f"{text} · {budget}" if several else text,
        )
    return Catalog(rows)


def assignable_tasks(ctx: RequestContext, project: Project) -> dict[UUID, Task]:
    """Las tareas a las que se puede asignar un costo: hojas vigentes (ni resumen ni retiradas)."""
    return {
        task.pk: task
        for task in scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True, is_summary=False)
        .order_by("sort_order")
    }
```

- [ ] **Step 5: Acceso**

`server/suno/costs/access.py`:

```python
"""Quién ve el libro de costos y cómo se resuelven el proyecto y el movimiento de la URL."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from suno.core.access import Section
from suno.core.context import RequestContext
from suno.core.models import CostEntry, Project
from suno.core.policies import can_read_costs
from suno.core.scoping import scoped_qs
from suno.projects.access import get_visible_project


def get_costs_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404 (RD-11): sin la sección Costos, **antes** de mirar el proyecto; si es
    de otra organización o no se ve; o si el alcance de Costos no lo incluye."""
    if not ctx.has_section_access(Section.COSTS):
        raise Http404
    project = get_visible_project(ctx, project_id)
    if not can_read_costs(ctx, project):
        raise Http404
    return project


def get_entry(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    entry = (
        scoped_qs(ctx, CostEntry)
        .select_related("created_by", "updated_by", "decided_by", "chapter", "task", "reverses")
        .filter(pk=entry_id, project=project)
        .first()
    )
    if entry is None:
        raise Http404
    return entry
```

- [ ] **Step 6: Los servicios**

`server/suno/costs/services.py`:

```python
"""Libro de costos del proyecto (costos, pieza 6a): registro, aprobación y reverso.

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, secciones 1, 2 y 5.

- Todo se escribe en `transaction.atomic`, con `scoped_qs` y la auditoría en la misma
  transacción.
- Solo cuenta lo aprobado. Lo aprobado no se edita ni se borra: se reversa.
- La asignación se valida al registrar contra la versión vigente de cada presupuesto del
  proyecto (`catalog.load_catalog`).
- Nadie aprueba ni rechaza lo que registró o modificó por última vez.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import money
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    Blob,
    BudgetRowKind,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Task,
)
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_read_costs,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import blob_registry, scoped_qs

from .catalog import Catalog, assignable_tasks, load_catalog

FIRST_DAY = date(2000, 1, 1)
MAX_AMOUNT = Decimal("9999999999999999.99")
MAX_DESCRIPTION = 300
MAX_THIRD_PARTY = 200
MAX_DOCUMENT_NUMBER = 80
MAX_NOTE = 500
MAX_FILE_NAME = 200
SELF_DECISION = (
    "Nadie aprueba ni rechaza un costo que registró o modificó: pídeselo a otra persona."
)
APPROVED_IS_FINAL = "Un costo aprobado no se cambia ni se borra: se reversa."


class CostRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0])


@dataclass(frozen=True)
class EntryData:
    incurred_on: date
    kind: str
    description: str
    amount: Decimal
    third_party: str
    document_type: str
    document_number: str
    scope: str
    item_ids: tuple[UUID, ...] = ()
    chapter_id: UUID | None = None
    task_id: UUID | None = None


@dataclass(frozen=True)
class Assignment:
    scope: str
    item_ids: tuple[UUID, ...] = ()
    #: El capítulo asignado o, en un `item`, el capítulo padre del ítem (su origen).
    chapter_id: UUID | None = None
    task_id: UUID | None = None


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _field_errors(data: EntryData, today: date) -> list[str]:
    errors: list[str] = []
    if not FIRST_DAY <= data.incurred_on <= today:
        errors.append("La fecha de causación no puede ser futura ni anterior a 2000.")
    if data.kind not in CostKind.values:
        errors.append("Elige el tipo de costo.")
    if data.document_type not in CostDocumentType.values:
        errors.append("Elige el tipo de documento.")
    description = data.description.strip()
    if not description:
        errors.append("Escribe la descripción.")
    elif len(description) > MAX_DESCRIPTION:
        errors.append(f"La descripción pasa de {MAX_DESCRIPTION} caracteres.")
    if not 0 < data.amount <= MAX_AMOUNT:
        errors.append("El valor debe ser mayor que cero.")
    elif money(data.amount) != data.amount:
        errors.append("El valor va en pesos y centavos (dos decimales).")
    if len(data.third_party.strip()) > MAX_THIRD_PARTY:
        errors.append(f"El tercero pasa de {MAX_THIRD_PARTY} caracteres.")
    if len(data.document_number.strip()) > MAX_DOCUMENT_NUMBER:
        errors.append(f"El número de documento pasa de {MAX_DOCUMENT_NUMBER} caracteres.")
    return errors


def _assignment(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: EntryData
) -> tuple[list[str], Assignment | None]:
    scope = data.scope
    if scope not in CostScope.values:
        return ["Elige a qué se asigna el costo."], None
    if scope == CostScope.INDIRECT:
        if data.item_ids or data.chapter_id or data.task_id:
            return ["Un costo indirecto va al proyecto: sin ítem, capítulo ni tarea."], None
        return [], Assignment(scope)
    errors: list[str] = []
    task_id = data.task_id
    if task_id is not None and task_id not in tasks:
        errors.append("La tarea no es una tarea vigente del proyecto.")
    if scope == CostScope.ITEM:
        row = catalog.rows.get(data.item_ids[0]) if len(data.item_ids) == 1 else None
        if row is None or row.kind != BudgetRowKind.ITEM:
            errors.append(
                "Elige un ítem que esté como ítem en la versión vigente de su presupuesto."
            )
            return errors, None
        return errors, Assignment(scope, (row.item_id,), row.parent_id, task_id)
    chapter = catalog.rows.get(data.chapter_id) if data.chapter_id is not None else None
    if chapter is None or chapter.kind != BudgetRowKind.CHAPTER:
        errors.append("Elige un capítulo de la versión vigente del presupuesto.")
        return errors, None
    if scope == CostScope.CHAPTER:
        if data.item_ids:
            errors.append("Un capítulo completo no lleva ítems sueltos.")
        return errors, Assignment(scope, (), chapter.item_id, task_id)
    picked = tuple(dict.fromkeys(data.item_ids))
    inside = catalog.descendants(chapter.item_id)
    if len(picked) < 2 or any(item_id not in inside for item_id in picked):
        errors.append("Una selección lleva dos o más ítems del mismo capítulo.")
        return errors, None
    return errors, Assignment(scope, picked, chapter.item_id, task_id)


def validate_entry(
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    data: EntryData,
    *,
    today: date | None = None,
) -> tuple[list[str], Assignment | None]:
    """Errores para el usuario y, si no hay, la asignación lista para guardar."""
    errors = _field_errors(data, today or timezone.localdate())
    more, assignment = _assignment(catalog, tasks, data)
    errors.extend(more)
    return errors, None if errors else assignment


def entry_fields(data: EntryData, assignment: Assignment) -> dict[str, Any]:
    return {
        "incurred_on": data.incurred_on,
        "kind": data.kind,
        "description": data.description.strip(),
        "amount": data.amount,
        "third_party": data.third_party.strip(),
        "document_type": data.document_type,
        "document_number": data.document_number.strip(),
        "scope": assignment.scope,
        "chapter_id": assignment.chapter_id,
        "task_id": assignment.task_id,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: EntryData,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
) -> Assignment:
    errors, assignment = validate_entry(
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
        data,
    )
    if assignment is None:
        raise CostRejected(errors)
    return assignment


def _stored(upload: Upload | None) -> Blob | None:
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise CostRejected([str(exc)]) from exc


def _link_items(ctx: RequestContext, entry: CostEntry, item_ids: Sequence[UUID]) -> None:
    scoped_qs(ctx, CostEntryItem).filter(entry=entry).delete()
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id) for item_id in item_ids]
    )


def _metadata(entry: CostEntry) -> dict[str, Any]:
    return {
        "project": str(entry.project_id),
        "incurred_on": entry.incurred_on.isoformat(),
        "kind": entry.kind,
        "scope": entry.scope,
        "amount": str(entry.amount),
        "status": entry.status,
    }


def _locked(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    entry = (
        scoped_qs(ctx, CostEntry)
        .select_for_update()
        .filter(pk=entry_id, project=project)
        .first()
    )
    if entry is None:
        raise Http404
    return entry


def _require_editable(
    ctx: RequestContext,
    entry: CostEntry,
    policy: Callable[[RequestContext, CostEntry], bool],
) -> None:
    if entry.status == CostStatus.APPROVED:
        raise CostRejected([APPROVED_IS_FINAL])
    if entry.status == CostStatus.REJECTED and entry.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, entry):
        raise PermissionDenied


def _require_decidable(ctx: RequestContext, entry: CostEntry) -> None:
    if entry.status != CostStatus.DRAFT:
        raise CostRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (entry.created_by_id, entry.updated_by_id):
        raise CostRejected([SELF_DECISION])


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def create_draft(
    ctx: RequestContext,
    project: Project,
    data: EntryData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> CostEntry:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    assignment = _checked(ctx, project, data, catalog, tasks)
    blob = _stored(upload)
    with transaction.atomic():
        entry = scoped_qs(ctx, CostEntry).create(
            org_id=ctx.org_id,
            project=project,
            status=CostStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            attachment=blob,
            attachment_name=name[:MAX_FILE_NAME] if blob is not None else "",
            **entry_fields(data, assignment),
        )
        _link_items(ctx, entry, assignment.item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_CREATED,
            target=f"cost:{entry.pk}",
            metadata=_metadata(entry),
        )
    return entry


def update_draft(
    ctx: RequestContext,
    project: Project,
    entry_id: UUID,
    data: EntryData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> CostEntry:
    """Guarda un borrador. Un rechazado (solo su autor) vuelve a borrador: eso es reenviarlo."""
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        _require_editable(ctx, entry, can_update_cost)
        assignment = _checked(ctx, project, data, catalog, tasks)
        blob = _stored(upload)
        resubmitted = entry.status == CostStatus.REJECTED
        for field, value in entry_fields(data, assignment).items():
            setattr(entry, field, value)
        if blob is not None:
            entry.attachment = blob
            entry.attachment_name = name[:MAX_FILE_NAME]
        entry.status = CostStatus.DRAFT
        entry.decided_by = None
        entry.decided_at = None
        entry.decision_note = ""
        entry.updated_by_id = ctx.user_id
        entry.save()
        _link_items(ctx, entry, assignment.item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_UPDATED,
            target=f"cost:{entry.pk}",
            metadata={**_metadata(entry), "resubmitted": resubmitted},
        )
    return entry


def delete_draft(ctx: RequestContext, project: Project, entry_id: UUID) -> None:
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        _require_editable(ctx, entry, can_delete_cost)
        target = f"cost:{entry.pk}"
        metadata = {
            **_metadata(entry),
            "description": entry.description,
            "third_party": entry.third_party,
            "document_number": entry.document_number,
        }
        scoped_qs(ctx, CostEntryItem).filter(entry=entry).delete()
        entry.delete()
        audit.record(ctx, action=AuditAction.COST_DELETED, target=target, metadata=metadata)


def approve(ctx: RequestContext, project: Project, entry_id: UUID) -> CostEntry:
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        _require_decidable(ctx, entry)
        if entry.attachment_id is None:
            raise CostRejected(["Adjunta el soporte antes de aprobar."])
        entry.status = CostStatus.APPROVED
        entry.decided_by_id = ctx.user_id
        entry.decided_at = timezone.now()
        entry.decision_note = ""
        entry.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.COST_APPROVED,
            target=f"cost:{entry.pk}",
            metadata=_metadata(entry),
        )
    return entry


def reject(ctx: RequestContext, project: Project, entry_id: UUID, note: str) -> CostEntry:
    reason = note.strip()
    if not reason:
        raise CostRejected(["Escribe el motivo del rechazo."])
    if len(reason) > MAX_NOTE:
        raise CostRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    with transaction.atomic():
        entry = _locked(ctx, project, entry_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        _require_decidable(ctx, entry)
        entry.status = CostStatus.REJECTED
        entry.decided_by_id = ctx.user_id
        entry.decided_at = timezone.now()
        entry.decision_note = reason
        entry.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.COST_REJECTED,
            target=f"cost:{entry.pk}",
            metadata={**_metadata(entry), "note": reason},
        )
    return entry


def reverse(ctx: RequestContext, project: Project, entry_id: UUID, reason: str) -> CostEntry:
    """Anula un aprobado con otro aprobado de signo contrario, con fecha de hoy (los periodos
    pasados no cambian), la misma asignación y el motivo. No pide soporte propio."""
    text = reason.strip()
    if not text:
        raise CostRejected(["Escribe el motivo del reverso."])
    if len(text) > MAX_NOTE:
        raise CostRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    with transaction.atomic():
        original = _locked(ctx, project, entry_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if original.status != CostStatus.APPROVED or original.reverses_id is not None:
            raise CostRejected(["Solo se reversa un costo aprobado que no sea un reverso."])
        if scoped_qs(ctx, CostEntry).filter(reverses=original).exists():
            raise CostRejected(["Este costo ya se reversó."])
        reversal = scoped_qs(ctx, CostEntry).create(
            org_id=ctx.org_id,
            project=project,
            incurred_on=timezone.localdate(),
            kind=original.kind,
            description=f"Reverso: {original.description}"[:MAX_DESCRIPTION],
            amount=-original.amount,
            third_party=original.third_party,
            document_type=original.document_type,
            document_number=original.document_number,
            scope=original.scope,
            chapter_id=original.chapter_id,
            task_id=original.task_id,
            status=CostStatus.APPROVED,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            decided_by_id=ctx.user_id,
            decided_at=timezone.now(),
            decision_note=text,
            reverses=original,
        )
        item_ids = list(
            scoped_qs(ctx, CostEntryItem).filter(entry=original).values_list("item_id", flat=True)
        )
        _link_items(ctx, reversal, item_ids)
        audit.record(
            ctx,
            action=AuditAction.COST_REVERSED,
            target=f"cost:{original.pk}",
            metadata={**_metadata(original), "reversal": str(reversal.pk), "note": text},
        )
    return reversal


def support_url(ctx: RequestContext, project: Project, entry: CostEntry) -> str:
    """URL prefirmada y corta del soporte, **después** de comprobar que se leen los costos del
    proyecto (el `Blob` no tiene organización: la autorización es la del movimiento)."""
    if not can_read_costs(ctx, project) or entry.project_id != project.pk:
        raise Http404
    blob = (
        blob_registry().filter(pk=entry.attachment_id).first() if entry.attachment_id else None
    )
    if blob is None:
        raise Http404
    return get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )
```

- [ ] **Step 7: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/costs suno/core/audit.py && ruff check suno && mypy suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs suno/core/tests/test_architecture.py
```

Expected: PASS. `test_architecture.py` confirma que `suno/costs` no usa `Model.objects`.

- [ ] **Step 8: Commit**

```bash
git add server/suno/costs server/suno/core/audit.py
git commit -m "feat(costs): ledger services with approval, rejection and reversal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Importar costos desde Excel

**Files:**
- Create: `server/suno/costs/importer.py`
- Test: `server/suno/costs/tests/test_importer.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/costs/tests/test_importer.py`:

```python
"""Importación del libro de costos (spec, 2.6): plantilla, errores por fila, todo o nada."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.budgets.tests.factories import apply_rows
from suno.budgets.tests.samples import AIU, V1_ROWS
from suno.budgets.tests.xlsx import build_xlsx
from suno.core.access import SectionRole
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent, CostEntry, CostStatus, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.importer import HEADER, SHEET, apply, preview, template_bytes
from suno.costs.services import CostRejected

from .factories import cost_world, grant_costs

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
DAY = date(2026, 3, 10)


def row(**values: Any) -> list[Any]:
    base: dict[str, Any] = {
        "date": DAY,
        "kind": "Materiales",
        "code": "2.1",
        "task": "",
        "description": "Concreto premezclado",
        "amount": 1500000,
        "third": "Concretos SA",
        "doc": "Factura",
        "number": "F-101",
    }
    base.update(values)
    return list(base.values())


@skip_without_db
class CostImportTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.project = self.world.project
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.ctx = self.world.ctx(self.contributor)

    def book(self, *rows: list[Any], header: list[str] = HEADER) -> bytes:
        return build_xlsx(rows, header=header, sheet=SHEET)

    def test_template_lists_kinds_codes_and_tasks(self) -> None:
        data = template_bytes(self.world.ctx(), self.project)
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            workbook = book.read("xl/workbook.xml").decode()
            first = book.read("xl/worksheets/sheet1.xml").decode()
            codes = book.read("xl/worksheets/sheet4.xml").decode()
            tasks = book.read("xl/worksheets/sheet5.xml").decode()
        for name in ("Costos", "Tipos", "Documentos", "Códigos", "Tareas"):
            self.assertIn(f'name="{name}"', workbook)
        self.assertIn(">Código de ítem o capítulo<", first)
        self.assertIn(">2.1<", codes)
        self.assertIn(">Concreto zapatas<", codes)
        self.assertIn(">1.2<", tasks)

    def test_preview_reads_items_chapters_indirect_and_tasks(self) -> None:
        data = self.book(
            row(task="2.1"),
            row(
                date=date(2026, 3, 11),
                kind="mano de obra",
                code="2",
                description="Cuadrilla de estructura",
                amount="2.400.000",
                third="",
                doc="cuenta de cobro",
                number="",
            ),
            row(date="2026-03-12", kind="Nómina", code="", description="Residente", doc="Nómina"),
        )
        result = preview(self.ctx, self.project, data)
        self.assertEqual(result.errors, ())
        self.assertEqual([r.data.scope for r in result.rows], ["item", "chapter", "indirect"])
        self.assertEqual(result.rows[0].task, "2.1 Columnas")
        self.assertEqual(result.rows[0].assignment, "2.1 Concreto zapatas")
        self.assertEqual(result.rows[1].assignment, "Capítulo 2 Estructura")
        self.assertEqual(result.rows[1].data.amount, D("2400000"))
        self.assertEqual(result.total, D("5400000"))
        self.assertTrue(result.token)
        self.assertFalse(CostEntry.objects.exists())

    def test_errors_by_row(self) -> None:
        data = self.book(
            row(date="10-03-2026"),
            row(kind="Gasolina"),
            row(code="9.9"),
            row(amount="cien"),
            row(code="", task="2.1"),
            row(task="7.7"),
            row(),
        )
        result = preview(self.ctx, self.project, data)
        self.assertEqual(result.token, "")
        self.assertEqual(
            [error.split(":")[0] for error in result.errors],
            ["Fila 2", "Fila 3", "Fila 4", "Fila 5", "Fila 6", "Fila 7"],
        )

    def test_code_in_two_budgets_and_missing_columns(self) -> None:
        from suno.budgets.tests.factories import make_budget

        other = make_budget(self.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.world.ctx(), other, V1_ROWS, charges=AIU)
        twice = preview(self.ctx, self.project, self.book(row()))
        self.assertIn("más de un presupuesto", twice.errors[0])
        missing = preview(self.ctx, self.project, self.book(row()[:5], header=HEADER[:5]))
        self.assertIn("Falta la columna «Valor».", missing.errors)

    def test_apply_creates_every_row_in_draft(self) -> None:
        result = preview(self.ctx, self.project, self.book(row(), row(code="2", amount=10)))
        self.assertEqual(apply(self.ctx, self.project, result.token), 2)
        drafts = CostEntry.objects.filter(
            project=self.project, status=CostStatus.DRAFT, created_by=self.contributor
        )
        self.assertEqual(drafts.count(), 2)
        self.assertFalse(drafts.filter(attachment__isnull=False).exists())
        event = AuditEvent.objects.get(action=AuditAction.COST_IMPORTED)
        self.assertEqual(event.metadata["rows"], 2)

    def test_tampered_foreign_or_stale_previews_are_refused(self) -> None:
        token = preview(self.ctx, self.project, self.book(row(code="1.2"))).token
        with self.assertRaises(CostRejected):
            apply(self.ctx, self.project, token + "x")
        with self.assertRaises(CostRejected):  # la vista previa es de otro usuario
            apply(self.world.ctx(), self.project, token)
        from suno.budgets.tests.samples import V2_ROWS

        apply_rows(self.world.ctx(), self.budget, V2_ROWS, charges=AIU)  # quita el 1.2
        with self.assertRaises(CostRejected):
            apply(self.ctx, self.project, token)
        self.assertFalse(CostEntry.objects.exists())

    def test_only_roles_that_import(self) -> None:
        with self.assertRaises(PermissionDenied):
            preview(self.world.ctx(self.world.supervisor), self.project, self.book(row()))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_importer.py`
Expected: ERROR de import (`No module named 'suno.costs.importer'`).

- [ ] **Step 3: Implementar**

`server/suno/costs/importer.py`:

```python
"""Importar costos desde Excel (costos, pieza 6a, spec 2.6).

- Hoja "Costos" con el encabezado en la fila 1; las columnas se encuentran por su texto (sin
  importar mayúsculas ni tildes), así que se pueden reordenar.
- Código vacío: costo indirecto (sin tarea). Un código de capítulo asigna el capítulo completo;
  las selecciones de ítems solo se hacen en el formulario. Un código que esté en dos
  presupuestos del proyecto es un error de la fila.
- La tarea se identifica por su número de esquema ("1.2").
- La vista previa no guarda nada: las filas validadas viajan firmadas (`django.core.signing`)
  en el formulario durante una hora; al aplicar se verifica la firma, el proyecto y el usuario,
  y se valida todo otra vez. Todo o nada, y todo en borrador; los soportes van después.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction

from suno.backoffice.export import safe_cell, to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.mapping import fold, normalize_header
from suno.budgets.totals import parse_number
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    BudgetRowKind,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Project,
    Task,
)
from suno.core.policies import can_import_costs
from suno.core.scoping import scoped_qs

from .catalog import Catalog, assignable_tasks, load_catalog
from .services import CostRejected, EntryData, entry_fields, validate_entry

SHEET = "Costos"
H_DATE = "Fecha"
H_KIND = "Tipo"
H_CODE = "Código de ítem o capítulo"
H_TASK = "Tarea"
H_DESCRIPTION = "Descripción"
H_AMOUNT = "Valor"
H_THIRD = "Tercero"
H_DOC_TYPE = "Tipo de documento"
H_DOC_NUMBER = "Número de documento"
HEADER = [
    H_DATE, H_KIND, H_CODE, H_TASK, H_DESCRIPTION, H_AMOUNT, H_THIRD, H_DOC_TYPE, H_DOC_NUMBER,
]  # fmt: skip
REQUIRED = (H_DATE, H_KIND, H_CODE, H_DESCRIPTION, H_AMOUNT, H_DOC_TYPE)
MAX_IMPORT_ROWS = 2000
MAX_ERRORS = 50
TOKEN_SALT = "suno.costs.import"
TOKEN_MAX_AGE = 3600
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")
INDIRECT_LABEL = "Indirecto (proyecto)"


@dataclass(frozen=True)
class PreviewRow:
    #: Fila del Excel (el encabezado es la 1).
    line: int
    data: EntryData
    assignment: str
    task: str

    @property
    def kind_label(self) -> str:
        return str(CostKind(self.data.kind).label)


@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[PreviewRow, ...] = ()
    errors: tuple[str, ...] = ()
    #: Vacío si hay errores: no se puede aplicar.
    token: str = ""

    @property
    def total(self) -> Decimal:
        return sum((row.data.amount for row in self.rows), Decimal(0))


def template_bytes(ctx: RequestContext, project: Project) -> bytes:
    """La hoja para llenar y, aparte, las listas válidas (tipos, documentos, códigos, tareas)."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    return to_xlsx_book(
        [
            (SHEET, HEADER, []),
            ("Tipos", [H_KIND], [[label] for _, label in CostKind.choices]),
            ("Documentos", [H_DOC_TYPE], [[label] for _, label in CostDocumentType.choices]),
            (
                "Códigos",
                ["Código", "Descripción", "Clase", "Presupuesto"],
                [
                    [
                        safe_cell(row.code),
                        safe_cell(row.description),
                        "Capítulo" if row.kind == BudgetRowKind.CHAPTER else "Ítem",
                        safe_cell(row.budget),
                    ]
                    for row in catalog.rows.values()
                ],
            ),
            (
                "Tareas",
                [H_TASK, "Nombre"],
                [[safe_cell(t.outline_number), safe_cell(t.name)] for t in tasks.values()],
            ),
        ]
    )


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(value: Any) -> str:
    """Texto de una celda. Un número entero de Excel ("2") sale sin decimales."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = str(value).strip()
    # Deshace el apóstrofo que `safe_cell` antepone a lo que empieza con = + - @.
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _choice(value: Any, choices: Sequence[tuple[str, str]]) -> str | None:
    wanted = fold(_text(value)).strip()
    for key, label in choices:
        if wanted in (fold(label), key):
            return key
    return None


def _columns(header: Sequence[Any]) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in HEADER}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in REQUIRED if name not in found]


def _parse_row(
    values: Sequence[Any],
    columns: Mapping[str, int],
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    by_outline: Mapping[str, Task],
) -> tuple[list[str], tuple[EntryData, str, str] | None]:
    def cell(name: str) -> Any:
        index = columns.get(name)
        return values[index] if index is not None and index < len(values) else None

    errors: list[str] = []
    day = _day(cell(H_DATE))
    if day is None:
        errors.append("La fecha no es válida (usa AAAA-MM-DD o DD/MM/AAAA).")
    kind = _choice(cell(H_KIND), CostKind.choices)
    if kind is None:
        errors.append(f"El tipo «{_text(cell(H_KIND))}» no existe.")
    document_type = _choice(cell(H_DOC_TYPE), CostDocumentType.choices)
    if document_type is None:
        errors.append(f"El tipo de documento «{_text(cell(H_DOC_TYPE))}» no existe.")
    try:
        amount = parse_number(cell(H_AMOUNT))
    except ValueError:
        amount = None
    if amount is None:
        errors.append("El valor no es un número.")
    scope, item_ids, chapter_id, assignment = CostScope.INDIRECT, (), None, INDIRECT_LABEL
    code = _text(cell(H_CODE))
    if code:
        found = catalog.by_code(code)
        if not found:
            errors.append(
                f"No existe el código «{code}» en la versión vigente de los presupuestos."
            )
        elif len(found) > 1:
            errors.append(f"El código «{code}» está en más de un presupuesto del proyecto.")
        elif found[0].kind == BudgetRowKind.ITEM:
            scope, item_ids, assignment = CostScope.ITEM, (found[0].item_id,), found[0].label
        else:
            scope, chapter_id = CostScope.CHAPTER, found[0].item_id
            assignment = f"Capítulo {found[0].label}"
    task_id: UUID | None = None
    task_label = ""
    outline = _text(cell(H_TASK))
    if outline:
        task = by_outline.get(outline)
        if task is None:
            errors.append(f"La tarea «{outline}» no es una tarea vigente del proyecto.")
        else:
            task_id, task_label = task.pk, f"{task.outline_number} {task.name}"
    if errors or day is None or kind is None or document_type is None or amount is None:
        return errors, None
    data = EntryData(
        incurred_on=day,
        kind=kind,
        description=_text(cell(H_DESCRIPTION)),
        amount=amount,
        third_party=_text(cell(H_THIRD)),
        document_type=document_type,
        document_number=_text(cell(H_DOC_NUMBER)),
        scope=scope,
        item_ids=item_ids,
        chapter_id=chapter_id,
        task_id=task_id,
    )
    problems, checked = validate_entry(catalog, tasks, data)
    if checked is None:
        return problems, None
    return [], (data, assignment, task_label)


def preview(ctx: RequestContext, project: Project, data: bytes) -> ImportPreview:
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    try:
        sheet = preview_sheet(data, SHEET, rows=1).sheet
        table = read_rows(data, sheet, max_rows=MAX_IMPORT_ROWS + 1)
    except ExcelRejected as exc:
        return ImportPreview(errors=(str(exc),))
    if not table:
        return ImportPreview(errors=("El archivo está vacío.",))
    columns, missing = _columns(table[0])
    if missing:
        return ImportPreview(errors=tuple(missing))
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    by_outline = {task.outline_number: task for task in tasks.values()}
    rows: list[PreviewRow] = []
    errors: list[str] = []
    for line, values in enumerate(table[1:], start=2):
        if not any(_present(value) for value in values):
            continue
        problems, parsed = _parse_row(values, columns, catalog, tasks, by_outline)
        errors.extend(f"Fila {line}: {problem}" for problem in problems)
        if parsed is not None:
            rows.append(PreviewRow(line, *parsed))
        if len(errors) >= MAX_ERRORS:
            errors.append("Hay más errores: corrige estos y vuelve a subir el archivo.")
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de costos.")
    if errors:
        return ImportPreview(rows=tuple(rows), errors=tuple(errors))
    return ImportPreview(rows=tuple(rows), token=_sign(ctx, project, rows))


def _serialize(row: PreviewRow) -> dict[str, Any]:
    data = row.data
    return {
        "line": row.line,
        "date": data.incurred_on.isoformat(),
        "kind": data.kind,
        "description": data.description,
        "amount": str(data.amount),
        "third": data.third_party,
        "doc": data.document_type,
        "number": data.document_number,
        "scope": data.scope,
        "items": [str(item_id) for item_id in data.item_ids],
        "chapter": str(data.chapter_id) if data.chapter_id else "",
        "task": str(data.task_id) if data.task_id else "",
    }


def _deserialize(raw: Mapping[str, Any]) -> tuple[int, EntryData]:
    return int(raw["line"]), EntryData(
        incurred_on=date.fromisoformat(raw["date"]),
        kind=raw["kind"],
        description=raw["description"],
        amount=Decimal(raw["amount"]),
        third_party=raw["third"],
        document_type=raw["doc"],
        document_number=raw["number"],
        scope=raw["scope"],
        item_ids=tuple(UUID(value) for value in raw["items"]),
        chapter_id=UUID(raw["chapter"]) if raw["chapter"] else None,
        task_id=UUID(raw["task"]) if raw["task"] else None,
    )


def _sign(ctx: RequestContext, project: Project, rows: Sequence[PreviewRow]) -> str:
    payload = {
        "project": str(project.pk),
        "user": str(ctx.user_id),
        "rows": [_serialize(row) for row in rows],
    }
    return signing.dumps(payload, salt=TOKEN_SALT, compress=True)


def apply(ctx: RequestContext, project: Project, token: str) -> int:
    """Crea en borrador todas las filas de la vista previa, o ninguna."""
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise CostRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise CostRejected(["La vista previa no es válida: vuelve a subir el archivo."]) from exc
    if payload.get("project") != str(project.pk) or payload.get("user") != str(ctx.user_id):
        raise CostRejected(["La vista previa no es tuya o no es de este proyecto."])
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    checked = []
    errors: list[str] = []
    for raw in payload.get("rows", []):
        line, data = _deserialize(raw)
        problems, assignment = validate_entry(catalog, tasks, data)
        if assignment is None:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            checked.append((data, assignment))
    if errors or not checked:
        raise CostRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    with transaction.atomic():
        created = scoped_qs(ctx, CostEntry).bulk_create(
            [
                CostEntry(
                    org_id=ctx.org_id,
                    project=project,
                    status=CostStatus.DRAFT,
                    created_by_id=ctx.user_id,
                    updated_by_id=ctx.user_id,
                    **entry_fields(data, assignment),
                )
                for data, assignment in checked
            ]
        )
        scoped_qs(ctx, CostEntryItem).bulk_create(
            [
                CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id)
                for entry, (_, assignment) in zip(created, checked, strict=True)
                for item_id in assignment.item_ids
            ]
        )
        audit.record(
            ctx,
            action=AuditAction.COST_IMPORTED,
            target=f"project:{project.pk}",
            metadata={
                "rows": len(created),
                "amount": str(sum((data.amount for data, _ in checked), Decimal(0))),
            },
        )
    return len(created)
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/costs && ruff check suno/costs && mypy suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs
```

Expected: PASS. Si `"2.400.000"` no se lee como 2 400 000, revisar `parse_number_detail` (el presupuesto ya acepta puntos de miles) antes de tocar el importador.

- [ ] **Step 5: Commit**

```bash
git add server/suno/costs
git commit -m "feat(costs): import costs from excel with a signed preview

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Consultas del libro, líneas automáticas y Excel del libro

**Files:**
- Create: `server/suno/costs/ledger.py`, `server/suno/costs/export.py`
- Test: `server/suno/costs/tests/test_ledger.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/costs/tests/test_ledger.py`:

```python
"""Lista del libro de costos (spec, 2.1 y 2.7): filtros, automáticos, totales, Excel."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.core.models import CostEntry, CostKind, CostScope, CostStatus, EquipmentOwnership
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import ledger
from suno.costs.catalog import load_catalog
from suno.costs.export import ledger_workbook
from suno.costs.ledger import LedgerFilters, LedgerPage, LedgerTooLarge, export_lines, ledger_page

from .factories import cost_world, entry, equipment, item, trip, usage

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class LedgerTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")
        self.structure = item(self.budget, "2")
        self.approved = entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=self.world.task(5),
        )
        entry(self.world, day=date(2026, 3, 12), amount="500.00", description="Papelería")
        entry(
            self.world,
            day=date(2026, 3, 11),
            status=CostStatus.REJECTED,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        entry(
            self.world,
            day=date(2026, 3, 9),
            amount="700.00",
            status=CostStatus.APPROVED,
            kind=CostKind.LABOR,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        machine = equipment(self.world)
        usage(self.world, machine, date(2026, 3, 10), "800.00", budget_item=self.concrete)
        usage(self.world, machine, date(2026, 3, 10), "200.00")
        usage(self.world, machine, date(2026, 3, 11), None)  # sin tarifa: no suma
        rented = equipment(self.world, "AL-01", ownership=EquipmentOwnership.RENTED)
        usage(self.world, rented, date(2026, 3, 10), "999.00")  # alquilado: va al libro
        trip(self.world, date(2026, 3, 8), "1400.00")
        trip(self.world, date(2026, 3, 8), "1400.00", own=machine)  # ya está en sus horas
        self.catalog = load_catalog(self.ctx, self.world.project)

    def page(self, number: int = 1, **query: Any) -> LedgerPage:
        return ledger_page(
            self.ctx,
            self.world.project,
            LedgerFilters.from_query(query),
            page=number,
            catalog=self.catalog,
        )

    def test_combined_list_by_date(self) -> None:
        page = self.page()
        self.assertEqual(page.count, 7)
        self.assertEqual(
            [(line.day.day, line.origin) for line in page.lines],
            [
                (12, "libro"),
                (11, "libro"),
                (10, "libro"),
                (10, "equipo"),
                (10, "equipo"),
                (9, "libro"),
                (8, "transporte"),
            ],
        )
        assignments = [line.assignment for line in page.lines]
        self.assertEqual(assignments[0], "Indirecto (proyecto)")  # Papelería, del 12
        self.assertIn("2.1 Concreto zapatas · tarea 2.1 Columnas", assignments)
        self.assertIn("Capítulo 2 Estructura", assignments)
        equipment_lines = [line for line in page.lines if line.origin == "equipo"]
        self.assertEqual(
            {line.assignment for line in equipment_lines}, {"2.1 Concreto zapatas", "Sin asignar"}
        )
        self.assertTrue(all(line.automatic for line in equipment_lines))

    def test_filters(self) -> None:
        self.assertEqual(self.page(estado="draft").count, 1)
        self.assertEqual(self.page(estado="approved").count, 5)  # 2 del libro y 3 automáticos
        self.assertEqual(self.page(tipo="labor").count, 1)
        self.assertEqual(self.page(origen="automaticos").count, 3)
        self.assertEqual(self.page(origen="libro").count, 4)
        self.assertEqual(self.page(capitulo=str(self.structure.pk)).count, 4)
        self.assertEqual(self.page(item=str(self.concrete.pk)).count, 2)
        self.assertEqual(self.page(desde="2026-03-10", hasta="2026-03-11").count, 4)
        self.assertEqual(self.page(estado="no-existe").count, 7)

    def test_totals_by_kind(self) -> None:
        self.assertEqual(
            self.page().totals,
            (
                ("Materiales", D("1000.00")),
                ("Mano de obra", D("700.00")),
                ("Equipo propio (automático)", D("1000.00")),
                ("Transporte (automático)", D("1400.00")),
            ),
        )

    def test_pagination(self) -> None:
        for n in range(150):
            entry(self.world, day=date(2026, 2, 1), description=f"Gasto {n}")
        first, second = self.page(1), self.page(2)
        self.assertEqual((first.pages, len(first.lines), len(second.lines)), (2, 100, 57))
        self.assertTrue(first.has_next and second.has_previous)
        self.assertEqual(self.page(9).number, 2)

    def test_reversal_marks(self) -> None:
        entry(
            self.world,
            day=date(2026, 3, 13),
            amount="-1000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            reverses=self.approved,
            decided_by=self.world.admin,
        )
        lines = {line.entry_id: line for line in self.page().lines if line.entry_id}
        self.assertTrue(lines[self.approved.pk].reversed)
        self.assertTrue(lines[CostEntry.objects.get(reverses=self.approved).pk].is_reversal)

    def test_export(self) -> None:
        entry(self.world, description="=SUMA(A1)")
        lines = export_lines(self.ctx, self.world.project, LedgerFilters(), catalog=self.catalog)
        with zipfile.ZipFile(io.BytesIO(ledger_workbook(lines))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn(">Concreto premezclado<", sheet)
        self.assertIn(">'=SUMA(A1)<", sheet)
        self.assertIn(">Equipo propio<", sheet)
        with mock.patch.object(ledger, "MAX_EXPORT_LINES", 2), self.assertRaises(LedgerTooLarge):
            export_lines(self.ctx, self.world.project, LedgerFilters(), catalog=self.catalog)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_ledger.py`
Expected: ERROR de import (`No module named 'suno.costs.ledger'`).

- [ ] **Step 3: Implementar las consultas**

`server/suno/costs/ledger.py`:

```python
"""Consultas del libro de costos (costos, pieza 6a, spec 2.1 y 2.7).

- Los movimientos se filtran en SQL y se ordenan por fecha de causación (y registro).
- Las líneas automáticas no se copian al libro: se leen agregadas (equipo propio por día,
  equipo e ítem; transporte de terceros por viaje) y se mezclan por fecha. Son de solo
  lectura y cuentan como aprobadas.
- Un filtro de estado distinto de "aprobado", o de tipo, deja fuera las automáticas; un filtro
  de capítulo o de ítem deja fuera el transporte (no tiene ítem).
- La página `n` lee como mucho `n × PAGE_SIZE` movimientos.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from math import ceil
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet, Sum
from django.utils import timezone

from suno.core.context import RequestContext
from suno.core.models import (
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    EquipmentOwnership,
    EquipmentUsage,
    EquipmentUsageStatus,
    Project,
    Trip,
)
from suno.core.scoping import scoped_qs

from .catalog import Catalog

PAGE_SIZE = 100
MAX_EXPORT_LINES = 50_000
ALL, LEDGER_ONLY, AUTOMATIC = "todos", "libro", "automaticos"
ORIGINS = {ALL: "Todos", LEDGER_ONLY: "Libro", AUTOMATIC: "Solo automáticos"}
LEDGER, EQUIPMENT, TRANSPORT = "libro", "equipo", "transporte"
ORIGIN_LABELS = {LEDGER: "Libro", EQUIPMENT: "Equipo propio", TRANSPORT: "Transporte"}
COUNTED_USAGE = (EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS)
INDIRECT_LABEL = "Indirecto (proyecto)"
UNASSIGNED_LABEL = "Sin asignar"
AUTOMATIC_LABEL = "Automático"


class LedgerTooLarge(Exception):
    """El libro filtrado pasa del máximo exportable."""


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class LedgerFilters:
    status: str = ""
    kind: str = ""
    date_from: date | None = None
    date_to: date | None = None
    chapter_id: UUID | None = None
    item_id: UUID | None = None
    origin: str = ALL

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> LedgerFilters:
        """Valores de la URL; un valor desconocido se ignora (sin error)."""
        status = query.get("estado", "")
        kind = query.get("tipo", "")
        origin = query.get("origen", "")
        return cls(
            status=status if status in CostStatus.values else "",
            kind=kind if kind in CostKind.values else "",
            date_from=_date(query.get("desde")),
            date_to=_date(query.get("hasta")),
            chapter_id=_uuid(query.get("capitulo")),
            item_id=_uuid(query.get("item")),
            origin=origin if origin in ORIGINS else ALL,
        )

    @property
    def query(self) -> str:
        values = {
            "estado": self.status,
            "tipo": self.kind,
            "desde": self.date_from.isoformat() if self.date_from else "",
            "hasta": self.date_to.isoformat() if self.date_to else "",
            "capitulo": str(self.chapter_id) if self.chapter_id else "",
            "item": str(self.item_id) if self.item_id else "",
            "origen": "" if self.origin == ALL else self.origin,
        }
        return urlencode({key: value for key, value in values.items() if value})

    @property
    def shows_ledger(self) -> bool:
        return self.origin != AUTOMATIC

    @property
    def shows_automatic(self) -> bool:
        return (
            self.origin != LEDGER_ONLY
            and not self.kind
            and self.status in ("", CostStatus.APPROVED)
        )


@dataclass(frozen=True)
class LedgerLine:
    day: date
    #: `LEDGER`, `EQUIPMENT` o `TRANSPORT`.
    origin: str
    kind: str
    description: str
    assignment: str
    third_party: str
    document: str
    amount: Decimal
    #: Un `CostStatus`, o "auto".
    status: str
    status_label: str
    created_by: str = ""
    decided_by: str = ""
    entry_id: UUID | None = None
    reversed: bool = False
    is_reversal: bool = False

    @property
    def automatic(self) -> bool:
        return self.origin != LEDGER


@dataclass(frozen=True)
class LedgerPage:
    lines: tuple[LedgerLine, ...]
    number: int
    pages: int
    count: int
    #: Aprobados por tipo, más los automáticos.
    totals: tuple[tuple[str, Decimal], ...]

    @property
    def total(self) -> Decimal:
        return sum((amount for _, amount in self.totals), Decimal(0))

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


def manual_entries(
    ctx: RequestContext, project: Project, filters: LedgerFilters, catalog: Catalog
) -> QuerySet[CostEntry]:
    entries = scoped_qs(ctx, CostEntry).filter(project=project)
    if filters.status:
        entries = entries.filter(status=filters.status)
    if filters.kind:
        entries = entries.filter(kind=filters.kind)
    if filters.date_from:
        entries = entries.filter(incurred_on__gte=filters.date_from)
    if filters.date_to:
        entries = entries.filter(incurred_on__lte=filters.date_to)
    links = scoped_qs(ctx, CostEntryItem)
    if filters.item_id:
        entries = entries.filter(pk__in=links.filter(item_id=filters.item_id).values("entry_id"))
    elif filters.chapter_id:
        below = catalog.below(filters.chapter_id)
        entries = entries.filter(
            Q(chapter_id__in=[filters.chapter_id, *below])
            | Q(pk__in=links.filter(item_id__in=below).values("entry_id"))
        )
    return entries.order_by("-incurred_on", "-created_at")


def _with_details(ctx: RequestContext, entries: QuerySet[CostEntry]) -> QuerySet[CostEntry]:
    return (
        entries.select_related("created_by", "decided_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .annotate(
            is_reversed=Exists(scoped_qs(ctx, CostEntry).filter(reverses=OuterRef("pk")))
        )
    )


def assignment_text(entry: CostEntry, catalog: Catalog) -> str:
    links = list(entry.item_links.all())
    chapter = catalog.label_of(entry.chapter_id, entry.chapter.code if entry.chapter else "")
    if entry.scope == CostScope.INDIRECT:
        text = INDIRECT_LABEL
    elif entry.scope == CostScope.ITEM:
        text = catalog.label_of(links[0].item_id, links[0].item.code) if links else "—"
    elif entry.scope == CostScope.CHAPTER:
        text = f"Capítulo {chapter}"
    else:
        text = f"{len(links)} ítems de {chapter}"
    if entry.task is not None:
        text = f"{text} · tarea {entry.task.outline_number} {entry.task.name}"
    return text


def _entry_line(entry: CostEntry, catalog: Catalog) -> LedgerLine:
    document = " ".join(
        part for part in (entry.get_document_type_display(), entry.document_number) if part
    )
    return LedgerLine(
        day=entry.incurred_on,
        origin=LEDGER,
        kind=entry.get_kind_display(),
        description=entry.description,
        assignment=assignment_text(entry, catalog),
        third_party=entry.third_party,
        document=document,
        amount=entry.amount,
        status=entry.status,
        status_label=entry.get_status_display(),
        created_by=str(entry.created_by),
        decided_by=str(entry.decided_by) if entry.decided_by else "",
        entry_id=entry.pk,
        reversed=bool(getattr(entry, "is_reversed", False)),
        is_reversal=entry.reverses_id is not None,
    )


def automatic_lines(
    ctx: RequestContext, project: Project, filters: LedgerFilters, catalog: Catalog
) -> list[LedgerLine]:
    if not filters.shows_automatic:
        return []
    usages = scoped_qs(ctx, EquipmentUsage).filter(
        project=project,
        status__in=COUNTED_USAGE,
        equipment__ownership=EquipmentOwnership.OWN,
        cost__isnull=False,
    )
    trips = scoped_qs(ctx, Trip).filter(
        project=project, vehicle__isnull=False, amount__isnull=False
    )
    if filters.date_from:
        usages = usages.filter(usage_date__gte=filters.date_from)
        trips = trips.filter(recorded_at__date__gte=filters.date_from)
    if filters.date_to:
        usages = usages.filter(usage_date__lte=filters.date_to)
        trips = trips.filter(recorded_at__date__lte=filters.date_to)
    if filters.item_id:
        usages = usages.filter(budget_item_id=filters.item_id)
        trips = trips.none()
    elif filters.chapter_id:
        usages = usages.filter(budget_item_id__in=catalog.descendants(filters.chapter_id))
        trips = trips.none()
    lines: list[LedgerLine] = []
    for day, code, name, item_id, hours, total in (
        usages.order_by()
        .values("usage_date", "equipment__code", "equipment__name", "budget_item_id")
        .annotate(hours=Sum("billable_hours"), total=Sum("cost"))
        .values_list(
            "usage_date", "equipment__code", "equipment__name", "budget_item_id", "hours", "total"
        )
    ):
        lines.append(
            LedgerLine(
                day=day,
                origin=EQUIPMENT,
                kind=ORIGIN_LABELS[EQUIPMENT],
                description=f"{code} · {name} ({hours or 0} h)",
                assignment=catalog.label_of(item_id, UNASSIGNED_LABEL)
                if item_id
                else UNASSIGNED_LABEL,
                third_party="",
                document="",
                amount=total,
                status="auto",
                status_label=AUTOMATIC_LABEL,
            )
        )
    for trip in trips.select_related("material", "route", "vehicle__contractor"):
        contractor = trip.vehicle.contractor.name if trip.vehicle else ""
        lines.append(
            LedgerLine(
                day=timezone.localtime(trip.recorded_at).date(),
                origin=TRANSPORT,
                kind=ORIGIN_LABELS[TRANSPORT],
                description=f"{trip.material.name} · {trip.volume_m3} m³ · {trip.route}",
                assignment=UNASSIGNED_LABEL,
                third_party=contractor,
                document=trip.code,
                amount=trip.amount,
                status="auto",
                status_label=AUTOMATIC_LABEL,
            )
        )
    lines.sort(key=lambda line: line.day, reverse=True)
    return lines


def _merge(manual: Sequence[LedgerLine], automatic: Sequence[LedgerLine]) -> list[LedgerLine]:
    """Por fecha, de la más reciente a la más antigua; en un mismo día, primero el libro."""
    return sorted([*manual, *automatic], key=lambda line: line.day, reverse=True)


def kind_totals(
    ctx: RequestContext,
    project: Project,
    filters: LedgerFilters,
    catalog: Catalog,
    automatic: Sequence[LedgerLine],
) -> tuple[tuple[str, Decimal], ...]:
    totals: list[tuple[str, Decimal]] = []
    if filters.shows_ledger and filters.status in ("", CostStatus.APPROVED):
        approved = manual_entries(
            ctx, project, replace(filters, status=CostStatus.APPROVED), catalog
        )
        sums = dict(
            approved.order_by().values("kind").annotate(total=Sum("amount")).values_list(
                "kind", "total"
            )
        )
        totals += [(label, sums[kind]) for kind, label in CostKind.choices if sums.get(kind)]
    for origin in (EQUIPMENT, TRANSPORT):
        amount = sum((line.amount for line in automatic if line.origin == origin), Decimal(0))
        if amount:
            totals.append((f"{ORIGIN_LABELS[origin]} (automático)", amount))
    return tuple(totals)


def ledger_page(
    ctx: RequestContext,
    project: Project,
    filters: LedgerFilters,
    *,
    page: int,
    catalog: Catalog,
) -> LedgerPage:
    entries = manual_entries(ctx, project, filters, catalog) if filters.shows_ledger else None
    automatic = automatic_lines(ctx, project, filters, catalog)
    count = (entries.count() if entries is not None else 0) + len(automatic)
    pages = max(1, ceil(count / PAGE_SIZE))
    number = min(max(page, 1), pages)
    end = number * PAGE_SIZE
    head = (
        [_entry_line(e, catalog) for e in _with_details(ctx, entries)[:end]]
        if entries is not None
        else []
    )
    lines = _merge(head, automatic[:end])[end - PAGE_SIZE : end]
    return LedgerPage(
        lines=tuple(lines),
        number=number,
        pages=pages,
        count=count,
        totals=kind_totals(ctx, project, filters, catalog, automatic),
    )


def export_lines(
    ctx: RequestContext, project: Project, filters: LedgerFilters, *, catalog: Catalog
) -> list[LedgerLine]:
    """Todo el libro filtrado (movimientos y automáticos). Lanza `LedgerTooLarge`."""
    entries = (
        list(_with_details(ctx, manual_entries(ctx, project, filters, catalog))[: MAX_EXPORT_LINES + 1])
        if filters.shows_ledger
        else []
    )
    automatic = automatic_lines(ctx, project, filters, catalog)
    if len(entries) + len(automatic) > MAX_EXPORT_LINES:
        raise LedgerTooLarge
    return _merge([_entry_line(e, catalog) for e in entries], automatic)
```

Notas para quien implementa:
- `test_filters` cuenta con `estado=approved` 5 líneas: el movimiento de ítem y el de mano de obra del libro, más dos de equipo y una de transporte.
- Con `capitulo` = "2 Estructura" entran el movimiento de ítem (su `chapter` es "2"), el rechazado y el de mano de obra (los tres por `chapter_id`) y el uso de equipo del 2.1: 4.
- Los agregados llevan `.order_by()`: el `ordering` de `EquipmentUsage` (`-usage_date`, `-start_time`) entraría si no al `GROUP BY` y partiría los grupos.

- [ ] **Step 4: El Excel del libro**

`server/suno/costs/export.py`:

```python
"""Excel del libro de costos (costos, pieza 6a, spec 2.7).

Descripciones, terceros y documentos los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Valores como número; fechas en ISO.
"""

from __future__ import annotations

from collections.abc import Sequence

from suno.backoffice.export import safe_cell, to_xlsx

from .ledger import ORIGIN_LABELS, LedgerLine

SHEET = "Libro de costos"
HEADER = [
    "fecha", "origen", "tipo", "descripcion", "asignacion", "tercero", "documento",
    "valor", "estado", "registro", "aprobo",
]  # fmt: skip


def ledger_workbook(lines: Sequence[LedgerLine]) -> bytes:
    rows = [
        [
            safe_cell(value)
            for value in (
                line.day.isoformat(),
                ORIGIN_LABELS[line.origin],
                line.kind,
                line.description,
                line.assignment,
                line.third_party,
                line.document,
                float(line.amount),
                line.status_label,
                line.created_by,
                line.decided_by,
            )
        ]
        for line in lines
    ]
    return to_xlsx(HEADER, rows, sheet_name=SHEET)
```

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/costs && ruff check suno/costs && mypy suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/suno/costs
git commit -m "feat(costs): ledger queries with automatic equipment and transport lines

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Pestaña "Costos": libro, formulario, ficha y soporte

**Files:**
- Create: `server/suno/costs/forms.py`, `server/suno/costs/views.py`, `server/suno/costs/urls.py`
- Modify: `server/config/urls.py` (monta `costos/`)
- Modify: `server/suno/projects/views.py` (`_project_context`: `can_view_costs`)
- Modify: `server/suno/core/templates/suno/projects/_header.html` (pestaña "Costos")
- Create: `server/suno/core/templates/suno/costs/_nav.html`, `list.html`, `form.html`, `detail.html`
- Modify: `server/suno/core/static/suno/css/projects.css` (estilos `cst-*`), `server/suno/core/templates/suno/projects/base.html` (`?v=`)
- Test: `server/suno/costs/tests/test_views.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/costs/tests/test_views.py`:

```python
"""Pestaña "Costos" (spec, 2.1, 2.2 y 5): permisos, lista, formulario, ficha y soporte."""

from __future__ import annotations

import tempfile
import unittest
from typing import Any

from django.test import TestCase, override_settings

from suno.authn.tests.factories import make_user
from suno.core.access import AccessScope, SectionRole
from suno.core.models import CostEntry, CostScope, CostStatus, Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import cost_world, entry, grant_costs, item, pdf_upload

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class CostPagesTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}costos/"
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.client.force_login(self.world.admin)

    def form(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "alcance": "item",
            "capitulo": "",
            "item": "2.1 Concreto zapatas",
            "task": str(self.world.task(5).pk),
            "incurred_on": "2026-03-10",
            "kind": "materials",
            "description": "Concreto premezclado",
            "amount": "1500000.00",
            "third_party": "Concretos SA",
            "document_type": "invoice",
            "document_number": "F-101",
            "support": pdf_upload(),
        }
        values.update(overrides)
        return values

    def test_who_sees_the_tab(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Costos</a>")
        self.client.force_login(self.world.supervisor)  # sin la sección Costos
        self.assertNotContains(self.client.get(self.project_url), ">Costos</a>")
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other, _ = cost_world("Otra constructora")
        self.client.force_login(other.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(
            self.world, scoped, SectionRole.VIEWER, scope=AccessScope.PROJECTS, projects=(elsewhere,)
        )
        self.client.force_login(scoped)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(
            self.client.get(f"/plataforma/proyectos/{elsewhere.pk}/costos/").status_code, 200
        )

    def test_list_filters_and_totals(self) -> None:
        entry(self.world, status=CostStatus.APPROVED, description="Cemento gris")
        entry(self.world, description="Papelería")
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Costos</a>')
        self.assertContains(response, "Cemento gris")
        self.assertContains(response, "Papelería")
        self.assertContains(response, "Total aprobado")
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertNotContains(drafts, "Cemento gris")
        self.assertContains(drafts, '<option value="draft" selected>')

    def test_viewer_reads_but_does_not_create(self) -> None:
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(self.base + "nuevo/").status_code, 403)

    def test_create_an_item_draft_with_support(self) -> None:
        page = self.client.get(self.base + "nuevo/")
        self.assertContains(page, '<datalist id="cst-items">')
        self.assertContains(page, '<option value="2.1 Concreto zapatas">')
        response = self.client.post(self.base + "nuevo/", self.form())
        cost = CostEntry.objects.get()
        self.assertRedirects(response, f"{self.base}{cost.pk}/")
        self.assertEqual((cost.status, cost.task_id), ("draft", self.world.task(5).pk))
        detail = self.client.get(f"{self.base}{cost.pk}/")
        self.assertContains(detail, "2.1 Concreto zapatas · tarea 2.1 Columnas")
        self.assertContains(detail, "factura.pdf")
        support = self.client.get(f"{self.base}{cost.pk}/soporte/")
        self.assertEqual(support.status_code, 302)
        self.assertIn(cost.attachment_id, support["Location"])

    def test_errors_are_shown(self) -> None:
        response = self.client.post(self.base + "nuevo/", self.form(item="9.9 No existe"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "versión vigente de su presupuesto")
        self.assertFalse(CostEntry.objects.exists())

    def test_selection_in_two_steps(self) -> None:
        chapter = item(self.budget, "2")
        step = self.client.get(self.base + f"nuevo/?alcance=selection&capitulo={chapter.pk}")
        for code in ("2.1", "2.2", "2.4"):
            self.assertContains(step, f'value="{item(self.budget, code).pk}"')
        self.client.post(
            self.base + "nuevo/",
            self.form(
                alcance="selection",
                capitulo=str(chapter.pk),
                items=[str(item(self.budget, "2.1").pk), str(item(self.budget, "2.2").pk)],
            ),
        )
        cost = CostEntry.objects.get()
        self.assertEqual((cost.scope, cost.items.count()), (CostScope.SELECTION, 2))

    def test_edit_rules(self) -> None:
        theirs = entry(self.world)
        self.client.force_login(self.contributor)
        self.assertEqual(self.client.get(f"{self.base}{theirs.pk}/editar/").status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(f"{self.base}{theirs.pk}/editar/", self.form(description="Corregido"))
        theirs.refresh_from_db()
        self.assertEqual(theirs.description, "Corregido")
        approved = entry(self.world, status=CostStatus.APPROVED)
        response = self.client.get(f"{self.base}{approved.pk}/editar/", follow=True)
        self.assertContains(response, "se reversa")

    def test_delete_and_copy(self) -> None:
        draft = entry(self.world, description="Para borrar")
        copy = self.client.get(self.base + f"nuevo/?copiar={draft.pk}")
        self.assertContains(copy, 'value="Para borrar"')
        self.client.post(f"{self.base}{draft.pk}/borrar/")
        self.assertFalse(CostEntry.objects.filter(pk=draft.pk).exists())
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_views.py`
Expected: FAIL (404 en `costos/`: la URL no existe).

- [ ] **Step 3: El formulario**

`server/suno/costs/forms.py`:

```python
"""Formulario de un movimiento del libro de costos (costos, pieza 6a, spec 2.2).

La asignación se elige en dos pasos, sin JavaScript: un formulario GET con el tipo de
asignación (y, para una selección, el capítulo) y luego este formulario con el ítem (campo con
`datalist` sobre las etiquetas del catálogo), el capítulo o las casillas de los ítems.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.utils import timezone

from suno.attachments.web import ACCEPT
from suno.core.models import CostDocumentType, CostEntry, CostKind, CostScope, Task

from .catalog import Catalog
from .services import EntryData


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Step:
    scope: str
    #: Solo en una selección: el capítulo cuyos ítems se ofrecen.
    chapter_id: UUID | None = None


def read_step(params: Mapping[str, Any], entry: CostEntry | None = None) -> Step:
    """El paso 1 (de la URL o de los campos ocultos); por defecto, el del movimiento o `item`."""
    scope = params.get("alcance") or (entry.scope if entry is not None else CostScope.ITEM)
    if scope not in CostScope.values:
        scope = CostScope.ITEM
    chapter = _uuid(params.get("capitulo"))
    if chapter is None and entry is not None and entry.scope == scope:
        chapter = entry.chapter_id
    return Step(scope, chapter if scope == CostScope.SELECTION else None)


class EntryForm(forms.Form):
    item = forms.CharField(
        label="Ítem",
        required=False,
        widget=forms.TextInput(attrs={"list": "cst-items", "autocomplete": "off"}),
    )
    chapter = forms.ChoiceField(label="Capítulo", required=False)
    items = forms.MultipleChoiceField(
        label="Ítems del capítulo", required=False, widget=forms.CheckboxSelectMultiple
    )
    task = forms.ChoiceField(label="Tarea (opcional)", required=False)
    incurred_on = forms.DateField(
        label="Fecha de causación",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    kind = forms.ChoiceField(label="Tipo", choices=CostKind.choices)
    description = forms.CharField(label="Descripción", max_length=300)
    amount = forms.DecimalField(
        label="Valor con IVA", max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    third_party = forms.CharField(
        label="Tercero (proveedor, contratista o trabajador)", max_length=200, required=False
    )
    document_type = forms.ChoiceField(label="Tipo de documento", choices=CostDocumentType.choices)
    document_number = forms.CharField(label="Número de documento", max_length=80, required=False)
    support = forms.FileField(
        label="Soporte (PDF, JPG, PNG o WebP)",
        required=False,
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )

    def __init__(
        self,
        *args: Any,
        catalog: Catalog,
        tasks: Mapping[UUID, Task],
        step: Step,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.catalog = catalog
        self.step = step
        self.fields["chapter"].choices = [
            ("", "Elige un capítulo"),
            *((str(row.item_id), row.label) for row in catalog.chapters()),
        ]
        self.fields["items"].choices = (
            [(str(row.item_id), row.label) for row in catalog.items_under(step.chapter_id)]
            if step.chapter_id is not None
            else []
        )
        self.fields["task"].choices = [
            ("", "Sin tarea"),
            *((str(task.pk), f"{task.outline_number} {task.name}") for task in tasks.values()),
        ]

    def entry_data(self) -> EntryData:
        data = self.cleaned_data
        scope = self.step.scope
        item_ids: tuple[UUID, ...] = ()
        chapter_id: UUID | None = None
        if scope == CostScope.ITEM:
            row = self.catalog.by_label(data.get("item") or "")
            item_ids = (row.item_id,) if row is not None else ()
        elif scope == CostScope.CHAPTER:
            chapter_id = _uuid(data.get("chapter"))
        elif scope == CostScope.SELECTION:
            chapter_id = self.step.chapter_id
            item_ids = tuple(UUID(value) for value in data.get("items") or ())
        return EntryData(
            incurred_on=data["incurred_on"],
            kind=data["kind"],
            description=data["description"],
            amount=data["amount"],
            third_party=data.get("third_party") or "",
            document_type=data["document_type"],
            document_number=data.get("document_number") or "",
            scope=scope,
            item_ids=item_ids,
            chapter_id=chapter_id,
            task_id=_uuid(data.get("task")) if scope != CostScope.INDIRECT else None,
        )


def initial_from(entry: CostEntry, catalog: Catalog, *, copy: bool = False) -> dict[str, Any]:
    """Valores del formulario para editar un movimiento o copiarlo en uno nuevo (sin soporte;
    la copia con fecha de hoy)."""
    items = [link.item_id for link in entry.item_links.all()]
    return {
        "incurred_on": timezone.localdate() if copy else entry.incurred_on,
        "kind": entry.kind,
        "description": entry.description,
        "amount": entry.amount,
        "third_party": entry.third_party,
        "document_type": entry.document_type,
        "document_number": entry.document_number,
        "item": catalog.label_of(items[0]) if entry.scope == CostScope.ITEM and items else "",
        "chapter": str(entry.chapter_id) if entry.chapter_id else "",
        "items": [str(item_id) for item_id in items],
        "task": str(entry.task_id) if entry.task_id else "",
    }
```

- [ ] **Step 4: Las vistas**

`server/suno/costs/views.py`:

```python
"""Pestaña "Costos" del proyecto (costos, pieza 6a): el libro de costos.

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_costs_project`). Quien los
lee pero no tiene la capacidad de una acción recibe 403. Las reglas de estado y de
autoaprobación viven en `services`: aquí solo se traducen a mensajes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.context import RequestContext
from suno.core.models import CostEntry, CostKind, CostScope, CostStatus, Project
from suno.core.policies import (
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_import_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.projects.views import _ctx, _parse_uuid, _project_context, _render

from . import services
from .access import get_costs_project, get_entry
from .catalog import assignable_tasks, load_catalog
from .forms import EntryForm, initial_from, read_step
from .ledger import ORIGINS, LedgerFilters, assignment_text, ledger_page
from .services import APPROVED_IS_FINAL, CostRejected


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_cost": can_create_costs(ctx, project),
        "can_import_cost": can_import_costs(ctx, project),
        "can_export_cost": can_export_costs(ctx, project),
    }


def _page(value: str | None) -> int:
    try:
        return int(value or 1)
    except ValueError:
        return 1


def _editable(ctx: RequestContext, entry: CostEntry, delete: bool = False) -> bool:
    """Lo mismo que exige el servicio, para mostrar u ocultar los botones."""
    if entry.status == CostStatus.APPROVED:
        return False
    if entry.status == CostStatus.REJECTED and entry.created_by_id != ctx.user_id:
        return False
    return can_delete_cost(ctx, entry) if delete else can_update_cost(ctx, entry)


@login_required(login_url="platform_login")
@require_GET
def ledger_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    catalog = load_catalog(ctx, project)
    filters = LedgerFilters.from_query(request.GET)
    page = ledger_page(
        ctx, project, filters, page=_page(request.GET.get("pagina")), catalog=catalog
    )
    return _render(
        request,
        ctx,
        "suno/costs/list.html",
        {
            **_context(ctx, project),
            "filters": filters,
            "page": page,
            "catalog": catalog,
            "statuses": CostStatus.choices,
            "kinds": CostKind.choices,
            "origins": ORIGINS,
        },
    )


def _entry_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    entry: CostEntry | None,
) -> HttpResponse:
    """Formulario nuevo (con `?copiar=` opcional) o de edición."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    source = entry
    copy_id = _parse_uuid(request.GET.get("copiar")) if entry is None else None
    if copy_id is not None:
        source = get_entry(ctx, project, copy_id)
    step = read_step(request.POST if request.method == "POST" else request.GET, source)
    initial: dict[str, Any] = (
        initial_from(source, catalog, copy=entry is None)
        if source is not None
        else {"incurred_on": timezone.localdate()}
    )
    form = EntryForm(
        request.POST or None,
        request.FILES or None,
        catalog=catalog,
        tasks=tasks,
        step=step,
        initial=initial,
    )
    errors: list[str] = []
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data.get("support")
        name = upload.name if upload is not None else ""
        try:
            if entry is None:
                saved = services.create_draft(
                    ctx, project, form.entry_data(), upload=upload, name=name,
                    catalog=catalog, tasks=tasks,
                )  # fmt: skip
            else:
                saved = services.update_draft(
                    ctx, project, entry.pk, form.entry_data(), upload=upload, name=name,
                    catalog=catalog, tasks=tasks,
                )  # fmt: skip
        except CostRejected as exc:
            errors = exc.errors
        else:
            messages.success(request, "Costo guardado en borrador.")
            return redirect("costs:detail", project.pk, saved.pk)
    return _render(
        request,
        ctx,
        "suno/costs/form.html",
        {
            **_context(ctx, project),
            "form": form,
            "step": step,
            "scopes": CostScope.choices,
            "catalog": catalog,
            "errors": errors,
            "entry": entry,
            "copy_id": copy_id,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    return _entry_form(request, ctx, project, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    if entry.status == CostStatus.APPROVED:
        messages.error(request, APPROVED_IS_FINAL)
        return redirect("costs:detail", project.pk, entry.pk)
    if not _editable(ctx, entry):
        raise PermissionDenied
    return _entry_form(request, ctx, project, entry)


@login_required(login_url="platform_login")
@require_GET
def detail(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    catalog = load_catalog(ctx, project)
    return _render(
        request,
        ctx,
        "suno/costs/detail.html",
        {
            **_context(ctx, project),
            "entry": entry,
            "assignment": assignment_text(entry, catalog),
            "reversal": scoped_qs(ctx, CostEntry).filter(reverses=entry).first(),
            "can_edit": _editable(ctx, entry),
            "can_delete": _editable(ctx, entry, delete=True),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def delete(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.delete_draft(ctx, project, entry_id)
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect("costs:detail", project.pk, entry_id)
    messages.success(request, "Borrador borrado.")
    return redirect("costs:list", project.pk)


@login_required(login_url="platform_login")
@require_GET
def support(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    url = services.support_url(ctx, project, get_entry(ctx, project, entry_id))
    # A mano y no con `redirect`: `HttpResponseRedirect` rechaza el esquema `local://` del
    # adaptador local (desarrollo y pruebas). La URL la firma el almacén, no el usuario.
    response = HttpResponse(status=302)
    response["Location"] = url
    return response
```

`server/suno/costs/urls.py`:

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/costos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "costs"

urlpatterns = [
    path("", views.ledger_list, name="list"),
    path("nuevo/", views.new, name="new"),
    path("<uuid:entry_id>/", views.detail, name="detail"),
    path("<uuid:entry_id>/editar/", views.edit, name="edit"),
    path("<uuid:entry_id>/borrar/", views.delete, name="delete"),
    path("<uuid:entry_id>/soporte/", views.support, name="support"),
]
```

En `server/config/urls.py`, después del `path` de `control/`:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/costos/",
        include("suno.costs.urls"),
    ),
```

En `server/suno/projects/views.py`, importar `can_read_costs` de `suno.core.policies` y agregar a `_project_context`:

```python
        # Pestaña "Costos" de la cabecera (costos, pieza 6a): por proyecto.
        "can_view_costs": can_read_costs(ctx, project),
```

En `server/suno/core/templates/suno/projects/_header.html`, después de la línea de "Control":

```html
      {% if can_view_costs %}<a href="{% url 'costs:list' project.pk %}" {% if tab == "costs" %}aria-current="page"{% endif %}>Costos</a>{% endif %}
```

- [ ] **Step 5: Plantillas**

`server/suno/core/templates/suno/costs/_nav.html` (la Task 7 lo amplía):

```html
<nav class="pj-tabs cst-nav" aria-label="Libro de costos">
  <a href="{% url 'costs:list' project.pk %}" {% if sub == "list" %}aria-current="page"{% endif %}>Libro</a>
  {% if can_create_cost %}<a href="{% url 'costs:new' project.pk %}" {% if sub == "new" %}aria-current="page"{% endif %}>Nuevo costo</a>{% endif %}
</nav>
```

`server/suno/core/templates/suno/costs/list.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Costos · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="list" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros del libro">
    <label>Estado <select name="estado"><option value="">Todos</option>{% for key, label in statuses %}<option value="{{ key }}" {% if filters.status == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Tipo <select name="tipo"><option value="">Todos</option>{% for key, label in kinds %}<option value="{{ key }}" {% if filters.kind == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Desde <input type="date" name="desde" value="{{ filters.date_from|date:'Y-m-d' }}"></label>
    <label>Hasta <input type="date" name="hasta" value="{{ filters.date_to|date:'Y-m-d' }}"></label>
    <label>Capítulo <select name="capitulo"><option value="">Todos</option>{% for row in catalog.chapters %}<option value="{{ row.item_id }}" {% if filters.chapter_id == row.item_id %}selected{% endif %}>{{ row.label|truncatechars:60 }}</option>{% endfor %}</select></label>
    <label>Ítem <select name="item"><option value="">Todos</option>{% for row in catalog.items %}<option value="{{ row.item_id }}" {% if filters.item_id == row.item_id %}selected{% endif %}>{{ row.label|truncatechars:60 }}</option>{% endfor %}</select></label>
    <label>Origen <select name="origen">{% for key, label in origins.items %}<option value="{{ key }}" {% if filters.origin == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Filtrar</button>
  </form>
</section>
<section class="pj-panel">
  {% if page.lines %}
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <caption class="sr-only">Libro de costos</caption>
    <thead><tr><th>Fecha</th><th>Tipo</th><th>Descripción</th><th>Asignación</th><th>Tercero</th><th>Documento</th><th class="num">Valor</th><th>Estado</th><th>Registró</th><th>Aprobó</th></tr></thead>
    <tbody>
    {% for line in page.lines %}
      <tr{% if line.automatic %} class="cst-auto"{% endif %}>
        <td>{{ line.day|date:"d/m/Y" }}</td>
        <td>{{ line.kind }}</td>
        <td class="pj-name">{% if line.entry_id %}<a href="{% url 'costs:detail' project.pk line.entry_id %}">{{ line.description }}</a>{% else %}{{ line.description }}{% endif %}{% if line.reversed %} <small class="cst-mark">reversado</small>{% endif %}{% if line.is_reversal %} <small class="cst-mark">reverso</small>{% endif %}</td>
        <td class="pj-wrap">{{ line.assignment }}</td>
        <td class="pj-wrap">{{ line.third_party }}</td>
        <td class="pj-wrap">{{ line.document }}</td>
        <td class="num">{{ line.amount|signed }}</td>
        <td><span class="bg-label cst-{{ line.status }}">{{ line.status_label }}</span></td>
        <td class="pj-wrap">{{ line.created_by }}</td>
        <td class="pj-wrap">{{ line.decided_by }}</td>
      </tr>
    {% endfor %}
    </tbody>
    {% if page.totals %}
    <tfoot>
      {% for label, amount in page.totals %}<tr><th colspan="6" scope="row">{{ label }}</th><td class="num">{{ amount|signed }}</td><td colspan="3"></td></tr>{% endfor %}
      <tr><th colspan="6" scope="row">Total aprobado</th><td class="num">{{ page.total|signed }}</td><td colspan="3"></td></tr>
    </tfoot>
    {% endif %}
  </table>
  </div>
  <nav class="cst-pages" aria-label="Páginas">
    {% if page.has_previous %}<a href="?{{ filters.query }}&amp;pagina={{ page.number|add:'-1' }}">Anterior</a>{% endif %}
    <span>Página {{ page.number }} de {{ page.pages }} · {{ page.count }} línea{{ page.count|pluralize:"s" }}</span>
    {% if page.has_next %}<a href="?{{ filters.query }}&amp;pagina={{ page.number|add:'1' }}">Siguiente</a>{% endif %}
  </nav>
  {% else %}
  <p class="pj-empty">No hay costos con estos filtros.{% if can_create_cost %} <a href="{% url 'costs:new' project.pk %}">Registra uno</a>.{% endif %}</p>
  {% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/costs/form.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}{% if entry %}Editar costo{% else %}Nuevo costo{% endif %} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="new" %}
<section class="pj-panel">
  <h2>{% if entry %}Editar costo{% else %}Nuevo costo{% endif %}</h2>
  {% if entry.status == "rejected" %}<p class="pj-warning">Rechazado: {{ entry.decision_note }}. Al guardarlo vuelve a borrador para que lo aprueben.</p>{% endif %}
  <form method="get" class="pj-filters" aria-label="Paso 1: a qué se asigna">
    {% if copy_id %}<input type="hidden" name="copiar" value="{{ copy_id }}">{% endif %}
    <label>Asignar a <select name="alcance">{% for key, label in scopes %}<option value="{{ key }}" {% if step.scope == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Capítulo (solo para ítems de un capítulo) <select name="capitulo"><option value="">—</option>{% for row in catalog.chapters %}<option value="{{ row.item_id }}" {% if step.chapter_id == row.item_id %}selected{% endif %}>{{ row.label|truncatechars:60 }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Elegir</button>
  </form>
</section>
<section class="pj-panel">
  <form method="post" enctype="multipart/form-data" class="pj-form cst-form">
    {% csrf_token %}
    <input type="hidden" name="alcance" value="{{ step.scope }}">
    <input type="hidden" name="capitulo" value="{{ step.chapter_id|default:'' }}">
    {% if errors or form.errors %}<div class="pj-late" role="alert"><p>No se pudo guardar:</p><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}
    {% if step.scope == "item" %}
      <label>{{ form.item.label }} {{ form.item }}</label>
      <datalist id="cst-items">{% for row in catalog.items %}<option value="{{ row.label }}">{% endfor %}</datalist>
    {% elif step.scope == "chapter" %}
      <label>{{ form.chapter.label }} {{ form.chapter }}</label>
    {% elif step.scope == "selection" %}
      {% if step.chapter_id %}<fieldset class="cst-items"><legend>{{ form.items.label }}</legend>{{ form.items }}</fieldset>{% else %}<p class="pj-warning">Elige el capítulo arriba y pulsa «Elegir» para ver sus ítems.</p>{% endif %}
    {% else %}
      <p class="pj-muted">Costo indirecto: va al proyecto, sin ítem ni tarea.</p>
    {% endif %}
    {% if step.scope != "indirect" %}<label>{{ form.task.label }} {{ form.task }}</label>{% endif %}
    <label>{{ form.incurred_on.label }} {{ form.incurred_on }}</label>
    <label>{{ form.kind.label }} {{ form.kind }}</label>
    <label>{{ form.description.label }} {{ form.description }}</label>
    <label>{{ form.amount.label }} {{ form.amount }}</label>
    <label>{{ form.third_party.label }} {{ form.third_party }}</label>
    <label>{{ form.document_type.label }} {{ form.document_type }}</label>
    <label>{{ form.document_number.label }} {{ form.document_number }}</label>
    <label>{{ form.support.label }}{% if entry.attachment_id %} <small>(ya tiene {{ entry.attachment_name|default:"un soporte" }}; otro lo reemplaza)</small>{% endif %} {{ form.support }}</label>
    <p class="pj-muted">Se guarda en borrador. Para aprobarlo hacen falta el soporte y otra persona.</p>
    <button class="button" type="submit">Guardar</button>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/costs/detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Costo · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="detail" %}
<section class="pj-panel cst-detail">
  <h2 class="pj-name">{{ entry.description }}</h2>
  <p><span class="bg-label cst-{{ entry.status }}">{{ entry.get_status_display }}</span>
    {% if reversal %}<a href="{% url 'costs:detail' project.pk reversal.pk %}">Reversado el {{ reversal.incurred_on|date:"d/m/Y" }}</a>{% endif %}
    {% if entry.reverses %}Reverso de <a href="{% url 'costs:detail' project.pk entry.reverses_id %}">{{ entry.reverses.description }}</a>{% endif %}</p>
  {% if entry.status == "rejected" %}<p class="pj-warning">Motivo del rechazo: {{ entry.decision_note }}</p>{% elif entry.reverses %}<p class="pj-muted">Motivo: {{ entry.decision_note }}</p>{% endif %}
  <dl class="cst-facts">
    <dt>Fecha de causación</dt><dd>{{ entry.incurred_on|date:"d/m/Y" }}</dd>
    <dt>Tipo</dt><dd>{{ entry.get_kind_display }}</dd>
    <dt>Valor (IVA incluido)</dt><dd>{{ entry.amount|signed }}</dd>
    <dt>Asignación</dt><dd>{{ assignment }}</dd>
    <dt>Tercero</dt><dd>{{ entry.third_party|default:"—" }}</dd>
    <dt>Documento</dt><dd>{{ entry.get_document_type_display }} {{ entry.document_number }}</dd>
    <dt>Soporte</dt><dd>{% if entry.attachment_id %}<a href="{% url 'costs:support' project.pk entry.pk %}" target="_blank" rel="noopener">{{ entry.attachment_name|default:"Ver soporte" }}</a>{% else %}Sin soporte{% endif %}</dd>
    <dt>Registró</dt><dd>{{ entry.created_by }} · {{ entry.created_at|date:"d/m/Y H:i" }}</dd>
    {% if entry.decided_by %}<dt>{% if entry.status == "rejected" %}Rechazó{% else %}Aprobó{% endif %}</dt><dd>{{ entry.decided_by }} · {{ entry.decided_at|date:"d/m/Y H:i" }}</dd>{% endif %}
  </dl>
  <div class="pj-filters">
    {% if can_edit %}<a class="button" href="{% url 'costs:edit' project.pk entry.pk %}">{% if entry.status == "rejected" %}Corregir y reenviar{% else %}Editar{% endif %}</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'costs:delete' project.pk entry.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 6: Estilos**

Al final de `server/suno/core/static/suno/css/projects.css`:

```css
/* Libro de costos (costos, pieza 6a). */
.cst-nav { margin: 0 0 16px; }
.cst-ledger td.pj-name { min-width: 200px; max-width: 320px; }
.cst-ledger tr.cst-auto td { color: var(--suno-muted); }
.cst-ledger tfoot th { text-align: right; font-weight: 600; }
.bg-label.cst-draft { background: var(--suno-line-strong); color: var(--suno-ink); }
.bg-label.cst-approved { background: var(--suno-green); }
.bg-label.cst-rejected { background: var(--suno-red); }
.bg-label.cst-auto { background: var(--chart-petrol); }
.cst-mark { color: var(--suno-muted); font-size: 11px; }
.cst-pages { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 12px; font-size: 13px; }
.cst-form { max-width: 640px; }
.cst-items { display: grid; gap: 4px; padding: 8px 12px; border: 1px solid var(--suno-line); border-radius: var(--suno-radius-sm); }
.cst-items ul { margin: 0; padding: 0; list-style: none; }
.cst-facts { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 16px; margin: 12px 0; font-size: 13px; }
.cst-facts dt { color: var(--suno-muted); }
.cst-facts dd { margin: 0; overflow-wrap: anywhere; }
@media (max-width: 767px) {
  .cst-facts { grid-template-columns: minmax(0, 1fr); }
  .cst-facts dd { margin-bottom: 6px; }
  .cst-ledger td.pj-name { min-width: 160px; max-width: 200px; }
}
```

En `server/suno/core/templates/suno/projects/base.html`, cambiar la versión de `projects.css` a `?v=20261112-costs-v1`.

- [ ] **Step 7: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/costs suno/projects config && ruff check suno config && mypy suno/costs suno/projects --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs suno/projects suno/budgets suno/matrix suno/control
```

Expected: PASS (las páginas de proyectos, presupuestos, matriz y control siguen igual con la pestaña nueva).

- [ ] **Step 8: Commit**

```bash
git add server/suno/costs server/suno/projects/views.py server/config/urls.py server/suno/core/templates server/suno/core/static
git commit -m "feat(costs): costs tab with ledger, entry form and support download

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Bandeja, rechazados, reverso, importación y exportación

**Files:**
- Modify: `server/suno/costs/views.py` (`_context`, `detail` y vistas nuevas), `server/suno/costs/urls.py`
- Modify: `server/suno/core/templates/suno/costs/_nav.html`, `detail.html`
- Create: `server/suno/core/templates/suno/costs/inbox.html`, `rejected.html`, `reverse.html`, `import.html`
- Test: `server/suno/costs/tests/test_views_actions.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/costs/tests/test_views_actions.py`:

```python
"""Pestaña "Costos" (spec, 2.3 a 2.7): bandeja, rechazados, reverso, importar y exportar."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.authn.tests.factories import make_user
from suno.budgets.tests.xlsx import build_xlsx
from suno.core.access import SectionRole
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent, CostEntry, CostStatus, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import ledger
from suno.costs.importer import HEADER, SHEET

from .factories import cost_world, entry, grant_costs, support_blob

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def costs_file(*rows: list[object]) -> SimpleUploadedFile:
    return SimpleUploadedFile("costos.xlsx", build_xlsx(rows, header=HEADER, sheet=SHEET), XLSX)


@skip_without_db
class CostActionPagesTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(
            STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.world, self.budget = cost_world()
        grant_costs(self.world, self.world.supervisor, SectionRole.REVIEWER)
        self.contributor = make_user(self.world.org, username="colaborador", role=Role.VIEWER)
        grant_costs(self.world, self.contributor, SectionRole.CONTRIBUTOR)
        self.viewer = make_user(self.world.org, username="consulta", role=Role.VIEWER)
        grant_costs(self.world, self.viewer, SectionRole.VIEWER)
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/costos/"

    def test_inbox_lists_drafts_of_others_and_decides(self) -> None:
        theirs = entry(self.world, description="Arena de río", attachment=support_blob())
        entry(self.world, description="Mío", created_by=self.world.supervisor)
        self.client.force_login(self.world.supervisor)
        inbox = self.client.get(self.base + "por-aprobar/")
        self.assertContains(inbox, "Arena de río")
        self.assertNotContains(inbox, "Mío")
        self.assertContains(inbox, "Por aprobar (1)")
        response = self.client.post(f"{self.base}{theirs.pk}/aprobar/", {"volver": "bandeja"})
        self.assertRedirects(response, self.base + "por-aprobar/")
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, CostStatus.APPROVED)

    def test_self_approval_is_explained(self) -> None:
        own = entry(self.world, attachment=support_blob())
        self.client.force_login(self.world.admin)
        response = self.client.post(f"{self.base}{own.pk}/aprobar/", follow=True)
        self.assertContains(response, "Nadie aprueba")
        own.refresh_from_db()
        self.assertEqual(own.status, CostStatus.DRAFT)

    def test_contributor_does_not_open_the_inbox(self) -> None:
        self.client.force_login(self.contributor)
        self.assertEqual(self.client.get(self.base + "por-aprobar/").status_code, 403)

    def test_rejection_and_the_authors_list(self) -> None:
        draft = entry(self.world, description="Formaleta", created_by=self.contributor)
        self.client.force_login(self.world.supervisor)
        empty = self.client.post(f"{self.base}{draft.pk}/rechazar/", {"motivo": " "}, follow=True)
        self.assertContains(empty, "Escribe el motivo")
        self.client.post(f"{self.base}{draft.pk}/rechazar/", {"motivo": "Falta la factura"})
        self.client.force_login(self.contributor)
        page = self.client.get(self.base + "rechazados/")
        self.assertContains(page, "Formaleta")
        self.assertContains(page, "Falta la factura")
        self.assertContains(page, "Mis rechazados (1)")
        self.assertContains(page, f"{self.base}{draft.pk}/editar/")

    def test_reverse_and_reopen(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED, description="Factura duplicada")
        self.client.force_login(self.world.supervisor)
        self.assertEqual(self.client.get(f"{self.base}{cost.pk}/reversar/").status_code, 403)
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/"), "Reversar")
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/reversar/"), "Reversar costo")
        response = self.client.post(
            f"{self.base}{cost.pk}/reversar/", {"motivo": "Duplicada", "reabrir": "1"}
        )
        reversal = CostEntry.objects.get(reverses=cost)
        self.assertEqual(reversal.amount, -cost.amount)
        self.assertTrue(response["Location"].startswith(f"{self.base}nuevo/?copiar={cost.pk}"))
        self.assertContains(self.client.get(response["Location"]), 'value="Factura duplicada"')
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/"), "Reversado el")

    def test_import_preview_and_apply(self) -> None:
        self.client.force_login(self.contributor)
        good = [date(2026, 3, 10), "Materiales", "2.1", "", "Cemento", 950000, "", "Factura", "1"]
        preview = self.client.post(self.base + "importar/", {"file": costs_file(good)})
        self.assertContains(preview, "1 costo por")
        self.assertContains(preview, "Cargar en borrador")
        token = preview.context["result"].token
        response = self.client.post(self.base + "importar/aplicar/", {"token": token})
        self.assertRedirects(response, self.base + "?estado=draft")
        self.assertEqual(CostEntry.objects.filter(status=CostStatus.DRAFT).count(), 1)
        bad = self.client.post(
            self.base + "importar/", {"file": costs_file([*good[:2], "9.9", *good[3:]])}
        )
        self.assertContains(bad, "Fila 2")
        self.assertNotContains(bad, "Cargar en borrador")
        wrong = SimpleUploadedFile("costos.xls", b"x", "application/vnd.ms-excel")
        self.assertEqual(self.client.post(self.base + "importar/", {"file": wrong}).status_code, 200)

    def test_template_and_export(self) -> None:
        entry(self.world, status=CostStatus.APPROVED, description="Cemento gris")
        self.client.force_login(self.contributor)
        template = self.client.get(self.base + "plantilla.xlsx")
        self.assertEqual(template["Content-Type"], XLSX)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.base + "plantilla.xlsx").status_code, 403)
        export = self.client.get(self.base + "libro.xlsx?estado=approved")
        self.assertEqual(export["Content-Type"], XLSX)
        self.assertIn("costos-", export["Content-Disposition"])
        event = AuditEvent.objects.get(action=AuditAction.COST_EXPORTED)
        self.assertEqual(event.metadata["lines"], 1)
        with mock.patch.object(ledger, "MAX_EXPORT_LINES", 0):
            refused = self.client.get(self.base + "libro.xlsx", follow=True)
        self.assertContains(refused, "filtra por fechas")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_views_actions.py`
Expected: FAIL (404 en `por-aprobar/`, `rechazar/`, `importar/`…).

- [ ] **Step 3: Las vistas**

En `server/suno/costs/views.py`:

1. Imports nuevos:

```python
from urllib.parse import urlencode

from django.db import transaction
from django.urls import reverse

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.download import content_disposition
from suno.core.policies import can_approve_costs, can_reverse_costs

from . import importer
from .export import ledger_workbook
from .ledger import MAX_EXPORT_LINES, LedgerTooLarge, export_lines
```

(y `can_approve_costs`, `can_reverse_costs` en el import existente de `suno.core.policies`).

2. Reemplazar `_context` por:

```python
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Permisos de la pestaña y los contadores de "Por aprobar" y "Mis rechazados"."""
    can_decide = can_approve_costs(ctx, project)
    entries = scoped_qs(ctx, CostEntry).filter(project=project)
    pending = (
        entries.filter(status=CostStatus.DRAFT)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
        .count()
        if can_decide
        else 0
    )
    return {
        **_project_context(ctx, project),
        "can_create_cost": can_create_costs(ctx, project),
        "can_import_cost": can_import_costs(ctx, project),
        "can_export_cost": can_export_costs(ctx, project),
        "can_decide_cost": can_decide,
        "pending_count": pending,
        "rejected_count": entries.filter(
            status=CostStatus.REJECTED, created_by_id=ctx.user_id
        ).count(),
    }
```

3. En `detail`, calcular `reversal` antes del `_render` y agregar al contexto:

```python
    reversal = scoped_qs(ctx, CostEntry).filter(reverses=entry).first()
    ...
            "reversal": reversal,
            "can_decide": entry.status == CostStatus.DRAFT
            and can_approve_costs(ctx, project)
            and ctx.user_id not in (entry.created_by_id, entry.updated_by_id),
            "can_reverse": entry.status == CostStatus.APPROVED
            and entry.reverses_id is None
            and reversal is None
            and can_reverse_costs(ctx, project),
```

4. Al final del archivo:

```python
def _back(request: HttpRequest, project: Project, entry_id: UUID) -> HttpResponse:
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return redirect("costs:detail", project.pk, entry_id)


@login_required(login_url="platform_login")
@require_GET
def inbox(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Borradores de otras personas (ni registrados ni modificados por quien mira)."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_approve_costs(ctx, project):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    entries = (
        scoped_qs(ctx, CostEntry)
        .filter(project=project, status=CostStatus.DRAFT)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
        .select_related("created_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .order_by("incurred_on", "created_at")
    )
    return _render(
        request,
        ctx,
        "suno/costs/inbox.html",
        {
            **_context(ctx, project),
            "rows": [(entry, assignment_text(entry, catalog)) for entry in entries],
        },
    )


@login_required(login_url="platform_login")
@require_GET
def rejected(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Los rechazados de quien mira, con su motivo, para corregirlos y reenviarlos."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    catalog = load_catalog(ctx, project)
    entries = (
        scoped_qs(ctx, CostEntry)
        .filter(project=project, status=CostStatus.REJECTED, created_by_id=ctx.user_id)
        .select_related("decided_by", "chapter", "task")
        .prefetch_related("item_links__item")
        .order_by("-decided_at")
    )
    return _render(
        request,
        ctx,
        "suno/costs/rejected.html",
        {
            **_context(ctx, project),
            "rows": [(entry, assignment_text(entry, catalog)) for entry in entries],
        },
    )


@login_required(login_url="platform_login")
@require_POST
def approve(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.approve(ctx, project, entry_id)
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Costo aprobado.")
    return _back(request, project, entry_id)


@login_required(login_url="platform_login")
@require_POST
def reject(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        services.reject(ctx, project, entry_id, request.POST.get("motivo", ""))
    except CostRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Costo rechazado; su autor lo verá con el motivo.")
    return _back(request, project, entry_id)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def reverse_entry(request: HttpRequest, project_id: UUID, entry_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    entry = get_entry(ctx, project, entry_id)
    if not can_reverse_costs(ctx, project):
        raise PermissionDenied
    errors: list[str] = []
    if request.method == "POST":
        try:
            reversal = services.reverse(ctx, project, entry.pk, request.POST.get("motivo", ""))
        except CostRejected as exc:
            errors = exc.errors
        else:
            messages.success(request, "Costo reversado.")
            if request.POST.get("reabrir"):
                query = urlencode(
                    {
                        "copiar": entry.pk,
                        "alcance": entry.scope,
                        "capitulo": entry.chapter_id or "",
                    }
                )
                return redirect(f"{reverse('costs:new', args=[project.pk])}?{query}")
            return redirect("costs:detail", project.pk, reversal.pk)
    return _render(
        request,
        ctx,
        "suno/costs/reverse.html",
        {**_context(ctx, project), "entry": entry, "errors": errors},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_import_costs(ctx, project):
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
            except ExcelRejected as exc:
                errors = [str(exc)]
            else:
                result = importer.preview(ctx, project, upload.read())
    return _render(
        request,
        ctx,
        "suno/costs/import.html",
        {**_context(ctx, project), "errors": errors, "result": result},
    )


@login_required(login_url="platform_login")
@require_POST
def import_apply(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    try:
        created = importer.apply(ctx, project, request.POST.get("token", ""))
    except CostRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("costs:import", project.pk)
    plural = "s" if created != 1 else ""
    messages.success(
        request,
        f"{created} costo{plural} importado{plural} en borrador. Adjunta el soporte de cada uno.",
    )
    return redirect(f"{reverse('costs:list', args=[project.pk])}?estado=draft")


@login_required(login_url="platform_login")
@require_GET
def template_download(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_import_costs(ctx, project):
        raise PermissionDenied
    response = HttpResponse(importer.template_bytes(ctx, project), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"plantilla-costos-{project.code or project.name}", ".xlsx", fallback="plantilla-costos"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def export(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """El libro filtrado, en Excel (auditado)."""
    ctx = _ctx(request)
    project = get_costs_project(ctx, project_id)
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    filters = LedgerFilters.from_query(request.GET)
    try:
        lines = export_lines(ctx, project, filters, catalog=load_catalog(ctx, project))
    except LedgerTooLarge:
        messages.error(
            request,
            f"El libro filtrado pasa de {MAX_EXPORT_LINES} líneas: filtra por fechas.",
        )
        return redirect(f"{reverse('costs:list', args=[project.pk])}?{filters.query}")
    content = ledger_workbook(lines)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.COST_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"lines": len(lines), "filters": filters.query},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"costos-{project.code or project.name}", ".xlsx", fallback="costos"
    )
    return response
```

Nota: `test_template_and_export` cuenta 1 línea con `estado=approved` porque `cost_world` no tiene usos de equipo ni viajes.

En `server/suno/costs/urls.py`, agregar a `urlpatterns`:

```python
    path("por-aprobar/", views.inbox, name="inbox"),
    path("rechazados/", views.rejected, name="rejected"),
    path("importar/", views.import_upload, name="import"),
    path("importar/aplicar/", views.import_apply, name="import_apply"),
    path("plantilla.xlsx", views.template_download, name="template"),
    path("libro.xlsx", views.export, name="export"),
    path("<uuid:entry_id>/aprobar/", views.approve, name="approve"),
    path("<uuid:entry_id>/rechazar/", views.reject, name="reject"),
    path("<uuid:entry_id>/reversar/", views.reverse_entry, name="reverse"),
```

- [ ] **Step 4: Plantillas**

Reemplazar `server/suno/core/templates/suno/costs/_nav.html`:

```html
<nav class="pj-tabs cst-nav" aria-label="Libro de costos">
  <a href="{% url 'costs:list' project.pk %}" {% if sub == "list" %}aria-current="page"{% endif %}>Libro</a>
  {% if can_decide_cost %}<a href="{% url 'costs:inbox' project.pk %}" {% if sub == "inbox" %}aria-current="page"{% endif %}>Por aprobar ({{ pending_count }})</a>{% endif %}
  {% if rejected_count or sub == "rejected" %}<a href="{% url 'costs:rejected' project.pk %}" {% if sub == "rejected" %}aria-current="page"{% endif %}>Mis rechazados ({{ rejected_count }})</a>{% endif %}
  {% if can_create_cost %}<a href="{% url 'costs:new' project.pk %}" {% if sub == "new" %}aria-current="page"{% endif %}>Nuevo costo</a>{% endif %}
  {% if can_import_cost %}<a href="{% url 'costs:import' project.pk %}" {% if sub == "import" %}aria-current="page"{% endif %}>Importar Excel</a>{% endif %}
  {% if can_export_cost %}<a href="{% url 'costs:export' project.pk %}{% if filters.query %}?{{ filters.query }}{% endif %}">Exportar a Excel</a>{% endif %}
</nav>
```

En `server/suno/core/templates/suno/costs/detail.html`, antes de `</section>`:

```html
  {% if can_decide %}
  <div class="cst-decide">
    <form method="post" action="{% url 'costs:approve' project.pk entry.pk %}">{% csrf_token %}<button class="button" type="submit"{% if not entry.attachment_id %} disabled title="Falta el soporte"{% endif %}>Aprobar</button></form>
    <form method="post" action="{% url 'costs:reject' project.pk entry.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo del rechazo <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Rechazar</button></form>
  </div>
  {% endif %}
  {% if can_reverse %}<p><a class="text-link" href="{% url 'costs:reverse' project.pk entry.pk %}">Reversar</a></p>{% endif %}
```

`server/suno/core/templates/suno/costs/inbox.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Por aprobar · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="inbox" %}
<section class="pj-panel">
  <h2>Por aprobar</h2>
  <p class="pj-muted">Borradores de otras personas. Aprobar exige el soporte; rechazar, un motivo.</p>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <thead><tr><th>Fecha</th><th>Descripción</th><th>Asignación</th><th class="num">Valor</th><th>Registró</th><th>Soporte</th><th>Decisión</th></tr></thead>
    <tbody>
    {% for entry, assignment in rows %}
      <tr>
        <td>{{ entry.incurred_on|date:"d/m/Y" }}</td>
        <td class="pj-name"><a href="{% url 'costs:detail' project.pk entry.pk %}">{{ entry.description }}</a><br><small class="pj-muted">{{ entry.get_kind_display }}{% if entry.third_party %} · {{ entry.third_party }}{% endif %}</small></td>
        <td class="pj-wrap">{{ assignment }}</td>
        <td class="num">{{ entry.amount|signed }}</td>
        <td class="pj-wrap">{{ entry.created_by }}</td>
        <td>{% if entry.attachment_id %}<a href="{% url 'costs:support' project.pk entry.pk %}" target="_blank" rel="noopener">Ver</a>{% else %}<span class="pj-late">Sin soporte</span>{% endif %}</td>
        <td class="cst-decide">
          <form method="post" action="{% url 'costs:approve' project.pk entry.pk %}">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><button class="button" type="submit"{% if not entry.attachment_id %} disabled{% endif %}>Aprobar</button></form>
          <form method="post" action="{% url 'costs:reject' project.pk entry.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><label class="sr-only" for="motivo-{{ entry.pk }}">Motivo del rechazo</label><input id="motivo-{{ entry.pk }}" name="motivo" maxlength="500" required placeholder="Motivo del rechazo"><button class="text-link" type="submit">Rechazar</button></form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay borradores por aprobar.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/costs/rejected.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Mis rechazados · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="rejected" %}
<section class="pj-panel">
  <h2>Mis rechazados</h2>
  <p class="pj-muted">Corrígelos y guárdalos: vuelven a borrador para que otra persona los apruebe.</p>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <thead><tr><th>Fecha</th><th>Descripción</th><th>Asignación</th><th class="num">Valor</th><th>Motivo</th><th>Rechazó</th><th></th></tr></thead>
    <tbody>
    {% for entry, assignment in rows %}
      <tr>
        <td>{{ entry.incurred_on|date:"d/m/Y" }}</td>
        <td class="pj-name">{{ entry.description }}</td>
        <td class="pj-wrap">{{ assignment }}</td>
        <td class="num">{{ entry.amount|signed }}</td>
        <td class="pj-wrap">{{ entry.decision_note }}</td>
        <td class="pj-wrap">{{ entry.decided_by }}</td>
        <td><a class="text-link" href="{% url 'costs:edit' project.pk entry.pk %}">Corregir</a></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No tienes costos rechazados.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/costs/reverse.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Reversar costo · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="detail" %}
<section class="pj-panel">
  <h2>Reversar costo</h2>
  <p class="pj-name"><a href="{% url 'costs:detail' project.pk entry.pk %}">{{ entry.description }}</a> · {{ entry.incurred_on|date:"d/m/Y" }} · {{ entry.amount|signed }}</p>
  <p class="pj-muted">Se registra hoy un movimiento aprobado por el mismo valor en negativo y con la misma asignación. Los periodos pasados del control no cambian.</p>
  {% if errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
  <form method="post" class="pj-form">
    {% csrf_token %}
    <label>Motivo <textarea name="motivo" rows="3" maxlength="500" required></textarea></label>
    <label class="cst-check"><input type="checkbox" name="reabrir" value="1"> Abrir un borrador nuevo con los mismos datos</label>
    <button class="button" type="submit">Reversar</button>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/costs/import.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Importar costos · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="costs" %}
{% include "suno/costs/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Importar costos desde Excel</h2>
  <p><a href="{% url 'costs:template' project.pk %}">Descarga la plantilla</a>: una fila por costo en la hoja «Costos». Las otras hojas listan los tipos, los documentos, los códigos del presupuesto vigente y las tareas.</p>
  <ul class="pj-muted">
    <li>El código de un ítem asigna el ítem; el de un capítulo, el capítulo completo; sin código, el costo es indirecto (del proyecto, sin tarea).</li>
    <li>La tarea va por su número de esquema (por ejemplo 1.2) y es opcional.</li>
    <li>Todo entra en borrador; el soporte se adjunta después en cada costo. Si una fila tiene un error, no se carga ninguna.</li>
  </ul>
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
  <p>{{ result.rows|length }} costo{{ result.rows|length|pluralize }} por {{ result.total|money }}.</p>
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <thead><tr><th>Fila</th><th>Fecha</th><th>Tipo</th><th>Asignación</th><th>Tarea</th><th>Descripción</th><th class="num">Valor</th></tr></thead>
    <tbody>
    {% for row in result.rows %}
      <tr><td>{{ row.line }}</td><td>{{ row.data.incurred_on|date:"d/m/Y" }}</td><td>{{ row.kind_label }}</td><td class="pj-wrap">{{ row.assignment }}</td><td class="pj-wrap">{{ row.task|default:"—" }}</td><td class="pj-name">{{ row.data.description }}</td><td class="num">{{ row.data.amount|signed }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
  {% if result.token %}
  <form method="post" action="{% url 'costs:import_apply' project.pk %}" class="pj-filters">
    {% csrf_token %}
    <input type="hidden" name="token" value="{{ result.token }}">
    <button class="button" type="submit">Cargar en borrador</button>
  </form>
  {% endif %}
</section>
{% endif %}
{% endblock %}
```

Al final de `projects.css`, dentro del bloque de costos:

```css
.cst-decide { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
.cst-reject { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.cst-reject input { min-height: 32px; padding: 4px 8px; border: 1px solid var(--suno-line-strong); border-radius: var(--suno-radius-sm); font: inherit; font-size: 12px; }
.cst-check { display: flex; gap: 8px; align-items: center; }
```

y subir la versión a `?v=20261112-costs-v2` en `projects/base.html`.

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/costs && ruff check suno/costs && mypy suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/suno/costs server/suno/core/templates server/suno/core/static
git commit -m "feat(costs): approval inbox, rejections, reversal, excel import and export

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Cálculo del costo real (puro)

**Files:**
- Create: `server/suno/control/indices.py`, `server/suno/control/actual_cost.py`
- Modify: `server/suno/control/earned_value.py` (imports y constantes, `spi_of`, `PricedItem`, `ControlInput`, `PeriodRow`, `BreakdownRow`, `ControlResult`, `_Totals.row`, `worst_cost_first`, segunda mitad de `_compute`)
- Test: `server/suno/control/tests/test_actual_cost.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_actual_cost.py`:

```python
"""Costo real en el control (costos, pieza 6a, spec sección 3). Sin BD."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control import actual_cost, earned_value
from suno.control.actual_cost import (
    CHAPTER,
    EQUIPMENT,
    INDIRECT,
    ITEM,
    LEDGER,
    SELECTION,
    TRANSPORT,
    UNASSIGNED,
    AdminLine,
    ChapterRef,
    CostInput,
    CostLine,
    RetiredCost,
    UnassignedCost,
)
from suno.control.earned_value import (
    ControlInput,
    ControlResult,
    Execution,
    PlanPair,
    PricedItem,
    TaskWindow,
    compute,
    worst_cost_first,
)
from suno.control.periods import WEEK
from suno.projects.workdays import WorkCalendar

D = Decimal
T1, T2 = uuid.uuid4(), uuid.uuid4()
EXC, CONC, STEEL, RETIRED = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CH2, CH21, GONE = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
B1, B2 = uuid.uuid4(), uuid.uuid4()
MON, FRI = date(2026, 3, 2), date(2026, 3, 13)
CUT = date(2026, 3, 31)

ITEMS = {
    EXC: PricedItem(EXC, "1.1 Excavación", "m3", D("1000"), "1 Preliminares", budget_id=B1),
    CONC: PricedItem(CONC, "2.1.1 Concreto", "m3", D("450000"), "2 Estructura", budget_id=B2),
    STEEL: PricedItem(STEEL, "2.2 Acero", "kg", D("4200"), "2 Estructura", budget_id=B2),
}
CHAPTERS = {
    CH2: ChapterRef("2 Estructura", "2 Estructura", 0, frozenset({CONC, STEEL})),
    CH21: ChapterRef("2.1 Concretos", "2 Estructura", 1, frozenset({CONC})),
}
LABELS = {
    **{key: item.label for key, item in ITEMS.items()},
    RETIRED: "2.9 Retirado",
    GONE: "3 Capítulo retirado",
}


def line(
    day: date,
    amount: str,
    scope: str = ITEM,
    *,
    items: tuple[uuid.UUID, ...] = (),
    chapter: uuid.UUID | None = None,
    task: uuid.UUID | None = None,
    origin: str = LEDGER,
) -> CostLine:
    return CostLine(day, D(amount), scope, items, chapter, task, origin)


def control(
    lines: Sequence[CostLine],
    *,
    cut: date = CUT,
    admin: Sequence[AdminLine] = (AdminLine(B1, "Contrato", D("0.1")),),
    pending: tuple[int, str] = (0, "0"),
) -> ControlResult:
    tasks = {
        T1: TaskWindow(T1, "1.1 Excavación", "Tramo 1", MON, FRI, True, False, 1),
        T2: TaskWindow(T2, "2.1 Columnas", "", MON, FRI, True, False, 2),
    }
    return compute(
        ControlInput(
            cut=cut,
            kind=WEEK,
            calendar=WorkCalendar(),
            tasks=tasks,
            items=ITEMS,
            item_labels=LABELS,
            plans=[
                PlanPair(T1, EXC, D("100")),
                PlanPair(T2, CONC, D("10")),
                PlanPair(T2, STEEL, D("800")),
            ],
            executions=[
                Execution(T1, EXC, date(2026, 3, 5), D("50")),
                Execution(T2, CONC, date(2026, 3, 5), D("2")),
            ],
            costs=CostInput(
                lines=list(lines),
                chapters=CHAPTERS,
                admin=list(admin),
                pending_count=pending[0],
                pending_amount=D(pending[1]),
            ),
        )
    )


#: BAC = 100.000 + 4.500.000 + 3.360.000 = 7.960.000. EV = 50.000 + 900.000 = 950.000.
BASIC = [
    line(date(2026, 3, 6), "40000", items=(EXC,), task=T1),
    line(date(2026, 3, 7), "10000", UNASSIGNED, origin=EQUIPMENT),
    line(date(2026, 3, 10), "5000", UNASSIGNED, origin=TRANSPORT),
    line(date(2026, 3, 11), "8000", INDIRECT),
]


class IndicatorTests(SimpleTestCase):
    def test_three_buckets_and_indices(self) -> None:
        cost = control(BASIC).cost
        assert cost is not None
        self.assertEqual(
            (cost.ac_assigned, cost.ac_unassigned, cost.ac_direct, cost.ac_indirect),
            (D("40000.00"), D("15000.00"), D("55000.00"), D("8000.00")),
        )
        self.assertEqual((cost.bac, cost.ev), (D("7960000.00"), D("950000.00")))
        self.assertEqual(cost.cv, D("895000.00"))
        self.assertEqual(cost.cpi, D("17.27"))
        self.assertEqual((cost.status, cost.status_label), ("green", "En presupuesto"))
        # EAC = 55.000 + (7.960.000 − 950.000) × 55.000 / 950.000
        self.assertEqual(cost.eac, D("460842.11"))
        self.assertEqual(cost.vac, D("7499157.89"))
        self.assertFalse(cost.over_budget)
        self.assertEqual(cost.tcpi, D("0.89"))
        self.assertEqual(cost.ac_share, D("0.7"))
        # Administración ganada: solo el presupuesto B1 (EV 50.000 × 10 %).
        self.assertEqual(cost.admin_earned, D("5000.00"))
        self.assertEqual(cost.admin_cpi, D("0.63"))  # 5.000 / 8.000 = 0,625
        self.assertEqual(cost.total_cpi, D("15.16"))  # 955.000 / 63.000

    def test_over_budget(self) -> None:
        cost = control([line(date(2026, 3, 6), "2000000", items=(CONC,), chapter=CH21)]).cost
        assert cost is not None
        self.assertEqual(cost.cpi, D("0.48"))  # 950.000 / 2.000.000
        self.assertEqual(cost.status_label, "Sobrecosto")
        self.assertTrue(cost.over_budget)
        self.assertEqual(cost.eac, D("16757894.74"))  # 2.000.000 + 7.010.000 × 2.000.000 / 950.000

    def test_without_cost(self) -> None:
        result = control([])
        cost = result.cost
        assert cost is not None
        self.assertTrue(cost.empty)
        self.assertIsNone(cost.cpi)
        self.assertIsNone(cost.eac)
        self.assertIsNone(cost.vac)
        self.assertEqual(cost.status_label, "Sin costo")

    def test_cost_without_earned_value(self) -> None:
        cost = control([line(date(2026, 3, 3), "1000", items=(EXC,))], cut=date(2026, 3, 4)).cost
        assert cost is not None
        self.assertEqual(cost.cpi, D("0.00"))
        self.assertIsNone(cost.eac)

    def test_exhausted_budget(self) -> None:
        result = control([line(date(2026, 3, 6), "8000000", items=(CONC,), chapter=CH21)])
        assert result.cost is not None and result.cost_warnings is not None
        self.assertIsNone(result.cost.tcpi)
        self.assertTrue(result.cost_warnings.exhausted)

    def test_admin_lines(self) -> None:
        none = control(BASIC, admin=[AdminLine(B1, "Contrato", None)]).cost
        assert none is not None
        self.assertIsNone(none.admin_earned)
        self.assertIsNone(none.admin_cpi)
        both = control(
            BASIC, admin=[AdminLine(B1, "Contrato", D("0.1")), AdminLine(B2, "Interventoría", None)]
        )
        assert both.cost is not None and both.cost_warnings is not None
        self.assertEqual(both.cost.admin_earned, D("5000.00"))
        self.assertEqual(both.cost_warnings.no_admin, ("Interventoría",))
        two = control(
            BASIC,
            admin=[AdminLine(B1, "Contrato", D("0.1")), AdminLine(B2, "Interventoría", D("0.05"))],
        ).cost
        assert two is not None
        self.assertEqual(two.admin_earned, D("50000.00"))  # 5.000 + 900.000 × 5 %


class PeriodTests(SimpleTestCase):
    def test_ac_by_period_until_the_cut(self) -> None:
        rows = control(BASIC).periods
        self.assertEqual(
            [(row.period.start, row.ac, row.ac_cum) for row in rows[:3]],
            [
                (date(2026, 3, 2), D("50000.00"), D("50000.00")),
                (date(2026, 3, 9), D("5000.00"), D("55000.00")),
                (date(2026, 3, 16), D("0.00"), D("55000.00")),
            ],
        )

    def test_a_reversal_does_not_change_past_periods(self) -> None:
        rows = control(
            [
                line(date(2026, 3, 6), "40000", items=(EXC,)),
                line(date(2026, 3, 20), "-40000", items=(EXC,)),
            ]
        ).periods
        self.assertEqual(rows[0].ac_cum, D("40000.00"))
        self.assertEqual(rows[2].ac, D("-40000.00"))
        self.assertEqual(rows[2].ac_cum, D("0.00"))

    def test_after_the_cut_does_not_count_and_before_the_start_extends(self) -> None:
        result = control(
            [line(date(2026, 4, 15), "999", items=(EXC,)), line(date(2026, 2, 20), "10", INDIRECT)]
        )
        assert result.cost is not None
        self.assertEqual(result.cost.ac_direct, D("0.00"))
        self.assertEqual(result.cost.ac_indirect, D("10.00"))
        self.assertEqual(result.periods[0].period.start, date(2026, 3, 2))  # indirecto: no extiende
        early = control([line(date(2026, 2, 20), "10", items=(EXC,))])
        self.assertEqual(early.periods[0].period.start, date(2026, 2, 16))


class BreakdownTests(SimpleTestCase):
    def test_chapter_task_and_location_rows(self) -> None:
        result = control(BASIC)
        chapter = {row.label: row for row in result.by_chapter}["1 Preliminares"]
        assert chapter.cost is not None
        self.assertEqual((chapter.cost.ac, chapter.cost.cpi), (D("40000.00"), D("1.25")))
        task = {row.label: row for row in result.by_task}["1.1 Excavación"]
        assert task.cost is not None
        self.assertEqual(task.cost.ac, D("40000.00"))
        self.assertTrue(all(row.cost is None for row in result.by_location))
        assert result.cost_warnings is not None
        self.assertEqual(result.cost_warnings.no_task, D("15000.00"))
        self.assertEqual(
            result.cost_warnings.unassigned,
            (
                UnassignedCost("Equipos propios sin ítem", D("10000.00")),
                UnassignedCost("Transporte de terceros", D("5000.00")),
            ),
        )

    def test_chapter_and_selection_groups(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "1000000", CHAPTER, chapter=CH2),
                line(date(2026, 3, 6), "500000", items=(CONC,), chapter=CH21),
                line(date(2026, 3, 9), "100000", SELECTION, items=(CONC, STEEL), chapter=CH21),
            ]
        )
        items = {row.label: row for row in result.by_item}
        concrete, steel = items["2.1.1 Concreto"].cost, items["2.2 Acero"].cost
        assert concrete is not None and steel is not None
        self.assertEqual((concrete.ac, concrete.group), (D("500000.00"), "2 Estructura"))
        self.assertEqual((concrete.group_ev, concrete.group_ac), (D("900000.00"), D("1600000.00")))
        self.assertEqual(concrete.cpi, D("0.56"))
        self.assertEqual(concrete.cv, D("-700000.00"))
        self.assertEqual((steel.ac, steel.cpi), (D("0.00"), D("0.56")))
        chapter = {row.label: row for row in result.by_chapter}["2 Estructura"]
        assert chapter.cost is not None
        self.assertEqual(chapter.cost.ac, D("1600000.00"))
        excavation = items["1.1 Excavación"].cost
        assert excavation is not None
        self.assertEqual((excavation.group, excavation.cpi), ("", None))

    def test_a_selection_alone_is_named_after_its_chapter(self) -> None:
        result = control(
            [line(date(2026, 3, 9), "100000", SELECTION, items=(CONC, STEEL), chapter=CH21)]
        )
        concrete = {row.label: row for row in result.by_item}["2.1.1 Concreto"].cost
        assert concrete is not None
        self.assertEqual(concrete.group, "2.1 Concretos")

    def test_retired_items_count_in_their_chapter(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "300", items=(RETIRED,), chapter=CH2),
                line(date(2026, 3, 6), "200", items=(RETIRED,), origin=EQUIPMENT),
                line(date(2026, 3, 6), "100", CHAPTER, chapter=GONE),
            ]
        )
        chapters = {row.label: row.cost.ac for row in result.by_chapter if row.cost}
        self.assertEqual(chapters["2 Estructura"], D("300.00"))
        self.assertEqual(chapters["Sin capítulo"], D("300.00"))
        assert result.cost_warnings is not None
        self.assertEqual(
            result.cost_warnings.retired,
            (
                RetiredCost("2.9 Retirado", "2 Estructura", D("300.00")),
                RetiredCost("2.9 Retirado", "Sin capítulo", D("200.00")),
                RetiredCost("3 Capítulo retirado", "Sin capítulo", D("100.00")),
            ),
        )

    def test_order_by_cpi(self) -> None:
        result = control(
            [
                line(date(2026, 3, 6), "100000", items=(EXC,)),
                line(date(2026, 3, 6), "100000", CHAPTER, chapter=CH2),
            ]
        )
        self.assertEqual(
            [row.label for row in worst_cost_first(result.by_chapter)],
            ["1 Preliminares", "2 Estructura"],  # 0,50 antes que 9,00
        )

    def test_pending_drafts_and_no_costs_permission(self) -> None:
        result = control(BASIC, pending=(2, "1500"))
        assert result.cost_warnings is not None
        self.assertEqual(
            (result.cost_warnings.pending_count, result.cost_warnings.pending_amount),
            (2, D("1500.00")),
        )
        self.assertEqual(actual_cost.NO_CHAPTER, earned_value.NO_CHAPTER)
```

Notas:
- `assert` en pruebas está permitido (bandit excluye `tests/`); se usa para estrechar tipos antes de leer atributos.
- Los números: BAC = 100 × 1.000 + 10 × 450.000 + 800 × 4.200 = 7.960.000; EV = 50 × 1.000 + 2 × 450.000 = 950.000. En `test_chapter_and_selection_groups`, el grupo une CONC y STEEL (capítulo 2 y selección del 2.1): AC 1.000.000 + 500.000 + 100.000 = 1.600.000, EV 900.000, CPI 0,5625 → 0,56.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_actual_cost.py`
Expected: ERROR de import (`No module named 'suno.control.actual_cost'`).

- [ ] **Step 3: Índices compartidos**

`server/suno/control/indices.py`:

```python
"""Índices del control (SPI y CPI) y porcentajes del BAC. Puro.

Un índice es un cociente a dos decimales (`ROUND_HALF_UP`) y su color se decide sobre el valor
redondeado: verde desde 0,95, ámbar desde 0,85, rojo por debajo (pieza 5, 1.4; pieza 6a, 3.2).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from suno.budgets.totals import HUNDRED, WIDE_PRECISION

INDEX_STEP = Decimal("0.01")
SHARE_STEP = Decimal("0.1")
GREEN_FROM = Decimal("0.95")
AMBER_FROM = Decimal("0.85")


def ratio_of(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """`numerator / denominator` a dos decimales; `None` si el denominador no es positivo."""
    if denominator <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (numerator / denominator).quantize(INDEX_STEP, rounding=ROUND_HALF_UP)


def status_of(index: Decimal | None) -> str:
    """Verde desde 0,95; ámbar desde 0,85; rojo por debajo; "none" sin índice."""
    if index is None:
        return "none"
    if index >= GREEN_FROM:
        return "green"
    if index >= AMBER_FROM:
        return "amber"
    return "red"


def share_of(value: Decimal, total: Decimal) -> Decimal | None:
    """% de `total` a un decimal; `None` si `total` es 0."""
    if total <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (value * HUNDRED / total).quantize(SHARE_STEP, rounding=ROUND_HALF_UP)
```

- [ ] **Step 4: El cálculo del costo real**

`server/suno/control/actual_cost.py`:

```python
"""Costo real del proyecto (costos, pieza 6a). Puro: sin BD.

Diseño: plans/2026-11-12-costos-pieza6a-costo-real-design.md, sección 3.

- **AC** al corte: movimientos aprobados del libro con fecha hasta el corte, más los costos
  automáticos (equipos propios y transporte de terceros). Un reverso cae en su propia fecha.
- Tres bolsas: directo asignado (ítem, capítulo, selección; equipos con ítem), directo sin
  asignar (equipos sin ítem y transporte) e indirecto (al proyecto).
- `CPI = EV / AC directo`; `EAC = AC + (BAC − EV) / CPI`; `TCPI = (BAC − EV) / (BAC − AC)`.
- Administración ganada: EV de cada presupuesto × (línea Administración ÷ costo directo).
- Grupos: los ítems que reciben costo de un capítulo o de una selección comparten índice; si
  se traslapan, se unen. Sin reparto del costo del grupo entre sus ítems.

No importa `earned_value` (al revés sí): recibe del valor ganado solo lo que necesita.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from typing import Protocol
from uuid import UUID

from suno.budgets.totals import WIDE_PRECISION, money

from .indices import ratio_of, share_of, status_of

ZERO = Decimal(0)
#: Asignaciones de una línea (las del libro, más "sin asignar" para los automáticos).
INDIRECT = "indirect"
ITEM = "item"
CHAPTER = "chapter"
SELECTION = "selection"
UNASSIGNED = "unassigned"
#: Origen de una línea.
LEDGER = "ledger"
EQUIPMENT = "equipment"
TRANSPORT = "transport"
#: El mismo texto que `earned_value.NO_CHAPTER` (lo comprueba una prueba).
NO_CHAPTER = "Sin capítulo"
UNKNOWN = "Ítem desconocido"
ORIGIN_LABELS = {EQUIPMENT: "Equipos propios sin ítem", TRANSPORT: "Transporte de terceros"}
COST_LABELS = {
    "green": "En presupuesto",
    "amber": "Atención",
    "red": "Sobrecosto",
    "none": "Sin costo",
}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    """Un costo a una fecha: un movimiento aprobado o un costo automático del día."""

    day: date
    amount: Decimal
    scope: str
    #: Ítems del movimiento (`item`, `selection`) o del uso de equipo.
    items: tuple[UUID, ...] = ()
    #: Capítulo asignado (`chapter`, `selection`) o de origen (`item` del libro).
    chapter_id: UUID | None = None
    task_id: UUID | None = None
    origin: str = LEDGER


@dataclass(frozen=True)
class ChapterRef:
    """Un capítulo de la versión vigente."""

    label: str
    #: Capítulo de primer nivel que lo contiene (él mismo si es de primer nivel).
    top: str
    #: 0 en el primer nivel.
    depth: int
    #: Ítems con precio vigente que contiene, a cualquier profundidad.
    items: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class AdminLine:
    budget_id: UUID
    budget: str
    #: Monto de la línea Administración ÷ costo directo de la versión vigente; `None` sin línea.
    ratio: Decimal | None


@dataclass(frozen=True)
class CostInput:
    lines: Sequence[CostLine] = ()
    chapters: Mapping[UUID, ChapterRef] = field(default_factory=dict)
    admin: Sequence[AdminLine] = ()
    #: Borradores con fecha hasta el corte: no cuentan, se avisan.
    pending_count: int = 0
    pending_amount: Decimal = ZERO
    #: Usos de equipo y viajes sin valor (sin tarifa): no suman, se avisan.
    unpriced_usages: int = 0
    unpriced_trips: int = 0


class PricedLike(Protocol):
    """Lo que se usa de `earned_value.PricedItem`."""

    @property
    def label(self) -> str: ...

    @property
    def chapter(self) -> str: ...

    @property
    def budget_id(self) -> UUID | None: ...


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostMeasure:
    """AC y EV de una fila del desglose. En un ítem de grupo, el índice es el del grupo."""

    ac: Decimal
    ev: Decimal
    group: str = ""
    group_ev: Decimal = ZERO
    group_ac: Decimal = ZERO

    @property
    def cv(self) -> Decimal:
        return self.group_ev - self.group_ac if self.group else self.ev - self.ac

    @property
    def cpi(self) -> Decimal | None:
        return ratio_of(self.group_ev, self.group_ac) if self.group else ratio_of(self.ev, self.ac)

    @property
    def status(self) -> str:
        return status_of(self.cpi)

    @property
    def status_label(self) -> str:
        return COST_LABELS[self.status]


@dataclass(frozen=True)
class ItemCost:
    """Lo que el cálculo sabe de un ítem; el EV lo pone el desglose."""

    ac: Decimal
    group: str = ""
    group_ev: Decimal = ZERO
    group_ac: Decimal = ZERO


@dataclass(frozen=True)
class CostIndicators:
    bac: Decimal
    ev: Decimal
    ac_assigned: Decimal
    ac_unassigned: Decimal
    ac_indirect: Decimal
    #: `None` si ningún presupuesto tiene línea de Administración.
    admin_earned: Decimal | None

    @property
    def ac_direct(self) -> Decimal:
        return self.ac_assigned + self.ac_unassigned

    @property
    def empty(self) -> bool:
        """Sin costos al corte (ni directos ni indirectos)."""
        return self.ac_direct == 0 and self.ac_indirect == 0

    @property
    def ac_share(self) -> Decimal | None:
        return share_of(self.ac_direct, self.bac)

    @property
    def cv(self) -> Decimal:
        return self.ev - self.ac_direct

    @property
    def cpi(self) -> Decimal | None:
        return ratio_of(self.ev, self.ac_direct)

    @property
    def status(self) -> str:
        return status_of(self.cpi)

    @property
    def status_label(self) -> str:
        return COST_LABELS[self.status]

    @property
    def eac(self) -> Decimal | None:
        """`AC + (BAC − EV) / CPI`, con el cociente sin redondear. Vacío sin CPI o sin EV."""
        if self.cpi is None or self.ev <= 0:
            return None
        with localcontext() as context:
            context.prec = WIDE_PRECISION
            return money(self.ac_direct + (self.bac - self.ev) * self.ac_direct / self.ev)

    @property
    def vac(self) -> Decimal | None:
        eac = self.eac
        return None if eac is None else self.bac - eac

    @property
    def over_budget(self) -> bool:
        eac = self.eac
        return eac is not None and eac > self.bac

    @property
    def tcpi(self) -> Decimal | None:
        """Vacío si el presupuesto ya se agotó (`BAC − AC ≤ 0`)."""
        return ratio_of(self.bac - self.ev, self.bac - self.ac_direct)

    @property
    def exhausted(self) -> bool:
        return self.ac_direct > 0 and self.bac - self.ac_direct <= 0

    @property
    def admin_cpi(self) -> Decimal | None:
        return None if self.admin_earned is None else ratio_of(self.admin_earned, self.ac_indirect)

    @property
    def admin_status(self) -> str:
        return status_of(self.admin_cpi)

    @property
    def total_cpi(self) -> Decimal | None:
        return ratio_of(
            self.ev + (self.admin_earned or ZERO), self.ac_direct + self.ac_indirect
        )

    @property
    def total_status(self) -> str:
        return status_of(self.total_cpi)


@dataclass(frozen=True)
class UnassignedCost:
    origin: str
    amount: Decimal


@dataclass(frozen=True)
class RetiredCost:
    item: str
    chapter: str
    amount: Decimal


@dataclass(frozen=True)
class CostWarnings:
    unassigned: tuple[UnassignedCost, ...] = ()
    #: Presupuestos sin línea de Administración.
    no_admin: tuple[str, ...] = ()
    retired: tuple[RetiredCost, ...] = ()
    pending_count: int = 0
    pending_amount: Decimal = ZERO
    exhausted: bool = False
    #: Costo directo sin tarea (incluye los automáticos): se avisa en el desglose por tarea.
    no_task: Decimal = ZERO
    unpriced_usages: int = 0
    unpriced_trips: int = 0

    @property
    def unassigned_total(self) -> Decimal:
        return sum((u.amount for u in self.unassigned), ZERO)

    @property
    def count(self) -> int:
        return (
            len(self.unassigned)
            + len(self.no_admin)
            + len(self.retired)
            + (1 if self.pending_count else 0)
            + (1 if self.exhausted else 0)
            + (1 if self.unpriced_usages or self.unpriced_trips else 0)
        )


@dataclass(frozen=True)
class CostResult:
    indicators: CostIndicators
    #: AC directo de cada periodo (sin acumular), alineado con los periodos del control.
    per_period: tuple[Decimal, ...]
    #: AC por capítulo de primer nivel (la misma etiqueta que `PricedItem.chapter`).
    by_chapter: Mapping[str, Decimal]
    by_task: Mapping[UUID, Decimal]
    by_item: Mapping[UUID, ItemCost]
    warnings: CostWarnings


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


def measure_costs(
    data: CostInput,
    *,
    cut: date,
    starts: Sequence[date],
    items: Mapping[UUID, PricedLike],
    item_labels: Mapping[UUID, str],
    ev_by_item: Mapping[UUID, Decimal],
    bac: Decimal,
    ev: Decimal,
) -> CostResult:
    """`starts`: inicio de cada periodo del control (ya incluye el primer costo contado)."""
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return _measure(data, cut, starts, items, item_labels, ev_by_item, bac, ev)


def _top(chapters: Mapping[UUID, ChapterRef], chapter_id: UUID | None) -> str:
    ref = chapters.get(chapter_id) if chapter_id is not None else None
    return ref.top if ref is not None else NO_CHAPTER


def _measure(
    data: CostInput,
    cut: date,
    starts: Sequence[date],
    items: Mapping[UUID, PricedLike],
    item_labels: Mapping[UUID, str],
    ev_by_item: Mapping[UUID, Decimal],
    bac: Decimal,
    ev: Decimal,
) -> CostResult:
    per_period = [ZERO] * len(starts)
    assigned = unassigned = indirect = no_task = ZERO
    by_origin: dict[str, Decimal] = defaultdict(Decimal)
    by_chapter: dict[str, Decimal] = defaultdict(Decimal)
    by_task: dict[UUID, Decimal] = defaultdict(Decimal)
    own: dict[UUID, Decimal] = defaultdict(Decimal)
    retired: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    grouped: list[tuple[tuple[UUID, ...], CostLine, ChapterRef]] = []
    for line in data.lines:
        if line.day > cut:
            continue
        amount = line.amount
        if line.scope == INDIRECT:
            indirect += amount
            continue
        index = bisect_right(starts, line.day) - 1
        if index >= 0:
            per_period[index] += amount
        if line.task_id is None:
            no_task += amount
        else:
            by_task[line.task_id] += amount
        if line.scope == UNASSIGNED:
            unassigned += amount
            by_origin[line.origin] += amount
            continue
        assigned += amount
        if line.scope == ITEM:
            item_id = line.items[0] if line.items else None
            priced = items.get(item_id) if item_id is not None else None
            if item_id is not None and priced is not None:
                own[item_id] += amount
                by_chapter[priced.chapter] += amount
            else:
                chapter = _top(data.chapters, line.chapter_id)
                by_chapter[chapter] += amount
                label = item_labels.get(item_id, UNKNOWN) if item_id is not None else UNKNOWN
                retired[(label, chapter)] += amount
            continue
        ref = data.chapters.get(line.chapter_id) if line.chapter_id is not None else None
        if ref is None:
            label = (
                item_labels.get(line.chapter_id, UNKNOWN)
                if line.chapter_id is not None
                else UNKNOWN
            )
            by_chapter[NO_CHAPTER] += amount
            retired[(label, NO_CHAPTER)] += amount
            continue
        by_chapter[ref.top] += amount
        members = (
            tuple(sorted(ref.items))
            if line.scope == CHAPTER
            else tuple(i for i in line.items if i in items)
        )
        if members:
            grouped.append((members, line, ref))
    admin_earned, no_admin = _admin(data.admin, items, ev_by_item)
    indicators = CostIndicators(
        bac=bac,
        ev=ev,
        ac_assigned=money(assigned),
        ac_unassigned=money(unassigned),
        ac_indirect=money(indirect),
        admin_earned=admin_earned,
    )
    warnings = CostWarnings(
        unassigned=tuple(
            UnassignedCost(ORIGIN_LABELS.get(origin, origin), money(amount))
            for origin, amount in sorted(by_origin.items())
            if amount
        ),
        no_admin=no_admin,
        retired=tuple(
            RetiredCost(item, chapter, money(amount))
            for (item, chapter), amount in sorted(retired.items())
        ),
        pending_count=data.pending_count,
        pending_amount=money(data.pending_amount),
        exhausted=indicators.exhausted,
        no_task=money(no_task),
        unpriced_usages=data.unpriced_usages,
        unpriced_trips=data.unpriced_trips,
    )
    return CostResult(
        indicators=indicators,
        per_period=tuple(per_period),
        by_chapter=dict(by_chapter),
        by_task=dict(by_task),
        by_item=_groups(grouped, own, ev_by_item),
        warnings=warnings,
    )


def _groups(
    grouped: Sequence[tuple[tuple[UUID, ...], CostLine, ChapterRef]],
    own: Mapping[UUID, Decimal],
    ev_by_item: Mapping[UUID, Decimal],
) -> dict[UUID, ItemCost]:
    """Unión de conjuntos sobre los ítems de los movimientos de capítulo y de selección."""
    parent: dict[UUID, UUID] = {}

    def find(node: UUID) -> UUID:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            following = parent[node]
            parent[node] = root
            node = following
        return root

    for members, _line, _ref in grouped:
        for member in members:
            parent.setdefault(member, member)
        first = find(members[0])
        for member in members[1:]:
            root = find(member)
            if root != first:
                parent[root] = first
    group_ac: dict[UUID, Decimal] = defaultdict(Decimal)
    names: dict[UUID, tuple[int, str]] = {}
    for members, line, ref in grouped:
        root = find(members[0])
        group_ac[root] += line.amount
        candidate = (ref.depth, ref.label)
        if root not in names or candidate < names[root]:
            names[root] = candidate
    components: dict[UUID, list[UUID]] = defaultdict(list)
    for member in parent:
        components[find(member)].append(member)
    result: dict[UUID, ItemCost] = {}
    for root, members in components.items():
        total_ev = sum((ev_by_item.get(m, ZERO) for m in members), ZERO)
        total_ac = group_ac[root] + sum((own.get(m, ZERO) for m in members), ZERO)
        for member in members:
            result[member] = ItemCost(
                ac=money(own.get(member, ZERO)),
                group=names[root][1],
                group_ev=money(total_ev),
                group_ac=money(total_ac),
            )
    for item_id, amount in own.items():
        result.setdefault(item_id, ItemCost(ac=money(amount)))
    return result


def _admin(
    lines: Sequence[AdminLine],
    items: Mapping[UUID, PricedLike],
    ev_by_item: Mapping[UUID, Decimal],
) -> tuple[Decimal | None, tuple[str, ...]]:
    by_budget: dict[UUID, Decimal] = defaultdict(Decimal)
    for item_id, value in ev_by_item.items():
        priced = items.get(item_id)
        if priced is not None and priced.budget_id is not None:
            by_budget[priced.budget_id] += value
    with_line = [line for line in lines if line.ratio is not None]
    earned = (
        money(
            sum(
                (by_budget.get(line.budget_id, ZERO) * (line.ratio or ZERO) for line in with_line),
                ZERO,
            )
        )
        if with_line
        else None
    )
    return earned, tuple(sorted(line.budget for line in lines if line.ratio is None))
```

- [ ] **Step 5: Enganchar el costo en el valor ganado**

En `server/suno/control/earned_value.py`:

1. Imports y constantes. Reemplazar el bloque de imports y las constantes `SPI_STEP`, `SHARE_STEP`, `GREEN_FROM`, `AMBER_FROM` por:

```python
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID

from suno.budgets.totals import WIDE_PRECISION, money
from suno.projects.workdays import WorkCalendar

from .actual_cost import (
    INDIRECT,
    CostIndicators,
    CostInput,
    CostMeasure,
    CostWarnings,
    ItemCost,
    measure_costs,
)
from .indices import ratio_of, share_of, status_of
from .periods import Period, build_periods

ZERO = Decimal(0)
ONE = Decimal(1)
```

(Se conservan `STATUS_LABELS`, `NO_CHAPTER`, `NO_LOCATION`, `UNKNOWN_ITEM` y `Pair`.)

2. Reemplazar las funciones `spi_of`, `status_of` y `share_of` por (`status_of` y `share_of` quedan importadas de `indices`, con el mismo nombre para las pruebas de la pieza 5):

```python
def spi_of(ev: Decimal, pv: Decimal) -> Decimal | None:
    """EV / PV a dos decimales; `None` sin valor planeado."""
    return ratio_of(ev, pv)
```

3. En `PricedItem`, al final:

```python
    #: Presupuesto del ítem: la administración ganada se calcula por presupuesto.
    budget_id: UUID | None = None
```

4. En `ControlInput`, al final:

```python
    #: Costo real (pieza 6a); `None` si quien mira no lee costos.
    costs: CostInput | None = None
```

5. En `PeriodRow`, después de `ev_cum`:

```python
    #: AC directo del periodo y acumulado; `None` tras el corte o sin permiso de costos.
    ac: Decimal | None = None
    ac_cum: Decimal | None = None
```

6. `BreakdownRow` queda:

```python
@dataclass(frozen=True)
class BreakdownRow:
    label: str
    bac: Decimal
    measure: Measure
    #: Solo en el desglose por tarea (enlace a su ficha).
    task_id: UUID | None = None
    #: AC, CV y CPI; `None` sin permiso de costos (y siempre en el desglose por ubicación).
    cost: CostMeasure | None = None
    #: Solo en el desglose por ítem.
    item_id: UUID | None = None
```

7. En `ControlResult`, al final:

```python
    by_item: tuple[BreakdownRow, ...] = ()
    cost: CostIndicators | None = None
    cost_warnings: CostWarnings | None = None
```

8. `_Totals.row` queda:

```python
    def row(
        self, label: str, task_id: UUID | None = None, item_id: UUID | None = None
    ) -> BreakdownRow:
        return BreakdownRow(
            label,
            money(self.bac),
            Measure(money(self.pv), money(self.ev)),
            task_id,
            item_id=item_id,
        )
```

9. Después de `_worst_first`:

```python
def worst_cost_first(rows: Iterable[BreakdownRow]) -> tuple[BreakdownRow, ...]:
    """Por CPI (el del grupo en un ítem de grupo), peor primero; sin CPI al final."""

    def key(row: BreakdownRow) -> tuple[bool, Decimal, str]:
        cpi = row.cost.cpi if row.cost is not None else None
        return (cpi is None, ZERO if cpi is None else cpi, row.label.lower())

    return tuple(sorted(rows, key=key))
```

10. En `_compute`, cambiar el cálculo de `first`:

```python
    counted_days = [e.day for key in pairs for e in executions.get(key, ())]
    cost_days = (
        [
            line.day
            for line in data.costs.lines
            if line.day <= data.cut and line.scope != INDIRECT
        ]
        if data.costs is not None
        else []
    )
    first = min([s.start for s in spreads.values()] + counted_days + cost_days)
```

(Un indirecto no extiende la serie: no entra en el AC de los periodos.)

11. En `_compute`, reemplazar desde `rows: list[PeriodRow] = []` hasta el final de la función por:

```python
    # A la fecha de corte: total y desglose.
    shares = {task_id: spread.share(data.cut) for task_id, spread in spreads.items()}
    total = _Totals()
    chapters: dict[str, _Totals] = defaultdict(_Totals)
    tasks: dict[UUID, _Totals] = defaultdict(_Totals)
    locations: dict[str, _Totals] = defaultdict(_Totals)
    by_item: dict[UUID, _Totals] = defaultdict(_Totals)
    for key, pair in pairs.items():
        value = pair.value
        pv = value * shares[pair.task.task_id]
        ev = earned_quantity[key] * pair.item.unit_price
        for bucket in (
            total,
            chapters[pair.item.chapter],
            tasks[pair.task.task_id],
            locations[pair.task.location or NO_LOCATION],
            by_item[pair.item.item_id],
        ):
            bucket.add(value, pv, ev)

    cost = (
        measure_costs(
            data.costs,
            cut=data.cut,
            starts=starts,
            items=data.items,
            item_labels=data.item_labels,
            ev_by_item={item_id: t.ev for item_id, t in by_item.items()},
            bac=money(total.bac),
            ev=money(total.ev),
        )
        if data.costs is not None
        else None
    )

    rows: list[PeriodRow] = []
    pv_cum = ev_cum = ac_cum = ZERO
    for i, period in enumerate(periods):
        pv_cum += planned[i]
        if not period.measured:
            rows.append(PeriodRow(period, money(planned[i]), money(pv_cum), None, None))
            continue
        ev_cum += earned[i]
        ac: Decimal | None = None
        ac_total: Decimal | None = None
        if cost is not None:
            ac_cum += cost.per_period[i]
            ac, ac_total = money(cost.per_period[i]), money(ac_cum)
        rows.append(
            PeriodRow(
                period,
                money(planned[i]),
                money(pv_cum),
                money(earned[i]),
                money(ev_cum),
                ac,
                ac_total,
            )
        )

    def costed(
        row: BreakdownRow, ac: Decimal | None, group: ItemCost | None = None
    ) -> BreakdownRow:
        if cost is None:
            return row
        if group is not None and group.group:
            measure = CostMeasure(
                group.ac, row.measure.ev, group.group, group.group_ev, group.group_ac
            )
        else:
            own = group.ac if group is not None else money(ac or ZERO)
            measure = CostMeasure(own, row.measure.ev)
        return replace(row, cost=measure)

    chapter_labels = set(chapters) | (set(cost.by_chapter) if cost else set())
    task_ids = set(tasks) | ({t for t in cost.by_task if t in data.tasks} if cost else set())
    item_ids = set(by_item) | ({i for i in cost.by_item if i in data.items} if cost else set())
    return ControlResult(
        cut=data.cut,
        kind=data.kind,
        has_budget=data.has_budget,
        has_plan=True,
        indicators=Indicators(
            money(total.bac),
            Measure(money(total.pv), money(total.ev)),
            data.contractual_direct_cost,
            data.contractual_budgets,
            data.applied_budgets,
        ),
        periods=tuple(rows),
        by_chapter=_worst_first(
            costed(chapters[label].row(label), cost.by_chapter.get(label) if cost else None)
            for label in chapter_labels
        ),
        by_task=_worst_first(
            costed(
                tasks[task_id].row(data.tasks[task_id].label, task_id),
                cost.by_task.get(task_id) if cost else None,
            )
            for task_id in task_ids
        ),
        by_location=_worst_first(t.row(label) for label, t in locations.items()),
        by_item=_worst_first(
            costed(
                by_item[item_id].row(data.items[item_id].label, item_id=item_id),
                None,
                cost.by_item.get(item_id) if cost else None,
            )
            for item_id in item_ids
        ),
        warnings=Warnings(
            no_baseline=base_warnings.no_baseline,
            undated=base_warnings.undated,
            unpriced=base_warnings.unpriced,
            overrun=tuple(overruns),
            outside_plan=base_warnings.outside_plan,
            percent_tasks=base_warnings.percent_tasks,
        ),
        cost=cost.indicators if cost else None,
        cost_warnings=cost.warnings if cost else None,
    )
```

Notas para quien implementa:
- `test_after_the_cut_does_not_count_and_before_the_start_extends`: un indirecto de antes del inicio no mueve la serie (paso 10); uno directo sí (la semana del 16 de febrero).
- Un capítulo con solo costo (sin pares) aparece en el desglose con BAC, PV y EV en 0: `chapters[label]` crea el `_Totals` vacío.
- Si `ruff` marca `HUNDRED` o `ROUND_HALF_UP` como no usados en `earned_value.py`, quitarlos del import (ya viven en `indices.py`).

- [ ] **Step 6: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control
```

Expected: PASS, con las pruebas de la pieza 5 sin tocar (`spi_of`, `status_of`, `PeriodRow` de cinco campos, `PricedItem` sin presupuesto).

- [ ] **Step 7: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): actual cost, cpi, eac and tcpi in the earned value calculation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Cargador del costo real

**Files:**
- Modify: `server/suno/control/loader.py` (docstring, `load_control_input(..., costs=False)`, `PricedItem.budget_id`, `_load_costs`, `_chapters`, `_admin_lines`)
- Test: `server/suno/control/tests/test_loader.py` (clase nueva al final)

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `server/suno/control/tests/test_loader.py` (y en sus imports: `from django.db import connection`, `from django.test.utils import CaptureQueriesContext`, `from suno.control.actual_cost import AdminLine, ChapterRef`, `from suno.control.earned_value import compute`, `from suno.core.models import CostScope, CostStatus, EquipmentOwnership, EquipmentUsageStatus` y `from suno.costs.tests.factories import cost_world, entry, equipment, trip, usage`):

```python
@skip_without_db
class CostLoaderTests(TestCase):
    """Costos, pieza 6a: cinco consultas más, solo con `costs=True`."""

    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.ctx = self.world.ctx()
        self.concrete = item(self.budget, "2.1")
        self.steel = item(self.budget, "2.2")
        self.structure = item(self.budget, "2")
        plan(self.world, 5, self.concrete, "8")

    def load(self, *, costs: bool = True) -> ControlInput:
        return load_control_input(
            self.ctx, self.world.project, cut=CUT, kind=MONTH, costs=costs
        )

    def test_query_count_does_not_grow_with_the_data(self) -> None:
        with self.assertNumQueries(14):
            self.load()
        machine = equipment(self.world)
        for day in range(2, 12):
            entry(self.world, day=date(2026, 3, day), status=CostStatus.APPROVED)
            usage(self.world, machine, date(2026, 3, day), "100.00", budget_item=self.concrete)
            trip(self.world, date(2026, 3, day), "50.00")
        entry(
            self.world,
            status=CostStatus.APPROVED,
            scope=CostScope.SELECTION,
            chapter=self.structure,
            items=(self.concrete, self.steel),
        )
        with CaptureQueriesContext(connection) as queries:
            self.load()
        self.assertEqual(len(queries.captured_queries), 14)
        # El combustible y el mantenimiento ya están en la tarifa horaria: no se leen.
        tables = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertNotIn('"fuel_', tables)
        self.assertNotIn('"work_order', tables)
        with self.assertNumQueries(9):
            self.assertIsNone(self.load(costs=False).costs)

    def test_ledger_lines_drafts_and_automatic_costs(self) -> None:
        task = self.world.task(5)
        entry(
            self.world,
            amount="1000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=task,
        )
        entry(
            self.world,
            day=date(2026, 3, 11),
            amount="500.00",
            status=CostStatus.APPROVED,
            scope=CostScope.SELECTION,
            chapter=self.structure,
            items=(self.concrete, self.steel),
        )
        entry(self.world, day=date(2026, 3, 12), amount="700.00")  # borrador
        entry(self.world, day=date(2026, 3, 12), status=CostStatus.REJECTED)
        entry(self.world, day=date(2026, 4, 2), status=CostStatus.APPROVED)  # tras el corte
        machine = equipment(self.world)
        usage(self.world, machine, date(2026, 3, 10), "800.00", budget_item=self.concrete)
        usage(self.world, machine, date(2026, 3, 10), "200.00")
        usage(self.world, machine, date(2026, 3, 11), None)
        usage(
            self.world, machine, date(2026, 3, 11), "50.00", status=EquipmentUsageStatus.SCHEDULED
        )
        rented = equipment(self.world, "AL-01", ownership=EquipmentOwnership.RENTED)
        usage(self.world, rented, date(2026, 3, 10), "999.00")
        trip(self.world, date(2026, 3, 8), "1400.00")
        trip(self.world, date(2026, 3, 8), None)
        trip(self.world, date(2026, 3, 8), "999.00", own=machine)
        costs = self.load().costs
        assert costs is not None
        ledger = sorted(
            (line.day, line.amount, line.scope, line.items, line.chapter_id, line.task_id)
            for line in costs.lines
            if line.origin == "ledger"
        )
        self.assertEqual(
            ledger,
            [
                (
                    date(2026, 3, 10),
                    D("1000.00"),
                    "item",
                    (self.concrete.pk,),
                    self.structure.pk,
                    task.pk,
                ),
                (
                    date(2026, 3, 11),
                    D("500.00"),
                    "selection",
                    tuple(sorted((self.concrete.pk, self.steel.pk))),
                    self.structure.pk,
                    None,
                ),
            ],
        )
        automatic = sorted(
            (line.origin, line.day, line.amount, line.scope, line.items)
            for line in costs.lines
            if line.origin != "ledger"
        )
        self.assertEqual(
            automatic,
            [
                ("equipment", date(2026, 3, 10), D("200.00"), "unassigned", ()),
                ("equipment", date(2026, 3, 10), D("800.00"), "item", (self.concrete.pk,)),
                ("transport", date(2026, 3, 8), D("1400.00"), "unassigned", ()),
            ],
        )
        self.assertEqual((costs.pending_count, costs.pending_amount), (1, D("700.00")))
        self.assertEqual((costs.unpriced_usages, costs.unpriced_trips), (1, 1))

    def test_chapters_admin_and_budget_of_each_item(self) -> None:
        data = self.load()
        costs = data.costs
        assert costs is not None
        self.assertEqual(
            costs.chapters[self.structure.pk],
            ChapterRef(
                "2 Estructura",
                "2 Estructura",
                0,
                frozenset({self.concrete.pk, self.steel.pk, item(self.budget, "2.4").pk}),
            ),
        )
        # Administración 10 % del costo directo: 913.000 / 9.130.000.
        self.assertEqual(
            list(costs.admin), [AdminLine(self.budget.pk, "Contrato de obra", D("0.1"))]
        )
        self.assertEqual(data.items[self.concrete.pk].budget_id, self.budget.pk)
        self.assertIsNotNone(compute(data).cost)

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto en costos")
        with self.assertNumQueries(12):
            data = load_control_input(world.ctx(), world.project, cut=CUT, kind=MONTH, costs=True)
        assert data.costs is not None
        self.assertEqual((list(data.costs.admin), dict(data.costs.chapters)), ([], {}))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_loader.py`
Expected: FAIL (`load_control_input() got an unexpected keyword argument 'costs'`).

- [ ] **Step 3: Implementar**

En `server/suno/control/loader.py`:

1. El docstring del módulo pasa a:

```python
"""Lo que el control necesita de la BD, en un número fijo de queries (costos, piezas 5 y 6a).

Pieza 5: nueve queries sin importar el tamaño del proyecto (ocho si no hay presupuesto
aplicado): árbol de ubicaciones, tareas vigentes, presupuestos (costo directo contractual),
versiones vigentes, sus filas, códigos de los ítems, plan de la matriz, cantidades validadas
hasta el corte y el último % validado por tarea hasta el corte.

Pieza 6a, solo con `costs=True` (quien lee costos): cinco más (cuatro sin presupuesto
aplicado): cargos de las versiones vigentes (línea de Administración), movimientos aprobados
y borradores hasta el corte, ítems de los aprobados, usos de equipos propios agregados por día
e ítem, y viajes de terceros agregados por día. Las agregaciones llevan `.order_by()` para que
el `ordering` del modelo no entre en el `GROUP BY`.
"""
```

2. Imports nuevos:

```python
from collections import defaultdict

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate

from suno.budgets.mapping import fold
from suno.core.models import (
    BudgetCharge,
    BudgetVersion,
    CostEntry,
    CostEntryItem,
    CostStatus,
    EquipmentOwnership,
    EquipmentUsage,
    EquipmentUsageStatus,
    Trip,
)

from .actual_cost import (
    EQUIPMENT,
    ITEM,
    LEDGER,
    TRANSPORT,
    UNASSIGNED,
    AdminLine,
    ChapterRef,
    CostInput,
    CostLine,
)
```

(los modelos, sumados al import existente de `suno.core.models`).

3. Constantes, después de los imports:

```python
COUNTED_USAGE = (EquipmentUsageStatus.COMPLETED, EquipmentUsageStatus.IN_PROGRESS)
ADMINISTRATION = "administracion"
```

4. La firma pasa a `def load_control_input(ctx, project, *, cut: date, kind: str, costs: bool = False) -> ControlInput:`; guardar los nombres de los presupuestos antes de `_priced_items`:

```python
    budget_names = {pk: name for pk, name, _ in budgets}
    items = _priced_items(version_rows, budget_names, item_labels)
```

y en el `return ControlInput(...)`, al final:

```python
        costs=_load_costs(ctx, project, cut, versions, version_rows, item_labels, budget_names)
        if costs
        else None,
```

5. En `_priced_items`, el `PricedItem(...)` lleva además `budget_id=budget_id`.

6. Al final del archivo:

```python
def _load_costs(
    ctx: RequestContext,
    project: Project,
    cut: date,
    versions: Sequence[BudgetVersion],
    rows: Sequence[tuple[Any, ...]],
    labels: dict[UUID, str],
    budget_names: dict[UUID, str],
) -> CostInput:
    """Movimientos aprobados, borradores y costos automáticos hasta el corte."""
    lines: list[CostLine] = []
    pending_count = 0
    pending_amount = Decimal(0)
    approved: dict[UUID, tuple[date, Decimal, str, UUID | None, UUID | None]] = {}
    for pk, status, day, amount, scope, chapter_id, task_id in (
        scoped_qs(ctx, CostEntry)
        .filter(
            project=project,
            incurred_on__lte=cut,
            status__in=[CostStatus.APPROVED, CostStatus.DRAFT],
        )
        .values_list("pk", "status", "incurred_on", "amount", "scope", "chapter_id", "task_id")
    ):
        if status == CostStatus.DRAFT:
            pending_count += 1
            pending_amount += amount
        else:
            approved[pk] = (day, amount, scope, chapter_id, task_id)
    links: dict[UUID, list[UUID]] = defaultdict(list)
    for entry_id, item_id in (
        scoped_qs(ctx, CostEntryItem)
        .filter(
            entry__project=project,
            entry__status=CostStatus.APPROVED,
            entry__incurred_on__lte=cut,
        )
        .values_list("entry_id", "item_id")
    ):
        links[entry_id].append(item_id)
    for pk, (day, amount, scope, chapter_id, task_id) in approved.items():
        items = tuple(sorted(links.get(pk, ())))
        lines.append(CostLine(day, amount, scope, items, chapter_id, task_id, LEDGER))

    unpriced_usages = 0
    for day, item_id, total, unpriced in (
        scoped_qs(ctx, EquipmentUsage)
        .filter(
            project=project,
            usage_date__lte=cut,
            status__in=COUNTED_USAGE,
            equipment__ownership=EquipmentOwnership.OWN,
        )
        .order_by()
        .values("usage_date", "budget_item_id")
        .annotate(total=Sum("cost"), unpriced=Count("id", filter=Q(cost__isnull=True)))
        .values_list("usage_date", "budget_item_id", "total", "unpriced")
    ):
        unpriced_usages += unpriced
        if total:
            if item_id is not None:
                lines.append(CostLine(day, total, ITEM, (item_id,), origin=EQUIPMENT))
            else:
                lines.append(CostLine(day, total, UNASSIGNED, origin=EQUIPMENT))

    unpriced_trips = 0
    for day, total, unpriced in (
        scoped_qs(ctx, Trip)
        .filter(project=project, vehicle__isnull=False, recorded_at__date__lte=cut)
        .annotate(day=TruncDate("recorded_at"))
        .order_by()
        .values("day")
        .annotate(total=Sum("amount"), unpriced=Count("id", filter=Q(amount__isnull=True)))
        .values_list("day", "total", "unpriced")
    ):
        unpriced_trips += unpriced
        if total:
            lines.append(CostLine(day, total, UNASSIGNED, origin=TRANSPORT))

    return CostInput(
        lines=lines,
        chapters=_chapters(rows, labels),
        admin=_admin_lines(ctx, versions, budget_names) if versions else [],
        pending_count=pending_count,
        pending_amount=pending_amount,
        unpriced_usages=unpriced_usages,
        unpriced_trips=unpriced_trips,
    )


def _chapters(rows: Sequence[tuple[Any, ...]], labels: dict[UUID, str]) -> dict[UUID, ChapterRef]:
    """Cada capítulo de las versiones vigentes: su capítulo de primer nivel, su profundidad y
    sus ítems con precio a cualquier profundidad. `labels` ya trae las etiquetas de
    `_priced_items` (con el presupuesto si hay varios)."""
    parents: dict[UUID, UUID | None] = {}
    children: dict[UUID, list[UUID]] = defaultdict(list)
    chapters: list[UUID] = []
    priced: set[UUID] = set()
    for item_id, _code, kind, _description, _unit, price, parent_id, _budget in rows:
        parents[item_id] = parent_id
        if parent_id is not None:
            children[parent_id].append(item_id)
        if kind == BudgetRowKind.CHAPTER:
            chapters.append(item_id)
        elif price is not None:
            priced.add(item_id)
    refs: dict[UUID, ChapterRef] = {}
    for chapter_id in chapters:
        top, depth, seen = chapter_id, 0, {chapter_id}
        while (parent := parents.get(top)) is not None and parent not in seen:
            seen.add(parent)
            top = parent
            depth += 1
        inside: set[UUID] = set()
        pending = list(children.get(chapter_id, ()))
        while pending:
            node = pending.pop()
            if node not in inside:
                inside.add(node)
                pending.extend(children.get(node, ()))
        refs[chapter_id] = ChapterRef(
            label=labels.get(chapter_id, NO_CHAPTER),
            top=labels.get(top, NO_CHAPTER),
            depth=depth,
            items=frozenset(inside & priced),
        )
    return refs


def _admin_lines(
    ctx: RequestContext, versions: Sequence[BudgetVersion], budget_names: dict[UUID, str]
) -> list[AdminLine]:
    """La primera línea de cada versión vigente cuyo nombre (sin mayúsculas ni tildes) empieza
    por "administracion", como razón de su monto sobre el costo directo."""
    amounts: dict[UUID, Decimal] = {}
    for version_id, name, amount in (
        scoped_qs(ctx, BudgetCharge)
        .filter(version__in=versions)
        .order_by("order")
        .values_list("version_id", "name", "amount")
    ):
        if (
            version_id not in amounts
            and amount is not None
            and fold(name).strip().startswith(ADMINISTRATION)
        ):
            amounts[version_id] = amount
    lines: list[AdminLine] = []
    for version in versions:
        amount = amounts.get(version.pk)
        direct = version.direct_cost
        ratio = amount / direct if amount is not None and direct else None
        lines.append(AdminLine(version.budget_id, budget_names.get(version.budget_id, ""), ratio))
    return lines
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control
```

Expected: PASS. Si `assertNumQueries(14)` cuenta otra cosa, imprimir las consultas con `CaptureQueriesContext` y revisar que ninguna se repita por fila (por ejemplo, un `ordering` que entró al `GROUP BY` y partió los grupos).

- [ ] **Step 5: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): load approved costs, drafts and automatic costs in fixed queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Costo en la página "Control" y en su Excel

**Files:**
- Modify: `server/suno/control/views.py` (pestañas, `orden`, permiso de costos)
- Modify: `server/suno/control/chart.py` (serie AC)
- Modify: `server/suno/control/export.py` (columnas de AC, filas de ítem, hoja "Indicadores")
- Modify: `server/suno/core/templates/suno/control/dashboard.html` (completo)
- Create: `server/suno/core/templates/suno/control/_cost_metrics.html`, `_cost_warnings.html`
- Modify: `server/suno/core/static/suno/css/projects.css`, `server/suno/core/templates/suno/projects/base.html` (`?v=`)
- Test: `server/suno/control/tests/test_views.py`, `test_chart.py`, `test_export.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/control/tests/test_chart.py`, agregar `from dataclasses import replace` a los imports y al final de `SCurveTests`:

```python
    def test_actual_cost_series(self) -> None:
        first = replace(row(0, "Mar-2026", "50", "40"), ac=D("120"), ac_cum=D("120"))
        curve = build_s_curve([first, row(1, "Abr-2026", "100", None)], D(100))
        assert curve is not None
        self.assertGreaterEqual(curve.axis.maximum, D(120))
        self.assertTrue(curve.ac_points.startswith("0,100 50.00,"))
        self.assertEqual(curve.ac_last, "$120")
        plain = build_s_curve([row(0, "Mar-2026", "50", "40")], D(50))
        assert plain is not None
        self.assertEqual((plain.ac_points, plain.ac_last), ("", ""))
```

En `server/suno/control/tests/test_export.py`, al final de `test_two_sheets_with_safe_cells`:

```python
        self.assertNotIn(">ac_acumulado<", periods)  # sin permiso de costos, sin columnas de AC
```

y una prueba nueva al final de `ControlWorkbookTests`:

```python
    def test_cost_columns_and_indicators(self) -> None:
        from suno.control.tests.test_actual_cost import BASIC, control

        with zipfile.ZipFile(io.BytesIO(control_workbook(control(BASIC)))) as book:
            workbook = book.read("xl/workbook.xml").decode()
            periods = book.read("xl/worksheets/sheet1.xml").decode()
            breakdown = book.read("xl/worksheets/sheet2.xml").decode()
            indicators = book.read("xl/worksheets/sheet3.xml").decode()
        self.assertIn('<sheet name="Indicadores"', workbook)
        self.assertIn(">ac_acumulado<", periods)
        self.assertIn("<v>55000.0</v>", periods)
        self.assertIn(">indice_del_grupo<", breakdown)
        self.assertIn(">ítem<", breakdown)
        self.assertIn(">CPI<", indicators)
        self.assertIn("<v>17.27</v>", indicators)
```

En `server/suno/control/tests/test_views.py`, agregar a los imports `from suno.core.models import CostEntry, CostScope, CostStatus` y `from suno.costs.tests.factories import cost_world, entry as cost_entry, equipment, usage`, y al final:

```python
@skip_without_db
class ControlCostTests(TestCase):
    """Costos, pieza 6a: fila de costo, serie AC, columnas, orden, avisos y Excel.

    EV 900.000 (2 m3 × 450.000); AC directo 1.200.000; indirecto 100.000; BAC 5.400.000;
    Administración 10 % (AIU de `cost_world`)."""

    def setUp(self) -> None:
        self.world, self.budget = cost_world()
        self.concrete = item(self.budget, "2.1")
        self.structure = item(self.budget, "2")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))
        cost_entry(
            self.world,
            amount="1200000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.ITEM,
            items=(self.concrete,),
            chapter=self.structure,
            task=self.world.task(5),
        )
        cost_entry(self.world, amount="100000.00", status=CostStatus.APPROVED)
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/control/"
        self.client.force_login(self.world.admin)

    def test_cost_row_curve_and_columns(self) -> None:
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, "Costo real directo (AC)")
        self.assertContains(response, "$1.200.000,00")
        self.assertContains(response, "CPI 0,75 · Sobrecosto")
        self.assertContains(response, "$7.200.000,00")  # EAC
        self.assertContains(response, 'class="ctl-over"')
        self.assertContains(response, "Rendimiento necesario para terminar en presupuesto")
        self.assertContains(response, "CPI adm. 0,90")
        self.assertContains(response, "CPI total 0,76")
        self.assertContains(response, 'class="ctl-ac"')
        self.assertContains(response, "AC acumulado")
        self.assertContains(response, '<th class="num">CPI</th>')

    def test_hidden_without_costs_permission(self) -> None:
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.base + AT_CUT + "&orden=cpi")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SPI 0,22")
        self.assertNotContains(response, "Costo real")
        self.assertNotContains(response, 'class="ctl-ac"')
        self.assertNotContains(response, "Ordenar por")
        self.assertContains(response, "De peor a mejor SPI")

    def test_order_by_cpi_and_the_item_tab(self) -> None:
        cost_entry(
            self.world,
            amount="50000.00",
            status=CostStatus.APPROVED,
            scope=CostScope.CHAPTER,
            chapter=self.structure,
        )
        by_cpi = self.client.get(self.base + AT_CUT + "&orden=cpi")
        self.assertContains(by_cpi, "De peor a mejor CPI")
        self.assertContains(by_cpi, '<option value="cpi" selected>')
        self.assertContains(by_cpi, "orden=cpi&amp;desglose=tarea")
        items = self.client.get(self.base + AT_CUT + "&desglose=item&orden=cpi")
        self.assertContains(items, 'aria-current="page">Por ítem</a>')
        self.assertContains(items, "2.1 Concreto zapatas")
        self.assertContains(items, "Índice del grupo: 2 Estructura")

    def test_empty_state_and_warnings(self) -> None:
        CostEntry.objects.all().delete()
        cost_entry(self.world, amount="700.00")  # borrador: no cuenta
        empty = self.client.get(self.base + AT_CUT)
        self.assertContains(empty, "Sin costos aprobados al corte")
        self.assertContains(empty, f"/plataforma/proyectos/{self.world.project.pk}/costos/")
        self.assertContains(empty, "Borradores por aprobar hasta el corte (1")
        usage(self.world, equipment(self.world), date(2026, 3, 10), "500.00")
        warned = self.client.get(self.base + AT_CUT)
        self.assertContains(warned, "Costo directo sin asignar")
        self.assertContains(warned, "Equipos propios sin ítem")

    def test_excel_cost_columns_only_with_permission(self) -> None:
        url = self.base + "control.xlsx?corte=2026-03-20&periodo=mes"

        def sheets(response: HttpResponse) -> tuple[str, str]:
            with zipfile.ZipFile(io.BytesIO(response.content)) as book:
                return (
                    book.read("xl/worksheets/sheet1.xml").decode(),
                    book.read("xl/worksheets/sheet3.xml").decode(),
                )

        periods, indicators = sheets(self.client.get(url))
        self.assertIn(">ac_acumulado<", periods)
        self.assertIn(">CPI<", indicators)
        self.client.force_login(self.world.supervisor)
        periods, indicators = sheets(self.client.get(url))
        self.assertNotIn(">ac_acumulado<", periods)
        self.assertIn(">SPI<", indicators)
        self.assertNotIn(">CPI<", indicators)
```

(Agregar `from django.http import HttpResponse` a los imports.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control`
Expected: FAIL (`SCurve` sin `ac_points`; sin hoja "Indicadores"; la página no muestra la fila de costo).

- [ ] **Step 3: La serie AC de la curva**

En `server/suno/control/chart.py`, agregar a `SCurve`, al final:

```python
    #: Costo real directo acumulado (pieza 6a); vacío sin permiso de costos.
    ac_points: str = ""
    ac_last: str = ""
```

y en `build_s_curve`, reemplazar el cálculo de `measured` y del eje por:

```python
    measured: list[tuple[int, Decimal]] = []
    actual: list[tuple[int, Decimal]] = []
    for index, period_row in enumerate(rows):
        if period_row.ev_cum is not None:
            measured.append((index, period_row.ev_cum))
        if period_row.ac_cum is not None:
            actual.append((index, period_row.ac_cum))
    raw = money_axis(
        max(
            [
                bac,
                *(r.pv_cum for r in rows),
                *(v for _, v in measured),
                *(v for _, v in actual),
            ]
        )
    )
```

y antes del `return`:

```python
    ac_points = " ".join(["0,100", *(f"{x(i)},{y(v)}" for i, v in actual)]) if actual else ""
```

con, en el `SCurve(...)` del `return`:

```python
        ac_points=ac_points,
        ac_last=_grouped(short_money(actual[-1][1])) if actual else "",
```

- [ ] **Step 4: El Excel**

Reemplazar `server/suno/control/export.py` por:

```python
"""Libro Excel del control (costos, piezas 5 y 6a): "Periodos", "Desglose" e "Indicadores".

Los nombres (tareas, ítems, ubicaciones) los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Montos como número (`float`, a centavos) y fechas en ISO.
Las columnas y filas de costo solo salen si el resultado trae costo (quien lee costos).
"""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx_book

from .actual_cost import CostMeasure
from .earned_value import BreakdownRow, ControlResult

PERIOD_HEADER = [
    "periodo", "desde", "hasta", "pv_periodo", "pv_acumulado",
    "ev_periodo", "ev_acumulado", "sv", "spi",
]  # fmt: skip
COST_PERIOD_HEADER = ["ac_periodo", "ac_acumulado"]
BREAKDOWN_HEADER = ["desglose", "nombre", "bac", "pv", "ev", "sv", "spi", "estado"]
COST_BREAKDOWN_HEADER = ["ac", "cv", "cpi", "estado_costo", "indice_del_grupo"]
INDICATOR_HEADER = ["indicador", "valor"]


def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float."""
    return None if value is None else float(value)


def _breakdown(kind: str, row: BreakdownRow, with_cost: bool) -> list[object]:
    measure = row.measure
    cells = [
        kind, row.label, _num(row.bac), _num(measure.pv), _num(measure.ev),
        _num(measure.sv), _num(measure.spi), measure.status_label,
    ]  # fmt: skip
    if with_cost:
        cost = row.cost
        cells += (
            [_num(cost.ac), _num(cost.cv), _num(cost.cpi), cost.status_label, cost.group]
            if cost is not None
            else [None, None, None, "", ""]
        )
    return [safe_cell(value) for value in cells]


def _indicators(result: ControlResult) -> list[list[object]]:
    indicators, at_cut = result.indicators, result.indicators.at_cut
    rows: list[tuple[str, Decimal | None]] = [
        ("BAC vigente", indicators.bac),
        ("Valor planeado (PV)", at_cut.pv),
        ("Valor ganado (EV)", at_cut.ev),
        ("Variación del cronograma (SV)", at_cut.sv),
        ("SPI", at_cut.spi),
    ]
    cost = result.cost
    if cost is not None:
        rows += [
            ("Costo real directo (AC)", cost.ac_direct),
            ("AC asignado", cost.ac_assigned),
            ("AC sin asignar", cost.ac_unassigned),
            ("AC indirecto", cost.ac_indirect),
            ("Variación del costo (CV)", cost.cv),
            ("CPI", cost.cpi),
            ("Estimado al terminar (EAC)", cost.eac),
            ("Variación al terminar (VAC)", cost.vac),
            ("TCPI", cost.tcpi),
            ("Administración ganada", cost.admin_earned),
            ("CPI de administración", cost.admin_cpi),
            ("CPI total", cost.total_cpi),
        ]
    return [[safe_cell(label), safe_cell(_num(value))] for label, value in rows]


def control_workbook(result: ControlResult) -> bytes:
    with_cost = result.cost is not None
    periods = []
    for row in result.periods:
        cells = [
            row.period.label, row.period.start.isoformat(), row.period.end.isoformat(),
            _num(row.pv), _num(row.pv_cum), _num(row.ev), _num(row.ev_cum),
            _num(row.sv), _num(row.spi),
        ]  # fmt: skip
        if with_cost:
            cells += [_num(row.ac), _num(row.ac_cum)]
        periods.append([safe_cell(value) for value in cells])
    total = BreakdownRow(
        "Proyecto",
        result.indicators.bac,
        result.indicators.at_cut,
        cost=CostMeasure(result.cost.ac_direct, result.cost.ev) if result.cost else None,
    )
    breakdown = [_breakdown("proyecto", total, with_cost)]
    for kind, rows in (
        ("capítulo", result.by_chapter),
        ("ítem", result.by_item),
        ("tarea", result.by_task),
        ("ubicación", result.by_location),
    ):
        breakdown.extend(_breakdown(kind, row, with_cost) for row in rows)
    return to_xlsx_book(
        [
            ("Periodos", PERIOD_HEADER + (COST_PERIOD_HEADER if with_cost else []), periods),
            (
                "Desglose",
                BREAKDOWN_HEADER + (COST_BREAKDOWN_HEADER if with_cost else []),
                breakdown,
            ),
            ("Indicadores", INDICATOR_HEADER, _indicators(result)),
        ]
    )
```

- [ ] **Step 5: La vista**

En `server/suno/control/views.py`:

1. Docstring: agregar al final "Costos, pieza 6a: la fila de costo, la serie AC, sus columnas y el orden por CPI solo para quien lee costos del proyecto (`can_read_costs`); quien no, ve el control de cronograma como antes."
2. Imports: `from suno.core.policies import can_export_budgets, can_read_costs` y `from .earned_value import BreakdownRow, ControlResult, compute, worst_cost_first`.
3. Reemplazar `BREAKDOWN_TABS`, `DEFAULT_TAB`, `Options`, `_options` y `_breakdown` por:

```python
BREAKDOWN_TABS = {
    "capitulo": "Por capítulo",
    "item": "Por ítem",
    "tarea": "Por tarea",
    "ubicacion": "Por ubicación",
}
DEFAULT_TAB = "capitulo"
ORDERS = {"spi": "cronograma (SPI)", "cpi": "costo (CPI)"}
DEFAULT_ORDER = "spi"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class Options:
    cut: date
    kind: str
    tab: str
    order: str = DEFAULT_ORDER

    @property
    def query(self) -> str:
        """Corte y periodo, para la exportación."""
        return urlencode({"corte": self.cut.isoformat(), "periodo": self.kind})

    @property
    def listing(self) -> str:
        """Corte, periodo y orden (si no es el de por defecto), para las pestañas del desglose."""
        values = {"corte": self.cut.isoformat(), "periodo": self.kind}
        if self.order != DEFAULT_ORDER:
            values["orden"] = self.order
        return urlencode(values)


def _options(request: HttpRequest, *, costs: bool) -> Options:
    kind = request.GET.get("periodo", "")
    tab = request.GET.get("desglose", "")
    order = request.GET.get("orden", "")
    return Options(
        cut=_parse_date(request.GET.get("corte")) or timezone.localdate(),
        kind=kind if kind in PERIOD_KINDS else DEFAULT_KIND,
        tab=tab if tab in BREAKDOWN_TABS else DEFAULT_TAB,
        order=order if costs and order in ORDERS else DEFAULT_ORDER,
    )


def _breakdown(result: ControlResult, tab: str, order: str) -> tuple[BreakdownRow, ...]:
    rows = {
        "tarea": result.by_task,
        "ubicacion": result.by_location,
        "item": result.by_item,
    }.get(tab, result.by_chapter)
    return worst_cost_first(rows) if order == "cpi" else rows
```

4. En `dashboard` y en `export`, después de `project = get_matrix_project(...)`:

```python
    costs = can_read_costs(ctx, project)
    options = _options(request, costs=costs)
```

y en ambas llamadas a `load_control_input` agregar `costs=costs`. En `dashboard`, el contexto lleva `"orders": ORDERS` y `"rows": _breakdown(result, options.tab, options.order) if result else ()`.

- [ ] **Step 6: Las plantillas**

Reemplazar `server/suno/core/templates/suno/control/dashboard.html` por:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Control · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="control" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Fecha de corte y periodos">
    <label>Fecha de corte <input type="date" name="corte" value="{{ options.cut|date:'Y-m-d' }}"></label>
    <label>Periodos <select name="periodo">{% for key, label in period_kinds.items %}<option value="{{ key }}" {% if options.kind == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <input type="hidden" name="desglose" value="{{ options.tab }}">
    {% if options.order != "spi" %}<input type="hidden" name="orden" value="{{ options.order }}">{% endif %}
    <button class="button" type="submit">Ver</button>
    {% if can_export_control and result.periods %}<a class="text-link" href="{% url 'control:export' project.pk %}?{{ options.query }}">Exportar a Excel</a>{% endif %}
  </form>
</section>

{% if error %}
<p class="pj-warning" role="alert">{{ error }}</p>
{% elif not result.has_budget %}
<section class="pj-panel"><p class="pj-empty">El proyecto no tiene un presupuesto aplicado. El control valora el avance con los precios vigentes: carga uno en <a href="{% url 'budgets:list' project.pk %}">Presupuestos</a>.</p></section>
{% elif not result.has_plan %}
<section class="pj-panel"><p class="pj-empty">La matriz no tiene cantidades planeadas. El valor planeado sale de ellas: cárgalas en <a href="{% url 'matrix:items' project.pk %}">Matriz</a>.</p></section>
{% else %}
{% if not result.periods %}
<section class="pj-panel"><p class="pj-empty">Ningún par de la matriz tiene precio vigente y fechas en el cronograma. Revisa los avisos.</p></section>
{% else %}
{% with ind=result.indicators m=result.indicators.at_cut %}
<section class="metrics-strip ctl-metrics{% if ind.bac|money|length > 17 %} ctl-long{% endif %}{% if ind.bac|money|length > 19 %} ctl-xlong{% endif %}" aria-label="Indicadores a la fecha de corte">
  <article class="bg-money"><span>BAC vigente</span><strong>{{ ind.bac|money }}</strong><small>{% if ind.contractual_direct_cost is not None %}Costo directo contractual: {{ ind.contractual_direct_cost|money }}{% if ind.contractual_partial %} (parcial: {{ ind.contractual_budgets }} de {{ ind.applied_budgets }} presupuestos){% endif %}{% else %}Sin versión contractual{% endif %}</small></article>
  <article class="bg-money"><span>Valor planeado (PV)</span><strong>{{ m.pv|money }}</strong><small>{% if ind.pv_share is not None %}{{ ind.pv_share|floatformat:1 }} % del BAC{% endif %}</small></article>
  <article class="bg-money"><span>Valor ganado (EV)</span><strong>{{ m.ev|money }}</strong><small>{% if ind.ev_share is not None %}{{ ind.ev_share|floatformat:1 }} % del BAC{% endif %}</small></article>
  <article class="bg-money"><span>Variación del cronograma (SV)</span><strong>{{ m.sv|money }}</strong><small><b class="bg-label ctl-{{ m.status }}">SPI {% if m.spi is not None %}{{ m.spi|floatformat:2 }}{% else %}—{% endif %} · {{ m.status_label }}</b></small></article>
</section>
{% endwith %}
{% if result.cost %}{% include "suno/control/_cost_metrics.html" with c=result.cost %}{% endif %}

<figure class="pj-panel ctl-figure" aria-labelledby="ctl-curve-title">
  <figcaption id="ctl-curve-title"><strong>Curva S: valor planeado{% if result.cost %}, valor ganado y costo real{% else %} y valor ganado{% endif %} acumulados</strong></figcaption>
  <ul class="ctl-legend" aria-label="Leyenda">
    <li><i class="ctl-key"></i> Valor planeado (PV) · {{ curve.pv_last }}</li>
    <li><i class="ctl-key ctl-key-ev"></i> Valor ganado (EV){% if curve.ev_last %} · {{ curve.ev_last }}{% endif %}</li>
    {% if result.cost %}<li><i class="ctl-key ctl-key-ac"></i> Costo real directo (AC){% if curve.ac_last %} · {{ curve.ac_last }}{% endif %}</li>{% endif %}
    <li><i class="ctl-key ctl-key-cut"></i> Fecha de corte</li>
  </ul>
  <div class="ctl-chart" style="--ctl-tick-chars: {{ curve.tick_chars }}">
    <div class="ctl-ticks" aria-hidden="true">{% for pos, label in curve.axis.ticks %}<span style="bottom: {{ pos|stringformat:'s' }}%">{{ label }}</span>{% endfor %}</div>
    <div class="ctl-plot" role="img" aria-label="Curva S acumulada{% if result.cost %} con el costo real{% endif %}. Los números están en la tabla de la curva.">
      {% for pos, label in curve.axis.ticks %}<i class="ctl-grid" style="bottom: {{ pos|stringformat:'s' }}%"></i>{% endfor %}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {% if curve.cut_x is not None %}<line class="ctl-cut" x1="{{ curve.cut_x|stringformat:'s' }}" x2="{{ curve.cut_x|stringformat:'s' }}" y1="0" y2="100"/>{% endif %}
        <polyline class="ctl-pv" points="{{ curve.pv_points }}"/>
        {% if curve.ev_points %}<polyline class="ctl-ev" points="{{ curve.ev_points }}"/>{% endif %}
        {% if curve.ac_points %}<polyline class="ctl-ac" points="{{ curve.ac_points }}"/>{% endif %}
      </svg>
    </div>
    <div class="ctl-xlabels" aria-hidden="true">{% for pos, label in curve.x_labels %}<span style="left: {{ pos|stringformat:'s' }}%">{{ label }}</span>{% endfor %}</div>
  </div>
  <p class="pj-muted ctl-source">Eje vertical: {{ curve.axis.unit }}. PV repartido por días laborables de la línea base; EV con las cantidades validadas hasta el {{ result.cut|date:"d/m/Y" }}. Precios del presupuesto vigente.{% if result.cost %} AC: costos aprobados del libro y automáticos (equipos propios y transporte de terceros) hasta el corte, sin indirectos.{% endif %}</p>
</figure>

<details class="pj-panel ctl-table">
  <summary>Tabla de la curva ({{ result.periods|length }} periodos)</summary>
  <div class="pj-scroll">
  <table class="pj-table">
    <caption class="sr-only">Valor planeado, valor ganado{% if result.cost %} y costo real{% endif %} por periodo</caption>
    <thead><tr><th>Periodo</th><th>Hasta</th><th class="num">PV del periodo</th><th class="num">PV acumulado</th><th class="num">EV del periodo</th><th class="num">EV acumulado</th><th class="num">SV</th><th class="num">SPI</th>{% if result.cost %}<th class="num">AC del periodo</th><th class="num">AC acumulado</th>{% endif %}</tr></thead>
    <tbody>
    {% for row in result.periods %}
      <tr{% if not row.period.measured %} class="pj-muted"{% endif %}>
        <td>{{ row.period.label }}</td>
        <td>{{ row.period.end|date:"d/m/Y" }}</td>
        <td class="num">{{ row.pv|signed }}</td>
        <td class="num">{{ row.pv_cum|signed }}</td>
        <td class="num">{% if row.ev is not None %}{{ row.ev|signed }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.ev_cum is not None %}{{ row.ev_cum|signed }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.sv is not None %}{{ row.sv|signed }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.spi is not None %}{{ row.spi|floatformat:2 }}{% else %}—{% endif %}</td>
        {% if result.cost %}
        <td class="num">{% if row.ac is not None %}{{ row.ac|signed }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.ac_cum is not None %}{{ row.ac_cum|signed }}{% else %}—{% endif %}</td>
        {% endif %}
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</details>

<section class="pj-panel" aria-labelledby="ctl-breakdown-title">
  <h2 id="ctl-breakdown-title">Desglose a la fecha de corte</h2>
  <nav class="pj-tabs ctl-tabs" aria-label="Desglose">
    {% for key, label in tabs.items %}<a href="?{{ options.listing }}&amp;desglose={{ key }}" {% if options.tab == key %}aria-current="page"{% endif %}>{{ label }}</a>{% endfor %}
  </nav>
  {% if result.cost %}
  <form method="get" class="pj-filters ctl-order" aria-label="Orden del desglose">
    <input type="hidden" name="corte" value="{{ options.cut|date:'Y-m-d' }}">
    <input type="hidden" name="periodo" value="{{ options.kind }}">
    <input type="hidden" name="desglose" value="{{ options.tab }}">
    <label>Ordenar por <select name="orden">{% for key, label in orders.items %}<option value="{{ key }}" {% if options.order == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Ordenar</button>
  </form>
  {% endif %}
  <p class="pj-muted">{% if options.order == "cpi" %}De peor a mejor CPI; sin costo, al final.{% else %}De peor a mejor SPI; sin valor planeado a la fecha, al final.{% endif %}{% if options.tab == "tarea" and result.cost_warnings.no_task %} {{ result.cost_warnings.no_task|money }} de costo directo no indicó tarea y no está en esta tabla.{% endif %}{% if options.tab == "ubicacion" and result.cost %} El costo no se desglosa por ubicación en esta versión.{% endif %}</p>
  <div class="pj-scroll">
  <table class="pj-table ctl-breakdown">
    <thead><tr><th>{% if options.tab == "tarea" %}Tarea{% elif options.tab == "ubicacion" %}Ubicación{% elif options.tab == "item" %}Ítem{% else %}Capítulo{% endif %}</th><th class="num">BAC</th><th class="num">PV</th><th class="num">EV</th><th class="num">SV</th><th class="num">SPI</th><th>Estado</th>{% if result.cost and options.tab != "ubicacion" %}<th class="num">AC</th><th class="num">CV</th><th class="num">CPI</th><th>Costo</th>{% endif %}</tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>
        <td class="pj-name">{% if row.task_id %}<a href="{% url 'projects:task' project.pk row.task_id %}">{{ row.label }}</a>{% else %}{{ row.label }}{% endif %}{% if row.cost.group %}<br><small class="ctl-group">Índice del grupo: {{ row.cost.group }}</small>{% endif %}</td>
        <td class="num">{{ row.bac|signed }}</td>
        <td class="num">{{ row.measure.pv|signed }}</td>
        <td class="num">{{ row.measure.ev|signed }}</td>
        <td class="num">{{ row.measure.sv|signed }}</td>
        <td class="num">{% if row.measure.spi is not None %}{{ row.measure.spi|floatformat:2 }}{% else %}—{% endif %}</td>
        <td><span class="bg-label ctl-{{ row.measure.status }}">{{ row.measure.status_label }}</span></td>
        {% if result.cost and options.tab != "ubicacion" %}
        <td class="num">{{ row.cost.ac|signed }}</td>
        <td class="num">{{ row.cost.cv|signed }}</td>
        <td class="num">{% if row.cost.cpi is not None %}{{ row.cost.cpi|floatformat:2 }}{% else %}—{% endif %}</td>
        <td><span class="bg-label ctl-{{ row.cost.status }}">{{ row.cost.status_label }}</span></td>
        {% endif %}
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endif %}
{% include "suno/control/_warnings.html" with w=result.warnings %}
{% if result.cost_warnings %}{% include "suno/control/_cost_warnings.html" with w=result.cost_warnings %}{% endif %}
{% endif %}
{% endblock %}
```

`server/suno/core/templates/suno/control/_cost_metrics.html`:

```html
{% load money_tags %}
{% if c.empty %}
<section class="pj-panel ctl-cost-empty"><p class="pj-empty">Sin costos aprobados al corte.{% if can_view_costs %} Regístralos y apruébalos en el <a href="{% url 'costs:list' project.pk %}">libro de costos</a>.{% endif %}</p></section>
{% else %}
<section class="metrics-strip ctl-metrics ctl-cost{% if c.bac|money|length > 17 %} ctl-long{% endif %}{% if c.bac|money|length > 19 %} ctl-xlong{% endif %}" aria-label="Indicadores de costo a la fecha de corte">
  <article class="bg-money"><span>Costo real directo (AC)</span><strong>{{ c.ac_direct|money }}</strong><small>{% if c.ac_share is not None %}{{ c.ac_share|floatformat:1 }} % del BAC{% endif %}{% if c.ac_unassigned %} · {{ c.ac_unassigned|money }} sin asignar{% endif %}</small></article>
  <article class="bg-money"><span>Variación del costo (CV)</span><strong>{{ c.cv|money }}</strong><small><b class="bg-label ctl-{{ c.status }}">CPI {% if c.cpi is not None %}{{ c.cpi|floatformat:2 }}{% else %}—{% endif %} · {{ c.status_label }}</b></small></article>
  <article class="bg-money"><span>Estimado al terminar (EAC)</span><strong{% if c.over_budget %} class="ctl-over"{% endif %}>{% if c.eac is not None %}{{ c.eac|money }}{% else %}—{% endif %}</strong><small>VAC {% if c.vac is not None %}{{ c.vac|money }}{% else %}—{% endif %}</small></article>
  <article class="bg-money"><span>Índice para terminar (TCPI)</span><strong>{% if c.tcpi is not None %}{{ c.tcpi|floatformat:2 }}{% else %}—{% endif %}</strong><small>Rendimiento necesario para terminar en presupuesto</small></article>
  <article class="bg-money"><span>Administración</span>{% if c.admin_earned is None %}<strong>—</strong><small>Sin línea de Administración · CPI total {% if c.total_cpi is not None %}{{ c.total_cpi|floatformat:2 }}{% else %}—{% endif %}</small>{% else %}<strong><b class="bg-label ctl-{{ c.admin_status }}">CPI adm. {% if c.admin_cpi is not None %}{{ c.admin_cpi|floatformat:2 }}{% else %}—{% endif %}</b></strong><small>Indirectos {{ c.ac_indirect|money }} · ganada {{ c.admin_earned|money }} · <b class="bg-label ctl-{{ c.total_status }}">CPI total {% if c.total_cpi is not None %}{{ c.total_cpi|floatformat:2 }}{% else %}—{% endif %}</b></small>{% endif %}</article>
</section>
{% endif %}
```

`server/suno/core/templates/suno/control/_cost_warnings.html`:

```html
{% load money_tags %}
{% if w.count %}
<section class="pj-panel" aria-labelledby="ctl-cost-warnings-title">
  <h2 id="ctl-cost-warnings-title">Avisos de costo</h2>
  {% if w.unassigned %}
  <details class="ctl-warning"><summary>Costo directo sin asignar ({{ w.unassigned_total|money }}): entra en el AC directo, no en el desglose por capítulo ni por ítem</summary>
    <ul>{% for u in w.unassigned %}<li>{{ u.origin }}: {{ u.amount|money }}</li>{% endfor %}</ul>
  </details>
  {% endif %}
  {% if w.no_admin %}
  <details class="ctl-warning"><summary>Presupuestos sin línea de Administración ({{ w.no_admin|length }}): no aportan administración ganada</summary>
    <ul>{% for name in w.no_admin %}<li>{{ name }}</li>{% endfor %}</ul>
  </details>
  {% endif %}
  {% if w.retired %}
  <details class="ctl-warning"><summary>Costos de ítems o capítulos que ya no están en la versión vigente ({{ w.retired|length }}): cuentan en su capítulo de origen</summary>
    <div class="pj-scroll"><table class="pj-table">
      <thead><tr><th>Ítem o capítulo</th><th>Cuenta en</th><th class="num">AC</th></tr></thead>
      <tbody>{% for r in w.retired %}<tr><td class="pj-wrap">{{ r.item }}</td><td class="pj-wrap">{{ r.chapter }}</td><td class="num">{{ r.amount|signed }}</td></tr>{% endfor %}</tbody>
    </table></div>
  </details>
  {% endif %}
  {% if w.pending_count %}
  <details class="ctl-warning"><summary>Borradores por aprobar hasta el corte ({{ w.pending_count }}, {{ w.pending_amount|money }}): no cuentan</summary>
    {% if can_view_costs %}<p><a href="{% url 'costs:list' project.pk %}?estado=draft">Ver los borradores</a></p>{% endif %}
  </details>
  {% endif %}
  {% if w.exhausted %}
  <details class="ctl-warning"><summary>Presupuesto agotado: el costo real directo ya alcanzó el BAC y el TCPI no tiene valor</summary></details>
  {% endif %}
  {% if w.unpriced_usages or w.unpriced_trips %}
  <details class="ctl-warning"><summary>Costos automáticos sin valor: {{ w.unpriced_usages }} uso{{ w.unpriced_usages|pluralize }} de equipo sin tarifa y {{ w.unpriced_trips }} viaje{{ w.unpriced_trips|pluralize }} sin precio; no suman</summary></details>
  {% endif %}
</section>
{% endif %}
```

- [ ] **Step 7: Estilos**

En `server/suno/core/static/suno/css/projects.css`, después de las reglas `ctl-*`:

```css
/* Control de costo (costos, pieza 6a). */
.metrics-strip.ctl-cost { margin-top: 12px; grid-template-columns: repeat(5, minmax(0, 1fr)); }
.ctl-over { color: var(--suno-red); }
.ctl-key-ac { border-top: 2px dashed var(--chart-petrol); }
.ctl-plot .ctl-ac { stroke: var(--chart-petrol); stroke-width: 2; stroke-dasharray: 6 3; }
.ctl-group { color: var(--suno-muted); font-size: 11px; }
.ctl-order { margin: 0 0 8px; }
@media (max-width: 1100px) { .metrics-strip.ctl-cost { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 767px) {
  .metrics-strip.ctl-cost article:nth-child(5) { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--suno-line); }
}
```

y subir `?v=` de `projects.css` en `projects/base.html` a `20261112-costs-v3`.

- [ ] **Step 8: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control suno/costs suno/projects
```

Expected: PASS, incluidas las pruebas de la pieza 5 (`control.xlsx?corte=2026-03-20&amp;periodo=mes` sigue igual porque la exportación usa `options.query`).

- [ ] **Step 9: Commit**

```bash
git add server/suno/control server/suno/core/templates server/suno/core/static
git commit -m "feat(control): cost indicators, actual cost curve and cpi breakdown

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Prueba de carga (5000 pares y 20 000 movimientos en menos de 1 s)

**Files:**
- Modify: `server/suno/control/tests/test_performance.py` (función y clase nuevas al final)

La prueba sigue la de la pieza 5: datos con `bulk_create` en `setUpTestData` (fuera de la medición) y `time.perf_counter` sobre la **segunda** petición. Reutiliza `build_large_project` (500 tareas × 10 ítems, 1000 registros validados) y suma 20 000 movimientos aprobados con fecha antes del corte: uno de cada cuatro a un ítem (con su `CostEntryItem`), uno de cada cuatro a un capítulo y la mitad indirectos.

- [ ] **Step 1: Escribir la prueba**

En `server/suno/control/tests/test_performance.py`, agregar a los imports `CostDocumentType`, `CostEntry`, `CostEntryItem`, `CostKind`, `CostScope`, `CostStatus` (de `suno.core.models`) y `from suno.costs.tests.factories import support_blob`, y al final del archivo:

```python
ENTRIES = 20_000


def add_cost_entries(world: FieldWorld) -> None:
    """20 000 movimientos aprobados antes del corte (25 de enero de 2026 el último)."""
    org, project = world.org, world.project
    rows = list(BudgetItem.objects.filter(budget__project=project).values_list("code", "pk"))
    chapters = {code: pk for code, pk in rows if "." not in code}
    items = [(code, pk) for code, pk in rows if "." in code]
    blob = support_blob("carga del costo")
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    scopes = (CostScope.ITEM, CostScope.CHAPTER, CostScope.INDIRECT, CostScope.INDIRECT)
    for n in range(ENTRIES):
        scope = scopes[n % 4]
        code, item_id = items[n % len(items)]
        entry = CostEntry(
            org=org,
            project=project,
            incurred_on=FIRST_START + timedelta(days=n % 385),
            kind=CostKind.MATERIALS,
            description=f"Costo de carga {n}",
            amount=Decimal(1000 + n % 500),
            document_type=CostDocumentType.INVOICE,
            scope=scope,
            chapter_id=chapters[code.split(".")[0]] if scope != CostScope.INDIRECT else None,
            status=CostStatus.APPROVED,
            created_by=world.admin,
            updated_by=world.admin,
            decided_by=world.supervisor,
            decided_at=now,
            attachment=blob,
        )
        entries.append(entry)
        if scope == CostScope.ITEM:
            links.append(CostEntryItem(org=org, entry=entry, item_id=item_id))
    CostEntry.objects.bulk_create(entries, batch_size=2000)
    CostEntryItem.objects.bulk_create(links, batch_size=2000)


@skip_without_db
class ControlCostLoadTests(TestCase):
    """Costos, pieza 6a (spec, sección 6): la página con costo, también en menos de 1 s."""

    world: FieldWorld

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga del costo")
        build_large_project(cls.world)
        add_cost_entries(cls.world)

    def test_page_with_5000_pairs_and_20000_entries_renders_under_a_second(self) -> None:
        self.assertEqual(CostEntry.objects.filter(project=self.world.project).count(), ENTRIES)
        self.client.force_login(self.world.admin)
        url = (
            f"/plataforma/proyectos/{self.world.project.pk}/control/"
            "?corte=2026-01-30&periodo=semana&desglose=tarea&orden=cpi"
        )
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        started = time.perf_counter()
        response = self.client.get(url)
        elapsed = time.perf_counter() - started
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Costo real directo (AC)")
        self.assertContains(response, "Tarea de carga 499")
        self.assertLess(elapsed, LIMIT_SECONDS, f"la página tardó {elapsed:.3f} s")
```

Nota: el administrador de `make_field_world` es Gestor de Costos por la señal (`LEGACY_GRANTS`), así que la página carga el costo y ordena por CPI.

- [ ] **Step 2: Correr**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_performance.py`
Expected: PASS las dos pruebas. Anotar el tiempo medido (agregar temporalmente `print(f"{elapsed:.3f}")` si hace falta y quitarlo antes del commit) para el registro de avance.

- [ ] **Step 3: Si pasa de 1 s**

No subir `LIMIT_SECONDS` sin aprobación del usuario. Perfilar dentro del contenedor (`infra/scripts/test_docker.sh --cmd "python -m cProfile -s cumtime -m pytest -q suno/control/tests/test_performance.py -k 20000 | head -60"`). Lo esperable: la construcción de 20 000 `CostLine` (es lineal), la consulta de ítems sin índice (agregar `models.Index(fields=["org", "entry"])` no hace falta: la FK ya tiene índice) o la plantilla del desglose por tarea. El cálculo no debe hacer trabajo por línea × periodo (cada línea va a su periodo con `bisect_right`).

- [ ] **Step 4: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
git add server/suno/control/tests/test_performance.py
git commit -m "test(control): load test with 5000 pairs and 20000 cost entries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Costos en los datos de ejemplo (`seed_control_demo`)

**Files:**
- Modify: `server/suno/management/commands/seed_control_demo.py`
- Modify: `server/suno/control/tests/test_seed_demo.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/control/tests/test_seed_demo.py`:

1. Imports: `import tempfile`, `from django.test import TestCase, override_settings` (en lugar de solo `TestCase`) y `from suno.core.models import CostEntry, CostStatus, Project, Role`.
2. Al final de `setUp`, un almacén temporal para el soporte de ejemplo:

```python
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
```

3. Una prueba nueva:

```python
    def test_seeds_the_cost_ledger(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        result = compute(load_control_input(ctx, project, cut=TODAY, kind=MONTH, costs=True))
        cost, warnings = result.cost, result.cost_warnings
        assert cost is not None and warnings is not None
        self.assertGreater(cost.ac_direct, Decimal(0))
        self.assertGreater(cost.ac_indirect, Decimal(0))
        self.assertIsNotNone(cost.cpi)
        self.assertIsNotNone(cost.admin_earned)  # Administración 8 %
        self.assertEqual(warnings.pending_count, 1)
        self.assertEqual(
            {u.origin for u in warnings.unassigned},
            {"Equipos propios sin ítem", "Transporte de terceros"},
        )
        entries = CostEntry.objects.filter(project=project)
        self.assertEqual(
            set(entries.values_list("scope", flat=True)),
            {"indirect", "item", "chapter", "selection"},
        )
        self.assertTrue(entries.filter(status=CostStatus.REJECTED).exists())
        self.assertTrue(entries.filter(reverses__isnull=False).exists())
        self.assertFalse(
            entries.filter(status=CostStatus.APPROVED, reverses__isnull=True, attachment=None).exists()
        )
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: FAIL (`result.cost` sin cargos ni movimientos: `ac_direct` es 0).

- [ ] **Step 3: Implementar**

En `server/suno/management/commands/seed_control_demo.py`:

1. Docstring: agregar al primer párrafo "Desde la pieza 6a también siembra el libro de costos: cargos AIU (Administración del 8 %), un residente que registra y el administrador que aprueba, movimientos de todos los tipos y asignaciones, un borrador, un rechazado, un reverso, un equipo propio con y sin ítem y viajes de un transportador."
2. Imports nuevos:

```python
from django.core.files.uploadedfile import SimpleUploadedFile

from suno.attachments.web import store_upload
from suno.budgets.totals import BASE_DIRECT, ChargeSpec, compute_charges, line_base
from suno.core.models import (
    BudgetCharge,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    CostStatus,
    Equipment,
    EquipmentOwnership,
    EquipmentType,
    EquipmentUsage,
    EquipmentUsageStatus,
    Membership,
    TransportContractor,
    TransportMaterial,
    TransportRoute,
    TransportVehicle,
    Trip,
)
from suno.equipment.costing import usage_cost, usage_hours
```

(los modelos, sumados al import existente de `suno.core.models`; `from django.db.models import Max`).

3. Constantes, después de `LEAVES`:

```python
CHARGES = [
    ChargeSpec(1, "Administración", Decimal("8"), BASE_DIRECT),
    ChargeSpec(2, "Imprevistos", Decimal("3"), BASE_DIRECT),
    ChargeSpec(3, "Utilidad", Decimal("5"), BASE_DIRECT),
    ChargeSpec(4, "IVA sobre utilidad", Decimal("19"), line_base(3)),
]
#: Un PDF mínimo como soporte de ejemplo (se sube de verdad al almacén configurado).
DEMO_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
EQUIPMENT_RATE = Decimal("180000")
TRIP_PRICE = Decimal("85000")


class Cost(NamedTuple):
    #: Días desde el inicio del proyecto.
    offset: int
    kind: str
    scope: str
    #: Códigos de los ítems (`item`, `selection`).
    codes: tuple[str, ...]
    #: Código del capítulo (`chapter`, `selection`).
    chapter: str
    amount: str
    description: str
    third_party: str
    document_type: str
    #: Nombre de la tarea (opcional).
    task: str = ""


K, S, DOC = CostKind, CostScope, CostDocumentType
COSTS = [
    Cost(10, K.MATERIALS, S.ITEM, ("1.1",), "", "11800000", "Estacas, pintura y mojones",
         "Topografía del Norte SAS", DOC.INVOICE, "Localización y replanteo"),
    Cost(25, K.SUBCONTRACT, S.CHAPTER, (), "2", "46000000", "Acta 1: movimiento de tierras",
         "Excavaciones Andinas SAS", DOC.COLLECTION_ACCOUNT),
    Cost(60, K.SUBCONTRACT, S.CHAPTER, (), "2", "51500000", "Acta 2: movimiento de tierras",
         "Excavaciones Andinas SAS", DOC.COLLECTION_ACCOUNT),
    Cost(70, K.RENTED_EQUIPMENT, S.ITEM, ("2.1",), "", "18500000",
         "Retroexcavadora alquilada (contratista)", "Alquileres del Valle", DOC.INVOICE,
         "Excavación frente sur"),
    Cost(105, K.MATERIALS, S.ITEM, ("3.1",), "", "86000000", "Concreto premezclado 3000 psi",
         "Concretos SA", DOC.INVOICE, "Zapatas eje A"),
    Cost(110, K.MATERIALS, S.ITEM, ("3.2",), "", "61000000", "Acero de refuerzo 60000 psi",
         "Aceros de Colombia", DOC.INVOICE, "Zapatas eje A"),
    Cost(130, K.LABOR, S.SELECTION, ("3.1", "3.2"), "3", "27000000",
         "Cuadrilla de estructura (quincenas)", "Cuadrilla Pérez", DOC.COLLECTION_ACCOUNT),
    Cost(160, K.MATERIALS, S.ITEM, ("4.1",), "", "54000000", "Tubería PVC de 6 pulgadas",
         "Tuberías y Accesorios SAS", DOC.INVOICE, "Tubería principal"),
    Cost(165, K.OTHER, S.CHAPTER, (), "1", "3500000", "Pólizas y trámites",
         "Seguros del Sur", DOC.RECEIPT),
]  # fmt: skip
```

4. En `_Seeder.__init__`, agregar `self.costs = 0` y `self.chapters: dict[str, BudgetItem] = {}`.

5. En `_budget`, calcular los cargos antes de crear la versión y guardarlos:

```python
        charges = compute_charges(money(direct), CHARGES)
```

`BudgetVersion.objects.create(..., direct_cost=money(direct), total=charges.total)` y, después de crear la versión:

```python
        BudgetCharge.objects.bulk_create(
            [
                BudgetCharge(
                    org=self.org,
                    version=version,
                    order=line.order,
                    name=line.name,
                    pct=line.pct,
                    base=line.base,
                    amount=line.amount,
                )
                for line in charges.lines
            ]
        )
```

y, en el bucle de capítulos, `self.chapters[code] = chapter` justo después de crear cada capítulo.

6. En `run`, antes de `return project`:

```python
        self._costs(project, start, items, tasks)
```

7. Métodos nuevos de `_Seeder`:

```python
    def _resident(self) -> AppUser:
        """Quien registra los costos de ejemplo: nadie aprueba lo suyo."""
        username = f"residente-{self.org.pk.hex[:8]}"
        user = AppUser.objects.filter(username=username).first()
        if user is None:
            user = AppUser.objects.create_user(
                username=username,
                email=f"{username}@example.com",
                password=None,
                org=self.org,
                display_name="Residente de obra (ejemplo)",
            )
            Membership.objects.create(org=self.org, user=user, role=Role.SUPERVISOR)
        return user

    def _cost(
        self,
        project: Project,
        items: dict[str, BudgetItem],
        tasks: dict[str, Task],
        cost: Cost,
        day: date,
        *,
        author: AppUser,
        status: str = CostStatus.APPROVED,
        support: Any = None,
        note: str = "",
    ) -> CostEntry:
        self.costs += 1
        decided = status != CostStatus.DRAFT
        chapter = self.chapters[cost.chapter] if cost.chapter else None
        if cost.scope == CostScope.ITEM:
            chapter = self.chapters[cost.codes[0].split(".")[0]]
        entry = CostEntry.objects.create(
            org=self.org,
            project=project,
            incurred_on=day,
            kind=cost.kind,
            description=cost.description,
            amount=Decimal(cost.amount),
            third_party=cost.third_party,
            document_type=cost.document_type,
            document_number=f"D-{self.costs:04d}",
            scope=cost.scope,
            chapter=chapter,
            task=tasks.get(cost.task) if cost.task else None,
            status=status,
            created_by=author,
            updated_by=author,
            decided_by=self.admin if decided else None,
            decided_at=timezone.now() if decided else None,
            decision_note=note,
            attachment=support,
            attachment_name="factura-ejemplo.pdf" if support is not None else "",
        )
        for code in cost.codes:
            CostEntryItem.objects.create(org=self.org, entry=entry, item=items[code])
        return entry

    def _costs(
        self,
        project: Project,
        start: date,
        items: dict[str, BudgetItem],
        tasks: dict[str, Task],
    ) -> None:
        resident = self._resident()
        support = store_upload(
            SimpleUploadedFile("factura-ejemplo.pdf", DEMO_PDF, content_type="application/pdf")
        )
        for cost in COSTS:
            self._cost(
                project, items, tasks, cost, start + timedelta(days=cost.offset),
                author=resident, support=support,
            )  # fmt: skip
        payroll = Cost(0, K.PAYROLL, S.INDIRECT, (), "", "9200000",
                       "Nómina de dirección de obra", "Nómina interna", DOC.PAYROLL)  # fmt: skip
        day = start + timedelta(days=27)
        while day <= self.today:
            self._cost(project, items, tasks, payroll, day, author=resident, support=support)
            day += timedelta(days=30)
        # Un reverso: factura registrada dos veces.
        duplicate = self._cost(
            project, items, tasks,
            Cost(90, K.MATERIALS, S.ITEM, ("2.2",), "", "7200000",
                 "Material de relleno (factura duplicada)", "Agregados del Río", DOC.INVOICE),
            start + timedelta(days=90), author=resident, support=support,
        )  # fmt: skip
        reversal = CostEntry.objects.create(
            org=self.org,
            project=project,
            incurred_on=self.today - timedelta(days=10),
            kind=duplicate.kind,
            description=f"Reverso: {duplicate.description}",
            amount=-duplicate.amount,
            third_party=duplicate.third_party,
            document_type=duplicate.document_type,
            document_number=duplicate.document_number,
            scope=duplicate.scope,
            chapter=duplicate.chapter,
            status=CostStatus.APPROVED,
            created_by=self.admin,
            updated_by=self.admin,
            decided_by=self.admin,
            decided_at=timezone.now(),
            decision_note="Factura registrada dos veces",
            reverses=duplicate,
        )
        CostEntryItem.objects.create(org=self.org, entry=reversal, item=items["2.2"])
        # Uno por aprobar (sin soporte) y uno rechazado.
        self._cost(
            project, items, tasks,
            Cost(0, K.MATERIALS, S.ITEM, ("4.1",), "", "6400000", "Accesorios de tubería",
                 "Tuberías y Accesorios SAS", DOC.INVOICE, "Tubería principal"),
            self.today - timedelta(days=3), author=resident, status=CostStatus.DRAFT,
        )  # fmt: skip
        self._cost(
            project, items, tasks,
            Cost(0, K.OTHER, S.INDIRECT, (), "", "850000", "Viáticos de la visita técnica",
                 "", DOC.RECEIPT),
            self.today - timedelta(days=5), author=resident, status=CostStatus.REJECTED,
            note="Falta la factura",
        )  # fmt: skip
        self._equipment(project, start, items)
        self._trips(project, start, resident)

    def _equipment(self, project: Project, start: date, items: dict[str, BudgetItem]) -> None:
        """Una retroexcavadora propia: 8 h por semana en la excavación; una de cada tres
        jornadas sin ítem (va a "sin asignar")."""
        kind, _ = EquipmentType.objects.get_or_create(
            org=self.org, code="RET-DEMO", defaults={"name": "Retroexcavadora (ejemplo)"}
        )
        machine, _ = Equipment.objects.get_or_create(
            org=self.org,
            code="RE-DEMO-01",
            defaults={
                "name": "Retroexcavadora de ejemplo",
                "equipment_type": kind,
                "ownership": EquipmentOwnership.OWN,
                "created_by": self.admin,
                "updated_by": self.admin,
            },
        )
        begin, end = time(7), time(15)
        hours = usage_hours(begin, end)
        for week in range(16):
            day = start + timedelta(days=37 + 7 * week)
            if day > self.today:
                break
            EquipmentUsage.objects.create(
                org=self.org,
                equipment=machine,
                project=project,
                operator=self.admin,
                usage_date=day,
                start_time=begin,
                end_time=end,
                activity="Excavación mecánica",
                status=EquipmentUsageStatus.COMPLETED,
                budget_item=None if week % 3 == 2 else items["2.1"],
                billable_hours=hours,
                hourly_rate=EQUIPMENT_RATE,
                cost=usage_cost(hours, EQUIPMENT_RATE),
                created_by=self.admin,
                updated_by=self.admin,
            )

    def _trips(self, project: Project, start: date, resident: AppUser) -> None:
        """Tres viajes de gravas de un transportador (transporte de terceros)."""
        material, _ = TransportMaterial.objects.get_or_create(org=self.org, name="Gravas (ejemplo)")
        route, _ = TransportRoute.objects.get_or_create(
            org=self.org,
            origin="Cantera La Esperanza",
            destination="Obra de ejemplo",
            defaults={"distance_km": Decimal("12.5")},
        )
        contractor, _ = TransportContractor.objects.get_or_create(
            org=self.org, name="Transportes de ejemplo"
        )
        vehicle, _ = TransportVehicle.objects.get_or_create(
            org=self.org, plate="DEMO01", defaults={"contractor": contractor}
        )
        volume = Decimal("14")
        for offset in (40, 55, 70):
            number = (Trip.objects.filter(org=self.org).aggregate(top=Max("number"))["top"] or 0) + 1
            Trip.objects.create(
                id=uuid.uuid4(),
                org=self.org,
                number=number,
                recorded_at=timezone.make_aware(
                    datetime.combine(start + timedelta(days=offset), time(9))
                ),
                vehicle=vehicle,
                project=project,
                route=route,
                distance_km=route.distance_km,
                material=material,
                volume_m3=volume,
                unit_price=TRIP_PRICE,
                amount=money(volume * TRIP_PRICE),
                requested_by=self.admin,
                dispatched_by=resident,
                received_by=self.admin,
                created_by=self.admin,
            )
```

8. En `Command.handle`, el mensaje final dice además cuántos costos sembró:

```python
                f"Proyecto de ejemplo listo: «{project.name}» ({seeder.records} registros, "
                f"{seeder.costs} costos). Página: /plataforma/proyectos/{project.pk}/control/"
```

Notas para quien implementa:
- `test_refuses_to_seed_twice` siembra dos proyectos en la misma organización: el residente, el tipo de equipo, el equipo, el material, el tramo, el transportador y el vehículo se reutilizan (`get_or_create`); los viajes toman el siguiente número.
- El residente es Supervisor (sin la sección Costos): es quien registra; el administrador aprueba. Para entrar como él en la revisión visual hay que darle contraseña y la sección (Task 13).
- Las líneas de `COSTS` llevan `# fmt: skip` para conservar su forma de tabla; las que pasen de 100 columnas se parten a mano.

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/management suno/control && ruff check suno && mypy suno/management suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py suno/core/tests/test_architecture.py
```

Expected: PASS (las pruebas viejas del comando también: el cálculo de la pieza 5 no cambia con los costos).

- [ ] **Step 5: Commit**

```bash
git add server/suno/management/commands/seed_control_demo.py server/suno/control/tests/test_seed_demo.py
git commit -m "feat(control): demo cost ledger in seed_control_demo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Verificación completa, revisión visual, registro de avance y PR

**Files:**
- Modify: `plans/suno-progreso.md`

- [ ] **Step 1: Suite completa y compuertas**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pip install -q bandit && bandit -q -r suno config -x '*/tests/*,*/migrations/*'"
infra/scripts/test_docker.sh --cmd "set -o pipefail; coverage run -m pytest -q -p no:cacheprovider && coverage report | tail -1"
```

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y `healthcheck.py` en el formato; `makemigrations --check` sin cambios (la `0028` ya lo cubre todo); bandit sin hallazgos; cobertura ≥ la de `master` (93 %), con `suno/costs` y `suno/control` cerca del 100 %.

- [ ] **Step 2: Migración hacia atrás**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py migrate core 0027 -v 0 && python manage.py migrate -v 0"
```

Expected: sin errores (la reversa quita los grants de Costos y las tablas; la ida los vuelve a crear).

- [ ] **Step 3: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-costos -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0.

- [ ] **Step 4: Revisión visual a 1440 y 390 px**

Levantar la app contra el Postgres de pruebas (sigue vivo hasta `--down`), sembrar y abrir:

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py bootstrap_demo --org-name 'SUNO Demo' --username agente-demo --password 'Revision-visual-2026' && python manage.py shell -c \"from suno.core.models import AppUser, Membership, Organization, Role; org = Organization.objects.get(name='SUNO Demo'); user = AppUser.objects.create_user(username='admin-demo', email='admin-demo@example.com', password='Revision-visual-2026', org=org); Membership.objects.create(org=org, user=user, role=Role.ADMIN)\" && python manage.py seed_control_demo"
MSYS_NO_PATHCONV=1 docker run --rm -d --name suno-costs-web --network suno-test -p 8000:8000 \
  -v "$(cd server && pwd -W):/app" -v suno-test-venv:/venv -w /app \
  -e POSTGRES_HOST=suno-test-db -e POSTGRES_PASSWORD=test -e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
  python:3.12-slim sh -c "python manage.py runserver 0.0.0.0:8000"
```

Para entrar también como el residente (registra, ve sus rechazados) y como un Revisor, en un `docker exec suno-costs-web python manage.py shell -c "…"`: darle contraseña al residente (`set_password`) y, con `set_section_grant` desde el contexto de `admin-demo`, Colaborador de Costos al residente y Revisor de Costos a otro usuario.

Entrar en `http://localhost:8000/plataforma/ingresar/` como `admin-demo` y recorrer, con capturas de Edge headless a 1440 px y a 390 px (a 390 px dentro de un iframe, porque el mínimo de Edge es 496 px):

- Control: fila de costo, curva con la serie AC (y su leyenda), tabla de la curva abierta con las columnas de AC, pestañas Por capítulo, Por ítem (con "Índice del grupo"), Por tarea (con el aviso de costo sin tarea) y Por ubicación, orden por CPI, avisos de costo abiertos, y el estado vacío (corte antes del primer costo).
- Costos: libro con filtros y totales al pie, una página siguiente, origen "Solo automáticos", formulario en sus cuatro asignaciones (ítem con buscador, capítulo, selección con casillas, indirecto), ficha de un aprobado, de un reverso y de un rechazado, bandeja "Por aprobar", "Mis rechazados" (como el residente), reverso con "abrir un borrador", importación con vista previa válida y con errores por fila.
- Textos largos: alargar con `python manage.py shell -c` la descripción de un costo, el tercero, el nombre de un capítulo y el de una tarea a 120 caracteres, y un valor a 15 cifras.

Revisar: la segunda fila de indicadores (cinco tarjetas) sin cortes a 1440 y en una o dos columnas a 390; la serie AC distinguible de PV y EV; tablas dentro de su panel con desplazamiento horizontal; formularios y botones de la bandeja sin desbordes. Con el adaptador local, el enlace del soporte es `local://…` (no abre en el navegador): es lo esperado en desarrollo. Corregir antes del PR (CSS en `projects.css`, subiendo `?v=` en `base.html`).

Al terminar: `docker rm -f suno-costs-web` y `infra/scripts/test_docker.sh --down`.

- [ ] **Step 5: Registro de avance**

En `plans/suno-progreso.md`, sección nueva al principio de la parte de costos: "Módulo de costos — Pieza 6a (libro de costos y control de costo), servidor — <fecha>", con: PR pendiente y rama; qué se entregó (sección Costos y capacidad `reverse`, modelos y migración `0028`, soporte desde la web con `put_blob`, pestaña "Costos" con libro, formulario, bandeja, rechazados, reverso, importación y exportación, costos automáticos de equipos y transporte, AC, CV, CPI, EAC, VAC, TCPI y administración ganada en el control, serie AC, pestaña "Por ítem" con grupos, orden por CPI, hoja "Indicadores", `seed_control_demo` con costos); las decisiones del plan (resumen de las anotadas en la spec); los números (pruebas, cobertura, tiempo de las dos pruebas de carga); lo que se corrigió en la revisión visual; y lo que sigue (6b subcontratos y actas, 6c materiales, 6d tareo y nómina). Actualizar "Última actualización" del encabezado.

- [ ] **Step 6: Commit, push y PR**

```bash
git add plans/suno-progreso.md
git commit -m "docs(plan): progress log for cost piece 6a

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/costos-libro
gh pr create --base master --head feat/costos-libro \
  --title "feat(costs): cost ledger and cost control (CPI, EAC, TCPI)" \
  --body-file <archivo temporal en el scratchpad con: resumen; qué entra (sección Costos, modelos y migración 0028 aditiva con grants de administración, soporte web, pestaña Costos, costos automáticos, cálculo y página Control, Excel, seed); decisiones del plan; permisos (matriz por rol, alcance por proyecto, 404/403); sin cambios de contrato ni de la app; pruebas, cobertura y tiempos de carga; capturas de la revisión visual; y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)">
```

Esperar el CI (backend, DAST, CodeQL). **No integrar el PR: el merge es del usuario.**

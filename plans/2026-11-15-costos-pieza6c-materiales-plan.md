# Costos — Pieza 6c (materiales: compras, bodegas y consumos): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Llevar en SUNO los materiales de cada proyecto (catálogo de la organización, bodegas, compras directas y a bodega, salidas, devoluciones, traslados, ajustes por conteo físico y un kárdex al costo promedio ponderado móvil), y que lo consumido llegue al libro de costos de la 6a como costo aprobado: la compra directa al aprobarla, el faltante de un ajuste al aprobarlo y las salidas y devoluciones por un **corte de consumos** periódico que aprueba otra persona.

**Architecture:** Dieciocho modelos nuevos en `core/models/material.py` y tres FK de origen en `CostEntry`, en una migración `0030_materials` (aditiva salvo dos restricciones del libro que se reescriben). Un paquete nuevo `server/suno/materials/` lleva el cálculo puro del promedio (`stock.py`), el motor de saldos y kárdex con bloqueo de fila (`kardex.py`), el destino de 6a escrito como texto (`destination.py`), los servicios con auditoría (`catalog_services.py`, `catalog_import.py`, `warehouses.py`, `purchases.py`, `movements.py`, `adjustments.py`, `cuts.py`), las consultas (`queries.py`), el PDF del corte (`pdf.py`), los Excel (`export.py`) y la web (catálogo de la organización en `plataforma/materiales/` y la pestaña "Materiales" del proyecto). Los permisos son los de la sección Costos, más dos políticas de organización para el catálogo. El libro (6a) gana los orígenes "Compra", "Corte" y "Ajuste"; la bandeja "Por aprobar", las compras, ajustes y cortes; el control, el valor en bodega como dato informativo.

**Tech Stack:** Django 5.2, PostgreSQL 16, plantillas de Django con CSS propio (sin JavaScript nuevo), reportlab, `unittest`/`django.test` corridos en Docker.

**Spec:** `plans/2026-11-15-costos-pieza6c-materiales-design.md` (aprobada). Plan de referencia de formato y código base: `plans/2026-11-14-costos-pieza6b-contratos-plan.md`.

**Rama y worktree:** `feat/costos-materiales` en `C:\Users\gabri\APP\wt-server-materiales` (base `master` `3bbd619`, que ya trae 6a y 6b; spec en `6573fb6`). Todos los comandos se corren desde la raíz de ese worktree.

---

## Decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Las marcadas con ⚑ conviene que el usuario las mire (al terminar se copian al final de la spec).

1. **Una migración `0030_materials`.** Tablas `material`, `warehouse`, `material_purchase`, `material_purchase_line`, `material_purchase_line_item`, `material_issue`, `material_issue_item`, `material_issue_line`, `material_return`, `material_return_line`, `material_transfer`, `material_transfer_line`, `inventory_adjustment`, `inventory_adjustment_item`, `inventory_adjustment_line`, `consumption_cut`, `stock_balance`, `stock_movement` y `material_import_token`; columnas nulas `cost_entry.material_purchase_id`, `consumption_cut_id` e `inventory_adjustment_id`; dos opciones nuevas de `CostDocumentType` ("Corte de consumos" e "Inventario físico", solo `choices`); la restricción de signo del libro se reescribe (ver 18) y se agrega "un solo origen". Sin `RunPython`. No nombra la migración del renombre, así que la guardia de nombres no necesita lista blanca nueva.
2. ⚑ **Permiso del catálogo de la organización.** No hay un permiso de catálogo de organización que encaje: el de combustibles es `EQUIPMENT create` (otra sección) y `can_manage_budgets` es de Presupuestos. Se agregan dos políticas en `core/policies.py`: `can_read_material_catalog` (cualquier rol de Costos, con cualquier alcance: los formularios de cada proyecto lo usan) y `can_manage_material_catalog` (`create` **y** `update` de Costos con alcance de **toda la organización**: un acceso limitado a algunos proyectos no basta). Con la matriz fija, gestionan el catálogo Gestor, Revisor y Colaborador con alcance de organización.
3. **Permisos del proyecto** (sección Costos, sin políticas nuevas): ver con `can_read_costs`; registrar con `can_create_costs`; editar y borrar con `can_update_cost`/`can_delete_cost` (estructurales: el Colaborador, solo lo suyo); aprobar `can_approve_costs`; rechazar `can_reject_costs`; anular `can_reverse_costs`; Excel y PDF `can_export_costs`. ⚑ **Bodegas**: crear pide `create`; renombrar, desactivar y activar piden `can_update_cost(bodega)` (`update`; el Colaborador, solo las que creó).
4. ⚑ **El destino se escribe en un campo de texto** (sin JavaScript y cabe en una fila de líneas): vacío o "Indirecto"; la etiqueta o el código de un ítem ("2.1 Concreto zapatas" o "2.1"); "Capítulo 2"; o varios códigos de ítems del mismo capítulo separados por comas ("2.1, 2.2"). La tarea va aparte (lista en los encabezados, número de esquema en las filas). Se valida con la misma función de la 6a, que se extrae como `costs.services.check_assignment` sin cambiar su comportamiento.
5. ⚑ **Precisión.** Cantidades con 4 decimales, valores a centavos y costo unitario con 6 decimales. Una salida al promedio nunca se lleva más valor que el que tiene el saldo (con cantidades enormes, el redondeo del unitario podría pasarse unos centavos): el exceso se recorta.
6. ⚑ **Orden del kárdex**: la secuencia de registro por bodega y material (la spec), no la fecha del documento. El saldo corrido sigue la secuencia; el filtro por fechas solo elige qué filas se muestran. En pantalla, como mucho los últimos 1.000 movimientos del filtro (el Excel lleva hasta 50.000).
7. ⚑ **Retiros por anulación** (anular una compra a bodega o el sobrante de un ajuste): se retira la cantidad por el valor con que entró, sin pasar del valor que tiene el saldo; si se lleva toda la cantidad, el valor restante. Así el saldo nunca queda negativo aunque el promedio haya bajado entre la entrada y la anulación.
8. ⚑ **Editar o borrar una salida** borra sus movimientos del kárdex (la spec no tiene un tipo "reverso de salida") y, al editar, los escribe de nuevo al final de la secuencia al promedio del momento. Así el saldo sigue siendo la suma exacta de los movimientos. **Las devoluciones y los traslados no se editan ni se borran**: un error se corrige con otro documento (una salida, un traslado de vuelta).
9. ⚑ **Devolución**: entra al costo unitario de la línea de salida; la devolución que completa lo que salió de una línea se lleva exactamente el valor que faltaba (sin residuos de centavos).
10. **Bloqueo**: toda escritura de materiales bloquea primero la fila del proyecto con `select_for_update(no_key=True)` (no frena a quien solo inserta filas que apuntan al proyecto), después el documento y al final los saldos en orden de (bodega, material). Serializa consecutivos, periodo cerrado, corte y el "no inventario negativo".
11. **Consecutivos** por proyecto y tipo (`CM-001`, `SA-001`, `DV-001`, `TR-001`, `AJ-001`, `Corte 1`), asignados al crear (máximo + 1); borrar el último borrador libera su número.
12. ⚑ **Estados** con etiquetas neutras ("Borrador", "Aprobado", "Rechazado", "Anulado") para compras, ajustes y cortes.
13. ⚑ **Compra**: el soporte (la factura) se puede adjuntar al crearla o después; adjuntarlo **no** es editar; editar la compra **no** quita la factura (es el soporte del gasto, no un reporte que se imprime y se firma). Para aprobar una compra a bodega, la bodega tiene que seguir activa. El destino no se revalida al aprobar (como 6b). Tercero en el libro: "Proveedor (NIT 900…)" si hay NIT. Descripción: "Compra CM-003 · CEM-01 Cemento gris 50 kg".
14. ⚑ **`client_id`** de una salida: si ya existe en el proyecto, el servicio devuelve esa salida sin crear otra (reintento de la futura app); si existe en otro proyecto, error.
15. ⚑ **Ajuste**: una línea por material; destino obligatorio del faltante (por defecto Indirecto); un conteo sin diferencias no se guarda; la fecha no puede ser futura ni anterior a 2000, pero **el periodo cerrado no aplica a compras ni ajustes** (no entran en los cortes); "Traer el inventario de la bodega" llena las líneas con los materiales que tienen saldo; al anular, el faltante vuelve a la bodega por el valor que salió y el sobrante se retira (los dos como `adjustment_void`).
16. ⚑ **Corte**: el inicio no se elige (día siguiente al fin del último aprobado; sin cortes, la primera salida o devolución sin corte); un corte sin salidas ni devoluciones en el periodo no se crea; al aprobar se exige que el inicio siga siendo ese ("recalcula el corte" si no); no se anula el último aprobado mientras haya un corte abierto. El resumen guardado (`summary`) tiene la **huella** que se compara al aprobar (total, número de salidas y devoluciones y neto por destino) y el **detalle** que imprime el PDF; la ficha recalcula la propuesta y avisa si difiere de lo guardado.
17. **Corte → libro**: un `CostEntry` aprobado por destino con neto distinto de cero; fecha = fin del periodo; tipo Materiales; documento "Corte de consumos" número "Corte 2"; descripción "Corte 2 de consumos · 2.1 Concreto zapatas · tarea 2.1 Columnas"; registró y modificó = quien preparó y recalculó el corte; aprobó = quien aprueba; soporte = el reporte firmado.
18. **Negativos en el libro**: la restricción `cost_entry_amount_sign` admite un valor negativo (no reverso) **solo** si el movimiento viene de un corte (`consumption_cut`): el destino cuyo neto es negativo (se devolvió en el periodo material que salió en un corte anterior). Nueva restricción `cost_entry_single_origin`: a lo sumo uno de `certificate`, `material_purchase`, `consumption_cut`, `inventory_adjustment`.
19. **Libro (6a)**: el filtro de origen queda Todos, Manual, Actas, Compras, Cortes, Ajustes y Solo automáticos ("Manual" excluye todos los documentos). Cada línea de documento dice "Compra CM-003", "Corte 2" o "Ajuste AJ-001" y enlaza al documento. `costs.services.reverse` rechaza un movimiento de materiales ("se anula el documento en la pestaña Materiales") y la ficha no ofrece "Reversar".
20. **Control, "Por ítem"**: una línea "Valor en bodega del proyecto: $X (inventario; no es costo)" sobre la tabla, solo con permiso de costos. El cálculo no cambia.
21. **Listas**: inventario paginado de a 100 filas (con el total del valor filtrado); compras, salidas, traslados, ajustes y cortes muestran los 200 más recientes del filtro con un aviso si hay más (el Excel de salidas lleva el periodo completo, hasta 50.000 líneas).
22. **Avisos de la pestaña**: salidas sin cortar con más de 31 días (cuántas y la más vieja); compras, ajustes y cortes en borrador (cuántos de cada uno).
23. **Excel** (con `safe_cell` en todo texto y auditados con `materials.exported`): `inventario-<proyecto>.xlsx`, `kardex-<código>.xlsx`, `salidas-<desde>-<hasta>.xlsx` y `corte-<n>.xlsx`. **PDF**: `corte-<n>.pdf` (A4 horizontal; destinos con desglose por material, totales y firmas) auditado con `consumption_cut.exported`.
24. **Catálogo**: un material no se borra (no hay botón); se desactiva. ⚑ La **unidad** de un material con movimientos o líneas de compra no se cambia (el kárdex perdería sentido): crear otro material. La importación crea los códigos nuevos y actualiza nombre, unidad y categoría de los existentes; las filas sin cambios se muestran y no se tocan; hasta 5.000 filas; nonce de un solo uso (`MaterialImportToken`).
25. **Límites**: hasta 200 líneas por documento; código 40, nombre 200, unidad 20, categoría 80, bodega 120, proveedor 200, NIT 40, quién recibe 120, notas y motivos 500.
26. **Navegación**: la pestaña "Materiales" del proyecto (para quien lee costos) y "Materiales" en la barra lateral, sección Gestión (para quien tiene la sección Costos), que abre el catálogo.

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `tearDownModule = teardown_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; `blob_registry()` para `Blob`; las pruebas y los comandos de `ALLOWED` sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`, con un código nuevo en `AuditAction`. Nada de `assert` en producción (bandit B101) ni `import xml.etree`. Ruff a 100 columnas (`E, F, I, UP, B, C4, DJ`; B008: sin llamadas en valores por defecto).
- mypy sin el plugin de Django: en producción, los miembros de `TextChoices` que van a un `str` se envuelven con `str(...)`; en las pruebas, `Any` donde haga falta.
- `core/policies.py` es el único lugar donde se decide quién puede qué. Toda política nueva va en `__all__`, con su `check_*` y en el registro de `core/tests/test_policies.py`.
- Comentarios y textos de interfaz en español; identificadores en inglés. Commits en inglés, estilo conventional, terminados en una línea en blanco y `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La guardia de nombres (`infra/scripts/check_naming.py concept`) rechaza "visit"/"visita" en código y plantillas.
- Si cambia `projects.css`, se sube su `?v=` en `server/suno/core/templates/suno/projects/base.html` (hoy `20261114-contracts-v3`; esta pieza lo deja en `20261115-materials-v1`).
- Todo texto que escribe un usuario y va a un Excel pasa por `safe_cell`.
- Los formularios POST largos, donde `{% csrf_token %}` va en la línea siguiente al `<form>`, llevan el comentario del repositorio: `<!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->` (como `equipment/collection_form.html` y `contracts/amendment_form.html`).
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura. No hay Python local: todo corre en Docker.

## Cómo correr las pruebas

```bash
# Desde C:/Users/gabri/APP/wt-server-materiales, con Docker Desktop arrancado (el contenedor trabaja en server/)
infra/scripts/test_docker.sh suno/materials/tests/test_stock.py       # un archivo
infra/scripts/test_docker.sh suno/materials suno/costs suno/control    # paquetes
infra/scripts/test_docker.sh                                           # suite completa (~1 h)
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                    # al terminar

# Guardia de nombres (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-materiales -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Fallas conocidas, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y `healthcheck.py` en `ruff format --check` (comprobarlo en la Task 0 y anotarlo).

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/suno/core/models/material.py` | Catálogo, bodegas, documentos, líneas, kárdex, saldos, token de importación y sus enumeraciones |
| `server/suno/core/models/cost.py` | `CostEntry.material_purchase`, `.consumption_cut`, `.inventory_adjustment`, `from_document`; dos tipos de documento; restricciones de signo y de origen |
| `server/suno/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/suno/core/migrations/0030_materials.py` | Generada |
| `server/suno/core/policies.py` | `can_read_material_catalog`, `can_manage_material_catalog` y sus `check_*` |
| `server/suno/core/audit.py` | `MATERIAL_*`, `WAREHOUSE_*`, `MATERIAL_PURCHASE_*`, `MATERIAL_ISSUE_*`, `MATERIAL_RETURN_CREATED`, `MATERIAL_TRANSFER_CREATED`, `INVENTORY_ADJUSTMENT_*`, `CONSUMPTION_CUT_*`, `MATERIALS_EXPORTED` |
| `server/suno/costs/services.py` | `check_assignment` (extraída) y `reverse` rechaza documentos de materiales |
| `server/suno/materials/common.py` | `MaterialRejected`, límites, bloqueo del proyecto, consecutivos, estado y decisión, soporte, reversos del libro |
| `server/suno/materials/stock.py` | Promedio ponderado móvil (puro) |
| `server/suno/materials/kardex.py` | `Stock`: bloqueo de saldos, movimientos y saldos en una transacción |
| `server/suno/materials/destination.py` | Destino escrito como texto, validación, clave y etiqueta |
| `server/suno/materials/catalog_services.py`, `catalog_import.py` | Catálogo de la organización e importación desde Excel |
| `server/suno/materials/warehouses.py` | Bodegas |
| `server/suno/materials/purchases.py` | Compras |
| `server/suno/materials/movements.py` | Salidas, devoluciones, traslados y periodo cerrado |
| `server/suno/materials/adjustments.py` | Ajustes por conteo físico |
| `server/suno/materials/cuts.py` | Propuesta y corte de consumos |
| `server/suno/materials/queries.py` | Inventario, kárdex, listas, avisos, bandeja, valor en bodega |
| `server/suno/materials/access.py` | Resolución de proyecto y documentos de la URL (404) |
| `server/suno/materials/forms.py` | Formularios y formsets |
| `server/suno/materials/pdf.py`, `export.py` | PDF del corte y Excel |
| `server/suno/materials/views.py`, `views_purchases.py`, `views_movements.py`, `views_adjustments.py`, `views_cuts.py`, `urls.py` | Pestaña "Materiales" |
| `server/suno/materials/catalog_views.py`, `catalog_urls.py` | Catálogo de la organización |
| `server/config/urls.py` | Monta `plataforma/materiales/` y `plataforma/proyectos/<uuid:project_id>/materiales/` |
| `server/suno/core/templates/suno/materials/*.html` | Plantillas de la pestaña y del catálogo |
| `server/suno/core/templates/suno/projects/_header.html`, `suno/_sidebar.html`, `projects/base.html` | Pestaña, enlace lateral, versión del CSS |
| `server/suno/costs/ledger.py`, `export.py`, `views.py`, `core/templates/suno/costs/{list,detail,inbox}.html` | Orígenes, etiquetas y enlaces; bandeja |
| `server/suno/control/views.py`, `core/templates/suno/control/dashboard.html` | Valor en bodega |
| `server/suno/core/static/suno/css/projects.css` | Estilos `mat-*` |
| `server/suno/management/commands/seed_control_demo.py` | Materiales de ejemplo |
| Pruebas | `core/tests/test_policies.py`; `materials/tests/{__init__,factories,test_models,test_stock,test_kardex,test_destination,test_catalog,test_warehouses,test_purchases,test_movements,test_concurrency,test_adjustments,test_cuts,test_queries,test_views_catalog,test_views,test_views_purchases,test_views_movements,test_views_adjustments,test_views_cuts,test_integration,test_performance}.py`; `control/tests/test_seed_demo.py` (lo de Costos y Control se prueba en `materials/tests/test_integration.py`) |
| `plans/suno-progreso.md` | Registro de avance (Task 21) |

---

### Task 0: Línea base

**Files:** ninguno.

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-materiales status --short --branch`
Expected: `## feat/costos-materiales` sin cambios pendientes; los dos últimos commits son el de este plan y el de la spec (`6573fb6`).

- [ ] **Step 2: Línea base**

```bash
infra/scripts/test_docker.sh 2>&1 | tail -5
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh suno/contracts/tests/test_performance.py suno/control/tests/test_performance.py -s 2>&1 | tail -5
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`; `ruff format --check` solo marca `healthcheck.py`; `No changes detected`; las pruebas de carga pasan. Anotar el número de pruebas y los tiempos de carga (para el registro de avance). No hay commit en esta tarea.

---

### Task 1: Modelos de materiales, migración y datos de prueba

**Files:**
- Create: `server/suno/core/models/material.py`
- Modify: `server/suno/core/models/cost.py`, `server/suno/core/models/__init__.py`
- Create (generada): `server/suno/core/migrations/0030_materials.py`
- Create: `server/suno/materials/__init__.py`, `server/suno/materials/tests/__init__.py` (vacíos), `server/suno/materials/tests/factories.py`
- Test: `server/suno/materials/tests/test_models.py`

- [ ] **Step 1: Paquete y datos de prueba**

Crear vacíos `server/suno/materials/__init__.py` y `server/suno/materials/tests/__init__.py` (como `suno/costs/` y `suno/contracts/`: sin modelos ni `apps.py`; las plantillas van en `core/templates/suno/materials/`).

`server/suno/materials/tests/factories.py` (las tareas 7 y 8 le agregan `stock_in` e `issue`):

```python
"""Datos de prueba de materiales. Las pruebas sí pueden usar `Model.objects`.

Sobre `contracts.tests.factories.contract_world`: presupuesto `samples.V1_ROWS` con AIU (ítems
1.1, 1.2, 2.1, 2.2 y 2.4; capítulos 1 y 2) y cronograma `samples.v1()` (tareas hoja uid 2
"1.1 Localización", 3 "1.2 Descapote" y 5 "2.1 Columnas"). La administración es Gestora de
Costos (señal), la supervisión Revisora; hay un Colaborador y un Consulta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

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
```

- [ ] **Step 2: Pruebas del modelo**

`server/suno/materials/tests/test_models.py`:

```python
"""Restricciones de la base de materiales (spec, 1) y del libro (spec, 1.4)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    ConsumptionCut,
    CostDocumentType,
    CostEntry,
    CostStatus,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    MaterialPurchase,
    MaterialTransfer,
    StockBalance,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import entry, support_blob

from .factories import (
    bare_cut,
    bare_issue,
    draft_purchase,
    make_material,
    make_warehouse,
    material_world,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class CatalogAndStockTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_codes_and_warehouse_names_are_unique_ignoring_case(self) -> None:
        self.broken(lambda: make_material(self.world.org, "cem-01", "Otro", "und"))
        self.broken(lambda: make_warehouse(self.world, "almacén PRINCIPAL"))
        other = material_world("Otra constructora")
        self.assertEqual(other.cement.code, "CEM-01")  # otra organización, mismo código

    def balance(self, **fields: Any) -> StockBalance:
        values: dict[str, Any] = {
            "org": self.world.org,
            "project": self.mw.project,
            "warehouse": self.mw.warehouse,
            "material": self.mw.cement,
        }
        values.update(fields)
        return StockBalance.objects.create(**values)

    def test_a_balance_is_never_negative_nor_has_value_without_quantity(self) -> None:
        for fields in ({"quantity": D("-1")}, {"value": D("-0.01")},
                       {"quantity": D("0"), "value": D("1.00")}):  # fmt: skip
            with self.subTest(fields=fields):
                self.broken(lambda fields=fields: self.balance(**fields))
        self.assertEqual(self.balance(quantity=D("2"), value=D("0.00")).value, D("0.00"))
        self.broken(lambda: self.balance())  # el par (bodega, material) es único

    def movement(self, **fields: Any) -> StockMovement:
        values: dict[str, Any] = {
            "org": self.world.org,
            "project": self.mw.project,
            "warehouse": self.mw.warehouse,
            "material": self.mw.cement,
            "moved_on": date(2026, 3, 2),
            "kind": "purchase",
            "quantity": D("10"),
            "unit_cost": D("100"),
            "value": D("1000.00"),
            "sequence": 1,
            "created_by": self.world.admin,
        }
        values.update(fields)
        return StockMovement.objects.create(**values)

    def test_a_movement_has_the_sign_and_the_origin_of_its_kind(self) -> None:
        purchase = draft_purchase(self.mw)
        issue = bare_issue(self.mw)
        self.movement(purchase=purchase)
        self.broken(lambda: self.movement(purchase=purchase))  # secuencia repetida
        self.broken(lambda: self.movement(sequence=2))  # sin documento de origen
        self.broken(lambda: self.movement(sequence=2, purchase=purchase, issue=issue))
        self.broken(lambda: self.movement(sequence=2, kind="issue", purchase=purchase))
        self.broken(lambda: self.movement(sequence=2, kind="issue", issue=issue))  # entra
        self.movement(sequence=2, kind="issue", issue=issue, quantity=D("-3"),
                      value=D("-300.00"))  # fmt: skip
        self.broken(lambda: self.movement(sequence=3, purchase=purchase, quantity=D("-1"),
                                          value=D("-100.00")))  # fmt: skip


@skip_without_db
class DocumentConstraintTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def broken(self, build: Any) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            build()

    def test_a_purchase_has_a_warehouse_only_when_it_goes_to_one(self) -> None:
        self.broken(lambda: draft_purchase(self.mw, warehouse=None))
        self.broken(lambda: draft_purchase(self.mw, kind="direct"))
        draft_purchase(self.mw, kind="direct", warehouse=None)
        self.broken(lambda: draft_purchase(self.mw, number=1, kind="direct", warehouse=None))

    def test_a_purchase_is_approved_by_someone_else_and_with_support(self) -> None:
        purchase = draft_purchase(self.mw)
        rows = MaterialPurchase.objects.filter(pk=purchase.pk)
        now = timezone.now()
        self.broken(lambda: rows.update(status="approved", decided_by=self.world.supervisor,
                                        decided_at=now))  # sin soporte  # fmt: skip
        self.broken(lambda: rows.update(status="approved", decided_by=self.world.admin,
                                        decided_at=now, attachment=support_blob()))  # fmt: skip
        self.broken(lambda: rows.update(status="rejected", decided_by=self.world.supervisor,
                                        decided_at=now))  # sin motivo  # fmt: skip
        rows.update(status="approved", decided_by=self.world.supervisor, decided_at=now,
                    attachment=support_blob())  # fmt: skip
        self.broken(lambda: rows.update(status="voided"))  # sin quién ni motivo

    def test_only_one_open_cut_per_project_and_its_period_in_order(self) -> None:
        bare_cut(self.mw, number=1, status="approved")
        bare_cut(self.mw, number=2, start=date(2026, 4, 1), end=date(2026, 4, 10))
        self.broken(lambda: bare_cut(self.mw, number=3, status="rejected"))
        self.broken(lambda: bare_cut(self.mw, number=2, status="approved"))
        ConsumptionCut.objects.filter(number=2).delete()
        self.broken(lambda: bare_cut(self.mw, number=2, start=date(2026, 4, 2),
                                     end=date(2026, 4, 1)))  # fmt: skip

    def test_a_transfer_goes_between_two_warehouses(self) -> None:
        def transfer(target: Any) -> MaterialTransfer:
            return MaterialTransfer.objects.create(
                org=self.world.org, project=self.mw.project, number=1, source=self.mw.warehouse,
                target=target, moved_on=date(2026, 3, 5), created_by=self.world.admin,
                updated_by=self.world.admin,
            )  # fmt: skip

        self.broken(lambda: transfer(self.mw.warehouse))
        transfer(make_warehouse(self.world, "Frente norte"))

    def test_an_adjustment_line_keeps_its_difference(self) -> None:
        adjustment = InventoryAdjustment.objects.create(
            org=self.world.org, project=self.mw.project, number=1, warehouse=self.mw.warehouse,
            counted_on=date(2026, 3, 31), scope="indirect", created_by=self.world.admin,
            updated_by=self.world.admin,
        )  # fmt: skip

        def line(**fields: Any) -> InventoryAdjustmentLine:
            values: dict[str, Any] = {
                "org": self.world.org, "adjustment": adjustment, "order": 1,
                "material": self.mw.cement, "expected": D("10"), "counted": D("8"),
                "difference": D("-2"),
            }  # fmt: skip
            values.update(fields)
            return InventoryAdjustmentLine.objects.create(**values)

        self.broken(lambda: line(difference=D("2")))
        self.broken(lambda: line(given_unit_cost=D("0")))
        line()
        self.broken(lambda: line(order=2))  # un material por ajuste


@skip_without_db
class LedgerOriginTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def test_only_a_cut_writes_a_negative_cost(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        rows = CostEntry.objects.filter(pk=cost.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            rows.update(amount=D("-5.00"))
        cut = bare_cut(self.mw, status="approved")
        rows.update(amount=D("-5.00"), consumption_cut=cut)
        with self.assertRaises(IntegrityError), transaction.atomic():
            rows.update(amount=D("0.00"))
        self.assertTrue(CostEntry.objects.get(pk=cost.pk).from_document)

    def test_a_cost_has_at_most_one_origin(self) -> None:
        cost = entry(self.world, status=CostStatus.APPROVED)
        self.assertFalse(cost.from_document)
        purchase = draft_purchase(self.mw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CostEntry.objects.filter(pk=cost.pk).update(
                material_purchase=purchase, consumption_cut=bare_cut(self.mw)
            )
        CostEntry.objects.filter(pk=cost.pk).update(material_purchase=purchase)
        self.assertIn(CostDocumentType.CONSUMPTION_CUT, CostDocumentType.values)
        self.assertIn(CostDocumentType.INVENTORY_COUNT, CostDocumentType.values)
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_models.py`
Expected: ERROR (`cannot import name 'ConsumptionCut' from 'suno.core.models'`).

- [ ] **Step 4: El modelo**

`server/suno/core/models/material.py`:

```python
"""Materiales: catálogo, bodegas, compras, salidas, devoluciones, traslados, ajustes y cortes
de consumos (costos, pieza 6c).

Diseño: plans/2026-11-15-costos-pieza6c-materiales-design.md, sección 1.

- `Material`: catálogo de la organización; el código es único sin distinguir mayúsculas. Un
  material no se borra: se desactiva.
- `Warehouse`: una bodega del proyecto.
- `MaterialPurchase` (CM-001): compra directa a un destino (costo en el libro al aprobarla) o a
  bodega (entra al inventario al aprobarla). Sus líneas llevan el destino solo si es directa.
- `MaterialIssue` (SA-001), `MaterialReturn` (DV-001) y `MaterialTransfer` (TR-001): mueven el
  inventario al guardarse, sin aprobación. Salidas y devoluciones llegan al libro por un corte.
- `InventoryAdjustment` (AJ-001): conteo físico; al aprobarlo, el faltante es costo.
- `ConsumptionCut`: el corte de consumos de un periodo; al aprobarlo escribe el libro.
- `StockMovement` (kárdex) y `StockBalance` (saldo por bodega y material): el saldo es la suma
  de los movimientos de su par y se actualiza en la misma transacción, con la fila bloqueada
  (`suno.materials.kardex`).

El promedio no se guarda: es valor / cantidad del saldo. Valores con IVA incluido. Cantidades
con 4 decimales, valores a centavos y costo unitario con 6 decimales.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel
from .cost import CostDocumentType, CostScope


class MaterialStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"
    VOIDED = "voided", "Anulado"


class PurchaseKind(models.TextChoices):
    DIRECT = "direct", "Directa a un destino"
    WAREHOUSE = "warehouse", "A bodega"


class MovementKind(models.TextChoices):
    PURCHASE = "purchase", "Compra"
    ISSUE = "issue", "Salida"
    RETURN = "return", "Devolución"
    TRANSFER_OUT = "transfer_out", "Traslado que sale"
    TRANSFER_IN = "transfer_in", "Traslado que entra"
    ADJUSTMENT_IN = "adjustment_in", "Sobrante de ajuste"
    ADJUSTMENT_OUT = "adjustment_out", "Faltante de ajuste"
    PURCHASE_VOID = "purchase_void", "Anulación de compra"
    ADJUSTMENT_VOID = "adjustment_void", "Anulación de ajuste"


#: Tipos que solo entran o solo salen; la anulación de un ajuste va en los dos sentidos.
ENTRY_KINDS = ("purchase", "return", "transfer_in", "adjustment_in")
EXIT_KINDS = ("issue", "transfer_out", "adjustment_out", "purchase_void")
#: El campo del documento de origen de cada grupo de tipos.
ORIGINS = (
    (("purchase", "purchase_void"), "purchase"),
    (("issue",), "issue"),
    (("return",), "material_return"),
    (("transfer_out", "transfer_in"), "transfer"),
    (("adjustment_in", "adjustment_out", "adjustment_void"), "adjustment"),
)
MOVEMENT_ORIGINS = tuple(field for _, field in ORIGINS)


def _decided_by_someone_else() -> models.Q:
    return ~models.Q(decided_by=models.F("created_by")) & ~models.Q(
        decided_by=models.F("updated_by")
    )


def _document_constraints(prefix: str) -> list[models.BaseConstraint]:
    """Consecutivo, decisión, autoaprobación con soporte, motivo y anulación de una compra, un
    ajuste o un corte."""
    return [
        models.UniqueConstraint(fields=["project", "number"], name=f"{prefix}_number_uq"),
        models.CheckConstraint(
            condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
            | models.Q(
                status__in=["approved", "rejected", "voided"],
                decided_by__isnull=False,
                decided_at__isnull=False,
            ),
            name=f"{prefix}_decision_pair",
        ),
        models.CheckConstraint(
            condition=~models.Q(status__in=["approved", "voided"])
            | (models.Q(attachment__isnull=False) & _decided_by_someone_else()),
            name=f"{prefix}_approval_ok",
        ),
        models.CheckConstraint(
            condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
            name=f"{prefix}_rejection_note",
        ),
        models.CheckConstraint(
            condition=~models.Q(status="voided")
            | (models.Q(voided_by__isnull=False, voided_at__isnull=False) & ~models.Q(void_note="")),
            name=f"{prefix}_void_note",
        ),
    ]


def _destination_check(name: str, *, optional: bool = False) -> models.CheckConstraint:
    """La misma forma del destino de la 6a (`cost_entry_scope_fields`); `optional`: una línea de
    compra a bodega no lleva destino (`scope` vacío)."""
    condition = (
        models.Q(scope="indirect", chapter__isnull=True, task__isnull=True)
        | models.Q(scope="item")
        | models.Q(scope__in=["chapter", "selection"], chapter__isnull=False)
    )
    if optional:
        condition |= models.Q(scope="", chapter__isnull=True, task__isnull=True)
    return models.CheckConstraint(condition=condition, name=name)


def _one_origin() -> models.Q:
    """Exactamente un documento de origen, y el que corresponde al tipo del movimiento."""
    condition = models.Q()
    for kinds, field in ORIGINS:
        nulls = {f"{other}__isnull": other != field for other in MOVEMENT_ORIGINS}
        condition |= models.Q(kind__in=list(kinds), **nulls)
    return condition


class Authored(OrgScopedModel):
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="registró"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="modificó"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Decided(Authored):
    """Lo que se aprueba uno por uno: estado, decisión, anulación y soporte."""

    status = models.CharField(
        max_length=10,
        choices=MaterialStatus.choices,
        default=MaterialStatus.DRAFT,
        verbose_name="estado",
    )
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
    voided_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="anuló",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="soporte",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")

    class Meta:
        abstract = True


class Destined(models.Model):
    """El destino de la 6a: `scope`, capítulo, ítems (M2M en cada modelo) y tarea opcional."""

    scope = models.CharField(
        max_length=12, choices=CostScope.choices, blank=True, verbose_name="destino"
    )
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

    class Meta:
        abstract = True


class Material(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=40, verbose_name="código")
    name = models.CharField(max_length=200, verbose_name="nombre")
    unit = models.CharField(max_length=20, verbose_name="unidad")
    category = models.CharField(max_length=80, blank=True, verbose_name="categoría")
    active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "material"
        ordering = ["code"]
        verbose_name = "material"
        verbose_name_plural = "materiales"
        constraints = [
            models.UniqueConstraint(Lower("code"), "org", name="material_org_code_ci_uq"),
        ]
        indexes = [models.Index(fields=["org", "category"], name="material_category_idx")]

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}"

    def __str__(self) -> str:
        return self.label


class Warehouse(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="warehouses", verbose_name="proyecto"
    )
    name = models.CharField(max_length=120, verbose_name="nombre")
    active = models.BooleanField(default=True, verbose_name="activa")

    class Meta:
        db_table = "warehouse"
        ordering = ["name"]
        verbose_name = "bodega"
        verbose_name_plural = "bodegas"
        constraints = [
            models.UniqueConstraint(Lower("name"), "project", name="warehouse_project_name_ci_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class MaterialPurchase(Decided):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_purchases",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    kind = models.CharField(max_length=10, choices=PurchaseKind.choices, verbose_name="tipo")
    warehouse = models.ForeignKey(
        Warehouse,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purchases",
        verbose_name="bodega",
    )
    supplier = models.CharField(max_length=200, verbose_name="proveedor")
    supplier_tax_id = models.CharField(max_length=40, blank=True, verbose_name="NIT")
    document_type = models.CharField(
        max_length=24, choices=CostDocumentType.choices, verbose_name="tipo de documento"
    )
    document_number = models.CharField(max_length=80, blank=True, verbose_name="número")
    purchased_on = models.DateField(verbose_name="fecha")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total"
    )

    class Meta:
        db_table = "material_purchase"
        ordering = ["-number"]
        verbose_name = "compra de materiales"
        verbose_name_plural = "compras de materiales"
        constraints = [
            *_document_constraints("material_purchase"),
            models.CheckConstraint(
                condition=models.Q(kind="warehouse", warehouse__isnull=False)
                | models.Q(kind="direct", warehouse__isnull=True),
                name="material_purchase_kind_warehouse",
            ),
        ]
        indexes = [
            models.Index(fields=["org", "project", "status"], name="material_purchase_status_idx")
        ]

    @property
    def code(self) -> str:
        return f"CM-{self.number:03d}"

    def __str__(self) -> str:
        return self.code


class MaterialPurchaseLine(OrgScopedModel, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase = models.ForeignKey(
        MaterialPurchase, on_delete=models.CASCADE, related_name="lines", verbose_name="compra"
    )
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="purchase_lines", verbose_name="material"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_price = models.DecimalField(
        max_digits=18, decimal_places=2, verbose_name="valor unitario"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="MaterialPurchaseLineItem",
        related_name="material_purchase_lines",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "material_purchase_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["purchase", "order"], name="material_purchase_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_price__gt=0, amount__gt=0),
                name="material_purchase_line_values_ok",
            ),
            _destination_check("material_purchase_line_destination", optional=True),
        ]


class MaterialPurchaseLineItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(
        MaterialPurchaseLine, on_delete=models.CASCADE, related_name="item_links"
    )
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "material_purchase_line_item"
        constraints = [
            models.UniqueConstraint(fields=["line", "item"], name="material_purchase_line_item_uq"),
        ]


class ConsumptionCut(Decided):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="consumption_cuts",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    period_start = models.DateField(verbose_name="desde")
    period_end = models.DateField(verbose_name="hasta")
    #: `fingerprint` (lo que se compara al aprobar) y `groups` (el detalle que imprime el PDF),
    #: guardados al crear o recalcular el borrador (`suno.materials.cuts.Proposal.snapshot`).
    summary = models.JSONField(default=dict, blank=True, verbose_name="resumen")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="total"
    )
    issue_count = models.PositiveIntegerField(default=0, verbose_name="salidas")
    return_count = models.PositiveIntegerField(default=0, verbose_name="devoluciones")

    class Meta:
        db_table = "consumption_cut"
        ordering = ["-number"]
        verbose_name = "corte de consumos"
        verbose_name_plural = "cortes de consumos"
        constraints = [
            *_document_constraints("consumption_cut"),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="consumption_cut_period_order",
            ),
            models.UniqueConstraint(
                fields=["project"],
                condition=models.Q(status__in=["draft", "rejected"]),
                name="consumption_cut_one_open_uq",
            ),
        ]

    @property
    def code(self) -> str:
        return f"Corte {self.number}"

    def __str__(self) -> str:
        return self.code


class MaterialIssue(Authored, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_issues",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="issues", verbose_name="bodega"
    )
    issued_on = models.DateField(verbose_name="fecha")
    received_by = models.CharField(max_length=120, blank=True, verbose_name="recibe")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    #: Generado por el cliente (la futura captura móvil): un reintento no duplica la salida.
    client_id = models.UUIDField(null=True, blank=True, unique=True)
    cut = models.ForeignKey(
        ConsumptionCut,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="issues",
        verbose_name="corte",
    )
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="MaterialIssueItem",
        related_name="material_issues",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "material_issue"
        ordering = ["-issued_on", "-number"]
        verbose_name = "salida de materiales"
        verbose_name_plural = "salidas de materiales"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="material_issue_number_uq"),
            _destination_check("material_issue_destination"),
        ]
        indexes = [
            models.Index(
                fields=["project", "issued_on"],
                condition=models.Q(cut__isnull=True),
                name="material_issue_uncut_idx",
            ),
            models.Index(fields=["warehouse", "issued_on"], name="material_issue_warehouse_idx"),
        ]

    @property
    def code(self) -> str:
        return f"SA-{self.number:03d}"

    def __str__(self) -> str:
        return self.code


class MaterialIssueItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="item_links")
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "material_issue_item"
        constraints = [
            models.UniqueConstraint(fields=["issue", "item"], name="material_issue_item_uq"),
        ]


class MaterialIssueLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Fijos al registrar la salida (el promedio de ese momento).
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_issue_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["issue", "order"], name="material_issue_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_issue_line_values_ok",
            ),
        ]


class MaterialReturn(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_returns",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    #: La salida de origen: de ella salen la bodega y el destino.
    issue = models.ForeignKey(
        MaterialIssue, on_delete=models.PROTECT, related_name="returns", verbose_name="salida"
    )
    returned_on = models.DateField(verbose_name="fecha")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    cut = models.ForeignKey(
        ConsumptionCut,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="returns",
        verbose_name="corte",
    )
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )

    class Meta:
        db_table = "material_return"
        ordering = ["-returned_on", "-number"]
        verbose_name = "devolución de materiales"
        verbose_name_plural = "devoluciones de materiales"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="material_return_number_uq"),
        ]
        indexes = [
            models.Index(
                fields=["project", "returned_on"],
                condition=models.Q(cut__isnull=True),
                name="material_return_uncut_idx",
            ),
        ]

    @property
    def code(self) -> str:
        return f"DV-{self.number:03d}"

    def __str__(self) -> str:
        return self.code


class MaterialReturnLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material_return = models.ForeignKey(
        MaterialReturn, on_delete=models.CASCADE, related_name="lines"
    )
    issue_line = models.ForeignKey(
        MaterialIssueLine, on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_return_line"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_return_line_values_ok",
            ),
        ]


class MaterialTransfer(Authored):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="material_transfers",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    source = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="sale de"
    )
    target = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="entra a"
    )
    moved_on = models.DateField(verbose_name="fecha")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )

    class Meta:
        db_table = "material_transfer"
        ordering = ["-moved_on", "-number"]
        verbose_name = "traslado de materiales"
        verbose_name_plural = "traslados de materiales"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "number"], name="material_transfer_number_uq"
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")),
                name="material_transfer_two_warehouses",
            ),
        ]

    @property
    def code(self) -> str:
        return f"TR-{self.number:03d}"

    def __str__(self) -> str:
        return self.code


class MaterialTransferLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(MaterialTransfer, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "material_transfer_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["transfer", "order"], name="material_transfer_line_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gte=0, amount__gte=0),
                name="material_transfer_line_values_ok",
            ),
        ]


class InventoryAdjustment(Decided, Destined):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.PROTECT,
        related_name="inventory_adjustments",
        verbose_name="proyecto",
    )
    number = models.PositiveIntegerField(verbose_name="número")
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name="adjustments", verbose_name="bodega"
    )
    counted_on = models.DateField(verbose_name="fecha del conteo")
    notes = models.CharField(max_length=500, blank=True, verbose_name="notas")
    #: Fijados al aprobar.
    shortage_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="faltantes"
    )
    surplus_total = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="sobrantes"
    )
    items = models.ManyToManyField(
        "core.BudgetItem",
        through="InventoryAdjustmentItem",
        related_name="inventory_adjustments",
        verbose_name="ítems",
    )

    class Meta:
        db_table = "inventory_adjustment"
        ordering = ["-number"]
        verbose_name = "ajuste de inventario"
        verbose_name_plural = "ajustes de inventario"
        constraints = [
            *_document_constraints("inventory_adjustment"),
            _destination_check("inventory_adjustment_destination"),
        ]

    @property
    def code(self) -> str:
        return f"AJ-{self.number:03d}"

    def __str__(self) -> str:
        return self.code


class InventoryAdjustmentItem(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adjustment = models.ForeignKey(
        InventoryAdjustment, on_delete=models.CASCADE, related_name="item_links"
    )
    item = models.ForeignKey("core.BudgetItem", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "inventory_adjustment_item"
        constraints = [
            models.UniqueConstraint(
                fields=["adjustment", "item"], name="inventory_adjustment_item_uq"
            ),
        ]


class InventoryAdjustmentLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    adjustment = models.ForeignKey(
        InventoryAdjustment, on_delete=models.CASCADE, related_name="lines"
    )
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")
    #: El saldo al guardar el borrador; al aprobar tiene que seguir igual.
    expected = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="esperado")
    counted = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="contado")
    difference = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="diferencia")
    #: El costo unitario que escribe el usuario para un sobrante sin saldo ni entradas previas.
    given_unit_cost = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="costo dado"
    )
    #: Fijados al aprobar.
    unit_cost = models.DecimalField(
        max_digits=22, decimal_places=6, null=True, blank=True, verbose_name="costo unitario"
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="valor"
    )

    class Meta:
        db_table = "inventory_adjustment_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["adjustment", "order"], name="inventory_adjustment_line_uq"
            ),
            models.UniqueConstraint(
                fields=["adjustment", "material"], name="inventory_adjustment_material_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    expected__gte=0,
                    counted__gte=0,
                    difference=models.F("counted") - models.F("expected"),
                ),
                name="inventory_adjustment_line_difference",
            ),
            models.CheckConstraint(
                condition=models.Q(given_unit_cost__isnull=True)
                | models.Q(given_unit_cost__gt=0),
                name="inventory_adjustment_line_given_cost",
            ),
        ]


class StockBalance(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: Repetido a propósito (la bodega ya lo dice): el inventario del proyecto sin otra unión.
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="stock_balances"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="balances")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="balances")
    quantity = models.DecimalField(
        max_digits=18, decimal_places=4, default=Decimal("0"), verbose_name="cantidad"
    )
    value = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), verbose_name="valor"
    )
    #: La secuencia del último movimiento del par: la del siguiente es esta + 1.
    last_sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stock_balance"
        verbose_name = "saldo de inventario"
        verbose_name_plural = "saldos de inventario"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "material"], name="stock_balance_uq"),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0, value__gte=0),
                name="stock_balance_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0) | models.Q(value=0),
                name="stock_balance_empty_has_no_value",
            ),
        ]
        indexes = [models.Index(fields=["project", "material"], name="stock_balance_project_idx")]


class StockMovement(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="stock_movements"
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="movements")
    moved_on = models.DateField(verbose_name="fecha")
    kind = models.CharField(max_length=16, choices=MovementKind.choices, verbose_name="tipo")
    #: Con signo: positiva si entra, negativa si sale.
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    unit_cost = models.DecimalField(max_digits=22, decimal_places=6, verbose_name="costo unitario")
    value = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")
    #: Creciente por bodega y material: da el orden del kárdex.
    sequence = models.PositiveIntegerField(verbose_name="secuencia")
    created_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    purchase = models.ForeignKey(
        MaterialPurchase, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    issue = models.ForeignKey(
        MaterialIssue, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    material_return = models.ForeignKey(
        MaterialReturn, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    transfer = models.ForeignKey(
        MaterialTransfer, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    adjustment = models.ForeignKey(
        InventoryAdjustment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )

    class Meta:
        db_table = "stock_movement"
        ordering = ["warehouse", "material", "sequence"]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "material", "sequence"], name="stock_movement_sequence_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=[*ENTRY_KINDS, "adjustment_void"], quantity__gt=0, value__gte=0
                )
                | models.Q(kind__in=[*EXIT_KINDS, "adjustment_void"], quantity__lt=0, value__lte=0),
                name="stock_movement_sign",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost__gte=0), name="stock_movement_unit_cost_ok"
            ),
            models.CheckConstraint(condition=_one_origin(), name="stock_movement_one_origin"),
        ]
        indexes = [
            models.Index(
                fields=["warehouse", "material", "moved_on"], name="stock_movement_kardex_day_idx"
            ),
            models.Index(fields=["project", "moved_on"], name="stock_movement_project_day_idx"),
        ]


class MaterialImportToken(OrgScopedModel):
    """Una vista previa de importación del catálogo ya cargada (como `CostImportToken`): su
    `nonce` va firmado en la vista previa y es único."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nonce = models.CharField(max_length=64, unique=True)
    applied_by = models.ForeignKey("core.AppUser", on_delete=models.PROTECT, related_name="+")
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "material_import_token"
        verbose_name = "importación de materiales aplicada"
        verbose_name_plural = "importaciones de materiales aplicadas"
```

- [ ] **Step 5: El libro conoce los documentos de materiales**

En `server/suno/core/models/cost.py`:

1. Agregar `from itertools import combinations` a los imports.
2. En `CostDocumentType`, antes de `OTHER`:

```python
    CONSUMPTION_CUT = "consumption_cut", "Corte de consumos"
    INVENTORY_COUNT = "inventory_count", "Inventario físico"
```

3. Antes de `class CostEntry`:

```python
#: Los documentos que escriben movimientos del libro (6b y 6c). Un movimiento con uno de ellos
#: no se edita ni se reversa desde el libro: se anula el documento.
ORIGIN_FIELDS = ("certificate", "material_purchase", "consumption_cut", "inventory_adjustment")


def _at_most_one_origin() -> models.Q:
    condition = models.Q()
    for first, second in combinations(ORIGIN_FIELDS, 2):
        condition &= models.Q(**{f"{first}__isnull": True}) | models.Q(
            **{f"{second}__isnull": True}
        )
    return condition
```

4. Después del campo `certificate`:

```python
    #: Pieza 6c: la compra directa, el corte de consumos o el ajuste de inventario que lo
    #: generó. Tampoco se reversan desde el libro: se anula el documento.
    material_purchase = models.ForeignKey(
        "core.MaterialPurchase",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="compra",
    )
    consumption_cut = models.ForeignKey(
        "core.ConsumptionCut",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="corte",
    )
    inventory_adjustment = models.ForeignKey(
        "core.InventoryAdjustment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="ajuste",
    )
```

5. Reemplazar la restricción `cost_entry_amount_sign` y agregar la de origen al final de `constraints`:

```python
            # Pieza 6c: un corte escribe el neto de un destino, que puede ser negativo (se
            # devolvió en el periodo material que salió en un corte anterior).
            models.CheckConstraint(
                condition=models.Q(reverses__isnull=True, amount__gt=0)
                | models.Q(reverses__isnull=False, amount__lt=0)
                | (models.Q(consumption_cut__isnull=False) & ~models.Q(amount=0)),
                name="cost_entry_amount_sign",
            ),
```

```python
            models.CheckConstraint(
                condition=_at_most_one_origin(), name="cost_entry_single_origin"
            ),
```

6. Después de `__str__` de `CostEntry`:

```python
    @property
    def from_document(self) -> bool:
        """Viene de un acta, una compra, un corte o un ajuste."""
        return any(getattr(self, f"{field}_id") is not None for field in ORIGIN_FIELDS)
```

Y actualizar el docstring del módulo: "- `CostEntry` … El acta (6b) y la compra, el corte o el ajuste (6c) que lo generaron se guardan como origen."

- [ ] **Step 6: Reexportar**

En `server/suno/core/models/__init__.py`, en el docstring agregar la línea `- \`material\` — catálogo, bodegas, compras, salidas, devoluciones, traslados, ajustes, cortes y kárdex (costos, pieza 6c).`; después del bloque `from .location import …` (orden alfabético de módulos):

```python
from .material import (
    ConsumptionCut,
    InventoryAdjustment,
    InventoryAdjustmentItem,
    InventoryAdjustmentLine,
    Material,
    MaterialImportToken,
    MaterialIssue,
    MaterialIssueItem,
    MaterialIssueLine,
    MaterialPurchase,
    MaterialPurchaseLine,
    MaterialPurchaseLineItem,
    MaterialReturn,
    MaterialReturnLine,
    MaterialStatus,
    MaterialTransfer,
    MaterialTransferLine,
    MovementKind,
    PurchaseKind,
    StockBalance,
    StockMovement,
    Warehouse,
)
```

y los mismos 22 nombres en `__all__`, en su lugar alfabético.

- [ ] **Step 7: Generar la migración**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name materials"
```

Expected: `server/suno/core/migrations/0030_materials.py` con `dependencies = [('core', '0029_contracts')]`, los `CreateModel` de las 19 tablas, los tres `AddField` de `costentry`, el `AlterField` de `document_type`, `RemoveConstraint`/`AddConstraint` de `cost_entry_amount_sign` y el `AddConstraint` de `cost_entry_single_origin`. Revisarla: sin `RunPython` y sin la palabra del renombre.

- [ ] **Step 8: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_models.py suno/costs/tests/test_models.py suno/contracts/tests/test_models.py`
Expected: PASS.

- [ ] **Step 9: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/core/models suno/materials && ruff check suno/core/models suno/materials && python manage.py makemigrations --check --dry-run"
git add server/suno/core/models server/suno/core/migrations/0030_materials.py server/suno/materials
git commit -m "feat(materials): catalog, warehouses, documents, stock ledger and balances models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Permisos del catálogo y acciones de auditoría

**Files:**
- Modify: `server/suno/core/policies.py`, `server/suno/core/audit.py`
- Test: `server/suno/core/tests/test_policies.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/core/tests/test_policies.py`, en `_registry()` de `NoRoleGrantsNothingTests`, después de `"can_delete_cost": cost_entry(),`:

```python
            # Costos, pieza 6c: catálogo de materiales de la organización.
            "can_read_material_catalog": ORG,
            "can_manage_material_catalog": ORG,
```

Y dentro de `CostPolicyTests`, después de `test_contracts_use_the_cost_policies`:

```python
    def test_material_catalog(self) -> None:
        """Costos, pieza 6c (decisión 2 del plan): lo ve cualquier rol de Costos con cualquier
        alcance; lo gestiona quien crea y actualiza con alcance de toda la organización."""
        cases = {
            SectionRole.MANAGER: True,
            SectionRole.REVIEWER: True,
            SectionRole.CONTRIBUTOR: True,
            SectionRole.VIEWER: False,
        }
        for role, manages in cases.items():
            actor = self.actor(role)
            with self.subTest(role=role):
                self.assertTrue(policies.can_read_material_catalog(actor, ORG))
                self.assertEqual(policies.can_manage_material_catalog(actor, ORG), manages)
        limited = self.actor(
            SectionRole.MANAGER, scope=AccessScope.PROJECTS, projects=frozenset({uuid.uuid4()})
        )
        self.assertTrue(policies.can_read_material_catalog(limited, ORG))
        self.assertFalse(policies.can_manage_material_catalog(limited, ORG))
        self.assertFalse(
            policies.can_read_material_catalog(self.actor(SectionRole.MANAGER), OTHER_ORG)
        )
        self.assertFalse(policies.can_read_material_catalog(ctx(Role.SUPERVISOR), ORG))
        with self.assertRaises(HttpError) as denied:
            policies.check_manage_material_catalog(self.actor(SectionRole.VIEWER), ORG)
        self.assertEqual(denied.exception.status_code, 403)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/core/tests/test_policies.py`
Expected: FAIL (`module 'suno.core.policies' has no attribute 'can_read_material_catalog'`, y el registro ya no cubre el módulo).

- [ ] **Step 3: Las políticas**

En `server/suno/core/policies.py`:

1. El import de acceso queda `from suno.core.access import Capability, Section, role_has_capability`.
2. En `__all__`, en su lugar alfabético: `"can_manage_material_catalog"`, `"can_read_material_catalog"`, `"check_manage_material_catalog"`, `"check_read_material_catalog"`.
3. Después de `can_delete_cost`:

```python
def can_read_material_catalog(ctx: RequestContext, org_id: UUID) -> bool:
    """Ver el catálogo de materiales de la organización (costos, pieza 6c): cualquier rol de la
    sección Costos, con cualquier alcance (los formularios de cada proyecto lo usan)."""
    access = ctx.access_for(Section.COSTS)
    return (
        ctx.org_id == org_id
        and ctx.is_member
        and access is not None
        and role_has_capability(access.section, access.role, Capability.READ)
    )


def can_manage_material_catalog(ctx: RequestContext, org_id: UUID) -> bool:
    """Crear, editar, desactivar e importar materiales: `create` y `update` de Costos con
    alcance de toda la organización. El catálogo es de todos los proyectos: un acceso limitado
    a algunos no basta (sin `project_id`, `has_capability` solo lo concede a ese alcance)."""
    return user_has_section_capability(
        ctx, org_id, Section.COSTS, Capability.CREATE
    ) and user_has_section_capability(ctx, org_id, Section.COSTS, Capability.UPDATE)
```

4. Después de `check_delete_cost`:

```python
def check_read_material_catalog(ctx: RequestContext, org_id: UUID) -> None:
    if not can_read_material_catalog(ctx, org_id):
        raise _denial(ctx, org_id)


def check_manage_material_catalog(ctx: RequestContext, org_id: UUID) -> None:
    if not can_manage_material_catalog(ctx, org_id):
        raise _denial(ctx, org_id)
```

- [ ] **Step 4: Las acciones de auditoría**

En `server/suno/core/audit.py`, dentro de `AuditAction`, después de `CERTIFICATE_EXPORTED`:

```python
    # Materiales (costos, pieza 6c). `metadata.project` en todos los del proyecto.
    MATERIAL_CREATED = "material.created"
    MATERIAL_UPDATED = "material.updated"
    MATERIAL_IMPORTED = "material.imported"
    WAREHOUSE_CREATED = "warehouse.created"
    WAREHOUSE_UPDATED = "warehouse.updated"
    MATERIAL_PURCHASE_CREATED = "material_purchase.created"
    MATERIAL_PURCHASE_UPDATED = "material_purchase.updated"
    MATERIAL_PURCHASE_DELETED = "material_purchase.deleted"
    MATERIAL_PURCHASE_APPROVED = "material_purchase.approved"
    MATERIAL_PURCHASE_REJECTED = "material_purchase.rejected"
    MATERIAL_PURCHASE_VOIDED = "material_purchase.voided"
    MATERIAL_ISSUE_CREATED = "material_issue.created"
    MATERIAL_ISSUE_UPDATED = "material_issue.updated"
    MATERIAL_ISSUE_DELETED = "material_issue.deleted"
    MATERIAL_RETURN_CREATED = "material_return.created"
    MATERIAL_TRANSFER_CREATED = "material_transfer.created"
    INVENTORY_ADJUSTMENT_CREATED = "inventory_adjustment.created"
    INVENTORY_ADJUSTMENT_UPDATED = "inventory_adjustment.updated"
    INVENTORY_ADJUSTMENT_DELETED = "inventory_adjustment.deleted"
    INVENTORY_ADJUSTMENT_APPROVED = "inventory_adjustment.approved"
    INVENTORY_ADJUSTMENT_REJECTED = "inventory_adjustment.rejected"
    INVENTORY_ADJUSTMENT_VOIDED = "inventory_adjustment.voided"
    CONSUMPTION_CUT_CREATED = "consumption_cut.created"
    CONSUMPTION_CUT_UPDATED = "consumption_cut.updated"
    CONSUMPTION_CUT_DELETED = "consumption_cut.deleted"
    CONSUMPTION_CUT_APPROVED = "consumption_cut.approved"
    CONSUMPTION_CUT_REJECTED = "consumption_cut.rejected"
    CONSUMPTION_CUT_VOIDED = "consumption_cut.voided"
    CONSUMPTION_CUT_EXPORTED = "consumption_cut.exported"
    MATERIALS_EXPORTED = "materials.exported"
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/core/tests/test_policies.py suno/core/tests/test_audit.py`
Expected: PASS.

- [ ] **Step 6: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/core && ruff check suno/core"
git add server/suno/core/policies.py server/suno/core/audit.py server/suno/core/tests/test_policies.py
git commit -m "feat(materials): organization catalog policies and audit actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Promedio ponderado móvil (cálculo puro)

**Files:**
- Create: `server/suno/materials/stock.py`
- Test: `server/suno/materials/tests/test_stock.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_stock.py`:

```python
"""Promedio ponderado móvil (spec, 2.1 y 4 "Promedio móvil"): puro, sin base de datos."""

from __future__ import annotations

import unittest
from decimal import Decimal

from suno.materials.stock import (
    Balance,
    Shortage,
    apply,
    entry,
    exit_at_average,
    returned_value,
    unit_cost,
    withdraw,
)

D = Decimal
EMPTY = Balance(D("0"), D("0.00"))


class MovingAverageTests(unittest.TestCase):
    def test_purchases_at_different_prices_move_the_average(self) -> None:
        balance = apply(EMPTY, entry(D("10"), D("1000000.00")))
        balance = apply(balance, entry(D("20"), D("2200000.00")))
        self.assertEqual(balance, Balance(D("30"), D("3200000.00")))
        self.assertEqual(balance.average, D("106666.666667"))
        self.assertIsNone(EMPTY.average)

    def test_each_exit_is_rounded_to_cents_and_the_last_takes_the_rest(self) -> None:
        balance = Balance(D("30"), D("3200000.00"))
        first = exit_at_average(balance, D("7"))
        self.assertEqual(
            (first.quantity, first.value, first.unit_cost),
            (D("-7"), D("-746666.67"), D("106666.666667")),
        )
        balance = apply(balance, first)
        self.assertEqual(balance, Balance(D("23"), D("2453333.33")))
        last = exit_at_average(balance, D("23"))
        self.assertEqual(last.value, D("-2453333.33"))
        self.assertEqual(apply(balance, last), Balance(D("0"), D("0.00")))

    def test_an_exit_never_takes_more_value_than_the_balance_has(self) -> None:
        balance = Balance(D("3000000"), D("2000000.00"))  # unitario 0,666667 (redondeado arriba)
        move = exit_at_average(balance, D("2999999"))
        self.assertEqual(move.value, D("-2000000.00"))  # 2.000.000,33 recortado
        self.assertEqual(apply(balance, move), Balance(D("1"), D("0.00")))

    def test_negative_stock_is_refused(self) -> None:
        with self.assertRaises(Shortage) as caught:
            exit_at_average(Balance(D("3"), D("300.00")), D("3.0001"))
        self.assertEqual(caught.exception.available, D("3"))
        with self.assertRaises(Shortage):
            withdraw(Balance(D("3"), D("300.00")), D("4"), D("400.00"))

    def test_a_withdrawal_takes_its_value_without_leaving_the_balance_negative(self) -> None:
        balance = Balance(D("10"), D("505.00"))
        self.assertEqual(withdraw(balance, D("5"), D("250.00")).value, D("-250.00"))
        self.assertEqual(withdraw(balance, D("5"), D("1000.00")).value, D("-505.00"))
        self.assertEqual(withdraw(balance, D("10"), D("1000.00")).value, D("-505.00"))

    def test_a_return_enters_at_the_cost_of_its_line_without_leftover_cents(self) -> None:
        unit = D("106666.666667")
        first = returned_value(D("7"), D("746666.67"), unit, D("0"), D("0.00"), D("3"))
        self.assertEqual(first, D("320000.00"))
        rest = returned_value(D("7"), D("746666.67"), unit, D("3"), first, D("4"))
        self.assertEqual(first + rest, D("746666.67"))

    def test_unit_cost_has_six_decimals(self) -> None:
        self.assertEqual(unit_cost(D("3"), D("2.00")), D("0.666667"))
        self.assertEqual(unit_cost(D("0"), D("0.00")), D("0.000000"))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_stock.py`
Expected: ERROR (`No module named 'suno.materials.stock'`).

- [ ] **Step 3: El cálculo**

`server/suno/materials/stock.py`:

```python
"""Promedio ponderado móvil por material y bodega (costos, pieza 6c, spec 2.1). Puro: sin
base de datos (el motor con bloqueo es `kardex`).

- Entrada (compra, sobrante, devolución, traslado que entra): suma cantidad y valor.
- Salida (salida, faltante, traslado que sale): costo unitario = valor / cantidad del saldo;
  valor = cantidad × unitario a centavos, sin pasar del valor del saldo; si se lleva toda la
  cantidad, exactamente el valor restante. Sin inventario negativo.
- Retiro por anulación: por el valor con que entró, entre cero y el valor del saldo.

Cantidades con 4 decimales, valores a centavos, costo unitario con 6 decimales.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

from suno.budgets.totals import ZERO, item_total

UNIT_STEP = Decimal("0.000001")
_WIDE = 60


class Shortage(Exception):
    """El saldo no alcanza; `available` es lo que hay."""

    def __init__(self, available: Decimal) -> None:
        self.available = available
        super().__init__(str(available))


@dataclass(frozen=True)
class Balance:
    quantity: Decimal
    value: Decimal

    @property
    def average(self) -> Decimal | None:
        return unit_cost(self.quantity, self.value) if self.quantity > 0 else None


@dataclass(frozen=True)
class Move:
    """Lo que un movimiento hace al saldo: cantidad y valor con signo, y su costo unitario."""

    quantity: Decimal
    value: Decimal
    unit_cost: Decimal


def unit_cost(quantity: Decimal, value: Decimal) -> Decimal:
    if quantity <= 0:
        return Decimal("0.000000")
    with localcontext() as context:
        context.prec = _WIDE
        return (value / quantity).quantize(UNIT_STEP, rounding=ROUND_HALF_UP)


def apply(balance: Balance, move: Move) -> Balance:
    return Balance(balance.quantity + move.quantity, balance.value + move.value)


def entry(quantity: Decimal, value: Decimal) -> Move:
    return Move(quantity, value, unit_cost(quantity, value))


def exit_at_average(balance: Balance, quantity: Decimal) -> Move:
    if quantity > balance.quantity:
        raise Shortage(balance.quantity)
    unit = unit_cost(balance.quantity, balance.value)
    if quantity == balance.quantity:
        value = balance.value
    else:
        value = min(item_total(quantity, unit), balance.value)
    return Move(-quantity, -value, unit)


def withdraw(balance: Balance, quantity: Decimal, value: Decimal) -> Move:
    if quantity > balance.quantity:
        raise Shortage(balance.quantity)
    taken = balance.value if quantity == balance.quantity else min(max(value, ZERO), balance.value)
    return Move(-quantity, -taken, unit_cost(quantity, taken))


def returned_value(
    issued_quantity: Decimal,
    issued_amount: Decimal,
    unit: Decimal,
    already_quantity: Decimal,
    already_value: Decimal,
    quantity: Decimal,
) -> Decimal:
    """Valor de una devolución al costo unitario de la línea de salida. La que completa lo que
    salió se lleva lo que falta del valor de la línea (sin residuos de centavos)."""
    left = issued_amount - already_value
    if already_quantity + quantity == issued_quantity:
        return left
    return min(item_total(quantity, unit), left)
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_stock.py`
Expected: PASS (7 pruebas).

- [ ] **Step 5: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials"
git add server/suno/materials/stock.py server/suno/materials/tests/test_stock.py
git commit -m "feat(materials): moving weighted average arithmetic

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Piezas comunes, saldos y kárdex con bloqueo de fila

**Files:**
- Create: `server/suno/materials/common.py`, `server/suno/materials/kardex.py`
- Modify: `server/suno/materials/tests/factories.py` (`balance_of`, `assert_consistent`)
- Test: `server/suno/materials/tests/test_kardex.py`

- [ ] **Step 1: Ayudas de prueba**

En `server/suno/materials/tests/factories.py`, agregar `import unittest` y `from django.db.models import Sum` a los imports, `StockBalance` y `StockMovement` al de `suno.core.models`, y al final:

```python
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
        test.assertEqual(
            (row.quantity, row.value), (sums["q"] or D("0"), sums["v"] or D("0.00"))
        )
        last = moves.order_by("-sequence").values_list("sequence", flat=True).first()
        test.assertGreaterEqual(row.last_sequence, last or 0)
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_kardex.py`:

```python
"""Saldos y kárdex (spec, 1.3 y 2.1): bloqueo, secuencia, promedio y deshacer."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.test import TestCase

from suno.core.models import MovementKind, StockBalance, StockMovement
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected
from suno.materials.kardex import Stock, lock_balances

from .factories import (
    assert_consistent,
    balance_of,
    bare_issue,
    draft_purchase,
    make_warehouse,
    material_world,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
DAY = date(2026, 3, 2)
IN, OUT = str(MovementKind.PURCHASE), str(MovementKind.ISSUE)


@skip_without_db
class StockTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.ctx = self.mw.ctx()
        self.purchase = draft_purchase(self.mw)
        self.issue = bare_issue(self.mw)
        self.pair = (self.mw.warehouse.pk, self.mw.sand.pk)

    def stock(self) -> Stock:
        return Stock(self.ctx, self.mw.project, [self.pair])

    def test_entries_and_exits_at_the_moving_average(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("10"), D("1000000.00"), DAY, purchase=self.purchase)
            stock.add(IN, *self.pair, D("20"), D("2200000.00"), DAY, purchase=self.purchase)
            out = stock.take(OUT, *self.pair, D("7"), DAY, issue=self.issue)
            stock.save()
        self.assertEqual((out.unit_cost, out.value), (D("106666.666667"), D("-746666.67")))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("23"), D("2453333.33")))
        self.assertEqual(
            list(StockMovement.objects.order_by("sequence").values_list("sequence", flat=True)),
            [1, 2, 3],
        )
        self.assertEqual(StockBalance.objects.get().last_sequence, 3)
        assert_consistent(self, self.mw)

    def test_taking_everything_leaves_exactly_zero(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("30"), D("3200000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("7"), DAY, issue=self.issue)
            stock.take(OUT, *self.pair, D("23"), DAY, issue=self.issue)
            stock.save()
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("0"), D("0.00")))
        assert_consistent(self, self.mw)

    def test_a_shortage_is_refused_with_the_quantity_unit_and_warehouse(self) -> None:
        with self.assertRaises(MaterialRejected) as caught, transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("3"), D("300000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("5"), DAY, issue=self.issue)
        self.assertEqual(
            caught.exception.errors, ["Solo hay 3 m3 de Arena de río en Almacén principal."]
        )
        self.assertFalse(StockMovement.objects.exists())

    def test_restore_gives_back_what_the_movements_took_and_deletes_them(self) -> None:
        with transaction.atomic():
            stock = self.stock()
            stock.add(IN, *self.pair, D("10"), D("1000000.00"), DAY, purchase=self.purchase)
            stock.take(OUT, *self.pair, D("4"), DAY, issue=self.issue)
            stock.save()
        taken = list(StockMovement.objects.filter(issue=self.issue))
        with transaction.atomic():
            stock = self.stock()
            stock.restore(taken)
            stock.save()
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("10"), D("1000000.00")))
        self.assertFalse(StockMovement.objects.filter(issue=self.issue).exists())
        self.assertEqual(StockBalance.objects.get().last_sequence, 2)
        assert_consistent(self, self.mw)

    def test_lock_creates_the_missing_balances_once(self) -> None:
        north = make_warehouse(self.mw.world, "Frente norte")
        pairs = [(north.pk, self.mw.cement.pk), self.pair, self.pair]
        with transaction.atomic():
            first = lock_balances(self.ctx, self.mw.project, pairs)
        with transaction.atomic():
            again = lock_balances(self.ctx, self.mw.project, pairs)
        self.assertEqual(set(first), {(north.pk, self.mw.cement.pk), self.pair})
        self.assertEqual({row.pk for row in first.values()}, {row.pk for row in again.values()})
        self.assertEqual(StockBalance.objects.count(), 2)
        self.assertEqual(lock_balances(self.ctx, self.mw.project, []), {})
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_kardex.py`
Expected: ERROR (`No module named 'suno.materials.common'`).

- [ ] **Step 4: Las piezas comunes**

`server/suno/materials/common.py`:

```python
"""Piezas compartidas de los servicios de materiales (costos, pieza 6c).

- Toda escritura bloquea primero la fila del proyecto (`lock_project`: `FOR NO KEY UPDATE`,
  que no frena a quien solo inserta filas que apuntan al proyecto), después el documento y al
  final los saldos (`kardex`). Así se serializan consecutivos, periodo cerrado y corte, y el
  orden de bloqueo es siempre proyecto → documento → saldos.
- Nadie aprueba ni rechaza lo que creó o editó por última vez (también la base lo exige).
- Los reversos del libro llevan la fecha de hoy y el mismo origen que el movimiento reversado.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import money, quantity
from suno.core.context import RequestContext
from suno.core.models import Blob, CostEntry, CostEntryItem, CostStatus, Project
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import can_read_costs
from suno.core.scoping import blob_registry, scoped_qs

FIRST_DAY = date(2000, 1, 1)
MAX_QUANTITY = Decimal("99999999999999.9999")
MAX_PRICE = Decimal("9999999999999999.99")
MAX_LINES = 200
MAX_NOTE = 500
MAX_FILE_NAME = 200
MAX_DESCRIPTION = 300
MAX_THIRD_PARTY = 200
MAX_DOCUMENT_NUMBER = 80
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."


class MaterialRejected(Exception):
    """No se puede guardar; `errors` son mensajes para el usuario."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "Rechazado")


def lock_project(ctx: RequestContext, project: Project) -> None:
    found = (
        scoped_qs(ctx, Project)
        .select_for_update(no_key=True)
        .filter(pk=project.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if found is None:
        raise Http404


def next_number(ctx: RequestContext, model: type[models.Model], project: Project) -> int:
    """El consecutivo siguiente del proyecto (con el proyecto ya bloqueado)."""
    last = scoped_qs(ctx, model).filter(project=project).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


def locked[M: models.Model](ctx: RequestContext, model: type[M], project: Project, pk: UUID) -> M:
    found = scoped_qs(ctx, model).select_for_update().filter(pk=pk, project=project).first()
    if found is None:
        raise Http404
    return found


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise MaterialRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise MaterialRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un borrador o un rechazado; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise MaterialRejected(["Solo se edita o se borra un borrador o un rechazado."])
    if obj.status == "rejected" and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != "draft":
        raise MaterialRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise MaterialRejected([SELF_DECISION])


def quantity_ok(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and 0 < value <= MAX_QUANTITY
        and quantity(value) == value
    )


def price_ok(value: Decimal | None) -> bool:
    return (
        value is not None and value.is_finite() and 0 < value <= MAX_PRICE and money(value) == value
    )


def day_ok(day: date, today: date) -> bool:
    return FIRST_DAY <= day <= today


def stored_upload(upload: Upload | None) -> Blob | None:
    """Sube el soporte (antes de la transacción: no se tiene el bloqueo mientras se escribe)."""
    if upload is None:
        return None
    try:
        return store_upload(upload)
    except UploadRejected as exc:
        raise MaterialRejected([str(exc)]) from exc


def attachment_url(ctx: RequestContext, project: Project, obj: Any) -> str:
    """URL prefirmada del soporte **después** de comprobar que se leen los costos del proyecto
    (el `Blob` no tiene organización), como `costs.services.support_url`."""
    if not can_read_costs(ctx, project) or obj.project_id != project.pk:
        raise Http404
    blob = blob_registry().filter(pk=obj.attachment_id).first() if obj.attachment_id else None
    if blob is None:
        raise Http404
    return get_blob_store().presign_download(
        sha256=blob.sha256,
        expires_in=int(settings.ATTACHMENT_DOWNLOAD_URL_TTL_SECONDS),
        mime=blob.mime,
        filename=validation.generated_filename(blob.mime),
    )


def reverse_entries(
    ctx: RequestContext, project: Project, field: str, document: Any, note: str
) -> int:
    """Reversa con fecha de hoy y el motivo los movimientos del libro que escribió el documento
    (`field`: su origen en `CostEntry`), como al anular un acta (6b). Devuelve cuántos."""
    today, now = timezone.localdate(), timezone.now()
    originals = list(
        scoped_qs(ctx, CostEntry)
        .filter(**{field: document}, reverses__isnull=True)
        .prefetch_related("item_links")
    )
    reversals = [
        CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=today,
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
            decided_at=now,
            decision_note=note,
            reverses=original,
            **{field: document},
        )
        for original in originals
    ]
    scoped_qs(ctx, CostEntry).bulk_create(reversals)
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [
            CostEntryItem(org_id=ctx.org_id, entry=reversal, item_id=link.item_id)
            for reversal, original in zip(reversals, originals, strict=True)
            for link in original.item_links.all()
        ]
    )
    return len(reversals)
```

- [ ] **Step 5: El motor de saldos**

`server/suno/materials/kardex.py`:

```python
"""Saldos y kárdex (costos, pieza 6c, spec 1.3 y 2.1).

Toda operación que mueve inventario crea un `Stock` con los pares (bodega, material) que toca:
bloquea sus saldos con `select_for_update` en orden de (bodega, material) —dos operaciones
nunca se bloquean cruzadas—, calcula cada movimiento sobre el saldo en memoria (`stock`) y, al
final, `save()` escribe los movimientos y los saldos en la misma transacción. El saldo es
siempre la suma de los movimientos de su par; la secuencia del par da el orden del kárdex.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from functools import reduce
from operator import or_
from typing import Any
from uuid import UUID

from django.db.models import Q

from suno.core.context import RequestContext
from suno.core.models import Project, StockBalance, StockMovement
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty

from . import stock
from .common import MaterialRejected

Pair = tuple[UUID, UUID]


def lock_balances(
    ctx: RequestContext, project: Project, pairs: Iterable[Pair]
) -> dict[Pair, StockBalance]:
    """Los saldos de los pares, bloqueados en orden. Los que no existen se crean antes en una
    sola sentencia (`ON CONFLICT DO NOTHING`: dos operaciones que crean el mismo par no
    chocan). Los pares tienen que ser de bodegas del proyecto (lo comprueba quien llama)."""
    wanted = sorted(set(pairs), key=lambda pair: (str(pair[0]), str(pair[1])))
    if not wanted:
        return {}
    scoped_qs(ctx, StockBalance).bulk_create(
        [
            StockBalance(org_id=ctx.org_id, project=project, warehouse_id=w, material_id=m)
            for w, m in wanted
        ],
        ignore_conflicts=True,
    )
    rows = (
        scoped_qs(ctx, StockBalance)
        .select_for_update(of=("self",))
        .select_related("warehouse", "material")
        .filter(reduce(or_, (Q(warehouse_id=w, material_id=m) for w, m in wanted)))
        .order_by("warehouse_id", "material_id")
    )
    return {(row.warehouse_id, row.material_id): row for row in rows}


class Stock:
    def __init__(self, ctx: RequestContext, project: Project, pairs: Iterable[Pair]) -> None:
        self.ctx = ctx
        self.project = project
        self._rows = lock_balances(ctx, project, pairs)
        self._new: list[StockMovement] = []
        self._gone: list[UUID] = []

    def balance(self, warehouse_id: UUID, material_id: UUID) -> stock.Balance:
        row = self._rows[(warehouse_id, material_id)]
        return stock.Balance(row.quantity, row.value)

    def shortage(self, warehouse_id: UUID, material_id: UUID, available: Decimal) -> str:
        row = self._rows[(warehouse_id, material_id)]
        return (
            f"Solo hay {qty(available) or '0'} {row.material.unit} de {row.material.name} "
            f"en {row.warehouse.name}."
        )

    def _post(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        move: stock.Move,
        day: date,
        origin: dict[str, Any],
    ) -> StockMovement:
        row = self._rows[(warehouse_id, material_id)]
        row.quantity += move.quantity
        row.value += move.value
        row.last_sequence += 1
        movement = StockMovement(
            org_id=self.ctx.org_id,
            project=self.project,
            warehouse_id=warehouse_id,
            material_id=material_id,
            moved_on=day,
            kind=kind,
            quantity=move.quantity,
            unit_cost=move.unit_cost,
            value=move.value,
            sequence=row.last_sequence,
            created_by_id=self.ctx.user_id,
            **origin,
        )
        self._new.append(movement)
        return movement

    def add(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        value: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Una entrada por su valor."""
        move = stock.entry(quantity, value)
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def take(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Una salida al promedio; si no alcanza, `MaterialRejected` con lo que hay."""
        try:
            move = stock.exit_at_average(self.balance(warehouse_id, material_id), quantity)
        except stock.Shortage as exc:
            raise MaterialRejected(
                [self.shortage(warehouse_id, material_id, exc.available)]
            ) from exc
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def withdraw(
        self,
        kind: str,
        warehouse_id: UUID,
        material_id: UUID,
        quantity: Decimal,
        value: Decimal,
        day: date,
        **origin: Any,
    ) -> StockMovement:
        """Un retiro por anulación, por el valor con que entró (sin dejar el saldo negativo)."""
        try:
            move = stock.withdraw(self.balance(warehouse_id, material_id), quantity, value)
        except stock.Shortage as exc:
            raise MaterialRejected(
                [self.shortage(warehouse_id, material_id, exc.available)]
            ) from exc
        return self._post(kind, warehouse_id, material_id, move, day, origin)

    def restore(self, movements: Sequence[StockMovement]) -> None:
        """Deshace movimientos (los de una salida que se edita o se borra): el saldo recupera lo
        que se llevaron y los movimientos se borran al guardar."""
        for movement in movements:
            row = self._rows[(movement.warehouse_id, movement.material_id)]
            row.quantity -= movement.quantity
            row.value -= movement.value
            self._gone.append(movement.pk)

    def save(self) -> list[StockMovement]:
        if self._gone:
            scoped_qs(self.ctx, StockMovement).filter(pk__in=self._gone).delete()
        created = scoped_qs(self.ctx, StockMovement).bulk_create(self._new)
        scoped_qs(self.ctx, StockBalance).bulk_update(
            list(self._rows.values()), ["quantity", "value", "last_sequence"]
        )
        self._new, self._gone = [], []
        return created
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_kardex.py`
Expected: PASS (5 pruebas).

- [ ] **Step 7: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): locked stock balances and kardex movements

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Destino de los documentos (y `check_assignment` en la 6a)

**Files:**
- Modify: `server/suno/costs/services.py` (extraer `check_assignment`)
- Create: `server/suno/materials/destination.py`
- Test: `server/suno/materials/tests/test_destination.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_destination.py`:

```python
"""El destino escrito como texto (decisión 4 del plan) y validado como en la 6a."""

from __future__ import annotations

import unittest
from typing import Any

from django.test import TestCase

from suno.core.models import CostScope, MaterialIssueItem
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.costs.tests.factories import item
from suno.materials.destination import (
    DestinationData,
    check_destination,
    destination_key,
    destination_label,
    destination_options,
    destination_text,
    link_items,
    parse_destination,
)

from .factories import bare_issue, material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class DestinationTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.ctx = self.mw.ctx()
        self.catalog = load_catalog(self.ctx, self.mw.project)
        self.tasks = assignable_tasks(self.ctx, self.mw.project)
        budget = self.mw.cw.budget
        self.concrete, self.steel = item(budget, "2.1"), item(budget, "2.2")
        self.structure = item(budget, "2")

    def parsed(self, text: str) -> Any:
        data, error = parse_destination(text, self.catalog)
        self.assertEqual(error, "", text)
        return data

    def test_texts_that_name_a_destination(self) -> None:
        indirect = DestinationData(str(CostScope.INDIRECT))
        self.assertEqual(self.parsed(""), indirect)
        self.assertEqual(self.parsed(" indirecto "), indirect)
        item_only = DestinationData(str(CostScope.ITEM), (self.concrete.pk,))
        self.assertEqual(self.parsed("2.1 Concreto zapatas"), item_only)
        self.assertEqual(self.parsed("2.1"), item_only)
        chapter = DestinationData(str(CostScope.CHAPTER), chapter_id=self.structure.pk)
        for text in ("2", "Capítulo 2", "capítulo 2 Estructura"):
            with self.subTest(text=text):
                self.assertEqual(self.parsed(text), chapter)
        self.assertEqual(
            self.parsed("2.1, 2.2"),
            DestinationData(
                str(CostScope.SELECTION), (self.concrete.pk, self.steel.pk), self.structure.pk
            ),
        )

    def test_texts_that_do_not(self) -> None:
        for text, error in (
            ("9.9", "«9.9» no es un ítem ni un capítulo del presupuesto vigente."),
            ("2, 2.1", "Una selección lleva solo ítems (no capítulos), separados por comas."),
            ("Capítulo 9", "No hay un capítulo «9» en la versión vigente del presupuesto."),
            (",", "Escribe el destino: un ítem, «Capítulo …» o varios códigos separados por comas."),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_destination(text, self.catalog), (None, error))

    def test_the_check_is_the_one_of_the_ledger(self) -> None:
        found = check_destination(self.catalog, self.tasks, self.parsed("2.1, 1.1"))
        self.assertEqual(found, (["Una selección lleva dos o más ítems del mismo capítulo."], None))
        task = self.mw.world.task(5)
        errors, assignment = check_destination(
            self.catalog,
            self.tasks,
            DestinationData(str(CostScope.ITEM), (self.concrete.pk,), task_id=task.pk),
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            (assignment.chapter_id, assignment.task_id),  # type: ignore[union-attr]
            (self.structure.pk, task.pk),
        )
        errors, _ = check_destination(
            self.catalog, self.tasks, DestinationData(str(CostScope.INDIRECT), task_id=task.pk)
        )
        self.assertEqual(errors, ["Un costo indirecto va al proyecto: sin ítem, capítulo ni tarea."])

    def test_labels_texts_and_keys(self) -> None:
        item_scope, sel = str(CostScope.ITEM), str(CostScope.SELECTION)
        chapter_scope = str(CostScope.CHAPTER)
        self.assertEqual(
            destination_label(self.catalog, str(CostScope.INDIRECT), None, ()),
            "Indirecto (proyecto)",
        )
        self.assertEqual(
            destination_label(
                self.catalog, item_scope, self.structure.pk, (self.concrete.pk,), "2.1 Columnas"
            ),
            "2.1 Concreto zapatas · tarea 2.1 Columnas",
        )
        self.assertEqual(
            destination_label(self.catalog, chapter_scope, self.structure.pk, ()),
            "Capítulo 2 Estructura",
        )
        pair = (self.steel.pk, self.concrete.pk)
        self.assertEqual(
            destination_label(self.catalog, sel, self.structure.pk, pair),
            "Ítems 2.2, 2.1 de 2 Estructura",
        )
        self.assertEqual(destination_text(self.catalog, sel, self.structure.pk, pair), "2.2, 2.1")
        self.assertEqual(
            destination_text(self.catalog, chapter_scope, self.structure.pk, ()), "Capítulo 2"
        )
        self.assertEqual(
            destination_key(sel, self.structure.pk, pair, None),
            destination_key(sel, self.structure.pk, pair[::-1], None),
        )
        options = destination_options(self.catalog)
        self.assertEqual(options[:2], ["Indirecto", "Capítulo 1 Preliminares"])
        self.assertIn("2.1 Concreto zapatas", options)

    def test_link_items_replaces_the_links(self) -> None:
        issue = bare_issue(self.mw)
        link_items(self.ctx, MaterialIssueItem, "issue", issue, [self.concrete.pk, self.steel.pk])
        link_items(self.ctx, MaterialIssueItem, "issue", issue, [self.steel.pk])
        self.assertEqual(
            list(MaterialIssueItem.objects.filter(issue=issue).values_list("item_id", flat=True)),
            [self.steel.pk],
        )
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_destination.py`
Expected: ERROR (`No module named 'suno.materials.destination'`).

- [ ] **Step 3: Extraer `check_assignment` en la 6a**

En `server/suno/costs/services.py`, reemplazar la función `_assignment` completa por las dos siguientes (las reglas y los mensajes no cambian):

```python
def check_assignment(
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    *,
    scope: str,
    item_ids: Sequence[UUID] = (),
    chapter_id: UUID | None = None,
    task_id: UUID | None = None,
) -> tuple[list[str], Assignment | None]:
    """La asignación validada contra la versión vigente. También la usan los destinos de los
    documentos de materiales (6c)."""
    if scope not in CostScope.values:
        return ["Elige a qué se asigna el costo."], None
    if scope == CostScope.INDIRECT:
        if item_ids or chapter_id or task_id:
            return ["Un costo indirecto va al proyecto: sin ítem, capítulo ni tarea."], None
        return [], Assignment(scope)
    errors: list[str] = []
    if task_id is not None and task_id not in tasks:
        errors.append("La tarea no es una tarea vigente del proyecto.")
    if scope == CostScope.ITEM:
        row = catalog.rows.get(item_ids[0]) if len(item_ids) == 1 else None
        if row is None or row.kind != BudgetRowKind.ITEM:
            errors.append(
                "Elige un ítem que esté como ítem en la versión vigente de su presupuesto."
            )
            return errors, None
        return errors, Assignment(scope, (row.item_id,), row.parent_id, task_id)
    chapter = catalog.rows.get(chapter_id) if chapter_id is not None else None
    if chapter is None or chapter.kind != BudgetRowKind.CHAPTER:
        errors.append("Elige un capítulo de la versión vigente del presupuesto.")
        return errors, None
    if scope == CostScope.CHAPTER:
        if item_ids:
            errors.append("Un capítulo completo no lleva ítems sueltos.")
        return errors, Assignment(scope, (), chapter.item_id, task_id)
    picked = tuple(dict.fromkeys(item_ids))
    inside = catalog.descendants(chapter.item_id)
    if len(picked) < 2 or any(item_id not in inside for item_id in picked):
        errors.append("Una selección lleva dos o más ítems del mismo capítulo.")
        return errors, None
    return errors, Assignment(scope, picked, chapter.item_id, task_id)


def _assignment(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: EntryData
) -> tuple[list[str], Assignment | None]:
    return check_assignment(
        catalog,
        tasks,
        scope=data.scope,
        item_ids=data.item_ids,
        chapter_id=data.chapter_id,
        task_id=data.task_id,
    )
```

- [ ] **Step 4: El destino**

`server/suno/materials/destination.py`:

```python
"""Destino de un documento de materiales (costos, pieza 6c, spec 1.2 "Destino").

Es el de la 6a —indirecto, un ítem, un capítulo completo o ítems de un capítulo, y la tarea
opcional— y se valida con `costs.services.check_assignment`. Sin JavaScript se escribe en un
solo campo de texto (decisión 4 del plan, `parse_destination`):

- vacío o "Indirecto": el proyecto;
- la etiqueta o el código de un ítem ("2.1 Concreto zapatas", "2.1");
- un capítulo: su código, o "Capítulo 2" (también con la etiqueta, "Capítulo 2 Estructura");
- varios códigos de ítems del mismo capítulo separados por comas ("2.1, 2.2").

La tarea va aparte. Los ítems se guardan en una tabla de enlaces por documento.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db import models

from suno.core.context import RequestContext
from suno.core.models import BudgetRowKind, CostScope, Task
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, CatalogRow
from suno.costs.services import Assignment, check_assignment

INDIRECT_TEXT = "Indirecto"
INDIRECT_LABEL = "Indirecto (proyecto)"
CHAPTER_PREFIX = "Capítulo "
EMPTY_SELECTION = "Escribe el destino: un ítem, «Capítulo …» o varios códigos separados por comas."
_SPLIT = re.compile(r"\s*[,;]\s*")


@dataclass(frozen=True)
class DestinationData:
    scope: str
    item_ids: tuple[UUID, ...] = ()
    chapter_id: UUID | None = None
    task_id: UUID | None = None


INDIRECT = DestinationData(str(CostScope.INDIRECT))
#: (scope, capítulo, ítems ordenados, tarea): agrupa los consumos de un corte.
DestinationKey = tuple[str, UUID | None, tuple[UUID, ...], UUID | None]


def _chapter_id(catalog: Catalog, text: str) -> UUID | None:
    wanted = text.strip().casefold()
    for row in catalog.chapters():
        if wanted in (row.code.casefold(), row.label.casefold()):
            return row.item_id
    return None


def parse_destination(
    text: str, catalog: Catalog, task_id: UUID | None = None
) -> tuple[DestinationData | None, str]:
    """El destino escrito y, si no se entiende, el error para el usuario."""
    value = text.strip()
    if not value or value.casefold() in (INDIRECT_TEXT.casefold(), INDIRECT_LABEL.casefold()):
        return DestinationData(str(CostScope.INDIRECT), task_id=task_id), ""
    row = catalog.by_label(value)
    if row is not None:
        return DestinationData(str(CostScope.ITEM), (row.item_id,), task_id=task_id), ""
    if value.casefold().startswith(CHAPTER_PREFIX.casefold()):
        name = value[len(CHAPTER_PREFIX) :].strip()
        chapter_id = _chapter_id(catalog, name)
        if chapter_id is None:
            return None, f"No hay un capítulo «{name}» en la versión vigente del presupuesto."
        chapter = DestinationData(str(CostScope.CHAPTER), chapter_id=chapter_id, task_id=task_id)
        return chapter, ""
    rows: list[CatalogRow] = []
    for code in filter(None, _SPLIT.split(value)):
        found = catalog.by_code(code)
        if not found:
            return None, f"«{code}» no es un ítem ni un capítulo del presupuesto vigente."
        if len(found) > 1:
            return None, (
                f"El código «{code}» está en más de un presupuesto: elige el ítem de la lista."
            )
        rows.append(found[0])
    if not rows:
        return None, EMPTY_SELECTION
    if len(rows) == 1:
        only = rows[0]
        if only.kind == BudgetRowKind.CHAPTER:
            scope = str(CostScope.CHAPTER)
            return DestinationData(scope, chapter_id=only.item_id, task_id=task_id), ""
        return DestinationData(str(CostScope.ITEM), (only.item_id,), task_id=task_id), ""
    if any(row.kind != BudgetRowKind.ITEM for row in rows):
        return None, "Una selección lleva solo ítems (no capítulos), separados por comas."
    item_ids = tuple(dict.fromkeys(row.item_id for row in rows))
    return DestinationData(str(CostScope.SELECTION), item_ids, rows[0].parent_id, task_id), ""


def check_destination(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: DestinationData
) -> tuple[list[str], Assignment | None]:
    return check_assignment(
        catalog,
        tasks,
        scope=data.scope,
        item_ids=data.item_ids,
        chapter_id=data.chapter_id,
        task_id=data.task_id,
    )


def destination_fields(assignment: Assignment) -> dict[str, Any]:
    return {
        "scope": assignment.scope,
        "chapter_id": assignment.chapter_id,
        "task_id": assignment.task_id,
    }


def destination_key(
    scope: str, chapter_id: UUID | None, item_ids: Sequence[UUID], task_id: UUID | None
) -> DestinationKey:
    return (scope, chapter_id, tuple(sorted(item_ids, key=str)), task_id)


def key_text(key: DestinationKey) -> str:
    scope, chapter_id, item_ids, task_id = key
    return "|".join(
        (scope, str(chapter_id or ""), ",".join(str(i) for i in item_ids), str(task_id or ""))
    )


def task_label(task: Task | None) -> str:
    return f"{task.outline_number} {task.name}" if task is not None else ""


def destination_label(
    catalog: Catalog,
    scope: str,
    chapter_id: UUID | None,
    item_ids: Sequence[UUID],
    task: str = "",
) -> str:
    """"2.1 Concreto zapatas · tarea 2.1 Columnas", "Capítulo 2 Estructura", "Ítems 2.1, 2.2 de
    2 Estructura" o "Indirecto (proyecto)"."""
    if scope == CostScope.INDIRECT:
        text = INDIRECT_LABEL
    elif scope == CostScope.ITEM:
        text = catalog.label_of(item_ids[0], "—") if item_ids else "—"
    elif scope == CostScope.CHAPTER:
        text = f"Capítulo {catalog.label_of(chapter_id, '—')}"
    else:
        codes = ", ".join(catalog.rows[i].code if i in catalog.rows else "—" for i in item_ids)
        text = f"Ítems {codes} de {catalog.label_of(chapter_id, '—')}"
    return f"{text} · tarea {task}" if task else text


def destination_text(
    catalog: Catalog, scope: str, chapter_id: UUID | None, item_ids: Sequence[UUID]
) -> str:
    """Lo que hay que escribir en el campo para volver a elegir el destino (al editar)."""
    if scope == CostScope.ITEM and item_ids:
        return catalog.label_of(item_ids[0])
    if scope == CostScope.CHAPTER and chapter_id is not None and chapter_id in catalog.rows:
        return f"{CHAPTER_PREFIX}{catalog.rows[chapter_id].code}"
    if scope == CostScope.SELECTION:
        return ", ".join(catalog.rows[i].code for i in item_ids if i in catalog.rows)
    return ""


def destination_options(catalog: Catalog) -> list[str]:
    """Las sugerencias del `datalist`: "Indirecto", los capítulos y los ítems."""
    return [
        INDIRECT_TEXT,
        *(f"{CHAPTER_PREFIX}{row.label}" for row in catalog.chapters()),
        *(row.label for row in catalog.items()),
    ]


def link_items(
    ctx: RequestContext,
    model: type[models.Model],
    owner_field: str,
    owner: Any,
    item_ids: Sequence[UUID],
) -> None:
    """Reemplaza los ítems enlazados de un documento o una línea."""
    scoped_qs(ctx, model).filter(**{owner_field: owner}).delete()
    scoped_qs(ctx, model).bulk_create(
        [model(org_id=ctx.org_id, item_id=item_id, **{owner_field: owner}) for item_id in item_ids]
    )
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_destination.py suno/costs`
Expected: PASS (los de `suno/costs` siguen verdes: la extracción no cambió nada).

- [ ] **Step 6: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials suno/costs && ruff check suno/materials suno/costs && mypy suno/materials suno/costs --config-file pyproject.toml"
git add server/suno/costs/services.py server/suno/materials
git commit -m "feat(materials): destinations written as text, validated like the cost ledger

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Catálogo de materiales: servicios e importación desde Excel

**Files:**
- Create: `server/suno/materials/catalog_services.py`, `server/suno/materials/catalog_import.py`
- Test: `server/suno/materials/tests/test_catalog.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_catalog.py`:

```python
"""Catálogo de la organización (spec, 1.1 y 3.1): servicios, permisos e importación."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase

from suno.backoffice.export import to_xlsx_book
from suno.core.access import AccessScope, SectionRole
from suno.core.models import AuditEvent, Material, MovementKind
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs
from suno.materials import catalog_import
from suno.materials.catalog_services import MaterialData, create_material, update_material
from suno.materials.common import MaterialRejected
from suno.materials.kardex import Stock

from .factories import draft_purchase, material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
HEADER = ["Código", "Nombre", "Unidad", "Categoría"]


def workbook(rows: Sequence[Sequence[Any]], header: Sequence[str] = HEADER) -> bytes:
    return to_xlsx_book([("Materiales", list(header), [list(row) for row in rows])])


@skip_without_db
class CatalogServiceTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world
        self.admin = self.mw.ctx()

    def test_create_and_update_a_material(self) -> None:
        brick = create_material(self.admin, MaterialData(" LAD-01 ", "Ladrillo tolete", "und"))
        self.assertEqual((brick.code, brick.active), ("LAD-01", True))
        self.assertTrue(AuditEvent.objects.filter(action="material.created").exists())
        updated = update_material(
            self.admin,
            brick.pk,
            MaterialData("LAD-01", "Ladrillo tolete macizo", "und", "Mampostería", False),
        )
        self.assertEqual((updated.name, updated.category, updated.active),
                         ("Ladrillo tolete macizo", "Mampostería", False))  # fmt: skip

    def test_codes_are_unique_ignoring_case_and_fields_are_required(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            create_material(self.admin, MaterialData("cem-01", "Otro", "und"))
        self.assertEqual(caught.exception.errors, ["Ya hay un material con el código cem-01."])
        with self.assertRaises(MaterialRejected) as caught:
            create_material(self.admin, MaterialData(" ", "", "x" * 21))
        self.assertEqual(
            caught.exception.errors,
            ["Escribe el código.", "Escribe el nombre.", "La unidad pasa de 20 caracteres."],
        )
        with self.assertRaises(MaterialRejected):
            update_material(self.admin, self.mw.sand.pk, MaterialData("CEM-01", "Arena", "m3"))

    def test_the_unit_of_a_material_with_movements_does_not_change(self) -> None:
        purchase = draft_purchase(self.mw)
        with transaction.atomic():
            stock = Stock(self.admin, self.mw.project, [(self.mw.warehouse.pk, self.mw.cement.pk)])
            stock.add(str(MovementKind.PURCHASE), self.mw.warehouse.pk, self.mw.cement.pk,
                      D("1"), D("1.00"), date(2026, 3, 2), purchase=purchase)  # fmt: skip
            stock.save()
        with self.assertRaises(MaterialRejected) as caught:
            update_material(self.admin, self.mw.cement.pk, MaterialData("CEM-01", "Cemento", "kg"))
        self.assertEqual(
            caught.exception.errors,
            ["CEM-01 ya tiene movimientos: su unidad no se cambia (crea otro material)."],
        )
        update_material(self.admin, self.mw.cement.pk, MaterialData("CEM-01", "Cemento", "und"))

    def test_who_manages_the_catalog(self) -> None:
        data = MaterialData("LAD-01", "Ladrillo", "und")
        create_material(self.mw.ctx(self.mw.cw.contributor), data)  # Colaborador de toda la org.
        with self.assertRaises(PermissionDenied):
            create_material(self.mw.ctx(self.mw.cw.viewer), MaterialData("X-1", "X", "und"))
        grant_costs(self.world, self.world.supervisor, SectionRole.MANAGER,
                    scope=AccessScope.PROJECTS, projects=[self.mw.project])  # fmt: skip
        with self.assertRaises(PermissionDenied):
            create_material(self.mw.ctx(self.world.supervisor), MaterialData("X-2", "X", "und"))


@skip_without_db
class CatalogImportTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.admin = self.mw.ctx()

    def test_preview_classifies_and_apply_creates_and_updates_once(self) -> None:
        result = catalog_import.preview(
            self.admin,
            workbook([
                ["CEM-01", "Cemento gris 50 kg", "und", "Cementos"],
                ["are-01", "Arena lavada", "m3", "Agregados"],
                ["LAD-01", "Ladrillo tolete", "und", "Mampostería"],
                ["", "", "", ""],
            ]),
        )  # fmt: skip
        self.assertEqual(result.errors, ())
        self.assertEqual([(row.line, row.action) for row in result.rows],
                         [(2, "sin cambios"), (3, "actualiza"), (4, "nuevo")])  # fmt: skip
        self.assertEqual(catalog_import.apply(self.admin, result.token), (1, 1))
        self.assertEqual(Material.objects.get(code="ARE-01").name, "Arena lavada")
        self.assertEqual(Material.objects.filter(org=self.mw.world.org).count(), 4)
        with self.assertRaises(MaterialRejected) as caught:
            catalog_import.apply(self.admin, result.token)
        self.assertEqual(caught.exception.errors, [catalog_import.ALREADY_APPLIED])
        self.assertTrue(AuditEvent.objects.filter(action="material.imported").exists())

    def test_errors_of_the_file(self) -> None:
        result = catalog_import.preview(
            self.admin,
            workbook([["LAD-01", "Ladrillo", "und", ""], ["lad-01", "Ladrillo 2", "und", ""],
                      ["BLO-01", "Bloque", "", ""], ["CEM-01", "Cemento", "kg", ""]]),
        )  # fmt: skip
        self.assertEqual(
            result.errors,
            (
                "Fila 3: El código «lad-01» se repite (fila 2).",
                "Fila 4: Escribe la unidad (por ejemplo m3, kg o und).",
            ),
        )
        self.assertEqual(result.token, "")
        missing = catalog_import.preview(self.admin, workbook([], header=["Código", "Nombre"]))
        self.assertEqual(missing.errors, ("Falta la columna «Unidad».",))

    def test_only_its_author_applies_a_preview(self) -> None:
        result = catalog_import.preview(self.admin, workbook([["LAD-01", "Ladrillo", "und", ""]]))
        with self.assertRaises(MaterialRejected):
            catalog_import.apply(self.mw.ctx(self.mw.cw.contributor), result.token)
        with self.assertRaises(PermissionDenied):
            catalog_import.preview(self.mw.ctx(self.mw.cw.viewer), workbook([]))
        self.assertTrue(catalog_import.template_bytes().startswith(b"PK"))
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_catalog.py`
Expected: ERROR (`No module named 'suno.materials.catalog_services'`).

- [ ] **Step 3: Los servicios del catálogo**

`server/suno/materials/catalog_services.py`:

```python
"""Catálogo de materiales de la organización (costos, pieza 6c, spec 1.1 y 3.1).

Lo comparten todos los proyectos y lo gestiona quien tiene `create` y `update` de Costos con
alcance de toda la organización (`can_manage_material_catalog`). Un material no se borra: se
desactiva (deja de ofrecerse en formularios nuevos, pero sigue en el kárdex y en los saldos).
La unidad de un material que ya tiene movimientos o líneas de compra no se cambia.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Material, MaterialPurchaseLine, StockMovement
from suno.core.policies import can_manage_material_catalog
from suno.core.scoping import scoped_qs

from .common import MaterialRejected

MAX_CODE = 40
MAX_NAME = 200
MAX_UNIT = 20
MAX_CATEGORY = 80
CODE_CONSTRAINT = "material_org_code_ci_uq"
UNIT_LOCKED = "{code} ya tiene movimientos: su unidad no se cambia (crea otro material)."


@dataclass(frozen=True)
class MaterialData:
    code: str
    name: str
    unit: str
    category: str = ""
    active: bool = True


def material_errors(data: MaterialData) -> list[str]:
    errors: list[str] = []
    for value, limit, missing, what in (
        (data.code, MAX_CODE, "Escribe el código.", "El código"),
        (data.name, MAX_NAME, "Escribe el nombre.", "El nombre"),
        (data.unit, MAX_UNIT, "Escribe la unidad (por ejemplo m3, kg o und).", "La unidad"),
    ):
        text = value.strip()
        if not text:
            errors.append(missing)
        elif len(text) > limit:
            errors.append(f"{what} pasa de {limit} caracteres.")
    if len(data.category.strip()) > MAX_CATEGORY:
        errors.append(f"La categoría pasa de {MAX_CATEGORY} caracteres.")
    return errors


def code_taken(ctx: RequestContext, code: str, exclude: UUID | None = None) -> bool:
    found = scoped_qs(ctx, Material).filter(code__iexact=code.strip())
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


def with_movements(ctx: RequestContext, material_ids: Iterable[UUID]) -> set[UUID]:
    """Los materiales que ya se usaron (movimientos de inventario o líneas de compra)."""
    ids = list(material_ids)
    used = set(
        scoped_qs(ctx, StockMovement)
        .filter(material_id__in=ids)
        .values_list("material_id", flat=True)
        .distinct()
    )
    return used | set(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(material_id__in=ids)
        .values_list("material_id", flat=True)
        .distinct()
    )


def material_metadata(material: Material) -> dict[str, Any]:
    return {
        "material": str(material.pk),
        "code": material.code,
        "unit": material.unit,
        "active": material.active,
    }


@contextmanager
def _unique_code(code: str) -> Iterator[None]:
    """Otra persona guardó el mismo código entre la comprobación y la escritura: la restricción
    lo detiene y aquí vuelve el mensaje de siempre (por fuera de `transaction.atomic`)."""
    try:
        yield
    except IntegrityError as exc:
        if CODE_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([f"Ya hay un material con el código {code.strip()}."]) from exc


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied


def _fields(data: MaterialData) -> dict[str, Any]:
    return {
        "code": data.code.strip(),
        "name": data.name.strip(),
        "unit": data.unit.strip(),
        "category": data.category.strip(),
        "active": data.active,
    }


def create_material(ctx: RequestContext, data: MaterialData) -> Material:
    _require_manager(ctx)
    errors = material_errors(data)
    if errors:
        raise MaterialRejected(errors)
    with _unique_code(data.code), transaction.atomic():
        if code_taken(ctx, data.code):
            raise MaterialRejected([f"Ya hay un material con el código {data.code.strip()}."])
        material = scoped_qs(ctx, Material).create(org_id=ctx.org_id, **_fields(data))
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_CREATED,
            target=f"material:{material.pk}",
            metadata=material_metadata(material),
        )
    return material


def update_material(ctx: RequestContext, material_id: UUID, data: MaterialData) -> Material:
    _require_manager(ctx)
    errors = material_errors(data)
    if errors:
        raise MaterialRejected(errors)
    with _unique_code(data.code), transaction.atomic():
        material = scoped_qs(ctx, Material).select_for_update().filter(pk=material_id).first()
        if material is None:
            raise Http404
        if code_taken(ctx, data.code, exclude=material.pk):
            raise MaterialRejected([f"Ya hay un material con el código {data.code.strip()}."])
        fields = _fields(data)
        if fields["unit"] != material.unit and with_movements(ctx, [material.pk]):
            raise MaterialRejected([UNIT_LOCKED.format(code=material.code)])
        for field, value in fields.items():
            setattr(material, field, value)
        material.save()
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_UPDATED,
            target=f"material:{material.pk}",
            metadata=material_metadata(material),
        )
    return material
```

- [ ] **Step 4: La importación**

`server/suno/materials/catalog_import.py`:

```python
"""Importar el catálogo de materiales desde Excel (costos, pieza 6c, spec 3.1).

- Hoja "Materiales" con el encabezado en la fila 1: Código, Nombre, Unidad y Categoría (se
  encuentran por su texto, sin importar mayúsculas ni tildes).
- Crea los códigos nuevos y actualiza nombre, unidad y categoría de los que ya existen (sin
  distinguir mayúsculas); las filas sin cambios se muestran y no se tocan.
- Como el libro (6a): la vista previa no guarda nada; las filas viajan firmadas una hora, con un
  nonce que se carga una sola vez (`MaterialImportToken`); al aplicar se valida otra vez. Todo
  o nada.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from suno.backoffice.export import to_xlsx_book
from suno.budgets.excel import ExcelRejected, preview_sheet, read_rows
from suno.budgets.mapping import normalize_header
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Material, MaterialImportToken
from suno.core.policies import can_manage_material_catalog
from suno.core.scoping import scoped_qs

from .catalog_services import (
    CODE_CONSTRAINT,
    UNIT_LOCKED,
    MaterialData,
    material_errors,
    with_movements,
)
from .common import MaterialRejected

SHEET = "Materiales"
H_CODE, H_NAME, H_UNIT, H_CATEGORY = "Código", "Nombre", "Unidad", "Categoría"
HEADER = [H_CODE, H_NAME, H_UNIT, H_CATEGORY]
REQUIRED = (H_CODE, H_NAME, H_UNIT)
MAX_IMPORT_ROWS = 5000
MAX_ERRORS = 50
#: Sal de la firma de la vista previa (no es un secreto: la clave es `SECRET_KEY`).
PREVIEW_SALT = "suno.materials.import"
TOKEN_MAX_AGE = 3600
NEW, UPDATE, SAME = "nuevo", "actualiza", "sin cambios"
ALREADY_APPLIED = "Esta vista previa ya se cargó: revisa el catálogo antes de importar otra vez."
RACE = "Otra persona cambió el catálogo mientras tanto: vuelve a subir el archivo."
INVALID = "La vista previa no es válida: vuelve a subir el archivo."


@dataclass(frozen=True)
class ImportRow:
    #: Fila del Excel (el encabezado es la 1).
    line: int
    data: MaterialData
    #: `NEW`, `UPDATE` o `SAME`.
    action: str


@dataclass(frozen=True)
class ImportPreview:
    rows: tuple[ImportRow, ...] = ()
    errors: tuple[str, ...] = ()
    #: Vacío si hay errores: no se puede aplicar.
    token: str = ""

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)


def template_bytes() -> bytes:
    return to_xlsx_book([(SHEET, HEADER, [])])


def _text(value: Any) -> str:
    """Texto de una celda. Un número entero de Excel ("12") sale sin decimales."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip()
    # Deshace el apóstrofo que `safe_cell` antepone a lo que empieza con = + - @.
    if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def _columns(header: Sequence[Any]) -> tuple[dict[str, int], list[str]]:
    keys = {normalize_header(name): name for name in HEADER}
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        name = keys.get(normalize_header(value))
        if name is not None and name not in found:
            found[name] = index
    return found, [f"Falta la columna «{name}»." for name in REQUIRED if name not in found]


def _classify(data: MaterialData, current: Material | None, used: set[Any]) -> tuple[str, str]:
    """(acción, error) de una fila frente a lo que hay en el catálogo."""
    if current is None:
        return NEW, ""
    if current.unit != data.unit.strip() and current.pk in used:
        return "", UNIT_LOCKED.format(code=current.code)
    same = (current.name, current.unit, current.category) == (
        data.name.strip(),
        data.unit.strip(),
        data.category.strip(),
    )
    return (SAME if same else UPDATE), ""


def _require_manager(ctx: RequestContext) -> None:
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied


def preview(ctx: RequestContext, data: bytes) -> ImportPreview:
    _require_manager(ctx)
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
    existing = {material.code.casefold(): material for material in scoped_qs(ctx, Material)}
    used = with_movements(ctx, [material.pk for material in existing.values()])
    rows: list[ImportRow] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    for line, values in enumerate(table[1:], start=2):
        texts = [
            _text(values[columns[name]]) if name in columns and columns[name] < len(values) else ""
            for name in HEADER
        ]
        if not any(texts):
            continue
        item = MaterialData(code=texts[0], name=texts[1], unit=texts[2], category=texts[3])
        problems = material_errors(item)
        key = item.code.strip().casefold()
        if key and key in seen:
            problems.append(f"El código «{item.code.strip()}» se repite (fila {seen[key]}).")
        seen.setdefault(key, line)
        current = existing.get(key)
        action = ""
        if not problems:
            action, problem = _classify(item, current, used)
            if problem:
                problems.append(problem)
        if problems:
            errors.extend(f"Fila {line}: {problem}" for problem in problems)
        else:
            active = current.active if current is not None else True
            rows.append(ImportRow(line, replace(item, active=active), action))
        if len(errors) >= MAX_ERRORS:
            errors.append("Hay más errores: corrige estos y vuelve a subir el archivo.")
            break
    if not rows and not errors:
        errors.append("El archivo no tiene filas de materiales.")
    if errors:
        return ImportPreview(rows=tuple(rows), errors=tuple(errors))
    return ImportPreview(rows=tuple(rows), token=_sign(ctx, rows))


def _sign(ctx: RequestContext, rows: Sequence[ImportRow]) -> str:
    payload = {
        "org": str(ctx.org_id),
        "user": str(ctx.user_id),
        #: Una vista previa se carga una sola vez (`MaterialImportToken`).
        "nonce": secrets.token_urlsafe(24),
        "rows": [
            [row.line, row.data.code, row.data.name, row.data.unit, row.data.category]
            for row in rows
        ],
    }
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def _load(ctx: RequestContext, token: str) -> Mapping[str, Any]:
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired as exc:
        raise MaterialRejected(["La vista previa caducó: vuelve a subir el archivo."]) from exc
    except signing.BadSignature as exc:
        raise MaterialRejected([INVALID]) from exc
    if payload.get("org") != str(ctx.org_id) or payload.get("user") != str(ctx.user_id):
        raise MaterialRejected(["La vista previa no es tuya."])
    if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
        raise MaterialRejected([INVALID])
    return payload


def apply(ctx: RequestContext, token: str) -> tuple[int, int]:
    """Crea y actualiza todas las filas de la vista previa, o ninguna. (creados, actualizados)."""
    _require_manager(ctx)
    payload = _load(ctx, token)
    rows = [
        (int(line), MaterialData(code, name, unit, category))
        for line, code, name, unit, category in payload.get("rows", [])
    ]
    errors = [f"Fila {line}: {problem}" for line, data in rows for problem in material_errors(data)]
    if errors or not rows:
        raise MaterialRejected(errors[:MAX_ERRORS] or ["La vista previa no tiene filas."])
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    scoped_qs(ctx, MaterialImportToken).create(
                        org_id=ctx.org_id, nonce=payload["nonce"], applied_by_id=ctx.user_id
                    )
            except IntegrityError as exc:
                raise MaterialRejected([ALREADY_APPLIED]) from exc
            existing = {
                material.code.casefold(): material
                for material in scoped_qs(ctx, Material).select_for_update()
            }
            used = with_movements(ctx, [material.pk for material in existing.values()])
            created: list[Material] = []
            changed: list[Material] = []
            problems: list[str] = []
            now = timezone.now()
            for line, data in rows:
                current = existing.get(data.code.strip().casefold())
                action, problem = _classify(data, current, used)
                if problem:
                    problems.append(f"Fila {line}: {problem}")
                elif current is None:
                    created.append(
                        Material(
                            org_id=ctx.org_id,
                            code=data.code.strip(),
                            name=data.name.strip(),
                            unit=data.unit.strip(),
                            category=data.category.strip(),
                        )
                    )
                elif action == UPDATE:
                    current.name = data.name.strip()
                    current.unit = data.unit.strip()
                    current.category = data.category.strip()
                    current.updated_at = now
                    changed.append(current)
            if problems:
                raise MaterialRejected(problems[:MAX_ERRORS])
            scoped_qs(ctx, Material).bulk_create(created)
            scoped_qs(ctx, Material).bulk_update(changed, ["name", "unit", "category", "updated_at"])
            audit.record(
                ctx,
                action=AuditAction.MATERIAL_IMPORTED,
                target=f"organization:{ctx.org_id}",
                metadata={"created": len(created), "updated": len(changed)},
            )
    except IntegrityError as exc:
        if CODE_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([RACE]) from exc
    return len(created), len(changed)
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_catalog.py`
Expected: PASS (7 pruebas).

- [ ] **Step 6: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): organization material catalog with Excel import

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Bodegas y compras (servicios)

**Files:**
- Create: `server/suno/materials/warehouses.py`, `server/suno/materials/purchases.py`
- Modify: `server/suno/materials/tests/factories.py` (`stock_in`)
- Test: `server/suno/materials/tests/test_warehouses.py`, `server/suno/materials/tests/test_purchases.py`

- [ ] **Step 1: Ayuda de prueba**

En `server/suno/materials/tests/factories.py`, agregar a los imports `from collections.abc import Sequence`, `CostDocumentType` y `PurchaseKind` al de `suno.core.models`, `from suno.materials import purchases` y `from suno.materials.purchases import PurchaseData, PurchaseLineData`; y al final:

```python
def stock_in(
    mw: MaterialWorld,
    lines: Sequence[tuple[Material, str, str]],
    *,
    warehouse: Warehouse | None = None,
    day: date = BOUGHT,
) -> MaterialPurchase:
    """Una compra a bodega aprobada por los servicios: la registra el Colaborador, se le pone un
    soporte en la BD (sin almacén) y la aprueba la supervisión. `lines`: (material, cantidad,
    valor unitario)."""
    purchase = purchases.create_purchase(
        mw.ctx(mw.cw.contributor),
        mw.project,
        PurchaseData(
            kind=str(PurchaseKind.WAREHOUSE),
            warehouse_id=(warehouse or mw.warehouse).pk,
            supplier="Ferretería El Constructor",
            supplier_tax_id="900123456",
            document_type=str(CostDocumentType.INVOICE),
            document_number="FE-100",
            purchased_on=day,
            lines=tuple(PurchaseLineData(m.pk, D(q), D(p)) for m, q, p in lines),
        ),
    )
    MaterialPurchase.objects.filter(pk=purchase.pk).update(
        attachment=support_blob("factura de materiales"), attachment_name="factura.pdf"
    )
    return purchases.approve_purchase(mw.ctx(mw.world.supervisor), mw.project, purchase.pk)
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_warehouses.py`:

```python
"""Bodegas (spec, 1.2 y 3.2 "Bodegas"): crear, renombrar, desactivar."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import AuditEvent
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected
from suno.materials.warehouses import (
    HAS_STOCK,
    create_warehouse,
    rename_warehouse,
    set_warehouse_active,
)

from .factories import material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class WarehouseTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=date(2026, 4, 15))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)

    def test_create_and_rename_with_unique_names(self) -> None:
        north = create_warehouse(self.contributor, self.project, " Frente norte ")
        self.assertEqual(north.name, "Frente norte")
        with self.assertRaises(MaterialRejected) as caught:
            create_warehouse(self.contributor, self.project, "almacén principal")
        self.assertEqual(caught.exception.errors,
                         ["Ya hay una bodega «almacén principal» en el proyecto."])  # fmt: skip
        renamed = rename_warehouse(self.contributor, self.project, north.pk, "Frente norte 2")
        self.assertEqual(renamed.name, "Frente norte 2")
        self.assertEqual(AuditEvent.objects.filter(action__startswith="warehouse.").count(), 2)
        with self.assertRaises(PermissionDenied):  # el Colaborador, solo las suyas
            rename_warehouse(self.contributor, self.project, self.mw.warehouse.pk, "Otra")
        with self.assertRaises(PermissionDenied):
            create_warehouse(self.mw.ctx(self.mw.cw.viewer), self.project, "Otra")

    def test_a_warehouse_with_stock_is_not_deactivated(self) -> None:
        admin = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "1", "32000.00")])
        with self.assertRaises(MaterialRejected) as caught:
            set_warehouse_active(admin, self.project, self.mw.warehouse.pk, False)
        self.assertEqual(caught.exception.errors, [HAS_STOCK])
        empty = create_warehouse(admin, self.project, "Frente sur")
        self.assertFalse(set_warehouse_active(admin, self.project, empty.pk, False).active)
        self.assertTrue(set_warehouse_active(admin, self.project, empty.pk, True).active)
```

`server/suno/materials/tests/test_purchases.py`:

```python
"""Compras (spec, 2.2, 2.3 y 2.7): a bodega, directas, aprobación, rechazo y anulación."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.test import TestCase, override_settings

from suno.core.models import (
    CostEntry,
    CostEntryItem,
    CostKind,
    CostScope,
    MaterialPurchase,
    MovementKind,
    PurchaseKind,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item, pdf_upload, support_blob
from suno.materials import purchases
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import INDIRECT, DestinationData
from suno.materials.kardex import Stock
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.materials.warehouses import set_warehouse_active

from .factories import assert_consistent, balance_of, bare_issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class PurchaseTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        self.concrete = item(self.mw.cw.budget, "2.1")

    def data(self, *lines: PurchaseLineData, **fields: Any) -> PurchaseData:
        values: dict[str, Any] = {
            "kind": str(PurchaseKind.DIRECT),
            "warehouse_id": None,
            "supplier": "Ferretería El Constructor",
            "supplier_tax_id": "900123456",
            "document_type": "invoice",
            "document_number": "FE-200",
            "purchased_on": date(2026, 3, 5),
            "lines": lines,
        }
        values.update(fields)
        return PurchaseData(**values)

    def direct(self) -> MaterialPurchase:
        to_concrete = DestinationData(
            str(CostScope.ITEM), (self.concrete.pk,), task_id=self.mw.world.task(5).pk
        )
        return purchases.create_purchase(
            self.contributor,
            self.project,
            self.data(
                PurchaseLineData(self.mw.sand.pk, D("5"), D("120000.00"), to_concrete),
                PurchaseLineData(self.mw.cement.pk, D("10"), D("32000.00"), INDIRECT),
            ),
        )

    def supported(self, purchase: MaterialPurchase) -> None:
        MaterialPurchase.objects.filter(pk=purchase.pk).update(attachment=support_blob())

    def test_a_warehouse_purchase_enters_the_stock_when_approved(self) -> None:
        first = stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        self.assertEqual((first.code, second.code), ("CM-001", "CM-002"))
        self.assertEqual((second.status, second.total), ("approved", D("1675000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertFalse(CostEntry.objects.exists())  # el costo llega con las salidas
        assert_consistent(self, self.mw)

    def test_a_direct_purchase_writes_one_approved_cost_per_line(self) -> None:
        purchase = self.direct()
        self.supported(purchase)
        purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        entries = CostEntry.objects.filter(material_purchase=purchase).order_by("-amount")
        self.assertEqual([e.amount for e in entries], [D("600000.00"), D("320000.00")])
        sand = entries[0]
        self.assertEqual(
            (sand.description, sand.third_party, sand.kind, sand.incurred_on),
            ("Compra CM-001 · ARE-01 Arena de río", "Ferretería El Constructor (NIT 900123456)",
             CostKind.MATERIALS, date(2026, 3, 5)),
        )  # fmt: skip
        self.assertEqual((sand.created_by, sand.decided_by),
                         (self.mw.cw.contributor, self.mw.world.supervisor))  # fmt: skip
        self.assertEqual((sand.scope, sand.task_id), ("item", self.mw.world.task(5).pk))
        self.assertEqual(sand.chapter_id, item(self.mw.cw.budget, "2").pk)
        self.assertEqual(CostEntryItem.objects.get(entry=sand).item_id, self.concrete.pk)
        self.assertEqual(entries[1].scope, "indirect")
        self.assertFalse(StockMovement.objects.exists())

    def test_what_a_purchase_needs(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            purchases.create_purchase(
                self.contributor,
                self.project,
                self.data(
                    PurchaseLineData(self.mw.sand.pk, D("0"), D("1000.001")),
                    PurchaseLineData(self.mw.cement.pk, D("2"), D("1000.00"), INDIRECT),
                    kind=str(PurchaseKind.WAREHOUSE),
                    supplier=" ",
                    purchased_on=date(2026, 4, 16),
                ),
            )
        self.assertEqual(
            caught.exception.errors,
            [
                "Elige una bodega activa del proyecto.",
                "Escribe el proveedor.",
                "La fecha de la compra no puede ser futura ni anterior a 2000.",
                "Línea 1: La cantidad es mayor que cero, con máximo 4 decimales.",
                "Línea 1: El valor unitario es mayor que cero, en pesos y centavos.",
                "Línea 2: Una compra a bodega no lleva destino: el costo llega con las salidas.",
            ],
        )
        with self.assertRaises(MaterialRejected) as caught:
            purchases.create_purchase(
                self.contributor,
                self.project,
                self.data(PurchaseLineData(self.mw.sand.pk, D("1"), D("1000.00"))),
            )
        self.assertEqual(caught.exception.errors, ["Línea 1: Escribe el destino."])

    def test_nobody_approves_their_own_purchase_and_the_invoice_is_needed(self) -> None:
        purchase = self.direct()
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual(caught.exception.errors, ["Adjunta la factura antes de aprobar."])
        with self.assertRaises(PermissionDenied):
            purchases.approve_purchase(self.contributor, self.project, purchase.pk)
        purchases.update_purchase(
            self.admin, self.project, purchase.pk,
            self.data(PurchaseLineData(self.mw.cement.pk, D("10"), D("32000.00"), INDIRECT)),
        )  # fmt: skip
        self.supported(purchase)
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.admin, self.project, purchase.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])
        approved = purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual((approved.status, approved.total), ("approved", D("320000.00")))

    def test_a_rejection_has_a_reason_and_its_author_resubmits(self) -> None:
        purchase = self.direct()
        with self.assertRaises(MaterialRejected) as caught:
            purchases.reject_purchase(self.supervisor, self.project, purchase.pk, " ")
        self.assertEqual(caught.exception.errors, ["Escribe el motivo del rechazo."])
        purchases.reject_purchase(self.supervisor, self.project, purchase.pk, "Falta el NIT")
        with self.assertRaises(PermissionDenied):
            purchases.update_purchase(
                self.supervisor, self.project, purchase.pk,
                self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00"), INDIRECT)),
            )  # fmt: skip
        again = purchases.update_purchase(
            self.contributor, self.project, purchase.pk,
            self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00"), INDIRECT)),
        )  # fmt: skip
        self.assertEqual((again.status, again.decided_by_id, again.decision_note),
                         ("draft", None, ""))  # fmt: skip

    def test_attaching_the_invoice_is_not_an_edit(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=tmp.name):
            purchase = self.direct()
            purchases.attach_support(
                self.supervisor, self.project, purchase.pk, pdf_upload(), "factura.pdf"
            )
            purchase.refresh_from_db()
            self.assertEqual((purchase.updated_by, purchase.attachment_name),
                             (self.mw.cw.contributor, "factura.pdf"))  # fmt: skip
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)

    def take(self, quantity: str) -> None:
        """Saca cemento con el motor (las salidas por servicio llegan en la Task 8)."""
        pair = (self.mw.warehouse.pk, self.mw.cement.pk)
        with transaction.atomic():
            stock = Stock(self.admin, self.project, [pair])
            stock.take(str(MovementKind.ISSUE), *pair, D(quantity), date(2026, 3, 10),
                       issue=bare_issue(self.mw))  # fmt: skip
            stock.save()

    def test_voiding_a_warehouse_purchase_needs_the_stock(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        self.take("120")
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            purchases.void_purchase(self.supervisor, self.project, second.pk, "Error")
        with self.assertRaises(MaterialRejected) as caught:
            purchases.void_purchase(self.admin, self.project, second.pk, "Factura errada")
        self.assertEqual(
            caught.exception.errors,
            ["No se puede anular: de Cemento gris 50 kg quedan 30 und en la bodega y la compra "
             "trajo 50."],
        )  # fmt: skip

    def test_voiding_a_warehouse_purchase_takes_out_what_it_brought(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        second = stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        voided = purchases.void_purchase(self.admin, self.project, second.pk, "Factura errada")
        self.assertEqual((voided.status, voided.void_note), ("voided", "Factura errada"))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("100"), D("3200000.00")))
        movement = StockMovement.objects.get(kind="purchase_void")
        self.assertEqual((movement.moved_on, movement.value), (TODAY, D("-1675000.00")))
        assert_consistent(self, self.mw)

    def test_voiding_a_direct_purchase_reverses_its_costs_today(self) -> None:
        purchase = self.direct()
        self.supported(purchase)
        purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        purchases.void_purchase(self.admin, self.project, purchase.pk, "Proveedor equivocado")
        reversals = CostEntry.objects.filter(material_purchase=purchase, reverses__isnull=False)
        self.assertEqual(reversals.count(), 2)
        self.assertEqual({r.incurred_on for r in reversals}, {TODAY})
        self.assertEqual(
            sum(e.amount for e in CostEntry.objects.filter(material_purchase=purchase)), D("0")
        )
        with self.assertRaises(MaterialRejected):
            purchases.void_purchase(self.admin, self.project, purchase.pk, "Otra vez")

    def test_drafts_are_deleted_and_an_inactive_warehouse_blocks_approval(self) -> None:
        purchase = purchases.create_purchase(
            self.contributor, self.project,
            self.data(PurchaseLineData(self.mw.cement.pk, D("1"), D("1000.00")),
                      kind=str(PurchaseKind.WAREHOUSE), warehouse_id=self.mw.warehouse.pk),
        )  # fmt: skip
        self.supported(purchase)
        set_warehouse_active(self.admin, self.project, self.mw.warehouse.pk, False)
        with self.assertRaises(MaterialRejected) as caught:
            purchases.approve_purchase(self.supervisor, self.project, purchase.pk)
        self.assertEqual(
            caught.exception.errors,
            ["La bodega de la compra está inactiva: actívala o cambia la bodega."],
        )
        purchases.delete_purchase(self.contributor, self.project, purchase.pk)
        self.assertFalse(MaterialPurchase.objects.exists())
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_warehouses.py suno/materials/tests/test_purchases.py`
Expected: ERROR (`cannot import name 'purchases' from 'suno.materials'`).

- [ ] **Step 4: Las bodegas**

`server/suno/materials/warehouses.py`:

```python
"""Bodegas del proyecto (costos, pieza 6c, spec 1.2 y 3.2 "Bodegas").

Crear pide `create` de Costos; renombrar, desactivar y activar, `can_update_cost` sobre la
bodega (el Colaborador, solo las que creó; decisión 3 del plan). Solo se desactiva una bodega
sin inventario (cantidad y valor en cero en todos sus materiales).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import Project, StockBalance, Warehouse
from suno.core.policies import can_create_costs, can_update_cost
from suno.core.scoping import scoped_qs

from .common import MaterialRejected, lock_project, locked

MAX_NAME = 120
NAME_CONSTRAINT = "warehouse_project_name_ci_uq"
HAS_STOCK = "La bodega tiene inventario: trasládalo o ajústalo antes de desactivarla."


def _name(name: str) -> str:
    text = name.strip()
    if not text:
        raise MaterialRejected(["Escribe el nombre de la bodega."])
    if len(text) > MAX_NAME:
        raise MaterialRejected([f"El nombre pasa de {MAX_NAME} caracteres."])
    return text


def _taken_message(name: str) -> str:
    return f"Ya hay una bodega «{name}» en el proyecto."


def _taken(
    ctx: RequestContext, project: Project, name: str, exclude: UUID | None = None
) -> bool:
    found = scoped_qs(ctx, Warehouse).filter(project=project, name__iexact=name)
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


@contextmanager
def _unique_name(name: str) -> Iterator[None]:
    try:
        yield
    except IntegrityError as exc:
        if NAME_CONSTRAINT not in str(exc):
            raise
        raise MaterialRejected([_taken_message(name)]) from exc


def _metadata(warehouse: Warehouse) -> dict[str, Any]:
    return {
        "project": str(warehouse.project_id),
        "warehouse": str(warehouse.pk),
        "name": warehouse.name,
        "active": warehouse.active,
    }


def create_warehouse(ctx: RequestContext, project: Project, name: str) -> Warehouse:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    text = _name(name)
    with _unique_name(text), transaction.atomic():
        lock_project(ctx, project)
        if _taken(ctx, project, text):
            raise MaterialRejected([_taken_message(text)])
        warehouse = scoped_qs(ctx, Warehouse).create(
            org_id=ctx.org_id,
            project=project,
            name=text,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_CREATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse


def rename_warehouse(
    ctx: RequestContext, project: Project, warehouse_id: UUID, name: str
) -> Warehouse:
    text = _name(name)
    with _unique_name(text), transaction.atomic():
        lock_project(ctx, project)
        warehouse = locked(ctx, Warehouse, project, warehouse_id)
        if not can_update_cost(ctx, warehouse):
            raise PermissionDenied
        if _taken(ctx, project, text, exclude=warehouse.pk):
            raise MaterialRejected([_taken_message(text)])
        warehouse.name = text
        warehouse.updated_by_id = ctx.user_id
        warehouse.save(update_fields=["name", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_UPDATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse


def set_warehouse_active(
    ctx: RequestContext, project: Project, warehouse_id: UUID, active: bool
) -> Warehouse:
    with transaction.atomic():
        lock_project(ctx, project)
        warehouse = locked(ctx, Warehouse, project, warehouse_id)
        if not can_update_cost(ctx, warehouse):
            raise PermissionDenied
        if not active and (
            scoped_qs(ctx, StockBalance)
            .filter(warehouse=warehouse)
            .filter(Q(quantity__gt=0) | Q(value__gt=0))
            .exists()
        ):
            raise MaterialRejected([HAS_STOCK])
        warehouse.active = active
        warehouse.updated_by_id = ctx.user_id
        warehouse.save(update_fields=["active", "updated_by", "updated_at"])
        audit.record(
            ctx,
            action=AuditAction.WAREHOUSE_UPDATED,
            target=f"warehouse:{warehouse.pk}",
            metadata=_metadata(warehouse),
        )
    return warehouse
```

- [ ] **Step 5: Las compras**

`server/suno/materials/purchases.py`:

```python
"""Compras de materiales (costos, pieza 6c, spec 2.2, 2.3 y 2.7).

- Una compra nace en borrador con su consecutivo (CM-001) y se aprueba una por una, con su
  soporte (la factura) y por otra persona. Adjuntar la factura no es editar; editar no la
  quita (decisión 13 del plan). Un rechazo lo corrige su autor y vuelve a borrador.
- Directa: al aprobarla, cada línea es un costo aprobado en el libro, al destino de la línea; no
  toca el inventario. Anularla escribe los reversos con fecha de hoy.
- A bodega: al aprobarla, cada línea entra a la bodega por su valor (`purchase`); no es costo
  (llega con las salidas). Se anula solo si el saldo alcanza para retirar lo comprado
  (`purchase_void`).
- Anular pide `reverse`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.budgets.totals import ZERO, item_total
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    Material,
    MaterialPurchase,
    MaterialPurchaseLine,
    MaterialPurchaseLineItem,
    MaterialStatus,
    MovementKind,
    Project,
    PurchaseKind,
    Task,
    Warehouse,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.costs.services import Assignment

from .common import (
    MAX_DESCRIPTION,
    MAX_DOCUMENT_NUMBER,
    MAX_FILE_NAME,
    MAX_LINES,
    MAX_THIRD_PARTY,
    MaterialRejected,
    clean_note,
    day_ok,
    lock_project,
    locked,
    next_number,
    price_ok,
    quantity_ok,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationData, check_destination
from .kardex import Stock

MAX_SUPPLIER = 200
MAX_TAX_ID = 40


@dataclass(frozen=True)
class PurchaseLineData:
    material_id: UUID
    quantity: Decimal
    unit_price: Decimal
    #: Solo en una compra directa.
    destination: DestinationData | None = None


@dataclass(frozen=True)
class PurchaseData:
    kind: str
    warehouse_id: UUID | None
    supplier: str
    supplier_tax_id: str
    document_type: str
    document_number: str
    purchased_on: date
    lines: tuple[PurchaseLineData, ...]


@dataclass(frozen=True)
class CheckedLine:
    order: int
    material: Material
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    assignment: Assignment | None


def _header_errors(
    ctx: RequestContext, project: Project, data: PurchaseData, today: date
) -> list[str]:
    errors: list[str] = []
    if data.kind not in PurchaseKind.values:
        errors.append("Elige el tipo de compra.")
    if data.kind == PurchaseKind.WAREHOUSE:
        found = data.warehouse_id is not None and (
            scoped_qs(ctx, Warehouse)
            .filter(pk=data.warehouse_id, project=project, active=True)
            .exists()
        )
        if not found:
            errors.append("Elige una bodega activa del proyecto.")
    elif data.warehouse_id is not None:
        errors.append("Una compra directa no lleva bodega.")
    supplier = data.supplier.strip()
    if not supplier:
        errors.append("Escribe el proveedor.")
    elif len(supplier) > MAX_SUPPLIER:
        errors.append(f"El proveedor pasa de {MAX_SUPPLIER} caracteres.")
    if len(data.supplier_tax_id.strip()) > MAX_TAX_ID:
        errors.append(f"El NIT pasa de {MAX_TAX_ID} caracteres.")
    if data.document_type not in CostDocumentType.values:
        errors.append("Elige el tipo de documento.")
    if len(data.document_number.strip()) > MAX_DOCUMENT_NUMBER:
        errors.append(f"El número de documento pasa de {MAX_DOCUMENT_NUMBER} caracteres.")
    if not day_ok(data.purchased_on, today):
        errors.append("La fecha de la compra no puede ser futura ni anterior a 2000.")
    if not data.lines:
        errors.append("Agrega al menos una línea.")
    elif len(data.lines) > MAX_LINES:
        errors.append(f"Una compra lleva hasta {MAX_LINES} líneas.")
    return errors


def purchase_errors(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    *,
    today: date,
    keep: frozenset[UUID] = frozenset(),
) -> tuple[list[str], list[CheckedLine]]:
    """Errores para el usuario y las líneas listas para guardar. `keep`: los materiales que ya
    tiene la compra (siguen aunque se hayan desactivado)."""
    errors = _header_errors(ctx, project, data, today)
    direct = data.kind == PurchaseKind.DIRECT
    materials = {
        material.pk: material
        for material in scoped_qs(ctx, Material).filter(
            pk__in=[line.material_id for line in data.lines]
        )
    }
    checked: list[CheckedLine] = []
    for n, line in enumerate(data.lines, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
            continue
        problems: list[str] = []
        if not quantity_ok(line.quantity):
            problems.append("La cantidad es mayor que cero, con máximo 4 decimales.")
        if not price_ok(line.unit_price):
            problems.append("El valor unitario es mayor que cero, en pesos y centavos.")
        amount = ZERO if problems else item_total(line.quantity, line.unit_price)
        if not problems and amount <= 0:
            problems.append("El valor de la línea da cero: revisa la cantidad y el valor unitario.")
        assignment: Assignment | None = None
        if direct:
            if line.destination is None:
                problems.append("Escribe el destino.")
            else:
                found, assignment = check_destination(catalog, tasks, line.destination)
                problems += found
        elif line.destination is not None:
            problems.append("Una compra a bodega no lleva destino: el costo llega con las salidas.")
        errors += [f"Línea {n}: {problem}" for problem in problems]
        if not problems:
            checked.append(
                CheckedLine(n, material, line.quantity, line.unit_price, amount, assignment)
            )
    return errors, checked


def purchase_metadata(purchase: MaterialPurchase) -> dict[str, Any]:
    return {
        "project": str(purchase.project_id),
        "purchase": str(purchase.pk),
        "number": purchase.number,
        "kind": purchase.kind,
        "total": str(purchase.total),
        "status": purchase.status,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    catalog: Catalog | None,
    tasks: Mapping[UUID, Task] | None,
    keep: frozenset[UUID] = frozenset(),
) -> list[CheckedLine]:
    errors, lines = purchase_errors(
        ctx,
        project,
        data,
        catalog if catalog is not None else load_catalog(ctx, project),
        tasks if tasks is not None else assignable_tasks(ctx, project),
        today=timezone.localdate(),
        keep=keep,
    )
    if errors:
        raise MaterialRejected(errors)
    return lines


def _fields(data: PurchaseData) -> dict[str, Any]:
    return {
        "kind": data.kind,
        "warehouse_id": data.warehouse_id if data.kind == PurchaseKind.WAREHOUSE else None,
        "supplier": data.supplier.strip(),
        "supplier_tax_id": data.supplier_tax_id.strip(),
        "document_type": data.document_type,
        "document_number": data.document_number.strip(),
        "purchased_on": data.purchased_on,
    }


def _write_lines(
    ctx: RequestContext, purchase: MaterialPurchase, lines: Sequence[CheckedLine]
) -> None:
    scoped_qs(ctx, MaterialPurchaseLine).filter(purchase=purchase).delete()
    rows = scoped_qs(ctx, MaterialPurchaseLine).bulk_create(
        [
            MaterialPurchaseLine(
                org_id=ctx.org_id,
                purchase=purchase,
                order=line.order,
                material=line.material,
                quantity=line.quantity,
                unit_price=line.unit_price,
                amount=line.amount,
                scope=line.assignment.scope if line.assignment else "",
                chapter_id=line.assignment.chapter_id if line.assignment else None,
                task_id=line.assignment.task_id if line.assignment else None,
            )
            for line in lines
        ]
    )
    scoped_qs(ctx, MaterialPurchaseLineItem).bulk_create(
        [
            MaterialPurchaseLineItem(org_id=ctx.org_id, line=row, item_id=item_id)
            for row, line in zip(rows, lines, strict=True)
            if line.assignment is not None
            for item_id in line.assignment.item_ids
        ]
    )


def create_purchase(
    ctx: RequestContext,
    project: Project,
    data: PurchaseData,
    *,
    upload: Upload | None = None,
    name: str = "",
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialPurchase:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    lines = _checked(ctx, project, data, catalog, tasks)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = scoped_qs(ctx, MaterialPurchase).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialPurchase, project),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            attachment=blob,
            attachment_name=name[:MAX_FILE_NAME] if blob is not None else "",
            total=sum((line.amount for line in lines), ZERO),
            **_fields(data),
        )
        _write_lines(ctx, purchase, lines)
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_CREATED,
            target=f"material_purchase:{purchase.pk}",
            metadata=purchase_metadata(purchase),
        )
    return purchase


def _current(ctx: RequestContext, project: Project, purchase_id: UUID) -> MaterialPurchase:
    current = scoped_qs(ctx, MaterialPurchase).filter(pk=purchase_id, project=project).first()
    if current is None:
        raise Http404
    return current


def update_purchase(
    ctx: RequestContext,
    project: Project,
    purchase_id: UUID,
    data: PurchaseData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialPurchase:
    """Guarda un borrador; un rechazado (solo su autor) vuelve a borrador. La factura queda."""
    current = _current(ctx, project, purchase_id)
    require_editable(ctx, current, can_update_cost)
    keep = frozenset(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(purchase=current)
        .values_list("material_id", flat=True)
    )
    lines = _checked(ctx, project, data, catalog, tasks, keep)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        require_editable(ctx, purchase, can_update_cost)
        resubmitted = purchase.status == MaterialStatus.REJECTED
        for field, value in _fields(data).items():
            setattr(purchase, field, value)
        purchase.total = sum((line.amount for line in lines), ZERO)
        purchase.status = str(MaterialStatus.DRAFT)
        purchase.decided_by = None
        purchase.decided_at = None
        purchase.decision_note = ""
        purchase.updated_by_id = ctx.user_id
        purchase.save()
        _write_lines(ctx, purchase, lines)
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_UPDATED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "resubmitted": resubmitted},
        )
    return purchase


def delete_purchase(ctx: RequestContext, project: Project, purchase_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        require_editable(ctx, purchase, can_delete_cost)
        metadata = {**purchase_metadata(purchase), "supplier": purchase.supplier}
        target = f"material_purchase:{purchase.pk}"
        purchase.delete()
        audit.record(
            ctx, action=AuditAction.MATERIAL_PURCHASE_DELETED, target=target, metadata=metadata
        )


def _require_attachable(ctx: RequestContext, purchase: MaterialPurchase) -> None:
    if purchase.status not in OPEN_STATES:
        raise MaterialRejected(["La factura se adjunta a una compra en borrador o rechazada."])
    if not can_update_cost(ctx, purchase):
        raise PermissionDenied


def attach_support(
    ctx: RequestContext, project: Project, purchase_id: UUID, upload: Upload, name: str
) -> MaterialPurchase:
    """Adjunta la factura. No es editar: `updated_by` no cambia (quien la escanea puede aprobar
    si no registró la compra). El archivo se sube antes del bloqueo."""
    _require_attachable(ctx, _current(ctx, project, purchase_id))
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        _require_attachable(ctx, purchase)
        purchase.attachment = blob
        purchase.attachment_name = name[:MAX_FILE_NAME]
        purchase.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_UPDATED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "support": True},
        )
    return purchase


def purchase_lines(ctx: RequestContext, purchase: MaterialPurchase) -> list[MaterialPurchaseLine]:
    return list(
        scoped_qs(ctx, MaterialPurchaseLine)
        .filter(purchase=purchase)
        .select_related("material", "task")
        .prefetch_related("item_links")
        .order_by("order")
    )


def third_party(purchase: MaterialPurchase) -> str:
    tax = purchase.supplier_tax_id
    return (f"{purchase.supplier} (NIT {tax})" if tax else purchase.supplier)[:MAX_THIRD_PARTY]


def _post_direct(
    ctx: RequestContext,
    project: Project,
    purchase: MaterialPurchase,
    lines: Sequence[MaterialPurchaseLine],
) -> int:
    """Un costo aprobado por línea (spec 2.3): tipo Materiales, fecha de la compra, el
    proveedor, el documento y la factura; registró y modificó, los de la compra."""
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for line in lines:
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=purchase.purchased_on,
            kind=CostKind.MATERIALS,
            description=(
                f"Compra {purchase.code} · {line.material.code} {line.material.name}"
            )[:MAX_DESCRIPTION],
            amount=line.amount,
            third_party=third_party(purchase),
            document_type=purchase.document_type,
            document_number=purchase.document_number[:MAX_DOCUMENT_NUMBER],
            scope=line.scope,
            chapter_id=line.chapter_id,
            task_id=line.task_id,
            status=CostStatus.APPROVED,
            created_by_id=purchase.created_by_id,
            updated_by_id=purchase.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=purchase.attachment_id,
            attachment_name=purchase.attachment_name,
            material_purchase=purchase,
        )
        entries.append(entry)
        links += [
            CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=link.item_id)
            for link in line.item_links.all()
        ]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def approve_purchase(ctx: RequestContext, project: Project, purchase_id: UUID) -> MaterialPurchase:
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, purchase)
        if purchase.attachment_id is None:
            raise MaterialRejected(["Adjunta la factura antes de aprobar."])
        lines = purchase_lines(ctx, purchase)
        entries = 0
        if purchase.kind == PurchaseKind.WAREHOUSE:
            warehouse_id = purchase.warehouse_id
            if not scoped_qs(ctx, Warehouse).filter(pk=warehouse_id, active=True).exists():
                raise MaterialRejected(
                    ["La bodega de la compra está inactiva: actívala o cambia la bodega."]
                )
            stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
            for line in lines:
                stock.add(
                    str(MovementKind.PURCHASE),
                    warehouse_id,
                    line.material_id,
                    line.quantity,
                    line.amount,
                    purchase.purchased_on,
                    purchase=purchase,
                )
            stock.save()
        else:
            entries = _post_direct(ctx, project, purchase, lines)
        purchase.status = str(MaterialStatus.APPROVED)
        purchase.decided_by_id = ctx.user_id
        purchase.decided_at = timezone.now()
        purchase.decision_note = ""
        purchase.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_APPROVED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "entries": entries},
        )
    return purchase


def reject_purchase(
    ctx: RequestContext, project: Project, purchase_id: UUID, note: str
) -> MaterialPurchase:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, purchase)
        purchase.status = str(MaterialStatus.REJECTED)
        purchase.decided_by_id = ctx.user_id
        purchase.decided_at = timezone.now()
        purchase.decision_note = reason
        purchase.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_REJECTED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "note": reason},
        )
    return purchase


def void_purchase(
    ctx: RequestContext, project: Project, purchase_id: UUID, note: str
) -> MaterialPurchase:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        purchase = locked(ctx, MaterialPurchase, project, purchase_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if purchase.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula una compra aprobada."])
        reversals = 0
        if purchase.kind == PurchaseKind.WAREHOUSE:
            warehouse_id = purchase.warehouse_id
            lines = purchase_lines(ctx, purchase)
            stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
            needed: dict[UUID, Decimal] = defaultdict(Decimal)
            materials: dict[UUID, Material] = {}
            for line in lines:
                needed[line.material_id] += line.quantity
                materials[line.material_id] = line.material
            errors = [
                f"No se puede anular: de {materials[m].name} quedan "
                f"{qty(stock.balance(warehouse_id, m).quantity) or '0'} {materials[m].unit} en la "
                f"bodega y la compra trajo {qty(amount)}."
                for m, amount in needed.items()
                if stock.balance(warehouse_id, m).quantity < amount
            ]
            if errors:
                raise MaterialRejected(errors)
            today = timezone.localdate()
            for line in lines:
                stock.withdraw(
                    str(MovementKind.PURCHASE_VOID),
                    warehouse_id,
                    line.material_id,
                    line.quantity,
                    line.amount,
                    today,
                    purchase=purchase,
                )
            stock.save()
        else:
            reversals = reverse_entries(ctx, project, "material_purchase", purchase, reason)
        purchase.status = str(MaterialStatus.VOIDED)
        purchase.voided_by_id = ctx.user_id
        purchase.voided_at = timezone.now()
        purchase.void_note = reason
        purchase.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_PURCHASE_VOIDED,
            target=f"material_purchase:{purchase.pk}",
            metadata={**purchase_metadata(purchase), "note": reason, "reversals": reversals},
        )
    return purchase
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_warehouses.py suno/materials/tests/test_purchases.py`
Expected: PASS.

- [ ] **Step 7: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): warehouses and purchases, direct to the ledger or into stock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Salidas, devoluciones y traslados (servicios)

**Files:**
- Create: `server/suno/materials/movements.py`
- Modify: `server/suno/materials/tests/factories.py` (`issue`)
- Test: `server/suno/materials/tests/test_movements.py`, `server/suno/materials/tests/test_concurrency.py`

- [ ] **Step 1: Ayuda de prueba**

En `server/suno/materials/tests/factories.py`, agregar a los imports `from uuid import UUID`, `from suno.materials import movements`, `from suno.materials.destination import INDIRECT, DestinationData` y `from suno.materials.movements import IssueData, IssueLineData`; y al final:

```python
def issue(
    mw: MaterialWorld,
    lines: Sequence[tuple[Material, str]],
    *,
    day: date = date(2026, 3, 10),
    destination: DestinationData = INDIRECT,
    warehouse: Warehouse | None = None,
    user: AppUser | None = None,
    client_id: UUID | None = None,
) -> MaterialIssue:
    """Una salida por el servicio (por defecto, del Colaborador, indirecta)."""
    return movements.create_issue(
        mw.ctx(user or mw.cw.contributor),
        mw.project,
        IssueData(
            warehouse_id=(warehouse or mw.warehouse).pk,
            issued_on=day,
            destination=destination,
            lines=tuple(IssueLineData(m.pk, D(q)) for m, q in lines),
            received_by="Maestro de obra",
            client_id=client_id,
        ),
    )
```

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_movements.py`:

```python
"""Salidas, devoluciones y traslados (spec, 2.4 y 4 "Promedio móvil")."""

from __future__ import annotations

import unittest
import uuid
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    AuditEvent,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials import movements
from suno.materials.common import MaterialRejected
from suno.materials.destination import INDIRECT
from suno.materials.movements import (
    IssueData,
    IssueLineData,
    ReturnData,
    ReturnLineData,
    TransferData,
)

from .factories import (
    assert_consistent,
    balance_of,
    bare_cut,
    issue,
    make_warehouse,
    material_world,
    stock_in,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class IssueTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])

    def data(self, quantity: str, day: date = date(2026, 3, 10)) -> IssueData:
        return IssueData(
            warehouse_id=self.mw.warehouse.pk,
            issued_on=day,
            destination=INDIRECT,
            lines=(IssueLineData(self.mw.cement.pk, D(quantity)),),
        )

    def test_an_issue_leaves_at_the_average_and_fixes_its_cost(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        self.assertEqual((out.code, out.total), ("SA-001", D("227500.00")))
        self.assertEqual((line.unit_cost, line.amount), (D("32500.000000"), D("227500.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("143"), D("4647500.00")))
        self.assertTrue(AuditEvent.objects.filter(action="material_issue.created").exists())
        assert_consistent(self, self.mw)

    def test_rounding_to_cents_and_taking_everything(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.sand, "20", "110000.00")])
        self.assertEqual(issue(self.mw, [(self.mw.sand, "7")]).total, D("746666.67"))
        self.assertEqual(issue(self.mw, [(self.mw.sand, "23")]).total, D("2453333.33"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("0"), D("0.00")))
        assert_consistent(self, self.mw)

    def test_no_negative_stock_no_future_and_no_closed_period(self) -> None:
        cases = (
            (self.data("151"), "Solo hay 150 und de Cemento gris 50 kg en Almacén principal."),
            (self.data("1", date(2026, 4, 16)), "La fecha no puede ser futura ni anterior a 2000."),
        )
        for data, error in cases:
            with self.subTest(error=error), self.assertRaises(MaterialRejected) as caught:
                movements.create_issue(self.contributor, self.project, data)
            self.assertEqual(caught.exception.errors, [error])
        bare_cut(self.mw, status="approved")  # hasta el 31/03/2026
        with self.assertRaises(MaterialRejected) as caught:
            movements.create_issue(self.contributor, self.project, self.data("1", date(2026, 3, 31)))
        self.assertEqual(
            caught.exception.errors,
            ["La fecha cae en un periodo ya cortado (hasta el 31/03/2026): usa una fecha posterior."],
        )
        movements.create_issue(self.contributor, self.project, self.data("1", date(2026, 4, 1)))
        self.assertFalse(MaterialIssue.objects.filter(issued_on=date(2026, 3, 31)).exists())

    def test_editing_an_issue_reapplies_the_average_of_the_moment(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        stock_in(self.mw, [(self.mw.cement, "50", "35000.00")])  # 193 / 6.397.500,00
        edited = movements.update_issue(self.contributor, self.project, out.pk, self.data("10"))
        line = MaterialIssueLine.objects.get(issue=edited)
        self.assertEqual((line.unit_cost, line.amount), (D("33125.000000"), D("331250.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("190"), D("6293750.00")))
        sequences = StockMovement.objects.filter(material=self.mw.cement).order_by("sequence")
        self.assertEqual(list(sequences.values_list("sequence", flat=True)), [1, 2, 4, 5])
        assert_consistent(self, self.mw)

    def test_deleting_an_issue_gives_everything_back(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        movements.delete_issue(self.contributor, self.project, out.pk)
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertFalse(MaterialIssue.objects.exists())
        self.assertTrue(AuditEvent.objects.filter(action="material_issue.deleted").exists())
        assert_consistent(self, self.mw)

    def test_an_issue_with_returns_or_in_a_cut_does_not_change(self) -> None:
        returned = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=returned)
        movements.create_return(
            self.contributor, self.project, returned.pk,
            ReturnData(date(2026, 3, 12), (ReturnLineData(line.pk, D("1")),)),
        )  # fmt: skip
        with self.assertRaises(MaterialRejected) as caught:
            movements.update_issue(self.contributor, self.project, returned.pk, self.data("3"))
        self.assertEqual(caught.exception.errors,
                         ["La salida tiene devoluciones: no se edita ni se borra."])  # fmt: skip
        cut = issue(self.mw, [(self.mw.cement, "1")])
        MaterialIssue.objects.filter(pk=cut.pk).update(cut=bare_cut(self.mw, status="approved"))
        with self.assertRaises(MaterialRejected) as caught:
            movements.delete_issue(self.contributor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors,
                         ["La salida ya está en un corte: no se edita ni se borra."])  # fmt: skip

    def test_permissions(self) -> None:
        with self.assertRaises(PermissionDenied):
            movements.create_issue(self.mw.ctx(self.mw.cw.viewer), self.project, self.data("1"))
        theirs = issue(self.mw, [(self.mw.cement, "1")], user=self.mw.world.admin)
        with self.assertRaises(PermissionDenied):  # el Colaborador, solo las suyas
            movements.update_issue(self.contributor, self.project, theirs.pk, self.data("2"))

    def test_a_client_id_makes_a_retry_safe(self) -> None:
        client_id = uuid.uuid4()
        first = issue(self.mw, [(self.mw.cement, "7")], client_id=client_id)
        again = issue(self.mw, [(self.mw.cement, "7")], client_id=client_id)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("143"), D("4647500.00")))


@skip_without_db
class ReturnAndTransferTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)

    def test_a_return_enters_at_the_cost_of_its_line(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.sand, "20", "110000.00")])
        out = issue(self.mw, [(self.mw.sand, "7")])
        line = MaterialIssueLine.objects.get(issue=out)

        def back(quantity: str, day: date = date(2026, 3, 12)) -> MaterialReturn:
            return movements.create_return(
                self.contributor, self.project, out.pk,
                ReturnData(day, (ReturnLineData(line.pk, D(quantity)),)),
            )  # fmt: skip

        first, second = back("3"), back("4")
        self.assertEqual((first.code, first.total), ("DV-001", D("320000.00")))
        self.assertEqual(second.total, D("426666.67"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("30"), D("3200000.00")))
        with self.assertRaises(MaterialRejected) as caught:
            back("1")
        self.assertEqual(caught.exception.errors,
                         ["Arena de río: con esta devolución vuelven 8 m3 y salieron 7."])  # fmt: skip
        with self.assertRaises(MaterialRejected) as caught:
            back("1", date(2026, 3, 9))
        self.assertIn("La devolución no puede ser anterior a la salida.", caught.exception.errors)
        assert_consistent(self, self.mw)

    def test_a_transfer_round_trip_keeps_the_value(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        north = make_warehouse(self.mw.world, "Frente norte")
        main = self.mw.warehouse
        lines = (IssueLineData(self.mw.cement.pk, D("10")),)
        there = movements.create_transfer(
            self.contributor, self.project, TransferData(main.pk, north.pk, date(2026, 3, 11), lines)
        )
        self.assertEqual((there.code, there.total), ("TR-001", D("325000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement, north), (D("10"), D("325000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("140"), D("4550000.00")))
        movements.create_transfer(
            self.contributor, self.project, TransferData(north.pk, main.pk, date(2026, 3, 12), lines)
        )
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.cement, north), (D("0"), D("0.00")))
        with self.assertRaises(MaterialRejected) as caught:
            movements.create_transfer(
                self.contributor, self.project,
                TransferData(main.pk, main.pk, date(2026, 3, 12), lines),
            )  # fmt: skip
        self.assertEqual(caught.exception.errors, ["Un traslado va entre dos bodegas distintas."])
        assert_consistent(self, self.mw)
```

`server/suno/materials/tests/test_concurrency.py`:

```python
"""Dos salidas a la vez sobre el mismo saldo (spec, 4 "Inventario negativo"): una pasa y la
otra se rechaza. Requiere `TransactionTestCase`: con la transacción envolvente de `TestCase`
los hilos no verían los datos ni habría concurrencia real (como `attachments/tests/
test_concurrency.py`)."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from unittest import mock

from django.db import connections
from django.test import TransactionTestCase

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials.common import MaterialRejected

from .factories import assert_consistent, balance_of, issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


@skip_without_db
class ConcurrentIssueTests(TransactionTestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=date(2026, 4, 15))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "10", "32000.00")])

    def _issue(self, barrier: threading.Barrier) -> str:
        try:
            barrier.wait(timeout=10)
            issue(self.mw, [(self.mw.cement, "7")])
            return "ok"
        except MaterialRejected as exc:
            return exc.errors[0]
        finally:
            connections.close_all()

    def test_two_issues_on_the_same_balance_end_with_one_refused(self) -> None:
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                future.result() for future in [pool.submit(self._issue, barrier) for _ in range(2)]
            )
        self.assertEqual(
            results, ["Solo hay 3 und de Cemento gris 50 kg en Almacén principal.", "ok"]
        )
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("3"), D("96000.00")))
        assert_consistent(self, self.mw)
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_movements.py suno/materials/tests/test_concurrency.py`
Expected: ERROR (`cannot import name 'movements' from 'suno.materials'`).

- [ ] **Step 4: Las salidas, devoluciones y traslados**

`server/suno/materials/movements.py`:

```python
"""Salidas, devoluciones y traslados (costos, pieza 6c, spec 2.4).

- Se registran sin aprobación y mueven el inventario al guardarse (`kardex.Stock`).
- Salida: sale al promedio de su momento; costo unitario y valor de cada línea quedan fijos.
  Mientras no esté en un corte ni tenga devoluciones se edita o se borra: sus movimientos se
  deshacen (decisión 8 del plan) y, al editar, se aplica de nuevo al promedio del momento.
- Devolución: por línea de salida, lo devuelto acumulado no pasa de lo que salió; entra al costo
  de la línea (la que completa se lleva el valor exacto que faltaba). Bodega y destino, los de
  la salida. No se edita ni se borra.
- Traslado: sale de una bodega al promedio y entra a otra del mismo proyecto con el mismo valor.
  No se edita ni se borra.
- Periodo cerrado: nada con fecha menor o igual al fin del último corte aprobado, ni futura.
- `client_id` (la futura app): si la salida ya existe en el proyecto, se devuelve esa.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    Material,
    MaterialIssue,
    MaterialIssueItem,
    MaterialIssueLine,
    MaterialReturn,
    MaterialReturnLine,
    MaterialStatus,
    MaterialTransfer,
    MaterialTransferLine,
    MovementKind,
    Project,
    StockMovement,
    Task,
    Warehouse,
)
from suno.core.policies import can_create_costs, can_delete_cost, can_update_cost
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.costs.services import Assignment

from .common import (
    FIRST_DAY,
    MAX_LINES,
    MAX_NOTE,
    MaterialRejected,
    lock_project,
    locked,
    next_number,
    quantity_ok,
)
from .destination import DestinationData, check_destination, destination_fields, link_items
from .kardex import Stock
from .stock import returned_value

MAX_RECEIVER = 120
IN_A_CUT = "La salida ya está en un corte: no se edita ni se borra."
HAS_RETURNS = "La salida tiene devoluciones: no se edita ni se borra."


@dataclass(frozen=True)
class IssueLineData:
    material_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class IssueData:
    warehouse_id: UUID | None
    issued_on: date
    destination: DestinationData
    lines: tuple[IssueLineData, ...]
    received_by: str = ""
    notes: str = ""
    client_id: UUID | None = None


@dataclass(frozen=True)
class ReturnLineData:
    issue_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class ReturnData:
    returned_on: date
    lines: tuple[ReturnLineData, ...]
    notes: str = ""


@dataclass(frozen=True)
class TransferData:
    source_id: UUID | None
    target_id: UUID | None
    moved_on: date
    lines: tuple[IssueLineData, ...]
    notes: str = ""


def closed_until(ctx: RequestContext, project: Project) -> date | None:
    """El fin del último corte aprobado: hasta ahí el periodo está cerrado."""
    return (
        scoped_qs(ctx, ConsumptionCut)
        .filter(project=project, status=MaterialStatus.APPROVED)
        .aggregate(end=Max("period_end"))["end"]
    )


def day_problem(
    ctx: RequestContext, project: Project, day: date, today: date, what: str = "La fecha"
) -> str:
    """Vacío si la fecha sirve para una salida, una devolución o un traslado."""
    if not FIRST_DAY <= day <= today:
        return f"{what} no puede ser futura ni anterior a 2000."
    closed = closed_until(ctx, project)
    if closed is not None and day <= closed:
        return (
            f"{what} cae en un periodo ya cortado (hasta el {closed:%d/%m/%Y}): "
            "usa una fecha posterior."
        )
    return ""


def active_warehouse(
    ctx: RequestContext, project: Project, warehouse_id: UUID | None
) -> Warehouse | None:
    if warehouse_id is None:
        return None
    return scoped_qs(ctx, Warehouse).filter(pk=warehouse_id, project=project, active=True).first()


def line_errors(
    ctx: RequestContext, lines: Sequence[IssueLineData], keep: frozenset[UUID] = frozenset()
) -> list[str]:
    """Materiales activos (o que ya tenía el documento) y cantidades válidas."""
    errors: list[str] = []
    if not lines:
        errors.append("Agrega al menos una línea.")
    elif len(lines) > MAX_LINES:
        errors.append(f"Un documento lleva hasta {MAX_LINES} líneas.")
    materials = {
        material.pk: material
        for material in scoped_qs(ctx, Material).filter(pk__in=[line.material_id for line in lines])
    }
    for n, line in enumerate(lines, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
        if not quantity_ok(line.quantity):
            errors.append(f"Línea {n}: La cantidad es mayor que cero, con máximo 4 decimales.")
    return errors


def _issue_checked(
    ctx: RequestContext,
    project: Project,
    data: IssueData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    keep: frozenset[UUID],
    today: date,
) -> tuple[Warehouse, Assignment]:
    """Con el proyecto bloqueado: bodega activa, fecha abierta, destino y líneas."""
    errors: list[str] = []
    warehouse = active_warehouse(ctx, project, data.warehouse_id)
    if warehouse is None:
        errors.append("Elige una bodega activa del proyecto.")
    problem = day_problem(ctx, project, data.issued_on, today)
    if problem:
        errors.append(problem)
    if len(data.received_by.strip()) > MAX_RECEIVER:
        errors.append(f"Quién recibe pasa de {MAX_RECEIVER} caracteres.")
    if len(data.notes.strip()) > MAX_NOTE:
        errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
    found, assignment = check_destination(catalog, tasks, data.destination)
    errors += found
    errors += line_errors(ctx, data.lines, keep)
    if errors or warehouse is None or assignment is None:
        raise MaterialRejected(errors)
    return warehouse, assignment


def issue_metadata(issue: MaterialIssue) -> dict[str, Any]:
    return {
        "project": str(issue.project_id),
        "issue": str(issue.pk),
        "number": issue.number,
        "warehouse": str(issue.warehouse_id),
        "issued_on": issue.issued_on.isoformat(),
        "scope": issue.scope,
        "total": str(issue.total),
    }


def _take_lines(
    ctx: RequestContext, stock: Stock, issue: MaterialIssue, lines: Sequence[IssueLineData]
) -> Decimal:
    rows: list[MaterialIssueLine] = []
    total = ZERO
    for n, line in enumerate(lines, start=1):
        movement = stock.take(
            str(MovementKind.ISSUE),
            issue.warehouse_id,
            line.material_id,
            line.quantity,
            issue.issued_on,
            issue=issue,
        )
        rows.append(
            MaterialIssueLine(
                org_id=ctx.org_id,
                issue=issue,
                order=n,
                material_id=line.material_id,
                quantity=line.quantity,
                unit_cost=movement.unit_cost,
                amount=-movement.value,
            )
        )
        total += -movement.value
    scoped_qs(ctx, MaterialIssueLine).bulk_create(rows)
    return total


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


def create_issue(
    ctx: RequestContext,
    project: Project,
    data: IssueData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialIssue:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        if data.client_id is not None:
            existing = scoped_qs(ctx, MaterialIssue).filter(client_id=data.client_id).first()
            if existing is not None:
                if existing.project_id != project.pk:
                    raise MaterialRejected(["Ese identificador de salida ya se usó en otro proyecto."])
                return existing
        warehouse, assignment = _issue_checked(
            ctx, project, data, catalog, tasks, frozenset(), today
        )
        issue = scoped_qs(ctx, MaterialIssue).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialIssue, project),
            warehouse=warehouse,
            issued_on=data.issued_on,
            received_by=data.received_by.strip(),
            notes=data.notes.strip(),
            client_id=data.client_id,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **destination_fields(assignment),
        )
        link_items(ctx, MaterialIssueItem, "issue", issue, assignment.item_ids)
        stock = Stock(ctx, project, [(warehouse.pk, line.material_id) for line in data.lines])
        issue.total = _take_lines(ctx, stock, issue, data.lines)
        stock.save()
        issue.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_ISSUE_CREATED,
            target=f"material_issue:{issue.pk}",
            metadata=issue_metadata(issue),
        )
    return issue


def _require_changeable(
    ctx: RequestContext,
    project: Project,
    issue: MaterialIssue,
    today: date,
    policy: Callable[[RequestContext, Any], bool],
) -> None:
    if not policy(ctx, issue):
        raise PermissionDenied
    if issue.cut_id is not None:
        raise MaterialRejected([IN_A_CUT])
    if scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists():
        raise MaterialRejected([HAS_RETURNS])
    problem = day_problem(ctx, project, issue.issued_on, today)
    if problem:
        raise MaterialRejected([problem])


def _pairs(movements: Iterable[StockMovement]) -> set[tuple[UUID, UUID]]:
    return {(movement.warehouse_id, movement.material_id) for movement in movements}


def update_issue(
    ctx: RequestContext,
    project: Project,
    issue_id: UUID,
    data: IssueData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> MaterialIssue:
    """Deshace los movimientos de la salida y la aplica de nuevo al promedio del momento."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        _require_changeable(ctx, project, issue, today, can_update_cost)
        keep = frozenset(
            scoped_qs(ctx, MaterialIssueLine)
            .filter(issue=issue)
            .values_list("material_id", flat=True)
        )
        warehouse, assignment = _issue_checked(ctx, project, data, catalog, tasks, keep, today)
        old = list(scoped_qs(ctx, StockMovement).filter(issue=issue))
        stock = Stock(
            ctx,
            project,
            _pairs(old) | {(warehouse.pk, line.material_id) for line in data.lines},
        )
        stock.restore(old)
        scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).delete()
        issue.warehouse = warehouse
        issue.issued_on = data.issued_on
        issue.received_by = data.received_by.strip()
        issue.notes = data.notes.strip()
        for field, value in destination_fields(assignment).items():
            setattr(issue, field, value)
        issue.updated_by_id = ctx.user_id
        link_items(ctx, MaterialIssueItem, "issue", issue, assignment.item_ids)
        issue.total = _take_lines(ctx, stock, issue, data.lines)
        stock.save()
        issue.save()
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_ISSUE_UPDATED,
            target=f"material_issue:{issue.pk}",
            metadata=issue_metadata(issue),
        )
    return issue


def delete_issue(ctx: RequestContext, project: Project, issue_id: UUID) -> None:
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        _require_changeable(ctx, project, issue, today, can_delete_cost)
        old = list(scoped_qs(ctx, StockMovement).filter(issue=issue))
        stock = Stock(ctx, project, _pairs(old))
        stock.restore(old)
        stock.save()
        metadata = issue_metadata(issue)
        target = f"material_issue:{issue.pk}"
        issue.delete()
        audit.record(ctx, action=AuditAction.MATERIAL_ISSUE_DELETED, target=target, metadata=metadata)


def returned_by_line(
    ctx: RequestContext, line_ids: Iterable[UUID]
) -> dict[UUID, tuple[Decimal, Decimal]]:
    """Lo devuelto hasta ahora por línea de salida: (cantidad, valor)."""
    return {
        line_id: (quantity, value)
        for line_id, quantity, value in scoped_qs(ctx, MaterialReturnLine)
        .filter(issue_line_id__in=list(line_ids))
        .values("issue_line_id")
        .annotate(quantity=Sum("quantity"), value=Sum("amount"))
        .values_list("issue_line_id", "quantity", "value")
    }


def create_return(
    ctx: RequestContext, project: Project, issue_id: UUID, data: ReturnData
) -> MaterialReturn:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        issue = locked(ctx, MaterialIssue, project, issue_id)
        lines = {
            line.pk: line
            for line in scoped_qs(ctx, MaterialIssueLine)
            .filter(issue=issue)
            .select_related("material")
        }
        errors: list[str] = []
        problem = day_problem(ctx, project, data.returned_on, today)
        if problem:
            errors.append(problem)
        if data.returned_on < issue.issued_on:
            errors.append("La devolución no puede ser anterior a la salida.")
        if len(data.notes.strip()) > MAX_NOTE:
            errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
        wanted: dict[UUID, Decimal] = defaultdict(Decimal)
        for entry in data.lines:
            if entry.quantity:
                wanted[entry.issue_line_id] += entry.quantity
        if not wanted:
            errors.append("Escribe lo que vuelve en al menos una línea.")
        before = returned_by_line(ctx, lines)
        for line_id, amount in wanted.items():
            line = lines.get(line_id)
            if line is None:
                errors.append("Hay líneas que no son de esta salida.")
                continue
            if not quantity_ok(amount):
                errors.append(f"{line.material.name}: La cantidad es mayor que cero, con máximo "
                              "4 decimales.")  # fmt: skip
                continue
            total = before.get(line_id, (ZERO, ZERO))[0] + amount
            if total > line.quantity:
                errors.append(
                    f"{line.material.name}: con esta devolución vuelven {qty(total)} "
                    f"{line.material.unit} y salieron {qty(line.quantity)}."
                )
        if errors:
            raise MaterialRejected(list(dict.fromkeys(errors)))
        back = scoped_qs(ctx, MaterialReturn).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialReturn, project),
            issue=issue,
            returned_on=data.returned_on,
            notes=data.notes.strip(),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        stock = Stock(
            ctx, project, [(issue.warehouse_id, lines[line_id].material_id) for line_id in wanted]
        )
        rows: list[MaterialReturnLine] = []
        total_value = ZERO
        for line_id, amount in wanted.items():
            line = lines[line_id]
            done_quantity, done_value = before.get(line_id, (ZERO, ZERO))
            value = returned_value(
                line.quantity, line.amount, line.unit_cost, done_quantity, done_value, amount
            )
            stock.add(
                str(MovementKind.RETURN),
                issue.warehouse_id,
                line.material_id,
                amount,
                value,
                data.returned_on,
                material_return=back,
            )
            rows.append(
                MaterialReturnLine(
                    org_id=ctx.org_id,
                    material_return=back,
                    issue_line=line,
                    quantity=amount,
                    unit_cost=line.unit_cost,
                    amount=value,
                )
            )
            total_value += value
        scoped_qs(ctx, MaterialReturnLine).bulk_create(rows)
        stock.save()
        back.total = total_value
        back.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_RETURN_CREATED,
            target=f"material_return:{back.pk}",
            metadata={
                "project": str(project.pk),
                "return": str(back.pk),
                "issue": str(issue.pk),
                "number": back.number,
                "total": str(back.total),
            },
        )
    return back


def create_transfer(ctx: RequestContext, project: Project, data: TransferData) -> MaterialTransfer:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    today = timezone.localdate()
    with transaction.atomic():
        lock_project(ctx, project)
        source = active_warehouse(ctx, project, data.source_id)
        target = active_warehouse(ctx, project, data.target_id)
        errors: list[str] = []
        if source is None:
            errors.append("Elige la bodega de donde sale.")
        if target is None:
            errors.append("Elige la bodega a donde entra.")
        if source is not None and target is not None and source.pk == target.pk:
            errors.append("Un traslado va entre dos bodegas distintas.")
        problem = day_problem(ctx, project, data.moved_on, today)
        if problem:
            errors.append(problem)
        if len(data.notes.strip()) > MAX_NOTE:
            errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
        errors += line_errors(ctx, data.lines)
        if errors or source is None or target is None:
            raise MaterialRejected(errors)
        transfer = scoped_qs(ctx, MaterialTransfer).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, MaterialTransfer, project),
            source=source,
            target=target,
            moved_on=data.moved_on,
            notes=data.notes.strip(),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        stock = Stock(
            ctx,
            project,
            [(w.pk, line.material_id) for line in data.lines for w in (source, target)],
        )
        rows: list[MaterialTransferLine] = []
        total = ZERO
        for n, line in enumerate(data.lines, start=1):
            out = stock.take(
                str(MovementKind.TRANSFER_OUT), source.pk, line.material_id, line.quantity,
                data.moved_on, transfer=transfer,
            )  # fmt: skip
            stock.add(
                str(MovementKind.TRANSFER_IN), target.pk, line.material_id, line.quantity,
                -out.value, data.moved_on, transfer=transfer,
            )  # fmt: skip
            rows.append(
                MaterialTransferLine(
                    org_id=ctx.org_id,
                    transfer=transfer,
                    order=n,
                    material_id=line.material_id,
                    quantity=line.quantity,
                    unit_cost=out.unit_cost,
                    amount=-out.value,
                )
            )
            total += -out.value
        scoped_qs(ctx, MaterialTransferLine).bulk_create(rows)
        stock.save()
        transfer.total = total
        transfer.save(update_fields=["total"])
        audit.record(
            ctx,
            action=AuditAction.MATERIAL_TRANSFER_CREATED,
            target=f"material_transfer:{transfer.pk}",
            metadata={
                "project": str(project.pk),
                "transfer": str(transfer.pk),
                "number": transfer.number,
                "source": str(source.pk),
                "target": str(target.pk),
                "total": str(total),
            },
        )
    return transfer
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_movements.py suno/materials/tests/test_concurrency.py`
Expected: PASS.

- [ ] **Step 6: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): issues, returns and transfers at the moving average

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Ajustes de inventario por conteo físico (servicios)

**Files:**
- Create: `server/suno/materials/adjustments.py`
- Test: `server/suno/materials/tests/test_adjustments.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_adjustments.py`:

```python
"""Ajuste por conteo físico (spec, 2.5 y 4 "Ajuste")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    CostEntry,
    InventoryAdjustment,
    InventoryAdjustmentLine,
    StockMovement,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import support_blob
from suno.materials import adjustments
from suno.materials.adjustments import CHANGED_COUNT, AdjustmentData, AdjustmentLineData
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import INDIRECT

from .factories import assert_consistent, balance_of, issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class AdjustmentTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])  # 150 / 4.875.000,00

    def count(self, *lines: AdjustmentLineData) -> InventoryAdjustment:
        adjustment = adjustments.create_adjustment(
            self.contributor,
            self.project,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 3, 31), INDIRECT, lines),
        )
        InventoryAdjustment.objects.filter(pk=adjustment.pk).update(
            attachment=support_blob("acta de conteo")
        )
        return adjustment

    def test_a_shortage_leaves_at_the_average_and_is_an_approved_cost(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        line = InventoryAdjustmentLine.objects.get(adjustment=draft)
        self.assertEqual((draft.code, line.expected, line.difference),
                         ("AJ-001", D("150"), D("-5")))  # fmt: skip
        approved = adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(approved.shortage_total, D("162500.00"))
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("145"), D("4712500.00")))
        entry = CostEntry.objects.get(inventory_adjustment=approved)
        self.assertEqual(
            (entry.amount, entry.incurred_on, entry.scope, entry.document_type, entry.decided_by),
            (D("162500.00"), date(2026, 3, 31), "indirect", "inventory_count",
             self.mw.world.supervisor),
        )  # fmt: skip
        assert_consistent(self, self.mw)

    def test_a_surplus_enters_at_the_average_or_the_last_entry(self) -> None:
        stock_in(self.mw, [(self.mw.sand, "10", "100000.00")])
        issue(self.mw, [(self.mw.sand, "10")])  # arena en cero; última entrada 100.000
        draft = self.count(
            AdjustmentLineData(self.mw.cement.pk, D("152")),
            AdjustmentLineData(self.mw.sand.pk, D("2")),
            AdjustmentLineData(self.mw.steel.pk, D("5"), D("4100.00")),
        )
        approved = adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(approved.surplus_total, D("65000.00") + D("200000.00") + D("20500.00"))
        self.assertEqual(balance_of(self.mw, self.mw.sand), (D("2"), D("200000.00")))
        self.assertEqual(balance_of(self.mw, self.mw.steel), (D("5"), D("20500.00")))
        self.assertFalse(CostEntry.objects.filter(inventory_adjustment=approved).exists())
        assert_consistent(self, self.mw)

    def test_a_surplus_without_balance_nor_entries_needs_a_unit_cost(self) -> None:
        with self.assertRaises(MaterialRejected) as caught:
            self.count(AdjustmentLineData(self.mw.steel.pk, D("5")))
        self.assertEqual(
            caught.exception.errors,
            ["Varilla de 1/2 pulgada: no hay saldo ni entradas de este material en la bodega; "
             "escribe el costo unitario del sobrante."],
        )  # fmt: skip
        with self.assertRaises(MaterialRejected) as caught:
            self.count(AdjustmentLineData(self.mw.cement.pk, D("150")))
        self.assertEqual(caught.exception.errors,
                         ["El conteo coincide con el inventario: no hay nada que ajustar."])  # fmt: skip

    def test_the_stock_changed_since_the_count(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 4, 1))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(caught.exception.errors, [CHANGED_COUNT])
        with self.assertRaises(PermissionDenied):  # el Colaborador no aprueba
            adjustments.approve_adjustment(self.contributor, self.project, draft.pk)
        adjustments.update_adjustment(
            self.supervisor, self.project, draft.pk,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 4, 2), INDIRECT,
                           (AdjustmentLineData(self.mw.cement.pk, D("145")),)),
        )  # fmt: skip
        InventoryAdjustment.objects.filter(pk=draft.pk).update(attachment=support_blob("acta"))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.approve_adjustment(self.supervisor, self.project, draft.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])

    def test_editing_recomputes_the_expected_and_removes_the_signed_count(self) -> None:
        draft = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 4, 1))
        edited = adjustments.update_adjustment(
            self.contributor, self.project, draft.pk,
            AdjustmentData(self.mw.warehouse.pk, date(2026, 4, 2), INDIRECT,
                           (AdjustmentLineData(self.mw.cement.pk, D("145")),)),
        )  # fmt: skip
        self.assertIsNone(edited.attachment_id)
        self.assertEqual(InventoryAdjustmentLine.objects.get(adjustment=edited).expected, D("149"))

    def test_voiding_puts_back_the_shortage_and_takes_out_the_surplus(self) -> None:
        shortage = self.count(AdjustmentLineData(self.mw.cement.pk, D("145")))
        adjustments.approve_adjustment(self.supervisor, self.project, shortage.pk)
        adjustments.void_adjustment(self.admin, self.project, shortage.pk, "Conteo errado")
        self.assertEqual(balance_of(self.mw, self.mw.cement), (D("150"), D("4875000.00")))
        entries = CostEntry.objects.filter(inventory_adjustment=shortage)
        self.assertEqual(sum(e.amount for e in entries), D("0"))
        surplus = self.count(AdjustmentLineData(self.mw.cement.pk, D("152")))
        adjustments.approve_adjustment(self.supervisor, self.project, surplus.pk)
        issue(self.mw, [(self.mw.cement, "151")], day=date(2026, 4, 1))
        with self.assertRaises(MaterialRejected) as caught:
            adjustments.void_adjustment(self.admin, self.project, surplus.pk, "Error")
        self.assertEqual(
            caught.exception.errors,
            ["No se puede anular: de Cemento gris 50 kg quedan 1 und en la bodega y el sobrante "
             "fue 2."],
        )  # fmt: skip
        self.assertEqual(StockMovement.objects.filter(kind="adjustment_void").count(), 1)
        assert_consistent(self, self.mw)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_adjustments.py`
Expected: ERROR (`cannot import name 'adjustments' from 'suno.materials'`).

- [ ] **Step 3: Los ajustes**

`server/suno/materials/adjustments.py`:

```python
"""Ajustes de inventario por conteo físico (costos, pieza 6c, spec 2.5 y 2.7).

- Borrador: por material, lo esperado es el saldo al guardar y la diferencia es contado −
  esperado. Una línea por material; un conteo sin diferencias no se guarda. Editar recalcula lo
  esperado y quita el acta de conteo firmada (ya no corresponde); adjuntarla no es editar.
- Al aprobar (otra persona, con el acta): se vuelven a leer los saldos; si alguno cambió desde
  el borrador, se rechaza. El faltante sale al promedio (`adjustment_out`) y es un costo
  aprobado al destino del ajuste por el total de los faltantes, con la fecha del conteo. El
  sobrante entra al promedio vigente; con saldo en cero, al costo unitario de la última entrada
  del material en la bodega; sin ninguna, al que escribió el usuario (`adjustment_in`).
- Anular (`reverse`): el faltante vuelve a la bodega por el valor que salió y el sobrante se
  retira si el saldo alcanza (los dos `adjustment_void`); el costo se reversa con fecha de hoy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.budgets.totals import ZERO, item_total, quantity
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    InventoryAdjustment,
    InventoryAdjustmentItem,
    InventoryAdjustmentLine,
    Material,
    MaterialStatus,
    MovementKind,
    Project,
    StockBalance,
    StockMovement,
    Task,
    Warehouse,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.core.templatetags.money_tags import qty
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.costs.services import Assignment

from .common import (
    MAX_DESCRIPTION,
    MAX_FILE_NAME,
    MAX_LINES,
    MAX_NOTE,
    MAX_QUANTITY,
    MaterialRejected,
    clean_note,
    day_ok,
    lock_project,
    locked,
    next_number,
    price_ok,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationData, check_destination, destination_fields, link_items
from .kardex import Stock

CHANGED_COUNT = "El inventario cambió desde el conteo; recalcula el ajuste."
NOTHING_TO_ADJUST = "El conteo coincide con el inventario: no hay nada que ajustar."


@dataclass(frozen=True)
class AdjustmentLineData:
    material_id: UUID
    counted: Decimal
    #: Solo para un sobrante sin saldo ni entradas previas del material en la bodega.
    unit_cost: Decimal | None = None


@dataclass(frozen=True)
class AdjustmentData:
    warehouse_id: UUID | None
    counted_on: date
    destination: DestinationData
    lines: tuple[AdjustmentLineData, ...]
    notes: str = ""


@dataclass(frozen=True)
class CountLine:
    """Una línea calculada sin guardar: para el formulario ("Recalcular") y el servicio."""

    order: int
    material: Material
    expected: Decimal
    counted: Decimal
    given_unit_cost: Decimal | None
    #: Sobrante sin saldo ni entradas: hace falta el costo unitario que escriba el usuario.
    needs_unit_cost: bool

    @property
    def difference(self) -> Decimal:
        return self.counted - self.expected


def expected_quantities(
    ctx: RequestContext, warehouse_id: UUID, material_ids: Iterable[UUID]
) -> dict[UUID, Decimal]:
    return dict(
        scoped_qs(ctx, StockBalance)
        .filter(warehouse_id=warehouse_id, material_id__in=list(material_ids))
        .values_list("material_id", "quantity")
    )


def last_entry_costs(
    ctx: RequestContext, warehouse_id: UUID, material_ids: Iterable[UUID]
) -> dict[UUID, Decimal]:
    """El costo unitario del último movimiento de entrada de cada material en la bodega."""
    return dict(
        scoped_qs(ctx, StockMovement)
        .filter(warehouse_id=warehouse_id, material_id__in=list(material_ids), quantity__gt=0)
        .order_by("material_id", "-sequence")
        .distinct("material_id")
        .values_list("material_id", "unit_cost")
    )


def count_lines(
    ctx: RequestContext,
    warehouse: Warehouse,
    lines: Iterable[AdjustmentLineData],
    *,
    keep: frozenset[UUID] = frozenset(),
) -> tuple[list[str], list[CountLine]]:
    """Lo esperado (el saldo de hoy) y la diferencia de cada línea, y los errores."""
    given = list(lines)
    errors: list[str] = []
    if not given:
        errors.append("Agrega al menos un material contado.")
    elif len(given) > MAX_LINES:
        errors.append(f"Un ajuste lleva hasta {MAX_LINES} materiales.")
    ids = [line.material_id for line in given]
    materials = {m.pk: m for m in scoped_qs(ctx, Material).filter(pk__in=ids)}
    expected = expected_quantities(ctx, warehouse.pk, ids)
    last = last_entry_costs(ctx, warehouse.pk, ids)
    seen: set[UUID] = set()
    counted: list[CountLine] = []
    for n, line in enumerate(given, start=1):
        material = materials.get(line.material_id)
        if material is None or not (material.active or material.pk in keep):
            errors.append(f"Línea {n}: Elige un material activo del catálogo.")
            continue
        if material.pk in seen:
            errors.append(f"{material.name}: va una sola vez en el ajuste.")
            continue
        seen.add(material.pk)
        if not (
            line.counted.is_finite()
            and 0 <= line.counted <= MAX_QUANTITY
            and quantity(line.counted) == line.counted
        ):
            errors.append(f"{material.name}: lo contado es cero o más, con máximo 4 decimales.")
            continue
        if line.unit_cost is not None and not price_ok(line.unit_cost):
            errors.append(f"{material.name}: el costo unitario va en pesos y centavos.")
            continue
        before = expected.get(material.pk, Decimal("0"))
        needs = line.counted > before and before == 0 and material.pk not in last
        if needs and line.unit_cost is None:
            errors.append(
                f"{material.name}: no hay saldo ni entradas de este material en la bodega; "
                "escribe el costo unitario del sobrante."
            )
        counted.append(CountLine(n, material, before, line.counted, line.unit_cost, needs))
    if not errors and counted and all(line.difference == 0 for line in counted):
        errors.append(NOTHING_TO_ADJUST)
    return errors, counted


def adjustment_metadata(adjustment: InventoryAdjustment) -> dict[str, Any]:
    return {
        "project": str(adjustment.project_id),
        "adjustment": str(adjustment.pk),
        "number": adjustment.number,
        "warehouse": str(adjustment.warehouse_id),
        "shortage": str(adjustment.shortage_total),
        "surplus": str(adjustment.surplus_total),
        "status": adjustment.status,
    }


def _checked(
    ctx: RequestContext,
    project: Project,
    data: AdjustmentData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    keep: frozenset[UUID] = frozenset(),
) -> tuple[Warehouse, Assignment, list[CountLine]]:
    errors: list[str] = []
    warehouse = (
        scoped_qs(ctx, Warehouse)
        .filter(pk=data.warehouse_id, project=project, active=True)
        .first()
        if data.warehouse_id is not None
        else None
    )
    if warehouse is None:
        errors.append("Elige una bodega activa del proyecto.")
    if not day_ok(data.counted_on, timezone.localdate()):
        errors.append("La fecha del conteo no puede ser futura ni anterior a 2000.")
    if len(data.notes.strip()) > MAX_NOTE:
        errors.append(f"Las notas pasan de {MAX_NOTE} caracteres.")
    found, assignment = check_destination(catalog, tasks, data.destination)
    errors += found
    lines: list[CountLine] = []
    if warehouse is not None:
        more, lines = count_lines(ctx, warehouse, data.lines, keep=keep)
        errors += more
    if errors or warehouse is None or assignment is None:
        raise MaterialRejected(errors)
    return warehouse, assignment, lines


def _write(
    ctx: RequestContext,
    adjustment: InventoryAdjustment,
    assignment: Assignment,
    lines: list[CountLine],
) -> None:
    link_items(ctx, InventoryAdjustmentItem, "adjustment", adjustment, assignment.item_ids)
    scoped_qs(ctx, InventoryAdjustmentLine).filter(adjustment=adjustment).delete()
    scoped_qs(ctx, InventoryAdjustmentLine).bulk_create(
        [
            InventoryAdjustmentLine(
                org_id=ctx.org_id,
                adjustment=adjustment,
                order=line.order,
                material=line.material,
                expected=line.expected,
                counted=line.counted,
                difference=line.difference,
                given_unit_cost=line.given_unit_cost,
            )
            for line in lines
        ]
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


def create_adjustment(
    ctx: RequestContext,
    project: Project,
    data: AdjustmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> InventoryAdjustment:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    with transaction.atomic():
        lock_project(ctx, project)
        warehouse, assignment, lines = _checked(ctx, project, data, catalog, tasks)
        adjustment = scoped_qs(ctx, InventoryAdjustment).create(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, InventoryAdjustment, project),
            warehouse=warehouse,
            counted_on=data.counted_on,
            notes=data.notes.strip(),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **destination_fields(assignment),
        )
        _write(ctx, adjustment, assignment, lines)
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_CREATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata=adjustment_metadata(adjustment),
        )
    return adjustment


def update_adjustment(
    ctx: RequestContext,
    project: Project,
    adjustment_id: UUID,
    data: AdjustmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> InventoryAdjustment:
    """Recalcula lo esperado con los saldos de hoy y quita el acta firmada; un rechazado (solo
    su autor) vuelve a borrador."""
    catalog, tasks = _defaults(ctx, project, catalog, tasks)
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        require_editable(ctx, adjustment, can_update_cost)
        keep = frozenset(
            scoped_qs(ctx, InventoryAdjustmentLine)
            .filter(adjustment=adjustment)
            .values_list("material_id", flat=True)
        )
        warehouse, assignment, lines = _checked(ctx, project, data, catalog, tasks, keep)
        resubmitted = adjustment.status == MaterialStatus.REJECTED
        signed_removed = adjustment.attachment_id is not None
        adjustment.warehouse = warehouse
        adjustment.counted_on = data.counted_on
        adjustment.notes = data.notes.strip()
        for field, value in destination_fields(assignment).items():
            setattr(adjustment, field, value)
        adjustment.status = str(MaterialStatus.DRAFT)
        adjustment.decided_by = None
        adjustment.decided_at = None
        adjustment.decision_note = ""
        adjustment.attachment = None
        adjustment.attachment_name = ""
        adjustment.updated_by_id = ctx.user_id
        adjustment.save()
        _write(ctx, adjustment, assignment, lines)
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_UPDATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={
                **adjustment_metadata(adjustment),
                "resubmitted": resubmitted,
                "signed_removed": signed_removed,
            },
        )
    return adjustment


def delete_adjustment(ctx: RequestContext, project: Project, adjustment_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        require_editable(ctx, adjustment, can_delete_cost)
        metadata = adjustment_metadata(adjustment)
        target = f"inventory_adjustment:{adjustment.pk}"
        adjustment.delete()
        audit.record(
            ctx, action=AuditAction.INVENTORY_ADJUSTMENT_DELETED, target=target, metadata=metadata
        )


def _require_signable(ctx: RequestContext, adjustment: InventoryAdjustment) -> None:
    if adjustment.status not in OPEN_STATES:
        raise MaterialRejected(["El acta de conteo se adjunta a un ajuste en borrador."])
    if not can_update_cost(ctx, adjustment):
        raise PermissionDenied


def attach_count(
    ctx: RequestContext, project: Project, adjustment_id: UUID, upload: Upload, name: str
) -> InventoryAdjustment:
    """Adjunta el acta de conteo firmada. No es editar (`updated_by` no cambia)."""
    current = (
        scoped_qs(ctx, InventoryAdjustment).filter(pk=adjustment_id, project=project).first()
    )
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        _require_signable(ctx, adjustment)
        adjustment.attachment = blob
        adjustment.attachment_name = name[:MAX_FILE_NAME]
        adjustment.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_UPDATED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "signed": True},
        )
    return adjustment


def adjustment_lines(
    ctx: RequestContext, adjustment: InventoryAdjustment
) -> list[InventoryAdjustmentLine]:
    return list(
        scoped_qs(ctx, InventoryAdjustmentLine)
        .filter(adjustment=adjustment)
        .select_related("material")
        .order_by("order")
    )


def _post_shortage(
    ctx: RequestContext, project: Project, adjustment: InventoryAdjustment, amount: Decimal
) -> None:
    """Un costo aprobado por el total de los faltantes, al destino del ajuste (spec 2.5)."""
    entry = scoped_qs(ctx, CostEntry).create(
        org_id=ctx.org_id,
        project=project,
        incurred_on=adjustment.counted_on,
        kind=CostKind.MATERIALS,
        description=(
            f"Ajuste {adjustment.code} · faltantes de inventario en {adjustment.warehouse.name}"
        )[:MAX_DESCRIPTION],
        amount=amount,
        third_party="",
        document_type=CostDocumentType.INVENTORY_COUNT,
        document_number=adjustment.code,
        scope=adjustment.scope,
        chapter_id=adjustment.chapter_id,
        task_id=adjustment.task_id,
        status=CostStatus.APPROVED,
        created_by_id=adjustment.created_by_id,
        updated_by_id=adjustment.updated_by_id,
        decided_by_id=ctx.user_id,
        decided_at=timezone.now(),
        attachment_id=adjustment.attachment_id,
        attachment_name=adjustment.attachment_name,
        inventory_adjustment=adjustment,
    )
    scoped_qs(ctx, CostEntryItem).bulk_create(
        [
            CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=item_id)
            for item_id in scoped_qs(ctx, InventoryAdjustmentItem)
            .filter(adjustment=adjustment)
            .values_list("item_id", flat=True)
        ]
    )


def approve_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID
) -> InventoryAdjustment:
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, adjustment)
        if adjustment.attachment_id is None:
            raise MaterialRejected(["Adjunta el acta de conteo firmada antes de aprobar."])
        lines = adjustment_lines(ctx, adjustment)
        warehouse_id = adjustment.warehouse_id
        stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
        if any(
            stock.balance(warehouse_id, line.material_id).quantity != line.expected
            for line in lines
        ):
            raise MaterialRejected([CHANGED_COUNT])
        last = last_entry_costs(ctx, warehouse_id, [line.material_id for line in lines])
        shortage = surplus = ZERO
        day = adjustment.counted_on
        for line in lines:
            balance = stock.balance(warehouse_id, line.material_id)
            if line.difference < 0:
                out = stock.take(
                    str(MovementKind.ADJUSTMENT_OUT), warehouse_id, line.material_id,
                    -line.difference, day, adjustment=adjustment,
                )  # fmt: skip
                line.unit_cost, line.amount = out.unit_cost, -out.value
                shortage += line.amount
                continue
            if line.difference == 0:
                line.unit_cost, line.amount = balance.average or ZERO, ZERO
                continue
            unit = balance.average or last.get(line.material_id) or line.given_unit_cost
            if unit is None:
                raise MaterialRejected(
                    [f"{line.material.name}: escribe el costo unitario del sobrante."]
                )
            line.unit_cost = unit
            line.amount = item_total(line.difference, unit)
            stock.add(
                str(MovementKind.ADJUSTMENT_IN), warehouse_id, line.material_id,
                line.difference, line.amount, day, adjustment=adjustment,
            )  # fmt: skip
            surplus += line.amount
        scoped_qs(ctx, InventoryAdjustmentLine).bulk_update(lines, ["unit_cost", "amount"])
        stock.save()
        if shortage > 0:
            _post_shortage(ctx, project, adjustment, shortage)
        adjustment.shortage_total, adjustment.surplus_total = shortage, surplus
        adjustment.status = str(MaterialStatus.APPROVED)
        adjustment.decided_by_id = ctx.user_id
        adjustment.decided_at = timezone.now()
        adjustment.save(
            update_fields=["shortage_total", "surplus_total", "status", "decided_by", "decided_at"]
        )
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_APPROVED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata=adjustment_metadata(adjustment),
        )
    return adjustment


def reject_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID, note: str
) -> InventoryAdjustment:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, adjustment)
        adjustment.status = str(MaterialStatus.REJECTED)
        adjustment.decided_by_id = ctx.user_id
        adjustment.decided_at = timezone.now()
        adjustment.decision_note = reason
        adjustment.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_REJECTED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "note": reason},
        )
    return adjustment


def void_adjustment(
    ctx: RequestContext, project: Project, adjustment_id: UUID, note: str
) -> InventoryAdjustment:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        adjustment = locked(ctx, InventoryAdjustment, project, adjustment_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if adjustment.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula un ajuste aprobado."])
        lines = adjustment_lines(ctx, adjustment)
        warehouse_id = adjustment.warehouse_id
        stock = Stock(ctx, project, [(warehouse_id, line.material_id) for line in lines])
        errors = [
            f"No se puede anular: de {line.material.name} quedan "
            f"{qty(stock.balance(warehouse_id, line.material_id).quantity) or '0'} "
            f"{line.material.unit} en la bodega y el sobrante fue {qty(line.difference)}."
            for line in lines
            if line.difference > 0
            and stock.balance(warehouse_id, line.material_id).quantity < line.difference
        ]
        if errors:
            raise MaterialRejected(errors)
        today = timezone.localdate()
        kind = str(MovementKind.ADJUSTMENT_VOID)
        for line in lines:
            value = line.amount or ZERO
            if line.difference > 0:
                stock.withdraw(kind, warehouse_id, line.material_id, line.difference, value,
                               today, adjustment=adjustment)  # fmt: skip
            elif line.difference < 0:
                stock.add(kind, warehouse_id, line.material_id, -line.difference, value, today,
                          adjustment=adjustment)  # fmt: skip
        stock.save()
        reversals = reverse_entries(ctx, project, "inventory_adjustment", adjustment, reason)
        adjustment.status = str(MaterialStatus.VOIDED)
        adjustment.voided_by_id = ctx.user_id
        adjustment.voided_at = timezone.now()
        adjustment.void_note = reason
        adjustment.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.INVENTORY_ADJUSTMENT_VOIDED,
            target=f"inventory_adjustment:{adjustment.pk}",
            metadata={**adjustment_metadata(adjustment), "note": reason, "reversals": reversals},
        )
    return adjustment
```

Comprobación de los números de la prueba de sobrantes: cemento 150 / 4.875.000 (promedio 32.500) cuenta 152 → +2 × 32.500 = 65.000,00; arena en cero con última entrada a 100.000,000000 → +2 = 200.000,00; varilla sin saldo ni entradas con costo dado 4.100 → +5 = 20.500,00.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_adjustments.py`
Expected: PASS (6 pruebas).

- [ ] **Step 5: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): inventory adjustments by physical count

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Corte de consumos (servicios)

**Files:**
- Create: `server/suno/materials/cuts.py`
- Test: `server/suno/materials/tests/test_cuts.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_cuts.py`:

```python
"""Corte de consumos (spec, 2.6 y 4 "Corte")."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.core.models import (
    ConsumptionCut,
    CostEntry,
    CostScope,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import item, support_blob
from suno.materials import cuts, movements
from suno.materials.common import SELF_DECISION, MaterialRejected
from suno.materials.destination import DestinationData
from suno.materials.movements import ReturnData, ReturnLineData

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
MARCH_END = date(2026, 3, 31)


@skip_without_db
class CutTests(TestCase):
    """Cemento a 32.500 de promedio y varilla a 4.000. Salidas: 7 und indirectas (227.500,00),
    10 und al 2.1 en la tarea 2.1 (325.000,00) y 100 kg al 2.2 (400.000,00); devolución de
    2 und del 2.1 (65.000,00). Neto del periodo: 887.500,00."""

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.contributor = self.mw.ctx(self.mw.cw.contributor)
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        self.admin = self.mw.ctx()
        budget = self.mw.cw.budget
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00"), (self.mw.steel, "800", "4000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        concrete = DestinationData(
            str(CostScope.ITEM), (item(budget, "2.1").pk,), task_id=self.mw.world.task(5).pk
        )
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.to_concrete = issue(
            self.mw, [(self.mw.cement, "10")], day=date(2026, 3, 12), destination=concrete
        )
        issue(self.mw, [(self.mw.steel, "100")], day=date(2026, 3, 20),
              destination=DestinationData(str(CostScope.ITEM), (item(budget, "2.2").pk,)))  # fmt: skip
        self.give_back("2", date(2026, 3, 25))

    def give_back(self, quantity: str, day: date) -> MaterialReturn:
        line = MaterialIssueLine.objects.get(issue=self.to_concrete)
        return movements.create_return(
            self.contributor, self.project, self.to_concrete.pk,
            ReturnData(day, (ReturnLineData(line.pk, D(quantity)),)),
        )  # fmt: skip

    def signed(self, end: date) -> ConsumptionCut:
        cut = cuts.create_cut(self.contributor, self.project, end)
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("reporte"))
        return cut

    def approved(self, end: date) -> ConsumptionCut:
        return cuts.approve_cut(self.supervisor, self.project, self.signed(end).pk)

    def test_the_proposal_groups_by_destination_and_returns_subtract(self) -> None:
        self.assertEqual(cuts.default_start(self.mw.ctx(), self.project), date(2026, 3, 10))
        catalog = load_catalog(self.admin, self.project)
        with self.assertNumQueries(5):
            proposal = cuts.build_proposal(
                self.admin, self.project, date(2026, 3, 10), MARCH_END, catalog
            )
        self.assertEqual(
            [(group.label, group.net) for group in proposal.groups],
            [("2.1 Concreto zapatas · tarea 2.1 Columnas", D("260000.00")),
             ("2.2 Acero de refuerzo", D("400000.00")),
             ("Indirecto (proyecto)", D("227500.00"))],
        )  # fmt: skip
        self.assertEqual((proposal.issued, proposal.returned, proposal.total),
                         (D("952500.00"), D("65000.00"), D("887500.00")))  # fmt: skip
        self.assertEqual((len(proposal.issue_ids), len(proposal.return_ids)), (3, 1))
        cement = proposal.groups[0].materials[0]
        self.assertEqual((cement.code, cement.issued_quantity, cement.returned_quantity),
                         ("CEM-01", D("10"), D("2")))  # fmt: skip

    def test_approval_posts_one_cost_per_destination_and_closes_the_period(self) -> None:
        cut = self.approved(MARCH_END)
        entries = CostEntry.objects.filter(consumption_cut=cut)
        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(e.amount for e in entries), D("887500.00"))
        indirect = entries.get(scope="indirect")
        self.assertEqual(
            (indirect.description, indirect.incurred_on, indirect.document_type,
             indirect.document_number, indirect.created_by, indirect.decided_by),
            ("Corte 1 de consumos · Indirecto (proyecto)", MARCH_END, "consumption_cut",
             "Corte 1", self.mw.cw.contributor, self.mw.world.supervisor),
        )  # fmt: skip
        self.assertEqual(MaterialIssue.objects.filter(cut=cut).count(), 3)
        self.assertEqual(MaterialReturn.objects.filter(cut=cut).count(), 1)
        self.assertEqual(cut.summary["fingerprint"]["total"], "887500.00")
        with self.assertRaises(MaterialRejected):
            issue(self.mw, [(self.mw.cement, "1")], day=MARCH_END)
        self.assertEqual(cuts.default_start(self.admin, self.project), date(2026, 4, 1))

    def test_what_is_approved_is_what_was_signed(self) -> None:
        cut = self.signed(MARCH_END)
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 28))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, [cuts.CHANGED])
        again = cuts.recalculate_cut(self.contributor, self.project, cut.pk, MARCH_END)
        self.assertIsNone(again.attachment_id)  # el reporte firmado ya no corresponde
        self.assertEqual((again.total, again.issue_count), (D("920000.00"), 4))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, ["Sube el reporte firmado antes de aprobar."])

    def test_a_negative_destination_and_voiding_only_the_last_cut(self) -> None:
        first = self.approved(MARCH_END)
        late = self.give_back("2", date(2026, 4, 5))  # salió en el corte 1
        second = self.approved(date(2026, 4, 10))
        entry = CostEntry.objects.get(consumption_cut=second)
        self.assertEqual((entry.amount, entry.incurred_on), (D("-65000.00"), date(2026, 4, 10)))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.void_cut(self.admin, self.project, first.pk, "Error")
        self.assertEqual(caught.exception.errors,
                         ["Solo se anula el último corte aprobado (el 2)."])  # fmt: skip
        voided = cuts.void_cut(self.admin, self.project, second.pk, "Devolución mal fechada")
        self.assertEqual(voided.status, "voided")
        self.assertEqual(
            sum(e.amount for e in CostEntry.objects.filter(consumption_cut=second)), D("0")
        )
        late.refresh_from_db()
        self.assertIsNone(late.cut_id)
        self.assertEqual(cuts.default_start(self.admin, self.project), date(2026, 4, 1))

    def test_one_open_cut_and_its_period(self) -> None:
        cut = cuts.create_cut(self.contributor, self.project, MARCH_END)
        with self.assertRaises(MaterialRejected) as caught:
            cuts.create_cut(self.contributor, self.project, MARCH_END)
        self.assertEqual(caught.exception.errors, [cuts.OPEN_CUT])
        cuts.delete_cut(self.contributor, self.project, cut.pk)
        for end in (date(2026, 3, 9), date(2026, 4, 16)):
            with self.subTest(end=end), self.assertRaises(MaterialRejected) as caught:
                cuts.create_cut(self.contributor, self.project, end)
            self.assertEqual(caught.exception.errors,
                             ["El corte empieza el 10/03/2026: termina entre esa fecha y hoy."])  # fmt: skip
        self.assertEqual(cuts.create_cut(self.contributor, self.project, MARCH_END).number, 1)

    def test_permissions_and_self_approval(self) -> None:
        cut = self.signed(MARCH_END)
        with self.assertRaises(PermissionDenied):
            cuts.approve_cut(self.contributor, self.project, cut.pk)
        cuts.recalculate_cut(self.supervisor, self.project, cut.pk, MARCH_END)
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("reporte"))
        with self.assertRaises(MaterialRejected) as caught:
            cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.assertEqual(caught.exception.errors, [SELF_DECISION])
        approved = cuts.approve_cut(self.admin, self.project, cut.pk)
        with self.assertRaises(PermissionDenied):
            cuts.void_cut(self.supervisor, self.project, approved.pk, "No")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_cuts.py`
Expected: ERROR (`cannot import name 'cuts' from 'suno.materials'`).

- [ ] **Step 3: El corte**

`server/suno/materials/cuts.py`:

```python
"""Corte de consumos (costos, pieza 6c, spec 2.6 y 2.7).

- Periodo: empieza el día siguiente al fin del último corte aprobado (sin cortes, en la primera
  salida o devolución sin corte) y termina en la fecha que se elige, nunca en el futuro. Un solo
  corte abierto (borrador o rechazado) por proyecto; el número se asigna al crearlo.
- Contenido: las salidas y devoluciones del proyecto con fecha en el periodo y sin corte,
  agrupadas por destino (`scope`, capítulo o ítems, y tarea); el valor de cada destino es lo
  que salió menos lo devuelto. La devolución va al destino de su salida.
- El borrador guarda un resumen (`summary`): la huella que se compara al aprobar y el detalle
  que imprime el PDF. Recalcular es editar: quita el reporte firmado. Subirlo no es editar.
- Al aprobar (otra persona, con el reporte firmado) se recalcula: si la huella cambió, se
  rechaza ("lo aprobado es lo firmado", como en 6b). Se asigna el corte a sus salidas y
  devoluciones y se escribe un costo aprobado por destino con valor (puede ser negativo), con
  fecha del fin del periodo. El periodo queda cerrado.
- Anular (`reverse`): solo el último aprobado y sin otro corte abierto; reversos con fecha de
  hoy, salidas y devoluciones liberadas y periodo reabierto.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Min, Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments.web import Upload
from suno.budgets.totals import ZERO
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    CostDocumentType,
    CostEntry,
    CostEntryItem,
    CostKind,
    CostStatus,
    MaterialIssue,
    MaterialIssueItem,
    MaterialIssueLine,
    MaterialReturn,
    MaterialReturnLine,
    MaterialStatus,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, load_catalog

from .common import (
    MAX_DESCRIPTION,
    MAX_FILE_NAME,
    MaterialRejected,
    clean_note,
    lock_project,
    locked,
    next_number,
    require_decidable,
    require_editable,
    reverse_entries,
    stored_upload,
)
from .destination import DestinationKey, destination_key, destination_label, key_text
from .movements import closed_until

CHANGED = "Los consumos del periodo cambiaron; revisa el corte y vuelve a cargar el reporte firmado."
MOVED_START = (
    "Hay salidas o devoluciones sin cortar antes del inicio del corte; recalcula el corte."
)
OPEN_CUT = "Ya hay un corte abierto en el proyecto: termínalo o bórralo primero."
NOTHING = "No hay salidas ni devoluciones en el periodo."
QTY_ZERO = Decimal("0.0000")


@dataclass(frozen=True)
class MaterialLine:
    code: str
    name: str
    unit: str
    issued_quantity: Decimal
    issued_value: Decimal
    returned_quantity: Decimal
    returned_value: Decimal

    @property
    def net_quantity(self) -> Decimal:
        return self.issued_quantity - self.returned_quantity

    @property
    def net_value(self) -> Decimal:
        return self.issued_value - self.returned_value


@dataclass(frozen=True)
class CutGroup:
    key: DestinationKey
    label: str
    materials: tuple[MaterialLine, ...]

    @property
    def issued(self) -> Decimal:
        return sum((line.issued_value for line in self.materials), ZERO)

    @property
    def returned(self) -> Decimal:
        return sum((line.returned_value for line in self.materials), ZERO)

    @property
    def net(self) -> Decimal:
        return self.issued - self.returned


@dataclass(frozen=True)
class Proposal:
    start: date
    end: date
    groups: tuple[CutGroup, ...]
    issue_ids: tuple[UUID, ...]
    return_ids: tuple[UUID, ...]

    @property
    def issued(self) -> Decimal:
        return sum((group.issued for group in self.groups), ZERO)

    @property
    def returned(self) -> Decimal:
        return sum((group.returned for group in self.groups), ZERO)

    @property
    def total(self) -> Decimal:
        return self.issued - self.returned

    def fingerprint(self) -> dict[str, Any]:
        """Lo que se compara al aprobar: total, cuántas salidas y devoluciones, y el neto por
        destino."""
        return {
            "total": str(self.total),
            "issues": len(self.issue_ids),
            "returns": len(self.return_ids),
            "groups": {key_text(group.key): str(group.net) for group in self.groups},
        }

    def snapshot(self) -> dict[str, Any]:
        """La huella y el detalle que imprime el PDF (`ConsumptionCut.summary`)."""
        return {
            "fingerprint": self.fingerprint(),
            "groups": [
                {
                    "label": group.label,
                    "issued": str(group.issued),
                    "returned": str(group.returned),
                    "net": str(group.net),
                    "materials": [
                        {
                            "code": line.code,
                            "name": line.name,
                            "unit": line.unit,
                            "issued_quantity": str(line.issued_quantity),
                            "issued_value": str(line.issued_value),
                            "returned_quantity": str(line.returned_quantity),
                            "returned_value": str(line.returned_value),
                        }
                        for line in group.materials
                    ],
                }
                for group in self.groups
            ],
        }


def default_start(ctx: RequestContext, project: Project) -> date | None:
    """El inicio del próximo corte; `None` si no hay nada que cortar."""
    closed = closed_until(ctx, project)
    if closed is not None:
        return closed + timedelta(days=1)
    days = [
        scoped_qs(ctx, MaterialIssue)
        .filter(project=project, cut__isnull=True)
        .aggregate(day=Min("issued_on"))["day"],
        scoped_qs(ctx, MaterialReturn)
        .filter(project=project, cut__isnull=True)
        .aggregate(day=Min("returned_on"))["day"],
    ]
    found = [day for day in days if day is not None]
    return min(found) if found else None


def build_proposal(
    ctx: RequestContext, project: Project, start: date, end: date, catalog: Catalog
) -> Proposal:
    """Las salidas y devoluciones sin corte del periodo, por destino y material. Cinco
    consultas (salidas, devoluciones, ítems de sus destinos y las dos sumas por material)."""
    owners: dict[UUID, tuple[str, UUID | None, UUID | None, str]] = {}
    issue_ids: list[UUID] = []
    for pk, scope, chapter_id, task_id, outline, name in (
        scoped_qs(ctx, MaterialIssue)
        .filter(project=project, cut__isnull=True, issued_on__gte=start, issued_on__lte=end)
        .order_by("number")
        .values_list("pk", "scope", "chapter_id", "task_id", "task__outline_number", "task__name")
    ):
        owners[pk] = (scope, chapter_id, task_id, f"{outline} {name}" if task_id else "")
        issue_ids.append(pk)
    return_ids: list[UUID] = []
    for pk, issue_id, scope, chapter_id, task_id, outline, name in (
        scoped_qs(ctx, MaterialReturn)
        .filter(project=project, cut__isnull=True, returned_on__gte=start, returned_on__lte=end)
        .order_by("number")
        .values_list(
            "pk", "issue_id", "issue__scope", "issue__chapter_id", "issue__task_id",
            "issue__task__outline_number", "issue__task__name",
        )  # fmt: skip
    ):
        owners[issue_id] = (scope, chapter_id, task_id, f"{outline} {name}" if task_id else "")
        return_ids.append(pk)
    if not owners:
        return Proposal(start, end, (), (), ())
    items: dict[UUID, list[UUID]] = defaultdict(list)
    for issue_id, item_id in (
        scoped_qs(ctx, MaterialIssueItem)
        .filter(issue_id__in=list(owners))
        .values_list("issue_id", "item_id")
    ):
        items[issue_id].append(item_id)
    #: destino → material → [cantidad que salió, valor, cantidad devuelta, valor]
    sums: dict[DestinationKey, dict[UUID, list[Decimal]]] = defaultdict(dict)
    names: dict[UUID, tuple[str, str, str]] = {}
    labels: dict[DestinationKey, str] = {}

    def add(issue_id: UUID, material_id: UUID, offset: int, amount: Decimal, value: Decimal) -> None:
        scope, chapter_id, task_id, task = owners[issue_id]
        key = destination_key(scope, chapter_id, items.get(issue_id, ()), task_id)
        labels.setdefault(key, destination_label(catalog, scope, chapter_id, key[2], task))
        row = sums[key].setdefault(material_id, [QTY_ZERO, ZERO, QTY_ZERO, ZERO])
        row[offset] += amount
        row[offset + 1] += value

    for issue_id, material_id, code, name, unit, amount, value in (
        scoped_qs(ctx, MaterialIssueLine)
        .filter(issue_id__in=issue_ids)
        .values("issue_id", "material_id", "material__code", "material__name", "material__unit")
        .annotate(amount=Sum("quantity"), value=Sum("amount"))
        .values_list(
            "issue_id", "material_id", "material__code", "material__name", "material__unit",
            "amount", "value",
        )  # fmt: skip
    ):
        names[material_id] = (code, name, unit)
        add(issue_id, material_id, 0, amount, value)
    for issue_id, material_id, code, name, unit, amount, value in (
        scoped_qs(ctx, MaterialReturnLine)
        .filter(material_return_id__in=return_ids)
        .values(
            "material_return__issue_id", "issue_line__material_id",
            "issue_line__material__code", "issue_line__material__name",
            "issue_line__material__unit",
        )  # fmt: skip
        .annotate(amount=Sum("quantity"), value=Sum("amount"))
        .values_list(
            "material_return__issue_id", "issue_line__material_id",
            "issue_line__material__code", "issue_line__material__name",
            "issue_line__material__unit", "amount", "value",
        )  # fmt: skip
    ):
        names[material_id] = (code, name, unit)
        add(issue_id, material_id, 2, amount, value)
    groups = [
        CutGroup(
            key,
            labels[key],
            tuple(
                sorted(
                    (MaterialLine(*names[material_id], *row) for material_id, row in rows.items()),
                    key=lambda line: line.code,
                )
            ),
        )
        for key, rows in sums.items()
    ]
    groups.sort(key=lambda group: group.label)
    return Proposal(start, end, tuple(groups), tuple(issue_ids), tuple(return_ids))


def cut_metadata(cut: ConsumptionCut) -> dict[str, Any]:
    return {
        "project": str(cut.project_id),
        "cut": str(cut.pk),
        "number": cut.number,
        "start": cut.period_start.isoformat(),
        "end": cut.period_end.isoformat(),
        "total": str(cut.total),
        "status": cut.status,
    }


def proposal_until(
    ctx: RequestContext, project: Project, end: date, catalog: Catalog
) -> Proposal:
    """La propuesta desde el inicio que toca hasta `end`; rechaza un periodo imposible o vacío."""
    start = default_start(ctx, project)
    if start is None:
        raise MaterialRejected(["No hay salidas ni devoluciones sin cortar."])
    if not start <= end <= timezone.localdate():
        raise MaterialRejected(
            [f"El corte empieza el {start:%d/%m/%Y}: termina entre esa fecha y hoy."]
        )
    proposal = build_proposal(ctx, project, start, end, catalog)
    if not proposal.issue_ids and not proposal.return_ids:
        raise MaterialRejected([NOTHING])
    return proposal


def _store(cut: ConsumptionCut, proposal: Proposal) -> None:
    cut.period_start = proposal.start
    cut.period_end = proposal.end
    cut.summary = proposal.snapshot()
    cut.total = proposal.total
    cut.issue_count = len(proposal.issue_ids)
    cut.return_count = len(proposal.return_ids)


def _has_open(ctx: RequestContext, project: Project, exclude: UUID | None = None) -> bool:
    rows = scoped_qs(ctx, ConsumptionCut).filter(project=project, status__in=OPEN_STATES)
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    return rows.exists()


def create_cut(
    ctx: RequestContext, project: Project, period_end: date, *, catalog: Catalog | None = None
) -> ConsumptionCut:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        if _has_open(ctx, project):
            raise MaterialRejected([OPEN_CUT])
        proposal = proposal_until(ctx, project, period_end, catalog)
        cut = ConsumptionCut(
            org_id=ctx.org_id,
            project=project,
            number=next_number(ctx, ConsumptionCut, project),
            status=str(MaterialStatus.DRAFT),
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store(cut, proposal)
        cut.save()
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_CREATED,
            target=f"consumption_cut:{cut.pk}",
            metadata=cut_metadata(cut),
        )
    return cut


def recalculate_cut(
    ctx: RequestContext,
    project: Project,
    cut_id: UUID,
    period_end: date,
    *,
    catalog: Catalog | None = None,
) -> ConsumptionCut:
    """Recalcula y guarda el resumen. Es editar: quita el reporte firmado; un rechazado (solo
    su autor) vuelve a borrador."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        require_editable(ctx, cut, can_update_cost)
        proposal = proposal_until(ctx, project, period_end, catalog)
        resubmitted = cut.status == MaterialStatus.REJECTED
        signed_removed = cut.attachment_id is not None
        _store(cut, proposal)
        cut.status = str(MaterialStatus.DRAFT)
        cut.decided_by = None
        cut.decided_at = None
        cut.decision_note = ""
        cut.attachment = None
        cut.attachment_name = ""
        cut.updated_by_id = ctx.user_id
        cut.save()
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_UPDATED,
            target=f"consumption_cut:{cut.pk}",
            metadata={
                **cut_metadata(cut),
                "resubmitted": resubmitted,
                "signed_removed": signed_removed,
            },
        )
    return cut


def delete_cut(ctx: RequestContext, project: Project, cut_id: UUID) -> None:
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        require_editable(ctx, cut, can_delete_cost)
        metadata = cut_metadata(cut)
        target = f"consumption_cut:{cut.pk}"
        cut.delete()
        audit.record(
            ctx, action=AuditAction.CONSUMPTION_CUT_DELETED, target=target, metadata=metadata
        )


def _require_signable(ctx: RequestContext, cut: ConsumptionCut) -> None:
    if cut.status != MaterialStatus.DRAFT:
        raise MaterialRejected(["El reporte firmado se sube a un corte en borrador."])
    if not can_update_cost(ctx, cut):
        raise PermissionDenied


def attach_signed_report(
    ctx: RequestContext, project: Project, cut_id: UUID, upload: Upload, name: str
) -> ConsumptionCut:
    """Sube el reporte firmado. No es editar: `updated_by` no cambia."""
    current = scoped_qs(ctx, ConsumptionCut).filter(pk=cut_id, project=project).first()
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    blob = stored_upload(upload)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        _require_signable(ctx, cut)
        cut.attachment = blob
        cut.attachment_name = name[:MAX_FILE_NAME]
        cut.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_UPDATED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "signed": True},
        )
    return cut


def _post(ctx: RequestContext, project: Project, cut: ConsumptionCut, proposal: Proposal) -> int:
    """Un costo aprobado por destino con neto distinto de cero (decisión 17 del plan)."""
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for group in proposal.groups:
        if group.net == 0:
            continue
        scope, chapter_id, item_ids, task_id = group.key
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=cut.period_end,
            kind=CostKind.MATERIALS,
            description=f"{cut.code} de consumos · {group.label}"[:MAX_DESCRIPTION],
            amount=group.net,
            third_party="",
            document_type=CostDocumentType.CONSUMPTION_CUT,
            document_number=cut.code,
            scope=scope,
            chapter_id=chapter_id,
            task_id=task_id,
            status=CostStatus.APPROVED,
            created_by_id=cut.created_by_id,
            updated_by_id=cut.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=cut.attachment_id,
            attachment_name=cut.attachment_name,
            consumption_cut=cut,
        )
        entries.append(entry)
        links += [CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=i) for i in item_ids]
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return len(entries)


def approve_cut(
    ctx: RequestContext, project: Project, cut_id: UUID, *, catalog: Catalog | None = None
) -> ConsumptionCut:
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, cut)
        if cut.attachment_id is None:
            raise MaterialRejected(["Sube el reporte firmado antes de aprobar."])
        if default_start(ctx, project) != cut.period_start:
            raise MaterialRejected([MOVED_START])
        proposal = build_proposal(ctx, project, cut.period_start, cut.period_end, catalog)
        if proposal.fingerprint() != cut.summary.get("fingerprint"):
            raise MaterialRejected([CHANGED])
        scoped_qs(ctx, MaterialIssue).filter(pk__in=proposal.issue_ids).update(cut=cut)
        scoped_qs(ctx, MaterialReturn).filter(pk__in=proposal.return_ids).update(cut=cut)
        entries = _post(ctx, project, cut, proposal)
        cut.summary = proposal.snapshot()
        cut.status = str(MaterialStatus.APPROVED)
        cut.decided_by_id = ctx.user_id
        cut.decided_at = timezone.now()
        cut.save(update_fields=["summary", "status", "decided_by", "decided_at"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_APPROVED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "entries": entries},
        )
    return cut


def reject_cut(ctx: RequestContext, project: Project, cut_id: UUID, note: str) -> ConsumptionCut:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, cut)
        cut.status = str(MaterialStatus.REJECTED)
        cut.decided_by_id = ctx.user_id
        cut.decided_at = timezone.now()
        cut.decision_note = reason
        cut.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_REJECTED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "note": reason},
        )
    return cut


def void_cut(ctx: RequestContext, project: Project, cut_id: UUID, note: str) -> ConsumptionCut:
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        lock_project(ctx, project)
        cut = locked(ctx, ConsumptionCut, project, cut_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if cut.status != MaterialStatus.APPROVED:
            raise MaterialRejected(["Solo se anula un corte aprobado."])
        if _has_open(ctx, project):
            raise MaterialRejected(["Hay un corte abierto: apruébalo o bórralo antes de anular."])
        last = (
            scoped_qs(ctx, ConsumptionCut)
            .filter(project=project, status=MaterialStatus.APPROVED)
            .order_by("-number")
            .first()
        )
        if last is None or last.pk != cut.pk:
            number = last.number if last is not None else "—"
            raise MaterialRejected([f"Solo se anula el último corte aprobado (el {number})."])
        reversals = reverse_entries(ctx, project, "consumption_cut", cut, reason)
        scoped_qs(ctx, MaterialIssue).filter(cut=cut).update(cut=None)
        scoped_qs(ctx, MaterialReturn).filter(cut=cut).update(cut=None)
        cut.status = str(MaterialStatus.VOIDED)
        cut.voided_by_id = ctx.user_id
        cut.voided_at = timezone.now()
        cut.void_note = reason
        cut.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_VOIDED,
            target=f"consumption_cut:{cut.pk}",
            metadata={**cut_metadata(cut), "note": reason, "reversals": reversals},
        )
    return cut
```

Nota: en `build_proposal`, `issue_id__in=issue_ids` y `material_return_id__in=return_ids` son listas del periodo (en la prueba de carga, un mes sin cortar: unos cientos de ids). Si la Task 19 muestra que pesa, cambiarlas por los mismos filtros con unión (`issue__project=…, issue__cut__isnull=True, issue__issued_on__range=…`).

Revisión de los números de `test_what_is_approved_is_what_was_signed`: la salida nueva de 1 und a 32.500 suma 32.500,00 → 887.500 + 32.500 = 920.000,00 y 4 salidas.

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_cuts.py`
Expected: PASS (6 pruebas).

- [ ] **Step 5: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): consumption cuts posted to the cost ledger

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Consultas de la pestaña (inventario, kárdex, listas, avisos, bandeja, valor en bodega)

**Files:**
- Create: `server/suno/materials/queries.py`
- Test: `server/suno/materials/tests/test_queries.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_queries.py`:

```python
"""Consultas de la pestaña "Materiales" (spec, 3.2, 3.3 y 4 "Carga"): número fijo de consultas."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.models import PurchaseKind
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.materials import purchases, queries
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.materials.queries import DocumentFilters, InventoryFilters

from .factories import issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class QueryTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.ctx = self.mw.ctx()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00"), (self.mw.sand, "10", "100000.00")])
        stock_in(self.mw, [(self.mw.cement, "50", "33500.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        issue(self.mw, [(self.mw.cement, "3")], day=date(2026, 4, 10))
        make_warehouse(self.mw.world, "Frente norte")

    def test_inventory_in_two_queries_with_filters(self) -> None:
        with self.assertNumQueries(2):
            page = queries.inventory_page(self.ctx, self.project, InventoryFilters())
        self.assertEqual([row.balance.material.code for row in page.rows], ["ARE-01", "CEM-01"])
        self.assertEqual((page.count, page.value), (2, D("5550000.00")))
        cement = page.rows[1]
        self.assertEqual((cement.balance.quantity, cement.average), (D("140"), D("32500.000000")))
        found = queries.inventory_page(
            self.ctx, self.project, InventoryFilters.from_query({"texto": "arena"})
        )
        self.assertEqual([row.balance.material.code for row in found.rows], ["ARE-01"])
        by_category = queries.inventory_page(
            self.ctx, self.project, InventoryFilters.from_query({"categoria": "Cementos"})
        )
        self.assertEqual(by_category.count, 1)
        self.assertEqual(queries.stock_value(self.ctx, self.project), D("5550000.00"))

    def test_kardex_with_running_balance_and_documents(self) -> None:
        with self.assertNumQueries(1):
            sheet = queries.kardex(self.ctx, self.project, self.mw.warehouse, self.mw.cement)
        self.assertEqual([row.document for row in sheet.rows],
                         ["CM-001", "CM-002", "SA-001", "SA-002"])  # fmt: skip
        self.assertEqual(
            [(row.balance_quantity, row.balance_value) for row in sheet.rows],
            [(D("100"), D("3200000.00")), (D("150"), D("4875000.00")),
             (D("143"), D("4647500.00")), (D("140"), D("4550000.00"))],
        )  # fmt: skip
        self.assertEqual((sheet.rows[2].entry, sheet.rows[2].exit), (None, D("7")))
        later = queries.kardex(
            self.ctx, self.project, self.mw.warehouse, self.mw.cement, start=date(2026, 4, 1)
        )
        self.assertEqual([row.document for row in later.rows], ["SA-002"])
        self.assertEqual(later.rows[0].balance_quantity, D("140"))  # el saldo sigue la secuencia

    def test_lists_alerts_and_inbox(self) -> None:
        purchases.create_purchase(
            self.mw.ctx(self.mw.cw.contributor), self.project,
            PurchaseData(str(PurchaseKind.WAREHOUSE), self.mw.warehouse.pk, "Ferretería", "",
                         "invoice", "F-1", date(2026, 4, 12),
                         (PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00")),)),
        )  # fmt: skip
        rows, more = queries.purchase_rows(
            self.ctx, self.project, DocumentFilters.from_query({"estado": "draft"})
        )
        self.assertEqual(([row.code for row in rows], more), (["CM-003"], False))
        issues, _ = queries.issue_rows(
            self.ctx, self.project, DocumentFilters.from_query({"desde": "2026-04-01"})
        )
        self.assertEqual([row.code for row in issues], ["SA-002"])
        with self.assertNumQueries(4):
            alerts = queries.tab_alerts(self.ctx, self.project)
        self.assertEqual(
            [alert.text for alert in alerts],
            ["1 salida sin cortar con más de 31 días (la más vieja, del 10/03/2026).",
             "1 compra en borrador por aprobar."],
        )  # fmt: skip
        supervisor = self.mw.ctx(self.mw.world.supervisor)
        with self.assertNumQueries(3):
            self.assertEqual(queries.inbox_count(supervisor, self.project), 1)
        self.assertEqual(queries.inbox_count(self.mw.ctx(self.mw.cw.contributor), self.project), 0)
        self.assertEqual([p.code for p in queries.inbox_items(supervisor, self.project).purchases],
                         ["CM-003"])  # fmt: skip
```

Números: cemento 150 / 4.875.000,00 − 7 − 3 a 32.500 = 140 / 4.550.000,00; arena 10 / 1.000.000,00; valor en bodega 5.550.000,00.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_queries.py`
Expected: ERROR (`cannot import name 'queries' from 'suno.materials'`).

- [ ] **Step 3: Las consultas**

`server/suno/materials/queries.py`:

```python
"""Consultas de la pestaña "Materiales" (costos, pieza 6c, spec 3.2 y 3.3).

Número fijo de consultas: inventario 2 (totales y la página), kárdex 1, cada lista 1, avisos 4,
bandeja 3, valor en bodega 1. Las listas muestran los `LIST_LIMIT` más recientes del filtro
(decisión 21 del plan); el inventario va paginado de a `PAGE_SIZE`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from math import ceil
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Count, Min, Q, QuerySet, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO
from suno.core.context import RequestContext
from suno.core.models import (
    ConsumptionCut,
    CostScope,
    InventoryAdjustment,
    Material,
    MaterialIssue,
    MaterialPurchase,
    MaterialStatus,
    MaterialTransfer,
    Project,
    PurchaseKind,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.core.scoping import scoped_qs

from .stock import unit_cost

PAGE_SIZE = 100
LIST_LIMIT = 200
KARDEX_LIMIT = 1000
MAX_EXPORT_ROWS = 50_000
STALE_DAYS = 31
UNCUT, CUT = "sin", "con"


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


def _page(value: str | None) -> int:
    try:
        return max(int(value or 1), 1)
    except ValueError:
        return 1


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass(frozen=True)
class InventoryFilters:
    warehouse_id: UUID | None = None
    category: str = ""
    text: str = ""
    page: int = 1

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> InventoryFilters:
        return cls(
            warehouse_id=_uuid(query.get("bodega")),
            category=(query.get("categoria") or "").strip()[:80],
            text=(query.get("texto") or "").strip()[:100],
            page=_page(query.get("pagina")),
        )

    @property
    def query(self) -> str:
        values = {
            "bodega": str(self.warehouse_id) if self.warehouse_id else "",
            "categoria": self.category,
            "texto": self.text,
        }
        return urlencode({key: value for key, value in values.items() if value})


@dataclass(frozen=True)
class InventoryRow:
    balance: StockBalance

    @property
    def average(self) -> Decimal | None:
        balance = self.balance
        return unit_cost(balance.quantity, balance.value) if balance.quantity > 0 else None


@dataclass(frozen=True)
class InventoryPage:
    rows: tuple[InventoryRow, ...]
    number: int
    pages: int
    count: int
    value: Decimal

    @property
    def has_previous(self) -> bool:
        return self.number > 1

    @property
    def has_next(self) -> bool:
        return self.number < self.pages


def _balances(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> QuerySet[StockBalance]:
    """Los pares con movimientos (aunque hoy estén en cero)."""
    rows = scoped_qs(ctx, StockBalance).filter(project=project, last_sequence__gt=0)
    if filters.warehouse_id:
        rows = rows.filter(warehouse_id=filters.warehouse_id)
    if filters.category:
        rows = rows.filter(material__category__iexact=filters.category)
    if filters.text:
        rows = rows.filter(
            Q(material__code__icontains=filters.text) | Q(material__name__icontains=filters.text)
        )
    return rows


def inventory_page(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> InventoryPage:
    rows = _balances(ctx, project, filters)
    totals = rows.aggregate(count=Count("pk"), value=Sum("value"))
    count = totals["count"] or 0
    pages = max(1, ceil(count / PAGE_SIZE))
    number = min(filters.page, pages)
    start = (number - 1) * PAGE_SIZE
    page = (
        rows.select_related("warehouse", "material")
        .order_by("warehouse__name", "material__code")[start : start + PAGE_SIZE]
    )
    return InventoryPage(
        rows=tuple(InventoryRow(balance) for balance in page),
        number=number,
        pages=pages,
        count=count,
        value=totals["value"] or ZERO,
    )


def inventory_rows(
    ctx: RequestContext, project: Project, filters: InventoryFilters
) -> list[InventoryRow]:
    """Todo el inventario filtrado, para el Excel (hasta `MAX_EXPORT_ROWS`)."""
    rows = (
        _balances(ctx, project, filters)
        .select_related("warehouse", "material")
        .order_by("warehouse__name", "material__code")[:MAX_EXPORT_ROWS]
    )
    return [InventoryRow(balance) for balance in rows]


def categories(ctx: RequestContext) -> list[str]:
    return list(
        scoped_qs(ctx, Material)
        .exclude(category="")
        .order_by("category")
        .values_list("category", flat=True)
        .distinct()
    )


def stock_value(ctx: RequestContext, project: Project) -> Decimal:
    """El valor en bodega del proyecto (spec 3.3): inventario, no costo."""
    total = scoped_qs(ctx, StockBalance).filter(project=project).aggregate(value=Sum("value"))
    return total["value"] or ZERO


def document_label(movement: StockMovement) -> str:
    """"CM-001", "SA-003", "DV-001", "TR-002" o "AJ-001" (con `select_related` del origen)."""
    for field in ("purchase", "issue", "material_return", "transfer", "adjustment"):
        document = getattr(movement, field)
        if document is not None:
            return str(document.code)
    return ""


@dataclass(frozen=True)
class KardexRow:
    movement: StockMovement
    document: str
    entry: Decimal | None
    exit: Decimal | None
    balance_quantity: Decimal
    balance_value: Decimal

    @property
    def average(self) -> Decimal | None:
        if self.balance_quantity <= 0:
            return None
        return unit_cost(self.balance_quantity, self.balance_value)


@dataclass(frozen=True)
class Kardex:
    warehouse: Warehouse
    material: Material
    rows: tuple[KardexRow, ...]
    #: Hay más filas en el filtro de las que se muestran (se muestran las últimas).
    truncated: bool


def kardex(
    ctx: RequestContext,
    project: Project,
    warehouse: Warehouse,
    material: Material,
    *,
    start: date | None = None,
    end: date | None = None,
    limit: int = KARDEX_LIMIT,
) -> Kardex:
    """Los movimientos del par en el orden de su secuencia, con el saldo corrido. El filtro por
    fechas solo elige qué filas se muestran (decisión 6 del plan). Una consulta."""
    movements = (
        scoped_qs(ctx, StockMovement)
        .filter(project=project, warehouse=warehouse, material=material)
        .select_related("purchase", "issue", "material_return", "transfer", "adjustment")
        .order_by("sequence")
    )
    quantity, value = Decimal("0"), ZERO
    rows: list[KardexRow] = []
    for movement in movements:
        quantity += movement.quantity
        value += movement.value
        if (start and movement.moved_on < start) or (end and movement.moved_on > end):
            continue
        rows.append(
            KardexRow(
                movement=movement,
                document=document_label(movement),
                entry=movement.quantity if movement.quantity > 0 else None,
                exit=-movement.quantity if movement.quantity < 0 else None,
                balance_quantity=quantity,
                balance_value=value,
            )
        )
    return Kardex(warehouse, material, tuple(rows[-limit:]), len(rows) > limit)


@dataclass(frozen=True)
class DocumentFilters:
    kind: str = ""
    status: str = ""
    start: date | None = None
    end: date | None = None
    warehouse_id: UUID | None = None
    scope: str = ""
    cut: str = ""

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> DocumentFilters:
        kind, status = query.get("tipo", ""), query.get("estado", "")
        scope, cut = query.get("destino", ""), query.get("corte", "")
        return cls(
            kind=kind if kind in PurchaseKind.values else "",
            status=status if status in MaterialStatus.values else "",
            start=_date(query.get("desde")),
            end=_date(query.get("hasta")),
            warehouse_id=_uuid(query.get("bodega")),
            scope=scope if scope in CostScope.values else "",
            cut=cut if cut in (UNCUT, CUT) else "",
        )

    @property
    def query(self) -> str:
        values = {
            "tipo": self.kind,
            "estado": self.status,
            "desde": self.start.isoformat() if self.start else "",
            "hasta": self.end.isoformat() if self.end else "",
            "bodega": str(self.warehouse_id) if self.warehouse_id else "",
            "destino": self.scope,
            "corte": self.cut,
        }
        return urlencode({key: value for key, value in values.items() if value})


def _limited(rows: QuerySet[Any]) -> tuple[list[Any], bool]:
    found = list(rows[: LIST_LIMIT + 1])
    return found[:LIST_LIMIT], len(found) > LIST_LIMIT


def _dates(rows: QuerySet[Any], field: str, filters: DocumentFilters) -> QuerySet[Any]:
    if filters.start:
        rows = rows.filter(**{f"{field}__gte": filters.start})
    if filters.end:
        rows = rows.filter(**{f"{field}__lte": filters.end})
    return rows


def purchase_rows(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> tuple[list[MaterialPurchase], bool]:
    rows = scoped_qs(ctx, MaterialPurchase).filter(project=project)
    if filters.kind:
        rows = rows.filter(kind=filters.kind)
    if filters.status:
        rows = rows.filter(status=filters.status)
    rows = _dates(rows, "purchased_on", filters)
    return _limited(rows.select_related("warehouse", "created_by").order_by("-number"))


def issue_query(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> QuerySet[MaterialIssue]:
    rows = scoped_qs(ctx, MaterialIssue).filter(project=project)
    if filters.warehouse_id:
        rows = rows.filter(warehouse_id=filters.warehouse_id)
    if filters.scope:
        rows = rows.filter(scope=filters.scope)
    if filters.cut == UNCUT:
        rows = rows.filter(cut__isnull=True)
    elif filters.cut == CUT:
        rows = rows.filter(cut__isnull=False)
    return _dates(rows, "issued_on", filters)


def issue_rows(
    ctx: RequestContext, project: Project, filters: DocumentFilters
) -> tuple[list[MaterialIssue], bool]:
    rows = issue_query(ctx, project, filters).select_related("warehouse", "cut", "task")
    return _limited(rows.order_by("-issued_on", "-number"))


def transfer_rows(
    ctx: RequestContext, project: Project
) -> tuple[list[MaterialTransfer], bool]:
    rows = scoped_qs(ctx, MaterialTransfer).filter(project=project)
    return _limited(rows.select_related("source", "target").order_by("-moved_on", "-number"))


def adjustment_rows(
    ctx: RequestContext, project: Project
) -> tuple[list[InventoryAdjustment], bool]:
    rows = scoped_qs(ctx, InventoryAdjustment).filter(project=project)
    return _limited(rows.select_related("warehouse").order_by("-number"))


def cut_rows(ctx: RequestContext, project: Project) -> tuple[list[ConsumptionCut], bool]:
    return _limited(scoped_qs(ctx, ConsumptionCut).filter(project=project).order_by("-number"))


@dataclass(frozen=True)
class Alert:
    text: str
    #: Vista de la pestaña a la que lleva y su consulta ("?corte=sin").
    view: str
    query: str = ""


def tab_alerts(
    ctx: RequestContext, project: Project, *, today: date | None = None
) -> list[Alert]:
    """Spec 3.2, "Avisos de la pestaña". Cuatro consultas."""
    today = today or timezone.localdate()
    alerts: list[Alert] = []
    stale = (
        scoped_qs(ctx, MaterialIssue)
        .filter(
            project=project,
            cut__isnull=True,
            issued_on__lt=today - timedelta(days=STALE_DAYS),
        )
        .aggregate(count=Count("pk"), oldest=Min("issued_on"))
    )
    if stale["count"]:
        alerts.append(
            Alert(
                f"{_plural(stale['count'], 'salida')} sin cortar con más de {STALE_DAYS} días "
                f"(la más vieja, del {stale['oldest']:%d/%m/%Y}).",
                "materials:issues",
                f"?corte={UNCUT}",
            )
        )
    for model, word, view, query in (
        (MaterialPurchase, "compra", "materials:purchases", "?estado=draft"),
        (InventoryAdjustment, "ajuste", "materials:adjustments", ""),
        (ConsumptionCut, "corte", "materials:cuts", ""),
    ):
        drafts = scoped_qs(ctx, model).filter(project=project, status="draft").count()
        if drafts:
            alerts.append(Alert(f"{_plural(drafts, word)} en borrador por aprobar.", view, query))
    return alerts


@dataclass(frozen=True)
class InboxItems:
    purchases: list[MaterialPurchase]
    adjustments: list[InventoryAdjustment]
    cuts: list[ConsumptionCut]

    @property
    def count(self) -> int:
        return len(self.purchases) + len(self.adjustments) + len(self.cuts)


def _waiting(ctx: RequestContext, project: Project) -> tuple[Any, Any, Any]:
    """Borradores de otras personas (ni creados ni editados por quien mira)."""

    def others(model: Any) -> Any:
        return (
            scoped_qs(ctx, model)
            .filter(project=project, status="draft")
            .exclude(created_by_id=ctx.user_id)
            .exclude(updated_by_id=ctx.user_id)
        )

    return others(MaterialPurchase), others(InventoryAdjustment), others(ConsumptionCut)


def inbox_items(ctx: RequestContext, project: Project) -> InboxItems:
    """Para la bandeja "Por aprobar" de Costos (spec 3.3). Tres consultas."""
    purchases, adjustments, cuts = _waiting(ctx, project)
    return InboxItems(
        purchases=list(purchases.select_related("created_by").order_by("number")),
        adjustments=list(adjustments.select_related("created_by", "warehouse").order_by("number")),
        cuts=list(cuts.select_related("created_by").order_by("number")),
    )


def inbox_count(ctx: RequestContext, project: Project) -> int:
    """Para el contador de "Por aprobar" de Costos. Tres consultas."""
    return sum(rows.count() for rows in _waiting(ctx, project))
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_queries.py`
Expected: PASS (3 pruebas).

- [ ] **Step 5: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials
git commit -m "feat(materials): inventory, kardex, lists, alerts and inbox queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Catálogo de materiales en la web (organización) y enlace lateral

**Files:**
- Create: `server/suno/materials/forms.py` (las tareas 13 a 17 le agregan formularios)
- Create: `server/suno/materials/catalog_views.py`, `server/suno/materials/catalog_urls.py`
- Modify: `server/config/urls.py`
- Create: `server/suno/core/templates/suno/materials/{catalog,catalog_form,catalog_import}.html`
- Modify: `server/suno/core/templates/suno/_sidebar.html`, `server/suno/core/templates/suno/projects/base.html`
- Test: `server/suno/materials/tests/test_views_catalog.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views_catalog.py`:

```python
"""Catálogo de materiales en la web (spec, 3.1): lista, formulario, importación y permisos."""

from __future__ import annotations

import unittest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from suno.backoffice.export import to_xlsx_book
from suno.core.models import Material
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import material_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

BASE = "/plataforma/materiales/"


@skip_without_db
class CatalogPageTests(TestCase):
    def setUp(self) -> None:
        self.mw = material_world()
        self.world = self.mw.world

    def test_anyone_with_costs_reads_the_catalog_with_filters(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        page = self.client.get(BASE)
        self.assertContains(page, "CEM-01")
        self.assertNotContains(page, "Nuevo material")
        found = self.client.get(BASE + "?texto=arena")
        self.assertContains(found, "ARE-01")
        self.assertNotContains(found, "CEM-01")
        self.assertNotContains(self.client.get(BASE + "?estado=inactivos"), "CEM-01")
        self.client.force_login(self.world.agent)  # sin la sección Costos
        self.assertEqual(self.client.get(BASE).status_code, 404)

    def test_create_edit_and_deactivate(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.get(BASE + "nuevo/").status_code, 403)
        self.client.force_login(self.world.admin)
        form = {"code": "LAD-01", "name": "Ladrillo tolete", "unit": "und", "category": "",
                "active": "on"}  # fmt: skip
        self.assertRedirects(self.client.post(BASE + "nuevo/", form), BASE)
        again = self.client.post(BASE + "nuevo/", {**form, "code": "lad-01"})
        self.assertContains(again, "Ya hay un material con el código lad-01.")
        brick = Material.objects.get(code="LAD-01")
        edited = {**form, "name": "Ladrillo macizo"}
        del edited["active"]  # casilla sin marcar: se desactiva
        self.client.post(f"{BASE}{brick.pk}/editar/", edited)
        brick.refresh_from_db()
        self.assertEqual((brick.name, brick.active), ("Ladrillo macizo", False))
        other = material_world("Otra constructora")
        self.assertEqual(self.client.get(f"{BASE}{other.cement.pk}/editar/").status_code, 404)

    def test_import_preview_and_apply(self) -> None:
        self.client.force_login(self.world.admin)
        content = to_xlsx_book([("Materiales", ["Código", "Nombre", "Unidad", "Categoría"],
                                 [["LAD-01", "Ladrillo", "und", "Mampostería"]])])  # fmt: skip
        upload = SimpleUploadedFile("materiales.xlsx", content)
        page = self.client.post(BASE + "importar/", {"file": upload})
        self.assertContains(page, "1 nuevo")
        token = page.context["result"].token
        self.assertRedirects(self.client.post(BASE + "importar/aplicar/", {"token": token}), BASE)
        self.assertTrue(Material.objects.filter(code="LAD-01").exists())
        template = self.client.get(BASE + "plantilla.xlsx")
        self.assertEqual(template.status_code, 200)

    def test_the_sidebar_links_the_catalog(self) -> None:
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get("/plataforma/proyectos/"), f'href="{BASE}"')
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_catalog.py`
Expected: FAIL (404 en `/plataforma/materiales/`).

- [ ] **Step 3: Formularios**

`server/suno/materials/forms.py`:

```python
"""Formularios de materiales (costos, pieza 6c, spec 3). Sin JavaScript, como 6a y 6b: las
líneas son filas de un formset y "Agregar líneas" vuelve a pintar el formulario con filas de
más; "Recalcular" calcula en el servidor sin guardar. El material se elige con un `datalist`
("CEM-01 · Cemento gris 50 kg", o solo su código); el destino es un texto
(`destination.parse_destination`) y la tarea de una fila, su número de esquema."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django import forms
from django.db.models import Q
from django.http import QueryDict

from suno.attachments.web import ACCEPT
from suno.core.context import RequestContext
from suno.core.models import Material
from suno.core.scoping import scoped_qs

from .catalog_services import MAX_CATEGORY, MAX_CODE, MAX_NAME, MAX_UNIT, MaterialData

#: Filas vacías que agrega "Agregar líneas" y filas de un formulario nuevo.
ADD_ROWS = 3
NEW_ROWS = 3
LINES = "lineas"


def date_input() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def plain(value: Any) -> str:
    """Un decimal sin ceros de sobra para un `<input>`: 10.0000 → "10"."""
    return format(value.normalize(), "f") if value is not None else ""


class MaterialIndex:
    """Los materiales que se ofrecen: activos, más los que ya tiene el documento."""

    def __init__(self, materials: Sequence[Material]) -> None:
        self.materials = list(materials)
        self._labels = {material.label: material for material in self.materials}
        self._codes = {material.code.casefold(): material for material in self.materials}

    def find(self, text: str) -> Material | None:
        value = text.strip()
        return self._labels.get(value) or self._codes.get(value.casefold())


def material_index(ctx: RequestContext, include: Sequence[Any] = ()) -> MaterialIndex:
    return MaterialIndex(
        list(
            scoped_qs(ctx, Material)
            .filter(Q(active=True) | Q(pk__in=list(include)))
            .order_by("code")
        )
    )


def more_rows(data: QueryDict, action: str) -> QueryDict | None:
    """El POST con filas vacías de más si se pulsó "Agregar líneas"; si no, None."""
    if action != LINES:
        return None
    copy = data.copy()
    key = f"{LINES}-TOTAL_FORMS"
    try:
        total = int(copy.get(key, "0"))
    except ValueError:
        total = 0
    copy[key] = str(total + ADD_ROWS)
    return copy


class CatalogMaterialForm(forms.Form):
    code = forms.CharField(label="Código", max_length=MAX_CODE)
    name = forms.CharField(label="Nombre", max_length=MAX_NAME)
    unit = forms.CharField(
        label="Unidad",
        max_length=MAX_UNIT,
        widget=forms.TextInput(attrs={"placeholder": "m3, kg, und…"}),
    )
    category = forms.CharField(label="Categoría", max_length=MAX_CATEGORY, required=False)
    active = forms.BooleanField(label="Activo", required=False)

    def material_data(self) -> MaterialData:
        data = self.cleaned_data
        return MaterialData(
            data["code"], data["name"], data["unit"], data.get("category") or "", data["active"]
        )


class SupportForm(forms.Form):
    """Un soporte (factura, acta de conteo o reporte firmado)."""

    file = forms.FileField(
        label="Archivo (PDF, JPG, PNG o WebP)", widget=forms.FileInput(attrs={"accept": ACCEPT})
    )
```

- [ ] **Step 4: Vistas del catálogo**

`server/suno/materials/catalog_views.py`:

```python
"""Catálogo de materiales de la organización en la web (costos, pieza 6c, spec 3.1).

Lo ve cualquier rol de Costos (`can_read_material_catalog`; si no, 404). Crear, editar e
importar piden `can_manage_material_catalog` (si no, 403)."""

from __future__ import annotations

from typing import cast
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.excel import MAX_BYTES, ExcelRejected, check_file_name
from suno.core.context import Principal, RequestContext, resolve_request_context
from suno.core.download import content_disposition
from suno.core.models import AppUser, Material
from suno.core.policies import can_manage_material_catalog, can_read_material_catalog
from suno.core.scoping import scoped_qs
from suno.projects.views import _render

from . import catalog_import
from .catalog_services import create_material, update_material
from .common import MaterialRejected
from .forms import CatalogMaterialForm
from .queries import categories

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATES = {"activos": "Activos", "inactivos": "Inactivos", "todos": "Todos"}


def _ctx(request: HttpRequest) -> RequestContext:
    user = cast(AppUser, request.user)
    ctx = resolve_request_context(Principal(user_id=user.pk, org_id=user.org_id))
    if not can_read_material_catalog(ctx, ctx.org_id):
        raise Http404
    return ctx


def _manager(request: HttpRequest) -> RequestContext:
    ctx = _ctx(request)
    if not can_manage_material_catalog(ctx, ctx.org_id):
        raise PermissionDenied
    return ctx


def _page(request: HttpRequest, ctx: RequestContext, template: str, **context: object) -> HttpResponse:
    return _render(
        request,
        ctx,
        template,
        {
            "sidebar_active": "materials",
            "can_manage_catalog": can_manage_material_catalog(ctx, ctx.org_id),
            **context,
        },
    )


@login_required(login_url="platform_login")
@require_GET
def catalog_list(request: HttpRequest) -> HttpResponse:
    ctx = _ctx(request)
    text = (request.GET.get("texto") or "").strip()[:100]
    category = (request.GET.get("categoria") or "").strip()[:80]
    state = request.GET.get("estado") or "activos"
    state = state if state in STATES else "activos"
    rows = scoped_qs(ctx, Material).order_by("code")
    if text:
        rows = rows.filter(Q(code__icontains=text) | Q(name__icontains=text))
    if category:
        rows = rows.filter(category__iexact=category)
    if state != "todos":
        rows = rows.filter(active=state == "activos")
    page = Paginator(rows, 100).get_page(request.GET.get("pagina"))
    query = urlencode({k: v for k, v in (("texto", text), ("categoria", category),
                                         ("estado", state)) if v})  # fmt: skip
    return _page(
        request, ctx, "suno/materials/catalog.html", page=page, text=text, category=category,
        state=state, states=STATES, categories=categories(ctx), query=query,
    )  # fmt: skip


def _form(request: HttpRequest, material: Material | None) -> HttpResponse:
    ctx = _manager(request)
    errors: list[str] = []
    if request.method == "POST":
        form = CatalogMaterialForm(request.POST)
        if form.is_valid():
            try:
                if material is None:
                    create_material(ctx, form.material_data())
                else:
                    update_material(ctx, material.pk, form.material_data())
            except MaterialRejected as exc:
                errors = exc.errors
            else:
                messages.success(request, "Material guardado.")
                return redirect("materials_catalog:list")
    elif material is None:
        form = CatalogMaterialForm(initial={"active": True})
    else:
        form = CatalogMaterialForm(
            initial={
                "code": material.code,
                "name": material.name,
                "unit": material.unit,
                "category": material.category,
                "active": material.active,
            }
        )
    return _page(
        request, ctx, "suno/materials/catalog_form.html", form=form, material=material,
        errors=errors,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_new(request: HttpRequest) -> HttpResponse:
    return _form(request, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_edit(request: HttpRequest, material_id: UUID) -> HttpResponse:
    material = scoped_qs(_ctx(request), Material).filter(pk=material_id).first()
    if material is None:
        raise Http404
    return _form(request, material)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def catalog_import_upload(request: HttpRequest) -> HttpResponse:
    ctx = _manager(request)
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
                result = catalog_import.preview(ctx, upload.read())
    actions = (catalog_import.NEW, catalog_import.UPDATE, catalog_import.SAME)
    counts = [(action, result.count(action)) for action in actions] if result else []
    return _page(
        request, ctx, "suno/materials/catalog_import.html", errors=errors, result=result,
        counts=counts,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def catalog_import_apply(request: HttpRequest) -> HttpResponse:
    ctx = _manager(request)
    try:
        created, updated = catalog_import.apply(ctx, request.POST.get("token", ""))
    except MaterialRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return redirect("materials_catalog:import")
    messages.success(request, f"Catálogo importado: {created} nuevos y {updated} actualizados.")
    return redirect("materials_catalog:list")


@login_required(login_url="platform_login")
@require_GET
def catalog_template(request: HttpRequest) -> HttpResponse:
    _manager(request)
    response = HttpResponse(catalog_import.template_bytes(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        "plantilla-materiales", ".xlsx", fallback="plantilla-materiales"
    )
    return response
```

`server/suno/materials/catalog_urls.py`:

```python
"""Montadas bajo `plataforma/materiales/` (config/urls.py)."""

from django.urls import path

from . import catalog_views

app_name = "materials_catalog"

urlpatterns = [
    path("", catalog_views.catalog_list, name="list"),
    path("nuevo/", catalog_views.catalog_new, name="new"),
    path("<uuid:material_id>/editar/", catalog_views.catalog_edit, name="edit"),
    path("importar/", catalog_views.catalog_import_upload, name="import"),
    path("importar/aplicar/", catalog_views.catalog_import_apply, name="import_apply"),
    path("plantilla.xlsx", catalog_views.catalog_template, name="template"),
]
```

En `server/config/urls.py`, después de `path("plataforma/equipos/", …)`:

```python
    path("plataforma/materiales/", include("suno.materials.catalog_urls")),
```

- [ ] **Step 5: Plantillas y enlace lateral**

En `server/suno/core/templates/suno/projects/base.html`, cambiar `{% include "suno/_sidebar.html" with active="projects" %}` por `{% include "suno/_sidebar.html" with active=sidebar_active|default:"projects" %}`.

En `server/suno/core/templates/suno/_sidebar.html`, después de la línea de Equipos:

```html
        {% if navigation.COSTS %}<a {% if active == "materials" %}class="active" {% endif %}href="{% url 'materials_catalog:list' %}"><svg class="ui-icon" aria-hidden="true"><use href="{{ suno_icons }}#layers"></use></svg> Materiales</a>{% endif %}
```

`server/suno/core/templates/suno/materials/catalog.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Materiales{% endblock %}
{% block content %}
<section class="workspace-heading">
  <div><p class="editorial-label">Costos</p><h1>Catálogo de materiales</h1><p>Lo comparten todos los proyectos de la organización.</p></div>
</section>
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros del catálogo">
    <label>Código o nombre <input name="texto" value="{{ text }}" maxlength="100"></label>
    <label>Categoría <select name="categoria"><option value="">Todas</option>{% for c in categories %}<option value="{{ c }}" {% if c == category %}selected{% endif %}>{{ c }}</option>{% endfor %}</select></label>
    <label>Estado <select name="estado">{% for key, label in states.items %}<option value="{{ key }}" {% if key == state %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Filtrar</button>
  </form>
  {% if can_manage_catalog %}<p class="ctr-actions"><a class="button" href="{% url 'materials_catalog:new' %}">Nuevo material</a> <a class="text-link" href="{% url 'materials_catalog:import' %}">Importar Excel</a></p>{% endif %}
</section>
<section class="pj-panel">
  {% if page.object_list %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <caption class="sr-only">Materiales del catálogo</caption>
    <thead><tr><th>Código</th><th>Nombre</th><th>Unidad</th><th>Categoría</th><th>Estado</th></tr></thead>
    <tbody>
    {% for m in page.object_list %}
      <tr>
        <td>{% if can_manage_catalog %}<a href="{% url 'materials_catalog:edit' m.pk %}">{{ m.code }}</a>{% else %}{{ m.code }}{% endif %}</td>
        <td class="pj-name">{{ m.name }}</td><td>{{ m.unit }}</td><td class="pj-wrap">{{ m.category|default:"—" }}</td>
        <td>{% if m.active %}Activo{% else %}<span class="pj-muted">Inactivo</span>{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  <nav class="cst-pages" aria-label="Páginas">
    {% if page.has_previous %}<a href="?{{ query }}&amp;pagina={{ page.previous_page_number }}">Anterior</a>{% endif %}
    <span>Página {{ page.number }} de {{ page.paginator.num_pages }} · {{ page.paginator.count }} material{{ page.paginator.count|pluralize:"es" }}</span>
    {% if page.has_next %}<a href="?{{ query }}&amp;pagina={{ page.next_page_number }}">Siguiente</a>{% endif %}
  </nav>
  {% else %}<p class="pj-empty">No hay materiales con estos filtros.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/catalog_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}{% if material %}Editar {{ material.code }}{% else %}Nuevo material{% endif %}{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'materials_catalog:list' %}">Catálogo de materiales</a></p><h1>{% if material %}Editar {{ material.code }}{% else %}Nuevo material{% endif %}</h1></div></section>
<section class="pj-panel">
  <form method="post" class="pj-form ctr-head">
    {% csrf_token %}
    {% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}
    <label>{{ form.code.label }} {{ form.code }}</label>
    <label>{{ form.name.label }} {{ form.name }}</label>
    <label>{{ form.unit.label }} {{ form.unit }}</label>
    <label>{{ form.category.label }} {{ form.category }}</label>
    <label class="cst-check">{{ form.active }} {{ form.active.label }}</label>
    <p class="pj-muted">Un material inactivo no se ofrece en documentos nuevos, pero sigue en el kárdex. La unidad de un material con movimientos no se cambia.</p>
    <div class="ctr-actions"><button class="button" type="submit">Guardar</button> <a href="{% url 'materials_catalog:list' %}">Cancelar</a></div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/catalog_import.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Importar materiales{% endblock %}
{% block content %}
<section class="workspace-heading"><div><p class="editorial-label"><a href="{% url 'materials_catalog:list' %}">Catálogo de materiales</a></p><h1>Importar desde Excel</h1></div></section>
<section class="pj-panel">
  <p><a href="{% url 'materials_catalog:template' %}">Descarga la plantilla</a>: hoja «Materiales» con Código, Nombre, Unidad y Categoría. Los códigos nuevos se crean; los que ya existen se actualizan.</p>
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
  <table class="pj-table mat-table">
    <thead><tr><th>Fila</th><th>Código</th><th>Nombre</th><th>Unidad</th><th>Categoría</th><th>Qué pasa</th></tr></thead>
    <tbody>{% for row in result.rows %}<tr><td>{{ row.line }}</td><td>{{ row.data.code }}</td><td class="pj-name">{{ row.data.name }}</td><td>{{ row.data.unit }}</td><td class="pj-wrap">{{ row.data.category|default:"—" }}</td><td>{{ row.action }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% endif %}
  {% if result.token %}
  <form method="post" action="{% url 'materials_catalog:import_apply' %}">{% csrf_token %}<input type="hidden" name="token" value="{{ result.token }}"><button class="button" type="submit">Cargar al catálogo</button></form>
  {% endif %}
</section>
{% endif %}
{% endblock %}
```

Con el archivo de la prueba, el resumen dice "1 nuevo · 0 actualiza · 0 sin cambios".

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_catalog.py suno/projects/tests/test_views.py`
Expected: PASS.

- [ ] **Step 7: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials config && ruff check suno/materials config && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/config/urls.py server/suno/core/templates
git commit -m "feat(materials): organization material catalog pages and sidebar link

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Pestaña "Materiales": cabecera, inventario, kárdex y bodegas

**Files:**
- Create: `server/suno/materials/access.py`, `server/suno/materials/views.py`, `server/suno/materials/urls.py`, `server/suno/materials/export.py`
- Modify: `server/suno/materials/forms.py` (`WarehouseForm`)
- Modify: `server/config/urls.py`, `server/suno/core/templates/suno/projects/_header.html`
- Create: `server/suno/core/templates/suno/materials/{_nav,_alerts,inventory,kardex,warehouses}.html`
- Modify: `server/suno/core/static/suno/css/projects.css`, `server/suno/core/templates/suno/projects/base.html` (`?v=20261115-materials-v1`)
- Test: `server/suno/materials/tests/test_views.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views.py`:

```python
"""Pestaña "Materiales" (spec, 3.2 y 3.5): acceso, inventario, kárdex, bodegas y Excel."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from django.test import TestCase

from suno.core.models import AuditEvent, Project, Warehouse
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class MaterialsTabTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.world = self.mw.world
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/"

    def test_access_follows_the_costs_section(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        page = self.client.get(self.base)
        self.assertContains(page, "CEM-01")
        self.assertContains(page, "2.976.000,00")  # 93 und a 32.000
        self.assertContains(page, "1 salida sin cortar con más de 31 días")
        self.assertContains(self.client.get(f"/plataforma/proyectos/{self.mw.project.pk}/costos/"),
                            f'href="{self.base}"')  # fmt: skip
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other = material_world("Otra constructora")
        self.client.force_login(self.world.admin)
        self.assertEqual(
            self.client.get(f"/plataforma/proyectos/{other.project.pk}/materiales/").status_code,
            404,
        )

    def test_kardex_of_a_material_in_a_warehouse(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        url = f"{self.base}kardex/{self.mw.warehouse.pk}/{self.mw.cement.pk}/"
        page = self.client.get(url)
        self.assertContains(page, "CM-001")
        self.assertContains(page, "SA-001")
        self.assertContains(page, "2.976.000,00")
        elsewhere = Project.objects.create(org=self.world.org, name="Otra obra",
                                           created_by=self.world.admin)  # fmt: skip
        foreign = make_warehouse(self.world, "Bodega ajena", project=elsewhere)
        bad = [
            f"{self.base}kardex/{foreign.pk}/{self.mw.cement.pk}/",
            f"{self.base}kardex/{self.mw.warehouse.pk}/{material_world('Otra').cement.pk}/",
        ]
        for wrong in bad:
            with self.subTest(url=wrong):
                self.assertEqual(self.client.get(wrong).status_code, 404)
        sheet = self.client.get(url + "kardex.xlsx")
        self.assertEqual(sheet.status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="materials.exported").exists())

    def test_inventory_export(self) -> None:
        self.client.force_login(self.mw.cw.viewer)
        response = self.client.get(self.base + "inventario.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"PK"))

    def test_warehouses_are_created_renamed_and_deactivated(self) -> None:
        self.client.force_login(self.mw.cw.contributor)
        url = self.base + "bodegas/"
        self.assertRedirects(self.client.post(url, {"name": "Frente norte"}), url)
        north = Warehouse.objects.get(name="Frente norte")
        self.client.post(f"{url}{north.pk}/", {"accion": "renombrar", "name": "Frente sur"})
        self.client.post(f"{url}{north.pk}/", {"accion": "desactivar"})
        north.refresh_from_db()
        self.assertEqual((north.name, north.active), ("Frente sur", False))
        self.client.force_login(self.world.admin)
        page = self.client.post(f"{url}{self.mw.warehouse.pk}/", {"accion": "desactivar"},
                                follow=True)  # fmt: skip
        self.assertContains(page, "La bodega tiene inventario")
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.post(url, {"name": "X"}).status_code, 403)
```

Números: 100 und a 32.000 = 3.200.000,00; salen 7 → 93 / 2.976.000,00.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views.py`
Expected: FAIL (404 en `/materiales/` del proyecto).

- [ ] **Step 3: Acceso**

`server/suno/materials/access.py`:

```python
"""Quién ve los materiales del proyecto y cómo se resuelven los objetos de la URL.

Son de la sección Costos (spec 3.5): el mismo 404 de la 6a para quien no los lee. Todo id de
otra organización, otro proyecto u otra bodega es 404."""

from __future__ import annotations

from uuid import UUID

from django.db import models
from django.http import Http404

from suno.core.context import RequestContext
from suno.core.models import Material, Project, Warehouse
from suno.core.scoping import scoped_qs
from suno.costs.access import get_costs_project


def get_materials_project(ctx: RequestContext, project_id: UUID) -> Project:
    return get_costs_project(ctx, project_id)


def get_document[M: models.Model](
    ctx: RequestContext, model: type[M], project: Project, pk: UUID, *related: str
) -> M:
    found = scoped_qs(ctx, model).select_related(*related).filter(pk=pk, project=project).first()
    if found is None:
        raise Http404
    return found


def get_warehouse(ctx: RequestContext, project: Project, warehouse_id: UUID) -> Warehouse:
    return get_document(ctx, Warehouse, project, warehouse_id)


def get_material(ctx: RequestContext, material_id: UUID) -> Material:
    found = scoped_qs(ctx, Material).filter(pk=material_id).first()
    if found is None:
        raise Http404
    return found
```

- [ ] **Step 4: Excel del inventario y del kárdex**

`server/suno/materials/export.py` (la Task 17 le agrega las salidas y el corte):

```python
"""Excel de materiales (costos, pieza 6c, spec 3.4). Todo texto de usuario pasa por
`safe_cell`; los valores van como número y las fechas en ISO."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx

from .queries import InventoryRow, Kardex

INVENTORY_HEADER = [
    "bodega", "codigo", "material", "unidad", "categoria", "cantidad", "promedio", "valor",
]  # fmt: skip
KARDEX_HEADER = [
    "secuencia", "fecha", "tipo", "documento", "entrada", "salida", "costo_unitario", "valor",
    "saldo", "valor_saldo", "promedio",
]  # fmt: skip


def number(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def inventory_workbook(rows: Sequence[InventoryRow]) -> bytes:
    return to_xlsx(
        INVENTORY_HEADER,
        [
            [
                safe_cell(row.balance.warehouse.name),
                safe_cell(row.balance.material.code),
                safe_cell(row.balance.material.name),
                safe_cell(row.balance.material.unit),
                safe_cell(row.balance.material.category),
                number(row.balance.quantity),
                number(row.average),
                number(row.balance.value),
            ]
            for row in rows
        ],
        sheet_name="Inventario",
    )


def kardex_workbook(sheet: Kardex) -> bytes:
    return to_xlsx(
        KARDEX_HEADER,
        [
            [
                row.movement.sequence,
                row.movement.moved_on.isoformat(),
                row.movement.get_kind_display(),
                safe_cell(row.document),
                number(row.entry),
                number(row.exit),
                number(row.movement.unit_cost),
                number(row.movement.value),
                number(row.balance_quantity),
                number(row.balance_value),
                number(row.average),
            ]
            for row in sheet.rows
        ],
        sheet_name="Kárdex",
    )
```

- [ ] **Step 5: Formulario de bodega**

Al final de `server/suno/materials/forms.py`:

```python
class WarehouseForm(forms.Form):
    name = forms.CharField(label="Nombre", max_length=120)
```

- [ ] **Step 6: Vistas, URL y cabecera**

`server/suno/materials/views.py`:

```python
"""Pestaña "Materiales" del proyecto (costos, pieza 6c, spec 3.2): inventario, kárdex y
bodegas, y las piezas que comparten las demás vistas (`views_*`).

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_materials_project`). Quien
los lee pero no tiene la capacidad de una acción recibe 403. Las reglas viven en los servicios:
aquí solo se traducen a mensajes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.download import content_disposition
from suno.core.models import Project, Warehouse
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from . import warehouses
from .access import get_material, get_materials_project, get_warehouse
from .common import MaterialRejected
from .export import inventory_workbook, kardex_workbook
from .forms import WarehouseForm
from .queries import (
    MAX_EXPORT_ROWS,
    InventoryFilters,
    categories,
    inventory_page,
    inventory_rows,
    kardex,
    tab_alerts,
)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def tab_context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    """Permisos de la pestaña y sus avisos (cuatro consultas)."""
    return {
        **_project_context(ctx, project),
        "can_create_material": can_create_costs(ctx, project),
        "can_decide_material": can_approve_costs(ctx, project),
        "can_void_material": can_reverse_costs(ctx, project),
        "can_export_material": can_export_costs(ctx, project),
        "material_alerts": tab_alerts(ctx, project),
    }


def page(
    request: HttpRequest, ctx: RequestContext, project: Project, template: str, **context: Any
) -> HttpResponse:
    return _render(request, ctx, template, {**tab_context(ctx, project), **context})


def download(url: str) -> HttpResponse:
    """Como el soporte del libro (6a): URL prefirmada; `local://` se arma a mano."""
    scheme = urlsplit(url).scheme
    if scheme == "local":
        response = HttpResponse(status=302)
        response["Location"] = url
        return response
    if scheme == "https" or (scheme == "http" and settings.DEBUG):
        return redirect(url)
    raise Http404


def xlsx(
    ctx: RequestContext,
    project: Project,
    content: bytes,
    stem: str,
    what: str,
    **metadata: Any,
) -> HttpResponse:
    """Un Excel auditado (`materials.exported`)."""
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.MATERIALS_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"what": what, **metadata},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(stem, ".xlsx", fallback=what)
    return response


def run(
    request: HttpRequest, action: Callable[[], Any], success: str
) -> bool:
    """Ejecuta un servicio y deja el mensaje; `True` si salió bien."""
    try:
        action()
    except MaterialRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
        return False
    messages.success(request, success)
    return True


@login_required(login_url="platform_login")
@require_GET
def inventory(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = InventoryFilters.from_query(request.GET)
    return page(
        request, ctx, project, "suno/materials/inventory.html",
        filters=filters,
        result=inventory_page(ctx, project, filters),
        warehouses=scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"),
        categories=categories(ctx),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def export_inventory(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = InventoryFilters.from_query(request.GET)
    rows = inventory_rows(ctx, project, filters)
    return xlsx(
        ctx, project, inventory_workbook(rows), f"inventario-{project.code or project.name}",
        "inventario", rows=len(rows), filters=filters.query,
    )  # fmt: skip


def _kardex(request: HttpRequest, project_id: UUID, warehouse_id: UUID, material_id: UUID,
            limit: int) -> tuple[RequestContext, Project, Any]:  # fmt: skip
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    sheet = kardex(
        ctx, project, get_warehouse(ctx, project, warehouse_id), get_material(ctx, material_id),
        start=_parse_date(request.GET.get("desde")), end=_parse_date(request.GET.get("hasta")),
        limit=limit,
    )  # fmt: skip
    return ctx, project, sheet


@login_required(login_url="platform_login")
@require_GET
def kardex_page(
    request: HttpRequest, project_id: UUID, warehouse_id: UUID, material_id: UUID
) -> HttpResponse:
    ctx, project, sheet = _kardex(request, project_id, warehouse_id, material_id, 1000)
    return page(request, ctx, project, "suno/materials/kardex.html", sheet=sheet,
                start=request.GET.get("desde", ""), end=request.GET.get("hasta", ""))  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def export_kardex(
    request: HttpRequest, project_id: UUID, warehouse_id: UUID, material_id: UUID
) -> HttpResponse:
    ctx, project, sheet = _kardex(request, project_id, warehouse_id, material_id, MAX_EXPORT_ROWS)
    return xlsx(
        ctx, project, kardex_workbook(sheet), f"kardex-{sheet.material.code}", "kardex",
        material=str(sheet.material.pk), warehouse=str(sheet.warehouse.pk),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def warehouse_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = WarehouseForm(request.POST or None)
    if request.method == "POST":
        if not can_create_costs(ctx, project):
            raise PermissionDenied
        if form.is_valid() and run(
            request,
            lambda: warehouses.create_warehouse(ctx, project, form.cleaned_data["name"]),
            "Bodega creada.",
        ):
            return redirect("materials:warehouses", project.pk)
    rows = list(scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"))
    return page(
        request, ctx, project, "suno/materials/warehouses.html", form=form,
        rows=[(row, can_update_cost(ctx, row)) for row in rows],
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def warehouse_update(request: HttpRequest, project_id: UUID, warehouse_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    get_warehouse(ctx, project, warehouse_id)
    action = request.POST.get("accion", "")
    name = request.POST.get("name", "")
    if action == "renombrar":
        run(
            request,
            lambda: warehouses.rename_warehouse(ctx, project, warehouse_id, name),
            "Bodega renombrada.",
        )
    elif action in ("desactivar", "activar"):
        active = action == "activar"
        run(
            request,
            lambda: warehouses.set_warehouse_active(ctx, project, warehouse_id, active),
            "Bodega activada." if active else "Bodega desactivada.",
        )
    return redirect("materials:warehouses", project.pk)
```

`server/suno/materials/urls.py` (las tareas 14 a 17 le agregan sus rutas):

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/materiales/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "materials"

K = "kardex/<uuid:warehouse_id>/<uuid:material_id>/"

urlpatterns = [
    path("", views.inventory, name="inventory"),
    path("inventario.xlsx", views.export_inventory, name="export_inventory"),
    path(K, views.kardex_page, name="kardex"),
    path(K + "kardex.xlsx", views.export_kardex, name="export_kardex"),
    path("bodegas/", views.warehouse_list, name="warehouses"),
    path("bodegas/<uuid:warehouse_id>/", views.warehouse_update, name="warehouse_update"),
]
```

En `server/config/urls.py`, después del bloque de `contratos/`:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/materiales/",
        include("suno.materials.urls"),
    ),
```

En `server/suno/core/templates/suno/projects/_header.html`, después de la pestaña Contratos:

```html
      {% if can_view_costs %}<a href="{% url 'materials:inventory' project.pk %}" {% if tab == "materials" %}aria-current="page"{% endif %}>Materiales</a>{% endif %}
```

- [ ] **Step 7: Plantillas**

`server/suno/core/templates/suno/materials/_nav.html` (las tareas 14 a 17 agregan sus enlaces en el lugar marcado):

```html
<nav class="pj-tabs mat-nav" aria-label="Materiales">
  <a href="{% url 'materials:inventory' project.pk %}" {% if sub == "inventory" %}aria-current="page"{% endif %}>Inventario</a>
  {# compras, salidas, traslados, ajustes, cortes #}
  <a href="{% url 'materials:warehouses' project.pk %}" {% if sub == "warehouses" %}aria-current="page"{% endif %}>Bodegas</a>
  <a href="{% url 'materials_catalog:list' %}">Catálogo</a>
</nav>
{% include "suno/materials/_alerts.html" %}
```

`server/suno/core/templates/suno/materials/_alerts.html` (sin enlaces todavía: las vistas a las que apuntan `alert.view` llegan en las tareas 14 a 17; la Task 17 agrega el enlace):

```html
{% if material_alerts %}
<details class="pj-warning ctr-warning" open>
  <summary>{{ material_alerts|length }} aviso{{ material_alerts|length|pluralize:"s" }} de materiales</summary>
  <ul class="ctr-alerts">{% for alert in material_alerts %}<li>{{ alert.text }}</li>{% endfor %}</ul>
</details>
{% endif %}
```

`server/suno/core/templates/suno/materials/inventory.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Materiales · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="inventory" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros del inventario">
    <label>Bodega <select name="bodega"><option value="">Todas</option>{% for w in warehouses %}<option value="{{ w.pk }}" {% if filters.warehouse_id == w.pk %}selected{% endif %}>{{ w.name }}{% if not w.active %} (inactiva){% endif %}</option>{% endfor %}</select></label>
    <label>Categoría <select name="categoria"><option value="">Todas</option>{% for c in categories %}<option value="{{ c }}" {% if c == filters.category %}selected{% endif %}>{{ c }}</option>{% endfor %}</select></label>
    <label>Código o nombre <input name="texto" value="{{ filters.text }}" maxlength="100"></label>
    <button class="button" type="submit">Filtrar</button>
    {% if can_export_material %}<a class="text-link" href="{% url 'materials:export_inventory' project.pk %}{% if filters.query %}?{{ filters.query }}{% endif %}">Exportar a Excel</a>{% endif %}
  </form>
</section>
<section class="pj-panel">
  <p>Valor en bodega{% if filters.query %} (filtrado){% endif %}: <strong>{{ result.value|money }}</strong>. Es inventario: entra al costo con las salidas y los cortes.</p>
  {% if result.rows %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <caption class="sr-only">Saldo por bodega y material</caption>
    <thead><tr><th>Bodega</th><th>Material</th><th>Unidad</th><th class="num">Cantidad</th><th class="num">Promedio</th><th class="num">Valor</th></tr></thead>
    <tbody>
    {% for row in result.rows %}
      <tr>
        <td class="pj-wrap">{{ row.balance.warehouse.name }}</td>
        <td class="pj-name"><a href="{% url 'materials:kardex' project.pk row.balance.warehouse_id row.balance.material_id %}">{{ row.balance.material.code }} · {{ row.balance.material.name }}</a></td>
        <td>{{ row.balance.material.unit }}</td>
        <td class="num">{{ row.balance.quantity|qty }}</td>
        <td class="num">{% if row.average is not None %}{{ row.average|money }}{% else %}—{% endif %}</td>
        <td class="num">{{ row.balance.value|signed }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  <nav class="cst-pages" aria-label="Páginas">
    {% if result.has_previous %}<a href="?{{ filters.query }}&amp;pagina={{ result.number|add:'-1' }}">Anterior</a>{% endif %}
    <span>Página {{ result.number }} de {{ result.pages }} · {{ result.count }} saldo{{ result.count|pluralize:"s" }}</span>
    {% if result.has_next %}<a href="?{{ filters.query }}&amp;pagina={{ result.number|add:'1' }}">Siguiente</a>{% endif %}
  </nav>
  {% else %}<p class="pj-empty">No hay inventario con estos filtros.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/kardex.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Kárdex {{ sheet.material.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="inventory" %}
<section class="pj-panel">
  <h2 class="pj-name">Kárdex · {{ sheet.material.code }} {{ sheet.material.name }} · {{ sheet.warehouse.name }}</h2>
  <form method="get" class="pj-filters" aria-label="Fechas del kárdex">
    <label>Desde <input type="date" name="desde" value="{{ start }}"></label>
    <label>Hasta <input type="date" name="hasta" value="{{ end }}"></label>
    <button class="button" type="submit">Filtrar</button>
    {% if can_export_material %}<a class="text-link" href="{% url 'materials:export_kardex' project.pk sheet.warehouse.pk sheet.material.pk %}{% if start or end %}?desde={{ start }}&amp;hasta={{ end }}{% endif %}">Exportar a Excel</a>{% endif %}
  </form>
  <p class="pj-muted">En el orden en que se registraron los movimientos; el saldo corrido sigue ese orden aunque se filtre por fechas.{% if sheet.truncated %} Se muestran los últimos 1.000 movimientos del filtro.{% endif %}</p>
  {% if sheet.rows %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Fecha</th><th>Movimiento</th><th>Documento</th><th class="num">Entrada</th><th class="num">Salida</th><th class="num">Valor</th><th class="num">Saldo</th><th class="num">Valor del saldo</th><th class="num">Promedio</th></tr></thead>
    <tbody>
    {% for row in sheet.rows %}
      <tr>
        <td>{{ row.movement.moved_on|date:"d/m/Y" }}</td>
        <td>{{ row.movement.get_kind_display }}</td>
        <td>{{ row.document }}</td>
        <td class="num">{{ row.entry|qty }}</td>
        <td class="num">{{ row.exit|qty }}</td>
        <td class="num">{{ row.movement.value|signed }}</td>
        <td class="num">{{ row.balance_quantity|qty }}</td>
        <td class="num">{{ row.balance_value|signed }}</td>
        <td class="num">{% if row.average is not None %}{{ row.average|money }}{% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay movimientos en estas fechas.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/warehouses.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Bodegas · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="warehouses" %}
<section class="pj-panel">
  <h2>Bodegas del proyecto</h2>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Nombre</th><th>Estado</th><th>Acciones</th></tr></thead>
    <tbody>
    {% for w, editable in rows %}
      <tr>
        <td class="pj-name">{{ w.name }}</td>
        <td>{% if w.active %}Activa{% else %}<span class="pj-muted">Inactiva</span>{% endif %}</td>
        <td class="cst-decide">{% if editable %}
          <form method="post" action="{% url 'materials:warehouse_update' project.pk w.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="accion" value="renombrar"><input aria-label="Nuevo nombre de {{ w.name }}" name="name" maxlength="120" required placeholder="Nuevo nombre"><button class="text-link" type="submit">Renombrar</button></form>
          <form method="post" action="{% url 'materials:warehouse_update' project.pk w.pk %}">{% csrf_token %}<input type="hidden" name="accion" value="{% if w.active %}desactivar{% else %}activar{% endif %}"><button class="text-link" type="submit">{% if w.active %}Desactivar{% else %}Activar{% endif %}</button></form>
        {% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">El proyecto todavía no tiene bodegas.</p>{% endif %}
  <p class="pj-muted">Solo se desactiva una bodega sin inventario.</p>
</section>
{% if can_create_material %}
<section class="pj-panel">
  <h2>Nueva bodega</h2>
  <form method="post" class="pj-form ctr-head">
    {% csrf_token %}
    {% if form.errors %}<div class="pj-late" role="alert">{{ form.errors }}</div>{% endif %}
    <label>{{ form.name.label }} {{ form.name }}</label>
    <div class="ctr-actions"><button class="button" type="submit">Crear bodega</button></div>
  </form>
</section>
{% endif %}
{% endblock %}
```

- [ ] **Step 8: Estilos**

Al final de `server/suno/core/static/suno/css/projects.css`:

```css
/* Materiales (costos, pieza 6c) */
.mat-nav { margin: 0 0 16px; }
.mat-table td.pj-name { min-width: 200px; max-width: 320px; }
.mat-lines input, .mat-lines select { width: 100%; min-width: 0; min-height: var(--suno-control-height); padding: 6px 8px; border: 1px solid var(--suno-line-strong); border-radius: var(--suno-radius-sm); font: inherit; }
.mat-lines td { vertical-align: top; }
.mat-lines .mat-material { min-width: 240px; }
.mat-lines .mat-num { min-width: 110px; }
.mat-lines .mat-destination { min-width: 220px; }
.mat-lines .mat-task { min-width: 90px; }
.mat-groups h3 { margin: 16px 0 4px; overflow-wrap: anywhere; }
.bg-label.mat-draft { background: var(--suno-line-strong); color: var(--suno-ink); }
.bg-label.mat-approved { background: var(--suno-green); }
.bg-label.mat-rejected, .bg-label.mat-voided { background: var(--suno-red); }
@media (max-width: 640px) {
  .mat-table td.pj-name { min-width: 160px; max-width: 200px; }
}
```

En `server/suno/core/templates/suno/projects/base.html`, subir la versión: `projects.css' %}?v=20261115-materials-v1`.

- [ ] **Step 9: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views.py suno/costs/tests/test_views.py suno/contracts/tests/test_views.py`
Expected: PASS.

- [ ] **Step 10: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials config && ruff check suno/materials config && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/config/urls.py server/suno/core/templates server/suno/core/static/suno/css/projects.css
git commit -m "feat(materials): project tab with inventory, kardex and warehouses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Compras en la web

**Files:**
- Modify: `server/suno/materials/forms.py` (formularios de compra y lectura de líneas)
- Create: `server/suno/materials/views_purchases.py`
- Modify: `server/suno/materials/urls.py`, `server/suno/core/templates/suno/materials/_nav.html`
- Create: `server/suno/core/templates/suno/materials/{purchases,purchase_form,purchase_detail,_decide,_support,_datalists}.html`
- Test: `server/suno/materials/tests/test_views_purchases.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views_purchases.py`:

```python
"""Compras en la web (spec, 3.2 "Compras"): formulario sin JavaScript, ficha y decisiones."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import MaterialPurchase
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class PurchasePageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.mw = material_world()
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/compras/"
        self.client.force_login(self.mw.cw.contributor)

    def post(self, action: str = "guardar", **overrides: Any) -> Any:
        data: dict[str, Any] = {
            "accion": action,
            "kind": "warehouse",
            "warehouse": str(self.mw.warehouse.pk),
            "supplier": "Ferretería El Constructor",
            "supplier_tax_id": "900123456",
            "document_type": "invoice",
            "document_number": "FE-1",
            "purchased_on": "2026-03-02",
            "lineas-TOTAL_FORMS": "2",
            "lineas-INITIAL_FORMS": "0",
            "lineas-0-material": "CEM-01 · Cemento gris 50 kg",
            "lineas-0-quantity": "50",
            "lineas-0-unit_price": "32000",
            "lineas-1-material": "",
        }
        data.update(overrides)
        return self.client.post(self.base + "nueva/", data)

    def test_new_purchase_with_add_lines_and_recalculate(self) -> None:
        more = self.post("lineas")
        self.assertContains(more, 'name="lineas-4-material"')
        again = self.post("calcular")
        self.assertContains(again, "1.600.000,00")
        self.assertFalse(MaterialPurchase.objects.exists())
        saved = self.post()
        purchase = MaterialPurchase.objects.get()
        self.assertRedirects(saved, f"{self.base}{purchase.pk}/")
        self.assertEqual(purchase.total, 1600000)

    def test_a_direct_purchase_shows_destination_errors(self) -> None:
        page = self.post(kind="direct", warehouse="", **{"lineas-0-destination": "9.9"})
        self.assertContains(page, "Línea 1: «9.9» no es un ítem ni un capítulo del presupuesto vigente.")
        ok = self.post(kind="direct", warehouse="",
                       **{"lineas-0-destination": "2.1", "lineas-0-task": "2.1"})  # fmt: skip
        self.assertEqual(ok.status_code, 302)
        detail = self.client.get(ok["Location"])
        self.assertContains(detail, "2.1 Concreto zapatas · tarea 2.1 Columnas")

    def test_invoice_approval_and_void_from_the_sheet(self) -> None:
        self.post()
        purchase = MaterialPurchase.objects.get()
        url = f"{self.base}{purchase.pk}/"
        self.client.post(url + "soporte/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        page = self.client.get(url)
        self.assertContains(page, "Aprobar")
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "approved")
        self.client.force_login(self.mw.world.admin)
        self.client.post(url + "anular/", {"motivo": "Factura duplicada"})
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "voided")
        self.assertEqual(self.client.get(url + "soporte/ver/").status_code, 302)

    def test_list_filters_and_isolation(self) -> None:
        stock_in(self.mw, [(self.mw.cement, "1", "1000.00")])
        self.post()
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertContains(drafts, "CM-002")
        self.assertNotContains(drafts, "CM-001")
        other = material_world("Otra constructora")
        foreign = stock_in(other, [(other.cement, "1", "1000.00")])
        self.assertEqual(self.client.get(f"{self.base}{foreign.pk}/").status_code, 404)
        self.client.force_login(self.mw.cw.viewer)
        self.assertEqual(self.client.get(self.base + "nueva/").status_code, 403)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_purchases.py`
Expected: FAIL (404 en `/compras/`).

- [ ] **Step 3: Formularios de compra**

En `server/suno/materials/forms.py`, agregar a los imports `from collections.abc import Mapping`, `from uuid import UUID`, `from django.forms import formset_factory`, `CostDocumentType`, `MaterialPurchase`, `MaterialPurchaseLine`, `PurchaseKind`, `Task` y `Warehouse` al de `suno.core.models`, `from suno.costs.catalog import Catalog`, `from .destination import destination_text, parse_destination, DestinationData` y `from .purchases import PurchaseData, PurchaseLineData`; y al final:

```python
def outline_index(tasks: Mapping[UUID, Task]) -> dict[str, UUID]:
    return {task.outline_number: task.pk for task in tasks.values()}


def warehouse_choices(warehouses: Sequence[Warehouse]) -> list[tuple[str, str]]:
    return [("", "Elige una bodega"), *((str(w.pk), w.name) for w in warehouses)]


def task_choices(tasks: Mapping[UUID, Task]) -> list[tuple[str, str]]:
    return [
        ("", "Sin tarea"),
        *((str(task.pk), f"{task.outline_number} {task.name}") for task in tasks.values()),
    ]


def material_field() -> forms.CharField:
    return forms.CharField(
        label="Material",
        required=False,
        widget=forms.TextInput(attrs={"list": "mat-materials", "autocomplete": "off"}),
    )


def destination_field(required: bool = False) -> forms.CharField:
    return forms.CharField(
        label="Destino",
        required=required,
        max_length=500,
        widget=forms.TextInput(
            attrs={"list": "mat-destinations", "autocomplete": "off", "placeholder": "Indirecto"}
        ),
    )


def read_destination(
    text: str, task_text: str, catalog: Catalog, outlines: Mapping[str, UUID]
) -> tuple[DestinationData | None, str]:
    """El destino de una fila: el texto y el número de esquema de la tarea (vacío: sin tarea)."""
    task_id = None
    task = task_text.strip()
    if task:
        task_id = outlines.get(task)
        if task_id is None:
            return None, f"No hay una tarea vigente con número {task}."
    return parse_destination(text, catalog, task_id)


class PurchaseForm(forms.Form):
    kind = forms.ChoiceField(label="Tipo", choices=PurchaseKind.choices)
    warehouse = forms.ChoiceField(label="Bodega (si es a bodega)", required=False)
    supplier = forms.CharField(label="Proveedor", max_length=200)
    supplier_tax_id = forms.CharField(label="NIT", max_length=40, required=False)
    document_type = forms.ChoiceField(label="Documento", choices=CostDocumentType.choices)
    document_number = forms.CharField(label="Número", max_length=80, required=False)
    purchased_on = forms.DateField(label="Fecha", widget=date_input())
    support = forms.FileField(
        label="Factura (PDF, JPG, PNG o WebP; opcional ahora)",
        required=False,
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )

    def __init__(self, *args: Any, warehouses: Sequence[Warehouse], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)


class PurchaseLineForm(forms.Form):
    material = material_field()
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_price = forms.DecimalField(
        label="Valor unitario (IVA incluido)", required=False, max_digits=18, decimal_places=2,
        min_value=0,
    )  # fmt: skip
    destination = destination_field()
    task = forms.CharField(
        label="Tarea", required=False, max_length=40,
        widget=forms.TextInput(attrs={"placeholder": "2.1"}),
    )  # fmt: skip


PurchaseLineFormSet = formset_factory(PurchaseLineForm, extra=0)


def read_purchase_lines(
    formset: Any,
    index: MaterialIndex,
    catalog: Catalog,
    outlines: Mapping[str, UUID],
    *,
    direct: bool,
) -> tuple[list[PurchaseLineData], list[str]]:
    """Las filas con material (las vacías se ignoran). Formset ya validado."""
    lines: list[PurchaseLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        text = (data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        amount, price = data.get("quantity"), data.get("unit_price")
        problems: list[str] = []
        if material is None:
            problems.append(f"«{text}» no está en el catálogo; elígelo de la lista.")
        if amount is None or price is None:
            problems.append("Escribe la cantidad y el valor unitario.")
        destination = None
        if direct:
            destination, error = read_destination(
                data.get("destination") or "", data.get("task") or "", catalog, outlines
            )
            if error:
                problems.append(error)
        errors += [f"Línea {n}: {problem}" for problem in problems]
        if not problems and material is not None and amount is not None and price is not None:
            lines.append(PurchaseLineData(material.pk, amount, price, destination))
    return lines, errors


def purchase_data(form: PurchaseForm, lines: Sequence[PurchaseLineData]) -> PurchaseData:
    data = form.cleaned_data
    warehouse = data.get("warehouse") or ""
    to_warehouse = bool(warehouse) and data["kind"] == PurchaseKind.WAREHOUSE
    return PurchaseData(
        kind=data["kind"],
        warehouse_id=UUID(warehouse) if to_warehouse else None,
        supplier=data["supplier"],
        supplier_tax_id=data.get("supplier_tax_id") or "",
        document_type=data["document_type"],
        document_number=data.get("document_number") or "",
        purchased_on=data["purchased_on"],
        lines=tuple(lines),
    )


def purchase_initial(
    purchase: MaterialPurchase, lines: Sequence[MaterialPurchaseLine], catalog: Catalog
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Encabezado y filas para editar (líneas con `select_related("material", "task")` y
    `prefetch_related("item_links")`)."""
    header = {
        "kind": purchase.kind,
        "warehouse": str(purchase.warehouse_id or ""),
        "supplier": purchase.supplier,
        "supplier_tax_id": purchase.supplier_tax_id,
        "document_type": purchase.document_type,
        "document_number": purchase.document_number,
        "purchased_on": purchase.purchased_on,
    }
    rows = [
        {
            "material": line.material.label,
            "quantity": plain(line.quantity),
            "unit_price": line.unit_price,
            "destination": destination_text(
                catalog,
                line.scope,
                line.chapter_id,
                [link.item_id for link in line.item_links.all()],
            ),
            "task": line.task.outline_number if line.task else "",
        }
        for line in lines
    ]
    return header, rows
```

(`purchases.py` no importa `forms`, así que no hay ciclo.)

- [ ] **Step 4: Plantillas compartidas**

`server/suno/core/templates/suno/materials/_datalists.html`:

```html
<datalist id="mat-materials">{% for m in material_index.materials %}<option value="{{ m.label }}">{% endfor %}</datalist>
<datalist id="mat-destinations">{% for d in destination_options %}<option value="{{ d }}">{% endfor %}</datalist>
<p class="pj-muted">Destino: vacío o «Indirecto», un ítem («2.1» o su nombre), «Capítulo 2», o varios ítems del mismo capítulo separados por comas («2.1, 2.2»). Tarea: su número de esquema (opcional).</p>
```

`server/suno/core/templates/suno/materials/_decide.html` (parámetros: `doc`, `approve`, `reject`, `void`, `can_decide`, `can_void`, `needs` = texto de lo que falta para aprobar o vacío):

```html
{% if can_decide %}
<div class="cst-decide">
  <form method="post" action="{{ approve }}">{% csrf_token %}<input type="hidden" name="volver" value="{{ back }}"><button class="button" type="submit"{% if needs %} disabled title="{{ needs }}"{% endif %}>Aprobar</button></form>
  <form method="post" action="{{ reject }}" class="cst-reject">{% csrf_token %}<input type="hidden" name="volver" value="{{ back }}"><input aria-label="Motivo del rechazo" name="motivo" maxlength="500" required placeholder="Motivo del rechazo"><button class="text-link" type="submit">Rechazar</button></form>
</div>
{% endif %}
{% if can_void %}
<form method="post" action="{{ void }}" class="cst-reject">{% csrf_token %}<input aria-label="Motivo de la anulación" name="motivo" maxlength="500" required placeholder="Motivo de la anulación"><button class="text-link" type="submit">Anular</button></form>
{% endif %}
```

`server/suno/core/templates/suno/materials/_support.html` (parámetros: `doc`, `upload`, `view`, `can_attach`, `label`):

```html
<p>{{ label }}: {% if doc.attachment_id %}<a href="{{ view }}" target="_blank" rel="noopener">{{ doc.attachment_name|default:"Ver" }}</a>{% else %}<span class="pj-late">sin cargar</span>{% endif %}</p>
{% if can_attach %}
<form method="post" action="{{ upload }}" enctype="multipart/form-data" class="cst-reject">{% csrf_token %}<input type="file" name="file" accept="application/pdf,image/jpeg,image/png,image/webp" required aria-label="{{ label }}"><button class="text-link" type="submit">Cargar</button></form>
{% endif %}
```

- [ ] **Step 5: Vistas de compras**

`server/suno/materials/views_purchases.py`:

```python
"""Compras en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Compras")."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import MaterialPurchase, MaterialStatus, PurchaseKind, Warehouse
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.projects.views import _ctx

from . import purchases
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .destination import destination_label, destination_options, task_label
from .forms import (
    NEW_ROWS,
    PurchaseForm,
    PurchaseLineFormSet,
    SupportForm,
    material_index,
    more_rows,
    outline_index,
    purchase_data,
    purchase_initial,
    read_purchase_lines,
)
from .queries import DocumentFilters, purchase_rows
from .views import download, page, run


def _detail(project_id: UUID, purchase_id: UUID) -> HttpResponse:
    return redirect("materials:purchase", project_id, purchase_id)


def back(request: HttpRequest, project_id: UUID, detail: HttpResponse) -> HttpResponse:
    """A la bandeja "Por aprobar" de Costos si de allá vino; si no, a la ficha."""
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project_id)
    return detail


@login_required(login_url="platform_login")
@require_GET
def purchase_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = DocumentFilters.from_query(request.GET)
    rows, more = purchase_rows(ctx, project, filters)
    return page(
        request, ctx, project, "suno/materials/purchases.html", filters=filters, rows=rows,
        more=more, kinds=PurchaseKind.choices, statuses=MaterialStatus.choices,
    )  # fmt: skip


def _form(request: HttpRequest, project_id: UUID, purchase_id: UUID | None) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    purchase = (
        get_document(ctx, MaterialPurchase, project, purchase_id) if purchase_id else None
    )
    if purchase is None and not can_create_costs(ctx, project):
        raise PermissionDenied
    if purchase is not None and (
        purchase.status not in OPEN_STATES or not can_update_cost(ctx, purchase)
    ):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = purchases.purchase_lines(ctx, purchase) if purchase else []
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = list(scoped_qs(ctx, Warehouse).filter(project=project, active=True))
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    total = None
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = PurchaseForm(data, request.FILES, warehouses=warehouses)
        lines = PurchaseLineFormSet(data, prefix="lineas")
        if action in ("guardar", "calcular") and form.is_valid() and lines.is_valid():
            direct = form.cleaned_data["kind"] == PurchaseKind.DIRECT
            rows, errors = read_purchase_lines(
                lines, index, catalog, outline_index(tasks), direct=direct
            )
            values = purchase_data(form, rows)
            if not errors and action == "calcular":
                errors, checked = purchases.purchase_errors(
                    ctx, project, values, catalog, tasks, today=timezone.localdate()
                )
                total = sum((line.amount for line in checked), start=0)
            elif not errors:
                upload = form.cleaned_data.get("support")
                try:
                    if purchase is None:
                        saved = purchases.create_purchase(
                            ctx, project, values, upload=upload,
                            name=upload.name if upload else "", catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                    else:
                        saved = purchases.update_purchase(
                            ctx, project, purchase.pk, values, catalog=catalog, tasks=tasks
                        )
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    return _detail(project.pk, saved.pk)
    elif purchase is None:
        form = PurchaseForm(
            initial={"kind": PurchaseKind.WAREHOUSE, "purchased_on": timezone.localdate(),
                     "document_type": "invoice"},
            warehouses=warehouses,
        )  # fmt: skip
        lines = PurchaseLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        header, rows_initial = purchase_initial(purchase, current, catalog)
        form = PurchaseForm(initial=header, warehouses=warehouses)
        lines = PurchaseLineFormSet(prefix="lineas", initial=rows_initial)
    return page(
        request, ctx, project, "suno/materials/purchase_form.html", form=form, lines=lines,
        purchase=purchase, errors=errors, total=total, material_index=index,
        destination_options=destination_options(catalog), show_errors=action != "lineas",
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def purchase_new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    return _form(request, project_id, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def purchase_edit(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    return _form(request, project_id, purchase_id)


@login_required(login_url="platform_login")
@require_GET
def purchase_detail(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    purchase = get_document(
        ctx, MaterialPurchase, project, purchase_id,
        "warehouse", "created_by", "updated_by", "decided_by", "voided_by",
    )  # fmt: skip
    catalog = load_catalog(ctx, project)
    lines = [
        (
            line,
            destination_label(
                catalog, line.scope, line.chapter_id,
                [link.item_id for link in line.item_links.all()], task_label(line.task),
            )
            if line.scope
            else "",
        )  # fmt: skip
        for line in purchases.purchase_lines(ctx, purchase)
    ]
    is_open = purchase.status in OPEN_STATES
    mine = ctx.user_id in (purchase.created_by_id, purchase.updated_by_id)
    url = reverse("materials:purchase", args=[project.pk, purchase.pk])
    return page(
        request, ctx, project, "suno/materials/purchase_detail.html",
        purchase=purchase, lines=lines,
        can_edit=is_open and can_update_cost(ctx, purchase)
        and (purchase.status == "draft" or purchase.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, purchase),
        can_attach=is_open and can_update_cost(ctx, purchase),
        can_decide=purchase.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=purchase.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if purchase.attachment_id else "Falta la factura",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "soporte/", "view": url + "soporte/ver/"},
        support_form=SupportForm(),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def purchase_delete(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: purchases.delete_purchase(ctx, project, purchase_id),
           "Borrador de la compra borrado."):  # fmt: skip
        return redirect("materials:purchases", project.pk)
    return _detail(project.pk, purchase_id)


@login_required(login_url="platform_login")
@require_POST
def purchase_support(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = SupportForm(request.POST, request.FILES)
    if form.is_valid():
        upload = form.cleaned_data["file"]
        run(request, lambda: purchases.attach_support(
            ctx, project, purchase_id, upload, upload.name or "factura.pdf"),
            "Factura adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, purchase_id)


@login_required(login_url="platform_login")
@require_GET
def purchase_support_file(
    request: HttpRequest, project_id: UUID, purchase_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    return download(attachment_url(ctx, project, get_document(ctx, MaterialPurchase, project,
                                                              purchase_id)))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def purchase_approve(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: purchases.approve_purchase(ctx, project, purchase_id),
        "Compra aprobada.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, purchase_id))


@login_required(login_url="platform_login")
@require_POST
def purchase_reject(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: purchases.reject_purchase(ctx, project, purchase_id, note),
        "Compra rechazada; su autor la verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, purchase_id))


@login_required(login_url="platform_login")
@require_POST
def purchase_void(request: HttpRequest, project_id: UUID, purchase_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: purchases.void_purchase(ctx, project, purchase_id, note),
        "Compra anulada.")  # fmt: skip
    return _detail(project.pk, purchase_id)
```

En `server/suno/materials/urls.py`, agregar `from . import views, views_purchases`, la constante `P = "compras/<uuid:purchase_id>/"` y estas rutas:

```python
    path("compras/", views_purchases.purchase_list, name="purchases"),
    path("compras/nueva/", views_purchases.purchase_new, name="new_purchase"),
    path(P, views_purchases.purchase_detail, name="purchase"),
    path(P + "editar/", views_purchases.purchase_edit, name="edit_purchase"),
    path(P + "borrar/", views_purchases.purchase_delete, name="delete_purchase"),
    path(P + "soporte/", views_purchases.purchase_support, name="purchase_support"),
    path(P + "soporte/ver/", views_purchases.purchase_support_file, name="purchase_file"),
    path(P + "aprobar/", views_purchases.purchase_approve, name="approve_purchase"),
    path(P + "rechazar/", views_purchases.purchase_reject, name="reject_purchase"),
    path(P + "anular/", views_purchases.purchase_void, name="void_purchase"),
```

En `_nav.html`, en el lugar marcado, `<a href="{% url 'materials:purchases' project.pk %}" {% if sub == "purchases" %}aria-current="page"{% endif %}>Compras</a>` (y dejar el comentario para las siguientes).

- [ ] **Step 6: Plantillas de compras**

`server/suno/core/templates/suno/materials/purchases.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Compras · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="purchases" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros de compras">
    <label>Tipo <select name="tipo"><option value="">Todos</option>{% for key, label in kinds %}<option value="{{ key }}" {% if filters.kind == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Estado <select name="estado"><option value="">Todos</option>{% for key, label in statuses %}<option value="{{ key }}" {% if filters.status == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Desde <input type="date" name="desde" value="{{ filters.start|date:'Y-m-d' }}"></label>
    <label>Hasta <input type="date" name="hasta" value="{{ filters.end|date:'Y-m-d' }}"></label>
    <button class="button" type="submit">Filtrar</button>
    {% if can_create_material %}<a class="button" href="{% url 'materials:new_purchase' project.pk %}">Nueva compra</a>{% endif %}
  </form>
</section>
<section class="pj-panel">
  {% if rows %}
  {% if more %}<p class="pj-muted">Se muestran las 200 más recientes: filtra por fechas para ver las demás.</p>{% endif %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Número</th><th>Fecha</th><th>Tipo</th><th>Proveedor</th><th>Documento</th><th class="num">Total</th><th>Estado</th></tr></thead>
    <tbody>
    {% for p in rows %}
      <tr>
        <td><a href="{% url 'materials:purchase' project.pk p.pk %}">{{ p.code }}</a></td>
        <td>{{ p.purchased_on|date:"d/m/Y" }}</td>
        <td>{{ p.get_kind_display }}{% if p.warehouse %} · {{ p.warehouse.name }}{% endif %}</td>
        <td class="pj-name">{{ p.supplier }}</td>
        <td class="pj-wrap">{{ p.get_document_type_display }} {{ p.document_number }}</td>
        <td class="num">{{ p.total|signed }}</td>
        <td><span class="bg-label mat-{{ p.status }}">{{ p.get_status_display }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay compras con estos filtros.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/purchase_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{% if purchase %}Editar {{ purchase.code }}{% else %}Nueva compra{% endif %} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="purchases" %}
<section class="pj-panel">
  <h2>{% if purchase %}Editar compra {{ purchase.code }}{% else %}Nueva compra{% endif %}</h2>
  {% if purchase.status == "rejected" %}<p class="pj-warning">Rechazada: {{ purchase.decision_note }}. Al guardarla vuelve a borrador.</p>{% endif %}
  <form method="post" enctype="multipart/form-data" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.kind.label }} {{ form.kind }}</label>
      <label>{{ form.warehouse.label }} {{ form.warehouse }}</label>
      <label>{{ form.supplier.label }} {{ form.supplier }}</label>
      <label>{{ form.supplier_tax_id.label }} {{ form.supplier_tax_id }}</label>
      <label>{{ form.document_type.label }} {{ form.document_type }}</label>
      <label>{{ form.document_number.label }} {{ form.document_number }}</label>
      <label>{{ form.purchased_on.label }} {{ form.purchased_on }}</label>
      {% if not purchase %}<label>{{ form.support.label }} {{ form.support }}</label>{% endif %}
    </div>
    <p class="pj-muted">A bodega: entra al inventario al aprobarla y el costo llega con las salidas. Directa: cada línea es un costo de su destino al aprobarla (el destino y la tarea solo se usan en una compra directa).</p>
    {{ lines.management_form }}
    <div class="pj-scroll">
    <table class="pj-table mat-lines">
      <thead><tr><th>#</th><th>Material</th><th class="num">Cantidad</th><th class="num">Valor unitario</th><th>Destino</th><th>Tarea</th></tr></thead>
      <tbody>
      {% for row in lines %}
        <tr>
          <td>{{ forloop.counter }}</td>
          <td class="mat-material"><label class="sr-only" for="{{ row.material.id_for_label }}">Material de la línea {{ forloop.counter }}</label>{{ row.material }}</td>
          <td class="mat-num"><label class="sr-only" for="{{ row.quantity.id_for_label }}">Cantidad de la línea {{ forloop.counter }}</label>{{ row.quantity }}</td>
          <td class="mat-num"><label class="sr-only" for="{{ row.unit_price.id_for_label }}">Valor unitario de la línea {{ forloop.counter }}</label>{{ row.unit_price }}</td>
          <td class="mat-destination"><label class="sr-only" for="{{ row.destination.id_for_label }}">Destino de la línea {{ forloop.counter }}</label>{{ row.destination }}</td>
          <td class="mat-task"><label class="sr-only" for="{{ row.task.id_for_label }}">Tarea de la línea {{ forloop.counter }}</label>{{ row.task }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% include "suno/materials/_datalists.html" %}
    {% if total is not None %}<p>Total de la compra: <strong>{{ total|money }}</strong></p>{% endif %}
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
      <button class="text-link" type="submit" name="accion" value="calcular">Recalcular</button>
      <button class="button" type="submit" name="accion" value="guardar">Guardar borrador</button>
      <a href="{% if purchase %}{% url 'materials:purchase' project.pk purchase.pk %}{% else %}{% url 'materials:purchases' project.pk %}{% endif %}">Cancelar</a>
    </div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/purchase_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Compra {{ purchase.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="purchases" %}
<section class="pj-panel cst-detail">
  <h2>Compra {{ purchase.code }} <span class="bg-label mat-{{ purchase.status }}">{{ purchase.get_status_display }}</span></h2>
  {% if purchase.status == "rejected" %}<p class="pj-warning">Motivo del rechazo: {{ purchase.decision_note }}</p>{% endif %}
  {% if purchase.status == "voided" %}<p class="pj-warning">Anulada por {{ purchase.voided_by }}: {{ purchase.void_note }}</p>{% endif %}
  <dl class="cst-facts">
    <dt>Tipo</dt><dd>{{ purchase.get_kind_display }}{% if purchase.warehouse %} · {{ purchase.warehouse.name }}{% endif %}</dd>
    <dt>Proveedor</dt><dd>{{ purchase.supplier }}{% if purchase.supplier_tax_id %} (NIT {{ purchase.supplier_tax_id }}){% endif %}</dd>
    <dt>Documento</dt><dd>{{ purchase.get_document_type_display }} {{ purchase.document_number }}</dd>
    <dt>Fecha</dt><dd>{{ purchase.purchased_on|date:"d/m/Y" }}</dd>
    <dt>Total (IVA incluido)</dt><dd>{{ purchase.total|money }}</dd>
    <dt>Registró</dt><dd>{{ purchase.created_by }} · modificó {{ purchase.updated_by }}</dd>
    {% if purchase.decided_by %}<dt>{% if purchase.status == "rejected" %}Rechazó{% else %}Aprobó{% endif %}</dt><dd>{{ purchase.decided_by }} · {{ purchase.decided_at|date:"d/m/Y H:i" }}</dd>{% endif %}
  </dl>
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Material</th><th class="num">Cantidad</th><th class="num">Valor unitario</th><th class="num">Valor</th>{% if purchase.kind == "direct" %}<th>Destino</th>{% endif %}</tr></thead>
    <tbody>{% for line, destination in lines %}<tr><td class="pj-name">{{ line.material.code }} · {{ line.material.name }}</td><td class="num">{{ line.quantity|qty }} {{ line.material.unit }}</td><td class="num">{{ line.unit_price|money }}</td><td class="num">{{ line.amount|signed }}</td>{% if purchase.kind == "direct" %}<td class="pj-wrap">{{ destination }}</td>{% endif %}</tr>{% endfor %}</tbody>
  </table>
  </div>
  {% include "suno/materials/_support.html" with doc=purchase label="Factura" upload=urls.upload view=urls.view %}
  <div class="ctr-actions">
    {% if can_edit %}<a class="button" href="{% url 'materials:edit_purchase' project.pk purchase.pk %}">{% if purchase.status == "rejected" %}Corregir y reenviar{% else %}Editar{% endif %}</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'materials:delete_purchase' project.pk purchase.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
  </div>
  {% include "suno/materials/_decide.html" with approve=urls.approve reject=urls.reject void=urls.void back="" %}
</section>
{% endblock %}
```

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_purchases.py suno/materials/tests/test_views.py`
Expected: PASS.

- [ ] **Step 8: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/suno/core/templates/suno/materials
git commit -m "feat(materials): purchase pages with add lines, recalculate and decisions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Salidas, devoluciones y traslados en la web

**Files:**
- Modify: `server/suno/materials/forms.py`, `server/suno/materials/export.py` (`issues_workbook`), `server/suno/materials/urls.py`, `server/suno/core/templates/suno/materials/_nav.html`
- Create: `server/suno/materials/views_movements.py`
- Create: `server/suno/core/templates/suno/materials/{_material_rows,issues,issue_form,issue_detail,transfers,transfer_detail}.html`
- Test: `server/suno/materials/tests/test_views_movements.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views_movements.py`:

```python
"""Salidas, devoluciones y traslados en la web (spec, 3.2 "Salidas" y "Traslados")."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.core.models import MaterialIssue, MaterialIssueLine, MaterialReturn, MaterialTransfer
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import balance_of, issue, make_warehouse, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class MovementPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/"
        self.client.force_login(self.mw.cw.contributor)

    def lines(self, quantity: str) -> dict[str, Any]:
        return {
            "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "0",
            "lineas-0-material": "CEM-01",
            "lineas-0-quantity": quantity,
        }

    def issue_post(self, quantity: str, **extra: str) -> Any:
        return self.client.post(self.base + "salidas/", {
            "accion": "guardar", "warehouse": str(self.mw.warehouse.pk),
            "issued_on": "2026-03-10", "received_by": "Maestro", "destination": "2.1",
            "task": "", "notes": "", **self.lines(quantity), **extra,
        })  # fmt: skip

    def test_the_quick_issue_form_and_the_list(self) -> None:
        self.assertRedirects(self.issue_post("7"), self.base + "salidas/")
        created = MaterialIssue.objects.get()
        self.assertEqual((created.code, created.scope, created.total), ("SA-001", "item", 224000))
        shortage = self.issue_post("94")
        self.assertContains(shortage, "Solo hay 93 und de Cemento gris 50 kg en Almacén principal.")
        page = self.client.get(self.base + "salidas/?corte=sin")
        self.assertContains(page, "SA-001")
        self.assertContains(page, "2.1 Concreto zapatas")

    def test_return_from_the_issue_and_no_more_edits(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        line = MaterialIssueLine.objects.get(issue=out)
        url = f"{self.base}salidas/{out.pk}/"
        self.assertContains(self.client.get(url), "Registrar devolución")
        self.client.post(url + "devolver/", {"returned_on": "2026-03-12", "notes": "",
                                             f"devolver_{line.pk}": "2"})  # fmt: skip
        self.assertEqual(MaterialReturn.objects.get().code, "DV-001")
        self.assertEqual(balance_of(self.mw, self.mw.cement)[0], 95)
        edit = self.client.post(url + "editar/", {"accion": "guardar"}, follow=True)
        self.assertContains(edit, "La salida tiene devoluciones")

    def test_editing_and_deleting_an_issue(self) -> None:
        out = issue(self.mw, [(self.mw.cement, "7")])
        url = f"{self.base}salidas/{out.pk}/"
        self.assertContains(self.client.get(url + "editar/"), 'value="CEM-01 · Cemento gris 50 kg"')
        self.client.post(url + "editar/", {
            "accion": "guardar", "warehouse": str(self.mw.warehouse.pk),
            "issued_on": "2026-03-10", "received_by": "", "destination": "", "task": "",
            "notes": "", **self.lines("10"),
        })  # fmt: skip
        out.refresh_from_db()
        self.assertEqual(out.total, 320000)
        self.client.post(url + "borrar/")
        self.assertFalse(MaterialIssue.objects.exists())

    def test_transfers_and_the_issues_excel(self) -> None:
        north = make_warehouse(self.mw.world, "Frente norte")
        response = self.client.post(self.base + "traslados/", {
            "accion": "guardar", "source": str(self.mw.warehouse.pk), "target": str(north.pk),
            "moved_on": "2026-03-11", "notes": "", **self.lines("10"),
        })  # fmt: skip
        transfer = MaterialTransfer.objects.get()
        self.assertRedirects(response, f"{self.base}traslados/{transfer.pk}/")
        self.assertEqual(balance_of(self.mw, self.mw.cement, north)[0], 10)
        issue(self.mw, [(self.mw.cement, "1")])
        sheet = self.client.get(self.base + "salidas.xlsx?desde=2026-03-01&hasta=2026-03-31")
        self.assertEqual(sheet.status_code, 200)
        self.assertTrue(sheet.content.startswith(b"PK"))
```

Números: 100 und a 32.000; salen 7 → 224.000,00 (y quedan 93); al editar a 10 → 320.000,00.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_movements.py`
Expected: FAIL (404 en `/salidas/`).

- [ ] **Step 3: Formularios**

En `server/suno/materials/forms.py`, agregar `MaterialIssue`, `MaterialIssueLine` al import de modelos y `from .movements import IssueData, IssueLineData, ReturnData, ReturnLineData, TransferData`; y al final:

```python
class MaterialLineForm(forms.Form):
    """Una fila de material y cantidad (salidas y traslados)."""

    material = material_field()
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )


MaterialLineFormSet = formset_factory(MaterialLineForm, extra=0)


def read_material_lines(formset: Any, index: MaterialIndex) -> tuple[list[IssueLineData], list[str]]:
    """Las filas con material (las vacías se ignoran). Formset ya validado."""
    lines: list[IssueLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        text = (form.cleaned_data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        amount = form.cleaned_data.get("quantity")
        if material is None:
            errors.append(f"Línea {n}: «{text}» no está en el catálogo; elígelo de la lista.")
        elif amount is None:
            errors.append(f"Línea {n}: Escribe la cantidad.")
        else:
            lines.append(IssueLineData(material.pk, amount))
    return lines, errors


def material_rows(lines: Sequence[Any]) -> list[dict[str, Any]]:
    """Filas para editar (líneas con `select_related("material")`)."""
    return [{"material": line.material.label, "quantity": plain(line.quantity)} for line in lines]


class IssueForm(forms.Form):
    warehouse = forms.ChoiceField(label="Bodega")
    issued_on = forms.DateField(label="Fecha", widget=date_input())
    received_by = forms.CharField(label="Recibe", max_length=120, required=False)
    destination = destination_field()
    task = forms.ChoiceField(label="Tarea", required=False)
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(
        self,
        *args: Any,
        warehouses: Sequence[Warehouse],
        tasks: Mapping[UUID, Task],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)
        self.fields["task"].choices = task_choices(tasks)

    def issue_data(
        self, lines: Sequence[IssueLineData], catalog: Catalog
    ) -> tuple[IssueData | None, str]:
        data = self.cleaned_data
        task = data.get("task") or ""
        destination, error = parse_destination(
            data.get("destination") or "", catalog, UUID(task) if task else None
        )
        if destination is None:
            return None, error
        return (
            IssueData(
                warehouse_id=UUID(data["warehouse"]) if data.get("warehouse") else None,
                issued_on=data["issued_on"],
                destination=destination,
                lines=tuple(lines),
                received_by=data.get("received_by") or "",
                notes=data.get("notes") or "",
            ),
            "",
        )


def issue_initial(issue: MaterialIssue, catalog: Catalog) -> dict[str, Any]:
    """El encabezado para editar (con `prefetch_related("item_links")`)."""
    return {
        "warehouse": str(issue.warehouse_id),
        "issued_on": issue.issued_on,
        "received_by": issue.received_by,
        "destination": destination_text(
            catalog,
            issue.scope,
            issue.chapter_id,
            [link.item_id for link in issue.item_links.all()],
        ),
        "task": str(issue.task_id or ""),
        "notes": issue.notes,
    }


class ReturnForm(forms.Form):
    returned_on = forms.DateField(label="Fecha de la devolución", widget=date_input())
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(self, *args: Any, lines: Sequence[MaterialIssueLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"devolver_{line.pk}"] = forms.DecimalField(
                label="Vuelve", required=False, max_digits=18, decimal_places=4, min_value=0
            )

    def line_fields(self) -> list[tuple[MaterialIssueLine, Any]]:
        return [(line, self[f"devolver_{line.pk}"]) for line in self.lines]

    def return_data(self) -> ReturnData:
        data = self.cleaned_data
        return ReturnData(
            returned_on=data["returned_on"],
            lines=tuple(
                ReturnLineData(line.pk, data[f"devolver_{line.pk}"])
                for line in self.lines
                if data.get(f"devolver_{line.pk}")
            ),
            notes=data.get("notes") or "",
        )


class TransferForm(forms.Form):
    source = forms.ChoiceField(label="Sale de")
    target = forms.ChoiceField(label="Entra a")
    moved_on = forms.DateField(label="Fecha", widget=date_input())
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(self, *args: Any, warehouses: Sequence[Warehouse], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["source"].choices = warehouse_choices(warehouses)
        self.fields["target"].choices = warehouse_choices(warehouses)

    def transfer_data(self, lines: Sequence[IssueLineData]) -> TransferData:
        data = self.cleaned_data
        return TransferData(
            source_id=UUID(data["source"]) if data.get("source") else None,
            target_id=UUID(data["target"]) if data.get("target") else None,
            moved_on=data["moved_on"],
            lines=tuple(lines),
            notes=data.get("notes") or "",
        )
```

- [ ] **Step 4: Excel de salidas**

En `server/suno/materials/export.py`, agregar `from suno.core.models import MaterialIssueLine` y:

```python
ISSUES_HEADER = [
    "salida", "fecha", "bodega", "destino", "recibe", "codigo", "material", "unidad",
    "cantidad", "costo_unitario", "valor", "corte",
]  # fmt: skip


def issues_workbook(lines: Sequence[tuple[MaterialIssueLine, str]]) -> bytes:
    """Las líneas de salida de un periodo, con la etiqueta de su destino."""
    return to_xlsx(
        ISSUES_HEADER,
        [
            [
                line.issue.code,
                line.issue.issued_on.isoformat(),
                safe_cell(line.issue.warehouse.name),
                safe_cell(destination),
                safe_cell(line.issue.received_by),
                safe_cell(line.material.code),
                safe_cell(line.material.name),
                safe_cell(line.material.unit),
                number(line.quantity),
                number(line.unit_cost),
                number(line.amount),
                line.issue.cut.code if line.issue.cut else "",
            ]
            for line, destination in lines
        ],
        sheet_name="Salidas",
    )
```

- [ ] **Step 5: Vistas**

`server/suno/materials/views_movements.py`:

```python
"""Salidas, devoluciones y traslados en la pestaña "Materiales" (costos, pieza 6c, spec 3.2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import (
    CostScope,
    MaterialIssue,
    MaterialIssueLine,
    MaterialReturn,
    MaterialTransfer,
    MaterialTransferLine,
    Warehouse,
)
from suno.core.policies import can_create_costs, can_delete_cost, can_update_cost
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog
from suno.projects.views import _ctx

from . import movements
from .access import get_document, get_materials_project
from .common import MaterialRejected
from .destination import destination_label, destination_options, task_label
from .export import issues_workbook
from .forms import (
    NEW_ROWS,
    IssueForm,
    MaterialLineFormSet,
    ReturnForm,
    TransferForm,
    issue_initial,
    material_index,
    material_rows,
    more_rows,
    read_material_lines,
)
from .queries import MAX_EXPORT_ROWS, DocumentFilters, issue_query, issue_rows, transfer_rows
from .views import page, run, xlsx


def issue_label(issue: MaterialIssue, catalog: Catalog) -> str:
    """Con `select_related("task")` y `prefetch_related("item_links")`."""
    return destination_label(
        catalog, issue.scope, issue.chapter_id, [link.item_id for link in issue.item_links.all()],
        task_label(issue.task),
    )  # fmt: skip


def _warehouses(ctx: Any, project: Any) -> list[Warehouse]:
    return list(scoped_qs(ctx, Warehouse).filter(project=project, active=True).order_by("name"))


def _issue_form(
    request: HttpRequest, ctx: Any, project: Any, issue: MaterialIssue | None
) -> tuple[HttpResponse | None, dict[str, Any]]:
    """El formulario de salida (nueva o editada). Devuelve la redirección si se guardó, o el
    contexto para pintarlo."""
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = (
        list(scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).select_related("material"))
        if issue
        else []
    )
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = _warehouses(ctx, project)
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = IssueForm(data, warehouses=warehouses, tasks=tasks)
        lines = MaterialLineFormSet(data, prefix="lineas")
        if action == "guardar" and form.is_valid() and lines.is_valid():
            rows, errors = read_material_lines(lines, index)
            values, error = form.issue_data(rows, catalog)
            if error:
                errors.append(error)
            if not errors and values is not None:
                try:
                    if issue is None:
                        movements.create_issue(ctx, project, values, catalog=catalog, tasks=tasks)
                        return redirect("materials:issues", project.pk), {}
                    movements.update_issue(
                        ctx, project, issue.pk, values, catalog=catalog, tasks=tasks
                    )
                    return redirect("materials:issue", project.pk, issue.pk), {}
                except MaterialRejected as exc:
                    errors = exc.errors
    elif issue is None:
        form = IssueForm(initial={"issued_on": timezone.localdate()}, warehouses=warehouses,
                         tasks=tasks)  # fmt: skip
        lines = MaterialLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        form = IssueForm(initial=issue_initial(issue, catalog), warehouses=warehouses, tasks=tasks)
        lines = MaterialLineFormSet(prefix="lineas", initial=material_rows(current))
    return None, {
        "form": form,
        "lines": lines,
        "errors": errors,
        "show_errors": action == "guardar",
        "material_index": index,
        "destination_options": destination_options(catalog),
        "catalog": catalog,
    }


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def issue_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """La lista filtrable y, para quien registra, el formulario rápido de salida."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    can_create = can_create_costs(ctx, project)
    if request.method == "POST" and not can_create:
        raise PermissionDenied
    done, form_context = _issue_form(request, ctx, project, None) if can_create else (None, {})
    if done is not None:
        messages.success(request, "Salida registrada.")
        return done
    filters = DocumentFilters.from_query(request.GET)
    rows, more = issue_rows(ctx, project, filters)
    catalog = form_context.get("catalog") or load_catalog(ctx, project)
    labelled = [(row, issue_label(row, catalog)) for row in _with_items(rows)]
    return page(
        request, ctx, project, "suno/materials/issues.html", filters=filters, rows=labelled,
        more=more, scopes=CostScope.choices,
        all_warehouses=scoped_qs(ctx, Warehouse).filter(project=project).order_by("name"),
        **form_context,
    )  # fmt: skip


def _with_items(rows: list[MaterialIssue]) -> list[MaterialIssue]:
    """Los ítems de los destinos de una lista ya leída (una consulta)."""
    prefetch_related_objects(rows, "item_links")
    return rows


def _changeable(ctx: Any, issue: MaterialIssue, policy: Any) -> bool:
    return (
        issue.cut_id is None
        and not scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists()
        and policy(ctx, issue)
    )


@login_required(login_url="platform_login")
@require_GET
def issue_detail(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id, "warehouse", "task", "cut",
                         "created_by", "updated_by")  # fmt: skip
    catalog = load_catalog(ctx, project)
    _with_items([issue])
    lines = list(
        scoped_qs(ctx, MaterialIssueLine).filter(issue=issue).select_related("material")
        .order_by("order")
    )  # fmt: skip
    returned = movements.returned_by_line(ctx, [line.pk for line in lines])
    return page(
        request, ctx, project, "suno/materials/issue_detail.html", issue=issue,
        label=issue_label(issue, catalog),
        lines=[(line, returned.get(line.pk, (None, None))[0]) for line in lines],
        returns=scoped_qs(ctx, MaterialReturn).filter(issue=issue).order_by("number"),
        return_form=ReturnForm(initial={"returned_on": timezone.localdate()}, lines=lines)
        if can_create_costs(ctx, project)
        else None,
        can_edit=_changeable(ctx, issue, can_update_cost),
        can_delete=_changeable(ctx, issue, can_delete_cost),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def issue_edit(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id, "task")
    if not can_update_cost(ctx, issue):
        raise PermissionDenied
    if issue.cut_id is not None:
        messages.error(request, movements.IN_A_CUT)
        return redirect("materials:issue", project.pk, issue.pk)
    if scoped_qs(ctx, MaterialReturn).filter(issue=issue).exists():
        messages.error(request, movements.HAS_RETURNS)
        return redirect("materials:issue", project.pk, issue.pk)
    _with_items([issue])
    done, form_context = _issue_form(request, ctx, project, issue)
    if done is not None:
        messages.success(request, "Salida guardada.")
        return done
    return page(request, ctx, project, "suno/materials/issue_form.html", issue=issue,
                **form_context)  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def issue_delete(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: movements.delete_issue(ctx, project, issue_id), "Salida borrada."):
        return redirect("materials:issues", project.pk)
    return redirect("materials:issue", project.pk, issue_id)


@login_required(login_url="platform_login")
@require_POST
def issue_return(request: HttpRequest, project_id: UUID, issue_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    issue = get_document(ctx, MaterialIssue, project, issue_id)
    lines = list(scoped_qs(ctx, MaterialIssueLine).filter(issue=issue))
    form = ReturnForm(request.POST, lines=lines)
    if form.is_valid():
        data = form.return_data()
        run(request, lambda: movements.create_return(ctx, project, issue.pk, data),
            "Devolución registrada.")  # fmt: skip
    else:
        messages.error(request, "Revisa la fecha y las cantidades de la devolución.")
    return redirect("materials:issue", project.pk, issue.pk)


@login_required(login_url="platform_login")
@require_GET
def export_issues(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Las líneas de salida de un periodo (spec 3.4)."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    filters = DocumentFilters.from_query(request.GET)
    catalog = load_catalog(ctx, project)
    issues = issue_query(ctx, project, filters)
    lines = list(
        scoped_qs(ctx, MaterialIssueLine)
        .filter(issue__in=issues)
        .select_related("issue__warehouse", "issue__cut", "issue__task", "material")
        .prefetch_related("issue__item_links")
        .order_by("issue__issued_on", "issue__number", "order")[:MAX_EXPORT_ROWS]
    )
    start = filters.start.isoformat() if filters.start else "inicio"
    end = filters.end.isoformat() if filters.end else "hoy"
    return xlsx(
        ctx, project, issues_workbook([(line, issue_label(line.issue, catalog)) for line in lines]),
        f"salidas-{start}-{end}", "salidas", lines=len(lines), filters=filters.query,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def transfer_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    can_create = can_create_costs(ctx, project)
    if request.method == "POST" and not can_create:
        raise PermissionDenied
    warehouses = _warehouses(ctx, project)
    index = material_index(ctx)
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = TransferForm(data, warehouses=warehouses)
        lines = MaterialLineFormSet(data, prefix="lineas")
        if action == "guardar" and form.is_valid() and lines.is_valid():
            rows, errors = read_material_lines(lines, index)
            if not errors:
                values = form.transfer_data(rows)
                try:
                    transfer = movements.create_transfer(ctx, project, values)
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    messages.success(request, "Traslado registrado.")
                    return redirect("materials:transfer", project.pk, transfer.pk)
    else:
        form = TransferForm(initial={"moved_on": timezone.localdate()}, warehouses=warehouses)
        lines = MaterialLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    rows_list, more = transfer_rows(ctx, project)
    return page(
        request, ctx, project, "suno/materials/transfers.html", rows=rows_list, more=more,
        form=form, lines=lines, errors=errors, show_errors=action == "guardar",
        material_index=index, can_create=can_create,
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_GET
def transfer_detail(request: HttpRequest, project_id: UUID, transfer_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    transfer = get_document(ctx, MaterialTransfer, project, transfer_id, "source", "target",
                            "created_by")  # fmt: skip
    lines = (
        scoped_qs(ctx, MaterialTransferLine).filter(transfer=transfer).select_related("material")
        .order_by("order")
    )  # fmt: skip
    return page(request, ctx, project, "suno/materials/transfer_detail.html", transfer=transfer,
                lines=lines)  # fmt: skip
```

En `server/suno/materials/urls.py`, agregar `views_movements` al import, `S = "salidas/<uuid:issue_id>/"` y:

```python
    path("salidas/", views_movements.issue_list, name="issues"),
    path("salidas.xlsx", views_movements.export_issues, name="export_issues"),
    path(S, views_movements.issue_detail, name="issue"),
    path(S + "editar/", views_movements.issue_edit, name="edit_issue"),
    path(S + "borrar/", views_movements.issue_delete, name="delete_issue"),
    path(S + "devolver/", views_movements.issue_return, name="return_issue"),
    path("traslados/", views_movements.transfer_list, name="transfers"),
    path("traslados/<uuid:transfer_id>/", views_movements.transfer_detail, name="transfer"),
```

En `_nav.html`, después de Compras: enlaces "Salidas" (`materials:issues`, `sub == "issues"`) y "Traslados" (`materials:transfers`, `sub == "transfers"`).

- [ ] **Step 6: Plantillas**

`server/suno/core/templates/suno/materials/_material_rows.html` (parámetro `formset`):

```html
{{ formset.management_form }}
<div class="pj-scroll">
<table class="pj-table mat-lines">
  <thead><tr><th>#</th><th>Material</th><th class="num">Cantidad</th></tr></thead>
  <tbody>
  {% for row in formset %}
    <tr>
      <td>{{ forloop.counter }}</td>
      <td class="mat-material"><label class="sr-only" for="{{ row.material.id_for_label }}">Material de la línea {{ forloop.counter }}</label>{{ row.material }}</td>
      <td class="mat-num"><label class="sr-only" for="{{ row.quantity.id_for_label }}">Cantidad de la línea {{ forloop.counter }}</label>{{ row.quantity }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
```

`server/suno/core/templates/suno/materials/issues.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Salidas · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="issues" %}
{% if form %}
<section class="pj-panel">
  <h2>Registrar una salida</h2>
  <form method="post" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.warehouse.label }} {{ form.warehouse }}</label>
      <label>{{ form.issued_on.label }} {{ form.issued_on }}</label>
      <label>{{ form.received_by.label }} {{ form.received_by }}</label>
      <label>{{ form.destination.label }} {{ form.destination }}</label>
      <label>{{ form.task.label }} {{ form.task }}</label>
      <label>{{ form.notes.label }} {{ form.notes }}</label>
    </div>
    {% include "suno/materials/_material_rows.html" with formset=lines %}
    {% include "suno/materials/_datalists.html" %}
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
      <button class="button" type="submit" name="accion" value="guardar">Registrar salida</button>
    </div>
  </form>
</section>
{% endif %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros de salidas">
    <label>Desde <input type="date" name="desde" value="{{ filters.start|date:'Y-m-d' }}"></label>
    <label>Hasta <input type="date" name="hasta" value="{{ filters.end|date:'Y-m-d' }}"></label>
    <label>Bodega <select name="bodega"><option value="">Todas</option>{% for w in all_warehouses %}<option value="{{ w.pk }}" {% if filters.warehouse_id == w.pk %}selected{% endif %}>{{ w.name }}</option>{% endfor %}</select></label>
    <label>Destino <select name="destino"><option value="">Todos</option>{% for key, label in scopes %}<option value="{{ key }}" {% if filters.scope == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Corte <select name="corte"><option value="">Todas</option><option value="sin" {% if filters.cut == "sin" %}selected{% endif %}>Sin cortar</option><option value="con" {% if filters.cut == "con" %}selected{% endif %}>En un corte</option></select></label>
    <button class="button" type="submit">Filtrar</button>
    {% if can_export_material %}<a class="text-link" href="{% url 'materials:export_issues' project.pk %}{% if filters.query %}?{{ filters.query }}{% endif %}">Exportar a Excel</a>{% endif %}
  </form>
  {% if rows %}
  {% if more %}<p class="pj-muted">Se muestran las 200 más recientes: filtra por fechas para ver las demás.</p>{% endif %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Número</th><th>Fecha</th><th>Bodega</th><th>Destino</th><th>Recibe</th><th class="num">Valor</th><th>Corte</th></tr></thead>
    <tbody>
    {% for issue, label in rows %}
      <tr>
        <td><a href="{% url 'materials:issue' project.pk issue.pk %}">{{ issue.code }}</a></td>
        <td>{{ issue.issued_on|date:"d/m/Y" }}</td>
        <td class="pj-wrap">{{ issue.warehouse.name }}</td>
        <td class="pj-name">{{ label }}</td>
        <td class="pj-wrap">{{ issue.received_by|default:"—" }}</td>
        <td class="num">{{ issue.total|signed }}</td>
        <td>{% if issue.cut %}{{ issue.cut.code }}{% else %}<span class="pj-muted">Sin cortar</span>{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay salidas con estos filtros.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/issue_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}Editar {{ issue.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="issues" %}
<section class="pj-panel">
  <h2>Editar salida {{ issue.code }}</h2>
  <p class="pj-muted">Al guardarla, sus movimientos se deshacen y se vuelve a aplicar al promedio de hoy.</p>
  <form method="post" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.warehouse.label }} {{ form.warehouse }}</label>
      <label>{{ form.issued_on.label }} {{ form.issued_on }}</label>
      <label>{{ form.received_by.label }} {{ form.received_by }}</label>
      <label>{{ form.destination.label }} {{ form.destination }}</label>
      <label>{{ form.task.label }} {{ form.task }}</label>
      <label>{{ form.notes.label }} {{ form.notes }}</label>
    </div>
    {% include "suno/materials/_material_rows.html" with formset=lines %}
    {% include "suno/materials/_datalists.html" %}
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
      <button class="button" type="submit" name="accion" value="guardar">Guardar</button>
      <a href="{% url 'materials:issue' project.pk issue.pk %}">Cancelar</a>
    </div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/issue_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Salida {{ issue.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="issues" %}
<section class="pj-panel cst-detail">
  <h2>Salida {{ issue.code }}</h2>
  <dl class="cst-facts">
    <dt>Fecha</dt><dd>{{ issue.issued_on|date:"d/m/Y" }}</dd>
    <dt>Bodega</dt><dd>{{ issue.warehouse.name }}</dd>
    <dt>Destino</dt><dd>{{ label }}</dd>
    <dt>Recibe</dt><dd>{{ issue.received_by|default:"—" }}</dd>
    <dt>Valor</dt><dd>{{ issue.total|money }}</dd>
    <dt>Corte</dt><dd>{% if issue.cut %}{{ issue.cut.code }}{% else %}Sin cortar{% endif %}</dd>
    <dt>Registró</dt><dd>{{ issue.created_by }} · {{ issue.created_at|date:"d/m/Y H:i" }}</dd>
    {% if issue.notes %}<dt>Notas</dt><dd>{{ issue.notes }}</dd>{% endif %}
  </dl>
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Material</th><th class="num">Cantidad</th><th class="num">Costo unitario</th><th class="num">Valor</th><th class="num">Devuelto</th></tr></thead>
    <tbody>{% for line, back in lines %}<tr><td class="pj-name">{{ line.material.code }} · {{ line.material.name }}</td><td class="num">{{ line.quantity|qty }} {{ line.material.unit }}</td><td class="num">{{ line.unit_cost|money }}</td><td class="num">{{ line.amount|signed }}</td><td class="num">{{ back|qty|default:"—" }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  <div class="ctr-actions">
    {% if can_edit %}<a class="button" href="{% url 'materials:edit_issue' project.pk issue.pk %}">Editar</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'materials:delete_issue' project.pk issue.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
  </div>
  {% if returns %}<h3>Devoluciones</h3><ul>{% for r in returns %}<li>{{ r.code }} · {{ r.returned_on|date:"d/m/Y" }} · {{ r.total|money }}</li>{% endfor %}</ul>{% endif %}
</section>
{% if return_form %}
<section class="pj-panel">
  <h2>Registrar devolución</h2>
  <form method="post" action="{% url 'materials:return_issue' project.pk issue.pk %}" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    <div class="pj-form ctr-head">
      <label>{{ return_form.returned_on.label }} {{ return_form.returned_on }}</label>
      <label>{{ return_form.notes.label }} {{ return_form.notes }}</label>
    </div>
    <div class="pj-scroll">
    <table class="pj-table mat-lines">
      <thead><tr><th>Material</th><th class="num">Salió</th><th class="num">Vuelve</th></tr></thead>
      <tbody>{% for line, field in return_form.line_fields %}<tr><td class="pj-name">{{ line.material.code }} · {{ line.material.name }}</td><td class="num">{{ line.quantity|qty }}</td><td class="mat-num"><label class="sr-only" for="{{ field.id_for_label }}">Cantidad que vuelve de {{ line.material.name }}</label>{{ field }}</td></tr>{% endfor %}</tbody>
    </table>
    </div>
    <p class="pj-muted">Vuelve al costo de la salida y llega al libro restando en el corte del periodo.</p>
    <div class="ctr-actions"><button class="button" type="submit">Registrar devolución</button></div>
  </form>
</section>
{% endif %}
{% endblock %}
```

`server/suno/core/templates/suno/materials/transfers.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Traslados · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="transfers" %}
{% if can_create %}
<section class="pj-panel">
  <h2>Nuevo traslado</h2>
  <form method="post" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.source.label }} {{ form.source }}</label>
      <label>{{ form.target.label }} {{ form.target }}</label>
      <label>{{ form.moved_on.label }} {{ form.moved_on }}</label>
      <label>{{ form.notes.label }} {{ form.notes }}</label>
    </div>
    {% include "suno/materials/_material_rows.html" with formset=lines %}
    <datalist id="mat-materials">{% for m in material_index.materials %}<option value="{{ m.label }}">{% endfor %}</datalist>
    <p class="pj-muted">Sale al promedio de la bodega de origen y entra a la otra con el mismo valor. No es costo.</p>
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
      <button class="button" type="submit" name="accion" value="guardar">Registrar traslado</button>
    </div>
  </form>
</section>
{% endif %}
<section class="pj-panel">
  {% if rows %}
  {% if more %}<p class="pj-muted">Se muestran los 200 más recientes.</p>{% endif %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Número</th><th>Fecha</th><th>Sale de</th><th>Entra a</th><th class="num">Valor</th></tr></thead>
    <tbody>{% for t in rows %}<tr><td><a href="{% url 'materials:transfer' project.pk t.pk %}">{{ t.code }}</a></td><td>{{ t.moved_on|date:"d/m/Y" }}</td><td class="pj-wrap">{{ t.source.name }}</td><td class="pj-wrap">{{ t.target.name }}</td><td class="num">{{ t.total|signed }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay traslados.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/transfer_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Traslado {{ transfer.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="transfers" %}
<section class="pj-panel cst-detail">
  <h2>Traslado {{ transfer.code }}</h2>
  <dl class="cst-facts">
    <dt>Fecha</dt><dd>{{ transfer.moved_on|date:"d/m/Y" }}</dd>
    <dt>Sale de</dt><dd>{{ transfer.source.name }}</dd>
    <dt>Entra a</dt><dd>{{ transfer.target.name }}</dd>
    <dt>Valor</dt><dd>{{ transfer.total|money }}</dd>
    <dt>Registró</dt><dd>{{ transfer.created_by }}</dd>
    {% if transfer.notes %}<dt>Notas</dt><dd>{{ transfer.notes }}</dd>{% endif %}
  </dl>
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Material</th><th class="num">Cantidad</th><th class="num">Costo unitario</th><th class="num">Valor</th></tr></thead>
    <tbody>{% for line in lines %}<tr><td class="pj-name">{{ line.material.code }} · {{ line.material.name }}</td><td class="num">{{ line.quantity|qty }} {{ line.material.unit }}</td><td class="num">{{ line.unit_cost|money }}</td><td class="num">{{ line.amount|signed }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  <p class="pj-muted">Un traslado no se edita ni se borra: si hubo un error, registra el traslado de vuelta.</p>
</section>
{% endblock %}
```

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_movements.py suno/materials/tests/test_views.py`
Expected: PASS.

- [ ] **Step 8: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/suno/core/templates/suno/materials
git commit -m "feat(materials): issue, return and transfer pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Ajustes de inventario en la web

**Files:**
- Modify: `server/suno/materials/forms.py`, `server/suno/materials/urls.py`, `server/suno/core/templates/suno/materials/_nav.html`
- Create: `server/suno/materials/views_adjustments.py`
- Create: `server/suno/core/templates/suno/materials/{adjustments,adjustment_form,adjustment_detail}.html`
- Test: `server/suno/materials/tests/test_views_adjustments.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views_adjustments.py`:

```python
"""Ajustes en la web (spec, 3.2 "Ajustes"): conteo que trae lo esperado, "Recalcular" y firma."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import CostEntry, InventoryAdjustment
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class AdjustmentPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "150", "32500.00")])
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/ajustes/"
        self.client.force_login(self.mw.cw.contributor)

    def post(self, action: str, **rows: str) -> Any:
        return self.client.post(self.base + "nuevo/", {
            "accion": action, "warehouse": str(self.mw.warehouse.pk), "counted_on": "2026-03-31",
            "destination": "", "task": "", "notes": "", "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "0", "lineas-0-material": "CEM-01",
            "lineas-0-counted": "145", "lineas-0-unit_cost": "", **rows,
        })  # fmt: skip

    def test_bring_the_stock_recalculate_and_save(self) -> None:
        brought = self.post("traer")
        self.assertContains(brought, 'value="CEM-01 · Cemento gris 50 kg"')
        recalculated = self.post("calcular")
        self.assertContains(recalculated, "<td class=\"num\">150</td>", html=False)
        self.assertFalse(InventoryAdjustment.objects.exists())
        saved = self.post("guardar")
        adjustment = InventoryAdjustment.objects.get()
        self.assertRedirects(saved, f"{self.base}{adjustment.pk}/")

    def test_signed_count_approval_and_the_cost(self) -> None:
        self.post("guardar")
        adjustment = InventoryAdjustment.objects.get()
        url = f"{self.base}{adjustment.pk}/"
        self.client.post(url + "soporte/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        entry = CostEntry.objects.get(inventory_adjustment=adjustment)
        self.assertEqual(entry.amount, 162500)
        self.assertContains(self.client.get(url), "162.500,00")
```

Números: 150 und a 32.500; se cuentan 145 → faltan 5 → 162.500,00.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_adjustments.py`
Expected: FAIL (404 en `/ajustes/`).

- [ ] **Step 3: Formularios**

En `server/suno/materials/forms.py`, agregar `InventoryAdjustment`, `InventoryAdjustmentLine` al import de modelos y `from .adjustments import AdjustmentData, AdjustmentLineData`; y al final:

```python
class AdjustmentForm(forms.Form):
    warehouse = forms.ChoiceField(label="Bodega")
    counted_on = forms.DateField(label="Fecha del conteo", widget=date_input())
    destination = destination_field()
    task = forms.ChoiceField(label="Tarea", required=False)
    notes = forms.CharField(label="Notas", max_length=500, required=False)

    def __init__(
        self,
        *args: Any,
        warehouses: Sequence[Warehouse],
        tasks: Mapping[UUID, Task],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].choices = warehouse_choices(warehouses)
        self.fields["task"].choices = task_choices(tasks)

    def adjustment_data(
        self, lines: Sequence[AdjustmentLineData], catalog: Catalog
    ) -> tuple[AdjustmentData | None, str]:
        data = self.cleaned_data
        task = data.get("task") or ""
        destination, error = parse_destination(
            data.get("destination") or "", catalog, UUID(task) if task else None
        )
        if destination is None:
            return None, f"Destino del faltante: {error}"
        return (
            AdjustmentData(
                warehouse_id=UUID(data["warehouse"]) if data.get("warehouse") else None,
                counted_on=data["counted_on"],
                destination=destination,
                lines=tuple(lines),
                notes=data.get("notes") or "",
            ),
            "",
        )


class AdjustmentLineForm(forms.Form):
    material = material_field()
    counted = forms.DecimalField(
        label="Contado", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_cost = forms.DecimalField(
        label="Costo unitario (solo si se pide)", required=False, max_digits=18,
        decimal_places=2, min_value=0,
    )  # fmt: skip


AdjustmentLineFormSet = formset_factory(AdjustmentLineForm, extra=0)


def read_adjustment_lines(
    formset: Any, index: MaterialIndex
) -> tuple[list[AdjustmentLineData], list[str]]:
    lines: list[AdjustmentLineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        text = (data.get("material") or "").strip()
        if not text:
            continue
        material = index.find(text)
        if material is None:
            errors.append(f"Línea {n}: «{text}» no está en el catálogo; elígelo de la lista.")
        elif data.get("counted") is None:
            errors.append(f"Línea {n}: Escribe lo contado.")
        else:
            lines.append(AdjustmentLineData(material.pk, data["counted"], data.get("unit_cost")))
    return lines, errors


def adjustment_initial(
    adjustment: InventoryAdjustment,
    lines: Sequence[InventoryAdjustmentLine],
    catalog: Catalog,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Con `prefetch_related("item_links")` en el ajuste y `select_related("material")` en
    las líneas."""
    header = {
        "warehouse": str(adjustment.warehouse_id),
        "counted_on": adjustment.counted_on,
        "destination": destination_text(
            catalog,
            adjustment.scope,
            adjustment.chapter_id,
            [link.item_id for link in adjustment.item_links.all()],
        ),
        "task": str(adjustment.task_id or ""),
        "notes": adjustment.notes,
    }
    rows = [
        {
            "material": line.material.label,
            "counted": plain(line.counted),
            "unit_cost": line.given_unit_cost,
        }
        for line in lines
    ]
    return header, rows
```

- [ ] **Step 4: Vistas**

`server/suno/materials/views_adjustments.py`:

```python
"""Ajustes de inventario en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Ajustes").

El formulario de conteo, sin JavaScript: "Traer el inventario de la bodega" llena las filas con
los materiales que tienen saldo; "Recalcular" muestra lo esperado y la diferencia sin guardar."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import prefetch_related_objects
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.core.models import InventoryAdjustment, StockBalance, Warehouse
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.projects.views import _ctx

from . import adjustments
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .destination import destination_label, destination_options, task_label
from .forms import (
    NEW_ROWS,
    AdjustmentForm,
    AdjustmentLineFormSet,
    SupportForm,
    adjustment_initial,
    material_index,
    more_rows,
    plain,
    read_adjustment_lines,
)
from .queries import adjustment_rows
from .views import download, page, run
from .views_purchases import back


def _bring(data: QueryDict, ctx: Any, project: Any) -> QueryDict:
    """El POST con una fila por material con saldo en la bodega elegida."""
    copy = data.copy()
    balances = (
        scoped_qs(ctx, StockBalance)
        .filter(project=project, warehouse_id=data.get("warehouse") or None, quantity__gt=0)
        .select_related("material")
        .order_by("material__code")
    )
    rows = list(balances)
    copy["lineas-TOTAL_FORMS"] = str(len(rows) or 1)
    for n, balance in enumerate(rows):
        copy[f"lineas-{n}-material"] = balance.material.label
        copy[f"lineas-{n}-counted"] = plain(balance.quantity)
        copy[f"lineas-{n}-unit_cost"] = ""
    return copy


@login_required(login_url="platform_login")
@require_GET
def adjustment_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    rows, more = adjustment_rows(ctx, project)
    return page(request, ctx, project, "suno/materials/adjustments.html", rows=rows, more=more)


def _form(request: HttpRequest, project_id: UUID, adjustment_id: UUID | None) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = (
        get_document(ctx, InventoryAdjustment, project, adjustment_id) if adjustment_id else None
    )
    if adjustment is None and not can_create_costs(ctx, project):
        raise PermissionDenied
    if adjustment is not None and (
        adjustment.status not in OPEN_STATES or not can_update_cost(ctx, adjustment)
    ):
        raise PermissionDenied
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    current = adjustments.adjustment_lines(ctx, adjustment) if adjustment else []
    index = material_index(ctx, [line.material_id for line in current])
    warehouses = list(scoped_qs(ctx, Warehouse).filter(project=project, active=True))
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    errors: list[str] = []
    counted: list[adjustments.CountLine] = []
    if request.method == "POST":
        data = request.POST
        if action == "traer":
            data = _bring(data, ctx, project)
        data = more_rows(data, action) or data
        form = AdjustmentForm(data, warehouses=warehouses, tasks=tasks)
        lines = AdjustmentLineFormSet(data, prefix="lineas")
        if action in ("guardar", "calcular") and form.is_valid() and lines.is_valid():
            rows, errors = read_adjustment_lines(lines, index)
            values, error = form.adjustment_data(rows, catalog)
            if error:
                errors.append(error)
            if not errors and values is not None and action == "calcular":
                warehouse = next((w for w in warehouses if w.pk == values.warehouse_id), None)
                if warehouse is None:
                    errors.append("Elige una bodega activa del proyecto.")
                else:
                    errors, counted = adjustments.count_lines(ctx, warehouse, values.lines)
            elif not errors and values is not None:
                try:
                    if adjustment is None:
                        saved = adjustments.create_adjustment(
                            ctx, project, values, catalog=catalog, tasks=tasks
                        )
                    else:
                        saved = adjustments.update_adjustment(
                            ctx, project, adjustment.pk, values, catalog=catalog, tasks=tasks
                        )
                except MaterialRejected as exc:
                    errors = exc.errors
                else:
                    return redirect("materials:adjustment", project.pk, saved.pk)
    elif adjustment is None:
        form = AdjustmentForm(initial={"counted_on": timezone.localdate()},
                              warehouses=warehouses, tasks=tasks)  # fmt: skip
        lines = AdjustmentLineFormSet(prefix="lineas", initial=[{}] * NEW_ROWS)
    else:
        prefetch_related_objects([adjustment], "item_links")
        header, rows_initial = adjustment_initial(adjustment, current, catalog)
        form = AdjustmentForm(initial=header, warehouses=warehouses, tasks=tasks)
        lines = AdjustmentLineFormSet(prefix="lineas", initial=rows_initial)
    return page(
        request, ctx, project, "suno/materials/adjustment_form.html", form=form, lines=lines,
        adjustment=adjustment, errors=errors, counted=counted, material_index=index,
        destination_options=destination_options(catalog),
        show_errors=action in ("guardar", "calcular"),
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def adjustment_new(request: HttpRequest, project_id: UUID) -> HttpResponse:
    return _form(request, project_id, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def adjustment_edit(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    return _form(request, project_id, adjustment_id)


@login_required(login_url="platform_login")
@require_GET
def adjustment_detail(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = get_document(
        ctx, InventoryAdjustment, project, adjustment_id,
        "warehouse", "task", "created_by", "updated_by", "decided_by", "voided_by",
    )  # fmt: skip
    prefetch_related_objects([adjustment], "item_links")
    catalog = load_catalog(ctx, project)
    is_open = adjustment.status in OPEN_STATES
    mine = ctx.user_id in (adjustment.created_by_id, adjustment.updated_by_id)
    url = reverse("materials:adjustment", args=[project.pk, adjustment.pk])
    return page(
        request, ctx, project, "suno/materials/adjustment_detail.html",
        adjustment=adjustment, lines=adjustments.adjustment_lines(ctx, adjustment),
        label=destination_label(
            catalog, adjustment.scope, adjustment.chapter_id,
            [link.item_id for link in adjustment.item_links.all()], task_label(adjustment.task),
        ),
        can_edit=is_open and can_update_cost(ctx, adjustment)
        and (adjustment.status == "draft" or adjustment.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, adjustment),
        can_attach=adjustment.status == "draft" and can_update_cost(ctx, adjustment),
        can_decide=adjustment.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=adjustment.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if adjustment.attachment_id else "Falta el acta de conteo firmada",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "soporte/", "view": url + "soporte/ver/"},
    )  # fmt: skip


def _detail(project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    return redirect("materials:adjustment", project_id, adjustment_id)


@login_required(login_url="platform_login")
@require_POST
def adjustment_delete(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: adjustments.delete_adjustment(ctx, project, adjustment_id),
           "Borrador del ajuste borrado."):  # fmt: skip
        return redirect("materials:adjustments", project.pk)
    return _detail(project.pk, adjustment_id)


@login_required(login_url="platform_login")
@require_POST
def adjustment_support(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    form = SupportForm(request.POST, request.FILES)
    if form.is_valid():
        upload = form.cleaned_data["file"]
        name = upload.name or "acta-de-conteo.pdf"
        run(request, lambda: adjustments.attach_count(ctx, project, adjustment_id, upload, name),
            "Acta de conteo adjunta; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, adjustment_id)


@login_required(login_url="platform_login")
@require_GET
def adjustment_support_file(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    adjustment = get_document(ctx, InventoryAdjustment, project, adjustment_id)
    return download(attachment_url(ctx, project, adjustment))


@login_required(login_url="platform_login")
@require_POST
def adjustment_approve(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: adjustments.approve_adjustment(ctx, project, adjustment_id),
        "Ajuste aprobado.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, adjustment_id))


@login_required(login_url="platform_login")
@require_POST
def adjustment_reject(
    request: HttpRequest, project_id: UUID, adjustment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: adjustments.reject_adjustment(ctx, project, adjustment_id, note),
        "Ajuste rechazado; su autor lo verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, adjustment_id))


@login_required(login_url="platform_login")
@require_POST
def adjustment_void(request: HttpRequest, project_id: UUID, adjustment_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: adjustments.void_adjustment(ctx, project, adjustment_id, note),
        "Ajuste anulado: el inventario y el libro quedaron como antes del ajuste.")  # fmt: skip
    return _detail(project.pk, adjustment_id)
```

En `server/suno/materials/urls.py`, agregar `views_adjustments` al import, `A = "ajustes/<uuid:adjustment_id>/"` y:

```python
    path("ajustes/", views_adjustments.adjustment_list, name="adjustments"),
    path("ajustes/nuevo/", views_adjustments.adjustment_new, name="new_adjustment"),
    path(A, views_adjustments.adjustment_detail, name="adjustment"),
    path(A + "editar/", views_adjustments.adjustment_edit, name="edit_adjustment"),
    path(A + "borrar/", views_adjustments.adjustment_delete, name="delete_adjustment"),
    path(A + "soporte/", views_adjustments.adjustment_support, name="adjustment_support"),
    path(A + "soporte/ver/", views_adjustments.adjustment_support_file, name="adjustment_file"),
    path(A + "aprobar/", views_adjustments.adjustment_approve, name="approve_adjustment"),
    path(A + "rechazar/", views_adjustments.adjustment_reject, name="reject_adjustment"),
    path(A + "anular/", views_adjustments.adjustment_void, name="void_adjustment"),
```

En `_nav.html`, después de Traslados: "Ajustes" (`materials:adjustments`, `sub == "adjustments"`).

- [ ] **Step 5: Plantillas**

`server/suno/core/templates/suno/materials/adjustments.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Ajustes · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="adjustments" %}
<section class="pj-panel">
  {% if can_create_material %}<p class="ctr-actions"><a class="button" href="{% url 'materials:new_adjustment' project.pk %}">Nuevo conteo</a></p>{% endif %}
  {% if rows %}
  {% if more %}<p class="pj-muted">Se muestran los 200 más recientes.</p>{% endif %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Número</th><th>Fecha del conteo</th><th>Bodega</th><th class="num">Faltantes</th><th class="num">Sobrantes</th><th>Estado</th></tr></thead>
    <tbody>{% for a in rows %}<tr><td><a href="{% url 'materials:adjustment' project.pk a.pk %}">{{ a.code }}</a></td><td>{{ a.counted_on|date:"d/m/Y" }}</td><td class="pj-wrap">{{ a.warehouse.name }}</td><td class="num">{{ a.shortage_total|signed }}</td><td class="num">{{ a.surplus_total|signed }}</td><td><span class="bg-label mat-{{ a.status }}">{{ a.get_status_display }}</span></td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">No hay ajustes de inventario.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/adjustment_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{% if adjustment %}Editar {{ adjustment.code }}{% else %}Nuevo conteo{% endif %} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="adjustments" %}
<section class="pj-panel">
  <h2>{% if adjustment %}Editar ajuste {{ adjustment.code }}{% else %}Conteo físico{% endif %}</h2>
  {% if adjustment.status == "rejected" %}<p class="pj-warning">Rechazado: {{ adjustment.decision_note }}. Al guardarlo vuelve a borrador.</p>{% endif %}
  {% if adjustment %}<p class="pj-muted">Al guardarlo se vuelve a leer el inventario y se quita el acta de conteo firmada.</p>{% endif %}
  <form method="post" class="ctr-form"><!-- El token se renderiza justo debajo. nosemgrep: python.django.security.django-no-csrf-token.django-no-csrf-token -->
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.warehouse.label }} {{ form.warehouse }}</label>
      <label>{{ form.counted_on.label }} {{ form.counted_on }}</label>
      <label>Destino del faltante {{ form.destination }}</label>
      <label>{{ form.task.label }} {{ form.task }}</label>
      <label>{{ form.notes.label }} {{ form.notes }}</label>
    </div>
    {{ lines.management_form }}
    <div class="pj-scroll">
    <table class="pj-table mat-lines">
      <thead><tr><th>#</th><th>Material</th><th class="num">Contado</th><th class="num">Costo unitario</th></tr></thead>
      <tbody>
      {% for row in lines %}
        <tr>
          <td>{{ forloop.counter }}</td>
          <td class="mat-material"><label class="sr-only" for="{{ row.material.id_for_label }}">Material de la línea {{ forloop.counter }}</label>{{ row.material }}</td>
          <td class="mat-num"><label class="sr-only" for="{{ row.counted.id_for_label }}">Contado de la línea {{ forloop.counter }}</label>{{ row.counted }}</td>
          <td class="mat-num"><label class="sr-only" for="{{ row.unit_cost.id_for_label }}">Costo unitario de la línea {{ forloop.counter }}</label>{{ row.unit_cost }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% include "suno/materials/_datalists.html" %}
    {% if counted %}
    <div class="pj-scroll">
    <table class="pj-table mat-table">
      <caption>Lo esperado es el saldo de hoy</caption>
      <thead><tr><th>Material</th><th class="num">Esperado</th><th class="num">Contado</th><th class="num">Diferencia</th></tr></thead>
      <tbody>{% for line in counted %}<tr><td class="pj-name">{{ line.material.label }}</td><td class="num">{{ line.expected|qty }}</td><td class="num">{{ line.counted|qty }}</td><td class="num{% if line.difference %} ctr-diff{% endif %}">{{ line.difference|qty }}</td></tr>{% endfor %}</tbody>
    </table>
    </div>
    {% endif %}
    <p class="pj-muted">El costo unitario solo se pide para un sobrante de un material sin saldo ni entradas en la bodega.</p>
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="traer">Traer el inventario de la bodega</button>
      <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
      <button class="text-link" type="submit" name="accion" value="calcular">Recalcular</button>
      <button class="button" type="submit" name="accion" value="guardar">Guardar borrador</button>
      <a href="{% if adjustment %}{% url 'materials:adjustment' project.pk adjustment.pk %}{% else %}{% url 'materials:adjustments' project.pk %}{% endif %}">Cancelar</a>
    </div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/adjustment_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Ajuste {{ adjustment.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="adjustments" %}
<section class="pj-panel cst-detail">
  <h2>Ajuste {{ adjustment.code }} <span class="bg-label mat-{{ adjustment.status }}">{{ adjustment.get_status_display }}</span></h2>
  {% if adjustment.status == "rejected" %}<p class="pj-warning">Motivo del rechazo: {{ adjustment.decision_note }}</p>{% endif %}
  {% if adjustment.status == "voided" %}<p class="pj-warning">Anulado por {{ adjustment.voided_by }}: {{ adjustment.void_note }}</p>{% endif %}
  <dl class="cst-facts">
    <dt>Bodega</dt><dd>{{ adjustment.warehouse.name }}</dd>
    <dt>Fecha del conteo</dt><dd>{{ adjustment.counted_on|date:"d/m/Y" }}</dd>
    <dt>Destino del faltante</dt><dd>{{ label }}</dd>
    {% if adjustment.status == "approved" or adjustment.status == "voided" %}<dt>Faltantes</dt><dd>{{ adjustment.shortage_total|money }}</dd><dt>Sobrantes</dt><dd>{{ adjustment.surplus_total|money }}</dd>{% endif %}
    <dt>Registró</dt><dd>{{ adjustment.created_by }} · modificó {{ adjustment.updated_by }}</dd>
  </dl>
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Material</th><th class="num">Esperado</th><th class="num">Contado</th><th class="num">Diferencia</th><th class="num">Costo unitario</th><th class="num">Valor</th></tr></thead>
    <tbody>{% for line in lines %}<tr><td class="pj-name">{{ line.material.label }}</td><td class="num">{{ line.expected|qty }}</td><td class="num">{{ line.counted|qty }}</td><td class="num">{{ line.difference|qty }}</td><td class="num">{% if line.unit_cost is not None %}{{ line.unit_cost|money }}{% elif line.given_unit_cost %}{{ line.given_unit_cost|money }}{% else %}—{% endif %}</td><td class="num">{{ line.amount|signed|default:"—" }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% include "suno/materials/_support.html" with doc=adjustment label="Acta de conteo firmada" upload=urls.upload view=urls.view %}
  <div class="ctr-actions">
    {% if can_edit %}<a class="button" href="{% url 'materials:edit_adjustment' project.pk adjustment.pk %}">{% if adjustment.status == "rejected" %}Corregir y reenviar{% else %}Editar{% endif %}</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'materials:delete_adjustment' project.pk adjustment.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
  </div>
  {% include "suno/materials/_decide.html" with approve=urls.approve reject=urls.reject void=urls.void back="" %}
</section>
{% endblock %}
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_adjustments.py`
Expected: PASS.

- [ ] **Step 7: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/suno/core/templates/suno/materials
git commit -m "feat(materials): physical count adjustment pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Cortes en la web, PDF para firmar y Excel del corte

**Files:**
- Create: `server/suno/materials/pdf.py`, `server/suno/materials/views_cuts.py`
- Modify: `server/suno/materials/export.py` (`cut_workbook`), `server/suno/materials/forms.py` (`CutForm`), `server/suno/materials/urls.py`
- Modify: `server/suno/core/templates/suno/materials/_nav.html` (Cortes), `_alerts.html` (con enlaces)
- Create: `server/suno/core/templates/suno/materials/{cuts,cut_detail,_cut_groups}.html`
- Test: `server/suno/materials/tests/test_views_cuts.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_views_cuts.py`:

```python
"""Cortes en la web (spec, 3.2 "Cortes" y 3.4): propuesta, PDF, firmado, aprobación, Excel."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import AuditEvent, ConsumptionCut
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class CutPageTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        storage = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.mw = material_world()
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        self.base = f"/plataforma/proyectos/{self.mw.project.pk}/materiales/cortes/"
        self.client.force_login(self.mw.cw.contributor)

    def test_proposal_draft_pdf_signature_approval_and_excel(self) -> None:
        proposal = self.client.get(self.base + "?hasta=2026-03-31")
        self.assertContains(proposal, "Indirecto (proyecto)")
        self.assertContains(proposal, "224.000,00")
        self.assertContains(proposal, 'value="2026-03-10"', html=False)  # el inicio que toca
        created = self.client.post(self.base + "nuevo/", {"hasta": "2026-03-31"})
        cut = ConsumptionCut.objects.get()
        url = f"{self.base}{cut.pk}/"
        self.assertRedirects(created, url)
        document = self.client.get(url + "corte.pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(AuditEvent.objects.filter(action="consumption_cut.exported").exists())
        self.client.post(url + "firmado/", {"file": pdf_upload()})
        self.client.force_login(self.mw.world.supervisor)
        self.assertRedirects(self.client.post(url + "aprobar/"), url)
        cut.refresh_from_db()
        self.assertEqual(cut.status, "approved")
        sheet = self.client.get(url + "corte.xlsx")
        self.assertTrue(sheet.content.startswith(b"PK"))

    def test_the_sheet_warns_when_the_period_changed(self) -> None:
        self.client.post(self.base + "nuevo/", {"hasta": "2026-03-31"})
        cut = ConsumptionCut.objects.get()
        issue(self.mw, [(self.mw.cement, "1")], day=date(2026, 3, 20))
        page = self.client.get(f"{self.base}{cut.pk}/")
        self.assertContains(page, "Los consumos del periodo cambiaron desde que se guardó el corte")
        self.client.post(f"{self.base}{cut.pk}/recalcular/", {"hasta": "2026-03-31"})
        cut.refresh_from_db()
        self.assertEqual(cut.issue_count, 2)
```

Números: 100 und a 32.000; salen 7 → 224.000,00 al destino indirecto.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_views_cuts.py`
Expected: FAIL (404 en `/cortes/`).

- [ ] **Step 3: El PDF del corte**

`server/suno/materials/pdf.py`:

```python
"""Corte de consumos para firmar (costos, pieza 6c, spec 3.4), con reportlab como el acta de
6b (`contracts/pdf.py`). A4 horizontal: destinos con su desglose por material, totales y
firmas. Imprime el resumen guardado del corte (`summary`): lo firmado es lo que se aprueba."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
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

from suno.core.models import ConsumptionCut, Project
from suno.core.templatetags.money_tags import money, qty

__all__ = ["cut_pdf"]

PAGE = landscape(A4)
ACCENT = colors.HexColor("#8C2633")
INK = colors.HexColor("#171717")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def cut_pdf(project: Project, cut: ConsumptionCut, *, generated_on: date) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CutBody", parent=styles["BodyText"], fontName="SunoReport", fontSize=8, leading=10.5
    )
    heading = ParagraphStyle(
        "CutHeading", parent=body, fontSize=15, leading=20, spaceAfter=4, textColor=ACCENT
    )
    section = ParagraphStyle(
        "CutSection", parent=body, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4,
        textColor=INK, keepWithNext=True,
    )  # fmt: skip

    def table(rows: list[list[str]], widths: list[float], *, header: bool = True) -> Table:
        built = Table(
            [[Paragraph(escape(c), body) for c in row] for row in rows],
            colWidths=widths,
            repeatRows=int(header),
        )
        style: list[Any] = [
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), HEAD))
        built.setStyle(TableStyle(style))
        return built

    width = PAGE[0] - 72
    summary = cut.summary or {}
    fingerprint = summary.get("fingerprint", {})
    story: list[Any] = [
        Paragraph(f"Corte de consumos N.º {cut.number}", heading),
        table(
            [
                ["Proyecto", project.name],
                ["Periodo", f"{cut.period_start:%d/%m/%Y} – {cut.period_end:%d/%m/%Y}"],
                ["Salidas y devoluciones",
                 f"{fingerprint.get('issues', 0)} salidas · {fingerprint.get('returns', 0)} "
                 "devoluciones"],
            ],
            [width * 0.22, width * 0.78],
            header=False,
        ),  # fmt: skip
    ]
    for group in summary.get("groups", []):
        story.append(Paragraph(f"{group['label']} · neto {money(group['net'])}", section))
        story.append(
            table(
                [
                    ["Código", "Material", "Unidad", "Salió", "Valor", "Devuelto", "Valor",
                     "Neto"],
                    *[
                        [
                            line["code"], line["name"], line["unit"],
                            qty(line["issued_quantity"]), money(line["issued_value"]),
                            qty(line["returned_quantity"]), money(line["returned_value"]),
                            money(Decimal(line["issued_value"]) - Decimal(line["returned_value"])),
                        ]
                        for line in group["materials"]
                    ],
                ],
                [width * 0.1, width * 0.28, width * 0.07, *[width * 0.11] * 5],
            )
        )  # fmt: skip
    story += [
        Spacer(1, 6),
        table([["Total del corte (costo de materiales del periodo)",
                money(fingerprint.get("total", "0"))]],
              [width * 0.6, width * 0.2], header=False),
        Spacer(1, 28),
        table(
            [["", "", ""], ["Preparó (almacén)", "Revisó (residente o interventoría)", "Aprobó"]],
            [width / 3] * 3,
            header=False,
        ),
        Spacer(1, 6),
        Paragraph(f"Generado por SUNO el {generated_on:%d/%m/%Y}.", body),
    ]  # fmt: skip

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {project.name} · {cut.code}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=PAGE, leftMargin=36, rightMargin=36, topMargin=30, bottomMargin=36,
        title=f"{cut.code} de consumos", author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)  # fmt: skip
    return buffer.getvalue()
```

Las columnas del desglose suman el ancho útil (0,10 + 0,28 + 0,07 + 5 × 0,11 = 1,0).

- [ ] **Step 4: Excel del corte y formulario**

En `server/suno/materials/export.py`, agregar `from suno.core.models import ConsumptionCut` y:

```python
CUT_HEADER = [
    "destino", "codigo", "material", "unidad", "cantidad_salio", "valor_salio",
    "cantidad_devuelta", "valor_devuelto", "neto",
]  # fmt: skip


def cut_workbook(cut: ConsumptionCut) -> bytes:
    """El detalle del corte por destino y material, del resumen guardado."""
    rows = [
        [
            safe_cell(group["label"]),
            safe_cell(line["code"]),
            safe_cell(line["name"]),
            safe_cell(line["unit"]),
            float(Decimal(line["issued_quantity"])),
            float(Decimal(line["issued_value"])),
            float(Decimal(line["returned_quantity"])),
            float(Decimal(line["returned_value"])),
            float(Decimal(line["issued_value"]) - Decimal(line["returned_value"])),
        ]
        for group in (cut.summary or {}).get("groups", [])
        for line in group["materials"]
    ]
    return to_xlsx(CUT_HEADER, rows, sheet_name=f"Corte {cut.number}")
```

En `server/suno/materials/forms.py`:

```python
class CutForm(forms.Form):
    hasta = forms.DateField(label="Hasta", widget=date_input())
```

- [ ] **Step 5: Vistas**

`server/suno/materials/views_cuts.py`:

```python
"""Cortes de consumos en la pestaña "Materiales" (costos, pieza 6c, spec 3.2 "Cortes")."""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.download import content_disposition
from suno.core.models import ConsumptionCut
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.costs.catalog import load_catalog
from suno.projects.views import _ctx, _parse_date

from . import cuts
from .access import get_document, get_materials_project
from .common import MaterialRejected, attachment_url
from .export import cut_workbook
from .pdf import cut_pdf
from .queries import cut_rows
from .views import download, page, run, xlsx
from .views_purchases import back


@login_required(login_url="platform_login")
@require_GET
def cut_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """La lista y la propuesta del próximo corte hasta la fecha que se elija (por defecto,
    hoy). La propuesta no guarda nada."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    start = cuts.default_start(ctx, project)
    end = _parse_date(request.GET.get("hasta")) or timezone.localdate()
    proposal = None
    error = ""
    if start is not None:
        try:
            proposal = cuts.proposal_until(ctx, project, end, load_catalog(ctx, project))
        except MaterialRejected as exc:
            error = exc.errors[0]
    rows, more = cut_rows(ctx, project)
    return page(
        request, ctx, project, "suno/materials/cuts.html", rows=rows, more=more, start=start,
        end=end, proposal=proposal, error=error,
    )  # fmt: skip


def _detail(project_id: UUID, cut_id: UUID) -> HttpResponse:
    return redirect("materials:cut", project_id, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_create(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    end = _parse_date(request.POST.get("hasta")) or timezone.localdate()
    created: list[ConsumptionCut] = []
    if run(request, lambda: created.append(cuts.create_cut(ctx, project, end)),
           "Corte guardado en borrador: descarga el PDF, hazlo firmar y súbelo."):  # fmt: skip
        return _detail(project.pk, created[0].pk)
    return redirect(f"{reverse('materials:cuts', args=[project.pk])}?hasta={end.isoformat()}")


@login_required(login_url="platform_login")
@require_GET
def cut_detail(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    cut = get_document(ctx, ConsumptionCut, project, cut_id, "created_by", "updated_by",
                       "decided_by", "voided_by")  # fmt: skip
    is_open = cut.status in OPEN_STATES
    changed = False
    if is_open:
        live = cuts.build_proposal(ctx, project, cut.period_start, cut.period_end,
                                   load_catalog(ctx, project))  # fmt: skip
        changed = (
            live.fingerprint() != (cut.summary or {}).get("fingerprint")
            or cuts.default_start(ctx, project) != cut.period_start
        )
    mine = ctx.user_id in (cut.created_by_id, cut.updated_by_id)
    url = reverse("materials:cut", args=[project.pk, cut.pk])
    return page(
        request, ctx, project, "suno/materials/cut_detail.html", cut=cut, changed=changed,
        groups=(cut.summary or {}).get("groups", []),
        can_edit=is_open and can_update_cost(ctx, cut)
        and (cut.status == "draft" or cut.created_by_id == ctx.user_id),
        can_delete=is_open and can_delete_cost(ctx, cut),
        can_attach=cut.status == "draft" and can_update_cost(ctx, cut),
        can_decide=cut.status == "draft" and can_approve_costs(ctx, project) and not mine,
        can_void=cut.status == "approved" and can_reverse_costs(ctx, project),
        needs="" if cut.attachment_id else "Falta el reporte firmado",
        urls={"approve": url + "aprobar/", "reject": url + "rechazar/", "void": url + "anular/",
              "upload": url + "firmado/", "view": url + "firmado/ver/"},
    )  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def cut_recalculate(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    end = _parse_date(request.POST.get("hasta")) or timezone.localdate()
    run(request, lambda: cuts.recalculate_cut(ctx, project, cut_id, end),
        "Corte recalculado: vuelve a imprimirlo y a cargar el reporte firmado.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_delete(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    if run(request, lambda: cuts.delete_cut(ctx, project, cut_id), "Borrador del corte borrado."):
        return redirect("materials:cuts", project.pk)
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_POST
def cut_signed(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    upload = request.FILES.get("file")
    if upload is not None:
        name = upload.name or "reporte-firmado.pdf"
        run(request, lambda: cuts.attach_signed_report(ctx, project, cut_id, upload, name),
            "Reporte firmado adjunto; ya se puede aprobar.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_GET
def cut_signed_file(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    return download(attachment_url(ctx, project, get_document(ctx, ConsumptionCut, project,
                                                              cut_id)))  # fmt: skip


@login_required(login_url="platform_login")
@require_POST
def cut_approve(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    run(request, lambda: cuts.approve_cut(ctx, project, cut_id),
        "Corte aprobado: sus costos están en el libro y el periodo quedó cerrado.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, cut_id))


@login_required(login_url="platform_login")
@require_POST
def cut_reject(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: cuts.reject_cut(ctx, project, cut_id, note),
        "Corte rechazado; su autor lo verá con el motivo.")  # fmt: skip
    return back(request, project.pk, _detail(project.pk, cut_id))


@login_required(login_url="platform_login")
@require_POST
def cut_void(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    note = request.POST.get("motivo", "")
    run(request, lambda: cuts.void_cut(ctx, project, cut_id, note),
        "Corte anulado: sus reversos están en el libro y el periodo se reabrió.")  # fmt: skip
    return _detail(project.pk, cut_id)


@login_required(login_url="platform_login")
@require_GET
def cut_document(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    """El corte para imprimir y firmar (auditado)."""
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    cut = get_document(ctx, ConsumptionCut, project, cut_id)
    if not can_export_costs(ctx, project):
        raise PermissionDenied
    content = cut_pdf(project, cut, generated_on=timezone.localdate())
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONSUMPTION_CUT_EXPORTED,
            target=f"consumption_cut:{cut.pk}",
            metadata={"project": str(project.pk), "number": cut.number, "status": cut.status},
        )
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition(
        f"corte-{cut.number}", ".pdf", fallback="corte"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def cut_sheet(request: HttpRequest, project_id: UUID, cut_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_materials_project(ctx, project_id)
    cut = get_document(ctx, ConsumptionCut, project, cut_id)
    return xlsx(ctx, project, cut_workbook(cut), f"corte-{cut.number}", "corte",
                cut=str(cut.pk))  # fmt: skip
```

En `server/suno/materials/urls.py`, agregar `views_cuts` al import, `C = "cortes/<uuid:cut_id>/"` y:

```python
    path("cortes/", views_cuts.cut_list, name="cuts"),
    path("cortes/nuevo/", views_cuts.cut_create, name="new_cut"),
    path(C, views_cuts.cut_detail, name="cut"),
    path(C + "recalcular/", views_cuts.cut_recalculate, name="recalculate_cut"),
    path(C + "borrar/", views_cuts.cut_delete, name="delete_cut"),
    path(C + "firmado/", views_cuts.cut_signed, name="cut_signed"),
    path(C + "firmado/ver/", views_cuts.cut_signed_file, name="cut_file"),
    path(C + "aprobar/", views_cuts.cut_approve, name="approve_cut"),
    path(C + "rechazar/", views_cuts.cut_reject, name="reject_cut"),
    path(C + "anular/", views_cuts.cut_void, name="void_cut"),
    path(C + "corte.pdf", views_cuts.cut_document, name="cut_pdf"),
    path(C + "corte.xlsx", views_cuts.cut_sheet, name="cut_xlsx"),
```

En `_nav.html`, después de Ajustes: "Cortes" (`materials:cuts`, `sub == "cuts"`), y quitar el comentario `{# … #}`. En `_alerts.html`, ya existen todas las vistas: la línea de la lista queda

```html
  <ul class="ctr-alerts">{% for alert in material_alerts %}<li><a href="{% url alert.view project.pk %}{{ alert.query }}">{{ alert.text }}</a></li>{% endfor %}</ul>
```

- [ ] **Step 6: Plantillas**

`server/suno/core/templates/suno/materials/_cut_groups.html` (parámetro `groups`: la lista del resumen o, en la propuesta, `proposal.snapshot.groups`):

```html
{% load money_tags %}
<div class="mat-groups">
{% for group in groups %}
  <h3>{{ group.label }} · {{ group.net|money }}</h3>
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Material</th><th>Unidad</th><th class="num">Salió</th><th class="num">Valor</th><th class="num">Devuelto</th><th class="num">Valor</th></tr></thead>
    <tbody>{% for line in group.materials %}<tr><td class="pj-name">{{ line.code }} · {{ line.name }}</td><td>{{ line.unit }}</td><td class="num">{{ line.issued_quantity|qty }}</td><td class="num">{{ line.issued_value|signed }}</td><td class="num">{{ line.returned_quantity|qty }}</td><td class="num">{{ line.returned_value|signed }}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
{% endfor %}
</div>
```

`server/suno/core/templates/suno/materials/cuts.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Cortes · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="cuts" %}
<section class="pj-panel">
  <h2>Próximo corte</h2>
  {% if start %}
  <form method="get" class="pj-filters" aria-label="Periodo del corte">
    <label>Desde <input type="date" value="{{ start|date:'Y-m-d' }}" disabled></label>
    <label>Hasta <input type="date" name="hasta" value="{{ end|date:'Y-m-d' }}"></label>
    <button class="button" type="submit">Ver propuesta</button>
  </form>
  {% if error %}<p class="pj-warning">{{ error }}</p>{% endif %}
  {% if proposal %}
  <p>Salió {{ proposal.issued|money }} · devuelto {{ proposal.returned|money }} · <strong>al libro {{ proposal.total|money }}</strong> ({{ proposal.issue_ids|length }} salidas, {{ proposal.return_ids|length }} devoluciones).</p>
  {% include "suno/materials/_cut_groups.html" with groups=proposal.snapshot.groups %}
  {% if can_create_material %}<form method="post" action="{% url 'materials:new_cut' project.pk %}" class="ctr-actions">{% csrf_token %}<input type="hidden" name="hasta" value="{{ end|date:'Y-m-d' }}"><button class="button" type="submit">Guardar el corte en borrador</button></form>{% endif %}
  {% endif %}
  {% else %}<p class="pj-empty">No hay salidas ni devoluciones sin cortar.</p>{% endif %}
</section>
<section class="pj-panel">
  <h2>Cortes</h2>
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table mat-table">
    <thead><tr><th>Corte</th><th>Periodo</th><th class="num">Total</th><th>Estado</th></tr></thead>
    <tbody>{% for c in rows %}<tr><td><a href="{% url 'materials:cut' project.pk c.pk %}">{{ c.code }}</a></td><td>{{ c.period_start|date:"d/m/Y" }} – {{ c.period_end|date:"d/m/Y" }}</td><td class="num">{{ c.total|signed }}</td><td><span class="bg-label mat-{{ c.status }}">{{ c.get_status_display }}</span></td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">Todavía no hay cortes.</p>{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/materials/cut_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{{ cut.code }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="materials" %}
{% include "suno/materials/_nav.html" with sub="cuts" %}
<section class="pj-panel cst-detail">
  <h2>{{ cut.code }} de consumos <span class="bg-label mat-{{ cut.status }}">{{ cut.get_status_display }}</span></h2>
  {% if changed %}<p class="pj-warning" role="alert">Los consumos del periodo cambiaron desde que se guardó el corte: recalcúlalo, imprímelo y vuelve a cargar el reporte firmado. Así no se puede aprobar.</p>{% endif %}
  {% if cut.status == "rejected" %}<p class="pj-warning">Motivo del rechazo: {{ cut.decision_note }}</p>{% endif %}
  {% if cut.status == "voided" %}<p class="pj-warning">Anulado por {{ cut.voided_by }}: {{ cut.void_note }}</p>{% endif %}
  <dl class="cst-facts">
    <dt>Periodo</dt><dd>{{ cut.period_start|date:"d/m/Y" }} – {{ cut.period_end|date:"d/m/Y" }}</dd>
    <dt>Al libro</dt><dd>{{ cut.total|money }} ({{ cut.issue_count }} salidas, {{ cut.return_count }} devoluciones)</dd>
    <dt>Preparó</dt><dd>{{ cut.created_by }} · recalculó {{ cut.updated_by }}</dd>
    {% if cut.decided_by %}<dt>{% if cut.status == "rejected" %}Rechazó{% else %}Aprobó{% endif %}</dt><dd>{{ cut.decided_by }} · {{ cut.decided_at|date:"d/m/Y H:i" }}</dd>{% endif %}
  </dl>
  {% include "suno/materials/_cut_groups.html" with groups=groups %}
  <p class="ctr-actions">{% if can_export_material %}<a class="text-link" href="{% url 'materials:cut_pdf' project.pk cut.pk %}">PDF para firmar</a> <a class="text-link" href="{% url 'materials:cut_xlsx' project.pk cut.pk %}">Excel</a>{% endif %}</p>
  {% include "suno/materials/_support.html" with doc=cut label="Reporte firmado" upload=urls.upload view=urls.view %}
  <div class="ctr-actions">
    {% if can_edit %}<form method="post" action="{% url 'materials:recalculate_cut' project.pk cut.pk %}" class="cst-reject">{% csrf_token %}<label>Hasta <input type="date" name="hasta" value="{{ cut.period_end|date:'Y-m-d' }}"></label><button class="button" type="submit">Recalcular</button></form>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'materials:delete_cut' project.pk cut.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
  </div>
  {% include "suno/materials/_decide.html" with approve=urls.approve reject=urls.reject void=urls.void back="" %}
</section>
{% endblock %}
```

En `Proposal` (Task 10) `snapshot` es un método: en la plantilla `proposal.snapshot.groups` lo llama sin argumentos (Django llama a los métodos sin argumentos), así que funciona tal cual.

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials`
Expected: PASS (todo el paquete: los avisos ya enlazan a vistas que existen).

- [ ] **Step 8: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials && mypy suno/materials --config-file pyproject.toml"
git add server/suno/materials server/suno/core/templates/suno/materials
git commit -m "feat(materials): consumption cut pages with signed PDF and Excel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: El libro, la bandeja y el control conocen los materiales

**Files:**
- Modify: `server/suno/costs/ledger.py`, `server/suno/costs/services.py`, `server/suno/costs/access.py`, `server/suno/costs/views.py`
- Modify: `server/suno/core/templates/suno/costs/{list,detail,inbox}.html`
- Modify: `server/suno/control/views.py`, `server/suno/core/templates/suno/control/dashboard.html`
- Test: `server/suno/materials/tests/test_integration.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/materials/tests/test_integration.py`:

```python
"""Costos y Control con materiales (spec, 1.4 y 3.3): orígenes del libro, reverso, bandeja y
valor en bodega."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from suno.core.models import (
    ConsumptionCut,
    CostEntry,
    CostScope,
    MaterialPurchase,
    PurchaseKind,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.catalog import load_catalog
from suno.costs.ledger import ORIGINS, LedgerFilters, ledger_page
from suno.costs.services import CostRejected
from suno.costs.tests.factories import entry, support_blob
from suno.materials import cuts, purchases
from suno.materials.destination import INDIRECT
from suno.materials.purchases import PurchaseData, PurchaseLineData

from .factories import issue, material_world, stock_in

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)


@skip_without_db
class LedgerIntegrationTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mw = material_world()
        self.project = self.mw.project
        self.admin = self.mw.ctx()
        self.supervisor = self.mw.ctx(self.mw.world.supervisor)
        contributor = self.mw.ctx(self.mw.cw.contributor)
        stock_in(self.mw, [(self.mw.cement, "100", "32000.00")])  # CM-001, a bodega
        direct = purchases.create_purchase(
            contributor, self.project,
            PurchaseData(str(PurchaseKind.DIRECT), None, "Arenas SAS", "", "invoice", "F-9",
                         date(2026, 3, 5),
                         (PurchaseLineData(self.mw.sand.pk, D("2"), D("100000.00"), INDIRECT),)),
        )  # fmt: skip
        MaterialPurchase.objects.filter(pk=direct.pk).update(attachment=support_blob("f"))
        self.direct = purchases.approve_purchase(self.supervisor, self.project, direct.pk)
        issue(self.mw, [(self.mw.cement, "7")], day=date(2026, 3, 10))
        cut = cuts.create_cut(contributor, self.project, date(2026, 3, 31))
        ConsumptionCut.objects.filter(pk=cut.pk).update(attachment=support_blob("r"))
        self.cut = cuts.approve_cut(self.supervisor, self.project, cut.pk)
        self.manual = entry(self.mw.world, status="approved", description="Manual",
                            scope=CostScope.INDIRECT)  # fmt: skip
        self.catalog = load_catalog(self.admin, self.project)

    def descriptions(self, origin: str) -> list[str]:
        page = ledger_page(self.admin, self.project, LedgerFilters.from_query({"origen": origin}),
                           page=1, catalog=self.catalog)  # fmt: skip
        return [line.description for line in page.lines]

    def test_origin_filters_and_labels(self) -> None:
        self.assertEqual(
            (ORIGINS["compras"], ORIGINS["cortes"], ORIGINS["ajustes"]),
            ("Compras", "Cortes", "Ajustes"),
        )
        self.assertEqual(self.descriptions("compras"), ["Compra CM-002 · ARE-01 Arena de río"])
        self.assertEqual(self.descriptions("cortes"),
                         ["Corte 1 de consumos · Indirecto (proyecto)"])  # fmt: skip
        self.assertEqual(self.descriptions("libro"), ["Manual"])
        page = ledger_page(self.admin, self.project,
                           LedgerFilters.from_query({"origen": "cortes"}), page=1,
                           catalog=self.catalog)  # fmt: skip
        line = page.lines[0]
        self.assertEqual((line.origin_label, line.source_label), ("Corte", "Corte 1"))
        self.assertEqual(
            line.source_url,
            f"/plataforma/proyectos/{self.project.pk}/materiales/cortes/{self.cut.pk}/",
        )

    def test_the_ledger_refuses_to_reverse_a_material_cost(self) -> None:
        cost = CostEntry.objects.get(material_purchase=self.direct)
        with self.assertRaises(CostRejected) as caught:
            cost_services.reverse(self.admin, self.project, cost.pk, "No")
        self.assertEqual(caught.exception.errors, [cost_services.FROM_MATERIALS])

    def test_pages_of_costs_and_control(self) -> None:
        self.client.force_login(self.mw.world.admin)
        base = f"/plataforma/proyectos/{self.project.pk}"
        cost = CostEntry.objects.get(consumption_cut=self.cut)
        detail = self.client.get(f"{base}/costos/{cost.pk}/")
        self.assertContains(detail, f"{base}/materiales/cortes/{self.cut.pk}/")
        self.assertNotContains(detail, "Reversar")
        purchases.create_purchase(
            self.mw.ctx(self.mw.cw.contributor), self.project,
            PurchaseData(str(PurchaseKind.WAREHOUSE), self.mw.warehouse.pk, "Ferretería", "",
                         "invoice", "F-10", date(2026, 4, 1),
                         (PurchaseLineData(self.mw.cement.pk, D("1"), D("1.00")),)),
        )  # fmt: skip
        inbox = self.client.get(f"{base}/costos/por-aprobar/")
        self.assertContains(inbox, "Compra CM-003")
        self.assertContains(inbox, f"{base}/materiales/compras/")
        control = self.client.get(f"{base}/control/?desglose=item&corte=2026-04-15")
        self.assertContains(control, "Valor en bodega del proyecto")
        self.assertContains(control, "2.976.000,00")
```

Números: CM-001 a bodega 100 und a 32.000; la compra directa (CM-002) es 2 m3 de arena a 100.000 = 200.000,00; la salida de 7 und (224.000,00) entra en el corte 1; quedan 93 und = 2.976.000,00 en bodega.

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_integration.py`
Expected: FAIL (`KeyError: 'compras'` y el reverso no se rechaza con `FROM_MATERIALS`).

- [ ] **Step 3: El libro**

En `server/suno/costs/ledger.py`:

1. Imports: `from django.urls import reverse` y `from suno.core.models.cost import ORIGIN_FIELDS`.
2. Reemplazar las constantes de origen por:

```python
ALL, LEDGER_ONLY, AUTOMATIC = "todos", "libro", "automaticos"
CERTIFICATES, PURCHASES, CUTS, ADJUSTMENTS = "actas", "compras", "cortes", "ajustes"
ORIGINS = {
    ALL: "Todos",
    LEDGER_ONLY: "Manual",
    CERTIFICATES: "Actas",
    PURCHASES: "Compras",
    CUTS: "Cortes",
    ADJUSTMENTS: "Ajustes",
    AUTOMATIC: "Solo automáticos",
}
#: El campo de origen de `CostEntry` de cada filtro de documento (6b y 6c).
DOCUMENT_ORIGINS = {
    CERTIFICATES: "certificate",
    PURCHASES: "material_purchase",
    CUTS: "consumption_cut",
    ADJUSTMENTS: "inventory_adjustment",
}
CERTIFICATE_LABEL = "Acta"
SOURCE_LABELS = {"certificate": "Acta", "purchase": "Compra", "cut": "Corte", "adjustment": "Ajuste"}
```

3. En `LedgerFilters.shows_automatic`, `self.origin not in (LEDGER_ONLY, *DOCUMENT_ORIGINS)`.
4. En `LedgerLine`, después de `certificate_label`:

```python
    #: El documento que escribió el movimiento: "certificate", "purchase", "cut", "adjustment".
    source_kind: str = ""
    source_label: str = ""
    source_url: str = ""
```

y `origin_label` queda:

```python
    @property
    def origin_label(self) -> str:
        return SOURCE_LABELS.get(self.source_kind) or ORIGIN_LABELS[self.origin]
```

5. En `manual_entries`, reemplazar el bloque del filtro de origen por:

```python
    if filters.origin == LEDGER_ONLY:
        entries = entries.filter(**{f"{field}__isnull": True for field in ORIGIN_FIELDS})
    elif filters.origin in DOCUMENT_ORIGINS:
        entries = entries.filter(**{f"{DOCUMENT_ORIGINS[filters.origin]}__isnull": False})
```

6. En `_with_details`, `select_related(…, "certificate__contract", "material_purchase", "consumption_cut", "inventory_adjustment")`.
7. Antes de `_entry_line`:

```python
def entry_source(entry: CostEntry) -> tuple[str, str, str]:
    """(tipo, etiqueta, URL) del documento que escribió el movimiento; vacío si es manual."""
    project_id = entry.project_id
    if entry.certificate is not None:
        certificate = entry.certificate
        return (
            "certificate",
            f"Acta {certificate.number} · Contrato {certificate.contract.number}",
            reverse("contracts:certificate",
                    args=[project_id, certificate.contract_id, certificate.pk]),
        )  # fmt: skip
    if entry.material_purchase is not None:
        purchase = entry.material_purchase
        return "purchase", f"Compra {purchase.code}", reverse(
            "materials:purchase", args=[project_id, purchase.pk]
        )
    if entry.consumption_cut is not None:
        cut = entry.consumption_cut
        return "cut", cut.code, reverse("materials:cut", args=[project_id, cut.pk])
    if entry.inventory_adjustment is not None:
        adjustment = entry.inventory_adjustment
        return "adjustment", f"Ajuste {adjustment.code}", reverse(
            "materials:adjustment", args=[project_id, adjustment.pk]
        )
    return "", "", ""
```

y en `_entry_line`, calcular `kind, label, url = entry_source(entry)` y pasar `source_kind=kind, source_label=label, source_url=url` al `LedgerLine` (los campos `certificate_*` siguen igual para la 6b).

- [ ] **Step 4: Reverso, acceso y vistas de Costos**

En `server/suno/costs/services.py`, después de `FROM_CERTIFICATE`:

```python
FROM_MATERIALS = (
    "Este costo viene de una compra, un corte o un ajuste de materiales: se anula el documento "
    "desde la pestaña Materiales."
)
```

y en `reverse` reemplazar `if original.certificate_id is not None: raise CostRejected([FROM_CERTIFICATE])` por:

```python
        if original.from_document:
            raise CostRejected(
                [FROM_CERTIFICATE if original.certificate_id is not None else FROM_MATERIALS]
            )
```

En `server/suno/costs/access.py`, `get_entry` agrega `"material_purchase", "consumption_cut", "inventory_adjustment"` a su `select_related`.

En `server/suno/costs/views.py`:

1. Imports: `from suno.materials.queries import inbox_count as material_inbox_count, inbox_items as material_inbox_items` y `entry_source` desde `.ledger`.
2. En `_context`, el contador suma `+ material_inbox_count(ctx, project)` junto a `inbox_count(ctx, project)`.
3. En `_contract_warning`, la primera condición pasa a `entry.from_document`.
4. En `detail`, `can_reverse` usa `and not entry.from_document` en lugar de `entry.certificate_id is None`, y el contexto agrega `"source": entry_source(entry)`.
5. En `inbox`, el contexto agrega `"material_items": material_inbox_items(ctx, project)`.

En `server/suno/core/templates/suno/costs/detail.html`, reemplazar la línea `{% if entry.certificate %}…{% endif %}` por:

```html
  {% if source.1 %}<p class="pj-muted">Viene de <a href="{{ source.2 }}">{{ source.1 }}</a>: se anula desde el documento.</p>{% endif %}
```

En `server/suno/core/templates/suno/costs/list.html`, reemplazar `{% if line.certificate_id %}<br><a class="cst-mark" href="{% url 'contracts:certificate' project.pk line.contract_id line.certificate_id %}">{{ line.certificate_label }}</a>{% endif %}` por:

```html
{% if line.source_url %}<br><a class="cst-mark" href="{{ line.source_url }}">{{ line.source_label }}</a>{% endif %}
```

En `server/suno/core/templates/suno/costs/inbox.html`, el vacío queda `{% elif not contract_items.count and not material_items.count %}` y, después de la sección de contratos:

```html
  {% if material_items.count %}
  <h3>Materiales: compras, ajustes y cortes</h3>
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <thead><tr><th>Qué</th><th>Detalle</th><th class="num">Valor</th><th>Preparó</th><th>Soporte</th><th>Decisión</th></tr></thead>
    <tbody>
    {% for p in material_items.purchases %}
      <tr>
        <td><a href="{% url 'materials:purchase' project.pk p.pk %}">Compra {{ p.code }}</a></td>
        <td class="pj-wrap">{{ p.get_kind_display }} · {{ p.supplier }}</td>
        <td class="num">{{ p.total|signed }}</td>
        <td class="pj-wrap">{{ p.created_by }}</td>
        <td>{% if p.attachment_id %}<a href="{% url 'materials:purchase_file' project.pk p.pk %}" target="_blank" rel="noopener">Factura</a>{% else %}<span class="pj-late">Sin factura</span>{% endif %}</td>
        <td>{% url 'materials:approve_purchase' project.pk p.pk as approve %}{% url 'materials:reject_purchase' project.pk p.pk as reject %}{% include "suno/materials/_decide.html" with can_decide=True can_void=False needs=p.attachment_id|yesno:",Falta la factura" back="bandeja" %}</td>
      </tr>
    {% endfor %}
    {% for a in material_items.adjustments %}
      <tr>
        <td><a href="{% url 'materials:adjustment' project.pk a.pk %}">Ajuste {{ a.code }}</a></td>
        <td class="pj-wrap">Conteo del {{ a.counted_on|date:"d/m/Y" }} · {{ a.warehouse.name }}</td>
        <td class="num">—</td>
        <td class="pj-wrap">{{ a.created_by }}</td>
        <td>{% if a.attachment_id %}<a href="{% url 'materials:adjustment_file' project.pk a.pk %}" target="_blank" rel="noopener">Acta</a>{% else %}<span class="pj-late">Sin acta</span>{% endif %}</td>
        <td>{% url 'materials:approve_adjustment' project.pk a.pk as approve %}{% url 'materials:reject_adjustment' project.pk a.pk as reject %}{% include "suno/materials/_decide.html" with can_decide=True can_void=False needs=a.attachment_id|yesno:",Falta el acta" back="bandeja" %}</td>
      </tr>
    {% endfor %}
    {% for c in material_items.cuts %}
      <tr>
        <td><a href="{% url 'materials:cut' project.pk c.pk %}">{{ c.code }} de consumos</a></td>
        <td class="pj-wrap">{{ c.period_start|date:"d/m/Y" }} – {{ c.period_end|date:"d/m/Y" }}</td>
        <td class="num">{{ c.total|signed }}</td>
        <td class="pj-wrap">{{ c.created_by }}</td>
        <td>{% if c.attachment_id %}<a href="{% url 'materials:cut_file' project.pk c.pk %}" target="_blank" rel="noopener">Reporte firmado</a>{% else %}<span class="pj-late">Sin firmar</span>{% endif %}</td>
        <td>{% url 'materials:approve_cut' project.pk c.pk as approve %}{% url 'materials:reject_cut' project.pk c.pk as reject %}{% include "suno/materials/_decide.html" with can_decide=True can_void=False needs=c.attachment_id|yesno:",Falta el reporte firmado" back="bandeja" %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
```

(`_decide.html` usa `approve` y `reject` del contexto: `{% url … as approve %}` los deja en el bloque. `yesno:",Falta…"` da vacío si hay soporte.)

- [ ] **Step 5: El control**

En `server/suno/control/views.py`, importar `from suno.materials.queries import stock_value` y, en `dashboard`, después de `committed = …`:

```python
    stock = stock_value(ctx, project) if costs and options.tab == "item" and result else None
```

y en el contexto `"stock_value": stock,`. En `server/suno/core/templates/suno/control/dashboard.html`, justo antes de la línea del aviso `committed_over_budget`:

```html
  {% if stock_value is not None %}<p class="pj-muted">Valor en bodega del proyecto: {{ stock_value|money }} (inventario; no es costo).</p>{% endif %}
```

(La plantilla ya carga `money_tags`.)

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_integration.py suno/costs suno/contracts suno/control`
Expected: PASS (las pruebas de la 6b sobre `certificate_label` y el origen "Actas" siguen verdes).

- [ ] **Step 7: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno --config-file pyproject.toml"
git add server/suno/costs server/suno/control server/suno/materials server/suno/core/templates
git commit -m "feat(costs): ledger origins, inbox and control know purchases, cuts and adjustments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Prueba de carga (2.000 materiales, 5 bodegas y 50.000 movimientos)

**Files:**
- Test: `server/suno/materials/tests/test_performance.py`

- [ ] **Step 1: Escribir la prueba**

`server/suno/materials/tests/test_performance.py`:

```python
"""Prueba de carga de la pestaña "Materiales" (spec, 4 "Carga"): 2.000 materiales, 5 bodegas y
50.000 movimientos (10.000 entradas de compra y 40.000 salidas). Inventario, kárdex y propuesta
del corte en menos de 1 s (2 s en CI)."""

from __future__ import annotations

import time
import unittest
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from suno.control.tests.test_performance import LIMIT_SECONDS, MEASUREMENTS
from suno.core.access import SectionRole
from suno.core.models import (
    ConsumptionCut,
    Material,
    MaterialIssue,
    MaterialIssueLine,
    MaterialPurchase,
    MaterialPurchaseLine,
    StockBalance,
    StockMovement,
    Warehouse,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs, support_blob
from suno.projects.tests.factories import FieldWorld, make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MATERIALS = 2000
WAREHOUSES = 5
ISSUES = 8000
LINES_PER_ISSUE = 5
#: Las salidas usan los primeros 200 materiales: así un par tiene unas 40 salidas en su kárdex.
ISSUED_MATERIALS = 200
FIRST_DAY = date(2025, 1, 6)
CUT_END = date(2026, 2, 28)
TODAY = date(2026, 4, 15)
PURCHASED = D("1000")


def load(world: FieldWorld) -> Warehouse:
    org, project, admin = world.org, world.project, world.admin
    now = timezone.now()
    blob = support_blob("carga de materiales")
    materials = Material.objects.bulk_create(
        [Material(org=org, code=f"MAT-{n:04d}", name=f"Material de carga {n}", unit="und",
                  category=f"Categoría {n % 20}") for n in range(MATERIALS)]
    )  # fmt: skip
    warehouses = Warehouse.objects.bulk_create(
        [Warehouse(org=org, project=project, name=f"Bodega {w}", created_by=admin,
                   updated_by=admin) for w in range(WAREHOUSES)]
    )  # fmt: skip
    price = {m.pk: D(1000 + n) for n, m in enumerate(materials)}
    purchases: list[MaterialPurchase] = []
    lines: list[MaterialPurchaseLine] = []
    movements: list[StockMovement] = []
    sequence: dict[tuple[object, object], int] = defaultdict(int)
    quantity: dict[tuple[object, object], Decimal] = defaultdict(Decimal)
    number = 0
    for warehouse in warehouses:
        for chunk in range(0, MATERIALS, 200):
            number += 1
            purchase = MaterialPurchase(
                org=org, project=project, number=number, kind="warehouse", warehouse=warehouse,
                supplier="Proveedor de carga", document_type="invoice", purchased_on=FIRST_DAY,
                status="approved", created_by=admin, updated_by=admin,
                decided_by=world.supervisor, decided_at=now, attachment=blob,
            )  # fmt: skip
            purchases.append(purchase)
            for order, material in enumerate(materials[chunk : chunk + 200], start=1):
                amount = PURCHASED * price[material.pk]
                lines.append(MaterialPurchaseLine(
                    org=org, purchase=purchase, order=order, material=material,
                    quantity=PURCHASED, unit_price=price[material.pk], amount=amount, scope="",
                ))  # fmt: skip
                pair = (warehouse.pk, material.pk)
                sequence[pair] += 1
                quantity[pair] += PURCHASED
                movements.append(StockMovement(
                    org=org, project=project, warehouse=warehouse, material=material,
                    moved_on=FIRST_DAY, kind="purchase", quantity=PURCHASED,
                    unit_cost=price[material.pk], value=amount, sequence=sequence[pair],
                    created_by=admin, purchase=purchase,
                ))  # fmt: skip
    MaterialPurchase.objects.bulk_create(purchases)
    MaterialPurchaseLine.objects.bulk_create(lines, batch_size=5000)
    cut = ConsumptionCut.objects.create(
        org=org, project=project, number=1, period_start=FIRST_DAY, period_end=CUT_END,
        status="approved", created_by=admin, updated_by=admin, decided_by=world.supervisor,
        decided_at=now, attachment=blob,
    )  # fmt: skip
    issues: list[MaterialIssue] = []
    issue_lines: list[MaterialIssueLine] = []
    for n in range(ISSUES):
        warehouse = warehouses[n % WAREHOUSES]
        day = FIRST_DAY + timedelta(days=n * 449 // ISSUES)
        issue = MaterialIssue(
            org=org, project=project, number=n + 1, warehouse=warehouse, issued_on=day,
            scope="indirect", created_by=admin, updated_by=admin,
            cut=cut if day <= CUT_END else None,
        )  # fmt: skip
        issues.append(issue)
        for k in range(LINES_PER_ISSUE):
            material = materials[(n * LINES_PER_ISSUE + k) % ISSUED_MATERIALS]
            pair = (warehouse.pk, material.pk)
            unit = price[material.pk]
            issue_lines.append(MaterialIssueLine(
                org=org, issue=issue, order=k + 1, material=material, quantity=D(1),
                unit_cost=unit, amount=unit,
            ))  # fmt: skip
            sequence[pair] += 1
            quantity[pair] -= 1
            movements.append(StockMovement(
                org=org, project=project, warehouse=warehouse, material=material, moved_on=day,
                kind="issue", quantity=D(-1), unit_cost=unit, value=-unit,
                sequence=sequence[pair], created_by=admin, issue=issue,
            ))  # fmt: skip
    MaterialIssue.objects.bulk_create(issues, batch_size=5000)
    MaterialIssueLine.objects.bulk_create(issue_lines, batch_size=5000)
    StockMovement.objects.bulk_create(movements, batch_size=5000)
    StockBalance.objects.bulk_create(
        [
            StockBalance(org=org, project=project, warehouse_id=w, material_id=m,
                         quantity=quantity[(w, m)], value=quantity[(w, m)] * price[m],
                         last_sequence=sequence[(w, m)])
            for w, m in sequence
        ],
        batch_size=5000,
    )  # fmt: skip
    # Sin estadísticas (datos recién insertados en la transacción de la prueba) el planificador
    # puede elegir un nested loop; en producción las pone el autovacuum (como en 6b).
    with connection.cursor() as cursor:
        for table in ("material", "warehouse", "material_issue", "material_issue_line",
                      "stock_movement", "stock_balance", "material_return"):  # fmt: skip
            cursor.execute(f"ANALYZE {table}")
    return warehouses[0]


@skip_without_db
class MaterialsLoadTests(TestCase):
    world: FieldWorld
    warehouse: Warehouse

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga de materiales")
        grant_costs(cls.world, cls.world.supervisor, SectionRole.REVIEWER)
        cls.warehouse = load(cls.world)

    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def best_of(self, url: str, expected: str) -> float:
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        timings = []
        for _ in range(MEASUREMENTS):
            started = time.perf_counter()
            response = self.client.get(url)
            timings.append(time.perf_counter() - started)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)
        return min(timings)

    def test_inventory_kardex_and_cut_proposal_render_under_the_limit(self) -> None:
        project = self.world.project
        self.assertEqual(StockMovement.objects.filter(project=project).count(), 50_000)
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{project.pk}/materiales/"
        material = Material.objects.get(org=self.world.org, code="MAT-0000")
        for url, expected in (
            (base, "MAT-0000"),
            (f"{base}kardex/{self.warehouse.pk}/{material.pk}/", "SA-"),
            (f"{base}cortes/?hasta=2026-03-31", "Indirecto (proyecto)"),
        ):
            with self.subTest(url=url):
                best = self.best_of(url, expected)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")
```

Números: 5 bodegas × 2.000 materiales = 10.000 entradas; 8.000 salidas × 5 líneas = 40.000 salidas; 50.000 movimientos. Cada par de los 200 materiales usados tiene 1.600 salidas ÷ 200 × 5 = 40 salidas en su kárdex. Las salidas posteriores al 28/02/2026 (≈ 550, unas 2.750 líneas) quedan sin cortar y forman la propuesta.

- [ ] **Step 2: Correr**

Run: `infra/scripts/test_docker.sh suno/materials/tests/test_performance.py -s`
Expected: PASS. Anotar los tiempos (mejor de 3) para el registro de avance.

- [ ] **Step 3: Si pasa del límite**

Mirar con `CaptureQueriesContext` cuál consulta pesa. Lo esperable: en `queries.inventory_page`, el `Count`/`Sum` sobre 10.000 saldos con unión a `material` solo si hay filtro de texto o categoría (sin filtro no une); en `cuts.build_proposal`, cambiar `issue_id__in=issue_ids` por el filtro con unión (`issue__project=…, issue__cut__isnull=True, issue__issued_on__range=(start, end)`) como dice la nota de la Task 10. No bajar el número de movimientos de la prueba.

- [ ] **Step 4: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/materials && ruff check suno/materials"
git add server/suno/materials/tests/test_performance.py
git commit -m "test(materials): load test with 2000 materials, 5 warehouses and 50000 movements

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Materiales en los datos de ejemplo (`seed_control_demo`)

**Files:**
- Modify: `server/suno/management/commands/seed_control_demo.py`
- Test: `server/suno/control/tests/test_seed_demo.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/suno/control/tests/test_seed_demo.py`, agregar a los imports de modelos `ConsumptionCut`, `InventoryAdjustment`, `Material`, `MaterialIssue`, `MaterialPurchase`, `MaterialReturn`, `MaterialTransfer`, `StockBalance`, `StockMovement`, `Warehouse` y `from django.db.models import Sum`; y en `SeedControlDemoTests`:

```python
    def test_seeds_materials_with_a_cut_in_the_ledger(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        self.assertEqual(
            set(Material.objects.filter(org=self.org).values_list("code", flat=True)),
            {"CEM-01", "ARE-01", "ACE-12", "TUB-06"},
        )
        self.assertEqual(Warehouse.objects.filter(project=project).count(), 2)
        purchases = MaterialPurchase.objects.filter(project=project)
        self.assertEqual(set(purchases.values_list("kind", "status")),
                         {("warehouse", "approved"), ("direct", "approved"),
                          ("warehouse", "draft")})  # fmt: skip
        self.assertTrue(MaterialReturn.objects.filter(project=project).exists())
        self.assertTrue(MaterialTransfer.objects.filter(project=project).exists())
        self.assertTrue(InventoryAdjustment.objects.filter(project=project, status="approved")
                        .exists())  # fmt: skip
        cut = ConsumptionCut.objects.get(project=project)
        self.assertEqual(cut.status, "approved")
        self.assertTrue(CostEntry.objects.filter(consumption_cut=cut).exists())
        self.assertTrue(MaterialIssue.objects.filter(project=project, cut__isnull=True).exists())
        for row in StockBalance.objects.filter(project=project):
            sums = StockMovement.objects.filter(
                warehouse=row.warehouse, material=row.material
            ).aggregate(q=Sum("quantity"), v=Sum("value"))
            self.assertEqual((row.quantity, row.value), (sums["q"], sums["v"]))
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: FAIL (no hay materiales).

- [ ] **Step 3: Implementar**

En `server/suno/management/commands/seed_control_demo.py`:

1. Docstring, al final del párrafo de la pieza 6b: "Desde la pieza 6c también siembra materiales: catálogo, dos bodegas, compras a bodega y directa, salidas, una devolución, un traslado, un ajuste y un corte aprobados, una salida sin cortar y una compra en borrador."
2. Imports:

```python
from suno.core.models import CostScope, Material, PurchaseKind
from suno.materials import adjustments as material_adjustments
from suno.materials import cuts as material_cuts
from suno.materials import movements as material_movements
from suno.materials import purchases as material_purchases
from suno.materials.adjustments import AdjustmentData, AdjustmentLineData
from suno.materials.destination import INDIRECT, DestinationData
from suno.materials.movements import (
    IssueData,
    IssueLineData,
    ReturnData,
    ReturnLineData,
    TransferData,
)
from suno.materials.purchases import PurchaseData, PurchaseLineData
from suno.materials.warehouses import create_warehouse
```

(`CostScope` ya se importa; agregar solo lo que falte. `MaterialIssueLine` y `StockBalance` también, para leer la línea de la salida y el saldo esperado.)

3. Constante del catálogo, después de `COSTS`:

```python
#: (código, nombre, unidad, categoría) del catálogo de ejemplo (de la organización).
MATERIALS = [
    ("CEM-01", "Cemento gris 50 kg", "und", "Cementos"),
    ("ARE-01", "Arena de río", "m3", "Agregados"),
    ("ACE-12", "Varilla de 1/2 pulgada", "kg", "Aceros"),
    ("TUB-06", "Tubería PVC de 6 pulgadas", "ml", "Redes"),
]
```

4. En `run`, después de `self._contracts(...)`: `self._materials(project, start, items, tasks)`.
5. El método:

```python
    def _materials(
        self,
        project: Project,
        start: date,
        items: dict[str, BudgetItem],
        tasks: dict[str, Task],
    ) -> None:
        """Materiales por los servicios: el residente (Revisor de Costos desde `_contracts`)
        registra y el administrador aprueba. El catálogo es de la organización: se reutiliza
        si ya existe."""
        office = resolve_request_context(Principal(user_id=self._resident().pk, org_id=self.org.pk))
        admin = self.ctx
        stock = {
            code: Material.objects.get_or_create(
                org=self.org, code=code, defaults={"name": name, "unit": unit, "category": cat}
            )[0]
            for code, name, unit, cat in MATERIALS
        }
        main = create_warehouse(office, project, "Almacén principal")
        north = create_warehouse(office, project, "Frente norte")

        def day(offset: int) -> date:
            return start + timedelta(days=offset)

        def line(code: str, amount: str, price: str, dest: Any = None) -> PurchaseLineData:
            return PurchaseLineData(stock[code].pk, Decimal(amount), Decimal(price), dest)

        def purchase(kind: Any, warehouse: Any, number: str, offset: int, *lines: Any,
                     approve: bool = True) -> Any:  # fmt: skip
            data = PurchaseData(str(kind), warehouse.pk if warehouse else None,
                                "Ferretería El Constructor", "900123456", "invoice", number,
                                day(offset) if offset >= 0 else self.today + timedelta(offset),
                                tuple(lines))  # fmt: skip
            created = material_purchases.create_purchase(
                office, project, data, upload=self._upload(), name="factura-ejemplo.pdf"
            )
            if approve:
                material_purchases.approve_purchase(admin, project, created.pk)
            return created

        def to_item(code: str, task: str) -> DestinationData:
            return DestinationData(str(CostScope.ITEM), (items[code].pk,), task_id=tasks[task].pk)

        def issue(warehouse: Any, when: date, dest: DestinationData, code: str, amount: str) -> Any:
            return material_movements.create_issue(office, project, IssueData(
                warehouse.pk, when, dest, (IssueLineData(stock[code].pk, Decimal(amount)),),
                received_by="Maestro de obra",
            ))  # fmt: skip

        W, DIRECT = PurchaseKind.WAREHOUSE, PurchaseKind.DIRECT
        purchase(W, main, "FE-2001", 95, line("CEM-01", "400", "32000"),
                 line("ACE-12", "12000", "4100"))  # fmt: skip
        purchase(W, main, "FE-2002", 120, line("CEM-01", "300", "33500"))
        purchase(DIRECT, None, "FA-0077", 100,
                 line("ARE-01", "30", "95000", to_item("3.1", "Zapatas eje A")))  # fmt: skip
        footing = issue(main, day(100), to_item("3.1", "Zapatas eje A"), "CEM-01", "120")
        issue(main, day(105), to_item("3.2", "Zapatas eje A"), "ACE-12", "4000")
        issue(main, day(130), to_item("3.1", "Zapatas eje B"), "CEM-01", "150")
        returned = MaterialIssueLine.objects.get(issue=footing)
        material_movements.create_return(office, project, footing.pk, ReturnData(
            day(110), (ReturnLineData(returned.pk, Decimal("10")),)))  # fmt: skip
        material_movements.create_transfer(office, project, TransferData(
            main.pk, north.pk, day(112), (IssueLineData(stock["CEM-01"].pk, Decimal("40")),)))  # fmt: skip
        expected = StockBalance.objects.get(warehouse=main, material=stock["CEM-01"]).quantity
        adjustment = material_adjustments.create_adjustment(office, project, AdjustmentData(
            main.pk, day(140), INDIRECT,
            (AdjustmentLineData(stock["CEM-01"].pk, expected - Decimal("2")),)))  # fmt: skip
        material_adjustments.attach_count(office, project, adjustment.pk, self._upload(),
                                          "acta-de-conteo.pdf")  # fmt: skip
        material_adjustments.approve_adjustment(admin, project, adjustment.pk)
        cut = material_cuts.create_cut(office, project, day(140))
        material_cuts.attach_signed_report(office, project, cut.pk, self._upload(),
                                           "reporte-firmado.pdf")  # fmt: skip
        material_cuts.approve_cut(admin, project, cut.pk)
        # Después del corte: una salida sin cortar vieja (aviso) y una compra por aprobar.
        issue(north, self.today - timedelta(days=35), INDIRECT, "CEM-01", "5")
        purchase(W, main, "FE-2003", -2, line("ACE-12", "2000", "4200"), approve=False)
```

`day(140)` cae hacia 42 días antes de hoy (el proyecto empieza unos 182 días atrás), así que la salida de hace 35 días queda después del corte y con más de 31 días sin cortar. Ninguna fecha es futura. Si `ruff` marca líneas largas en este método, partirlas (o dejar el `# fmt: skip` en los llamados de varios argumentos, como en `_contracts`).

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: PASS (las pruebas de 6a y 6b del sembrado siguen verdes).

- [ ] **Step 5: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/management && ruff check suno/management && mypy suno/management --config-file pyproject.toml"
git add server/suno/management/commands/seed_control_demo.py server/suno/control/tests/test_seed_demo.py
git commit -m "feat(demo): seed materials, stock documents and an approved consumption cut

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Verificación completa, revisión visual, registro de avance (push y PR los hace el controlador)

**Files:**
- Modify: `plans/suno-progreso.md`, `plans/2026-11-15-costos-pieza6c-materiales-design.md` (las decisiones del plan al final)

- [ ] **Step 1: Suite completa y compuertas**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --cmd "pip install -q bandit && bandit -q -r suno config -x '*/tests/*,*/migrations/*'"
infra/scripts/test_docker.sh --cmd "set -o pipefail; coverage run -m pytest -q -p no:cacheprovider && coverage report | tail -1"
```

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y `healthcheck.py` en el formato; `makemigrations --check` sin cambios (la `0030` lo cubre todo); bandit sin hallazgos (nada de `assert` ni `xml.etree` en producción); cobertura ≥ la de `master` (93 %), con `suno/materials` cerca del 100 %. La suite completa tarda cerca de una hora: correrla en segundo plano.

- [ ] **Step 2: Migración hacia atrás**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py migrate core 0029 -v 0 && python manage.py migrate -v 0"
```

Expected: sin errores (la reversa quita las tablas de materiales, las tres columnas de `cost_entry` y vuelve a poner la restricción de signo anterior; la ida las crea otra vez). Con datos sembrados de materiales la reversa de la restricción de signo fallaría si hay movimientos negativos de corte: correrla sobre una base sin sembrar, como aquí.

- [ ] **Step 3: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-materiales -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0. (La `0030` depende de la `0029` y no nombra la migración del renombre: no hace falta lista blanca en `check_naming.py`.)

- [ ] **Step 4: Revisión visual a 1440 y 390 px**

Levantar la app contra el Postgres de pruebas (sigue vivo hasta `--down`), sembrar y abrir:

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py bootstrap_demo --org-name 'SUNO Demo' --username agente-demo --password 'Revision-visual-2026' && python manage.py shell -c \"from suno.core.models import AppUser, Membership, Organization, Role; org = Organization.objects.get(name='SUNO Demo'); user = AppUser.objects.create_user(username='admin-demo', email='admin-demo@example.com', password='Revision-visual-2026', org=org); Membership.objects.create(org=org, user=user, role=Role.ADMIN)\" && python manage.py seed_control_demo"
MSYS_NO_PATHCONV=1 docker run --rm -d --name suno-materials-web --network suno-test -p 8000:8000 \
  -v "$(cd server && pwd -W):/app" -v suno-test-venv:/venv -w /app \
  -e POSTGRES_HOST=suno-test-db -e POSTGRES_PASSWORD=test -e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
  python:3.12-slim sh -c "python manage.py runserver 0.0.0.0:8000"
```

Entrar en `http://localhost:8000/plataforma/ingresar/` como `admin-demo` (para entrar también como el residente, darle contraseña con `docker exec suno-materials-web python manage.py shell -c "…set_password…"`) y recorrer, con capturas de Edge headless (o Playwright) a 1440 px y a 390 px (a 390 px dentro de un iframe, porque el mínimo de Edge es 496 px):

- Catálogo (barra lateral "Materiales"): lista con filtros, formulario, importación con vista previa.
- Pestaña Materiales: inventario con filtros y paginación, kárdex con filtro de fechas, bodegas (renombrar, desactivar), compras (lista, formulario con "Agregar líneas" y "Recalcular", ficha con factura, aprobar, anular), salidas (formulario rápido, lista, ficha con devolución, editar), traslados, ajustes ("Traer el inventario", "Recalcular", ficha con acta), cortes (propuesta, ficha, PDF —abrirlo: tablas dentro de A4 horizontal, firmas al final—, firmado, aprobación) y los Excel; los avisos (salida sin cortar de hace 35 días, compra en borrador).
- Costos: "Por aprobar" con la sección de materiales (como `admin-demo`: FE-2003), libro con orígenes "Compras", "Cortes" y "Ajustes" y la etiqueta enlazada, ficha de un movimiento del corte (sin "Reversar").
- Control: pestaña "Por ítem" con "Valor en bodega del proyecto".
- Textos largos: alargar con `python manage.py shell -c` el nombre de un material, de una bodega y el proveedor a 120 caracteres, y un valor a 15 cifras.

Revisar: tablas de líneas dentro de su panel con desplazamiento horizontal; encabezados de formulario en una columna a 390; botones de decisión y motivo sin desbordes; el detalle del corte legible. Con el adaptador local, los enlaces de soporte son `local://…` (no abren en el navegador): es lo esperado en desarrollo. Corregir antes de cerrar (CSS en `projects.css`, subiendo `?v=` en `base.html`).

Al terminar: `docker rm -f suno-materials-web` y `infra/scripts/test_docker.sh --down`.

- [ ] **Step 5: Decisiones en la spec y registro de avance**

1. Al final de `plans/2026-11-15-costos-pieza6c-materiales-design.md`, una sección "Decisiones tomadas al planear" con el resumen de la sección del mismo nombre de este plan (las ⚑ marcadas).
2. En `plans/suno-progreso.md`, sección nueva al principio de la parte de costos: "Módulo de costos — Pieza 6c (materiales: compras, bodegas y consumos), servidor — <fecha>", con: PR y rama (la PR la abre el controlador: dejar el número para que lo complete); qué se entregó (modelos y migración `0030`, promedio móvil y kárdex con bloqueo, catálogo con importación, bodegas, compras directas y a bodega, salidas, devoluciones, traslados, ajustes, cortes con PDF y firma, libro con orígenes, bandeja, control con valor en bodega, Excel, `seed_control_demo`); las decisiones del plan (con las ⚑); los números (pruebas, cobertura, consultas fijas, tiempos de la prueba de carga); lo que se corrigió en la revisión visual; y lo que sigue (captura móvil de salidas sin conexión, 6d tareo y nómina). Actualizar "Última actualización" del encabezado.

- [ ] **Step 6: Commit (sin push)**

```bash
git add plans/suno-progreso.md plans/2026-11-15-costos-pieza6c-materiales-design.md
git commit -m "docs(plan): progress log and planning decisions for cost piece 6c

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

El push de `feat/costos-materiales` y la PR (título sugerido: "feat(materials): materials, warehouses and consumption cuts posted to the cost ledger"; cuerpo con resumen, qué entra, decisiones ⚑, permisos, pruebas, cobertura, tiempos de carga, capturas y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)") **los hace el controlador**. No integrar la PR: el merge es del usuario.

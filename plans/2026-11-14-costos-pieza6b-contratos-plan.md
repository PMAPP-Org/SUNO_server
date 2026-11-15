# Costos — Pieza 6b (subcontratos y destajos): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capturar en SUNO los subcontratos y destajos de cada proyecto (contrato con líneas, cargos y descuentos; otrosíes; actas de avance por cantidades propuestas desde lo validado en campo; anticipo y retención de garantía; liquidación y anulación), y que cada acta aprobada escriba sus movimientos en el libro de costos de la pieza 6a.

**Architecture:** Nueve modelos nuevos en `core/models/contract.py` y la FK `CostEntry.certificate`, en una migración aditiva `0029_contracts`. Un paquete nuevo `server/suno/contracts/` lleva el cálculo puro (`calc.py`, sobre `budgets.totals`), la cantidad propuesta desde campo (`proposal.py`, sobre `matrix.quantities.executed_by_pair`), los servicios con auditoría (`services.py` para contratos y otrosíes, `certificates.py` para actas y el paso al libro), las consultas de la pestaña (`queries.py`), el PDF del acta (`pdf.py`, reportlab), el Excel (`export.py`) y la pestaña "Contratos" (`views.py`, `forms.py`, `urls.py`). Los permisos son los de la sección Costos (6a), sin políticas nuevas. El libro (6a) gana el origen "Actas" y el control (5 y 6a) gana el "comprometido en contratos" en el desglose por ítem, sin cambios de cálculo.

**Tech Stack:** Django 5.2, PostgreSQL 16, plantillas de Django con CSS propio (sin JavaScript nuevo), reportlab, `unittest`/`django.test` corridos en Docker.

**Spec:** `plans/2026-11-14-costos-pieza6b-contratos-design.md` (aprobada; sus "Decisiones tomadas al planear" ya están al final). Plan de referencia de formato y código base: `plans/2026-11-12-costos-pieza6a-costo-real-plan.md`.

**Rama y worktree:** `feat/costos-contratos` en `C:\Users\gabri\APP\wt-server-contratos` (base `master` `38a31e7`, spec en `05d97b7`). Todos los comandos se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Ya están anotadas al final de la spec (las marcadas con ⚑ conviene que el usuario las mire).

1. **Una migración aditiva** (`0029_contracts`): tablas `contract`, `contract_line`, `contract_line_task`, `contract_charge`, `contract_deduction`, `contract_amendment`, `contract_amendment_line`, `contract_certificate`, `certificate_line`; columna `cost_entry.certificate_id` (nula); y la opción "Acta de avance" (`certificate`) en `CostDocumentType` (solo cambia `choices`). Sin `RunPython`.
2. **Sin políticas nuevas.** Todo se decide con las de Costos de la 6a: `can_read_costs`, `can_create_costs`, `can_approve_costs`, `can_reject_costs`, `can_reverse_costs`, `can_export_costs` (por proyecto) y `can_update_cost`/`can_delete_cost`, que son estructurales (`org_id`, `project_id`, `created_by_id`): `Contract`, `ContractAmendment` y `ContractCertificate` los tienen (otrosí y acta llevan `project` repetido a propósito, también para la bandeja y los índices). Crear la **acta de liquidación** pide `reverse` (spec: "liquidar" es `reverse`); aprobarla, `approve` como cualquier acta.
3. ⚑ **Rechazo de un contrato:** la spec da cuatro estados al contrato (borrador, aprobado, liquidado, anulado) y "rechazado por y motivo" en su autoría. Rechazar un contrato lo deja en **borrador con el motivo a la vista** (`rejected_by`, `rejected_at`, `rejection_note`); al editarlo se limpia (la auditoría lo conserva). Otrosíes y actas sí tienen estado "rechazado" y, como en la 6a, solo su autor los corrige; al guardarlos vuelven a borrador.
4. **Borradores que se borran** (capacidad `delete`; el Colaborador, solo los suyos): un contrato en borrador, un otrosí abierto y un acta abierta. Quedan en la auditoría (`contract.deleted`, `amendment.deleted`, `certificate.deleted`). **Anular un contrato** (`reverse`) solo se puede si está aprobado, sin actas aprobadas y sin otrosíes ni actas abiertos.
5. **Lo vigente se guarda:** `ContractLine.current_quantity` (original + otrosíes aprobados) y `Contract.current_value` (directo vigente + cargos), recalculados por el servicio al guardar el borrador y al aprobar un otrosí. Una línea agregada por un otrosí es una `ContractLine` con `amendment`; solo cuenta ("línea vigente") si ese otrosí está aprobado.
6. ⚑ **Otrosí:** aumentos **positivos** de cantidad, líneas nuevas y nueva fecha de fin (lo que dice la spec); ni precios ni cargos cambian. Un otrosí abierto a la vez por contrato; número consecutivo por contrato.
7. ⚑ **Anticipo:** su valor se calcula **al aprobar el contrato** (valor vigente × %) y queda fijo; un otrosí no lo cambia. Cada acta amortiza `bruto × anticipo ÷ valor vigente` (el vigente al aprobar el acta), sin pasar del saldo.
8. ⚑ **Bases de los descuentos:** `direct` (bruto directo del acta), `gross` (bruto) y `line:<n>`, que en un descuento es **la línea `n` de los cargos** del acta (así ReteIVA = 15 % del IVA). `subtotal` no se ofrece en los descuentos (sería igual al bruto) y se rechaza.
9. ⚑ **Plantillas:** "AIU con IVA sobre utilidad" (A, I y U en 0 % para llenar, IVA sobre utilidad 19 %), "IVA pleno" (19 % sobre el directo), "Sin cargos"; "Retenciones típicas" (todas en 0 %): Retención en la fuente y ReteICA sobre el directo, ReteIVA sobre la primera línea de cargos cuyo nombre empiece por "IVA" (o sobre el bruto si no hay) y Estampillas sobre el bruto.
10. ⚑ **Cantidad propuesta:** lo validado (revisión vigente aprobada) para el ítem y las tareas de la línea con fecha de captura en Colombia **hasta el fin del periodo**, menos lo pagado en actas aprobadas de esa línea, mínimo 0. Así lo capturado antes y validado tarde entra en la siguiente acta y nada se paga dos veces. Se muestra además lo capturado dentro del periodo (informativo). La propuesta no se recorta al saldo: la **aprobación** exige el tope y el formulario avisa que hace falta un otrosí.
11. ⚑ **Una sola acta abierta** (borrador o rechazada) por contrato, con índice parcial único. El número se asigna al crearla (máximo + 1), así el PDF para firmar ya lo lleva; borrar el borrador libera su número. Cada acta lleva una línea por cada línea vigente del contrato.
12. **Periodo:** desde ≤ hasta ≤ hoy, desde el 1 de enero de 2000; sin cruce con las actas no anuladas del contrato. Se valida en el servicio con el contrato bloqueado (`select_for_update`): una restricción de exclusión necesitaría la extensión `btree_gist`, que el proyecto no usa. Por defecto: desde el día siguiente al fin de la última acta no anulada (o el inicio del contrato) hasta hoy.
13. ⚑ **Acta firmada:** PDF, JPG, PNG o WebP hasta 25 MB (`attachments.web.store_upload`, como el soporte de la 6a). Subirla **no** es editar el acta (no cambia `updated_by`, para que quien la escanea pueda aprobar si no la preparó) y queda en la auditoría como `certificate.updated` con `signed: true`. Editar el borrador **quita** el firmado (ya no corresponde a lo impreso).
14. ⚑ **Liquidación:** acta con `is_settlement`; amortiza todo el saldo del anticipo, **no retiene** y devuelve todo lo retenido; el neto puede quedar negativo (lo debe el contratista). Al aprobarla el contrato pasa a liquidado (`contract.settled`). Si se anula (debe ser la última aprobada) el contrato vuelve a aprobado.
15. **Paso al libro:** un `CostEntry` aprobado por línea con valor > 0: fecha de causación = fin del periodo; tipo Subcontrato (o Mano de obra si es destajo); asignación `item` con su capítulo de origen en la versión vigente (como la 6a) y la tarea si la línea cubre una sola; valor = directo de la línea + su parte de los cargos, el ajuste de redondeo a la línea de mayor valor (empate: la de menor orden); descripción "Acta 3 · Contrato SC-012 · 2.1 Concreto zapatas"; tercero = contratista; documento "Acta de avance" número "SC-012 · Acta 3"; registró y modificó = quien preparó y editó el acta; aprobó = quien aprobó; soporte = el acta firmada. Anular el acta crea los reversos (fecha de hoy, motivo, mismo `certificate`). `costs.services.reverse` rechaza un movimiento con acta.
16. ⚑ **Formularios sin JavaScript** (como la 6a): líneas, cargos y descuentos son filas de un formset con "Agregar filas"; el ítem se elige con un buscador `datalist`; las tareas de una línea se escriben como números de esquema separados por comas (vacío = todas); el "cálculo en vivo" de la spec es un botón **"Recalcular"** que recalcula en el servidor sin guardar; el periodo del acta se elige con un formulario GET antes de las cantidades.
17. **Traslape:** dos líneas cubren lo mismo si tienen el mismo ítem y sus tareas se cruzan (vacío = todas). Se compara con las líneas vigentes de los contratos en borrador o aprobados del proyecto (y dentro del mismo contrato). Al guardar se listan y se pide marcar "Confirmo el traslape".
18. **Comprometido en contratos** (Control, pestaña "Por ítem", solo con permiso de costos): contratos aprobados, cantidad vigente × precio; liquidados, lo pagado × precio. Es directo (sin cargos), comparable con el BAC del ítem; aviso cuando lo supera.
19. **Libro (6a):** el filtro de origen queda Todos, Manual (`libro`), Actas (`actas`) y Solo automáticos. Las líneas de acta dicen "Acta 3 · Contrato SC-012" y enlazan al acta. Un costo **manual** de tipo Subcontrato o Mano de obra en un ítem con contrato aprobado muestra un aviso al guardarlo y en su ficha.
20. **Avisos de la pestaña:** actas en borrador creadas hace más de 15 días; validado sin pagar por línea (contratos aprobados, hasta hoy); contratos aprobados vencidos (fin < hoy) sin liquidar; anticipo sin amortizar con lo ejecutado ≥ 100 % del vigente; líneas que se traslapan.
21. **Rendimiento:** la lista son 2 consultas (contratos y agregados de las actas aprobadas); la ficha, un número fijo (ver Task 7). Prueba de carga: 200 contratos con 10 líneas (2000), 25 actas aprobadas por contrato (5000) con sus 50 000 líneas; lista y ficha en menos de 1 s (2 s con `CI=true`).
22. **Historial de la ficha:** los eventos de auditoría con `target` del contrato o con `metadata.contract` igual a su id (todos los de otrosíes y actas lo llevan).
23. **Excel:** `contratos.xlsx` (la lista con saldos) y `contrato-<número>.xlsx` (hojas Contrato, Líneas, Actas y Otrosíes); `safe_cell` en todo texto; `contract.exported`.
24. **Destajo:** el mismo modelo; arranca sin cargos, sin anticipo y sin garantía; número sugerido `DT-001` (subcontrato `SC-001`), único por proyecto sin distinguir mayúsculas.
25. **Límites:** hasta 200 líneas, 20 cargos y 20 descuentos por contrato; cantidades con 4 decimales y mayores que cero en el contrato (mayores o iguales a cero en el acta); precios a centavos y mayores que cero; anticipo y garantía entre 0 y 100 %.

**Limitación conocida (revisión de código, M-5):** el PDF de un acta vieja que se vuelve a descargar muestra en «contratado» la cantidad vigente de **hoy** (`ContractLine.current_quantity`), no la que había cuando se firmó: si después se aprobó un otrosí, el contratado y el saldo de ese PDF no coinciden con la copia firmada. Lo que vale es la copia firmada que quedó adjunta; las cantidades del acta, sus valores y lo «anterior» sí son los de entonces. Se deja así a propósito (no se guarda una foto del contratado por acta).

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `tearDownModule = teardown_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; `blob_registry()` para `Blob`; las pruebas y los comandos de `ALLOWED` sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`, con un código nuevo en `AuditAction`. Nada de `assert` en producción (bandit B101) ni `import xml.etree`. Ruff a 100 columnas (`E, F, I, UP, B, C4, DJ`; B008: sin llamadas en valores por defecto).
- `core/policies.py` es el único lugar donde se decide quién puede qué. Esta pieza no agrega políticas: reutiliza las de Costos (decisión 2).
- Comentarios y textos de interfaz en español; identificadores en inglés. Commits en inglés, estilo conventional, terminados en una línea en blanco y `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La guardia de nombres (`infra/scripts/check_naming.py concept`) rechaza "visit"/"visita" en código y plantillas.
- Si cambia `projects.css`, se sube su `?v=` en `server/suno/core/templates/suno/projects/base.html` (hoy `20261113-costs-v5`).
- Todo texto que escribe un usuario y va a un Excel pasa por `safe_cell`.
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura. No hay Python local: todo corre en Docker.

## Cómo correr las pruebas

```bash
# Desde C:/Users/gabri/APP/wt-server-contratos, con Docker Desktop arrancado (el contenedor trabaja en server/)
infra/scripts/test_docker.sh suno/contracts/tests/test_calc.py        # un archivo
infra/scripts/test_docker.sh suno/contracts suno/costs suno/control    # paquetes
infra/scripts/test_docker.sh                                           # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                    # al terminar

# Guardia de nombres (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-contratos -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Fallas conocidas, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y `healthcheck.py` en `ruff format --check` (comprobarlo en la Task 0 y anotarlo).

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/suno/core/models/contract.py` | `Contract`, `ContractLine`, `ContractLineTask`, `ContractCharge`, `ContractDeduction`, `ContractAmendment`, `ContractAmendmentLine`, `ContractCertificate`, `CertificateLine` y sus enumeraciones |
| `server/suno/core/models/cost.py` | `CostEntry.certificate`, `CostDocumentType.CERTIFICATE` |
| `server/suno/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/suno/core/migrations/0029_contracts.py` | Tablas nuevas y la columna del libro (generada) |
| `server/suno/core/audit.py` | `CONTRACT_*`, `AMENDMENT_*`, `CERTIFICATE_*` |
| `server/suno/matrix/quantities.py` | `executed_by_pair(..., captured_from=, captured_to=)` |
| `server/suno/contracts/__init__.py` | Paquete nuevo (sin modelos; plantillas en `core/templates/suno/contracts/`) |
| `server/suno/contracts/calc.py` | Valor del contrato, totales del acta, descuentos, reparto al libro, plantillas (puro) |
| `server/suno/contracts/proposal.py` | Validado por línea, pagado por línea, propuesta del acta |
| `server/suno/contracts/access.py` | `get_contracts_project`, `get_contract`, `get_amendment`, `get_certificate` |
| `server/suno/contracts/services.py` | Contratos y otrosíes: validar, crear, actualizar, borrar, aprobar, rechazar, anular; traslapes |
| `server/suno/contracts/certificates.py` | Actas: borrador, firmado, aprobar (paso al libro), rechazar, anular, liquidar |
| `server/suno/contracts/queries.py` | Lista con saldos, ficha, avisos de la pestaña, comprometido por ítem, contratos de un ítem, bandeja |
| `server/suno/contracts/pdf.py` | Documento y PDF del acta para firmar |
| `server/suno/contracts/export.py` | `contracts_workbook`, `contract_workbook` con `safe_cell` |
| `server/suno/contracts/forms.py` | Formularios y formsets de contrato, otrosí y acta; conversión a datos del servicio |
| `server/suno/contracts/views.py`, `urls.py` | Pestaña "Contratos" |
| `server/config/urls.py` | Monta `plataforma/proyectos/<uuid:project_id>/contratos/` |
| `server/suno/core/templates/suno/contracts/*.html` | Lista, formulario, ficha, otrosí, acta (formulario y ficha), anulación, `_nav.html`, `_alerts.html` |
| `server/suno/core/templates/suno/projects/_header.html` | Pestaña "Contratos" en la cabecera |
| `server/suno/costs/services.py` | `reverse` rechaza movimientos de acta |
| `server/suno/costs/ledger.py`, `export.py`, `views.py` | Origen "Actas", etiqueta y enlace del acta, aviso del costo manual, sección de contratos en "Por aprobar" |
| `server/suno/core/templates/suno/costs/{list,detail,inbox}.html` | Lo mismo en las plantillas |
| `server/suno/control/views.py`, `core/templates/suno/control/dashboard.html` | "Comprometido en contratos" y su aviso |
| `server/suno/core/static/suno/css/projects.css`, `projects/base.html` | Estilos `ctr-*`; versión del CSS |
| `server/suno/management/commands/seed_control_demo.py` | Contratos de ejemplo |
| `server/suno/core/templatetags/money_tags.py` | Filtro `qty` (cantidades sin ceros de sobra) |
| Pruebas | `core/tests/test_policies.py`, `contracts/tests/{__init__,factories,test_models,test_calc,test_proposal,test_services,test_certificates,test_ledger_posting,test_queries,test_views,test_views_certificates,test_documents,test_performance}.py`, `costs/tests/{test_services,test_ledger,test_views}.py`, `control/tests/{test_views,test_seed_demo,test_money_tags}.py` |
| `plans/suno-progreso.md` | Registro de avance (Task 14) |

---

### Task 0: Línea base

**Files:** ninguno.

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-contratos status --short --branch`
Expected: `## feat/costos-contratos` sin cambios pendientes; los dos últimos commits son el de este plan y el de la spec (`05d97b7`).

- [ ] **Step 2: Línea base**

```bash
infra/scripts/test_docker.sh 2>&1 | tail -5
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh suno/control/tests/test_performance.py -s 2>&1 | tail -5
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`; `ruff format --check` solo marca `healthcheck.py`; `No changes detected`; las dos pruebas de carga del control pasan. Anotar el número de pruebas y el tiempo de las pruebas de carga (para el registro de avance). No hay commit en esta tarea.

---

### Task 1: Modelos de contratos, migración y datos de prueba

**Files:**
- Create: `server/suno/core/models/contract.py`
- Modify: `server/suno/core/models/cost.py` (`CostDocumentType.CERTIFICATE`, `CostEntry.certificate`)
- Modify: `server/suno/core/models/__init__.py`
- Create (generada): `server/suno/core/migrations/0029_contracts.py`
- Create: `server/suno/contracts/__init__.py`, `server/suno/contracts/tests/__init__.py` (vacíos), `server/suno/contracts/tests/factories.py`
- Test: `server/suno/contracts/tests/test_models.py`, `server/suno/core/tests/test_policies.py`

- [ ] **Step 1: Prueba de las políticas sobre un contrato**

Las políticas de la 6a son estructurales; esta prueba fija que valen para un contrato (decisión 2). En `server/suno/core/tests/test_policies.py`, agregar `Contract` al import de `suno.core.models` y, dentro de `CostPolicyTests`, después de `test_contributor_updates_and_deletes_only_its_own`:

```python
    def test_contracts_use_the_cost_policies(self) -> None:
        """Costos, pieza 6b: contratos, otrosíes y actas se editan con `can_update_cost`."""
        own = Contract(org_id=ORG, project_id=uuid.uuid4(), created_by_id=USER)
        other = Contract(org_id=ORG, project_id=uuid.uuid4(), created_by_id=OTHER_USER)
        contributor = self.actor(SectionRole.CONTRIBUTOR)
        self.assertTrue(policies.can_update_cost(contributor, own))
        self.assertFalse(policies.can_update_cost(contributor, other))
        self.assertFalse(policies.can_delete_cost(contributor, other))
        self.assertTrue(policies.can_update_cost(self.actor(SectionRole.REVIEWER), other))
        self.assertFalse(policies.can_update_cost(self.actor(SectionRole.VIEWER), own))
```

- [ ] **Step 2: Paquete y datos de prueba**

Crear vacíos `server/suno/contracts/__init__.py` y `server/suno/contracts/tests/__init__.py` (como `suno/costs/`: sin modelos ni `apps.py`).

`server/suno/contracts/tests/factories.py` (lo usan todas las tareas siguientes):

```python
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
    kind: str = ContractKind.SUBCONTRACT,
    status: str = ContractStatus.APPROVED,
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
        closed_by=world.admin if status in (ContractStatus.SETTLED, ContractStatus.VOIDED) else None,
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
    status: str = CertificateStatus.APPROVED,
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
```

- [ ] **Step 3: Pruebas del modelo**

`server/suno/contracts/tests/test_models.py`:

```python
"""Restricciones de la base de contratos, otrosíes y actas (spec, 1)."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractAmendment,
    ContractCertificate,
    ContractStatus,
    CostDocumentType,
    CostEntry,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class ContractConstraintTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world

    def broken(self, **fields: object) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_contract(self.cw, **fields)  # type: ignore[arg-type]

    def test_number_is_unique_per_project_ignoring_case(self) -> None:
        make_contract(self.cw, number="SC-001")
        self.broken(number="sc-001")
        self.assertEqual(make_contract(self.cw, number="SC-002").number, "SC-002")

    def test_dates_and_percentages(self) -> None:
        self.broken(number="X-1", starts_on=date(2026, 5, 1), ends_on=date(2026, 4, 30))
        self.broken(number="X-2", advance_pct="100.0001")
        self.broken(number="X-3", retention_pct="-1")

    def test_an_approved_contract_was_approved_by_someone_else(self) -> None:
        draft = make_contract(self.cw, status=ContractStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(
                status=ContractStatus.APPROVED,
                approved_by=self.world.admin,
                approved_at=timezone.now(),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(status=ContractStatus.APPROVED)

    def test_a_voided_contract_has_its_reason_and_closing(self) -> None:
        contract = make_contract(self.cw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=contract.pk).update(status=ContractStatus.VOIDED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=contract.pk).update(
                status=ContractStatus.VOIDED,
                closed_by=self.world.admin,
                closed_at=timezone.now(),
                close_note="",
            )

    def test_a_rejection_keeps_the_draft_with_its_reason(self) -> None:
        draft = make_contract(self.cw, status=ContractStatus.DRAFT)
        Contract.objects.filter(pk=draft.pk).update(
            rejected_by=self.world.supervisor, rejected_at=timezone.now(), rejection_note="Falta"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contract.objects.filter(pk=draft.pk).update(rejection_note="")


@skip_without_db
class CertificateConstraintTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world
        self.contract = make_contract(self.cw)

    def test_number_is_unique_and_only_one_is_open(self) -> None:
        make_certificate(self.cw, self.contract, number=1, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, number=2, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, number=1, start=date(2026, 4, 1),
                             end=date(2026, 4, 30))  # fmt: skip
        make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30)
        )

    def test_period_order(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_certificate(self.cw, self.contract, start=date(2026, 4, 2), end=date(2026, 4, 1))

    def test_approval_needs_the_signed_copy_and_someone_else(self) -> None:
        draft = make_certificate(self.cw, self.contract, status=CertificateStatus.DRAFT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=draft.pk).update(
                status=CertificateStatus.APPROVED,
                decided_by=self.world.supervisor,
                decided_at=timezone.now(),
            )  # sin soporte
        approved = make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30)
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=approved.pk).update(decided_by=self.world.admin)

    def test_voided_needs_its_reason(self) -> None:
        approved = make_certificate(self.cw, self.contract)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContractCertificate.objects.filter(pk=approved.pk).update(
                status=CertificateStatus.VOIDED
            )

    def test_a_changed_quantity_needs_its_justification(self) -> None:
        certificate = make_certificate(self.cw, self.contract, quantities={"2.1": "2"})
        line = CertificateLine.objects.get(certificate=certificate, line__item__code="2.1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateLine.objects.filter(pk=line.pk).update(quantity=Decimal("3"))
        CertificateLine.objects.filter(pk=line.pk).update(
            quantity=Decimal("3"), justification="Medición en obra"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateLine.objects.filter(pk=line.pk).update(
                quantity=Decimal("-1"), proposed_quantity=Decimal("-1")
            )

    def test_amendments_are_numbered_and_one_is_open(self) -> None:
        def amendment(number: int) -> ContractAmendment:
            return ContractAmendment.objects.create(
                org=self.world.org,
                contract=self.contract,
                project=self.world.project,
                number=number,
                signed_on=date(2026, 4, 10),
                reason="Mayor cantidad",
                created_by=self.world.admin,
                updated_by=self.world.admin,
            )

        amendment(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            amendment(2)

    def test_the_ledger_knows_its_certificate(self) -> None:
        field = CostEntry._meta.get_field("certificate")
        self.assertTrue(field.null)
        self.assertIn(CostDocumentType.CERTIFICATE, CostDocumentType.values)
```

- [ ] **Step 4: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_models.py suno/core/tests/test_policies.py`
Expected: ERROR (`cannot import name 'Contract' from 'suno.core.models'`).

- [ ] **Step 5: El modelo**

`server/suno/core/models/contract.py`:

```python
"""Subcontratos y destajos (costos, pieza 6b).

Diseño: plans/2026-11-14-costos-pieza6b-contratos-design.md, sección 1.

- `Contract`: el contrato (subcontrato o destajo) con sus líneas, cargos y descuentos. Nace en
  borrador; lo aprueba otra persona y desde ahí solo cambia por otrosíes. Termina liquidado (su
  acta de liquidación aprobada) o anulado (aprobado y sin actas aprobadas). Un rechazo lo deja
  en borrador con el motivo.
- `ContractLine`: un ítem del presupuesto (identidad estable) y las tareas que cubre (ninguna:
  todas las del ítem). `amendment`: el otrosí que la agregó; solo cuenta si está aprobado.
- `ContractAmendment` y `ContractAmendmentLine`: el otrosí y sus aumentos de cantidad.
- `ContractCertificate` y `CertificateLine`: el acta de avance (o de liquidación) por
  cantidades. Al aprobarse escribe sus movimientos en el libro (`CostEntry.certificate`).

El dinero es `numeric` y `Decimal`; la aritmética vive en `suno.contracts.calc`.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel

#: Estados abiertos de un otrosí o un acta: se pueden editar o borrar.
OPEN_STATES = ("draft", "rejected")


class ContractKind(models.TextChoices):
    SUBCONTRACT = "subcontract", "Subcontrato"
    PIECEWORK = "piecework", "Destajo"


class ContractStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    SETTLED = "settled", "Liquidado"
    VOIDED = "voided", "Anulado"


class AmendmentStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobado"
    REJECTED = "rejected", "Rechazado"


class CertificateStatus(models.TextChoices):
    DRAFT = "draft", "Borrador"
    APPROVED = "approved", "Aprobada"
    REJECTED = "rejected", "Rechazada"
    VOIDED = "voided", "Anulada"


def _pct_between_0_and_100(field: str, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(**{f"{field}__gte": 0, f"{field}__lte": 100}), name=name
    )


def _decided_by_someone_else() -> models.Q:
    return ~models.Q(decided_by=models.F("created_by")) & ~models.Q(
        decided_by=models.F("updated_by")
    )


class Contract(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="contracts", verbose_name="proyecto"
    )
    kind = models.CharField(max_length=12, choices=ContractKind.choices, verbose_name="tipo")
    number = models.CharField(max_length=30, verbose_name="número")
    purpose = models.CharField(max_length=300, verbose_name="objeto")
    contractor = models.CharField(max_length=200, verbose_name="contratista")
    starts_on = models.DateField(verbose_name="inicio")
    ends_on = models.DateField(verbose_name="fin")
    status = models.CharField(
        max_length=10,
        choices=ContractStatus.choices,
        default=ContractStatus.DRAFT,
        verbose_name="estado",
    )
    advance_pct = models.DecimalField(
        max_digits=7, decimal_places=4, default=0, verbose_name="anticipo (%)"
    )
    #: Valor vigente × % al aprobar el contrato; fijo después (un otrosí no lo cambia).
    advance_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=0, verbose_name="anticipo"
    )
    retention_pct = models.DecimalField(
        max_digits=7, decimal_places=4, default=0, verbose_name="retención de garantía (%)"
    )
    #: Bruto directo de las cantidades vigentes + cargos. Lo recalcula el servicio al guardar el
    #: borrador y al aprobar un otrosí.
    current_value = models.DecimalField(
        max_digits=18, decimal_places=2, default=0, verbose_name="valor vigente"
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="aprobó",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    #: El último rechazo: el contrato sigue en borrador con el motivo; al editarlo se limpia.
    rejected_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="rechazó",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    #: Liquidación (quien aprobó el acta de liquidación) o anulación (con su motivo).
    closed_by = models.ForeignKey(
        "core.AppUser",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="cerró",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    close_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")

    class Meta:
        db_table = "contract"
        ordering = ["number"]
        verbose_name = "contrato"
        verbose_name_plural = "contratos"
        indexes = [
            models.Index(fields=["org", "project", "status"], name="contract_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                Lower("number"), "project", name="contract_project_number_ci_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(starts_on__lte=models.F("ends_on")), name="contract_dates_ok"
            ),
            _pct_between_0_and_100("advance_pct", "contract_advance_pct_ok"),
            _pct_between_0_and_100("retention_pct", "contract_retention_pct_ok"),
            models.CheckConstraint(
                condition=models.Q(advance_amount__gte=0, current_value__gte=0),
                name="contract_amounts_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status="draft", approved_by__isnull=True, approved_at__isnull=True
                )
                | (
                    models.Q(
                        status__in=["approved", "settled", "voided"],
                        approved_by__isnull=False,
                        approved_at__isnull=False,
                    )
                    & ~models.Q(approved_by=models.F("created_by"))
                    & ~models.Q(approved_by=models.F("updated_by"))
                ),
                name="contract_approval_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["settled", "voided"],
                    closed_by__isnull=False,
                    closed_at__isnull=False,
                )
                | models.Q(
                    status__in=["draft", "approved"],
                    closed_by__isnull=True,
                    closed_at__isnull=True,
                ),
                name="contract_closing_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="voided") | ~models.Q(close_note=""),
                name="contract_void_note",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    rejected_by__isnull=True, rejected_at__isnull=True, rejection_note=""
                )
                | (
                    models.Q(status="draft", rejected_by__isnull=False, rejected_at__isnull=False)
                    & ~models.Q(rejection_note="")
                ),
                name="contract_rejection_pair",
            ),
        ]

    def __str__(self) -> str:
        return self.number


class ContractLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="lines")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    item = models.ForeignKey(
        "core.BudgetItem", on_delete=models.PROTECT, related_name="contract_lines"
    )
    tasks = models.ManyToManyField(
        "core.Task", through="ContractLineTask", related_name="contract_lines", blank=True
    )
    unit = models.CharField(max_length=30, blank=True, verbose_name="unidad")
    #: Con la que entró (en el contrato o en el otrosí que la agregó).
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Contratada vigente: `quantity` + aumentos de los otrosíes aprobados.
    current_quantity = models.DecimalField(
        max_digits=18, decimal_places=4, verbose_name="cantidad vigente"
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="precio")
    #: El otrosí que la agregó; nulo si viene del contrato. Cuenta si el otrosí está aprobado.
    amendment = models.ForeignKey(
        "core.ContractAmendment",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="new_lines",
    )

    class Meta:
        db_table = "contract_line"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "order"], name="contract_line_order_uq"),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gt=0,
                    current_quantity__gte=models.F("quantity"),
                    unit_price__gte=0,
                ),
                name="contract_line_values_ok",
            ),
        ]


class ContractLineTask(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(ContractLine, on_delete=models.CASCADE, related_name="task_links")
    task = models.ForeignKey("core.Task", on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "contract_line_task"
        constraints = [
            models.UniqueConstraint(fields=["line", "task"], name="contract_line_task_uq"),
        ]


class ContractCharge(OrgScopedModel):
    """Un cargo del contrato (AIU, IVA…), como `BudgetCharge`: base `direct`, `subtotal` o
    `line:<orden>`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="charges")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    base = models.CharField(max_length=20, verbose_name="base")

    class Meta:
        db_table = "contract_charge"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["contract", "order"], name="contract_charge_order_uq"),
            models.CheckConstraint(condition=models.Q(pct__gte=0), name="contract_charge_pct_ok"),
        ]


class ContractDeduction(OrgScopedModel):
    """Un descuento del acta (retenciones, estampillas…): base `direct`, `gross` o `line:<n>`
    (la línea `n` de los cargos)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="deductions")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    base = models.CharField(max_length=20, verbose_name="base")

    class Meta:
        db_table = "contract_deduction"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "order"], name="contract_deduction_order_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(pct__gte=0), name="contract_deduction_pct_ok"
            ),
        ]


class ContractAmendment(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="amendments")
    #: El del contrato, repetido: alcance por proyecto y bandeja "Por aprobar".
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField(verbose_name="número")
    signed_on = models.DateField(verbose_name="fecha")
    reason = models.CharField(max_length=500, verbose_name="motivo")
    new_ends_on = models.DateField(null=True, blank=True, verbose_name="nueva fecha de fin")
    status = models.CharField(
        max_length=10,
        choices=AmendmentStatus.choices,
        default=AmendmentStatus.DRAFT,
        verbose_name="estado",
    )
    #: Valor vigente del contrato después de aplicarlo (al aprobarlo).
    value_after = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")

    class Meta:
        db_table = "contract_amendment"
        ordering = ["number"]
        verbose_name = "otrosí"
        verbose_name_plural = "otrosíes"
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "number"], name="contract_amendment_number_uq"
            ),
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(status__in=OPEN_STATES),
                name="contract_amendment_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="contract_amendment_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="approved") | _decided_by_someone_else(),
                name="contract_amendment_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="contract_amendment_rejection_note",
            ),
        ]


class ContractAmendmentLine(OrgScopedModel):
    """Un aumento de la cantidad de una línea existente."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    amendment = models.ForeignKey(
        ContractAmendment, on_delete=models.CASCADE, related_name="increases"
    )
    line = models.ForeignKey(ContractLine, on_delete=models.PROTECT, related_name="+")
    added_quantity = models.DecimalField(max_digits=18, decimal_places=4)

    class Meta:
        db_table = "contract_amendment_line"
        constraints = [
            models.UniqueConstraint(
                fields=["amendment", "line"], name="contract_amendment_line_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(added_quantity__gt=0), name="contract_amendment_line_positive"
            ),
        ]


class ContractCertificate(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="certificates")
    #: El del contrato, repetido: alcance por proyecto y bandeja "Por aprobar".
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="+")
    number = models.PositiveIntegerField(verbose_name="número")
    period_start = models.DateField(verbose_name="desde")
    period_end = models.DateField(verbose_name="hasta")
    is_settlement = models.BooleanField(default=False, verbose_name="liquidación")
    status = models.CharField(
        max_length=10,
        choices=CertificateStatus.choices,
        default=CertificateStatus.DRAFT,
        verbose_name="estado",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="preparó"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="editó"
    )
    updated_at = models.DateTimeField(auto_now=True)
    decided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, verbose_name="motivo")
    voided_by = models.ForeignKey(
        "core.AppUser", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_note = models.CharField(max_length=500, blank=True, verbose_name="motivo de anulación")
    #: El acta firmada (módulo de adjuntos). Obligatoria para aprobar.
    attachment = models.ForeignKey(
        "core.Blob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="contract_certificates",
        verbose_name="acta firmada",
    )
    attachment_name = models.CharField(max_length=200, blank=True, verbose_name="archivo")
    direct = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    charges = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Bruto = directo + cargos: el costo que va al libro.
    gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amortization = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention_returned = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Puede ser negativo en una liquidación.
    net = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    #: Cargos y descuentos línea por línea (`calc.CertificateTotals.breakdown`).
    breakdown = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "contract_certificate"
        ordering = ["number"]
        verbose_name = "acta"
        verbose_name_plural = "actas"
        indexes = [
            models.Index(fields=["org", "project", "status"], name="contract_cert_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "number"], name="contract_certificate_number_uq"
            ),
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(status__in=OPEN_STATES),
                name="contract_certificate_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(period_start__lte=models.F("period_end")),
                name="contract_certificate_period_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(status="draft", decided_by__isnull=True, decided_at__isnull=True)
                | models.Q(
                    status__in=["approved", "rejected", "voided"],
                    decided_by__isnull=False,
                    decided_at__isnull=False,
                ),
                name="contract_certificate_decision_pair",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=["approved", "voided"])
                | (models.Q(attachment__isnull=False) & _decided_by_someone_else()),
                name="contract_certificate_approval_ok",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | ~models.Q(decision_note=""),
                name="contract_certificate_rejection_note",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="voided", voided_by__isnull=False, voided_at__isnull=False)
                    & ~models.Q(void_note="")
                )
                | (
                    ~models.Q(status="voided")
                    & models.Q(voided_by__isnull=True, voided_at__isnull=True)
                ),
                name="contract_certificate_void_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    direct__gte=0,
                    charges__gte=0,
                    gross__gte=0,
                    amortization__gte=0,
                    retention__gte=0,
                    deductions__gte=0,
                    retention_returned__gte=0,
                ),
                name="contract_certificate_amounts_ok",
            ),
        ]

    def __str__(self) -> str:
        return f"Acta {self.number}"


class CertificateLine(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    certificate = models.ForeignKey(
        ContractCertificate, on_delete=models.CASCADE, related_name="lines"
    )
    line = models.ForeignKey(
        ContractLine, on_delete=models.PROTECT, related_name="certificate_lines"
    )
    #: Lo validado en campo sin pagar (lo calcula el servidor; ver `contracts.proposal`).
    proposed_quantity = models.DecimalField(max_digits=18, decimal_places=4)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, verbose_name="cantidad")
    #: Obligatoria si la cantidad difiere de la propuesta.
    justification = models.CharField(max_length=300, blank=True, verbose_name="justificación")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="valor")

    class Meta:
        db_table = "certificate_line"
        constraints = [
            models.UniqueConstraint(fields=["certificate", "line"], name="certificate_line_uq"),
            models.CheckConstraint(
                condition=models.Q(proposed_quantity__gte=0, quantity__gte=0, amount__gte=0),
                name="certificate_line_values_ok",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity=models.F("proposed_quantity"))
                | ~models.Q(justification=""),
                name="certificate_line_justified",
            ),
        ]
```

En `server/suno/core/models/cost.py`:

1. En `CostDocumentType`, antes de `OTHER`: `CERTIFICATE = "certificate", "Acta de avance"`.
2. En `CostEntry`, después de `attachment_name`:

```python
    #: El acta de contrato que lo generó (pieza 6b). No se edita ni se reversa desde el libro:
    #: se anula el acta.
    certificate = models.ForeignKey(
        "core.ContractCertificate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cost_entries",
        verbose_name="acta",
    )
```

En `server/suno/core/models/__init__.py`: en el docstring, después de la línea de `cost`, agregar `` - `contract` — `Contract`, sus líneas, otrosíes y actas (costos, pieza 6b). ``; importar de `.contract` `OPEN_STATES`, `AmendmentStatus`, `CertificateLine`, `CertificateStatus`, `Contract`, `ContractAmendment`, `ContractAmendmentLine`, `ContractCertificate`, `ContractCharge`, `ContractDeduction`, `ContractKind`, `ContractLine`, `ContractLineTask` y `ContractStatus`, y agregarlos (salvo `OPEN_STATES`, que se importa de `suno.core.models.contract`) a `__all__` en orden alfabético.

- [ ] **Step 6: La migración**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core --name contracts"
```

Expected: `server/suno/core/migrations/0029_contracts.py` con `CreateModel` de los nueve modelos, `AddField` de `costentry.certificate`, `AlterField` de `costentry.document_type` (las opciones) y las restricciones e índices. Revisar que `dependencies` sea `('core', '0028_costs_ledger')` y que no aparezca nada fuera de lo listado. Es aditiva: no hace falta `RunPython`.

- [ ] **Step 7: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/core suno/contracts && ruff check suno/core suno/contracts && mypy suno/core suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts suno/core/tests/test_policies.py suno/core/tests/test_architecture.py suno/costs/tests/test_models.py
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
```

Expected: PASS; `No changes detected`.

- [ ] **Step 8: Commit**

```bash
git add server/suno/core/models server/suno/core/migrations/0029_contracts.py server/suno/contracts server/suno/core/tests/test_policies.py
git commit -m "feat(contracts): contract, amendment and certificate models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Cálculo del contrato y del acta (puro)

**Files:**
- Create: `server/suno/contracts/calc.py`
- Test: `server/suno/contracts/tests/test_calc.py`

Los números de las pruebas salen del contrato de ejemplo de `factories.LINES` con AIU (10/5/5, IVA 19 % sobre la utilidad), anticipo 30 %, garantía 5 % y `factories.WITHHOLDINGS`. Acta 1: 4 m3 del 2.1 (1.600.000) y 200 kg del 2.2 (700.000):

| Concepto | Valor |
|---|---|
| Bruto directo | 2.300.000,00 |
| Cargos: A 230.000 + I 115.000 + U 115.000 + IVA 21.850 | 481.850,00 |
| Bruto | 2.781.850,00 |
| Amortización: 2.781.850 × 2.467.380 ÷ 8.224.600 (= 30 %) | 834.555,00 |
| Retención 5 % | 139.092,50 |
| Descuentos: 2 % de 2.300.000 + 0,966 % de 2.300.000 + 15 % de 21.850 | 46.000,00 + 22.218,00 + 3.277,50 = 71.495,50 |
| Neto | 1.736.707,00 |
| Libro: 2.1 = 1.600.000 + 481.850 × 16/23 | 1.935.200,00 |
| Libro: 2.2 = 700.000 + 481.850 × 7/23 | 846.650,00 |

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_calc.py`:

```python
"""Cálculo de contratos y actas (spec, 3.1 y 3.2). Puro: sin BD."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from suno.budgets.tests.samples import AIU
from suno.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    ChargeError,
    ChargeSpec,
    compute_charges,
    line_base,
)
from suno.contracts.calc import (
    BASE_GROSS,
    Balances,
    LineAmount,
    LineInput,
    certificate_totals,
    charge_template,
    compute_deductions,
    contract_value,
    ledger_split,
    validate_deductions,
    withholding_template,
)

from .factories import WITHHOLDINGS

D = Decimal
L1, L2, L3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
VALUE = D("8224600.00")
ADVANCE = D("2467380.00")


def lines(q1: str, q2: str) -> list[LineInput]:
    return [LineInput(L1, 1, D(q1), D("400000.00")), LineInput(L2, 2, D(q2), D("3500.00"))]


class ContractValueTests(SimpleTestCase):
    def test_value_is_direct_plus_charges(self) -> None:
        value = contract_value(lines("10", "800"), AIU)
        self.assertEqual(value.direct_cost, D("6800000.00"))
        self.assertEqual([line.amount for line in value.lines],
                         [D("680000.00"), D("340000.00"), D("340000.00"), D("64600.00")])  # fmt: skip
        self.assertEqual(value.total, VALUE)

    def test_without_charges_the_value_is_the_direct_cost(self) -> None:
        self.assertEqual(contract_value(lines("10", "800"), []).total, D("6800000.00"))


class CertificateTotalsTests(SimpleTestCase):
    def balances(self, **values: str) -> Balances:
        return Balances(
            current_value=VALUE,
            advance_amount=ADVANCE,
            **{key: D(value) for key, value in values.items()},
        )

    def test_aiu_with_advance_retention_and_withholdings(self) -> None:
        totals = certificate_totals(
            lines("4", "200"),
            AIU,
            WITHHOLDINGS,
            retention_pct=D("5"),
            balances=self.balances(),
        )
        self.assertEqual(totals.direct, D("2300000.00"))
        self.assertEqual(totals.charges_total, D("481850.00"))
        self.assertEqual(totals.gross, D("2781850.00"))
        self.assertEqual(totals.amortization, D("834555.00"))
        self.assertEqual(totals.retention, D("139092.50"))
        self.assertEqual([d.amount for d in totals.deductions],
                         [D("46000.00"), D("22218.00"), D("3277.50")])  # fmt: skip
        self.assertEqual(totals.deductions_total, D("71495.50"))
        self.assertEqual(totals.retention_returned, D("0.00"))
        self.assertEqual(totals.net, D("1736707.00"))
        stored = totals.stored()
        self.assertEqual(stored["gross"], D("2781850.00"))
        self.assertEqual(stored["net"], D("1736707.00"))
        breakdown = totals.breakdown()
        self.assertEqual(breakdown["charges"][3]["name"], "IVA sobre utilidad")
        self.assertEqual(breakdown["deductions"][2]["amount"], "3277.50")

    def test_full_vat_and_no_charges(self) -> None:
        iva = [ChargeSpec(1, "IVA", D("19"), BASE_DIRECT)]
        balances = Balances(VALUE, D("0.00"))
        vat = certificate_totals(lines("4", "200"), iva, [], retention_pct=D("0"), balances=balances)
        self.assertEqual(vat.gross, D("2737000.00"))
        bare = certificate_totals(lines("4", "200"), [], [], retention_pct=D("0"), balances=balances)
        self.assertEqual((bare.gross, bare.net), (D("2300000.00"), D("2300000.00")))

    def test_amortization_never_passes_the_balance(self) -> None:
        totals = certificate_totals(
            lines("4", "200"),
            AIU,
            [],
            retention_pct=D("5"),
            balances=self.balances(amortized="2367380.00"),
        )
        self.assertEqual(totals.amortization, D("100000.00"))
        spent = certificate_totals(
            lines("4", "200"), AIU, [], retention_pct=D("0"),
            balances=self.balances(amortized="2467380.00"),
        )  # fmt: skip
        self.assertEqual(spent.amortization, D("0.00"))

    def test_settlement_amortizes_the_rest_and_returns_the_retention(self) -> None:
        totals = certificate_totals(
            lines("0", "0"),
            AIU,
            WITHHOLDINGS,
            retention_pct=D("5"),
            balances=self.balances(amortized="834555.00", retained="139092.50"),
            settlement=True,
        )
        self.assertEqual(totals.gross, D("0.00"))
        self.assertEqual(totals.amortization, D("1632825.00"))
        self.assertEqual(totals.retention, D("0.00"))
        self.assertEqual(totals.retention_returned, D("139092.50"))
        self.assertEqual(totals.net, D("-1493732.50"))

    def test_huge_values_keep_every_digit(self) -> None:
        big = [LineInput(L1, 1, D("99999999.9999"), D("9999999.99"))]
        totals = certificate_totals(
            big, [], [], retention_pct=D("5"),
            balances=Balances(D("999999998999000.00"), D("299999999699700.00")),
        )  # fmt: skip
        self.assertEqual(totals.direct, D("999999998999000.00"))
        self.assertEqual(totals.amortization, D("299999999699700.00"))
        self.assertEqual(totals.retention, D("49999999949950.00"))


class DeductionTests(SimpleTestCase):
    def test_bases(self) -> None:
        charges = compute_charges(D("1000.00"), AIU)
        result = compute_deductions(
            D("1000.00"),
            charges,
            [
                ChargeSpec(1, "Directo", D("10"), BASE_DIRECT),
                ChargeSpec(2, "Bruto", D("10"), BASE_GROSS),
                ChargeSpec(3, "Del IVA", D("100"), line_base(4)),
            ],
        )
        self.assertEqual([line.base_amount for line in result],
                         [D("1000.00"), charges.total, D("9.50")])  # fmt: skip
        self.assertEqual([line.amount for line in result], [D("100.00"), D("120.95"), D("9.50")])

    def test_invalid_bases_and_percentages(self) -> None:
        for wrong in (
            [ChargeSpec(1, "Subtotal", D("1"), BASE_SUBTOTAL)],
            [ChargeSpec(1, "Cargo 5", D("1"), line_base(5))],
            [ChargeSpec(2, "Desordenado", D("1"), BASE_DIRECT)],
            [ChargeSpec(1, " ", D("1"), BASE_DIRECT)],
            [ChargeSpec(1, "Más de 100", D("100.0001"), BASE_DIRECT)],
        ):
            with self.subTest(wrong=wrong), self.assertRaises(ChargeError):
                validate_deductions(wrong, charge_count=4)


class LedgerSplitTests(SimpleTestCase):
    def test_charges_in_proportion_to_the_direct_cost(self) -> None:
        amounts = [
            LineAmount(L1, 1, D("4"), D("1600000.00")),
            LineAmount(L2, 2, D("200"), D("700000.00")),
            LineAmount(L3, 3, D("0"), D("0.00")),
        ]
        self.assertEqual(
            ledger_split(amounts, D("481850.00")),
            {L1: D("1935200.00"), L2: D("846650.00")},
        )

    def test_the_rounding_goes_to_the_biggest_line_and_adds_up(self) -> None:
        amounts = [
            LineAmount(L1, 1, D("1"), D("100.00")),
            LineAmount(L2, 2, D("1"), D("100.00")),
            LineAmount(L3, 3, D("1"), D("100.00")),
        ]
        split = ledger_split(amounts, D("100.00"))
        self.assertEqual(split, {L1: D("133.34"), L2: D("133.33"), L3: D("133.33")})
        self.assertEqual(sum(split.values()), D("400.00"))

    def test_nothing_to_post(self) -> None:
        self.assertEqual(ledger_split([LineAmount(L1, 1, D("0"), D("0.00"))], D("0.00")), {})


class TemplateTests(SimpleTestCase):
    def test_charge_templates(self) -> None:
        aiu = charge_template("aiu")
        self.assertEqual([c.name for c in aiu],
                         ["Administración", "Imprevistos", "Utilidad", "IVA sobre utilidad"])  # fmt: skip
        self.assertEqual([c.pct for c in aiu], [D("0"), D("0"), D("0"), D("19")])
        self.assertEqual(aiu[3].base, line_base(3))
        self.assertEqual(charge_template("iva"), [ChargeSpec(1, "IVA", D("19"), BASE_DIRECT)])
        self.assertEqual(charge_template("ninguno"), [])
        self.assertEqual(charge_template("otra"), [])

    def test_withholdings_find_the_vat_line(self) -> None:
        self.assertEqual(withholding_template(charge_template("aiu"))[2].base, line_base(4))
        self.assertEqual(withholding_template(charge_template("iva"))[2].base, line_base(1))
        self.assertEqual(withholding_template([])[2].base, BASE_GROSS)
        self.assertEqual({d.pct for d in withholding_template([])}, {D("0")})
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_calc.py`
Expected: ERROR (`No module named 'suno.contracts.calc'`).

- [ ] **Step 3: Implementar**

`server/suno/contracts/calc.py`:

```python
"""Cálculo de contratos y actas (costos, pieza 6b, spec 3). Puro: sin BD.

Todo es `Decimal` con el redondeo comercial a centavos de `budgets.totals`, línea por línea:

1. Bruto directo = Σ (cantidad × precio del contrato), cada línea a centavos.
2. Cargos: los del contrato sobre sus bases (`budgets.totals.compute_charges`).
3. Bruto = bruto directo + cargos: el costo que va al libro.
4. Amortización = bruto × anticipo ÷ valor vigente, sin pasar del saldo (en la liquidación,
   todo el saldo).
5. Retención de garantía = bruto × % (en la liquidación no se retiene y se devuelve lo retenido).
6. Descuentos: cada uno sobre su base: `direct` (bruto directo), `gross` (bruto) o `line:<n>`
   (la línea `n` de los cargos del acta).
7. Neto = bruto − amortización − retención − descuentos + devolución.

El valor vigente de un contrato es el bruto directo de sus cantidades vigentes más sus cargos.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any
from uuid import UUID

from suno.budgets.totals import (
    BASE_DIRECT,
    HUNDRED,
    MAX_CHARGE_NAME,
    MAX_CHARGES,
    PCT_STEP,
    WIDE_PRECISION,
    ZERO,
    ChargeError,
    ChargeLine,
    ChargeResult,
    ChargeSpec,
    base_line_order,
    compute_charges,
    default_charges,
    item_total,
    line_base,
    money,
)

BASE_GROSS = "gross"
#: Tarifa general del IVA en Colombia (plantillas).
VAT = Decimal("19")
CHARGE_TEMPLATES = {
    "aiu": "AIU con IVA sobre utilidad",
    "iva": "IVA pleno",
    "ninguno": "Sin cargos",
}
DEDUCTION_TEMPLATES = {"retenciones": "Retenciones típicas", "ninguno": "Sin descuentos"}


@dataclass(frozen=True)
class LineInput:
    line_id: UUID
    order: int
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class LineAmount:
    line_id: UUID
    order: int
    quantity: Decimal
    amount: Decimal


def scaled(value: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    """`value × numerator ÷ denominator` a centavos, sin perder dígitos en el producto."""
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return money(value * numerator / denominator)


def line_amounts(lines: Sequence[LineInput]) -> tuple[LineAmount, ...]:
    return tuple(
        LineAmount(
            line.line_id, line.order, line.quantity, item_total(line.quantity, line.unit_price)
        )
        for line in lines
    )


def contract_value(lines: Sequence[LineInput], charges: Sequence[ChargeSpec]) -> ChargeResult:
    """Bruto directo de las cantidades dadas (las vigentes) y sus cargos."""
    direct = sum((amount.amount for amount in line_amounts(lines)), ZERO)
    return compute_charges(direct, charges)


def validate_deductions(deductions: Sequence[ChargeSpec], charge_count: int) -> None:
    if len(deductions) > MAX_CHARGES:
        raise ChargeError(f"Máximo {MAX_CHARGES} descuentos.")
    for expected, deduction in enumerate(deductions, start=1):
        if deduction.order != expected:
            raise ChargeError("Los descuentos deben numerarse 1, 2, 3… en orden.")
        name = deduction.name.strip()
        if not name or len(name) > MAX_CHARGE_NAME:
            raise ChargeError(
                f"Descuento {expected}: el nombre es obligatorio y tiene máximo "
                f"{MAX_CHARGE_NAME} caracteres."
            )
        pct = deduction.pct
        if (
            not pct.is_finite()
            or pct < 0
            or pct > HUNDRED
            or pct != pct.quantize(PCT_STEP, rounding=ROUND_HALF_UP)
        ):
            raise ChargeError(
                f"Descuento {expected}: el porcentaje va entre 0 y 100, con máximo 4 decimales."
            )
        if deduction.base in (BASE_DIRECT, BASE_GROSS):
            continue
        ref = base_line_order(deduction.base)
        if ref is None or ref > charge_count:
            raise ChargeError(
                f"Descuento {expected}: la base es el bruto directo, el bruto o un cargo "
                "del contrato."
            )


def compute_deductions(
    direct: Decimal, charges: ChargeResult, deductions: Sequence[ChargeSpec]
) -> tuple[ChargeLine, ...]:
    validate_deductions(deductions, len(charges.lines))
    by_order = {line.order: line.amount for line in charges.lines}
    result: list[ChargeLine] = []
    for deduction in deductions:
        if deduction.base == BASE_DIRECT:
            base_amount = direct
        elif deduction.base == BASE_GROSS:
            base_amount = charges.total
        else:
            base_amount = by_order[base_line_order(deduction.base) or 0]
        result.append(
            ChargeLine(
                order=deduction.order,
                name=deduction.name.strip(),
                pct=deduction.pct,
                base=deduction.base,
                base_amount=base_amount,
                amount=scaled(base_amount, deduction.pct, HUNDRED),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class Balances:
    """Lo que ya pasó en el contrato antes de un acta (sus actas aprobadas)."""

    current_value: Decimal
    advance_amount: Decimal
    amortized: Decimal = ZERO
    retained: Decimal = ZERO
    returned: Decimal = ZERO

    @property
    def advance_balance(self) -> Decimal:
        return max(self.advance_amount - self.amortized, ZERO)

    @property
    def retained_balance(self) -> Decimal:
        return max(self.retained - self.returned, ZERO)


def _line_json(line: ChargeLine) -> dict[str, Any]:
    return {
        "order": line.order,
        "name": line.name,
        "pct": str(line.pct),
        "base": line.base,
        "base_amount": str(line.base_amount),
        "amount": str(line.amount),
    }


@dataclass(frozen=True)
class CertificateTotals:
    lines: tuple[LineAmount, ...]
    charges: ChargeResult
    deductions: tuple[ChargeLine, ...]
    amortization: Decimal
    retention: Decimal
    retention_returned: Decimal

    @property
    def direct(self) -> Decimal:
        return self.charges.direct_cost

    @property
    def charges_total(self) -> Decimal:
        return money(self.charges.total - self.charges.direct_cost)

    @property
    def gross(self) -> Decimal:
        return self.charges.total

    @property
    def deductions_total(self) -> Decimal:
        return money(sum((line.amount for line in self.deductions), ZERO))

    @property
    def net(self) -> Decimal:
        return money(
            self.gross
            - self.amortization
            - self.retention
            - self.deductions_total
            + self.retention_returned
        )

    def stored(self) -> dict[str, Decimal]:
        """Los campos de `ContractCertificate` con los totales."""
        return {
            "direct": self.direct,
            "charges": self.charges_total,
            "gross": self.gross,
            "amortization": self.amortization,
            "retention": self.retention,
            "deductions": self.deductions_total,
            "retention_returned": self.retention_returned,
            "net": self.net,
        }

    def breakdown(self) -> dict[str, Any]:
        return {
            "charges": [_line_json(line) for line in self.charges.lines],
            "deductions": [_line_json(line) for line in self.deductions],
        }


def certificate_totals(
    lines: Sequence[LineInput],
    charges: Sequence[ChargeSpec],
    deductions: Sequence[ChargeSpec],
    *,
    retention_pct: Decimal,
    balances: Balances,
    settlement: bool = False,
) -> CertificateTotals:
    amounts = line_amounts(lines)
    direct = sum((amount.amount for amount in amounts), ZERO)
    charge_result = compute_charges(direct, charges)
    gross = charge_result.total
    if settlement:
        amortization = balances.advance_balance
        retention = ZERO
        returned = balances.retained_balance
    else:
        share = (
            scaled(gross, balances.advance_amount, balances.current_value)
            if balances.current_value > 0
            else ZERO
        )
        amortization = min(share, balances.advance_balance)
        retention = scaled(gross, retention_pct, HUNDRED)
        returned = ZERO
    return CertificateTotals(
        lines=amounts,
        charges=charge_result,
        deductions=compute_deductions(direct, charge_result, deductions),
        amortization=amortization,
        retention=retention,
        retention_returned=returned,
    )


def ledger_split(amounts: Sequence[LineAmount], charges_total: Decimal) -> dict[UUID, Decimal]:
    """Valor de cada línea en el libro: su directo más su parte de los cargos, en proporción al
    directo. El ajuste de redondeo va a la línea de mayor valor (empate: la de menor orden), así
    la suma es exactamente el bruto. Las líneas en cero no van."""
    priced = [amount for amount in amounts if amount.amount > 0]
    if not priced:
        return {}
    direct = sum((amount.amount for amount in priced), ZERO)
    values = {
        amount.line_id: amount.amount + scaled(charges_total, amount.amount, direct)
        for amount in priced
    }
    biggest = min(priced, key=lambda amount: (-amount.amount, amount.order)).line_id
    values[biggest] += direct + charges_total - sum(values.values(), ZERO)
    return values


def _folded(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.strip().casefold())
        if not unicodedata.combining(char)
    )


def charge_template(name: str) -> list[ChargeSpec]:
    """"AIU con IVA sobre utilidad" (A, I y U en 0 % para llenar), "IVA pleno" o "Sin cargos"."""
    if name == "aiu":
        aiu = default_charges()
        return [*aiu[:3], ChargeSpec(4, aiu[3].name, VAT, aiu[3].base)]
    if name == "iva":
        return [ChargeSpec(1, "IVA", VAT, BASE_DIRECT)]
    return []


def withholding_template(charges: Sequence[ChargeSpec]) -> list[ChargeSpec]:
    """"Retenciones típicas", en 0 % para llenar. ReteIVA va sobre la primera línea de cargos
    cuyo nombre empieza por "IVA" (o sobre el bruto si no hay)."""
    vat = next((c.order for c in charges if _folded(c.name).startswith("iva")), None)
    return [
        ChargeSpec(1, "Retención en la fuente", Decimal("0"), BASE_DIRECT),
        ChargeSpec(2, "ReteICA", Decimal("0"), BASE_DIRECT),
        ChargeSpec(3, "ReteIVA", Decimal("0"), line_base(vat) if vat else BASE_GROSS),
        ChargeSpec(4, "Estampillas", Decimal("0"), BASE_GROSS),
    ]
```

Nota: `test_huge_values_keep_every_digit` fija que `scaled` multiplica con `WIDE_PRECISION` (el producto bruto × anticipo pasa de los 28 dígitos de la precisión por defecto de `decimal`): 99.999.999,9999 × 9.999.999,99 = 999.999.998.999.000,00; el 30 % y el 5 % salen exactos.

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts && ruff check suno/contracts && mypy suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts/tests/test_calc.py suno/budgets
```

Expected: PASS (las de presupuestos siguen igual: `calc` solo importa de `totals`).

- [ ] **Step 5: Commit**

```bash
git add server/suno/contracts/calc.py server/suno/contracts/tests/test_calc.py
git commit -m "feat(contracts): certificate and contract value calculation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Cantidad propuesta desde lo validado en campo

**Files:**
- Modify: `server/suno/matrix/quantities.py` (`executed_by_pair`: `captured_from`, `captured_to`)
- Create: `server/suno/contracts/proposal.py`
- Test: `server/suno/contracts/tests/test_proposal.py`

Las cantidades de campo de la pieza 4b viven en `Record.quantities` (`[{item_id, location_id, quantity, note}]`) y cuentan cuando la revisión vigente del registro es "aprobada" (`projects.field_progress.approved`). `matrix.quantities.executed_by_pair` ya las suma por (tarea, ítem) en una consulta; se le agregan los límites de fecha de captura, en la zona horaria de Colombia, como el cargador del control (`captured_at__date`).

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_proposal.py`:

```python
"""Cantidad propuesta de un acta (spec, 2.4 y 3.3): periodo, tareas, pagado, validación tardía."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.test import TestCase

from suno.contracts.proposal import LineScope, line_scopes, paid_by_line, proposals
from suno.core.models import CertificateStatus, ContractLine
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import item
from suno.matrix.quantities import executed_by_pair

from .factories import (
    contract_line,
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
MARCH_1, MARCH_15, MARCH_16, MARCH_31 = (
    date(2026, 3, 1), date(2026, 3, 15), date(2026, 3, 16), date(2026, 3, 31),
)  # fmt: skip


@skip_without_db
class ProposalTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.ctx = self.cw.world.ctx()
        # 2.1 solo en la tarea 2.1 Columnas (uid 5); 2.2 en todas.
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        self.steel = contract_line(self.contract, "2.2")
        field_quantity(self.cw, 5, "2.1", "3", date(2026, 3, 5))
        field_quantity(self.cw, 5, "2.1", "2", date(2026, 3, 20))
        field_quantity(self.cw, 3, "2.1", "7", date(2026, 3, 6))  # otra tarea: no es de la línea
        self.late = field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 12), validated=False)
        field_quantity(self.cw, 3, "2.2", "100", date(2026, 3, 8))
        field_quantity(self.cw, 5, "2.2", "50", date(2026, 3, 9))

    def scopes(self) -> list[LineScope]:
        lines = ContractLine.objects.filter(contract=self.contract).prefetch_related("task_links")
        return line_scopes(lines)

    def test_period_and_tasks_of_the_line(self) -> None:
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_1, end=MARCH_15)
        concrete, steel = found[self.concrete.pk], found[self.steel.pk]
        self.assertEqual((concrete.validated, concrete.in_period, concrete.paid), (D(3), D(3), D(0)))
        self.assertEqual(concrete.proposed, D(3))
        self.assertEqual(steel.proposed, D(150))

    def test_paid_is_discounted_and_a_late_validation_enters_the_next_one(self) -> None:
        make_certificate(
            self.cw, self.contract, start=MARCH_1, end=MARCH_15,
            quantities={"2.1": "3", "2.2": "150"},
        )  # fmt: skip
        self.cw.world.approve(self.late)  # capturado el 12, validado después del acta 1
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_16, end=MARCH_31)
        concrete = found[self.concrete.pk]
        self.assertEqual((concrete.validated, concrete.paid), (D(9), D(3)))
        self.assertEqual(concrete.in_period, D(2))
        self.assertEqual(concrete.proposed, D(6))
        self.assertEqual(found[self.steel.pk].proposed, D(0))

    def test_only_approved_certificates_count_as_paid(self) -> None:
        make_certificate(self.cw, self.contract, quantities={"2.1": "3"},
                         status=CertificateStatus.VOIDED)  # fmt: skip
        make_certificate(self.cw, self.contract, number=2, start=date(2026, 4, 1),
                         end=date(2026, 4, 30), quantities={"2.1": "1"},
                         status=CertificateStatus.DRAFT)  # fmt: skip
        self.assertEqual(paid_by_line(self.ctx, [self.concrete.pk]), {})

    def test_never_negative(self) -> None:
        make_certificate(self.cw, self.contract, quantities={"2.1": "8"})
        found = proposals(self.ctx, self.cw.project, self.scopes(), start=MARCH_1, end=MARCH_31)
        self.assertEqual(found[self.concrete.pk].proposed, D(0))

    def test_executed_by_pair_by_capture_date(self) -> None:
        pair = (self.cw.world.task(5).pk, item(self.cw.budget, "2.1").pk)
        project_ids = [self.cw.project.pk]
        self.assertEqual(executed_by_pair(self.ctx, project_ids=project_ids)[pair], D(5))
        first_half = executed_by_pair(
            self.ctx, project_ids=project_ids, captured_from=MARCH_1, captured_to=MARCH_15
        )
        self.assertEqual(first_half[pair], D(3))
        self.assertNotIn(pair, executed_by_pair(self.ctx, project_ids=project_ids,
                                                captured_to=MARCH_1))  # fmt: skip

    def test_three_queries(self) -> None:
        scopes = self.scopes()
        with self.assertNumQueries(3):
            proposals(self.ctx, self.cw.project, scopes, start=MARCH_1, end=MARCH_31)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_proposal.py`
Expected: ERROR (`No module named 'suno.contracts.proposal'`).

- [ ] **Step 3: Fechas en `executed_by_pair`**

En `server/suno/matrix/quantities.py`, agregar `from datetime import date` a los imports y reemplazar `executed_by_pair` por:

```python
def executed_by_pair(
    ctx: RequestContext,
    *,
    task_ids: Iterable[UUID] | None = None,
    project_ids: Iterable[UUID] | None = None,
    include_pending: bool = False,
    captured_from: date | None = None,
    captured_to: date | None = None,
) -> dict[Pair, Decimal]:
    """(tarea, ítem) → suma de las cantidades de sus registros validados. Una query.

    `include_pending`: también los registros aún sin revisar (no los rechazados). Lo usa
    quien decide si un par "tiene ejecución" para no borrarlo del plan: un registro
    pendiente puede validarse después.

    `captured_from`/`captured_to`: solo los registros capturados en ese rango de fechas (en la
    zona horaria del servidor, Colombia, como el cargador del control). Lo usan las actas de
    contratos (costos, pieza 6b)."""
    tasks, projects = _ids(task_ids), _ids(project_ids)
    if tasks == [] or projects == []:
        return {}
    records = (
        scoped_qs(ctx, Record)
        .filter(task__isnull=False, deleted_at__isnull=True)
        .exclude(quantities=[])
    )
    records = _by_scope(records, tasks, projects, task="task")
    if captured_from is not None:
        records = records.filter(captured_at__date__gte=captured_from)
    if captured_to is not None:
        records = records.filter(captured_at__date__lte=captured_to)
    counted = not_rejected(records) if include_pending else approved(records)
    totals: dict[Pair, Decimal] = defaultdict(Decimal)
    for task_id, rows in counted.values_list("task_id", "quantities"):
        for row in rows:
            totals[(task_id, UUID(row["item_id"]))] += Decimal(row["quantity"])
    return dict(totals)
```

- [ ] **Step 4: La propuesta**

`server/suno/contracts/proposal.py`:

```python
"""Cantidad propuesta de un acta (costos, pieza 6b, spec 2.4 y 3.3).

Para cada línea: lo validado en campo (registros con revisión vigente aprobada, pieza 4b) para
su ítem y sus tareas (ninguna: todas las del ítem), capturado hasta el fin del periodo, menos
lo ya pagado en actas aprobadas de la línea, nunca menos de cero. Así lo capturado antes y
validado tarde entra en la siguiente acta, y nada se paga dos veces. Lo capturado dentro del
periodo se muestra aparte (informativo).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Sum

from suno.core.context import RequestContext
from suno.core.models import CertificateLine, CertificateStatus, ContractLine, Project
from suno.core.scoping import scoped_qs
from suno.matrix.quantities import executed_by_pair

NONE = Decimal("0.0000")


@dataclass(frozen=True)
class LineScope:
    line_id: UUID
    item_id: UUID
    #: Vacío: todas las tareas del ítem.
    task_ids: frozenset[UUID]

    def covers(self, task_id: UUID) -> bool:
        return not self.task_ids or task_id in self.task_ids


@dataclass(frozen=True)
class Proposal:
    #: Validado hasta el fin del periodo.
    validated: Decimal
    #: Validado y capturado dentro del periodo (informativo).
    in_period: Decimal
    #: Pagado en actas aprobadas.
    paid: Decimal

    @property
    def proposed(self) -> Decimal:
        return max(self.validated - self.paid, NONE)


def line_scopes(lines: Iterable[ContractLine]) -> list[LineScope]:
    """Las líneas, con `prefetch_related("task_links")`."""
    return [
        LineScope(line.pk, line.item_id, frozenset(link.task_id for link in line.task_links.all()))
        for line in lines
    ]


def validated_by_line(
    ctx: RequestContext,
    project: Project,
    scopes: Sequence[LineScope],
    *,
    until: date,
    since: date | None = None,
) -> dict[UUID, Decimal]:
    """Lo validado por línea, capturado entre `since` (opcional) y `until`. Una consulta."""
    wanted = {scope.item_id for scope in scopes}
    by_item: dict[UUID, list[tuple[UUID, Decimal]]] = defaultdict(list)
    pairs = executed_by_pair(ctx, project_ids=[project.pk], captured_from=since, captured_to=until)
    for (task_id, item_id), quantity in pairs.items():
        if item_id in wanted:
            by_item[item_id].append((task_id, quantity))
    return {
        scope.line_id: sum(
            (quantity for task_id, quantity in by_item[scope.item_id] if scope.covers(task_id)),
            NONE,
        )
        for scope in scopes
    }


def paid_by_line(
    ctx: RequestContext, line_ids: Iterable[UUID], *, before_number: int | None = None
) -> dict[UUID, Decimal]:
    """Lo pagado por línea en actas aprobadas; con `before_number`, solo en las de número menor
    (lo "anterior" del PDF). Una consulta; las líneas sin pagos no aparecen."""
    rows = scoped_qs(ctx, CertificateLine).filter(
        line_id__in=list(line_ids), certificate__status=CertificateStatus.APPROVED
    )
    if before_number is not None:
        rows = rows.filter(certificate__number__lt=before_number)
    return dict(
        rows.order_by()
        .values("line_id")
        .annotate(total=Sum("quantity"))
        .values_list("line_id", "total")
    )


def proposals(
    ctx: RequestContext,
    project: Project,
    scopes: Sequence[LineScope],
    *,
    start: date,
    end: date,
) -> dict[UUID, Proposal]:
    """La propuesta de cada línea para el periodo `[start, end]`. Tres consultas."""
    validated = validated_by_line(ctx, project, scopes, until=end)
    in_period = validated_by_line(ctx, project, scopes, until=end, since=start)
    paid = paid_by_line(ctx, [scope.line_id for scope in scopes])
    return {
        scope.line_id: Proposal(
            validated=validated[scope.line_id],
            in_period=in_period[scope.line_id],
            paid=paid.get(scope.line_id, NONE),
        )
        for scope in scopes
    }
```

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts suno/matrix && ruff check suno/contracts suno/matrix && mypy suno/contracts suno/matrix --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts/tests/test_proposal.py suno/matrix suno/control/tests/test_loader.py
```

Expected: PASS (la matriz y el cargador no cambian: los parámetros nuevos son opcionales).

- [ ] **Step 6: Commit**

```bash
git add server/suno/matrix/quantities.py server/suno/contracts/proposal.py server/suno/contracts/tests/test_proposal.py
git commit -m "feat(contracts): propose certificate quantities from validated field records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Servicios de contratos y otrosíes

**Files:**
- Modify: `server/suno/core/audit.py` (códigos `contract.*`, `amendment.*`, `certificate.*`)
- Create: `server/suno/contracts/services.py`
- Test: `server/suno/contracts/tests/test_services.py`

Valores del otrosí de la prueba: 2.1 pasa de 10 a 12 m3 (4.800.000), 2.2 sigue en 2.800.000 y entra el 2.4 con 40 m2 × 3.000 (120.000). Directo 7.720.000; cargos A 772.000 + I 386.000 + U 386.000 + IVA 73.340 = 1.617.340; valor vigente 9.337.340,00. El anticipo sigue en 2.467.380,00.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_services.py`:

```python
"""Contratos y otrosíes (spec, 1, 2.2, 2.3 y 4): validación, traslapes, ciclo, permisos."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from suno.authn.tests.factories import make_user
from suno.budgets.tests.samples import AIU
from suno.budgets.totals import BASE_SUBTOTAL, ChargeSpec
from suno.contracts import services
from suno.contracts.services import (
    AmendmentData,
    ContractData,
    ContractRejected,
    IncreaseData,
    LineData,
)
from suno.core.access import AccessScope, SectionRole
from suno.core.audit import AuditAction
from suno.core.models import (
    AmendmentStatus,
    AppUser,
    AuditEvent,
    Contract,
    ContractKind,
    ContractLine,
    ContractStatus,
    Project,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import grant_costs, item

from .factories import (
    WITHHOLDINGS,
    contract_line,
    contract_world,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 5, 15)


@skip_without_db
class ContractServiceTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project

    def line(self, code: str, quantity: str, price: str, *uids: int) -> LineData:
        return LineData(
            item_id=item(self.cw.budget, code).pk,
            task_ids=tuple(self.world.task(uid).pk for uid in uids),
            unit="m3",
            quantity=D(quantity),
            unit_price=D(price),
        )

    def data(self, **overrides: Any) -> ContractData:
        values: dict[str, Any] = {
            "kind": ContractKind.SUBCONTRACT,
            "number": "SC-001",
            "purpose": "Estructura en concreto",
            "contractor": "Estructuras del Norte SAS",
            "starts_on": date(2026, 3, 1),
            "ends_on": date(2026, 8, 31),
            "advance_pct": D("30"),
            "retention_pct": D("5"),
            "lines": (self.line("2.1", "10", "400000.00", 5), self.line("2.2", "800", "3500.00")),
            "charges": tuple(AIU),
            "deductions": tuple(WITHHOLDINGS),
        }
        values.update(overrides)
        return ContractData(**values)

    def create(self, user: AppUser | None = None, **overrides: Any) -> Contract:
        return services.create_contract(self.world.ctx(user), self.project, self.data(**overrides))

    def test_create_a_draft_with_value_terms_and_audit(self) -> None:
        contract = self.create()
        self.assertEqual(contract.status, ContractStatus.DRAFT)
        self.assertEqual(contract.current_value, D("8224600.00"))
        self.assertEqual(contract.advance_amount, D("0.00"))
        lines = list(contract.lines.order_by("order"))
        self.assertEqual([(line.order, line.current_quantity) for line in lines],
                         [(1, D("10")), (2, D("800"))])  # fmt: skip
        self.assertEqual(list(lines[0].tasks.all()), [self.world.task(5)])
        self.assertEqual(contract.charges.count(), 4)
        self.assertEqual(contract.deductions.count(), 3)
        event = AuditEvent.objects.get(action=AuditAction.CONTRACT_CREATED)
        self.assertEqual(event.metadata["contract"], str(contract.pk))

    def test_validation_messages(self) -> None:
        cases = {
            "ítem": {"lines": (self.line("2", "1", "1.00"),)},
            "al menos una línea": {"lines": ()},
            "anticipo": {"advance_pct": D("101")},
            "fechas": {"ends_on": date(2026, 2, 1)},
            "cantidad": {"lines": (self.line("2.1", "0", "1.00"),)},
            "precio": {"lines": (self.line("2.1", "1", "0.001"),)},
            "Descuentos": {
                "deductions": (ChargeSpec(1, "Mal", D("1"), BASE_SUBTOTAL),),
            },
        }
        for fragment, overrides in cases.items():
            with self.subTest(fragment=fragment), self.assertRaises(ContractRejected) as refused:
                self.create(**overrides)
            self.assertIn(fragment, " ".join(refused.exception.errors))

    def test_number_is_unique_ignoring_case(self) -> None:
        self.create()
        with self.assertRaises(ContractRejected):
            self.create(number="sc-001")

    def test_an_overlap_needs_confirmation(self) -> None:
        make_contract(self.cw, number="SC-009", lines=(("2.1", "5", "1.00", (5,)),))
        with self.assertRaises(ContractRejected) as refused:
            self.create(lines=(self.line("2.1", "1", "1.00"),))
        self.assertEqual(refused.exception.errors, [])
        self.assertIn("SC-009", refused.exception.overlaps[0])
        # Otras tareas: no se traslapan.
        self.create(number="SC-002", lines=(self.line("2.1", "1", "1.00", 3),))
        # Confirmado: se guarda.
        self.create(number="SC-003", lines=(self.line("2.1", "1", "1.00"),), confirm_overlap=True)
        # Dentro del mismo contrato también se avisa.
        with self.assertRaises(ContractRejected) as twice:
            self.create(number="SC-004",
                        lines=(self.line("2.2", "1", "1.00"), self.line("2.2", "2", "1.00", 3)))  # fmt: skip
        self.assertIn("líneas 1 y 2", twice.exception.overlaps[0])

    def test_roles_and_own_drafts(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.create(self.cw.viewer)
        own = self.create(self.cw.contributor)
        other = self.create(number="SC-002", confirm_overlap=True)
        services.update_contract(
            self.world.ctx(self.cw.contributor), self.project, own.pk,
            self.data(purpose="Otro", confirm_overlap=True),
        )
        with self.assertRaises(PermissionDenied):
            services.update_contract(
                self.world.ctx(self.cw.contributor), self.project, other.pk,
                self.data(number="SC-002"),
            )  # fmt: skip
        with self.assertRaises(PermissionDenied):
            services.delete_contract(self.world.ctx(self.cw.contributor), self.project, other.pk)

    def test_project_scope(self) -> None:
        elsewhere = Project.objects.create(
            org=self.world.org, name="Otra obra", created_by=self.world.admin
        )
        scoped = make_user(self.world.org, username="limitado", role=Role.VIEWER)
        grant_costs(self.world, scoped, SectionRole.MANAGER, scope=AccessScope.PROJECTS,
                    projects=(elsewhere,))  # fmt: skip
        with self.assertRaises(PermissionDenied):
            self.create(scoped)

    def test_approval_fixes_the_advance_and_needs_someone_else(self) -> None:
        contract = self.create()
        with self.assertRaises(ContractRejected) as own:
            services.approve_contract(self.world.ctx(), self.project, contract.pk)
        self.assertIn("otra persona", own.exception.errors[0])
        approved = services.approve_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk
        )
        self.assertEqual(approved.status, ContractStatus.APPROVED)
        self.assertEqual(approved.advance_amount, D("2467380.00"))
        self.assertEqual(approved.approved_by, self.world.supervisor)
        with self.assertRaises(ContractRejected):
            services.update_contract(self.world.ctx(), self.project, contract.pk, self.data())
        with self.assertRaises(PermissionDenied):
            services.approve_contract(self.world.ctx(self.cw.contributor), self.project,
                                      self.create(number="SC-002", confirm_overlap=True).pk)  # fmt: skip

    def test_the_last_editor_does_not_approve(self) -> None:
        contract = self.create(self.cw.contributor)
        services.update_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk, self.data()
        )
        with self.assertRaises(ContractRejected):
            services.approve_contract(self.world.ctx(self.world.supervisor), self.project,
                                      contract.pk)  # fmt: skip
        services.approve_contract(self.world.ctx(), self.project, contract.pk)

    def test_a_rejection_keeps_the_draft_and_editing_clears_it(self) -> None:
        contract = self.create()
        with self.assertRaises(ContractRejected):
            services.reject_contract(self.world.ctx(self.world.supervisor), self.project,
                                     contract.pk, " ")  # fmt: skip
        rejected = services.reject_contract(
            self.world.ctx(self.world.supervisor), self.project, contract.pk, "Falta la póliza"
        )
        self.assertEqual((rejected.status, rejected.rejection_note), ("draft", "Falta la póliza"))
        edited = services.update_contract(self.world.ctx(), self.project, contract.pk, self.data())
        self.assertEqual((edited.rejection_note, edited.rejected_by_id), ("", None))
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.CONTRACT_UPDATED, metadata__resubmitted=True
            ).exists()
        )

    def test_only_a_draft_is_deleted(self) -> None:
        draft = self.create()
        services.delete_contract(self.world.ctx(), self.project, draft.pk)
        self.assertFalse(Contract.objects.filter(pk=draft.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_DELETED).exists())
        with self.assertRaises(ContractRejected):
            services.delete_contract(self.world.ctx(), self.project, make_contract(self.cw).pk)

    def test_void_only_without_approved_certificates(self) -> None:
        busy = make_contract(self.cw)
        make_certificate(self.cw, busy, quantities={"2.1": "1"})
        with self.assertRaises(ContractRejected):
            services.void_contract(self.world.ctx(), self.project, busy.pk, "Ya no va")
        idle = make_contract(self.cw, number="SC-002")
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            services.void_contract(self.world.ctx(self.world.supervisor), self.project, idle.pk,
                                   "Ya no va")  # fmt: skip
        with self.assertRaises(ContractRejected):
            services.void_contract(self.world.ctx(), self.project, idle.pk, "")
        voided = services.void_contract(self.world.ctx(), self.project, idle.pk, "Ya no va")
        self.assertEqual((voided.status, voided.close_note), ("voided", "Ya no va"))
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_VOIDED).exists())


@skip_without_db
class AmendmentServiceTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")

    def data(self, **overrides: Any) -> AmendmentData:
        values: dict[str, Any] = {
            "signed_on": date(2026, 5, 2),
            "reason": "Mayor cantidad de concreto y curado",
            "new_ends_on": date(2026, 10, 31),
            "increases": (IncreaseData(self.concrete.pk, D("2")),),
            "new_lines": (
                LineData(item(self.cw.budget, "2.4").pk, (), "m2", D("40"), D("3000.00")),
            ),
        }
        values.update(overrides)
        return AmendmentData(**values)

    def test_approval_applies_increases_new_lines_and_end_date(self) -> None:
        amendment = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk, self.data()
        )
        self.assertEqual(amendment.number, 1)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.current_value, D("8224600.00"))  # aún no cuenta
        services.approve_amendment(
            self.world.ctx(self.world.supervisor), self.project, self.contract.pk, amendment.pk
        )
        self.contract.refresh_from_db()
        self.concrete.refresh_from_db()
        self.assertEqual(self.concrete.current_quantity, D("12"))
        self.assertEqual(self.concrete.quantity, D("10"))
        self.assertEqual(self.contract.ends_on, date(2026, 10, 31))
        self.assertEqual(self.contract.current_value, D("9337340.00"))
        self.assertEqual(self.contract.advance_amount, D("2467380.00"))
        amendment.refresh_from_db()
        self.assertEqual((amendment.status, amendment.value_after),
                         (AmendmentStatus.APPROVED, D("9337340.00")))  # fmt: skip
        new_line = ContractLine.objects.get(contract=self.contract, item__code="2.4")
        self.assertEqual((new_line.order, new_line.amendment_id), (3, amendment.pk))
        event = AuditEvent.objects.get(action=AuditAction.AMENDMENT_APPROVED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_one_open_amendment_only_on_approved_contracts(self) -> None:
        services.create_amendment(self.world.ctx(), self.project, self.contract.pk, self.data())
        with self.assertRaises(ContractRejected):
            services.create_amendment(self.world.ctx(), self.project, self.contract.pk,
                                      self.data())  # fmt: skip
        draft = make_contract(self.cw, number="SC-002", status=ContractStatus.DRAFT)
        with self.assertRaises(ContractRejected):
            services.create_amendment(self.world.ctx(), self.project, draft.pk,
                                      self.data(increases=(), new_lines=()))  # fmt: skip

    def test_validation(self) -> None:
        other = contract_line(make_contract(self.cw, number="SC-002"), "2.1")
        for overrides in (
            {"increases": (), "new_lines": (), "new_ends_on": None},
            {"increases": (IncreaseData(self.concrete.pk, D("-1")),), "new_lines": ()},
            {"increases": (IncreaseData(other.pk, D("1")),), "new_lines": ()},
            {"reason": " "},
            {"signed_on": date(2026, 6, 1)},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ContractRejected):
                services.create_amendment(self.world.ctx(), self.project, self.contract.pk,
                                          self.data(**overrides))  # fmt: skip

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        amendment = services.create_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, self.data()
        )
        services.reject_amendment(self.world.ctx(self.world.supervisor), self.project,
                                  self.contract.pk, amendment.pk, "Sin soporte")  # fmt: skip
        with self.assertRaises(PermissionDenied):
            services.update_amendment(self.world.ctx(), self.project, self.contract.pk,
                                      amendment.pk, self.data())  # fmt: skip
        again = services.update_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, amendment.pk,
            self.data(reason="Con soporte"),
        )  # fmt: skip
        self.assertEqual((again.status, again.decision_note), ("draft", ""))
        services.delete_amendment(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, amendment.pk
        )
        self.assertFalse(ContractLine.objects.filter(contract=self.contract,
                                                     item__code="2.4").exists())  # fmt: skip
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_services.py`
Expected: ERROR (`cannot import name 'services' from 'suno.contracts'`).

- [ ] **Step 3: Códigos de auditoría**

En `server/suno/core/audit.py`, al final de `AuditAction`:

```python
    # Subcontratos y destajos (costos, pieza 6b). `metadata.contract` en todos: el historial
    # de la ficha del contrato los reúne.
    CONTRACT_CREATED = "contract.created"
    CONTRACT_UPDATED = "contract.updated"
    CONTRACT_DELETED = "contract.deleted"
    CONTRACT_APPROVED = "contract.approved"
    CONTRACT_REJECTED = "contract.rejected"
    CONTRACT_VOIDED = "contract.voided"
    CONTRACT_SETTLED = "contract.settled"
    CONTRACT_EXPORTED = "contract.exported"
    AMENDMENT_CREATED = "amendment.created"
    AMENDMENT_UPDATED = "amendment.updated"
    AMENDMENT_DELETED = "amendment.deleted"
    AMENDMENT_APPROVED = "amendment.approved"
    AMENDMENT_REJECTED = "amendment.rejected"
    CERTIFICATE_CREATED = "certificate.created"
    CERTIFICATE_UPDATED = "certificate.updated"
    CERTIFICATE_DELETED = "certificate.deleted"
    CERTIFICATE_APPROVED = "certificate.approved"
    CERTIFICATE_REJECTED = "certificate.rejected"
    CERTIFICATE_VOIDED = "certificate.voided"
    CERTIFICATE_EXPORTED = "certificate.exported"
```

- [ ] **Step 4: Los servicios**

`server/suno/contracts/services.py`:

```python
"""Contratos y otrosíes (costos, pieza 6b, spec 1, 2.2, 2.3 y 4).

- Todo se escribe en `transaction.atomic`, con `scoped_qs` y la auditoría en la misma
  transacción. Toda escritura sobre un contrato, sus otrosíes o sus actas bloquea antes la fila
  del contrato (`select_for_update`): así se serializan consecutivos, topes y periodos.
- Un contrato se edita solo en borrador; aprobado, cambia por otrosíes.
- Nadie aprueba ni rechaza lo que creó o editó por última vez.
- Permisos: los de la sección Costos (`core.policies`), sin políticas nuevas.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.http import Http404
from django.utils import timezone

from suno.budgets.totals import (
    HUNDRED,
    PCT_STEP,
    ChargeError,
    ChargeSpec,
    money,
    quantity,
    validate_charges,
)
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    AmendmentStatus,
    BudgetRowKind,
    Contract,
    ContractAmendment,
    ContractAmendmentLine,
    ContractCertificate,
    ContractCharge,
    ContractDeduction,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Project,
    Task,
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
from suno.costs.catalog import Catalog, assignable_tasks, load_catalog

from .calc import LineInput, contract_value, scaled, validate_deductions

MAX_LINES = 200
MAX_NUMBER = 30
MAX_PURPOSE = 300
MAX_CONTRACTOR = 200
MAX_UNIT = 30
MAX_NOTE = 500
FIRST_DAY = date(2000, 1, 1)
MAX_QUANTITY = Decimal("99999999999999.9999")
MAX_PRICE = Decimal("9999999999999999.99")
SELF_DECISION = "Nadie aprueba ni rechaza lo que creó o editó: pídeselo a otra persona."
APPROVED_IS_FINAL = "Un contrato aprobado solo cambia por otrosíes."


class ContractRejected(Exception):
    """No se puede guardar. `errors`: mensajes para el usuario. `overlaps`: traslapes que hay
    que confirmar (sin `errors`)."""

    def __init__(self, errors: Sequence[str], *, overlaps: Sequence[str] = ()) -> None:
        self.errors = list(errors)
        self.overlaps = list(overlaps)
        super().__init__((self.errors or self.overlaps or ["Traslape"])[0])


@dataclass(frozen=True)
class LineData:
    item_id: UUID
    #: Vacío: todas las tareas del ítem.
    task_ids: tuple[UUID, ...]
    unit: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class ContractData:
    kind: str
    number: str
    purpose: str
    contractor: str
    starts_on: date
    ends_on: date
    advance_pct: Decimal
    retention_pct: Decimal
    lines: tuple[LineData, ...]
    charges: tuple[ChargeSpec, ...] = ()
    deductions: tuple[ChargeSpec, ...] = ()
    confirm_overlap: bool = False


@dataclass(frozen=True)
class IncreaseData:
    line_id: UUID
    added_quantity: Decimal


@dataclass(frozen=True)
class AmendmentData:
    signed_on: date
    reason: str
    new_ends_on: date | None = None
    increases: tuple[IncreaseData, ...] = ()
    new_lines: tuple[LineData, ...] = ()
    confirm_overlap: bool = False


# ---------------------------------------------------------------------------
# Lecturas compartidas (también las usan `certificates` y `queries`)
# ---------------------------------------------------------------------------


def active_lines(lines: QuerySet[ContractLine]) -> QuerySet[ContractLine]:
    """Las líneas que cuentan: las del contrato y las de otrosíes aprobados."""
    return lines.filter(Q(amendment__isnull=True) | Q(amendment__status=AmendmentStatus.APPROVED))


def charge_specs(ctx: RequestContext, contract: Contract) -> list[ChargeSpec]:
    return [
        ChargeSpec(order, name, pct, base)
        for order, name, pct, base in scoped_qs(ctx, ContractCharge)
        .filter(contract=contract)
        .order_by("order")
        .values_list("order", "name", "pct", "base")
    ]


def deduction_specs(ctx: RequestContext, contract: Contract) -> list[ChargeSpec]:
    return [
        ChargeSpec(order, name, pct, base)
        for order, name, pct, base in scoped_qs(ctx, ContractDeduction)
        .filter(contract=contract)
        .order_by("order")
        .values_list("order", "name", "pct", "base")
    ]


def refresh_value(ctx: RequestContext, contract: Contract) -> Decimal:
    """Valor vigente: bruto directo de las líneas vigentes (cantidad vigente) más cargos."""
    rows = (
        active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
        .order_by("order")
        .values_list("pk", "order", "current_quantity", "unit_price")
    )
    return contract_value([LineInput(*row) for row in rows], charge_specs(ctx, contract)).total


def locked_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    contract = (
        scoped_qs(ctx, Contract).select_for_update().filter(pk=contract_id, project=project).first()
    )
    if contract is None:
        raise Http404
    return contract


def require_open(contract: Contract) -> None:
    """Solo un contrato aprobado admite otrosíes y actas."""
    if contract.status == ContractStatus.DRAFT:
        raise ContractRejected(["El contrato todavía no está aprobado."])
    if contract.status != ContractStatus.APPROVED:
        raise ContractRejected(["Un contrato liquidado o anulado no admite otrosíes ni actas."])



def require_editable(
    ctx: RequestContext, obj: Any, policy: Callable[[RequestContext, Any], bool]
) -> None:
    """Un otrosí o un acta abiertos; un rechazado, solo su autor."""
    if obj.status not in OPEN_STATES:
        raise ContractRejected(["Solo se edita o se borra un borrador o un rechazado."])
    if obj.status == AmendmentStatus.REJECTED and obj.created_by_id != ctx.user_id:
        raise PermissionDenied
    if not policy(ctx, obj):
        raise PermissionDenied


def require_decidable(ctx: RequestContext, obj: Any) -> None:
    if obj.status != AmendmentStatus.DRAFT:
        raise ContractRejected(["Solo se aprueba o se rechaza un borrador."])
    if ctx.user_id in (obj.created_by_id, obj.updated_by_id):
        raise ContractRejected([SELF_DECISION])


def clean_note(note: str, what: str) -> str:
    text = note.strip()
    if not text:
        raise ContractRejected([f"Escribe el motivo {what}."])
    if len(text) > MAX_NOTE:
        raise ContractRejected([f"El motivo pasa de {MAX_NOTE} caracteres."])
    return text


def contract_metadata(contract: Contract) -> dict[str, Any]:
    return {
        "contract": str(contract.pk),
        "project": str(contract.project_id),
        "number": contract.number,
        "kind": contract.kind,
        "value": str(contract.current_value),
        "status": contract.status,
    }


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------


def _pct_ok(value: Decimal) -> bool:
    return value.is_finite() and 0 <= value <= HUNDRED and value == value.quantize(PCT_STEP)


def line_errors(
    catalog: Catalog, tasks: Mapping[UUID, Task], lines: Sequence[LineData], *, first: int = 1
) -> list[str]:
    errors: list[str] = []
    for n, line in enumerate(lines, start=first):
        row = catalog.rows.get(line.item_id)
        if row is None or row.kind != BudgetRowKind.ITEM:
            errors.append(f"Línea {n}: elige un ítem de la versión vigente del presupuesto.")
        if any(task_id not in tasks for task_id in line.task_ids):
            errors.append(f"Línea {n}: las tareas deben ser tareas vigentes del proyecto.")
        if not 0 < line.quantity <= MAX_QUANTITY or quantity(line.quantity) != line.quantity:
            errors.append(f"Línea {n}: la cantidad es mayor que cero, con máximo 4 decimales.")
        if not 0 < line.unit_price <= MAX_PRICE or money(line.unit_price) != line.unit_price:
            errors.append(f"Línea {n}: el precio va en pesos y centavos y es mayor que cero.")
        if len(line.unit.strip()) > MAX_UNIT:
            errors.append(f"Línea {n}: la unidad pasa de {MAX_UNIT} caracteres.")
    return errors


def validate_contract(
    catalog: Catalog, tasks: Mapping[UUID, Task], data: ContractData
) -> list[str]:
    errors: list[str] = []
    if data.kind not in ContractKind.values:
        errors.append("Elige el tipo de contrato.")
    for value, label, limit in (
        (data.number, "El número", MAX_NUMBER),
        (data.purpose, "El objeto", MAX_PURPOSE),
        (data.contractor, "El contratista", MAX_CONTRACTOR),
    ):
        if not value.strip() or len(value.strip()) > limit:
            errors.append(f"{label} es obligatorio y tiene máximo {limit} caracteres.")
    if not FIRST_DAY <= data.starts_on <= data.ends_on:
        errors.append("Las fechas van desde 2000 y el fin no es anterior al inicio.")
    if not _pct_ok(data.advance_pct):
        errors.append("El anticipo es un porcentaje entre 0 y 100, con máximo 4 decimales.")
    if not _pct_ok(data.retention_pct):
        errors.append(
            "La retención de garantía es un porcentaje entre 0 y 100, con máximo 4 decimales."
        )
    if not data.lines:
        errors.append("Agrega al menos una línea.")
    elif len(data.lines) > MAX_LINES:
        errors.append(f"Máximo {MAX_LINES} líneas por contrato.")
    errors += line_errors(catalog, tasks, data.lines)
    try:
        validate_charges(data.charges)
    except ChargeError as exc:
        errors.append(f"Cargos: {exc}")
    try:
        validate_deductions(data.deductions, len(data.charges))
    except ChargeError as exc:
        errors.append(f"Descuentos: {exc}")
    return errors


def _crosses(mine: frozenset[UUID], theirs: frozenset[UUID]) -> bool:
    return not mine or not theirs or bool(mine & theirs)


def find_overlaps(
    ctx: RequestContext,
    project: Project,
    lines: Sequence[LineData],
    catalog: Catalog,
    *,
    exclude: Contract | None = None,
) -> list[str]:
    """Traslapes de `lines` entre sí y con las líneas vigentes de los contratos en borrador o
    aprobados del proyecto (sin `exclude`). Dos consultas."""
    others = active_lines(
        scoped_qs(ctx, ContractLine).filter(
            contract__project=project,
            contract__status__in=[ContractStatus.DRAFT, ContractStatus.APPROVED],
            item_id__in={line.item_id for line in lines},
        )
    )
    if exclude is not None:
        others = others.exclude(contract=exclude)
    existing = [
        (row.item_id, frozenset(link.task_id for link in row.task_links.all()), row.contract.number)
        for row in others.select_related("contract").prefetch_related("task_links")
    ]
    found: list[str] = []
    for n, line in enumerate(lines, start=1):
        mine = frozenset(line.task_ids)
        label = catalog.label_of(line.item_id, "Un ítem")
        for item_id, theirs, number in existing:
            if item_id == line.item_id and _crosses(mine, theirs):
                found.append(f"{label}: el contrato {number} ya lo cubre en las mismas tareas.")
        for m, other in enumerate(lines[: n - 1], start=1):
            if other.item_id == line.item_id and _crosses(mine, frozenset(other.task_ids)):
                found.append(f"{label}: las líneas {m} y {n} cubren las mismas tareas.")
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------


def _write_lines(
    ctx: RequestContext,
    contract: Contract,
    lines: Sequence[LineData],
    *,
    first_order: int = 1,
    amendment: ContractAmendment | None = None,
) -> None:
    created = scoped_qs(ctx, ContractLine).bulk_create(
        [
            ContractLine(
                org_id=ctx.org_id,
                contract=contract,
                order=first_order + n,
                item_id=line.item_id,
                unit=line.unit.strip(),
                quantity=line.quantity,
                current_quantity=line.quantity,
                unit_price=line.unit_price,
                amendment=amendment,
            )
            for n, line in enumerate(lines)
        ]
    )
    scoped_qs(ctx, ContractLineTask).bulk_create(
        [
            ContractLineTask(org_id=ctx.org_id, line=row, task_id=task_id)
            for row, line in zip(created, lines, strict=True)
            for task_id in dict.fromkeys(line.task_ids)
        ]
    )


def _write_terms(ctx: RequestContext, contract: Contract, data: ContractData) -> None:
    scoped_qs(ctx, ContractCharge).filter(contract=contract).delete()
    scoped_qs(ctx, ContractDeduction).filter(contract=contract).delete()
    scoped_qs(ctx, ContractCharge).bulk_create(
        [
            ContractCharge(
                org_id=ctx.org_id, contract=contract, order=c.order, name=c.name.strip(),
                pct=c.pct, base=c.base,
            )  # fmt: skip
            for c in data.charges
        ]
    )
    scoped_qs(ctx, ContractDeduction).bulk_create(
        [
            ContractDeduction(
                org_id=ctx.org_id, contract=contract, order=d.order, name=d.name.strip(),
                pct=d.pct, base=d.base,
            )  # fmt: skip
            for d in data.deductions
        ]
    )


def _contract_fields(data: ContractData) -> dict[str, Any]:
    return {
        "kind": data.kind,
        "number": data.number.strip(),
        "purpose": data.purpose.strip(),
        "contractor": data.contractor.strip(),
        "starts_on": data.starts_on,
        "ends_on": data.ends_on,
        "advance_pct": data.advance_pct,
        "retention_pct": data.retention_pct,
    }


def _checked_contract(
    ctx: RequestContext,
    project: Project,
    data: ContractData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    *,
    exclude: Contract | None = None,
) -> None:
    errors = validate_contract(catalog, tasks, data)
    if errors:
        raise ContractRejected(errors)
    if not data.confirm_overlap:
        overlaps = find_overlaps(ctx, project, data.lines, catalog, exclude=exclude)
        if overlaps:
            raise ContractRejected([], overlaps=overlaps)


def _number_taken(
    ctx: RequestContext, project: Project, number: str, exclude: UUID | None = None
) -> bool:
    found = scoped_qs(ctx, Contract).filter(project=project, number__iexact=number.strip())
    if exclude is not None:
        found = found.exclude(pk=exclude)
    return found.exists()


def _require_draft(
    ctx: RequestContext, contract: Contract, policy: Callable[[RequestContext, Any], bool]
) -> None:
    if contract.status != ContractStatus.DRAFT:
        raise ContractRejected([APPROVED_IS_FINAL])
    if not policy(ctx, contract):
        raise PermissionDenied


def create_contract(
    ctx: RequestContext,
    project: Project,
    data: ContractData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Contract:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    _checked_contract(ctx, project, data, catalog, tasks)
    with transaction.atomic():
        if _number_taken(ctx, project, data.number):
            raise ContractRejected([f"Ya hay un contrato {data.number.strip()} en el proyecto."])
        contract = scoped_qs(ctx, Contract).create(
            org_id=ctx.org_id,
            project=project,
            status=ContractStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **_contract_fields(data),
        )
        _write_lines(ctx, contract, data.lines)
        _write_terms(ctx, contract, data)
        contract.current_value = refresh_value(ctx, contract)
        contract.save(update_fields=["current_value"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_CREATED,
            target=f"contract:{contract.pk}",
            metadata=contract_metadata(contract),
        )
    return contract


def update_contract(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: ContractData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> Contract:
    """Guarda un borrador. Si tenía un rechazo, se limpia (eso es "reenviarlo")."""
    current = scoped_qs(ctx, Contract).filter(pk=contract_id, project=project).first()
    if current is None:
        raise Http404
    _require_draft(ctx, current, can_update_cost)
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    _checked_contract(ctx, project, data, catalog, tasks, exclude=current)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        _require_draft(ctx, contract, can_update_cost)
        if _number_taken(ctx, project, data.number, exclude=contract.pk):
            raise ContractRejected([f"Ya hay un contrato {data.number.strip()} en el proyecto."])
        for field, value in _contract_fields(data).items():
            setattr(contract, field, value)
        # Un borrador no tiene actas ni otrosíes: sus líneas se reemplazan enteras.
        scoped_qs(ctx, ContractLine).filter(contract=contract).delete()
        _write_lines(ctx, contract, data.lines)
        _write_terms(ctx, contract, data)
        resubmitted = contract.rejected_by_id is not None
        contract.current_value = refresh_value(ctx, contract)
        contract.rejected_by = None
        contract.rejected_at = None
        contract.rejection_note = ""
        contract.updated_by_id = ctx.user_id
        contract.save()
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_UPDATED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "resubmitted": resubmitted},
        )
    return contract


def delete_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        _require_draft(ctx, contract, can_delete_cost)
        metadata = {**contract_metadata(contract), "contractor": contract.contractor}
        scoped_qs(ctx, ContractLine).filter(contract=contract).delete()
        scoped_qs(ctx, ContractCharge).filter(contract=contract).delete()
        scoped_qs(ctx, ContractDeduction).filter(contract=contract).delete()
        target = f"contract:{contract.pk}"
        contract.delete()
        audit.record(ctx, action=AuditAction.CONTRACT_DELETED, target=target, metadata=metadata)


def approve_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    """Aprueba y fija el anticipo (valor vigente × %)."""
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.DRAFT:
            raise ContractRejected(["Solo se aprueba un contrato en borrador."])
        if ctx.user_id in (contract.created_by_id, contract.updated_by_id):
            raise ContractRejected([SELF_DECISION])
        if not scoped_qs(ctx, ContractLine).filter(contract=contract).exists():
            raise ContractRejected(["El contrato no tiene líneas."])
        contract.current_value = refresh_value(ctx, contract)
        contract.advance_amount = scaled(contract.current_value, contract.advance_pct, HUNDRED)
        contract.status = ContractStatus.APPROVED
        contract.approved_by_id = ctx.user_id
        contract.approved_at = timezone.now()
        contract.rejected_by = None
        contract.rejected_at = None
        contract.rejection_note = ""
        contract.save(
            update_fields=[
                "current_value", "advance_amount", "status", "approved_by", "approved_at",
                "rejected_by", "rejected_at", "rejection_note",
            ]
        )  # fmt: skip
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_APPROVED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "advance": str(contract.advance_amount)},
        )
    return contract


def reject_contract(
    ctx: RequestContext, project: Project, contract_id: UUID, note: str
) -> Contract:
    """Devuelve el borrador con el motivo (el contrato no tiene estado "rechazado")."""
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.DRAFT:
            raise ContractRejected(["Solo se rechaza un contrato en borrador."])
        if ctx.user_id in (contract.created_by_id, contract.updated_by_id):
            raise ContractRejected([SELF_DECISION])
        contract.rejected_by_id = ctx.user_id
        contract.rejected_at = timezone.now()
        contract.rejection_note = reason
        contract.save(update_fields=["rejected_by", "rejected_at", "rejection_note"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_REJECTED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "note": reason},
        )
    return contract


def void_contract(ctx: RequestContext, project: Project, contract_id: UUID, note: str) -> Contract:
    """Anula un contrato aprobado sin actas aprobadas ni otrosíes o actas abiertos."""
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if contract.status != ContractStatus.APPROVED:
            raise ContractRejected(["Solo se anula un contrato aprobado; un borrador se borra."])
        certificates = scoped_qs(ctx, ContractCertificate).filter(contract=contract)
        if certificates.filter(status="approved").exists():
            raise ContractRejected(
                ["El contrato tiene actas aprobadas: anúlalas primero o liquídalo."]
            )
        if (
            certificates.filter(status__in=OPEN_STATES).exists()
            or scoped_qs(ctx, ContractAmendment)
            .filter(contract=contract, status__in=OPEN_STATES)
            .exists()
        ):
            raise ContractRejected(["Borra o decide primero los otrosíes y las actas abiertos."])
        contract.status = ContractStatus.VOIDED
        contract.closed_by_id = ctx.user_id
        contract.closed_at = timezone.now()
        contract.close_note = reason
        contract.save(update_fields=["status", "closed_by", "closed_at", "close_note"])
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_VOIDED,
            target=f"contract:{contract.pk}",
            metadata={**contract_metadata(contract), "note": reason},
        )
    return contract


# ---------------------------------------------------------------------------
# Otrosíes
# ---------------------------------------------------------------------------


def _amendment_metadata(amendment: ContractAmendment) -> dict[str, Any]:
    return {
        "contract": str(amendment.contract_id),
        "project": str(amendment.project_id),
        "number": amendment.number,
        "status": amendment.status,
    }


def _amendment_errors(
    contract: Contract,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
    data: AmendmentData,
    lines: Mapping[UUID, ContractLine],
) -> list[str]:
    errors: list[str] = []
    if not FIRST_DAY <= data.signed_on <= timezone.localdate():
        errors.append("La fecha del otrosí no puede ser futura ni anterior a 2000.")
    if not data.reason.strip() or len(data.reason.strip()) > MAX_NOTE:
        errors.append(f"El motivo es obligatorio y tiene máximo {MAX_NOTE} caracteres.")
    if data.new_ends_on is not None and data.new_ends_on < contract.starts_on:
        errors.append("La nueva fecha de fin no puede ser anterior al inicio del contrato.")
    if not (data.increases or data.new_lines or data.new_ends_on):
        errors.append("Un otrosí cambia algo: cantidades, líneas nuevas o la fecha de fin.")
    seen: set[UUID] = set()
    for increase in data.increases:
        if increase.line_id not in lines or increase.line_id in seen:
            errors.append("Los aumentos son de líneas vigentes del contrato, una vez cada una.")
        seen.add(increase.line_id)
        added = increase.added_quantity
        if not 0 < added <= MAX_QUANTITY or quantity(added) != added:
            errors.append("Un aumento es una cantidad mayor que cero, con máximo 4 decimales.")
    errors += line_errors(catalog, tasks, data.new_lines, first=len(lines) + 1)
    if len(lines) + len(data.new_lines) > MAX_LINES:
        errors.append(f"Máximo {MAX_LINES} líneas por contrato.")
    return list(dict.fromkeys(errors))


def _apply_amendment_data(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    amendment: ContractAmendment | None,
    data: AmendmentData,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> ContractAmendment:
    """Valida y escribe el otrosí (nuevo si `amendment` es None) con el contrato bloqueado."""
    lines = {
        line.pk: line
        for line in active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
    }
    errors = _amendment_errors(contract, catalog, tasks, data, lines)
    if errors:
        raise ContractRejected(errors)
    if not data.confirm_overlap:
        overlaps = find_overlaps(ctx, project, data.new_lines, catalog)
        if overlaps:
            raise ContractRejected([], overlaps=overlaps)
    fields = {
        "signed_on": data.signed_on,
        "reason": data.reason.strip(),
        "new_ends_on": data.new_ends_on,
    }
    if amendment is None:
        top = scoped_qs(ctx, ContractAmendment).filter(contract=contract).aggregate(n=Max("number"))
        amendment = scoped_qs(ctx, ContractAmendment).create(
            org_id=ctx.org_id,
            contract=contract,
            project=project,
            number=(top["n"] or 0) + 1,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
            **fields,
        )
    else:
        for field, value in fields.items():
            setattr(amendment, field, value)
        amendment.status = AmendmentStatus.DRAFT
        amendment.decided_by = None
        amendment.decided_at = None
        amendment.decision_note = ""
        amendment.updated_by_id = ctx.user_id
        amendment.save()
        scoped_qs(ctx, ContractAmendmentLine).filter(amendment=amendment).delete()
        scoped_qs(ctx, ContractLine).filter(amendment=amendment).delete()
    scoped_qs(ctx, ContractAmendmentLine).bulk_create(
        [
            ContractAmendmentLine(
                org_id=ctx.org_id,
                amendment=amendment,
                line_id=increase.line_id,
                added_quantity=increase.added_quantity,
            )
            for increase in data.increases
        ]
    )
    top_order = scoped_qs(ctx, ContractLine).filter(contract=contract).aggregate(n=Max("order"))
    _write_lines(
        ctx, contract, data.new_lines, first_order=(top_order["n"] or 0) + 1, amendment=amendment
    )
    return amendment


def _locked_amendment(
    ctx: RequestContext, contract: Contract, amendment_id: UUID
) -> ContractAmendment:
    amendment = (
        scoped_qs(ctx, ContractAmendment)
        .select_for_update()
        .filter(pk=amendment_id, contract=contract)
        .first()
    )
    if amendment is None:
        raise Http404
    return amendment


def create_amendment(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: AmendmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> ContractAmendment:
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        require_open(contract)
        if (
            scoped_qs(ctx, ContractAmendment)
            .filter(contract=contract, status__in=OPEN_STATES)
            .exists()
        ):
            raise ContractRejected(["Ya hay un otrosí abierto en este contrato: termínalo primero."])
        amendment = _apply_amendment_data(ctx, project, contract, None, data, catalog, tasks)
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_CREATED,
            target=f"amendment:{amendment.pk}",
            metadata=_amendment_metadata(amendment),
        )
    return amendment


def update_amendment(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    amendment_id: UUID,
    data: AmendmentData,
    *,
    catalog: Catalog | None = None,
    tasks: Mapping[UUID, Task] | None = None,
) -> ContractAmendment:
    """Guarda un otrosí abierto; un rechazado (solo su autor) vuelve a borrador."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    tasks = tasks if tasks is not None else assignable_tasks(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        require_editable(ctx, amendment, can_update_cost)
        require_open(contract)
        resubmitted = amendment.status == AmendmentStatus.REJECTED
        amendment = _apply_amendment_data(ctx, project, contract, amendment, data, catalog, tasks)
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_UPDATED,
            target=f"amendment:{amendment.pk}",
            metadata={**_amendment_metadata(amendment), "resubmitted": resubmitted},
        )
    return amendment


def delete_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID
) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        require_editable(ctx, amendment, can_delete_cost)
        metadata = {**_amendment_metadata(amendment), "reason": amendment.reason}
        target = f"amendment:{amendment.pk}"
        scoped_qs(ctx, ContractAmendmentLine).filter(amendment=amendment).delete()
        scoped_qs(ctx, ContractLine).filter(amendment=amendment).delete()
        amendment.delete()
        audit.record(ctx, action=AuditAction.AMENDMENT_DELETED, target=target, metadata=metadata)


def approve_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID
) -> ContractAmendment:
    """Aplica el otrosí: aumentos, líneas nuevas (desde ya vigentes), fecha de fin y valor."""
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, amendment)
        require_open(contract)
        for line_id, added in scoped_qs(ctx, ContractAmendmentLine).filter(
            amendment=amendment
        ).values_list("line_id", "added_quantity"):
            line = scoped_qs(ctx, ContractLine).get(pk=line_id)
            line.current_quantity += added
            line.save(update_fields=["current_quantity"])
        amendment.status = AmendmentStatus.APPROVED
        amendment.decided_by_id = ctx.user_id
        amendment.decided_at = timezone.now()
        amendment.save(update_fields=["status", "decided_by", "decided_at"])
        before = contract.current_value
        if amendment.new_ends_on is not None:
            contract.ends_on = amendment.new_ends_on
        contract.current_value = refresh_value(ctx, contract)
        contract.save(update_fields=["ends_on", "current_value"])
        amendment.value_after = contract.current_value
        amendment.save(update_fields=["value_after"])
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_APPROVED,
            target=f"amendment:{amendment.pk}",
            metadata={
                **_amendment_metadata(amendment),
                "value_before": str(before),
                "value_after": str(contract.current_value),
            },
        )
    return amendment


def reject_amendment(
    ctx: RequestContext, project: Project, contract_id: UUID, amendment_id: UUID, note: str
) -> ContractAmendment:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        amendment = _locked_amendment(ctx, contract, amendment_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, amendment)
        amendment.status = AmendmentStatus.REJECTED
        amendment.decided_by_id = ctx.user_id
        amendment.decided_at = timezone.now()
        amendment.decision_note = reason
        amendment.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.AMENDMENT_REJECTED,
            target=f"amendment:{amendment.pk}",
            metadata={**_amendment_metadata(amendment), "note": reason},
        )
    return amendment
```

Notas:

- `require_editable` y `require_decidable` también los usa `certificates.py` (Task 5): por eso reciben `Any` y comparan con los valores de texto comunes (`"draft"`, `"rejected"`), que son los mismos en `AmendmentStatus` y `CertificateStatus`.
- En `test_validation_messages`, "precio" con `0.001` dispara "pesos y centavos"; "fechas" usa un fin anterior al inicio.
- `approve_amendment` suma el aumento con `+=` sobre la fila (el contrato está bloqueado, nadie más la escribe).

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts suno/core && ruff check suno/contracts suno/core && mypy suno/contracts suno/core --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts suno/core/tests/test_architecture.py
```

Expected: PASS (la prueba de arquitectura confirma que `contracts` no usa `Model.objects`).

- [ ] **Step 6: Commit**

```bash
git add server/suno/core/audit.py server/suno/contracts/services.py server/suno/contracts/tests/test_services.py
git commit -m "feat(contracts): contract and amendment services with approval and audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Actas en borrador: propuesta, ajuste, acta firmada y rechazo

**Files:**
- Create: `server/suno/contracts/certificates.py`
- Test: `server/suno/contracts/tests/test_certificates.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_certificates.py`:

```python
"""Actas en borrador (spec, 2.4, 2.5 y 3.1): propuesta, ajuste, periodo, firmado, rechazo."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from suno.contracts import certificates
from suno.contracts.certificates import CertificateData, CertificateLineData, default_period
from suno.contracts.services import ContractRejected
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    CertificateLine,
    CertificateStatus,
    ContractCertificate,
    ContractStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import (
    contract_line,
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 4, 15)
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@skip_without_db
class CertificateDraftTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 10))
        field_quantity(self.cw, 3, "2.2", "200", date(2026, 3, 12))

    def create(
        self,
        user: AppUser | None = None,
        *lines: CertificateLineData,
        period: tuple[date, date] = MARCH,
        **kwargs: Any,
    ) -> ContractCertificate:
        return certificates.create_certificate(
            self.world.ctx(user or self.cw.contributor),
            self.project,
            self.contract.pk,
            CertificateData(period[0], period[1], tuple(lines)),
            **kwargs,
        )

    def test_create_proposes_from_the_field_and_computes_totals(self) -> None:
        certificate = self.create()
        self.assertEqual((certificate.number, certificate.status), (1, CertificateStatus.DRAFT))
        self.assertEqual(certificate.gross, D("2781850.00"))
        self.assertEqual(certificate.amortization, D("834555.00"))
        self.assertEqual(certificate.net, D("1736707.00"))
        self.assertEqual(len(certificate.breakdown["charges"]), 4)
        lines = {
            row.line.item.code: (row.proposed_quantity, row.quantity)
            for row in CertificateLine.objects.filter(certificate=certificate).select_related(
                "line__item"
            )
        }
        self.assertEqual(lines, {"2.1": (D(4), D(4)), "2.2": (D(200), D(200))})
        event = AuditEvent.objects.get(action=AuditAction.CERTIFICATE_CREATED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_an_adjusted_quantity_needs_its_justification(self) -> None:
        with self.assertRaises(ContractRejected) as refused:
            self.create(None, CertificateLineData(self.concrete.pk, D("5")))
        self.assertIn("justificación", refused.exception.errors[0])
        certificate = self.create(
            None, CertificateLineData(self.concrete.pk, D("5"), "Medición topográfica")
        )
        row = CertificateLine.objects.get(certificate=certificate, line=self.concrete)
        self.assertEqual((row.proposed_quantity, row.quantity), (D(4), D(5)))
        self.assertEqual(row.justification, "Medición topográfica")

    def test_only_one_open_and_only_on_approved_contracts(self) -> None:
        self.create()
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 4, 1), date(2026, 4, 10)))
        for status in (ContractStatus.DRAFT, ContractStatus.SETTLED, ContractStatus.VOIDED):
            other = make_contract(self.cw, number=f"X-{status}", status=status,
                                  lines=(("2.4", "1", "1.00", ()),))  # fmt: skip
            with self.subTest(status=status), self.assertRaises(ContractRejected):
                certificates.create_certificate(
                    self.world.ctx(), self.project, other.pk, CertificateData(*MARCH)
                )

    def test_periods_do_not_cross_nor_end_in_the_future(self) -> None:
        make_certificate(self.cw, self.contract, start=date(2026, 3, 1), end=date(2026, 3, 15))
        with self.assertRaises(ContractRejected) as crossing:
            self.create(period=(date(2026, 3, 10), date(2026, 3, 31)))
        self.assertIn("acta 1", crossing.exception.errors[0])
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 3, 16), date(2026, 4, 16)))
        with self.assertRaises(ContractRejected):
            self.create(period=(date(2026, 3, 20), date(2026, 3, 16)))
        self.assertEqual(self.create(period=(date(2026, 3, 16), date(2026, 3, 31))).number, 2)

    def test_default_period(self) -> None:
        ctx = self.world.ctx()
        self.assertEqual(default_period(ctx, self.contract), (date(2026, 3, 1), TODAY))
        make_certificate(self.cw, self.contract, start=date(2026, 3, 1), end=date(2026, 3, 15))
        self.assertEqual(default_period(ctx, self.contract), (date(2026, 3, 16), TODAY))

    def test_the_signed_copy_is_not_an_edit_and_editing_removes_it(self) -> None:
        certificate = self.create()
        signed = certificates.attach_signed(
            self.world.ctx(), self.project, self.contract.pk, certificate.pk,
            pdf_upload("acta-1.pdf"), "acta-1.pdf",
        )  # fmt: skip
        self.assertIsNotNone(signed.attachment_id)
        self.assertEqual(signed.updated_by, self.cw.contributor)
        with self.assertRaises(ContractRejected):
            certificates.attach_signed(
                self.world.ctx(), self.project, self.contract.pk, certificate.pk,
                SimpleUploadedFile("nota.txt", b"hola mundo"), "nota.txt",
            )  # fmt: skip
        edited = certificates.update_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk,
            CertificateData(*MARCH),
        )  # fmt: skip
        self.assertIsNone(edited.attachment_id)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.CERTIFICATE_UPDATED, metadata__signed_removed=True
            ).exists()
        )

    def test_rejection_and_resubmission_by_the_author(self) -> None:
        certificate = self.create()
        rejected = certificates.reject_certificate(
            self.world.ctx(self.world.supervisor), self.project, self.contract.pk,
            certificate.pk, "Falta la medición del 2.2",
        )  # fmt: skip
        self.assertEqual(rejected.status, CertificateStatus.REJECTED)
        with self.assertRaises(ContractRejected):  # el firmado va a un borrador
            certificates.attach_signed(
                self.world.ctx(self.cw.contributor), self.project, self.contract.pk,
                certificate.pk, pdf_upload(), "acta.pdf",
            )  # fmt: skip
        with self.assertRaises(PermissionDenied):
            certificates.update_certificate(
                self.world.ctx(), self.project, self.contract.pk, certificate.pk,
                CertificateData(*MARCH),
            )  # fmt: skip
        again = certificates.update_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk,
            CertificateData(*MARCH),
        )  # fmt: skip
        self.assertEqual((again.status, again.decision_note), (CertificateStatus.DRAFT, ""))

    def test_deleting_the_draft_frees_its_number(self) -> None:
        certificate = self.create()
        certificates.delete_certificate(
            self.world.ctx(self.cw.contributor), self.project, self.contract.pk, certificate.pk
        )
        self.assertFalse(ContractCertificate.objects.filter(pk=certificate.pk).exists())
        self.assertEqual(self.create().number, 1)

    def test_roles(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.create(self.cw.viewer)
        with self.assertRaises(PermissionDenied):  # liquidar es del Gestor
            self.create(self.world.supervisor, settlement=True)
        self.assertTrue(self.create(self.world.admin, settlement=True).is_settlement)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_certificates.py`
Expected: ERROR (`cannot import name 'certificates' from 'suno.contracts'`).

- [ ] **Step 3: Implementar**

`server/suno/contracts/certificates.py` (la aprobación, el paso al libro y la anulación se agregan en la Task 6):

```python
"""Actas de avance y de liquidación (costos, pieza 6b, spec 2.4–2.7 y 3).

- Un acta se prepara en borrador con las cantidades propuestas desde campo (`proposal`), que se
  ajustan con justificación. Una sola abierta por contrato; el número se asigna al crearla.
- El acta firmada (escaneada) es obligatoria para aprobar. Subirla no es editar el acta (no
  cambia `updated_by`); editar el borrador la quita, porque ya no corresponde a lo impreso.
- Al aprobarla se escriben sus movimientos en el libro (6a): uno por línea con valor.
- Solo se anula la última aprobada, con reversos fechados hoy. La de liquidación cierra el
  contrato; anularla lo reabre.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Sum
from django.http import Http404
from django.utils import timezone

from suno.attachments import validation
from suno.attachments.storage import get_blob_store
from suno.attachments.web import Upload, UploadRejected, store_upload
from suno.budgets.totals import ZERO, item_total, quantity
from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractCertificate,
    ContractLine,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.policies import (
    can_create_costs,
    can_delete_cost,
    can_read_costs,
    can_reject_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import blob_registry, scoped_qs
from suno.costs.catalog import Catalog, load_catalog
from suno.costs.services import MAX_FILE_NAME

from .calc import Balances, CertificateTotals, LineInput, certificate_totals
from .proposal import Proposal, line_scopes, proposals
from .services import (
    FIRST_DAY,
    MAX_QUANTITY,
    ContractRejected,
    active_lines,
    charge_specs,
    clean_note,
    deduction_specs,
    locked_contract,
    require_decidable,
    require_editable,
    require_open,
)

MAX_JUSTIFICATION = 300


def quantity_text(value: Decimal) -> str:
    """Una cantidad para leer: `4.0000` → "4", `12.5000` → "12,5"."""
    return format(value.normalize(), "f").replace(".", ",")


@dataclass(frozen=True)
class CertificateLineData:
    line_id: UUID
    quantity: Decimal
    justification: str = ""


@dataclass(frozen=True)
class CertificateData:
    period_start: date
    period_end: date
    #: Por línea del contrato; una que falte va con su cantidad propuesta.
    lines: tuple[CertificateLineData, ...] = ()


@dataclass(frozen=True)
class DraftLine:
    line: ContractLine
    label: str
    proposal: Proposal
    quantity: Decimal
    justification: str
    amount: Decimal

    @property
    def differs(self) -> bool:
        return self.quantity != self.proposal.proposed

    @property
    def balance(self) -> Decimal:
        """Saldo de la línea antes de esta acta."""
        return self.line.current_quantity - self.proposal.paid

    @property
    def over_balance(self) -> bool:
        return self.quantity > self.balance


@dataclass(frozen=True)
class Draft:
    """Un acta calculada sin guardar: para el formulario ("Recalcular") y el servicio."""

    contract: Contract
    period_start: date
    period_end: date
    settlement: bool
    lines: tuple[DraftLine, ...]
    totals: CertificateTotals
    errors: tuple[str, ...]

    @property
    def over_balance(self) -> list[DraftLine]:
        return [line for line in self.lines if line.over_balance]


def default_period(
    ctx: RequestContext, contract: Contract, *, today: date | None = None
) -> tuple[date, date]:
    """Desde el día siguiente al fin de la última acta no anulada (o el inicio del contrato)
    hasta hoy."""
    today = today or timezone.localdate()
    last = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract)
        .exclude(status=CertificateStatus.VOIDED)
        .aggregate(end=Max("period_end"))["end"]
    )
    start = last + timedelta(days=1) if last else contract.starts_on
    return start, max(start, today)


def balances(ctx: RequestContext, contract: Contract) -> Balances:
    """Anticipo amortizado y retención de las actas aprobadas. Una consulta."""
    sums = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, status=CertificateStatus.APPROVED)
        .aggregate(
            amortized=Sum("amortization"),
            retained=Sum("retention"),
            returned=Sum("retention_returned"),
        )
    )
    return Balances(
        current_value=contract.current_value,
        advance_amount=contract.advance_amount,
        amortized=sums["amortized"] or ZERO,
        retained=sums["retained"] or ZERO,
        returned=sums["returned"] or ZERO,
    )


def period_conflicts(
    ctx: RequestContext,
    contract: Contract,
    start: date,
    end: date,
    *,
    exclude: UUID | None = None,
) -> list[str]:
    crossing = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, period_start__lte=end, period_end__gte=start)
        .exclude(status=CertificateStatus.VOIDED)
    )
    if exclude is not None:
        crossing = crossing.exclude(pk=exclude)
    return [
        f"El periodo se cruza con el acta {other.number} "
        f"({other.period_start:%d/%m/%Y} – {other.period_end:%d/%m/%Y})."
        for other in crossing.order_by("number")
    ]


def contract_lines(ctx: RequestContext, contract: Contract) -> list[ContractLine]:
    """Las líneas vigentes, en orden, con su ítem y sus tareas."""
    return list(
        active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract))
        .select_related("item")
        .prefetch_related("task_links")
        .order_by("order")
    )


def build_draft(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    data: CertificateData,
    *,
    settlement: bool,
    catalog: Catalog,
    exclude: UUID | None = None,
    today: date | None = None,
) -> Draft:
    today = today or timezone.localdate()
    lines = contract_lines(ctx, contract)
    found = proposals(
        ctx, project, line_scopes(lines), start=data.period_start, end=data.period_end
    )
    given = {entry.line_id: entry for entry in data.lines}
    errors: list[str] = []
    if not FIRST_DAY <= data.period_start <= data.period_end <= today:
        errors.append(
            "El periodo va desde 2000, «desde» no es posterior a «hasta» y no termina en el "
            "futuro."
        )
    else:
        errors += period_conflicts(
            ctx, contract, data.period_start, data.period_end, exclude=exclude
        )
    if set(given) - {line.pk for line in lines}:
        errors.append("Hay cantidades de líneas que no son de este contrato.")
    drafted: list[DraftLine] = []
    for line in lines:
        proposal = found[line.pk]
        entry = given.get(line.pk)
        qty = entry.quantity if entry is not None else proposal.proposed
        justification = entry.justification.strip() if entry is not None else ""
        label = catalog.label_of(line.item_id, line.item.code)
        if not 0 <= qty <= MAX_QUANTITY or quantity(qty) != qty:
            errors.append(f"{label}: la cantidad es cero o más, con máximo 4 decimales.")
            qty = proposal.proposed
        if qty != proposal.proposed and not justification:
            errors.append(
                f"{label}: la cantidad difiere de la propuesta "
                f"({quantity_text(proposal.proposed)}); escribe la justificación."
            )
        if len(justification) > MAX_JUSTIFICATION:
            errors.append(f"{label}: la justificación pasa de {MAX_JUSTIFICATION} caracteres.")
        drafted.append(
            DraftLine(
                line=line,
                label=label,
                proposal=proposal,
                quantity=qty,
                justification=justification,
                amount=item_total(qty, line.unit_price),
            )
        )
    totals = certificate_totals(
        [LineInput(d.line.pk, d.line.order, d.quantity, d.line.unit_price) for d in drafted],
        charge_specs(ctx, contract),
        deduction_specs(ctx, contract),
        retention_pct=contract.retention_pct,
        balances=balances(ctx, contract),
        settlement=settlement,
    )
    return Draft(
        contract=contract,
        period_start=data.period_start,
        period_end=data.period_end,
        settlement=settlement,
        lines=tuple(drafted),
        totals=totals,
        errors=tuple(dict.fromkeys(errors)),
    )


def certificate_metadata(certificate: ContractCertificate) -> dict[str, Any]:
    return {
        "contract": str(certificate.contract_id),
        "project": str(certificate.project_id),
        "certificate": str(certificate.pk),
        "number": certificate.number,
        "settlement": certificate.is_settlement,
        "gross": str(certificate.gross),
        "net": str(certificate.net),
        "status": certificate.status,
    }


def _locked_certificate(
    ctx: RequestContext, contract: Contract, certificate_id: UUID
) -> ContractCertificate:
    certificate = (
        scoped_qs(ctx, ContractCertificate)
        .select_for_update()
        .filter(pk=certificate_id, contract=contract)
        .first()
    )
    if certificate is None:
        raise Http404
    return certificate


def _store(certificate: ContractCertificate, draft: Draft) -> None:
    certificate.period_start = draft.period_start
    certificate.period_end = draft.period_end
    for field, value in draft.totals.stored().items():
        setattr(certificate, field, value)
    certificate.breakdown = draft.totals.breakdown()


def _write_lines(ctx: RequestContext, certificate: ContractCertificate, draft: Draft) -> None:
    scoped_qs(ctx, CertificateLine).filter(certificate=certificate).delete()
    scoped_qs(ctx, CertificateLine).bulk_create(
        [
            CertificateLine(
                org_id=ctx.org_id,
                certificate=certificate,
                line=line.line,
                proposed_quantity=line.proposal.proposed,
                quantity=line.quantity,
                justification=line.justification,
                amount=line.amount,
            )
            for line in draft.lines
        ]
    )


def create_certificate(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    data: CertificateData,
    *,
    settlement: bool = False,
    catalog: Catalog | None = None,
) -> ContractCertificate:
    """Un acta en borrador. La de liquidación la prepara el Gestor (`reverse`)."""
    if not can_create_costs(ctx, project) or (settlement and not can_reverse_costs(ctx, project)):
        raise PermissionDenied
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        require_open(contract)
        existing = scoped_qs(ctx, ContractCertificate).filter(contract=contract)
        if existing.filter(status__in=OPEN_STATES).exists():
            raise ContractRejected(["Ya hay un acta abierta en este contrato: termínala primero."])
        draft = build_draft(ctx, project, contract, data, settlement=settlement, catalog=catalog)
        if draft.errors:
            raise ContractRejected(list(draft.errors))
        certificate = ContractCertificate(
            org_id=ctx.org_id,
            contract=contract,
            project=project,
            number=(existing.aggregate(n=Max("number"))["n"] or 0) + 1,
            is_settlement=settlement,
            status=CertificateStatus.DRAFT,
            created_by_id=ctx.user_id,
            updated_by_id=ctx.user_id,
        )
        _store(certificate, draft)
        certificate.save()
        _write_lines(ctx, certificate, draft)
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_CREATED,
            target=f"certificate:{certificate.pk}",
            metadata=certificate_metadata(certificate),
        )
    return certificate


def update_certificate(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    certificate_id: UUID,
    data: CertificateData,
    *,
    catalog: Catalog | None = None,
) -> ContractCertificate:
    """Guarda un acta abierta; una rechazada (solo su autor) vuelve a borrador. Quita el acta
    firmada: ya no corresponde a lo impreso."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        require_editable(ctx, certificate, can_update_cost)
        require_open(contract)
        draft = build_draft(
            ctx, project, contract, data, settlement=certificate.is_settlement,
            catalog=catalog, exclude=certificate.pk,
        )  # fmt: skip
        if draft.errors:
            raise ContractRejected(list(draft.errors))
        resubmitted = certificate.status == CertificateStatus.REJECTED
        unsigned = certificate.attachment_id is not None
        _store(certificate, draft)
        certificate.status = CertificateStatus.DRAFT
        certificate.decided_by = None
        certificate.decided_at = None
        certificate.decision_note = ""
        certificate.attachment = None
        certificate.attachment_name = ""
        certificate.updated_by_id = ctx.user_id
        certificate.save()
        _write_lines(ctx, certificate, draft)
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_UPDATED,
            target=f"certificate:{certificate.pk}",
            metadata={
                **certificate_metadata(certificate),
                "resubmitted": resubmitted,
                "signed_removed": unsigned,
            },
        )
    return certificate


def delete_certificate(
    ctx: RequestContext, project: Project, contract_id: UUID, certificate_id: UUID
) -> None:
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        require_editable(ctx, certificate, can_delete_cost)
        metadata = certificate_metadata(certificate)
        target = f"certificate:{certificate.pk}"
        scoped_qs(ctx, CertificateLine).filter(certificate=certificate).delete()
        certificate.delete()
        audit.record(ctx, action=AuditAction.CERTIFICATE_DELETED, target=target, metadata=metadata)


def _require_signable(ctx: RequestContext, certificate: ContractCertificate) -> None:
    if certificate.status != CertificateStatus.DRAFT:
        raise ContractRejected(["El acta firmada se sube a un acta en borrador."])
    if not can_update_cost(ctx, certificate):
        raise PermissionDenied


def attach_signed(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    certificate_id: UUID,
    upload: Upload,
    name: str,
) -> ContractCertificate:
    """Sube el acta firmada (PDF o imagen). No es una edición: `updated_by` no cambia. El
    archivo se sube antes del bloqueo; permisos y estado se miran antes y bajo el bloqueo."""
    current = (
        scoped_qs(ctx, ContractCertificate)
        .filter(pk=certificate_id, contract_id=contract_id, project=project)
        .first()
    )
    if current is None:
        raise Http404
    _require_signable(ctx, current)
    try:
        blob = store_upload(upload)
    except UploadRejected as exc:
        raise ContractRejected([str(exc)]) from exc
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        _require_signable(ctx, certificate)
        certificate.attachment = blob
        certificate.attachment_name = name[:MAX_FILE_NAME]
        certificate.save(update_fields=["attachment", "attachment_name"])
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_UPDATED,
            target=f"certificate:{certificate.pk}",
            metadata={**certificate_metadata(certificate), "signed": True},
        )
    return certificate


def reject_certificate(
    ctx: RequestContext, project: Project, contract_id: UUID, certificate_id: UUID, note: str
) -> ContractCertificate:
    reason = clean_note(note, "del rechazo")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        if not can_reject_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, certificate)
        certificate.status = CertificateStatus.REJECTED
        certificate.decided_by_id = ctx.user_id
        certificate.decided_at = timezone.now()
        certificate.decision_note = reason
        certificate.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_REJECTED,
            target=f"certificate:{certificate.pk}",
            metadata={**certificate_metadata(certificate), "note": reason},
        )
    return certificate


def signed_url(ctx: RequestContext, project: Project, certificate: ContractCertificate) -> str:
    """URL prefirmada del acta firmada, **después** de comprobar que se leen los costos del
    proyecto (el `Blob` no tiene organización), como `costs.services.support_url`."""
    if not can_read_costs(ctx, project) or certificate.project_id != project.pk:
        raise Http404
    blob = (
        blob_registry().filter(pk=certificate.attachment_id).first()
        if certificate.attachment_id
        else None
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

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts && ruff check suno/contracts && mypy suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/suno/contracts/certificates.py server/suno/contracts/tests/test_certificates.py
git commit -m "feat(contracts): certificate drafts proposed from the field, signed copy and rejection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Aprobar, anular y liquidar actas; paso al libro

**Files:**
- Modify: `server/suno/contracts/certificates.py` (`approve_certificate`, `post_to_ledger`, `void_certificate`)
- Modify: `server/suno/costs/services.py` (`reverse` rechaza movimientos de acta)
- Test: `server/suno/contracts/tests/test_ledger_posting.py`, `server/suno/costs/tests/test_services.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_ledger_posting.py`:

```python
"""Aprobación de actas y paso al libro (spec, 3.2, 2.6 y 2.7): movimientos, topes, anulación,
liquidación y el control."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from suno.budgets.totals import BASE_DIRECT, ChargeSpec
from suno.contracts import certificates, services
from suno.contracts.certificates import CertificateData, CertificateLineData
from suno.contracts.services import AmendmentData, ContractRejected, IncreaseData
from suno.control.earned_value import compute
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.audit import AuditAction
from suno.core.models import (
    AppUser,
    AuditEvent,
    CertificateStatus,
    ContractCertificate,
    ContractKind,
    ContractStatus,
    CostDocumentType,
    CostEntry,
    CostKind,
    CostScope,
    CostStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs import services as cost_services
from suno.costs.services import CostRejected
from suno.costs.tests.factories import item, pdf_upload

from .factories import contract_line, contract_world, field_quantity, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 5, 15)
MARCH = (date(2026, 3, 1), date(2026, 3, 31))
APRIL = (date(2026, 4, 1), date(2026, 4, 30))


@skip_without_db
class CertificateApprovalTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 10))
        field_quantity(self.cw, 3, "2.2", "200", date(2026, 3, 12))

    def prepared(
        self,
        period: tuple[date, date] = MARCH,
        *lines: CertificateLineData,
        user: AppUser | None = None,
        settlement: bool = False,
    ) -> ContractCertificate:
        """Un acta preparada (por el Colaborador, salvo `user`) y con el firmado subido."""
        ctx = self.world.ctx(user or self.cw.contributor)
        certificate = certificates.create_certificate(
            ctx, self.project, self.contract.pk, CertificateData(*period, tuple(lines)),
            settlement=settlement,
        )  # fmt: skip
        return certificates.attach_signed(
            ctx, self.project, self.contract.pk, certificate.pk, pdf_upload("acta.pdf"), "acta.pdf"
        )

    def approve(self, certificate: ContractCertificate, user: AppUser | None = None) -> ContractCertificate:
        return certificates.approve_certificate(
            self.world.ctx(user or self.world.supervisor), self.project, self.contract.pk,
            certificate.pk,
        )  # fmt: skip

    def test_approval_posts_one_entry_per_line_that_adds_up_to_the_gross(self) -> None:
        certificate = self.approve(self.prepared())
        self.assertEqual(certificate.status, CertificateStatus.APPROVED)
        entries = {
            entry.items.get().code: entry
            for entry in CostEntry.objects.filter(certificate=certificate)
        }
        self.assertEqual({code: e.amount for code, e in entries.items()},
                         {"2.1": D("1935200.00"), "2.2": D("846650.00")})  # fmt: skip
        self.assertEqual(sum(e.amount for e in entries.values()), certificate.gross)
        concrete = entries["2.1"]
        self.assertEqual(concrete.kind, CostKind.SUBCONTRACT)
        self.assertEqual(concrete.incurred_on, date(2026, 3, 31))
        self.assertEqual((concrete.scope, concrete.status), (CostScope.ITEM, CostStatus.APPROVED))
        self.assertEqual(concrete.chapter_id, item(self.cw.budget, "2").pk)
        self.assertEqual(concrete.task, self.world.task(5))
        self.assertIsNone(entries["2.2"].task_id)  # cubre todas las tareas
        self.assertEqual(concrete.document_type, CostDocumentType.CERTIFICATE)
        self.assertEqual(concrete.document_number, "SC-001 · Acta 1")
        self.assertEqual(concrete.description, "Acta 1 · Contrato SC-001 · 2.1 Concreto zapatas")
        self.assertEqual(concrete.third_party, "Estructuras del Norte SAS")
        self.assertEqual((concrete.created_by, concrete.decided_by),
                         (self.cw.contributor, self.world.supervisor))  # fmt: skip
        self.assertEqual(concrete.attachment_id, certificate.attachment_id)
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CERTIFICATE_APPROVED).exists())

    def test_self_approval_missing_copy_and_roles(self) -> None:
        own = certificates.create_certificate(
            self.world.ctx(), self.project, self.contract.pk, CertificateData(*MARCH)
        )
        with self.assertRaises(ContractRejected):  # sin el firmado
            self.approve(own)
        certificates.attach_signed(self.world.ctx(), self.project, self.contract.pk, own.pk,
                                   pdf_upload(), "acta.pdf")  # fmt: skip
        with self.assertRaises(ContractRejected):  # lo preparó el administrador
            self.approve(own, self.world.admin)
        with self.assertRaises(PermissionDenied):
            self.approve(own, self.cw.contributor)
        self.assertEqual(self.approve(own).status, CertificateStatus.APPROVED)

    def test_the_cap_is_the_current_quantity(self) -> None:
        certificate = self.prepared(
            MARCH, CertificateLineData(self.concrete.pk, D("11"), "Sobreejecución medida")
        )
        with self.assertRaises(ContractRejected) as refused:
            self.approve(certificate)
        self.assertIn("otrosí", refused.exception.errors[0])
        amendment = services.create_amendment(
            self.world.ctx(), self.project, self.contract.pk,
            AmendmentData(date(2026, 5, 2), "Mayor cantidad",
                          increases=(IncreaseData(self.concrete.pk, D("2")),)),
        )  # fmt: skip
        services.approve_amendment(self.world.ctx(self.world.supervisor), self.project,
                                   self.contract.pk, amendment.pk)  # fmt: skip
        self.assertEqual(self.approve(certificate).status, CertificateStatus.APPROVED)

    def test_piecework_goes_to_labor_and_the_rounding_adds_up(self) -> None:
        piecework = make_contract(
            self.cw,
            number="DT-001",
            kind=ContractKind.PIECEWORK,
            lines=(("1.1", "1", "100.00", ()), ("1.2", "1", "100.00", ()),
                   ("2.4", "1", "100.00", ())),
            charges=[ChargeSpec(1, "Cargo", D("33.3333"), BASE_DIRECT)],
            deductions=[],
            advance_pct="0",
            retention_pct="0",
        )  # fmt: skip
        ctx = self.world.ctx(self.cw.contributor)
        lines = tuple(
            CertificateLineData(line.pk, D("1"), "Medido en obra")
            for line in piecework.lines.order_by("order")
        )
        draft = certificates.create_certificate(
            ctx, self.project, piecework.pk, CertificateData(*MARCH, lines)
        )
        certificates.attach_signed(ctx, self.project, piecework.pk, draft.pk, pdf_upload(),
                                   "acta.pdf")  # fmt: skip
        approved = certificates.approve_certificate(
            self.world.ctx(self.world.supervisor), self.project, piecework.pk, draft.pk
        )
        entries = {e.items.get().code: e for e in CostEntry.objects.filter(certificate=approved)}
        self.assertEqual({code: e.amount for code, e in entries.items()},
                         {"1.1": D("133.34"), "1.2": D("133.33"), "2.4": D("133.33")})  # fmt: skip
        self.assertEqual({e.kind for e in entries.values()}, {CostKind.LABOR})

    def test_only_the_last_one_is_voided_with_reversals_dated_today(self) -> None:
        first = self.approve(self.prepared())
        second = self.approve(
            self.prepared(APRIL, CertificateLineData(self.concrete.pk, D("2"), "Abril"))
        )
        with self.assertRaises(ContractRejected):
            certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk,
                                          first.pk, "Error")  # fmt: skip
        with self.assertRaises(PermissionDenied):  # el Revisor no anula
            certificates.void_certificate(self.world.ctx(self.world.supervisor), self.project,
                                          self.contract.pk, second.pk, "Error")  # fmt: skip
        voided = certificates.void_certificate(
            self.world.ctx(), self.project, self.contract.pk, second.pk, "Error de medición"
        )
        self.assertEqual((voided.status, voided.void_note), ("voided", "Error de medición"))
        reversals = CostEntry.objects.filter(certificate=second, reverses__isnull=False)
        self.assertEqual(reversals.count(), 1)
        self.assertEqual({r.incurred_on for r in reversals}, {TODAY})
        self.assertEqual(sum(e.amount for e in CostEntry.objects.filter(certificate=second)), 0)
        certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk, first.pk,
                                      "Se rehace")  # fmt: skip
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CERTIFICATE_VOIDED).exists())

    def test_settlement_amortizes_returns_and_closes_the_contract(self) -> None:
        self.approve(self.prepared())
        settlement = self.prepared(
            (date(2026, 4, 1), date(2026, 4, 15)), user=self.world.admin, settlement=True
        )
        self.assertEqual(settlement.amortization, D("1632825.00"))
        self.assertEqual(settlement.retention_returned, D("139092.50"))
        self.assertEqual(settlement.net, D("-1493732.50"))
        approved = self.approve(settlement)
        self.assertFalse(CostEntry.objects.filter(certificate=approved).exists())  # bruto 0
        self.contract.refresh_from_db()
        self.assertEqual((self.contract.status, self.contract.closed_by),
                         (ContractStatus.SETTLED, self.world.supervisor))  # fmt: skip
        self.assertTrue(AuditEvent.objects.filter(action=AuditAction.CONTRACT_SETTLED).exists())
        with self.assertRaises(ContractRejected):
            self.prepared(APRIL)
        certificates.void_certificate(self.world.ctx(), self.project, self.contract.pk,
                                      approved.pk, "Faltaba una cantidad")  # fmt: skip
        self.contract.refresh_from_db()
        self.assertEqual((self.contract.status, self.contract.closed_by_id),
                         (ContractStatus.APPROVED, None))  # fmt: skip

    def test_the_ledger_refuses_to_reverse_a_certificate_entry(self) -> None:
        certificate = self.approve(self.prepared())
        entry = CostEntry.objects.filter(certificate=certificate).first()
        assert entry is not None
        with self.assertRaises(CostRejected) as refused:
            cost_services.reverse(self.world.ctx(), self.project, entry.pk, "x")
        self.assertIn("acta", refused.exception.errors[0])

    def test_the_control_counts_the_certificate(self) -> None:
        certificate = self.approve(self.prepared())
        ctx = self.world.ctx()
        result = compute(load_control_input(ctx, self.project, cut=TODAY, kind=MONTH, costs=True))
        assert result.cost is not None
        self.assertEqual(result.cost.ac_direct, certificate.gross)
```

En `server/suno/costs/tests/test_services.py`, dentro de `CostServiceTests`, al final:

```python
    def test_entries_of_a_certificate_are_not_reversed_from_the_ledger(self) -> None:
        from suno.contracts.tests.factories import contract_world, make_certificate, make_contract

        cw = contract_world("Con contratos")
        certificate = make_certificate(cw, make_contract(cw), quantities={"2.1": "1"})
        cost = entry(cw.world, status=CostStatus.APPROVED)
        CostEntry.objects.filter(pk=cost.pk).update(certificate=certificate)
        with self.assertRaises(CostRejected):
            services.reverse(cw.world.ctx(), cw.world.project, cost.pk, "No va")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_ledger_posting.py suno/costs/tests/test_services.py`
Expected: ERROR/FAIL (`module 'suno.contracts.certificates' has no attribute 'approve_certificate'`; el reverso del libro se hace).

- [ ] **Step 3: El libro no reversa movimientos de acta**

En `server/suno/costs/services.py`, junto a `APPROVED_IS_FINAL`:

```python
FROM_CERTIFICATE = (
    "Este costo viene de un acta de contrato: se anula el acta desde la pestaña Contratos."
)
```

y en `reverse`, después de la comprobación `if original.status != CostStatus.APPROVED or original.reverses_id is not None:`:

```python
        if original.certificate_id is not None:
            raise CostRejected([FROM_CERTIFICATE])
```

- [ ] **Step 4: Aprobación, paso al libro y anulación**

En `server/suno/contracts/certificates.py`, agregar a los imports `from collections.abc import Sequence`, `CostDocumentType`, `CostEntry`, `CostEntryItem`, `CostKind`, `CostScope`, `CostStatus`, `ContractKind`, `ContractStatus` (de `suno.core.models`), `can_approve_costs` (de `suno.core.policies`), `MAX_DESCRIPTION`, `MAX_DOCUMENT_NUMBER` y `MAX_THIRD_PARTY` (de `suno.costs.services`), `ledger_split` (de `.calc`) y `paid_by_line` (de `.proposal`); y al final del archivo:

```python
# ---------------------------------------------------------------------------
# Aprobación, paso al libro y anulación
# ---------------------------------------------------------------------------


def post_to_ledger(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    certificate: ContractCertificate,
    lines: Sequence[ContractLine],
    totals: CertificateTotals,
    catalog: Catalog,
) -> list[CostEntry]:
    """Un movimiento aprobado por línea con valor (spec 3.2). Registró y modificó: quien
    preparó y editó el acta; aprobó: quien aprueba; soporte: el acta firmada."""
    kind = CostKind.LABOR if contract.kind == ContractKind.PIECEWORK else CostKind.SUBCONTRACT
    by_id = {line.pk: line for line in lines}
    now = timezone.now()
    entries: list[CostEntry] = []
    links: list[CostEntryItem] = []
    for line_id, value in ledger_split(totals.lines, totals.charges_total).items():
        line = by_id[line_id]
        row = catalog.rows.get(line.item_id)
        task_ids = [link.task_id for link in line.task_links.all()]
        label = catalog.label_of(line.item_id, line.item.code)
        entry = CostEntry(
            org_id=ctx.org_id,
            project=project,
            incurred_on=certificate.period_end,
            kind=kind,
            description=(
                f"Acta {certificate.number} · Contrato {contract.number} · {label}"
            )[:MAX_DESCRIPTION],
            amount=value,
            third_party=contract.contractor[:MAX_THIRD_PARTY],
            document_type=CostDocumentType.CERTIFICATE,
            document_number=f"{contract.number} · Acta {certificate.number}"[
                :MAX_DOCUMENT_NUMBER
            ],
            scope=CostScope.ITEM,
            chapter_id=row.parent_id if row is not None else None,
            task_id=task_ids[0] if len(task_ids) == 1 else None,
            status=CostStatus.APPROVED,
            created_by_id=certificate.created_by_id,
            updated_by_id=certificate.updated_by_id,
            decided_by_id=ctx.user_id,
            decided_at=now,
            attachment_id=certificate.attachment_id,
            attachment_name=certificate.attachment_name,
            certificate=certificate,
        )
        entries.append(entry)
        links.append(CostEntryItem(org_id=ctx.org_id, entry=entry, item_id=line.item_id))
    scoped_qs(ctx, CostEntry).bulk_create(entries)
    scoped_qs(ctx, CostEntryItem).bulk_create(links)
    return entries


def approve_certificate(
    ctx: RequestContext,
    project: Project,
    contract_id: UUID,
    certificate_id: UUID,
    *,
    catalog: Catalog | None = None,
) -> ContractCertificate:
    """Aprueba con el contrato bloqueado: comprueba el firmado, el periodo y el tope por línea,
    recalcula los totales con los saldos de hoy, escribe el libro y, si es la liquidación,
    cierra el contrato. Las cantidades y las propuestas son las guardadas en el borrador."""
    catalog = catalog if catalog is not None else load_catalog(ctx, project)
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        if not can_approve_costs(ctx, project):
            raise PermissionDenied
        require_decidable(ctx, certificate)
        require_open(contract)
        if certificate.attachment_id is None:
            raise ContractRejected(["Sube el acta firmada antes de aprobar."])
        lines = contract_lines(ctx, contract)
        stored = {
            row.line_id: row
            for row in scoped_qs(ctx, CertificateLine).filter(certificate=certificate)
        }
        if set(stored) != {line.pk for line in lines}:
            raise ContractRejected(
                ["El contrato cambió desde que se preparó el acta: edítala para recalcularla."]
            )
        errors = period_conflicts(
            ctx, contract, certificate.period_start, certificate.period_end,
            exclude=certificate.pk,
        )  # fmt: skip
        paid = paid_by_line(ctx, stored)
        for line in lines:
            total = paid.get(line.pk, ZERO) + stored[line.pk].quantity
            if total > line.current_quantity:
                errors.append(
                    f"{catalog.label_of(line.item_id, line.item.code)}: con esta acta lo pagado "
                    f"({quantity_text(total)}) pasa lo contratado vigente "
                    f"({quantity_text(line.current_quantity)}). Tramita un otrosí o ajusta el "
                    "acta."
                )
        if errors:
            raise ContractRejected(errors)
        totals = certificate_totals(
            [
                LineInput(line.pk, line.order, stored[line.pk].quantity, line.unit_price)
                for line in lines
            ],
            charge_specs(ctx, contract),
            deduction_specs(ctx, contract),
            retention_pct=contract.retention_pct,
            balances=balances(ctx, contract),
            settlement=certificate.is_settlement,
        )
        for field, value in totals.stored().items():
            setattr(certificate, field, value)
        certificate.breakdown = totals.breakdown()
        certificate.status = CertificateStatus.APPROVED
        certificate.decided_by_id = ctx.user_id
        certificate.decided_at = timezone.now()
        certificate.save()
        entries = post_to_ledger(ctx, project, contract, certificate, lines, totals, catalog)
        if certificate.is_settlement:
            contract.status = ContractStatus.SETTLED
            contract.closed_by_id = ctx.user_id
            contract.closed_at = timezone.now()
            contract.save(update_fields=["status", "closed_by", "closed_at"])
            audit.record(
                ctx,
                action=AuditAction.CONTRACT_SETTLED,
                target=f"contract:{contract.pk}",
                metadata={
                    "contract": str(contract.pk),
                    "project": str(project.pk),
                    "certificate": str(certificate.pk),
                    "net": str(certificate.net),
                },
            )
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_APPROVED,
            target=f"certificate:{certificate.pk}",
            metadata={**certificate_metadata(certificate), "entries": len(entries)},
        )
    return certificate


def void_certificate(
    ctx: RequestContext, project: Project, contract_id: UUID, certificate_id: UUID, note: str
) -> ContractCertificate:
    """Anula la última acta aprobada: reversa sus movimientos con fecha de hoy y el motivo. Si
    era la liquidación, el contrato vuelve a aprobado."""
    reason = clean_note(note, "de la anulación")
    with transaction.atomic():
        contract = locked_contract(ctx, project, contract_id)
        certificate = _locked_certificate(ctx, contract, certificate_id)
        if not can_reverse_costs(ctx, project):
            raise PermissionDenied
        if certificate.status != CertificateStatus.APPROVED:
            raise ContractRejected(["Solo se anula un acta aprobada."])
        last = (
            scoped_qs(ctx, ContractCertificate)
            .filter(contract=contract, status=CertificateStatus.APPROVED)
            .order_by("-number")
            .first()
        )
        if last is None or last.pk != certificate.pk:
            raise ContractRejected(
                [f"Solo se anula la última acta aprobada (la {last.number if last else '—'})."]
            )
        today, now = timezone.localdate(), timezone.now()
        originals = list(
            scoped_qs(ctx, CostEntry)
            .filter(certificate=certificate, reverses__isnull=True)
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
                decision_note=reason,
                reverses=original,
                certificate=certificate,
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
        certificate.status = CertificateStatus.VOIDED
        certificate.voided_by_id = ctx.user_id
        certificate.voided_at = now
        certificate.void_note = reason
        certificate.save(update_fields=["status", "voided_by", "voided_at", "void_note"])
        if certificate.is_settlement:
            contract.status = ContractStatus.APPROVED
            contract.closed_by = None
            contract.closed_at = None
            contract.save(update_fields=["status", "closed_by", "closed_at"])
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_VOIDED,
            target=f"certificate:{certificate.pk}",
            metadata={
                **certificate_metadata(certificate),
                "note": reason,
                "reversals": len(reversals),
            },
        )
    return certificate
```

Notas:

- La restricción `cost_entry_approval_ok` de la 6a se cumple sola: quien aprueba no es quien preparó ni quien editó el acta (`require_decidable`), y el soporte es el acta firmada. Los reversos no la piden.
- En la prueba, `assert entry is not None` y `assert result.cost is not None` son de pruebas (bandit solo mira producción).

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts suno/costs && ruff check suno/contracts suno/costs && mypy suno/contracts suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts suno/costs suno/control
```

Expected: PASS (las del libro y el control de la 6a no cambian).

- [ ] **Step 6: Commit**

```bash
git add server/suno/contracts/certificates.py server/suno/contracts/tests/test_ledger_posting.py server/suno/costs/services.py server/suno/costs/tests/test_services.py
git commit -m "feat(contracts): approve, void and settle certificates with ledger entries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Consultas de la pestaña: lista con saldos, ficha, avisos, comprometido y bandeja

**Files:**
- Create: `server/suno/contracts/queries.py`
- Test: `server/suno/contracts/tests/test_queries.py`

Todas las lecturas de la pestaña, con número fijo de consultas (spec 5, "Rendimiento"): la lista son 2, la ficha 8 (más las 2 del catálogo, que la vista carga una vez), el documento de un acta 2.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_queries.py`:

```python
"""Consultas de la pestaña "Contratos" (spec, 2.1, 2.3, 3.3 y 4)."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from suno.contracts import queries
from suno.contracts.queries import ListFilters
from suno.core.audit import AuditAction
from suno.core.models import (
    AmendmentStatus,
    CertificateStatus,
    ContractAmendment,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractStatus,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog
from suno.costs.tests.factories import item

from .factories import (
    contract_line,
    contract_world,
    field_quantity,
    make_certificate,
    make_contract,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
TODAY = date(2026, 9, 15)


@skip_without_db
class ContractQueryTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project = self.world.project
        self.ctx = self.world.ctx()
        self.contract = make_contract(self.cw)  # 8.224.600, anticipo 2.467.380, fin 31/08/2026
        make_certificate(
            self.cw, self.contract, quantities={"2.1": "4", "2.2": "200"},
            totals={"gross": "2781850.00", "amortization": "834555.00",
                    "retention": "139092.50"},
        )  # fmt: skip
        make_certificate(
            self.cw, self.contract, number=2, start=date(2026, 4, 1), end=date(2026, 4, 30),
            quantities={"2.1": "1"}, status=CertificateStatus.VOIDED,
            totals={"gross": "999.00"},
        )  # fmt: skip
        self.piecework = make_contract(
            self.cw, number="DT-001", kind=ContractKind.PIECEWORK, status=ContractStatus.DRAFT,
            lines=(("2.4", "40", "3000.00", ()),), charges=[], deductions=[],
            advance_pct="0", retention_pct="0",
        )  # fmt: skip

    def test_list_rows_with_balances_in_two_queries(self) -> None:
        with self.assertNumQueries(2):
            rows = queries.contract_rows(self.ctx, self.project, ListFilters())
        self.assertEqual([row.contract.number for row in rows], ["DT-001", "SC-001"])
        row = rows[1]
        self.assertEqual(row.progress.executed, D("2781850.00"))  # la anulada no cuenta
        self.assertEqual(row.executed_pct, D("33.8"))
        self.assertEqual(row.advance_balance, D("1632825.00"))
        self.assertEqual(row.retained, D("139092.50"))
        only_drafts = queries.contract_rows(self.ctx, self.project, ListFilters(status="draft"))
        self.assertEqual([r.contract.number for r in only_drafts], ["DT-001"])
        subcontracts = ListFilters.from_query({"tipo": "subcontract", "estado": "nada"})
        self.assertEqual((subcontracts.kind, subcontracts.status), ("subcontract", ""))
        self.assertEqual(subcontracts.query, "tipo=subcontract")

    def test_the_sheet_of_a_contract(self) -> None:
        field_quantity(self.cw, 5, "2.1", "6", date(2026, 3, 10))
        catalog = load_catalog(self.ctx, self.project)
        with self.assertNumQueries(8):
            sheet = queries.contract_sheet(self.ctx, self.project, self.contract, catalog)
        concrete = sheet.lines[0]
        self.assertEqual(concrete.label, "2.1 Concreto zapatas")
        self.assertEqual(concrete.tasks, "2.1 Columnas")
        self.assertEqual(sheet.lines[1].tasks, "Todas")
        self.assertEqual((concrete.contracted, concrete.paid, concrete.balance),
                         (D(10), D(4), D(6)))  # fmt: skip
        self.assertEqual(concrete.balance_value, D("2400000.00"))
        self.assertEqual(concrete.unpaid_validated, D(2))
        self.assertEqual([c.number for c in sheet.certificates], [1, 2])
        self.assertEqual(sheet.advance_balance, D("1632825.00"))
        self.assertIsNone(sheet.open_certificate)

    def test_history_gathers_the_contract_and_its_certificates(self) -> None:
        from suno.core import audit

        audit.record(self.ctx, action=AuditAction.CONTRACT_APPROVED,
                     target=f"contract:{self.contract.pk}", metadata={})  # fmt: skip
        audit.record(self.ctx, action=AuditAction.CERTIFICATE_APPROVED, target="certificate:x",
                     metadata={"contract": str(self.contract.pk)})  # fmt: skip
        audit.record(self.ctx, action=AuditAction.CERTIFICATE_APPROVED, target="certificate:y",
                     metadata={"contract": "otro"})  # fmt: skip
        events = queries.contract_history(self.ctx, self.contract)
        self.assertEqual(len(events), 2)
        self.assertEqual(queries.ACTION_LABELS[AuditAction.CONTRACT_APPROVED], "Contrato aprobado")

    def test_tab_alerts(self) -> None:
        # Vencido (fin 31/08) y validado sin pagar: 6 m3 más del 2.1.
        field_quantity(self.cw, 5, "2.1", "6", date(2026, 8, 10))
        # Un borrador de acta de hace 16 días.
        stale = make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                                 end=date(2026, 5, 31), status=CertificateStatus.DRAFT)  # fmt: skip
        ContractCertificate.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=16)
        )
        # Ejecutado al 100 % con anticipo por amortizar.
        full = make_contract(self.cw, number="SC-002", lines=(("1.1", "10", "1000.00", ()),),
                             charges=[], deductions=[], advance_pct="50")  # fmt: skip
        make_certificate(self.cw, full, quantities={"1.1": "10"},
                         totals={"amortization": "1000.00"})  # fmt: skip
        # Traslape: el destajo en borrador y otro contrato aprobado en el 2.4.
        make_contract(self.cw, number="SC-003", lines=(("2.4", "1", "1.00", ()),),
                      charges=[], deductions=[])  # fmt: skip
        alerts = queries.tab_alerts(self.ctx, self.project, load_catalog(self.ctx, self.project))
        texts = " | ".join(f"{alert.kind}:{alert.text}" for alert in alerts)
        self.assertIn("stale:Acta 3 de SC-001", texts)
        self.assertIn("unpaid:SC-001 · 2.1 Concreto zapatas: 2 validado sin pagar", texts)
        self.assertIn("overdue:SC-001: venció el 31/08/2026", texts)
        self.assertIn("advance:SC-002", texts)
        self.assertIn("overlap:2.4 Curado: DT-001 y SC-003", texts)

    def test_committed_by_item(self) -> None:
        settled = make_contract(self.cw, number="SC-002", status=ContractStatus.SETTLED,
                                lines=(("1.1", "10", "1000.00", ()),), charges=[],
                                deductions=[])  # fmt: skip
        make_certificate(self.cw, settled, quantities={"1.1": "3"})
        make_contract(self.cw, number="SC-003", status=ContractStatus.VOIDED,
                      lines=(("1.2", "10", "1000.00", ()),), charges=[], deductions=[])  # fmt: skip
        pending = ContractAmendment.objects.create(
            org=self.world.org, contract=self.contract, project=self.project, number=1,
            signed_on=date(2026, 5, 2), reason="Curado", created_by=self.world.admin,
            updated_by=self.world.admin, status=AmendmentStatus.DRAFT,
        )  # fmt: skip
        ContractLine.objects.create(
            org=self.world.org, contract=self.contract, order=3, item=item(self.cw.budget, "1.2"),
            quantity=D(1), current_quantity=D(1), unit_price=D("5.00"), amendment=pending,
        )  # fmt: skip
        committed = queries.committed_by_item(self.ctx, self.project)
        self.assertEqual(committed[item(self.cw.budget, "2.1").pk], D("4000000.00"))
        self.assertEqual(committed[item(self.cw.budget, "1.1").pk], D("3000.00"))
        self.assertNotIn(item(self.cw.budget, "1.2").pk, committed)  # anulado y otrosí sin aprobar
        self.assertNotIn(item(self.cw.budget, "2.4").pk, committed)  # el destajo es borrador
        self.assertEqual(
            queries.contracts_on_item(self.ctx, self.project, item(self.cw.budget, "2.1").pk),
            ["SC-001"],
        )

    def test_inbox_excludes_what_the_user_prepared(self) -> None:
        make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                         end=date(2026, 5, 31), status=CertificateStatus.DRAFT)  # fmt: skip
        mine = queries.inbox_items(self.ctx, self.project)  # el administrador preparó todo
        self.assertEqual(mine.count, 0)
        theirs = queries.inbox_items(self.world.ctx(self.world.supervisor), self.project)
        self.assertEqual(([c.number for c in theirs.contracts], len(theirs.certificates)),
                         (["DT-001"], 1))  # fmt: skip
        self.assertEqual(theirs.count, 2)
        self.assertEqual(queries.inbox_count(self.world.ctx(self.world.supervisor), self.project), 2)

    def test_certificate_document(self) -> None:
        second = make_certificate(self.cw, self.contract, number=3, start=date(2026, 5, 1),
                                  end=date(2026, 5, 31), quantities={"2.1": "2"})  # fmt: skip
        catalog = load_catalog(self.ctx, self.project)
        with self.assertNumQueries(2):
            document = queries.certificate_document(
                self.ctx, self.project, self.contract, second, catalog
            )
        concrete = document.lines[0]
        self.assertEqual((concrete.contracted, concrete.previous, concrete.this),
                         (D(10), D(4), D(2)))  # fmt: skip
        self.assertEqual((concrete.accumulated, concrete.balance), (D(6), D(4)))
        self.assertEqual(concrete.balance_value, D("1600000.00"))

    def test_suggested_numbers(self) -> None:
        self.assertEqual(queries.suggest_number(self.ctx, self.project, "subcontract"), "SC-002")
        self.assertEqual(queries.suggest_number(self.ctx, self.project, "piecework"), "DT-002")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_queries.py`
Expected: ERROR (`cannot import name 'queries' from 'suno.contracts'`).

- [ ] **Step 3: Implementar**

`server/suno/contracts/queries.py`:

```python
"""Consultas de la pestaña "Contratos" (costos, pieza 6b, spec 2.1, 2.3, 3.3 y 4).

Número fijo de consultas: la lista son 2 (contratos y agregados de sus actas aprobadas); la
ficha, 8 (el catálogo lo carga la vista); el documento de un acta, 2. Lo ejecutado de un
contrato es el bruto de sus actas aprobadas (las anuladas no cuentan).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.db.models import Count, F, Prefetch, Q, Sum
from django.utils import timezone

from suno.budgets.totals import ZERO, ChargeSpec, item_total
from suno.control.indices import share_of
from suno.core.audit import AuditAction
from suno.core.context import RequestContext
from suno.core.models import (
    AuditEvent,
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractAmendment,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Project,
)
from suno.core.models.contract import OPEN_STATES
from suno.core.scoping import scoped_qs
from suno.costs.catalog import Catalog

from .proposal import NONE, line_scopes, paid_by_line, validated_by_line
from .services import active_lines, charge_specs, deduction_specs

STALE_DAYS = 15
HISTORY_LIMIT = 200
PREFIXES = {ContractKind.SUBCONTRACT: "SC", ContractKind.PIECEWORK: "DT"}
ALL_TASKS = "Todas"
ACTION_LABELS = {
    AuditAction.CONTRACT_CREATED: "Contrato creado",
    AuditAction.CONTRACT_UPDATED: "Contrato editado",
    AuditAction.CONTRACT_APPROVED: "Contrato aprobado",
    AuditAction.CONTRACT_REJECTED: "Contrato rechazado",
    AuditAction.CONTRACT_VOIDED: "Contrato anulado",
    AuditAction.CONTRACT_SETTLED: "Contrato liquidado",
    AuditAction.CONTRACT_EXPORTED: "Contrato exportado",
    AuditAction.AMENDMENT_CREATED: "Otrosí creado",
    AuditAction.AMENDMENT_UPDATED: "Otrosí editado",
    AuditAction.AMENDMENT_DELETED: "Otrosí borrado",
    AuditAction.AMENDMENT_APPROVED: "Otrosí aprobado",
    AuditAction.AMENDMENT_REJECTED: "Otrosí rechazado",
    AuditAction.CERTIFICATE_CREATED: "Acta creada",
    AuditAction.CERTIFICATE_UPDATED: "Acta editada",
    AuditAction.CERTIFICATE_DELETED: "Acta borrada",
    AuditAction.CERTIFICATE_APPROVED: "Acta aprobada",
    AuditAction.CERTIFICATE_REJECTED: "Acta rechazada",
    AuditAction.CERTIFICATE_VOIDED: "Acta anulada",
    AuditAction.CERTIFICATE_EXPORTED: "PDF del acta descargado",
}


@dataclass(frozen=True)
class ListFilters:
    kind: str = ""
    status: str = ""

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> ListFilters:
        """Valores de la URL; uno desconocido se ignora."""
        kind, status = query.get("tipo", ""), query.get("estado", "")
        return cls(
            kind=kind if kind in ContractKind.values else "",
            status=status if status in ContractStatus.values else "",
        )

    @property
    def query(self) -> str:
        values = {"tipo": self.kind, "estado": self.status}
        return urlencode({key: value for key, value in values.items() if value})


@dataclass(frozen=True)
class Progress:
    """Lo aprobado de un contrato (sus actas aprobadas)."""

    executed: Decimal = ZERO
    amortized: Decimal = ZERO
    #: Retenido menos lo devuelto en la liquidación.
    retained: Decimal = ZERO
    certificates: int = 0


def progress_by_contract(ctx: RequestContext, project: Project) -> dict[UUID, Progress]:
    """Una consulta."""
    rows = (
        scoped_qs(ctx, ContractCertificate)
        .filter(project=project, status=CertificateStatus.APPROVED)
        .order_by()
        .values("contract_id")
        .annotate(
            executed=Sum("gross"),
            amortized=Sum("amortization"),
            retained=Sum(F("retention") - F("retention_returned")),
            n=Count("id"),
        )
        .values_list("contract_id", "executed", "amortized", "retained", "n")
    )
    return {
        contract_id: Progress(executed, amortized, retained, n)
        for contract_id, executed, amortized, retained, n in rows
    }


@dataclass(frozen=True)
class ContractRow:
    contract: Contract
    progress: Progress

    @property
    def executed_pct(self) -> Decimal | None:
        return share_of(self.progress.executed, self.contract.current_value)

    @property
    def advance_balance(self) -> Decimal:
        return max(self.contract.advance_amount - self.progress.amortized, ZERO)

    @property
    def retained(self) -> Decimal:
        return self.progress.retained


def contract_rows(
    ctx: RequestContext, project: Project, filters: ListFilters
) -> list[ContractRow]:
    """La lista de la pestaña. Dos consultas."""
    contracts = scoped_qs(ctx, Contract).filter(project=project)
    if filters.kind:
        contracts = contracts.filter(kind=filters.kind)
    if filters.status:
        contracts = contracts.filter(status=filters.status)
    progress = progress_by_contract(ctx, project)
    return [
        ContractRow(contract, progress.get(contract.pk, Progress()))
        for contract in contracts.order_by("number")
    ]


def suggest_number(ctx: RequestContext, project: Project, kind: str) -> str:
    """`SC-001`, `DT-001`… el siguiente libre del prefijo del tipo. Una consulta."""
    prefix = PREFIXES.get(kind, "SC")
    pattern = re.compile(rf"{prefix}-(\d+)", re.IGNORECASE)
    used = [
        int(match.group(1))
        for number in scoped_qs(ctx, Contract)
        .filter(project=project, number__istartswith=f"{prefix}-")
        .values_list("number", flat=True)
        if (match := pattern.fullmatch(number))
    ]
    return f"{prefix}-{max(used, default=0) + 1:03d}"


# ---------------------------------------------------------------------------
# Ficha
# ---------------------------------------------------------------------------


def _with_tasks(ctx: RequestContext, lines: Any) -> Any:
    return lines.select_related("item").prefetch_related(
        Prefetch(
            "task_links",
            queryset=scoped_qs(ctx, ContractLineTask)
            .select_related("task")
            .order_by("task__sort_order"),
        )
    )


def tasks_text(line: ContractLine) -> str:
    """Las tareas de la línea ("2.1 Columnas, 2.2 …") o "Todas"."""
    names = [f"{link.task.outline_number} {link.task.name}" for link in line.task_links.all()]
    return ", ".join(names) if names else ALL_TASKS


@dataclass(frozen=True)
class LineState:
    line: ContractLine
    label: str
    tasks: str
    paid: Decimal
    #: Validado en campo hasta hoy (contratos aprobados).
    validated: Decimal

    @property
    def contracted(self) -> Decimal:
        return self.line.current_quantity

    @property
    def balance(self) -> Decimal:
        return self.contracted - self.paid

    @property
    def contracted_value(self) -> Decimal:
        return item_total(self.contracted, self.line.unit_price)

    @property
    def paid_value(self) -> Decimal:
        return item_total(self.paid, self.line.unit_price)

    @property
    def balance_value(self) -> Decimal:
        return item_total(self.balance, self.line.unit_price)

    @property
    def unpaid_validated(self) -> Decimal:
        return max(self.validated - self.paid, NONE)


@dataclass(frozen=True)
class ContractSheet:
    contract: Contract
    lines: tuple[LineState, ...]
    charges: list[ChargeSpec]
    deductions: list[ChargeSpec]
    certificates: tuple[ContractCertificate, ...]
    amendments: tuple[ContractAmendment, ...]
    progress: Progress = field(default_factory=Progress)

    @property
    def direct_value(self) -> Decimal:
        return sum((line.contracted_value for line in self.lines), ZERO)

    @property
    def executed_pct(self) -> Decimal | None:
        return share_of(self.progress.executed, self.contract.current_value)

    @property
    def advance_balance(self) -> Decimal:
        return max(self.contract.advance_amount - self.progress.amortized, ZERO)

    @property
    def open_certificate(self) -> ContractCertificate | None:
        return next((c for c in self.certificates if c.status in OPEN_STATES), None)

    @property
    def open_amendment(self) -> ContractAmendment | None:
        return next((a for a in self.amendments if a.status in OPEN_STATES), None)

    @property
    def last_approved(self) -> ContractCertificate | None:
        approved = [c for c in self.certificates if c.status == CertificateStatus.APPROVED]
        return approved[-1] if approved else None


def contract_sheet(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    catalog: Catalog,
    *,
    today: date | None = None,
) -> ContractSheet:
    """La ficha: líneas vigentes con contratado, pagado, saldo y validado; cargos; descuentos;
    actas; otrosíes. Ocho consultas."""
    today = today or timezone.localdate()
    lines = list(
        _with_tasks(ctx, active_lines(scoped_qs(ctx, ContractLine).filter(contract=contract)))
        .order_by("order")
    )
    # Una consulta también en un borrador (se ignora en la plantilla): número fijo.
    validated = validated_by_line(ctx, project, line_scopes(lines), until=today)
    paid = paid_by_line(ctx, [line.pk for line in lines])
    certificates = tuple(
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract)
        .select_related("created_by", "decided_by", "voided_by")
        .order_by("number")
    )
    amendments = tuple(
        scoped_qs(ctx, ContractAmendment)
        .filter(contract=contract)
        .select_related("created_by", "decided_by")
        .order_by("number")
    )
    approved = [c for c in certificates if c.status == CertificateStatus.APPROVED]
    return ContractSheet(
        contract=contract,
        lines=tuple(
            LineState(
                line=line,
                label=catalog.label_of(line.item_id, line.item.code),
                tasks=tasks_text(line),
                paid=paid.get(line.pk, NONE),
                validated=validated[line.pk],
            )
            for line in lines
        ),
        charges=charge_specs(ctx, contract),
        deductions=deduction_specs(ctx, contract),
        certificates=certificates,
        amendments=amendments,
        progress=Progress(
            executed=sum((c.gross for c in approved), ZERO),
            amortized=sum((c.amortization for c in approved), ZERO),
            retained=sum((c.retention - c.retention_returned for c in approved), ZERO),
            certificates=len(approved),
        ),
    )


def contract_history(ctx: RequestContext, contract: Contract) -> list[AuditEvent]:
    """Los eventos del contrato y de sus otrosíes y actas (todos llevan `metadata.contract`)."""
    return list(
        scoped_qs(ctx, AuditEvent)
        .filter(Q(target=f"contract:{contract.pk}") | Q(metadata__contract=str(contract.pk)))
        .select_related("actor")
        .order_by("-created_at")[:HISTORY_LIMIT]
    )


# ---------------------------------------------------------------------------
# Documento de un acta (ficha y PDF)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentLine:
    label: str
    unit: str
    unit_price: Decimal
    contracted: Decimal
    previous: Decimal
    this: Decimal
    proposed: Decimal
    justification: str
    amount: Decimal

    @property
    def accumulated(self) -> Decimal:
        return self.previous + self.this

    @property
    def balance(self) -> Decimal:
        return self.contracted - self.accumulated

    @property
    def contracted_value(self) -> Decimal:
        return item_total(self.contracted, self.unit_price)

    @property
    def previous_value(self) -> Decimal:
        return item_total(self.previous, self.unit_price)

    @property
    def accumulated_value(self) -> Decimal:
        return item_total(self.accumulated, self.unit_price)

    @property
    def balance_value(self) -> Decimal:
        return item_total(self.balance, self.unit_price)

    @property
    def differs(self) -> bool:
        return self.this != self.proposed


@dataclass(frozen=True)
class CertificateDocument:
    project: Project
    contract: Contract
    certificate: ContractCertificate
    lines: tuple[DocumentLine, ...]

    @property
    def charges(self) -> list[dict[str, Any]]:
        return list(self.certificate.breakdown.get("charges", []))

    @property
    def deductions(self) -> list[dict[str, Any]]:
        return list(self.certificate.breakdown.get("deductions", []))


def certificate_document(
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    certificate: ContractCertificate,
    catalog: Catalog,
) -> CertificateDocument:
    """Por línea: contratado (vigente), anterior (actas aprobadas de número menor), esta acta,
    acumulado y saldo, en cantidad y valor. Dos consultas."""
    rows = list(
        scoped_qs(ctx, CertificateLine)
        .filter(certificate=certificate)
        .select_related("line__item")
        .order_by("line__order")
    )
    previous = paid_by_line(ctx, [row.line_id for row in rows], before_number=certificate.number)
    return CertificateDocument(
        project=project,
        contract=contract,
        certificate=certificate,
        lines=tuple(
            DocumentLine(
                label=catalog.label_of(row.line.item_id, row.line.item.code),
                unit=row.line.unit,
                unit_price=row.line.unit_price,
                contracted=row.line.current_quantity,
                previous=previous.get(row.line_id, NONE),
                this=row.quantity,
                proposed=row.proposed_quantity,
                justification=row.justification,
                amount=row.amount,
            )
            for row in rows
        ),
    )


# ---------------------------------------------------------------------------
# Avisos de la pestaña, comprometido, bandeja
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alert:
    #: `stale`, `unpaid`, `overdue`, `advance` u `overlap`.
    kind: str
    text: str
    contract_id: UUID | None = None


def _crosses(mine: frozenset[UUID], theirs: frozenset[UUID]) -> bool:
    return not mine or not theirs or bool(mine & theirs)


def tab_alerts(
    ctx: RequestContext, project: Project, catalog: Catalog, *, today: date | None = None
) -> list[Alert]:
    """Spec 4, "Avisos de la pestaña". Siete consultas."""
    today = today or timezone.localdate()
    alerts: list[Alert] = []
    stale = (
        scoped_qs(ctx, ContractCertificate)
        .filter(
            project=project,
            status=CertificateStatus.DRAFT,
            created_at__lt=timezone.now() - timedelta(days=STALE_DAYS),
        )
        .select_related("contract")
        .order_by("created_at")
    )
    for certificate in stale:
        alerts.append(
            Alert(
                "stale",
                f"Acta {certificate.number} de {certificate.contract.number}: en borrador desde "
                f"el {timezone.localtime(certificate.created_at):%d/%m/%Y}.",
                certificate.contract_id,
            )
        )
    contracts = {
        contract.pk: contract
        for contract in scoped_qs(ctx, Contract).filter(
            project=project, status__in=[ContractStatus.DRAFT, ContractStatus.APPROVED]
        )
    }
    lines = list(
        active_lines(scoped_qs(ctx, ContractLine).filter(contract_id__in=list(contracts)))
        .prefetch_related("task_links")
        .order_by("contract__number", "order")
    )
    live = [line for line in lines if contracts[line.contract_id].status == ContractStatus.APPROVED]
    validated = validated_by_line(ctx, project, line_scopes(live), until=today)
    paid = paid_by_line(ctx, [line.pk for line in live])
    for line in live:
        unpaid = validated[line.pk] - paid.get(line.pk, NONE)
        if unpaid > 0:
            number = contracts[line.contract_id].number
            alerts.append(
                Alert(
                    "unpaid",
                    f"{number} · {catalog.label_of(line.item_id)}: "
                    f"{format(unpaid.normalize(), 'f').replace('.', ',')} validado sin pagar.",
                    line.contract_id,
                )
            )
    progress = progress_by_contract(ctx, project)
    for contract in contracts.values():
        if contract.status != ContractStatus.APPROVED:
            continue
        if contract.ends_on < today:
            alerts.append(
                Alert(
                    "overdue",
                    f"{contract.number}: venció el {contract.ends_on:%d/%m/%Y} y no está "
                    "liquidado.",
                    contract.pk,
                )
            )
        done = progress.get(contract.pk, Progress())
        if (
            contract.current_value > 0
            and done.executed >= contract.current_value
            and contract.advance_amount > done.amortized
        ):
            alerts.append(
                Alert(
                    "advance",
                    f"{contract.number}: ejecutado al 100 % con anticipo por amortizar.",
                    contract.pk,
                )
            )
    by_item: dict[UUID, list[ContractLine]] = defaultdict(list)
    for line in lines:
        by_item[line.item_id].append(line)
    seen: set[tuple[UUID, str, str]] = set()
    for item_id, group in by_item.items():
        for n, first in enumerate(group):
            first_tasks = frozenset(link.task_id for link in first.task_links.all())
            for second in group[n + 1 :]:
                second_tasks = frozenset(link.task_id for link in second.task_links.all())
                if not _crosses(first_tasks, second_tasks):
                    continue
                a = contracts[first.contract_id].number
                b = contracts[second.contract_id].number
                key = (item_id, a, b)
                if key in seen:
                    continue
                seen.add(key)
                who = f"{a} y {b}" if a != b else f"dos líneas de {a}"
                alerts.append(
                    Alert(
                        "overlap",
                        f"{catalog.label_of(item_id)}: {who} lo cubren en las mismas tareas.",
                        first.contract_id,
                    )
                )
    return alerts


def committed_by_item(ctx: RequestContext, project: Project) -> dict[UUID, Decimal]:
    """Comprometido en contratos por ítem, en valor directo (comparable con el BAC del ítem):
    aprobados, cantidad vigente × precio; liquidados, lo pagado × precio. Dos consultas."""
    rows = list(
        active_lines(
            scoped_qs(ctx, ContractLine).filter(
                contract__project=project,
                contract__status__in=[ContractStatus.APPROVED, ContractStatus.SETTLED],
            )
        ).values_list("pk", "item_id", "current_quantity", "unit_price", "contract__status")
    )
    paid = paid_by_line(
        ctx, [pk for pk, *_, status in rows if status == ContractStatus.SETTLED]
    )
    committed: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for pk, item_id, current, price, status in rows:
        quantity = current if status == ContractStatus.APPROVED else paid.get(pk, NONE)
        committed[item_id] += item_total(quantity, price)
    return {item_id: value for item_id, value in committed.items() if value}


def contracts_on_item(ctx: RequestContext, project: Project, item_id: UUID) -> list[str]:
    """Los contratos aprobados con una línea vigente del ítem. Una consulta."""
    return sorted(
        set(
            active_lines(
                scoped_qs(ctx, ContractLine).filter(
                    contract__project=project,
                    contract__status=ContractStatus.APPROVED,
                    item_id=item_id,
                )
            ).values_list("contract__number", flat=True)
        )
    )


@dataclass(frozen=True)
class InboxItems:
    contracts: list[Contract]
    amendments: list[ContractAmendment]
    certificates: list[ContractCertificate]

    @property
    def count(self) -> int:
        return len(self.contracts) + len(self.amendments) + len(self.certificates)


def _not_mine(ctx: RequestContext, rows: Any) -> Any:
    return rows.exclude(created_by_id=ctx.user_id).exclude(updated_by_id=ctx.user_id)


def _waiting(ctx: RequestContext, project: Project) -> tuple[Any, Any, Any]:
    contracts = _not_mine(
        ctx,
        scoped_qs(ctx, Contract).filter(
            project=project, status=ContractStatus.DRAFT, rejected_by__isnull=True
        ),
    )
    amendments = _not_mine(
        ctx, scoped_qs(ctx, ContractAmendment).filter(project=project, status="draft")
    )
    certificates = _not_mine(
        ctx, scoped_qs(ctx, ContractCertificate).filter(project=project, status="draft")
    )
    return contracts, amendments, certificates


def inbox_items(ctx: RequestContext, project: Project) -> InboxItems:
    """Lo que otras personas prepararon y espera decisión (un contrato devuelto con motivo no:
    espera a su autor). Tres consultas."""
    contracts, amendments, certificates = _waiting(ctx, project)
    return InboxItems(
        contracts=list(contracts.select_related("created_by").order_by("created_at")),
        amendments=list(
            amendments.select_related("contract", "created_by").order_by("created_at")
        ),
        certificates=list(
            certificates.select_related("contract", "created_by")
            .annotate(
                adjusted=Count(
                    "lines", filter=~Q(lines__quantity=F("lines__proposed_quantity"))
                )
            )
            .order_by("created_at")
        ),
    )


def inbox_count(ctx: RequestContext, project: Project) -> int:
    """Para el contador de "Por aprobar" de Costos. Tres consultas."""
    return sum(rows.count() for rows in _waiting(ctx, project))
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts && ruff check suno/contracts && mypy suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts
```

Expected: PASS. Si el número de consultas de la ficha no da 8, contar con `CaptureQueriesContext` y ajustar el comentario y la prueba al número real **solo** si cada consulta tiene razón de ser (líneas, tareas, validado, pagado, cargos, descuentos, actas, otrosíes).

- [ ] **Step 5: Commit**

```bash
git add server/suno/contracts/queries.py server/suno/contracts/tests/test_queries.py
git commit -m "feat(contracts): list balances, contract sheet, tab alerts, commitments and inbox

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Pestaña "Contratos": lista, formulario de contrato, ficha y otrosíes

**Files:**
- Create: `server/suno/contracts/access.py`, `server/suno/contracts/forms.py`, `server/suno/contracts/views.py`, `server/suno/contracts/urls.py`
- Modify: `server/config/urls.py`
- Modify: `server/suno/core/templates/suno/projects/_header.html` (pestaña "Contratos")
- Create: `server/suno/core/templates/suno/contracts/{_nav,_alerts,_rows,_terms,_certificates,list,form,detail,amendment_form}.html`
- Modify: `server/suno/core/static/suno/css/projects.css` (estilos `ctr-*`), `server/suno/core/templates/suno/projects/base.html` (`?v=20261114-contracts-v1`)
- Test: `server/suno/contracts/tests/test_views.py`

Acceso como en la 6a: quien no lee costos del proyecto recibe 404 (`get_contracts_project`, RD-11); quien lee pero no tiene la capacidad de una acción, 403 (`PermissionDenied`, lo levanta el servicio o la vista). Las reglas de estado y de autoaprobación viven en los servicios: la vista solo las traduce a mensajes y decide qué botones mostrar.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_views.py`:

```python
"""Pestaña "Contratos" (spec, 2.1–2.3 y 4): acceso, lista, formulario, ficha y otrosíes."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest import mock

from django.test import TestCase

from suno.authn.tests.factories import make_user

from suno.core.models import (
    AmendmentStatus,
    Contract,
    ContractAmendment,
    ContractStatus,
    Role,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import contract_line, contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 5, 15)
AIU_ROWS = [
    ("Administración", "10", "direct"),
    ("Imprevistos", "5", "direct"),
    ("Utilidad", "5", "direct"),
    ("IVA sobre utilidad", "19", "line:3"),
]


def contract_post(**overrides: Any) -> dict[str, Any]:
    """El POST del formulario de contrato: encabezado, dos líneas y los cargos AIU."""
    data: dict[str, Any] = {
        "accion": "guardar",
        "kind": "subcontract",
        "number": "SC-001",
        "purpose": "Estructura en concreto",
        "contractor": "Estructuras del Norte SAS",
        "starts_on": "2026-03-01",
        "ends_on": "2026-08-31",
        "advance_pct": "30",
        "retention_pct": "5",
        "lineas-TOTAL_FORMS": "2",
        "lineas-INITIAL_FORMS": "0",
        "lineas-0-item": "2.1 Concreto zapatas",
        "lineas-0-tasks": "2.1",
        "lineas-0-unit": "m3",
        "lineas-0-quantity": "10",
        "lineas-0-unit_price": "400000",
        "lineas-1-item": "2.2 Acero de refuerzo",
        "lineas-1-tasks": "",
        "lineas-1-unit": "kg",
        "lineas-1-quantity": "800",
        "lineas-1-unit_price": "3500",
        "cargos-TOTAL_FORMS": str(len(AIU_ROWS)),
        "cargos-INITIAL_FORMS": "0",
        "descuentos-TOTAL_FORMS": "0",
        "descuentos-INITIAL_FORMS": "0",
    }
    for n, (name, pct, base) in enumerate(AIU_ROWS):
        data.update({f"cargos-{n}-name": name, f"cargos-{n}-pct": pct, f"cargos-{n}-base": base})
    data.update(overrides)
    return data


@skip_without_db
class ContractPagesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cw = contract_world()
        self.world = self.cw.world
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}contratos/"
        self.client.force_login(self.world.admin)

    def test_who_sees_the_tab(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Contratos</a>")
        self.client.force_login(self.world.supervisor)  # Revisor de Costos: lee
        self.assertEqual(self.client.get(self.base).status_code, 200)
        plain = make_user(self.world.org, username="sin-costos", role=Role.SUPERVISOR)
        self.client.force_login(plain)  # sin la sección Costos
        self.assertNotContains(self.client.get(self.project_url), ">Contratos</a>")
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        other = contract_world("Otra constructora")
        self.client.force_login(other.world.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_list_filters_and_alerts(self) -> None:
        make_contract(self.cw, ends_on=date(2026, 4, 30))
        make_contract(self.cw, number="DT-001", kind="piecework", status=ContractStatus.DRAFT,
                      lines=(("2.4", "40", "3000.00", ()),), charges=[], deductions=[])  # fmt: skip
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Contratos</a>')
        self.assertContains(response, "SC-001")
        self.assertContains(response, "DT-001")
        self.assertContains(response, "8.224.600,00")
        self.assertContains(response, "venció el 30/04/2026")
        drafts = self.client.get(self.base + "?estado=draft")
        self.assertNotContains(drafts, "SC-001")
        self.assertContains(drafts, '<option value="draft" selected>')

    def test_viewer_reads_but_does_not_create(self) -> None:
        self.client.force_login(self.cw.viewer)
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(self.base + "nuevo/").status_code, 403)
        self.assertEqual(self.client.post(self.base + "nuevo/", contract_post()).status_code, 403)

    def test_new_form_starts_from_the_templates(self) -> None:
        page = self.client.get(self.base + "nuevo/")
        self.assertContains(page, 'value="SC-001"')
        self.assertContains(page, 'value="Administración"')
        self.assertContains(page, 'value="Retención en la fuente"')
        self.assertContains(page, '<datalist id="ctr-items">')
        self.assertContains(page, '<option value="2.1 Concreto zapatas">')
        piecework = self.client.get(self.base + "nuevo/?tipo=piecework")
        self.assertContains(piecework, 'value="DT-001"')
        self.assertContains(piecework, 'name="cargos-TOTAL_FORMS" value="0"')
        self.assertContains(piecework, 'name="descuentos-TOTAL_FORMS" value="0"')

    def test_create_a_contract(self) -> None:
        response = self.client.post(self.base + "nuevo/", contract_post())
        contract = Contract.objects.get()
        self.assertRedirects(response, f"{self.base}{contract.pk}/")
        self.assertEqual(str(contract.current_value), "8224600.00")
        self.assertEqual(list(contract_line(contract, "2.1").tasks.values_list("outline_number",
                                                                               flat=True)), ["2.1"])  # fmt: skip
        detail = self.client.get(f"{self.base}{contract.pk}/")
        self.assertContains(detail, "Estructuras del Norte SAS")
        self.assertContains(detail, "8.224.600,00")

    def test_add_rows_does_not_save(self) -> None:
        response = self.client.post(self.base + "nuevo/", contract_post(accion="lineas"))
        self.assertContains(response, 'name="lineas-TOTAL_FORMS" value="5"')
        self.assertFalse(Contract.objects.exists())

    def test_errors_of_the_rows(self) -> None:
        response = self.client.post(
            self.base + "nuevo/",
            contract_post(**{"lineas-0-tasks": "9.9", "lineas-1-item": "Nada que ver"}),
        )
        self.assertContains(response, "no hay tareas vigentes con número 9.9")
        self.assertContains(response, "«Nada que ver» no es un ítem")
        self.assertFalse(Contract.objects.exists())

    def test_an_overlap_asks_for_confirmation(self) -> None:
        make_contract(self.cw, number="SC-009", lines=(("2.2", "5", "1.00", ()),))
        response = self.client.post(self.base + "nuevo/", contract_post())
        self.assertContains(response, "el contrato SC-009 ya lo cubre")
        self.assertContains(response, 'name="confirm_overlap"')
        self.assertEqual(Contract.objects.count(), 1)
        self.client.post(self.base + "nuevo/", contract_post(confirm_overlap="on"))
        self.assertEqual(Contract.objects.count(), 2)

    def test_detail_tabs(self) -> None:
        contract = make_contract(self.cw)
        make_certificate(self.cw, contract, quantities={"2.1": "4"})
        url = f"{self.base}{contract.pk}/"
        self.assertContains(self.client.get(url), "Anticipo por amortizar")
        lines = self.client.get(url + "?pestana=lineas")
        self.assertContains(lines, "2.1 Concreto zapatas")
        self.assertContains(lines, "2.1 Columnas")
        self.assertContains(self.client.get(url + "?pestana=actas"), "Acta 1")
        self.assertContains(self.client.get(url + "?pestana=otrosies"), "Nuevo otrosí")
        self.assertEqual(self.client.get(url + "?pestana=otra").status_code, 200)

    def test_approve_reject_and_void_from_the_sheet(self) -> None:
        self.client.post(self.base + "nuevo/", contract_post())
        contract = Contract.objects.get()
        url = f"{self.base}{contract.pk}/"
        own = self.client.post(url + "aprobar/", follow=True)
        self.assertContains(own, "otra persona")
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "rechazar/", {"motivo": "Falta la póliza"})
        contract.refresh_from_db()
        self.assertEqual(contract.rejection_note, "Falta la póliza")
        self.client.force_login(self.world.admin)
        self.client.post(url + "editar/", contract_post())
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "aprobar/")
        contract.refresh_from_db()
        self.assertEqual(contract.status, ContractStatus.APPROVED)
        self.assertEqual(self.client.post(url + "anular/", {"motivo": "x"}).status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(url + "anular/", {"motivo": "El contratista desistió"})
        contract.refresh_from_db()
        self.assertEqual(contract.status, ContractStatus.VOIDED)

    def test_contributor_edits_only_its_drafts(self) -> None:
        other = make_contract(self.cw, status=ContractStatus.DRAFT)
        self.client.force_login(self.cw.contributor)
        self.assertEqual(self.client.get(f"{self.base}{other.pk}/editar/").status_code, 403)
        self.client.post(self.base + "nuevo/", contract_post(number="SC-002",
                                                             confirm_overlap="on"))  # fmt: skip
        own = Contract.objects.get(number="SC-002")
        self.assertEqual(self.client.get(f"{self.base}{own.pk}/editar/").status_code, 200)

    def test_amendment_create_and_approve(self) -> None:
        contract = make_contract(self.cw)
        concrete = contract_line(contract, "2.1")
        url = f"{self.base}{contract.pk}/otrosies/nuevo/"
        self.assertContains(self.client.get(url), f'name="aumento_{concrete.pk}"')
        response = self.client.post(
            url,
            {
                "accion": "guardar",
                "signed_on": "2026-05-02",
                "reason": "Mayor cantidad de concreto",
                "new_ends_on": "2026-10-31",
                f"aumento_{concrete.pk}": "2",
                "lineas-TOTAL_FORMS": "1",
                "lineas-INITIAL_FORMS": "0",
                "lineas-0-item": "2.4 Curado",
                "lineas-0-tasks": "",
                "lineas-0-unit": "m2",
                "lineas-0-quantity": "40",
                "lineas-0-unit_price": "3000",
            },
        )
        amendment = ContractAmendment.objects.get()
        self.assertRedirects(response, f"{self.base}{contract.pk}/?pestana=otrosies")
        self.client.force_login(self.world.supervisor)
        self.client.post(f"{self.base}{contract.pk}/otrosies/{amendment.pk}/aprobar/")
        amendment.refresh_from_db()
        contract.refresh_from_db()
        self.assertEqual(amendment.status, AmendmentStatus.APPROVED)
        self.assertEqual(str(contract.current_value), "9337340.00")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_views.py`
Expected: FAIL (404 en `/contratos/`: la ruta no existe; la cabecera no tiene la pestaña).

- [ ] **Step 3: Acceso**

`server/suno/contracts/access.py`:

```python
"""Quién ve los contratos del proyecto y cómo se resuelven los objetos de la URL.

Los contratos son de la sección Costos (spec 4): el mismo 404 de la 6a para quien no la lee."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from suno.core.context import RequestContext
from suno.core.models import Contract, ContractAmendment, ContractCertificate, Project
from suno.core.scoping import scoped_qs
from suno.costs.access import get_costs_project


def get_contracts_project(ctx: RequestContext, project_id: UUID) -> Project:
    return get_costs_project(ctx, project_id)


def get_contract(ctx: RequestContext, project: Project, contract_id: UUID) -> Contract:
    contract = (
        scoped_qs(ctx, Contract)
        .select_related("created_by", "updated_by", "approved_by", "rejected_by", "closed_by")
        .filter(pk=contract_id, project=project)
        .first()
    )
    if contract is None:
        raise Http404
    return contract


def get_amendment(
    ctx: RequestContext, contract: Contract, amendment_id: UUID
) -> ContractAmendment:
    amendment = (
        scoped_qs(ctx, ContractAmendment)
        .select_related("created_by", "decided_by")
        .filter(pk=amendment_id, contract=contract)
        .first()
    )
    if amendment is None:
        raise Http404
    return amendment


def get_certificate(
    ctx: RequestContext, contract: Contract, certificate_id: UUID
) -> ContractCertificate:
    certificate = (
        scoped_qs(ctx, ContractCertificate)
        .select_related("created_by", "updated_by", "decided_by", "voided_by")
        .filter(pk=certificate_id, contract=contract)
        .first()
    )
    if certificate is None:
        raise Http404
    return certificate
```

- [ ] **Step 4: Formularios**

`server/suno/contracts/forms.py`:

```python
"""Formularios de la pestaña "Contratos" (costos, pieza 6b, spec 2.2–2.4).

Sin JavaScript (como el libro de la 6a): líneas, cargos y descuentos son filas de un formset y
"Agregar filas" vuelve a pintar el formulario con filas vacías de más. El ítem se elige con un
buscador `datalist` sobre las etiquetas del catálogo; las tareas se escriben como números de
esquema separados por comas (vacío: todas las del ítem).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.forms import formset_factory
from django.http import QueryDict

from suno.attachments.web import ACCEPT
from suno.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    MAX_CHARGES,
    ChargeSpec,
    line_base,
)
from suno.core.models import ContractAmendment, ContractLine, ContractKind, Task
from suno.costs.catalog import Catalog

from .calc import BASE_GROSS
from .certificates import CertificateLineData
from .services import AmendmentData, ContractData, IncreaseData, LineData

#: Filas vacías que agrega cada botón "Agregar filas" (`accion` = prefijo del formset).
ADD_ROWS = {"lineas": 3, "cargos": 1, "descuentos": 1}
NEW_LINE_ROWS = 3
_SPLIT = re.compile(r"[,;\s]+")
CHARGE_BASES = [
    (BASE_DIRECT, "Costo directo"),
    (BASE_SUBTOTAL, "Subtotal"),
    *((line_base(n), f"Cargo {n}") for n in range(1, MAX_CHARGES + 1)),
]
DEDUCTION_BASES = [
    (BASE_DIRECT, "Bruto directo"),
    (BASE_GROSS, "Bruto"),
    *((line_base(n), f"Cargo {n}") for n in range(1, MAX_CHARGES + 1)),
]


def _date() -> forms.DateInput:
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def plain(value: Decimal) -> str:
    """Un decimal sin ceros de sobra ni notación científica, para un `<input>`: 10.0000 → 10."""
    return format(value.normalize(), "f")


class ContractForm(forms.Form):
    kind = forms.ChoiceField(label="Tipo", choices=ContractKind.choices)
    number = forms.CharField(label="Número", max_length=30)
    purpose = forms.CharField(label="Objeto", max_length=300)
    contractor = forms.CharField(label="Contratista", max_length=200)
    starts_on = forms.DateField(label="Inicio", widget=_date())
    ends_on = forms.DateField(label="Fin", widget=_date())
    advance_pct = forms.DecimalField(
        label="Anticipo (%)", max_digits=7, decimal_places=4, min_value=0, max_value=100
    )
    retention_pct = forms.DecimalField(
        label="Retención de garantía (%)",
        max_digits=7,
        decimal_places=4,
        min_value=0,
        max_value=100,
    )
    confirm_overlap = forms.BooleanField(label="Confirmo el traslape", required=False)


class LineForm(forms.Form):
    item = forms.CharField(
        label="Ítem",
        required=False,
        widget=forms.TextInput(attrs={"list": "ctr-items", "autocomplete": "off"}),
    )
    tasks = forms.CharField(
        label="Tareas",
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Todas (o 1.2, 2.1)"}),
    )
    unit = forms.CharField(label="Unidad", required=False, max_length=30)
    quantity = forms.DecimalField(
        label="Cantidad", required=False, max_digits=18, decimal_places=4, min_value=0
    )
    unit_price = forms.DecimalField(
        label="Precio unitario", required=False, max_digits=18, decimal_places=2, min_value=0
    )


class TermForm(forms.Form):
    """Un cargo o un descuento: nombre, % y base."""

    name = forms.CharField(label="Nombre", required=False, max_length=100)
    pct = forms.DecimalField(label="%", required=False, max_digits=7, decimal_places=4, min_value=0)
    base = forms.ChoiceField(label="Base", required=False)

    def __init__(self, *args: Any, bases: Sequence[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["base"].choices = list(bases)  # type: ignore[attr-defined]


LineFormSet = formset_factory(LineForm, extra=0)
TermFormSet = formset_factory(TermForm, extra=0)


def more_rows(data: QueryDict, action: str) -> QueryDict | None:
    """El POST con filas vacías de más si se pulsó "Agregar filas" (`action`); si no, None."""
    if action not in ADD_ROWS:
        return None
    copy = data.copy()
    key = f"{action}-TOTAL_FORMS"
    try:
        total = int(copy.get(key, "0"))
    except ValueError:
        total = 0
    copy[key] = str(total + ADD_ROWS[action])
    return copy


def contract_formsets(
    data: QueryDict | None,
    *,
    lines: Sequence[Mapping[str, Any]] = (),
    charges: Sequence[Mapping[str, Any]] = (),
    deductions: Sequence[Mapping[str, Any]] = (),
) -> tuple[Any, Any, Any]:
    return (
        LineFormSet(data, prefix="lineas", initial=list(lines)),
        TermFormSet(
            data, prefix="cargos", initial=list(charges), form_kwargs={"bases": CHARGE_BASES}
        ),
        TermFormSet(
            data,
            prefix="descuentos",
            initial=list(deductions),
            form_kwargs={"bases": DEDUCTION_BASES},
        ),
    )


def outline_index(tasks: Mapping[UUID, Task]) -> dict[str, UUID]:
    return {task.outline_number: task.pk for task in tasks.values()}


def task_ids_of(text: str, index: Mapping[str, UUID]) -> tuple[tuple[UUID, ...], list[str]]:
    """Los números de esquema escritos → tareas; y los que no existen."""
    found: list[UUID] = []
    unknown: list[str] = []
    for token in filter(None, _SPLIT.split(text.strip())):
        task_id = index.get(token)
        if task_id is None:
            unknown.append(token)
        else:
            found.append(task_id)
    return tuple(dict.fromkeys(found)), unknown


def read_lines(
    formset: Any, catalog: Catalog, index: Mapping[str, UUID]
) -> tuple[list[LineData], list[str]]:
    """Las filas con ítem (las vacías se ignoran). Formset ya validado."""
    lines: list[LineData] = []
    errors: list[str] = []
    for n, form in enumerate(formset.forms, start=1):
        data = form.cleaned_data
        label = (data.get("item") or "").strip()
        if not label:
            continue
        row = catalog.by_label(label)
        task_ids, unknown = task_ids_of(data.get("tasks") or "", index)
        amount, price = data.get("quantity"), data.get("unit_price")
        if row is None:
            errors.append(
                f"Línea {n}: «{label}» no es un ítem del presupuesto vigente; elígelo de la lista."
            )
        if unknown:
            errors.append(f"Línea {n}: no hay tareas vigentes con número {', '.join(unknown)}.")
        if amount is None or price is None:
            errors.append(f"Línea {n}: escribe la cantidad y el precio.")
        if row is not None and not unknown and amount is not None and price is not None:
            lines.append(LineData(row.item_id, task_ids, data.get("unit") or "", amount, price))
    return lines, errors


def read_terms(formset: Any) -> list[ChargeSpec]:
    """Las filas con nombre, numeradas 1, 2, 3… en orden. Formset ya validado."""
    named = [form.cleaned_data for form in formset.forms if (form.cleaned_data.get("name") or "").strip()]
    return [
        ChargeSpec(n, data["name"], data.get("pct") or Decimal("0"), data.get("base") or BASE_DIRECT)
        for n, data in enumerate(named, start=1)
    ]


def contract_data(
    form: ContractForm,
    lines: Any,
    charges: Any,
    deductions: Any,
    catalog: Catalog,
    tasks: Mapping[UUID, Task],
) -> tuple[ContractData, list[str]]:
    rows, errors = read_lines(lines, catalog, outline_index(tasks))
    data = form.cleaned_data
    return (
        ContractData(
            kind=data["kind"],
            number=data["number"],
            purpose=data["purpose"],
            contractor=data["contractor"],
            starts_on=data["starts_on"],
            ends_on=data["ends_on"],
            advance_pct=data["advance_pct"],
            retention_pct=data["retention_pct"],
            lines=tuple(rows),
            charges=tuple(read_terms(charges)),
            deductions=tuple(read_terms(deductions)),
            confirm_overlap=data["confirm_overlap"],
        ),
        errors,
    )


def line_initial(lines: Sequence[ContractLine], catalog: Catalog) -> list[dict[str, Any]]:
    """Las líneas con `select_related("item")` y `prefetch_related("task_links__task")`."""
    return [
        {
            "item": catalog.label_of(line.item_id, line.item.code),
            "tasks": ", ".join(link.task.outline_number for link in line.task_links.all()),
            "unit": line.unit,
            "quantity": plain(line.quantity),
            "unit_price": line.unit_price,
        }
        for line in lines
    ]


def term_initial(specs: Sequence[ChargeSpec]) -> list[dict[str, Any]]:
    return [{"name": s.name, "pct": plain(s.pct), "base": s.base} for s in specs]


class AmendmentForm(forms.Form):
    signed_on = forms.DateField(label="Fecha del otrosí", widget=_date())
    reason = forms.CharField(
        label="Motivo", max_length=500, widget=forms.Textarea(attrs={"rows": 2})
    )
    new_ends_on = forms.DateField(
        label="Nueva fecha de fin (opcional)", required=False, widget=_date()
    )
    confirm_overlap = forms.BooleanField(label="Confirmo el traslape", required=False)

    def __init__(self, *args: Any, lines: Sequence[ContractLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"aumento_{line.pk}"] = forms.DecimalField(
                label="Aumento", required=False, max_digits=18, decimal_places=4, min_value=0
            )

    def increase_fields(self) -> list[tuple[ContractLine, Any]]:
        return [(line, self[f"aumento_{line.pk}"]) for line in self.lines]

    def amendment_data(self, new_lines: Sequence[LineData]) -> AmendmentData:
        data = self.cleaned_data
        return AmendmentData(
            signed_on=data["signed_on"],
            reason=data["reason"],
            new_ends_on=data.get("new_ends_on"),
            increases=tuple(
                IncreaseData(line.pk, data[f"aumento_{line.pk}"])
                for line in self.lines
                if data.get(f"aumento_{line.pk}")
            ),
            new_lines=tuple(new_lines),
            confirm_overlap=data["confirm_overlap"],
        )


def amendment_initial(amendment: ContractAmendment) -> dict[str, Any]:
    values: dict[str, Any] = {
        "signed_on": amendment.signed_on,
        "reason": amendment.reason,
        "new_ends_on": amendment.new_ends_on,
    }
    for increase in amendment.increases.all():
        values[f"aumento_{increase.line_id}"] = plain(increase.added_quantity)
    return values


class CertificateForm(forms.Form):
    """Cantidad y justificación por línea vigente; vacía = la propuesta."""

    def __init__(self, *args: Any, lines: Sequence[ContractLine], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lines = list(lines)
        for line in self.lines:
            self.fields[f"cantidad_{line.pk}"] = forms.DecimalField(
                label="Cantidad del acta", required=False, max_digits=18, decimal_places=4,
                min_value=0,
            )  # fmt: skip
            self.fields[f"justificacion_{line.pk}"] = forms.CharField(
                label="Justificación", required=False, max_length=300
            )

    def certificate_lines(self) -> tuple[CertificateLineData, ...]:
        data = self.cleaned_data
        return tuple(
            CertificateLineData(
                line.pk,
                data[f"cantidad_{line.pk}"],
                data.get(f"justificacion_{line.pk}") or "",
            )
            for line in self.lines
            if data.get(f"cantidad_{line.pk}") is not None
        )


class SignedForm(forms.Form):
    signed = forms.FileField(
        label="Acta firmada (PDF, JPG, PNG o WebP)",
        widget=forms.FileInput(attrs={"accept": ACCEPT}),
    )
```

- [ ] **Step 5: Las vistas de contratos y otrosíes**

`server/suno/contracts/views.py` (las actas se agregan en la Task 9 y los documentos en la Task 10):

```python
"""Pestaña "Contratos" del proyecto (costos, pieza 6b): subcontratos y destajos.

Quien no lee los costos del proyecto recibe 404 (RD-11; `access.get_contracts_project`). Quien
los lee pero no tiene la capacidad de una acción recibe 403. Las reglas de estado y de
autoaprobación viven en `services` y `certificates`: aquí solo se traducen a mensajes.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from suno.budgets.totals import BASE_DIRECT, describe_base, pct_text
from suno.core.context import RequestContext
from suno.core.models import (
    Contract,
    ContractAmendment,
    ContractKind,
    ContractLine,
    ContractStatus,
    Project,
)
from suno.core.policies import (
    can_approve_costs,
    can_create_costs,
    can_delete_cost,
    can_export_costs,
    can_reverse_costs,
    can_update_cost,
)
from suno.core.scoping import scoped_qs
from suno.costs.catalog import assignable_tasks, load_catalog
from suno.projects.views import _ctx, _project_context, _render

from . import services
from .access import get_amendment, get_contract, get_contracts_project
from .calc import (
    BASE_GROSS,
    CHARGE_TEMPLATES,
    DEDUCTION_TEMPLATES,
    charge_template,
    withholding_template,
)
from .certificates import contract_lines
from .forms import (
    NEW_LINE_ROWS,
    AmendmentForm,
    ContractForm,
    LineFormSet,
    amendment_initial,
    contract_data,
    contract_formsets,
    line_initial,
    more_rows,
    outline_index,
    plain,
    read_lines,
    term_initial,
)
from .queries import (
    ACTION_LABELS,
    ContractSheet,
    ListFilters,
    contract_history,
    contract_rows,
    contract_sheet,
    suggest_number,
    tab_alerts,
)
from .services import ContractRejected


def term_texts(sheet: ContractSheet) -> tuple[list[str], list[str]]:
    """Cargos y descuentos en palabras: "4. IVA sobre utilidad: 19 % sobre línea 3 (Utilidad)"."""
    names = {charge.order: charge.name for charge in sheet.charges}
    charges = [
        f"{c.order}. {c.name}: {pct_text(c.pct)} % sobre {describe_base(c.base, names)}"
        for c in sheet.charges
    ]
    bases = {BASE_DIRECT: "bruto directo", BASE_GROSS: "bruto"}
    deductions = [
        f"{d.order}. {d.name}: {pct_text(d.pct)} % sobre "
        f"{bases.get(d.base) or describe_base(d.base, names).replace('línea', 'cargo')}"
        for d in sheet.deductions
    ]
    return charges, deductions


TABS = {
    "resumen": "Resumen",
    "lineas": "Líneas",
    "actas": "Actas",
    "otrosies": "Otrosíes",
    "historial": "Historial",
}


def _context(ctx: RequestContext, project: Project) -> dict[str, Any]:
    return {
        **_project_context(ctx, project),
        "can_create_contract": can_create_costs(ctx, project),
        "can_decide_contract": can_approve_costs(ctx, project),
        "can_close_contract": can_reverse_costs(ctx, project),
        "can_export_contract": can_export_costs(ctx, project),
    }


def _detail(project: Project, contract_id: UUID, tab: str = "") -> HttpResponse:
    url = reverse("contracts:detail", args=[project.pk, contract_id])
    return redirect(f"{url}?{urlencode({'pestana': tab})}" if tab else url)


def _back(request: HttpRequest, project: Project, contract_id: UUID, tab: str = "") -> HttpResponse:
    """A la bandeja "Por aprobar" de Costos si de allá vino; si no, a la ficha."""
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return _detail(project, contract_id, tab)


@login_required(login_url="platform_login")
@require_GET
def contract_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    filters = ListFilters.from_query(request.GET)
    return _render(
        request,
        ctx,
        "suno/contracts/list.html",
        {
            **_context(ctx, project),
            "filters": filters,
            "rows": contract_rows(ctx, project, filters),
            "alerts": tab_alerts(ctx, project, load_catalog(ctx, project)),
            "kinds": ContractKind.choices,
            "statuses": ContractStatus.choices,
        },
    )


def _edit_lines(ctx: RequestContext, contract: Contract) -> list[ContractLine]:
    return list(
        scoped_qs(ctx, ContractLine)
        .filter(contract=contract)
        .select_related("item")
        .prefetch_related("task_links__task")
        .order_by("order")
    )


def _contract_form(
    request: HttpRequest, ctx: RequestContext, project: Project, contract: Contract | None
) -> HttpResponse:
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    errors: list[str] = []
    overlaps: list[str] = []
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = ContractForm(data)
        lines, charges, deductions = contract_formsets(data)
        if action == "guardar":
            valid = [form.is_valid(), lines.is_valid(), charges.is_valid(), deductions.is_valid()]
            if all(valid):
                values, errors = contract_data(form, lines, charges, deductions, catalog, tasks)
                if not errors:
                    try:
                        if contract is None:
                            saved = services.create_contract(
                                ctx, project, values, catalog=catalog, tasks=tasks
                            )
                        else:
                            saved = services.update_contract(
                                ctx, project, contract.pk, values, catalog=catalog, tasks=tasks
                            )
                    except ContractRejected as exc:
                        errors, overlaps = exc.errors, exc.overlaps
                    else:
                        messages.success(request, "Contrato guardado en borrador.")
                        return _detail(project, saved.pk)
    elif contract is None:
        kind = request.GET.get("tipo", "")
        kind = kind if kind in ContractKind.values else str(ContractKind.SUBCONTRACT)
        piecework = kind == ContractKind.PIECEWORK
        template = request.GET.get("plantilla") or ("ninguno" if piecework else "aiu")
        withholdings = request.GET.get("descuentos") or ("ninguno" if piecework else "retenciones")
        charge_rows = charge_template(template)
        today = timezone.localdate()
        form = ContractForm(
            initial={
                "kind": kind,
                "number": suggest_number(ctx, project, kind),
                "starts_on": today,
                "ends_on": today,
                "advance_pct": 0,
                "retention_pct": 0,
            }
        )
        lines, charges, deductions = contract_formsets(
            None,
            lines=[{}] * NEW_LINE_ROWS,
            charges=term_initial(charge_rows),
            deductions=term_initial(
                withholding_template(charge_rows) if withholdings == "retenciones" else []
            ),
        )
    else:
        form = ContractForm(
            initial={
                "kind": contract.kind,
                "number": contract.number,
                "purpose": contract.purpose,
                "contractor": contract.contractor,
                "starts_on": contract.starts_on,
                "ends_on": contract.ends_on,
                "advance_pct": plain(contract.advance_pct),
                "retention_pct": plain(contract.retention_pct),
            }
        )
        lines, charges, deductions = contract_formsets(
            None,
            lines=line_initial(_edit_lines(ctx, contract), catalog),
            charges=term_initial(services.charge_specs(ctx, contract)),
            deductions=term_initial(services.deduction_specs(ctx, contract)),
        )
    return _render(
        request,
        ctx,
        "suno/contracts/form.html",
        {
            **_context(ctx, project),
            "form": form,
            "lines": lines,
            "charges": charges,
            "deductions": deductions,
            "catalog": catalog,
            "contract": contract,
            "errors": errors,
            "overlaps": overlaps,
            "show_errors": action == "guardar",
            "kinds": ContractKind.choices,
            "charge_templates": CHARGE_TEMPLATES,
            "deduction_templates": DEDUCTION_TEMPLATES,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_contract(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    return _contract_form(request, ctx, project, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    if contract.status != ContractStatus.DRAFT:
        messages.error(request, services.APPROVED_IS_FINAL)
        return _detail(project, contract.pk)
    if not can_update_cost(ctx, contract):
        raise PermissionDenied
    return _contract_form(request, ctx, project, contract)


@login_required(login_url="platform_login")
@require_GET
def contract_detail(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    tab = request.GET.get("pestana", "")
    tab = tab if tab in TABS else "resumen"
    sheet = contract_sheet(ctx, project, contract, load_catalog(ctx, project))
    charge_texts, deduction_texts = term_texts(sheet)
    approved = contract.status == ContractStatus.APPROVED
    draft = contract.status == ContractStatus.DRAFT
    mine = ctx.user_id in (contract.created_by_id, contract.updated_by_id)
    return _render(
        request,
        ctx,
        "suno/contracts/detail.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "sheet": sheet,
            "charge_texts": charge_texts,
            "deduction_texts": deduction_texts,
            "tabs": TABS,
            "tab": tab,
            "history": [
                (event, ACTION_LABELS.get(event.action, event.action))
                for event in (contract_history(ctx, contract) if tab == "historial" else [])
            ],
            "can_edit": draft and can_update_cost(ctx, contract),
            "can_delete": draft and can_delete_cost(ctx, contract),
            "can_decide": draft
            and contract.rejected_by_id is None
            and can_approve_costs(ctx, project)
            and not mine,
            "can_void": approved
            and can_reverse_costs(ctx, project)
            and sheet.progress.certificates == 0
            and sheet.open_certificate is None
            and sheet.open_amendment is None,
            "can_new_certificate": approved
            and sheet.open_certificate is None
            and can_create_costs(ctx, project),
            "can_settle": approved
            and sheet.open_certificate is None
            and can_reverse_costs(ctx, project),
            "can_new_amendment": approved
            and sheet.open_amendment is None
            and can_create_costs(ctx, project),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def delete_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.delete_contract(ctx, project, contract_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return _detail(project, contract_id)
    messages.success(request, "Borrador borrado.")
    return redirect("contracts:list", project.pk)


@login_required(login_url="platform_login")
@require_POST
def approve_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.approve_contract(ctx, project, contract_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato aprobado; el anticipo quedó fijado.")
    return _back(request, project, contract_id)


@login_required(login_url="platform_login")
@require_POST
def reject_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.reject_contract(ctx, project, contract_id, request.POST.get("motivo", ""))
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato devuelto a su autor con el motivo.")
    return _back(request, project, contract_id)


@login_required(login_url="platform_login")
@require_POST
def void_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.void_contract(ctx, project, contract_id, request.POST.get("motivo", ""))
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Contrato anulado.")
    return _detail(project, contract_id)


def _amendment_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    amendment: ContractAmendment | None,
) -> HttpResponse:
    catalog = load_catalog(ctx, project)
    tasks = assignable_tasks(ctx, project)
    lines = contract_lines(ctx, contract)
    errors: list[str] = []
    overlaps: list[str] = []
    action = request.POST.get("accion", "guardar") if request.method == "POST" else ""
    if request.method == "POST":
        data = more_rows(request.POST, action) or request.POST
        form = AmendmentForm(data, lines=lines)
        new_lines = LineFormSet(data, prefix="lineas")
        if action == "guardar" and all([form.is_valid(), new_lines.is_valid()]):
            rows, errors = read_lines(new_lines, catalog, outline_index(tasks))
            if not errors:
                try:
                    if amendment is None:
                        services.create_amendment(
                            ctx, project, contract.pk, form.amendment_data(rows),
                            catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                    else:
                        services.update_amendment(
                            ctx, project, contract.pk, amendment.pk, form.amendment_data(rows),
                            catalog=catalog, tasks=tasks,
                        )  # fmt: skip
                except ContractRejected as exc:
                    errors, overlaps = exc.errors, exc.overlaps
                else:
                    messages.success(request, "Otrosí guardado en borrador.")
                    return _detail(project, contract.pk, "otrosies")
    elif amendment is None:
        form = AmendmentForm(lines=lines, initial={"signed_on": timezone.localdate()})
        new_lines = LineFormSet(prefix="lineas", initial=[{}] * NEW_LINE_ROWS)
    else:
        form = AmendmentForm(lines=lines, initial=amendment_initial(amendment))
        own = list(
            scoped_qs(ctx, ContractLine)
            .filter(amendment=amendment)
            .select_related("item")
            .prefetch_related("task_links__task")
            .order_by("order")
        )
        new_lines = LineFormSet(prefix="lineas", initial=line_initial(own, catalog))
    return _render(
        request,
        ctx,
        "suno/contracts/amendment_form.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "amendment": amendment,
            "form": form,
            "new_lines": new_lines,
            "catalog": catalog,
            "errors": errors,
            "overlaps": overlaps,
            "show_errors": action == "guardar",
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_amendment(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    if not can_create_costs(ctx, project):
        raise PermissionDenied
    if contract.status != ContractStatus.APPROVED:
        messages.error(request, "Solo un contrato aprobado admite otrosíes.")
        return _detail(project, contract.pk)
    return _amendment_form(request, ctx, project, contract, None)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    amendment = get_amendment(ctx, contract, amendment_id)
    try:
        services.require_editable(ctx, amendment, can_update_cost)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return _detail(project, contract.pk, "otrosies")
    return _amendment_form(request, ctx, project, contract, amendment)


@login_required(login_url="platform_login")
@require_POST
def delete_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.delete_amendment(ctx, project, contract_id, amendment_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí borrado.")
    return _detail(project, contract_id, "otrosies")


@login_required(login_url="platform_login")
@require_POST
def approve_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.approve_amendment(ctx, project, contract_id, amendment_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí aprobado y aplicado al contrato.")
    return _back(request, project, contract_id, "otrosies")


@login_required(login_url="platform_login")
@require_POST
def reject_amendment(
    request: HttpRequest, project_id: UUID, contract_id: UUID, amendment_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        services.reject_amendment(
            ctx, project, contract_id, amendment_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Otrosí rechazado; su autor lo verá con el motivo.")
    return _back(request, project, contract_id, "otrosies")
```


`server/suno/contracts/urls.py`:

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/contratos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "contracts"

C = "<uuid:contract_id>/"
A = C + "otrosies/<uuid:amendment_id>/"

urlpatterns = [
    path("", views.contract_list, name="list"),
    path("nuevo/", views.new_contract, name="new"),
    path(C, views.contract_detail, name="detail"),
    path(C + "editar/", views.edit_contract, name="edit"),
    path(C + "borrar/", views.delete_contract, name="delete"),
    path(C + "aprobar/", views.approve_contract, name="approve"),
    path(C + "rechazar/", views.reject_contract, name="reject"),
    path(C + "anular/", views.void_contract, name="void"),
    path(C + "otrosies/nuevo/", views.new_amendment, name="new_amendment"),
    path(A + "editar/", views.edit_amendment, name="edit_amendment"),
    path(A + "borrar/", views.delete_amendment, name="delete_amendment"),
    path(A + "aprobar/", views.approve_amendment, name="approve_amendment"),
    path(A + "rechazar/", views.reject_amendment, name="reject_amendment"),
]
```

En `server/config/urls.py`, después de la ruta de `costos/`:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/contratos/",
        include("suno.contracts.urls"),
    ),
```

En `server/suno/core/templates/suno/projects/_header.html`, después del enlace de "Costos":

```html
      {% if can_view_costs %}<a href="{% url 'contracts:list' project.pk %}" {% if tab == "contracts" %}aria-current="page"{% endif %}>Contratos</a>{% endif %}
```

- [ ] **Step 6: Plantillas**

Primero, un filtro para mostrar cantidades (con `.normalize()` un 10 sale como `1E+1`). En `server/suno/core/templatetags/money_tags.py`, al final:

```python
@register.filter
def qty(value: Any) -> str:
    """Una cantidad sin ceros de sobra, con punto de miles y coma decimal: `1234.5000` →
    `1.234,5`; vacío si no hay valor (costos, pieza 6b)."""
    if value is None or value == "":
        return ""
    amount = Decimal(value).normalize()
    exponent = amount.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    return floatformat(amount, f"{places}g")
```

y su prueba en `server/suno/control/tests/test_money_tags.py` (importar `qty` junto a `money` y `signed`):

```python
    def test_quantities(self) -> None:
        self.assertEqual(qty(D("1234.5000")), "1.234,5")
        self.assertEqual(qty(D("10.0000")), "10")
        self.assertEqual(qty(D("0.0000")), "0")
        self.assertEqual(qty(None), "")
```

Agregar `server/suno/core/templatetags/money_tags.py` y `server/suno/control/tests/test_money_tags.py` a los archivos del commit de esta tarea.


`server/suno/core/templates/suno/contracts/_nav.html`:

```html
<nav class="pj-tabs ctr-nav" aria-label="Contratos">
  <a href="{% url 'contracts:list' project.pk %}" {% if sub == "list" %}aria-current="page"{% endif %}>Contratos</a>
  {% if can_create_contract %}<a href="{% url 'contracts:new' project.pk %}" {% if sub == "new" %}aria-current="page"{% endif %}>Nuevo contrato</a>{% endif %}
  {% if can_decide_contract %}<a href="{% url 'costs:inbox' project.pk %}">Por aprobar</a>{% endif %}
</nav>
```

`server/suno/core/templates/suno/contracts/_alerts.html`:

```html
{% if alerts %}
<details class="pj-warning ctr-warning" open>
  <summary>{{ alerts|length }} aviso{{ alerts|length|pluralize:"s" }} de contratos</summary>
  <ul class="ctr-alerts">
    {% for alert in alerts %}<li>{% if alert.contract_id %}<a href="{% url 'contracts:detail' project.pk alert.contract_id %}">{{ alert.text }}</a>{% else %}{{ alert.text }}{% endif %}</li>{% endfor %}
  </ul>
</details>
{% endif %}
```

`server/suno/core/templates/suno/contracts/list.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Contratos · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="list" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Filtros de contratos">
    <label>Tipo <select name="tipo"><option value="">Todos</option>{% for key, label in kinds %}<option value="{{ key }}" {% if filters.kind == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Estado <select name="estado"><option value="">Todos</option>{% for key, label in statuses %}<option value="{{ key }}" {% if filters.status == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Filtrar</button>
  </form>
  {% include "suno/contracts/_alerts.html" %}
</section>
<section class="pj-panel">
  {% if rows %}
  <div class="pj-scroll">
  <table class="pj-table ctr-table">
    <caption class="sr-only">Contratos del proyecto</caption>
    <thead><tr><th>Número</th><th>Tipo</th><th>Contratista</th><th>Objeto</th><th class="num">Valor vigente</th><th class="num">Ejecutado</th><th class="num">%</th><th class="num">Anticipo por amortizar</th><th class="num">Retenido</th><th>Estado</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>
        <td><a href="{% url 'contracts:detail' project.pk row.contract.pk %}">{{ row.contract.number }}</a></td>
        <td>{{ row.contract.get_kind_display }}</td>
        <td class="pj-wrap">{{ row.contract.contractor }}</td>
        <td class="pj-name">{{ row.contract.purpose }}</td>
        <td class="num">{{ row.contract.current_value|signed }}</td>
        <td class="num">{{ row.progress.executed|signed }}</td>
        <td class="num">{% if row.executed_pct is not None %}{{ row.executed_pct|floatformat:1 }}{% else %}—{% endif %}</td>
        <td class="num">{{ row.advance_balance|signed }}</td>
        <td class="num">{{ row.retained|signed }}</td>
        <td><span class="bg-label ctr-{{ row.contract.status }}">{{ row.contract.get_status_display }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="pj-empty">No hay contratos con estos filtros.{% if can_create_contract %} <a href="{% url 'contracts:new' project.pk %}">Registra uno</a>.{% endif %}</p>
  {% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/contracts/_rows.html` (filas de líneas, compartidas por el contrato y el otrosí; `formset` es el de líneas):

```html
{{ formset.management_form }}
<div class="pj-scroll">
<table class="pj-table ctr-lines">
  <thead><tr><th>#</th><th>Ítem</th><th>Tareas</th><th>Unidad</th><th class="num">Cantidad</th><th class="num">Precio unitario</th></tr></thead>
  <tbody>
  {% for row in formset %}
    <tr>
      <td>{{ forloop.counter }}</td>
      <td class="ctr-item"><label class="sr-only" for="{{ row.item.id_for_label }}">Ítem de la línea {{ forloop.counter }}</label>{{ row.item }}</td>
      <td><label class="sr-only" for="{{ row.tasks.id_for_label }}">Tareas de la línea {{ forloop.counter }}</label>{{ row.tasks }}</td>
      <td class="ctr-unit"><label class="sr-only" for="{{ row.unit.id_for_label }}">Unidad de la línea {{ forloop.counter }}</label>{{ row.unit }}</td>
      <td class="ctr-num"><label class="sr-only" for="{{ row.quantity.id_for_label }}">Cantidad de la línea {{ forloop.counter }}</label>{{ row.quantity }}</td>
      <td class="ctr-num"><label class="sr-only" for="{{ row.unit_price.id_for_label }}">Precio de la línea {{ forloop.counter }}</label>{{ row.unit_price }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
<datalist id="ctr-items">{% for row in catalog.items %}<option value="{{ row.label }}">{% endfor %}</datalist>
<p class="pj-muted">Tareas: números de esquema separados por comas (por ejemplo 1.2, 2.1). Vacío: todas las tareas del ítem.</p>
```

`server/suno/core/templates/suno/contracts/_terms.html` (cargos o descuentos; `formset`, `title`, `action`):

```html
<fieldset class="ctr-terms">
  <legend>{{ title }}</legend>
  {{ formset.management_form }}
  <div class="pj-scroll">
  <table class="pj-table">
    <thead><tr><th>#</th><th>Nombre</th><th class="num">%</th><th>Base</th></tr></thead>
    <tbody>
    {% for row in formset %}
      <tr>
        <td>{{ forloop.counter }}</td>
        <td><label class="sr-only" for="{{ row.name.id_for_label }}">Nombre {{ forloop.counter }}</label>{{ row.name }}</td>
        <td class="ctr-num"><label class="sr-only" for="{{ row.pct.id_for_label }}">Porcentaje {{ forloop.counter }}</label>{{ row.pct }}</td>
        <td><label class="sr-only" for="{{ row.base.id_for_label }}">Base {{ forloop.counter }}</label>{{ row.base }}</td>
      </tr>
    {% empty %}
      <tr><td colspan="4" class="pj-muted">Sin filas.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  <button class="text-link" type="submit" name="accion" value="{{ action }}">Agregar fila</button>
</fieldset>
```

`server/suno/core/templates/suno/contracts/form.html`:

```html
{% extends "suno/projects/base.html" %}
{% block title %}{% if contract %}Editar contrato{% else %}Nuevo contrato{% endif %} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="new" %}
{% if not contract %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Paso 1: tipo y plantillas">
    <label>Tipo <select name="tipo">{% for key, label in kinds %}<option value="{{ key }}" {% if form.kind.value == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Cargos <select name="plantilla">{% for key, label in charge_templates.items %}<option value="{{ key }}" {% if request.GET.plantilla == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <label>Descuentos <select name="descuentos">{% for key, label in deduction_templates.items %}<option value="{{ key }}" {% if request.GET.descuentos == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Aplicar</button>
  </form>
  <p class="pj-muted">Un destajo arranca sin cargos, sin anticipo y sin garantía. Las plantillas se pueden ajustar abajo.</p>
</section>
{% endif %}
<section class="pj-panel">
  <h2>{% if contract %}Editar contrato {{ contract.number }}{% else %}Nuevo contrato{% endif %}</h2>
  {% if contract.rejection_note %}<p class="pj-warning">Devuelto por {{ contract.rejected_by }}: {{ contract.rejection_note }}. Al guardarlo queda listo para aprobar otra vez.</p>{% endif %}
  <form method="post" class="ctr-form">
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><p>No se pudo guardar:</p><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    {% if overlaps %}
    <div class="pj-warning" role="alert">
      <p>Otro contrato (o línea) ya cubre lo mismo:</p>
      <ul>{% for o in overlaps %}<li>{{ o }}</li>{% endfor %}</ul>
      <label class="cst-check">{{ form.confirm_overlap }} {{ form.confirm_overlap.label }}</label>
    </div>
    {% endif %}
    <div class="pj-form ctr-head">
      <label>{{ form.kind.label }} {{ form.kind }}</label>
      <label>{{ form.number.label }} {{ form.number }}</label>
      <label>{{ form.purpose.label }} {{ form.purpose }}</label>
      <label>{{ form.contractor.label }} {{ form.contractor }}</label>
      <label>{{ form.starts_on.label }} {{ form.starts_on }}</label>
      <label>{{ form.ends_on.label }} {{ form.ends_on }}</label>
      <label>{{ form.advance_pct.label }} {{ form.advance_pct }}</label>
      <label>{{ form.retention_pct.label }} {{ form.retention_pct }}</label>
    </div>
    <h3>Líneas</h3>
    {% include "suno/contracts/_rows.html" with formset=lines %}
    <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
    {% include "suno/contracts/_terms.html" with formset=charges title="Cargos (AIU, IVA…)" action="cargos" %}
    {% include "suno/contracts/_terms.html" with formset=deductions title="Descuentos de cada acta (retenciones, estampillas…)" action="descuentos" %}
    <p class="pj-muted">Anticipo y retención de garantía se descuentan en cada acta; no son costo.</p>
    <div class="ctr-actions"><button class="button" type="submit" name="accion" value="guardar">Guardar borrador</button>
    <a href="{% if contract %}{% url 'contracts:detail' project.pk contract.pk %}{% else %}{% url 'contracts:list' project.pk %}{% endif %}">Cancelar</a></div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/contracts/detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{{ contract.number }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="detail" %}
<section class="pj-panel">
  <h2 class="pj-name">{{ contract.number }} · {{ contract.purpose }}</h2>
  <p><span class="bg-label ctr-{{ contract.status }}">{{ contract.get_status_display }}</span> {{ contract.get_kind_display }} con {{ contract.contractor }}</p>
  {% if contract.rejection_note %}<p class="pj-warning">Devuelto por {{ contract.rejected_by }}: {{ contract.rejection_note }}</p>{% endif %}
  {% if contract.status == "voided" %}<p class="pj-muted">Anulado por {{ contract.closed_by }}: {{ contract.close_note }}</p>{% endif %}
  <div class="ctr-actions">
    {% if can_edit %}<a class="button" href="{% url 'contracts:edit' project.pk contract.pk %}">Editar</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'contracts:delete' project.pk contract.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar borrador</button></form>{% endif %}
    {% if can_decide %}
      <form method="post" action="{% url 'contracts:approve' project.pk contract.pk %}">{% csrf_token %}<button class="button" type="submit">Aprobar</button></form>
      <form method="post" action="{% url 'contracts:reject' project.pk contract.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Rechazar</button></form>
    {% endif %}
    {% if can_new_amendment %}<a class="button" href="{% url 'contracts:new_amendment' project.pk contract.pk %}">Nuevo otrosí</a>{% endif %}
    {% if can_void %}<form method="post" action="{% url 'contracts:void' project.pk contract.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo de la anulación <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Anular contrato</button></form>{% endif %}
  </div>
  <nav class="pj-tabs" aria-label="Ficha del contrato">
    {% for key, label in tabs.items %}<a href="?pestana={{ key }}" {% if tab == key %}aria-current="page"{% endif %}>{{ label }}</a>{% endfor %}
  </nav>
</section>
<section class="pj-panel">
{% if tab == "resumen" %}
  <dl class="cst-facts">
    <dt>Vigencia</dt><dd>{{ contract.starts_on|date:"d/m/Y" }} – {{ contract.ends_on|date:"d/m/Y" }}</dd>
    <dt>Valor vigente</dt><dd>{{ contract.current_value|money }}</dd>
    <dt>Ejecutado (actas aprobadas)</dt><dd>{{ sheet.progress.executed|money }}{% if sheet.executed_pct is not None %} · {{ sheet.executed_pct|floatformat:1 }} %{% endif %}</dd>
    <dt>Anticipo</dt><dd>{{ contract.advance_pct|qty }} %{% if contract.status != "draft" %} · {{ contract.advance_amount|money }}{% endif %}</dd>
    <dt>Anticipo por amortizar</dt><dd>{{ sheet.advance_balance|money }}</dd>
    <dt>Retención de garantía</dt><dd>{{ contract.retention_pct|qty }} % · retenido {{ sheet.progress.retained|money }}</dd>
    <dt>Creó</dt><dd>{{ contract.created_by }} · {{ contract.created_at|date:"d/m/Y H:i" }}</dd>
    {% if contract.approved_by %}<dt>Aprobó</dt><dd>{{ contract.approved_by }} · {{ contract.approved_at|date:"d/m/Y H:i" }}</dd>{% endif %}
  </dl>
  <h3>Cargos</h3>
  {% if charge_texts %}<ul>{% for text in charge_texts %}<li>{{ text }}</li>{% endfor %}</ul>{% else %}<p class="pj-muted">Sin cargos.</p>{% endif %}
  <h3>Descuentos de cada acta</h3>
  {% if deduction_texts %}<ul>{% for text in deduction_texts %}<li>{{ text }}</li>{% endfor %}</ul>{% else %}<p class="pj-muted">Sin descuentos.</p>{% endif %}
{% elif tab == "lineas" %}
  <div class="pj-scroll">
  <table class="pj-table ctr-table">
    <thead><tr><th>#</th><th>Ítem</th><th>Tareas</th><th>Unidad</th><th class="num">Precio</th><th class="num">Contratado</th><th class="num">Pagado</th><th class="num">Saldo</th><th class="num">Saldo ($)</th>{% if contract.status == "approved" %}<th class="num">Validado sin pagar</th>{% endif %}</tr></thead>
    <tbody>
    {% for line in sheet.lines %}
      <tr>
        <td>{{ line.line.order }}</td>
        <td class="pj-name">{{ line.label }}{% if line.line.amendment_id %} <small class="cst-mark">otrosí</small>{% endif %}</td>
        <td class="pj-wrap">{{ line.tasks }}</td>
        <td>{{ line.line.unit }}</td>
        <td class="num">{{ line.line.unit_price|signed }}</td>
        <td class="num">{{ line.contracted|qty }}</td>
        <td class="num">{{ line.paid|qty }}</td>
        <td class="num">{{ line.balance|qty }}</td>
        <td class="num">{{ line.balance_value|signed }}</td>
        {% if contract.status == "approved" %}<td class="num{% if line.unpaid_validated %} pj-late{% endif %}">{{ line.unpaid_validated|qty }}</td>{% endif %}
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
{% elif tab == "actas" %}
  {% include "suno/contracts/_certificates.html" %}
{% elif tab == "otrosies" %}
  {% if can_new_amendment %}<p><a class="button" href="{% url 'contracts:new_amendment' project.pk contract.pk %}">Nuevo otrosí</a></p>{% endif %}
  {% for a in sheet.amendments %}
  <article class="ctr-amendment">
    <h3>Otrosí {{ a.number }} · {{ a.signed_on|date:"d/m/Y" }} <span class="bg-label ctr-{{ a.status }}">{{ a.get_status_display }}</span></h3>
    <p class="pj-name">{{ a.reason }}</p>
    {% if a.status == "rejected" %}<p class="pj-warning">Rechazado: {{ a.decision_note }}</p>{% endif %}
    {% if a.value_after %}<p class="pj-muted">Valor vigente después: {{ a.value_after|money }}</p>{% endif %}
    {% if a.status != "approved" %}
    <div class="ctr-actions">
      {% if a.created_by_id == request.user.pk or can_decide_contract %}<a class="text-link" href="{% url 'contracts:edit_amendment' project.pk contract.pk a.pk %}">Editar</a>
      <form method="post" action="{% url 'contracts:delete_amendment' project.pk contract.pk a.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar</button></form>{% endif %}
      {% if a.status == "draft" and can_decide_contract and a.created_by_id != request.user.pk and a.updated_by_id != request.user.pk %}
      <form method="post" action="{% url 'contracts:approve_amendment' project.pk contract.pk a.pk %}">{% csrf_token %}<button class="button" type="submit">Aprobar</button></form>
      <form method="post" action="{% url 'contracts:reject_amendment' project.pk contract.pk a.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Rechazar</button></form>
      {% endif %}
    </div>
    {% endif %}
  </article>
  {% empty %}<p class="pj-empty">Sin otrosíes.</p>{% endfor %}
{% else %}
  {% if history %}
  <div class="pj-scroll">
  <table class="pj-table">
    <thead><tr><th>Fecha</th><th>Qué pasó</th><th>Quién</th><th>Detalle</th></tr></thead>
    <tbody>{% for event, label in history %}<tr><td>{{ event.created_at|date:"d/m/Y H:i" }}</td><td>{{ label }}</td><td class="pj-wrap">{{ event.actor|default:"Sistema" }}</td><td class="pj-wrap">{% if event.metadata.number %}N.º {{ event.metadata.number }}{% endif %}{% if event.metadata.note %} · {{ event.metadata.note }}{% endif %}</td></tr>{% endfor %}</tbody>
  </table>
  </div>
  {% else %}<p class="pj-empty">Sin eventos.</p>{% endif %}
{% endif %}
</section>
{% endblock %}
```

`server/suno/core/templates/suno/contracts/_certificates.html` (la pestaña "Actas"; en esta tarea solo la lista, sin enlaces: las vistas de actas llegan en la Task 9, que reemplaza este archivo):

```html
{% load money_tags %}
{% if sheet.certificates %}
<div class="pj-scroll">
<table class="pj-table ctr-table">
  <thead><tr><th>Acta</th><th>Periodo</th><th class="num">Bruto</th><th class="num">Neto</th><th>Estado</th><th>Preparó</th></tr></thead>
  <tbody>
  {% for c in sheet.certificates %}
    <tr>
      <td>Acta {{ c.number }}{% if c.is_settlement %} <small class="cst-mark">liquidación</small>{% endif %}</td>
      <td>{{ c.period_start|date:"d/m/Y" }} – {{ c.period_end|date:"d/m/Y" }}</td>
      <td class="num">{{ c.gross|signed }}</td>
      <td class="num">{{ c.net|signed }}</td>
      <td><span class="bg-label ctr-{{ c.status }}">{{ c.get_status_display }}</span></td>
      <td class="pj-wrap">{{ c.created_by }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}<p class="pj-empty">Todavía no hay actas.</p>{% endif %}
```

`server/suno/core/templates/suno/contracts/amendment_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Otrosí · {{ contract.number }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="detail" %}
<section class="pj-panel">
  <h2>{% if amendment %}Otrosí {{ amendment.number }}{% else %}Nuevo otrosí{% endif %} · {{ contract.number }}</h2>
  {% if amendment.status == "rejected" %}<p class="pj-warning">Rechazado: {{ amendment.decision_note }}. Al guardarlo vuelve a borrador.</p>{% endif %}
  <form method="post" class="ctr-form">
    {% csrf_token %}
    {% if show_errors %}{% if errors or form.errors %}<div class="pj-late" role="alert"><p>No se pudo guardar:</p><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}{% endif %}
    {% if overlaps %}<div class="pj-warning" role="alert"><ul>{% for o in overlaps %}<li>{{ o }}</li>{% endfor %}</ul><label class="cst-check">{{ form.confirm_overlap }} {{ form.confirm_overlap.label }}</label></div>{% endif %}
    <div class="pj-form">
      <label>{{ form.signed_on.label }} {{ form.signed_on }}</label>
      <label>{{ form.reason.label }} {{ form.reason }}</label>
      <label>{{ form.new_ends_on.label }} {{ form.new_ends_on }} <small class="pj-muted">Hoy termina el {{ contract.ends_on|date:"d/m/Y" }}.</small></label>
    </div>
    <h3>Aumentos de cantidad</h3>
    <div class="pj-scroll">
    <table class="pj-table ctr-lines">
      <thead><tr><th>#</th><th>Ítem</th><th class="num">Vigente</th><th class="num">Aumento</th></tr></thead>
      <tbody>{% for line, field in form.increase_fields %}<tr><td>{{ line.order }}</td><td class="pj-name">{{ line.item.code }}</td><td class="num">{{ line.current_quantity|qty }} {{ line.unit }}</td><td class="ctr-num"><label class="sr-only" for="{{ field.id_for_label }}">Aumento de la línea {{ line.order }}</label>{{ field }}</td></tr>{% endfor %}</tbody>
    </table>
    </div>
    <h3>Líneas nuevas</h3>
    {% include "suno/contracts/_rows.html" with formset=new_lines %}
    <button class="text-link" type="submit" name="accion" value="lineas">Agregar líneas</button>
    <div class="ctr-actions"><button class="button" type="submit" name="accion" value="guardar">Guardar borrador</button> <a href="{% url 'contracts:detail' project.pk contract.pk %}?pestana=otrosies">Cancelar</a></div>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 7: Estilos**

Al final de `server/suno/core/static/suno/css/projects.css` (antes del bloque `@media` final si lo hay, o en uno propio):

```css
/* Contratos (costos, pieza 6b) */
.ctr-nav { margin: 0 0 16px; }
.ctr-form { display: grid; gap: 16px; }
.ctr-head { max-width: 720px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.ctr-table td.pj-name { min-width: 200px; max-width: 320px; }
.ctr-lines input, .ctr-terms input, .ctr-terms select { width: 100%; min-width: 0; min-height: var(--suno-control-height); padding: 6px 8px; border: 1px solid var(--suno-line-strong); border-radius: var(--suno-radius-sm); font: inherit; }
.ctr-lines td, .ctr-terms td { vertical-align: top; }
.ctr-lines .ctr-item { min-width: 260px; }
.ctr-lines .ctr-num, .ctr-terms .ctr-num { min-width: 110px; }
.ctr-lines .ctr-unit { min-width: 70px; }
.ctr-terms { border: 1px solid var(--suno-line); border-radius: var(--suno-radius-sm); padding: 8px 12px; }
.ctr-totals { display: grid; grid-template-columns: minmax(0, 1fr) max-content; gap: 4px 16px; max-width: 520px; margin: 12px 0; font-size: 13px; }
.ctr-totals dt { color: var(--suno-muted); }
.ctr-totals dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.ctr-totals .ctr-net { font-weight: 600; color: var(--suno-ink); }
.ctr-alerts { margin: 8px 0 0; padding-left: 18px; }
.ctr-alerts li { margin: 2px 0; overflow-wrap: anywhere; }
.ctr-warning { margin-top: 12px; }
.ctr-diff { color: var(--suno-red); }
.ctr-actions { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; margin: 12px 0; }
.ctr-actions form { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.ctr-amendment { padding: 12px 0; border-bottom: 1px solid var(--suno-line); }
.bg-label.ctr-draft { background: var(--suno-line-strong); color: var(--suno-ink); }
.bg-label.ctr-approved { background: var(--suno-green); }
.bg-label.ctr-rejected, .bg-label.ctr-voided { background: var(--suno-red); }
.bg-label.ctr-settled { background: var(--chart-petrol); }
@media (max-width: 640px) {
  .ctr-table td.pj-name { min-width: 160px; max-width: 200px; }
  .ctr-head { grid-template-columns: minmax(0, 1fr); }
  .ctr-totals { max-width: none; }
}
```

En `server/suno/core/templates/suno/projects/base.html`, cambiar `projects.css' %}?v=20261113-costs-v5` por `projects.css' %}?v=20261114-contracts-v1`.

- [ ] **Step 8: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts config && ruff check suno config && mypy suno/contracts config --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts suno/costs/tests/test_views.py suno/projects/tests suno/control/tests/test_money_tags.py suno/core/tests/test_architecture.py
```

Expected: PASS (las pruebas de la cabecera del proyecto siguen pasando: la pestaña nueva solo aparece con permiso de costos).

- [ ] **Step 9: Commit**

```bash
git add server/config/urls.py server/suno/contracts server/suno/core/templates/suno/contracts server/suno/core/templates/suno/projects server/suno/core/static/suno/css/projects.css server/suno/core/templatetags/money_tags.py server/suno/control/tests/test_money_tags.py
git commit -m "feat(contracts): Contracts tab with list, contract form, sheet and amendments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Actas en la web: nueva acta con "Recalcular", ficha, firmado, decisiones y bandeja

**Files:**
- Modify: `server/suno/contracts/views.py`, `server/suno/contracts/urls.py`
- Create: `server/suno/core/templates/suno/contracts/{_totals,certificate_form,certificate_detail}.html`
- Modify (reemplazar): `server/suno/core/templates/suno/contracts/_certificates.html`
- Modify: `server/suno/costs/views.py` (`_context`, `inbox`), `server/suno/core/templates/suno/costs/inbox.html`
- Test: `server/suno/contracts/tests/test_views_certificates.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_views_certificates.py`:

```python
"""Actas en la web (spec, 2.4–2.7): propuesta, "Recalcular", ficha, firmado, bandeja, anular."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings

from suno.core.models import CertificateStatus, ContractCertificate, CostEntry
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import pdf_upload

from .factories import contract_line, contract_world, field_quantity, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TODAY = date(2026, 4, 15)


@skip_without_db
class CertificatePagesTests(TestCase):
    def setUp(self) -> None:
        patcher = mock.patch("django.utils.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        overrides = override_settings(STORAGE_BACKEND="localfs", STORAGE_LOCAL_ROOT=self._tmp.name)
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.cw = contract_world()
        self.world = self.cw.world
        self.contract = make_contract(self.cw)
        self.concrete = contract_line(self.contract, "2.1")
        field_quantity(self.cw, 5, "2.1", "4", date(2026, 3, 10))
        field_quantity(self.cw, 3, "2.2", "200", date(2026, 3, 12))
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/contratos/{self.contract.pk}/"
        self.new = self.base + "actas/nueva/"
        self.client.force_login(self.cw.contributor)

    def save(self, **extra: str) -> ContractCertificate:
        self.client.post(
            self.new, {"accion": "guardar", "desde": "2026-03-01", "hasta": "2026-03-31", **extra}
        )
        return ContractCertificate.objects.get()

    def test_new_certificate_proposes_and_recalculates(self) -> None:
        page = self.client.get(self.new + "?desde=2026-03-01&hasta=2026-03-31")
        self.assertContains(page, "2.781.850,00")  # bruto con la propuesta
        self.assertContains(page, "1.736.707,00")  # neto
        self.assertContains(page, 'value="2026-03-01"')
        default = self.client.get(self.new)
        self.assertContains(default, 'value="2026-03-01"')  # inicio del contrato
        self.assertContains(default, 'value="2026-04-15"')  # hoy
        recalculated = self.client.post(
            self.new,
            {"accion": "calcular", "desde": "2026-03-01", "hasta": "2026-03-31",
             f"cantidad_{self.concrete.pk}": "5"},
        )  # fmt: skip
        self.assertContains(recalculated, "escribe la justificación")
        self.assertFalse(ContractCertificate.objects.exists())

    def test_save_with_justification(self) -> None:
        certificate = self.save(
            **{f"cantidad_{self.concrete.pk}": "5", f"justificacion_{self.concrete.pk}": "Medido"}
        )
        self.assertEqual(certificate.status, CertificateStatus.DRAFT)
        detail = self.client.get(f"{self.base}actas/{certificate.pk}/")
        self.assertContains(detail, "Acta 1")
        self.assertContains(detail, "Medido")
        self.assertContains(detail, 'name="signed"')

    def test_signed_copy_inbox_approval_and_ledger(self) -> None:
        certificate = self.save()
        url = f"{self.base}actas/{certificate.pk}/"
        self.client.post(url + "firmada/", {"signed": pdf_upload("acta-1.pdf")})
        certificate.refresh_from_db()
        self.assertEqual(certificate.attachment_name, "acta-1.pdf")
        self.assertEqual(self.client.get(url + "soporte/").status_code, 302)
        self.client.force_login(self.world.supervisor)
        inbox = self.client.get(f"/plataforma/proyectos/{self.world.project.pk}/costos/por-aprobar/")
        self.assertContains(inbox, "Contratos, otrosíes y actas")
        self.assertContains(inbox, "Acta 1 · SC-001")
        self.assertContains(inbox, "Por aprobar (1)")
        response = self.client.post(url + "aprobar/", {"volver": "bandeja"})
        self.assertRedirects(
            response, f"/plataforma/proyectos/{self.world.project.pk}/costos/por-aprobar/"
        )
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.APPROVED)
        self.assertEqual(CostEntry.objects.filter(certificate=certificate).count(), 2)
        self.assertContains(self.client.get(url), "Movimientos en el libro")

    def test_reject_and_void(self) -> None:
        certificate = self.save()
        url = f"{self.base}actas/{certificate.pk}/"
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "rechazar/", {"motivo": "Revisar el 2.2"})
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.REJECTED)
        self.client.force_login(self.cw.contributor)
        self.client.post(url + "editar/", {"accion": "guardar", "desde": "2026-03-01",
                                           "hasta": "2026-03-31"})  # fmt: skip
        self.client.post(url + "firmada/", {"signed": pdf_upload()})
        self.client.force_login(self.world.supervisor)
        self.client.post(url + "aprobar/")
        self.assertEqual(self.client.post(url + "anular/", {"motivo": "x"}).status_code, 403)
        self.client.force_login(self.world.admin)
        self.client.post(url + "anular/", {"motivo": "Error de medición"})
        certificate.refresh_from_db()
        self.assertEqual(certificate.status, CertificateStatus.VOIDED)

    def test_settlement_is_for_the_manager_and_viewer_reads(self) -> None:
        self.client.force_login(self.world.supervisor)
        self.assertEqual(self.client.get(self.new + "?liquidacion=1").status_code, 403)
        self.client.force_login(self.world.admin)
        self.assertContains(self.client.get(self.new + "?liquidacion=1"), "Acta de liquidación")
        certificate = self.save()
        self.client.force_login(self.cw.viewer)
        self.assertEqual(self.client.get(f"{self.base}actas/{certificate.pk}/").status_code, 200)
        self.assertEqual(self.client.get(self.new).status_code, 403)

    def test_actas_tab_links(self) -> None:
        certificate = self.save()
        tab = self.client.get(self.base + "?pestana=actas")
        self.assertContains(tab, f"actas/{certificate.pk}/")
        self.assertNotContains(tab, "Nueva acta")  # ya hay una abierta
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_views_certificates.py`
Expected: FAIL (404: `actas/nueva/` no existe).

- [ ] **Step 3: Las vistas de actas**

En `server/suno/contracts/views.py`, agregar a los imports:

```python
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404

from suno.core.models import CertificateLine, CertificateStatus, ContractCertificate, CostEntry
from suno.core.models.contract import OPEN_STATES
from suno.projects.views import _parse_date

from . import certificates
from .access import get_certificate
from .certificates import CertificateData, CertificateLineData, build_draft, default_period
from .forms import CertificateForm, SignedForm
from .queries import certificate_document
```

(unidos a los imports que ya hay del mismo módulo, en orden), y al final del archivo:

```python
# ---------------------------------------------------------------------------
# Actas
# ---------------------------------------------------------------------------


def _certificate_url(project: Project, contract_id: UUID, certificate_id: UUID) -> str:
    return reverse("contracts:certificate", args=[project.pk, contract_id, certificate_id])


def _certificate_form(
    request: HttpRequest,
    ctx: RequestContext,
    project: Project,
    contract: Contract,
    certificate: ContractCertificate | None,
    settlement: bool,
) -> HttpResponse:
    """Paso 1 (GET): el periodo. Paso 2 (POST): cantidades; "Recalcular" no guarda."""
    catalog = load_catalog(ctx, project)
    lines = contract_lines(ctx, contract)
    params = request.POST if request.method == "POST" else request.GET
    default = (
        (certificate.period_start, certificate.period_end)
        if certificate is not None
        else default_period(ctx, contract)
    )
    start = _parse_date(params.get("desde")) or default[0]
    end = _parse_date(params.get("hasta")) or default[1]
    initial: dict[str, Any] = {}
    stored: tuple[CertificateLineData, ...] = ()
    if certificate is not None and request.method != "POST":
        rows = list(scoped_qs(ctx, CertificateLine).filter(certificate=certificate))
        for row in rows:
            if row.quantity != row.proposed_quantity:
                initial[f"cantidad_{row.line_id}"] = plain(row.quantity)
                initial[f"justificacion_{row.line_id}"] = row.justification
        stored = tuple(
            CertificateLineData(row.line_id, row.quantity, row.justification)
            for row in rows
            if row.quantity != row.proposed_quantity
        )
    form = CertificateForm(request.POST or None, lines=lines, initial=initial)
    action = request.POST.get("accion", "") if request.method == "POST" else ""
    valid = form.is_valid() if request.method == "POST" else False
    given = form.certificate_lines() if valid else stored
    draft = build_draft(
        ctx,
        project,
        contract,
        CertificateData(start, end, given),
        settlement=settlement,
        catalog=catalog,
        exclude=certificate.pk if certificate is not None else None,
    )
    errors: list[str] = []
    if action == "guardar" and valid:
        data = CertificateData(start, end, given)
        try:
            if certificate is None:
                saved = certificates.create_certificate(
                    ctx, project, contract.pk, data, settlement=settlement, catalog=catalog
                )
            else:
                saved = certificates.update_certificate(
                    ctx, project, contract.pk, certificate.pk, data, catalog=catalog
                )
        except ContractRejected as exc:
            errors = exc.errors
        else:
            messages.success(
                request,
                "Acta guardada en borrador. Descarga el PDF, hazla firmar y súbela para aprobarla.",
            )
            return redirect(_certificate_url(project, contract.pk, saved.pk))
    return _render(
        request,
        ctx,
        "suno/contracts/certificate_form.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "certificate": certificate,
            "settlement": settlement,
            "draft": draft,
            "rows": [
                (line, form[f"cantidad_{line.line.pk}"], form[f"justificacion_{line.line.pk}"])
                for line in draft.lines
            ],
            "form": form,
            "errors": errors or (list(draft.errors) if action else []),
            "start": start,
            "end": end,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def new_certificate(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    params = request.POST if request.method == "POST" else request.GET
    settlement = params.get("liquidacion") == "1"
    if not can_create_costs(ctx, project) or (
        settlement and not can_reverse_costs(ctx, project)
    ):
        raise PermissionDenied
    if contract.status != ContractStatus.APPROVED:
        messages.error(request, "Solo un contrato aprobado admite actas.")
        return _detail(project, contract.pk, "actas")
    return _certificate_form(request, ctx, project, contract, None, settlement)


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def edit_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    try:
        services.require_editable(ctx, certificate, can_update_cost)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect(_certificate_url(project, contract.pk, certificate.pk))
    return _certificate_form(
        request, ctx, project, contract, certificate, certificate.is_settlement
    )


def _is_editable(ctx: RequestContext, certificate: ContractCertificate, delete: bool) -> bool:
    if certificate.status not in OPEN_STATES:
        return False
    if certificate.status == CertificateStatus.REJECTED and certificate.created_by_id != ctx.user_id:
        return False
    return can_delete_cost(ctx, certificate) if delete else can_update_cost(ctx, certificate)


@login_required(login_url="platform_login")
@require_GET
def certificate_detail(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    document = certificate_document(ctx, project, contract, certificate, load_catalog(ctx, project))
    draft = certificate.status == CertificateStatus.DRAFT
    mine = ctx.user_id in (certificate.created_by_id, certificate.updated_by_id)
    last = (
        scoped_qs(ctx, ContractCertificate)
        .filter(contract=contract, status=CertificateStatus.APPROVED)
        .order_by("-number")
        .values_list("pk", flat=True)
        .first()
    )
    return _render(
        request,
        ctx,
        "suno/contracts/certificate_detail.html",
        {
            **_context(ctx, project),
            "contract": contract,
            "certificate": certificate,
            "document": document,
            "signed_form": SignedForm(),
            "entries": list(
                scoped_qs(ctx, CostEntry)
                .filter(certificate=certificate)
                .order_by("incurred_on", "amount")
            ),
            "can_edit": _is_editable(ctx, certificate, delete=False),
            "can_delete": _is_editable(ctx, certificate, delete=True),
            "can_sign": draft and can_update_cost(ctx, certificate),
            "can_decide": draft and can_approve_costs(ctx, project) and not mine,
            "can_void": certificate.status == CertificateStatus.APPROVED
            and last == certificate.pk
            and can_reverse_costs(ctx, project),
        },
    )


@login_required(login_url="platform_login")
@require_POST
def upload_signed(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    form = SignedForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Elige el archivo del acta firmada.")
    else:
        upload = form.cleaned_data["signed"]
        try:
            certificates.attach_signed(
                ctx, project, contract_id, certificate_id, upload, upload.name or "acta.pdf"
            )
        except ContractRejected as exc:
            messages.error(request, exc.errors[0])
        else:
            messages.success(request, "Acta firmada adjunta; ya se puede aprobar.")
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_GET
def signed_copy(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    """Como el soporte del libro (6a): URL prefirmada; `local://` se arma a mano."""
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    url = certificates.signed_url(ctx, project, get_certificate(ctx, contract, certificate_id))
    scheme = urlsplit(url).scheme
    if scheme == "local":
        response = HttpResponse(status=302)
        response["Location"] = url
        return response
    if scheme == "https" or (scheme == "http" and settings.DEBUG):
        return redirect(url)
    raise Http404


def _certificate_back(
    request: HttpRequest, project: Project, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    if request.POST.get("volver") == "bandeja":
        return redirect("costs:inbox", project.pk)
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_POST
def approve_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        approved = certificates.approve_certificate(ctx, project, contract_id, certificate_id)
    except ContractRejected as exc:
        for error in exc.errors[:5]:
            messages.error(request, error)
    else:
        messages.success(
            request,
            "Acta aprobada; sus movimientos están en el libro."
            + (" El contrato quedó liquidado." if approved.is_settlement else ""),
        )
    return _certificate_back(request, project, contract_id, certificate_id)


@login_required(login_url="platform_login")
@require_POST
def reject_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.reject_certificate(
            ctx, project, contract_id, certificate_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Acta rechazada; su autor la verá con el motivo.")
    return _certificate_back(request, project, contract_id, certificate_id)


@login_required(login_url="platform_login")
@require_POST
def void_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.void_certificate(
            ctx, project, contract_id, certificate_id, request.POST.get("motivo", "")
        )
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
    else:
        messages.success(request, "Acta anulada; sus reversos están en el libro con fecha de hoy.")
    return redirect(_certificate_url(project, contract_id, certificate_id))


@login_required(login_url="platform_login")
@require_POST
def delete_certificate(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    try:
        certificates.delete_certificate(ctx, project, contract_id, certificate_id)
    except ContractRejected as exc:
        messages.error(request, exc.errors[0])
        return redirect(_certificate_url(project, contract_id, certificate_id))
    messages.success(request, "Borrador del acta borrado.")
    return _detail(project, contract_id, "actas")
```

En `server/suno/contracts/urls.py`, agregar `R = C + "actas/<uuid:certificate_id>/"` y al final de `urlpatterns`:

```python
    path(C + "actas/nueva/", views.new_certificate, name="new_certificate"),
    path(R, views.certificate_detail, name="certificate"),
    path(R + "editar/", views.edit_certificate, name="edit_certificate"),
    path(R + "borrar/", views.delete_certificate, name="delete_certificate"),
    path(R + "firmada/", views.upload_signed, name="upload_signed"),
    path(R + "soporte/", views.signed_copy, name="signed_copy"),
    path(R + "aprobar/", views.approve_certificate, name="approve_certificate"),
    path(R + "rechazar/", views.reject_certificate, name="reject_certificate"),
    path(R + "anular/", views.void_certificate, name="void_certificate"),
```

- [ ] **Step 4: La bandeja "Por aprobar" de Costos**

En `server/suno/costs/views.py`:

1. Import: `from suno.contracts.queries import inbox_count, inbox_items`.
2. En `_context`, cambiar el cálculo de `pending` para sumar lo de contratos:

```python
    pending = (
        entries.filter(status=CostStatus.DRAFT)
        .exclude(created_by_id=ctx.user_id)
        .exclude(updated_by_id=ctx.user_id)
        .count()
        + inbox_count(ctx, project)
        if can_decide
        else 0
    )
```

3. En `inbox`, agregar al contexto `"contract_items": inbox_items(ctx, project)`.

`suno.contracts` importa de `suno.costs` (catálogo, acceso, constantes del libro) y ahora `costs.views` importa de `suno.contracts.queries`: no hay ciclo porque `contracts.queries` no importa `costs.views`.

En `server/suno/core/templates/suno/costs/inbox.html`, después de la tabla de movimientos (antes de `</section>`), y cambiando el `{% else %}` final para que el vacío solo se diga si tampoco hay nada de contratos:

```html
  {% if contract_items.count %}
  <h3>Contratos, otrosíes y actas</h3>
  <div class="pj-scroll">
  <table class="pj-table cst-ledger">
    <thead><tr><th>Qué</th><th>Detalle</th><th class="num">Valor</th><th>Preparó</th><th>Soporte</th><th>Decisión</th></tr></thead>
    <tbody>
    {% for c in contract_items.contracts %}
      <tr>
        <td><a href="{% url 'contracts:detail' project.pk c.pk %}">Contrato {{ c.number }}</a></td>
        <td class="pj-wrap">{{ c.get_kind_display }} · {{ c.contractor }}</td>
        <td class="num">{{ c.current_value|signed }}</td>
        <td class="pj-wrap">{{ c.created_by }}</td>
        <td>—</td>
        <td class="cst-decide">
          <form method="post" action="{% url 'contracts:approve' project.pk c.pk %}">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><button class="button" type="submit">Aprobar</button></form>
          <form method="post" action="{% url 'contracts:reject' project.pk c.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><input aria-label="Motivo del rechazo" name="motivo" maxlength="500" required placeholder="Motivo del rechazo"><button class="text-link" type="submit">Rechazar</button></form>
        </td>
      </tr>
    {% endfor %}
    {% for a in contract_items.amendments %}
      <tr>
        <td><a href="{% url 'contracts:detail' project.pk a.contract_id %}?pestana=otrosies">Otrosí {{ a.number }} · {{ a.contract.number }}</a></td>
        <td class="pj-wrap">{{ a.reason }}</td>
        <td class="num">—</td>
        <td class="pj-wrap">{{ a.created_by }}</td>
        <td>—</td>
        <td class="cst-decide">
          <form method="post" action="{% url 'contracts:approve_amendment' project.pk a.contract_id a.pk %}">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><button class="button" type="submit">Aprobar</button></form>
          <form method="post" action="{% url 'contracts:reject_amendment' project.pk a.contract_id a.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><input aria-label="Motivo del rechazo" name="motivo" maxlength="500" required placeholder="Motivo del rechazo"><button class="text-link" type="submit">Rechazar</button></form>
        </td>
      </tr>
    {% endfor %}
    {% for c in contract_items.certificates %}
      <tr>
        <td><a href="{% url 'contracts:certificate' project.pk c.contract_id c.pk %}">Acta {{ c.number }} · {{ c.contract.number }}</a>{% if c.is_settlement %} <small class="cst-mark">liquidación</small>{% endif %}</td>
        <td class="pj-wrap">{{ c.period_start|date:"d/m/Y" }} – {{ c.period_end|date:"d/m/Y" }}{% if c.adjusted %} · <span class="ctr-diff">{{ c.adjusted }} línea{{ c.adjusted|pluralize:"s" }} distinta{{ c.adjusted|pluralize:"s" }} de lo validado</span>{% endif %}</td>
        <td class="num">{{ c.gross|signed }}</td>
        <td class="pj-wrap">{{ c.created_by }}</td>
        <td>{% if c.attachment_id %}<a href="{% url 'contracts:signed_copy' project.pk c.contract_id c.pk %}" target="_blank" rel="noopener">Acta firmada</a>{% else %}<span class="pj-late">Sin firmar</span>{% endif %}</td>
        <td class="cst-decide">
          <form method="post" action="{% url 'contracts:approve_certificate' project.pk c.contract_id c.pk %}">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><button class="button" type="submit"{% if not c.attachment_id %} disabled{% endif %}>Aprobar</button></form>
          <form method="post" action="{% url 'contracts:reject_certificate' project.pk c.contract_id c.pk %}" class="cst-reject">{% csrf_token %}<input type="hidden" name="volver" value="bandeja"><input aria-label="Motivo del rechazo" name="motivo" maxlength="500" required placeholder="Motivo del rechazo"><button class="text-link" type="submit">Rechazar</button></form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
```

y cambiar `{% else %}<p class="pj-empty">No hay borradores por aprobar.</p>{% endif %}` por `{% elif not contract_items.count %}<p class="pj-empty">No hay borradores por aprobar.</p>{% endif %}`.

- [ ] **Step 5: Plantillas de actas**

`server/suno/core/templates/suno/contracts/_certificates.html` (reemplaza la de la Task 8):

```html
{% load money_tags %}
<div class="ctr-actions">
  {% if can_new_certificate %}<a class="button" href="{% url 'contracts:new_certificate' project.pk contract.pk %}">Nueva acta</a>{% endif %}
  {% if can_settle %}<a class="text-link" href="{% url 'contracts:new_certificate' project.pk contract.pk %}?liquidacion=1">Liquidar</a>{% endif %}
</div>
{% if sheet.certificates %}
<div class="pj-scroll">
<table class="pj-table ctr-table">
  <thead><tr><th>Acta</th><th>Periodo</th><th class="num">Bruto</th><th class="num">Neto</th><th>Estado</th><th>Preparó</th></tr></thead>
  <tbody>
  {% for c in sheet.certificates %}
    <tr>
      <td><a href="{% url 'contracts:certificate' project.pk contract.pk c.pk %}">Acta {{ c.number }}</a>{% if c.is_settlement %} <small class="cst-mark">liquidación</small>{% endif %}</td>
      <td>{{ c.period_start|date:"d/m/Y" }} – {{ c.period_end|date:"d/m/Y" }}</td>
      <td class="num">{{ c.gross|signed }}</td>
      <td class="num">{{ c.net|signed }}</td>
      <td><span class="bg-label ctr-{{ c.status }}">{{ c.get_status_display }}</span></td>
      <td class="pj-wrap">{{ c.created_by }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}<p class="pj-empty">Todavía no hay actas.</p>{% endif %}
```

`server/suno/core/templates/suno/contracts/_totals.html` (los totales de un acta; recibe los importes y las líneas de cargos y descuentos, del borrador calculado o del acta guardada):

```html
{% load money_tags %}
<dl class="ctr-totals">
  <dt>Bruto directo</dt><dd>{{ direct|signed }}</dd>
  {% for c in charge_lines %}<dt>{{ c.name }} ({{ c.pct|qty }} %)</dt><dd>{{ c.amount|signed }}</dd>{% endfor %}
  <dt><strong>Bruto (va al libro)</strong></dt><dd><strong>{{ gross|signed }}</strong></dd>
  <dt>Amortización del anticipo</dt><dd>−{{ amortization|signed }}</dd>
  <dt>Retención de garantía</dt><dd>−{{ retention|signed }}</dd>
  {% for d in deduction_lines %}<dt>{{ d.name }} ({{ d.pct|qty }} %)</dt><dd>−{{ d.amount|signed }}</dd>{% endfor %}
  {% if returned %}<dt>Devolución de la retención</dt><dd>{{ returned|signed }}</dd>{% endif %}
  <dt class="ctr-net">Neto a pagar</dt><dd class="ctr-net">{{ net|signed }}</dd>
</dl>
```

`server/suno/core/templates/suno/contracts/certificate_form.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}{% if settlement %}Acta de liquidación{% else %}Acta{% endif %} · {{ contract.number }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="detail" %}
<section class="pj-panel">
  <h2>{% if settlement %}Acta de liquidación{% elif certificate %}Acta {{ certificate.number }}{% else %}Nueva acta{% endif %} · <a href="{% url 'contracts:detail' project.pk contract.pk %}">{{ contract.number }}</a></h2>
  {% if settlement %}<p class="pj-warning">La liquidación descuenta todo el anticipo que falte, devuelve lo retenido y deja el contrato liquidado al aprobarse. Puede ir con cantidades en cero.</p>{% endif %}
  {% if certificate.status == "rejected" %}<p class="pj-warning">Rechazada: {{ certificate.decision_note }}. Al guardarla vuelve a borrador.</p>{% endif %}
  <form method="get" class="pj-filters" aria-label="Paso 1: periodo">
    {% if settlement %}<input type="hidden" name="liquidacion" value="1">{% endif %}
    <label>Desde <input type="date" name="desde" value="{{ start|date:'Y-m-d' }}"></label>
    <label>Hasta <input type="date" name="hasta" value="{{ end|date:'Y-m-d' }}"></label>
    <button class="button" type="submit">Proponer cantidades</button>
  </form>
  <p class="pj-muted">La propuesta es lo validado en campo hasta el fin del periodo para el ítem y las tareas de cada línea, menos lo ya pagado.</p>
</section>
<section class="pj-panel">
  <form method="post" class="ctr-form">
    {% csrf_token %}
    <input type="hidden" name="desde" value="{{ start|date:'Y-m-d' }}">
    <input type="hidden" name="hasta" value="{{ end|date:'Y-m-d' }}">
    {% if settlement %}<input type="hidden" name="liquidacion" value="1">{% endif %}
    {% if errors or form.errors %}<div class="pj-late" role="alert"><ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}{% for field in form %}{% for e in field.errors %}<li>{{ field.label }}: {{ e }}</li>{% endfor %}{% endfor %}</ul></div>{% endif %}
    {% if draft.over_balance %}<p class="pj-warning">Hay líneas que pasan lo contratado vigente: no se podrá aprobar sin un otrosí.</p>{% endif %}
    <div class="pj-scroll">
    <table class="pj-table ctr-lines">
      <thead><tr><th>Ítem</th><th class="num">Saldo</th><th class="num">Validado en el periodo</th><th class="num">Propuesta</th><th class="num">Cantidad del acta</th><th>Justificación (si difiere)</th><th class="num">Valor</th></tr></thead>
      <tbody>
      {% for line, quantity, justification in rows %}
        <tr>
          <td class="pj-name">{{ line.label }}<br><small class="pj-muted">{{ line.line.unit }} · {{ line.line.unit_price|money }}</small></td>
          <td class="num{% if line.over_balance %} pj-late{% endif %}">{{ line.balance|qty }}</td>
          <td class="num">{{ line.proposal.in_period|qty }}</td>
          <td class="num">{{ line.proposal.proposed|qty }}</td>
          <td class="ctr-num"><label class="sr-only" for="{{ quantity.id_for_label }}">Cantidad del acta de {{ line.label }}</label>{{ quantity }}</td>
          <td><label class="sr-only" for="{{ justification.id_for_label }}">Justificación de {{ line.label }}</label>{{ justification }}</td>
          <td class="num{% if line.differs %} ctr-diff{% endif %}">{{ line.amount|signed }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    <p class="pj-muted">Deja la cantidad vacía para usar la propuesta.</p>
    {% include "suno/contracts/_totals.html" with direct=draft.totals.direct gross=draft.totals.gross amortization=draft.totals.amortization retention=draft.totals.retention returned=draft.totals.retention_returned net=draft.totals.net charge_lines=draft.totals.charges.lines deduction_lines=draft.totals.deductions %}
    <div class="ctr-actions">
      <button class="text-link" type="submit" name="accion" value="calcular">Recalcular</button>
      <button class="button" type="submit" name="accion" value="guardar">Guardar borrador</button>
      <a href="{% url 'contracts:detail' project.pk contract.pk %}?pestana=actas">Cancelar</a>
    </div>
  </form>
</section>
{% endblock %}
```

`server/suno/core/templates/suno/contracts/certificate_detail.html`:

```html
{% extends "suno/projects/base.html" %}
{% load money_tags %}
{% block title %}Acta {{ certificate.number }} · {{ contract.number }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="contracts" %}
{% include "suno/contracts/_nav.html" with sub="detail" %}
<section class="pj-panel">
  <h2>{% if certificate.is_settlement %}Acta de liquidación{% else %}Acta{% endif %} {{ certificate.number }} · <a href="{% url 'contracts:detail' project.pk contract.pk %}?pestana=actas">{{ contract.number }}</a></h2>
  <p><span class="bg-label ctr-{{ certificate.status }}">{{ certificate.get_status_display }}</span> Periodo {{ certificate.period_start|date:"d/m/Y" }} – {{ certificate.period_end|date:"d/m/Y" }} · {{ contract.contractor }}</p>
  {% if certificate.status == "rejected" %}<p class="pj-warning">Rechazada por {{ certificate.decided_by }}: {{ certificate.decision_note }}</p>{% endif %}
  {% if certificate.status == "voided" %}<p class="pj-muted">Anulada por {{ certificate.voided_by }} el {{ certificate.voided_at|date:"d/m/Y" }}: {{ certificate.void_note }}</p>{% endif %}
  <dl class="cst-facts">
    <dt>Preparó</dt><dd>{{ certificate.created_by }} · {{ certificate.created_at|date:"d/m/Y H:i" }}</dd>
    {% if certificate.decided_by %}<dt>{% if certificate.status == "rejected" %}Rechazó{% else %}Aprobó{% endif %}</dt><dd>{{ certificate.decided_by }} · {{ certificate.decided_at|date:"d/m/Y H:i" }}</dd>{% endif %}
    <dt>Acta firmada</dt><dd>{% if certificate.attachment_id %}<a href="{% url 'contracts:signed_copy' project.pk contract.pk certificate.pk %}" target="_blank" rel="noopener">{{ certificate.attachment_name|default:"Ver" }}</a>{% else %}<span class="pj-late">Falta</span>{% endif %}</dd>
  </dl>
  <div class="ctr-actions">
    {% if can_edit %}<a class="button" href="{% url 'contracts:edit_certificate' project.pk contract.pk certificate.pk %}">{% if certificate.status == "rejected" %}Corregir y reenviar{% else %}Editar{% endif %}</a>{% endif %}
    {% if can_delete %}<form method="post" action="{% url 'contracts:delete_certificate' project.pk contract.pk certificate.pk %}">{% csrf_token %}<button class="text-link" type="submit">Borrar borrador</button></form>{% endif %}
  </div>
  {% if can_sign %}
  <form method="post" enctype="multipart/form-data" action="{% url 'contracts:upload_signed' project.pk contract.pk certificate.pk %}" class="pj-form">
    {% csrf_token %}
    <label>{{ signed_form.signed.label }}{% if certificate.attachment_id %} <small>(ya tiene una; otra la reemplaza)</small>{% endif %} {{ signed_form.signed }}</label>
    <button class="button" type="submit">Subir acta firmada</button>
  </form>
  {% endif %}
  {% if can_decide %}
  <div class="cst-decide">
    <form method="post" action="{% url 'contracts:approve_certificate' project.pk contract.pk certificate.pk %}">{% csrf_token %}<button class="button" type="submit"{% if not certificate.attachment_id %} disabled title="Falta el acta firmada"{% endif %}>Aprobar</button></form>
    <form method="post" action="{% url 'contracts:reject_certificate' project.pk contract.pk certificate.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo del rechazo <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Rechazar</button></form>
  </div>
  {% endif %}
  {% if can_void %}<form method="post" action="{% url 'contracts:void_certificate' project.pk contract.pk certificate.pk %}" class="cst-reject">{% csrf_token %}<label>Motivo de la anulación <input name="motivo" maxlength="500" required></label><button class="text-link" type="submit">Anular acta</button></form>{% endif %}
</section>
<section class="pj-panel">
  <div class="pj-scroll">
  <table class="pj-table ctr-table">
    <thead><tr><th>Ítem</th><th class="num">Contratado</th><th class="num">Anterior</th><th class="num">Esta acta</th><th class="num">Acumulado</th><th class="num">Saldo</th><th class="num">Valor de esta acta</th></tr></thead>
    <tbody>
    {% for line in document.lines %}
      <tr>
        <td class="pj-name">{{ line.label }}{% if line.differs %}<br><small class="ctr-diff">Propuesta {{ line.proposed|qty }} · {{ line.justification }}</small>{% endif %}</td>
        <td class="num">{{ line.contracted|qty }}</td>
        <td class="num">{{ line.previous|qty }}</td>
        <td class="num">{{ line.this|qty }}</td>
        <td class="num">{{ line.accumulated|qty }}</td>
        <td class="num">{{ line.balance|qty }}</td>
        <td class="num">{{ line.amount|signed }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% include "suno/contracts/_totals.html" with direct=certificate.direct gross=certificate.gross amortization=certificate.amortization retention=certificate.retention returned=certificate.retention_returned net=certificate.net charge_lines=document.charges deduction_lines=document.deductions %}
  {% if entries %}
  <h3>Movimientos en el libro</h3>
  <ul>{% for e in entries %}<li><a href="{% url 'costs:detail' project.pk e.pk %}">{{ e.incurred_on|date:"d/m/Y" }} · {{ e.description }}</a> · {{ e.amount|signed }}</li>{% endfor %}</ul>
  {% endif %}
</section>
{% endblock %}
```

Los `charge_lines` del borrador son `ChargeLine` (con `pct` y `amount` como `Decimal`) y los del acta guardada son los diccionarios de `breakdown` (texto): la plantilla `_totals.html` usa los mismos nombres en ambos.

- [ ] **Step 6: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts suno/costs && ruff check suno && mypy suno/contracts suno/costs --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts suno/costs
```

Expected: PASS (las de la bandeja de la 6a también: sin contratos, la sección no aparece y el contador no cambia).

- [ ] **Step 7: Commit**

```bash
git add server/suno/contracts server/suno/costs/views.py server/suno/core/templates/suno/contracts server/suno/core/templates/suno/costs/inbox.html
git commit -m "feat(contracts): certificate pages, signed copy upload and the costs approval inbox

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: PDF del acta y Excel de contratos

**Files:**
- Create: `server/suno/contracts/pdf.py`, `server/suno/contracts/export.py`
- Modify: `server/suno/contracts/views.py`, `server/suno/contracts/urls.py`
- Modify: `server/suno/core/templates/suno/contracts/{_nav,detail,certificate_detail}.html` (enlaces)
- Test: `server/suno/contracts/tests/test_documents.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/contracts/tests/test_documents.py`:

```python
"""PDF del acta y Excel de contratos (spec, 2.8 y 4): contenido, `safe_cell`, auditoría."""

from __future__ import annotations

import io
import unittest
import zipfile
from datetime import date

from django.test import TestCase

from suno.contracts.pdf import certificate_pdf
from suno.contracts.queries import certificate_document
from suno.core.audit import AuditAction
from suno.core.models import AuditEvent
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.catalog import load_catalog

from .factories import contract_world, make_certificate, make_contract

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sheets(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        return [
            book.read(name).decode()
            for name in sorted(book.namelist())
            if name.startswith("xl/worksheets/sheet")
        ]


@skip_without_db
class ContractDocumentTests(TestCase):
    def setUp(self) -> None:
        self.cw = contract_world()
        self.world = self.cw.world
        self.contract = make_contract(self.cw)
        self.contract.contractor = "=HYPERLINK(\"x\")"
        self.contract.save(update_fields=["contractor"])
        self.certificate = make_certificate(
            self.cw, self.contract, quantities={"2.1": "4", "2.2": "200"},
            totals={"gross": "2781850.00", "net": "1736707.00"},
        )  # fmt: skip
        self.base = f"/plataforma/proyectos/{self.world.project.pk}/contratos/"
        self.client.force_login(self.cw.contributor)

    def test_the_pdf_to_sign(self) -> None:
        ctx = self.world.ctx()
        document = certificate_document(
            ctx, self.world.project, self.contract, self.certificate,
            load_catalog(ctx, self.world.project),
        )  # fmt: skip
        content = certificate_pdf(document, generated_on=date(2026, 4, 1))
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(len(content), 1500)
        url = f"{self.base}{self.contract.pk}/actas/{self.certificate.pk}/acta.pdf"
        response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("acta-SC-001-1", response["Content-Disposition"])
        event = AuditEvent.objects.get(action=AuditAction.CERTIFICATE_EXPORTED)
        self.assertEqual(event.metadata["contract"], str(self.contract.pk))

    def test_the_list_in_excel(self) -> None:
        response = self.client.get(self.base + "contratos.xlsx?tipo=subcontract")
        self.assertEqual(response["Content-Type"], XLSX)
        (sheet,) = sheets(response.content)
        self.assertIn(">SC-001<", sheet)
        self.assertIn(">'=HYPERLINK(\"x\")<", sheet)
        self.assertIn("<v>8224600", sheet)
        event = AuditEvent.objects.get(action=AuditAction.CONTRACT_EXPORTED)
        self.assertEqual(event.metadata["contracts"], 1)

    def test_one_contract_in_excel(self) -> None:
        response = self.client.get(f"{self.base}{self.contract.pk}/contrato.xlsx")
        self.assertEqual(response["Content-Type"], XLSX)
        contract, lines, certificates, amendments = sheets(response.content)
        self.assertIn(">Estructura en concreto<", contract)
        self.assertIn(">2.1 Concreto zapatas<", lines)
        self.assertIn("<v>2781850", certificates)
        self.assertIn(">numero<", amendments)

    def test_who_downloads(self) -> None:
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base + "contratos.xlsx").status_code, 404)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_documents.py`
Expected: ERROR (`No module named 'suno.contracts.pdf'`).

- [ ] **Step 3: El PDF**

`server/suno/contracts/pdf.py`:

```python
"""Acta para firmar (costos, pieza 6b, spec 4 "PDF del acta"), con reportlab como la Hoja de
Vida de equipos (`equipment/hoja_de_vida.py`). Sin recursos externos.

Contenido: proyecto y contrato; por línea, contratado, anterior, esta acta, acumulado y saldo,
en cantidad y en valor; cargos, amortización, retención, descuentos y neto; las
justificaciones de lo que difiere de lo validado; y las firmas.
"""

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

from suno.core.templatetags.money_tags import money, qty

from .queries import CertificateDocument

__all__ = ["certificate_pdf"]

PAGE = landscape(A4)
INK = colors.HexColor("#171717")
ACCENT = colors.HexColor("#8C2633")
GRID = colors.HexColor("#CCCCCC")
HEAD = colors.HexColor("#F0EEE9")


def _font() -> None:
    if "SunoReport" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("SunoReport", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )


def certificate_pdf(document: CertificateDocument, *, generated_on: date) -> bytes:
    _font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "ActBody", parent=styles["BodyText"], fontName="SunoReport", fontSize=8, leading=10.5
    )
    heading = ParagraphStyle(
        "ActHeading", parent=body, fontSize=15, leading=20, spaceAfter=4, textColor=ACCENT
    )
    section = ParagraphStyle(
        "ActSection", parent=body, fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4,
        textColor=INK, keepWithNext=True,
    )  # fmt: skip

    def cell(text: str) -> Paragraph:
        return Paragraph(escape(text), body)

    def table(
        rows: list[list[str]],
        widths: list[float],
        *,
        header: bool = True,
        heights: list[float | None] | None = None,
    ) -> Table:
        built = Table(
            [[cell(c) for c in row] for row in rows],
            colWidths=widths,
            rowHeights=heights,
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
    certificate, contract = document.certificate, document.contract
    kind = "liquidación" if certificate.is_settlement else "avance"
    story: list[Any] = [
        Paragraph(f"Acta de {kind} N.º {certificate.number}", heading),
        table(
            [
                ["Proyecto", document.project.name],
                ["Contrato", f"{contract.number} · {contract.get_kind_display()}"],
                ["Contratista", contract.contractor],
                ["Objeto", contract.purpose],
                [
                    "Periodo",
                    f"{certificate.period_start:%d/%m/%Y} – {certificate.period_end:%d/%m/%Y}",
                ],
            ],
            [width * 0.18, width * 0.82],
            header=False,
        ),
        Paragraph("Cantidades", section),
        table(
            [
                ["Ítem", "Unidad", "Precio", "Contratado", "Anterior", "Esta acta", "Acumulado",
                 "Saldo"],
                *[
                    [line.label, line.unit, money(line.unit_price), qty(line.contracted),
                     qty(line.previous), qty(line.this), qty(line.accumulated), qty(line.balance)]
                    for line in document.lines
                ],
            ],
            [width * 0.28, width * 0.06, *[width * 0.11] * 6],
        ),  # fmt: skip
        Paragraph("Valores", section),
        table(
            [
                ["Ítem", "Contratado", "Anterior", "Esta acta", "Acumulado", "Saldo"],
                *[
                    [line.label, money(line.contracted_value), money(line.previous_value),
                     money(line.amount), money(line.accumulated_value), money(line.balance_value)]
                    for line in document.lines
                ],
            ],
            [width * 0.30, *[width * 0.14] * 5],
        ),  # fmt: skip
        Paragraph("Liquidación del acta", section),
        table(
            [
                ["Bruto directo", money(certificate.direct)],
                *[[f"{c['name']} ({qty(c['pct'])} %)", money(c["amount"])]
                  for c in document.charges],
                ["Bruto (costo del periodo)", money(certificate.gross)],
                ["Amortización del anticipo", money(-certificate.amortization)],
                ["Retención de garantía", money(-certificate.retention)],
                *[[f"{d['name']} ({qty(d['pct'])} %)", money(-Decimal(d["amount"]))]
                  for d in document.deductions],
                *([["Devolución de la retención", money(certificate.retention_returned)]]
                  if certificate.retention_returned else []),
                ["Neto a pagar", money(certificate.net)],
            ],
            [width * 0.5, width * 0.2],
            header=False,
        ),  # fmt: skip
    ]
    differing = [line for line in document.lines if line.differs]
    if differing:
        story.append(Paragraph("Ajustes frente a lo validado en campo", section))
        story += [
            Paragraph(
                escape(
                    f"{line.label}: propuesta {qty(line.proposed)}, acta {qty(line.this)}. "
                    f"{line.justification}"
                ),
                body,
            )
            for line in differing
        ]
    story += [
        Spacer(1, 28),
        table(
            [
                ["", "", ""],
                ["Contratista", "Residente o interventoría", "Aprobó"],
                [contract.contractor, "", ""],
            ],
            [width / 3] * 3,
            header=False,
            heights=[40, None, None],
        ),
        Spacer(1, 6),
        Paragraph(f"Generado por SUNO el {generated_on:%d/%m/%Y}.", body),
    ]

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("SunoReport", 8)
        canvas.drawString(36, 20, f"SUNO · {contract.number} · Acta {certificate.number}")
        canvas.drawRightString(PAGE[0] - 36, 20, str(doc.page))
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=36,
        rightMargin=36,
        topMargin=30,
        bottomMargin=36,
        title=f"Acta {certificate.number} {contract.number}",
        author="SUNO",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
```

- [ ] **Step 4: El Excel**

`server/suno/contracts/export.py`:

```python
"""Excel de contratos (costos, pieza 6b, spec 2.8): la lista con saldos y el detalle de un
contrato con sus actas. Todo texto de usuario pasa por `safe_cell`; los valores van como
número (`to_xlsx` solo conoce int y float) y las fechas en ISO."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from suno.backoffice.export import safe_cell, to_xlsx, to_xlsx_book

from .queries import ContractRow, ContractSheet

LIST_HEADER = [
    "numero", "tipo", "contratista", "objeto", "inicio", "fin", "valor_vigente", "ejecutado",
    "porcentaje", "anticipo", "anticipo_por_amortizar", "retenido", "estado",
]  # fmt: skip


def _n(value: Decimal | None) -> float | str:
    return "" if value is None else float(value)


def contracts_workbook(rows: Sequence[ContractRow]) -> bytes:
    return to_xlsx(
        LIST_HEADER,
        [
            [
                safe_cell(row.contract.number),
                row.contract.get_kind_display(),
                safe_cell(row.contract.contractor),
                safe_cell(row.contract.purpose),
                row.contract.starts_on.isoformat(),
                row.contract.ends_on.isoformat(),
                _n(row.contract.current_value),
                _n(row.progress.executed),
                _n(row.executed_pct),
                _n(row.contract.advance_amount),
                _n(row.advance_balance),
                _n(row.retained),
                row.contract.get_status_display(),
            ]
            for row in rows
        ],
        sheet_name="Contratos",
    )


def contract_workbook(sheet: ContractSheet) -> bytes:
    contract = sheet.contract
    facts: list[list[Any]] = [
        ["numero", safe_cell(contract.number)],
        ["tipo", contract.get_kind_display()],
        ["contratista", safe_cell(contract.contractor)],
        ["objeto", safe_cell(contract.purpose)],
        ["inicio", contract.starts_on.isoformat()],
        ["fin", contract.ends_on.isoformat()],
        ["valor_vigente", _n(contract.current_value)],
        ["anticipo", _n(contract.advance_amount)],
        ["anticipo_por_amortizar", _n(sheet.advance_balance)],
        ["retencion_pct", _n(contract.retention_pct)],
        ["ejecutado", _n(sheet.progress.executed)],
        ["estado", contract.get_status_display()],
    ]
    lines = [
        [
            line.line.order,
            safe_cell(line.label),
            safe_cell(line.tasks),
            safe_cell(line.line.unit),
            _n(line.line.unit_price),
            _n(line.contracted),
            _n(line.paid),
            _n(line.balance),
            _n(line.contracted_value),
            _n(line.balance_value),
        ]
        for line in sheet.lines
    ]
    certificates = [
        [
            c.number,
            "liquidación" if c.is_settlement else "avance",
            c.period_start.isoformat(),
            c.period_end.isoformat(),
            _n(c.direct),
            _n(c.charges),
            _n(c.gross),
            _n(c.amortization),
            _n(c.retention),
            _n(c.deductions),
            _n(c.retention_returned),
            _n(c.net),
            c.get_status_display(),
        ]
        for c in sheet.certificates
    ]
    amendments = [
        [
            a.number,
            a.signed_on.isoformat(),
            safe_cell(a.reason),
            a.new_ends_on.isoformat() if a.new_ends_on else "",
            _n(a.value_after),
            a.get_status_display(),
        ]
        for a in sheet.amendments
    ]
    return to_xlsx_book(
        [
            ("Contrato", ["campo", "valor"], facts),
            ("Líneas", ["orden", "item", "tareas", "unidad", "precio", "contratado", "pagado",
                        "saldo", "valor_contratado", "valor_saldo"], lines),
            ("Actas", ["numero", "tipo", "desde", "hasta", "bruto_directo", "cargos", "bruto",
                       "amortizacion", "retencion", "descuentos", "devolucion", "neto",
                       "estado"], certificates),
            ("Otrosíes", ["numero", "fecha", "motivo", "nuevo_fin", "valor_despues", "estado"],
             amendments),
        ]
    )  # fmt: skip
```

- [ ] **Step 5: Las vistas y los enlaces**

En `server/suno/contracts/views.py`, agregar a los imports `from django.db import transaction`, `from suno.core import audit`, `from suno.core.audit import AuditAction`, `from suno.core.download import content_disposition`, `from .export import contract_workbook, contracts_workbook` y `from .pdf import certificate_pdf`, la constante `XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"`, y al final:

```python
# ---------------------------------------------------------------------------
# Documentos (auditados)
# ---------------------------------------------------------------------------


def _require_export(ctx: RequestContext, project: Project) -> None:
    if not can_export_costs(ctx, project):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_GET
def export_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    _require_export(ctx, project)
    filters = ListFilters.from_query(request.GET)
    rows = contract_rows(ctx, project, filters)
    content = contracts_workbook(rows)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"contracts": len(rows), "filters": filters.query},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"contratos-{project.code or project.name}", ".xlsx", fallback="contratos"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def export_contract(request: HttpRequest, project_id: UUID, contract_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    _require_export(ctx, project)
    content = contract_workbook(contract_sheet(ctx, project, contract, load_catalog(ctx, project)))
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTRACT_EXPORTED,
            target=f"contract:{contract.pk}",
            metadata={"contract": str(contract.pk), "number": contract.number},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"contrato-{contract.number}", ".xlsx", fallback="contrato"
    )
    return response


@login_required(login_url="platform_login")
@require_GET
def certificate_document_pdf(
    request: HttpRequest, project_id: UUID, contract_id: UUID, certificate_id: UUID
) -> HttpResponse:
    """El acta para imprimir y firmar."""
    ctx = _ctx(request)
    project = get_contracts_project(ctx, project_id)
    contract = get_contract(ctx, project, contract_id)
    certificate = get_certificate(ctx, contract, certificate_id)
    _require_export(ctx, project)
    document = certificate_document(ctx, project, contract, certificate, load_catalog(ctx, project))
    content = certificate_pdf(document, generated_on=timezone.localdate())
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CERTIFICATE_EXPORTED,
            target=f"certificate:{certificate.pk}",
            metadata={
                "contract": str(contract.pk),
                "number": certificate.number,
                "status": certificate.status,
            },
        )
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition(
        f"acta-{contract.number}-{certificate.number}", ".pdf", fallback="acta"
    )
    return response
```

En `server/suno/contracts/urls.py`:

```python
    path("contratos.xlsx", views.export_list, name="export"),
    path(C + "contrato.xlsx", views.export_contract, name="export_contract"),
    path(R + "acta.pdf", views.certificate_document_pdf, name="certificate_pdf"),
```

(`contratos.xlsx` antes de las rutas con `<uuid>`; da igual el orden porque no es un UUID.)

Enlaces en las plantillas:

- `_nav.html`, al final de la `<nav>`: `{% if can_export_contract %}<a href="{% url 'contracts:export' project.pk %}{% if filters.query %}?{{ filters.query }}{% endif %}">Exportar a Excel</a>{% endif %}`
- `detail.html`, dentro de `.ctr-actions`: `{% if can_export_contract %}<a class="text-link" href="{% url 'contracts:export_contract' project.pk contract.pk %}">Exportar a Excel</a>{% endif %}`
- `certificate_detail.html`, primero dentro de `.ctr-actions`: `{% if can_export_contract %}<a class="button" href="{% url 'contracts:certificate_pdf' project.pk contract.pk certificate.pk %}">Descargar PDF para firmar</a>{% endif %}`

- [ ] **Step 6: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts && ruff check suno/contracts && mypy suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/contracts
```

Expected: PASS. Abrir el PDF de la prueba a mano una vez (guardarlo con `infra/scripts/test_docker.sh --cmd "python manage.py shell -c '…'"` o desde la revisión visual de la Task 14) para ver que las tablas caben en A4 horizontal.

- [ ] **Step 7: Commit**

```bash
git add server/suno/contracts server/suno/core/templates/suno/contracts
git commit -m "feat(contracts): certificate PDF to sign and contract Excel exports

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: El libro y el control conocen las actas

**Files:**
- Modify: `server/suno/costs/ledger.py`, `server/suno/costs/export.py`, `server/suno/costs/access.py`, `server/suno/costs/views.py`
- Modify: `server/suno/core/templates/suno/costs/{list,detail}.html`
- Modify: `server/suno/control/views.py`, `server/suno/core/templates/suno/control/dashboard.html`
- Test: `server/suno/costs/tests/test_ledger.py`, `server/suno/costs/tests/test_views.py`, `server/suno/control/tests/test_views.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/costs/tests/test_ledger.py`, una clase nueva al final (con los imports que falten: `from suno.contracts.tests.factories import contract_world, make_certificate, make_contract` y `from suno.core.models import CostEntry`):

```python
@skip_without_db
class CertificateOriginTests(TestCase):
    """Costos, pieza 6b: origen "Actas" y la etiqueta del acta en el libro."""

    def setUp(self) -> None:
        self.cw = contract_world()
        self.ctx = self.cw.world.ctx()
        self.contract = make_contract(self.cw)
        self.certificate = make_certificate(self.cw, self.contract, quantities={"2.1": "1"})
        self.manual = entry(self.cw.world, status=CostStatus.APPROVED, description="Manual")
        from_certificate = entry(self.cw.world, status=CostStatus.APPROVED, description="Del acta")
        CostEntry.objects.filter(pk=from_certificate.pk).update(certificate=self.certificate)
        self.catalog = load_catalog(self.ctx, self.cw.project)

    def page(self, **query: str) -> LedgerPage:
        return ledger_page(self.ctx, self.cw.project, LedgerFilters.from_query(query), page=1,
                           catalog=self.catalog)  # fmt: skip

    def test_origin_filter_and_label(self) -> None:
        self.assertEqual([line.description for line in self.page(origen="actas").lines],
                         ["Del acta"])  # fmt: skip
        self.assertEqual([line.description for line in self.page(origen="libro").lines],
                         ["Manual"])  # fmt: skip
        line = self.page(origen="actas").lines[0]
        self.assertEqual(line.certificate_label, "Acta 1 · Contrato SC-001")
        self.assertEqual((line.certificate_id, line.contract_id),
                         (self.certificate.pk, self.contract.pk))  # fmt: skip
        self.assertEqual(line.origin_label, "Acta")
        self.assertEqual(ORIGINS["libro"], "Manual")
        self.assertFalse(LedgerFilters(origin="actas").shows_automatic)
```

(importar `LedgerPage`, `ORIGINS` de `suno.costs.ledger` si no están). En `server/suno/costs/tests/test_views.py`, al final de `CostPagesTests`:

```python
    def test_certificate_entries_link_to_their_certificate_and_are_not_reversed(self) -> None:
        from suno.contracts.tests.factories import ContractWorld, make_certificate, make_contract

        cw = ContractWorld(self.world, self.budget, self.contributor, self.viewer)
        certificate = make_certificate(cw, make_contract(cw), quantities={"2.1": "1"})
        cost = entry(self.world, status=CostStatus.APPROVED, description="Del acta")
        CostEntry.objects.filter(pk=cost.pk).update(certificate=certificate)
        listing = self.client.get(self.base)
        self.assertContains(listing, "Acta 1 · Contrato SC-001")
        detail = self.client.get(f"{self.base}{cost.pk}/")
        self.assertContains(detail, "Viene del acta")
        self.assertNotContains(detail, ">Reversar</a>")

    def test_a_manual_subcontract_on_a_contracted_item_warns(self) -> None:
        from suno.contracts.tests.factories import ContractWorld, make_contract

        make_contract(ContractWorld(self.world, self.budget, self.contributor, self.viewer))
        response = self.client.post(self.base + "nuevo/", self.form(kind="subcontract"),
                                    follow=True)  # fmt: skip
        self.assertContains(response, "tiene contrato vigente (SC-001)")
        cost = CostEntry.objects.get()
        self.assertContains(self.client.get(f"{self.base}{cost.pk}/"), "SC-001")
```

En `server/suno/control/tests/test_views.py`, al final de `ControlCostTests`:

```python
    def test_committed_in_contracts_on_the_item_tab(self) -> None:
        from suno.contracts.tests.factories import ContractWorld, make_contract

        cw = ContractWorld(self.world, self.budget, self.world.admin, self.world.admin)
        make_contract(cw, lines=(("2.1", "12", "400000.00", (5,)),))  # 4.800.000 > BAC 4.500.000
        items = self.client.get(self.base + AT_CUT + "&desglose=item")
        self.assertContains(items, "Comprometido en contratos")
        self.assertContains(items, "4.800.000,00")
        self.assertContains(items, "lo contratado supera el presupuesto vigente")
        self.client.force_login(self.world.supervisor)  # sin costos: ni columna ni aviso
        self.assertNotContains(
            self.client.get(self.base + AT_CUT + "&desglose=item"), "Comprometido en contratos"
        )
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/costs/tests/test_ledger.py suno/costs/tests/test_views.py suno/control/tests/test_views.py`
Expected: FAIL/ERROR (`LedgerLine` no tiene `certificate_label`; las páginas no dicen nada de actas ni contratos).

- [ ] **Step 3: El libro**

En `server/suno/costs/ledger.py`:

```python
ALL, LEDGER_ONLY, CERTIFICATES, AUTOMATIC = "todos", "libro", "actas", "automaticos"
ORIGINS = {
    ALL: "Todos",
    LEDGER_ONLY: "Manual",
    CERTIFICATES: "Actas",
    AUTOMATIC: "Solo automáticos",
}
CERTIFICATE_LABEL = "Acta"
```

- `LedgerFilters.shows_automatic`: `self.origin not in (LEDGER_ONLY, CERTIFICATES)` en vez de `self.origin != LEDGER_ONLY`.
- `manual_entries`, al principio, después de `entries = scoped_qs(...)`:

```python
    if filters.origin == LEDGER_ONLY:
        entries = entries.filter(certificate__isnull=True)
    elif filters.origin == CERTIFICATES:
        entries = entries.filter(certificate__isnull=False)
```

- `LedgerLine`: campos nuevos al final `certificate_id: UUID | None = None`, `contract_id: UUID | None = None`, `certificate_label: str = ""`, y:

```python
    @property
    def origin_label(self) -> str:
        return CERTIFICATE_LABEL if self.certificate_id else ORIGIN_LABELS[self.origin]
```

- `_with_details`: `select_related("created_by", "decided_by", "chapter", "task", "certificate__contract")`.
- `_entry_line`: agregar

```python
        certificate_id=entry.certificate_id,
        contract_id=entry.certificate.contract_id if entry.certificate else None,
        certificate_label=(
            f"Acta {entry.certificate.number} · Contrato {entry.certificate.contract.number}"
            if entry.certificate
            else ""
        ),
```

En `server/suno/costs/export.py`, cambiar `ORIGIN_LABELS[line.origin]` por `line.origin_label` y quitar `ORIGIN_LABELS` del import.

En `server/suno/costs/access.py`, en `get_entry`, agregar `"certificate__contract"` al `select_related`.

En `server/suno/costs/views.py`:

1. Import: `from suno.contracts.queries import contracts_on_item` (junto a `inbox_count, inbox_items`) y `CostEntryItem` de `suno.core.models`.
2. Una función:

```python
def _contract_warning(ctx: RequestContext, project: Project, entry: CostEntry) -> str:
    """Un costo manual de subcontrato o mano de obra sobre un ítem con contrato vigente: aviso,
    sin bloquear (spec 4). Vacío si no aplica."""
    if (
        entry.certificate_id is not None
        or entry.scope != CostScope.ITEM
        or entry.kind not in (CostKind.SUBCONTRACT, CostKind.LABOR)
    ):
        return ""
    item_id = (
        scoped_qs(ctx, CostEntryItem).filter(entry=entry).values_list("item_id", flat=True).first()
    )
    numbers = contracts_on_item(ctx, project, item_id) if item_id else []
    if not numbers:
        return ""
    return (
        f"El ítem tiene contrato vigente ({', '.join(numbers)}): su costo entra por las actas. "
        "Revisa que este costo no se cuente dos veces."
    )
```

3. En `_entry_form`, en la rama `else` del guardado, después de `messages.success(...)`:

```python
            warning = _contract_warning(ctx, project, saved)
            if warning:
                messages.warning(request, warning)
```

4. En `detail`: agregar al contexto `"contract_warning": _contract_warning(ctx, project, entry)` y en `can_reverse` la condición `and entry.certificate_id is None`.

En `server/suno/core/templates/suno/costs/list.html`, en la celda de la descripción, después de las marcas de reverso:

```html
{% if line.certificate_id %}<br><a class="cst-mark" href="{% url 'contracts:certificate' project.pk line.contract_id line.certificate_id %}">{{ line.certificate_label }}</a>{% endif %}
```

En `server/suno/core/templates/suno/costs/detail.html`, después del párrafo de estado:

```html
  {% if entry.certificate %}<p class="pj-muted">Viene del acta <a href="{% url 'contracts:certificate' project.pk entry.certificate.contract_id entry.certificate_id %}">Acta {{ entry.certificate.number }} · Contrato {{ entry.certificate.contract.number }}</a>: se anula desde el contrato.</p>{% endif %}
  {% if contract_warning %}<p class="pj-warning">{{ contract_warning }}</p>{% endif %}
```

- [ ] **Step 4: El control**

En `server/suno/control/views.py`:

1. Import: `from suno.contracts.queries import committed_by_item`.
2. En `dashboard`, después de calcular `result`:

```python
    rows = _breakdown(result, options.tab, options.order) if result else ()
    committed = (
        committed_by_item(ctx, project) if costs and options.tab == "item" and result else {}
    )
    over_budget = [
        row.label
        for row in rows
        if row.item_id is not None and committed.get(row.item_id, Decimal(0)) > row.bac
    ]
```

y en el contexto reemplazar `"rows": …` por:

```python
            "rows": [(row, committed.get(row.item_id) if row.item_id else None) for row in rows],
            "show_committed": bool(costs and options.tab == "item"),
            "committed_over_budget": over_budget,
```

(con `from decimal import Decimal`). El cálculo del control no cambia (spec 4): es una columna informativa.

En `server/suno/core/templates/suno/control/dashboard.html`, en la tabla del desglose:

- Antes de `<div class="pj-scroll">` de la tabla: `{% if committed_over_budget %}<p class="pj-warning">En {{ committed_over_budget|join:", " }} lo contratado supera el presupuesto vigente.</p>{% endif %}`
- En el `<thead>`, al final de la fila: `{% if show_committed %}<th class="num">Comprometido en contratos</th>{% endif %}`
- `{% for row in rows %}` → `{% for row, committed in rows %}`
- Al final de cada fila del cuerpo: `{% if show_committed %}<td class="num">{% if committed %}{{ committed|signed }}{% else %}—{% endif %}</td>{% endif %}`

El texto de la prueba es "lo contratado supera el presupuesto vigente": el aviso lo contiene.

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno && ruff check suno && mypy suno/costs suno/control suno/contracts --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/costs suno/control suno/contracts
```

Expected: PASS (las de la 6a también: sin actas el libro y el control se ven igual, salvo la etiqueta "Manual" del origen).

- [ ] **Step 6: Commit**

```bash
git add server/suno/costs server/suno/control server/suno/core/templates/suno/costs server/suno/core/templates/suno/control
git commit -m "feat(costs): certificate origin in the ledger and contract commitments in control

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Prueba de carga (200 contratos, 2000 líneas y 5000 actas)

**Files:**
- Create: `server/suno/contracts/tests/test_performance.py`

Sigue la prueba del control (`control/tests/test_performance.py`): datos con `bulk_create` en `setUpTestData` (fuera de la medición), una petición de calentamiento y la mejor de `MEASUREMENTS` mediciones, contra `LIMIT_SECONDS` (1 s en local, 2 s con `CI=true`). Reutiliza `build_large_project` (500 tareas, 50 ítems, 1000 registros validados) para que la ficha calcule lo validado con datos reales. Las líneas no se traslapan entre sí (ítem y tarea distintos), para medir la página y no un aviso por par.

- [ ] **Step 1: Escribir la prueba**

`server/suno/contracts/tests/test_performance.py`:

```python
"""Prueba de carga de la pestaña "Contratos" (spec, 5): 200 contratos, 2000 líneas, 5000 actas
aprobadas (50 000 líneas de acta). La lista y la ficha, en menos de 1 s (2 s en CI)."""

from __future__ import annotations

import time
import unittest
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from suno.control.tests.test_performance import (
    FIRST_START,
    LIMIT_SECONDS,
    MEASUREMENTS,
    build_large_project,
)
from suno.core.models import (
    BudgetItem,
    CertificateLine,
    CertificateStatus,
    Contract,
    ContractCertificate,
    ContractKind,
    ContractLine,
    ContractLineTask,
    ContractStatus,
    Task,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.costs.tests.factories import support_blob
from suno.projects.tests.factories import FieldWorld, make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
CONTRACTS = 200
LINES = 10
CERTIFICATES = 25
QUANTITY = D("1000")
PER_CERTIFICATE = D("4")


def add_contracts(world: FieldWorld) -> None:
    org, project = world.org, world.project
    items = list(BudgetItem.objects.filter(budget__project=project, code__contains=".")
                 .order_by("code"))  # fmt: skip
    tasks = list(Task.objects.filter(project=project, uid__gte=1000).order_by("sort_order"))
    blob = support_blob("carga de contratos")
    now = timezone.now()
    prices = [D(1000 + k) for k in range(LINES)]
    direct = sum((QUANTITY * price for price in prices), D(0))
    contracts = Contract.objects.bulk_create(
        [
            Contract(
                org=org, project=project,
                kind=ContractKind.PIECEWORK if n % 4 == 0 else ContractKind.SUBCONTRACT,
                number=f"SC-{n:03d}", purpose=f"Contrato de carga {n}",
                contractor=f"Contratista de carga {n}", starts_on=FIRST_START,
                ends_on=FIRST_START + timedelta(days=400), status=ContractStatus.APPROVED,
                advance_pct=D("10"), advance_amount=direct / 10, retention_pct=D("5"),
                current_value=direct, created_by=world.admin, updated_by=world.admin,
                approved_by=world.supervisor, approved_at=now,
            )
            for n in range(CONTRACTS)
        ]
    )  # fmt: skip
    lines: list[ContractLine] = []
    links: list[ContractLineTask] = []
    for n, contract in enumerate(contracts):
        for k in range(LINES):
            j = n * LINES + k
            line = ContractLine(
                org=org, contract=contract, order=k + 1,
                item=items[(j // len(tasks) * 7 + j) % len(items)], unit="m3",
                quantity=QUANTITY, current_quantity=QUANTITY, unit_price=prices[k],
            )  # fmt: skip
            lines.append(line)
            links.append(ContractLineTask(org=org, line=line, task=tasks[j % len(tasks)]))
    ContractLine.objects.bulk_create(lines, batch_size=2000)
    ContractLineTask.objects.bulk_create(links, batch_size=2000)
    gross = sum((PER_CERTIFICATE * price for price in prices), D(0))
    certificates: list[ContractCertificate] = []
    certificate_lines: list[CertificateLine] = []
    for n, contract in enumerate(contracts):
        own = lines[n * LINES : (n + 1) * LINES]
        for c in range(CERTIFICATES):
            start = FIRST_START + timedelta(days=7 * c)
            certificate = ContractCertificate(
                org=org, contract=contract, project=project, number=c + 1,
                period_start=start, period_end=start + timedelta(days=6),
                status=CertificateStatus.APPROVED, created_by=world.admin,
                updated_by=world.admin, decided_by=world.supervisor, decided_at=now,
                attachment=blob, attachment_name="acta.pdf", direct=gross, gross=gross,
                amortization=gross / 10, retention=gross / 20, net=gross * D("0.85"),
            )  # fmt: skip
            certificates.append(certificate)
            certificate_lines += [
                CertificateLine(
                    org=org, certificate=certificate, line=line,
                    proposed_quantity=PER_CERTIFICATE, quantity=PER_CERTIFICATE,
                    amount=PER_CERTIFICATE * line.unit_price,
                )  # fmt: skip
                for line in own
            ]
    ContractCertificate.objects.bulk_create(certificates, batch_size=2000)
    CertificateLine.objects.bulk_create(certificate_lines, batch_size=5000)


@skip_without_db
class ContractLoadTests(TestCase):
    world: FieldWorld

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga de contratos")
        build_large_project(cls.world)
        add_contracts(cls.world)

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

    def test_list_and_sheet_render_under_the_limit(self) -> None:
        project = self.world.project
        self.assertEqual(Contract.objects.filter(project=project).count(), CONTRACTS)
        self.assertEqual(ContractLine.objects.filter(contract__project=project).count(),
                         CONTRACTS * LINES)  # fmt: skip
        self.assertEqual(ContractCertificate.objects.filter(project=project).count(),
                         CONTRACTS * CERTIFICATES)  # fmt: skip
        self.client.force_login(self.world.admin)
        base = f"/plataforma/proyectos/{project.pk}/contratos/"
        contract = Contract.objects.get(project=project, number="SC-199")
        for url, expected in (
            (base, "Contrato de carga 199"),
            (f"{base}{contract.pk}/?pestana=lineas", "Ítem"),
            (f"{base}{contract.pk}/?pestana=actas", "Acta 25"),
        ):
            with self.subTest(url=url):
                best = self.best_of(url, expected)
                self.assertLess(best, LIMIT_SECONDS, f"{url} tardó {best:.3f} s")
```

- [ ] **Step 2: Correr**

Run: `infra/scripts/test_docker.sh suno/contracts/tests/test_performance.py`
Expected: PASS. Anotar los tres tiempos (con un `print(f"{best:.3f}")` temporal que se quita antes del commit) para el registro de avance.

- [ ] **Step 3: Si pasa del límite**

No subir `LIMIT_SECONDS` sin aprobación del usuario. Perfilar en el contenedor (`infra/scripts/test_docker.sh --cmd "python -m cProfile -s cumtime -m pytest -q suno/contracts/tests/test_performance.py | head -60"`). Lo esperable: `tab_alerts` (lee las 2000 líneas con sus tareas y lo validado del proyecto; si pesa, calcular lo validado solo de los ítems con líneas, que ya hace `validated_by_line`) o la plantilla de la lista (200 filas). La lista no debe hacer consultas por contrato (2 consultas: `ContractQueryTests.test_list_rows_with_balances_in_two_queries`).

- [ ] **Step 4: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/contracts && ruff check suno/contracts && mypy suno/contracts --config-file pyproject.toml"
git add server/suno/contracts/tests/test_performance.py
git commit -m "test(contracts): load test with 200 contracts, 2000 lines and 5000 certificates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Contratos en los datos de ejemplo (`seed_control_demo`)

**Files:**
- Modify: `server/suno/management/commands/seed_control_demo.py`
- Modify: `server/suno/control/tests/test_seed_demo.py`

El comando siembra los contratos **por los servicios** (como lo haría la oficina): el residente (que pasa a Revisor de Costos) prepara y el administrador aprueba, y al revés en la liquidación. Así el ejemplo también prueba el ciclo completo. Las dos "Acta 1/2: movimiento de tierras" manuales de la 6a (capítulo 2, tipo Subcontrato) salen de `COSTS`: ahora las escribe el contrato SC-001, y dejarlas contaría dos veces ese costo.

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/control/tests/test_seed_demo.py`, importar `CertificateStatus`, `Contract`, `ContractAmendment`, `ContractCertificate` y `CostKind` de `suno.core.models`, y al final de la clase:

```python
    def test_seeds_contracts_amendment_certificates_and_piecework(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        contracts = Contract.objects.filter(project=project)
        self.assertEqual(
            set(contracts.values_list("number", "status")),
            {("SC-001", "approved"), ("DT-001", "approved"), ("SC-002", "settled"),
             ("SC-003", "draft")},
        )  # fmt: skip
        self.assertEqual(contracts.get(number="DT-001").kind, "piecework")
        self.assertTrue(
            ContractAmendment.objects.filter(contract__number="SC-001", status="approved").exists()
        )
        certificates = ContractCertificate.objects.filter(project=project)
        self.assertTrue(certificates.filter(status=CertificateStatus.VOIDED).exists())
        self.assertTrue(certificates.filter(is_settlement=True, status="approved").exists())
        self.assertTrue(certificates.filter(status="draft").exclude(attachment=None).exists())
        entries = CostEntry.objects.filter(project=project, certificate__isnull=False)
        self.assertTrue(entries.filter(reverses__isnull=False).exists())
        self.assertTrue(entries.filter(kind=CostKind.LABOR).exists())
        self.assertFalse(
            CostEntry.objects.filter(project=project, description__startswith="Acta 1: ").exists()
        )
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py`
Expected: FAIL (no hay contratos).

- [ ] **Step 3: Implementar**

En `server/suno/management/commands/seed_control_demo.py`:

1. Docstring del módulo: agregar al final del primer párrafo "Desde la pieza 6b también siembra contratos: un subcontrato con un otrosí y actas (una anulada y una en borrador firmada), un destajo, un subcontrato liquidado y un borrador por aprobar."
2. Quitar de `COSTS` las dos entradas `Cost(25, K.SUBCONTRACT, S.CHAPTER, (), "2", "46000000", "Acta 1: movimiento de tierras", …)` y `Cost(60, …, "Acta 2: movimiento de tierras", …)`.
3. Imports nuevos:

```python
from suno.contracts import certificates as contract_certificates
from suno.contracts import services as contract_services
from suno.contracts.certificates import CertificateData, CertificateLineData
from suno.contracts.services import AmendmentData, ContractData, IncreaseData, LineData
from suno.core.access import AccessScope, Section, SectionRole
from suno.core.access_services import set_section_grant
from suno.core.models import ContractKind, ContractLine
```

4. En `run`, después de `self._costs(project, start, items, tasks)`: `self._contracts(project, start, items, tasks)`.
5. Métodos nuevos en `_Seeder`:

```python
    def _upload(self) -> Any:
        return SimpleUploadedFile("acta-firmada.pdf", DEMO_PDF, content_type="application/pdf")

    def _certificate(
        self,
        project: Project,
        contract_id: Any,
        start: date,
        end: date,
        *,
        author: Any,
        approver: Any | None,
        lines: tuple[CertificateLineData, ...] = (),
        settlement: bool = False,
    ) -> Any:
        """Prepara un acta (con el firmado subido) y, si hay `approver`, la aprueba."""
        certificate = contract_certificates.create_certificate(
            author, project, contract_id, CertificateData(start, end, lines), settlement=settlement
        )
        contract_certificates.attach_signed(
            author, project, contract_id, certificate.pk, self._upload(), "acta-firmada.pdf"
        )
        if approver is None:
            return certificate
        return contract_certificates.approve_certificate(
            approver, project, contract_id, certificate.pk
        )

    def _contracts(
        self,
        project: Project,
        start: date,
        items: dict[str, BudgetItem],
        tasks: dict[str, Task],
    ) -> None:
        """Contratos por los servicios: el residente (Revisor de Costos) prepara y el
        administrador aprueba; en la liquidación, al revés."""
        resident = self._resident()
        set_section_grant(
            self.ctx,
            user_id=resident.pk,
            section=Section.COSTS,
            role=SectionRole.REVIEWER,
            scope=AccessScope.ORGANIZATION,
        )
        office = resolve_request_context(Principal(user_id=resident.pk, org_id=self.org.pk))
        admin = self.ctx
        aiu = tuple(CHARGES)
        withholdings = (
            ChargeSpec(1, "Retención en la fuente", Decimal("2"), BASE_DIRECT),
            ChargeSpec(2, "ReteICA", Decimal("0.966"), BASE_DIRECT),
            ChargeSpec(3, "ReteIVA", Decimal("15"), line_base(4)),
        )

        def line(code: str, quantity: str, price: str, *names: str) -> LineData:
            return LineData(
                items[code].pk, tuple(tasks[name].pk for name in names), "", Decimal(quantity),
                Decimal(price),
            )  # fmt: skip

        def contract(data: ContractData, *, approve: bool = True) -> Any:
            created = contract_services.create_contract(office, project, data)
            if approve:
                contract_services.approve_contract(admin, project, created.pk)
            return created

        def day(offset: int) -> date:
            return start + timedelta(days=offset)

        # SC-001: movimiento de tierras, con un otrosí y tres actas (la tercera, anulada).
        earthworks = contract(
            ContractData(
                kind=ContractKind.SUBCONTRACT, number="SC-001",
                purpose="Movimiento de tierras: excavación y rellenos",
                contractor="Excavaciones Andinas SAS", starts_on=day(30), ends_on=day(150),
                advance_pct=Decimal("20"), retention_pct=Decimal("5"),
                lines=(
                    line("2.1", "2600", "26000", "Excavación frente norte", "Excavación frente sur"),
                    line("2.2", "1800", "42000", "Rellenos"),
                ),
                charges=aiu, deductions=withholdings,
            )
        )  # fmt: skip
        excavation = contract_services.active_lines(
            ContractLine.objects.filter(contract=earthworks)
        ).get(item=items["2.1"])
        amendment = contract_services.create_amendment(
            office, project, earthworks.pk,
            AmendmentData(day(80), "Mayor volumen de excavación en el frente sur", day(200),
                          (IncreaseData(excavation.pk, Decimal("200")),)),
        )  # fmt: skip
        contract_services.approve_amendment(admin, project, earthworks.pk, amendment.pk)
        self._certificate(project, earthworks.pk, day(30), day(90), author=office, approver=admin)
        self._certificate(project, earthworks.pk, day(91), day(150), author=office, approver=admin)
        closing = (CertificateLineData(excavation.pk, Decimal("20"), "Medición de cierre"),)
        wrong = self._certificate(project, earthworks.pk, day(151), day(170), author=office,
                                  approver=admin, lines=closing)  # fmt: skip
        contract_certificates.void_certificate(
            admin, project, earthworks.pk, wrong.pk, "Error en la medición de cierre"
        )
        self._certificate(project, earthworks.pk, day(151), self.today - timedelta(days=3),
                          author=office, approver=None, lines=closing)  # fmt: skip
        # DT-001: destajo de armado de acero (va al libro como mano de obra); vencido.
        steel = contract(
            ContractData(
                kind=ContractKind.PIECEWORK, number="DT-001", purpose="Armado de acero de zapatas",
                contractor="Cuadrilla Pérez", starts_on=day(98), ends_on=day(170),
                advance_pct=Decimal("0"), retention_pct=Decimal("0"),
                lines=(line("3.2", "21000", "1200", "Zapatas eje A", "Zapatas eje B"),),
            )
        )  # fmt: skip
        self._certificate(project, steel.pk, day(98), day(160), author=office, approver=admin)
        # SC-002: redes, con un acta y la liquidación (la prepara el Gestor y aprueba otro).
        networks = contract(
            ContractData(
                kind=ContractKind.SUBCONTRACT, number="SC-002", purpose="Tubería principal",
                contractor="Tuberías y Accesorios SAS", starts_on=day(150), ends_on=day(260),
                advance_pct=Decimal("0"), retention_pct=Decimal("5"),
                lines=(line("4.1", "900", "70000", "Tubería principal"),),
                charges=(ChargeSpec(1, "IVA", Decimal("19"), BASE_DIRECT),),
            )
        )  # fmt: skip
        self._certificate(project, networks.pk, day(150), self.today - timedelta(days=10),
                          author=office, approver=admin)  # fmt: skip
        self._certificate(project, networks.pk, self.today - timedelta(days=9),
                          self.today - timedelta(days=1), author=admin, approver=office,
                          settlement=True)  # fmt: skip
        # SC-003: borrador por aprobar (en la bandeja del administrador).
        contract(
            ContractData(
                kind=ContractKind.SUBCONTRACT, number="SC-003", purpose="Cámaras de inspección",
                contractor="Redes del Valle SAS", starts_on=day(196), ends_on=day(256),
                advance_pct=Decimal("10"), retention_pct=Decimal("5"),
                lines=(line("4.2", "18", "2300000", "Cámaras de inspección"),),
                charges=(ChargeSpec(1, "IVA", Decimal("19"), BASE_DIRECT),),
            ),
            approve=False,
        )  # fmt: skip
```

Notas:

- `ContractLine.objects` en un comando de `ALLOWED` (`test_architecture.py`) está permitido, como el resto del comando.
- Las fechas son relativas a `start` (hoy − 182 días, llevado al lunes): `day(170)` y `today − 3` siempre quedan en el pasado y `day(200)` (el fin del otrosí) en el futuro, así SC-001 no aparece vencido y DT-001 (fin `day(170)`) sí.
- La propuesta de cada acta sale de los registros validados del ejemplo; donde ya no queda nada validado sin pagar, el acta lleva la cantidad explícita con su justificación (`closing`).
- `ChargeSpec`, `BASE_DIRECT` y `line_base` ya están en el import de `budgets.totals` del comando.

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/management suno/control && ruff check suno && mypy suno/management suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py suno/core/tests/test_architecture.py
```

Expected: PASS (las pruebas viejas del comando también: el control sigue con costo directo e indirecto, un borrador del libro y los automáticos).

- [ ] **Step 5: Commit**

```bash
git add server/suno/management/commands/seed_control_demo.py server/suno/control/tests/test_seed_demo.py
git commit -m "feat(contracts): demo contracts, amendment and certificates in seed_control_demo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Verificación completa, revisión visual, registro de avance y PR

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

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y `healthcheck.py` en el formato; `makemigrations --check` sin cambios (la `0029` lo cubre todo); bandit sin hallazgos (nada de `assert` ni `xml.etree` en producción); cobertura ≥ la de `master` (93 %), con `suno/contracts` cerca del 100 %.

- [ ] **Step 2: Migración hacia atrás**

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py migrate core 0028 -v 0 && python manage.py migrate -v 0"
```

Expected: sin errores (la reversa quita las tablas de contratos y la columna `cost_entry.certificate_id`; la ida las vuelve a crear).

- [ ] **Step 3: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-contratos -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0.

- [ ] **Step 4: Revisión visual a 1440 y 390 px**

Levantar la app contra el Postgres de pruebas (sigue vivo hasta `--down`), sembrar y abrir:

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py bootstrap_demo --org-name 'SUNO Demo' --username agente-demo --password 'Revision-visual-2026' && python manage.py shell -c \"from suno.core.models import AppUser, Membership, Organization, Role; org = Organization.objects.get(name='SUNO Demo'); user = AppUser.objects.create_user(username='admin-demo', email='admin-demo@example.com', password='Revision-visual-2026', org=org); Membership.objects.create(org=org, user=user, role=Role.ADMIN)\" && python manage.py seed_control_demo"
MSYS_NO_PATHCONV=1 docker run --rm -d --name suno-contracts-web --network suno-test -p 8000:8000 \
  -v "$(cd server && pwd -W):/app" -v suno-test-venv:/venv -w /app \
  -e POSTGRES_HOST=suno-test-db -e POSTGRES_PASSWORD=test -e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
  python:3.12-slim sh -c "python manage.py runserver 0.0.0.0:8000"
```

Para entrar también como el residente (prepara actas, Revisor de Costos), darle contraseña con `docker exec suno-contracts-web python manage.py shell -c "…set_password…"`.

Entrar en `http://localhost:8000/plataforma/ingresar/` como `admin-demo` y recorrer, con capturas de Edge headless (o Playwright) a 1440 px y a 390 px (a 390 px dentro de un iframe, porque el mínimo de Edge es 496 px):

- Contratos: lista con filtros y avisos (vencido DT-001, validado sin pagar, borrador de acta), formulario nuevo de subcontrato (plantillas AIU y retenciones) y de destajo, "Agregar líneas", el aviso de traslape con la casilla, ficha con sus cinco pestañas, otrosí (formulario y aplicado), nueva acta con "Recalcular" y una cantidad ajustada sin justificación, ficha del acta (firmado, aprobar, anular), liquidación de SC-002 y su contrato liquidado, Excel de la lista y del contrato, y el PDF del acta (abrirlo: tablas dentro de A4 horizontal, firmas al final).
- Costos: "Por aprobar" con la sección de contratos (como `admin-demo`: SC-003 y el acta 4 de SC-001), libro con origen "Actas" y la etiqueta enlazada, ficha de un movimiento de acta (sin "Reversar"), un costo manual de Subcontrato en el 2.1 con el aviso.
- Control: pestaña "Por ítem" con "Comprometido en contratos" y el aviso de lo que supera el presupuesto.
- Textos largos: alargar con `python manage.py shell -c` el objeto y el contratista de un contrato y la justificación de una línea a 120 caracteres, y un valor a 15 cifras.

Revisar: tablas de líneas, cargos y descuentos dentro de su panel con desplazamiento horizontal; encabezado del formulario en una columna a 390; totales del acta alineados; botones de decisión sin desbordes. Con el adaptador local, el enlace del acta firmada es `local://…` (no abre en el navegador): es lo esperado en desarrollo. Corregir antes del PR (CSS en `projects.css`, subiendo `?v=` en `base.html`).

Al terminar: `docker rm -f suno-contracts-web` y `infra/scripts/test_docker.sh --down`.

- [ ] **Step 5: Registro de avance**

En `plans/suno-progreso.md`, sección nueva al principio de la parte de costos: "Módulo de costos — Pieza 6b (subcontratos y destajos), servidor — <fecha>", con: PR y rama; qué se entregó (modelos y migración `0029` aditiva, cálculo del acta con AIU, anticipo, garantía y descuentos, propuesta desde lo validado en campo, otrosíes, actas con firmado, aprobación con paso al libro, anulación con reversos, liquidación, pestaña "Contratos" con lista, ficha, avisos, PDF y Excel, sección en "Por aprobar", origen "Actas" del libro, comprometido en el control, `seed_control_demo` con contratos); las decisiones del plan (resumen de las anotadas en la spec, con las ⚑); los números (pruebas, cobertura, tiempos de la prueba de carga); lo que se corrigió en la revisión visual; y lo que sigue (6c materiales, 6d tareo y nómina). Actualizar "Última actualización" del encabezado.

- [ ] **Step 6: Commit, push y PR**

```bash
git add plans/suno-progreso.md
git commit -m "docs(plan): progress log for cost piece 6b

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/costos-contratos
gh pr create --base master --head feat/costos-contratos \
  --title "feat(contracts): subcontracts and piecework with certificates posted to the cost ledger" \
  --body-file <archivo temporal en el scratchpad con: resumen; qué entra (modelos y migración 0029 aditiva, cálculo, propuesta desde campo, otrosíes, actas, paso al libro, anulación, liquidación, pestaña Contratos, PDF y Excel, bandeja, libro y control, seed); decisiones del plan (las ⚑ arriba); permisos (sección Costos, sin políticas nuevas, 404/403); sin cambios de contrato ni de la app; pruebas, cobertura y tiempos de carga; capturas de la revisión visual; y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)">
```

Esperar el CI (backend, DAST, CodeQL). **No integrar el PR: el merge es del usuario.**

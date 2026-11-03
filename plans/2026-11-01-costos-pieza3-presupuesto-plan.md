# Costos — Pieza 3 (Presupuesto), servidor: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada proyecto tenga uno o varios presupuestos cargados desde el Excel propio del usuario (`.xlsx`, con mapeo de columnas guardado por presupuesto), versionados (contractual fijo + vigente = última aplicada), con cargos configurables (AIU + IVA sobre utilidad), una comparación ítem a ítem contractual contra vigente (*Mayor cantidad*, *Menor cantidad*, *Cambio de precio*, *No previsto*, *Eliminado*), historial, descarga del Excel original y exportación a Excel. El agente de campo no ve nada de esto.

**Architecture:** Modelos nuevos en `visitacampo/core/models/budget.py` (`Budget`, `BudgetVersion`, `BudgetCharge`, `BudgetItem`, `BudgetItemVersion`; todas las tablas de negocio viven en `core`, ADR-011). Lógica en el paquete nuevo `visitacampo/budgets/`: aritmética de dinero pura (`totals.py`), mapeo de columnas y sugerencia por encabezados pura (`mapping.py`), lector del Excel puro (`excel.py`: archivo seguro + interpretación de filas), resumen de cambios y etiquetas puros (`diff.py`), importación en dos pasos con BD (`importer.py`), lecturas para pantalla con coste fijo en consultas (`comparison.py`), acceso por rol (`access.py`) y vistas web (`views.py`, `urls.py`). Plantillas en `core/templates/suno/budgets/`, que extienden la base y la cabecera de proyectos (pestaña nueva **Presupuestos**). Todo acceso a modelos pasa por `scoped_qs` (lo exige `core/tests/test_architecture.py`).

**Tech Stack:** Django 5.2, PostgreSQL 16, `openpyxl` 3.1.5 (ya en `requirements.txt`, lo usa `forms/xlsform.py`), `Decimal` con `ROUND_HALF_UP`, plantillas Django, el JavaScript de plegado ya existente (`suno/js/schedule.js`).

**Spec:** `plans/2026-11-01-costos-pieza3-presupuesto-design.md` (aprobada). Planes de referencia: `plans/2026-10-31-costos-etapa1-servidor-plan.md` y `plans/2026-10-31-costos-etapa1-campo-plan.md`.

**Rama:** `feat/costos-presupuesto`, en el worktree `C:\Users\gabri\APP\wt-server-presupuesto`. Todos los comandos se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguno reabre las decisiones de la spec; precisan cómo se cumplen. Los que la completan se anotan en ella en la Task 0.

1. **Importación síncrona en dos pasos, como el cronograma.** La subida crea una `BudgetVersion` `pending` con el `.xlsx` original (`source_file`). La vista previa guarda en `summary` el resumen de cambios **y el mapeo con el que se leyó**; al confirmar, `apply_version` vuelve a leer el archivo guardado con ese mapeo (la lectura es determinista) dentro de la transacción con el presupuesto bloqueado (`select_for_update(nowait=True)`). No se guardan filas intermedias. Leer 20.000 filas con `openpyxl` en `read_only` toma unos segundos; no se usa la cola `jobs` (misma decisión que en la etapa 1).
2. **El mapeo va entre la subida y la vista previa.** Para no pedir el archivo dos veces, la versión pendiente existe desde la subida; una pendiente sin `summary["mapping"]` está "sin mapear" y su vista previa redirige a la página de mapeo. El mapeo se guarda en `Budget.column_mapping` **solo cuando produce una lectura válida** (`prepare_version`). Con mapeo guardado, la subida lo aplica directo y va a la vista previa; una casilla "Cambiar el mapeo" fuerza la página de mapeo.
3. **La columna opcional "capítulo" de §2.2 no se implementa.** El formato JSON del mapeo de la misma spec no tiene esa clave y la tabla de interpretación define el capítulo por el contenido de la fila (código y descripción sin cantidad ni valor unitario). Queda como extensión (`columns.chapter`) sin migración, porque `column_mapping` es JSON.
4. **Niveles y padres.** Capítulo con código con puntos: su padre es el capítulo más largo que sea prefijo por segmentos ("1.2" para "1.2.3"; "1.1" no es padre de "1.10"). Capítulo sin puntos: primer nivel. Ítem: el capítulo prefijo si existe; si no (códigos no jerárquicos o prefijo ausente), el último capítulo encima de él. Así "el nivel sale del orden de filas" se aplica a los ítems; un capítulo sin puntos no tiene cómo anidarse. Los niveles no se guardan: la profundidad se calcula al mostrar desde `parent`.
5. **Filas a medias.** Con código y solo cantidad o solo valor unitario → se rechaza la importación nombrando la fila (la spec no lo trata y adivinar daría totales falsos). Con código sin descripción ni números → se ignora y se cuenta. Filas totalmente vacías no cuentan.
6. **Números.** Celdas numéricas tal cual (`Decimal(repr(float))`). Texto: se quitan `$` y cualquier espacio (también NBSP, U+202F y tabuladores); si trae punto y coma, el último es el decimal; con un solo separador rige la convención colombiana (decisión 20); cuando un separador son miles, los grupos son de exactamente 3 dígitos o el texto se rechaza ("1.5.0", "12.345.6"). Fechas, booleanos y errores de Excel (`#DIV/0!`) no son números. Cantidad a 4 decimales y valor unitario a centavos con `ROUND_HALF_UP` al leer; el total del ítem se calcula con los valores ya redondeados (lo guardado siempre cumple total = cantidad × precio a centavos). Topes: cantidad < 10¹⁴, valores < 10¹⁶ (los de `numeric(18,4)`/`numeric(18,2)`).
7. **Códigos numéricos en Excel.** Un código escrito como número (`1.10`) llega como `1.1` y choca con el "1.1" real → "código repetido". Se documenta en la pantalla de carga ("formatea la columna de códigos como texto"); no hay forma de recuperar el texto original desde el valor.
8. **Fórmulas sin valor calculado.** Con `data_only=True` se lee el valor que guardó Excel/LibreOffice. Un archivo generado por un programa que no calcula (no guarda el valor) se leería vacío. No se detecta: haría falta una segunda pasada con `data_only=False`, que duplica el tiempo de lectura. Riesgo bajo: los presupuestos reales salen de Excel.
9. **Celdas combinadas.** En `read_only` openpyxl no expone las combinaciones: el valor está en la celda superior izquierda y las demás vienen vacías. Para una descripción combinada en varias columnas (el caso real) basta con mapear la primera columna; un código combinado en vertical solo cuenta en su primera fila. Se prueba con un archivo con combinaciones.
10. **Límites y archivo hostil.** Solo `.xlsx` por nombre (`.xlsm` y `.xls` con mensaje propio); 20 MB; 20.000 filas (si hay datos más abajo se **rechaza**, no se trunca en silencio); 60 columnas (A–BH, límite nuevo para acotar el ancho de cada fila); ZIP con máximo 1.000 entradas y 200 MB descomprimidos; un `vbaProject.bin` (macros) se rechaza aunque el archivo se llame `.xlsx`. `openpyxl` usa `defusedxml` (instalado) para el XML. `keep_links=False`.
11. **Resumen de la vista previa contra la versión vigente, solo ítems.** Grupos: *no previstos* (código nunca visto), *vuelven* (código que existió en alguna versión pero no está en la vigente; la spec pide probar "un eliminado que vuelve"), *eliminados*, *mayor/menor cantidad*, *cambio de precio*. Los capítulos son estructura y no se cuentan.
12. **Comparación.** Una fila puede llevar dos etiquetas (cantidad y precio). Los capítulos solo llevan *No previsto*/*Eliminado*; su diferencia se ve en los totales. Las filas eliminadas se ubican después de su hermano anterior en el orden contractual. Para *No previsto* las columnas contractuales van vacías; para *Eliminado* la cantidad y el total vigentes se muestran como 0. "Solo con cambios", búsqueda y capítulo conservan los capítulos ancestros de cada fila que pasa el filtro, para que el árbol se lea.
13. **Cargos.** Líneas numeradas 1..n (máx. 20), % entre 0 y 999,9999 con 4 decimales, base `direct`, `subtotal` o `line:<k>` con k anterior. Se editan solo en la vista previa de una versión pendiente; una versión aplicada es inmutable. La primera versión arranca con la plantilla "AIU + IVA sobre utilidad" en 0; las siguientes copian los de la vigente. Cada línea se redondea a centavos antes de servir de base a otra.
14. **Permisos.** Políticas nuevas `can_view_budgets` y `can_export_budgets` (Propietario, Administrador, Supervisor, Observador: conjunto `BUDGET_READ_ROLES`) y `can_manage_budgets` (`ADMIN_ROLES`). A diferencia del cronograma (que solo exporta supervisión), aquí el Observador exporta y descarga el Excel original, como dice la spec. El agente de campo recibe 404 en toda URL de presupuestos **antes** de mirar el proyecto, aunque vea el proyecto por ser responsable de una tarea, y no ve la pestaña.
15. **Reutilización de `projects/views.py`.** Las vistas de presupuestos importan `_ctx`, `_render`, `_project_context` y `_parse_uuid` de `visitacampo.projects.views` (nombres privados, a propósito: una sola cabecera y un solo contexto de proyecto). `_project_context` gana la clave `can_view_budgets`, que usa `_header.html` para la pestaña.
16. **Rendimiento.** `build_comparison` hace exactamente 3 consultas (versiones aplicadas, filas de las dos versiones con `values()`, cargos) y `budget_summaries` 2, sin importar el tamaño; una prueba con 3.000 ítems × 2 versiones lo fija con `assertNumQueries(3)`. Ninguna lectura de listas carga `source_file` (hasta 20 MB por versión): `.defer("source_file")`.
17. **Exportación.** Exporta la comparación completa (sin filtros, como "Exportar cronograma completo"), con las líneas de cargos y totales al final. `to_xlsx` solo conoce `int`/`float`: los `Decimal` se pasan a `float` (dos decimales, sin pérdida visible).
18. **Formato en pantalla.** `floatformat:"2g"` con el locale `es` → `1.234.567,89`; porcentajes con `floatformat:2` → `-4,82 %`. Sin símbolo de moneda (moneda de la organización, §Fuera de alcance).
19. **URL.** Se monta `plataforma/proyectos/<uuid:project_id>/presupuestos/` en `config/urls.py` con `app_name = "budgets"`, después del include de proyectos.
20. **Un solo separador en texto (convención colombiana).** La coma es decimal ("1,234" = 1,234; "1,5" = 1,5; varias comas con grupos de 3 son miles). Un punto seguido de exactamente 3 dígitos son miles ("1.234" = 1234), salvo con entero "0" ("0.333" = 0,333); un punto con otra cantidad de dígitos es decimal ("1.5"). Cada celda de texto leída con la regla "punto + 3 dígitos" se cuenta y la vista previa lo avisa en `summary["warnings"]`. Las celdas numéricas de Excel no se ven afectadas. Aritmética con contexto decimal de 60 dígitos (`money`, `quantity`, `item_total`) para que un producto enorme llegue al rechazo "el total es demasiado grande" en vez de una excepción. `BudgetVersion` lleva una restricción: `number` no es nulo si y solo si `status = applied`.

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test` (no `pytest-django`). Los que tocan BD usan `visitacampo/core/tests/dbsupport.py`:
  ```python
  setUpModule = setup_test_database
  tearDownModule = teardown_test_database
  skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")
  ```
- Fábricas: `visitacampo.authn.tests.factories.make_org(name)` y `make_user(org, username=..., role=Role.X)`. Contexto: `resolve_request_context(Principal(user_id=..., org_id=...))`.
- Ningún módulo de producción usa `Model.objects`: siempre `scoped_qs(ctx, Model)`; al crear, `org_id=ctx.org_id`. Los tests sí pueden usar `Model.objects`.
- Toda política nueva `can_*` va en `__all__` de `core/policies.py`, con su `check_*`, y se registra en `core/tests/test_policies.py::NoRoleGrantsNothingTests._registry`.
- Auditoría: `visitacampo.core.audit.record(ctx, action=AuditAction.X, target=..., metadata=...)` dentro de `transaction.atomic()` junto con el cambio.
- Excel de salida: `visitacampo.backoffice.export.to_xlsx(header, rows)` y `safe_cell(value)`.
- Comentarios y textos de interfaz en español; código en inglés. Commits en inglés, estilo conventional. Líneas de 100 caracteres (ruff); imports ordenados por ruff (`I`): si `ruff check` se queja de orden, `ruff check --fix` lo resuelve.

## Cómo correr las pruebas

No hay Python 3.12 en la máquina de desarrollo: todo corre en Docker con `infra/scripts/test_docker.sh`. Docker Desktop debe estar arrancado.

```bash
infra/scripts/test_docker.sh visitacampo/budgets                    # un paquete
infra/scripts/test_docker.sh visitacampo/budgets/tests/test_totals.py
infra/scripts/test_docker.sh                                        # suite completa
infra/scripts/test_docker.sh --cmd "ruff check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                 # al terminar
```

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/visitacampo/core/models/budget.py` | `Budget`, `BudgetVersion`, `BudgetCharge`, `BudgetItem`, `BudgetItemVersion` y sus enumeraciones |
| `server/visitacampo/core/models/__init__.py` | Reexporta los modelos nuevos |
| `server/visitacampo/core/migrations/0013_add_budgets.py` | Generada |
| `server/visitacampo/core/policies.py` | `BUDGET_READ_ROLES`, `can_/check_view_budgets`, `_manage_budgets`, `_export_budgets` |
| `server/visitacampo/core/audit.py` | Códigos `budgets.*` |
| `server/visitacampo/budgets/totals.py` | Dinero, números, cargos (puro) |
| `server/visitacampo/budgets/mapping.py` | `ColumnMapping`, sugerencia por encabezados, `fold` (puro) |
| `server/visitacampo/budgets/excel.py` | Archivo `.xlsx` seguro, vista de hoja, lectura e interpretación de filas (puro) |
| `server/visitacampo/budgets/diff.py` | Etiquetas de comparación y resumen de cambios (puro) |
| `server/visitacampo/budgets/importer.py` | Subir, preparar, editar, aplicar, descartar, marcar contractual |
| `server/visitacampo/budgets/comparison.py` | Comparación, filtros, filas de una versión, totales por proyecto |
| `server/visitacampo/budgets/access.py` | 404 para quien no ve presupuestos; resolución de proyecto y presupuesto |
| `server/visitacampo/budgets/views.py`, `urls.py` | Páginas `/plataforma/proyectos/<id>/presupuestos/` |
| `server/visitacampo/budgets/tests/…` | Pruebas, `xlsx.py` (libros en memoria), `samples.py`, `factories.py` |
| `server/config/urls.py` | Monta las URL de presupuestos |
| `server/visitacampo/projects/views.py` | `_project_context` añade `can_view_budgets` |
| `server/visitacampo/core/templates/suno/projects/_header.html` | Pestaña **Presupuestos** |
| `server/visitacampo/core/templates/suno/budgets/*.html` | Plantillas del módulo |
| `server/visitacampo/core/static/suno/css/projects.css` | Estilos de presupuestos (prefijo `bg-`) |

---

### Task 0: Rama y notas en la spec

**Files:**
- Modify: `plans/2026-11-01-costos-pieza3-presupuesto-design.md`

- [ ] **Step 1: Comprobar la rama**

Run: `git -C C:/Users/gabri/APP/wt-server-presupuesto status -sb`
Expected: `## feat/costos-presupuesto` y árbol limpio (salvo este plan).

- [ ] **Step 2: Anotar en la spec las decisiones tomadas al planear**

Al final de `plans/2026-11-01-costos-pieza3-presupuesto-design.md` añadir:

```markdown
## Decisiones tomadas al planear

Precisiones del plan `2026-11-01-costos-pieza3-presupuesto-plan.md`; no cambian lo aprobado.

1. **Mapeo entre subida y vista previa.** La versión pendiente se crea al subir el archivo;
   si el presupuesto no tiene mapeo (o se pide cambiarlo) se pasa por la página de mapeo.
   El mapeo se guarda en el presupuesto solo cuando produce una lectura válida, y la vista
   previa guarda el mapeo con que se leyó: al confirmar se vuelve a leer el archivo con él.
2. **Columna "capítulo".** No se implementa: el capítulo se reconoce por la fila (código y
   descripción sin cantidad ni valor unitario). Queda como extensión `columns.chapter`.
3. **Niveles.** Capítulo con puntos: su padre es el capítulo prefijo más largo. Capítulo sin
   puntos: primer nivel. Ítem: el capítulo prefijo o, si no hay, el último capítulo encima.
4. **Filas a medias.** Con código y solo cantidad o solo valor unitario: se rechaza nombrando
   la fila. Con código sin descripción ni números: se ignora y se cuenta.
5. **Números en texto.** Se aceptan con separadores de miles y decimales ("1.234,56",
   "1,234.56"); cantidad a 4 decimales y valor unitario a centavos (`ROUND_HALF_UP`) al leer.
6. **Límites.** Además de 20 MB y 20.000 filas: 60 columnas (A–BH), ZIP de máximo 1.000
   entradas y 200 MB descomprimidos, y rechazo de macros aunque el archivo se llame `.xlsx`.
   Datos más allá de la fila 20.000 rechazan la importación (no se trunca).
7. **Vista previa.** Compara contra la versión vigente y solo cuenta ítems; añade el grupo
   "vuelven" (ítems que existieron antes y regresan con la misma identidad).
8. **Cargos.** Máximo 20 líneas numeradas en orden; solo se editan en una versión pendiente.
9. **Exportación.** El Excel de la comparación es completo (sin filtros), con cargos y totales.
```

- [ ] **Step 3: Commit**

```bash
git add plans/2026-11-01-costos-pieza3-presupuesto-design.md plans/2026-11-01-costos-pieza3-presupuesto-plan.md
git commit -m "docs(plan): budget implementation plan; note planning decisions in spec"
```

---

### Task 1: Modelos y migración

**Files:**
- Create: `server/visitacampo/core/models/budget.py`
- Modify: `server/visitacampo/core/models/__init__.py`
- Create (generada): `server/visitacampo/core/migrations/0013_add_budgets.py`
- Create: `server/visitacampo/budgets/__init__.py`, `server/visitacampo/budgets/tests/__init__.py`
- Test: `server/visitacampo/budgets/tests/test_models.py`

- [ ] **Step 1: Crear los paquetes vacíos**

`server/visitacampo/budgets/__init__.py`:

```python
"""Módulo de costos y presupuesto — pieza 3: presupuestos del proyecto."""
```

`server/visitacampo/budgets/tests/__init__.py`: archivo vacío.

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_models.py`:

```python
"""Restricciones de BD del modelo de presupuestos."""

from __future__ import annotations

import unittest
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class BudgetModelTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.user = make_user(self.org, username="admin", role=Role.ADMIN)
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.user)
        self.budget = Budget.objects.create(
            org=self.org, project=self.project, name="Contrato", created_by=self.user
        )

    def _version(self, number: int | None) -> BudgetVersion:
        return BudgetVersion.objects.create(
            org=self.org,
            budget=self.budget,
            number=number,
            status=BudgetVersionStatus.APPLIED if number else BudgetVersionStatus.PENDING,
            file_name="p.xlsx",
            source_file=b"",
            uploaded_by=self.user,
        )

    def test_budget_name_is_unique_per_project_ignoring_case(self) -> None:
        other = Project.objects.create(org=self.org, name="Otra", created_by=self.user)
        Budget.objects.create(org=self.org, project=other, name="Contrato", created_by=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Budget.objects.create(
                org=self.org, project=self.project, name="CONTRATO", created_by=self.user
            )

    def test_version_number_is_unique_per_budget_but_pending_has_none(self) -> None:
        self._version(None)
        self._version(None)
        self._version(1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._version(1)

    def test_item_code_is_unique_per_budget(self) -> None:
        BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")

    def test_row_values_follow_their_kind(self) -> None:
        version = self._version(1)
        item = BudgetItem.objects.create(org=self.org, budget=self.budget, code="1.1")

        def row(kind: str, quantity: str | None, price: str | None) -> BudgetItemVersion:
            return BudgetItemVersion.objects.create(
                org=self.org,
                version=version,
                item=item,
                kind=kind,
                sort_order=0,
                description="Excavación",
                quantity=None if quantity is None else Decimal(quantity),
                unit_price=None if price is None else Decimal(price),
                total=Decimal("0"),
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.ITEM, None, "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.CHAPTER, "1", None)
        row(BudgetRowKind.ITEM, "2", "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            row(BudgetRowKind.ITEM, "3", "10")  # (versión, ítem) único

    def test_charge_pct_is_not_negative_and_order_is_unique(self) -> None:
        version = self._version(1)

        def charge(order: int, pct: str) -> BudgetCharge:
            return BudgetCharge.objects.create(
                org=self.org,
                version=version,
                order=order,
                name="A",
                pct=Decimal(pct),
                base="direct",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            charge(1, "-1")
        charge(1, "10")
        with self.assertRaises(IntegrityError), transaction.atomic():
            charge(1, "5")
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_models.py`
Expected: FAIL con `ImportError: cannot import name 'Budget'`.

- [ ] **Step 4: Escribir los modelos**

`server/visitacampo/core/models/budget.py`:

```python
"""Presupuestos de proyectos (módulo de costos, pieza 3).

Diseño: plans/2026-11-01-costos-pieza3-presupuesto-design.md.

- `Budget`: un presupuesto del proyecto (p. ej. por contrato o fuente de
  financiación), con el mapeo de columnas de su Excel y su versión contractual.
- `BudgetVersion`: cada Excel subido. Nace `pending` (vista previa); al
  confirmarse pasa a `applied` con su `number` consecutivo. La vigente es la
  última aplicada. Las descartadas quedan como rastro.
- `BudgetItem`: identidad estable de una fila (capítulo o ítem) por su código.
  **Nunca se borra**: lo que la pieza 4 cuelgue de aquí (homologación con
  tareas) sobrevive a todas las versiones.
- `BudgetItemVersion`: los valores de una fila en una versión.
- `BudgetCharge`: líneas de indirectos e impuestos de una versión (AIU, IVA…).

El dinero es `numeric` y `Decimal`; la aritmética vive en
`visitacampo.budgets.totals`.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models.functions import Lower

from .base import OrgScopedModel


class Budget(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "core.Project", on_delete=models.PROTECT, related_name="budgets", verbose_name="proyecto"
    )
    name = models.CharField(max_length=200, verbose_name="nombre")
    code = models.CharField(max_length=50, blank=True, verbose_name="código")
    description = models.TextField(blank=True, verbose_name="descripción")
    #: `{"sheet", "first_row", "columns"}`: ver `budgets.mapping.ColumnMapping`.
    #: Vacío hasta la primera lectura válida; se reutiliza en las versiones siguientes.
    column_mapping = models.JSONField(default=dict, blank=True, verbose_name="mapeo de columnas")
    #: Nula hasta la primera versión aplicada, que queda como contractual.
    contractual_version = models.ForeignKey(
        "core.BudgetVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="versión contractual",
    )
    created_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="creado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "budget"
        ordering = ["name"]
        verbose_name = "presupuesto"
        verbose_name_plural = "presupuestos"
        constraints = [
            models.UniqueConstraint(Lower("name"), "project", name="budget_project_name_ci_uq"),
        ]

    def __str__(self) -> str:
        return self.name


class BudgetVersionStatus(models.TextChoices):
    PENDING = "pending", "Pendiente de confirmar"
    APPLIED = "applied", "Aplicada"
    DISCARDED = "discarded", "Descartada"


class BudgetVersion(OrgScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(
        Budget, on_delete=models.PROTECT, related_name="versions", verbose_name="presupuesto"
    )
    #: Consecutivo por presupuesto; solo lo tienen las aplicadas.
    number = models.PositiveIntegerField(null=True, blank=True, verbose_name="versión")
    status = models.CharField(
        max_length=16,
        choices=BudgetVersionStatus.choices,
        default=BudgetVersionStatus.PENDING,
        verbose_name="estado",
    )
    label = models.CharField(max_length=200, blank=True, verbose_name="etiqueta")
    file_name = models.CharField(max_length=255, verbose_name="archivo")
    #: El `.xlsx` original (ya es un ZIP: no se comprime otra vez).
    source_file = models.BinaryField(verbose_name="Excel original")
    uploaded_by = models.ForeignKey(
        "core.AppUser", on_delete=models.PROTECT, related_name="+", verbose_name="subido por"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    #: Resumen de cambios, advertencias y el mapeo usado: ver
    #: `budgets.diff.summarize_changes` y `budgets.importer.prepare_version`.
    summary = models.JSONField(default=dict, blank=True)
    direct_cost = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True, verbose_name="costo directo"
    )
    total = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "budget_version"
        ordering = ["-uploaded_at"]
        verbose_name = "versión de presupuesto"
        verbose_name_plural = "versiones de presupuesto"
        constraints = [
            models.UniqueConstraint(
                fields=["budget", "number"],
                condition=models.Q(number__isnull=False),
                name="budget_version_number_uq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.budget} v{self.number or '?'}"


class BudgetCharge(OrgScopedModel):
    """Una línea de cargos (indirectos o impuestos) de una versión."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(BudgetVersion, on_delete=models.CASCADE, related_name="charges")
    order = models.PositiveSmallIntegerField(verbose_name="orden")
    name = models.CharField(max_length=100, verbose_name="nombre")
    pct = models.DecimalField(max_digits=7, decimal_places=4, verbose_name="porcentaje")
    #: `direct`, `subtotal` o `line:<orden>` (ver `budgets.totals`).
    base = models.CharField(max_length=20, verbose_name="base")
    #: Calculado al aplicar la versión; nulo mientras está pendiente.
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "budget_charge"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["version", "order"], name="budget_charge_order_uq"),
            models.CheckConstraint(condition=models.Q(pct__gte=0), name="budget_charge_pct_gte_0"),
        ]


class BudgetItem(OrgScopedModel):
    """Identidad estable de una fila del presupuesto, por su código."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(Budget, on_delete=models.PROTECT, related_name="items")
    code = models.CharField(max_length=50, verbose_name="código")

    class Meta:
        db_table = "budget_item"
        constraints = [
            models.UniqueConstraint(fields=["budget", "code"], name="budget_item_code_uq"),
        ]

    def __str__(self) -> str:
        return self.code


class BudgetRowKind(models.TextChoices):
    CHAPTER = "chapter", "Capítulo"
    ITEM = "item", "Ítem"


class BudgetItemVersion(OrgScopedModel):
    """Los valores de una fila (`BudgetItem`) en una versión."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(BudgetVersion, on_delete=models.CASCADE, related_name="rows")
    item = models.ForeignKey(BudgetItem, on_delete=models.PROTECT, related_name="values")
    kind = models.CharField(max_length=10, choices=BudgetRowKind.choices, verbose_name="tipo")
    #: Posición de la fila en el Excel de esta versión (0, 1, 2…).
    sort_order = models.IntegerField(verbose_name="orden")
    description = models.TextField(blank=True, verbose_name="descripción")
    unit = models.CharField(max_length=30, blank=True, verbose_name="unidad")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    #: Ítem: cantidad × valor unitario a centavos. Capítulo: suma de sus hijos.
    total = models.DecimalField(max_digits=18, decimal_places=2)
    #: Capítulo que contiene la fila en esta versión; nulo en el primer nivel.
    parent = models.ForeignKey(
        BudgetItem, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "budget_item_version"
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["version", "item"], name="budget_item_version_uq"),
            models.CheckConstraint(
                condition=(
                    models.Q(kind="item", quantity__isnull=False, unit_price__isnull=False)
                    | models.Q(kind="chapter", quantity__isnull=True, unit_price__isnull=True)
                ),
                name="budget_item_version_kind_values",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "sort_order"], name="budget_row_version_order_idx"),
        ]
```

- [ ] **Step 5: Reexportar en `core/models/__init__.py`**

En el docstring, tras la línea de `project`, añadir:

```
- `budget`   — `Budget`, `BudgetVersion`, `BudgetItem` y sus tablas (costos, pieza 3).
```

Tras `from .basemap import BasemapPackage, TileFormat` añadir (orden alfabético de módulos, que exige ruff):

```python
from .budget import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
)
```

y en `__all__`, tras `"BasemapPackage",`, añadir:

```python
    "Budget",
    "BudgetCharge",
    "BudgetItem",
    "BudgetItemVersion",
    "BudgetRowKind",
    "BudgetVersion",
    "BudgetVersionStatus",
```

- [ ] **Step 6: Generar la migración**

Run: `infra/scripts/test_docker.sh --cmd "python manage.py makemigrations core -n add_budgets"`
Expected: `Migrations for 'core': visitacampo/core/migrations/0013_add_budgets.py` con las 5 tablas (Django crea `Budget` sin `contractual_version` y añade ese campo después de `BudgetVersion`, por la referencia circular).

- [ ] **Step 7: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_models.py visitacampo/core/tests/test_models.py visitacampo/core/tests/test_scoping.py`
Expected: todo pasa (las invariantes de `org_id` cubren los modelos nuevos sin tocarlas).

- [ ] **Step 8: Commit**

```bash
git add server/visitacampo/core/models server/visitacampo/core/migrations/0013_add_budgets.py server/visitacampo/budgets
git commit -m "feat(budgets): budget data model (budget, version, item, item values, charges)"
```

---

### Task 2: Políticas y códigos de auditoría

**Files:**
- Modify: `server/visitacampo/core/policies.py`
- Modify: `server/visitacampo/core/audit.py`
- Modify: `server/visitacampo/core/tests/test_policies.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/visitacampo/core/tests/test_policies.py`, en `NoRoleGrantsNothingTests._registry`, tras `"can_read_field_tasks": ORG,` añadir:

```python
            # Presupuestos (costos, pieza 3): todos menos el agente de campo
            # ven y exportan; administración gestiona.
            "can_view_budgets": ORG,
            "can_manage_budgets": ORG,
            "can_export_budgets": ORG,
```

y antes de la línea `# --- Deny-by-default, para toda política presente y futura ---` añadir la clase:

```python
class BudgetPoliciesTests(SimpleTestCase):
    """Costos, pieza 3: el agente de campo no ve presupuestos."""

    def test_everyone_but_the_field_agent_views_and_exports(self) -> None:
        for policy in (policies.can_view_budgets, policies.can_export_budgets):
            for role in (Role.OWNER, Role.ADMIN, Role.SUPERVISOR, Role.VIEWER):
                with self.subTest(policy=policy.__name__, role=role):
                    self.assertTrue(policy(ctx(role), ORG))
            with self.subTest(policy=policy.__name__, role=Role.FIELD_AGENT):
                self.assertFalse(policy(ctx(Role.FIELD_AGENT), ORG))

    def test_only_administration_manages(self) -> None:
        for role in (Role.OWNER, Role.ADMIN):
            with self.subTest(role=role):
                self.assertTrue(policies.can_manage_budgets(ctx(role), ORG))
        for role in (Role.SUPERVISOR, Role.FIELD_AGENT, Role.VIEWER):
            with self.subTest(role=role):
                self.assertFalse(policies.can_manage_budgets(ctx(role), ORG))

    def test_another_organization_gets_404(self) -> None:
        with self.assertRaises(HttpError) as raised:
            policies.check_view_budgets(ctx(Role.ADMIN, org_id=OTHER_ORG), ORG)
        self.assertEqual(raised.exception.status_code, 404)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/core/tests/test_policies.py`
Expected: FAIL (`AttributeError: module ... has no attribute 'can_view_budgets'` y el registro no coincide).

- [ ] **Step 3: Escribir las políticas**

En `server/visitacampo/core/policies.py`:

En `__all__`, añadir (manteniendo el orden alfabético de la lista): `"BUDGET_READ_ROLES"` tras `"ADMIN_ROLES"`; `"can_export_budgets"` antes de `"can_export_schedule"`; `"can_manage_budgets"` antes de `"can_manage_device"`; `"can_view_budgets"` tras `"can_upload_attachment"`; `"check_export_budgets"` antes de `"check_export_schedule"`; `"check_manage_budgets"` antes de `"check_manage_device"`; `"check_view_budgets"` tras `"check_upload_attachment"`.

Tras la definición de `PROJECT_READ_ALL_ROLES` añadir:

```python
#: Presupuestos (costos, pieza 3): quién los ve y exporta. El `FIELD_AGENT` no:
#: los valores del contrato no son información de campo.
BUDGET_READ_ROLES: frozenset[RoleValue] = frozenset(
    {Role.OWNER, Role.ADMIN, Role.SUPERVISOR, Role.VIEWER}
)
```

Tras `can_read_field_tasks` añadir:

```python
def can_view_budgets(ctx: RequestContext, org_id: UUID) -> bool:
    """Ver los presupuestos de los proyectos: todos los roles menos el agente de campo."""
    return user_has_organization_roles(ctx, org_id, BUDGET_READ_ROLES)


def can_manage_budgets(ctx: RequestContext, org_id: UUID) -> bool:
    """Crear presupuestos, importar versiones, editar cargos y marcar la contractual."""
    return user_has_organization_roles(ctx, org_id, ADMIN_ROLES)


def can_export_budgets(ctx: RequestContext, org_id: UUID) -> bool:
    """Exportar la comparación y descargar el Excel original (queda en la auditoría).

    A diferencia del cronograma, el Observador sí exporta: la spec de la pieza 3
    da a Supervisor y Observador "ver y exportar".
    """
    return user_has_organization_roles(ctx, org_id, BUDGET_READ_ROLES)
```

Y al final del archivo:

```python
def check_view_budgets(ctx: RequestContext, org_id: UUID) -> None:
    if not can_view_budgets(ctx, org_id):
        raise _denial(ctx, org_id)


def check_manage_budgets(ctx: RequestContext, org_id: UUID) -> None:
    if not can_manage_budgets(ctx, org_id):
        raise _denial(ctx, org_id)


def check_export_budgets(ctx: RequestContext, org_id: UUID) -> None:
    if not can_export_budgets(ctx, org_id):
        raise _denial(ctx, org_id)
```

- [ ] **Step 4: Códigos de auditoría**

En `server/visitacampo/core/audit.py`, al final de la clase `AuditAction` (tras `SCHEDULE_SOURCE_DOWNLOADED`), añadir:

```python
    # Presupuestos (costos, pieza 3).
    BUDGET_CREATED = "budgets.created"
    BUDGET_VERSION_APPLIED = "budgets.version_applied"
    BUDGET_VERSION_DISCARDED = "budgets.version_discarded"
    BUDGET_CONTRACTUAL_CHANGED = "budgets.contractual_changed"
    BUDGET_EXPORTED = "budgets.exported"
    BUDGET_SOURCE_DOWNLOADED = "budgets.source_downloaded"
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/core/tests/test_policies.py`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/core/policies.py server/visitacampo/core/audit.py server/visitacampo/core/tests/test_policies.py
git commit -m "feat(budgets): budget policies and audit codes"
```

---
### Task 3: Aritmética del presupuesto (`totals.py`)

**Files:**
- Create: `server/visitacampo/budgets/totals.py`
- Test: `server/visitacampo/budgets/tests/test_totals.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_totals.py`:

```python
"""Dinero, números y cargos del presupuesto (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal

from visitacampo.budgets.totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    ChargeError,
    ChargeSpec,
    compute_charges,
    default_charges,
    describe_base,
    item_total,
    line_base,
    money,
    parse_number,
    pct_change,
    pct_text,
    validate_charges,
)

D = Decimal


class MoneyTests(unittest.TestCase):
    def test_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(money(D("0.125")), D("0.13"))
        self.assertEqual(money(D("2.675")), D("2.68"))
        self.assertEqual(money(D("-0.125")), D("-0.13"))

    def test_item_total_is_quantity_times_price_in_cents(self) -> None:
        self.assertEqual(item_total(D("2.5"), D("0.33")), D("0.83"))  # 0,825 sube

    def test_pct_change(self) -> None:
        self.assertEqual(pct_change(D("100"), D("120")), D("20.00"))
        self.assertEqual(pct_change(D("3"), D("2")), D("-33.33"))
        self.assertIsNone(pct_change(D("0"), D("5")))

    def test_pct_text(self) -> None:
        self.assertEqual(pct_text(D("10.0000")), "10")
        self.assertEqual(pct_text(D("12.5000")), "12,5")
        self.assertEqual(pct_text(D("0")), "0")


class ParseNumberTests(unittest.TestCase):
    def test_numbers_and_text(self) -> None:
        cases = {
            None: None,
            "": None,
            "  ": None,
            3: D("3"),
            0.1: D("0.1"),
            D("1.50"): D("1.50"),
            "1.234,56": D("1234.56"),
            "1,234.56": D("1234.56"),
            "1.234.567": D("1234567"),
            "12,5": D("12.5"),
            "1,234,567": D("1234567"),
            "1.5": D("1.5"),
            "$ 1.500,00": D("1500.00"),
            "-5": D("-5"),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_number(value), expected)

    def test_rejects_what_is_not_a_number(self) -> None:
        for value in ("abc", "1.2.3,4,5", "#DIV/0!", True, float("nan"), float("inf"), "."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_number(value)


class ChargesTests(unittest.TestCase):
    def test_each_base_in_order_rounding_each_line(self) -> None:
        result = compute_charges(
            D("1234.50"),
            [
                ChargeSpec(1, "Administración", D("10"), BASE_DIRECT),
                ChargeSpec(2, "Póliza", D("2"), BASE_SUBTOTAL),
                ChargeSpec(3, "Sobre la administración", D("50"), line_base(1)),
            ],
        )
        self.assertEqual(
            [line.amount for line in result.lines], [D("123.45"), D("27.16"), D("61.73")]
        )
        self.assertEqual(result.lines[1].base_amount, D("1357.95"))  # directo + línea 1
        self.assertEqual(result.total, D("1446.84"))

    def test_aiu_template(self) -> None:
        template = default_charges()
        self.assertEqual(
            [(c.order, c.name, c.pct, c.base) for c in template],
            [
                (1, "Administración", D("0"), "direct"),
                (2, "Imprevistos", D("0"), "direct"),
                (3, "Utilidad", D("0"), "direct"),
                (4, "IVA sobre utilidad", D("0"), "line:3"),
            ],
        )
        pcts = [D("10"), D("5"), D("5"), D("19")]
        charges = [
            ChargeSpec(c.order, c.name, p, c.base) for c, p in zip(template, pcts, strict=True)
        ]
        result = compute_charges(D("9130000.00"), charges)
        self.assertEqual(
            [line.amount for line in result.lines],
            [D("913000.00"), D("456500.00"), D("456500.00"), D("86735.00")],
        )
        self.assertEqual(result.total, D("11042735.00"))

    def test_no_charges_total_is_direct_cost(self) -> None:
        self.assertEqual(compute_charges(D("10.00"), []).total, D("10.00"))

    def test_invalid_charges(self) -> None:
        bad = {
            "base posterior": [ChargeSpec(1, "A", D("1"), line_base(1))],
            "base desconocida": [ChargeSpec(1, "A", D("1"), "otra")],
            "orden con hueco": [ChargeSpec(2, "A", D("1"), BASE_DIRECT)],
            "negativo": [ChargeSpec(1, "A", D("-1"), BASE_DIRECT)],
            "cinco decimales": [ChargeSpec(1, "A", D("1.00001"), BASE_DIRECT)],
            "demasiado alto": [ChargeSpec(1, "A", D("1000"), BASE_DIRECT)],
            "sin nombre": [ChargeSpec(1, "  ", D("1"), BASE_DIRECT)],
            "demasiadas": [ChargeSpec(i, "A", D("1"), BASE_DIRECT) for i in range(1, 22)],
        }
        for case, charges in bad.items():
            with self.subTest(case=case), self.assertRaises(ChargeError):
                validate_charges(charges)

    def test_describe_base(self) -> None:
        names = {3: "Utilidad"}
        self.assertEqual(describe_base("direct", names), "costo directo")
        self.assertEqual(describe_base("subtotal", names), "subtotal")
        self.assertEqual(describe_base("line:3", names), "línea 3 (Utilidad)")
        self.assertEqual(describe_base("line:2", names), "línea 2")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_totals.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.budgets.totals'`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/budgets/totals.py`:

```python
"""Aritmética del presupuesto (pura: sin BD).

Todo el dinero es `Decimal` y se redondea a centavos con `ROUND_HALF_UP` (la
regla comercial: 0,005 sube), nunca con el redondeo bancario por defecto de
Python. Las cantidades se guardan con 4 decimales; los porcentajes de los
cargos, también.

Cargos (§1 de la spec): líneas en orden, cada una con un % sobre una base:

- `direct`: el costo directo (suma de los ítems);
- `line:<n>`: el valor de la línea `n`, que tiene que ser anterior;
- `subtotal`: costo directo + todas las líneas anteriores.

Cada línea se redondea a centavos antes de servir de base a otra: así la
pantalla, la BD y el Excel exportado suman lo mismo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
QUANTITY_STEP = Decimal("0.0001")
PCT_STEP = Decimal("0.0001")
MAX_PCT = Decimal("1000")
HUNDRED = Decimal(100)
ZERO = Decimal("0.00")

BASE_DIRECT = "direct"
BASE_SUBTOTAL = "subtotal"
LINE_PREFIX = "line:"
MAX_CHARGES = 20
MAX_CHARGE_NAME = 100

_LINE = re.compile(r"line:([1-9][0-9]?)")
_NUMBER_TEXT = re.compile(r"[0-9.,]+")


class ChargeError(ValueError):
    """Los cargos no son válidos; el mensaje es para el usuario."""


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


def item_total(qty: Decimal, unit_price: Decimal) -> Decimal:
    """Total de un ítem: cantidad × valor unitario, a centavos."""
    return money(qty * unit_price)


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    """Variación porcentual de `old` a `new`, con 2 decimales; None si `old` es 0."""
    if old == 0:
        return None
    return money((new - old) / old * HUNDRED)


def pct_text(value: Decimal) -> str:
    """Un porcentaje para leer: `10.0000` → "10", `12.5000` → "12,5"."""
    return format(value.normalize(), "f").replace(".", ",")


def parse_number(value: object) -> Decimal | None:
    """Número de una celda o de un campo de texto; None si está vacío.

    Lanza `ValueError` si no es un número. En texto acepta separadores de miles
    y decimales en los dos estilos ("1.234,56" y "1,234.56"), `$` y espacios.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("booleano")
    if isinstance(value, int | Decimal):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.replace("$", "").replace(" ", "").replace(" ", "").strip()
        if not text:
            return None
        negative = text.startswith("-")
        if negative:
            text = text[1:]
        if not _NUMBER_TEXT.fullmatch(text):
            raise ValueError(value)
        try:
            result = Decimal(_normalize_separators(text))
        except InvalidOperation as exc:
            raise ValueError(value) from exc
        if negative:
            result = -result
    else:
        raise ValueError(value)
    if not result.is_finite():
        raise ValueError(value)
    return result


def _normalize_separators(text: str) -> str:
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        text = text.replace(thousands, "")
        if text.count(decimal_sep) > 1:
            raise ValueError(text)
        return text.replace(",", ".")
    if commas:
        return text.replace(",", "") if commas > 1 else text.replace(",", ".")
    if dots > 1:
        return text.replace(".", "")
    return text


@dataclass(frozen=True)
class ChargeSpec:
    order: int
    name: str
    pct: Decimal
    base: str


@dataclass(frozen=True)
class ChargeLine:
    order: int
    name: str
    pct: Decimal
    base: str
    base_amount: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ChargeResult:
    direct_cost: Decimal
    lines: tuple[ChargeLine, ...]
    total: Decimal


def line_base(order: int) -> str:
    return f"{LINE_PREFIX}{order}"


def base_line_order(base: str) -> int | None:
    """`line:3` → 3; None si la base no es una línea."""
    match = _LINE.fullmatch(base)
    return int(match.group(1)) if match else None


def describe_base(base: str, names: Mapping[int, str]) -> str:
    """La base en palabras: "costo directo", "subtotal" o "línea 3 (Utilidad)"."""
    if base == BASE_DIRECT:
        return "costo directo"
    if base == BASE_SUBTOTAL:
        return "subtotal"
    order = base_line_order(base)
    if order is None:
        return base
    name = names.get(order)
    return f"línea {order} ({name})" if name else f"línea {order}"


def default_charges() -> list[ChargeSpec]:
    """Plantilla "AIU + IVA sobre utilidad", con los porcentajes en 0."""
    return [
        ChargeSpec(1, "Administración", Decimal("0"), BASE_DIRECT),
        ChargeSpec(2, "Imprevistos", Decimal("0"), BASE_DIRECT),
        ChargeSpec(3, "Utilidad", Decimal("0"), BASE_DIRECT),
        ChargeSpec(4, "IVA sobre utilidad", Decimal("0"), line_base(3)),
    ]


def validate_charges(charges: Sequence[ChargeSpec]) -> None:
    if len(charges) > MAX_CHARGES:
        raise ChargeError(f"Máximo {MAX_CHARGES} líneas de cargos.")
    for expected, charge in enumerate(charges, start=1):
        if charge.order != expected:
            raise ChargeError("Las líneas de cargos deben numerarse 1, 2, 3… en orden.")
        name = charge.name.strip()
        if not name or len(name) > MAX_CHARGE_NAME:
            raise ChargeError(
                f"Línea {expected}: el nombre es obligatorio y tiene máximo "
                f"{MAX_CHARGE_NAME} caracteres."
            )
        pct = charge.pct
        if (
            not pct.is_finite()
            or pct < 0
            or pct >= MAX_PCT
            or pct != pct.quantize(PCT_STEP, rounding=ROUND_HALF_UP)
        ):
            raise ChargeError(
                f"Línea {expected}: el porcentaje debe estar entre 0 y 999,9999, "
                "con máximo 4 decimales."
            )
        if charge.base in (BASE_DIRECT, BASE_SUBTOTAL):
            continue
        ref = base_line_order(charge.base)
        if ref is None or ref >= expected:
            raise ChargeError(
                f"Línea {expected}: la base debe ser el costo directo, el subtotal "
                "o una línea anterior."
            )


def compute_charges(direct_cost: Decimal, charges: Sequence[ChargeSpec]) -> ChargeResult:
    """Valor de cada línea, en orden, y el total de la versión."""
    validate_charges(charges)
    amounts: dict[int, Decimal] = {}
    lines: list[ChargeLine] = []
    for charge in charges:
        if charge.base == BASE_DIRECT:
            base_amount = direct_cost
        elif charge.base == BASE_SUBTOTAL:
            base_amount = direct_cost + sum(amounts.values(), ZERO)
        else:
            base_amount = amounts[base_line_order(charge.base) or 0]
        amount = money(base_amount * charge.pct / HUNDRED)
        amounts[charge.order] = amount
        lines.append(
            ChargeLine(
                order=charge.order,
                name=charge.name.strip(),
                pct=charge.pct,
                base=charge.base,
                base_amount=base_amount,
                amount=amount,
            )
        )
    total = money(direct_cost + sum(amounts.values(), ZERO))
    return ChargeResult(direct_cost=direct_cost, lines=tuple(lines), total=total)
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_totals.py`
Expected: todo pasa. (Si el caso `"1.2.3,4,5"` no fallara, revisar `_normalize_separators`: con puntos y comas, quita los de miles y exige un solo separador decimal.)

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/budgets/totals.py server/visitacampo/budgets/tests/test_totals.py
git commit -m "feat(budgets): money rounding, number parsing and ordered charges"
```

---

### Task 4: Mapeo de columnas y sugerencia por encabezados (`mapping.py`)

**Files:**
- Create: `server/visitacampo/budgets/mapping.py`
- Test: `server/visitacampo/budgets/tests/test_mapping.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_mapping.py`:

```python
"""Mapeo de columnas del Excel y su sugerencia por encabezados (puro)."""

from __future__ import annotations

import unittest

from visitacampo.budgets.mapping import (
    FIELDS,
    ColumnMapping,
    MappingError,
    fold,
    suggest_mapping,
)

COLUMNS: dict[str, str | None] = {
    "code": "A",
    "description": "B",
    "unit": "C",
    "quantity": "D",
    "unit_price": "E",
    "total": None,
}
VALID: dict[str, object] = {"sheet": "Presupuesto", "first_row": 3, "columns": COLUMNS}


def with_columns(**changes: str | None) -> dict[str, object]:
    return {**VALID, "columns": {**COLUMNS, **changes}}


class ColumnMappingTests(unittest.TestCase):
    def test_round_trip_and_indexes(self) -> None:
        mapping = ColumnMapping.from_json(VALID)
        self.assertEqual(mapping.to_json(), VALID)
        self.assertEqual(mapping.index("code"), 0)
        self.assertEqual(mapping.index("unit_price"), 4)
        self.assertIsNone(mapping.index("total"))

    def test_letters_are_normalised(self) -> None:
        mapping = ColumnMapping.from_json(with_columns(code=" a ", total="f"))
        self.assertEqual((mapping.columns["code"], mapping.columns["total"]), ("A", "F"))

    def test_rejects_invalid_mappings(self) -> None:
        bad = {
            "falta unidad": with_columns(unit=None),
            "letra inválida": with_columns(code="A1"),
            "tres letras": with_columns(code="AAA"),
            "más allá de BH": with_columns(total="BI"),
            "columna repetida": with_columns(description="A"),
            "fila 0": {**VALID, "first_row": 0},
            "fila booleana": {**VALID, "first_row": True},
            "sin hoja": {**VALID, "sheet": " "},
            "no es objeto": "hola",
        }
        for case, data in bad.items():
            with self.subTest(case=case), self.assertRaises(MappingError):
                ColumnMapping.from_json(data)


class SuggestMappingTests(unittest.TestCase):
    def test_finds_the_header_row_below_titles(self) -> None:
        rows = [
            ["PRESUPUESTO DE OBRA"],
            [],
            ["Ítem", "Descripción", "Und", "Cant.", "Vr. unitario", "Vr. total"],
            ["1", "Preliminares"],
        ]
        suggestion = suggest_mapping(rows)
        self.assertEqual((suggestion.header_row, suggestion.first_row), (3, 4))
        self.assertEqual(
            suggestion.columns,
            dict(zip(FIELDS, ["A", "B", "C", "D", "E", "F"], strict=True)),
        )

    def test_other_wordings_and_order(self) -> None:
        rows = [
            ["Descripción de la actividad", "Código", "Cantidad", "Unidad", "Valor Unitario ($)"],
        ]
        columns = suggest_mapping(rows).columns
        self.assertEqual(
            columns,
            {
                "code": "B",
                "description": "A",
                "unit": "D",
                "quantity": "C",
                "unit_price": "E",
                "total": None,
            },
        )

    def test_without_headers_suggests_nothing(self) -> None:
        suggestion = suggest_mapping([["1", "Excavación", "m3", 10, 5000]])
        self.assertIsNone(suggestion.header_row)
        self.assertEqual(suggestion.first_row, 1)
        self.assertEqual(set(suggestion.columns.values()), {None})

    def test_fold(self) -> None:
        self.assertEqual(fold("Descripción ÍTEM Nº"), "descripcion item no")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_mapping.py`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/budgets/mapping.py`:

```python
"""Mapeo de columnas del Excel de presupuesto y su sugerencia (puro).

El mapeo dice en qué hoja, desde qué fila y en qué columna (letra de Excel)
está cada dato. Se guarda por presupuesto (`Budget.column_mapping`) con la
forma de la spec::

    {"sheet": str, "first_row": int,
     "columns": {"code": "A", "description": "B", "unit": "C",
                 "quantity": "D", "unit_price": "E", "total": "F" | None}}

La sugerencia busca, en las primeras filas, la que más se parece a una fila de
encabezados ("Ítem", "Descripción", "Und", "Cant.", "Vr. unitario"…).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter

#: Columnas que se leen de cada fila (A–BH): acota el ancho de un archivo hostil.
MAX_COLUMNS = 60
MAX_FIRST_ROW = 20_000
FIELDS = ("code", "description", "unit", "quantity", "unit_price", "total")
REQUIRED_FIELDS = ("code", "description", "unit", "quantity", "unit_price")
FIELD_LABELS = {
    "code": "Código",
    "description": "Descripción",
    "unit": "Unidad",
    "quantity": "Cantidad",
    "unit_price": "Valor unitario",
    "total": "Valor total (opcional, para verificar)",
}

_LETTERS = re.compile(r"[A-Z]{1,2}")

#: Encabezados conocidos, ya normalizados con `normalize_header`.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "code": ("item", "items", "it", "cod", "codigo", "no", "n", "num", "numero", "id"),
    "description": (
        "descripcion", "actividad", "actividades", "concepto", "detalle", "nombre",
    ),
    "unit": ("und", "un", "unid", "unidad", "u", "ud", "uds", "um", "unidad de medida"),
    "quantity": ("cant", "cantidad", "cantidades", "cant total", "qty"),
    "unit_price": (
        "vr unitario", "valor unitario", "precio unitario", "v unitario", "vlr unitario",
        "vr unit", "v unit", "vlr unit", "p unitario", "pu", "costo unitario", "unitario",
        "precio",
    ),
    "total": (
        "vr total", "valor total", "precio total", "vlr total", "v total", "total",
        "valor parcial", "vr parcial", "parcial", "costo total", "importe",
    ),
}  # fmt: skip
_EXACT = {synonym: field for field, synonyms in _SYNONYMS.items() for synonym in synonyms}
#: Si no hay coincidencia exacta: una palabra que empiece así (en este orden).
_KEYWORDS = (
    ("unit_price", "unitari"),
    ("description", "descrip"),
    ("quantity", "cant"),
    ("unit", "unid"),
    ("code", "codig"),
    ("total", "total"),
)


class MappingError(ValueError):
    """El mapeo no es válido; el mensaje es para el usuario."""


def fold(value: object) -> str:
    """Texto en minúsculas y sin tildes, para comparar sin importar cómo se escribió."""
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def normalize_header(value: object) -> str:
    """`"Vr. Unitario ($)"` → `"vr unitario"`."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", fold(value)).split())


@dataclass(frozen=True)
class ColumnMapping:
    sheet: str
    first_row: int
    columns: Mapping[str, str | None]

    def index(self, field: str) -> int | None:
        """Posición (desde 0) de la columna de `field` en una fila; None si no se mapeó."""
        letter = self.columns.get(field)
        return column_index_from_string(letter) - 1 if letter else None

    def to_json(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "first_row": self.first_row,
            "columns": {field: self.columns.get(field) for field in FIELDS},
        }

    @classmethod
    def from_json(cls, data: object) -> ColumnMapping:
        if not isinstance(data, dict):
            raise MappingError("El mapeo de columnas no es válido.")
        sheet = data.get("sheet")
        if not isinstance(sheet, str) or not sheet.strip():
            raise MappingError("Elige la hoja del presupuesto.")
        first_row = data.get("first_row")
        if (
            isinstance(first_row, bool)
            or not isinstance(first_row, int)
            or not 1 <= first_row <= MAX_FIRST_ROW
        ):
            raise MappingError("La fila donde empiezan los datos debe estar entre 1 y 20.000.")
        raw = data.get("columns")
        if not isinstance(raw, dict):
            raise MappingError("El mapeo de columnas no es válido.")
        columns: dict[str, str | None] = {}
        for field in FIELDS:
            value = raw.get(field)
            letter = value.strip().upper() if isinstance(value, str) else value
            if not letter:
                if field in REQUIRED_FIELDS:
                    raise MappingError(f"Elige la columna de {FIELD_LABELS[field].lower()}.")
                columns[field] = None
                continue
            if (
                not isinstance(letter, str)
                or not _LETTERS.fullmatch(letter)
                or column_index_from_string(letter) > MAX_COLUMNS
            ):
                raise MappingError(f"{FIELD_LABELS[field]}: columna no válida.")
            columns[field] = letter
        used = [letter for letter in columns.values() if letter]
        if len(used) != len(set(used)):
            raise MappingError("Una misma columna no puede tener dos significados.")
        return cls(sheet=sheet, first_row=first_row, columns=columns)


@dataclass(frozen=True)
class Suggestion:
    #: Fila de Excel (desde 1) con los encabezados; None si no se encontró.
    header_row: int | None
    first_row: int
    columns: dict[str, str | None]


def header_field(value: object) -> str | None:
    """Qué dato anuncia un encabezado, o None."""
    if value is None:
        return None
    text = normalize_header(value)
    if not text:
        return None
    if text in _EXACT:
        return _EXACT[text]
    words = text.split()
    for field, keyword in _KEYWORDS:
        if any(word.startswith(keyword) for word in words):
            return field
    return None


def suggest_mapping(rows: Sequence[Sequence[object]]) -> Suggestion:
    """La fila con más encabezados reconocidos (mínimo 3) y sus columnas."""
    best: tuple[int, int, dict[str, str]] | None = None
    for number, row in enumerate(rows, start=1):
        columns: dict[str, str] = {}
        for index, value in enumerate(row[:MAX_COLUMNS]):
            field = header_field(value)
            if field is not None and field not in columns:
                columns[field] = get_column_letter(index + 1)
        if len(columns) >= 3 and (best is None or len(columns) > best[0]):
            best = (len(columns), number, columns)
    if best is None:
        return Suggestion(header_row=None, first_row=1, columns=dict.fromkeys(FIELDS))
    _, number, found = best
    return Suggestion(
        header_row=number,
        first_row=number + 1,
        columns={field: found.get(field) for field in FIELDS},
    )
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_mapping.py`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/budgets/mapping.py server/visitacampo/budgets/tests/test_mapping.py
git commit -m "feat(budgets): column mapping and header-based suggestion"
```

---

### Task 5: Lectura segura del `.xlsx` (`excel.py`, primera parte)

**Files:**
- Create: `server/visitacampo/budgets/excel.py`
- Create: `server/visitacampo/budgets/tests/xlsx.py`
- Test: `server/visitacampo/budgets/tests/test_excel_read.py`

- [ ] **Step 1: Ayudante de pruebas para construir libros en memoria**

`server/visitacampo/budgets/tests/xlsx.py`:

```python
"""Libros `.xlsx` de prueba, construidos en memoria con openpyxl."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from openpyxl import Workbook

HEADER = ["Ítem", "Descripción", "Und", "Cant.", "Vr. unitario", "Vr. total"]


def build_xlsx(
    rows: Iterable[Sequence[Any]],
    *,
    header: Sequence[Any] | None = HEADER,
    sheet: str = "Presupuesto",
    title_rows: Sequence[Sequence[Any]] = (),
    merges: Sequence[str] = (),
    extra_sheets: Sequence[str] = (),
) -> bytes:
    """Una hoja con `title_rows`, el encabezado y `rows`, en ese orden desde la fila 1."""
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet
    for row in title_rows:
        worksheet.append(list(row))
    if header is not None:
        worksheet.append(list(header))
    for row in rows:
        worksheet.append(list(row))
    for cell_range in merges:
        worksheet.merge_cells(cell_range)
    for name in extra_sheets:
        workbook.create_sheet(name)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def rewrite_entry(data: bytes, name: str, change: Callable[[str], str]) -> bytes:
    """El mismo ZIP con la entrada `name` transformada por `change`."""
    buffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == name:
                content = change(content.decode()).encode()
            target.writestr(info.filename, content)
    return buffer.getvalue()


def with_cached_formula(
    data: bytes, cell: str, formula: str, value: str, *, sheet_xml: str = "xl/worksheets/sheet1.xml"
) -> bytes:
    """Pone en `cell` una fórmula **con su valor calculado**, como la guarda Excel.

    openpyxl escribe fórmulas sin valor calculado; Excel y LibreOffice guardan
    los dos. Se reescribe el XML de la celda para imitarlos.
    """
    pattern = re.compile(rf'<c r="{cell}"[^>]*?(?:/>|>.*?</c>)', re.S)
    replacement = f'<c r="{cell}"><f>{formula}</f><v>{value}</v></c>'

    def change(xml: str) -> str:
        new, count = pattern.subn(replacement, xml, count=1)
        assert count == 1, f"la celda {cell} no está en la hoja"
        return new

    return rewrite_entry(data, sheet_xml, change)


def with_extra_entry(data: bytes, name: str, content: bytes = b"") -> bytes:
    """El mismo ZIP con una entrada más (p. ej. `xl/vbaProject.bin`)."""
    buffer = io.BytesIO(data)
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()
```

Nota: en las pruebas, la celda que se pasa a `with_cached_formula` tiene que existir en el libro (escribir antes cualquier valor en ella con `build_xlsx`).

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_excel_read.py`:

```python
"""Lectura segura del `.xlsx` de presupuesto: archivo, hojas, límites (puro)."""

from __future__ import annotations

import unittest
from unittest import mock

from visitacampo.budgets import excel
from visitacampo.budgets.excel import (
    MAX_BYTES,
    MAX_ROWS,
    ExcelRejected,
    check_file_name,
    preview_sheet,
    read_rows,
)
from visitacampo.budgets.mapping import MAX_COLUMNS

from .xlsx import HEADER, build_xlsx, with_cached_formula, with_extra_entry

ROWS = [["1", "Preliminares"], ["1.1", "Localización", "m2", 100, 1500, 150000]]


class FileNameTests(unittest.TestCase):
    def test_only_xlsx(self) -> None:
        check_file_name("Presupuesto Obra.XLSX")
        for name, words in (
            ("p.xlsm", "macros"),
            ("p.xls", "97-2003"),
            ("p.csv", "Solo se admiten"),
        ):
            with self.subTest(name=name), self.assertRaises(ExcelRejected) as caught:
                check_file_name(name)
            self.assertIn(words, str(caught.exception))

    def test_limits_are_the_ones_in_the_spec(self) -> None:
        self.assertEqual(MAX_BYTES, 20 * 1024 * 1024)
        self.assertEqual(MAX_ROWS, 20_000)


class ArchiveTests(unittest.TestCase):
    def test_not_a_zip(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(b"hola", "Presupuesto")
        self.assertIn("no es un archivo .xlsx válido", str(caught.exception))

    def test_macros_are_rejected_even_with_xlsx_name(self) -> None:
        data = with_extra_entry(build_xlsx(ROWS), "xl/vbaProject.bin", b"\x00")
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(data, "Presupuesto")
        self.assertIn("macros", str(caught.exception))

    def test_size_limit(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(build_xlsx(ROWS), "Presupuesto", max_bytes=100)
        self.assertIn("supera", str(caught.exception))

    def test_expanded_size_limit(self) -> None:
        with mock.patch.object(excel, "MAX_EXPANDED_BYTES", 100):
            with self.assertRaises(ExcelRejected) as caught:
                read_rows(build_xlsx(ROWS), "Presupuesto")
        self.assertIn("demasiado grande", str(caught.exception))


class PreviewTests(unittest.TestCase):
    def test_first_rows_of_the_chosen_sheet(self) -> None:
        data = build_xlsx(
            [[str(i), f"Ítem {i}", "u", 1, 1] for i in range(40)], extra_sheets=["APU"]
        )
        preview = preview_sheet(data)
        self.assertEqual(preview.sheet_names, ("Presupuesto", "APU"))
        self.assertEqual(preview.sheet, "Presupuesto")
        self.assertEqual(len(preview.rows), 30)
        self.assertEqual(list(preview.rows[0][:6]), HEADER)
        self.assertEqual(preview_sheet(data, "APU").sheet, "APU")
        self.assertEqual(preview_sheet(data, "No existe").sheet, "Presupuesto")


class ReadRowsTests(unittest.TestCase):
    def test_rows_are_padded_to_the_column_limit(self) -> None:
        rows = read_rows(build_xlsx(ROWS), "Presupuesto")
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), MAX_COLUMNS)
        self.assertEqual(rows[2][:6], ("1.1", "Localización", "m2", 100, 1500, 150000))

    def test_missing_sheet(self) -> None:
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(build_xlsx(ROWS), "Hoja1")
        self.assertIn("no tiene la hoja «Hoja1»", str(caught.exception))

    def test_data_beyond_the_row_limit_is_rejected(self) -> None:
        data = build_xlsx([[str(i), "x", "u", 1, 1] for i in range(8)])
        with self.assertRaises(ExcelRejected) as caught:
            read_rows(data, "Presupuesto", max_rows=5)
        self.assertIn("después de la fila 5", str(caught.exception))

    def test_merged_cells_keep_the_value_in_the_first_cell(self) -> None:
        data = build_xlsx(
            [["1", "Preliminares y obras provisionales"]],
            title_rows=[["PRESUPUESTO"]],
            merges=["A1:F1", "B3:D3"],
        )
        rows = read_rows(data, "Presupuesto")
        self.assertEqual(rows[0][0], "PRESUPUESTO")
        self.assertEqual(rows[2][1], "Preliminares y obras provisionales")
        self.assertIsNone(rows[2][2])

    def test_formulas_are_read_as_their_calculated_value(self) -> None:
        data = with_cached_formula(build_xlsx(ROWS), "F3", "D3*E3", "150000")
        self.assertEqual(read_rows(data, "Presupuesto")[2][5], 150000)
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_excel_read.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.budgets.excel'`.

- [ ] **Step 4: Escribir la primera parte del módulo**

`server/visitacampo/budgets/excel.py`:

```python
"""Lectura del Excel de presupuesto (pura: sin BD).

Primera parte — el archivo:

- Solo `.xlsx` (`.xlsm` y `.xls` se rechazan por nombre, con su motivo) y un
  `vbaProject.bin` dentro (macros) se rechaza aunque el archivo se llame `.xlsx`.
- Límites antes de que openpyxl mire nada: 20 MB, ZIP de máximo 1.000 entradas
  y 200 MB descomprimidos. openpyxl parsea el XML con `defusedxml` (instalado).
- `read_only=True, data_only=True`: se lee en streaming y el valor **calculado**
  de las fórmulas (el que guardó Excel). En `read_only` las celdas combinadas
  no se exponen: el valor está en la celda superior izquierda y el resto vacío.
- Se ignora la dimensión que declara la hoja (`reset_dimensions`), que puede
  anunciar un millón de filas vacías, y cada fila se lee hasta la columna 60.
- Máximo 20.000 filas: si hay datos más abajo se rechaza, no se trunca.

Segunda parte — interpretación de filas: ver más abajo (`interpret_rows`).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .mapping import MAX_COLUMNS

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 1_000
PREVIEW_ROWS = 30

_NOT_XLSX = "El archivo no es un archivo .xlsx válido."


class ExcelRejected(Exception):
    """El archivo no se puede usar; el mensaje es para el usuario."""


def check_file_name(name: str) -> None:
    lower = name.lower()
    if lower.endswith(".xlsm"):
        raise ExcelRejected(
            "Los libros con macros (.xlsm) no se admiten: guárdalo como .xlsx."
        )
    if lower.endswith(".xls"):
        raise ExcelRejected(
            "El formato .xls (Excel 97-2003) no se admite: guárdalo como .xlsx."
        )
    if not lower.endswith(".xlsx"):
        raise ExcelRejected("Solo se admiten archivos .xlsx.")


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _check_archive(data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise ExcelRejected(f"El archivo supera {max_bytes // (1024 * 1024)} MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ExcelRejected(_NOT_XLSX) from exc
    if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
        raise ExcelRejected("El libro es demasiado grande una vez descomprimido.")
    if any("vbaProject" in entry.filename for entry in entries):
        raise ExcelRejected("El libro contiene macros: guárdalo como .xlsx sin macros.")


def _open(data: bytes, max_bytes: int) -> Any:
    _check_archive(data, max_bytes)
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except (InvalidFileException, KeyError, ValueError, TypeError, OSError) as exc:
        raise ExcelRejected(_NOT_XLSX) from exc


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


@dataclass(frozen=True)
class SheetPreview:
    sheet_names: tuple[str, ...]
    sheet: str
    rows: tuple[tuple[Any, ...], ...]


def preview_sheet(
    data: bytes,
    sheet: str | None = None,
    *,
    rows: int = PREVIEW_ROWS,
    max_bytes: int = MAX_BYTES,
) -> SheetPreview:
    """Las primeras `rows` filas de `sheet` (o de la primera hoja si no existe)."""
    workbook = _open(data, max_bytes)
    try:
        names = tuple(workbook.sheetnames)
        if not names:
            raise ExcelRejected("El libro no tiene hojas.")
        name = sheet if sheet in names else names[0]
        worksheet = workbook[name]
        worksheet.reset_dimensions()
        values = tuple(
            tuple(row)
            for row in worksheet.iter_rows(
                min_row=1, max_row=rows, max_col=MAX_COLUMNS, values_only=True
            )
        )
        return SheetPreview(sheet_names=names, sheet=name, rows=values)
    finally:
        workbook.close()


def read_rows(
    data: bytes,
    sheet: str,
    *,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
) -> list[tuple[Any, ...]]:
    """Todas las filas de `sheet` (la 1 de Excel es la posición 0), de 60 columnas."""
    workbook = _open(data, max_bytes)
    try:
        if sheet not in workbook.sheetnames:
            raise ExcelRejected(
                f"El libro no tiene la hoja «{sheet}». Cambia el mapeo de columnas."
            )
        worksheet = workbook[sheet]
        worksheet.reset_dimensions()
        rows: list[tuple[Any, ...]] = []
        for number, row in enumerate(
            worksheet.iter_rows(min_row=1, max_col=MAX_COLUMNS, values_only=True), start=1
        ):
            if number <= max_rows:
                rows.append(tuple(row))
            elif any(_present(value) for value in row):
                raise ExcelRejected(
                    f"La hoja tiene datos después de la fila {_thousands(max_rows)}: "
                    f"el máximo es {_thousands(max_rows)} filas."
                )
        return rows
    finally:
        workbook.close()
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_excel_read.py`
Expected: todo pasa. Si `test_formulas_are_read_as_their_calculated_value` falla en el ayudante (`la celda F3 no está en la hoja`), imprime `xl/worksheets/sheet1.xml` del libro de prueba y ajusta el patrón de `with_cached_formula` a cómo escribe la celda la versión instalada de openpyxl.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/budgets/excel.py server/visitacampo/budgets/tests/xlsx.py server/visitacampo/budgets/tests/test_excel_read.py
git commit -m "feat(budgets): safe xlsx reading with size, row and macro limits"
```

---
### Task 6: Interpretación de filas (`excel.py`, segunda parte) y presupuestos de ejemplo

**Files:**
- Modify: `server/visitacampo/budgets/excel.py`
- Create: `server/visitacampo/budgets/tests/samples.py`
- Test: `server/visitacampo/budgets/tests/test_excel_rows.py`

- [ ] **Step 1: Presupuestos de ejemplo (los usan todas las pruebas siguientes)**

`server/visitacampo/budgets/tests/samples.py`:

```python
"""Presupuestos de ejemplo para las pruebas (sin BD).

Totales, para no recalcularlos en cada prueba:

- v1: costo directo 9.130.000,00; capítulo 1 = 1.150.000,00; capítulo 2 = 7.980.000,00.
  Con `AIU` (10 / 5 / 5 / IVA 19 % sobre utilidad): total 11.042.735,00.
- v2 frente a v1: 1.1 mayor cantidad (100 → 120), 1.2 eliminado, 2.1 cambio de
  precio (450.000 → 470.000), 2.2 menor cantidad (800 → 700), 2.3 no previsto,
  2.4 sin cambios. Costo directo 8.690.000,00; con los mismos cargos 10.510.555,00.
- v3: la v2 con el 1.2 de vuelta.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from visitacampo.budgets.mapping import ColumnMapping
from visitacampo.budgets.totals import BASE_DIRECT, ChargeSpec, line_base

from .xlsx import HEADER, build_xlsx

SHEET = "Presupuesto"
MAPPING = ColumnMapping(
    sheet=SHEET,
    first_row=2,
    columns={
        "code": "A",
        "description": "B",
        "unit": "C",
        "quantity": "D",
        "unit_price": "E",
        "total": "F",
    },
)

V1_ROWS: list[list[Any]] = [
    ["1", "Preliminares"],
    ["1.1", "Localización y replanteo", "m2", 100, 1500, 150000],
    ["1.2", "Cerramiento provisional", "ml", 50, 20000, 1000000],
    ["2", "Estructura"],
    ["2.1", "Concreto zapatas", "m3", 10, 450000, 4500000],
    ["2.2", "Acero de refuerzo", "kg", 800, 4200, 3360000],
    ["2.4", "Curado", "m2", 40, 3000, 120000],
]
V2_ROWS: list[list[Any]] = [
    ["1", "Preliminares"],
    ["1.1", "Localización y replanteo", "m2", 120, 1500, 180000],
    ["2", "Estructura"],
    ["2.1", "Concreto zapatas", "m3", 10, 470000, 4700000],
    ["2.2", "Acero de refuerzo", "kg", 700, 4200, 2940000],
    ["2.3", "Formaleta", "m2", 30, 25000, 750000],
    ["2.4", "Curado", "m2", 40, 3000, 120000],
]
V3_ROWS: list[list[Any]] = [*V2_ROWS[:2], V1_ROWS[2], *V2_ROWS[2:]]

AIU = [
    ChargeSpec(1, "Administración", Decimal("10"), BASE_DIRECT),
    ChargeSpec(2, "Imprevistos", Decimal("5"), BASE_DIRECT),
    ChargeSpec(3, "Utilidad", Decimal("5"), BASE_DIRECT),
    ChargeSpec(4, "IVA sobre utilidad", Decimal("19"), line_base(3)),
]


def xlsx(rows: list[list[Any]]) -> bytes:
    """El Excel de un presupuesto de ejemplo, con el encabezado en la fila 1."""
    return build_xlsx(rows, sheet=SHEET)


def sheet_rows(rows: list[list[Any]]) -> list[list[Any]]:
    """Las mismas filas, como las devuelve `excel.read_rows` (encabezado incluido)."""
    return [HEADER, *rows]
```

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_excel_rows.py`:

```python
"""Interpretación de las filas del Excel de presupuesto (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from visitacampo.budgets.excel import (
    KIND_CHAPTER,
    KIND_ITEM,
    ExcelRejected,
    ParsedBudget,
    interpret_rows,
    parse_budget,
)
from visitacampo.budgets.mapping import ColumnMapping

from .samples import MAPPING, V1_ROWS, sheet_rows
from .xlsx import build_xlsx, with_cached_formula

D = Decimal


def interpret(rows: list[list[Any]]) -> ParsedBudget:
    return interpret_rows(sheet_rows(rows), MAPPING)


class KindsAndHierarchyTests(unittest.TestCase):
    def test_chapters_by_code_levels_items_and_totals(self) -> None:
        parsed = interpret(
            [
                ["1", "Preliminares"],
                ["1.1", "Localización", "m2", 100, 1500, 150000],
                ["1.2", "Cerramientos"],
                ["1.2.1", "Cerramiento en malla", "ml", 50, "20.000,50", None],
                ["2", "Estructura"],
                ["2.1", "Concreto", "m3", 2.5, 0.33, None],
            ]
        )
        by_code = {row.code: row for row in parsed.rows}
        self.assertEqual(
            [(r.code, r.kind, r.parent_code, r.sort_order) for r in parsed.rows],
            [
                ("1", KIND_CHAPTER, None, 0),
                ("1.1", KIND_ITEM, "1", 1),
                ("1.2", KIND_CHAPTER, "1", 2),
                ("1.2.1", KIND_ITEM, "1.2", 3),
                ("2", KIND_CHAPTER, None, 4),
                ("2.1", KIND_ITEM, "2", 5),
            ],
        )
        self.assertEqual(by_code["1.2.1"].unit_price, D("20000.50"))
        self.assertEqual(by_code["1.2.1"].total, D("1000025.00"))
        self.assertEqual(by_code["1.2"].total, D("1000025.00"))
        self.assertEqual(by_code["1"].total, D("1150025.00"))
        self.assertEqual(by_code["2.1"].total, D("0.83"))  # 2,5 × 0,33 = 0,825
        self.assertEqual(by_code["2.1"].quantity, D("2.5000"))
        self.assertIsNone(by_code["1"].quantity)
        self.assertEqual(parsed.direct_cost, D("1150025.83"))
        self.assertEqual(by_code["1.1"].row, 3)

    def test_non_hierarchical_codes_hang_from_the_last_chapter_above(self) -> None:
        parsed = interpret(
            [
                ["A", "Obras preliminares"],
                ["101", "Localización", "m2", 10, 100],
                ["102", "Descapote", "m2", 10, 100],
                ["B", "Estructura"],
                ["201", "Concreto", "m3", 1, 100],
            ]
        )
        self.assertEqual(
            {r.code: r.parent_code for r in parsed.rows},
            {"A": None, "101": "A", "102": "A", "B": None, "201": "B"},
        )

    def test_dotted_item_without_its_chapter_uses_the_last_chapter_above(self) -> None:
        parsed = interpret([["CAP-1", "Preliminares"], ["1.1", "Localización", "u", 1, 1]])
        self.assertEqual(parsed.rows[1].parent_code, "CAP-1")

    def test_codes_written_as_numbers_and_trailing_dots(self) -> None:
        parsed = interpret([[1, "Preliminares"], [1.1, "Localización", "u", 1, 1], ["2.", "B"]])
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1", "2"])
        self.assertEqual(parsed.rows[1].parent_code, "1")


class IgnoredRowsTests(unittest.TestCase):
    def test_rows_without_code_and_subtotals_are_ignored_and_counted(self) -> None:
        parsed = interpret(
            [
                ["1", "Preliminares"],
                ["1.1", "Localización", "m2", 100, 1500, 150000],
                [None, "Subtotal preliminares", None, None, None, 150000],
                [],
                ["", "Nota: precios con AIU incluido"],
                ["3", "SUBTOTAL CAPÍTULO 3", None, None, None, 5],
                ["TOTAL", None, None, None, None, 150000],
                ["9"],
            ]
        )
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1"])
        self.assertEqual(parsed.ignored_rows, 5)  # la fila vacía no cuenta
        self.assertEqual(parsed.ignored_examples, (4, 6, 7, 8, 9))
        self.assertIn("5 fila(s) ignorada(s)", parsed.warnings[0])


class RejectedFilesTests(unittest.TestCase):
    def rejected(self, rows: list[list[Any]]) -> str:
        with self.assertRaises(ExcelRejected) as caught:
            interpret(rows)
        return str(caught.exception)

    def test_repeated_code_is_named(self) -> None:
        message = self.rejected(
            [["1.1", "A", "u", 1, 1], ["1.2", "B", "u", 1, 1], ["1.1", "C", "u", 1, 1]]
        )
        self.assertIn("El código 1.1 está repetido (filas 2 y 4)", message)

    def test_non_numeric_quantity_names_the_row(self) -> None:
        message = self.rejected([["1.1", "A", "u", "diez", 1]])
        self.assertIn("Fila 2 (código 1.1): la cantidad «diez» no es un número", message)

    def test_negative_price_names_the_row(self) -> None:
        message = self.rejected([["1.1", "A", "u", 1, -5]])
        self.assertIn("Fila 2 (código 1.1): el valor unitario tiene un valor negativo", message)

    def test_quantity_without_price(self) -> None:
        message = self.rejected([["1.1", "A", "u", 3, None]])
        self.assertIn("le falta el valor unitario", message)

    def test_code_too_long(self) -> None:
        self.assertIn("más de 50 caracteres", self.rejected([["X" * 51, "A", "u", 1, 1]]))

    def test_value_too_large(self) -> None:
        self.assertIn("demasiado grande", self.rejected([["1.1", "A", "u", 1, 10**17]]))

    def test_no_items(self) -> None:
        self.assertIn("No se encontró ningún ítem", self.rejected([["1", "Preliminares"]]))


class WarningsTests(unittest.TestCase):
    def test_excel_total_that_does_not_match_is_warned(self) -> None:
        parsed = interpret([["1.1", "A", "u", 10, 15, 151], ["1.2", "B", "u", 2, 3, 6]])
        self.assertEqual(parsed.total_mismatches, 1)
        self.assertIn("1.1 (fila 2: 151.00 en el Excel, 150.00 calculado)", parsed.warnings[0])
        self.assertEqual(parsed.rows[0].total, D("150.00"))  # manda el calculado

    def test_without_total_column_nothing_is_compared(self) -> None:
        no_total = ColumnMapping(
            sheet=MAPPING.sheet, first_row=2, columns={**MAPPING.columns, "total": None}
        )
        parsed = interpret_rows(sheet_rows([["1.1", "A", "u", 10, 15, 151]]), no_total)
        self.assertEqual(parsed.total_mismatches, 0)


class ParseBudgetTests(unittest.TestCase):
    def test_real_file_with_titles_merged_cells_and_formulas(self) -> None:
        data = build_xlsx(
            [
                ["1", "Preliminares y obras provisionales"],
                ["1.1", "Localización y replanteo", "m2", 100, 1, 1],
            ],
            title_rows=[["PRESUPUESTO DE OBRA"]],
            merges=["A1:F1", "B3:D3"],
        )
        data = with_cached_formula(data, "E4", "1000+500", "1500")
        data = with_cached_formula(data, "F4", "D4*E4", "150000")
        mapping = ColumnMapping(sheet="Presupuesto", first_row=3, columns=MAPPING.columns)
        parsed = parse_budget(data, mapping)
        self.assertEqual([r.code for r in parsed.rows], ["1", "1.1"])
        self.assertEqual(parsed.rows[0].description, "Preliminares y obras provisionales")
        self.assertEqual(parsed.rows[1].unit_price, D("1500.00"))
        self.assertEqual(parsed.rows[1].total, D("150000.00"))
        self.assertEqual(parsed.total_mismatches, 0)

    def test_sample_v1(self) -> None:
        parsed = interpret(V1_ROWS)
        self.assertEqual(parsed.direct_cost, D("9130000.00"))
        totals = {r.code: r.total for r in parsed.rows if r.kind == KIND_CHAPTER}
        self.assertEqual(totals, {"1": D("1150000.00"), "2": D("7980000.00")})
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_excel_rows.py`
Expected: FAIL con `ImportError: cannot import name 'KIND_CHAPTER'`.

- [ ] **Step 4: Escribir la segunda parte del módulo**

En `server/visitacampo/budgets/excel.py`, reemplazar el bloque de imports por:

```python
import io
import re
import zipfile
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .mapping import FIELDS, MAX_COLUMNS, ColumnMapping, fold
from .totals import ZERO, item_total, money, parse_number, quantity
```

tras `PREVIEW_ROWS = 30` añadir:

```python
MAX_CODE_LENGTH = 50
MAX_UNIT_LENGTH = 30
MAX_DESCRIPTION_LENGTH = 2_000
MAX_EXAMPLES = 20
#: Topes de `numeric(18,4)` (cantidad) y `numeric(18,2)` (dinero).
MAX_QUANTITY = Decimal("1e14")
MAX_MONEY = Decimal("1e16")

#: Tipos de fila; los mismos valores que `core.models.BudgetRowKind`.
KIND_CHAPTER = "chapter"
KIND_ITEM = "item"

#: "Subtotal", "Sub-total", "TOTAL COSTO DIRECTO", "Totales"…, ya sin tildes ni mayúsculas.
_TOTAL_LABEL = re.compile(r"(sub\s*-?\s*)?totale?s?\b")
```

y en el docstring del módulo reemplazar la última línea (la que empieza por "Segunda parte — interpretación de filas: ver más abajo") por:

```
Segunda parte — interpretación de filas (§2 de la spec):

- Código, cantidad y valor unitario → ítem. Código y descripción sin cantidad
  ni valor unitario → capítulo. Sin código, o con texto tipo "Subtotal" /
  "Total" en el código o la descripción → se ignora y se cuenta.
- Código repetido, cantidad o valor unitario no numéricos o negativos, o solo
  uno de los dos → se rechaza nombrando la fila.
- Padre: el capítulo prefijo más largo ("1.2" para "1.2.3"); si no hay y la
  fila es un ítem, el último capítulo encima de él.
- Cantidad a 4 decimales y valor unitario a centavos; el total del ítem es
  cantidad × valor unitario a centavos (el del Excel solo se compara y avisa);
  el del capítulo, la suma de sus hijos.
```

Al final del archivo añadir:

```python
@dataclass(frozen=True)
class ParsedRow:
    #: Fila de Excel (desde 1).
    row: int
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    parent_code: str | None
    #: Posición entre las filas aceptadas (0, 1, 2…).
    sort_order: int


@dataclass(frozen=True)
class ParsedBudget:
    rows: tuple[ParsedRow, ...]
    direct_cost: Decimal
    ignored_rows: int
    ignored_examples: tuple[int, ...]
    total_mismatches: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Found:
    row: int
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    excel_total: Decimal | None


def _cell(row: Sequence[Any], index: int | None) -> Any:
    return row[index] if index is not None and index < len(row) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _code(value: Any) -> str:
    return _text(value).rstrip(".").strip()


def _is_total_label(text: str) -> bool:
    return bool(text) and _TOTAL_LABEL.match(fold(text).strip()) is not None


def _number(value: Any, number: int, code: str, label: str, limit: Decimal) -> Decimal | None:
    try:
        parsed = parse_number(value)
    except ValueError:
        raise ExcelRejected(
            f"Fila {number} (código {code}): {label} «{_text(value)[:40]}» no es un número."
        ) from None
    if parsed is None:
        return None
    if parsed < 0:
        raise ExcelRejected(
            f"Fila {number} (código {code}): {label} tiene un valor negativo ({_text(value)})."
        )
    if parsed >= limit:
        raise ExcelRejected(f"Fila {number} (código {code}): {label} es demasiado grande.")
    return parsed


def _optional_number(value: Any) -> Decimal | None:
    try:
        return parse_number(value)
    except ValueError:
        return None  # el total del Excel solo se usa para avisar


def _prefix_parent(code: str, chapters: Collection[str]) -> str | None:
    parts = code.split(".")
    for size in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in chapters:
            return candidate
    return None


def _parents(found: Sequence[_Found]) -> dict[str, str | None]:
    chapters = {f.code for f in found if f.kind == KIND_CHAPTER}
    parents: dict[str, str | None] = {}
    last_chapter: str | None = None
    for f in found:
        parent = _prefix_parent(f.code, chapters)
        if parent is None and f.kind == KIND_ITEM:
            parent = last_chapter
        parents[f.code] = parent
        if f.kind == KIND_CHAPTER:
            last_chapter = f.code
    return parents


def _totals(found: Sequence[_Found], parents: dict[str, str | None]) -> dict[str, Decimal]:
    """Total de cada fila. Los padres de un capítulo son prefijos estrictos de su
    código, así que subir por la cadena siempre termina."""
    totals = {f.code: ZERO for f in found}
    for f in found:
        if f.kind != KIND_ITEM or f.quantity is None or f.unit_price is None:
            continue
        value = item_total(f.quantity, f.unit_price)
        if value >= MAX_MONEY:
            raise ExcelRejected(f"Fila {f.row} (código {f.code}): el total es demasiado grande.")
        totals[f.code] = value
        parent = parents[f.code]
        while parent is not None:
            totals[parent] += value
            parent = parents[parent]
    return totals


def _examples(values: Sequence[str]) -> str:
    text = ", ".join(values[:MAX_EXAMPLES])
    return text + ("…" if len(values) > MAX_EXAMPLES else "")


def interpret_rows(rows: Sequence[Sequence[Any]], mapping: ColumnMapping) -> ParsedBudget:
    """Filas de la hoja (la 1 de Excel en `rows[0]`) → capítulos e ítems."""
    col = {field: mapping.index(field) for field in FIELDS}
    found: list[_Found] = []
    first_seen: dict[str, int] = {}
    ignored: list[int] = []
    for number in range(mapping.first_row, len(rows) + 1):
        row = rows[number - 1]
        if not any(_present(value) for value in row):
            continue
        code = _code(_cell(row, col["code"]))
        description = _text(_cell(row, col["description"]))
        if not code or _is_total_label(code) or _is_total_label(description):
            ignored.append(number)
            continue
        if len(code) > MAX_CODE_LENGTH:
            raise ExcelRejected(
                f"Fila {number}: el código «{code[:20]}…» tiene más de "
                f"{MAX_CODE_LENGTH} caracteres."
            )
        qty = _number(_cell(row, col["quantity"]), number, code, "la cantidad", MAX_QUANTITY)
        price = _number(
            _cell(row, col["unit_price"]), number, code, "el valor unitario", MAX_MONEY
        )
        if qty is None and price is None:
            if not description:
                ignored.append(number)
                continue
            kind = KIND_CHAPTER
        elif qty is None or price is None:
            has, missing = ("valor unitario", "la cantidad")
            if price is None:
                has, missing = ("cantidad", "el valor unitario")
            raise ExcelRejected(
                f"Fila {number} (código {code}): tiene {has} pero le falta {missing}."
            )
        else:
            kind = KIND_ITEM
        if code in first_seen:
            raise ExcelRejected(
                f"El código {code} está repetido (filas {first_seen[code]} y {number})."
            )
        first_seen[code] = number
        found.append(
            _Found(
                row=number,
                code=code,
                kind=kind,
                description=description[:MAX_DESCRIPTION_LENGTH],
                unit=_text(_cell(row, col["unit"]))[:MAX_UNIT_LENGTH],
                quantity=quantity(qty) if qty is not None else None,
                unit_price=money(price) if price is not None else None,
                excel_total=(
                    _optional_number(_cell(row, col["total"])) if kind == KIND_ITEM else None
                ),
            )
        )
    if not any(f.kind == KIND_ITEM for f in found):
        raise ExcelRejected(
            f"No se encontró ningún ítem (código, cantidad y valor unitario) desde la fila "
            f"{mapping.first_row}. Revisa el mapeo de columnas."
        )

    parents = _parents(found)
    totals = _totals(found, parents)
    direct_cost = sum((totals[f.code] for f in found if f.kind == KIND_ITEM), ZERO)
    if direct_cost >= MAX_MONEY:
        raise ExcelRejected("El costo directo del presupuesto es demasiado grande.")
    mismatches = [
        f
        for f in found
        if f.excel_total is not None and money(f.excel_total) != totals[f.code]
    ]
    warnings: list[str] = []
    if ignored:
        warnings.append(
            f"{len(ignored)} fila(s) ignorada(s) por no tener código o ser de subtotal/total: "
            f"{_examples([str(n) for n in ignored])}."
        )
    if mismatches:
        warnings.append(
            f"{len(mismatches)} ítem(s) cuyo total del Excel no coincide con cantidad × valor "
            "unitario (se usa el calculado): "
            + _examples(
                [
                    f"{f.code} (fila {f.row}: {money(f.excel_total or ZERO)} en el Excel, "
                    f"{totals[f.code]} calculado)"
                    for f in mismatches
                ]
            )
            + "."
        )
    return ParsedBudget(
        rows=tuple(
            ParsedRow(
                row=f.row,
                code=f.code,
                kind=f.kind,
                description=f.description,
                unit=f.unit,
                quantity=f.quantity,
                unit_price=f.unit_price,
                total=totals[f.code],
                parent_code=parents[f.code],
                sort_order=index,
            )
            for index, f in enumerate(found)
        ),
        direct_cost=direct_cost,
        ignored_rows=len(ignored),
        ignored_examples=tuple(ignored[:MAX_EXAMPLES]),
        total_mismatches=len(mismatches),
        warnings=tuple(warnings),
    )


def parse_budget(
    data: bytes,
    mapping: ColumnMapping,
    *,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
) -> ParsedBudget:
    """Lee el `.xlsx` con `mapping`. Lanza `ExcelRejected` con un mensaje para el usuario."""
    return interpret_rows(
        read_rows(data, mapping.sheet, max_rows=max_rows, max_bytes=max_bytes), mapping
    )
```

Añade también a `server/visitacampo/budgets/tests/test_models.py` (que ya importa `BudgetRowKind`) esta prueba sin BD, en una clase propia al final del archivo, para que los valores de `excel` y del modelo no se separen:

```python
class RowKindValuesTests(unittest.TestCase):
    def test_reader_and_model_use_the_same_values(self) -> None:
        from visitacampo.budgets.excel import KIND_CHAPTER, KIND_ITEM

        self.assertEqual((KIND_CHAPTER, KIND_ITEM), (BudgetRowKind.CHAPTER, BudgetRowKind.ITEM))
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_excel_rows.py visitacampo/budgets/tests/test_excel_read.py visitacampo/budgets/tests/test_models.py`
Expected: todo pasa. Comprobaciones a mano de los casos:
- ignoradas: filas 4 (sin código, "Subtotal"), 6 (código vacío), 7 (código "3" con "SUBTOTAL CAPÍTULO 3"), 8 ("TOTAL"), 9 (código "9" sin descripción ni números); la 5 está vacía.
- `"20.000,50"` → 20.000,50; 50 × 20.000,50 = 1.000.025,00.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/budgets/excel.py server/visitacampo/budgets/tests
git commit -m "feat(budgets): interpret budget rows into chapters and items"
```

---

### Task 7: Etiquetas y resumen de cambios (`diff.py`)

**Files:**
- Create: `server/visitacampo/budgets/diff.py`
- Test: `server/visitacampo/budgets/tests/test_diff.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_diff.py`:

```python
"""Etiquetas de comparación y resumen de cambios de una versión (puro)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from visitacampo.budgets.diff import (
    LABEL_LESS,
    LABEL_MORE,
    LABEL_NEW,
    LABEL_PRICE,
    LABEL_REMOVED,
    Values,
    row_labels,
    summarize_changes,
)
from visitacampo.budgets.excel import KIND_CHAPTER, KIND_ITEM, ParsedBudget, interpret_rows

from .samples import MAPPING, V1_ROWS, V2_ROWS, V3_ROWS, sheet_rows

D = Decimal


def parsed(rows: list[list[Any]]) -> ParsedBudget:
    return interpret_rows(sheet_rows(rows), MAPPING)


def as_previous(rows: list[list[Any]]) -> dict[str, Values]:
    return {
        r.code: Values(r.kind, r.quantity, r.unit_price, r.total, r.description)
        for r in parsed(rows).rows
    }


def item(quantity: str, price: str) -> Values:
    return Values(KIND_ITEM, D(quantity), D(price), D(quantity) * D(price))


class RowLabelsTests(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(row_labels(item("1", "10"), item("2", "10")), (LABEL_MORE,))
        self.assertEqual(row_labels(item("2", "10"), item("1", "10")), (LABEL_LESS,))
        self.assertEqual(row_labels(item("1", "10"), item("1", "12")), (LABEL_PRICE,))
        self.assertEqual(row_labels(item("1", "10"), item("2", "12")), (LABEL_MORE, LABEL_PRICE))
        self.assertEqual(row_labels(None, item("1", "10")), (LABEL_NEW,))
        self.assertEqual(row_labels(item("1", "10"), None), (LABEL_REMOVED,))
        self.assertEqual(row_labels(item("1", "10"), item("1", "10")), ())
        chapter = Values(KIND_CHAPTER, None, None, D("5"))
        bigger = Values(KIND_CHAPTER, None, None, D("9"))
        self.assertEqual(row_labels(chapter, bigger), ())
        self.assertEqual(row_labels(None, chapter), (LABEL_NEW,))


class SummarizeChangesTests(unittest.TestCase):
    def test_first_version_is_all_new(self) -> None:
        summary = summarize_changes({}, parsed(V1_ROWS), set())
        self.assertEqual(
            (summary["item_count"], summary["chapter_count"], summary["added"]), (5, 2, 5)
        )
        self.assertEqual(summary["direct_cost"], "9130000.00")
        self.assertIsNone(summary["previous_direct_cost"])

    def test_second_version_against_the_first(self) -> None:
        previous = as_previous(V1_ROWS)
        summary = summarize_changes(previous, parsed(V2_ROWS), set(previous))
        self.assertEqual(
            {
                key: summary[key]
                for key in (
                    "added",
                    "reappeared",
                    "removed",
                    "more_quantity",
                    "less_quantity",
                    "price_changed",
                )
            },
            {
                "added": 1,
                "reappeared": 0,
                "removed": 1,
                "more_quantity": 1,
                "less_quantity": 1,
                "price_changed": 1,
            },
        )
        self.assertEqual(summary["examples"]["added"], ["2.3 Formaleta"])
        self.assertEqual(summary["examples"]["removed"], ["1.2 Cerramiento provisional"])
        self.assertEqual(summary["previous_direct_cost"], "9130000.00")
        self.assertEqual(summary["direct_cost"], "8690000.00")

    def test_a_removed_item_that_comes_back_is_not_new(self) -> None:
        previous = as_previous(V2_ROWS)
        known = set(previous) | {"1.2"}
        summary = summarize_changes(previous, parsed(V3_ROWS), known)
        self.assertEqual((summary["added"], summary["reappeared"]), (0, 1))
        self.assertEqual(summary["examples"]["reappeared"], ["1.2 Cerramiento provisional"])

    def test_parser_figures_are_carried(self) -> None:
        rows = [*V1_ROWS, [None, "Subtotal", None, None, None, 1]]
        summary = summarize_changes({}, parsed(rows), set())
        self.assertEqual(summary["ignored_rows"], 1)
        self.assertEqual(summary["ignored_examples"], [9])
        self.assertEqual(len(summary["warnings"]), 1)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_diff.py`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/budgets/diff.py`:

```python
"""Qué cambió entre dos versiones de un presupuesto (puro).

- `row_labels`: las etiquetas de una fila en la comparación contractual contra
  vigente (*Mayor cantidad*, *Menor cantidad*, *Cambio de precio*, *No
  previsto*, *Eliminado*). Un ítem puede llevar dos (cantidad y precio); un
  capítulo solo *No previsto* o *Eliminado*: su diferencia se ve en el total.
- `summarize_changes`: el resumen de la vista previa de importación, contra la
  versión vigente. Cuenta solo ítems; los capítulos son estructura.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .excel import KIND_ITEM, ParsedBudget
from .totals import ZERO

LABEL_MORE = "Mayor cantidad"
LABEL_LESS = "Menor cantidad"
LABEL_PRICE = "Cambio de precio"
LABEL_NEW = "No previsto"
LABEL_REMOVED = "Eliminado"
#: Sufijo de la clase CSS de cada etiqueta (`bg-label-<sufijo>`).
LABEL_CLASSES = {
    LABEL_MORE: "more",
    LABEL_LESS: "less",
    LABEL_PRICE: "price",
    LABEL_NEW: "new",
    LABEL_REMOVED: "removed",
}
MAX_EXAMPLES = 20
GROUPS = ("added", "reappeared", "removed", "more_quantity", "less_quantity", "price_changed")


@dataclass(frozen=True)
class Values:
    """Los valores de una fila en una versión."""

    kind: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    description: str = ""


def row_labels(before: Values | None, after: Values | None) -> tuple[str, ...]:
    if before is None and after is None:
        return ()
    if before is None:
        return (LABEL_NEW,)
    if after is None:
        return (LABEL_REMOVED,)
    if before.kind != KIND_ITEM or after.kind != KIND_ITEM:
        return ()
    labels: list[str] = []
    old_qty, new_qty = before.quantity or ZERO, after.quantity or ZERO
    if new_qty > old_qty:
        labels.append(LABEL_MORE)
    elif new_qty < old_qty:
        labels.append(LABEL_LESS)
    if before.unit_price != after.unit_price:
        labels.append(LABEL_PRICE)
    return tuple(labels)


def _label(code: str, description: str) -> str:
    return f"{code} {description[:80]}".strip()


def summarize_changes(
    previous: Mapping[str, Values], parsed: ParsedBudget, known_codes: Collection[str]
) -> dict[str, Any]:
    """Qué cambiaría al aplicar `parsed` sobre la versión vigente.

    `previous`: las filas de la vigente por código (vacío si es la primera).
    `known_codes`: todos los códigos que el presupuesto tuvo alguna vez
    (`BudgetItem`): un ítem que no está en la vigente pero sí aquí "vuelve".
    Los números van como texto: `summary` es JSON.
    """
    counts = dict.fromkeys(GROUPS, 0)
    examples: dict[str, list[str]] = {group: [] for group in GROUPS}

    def note(group: str, label: str) -> None:
        counts[group] += 1
        if len(examples[group]) < MAX_EXAMPLES:
            examples[group].append(label)

    items = [row for row in parsed.rows if row.kind == KIND_ITEM]
    parsed_items = {row.code for row in items}
    for row in items:
        label = _label(row.code, row.description)
        before = previous.get(row.code)
        if before is None or before.kind != KIND_ITEM:
            known = before is None and row.code in known_codes
            note("reappeared" if known else "added", label)
            continue
        after = Values(row.kind, row.quantity, row.unit_price, row.total, row.description)
        for tag in row_labels(before, after):
            if tag == LABEL_MORE:
                note("more_quantity", label)
            elif tag == LABEL_LESS:
                note("less_quantity", label)
            elif tag == LABEL_PRICE:
                note("price_changed", label)
    for code, before in previous.items():
        if before.kind == KIND_ITEM and code not in parsed_items:
            note("removed", _label(code, before.description))

    previous_direct = (
        sum((v.total for v in previous.values() if v.kind == KIND_ITEM), ZERO)
        if previous
        else None
    )
    return {
        "row_count": len(parsed.rows),
        "item_count": len(items),
        "chapter_count": len(parsed.rows) - len(items),
        **counts,
        "examples": examples,
        "ignored_rows": parsed.ignored_rows,
        "ignored_examples": list(parsed.ignored_examples),
        "total_mismatches": parsed.total_mismatches,
        "warnings": list(parsed.warnings),
        "direct_cost": str(parsed.direct_cost),
        "previous_direct_cost": None if previous_direct is None else str(previous_direct),
    }
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_diff.py`
Expected: todo pasa.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/budgets/diff.py server/visitacampo/budgets/tests/test_diff.py
git commit -m "feat(budgets): comparison labels and change summary"
```

---
### Task 8: Importación — subir, preparar la vista previa, editar y descartar

**Files:**
- Create: `server/visitacampo/budgets/importer.py`
- Create: `server/visitacampo/budgets/tests/factories.py`
- Test: `server/visitacampo/budgets/tests/test_importer.py`

- [ ] **Step 1: Fábricas con BD**

`server/visitacampo/budgets/tests/factories.py`:

```python
"""Presupuestos de prueba con BD. Los tests sí pueden usar `Model.objects`."""

from __future__ import annotations

from typing import Any

from visitacampo.budgets.importer import create_upload, prepare_version
from visitacampo.core.context import RequestContext
from visitacampo.core.models import AppUser, Budget, BudgetVersion, Project

from .samples import MAPPING, xlsx


def make_budget(
    project: Project, user: AppUser, *, name: str = "Contrato de obra", code: str = "C-01"
) -> Budget:
    return Budget.objects.create(
        org_id=project.org_id, project=project, name=name, code=code, created_by=user
    )


def stage_rows(
    ctx: RequestContext,
    budget: Budget,
    rows: list[list[Any]],
    *,
    file_name: str = "presupuesto.xlsx",
) -> BudgetVersion:
    """Sube el Excel de `rows` y prepara su vista previa con `samples.MAPPING`."""
    version = create_upload(ctx, budget, file_name=file_name, data=xlsx(rows))
    return prepare_version(ctx, version.pk, MAPPING)
```

- [ ] **Step 2: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_importer.py`:

```python
"""Importación de versiones de presupuesto: subida, vista previa, aplicación."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

from django.http import Http404
from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets import importer
from visitacampo.budgets.importer import (
    ImportRejected,
    charges_of,
    create_upload,
    discard_version,
    prepare_version,
    update_pending,
)
from visitacampo.budgets.mapping import ColumnMapping
from visitacampo.budgets.totals import ChargeSpec, default_charges
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import make_budget, stage_rows
from .samples import AIU, MAPPING, V1_ROWS, xlsx

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class ImporterTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        self.budget = make_budget(self.project, self.admin)


@skip_without_db
class UploadAndPrepareTests(ImporterTestCase):
    def test_upload_keeps_the_file_as_a_pending_version_without_summary(self) -> None:
        data = xlsx(V1_ROWS)
        version = create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=data)
        self.assertEqual(version.status, BudgetVersionStatus.PENDING)
        self.assertIsNone(version.number)
        self.assertEqual(version.summary, {})
        self.assertEqual(bytes(BudgetVersion.objects.get(pk=version.pk).source_file), data)

    def test_upload_rejects_bad_files(self) -> None:
        for name, data, words in (
            ("v1.xlsm", xlsx(V1_ROWS), "macros"),
            ("v1.xlsx", b"hola", "no es un archivo .xlsx"),
        ):
            with self.subTest(name=name), self.assertRaises(ImportRejected) as caught:
                create_upload(self.ctx, self.budget, file_name=name, data=data)
            self.assertIn(words, str(caught.exception))
        with mock.patch.object(importer, "MAX_BYTES", 10), self.assertRaises(ImportRejected):
            create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))
        self.assertFalse(BudgetVersion.objects.exists())

    def test_upload_to_a_budget_of_another_organization_is_404(self) -> None:
        other = make_org("Otra")
        outsider = make_user(other, username="otro", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=outsider.pk, org_id=other.pk))
        with self.assertRaises(Http404):
            create_upload(ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))

    def test_prepare_the_first_version(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        summary = version.summary
        self.assertEqual(
            (summary["added"], summary["item_count"], summary["chapter_count"]), (5, 5, 2)
        )
        self.assertIsNone(summary["base_number"])
        self.assertIsNone(summary["previous_total"])
        self.assertEqual(summary["mapping"], MAPPING.to_json())
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.column_mapping, MAPPING.to_json())
        self.assertEqual(charges_of(self.ctx, version), default_charges())
        self.assertFalse(BudgetItemVersion.objects.exists())  # nada aplicado aún

    def test_a_mapping_that_does_not_read_is_not_saved(self) -> None:
        version = create_upload(self.ctx, self.budget, file_name="v1.xlsx", data=xlsx(V1_ROWS))
        wrong = ColumnMapping(sheet="Hoja1", first_row=2, columns=MAPPING.columns)
        with self.assertRaises(ImportRejected) as caught:
            prepare_version(self.ctx, version.pk, wrong)
        self.assertIn("no tiene la hoja «Hoja1»", str(caught.exception))
        self.budget.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual((self.budget.column_mapping, version.summary), ({}, {}))

    def test_update_pending_saves_label_and_charges(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        update_pending(self.ctx, version.pk, label="  Otrosí 1 ", charges=AIU)
        version.refresh_from_db()
        self.assertEqual(version.label, "Otrosí 1")
        self.assertEqual(charges_of(self.ctx, version), AIU)

    def test_invalid_charges_change_nothing(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        with self.assertRaises(ImportRejected):
            update_pending(
                self.ctx,
                version.pk,
                label="X",
                charges=[ChargeSpec(1, "A", D("1"), "line:1")],
            )
        version.refresh_from_db()
        self.assertEqual(version.label, "")
        self.assertEqual(charges_of(self.ctx, version), default_charges())

    def test_preparing_again_keeps_the_edited_charges(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        update_pending(self.ctx, version.pk, label="", charges=AIU[:2])
        prepare_version(self.ctx, version.pk, MAPPING)
        self.assertEqual(charges_of(self.ctx, version), AIU[:2])

    def test_discard(self) -> None:
        version = stage_rows(self.ctx, self.budget, V1_ROWS)
        discard_version(self.ctx, version.pk)
        version.refresh_from_db()
        self.assertEqual(version.status, BudgetVersionStatus.DISCARDED)
        with self.assertRaises(ImportRejected):
            update_pending(self.ctx, version.pk, label="", charges=[])
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_importer.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.budgets.importer'`.

- [ ] **Step 4: Escribir la primera parte del importador**

`server/visitacampo/budgets/importer.py`:

```python
"""Importación de una versión de presupuesto desde Excel, en dos pasos.

1. `create_upload` guarda el `.xlsx` como versión pendiente. `prepare_version`
   lo lee con el mapeo de columnas, calcula el resumen de cambios contra la
   versión vigente, guarda el mapeo en el presupuesto y deja los cargos listos
   para editar (vista previa). `update_pending` guarda etiqueta y cargos.
2. `apply_version`: en una transacción, con el presupuesto bloqueado, vuelve a
   leer el archivo con el mapeo de la vista previa, crea los ítems nuevos y los
   valores de la versión, calcula cargos y totales y la numera. La primera
   aplicada queda como contractual. `discard_version` la descarta.

`mark_contractual` cambia la versión contractual. Todas las escrituras toman
el mismo bloqueo del presupuesto (`select_for_update(nowait=True)`): dos
importaciones simultáneas no se esperan, la segunda se rechaza con un mensaje.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from django.db import DatabaseError, transaction
from django.http import Http404

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
)
from visitacampo.core.scoping import scoped_qs

from .diff import Values, summarize_changes
from .excel import (
    MAX_BYTES,
    ExcelRejected,
    ParsedBudget,
    check_file_name,
    parse_budget,
    preview_sheet,
)
from .mapping import ColumnMapping
from .totals import ChargeError, ChargeSpec, default_charges, validate_charges

BUSY = "Otra importación de este presupuesto está en curso; intenta de nuevo en unos segundos."


class ImportRejected(Exception):
    """La importación no se puede hacer; el mensaje es para el usuario."""


def create_upload(
    ctx: RequestContext, budget: Budget, *, file_name: str, data: bytes
) -> BudgetVersion:
    """Guarda el archivo como versión pendiente, todavía sin leer (sin `summary`)."""
    if budget.org_id != ctx.org_id:
        raise Http404("Presupuesto no encontrado.")
    if len(data) > MAX_BYTES:
        raise ImportRejected("El archivo supera 20 MB.")
    try:
        check_file_name(file_name)
        preview_sheet(data, rows=1)  # un .xlsx legible, antes de guardarlo
    except ExcelRejected as exc:
        raise ImportRejected(str(exc)) from exc
    return scoped_qs(ctx, BudgetVersion).create(
        org_id=ctx.org_id,
        budget=budget,
        file_name=file_name[:255],
        source_file=data,
        uploaded_by_id=ctx.user_id,
    )


def current_version(ctx: RequestContext, budget: Budget) -> BudgetVersion | None:
    """La versión vigente: la última aplicada (sin cargar el archivo)."""
    return (
        scoped_qs(ctx, BudgetVersion)
        .filter(budget=budget, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("-number")
        .first()
    )


def charges_of(ctx: RequestContext, version: BudgetVersion) -> list[ChargeSpec]:
    return [
        ChargeSpec(order=c.order, name=c.name, pct=c.pct, base=c.base)
        for c in scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order")
    ]


def _values_by_code(ctx: RequestContext, version: BudgetVersion | None) -> dict[str, Values]:
    if version is None:
        return {}
    rows = (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version)
        .values_list("item__code", "kind", "quantity", "unit_price", "total", "description")
    )
    return {
        code: Values(kind, qty, price, total, description)
        for code, kind, qty, price, total, description in rows
    }


def _parse(data: bytes, mapping: ColumnMapping) -> ParsedBudget:
    try:
        return parse_budget(data, mapping)
    except ExcelRejected as exc:
        raise ImportRejected(str(exc)) from exc


def _locked_budget(ctx: RequestContext, budget_id: UUID) -> Budget:
    """El presupuesto bloqueado para esta transacción, sin esperar a otra."""
    try:
        return scoped_qs(ctx, Budget).select_for_update(nowait=True).get(pk=budget_id)
    except Budget.DoesNotExist as exc:
        raise Http404("Presupuesto no encontrado.") from exc
    except DatabaseError as exc:
        raise ImportRejected(BUSY) from exc


def _locked_pending(
    ctx: RequestContext, version_id: UUID, *, with_file: bool = False
) -> tuple[BudgetVersion, Budget]:
    """La versión pendiente y su presupuesto, bloqueados. Sin el archivo (hasta
    20 MB) salvo que haga falta leerlo."""
    versions = scoped_qs(ctx, BudgetVersion)
    try:
        budget_id = versions.values_list("budget_id", flat=True).get(pk=version_id)
    except BudgetVersion.DoesNotExist as exc:
        raise ImportRejected("La versión no existe.") from exc
    budget = _locked_budget(ctx, budget_id)
    locked = versions.select_for_update()
    version = (locked if with_file else locked.defer("source_file")).get(pk=version_id)
    if version.status != BudgetVersionStatus.PENDING:
        raise ImportRejected("Esta versión ya fue aplicada o descartada.")
    return version, budget


def _replace_charges(
    ctx: RequestContext, version: BudgetVersion, charges: Sequence[ChargeSpec]
) -> None:
    qs = scoped_qs(ctx, BudgetCharge)
    qs.filter(version=version).delete()
    qs.bulk_create(
        [
            BudgetCharge(
                org_id=ctx.org_id,
                version=version,
                order=c.order,
                name=c.name.strip(),
                pct=c.pct,
                base=c.base,
            )
            for c in charges
        ]
    )


def prepare_version(
    ctx: RequestContext, version_id: UUID, mapping: ColumnMapping
) -> BudgetVersion:
    """Lee la versión pendiente con `mapping` y deja lista su vista previa."""
    with transaction.atomic():
        version, budget = _locked_pending(ctx, version_id, with_file=True)
        parsed = _parse(bytes(version.source_file), mapping)
        current = current_version(ctx, budget)
        known = set(
            scoped_qs(ctx, BudgetItem).filter(budget=budget).values_list("code", flat=True)
        )
        summary = summarize_changes(_values_by_code(ctx, current), parsed, known)
        summary["mapping"] = mapping.to_json()
        # Versión sobre la que se calculó la vista previa; si cambia, hay que re-subir.
        summary["base_number"] = current.number if current else None
        summary["previous_total"] = (
            str(current.total) if current is not None and current.total is not None else None
        )
        version.summary = summary
        version.save(update_fields=["summary"])
        budget.column_mapping = mapping.to_json()
        budget.save(update_fields=["column_mapping"])
        if not scoped_qs(ctx, BudgetCharge).filter(version=version).exists():
            template = charges_of(ctx, current) if current is not None else default_charges()
            _replace_charges(ctx, version, template)
        return version


def update_pending(
    ctx: RequestContext, version_id: UUID, *, label: str, charges: Sequence[ChargeSpec]
) -> BudgetVersion:
    """Guarda la etiqueta y los cargos de una versión pendiente."""
    try:
        validate_charges(charges)
    except ChargeError as exc:
        raise ImportRejected(str(exc)) from exc
    with transaction.atomic():
        version, _ = _locked_pending(ctx, version_id)
        version.label = label.strip()[:200]
        version.save(update_fields=["label"])
        _replace_charges(ctx, version, charges)
        return version


def discard_version(ctx: RequestContext, version_id: UUID) -> BudgetVersion:
    with transaction.atomic():
        version, _ = _locked_pending(ctx, version_id)
        version.status = BudgetVersionStatus.DISCARDED
        version.save(update_fields=["status"])
        return version
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_importer.py`
Expected: todo pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/budgets/importer.py server/visitacampo/budgets/tests/factories.py server/visitacampo/budgets/tests/test_importer.py
git commit -m "feat(budgets): stage budget versions with preview, charges and discard"
```

---

### Task 9: Importación — aplicar, contractual, vista previa obsoleta y concurrencia

**Files:**
- Modify: `server/visitacampo/budgets/importer.py`
- Modify: `server/visitacampo/budgets/tests/factories.py`
- Modify: `server/visitacampo/budgets/tests/test_importer.py`

- [ ] **Step 1: Fábrica `apply_rows`**

En `server/visitacampo/budgets/tests/factories.py`, cambiar el import del importador por:

```python
from visitacampo.budgets.importer import (
    apply_version,
    charges_of,
    create_upload,
    prepare_version,
    update_pending,
)
from visitacampo.budgets.totals import ChargeSpec
```

y añadir al final:

```python
def apply_rows(
    ctx: RequestContext,
    budget: Budget,
    rows: list[list[Any]],
    *,
    label: str = "",
    charges: list[ChargeSpec] | None = None,
) -> BudgetVersion:
    """Sube, prepara y aplica una versión. Sin `charges` usa los que propone la vista previa."""
    version = stage_rows(ctx, budget, rows)
    if label or charges is not None:
        update_pending(
            ctx,
            version.pk,
            label=label,
            charges=charges if charges is not None else charges_of(ctx, version),
        )
    return apply_version(ctx, version.pk)
```

- [ ] **Step 2: Escribir la prueba que falla**

En `server/visitacampo/budgets/tests/test_importer.py`:

- Añadir `from django.db import connection, connections` y cambiar `from django.test import TestCase` por `from django.test import TestCase, TransactionTestCase`.
- En el import de `visitacampo.budgets.importer` añadir `apply_version` y `mark_contractual`.
- En el import de modelos añadir `Budget`, `BudgetCharge` y `BudgetItem`.
- Cambiar `from .factories import make_budget, stage_rows` por `from .factories import apply_rows, make_budget, stage_rows` y `from .samples import AIU, MAPPING, V1_ROWS, xlsx` por `from .samples import AIU, MAPPING, V1_ROWS, V2_ROWS, V3_ROWS, xlsx`.

Y al final del archivo:

```python
@skip_without_db
class ApplyVersionTests(ImporterTestCase):
    def test_first_version_becomes_contractual_with_items_totals_and_charges(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS, label="Contrato", charges=AIU)
        self.assertEqual((v1.number, v1.status, v1.label), (1, "applied", "Contrato"))
        self.assertIsNotNone(v1.applied_at)
        self.assertEqual((v1.direct_cost, v1.total), (D("9130000.00"), D("11042735.00")))
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v1.pk)
        rows = {
            r.item.code: r
            for r in BudgetItemVersion.objects.filter(version=v1).select_related("item", "parent")
        }
        self.assertEqual(len(rows), 7)
        self.assertEqual((rows["1"].total, rows["2"].total), (D("1150000.00"), D("7980000.00")))
        self.assertEqual(rows["1.1"].parent.code, "1")
        self.assertIsNone(rows["1"].parent)
        self.assertEqual(rows["2.2"].quantity, D("800"))
        amounts = list(
            BudgetCharge.objects.filter(version=v1)
            .order_by("order")
            .values_list("amount", flat=True)
        )
        self.assertEqual(amounts, [D("913000"), D("456500"), D("456500"), D("86735")])

    def test_second_version_keeps_the_contractual_and_copies_the_charges(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        staged = stage_rows(self.ctx, self.budget, V2_ROWS)
        summary = staged.summary
        self.assertEqual(
            [
                summary[key]
                for key in (
                    "added",
                    "removed",
                    "more_quantity",
                    "less_quantity",
                    "price_changed",
                )
            ],
            [1, 1, 1, 1, 1],
        )
        self.assertEqual((summary["base_number"], summary["previous_total"]), (1, "11042735.00"))
        self.assertEqual(charges_of(self.ctx, staged), AIU)
        v2 = apply_version(self.ctx, staged.pk)
        self.assertEqual(
            (v2.number, v2.direct_cost, v2.total), (2, D("8690000.00"), D("10510555.00"))
        )
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v1.pk)
        self.assertEqual(BudgetItem.objects.filter(budget=self.budget).count(), 8)
        self.assertFalse(BudgetItemVersion.objects.filter(version=v2, item__code="1.2").exists())

    def test_a_removed_item_comes_back_with_the_same_identity(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        item = BudgetItem.objects.get(budget=self.budget, code="1.2")
        staged = stage_rows(self.ctx, self.budget, V3_ROWS)
        self.assertEqual((staged.summary["added"], staged.summary["reappeared"]), (0, 1))
        v3 = apply_version(self.ctx, staged.pk)
        self.assertTrue(BudgetItemVersion.objects.filter(version=v3, item=item).exists())

    def test_cannot_apply_twice_nor_without_mapping(self) -> None:
        staged = stage_rows(self.ctx, self.budget, V1_ROWS)
        apply_version(self.ctx, staged.pk)
        with self.assertRaises(ImportRejected):
            apply_version(self.ctx, staged.pk)
        raw = create_upload(self.ctx, self.budget, file_name="v2.xlsx", data=xlsx(V2_ROWS))
        with self.assertRaises(ImportRejected) as caught:
            apply_version(self.ctx, raw.pk)
        self.assertIn("mapeo", str(caught.exception))

    def test_stale_preview_is_rejected(self) -> None:
        old = stage_rows(self.ctx, self.budget, V1_ROWS)
        newer = stage_rows(self.ctx, self.budget, V2_ROWS)
        apply_version(self.ctx, newer.pk)
        with self.assertRaises(ImportRejected) as caught:
            apply_version(self.ctx, old.pk)
        self.assertIn("Vuelve a subir", str(caught.exception))
        old.refresh_from_db()
        self.assertEqual(old.status, BudgetVersionStatus.PENDING)

    def test_mark_contractual(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        v2 = apply_rows(self.ctx, self.budget, V2_ROWS)
        previous, version = mark_contractual(self.ctx, self.budget.pk, v2.pk)
        self.assertEqual((previous, version.pk), (1, v2.pk))
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, v2.pk)
        pending = stage_rows(self.ctx, self.budget, V3_ROWS)
        with self.assertRaises(ImportRejected):
            mark_contractual(self.ctx, self.budget.pk, pending.pk)
        other = make_budget(self.project, self.admin, name="Interventoría")
        foreign = apply_rows(self.ctx, other, V1_ROWS)
        with self.assertRaises(ImportRejected):
            mark_contractual(self.ctx, self.budget.pk, foreign.pk)


@skip_without_db
class ConcurrentImportTests(TransactionTestCase):
    """Otra transacción tiene bloqueado el presupuesto: se rechaza sin esperar."""

    def test_apply_is_rejected_while_the_budget_is_locked(self) -> None:
        org = make_org("Constructora")
        admin = make_user(org, username="admin", role=Role.ADMIN)
        ctx = resolve_request_context(Principal(user_id=admin.pk, org_id=org.pk))
        project = Project.objects.create(org=org, name="Obra", created_by=admin)
        budget = make_budget(project, admin)
        staged = stage_rows(ctx, budget, V1_ROWS)

        other = connections.create_connection("default")
        other.set_autocommit(False)
        try:
            with other.cursor() as cursor:
                table = connection.ops.quote_name(Budget._meta.db_table)
                cursor.execute(f"SELECT id FROM {table} WHERE id = %s FOR UPDATE", [budget.pk])
            with self.assertRaises(ImportRejected) as caught:
                apply_version(ctx, staged.pk)
            self.assertIn("en curso", str(caught.exception))
        finally:
            other.rollback()
            other.close()
        staged.refresh_from_db()
        self.assertEqual(staged.status, BudgetVersionStatus.PENDING)
```

- [ ] **Step 3: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_importer.py`
Expected: FAIL con `ImportError: cannot import name 'apply_version'`.

- [ ] **Step 4: Escribir la segunda parte del importador**

En `server/visitacampo/budgets/importer.py`:

- Añadir `from django.db.models import Max` y `from django.utils import timezone` a los imports de Django.
- Cambiar `from .mapping import ColumnMapping` por `from .mapping import ColumnMapping, MappingError`.
- Cambiar el import de `.totals` por:
  `from .totals import ChargeError, ChargeSpec, compute_charges, default_charges, validate_charges`.

Y al final del archivo:

```python
def _ensure_items(
    ctx: RequestContext, budget: Budget, parsed: ParsedBudget
) -> dict[str, BudgetItem]:
    """Los `BudgetItem` de cada código: los existentes (aunque hayan sido
    eliminados en versiones anteriores, así vuelven con la misma identidad) y
    los nuevos."""
    qs = scoped_qs(ctx, BudgetItem)
    items = {item.code: item for item in qs.filter(budget=budget)}
    new = [
        BudgetItem(org_id=ctx.org_id, budget=budget, code=row.code)
        for row in parsed.rows
        if row.code not in items
    ]
    qs.bulk_create(new, batch_size=1000)
    items.update({item.code: item for item in new})
    return items


def apply_version(ctx: RequestContext, version_id: UUID) -> BudgetVersion:
    with transaction.atomic():
        version, budget = _locked_pending(ctx, version_id, with_file=True)
        summary = version.summary
        if "mapping" not in summary:
            raise ImportRejected("Falta confirmar el mapeo de columnas de esta versión.")
        current = current_version(ctx, budget)
        if (current.number if current else None) != summary.get("base_number"):
            raise ImportRejected(
                "Se aplicó otra versión de este presupuesto después de subir esta. "
                "Vuelve a subir el archivo para ver los cambios actualizados."
            )
        try:
            mapping = ColumnMapping.from_json(summary["mapping"])
        except MappingError as exc:
            raise ImportRejected(str(exc)) from exc
        parsed = _parse(bytes(version.source_file), mapping)
        items = _ensure_items(ctx, budget, parsed)
        scoped_qs(ctx, BudgetItemVersion).bulk_create(
            [
                BudgetItemVersion(
                    org_id=ctx.org_id,
                    version=version,
                    item=items[row.code],
                    kind=row.kind,
                    sort_order=row.sort_order,
                    description=row.description,
                    unit=row.unit,
                    quantity=row.quantity,
                    unit_price=row.unit_price,
                    total=row.total,
                    parent=items[row.parent_code] if row.parent_code else None,
                )
                for row in parsed.rows
            ],
            batch_size=1000,
        )

        charges = list(scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order"))
        try:
            result = compute_charges(
                parsed.direct_cost,
                [ChargeSpec(c.order, c.name, c.pct, c.base) for c in charges],
            )
        except ChargeError as exc:
            raise ImportRejected(str(exc)) from exc
        amounts = {line.order: line.amount for line in result.lines}
        for charge in charges:
            charge.amount = amounts[charge.order]
        scoped_qs(ctx, BudgetCharge).bulk_update(charges, ["amount"])

        applied = scoped_qs(ctx, BudgetVersion).filter(budget=budget)
        version.number = (applied.aggregate(n=Max("number"))["n"] or 0) + 1
        version.status = BudgetVersionStatus.APPLIED
        version.applied_at = timezone.now()
        version.direct_cost = result.direct_cost
        version.total = result.total
        version.save(
            update_fields=["number", "status", "applied_at", "direct_cost", "total"]
        )
        if budget.contractual_version_id is None:
            budget.contractual_version = version
            budget.save(update_fields=["contractual_version"])
        return version


def mark_contractual(
    ctx: RequestContext, budget_id: UUID, version_id: UUID
) -> tuple[int | None, BudgetVersion]:
    """Hace contractual una versión aplicada. Devuelve el número de la anterior."""
    with transaction.atomic():
        budget = _locked_budget(ctx, budget_id)
        try:
            version = (
                scoped_qs(ctx, BudgetVersion)
                .defer("source_file")
                .get(pk=version_id, budget=budget, status=BudgetVersionStatus.APPLIED)
            )
        except BudgetVersion.DoesNotExist as exc:
            raise ImportRejected(
                "Solo una versión aplicada de este presupuesto puede ser la contractual."
            ) from exc
        previous: int | None = (
            scoped_qs(ctx, BudgetVersion)
            .filter(pk=budget.contractual_version_id)
            .values_list("number", flat=True)
            .first()
        )
        budget.contractual_version = version
        budget.save(update_fields=["contractual_version"])
        return previous, version
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_importer.py`
Expected: todo pasa. (Recuerda los números de `samples.py`: con AIU, v1 = 11.042.735,00 y v2 = 10.510.555,00.)

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/budgets/importer.py server/visitacampo/budgets/tests
git commit -m "feat(budgets): apply versions under lock, stale check and contractual change"
```

---
### Task 10: Comparación, filtros, filas de una versión y totales del proyecto (`comparison.py`)

**Files:**
- Create: `server/visitacampo/budgets/comparison.py`
- Test: `server/visitacampo/budgets/tests/test_comparison.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_comparison.py`:

```python
"""Comparación contractual contra vigente, filtros y totales (coste fijo en consultas)."""

from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Any

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.budgets.comparison import (
    Comparison,
    budget_summaries,
    build_comparison,
    filter_rows,
    version_rows,
)
from visitacampo.budgets.diff import (
    LABEL_LESS,
    LABEL_MORE,
    LABEL_NEW,
    LABEL_PRICE,
    LABEL_REMOVED,
)
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    BudgetItem,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
    Role,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)

from .factories import apply_rows, make_budget
from .samples import AIU, V1_ROWS, V2_ROWS

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class ComparisonTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.project = Project.objects.create(org=self.org, name="Obra", created_by=self.admin)
        self.budget = make_budget(self.project, self.admin)

    def compare_v1_v2(self) -> Comparison:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        self.budget.refresh_from_db()
        return build_comparison(self.ctx, self.budget)


@skip_without_db
class BuildComparisonTests(ComparisonTestCase):
    def test_rows_follow_the_current_order_with_removed_rows_in_place(self) -> None:
        data = self.compare_v1_v2()
        self.assertEqual(
            [r.code for r in data.rows], ["1", "1.1", "1.2", "2", "2.1", "2.2", "2.3", "2.4"]
        )
        self.assertEqual(
            {r.code: r.labels for r in data.rows},
            {
                "1": (),
                "1.1": (LABEL_MORE,),
                "1.2": (LABEL_REMOVED,),
                "2": (),
                "2.1": (LABEL_PRICE,),
                "2.2": (LABEL_LESS,),
                "2.3": (LABEL_NEW,),
                "2.4": (),
            },
        )
        self.assertEqual([r.depth for r in data.rows], [0, 1, 1, 0, 1, 1, 1, 1])

    def test_values_and_differences(self) -> None:
        rows = {r.code: r for r in self.compare_v1_v2().rows}
        moved = rows["1.1"]
        assert moved.contractual is not None and moved.current is not None
        self.assertEqual((moved.contractual.quantity, moved.current.quantity), (D("100"), D("120")))
        self.assertEqual((moved.diff, moved.diff_pct), (D("30000.00"), D("20.00")))
        removed = rows["1.2"]
        self.assertIsNone(removed.current)
        self.assertEqual((removed.diff, removed.diff_pct), (D("-1000000.00"), D("-100.00")))
        new = rows["2.3"]
        self.assertIsNone(new.contractual)
        self.assertEqual((new.diff, new.diff_pct), (D("750000.00"), None))
        self.assertEqual(rows["1"].diff, D("-970000.00"))
        self.assertEqual(rows["1.2"].parent_id, rows["1"].item_id)
        self.assertEqual(rows["2.3"].root_id, rows["2"].item_id)
        self.assertEqual(rows["2.3"].badges, [("No previsto", "new")])

    def test_footer_with_charges_and_totals(self) -> None:
        footer = self.compare_v1_v2().footer
        self.assertEqual(
            [f.label for f in footer],
            [
                "Costo directo",
                "Administración",
                "Imprevistos",
                "Utilidad",
                "IVA sobre utilidad",
                "Total",
            ],
        )
        self.assertEqual(
            (footer[0].contractual, footer[0].current, footer[0].diff),
            (D("9130000.00"), D("8690000.00"), D("-440000.00")),
        )
        self.assertEqual(footer[1].note, "10 % sobre costo directo")
        self.assertEqual(footer[4].note, "19 % sobre línea 3 (Utilidad)")
        total = footer[-1]
        self.assertEqual(
            (total.contractual, total.current, total.diff, total.diff_pct, total.strong),
            (D("11042735.00"), D("10510555.00"), D("-532180.00"), D("-4.82"), True),
        )

    def test_counts_and_chapters(self) -> None:
        data = self.compare_v1_v2()
        self.assertEqual([r.code for r in data.chapters], ["1", "2"])
        self.assertEqual((data.item_count, data.changed_count), (6, 5))

    def test_a_single_version_has_no_changes(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        self.budget.refresh_from_db()
        data = build_comparison(self.ctx, self.budget)
        assert data.contractual is not None and data.current is not None
        self.assertEqual(data.contractual.pk, data.current.pk)
        self.assertTrue(all(not r.labels for r in data.rows))
        self.assertEqual(data.footer[-1].diff, D("0.00"))

    def test_without_versions(self) -> None:
        data = build_comparison(self.ctx, self.budget)
        self.assertEqual(
            (data.contractual, data.current, data.rows, data.footer), (None, None, [], [])
        )

    def test_filters_keep_the_chapters_of_what_matches(self) -> None:
        rows = self.compare_v1_v2().rows

        def codes(**filters: Any) -> list[str]:
            return [r.code for r in filter_rows(rows, **filters)]

        chapter_one = next(r.item_id for r in rows if r.code == "1")
        self.assertEqual(codes(), [r.code for r in rows])
        self.assertEqual(codes(changes_only=True), ["1", "1.1", "1.2", "2", "2.1", "2.2", "2.3"])
        self.assertEqual(codes(query="ACERO"), ["2", "2.2"])
        self.assertEqual(codes(query="localizacion"), ["1", "1.1"])
        self.assertEqual(codes(query="2.3"), ["2", "2.3"])
        self.assertEqual(codes(chapter_id=chapter_one), ["1", "1.1", "1.2"])
        self.assertEqual(
            codes(chapter_id=chapter_one, changes_only=True, query="cerramiento"), ["1", "1.2"]
        )

    def test_version_rows(self) -> None:
        v1 = apply_rows(self.ctx, self.budget, V1_ROWS)
        rows = version_rows(self.ctx, v1)
        self.assertEqual(
            [(r.code, r.depth) for r in rows],
            [("1", 0), ("1.1", 1), ("1.2", 1), ("2", 0), ("2.1", 1), ("2.2", 1), ("2.4", 1)],
        )
        self.assertEqual(rows[0].total, D("1150000.00"))

    def test_comparison_costs_three_queries_whatever_the_size(self) -> None:
        versions = [
            BudgetVersion.objects.create(
                org=self.org,
                budget=self.budget,
                number=number,
                status=BudgetVersionStatus.APPLIED,
                file_name="p.xlsx",
                source_file=b"",
                uploaded_by=self.admin,
                direct_cost=D("0"),
                total=D("0"),
            )
            for number in (1, 2)
        ]
        items = BudgetItem.objects.bulk_create(
            [BudgetItem(org=self.org, budget=self.budget, code=str(i)) for i in range(3000)]
        )
        for version in versions:
            BudgetItemVersion.objects.bulk_create(
                [
                    BudgetItemVersion(
                        org=self.org,
                        version=version,
                        item=item,
                        kind="item",
                        sort_order=i,
                        description=f"Ítem {i}",
                        unit="u",
                        quantity=D(i % 7 + version.number),
                        unit_price=D("10.00"),
                        total=D(i % 7 + version.number) * 10,
                    )
                    for i, item in enumerate(items)
                ],
                batch_size=1000,
            )
        self.budget.contractual_version = versions[0]
        self.budget.save(update_fields=["contractual_version"])
        with self.assertNumQueries(3):
            data = build_comparison(self.ctx, self.budget)
        self.assertEqual(len(data.rows), 3000)
        self.assertTrue(all(r.labels == (LABEL_MORE,) for r in data.rows))


@skip_without_db
class BudgetSummariesTests(ComparisonTestCase):
    def test_totals_per_budget_and_for_the_project_in_two_queries(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        other = make_budget(self.project, self.admin, name="Interventoría", code="I-01")
        apply_rows(self.ctx, other, V1_ROWS)  # cargos en 0: total = costo directo
        make_budget(self.project, self.admin, name="Adiciones", code="A-01")
        with self.assertNumQueries(2):
            summaries, totals = budget_summaries(self.ctx, self.project)
        self.assertEqual(
            [s.budget.name for s in summaries], ["Adiciones", "Contrato de obra", "Interventoría"]
        )
        main = summaries[1]
        self.assertEqual(
            (main.contractual_total, main.current_total, main.current_number),
            (D("11042735.00"), D("10510555.00"), 2),
        )
        self.assertEqual((main.diff, main.diff_pct), (D("-532180.00"), D("-4.82")))
        empty = summaries[0]
        self.assertEqual(
            (empty.contractual_total, empty.current_total, empty.diff, empty.diff_pct),
            (None, None, None, None),
        )
        self.assertEqual(
            (totals.contractual, totals.current, totals.diff, totals.diff_pct),
            (D("20172735.00"), D("19640555.00"), D("-532180.00"), D("-2.64")),
        )
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_comparison.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'visitacampo.budgets.comparison'`.

- [ ] **Step 3: Escribir el módulo**

`server/visitacampo/budgets/comparison.py`:

```python
"""Lecturas del presupuesto para mostrar o exportar, con coste fijo en consultas.

- `build_comparison`: contractual contra vigente, fila a fila, con etiquetas,
  diferencias, cargos y totales. **3 consultas**, sin importar el tamaño
  (versiones aplicadas; filas de las dos versiones con `values()`; cargos).
- `filter_rows`: capítulo, "solo con cambios" y búsqueda (en memoria),
  conservando los capítulos ancestros de cada fila que pasa el filtro.
- `version_rows`: el contenido de una versión. 1 consulta.
- `budget_summaries`: los presupuestos de un proyecto con sus totales y el
  total del proyecto. 2 consultas.

Ninguna carga `source_file` (hasta 20 MB por versión).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from visitacampo.core.context import RequestContext
from visitacampo.core.models import (
    Budget,
    BudgetCharge,
    BudgetItemVersion,
    BudgetVersion,
    BudgetVersionStatus,
    Project,
)
from visitacampo.core.scoping import scoped_qs

from .diff import LABEL_CLASSES, Values, row_labels
from .excel import KIND_CHAPTER, KIND_ITEM
from .mapping import fold
from .totals import ZERO, describe_base, pct_change, pct_text

_ROW_FIELDS = (
    "version_id", "item_id", "item__code", "kind", "sort_order", "description",
    "unit", "quantity", "unit_price", "total", "parent_id",
)  # fmt: skip


@dataclass(frozen=True)
class Side:
    """Los valores de una fila en una de las dos versiones."""

    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal


@dataclass(frozen=True)
class ComparisonRow:
    item_id: UUID
    code: str
    kind: str
    description: str
    unit: str
    parent_id: UUID | None
    #: Capítulo de primer nivel que la contiene (ella misma si es de primer nivel).
    root_id: UUID
    depth: int
    contractual: Side | None
    current: Side | None
    diff: Decimal
    diff_pct: Decimal | None
    labels: tuple[str, ...]

    @property
    def badges(self) -> list[tuple[str, str]]:
        """(etiqueta, sufijo de clase CSS) para la plantilla."""
        return [(label, LABEL_CLASSES[label]) for label in self.labels]


@dataclass(frozen=True)
class FooterRow:
    label: str
    note: str
    contractual: Decimal | None
    current: Decimal | None
    diff: Decimal
    diff_pct: Decimal | None
    strong: bool = False


@dataclass(frozen=True)
class Comparison:
    contractual: BudgetVersion | None
    current: BudgetVersion | None
    rows: list[ComparisonRow]
    footer: list[FooterRow]

    @property
    def chapters(self) -> list[ComparisonRow]:
        return [r for r in self.rows if r.depth == 0 and r.kind == KIND_CHAPTER]

    @property
    def item_count(self) -> int:
        return sum(1 for r in self.rows if r.kind == KIND_ITEM)

    @property
    def changed_count(self) -> int:
        return sum(1 for r in self.rows if r.kind == KIND_ITEM and r.labels)

    @property
    def total(self) -> FooterRow | None:
        return self.footer[-1] if self.footer else None


@dataclass(frozen=True)
class VersionRow:
    item_id: UUID
    code: str
    kind: str
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    total: Decimal
    parent_id: UUID | None
    depth: int


@dataclass(frozen=True)
class BudgetSummary:
    budget: Budget
    contractual_total: Decimal | None
    current_total: Decimal | None
    current_number: int | None

    @property
    def diff(self) -> Decimal | None:
        if self.contractual_total is None or self.current_total is None:
            return None
        return self.current_total - self.contractual_total

    @property
    def diff_pct(self) -> Decimal | None:
        if self.contractual_total is None or self.current_total is None:
            return None
        return pct_change(self.contractual_total, self.current_total)


@dataclass(frozen=True)
class ProjectBudgetTotals:
    contractual: Decimal
    current: Decimal

    @property
    def diff(self) -> Decimal:
        return self.current - self.contractual

    @property
    def diff_pct(self) -> Decimal | None:
        return pct_change(self.contractual, self.current)


def _tree(parents: Mapping[UUID, UUID | None]) -> tuple[dict[UUID, int], dict[UUID, UUID]]:
    """Profundidad y raíz de cada fila a partir de su padre (en memoria).

    Un padre que no está en `parents` cuenta como raíz; un ciclo (no debería
    haberlo: los padres salen de prefijos de código) también se corta.
    """
    depth: dict[UUID, int] = {}
    root: dict[UUID, UUID] = {}
    for node in parents:
        chain: list[UUID] = []
        seen: set[UUID] = set()
        current: UUID | None = node
        while current is not None and current not in depth and current not in seen:
            chain.append(current)
            seen.add(current)
            parent = parents.get(current)
            current = parent if parent is not None and parent in parents else None
        if current is None or current in seen:
            top = chain.pop()
            depth[top], root[top] = 0, top
            base_depth, base_root = 0, top
        else:
            base_depth, base_root = depth[current], root[current]
        for item in reversed(chain):
            base_depth += 1
            depth[item], root[item] = base_depth, base_root
    return depth, root


def _side(row: Mapping[str, Any] | None) -> Side | None:
    return None if row is None else Side(row["quantity"], row["unit_price"], row["total"])


def _values(row: Mapping[str, Any] | None) -> Values | None:
    if row is None:
        return None
    return Values(row["kind"], row["quantity"], row["unit_price"], row["total"])


def _footer_row(
    label: str,
    note: str,
    contractual: Decimal | None,
    current: Decimal | None,
    *,
    strong: bool = False,
) -> FooterRow:
    diff = (current or ZERO) - (contractual or ZERO)
    pct = pct_change(contractual, current or ZERO) if contractual is not None else None
    return FooterRow(label, note, contractual, current, diff, pct, strong)


def _charge_note(slot: Mapping[str, BudgetCharge], names: Mapping[UUID, dict[int, str]]) -> str:
    ref = slot.get("current") or slot["contractual"]
    base = describe_base(ref.base, names.get(ref.version_id, {}))
    pcts = [slot[key].pct for key in ("contractual", "current") if key in slot]
    if len(set(pcts)) > 1:
        return f"contractual {pct_text(pcts[0])} %, vigente {pct_text(pcts[1])} % sobre {base}"
    return f"{pct_text(ref.pct)} % sobre {base}"


def build_comparison(ctx: RequestContext, budget: Budget) -> Comparison:
    """Contractual contra vigente, en el orden de la vigente. 3 consultas."""
    applied = list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget=budget, status=BudgetVersionStatus.APPLIED)
        .defer("source_file")
        .order_by("number")
    )
    current = applied[-1] if applied else None
    contractual = next((v for v in applied if v.pk == budget.contractual_version_id), None)
    ids = {v.pk for v in (contractual, current) if v is not None}
    if not ids:
        return Comparison(contractual=None, current=None, rows=[], footer=[])

    con: dict[UUID, dict[str, Any]] = {}
    cur: dict[UUID, dict[str, Any]] = {}
    for row in (
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version_id__in=ids)
        .order_by("sort_order")
        .values(*_ROW_FIELDS)
    ):
        if contractual is not None and row["version_id"] == contractual.pk:
            con[row["item_id"]] = row
        if current is not None and row["version_id"] == current.pk:
            cur[row["item_id"]] = row
    charges = list(scoped_qs(ctx, BudgetCharge).filter(version_id__in=ids).order_by("order"))

    # Orden: el de la vigente; una fila eliminada va tras su hermano anterior
    # en el orden contractual (o al principio si no lo tiene).
    keys: dict[UUID, tuple[int, int, int]] = {
        item_id: (row["sort_order"], 0, 0) for item_id, row in cur.items()
    }
    anchor = -1
    for item_id, row in con.items():
        if item_id in cur:
            anchor = cur[item_id]["sort_order"]
        else:
            keys[item_id] = (anchor, 1, row["sort_order"])
    ordered = sorted(keys, key=keys.__getitem__)
    parents = {item_id: (cur.get(item_id) or con[item_id])["parent_id"] for item_id in ordered}
    depth, root = _tree(parents)

    rows: list[ComparisonRow] = []
    for item_id in ordered:
        before, after = con.get(item_id), cur.get(item_id)
        base = after or before
        assert base is not None
        old = before["total"] if before else ZERO
        new = after["total"] if after else ZERO
        rows.append(
            ComparisonRow(
                item_id=item_id,
                code=base["item__code"],
                kind=base["kind"],
                description=base["description"],
                unit=base["unit"],
                parent_id=parents[item_id],
                root_id=root[item_id],
                depth=depth[item_id],
                contractual=_side(before),
                current=_side(after),
                diff=new - old,
                diff_pct=pct_change(old, new) if before else None,
                labels=row_labels(_values(before), _values(after)),
            )
        )

    footer = [
        _footer_row(
            "Costo directo",
            "",
            contractual.direct_cost if contractual else None,
            current.direct_cost if current else None,
        )
    ]
    names: dict[UUID, dict[int, str]] = {}
    slots: dict[int, dict[str, BudgetCharge]] = {}
    for charge in charges:
        names.setdefault(charge.version_id, {})[charge.order] = charge.name
        slot = slots.setdefault(charge.order, {})
        if contractual is not None and charge.version_id == contractual.pk:
            slot["contractual"] = charge
        if current is not None and charge.version_id == current.pk:
            slot["current"] = charge
    for order in sorted(slots):
        slot = slots[order]
        ref = slot.get("current") or slot["contractual"]
        footer.append(
            _footer_row(
                ref.name,
                _charge_note(slot, names),
                slot["contractual"].amount if "contractual" in slot else None,
                slot["current"].amount if "current" in slot else None,
            )
        )
    footer.append(
        _footer_row(
            "Total",
            "",
            contractual.total if contractual else None,
            current.total if current else None,
            strong=True,
        )
    )
    return Comparison(contractual=contractual, current=current, rows=rows, footer=footer)


def filter_rows(
    rows: Sequence[ComparisonRow],
    *,
    chapter_id: UUID | None = None,
    changes_only: bool = False,
    query: str = "",
) -> list[ComparisonRow]:
    """Las filas que pasan los filtros, más los capítulos que las contienen."""
    needle = fold(query.strip())
    by_id = {r.item_id: r for r in rows}
    keep: set[UUID] = set()
    for r in rows:
        if chapter_id is not None and r.root_id != chapter_id:
            continue
        if changes_only and not r.labels:
            continue
        if needle and needle not in fold(f"{r.code} {r.description}"):
            continue
        keep.add(r.item_id)
        parent = r.parent_id
        while parent is not None and parent not in keep and parent in by_id:
            keep.add(parent)
            parent = by_id[parent].parent_id
    return [r for r in rows if r.item_id in keep]


def version_rows(ctx: RequestContext, version: BudgetVersion) -> list[VersionRow]:
    """El contenido de una versión, en su orden. 1 consulta."""
    values = list(
        scoped_qs(ctx, BudgetItemVersion)
        .filter(version=version)
        .order_by("sort_order")
        .values(
            "item_id", "item__code", "kind", "description", "unit",
            "quantity", "unit_price", "total", "parent_id",
        )  # fmt: skip
    )
    depth, _ = _tree({v["item_id"]: v["parent_id"] for v in values})
    return [
        VersionRow(
            item_id=v["item_id"],
            code=v["item__code"],
            kind=v["kind"],
            description=v["description"],
            unit=v["unit"],
            quantity=v["quantity"],
            unit_price=v["unit_price"],
            total=v["total"],
            parent_id=v["parent_id"],
            depth=depth[v["item_id"]],
        )
        for v in values
    ]


def budget_summaries(
    ctx: RequestContext, project: Project
) -> tuple[list[BudgetSummary], ProjectBudgetTotals]:
    """Los presupuestos del proyecto con sus totales y el total del proyecto. 2 consultas."""
    budgets = list(scoped_qs(ctx, Budget).filter(project=project).order_by("name"))
    totals_by_version: dict[UUID, Decimal] = {}
    latest: dict[UUID, tuple[int, Decimal]] = {}
    for budget_id, version_id, number, total in (
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project=project, status=BudgetVersionStatus.APPLIED)
        .values_list("budget_id", "id", "number", "total")
    ):
        totals_by_version[version_id] = total
        if budget_id not in latest or number > latest[budget_id][0]:
            latest[budget_id] = (number, total)
    summaries = [
        BudgetSummary(
            budget=budget,
            contractual_total=totals_by_version.get(budget.contractual_version_id),
            current_total=latest[budget.pk][1] if budget.pk in latest else None,
            current_number=latest[budget.pk][0] if budget.pk in latest else None,
        )
        for budget in budgets
    ]
    project_totals = ProjectBudgetTotals(
        contractual=sum((s.contractual_total or ZERO for s in summaries), ZERO),
        current=sum((s.current_total or ZERO for s in summaries), ZERO),
    )
    return summaries, project_totals
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_comparison.py`
Expected: todo pasa. Si `assertNumQueries(3)` cuenta más, revisa que nada en `build_comparison` navegue relaciones de los objetos (`charge.version`, `budget.contractual_version`): solo se leen los `*_id`.

- [ ] **Step 5: Commit**

```bash
git add server/visitacampo/budgets/comparison.py server/visitacampo/budgets/tests/test_comparison.py
git commit -m "feat(budgets): contractual vs current comparison in constant queries"
```

---
### Task 11: Web — acceso, pestaña, lista de presupuestos y comparación

**Files:**
- Create: `server/visitacampo/budgets/access.py`, `server/visitacampo/budgets/views.py`, `server/visitacampo/budgets/urls.py`
- Modify: `server/config/urls.py`
- Modify: `server/visitacampo/projects/views.py`
- Modify: `server/visitacampo/core/templates/suno/projects/_header.html`
- Create: `server/visitacampo/core/templates/suno/budgets/list.html`, `_nav.html`, `compare.html`
- Modify: `server/visitacampo/core/static/suno/css/projects.css`
- Test: `server/visitacampo/budgets/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

`server/visitacampo/budgets/tests/test_views.py`:

```python
"""Páginas de presupuestos: acceso por rol, lista, comparación, importación, historial."""

from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from visitacampo.authn.tests.factories import make_org, make_user
from visitacampo.core.context import Principal, resolve_request_context
from visitacampo.core.models import (
    AppUser,
    AuditEvent,
    Budget,
    BudgetItem,
    Project,
    Role,
    Task,
    TaskResponsible,
)
from visitacampo.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from visitacampo.projects.importer import apply_import, stage_import
from visitacampo.projects.tests.samples import v1 as schedule_v1

from .factories import apply_rows, make_budget
from .samples import AIU, V1_ROWS, V2_ROWS

setUpModule = setup_test_database
tearDownModule = teardown_test_database

skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal


class BudgetViewTestCase(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Constructora")
        self.admin = make_user(self.org, username="admin", role=Role.ADMIN)
        self.supervisor = make_user(self.org, username="super", role=Role.SUPERVISOR)
        self.agent = make_user(self.org, username="agente", role=Role.FIELD_AGENT)
        self.viewer = make_user(self.org, username="obs", role=Role.VIEWER)
        self.project = Project.objects.create(
            org=self.org, name="Obra Norte", created_by=self.admin
        )
        self.ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        self.base = f"/plataforma/proyectos/{self.project.pk}/presupuestos/"

    def as_user(self, user: AppUser) -> None:
        self.client.force_login(user)

    def budget_v1_v2(self) -> Budget:
        """Contractual v1 (con AIU) y vigente v2 (ver `samples.py`)."""
        budget = make_budget(self.project, self.admin)
        apply_rows(self.ctx, budget, V1_ROWS, label="Contrato", charges=AIU)
        apply_rows(self.ctx, budget, V2_ROWS, label="Otrosí 1")
        budget.refresh_from_db()
        return budget

    def budget_urls(self, budget: Budget) -> list[str]:
        """Todas las URL de presupuestos (las tareas siguientes añaden las suyas)."""
        return [self.base, f"{self.base}{budget.pk}/"]

    def make_agent_see_the_project(self) -> None:
        """El agente ve el proyecto (es responsable de una tarea del cronograma)."""
        staged = stage_import(self.ctx, self.project, file_name="v1.xml", data=schedule_v1())
        apply_import(self.ctx, staged.pk)
        task = Task.objects.filter(project=self.project).order_by("sort_order").first()
        TaskResponsible.objects.create(
            org=self.org, task=task, user=self.agent, assigned_by=self.admin
        )


@skip_without_db
class AccessTests(BudgetViewTestCase):
    def test_field_agent_gets_404_everywhere_and_has_no_tab(self) -> None:
        self.make_agent_see_the_project()
        budget = self.budget_v1_v2()
        self.as_user(self.agent)
        schedule = self.client.get(f"/plataforma/proyectos/{self.project.pk}/")
        self.assertEqual(schedule.status_code, 200)
        self.assertNotContains(schedule, "/presupuestos/")
        for url in self.budget_urls(budget):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(self.base, {"name": "X"}).status_code, 404)

    def test_the_other_roles_see_the_tab(self) -> None:
        for user in (self.admin, self.supervisor, self.viewer):
            self.as_user(user)
            with self.subTest(user=user.username):
                page = self.client.get(f"/plataforma/proyectos/{self.project.pk}/")
                self.assertContains(page, self.base)

    def test_budget_of_another_project_or_organization_is_404(self) -> None:
        budget = self.budget_v1_v2()
        other = Project.objects.create(org=self.org, name="Otra obra", created_by=self.admin)
        self.as_user(self.admin)
        url = f"/plataforma/proyectos/{other.pk}/presupuestos/{budget.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}{uuid4()}/").status_code, 404)
        outsider_org = make_org("Otra")
        self.as_user(make_user(outsider_org, username="otro", role=Role.ADMIN))
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}{budget.pk}/").status_code, 404)

    def test_requires_login(self) -> None:
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/plataforma/ingresar/", response["Location"])


@skip_without_db
class BudgetListTests(BudgetViewTestCase):
    def test_lists_budgets_with_totals_and_the_project_total(self) -> None:
        self.budget_v1_v2()
        self.as_user(self.viewer)
        response = self.client.get(self.base)
        for text in (
            "Contrato de obra",
            "11.042.735,00",
            "10.510.555,00",
            "-532.180,00",
            "-4,82 %",
            "Total del proyecto",
            "v2",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, 'name="name"')

    def test_admin_creates_a_budget_and_it_is_audited(self) -> None:
        self.as_user(self.admin)
        response = self.client.post(
            self.base, {"name": "Interventoría", "code": "I-01", "description": ""}
        )
        budget = Budget.objects.get(name="Interventoría")
        self.assertRedirects(response, f"{self.base}{budget.pk}/")
        self.assertEqual(
            (budget.project_id, budget.org_id, budget.created_by_id),
            (self.project.pk, self.org.pk, self.admin.pk),
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="budgets.created", target=f"budget:{budget.pk}")
        )

    def test_duplicate_name_in_the_project_shows_an_error(self) -> None:
        make_budget(self.project, self.admin, name="Contrato")
        self.as_user(self.admin)
        response = self.client.post(self.base, {"name": "contrato", "code": "", "description": ""})
        self.assertContains(response, "Ya existe un presupuesto con ese nombre")

    def test_supervisor_cannot_create(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(self.base, {"name": "X", "code": "", "description": ""})
        self.assertEqual(response.status_code, 403)


@skip_without_db
class ComparisonPageTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = self.budget_v1_v2()
        self.url = f"{self.base}{self.budget.pk}/"

    def test_rows_labels_totals_and_folding(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.url)
        for text in (
            "Formaleta",
            "No previsto",
            "Eliminado",
            "Mayor cantidad",
            "Menor cantidad",
            "Cambio de precio",
            "11.042.735,00",
            "10.510.555,00",
            "IVA sobre utilidad",
            "19 % sobre línea 3 (Utilidad)",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(response, "data-toggle", count=2)
        self.assertContains(response, "data-parent=", count=6)

    def test_filters(self) -> None:
        self.as_user(self.viewer)
        changes = self.client.get(self.url, {"cambios": "1"})
        self.assertContains(changes, "Formaleta")
        self.assertNotContains(changes, "Curado")
        search = self.client.get(self.url, {"q": "acero"})
        self.assertContains(search, "Acero de refuerzo")
        self.assertNotContains(search, "Formaleta")
        chapter = BudgetItem.objects.get(budget=self.budget, code="1")
        by_chapter = self.client.get(self.url, {"capitulo": str(chapter.pk)})
        self.assertContains(by_chapter, "Cerramiento provisional")
        self.assertNotContains(by_chapter, "Formaleta")
        self.assertEqual(self.client.get(self.url, {"capitulo": "basura"}).status_code, 200)

    def test_empty_budget(self) -> None:
        empty = make_budget(self.project, self.admin, name="Vacío")
        self.as_user(self.viewer)
        response = self.client.get(f"{self.base}{empty.pk}/")
        self.assertContains(response, "Todavía no hay versiones aplicadas")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: FAIL (404 en todas las URL de presupuestos: la ruta no existe).

- [ ] **Step 3: Acceso**

`server/visitacampo/budgets/access.py`:

```python
"""Quién ve presupuestos y cómo se resuelven el proyecto y el presupuesto de la URL."""

from __future__ import annotations

from uuid import UUID

from django.http import Http404

from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, Project
from visitacampo.core.policies import can_view_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.access import get_visible_project


def get_budget_project(ctx: RequestContext, project_id: UUID) -> Project:
    """El proyecto, o 404: si el usuario no ve presupuestos (agente de campo),
    **antes** de mirar el proyecto, aunque lo vea por ser responsable de una
    tarea; si es de otra organización o no lo ve (RD-11)."""
    if not can_view_budgets(ctx, ctx.org_id):
        raise Http404
    return get_visible_project(ctx, project_id)


def get_budget(ctx: RequestContext, project: Project, budget_id: UUID) -> Budget:
    """El presupuesto del proyecto, o 404."""
    try:
        return scoped_qs(ctx, Budget).get(pk=budget_id, project=project)
    except Budget.DoesNotExist as exc:
        raise Http404 from exc
```

- [ ] **Step 4: Pestaña en la cabecera del proyecto**

En `server/visitacampo/projects/views.py`, en el import de `visitacampo.core.policies` añadir `can_view_budgets` (tras `can_manage_projects`), y en `_project_context` añadir al diccionario:

```python
        # Pestaña "Presupuestos" de la cabecera (costos, pieza 3).
        "can_view_budgets": can_view_budgets(ctx, ctx.org_id),
```

En `server/visitacampo/core/templates/suno/projects/_header.html`, tras la línea del enlace **Historial**, añadir:

```django
      {% if can_view_budgets %}<a href="{% url 'budgets:list' project.pk %}" {% if tab == "budgets" %}aria-current="page"{% endif %}>Presupuestos</a>{% endif %}
```

- [ ] **Step 5: URL**

`server/visitacampo/budgets/urls.py`:

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/presupuestos/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "budgets"

urlpatterns = [
    path("", views.budget_list, name="list"),
    path("<uuid:budget_id>/", views.budget_detail, name="detail"),
]
```

En `server/config/urls.py`, tras `path("plataforma/proyectos/", include("visitacampo.projects.urls")),` añadir:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/presupuestos/",
        include("visitacampo.budgets.urls"),
    ),
```

- [ ] **Step 6: Vistas de lista y comparación**

`server/visitacampo/budgets/views.py`:

```python
"""Páginas de presupuestos en /plataforma/proyectos/<id>/presupuestos/ (costos, pieza 3).

Mismo esquema que `projects/views.py`, del que se reutilizan la resolución del
contexto, el render y la cabecera del proyecto (nombres privados importados a
propósito: una sola cabecera y un solo contexto de proyecto). Quien no puede
ver presupuestos (el agente de campo) recibe 404 en todas estas URL antes de
mirar nada más (`access.get_budget_project`); una acción para la que falta el
rol responde 403.
"""

from __future__ import annotations

from uuid import UUID

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, Project
from visitacampo.core.policies import can_export_budgets, can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_budget, get_budget_project
from .comparison import budget_summaries, build_comparison, filter_rows

DUPLICATE_NAME = "Ya existe un presupuesto con ese nombre en este proyecto."


class BudgetForm(forms.Form):
    name = forms.CharField(max_length=200, label="Nombre")
    code = forms.CharField(max_length=50, required=False, label="Código")
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Descripción"
    )


def _budget_context(
    ctx: RequestContext, project: Project, budget: Budget | None = None
) -> dict[str, object]:
    return {
        **_project_context(ctx, project),
        "budget": budget,
        "can_manage_budgets": can_manage_budgets(ctx, ctx.org_id),
        "can_export_budgets": can_export_budgets(ctx, ctx.org_id),
    }


def _require_manage(ctx: RequestContext) -> None:
    if not can_manage_budgets(ctx, ctx.org_id):
        raise PermissionDenied


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def budget_list(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    form = BudgetForm(request.POST or None)
    if request.method == "POST":
        _require_manage(ctx)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            budgets = scoped_qs(ctx, Budget).filter(project=project)
            if budgets.filter(name__iexact=name).exists():
                form.add_error("name", DUPLICATE_NAME)
            else:
                try:
                    with transaction.atomic():
                        budget = scoped_qs(ctx, Budget).create(
                            org_id=ctx.org_id,
                            project=project,
                            name=name,
                            code=form.cleaned_data["code"].strip(),
                            description=form.cleaned_data["description"].strip(),
                            created_by_id=ctx.user_id,
                        )
                        audit.record(
                            ctx,
                            action=AuditAction.BUDGET_CREATED,
                            target=f"budget:{budget.pk}",
                            metadata={"project": str(project.pk), "name": name},
                        )
                except IntegrityError:
                    form.add_error("name", DUPLICATE_NAME)
                else:
                    return redirect("budgets:detail", project.pk, budget.pk)
    summaries, totals = budget_summaries(ctx, project)
    return _render(
        request,
        ctx,
        "suno/budgets/list.html",
        {**_budget_context(ctx, project), "summaries": summaries, "totals": totals, "form": form},
    )


@login_required(login_url="platform_login")
def budget_detail(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    """Comparación contractual contra vigente (la página principal del presupuesto)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    data = build_comparison(ctx, budget)
    chapter = _parse_uuid(request.GET.get("capitulo"))
    query = (request.GET.get("q") or "").strip()[:100]
    changes_only = request.GET.get("cambios") == "1"
    rows = filter_rows(data.rows, chapter_id=chapter, changes_only=changes_only, query=query)
    return _render(
        request,
        ctx,
        "suno/budgets/compare.html",
        {
            **_budget_context(ctx, project, budget),
            "data": data,
            "rows": rows,
            "filters": {"capitulo": str(chapter or ""), "cambios": changes_only, "q": query},
        },
    )
```

- [ ] **Step 7: Plantillas**

`server/visitacampo/core/templates/suno/budgets/list.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Presupuestos · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}

<section class="pj-panel">
  <h2>Presupuestos del proyecto</h2>
  <table class="pj-table">
    <thead><tr><th>Presupuesto</th><th class="num">Total contractual</th><th class="num">Total vigente</th><th class="num">Diferencia</th><th class="num">Dif. %</th><th class="num">Versión vigente</th></tr></thead>
    <tbody>
    {% for s in summaries %}
      <tr>
        <td class="pj-name"><a href="{% url 'budgets:detail' project.pk s.budget.pk %}"><strong>{{ s.budget.name }}</strong></a>{% if s.budget.code %} <small class="pj-muted">{{ s.budget.code }}</small>{% endif %}</td>
        <td class="num">{{ s.contractual_total|floatformat:"2g"|default:"—" }}</td>
        <td class="num">{{ s.current_total|floatformat:"2g"|default:"—" }}</td>
        <td class="num">{% if s.diff is not None %}{{ s.diff|floatformat:"2g" }}{% else %}—{% endif %}</td>
        <td class="num">{% if s.diff_pct is not None %}{{ s.diff_pct|floatformat:2 }} %{% else %}—{% endif %}</td>
        <td class="num">{% if s.current_number %}v{{ s.current_number }}{% else %}—{% endif %}</td>
      </tr>
    {% empty %}
      <tr><td colspan="6" class="pj-empty">Este proyecto todavía no tiene presupuestos.</td></tr>
    {% endfor %}
    </tbody>
    {% if summaries %}
    <tfoot>
      <tr class="bg-total"><td>Total del proyecto</td><td class="num">{{ totals.contractual|floatformat:"2g" }}</td><td class="num">{{ totals.current|floatformat:"2g" }}</td><td class="num">{{ totals.diff|floatformat:"2g" }}</td><td class="num">{% if totals.diff_pct is not None %}{{ totals.diff_pct|floatformat:2 }} %{% else %}—{% endif %}</td><td></td></tr>
    </tfoot>
    {% endif %}
  </table>
</section>

{% if can_manage_budgets %}
<section class="pj-panel">
  <h2>Nuevo presupuesto</h2>
  <p class="pj-muted">Un proyecto puede tener varios (p. ej. por contrato o fuente de financiación); cada uno con su versión contractual y su vigente.</p>
  <form method="post" class="pj-form">
    {% csrf_token %}
    {{ form.non_field_errors }}
    <label>Nombre {{ form.name }}</label>{{ form.name.errors }}
    <label>Código {{ form.code }}</label>
    <label>Descripción {{ form.description }}</label>
    <button class="button" type="submit">Crear presupuesto</button>
  </form>
</section>
{% endif %}
{% endblock %}
```

`server/visitacampo/core/templates/suno/budgets/_nav.html`:

```django
<section class="bg-head">
  <h2>{{ budget.name }}{% if budget.code %} <small class="pj-muted">{{ budget.code }}</small>{% endif %}</h2>
  {% if budget.description %}<p class="pj-muted">{{ budget.description }}</p>{% endif %}
  <nav class="pj-tabs" aria-label="Secciones del presupuesto">
    <a href="{% url 'budgets:list' project.pk %}">← Presupuestos</a>
    <a href="{% url 'budgets:detail' project.pk budget.pk %}" {% if sub == "compare" %}aria-current="page"{% endif %}>Comparación</a>
  </nav>
</section>
```

`server/visitacampo/core/templates/suno/budgets/compare.html`:

```django
{% extends "suno/projects/base.html" %}
{% load static %}
{% block title %}{{ budget.name }} · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="compare" %}

{% if data.current %}
<section class="metrics-strip" aria-label="Totales del presupuesto">
  <article><span>Total contractual</span><strong>{{ data.total.contractual|floatformat:"2g" }}</strong><small>Versión {{ data.contractual.number }}{% if data.contractual.label %} · {{ data.contractual.label }}{% endif %}</small></article>
  <article><span>Total vigente</span><strong>{{ data.total.current|floatformat:"2g" }}</strong><small>Versión {{ data.current.number }}{% if data.current.label %} · {{ data.current.label }}{% endif %}</small></article>
  <article><span>Diferencia</span><strong>{{ data.total.diff|floatformat:"2g" }}</strong><small>{% if data.total.diff_pct is not None %}{{ data.total.diff_pct|floatformat:2 }} % sobre el contractual{% else %}—{% endif %}</small></article>
  <article><span>Ítems con cambios</span><strong>{{ data.changed_count }}</strong><small>de {{ data.item_count }} ítems</small></article>
</section>

<section class="pj-panel">
  <form method="get" class="pj-filters">
    <label>Capítulo <select name="capitulo"><option value="">Todos</option>{% for chapter in data.chapters %}<option value="{{ chapter.item_id }}" {% if filters.capitulo == chapter.item_id|stringformat:"s" %}selected{% endif %}>{{ chapter.code }} {{ chapter.description }}</option>{% endfor %}</select></label>
    <label>Buscar <input type="search" name="q" value="{{ filters.q }}" placeholder="Código o descripción"></label>
    <label><span><input type="checkbox" name="cambios" value="1" {% if filters.cambios %}checked{% endif %}> Solo con cambios</span></label>
    <button class="button" type="submit">Aplicar</button>
  </form>
</section>

<section class="pj-panel">
  <p class="pj-tree-actions"><button type="button" class="text-link" data-expand-all>Expandir todo</button> · <button type="button" class="text-link" data-collapse-all>Contraer todo</button></p>
  <div class="pj-scroll">
  <table class="pj-table bg-compare" data-wbs>
    <thead>
      <tr>
        <th>Código</th><th>Descripción</th><th>Und</th>
        <th class="num">Cantidad<br><small>contractual</small></th><th class="num">Vr. unitario<br><small>contractual</small></th><th class="num">Total<br><small>contractual</small></th>
        <th class="num">Cantidad<br><small>vigente</small></th><th class="num">Vr. unitario<br><small>vigente</small></th><th class="num">Total<br><small>vigente</small></th>
        <th class="num">Diferencia</th><th class="num">Dif. %</th><th>Estado</th>
      </tr>
    </thead>
    <tbody>
    {% for r in rows %}
      <tr data-id="{{ r.item_id }}"{% if r.parent_id %} data-parent="{{ r.parent_id }}"{% endif %} class="{% if r.kind == 'chapter' %}{% if r.depth == 0 %}pj-stage{% else %}pj-summary{% endif %}{% endif %}{% if r.current is None %} bg-removed{% endif %}">
        <td>{{ r.code }}</td>
        <td class="pj-name" style="padding-left: {{ r.depth|add:1 }}em">{% if r.kind == "chapter" %}<button type="button" class="pj-toggle" data-toggle aria-expanded="true" aria-label="Plegar {{ r.description }}">▾</button> {% endif %}{{ r.description|default:"—" }}</td>
        <td>{{ r.unit }}</td>
        {% with c=r.contractual v=r.current %}
        <td class="num">{% if c %}{{ c.quantity|floatformat:"-4g" }}{% endif %}</td>
        <td class="num">{% if c %}{{ c.unit_price|floatformat:"2g" }}{% endif %}</td>
        <td class="num">{% if c %}{{ c.total|floatformat:"2g" }}{% endif %}</td>
        <td class="num">{% if v %}{{ v.quantity|floatformat:"-4g" }}{% elif r.kind == "item" %}0{% endif %}</td>
        <td class="num">{% if v %}{{ v.unit_price|floatformat:"2g" }}{% endif %}</td>
        <td class="num">{% if v %}{{ v.total|floatformat:"2g" }}{% else %}0,00{% endif %}</td>
        {% endwith %}
        <td class="num{% if r.diff > 0 %} bg-up{% elif r.diff < 0 %} bg-down{% endif %}">{{ r.diff|floatformat:"2g" }}</td>
        <td class="num">{% if r.diff_pct is not None %}{{ r.diff_pct|floatformat:2 }} %{% else %}—{% endif %}</td>
        <td class="pj-wrap">{% for label, css in r.badges %}<span class="bg-label bg-label-{{ css }}">{{ label }}</span>{% endfor %}</td>
      </tr>
    {% empty %}
      <tr><td colspan="12" class="pj-empty">Ninguna fila coincide con los filtros.</td></tr>
    {% endfor %}
    </tbody>
    <tfoot>
    {% for f in data.footer %}
      <tr{% if f.strong %} class="bg-total"{% endif %}>
        <td colspan="5">{{ f.label }}{% if f.note %} <small class="pj-muted">{{ f.note }}</small>{% endif %}</td>
        <td class="num">{{ f.contractual|floatformat:"2g"|default:"—" }}</td>
        <td colspan="2"></td>
        <td class="num">{{ f.current|floatformat:"2g"|default:"—" }}</td>
        <td class="num">{{ f.diff|floatformat:"2g" }}</td>
        <td class="num">{% if f.diff_pct is not None %}{{ f.diff_pct|floatformat:2 }} %{% else %}—{% endif %}</td>
        <td></td>
      </tr>
    {% endfor %}
    </tfoot>
  </table>
  </div>
</section>
{% else %}
<section class="pj-panel"><p class="pj-empty">Todavía no hay versiones aplicadas de este presupuesto.</p></section>
{% endif %}
{% endblock %}
{% block scripts %}<script src="{% static 'suno/js/schedule.js' %}" defer></script>{% endblock %}
```

(`schedule.js` ya pliega cualquier tabla `[data-wbs]` cuyas filas lleven `data-id`/`data-parent` y un botón `[data-toggle]`; no hace falta JavaScript nuevo.)

- [ ] **Step 8: Estilos**

Al final de `server/visitacampo/core/static/suno/css/projects.css` añadir:

```css
/* Presupuestos (costos, pieza 3). */
.bg-head { margin: 24px 0 0; }
.bg-head h2 { margin: 0 0 4px; font-size: 22px; }
.bg-compare thead th:nth-child(-n+2), .bg-compare tbody td:nth-child(-n+2) { position: sticky; z-index: 1; background: var(--suno-white); }
.bg-compare thead th:nth-child(-n+2) { z-index: 2; background: var(--suno-paper); }
.bg-compare tbody tr.pj-stage td:nth-child(-n+2) { background: var(--suno-paper); }
.bg-compare thead th:nth-child(1), .bg-compare tbody td:nth-child(1) { left: 0; width: 72px; min-width: 72px; }
.bg-compare thead th:nth-child(2), .bg-compare tbody td:nth-child(2) { left: 72px; box-shadow: 1px 0 0 var(--suno-line); }
.bg-compare td.pj-name { min-width: 240px; max-width: 360px; }
@media (max-width: 767px) { .bg-compare td.pj-name { min-width: 160px; max-width: 220px; } }
.bg-compare tfoot td { background: var(--suno-paper); font-weight: 600; }
.pj-table tr.bg-total td { font-weight: 700; border-top: 2px solid var(--suno-ink); }
.bg-removed td:nth-child(2) { color: var(--suno-muted); text-decoration: line-through; }
.bg-up { color: var(--suno-red); }
.bg-down { color: var(--suno-green); }
.bg-label { display: inline-block; margin: 0 4px 2px 0; padding: 1px 6px; border-radius: var(--suno-radius); background: var(--suno-muted); color: var(--suno-white); font-size: 11px; }
.bg-label-more { background: var(--suno-amber); color: var(--suno-ink); }
.bg-label-less { background: var(--suno-topography); color: var(--suno-ink); }
.bg-label-price { background: var(--suno-ink); }
.bg-label-new { background: var(--suno-green); }
.bg-label-removed { background: var(--suno-red); }
.bg-label-contractual { background: var(--suno-ink); }
.bg-sheet td { max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
.bg-charges { max-width: 760px; }
.bg-charges input[type="text"] { width: 100%; }
```

- [ ] **Step 9: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py visitacampo/projects/tests/test_views.py`
Expected: todo pasa (las de proyectos siguen verdes con la pestaña nueva). Si un monto no aparece como `11.042.735,00`, revisa que `LANGUAGE_CODE = "es"` siga en `config/settings/base.py`: el agrupado de `floatformat:"2g"` sale del locale.

- [ ] **Step 10: Commit**

```bash
git add server/config/urls.py server/visitacampo/budgets server/visitacampo/projects/views.py server/visitacampo/core/templates server/visitacampo/core/static/suno/css/projects.css
git commit -m "feat(budgets): budgets tab, budget list and contractual vs current page"
```

---

### Task 12: Web — subir, mapear, vista previa con cargos, aplicar y descartar

**Files:**
- Modify: `server/visitacampo/budgets/views.py`, `server/visitacampo/budgets/urls.py`
- Create: `server/visitacampo/core/templates/suno/budgets/import.html`, `import_mapping.html`, `import_preview.html`
- Modify: `server/visitacampo/core/templates/suno/budgets/_nav.html`, `compare.html`
- Test: `server/visitacampo/budgets/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/visitacampo/budgets/tests/test_views.py`:

- Añadir `from typing import Any` y `from django.core.files.uploadedfile import SimpleUploadedFile`.
- En el import de modelos añadir `BudgetVersion`.
- Cambiar `from .factories import apply_rows, make_budget` por `from .factories import apply_rows, make_budget, stage_rows` y `from .samples import AIU, V1_ROWS, V2_ROWS` por `from .samples import AIU, V1_ROWS, V2_ROWS, xlsx`.
- Tras `D = Decimal` añadir `XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"`.
- En `BudgetViewTestCase.budget_urls`, cambiar el `return` por:

```python
        some = uuid4()
        return [
            self.base,
            f"{self.base}{budget.pk}/",
            f"{self.base}{budget.pk}/importar/",
            f"{self.base}{budget.pk}/importar/{some}/",
            f"{self.base}{budget.pk}/importar/{some}/mapeo/",
        ]
```

Y al final del archivo:

```python
@skip_without_db
class ImportPagesTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = make_budget(self.project, self.admin)
        self.url = f"{self.base}{self.budget.pk}/importar/"

    def upload(self, rows: list[list[Any]], name: str = "v1.xlsx", **extra: str) -> Any:
        upload = SimpleUploadedFile(name, xlsx(rows), content_type=XLSX)
        return self.client.post(self.url, {"file": upload, **extra})

    def pending(self) -> BudgetVersion:
        return BudgetVersion.objects.get(budget=self.budget, status="pending")

    @staticmethod
    def mapping_post(**changes: str) -> dict[str, str]:
        data = {
            "sheet": "Presupuesto",
            "first_row": "2",
            "col_code": "A",
            "col_description": "B",
            "col_unit": "C",
            "col_quantity": "D",
            "col_unit_price": "E",
            "col_total": "F",
        }
        return {**data, **changes}

    @staticmethod
    def charges_post(action: str = "confirm", label: str = "") -> dict[str, str]:
        """El formulario de la vista previa con AIU 10 / 5 / 5 / IVA 19 % sobre la utilidad."""
        data = {"action": action, "label": label, "charge_count": "5"}
        lines = [
            ("Administración", "10", "direct"),
            ("Imprevistos", "5", "direct"),
            ("Utilidad", "5", "direct"),
            ("IVA sobre utilidad", "19", "line:3"),
        ]
        for index, (name, pct, base) in enumerate(lines, start=1):
            data[f"charge_{index}_name"] = name
            data[f"charge_{index}_pct"] = pct
            data[f"charge_{index}_base"] = base
        return data

    def test_first_upload_goes_through_mapping_and_preview_and_applies(self) -> None:
        self.as_user(self.admin)
        response = self.upload(V1_ROWS)
        version = self.pending()
        mapping_url = f"{self.url}{version.pk}/mapeo/"
        self.assertRedirects(response, mapping_url)

        page = self.client.get(mapping_url)
        self.assertContains(page, "Vr. unitario")
        fields = {f["name"]: f["value"] for f in page.context["fields"]}
        self.assertEqual(
            fields,
            {
                "code": "A",
                "description": "B",
                "unit": "C",
                "quantity": "D",
                "unit_price": "E",
                "total": "F",
            },
        )
        self.assertEqual(page.context["first_row"], 2)

        preview_url = f"{self.url}{version.pk}/"
        self.assertRedirects(self.client.post(mapping_url, self.mapping_post()), preview_url)
        preview = self.client.get(preview_url)
        self.assertContains(preview, "5 no previstos")
        self.assertContains(preview, "9.130.000,00")
        self.assertContains(preview, 'name="charge_4_base"')

        response = self.client.post(preview_url, self.charges_post(label="Contrato"))
        self.assertRedirects(response, f"{self.base}{self.budget.pk}/")
        version.refresh_from_db()
        self.assertEqual(
            (version.number, version.label, version.total), (1, "Contrato", D("11042735.00"))
        )
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, version.pk)
        event = AuditEvent.objects.get(action="budgets.version_applied")
        self.assertEqual(event.metadata["version"], 1)

    def test_second_upload_reuses_the_saved_mapping(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS, charges=AIU)
        self.as_user(self.admin)
        response = self.upload(V2_ROWS, name="v2.xlsx")
        version = self.pending()
        self.assertRedirects(response, f"{self.url}{version.pk}/")
        preview = self.client.get(f"{self.url}{version.pk}/")
        for text in (
            "1 no previstos",
            "1 eliminados",
            "1 con mayor cantidad",
            "1 con menor cantidad",
            "1 con cambio de precio",
            "10.510.555,00",
        ):
            with self.subTest(text=text):
                self.assertContains(preview, text)

    def test_asking_to_change_the_mapping(self) -> None:
        apply_rows(self.ctx, self.budget, V1_ROWS)
        self.as_user(self.admin)
        response = self.upload(V2_ROWS, cambiar_mapeo="1")
        self.assertRedirects(response, f"{self.url}{self.pending().pk}/mapeo/")

    def test_mapping_errors_are_shown_and_nothing_is_saved(self) -> None:
        self.as_user(self.admin)
        self.upload(V1_ROWS)
        mapping_url = f"{self.url}{self.pending().pk}/mapeo/"
        response = self.client.post(mapping_url, self.mapping_post(col_description="A"))
        self.assertContains(response, "Una misma columna no puede tener dos significados")
        response = self.client.post(mapping_url, self.mapping_post(first_row="1"))
        self.assertContains(response, "Fila 1 (código Ítem)")
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.column_mapping, {})

    def test_xlsm_is_rejected(self) -> None:
        self.as_user(self.admin)
        response = self.upload(V1_ROWS, name="v1.xlsm")
        self.assertContains(response, "macros")
        self.assertFalse(BudgetVersion.objects.exists())

    def test_save_shows_amounts_bad_charges_show_an_error_and_discard(self) -> None:
        self.as_user(self.admin)
        self.upload(V1_ROWS)
        version = self.pending()
        self.client.post(f"{self.url}{version.pk}/mapeo/", self.mapping_post())
        preview_url = f"{self.url}{version.pk}/"

        saved = self.client.post(
            preview_url, self.charges_post(action="save", label="Contrato"), follow=True
        )
        self.assertContains(saved, "913.000,00")
        version.refresh_from_db()
        self.assertEqual(version.label, "Contrato")

        bad = {**self.charges_post(action="save"), "charge_4_base": "line:4"}
        self.assertContains(
            self.client.post(preview_url, bad, follow=True), "la base debe ser"
        )

        response = self.client.post(preview_url, {"action": "discard"})
        self.assertRedirects(response, f"{self.base}{self.budget.pk}/")
        version.refresh_from_db()
        self.assertEqual(version.status, "discarded")
        self.assertTrue(AuditEvent.objects.filter(action="budgets.version_discarded"))

    def test_stale_preview_is_rejected_with_a_message(self) -> None:
        old = stage_rows(self.ctx, self.budget, V1_ROWS)
        apply_rows(self.ctx, self.budget, V2_ROWS)
        self.as_user(self.admin)
        response = self.client.post(f"{self.url}{old.pk}/", self.charges_post(), follow=True)
        self.assertContains(response, "Vuelve a subir el archivo")
        old.refresh_from_db()
        self.assertEqual(old.status, "pending")

    def test_supervisor_cannot_import(self) -> None:
        self.as_user(self.supervisor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.upload(V1_ROWS).status_code, 403)
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: FAIL (`ImportPagesTests`: las URL de importación no existen).

- [ ] **Step 3: URL**

En `server/visitacampo/budgets/urls.py`, añadir a `urlpatterns`:

```python
    path("<uuid:budget_id>/importar/", views.import_upload, name="import"),
    path(
        "<uuid:budget_id>/importar/<uuid:version_id>/",
        views.import_preview,
        name="import_preview",
    ),
    path(
        "<uuid:budget_id>/importar/<uuid:version_id>/mapeo/",
        views.import_mapping,
        name="import_mapping",
    ),
```

- [ ] **Step 4: Vistas**

En `server/visitacampo/budgets/views.py`, reemplazar el bloque de imports (desde `from uuid import UUID` hasta `from .comparison import …`) por:

```python
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from openpyxl.utils import get_column_letter

from visitacampo.core import audit
from visitacampo.core.audit import AuditAction
from visitacampo.core.context import RequestContext
from visitacampo.core.models import Budget, BudgetVersion, BudgetVersionStatus, Project
from visitacampo.core.policies import can_export_budgets, can_manage_budgets
from visitacampo.core.scoping import scoped_qs
from visitacampo.projects.views import _ctx, _parse_uuid, _project_context, _render

from .access import get_budget, get_budget_project
from .comparison import budget_summaries, build_comparison, filter_rows
from .excel import MAX_BYTES, ExcelRejected, preview_sheet
from .importer import (
    ImportRejected,
    apply_version,
    charges_of,
    create_upload,
    discard_version,
    prepare_version,
    update_pending,
)
from .mapping import (
    FIELD_LABELS,
    FIELDS,
    MAX_COLUMNS,
    REQUIRED_FIELDS,
    ColumnMapping,
    MappingError,
    suggest_mapping,
)
from .totals import (
    BASE_DIRECT,
    BASE_SUBTOTAL,
    MAX_CHARGES,
    ChargeError,
    ChargeSpec,
    base_line_order,
    compute_charges,
    line_base,
    parse_number,
    pct_text,
    validate_charges,
)
```

Tras `DUPLICATE_NAME = …` añadir `MIN_PREVIEW_COLUMNS = 6`, y al final del archivo:

```python
def _int(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 0


def _saved_mapping(budget: Budget) -> ColumnMapping | None:
    if not budget.column_mapping:
        return None
    try:
        return ColumnMapping.from_json(budget.column_mapping)
    except MappingError:
        return None


def _get_version(
    ctx: RequestContext,
    budget: Budget,
    version_id: UUID,
    *,
    status: str | None = None,
    with_file: bool = False,
) -> BudgetVersion:
    """La versión del presupuesto, o 404. Sin el archivo salvo que se pida."""
    versions = scoped_qs(ctx, BudgetVersion).filter(budget=budget)
    if status is not None:
        versions = versions.filter(status=status)
    if not with_file:
        versions = versions.defer("source_file")
    try:
        return versions.get(pk=version_id)
    except BudgetVersion.DoesNotExist as exc:
        raise Http404 from exc


def _display(value: Any) -> str:
    """Una celda de la hoja, para la tabla de la página de mapeo."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)[:60]


def _last_column(row: Sequence[Any]) -> int:
    for index in range(len(row) - 1, -1, -1):
        if _display(row[index]).strip():
            return index + 1
    return 0


def _charges_from_post(data: QueryDict) -> list[ChargeSpec]:
    """Los cargos del formulario de la vista previa, renumerados 1..n.

    Una línea sin nombre se quita. Una base `line:k` se traduce al nuevo número
    de la línea k; si esa línea se quitó o no es anterior, queda inválida y
    `validate_charges` lo dice.
    """
    count = min(_int(data.get("charge_count")), MAX_CHARGES + 1)
    specs: list[ChargeSpec] = []
    renumber: dict[int, int] = {}
    for index in range(1, count + 1):
        name = (data.get(f"charge_{index}_name") or "").strip()
        if not name:
            continue
        try:
            pct = parse_number(data.get(f"charge_{index}_pct")) or Decimal(0)
        except ValueError:
            raise ImportRejected(f"Línea {index}: el porcentaje no es un número.") from None
        order = len(specs) + 1
        base = data.get(f"charge_{index}_base") or BASE_DIRECT
        ref = base_line_order(base)
        if ref is not None:
            base = line_base(renumber.get(ref, order))
        renumber[index] = order
        specs.append(ChargeSpec(order=order, name=name, pct=pct, base=base))
    try:
        validate_charges(specs)
    except ChargeError as exc:
        raise ImportRejected(str(exc)) from exc
    return specs


def _charge_form_rows(
    charges: Sequence[ChargeSpec], amounts: Mapping[int, Decimal]
) -> list[dict[str, Any]]:
    """Filas del formulario de cargos: las existentes y una vacía para añadir."""
    names = {c.order: c.name for c in charges}
    specs: list[ChargeSpec | None] = [*charges]
    if len(specs) < MAX_CHARGES:
        specs.append(None)
    rows = []
    for index, spec in enumerate(specs, start=1):
        options = [
            (BASE_DIRECT, "Costo directo"),
            (BASE_SUBTOTAL, "Subtotal (costo directo + líneas anteriores)"),
            *(
                (line_base(k), f"Línea {k} ({names[k]})" if k in names else f"Línea {k}")
                for k in range(1, index)
            ),
        ]
        rows.append(
            {
                "index": index,
                "name": spec.name if spec else "",
                "pct": pct_text(spec.pct) if spec else "",
                "base": spec.base if spec else BASE_DIRECT,
                "options": options,
                "amount": amounts.get(index),
            }
        )
    return rows


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_upload(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    saved = _saved_mapping(budget)
    error = ""
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            error = "Elige un archivo .xlsx."
        elif upload.size is not None and upload.size > MAX_BYTES:
            error = "El archivo supera 20 MB."
        else:
            # Con mapeo guardado (y sin pedir cambiarlo) se va directo a la vista previa.
            mapping = None if request.POST.get("cambiar_mapeo") == "1" else saved
            try:
                with transaction.atomic():
                    version = create_upload(
                        ctx,
                        budget,
                        file_name=upload.name or "presupuesto.xlsx",
                        data=upload.read(),
                    )
                    if mapping is not None:
                        prepare_version(ctx, version.pk, mapping)
            except ImportRejected as exc:
                error = str(exc)
            else:
                target = "budgets:import_preview" if mapping else "budgets:import_mapping"
                return redirect(target, project.pk, budget.pk, version.pk)
    return _render(
        request,
        ctx,
        "suno/budgets/import.html",
        {**_budget_context(ctx, project, budget), "saved_mapping": saved, "error": error},
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_mapping(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    version = _get_version(ctx, budget, version_id, with_file=True)
    if version.status != BudgetVersionStatus.PENDING:
        messages.info(request, "Esta versión ya fue aplicada o descartada.")
        return redirect("budgets:detail", project.pk, budget.pk)
    saved = _saved_mapping(budget)
    wanted = (
        request.POST.get("sheet") or request.GET.get("hoja") or (saved.sheet if saved else None)
    )
    try:
        preview = preview_sheet(bytes(version.source_file), wanted)
    except ExcelRejected as exc:
        messages.error(request, str(exc))
        return redirect("budgets:import", project.pk, budget.pk)
    if saved is not None and saved.sheet == preview.sheet:
        first_row, columns = saved.first_row, dict(saved.columns)
    else:
        suggestion = suggest_mapping(preview.rows)
        first_row, columns = suggestion.first_row, suggestion.columns
    error = ""
    if request.method == "POST":
        first_row = _int(request.POST.get("first_row"))
        columns = {field: request.POST.get(f"col_{field}") or None for field in FIELDS}
        try:
            mapping = ColumnMapping.from_json(
                {"sheet": preview.sheet, "first_row": first_row, "columns": columns}
            )
            prepare_version(ctx, version.pk, mapping)
        except (MappingError, ImportRejected) as exc:
            error = str(exc)
        else:
            return redirect("budgets:import_preview", project.pk, budget.pk, version.pk)
    width = min(
        MAX_COLUMNS, max([MIN_PREVIEW_COLUMNS, *(_last_column(row) for row in preview.rows)])
    )
    return _render(
        request,
        ctx,
        "suno/budgets/import_mapping.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "sheet_names": preview.sheet_names,
            "sheet": preview.sheet,
            "letters": [get_column_letter(i) for i in range(1, width + 1)],
            "preview_rows": [
                (number, [_display(value) for value in row[:width]])
                for number, row in enumerate(preview.rows, start=1)
            ],
            "fields": [
                {
                    "name": field,
                    "label": FIELD_LABELS[field],
                    "required": field in REQUIRED_FIELDS,
                    "value": columns.get(field),
                }
                for field in FIELDS
            ],
            "first_row": first_row,
            "error": error,
        },
    )


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def import_preview(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    _require_manage(ctx)
    version = _get_version(ctx, budget, version_id)
    pending = version.status == BudgetVersionStatus.PENDING
    if request.method == "POST":
        if not pending:
            messages.info(request, "Esta versión ya fue aplicada o descartada.")
            return redirect("budgets:detail", project.pk, budget.pk)
        action = request.POST.get("action")
        try:
            if action == "discard":
                with transaction.atomic():
                    discard_version(ctx, version.pk)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_VERSION_DISCARDED,
                        target=f"budget:{budget.pk}",
                        metadata={"file": version.file_name},
                    )
                messages.info(request, "Versión descartada.")
                return redirect("budgets:detail", project.pk, budget.pk)
            label = request.POST.get("label") or ""
            charges = _charges_from_post(request.POST)
            if action == "confirm":
                with transaction.atomic():
                    update_pending(ctx, version.pk, label=label, charges=charges)
                    applied = apply_version(ctx, version.pk)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_VERSION_APPLIED,
                        target=f"budget:{budget.pk}",
                        metadata={
                            "version": applied.number,
                            "file": applied.file_name,
                            "label": applied.label,
                            "total": str(applied.total),
                        },
                    )
                messages.success(request, f"Versión {applied.number} aplicada.")
                return redirect("budgets:detail", project.pk, budget.pk)
            update_pending(ctx, version.pk, label=label, charges=charges)
            messages.success(request, "Etiqueta y cargos guardados.")
        except ImportRejected as exc:
            messages.error(request, str(exc))
        return redirect("budgets:import_preview", project.pk, budget.pk, version.pk)

    summary = version.summary
    if pending and "mapping" not in summary:
        return redirect("budgets:import_mapping", project.pk, budget.pk, version.pk)
    charges = charges_of(ctx, version)
    try:
        result = compute_charges(Decimal(summary.get("direct_cost") or "0"), charges)
    except ChargeError:
        result = None
    amounts = {line.order: line.amount for line in result.lines} if result else {}
    return _render(
        request,
        ctx,
        "suno/budgets/import_preview.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "s": summary,
            "pending": pending,
            "result": result,
            "charge_rows": _charge_form_rows(charges, amounts),
        },
    )
```

- [ ] **Step 5: Plantillas**

`server/visitacampo/core/templates/suno/budgets/import.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Importar · {{ budget.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Importar una versión del presupuesto</h2>
  <p>Sube el Excel del presupuesto tal como lo tienes: <strong>.xlsx</strong>, máximo 20 MB y 20.000 filas. Antes de aplicar verás qué cambia frente a la versión vigente.</p>
  <p class="pj-muted">Consejo: formatea la columna de códigos como texto, para que "1.10" no se lea como "1.1".</p>
  <form method="post" enctype="multipart/form-data" class="pj-form">
    {% csrf_token %}
    <label>Archivo .xlsx <input type="file" name="file" accept=".xlsx" required></label>
    {% if saved_mapping %}
    <p class="pj-muted">Se usará el mapeo guardado: hoja «{{ saved_mapping.sheet }}», datos desde la fila {{ saved_mapping.first_row }}.</p>
    <label><span><input type="checkbox" name="cambiar_mapeo" value="1"> Cambiar el mapeo de columnas</span></label>
    {% endif %}
    {% if error %}<p class="pj-late" role="alert">{{ error }}</p>{% endif %}
    <button class="button" type="submit">Continuar</button>
  </form>
</section>
{% endblock %}
```

`server/visitacampo/core/templates/suno/budgets/import_mapping.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Mapeo de columnas · {{ budget.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Mapeo de columnas de {{ version.file_name }}</h2>
  <p>Indica dónde está cada dato. SUNO sugiere las columnas por los encabezados: revísalas antes de seguir. El mapeo se guarda para las próximas versiones de este presupuesto.</p>
  {% if sheet_names|length > 1 %}
  <form method="get" class="pj-filters">
    <label>Hoja <select name="hoja">{% for name in sheet_names %}<option value="{{ name }}" {% if name == sheet %}selected{% endif %}>{{ name }}</option>{% endfor %}</select></label>
    <button class="button" type="submit">Ver hoja</button>
  </form>
  {% endif %}
  <form method="post" class="pj-filters">
    {% csrf_token %}
    <input type="hidden" name="sheet" value="{{ sheet }}">
    <label>Los datos empiezan en la fila <input type="number" name="first_row" min="1" max="20000" value="{{ first_row }}" required></label>
    {% for field in fields %}
    <label>{{ field.label }}{% if field.required %} *{% endif %} <select name="col_{{ field.name }}"><option value="">—</option>{% for letter in letters %}<option value="{{ letter }}" {% if letter == field.value %}selected{% endif %}>{{ letter }}</option>{% endfor %}</select></label>
    {% endfor %}
    {% if error %}<p class="pj-late" role="alert">{{ error }}</p>{% endif %}
    <button class="button" type="submit">Ver cambios</button>
  </form>
  <form method="post" action="{% url 'budgets:import_preview' project.pk budget.pk version.pk %}">
    {% csrf_token %}
    <button class="text-link" type="submit" name="action" value="discard">Descartar esta carga</button>
  </form>
</section>
<section class="pj-panel">
  <h2>Primeras filas de «{{ sheet }}»</h2>
  <div class="pj-scroll">
  <table class="pj-table bg-sheet">
    <thead><tr><th>Fila</th>{% for letter in letters %}<th>{{ letter }}</th>{% endfor %}</tr></thead>
    <tbody>
    {% for number, cells in preview_rows %}
      <tr><th scope="row">{{ number }}</th>{% for value in cells %}<td>{{ value }}</td>{% endfor %}</tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endblock %}
```

`server/visitacampo/core/templates/suno/budgets/import_preview.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Vista previa · {{ budget.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="import" %}
<section class="pj-panel">
  <h2>Cambios de {{ version.file_name }}</h2>
  <p>{{ s.item_count }} ítems en {{ s.chapter_count }} capítulos · hoja «{{ s.mapping.sheet }}» desde la fila {{ s.mapping.first_row }}{% if pending %} · <a href="{% url 'budgets:import_mapping' project.pk budget.pk version.pk %}">Cambiar el mapeo</a>{% endif %}</p>
  <ul>
    <li><strong>{{ s.added }} no previstos</strong> (ítems nuevos){% if s.examples.added %}: {{ s.examples.added|join:", " }}{% endif %}</li>
    <li><strong>{{ s.removed }} eliminados</strong>{% if s.examples.removed %}: {{ s.examples.removed|join:", " }}{% endif %}</li>
    <li><strong>{{ s.reappeared }} vuelven</strong> (estaban en versiones anteriores){% if s.examples.reappeared %}: {{ s.examples.reappeared|join:", " }}{% endif %}</li>
    <li><strong>{{ s.more_quantity }} con mayor cantidad</strong>{% if s.examples.more_quantity %}: {{ s.examples.more_quantity|join:", " }}{% endif %}</li>
    <li><strong>{{ s.less_quantity }} con menor cantidad</strong>{% if s.examples.less_quantity %}: {{ s.examples.less_quantity|join:", " }}{% endif %}</li>
    <li><strong>{{ s.price_changed }} con cambio de precio</strong>{% if s.examples.price_changed %}: {{ s.examples.price_changed|join:", " }}{% endif %}</li>
  </ul>
  {% for warning in s.warnings %}<p class="pj-warning">{{ warning }}</p>{% endfor %}
</section>

<section class="metrics-strip" aria-label="Totales de la versión">
  <article><span>Costo directo</span><strong>{{ s.direct_cost|floatformat:"2g" }}</strong><small>Vigente: {{ s.previous_direct_cost|floatformat:"2g"|default:"—" }}</small></article>
  <article><span>Total con cargos</span><strong>{% if result %}{{ result.total|floatformat:"2g" }}{% else %}—{% endif %}</strong><small>Vigente: {{ s.previous_total|floatformat:"2g"|default:"—" }}</small></article>
</section>

{% if pending %}
<section class="pj-panel">
  <h2>Etiqueta y cargos</h2>
  <form method="post" class="bg-charges">
    {% csrf_token %}
    <p class="pj-form"><label>Etiqueta de la versión <input type="text" name="label" maxlength="200" value="{{ version.label }}" placeholder="p. ej. Otrosí 2"></label></p>
    <input type="hidden" name="charge_count" value="{{ charge_rows|length }}">
    <table class="pj-table">
      <thead><tr><th>#</th><th>Cargo</th><th class="num">%</th><th>Base</th><th class="num">Valor</th></tr></thead>
      <tbody>
      {% for row in charge_rows %}
        <tr>
          <td>{{ row.index }}</td>
          <td><input type="text" name="charge_{{ row.index }}_name" value="{{ row.name }}" maxlength="100" aria-label="Nombre del cargo {{ row.index }}"></td>
          <td class="num"><input type="text" inputmode="decimal" name="charge_{{ row.index }}_pct" value="{{ row.pct }}" size="8" aria-label="Porcentaje del cargo {{ row.index }}"></td>
          <td><select name="charge_{{ row.index }}_base" aria-label="Base del cargo {{ row.index }}">{% for value, text in row.options %}<option value="{{ value }}" {% if value == row.base %}selected{% endif %}>{{ text }}</option>{% endfor %}</select></td>
          <td class="num">{{ row.amount|floatformat:"2g" }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    <p class="pj-muted">Deja el nombre vacío para quitar una línea. Los valores se recalculan al guardar.</p>
    <p class="pj-filters">
      <button class="button" type="submit" name="action" value="confirm">Aplicar esta versión</button>
      <button class="text-link" type="submit" name="action" value="save">Guardar etiqueta y cargos</button>
      <button class="text-link" type="submit" name="action" value="discard">Descartar</button>
    </p>
  </form>
</section>
{% else %}
<p class="pj-muted">Esta versión está {{ version.get_status_display|lower }}.</p>
{% endif %}
{% endblock %}
```

En `server/visitacampo/core/templates/suno/budgets/_nav.html`, tras el enlace **Comparación**, añadir:

```django
    {% if can_manage_budgets %}<a href="{% url 'budgets:import' project.pk budget.pk %}" {% if sub == "import" %}aria-current="page"{% endif %}>Importar Excel</a>{% endif %}
```

En `compare.html`, reemplazar el mensaje del presupuesto vacío por:

```django
<section class="pj-panel"><p class="pj-empty">Todavía no hay versiones aplicadas de este presupuesto.{% if can_manage_budgets %} <a href="{% url 'budgets:import' project.pk budget.pk %}">Importa su Excel</a>.{% endif %}</p></section>
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/budgets server/visitacampo/core/templates/suno/budgets
git commit -m "feat(budgets): upload, column mapping and preview pages with editable charges"
```

---

### Task 13: Web — historial, contenido de una versión, Excel original y "Marcar como contractual"

**Files:**
- Modify: `server/visitacampo/budgets/views.py`, `server/visitacampo/budgets/urls.py`
- Create: `server/visitacampo/core/templates/suno/budgets/history.html`, `version.html`
- Modify: `server/visitacampo/core/templates/suno/budgets/_nav.html`
- Test: `server/visitacampo/budgets/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/visitacampo/budgets/tests/test_views.py`, en `BudgetViewTestCase.budget_urls` añadir a la lista que devuelve:

```python
            f"{self.base}{budget.pk}/historial/",
            f"{self.base}{budget.pk}/versiones/{budget.contractual_version_id}/",
            f"{self.base}{budget.pk}/versiones/{budget.contractual_version_id}/excel",
```

Y al final del archivo:

```python
@skip_without_db
class HistoryAndVersionTests(BudgetViewTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.budget = self.budget_v1_v2()
        self.v1 = BudgetVersion.objects.get(budget=self.budget, number=1)
        self.v2 = BudgetVersion.objects.get(budget=self.budget, number=2)
        self.history = f"{self.base}{self.budget.pk}/historial/"

    def version_url(self, version: BudgetVersion, suffix: str = "") -> str:
        return f"{self.base}{self.budget.pk}/versiones/{version.pk}/{suffix}"

    def test_history_lists_versions_and_marks_the_contractual(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.history)
        for text in ("Otrosí 1", "contractual", "11.042.735,00", "10.510.555,00"):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertContains(response, self.version_url(self.v1, "excel"))
        self.assertNotContains(response, "Marcar como contractual")

    def test_admin_marks_another_version_as_contractual(self) -> None:
        self.as_user(self.admin)
        self.assertContains(self.client.get(self.history), "Marcar como contractual", count=1)
        response = self.client.post(
            self.history, {"action": "contractual", "version": str(self.v2.pk)}
        )
        self.assertRedirects(response, self.history)
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.contractual_version_id, self.v2.pk)
        event = AuditEvent.objects.get(action="budgets.contractual_changed")
        self.assertEqual(event.metadata, {"from": 1, "to": 2})

    def test_supervisor_cannot_mark_contractual(self) -> None:
        self.as_user(self.supervisor)
        response = self.client.post(
            self.history, {"action": "contractual", "version": str(self.v2.pk)}
        )
        self.assertEqual(response.status_code, 403)

    def test_version_page_shows_its_content_and_charges(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.version_url(self.v1))
        for text in ("Cerramiento provisional", "Administración", "913.000,00", "11.042.735,00"):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, "Formaleta")

    def test_pending_or_foreign_versions_are_404(self) -> None:
        pending = stage_rows(self.ctx, self.budget, V1_ROWS)
        other = make_budget(self.project, self.admin, name="Interventoría")
        foreign = apply_rows(self.ctx, other, V1_ROWS)
        self.as_user(self.admin)
        for version in (pending, foreign):
            for suffix in ("", "excel"):
                with self.subTest(version=version.pk, suffix=suffix):
                    url = self.version_url(version, suffix)
                    self.assertEqual(self.client.get(url).status_code, 404)

    def test_viewer_downloads_the_original_excel_and_it_is_audited(self) -> None:
        self.as_user(self.viewer)
        response = self.client.get(self.version_url(self.v1, "excel"))
        self.assertEqual(response.status_code, 200)
        stored = BudgetVersion.objects.get(pk=self.v1.pk).source_file
        self.assertEqual(response.content, bytes(stored))
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="C-01-v1.xlsx"')
        event = AuditEvent.objects.get(action="budgets.source_downloaded")
        self.assertEqual(event.metadata, {"version": 1})
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: FAIL (`HistoryAndVersionTests` y `AccessTests`: las URL nuevas dan 404 a todos).

- [ ] **Step 3: URL**

En `server/visitacampo/budgets/urls.py`, añadir a `urlpatterns`:

```python
    path("<uuid:budget_id>/historial/", views.history, name="history"),
    path(
        "<uuid:budget_id>/versiones/<uuid:version_id>/",
        views.version_detail,
        name="version",
    ),
    path(
        "<uuid:budget_id>/versiones/<uuid:version_id>/excel",
        views.version_source,
        name="version_source",
    ),
```

- [ ] **Step 4: Vistas**

En `server/visitacampo/budgets/views.py`:

- Añadir `import re` al principio de los imports.
- En el import de modelos, añadir `BudgetCharge`.
- Cambiar `from .comparison import budget_summaries, build_comparison, filter_rows` por `from .comparison import budget_summaries, build_comparison, filter_rows, version_rows`.
- En el import de `.importer`, añadir `mark_contractual`.
- En el import de `.totals`, añadir `describe_base`.

Tras `MIN_PREVIEW_COLUMNS = 6` añadir:

```python
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
```

Y al final del archivo:

```python
def _safe_name(budget: Budget) -> str:
    """Código (o nombre) del presupuesto apto para nombres de archivo."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", budget.code or budget.name)[:60] or "presupuesto"


@login_required(login_url="platform_login")
@require_http_methods(["GET", "POST"])
def history(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if request.method == "POST":
        _require_manage(ctx)
        version_id = _parse_uuid(request.POST.get("version"))
        if request.POST.get("action") == "contractual" and version_id is not None:
            try:
                with transaction.atomic():
                    previous, version = mark_contractual(ctx, budget.pk, version_id)
                    audit.record(
                        ctx,
                        action=AuditAction.BUDGET_CONTRACTUAL_CHANGED,
                        target=f"budget:{budget.pk}",
                        metadata={"from": previous, "to": version.number},
                    )
            except ImportRejected as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"La versión {version.number} es ahora la contractual.")
        return redirect("budgets:history", project.pk, budget.pk)

    versions = scoped_qs(ctx, BudgetVersion).defer("source_file").select_related("uploaded_by")
    can_manage = can_manage_budgets(ctx, ctx.org_id)
    return _render(
        request,
        ctx,
        "suno/budgets/history.html",
        {
            **_budget_context(ctx, project, budget),
            "versions": versions.filter(
                budget=budget, status=BudgetVersionStatus.APPLIED
            ).order_by("-number"),
            "pending": (
                versions.filter(budget=budget, status=BudgetVersionStatus.PENDING).order_by(
                    "-uploaded_at"
                )
                if can_manage
                else []
            ),
        },
    )


@login_required(login_url="platform_login")
def version_detail(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    """El contenido de una versión aplicada, con sus cargos."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    version = _get_version(ctx, budget, version_id, status=BudgetVersionStatus.APPLIED)
    charges = list(scoped_qs(ctx, BudgetCharge).filter(version=version).order_by("order"))
    names = {c.order: c.name for c in charges}
    return _render(
        request,
        ctx,
        "suno/budgets/version.html",
        {
            **_budget_context(ctx, project, budget),
            "version": version,
            "is_contractual": version.pk == budget.contractual_version_id,
            "rows": version_rows(ctx, version),
            "charges": [
                {
                    "charge": c,
                    "note": f"{pct_text(c.pct)} % sobre {describe_base(c.base, names)}",
                }
                for c in charges
            ],
        },
    )


@login_required(login_url="platform_login")
def version_source(
    request: HttpRequest, project_id: UUID, budget_id: UUID, version_id: UUID
) -> HttpResponse:
    """Descarga del Excel original de una versión aplicada (auditada)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    version = _get_version(
        ctx, budget, version_id, status=BudgetVersionStatus.APPLIED, with_file=True
    )
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.BUDGET_SOURCE_DOWNLOADED,
            target=f"budget:{budget.pk}",
            metadata={"version": version.number},
        )
    response = HttpResponse(bytes(version.source_file), content_type=XLSX_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{_safe_name(budget)}-v{version.number}.xlsx"'
    )
    return response
```

- [ ] **Step 5: Plantillas**

`server/visitacampo/core/templates/suno/budgets/history.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Historial · {{ budget.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="history" %}
<section class="pj-panel">
  <h2>Versiones aplicadas</h2>
  <table class="pj-table">
    <thead><tr><th>Versión</th><th>Etiqueta</th><th>Archivo</th><th>Subida por</th><th>Aplicada</th><th class="num">Total</th><th>Cambios frente a la anterior</th><th></th></tr></thead>
    <tbody>
    {% for v in versions %}
      <tr>
        <td><a href="{% url 'budgets:version' project.pk budget.pk v.pk %}">v{{ v.number }}</a>{% if v.pk == budget.contractual_version_id %} <span class="bg-label bg-label-contractual">contractual</span>{% endif %}</td>
        <td>{{ v.label|default:"—" }}</td>
        <td class="pj-wrap">{{ v.file_name }}{% if can_export_budgets %} · <a href="{% url 'budgets:version_source' project.pk budget.pk v.pk %}">Excel</a>{% endif %}</td>
        <td>{{ v.uploaded_by.display_name|default:v.uploaded_by.username }}</td>
        <td>{{ v.applied_at|date:"d/m/Y H:i" }}</td>
        <td class="num">{{ v.total|floatformat:"2g" }}</td>
        <td class="pj-wrap">{{ v.summary.added }} no previstos · {{ v.summary.removed }} eliminados · {{ v.summary.more_quantity }} mayor cant. · {{ v.summary.less_quantity }} menor cant. · {{ v.summary.price_changed }} cambio de precio</td>
        <td>{% if can_manage_budgets and v.pk != budget.contractual_version_id %}<form method="post">{% csrf_token %}<input type="hidden" name="version" value="{{ v.pk }}"><button class="text-link" type="submit" name="action" value="contractual">Marcar como contractual</button></form>{% endif %}</td>
      </tr>
    {% empty %}
      <tr><td colspan="8" class="pj-empty">Sin versiones aplicadas.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% if pending %}
<section class="pj-panel">
  <h2>Cargas pendientes de confirmar</h2>
  <ul>
    {% for v in pending %}<li><a href="{% url 'budgets:import_preview' project.pk budget.pk v.pk %}">{{ v.file_name }}</a> · subida el {{ v.uploaded_at|date:"d/m/Y H:i" }} por {{ v.uploaded_by.display_name|default:v.uploaded_by.username }}</li>{% endfor %}
  </ul>
</section>
{% endif %}
{% endblock %}
```

`server/visitacampo/core/templates/suno/budgets/version.html`:

```django
{% extends "suno/projects/base.html" %}
{% load static %}
{% block title %}Versión {{ version.number }} · {{ budget.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="budgets" %}
{% include "suno/budgets/_nav.html" with sub="history" %}
<section class="pj-panel">
  <h2>Versión {{ version.number }}{% if version.label %} · {{ version.label }}{% endif %}{% if is_contractual %} <span class="bg-label bg-label-contractual">contractual</span>{% endif %}</h2>
  <p class="pj-muted">{{ version.file_name }} · aplicada el {{ version.applied_at|date:"d/m/Y H:i" }}{% if can_export_budgets %} · <a href="{% url 'budgets:version_source' project.pk budget.pk version.pk %}">Descargar el Excel original</a>{% endif %}</p>
  <p class="pj-tree-actions"><button type="button" class="text-link" data-expand-all>Expandir todo</button> · <button type="button" class="text-link" data-collapse-all>Contraer todo</button></p>
  <div class="pj-scroll">
  <table class="pj-table bg-compare" data-wbs>
    <thead><tr><th>Código</th><th>Descripción</th><th>Und</th><th class="num">Cantidad</th><th class="num">Vr. unitario</th><th class="num">Total</th></tr></thead>
    <tbody>
    {% for r in rows %}
      <tr data-id="{{ r.item_id }}"{% if r.parent_id %} data-parent="{{ r.parent_id }}"{% endif %} class="{% if r.kind == 'chapter' %}{% if r.depth == 0 %}pj-stage{% else %}pj-summary{% endif %}{% endif %}">
        <td>{{ r.code }}</td>
        <td class="pj-name" style="padding-left: {{ r.depth|add:1 }}em">{% if r.kind == "chapter" %}<button type="button" class="pj-toggle" data-toggle aria-expanded="true" aria-label="Plegar {{ r.description }}">▾</button> {% endif %}{{ r.description|default:"—" }}</td>
        <td>{{ r.unit }}</td>
        <td class="num">{{ r.quantity|floatformat:"-4g" }}</td>
        <td class="num">{{ r.unit_price|floatformat:"2g" }}</td>
        <td class="num">{{ r.total|floatformat:"2g" }}</td>
      </tr>
    {% endfor %}
    </tbody>
    <tfoot>
      <tr><td colspan="5">Costo directo</td><td class="num">{{ version.direct_cost|floatformat:"2g" }}</td></tr>
      {% for line in charges %}<tr><td colspan="5">{{ line.charge.name }} <small class="pj-muted">{{ line.note }}</small></td><td class="num">{{ line.charge.amount|floatformat:"2g" }}</td></tr>{% endfor %}
      <tr class="bg-total"><td colspan="5">Total</td><td class="num">{{ version.total|floatformat:"2g" }}</td></tr>
    </tfoot>
  </table>
  </div>
</section>
{% endblock %}
{% block scripts %}<script src="{% static 'suno/js/schedule.js' %}" defer></script>{% endblock %}
```

En `server/visitacampo/core/templates/suno/budgets/_nav.html`, entre el enlace **Comparación** y el de **Importar Excel**, añadir:

```django
    <a href="{% url 'budgets:history' project.pk budget.pk %}" {% if sub == "history" %}aria-current="page"{% endif %}>Historial</a>
```

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: todo pasa.

- [ ] **Step 7: Commit**

```bash
git add server/visitacampo/budgets server/visitacampo/core/templates/suno/budgets
git commit -m "feat(budgets): version history, version page, original excel and contractual change"
```

---

### Task 14: Web — exportar la comparación a Excel

**Files:**
- Modify: `server/visitacampo/budgets/views.py`, `server/visitacampo/budgets/urls.py`
- Modify: `server/visitacampo/core/templates/suno/budgets/compare.html`
- Test: `server/visitacampo/budgets/tests/test_views.py`

- [ ] **Step 1: Escribir la prueba que falla**

En `server/visitacampo/budgets/tests/test_views.py`:

- Añadir `import io` e `import zipfile` a los imports de la biblioteca estándar.
- En `BudgetViewTestCase.budget_urls`, añadir a la lista: `f"{self.base}{budget.pk}/exportar.xlsx",`.

Y al final del archivo:

```python
@skip_without_db
class ExportTests(BudgetViewTestCase):
    def test_viewer_exports_the_full_comparison_and_it_is_audited(self) -> None:
        budget = self.budget_v1_v2()
        self.as_user(self.viewer)
        page = self.client.get(f"{self.base}{budget.pk}/")
        self.assertContains(page, "exportar.xlsx")
        response = self.client.get(f"{self.base}{budget.pk}/exportar.xlsx", {"cambios": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="C-01-comparacion.xlsx"'
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
        for text in (
            "codigo",
            "total_vigente",
            "Formaleta",
            "Curado",  # sin filtros: la exportación es completa
            "No previsto",
            "Mayor cantidad",
            "IVA sobre utilidad",
            "11042735.0",
            "10510555.0",
        ):
            with self.subTest(text=text):
                self.assertIn(text, sheet)
        event = AuditEvent.objects.get(action="budgets.exported")
        self.assertEqual(event.metadata, {"rows": 8})
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh visitacampo/budgets/tests/test_views.py`
Expected: FAIL (`ExportTests` y `AccessTests`).

- [ ] **Step 3: URL y vista**

En `server/visitacampo/budgets/urls.py`, añadir a `urlpatterns`:

```python
    path("<uuid:budget_id>/exportar.xlsx", views.export_comparison, name="export"),
```

En `server/visitacampo/budgets/views.py`:

- Añadir `from visitacampo.backoffice.export import safe_cell, to_xlsx` (antes de `from visitacampo.core import audit`).
- Cambiar el import de `.comparison` por:
  `from .comparison import ComparisonRow, budget_summaries, build_comparison, filter_rows, version_rows`.
- Añadir `from .excel import KIND_CHAPTER, MAX_BYTES, ExcelRejected, preview_sheet` en lugar del import actual de `.excel`.

Tras `XLSX_TYPE = …` añadir:

```python
EXPORT_HEADER = [
    "codigo", "descripcion", "unidad", "tipo", "estado",
    "cant_contractual", "vr_unitario_contractual", "total_contractual",
    "cant_vigente", "vr_unitario_vigente", "total_vigente",
    "diferencia", "diferencia_pct",
]  # fmt: skip
```

Y al final del archivo:

```python
def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float: los montos van como float (2 decimales)."""
    return None if value is None else float(value)


@login_required(login_url="platform_login")
def export_comparison(request: HttpRequest, project_id: UUID, budget_id: UUID) -> HttpResponse:
    """La comparación completa (sin filtros) con cargos y totales, en Excel (auditada)."""
    ctx = _ctx(request)
    project = get_budget_project(ctx, project_id)
    budget = get_budget(ctx, project, budget_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    data = build_comparison(ctx, budget)

    def row_values(r: ComparisonRow) -> list[object]:
        c, v = r.contractual, r.current
        return [
            safe_cell(value)
            for value in (
                r.code, r.description, r.unit,
                "capítulo" if r.kind == KIND_CHAPTER else "ítem",
                ", ".join(r.labels),
                _num(c.quantity) if c else None, _num(c.unit_price) if c else None,
                _num(c.total) if c else None,
                _num(v.quantity) if v else None, _num(v.unit_price) if v else None,
                _num(v.total) if v else None,
                _num(r.diff), _num(r.diff_pct),
            )
        ]  # fmt: skip

    footer = [
        [
            safe_cell(value)
            for value in (
                "", f"{f.label} ({f.note})" if f.note else f.label, "", "", "", "", "",
                _num(f.contractual), "", "", _num(f.current), _num(f.diff), _num(f.diff_pct),
            )
        ]  # fmt: skip
        for f in data.footer
    ]
    content = to_xlsx(EXPORT_HEADER, [*(row_values(r) for r in data.rows), *footer])
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.BUDGET_EXPORTED,
            target=f"budget:{budget.pk}",
            metadata={"rows": len(data.rows)},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{_safe_name(budget)}-comparacion.xlsx"'
    )
    return response
```

- [ ] **Step 4: Enlace en la comparación**

En `compare.html`, dentro del formulario de filtros, tras el botón **Aplicar**, añadir:

```django
    {% if can_export_budgets %}<a class="text-link" href="{% url 'budgets:export' project.pk budget.pk %}">Exportar la comparación completa (Excel)</a>{% endif %}
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh visitacampo/budgets`
Expected: todo el paquete pasa.

- [ ] **Step 6: Commit**

```bash
git add server/visitacampo/budgets server/visitacampo/core/templates/suno/budgets/compare.html
git commit -m "feat(budgets): export the comparison to excel"
```

---
### Task 15: Verificación completa, prueba manual y registro de avance

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

Expected: suite completa en verde (incluidas `core/tests/test_architecture.py`, `test_policies.py`, `test_models.py` y `test_scoping.py`, que cubren los modelos y políticas nuevos sin tocarlas más allá de la Task 2), ruff y mypy limpios, sin migraciones pendientes y cobertura ≥ 90 % (el suelo de `pyproject.toml`). Si ruff marca orden de imports, `infra/scripts/test_docker.sh --cmd "ruff check --fix ."` y revisar el diff.

- [ ] **Step 2: Prueba manual en el navegador**

Levantar el stack de desarrollo (ver `infra/README.md`). Generar dos Excel de prueba desde las muestras (y borrarlos al terminar):

```bash
infra/scripts/test_docker.sh --cmd "python -c 'from visitacampo.budgets.tests.samples import V1_ROWS, V2_ROWS, xlsx; open(\"/app/v1.xlsx\",\"wb\").write(xlsx(V1_ROWS)); open(\"/app/v2.xlsx\",\"wb\").write(xlsx(V2_ROWS))'"
```

Con un Administrador: crear un proyecto y, en la pestaña **Presupuestos**, un presupuesto; importar `v1.xlsx` (revisar la sugerencia de columnas y la tabla de primeras filas), poner AIU 10 / 5 / 5 / 19 en la vista previa y aplicar; importar `v2.xlsx` (debe ir directo a la vista previa con 1 no previsto, 1 eliminado, 1 mayor, 1 menor, 1 cambio de precio) y aplicar. Revisar la comparación (etiquetas, plegado de capítulos, filtros, pie con cargos y totales, columnas fijas al desplazar en horizontal y en ancho de teléfono), el historial ("Marcar como contractual"), la página de una versión, la descarga del Excel original y la exportación. Probar también un Excel real del equipo (títulos arriba, celdas combinadas, subtotales, fórmulas). Con un Observador: ve y exporta, sin botones de gestión. Con un Agente de campo responsable de una tarea: no ve la pestaña y las URL dan 404. Al terminar: `docker compose … down`, `infra/scripts/test_docker.sh --down` y borrar `server/v1.xlsx` y `server/v2.xlsx`.

- [ ] **Step 3: Registrar el avance**

En `plans/visitacampo-progreso.md`, añadir una sección "Módulo de costos — Pieza 3 (Presupuesto), servidor" con: fecha, PR, qué entra (Tasks 1–14), resultado real de la verificación del Step 1 (número de pruebas, cobertura), las decisiones de "Riesgos y decisiones tomadas al planear" que afectan al uso (columna capítulo no implementada, códigos numéricos, fórmulas sin valor calculado, límites) y "Siguiente: pieza 4 (homologación con el cronograma)".

- [ ] **Step 4: Commit**

```bash
git add plans/visitacampo-progreso.md
git commit -m "docs(plan): record cost module piece 3 (budget) server progress"
```

---

## Cobertura de la spec

| Spec | Dónde |
|---|---|
| §1 Modelos `Budget`, `BudgetVersion`, `BudgetCharge`, `BudgetItem`, `BudgetItemVersion`, `OrgScopedModel`, nombre único sin mayúsculas, `numeric` | Task 1 |
| §1 Primera aplicada = contractual; marcar otra (auditado); vigente = última aplicada | Tasks 9, 13 |
| §1 Eliminado (cantidad vigente 0) y que vuelve con la misma identidad | Tasks 7, 9, 10 |
| §1 Totales: directo + cargos en orden; proyecto = suma; `ROUND_HALF_UP` a centavos | Tasks 3, 6, 9, 10 |
| §2 Flujo subir → mapeo (primera vez o a pedido) → vista previa → confirmar | Tasks 8, 12 |
| §2 Sugerencia por encabezados; mapeo guardado y reutilizado | Tasks 4, 8, 12 |
| §2 Vista previa: no previstos, eliminados, mayor/menor, precio, totales antes/después de cargos, advertencias, etiqueta, cargos | Tasks 7, 8, 12 |
| §2 Una transacción con bloqueo; vista previa obsoleta rechazada; simultáneas rechazadas | Task 9 |
| §2 Interpretación de filas (ítem, capítulo, ignoradas, repetidos, inválidos, combinadas, fórmulas) y padre | Tasks 5, 6 |
| §2 Cargos: plantilla AIU + IVA sobre utilidad en 0; copia de la anterior; edición en la vista previa | Tasks 3, 8, 12 |
| §2 Límites: solo `.xlsx`, 20 MB, 20.000 filas | Tasks 5, 8, 12 |
| §3 Pestaña Presupuestos solo para quien los ve | Task 11 |
| §3 Lista con totales, diferencia, versión vigente y total del proyecto; "Nuevo presupuesto" | Tasks 10, 11 |
| §3 Comparación por capítulos plegable, etiquetas, filtros, pie con cargos y total, exportar | Tasks 10, 11, 14 |
| §3 Versión con cargos, etiqueta y descarga del original | Task 13 |
| §3 Historial con contractual y "Marcar como contractual" | Task 13 |
| §3 Permisos por rol; agente de campo 404 | Tasks 2, 11–14 |
| §3 Auditoría: creación, aplicada, descartada, contractual, exportación, descarga | Tasks 2, 11–14 |
| Pruebas: N+1 (3.000 × 2 en consultas constantes) | Task 10 |

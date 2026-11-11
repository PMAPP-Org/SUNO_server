# Costos — Pieza 5 (control con valor ganado): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar al proyecto la página "Control" con el valor planeado (PV), el valor ganado (EV), la curva S, la variación (SV) y el índice (SPI) del cronograma a una fecha de corte, con desglose por capítulo, tarea y ubicación, avisos y exportación a Excel.

**Architecture:** Paquete nuevo `server/suno/control/` sin modelos ni migraciones. `periods.py` (periodos semanales o mensuales, puro) y `earned_value.py` (cálculo puro: reparto del PV por días laborables, EV con tope por par, indicadores, desglose y avisos) no tocan la BD; `loader.py` arma su entrada con nueve queries fijas; `chart.py` convierte la serie en coordenadas para un SVG del servidor (mismo estilo que el panel de costos de equipos); `export.py` arma el libro de dos hojas con `safe_cell`, sobre un `to_xlsx_book` nuevo en `backoffice/export.py`. La vista vive bajo `plataforma/proyectos/<id>/control/`, con el mismo acceso que la Matriz, y la pestaña "Control" se agrega a la cabecera del proyecto. Un comando `seed_control_demo` siembra un proyecto de ejemplo para la revisión visual.

**Tech Stack:** Django 5.2, PostgreSQL 16, plantillas de Django con SVG y CSS propios (sin JavaScript nuevo), `unittest`/`django.test` corridos en Docker.

**Spec:** `plans/2026-11-11-costos-pieza5-control-design.md` (aprobada). Plan de referencia de formato: `plans/2026-11-09-costos-pieza4b-cantidades-plan.md` (Parte A).

**Rama y worktree:** `feat/costos-control` en `C:\Users\gabri\APP\wt-server-control` (base `master` `a682ac6`, spec en `d61cc33`). Todos los comandos se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Se anotan en la spec en la Task 0.

1. **Parámetros de la URL.** `corte` (AAAA-MM-DD; inválido o ausente → hoy en `America/Bogota`), `periodo` (`mes` por defecto, o `semana`) y `desglose` (`capitulo` por defecto, `tarea`, `ubicacion`). Un valor desconocido cae al de por defecto, sin error.
2. **El periodo del corte se parte.** El periodo que contiene la fecha de corte termina en ella (es el último con EV). Si la serie sigue (curva planeada completa), el resto de ese periodo calendario es una fila aparte "… (tras el corte)", sin EV. Así ningún día del PV se pierde ni se cuenta dos veces.
3. **Rango de la serie:** de `min(primer inicio, corte, primer registro contado)` a `max(último fin, corte)`; un registro anterior al primer inicio cae en un periodo que existe. Tope de 1000 periodos (una fecha absurda del XML no genera miles de filas): por encima, la página muestra un mensaje y el Excel responde 400.
4. **Tope del EV:** el acumulado del par tarea × ítem se acota a `[0, planeada]`. Un ajuste negativo resta del acumulado, pero el EV del par nunca baja de 0. Sobreejecución = acumulado al corte − planeada (si es positivo), con su valor a precio vigente. Un par con planeada 0 tiene PV 0 y todo lo ejecutado es sobreejecución.
5. **Hitos:** una tarea marcada como hito **o** cuya ventana no tiene días laborables carga todo su valor en su fin (la misma regla que `projects/progress.py` usa para el % programado).
6. **Fechas:** la línea base cuenta solo si tiene inicio **y** fin; si no, las fechas actuales (aviso "sin línea base"). Una tarea con plan y sin ninguna de las dos (o con fin antes que inicio) va al aviso "Tareas sin fechas" y no suma a PV, EV ni BAC.
7. **Tareas retiradas** del cronograma quedan fuera de todo (como en el cronograma y en `matrix.reports.location_rows`).
8. **"Sin precio vigente"** = el ítem no está como `item` en la versión vigente (última aplicada) de su presupuesto: se retiró o hoy es capítulo. Sus cantidades ejecutadas tampoco entran; el aviso muestra planeado y ejecutado.
9. **Fecha del registro** = `Record.captured_at` en hora de Colombia (no el `captured_at` de cada fila de la 4b). El EV cuenta registros vivos con revisión vigente `approved` hasta el corte inclusive.
10. **Ejecutado fuera del plan:** suma neta por tarea × ítem hasta el corte; un neto de 0 no se lista; valor vacío si el ítem no tiene precio vigente.
11. **Tareas medidas por %:** hojas vigentes (ni resumen ni hito) sin ninguna fila en la matriz, con su último % validado (sin límite de fecha, como el % SUNO) o "sin registro validado".
12. **Capítulo del desglose** = capítulo de **primer nivel** del ítem en la versión vigente ("2 Estructura"); un ítem sin capítulo va a "Sin capítulo". Con más de un presupuesto en el proyecto, ítems y capítulos llevan " · <presupuesto>" para no mezclarse.
13. **Ubicación del desglose** = la ruta completa de la ubicación de la tarea ("Tramo 2 / PR 1"), en filas planas (sin consolidar hacia arriba, para no contar dos veces en una tabla ordenada por SPI); sin ubicación → "Sin ubicación".
14. **Referencia contractual** = suma del **costo directo** de la versión contractual de cada presupuesto: el BAC es costo directo (cantidad × precio, sin AIU ni IVA), así que es la cifra comparable. Si ningún presupuesto tiene contractual, "Sin versión contractual".
15. **SPI** a dos decimales y el color se decide sobre el valor redondeado (0,949 se ve y se pinta como 0,95, verde). Etiquetas: "Al día" (verde), "Atención" (ámbar), "Atrasado" (rojo), "Sin valor planeado" (PV = 0).
16. **Orden del desglose:** SPI ascendente (peor primero); las filas sin SPI al final; desempate por nombre.
17. **Redondeo:** todo se suma con precisión amplia y se redondea a centavos al presentar; la fila de un periodo y su acumulado se redondean por separado (pueden diferir en un centavo de la suma de filas).
18. **Excel:** pide `can_export_budgets` (hoy la tienen todos los roles que leen presupuestos: no es un permiso nuevo) y deja auditoría `control.exported` (código nuevo, como `budgets.exported`). Hojas "Periodos" y "Desglose"; la segunda empieza con una fila "proyecto" con el total a la fecha de corte. `to_xlsx` solo hacía una hoja: se agrega `to_xlsx_book` y `to_xlsx` pasa a usarlo sin cambiar su salida.
19. **Cabecera del proyecto:** la pestaña "Control" va después de "Matriz", con la misma condición (`can_view_budgets`); en la página de control, la "Fecha de corte" de la cabecera muestra la del control.
20. **Tabla de la curva:** debajo del gráfico, dentro de un `<details>` (con semanas puede pasar de cien filas); el gráfico tiene `role="img"` con una etiqueta que remite a ella.
21. **Prueba de carga:** la página completa (GET con periodos semanales y desglose por tarea) con 500 tareas × 10 ítems = 5000 pares y 1000 registros validados de 5 filas, en menos de 1 s (segunda petición, después de calentar).
22. **Datos de ejemplo:** comando `seed_control_demo` (sobre una organización existente, con su administrador), que entra en la lista `ALLOWED` de `core/tests/test_architecture.py` como `seed_equipment_demo`.

---

## Convenciones del repositorio que hay que respetar

- Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `tearDownModule = teardown_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; las pruebas y los comandos de `ALLOWED` sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`. Nada de `assert` en producción (bandit B101) ni `import xml.etree` (tampoco en las pruebas nuevas). Ruff a 100 columnas (`E, F, I, UP, B, C4, DJ`: sin llamadas a funciones en valores por defecto, B008).
- Comentarios y textos de interfaz en español; identificadores en inglés. Commits en inglés, estilo conventional, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- La guardia de nombres (`infra/scripts/check_naming.py concept`) rechaza "visit"/"visita" en código y plantillas: el concepto es "registro".
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura (`ls-files`, `rev-parse`). No hay Python local: todo corre en Docker.

## Cómo correr las pruebas

```bash
# Desde C:/Users/gabri/APP/wt-server-control, con Docker Desktop arrancado (el contenedor trabaja en server/)
infra/scripts/test_docker.sh suno/control/tests/test_periods.py      # un archivo
infra/scripts/test_docker.sh suno/control                            # el paquete
infra/scripts/test_docker.sh                                         # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                  # al terminar

# Guardia de nombres (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-control -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Fallas conocidas, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y `healthcheck.py` en `ruff format --check` (comprobarlo en la Task 0 y anotarlo).

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `server/suno/backoffice/export.py` | `to_xlsx_book` (varias hojas); `to_xlsx` lo usa |
| `server/suno/control/__init__.py` | Paquete nuevo (sin modelos) |
| `server/suno/control/periods.py` | `Period`, `build_periods`, `PERIOD_KINDS`, `PeriodRangeError` (puro) |
| `server/suno/control/earned_value.py` | Entrada (`ControlInput`…), `compute`, indicadores, desglose y avisos (puro) |
| `server/suno/control/loader.py` | `load_control_input`: nueve queries fijas |
| `server/suno/control/chart.py` | `build_s_curve`: coordenadas y marcas de la curva S (puro) |
| `server/suno/control/export.py` | `control_workbook`: hojas "Periodos" y "Desglose" con `safe_cell` |
| `server/suno/control/views.py` | `dashboard` y `export` |
| `server/suno/control/urls.py` | `control:dashboard`, `control:export` |
| `server/config/urls.py` | Monta `plataforma/proyectos/<uuid:project_id>/control/` |
| `server/suno/core/audit.py` | `AuditAction.CONTROL_EXPORTED` |
| `server/suno/core/templates/suno/control/dashboard.html`, `_warnings.html` | Página |
| `server/suno/core/templates/suno/projects/_header.html` | Pestaña "Control" y fecha de corte de la cabecera |
| `server/suno/core/templates/suno/projects/base.html` | Versión del CSS (`?v=`) |
| `server/suno/core/static/suno/css/projects.css` | Estilos `ctl-*` |
| `server/suno/management/commands/seed_control_demo.py` | Proyecto de ejemplo para la revisión visual |
| `server/suno/core/tests/test_architecture.py` | `seed_control_demo` en `ALLOWED` |
| Pruebas | `server/suno/backoffice/tests/test_services.py`, `server/suno/control/tests/{__init__,test_periods,test_earned_value,test_loader,test_chart,test_export,test_views,test_performance,test_seed_demo}.py` |
| `plans/2026-11-11-costos-pieza5-control-design.md` | Decisiones del plan (Task 0) |
| `plans/suno-progreso.md` | Registro de avance (Task 11) |

---

### Task 0: Línea base y notas en la spec

**Files:**
- Modify: `plans/2026-11-11-costos-pieza5-control-design.md` (al final)

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-control status --short --branch`
Expected: `## feat/costos-control` sin cambios pendientes.

- [ ] **Step 2: Línea base**

```bash
infra/scripts/test_docker.sh 2>&1 | tail -5
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`; `ruff format --check` solo marca `healthcheck.py`. Anotar el número de pruebas (para el registro de avance).

- [ ] **Step 3: Anotar en la spec las decisiones tomadas al planear**

Agregar al final de `plans/2026-11-11-costos-pieza5-control-design.md`:

```markdown
## Decisiones tomadas al planear

Precisiones del plan `2026-11-11-costos-pieza5-control-plan.md`; no cambian lo aprobado.

- URL: `corte` (por defecto hoy), `periodo` (`mes` por defecto o `semana`) y `desglose`
  (`capitulo` por defecto, `tarea`, `ubicacion`); un valor desconocido cae al de por defecto.
- El periodo que contiene el corte termina en él; si la serie sigue, el resto de ese periodo es
  una fila aparte "(tras el corte)" sin EV. La serie empieza en el menor entre el primer
  inicio, el corte y el primer registro contado. Tope de 1000 periodos.
- El EV de cada par se acota a [0, planeada]: un ajuste negativo resta, pero el par no baja de
  0. La sobreejecución es lo acumulado al corte menos lo planeado.
- Un hito, o una ventana sin días laborables, carga todo en su fin (como el % programado).
- Una tarea con plan y sin fechas (ni de línea base ni actuales) se avisa y no suma.
- Las tareas retiradas del cronograma no cuentan.
- Sin precio vigente: el ítem no es `item` en la versión vigente de su presupuesto; su
  ejecución tampoco entra.
- La fecha de un registro es su `captured_at` en hora de Colombia.
- Tareas medidas por %: hojas vigentes (ni resumen ni hito) sin filas en la matriz, con su
  último % validado.
- Desglose por capítulo de primer nivel y por la ubicación completa de la tarea, en filas
  planas; con varios presupuestos, ítems y capítulos llevan el nombre del presupuesto.
- Referencia contractual: suma del costo directo de la versión contractual de cada presupuesto
  (el BAC es costo directo).
- SPI a dos decimales; el color se decide sobre el valor redondeado. Desglose de peor a mejor
  SPI, sin SPI al final.
- Excel: pide `can_export_budgets` (los roles que leen presupuestos ya la tienen) y queda en la
  auditoría como `control.exported`.
- Datos de ejemplo para la revisión visual: `python manage.py seed_control_demo`.
```

- [ ] **Step 4: Commit**

```bash
git add plans/2026-11-11-costos-pieza5-control-design.md
git commit -m "docs(plan): planning notes for cost piece 5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Libro Excel de varias hojas

**Files:**
- Modify: `server/suno/backoffice/export.py` (`__all__`; de `def to_xlsx(` al final del archivo)
- Test: `server/suno/backoffice/tests/test_services.py` (clase nueva al final; import)

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/backoffice/tests/test_services.py`, cambiar los imports:

```python
from django.test import SimpleTestCase, TestCase
```

```python
from suno.backoffice.export import (
    ExportTooLarge,
    record_table,
    safe_cell,
    to_csv,
    to_xlsx,
    to_xlsx_book,
)
```

Y al final del archivo:

```python
class XlsxBookTests(SimpleTestCase):
    """Costos, pieza 5: el Excel del control lleva dos hojas."""

    def test_book_with_several_sheets(self) -> None:
        data = to_xlsx_book([("Uno", ["a"], [[1]]), ("Dos & tres", ["b"], [["x"]])])
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            self.assertIsNone(book.testzip())
            workbook = book.read("xl/workbook.xml").decode()
            relations = book.read("xl/_rels/workbook.xml.rels").decode()
            types = book.read("[Content_Types].xml").decode()
            first = book.read("xl/worksheets/sheet1.xml").decode()
            second = book.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn('<sheet name="Uno" sheetId="1" r:id="rId1"/>', workbook)
        self.assertIn('<sheet name="Dos &amp; tres" sheetId="2" r:id="rId2"/>', workbook)
        self.assertIn('Id="rId2"', relations)
        self.assertIn('Target="worksheets/sheet2.xml"', relations)
        self.assertIn('PartName="/xl/worksheets/sheet2.xml"', types)
        self.assertIn("<v>1</v>", first)
        self.assertIn(">x<", second)

    def test_single_sheet_keeps_its_layout(self) -> None:
        with zipfile.ZipFile(io.BytesIO(to_xlsx(["a"], [["b"]], sheet_name="Hoja"))) as book:
            self.assertEqual(
                sorted(book.namelist()),
                [
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "xl/_rels/workbook.xml.rels",
                    "xl/workbook.xml",
                    "xl/worksheets/sheet1.xml",
                ],
            )
            workbook = book.read("xl/workbook.xml").decode()
        self.assertIn('<sheet name="Hoja" sheetId="1" r:id="rId1"/>', workbook)

    def test_a_book_needs_a_sheet(self) -> None:
        with self.assertRaises(ValueError):
            to_xlsx_book([])
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/backoffice/tests/test_services.py`
Expected: ERROR de import (`cannot import name 'to_xlsx_book'`).

- [ ] **Step 3: Implementar**

En `server/suno/backoffice/export.py`, agregar `"to_xlsx_book",` a `__all__` (después de `"to_xlsx",`) y reemplazar desde `def to_xlsx(` hasta el final del archivo por:

```python
_SHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"


def _sheet_xml(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    sheet_rows = []
    for row_index, row in enumerate([list(header), *rows], start=1):
        cells = "".join(
            _xlsx_cell(f"{_column_name(col)}{row_index}", value) for col, value in enumerate(row)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )


def to_xlsx(
    header: Sequence[str], rows: Iterable[Sequence[Any]], *, sheet_name: str = "Registros"
) -> bytes:
    """Libro `.xlsx` mínimo válido: una hoja (`sheet_name`), cadenas en línea y números."""
    return to_xlsx_book([(sheet_name, header, rows)])


def to_xlsx_book(sheets: Sequence[tuple[str, Sequence[str], Iterable[Sequence[Any]]]]) -> bytes:
    """Libro `.xlsx` mínimo válido con una hoja por `(nombre, cabecera, filas)`, en orden."""
    if not sheets:
        raise ValueError("Un libro necesita al menos una hoja.")
    overrides: list[str] = []
    entries: list[str] = []
    relations: list[str] = []
    worksheets: dict[str, str] = {}
    for number, (sheet_name, header, rows) in enumerate(sheets, start=1):
        name = _xml_text(sheet_name).replace('"', "&quot;")
        worksheets[f"xl/worksheets/sheet{number}.xml"] = _sheet_xml(header, rows)
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{number}.xml" ContentType="{_SHEET_TYPE}"/>'
        )
        entries.append(f'<sheet name="{name}" sheetId="{number}" r:id="rId{number}"/>')
        relations.append(
            f'<Relationship Id="rId{number}" Type="{_REL_TYPES}/worksheet" '
            f'Target="worksheets/sheet{number}.xml"/>'
        )
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{''.join(overrides)}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            f'Type="{_REL_TYPES}/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(entries)}</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(relations)}</Relationships>"
        ),
        **worksheets,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/backoffice && ruff check suno/backoffice"
infra/scripts/test_docker.sh suno/backoffice suno/budgets/tests/test_views.py suno/matrix/tests/test_template.py
```

Expected: PASS (las pruebas viejas de `to_xlsx`, la exportación del presupuesto y la plantilla de la matriz siguen igual).

- [ ] **Step 5: Commit**

```bash
git add server/suno/backoffice
git commit -m "feat(backoffice): xlsx workbooks with several sheets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: Periodos semanales y mensuales

**Files:**
- Create: `server/suno/control/__init__.py`, `server/suno/control/tests/__init__.py` (vacíos)
- Create: `server/suno/control/periods.py`
- Test: `server/suno/control/tests/test_periods.py`

- [ ] **Step 1: Crear el paquete**

`server/suno/control/__init__.py` y `server/suno/control/tests/__init__.py` vacíos (como `suno/matrix/`: el paquete no tiene modelos ni `apps.py`; las plantillas viven en `core/templates/suno/control/`).

- [ ] **Step 2: Escribir las pruebas que fallan**

`server/suno/control/tests/test_periods.py`:

```python
"""Periodos del control (costos, pieza 5). Sin BD."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase

from suno.control.periods import (
    MONTH,
    WEEK,
    Period,
    PeriodRangeError,
    build_periods,
    next_start,
    period_label,
    period_start,
)


class PeriodHelpersTests(SimpleTestCase):
    def test_period_start(self) -> None:
        self.assertEqual(period_start(date(2026, 3, 4), WEEK), date(2026, 3, 2))  # lunes
        self.assertEqual(period_start(date(2026, 3, 8), WEEK), date(2026, 3, 2))  # domingo
        self.assertEqual(period_start(date(2026, 3, 4), MONTH), date(2026, 3, 1))

    def test_next_start(self) -> None:
        self.assertEqual(next_start(date(2026, 3, 2), WEEK), date(2026, 3, 9))
        self.assertEqual(next_start(date(2026, 11, 1), MONTH), date(2026, 12, 1))
        self.assertEqual(next_start(date(2026, 12, 1), MONTH), date(2027, 1, 1))

    def test_labels(self) -> None:
        self.assertEqual(period_label(date(2026, 3, 9), WEEK), "09-mar-2026")
        self.assertEqual(period_label(date(2026, 3, 1), MONTH), "Mar-2026")


class BuildPeriodsTests(SimpleTestCase):
    def test_weeks_until_the_cut(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 3, 31), WEEK)
        self.assertEqual(
            [(p.start, p.end, p.measured) for p in periods],
            [
                (date(2026, 3, 2), date(2026, 3, 8), True),
                (date(2026, 3, 9), date(2026, 3, 15), True),
                (date(2026, 3, 16), date(2026, 3, 22), True),
                (date(2026, 3, 23), date(2026, 3, 29), True),
                (date(2026, 3, 30), date(2026, 3, 31), True),  # termina en el corte
            ],
        )

    def test_the_cut_period_is_split_when_the_series_goes_on(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 4, 30), date(2026, 3, 18), MONTH)
        self.assertEqual(
            periods,
            [
                Period(date(2026, 3, 1), date(2026, 3, 18), "Mar-2026", True),
                Period(date(2026, 3, 19), date(2026, 3, 31), "Mar-2026 (tras el corte)", False),
                Period(date(2026, 4, 1), date(2026, 4, 30), "Abr-2026", False),
            ],
        )

    def test_a_cut_on_the_period_end_does_not_split(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 4, 30), date(2026, 3, 31), MONTH)
        self.assertEqual(
            [(p.start, p.end, p.measured) for p in periods],
            [
                (date(2026, 3, 1), date(2026, 3, 31), True),
                (date(2026, 4, 1), date(2026, 4, 30), False),
            ],
        )

    def test_a_cut_before_the_first_start(self) -> None:
        periods = build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 2, 20), WEEK)
        self.assertEqual(periods[0], Period(date(2026, 2, 16), date(2026, 2, 20), "16-feb-2026", True))
        self.assertEqual((periods[1].start, periods[1].end), (date(2026, 2, 21), date(2026, 2, 22)))
        self.assertFalse(periods[1].measured)
        self.assertEqual(len(periods), 6)
        self.assertEqual(periods[-1].end, date(2026, 3, 15))

    def test_too_many_periods(self) -> None:
        with self.assertRaises(PeriodRangeError):
            build_periods(date(1900, 1, 1), date(2100, 1, 1), date(2026, 1, 1), WEEK)

    def test_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            build_periods(date(2026, 3, 2), date(2026, 3, 13), date(2026, 3, 31), "dia")
```

- [ ] **Step 3: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_periods.py`
Expected: ERROR (`No module named 'suno.control.periods'`).

- [ ] **Step 4: Implementar**

`server/suno/control/periods.py`:

```python
"""Periodos del control con valor ganado (costos, pieza 5). Puro: sin BD.

Semanas de lunes a domingo o meses calendario. La serie va de `min(primer inicio, corte)` a
`max(último fin, corte)`. El periodo que contiene la fecha de corte termina en ella (es el
último con valor ganado); si la serie sigue, el resto de ese periodo es una fila aparte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from suno.equipment.charts import MONTHS_ES, month_label

WEEK = "semana"
MONTH = "mes"
PERIOD_KINDS = {MONTH: "Mensual", WEEK: "Semanal"}
DEFAULT_KIND = MONTH
#: Tope de filas: una fecha absurda en el XML no puede generar miles de periodos.
MAX_PERIODS = 1000
ONE_DAY = timedelta(days=1)


class PeriodRangeError(ValueError):
    """El rango de fechas da demasiados periodos; el mensaje es para el usuario."""


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    #: Termina en la fecha de corte o antes: lleva valor ganado.
    measured: bool


def period_start(day: date, kind: str) -> date:
    if kind == WEEK:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def next_start(start: date, kind: str) -> date:
    if kind == WEEK:
        return start + timedelta(days=7)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def period_label(start: date, kind: str) -> str:
    """Semana: `09-mar-2026` (su lunes). Mes: `Mar-2026`."""
    if kind == WEEK:
        return f"{start.day:02d}-{MONTHS_ES[start.month - 1]}-{start.year}"
    return month_label(start)


def build_periods(first: date, last: date, cut: date, kind: str) -> list[Period]:
    if kind not in PERIOD_KINDS:
        raise ValueError(f"Periodo desconocido: {kind!r}.")
    stop = max(last, cut)
    start = period_start(min(first, cut), kind)
    periods: list[Period] = []
    while start <= stop:
        following = next_start(start, kind)
        end = following - ONE_DAY
        label = period_label(start, kind)
        if start <= cut < end:
            periods.append(Period(start, cut, label, measured=True))
            if cut < stop:
                periods.append(
                    Period(cut + ONE_DAY, end, f"{label} (tras el corte)", measured=False)
                )
        else:
            periods.append(Period(start, end, label, measured=end <= cut))
        if len(periods) > MAX_PERIODS:
            raise PeriodRangeError(
                f"Las fechas del cronograma dan más de {MAX_PERIODS} periodos. Revisa las "
                "fechas de las tareas o usa periodos mensuales."
            )
        start = following
    return periods
```

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control"
infra/scripts/test_docker.sh suno/control/tests/test_periods.py
```

Expected: PASS (9 pruebas).

- [ ] **Step 6: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): weekly and monthly periods split at the cut date

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Cálculo del valor ganado

**Files:**
- Create: `server/suno/control/earned_value.py`
- Test: `server/suno/control/tests/test_earned_value.py`

Calendario de las pruebas: lunes a viernes. Marzo de 2026: el 2 es lunes, el 13 viernes, el 31 martes. La tarea por defecto va del lunes 2 al viernes 13 (10 días laborables) con 100 m3 a $1.000 = $100.000.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_earned_value.py`:

```python
"""Cálculo del valor ganado (costos, pieza 5). Sin BD."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.earned_value import (
    ControlInput,
    ControlResult,
    Execution,
    OutsidePlan,
    Overrun,
    PercentTask,
    PlanPair,
    PricedItem,
    TaskWindow,
    UnpricedPair,
    compute,
    spi_of,
    status_of,
)
from suno.control.periods import MONTH, WEEK
from suno.projects.workdays import CalendarException, WorkCalendar

D = Decimal
T1, T2, T3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
EXC, CONC, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MON = date(2026, 3, 2)
FRI = date(2026, 3, 13)
CUT = date(2026, 3, 31)


def window(
    task_id: uuid.UUID = T1,
    start: date | None = MON,
    finish: date | None = FRI,
    *,
    label: str = "1.1 Excavación",
    location: str = "Tramo 1",
    from_baseline: bool = True,
    milestone: bool = False,
    order: int = 1,
) -> TaskWindow:
    return TaskWindow(
        task_id=task_id,
        label=label,
        location=location,
        start=start,
        finish=finish,
        from_baseline=from_baseline,
        milestone=milestone,
        order=order,
    )


def priced(
    item_id: uuid.UUID = EXC,
    price: str = "1000",
    *,
    label: str = "1.1 Excavación mecánica",
    chapter: str = "1 Preliminares",
) -> PricedItem:
    return PricedItem(
        item_id=item_id, label=label, unit="m3", unit_price=D(price), chapter=chapter
    )


CONCRETE = priced(CONC, "450000", label="2.1 Concreto zapatas", chapter="2 Estructura")


def plan(task_id: uuid.UUID = T1, item_id: uuid.UUID = EXC, planned: str = "100") -> PlanPair:
    return PlanPair(task_id=task_id, item_id=item_id, planned=D(planned))


def done(
    day: date, quantity: str, task_id: uuid.UUID = T1, item_id: uuid.UUID = EXC
) -> Execution:
    return Execution(task_id=task_id, item_id=item_id, day=day, quantity=D(quantity))


def control(
    *,
    tasks: Sequence[TaskWindow] | None = None,
    items: Sequence[PricedItem] | None = None,
    plans: Sequence[PlanPair] | None = None,
    executions: Sequence[Execution] = (),
    cut: date = CUT,
    kind: str = WEEK,
    calendar: WorkCalendar | None = None,
    labels: Mapping[uuid.UUID, str] | None = None,
    percent_tasks: Sequence[PercentTask] = (),
    contractual: Decimal | None = None,
) -> ControlResult:
    task_list = [window()] if tasks is None else list(tasks)
    item_list = [priced()] if items is None else list(items)
    return compute(
        ControlInput(
            cut=cut,
            kind=kind,
            calendar=calendar or WorkCalendar(),
            tasks={t.task_id: t for t in task_list},
            items={i.item_id: i for i in item_list},
            item_labels=labels or {i.item_id: i.label for i in item_list},
            plans=[plan()] if plans is None else list(plans),
            executions=list(executions),
            percent_tasks=list(percent_tasks),
            contractual_direct_cost=contractual,
        )
    )


class PlannedValueTests(SimpleTestCase):
    def test_spread_evenly_over_workdays(self) -> None:
        result = control()
        self.assertEqual([r.pv for r in result.periods], [D("50000.00"), D("50000.00"), 0, 0, 0])
        self.assertEqual(result.periods[-1].pv_cum, D("100000.00"))
        self.assertEqual(result.indicators.bac, D("100000.00"))
        self.assertEqual(result.indicators.at_cut.pv, D("100000.00"))

    def test_cut_in_the_middle_of_a_week(self) -> None:
        result = control(cut=date(2026, 3, 4))
        rows = result.periods
        self.assertEqual((rows[0].period.end, rows[0].pv), (date(2026, 3, 4), D("30000.00")))
        self.assertEqual((rows[1].period.start, rows[1].pv), (date(2026, 3, 5), D("20000.00")))
        self.assertIsNone(rows[1].ev)
        self.assertEqual(rows[2].pv, D("50000.00"))
        self.assertEqual(result.indicators.at_cut.pv, D("30000.00"))
        self.assertEqual(result.indicators.pv_share, D("30.0"))

    def test_holidays_are_not_workdays(self) -> None:
        holiday = CalendarException(date(2026, 3, 9), date(2026, 3, 9), working=False)
        result = control(plans=[plan(planned="90")], calendar=WorkCalendar(exceptions=[holiday]))
        self.assertEqual([r.pv for r in result.periods[:2]], [D("50000.00"), D("40000.00")])

    def test_milestone_loads_everything_on_its_finish(self) -> None:
        milestone = window(start=date(2026, 3, 26), finish=date(2026, 3, 26), milestone=True)
        result = control(tasks=[milestone], plans=[plan(planned="5")])
        self.assertEqual([r.pv for r in result.periods], [D("5000.00"), D("0.00")])
        before = control(tasks=[milestone], plans=[plan(planned="5")], cut=date(2026, 3, 25))
        self.assertEqual(before.indicators.at_cut.pv, D("0.00"))

    def test_a_window_without_workdays_loads_on_its_finish(self) -> None:
        saturday = window(start=date(2026, 3, 28), finish=date(2026, 3, 28))
        on_it = control(tasks=[saturday], plans=[plan(planned="5")], cut=date(2026, 3, 28))
        self.assertEqual(on_it.indicators.at_cut.pv, D("5000.00"))
        before = control(tasks=[saturday], plans=[plan(planned="5")], cut=date(2026, 3, 27))
        self.assertEqual(before.indicators.at_cut.pv, D("0.00"))

    def test_monthly_periods_with_the_cut_in_the_middle(self) -> None:
        # 2 de marzo a 30 de abril: 22 + 22 días laborables; hasta el 18 de marzo, 13.
        result = control(
            tasks=[window(finish=date(2026, 4, 30))], kind=MONTH, cut=date(2026, 3, 18)
        )
        rows = result.periods
        self.assertEqual(rows[0].pv, D("29545.45"))
        self.assertEqual(rows[1].period.label, "Mar-2026 (tras el corte)")
        self.assertEqual((rows[1].pv, rows[1].pv_cum), (D("20454.55"), D("50000.00")))
        self.assertIsNone(rows[1].ev_cum)
        self.assertEqual((rows[2].period.label, rows[2].pv_cum), ("Abr-2026", D("100000.00")))
        self.assertEqual(result.indicators.at_cut.pv, D("29545.45"))


class EarnedValueTests(SimpleTestCase):
    def test_capped_at_the_plan_and_overrun_reported(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "80"), done(date(2026, 3, 10), "40")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("80000.00"), D("20000.00")])
        self.assertEqual(result.periods[-1].ev_cum, D("100000.00"))
        self.assertEqual(result.indicators.at_cut.ev, D("100000.00"))
        self.assertEqual(
            result.warnings.overrun,
            (
                Overrun(
                    task="1.1 Excavación",
                    item="1.1 Excavación mecánica",
                    unit="m3",
                    planned=D("100"),
                    executed=D("120"),
                    excess=D("20"),
                    value=D("20000.00"),
                ),
            ),
        )

    def test_negative_adjustments_subtract(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "50"), done(date(2026, 3, 10), "-20")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("50000.00"), D("-20000.00")])
        self.assertEqual(result.indicators.at_cut.ev, D("30000.00"))
        self.assertEqual(result.warnings.overrun, ())

    def test_earned_value_never_goes_below_zero(self) -> None:
        result = control(executions=[done(date(2026, 3, 4), "-5"), done(date(2026, 3, 10), "10")])
        self.assertEqual([r.ev for r in result.periods[:2]], [D("0.00"), D("5000.00")])

    def test_records_after_the_cut_do_not_count(self) -> None:
        result = control(executions=[done(date(2026, 4, 1), "10")])
        self.assertEqual(result.indicators.at_cut.ev, D("0.00"))

    def test_executed_outside_the_plan(self) -> None:
        result = control(
            items=[priced(), CONCRETE],
            executions=[done(date(2026, 3, 4), "2", item_id=CONC)],
        )
        self.assertEqual(result.indicators.at_cut.ev, D("0.00"))
        self.assertEqual(
            result.warnings.outside_plan,
            (
                OutsidePlan(
                    task="1.1 Excavación",
                    item="2.1 Concreto zapatas",
                    unit="m3",
                    quantity=D("2"),
                    value=D("900000.00"),
                ),
            ),
        )

    def test_outside_the_plan_without_price_or_netting_zero(self) -> None:
        labels = {EXC: "1.1 Excavación mecánica", OTHER: "9.9", CONC: "2.1 Concreto zapatas"}
        result = control(
            items=[priced(), CONCRETE],
            labels=labels,
            executions=[
                done(date(2026, 3, 4), "3", item_id=OTHER),
                done(date(2026, 3, 4), "2", item_id=CONC),
                done(date(2026, 3, 5), "-2", item_id=CONC),
            ],
        )
        self.assertEqual(
            result.warnings.outside_plan,
            (OutsidePlan(task="1.1 Excavación", item="9.9", unit="", quantity=D("3"), value=None),),
        )


class IndicatorTests(SimpleTestCase):
    def test_sv_spi_and_status_at_the_cut(self) -> None:
        result = control(cut=date(2026, 3, 6), executions=[done(date(2026, 3, 4), "40")])
        at_cut = result.indicators.at_cut
        self.assertEqual((at_cut.pv, at_cut.ev), (D("50000.00"), D("40000.00")))
        self.assertEqual(at_cut.sv, D("-10000.00"))
        self.assertEqual(at_cut.spi, D("0.80"))
        self.assertEqual((at_cut.status, at_cut.status_label), ("red", "Atrasado"))
        self.assertEqual(result.indicators.ev_share, D("40.0"))

    def test_spi_is_empty_without_planned_value(self) -> None:
        result = control(cut=date(2026, 2, 27), executions=[done(date(2026, 2, 26), "10")])
        at_cut = result.indicators.at_cut
        self.assertEqual((at_cut.pv, at_cut.ev), (D("0.00"), D("10000.00")))
        self.assertIsNone(at_cut.spi)
        self.assertEqual((at_cut.status, at_cut.status_label), ("none", "Sin valor planeado"))
        self.assertEqual(result.periods[0].ev, D("10000.00"))

    def test_status_thresholds(self) -> None:
        self.assertEqual(status_of(D("0.95")), "green")
        self.assertEqual(status_of(D("1.20")), "green")
        self.assertEqual(status_of(D("0.94")), "amber")
        self.assertEqual(status_of(D("0.85")), "amber")
        self.assertEqual(status_of(D("0.84")), "red")
        self.assertEqual(status_of(None), "none")

    def test_spi_rounds_to_two_decimals(self) -> None:
        self.assertEqual(spi_of(D("949"), D("1000")), D("0.95"))
        self.assertIsNone(spi_of(D("1"), D("0")))

    def test_contractual_reference_is_passed_through(self) -> None:
        result = control(contractual=D("9130000.00"))
        self.assertEqual(result.indicators.contractual_direct_cost, D("9130000.00"))


class BreakdownTests(SimpleTestCase):
    def setUp(self) -> None:
        self.result = control(
            tasks=[
                window(),
                window(T2, label="2.1 Columnas", location="", order=2),
                window(T3, date(2026, 4, 6), date(2026, 4, 10), label="3.1 Remates", order=3),
            ],
            items=[priced(), CONCRETE],
            plans=[plan(), plan(T2, CONC, "4"), plan(T3, EXC, "10")],
            executions=[done(date(2026, 3, 4), "100"), done(date(2026, 3, 10), "1", T2, CONC)],
        )

    def test_by_task_worst_first_and_without_spi_last(self) -> None:
        rows = self.result.by_task
        self.assertEqual(
            [r.label for r in rows], ["2.1 Columnas", "1.1 Excavación", "3.1 Remates"]
        )
        self.assertEqual(rows[0].task_id, T2)
        self.assertEqual((rows[0].measure.spi, rows[0].measure.status), (D("0.25"), "red"))
        self.assertEqual((rows[1].measure.spi, rows[1].measure.status), (D("1.00"), "green"))
        self.assertIsNone(rows[2].measure.spi)

    def test_by_location_and_chapter(self) -> None:
        locations = self.result.by_location
        self.assertEqual([r.label for r in locations], ["Sin ubicación", "Tramo 1"])
        self.assertEqual(locations[1].bac, D("110000.00"))
        self.assertIsNone(locations[1].task_id)
        self.assertEqual(
            [r.label for r in self.result.by_chapter], ["2 Estructura", "1 Preliminares"]
        )

    def test_bac_adds_every_priced_pair(self) -> None:
        self.assertEqual(self.result.indicators.bac, D("1910000.00"))


class WarningTests(SimpleTestCase):
    def test_task_without_baseline_uses_its_current_dates(self) -> None:
        result = control(tasks=[window(from_baseline=False)])
        self.assertEqual(result.warnings.no_baseline, ("1.1 Excavación",))
        self.assertEqual(result.indicators.bac, D("100000.00"))

    def test_task_without_dates_does_not_count(self) -> None:
        result = control(tasks=[window(start=None)])
        self.assertEqual(result.warnings.undated, ("1.1 Excavación",))
        self.assertEqual(result.indicators.bac, D("0.00"))
        self.assertEqual(result.periods, ())
        self.assertTrue(result.has_plan)

    def test_item_without_current_price(self) -> None:
        result = control(
            plans=[plan(), plan(item_id=OTHER, planned="3")],
            labels={EXC: "1.1 Excavación mecánica", OTHER: "9.9"},
            executions=[done(date(2026, 3, 4), "1", item_id=OTHER)],
        )
        self.assertEqual(
            result.warnings.unpriced,
            (UnpricedPair(task="1.1 Excavación", item="9.9", planned=D("3"), executed=D("1")),),
        )
        self.assertEqual(result.indicators.bac, D("100000.00"))

    def test_plans_of_tasks_not_in_the_schedule_are_ignored(self) -> None:
        result = control(plans=[plan(task_id=uuid.uuid4())])
        self.assertEqual(result.indicators.bac, D("0.00"))
        self.assertEqual(result.warnings.count, 0)
        self.assertTrue(result.has_plan)

    def test_percent_tasks_are_listed(self) -> None:
        tasks = [PercentTask("1.3 Aseo", D("40.00")), PercentTask("1.4 Cierre", None)]
        result = control(percent_tasks=tasks)
        self.assertEqual(result.warnings.percent_tasks, tuple(tasks))
        self.assertEqual(result.warnings.count, 2)

    def test_without_plan(self) -> None:
        result = control(plans=[])
        self.assertFalse(result.has_plan)
        self.assertEqual(result.periods, ())
        self.assertEqual(result.by_task, ())
```

(Las líneas que pasan de 100 columnas las parte `ruff format` en el Step 4; `ruff check` debe quedar limpio.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_earned_value.py`
Expected: ERROR (`No module named 'suno.control.earned_value'`).

- [ ] **Step 3: Implementar**

`server/suno/control/earned_value.py`:

```python
"""Valor ganado del proyecto (costos, pieza 5). Puro: sin BD.

Diseño: plans/2026-11-11-costos-pieza5-control-design.md, sección 1.

- **PV**: cada par tarea × ítem vale `planeada × precio unitario vigente`. El valor de la
  tarea se reparte parejo entre los días laborables de su ventana (línea base; sin ella, las
  fechas actuales, con aviso). Un hito, o una ventana sin días laborables, carga todo en su fin.
- **EV**: cantidades de registros validados × precio vigente, en la fecha del registro y hasta
  el corte. El acumulado de cada par se acota a `[0, planeada]`; lo que pasa de la planeada es
  sobreejecución y no entra.
- Se suma con precisión amplia y se redondea a centavos al presentar: el valor de un periodo y
  su acumulado se redondean por separado.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

from suno.budgets.totals import HUNDRED, WIDE_PRECISION, money
from suno.projects.workdays import WorkCalendar

from .periods import Period, build_periods

ZERO = Decimal(0)
ONE = Decimal(1)
SPI_STEP = Decimal("0.01")
SHARE_STEP = Decimal("0.1")
GREEN_FROM = Decimal("0.95")
AMBER_FROM = Decimal("0.85")
STATUS_LABELS = {
    "green": "Al día",
    "amber": "Atención",
    "red": "Atrasado",
    "none": "Sin valor planeado",
}
NO_CHAPTER = "Sin capítulo"
NO_LOCATION = "Sin ubicación"
UNKNOWN_ITEM = "Ítem desconocido"

Pair = tuple[UUID, UUID]


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskWindow:
    task_id: UUID
    #: "1.2 Descapote" (número de esquema y nombre).
    label: str
    #: Ubicación de la tarea ("Tramo 2 / PR 1"); vacía si no tiene.
    location: str
    start: date | None
    finish: date | None
    #: Las fechas son de la línea base; si no, son las actuales (va a los avisos).
    from_baseline: bool
    milestone: bool
    #: Orden de la fila en el Project, para listar.
    order: int = 0

    @property
    def dated(self) -> bool:
        return self.start is not None and self.finish is not None and self.start <= self.finish


@dataclass(frozen=True)
class PricedItem:
    item_id: UUID
    #: "2.1 Concreto zapatas".
    label: str
    unit: str
    unit_price: Decimal
    #: Capítulo de primer nivel que lo contiene ("2 Estructura"), o `NO_CHAPTER`.
    chapter: str


@dataclass(frozen=True)
class PlanPair:
    task_id: UUID
    item_id: UUID
    planned: Decimal


@dataclass(frozen=True)
class Execution:
    """Una fila de cantidad de un registro validado, en la fecha del registro."""

    task_id: UUID
    item_id: UUID
    day: date
    quantity: Decimal


@dataclass(frozen=True)
class PercentTask:
    """Tarea sin plan en la matriz: se mide por %. `pct`: su último % validado."""

    label: str
    pct: Decimal | None


@dataclass(frozen=True)
class ControlInput:
    cut: date
    kind: str
    calendar: WorkCalendar
    #: Tareas vigentes del cronograma (sin las retiradas).
    tasks: Mapping[UUID, TaskWindow]
    #: Ítems con precio vigente (`item` en la versión vigente de su presupuesto).
    items: Mapping[UUID, PricedItem]
    #: Nombre de cualquier ítem del proyecto, para los avisos.
    item_labels: Mapping[UUID, str]
    plans: Sequence[PlanPair]
    executions: Sequence[Execution]
    percent_tasks: Sequence[PercentTask] = ()
    #: El proyecto tiene al menos una versión de presupuesto aplicada.
    has_budget: bool = True
    #: Suma del costo directo de las versiones contractuales; `None` si no hay.
    contractual_direct_cost: Decimal | None = None


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


def spi_of(ev: Decimal, pv: Decimal) -> Decimal | None:
    """EV / PV a dos decimales; `None` sin valor planeado."""
    if pv <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (ev / pv).quantize(SPI_STEP, rounding=ROUND_HALF_UP)


def status_of(spi: Decimal | None) -> str:
    """Verde desde 0,95; ámbar desde 0,85; rojo por debajo; "none" sin SPI."""
    if spi is None:
        return "none"
    if spi >= GREEN_FROM:
        return "green"
    if spi >= AMBER_FROM:
        return "amber"
    return "red"


def share_of(value: Decimal, total: Decimal) -> Decimal | None:
    """% de `total` a un decimal; `None` si `total` es 0."""
    if total <= 0:
        return None
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return (value * HUNDRED / total).quantize(SHARE_STEP, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Measure:
    """PV y EV a una fecha (a centavos), con su SV, SPI y estado."""

    pv: Decimal
    ev: Decimal

    @property
    def sv(self) -> Decimal:
        return self.ev - self.pv

    @property
    def spi(self) -> Decimal | None:
        return spi_of(self.ev, self.pv)

    @property
    def status(self) -> str:
        return status_of(self.spi)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


@dataclass(frozen=True)
class PeriodRow:
    period: Period
    pv: Decimal
    pv_cum: Decimal
    #: `None` en los periodos posteriores al corte.
    ev: Decimal | None
    ev_cum: Decimal | None

    @property
    def sv(self) -> Decimal | None:
        return None if self.ev_cum is None else self.ev_cum - self.pv_cum

    @property
    def spi(self) -> Decimal | None:
        return None if self.ev_cum is None else spi_of(self.ev_cum, self.pv_cum)


@dataclass(frozen=True)
class Indicators:
    bac: Decimal
    at_cut: Measure
    contractual_direct_cost: Decimal | None = None

    @property
    def pv_share(self) -> Decimal | None:
        return share_of(self.at_cut.pv, self.bac)

    @property
    def ev_share(self) -> Decimal | None:
        return share_of(self.at_cut.ev, self.bac)


@dataclass(frozen=True)
class BreakdownRow:
    label: str
    bac: Decimal
    measure: Measure
    #: Solo en el desglose por tarea (enlace a su ficha).
    task_id: UUID | None = None


@dataclass(frozen=True)
class UnpricedPair:
    task: str
    item: str
    planned: Decimal
    executed: Decimal


@dataclass(frozen=True)
class Overrun:
    task: str
    item: str
    unit: str
    planned: Decimal
    executed: Decimal
    excess: Decimal
    value: Decimal


@dataclass(frozen=True)
class OutsidePlan:
    task: str
    item: str
    unit: str
    quantity: Decimal
    #: `None` si el ítem no tiene precio vigente.
    value: Decimal | None


@dataclass(frozen=True)
class Warnings:
    no_baseline: tuple[str, ...] = ()
    undated: tuple[str, ...] = ()
    unpriced: tuple[UnpricedPair, ...] = ()
    overrun: tuple[Overrun, ...] = ()
    outside_plan: tuple[OutsidePlan, ...] = ()
    percent_tasks: tuple[PercentTask, ...] = ()

    @property
    def count(self) -> int:
        return (
            len(self.no_baseline)
            + len(self.undated)
            + len(self.unpriced)
            + len(self.overrun)
            + len(self.outside_plan)
            + len(self.percent_tasks)
        )


@dataclass(frozen=True)
class ControlResult:
    cut: date
    kind: str
    has_budget: bool
    #: La matriz tiene al menos una fila (aunque ninguna mida).
    has_plan: bool
    indicators: Indicators
    #: Vacía si ningún par tiene precio vigente y fechas.
    periods: tuple[PeriodRow, ...]
    by_chapter: tuple[BreakdownRow, ...]
    by_task: tuple[BreakdownRow, ...]
    by_location: tuple[BreakdownRow, ...]
    warnings: Warnings


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pair:
    task: TaskWindow
    item: PricedItem
    planned: Decimal

    @property
    def value(self) -> Decimal:
        return self.planned * self.item.unit_price


class _Spread:
    """Reparto parejo del valor de una tarea entre los días laborables de su ventana."""

    def __init__(self, task: TaskWindow, calendar: WorkCalendar) -> None:
        if task.start is None or task.finish is None:
            raise ValueError("La tarea no tiene fechas.")
        self.start = task.start
        self.finish = task.finish
        self.calendar = calendar
        self.workdays = 0 if task.milestone else calendar.count_inclusive(self.start, self.finish)

    def share(self, day: date) -> Decimal:
        """Parte (0..1) del valor planeada hasta `day` inclusive."""
        if day >= self.finish:
            return ONE
        if self.workdays == 0 or day < self.start:
            return ZERO
        return Decimal(self.calendar.count_inclusive(self.start, day)) / Decimal(self.workdays)


@dataclass
class _Totals:
    bac: Decimal = ZERO
    pv: Decimal = ZERO
    ev: Decimal = ZERO

    def add(self, bac: Decimal, pv: Decimal, ev: Decimal) -> None:
        self.bac += bac
        self.pv += pv
        self.ev += ev

    def row(self, label: str, task_id: UUID | None = None) -> BreakdownRow:
        return BreakdownRow(label, money(self.bac), Measure(money(self.pv), money(self.ev)), task_id)


def _worst_first(rows: Iterable[BreakdownRow]) -> tuple[BreakdownRow, ...]:
    def key(row: BreakdownRow) -> tuple[bool, Decimal, str]:
        spi = row.measure.spi
        return (spi is None, ZERO if spi is None else spi, row.label.lower())

    return tuple(sorted(rows, key=key))


def compute(data: ControlInput) -> ControlResult:
    with localcontext() as context:
        context.prec = WIDE_PRECISION
        return _compute(data)


def _compute(data: ControlInput) -> ControlResult:
    pairs: dict[Pair, _Pair] = {}
    planned_keys: set[Pair] = set()
    unpriced: list[PlanPair] = []
    undated: dict[UUID, TaskWindow] = {}
    for plan in data.plans:
        task = data.tasks.get(plan.task_id)
        if task is None:  # tarea retirada del cronograma
            continue
        key = (plan.task_id, plan.item_id)
        planned_keys.add(key)
        item = data.items.get(plan.item_id)
        if item is None:
            unpriced.append(plan)
        elif not task.dated:
            undated[task.task_id] = task
        else:
            pairs[key] = _Pair(task, item, plan.planned)

    executions: dict[Pair, list[Execution]] = defaultdict(list)
    for execution in data.executions:
        if execution.day <= data.cut and execution.task_id in data.tasks:
            executions[(execution.task_id, execution.item_id)].append(execution)

    def executed(key: Pair) -> Decimal:
        return sum((e.quantity for e in executions.get(key, ())), ZERO)

    unpriced_rows = tuple(
        UnpricedPair(
            task=data.tasks[p.task_id].label,
            item=data.item_labels.get(p.item_id, UNKNOWN_ITEM),
            planned=p.planned,
            executed=executed((p.task_id, p.item_id)),
        )
        for p in sorted(
            unpriced,
            key=lambda p: (data.tasks[p.task_id].order, data.item_labels.get(p.item_id, "")),
        )
    )
    no_baseline = {p.task.task_id: p.task for p in pairs.values() if not p.task.from_baseline}
    base_warnings = Warnings(
        no_baseline=tuple(t.label for t in sorted(no_baseline.values(), key=lambda t: t.order)),
        undated=tuple(t.label for t in sorted(undated.values(), key=lambda t: t.order)),
        unpriced=unpriced_rows,
        outside_plan=_outside_plan(data, executions, planned_keys),
        percent_tasks=tuple(data.percent_tasks),
    )
    if not pairs:
        return ControlResult(
            cut=data.cut,
            kind=data.kind,
            has_budget=data.has_budget,
            has_plan=bool(data.plans),
            indicators=Indicators(
                money(ZERO), Measure(money(ZERO), money(ZERO)), data.contractual_direct_cost
            ),
            periods=(),
            by_chapter=(),
            by_task=(),
            by_location=(),
            warnings=base_warnings,
        )

    spreads = {p.task.task_id: _Spread(p.task, data.calendar) for p in pairs.values()}
    counted_days = [e.day for key in pairs for e in executions.get(key, ())]
    first = min([s.start for s in spreads.values()] + counted_days)
    last = max(s.finish for s in spreads.values())
    periods = build_periods(first, last, data.cut, data.kind)
    starts = [p.start for p in periods]

    def index(day: date) -> int:
        return bisect_right(starts, day) - 1

    # PV: el valor de cada tarea, repartido por periodos.
    task_value: dict[UUID, Decimal] = defaultdict(Decimal)
    for pair in pairs.values():
        task_value[pair.task.task_id] += pair.value
    planned = [ZERO] * len(periods)
    for task_id, value in task_value.items():
        spread = spreads[task_id]
        before = ZERO
        for i in range(index(spread.start), index(spread.finish) + 1):
            share = spread.share(periods[i].end)
            planned[i] += value * (share - before)
            before = share

    # EV: lo validado de cada par, acotado a [0, planeada], en el periodo del registro.
    earned = [ZERO] * len(periods)
    earned_quantity: dict[Pair, Decimal] = {}
    overruns: list[Overrun] = []
    ordered = sorted(pairs.items(), key=lambda kv: (kv[1].task.order, kv[1].item.label))
    for key, pair in ordered:
        running = capped = ZERO
        for execution in sorted(executions.get(key, ()), key=lambda e: e.day):
            running += execution.quantity
            bounded = min(max(running, ZERO), pair.planned)
            if bounded != capped:
                earned[index(execution.day)] += (bounded - capped) * pair.item.unit_price
                capped = bounded
        earned_quantity[key] = capped
        if running > pair.planned:
            excess = running - pair.planned
            overruns.append(
                Overrun(
                    task=pair.task.label,
                    item=pair.item.label,
                    unit=pair.item.unit,
                    planned=pair.planned,
                    executed=running,
                    excess=excess,
                    value=money(excess * pair.item.unit_price),
                )
            )

    rows: list[PeriodRow] = []
    pv_cum = ev_cum = ZERO
    for i, period in enumerate(periods):
        pv_cum += planned[i]
        if period.measured:
            ev_cum += earned[i]
            rows.append(
                PeriodRow(period, money(planned[i]), money(pv_cum), money(earned[i]), money(ev_cum))
            )
        else:
            rows.append(PeriodRow(period, money(planned[i]), money(pv_cum), None, None))

    # A la fecha de corte: total y desglose.
    shares = {task_id: spread.share(data.cut) for task_id, spread in spreads.items()}
    total = _Totals()
    chapters: dict[str, _Totals] = defaultdict(_Totals)
    tasks: dict[UUID, _Totals] = defaultdict(_Totals)
    locations: dict[str, _Totals] = defaultdict(_Totals)
    for key, pair in pairs.items():
        value = pair.value
        pv = value * shares[pair.task.task_id]
        ev = earned_quantity[key] * pair.item.unit_price
        for bucket in (
            total,
            chapters[pair.item.chapter],
            tasks[pair.task.task_id],
            locations[pair.task.location or NO_LOCATION],
        ):
            bucket.add(value, pv, ev)

    return ControlResult(
        cut=data.cut,
        kind=data.kind,
        has_budget=data.has_budget,
        has_plan=True,
        indicators=Indicators(
            money(total.bac),
            Measure(money(total.pv), money(total.ev)),
            data.contractual_direct_cost,
        ),
        periods=tuple(rows),
        by_chapter=_worst_first(t.row(label) for label, t in chapters.items()),
        by_task=_worst_first(
            t.row(data.tasks[task_id].label, task_id) for task_id, t in tasks.items()
        ),
        by_location=_worst_first(t.row(label) for label, t in locations.items()),
        warnings=Warnings(
            no_baseline=base_warnings.no_baseline,
            undated=base_warnings.undated,
            unpriced=base_warnings.unpriced,
            overrun=tuple(overruns),
            outside_plan=base_warnings.outside_plan,
            percent_tasks=base_warnings.percent_tasks,
        ),
    )


def _outside_plan(
    data: ControlInput, executions: Mapping[Pair, list[Execution]], planned_keys: set[Pair]
) -> tuple[OutsidePlan, ...]:
    """Cantidades netas de ítems que no están en el plan de su tarea (neto 0: no se listan)."""
    found: list[tuple[int, OutsidePlan]] = []
    for (task_id, item_id), rows in executions.items():
        if (task_id, item_id) in planned_keys:
            continue
        quantity = sum((e.quantity for e in rows), ZERO)
        if quantity == 0:
            continue
        task = data.tasks[task_id]
        item = data.items.get(item_id)
        found.append(
            (
                task.order,
                OutsidePlan(
                    task=task.label,
                    item=item.label if item else data.item_labels.get(item_id, UNKNOWN_ITEM),
                    unit=item.unit if item else "",
                    quantity=quantity,
                    value=money(quantity * item.unit_price) if item else None,
                ),
            )
        )
    found.sort(key=lambda entry: (entry[0], entry[1].item))
    return tuple(entry[1] for entry in found)
```

Notas para quien implementa: `Decimal(self.calendar.count_inclusive(...)) / Decimal(self.workdays)` corre dentro del `localcontext` de `compute` (precisión 60); `_Spread` solo se crea para pares con fechas (`task.dated`), así que el `ValueError` no se alcanza (es la red, en vez de un `assert`).

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control
```

Expected: PASS. Si `test_spread_evenly_over_workdays` falla solo por comparar `0` con `Decimal("0.00")`: son iguales en Python (`Decimal("0.00") == 0`), no cambiar la prueba.

- [ ] **Step 5: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): planned and earned value with caps, indicators and warnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: Cargador con un número fijo de queries

**Files:**
- Create: `server/suno/control/loader.py`
- Test: `server/suno/control/tests/test_loader.py`

Datos de las pruebas (`matrix/tests/factories.budget_world`): cronograma `samples.v1()` (marzo de 2026, lunes a viernes, festivo el lunes 23; 1.1 Localización uid 2, 1.2 Descapote uid 3 con línea base del 9 al 13, 2.1 Columnas uid 5 del 16 al 26, 2.2 Fin de obra uid 6 hito) y el presupuesto `V1_ROWS` aplicado (2.1 Concreto zapatas a $450.000, costo directo $9.130.000). `T0` es el 10 de marzo a las 09:00 UTC.

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_loader.py`:

```python
"""Cargador del control (costos, pieza 5): queries fijas y qué entra."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from django.test import TestCase

from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS, V2_ROWS
from suno.control.earned_value import ControlInput, Execution, PercentTask, TaskWindow
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.tests.factories import (
    budget_world,
    import_schedule,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import T0, make_field_world, make_record
from suno.projects.tests.samples import v2

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

D = Decimal
CUT = date(2026, 3, 20)


@skip_without_db
class LoaderTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        self.ctx = self.world.ctx()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))

    def load(self, cut: date = CUT) -> ControlInput:
        return load_control_input(self.ctx, self.world.project, cut=cut, kind=MONTH)

    def test_query_count_does_not_grow_with_the_data(self) -> None:
        with self.assertNumQueries(9):
            self.load()
        location = item(self.budget, "1.1")
        plan(self.world, 2, location, "50")
        for hours in (1, 2, 3):
            self.world.approve(
                quantity_record(self.world, 2, quantity_row(location, "5"), hours=hours)
            )
        with self.assertNumQueries(9):
            self.load()

    def test_windows_items_and_executions(self) -> None:
        data = self.load()
        task = self.world.task(3)
        self.assertEqual(
            data.tasks[task.pk],
            TaskWindow(
                task_id=task.pk,
                label="1.2 Descapote",
                location="Tramo 1",
                start=date(2026, 3, 9),
                finish=date(2026, 3, 13),
                from_baseline=True,
                milestone=False,
                order=task.sort_order,
            ),
        )
        priced = data.items[self.concrete.pk]
        self.assertEqual(
            (priced.label, priced.unit, priced.unit_price, priced.chapter),
            ("2.1 Concreto zapatas", "m3", D("450000"), "2 Estructura"),
        )
        self.assertEqual(
            data.executions,
            [Execution(self.world.task(5).pk, self.concrete.pk, date(2026, 3, 10), D("2"))],
        )
        self.assertEqual(sorted(p.planned for p in data.plans), [D("4"), D("8")])
        self.assertTrue(data.has_budget)
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))
        self.assertFalse(data.calendar.is_working(date(2026, 3, 23)))  # festivo del XML

    def test_only_live_validated_records_until_the_cut(self) -> None:
        row = quantity_row(self.concrete, "1")
        quantity_record(self.world, 5, row, hours=1)  # sin revisar
        self.world.reject(quantity_record(self.world, 5, row, hours=2))
        self.world.approve(
            make_record(
                self.world.org,
                self.world.agent,
                task=self.world.task(5),
                at=T0,
                deleted=True,
                quantities=[row],
            )
        )
        self.world.approve(quantity_record(self.world, 5, row, hours=24 * 30))  # 9 de abril
        # 21 de marzo a las 03:00 UTC: en Bogotá todavía es el 20.
        self.world.approve(
            quantity_record(self.world, 5, quantity_row(self.concrete, "3"), hours=258)
        )
        days = sorted((e.day, e.quantity) for e in self.load().executions)
        self.assertEqual(days, [(date(2026, 3, 10), D("2")), (date(2026, 3, 20), D("3"))])

    def test_current_prices_and_retired_items(self) -> None:
        apply_rows(self.ctx, self.budget, V2_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].unit_price, D("470000"))
        retired = item(self.budget, "1.2")
        self.assertNotIn(retired.pk, data.items)
        self.assertEqual(data.item_labels[retired.pk], "1.2")
        self.assertEqual(data.contractual_direct_cost, D("9130000.00"))

    def test_several_budgets_name_their_items(self) -> None:
        other = make_budget(self.world.project, self.world.admin, name="Interventoría", code="C-02")
        apply_rows(self.ctx, other, V1_ROWS)
        data = self.load()
        self.assertEqual(data.items[self.concrete.pk].chapter, "2 Estructura · Contrato de obra")
        self.assertEqual(data.contractual_direct_cost, D("18260000.00"))

    def test_retired_tasks_are_left_out(self) -> None:
        import_schedule(self.world, v2())
        self.assertNotIn(self.world.task(5).pk, self.load().tasks)

    def test_percent_tasks_are_the_leaves_without_plan(self) -> None:
        self.world.approve(self.world.record(2, "40"))
        self.assertEqual(self.load().percent_tasks, [PercentTask("1.1 Localización", D("40"))])

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        ctx = world.ctx()
        with self.assertNumQueries(8):
            data = load_control_input(ctx, world.project, cut=CUT, kind=MONTH)
        self.assertFalse(data.has_budget)
        self.assertEqual(data.items, {})
        self.assertEqual(data.plans, [])
        self.assertIsNone(data.contractual_direct_cost)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_loader.py`
Expected: ERROR (`No module named 'suno.control.loader'`).

- [ ] **Step 3: Implementar**

`server/suno/control/loader.py`:

```python
"""Lo que el control necesita de la BD, en un número fijo de queries (costos, pieza 5).

Nueve queries sin importar el tamaño del proyecto (ocho si no hay presupuesto aplicado):
árbol de ubicaciones, tareas vigentes, presupuestos (costo directo contractual), versiones
vigentes, sus filas, códigos de los ítems, plan de la matriz, cantidades validadas hasta el
corte y el último % validado por tarea.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone

from suno.core.context import RequestContext
from suno.core.models import (
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    Project,
    QuantityPlan,
    Record,
    Task,
)
from suno.core.scoping import scoped_qs
from suno.matrix.importer import current_versions
from suno.matrix.locations import load_tree
from suno.projects.field_progress import approved, validated_pct_by_task
from suno.projects.workdays import WorkCalendar

from .earned_value import (
    NO_CHAPTER,
    ControlInput,
    Execution,
    PercentTask,
    PlanPair,
    PricedItem,
    TaskWindow,
)


def load_control_input(
    ctx: RequestContext, project: Project, *, cut: date, kind: str
) -> ControlInput:
    tree = load_tree(ctx, project)
    tasks: dict[UUID, TaskWindow] = {}
    leaves: list[tuple[UUID, str]] = []
    for (
        pk,
        outline,
        name,
        summary,
        milestone,
        baseline_start,
        baseline_finish,
        start,
        finish,
        location_id,
        order,
    ) in (
        scoped_qs(ctx, Task)
        .filter(project=project, retired_in__isnull=True)
        .order_by("sort_order")
        .values_list(
            "pk",
            "outline_number",
            "name",
            "is_summary",
            "is_milestone",
            "baseline_start",
            "baseline_finish",
            "start",
            "finish",
            "location_id",
            "sort_order",
        )
    ):
        label = f"{outline} {name}"
        from_baseline = baseline_start is not None and baseline_finish is not None
        tasks[pk] = TaskWindow(
            task_id=pk,
            label=label,
            location=tree.label(location_id),
            start=baseline_start if from_baseline else start,
            finish=baseline_finish if from_baseline else finish,
            from_baseline=from_baseline,
            milestone=milestone,
            order=order,
        )
        if not summary and not milestone:
            leaves.append((pk, label))

    budgets = list(
        scoped_qs(ctx, Budget)
        .filter(project=project)
        .values_list("pk", "name", "contractual_version__direct_cost")
    )
    versions = current_versions(ctx, project)
    version_rows = (
        list(
            scoped_qs(ctx, BudgetItemVersion)
            .filter(version__in=versions)
            .values_list(
                "item_id",
                "item__code",
                "kind",
                "description",
                "unit",
                "unit_price",
                "parent_id",
                "version__budget_id",
            )
        )
        if versions
        else []
    )
    item_labels: dict[UUID, str] = {
        pk: code
        for pk, code in scoped_qs(ctx, BudgetItem)
        .filter(budget__project=project)
        .values_list("pk", "code")
    }
    items = _priced_items(version_rows, {pk: name for pk, name, _ in budgets}, item_labels)

    plans = [
        PlanPair(task_id, item_id, planned)
        for task_id, item_id, planned in scoped_qs(ctx, QuantityPlan)
        .filter(project=project)
        .values_list("task_id", "item_id", "planned_quantity")
    ]
    records = approved(
        scoped_qs(ctx, Record)
        .filter(task__project=project, deleted_at__isnull=True, captured_at__date__lte=cut)
        .exclude(quantities=[])
    ).values_list("task_id", "captured_at", "quantities")
    executions = [
        Execution(
            task_id=task_id,
            item_id=UUID(row["item_id"]),
            day=timezone.localtime(captured_at).date(),
            quantity=Decimal(row["quantity"]),
        )
        for task_id, captured_at, rows in records
        for row in rows
    ]

    planned_tasks = {p.task_id for p in plans}
    validated = validated_pct_by_task(scoped_qs(ctx, Record), project_ids=[project.pk])
    contractual = [direct for _, _, direct in budgets if direct is not None]
    return ControlInput(
        cut=cut,
        kind=kind,
        calendar=WorkCalendar.from_json(project.calendar),
        tasks=tasks,
        items=items,
        item_labels=item_labels,
        plans=plans,
        executions=executions,
        percent_tasks=[
            PercentTask(label, validated.get(pk)) for pk, label in leaves if pk not in planned_tasks
        ],
        has_budget=bool(versions),
        contractual_direct_cost=sum(contractual, Decimal(0)) if contractual else None,
    )


def _priced_items(
    rows: Sequence[tuple[Any, ...]], budget_names: dict[UUID, str], labels: dict[UUID, str]
) -> dict[UUID, PricedItem]:
    """Los ítems con precio vigente. Completa `labels` con "código descripción" (y el
    presupuesto, si el proyecto tiene más de uno) para todo lo de las versiones vigentes."""
    several = len(budget_names) > 1
    parents: dict[UUID, UUID | None] = {}
    names: dict[UUID, str] = {}
    for item_id, code, _kind, description, _unit, _price, parent_id, budget_id in rows:
        text = f"{code} {description}".strip()
        names[item_id] = f"{text} · {budget_names[budget_id]}" if several else text
        parents[item_id] = parent_id
    labels.update(names)
    items: dict[UUID, PricedItem] = {}
    for item_id, _code, kind, _description, unit, price, _parent, _budget in rows:
        if kind == BudgetRowKind.ITEM and price is not None:
            items[item_id] = PricedItem(
                item_id=item_id,
                label=names[item_id],
                unit=unit,
                unit_price=price,
                chapter=_top_chapter(item_id, parents, names),
            )
    return items


def _top_chapter(
    item_id: UUID, parents: dict[UUID, UUID | None], names: dict[UUID, str]
) -> str:
    """El capítulo de primer nivel que contiene al ítem, o `NO_CHAPTER`."""
    top, seen = item_id, {item_id}
    while (parent := parents.get(top)) is not None and parent not in seen:
        seen.add(parent)
        top = parent
    return NO_CHAPTER if top == item_id else names.get(top, NO_CHAPTER)
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control
```

Expected: PASS. Si `test_query_count_does_not_grow_with_the_data` cuenta otra cosa, leer el SQL que imprime `assertNumQueries` antes de cambiar el número: debe haber exactamente una query por fuente de la lista del docstring (ninguna por tarea, par o registro).

- [ ] **Step 5: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): load the earned value input in fixed queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Curva S

**Files:**
- Create: `server/suno/control/chart.py`
- Test: `server/suno/control/tests/test_chart.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_chart.py`:

```python
"""Coordenadas de la curva S (costos, pieza 5). Sin BD."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.chart import MAX_X_LABELS, build_s_curve
from suno.control.earned_value import PeriodRow
from suno.control.periods import Period

D = Decimal


def row(index: int, label: str, pv_cum: str, ev_cum: str | None) -> PeriodRow:
    start = date(2026, 3, 1) + timedelta(days=31 * index)
    return PeriodRow(
        period=Period(start, start + timedelta(days=30), label, ev_cum is not None),
        pv=D(0),
        pv_cum=D(pv_cum),
        ev=None if ev_cum is None else D(0),
        ev_cum=None if ev_cum is None else D(ev_cum),
    )


class SCurveTests(SimpleTestCase):
    def test_points_axis_and_cut(self) -> None:
        curve = build_s_curve([row(0, "Mar-2026", "50", "40"), row(1, "Abr-2026", "100", None)], D(100))
        assert curve is not None
        self.assertEqual(curve.axis.maximum, D(100))
        self.assertEqual(curve.pv_points, "0,100 50.00,50.00 100.00,0.00")
        self.assertEqual(curve.ev_points, "0,100 50.00,60.00")
        self.assertEqual(curve.cut_x, D("50.00"))
        self.assertEqual(
            curve.x_labels, ((D("25.00"), "Mar-2026"), (D("75.00"), "Abr-2026"))
        )
        self.assertEqual((curve.pv_last, curve.ev_last), ("$100", "$40"))

    def test_without_measured_periods(self) -> None:
        curve = build_s_curve([row(0, "Mar-2026", "50", None)], D(50))
        assert curve is not None
        self.assertEqual(curve.ev_points, "")
        self.assertIsNone(curve.cut_x)
        self.assertEqual(curve.ev_last, "")

    def test_labels_are_thinned(self) -> None:
        rows = [row(i, f"P{i}", str(i), None) for i in range(20)]
        curve = build_s_curve(rows, D(20))
        assert curve is not None
        self.assertLessEqual(len(curve.x_labels), MAX_X_LABELS)
        self.assertEqual(curve.x_labels[0][1], "P0")

    def test_no_rows_no_curve(self) -> None:
        self.assertIsNone(build_s_curve([], D(0)))
```

(El `assert` de las pruebas estrecha el tipo para mypy; las pruebas no pasan por bandit.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_chart.py`
Expected: ERROR (`No module named 'suno.control.chart'`).

- [ ] **Step 3: Implementar**

`server/suno/control/chart.py`:

```python
"""Curva S del control (costos, pieza 5). Puro: coordenadas y etiquetas ya calculadas.

Mismo estilo que los gráficos de costos de equipos (`suno.equipment.charts`): SVG con
`viewBox` 0-100 y `preserveAspectRatio="none"`, marcas y etiquetas en HTML. Los valores salen
de `Decimal` calculados, nunca de texto del usuario, así que los estilos en línea son seguros.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from suno.equipment.charts import Axis, money_axis, short_money

from .earned_value import PeriodRow

HUNDRED = Decimal(100)
HALF = Decimal("0.5")
COORD = Decimal("0.01")
#: Etiquetas del eje X como mucho: con cien semanas no caben todas.
MAX_X_LABELS = 8


@dataclass(frozen=True)
class SCurve:
    axis: Axis
    #: Puntos `x,y` del viewBox (y hacia abajo), desde el origen.
    pv_points: str
    #: Vacío si ningún periodo está medido.
    ev_points: str
    #: Posición (%) del fin del último periodo medido (la fecha de corte).
    cut_x: Decimal | None
    #: (posición % del centro del periodo, etiqueta).
    x_labels: tuple[tuple[Decimal, str], ...]
    pv_last: str
    ev_last: str


def build_s_curve(rows: Sequence[PeriodRow], bac: Decimal) -> SCurve | None:
    if not rows:
        return None
    measured: list[tuple[int, Decimal]] = []
    for index, period_row in enumerate(rows):
        if period_row.ev_cum is not None:
            measured.append((index, period_row.ev_cum))
    axis = money_axis(max([bac, *(r.pv_cum for r in rows), *(v for _, v in measured)]))
    count = Decimal(len(rows))

    def x(index: int) -> Decimal:
        return (Decimal(index + 1) / count * HUNDRED).quantize(COORD)

    def y(value: Decimal) -> Decimal:
        return (HUNDRED - value / axis.maximum * HUNDRED).quantize(COORD)

    pv_points = " ".join(["0,100", *(f"{x(i)},{y(r.pv_cum)}" for i, r in enumerate(rows))])
    ev_points = " ".join(["0,100", *(f"{x(i)},{y(v)}" for i, v in measured)]) if measured else ""
    step = -(-len(rows) // MAX_X_LABELS)  # división hacia arriba
    x_labels = tuple(
        (((Decimal(i) + HALF) / count * HUNDRED).quantize(COORD), rows[i].period.label)
        for i in range(0, len(rows), step)
    )
    return SCurve(
        axis=axis,
        pv_points=pv_points,
        ev_points=ev_points,
        cut_x=x(measured[-1][0]) if measured else None,
        x_labels=x_labels,
        pv_last=short_money(rows[-1].pv_cum),
        ev_last=short_money(measured[-1][1]) if measured else "",
    )
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control/tests/test_chart.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): S-curve coordinates for the server-rendered chart

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Libro Excel del control

**Files:**
- Create: `server/suno/control/export.py`
- Test: `server/suno/control/tests/test_export.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_export.py`:

```python
"""Excel del control (costos, pieza 5): dos hojas y celdas seguras. Sin BD."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from suno.control.earned_value import (
    ControlInput,
    Execution,
    PlanPair,
    PricedItem,
    TaskWindow,
    compute,
)
from suno.control.export import control_workbook
from suno.control.periods import WEEK
from suno.projects.workdays import WorkCalendar

D = Decimal


class ControlWorkbookTests(SimpleTestCase):
    def test_two_sheets_with_safe_cells(self) -> None:
        task = TaskWindow(
            task_id=uuid.uuid4(),
            label="=SUM(A1)",
            location="",
            start=date(2026, 3, 2),
            finish=date(2026, 3, 13),
            from_baseline=True,
            milestone=False,
        )
        item = PricedItem(
            item_id=uuid.uuid4(),
            label="1.1 Excavación",
            unit="m3",
            unit_price=D("1000"),
            chapter="1 Preliminares",
        )
        result = compute(
            ControlInput(
                cut=date(2026, 3, 31),
                kind=WEEK,
                calendar=WorkCalendar(),
                tasks={task.task_id: task},
                items={item.item_id: item},
                item_labels={item.item_id: item.label},
                plans=[PlanPair(task.task_id, item.item_id, D("100"))],
                executions=[Execution(task.task_id, item.item_id, date(2026, 3, 4), D("40"))],
            )
        )
        with zipfile.ZipFile(io.BytesIO(control_workbook(result))) as book:
            self.assertIsNone(book.testzip())
            workbook = book.read("xl/workbook.xml").decode()
            periods = book.read("xl/worksheets/sheet1.xml").decode()
            breakdown = book.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn('<sheet name="Periodos"', workbook)
        self.assertIn('<sheet name="Desglose"', workbook)
        self.assertIn(">pv_acumulado<", periods)
        self.assertIn(">02-mar-2026<", periods)
        self.assertIn(">2026-03-08<", periods)
        self.assertIn("<v>50000.0</v>", periods)
        self.assertIn(">proyecto<", breakdown)
        self.assertIn(">tarea<", breakdown)
        self.assertIn(">'=SUM(A1)<", breakdown, "un nombre con fórmula sale como texto")
        self.assertIn(">Atrasado<", breakdown)
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_export.py`
Expected: ERROR (`No module named 'suno.control.export'`).

- [ ] **Step 3: Implementar**

`server/suno/control/export.py`:

```python
"""Libro Excel del control (costos, pieza 5): hojas "Periodos" y "Desglose".

Los nombres (tareas, ítems, ubicaciones) los escribe un usuario: todo pasa por `safe_cell`
contra la inyección de fórmulas. Montos como número (`float`, a centavos) y fechas en ISO.
"""

from __future__ import annotations

from decimal import Decimal

from suno.backoffice.export import safe_cell, to_xlsx_book

from .earned_value import BreakdownRow, ControlResult

PERIOD_HEADER = [
    "periodo", "desde", "hasta", "pv_periodo", "pv_acumulado",
    "ev_periodo", "ev_acumulado", "sv", "spi",
]  # fmt: skip
BREAKDOWN_HEADER = ["desglose", "nombre", "bac", "pv", "ev", "sv", "spi", "estado"]


def _num(value: Decimal | None) -> float | None:
    """`to_xlsx` solo conoce int y float."""
    return None if value is None else float(value)


def _breakdown(kind: str, row: BreakdownRow) -> list[object]:
    measure = row.measure
    return [
        safe_cell(value)
        for value in (
            kind, row.label, _num(row.bac), _num(measure.pv), _num(measure.ev),
            _num(measure.sv), _num(measure.spi), measure.status_label,
        )
    ]  # fmt: skip


def control_workbook(result: ControlResult) -> bytes:
    periods = [
        [
            safe_cell(value)
            for value in (
                row.period.label, row.period.start.isoformat(), row.period.end.isoformat(),
                _num(row.pv), _num(row.pv_cum), _num(row.ev), _num(row.ev_cum),
                _num(row.sv), _num(row.spi),
            )
        ]  # fmt: skip
        for row in result.periods
    ]
    total = BreakdownRow("Proyecto", result.indicators.bac, result.indicators.at_cut)
    breakdown = [_breakdown("proyecto", total)]
    for kind, rows in (
        ("capítulo", result.by_chapter),
        ("tarea", result.by_task),
        ("ubicación", result.by_location),
    ):
        breakdown.extend(_breakdown(kind, row) for row in rows)
    return to_xlsx_book(
        [("Periodos", PERIOD_HEADER, periods), ("Desglose", BREAKDOWN_HEADER, breakdown)]
    )
```

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control/tests/test_export.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/suno/control
git commit -m "feat(control): excel workbook with periods and breakdown sheets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: Página "Control", URL y pestaña del proyecto

**Files:**
- Create: `server/suno/control/views.py`, `server/suno/control/urls.py`
- Create: `server/suno/core/templates/suno/control/dashboard.html`, `server/suno/core/templates/suno/control/_warnings.html`
- Modify: `server/config/urls.py` (montar `control/`)
- Modify: `server/suno/core/templates/suno/projects/_header.html` (pestaña y fecha de corte)
- Modify: `server/suno/core/templates/suno/projects/base.html` (versión del CSS)
- Modify: `server/suno/core/static/suno/css/projects.css` (al final)
- Test: `server/suno/control/tests/test_views.py`

Con el mundo de las pruebas (Task 4) y el corte del viernes 20 de marzo: la 1.2 Descapote (4 m3 de 2.1, $1.800.000) ya terminó; la 2.1 Columnas (8 m3, $3.600.000) va 5 de sus 8 días laborables (el 23 es festivo): PV = 1.800.000 + 2.250.000 = **4.050.000**; EV = 2 m3 × 450.000 = **900.000**; BAC **5.400.000**; SV −3.150.000; SPI 0,22 (rojo).

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_views.py`:

```python
"""Página "Control" del proyecto (costos, pieza 5)."""

from __future__ import annotations

import unittest

from django.test import TestCase
from django.utils import timezone

from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.matrix.tests.factories import (
    budget_world,
    item,
    locate,
    plan,
    quantity_record,
    quantity_row,
    suno_levels,
)
from suno.projects.tests.factories import make_field_world

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

AT_CUT = "?corte=2026-03-20"


@skip_without_db
class ControlPageTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        suno_levels(self.world)
        locate(self.world, 3, "Tramo 1")
        self.concrete = item(self.budget, "2.1")
        plan(self.world, 3, self.concrete, "4")
        plan(self.world, 5, self.concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(self.concrete, "2")))
        self.project_url = f"/plataforma/proyectos/{self.world.project.pk}/"
        self.base = f"{self.project_url}control/"
        self.client.force_login(self.world.admin)

    def test_field_agent_gets_404(self) -> None:
        self.world.responsible(3)
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_another_organization_gets_404(self) -> None:
        other, _ = budget_world("Otra constructora")
        self.client.force_login(other.admin)
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_tab_in_the_project_header(self) -> None:
        self.assertContains(self.client.get(self.project_url), ">Control</a>")
        self.client.force_login(self.world.supervisor)
        response = self.client.get(self.base)
        self.assertContains(response, 'aria-current="page">Control</a>')

    def test_indicators_at_the_cut(self) -> None:
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, 'value="2026-03-20"')
        self.assertContains(response, "$5.400.000,00")  # BAC
        self.assertContains(response, "$4.050.000,00")  # PV
        self.assertContains(response, "$900.000,00")  # EV
        self.assertContains(response, "-3.150.000,00")  # SV
        self.assertContains(response, "SPI 0,22")
        self.assertContains(response, "Atrasado")
        self.assertContains(response, "75,0 % del BAC")
        self.assertContains(response, "Costo directo contractual: $9.130.000,00")
        self.assertContains(response, "<polyline")

    def test_defaults_and_unknown_values(self) -> None:
        response = self.client.get(self.base + "?corte=no-es-fecha&periodo=dia&desglose=x")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{timezone.localdate().isoformat()}"')
        self.assertContains(response, '<option value="mes" selected>')
        self.assertContains(response, 'aria-current="page">Por capítulo</a>')

    def test_weekly_and_monthly_periods(self) -> None:
        weekly = self.client.get(self.base + AT_CUT + "&periodo=semana")
        self.assertContains(weekly, "09-mar-2026")
        self.assertContains(weekly, "16-mar-2026 (tras el corte)")
        monthly = self.client.get(self.base + AT_CUT + "&periodo=mes")
        self.assertContains(monthly, "Mar-2026")
        self.assertNotContains(monthly, "09-mar-2026")

    def test_breakdown_tabs(self) -> None:
        by_chapter = self.client.get(self.base + AT_CUT)
        self.assertContains(by_chapter, "2 Estructura")
        self.assertContains(by_chapter, "desglose=tarea")
        by_task = self.client.get(self.base + AT_CUT + "&desglose=tarea")
        task = self.world.task(5)
        self.assertContains(by_task, f'href="{self.project_url}tareas/{task.pk}/"')
        self.assertContains(by_task, "2.1 Columnas")
        by_location = self.client.get(self.base + AT_CUT + "&desglose=ubicacion")
        self.assertContains(by_location, "Tramo 1")
        self.assertContains(by_location, "Sin ubicación")

    def test_warnings(self) -> None:
        steel = item(self.budget, "2.2")
        self.world.approve(quantity_record(self.world, 3, quantity_row(steel, "10"), hours=1))
        response = self.client.get(self.base + AT_CUT)
        self.assertContains(response, "Ejecutado fuera del plan (1)")
        self.assertContains(response, "2.2 Acero de refuerzo")
        self.assertContains(response, "Tareas medidas por % (1)")
        self.assertContains(response, "1.1 Localización")

    def test_project_without_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/")
        self.assertContains(response, "no tiene un presupuesto aplicado")
        self.assertNotContains(response, "<polyline")

    def test_project_without_plan(self) -> None:
        world, _ = budget_world("Sin plan")
        self.client.force_login(world.admin)
        response = self.client.get(f"/plataforma/proyectos/{world.project.pk}/control/")
        self.assertContains(response, "no tiene cantidades planeadas")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_views.py`
Expected: FAIL (404 en la URL de control; `NoReverseMatch` no aparece todavía porque la cabecera aún no enlaza).

- [ ] **Step 3: Vistas**

`server/suno/control/views.py`:

```python
"""Página "Control" del proyecto (costos, pieza 5): valor planeado, valor ganado y curva S.

Mismo acceso que la Matriz (`get_matrix_project`: quien ve presupuestos; el agente de campo
recibe 404). Exportar pide además `can_export_budgets` y queda en la auditoría (Task 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from suno.core.policies import can_export_budgets
from suno.matrix.access import get_matrix_project
from suno.projects.views import _ctx, _parse_date, _project_context, _render

from .chart import build_s_curve
from .earned_value import BreakdownRow, ControlResult, compute
from .loader import load_control_input
from .periods import DEFAULT_KIND, PERIOD_KINDS, PeriodRangeError

BREAKDOWN_TABS = {"capitulo": "Por capítulo", "tarea": "Por tarea", "ubicacion": "Por ubicación"}
DEFAULT_TAB = "capitulo"


@dataclass(frozen=True)
class Options:
    cut: date
    kind: str
    tab: str

    @property
    def query(self) -> str:
        """Corte y periodo, para las pestañas del desglose y la exportación."""
        return urlencode({"corte": self.cut.isoformat(), "periodo": self.kind})


def _options(request: HttpRequest) -> Options:
    kind = request.GET.get("periodo", "")
    tab = request.GET.get("desglose", "")
    return Options(
        cut=_parse_date(request.GET.get("corte")) or timezone.localdate(),
        kind=kind if kind in PERIOD_KINDS else DEFAULT_KIND,
        tab=tab if tab in BREAKDOWN_TABS else DEFAULT_TAB,
    )


def _breakdown(result: ControlResult, tab: str) -> tuple[BreakdownRow, ...]:
    if tab == "tarea":
        return result.by_task
    if tab == "ubicacion":
        return result.by_location
    return result.by_chapter


@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def dashboard(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    options = _options(request)
    result: ControlResult | None = None
    error = ""
    try:
        result = compute(load_control_input(ctx, project, cut=options.cut, kind=options.kind))
    except PeriodRangeError as exc:
        error = str(exc)
    return _render(
        request,
        ctx,
        "suno/control/dashboard.html",
        {
            **_project_context(ctx, project),
            "header_cut": options.cut,
            "options": options,
            "period_kinds": PERIOD_KINDS,
            "tabs": BREAKDOWN_TABS,
            "result": result,
            "rows": _breakdown(result, options.tab) if result else (),
            "curve": build_s_curve(result.periods, result.indicators.bac) if result else None,
            "can_export_control": can_export_budgets(ctx, ctx.org_id),
            "error": error,
        },
    )
```

`server/suno/control/urls.py`:

```python
"""Montadas bajo `plataforma/proyectos/<uuid:project_id>/control/` (config/urls.py)."""

from django.urls import path

from . import views

app_name = "control"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
]
```

En `server/config/urls.py`, después del bloque de `matriz/`:

```python
    path(
        "plataforma/proyectos/<uuid:project_id>/control/",
        include("suno.control.urls"),
    ),
```

- [ ] **Step 4: Plantillas**

`server/suno/core/templates/suno/control/dashboard.html`:

```django
{% extends "suno/projects/base.html" %}
{% block title %}Control · {{ project.name }}{% endblock %}
{% block content %}
{% include "suno/projects/_header.html" with tab="control" %}
<section class="pj-panel">
  <form method="get" class="pj-filters" aria-label="Fecha de corte y periodos">
    <label>Fecha de corte <input type="date" name="corte" value="{{ options.cut|date:'Y-m-d' }}"></label>
    <label>Periodos <select name="periodo">{% for key, label in period_kinds.items %}<option value="{{ key }}" {% if options.kind == key %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></label>
    <input type="hidden" name="desglose" value="{{ options.tab }}">
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
<section class="metrics-strip ctl-metrics" aria-label="Indicadores a la fecha de corte">
  <article class="bg-money"><span>BAC vigente</span><strong>${{ ind.bac|floatformat:"2g" }}</strong><small>{% if ind.contractual_direct_cost is not None %}Costo directo contractual: ${{ ind.contractual_direct_cost|floatformat:"2g" }}{% else %}Sin versión contractual{% endif %}</small></article>
  <article class="bg-money"><span>Valor planeado (PV)</span><strong>${{ m.pv|floatformat:"2g" }}</strong><small>{% if ind.pv_share is not None %}{{ ind.pv_share|floatformat:1 }} % del BAC{% endif %}</small></article>
  <article class="bg-money"><span>Valor ganado (EV)</span><strong>${{ m.ev|floatformat:"2g" }}</strong><small>{% if ind.ev_share is not None %}{{ ind.ev_share|floatformat:1 }} % del BAC{% endif %}</small></article>
  <article class="bg-money"><span>Variación del cronograma (SV)</span><strong>${{ m.sv|floatformat:"2g" }}</strong><small><b class="bg-label ctl-{{ m.status }}">SPI {% if m.spi is not None %}{{ m.spi|floatformat:2 }}{% else %}—{% endif %} · {{ m.status_label }}</b></small></article>
</section>
{% endwith %}

<figure class="pj-panel ctl-figure" aria-labelledby="ctl-curve-title">
  <figcaption id="ctl-curve-title"><strong>Curva S: valor planeado y valor ganado acumulados</strong></figcaption>
  <ul class="ctl-legend" aria-label="Leyenda">
    <li><i class="ctl-key"></i> Valor planeado (PV) · {{ curve.pv_last }}</li>
    <li><i class="ctl-key ctl-key-ev"></i> Valor ganado (EV){% if curve.ev_last %} · {{ curve.ev_last }}{% endif %}</li>
    <li><i class="ctl-key ctl-key-cut"></i> Fecha de corte</li>
  </ul>
  <div class="ctl-chart">
    <div class="ctl-ticks" aria-hidden="true">{% for pos, label in curve.axis.ticks %}<span style="bottom: {{ pos|stringformat:'s' }}%">{{ label }}</span>{% endfor %}</div>
    <div class="ctl-plot" role="img" aria-label="Curva S del valor planeado y del valor ganado acumulados. Los números están en la tabla de la curva.">
      {% for pos, label in curve.axis.ticks %}<i class="ctl-grid" style="bottom: {{ pos|stringformat:'s' }}%"></i>{% endfor %}
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {% if curve.cut_x is not None %}<line class="ctl-cut" x1="{{ curve.cut_x|stringformat:'s' }}" x2="{{ curve.cut_x|stringformat:'s' }}" y1="0" y2="100"/>{% endif %}
        <polyline class="ctl-pv" points="{{ curve.pv_points }}"/>
        {% if curve.ev_points %}<polyline class="ctl-ev" points="{{ curve.ev_points }}"/>{% endif %}
      </svg>
    </div>
    <div class="ctl-xlabels" aria-hidden="true">{% for pos, label in curve.x_labels %}<span style="left: {{ pos|stringformat:'s' }}%">{{ label }}</span>{% endfor %}</div>
  </div>
  <p class="pj-muted ctl-source">Eje vertical: {{ curve.axis.unit }}. PV repartido por días laborables de la línea base; EV con las cantidades validadas hasta el {{ result.cut|date:"d/m/Y" }}. Precios del presupuesto vigente.</p>
</figure>

<details class="pj-panel ctl-table">
  <summary>Tabla de la curva ({{ result.periods|length }} periodos)</summary>
  <div class="pj-scroll">
  <table class="pj-table">
    <caption class="sr-only">Valor planeado y valor ganado por periodo</caption>
    <thead><tr><th>Periodo</th><th>Hasta</th><th class="num">PV del periodo</th><th class="num">PV acumulado</th><th class="num">EV del periodo</th><th class="num">EV acumulado</th><th class="num">SV</th><th class="num">SPI</th></tr></thead>
    <tbody>
    {% for row in result.periods %}
      <tr{% if not row.period.measured %} class="pj-muted"{% endif %}>
        <td>{{ row.period.label }}</td>
        <td>{{ row.period.end|date:"d/m/Y" }}</td>
        <td class="num">{{ row.pv|floatformat:"2g" }}</td>
        <td class="num">{{ row.pv_cum|floatformat:"2g" }}</td>
        <td class="num">{% if row.ev is not None %}{{ row.ev|floatformat:"2g" }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.ev_cum is not None %}{{ row.ev_cum|floatformat:"2g" }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.sv is not None %}{{ row.sv|floatformat:"2g" }}{% else %}—{% endif %}</td>
        <td class="num">{% if row.spi is not None %}{{ row.spi|floatformat:2 }}{% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</details>

<section class="pj-panel" aria-labelledby="ctl-breakdown-title">
  <h2 id="ctl-breakdown-title">Desglose a la fecha de corte</h2>
  <nav class="pj-tabs ctl-tabs" aria-label="Desglose">
    {% for key, label in tabs.items %}<a href="?{{ options.query }}&amp;desglose={{ key }}" {% if options.tab == key %}aria-current="page"{% endif %}>{{ label }}</a>{% endfor %}
  </nav>
  <p class="pj-muted">De peor a mejor SPI; sin valor planeado a la fecha, al final.</p>
  <div class="pj-scroll">
  <table class="pj-table ctl-breakdown">
    <thead><tr><th>{% if options.tab == "tarea" %}Tarea{% elif options.tab == "ubicacion" %}Ubicación{% else %}Capítulo{% endif %}</th><th class="num">BAC</th><th class="num">PV</th><th class="num">EV</th><th class="num">SV</th><th class="num">SPI</th><th>Estado</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>
        <td class="pj-name">{% if row.task_id %}<a href="{% url 'projects:task' project.pk row.task_id %}">{{ row.label }}</a>{% else %}{{ row.label }}{% endif %}</td>
        <td class="num">{{ row.bac|floatformat:"2g" }}</td>
        <td class="num">{{ row.measure.pv|floatformat:"2g" }}</td>
        <td class="num">{{ row.measure.ev|floatformat:"2g" }}</td>
        <td class="num">{{ row.measure.sv|floatformat:"2g" }}</td>
        <td class="num">{% if row.measure.spi is not None %}{{ row.measure.spi|floatformat:2 }}{% else %}—{% endif %}</td>
        <td><span class="bg-label ctl-{{ row.measure.status }}">{{ row.measure.status_label }}</span></td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endif %}
{% include "suno/control/_warnings.html" with w=result.warnings %}
{% endif %}
{% endblock %}
```

`server/suno/core/templates/suno/control/_warnings.html`:

```django
{% if w.count %}
<section class="pj-panel" aria-labelledby="ctl-warnings-title">
  <h2 id="ctl-warnings-title">Avisos</h2>
  {% if w.no_baseline %}
  <details class="ctl-warning"><summary>Tareas sin línea base ({{ w.no_baseline|length }}): su valor planeado usa las fechas actuales</summary>
    <ul>{% for label in w.no_baseline %}<li>{{ label }}</li>{% endfor %}</ul>
  </details>
  {% endif %}
  {% if w.undated %}
  <details class="ctl-warning"><summary>Tareas sin fechas ({{ w.undated|length }}): no suman al control</summary>
    <ul>{% for label in w.undated %}<li>{{ label }}</li>{% endfor %}</ul>
  </details>
  {% endif %}
  {% if w.unpriced %}
  <details class="ctl-warning"><summary>Ítems sin precio vigente ({{ w.unpriced|length }}): no suman al control</summary>
    <div class="pj-scroll"><table class="pj-table">
      <thead><tr><th>Tarea</th><th>Ítem</th><th class="num">Planeado</th><th class="num">Ejecutado</th></tr></thead>
      <tbody>{% for r in w.unpriced %}<tr><td class="pj-wrap">{{ r.task }}</td><td class="pj-wrap">{{ r.item }}</td><td class="num">{{ r.planned|floatformat:"-4g" }}</td><td class="num">{{ r.executed|floatformat:"-4g" }}</td></tr>{% endfor %}</tbody>
    </table></div>
  </details>
  {% endif %}
  {% if w.overrun %}
  <details class="ctl-warning"><summary>Sobreejecución ({{ w.overrun|length }}): el exceso sobre lo planeado no entra al valor ganado</summary>
    <div class="pj-scroll"><table class="pj-table">
      <thead><tr><th>Tarea</th><th>Ítem</th><th>Und</th><th class="num">Planeado</th><th class="num">Ejecutado</th><th class="num">Exceso</th><th class="num">Valor del exceso ($)</th></tr></thead>
      <tbody>{% for r in w.overrun %}<tr><td class="pj-wrap">{{ r.task }}</td><td class="pj-wrap">{{ r.item }}</td><td>{{ r.unit }}</td><td class="num">{{ r.planned|floatformat:"-4g" }}</td><td class="num">{{ r.executed|floatformat:"-4g" }}</td><td class="num">{{ r.excess|floatformat:"-4g" }}</td><td class="num">{{ r.value|floatformat:"2g" }}</td></tr>{% endfor %}</tbody>
    </table></div>
  </details>
  {% endif %}
  {% if w.outside_plan %}
  <details class="ctl-warning"><summary>Ejecutado fuera del plan ({{ w.outside_plan|length }}): ítems sin cantidad planeada en su tarea</summary>
    <div class="pj-scroll"><table class="pj-table">
      <thead><tr><th>Tarea</th><th>Ítem</th><th>Und</th><th class="num">Cantidad</th><th class="num">Valor ($)</th></tr></thead>
      <tbody>{% for r in w.outside_plan %}<tr><td class="pj-wrap">{{ r.task }}</td><td class="pj-wrap">{{ r.item }}</td><td>{{ r.unit }}</td><td class="num">{{ r.quantity|floatformat:"-4g" }}</td><td class="num">{% if r.value is not None %}{{ r.value|floatformat:"2g" }}{% else %}sin precio vigente{% endif %}</td></tr>{% endfor %}</tbody>
    </table></div>
  </details>
  {% endif %}
  {% if w.percent_tasks %}
  <details class="ctl-warning"><summary>Tareas medidas por % ({{ w.percent_tasks|length }}): sin plan en la matriz, no tienen valor planeado ni ganado</summary>
    <div class="pj-scroll"><table class="pj-table">
      <thead><tr><th>Tarea</th><th class="num">Último % validado</th></tr></thead>
      <tbody>{% for r in w.percent_tasks %}<tr><td class="pj-wrap">{{ r.label }}</td><td class="num">{% if r.pct is not None %}{{ r.pct|floatformat:"-2" }} %{% else %}sin registro validado{% endif %}</td></tr>{% endfor %}</tbody>
    </table></div>
  </details>
  {% endif %}
</section>
{% endif %}
```

- [ ] **Step 5: Cabecera del proyecto**

En `server/suno/core/templates/suno/projects/_header.html`, después de la línea del enlace "Matriz":

```django
      {% if can_view_budgets %}<a href="{% url 'control:dashboard' project.pk %}" {% if tab == "control" %}aria-current="page"{% endif %}>Control</a>{% endif %}
```

y reemplazar la línea de `workspace-scope` por:

```django
  <p class="workspace-scope">Fecha de corte<br><span>{% if header_cut %}{{ header_cut|date:"d/m/Y" }}{% elif schedule %}{{ schedule.cut|date:"d/m/Y" }}{% else %}{{ project.status_date|date:"d/m/Y"|default:"sin importar" }}{% endif %}</span></p>
```

En `server/suno/core/templates/suno/projects/base.html`, cambiar la versión de `projects.css` de `?v=20261108-topo-v1` a `?v=20261111-control-v1` (los navegadores no deben seguir con el CSS viejo).

- [ ] **Step 6: Estilos**

Al final de `server/suno/core/static/suno/css/projects.css`:

```css
/* Control con valor ganado (costos, pieza 5). */
.ctl-metrics { margin: 24px 0 0; }
.bg-label.ctl-green { background: var(--suno-green); }
.bg-label.ctl-amber { background: var(--suno-amber); color: var(--suno-ink); }
.bg-label.ctl-red { background: var(--suno-red); }
.bg-label.ctl-none { background: var(--suno-line-strong); color: var(--suno-ink); }
.metrics-strip small .bg-label { margin: 4px 0 0; font-size: 11px; font-weight: 600; }
.ctl-figure { display: grid; gap: 12px; margin-inline: 0; }
.ctl-figure figcaption { font-size: 14px; }
.ctl-legend { display: flex; flex-wrap: wrap; gap: 6px 22px; margin: 0; padding: 0; list-style: none; font-size: 12px; }
.ctl-legend li { display: inline-flex; align-items: center; gap: 8px; }
.ctl-key { display: inline-block; width: 24px; height: 0; border-top: 2px solid var(--chart-graphite); }
.ctl-key-ev { border-top: 3px solid var(--chart-red); }
.ctl-key-cut { width: 0; height: 14px; border-top: 0; border-left: 2px dashed var(--chart-amber); }
.ctl-chart { display: grid; grid-template-columns: 44px minmax(0, 1fr); grid-template-rows: 260px auto; column-gap: 8px; row-gap: 6px; }
.ctl-ticks { position: relative; font-size: 11px; font-variant-numeric: tabular-nums; }
.ctl-ticks span { position: absolute; right: 0; transform: translateY(50%); white-space: nowrap; }
.ctl-plot { position: relative; border-left: 1px solid var(--suno-line-strong); border-bottom: 1px solid var(--suno-line-strong); }
.ctl-grid { position: absolute; left: 0; right: 0; height: 0; border-top: 1px solid var(--suno-line); }
.ctl-plot svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.ctl-plot polyline { fill: none; stroke-width: 2; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.ctl-plot .ctl-pv { stroke: var(--chart-graphite); }
.ctl-plot .ctl-ev { stroke: var(--chart-red); stroke-width: 3; }
.ctl-plot .ctl-cut { stroke: var(--chart-amber); stroke-width: 2; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; }
.ctl-xlabels { grid-column: 2; position: relative; height: 18px; font-size: 11px; }
.ctl-xlabels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.ctl-source { margin: 0; font-size: 11px; }
.ctl-table summary, .ctl-warning summary { cursor: pointer; font-size: 13px; font-weight: 600; }
.ctl-table[open] summary { margin-bottom: 12px; }
.ctl-warning { padding: 8px 0; border-top: 1px solid var(--suno-line); }
.ctl-warning ul { margin: 8px 0 0; padding-left: 20px; font-size: 13px; }
.ctl-warning .pj-scroll { margin-top: 8px; }
.ctl-tabs { margin: 0 0 8px; }
.ctl-breakdown td.pj-name { min-width: 220px; max-width: 360px; }
@media (max-width: 767px) {
  .ctl-chart { grid-template-columns: 36px minmax(0, 1fr); grid-template-rows: 200px auto; }
  .ctl-xlabels span:nth-child(even) { display: none; }
  .ctl-breakdown td.pj-name { min-width: 160px; max-width: 200px; }
}
```

- [ ] **Step 7: Una URL de exportación provisional**

La plantilla ya enlaza `control:export` (Task 8). Para que las pruebas de esta Task pasen sin `NoReverseMatch`, agregar ya la ruta en `server/suno/control/urls.py`:

```python
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("control.xlsx", views.export, name="export"),
]
```

y en `server/suno/control/views.py`, al final, la vista mínima (la Task 8 la completa):

```python
@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def export(request: HttpRequest, project_id: UUID) -> HttpResponse:
    ctx = _ctx(request)
    get_matrix_project(ctx, project_id)
    return HttpResponse(status=501)
```

- [ ] **Step 8: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control config && ruff check suno/control config && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control suno/projects suno/matrix suno/budgets
```

Expected: PASS (las páginas del cronograma, la matriz y los presupuestos siguen pasando con la pestaña nueva).

- [ ] **Step 9: Commit**

```bash
git add server/config/urls.py server/suno/control server/suno/core/templates server/suno/core/static/suno/css/projects.css
git commit -m "feat(control): project control page with S-curve, breakdown and warnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Exportar a Excel, con auditoría

**Files:**
- Modify: `server/suno/core/audit.py` (`AuditAction`)
- Modify: `server/suno/control/views.py` (vista `export`)
- Test: `server/suno/control/tests/test_views.py` (clase nueva al final)

- [ ] **Step 1: Escribir las pruebas que fallan**

Imports nuevos en `server/suno/control/tests/test_views.py`:

```python
import io
import zipfile

from suno.core.models import AuditEvent
```

Y al final del archivo:

```python
@skip_without_db
class ControlExportTests(TestCase):
    def setUp(self) -> None:
        self.world, self.budget = budget_world()
        concrete = item(self.budget, "2.1")
        plan(self.world, 3, concrete, "4")
        plan(self.world, 5, concrete, "8")
        self.world.approve(quantity_record(self.world, 5, quantity_row(concrete, "2")))
        self.url = f"/plataforma/proyectos/{self.world.project.pk}/control/control.xlsx"
        self.client.force_login(self.world.supervisor)

    def test_export_has_both_sheets_and_is_audited(self) -> None:
        response = self.client.get(self.url + "?corte=2026-03-20&periodo=semana")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("2026-03-20", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as book:
            periods = book.read("xl/worksheets/sheet1.xml").decode()
            breakdown = book.read("xl/worksheets/sheet2.xml").decode()
        self.assertIn(">09-mar-2026<", periods)
        self.assertIn(">2.1 Columnas<", breakdown)
        event = AuditEvent.objects.get(action="control.exported")
        self.assertEqual(event.target, f"project:{self.world.project.pk}")
        self.assertEqual(event.metadata, {"cut": "2026-03-20", "period": "semana"})

    def test_link_on_the_page(self) -> None:
        page = self.client.get(f"/plataforma/proyectos/{self.world.project.pk}/control/")
        self.assertContains(page, "Exportar a Excel")
        self.assertContains(page, "control.xlsx?corte=")

    def test_field_agent_gets_404(self) -> None:
        self.client.force_login(self.world.agent)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(AuditEvent.objects.filter(action="control.exported").exists())
```

(Todos los roles que leen presupuestos tienen hoy `EXPORT` en la matriz de capacidades, así que no hay un rol con lectura y sin exportación para probar el 403; la condición queda en la vista igual que en `budgets.views.export_comparison`.)

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_views.py`
Expected: FAIL en `test_export_has_both_sheets_and_is_audited` (501).

- [ ] **Step 3: Código de auditoría**

En `server/suno/core/audit.py`, al final de `AuditAction` (después de `MATRIX_TASK_LOCATION_CHANGED`):

```python
    # Control con valor ganado (costos, pieza 5).
    CONTROL_EXPORTED = "control.exported"
```

- [ ] **Step 4: La vista**

En `server/suno/control/views.py`, reemplazar la vista provisional `export` por:

```python
@login_required(login_url="platform_login")
@require_http_methods(["GET"])
def export(request: HttpRequest, project_id: UUID) -> HttpResponse:
    """Periodos y desglose del corte, en Excel (auditado, como la comparación del presupuesto)."""
    ctx = _ctx(request)
    project = get_matrix_project(ctx, project_id)
    if not can_export_budgets(ctx, ctx.org_id):
        raise PermissionDenied
    options = _options(request)
    try:
        result = compute(load_control_input(ctx, project, cut=options.cut, kind=options.kind))
    except PeriodRangeError as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain; charset=utf-8")
    content = control_workbook(result)
    with transaction.atomic():
        audit.record(
            ctx,
            action=AuditAction.CONTROL_EXPORTED,
            target=f"project:{project.pk}",
            metadata={"cut": options.cut.isoformat(), "period": options.kind},
        )
    response = HttpResponse(content, content_type=XLSX_TYPE)
    response["Content-Disposition"] = content_disposition(
        f"control-{project.code or project.name}-{options.cut.isoformat()}",
        ".xlsx",
        fallback="control",
    )
    return response
```

Imports nuevos del módulo:

```python
from django.core.exceptions import PermissionDenied
from django.db import transaction

from suno.core import audit
from suno.core.audit import AuditAction
from suno.core.download import content_disposition

from .export import control_workbook
```

Y la constante, junto a `BREAKDOWN_TABS`:

```python
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
```

Actualizar el docstring del módulo: "(Task 8)" → "(`control.exported`)".

- [ ] **Step 5: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control suno/core/audit.py && ruff check suno/control suno/core && mypy suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control suno/core/tests
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/suno/control server/suno/core/audit.py
git commit -m "feat(control): audited excel export of the control page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Prueba de carga (~5000 pares en menos de 1 s)

**Files:**
- Test: `server/suno/control/tests/test_performance.py`

El repo no tiene un patrón de prueba de rendimiento con reloj (la de carga de sincronización es un script aparte en `infra/load/`). Esta prueba sigue el de las demás pruebas con BD: crea los datos con `bulk_create` en `setUpTestData` (fuera de la medición) y mide con `time.perf_counter` la **segunda** petición de la página (la primera calienta plantillas y conexión).

- [ ] **Step 1: Escribir la prueba**

`server/suno/control/tests/test_performance.py`:

```python
"""Prueba de carga del control (spec, sección 3): ~5000 pares tarea × ítem en menos de 1 s.

500 tareas × 10 ítems = 5000 pares; 1000 registros validados de 5 filas; periodos semanales
(los más) y desglose por tarea (la tabla más larga)."""

from __future__ import annotations

import itertools
import time
import unittest
import uuid
from datetime import UTC, date, datetime, time as day_time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from suno.core.models import (
    Budget,
    BudgetItem,
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    QuantityPlan,
    Record,
    RecordReview,
    RecordReviewStatus,
    Task,
)
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.projects.tests.factories import FieldWorld, make_field_world, make_record

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")

TASKS = 500
ITEMS_PER_TASK = 10
CHAPTERS = 5
ITEMS_PER_CHAPTER = 10
RECORDS_PER_TASK = 2
ROWS_PER_RECORD = 5
LIMIT_SECONDS = 1.0
FIRST_START = date(2025, 1, 6)


def build_large_project(world: FieldWorld) -> None:
    org, project, admin = world.org, world.project, world.admin
    budget = Budget.objects.create(
        org=org, project=project, name="Carga", code="CG", created_by=admin
    )
    version = BudgetVersion.objects.create(
        org=org,
        budget=budget,
        number=1,
        status=BudgetVersionStatus.APPLIED,
        file_name="carga.xlsx",
        source_file=b"",
        uploaded_by=admin,
        applied_at=timezone.now(),
    )
    chapters = BudgetItem.objects.bulk_create(
        [BudgetItem(org=org, budget=budget, code=str(c)) for c in range(1, CHAPTERS + 1)]
    )
    items = BudgetItem.objects.bulk_create(
        [
            BudgetItem(org=org, budget=budget, code=f"{c}.{i}")
            for c in range(1, CHAPTERS + 1)
            for i in range(1, ITEMS_PER_CHAPTER + 1)
        ]
    )
    rows = [
        BudgetItemVersion(
            org=org,
            version=version,
            item=chapter,
            kind=BudgetRowKind.CHAPTER,
            sort_order=n,
            description=f"Capítulo {chapter.code}",
            total=Decimal(0),
        )
        for n, chapter in enumerate(chapters)
    ]
    rows += [
        BudgetItemVersion(
            org=org,
            version=version,
            item=budget_item,
            kind=BudgetRowKind.ITEM,
            sort_order=100 + n,
            description=f"Ítem {budget_item.code}",
            unit="m3",
            quantity=Decimal(1000),
            unit_price=Decimal(1000 + n),
            total=Decimal(1000 * (1000 + n)),
            parent=chapters[n // ITEMS_PER_CHAPTER],
        )
        for n, budget_item in enumerate(items)
    ]
    BudgetItemVersion.objects.bulk_create(rows)
    tasks = Task.objects.bulk_create(
        [
            Task(
                org=org,
                project=project,
                uid=1000 + n,
                wbs=f"9.{n}",
                outline_number=f"9.{n}",
                outline_level=2,
                sort_order=1000 + n,
                name=f"Tarea de carga {n}",
                start=FIRST_START + timedelta(days=n),
                finish=FIRST_START + timedelta(days=n + 60),
                baseline_start=FIRST_START + timedelta(days=n),
                baseline_finish=FIRST_START + timedelta(days=n + 60),
            )
            for n in range(TASKS)
        ]
    )
    planned: dict[uuid.UUID, list[BudgetItem]] = {}
    plans = []
    for n, task in enumerate(tasks):
        own = [items[(n * 7 + k) % len(items)] for k in range(ITEMS_PER_TASK)]
        planned[task.pk] = own
        plans += [
            QuantityPlan(
                org=org,
                project=project,
                task=task,
                item=budget_item,
                planned_quantity=Decimal(100),
                updated_by=admin,
            )
            for budget_item in own
        ]
    QuantityPlan.objects.bulk_create(plans)
    # Sin tarea: solo da la plantilla y el dispositivo que reutilizan los registros de carga.
    seed = make_record(org, world.agent)
    sequence = itertools.count(10_000_000)
    records = [
        Record(
            id=uuid.uuid4(),
            org=org,
            change_seq=next(sequence),
            template_version_id=seed.template_version_id,
            answers={},
            captured_at=datetime.combine(
                FIRST_START + timedelta(days=n + 10 + 20 * r), day_time(15), tzinfo=UTC
            ),
            device_id=seed.device_id,
            created_by=world.agent,
            task=task,
            quantities=[
                {
                    "item_id": str(budget_item.pk),
                    "location_id": None,
                    "quantity": "30.0000",
                    "note": "",
                }
                for budget_item in planned[task.pk][:ROWS_PER_RECORD]
            ],
        )
        for n, task in enumerate(tasks)
        for r in range(RECORDS_PER_TASK)
    ]
    Record.objects.bulk_create(records)
    RecordReview.objects.bulk_create(
        [
            RecordReview(
                org=org,
                record=record,
                status=RecordReviewStatus.APPROVED,
                reviewer=world.supervisor,
            )
            for record in records
        ]
    )


@skip_without_db
class ControlLoadTests(TestCase):
    world: FieldWorld

    @classmethod
    def setUpTestData(cls) -> None:
        cls.world = make_field_world("Carga del control")
        build_large_project(cls.world)

    def test_page_with_5000_pairs_renders_under_a_second(self) -> None:
        self.assertEqual(
            QuantityPlan.objects.filter(project=self.world.project).count(),
            TASKS * ITEMS_PER_TASK,
        )
        self.client.force_login(self.world.admin)
        url = (
            f"/plataforma/proyectos/{self.world.project.pk}/control/"
            "?corte=2026-01-30&periodo=semana&desglose=tarea"
        )
        self.assertEqual(self.client.get(url).status_code, 200)  # calienta
        started = time.perf_counter()
        response = self.client.get(url)
        elapsed = time.perf_counter() - started
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tarea de carga 499")
        self.assertLess(elapsed, LIMIT_SECONDS, f"la página tardó {elapsed:.3f} s")
```

Notas: el registro semilla va **sin tarea** porque uno con tarea y sin % ni cantidades lo rechaza el `CHECK record_task_progress_pair`. El corte (30 de enero de 2026) cae a mitad del proyecto: todas las tareas tienen PV, la mayoría EV y una parte queda después del corte.

- [ ] **Step 2: Correr**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_performance.py`
Expected: PASS. Anotar el tiempo medido (agregar temporalmente `print(f"{elapsed:.3f}")` si hace falta y quitarlo antes del commit) para el registro de avance.

- [ ] **Step 3: Si pasa de 1 s**

No subir `LIMIT_SECONDS` sin aprobación del usuario. Perfilar con `cProfile` dentro del contenedor (`infra/scripts/test_docker.sh --cmd "python -m cProfile -s cumtime -m pytest -q suno/control/tests/test_performance.py | head -60"`) y corregir lo que domine: lo esperable es la plantilla del desglose (500 filas) o `count_inclusive` (ya es aritmético); el cálculo no debe hacer trabajo por par × periodo.

- [ ] **Step 4: Formato y commit**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/control && ruff check suno/control && mypy suno/control --config-file pyproject.toml"
git add server/suno/control/tests/test_performance.py
git commit -m "test(control): load test with 5000 task-item pairs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Datos de ejemplo para la revisión visual (`seed_control_demo`)

**Files:**
- Create: `server/suno/management/commands/seed_control_demo.py`
- Modify: `server/suno/core/tests/test_architecture.py` (`ALLOWED` y `test_the_allowlist_is_exactly_the_documented_one`)
- Test: `server/suno/control/tests/test_seed_demo.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

`server/suno/control/tests/test_seed_demo.py`:

```python
"""`seed_control_demo` siembra un proyecto que la página "Control" puede mostrar entero."""

from __future__ import annotations

import unittest
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from suno.authn.tests.factories import make_org, make_user
from suno.control.earned_value import compute
from suno.control.loader import load_control_input
from suno.control.periods import MONTH
from suno.core.context import Principal, resolve_request_context
from suno.core.models import Project, Role
from suno.core.tests.dbsupport import (
    SKIP_REASON,
    setup_test_database,
    teardown_test_database,
)
from suno.management.commands.seed_control_demo import PROJECT_NAME

setUpModule = setup_test_database
tearDownModule = teardown_test_database
skip_without_db = unittest.skipUnless(SKIP_REASON is None, SKIP_REASON or "")


@skip_without_db
class SeedControlDemoTests(TestCase):
    def setUp(self) -> None:
        self.org = make_org("Org demo control")
        self.admin = make_user(self.org, username="admin-control", role=Role.ADMIN)

    def seed(self, *flags: str) -> None:
        call_command(
            "seed_control_demo",
            "--org-name=Org demo control",
            "--allow-production",
            *flags,
            stdout=StringIO(),
        )

    def test_seeds_every_case_of_the_page(self) -> None:
        self.seed()
        project = Project.objects.get(org=self.org, name=PROJECT_NAME)
        ctx = resolve_request_context(Principal(user_id=self.admin.pk, org_id=self.org.pk))
        result = compute(
            load_control_input(ctx, project, cut=timezone.localdate(), kind=MONTH)
        )
        self.assertTrue(result.has_budget and result.has_plan)
        self.assertGreater(result.indicators.bac, Decimal(0))
        self.assertGreater(result.indicators.at_cut.ev, Decimal(0))
        self.assertGreater(len(result.periods), 6)
        self.assertIsNotNone(result.indicators.contractual_direct_cost)
        warnings = result.warnings
        self.assertEqual(warnings.no_baseline, ("4.2 Cámaras de inspección",))
        self.assertEqual(len(warnings.overrun), 1)
        self.assertEqual(len(warnings.outside_plan), 1)
        self.assertEqual(
            [(p.label, p.pct) for p in warnings.percent_tasks],
            [("1.3 Trámites y permisos", Decimal("60.00"))],
        )
        self.assertEqual({r.label for r in result.by_location}, {"Frente norte", "Frente sur"})

    def test_refuses_to_seed_twice(self) -> None:
        self.seed()
        with self.assertRaises(CommandError):
            self.seed()
        self.seed("--project-name=Otra obra de ejemplo")

    def test_needs_an_existing_organization(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "seed_control_demo", "--org-name=No existe", "--allow-production", stdout=StringIO()
            )
```

En `server/suno/core/tests/test_architecture.py`, agregar a `ALLOWED` (después de `seed_equipment_demo.py`):

```python
    # Datos de ejemplo de la página "Control" (costos, pieza 5): misma razón que el anterior.
    "suno/management/commands/seed_control_demo.py": "siembra offline del ejemplo del control",
```

y al conjunto de `test_the_allowlist_is_exactly_the_documented_one`:

```python
                "suno/management/commands/seed_control_demo.py",
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py suno/core/tests/test_architecture.py`
Expected: FAIL (`Unknown command: 'seed_control_demo'`; `test_every_allowlisted_path_still_exists` falla porque el archivo no existe).

- [ ] **Step 3: Implementar**

`server/suno/management/commands/seed_control_demo.py`:

```python
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
            id=uuid.uuid4(), org=self.org, owner=self.admin, platform="android", app_version="0.16.0"
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
        self._task(project, str(len(STAGES) + 1), "Entrega de obra", start=end, finish=end, milestone=True)
        return tasks

    def _progress(
        self, task: Task, leaf: Leaf, items: dict[str, BudgetItem], begin: date, end: date
    ) -> None:
        """Avance validado cada semana, al ritmo de la línea base por el rendimiento."""
        if leaf.pct is not None:
            if begin <= self.today:
                self._approved(task, min(self.today, begin + timedelta(days=10)), [], Decimal(leaf.pct))
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
```

Notas para quien implementa:
- El presupuesto se llama igual en cada proyecto de ejemplo; la restricción `budget_project_name_ci_uq` es por proyecto, así que dos proyectos de ejemplo no chocan (lo prueba `test_refuses_to_seed_twice`). La plantilla de formulario se reutiliza (`get_or_create`).
- `Leaf(..., pct="60")` crea un registro con `progress_pct` y sin cantidades: cumple `record_task_progress_pair`.
- La 4.2 "Cámaras de inspección" (sin línea base) empieza unos 14 días después de hoy: no tiene registros, pero su plan sí suma al BAC y al PV futuro con sus fechas actuales.
- "Excavación frente sur" (rendimiento 1,10) termina antes de hoy: su acumulado pasa de 1200 a 1320 m3 y es la sobreejecución.
- Las llamadas que pasan de 100 columnas (`Device.objects.create(...)`, el `_approved` de la tarea por %, el `_task` de "Entrega de obra") las parte `ruff format` en el Step 4; `CHAPTERS` lleva `# fmt: skip` para conservar su forma de tabla y todas sus líneas caben en 100 columnas.

- [ ] **Step 4: Formato y pruebas**

```bash
infra/scripts/test_docker.sh --cmd "ruff format suno/management suno/control suno/core/tests/test_architecture.py && ruff check suno && mypy suno/management suno/control --config-file pyproject.toml"
infra/scripts/test_docker.sh suno/control/tests/test_seed_demo.py suno/core/tests/test_architecture.py
```

Expected: PASS. Si `warnings.no_baseline` o `percent_tasks` no coinciden, revisar el orden de `LEAVES` (el número de esquema sale del orden dentro de la etapa: la 1.3 es "Trámites y permisos" y la 4.2 "Cámaras de inspección").

- [ ] **Step 5: Commit**

```bash
git add server/suno/management/commands/seed_control_demo.py server/suno/core/tests/test_architecture.py server/suno/control/tests/test_seed_demo.py
git commit -m "feat(control): demo data command for the control page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Verificación completa, revisión visual, registro de avance y PR

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

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y `healthcheck.py` en el formato; `makemigrations --check` sin cambios (la pieza no toca modelos); bandit sin hallazgos; cobertura ≥ la de `master` (93 %).

- [ ] **Step 2: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-control -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0.

- [ ] **Step 3: Revisión visual a 1440 y 390 px**

Levantar la app contra el Postgres de pruebas (sigue vivo hasta `--down`), sembrar y abrir:

```bash
infra/scripts/test_docker.sh --cmd "python manage.py migrate -v 0 && python manage.py bootstrap_demo --org-name 'SUNO Demo' --username agente-demo --password 'Revision-visual-2026' && python manage.py shell -c \"from suno.core.models import AppUser, Membership, Organization, Role; org = Organization.objects.get(name='SUNO Demo'); user = AppUser.objects.create_user(username='admin-demo', email='admin-demo@example.com', password='Revision-visual-2026', org=org); Membership.objects.create(org=org, user=user, role=Role.ADMIN)\" && python manage.py seed_control_demo"
MSYS_NO_PATHCONV=1 docker run --rm -d --name suno-control-web --network suno-test -p 8000:8000 \
  -v "$(cd server && pwd -W):/app" -v suno-test-venv:/venv -w /app \
  -e POSTGRES_HOST=suno-test-db -e POSTGRES_PASSWORD=test -e PATH="/venv/bin:/usr/local/bin:/usr/bin:/bin" \
  python:3.12-slim sh -c "python manage.py runserver 0.0.0.0:8000"
```

Entrar en `http://localhost:8000/plataforma/ingresar/` como `admin-demo` y abrir la URL que imprimió `seed_control_demo`. Para los textos largos, alargar con `python manage.py shell -c` (en un `docker exec suno-control-web`) el nombre del proyecto, de una tarea, de un frente y la descripción de un ítem a 120 caracteres, y un precio a 12 dígitos.

Capturas con Edge headless a 1440 px y a 390 px (a 390 px dentro de un iframe, porque el mínimo de Edge es 496 px) de: periodo mensual y semanal, las tres pestañas del desglose, la tabla de la curva abierta, los avisos abiertos, el corte en el pasado (antes del inicio: SPI vacío) y los estados vacíos (proyecto sin presupuesto y sin matriz: el proyecto de `bootstrap_demo --with-schedule` sirve). Revisar: indicadores que no se cortan, etiquetas del eje X que no se pisan, la línea del corte, tablas dentro de su panel con desplazamiento horizontal, sin desbordes de nombres largos. Corregir antes del PR (CSS en `projects.css`, subiendo `?v=` en `base.html` si cambia).

Al terminar: `docker rm -f suno-control-web` y `infra/scripts/test_docker.sh --down`.

- [ ] **Step 4: Registro de avance**

En `plans/suno-progreso.md`, sección nueva al principio de la parte de costos: "Módulo de costos — Pieza 5 (control con valor ganado), servidor — <fecha>", con: PR pendiente y rama; qué se entregó (paquete `control`, página y pestaña, curva S, desglose, avisos, Excel auditado, `to_xlsx_book`, `seed_control_demo`); las decisiones del plan (resumen de la Task 0); los números (pruebas, cobertura, tiempo de la prueba de carga); lo que se corrigió en la revisión visual; y lo que sigue (costo real, CPI y EAC en una pieza posterior). Actualizar "Última actualización" del encabezado.

- [ ] **Step 5: Commit, push y PR**

```bash
git add plans/suno-progreso.md
git commit -m "docs(plan): progress log for cost piece 5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/costos-control
gh pr create --base master --head feat/costos-control \
  --title "feat(control): earned value control page for projects" \
  --body-file <archivo temporal en el scratchpad con: resumen; qué entra (cálculo, cargador de 9 queries, página, curva S, desglose, avisos, Excel auditado con control.exported, seed_control_demo); decisiones del plan; sin migraciones ni cambios de contrato; pruebas, cobertura y tiempo de la prueba de carga; capturas de la revisión visual; y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)">
```

Esperar el CI (backend, DAST, CodeQL). **Merge solo con aprobación del usuario.**

# Costos — Pieza 4b (cantidades ejecutadas en la app): plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el agente de campo registre en la app las cantidades ejecutadas por ítem en las tareas medidas por cantidades (en vez del %), con los datos automáticos por fila que la revisión necesita, y que corrija desde "Para revisar" las filas que el servidor rechace.

**Architecture:** Dos PR. **Parte A (servidor, se mezcla primero):** `RecordQuantityPayload` acepta cinco campos opcionales por fila (`client_id`, `captured_at`, `gps`, `seen`, `location_mode`) que se guardan dentro del JSON `Record.quantities` y vuelven en el pull; `FieldTaskList` agrega `project_items` (catálogo de ítems medibles de las versiones vigentes del proyecto, dentro de la huella `version`); el Admin muestra hora de captura y GPS con avisos; `bootstrap_demo --with-quantities` siembra lo que necesita la E2E. Es aditivo: sin cambio de `SCHEMA_VERSION`, compatible con la app 0.12.0. **Parte B (app):** copia el contrato del merge del servidor; Drift pasa del esquema 1 al 2 (`ScheduleTasks.measuredBy`, `Records.quantitiesJson`/`knownValidated` y cuatro tablas nuevas) con prueba de migración generada por `drift_dev make-migrations`; la lógica pura de filas vive en `lib/core/schedule/quantity_rows.dart`, la captura en `QuantityCaptureController` + `QuantityCaptureSection`, y la corrección de rechazos en `QuarantineService.correctQuantities` + `QuantityCorrectionScreen`.

**Tech Stack:** Servidor: Django 5.2, Ninja, Pydantic v2, PostgreSQL 16. App: Flutter 3.44.9, Dart, Drift 2.34 (`drift_dev make-migrations`), Riverpod, `geolocator`, `pdf`, `package:yaml` (solo pruebas).

**Spec:** `plans/2026-11-09-costos-pieza4b-cantidades-design.md` (aprobada). Planes de referencia: `plans/2026-11-03-costos-pieza4-matriz-plan.md` (pieza 4a, servidor) y `plans/2026-11-06-suno-registro-rename-plan.md` (cómo se trabajan los dos repos).

**Ramas y worktrees:**
- Parte A: `feat/costos-cantidades-campo` en `C:\Users\gabri\APP\wt-server-4b`. Los comandos de la Parte A se corren desde la raíz de ese worktree.
- Parte B: `feat/costos-cantidades-campo` en `C:\Users\gabri\APP\wt-client-4b`. Los comandos de la Parte B se corren desde la raíz de ese worktree.

---

## Riesgos y decisiones tomadas al planear

Ninguna reabre lo aprobado; precisan cómo se cumple. Se anotan en la spec en la Task A0.

1. **Forma guardada de los campos automáticos.** En `Record.quantities` cada fila guarda los campos nuevos **solo si vienen** (`client_id` como texto, `captured_at` en RFC 3339 UTC con milisegundos, `gps` `{lat, lon, accuracy_m}`, `seen` `{planned, validated, location_id}`, `location_mode`). Una fila de la app 0.12.0 se guarda exactamente como hoy (las pruebas existentes de `stored_quantities` siguen valiendo) y la comparación de `validated_rejection` incluye los campos nuevos sin código extra, porque compara la forma guardada.
2. **En el pull salen siempre.** `RecordQuantity` gana las cinco propiedades como `required` y nulables, `null` si la fila no las tiene: es la convención de las entidades que emite el servidor ("lo opcional lo es en el valor", `RecordData`). Para la app 0.12.0 son claves que ignora.
3. **`gps` exige sus tres claves** (`lat` −90..90, `lon` −180..180, `accuracy_m` ≥ 0, finitas) y **`seen` las suyas** (`planned`, `validated` números o `null`, `location_id` UUID o `null`). `seen.location_id` es la ubicación **de la tarea** que la app mostraba al capturar (la `FieldTask.location`), `null` si la tarea no tenía.
4. **`client_id` repetido** se detecta en `payloads.py` sobre la lista ya normalizada: `validation_error` con `{"field": "quantities", "reason": "duplicate_client_id"}`. Filas sin `client_id` no cuentan como repetidas.
5. **"Presupuesto vigente" del proyecto** = la versión vigente (última aplicada) de **cada** presupuesto del proyecto: el push ya acepta ítems de cualquier presupuesto del proyecto de la tarea, y el catálogo no puede ser más estrecho que eso. `project_items` lleva una entrada por proyecto con tareas en la lista (en el orden en que aparecen), con `items` vacía si no hay versión vigente; ítems de `kind = item` ordenados por código (`code_key`). Dos queries.
6. **La huella** pasa a ser el SHA-256 de `{"items": …, "project_items": …}`. Todos los teléfonos bajan la lista una vez tras el despliegue; después `changed: false` vuelve a ahorrar la descarga. Con `changed: false`, `project_items` va vacía como `items`.
7. **Avisos de la revisión:** "otro día" se mide en `America/Bogota` (`TIME_ZONE`) entre `captured_at` de la fila y `Record.captured_at`; "GPS impreciso" es `accuracy_m > 100`.
8. **E2E:** el servidor agrega `bootstrap_demo --with-quantities` (con `--with-schedule`): un presupuesto aplicado con 1.1 "Excavación mecánica" (planeado 100 m3 en la tarea 1.1) y 1.2 "Retiro de sobrantes" (sin plan). Va en el PR del servidor junto con su nota en `docs/testing/e2e-fase0.md`; la app agrega `E2E_FLOW=quantities` a `tool/e2e.sh`.
9. **Tareas por % no cambian su payload:** sin clave `quantities` (el servidor guarda `[]`). Solo las tareas por cantidades envían `quantities` con `progress_pct: null`. Así las pruebas actuales del payload siguen valiendo.
10. **El rechazo no dice qué fila falló** (`details` es `{field, reason}`). La app la identifica con lo que tiene: `item_not_in_project` señala las filas cuyo ítem no está ni en el plan de la tarea ni en el catálogo del proyecto; `location_outside_task` las filas con `location_id` que no está en las opciones de la tarea. El código del ítem sale de una tabla local nueva, `KnownItems`, que guarda para siempre los datos de cada ítem visto (un ítem que se volvió capítulo desaparece del catálogo, pero su código sigue disponible para nombrar la fila).
11. **"Registro validado"**: el contrato no publica la revisión, así que la app lo sabe cuando el servidor rechaza un cambio con `record_validated`; el `SyncEngine` marca `Records.knownValidated` y la captura muestra sus cantidades de solo lectura.
12. **Esquema Drift 2.** Además de lo que pide la spec: `TaskLocationOptions` guarda `isTaskLocation` (cuál opción es la ubicación de la propia tarea) y `position`; `ProjectItems` y `TaskPlannedItems` guardan `position`; tabla `KnownItems` (punto 10); `Records.knownValidated` (punto 11). La migración borra `schedule_task_state` para que la próxima sincronización baje la lista entera (la guardada no sabe cómo se mide cada tarea). Esquemas y pasos con `drift_dev make-migrations` (`drift_schemas/`, `lib/data/local/database.steps.dart`, `test/drift/app/`).
13. **"Agregar ítem no planeado"** busca en el catálogo **sin** los ítems ya planeados de la tarea (para esos está "Otra fila de este ítem").
14. **GPS por fila:** lectura puntual sin pedir permiso (si no lo hay, `gps: null`), con límite de 8 s, y reutilizada 30 s entre filas (`CachedSpotLocation`). No hay seguimiento continuo.
15. **Sobreejecución:** se compara lo validado más la **suma** de las filas de hoy del mismo ítem contra lo planeado.
16. **Detalle del registro a 390 px:** la "tabla" de cantidades se muestra como un bloque por fila (ítem; cantidad con unidad; ubicación; nota) para no desbordar; el PDF sí lleva una tabla de cuatro columnas.
17. **Fixtures del contrato:** `openapi/fixtures/` solo tiene el DSL de formularios y no cambia. La paridad de la app con el contrato de cantidades la vigila una prueba nueva que lee `contract/v1.yaml` (`test/contract/quantities_contract_test.dart`).

---

## Convenciones de los repositorios que hay que respetar

- **Servidor.** Tests con `unittest`/`django.test`; los que tocan BD usan `suno/core/tests/dbsupport.py` (`setUpModule = setup_test_database`, `skip_without_db`). Ningún módulo de producción usa `Model.objects` (siempre `scoped_qs(ctx, Model)`; los comandos de `management/` y las pruebas sí pueden). Auditoría con `audit.record(...)` dentro de `transaction.atomic()`. Nada de `assert` en producción (bandit B101) ni `import xml.etree`. Ruff a 100 columnas.
- **App.** `dart analyze` sin avisos; `flutter test`; los lint de `tool/` (`lint_ui_tokens.sh`: en `lib/features/` sin `TextStyle(`, sin números en `EdgeInsets`/`SizedBox`, sin textos literales; `lint_no_dynamic_code.sh`; `lint_no_tls_bypass.sh`) y la guardia `tool/check_naming.sh`.
- Comentarios y textos de interfaz en español; código en inglés. Commits en inglés, estilo conventional, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nunca `git init`/`git config` ni git que modifique el repo a través de contenedores Docker; dentro de Docker solo git de lectura (`ls-files`, `rev-parse`).

## Cómo correr las pruebas

```bash
# Servidor (desde C:/Users/gabri/APP/wt-server-4b; Docker Desktop arrancado; el contenedor trabaja en server/)
infra/scripts/test_docker.sh suno/sync/tests/test_payloads.py      # un archivo
infra/scripts/test_docker.sh                                       # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ."
infra/scripts/test_docker.sh --cmd "mypy . --config-file pyproject.toml"
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                                # al terminar

# Guardia de nombres del servidor (python + git de solo lectura, imagen rename-py:local)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-4b -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"

# App (desde C:/Users/gabri/APP/wt-client-4b; flutter y dart están en el PATH: C:/Users/gabri/dev/flutter/bin)
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
dart analyze
flutter test test/core/schedule/quantity_rows_test.dart            # un archivo
flutter test --concurrency=1                                       # suite completa
bash tool/lint_ui_tokens.sh && bash tool/lint_no_dynamic_code.sh && bash tool/lint_no_tls_bypass.sh && bash tool/check_naming.sh
```

Fallas conocidas del servidor, ajenas a este cambio: las 4 pruebas de `suno/sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y los archivos que ya fallan `ruff format --check` en `master` (comprobarlo en la Task A0 y anotarlos).

## Mapa de archivos

### Parte A — servidor (`C:\Users\gabri\APP\wt-server-4b`)

| Archivo | Responsabilidad |
|---|---|
| `server/suno/sync/payloads.py` | `_QuantityGps`, `_QuantitySeen`, campos nuevos de `_QuantityPayload`, `stored_quantities` con los campos automáticos, `duplicate_client_id` |
| `server/suno/sync/records.py` | `quantity_records` emite los campos automáticos |
| `server/suno/sync/schemas.py` | `RecordQuantityGps`, `RecordQuantitySeen`, campos nuevos de `RecordQuantityRow` |
| `server/suno/projects/field_tasks.py` | `project_items` y `list_version` con el catálogo |
| `server/suno/projects/schemas.py` | `FieldProjectItem`, `FieldProjectItems`, `FieldTaskList.project_items` |
| `server/suno/projects/api.py` | Respuesta con `project_items` dentro de la huella |
| `server/suno/core/admin.py` | `_capture_flags` en `RecordAdmin.quantities_pretty` |
| `server/suno/management/commands/bootstrap_demo.py` | `--with-quantities` |
| `server/openapi/v1.yaml` | `RecordQuantity`, `RecordQuantityPayload`, `QuantityGps`, `QuantitySeen`, `RecordPayload.quantities`, `FieldTaskList`, `FieldProjectItems`, `FieldProjectItem` |
| `docs/testing/e2e-fase0.md` | Variante de cantidades de la E2E |
| Pruebas | `server/suno/sync/tests/test_payloads.py`, `server/suno/sync/tests/test_push_quantities.py`, `server/suno/matrix/tests/test_field_tasks.py`, `server/suno/projects/tests/test_api.py`, `server/suno/matrix/tests/test_admin.py`, `server/suno/core/tests/test_bootstrap_demo.py` |

### Parte B — app (`C:\Users\gabri\APP\wt-client-4b`)

| Archivo | Responsabilidad |
|---|---|
| `contract/v1.yaml`, `contract/fixtures/forms_parity.json`, `contract/SOURCE` | Copia del contrato del merge del servidor |
| `build.yaml` | Opciones de `drift_dev` para `make-migrations` |
| `drift_schemas/app/drift_schema_v1.json`, `drift_schema_v2.json` | Esquemas exportados (generados) |
| `lib/data/local/database.steps.dart` | Pasos de migración (generado) |
| `test/drift/app/generated/*.dart` | Esquemas para las pruebas (generados) |
| `test/drift/app/migration_test.dart` | Prueba de la migración 1 → 2 |
| `lib/data/local/tables.dart` | `ScheduleTasks.measuredBy`, `Records.quantitiesJson`/`knownValidated`, `TaskPlannedItems`, `TaskLocationOptions`, `ProjectItems`, `KnownItems` |
| `lib/data/local/database.dart` | Esquema 2, `onUpgrade`, `replaceScheduleTasks` con las tablas nuevas |
| `lib/core/schedule/quantity_rows.dart` | Lógica pura de las filas: modelo, número, validaciones, payload, lectura de lo guardado |
| `lib/core/schedule/schedule_task_sync.dart` | Lee `measured_by`, `location`, `location_options`, `planned_items` y `project_items` |
| `lib/data/local/schedule_task_repository.dart` | `ScheduleTaskItem.projectId`/`measuredBy`; `byId`, `quantityContext`, `knownItems`, `locationLabels` |
| `lib/core/location/spot_location.dart`, `lib/data/location/geolocator_spot_location.dart` | Lectura GPS puntual sin pedir permiso, con caché de 30 s |
| `lib/core/app_dependencies.dart` | `spotLocation` |
| `lib/core/capture/record_capture.dart` | `TaskProgress.quantities`; guardar y editar cantidades |
| `lib/core/sync/sync_engine.dart` | Pull de `quantities`; marca `knownValidated` |
| `lib/core/sync/quarantine_service.dart` | Filas señaladas, `correctQuantities`; `unlinkTask`/`resendMine` con cantidades |
| `lib/features/capture/quantity_capture_section.dart` | `QuantityCaptureController`, `QuantityCaptureSection`, `showQuantityItemPicker` |
| `lib/features/capture/task_progress_section.dart` | Sección de cantidades en lugar del % y tarea bloqueada |
| `lib/features/capture/capture_screen.dart` | Integración: elegir tarea, confirmar descarte, editar, solo lectura, guardar |
| `lib/features/review/review_screen.dart`, `lib/features/review/quantity_correction_screen.dart` | Rechazos de cantidades y su corrección |
| `lib/ui/forms/dynamic_form.dart` | Mensajes de los motivos nuevos |
| `lib/data/local/record_status_repository.dart` | `RecordDetail.quantities` |
| `lib/features/records/record_detail_screen.dart` | Cantidades en el detalle |
| `lib/core/exports/record_export.dart` | Tabla del PDF y propiedad `quantities` del GeoJSON |
| `lib/l10n/app_es.arb` | Textos |
| `integration_test/e2e_quantities_test.dart`, `tool/e2e.sh` | E2E de cantidades |
| `pubspec.yaml`, `pubspec.lock` | `yaml` como dependencia de desarrollo directa |
| Pruebas | `test/contract/quantities_contract_test.dart`, `test/core/schedule/quantity_rows_test.dart`, `test/core/schedule/schedule_task_sync_test.dart`, `test/data/local/schedule_task_repository_test.dart`, `test/core/capture/record_quantities_test.dart`, `test/core/sync/pull_quantities_test.dart`, `test/core/sync/quarantine_quantities_test.dart`, `test/features/capture/quantity_capture_section_test.dart`, `test/features/capture/capture_quantities_test.dart`, `test/features/review/review_quantities_test.dart`, `test/core/exports/record_export_quantities_test.dart`, `test/features/records/record_detail_quantities_test.dart` |

---

# Parte A — Servidor (un PR, se mezcla primero)

### Task A0: Rama, línea base y notas en la spec

**Files:**
- Modify: `plans/2026-11-09-costos-pieza4b-cantidades-design.md` (al final)

- [ ] **Step 1: Comprobar la rama y que el árbol está limpio**

Run: `git -C C:/Users/gabri/APP/wt-server-4b status --short --branch`
Expected: `## feat/costos-cantidades-campo` sin cambios pendientes.

- [ ] **Step 2: Línea base**

```bash
infra/scripts/test_docker.sh 2>&1 | tail -5
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check ." 2>&1 | tail -15
```

Expected: todo verde salvo las 4 fallas de `test_load_script.py`. Anotar el número de pruebas y la lista de archivos que ya fallan `ruff format --check` (no se tocan).

- [ ] **Step 3: Anotar en la spec las decisiones tomadas al planear**

Agregar al final de `plans/2026-11-09-costos-pieza4b-cantidades-design.md`:

```markdown
## Decisiones tomadas al planear

Precisiones del plan `2026-11-09-costos-pieza4b-cantidades-plan.md`; no cambian lo aprobado.

- En `Record.quantities` los campos automáticos se guardan solo si vienen: una fila de la app
  0.12.0 queda igual que hoy. En el pull salen siempre, `null` si la fila no los tiene.
- `gps` exige `lat`, `lon` y `accuracy_m`; `seen` exige `planned`, `validated` y
  `location_id` (nulables). `seen.location_id` es la ubicación de la tarea que la app mostraba.
- El catálogo del proyecto sale de la versión vigente de cada presupuesto del proyecto (el push
  ya acepta ítems de cualquiera de ellos). Con `changed: false`, `project_items` va vacía.
- "Otro día" se mide en la hora de Colombia; "GPS impreciso" es precisión peor que 100 m.
- `bootstrap_demo --with-quantities` siembra el presupuesto y el plan de la E2E de la 4b.
- Las tareas por % no cambian su payload (sin `quantities`; el servidor guarda `[]`).
- El rechazo no identifica la fila: la app la deduce (ítem fuera del plan y del catálogo,
  ubicación fuera de las opciones de la tarea) y nombra su código con una tabla local de ítems
  conocidos.
- La app sabe que un registro está validado cuando el servidor rechaza un cambio con
  `record_validated` (el contrato no publica la revisión); desde ahí sus cantidades son de solo
  lectura.
- "Agregar ítem no planeado" no ofrece los ítems ya planeados de la tarea.
- La lectura GPS por fila no pide permiso, espera hasta 8 s y se reutiliza 30 s.
- En el detalle del registro, a 390 px, cada fila de cantidad es un bloque; el PDF lleva tabla.
```

- [ ] **Step 4: Commit**

```bash
git add plans/2026-11-09-costos-pieza4b-cantidades-design.md
git commit -m "docs(plan): planning notes for cost piece 4b

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A1: Campos automáticos por fila en el payload del push

**Files:**
- Modify: `server/suno/sync/payloads.py` (`_QuantityPayload`, `stored_quantities`, `parse_op`; clases nuevas `_QuantityGps`, `_QuantitySeen`)
- Test: `server/suno/sync/tests/test_payloads.py` (clase nueva al final)

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `server/suno/sync/tests/test_payloads.py`:

```python
class QuantityCaptureFieldsTests(SimpleTestCase):
    """Costos, pieza 4b: datos automáticos por fila de cantidad (sin BD)."""

    ITEM = str(uuid.uuid4())
    TASK = str(uuid.uuid4())

    def _create(self, *rows: dict[str, object]) -> RecordWrite | Rejection:
        payload = _create_payload(task_id=self.TASK, progress_pct=None, quantities=list(rows))
        parsed = parse_op("record", "create", None, payload)
        assert not isinstance(parsed, AttachmentWrite)
        return parsed

    def _row(self, **values: object) -> dict[str, object]:
        return {"item_id": self.ITEM, "quantity": 2, **values}

    def test_capture_fields_are_stored(self) -> None:
        client_id, location = str(uuid.uuid4()), str(uuid.uuid4())
        write = self._create(
            self._row(
                client_id=client_id,
                captured_at="2026-11-09T09:03:00-05:00",
                gps={"lat": 4.61, "lon": -74.08, "accuracy_m": 8.5},
                seen={"planned": 10, "validated": 4.5, "location_id": location},
                location_mode="refined",
            )
        )
        assert isinstance(write, RecordWrite)
        self.assertEqual(
            write.fields["quantities"],
            [
                {
                    "item_id": self.ITEM,
                    "location_id": None,
                    "quantity": "2.0000",
                    "note": "",
                    "client_id": client_id,
                    "captured_at": "2026-11-09T14:03:00.000Z",
                    "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 8.5},
                    "seen": {"planned": 10.0, "validated": 4.5, "location_id": location},
                    "location_mode": "refined",
                }
            ],
        )

    def test_rows_without_them_keep_the_old_shape(self) -> None:
        write = self._create(self._row())
        assert isinstance(write, RecordWrite)
        self.assertEqual(
            write.fields["quantities"],
            [{"item_id": self.ITEM, "location_id": None, "quantity": "2.0000", "note": ""}],
        )

    def test_null_gps_and_seen_are_not_stored(self) -> None:
        write = self._create(self._row(gps=None, seen=None, location_mode="task"))
        assert isinstance(write, RecordWrite)
        row = write.fields["quantities"][0]
        self.assertNotIn("gps", row)
        self.assertNotIn("seen", row)
        self.assertEqual(row["location_mode"], "task")

    def test_seen_accepts_nulls(self) -> None:
        seen = {"planned": None, "validated": None, "location_id": None}
        write = self._create(self._row(seen=seen))
        assert isinstance(write, RecordWrite)
        self.assertEqual(write.fields["quantities"][0]["seen"], seen)

    def test_invalid_capture_fields_are_rejected(self) -> None:
        bad_values: list[dict[str, object]] = [
            {"client_id": "no-es-un-uuid"},
            {"captured_at": "2026-11-09T09:03:00"},
            {"captured_at": 1762700000},
            {"gps": {"lat": 91, "lon": 0, "accuracy_m": 1}},
            {"gps": {"lat": 0, "lon": 181, "accuracy_m": 1}},
            {"gps": {"lat": 0, "lon": 0, "accuracy_m": -1}},
            {"gps": {"lat": 0, "lon": 0}},
            {"gps": {"lat": 0, "lon": 0, "accuracy_m": 1, "altitude_m": 3}},
            {"gps": {"lat": float("nan"), "lon": 0, "accuracy_m": 1}},
            {"seen": {"planned": 1, "validated": None}},
            {"seen": {"planned": float("inf"), "validated": None, "location_id": None}},
            {"seen": {"planned": 1, "validated": 1, "location_id": "x"}},
            {"location_mode": "gps"},
        ]
        for bad in bad_values:
            with self.subTest(bad=bad):
                rejection = self._create(self._row(**bad))
                assert isinstance(rejection, Rejection)
                self.assertEqual(rejection.code, "validation_error")

    def test_duplicate_client_id_is_rejected(self) -> None:
        client_id = str(uuid.uuid4())
        rejection = self._create(self._row(client_id=client_id), self._row(client_id=client_id))
        assert isinstance(rejection, Rejection)
        self.assertEqual(rejection.code, "validation_error")
        self.assertEqual(
            rejection.details, {"field": "quantities", "reason": "duplicate_client_id"}
        )

    def test_rows_without_client_id_are_not_duplicates(self) -> None:
        write = self._create(self._row(), self._row(quantity=3))
        assert isinstance(write, RecordWrite)
        self.assertEqual(len(write.fields["quantities"]), 2)

    def test_update_also_checks_duplicates(self) -> None:
        client_id = str(uuid.uuid4())
        payload = {
            "id": str(uuid.uuid4()),
            "task_id": self.TASK,
            "progress_pct": None,
            "quantities": [self._row(client_id=client_id), self._row(client_id=client_id)],
        }
        rejection = parse_op("record", "update", 1, payload)
        assert isinstance(rejection, Rejection)
        self.assertEqual(rejection.details["reason"], "duplicate_client_id")  # type: ignore[index]
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/sync/tests/test_payloads.py`
Expected: FAIL en las pruebas nuevas (`extra_forbidden` en `client_id`, `captured_at`…), PASS en las demás.

- [ ] **Step 3: Modelos de los campos automáticos**

En `server/suno/sync/payloads.py`, el import de `suno.authn.schemas` (nuevo, junto a los demás de `suno.*`):

```python
from suno.authn.schemas import rfc3339_utc
```

Después de `_QUANTITY_STEP = Decimal("0.0001")` y antes de `class _QuantityPayload`:

```python
class _QuantityGps(BaseModel):
    """Contrato `QuantityGps`: una lectura puntual al digitar la cantidad (pieza 4b)."""

    model_config = ConfigDict(extra="forbid")

    lat: Finite = Field(ge=LAT_MIN, le=LAT_MAX)
    lon: Finite = Field(ge=LON_MIN, le=LON_MAX)
    accuracy_m: Finite = Field(ge=0)


class _QuantitySeen(BaseModel):
    """Contrato `QuantitySeen`: lo que la app mostraba al capturar la fila (pieza 4b).
    Las tres claves son obligatorias y nulables."""

    model_config = ConfigDict(extra="forbid")

    planned: Finite | None
    validated: Finite | None
    location_id: uuid.UUID | None
```

En `_QuantityPayload`, después de `note: QuantityNote = ""`:

```python
    # Costos, pieza 4b: datos automáticos de la fila. Opcionales: la app 0.12.0 no los
    # envía. `client_id` es único dentro del registro (`_duplicate_client_id`).
    client_id: uuid.UUID | None = None
    captured_at: UtcDatetime | None = None
    gps: _QuantityGps | None = None
    seen: _QuantitySeen | None = None
    location_mode: Literal["task", "refined"] | None = None
```

- [ ] **Step 4: Forma guardada y `client_id` repetido**

Reemplazar `stored_quantities` por:

```python
def stored_quantities(rows: list[Any]) -> list[dict[str, Any]]:
    """La forma guardada en `Record.quantities`: ids como texto y la cantidad como
    decimal de 4 decimales en texto (exacto). Es también la forma con la que se
    compara un registro validado (`services._References.validated_rejection`), así
    que los datos automáticos de la pieza 4b también quedan protegidos.

    Los datos automáticos se guardan solo si vienen: una fila de la app 0.12.0 queda
    exactamente como antes."""
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
                **_stored_capture_fields(data),
            }
        )
    return stored


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _stored_capture_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Los datos automáticos de una fila (pieza 4b) en tipos JSON, solo los presentes."""
    stored: dict[str, Any] = {}
    if data.get("client_id") is not None:
        stored["client_id"] = str(data["client_id"])
    captured = data.get("captured_at")
    if captured is not None:
        # Ya en UTC (`UtcDatetime`); el mismo formato que el resto del contrato.
        stored["captured_at"] = captured if isinstance(captured, str) else rfc3339_utc(captured)
    gps = data.get("gps")
    if gps is not None:
        stored["gps"] = {
            "lat": float(gps["lat"]),
            "lon": float(gps["lon"]),
            "accuracy_m": float(gps["accuracy_m"]),
        }
    seen = data.get("seen")
    if seen is not None:
        seen_location = seen.get("location_id")
        stored["seen"] = {
            "planned": _optional_float(seen.get("planned")),
            "validated": _optional_float(seen.get("validated")),
            "location_id": None if seen_location is None else str(seen_location),
        }
    if data.get("location_mode") is not None:
        stored["location_mode"] = str(data["location_mode"])
    return stored
```

Después de `_task_pair_rejection`, función nueva:

```python
def _duplicate_client_id(fields: dict[str, Any], entity_id: uuid.UUID) -> Rejection | None:
    """Pieza 4b: `client_id` identifica la fila dentro del registro (la corrección de un
    rechazo la señala por él). Dos filas con el mismo valor son un error del cliente.
    Las filas sin `client_id` (app 0.12.0) no cuentan."""
    seen: set[str] = set()
    for row in fields.get("quantities") or []:
        client_id = row.get("client_id")
        if client_id is None:
            continue
        if client_id in seen:
            return _rejection(
                "client_id must be unique within the record.",
                field="quantities",
                reason="duplicate_client_id",
                entity_id=entity_id,
            )
        seen.add(client_id)
    return None
```

En `parse_op`, reemplazar:

```python
    pair = _task_pair_rejection(fields, validated.id)
    if pair is not None:
        return pair
```

por:

```python
    pair = _task_pair_rejection(fields, validated.id)
    if pair is not None:
        return pair
    duplicate = _duplicate_client_id(fields, validated.id)
    if duplicate is not None:
        return duplicate
```

(`_task_pair_rejection` ya dejó `fields["quantities"]` en la forma guardada cuando viajan.)

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/sync/tests/test_payloads.py`
Expected: PASS (todas, también las de `QuantitiesPayloadTests` que ya existían).

- [ ] **Step 6: Commit**

```bash
git add server/suno/sync/payloads.py server/suno/sync/tests/test_payloads.py
git commit -m "feat(sync): accept per-row capture data on record quantities

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A2: Campos automáticos en el pull y en el contrato del registro

**Files:**
- Modify: `server/suno/sync/records.py` (`quantity_records`)
- Modify: `server/suno/sync/schemas.py` (`RecordQuantityRow`; clases nuevas `RecordQuantityGps`, `RecordQuantitySeen`)
- Modify: `server/openapi/v1.yaml` (`RecordQuantity`, `RecordQuantityPayload`, `RecordPayload.quantities`; schemas nuevos `QuantityGps`, `QuantitySeen`)
- Test: `server/suno/sync/tests/test_push_quantities.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/sync/tests/test_push_quantities.py`, en `test_planned_item_with_refined_location` el `assertEqual` de `data["quantities"]` pasa a:

```python
        self.assertEqual(
            data["quantities"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "location_id": str(self.pr.pk),
                    "quantity": 4.5,
                    "note": "",
                    "client_id": None,
                    "captured_at": None,
                    "gps": None,
                    "seen": None,
                    "location_mode": None,
                }
            ],
        )
```

Al final de la clase `QuantityPushTests`:

```python
    def capture_row(self, quantity: object, **extra: object) -> dict[str, Any]:
        """Una fila como la envía la app 0.13 (pieza 4b), con sus datos automáticos."""
        return self.row(
            self.concrete,
            quantity,
            client_id=str(uuid.uuid4()),
            captured_at="2026-07-30T09:00:00-05:00",
            gps={"lat": 4.61, "lon": -74.08, "accuracy_m": 12.0},
            seen={"planned": 10.0, "validated": 0.0, "location_id": str(self.tramo.pk)},
            location_mode="task",
            **extra,
        )

    def test_capture_fields_round_trip(self) -> None:
        record_id = uuid.uuid4()
        row = self.capture_row(3)
        result = self.push_one(self.create(row, record_id=record_id))
        self.assertEqual(result["status"], "applied", result)
        stored = record_of(record_id).quantities[0]
        self.assertEqual(stored["client_id"], row["client_id"])
        self.assertEqual(stored["captured_at"], "2026-07-30T14:00:00.000Z")
        rows = pull(self.scenario, CURSOR_ZERO).json()["rows"]
        data = next(r["data"] for r in rows if r["data"].get("id") == str(record_id))
        self.assertEqual(
            data["quantities"],
            [
                {
                    "item_id": str(self.concrete.pk),
                    "location_id": None,
                    "quantity": 3.0,
                    "note": "",
                    "client_id": row["client_id"],
                    "captured_at": "2026-07-30T14:00:00.000Z",
                    "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 12.0},
                    "seen": {
                        "planned": 10.0,
                        "validated": 0.0,
                        "location_id": str(self.tramo.pk),
                    },
                    "location_mode": "task",
                }
            ],
        )

    def test_validated_record_capture_fields_cannot_change(self) -> None:
        record_id = uuid.uuid4()
        row = self.capture_row(4)
        self.push_one(self.create(row, record_id=record_id))
        RecordReview.objects.create(
            org=self.scenario.org,
            record=record_of(record_id),
            status=str(RecordReviewStatus.APPROVED),
            reviewer=self.admin,
        )
        version = record_of(record_id).version
        moved = {**row, "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 30.0}}
        result = self.push_one(
            update_op(
                record_id,
                version,
                task_id=str(self.task(3).pk),
                progress_pct=None,
                quantities=[moved],
            )
        )
        self.assertEqual(result["status"], "rejected", result)
        self.assertEqual(
            result["error"]["details"], {"field": "quantities", "reason": "record_validated"}
        )
        same = update_op(
            record_id, version, task_id=str(self.task(3).pk), progress_pct=None, quantities=[row]
        )
        self.assertEqual(self.push_one(same)["status"], "applied")

    def test_duplicate_client_id_is_rejected(self) -> None:
        row = self.capture_row(1)
        result = self.push_one(self.create(row, {**row, "quantity": 2}))
        self.assertRejected(result, "duplicate_client_id")
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/sync/tests/test_push_quantities.py`
Expected: FAIL en `test_planned_item_with_refined_location` y `test_capture_fields_round_trip` (faltan claves en el pull); las demás nuevas ya pasan por la Task A1.

- [ ] **Step 3: `records.py`**

Reemplazar `quantity_records` por:

```python
def quantity_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`Record.quantities` como lo publica el contrato: la cantidad como número JSON.

    Los datos automáticos de la pieza 4b salen siempre, `null` si la fila no los tiene
    (captura de la app 0.12.0): lo opcional lo es en el valor, no en la presencia."""
    return [
        {
            "item_id": row["item_id"],
            "location_id": row["location_id"],
            "quantity": float(Decimal(row["quantity"])),
            "note": row["note"],
            "client_id": row.get("client_id"),
            "captured_at": row.get("captured_at"),
            "gps": row.get("gps"),
            "seen": row.get("seen"),
            "location_mode": row.get("location_mode"),
        }
        for row in rows
    ]
```

- [ ] **Step 4: `schemas.py`**

Reemplazar `class RecordQuantityRow` por:

```python
class RecordQuantityGps(Schema):
    """Contrato `QuantityGps`."""

    lat: float
    lon: float
    accuracy_m: float


class RecordQuantitySeen(Schema):
    """Contrato `QuantitySeen`."""

    planned: float | None
    validated: float | None
    location_id: uuid.UUID | None


class RecordQuantityRow(Schema):
    """Contrato `RecordQuantity`. Los datos automáticos (pieza 4b) son `null` en las
    filas capturadas sin ellos."""

    item_id: uuid.UUID
    location_id: uuid.UUID | None
    quantity: float
    note: str
    client_id: uuid.UUID | None
    captured_at: Timestamp | None
    gps: RecordQuantityGps | None
    seen: RecordQuantitySeen | None
    location_mode: Literal["task", "refined"] | None
```

(`Literal` y `Timestamp` ya están importados en ese archivo; si `Literal` no lo está, sumarlo al `from typing import …`.)

- [ ] **Step 5: Contrato `v1.yaml`**

En `components.schemas.RecordQuantity`, reemplazar `required: [item_id, location_id, quantity, note]` por:

```yaml
      required: [item_id, location_id, quantity, note, client_id, captured_at, gps, seen, location_mode]
```

y, después de la propiedad `note` de `RecordQuantity` (la que tiene `maxLength: 500` y precede a `RecordQuantityPayload:`), agregar:

```yaml
        client_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'
          description: |
            Pieza 4b: identificador de la fila generado por la app, único dentro del registro.
            `null` en filas capturadas antes de la pieza 4b.
        captured_at:
          oneOf:
            - $ref: '#/components/schemas/Timestamp'
            - type: 'null'
          description: Momento en que se digitó la cantidad (UTC). `null` si no se envió.
        gps:
          oneOf:
            - $ref: '#/components/schemas/QuantityGps'
            - type: 'null'
        seen:
          oneOf:
            - $ref: '#/components/schemas/QuantitySeen'
            - type: 'null'
        location_mode:
          type: [string, 'null']
          enum: [task, refined, null]
          description: '`refined`: el agente precisó la ubicación; `task`: usó la de la tarea.'
```

En `components.schemas.RecordQuantityPayload.properties`, después de `note`:

```yaml
        client_id:
          $ref: '#/components/schemas/Uuid'
        captured_at:
          $ref: '#/components/schemas/Timestamp'
        gps:
          oneOf:
            - $ref: '#/components/schemas/QuantityGps'
            - type: 'null'
          description: Lectura GPS puntual al digitar la cantidad, si había permiso y señal.
        seen:
          oneOf:
            - $ref: '#/components/schemas/QuantitySeen'
            - type: 'null'
        location_mode:
          type: string
          enum: [task, refined]
```

Schemas nuevos, inmediatamente antes de `    RecordAttachment:`:

```yaml
    QuantityGps:
      type: object
      additionalProperties: false
      description: Lectura GPS puntual de una fila de cantidad (pieza 4b). Sin seguimiento continuo.
      required: [lat, lon, accuracy_m]
      properties:
        lat:
          type: number
          minimum: -90
          maximum: 90
        lon:
          type: number
          minimum: -180
          maximum: 180
        accuracy_m:
          type: number
          minimum: 0

    QuantitySeen:
      type: object
      additionalProperties: false
      description: |
        Lo que la app mostraba al capturar la fila (pieza 4b): la cantidad planeada y la
        validada del ítem en la tarea (`null` si el ítem no estaba planeado) y la ubicación de
        la tarea (`null` si no tenía).
      required: [planned, validated, location_id]
      properties:
        planned:
          type: [number, 'null']
        validated:
          type: [number, 'null']
        location_id:
          oneOf:
            - $ref: '#/components/schemas/Uuid'
            - type: 'null'

```

En la descripción de `RecordPayload.properties.quantities`, reemplazar:

```yaml
            `location_outside_task` (la ubicación no es un nodo del proyecto de la tarea) y
```

por:

```yaml
            `location_outside_task` (la ubicación no es un nodo del proyecto de la tarea),
            `duplicate_client_id` (dos filas con el mismo `client_id`) y
```

Validar el YAML:

```bash
infra/scripts/test_docker.sh --cmd "python -c \"import yaml;from openapi_spec_validator import validate;from openapi_spec_validator.validation import OpenAPIV31SpecValidator;validate(yaml.safe_load(open('openapi/v1.yaml',encoding='utf-8')),cls=OpenAPIV31SpecValidator);print('OK 3.1')\""
```

Expected: `OK 3.1`. Si `openapi_spec_validator` no está en el venv del contenedor, anteponer `pip install -q openapi-spec-validator && ` al comando.

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/sync suno/matrix`
Expected: PASS (las 4 fallas conocidas de `test_load_script.py` aparte).

- [ ] **Step 7: Commit**

```bash
git add server/openapi/v1.yaml server/suno/sync
git commit -m "feat(sync): publish per-row capture data of quantities in pull and contract

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A3: Catálogo de ítems por proyecto en la lista de tareas de campo

**Files:**
- Modify: `server/suno/projects/field_tasks.py` (función nueva `project_items`; `list_version`)
- Modify: `server/suno/projects/schemas.py` (`FieldProjectItem`, `FieldProjectItems`, `FieldTaskList`)
- Modify: `server/suno/projects/api.py` (`list_field_tasks`)
- Modify: `server/openapi/v1.yaml` (`FieldTaskList`; schemas nuevos `FieldProjectItems`, `FieldProjectItem`)
- Test: `server/suno/matrix/tests/test_field_tasks.py`, `server/suno/projects/tests/test_api.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/matrix/tests/test_field_tasks.py`, los imports pasan a:

```python
from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import SPLIT_ROWS, V2_ROWS
```

y

```python
from suno.projects.field_tasks import field_tasks, list_version, project_items
```

y, al final del archivo:

```python
@skip_without_db
class ProjectItemsTests(TestCase):
    """Pieza 4b: el catálogo para "Agregar ítem no planeado"."""

    def setUp(self) -> None:
        self.world, self.budget = budget_world()

    def catalog(self) -> list[dict[str, object]]:
        return project_items(self.world.ctx(self.world.agent), [self.world.project.pk])

    def test_current_items_without_chapters_nor_prices(self) -> None:
        catalog = self.catalog()
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["project_id"], str(self.world.project.pk))
        items = catalog[0]["items"]
        self.assertEqual(
            [i["code"] for i in items],  # type: ignore[index]
            ["1.1", "1.2", "2.1", "2.2", "2.4"],
        )
        self.assertEqual(
            items[2],  # type: ignore[index]
            {
                "item_id": str(item(self.budget, "2.1").pk),
                "code": "2.1",
                "description": "Concreto zapatas",
                "unit": "m3",
            },
        )
        self.assertNotIn("price", str(catalog))
        self.assertNotIn("total", str(catalog))

    def test_follows_the_current_version(self) -> None:
        apply_rows(self.world.ctx(), self.budget, V2_ROWS)
        codes = [i["code"] for i in self.catalog()[0]["items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["1.1", "2.1", "2.2", "2.3", "2.4"])

    def test_every_budget_of_the_project(self) -> None:
        other = make_budget(self.world.project, self.world.admin, code="C-02", name="Adicional")
        rows: list[list[object]] = [["9", "Extras"], ["9.1", "Señalización", "un", 4, 1000, 4000]]
        apply_rows(self.world.ctx(), other, rows)
        codes = [i["code"] for i in self.catalog()[0]["items"]]  # type: ignore[attr-defined]
        self.assertEqual(codes, ["1.1", "1.2", "2.1", "2.2", "2.4", "9.1"])

    def test_project_without_current_budget(self) -> None:
        world = make_field_world("Sin presupuesto")
        catalog = project_items(world.ctx(world.agent), [world.project.pk])
        self.assertEqual(catalog, [{"project_id": str(world.project.pk), "items": []}])

    def test_no_projects_no_queries(self) -> None:
        with self.assertNumQueries(0):
            self.assertEqual(project_items(self.world.ctx(self.world.agent), []), [])

    def test_two_queries(self) -> None:
        with self.assertNumQueries(2):
            project_items(self.world.ctx(self.world.agent), [self.world.project.pk])

    def test_the_catalog_is_part_of_the_version(self) -> None:
        self.world.responsible(3)
        ctx = self.world.ctx(self.world.agent)
        tasks = field_tasks(ctx)
        before = list_version(tasks, project_items(ctx, [self.world.project.pk]))
        apply_rows(self.world.ctx(), self.budget, V2_ROWS)
        after = list_version(tasks, project_items(ctx, [self.world.project.pk]))
        self.assertNotEqual(before, after)
```

Sumar `make_field_world` a los imports del archivo:

```python
from suno.projects.tests.factories import make_field_world
```

(`make_budget(project, user, *, name="Contrato de obra", code="C-01")`: el segundo presupuesto necesita otro nombre, que es único por proyecto.)

En `server/suno/projects/tests/test_api.py`, imports nuevos:

```python
from suno.budgets.tests.factories import apply_rows, make_budget
from suno.budgets.tests.samples import V1_ROWS, V2_ROWS
```

y al final de `FieldTasksApiTests`:

```python
    def test_project_items_travel_with_the_list(self) -> None:
        w = self.world
        w.responsible(3)
        budget = make_budget(w.project, w.admin)
        apply_rows(w.ctx(), budget, V1_ROWS)
        body = self.get()
        self.assertEqual(len(body["project_items"]), 1)
        catalog = body["project_items"][0]
        self.assertEqual(catalog["project_id"], str(w.project.pk))
        self.assertEqual(
            [i["code"] for i in catalog["items"]], ["1.1", "1.2", "2.1", "2.2", "2.4"]
        )
        unchanged = self.get(version=body["version"])
        self.assertEqual((unchanged["changed"], unchanged["project_items"]), (False, []))
        apply_rows(w.ctx(), budget, V2_ROWS)
        self.assertTrue(self.get(version=body["version"])["changed"])

    def test_without_tasks_there_is_no_catalog(self) -> None:
        self.assertEqual(self.get()["project_items"], [])
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `infra/scripts/test_docker.sh suno/matrix/tests/test_field_tasks.py suno/projects/tests/test_api.py`
Expected: FAIL con `ImportError: cannot import name 'project_items'` y `KeyError: 'project_items'`.

- [ ] **Step 3: `field_tasks.py`**

Imports:

```python
from collections.abc import Iterable
```

y reemplazar `from suno.core.models import Task` por:

```python
from suno.core.models import (
    BudgetItemVersion,
    BudgetRowKind,
    BudgetVersion,
    BudgetVersionStatus,
    Task,
)
```

En el docstring del módulo, después de "—importación, responsables, revisiones, la matriz—", agregar ", el presupuesto vigente". Función nueva antes de `list_version`:

```python
def project_items(ctx: RequestContext, project_ids: Iterable[UUID]) -> list[dict[str, Any]]:
    """Contrato `FieldProjectItems` (pieza 4b): por proyecto, en el orden recibido, los ítems
    medibles (no capítulos) de la versión vigente de cada uno de sus presupuestos, sin
    precios ni montos. Lo usa la app para "Agregar ítem no planeado".

    Un proyecto sin versión vigente va con `items` vacía. Dos queries; ninguna sin
    proyectos."""
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return []
    current = list(
        scoped_qs(ctx, BudgetVersion)
        .filter(budget__project_id__in=ids, status=BudgetVersionStatus.APPLIED)
        .order_by("budget_id", "-number")
        .distinct("budget_id")
        .values_list("pk", flat=True)
    )
    by_project: dict[UUID, list[dict[str, Any]]] = {project_id: [] for project_id in ids}
    if current:
        rows = (
            scoped_qs(ctx, BudgetItemVersion)
            .filter(version_id__in=current, kind=BudgetRowKind.ITEM)
            .values_list(
                "version__budget__project_id", "item_id", "item__code", "description", "unit"
            )
        )
        for project_id, item_id, code, description, unit in rows:
            by_project[project_id].append(
                {"item_id": str(item_id), "code": code, "description": description, "unit": unit}
            )
    return [
        {
            "project_id": str(project_id),
            "items": sorted(items, key=lambda i: (code_key(i["code"]), i["item_id"])),
        }
        for project_id, items in by_project.items()
    ]
```

Reemplazar `list_version` por:

```python
def list_version(
    items: list[dict[str, Any]], catalog: list[dict[str, Any]] | None = None
) -> str:
    """Huella SHA-256 de la lista y del catálogo de ítems (`project_items`, pieza 4b):
    cambia si cambia cualquier dato entregado."""
    payload = {"items": items, "project_items": catalog or []}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Esquemas y API**

`server/suno/projects/schemas.py`: `__all__` pasa a

```python
__all__ = [
    "FieldLocation",
    "FieldPlannedItem",
    "FieldProjectItem",
    "FieldProjectItems",
    "FieldTask",
    "FieldTaskList",
]
```

clases nuevas antes de `FieldTask`:

```python
class FieldProjectItem(Schema):
    item_id: uuid.UUID
    code: str
    description: str
    unit: str


class FieldProjectItems(Schema):
    project_id: uuid.UUID
    items: list[FieldProjectItem]
```

y `FieldTaskList` pasa a:

```python
class FieldTaskList(Schema):
    version: str
    changed: bool
    items: list[FieldTask]
    project_items: list[FieldProjectItems]
```

`server/suno/projects/api.py`: imports

```python
from uuid import UUID
```

y el cuerpo de `list_field_tasks`:

```python
def list_field_tasks(request, version: str | None = Query(None, max_length=64)):
    """`GET /v1/projects/tasks?version=`: la lista y el catálogo de ítems de sus proyectos,
    o "sin cambios" si `version` coincide."""
    ctx = get_request_context(request)
    items = field_tasks.field_tasks(ctx)
    catalog = field_tasks.project_items(ctx, [UUID(i["project_id"]) for i in items])
    current = field_tasks.list_version(items, catalog)
    if version == current:
        return {"version": current, "changed": False, "items": [], "project_items": []}
    return {"version": current, "changed": True, "items": items, "project_items": catalog}
```

- [ ] **Step 5: Contrato**

En `components.schemas.FieldTaskList`: `required: [version, changed, items, project_items]`, la descripción de `changed` pasa a `'`false` si la lista no cambió desde `?version=`; entonces `items` y `project_items` van vacíos.'` y, después de `items`:

```yaml
        project_items:
          type: array
          description: |
            Pieza 4b: por cada proyecto con tareas en la lista, los ítems medibles (no capítulos)
            de la versión vigente de cada uno de sus presupuestos, sin precios ni montos. La app
            busca aquí al agregar un ítem no planeado. Entra en `version`.
          items:
            $ref: '#/components/schemas/FieldProjectItems'
```

Schemas nuevos, después de `FieldPlannedItem` (antes de `HealthStatus:`):

```yaml
    FieldProjectItems:
      type: object
      required: [project_id, items]
      properties:
        project_id:
          $ref: '#/components/schemas/Uuid'
        items:
          type: array
          description: Vacía si el proyecto no tiene presupuesto vigente.
          items:
            $ref: '#/components/schemas/FieldProjectItem'

    FieldProjectItem:
      type: object
      required: [item_id, code, description, unit]
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

```

Validar el YAML con el mismo comando de la Task A2, Step 5.

- [ ] **Step 6: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/matrix suno/projects`
Expected: PASS. `test_queries_are_bounded` sigue en 7: `field_tasks` no cambió.

- [ ] **Step 7: Commit**

```bash
git add server/openapi/v1.yaml server/suno/projects server/suno/matrix/tests/test_field_tasks.py
git commit -m "feat(projects): field task list carries the current item catalog per project

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A4: Revisión — hora de captura y GPS por fila

**Files:**
- Modify: `server/suno/core/admin.py` (función nueva `_capture_flags`; `RecordAdmin.quantities_pretty`)
- Test: `server/suno/matrix/tests/test_admin.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `RecordAdminQuantitiesTests` en `server/suno/matrix/tests/test_admin.py`:

```python
    def test_capture_time_and_gps_with_warnings(self) -> None:
        """El registro es de T0 (2026-03-10 04:00 en Bogotá)."""
        plan(self.world, 3, self.concrete, "10")
        record = quantity_record(
            self.world,
            3,
            {
                **quantity_row(self.concrete, "1"),
                "captured_at": "2026-03-10T15:30:00.000Z",
                "gps": {"lat": 4.61, "lon": -74.08, "accuracy_m": 8.0},
            },
            {
                **quantity_row(self.concrete, "2"),
                "captured_at": "2026-03-09T20:00:00.000Z",
                "gps": {"lat": 4.6, "lon": -74.1, "accuracy_m": 150.0},
            },
            quantity_row(self.concrete, "3"),
        )
        lines = str(self.admin.quantities_pretty(record)).split("</p>")
        self.assertIn("hora de captura 10:30", lines[0])
        self.assertNotIn("otro día", lines[0])
        self.assertIn("GPS 4.61000, -74.08000 (±8 m)", lines[0])
        self.assertNotIn("precisión baja", lines[0])
        self.assertIn("hora de captura 09/03/2026 15:00 (otro día que el registro)", lines[1])
        self.assertIn("(±150 m, precisión baja)", lines[1])
        self.assertNotIn("hora de captura", lines[2])
        self.assertNotIn("GPS", lines[2])
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh suno/matrix/tests/test_admin.py`
Expected: FAIL (`hora de captura 10:30` no está en el texto).

- [ ] **Step 3: Implementar**

En `server/suno/core/admin.py`, import:

```python
from django.utils.dateparse import parse_datetime
```

Constante y función a nivel de módulo, inmediatamente antes de `@admin.register(Record)`:

```python
#: Precisión GPS (m) a partir de la cual la revisión marca la fila (pieza 4b).
GPS_ACCURACY_WARNING_M = 100


def _capture_flags(row: dict[str, Any], record: Record) -> str:
    """Hora de captura y GPS de una fila de cantidad (pieza 4b), con sus avisos: digitada
    otro día que el del registro (hora de Colombia), o GPS con precisión peor que 100 m.
    Son solo avisos: el push los acepta. Una fila sin esos datos no agrega nada."""
    text = ""
    captured = parse_datetime(row["captured_at"]) if row.get("captured_at") else None
    if captured is not None:
        local = timezone.localtime(captured)
        if local.date() == timezone.localtime(record.captured_at).date():
            text += f" — hora de captura {local:%H:%M}"
        else:
            text += f" — hora de captura {local:%d/%m/%Y %H:%M} (otro día que el registro)"
    gps = row.get("gps")
    if gps:
        accuracy = gps["accuracy_m"]
        text += f" — GPS {gps['lat']:.5f}, {gps['lon']:.5f} (±{round(accuracy)} m"
        text += ", precisión baja)" if accuracy > GPS_ACCURACY_WARNING_M else ")"
    return text
```

En `RecordAdmin.quantities_pretty`, el docstring gana una frase al final del primer párrafo: "Muestra también la hora de captura y el GPS de cada fila (pieza 4b)." Dentro del bucle `for row in record.quantities:`, reemplazar

```python
            if row["note"]:
                text += f" — nota: {row['note']}"
```

por

```python
            text += _capture_flags(row, record)
            if row["note"]:
                text += f" — nota: {row['note']}"
```

- [ ] **Step 4: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/matrix/tests/test_admin.py suno/sync/tests/test_push_quantities.py suno/core/tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server/suno/core/admin.py server/suno/matrix/tests/test_admin.py
git commit -m "feat(admin): show capture time and GPS of each quantity row with warnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A5: `bootstrap_demo --with-quantities` para la E2E

**Files:**
- Modify: `server/suno/management/commands/bootstrap_demo.py`
- Modify: `docs/testing/e2e-fase0.md`
- Test: `server/suno/core/tests/test_bootstrap_demo.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

En `server/suno/core/tests/test_bootstrap_demo.py`, imports nuevos:

```python
from suno.core.context import Principal, resolve_request_context
from suno.projects.field_tasks import field_tasks, project_items
```

y, junto a `test_with_schedule_seeds_a_task_the_agent_can_report`:

```python
    def test_with_quantities_the_task_is_measured_by_quantities(self) -> None:
        call_command(
            "bootstrap_demo",
            "--org-name=Org cantidades test",
            "--username=agente-cantidades",
            "--password=una-contraseña-de-prueba-larga-42",
            "--with-schedule",
            "--with-quantities",
            stdout=io.StringIO(),
        )
        org = Organization.objects.get(name="Org cantidades test")
        user = get_user_model().objects.get(username="agente-cantidades")
        ctx = resolve_request_context(Principal(user_id=user.pk, org_id=org.pk))
        task = next(t for t in field_tasks(ctx) if t["wbs"] == "1.1")
        self.assertEqual(task["measured_by"], "quantities")
        self.assertEqual(
            [(i["code"], i["unit"], i["planned_quantity"]) for i in task["planned_items"]],
            [("1.1", "m3", 100.0)],
        )
        project = Project.objects.get(org=org)
        catalog = project_items(ctx, [project.pk])
        self.assertEqual([i["code"] for i in catalog[0]["items"]], ["1.1", "1.2"])

    def test_quantities_need_the_schedule(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_demo",
                "--org-name=Org sin cronograma cantidades",
                "--username=agente-sin-cronograma-cantidades",
                "--password=una-contraseña-de-prueba-larga-42",
                "--with-quantities",
                stdout=io.StringIO(),
            )
```

- [ ] **Step 2: Correr y ver que falla**

Run: `infra/scripts/test_docker.sh suno/core/tests/test_bootstrap_demo.py`
Expected: FAIL con `unrecognized arguments: --with-quantities`.

- [ ] **Step 3: Implementar**

En `bootstrap_demo.py`: el docstring del módulo gana el punto

```
6. Con `--with-quantities` (exige `--with-schedule`): un presupuesto aplicado con los ítems
   1.1 "Excavación mecánica" y 1.2 "Retiro de sobrantes" (m3), y 100 m3 del 1.1 planeados en
   la tarea 1.1, que pasa a medirse por cantidades (E2E de la pieza 4b de costos).
```

Imports: `from decimal import Decimal` y, en el import de `suno.core.models`, sumar `Budget`, `BudgetItem`, `BudgetItemVersion`, `BudgetRowKind`, `BudgetVersion`, `BudgetVersionStatus`, `QuantityPlan` (manteniendo el orden alfabético).

En `add_arguments`, después de `--with-schedule`:

```python
        parser.add_argument(
            "--with-quantities",
            action="store_true",
            help=(
                "Con --with-schedule: un presupuesto aplicado con los ítems 1.1 'Excavación "
                "mecánica' y 1.2 'Retiro de sobrantes', y 100 m3 del 1.1 planeados en la tarea "
                "1.1 (se mide por cantidades). Lo usa la E2E de la pieza 4b."
            ),
        )
```

En `handle`, después del cálculo de `extra_usernames`:

```python
        if options["with_quantities"] and not options["with_schedule"]:
            raise CommandError("--with-quantities necesita --with-schedule.")
```

Dentro del `with transaction.atomic():`, al final del bloque `if options["with_schedule"]:` (después de `TaskResponsible.objects.create(...)`):

```python
                if options["with_quantities"]:
                    _seed_quantities(org=org, project=project, task=leaf, user=user)
```

Función a nivel de módulo, antes de `class Command`:

```python
def _seed_quantities(*, org: Organization, project: Project, task: Task, user: Any) -> None:
    """Presupuesto aplicado mínimo y el plan de la tarea 1.1 (E2E de la pieza 4b).

    Se escribe directo (como el resto del comando) en vez de pasar por la importación de
    Excel: lo que importa a la E2E es el estado final, no el flujo de la oficina."""
    budget = Budget.objects.create(
        org=org, project=project, name="Presupuesto E2E", code="E2E", created_by=user
    )
    version = BudgetVersion.objects.create(
        org=org,
        budget=budget,
        number=1,
        status=BudgetVersionStatus.APPLIED,
        label="E2E",
        file_name="e2e.xlsx",
        source_file=b"",
        uploaded_by=user,
        applied_at=timezone.now(),
        direct_cost=Decimal("3460000.00"),
        total=Decimal("3460000.00"),
    )
    budget.contractual_version = version
    budget.save(update_fields=["contractual_version"])
    chapter = BudgetItem.objects.create(org=org, budget=budget, code="1")
    excavation = BudgetItem.objects.create(org=org, budget=budget, code="1.1")
    disposal = BudgetItem.objects.create(org=org, budget=budget, code="1.2")
    BudgetItemVersion.objects.bulk_create(
        [
            BudgetItemVersion(
                org=org,
                version=version,
                item=chapter,
                kind=BudgetRowKind.CHAPTER,
                sort_order=0,
                description="Preliminares",
                total=Decimal("3460000.00"),
            ),
            BudgetItemVersion(
                org=org,
                version=version,
                item=excavation,
                kind=BudgetRowKind.ITEM,
                sort_order=1,
                description="Excavación mecánica",
                unit="m3",
                quantity=Decimal("100"),
                unit_price=Decimal("25000"),
                total=Decimal("2500000.00"),
                parent=chapter,
            ),
            BudgetItemVersion(
                org=org,
                version=version,
                item=disposal,
                kind=BudgetRowKind.ITEM,
                sort_order=2,
                description="Retiro de sobrantes",
                unit="m3",
                quantity=Decimal("80"),
                unit_price=Decimal("12000"),
                total=Decimal("960000.00"),
                parent=chapter,
            ),
        ]
    )
    QuantityPlan.objects.create(
        org=org,
        project=project,
        task=task,
        item=excavation,
        planned_quantity=Decimal("100"),
        updated_by=user,
    )
```

Al final de `handle`, después de la línea de la tarea:

```python
        if options["with_quantities"]:
            self.stdout.write(
                "  Cantidades (E2E): 1.1 planeado 100 m3 en la tarea 1.1; 1.2 sin plan"
            )
```

- [ ] **Step 4: Documentar la variante en la E2E**

En `docs/testing/e2e-fase0.md`, antes de `## Si falla`, sección nueva:

```markdown
## Variante de cantidades (costos, pieza 4b)

    E2E_FLOW=quantities bash tool/e2e.sh

Siembra con `bootstrap_demo --with-schedule --with-quantities`: la tarea 1.1 se mide por
cantidades (1.1 "Excavación mecánica", 100 m3 planeados) y el catálogo del proyecto tiene además
1.2 "Retiro de sobrantes". Lanza `integration_test/e2e_quantities_test.dart`, que (con red) captura
12,5 m3 del 1.1 y 3 m3 del 1.2 con nota, sincroniza y marca `E2E:SYNCED`. El script valida
entonces el registro en el servidor (una `RecordReview` aprobada); la prueba sincroniza hasta
ver "validado 12,5 m3" en la tarea y marca `E2E:VALIDATED_SEEN`. Al final el script verifica en
el servidor las dos filas, con `client_id` y `location_mode`, y la revisión aprobada.
```

- [ ] **Step 5: Correr las pruebas**

Run: `infra/scripts/test_docker.sh suno/core/tests/test_bootstrap_demo.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add server/suno/management/commands/bootstrap_demo.py server/suno/core/tests/test_bootstrap_demo.py docs/testing/e2e-fase0.md
git commit -m "feat(demo): seed a quantity-measured task for the piece 4b E2E

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A6: Verificación completa, guardia, registro de avance y PR del servidor

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

Expected: todo verde salvo las 4 fallas conocidas de `test_load_script.py` y los archivos de formato anotados en la Task A0; `makemigrations --check` sin cambios (la 4b no toca modelos); cobertura ≥ la de `master`.

- [ ] **Step 2: Guardia de nombres**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/w" -v "C:/Users/gabri/APP/SUNO_server/.git:/g" \
  -e GIT_DIR=/g/worktrees/wt-server-4b -e GIT_WORK_TREE=/w \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w /w rename-py:local sh -c "python -m unittest infra/scripts/tests/test_check_naming.py && python infra/scripts/check_naming.py concept"
```

Expected: exit 0.

- [ ] **Step 3: Compatibilidad con la app 0.12.0**

Comprobar en el diff (`git diff master --stat` y lectura) que: `SCHEMA_VERSION` no cambió (`git diff master -- server/suno/serverinfo server/suno/sync/api.py | grep -i schema_version` vacío); ningún campo nuevo de `RecordQuantityPayload` es obligatorio; `FieldTaskList` solo gana una propiedad. Una app 0.12.0 sigue enviando filas de 4 claves (lo prueba `test_rows_without_them_keep_the_old_shape`).

- [ ] **Step 4: Registro de avance**

En `plans/suno-progreso.md`, sección nueva al final de la parte de costos: "Costos, pieza 4b — cantidades en la app (servidor)", con: qué se entregó (campos automáticos por fila, `project_items`, avisos de la revisión, `bootstrap_demo --with-quantities`), las decisiones del plan (resumen de la Task A0), los números de la suite (pruebas, cobertura), "PR pendiente" y "la app (Parte B) copia el contrato del merge".

- [ ] **Step 5: Commit, push y PR**

```bash
git add plans/suno-progreso.md
git commit -m "docs(plan): progress log for cost piece 4b (server)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/costos-cantidades-campo
gh pr create --base master --head feat/costos-cantidades-campo \
  --title "feat(sync): per-row capture data and item catalog for field quantities" \
  --body-file <archivo temporal en el scratchpad con: resumen; cambios del contrato (RecordQuantity, RecordQuantityPayload, QuantityGps, QuantitySeen, FieldTaskList.project_items); compatibilidad (sin SCHEMA_VERSION, app 0.12.0); pruebas y cobertura; y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)">
```

Esperar el CI (backend, DAST con Schemathesis, CodeQL). **Merge solo con aprobación del usuario.** Anotar el sha del merge en `master`: lo usa la Task B1.

---

# Parte B — App (un PR, después del merge del servidor)

### Task B0: Worktree, línea base y esquema 1 exportado

**Files:**
- Modify: `build.yaml`
- Create (generado): `drift_schemas/app/drift_schema_v1.json`

- [ ] **Step 1: Comprobar la rama y la línea base**

```bash
git -C C:/Users/gabri/APP/wt-client-4b status --short --branch
flutter pub get
dart run build_runner build --delete-conflicting-outputs
dart analyze
flutter test --concurrency=1 2>&1 | tail -3
```

Expected: `## feat/costos-cantidades-campo` limpio; analyze sin avisos; pruebas en verde. Anotar el número de pruebas.

- [ ] **Step 2: Configurar `drift_dev make-migrations`**

En `build.yaml`, dentro de `targets: $default: builders:`, junto a `json_serializable:` (mismo nivel de sangría):

```yaml
      # Migraciones de Drift (costos, pieza 4b): `dart run drift_dev make-migrations`
      # exporta el esquema de cada versión a drift_schemas/ y genera los pasos
      # (lib/data/local/database.steps.dart) y los esquemas para las pruebas
      # (test/drift/app/). Cada cambio de esquema se exporta antes de tocar las tablas.
      drift_dev:
        options:
          databases:
            app: lib/data/local/database.dart
          schema_dir: drift_schemas/
          test_dir: test/drift/
```

- [ ] **Step 3: Exportar el esquema 1 (antes de tocar las tablas)**

```bash
dart run drift_dev make-migrations
```

Expected: crea `drift_schemas/app/drift_schema_v1.json` (y quizá `test/drift/app/generated/schema_v1.dart`, `schema.dart`). Con una sola versión todavía no hay pasos.

- [ ] **Step 4: Commit**

```bash
git add build.yaml drift_schemas test/drift
git commit -m "build(drift): export schema 1 for step-by-step migrations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B1: Contrato del merge del servidor y prueba de paridad

**Files:**
- Modify: `contract/v1.yaml`, `contract/fixtures/forms_parity.json`, `contract/SOURCE`
- Modify: `pubspec.yaml`, `pubspec.lock` (`yaml` como dependencia de desarrollo directa)

- [ ] **Step 1: Copiar el contrato del merge de la Parte A**

`MERGE` es el sha del merge del PR del servidor en `master` (anotado al final de la Task A6). Solo git de lectura sobre el clon del servidor:

```bash
MERGE=<sha del merge del PR de la Parte A>
git -C C:/Users/gabri/APP/SUNO_server fetch -q origin
git -C C:/Users/gabri/APP/SUNO_server merge-base --is-ancestor "$MERGE" origin/master && echo "en master"
git -C C:/Users/gabri/APP/SUNO_server show "$MERGE:server/openapi/v1.yaml" > contract/v1.yaml
git -C C:/Users/gabri/APP/SUNO_server show "$MERGE:server/openapi/fixtures/forms_parity.json" > contract/fixtures/forms_parity.json
git diff --stat contract/
```

Expected: `en master`; solo cambia `contract/v1.yaml` (`forms_parity.json` igual).

- [ ] **Step 2: `contract/SOURCE`**

```
# Commit de PMAPP-Org/SUNO_server del que se copió contract/ (ver contract/README.md).
repo=PMAPP-Org/SUNO_server
# ref: commit del merge del PR de la pieza 4b de costos (cantidades en la app) en master.
ref=<MERGE>
```

(con el sha real en lugar de `<MERGE>`).

- [ ] **Step 3: `yaml` como dependencia de desarrollo**

En `pubspec.yaml`, en `dev_dependencies`, después de `test: ^1.31.0`:

```yaml
  # Ya en el árbol (vía build_runner); directa para que las pruebas lean
  # contract/v1.yaml (paridad del contrato de cantidades, costos pieza 4b).
  yaml: ^3.1.3
```

```bash
flutter pub get
git diff pubspec.lock
```

Expected: en `pubspec.lock`, `yaml` pasa de `transitive` a `direct dev` con la misma versión. Si `^3.1.3` no admite la versión fijada en el lock, usar `^<versión del lock>`.

- [ ] **Step 4: Commit**

```bash
git add contract pubspec.yaml pubspec.lock
git commit -m "chore(contract): copy the server contract with field quantities (piece 4b)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B2: Esquema Drift 2 y su migración

**Files:**
- Modify: `lib/data/local/tables.dart`
- Modify: `lib/data/local/database.dart`
- Create (generado): `drift_schemas/app/drift_schema_v2.json`, `lib/data/local/database.steps.dart`, `test/drift/app/generated/schema.dart`, `schema_v1.dart`, `schema_v2.dart`
- Test: `test/drift/app/migration_test.dart`

- [ ] **Step 1: Tablas y columnas nuevas**

En `lib/data/local/tables.dart`, en `class Records`, después de `progressPct`:

```dart
  /// Costos, pieza 4b: las cantidades ejecutadas, JSON con la forma del contrato
  /// (`RecordQuantityPayload` al capturar, `RecordQuantity` tras el pull). `[]` sin
  /// cantidades. Ver `core/schedule/quantity_rows.dart`.
  TextColumn get quantitiesJson => text().withDefault(const Constant('[]'))();

  /// El servidor rechazó un cambio porque el registro ya está validado
  /// (`record_validated`): desde entonces sus cantidades son de solo lectura en la
  /// captura. Solo local: el contrato no publica la revisión.
  BoolColumn get knownValidated => boolean().withDefault(const Constant(false))();
```

En `class ScheduleTasks`, después de `lastValidatedPct`:

```dart
  /// Costos, pieza 4b: `quantities` si la tarea tiene ítems planeados (la captura
  /// pide cantidades en vez del %); `pct` en el resto.
  TextColumn get measuredBy => text().withDefault(const Constant('pct'))();
```

Tablas nuevas, después de `ScheduleTaskState`:

```dart
/// Costos, pieza 4b: ítems planeados de cada tarea (`FieldTask.planned_items`). Se
/// reemplazan junto con la lista de tareas.
@DataClassName('TaskPlannedItemRow')
class TaskPlannedItems extends Table {
  TextColumn get taskId => text()();
  TextColumn get itemId => text()();
  TextColumn get code => text()();
  TextColumn get description => text()();
  TextColumn get unit => text()();
  RealColumn get plannedQuantity => real()();

  /// Lo validado en la tarea a la fecha de la última descarga (`executed_quantity`).
  RealColumn get validatedQuantity => real()();
  IntColumn get position => integer()();

  @override
  Set<Column> get primaryKey => {taskId, itemId};
}

/// Costos, pieza 4b: dónde se puede precisar la ubicación de una fila de cantidad
/// (`FieldTask.location_options`): la ubicación de la tarea y sus descendientes.
@DataClassName('TaskLocationOptionRow')
class TaskLocationOptions extends Table {
  TextColumn get taskId => text()();
  TextColumn get locationId => text()();
  TextColumn get label => text()();

  /// Es la ubicación de la propia tarea (`FieldTask.location`); las demás la precisan.
  BoolColumn get isTaskLocation => boolean()();
  IntColumn get position => integer()();

  @override
  Set<Column> get primaryKey => {taskId, locationId};
}

/// Costos, pieza 4b: ítems medibles del presupuesto vigente de cada proyecto
/// (`FieldTaskList.project_items`), para "Agregar ítem no planeado".
@DataClassName('ProjectItemRow')
class ProjectItems extends Table {
  TextColumn get projectId => text()();
  TextColumn get itemId => text()();
  TextColumn get code => text()();
  TextColumn get description => text()();
  TextColumn get unit => text()();
  IntColumn get position => integer()();

  @override
  Set<Column> get primaryKey => {projectId, itemId};
}

/// Costos, pieza 4b: código, descripción y unidad de cada ítem visto alguna vez. A
/// diferencia del catálogo, no se reemplaza: un ítem que sale del presupuesto vigente
/// (p. ej. se volvió capítulo) se sigue pudiendo nombrar en el detalle de un registro o
/// en un rechazo del servidor.
@DataClassName('KnownItemRow')
class KnownItems extends Table {
  TextColumn get itemId => text()();
  TextColumn get code => text()();
  TextColumn get description => text()();
  TextColumn get unit => text()();

  @override
  Set<Column> get primaryKey => {itemId};
}
```

- [ ] **Step 2: Base de datos: tablas, esquema 2 y reemplazo de la lista**

En `lib/data/local/database.dart`:

- import nuevo, después de `import 'tables.dart';`:

```dart
import 'database.steps.dart';
```

- en `@DriftDatabase(tables: [...])`, después de `ScheduleTaskState,`:

```dart
    TaskPlannedItems,
    TaskLocationOptions,
    ProjectItems,
    KnownItems,
```

- `schemaVersion` y `migration` pasan a:

```dart
  @override
  int get schemaVersion => 2;

  // La app `com.suno.app` empezó en el esquema 1. Cada cambio se exporta con
  // `dart run drift_dev make-migrations` (drift_schemas/) y su paso se escribe aquí;
  // `test/drift/app/migration_test.dart` lo verifica contra el esquema exportado.
  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (migrator) => migrator.createAll(),
    onUpgrade: stepByStep(
      // Costos, pieza 4b: cantidades ejecutadas en la captura.
      from1To2: (m, schema) async {
        await m.addColumn(schema.records, schema.records.quantitiesJson);
        await m.addColumn(schema.records, schema.records.knownValidated);
        await m.addColumn(schema.scheduleTasks, schema.scheduleTasks.measuredBy);
        await m.createTable(schema.taskPlannedItems);
        await m.createTable(schema.taskLocationOptions);
        await m.createTable(schema.projectItems);
        await m.createTable(schema.knownItems);
        // La lista guardada no sabe cómo se mide cada tarea: sin su versión, la
        // próxima sincronización la baja entera.
        await customStatement('DELETE FROM schedule_task_state');
      },
    ),
  );
```

- `replaceScheduleTasks` pasa a:

```dart
  /// Reemplaza la lista de tareas del cronograma, su versión y lo que cuelga de ella
  /// (costos, pieza 4b: ítems planeados, opciones de ubicación y el catálogo de ítems
  /// de sus proyectos) en una sola transacción: nunca queda media lista. Los datos de
  /// los ítems vistos se guardan además en `KnownItems`, que no se borra.
  Future<void> replaceScheduleTasks({
    required String orgId,
    required String userId,
    required String version,
    required List<ScheduleTasksCompanion> rows,
    required DateTime now,
    List<TaskPlannedItemsCompanion> planned = const [],
    List<TaskLocationOptionsCompanion> locations = const [],
    List<ProjectItemsCompanion> catalog = const [],
  }) {
    return transaction(() async {
      await delete(scheduleTasks).go();
      await delete(taskPlannedItems).go();
      await delete(taskLocationOptions).go();
      await delete(projectItems).go();
      await batch((b) {
        b.insertAll(scheduleTasks, rows);
        b.insertAll(taskPlannedItems, planned);
        b.insertAll(taskLocationOptions, locations);
        b.insertAll(projectItems, catalog);
        b.insertAllOnConflictUpdate(knownItems, [
          for (final p in planned)
            KnownItemsCompanion.insert(
              itemId: p.itemId.value,
              code: p.code.value,
              description: p.description.value,
              unit: p.unit.value,
            ),
          for (final c in catalog)
            KnownItemsCompanion.insert(
              itemId: c.itemId.value,
              code: c.code.value,
              description: c.description.value,
              unit: c.unit.value,
            ),
        ]);
      });
      await into(scheduleTaskState).insertOnConflictUpdate(
        ScheduleTaskStateCompanion.insert(orgId: orgId, userId: userId, version: version, updatedAt: now),
      );
    });
  }
```

- [ ] **Step 3: Exportar el esquema 2 y generar los pasos**

```bash
dart run drift_dev make-migrations
dart run build_runner build --delete-conflicting-outputs
```

Expected: `drift_schemas/app/drift_schema_v2.json`, `lib/data/local/database.steps.dart` (con `stepByStep` y `from1To2`), `test/drift/app/generated/schema.dart`, `schema_v1.dart`, `schema_v2.dart` y, si no existía, una plantilla `test/drift/app/migration_test.dart`. Si `dart analyze lib/data/local` falla porque `schema.records.knownValidated` u otro getter tiene otro nombre en `database.steps.dart`, usar el nombre que generó (la convención es el nombre Dart de la columna).

- [ ] **Step 4: Escribir la prueba de la migración**

Reemplazar el contenido de `test/drift/app/migration_test.dart` (la plantilla generada) por:

```dart
// Migración de la base local 1 → 2 (costos, pieza 4b): columnas y tablas nuevas, sin
// perder registros ni tareas, y la lista de tareas vuelve a bajarse entera.
import 'package:drift/drift.dart';
import 'package:drift_dev/api/migrations_native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/data/local/database.dart';

import 'generated/schema.dart';

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late SchemaVerifier verifier;

  setUpAll(() => verifier = SchemaVerifier(GeneratedHelper()));

  test('1 → 2: el esquema queda igual al de una instalación nueva', () async {
    final schema = await verifier.schemaAt(1);
    final db = AppDatabase.forTesting(schema.newConnection());
    await verifier.migrateAndValidate(db, 2);
    await db.close();
  });

  test('1 → 2: conserva registros y tareas, con los valores por defecto nuevos', () async {
    final schema = await verifier.schemaAt(1);
    schema.rawDatabase
      ..execute(
        "INSERT INTO records (id, org_id, version, template_version_id, answers_json, captured_at, "
        "device_id, created_by, task_id, progress_pct) "
        "VALUES ('r-1', 'org-1', 1, 'tv-1', '{}', 1762500000, 'd-1', 'u-1', 't-1', 40.0)",
      )
      ..execute(
        "INSERT INTO schedule_tasks (id, org_id, user_id, project_id, project_name, wbs, "
        "outline_number, name, position) "
        "VALUES ('t-1', 'org-1', 'u-1', 'p-1', 'Obra', '1.1', '1.1', 'Excavación', 0)",
      )
      ..execute(
        "INSERT INTO schedule_task_state (id, org_id, user_id, version, updated_at) "
        "VALUES (0, 'org-1', 'u-1', 'v1', 1762500000)",
      );
    final db = AppDatabase.forTesting(schema.newConnection());
    await verifier.migrateAndValidate(db, 2);

    final record = await db.select(db.records).getSingle();
    expect((record.id, record.progressPct, record.quantitiesJson, record.knownValidated), ('r-1', 40.0, '[]', false));
    final task = await db.select(db.scheduleTasks).getSingle();
    expect((task.id, task.measuredBy), ('t-1', 'pct'));
    expect(await db.readScheduleTaskState(), isNull);
    expect(await db.select(db.taskPlannedItems).get(), isEmpty);
    await db.close();
  });
}
```

- [ ] **Step 5: Correr las pruebas**

```bash
flutter test test/drift/app/migration_test.dart test/data/local
```

Expected: PASS. (Si un test existente de `test/data/local/database_test.dart` llama a `replaceScheduleTasks`, sigue compilando: los parámetros nuevos son opcionales.)

- [ ] **Step 6: Commit**

```bash
git add lib/data/local test/drift drift_schemas
git commit -m "feat(db): schema 2 with field quantities, task items, locations and catalog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B3: Lógica pura de las filas de cantidad

**Files:**
- Create: `lib/core/schedule/quantity_rows.dart`
- Test: `test/core/schedule/quantity_rows_test.dart`, `test/contract/quantities_contract_test.dart`

- [ ] **Step 1: Escribir las pruebas que fallan**

`test/core/schedule/quantity_rows_test.dart`:

```dart
// Filas de cantidad (costos, pieza 4b): número, notas obligatorias, sobreejecución,
// payload del contrato y lectura de lo guardado. Puro.
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/schedule/quantity_rows.dart';

const _concrete = QuantityItem(itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3');
const _steel = QuantityItem(itemId: 'item-2', code: '2.2', description: 'Acero de refuerzo', unit: 'kg');

QuantityRowDraft _planned(String id, String text, {String note = '', double validated = 4}) => QuantityRowDraft(
  clientId: id,
  item: _concrete,
  planned: 10,
  validated: validated,
  isPlanned: true,
  text: text,
  note: note,
);

QuantityRowDraft _unplanned(String id, String text, {String note = ''}) =>
    QuantityRowDraft(clientId: id, item: _steel, text: text, note: note, added: true);

void main() {
  group('parseQuantity', () {
    test('acepta coma o punto, signo y hasta 4 decimales', () {
      expect(parseQuantity('12,5'), 12.5);
      expect(parseQuantity(' 3.0001 '), 3.0001);
      expect(parseQuantity('-2'), -2);
      expect(parseQuantity('99999999999999,9999'), closeTo(99999999999999.9999, 0.01));
    });

    test('rechaza lo demás', () {
      for (final text in ['', 'abc', '1,23456', '123456789012345', '1e3', '--1', '1,']) {
        expect(parseQuantity(text), isNull, reason: text);
      }
    });
  });

  test('formatQuantity: sin ceros sobrantes y con coma', () {
    expect(formatQuantity(10), '10');
    expect(formatQuantity(2.5), '2,5');
    expect(formatQuantity(-0.125), '-0,125');
    expect(formatQuantity(1.23456), '1,2346');
  });

  group('checkQuantityRows', () {
    test('ítem no planeado y ajuste negativo exigen nota', () {
      final check = checkQuantityRows([_planned('a', '-1'), _unplanned('b', '3'), _planned('c', '2')]);
      expect(check.rows, {'a': QuantityRowProblem.noteRequired, 'b': QuantityRowProblem.noteRequired});
      expect(check.ok, isFalse);
      expect(checkQuantityRows([_planned('a', '-1', note: 'ajuste'), _unplanned('b', '3', note: 'extra')]).ok, isTrue);
    });

    test('las filas vacías o en 0 no cuentan; hace falta al menos una cantidad', () {
      final check = checkQuantityRows([_planned('a', ''), _planned('b', '0'), _unplanned('c', '')]);
      expect(check.rows, isEmpty);
      expect(check.noQuantity, isTrue);
    });

    test('un número mal escrito se señala en su fila', () {
      expect(checkQuantityRows([_planned('a', '1,23456')]).rows, {'a': QuantityRowProblem.invalidNumber});
    });

    test('hasta 200 filas con cantidad', () {
      final rows = [for (var i = 0; i < maxQuantityRows + 1; i++) _planned('r$i', '1')];
      expect(checkQuantityRows(rows).tooManyRows, isTrue);
      expect(checkQuantityRows(rows.sublist(1)).tooManyRows, isFalse);
    });

    test('nota de más de 500 caracteres', () {
      expect(
        checkQuantityRows([_unplanned('a', '1', note: 'x' * 501)]).rows,
        {'a': QuantityRowProblem.noteTooLong},
      );
    });
  });

  test('sobreejecución: lo validado más la suma de hoy del ítem supera lo planeado', () {
    expect(overrunItems([_planned('a', '3'), _planned('b', '4')]), {'item-1'});
    expect(overrunItems([_planned('a', '6')]), isEmpty);
    expect(todayTotal([_planned('a', '3'), _planned('b', '2,5')], 'item-1'), 5.5);
  });

  test('payload: solo las filas con cantidad distinta de 0, con la forma del contrato', () {
    final row = _planned('a', '12,5', note: ' bien ').copyWith(
      locationId: 'loc-2',
      capturedAt: DateTime.utc(2026, 11, 9, 14, 3),
      gps: const GpsReading(lat: 4.6, lon: -74.08, accuracyM: 7),
      seen: const QuantitySeen(planned: 10, validated: 4, locationId: 'loc-1'),
    );
    expect(quantityPayload([row, _planned('b', '0'), _planned('c', '')]), [
      {
        'item_id': 'item-1',
        'location_id': 'loc-2',
        'quantity': 12.5,
        'note': 'bien',
        'client_id': 'a',
        'captured_at': '2026-11-09T14:03:00.000Z',
        'gps': {'lat': 4.6, 'lon': -74.08, 'accuracy_m': 7.0},
        'seen': {'planned': 10.0, 'validated': 4.0, 'location_id': 'loc-1'},
        'location_mode': 'refined',
      },
    ]);
  });

  test('lo guardado vuelve como fila (también una del pull sin datos automáticos)', () {
    final row = QuantityRowDraft.fromStored(
      {
        'item_id': 'item-1',
        'location_id': null,
        'quantity': 3.5,
        'note': '',
        'client_id': null,
        'captured_at': null,
        'gps': null,
        'seen': null,
        'location_mode': null,
      },
      item: _concrete,
      planned: const PlannedQuantity(item: _concrete, planned: 10, validated: 4),
      fallbackId: 'nuevo',
    );
    expect((row.clientId, row.text, row.isPlanned, row.locationMode, row.capturedAt), ('nuevo', '3,5', true, 'task', null));
    final full = QuantityRowDraft.fromStored(
      quantityPayload([_planned('a', '2').copyWith(capturedAt: DateTime.utc(2026), gps: const GpsReading(lat: 1, lon: 2, accuracyM: 3))]).single,
      item: _concrete,
      fallbackId: 'x',
    );
    expect((full.clientId, full.capturedAt, full.gps?.accuracyM, full.isPlanned), ('a', DateTime.utc(2026), 3.0, false));
  });

  test('sameQuantities compara ítem, ubicación, cantidad y nota', () {
    final a = [
      {'item_id': 'i', 'location_id': null, 'quantity': 2, 'note': 'x', 'gps': null},
    ];
    final b = [
      {'item_id': 'i', 'location_id': null, 'quantity': 2.0, 'note': 'x', 'client_id': 'c', 'seen': {'planned': 1}},
    ];
    expect(sameQuantities(a, b), isTrue);
    expect(sameQuantities(a, [{...b.single, 'quantity': 3}]), isFalse);
    expect(sameQuantities(const [], decodeQuantities('[]')), isTrue);
  });

  test('QuantityLine: código y unidad conocidos, o el id si no', () {
    final line = QuantityLine.fromStored({'item_id': 'item-9', 'location_id': 'loc-2', 'quantity': 1, 'note': 'n'});
    expect((line.code, line.label, line.locationId, line.quantity), ('', 'item-9', 'loc-2', 1.0));
    final known = QuantityLine.fromStored({'item_id': 'item-1', 'location_id': null, 'quantity': 2.5, 'note': ''}, item: _concrete);
    expect((known.label, known.unit), ('2.1 Concreto zapatas', 'm3'));
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/core/schedule/quantity_rows_test.dart`
Expected: FAIL de compilación (`quantity_rows.dart` no existe).

- [ ] **Step 3: Implementar `lib/core/schedule/quantity_rows.dart`**

```dart
// Cantidades ejecutadas en campo (costos, pieza 4b): las filas que el agente digita en
// un registro de una tarea medida por cantidades. Puro: sin Flutter ni BD; lo comparten
// la captura, la corrección de rechazos, el detalle y las exportaciones.
//
// Reglas (spec, sección 2.2): solo se guardan las filas con cantidad distinta de 0;
// hace falta al menos una; hasta 4 decimales y |cantidad| < 10^14; la nota es
// obligatoria si el ítem no está planeado o la cantidad es negativa (ajuste), y tiene
// hasta 500 caracteres; hasta 200 filas. La sobreejecución solo se avisa.
import 'dart:convert';

import 'package:meta/meta.dart';

/// Filas de cantidad por registro (`MAX_QUANTITY_ROWS` del servidor).
const maxQuantityRows = 200;

/// Caracteres de la nota de una fila.
const maxQuantityNote = 500;

/// Hasta 14 dígitos enteros (|q| < 10^14) y 4 decimales, con coma o punto.
final _quantityPattern = RegExp(r'^-?\d{1,14}([.,]\d{1,4})?$');

/// La cantidad escrita, o `null` si no es un número válido.
double? parseQuantity(String text) {
  final value = text.trim();
  if (!_quantityPattern.hasMatch(value)) return null;
  return double.parse(value.replaceAll(',', '.'));
}

/// `10.0` → `10`; `2.5` → `2,5`; hasta 4 decimales.
String formatQuantity(double value) =>
    value.toStringAsFixed(4).replaceFirst(RegExp(r'\.?0+$'), '').replaceAll('.', ',');

/// Un ítem del presupuesto tal como lo muestra la app.
@immutable
class QuantityItem {
  const QuantityItem({required this.itemId, required this.code, required this.description, required this.unit});

  /// Un ítem del que el teléfono no tiene datos (llegó en un registro de otro proyecto).
  const QuantityItem.unknown(this.itemId) : code = '', description = '', unit = '';

  final String itemId;
  final String code;
  final String description;
  final String unit;

  String get label => code.isEmpty ? itemId : '$code $description';
}

/// Un ítem planeado de la tarea, con lo planeado y lo validado a la fecha.
@immutable
class PlannedQuantity {
  const PlannedQuantity({required this.item, required this.planned, required this.validated});
  final QuantityItem item;
  final double planned;
  final double validated;
}

/// Una ubicación elegible para una fila: la de la tarea o una que la precisa.
@immutable
class LocationChoice {
  const LocationChoice({required this.id, required this.label, required this.isTaskLocation});
  final String id;
  final String label;
  final bool isTaskLocation;
}

/// Lo que la captura de cantidades sabe de una tarea.
@immutable
class QuantityTaskContext {
  const QuantityTaskContext({
    required this.taskId,
    this.planned = const [],
    this.locations = const [],
    this.catalog = const [],
  });

  final String taskId;
  final List<PlannedQuantity> planned;
  final List<LocationChoice> locations;

  /// Ítems medibles del presupuesto vigente del proyecto de la tarea.
  final List<QuantityItem> catalog;

  /// La ubicación de la tarea, o `null` si no tiene.
  LocationChoice? get taskLocation => locations.where((l) => l.isTaskLocation).firstOrNull;

  /// Las ubicaciones con que se puede precisar la de la tarea.
  List<LocationChoice> get refinements => [for (final l in locations) if (!l.isTaskLocation) l];

  PlannedQuantity? plannedFor(String itemId) => planned.where((p) => p.item.itemId == itemId).firstOrNull;

  QuantityItem? catalogItem(String itemId) => catalog.where((i) => i.itemId == itemId).firstOrNull;

  /// "Agregar ítem no planeado": el catálogo sin los ítems ya planeados.
  List<QuantityItem> get unplannedCatalog => [
    for (final item in catalog)
      if (plannedFor(item.itemId) == null) item,
  ];

  /// Los planeados y el catálogo, sin repetir (para reasignar una fila rechazada).
  List<QuantityItem> get allItems => [
    for (final p in planned) p.item,
    ...unplannedCatalog,
  ];
}

/// Contrato `QuantityGps`.
@immutable
class GpsReading {
  const GpsReading({required this.lat, required this.lon, required this.accuracyM});

  factory GpsReading.fromJson(Map<String, Object?> json) => GpsReading(
    lat: (json['lat']! as num).toDouble(),
    lon: (json['lon']! as num).toDouble(),
    accuracyM: (json['accuracy_m']! as num).toDouble(),
  );

  final double lat;
  final double lon;
  final double accuracyM;

  Map<String, Object?> toJson() => {'lat': lat, 'lon': lon, 'accuracy_m': accuracyM};
}

/// Contrato `QuantitySeen`: lo que la app mostraba al capturar la fila.
@immutable
class QuantitySeen {
  const QuantitySeen({required this.planned, required this.validated, required this.locationId});

  factory QuantitySeen.fromJson(Map<String, Object?> json) => QuantitySeen(
    planned: (json['planned'] as num?)?.toDouble(),
    validated: (json['validated'] as num?)?.toDouble(),
    locationId: json['location_id'] as String?,
  );

  final double? planned;
  final double? validated;

  /// La ubicación de la tarea que se mostraba; `null` si no tenía.
  final String? locationId;

  Map<String, Object?> toJson() => {'planned': planned, 'validated': validated, 'location_id': locationId};
}

const Object _keep = Object();

/// Una fila de cantidad mientras se captura o se corrige.
@immutable
class QuantityRowDraft {
  const QuantityRowDraft({
    required this.clientId,
    required this.item,
    this.planned,
    this.validated,
    this.isPlanned = false,
    this.text = '',
    this.note = '',
    this.locationId,
    this.capturedAt,
    this.gps,
    this.seen,
    this.added = false,
  });

  /// Una fila guardada (forma del contrato) vuelta a la pantalla. [planned] es el ítem
  /// planeado de la tarea, si lo es; una fila sin `client_id` (captura anterior a la
  /// pieza 4b) toma [fallbackId].
  factory QuantityRowDraft.fromStored(
    Map<String, Object?> json, {
    required QuantityItem item,
    PlannedQuantity? planned,
    required String fallbackId,
    bool added = false,
  }) {
    final captured = json['captured_at'];
    final gps = json['gps'];
    final seen = json['seen'];
    return QuantityRowDraft(
      clientId: json['client_id'] as String? ?? fallbackId,
      item: item,
      planned: planned?.planned,
      validated: planned?.validated,
      isPlanned: planned != null,
      text: formatQuantity((json['quantity']! as num).toDouble()),
      note: json['note'] as String? ?? '',
      locationId: json['location_id'] as String?,
      capturedAt: captured is String ? DateTime.parse(captured) : null,
      gps: gps is Map ? GpsReading.fromJson(gps.cast<String, Object?>()) : null,
      seen: seen is Map ? QuantitySeen.fromJson(seen.cast<String, Object?>()) : null,
      added: added,
    );
  }

  final String clientId;
  final QuantityItem item;
  final double? planned;
  final double? validated;
  final bool isPlanned;

  /// Lo escrito en "Cantidad hoy".
  final String text;
  final String note;

  /// `null`: la ubicación de la tarea.
  final String? locationId;
  final DateTime? capturedAt;
  final GpsReading? gps;
  final QuantitySeen? seen;

  /// Fila agregada por el agente (ítem no planeado u otra fila de un ítem): se puede quitar.
  final bool added;

  double? get quantity => parseQuantity(text);
  bool get hasQuantity => (quantity ?? 0) != 0;
  bool get isAdjustment => (quantity ?? 0) < 0;

  /// Ítem fuera del plan o ajuste negativo.
  bool get needsNote => !isPlanned || isAdjustment;
  String get locationMode => locationId == null ? 'task' : 'refined';

  QuantityRowDraft copyWith({
    QuantityItem? item,
    Object? planned = _keep,
    Object? validated = _keep,
    bool? isPlanned,
    String? text,
    String? note,
    Object? locationId = _keep,
    Object? capturedAt = _keep,
    Object? gps = _keep,
    Object? seen = _keep,
    bool? added,
  }) => QuantityRowDraft(
    clientId: clientId,
    item: item ?? this.item,
    planned: identical(planned, _keep) ? this.planned : planned as double?,
    validated: identical(validated, _keep) ? this.validated : validated as double?,
    isPlanned: isPlanned ?? this.isPlanned,
    text: text ?? this.text,
    note: note ?? this.note,
    locationId: identical(locationId, _keep) ? this.locationId : locationId as String?,
    capturedAt: identical(capturedAt, _keep) ? this.capturedAt : capturedAt as DateTime?,
    gps: identical(gps, _keep) ? this.gps : gps as GpsReading?,
    seen: identical(seen, _keep) ? this.seen : seen as QuantitySeen?,
    added: added ?? this.added,
  );

  /// Contrato `RecordQuantityPayload`.
  Map<String, Object?> toPayload() => {
    'item_id': item.itemId,
    'location_id': locationId,
    'quantity': quantity,
    'note': note.trim(),
    'client_id': clientId,
    'captured_at': capturedAt?.toUtc().toIso8601String(),
    'gps': gps?.toJson(),
    'seen': seen?.toJson(),
    'location_mode': locationMode,
  };
}

enum QuantityRowProblem { invalidNumber, noteRequired, noteTooLong }

/// Resultado de validar las filas al guardar.
@immutable
class QuantityCheck {
  const QuantityCheck({this.rows = const {}, this.noQuantity = false, this.tooManyRows = false});

  /// Problema de cada fila, por `clientId`.
  final Map<String, QuantityRowProblem> rows;

  /// Ninguna fila tiene una cantidad distinta de 0.
  final bool noQuantity;
  final bool tooManyRows;

  bool get ok => rows.isEmpty && !noQuantity && !tooManyRows;
}

QuantityCheck checkQuantityRows(List<QuantityRowDraft> rows) {
  final problems = <String, QuantityRowProblem>{};
  var counted = 0;
  for (final row in rows) {
    if (row.text.trim().isEmpty) continue;
    final quantity = row.quantity;
    if (quantity == null) {
      problems[row.clientId] = QuantityRowProblem.invalidNumber;
      continue;
    }
    if (quantity == 0) continue;
    counted++;
    final note = row.note.trim();
    if (note.length > maxQuantityNote) {
      problems[row.clientId] = QuantityRowProblem.noteTooLong;
    } else if (row.needsNote && note.isEmpty) {
      problems[row.clientId] = QuantityRowProblem.noteRequired;
    }
  }
  return QuantityCheck(rows: problems, noQuantity: counted == 0, tooManyRows: counted > maxQuantityRows);
}

/// La suma de lo escrito hoy para [itemId] (las filas válidas).
double todayTotal(List<QuantityRowDraft> rows, String itemId) {
  var total = 0.0;
  for (final row in rows) {
    if (row.item.itemId == itemId) total += row.quantity ?? 0;
  }
  return total;
}

/// Ítems planeados cuyo validado más lo de hoy supera lo planeado. Solo se avisa.
Set<String> overrunItems(List<QuantityRowDraft> rows) => {
  for (final row in rows)
    if (row.isPlanned && row.planned != null && (row.validated ?? 0) + todayTotal(rows, row.item.itemId) > row.planned!)
      row.item.itemId,
};

/// Las filas que viajan: las que tienen una cantidad distinta de 0.
List<Map<String, Object?>> quantityPayload(List<QuantityRowDraft> rows) => [
  for (final row in rows)
    if (row.hasQuantity) row.toPayload(),
];

/// `Records.quantitiesJson` como lista; `[]` si no se entiende.
List<Map<String, Object?>> decodeQuantities(String json) {
  final decoded = jsonDecode(json);
  return decoded is List ? [for (final row in decoded) if (row is Map) row.cast<String, Object?>()] : const [];
}

/// ¿Las dos listas registran lo mismo? Compara ítem, ubicación, cantidad y nota (los
/// datos automáticos y la forma del pull no cuentan).
bool sameQuantities(List<Map<String, Object?>> a, List<Map<String, Object?>> b) {
  List<Object?> key(Map<String, Object?> row) => [
    row['item_id'],
    row['location_id'],
    (row['quantity'] as num?)?.toDouble(),
    (row['note'] as String? ?? '').trim(),
  ];
  return jsonEncode([for (final row in a) key(row)]) == jsonEncode([for (final row in b) key(row)]);
}

/// Una fila guardada, para mostrar o exportar.
@immutable
class QuantityLine {
  const QuantityLine({
    required this.itemId,
    required this.code,
    required this.description,
    required this.unit,
    required this.locationId,
    required this.locationLabel,
    required this.quantity,
    required this.note,
  });

  factory QuantityLine.fromStored(Map<String, Object?> json, {QuantityItem? item, String? locationLabel}) {
    final itemId = json['item_id']! as String;
    final known = item ?? QuantityItem.unknown(itemId);
    return QuantityLine(
      itemId: itemId,
      code: known.code,
      description: known.description,
      unit: known.unit,
      locationId: json['location_id'] as String?,
      locationLabel: locationLabel,
      quantity: (json['quantity']! as num).toDouble(),
      note: json['note'] as String? ?? '',
    );
  }

  final String itemId;
  final String code;
  final String description;
  final String unit;

  /// `null`: la ubicación de la tarea.
  final String? locationId;

  /// Etiqueta de [locationId] si el teléfono la conoce.
  final String? locationLabel;
  final double quantity;
  final String note;

  String get label => code.isEmpty ? itemId : '$code $description';
}
```

- [ ] **Step 4: Prueba de paridad con el contrato copiado en la Task B1**

`test/contract/quantities_contract_test.dart`:

```dart
// Paridad con el contrato (costos, pieza 4b): lo que la app envía en cada fila de
// cantidad y lo que lee de la lista de tareas existe en `contract/v1.yaml`, la copia
// fijada del contrato del servidor.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/schedule/quantity_rows.dart';
import 'package:yaml/yaml.dart';

void main() {
  final schemas =
      ((loadYaml(File('contract/v1.yaml').readAsStringSync()) as Map)['components'] as Map)['schemas'] as Map;

  Set<String> properties(String name) => {
    for (final key in ((schemas[name] as Map)['properties'] as Map).keys) key as String,
  };
  Set<String> required(String name) => {for (final key in ((schemas[name] as Map)['required'] as List?) ?? const []) key as String};

  final row = QuantityRowDraft(
    clientId: '0190f5d2-0000-7000-8000-000000000001',
    item: const QuantityItem(itemId: 'item-1', code: '1.1', description: 'Excavación', unit: 'm3'),
    planned: 100,
    validated: 4,
    isPlanned: true,
    text: '12,5',
    capturedAt: DateTime.utc(2026, 11, 9, 14),
    gps: const GpsReading(lat: 4.6, lon: -74.08, accuracyM: 7),
    seen: const QuantitySeen(planned: 100, validated: 4, locationId: null),
  ).toPayload();

  test('cada clave de la fila que se envía está en RecordQuantityPayload', () {
    expect(properties('RecordQuantityPayload'), containsAll(row.keys));
    expect(row.keys, containsAll(required('RecordQuantityPayload')));
  });

  test('gps y seen tienen exactamente las claves del contrato', () {
    expect((row['gps']! as Map).keys.toSet(), required('QuantityGps'));
    expect((row['seen']! as Map).keys.toSet(), required('QuantitySeen'));
  });

  test('lo que se guarda tras el pull tiene las claves de RecordQuantity', () {
    expect(required('RecordQuantity'), containsAll(row.keys));
  });

  test('la lista de tareas trae lo que lee ScheduleTaskSync', () {
    expect(properties('FieldTaskList'), containsAll(['version', 'changed', 'items', 'project_items']));
    expect(properties('FieldTask'), containsAll(['measured_by', 'location', 'location_options', 'planned_items']));
    expect(properties('FieldPlannedItem'), containsAll(['item_id', 'code', 'description', 'unit', 'planned_quantity', 'executed_quantity']));
    expect(properties('FieldLocation'), containsAll(['id', 'label']));
    expect(properties('FieldProjectItems'), containsAll(['project_id', 'items']));
    expect(properties('FieldProjectItem'), containsAll(['item_id', 'code', 'description', 'unit']));
  });
}
```

- [ ] **Step 5: Correr las pruebas**

```bash
flutter test test/core/schedule/quantity_rows_test.dart test/contract/quantities_contract_test.dart
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lib/core/schedule/quantity_rows.dart test/core/schedule/quantity_rows_test.dart test/contract/quantities_contract_test.dart
git commit -m "feat(schedule): pure rules for field quantity rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B4: Descargar y leer los datos de cantidades de la lista de tareas

**Files:**
- Modify: `lib/core/schedule/schedule_task_sync.dart` (`ScheduleTaskInfo`, `ScheduleTaskPage`, `_refresh`; clases nuevas `PlannedItemInfo`, `LocationInfo`, `CatalogItemInfo`, `ProjectCatalog`)
- Modify: `lib/data/local/schedule_task_repository.dart` (`ScheduleTaskItem`, métodos nuevos)
- Test: `test/core/schedule/schedule_task_sync_test.dart`

Usa `QuantityItem`, `PlannedQuantity`, `LocationChoice` y `QuantityTaskContext` de `lib/core/schedule/quantity_rows.dart` (Task B3).

- [ ] **Step 1: Escribir las pruebas que fallan**

Al final de `main()` en `test/core/schedule/schedule_task_sync_test.dart`:

```dart
  group('costos, pieza 4b', () {
    ScheduleTaskPage measuredPage() => ScheduleTaskPage.fromJson({
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
          'measured_by': 'quantities',
          'location': {'id': 'loc-1', 'label': 'Tramo 2'},
          'location_options': [
            {'id': 'loc-1', 'label': 'Tramo 2'},
            {'id': 'loc-2', 'label': 'Tramo 2 / PR 1'},
          ],
          'planned_items': [
            {
              'item_id': 'item-1',
              'code': '2.1',
              'description': 'Concreto zapatas',
              'unit': 'm3',
              'planned_quantity': 10,
              'executed_quantity': 4.5,
            },
          ],
        },
      ],
      'project_items': [
        {
          'project_id': 'p-1',
          'items': [
            {'item_id': 'item-1', 'code': '2.1', 'description': 'Concreto zapatas', 'unit': 'm3'},
            {'item_id': 'item-2', 'code': '2.2', 'description': 'Acero de refuerzo', 'unit': 'kg'},
          ],
        },
      ],
    });

    test('guarda cómo se mide la tarea, sus ítems, sus ubicaciones y el catálogo', () async {
      transport.responses.add(measuredPage());
      expect(await sync.refresh(), isTrue);
      final repo = ScheduleTaskRepository(db);
      final task = (await repo.forUser(orgId: 'org-1', userId: 'ana')).single;
      expect((task.projectId, task.measuredBy, task.measuredByQuantities), ('p-1', 'quantities', true));

      final context = await repo.quantityContext(taskId: 't-3', projectId: 'p-1');
      expect(context.planned.single.item.label, '2.1 Concreto zapatas');
      expect((context.planned.single.planned, context.planned.single.validated), (10.0, 4.5));
      expect(context.taskLocation?.label, 'Tramo 2');
      expect([for (final l in context.refinements) l.label], ['Tramo 2 / PR 1']);
      expect([for (final i in context.catalog) i.code], ['2.1', '2.2']);
      expect([for (final i in context.unplannedCatalog) i.code], ['2.2']);
      expect((await repo.knownItems({'item-2'}))['item-2']?.unit, 'kg');
      expect(await repo.locationLabels({'loc-2', 'loc-9'}), {'loc-2': 'Tramo 2 / PR 1'});
    });

    test('los ítems conocidos sobreviven a un catálogo nuevo', () async {
      transport.responses
        ..add(measuredPage())
        ..add(_page('v3', [_task('t-3', '1.2', 'Descapote')]));
      await sync.refresh();
      await sync.refresh();
      final repo = ScheduleTaskRepository(db);
      expect((await repo.quantityContext(taskId: 't-3', projectId: 'p-1')).catalog, isEmpty);
      expect((await repo.knownItems({'item-1'}))['item-1']?.code, '2.1');
    });

    test('una tarea sin las claves nuevas se mide por %', () async {
      transport.responses.add(_page('v1', [_task('t-2', '1.1', 'Localización')]));
      await sync.refresh();
      final task = (await ScheduleTaskRepository(db).forUser(orgId: 'org-1', userId: 'ana')).single;
      expect(task.measuredBy, 'pct');
      expect(await ScheduleTaskRepository(db).byId('t-2'), isNotNull);
    });
  });
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `flutter test test/core/schedule/schedule_task_sync_test.dart`
Expected: FAIL de compilación (`measuredBy`, `quantityContext`… no existen).

- [ ] **Step 3: `schedule_task_sync.dart`**

Antes de `/// Contrato `FieldTask`.`:

```dart
/// Contrato `FieldPlannedItem` (costos, pieza 4).
@immutable
class PlannedItemInfo {
  const PlannedItemInfo({
    required this.itemId,
    required this.code,
    required this.description,
    required this.unit,
    required this.plannedQuantity,
    required this.validatedQuantity,
  });

  factory PlannedItemInfo.fromJson(Map<String, dynamic> json) => PlannedItemInfo(
    itemId: json['item_id'] as String,
    code: json['code'] as String,
    description: json['description'] as String,
    unit: json['unit'] as String,
    plannedQuantity: (json['planned_quantity'] as num).toDouble(),
    validatedQuantity: (json['executed_quantity'] as num).toDouble(),
  );

  final String itemId;
  final String code;
  final String description;
  final String unit;
  final double plannedQuantity;

  /// `executed_quantity`: lo de los registros validados.
  final double validatedQuantity;
}

/// Contrato `FieldLocation`.
@immutable
class LocationInfo {
  const LocationInfo({required this.id, required this.label});

  factory LocationInfo.fromJson(Map<String, dynamic> json) =>
      LocationInfo(id: json['id'] as String, label: json['label'] as String);

  final String id;
  final String label;
}

/// Contrato `FieldProjectItem` (costos, pieza 4b).
@immutable
class CatalogItemInfo {
  const CatalogItemInfo({required this.itemId, required this.code, required this.description, required this.unit});

  factory CatalogItemInfo.fromJson(Map<String, dynamic> json) => CatalogItemInfo(
    itemId: json['item_id'] as String,
    code: json['code'] as String,
    description: json['description'] as String,
    unit: json['unit'] as String,
  );

  final String itemId;
  final String code;
  final String description;
  final String unit;
}

/// Contrato `FieldProjectItems` (costos, pieza 4b).
@immutable
class ProjectCatalog {
  const ProjectCatalog({required this.projectId, required this.items});

  factory ProjectCatalog.fromJson(Map<String, dynamic> json) => ProjectCatalog(
    projectId: json['project_id'] as String,
    items: [for (final item in json['items'] as List<dynamic>) CatalogItemInfo.fromJson(item as Map<String, dynamic>)],
  );

  final String projectId;
  final List<CatalogItemInfo> items;
}
```

`ScheduleTaskInfo` pasa a:

```dart
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
    this.measuredBy = 'pct',
    this.location,
    this.locationOptions = const [],
    this.plannedItems = const [],
  });

  factory ScheduleTaskInfo.fromJson(Map<String, dynamic> json) => ScheduleTaskInfo(
    id: json['id'] as String,
    projectId: json['project_id'] as String,
    projectName: json['project_name'] as String,
    wbs: json['wbs'] as String,
    outlineNumber: json['outline_number'] as String,
    name: json['name'] as String,
    lastValidatedPct: (json['last_validated_pct'] as num?)?.toDouble(),
    measuredBy: json['measured_by'] as String? ?? 'pct',
    location: json['location'] == null ? null : LocationInfo.fromJson(json['location'] as Map<String, dynamic>),
    locationOptions: [
      for (final option in json['location_options'] as List<dynamic>? ?? const [])
        LocationInfo.fromJson(option as Map<String, dynamic>),
    ],
    plannedItems: [
      for (final item in json['planned_items'] as List<dynamic>? ?? const [])
        PlannedItemInfo.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String id;
  final String projectId;
  final String projectName;
  final String wbs;
  final String outlineNumber;
  final String name;
  final double? lastValidatedPct;

  /// Costos, pieza 4: `quantities` o `pct`.
  final String measuredBy;
  final LocationInfo? location;
  final List<LocationInfo> locationOptions;
  final List<PlannedItemInfo> plannedItems;
}
```

`ScheduleTaskPage` pasa a:

```dart
/// Contrato `FieldTaskList`.
@immutable
class ScheduleTaskPage {
  const ScheduleTaskPage({
    required this.version,
    required this.changed,
    required this.items,
    this.projectItems = const [],
  });

  factory ScheduleTaskPage.fromJson(Map<String, dynamic> json) => ScheduleTaskPage(
    version: json['version'] as String,
    changed: json['changed'] as bool,
    items: [
      for (final item in json['items'] as List<dynamic>) ScheduleTaskInfo.fromJson(item as Map<String, dynamic>),
    ],
    projectItems: [
      for (final project in json['project_items'] as List<dynamic>? ?? const [])
        ProjectCatalog.fromJson(project as Map<String, dynamic>),
    ],
  );

  final String version;
  final bool changed;
  final List<ScheduleTaskInfo> items;

  /// Costos, pieza 4b: el catálogo de ítems de cada proyecto de la lista.
  final List<ProjectCatalog> projectItems;
}
```

En `_refresh`, el `await db.replaceScheduleTasks(...)` pasa a:

```dart
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
            measuredBy: Value(task.measuredBy),
          ),
      ],
      planned: [
        for (final task in page.items)
          for (final (position, item) in task.plannedItems.indexed)
            TaskPlannedItemsCompanion.insert(
              taskId: task.id,
              itemId: item.itemId,
              code: item.code,
              description: item.description,
              unit: item.unit,
              plannedQuantity: item.plannedQuantity,
              validatedQuantity: item.validatedQuantity,
              position: position,
            ),
      ],
      locations: [
        for (final task in page.items)
          for (final (position, option) in task.locationOptions.indexed)
            TaskLocationOptionsCompanion.insert(
              taskId: task.id,
              locationId: option.id,
              label: option.label,
              isTaskLocation: option.id == task.location?.id,
              position: position,
            ),
      ],
      catalog: [
        for (final project in page.projectItems)
          for (final (position, item) in project.items.indexed)
            ProjectItemsCompanion.insert(
              projectId: project.projectId,
              itemId: item.itemId,
              code: item.code,
              description: item.description,
              unit: item.unit,
              position: position,
            ),
      ],
    );
```

Actualizar el comentario de cabecera del archivo: "…con su último % validado y, desde la pieza 4b de costos, cómo se mide, sus ítems planeados, sus ubicaciones y el catálogo de ítems de sus proyectos."

- [ ] **Step 4: `schedule_task_repository.dart`**

Import nuevo:

```dart
import '../../core/schedule/quantity_rows.dart';
```

`ScheduleTaskItem` pasa a:

```dart
@immutable
class ScheduleTaskItem {
  const ScheduleTaskItem({
    required this.id,
    required this.projectName,
    required this.code,
    required this.name,
    required this.lastValidatedPct,
    this.projectId = '',
    this.measuredBy = 'pct',
    this.searchKey = '',
  });

  factory ScheduleTaskItem.fromRow(ScheduleTaskRow row) => ScheduleTaskItem(
    id: row.id,
    projectId: row.projectId,
    projectName: row.projectName,
    // La EDT del Project; si viene vacía, el número de esquema.
    code: row.wbs.isEmpty ? row.outlineNumber : row.wbs,
    name: row.name,
    lastValidatedPct: row.lastValidatedPct,
    measuredBy: row.measuredBy,
    searchKey: _keyOf(row.wbs.isEmpty ? row.outlineNumber : row.wbs, row.name, row.projectName),
  );

  final String id;
  final String projectId;
  final String projectName;
  final String code;
  final String name;
  final double? lastValidatedPct;

  /// Costos, pieza 4b: `quantities` o `pct`.
  final String measuredBy;

  /// EDT, nombre y proyecto sin mayúsculas ni tildes, calculado una vez al leer
  /// la fila; vacío si el ítem se armó a mano (la búsqueda lo calcula entonces).
  final String searchKey;

  String get label => '$code $name';

  /// La captura pide cantidades por ítem en vez del %.
  bool get measuredByQuantities => measuredBy == 'quantities';
}
```

En `ScheduleTaskRepository`, después de `forUser`:

```dart
  /// La tarea [id] de la lista guardada, o `null` si ya no está.
  Future<ScheduleTaskItem?> byId(String id) async {
    final row = await (db.select(db.scheduleTasks)..where((t) => t.id.equals(id))).getSingleOrNull();
    return row == null ? null : ScheduleTaskItem.fromRow(row);
  }

  /// Costos, pieza 4b: lo que la captura de cantidades necesita de la tarea [taskId]:
  /// sus ítems planeados, sus ubicaciones y el catálogo del proyecto [projectId]
  /// (vacío si no se conoce el proyecto). Tres consultas.
  Future<QuantityTaskContext> quantityContext({required String taskId, String? projectId}) async {
    final planned =
        await (db.select(db.taskPlannedItems)
              ..where((t) => t.taskId.equals(taskId))
              ..orderBy([(t) => OrderingTerm.asc(t.position)]))
            .get();
    final locations =
        await (db.select(db.taskLocationOptions)
              ..where((t) => t.taskId.equals(taskId))
              ..orderBy([(t) => OrderingTerm.asc(t.position)]))
            .get();
    final catalog = projectId == null
        ? const <ProjectItemRow>[]
        : await (db.select(db.projectItems)
                ..where((t) => t.projectId.equals(projectId))
                ..orderBy([(t) => OrderingTerm.asc(t.position)]))
              .get();
    return QuantityTaskContext(
      taskId: taskId,
      planned: [
        for (final p in planned)
          PlannedQuantity(
            item: QuantityItem(itemId: p.itemId, code: p.code, description: p.description, unit: p.unit),
            planned: p.plannedQuantity,
            validated: p.validatedQuantity,
          ),
      ],
      locations: [
        for (final l in locations) LocationChoice(id: l.locationId, label: l.label, isTaskLocation: l.isTaskLocation),
      ],
      catalog: [
        for (final c in catalog)
          QuantityItem(itemId: c.itemId, code: c.code, description: c.description, unit: c.unit),
      ],
    );
  }

  /// Código, descripción y unidad de los ítems [itemIds] que el teléfono vio alguna vez.
  Future<Map<String, QuantityItem>> knownItems(Iterable<String> itemIds) async {
    final ids = itemIds.toSet();
    if (ids.isEmpty) return const {};
    final rows = await (db.select(db.knownItems)..where((t) => t.itemId.isIn(ids))).get();
    return {
      for (final r in rows)
        r.itemId: QuantityItem(itemId: r.itemId, code: r.code, description: r.description, unit: r.unit),
    };
  }

  /// La etiqueta ("Tramo 2 / PR 1") de las ubicaciones [locationIds] que están en la
  /// lista guardada.
  Future<Map<String, String>> locationLabels(Iterable<String> locationIds) async {
    final ids = locationIds.toSet();
    if (ids.isEmpty) return const {};
    final rows = await (db.select(db.taskLocationOptions)..where((t) => t.locationId.isIn(ids))).get();
    return {for (final r in rows) r.locationId: r.label};
  }
```

- [ ] **Step 5: Correr las pruebas**

```bash
dart run build_runner build --delete-conflicting-outputs
flutter test test/core/schedule test/data/local test/features/capture/task_progress_section_test.dart
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lib/core/schedule lib/data/local/schedule_task_repository.dart test/core/schedule
git commit -m "feat(schedule): store how tasks are measured, planned items, locations and catalog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B5: Lectura GPS puntual

**Files:**
- Create: `lib/core/location/spot_location.dart`
- Create: `lib/data/location/geolocator_spot_location.dart`
- Modify: `lib/core/app_dependencies.dart`
- Test: `test/core/location/spot_location_test.dart`

- [ ] **Step 1: Escribir la prueba que falla**

`test/core/location/spot_location_test.dart`:

```dart
// Lectura GPS puntual de las filas de cantidad (costos, pieza 4b): se reutiliza 30 s
// para no encender el GPS en cada fila.
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/location/location_source.dart';
import 'package:suno/core/location/spot_location.dart';

class _CountingSpot implements SpotLocation {
  _CountingSpot(this.fix);
  final LocationFix? fix;
  var reads = 0;

  @override
  Future<LocationFix?> read() async {
    reads++;
    return fix;
  }
}

void main() {
  test('reutiliza una lectura reciente y vuelve a leer al caducar', () async {
    var now = DateTime.utc(2026, 11, 9, 10);
    final source = _CountingSpot(const LocationFix(lat: 4.6, lon: -74.08, accuracyM: 7));
    final spot = CachedSpotLocation(source, clock: () => now);
    expect((await spot.read())?.accuracyM, 7);
    now = now.add(const Duration(seconds: 29));
    await spot.read();
    expect(source.reads, 1);
    now = now.add(const Duration(seconds: 2));
    await spot.read();
    expect(source.reads, 2);
  });

  test('sin lectura no guarda nada: la siguiente fila vuelve a intentar', () async {
    final source = _CountingSpot(null);
    final spot = CachedSpotLocation(source);
    expect(await spot.read(), isNull);
    expect(await spot.read(), isNull);
    expect(source.reads, 2);
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/core/location/spot_location_test.dart`
Expected: FAIL de compilación (`spot_location.dart` no existe).

- [ ] **Step 3: Implementar**

`lib/core/location/spot_location.dart`:

```dart
// Lectura GPS puntual (costos, pieza 4b): cada fila de cantidad guarda dónde estaba el
// agente al digitarla, si hay permiso y señal. A diferencia de `LocationSource`, no pide
// permiso ni mantiene el GPS encendido: es un dato de contexto para la revisión, no la
// ubicación del registro.
import 'location_source.dart';

abstract class SpotLocation {
  /// Una lectura, o `null` sin permiso, con el GPS apagado o sin señal a tiempo.
  Future<LocationFix?> read();
}

/// Reutiliza una lectura de hace menos de [maxAge]: varias filas digitadas seguidas no
/// encienden el GPS cada vez.
class CachedSpotLocation implements SpotLocation {
  CachedSpotLocation(this._source, {this.maxAge = const Duration(seconds: 30), DateTime Function()? clock})
    : _now = clock ?? DateTime.now;

  final SpotLocation _source;
  final Duration maxAge;
  final DateTime Function() _now;
  LocationFix? _last;
  DateTime? _at;

  @override
  Future<LocationFix?> read() async {
    final at = _at;
    if (_last != null && at != null && _now().difference(at) < maxAge) return _last;
    final fix = await _source.read();
    if (fix != null) {
      _last = fix;
      _at = _now();
    }
    return fix;
  }
}
```

`lib/data/location/geolocator_spot_location.dart`:

```dart
// Implementación real de `SpotLocation` sobre `geolocator` (costos, pieza 4b). Sin test
// propio por la misma razón que `GeolocatorLocationSource`: es un envoltorio delgado de
// un plugin de plataforma; se prueba con dobles a través de quien lo usa.
import 'package:geolocator/geolocator.dart';

import '../../core/location/location_source.dart';
import '../../core/location/spot_location.dart';

class GeolocatorSpotLocation implements SpotLocation {
  const GeolocatorSpotLocation({this.timeLimit = const Duration(seconds: 8)});

  /// Cuánto se espera una posición antes de seguir sin ella.
  final Duration timeLimit;

  @override
  Future<LocationFix?> read() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return null;
      // Sin pedir permiso: si no lo dio en la captura del registro, la fila va sin GPS.
      final permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) return null;
      final position = await Geolocator.getCurrentPosition(
        locationSettings: LocationSettings(accuracy: LocationAccuracy.high, timeLimit: timeLimit),
      );
      return LocationFix(
        lat: position.latitude,
        lon: position.longitude,
        accuracyM: position.accuracy,
        altitudeM: position.altitude,
        isMocked: position.isMocked,
      );
    } on Exception {
      // Tiempo agotado, servicio apagado a mitad, permiso revocado: la fila va sin GPS.
      return null;
    }
  }
}
```

En `lib/core/app_dependencies.dart`:
- imports: `import 'location/spot_location.dart';` y `import '../data/location/geolocator_spot_location.dart';`
- parámetro del constructor, después de `LocationSource? locationSource,`: `SpotLocation? spotLocation,`
- inicializador, después de `locationSource = locationSource ?? const GeolocatorLocationSource(),`:

```dart
       spotLocation = spotLocation ?? CachedSpotLocation(const GeolocatorSpotLocation()),
```

- campo, después de `final LocationSource locationSource;`:

```dart
  /// Costos, pieza 4b: lectura GPS puntual de cada fila de cantidad.
  final SpotLocation spotLocation;
```

- [ ] **Step 4: Correr las pruebas**

```bash
flutter test test/core/location
dart analyze lib/core lib/data
```

Expected: PASS y sin avisos.

- [ ] **Step 5: Commit**

```bash
git add lib/core/location/spot_location.dart lib/data/location/geolocator_spot_location.dart lib/core/app_dependencies.dart test/core/location/spot_location_test.dart
git commit -m "feat(location): one-shot GPS reading without prompting, reused for 30 s

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B6: Guardar y editar cantidades en el registro

**Files:**
- Modify: `lib/core/capture/record_capture.dart` (`TaskProgress`, `save`, `update`)
- Test: `test/core/capture/record_quantities_test.dart`

- [ ] **Step 1: Escribir la prueba que falla**

`test/core/capture/record_quantities_test.dart`:

```dart
// Costos, pieza 4b: un registro de una tarea medida por cantidades guarda sus filas sin
// red y las envía con `progress_pct: null`; una tarea por % sigue sin `quantities`.
import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/capture/record_capture.dart';
import 'package:suno/core/forms/form_definition.dart';
import 'package:suno/data/local/database.dart';

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

Map<String, Object?> _row(double quantity, {String clientId = 'c-1'}) => {
  'item_id': 'item-1',
  'location_id': null,
  'quantity': quantity,
  'note': '',
  'client_id': clientId,
  'captured_at': '2026-11-09T14:00:00.000Z',
  'gps': null,
  'seen': {'planned': 10.0, 'validated': 4.0, 'location_id': null},
  'location_mode': 'task',
};

void main() {
  late AppDatabase db;
  late RecordCapture capture;
  var ids = 0;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    ids = 0;
    capture = RecordCapture(db: db, idGenerator: () => 'id-${ids++}', clock: () => DateTime.utc(2026, 11, 9, 10));
  });
  tearDown(() => db.close());

  Future<Map<String, dynamic>> lastPayload() async {
    final ops = await db.select(db.outboxOps).get();
    return jsonDecode(ops.last.payloadJson) as Map<String, dynamic>;
  }

  Future<Record> recordOf(String id) => (db.select(db.records)..where((t) => t.id.equals(id))).getSingle();

  Future<String> saveWith(TaskProgress progress) => capture.save(
    identity: _me,
    templateVersionId: 'tv-1',
    definition: _definition,
    answers: {'notas': 'x'},
    captured: const {},
    progress: progress,
  );

  Future<RecordChange> edit(String recordId, ({TaskProgress? value}) progress) => capture.update(
    identity: _me,
    recordId: recordId,
    definition: _definition,
    answers: {'notas': 'x'},
    captured: const {},
    progress: progress,
  );

  test('alta por cantidades: viajan con progress_pct null y quedan en el registro', () async {
    final recordId = await saveWith(TaskProgress(taskId: 'task-1', quantities: [_row(3)]));
    final payload = await lastPayload();
    expect((payload['task_id'], payload['progress_pct']), ('task-1', null));
    expect(payload['quantities'], [_row(3)]);
    final record = await recordOf(recordId);
    expect(record.progressPct, isNull);
    expect(jsonDecode(record.quantitiesJson), [_row(3)]);
  });

  test('alta por %: sin la clave quantities y el registro con []', () async {
    final recordId = await saveWith(const TaskProgress(taskId: 'task-1', pct: 35));
    expect((await lastPayload()).containsKey('quantities'), isFalse);
    expect((await recordOf(recordId)).quantitiesJson, '[]');
  });

  test('editar sin cambiar las cantidades no envía nada', () async {
    final recordId = await saveWith(TaskProgress(taskId: 'task-1', quantities: [_row(3)]));
    final change = await edit(recordId, (value: TaskProgress(taskId: 'task-1', quantities: [_row(3, clientId: 'otro')])));
    expect(change, RecordChange.unchanged);
  });

  test('cambiar una cantidad envía la tarea, el % y las cantidades', () async {
    final recordId = await saveWith(TaskProgress(taskId: 'task-1', quantities: [_row(3)]));
    expect(await edit(recordId, (value: TaskProgress(taskId: 'task-1', quantities: [_row(4)]))), RecordChange.saved);
    expect(await lastPayload(), {
      'id': recordId,
      'task_id': 'task-1',
      'progress_pct': null,
      'quantities': [_row(4)],
    });
    expect(jsonDecode((await recordOf(recordId)).quantitiesJson), [_row(4)]);
  });

  test('pasar a una tarea por % vacía las cantidades', () async {
    final recordId = await saveWith(TaskProgress(taskId: 'task-1', quantities: [_row(3)]));
    await edit(recordId, (value: const TaskProgress(taskId: 'task-2', pct: 20)));
    expect(await lastPayload(), {'id': recordId, 'task_id': 'task-2', 'progress_pct': 20.0});
    expect((await recordOf(recordId)).quantitiesJson, '[]');
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/core/capture/record_quantities_test.dart`
Expected: FAIL de compilación (`TaskProgress` no tiene `quantities`).

- [ ] **Step 3: Implementar**

En `lib/core/capture/record_capture.dart`, import nuevo:

```dart
import '../schedule/quantity_rows.dart';
```

`TaskProgress` pasa a:

```dart
/// Costos, etapa 1 y pieza 4b: la tarea del cronograma y su avance. Una tarea por %
/// lleva [pct]; una por cantidades, [quantities] (y `pct` nulo). Viajan siempre juntos
/// (`task_id` + `progress_pct`, más `quantities` si las hay).
@immutable
class TaskProgress {
  const TaskProgress({required this.taskId, this.pct, this.quantities});
  final String taskId;

  /// % acumulado de una tarea medida por %; `null` en una medida por cantidades.
  final double? pct;

  /// Filas `RecordQuantityPayload` de una tarea medida por cantidades; `null` en una por %.
  final List<Map<String, Object?>>? quantities;
}
```

En `save`, el payload:

```dart
      // Costos, etapa 1. Sin tarea no se envían: un servidor anterior los rechazaría.
      if (progress != null) ...{
        'task_id': progress.taskId,
        'progress_pct': progress.pct,
        // Pieza 4b: solo las tareas medidas por cantidades.
        if (progress.quantities != null) 'quantities': progress.quantities,
      },
```

y en `RecordsCompanion.insert(...)`, después de `progressPct: Value(progress?.pct),`:

```dart
        quantitiesJson: Value(jsonEncode(progress?.quantities ?? const <Object?>[])),
```

En `update`, reemplazar las dos líneas de `link`/`progressChanged` por:

```dart
    final link = progress?.value;
    // Pieza 4b: cambian las cantidades si cambia un ítem, una ubicación, una cantidad o
    // una nota (los datos automáticos no cuentan).
    final quantitiesChanged = !sameQuantities(link?.quantities ?? const [], decodeQuantities(record.quantitiesJson));
    final progressChanged =
        progress != null && (link?.taskId != record.taskId || link?.pct != record.progressPct || quantitiesChanged);
```

la entrada del par en `changed`:

```dart
      // El servidor exige el par completo, también en `update`; las cantidades viajan con él.
      if (progressChanged) ...{
        'task_id': link?.taskId,
        'progress_pct': link?.pct,
        if (link?.quantities != null) 'quantities': link!.quantities,
      },
```

y en el `RecordsCompanion(...)` del `write`, después de `progressPct: ...`:

```dart
          quantitiesJson: progressChanged
              ? Value(jsonEncode(link?.quantities ?? const <Object?>[]))
              : const Value.absent(),
```

Actualizar el comentario del parámetro `progress` de `update`: "`(value: TaskProgress(...))`: poner o cambiar la tarea y su % o sus cantidades."

- [ ] **Step 4: Correr las pruebas**

```bash
flutter test test/core/capture
```

Expected: PASS (incluidas las de `record_task_progress_test.dart`).

- [ ] **Step 5: Commit**

```bash
git add lib/core/capture/record_capture.dart test/core/capture/record_quantities_test.dart
git commit -m "feat(capture): save and edit field quantities with the record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B7: Pull de las cantidades y "registro validado"

**Files:**
- Modify: `lib/core/sync/sync_engine.dart` (`_upsertRecordFromServerRow`, rama `rejected` de `_applyPushResponse`)
- Test: `test/core/sync/pull_quantities_test.dart`

- [ ] **Step 1: Escribir la prueba que falla**

`test/core/sync/pull_quantities_test.dart`:

```dart
// Costos, pieza 4b: el pull guarda las cantidades del registro; un rechazo
// `record_validated` deja el registro marcado como validado (cantidades de solo lectura).
import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/sync/result.dart';
import 'package:suno/core/sync/sync_engine.dart';
import 'package:suno/core/sync/sync_transport.dart';
import 'package:suno/data/local/database.dart';

import 'fake_sync_transport.dart';

Map<String, dynamic> _row() => {
  'id': 'record-1',
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
  'task_id': 'task-1',
  'progress_pct': null,
};

const _quantity = {
  'item_id': 'item-1',
  'location_id': null,
  'quantity': 4.5,
  'note': '',
  'client_id': 'c-1',
  'captured_at': '2026-11-01T09:58:00.000Z',
  'gps': {'lat': 4.6, 'lon': -74.08, 'accuracy_m': 7.0},
  'seen': {'planned': 10.0, 'validated': 0.0, 'location_id': null},
  'location_mode': 'task',
};

void main() {
  late AppDatabase db;
  late FakeSyncTransport transport;
  late SyncEngine engine;

  setUp(() {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeSyncTransport();
    var ids = 0;
    engine = SyncEngine(db: db, transport: transport, deviceId: 'device-1', idGenerator: () => 'op-${ids++}');
  });
  tearDown(() => db.close());

  Future<Record> pullRow(Map<String, dynamic> row) async {
    transport.pullResponses.add(
      Result.ok(
        PullResponse(
          rows: [PullRow(entity: 'record', data: row)],
          tombstones: const [],
          nextCursor: 'c1:00000000000000000042',
          hasMore: false,
        ),
      ),
    );
    await engine.pull();
    return db.select(db.records).getSingle();
  }

  test('guarda las cantidades del registro tal como llegan', () async {
    final record = await pullRow({
      ..._row(),
      'quantities': [_quantity],
    });
    expect(jsonDecode(record.quantitiesJson), [_quantity]);
  });

  test('una fila sin la clave (servidor anterior) deja []', () async {
    expect((await pullRow(_row())).quantitiesJson, '[]');
  });

  test('un rechazo record_validated marca el registro como validado', () async {
    await pullRow({
      ..._row(),
      'quantities': [_quantity],
    });
    await engine.enqueueRecordOp(
      entityId: 'record-1',
      opType: 'update',
      baseVersion: 1,
      payload: {
        'id': 'record-1',
        'task_id': 'task-1',
        'progress_pct': null,
        'quantities': [
          {..._quantity, 'quantity': 5},
        ],
      },
    );
    final op = await db.select(db.outboxOps).getSingle();
    transport.pushResponses.add(
      Result.ok(
        PushResponse(
          schemaVersion: 2,
          results: [
            PushOpOutcome(
              opUuid: op.opUuid,
              status: 'rejected',
              error: const PushOpError(
                code: 'validation_error',
                message: 'The record was validated: its task, progress and quantities cannot change.',
                details: {'field': 'quantities', 'reason': 'record_validated'},
              ),
            ),
          ],
        ),
      ),
    );
    await engine.drainOutbox();
    expect((await db.select(db.records).getSingle()).knownValidated, isTrue);

    // Un pull posterior no borra la marca.
    expect((await pullRow({..._row(), 'version': 2})).knownValidated, isTrue);
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/core/sync/pull_quantities_test.dart`
Expected: FAIL (`quantitiesJson` es `[]` y `knownValidated` es `false`).

- [ ] **Step 3: Implementar**

En `lib/core/sync/sync_engine.dart`, en `_upsertRecordFromServerRow`, después de `progressPct: Value(...)`:

```dart
      // Costos, pieza 4b: tal como llegan (forma `RecordQuantity`). Un servidor anterior
      // no envía la clave: `[]`. `knownValidated` no viaja: un pull no la toca.
      quantitiesJson: Value(jsonEncode(serverRow['quantities'] ?? const <Object?>[])),
```

En `_applyPushResponse`, rama `else if (outcome.status == 'rejected')`, después del `write(...)` que pasa la op a cuarentena y antes de `quarantined++;`:

```dart
          // Costos, pieza 4b: el servidor dice que el registro ya está validado; desde
          // ahora la captura muestra sus cantidades de solo lectura.
          if (row.entity == 'record' && fields.any((f) => f.reason == 'record_validated')) {
            await (db.update(db.records)..where((t) => t.id.equals(row.entityId))).write(
              const RecordsCompanion(knownValidated: Value(true)),
            );
          }
```

- [ ] **Step 4: Correr las pruebas**

```bash
flutter test test/core/sync
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/core/sync/sync_engine.dart test/core/sync/pull_quantities_test.dart
git commit -m "feat(sync): pull record quantities and remember validated records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task B8: Sección de captura de cantidades y textos

**Files:**
- Create: `lib/features/capture/quantity_capture_section.dart`
- Modify: `lib/l10n/app_es.arb`
- Test: `test/features/capture/quantity_capture_section_test.dart`

- [ ] **Step 1: Textos**

En `lib/l10n/app_es.arb`, reemplazar el valor de `formErrorRecordValidated` por:

```json
  "formErrorRecordValidated": "El registro ya fue validado: su tarea, su % y sus cantidades no se pueden cambiar desde el teléfono.",
```

e insertar, inmediatamente después de la línea `"reviewUnlinkTask": "Quitar la tarea y reenviar",`, todos los textos de la pieza 4b (las tareas B9–B12 los usan):

```json
  "quantitySectionTitle": "Cantidades de hoy",
  "quantityToday": "Cantidad hoy",
  "quantityPlannedValidated": "Planeado {planned} {unit} · validado {validated} {unit}",
  "@quantityPlannedValidated": {
    "placeholders": { "planned": { "type": "String" }, "validated": { "type": "String" }, "unit": { "type": "String" } }
  },
  "quantityUnplanned": "No planeado en esta tarea",
  "quantityInvalid": "Escribe un número con hasta 4 decimales.",
  "quantityOverrun": "Con lo validado llega a {total} {unit} y supera lo planeado ({planned} {unit}). Se guardará y la supervisión lo verá marcado.",
  "@quantityOverrun": {
    "placeholders": { "total": { "type": "String" }, "planned": { "type": "String" }, "unit": { "type": "String" } }
  },
  "quantityAdjustment": "Cantidad negativa: se registra como ajuste y necesita nota.",
  "quantityLocation": "Ubicación",
  "quantityLocationTask": "La de la tarea",
  "quantityLocationTaskNamed": "La de la tarea ({label})",
  "@quantityLocationTaskNamed": {
    "placeholders": { "label": { "type": "String" } }
  },
  "quantityLocationRefined": "Ubicación precisada",
  "quantityNote": "Nota (opcional)",
  "quantityNoteRequiredLabel": "Nota (obligatoria)",
  "quantityNoteRequired": "Escribe una nota: el ítem no está planeado o la cantidad es negativa.",
  "quantityNoteTooLong": "La nota admite hasta 500 caracteres.",
  "quantityAddRow": "Otra fila de este ítem",
  "quantityRemoveRow": "Quitar fila",
  "quantityReassign": "Cambiar ítem",
  "quantityAddUnplanned": "Agregar ítem no planeado",
  "quantityNoneEntered": "Escribe al menos una cantidad distinta de 0.",
  "quantityTooManyRows": "Un registro admite hasta {max} filas de cantidad.",
  "@quantityTooManyRows": {
    "placeholders": { "max": { "type": "int" } }
  },
  "quantityReadOnly": "El registro ya fue validado: sus cantidades no se pueden cambiar.",
  "quantityRowFlagged": "El servidor rechazó esta fila: cambia el ítem o la ubicación, o quítala.",
  "quantityItemSearch": "Buscar por código o descripción",
  "quantityItemEmpty": "No hay ítems que coincidan.",
  "quantityDiscardTitle": "¿Cambiar de tarea?",
  "quantityDiscardBody": "Se descartarán las cantidades que escribiste para esta tarea.",
  "quantityDiscardConfirm": "Sí, cambiar",
  "quantityCorrectionTitle": "Corregir cantidades",
  "quantityCorrectionExplain": "El servidor rechazó las filas señaladas. Cambia su ítem o su ubicación, o quítalas; al guardar, el registro se reenvía.",
  "quantityLineValue": "{quantity} {unit}",
  "@quantityLineValue": {
    "placeholders": { "quantity": { "type": "String" }, "unit": { "type": "String" } }
  },
  "quantityLineLocation": "Ubicación: {location}",
  "@quantityLineLocation": {
    "placeholders": { "location": { "type": "String" } }
  },
  "quantityLineNote": "Nota: {note}",
  "@quantityLineNote": {
    "placeholders": { "note": { "type": "String" } }
  },
  "formErrorItemNotInProject": "El ítem no es del presupuesto del proyecto de la tarea.",
  "formErrorLocationOutsideTask": "La ubicación no es del proyecto de la tarea.",
  "formErrorDuplicateClientId": "Dos filas de cantidad tienen el mismo identificador.",
  "reviewCorrectQuantities": "Corregir cantidades",
  "reviewQuantityItemRow": "Fila del ítem {code}: el ítem no es del presupuesto del proyecto.",
  "@reviewQuantityItemRow": {
    "placeholders": { "code": { "type": "String" } }
  },
  "reviewQuantityLocationRow": "Fila del ítem {code}: la ubicación no es del proyecto de la tarea.",
  "@reviewQuantityLocationRow": {
    "placeholders": { "code": { "type": "String" } }
  },
  "reviewQuantityUnknownItem": "sin código",
  "reviewQuantityRows": "{count, plural, =1{1 fila} other{{count} filas}}",
  "@reviewQuantityRows": {
    "placeholders": { "count": { "type": "int" } }
  },
  "recordExportQuantities": "Cantidades",
  "recordExportItem": "Ítem",
  "recordExportQuantity": "Cantidad",
  "recordExportNote": "Nota",
```

```bash
flutter gen-l10n
```

Expected: sin errores.

- [ ] **Step 2: Escribir las pruebas que fallan**

`test/features/capture/quantity_capture_section_test.dart`:

```dart
// Captura de cantidades (costos, pieza 4b): el controlador lleva las filas y sus datos
// automáticos; la sección las dibuja con sus avisos.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/schedule/quantity_rows.dart';
import 'package:suno/features/capture/quantity_capture_section.dart';

import '../../support/test_app.dart';

const _concrete = QuantityItem(itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3');
const _steel = QuantityItem(itemId: 'item-2', code: '2.2', description: 'Acero de refuerzo', unit: 'kg');

const _task = QuantityTaskContext(
  taskId: 'task-1',
  planned: [PlannedQuantity(item: _concrete, planned: 10, validated: 8)],
  locations: [
    LocationChoice(id: 'loc-1', label: 'Tramo 2', isTaskLocation: true),
    LocationChoice(id: 'loc-2', label: 'Tramo 2 / PR 1', isTaskLocation: false),
  ],
  catalog: [_concrete, _steel],
);

void main() {
  late QuantityCaptureController controller;
  late List<GpsReading?> gps;
  var ids = 0;

  setUp(() {
    ids = 0;
    gps = [const GpsReading(lat: 4.6, lon: -74.08, accuracyM: 7)];
    controller = QuantityCaptureController(
      newId: () => 'c-${ids++}',
      readGps: () async => gps.isEmpty ? null : gps.removeAt(0),
      clock: () => DateTime.utc(2026, 11, 9, 14),
    );
  });
  tearDown(() => controller.dispose());

  group('controlador', () {
    test('una fila vacía por ítem planeado', () {
      controller.load(_task);
      expect(
        [for (final r in controller.rows) (r.clientId, r.item.code, r.isPlanned, r.text)],
        [('c-0', '2.1', true, '')],
      );
      expect(controller.hasCaptured, isFalse);
    });

    test('al digitar por primera vez toma la hora, lo que se veía y una lectura GPS', () async {
      controller.load(_task);
      controller.setQuantity('c-0', '1');
      await Future<void>.delayed(Duration.zero);
      controller.setQuantity('c-0', '1,5');
      final row = controller.rows.single;
      expect(row.capturedAt, DateTime.utc(2026, 11, 9, 14));
      expect(row.seen?.toJson(), {'planned': 10.0, 'validated': 8.0, 'location_id': 'loc-1'});
      expect(row.gps?.accuracyM, 7);
      expect(row.text, '1,5');
      expect(gps, isEmpty, reason: 'una sola lectura por fila');
      expect(controller.hasCaptured, isTrue);
    });

    test('precisar la ubicación cambia el modo', () {
      controller.load(_task);
      controller.setLocation('c-0', 'loc-2');
      expect((controller.rows.single.locationId, controller.rows.single.locationMode), ('loc-2', 'refined'));
      controller.setLocation('c-0', null);
      expect(controller.rows.single.locationMode, 'task');
    });

    test('otra fila del mismo ítem y un ítem no planeado; las agregadas se quitan', () {
      controller.load(_task);
      controller.addRowLike('c-0');
      controller.addUnplanned(_steel);
      expect(
        [for (final r in controller.rows) (r.item.code, r.isPlanned, r.added)],
        [('2.1', true, false), ('2.1', true, true), ('2.2', false, true)],
      );
      controller.removeRow('c-1');
      expect(controller.rows.length, 2);
    });

    test('validar: nota obligatoria en el no planeado y al menos una cantidad', () {
      controller.load(_task);
      expect(controller.validate(), isFalse);
      expect(controller.check?.noQuantity, isTrue);
      controller.addUnplanned(_steel);
      controller.setQuantity('c-1', '3');
      expect(controller.validate(), isFalse);
      expect(controller.check?.rows, {'c-1': QuantityRowProblem.noteRequired});
      controller.setNote('c-1', 'sobrante');
      expect(controller.validate(), isTrue);
      expect([for (final r in controller.payload()) r['item_id']], ['item-2']);
    });

    test('carga lo guardado: la primera fila de un ítem planeado ocupa su lugar', () {
      controller.load(
        _task,
        stored: [
          {'item_id': 'item-1', 'location_id': null, 'quantity': 2, 'note': '', 'client_id': 'g-1'},
          {'item_id': 'item-1', 'location_id': 'loc-2', 'quantity': 1, 'note': '', 'client_id': 'g-2'},
          {'item_id': 'item-9', 'location_id': null, 'quantity': 4, 'note': 'x', 'client_id': 'g-3'},
        ],
        known: {'item-9': const QuantityItem(itemId: 'item-9', code: '9.9', description: 'Viejo', unit: 'un')},
        flaggedRows: {2},
      );
      expect(
        [for (final r in controller.rows) (r.clientId, r.item.code, r.text, r.added)],
        [('g-1', '2.1', '2', false), ('g-2', '2.1', '1', true), ('g-3', '9.9', '4', true)],
      );
      expect(controller.isFlagged('g-3'), isTrue);
      controller.reassignItem('g-3', _concrete);
      final last = controller.rows.last;
      expect((last.item.code, last.isPlanned, controller.isFlagged('g-3')), ('2.1', true, false));
    });
  });

  Future<void> pump(WidgetTester tester, {bool correcting = false}) async {
    tester.view.physicalSize = const Size(390, 1800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      testApp(
        Scaffold(
          body: ListView(children: [QuantityCaptureSection(controller: controller, correcting: correcting)]),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('referencia, aviso de sobreejecución y de ajuste', (tester) async {
    controller.load(_task);
    await pump(tester);
    expect(find.text('Planeado 10 m3 · validado 8 m3'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('quantity_field-c-0')), '3');
    await tester.pumpAndSettle();
    expect(
      find.text('Con lo validado llega a 11 m3 y supera lo planeado (10 m3). Se guardará y la supervisión lo verá marcado.'),
      findsOneWidget,
    );
    await tester.enterText(find.byKey(const ValueKey('quantity_field-c-0')), '-1');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('quantity_overrun-c-0')), findsNothing);
    expect(find.byKey(const ValueKey('quantity_adjustment-c-0')), findsOneWidget);
    expect(find.text('Nota (obligatoria)'), findsOneWidget);
  });

  testWidgets('agregar un ítem no planeado desde el buscador', (tester) async {
    controller.load(_task);
    await pump(tester);
    await tester.tap(find.byKey(const Key('quantity_add_unplanned')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('quantity_item-item-1')), findsNothing, reason: 'ya está planeado');
    await tester.enterText(find.byKey(const Key('quantity_item_search')), 'acero');
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('quantity_item-item-2')));
    await tester.pumpAndSettle();
    expect(find.text('2.2 Acero de refuerzo'), findsOneWidget);
    expect(find.text('No planeado en esta tarea'), findsOneWidget);
    expect(find.text('Nota (obligatoria)'), findsOneWidget);
  });

  testWidgets('elegir una ubicación precisada', (tester) async {
    controller.load(_task);
    await pump(tester);
    expect(find.text('La de la tarea (Tramo 2)'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('quantity_location-c-0')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tramo 2 / PR 1').last);
    await tester.pumpAndSettle();
    expect(controller.rows.single.locationId, 'loc-2');
  });

  testWidgets('solo lectura: sin campos editables ni botones', (tester) async {
    controller.load(
      _task,
      stored: [
        {'item_id': 'item-1', 'location_id': null, 'quantity': 2, 'note': '', 'client_id': 'g-1'},
      ],
      readOnly: true,
    );
    await pump(tester);
    expect(find.byKey(const Key('quantity_read_only')), findsOneWidget);
    expect(tester.widget<TextFormField>(find.byKey(const ValueKey('quantity_field-g-1'))).enabled, isFalse);
    expect(find.byKey(const Key('quantity_add_unplanned')), findsNothing);
    expect(find.byKey(const ValueKey('quantity_add_row-g-1')), findsNothing);
  });

  testWidgets('a 390 px los textos largos no desbordan', (tester) async {
    const long = QuantityItem(
      itemId: 'item-3',
      code: '12.34.56',
      description: 'Suministro, transporte e instalación de tubería de concreto reforzado de 900 mm clase III',
      unit: 'ml',
    );
    controller.load(
      const QuantityTaskContext(
        taskId: 't',
        planned: [PlannedQuantity(item: long, planned: 12345678.1234, validated: 9876543.5)],
        locations: [
          LocationChoice(id: 'a', label: 'Tramo 2', isTaskLocation: true),
          LocationChoice(
            id: 'b',
            label: 'Tramo 2 / PR 12+300 / Costado izquierdo / Carril de desaceleración',
            isTaskLocation: false,
          ),
        ],
      ),
    );
    await pump(tester);
    expect(tester.takeException(), isNull);
  });
}
```

- [ ] **Step 3: Correr y ver que falla**

Run: `flutter test test/features/capture/quantity_capture_section_test.dart`
Expected: FAIL de compilación (`quantity_capture_section.dart` no existe).

- [ ] **Step 4: Implementar `lib/features/capture/quantity_capture_section.dart`**

```dart
// Captura de cantidades (costos, pieza 4b): en una tarea medida por cantidades, una fila
// por ítem planeado con "Cantidad hoy", su ubicación y su nota; "Agregar ítem no
// planeado" y "Otra fila de este ítem". El controlador guarda el estado y los datos
// automáticos de cada fila (hora, lo que se veía, GPS); la sección solo dibuja. La misma
// sección corrige un rechazo del servidor (`correcting`): cada fila se puede quitar y la
// señalada, cambiar de ítem.
import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/schedule/quantity_rows.dart';
import '../../data/local/schedule_task_repository.dart' show foldForSearch;
import '../../l10n/l10n.dart';
import '../../ui/theme/app_tokens.dart';

class QuantityCaptureController extends ChangeNotifier {
  QuantityCaptureController({
    required String Function() newId,
    required Future<GpsReading?> Function() readGps,
    DateTime Function()? clock,
  }) : _newId = newId,
       _readGps = readGps,
       _now = clock ?? DateTime.now;

  final String Function() _newId;
  final Future<GpsReading?> Function() _readGps;
  final DateTime Function() _now;

  QuantityTaskContext? _task;
  List<QuantityRowDraft> _rows = const [];
  Set<String> _flagged = const {};
  bool _readOnly = false;
  QuantityCheck? _check;
  bool _disposed = false;

  /// La tarea cargada; `null` si la elegida no se mide por cantidades.
  QuantityTaskContext? get task => _task;
  List<QuantityRowDraft> get rows => _rows;
  bool get readOnly => _readOnly;

  /// El resultado de la última validación (se recalcula al editar).
  QuantityCheck? get check => _check;

  /// Hay algo escrito en alguna "Cantidad hoy".
  bool get hasCaptured => _rows.any((r) => r.text.trim().isNotEmpty);
  Set<String> get overruns => overrunItems(_rows);
  bool isFlagged(String clientId) => _flagged.contains(clientId);

  /// Carga la tarea: una fila vacía por ítem planeado y, encima, las filas [stored] de un
  /// registro (la primera de cada ítem planeado ocupa su fila; las demás se agregan).
  /// [known] da código y unidad de los ítems que ya no están en la tarea ni en el
  /// catálogo; [flaggedRows] son las posiciones en [stored] que rechazó el servidor.
  void load(
    QuantityTaskContext task, {
    List<Map<String, Object?>> stored = const [],
    Map<String, QuantityItem> known = const {},
    bool readOnly = false,
    Set<int> flaggedRows = const {},
  }) {
    final rows = <QuantityRowDraft>[
      for (final p in task.planned)
        QuantityRowDraft(
          clientId: _newId(),
          item: p.item,
          planned: p.planned,
          validated: p.validated,
          isPlanned: true,
        ),
    ];
    final filled = <String>{};
    final flagged = <String>{};
    for (final (index, json) in stored.indexed) {
      final itemId = json['item_id']! as String;
      final planned = task.plannedFor(itemId);
      final item = planned?.item ?? known[itemId] ?? task.catalogItem(itemId) ?? QuantityItem.unknown(itemId);
      final slot = planned != null && !filled.contains(itemId)
          ? rows.indexWhere((r) => r.isPlanned && r.item.itemId == itemId)
          : -1;
      final row = QuantityRowDraft.fromStored(json, item: item, planned: planned, fallbackId: _newId(), added: slot < 0);
      if (slot >= 0) {
        rows[slot] = row;
        filled.add(itemId);
      } else {
        rows.add(row);
      }
      if (flaggedRows.contains(index)) flagged.add(row.clientId);
    }
    _task = task;
    _rows = rows;
    _flagged = flagged;
    _readOnly = readOnly;
    _check = null;
    notifyListeners();
  }

  /// La tarea elegida ya no se mide por cantidades (o no hay tarea).
  void clear() {
    _task = null;
    _rows = const [];
    _flagged = const {};
    _readOnly = false;
    _check = null;
    notifyListeners();
  }

  /// Lo escrito en "Cantidad hoy". La primera vez que la fila recibe un número se toman
  /// sus datos automáticos: hora, lo que la app mostraba y una lectura GPS puntual.
  void setQuantity(String clientId, String text) {
    var started = false;
    _replace(clientId, (row) {
      if (row.capturedAt != null || text.trim().isEmpty) return row.copyWith(text: text);
      started = true;
      return row.copyWith(
        text: text,
        capturedAt: _now().toUtc(),
        seen: QuantitySeen(
          planned: row.isPlanned ? row.planned : null,
          validated: row.isPlanned ? row.validated : null,
          locationId: _task?.taskLocation?.id,
        ),
      );
    });
    if (started) unawaited(_attachGps(clientId));
  }

  void setNote(String clientId, String note) => _replace(clientId, (row) => row.copyWith(note: note));

  /// `null`: la ubicación de la tarea.
  void setLocation(String clientId, String? locationId) {
    _flagged = {..._flagged}..remove(clientId);
    _replace(clientId, (row) => row.copyWith(locationId: locationId));
  }

  void addUnplanned(QuantityItem item) {
    _rows = [..._rows, QuantityRowDraft(clientId: _newId(), item: item, added: true)];
    _changed();
  }

  /// "Otra fila de este ítem" (p. ej. otra ubicación), justo debajo de [clientId].
  void addRowLike(String clientId) {
    final index = _rows.indexWhere((r) => r.clientId == clientId);
    if (index < 0) return;
    final source = _rows[index];
    final copy = QuantityRowDraft(
      clientId: _newId(),
      item: source.item,
      planned: source.planned,
      validated: source.validated,
      isPlanned: source.isPlanned,
      added: true,
    );
    _rows = [..._rows.sublist(0, index + 1), copy, ..._rows.sublist(index + 1)];
    _changed();
  }

  void removeRow(String clientId) {
    _rows = [for (final row in _rows) if (row.clientId != clientId) row];
    _flagged = {..._flagged}..remove(clientId);
    _changed();
  }

  /// Corrección de un rechazo: la fila pasa a [item] (planeado o no en la tarea).
  void reassignItem(String clientId, QuantityItem item) {
    final planned = _task?.plannedFor(item.itemId);
    _flagged = {..._flagged}..remove(clientId);
    _replace(
      clientId,
      (row) => row.copyWith(
        item: item,
        planned: planned?.planned,
        validated: planned?.validated,
        isPlanned: planned != null,
      ),
    );
  }

  /// Valida al guardar; las filas con problemas quedan señaladas. Un registro de solo
  /// lectura no tiene nada que validar.
  bool validate() {
    if (_readOnly) return true;
    _check = checkQuantityRows(_rows);
    notifyListeners();
    return _check!.ok;
  }

  /// Las filas que viajan (`RecordQuantityPayload`).
  List<Map<String, Object?>> payload() => quantityPayload(_rows);

  Future<void> _attachGps(String clientId) async {
    final reading = await _readGps();
    if (_disposed || reading == null) return;
    if (!_rows.any((r) => r.clientId == clientId && r.gps == null)) return;
    _replace(clientId, (row) => row.copyWith(gps: reading));
  }

  void _replace(String clientId, QuantityRowDraft Function(QuantityRowDraft row) change) {
    _rows = [for (final row in _rows) row.clientId == clientId ? change(row) : row];
    _changed();
  }

  void _changed() {
    if (_check != null) _check = checkQuantityRows(_rows);
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// Valor del desplegable para "la ubicación de la tarea" (`location_id: null`).
const _taskLocationValue = '';

class QuantityCaptureSection extends StatelessWidget {
  const QuantityCaptureSection({super.key, required this.controller, this.correcting = false});

  final QuantityCaptureController controller;

  /// Corrección de un rechazo del servidor (`QuantityCorrectionScreen`).
  final bool correcting;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: controller,
    builder: (context, _) {
      final task = controller.task;
      if (task == null) return const SizedBox.shrink();
      final l10n = context.l10n;
      final theme = Theme.of(context);
      final check = controller.check;
      final overruns = controller.overruns;
      final error = theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error);
      return Column(
        key: const Key('quantity_section'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l10n.quantitySectionTitle, style: theme.textTheme.titleSmall),
          if (controller.readOnly) Text(l10n.quantityReadOnly, key: const Key('quantity_read_only')),
          for (final row in controller.rows)
            _QuantityRowCard(
              key: ValueKey('quantity_row-${row.clientId}'),
              row: row,
              task: task,
              controller: controller,
              correcting: correcting,
              problem: check?.rows[row.clientId],
              overrun: overruns.contains(row.item.itemId),
            ),
          if (check?.noQuantity ?? false) Text(l10n.quantityNoneEntered, key: const Key('quantity_none'), style: error),
          if (check?.tooManyRows ?? false)
            Text(l10n.quantityTooManyRows(maxQuantityRows), key: const Key('quantity_too_many'), style: error),
          if (!controller.readOnly && !correcting)
            OutlinedButton.icon(
              key: const Key('quantity_add_unplanned'),
              onPressed: task.unplannedCatalog.isEmpty
                  ? null
                  : () async {
                      final item = await showQuantityItemPicker(context, task.unplannedCatalog);
                      if (item != null) controller.addUnplanned(item);
                    },
              icon: const Icon(Icons.add),
              label: Text(l10n.quantityAddUnplanned),
            ),
        ],
      );
    },
  );
}

class _QuantityRowCard extends StatelessWidget {
  const _QuantityRowCard({
    super.key,
    required this.row,
    required this.task,
    required this.controller,
    required this.correcting,
    required this.problem,
    required this.overrun,
  });

  final QuantityRowDraft row;
  final QuantityTaskContext task;
  final QuantityCaptureController controller;
  final bool correcting;
  final QuantityRowProblem? problem;
  final bool overrun;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final readOnly = controller.readOnly;
    final flagged = controller.isFlagged(row.clientId);
    final warning = theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error);
    final id = row.clientId;
    final taskLocation = task.taskLocation;
    final taskLabel = taskLocation == null ? l10n.quantityLocationTask : l10n.quantityLocationTaskNamed(taskLocation.label);
    final unknownLocation = row.locationId != null && !task.refinements.any((o) => o.id == row.locationId);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(row.item.label, style: theme.textTheme.titleSmall),
            Text(
              row.isPlanned
                  ? l10n.quantityPlannedValidated(
                      formatQuantity(row.planned ?? 0),
                      formatQuantity(row.validated ?? 0),
                      row.item.unit,
                    )
                  : l10n.quantityUnplanned,
              key: ValueKey('quantity_reference-$id'),
              style: theme.textTheme.bodySmall,
            ),
            if (flagged) Text(l10n.quantityRowFlagged, key: ValueKey('quantity_flagged-$id'), style: warning),
            TextFormField(
              key: ValueKey('quantity_field-$id'),
              initialValue: row.text,
              enabled: !readOnly,
              keyboardType: const TextInputType.numberWithOptions(decimal: true, signed: true),
              decoration: InputDecoration(
                labelText: l10n.quantityToday,
                suffixText: row.item.unit.isEmpty ? null : row.item.unit,
                errorText: problem == QuantityRowProblem.invalidNumber ? l10n.quantityInvalid : null,
              ),
              onChanged: (value) => controller.setQuantity(id, value),
            ),
            if (overrun)
              Text(
                l10n.quantityOverrun(
                  formatQuantity((row.validated ?? 0) + todayTotal(controller.rows, row.item.itemId)),
                  formatQuantity(row.planned ?? 0),
                  row.item.unit,
                ),
                key: ValueKey('quantity_overrun-$id'),
                style: warning,
              ),
            if (row.isAdjustment)
              Text(l10n.quantityAdjustment, key: ValueKey('quantity_adjustment-$id'), style: theme.textTheme.bodySmall),
            if (task.refinements.isNotEmpty || row.locationId != null)
              DropdownButtonFormField<String>(
                key: ValueKey('quantity_location-$id'),
                initialValue: row.locationId ?? _taskLocationValue,
                isExpanded: true,
                decoration: InputDecoration(labelText: l10n.quantityLocation),
                items: [
                  DropdownMenuItem(
                    value: _taskLocationValue,
                    child: Text(taskLabel, overflow: TextOverflow.ellipsis),
                  ),
                  for (final option in task.refinements)
                    DropdownMenuItem(value: option.id, child: Text(option.label, overflow: TextOverflow.ellipsis)),
                  // Una ubicación guardada que ya no está entre las opciones (rechazada o retirada).
                  if (unknownLocation)
                    DropdownMenuItem(
                      value: row.locationId,
                      child: Text(l10n.quantityLocationRefined, overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: readOnly
                    ? null
                    : (value) => controller.setLocation(id, value == null || value == _taskLocationValue ? null : value),
              )
            else if (taskLocation != null)
              Text(taskLabel, style: theme.textTheme.bodySmall),
            TextFormField(
              key: ValueKey('quantity_note-$id'),
              initialValue: row.note,
              enabled: !readOnly,
              maxLength: maxQuantityNote,
              decoration: InputDecoration(
                labelText: row.needsNote ? l10n.quantityNoteRequiredLabel : l10n.quantityNote,
                errorText: switch (problem) {
                  QuantityRowProblem.noteRequired => l10n.quantityNoteRequired,
                  QuantityRowProblem.noteTooLong => l10n.quantityNoteTooLong,
                  _ => null,
                },
              ),
              onChanged: (value) => controller.setNote(id, value),
            ),
            if (!readOnly)
              Wrap(
                spacing: AppSpacing.xs,
                children: [
                  if (!correcting)
                    TextButton(
                      key: ValueKey('quantity_add_row-$id'),
                      onPressed: () => controller.addRowLike(id),
                      child: Text(l10n.quantityAddRow),
                    ),
                  if (correcting && flagged)
                    TextButton(
                      key: ValueKey('quantity_reassign-$id'),
                      onPressed: () async {
                        final item = await showQuantityItemPicker(context, task.allItems);
                        if (item != null) controller.reassignItem(id, item);
                      },
                      child: Text(l10n.quantityReassign),
                    ),
                  if (row.added || correcting)
                    TextButton(
                      key: ValueKey('quantity_remove-$id'),
                      onPressed: () => controller.removeRow(id),
                      child: Text(l10n.quantityRemoveRow),
                    ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

/// Buscador de ítems por código o descripción. `null` si se cerró sin elegir.
Future<QuantityItem?> showQuantityItemPicker(BuildContext context, List<QuantityItem> items) =>
    showModalBottomSheet<QuantityItem>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _QuantityItemSheet(items: items),
    );

class _QuantityItemSheet extends StatefulWidget {
  const _QuantityItemSheet({required this.items});

  final List<QuantityItem> items;

  @override
  State<_QuantityItemSheet> createState() => _QuantityItemSheetState();
}

class _QuantityItemSheetState extends State<_QuantityItemSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final needle = foldForSearch(_query.trim());
    final visible = [
      for (final item in widget.items)
        if (needle.isEmpty || foldForSearch('${item.code} ${item.description}').contains(needle)) item,
    ];
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
                  key: const Key('quantity_item_search'),
                  autofocus: true,
                  decoration: InputDecoration(labelText: l10n.quantityItemSearch, prefixIcon: const Icon(Icons.search)),
                  onChanged: (value) => setState(() => _query = value),
                ),
              ),
              Expanded(
                child: visible.isEmpty
                    ? Center(child: Text(l10n.quantityItemEmpty))
                    : ListView.builder(
                        itemCount: visible.length,
                        itemBuilder: (context, index) {
                          final item = visible[index];
                          return ListTile(
                            key: ValueKey('quantity_item-${item.itemId}'),
                            title: Text(item.label),
                            subtitle: Text(item.unit),
                            onTap: () => Navigator.of(context).pop(item),
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

- [ ] **Step 5: Correr las pruebas y el lint de tokens**

```bash
flutter test test/features/capture/quantity_capture_section_test.dart
bash tool/lint_ui_tokens.sh
```

Expected: PASS y sin violaciones.

- [ ] **Step 6: Commit**

```bash
git add lib/features/capture/quantity_capture_section.dart lib/l10n/app_es.arb test/features/capture/quantity_capture_section_test.dart
git commit -m "feat(capture): quantity capture section with planned items, notes and locations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B9: Captura por cantidades en la pantalla de captura

**Files:**
- Modify: `lib/features/capture/task_progress_section.dart` (`quantities`, `locked`)
- Modify: `lib/features/capture/capture_screen.dart`
- Test: `test/features/capture/capture_quantities_test.dart`

- [ ] **Step 1: Escribir las pruebas que fallan**

`test/features/capture/capture_quantities_test.dart`:

```dart
// Costos, pieza 4b: en una tarea medida por cantidades la captura pide "Cantidad hoy"
// por ítem en lugar del %, exige nota donde corresponde, confirma antes de descartar lo
// escrito al cambiar de tarea, y deja de solo lectura un registro ya validado.
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/app_dependencies.dart';
import 'package:suno/core/auth/auth_controller.dart';
import 'package:suno/core/auth/auth_models.dart';
import 'package:suno/core/auth/auth_result.dart';
import 'package:suno/core/location/location_source.dart';
import 'package:suno/core/location/spot_location.dart';
import 'package:suno/core/providers.dart';
import 'package:suno/data/local/database.dart';
import 'package:suno/features/capture/capture_screen.dart';

import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';
import '../../support/test_app.dart';

class _FakeSpot implements SpotLocation {
  @override
  Future<LocationFix?> read() async => const LocationFix(lat: 4.6, lon: -74.08, accuracyM: 7);
}

Finder _byKeyPrefix(String prefix) => find.byWidgetPredicate(
  (widget) => widget.key is ValueKey<String> && (widget.key! as ValueKey<String>).value.startsWith(prefix),
);

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase.forTesting(NativeDatabase.memory()));
  tearDown(() => db.close());

  Future<AppDependencies> buildDependencies() async {
    final transport = FakeAuthTransport()
      ..loginResponses.add(
        AuthResult.ok(
          LoginResponse(
            tokens: TokenPair(
              accessToken: 'access-1',
              tokenType: 'Bearer',
              expiresIn: 900,
              refreshToken: 'refresh-1',
              refreshExpiresIn: 5184000,
              serverTime: DateTime.utc(2026, 11, 9, 12),
            ),
            user: const UserProfile(id: 'user-1', username: 'agente1', orgId: 'org-1', roles: ['agent']),
          ),
        ),
      );
    final auth = AuthController(transport: transport, tokenStore: FakeTokenStore(), deviceId: 'device-1');
    await auth.login(
      username: 'agente1',
      password: 'secret',
      deviceInfo: const DeviceInfo(deviceId: 'device-1', platform: 'android', appVersion: '1.0.0'),
    );
    final dependencies = AppDependencies(
      database: db,
      dio: Dio(),
      authController: auth,
      deviceId: 'device-1',
      spotLocation: _FakeSpot(),
    );
    await dependencies.formTemplates.upsertFromServerRow({
      'id': 'tv-1',
      'org_id': 'org-1',
      'version': 1,
      'change_seq': 1,
      'deleted_at': null,
      'template_id': 'tpl-1',
      'name': 'Avance',
      'version_number': 1,
      'published_at': '2026-10-01T00:00:00Z',
      'definition': {
        'fields': [
          {
            'key': 'notas',
            'type': 'text',
            'label': {'es': 'Notas'},
          },
        ],
      },
    });
    await db.replaceScheduleTasks(
      orgId: 'org-1',
      userId: 'user-1',
      version: 'v1',
      now: DateTime.utc(2026, 11, 9),
      rows: [
        ScheduleTasksCompanion.insert(
          id: 'task-q',
          orgId: 'org-1',
          userId: 'user-1',
          projectId: 'p-1',
          projectName: 'Obra Norte',
          wbs: '1.3',
          outlineNumber: '1.3',
          name: 'Excavación',
          position: 0,
          measuredBy: const Value('quantities'),
        ),
        ScheduleTasksCompanion.insert(
          id: 'task-1',
          orgId: 'org-1',
          userId: 'user-1',
          projectId: 'p-1',
          projectName: 'Obra Norte',
          wbs: '1.2',
          outlineNumber: '1.2',
          name: 'Descapote',
          position: 1,
        ),
      ],
      planned: [
        TaskPlannedItemsCompanion.insert(
          taskId: 'task-q',
          itemId: 'item-1',
          code: '1.1',
          description: 'Excavación mecánica',
          unit: 'm3',
          plannedQuantity: 100,
          validatedQuantity: 95,
          position: 0,
        ),
      ],
      locations: [
        TaskLocationOptionsCompanion.insert(
          taskId: 'task-q',
          locationId: 'loc-1',
          label: 'Tramo 2',
          isTaskLocation: true,
          position: 0,
        ),
      ],
      catalog: [
        ProjectItemsCompanion.insert(
          projectId: 'p-1',
          itemId: 'item-1',
          code: '1.1',
          description: 'Excavación mecánica',
          unit: 'm3',
          position: 0,
        ),
        ProjectItemsCompanion.insert(
          projectId: 'p-1',
          itemId: 'item-2',
          code: '1.2',
          description: 'Retiro de sobrantes',
          unit: 'm3',
          position: 1,
        ),
      ],
    );
    return dependencies;
  }

  Future<void> pumpCapture(WidgetTester tester, AppDependencies dependencies, {CaptureScreen screen = const CaptureScreen()}) async {
    tester.view.physicalSize = const Size(1200, 4000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(overrides: [appDependenciesProvider.overrideWithValue(dependencies)], child: testApp(screen)),
    );
    await tester.pumpAndSettle();
  }

  Future<void> pickTask(WidgetTester tester, String id) async {
    await tester.tap(find.byKey(const Key('capture_task_picker')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(ValueKey('task_picker_item-$id')));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('capture_save_button')));
    await tester.pumpAndSettle();
  }

  testWidgets('se guardan las cantidades con sus datos automáticos y progress_pct null', (tester) async {
    final dependencies = await buildDependencies();
    await pumpCapture(tester, dependencies);
    await pickTask(tester, 'task-q');
    expect(find.byKey(const Key('capture_progress_field')), findsNothing);
    expect(find.text('Planeado 100 m3 · validado 95 m3'), findsOneWidget);

    await tester.enterText(_byKeyPrefix('quantity_field-').first, '10');
    await tester.pumpAndSettle();
    expect(_byKeyPrefix('quantity_overrun-'), findsOneWidget);
    await save(tester);

    final record = await db.select(db.records).getSingle();
    expect((record.taskId, record.progressPct), ('task-q', null));
    final payload = jsonDecode((await db.select(db.outboxOps).getSingle()).payloadJson) as Map<String, dynamic>;
    expect(payload['progress_pct'], isNull);
    final row = (payload['quantities'] as List).single as Map<String, dynamic>;
    expect((row['item_id'], row['quantity'], row['location_id'], row['location_mode']), ('item-1', 10.0, null, 'task'));
    expect(row['client_id'], isA<String>());
    expect(row['captured_at'], isA<String>());
    expect(row['gps'], {'lat': 4.6, 'lon': -74.08, 'accuracy_m': 7.0});
    expect(row['seen'], {'planned': 100.0, 'validated': 95.0, 'location_id': 'loc-1'});
    expect(jsonDecode(record.quantitiesJson), payload['quantities']);
  });

  testWidgets('un ítem no planeado sin nota no se guarda', (tester) async {
    final dependencies = await buildDependencies();
    await pumpCapture(tester, dependencies);
    await pickTask(tester, 'task-q');
    await tester.tap(find.byKey(const Key('quantity_add_unplanned')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('quantity_item-item-2')));
    await tester.pumpAndSettle();
    await tester.enterText(_byKeyPrefix('quantity_field-').last, '3');
    await save(tester);
    expect(find.text('Escribe una nota: el ítem no está planeado o la cantidad es negativa.'), findsOneWidget);
    expect(await db.select(db.records).get(), isEmpty);

    await tester.enterText(_byKeyPrefix('quantity_note-').last, 'sobrante');
    await save(tester);
    final record = await db.select(db.records).getSingle();
    expect([for (final row in jsonDecode(record.quantitiesJson) as List) (row as Map)['item_id']], ['item-2']);
  });

  testWidgets('sin ninguna cantidad no se guarda', (tester) async {
    final dependencies = await buildDependencies();
    await pumpCapture(tester, dependencies);
    await pickTask(tester, 'task-q');
    await save(tester);
    expect(find.text('Escribe al menos una cantidad distinta de 0.'), findsOneWidget);
    expect(await db.select(db.records).get(), isEmpty);
  });

  testWidgets('cambiar de tarea con cantidades escritas pide confirmar', (tester) async {
    final dependencies = await buildDependencies();
    await pumpCapture(tester, dependencies);
    await pickTask(tester, 'task-q');
    await tester.enterText(_byKeyPrefix('quantity_field-').first, '5');
    await tester.pumpAndSettle();

    await pickTask(tester, 'task-1');
    expect(find.text('¿Cambiar de tarea?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('quantity_discard_cancel')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('quantity_section')), findsOneWidget);

    await pickTask(tester, 'task-1');
    await tester.tap(find.byKey(const Key('quantity_discard_confirm')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('quantity_section')), findsNothing);
    expect(find.byKey(const Key('capture_progress_field')), findsOneWidget);
  });

  testWidgets('registro ya validado: cantidades de solo lectura y no se envía nada', (tester) async {
    final dependencies = await buildDependencies();
    await db
        .into(db.records)
        .insert(
          RecordsCompanion.insert(
            id: 'record-1',
            orgId: 'org-1',
            version: 2,
            templateVersionId: 'tv-1',
            answersJson: jsonEncode({'notas': 'x'}),
            capturedAt: DateTime.utc(2026, 11, 8, 15),
            deviceId: 'device-1',
            createdBy: 'user-1',
            serverDataJson: const Value('{}'),
            taskId: const Value('task-q'),
            quantitiesJson: Value(
              jsonEncode([
                {'item_id': 'item-1', 'location_id': null, 'quantity': 4, 'note': '', 'client_id': 'g-1'},
              ]),
            ),
            knownValidated: const Value(true),
          ),
        );
    await pumpCapture(tester, dependencies, screen: const CaptureScreen(editRecordId: 'record-1'));
    expect(find.byKey(const Key('quantity_read_only')), findsOneWidget);
    expect(tester.widget<TextFormField>(find.byKey(const ValueKey('quantity_field-g-1'))).enabled, isFalse);
    expect(tester.widget<ListTile>(find.byKey(const Key('capture_task_picker'))).enabled, isFalse);
    await save(tester);
    expect(await db.select(db.outboxOps).get(), isEmpty);
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/features/capture/capture_quantities_test.dart`
Expected: FAIL (`capture_progress_field` aparece en la tarea por cantidades y no hay `quantity_section`).

- [ ] **Step 3: `task_progress_section.dart`**

En el constructor, después de `this.tasksLoaded = true,`:

```dart
    this.quantities,
    this.locked = false,
```

y los campos, después de `final bool tasksLoaded;`:

```dart
  /// Costos, pieza 4b: la captura de cantidades, en lugar del %, cuando la tarea elegida
  /// se mide por cantidades.
  final Widget? quantities;

  /// La tarea no se puede cambiar (registro ya validado).
  final bool locked;
```

En el `ListTile` del selector: agregar `enabled: !locked,` y cambiar `onTap: () async {` por `onTap: locked ? null : () async {` (cerrando igual). Reemplazar `if (selectedId != null)\n          ValueListenableBuilder<TextEditingValue>(` por:

```dart
        if (selectedId != null)
          quantities ??
              ValueListenableBuilder<TextEditingValue>(
```

(el resto del `ValueListenableBuilder` queda igual, con una sangría más). Actualizar el comentario de cabecera: "…con el último validado como referencia; en una tarea medida por cantidades, la sección de cantidades (costos, pieza 4b) en lugar del %."

- [ ] **Step 4: `capture_screen.dart`**

Imports nuevos:

```dart
import '../../core/schedule/quantity_rows.dart';
import 'quantity_capture_section.dart';
```

Campos nuevos en `_CaptureScreenState`, después de `String? _progressError;`:

```dart
  /// Costos, pieza 4b: la tarea elegida se mide por cantidades.
  bool _quantityTask = false;
  late final QuantityCaptureController _quantities = QuantityCaptureController(
    newId: ref.read(appDependenciesProvider).recordCapture.newAttachmentId,
    readGps: _spotGps,
  );

  /// Las tareas leídas de la BD (la edición las espera para saber cómo se mide la suya).
  late final Future<void> _tasksReady;
```

En `initState`, `_loadTasks();` pasa a `_tasksReady = _loadTasks();`. En `dispose`, antes de `super.dispose();`: `_quantities.dispose();`.

Métodos nuevos, después de `_loadTasks`:

```dart
  Future<GpsReading?> _spotGps() async {
    final fix = await ref.read(appDependenciesProvider).spotLocation.read();
    return fix == null ? null : GpsReading(lat: fix.lat, lon: fix.lon, accuracyM: fix.accuracyM);
  }

  /// Elegir otra tarea. Si había cantidades escritas, se confirma antes de descartarlas;
  /// una tarea por cantidades carga sus ítems, ubicaciones y catálogo.
  Future<void> _selectTask(String? id) async {
    if (id == _taskId) return;
    if (_quantityTask && _quantities.hasCaptured) {
      final discard = await _confirmDiscardQuantities();
      if (!discard || !mounted) return;
    }
    final task = _tasks.where((t) => t.id == id).firstOrNull;
    final byQuantities = task?.measuredByQuantities ?? false;
    if (byQuantities) {
      final quantityContext = await ref
          .read(appDependenciesProvider)
          .scheduleTaskRepository
          .quantityContext(taskId: task!.id, projectId: task.projectId);
      if (!mounted) return;
      _quantities.load(quantityContext);
    } else {
      _quantities.clear();
    }
    setState(() {
      // Otra tarea, otro avance: el % de la anterior no vale.
      _progressController.clear();
      _taskId = id;
      _progressError = null;
      _quantityTask = byQuantities;
    });
  }

  Future<bool> _confirmDiscardQuantities() async {
    final l10n = context.l10n;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l10n.quantityDiscardTitle),
        content: Text(l10n.quantityDiscardBody),
        actions: [
          TextButton(
            key: const Key('quantity_discard_cancel'),
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(l10n.reviewConfirmCancel),
          ),
          FilledButton(
            key: const Key('quantity_discard_confirm'),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(l10n.quantityDiscardConfirm),
          ),
        ],
      ),
    );
    return ok ?? false;
  }
```

`_readProgress` pasa a:

```dart
  /// Sin tarea: `ok` y nada que enviar. Con tarea: el % o las cantidades leídas, o el
  /// error en pantalla. Un registro validado (cantidades de solo lectura) no envía nada.
  ({bool ok, TaskProgress? value}) _readProgress(AppLocalizations l10n) {
    final taskId = _taskId;
    if (taskId == null) {
      setState(() => _progressError = null);
      return (ok: true, value: null);
    }
    if (_quantityTask) {
      if (_quantities.readOnly) return (ok: true, value: null);
      return _quantities.validate()
          ? (ok: true, value: TaskProgress(taskId: taskId, quantities: _quantities.payload()))
          : (ok: false, value: null);
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

En `_loadForEdit`, al final del método (después del `setState` existente):

```dart
    // Costos, pieza 4b: las cantidades del registro, sobre su tarea si sigue en la lista.
    await _tasksReady;
    final stored = decodeQuantities(record.quantitiesJson);
    final taskId = record.taskId;
    final task = _tasks.where((t) => t.id == taskId).firstOrNull;
    if (taskId == null || !mounted || (stored.isEmpty && !(task?.measuredByQuantities ?? false))) return;
    final schedule = dependencies.scheduleTaskRepository;
    final quantityContext = await schedule.quantityContext(taskId: taskId, projectId: task?.projectId);
    final known = await schedule.knownItems({for (final row in stored) row['item_id']! as String});
    if (!mounted) return;
    _quantities.load(quantityContext, stored: stored, known: known, readOnly: record.knownValidated);
    setState(() => _quantityTask = true);
```

En `_save`, en la llamada a `recordCapture.update(...)`, `progress: (value: progress.value),` pasa a:

```dart
          // Un registro validado no toca su tarea ni sus cantidades.
          progress: _quantityTask && _quantities.readOnly ? null : (value: progress.value),
```

En `build`, el `TaskProgressSection(...)` pasa a:

```dart
                TaskProgressSection(
                  tasks: _tasks,
                  tasksLoaded: _tasksLoaded,
                  selectedId: _taskId,
                  controller: _progressController,
                  errorText: _progressError,
                  locked: _quantityTask && _quantities.readOnly,
                  quantities: _quantityTask ? QuantityCaptureSection(controller: _quantities) : null,
                  onSelected: _selectTask,
                ),
```

Actualizar el comentario de cabecera del archivo con una línea: "- Costos, pieza 4b: en una tarea medida por cantidades, las cantidades por ítem en lugar del % (`QuantityCaptureSection`)."

- [ ] **Step 5: Correr las pruebas**

```bash
flutter test test/features/capture
bash tool/lint_ui_tokens.sh
```

Expected: PASS (también `capture_screen_test.dart` y `task_progress_section_test.dart`) y sin violaciones.

- [ ] **Step 6: Commit**

```bash
git add lib/features/capture test/features/capture/capture_quantities_test.dart
git commit -m "feat(capture): capture quantities instead of % on quantity-measured tasks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B10: Rechazos de cantidades en la cuarentena

**Files:**
- Modify: `lib/core/sync/quarantine_service.dart`
- Test: `test/core/sync/quarantine_quantities_test.dart`

- [ ] **Step 1: Escribir la prueba que falla**

`test/core/sync/quarantine_quantities_test.dart`:

```dart
// Costos, pieza 4b: `item_not_in_project` y `location_outside_task` llevan el registro a
// "Para revisar" con la fila señalada (el teléfono la identifica: el rechazo no dice
// cuál); corregir reescribe las cantidades y reenvía; quitar la tarea las vacía.
import 'dart:convert';

import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/capture/record_capture.dart';
import 'package:suno/core/forms/form_definition.dart';
import 'package:suno/core/schedule/quantity_rows.dart';
import 'package:suno/core/sync/quarantine_service.dart';
import 'package:suno/core/sync/result.dart';
import 'package:suno/core/sync/sync_engine.dart';
import 'package:suno/core/sync/sync_transport.dart';
import 'package:suno/data/local/database.dart';

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

Map<String, Object?> _row(String clientId, String itemId, {String? location, String note = ''}) => {
  'item_id': itemId,
  'location_id': location,
  'quantity': 2.0,
  'note': note,
  'client_id': clientId,
  'captured_at': '2026-11-09T14:00:00.000Z',
  'gps': null,
  'seen': null,
  'location_mode': location == null ? 'task' : 'refined',
};

void main() {
  late AppDatabase db;
  late FakeSyncTransport transport;
  late SyncEngine engine;
  late QuarantineService service;
  var ids = 0;

  setUp(() async {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeSyncTransport();
    ids = 0;
    engine = SyncEngine(db: db, transport: transport, deviceId: 'device-1', idGenerator: () => 'op-${ids++}');
    service = QuarantineService(db: db, engine: engine, clock: () => DateTime.utc(2026, 11, 9));
    await db.replaceScheduleTasks(
      orgId: 'org-1',
      userId: 'user-1',
      version: 'v1',
      now: DateTime.utc(2026, 11, 9),
      rows: [
        ScheduleTasksCompanion.insert(
          id: 'task-1',
          orgId: 'org-1',
          userId: 'user-1',
          projectId: 'p-1',
          projectName: 'Obra',
          wbs: '1.2',
          outlineNumber: '1.2',
          name: 'Descapote',
          position: 0,
          measuredBy: const Value('quantities'),
        ),
      ],
      planned: [
        TaskPlannedItemsCompanion.insert(
          taskId: 'task-1',
          itemId: 'item-1',
          code: '2.1',
          description: 'Concreto zapatas',
          unit: 'm3',
          plannedQuantity: 10,
          validatedQuantity: 0,
          position: 0,
        ),
      ],
      locations: [
        TaskLocationOptionsCompanion.insert(taskId: 'task-1', locationId: 'loc-1', label: 'Tramo 2', isTaskLocation: true, position: 0),
      ],
      catalog: [
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3', position: 0),
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-2', code: '2.2', description: 'Acero', unit: 'kg', position: 1),
      ],
    );
    // El 9.9 se vio en otra descarga y ya no está en el catálogo.
    await db.into(db.knownItems).insert(KnownItemsCompanion.insert(itemId: 'item-9', code: '9.9', description: 'Retirado', unit: 'un'));
    await RecordCapture(db: db, idGenerator: () => 'cap-${ids++}').save(
      identity: const CaptureIdentity(orgId: 'org-1', userId: 'user-1', deviceId: 'device-1'),
      templateVersionId: 'tv-1',
      definition: _definition,
      answers: {'notas': 'x'},
      captured: const {},
      progress: TaskProgress(
        taskId: 'task-1',
        quantities: [
          _row('q-1', 'item-1'),
          _row('q-2', 'item-9', note: 'viejo'),
          _row('q-3', 'item-1', location: 'loc-x'),
        ],
      ),
    );
  });
  tearDown(() => db.close());

  Future<void> rejectWith(String reason, {String field = 'quantities'}) async {
    final op = await db.select(db.outboxOps).getSingle();
    transport.pushResponses.add(
      Result.ok(
        PushResponse(
          schemaVersion: 2,
          results: [
            PushOpOutcome(
              opUuid: op.opUuid,
              status: 'rejected',
              error: PushOpError(
                code: 'validation_error',
                message: 'Rejected.',
                details: {'field': field, 'reason': reason},
              ),
            ),
          ],
        ),
      ),
    );
    await engine.drainOutbox();
  }

  test('item_not_in_project señala la fila del ítem fuera del plan y del catálogo', () async {
    await rejectWith('item_not_in_project');
    final item = (await service.list(orgId: 'org-1')).single;
    expect(item.quantityRejected, isTrue);
    expect(item.quantityFlags, [(index: 1, code: '9.9', reason: 'item_not_in_project')]);
  });

  test('location_outside_task señala la fila con una ubicación que no es de la tarea', () async {
    await rejectWith('location_outside_task');
    final item = (await service.list(orgId: 'org-1')).single;
    expect(item.quantityFlags, [(index: 2, code: '2.1', reason: 'location_outside_task')]);
  });

  test('corregir reescribe las cantidades del registro y de la operación, y la reenvía', () async {
    await rejectWith('item_not_in_project');
    final item = (await service.list(orgId: 'org-1')).single;
    final rows = decodeQuantities(item.record.quantitiesJson);
    final fixed = [rows[0], {...rows[1], 'item_id': 'item-2', 'note': 'reasignado'}, rows[2]];
    await service.correctQuantities(item, fixed);
    final op = await db.select(db.outboxOps).getSingle();
    expect(op.state, 'pending');
    expect((jsonDecode(op.payloadJson) as Map)['quantities'], fixed);
    expect(decodeQuantities((await db.select(db.records).getSingle()).quantitiesJson), fixed);
  });

  test('quitar la tarea también quita las cantidades', () async {
    await rejectWith('task_retired', field: 'task_id');
    final item = (await service.list(orgId: 'org-1')).single;
    expect(item.taskRejected, isTrue);
    await service.unlinkTask(item);
    final payload = jsonDecode((await db.select(db.outboxOps).getSingle()).payloadJson) as Map<String, dynamic>;
    expect(payload.containsKey('quantities'), isFalse);
    expect((await db.select(db.records).getSingle()).quantitiesJson, '[]');
  });

  test('un rechazo record_validated no es una corrección de filas', () async {
    await rejectWith('record_validated');
    final item = (await service.list(orgId: 'org-1')).single;
    expect((item.recordValidated, item.quantityRejected), (true, false));
    expect(item.quantityFlags, isEmpty);
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/core/sync/quarantine_quantities_test.dart`
Expected: FAIL de compilación (`quantityFlags`, `correctQuantities` no existen).

- [ ] **Step 3: Implementar**

En `lib/core/sync/quarantine_service.dart`, import nuevo:

```dart
import '../schedule/quantity_rows.dart';
```

Antes de `class QuarantinedRecord`:

```dart
/// Costos, pieza 4b: motivos de rechazo de una fila de cantidad que se corrigen
/// reasignando el ítem o la ubicación, o quitando la fila.
const quantityRejectionReasons = {'item_not_in_project', 'location_outside_task'};

/// Una fila de cantidad que el servidor rechazó, identificada en el teléfono (el rechazo
/// no dice cuál): su posición en `Records.quantitiesJson`, el código del ítem (`''` si no
/// se conoce) y el motivo.
typedef QuantityFlag = ({int index, String code, String reason});

List<Map<String, Object?>> _quantitiesOf(Object? value) => [
  for (final row in value is List ? value : const []) if (row is Map) row.cast<String, Object?>(),
];
```

En `QuarantinedRecord`: parámetro `this.quantityFlags = const [],` después de `this.taskLabels = const {},`, y los miembros:

```dart
  /// Costos, pieza 4b: las filas de cantidad señaladas.
  final List<QuantityFlag> quantityFlags;

  /// El servidor rechazó una fila de cantidad (ítem o ubicación): se corrige en
  /// `QuantityCorrectionScreen`. No si el registro ya está validado.
  bool get quantityRejected =>
      kind == QuarantineKind.rejected &&
      !recordValidated &&
      violations.any((v) => v.field == 'quantities' && quantityRejectionReasons.contains(v.reason));
```

`editedTask` pasa a considerar también `quantities`:

```dart
    return payload is Map &&
        (payload.containsKey('task_id') || payload.containsKey('progress_pct') || payload.containsKey('quantities'));
```

En `differences`, dentro de `if (editedTask) { ... }`, al final:

```dart
      final mine = _quantitiesOf(jsonDecode(record.quantitiesJson));
      final theirs = _quantitiesOf(server['quantities']);
      if (!sameQuantities(mine, theirs)) diffs.add((field: 'quantities', mine: mine, server: theirs));
```

En `list`, el `return [...]` final pasa a:

```dart
    return [
      for (final record in records)
        _describe(
          record,
          names[record.templateVersionId],
          byRecord[record.id]!,
          taskLabels,
          await _quantityFlags(record, byRecord[record.id]!),
        ),
    ];
```

`_describe` gana el parámetro `List<QuantityFlag> quantityFlags` (último) y lo pasa al constructor (`quantityFlags: quantityFlags,`).

Métodos nuevos en `QuarantineService`, después de `unlinkTask`:

```dart
  /// Costos, pieza 4b: corrige las cantidades de un registro rechazado (ítem o ubicación
  /// reasignados, filas quitadas) y reenvía el grupo. [rows] son las filas ya corregidas
  /// (`RecordQuantityPayload`).
  Future<void> correctQuantities(QuarantinedRecord item, List<Map<String, Object?>> rows) =>
      db.transaction(() async {
        await (db.update(db.records)..where((t) => t.id.equals(item.record.id))).write(
          RecordsCompanion(quantitiesJson: Value(jsonEncode(rows))),
        );
        for (final op in item.ops) {
          if (op.entity == 'record' && op.opType != 'delete') {
            final payload = (jsonDecode(op.payloadJson) as Map).cast<String, Object?>();
            if (payload.containsKey('quantities')) {
              payload['quantities'] = rows;
              await (db.update(db.outboxOps)..where((t) => t.opUuid.equals(op.opUuid))).write(
                OutboxOpsCompanion(payloadJson: Value(jsonEncode(payload))),
              );
            }
          }
          await engine.retryQuarantined(op.opUuid);
        }
      });

  /// Las filas que el servidor pudo rechazar por [ops]: el rechazo solo dice el motivo,
  /// así que se buscan con lo que sabe el teléfono. `item_not_in_project`: ítem que no
  /// está ni en el plan de la tarea ni en el catálogo de su proyecto.
  /// `location_outside_task`: ubicación que no es una de las opciones de la tarea.
  Future<List<QuantityFlag>> _quantityFlags(Record record, List<OutboxOp> ops) async {
    final reasons = {
      for (final op in ops)
        for (final v in _violations(op.errorFieldsJson))
          if (v.field == 'quantities' && quantityRejectionReasons.contains(v.reason)) v.reason,
    };
    final taskId = record.taskId;
    if (reasons.isEmpty || taskId == null) return const [];
    final rows = decodeQuantities(record.quantitiesJson);
    final schedule = ScheduleTaskRepository(db);
    final task = await schedule.byId(taskId);
    final context = await schedule.quantityContext(taskId: taskId, projectId: task?.projectId);
    final known = await schedule.knownItems({for (final row in rows) row['item_id']! as String});
    final measurable = {for (final i in context.allItems) i.itemId};
    final locations = {for (final l in context.locations) l.id};
    String code(Map<String, Object?> row) => known[row['item_id']]?.code ?? '';
    return [
      for (final (index, row) in rows.indexed) ...[
        if (reasons.contains('item_not_in_project') && !measurable.contains(row['item_id']))
          (index: index, code: code(row), reason: 'item_not_in_project'),
        if (reasons.contains('location_outside_task') &&
            row['location_id'] is String &&
            !locations.contains(row['location_id']))
          (index: index, code: code(row), reason: 'location_outside_task'),
      ],
    ];
  }
```

En `unlinkTask`:
- el `write` del registro pasa a `const RecordsCompanion(taskId: Value(null), progressPct: Value(null), quantitiesJson: Value('[]'))`;
- en el alta: `payload..remove('task_id')..remove('progress_pct')..remove('quantities');`
- en una edición, la condición pasa a `payload.containsKey('task_id') || payload.containsKey('progress_pct') || payload.containsKey('quantities')` y, además de poner el par en `null`, `payload.remove('quantities');` (un `update` con el par en `null` deja las cantidades vacías en el servidor);
- en el bucle de `later`, la condición `if (!payload.containsKey('task_id') && !payload.containsKey('progress_pct')) continue;` pasa a `if (!payload.containsKey('task_id') && !payload.containsKey('progress_pct') && !payload.containsKey('quantities')) continue;` y, después de poner el par en `null`, `payload.remove('quantities');`.

Actualizar el docstring de `unlinkTask`: "Rechazo por la tarea: el registro se queda sin tarea ni cantidades y se reenvía…".

En `resendMine`, antes de `final changed = <String, Object?>{};`:

```dart
    final localQuantities = decodeQuantities(record.quantitiesJson);
```

después del bucle `for (final (key, mine) in [...]) {...}`:

```dart
    // Costos, pieza 4b: las cantidades, si el agente las editó y difieren.
    if (item.editedTask && !sameQuantities(localQuantities, _quantitiesOf(server['quantities']))) {
      changed['quantities'] = localQuantities;
    }
```

y el bloque del par pasa a:

```dart
    // El servidor exige `task_id` y `progress_pct` juntos; las cantidades viajan con ellos
    // (sin la clave, el servidor deja la lista vacía).
    if (changed.containsKey('task_id') || changed.containsKey('progress_pct') || changed.containsKey('quantities')) {
      changed['task_id'] = record.taskId;
      changed['progress_pct'] = record.progressPct;
      if (localQuantities.isEmpty) {
        changed.remove('quantities');
      } else {
        changed['quantities'] = localQuantities;
      }
    }
```

- [ ] **Step 4: Correr las pruebas**

```bash
flutter test test/core/sync
```

Expected: PASS (también `quarantine_task_test.dart` y `quarantine_service_test.dart`).

- [ ] **Step 5: Commit**

```bash
git add lib/core/sync/quarantine_service.dart test/core/sync/quarantine_quantities_test.dart
git commit -m "feat(sync): flag rejected quantity rows and correct them from the review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B11: "Para revisar" y la corrección de cantidades

**Files:**
- Create: `lib/features/review/quantity_correction_screen.dart`
- Modify: `lib/features/review/review_screen.dart`
- Modify: `lib/ui/forms/dynamic_form.dart` (`formErrorMessage`)
- Test: `test/features/review/review_quantities_test.dart`

- [ ] **Step 1: Escribir la prueba que falla**

`test/features/review/review_quantities_test.dart`:

```dart
// Costos, pieza 4b: un rechazo de cantidades se ve en "Para revisar" nombrando la fila
// (código del ítem) y se corrige reasignando el ítem; al guardar se reenvía.
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/app_dependencies.dart';
import 'package:suno/core/auth/auth_controller.dart';
import 'package:suno/core/capture/record_capture.dart';
import 'package:suno/core/forms/form_definition.dart';
import 'package:suno/core/providers.dart';
import 'package:suno/core/sync/result.dart';
import 'package:suno/core/sync/sync_engine.dart';
import 'package:suno/core/sync/sync_transport.dart';
import 'package:suno/data/local/database.dart';
import 'package:suno/features/review/review_screen.dart';

import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';
import '../../core/sync/fake_sync_transport.dart';
import '../../support/test_app.dart';

const _definitionJson = {
  'fields': [
    {
      'key': 'notas',
      'type': 'text',
      'label': {'es': 'Notas'},
    },
  ],
};

Map<String, Object?> _row(String clientId, String itemId) => {
  'item_id': itemId,
  'location_id': null,
  'quantity': 2.0,
  'note': itemId == 'item-9' ? 'viejo' : '',
  'client_id': clientId,
  'captured_at': '2026-11-09T14:00:00.000Z',
  'gps': null,
  'seen': null,
  'location_mode': 'task',
};

void main() {
  late AppDatabase db;
  late FakeSyncTransport transport;
  late SyncEngine engine;
  late AppDependencies dependencies;

  setUp(() async {
    db = AppDatabase.forTesting(NativeDatabase.memory());
    transport = FakeSyncTransport();
    var ids = 0;
    engine = SyncEngine(db: db, transport: transport, deviceId: 'device-1', idGenerator: () => 'op-${ids++}');
    dependencies = AppDependencies(
      database: db,
      dio: Dio(),
      authController: AuthController(transport: FakeAuthTransport(), tokenStore: FakeTokenStore(), deviceId: 'device-1'),
      deviceId: 'device-1',
      syncEngine: engine,
    );
    await dependencies.formTemplates.upsertFromServerRow({
      'id': 'tv-1',
      'org_id': 'org-1',
      'version': 1,
      'change_seq': 1,
      'deleted_at': null,
      'template_id': 'tpl-1',
      'name': 'Avance',
      'version_number': 1,
      'published_at': '2026-10-01T00:00:00Z',
      'definition': _definitionJson,
    });
    await db.replaceScheduleTasks(
      orgId: 'org-1',
      userId: 'user-1',
      version: 'v1',
      now: DateTime.utc(2026, 11, 9),
      rows: [
        ScheduleTasksCompanion.insert(
          id: 'task-1',
          orgId: 'org-1',
          userId: 'user-1',
          projectId: 'p-1',
          projectName: 'Obra',
          wbs: '1.2',
          outlineNumber: '1.2',
          name: 'Descapote',
          position: 0,
          measuredBy: const Value('quantities'),
        ),
      ],
      planned: [
        TaskPlannedItemsCompanion.insert(
          taskId: 'task-1',
          itemId: 'item-1',
          code: '2.1',
          description: 'Concreto zapatas',
          unit: 'm3',
          plannedQuantity: 10,
          validatedQuantity: 0,
          position: 0,
        ),
      ],
      catalog: [
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3', position: 0),
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-2', code: '2.2', description: 'Acero de refuerzo', unit: 'kg', position: 1),
      ],
    );
    await db.into(db.knownItems).insert(KnownItemsCompanion.insert(itemId: 'item-9', code: '9.9', description: 'Retirado', unit: 'un'));
    await RecordCapture(db: db, idGenerator: () => 'cap-${ids++}').save(
      identity: const CaptureIdentity(orgId: 'org-1', userId: 'user-1', deviceId: 'device-1'),
      templateVersionId: 'tv-1',
      definition: (parseFormDefinition(_definitionJson) as DefinitionOk).definition,
      answers: {'notas': 'x'},
      captured: const {},
      progress: TaskProgress(taskId: 'task-1', quantities: [_row('q-1', 'item-1'), _row('q-2', 'item-9')]),
    );
    final op = await db.select(db.outboxOps).getSingle();
    transport.pushResponses.add(
      Result.ok(
        PushResponse(
          schemaVersion: 2,
          results: [
            PushOpOutcome(
              opUuid: op.opUuid,
              status: 'rejected',
              error: const PushOpError(
                code: 'validation_error',
                message: 'The item is not a measurable item of a budget of the task\'s project.',
                details: {'field': 'quantities', 'reason': 'item_not_in_project'},
              ),
            ),
          ],
        ),
      ),
    );
    await engine.drainOutbox();
  });
  tearDown(() => db.close());

  Future<void> pumpReview(WidgetTester tester) async {
    tester.view.physicalSize = const Size(390, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: testApp(const ReviewScreen(orgId: 'org-1')),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('nombra la fila rechazada y la corrige reasignando el ítem', (tester) async {
    await pumpReview(tester);
    expect(find.text('Fila del ítem 9.9: el ítem no es del presupuesto del proyecto.'), findsOneWidget);
    expect(find.text('Cantidades de hoy: El ítem no es del presupuesto del proyecto de la tarea.'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.byKey(const Key('review_correct_quantities')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('quantity_flagged-q-2')), findsOneWidget);
    expect(find.byKey(const ValueKey('quantity_flagged-q-1')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('quantity_reassign-q-2')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('quantity_item-item-2')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('quantity_flagged-q-2')), findsNothing);
    await tester.ensureVisible(find.byKey(const Key('quantity_correction_save')));
    await tester.tap(find.byKey(const Key('quantity_correction_save')));
    await tester.pumpAndSettle();

    final op = await db.select(db.outboxOps).getSingle();
    expect(op.state, 'pending');
    final quantities = (jsonDecode(op.payloadJson) as Map)['quantities'] as List;
    expect([for (final row in quantities) (row as Map)['item_id']], ['item-1', 'item-2']);
    expect(find.text('No hay registros para revisar.'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `flutter test test/features/review/review_quantities_test.dart`
Expected: FAIL (no aparece el texto de la fila ni `review_correct_quantities`).

- [ ] **Step 3: Mensajes de los motivos nuevos**

En `lib/ui/forms/dynamic_form.dart`, en `formErrorMessage`, después de `'record_validated' => l10n.formErrorRecordValidated,`:

```dart
  // Costos, pieza 4b: rechazos de las filas de cantidad.
  'item_not_in_project' => l10n.formErrorItemNotInProject,
  'location_outside_task' => l10n.formErrorLocationOutsideTask,
  'duplicate_client_id' => l10n.formErrorDuplicateClientId,
```

- [ ] **Step 4: `lib/features/review/quantity_correction_screen.dart`**

```dart
// Corregir las cantidades de un registro rechazado (costos, pieza 4b): la misma sección
// de captura de cantidades, con las filas que rechazó el servidor señaladas. Se reasigna
// el ítem o la ubicación, o se quita la fila; al guardar se reenvía
// (`QuarantineService.correctQuantities`).
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/schedule/quantity_rows.dart';
import '../../core/sync/quarantine_service.dart';
import '../../l10n/l10n.dart';
import '../../ui/widgets/app_button.dart';
import '../../ui/widgets/app_page_body.dart';
import '../capture/quantity_capture_section.dart';

class QuantityCorrectionScreen extends ConsumerStatefulWidget {
  const QuantityCorrectionScreen({super.key, required this.item});

  final QuarantinedRecord item;

  @override
  ConsumerState<QuantityCorrectionScreen> createState() => _QuantityCorrectionScreenState();
}

class _QuantityCorrectionScreenState extends ConsumerState<QuantityCorrectionScreen> {
  // Corregir no vuelve a leer el GPS: cada fila conserva los datos de su captura.
  late final QuantityCaptureController _controller = QuantityCaptureController(
    newId: ref.read(appDependenciesProvider).recordCapture.newAttachmentId,
    readGps: () async => null,
  );
  bool _loaded = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final record = widget.item.record;
    final taskId = record.taskId ?? '';
    final schedule = ref.read(appDependenciesProvider).scheduleTaskRepository;
    final stored = decodeQuantities(record.quantitiesJson);
    final task = await schedule.byId(taskId);
    final quantityContext = await schedule.quantityContext(taskId: taskId, projectId: task?.projectId);
    final known = await schedule.knownItems({for (final row in stored) row['item_id']! as String});
    if (!mounted) return;
    _controller.load(
      quantityContext,
      stored: stored,
      known: known,
      flaggedRows: {for (final flag in widget.item.quantityFlags) flag.index},
    );
    setState(() => _loaded = true);
  }

  Future<void> _save() async {
    final l10n = context.l10n;
    if (!_controller.validate()) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.captureFixErrors)));
      return;
    }
    setState(() => _saving = true);
    try {
      await ref.read(appDependenciesProvider).quarantine.correctQuantities(widget.item, _controller.payload());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
    if (mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.quantityCorrectionTitle)),
      body: AppPageBody(
        children: [
          if (!_loaded)
            const Center(child: CircularProgressIndicator())
          else ...[
            Text(l10n.quantityCorrectionExplain),
            const AppGap.sm(),
            QuantityCaptureSection(controller: _controller, correcting: true),
            const AppGap.lg(),
            AppButton(
              key: const Key('quantity_correction_save'),
              label: l10n.correctionSave,
              loadingLabel: l10n.captureSaving,
              isLoading: _saving,
              onPressed: _save,
            ),
          ],
        ],
      ),
    );
  }
}
```

- [ ] **Step 5: `review_screen.dart`**

- import nuevo: `import 'quantity_correction_screen.dart';`
- en `ReviewScreen.build`, al construir `_ReviewCard`, después de `onUnlinkTask: ...`:

```dart
                onCorrectQuantities: () async {
                  final corrected = await Navigator.of(
                    context,
                  ).push<bool>(MaterialPageRoute(builder: (_) => QuantityCorrectionScreen(item: item)));
                  if (corrected == true && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.reviewQueued)));
                    await _load();
                  }
                },
```

- `_ReviewCard`: parámetro `required this.onCorrectQuantities,` y campo `final VoidCallback onCorrectQuantities;`.
- en `violationLabel`, antes de `_ when field.startsWith('answers.')`: `'quantities' => l10n.quantitySectionTitle,`
- después del `for (final v in item.violations) Text(...)`:

```dart
            for (final flag in item.quantityFlags)
              Text(
                switch (flag.reason) {
                  'item_not_in_project' => l10n.reviewQuantityItemRow(
                    flag.code.isEmpty ? l10n.reviewQuantityUnknownItem : flag.code,
                  ),
                  _ => l10n.reviewQuantityLocationRow(flag.code.isEmpty ? l10n.reviewQuantityUnknownItem : flag.code),
                },
                key: ValueKey('review_quantity_flag-${flag.index}-${flag.reason}'),
              ),
```

- en el `_DiffTable` del conflicto, `label:` pasa a `(key) => key == 'task_id' || key == 'progress_pct' || key == 'quantities' ? violationLabel(key) : label(key)`.
- en los botones del rechazo, después del de `review_unlink_task`:

```dart
                  if (item.quantityRejected)
                    FilledButton(
                      key: const Key('review_correct_quantities'),
                      onPressed: busy ? null : onCorrectQuantities,
                      child: Text(l10n.reviewCorrectQuantities),
                    ),
```

- en `_DiffTable._show`, antes de `final List<Object?> list => list.join(', '),`:

```dart
      // Costos, pieza 4b: las filas de cantidad se resumen por cuántas son.
      final List<Object?> list when list.isNotEmpty && list.every((row) => row is Map) => l10n.reviewQuantityRows(
        list.length,
      ),
```

Actualizar el comentario de cabecera de `review_screen.dart`: "- Rechazo de cantidades (pieza 4b): la fila señalada y "Corregir cantidades"."

- [ ] **Step 6: Correr las pruebas**

```bash
flutter test test/features/review test/ui/forms
bash tool/lint_ui_tokens.sh
```

Expected: PASS y sin violaciones.

- [ ] **Step 7: Commit**

```bash
git add lib/features/review lib/ui/forms/dynamic_form.dart test/features/review/review_quantities_test.dart
git commit -m "feat(review): name rejected quantity rows and correct them

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B12: Detalle del registro y exportaciones

**Files:**
- Modify: `lib/data/local/record_status_repository.dart` (`RecordDetail.quantities`, `detail`)
- Modify: `lib/features/records/record_detail_screen.dart` (`_Body`, `_Answer`, `_export`)
- Modify: `lib/core/exports/record_export.dart` (`QuantityTableLabels`, `quantityTableRows`, `recordPdf`, `recordGeoJson`)
- Test: `test/core/exports/record_export_quantities_test.dart`, `test/features/records/record_detail_quantities_test.dart`

- [ ] **Step 1: Escribir las pruebas que fallan**

`test/core/exports/record_export_quantities_test.dart`:

```dart
// Costos, pieza 4b: el detalle del registro, la tabla del PDF y el GeoJSON llevan las
// cantidades (ítem, ubicación, cantidad con unidad, nota).
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/capture/record_capture.dart';
import 'package:suno/core/exports/record_export.dart';
import 'package:suno/core/forms/form_definition.dart';
import 'package:suno/data/local/database.dart';
import 'package:suno/data/local/record_status_repository.dart';

const _labels = QuantityTableLabels(
  title: 'Cantidades',
  item: 'Ítem',
  location: 'Ubicación',
  quantity: 'Cantidad',
  note: 'Nota',
  taskLocation: 'La de la tarea',
  refinedLocation: 'Ubicación precisada',
);

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase.forTesting(NativeDatabase.memory()));
  tearDown(() => db.close());

  test('detalle, tabla del PDF y GeoJSON', () async {
    await db.replaceScheduleTasks(
      orgId: 'org',
      userId: 'user',
      version: 'v1',
      now: DateTime.utc(2026, 11, 9),
      rows: [
        ScheduleTasksCompanion.insert(
          id: 'task-1',
          orgId: 'org',
          userId: 'user',
          projectId: 'p-1',
          projectName: 'Obra',
          wbs: '1.2',
          outlineNumber: '1.2',
          name: 'Descapote',
          position: 0,
        ),
      ],
      locations: [
        TaskLocationOptionsCompanion.insert(taskId: 'task-1', locationId: 'loc-2', label: 'Tramo 2 / PR 1', isTaskLocation: false, position: 1),
      ],
      catalog: [
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3', position: 0),
        ProjectItemsCompanion.insert(projectId: 'p-1', itemId: 'item-2', code: '2.2', description: 'Acero de refuerzo', unit: 'kg', position: 1),
      ],
    );
    final definition =
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
    final id = await RecordCapture(db: db).save(
      identity: const CaptureIdentity(orgId: 'org', userId: 'user', deviceId: 'device'),
      templateVersionId: 'tv',
      definition: definition,
      answers: const {},
      captured: const {},
      progress: const TaskProgress(
        taskId: 'task-1',
        quantities: [
          {'item_id': 'item-1', 'location_id': null, 'quantity': 12.5, 'note': ''},
          {'item_id': 'item-2', 'location_id': 'loc-2', 'quantity': 3.0, 'note': 'sobrante'},
        ],
      ),
    );
    final detail = (await RecordStatusRepository(db).detail(orgId: 'org', recordId: id))!;
    expect(quantityTableRows(detail, _labels), [
      ['2.1 Concreto zapatas', 'La de la tarea', '12,5 m3', ''],
      ['2.2 Acero de refuerzo', 'Tramo 2 / PR 1', '3 kg', 'sobrante'],
    ]);

    final geo = jsonDecode(utf8.decode(recordGeoJson(detail, definition, roles: ['SUPERVISOR']))) as Map<String, dynamic>;
    final properties = ((geo['features'] as List).single as Map)['properties'] as Map;
    expect(properties['quantities'], [
      {'item_code': '2.1', 'item_id': 'item-1', 'location_id': null, 'quantity': 12.5, 'unit': 'm3', 'note': ''},
      {'item_code': '2.2', 'item_id': 'item-2', 'location_id': 'loc-2', 'quantity': 3.0, 'unit': 'kg', 'note': 'sobrante'},
    ]);

    final pdf = await recordPdf(
      detail,
      definition,
      roles: ['ADMIN'],
      fontBytes: ByteData.sublistView(File('assets/fonts/Inter-Regular.ttf').readAsBytesSync()),
      locale: 'es',
      title: 'Informe de registro',
      sourceNote: 'Pendiente de sincronizar',
      locationLabel: 'Ubicación',
      noLocation: 'Sin ubicación.',
      answersLabel: 'Respuestas',
      attachmentsNote: 'Adjuntos por referencia',
      authorLabel: 'Autor',
      versionLabel: 'Versión del formulario',
      quantityLabels: _labels,
    );
    expect(ascii.decode(pdf.take(5).toList()), '%PDF-');
  });
}
```

`test/features/records/record_detail_quantities_test.dart`:

```dart
// Costos, pieza 4b: el detalle del registro muestra sus cantidades, una por bloque
// (ítem; cantidad con unidad; ubicación; nota), sin desbordar a 390 px.
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:drift/drift.dart' show Value;
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:suno/core/app_dependencies.dart';
import 'package:suno/core/auth/auth_controller.dart';
import 'package:suno/core/auth/auth_models.dart';
import 'package:suno/core/auth/auth_result.dart';
import 'package:suno/core/providers.dart';
import 'package:suno/data/local/database.dart';
import 'package:suno/features/records/record_detail_screen.dart';

import '../../core/auth/fake_auth_transport.dart';
import '../../core/auth/fake_token_store.dart';
import '../../support/test_app.dart';

void main() {
  late AppDatabase db;

  setUp(() => db = AppDatabase.forTesting(NativeDatabase.memory()));
  tearDown(() => db.close());

  testWidgets('muestra las cantidades del registro', (tester) async {
    final auth = AuthController(
      transport: FakeAuthTransport()
        ..loginResponses.add(
          AuthResult.ok(
            LoginResponse(
              tokens: TokenPair(
                accessToken: 'a',
                tokenType: 'Bearer',
                expiresIn: 900,
                refreshToken: 'r',
                refreshExpiresIn: 3600,
                serverTime: DateTime.utc(2026, 11, 9),
              ),
              user: const UserProfile(id: 'user-1', username: 'agente1', orgId: 'org-1', roles: ['agent']),
            ),
          ),
        ),
      tokenStore: FakeTokenStore(),
      deviceId: 'device-1',
    );
    await auth.login(
      username: 'agente1',
      password: 'x',
      deviceInfo: const DeviceInfo(deviceId: 'device-1', platform: 'android', appVersion: '1.0.0'),
    );
    final dependencies = AppDependencies(database: db, dio: Dio(), authController: auth, deviceId: 'device-1');
    await db.into(db.knownItems).insert(
      KnownItemsCompanion.insert(itemId: 'item-1', code: '2.1', description: 'Concreto zapatas', unit: 'm3'),
    );
    await db
        .into(db.records)
        .insert(
          RecordsCompanion.insert(
            id: 'record-1',
            orgId: 'org-1',
            version: 1,
            templateVersionId: 'tv-1',
            answersJson: '{}',
            capturedAt: DateTime.utc(2026, 11, 9, 15),
            deviceId: 'device-1',
            createdBy: 'user-1',
            taskId: const Value('task-1'),
            quantitiesJson: Value(
              jsonEncode([
                {'item_id': 'item-1', 'location_id': null, 'quantity': 12.5, 'note': 'frente norte'},
                {'item_id': 'item-7', 'location_id': 'loc-x', 'quantity': -1, 'note': 'ajuste'},
              ]),
            ),
          ),
        );
    tester.view.physicalSize = const Size(390, 1600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [appDependenciesProvider.overrideWithValue(dependencies)],
        child: testApp(const RecordDetailScreen(recordId: 'record-1')),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Cantidades de hoy'), findsOneWidget);
    expect(find.text('2.1 Concreto zapatas'), findsOneWidget);
    expect(find.text('12,5 m3'), findsOneWidget);
    expect(find.text('Ubicación: La de la tarea'), findsOneWidget);
    expect(find.text('Nota: frente norte'), findsOneWidget);
    expect(find.text('item-7'), findsOneWidget, reason: 'sin datos del ítem, su id');
    expect(find.text('-1'), findsOneWidget);
    expect(find.text('Ubicación: Ubicación precisada'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
```

- [ ] **Step 2: Correr y ver que fallan**

Run: `flutter test test/core/exports/record_export_quantities_test.dart test/features/records/record_detail_quantities_test.dart`
Expected: FAIL de compilación (`QuantityTableLabels`, `quantityTableRows` no existen).

- [ ] **Step 3: `record_status_repository.dart`**

Imports nuevos:

```dart
import '../../core/schedule/quantity_rows.dart';
import 'schedule_task_repository.dart';
```

`RecordDetail` pasa a:

```dart
@immutable
class RecordDetail {
  const RecordDetail({
    required this.record,
    required this.summary,
    required this.attachments,
    this.quantities = const [],
  });

  final Record record;
  final RecordSummary summary;
  final List<RecordAttachmentView> attachments;

  /// Costos, pieza 4b: las cantidades del registro, con los datos del ítem y la etiqueta
  /// de la ubicación que el teléfono conozca.
  final List<QuantityLine> quantities;

  Map<String, dynamic> get answers => jsonDecode(record.answersJson) as Map<String, dynamic>;
}
```

En `detail`, antes del `return RecordDetail(`:

```dart
    final stored = decodeQuantities(record.quantitiesJson);
    final schedule = ScheduleTaskRepository(db);
    final items = await schedule.knownItems({for (final row in stored) row['item_id']! as String});
    final locationLabels = await schedule.locationLabels({
      for (final row in stored)
        if (row['location_id'] is String) row['location_id']! as String,
    });
```

y en el `RecordDetail(...)`, después de `attachments: [...]`:

```dart
      quantities: [
        for (final row in stored)
          QuantityLine.fromStored(row, item: items[row['item_id']], locationLabel: locationLabels[row['location_id']]),
      ],
```

Actualizar el comentario de `detail`: "Un registro con su estado, sus adjuntos y sus cantidades…".

- [ ] **Step 4: `record_export.dart`**

Imports nuevos:

```dart
import 'package:meta/meta.dart';

import '../schedule/quantity_rows.dart';
```

Después de `canExportRecords`:

```dart
/// Textos de la tabla de cantidades del PDF (costos, pieza 4b).
@immutable
class QuantityTableLabels {
  const QuantityTableLabels({
    required this.title,
    required this.item,
    required this.location,
    required this.quantity,
    required this.note,
    required this.taskLocation,
    required this.refinedLocation,
  });

  final String title;
  final String item;
  final String location;
  final String quantity;
  final String note;

  /// Una fila sin ubicación propia: la de la tarea.
  final String taskLocation;

  /// Una ubicación precisada que el teléfono no sabe nombrar.
  final String refinedLocation;
}

/// Las filas de la tabla de cantidades: ítem, ubicación, cantidad con unidad y nota.
List<List<String>> quantityTableRows(RecordDetail detail, QuantityTableLabels labels) => [
  for (final line in detail.quantities)
    [
      line.label,
      line.locationId == null ? labels.taskLocation : line.locationLabel ?? labels.refinedLocation,
      '${formatQuantity(line.quantity)} ${line.unit}'.trim(),
      line.note,
    ],
];
```

En `recordGeoJson`, en `'properties'`, después de `'sync_status': detail.summary.status.name,`:

```dart
                // Costos, pieza 4b.
                'quantities': [
                  for (final line in detail.quantities)
                    {
                      'item_code': line.code,
                      'item_id': line.itemId,
                      'location_id': line.locationId,
                      'quantity': line.quantity,
                      'unit': line.unit,
                      'note': line.note,
                    },
                ],
```

En `recordPdf`, parámetro nuevo después de `required String versionLabel,`:

```dart
  QuantityTableLabels? quantityLabels,
```

y, en `build: (_) => [...]`, inmediatamente antes de `pw.SizedBox(height: 16), pw.Text(attachmentsNote, ...)`:

```dart
        if (quantityLabels != null && detail.quantities.isNotEmpty) ...[
          pw.SizedBox(height: 16),
          pw.Text(quantityLabels.title, style: const pw.TextStyle(fontSize: 14)),
          pw.SizedBox(height: 6),
          pw.TableHelper.fromTextArray(
            headers: [quantityLabels.item, quantityLabels.location, quantityLabels.quantity, quantityLabels.note],
            data: quantityTableRows(detail, quantityLabels),
            headerStyle: const pw.TextStyle(fontSize: 9),
            cellStyle: const pw.TextStyle(fontSize: 9),
          ),
        ],
```

- [ ] **Step 5: `record_detail_screen.dart`**

Imports nuevos:

```dart
import '../../core/schedule/quantity_rows.dart';
```

En `_export`, en la llamada a `recordPdf`, después de `versionLabel: l10n.recordExportVersion,`:

```dart
              quantityLabels: QuantityTableLabels(
                title: l10n.recordExportQuantities,
                item: l10n.recordExportItem,
                location: l10n.quantityLocation,
                quantity: l10n.recordExportQuantity,
                note: l10n.recordExportNote,
                taskLocation: l10n.quantityLocationTask,
                refinedLocation: l10n.quantityLocationRefined,
              ),
```

En `_Body.build`, después del bloque de respuestas (el `if (definition == null) ... else for (...)`) y antes de `if (_extraPhotos(...).isNotEmpty) ...[`:

```dart
        // Costos, pieza 4b: un bloque por fila para no desbordar en un teléfono.
        if (detail.quantities.isNotEmpty) ...[
          const AppGap.lg(),
          Text(l10n.quantitySectionTitle, style: theme.textTheme.titleSmall),
          const AppGap.sm(),
          for (final (index, line) in detail.quantities.indexed)
            _Answer(
              key: ValueKey('record_quantity-$index'),
              label: line.label,
              value: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(l10n.quantityLineValue(formatQuantity(line.quantity), line.unit).trim()),
                  Text(
                    l10n.quantityLineLocation(
                      line.locationId == null
                          ? l10n.quantityLocationTask
                          : line.locationLabel ?? l10n.quantityLocationRefined,
                    ),
                  ),
                  if (line.note.isNotEmpty) Text(l10n.quantityLineNote(line.note)),
                ],
              ),
            ),
        ],
```

`_Answer` gana `super.key`: `const _Answer({super.key, required this.label, required this.value});`.

- [ ] **Step 6: Correr las pruebas**

```bash
flutter test test/core/exports test/features/records test/data/local
bash tool/lint_ui_tokens.sh
```

Expected: PASS y sin violaciones.

- [ ] **Step 7: Commit**

```bash
git add lib/data/local/record_status_repository.dart lib/features/records/record_detail_screen.dart lib/core/exports/record_export.dart test/core/exports/record_export_quantities_test.dart test/features/records/record_detail_quantities_test.dart
git commit -m "feat(records): quantities in the record detail, PDF and GeoJSON

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task B13: E2E de cantidades en el emulador

**Files:**
- Create: `integration_test/e2e_quantities_test.dart`
- Modify: `tool/e2e.sh`

- [ ] **Step 1: La prueba de integración**

`integration_test/e2e_quantities_test.dart`:

```dart
// Prueba E2E de cantidades (costos, pieza 4b). La ejecuta `E2E_FLOW=quantities bash
// tool/e2e.sh`, que siembra la tarea 1.1 medida por cantidades (`bootstrap_demo
// --with-quantities`), valida el registro en el servidor cuando la prueba lo pide y
// verifica el resultado. No funciona sola: necesita los --dart-define del script.
//
// Coordinación con el script por `adb logcat` (tag `flutter`):
//   E2E:SHOT:<nombre>     → el script guarda una captura de pantalla (si E2E_SHOTS)
//   E2E:SYNCED            → el script valida el registro en el servidor
//   E2E:VALIDATED_SEEN    → la tarea muestra lo validado (12,5 m3)
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:suno/features/home/record_status_list.dart';
import 'package:suno/main.dart' as app;

const _username = String.fromEnvironment('E2E_USERNAME');
const _password = String.fromEnvironment('E2E_PASSWORD');
const _marker = String.fromEnvironment('E2E_MARKER');

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('cantidades capturadas en campo llegan, se validan y la tarea lo refleja', (tester) async {
    expect(
      _username.isNotEmpty && _password.isNotEmpty && _marker.isNotEmpty,
      isTrue,
      reason: 'Ejecutar con E2E_FLOW=quantities tool/e2e.sh: faltan E2E_USERNAME/E2E_PASSWORD/E2E_MARKER',
    );

    await app.main();
    await _pumpUntilAny(tester, [
      find.byKey(const Key('privacy_accept_button')),
      find.byKey(const Key('login_submit_button')),
    ]);

    // Aviso de privacidad (Ley 1581), solo si falta la autorización.
    if (find.byKey(const Key('privacy_accept_button')).evaluate().isNotEmpty) {
      for (final purpose in ['account', 'location', 'photos']) {
        await _tapVisible(tester, find.byKey(Key('privacy_$purpose')));
      }
      await _tapVisible(tester, find.byKey(const Key('privacy_accept_button')));
      await _pumpUntil(tester, find.byKey(const Key('login_submit_button')));
    }

    // Login. El primer intento choca con la CA interna de Caddy: TOFU.
    await tester.enterText(find.byKey(const Key('login_username_field')), _username);
    await tester.enterText(find.byKey(const Key('login_password_field')), _password);
    await _tapVisible(tester, find.byKey(const Key('login_submit_button')));
    await _pumpUntil(tester, find.byKey(const Key('tofu_trust_button')));
    await _tapVisible(tester, find.byKey(const Key('tofu_compared_check')));
    await _tapVisible(tester, find.byKey(const Key('tofu_trust_button')));
    await _pumpUntil(tester, find.byKey(const Key('home_new_record_button')), timeout: const Duration(seconds: 60));

    // Sincronización inicial: plantilla, tareas, ítems planeados y catálogo.
    await _tapVisible(tester, find.byKey(const Key('home_sync_button')));
    await _pumpUntil(tester, find.byKey(const Key('home_last_sync')), timeout: const Duration(seconds: 60));

    // Captura (con red): 12,5 m3 del 1.1 planeado y 3 m3 del 1.2 no planeado, con nota.
    await _tapVisible(tester, find.byKey(const Key('home_new_record_button')));
    await _pumpUntil(tester, find.byKey(const Key('capture_save_button')));
    final notas = find.descendant(of: find.byKey(const ValueKey('form-field-notas')), matching: find.byType(TextField));
    await tester.enterText(notas, _marker);
    await _pickExcavation(tester);
    await _pumpUntil(tester, find.textContaining('Planeado 100 m3'));
    await tester.enterText(_byKeyPrefix('quantity_field-').first, '12,5');
    await tester.pump();
    await _shot(tester, 'captura-cantidades');
    await _tapVisible(tester, find.byKey(const Key('quantity_add_unplanned')));
    await _pumpUntil(tester, find.byKey(const Key('quantity_item_search')));
    await tester.enterText(find.byKey(const Key('quantity_item_search')), 'retiro');
    await tester.pump();
    await _shot(tester, 'buscador-items');
    await _tapVisible(tester, find.textContaining('Retiro de sobrantes'));
    await _pumpUntil(tester, find.text('No planeado en esta tarea'));
    await tester.enterText(_byKeyPrefix('quantity_field-').last, '3');
    await tester.enterText(_byKeyPrefix('quantity_note-').last, 'sobrante E2E');
    await tester.pump();
    await tester.ensureVisible(_byKeyPrefix('quantity_note-').last);
    await _shot(tester, 'captura-no-planeado');
    await _tapVisible(tester, find.byKey(const Key('capture_save_button')));
    await _pumpUntil(tester, find.byKey(const Key('home_new_record_button')));

    // Sincroniza hasta que el registro quede confirmado.
    final synced = DateTime.now().add(const Duration(seconds: 120));
    while (_chip('Sincronizado').evaluate().isEmpty) {
      if (DateTime.now().isAfter(synced)) fail('El registro no pasó a Sincronizado en 120 s');
      await _tapVisible(tester, find.byKey(const Key('home_sync_button')));
      await _pumpFor(tester, const Duration(seconds: 10));
    }
    await _shot(tester, 'inicio-sincronizado');
    // La fila de la lista (un `ListTile` con el chip de estado) abre el detalle.
    await _tapVisible(tester, find.ancestor(of: _chip('Sincronizado').first, matching: find.byType(ListTile)).first);
    await _pumpUntil(tester, find.text('Cantidades de hoy'));
    await _shot(tester, 'detalle-registro');
    await tester.pageBack();
    await _pumpUntil(tester, find.byKey(const Key('home_new_record_button')));
    _mark('SYNCED');

    // El script valida el registro; la tarea lo muestra tras sincronizar.
    final validated = DateTime.now().add(const Duration(seconds: 180));
    while (true) {
      await _tapVisible(tester, find.byKey(const Key('home_sync_button')));
      await _pumpFor(tester, const Duration(seconds: 10));
      await _tapVisible(tester, find.byKey(const Key('home_new_record_button')));
      await _pumpUntil(tester, find.byKey(const Key('capture_save_button')));
      await _pickExcavation(tester);
      await _pumpUntil(tester, find.textContaining('Planeado 100 m3'));
      if (find.textContaining('validado 12,5 m3').evaluate().isNotEmpty) break;
      if (DateTime.now().isAfter(validated)) fail('La tarea no mostró lo validado en 180 s');
      await tester.pageBack();
      await _pumpUntil(tester, find.byKey(const Key('home_new_record_button')));
    }
    await _shot(tester, 'tarea-validada');
    _mark('VALIDATED_SEEN');
  });
}

Future<void> _pickExcavation(WidgetTester tester) async {
  await _tapVisible(tester, find.byKey(const Key('capture_task_picker')));
  await _pumpUntil(tester, find.byKey(const Key('task_picker_search')));
  await tester.enterText(find.byKey(const Key('task_picker_search')), 'excav');
  await tester.pump();
  await _tapVisible(tester, find.text('1.1 Excavación'));
  await _pumpUntil(tester, find.byKey(const Key('quantity_section')));
}

Finder _byKeyPrefix(String prefix) => find.byWidgetPredicate(
  (widget) => widget.key is ValueKey<String> && (widget.key! as ValueKey<String>).value.startsWith(prefix),
);

Finder _chip(String label) => find.descendant(of: find.byType(RecordStatusChip), matching: find.text(label));

/// Hace visible el widget antes de tocarlo.
Future<void> _tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump();
  await tester.tap(finder);
  await tester.pump();
}

void _mark(String name) {
  // ignore: avoid_print
  print('E2E:$name');
}

/// Pide al script una captura de pantalla y le da tiempo de tomarla.
Future<void> _shot(WidgetTester tester, String name) async {
  _mark('SHOT:$name');
  await _pumpFor(tester, const Duration(seconds: 3));
}

/// `pumpAndSettle` no sirve: los temporizadores de sincronización y las
/// animaciones de carga nunca dejan la app quieta.
Future<void> _pumpUntil(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 30)}) =>
    _pumpUntilAny(tester, [finder], timeout: timeout);

Future<void> _pumpUntilAny(
  WidgetTester tester,
  List<Finder> finders, {
  Duration timeout = const Duration(seconds: 30),
}) async {
  final deadline = DateTime.now().add(timeout);
  while (!finders.any((f) => f.evaluate().isNotEmpty)) {
    if (DateTime.now().isAfter(deadline)) fail('No apareció ninguno de $finders en $timeout');
    await tester.pump(const Duration(milliseconds: 200));
  }
}

Future<void> _pumpFor(WidgetTester tester, Duration duration) async {
  final end = DateTime.now().add(duration);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 200));
  }
}
```

- [ ] **Step 2: `tool/e2e.sh`**

Después de la línea `RUN_ID="e2e-$(date +%Y%m%d%H%M%S)"`:

```bash
# Variante: `fase0` (por defecto: registro con % en modo avión) o `quantities`
# (costos, pieza 4b: cantidades por ítem, validación en el servidor y lo validado en la
# tarea). Con E2E_SHOTS=<carpeta>, guarda las capturas que pida la prueba (E2E:SHOT:…).
FLOW="${E2E_FLOW:-fase0}"
case "$FLOW" in
  fase0) TARGET=integration_test/e2e_fase0_test.dart; SEED_FLAGS=(--with-schedule) ;;
  quantities) TARGET=integration_test/e2e_quantities_test.dart; SEED_FLAGS=(--with-schedule --with-quantities) ;;
  *) echo "E2E_FLOW desconocido: $FLOW (fase0 | quantities)" >&2; exit 2 ;;
esac
SHOTS="${E2E_SHOTS:-}"
[[ -z "$SHOTS" ]] || mkdir -p "$SHOTS"
```

En `cleanup`, `log "OK: el registro $RUN_ID llegó al servidor con su tarea y su avance."` pasa a `log "OK ($FLOW): el registro $RUN_ID llegó al servidor con su tarea y su avance."`.

En la siembra, `--password "$PASSWORD" --with-schedule >/dev/null` pasa a `--password "$PASSWORD" "${SEED_FLAGS[@]}" >/dev/null`.

En `flutter drive`, `--target=integration_test/e2e_fase0_test.dart \` pasa a `--target="$TARGET" \`.

El bucle de coordinación pasa a:

```bash
offline=0
approved=0
while kill -0 "$TEST_PID" 2>/dev/null; do
  if [[ $offline -eq 0 ]] && grep -q 'E2E:READY_FOR_OFFLINE' "$WORK/logcat.log"; then
    log "Modo avión: ON"
    adb shell cmd connectivity airplane-mode enable >/dev/null
    offline=1
  fi
  if [[ $offline -eq 1 ]] && grep -q 'E2E:SAVED_OFFLINE' "$WORK/logcat.log"; then
    log "Modo avión: OFF"
    adb shell cmd connectivity airplane-mode disable >/dev/null
    offline=2
  fi
  if [[ $FLOW == quantities && $approved -eq 0 ]] && grep -q 'E2E:SYNCED' "$WORK/logcat.log"; then
    log "Validando el registro $RUN_ID en el servidor"
    "${COMPOSE[@]}" exec -T -e E2E_MARKER="$RUN_ID" api python manage.py shell -c '
import os
from suno.core.models import Record, RecordReview
record = Record.objects.get(answers__notas=os.environ["E2E_MARKER"], deleted_at__isnull=True)
RecordReview.objects.create(org=record.org, record=record, status="approved", reviewer=record.created_by)
print("registro", record.pk, "validado")
' || die "No se pudo validar el registro $RUN_ID."
    approved=1
  fi
  if [[ -n "$SHOTS" ]]; then
    while IFS= read -r shot; do
      [[ -f "$SHOTS/$shot.png" ]] && continue
      adb exec-out screencap -p >"$SHOTS/$shot.png"
      log "Captura: $SHOTS/$shot.png"
    done < <(grep -o 'E2E:SHOT:[a-z0-9-]*' "$WORK/logcat.log" | sed 's/^E2E:SHOT://' | sort -u)
  fi
  sleep 1
done
```

Después de `grep -q 'E2E:SYNCED' "$WORK/logcat.log" || die "La prueba terminó sin la marca E2E:SYNCED."`:

```bash
if [[ $FLOW == quantities ]]; then
  grep -q 'E2E:VALIDATED_SEEN' "$WORK/logcat.log" || die "La prueba terminó sin ver lo validado en la tarea."
fi
```

La verificación en el servidor queda para `fase0` y se agrega la de `quantities`: envolver el bloque `"${COMPOSE[@]}" exec -T -e E2E_MARKER="$RUN_ID" api python manage.py shell -c '…' || die "…"` existente en `if [[ $FLOW == fase0 ]]; then … else … fi`, con esta rama `else`:

```bash
else
  "${COMPOSE[@]}" exec -T -e E2E_MARKER="$RUN_ID" api python manage.py shell -c '
import os, sys
from suno.core.models import Record, RecordReview
marker = os.environ["E2E_MARKER"]
records = list(Record.objects.filter(answers__notas=marker, deleted_at__isnull=True).select_related("task"))
rows = records[0].quantities if len(records) == 1 else []
ok = (
    len(records) == 1
    and records[0].task is not None
    and records[0].task.wbs == "1.1"
    and records[0].progress_pct is None
    and [r["quantity"] for r in rows] == ["12.5000", "3.0000"]
    and all(r.get("client_id") and r.get("captured_at") and r.get("location_mode") == "task" for r in rows)
    and rows[1]["note"] == "sobrante E2E"
    and RecordReview.objects.filter(record=records[0], status="approved").exists()
)
for r in rows:
    print("fila", r["item_id"], r["quantity"], repr(r["note"]), r.get("location_mode"), "gps" if r.get("gps") else "sin gps")
sys.exit(0 if ok else 1)
' || die "El registro $RUN_ID no está en el servidor como se esperaba (12,5 y 3 m3, validado)."
fi
```

Actualizar el comentario de cabecera de `tool/e2e.sh`: "Uso: bash tool/e2e.sh (E2E_FLOW=quantities para la variante de cantidades; E2E_SHOTS=<carpeta> para guardar capturas)".

- [ ] **Step 3: Verificar el script**

```bash
bash -n tool/e2e.sh
dart analyze integration_test
```

Expected: sin errores.

- [ ] **Step 4: Commit**

```bash
git add integration_test/e2e_quantities_test.dart tool/e2e.sh
git commit -m "test(e2e): field quantities captured, synced, validated and shown on the task

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B14: Verificación completa, E2E en emulador, revisión visual a 390 px y PR de la app

**Files:**
- (ninguno nuevo; correcciones que salgan de la revisión)

- [ ] **Step 1: Suites y compuertas**

```bash
flutter pub get --enforce-lockfile
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
dart analyze
flutter test --concurrency=1 --coverage
py -3 tool/check_lcov.py coverage/lcov.info --min 75
bash tool/lint_ui_tokens.sh
bash tool/lint_no_dynamic_code.sh
bash tool/lint_no_tls_bypass.sh
bash tool/check_naming.sh
git diff --exit-code -- lib/data/local/database.steps.dart drift_schemas test/drift/app/generated
```

Expected: analyze sin avisos; todas las pruebas en verde (las de la línea base de la Task B0 más las nuevas); cobertura ≥ 75 %; lints y guardia con exit 0; los generados de Drift ya versionados (el `git diff` no muestra cambios).

- [ ] **Step 2: Contrato fijado**

```bash
MERGE=$(sed -n 's/^ref=//p' contract/SOURCE)
git -C C:/Users/gabri/APP/SUNO_server show "$MERGE:server/openapi/v1.yaml" | diff -q - contract/v1.yaml && echo "contrato idéntico"
```

Expected: `contrato idéntico` (lo mismo que comprueba `contract.yml` en CI).

- [ ] **Step 3: E2E en el emulador (las dos variantes)**

Con Docker Desktop y el emulador arrancados (`emulator -avd suno_api36`), y `SUNO_SERVER_DIR` apuntando al clon del servidor ya en el merge de la Parte A (`git -C C:/Users/gabri/APP/SUNO_server log -1 --format=%H origin/master` igual a `MERGE`; actualizar el clon con `git -C C:/Users/gabri/APP/SUNO_server pull --ff-only` si hace falta):

```bash
SUNO_SERVER_DIR=C:/Users/gabri/APP/SUNO_server bash tool/e2e.sh
```

Expected: `OK (fase0): …` (regresión: el flujo del % sigue igual). Luego, con el ancho lógico del emulador en 390 dp para que las capturas sirvan a la revisión visual:

```bash
adb shell wm size                                   # p. ej. "Physical size: 1080x2400"
adb shell wm density $(( 1080 * 160 / 390 ))        # con el ancho físico real en lugar de 1080
SHOTS=/c/Users/gabri/AppData/Local/Temp/claude/a5dccb9cd35acb45/6922dacb-90a9-4132-980b-586ccfb5f9c2/scratchpad/e2e-4b-shots
E2E_FLOW=quantities E2E_SHOTS="$SHOTS" SUNO_SERVER_DIR=C:/Users/gabri/APP/SUNO_server bash tool/e2e.sh
adb shell wm density reset
```

Expected: `OK (quantities): …`, con las filas 12,5 y 3 m3 en el servidor, la revisión aprobada y la tarea mostrando "validado 12,5 m3"; seis capturas en `$SHOTS` (`captura-cantidades`, `buscador-items`, `captura-no-planeado`, `inicio-sincronizado`, `detalle-registro`, `tarea-validada`).

- [ ] **Step 4: Revisión visual a 390 px**

Abrir cada PNG de `$SHOTS` y revisar: textos cortados o superpuestos (código y descripción largos del ítem, "Planeado … · validado …", el aviso de sobreejecución, el desplegable de ubicación), que nada se salga del ancho, botones "Otra fila de este ítem" / "Quitar fila" legibles, concordancia y tildes ("Cantidades de hoy", "No planeado en esta tarea", "Nota (obligatoria)"), y el bloque de cantidades del detalle. "Para revisar" y "Corregir cantidades" se revisan con las pruebas a 390 px de la Task B11 (sin desbordes) y, si se quiere ver, con una captura en un emulador forzando un rechazo (no hace falta para cerrar la tarea). Corregir lo que aparezca, volver a correr el Step 1 y hacer commit:

```bash
git add -A lib test
git commit -m "fix(ui): quantity capture texts at phone width

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(solo si hubo correcciones).

- [ ] **Step 5: Push y PR**

```bash
git push -u origin feat/costos-cantidades-campo
gh pr create --base master --head feat/costos-cantidades-campo \
  --title "feat(capture): field quantities per item on quantity-measured tasks" \
  --body-file <archivo temporal en el scratchpad con: resumen (captura por cantidades, datos automáticos por fila, ítem no planeado, ubicación, avisos, solo lectura, corrección de rechazos, detalle y exportaciones); Drift 1 → 2 con prueba de migración; contrato copiado del merge (sha) y su SOURCE; pruebas y cobertura; E2E fase0 y quantities; revisión visual a 390 px (capturas); y la línea final "🤖 Generated with [Claude Code](https://claude.com/claude-code)">
```

Vigilar el CI (`mobile.yml`, `contract.yml`, `security.yml`) y corregir lo que falle. **Merge solo con aprobación del usuario.** Después, release normal de ambos repos con release-please (sin forzar actualización: `SCHEMA_VERSION` no cambió).

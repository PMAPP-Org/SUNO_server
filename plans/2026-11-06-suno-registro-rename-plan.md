# SUNO: "visita" pasa a "registro" y `visitacampo` pasa a `suno`, plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el código, la base de datos, la API, la infraestructura y los textos de SUNO dejen de decir `visitacampo` y "visita": el sistema se llama `suno` y el concepto se llama "registro" (`Record`).

**Architecture:** Un script de renombre con reglas explícitas y ordenadas (no versionado) hace el cambio mecánico en cada repo; se revisa a mano, se regeneran los archivos generados (Dart) y se reescriben en el sitio las migraciones del servidor. Una prueba guardiana en cada repo impide que el nombre viejo vuelva. Tres PR: servidor-paquete, servidor-concepto, app.

**Tech Stack:** Python 3.12 (script y guardia del servidor, corridos en Docker), Django 5.2, PostgreSQL 16, Flutter/Dart con `build_runner` (Drift, freezed, json_serializable, l10n).

**Spec:** `plans/2026-11-06-suno-registro-rename-design.md` (aprobada).

**Repos y ramas:**
- Servidor: `PMAPP-Org/SUNO_server`, rama `refactor/suno-registro`, worktree `C:\Users\gabri\APP\wt-server-rename`. PR 1 y PR 2 salen de esta rama en dos tandas (ver Task 6).
- App: `PMAPP-Org/SUNO_client`, rama `refactor/suno-registro`, worktree `C:\Users\gabri\APP\wt-client-rename` (se crea en la Task 9).

---

## Decisiones tomadas al planear

1. **Nombres de las filas del pull.** El pull nombra cada entidad `<Entidad>Record` (`EntityRecord`, `AssignmentRecord`, `FormTemplateVersionRecord`). `VisitRecord` daría `RecordRecord`, así que las tres que chocan pasan a `Row`: `VisitRecord` → `RecordRow`, `VisitAttachmentRecord` → `RecordAttachmentRow`, `VisitQuantityRecord` → `RecordQuantityRow`, `visit_record` → `record_row`, `visit_attachment_record` → `record_attachment_row`. Las demás entidades no cambian.
2. **`audit.record(...)`** (verbo) no cambia: vive en otro espacio de nombres que el modelo `Record`.
3. **Tabla `record` en PostgreSQL.** Se probó en Postgres 16: `CREATE TABLE record`, claves foráneas hacia ella y consultas funcionan (`record` es un pseudotipo de `pg_catalog`, no una palabra reservada para tablas).
4. **`Record` en Dart.** `dart:core` tiene el tipo `Record`; una declaración propia lo oculta sin error dentro de la librería y en las que la importan. Si el analizador marca un choque concreto, se usa un prefijo de importación (`import '...' as domain;`), no otro nombre.
5. **Referencias a documentos históricos.** Los `plans/*.md` históricos no se renombran ni se reescriben; el script protege las rutas `plans/...md` para que un comentario que cite `plans/visitacampo-construccion.md` no pase a apuntar a un archivo inexistente. La única excepción es `plans/visitacampo-progreso.md`, que pasa a `plans/suno-progreso.md` (spec).
6. **`CHANGELOG.md` no se toca**: es historia publicada.
7. **Migraciones del servidor reescritas en el sitio**, incluidos los nombres de archivo (`0007_add_visit_review.py` → `0007_add_record_review.py`) y sus `dependencies`. Todos los ambientes recrean la base.
8. **Historial de Drift reiniciado.** La app nueva (`com.suno.app`) nunca actualiza desde la base vieja: `schemaVersion` vuelve a 1, se borran los pasos de migración 2–9 y el esquema 1 es el actual con los nombres nuevos.
9. **Contrato.** `SCHEMA_VERSION = 2` y `MIN_SUPPORTED_SCHEMA_VERSION = 2` en el servidor; la app manda `schema_version: 2`. El `contract/` de la app se vuelve a copiar del servidor (commit del merge del PR 2).
10. **Textos.** "VisitaCampo" (nombre del producto en textos) pasa a "SUNO". "visita" pasa a "registro" con la concordancia del español (reglas en el script y revisión manual guiada por un grep).

---

## Convenciones del repositorio que hay que respetar

- Servidor: tests con `unittest`/`django.test`, BD vía `<paquete>/core/tests/dbsupport.py`. Nada de `Model.objects` en producción (`scoped_qs`), nada de `assert` (bandit B101) ni `xml.etree`. Ruff a 100 columnas.
- App: `flutter analyze` sin avisos, `flutter test`, y los lint propios de `tool/` (`lint_ui_tokens.sh`, `lint_no_dynamic_code.sh`, `lint_no_tls_bypass.sh`).
- Comentarios y textos de interfaz en español; código en inglés. Commits en inglés, estilo conventional, terminados en `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No hay Python local: el script y la guardia del servidor corren con `docker run --rm -v "$PWD:/w" -w /w python:3.12-slim python ...`. Las pruebas del servidor corren con `infra/scripts/test_docker.sh` (Docker Desktop arrancado).

## Cómo correr las pruebas

```bash
# Servidor (desde la raíz del worktree del servidor)
infra/scripts/test_docker.sh                                   # suite completa
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check . && mypy . && python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh --down                            # al terminar

# App (desde la raíz del worktree de la app)
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
flutter analyze
flutter test
```

Fallas conocidas del servidor, ajenas a este cambio: 4 pruebas de `sync/tests/test_load_script.py` (no hay `/infra/load/push_load.py` dentro del contenedor) y 8 archivos que ya fallan `ruff format --check` (catalog.py, test_context.py, jobs/tests/test_queue.py, test_toctou.py, test_retention.py, jobs/worker.py, healthcheck.py, enroll_admin_totp.py).

---

## Mapa de archivos

| Archivo | Qué hace |
|---|---|
| `.rename/rename.py` (en cada worktree, **ignorado** por git vía `.git/info/exclude`) | Script de renombre, fases `package` y `concept` |
| `infra/scripts/check_naming.py` (servidor, nuevo) | Guardia: falla si queda `visitacampo` o el concepto "visit/visita" fuera de las excepciones |
| `infra/scripts/tests/test_check_naming.py` (servidor, nuevo) | Pruebas de la guardia |
| `.github/workflows/backend.yml` (servidor) | Corre la guardia en CI |
| `tool/check_naming.sh` (app, nuevo) | Guardia de la app |
| `.github/workflows/*.yml` de la app (el que corre `tool/lint_*.sh`) | Corre la guardia de la app |
| Todo el resto | Cambios mecánicos del script más la revisión manual |

---

### Task 0: Preparar el worktree del servidor y medir la línea base

**Files:** ninguno.

- [ ] **Step 1: Confirmar la rama y el estado**

```bash
cd C:/Users/gabri/APP/wt-server-rename
git status --short          # vacío
git log --oneline -2        # 5318041 docs(plan): design ... / c6205bb chore: release 0.13.0
```

- [ ] **Step 2: Correr la suite antes de tocar nada**

```bash
infra/scripts/test_docker.sh
```

Expected: todo pasa salvo las 4 fallas conocidas de `test_load_script.py`. Anotar el número de pruebas que pasan (debe repetirse al final de cada PR).

- [ ] **Step 3: Excluir la carpeta del script**

```bash
echo ".rename/" >> "$(git rev-parse --git-common-dir)/info/exclude"
mkdir -p .rename
```

---

### Task 1: Guardia de nombres del servidor (TDD)

**Files:**
- Create: `infra/scripts/check_naming.py`
- Create: `infra/scripts/tests/test_check_naming.py`

- [ ] **Step 1: Escribir las pruebas**

`infra/scripts/tests/test_check_naming.py`:

```python
"""Pruebas de check_naming.py: el nombre viejo no vuelve al repositorio."""

import importlib.util
import pathlib
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "check_naming", pathlib.Path(__file__).resolve().parents[1] / "check_naming.py"
)
check_naming = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_naming)


class OffendersTests(unittest.TestCase):
    def test_flags_the_old_system_name(self):
        self.assertEqual(check_naming.offenders("import visitacampo.core\n", "package"), ["visitacampo"])
        self.assertEqual(check_naming.offenders("VISITACAMPO_API_IMAGE=x\n", "package"), ["VISITACAMPO"])

    def test_flags_the_old_concept_in_code_and_text(self):
        found = check_naming.offenders("class Visit(Model):\n    visit_id = 1  # la visita\n", "concept")
        self.assertEqual(found, ["Visit", "visit_id", "visita"])

    def test_allows_words_that_only_contain_visit(self):
        text = "visited nodes; revisitar; visitados; visitante\n"
        self.assertEqual(check_naming.offenders(text, "concept"), [])

    def test_package_mode_ignores_the_concept(self):
        self.assertEqual(check_naming.offenders("class Visit: pass\n", "package"), [])

    def test_skips_history(self):
        self.assertTrue(check_naming.skipped("plans/visitacampo-construccion.md"))
        self.assertTrue(check_naming.skipped("CHANGELOG.md"))
        self.assertTrue(check_naming.skipped("plans/suno-progreso.md"))  # historia de avance
        self.assertFalse(check_naming.skipped("server/suno/core/models/record.py"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Correrlas y ver que fallan**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim python -m unittest infra/scripts/tests/test_check_naming.py -v
```

Expected: FAIL (`check_naming.py` no existe).

- [ ] **Step 3: Escribir la guardia**

`infra/scripts/check_naming.py`:

```python
#!/usr/bin/env python3
"""Guardia de nombres: el sistema se llama `suno` y el concepto "registro".

    python infra/scripts/check_naming.py [package|concept]

`package` (PR 1) solo busca `visitacampo`; `concept` (desde el PR 2) busca además
"visit"/"visita" en código, plantillas, contrato e infraestructura. `plans/` (planes y
registro de avance: son historia) y el CHANGELOG no se revisan.
"""

import re
import subprocess
import sys

_PACKAGE = re.compile(r"visitacampo", re.IGNORECASE)
_CONCEPT = re.compile(r"[A-Za-z_]*visit[A-Za-z_]*", re.IGNORECASE)
# Palabras que contienen "visit" pero no son el concepto.
_ALLOWED = {"visited", "revisit", "revisited", "revisitar", "visitados", "visitante", "visitantes"}
_BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".xlsx", ".mbtiles", ".woff", ".woff2", ".ttf")


def skipped(path: str) -> bool:
    return path == "CHANGELOG.md" or path.endswith(_BINARY) or path.startswith("plans/")


def offenders(text: str, mode: str) -> list[str]:
    found = [m.group(0) for m in _PACKAGE.finditer(text)]
    if mode == "concept":
        found += [
            word
            for word in (m.group(0) for m in _CONCEPT.finditer(text))
            if not _PACKAGE.search(word) and word.lower() not in _ALLOWED
        ]
        found = [w for w in re.findall(r"[A-Za-z_]+", text) if w in set(found)]
    return found


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "concept"
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    bad = 0
    for path in files:
        if skipped(path):
            continue
        found = offenders(path + "\n", mode)
        try:
            with open(path, encoding="utf-8") as handle:
                found += offenders(handle.read(), mode)
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if found:
            bad += 1
            print(f"{path}: {', '.join(sorted(set(found)))}")
    if bad:
        print(f"\n{bad} archivo(s) con el nombre viejo. Ver plans/2026-11-06-suno-registro-rename-design.md.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

Notas: la spec y este plan quedan fuera por estar en `plans/`. El registro de avance también: sus entradas viejas hablan de "visitas" y son historia; la sección nueva (Task 7) ya usa los nombres nuevos. `offenders` devuelve las palabras en el orden del texto (lo usa la prueba de `concept`).

- [ ] **Step 4: Correr las pruebas**

Mismo comando del Step 2. Expected: 5 pruebas en verde.

- [ ] **Step 5: Ver que hoy falla en el repo (esperado)**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null && git config --global --add safe.directory /w && python infra/scripts/check_naming.py package | tail -3"
```

Expected: exit 1 con cientos de archivos. Se usará como lista de trabajo.

- [ ] **Step 6: Commit**

```bash
git add infra/scripts/check_naming.py infra/scripts/tests/test_check_naming.py
git commit -m "ci: add a naming guard for the suno and record rename"
```

---

### Task 2: El script de renombre

**Files:** Create `.rename/rename.py` (no versionado). El mismo archivo se copia luego al worktree de la app.

- [ ] **Step 1: Escribir el script**

```python
#!/usr/bin/env python3
"""Renombre visitacampo -> suno y visita -> registro. Herramienta de trabajo (no versionada).

    python .rename/rename.py package [--dry-run]
    python .rename/rename.py concept [--dry-run]

Reescribe el contenido de los archivos versionados y renombra con `git mv` las rutas
que contienen el nombre viejo. Respeta los documentos históricos y el CHANGELOG.
"""

import re
import subprocess
import sys

SKIP_PREFIXES = ("plans/",)
SKIP_FILES = {"CHANGELOG.md", ".rename/rename.py"}
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".xlsx", ".mbtiles", ".woff", ".woff2",
          ".ttf", ".jar", ".keystore", ".lock")
GENERATED = (".g.dart", ".freezed.dart")  # se regeneran con build_runner
ALLOWED = {"visited", "revisit", "revisited", "revisitar", "visitados", "visitante", "visitantes"}

# Rutas a documentos históricos: se protegen para no apuntar a archivos inexistentes.
PLAN_REF = re.compile(r"plans/(?!visitacampo-progreso\.md)[\w./-]+\.md")

PACKAGE_RULES = [
    ("plans/visitacampo-progreso.md", "plans/suno-progreso.md"),
    ("com.visitacampo.visitacampo", "com.suno.app"),
    ("com/visitacampo/visitacampo", "com/suno/app"),
    ("VisitaCampoOTPAdminSite", "SunoOTPAdminSite"),
    ("VisitaCampoConfig", "SunoConfig"),
    ("VisitaCampo", "SUNO"),
    ("VISITACAMPO", "SUNO"),
    ("Visitacampo", "Suno"),
    ("visitacampo", "suno"),
]

# Frases en español con concordancia: se aplican antes que la regla simple.
SPANISH_RULES = [
    (r"\b(de|De) la visita\b", r"\1l registro"),
    (r"\b(a|A) la visita\b", r"\1l registro"),
    (r"\bla visita\b", "el registro"), (r"\bLa visita\b", "El registro"),
    (r"\blas visitas\b", "los registros"), (r"\bLas visitas\b", "Los registros"),
    (r"\buna visita\b", "un registro"), (r"\bUna visita\b", "Un registro"),
    (r"\besta visita\b", "este registro"), (r"\bEsta visita\b", "Este registro"),
    (r"\bestas visitas\b", "estos registros"), (r"\bEstas visitas\b", "Estos registros"),
    (r"\besa visita\b", "ese registro"), (r"\bEsa visita\b", "Ese registro"),
    (r"\botra visita\b", "otro registro"), (r"\botras visitas\b", "otros registros"),
    (r"\bnueva visita\b", "nuevo registro"), (r"\bNueva visita\b", "Nuevo registro"),
    (r"\bnuevas visitas\b", "nuevos registros"),
    (r"\bninguna visita\b", "ningún registro"), (r"\bNinguna visita\b", "Ningún registro"),
    (r"\balguna visita\b", "algún registro"),
    (r"\bmisma visita\b", "mismo registro"),
    (r"\búltima visita\b", "último registro"), (r"\búltimas visitas\b", "últimos registros"),
    (r"\bprimera visita\b", "primer registro"),
    (r"\bvisitas\b", "registros"), (r"\bVisitas\b", "Registros"),
    (r"\bvisita\b", "registro"), (r"\bVisita\b", "Registro"),
    # Adjetivos que venían concordando con "visita": van al final, sobre "registro".
    (r"\b(registro) (validada|rechazada|eliminada|borrada|aprobada|nueva|vieja|propia|ajena)\b",
     lambda m: f"{m.group(1)} {_masc(m.group(2))}"),
    (r"\b(registros) (validadas|rechazadas|eliminadas|borradas|aprobadas|nuevas|viejas|propias|ajenas)\b",
     lambda m: f"{m.group(1)} {_masc(m.group(2))}"),
]

# Identificadores: primero los nombres que chocan con `*Record` del pull.
CONCEPT_SPECIALS = [
    ("VisitAttachmentRecord", "RecordAttachmentRow"),
    ("VisitQuantityRecord", "RecordQuantityRow"),
    ("VisitRecord", "RecordRow"),
    ("visit_attachment_record", "record_attachment_row"),
    ("visit_record", "record_row"),
]
CONCEPT_PARTS = [("VISIT", "RECORD"), ("Visit", "Record"), ("visit", "record")]
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _masc(word: str) -> str:
    """'validada' -> 'validado', 'validadas' -> 'validados', 'nueva' -> 'nuevo'."""
    if word.endswith("as"):
        return word[:-2] + "os"
    if word.endswith("a"):
        return word[:-1] + "o"
    return word


def _ident(word: str) -> str:
    if "visit" not in word.lower() or word.lower() in ALLOWED or re.fullmatch(r"[Vv]isitas?", word):
        return word
    for old, new in CONCEPT_SPECIALS:
        word = word.replace(old, new)
    for old, new in CONCEPT_PARTS:
        word = word.replace(old, new)
    return word


def _protect(text: str) -> tuple[str, list[str]]:
    kept: list[str] = []

    def hold(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    return PLAN_REF.sub(hold, text), kept


def _restore(text: str, kept: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def transform(text: str, phase: str) -> str:
    text, kept = _protect(text)
    if phase == "package":
        for old, new in PACKAGE_RULES:
            text = text.replace(old, new)
    else:
        for pattern, repl in SPANISH_RULES:
            text = re.sub(pattern, repl, text)
        text = IDENT.sub(lambda m: _ident(m.group(0)), text)
    return _restore(text, kept)


def transform_path(path: str, phase: str) -> str:
    if phase == "package":
        for old, new in PACKAGE_RULES:
            path = path.replace(old, new)
        return path
    return "/".join(IDENT.sub(lambda m: _ident(m.group(0)), part) for part in path.split("/"))


def main() -> int:
    phase = sys.argv[1]
    dry = "--dry-run" in sys.argv
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    changed = moved = 0
    for path in files:
        if path in SKIP_FILES or path.startswith(SKIP_PREFIXES) and path != "plans/visitacampo-progreso.md":
            continue
        if not path.endswith(BINARY + GENERATED):
            try:
                with open(path, encoding="utf-8", newline="") as handle:
                    before = handle.read()
            except (UnicodeDecodeError, FileNotFoundError):
                before = None
            if before is not None:
                after = transform(before, phase)
                if after != before:
                    changed += 1
                    if not dry:
                        with open(path, "w", encoding="utf-8", newline="") as handle:
                            handle.write(after)
        target = transform_path(path, phase)
        if target != path:
            moved += 1
            print(f"mv {path} -> {target}")
            if not dry:
                subprocess.run(["mkdir", "-p", target.rsplit("/", 1)[0]], check=True)
                subprocess.run(["git", "mv", path, target], check=True)
    print(f"{changed} archivo(s) reescritos, {moved} movidos ({'simulación' if dry else 'aplicado'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Notas:
- `transform_path` se aplica a cada archivo; `git mv` crea la carpeta destino. Las carpetas vacías que quedan atrás desaparecen solas en git.
- Los archivos generados de Dart (`*.g.dart`, `*.freezed.dart`) no se reescriben: se regeneran (Task 10). Se mueven igual si su ruta cambia, y `build_runner` los reemplaza.

- [ ] **Step 2: Probar el script en seco sobre frases conocidas**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim python - <<'EOF'
import importlib.util
spec = importlib.util.spec_from_file_location("r", ".rename/rename.py"); r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
cases = {
    ("from visitacampo.core import x", "package"): "from suno.core import x",
    ("VISITACAMPO_API_IMAGE", "package"): "SUNO_API_IMAGE",
    ("ver plans/visitacampo-construccion.md", "package"): "ver plans/visitacampo-construccion.md",
    ("ver plans/visitacampo-progreso.md", "package"): "ver plans/suno-progreso.md",
    ("class VisitRecord(EntityRecord)", "concept"): "class RecordRow(EntityRecord)",
    ("visit_id = make_visit()", "concept"): "record_id = make_record()",
    ("La visita validada de la visita", "concept"): "El registro validado del registro",
    ("las visitas rechazadas", "concept"): "los registros rechazados",
    ("nodes visited; revisitar", "concept"): "nodes visited; revisitar",
    ("homeVisitUntitled", "concept"): "homeRecordUntitled",
}
bad = [(k, r.transform(k[0], k[1]), v) for k, v in cases.items() if r.transform(k[0], k[1]) != v]
print("OK" if not bad else bad)
EOF
```

Expected: `OK`. Si algo falla, corregir la regla antes de seguir.

---

### Task 3: PR 1, servidor: `visitacampo` pasa a `suno`

**Files:** todos los que lista `check_naming.py package` (paquete `server/visitacampo/` entero, `server/config/`, `server/pyproject.toml`, `server/manage.py`, `server/Dockerfile`, `infra/**`, `.github/**`, `.gitleaksignore`, `CODEOWNERS`, `docs/**`, `README.md`, `CONTRIBUTING.md`, `plans/visitacampo-progreso.md`).

- [ ] **Step 1: Simular**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null && git config --global --add safe.directory /w && python .rename/rename.py package --dry-run | tail -15"
```

Expected: cientos de archivos reescritos; los `mv` incluyen `server/visitacampo/... -> server/suno/...`, `docs/0*-...-visitacampo.md -> ...-suno.md`, `infra/monitoring/grafana/dashboards/visitacampo-salud.json -> suno-salud.json` y `plans/visitacampo-progreso.md -> plans/suno-progreso.md`.

- [ ] **Step 2: Aplicar** (mismo comando sin `--dry-run`).

- [ ] **Step 3: Revisar a mano los puntos delicados**

```bash
git diff --stat | tail -3
git diff server/suno/apps.py server/config/settings/base.py server/pyproject.toml infra/.env.example infra/docker-compose.yml infra/docker-compose.prod.yml infra/scripts/test_docker.sh
```

Revisar:
- `server/suno/apps.py`: `class SunoConfig`, `name = "suno"`, `verbose_name = "SUNO"`; docstring con sentido ("App principal de SUNO").
- `settings/base.py`: `INSTALLED_APPS` con `suno.apps.SunoConfig`, `suno.core.apps.CoreConfig`, etc.; el logger y los formatters (`suno.core.scrub...`).
- `pyproject.toml`: `name = "suno-server"`, `known-first-party = ["config", "suno"]`, `source = ["suno", "config"]`.
- `.env.example`: `POSTGRES_DB=suno`, `POSTGRES_USER=suno`, buckets `suno-dev`, `SUNO_API_IMAGE`; que `infra/scripts/check_envvars.sh` siga verde (Step 5).
- `backend.yml`: `bandit -q -r server/suno server/config ...`, imagen `suno-api:ci`, `SUNO_REQUIRE_DB`.
- Métricas `suno_backup_last_success_timestamp_seconds` / `suno_backup_last_size_bytes` iguales en `infra/backup/backup.sh` y `infra/monitoring/prometheus/alerts.yml`; el dashboard `uid` `suno-salud` igual en el JSON y en `monitoring.yml`.
- Frases en documentos: "VisitaCampo" pasó a "SUNO"; corregir a mano las que queden raras ("el SUNO", "la SUNO").

- [ ] **Step 4: Ruff, mypy, migraciones y suite**

```bash
infra/scripts/test_docker.sh --down     # la BD vieja se llamaba visitacampo
infra/scripts/test_docker.sh --cmd "ruff check . && mypy . && python manage.py makemigrations --check --dry-run"
infra/scripts/test_docker.sh
```

Expected: ruff y mypy limpios, sin migraciones nuevas, la suite con el mismo número de pruebas que en la Task 0 (las 5 de la guardia corren aparte). La app raíz `SunoConfig` cambia de etiqueta (`visitacampo` → `suno`); no tiene modelos, así que no genera migraciones. Si `makemigrations` propone algo, revisar qué app perdió su `label` explícito (`core`, `authn`, … deben conservarlo).

- [ ] **Step 5: Guardia, lint de infraestructura y scripts**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null && git config --global --add safe.directory /w && python infra/scripts/check_naming.py package && python -m unittest infra/scripts/tests/test_check_naming.py"
bash infra/scripts/check_envvars.sh infra/.env.example infra/.env.example
docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:stable infra/provision/*.sh infra/scripts/*.sh infra/backup/*.sh
```

Expected: la guardia sale con 0; `check_envvars` y shellcheck limpios.

- [ ] **Step 6: Correr la guardia en CI**

En `.github/workflows/backend.yml`, en el job que ya corre `python infra/scripts/check_findings_map.py --repo server`, añadir después de ese paso:

```yaml
      - name: Nombres (suno / registro)
        run: |
          python -m unittest infra/scripts/tests/test_check_naming.py
          python infra/scripts/check_naming.py package
```

(El modo pasa a `concept` en la Task 8.)

- [ ] **Step 7: Construir la imagen**

```bash
docker build -t suno-api:local server/
```

Expected: la imagen construye (el Dockerfile copia el paquete nuevo).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor!: rename the visitacampo package and infrastructure to suno"
```

(El `!` marca el cambio incompatible para release-please.)

---

### Task 4: PR 2, servidor: `Visit` pasa a `Record` (aplicar el script)

**Files:** todo lo que lista `check_naming.py concept`.

- [ ] **Step 1: Simular y revisar los movimientos**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null && git config --global --add safe.directory /w && python .rename/rename.py concept --dry-run | grep '^mv' "
```

Expected (entre otros): `server/suno/core/models/visit.py -> record.py`, `server/suno/backoffice/visit_pdf.py -> record_pdf.py`, `core/migrations/0002_add_visit_attachment_org_isolation_fk.py -> 0002_add_record_attachment_org_isolation_fk.py`, `0003_add_visit_float_finiteness_checks.py`, `0007_add_visit_review.py`, `0012_add_visit_task_progress.py` (con `record`), `sync/tests/test_visit_ownership.py -> test_record_ownership.py`.

- [ ] **Step 2: Aplicar** (sin `--dry-run`).

- [ ] **Step 3: Revisar el SQL crudo y los nombres de restricciones**

```bash
git diff server/suno/sync/sql.py server/suno/sync/services.py server/suno/sync/records.py server/suno/sync/schemas.py | head -300
grep -rn "db_table\|name=\"record" server/suno/core/models/record.py | head -20
```

Revisar:
- `sync/sql.py`: `INSERT INTO record (...)`, `UPDATE record SET ...`, `record_id`; los nombres de restricción que el push traduce a motivos de rechazo (`record_task_progress_pair`, `record_lat_range`, …) coinciden con los de `core/models/record.py` y las migraciones.
- `sync/schemas.py`: `entity: Literal["record"] = "record"`; `sync/services.py`: `op.entity == "record"`; `sync/records.py`: el mapa de constructores usa la clave `"record": record_row`.
- `core/models/record.py`: `class Record(SyncedModel)`, `db_table = "record"`, `RecordAttachment` → `record_attachment`, `RecordReview` → `record_review`.

- [ ] **Step 4: Reescribir la explicación de las migraciones**

Las migraciones quedan con los nombres nuevos por el script. Comprobar que son coherentes y que no proponen cambios:

```bash
infra/scripts/test_docker.sh --down
infra/scripts/test_docker.sh --cmd "python manage.py makemigrations --check --dry-run && python manage.py migrate -v 0 && python manage.py migrate core 0013 -v 0 && python manage.py migrate -v 0"
```

Expected: sin migraciones nuevas; la base se crea con tablas `record`, `record_attachment`, `record_review`; ida y vuelta de 0014 funciona.

- [ ] **Step 5: Commit intermedio** (para poder revisar los pasos siguientes por separado)

```bash
git add -A
git commit -m "refactor!: rename the visit concept to record across the server"
```

---

### Task 5: PR 2, servidor: contrato, permisos, auditoría y textos

**Files:**
- Modify: `server/suno/serverinfo/capabilities.py`
- Modify: `server/openapi/v1.yaml`
- Modify: textos visibles en `server/suno/**/templates/**`, `server/suno/core/admin.py`, `server/suno/backoffice/**` (lo que haya dejado el script)
- Test: `server/suno/serverinfo/tests/` y `server/suno/sync/tests/` (los que comparan la versión del esquema)

- [ ] **Step 1: Prueba de la versión del esquema**

Buscar la prueba que fija la versión (`grep -rn "SCHEMA_VERSION" server/suno/serverinfo/tests server/suno/sync/tests`). Cambiar la expectativa a 2 y añadir:

```python
    def test_schema_1_clients_are_no_longer_supported(self):
        self.assertEqual(capabilities.SCHEMA_VERSION, 2)
        self.assertEqual(capabilities.MIN_SUPPORTED_SCHEMA_VERSION, 2)
```

en la clase de pruebas de `capabilities` que ya exista (con su import tal como está en ese archivo). Correr y ver que falla:

```bash
infra/scripts/test_docker.sh suno/serverinfo
```

- [ ] **Step 2: Subir la versión**

En `server/suno/serverinfo/capabilities.py`:

```python
SCHEMA_VERSION = 2
```

y

```python
#: El contrato 1 hablaba de "visitas"; el 2 de "registros". Sin producción no hay
#: período de convivencia: la app vieja deja de sincronizar.
MIN_SUPPORTED_SCHEMA_VERSION = 2
```

(reemplazando el comentario anterior sobre "no hay ninguna anterior"). Ajustar `sync/tests/factories.py` si fija `schema_version` a 1 en algún caso, y las pruebas que construyen un cliente "viejo" con `schema_version=1` para que ahora esperen el rechazo.

- [ ] **Step 3: Contrato**

En `server/openapi/v1.yaml`, después del script:
- `info.version` y cualquier `schema_version` de ejemplo pasan a 2.
- Los esquemas `Record`, `RecordPayload`, `RecordRow`, `RecordAttachmentRow`, `RecordQuantityRow`, el discriminador `record: '#/components/schemas/RecordRow'` y las rutas con `records` existen y no queda ningún `$ref` roto:

```bash
infra/scripts/test_docker.sh --cmd "python -c \"from openapi_spec_validator import validate; from openapi_spec_validator.readers import read_from_filename; validate(read_from_filename('openapi/v1.yaml')[0]); print('ok')\""
```

Expected: `ok`. Las descripciones en español quedan con "registro".

- [ ] **Step 4: Permisos y auditoría**

```bash
grep -rn "view_record\|change_record\|record\.\(created\|updated\|deleted\|reviewed\)" server/suno --include=*.py | head -20
```

Comprobar que los códigos de auditoría (`AuditAction`) dicen `record.*` y que los permisos del Admin y de las plantillas (`perms.core.view_record`) coinciden con el `codename` que Django deriva del modelo `Record` (`view_record`).

- [ ] **Step 5: Textos visibles y concordancia**

Buscar concordancias que el script no pudo resolver:

```bash
grep -rnE "\b(la|las|una|unas|esta|estas|esa|esas|otra|otras|nueva|nuevas|ninguna|alguna|misma|última|primera|cada una)\s+(\w+\s+)?registros?\b|\bregistros?\s+\w+(ada|adas|ida|idas)\b" server/suno server/openapi docs README.md CONTRIBUTING.md plans/suno-progreso.md
```

Corregir cada caso a mano (por ejemplo "la última registro" → "el último registro", "registro guardada" → "registro guardado"). Repetir el grep hasta que solo queden falsos positivos (un "registro" que no es este concepto, como "registro de auditoría").

- [ ] **Step 6: Suite completa y calidad**

```bash
infra/scripts/test_docker.sh
infra/scripts/test_docker.sh --cmd "ruff check . && ruff format --check suno config && mypy . && python manage.py makemigrations --check --dry-run"
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip -q install bandit && bandit -q -r server/suno server/config -x '*/tests/*,*/migrations/*'"
```

Expected: el mismo número de pruebas que en la Task 0 más la nueva de la Task 5 Step 1; ruff (salvo los 8 conocidos, ahora bajo `suno/`), mypy, migraciones y bandit limpios.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(sync)!: contract schema 2 speaks of records"
```

---

### Task 6: PR 1 y PR 2: abrir los PR del servidor

El PR 1 es el commit de la Task 3 (más la guardia de la Task 1); el PR 2, los de las Tasks 4–5 y 7–8.

- [ ] **Step 1: Rama del PR 1**

```bash
git branch refactor/suno-package <sha-del-commit-de-la-Task-3>
git push -u origin refactor/suno-package
gh pr create --base master --head refactor/suno-package --title "refactor!: rename the visitacampo package and infrastructure to suno" --body-file <(cat <<'EOF'
## Resumen
Primer PR del renombre (spec `plans/2026-11-06-suno-registro-rename-design.md`): el paquete `visitacampo` y toda la infraestructura pasan a `suno`. Sin cambios de concepto.

## Cambios
- `server/visitacampo/` → `server/suno/`, imports, settings, `pyproject.toml`.
- Docker, compose, provisión, despliegue, respaldos, monitoreo, workflows: `suno-api`, `SUNO_*`, base `suno`, métricas `suno_backup_*`, dashboard `suno-salud`.
- Docs vivos; `plans/visitacampo-progreso.md` → `plans/suno-progreso.md`.
- Guardia `infra/scripts/check_naming.py` en CI.

## Pruebas
Suite completa, ruff, mypy, `makemigrations --check`, bandit, shellcheck, `check_envvars.sh`, build de la imagen.

No hay producción: los ambientes se recrean.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)
```

- [ ] **Step 2: Vigilar CI** con `gh pr checks <n> --watch` y corregir lo que falle (commit y push en `refactor/suno-package`). Pedir al usuario permiso antes de hacer merge.
- [ ] **Step 3:** Tras el merge del PR 1, rebasar `refactor/suno-registro` sobre `master` (`git rebase origin/master`), comprobar que la suite sigue verde y seguir con la Task 7.

---

### Task 7: PR 2, servidor: documentos vivos y registro de avance

**Files:** `docs/**`, `README.md`, `CONTRIBUTING.md`, `plans/suno-progreso.md`.

- [ ] **Step 1:** Leer los diffs de `docs/` y del registro de avance, y corregir frases que quedaron forzadas (el script cambia palabras, no redacta). En `plans/suno-progreso.md`, añadir al final una sección "Renombre: SUNO y registros" con la fecha, los PR, la decisión de reescribir migraciones, el esquema 2 y la nota de que los documentos de `plans/` anteriores a este cambio conservan "visita" y "visitacampo".
- [ ] **Step 2: Commit**

```bash
git add docs README.md CONTRIBUTING.md plans/suno-progreso.md
git commit -m "docs: speak of records and SUNO in the living documents"
```

---

### Task 8: PR 2, servidor: guardia en modo `concept`, verificación y PR

- [ ] **Step 1:** En `.github/workflows/backend.yml` cambiar `check_naming.py package` por `check_naming.py concept`, y correrla localmente (comando de la Task 3 Step 5 con `concept`). Expected: 0 archivos. Lo que aparezca se corrige o, si es una palabra legítima que contiene "visit", se añade a `_ALLOWED` en `check_naming.py` **y** en `ALLOWED` del script, con su prueba en `test_check_naming.py`.
- [ ] **Step 2:** Suite, calidad y bandit (comandos de la Task 5 Step 6). Expected: verde.
- [ ] **Step 3: Revisión visual** de las páginas web a 1440 px y 390 px (lista de visitas, ahora registros, revisión, Admin del registro, exportaciones, ficha de tarea con el historial de registros validados): redacción, concordancia y textos cortados. Corregir y hacer commit.
- [ ] **Step 4: Commit y PR**

```bash
git add -A
git commit -m "ci: guard the record concept name"
git push -u origin refactor/suno-registro
gh pr create --base master --head refactor/suno-registro --title "refactor!: rename visits to records across the server" --body-file <cuerpo con resumen, cambios por área, contrato esquema 2, pruebas, revisión visual y la línea final de Claude Code>
```

No hacer merge todavía: el PR 2 se libera junto con la app (Task 14).

---

### Task 9: App: worktree y línea base

- [ ] **Step 1:**

```bash
cd C:/Users/gabri/APP/SUNO_client
git fetch -q && git worktree add -b refactor/suno-registro ../wt-client-rename origin/master
cd ../wt-client-rename
echo ".rename/" >> "$(git rev-parse --git-common-dir)/info/exclude"
mkdir -p .rename && cp ../wt-server-rename/.rename/rename.py .rename/
flutter pub get && flutter analyze && flutter test
```

Expected: analyze sin avisos y pruebas en verde. Anotar el número de pruebas.

---

### Task 10: App: paquete, id y concepto

**Files:** todo el repo salvo `CHANGELOG.md`, `build/`, `.dart_tool/`, `coverage/` (estos no están versionados o los salta el script) y los generados (`*.g.dart`, `*.freezed.dart`), que se regeneran.

- [ ] **Step 1: Fase `package`**

```bash
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null && git config --global --add safe.directory /w && python .rename/rename.py package"
```

Revisar:
- `pubspec.yaml`: `name: suno`; todos los `import 'package:visitacampo/...'` pasan a `package:suno/...`.
- `android/app/build.gradle*`: `namespace` y `applicationId` = `com.suno.app`; `MainActivity.kt` movido a `android/app/src/main/kotlin/com/suno/app/` con `package com.suno.app`.
- `ios/Runner.xcodeproj/project.pbxproj`: `PRODUCT_BUNDLE_IDENTIFIER = com.suno.app;` y `com.suno.app.RunnerTests;`.
- Las variables `--dart-define` (`SUNO_API_BASE_URL`, `SUNO_SPKI_PINS`, `SUNO_SPKI_PINS_EXPIRE`) cambiadas igual en `README.md`, los scripts de `tool/` y los workflows.
- El nombre visible de la app (`android:label`, `CFBundleDisplayName`) es "SUNO".

- [ ] **Step 2: Fase `concept`** (mismo comando con `concept`). Revisar los `mv` (`lib/core/domain/visit.dart -> record.dart`, `lib/features/visits/ -> lib/features/records/`, pruebas equivalentes) y que las claves de los ARB (`homeVisitUntitled` → `homeRecordUntitled`, …) cambiaron igual en todos los `.arb`.

- [ ] **Step 3: Reiniciar el historial de Drift**

En `lib/data/local/database.dart`:
- `int get schemaVersion => 1;`
- Quitar los pasos `from < 2 … from < 9` de `onUpgrade` (dejar `onCreate: (m) => m.createAll()` y un `onUpgrade` vacío o eliminarlo según cómo esté escrito), y el comentario que lo explique: "La app `com.suno.app` empieza en el esquema 1: ningún teléfono actualiza desde la base de `com.visitacampo.visitacampo`."
- En `lib/data/local/tables.dart`, quitar las menciones "añadida en `schemaVersion` N" de los comentarios (ya no son ciertas).
- Borrar las pruebas de migración de Drift que suben desde versiones viejas (`test/data/local/*migration*`, si existen) y los esquemas exportados de `drift_schemas/` si el repo los guarda.

- [ ] **Step 4: Esquema 2 del contrato**

En `lib/core/sync/sync_engine.dart`: `schemaVersion: 2,`. Copiar el contrato del servidor desde la rama del PR 2:

```bash
cp ../wt-server-rename/server/openapi/v1.yaml contract/v1.yaml
git -C ../wt-server-rename rev-parse HEAD   # anotar el sha
```

y poner ese sha en `contract/SOURCE` (`ref=`). Actualizar a mano los clientes HTTP de `lib/data/http/` y las claves JSON de sync que leen del contrato (`record_id`, `records`, `entity: 'record'`, `record_validated`) si el script no las cubrió por ser cadenas con otro formato.

- [ ] **Step 5: Regenerar**

```bash
dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
flutter analyze
```

Expected: sin avisos. Si `Record` choca con `dart:core` en algún archivo, usar un prefijo de importación (decisión 4).

- [ ] **Step 6: Pruebas**

```bash
flutter test
```

Expected: el mismo número de pruebas que en la Task 9 menos las de migración de Drift borradas en el Step 3.

- [ ] **Step 7: Concordancia en l10n**

```bash
grep -nE "\b(la|las|una|esta|estas|esa|otra|nueva|ninguna|alguna|misma|última|primera)\s+(\w+\s+)?registros?\b|\bregistros?\s+\w+(ada|adas|ida|idas)\b" lib/l10n/*.arb
```

Corregir a mano hasta que no quede nada.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor!: rename the app to SUNO (com.suno.app) and visits to records"
```

---

### Task 11: App: guardia de nombres

**Files:**
- Create: `tool/check_naming.sh`
- Modify: el workflow de la app que corre `tool/lint_ui_tokens.sh` (buscar con `grep -rn lint_ui_tokens .github/workflows`)

- [ ] **Step 1: Escribir la guardia**

```bash
#!/usr/bin/env bash
# Guardia de nombres: la app se llama SUNO y el concepto "registro".
# Falla si aparece `visitacampo` o "visit"/"visita" fuera de las excepciones.
set -euo pipefail
cd "$(dirname "$0")/.."
allowed='^(visited|revisit|revisited|revisitar|visitados|visitante|visitantes)$'
status=0
while IFS= read -r file; do
	case "$file" in CHANGELOG.md | plans/* | *.png | *.jpg | *.ttf | *.mbtiles) continue ;; esac
	words="$( { printf '%s\n' "$file"; cat "$file"; } | grep -oiE '[a-z_]*visit[a-z_]*' | grep -viE "$allowed" | sort -u || true)"
	if [ -n "$words" ]; then
		echo "$file: $(echo "$words" | tr '\n' ' ')"
		status=1
	fi
done < <(git ls-files)
exit "$status"
```

- [ ] **Step 2: Correrla**

```bash
bash tool/check_naming.sh
```

Expected: exit 0. Lo que aparezca se corrige (o se añade a `allowed` si es legítimo).

- [ ] **Step 3: Probar que falla** creando un archivo temporal con `visita`, correr la guardia (exit 1), borrarlo.

- [ ] **Step 4: Añadirla a CI**, en el mismo job y con el mismo formato que `tool/lint_ui_tokens.sh`:

```yaml
      - name: Nombres (SUNO / registro)
        run: bash tool/check_naming.sh
```

- [ ] **Step 5: Commit**

```bash
git add tool/check_naming.sh .github/workflows
git commit -m "ci: guard the SUNO and record names in the app"
```

---

### Task 12: Sincronización de punta a punta y revisión visual de la app

- [ ] **Step 1:** Levantar el servidor de la rama del PR 2 y correr la prueba de punta a punta de la app según `docs/testing/e2e-fase0.md` (y `tool/e2e.sh` si aplica): iniciar sesión, crear un registro con una tarea y una foto, sincronizar, revisarlo y validarlo en la web, y comprobar que la app lo muestra validado después del pull.
- [ ] **Step 2:** Revisión visual de la app a 390 px (teléfono) en: inicio ("Nuevo registro", "Registros recientes"), captura, detalle del registro, cuarentena y corrección, exportación PDF. Buscar redacción, concordancia y textos cortados. Corregir y hacer commit (`fix(ui): ...`).

---

### Task 13: PR 3, app

- [ ] **Step 1:**

```bash
git push -u origin refactor/suno-registro
gh pr create --base master --head refactor/suno-registro --title "refactor!: SUNO app (com.suno.app) speaks of records" --body-file <cuerpo con resumen, id nuevo de la app, reinicio de Drift, contrato esquema 2 y su SOURCE, guardia, pruebas, e2e, revisión visual, y la línea final de Claude Code>
```

- [ ] **Step 2:** Vigilar CI y corregir lo que falle.

---

### Task 14: Liberar juntos

- [ ] **Step 1:** Con los PR 2 (servidor) y 3 (app) en verde, **pedir permiso al usuario** para hacer merge.
- [ ] **Step 2:** Merge del PR 2 y del PR 3. Merge de los PR de release-please: servidor 0.14.0, app 0.12.0 (si release-please propone otra versión por el `!`, avisar al usuario antes de cambiarla).
- [ ] **Step 3:** Recrear ambientes locales: `infra/scripts/test_docker.sh --down`, borrar los volúmenes de compose del proyecto viejo (`docker volume ls | grep visitacampo`), desinstalar la app vieja de los teléfonos de prueba.
- [ ] **Step 4:** Limpieza: quitar los worktrees `wt-server-rename` y `wt-client-rename`, y las ramas locales ya integradas (pedir permiso para `git branch -D` si git no las reconoce como integradas por el squash).
- [ ] **Step 5:** Actualizar la memoria `costos-4b-decisiones` (los nombres nuevos ya existen) y retomar el diseño de la etapa 4b.

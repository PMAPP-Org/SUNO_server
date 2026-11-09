#!/usr/bin/env python3
"""Guardia de nombres: el sistema se llama `suno` y el concepto "registro".

    python infra/scripts/check_naming.py [package|concept]

`package` (PR 1) solo busca `visitacampo`; `concept` (desde el PR 2) busca además
"visit"/"visita" en código, plantillas, contrato e infraestructura. No se revisan
`plans/` (planes y registro de avance: son historia), el CHANGELOG, `.gitleaksignore` ni
la propia guardia (que nombra lo que busca). Las citas a documentos de `plans/`
(`plans/visitacampo-....md`) se permiten si el archivo citado existe: esos documentos
conservan su nombre. Corre siempre desde la raíz del repositorio.
"""

import os
import re
import subprocess
import sys

_PACKAGE = re.compile(r"visitacampo", re.IGNORECASE)
# Citas a documentos de plans/: se permiten solo si apuntan a un archivo versionado
# (el registro de avance, por ejemplo, ya no se llama plans/visitacampo-progreso.md).
_PLAN_REF = re.compile(r"plans/[\w./-]+\.md")
_WORD = re.compile(r"[A-Za-z_]+")
# Palabras que contienen "visit" pero no son el concepto.
_ALLOWED = {
    "visited",
    "revisit",
    "revisited",
    "revisitar",
    "visitados",
    "visitante",
    "visitantes",
}
_BINARY = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".xlsx",
    ".mbtiles",
    ".woff",
    ".woff2",
    ".ttf",
)
_SELF = {"infra/scripts/check_naming.py", "infra/scripts/tests/test_check_naming.py"}
# Historia que cita los nombres de entonces: el CHANGELOG publicado y las huellas de
# gitleaks (`commit:ruta:regla:línea`, con la ruta que tenía el archivo en ese commit).
_HISTORY = {"CHANGELOG.md", ".gitleaksignore"}
# Migraciones ya aplicadas en producción (no se reescriben) y la que hace el renombre: es
# la historia de la base de datos, y cada ruta se lista una por una a propósito.
_MIGRATIONS_HISTORY = {
    "server/suno/authn/migrations/0001_initial.py",
    "server/suno/core/migrations/0001_initial.py",
    "server/suno/core/migrations/0002_add_visit_attachment_org_isolation_fk.py",
    "server/suno/core/migrations/0003_add_visit_float_finiteness_checks.py",
    "server/suno/core/migrations/0004_add_sync_cursor_floor.py",
    "server/suno/core/migrations/0005_add_job_idempotency_key.py",
    "server/suno/core/migrations/0006_add_rate_limit_bucket.py",
    "server/suno/core/migrations/0007_add_visit_review.py",
    "server/suno/core/migrations/0008_add_privacy_consent.py",
    "server/suno/core/migrations/0009_add_basemap_package.py",
    "server/suno/core/migrations/0010_add_reference_layer.py",
    "server/suno/core/migrations/0011_add_projects.py",
    "server/suno/core/migrations/0012_add_visit_task_progress.py",
    "server/suno/core/migrations/0013_add_budgets.py",
    "server/suno/core/migrations/0014_add_quantity_matrix.py",
    "server/suno/core/migrations/0027_rename_visit_to_record.py",
    "server/suno/core/tests/test_rename_visit_migration.py",
}
# Compatibilidad de despliegue estrictamente acotada. La primera actualización
# desde v0.13 necesita reconocer su ruta y variable de imagen para poder hacer
# rollback; cualquier aparición del nombre anterior fuera de estos literales
# sigue fallando.
_PACKAGE_COMPATIBILITY: dict[str, tuple[str, ...]] = {
    ".github/workflows/deploy.yml": ("/opt/visitacampo",),
    ".github/workflows/infra.yml": ("VISITACAMPO_API_IMAGE",),
    "infra/scripts/deploy.sh": ("VISITACAMPO_API_IMAGE", "VISITACAMPO_*"),
}


def skipped(path: str) -> bool:
    return (
        path in _HISTORY
        or path in _MIGRATIONS_HISTORY
        or path in _SELF
        or path.endswith(_BINARY)
        or path.startswith("plans/")
    )


def _is_concept(word: str) -> bool:
    lower = word.lower()
    return "visit" in lower and not _PACKAGE.search(word) and lower not in _ALLOWED


def offenders(
    text: str,
    mode: str,
    tracked: frozenset[str] = frozenset(),
    allowed_literals: tuple[str, ...] = (),
) -> list[str]:
    """Las apariciones del nombre viejo, en el orden del texto.

    `tracked` son los archivos versionados: una cita `plans/....md` a uno de ellos no cuenta.
    """
    text = _PLAN_REF.sub(lambda m: " " if m.group(0) in tracked else m.group(0), text)
    for literal in allowed_literals:
        text = text.replace(literal, "")
    found = [m.group(0) for m in _PACKAGE.finditer(text)]
    if mode == "concept":
        found += [w for w in _WORD.findall(text) if _is_concept(w)]
    return found


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "concept"
    if mode not in ("package", "concept"):
        print(f"modo desconocido: {mode} (package|concept)")
        return 2
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    ).stdout.strip()
    os.chdir(top)  # `git ls-files` y `open` trabajan con rutas relativas a la raíz
    listing = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [p for p in listing.decode("utf-8").split("\0") if p]
    tracked = frozenset(files)
    bad = 0
    for path in files:
        if skipped(path):
            continue
        allowed_literals = _PACKAGE_COMPATIBILITY.get(path, ())
        found = offenders(path + "\n", mode, tracked, allowed_literals)
        try:
            with open(path, encoding="utf-8") as handle:
                found += offenders(handle.read(), mode, tracked, allowed_literals)
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            pass
        if found:
            bad += 1
            print(f"{path}: {', '.join(sorted(set(found)))}")
    if bad:
        print(
            f"\n{bad} archivo(s) con el nombre viejo. "
            "Ver plans/2026-11-06-suno-registro-rename-design.md."
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

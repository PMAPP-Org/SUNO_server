#!/usr/bin/env python3
"""Guardia de nombres: el sistema se llama `suno` y el concepto "registro".

    python infra/scripts/check_naming.py [package|concept]

`package` (PR 1) solo busca `visitacampo`; `concept` (desde el PR 2) busca además
"visit"/"visita" en código, plantillas, contrato e infraestructura. No se revisan
`plans/` (planes y registro de avance: son historia), el CHANGELOG, `.gitleaksignore` ni
la propia guardia (que nombra lo que busca). Las citas a documentos de `plans/`
(`plans/visitacampo-....md`) se permiten: esos archivos conservan su nombre.
"""

import re
import subprocess
import sys

_PACKAGE = re.compile(r"visitacampo", re.IGNORECASE)
# Rutas a documentos históricos: se citan con su nombre real (el registro de avance
# sí cambió de nombre: plans/suno-progreso.md).
_PLAN_REF = re.compile(r"plans/(?!visitacampo-progreso\.md)[\w./-]+\.md")
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


def skipped(path: str) -> bool:
    return (
        path in _HISTORY
        or path in _SELF
        or path.endswith(_BINARY)
        or path.startswith("plans/")
    )


def _is_concept(word: str) -> bool:
    lower = word.lower()
    return "visit" in lower and not _PACKAGE.search(word) and lower not in _ALLOWED


def offenders(text: str, mode: str) -> list[str]:
    """Las apariciones del nombre viejo, en el orden del texto."""
    text = _PLAN_REF.sub(" ", text)
    found = [m.group(0) for m in _PACKAGE.finditer(text)]
    if mode == "concept":
        found += [w for w in _WORD.findall(text) if _is_concept(w)]
    return found


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "concept"
    if mode not in ("package", "concept"):
        print(f"modo desconocido: {mode} (package|concept)")
        return 2
    listing = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [p for p in listing.decode("utf-8").split("\0") if p]
    bad = 0
    for path in files:
        if skipped(path):
            continue
        found = offenders(path + "\n", mode)
        try:
            with open(path, encoding="utf-8") as handle:
                found += offenders(handle.read(), mode)
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

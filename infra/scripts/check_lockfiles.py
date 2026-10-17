"""Comprueba que cada `.lock` de server/ refleja las versiones fijadas en sus fuentes.

Las fuentes (`requirements*.txt`) se editan a mano o las edita Dependabot;
los `.lock` los genera `uv pip compile` (comando en la cabecera de cada uno)
y son lo que se instala en CI y en la imagen Docker. Si alguien sube una
versión en el `.txt` y no regenera el `.lock`, el cambio no llega a ninguna
parte en silencio: este script lo convierte en un fallo de CI.

Solo compara los paquetes fijados con `==` en las fuentes (y en las que
estas incluyen con `-r`). No vuelve a resolver nada, así que su resultado no
depende de lo que PyPI publique hoy.

Uso: python infra/scripts/check_lockfiles.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"

#: lockfile -> fuentes con las que se generó (mismo orden que su cabecera).
LOCKS: dict[str, tuple[str, ...]] = {
    "requirements.lock": ("requirements.txt", "requirements-runtime.txt"),
    "requirements-dev.lock": ("requirements-dev.txt",),
    "requirements-dast.lock": ("requirements-dast.txt",),
    "requirements-sast.lock": ("requirements-sast.txt",),
}

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\#]+)")


def _normalize(name: str) -> str:
    """Nombre canónico de PEP 503: `Django` == `django`, `a_b` == `a-b`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    seen = set() if seen is None else seen
    if path in seen:
        return {}
    seen.add(path)
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("-r "):
            pins.update(_pins(path.parent / line[3:].strip(), seen))
            continue
        match = _PIN.match(line)
        if match:
            pins[_normalize(match.group(1))] = match.group(2)
    return pins


def main() -> int:
    problems: list[str] = []
    for lock_name, sources in LOCKS.items():
        locked = _pins(SERVER / lock_name)
        for source in sources:
            for name, version in _pins(SERVER / source).items():
                got = locked.get(name)
                if got != version:
                    problems.append(
                        f"{lock_name}: {name} es {got or 'ausente'}, {source} fija {version}"
                    )
    if problems:
        print("Lockfiles desactualizados respecto a sus fuentes:")
        for problem in problems:
            print(f"  - {problem}")
        print("Regenerar con el comando de la cabecera de cada .lock.")
        return 1
    print(f"OK: {len(LOCKS)} lockfiles coherentes con sus fuentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

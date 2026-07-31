"""Test de arquitectura: nadie consulta un modelo sin pasar por `scoped_qs`.

Paso 6, tarea 5 — ADR-011 ("test de arquitectura que **falla la build** si un
módulo de servicios usa `Model.objects` directamente sin scoping") y RD-11.

**Qué comprueba**: recorre el código de producción del servidor
(`server/visitacampo/` y `server/config/`), lo parsea con `ast` y falla si
encuentra un acceso a un *manager* de Django (`.objects`, `._default_manager`,
`._base_manager`). El camino legítimo es `scoped_qs(ctx, Model)`, que aplica
`org_id = ctx.org_id` (ver `visitacampo/core/scoping.py`).

**Por qué `ast` y no `grep`/regla de ruff**: un `grep` de `.objects.all(`
se esquiva sin querer con `.objects.filter(...)`, con `qs = Model.objects` en
una línea y el `.filter()` en otra, o con un alias; y una regla custom de ruff
exigiría un plugin propio, que es más maquinaria de la que este invariante
necesita. Con `ast` la comprobación es sobre el árbol sintáctico: cualquier
forma de nombrar el manager —llamada inmediata, asignación a variable,
argumento de otra función— es el mismo nodo `Attribute` y cae igual.

**Por qué prohibir el manager entero y no solo `.all()`**: lo peligroso no es
`.all()`, es *partir de todas las filas*. `Model.objects.filter(id=x)` sin
`org_id` es exactamente la fuga entre tenants que RD-11 describe, y a simple
vista parece código correcto. Prohibiendo la puerta y no las salidas, la única
forma de leer una tabla de negocio es la que lleva el aislamiento incorporado.

La lista de exenciones es deliberadamente diminuta y está fijada por un test:
ampliarla obliga a tocar este archivo, que es la fricción que se busca.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from django.test import SimpleTestCase

# `server/` — el raíz del proyecto Django (este archivo está en
# server/visitacampo/core/tests/).
SERVER_ROOT = Path(__file__).resolve().parents[3]

#: Paquetes de producción que se analizan.
SCANNED_PACKAGES = ("visitacampo", "config")

#: Directorios que no son código de producción.
SKIPPED_DIR_NAMES = frozenset({"migrations", "tests", "__pycache__"})

#: Accesos que **son** el manager de un modelo, en cualquiera de sus nombres.
MANAGER_ATTRIBUTES = frozenset({"objects", "_default_manager", "_base_manager"})

#: Exenciones, con su motivo. Rutas relativas a `server/` (un directorio exime
#: todo su contenido). Cada entrada es una decisión de diseño, no una excusa:
ALLOWED = {
    # La capa de modelo: es donde viven managers y querysets por definición.
    "visitacampo/core/models": "definición de los modelos y sus managers",
    # La propia puerta: `scoped_qs` *es* el acceso al manager, con el
    # `org_id = ctx.org_id` incorporado.
    "visitacampo/core/scoping.py": "implementa scoped_qs",
    # Resolver los roles ocurre *antes* de que exista un contexto que aplicar:
    # es la consulta que lo construye, y va filtrada por (user_id, org_id) del
    # principal. No puede scoparse con algo que todavía no existe.
    "visitacampo/core/context.py": "construye el RequestContext (query de roles)",
}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    expression: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.expression}"


def is_allowed(relative_path: str) -> bool:
    """`True` si la ruta (relativa a `server/`) está exenta."""
    return any(
        relative_path == allowed or relative_path.startswith(f"{allowed}/") for allowed in ALLOWED
    )


def scan_source(source: str, relative_path: str) -> list[Violation]:
    """Busca accesos a managers en un módulo, devueltos con línea y expresión."""
    tree = ast.parse(source, filename=relative_path)
    return [
        Violation(relative_path, node.lineno, ast.unparse(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in MANAGER_ATTRIBUTES
    ]


def production_modules() -> list[Path]:
    """Todos los `.py` de producción del servidor, en orden estable."""
    modules: list[Path] = []
    for package in SCANNED_PACKAGES:
        for path in sorted((SERVER_ROOT / package).rglob("*.py")):
            if SKIPPED_DIR_NAMES.intersection(path.relative_to(SERVER_ROOT).parts):
                continue
            modules.append(path)
    return modules


def scan_repository() -> list[Violation]:
    """Aplica `scan_source` a todo el código de producción no exento."""
    violations: list[Violation] = []
    for path in production_modules():
        relative_path = path.relative_to(SERVER_ROOT).as_posix()
        if is_allowed(relative_path):
            continue
        violations.extend(scan_source(path.read_text(encoding="utf-8"), relative_path))
    return violations


class UnscopedAccessTests(SimpleTestCase):
    """El estado limpio del repositorio pasa; cualquier acceso sin scoping no."""

    def test_no_production_module_touches_a_manager_directly(self) -> None:
        violations = scan_repository()
        self.assertEqual(
            violations,
            [],
            "Acceso a un modelo sin `scoped_qs` (ADR-011/RD-11):\n  "
            + "\n  ".join(str(v) for v in violations)
            + "\n\nUsa `scoped_qs(ctx, Model)` (visitacampo/core/scoping.py). Si el "
            "módulo tiene una razón real para saltárselo, añádelo a ALLOWED de "
            "este archivo con su motivo — y que se revise.",
        )


class ScannerTests(SimpleTestCase):
    """El detector detecta: sin esto, el test de arriba pasaría por vacuidad."""

    SERVICE = "visitacampo/core/services.py"

    def test_flags_objects_all(self) -> None:
        source = "def listar(ctx):\n    return Visit.objects.all()\n"
        found = [v.expression for v in scan_source(source, self.SERVICE)]
        self.assertEqual(found, ["Visit.objects"])

    def test_flags_objects_filter(self) -> None:
        """El caso realmente peligroso: parece filtrado, pero no por `org_id`."""
        source = "def buscar(ctx, visit_id):\n    return Visit.objects.filter(id=visit_id)\n"
        self.assertEqual(len(scan_source(source, self.SERVICE)), 1)

    def test_flags_a_manager_bound_to_a_variable(self) -> None:
        """La variante que un `grep` de `.objects.all(` no vería."""
        source = "def buscar(ctx):\n    qs = Visit.objects\n    return qs.filter(id=1)\n"
        self.assertEqual(len(scan_source(source, self.SERVICE)), 1)

    def test_flags_the_private_manager_aliases(self) -> None:
        source = "def a(ctx):\n    return Visit._default_manager.all()\n"
        self.assertEqual(len(scan_source(source, self.SERVICE)), 1)
        source = "def a(ctx):\n    return Visit._base_manager.all()\n"
        self.assertEqual(len(scan_source(source, self.SERVICE)), 1)

    def test_accepts_the_scoped_alternative(self) -> None:
        source = (
            "from visitacampo.core.scoping import scoped_qs\n"
            "def listar(ctx):\n"
            "    return scoped_qs(ctx, Visit).filter(deleted_at__isnull=True)\n"
        )
        self.assertEqual(scan_source(source, self.SERVICE), [])

    def test_reports_path_and_line(self) -> None:
        source = "def a(ctx):\n    pass\n\n\ndef b(ctx):\n    return Visit.objects.all()\n"
        violation = scan_source(source, self.SERVICE)[0]
        self.assertEqual(violation.path, self.SERVICE)
        self.assertEqual(violation.line, 6)


class WalkerTests(SimpleTestCase):
    """Que el recorrido mire donde tiene que mirar (y no donde no debe)."""

    def _scanned(self) -> set[str]:
        return {p.relative_to(SERVER_ROOT).as_posix() for p in production_modules()}

    def test_scans_the_real_production_modules(self) -> None:
        scanned = self._scanned()
        for expected in (
            "visitacampo/core/policies.py",
            "visitacampo/core/scoping.py",
            "visitacampo/core/context.py",
            "visitacampo/health/api.py",
            "config/api.py",
        ):
            with self.subTest(module=expected):
                self.assertIn(expected, scanned)

    def test_skips_tests_and_migrations(self) -> None:
        """Los tests sí usan `.objects` (crear datos); las migraciones también."""
        scanned = self._scanned()
        self.assertNotIn("visitacampo/core/tests/test_models.py", scanned)
        self.assertNotIn("visitacampo/core/migrations/0001_initial.py", scanned)

    def test_the_allowlist_is_exactly_the_documented_one(self) -> None:
        """Ampliar las exenciones exige editar este test: fricción a propósito."""
        self.assertEqual(
            set(ALLOWED),
            {
                "visitacampo/core/models",
                "visitacampo/core/scoping.py",
                "visitacampo/core/context.py",
            },
        )

    def test_every_allowlisted_path_still_exists(self) -> None:
        """Una exención obsoleta es una exención que nadie está revisando."""
        for allowed in ALLOWED:
            with self.subTest(path=allowed):
                self.assertTrue((SERVER_ROOT / allowed).exists())


class NegativeCaseTests(SimpleTestCase):
    """El caso negativo del brief, ejecutado de verdad y no solo descrito.

    Escribe un módulo de servicios con la violación **dentro del árbol que se
    analiza**, comprueba que `scan_repository()` la encuentra, y lo borra. Así
    se prueba la cadena entera —descubrimiento de archivos, exenciones y
    detector— y no solo el detector aislado.
    """

    PROBE = SERVER_ROOT / "visitacampo" / "core" / "_architecture_probe.py"
    PROBE_SOURCE = '"""Módulo temporal del test de arquitectura."""\n\n\ndef listar():\n    return Visit.objects.all()\n'  # noqa: E501

    def test_a_module_with_unscoped_access_breaks_the_build(self) -> None:
        self.assertEqual(scan_repository(), [], "el repositorio debe partir limpio")
        self.PROBE.write_text(self.PROBE_SOURCE, encoding="utf-8")
        try:
            violations = scan_repository()
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].path, "visitacampo/core/_architecture_probe.py")
            self.assertEqual(violations[0].expression, "Visit.objects")
        finally:
            self.PROBE.unlink()
        self.assertEqual(scan_repository(), [], "el probe debe haberse borrado")

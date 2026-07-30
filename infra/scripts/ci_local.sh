#!/usr/bin/env bash
# VisitaCampo — ejecuta localmente el mismo conjunto de checks que
# .github/workflows/backend.yml + mobile.yml (Paso 3 del plan), sin
# depender de tener un remoto de GitHub configurado. Ver CONTRIBUTING.md /
# infra/README.md: ningún paso posterior del plan se considera completo si
# su propio job de CI no pasa, y este script es la forma de comprobarlo
# cuando no hay GitHub Actions disponible (p.ej. este mismo entorno de
# desarrollo, sin `gh` ni remoto configurado).
#
# Comportamiento de cada check:
#   - Si el árbol de código correspondiente todavía no existe (server/,
#     app/ son placeholders hasta los Pasos 4/10) → SKIP informativo, no
#     fallo ("0 archivos que lintar no es un fallo", criterio del brief).
#   - Si el árbol de código existe pero la herramienta (ruff/mypy/pytest/
#     dart/flutter) no está instalada en este entorno → SKIP informativo,
#     documentado como "no verificado localmente, pendiente de un entorno
#     con el toolchain instalado o de la ejecución real en GitHub Actions".
#   - El lint RD-01 (anti-TLS-bypass) y check_envvars.sh SIEMPRE se
#     ejecutan de verdad: no dependen de ningún toolchain externo, solo de
#     bash/grep, así que no hay excusa para omitirlos.
#
# Uso: bash infra/scripts/ci_local.sh
# Exit: 0 si todo lo que se ejecutó (no lo omitido) pasó; 1 si algo falló.

set -uo pipefail  # sin -e a propósito: queremos recorrer todos los checks
                   # y reportar un resumen, no abortar en el primer fallo.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PASSED=0
FAILED=0
SKIPPED=0
FAILED_NAMES=()

section() {
	echo ""
	echo "=========================================="
	echo "== $1"
	echo "=========================================="
}

pass() {
	echo "PASS: $1"
	PASSED=$((PASSED + 1))
}

fail() {
	echo "FAIL: $1" >&2
	FAILED=$((FAILED + 1))
	FAILED_NAMES+=("$1")
}

skip() {
	echo "SKIP: $1 ($2)"
	SKIPPED=$((SKIPPED + 1))
}

command_exists() { command -v "$1" >/dev/null 2>&1; }
has_py_files() { find server -name '*.py' -not -path '*/.venv/*' 2>/dev/null | grep -q .; }
has_py_tests() { find server \( -name 'test_*.py' -o -name '*_test.py' \) 2>/dev/null | grep -q .; }
has_dart_files() { find app -name '*.dart' 2>/dev/null | grep -q .; }
has_dart_tests() { find app/test -name '*_test.dart' 2>/dev/null | grep -q .; }

# --- Backend (server/) --------------------------------------------------
section "Backend (server/) — ruff check"
if ! command_exists ruff; then
	skip "ruff check server/" "ruff no está instalado en este entorno"
elif ruff check server/; then
	pass "ruff check server/"
else
	fail "ruff check server/"
fi

section "Backend (server/) — mypy"
if ! has_py_files; then
	skip "mypy server/" "server/ todavía no tiene archivos .py (Paso 4)"
elif ! command_exists mypy; then
	skip "mypy server/" "mypy no está instalado en este entorno"
elif mypy server/; then
	pass "mypy server/"
else
	fail "mypy server/"
fi

section "Backend (server/) — pytest"
if ! has_py_tests; then
	skip "pytest server/" "server/ todavía no tiene tests (Paso 4+)"
elif ! command_exists pytest; then
	skip "pytest server/" "pytest no está instalado en este entorno"
elif pytest server/; then
	pass "pytest server/"
else
	fail "pytest server/"
fi

section "Backend (server/) — manage.py check --deploy"
if [ ! -f server/manage.py ]; then
	skip "manage.py check --deploy" "server/manage.py todavía no existe (Paso 4)"
elif ! command_exists python && ! command_exists python3; then
	skip "manage.py check --deploy" "python no está instalado en este entorno"
else
	PY="$(command -v python || command -v python3)"
	if "$PY" server/manage.py check --deploy; then
		pass "manage.py check --deploy"
	else
		fail "manage.py check --deploy"
	fi
fi

section "Backend (server/) — manage.py makemigrations --check --dry-run"
if [ ! -f server/manage.py ]; then
	skip "manage.py makemigrations --check --dry-run" "server/manage.py todavía no existe (Paso 4)"
elif ! command_exists python && ! command_exists python3; then
	skip "manage.py makemigrations --check --dry-run" "python no está instalado en este entorno"
else
	PY="$(command -v python || command -v python3)"
	if "$PY" server/manage.py makemigrations --check --dry-run; then
		pass "manage.py makemigrations --check --dry-run"
	else
		fail "manage.py makemigrations --check --dry-run"
	fi
fi

# --- Mobile (app/) --------------------------------------------------------
section "Mobile (app/) — dart analyze"
if ! has_dart_files; then
	skip "dart analyze app/" "app/ todavía no tiene archivos .dart (Paso 10)"
elif ! command_exists dart; then
	skip "dart analyze app/" "dart no está instalado en este entorno"
elif dart analyze app/; then
	pass "dart analyze app/"
else
	fail "dart analyze app/"
fi

section "Mobile (app/) — flutter test"
if ! has_dart_tests; then
	skip "flutter test app/" "app/ todavía no tiene tests (Paso 10+)"
elif ! command_exists flutter; then
	skip "flutter test app/" "flutter no está instalado en este entorno"
elif (cd app && flutter test); then
	pass "flutter test app/"
else
	fail "flutter test app/"
fi

# --- Gates que SIEMPRE se ejecutan de verdad (solo bash/grep) -----------
section "RD-01 — lint anti-TLS-bypass (ADR-009, siempre se ejecuta)"
if bash "$SCRIPT_DIR/lint_no_tls_bypass.sh"; then
	pass "lint_no_tls_bypass.sh"
else
	fail "lint_no_tls_bypass.sh"
fi

section "infra/.env.example — check_envvars.sh (auto-chequeo, patrón de CI)"
if bash "$SCRIPT_DIR/check_envvars.sh" infra/.env.example infra/.env.example; then
	pass "check_envvars.sh (self-check contra .env.example)"
else
	fail "check_envvars.sh (self-check contra .env.example)"
fi

# --- Resumen --------------------------------------------------------------
section "Resumen"
echo "PASS=$PASSED  SKIP=$SKIPPED  FAIL=$FAILED"
if [ "$FAILED" -gt 0 ]; then
	echo "" >&2
	echo "Checks en rojo:" >&2
	for name in "${FAILED_NAMES[@]}"; do
		echo "  - $name" >&2
	done
	exit 1
fi

echo "Todos los checks ejecutados pasaron (los omitidos tienen motivo justificado arriba)."
exit 0

#!/usr/bin/env bash
# VisitaCampo — valida que `.env` defina todas las variables documentadas en
# `.env.example` (patrón C-16, docs/08 §7.1, heredado de docs/05 §6.2).
#
# Falla (exit 1) con un mensaje claro si `.env` no existe o si le falta
# alguna variable presente en `.env.example`. No valida los VALORES (eso
# corresponde a `manage.py check --deploy` u otras validaciones específicas
# de Django en pasos posteriores) — solo que la CLAVE exista, para detectar
# configuración incompleta antes de que un servicio arranque con una
# variable ausente.
#
# Uso:
#   infra/scripts/check_envvars.sh                       # infra/.env vs infra/.env.example
#   infra/scripts/check_envvars.sh <env_file> <example>  # rutas explícitas (tests, CI)
#
# Exit codes: 0 = OK, 1 = falta el archivo .env, 2 = faltan variables,
# 3 = falta el archivo de referencia .env.example (error de configuración del repo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENV_FILE="${1:-$INFRA_DIR/.env}"
ENV_EXAMPLE_FILE="${2:-$INFRA_DIR/.env.example}"

# Extrae nombres de variable (`CLAVE=` al inicio de línea, ignorando
# comentarios y líneas en blanco) de un archivo de entorno.
extract_keys() {
	grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/'
}

if [ ! -f "$ENV_EXAMPLE_FILE" ]; then
	echo "ERROR: no se encontró el archivo de referencia '$ENV_EXAMPLE_FILE'." >&2
	echo "  Este script compara contra infra/.env.example — verifica que exista en el repo." >&2
	exit 3
fi

if [ ! -f "$ENV_FILE" ]; then
	echo "ERROR: no se encontró '$ENV_FILE'." >&2
	echo "  Copia la plantilla y completa los valores reales:" >&2
	echo "    cp '$ENV_EXAMPLE_FILE' '$ENV_FILE'" >&2
	exit 1
fi

example_keys="$(extract_keys "$ENV_EXAMPLE_FILE" | sort -u)"

if [ -z "$example_keys" ]; then
	echo "ERROR: '$ENV_EXAMPLE_FILE' no define ninguna variable (KEY=valor). ¿Archivo vacío o mal formado?" >&2
	exit 3
fi

missing=()
while IFS= read -r key; do
	[ -z "$key" ] && continue
	if ! grep -qE "^${key}=" "$ENV_FILE"; then
		missing+=("$key")
	fi
done <<< "$example_keys"

if [ "${#missing[@]}" -gt 0 ]; then
	echo "ERROR: faltan ${#missing[@]} variable(s) en '$ENV_FILE' que sí están documentadas en '$ENV_EXAMPLE_FILE':" >&2
	for key in "${missing[@]}"; do
		echo "  - $key" >&2
	done
	echo "" >&2
	echo "Añade cada variable (con su valor real) a '$ENV_FILE' y vuelve a ejecutar este script." >&2
	exit 2
fi

total_keys="$(printf '%s\n' "$example_keys" | grep -c .)"
echo "OK: '$ENV_FILE' define las $total_keys variables documentadas en '$ENV_EXAMPLE_FILE'."
exit 0

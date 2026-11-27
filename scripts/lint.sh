#!/usr/bin/env bash
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
#
# lint.sh — ruff check + mypy sobre el proceso Docuboto (ADR-047).
#
# Uso:
#   ./scripts/lint.sh                     # en un contenedor desechable
#   LINT_IN_PROCESS=1 ./scripts/lint.sh   # sin Docker (para CI)
#
# Las dos formas corren las MISMAS herramientas, con los MISMOS argumentos y
# desde el MISMO directorio (`services/docuboto`): ruff resuelve la primera parte
# de los imports según el directorio de trabajo, y reimplementar el gate en el
# workflow es cómo CI llega a aprobar lo que el gate local rechaza.
#
# Los paquetes se DERIVAN del árbol: todo directorio de `services/docuboto/` con
# `__init__.py` entra en mypy sin que nadie tenga que acordarse. `shared/` se
# instala siempre: sin ella `orpycamcp_common` colapsa a `Any` y la librería de
# auditoría inmutable queda sin verificar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
APP_DIR="$PROJECT_ROOT/services/docuboto"

mapfile -t PAQUETES < <(
    for d in "$APP_DIR"/*/; do
        name="$(basename "$d")"
        [ "$name" = tests ] && continue
        [ -f "$d/__init__.py" ] && echo "$name"
    done | sort
)
if [ ${#PAQUETES[@]} -lt 2 ]; then
    echo "ERROR: no se detectaron los paquetes de $APP_DIR: guardián ciego" >&2
    exit 1
fi

GATE='
set +e
rc=0
echo "--- ruff check ---"
ruff check . --config "$CONFIG" --no-cache || rc=1
echo "--- mypy ---"
mypy $PAQUETES --config-file "$CONFIG" --ignore-missing-imports \
    --no-error-summary --cache-dir=/tmp/mypy-cache-docuboto || rc=1
exit $rc
'

if [ "${LINT_IN_PROCESS:-0}" = "1" ]; then
    cd "$APP_DIR"
    CONFIG="$PROJECT_ROOT/pyproject.toml" PAQUETES="${PAQUETES[*]}" bash -c "$GATE"
    rc=$?
else
    # Fuente montada READ-ONLY: el contenedor corre como root y un bind-mount no
    # traduce UIDs. `shared/` se COPIA antes de instalar porque setuptools
    # escribe su egg-info junto al fuente. Sin `2>/dev/null` y bajo `set -e`:
    # si una instalación falla, el gate tiene que fallar en voz alta.
    set +e
    docker run --rm \
        -v "$APP_DIR:/app:ro" \
        -v "$PROJECT_ROOT/shared:/shared:ro" \
        -v "$PROJECT_ROOT/pyproject.toml:/project/pyproject.toml:ro" \
        -e CONFIG=/project/pyproject.toml -e PAQUETES="${PAQUETES[*]}" \
        -w /app python:3.11-slim sh -c "
            set -e
            pip install -q --root-user-action=ignore 'ruff>=0.4.0' 'mypy==1.11.0' -r requirements.lock
            cp -r /shared /tmp/shared-build
            pip install -q --root-user-action=ignore /tmp/shared-build
            bash -c '$GATE'
        "
    rc=$?
    set -e
fi

echo ""
if [ "$rc" -eq 0 ]; then
    echo "══ Lint completo: docuboto pasó ══"
else
    echo "══ Lint FALLIDO ══"
fi
exit "$rc"

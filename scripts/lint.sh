#!/usr/bin/env bash
# lint.sh — Corre ruff check + mypy en todos los servicios dentro de Docker.
#
# Uso:
#   ./scripts/lint.sh              # lint de todos los servicios
#   ./scripts/lint.sh tenant-service  # solo un servicio
#
# Requiere: Docker disponible.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

SERVICES=(
    api-gateway
    auth-service
    tenant-service
    document-service
    storage-service
    workflow-service
    archive-service
    notification-service
)

if [ $# -ge 1 ]; then
    SERVICES=("$@")
fi

FAILURES=()

for svc in "${SERVICES[@]}"; do
    SVC_DIR="$PROJECT_ROOT/services/$svc"
    if [ ! -d "$SVC_DIR" ]; then
        echo "WARN: $SVC_DIR no existe, omitiendo"
        continue
    fi

    echo ""
    echo "══════════════════════════════════════════"
    echo "  Linting: $svc"
    echo "══════════════════════════════════════════"

    # El código fuente se monta READ-ONLY. El contenedor corre como root y un
    # bind-mount no traduce UIDs, así que todo lo que escribiera aquí aparecería
    # en el host como root:root — así se colaron 14 archivos de frontend/src y
    # las cachés de la raíz. Ninguna de las dos herramientas necesita escribir
    # en el árbol: ruff con --no-cache y mypy con --cache-dir dentro del
    # contenedor. El :ro lo hace imposible, no solo improbable.
    if docker run --rm \
        -v "$SVC_DIR:/app:ro" \
        -v "$PROJECT_ROOT/pyproject.toml:/project/pyproject.toml:ro" \
        -w /app \
        python:3.11-slim \
        sh -c "
            pip install -q 'ruff>=0.4.0' 'mypy==1.11.0' -r requirements.txt 2>/dev/null
            echo '--- ruff check ---'
            ruff check . --config /project/pyproject.toml --no-cache
            echo '--- mypy ---'
            mypy app/ --config-file /project/pyproject.toml --ignore-missing-imports \
                --no-error-summary --cache-dir=/tmp/mypy-cache
        "; then
        echo "  ✓ $svc OK"
    else
        echo "  ✗ $svc FAILED"
        FAILURES+=("$svc")
    fi
done

echo ""
if [ ${#FAILURES[@]} -eq 0 ]; then
    echo "══ Lint completo: todos los servicios pasaron ══"
    exit 0
else
    echo "══ Lint FALLIDO en: ${FAILURES[*]} ══"
    exit 1
fi

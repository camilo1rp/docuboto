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

# La lista de servicios se DERIVA del árbol, no se escribe a mano. La versión
# escrita a mano estuvo tres servicios corta (signature, mcp-server, knowledge)
# y el gate pasaba por encima de ellos sin decirlo: no fallaba, sencillamente
# no miraba — 108 errores invisibles entre los tres. Un servicio que se añada a
# `services/` entra en el lint solo, sin que nadie tenga que acordarse.
#
# El criterio de "es un servicio Python" es tener `app/` y `requirements.txt`,
# que es justo lo que el propio contenedor de lint necesita más abajo.
mapfile -t SERVICES < <(
    for d in "$PROJECT_ROOT"/services/*/; do
        [ -d "$d/app" ] && [ -f "$d/requirements.txt" ] && basename "$d"
    done | sort
)

if [ ${#SERVICES[@]} -eq 0 ]; then
    echo "ERROR: no se detectó ningún servicio en $PROJECT_ROOT/services/" >&2
    exit 1
fi

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
    #
    # `shared/` se monta e instala igual que hace cada Dockerfile. Sin ella,
    # `orpycamcp_common` era un import ausente que `--ignore-missing-imports`
    # convertía en `Any`: TODA la librería de auditoría inmutable —la que
    # sostiene la trazabilidad legal— quedaba sin verificar, y el gate analizaba
    # un programa distinto del que se ejecuta. El síntoma visible eran 35
    # `type: ignore` marcados como sobrantes en los bloques `except ImportError`
    # de los once servicios; borrarlos habría cerrado el aviso dejando el agujero.
    if docker run --rm \
        -v "$SVC_DIR:/app:ro" \
        -v "$PROJECT_ROOT/shared:/shared:ro" \
        -v "$PROJECT_ROOT/pyproject.toml:/project/pyproject.toml:ro" \
        -w /app \
        python:3.11-slim \
        sh -c "
            set -e
            pip install -q 'ruff>=0.4.0' 'mypy==1.11.0' -r requirements.txt 2>/dev/null
            # Se COPIA antes de instalar: el build de setuptools escribe
            # \`orpycamcp_common.egg-info\` junto al fuente, y sobre el montaje
            # :ro eso falla. Copiar es lo que mantiene el :ro intacto — es el
            # mismo build que en su día dejó shared/build y shared/*.egg-info
            # como root:root en el host.
            #
            # Sin \`2>/dev/null\` y bajo \`set -e\` A PROPÓSITO: si esta instalación
            # falla, mypy seguiría corriendo y pasaría en verde con la auditoría
            # degradada a \`Any\`. Un gate que se salta su propia cobertura sin
            # decirlo es peor que uno rojo.
            cp -r /shared /tmp/shared-build
            pip install -q /tmp/shared-build
            # `set +e` para que mypy corra aunque ruff falle: se quiere ver TODO
            # lo que hay que arreglar, no solo lo primero. Pero entonces el estado
            # de salida del `sh -c` pasa a ser el del ULTIMO comando (mypy), y un
            # fallo de ruff se perdia en silencio — el gate imprimia '✓ OK' con
            # errores de ruff sin corregir. Se acumulan ambos y se sale con el OR.
            set +e
            rc=0
            echo '--- ruff check ---'
            ruff check . --config /project/pyproject.toml --no-cache || rc=1
            echo '--- mypy ---'
            mypy app/ --config-file /project/pyproject.toml --ignore-missing-imports \
                --no-error-summary --cache-dir=/tmp/mypy-cache || rc=1
            exit \$rc
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

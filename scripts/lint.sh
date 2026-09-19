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
# lint.sh — Corre ruff check + mypy en todos los servicios.
#
# Uso:
#   ./scripts/lint.sh                 # lint de todos los servicios (en Docker)
#   ./scripts/lint.sh tenant-service  # solo un servicio
#   LINT_IN_PROCESS=1 ./scripts/lint.sh   # sin Docker (para CI)
#
# Por defecto cada servicio se lintea en un contenedor desechable, que es lo que
# hace reproducible el resultado en cualquier máquina. `LINT_IN_PROCESS=1` corre
# las mismas herramientas con los MISMOS argumentos en el proceso actual: es para
# CI, donde el job ya vive dentro de un contenedor y no hay Docker disponible.
#
# El modo importa porque el gate y CI tienen que ser LA MISMA COSA. Reimplementar
# el gate en el .gitlab-ci.yml es cómo se llega a que CI apruebe lo que el gate
# local rechaza — que es exactamente lo que pasaba: el pipeline invocaba
# `ruff check services/` desde la RAÍZ, y ruff resuelve la primera-parte de los
# imports según el directorio de trabajo, así que las dos formas discrepan.

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

    if [ "${LINT_IN_PROCESS:-0}" = "1" ]; then
        # Mismas herramientas, mismos argumentos, misma configuración y el mismo
        # directorio de trabajo (el del SERVICIO, no la raíz) que el modo Docker.
        # `shared/` se instala igual, por el mismo motivo: sin ella
        # `orpycamcp_common` colapsa a `Any` y la librería de auditoría inmutable
        # queda sin verificar.
        rc=0
        ( cd "$SVC_DIR" || exit 1
          echo '--- ruff check ---'
          ruff check . --config "$PROJECT_ROOT/pyproject.toml" --no-cache || exit 1
        ) || rc=1
        ( cd "$SVC_DIR" || exit 1
          echo '--- mypy ---'
          mypy app/ --config-file "$PROJECT_ROOT/pyproject.toml" --ignore-missing-imports \
              --no-error-summary --cache-dir=/tmp/mypy-cache-$svc || exit 1
        ) || rc=1
        if [ "$rc" -eq 0 ]; then
            echo "  ✓ $svc OK"
        else
            echo "  ✗ $svc FAILED"
            FAILURES+=("$svc")
        fi
        continue
    fi

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
            # Sin \`2>/dev/null\`, por la MISMA razón que la instalación de
            # \`shared/\` de abajo — la lección estaba aplicada solo a la mitad.
            # Bajo \`set -e\`, un fallo aquí (red, contención al correr varios
            # gates a la vez, un índice caído) aborta el servicio y el script
            # lo lista como FAILED **sin un solo mensaje**: un rojo sin causa
            # visible. Eso ocurrió de verdad — cuatro servicios en rojo, dos de
            # ellos sin cambios, y nada que leer— y un gate que da rojos
            # inexplicables entrena a ignorarlo, que es exactamente el modo de
            # fallo que este repositorio lleva corrigiendo.
            pip install -q 'ruff>=0.4.0' 'mypy==1.11.0' -r requirements.txt
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
            # \`set +e\` para que mypy corra aunque ruff falle: se quiere ver TODO
            # lo que hay que arreglar, no solo lo primero. Pero entonces el estado
            # de salida del \`sh -c\` pasa a ser el del ULTIMO comando (mypy), y un
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

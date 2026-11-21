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
# test-app.sh — Las pruebas del proceso Docuboto (ADR-047), una instalación.
#
# Uso (desde services/docuboto, con `python`/`pytest` del entorno en el PATH):
#   ../../scripts/test-app.sh unit           # cada tests/<dominio>/
#   ../../scripts/test-app.sh integration    # cada tests/<dominio>/integration/
#   ../../scripts/test-app.sh unit archive   # solo esos dominios
#
# Cada dominio corre en su propia invocación de pytest: los conftest de dominio
# fijan entorno y neutralizan piezas propias, y en una sola sesión el orden de
# recolección decidiría el resultado. La lista se DERIVA del árbol.
#
# `integration` necesita DATABASE_URL, REDIS_URL y RUN_INTEGRATION=1. Antes de
# cada dominio recrea la base y vacía Redis: cada conftest asume un Postgres
# virgen con la cadena única aplicada (ADR-053), como cuando era un trabajo de CI.
set -uo pipefail

modo="${1:?uso: test-app.sh unit|integration [dominio...]}"; shift
cd "$(dirname "${BASH_SOURCE[0]}")/../services/docuboto"

# Dominios con tests/integration que no corren aquí, y por qué. Declarados, no
# aprobados (shared/tests/test_ci_corre_la_app.py los vigila).
FUERA_DE_INTEGRACION=(notification)

dominios() {
    if [ $# -gt 0 ]; then printf '%s\n' "$@"; return; fi
    for d in tests/*/; do
        d="$(basename "$d")"
        if [ "$modo" = integration ]; then
            compgen -G "tests/$d/integration/test_*.py" >/dev/null || continue
            [[ " ${FUERA_DE_INTEGRACION[*]} " == *" $d "* ]] && continue
        fi
        echo "$d"
    done
}

base_nueva() {
    python - <<'PY'
import asyncio, os
import asyncpg, redis

url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
base, nombre = url.rsplit("/", 1)

async def main() -> None:
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{nombre}"')
    finally:
        await conn.close()

asyncio.run(main())
redis.Redis.from_url(os.environ["REDIS_URL"]).flushall()
PY
}

fallidos=()
for d in $(dominios "$@"); do
    echo "══ $modo: $d"
    rc=0
    if [ "$modo" = unit ]; then
        pytest "tests/$d" -q --tb=short -p no:cacheprovider || rc=$?
    elif [ "$modo" = integration ]; then
        extra=()
        [ "$d" = storage ] && extra=(--ignore=tests/storage/integration/test_worm_object_lock_real_s3.py)
        { base_nueva && python migrations/runner.py; } || rc=$?
        [ $rc -eq 0 ] && { pytest "tests/$d/integration" -q --tb=short -p no:cacheprovider "${extra[@]}" || rc=$?; }
    else
        echo "modo desconocido: $modo" >&2; exit 2
    fi
    [ $rc -eq 0 ] || fallidos+=("$d")
done

if [ ${#fallidos[@]} -gt 0 ]; then
    echo "══ $modo FALLIDO en: ${fallidos[*]}"
    exit 1
fi
echo "══ $modo: todo pasó"

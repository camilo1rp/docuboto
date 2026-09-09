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
# run-e2e.sh — Levanta el stack E2E, inicializa el tenant de prueba y corre pytest.
#
# Uso:
#   ./tests/run-e2e.sh              # correr todos los tests E2E
#   ./tests/run-e2e.sh -k full_flow # correr solo un test específico
#   ./tests/run-e2e.sh --no-build   # sin rebuilding las imágenes
#
# Al terminar (con o sin error) baja el stack automáticamente.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE="docker compose -f $PROJECT_ROOT/docker-compose.e2e.yml"

BUILD=true
PYTEST_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --no-build) BUILD=false ;;
        *) PYTEST_ARGS+=("$arg") ;;
    esac
done

# ---------------------------------------------------------------------------
# Limpieza al salir
# ---------------------------------------------------------------------------

cleanup() {
    echo ""
    echo "── Bajando stack E2E ─────────────────────────────────────────────"
    $COMPOSE down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 1. Build
# ---------------------------------------------------------------------------

if [ "$BUILD" = true ]; then
    echo "── Build de imágenes ─────────────────────────────────────────────"
    $COMPOSE build --parallel
fi

# ---------------------------------------------------------------------------
# 2. Levantar infraestructura + servicios
# ---------------------------------------------------------------------------

echo ""
echo "── Levantando stack E2E ──────────────────────────────────────────────"
$COMPOSE up -d

# ---------------------------------------------------------------------------
# 3. Esperar a que todos los servicios estén healthy
# ---------------------------------------------------------------------------

echo ""
echo "── Esperando servicios healthy ───────────────────────────────────────"

SERVICES=(
    "e2e-postgres"
    "e2e-redis"
    "e2e-minio"
    "e2e-tenant"
    "e2e-document"
    "e2e-storage"
    "e2e-workflow"
    "e2e-archive"
    "e2e-notification"
)

MAX_WAIT=120
INTERVAL=3

for svc in "${SERVICES[@]}"; do
    elapsed=0
    printf "  Esperando %-25s" "$svc..."
    while true; do
        status=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "starting")
        if [ "$status" = "healthy" ]; then
            echo " OK"
            break
        fi
        if [ $elapsed -ge $MAX_WAIT ]; then
            echo " TIMEOUT"
            echo "ERROR: $svc no llegó a healthy en ${MAX_WAIT}s"
            docker logs "$svc" --tail=20
            exit 1
        fi
        sleep $INTERVAL
        elapsed=$((elapsed + INTERVAL))
    done
done

# ---------------------------------------------------------------------------
# 4. Inicializar tenant de prueba
# ---------------------------------------------------------------------------

echo ""
echo "── Inicializando tenant e2etest ──────────────────────────────────────"

$COMPOSE run --rm --no-deps \
    -v "$PROJECT_ROOT/scripts:/scripts:ro" \
    -v "$PROJECT_ROOT/services:/services:ro" \
    -e DATABASE_URL=postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db \
    tenant-service \
    python /scripts/init_tenant.py \
        --slug  e2etest \
        --name  "Institución E2E Test" \
        --code  E2ETEST

# ---------------------------------------------------------------------------
# 5. Correr pytest desde un contenedor con las dependencias instaladas
# ---------------------------------------------------------------------------

echo ""
echo "── Corriendo tests E2E ───────────────────────────────────────────────"
echo ""

docker run --rm \
    --network "orpyca-mcp-e2e_e2e-net" \
    -v "$PROJECT_ROOT:/project:ro" \
    -w /project \
    -e PYTHONPATH=/project \
    python:3.11-slim \
    sh -c "pip install -q httpx pytest pytest-asyncio && \
           pytest tests/e2e/ -v --tb=short ${PYTEST_ARGS[*]:-}"

echo ""
echo "── Tests E2E completados ─────────────────────────────────────────────"

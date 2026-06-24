#!/usr/bin/env bash
# init-tenant.sh — Inicializar un nuevo tenant en OrpycaMCP.
#
# Uso:
#   ./scripts/init-tenant.sh <slug> <nombre> <codigo>
#
# Ejemplos:
#   ./scripts/init-tenant.sh icetex "Instituto Colombiano de Crédito Educativo" ICETEX
#   ./scripts/init-tenant.sh bogota-limpia "Bogotá Limpia" BLIMPIA
#   ./scripts/init-tenant.sh demo "Institución Demo" DEMO
#
# Requiere que el stack de infraestructura esté levantado:
#   docker compose up -d postgres redis keycloak

set -euo pipefail

# ---------------------------------------------------------------------------
# Argumentos
# ---------------------------------------------------------------------------

if [ "$#" -ne 3 ]; then
    echo ""
    echo "Uso: $0 <slug> <nombre> <codigo>"
    echo ""
    echo "  slug    Identificador único en minúsculas (ej: icetex)"
    echo "  nombre  Nombre completo entre comillas (ej: \"ICETEX\")"
    echo "  codigo  Código para radicados en mayúsculas (ej: ICETEX)"
    echo ""
    exit 1
fi

SLUG="$1"
NAME="$2"
CODE="$3"

# ---------------------------------------------------------------------------
# Verificar que el directorio raíz es correcto
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$PROJECT_ROOT/docker-compose.yml" ]; then
    echo "ERROR: ejecutar desde la raíz del proyecto o desde scripts/"
    exit 1
fi

# ---------------------------------------------------------------------------
# Verificar que postgres está disponible
# ---------------------------------------------------------------------------

echo ""
echo "Verificando PostgreSQL..."
docker compose -f "$PROJECT_ROOT/docker-compose.yml" exec -T postgres \
    pg_isready -U orpycamcp -d orpycamcp_db -q || {
    echo "ERROR: PostgreSQL no está disponible."
    echo "Levanta la infraestructura primero: docker compose up -d postgres"
    exit 1
}
echo "  PostgreSQL OK"

# ---------------------------------------------------------------------------
# Ejecutar el script de inicialización dentro del contenedor auth-service
# (ya tiene asyncpg instalado y acceso a la red interna)
# ---------------------------------------------------------------------------

echo ""
echo "Inicializando tenant: slug=$SLUG | nombre=$NAME | código=$CODE"
echo ""

docker compose -f "$PROJECT_ROOT/docker-compose.yml" run --rm \
    --no-deps \
    -v "$PROJECT_ROOT/scripts:/scripts:ro" \
    -v "$PROJECT_ROOT/services:/services:ro" \
    -e DATABASE_URL=postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db \
    auth-service \
    python /scripts/init_tenant.py \
        --slug  "$SLUG" \
        --name  "$NAME" \
        --code  "$CODE"

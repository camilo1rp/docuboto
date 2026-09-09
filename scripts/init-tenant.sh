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
# EN PRODUCCIÓN, apunta al manifiesto y a las credenciales reales por entorno:
#   COMPOSE_FILE=infra/docker-compose.prod.yml \
#   ENV_FILE=infra/.env \
#   ./scripts/init-tenant.sh mi-institucion "Mi Institución" MIST
#
# Este script se ejecuta DESDE EL HOST (no está dentro de ninguna imagen).
# Requiere que la infraestructura esté levantada:
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
    echo "Variables de entorno opcionales:"
    echo "  COMPOSE_FILE  Manifiesto a usar    (por defecto: docker-compose.yml)"
    echo "  ENV_FILE      Fichero de variables (por defecto: el que use Compose)"
    echo "  DB_USER       Usuario de PostgreSQL (por defecto: orpycamcp)"
    echo "  DB_PASSWORD   Contraseña           (por defecto: orpycamcp)"
    echo "  DB_NAME       Base de datos        (por defecto: orpycamcp_db)"
    echo ""
    exit 1
fi

SLUG="$1"
NAME="$2"
CODE="$3"

# ---------------------------------------------------------------------------
# Localizar el proyecto y el manifiesto
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# El manifiesto es parametrizable: en producción es infra/docker-compose.prod.yml.
# La versión anterior exigía docker-compose.yml en la raíz y cableaba las
# credenciales de DESARROLLO, así que en un despliegue real fallaba o —peor—
# se conectaba con un usuario que no existe.
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
case "$COMPOSE_FILE" in
    /*) COMPOSE_PATH="$COMPOSE_FILE" ;;
    *)  COMPOSE_PATH="$PROJECT_ROOT/$COMPOSE_FILE" ;;
esac

if [ ! -f "$COMPOSE_PATH" ]; then
    echo "ERROR: no existe el manifiesto '$COMPOSE_PATH'."
    echo "Define COMPOSE_FILE si usas otro (ej: infra/docker-compose.prod.yml)."
    exit 1
fi

COMPOSE=(docker compose -f "$COMPOSE_PATH")
if [ -n "${ENV_FILE:-}" ]; then
    case "$ENV_FILE" in
        /*) ENV_PATH="$ENV_FILE" ;;
        *)  ENV_PATH="$PROJECT_ROOT/$ENV_FILE" ;;
    esac
    if [ ! -f "$ENV_PATH" ]; then
        echo "ERROR: no existe el fichero de entorno '$ENV_PATH'."
        exit 1
    fi
    COMPOSE=(docker compose --env-file "$ENV_PATH" -f "$COMPOSE_PATH")
    # Las credenciales viven ahí: se leen para construir el DATABASE_URL, salvo
    # que el llamador las haya fijado explícitamente en el entorno.
    # shellcheck disable=SC1090
    set -a; . "$ENV_PATH"; set +a
fi

DB_USER="${DB_USER:-orpycamcp}"
DB_PASSWORD="${DB_PASSWORD:-orpycamcp}"
DB_NAME="${DB_NAME:-orpycamcp_db}"

# ---------------------------------------------------------------------------
# Verificar que postgres está disponible
# ---------------------------------------------------------------------------

echo ""
echo "Verificando PostgreSQL (usuario '$DB_USER', base '$DB_NAME')..."
"${COMPOSE[@]}" exec -T postgres \
    pg_isready -U "$DB_USER" -d "$DB_NAME" -q || {
    echo "ERROR: PostgreSQL no está disponible."
    echo "Levanta la infraestructura primero: docker compose up -d postgres"
    exit 1
}
echo "  PostgreSQL OK"

# ---------------------------------------------------------------------------
# Ejecutar el script de inicialización dentro del contenedor auth-service
# (ya tiene asyncpg instalado y acceso a la red interna)
#
# scripts/ y services/ se montan en SOLO LECTURA: el contenedor corre como UID 0
# y un bind-mount de escritura dejaría los archivos como root:root en el host.
# ---------------------------------------------------------------------------

echo ""
echo "Inicializando tenant: slug=$SLUG | nombre=$NAME | código=$CODE"
echo ""

"${COMPOSE[@]}" run --rm \
    --no-deps \
    -v "$PROJECT_ROOT/scripts:/scripts:ro" \
    -v "$PROJECT_ROOT/services:/services:ro" \
    -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
    auth-service \
    python /scripts/init_tenant.py \
        --slug  "$SLUG" \
        --name  "$NAME" \
        --code  "$CODE"

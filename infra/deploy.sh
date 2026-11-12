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
#
# deploy.sh — Despliega un lanzamiento en ESTE servidor (ADR-053 §3).
#
#   ./infra/deploy.sh 1.0.0
#
# Lo llama el trabajo `deploy` de `.github/workflows/release.yml` por SSH, después
# de dejar el árbol en el tag y `infra/release-<versión>.yml` a su lado. Sirve
# igual a mano, y para volver atrás: `./infra/deploy.sh <versión-anterior>`
# (expandir/contraer, ADR-053 §4, garantiza que el esquema sirve a las dos).
#
# Orden, y por qué:
#   1. Respaldo, BLOQUEANTE. Cualquier salida distinta de 0 —incluidas 3 a 6:
#      cadena sin anclar, retención o versiones incompletas, paquete de secretos
#      ausente o viejo— detiene el despliegue. Si el respaldo falla, no se despliega.
#   2. `pull` de las imágenes fijadas por digest.
#   3. `up -d`: `migrate-docuboto` corre primero (los servicios esperan a que
#      termine bien) y anota la versión en `public.schema_version`.
#   4. `/health` de `app` tiene que responder con ESA versión.

set -euo pipefail

VERSION="${1:-}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Uso: $0 X.Y.Z" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-/srv/orpyca/backups}"
RELEASE="$SCRIPT_DIR/release-$VERSION.yml"
DEPLOYED="$SCRIPT_DIR/.deployed-version"

[ -f "$RELEASE" ] || { echo "ERROR: falta $RELEASE (lo publica release.yml con el lanzamiento)." >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ERROR: falta el fichero de entorno '$ENV_FILE'." >&2; exit 1; }

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$RELEASE")
ANTERIOR="$(cat "$DEPLOYED" 2>/dev/null || echo ninguna)"
echo "══ Despliegue $VERSION (anterior: $ANTERIOR) ══"

echo "── 1. Respaldo (bloqueante)"
if ! ENV_FILE="$ENV_FILE" BACKUP_DIR="$BACKUP_DIR" "$SCRIPT_DIR/backup.sh"; then
    echo "ERROR: el respaldo no terminó bien. NO se despliega $VERSION; sigue $ANTERIOR." >&2
    exit 1
fi

echo "── 2. Imágenes"
"${COMPOSE[@]}" pull --quiet

echo "── 3. Migraciones y servicios"
"${COMPOSE[@]}" up -d --remove-orphans

echo "── 4. Versión servida"
SERVIDA=""
for _ in $(seq 1 60); do
    SERVIDA="$("${COMPOSE[@]}" exec -T app python -c \
        'import json,urllib.request; print(json.load(urllib.request.urlopen("http://localhost:8000/health"))["version"])' \
        2>/dev/null || true)"
    [ "$SERVIDA" = "$VERSION" ] && break
    sleep 5
done
if [ "$SERVIDA" != "$VERSION" ]; then
    echo "ERROR: /health responde '${SERVIDA:-nada}', no $VERSION. Vuelta atrás: $0 $ANTERIOR" >&2
    exit 1
fi
echo "$VERSION" > "$DEPLOYED"
echo "══ $VERSION en servicio ══"

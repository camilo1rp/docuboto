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
# verify-restore-semanal.sh — Restaura el último respaldo OK en un Postgres
# desechable y comprueba la cadena de auditoría. No toca el despliegue vivo.
#
#   ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/verify-restore-semanal.sh
#
# En cron (domingo 03:00):
#   0 3 * * 0 ENV_FILE=/srv/orpyca/infra/.env /srv/orpyca/infra/verify-restore-semanal.sh \
#               >> /var/log/orpyca-restore-prueba.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$SCRIPT_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-/srv/orpyca/backups}"
PG_IMAGEN="${PG_IMAGEN:-postgres:17-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: no existe el fichero de entorno '$ENV_FILE'." >&2
    exit 1
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

ORIGEN="$(ls -1d "$BACKUP_DIR"/20* 2>/dev/null | grep -v '\.PARCIAL$' | tail -1 || true)"
if [ -z "$ORIGEN" ] || [ ! -f "$ORIGEN/OK" ]; then
    echo "ERROR: no hay un respaldo sellado OK en $BACKUP_DIR." >&2
    exit 1
fi

DUMP="$ORIGEN/postgres.dump"
DESCIFRADO=""
limpiar() {
    if [ -n "${NOMBRE:-}" ]; then docker rm -f "$NOMBRE" >/dev/null 2>&1 || true; fi
    if [ -n "$DESCIFRADO" ]; then rm -rf "$DESCIFRADO"; fi
}
trap limpiar EXIT

if [ -f "$ORIGEN/postgres.dump.gpg" ]; then
    command -v gpg >/dev/null 2>&1 || { echo "ERROR: el respaldo está cifrado y falta gpg." >&2; exit 1; }
    umask 077
    DESCIFRADO="$(mktemp -d)"
    gpg --batch --quiet --decrypt "$ORIGEN/postgres.dump.gpg" > "$DESCIFRADO/postgres.dump"
    DUMP="$DESCIFRADO/postgres.dump"
fi
if [ ! -s "$DUMP" ]; then
    echo "ERROR: no hay volcado de PostgreSQL en '$ORIGEN'." >&2
    exit 1
fi

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
RED="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); k=next(iter(d["services"]["postgres"].get("networks") or {"default": None})); print(d.get("networks", {}).get(k, {}).get("name") or (d.get("name", "") + "_" + k))')"
if [ -z "$RED" ]; then
    echo "ERROR: no se resolvió la red de postgres." >&2
    exit 1
fi
NOMBRE="orpyca-restore-scratch-$$"

echo "══ Restauración de prueba — $(basename "$ORIGEN") ══"
docker run -d --name "$NOMBRE" --network "$RED" \
    -e POSTGRES_USER=orpycamcp \
    -e POSTGRES_PASSWORD=orpycamcp \
    -e POSTGRES_DB=orpycamcp_db \
    "$PG_IMAGEN" >/dev/null

for _ in $(seq 1 30); do
    if docker exec "$NOMBRE" pg_isready -U orpycamcp -d orpycamcp_db >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "$NOMBRE" pg_isready -U orpycamcp -d orpycamcp_db >/dev/null

docker cp "$DUMP" "$NOMBRE:/tmp/postgres.dump"
docker exec "$NOMBRE" pg_restore -U orpycamcp -d orpycamcp_db --no-owner --no-acl /tmp/postgres.dump

ANCLAS="$(dirname "$ORIGEN")/anclas-auditoria.tsv"
ENTRADA="$(mktemp)"
if [ -f "$ANCLAS" ]; then cat "$ANCLAS" > "$ENTRADA"; else : > "$ENTRADA"; fi
if ! "${COMPOSE[@]}" run --rm --no-deps -T \
        -e "DATABASE_URL=postgresql+asyncpg://orpycamcp:orpycamcp@${NOMBRE}:5432/orpycamcp_db" \
        app python -m auth.ops.verify_audit_chains \
        --anclas - --origen restore < "$ENTRADA"; then
    rm -f "$ENTRADA"
    echo "ERROR: la cadena de auditoría no pasa sobre el volcado restaurado." >&2
    exit 1
fi
rm -f "$ENTRADA"
echo "── cadena de auditoría OK sobre $(basename "$ORIGEN")"

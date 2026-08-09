#!/usr/bin/env bash
#
# backup.sh — Respaldo completo de OrpycaMCP (PostgreSQL + MinIO).
#
# Uso:
#   ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh
#
# En cron (diario a las 02:00), redirigiendo la salida a un log que alguien MIRE:
#   0 2 * * * ENV_FILE=/srv/orpyca/infra/.env /srv/orpyca/infra/backup.sh \
#               >> /var/log/orpyca-backup.log 2>&1
#
# ─────────────────────────────────────────────────────────────────────────────
# POR QUÉ ESTE FICHERO EXISTE
#
# Hasta ahora el procedimiento de respaldo vivía SOLO como un bloque de bash
# dentro de `docs/es/deployment.md`, y tal como estaba escrito no funcionaba:
#
#   - usaba `-U orpycamcp` (el usuario de DESARROLLO) contra un despliegue que la
#     propia guía crea con `DB_USER=orpycamcp_prod`;
#   - volcaba MinIO a `/backup` dentro del contenedor de MinIO, ruta que ningún
#     servicio monta, con un cliente `mc` que esa imagen no trae;
#   - no llevaba `set -euo pipefail`, así que un `pg_dump` fallido producía un
#     `.gz` **vacío pero válido**;
#   - y purgaba los respaldos de más de 30 días **pase lo que pase**.
#
# Los cuatro juntos dan el peor resultado posible: en cron, a las 2 de la mañana,
# eso es una rotación de respaldos vacíos con apariencia de normalidad. Nadie se
# entera hasta que hace falta restaurar.
#
# Las tres decisiones que ordenan este script:
#   1. Se VERIFICA el volcado antes de darlo por bueno (`pg_restore --list`).
#   2. La purga de respaldos antiguos ocurre SOLO si la verificación pasó. Un
#      respaldo que no se puede leer no autoriza a borrar el anterior.
#   3. Formato `custom` (-Fc), no SQL plano: ya viene comprimido, permite
#      restauración selectiva y paralela, y `pg_restore --list` puede
#      inspeccionarlo sin restaurarlo — que es lo que hace posible el punto 1.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

COMPOSE_FILE="${COMPOSE_FILE:-$SCRIPT_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-/srv/orpyca/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: no existe el fichero de entorno '$ENV_FILE'." >&2
    echo "Define ENV_FILE o crea infra/.env a partir de infra/.env.example." >&2
    exit 1
fi

# Las credenciales salen del MISMO fichero que usa el despliegue. Cablearlas aquí
# es lo que hacía que el procedimiento anterior fallara en cualquier instalación
# real, donde el usuario no es el de desarrollo.
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

: "${DB_USER:?DB_USER no definido en $ENV_FILE}"
: "${DB_NAME:?DB_NAME no definido en $ENV_FILE}"
: "${MINIO_ROOT_USER:?MINIO_ROOT_USER no definido en $ENV_FILE}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD no definido en $ENV_FILE}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
FECHA="$(date +%Y%m%d_%H%M%S)"
DESTINO="$BACKUP_DIR/$FECHA"
mkdir -p "$DESTINO"

# Cualquier salida no controlada deja el directorio a medias. Marcarlo como
# PARCIAL es preferible a dejar un respaldo incompleto con aspecto de completo:
# el nombre es lo único que un operador mira con prisa.
marcar_parcial() {
    if [ -d "$DESTINO" ] && [ ! -f "$DESTINO/OK" ]; then
        mv "$DESTINO" "${DESTINO}.PARCIAL" 2>/dev/null || true
        echo "Respaldo INCOMPLETO en ${DESTINO}.PARCIAL" >&2
    fi
}
trap marcar_parcial EXIT

echo "══ Respaldo OrpycaMCP — $FECHA ══"
echo "Destino: $DESTINO"

# ---------------------------------------------------------------------------
# 1. PostgreSQL
# ---------------------------------------------------------------------------
# Incluye TODOS los schemas: `public` (registro de tenants + audit_log) y un
# `tenant_*` por institución. Un volcado por-tenant sería inútil para restaurar
# la cadena de auditoría, que es global y encadenada.

echo "── PostgreSQL (pg_dump -Fc)..."
"${COMPOSE[@]}" exec -T postgres \
    pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner \
    > "$DESTINO/postgres.dump"

if [ ! -s "$DESTINO/postgres.dump" ]; then
    echo "ERROR: el volcado de PostgreSQL está VACÍO." >&2
    exit 1
fi

# Verificación real: `pg_restore --list` parsea la cabecera Y el índice completo
# del archivo. Un fichero truncado o corrupto falla aquí, no seis meses después.
#
# El volcado se copia a un fichero temporal DENTRO del contenedor en vez de
# pasarlo por `/dev/stdin`: el formato `custom` es aleatorio-accesible y
# `pg_restore` necesita poder hacer `seek`, cosa que una tubería no permite
# («did not find magic string in file header» es lo que devuelve si se intenta).
echo "── Verificando el volcado..."
OBJETOS="$("${COMPOSE[@]}" exec -T postgres sh -c \
    'cat > /tmp/verificar.dump && pg_restore --list /tmp/verificar.dump; rc=$?; rm -f /tmp/verificar.dump; exit $rc' \
    < "$DESTINO/postgres.dump" | grep -c ';' || true)"
if [ "${OBJETOS:-0}" -lt 1 ]; then
    echo "ERROR: el volcado no es un archivo pg_dump legible." >&2
    exit 1
fi
echo "   volcado legible: $OBJETOS entradas en el índice"

# ---------------------------------------------------------------------------
# 2. MinIO
# ---------------------------------------------------------------------------
# `mc` va en un contenedor DESECHABLE unido a la red del despliegue: la imagen de
# MinIO no trae el cliente, que es por lo que el procedimiento anterior no podía
# funcionar. El directorio de respaldo se monta en ESE contenedor, no en MinIO.

echo "── MinIO (mc mirror)..."
RED="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; print(next(iter(json.load(sys.stdin)["networks"])))' 2>/dev/null \
    || echo "")"
RED_ARG=()
if [ -n "$RED" ]; then
    PROYECTO="$("${COMPOSE[@]}" config --format json \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')"
    RED_ARG=(--network "${PROYECTO}_${RED}")
fi

mkdir -p "$DESTINO/minio"
docker run --rm "${RED_ARG[@]}" \
    -v "$DESTINO/minio:/respaldo" \
    -e MC_HOST_orpyca="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
    --entrypoint sh minio/mc -c 'mc mirror --quiet orpyca /respaldo'

# ---------------------------------------------------------------------------
# 3. Sellar y purgar
# ---------------------------------------------------------------------------

{
    echo "fecha=$FECHA"
    echo "db_name=$DB_NAME"
    echo "db_user=$DB_USER"
    echo "postgres_bytes=$(stat -c%s "$DESTINO/postgres.dump")"
    echo "minio_objetos=$(find "$DESTINO/minio" -type f | wc -l)"
} > "$DESTINO/OK"

trap - EXIT

# La purga va DESPUÉS del sello y solo se alcanza si todo lo anterior pasó: bajo
# `set -e`, cualquier fallo previo aborta el script sin llegar aquí. Ese orden es
# la diferencia entre rotar respaldos y rotar ficheros vacíos.
echo "── Purgando respaldos de más de $RETENTION_DAYS días..."
find "$BACKUP_DIR" -maxdepth 1 -type d -name '20*' -mtime "+$RETENTION_DAYS" \
    -exec rm -rf {} + 2>/dev/null || true

echo "══ Respaldo COMPLETO: $DESTINO ══"

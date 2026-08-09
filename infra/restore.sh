#!/usr/bin/env bash
#
# restore.sh — Restauración de OrpycaMCP desde un respaldo de `backup.sh`.
#
# Uso:
#   ENV_FILE=infra/.env ./infra/restore.sh /srv/orpyca/backups/20260808_020000
#
# ─────────────────────────────────────────────────────────────────────────────
# POR QUÉ ESTE FICHERO EXISTE
#
# El procedimiento anterior (un bloque de bash en `docs/es/deployment.md`) hacía
# `docker compose down` y ACTO SEGUIDO `docker compose exec -T postgres psql`:
# el contenedor ya no existía cuando se le pedía restaurar. Es decir, no se había
# ejecutado nunca. Y no verificaba nada después.
#
# Un respaldo cuya restauración no se ha probado no es un respaldo: es un fichero.
#
# Tres cosas que este script hace y el anterior no:
#   1. Para las APLICACIONES pero deja en pie Postgres y MinIO, que son
#      exactamente los que necesita para trabajar.
#   2. Exige confirmación escrita: restaurar DESTRUYE el estado actual, y es
#      irreversible.
#   3. Verifica la CADENA DE HASH del `audit_log` restaurado, por tenant. Una
#      base que arranca no prueba nada sobre la integridad de la traza legal —
#      que es precisamente lo que un archivo tiene que poder demostrar.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-$SCRIPT_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

if [ "$#" -ne 1 ]; then
    echo "Uso: $0 <directorio-de-respaldo>" >&2
    echo "Ej.: $0 /srv/orpyca/backups/20260808_020000" >&2
    exit 1
fi

ORIGEN="$1"

if [ ! -f "$ORIGEN/OK" ]; then
    echo "ERROR: '$ORIGEN' no lleva el sello OK — respaldo incompleto o inexistente." >&2
    echo "El sello lo escribe backup.sh SOLO tras verificar el volcado." >&2
    exit 1
fi
if [ ! -s "$ORIGEN/postgres.dump" ]; then
    echo "ERROR: no hay volcado de PostgreSQL en '$ORIGEN'." >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
: "${DB_USER:?DB_USER no definido en $ENV_FILE}"
: "${DB_NAME:?DB_NAME no definido en $ENV_FILE}"
: "${MINIO_ROOT_USER:?}"
: "${MINIO_ROOT_PASSWORD:?}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "══ RESTAURACIÓN desde $ORIGEN ══"
cat "$ORIGEN/OK"
echo ""
echo "Esto DESTRUYE el contenido actual de la base '$DB_NAME' y de MinIO."
read -r -p "Escribe RESTAURAR para continuar: " CONFIRMACION
if [ "$CONFIRMACION" != "RESTAURAR" ]; then
    echo "Cancelado."
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Parar las aplicaciones — NO la infraestructura
# ---------------------------------------------------------------------------
# Postgres y MinIO tienen que seguir arriba: son el destino de la restauración.
# Keycloak también se para: sus tablas viven en el mismo `$DB_NAME` (schema
# `keycloak`) y restaurar bajo sus pies lo deja con una caché incoherente.

echo "── Parando servicios de aplicación..."
# `|| true`: parar un servicio que no está levantado no es un error, y bajo
# `set -e` abortaría la restauración por algo que no importa.
"${COMPOSE[@]}" stop \
    api-gateway auth-service tenant-service document-service storage-service \
    workflow-service archive-service notification-service signature-service \
    knowledge-service mcp-server frontend keycloak || true

# ---------------------------------------------------------------------------
# 2. PostgreSQL
# ---------------------------------------------------------------------------
# `--clean --if-exists` deja la base como el respaldo, sin arrastrar objetos que
# se hubieran creado después. Sin `--exit-on-error` a propósito: pg_restore emite
# avisos benignos (roles inexistentes, extensiones ya presentes) que abortarían
# una restauración por lo demás correcta; la verificación real es el paso 4.

echo "── Restaurando PostgreSQL..."
"${COMPOSE[@]}" exec -T postgres \
    pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner \
    < "$ORIGEN/postgres.dump" || true

# ---------------------------------------------------------------------------
# 3. MinIO
# ---------------------------------------------------------------------------
# `--overwrite --remove` deja el almacén EXACTAMENTE como el respaldo. Sin
# `--remove`, los objetos creados después de la copia sobrevivirían y el estado
# resultante no correspondería a ningún instante real del sistema.
#
# ATENCIÓN: los objetos bajo Object-Lock en modo COMPLIANCE **no se pueden
# borrar** hasta que venza su retención — ni por root. Si el bucket de
# preservación tiene objetos protegidos, `--remove` los dejará donde están y eso
# es lo correcto: la inmutabilidad WORM está por encima de esta restauración.

echo "── Restaurando MinIO..."
RED="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; print(next(iter(json.load(sys.stdin)["networks"])))' 2>/dev/null || echo "")"
RED_ARG=()
if [ -n "$RED" ]; then
    PROYECTO="$("${COMPOSE[@]}" config --format json \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')"
    RED_ARG=(--network "${PROYECTO}_${RED}")
fi

docker run --rm "${RED_ARG[@]}" \
    -v "$ORIGEN/minio:/respaldo:ro" \
    -e MC_HOST_orpyca="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
    --entrypoint sh minio/mc -c 'mc mirror --quiet --overwrite --remove /respaldo orpyca'

# ---------------------------------------------------------------------------
# 4. Verificar la cadena de auditoría
# ---------------------------------------------------------------------------
# El paso que convierte esto en una restauración probada. `audit.verify_chain`
# recalcula el hash de cada asiento y comprueba su enlace con el anterior: si el
# volcado se truncó, o si alguien manipuló una fila entre el respaldo y aquí, se
# detecta AQUÍ y no el día que un auditor pida la traza.
#
# Se corre por tenant porque la cadena está encadenada POR tenant, y se recorre
# `public.tenants` en vez de una lista fija: un tenant nuevo entra en la
# verificación el día que existe, sin que nadie tenga que acordarse.

echo "── Verificando la cadena de hash del audit_log..."
"${COMPOSE[@]}" start postgres >/dev/null 2>&1 || true
"${COMPOSE[@]}" run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
    auth-service python - <<'PY'
import asyncio
import sys

import asyncpg
from orpycamcp_common import audit

from app.core.config import settings


async def main() -> int:
    conn = await asyncpg.connect(settings.database_url_asyncpg)
    try:
        slugs = [r["slug"] for r in await conn.fetch("SELECT slug FROM public.tenants ORDER BY slug")]
        # `public.audit_log` guarda también asientos del tenant del SISTEMA, que no
        # tiene fila en `public.tenants`: sin incluirlo, la parte de la cadena que
        # cubre las operaciones internas quedaría sin verificar.
        for extra in await conn.fetch(
            "SELECT DISTINCT tenant_slug FROM public.audit_log WHERE tenant_slug <> ALL($1::text[])",
            slugs,
        ):
            slugs.append(extra["tenant_slug"])

        if not slugs:
            print("   (sin tenants: nada que verificar)")
            return 0

        roto = False
        for slug in slugs:
            ok, fila = await audit.verify_chain(conn, slug)
            total = await conn.fetchval(
                "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1", slug
            )
            if ok:
                print(f"   ✓ {slug}: cadena íntegra ({total} asientos)")
            else:
                print(f"   ✗ {slug}: CADENA ROTA en el asiento id={fila}")
                roto = True
        return 1 if roto else 0
    finally:
        await conn.close()


sys.exit(asyncio.run(main()))
PY

echo "── Levantando servicios..."
"${COMPOSE[@]}" up -d

echo "══ RESTAURACIÓN COMPLETA ══"
echo "Comprueba antes de dar el servicio por bueno:"
echo "  - login de un usuario real (Keycloak reconstruye su caché al arrancar)"
echo "  - un radicado con anexo: consultarlo y DESCARGARLO (BD + MinIO a la vez)"
echo "  - GET /api/v1/audit/verify de un tenant, desde la aplicación"

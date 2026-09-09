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
# ── Un respaldo cifrado se descifra a un área temporal ─────────────────────
#
# El respaldo puede venir cifrado (ver `backup.sh`). Sin este paso, cifrar habría
# convertido la copia en irrecuperable por el propio procedimiento de
# restauración: media corrección, y de las peores, porque el fallo aparece el día
# que hace falta restaurar.
#
# Se descifra a un directorio temporal con permisos restrictivos, que se borra
# pase lo que pase — no se toca el respaldo original, que sigue cifrado.
# El origen REAL se guarda antes de cualquier reasignación: el fichero de anclas
# vive junto al respaldo, no junto a lo descifrado. Sin esto, un respaldo cifrado
# haría buscar el ancla en un directorio temporal y la comprobación de retroceso
# se saltaría en silencio — que es peor que no tenerla, porque parece hecha.
ORIGEN_REAL="$ORIGEN"
DESCIFRADO=""
limpiar_descifrado() { [ -n "$DESCIFRADO" ] && rm -rf "$DESCIFRADO"; }
trap limpiar_descifrado EXIT

if [ -f "$ORIGEN/postgres.dump.gpg" ]; then
    command -v gpg >/dev/null 2>&1 || { echo "ERROR: el respaldo está cifrado y falta gpg." >&2; exit 1; }
    echo "── Descifrando el respaldo..."
    umask 077
    DESCIFRADO="$(mktemp -d)"
    gpg --batch --quiet --decrypt "$ORIGEN/postgres.dump.gpg" > "$DESCIFRADO/postgres.dump" \
        || { echo "ERROR: no se pudo descifrar el volcado." >&2; exit 1; }
    [ -s "$DESCIFRADO/postgres.dump" ] || { echo "ERROR: el volcado descifrado está vacío." >&2; exit 1; }
    if [ -f "$ORIGEN/minio.tar.gpg" ]; then
        gpg --batch --quiet --decrypt "$ORIGEN/minio.tar.gpg" | tar -C "$DESCIFRADO" -xf - \
            || { echo "ERROR: no se pudo descifrar el almacén." >&2; exit 1; }
    fi
    cp -p "$ORIGEN/minio-retencion.tsv" "$DESCIFRADO/" 2>/dev/null || true
    cp -p "$ORIGEN/OK" "$DESCIFRADO/" 2>/dev/null || true
    # A partir de aquí el resto del script trabaja sobre lo descifrado, sin
    # saber que lo estaba: un solo camino, no dos.
    ORIGEN="$DESCIFRADO"
    echo "   descifrado en un área temporal"
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

# Credenciales por fichero, no por `-e`: con `-e` las lee cualquiera que pueda
# hablar con el demonio de Docker.
CREDENCIALES="$(mktemp)"
chmod 600 "$CREDENCIALES"
trap 'rm -f "$CREDENCIALES"; limpiar_descifrado' EXIT
printf 'MC_HOST_orpyca=http://%s:%s@minio:9000\n' \
    "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > "$CREDENCIALES"

# ── El bloqueo se habilita ANTES del espejo, y el orden no es un detalle ────
#
# `mc mirror` copia objetos, no su inmutabilidad: ni el modo de retención, ni la
# fecha hasta la que aplica, ni la retención legal. Restaurar devolvía el índice
# electrónico firmado y las actas como objetos corrientes y borrables — y a
# continuación el script imprimía «cadena íntegra», así que el operador terminaba
# con un mensaje verde y una inmutabilidad que ya no existía.
#
# Reponerlo DESPUÉS no es posible, y esto se comprobó ejecutándolo: el bloqueo de
# un bucket solo puede habilitarse AL CREARLO. Si el espejo llega primero, crea
# un bucket corriente y ya no admite bloqueo nunca — habría que borrarlo y
# empezar de cero. Por eso los buckets protegidos se crean aquí, antes.
echo "── Rehabilitando el bloqueo de los buckets protegidos..."
RETENCION_REPUESTA="no aplica"
if [ ! -f "$ORIGEN/minio-retencion.tsv" ]; then
    echo "   AVISO IMPORTANTE: este respaldo no incluye el estado de retención." >&2
    echo "   Los objetos restaurados serán BORRABLES: la protección de solo-lectura" >&2
    echo "   del índice electrónico y de las actas NO se restaurará. Verifíquelo" >&2
    echo "   antes de dar el sistema por bueno." >&2
    RETENCION_REPUESTA="NO (el respaldo no la incluía)"
else
    while IFS="$(printf '\t')" read -r TIPO BUCKET MODO _; do
        [ "$TIPO" = "BUCKET" ] || continue
        case "$MODO" in COMPLIANCE|GOVERNANCE) ;; *) continue ;; esac
        docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
            --entrypoint sh minio/mc -c \
            "mc mb --with-lock orpyca/$BUCKET; mc retention set --default $MODO 1d orpyca/$BUCKET" \
            >/dev/null 2>&1 || true
        echo "   $BUCKET: bloqueo $MODO"
    done < "$ORIGEN/minio-retencion.tsv"
fi

echo "── Restaurando MinIO..."
docker run --rm "${RED_ARG[@]}" \
    -v "$ORIGEN/minio:/respaldo:ro" \
    --env-file "$CREDENCIALES" \
    --entrypoint sh minio/mc -c 'mc mirror --quiet --overwrite --remove /respaldo orpyca'

# ── Y la retención de cada objeto ──────────────────────────────────────────
#
# La orden solo admite una DURACIÓN en días o años, no la fecha guardada. Se
# calcula lo que falta desde ahora y se redondea HACIA ARRIBA: en modo de
# cumplimiento una retención no se puede acortar, y redondear a la baja
# debilitaría en silencio un plazo que la entidad fijó una vez. Un objeto cuya
# retención ya venció se deja como está: reponerla sería inventar un plazo nuevo.
if [ -f "$ORIGEN/minio-retencion.tsv" ]; then
    echo "── Reponiendo la retención de cada objeto..."
    REPUESTOS=0
    while IFS="$(printf '\t')" read -r TIPO OBJETO MODO HASTA; do
        [ "$TIPO" = "OBJETO" ] || continue
        [ -n "$MODO" ] && [ -n "$HASTA" ] || continue
        DIAS="$(python3 - "$HASTA" <<'PYDIAS'
import datetime, math, sys
try:
    hasta = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(0)
faltan = (hasta - datetime.datetime.now(datetime.UTC)).total_seconds()
if faltan > 0:
    print(max(1, math.ceil(faltan / 86400)))
PYDIAS
)"
        [ -n "$DIAS" ] || continue
        docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
            --entrypoint mc minio/mc retention set "$MODO" "${DIAS}d" "orpyca/$OBJETO" \
            >/dev/null 2>&1 && REPUESTOS=$((REPUESTOS + 1)) || true
    done < "$ORIGEN/minio-retencion.tsv"
    echo "   $REPUESTOS objetos con su retención repuesta"
    [ "$RETENCION_REPUESTA" = "no aplica" ] && RETENCION_REPUESTA="sí ($REPUESTOS objetos)"
fi

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

# ---------------------------------------------------------------------------
# 4b. ¿La traza restaurada está al día, o retrocedida?
# ---------------------------------------------------------------------------
#
# La verificación de arriba comprueba que la cadena es COHERENTE consigo misma,
# y un volcado de hace tres meses lo es perfectamente: sus hashes enlazan. Por
# eso, restaurar un respaldo antiguo suprimía un periodo entero de la traza legal
# y el propio script lo declaraba íntegro. Los tres controles de solo-anexar no
# intervienen, porque protegen contra modificar y borrar, no contra reemplazar
# la tabla.
#
# El ancla acumulativa que escribe el respaldo es lo que permite notar la
# diferencia: si la cadena restaurada tiene MENOS asientos que el último extremo
# anclado, ha retrocedido. Se dice, y el script termina en error.
#
# No es una garantía criptográfica frente a quien controla el host —puede
# reescribir también el ancla—. Es lo que convierte un retroceso silencioso en
# un segundo acto deliberado y detectable.

ANCLAS="$(dirname "$ORIGEN_REAL")/anclas-auditoria.tsv"
echo "── Comparando con el último extremo anclado..."
if [ ! -f "$ANCLAS" ]; then
    echo "   (sin fichero de anclas: no se puede saber si la traza retrocedió)"
else
    RETROCESO=0
    # Una entidad por línea del ancla, sin la cabecera. Se toma el ÚLTIMO ancla
    # de cada una, que es el extremo más reciente que llegó a registrarse.
    for SLUG in $(tail -n +2 "$ANCLAS" | cut -f2 | sort -u); do
        ULTIMO="$(grep -F "	$SLUG	" "$ANCLAS" | tail -1 | cut -f3)"
        [ -n "$ULTIMO" ] || continue
        AHORA="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -At -c \
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = '$SLUG';" 2>/dev/null | tr -d '[:space:]')"
        if [ -z "$AHORA" ]; then
            echo "   ? $SLUG: no se pudo contar los asientos" >&2
        elif [ "$AHORA" -lt "$ULTIMO" ]; then
            echo "   ✗ $SLUG: la traza RETROCEDIÓ — $AHORA asientos; el último ancla decía $ULTIMO" >&2
            RETROCESO=1
        else
            echo "   ✓ $SLUG: $AHORA asientos (último ancla: $ULTIMO)"
        fi
    done
    if [ "$RETROCESO" = "1" ]; then
        echo "" >&2
        echo "   La cadena es coherente pero le FALTAN asientos respecto al último" >&2
        echo "   respaldo registrado. Eso no es una restauración: es una supresión" >&2
        echo "   de traza. Revise antes de dar el sistema por bueno." >&2
        exit 1
    fi
fi

echo "── Levantando servicios..."
"${COMPOSE[@]}" up -d

echo "══ RESTAURACIÓN COMPLETA ══"
echo "Comprueba antes de dar el servicio por bueno:"
echo "  - login de un usuario real (Keycloak reconstruye su caché al arrancar)"
echo "  - un radicado con anexo: consultarlo y DESCARGARLO (BD + MinIO a la vez)"
echo "  - GET /api/v1/audit/verify de un tenant, desde la aplicación"

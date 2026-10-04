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
# Un respaldo cuya retención (Object-Lock) no se capturó por completo restauraría
# objetos borrables con un mensaje verde: el defecto que `reponer_retencion` existe
# para impedir, desplazado al lado del respaldo. Se rechaza salvo aceptación explícita.
if grep -qx 'retencion_capturada_completa=NO' "$ORIGEN/OK" \
        && [ "${RESTORE_ACEPTAR_RETENCION_INCOMPLETA:-}" != "1" ]; then
    echo "ERROR: el respaldo '$ORIGEN' se selló con retencion_capturada_completa=NO:" >&2
    echo "  su retención (Object-Lock) quedó incompleta y restaurarlo dejaría objetos" >&2
    echo "  borrables. Si el responsable lo acepta: RESTORE_ACEPTAR_RETENCION_INCOMPLETA=1." >&2
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
# Credenciales en formato de la CLI de AWS.
#
# El cliente pasó de `mc` (MinIO) a `aws-cli`: la imagen `minio/mc` ya no existe
# —MinIO archivó su proyecto libre y retiró todas sus imágenes—, así que este
# script estaba roto y no se había notado. `aws-cli` es Apache 2.0, está mantenida
# y cubre `put-object-retention`, sin lo cual la inmutabilidad no se puede reponer.
{
    printf 'AWS_ACCESS_KEY_ID=%s\n' "$MINIO_ROOT_USER"
    printf 'AWS_SECRET_ACCESS_KEY=%s\n' "$MINIO_ROOT_PASSWORD"
    printf 'AWS_DEFAULT_REGION=%s\n' "${MINIO_REGION:-us-east-1}"
    printf 'AWS_EC2_METADATA_DISABLED=true\n'
} > "$CREDENCIALES"

# Un solo sitio que sepa invocar el cliente S3.
S3_IMAGEN="${S3_CLI_IMAGEN:-amazon/aws-cli:latest}"
S3_ENDPOINT="${S3_ENDPOINT:-http://minio:9000}"

# shellcheck source=lib-retencion-s3.sh
. "$SCRIPT_DIR/lib-retencion-s3.sh"

s3api() {
    docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
        "$S3_IMAGEN" --endpoint-url "$S3_ENDPOINT" s3api "$@" 2>/dev/null
}

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
        # El bloqueo SOLO se puede habilitar al crear el bucket: no hay forma de
        # activarlo después sin perder su contenido. Por eso se recrea antes de
        # volcar los objetos y no al revés.
        s3api create-bucket --bucket "$BUCKET" --object-lock-enabled-for-bucket >/dev/null || true
        s3api put-object-lock-configuration --bucket "$BUCKET" \
            --object-lock-configuration "{\"ObjectLockEnabled\":\"Enabled\",\"Rule\":{\"DefaultRetention\":{\"Mode\":\"$MODO\",\"Days\":1}}}" \
            >/dev/null || true
        echo "   $BUCKET: bloqueo $MODO"
    done < "$ORIGEN/minio-retencion.tsv"
fi

echo "── Restaurando MinIO..."
# El respaldo guarda `<origen>/minio/<bucket>/...`, así que se vuelca bucket a
# bucket: `aws s3 sync` no tiene un equivalente de `mc mirror` sobre el alias.
for D in "$ORIGEN"/minio/*/; do
    [ -d "$D" ] || continue
    B="$(basename "$D")"
    s3api create-bucket --bucket "$B" >/dev/null || true
    docker run --rm "${RED_ARG[@]}" \
        -v "$D:/respaldo:ro" \
        --env-file "$CREDENCIALES" \
        "$S3_IMAGEN" --endpoint-url "$S3_ENDPOINT" \
        s3 sync /respaldo "s3://$B" --delete --only-show-errors || true
done

# ── Y la retención de cada objeto ──────────────────────────────────────────
#
# Se repone el INSTANTE EXACTO que el respaldo guardó, no una duración.
#
# El cliente anterior (`mc retention set`) solo aceptaba días o años, así que
# había que calcular lo que faltaba y redondear hacia arriba —nunca a la baja, que
# habría debilitado en silencio un plazo fijado una sola vez—. Ese redondeo movía
# la fecha de vencimiento en cada restauración. `put-object-retention` admite
# `RetainUntilDate`, así que el plazo vuelve a ser el que la entidad fijó y no uno
# derivado del momento en que se restauró.
#
# Un objeto cuya retención ya venció se deja como está: reponerla sería inventar
# un plazo nuevo.
if [ -f "$ORIGEN/minio-retencion.tsv" ]; then
    echo "── Reponiendo la retención de cada objeto..."
    # FAIL-CLOSED (AREA-08-10): antes `put-object-retention ... || true` y un contador
    # solo informativo; ahora, si queda sin reponer una sola retención vigente o
    # retención legal activa de la versión actual, la restauración TERMINA EN ERROR
    # (igual que la cadena de auditoría): objetos borrables con un mensaje verde es
    # exactamente el defecto que este bloque existe para impedir.
    MAPA_VERSIONES="$(dirname "$ORIGEN_REAL")/mapa-version-id-$(date +%Y%m%d_%H%M%S).tsv"
    RETENCION_REPUESTOS=0
    if ! reponer_retencion "$ORIGEN/minio-retencion.tsv" "$MAPA_VERSIONES"; then
        echo "" >&2
        echo "   ✗ Quedaron objetos SIN su retención. La restauración NO está completa:" >&2
        echo "     esos objetos son borrables. Corrija y reintente; no dé el sistema por bueno." >&2
        exit 1
    fi
    echo "   mapa version_id viejo → nuevo (acta ADR-027): $MAPA_VERSIONES"
    # El acto de restaurar deja traza INMUTABLE y la BD vuelve a apuntar a los
    # version_id nuevos: un solo módulo, una sola transacción (asiento
    # `storage.restauracion` en public.audit_log + preservacion_worm_objeto.version_id).
    # El mapa viaja por stdin: el contenedor no monta nada en escritura (regla 3).
    if ! "${COMPOSE[@]}" run --rm --no-deps -T \
            -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
            auth-service python -m app.ops.registrar_restauracion \
            --respaldo "$(basename "$ORIGEN_REAL")" \
            --previas-no-restauradas "${RETENCION_PREVIAS_COMPLIANCE:-0}" < "$MAPA_VERSIONES"; then
        echo "   ✗ no se pudo registrar la restauración en audit_log / actualizar version_id." >&2
        echo "     Registre el acta a mano (mapa: $MAPA_VERSIONES) antes de dar el sistema por bueno." >&2
        exit 1
    fi
    [ "$RETENCION_REPUESTA" = "no aplica" ] && RETENCION_REPUESTA="sí ($RETENCION_REPUESTOS objetos)"
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

echo "── Verificando la cadena de hash del audit_log y comparándola con el ancla..."
"${COMPOSE[@]}" start postgres >/dev/null 2>&1 || true

# 4b. ¿La traza restaurada está al día, o retrocedida?
#
# La verificación comprueba que la cadena es COHERENTE consigo misma, y un volcado
# de hace tres meses lo es perfectamente: sus hashes enlazan. Por eso restaurar un
# respaldo antiguo suprimía un periodo entero de la traza legal y el script lo
# declaraba íntegro. El ancla acumulativa que escribe backup.sh es lo que permite
# notar la diferencia: si la cadena restaurada tiene MENOS asientos que el último
# extremo anclado, o el asiento anclado cambió, ha retrocedido.
#
# Es el MISMO módulo que usa backup.sh antes de anclar (AREA-03-02): una sola
# implementación de «verificar + comparar con el ancla». Sustituye al heredoc
# anterior, que desempaquetaba `verify_chain` como una tupla `(ok, fila)` cuando
# devuelve un `ResultadoCadena` y reventaba con TypeError.
#
# No es una garantía criptográfica frente a quien controla el host —puede
# reescribir también el ancla—. Es lo que convierte un retroceso silencioso en un
# segundo acto deliberado y detectable.
ANCLAS="$(dirname "$ORIGEN_REAL")/anclas-auditoria.tsv"
if [ ! -f "$ANCLAS" ]; then
    echo "   (sin fichero de anclas: no se puede saber si la traza retrocedió; se verifica solo la coherencia)"
    ANCLAS="/dev/null"
fi
EXCLUIR=()
for P in ${BACKUP_AUDIT_EXCLUIR_PREFIJOS:-}; do EXCLUIR+=(--excluir-prefijo "$P"); done
# Mismo criterio que backup.sh: las roturas HISTÓRICAS que el responsable declaró
# (BACKUP_AUDIT_ROTURAS_DECLARADAS, o `roturas-declaradas.tsv` junto a los
# respaldos) no impiden restaurar —no se pueden reparar jamás—; una rotura NUEVA,
# un retroceso o un cambio del hash anclado SÍ. Viajan por stdin con el ancla.
ROTURAS_BASE="${BACKUP_AUDIT_ROTURAS_DECLARADAS:-$(dirname "$ORIGEN_REAL")/roturas-declaradas.tsv}"
ENTRADA_VERIF="$(mktemp)"
{ cat "$ANCLAS"; if [ -f "$ROTURAS_BASE" ]; then cat "$ROTURAS_BASE"; fi; } > "$ENTRADA_VERIF"
if ! "${COMPOSE[@]}" run --rm --no-deps -T \
        -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
        auth-service python -m app.ops.verify_audit_chains \
        --anclas - ${EXCLUIR[@]+"${EXCLUIR[@]}"} < "$ENTRADA_VERIF"; then
    rm -f "$ENTRADA_VERIF"
    echo "" >&2
    echo "   La cadena está ROTA o le FALTAN asientos respecto al último respaldo" >&2
    echo "   registrado. Eso no es una restauración: es una supresión de traza." >&2
    echo "   Revise antes de dar el sistema por bueno." >&2
    exit 1
fi
rm -f "$ENTRADA_VERIF"

echo "── Levantando servicios..."
"${COMPOSE[@]}" up -d

echo "══ RESTAURACIÓN COMPLETA ══"
echo "Comprueba antes de dar el servicio por bueno:"
echo "  - login de un usuario real (Keycloak reconstruye su caché al arrancar)"
echo "  - un radicado con anexo: consultarlo y DESCARGARLO (BD + MinIO a la vez)"
echo "  - GET /api/v1/audit/verify de un tenant, desde la aplicación"

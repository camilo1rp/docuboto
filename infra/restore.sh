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

# Idem para las versiones previas bajo COMPLIANCE / retención legal: si el respaldo se
# selló sin haberlas capturado, la evidencia anterior no se puede restaurar.
if grep -qx 'versiones_previas_capturadas=NO' "$ORIGEN/OK" \
        && [ "${RESTORE_ACEPTAR_PERDIDA_VERSIONES:-}" != "1" ]; then
    echo "ERROR: el respaldo '$ORIGEN' se selló con versiones_previas_capturadas=NO:" >&2
    echo "  sus versiones previas bajo COMPLIANCE o retención legal no se respaldaron." >&2
    echo "  Si el responsable lo acepta: RESTORE_ACEPTAR_PERDIDA_VERSIONES=1 (anótelo en el acta)." >&2
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
# Con `[ … ] && rm` la función devolvía 1 cuando no había nada que limpiar (respaldo
# sin cifrar), y como es lo último que corre la trampa EXIT, ese 1 pasaba a ser el
# código de salida del script: una restauración completa terminaba en «fallo».
limpiar_descifrado() { if [ -n "$DESCIFRADO" ]; then rm -rf "$DESCIFRADO"; fi; }
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
    cp -p "$ORIGEN/minio-versiones.tsv" "$DESCIFRADO/" 2>/dev/null || true
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

# ARC-M4: el acta de restauración nombra a una PERSONA. Fail-closed ANTES de cualquier
# acto: sin operador no se confirma, no se para ni se restaura nada.
RESTORE_OPERADOR="${RESTORE_OPERADOR:-}"
if [ "${#RESTORE_OPERADOR}" -lt 3 ]; then
    echo "ERROR: RESTORE_OPERADOR es obligatorio (nombre de la persona que ejecuta la restauración)." >&2
    echo "  Ej.: RESTORE_OPERADOR=\"Ana Pérez\" ENV_FILE=infra/.env $0 <respaldo>" >&2
    exit 1
fi

echo "══ RESTAURACIÓN desde $ORIGEN ══"
echo "Operador: $RESTORE_OPERADOR"
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

# ── Decisiones fail-closed (funciones aparte para poder probarlas) ──────────
#
# `pg_restore` salió distinto de 0. ¿Es el único error que se admite? Fail-closed por
# CUENTA y por CÓDIGO, no por búsqueda: puede salir ≠ 0 sin ninguna línea
# `pg_restore: error:` (contenedor caído, `compose exec` fallido, OOM), y con otros
# códigos (2 = uso, 126/127 = no ejecutable, 137 = OOM) aunque su stderr se parezca al
# inocuo. Solo se admite si:
#   - el código de salida es EXACTAMENTE 1 (pg_restore: «terminó con errores ignorados»);
#   - hay al menos una PK heredada de las particiones de `audit_log` («cannot drop
#     inherited constraint»), y
#   - la línea final `errors ignored on restore: N` declara N == ese número: pg_restore
#     cuenta TODOS los errores que ignoró, así que cualquier error ajeno hace N mayor.
# $1: fichero con el stderr de pg_restore. $2: su código de salida.
errores_de_pg_restore_inocuos() {
    local n_heredadas n_ignorados
    [ "${2:-}" = "1" ] || return 1
    n_heredadas="$(grep '^pg_restore: error:' "$1" | grep -c 'cannot drop inherited constraint' || true)"
    n_ignorados="$(sed -n 's/^pg_restore: warning: errors ignored on restore: \([0-9][0-9]*\)$/\1/p' "$1" | tail -n 1)"
    [ -n "$n_ignorados" ] || return 1
    [ "$n_heredadas" -gt 0 ] && [ "$n_ignorados" -eq "$n_heredadas" ]
}

# El Object-Lock de un bucket protegido tiene que estar `Enabled` ANTES de tocar datos.
# El `|| true` de `put-object-lock-configuration` tolera «ya configurado», pero también
# esconde que el bucket existía SIN bloqueo (no se puede habilitar a posteriori): las
# versiones previas subirían sin WORM y solo la retención por objeto lo detectaría,
# DESPUÉS del `sync --delete`. Necesita `s3api`. $1: bucket. Sale 1 si no está habilitado.
exigir_object_lock_habilitado() {
    local estado
    estado="$(s3api get-object-lock-configuration --bucket "$1" \
        --query 'ObjectLockConfiguration.ObjectLockEnabled' --output text || true)"
    if [ "$estado" != "Enabled" ]; then
        echo "ERROR: $1 no quedó con Object-Lock habilitado (estado: '${estado:-ninguno}')." >&2
        echo "       Existía sin bloqueo y no se puede habilitar a posteriori: bórrelo y repita." >&2
        echo "       No se ha parado ningún servicio ni se ha restaurado nada: la base y el almacén siguen como estaban." >&2
        return 1
    fi
}

# ── Pre-vuelo del almacén (ARC-M3): ANTES de parar servicios y de `pg_restore` ──
#
# El Object-Lock de un bucket protegido solo puede habilitarse AL CREARLO. Si el bucket
# existía sin él, no hay arreglo posible salvo borrarlo; descubrirlo DESPUÉS de
# `pg_restore` dejaba la base restaurada, los servicios parados y el mensaje «No se ha
# tocado ningún dato del almacén» sería cierto solo a medias. Aquí se crean (o se
# comprueban) los buckets protegidos primero: si algo falla, no se ha parado ni
# restaurado nada. Crear un bucket vacío con bloqueo no destruye datos.
echo "── Preparando el acceso al almacén..."
RED="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); k=next(iter(d["services"]["minio"].get("networks") or {"default": None})); print(d.get("networks", {}).get(k, {}).get("name") or (d.get("name", "") + "_" + k))' 2>/dev/null || echo "")"
# La red es la del SERVICIO `minio`, con el nombre ya resuelto por compose. Antes se
# tomaba la primera red declarada del fichero: en producción esa es `default` (la
# de los `migrate-*`), en la que el almacén no está, y el espejo salía vacío.
RED_ARG=()
if [ -n "$RED" ]; then
    RED_ARG=(--network "$RED")
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
S3_IMAGEN="${S3_CLI_IMAGEN:-amazon/aws-cli:2.37.12@sha256:ce44b800602264c12298bbf3399055a4cefdb9ed4a098abe564afe26e7071bce}"
S3_ENDPOINT="${S3_ENDPOINT:-http://minio:9000}"

# shellcheck source=lib-retencion-s3.sh
. "$SCRIPT_DIR/lib-retencion-s3.sh"

# S3_MONTAR: directorio del respaldo que el cliente necesita ver en la MISMA ruta y
# SOLO LECTURA (`put-object --body`). Nunca se monta el repo ni nada en escritura.
S3_MONTAR=""
s3api() {
    local MONTAJE=()
    [ -n "$S3_MONTAR" ] && MONTAJE=(-v "$S3_MONTAR:$S3_MONTAR:ro")
    docker run --rm "${RED_ARG[@]}" ${MONTAJE[@]+"${MONTAJE[@]}"} --env-file "$CREDENCIALES" \
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
        exigir_object_lock_habilitado "$BUCKET" || exit 1
        echo "   $BUCKET: bloqueo $MODO"
    done < "$ORIGEN/minio-retencion.tsv"
fi


echo "── Parando servicios de aplicación..."
# `|| true`: parar un servicio que no está levantado no es un error, y bajo
# `set -e` abortaría la restauración por algo que no importa.
"${COMPOSE[@]}" stop \
    api-gateway auth-service tenant-service document-service storage-service \
    workflow-service archive-service notification-service signature-service \
    knowledge-service mcp-server frontend keycloak || true


# ── Extremo de audit_log ANTES de restaurar (ARC-M4) ────────────────────────
#
# Restaurar un respaldo anterior al estado vivo suprime los asientos posteriores. Con los
# servicios ya parados (nadie escribe), se anota por tenant el conteo, el id y el hash
# del último asiento; el acta los compara con lo restaurado y declara la ventana perdida.
# Fail-closed: sin la captura no se restaura (no se puede declarar lo que no se midió).
echo "── Anotando el extremo actual de audit_log..."
EXTREMOS_PREVIOS="$(mktemp)"
EXISTE_AUDIT="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "SELECT to_regclass('public.audit_log') IS NOT NULL")" \
    || { echo "ERROR: no se pudo consultar la base antes de restaurar. No se ha tocado ningún dato." >&2; exit 1; }
printf 'CAPTURA_PREVIA\tOK\n' > "$EXTREMOS_PREVIOS"
if [ "$EXISTE_AUDIT" = "t" ]; then
    "${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -At -F "$(printf '\t')" -c \
        "WITH m AS (SELECT tenant_slug, count(*) AS n, max(id) AS ult FROM public.audit_log GROUP BY tenant_slug)
         SELECT 'EXTREMO_PREVIO', m.tenant_slug, m.n, m.ult, l.hash
           FROM m JOIN public.audit_log l ON l.id = m.ult AND l.tenant_slug = m.tenant_slug
          ORDER BY m.tenant_slug" >> "$EXTREMOS_PREVIOS" \
        || { rm -f "$EXTREMOS_PREVIOS"; echo "ERROR: no se pudo anotar el extremo de audit_log. No se ha restaurado nada." >&2; exit 1; }
fi

# ---------------------------------------------------------------------------
# 2. PostgreSQL
# ---------------------------------------------------------------------------
# `--clean --if-exists` deja la base como el respaldo, sin arrastrar objetos que
# se hubieran creado después. Sin `--exit-on-error` a propósito: pg_restore emite
# avisos benignos (roles inexistentes, extensiones ya presentes) que abortarían
# una restauración por lo demás correcta; la verificación real es el paso 4.

echo "── Restaurando PostgreSQL..."
# `|| true` dejaba seguir con una base a medio restaurar sin que nadie se enterara.
# Con `--clean`, PostgreSQL rechaza borrar la PK heredada de cada partición de
# `audit_log` («cannot drop inherited constraint»): la tabla padre la recrea igual,
# así que ESE error es inocuo y es el único que se admite. Cualquier otro aborta.
ERRORES_PG="$(mktemp)"
PG_RC=0
"${COMPOSE[@]}" exec -T postgres \
    pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner \
    < "$ORIGEN/postgres.dump" 2> "$ERRORES_PG" || PG_RC=$?
if [ "$PG_RC" -ne 0 ]; then
    if ! errores_de_pg_restore_inocuos "$ERRORES_PG" "$PG_RC"; then
        cat "$ERRORES_PG" >&2
        rm -f "$ERRORES_PG"
        echo "ERROR: pg_restore falló (código $PG_RC): la base NO quedó restaurada. Los servicios siguen parados." >&2
        exit 1
    fi
    echo "   (ignoradas $(grep -c 'cannot drop inherited constraint' "$ERRORES_PG") PK heredadas de particiones: inocuo)"
fi
rm -f "$ERRORES_PG"

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


# (El acceso al almacén y el bloqueo de los buckets protegidos se prepararon arriba,
# en el PRE-VUELO, antes de parar nada: ver «Pre-vuelo del almacén».)

# ── Versiones previas bajo COMPLIANCE / retención legal, ANTES del espejo ───
#
# El espejo solo copia la versión actual. Las previas se suben aquí, de la más antigua
# a la más reciente, con su retención repuesta sobre el VersionId nuevo; luego se sube
# con `put-object` explícito la versión actual de esas claves (el `sync` podría saltarla
# por tamaño y la clave quedaría apuntando a una previa). El mapa viejo -> nuevo empieza
# aquí y lo completa `reponer_retencion` (MAPA_CONSERVAR=1).
#
# Va DESPUÉS de definir `s3api` y de cargar `lib-retencion-s3.sh`, y de recrear
# los buckets protegidos: antes estaba encima de los tres y `restaurar_versiones_previas`
# no existía todavía —«command not found»—, así que toda restauración de un
# respaldo de `backup.sh` (que siempre escribe los dos TSV) moría aquí, con la
# base ya restaurada, los servicios parados y el almacén sin tocar.
MAPA_VERSIONES="$(dirname "$ORIGEN_REAL")/mapa-version-id-$(date +%Y%m%d_%H%M%S).tsv"
: > "$MAPA_VERSIONES"
if [ -f "$ORIGEN/minio-versiones.tsv" ] && [ -f "$ORIGEN/minio-retencion.tsv" ]; then
    echo "── Restaurando las versiones previas protegidas..."
    S3_MONTAR="$ORIGEN"
    if ! restaurar_versiones_previas "$ORIGEN/minio-versiones.tsv" "$ORIGEN/minio-versiones" \
            "$ORIGEN/minio-retencion.tsv" "$MAPA_VERSIONES" "$ORIGEN/minio"; then
        S3_MONTAR=""
        echo "   ✗ Las versiones previas protegidas NO se restauraron: la evidencia anterior se perdería." >&2
        exit 1
    fi
    S3_MONTAR=""
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
        s3 sync /respaldo "s3://$B" --delete --only-show-errors \
        || { echo "ERROR: no se pudo restaurar el bucket $B." >&2; exit 1; }
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
    RETENCION_REPUESTOS=0
    MAPA_CONSERVAR=1   # el mapa ya lo empezó restaurar_versiones_previas
    if ! reponer_retencion "$ORIGEN/minio-retencion.tsv" "$MAPA_VERSIONES" "$ORIGEN/minio-versiones.tsv"; then
        echo "" >&2
        echo "   ✗ Quedaron objetos SIN su retención. La restauración NO está completa:" >&2
        echo "     esos objetos son borrables. Corrija y reintente; no dé el sistema por bueno." >&2
        exit 1
    fi
    echo "   mapa version_id viejo → nuevo (acta ADR-027): $MAPA_VERSIONES"
    [ "$RETENCION_REPUESTA" = "no aplica" ] && RETENCION_REPUESTA="sí ($RETENCION_REPUESTOS objetos)"
fi

# El acto de restaurar deja traza INMUTABLE y la BD vuelve a apuntar a los version_id
# nuevos: un solo módulo, una sola transacción (asiento `storage.restauracion` en
# public.audit_log + preservacion_worm_objeto.version_id). Va SIEMPRE, también sin
# retención que reponer: el acta nombra al operador y declara la ventana de traza
# perdida (ARC-M4). La captura previa y el mapa viajan por stdin: el contenedor no monta
# nada en escritura (regla 3).
echo "── Registrando el acta de restauración..."
if ! { cat "$EXTREMOS_PREVIOS"; cat "$MAPA_VERSIONES"; } | "${COMPOSE[@]}" run --rm --no-deps -T \
        -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
        auth-service python -m app.ops.registrar_restauracion \
        --respaldo "$(basename "$ORIGEN_REAL")" \
        --operador "$RESTORE_OPERADOR" \
        --previas-no-restauradas "${RETENCION_PREVIAS_COMPLIANCE:-0}"; then
    rm -f "$EXTREMOS_PREVIOS"
    echo "   ✗ no se pudo registrar la restauración en audit_log / actualizar version_id." >&2
    echo "     Registre el acta a mano (mapa: $MAPA_VERSIONES) antes de dar el sistema por bueno." >&2
    exit 1
fi
rm -f "$EXTREMOS_PREVIOS"

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
        --anclas - --origen restore ${EXCLUIR[@]+"${EXCLUIR[@]}"} < "$ENTRADA_VERIF"; then
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

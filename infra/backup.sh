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

# El respaldo contiene, fuera de la aplicación y en claro, exactamente lo que
# RF-SEG-08 protege dentro: los radicados de nivel reservado se leen abriendo el
# fichero, sin credenciales, sin clearance, sin no-read-up y sin dejar asiento.
# Con el umask habitual de un cron de root (022) el árbol quedaba 0755/0644, o
# sea legible por cualquier cuenta del servidor. Se fija ANTES de crear nada:
# `chmod` después deja una ventana en la que los ficheros ya existen abiertos.
umask 077
mkdir -p "$DESTINO"
chmod 700 "$DESTINO" "$BACKUP_DIR" 2>/dev/null || true

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

echo "── Almacén de objetos (espejo S3)..."
RED="$("${COMPOSE[@]}" config --format json \
    | python3 -c 'import json,sys; print(next(iter(json.load(sys.stdin)["networks"])))' 2>/dev/null \
    || echo "")"
RED_ARG=()
if [ -n "$RED" ]; then
    PROYECTO="$("${COMPOSE[@]}" config --format json \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')"
    RED_ARG=(--network "${PROYECTO}_${RED}")
fi

mkdir -p "$DESTINO/minio" "$DESTINO/minio-versiones"

# Las credenciales van por fichero de entorno, no por `-e`.
#
# Con `-e` quedan en la configuración del contenedor: las lee cualquiera que
# pueda hablar con el demonio de Docker (`docker inspect`), que es un conjunto
# más amplio que quien debería conocer la contraseña del almacén. El fichero se
# crea con umask 077 y se borra pase lo que pase.
CREDENCIALES="$(mktemp)"
chmod 600 "$CREDENCIALES"
borrar_credenciales() { rm -f "$CREDENCIALES"; }
trap 'borrar_credenciales; marcar_parcial' EXIT
# Credenciales en formato de la CLI de AWS.
#
# El cliente pasó de `mc` (MinIO) a `aws-cli`: la imagen `minio/mc` **ya no
# existe** —MinIO archivó su proyecto libre y retiró todas sus imágenes—, así que
# estos scripts estaban rotos y no se había notado porque el arranque falla antes.
# `aws-cli` es Apache 2.0, está mantenida y es la única alternativa que cubre
# `get-object-retention` / `put-object-retention`, sin lo cual el respaldo no
# puede guardar la inmutabilidad ni la restauración reponerla.
{
    printf 'AWS_ACCESS_KEY_ID=%s\n' "$MINIO_ROOT_USER"
    printf 'AWS_SECRET_ACCESS_KEY=%s\n' "$MINIO_ROOT_PASSWORD"
    printf 'AWS_DEFAULT_REGION=%s\n' "${MINIO_REGION:-us-east-1}"
    printf 'AWS_EC2_METADATA_DISABLED=true\n'
} > "$CREDENCIALES"

# Un solo sitio que sepa invocar el cliente S3. Antes cada llamada repetía el
# `docker run` entero con su imagen y sus credenciales: seis copias que había que
# cambiar a la vez, y la migración de cliente lo demostró.
S3_IMAGEN="${S3_CLI_IMAGEN:-amazon/aws-cli:latest}"
S3_ENDPOINT="${S3_ENDPOINT:-http://minio:9000}"

# shellcheck source=lib-retencion-s3.sh
. "$SCRIPT_DIR/lib-retencion-s3.sh"

# S3_MONTAR: directorio del anfitrión que el cliente necesita ver en la MISMA ruta
# (`get-object` escribe en un fichero). Se monta con el usuario que invoca, para que
# el contenido salga con su umask y su propiedad (ver `--user` más abajo).
S3_MONTAR=""
s3api() {
    local MONTAJE=()
    [ -n "$S3_MONTAR" ] && MONTAJE=(--user "$(id -u):$(id -g)" -e HOME=/tmp -v "$S3_MONTAR:$S3_MONTAR")
    docker run --rm "${RED_ARG[@]}" ${MONTAJE[@]+"${MONTAJE[@]}"} --env-file "$CREDENCIALES" \
        "$S3_IMAGEN" --endpoint-url "$S3_ENDPOINT" s3api "$@"
}

# `--user` no es cosmético.
#
# El contenedor corre como root y con su propio umask, así que el `umask 077` de
# arriba no alcanza a lo que él escribe: el árbol del almacén quedaba 0644 y
# propiedad de root, es decir legible por cualquier cuenta del servidor — justo
# lo que este script trata de impedir— y encima no editable ni purgable sin
# privilegios. Se comprobó ejecutándolo: los anexos salían `-rw-r--r-- root`.
# Dárselo al usuario que invoca hace que hereden su umask y su propiedad.
# `aws s3 sync` trabaja bucket a bucket (no hay equivalente de `mc mirror` sobre
# el alias entero), así que se enumeran primero y se sincroniza cada uno a su
# subcarpeta. Mantener la jerarquía `<destino>/minio/<bucket>/...` importa: es la
# que `restore.sh` espera.
BUCKETS_ESPEJO="$(s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n' || true)"
for B in $BUCKETS_ESPEJO; do
    mkdir -p "$DESTINO/minio/$B"
    docker run --rm "${RED_ARG[@]}" \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp \
        -v "$DESTINO/minio/$B:/respaldo" \
        --env-file "$CREDENCIALES" \
        "$S3_IMAGEN" --endpoint-url "$S3_ENDPOINT" \
        s3 sync "s3://$B" /respaldo --only-show-errors
done

# Cinturón y tirantes: si una versión futura del cliente volviera a escribir con
# otro umask, esto lo corrige en vez de dejarlo pasar en silencio.
chmod -R go-rwx "$DESTINO/minio" 2>/dev/null || true

# El espejo copia OBJETOS, no su inmutabilidad.
#
# `mc mirror` no transporta el modo de retención, la fecha hasta la que aplica,
# la retención legal ni el versionado. Todo el trabajo de preservación —la
# protección que se aplica al índice electrónico firmado, al acta de
# transferencia y al paquete de archivo— desaparecía al restaurar: los objetos
# volvían a existir como objetos corrientes, borrables, en buckets recreados sin
# bloqueo. Y como la restauración SÍ verifica la cadena de auditoría, el
# operador terminaba con un mensaje verde de integridad y una inmutabilidad que
# ya no existía.
#
# Aquí se guarda lo que el espejo no guarda, para que restaurar pueda
# reponerlo. Sin este fichero la información sencillamente no está: no es que
# restaurar la pierda, es que nunca se llegó a copiar.
echo "── Retención del almacén (lo que el espejo no copia)..."

# El análisis se hace AQUÍ, en el anfitrión, y no dentro del contenedor de `mc`.
# Esa imagen es mínima: no trae `sed` ni `awk`, así que un script incrustado
# falla con «command not found» y —peor— lo hace sin romper el respaldo, dejando
# el fichero de retención vacío con aspecto de correcto. Se descubrió
# ejecutándolo. El contenedor solo emite JSON; interpretarlo es cosa nuestra.
: > "$DESTINO/minio-retencion.tsv"
chmod 600 "$DESTINO/minio-retencion.tsv"
: > "$DESTINO/minio-versiones.tsv"
chmod 600 "$DESTINO/minio-versiones.tsv"

# La lectura de la retención ya NO convierte un error en «no hay»: ante un fallo
# distinto de «sin configuración» el respaldo se COMPLETA igual (abortar dejaría un
# .PARCIAL en claro), pero queda sellado con `retencion_capturada_completa=NO` y el
# script sale con código 4. La restauración se niega a usar ese respaldo sin una
# aceptación explícita.
RETENCION_COMPLETA="SI"
# Versiones previas bajo COMPLIANCE/retención legal: el espejo no las copia, se
# respaldan aparte (`minio-versiones/` + `minio-versiones.tsv`). Si alguna no queda
# capturada, el respaldo se completa pero se sella con `versiones_previas_capturadas=NO`
# y el script sale con 5.
VERSIONES_PREVIAS_COMPLETAS="SI"
BUCKETS="$(s3api list-buckets --query 'Buckets[].Name' --output text | tr '\t' '\n')" || {
    echo "   ✗ no se pudieron listar los buckets: la retención NO se capturó" >&2
    RETENCION_COMPLETA="NO"
    BUCKETS=""
}

for B in $BUCKETS; do
    # `get-object-lock-configuration` falla en un bucket sin bloqueo
    # (ObjectLockConfigurationNotFoundError): la AUSENCIA se distingue del ERROR.
    if ! MODO="$(_leer_json_s3 "configuración de bloqueo de $B" \
            s3api get-object-lock-configuration --bucket "$B" --output json | tr -d '\r' | tr '\n' ' ')"; then
        RETENCION_COMPLETA="NO"
        printf 'BUCKET\t%s\tILEGIBLE\n' "$B" >> "$DESTINO/minio-retencion.tsv"
        continue
    fi
    case "$MODO" in
        *COMPLIANCE*) M=COMPLIANCE ;;
        *GOVERNANCE*) M=GOVERNANCE ;;
        *)            M=NINGUNO ;;
    esac
    printf 'BUCKET\t%s\t%s\n' "$B" "$M" >> "$DESTINO/minio-retencion.tsv"
    [ "$M" = "NINGUNO" ] && continue

    # Solo se recorren objeto a objeto los buckets que SÍ tienen bloqueo: en los
    # demás no hay nada que reponer y el recorrido sería gratuito y lento. Se
    # recorren TODAS las versiones y se guarda también la retención legal
    # (AREA-08-10); la lógica vive en lib-retencion-s3.sh, que se prueba con un
    # `s3api` falso.
    if ! respaldar_retencion_bucket "$B" "$DESTINO/minio-retencion.tsv"; then
        echo "   ✗ $B: la retención NO quedó capturada por completo" >&2
        RETENCION_COMPLETA="NO"
    fi
    S3_MONTAR="$DESTINO/minio-versiones"
    if ! respaldar_versiones_previas "$B" "$DESTINO/minio-versiones" "$DESTINO/minio-versiones.tsv"; then
        echo "   ✗ $B: las versiones previas protegidas NO quedaron respaldadas por completo" >&2
        VERSIONES_PREVIAS_COMPLETAS="NO"
    fi
    S3_MONTAR=""
done

echo "   $(grep -c . "$DESTINO/minio-retencion.tsv" 2>/dev/null || echo 0) entradas de retención"

# ---------------------------------------------------------------------------
# 3. Ancla de la cadena de auditoría
# ---------------------------------------------------------------------------
#
# La propiedad de solo-anexar vive DENTRO del motor: privilegios, triggers y
# hash encadenado. Ninguno de los tres interviene cuando la tabla se reemplaza
# entera, que es exactamente lo que hace restaurar un respaldo. Y la
# verificación posterior no lo nota: un volcado de hace tres meses tiene una
# cadena internamente coherente, así que `verify_chain` la da por buena e
# imprime «cadena íntegra» sobre una traza a la que le faltan tres meses.
#
# El ancla es lo que convierte «coherente» en «coherente Y no retrocedida». Se
# escribe fuera del respaldo fechado —en un fichero ACUMULATIVO al que solo se
# añade— porque un ancla guardada dentro del respaldo retrocedería con él, que
# es justo lo que se quiere impedir.
#
# No sustituye a un sello de tercero ni a una copia inmutable del extremo de la
# cadena: un actor que controle el host puede reescribir también este fichero.
# Lo que hace es que borrar un periodo de la traza deje de ser gratis y
# silencioso, y pase a exigir un segundo acto detectable.

ANCLAS="$BACKUP_DIR/anclas-auditoria.tsv"
echo "── Verificando la cadena de auditoría y anclando su extremo..."
if [ ! -f "$ANCLAS" ]; then
    printf 'fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash\n' > "$ANCLAS"
    chmod 600 "$ANCLAS"
fi

# FAIL-CLOSED (AREA-03-02). Antes este bloque ancló el extremo con un `psql` sin
# verificar nada: podía sellar como «extremo de confianza» una cadena YA rota, o
# una que había retrocedido. Ahora el ancla solo se escribe si
# `app.ops.verify_audit_chains` (el mismo módulo que usa restore.sh) comprueba que
# TODAS las cadenas son íntegras, que ningún conteo bajó respecto al ancla
# anterior y que el hash del asiento anclado no cambió. El ancla viaja por stdout
# y se anexa AQUÍ, en el host: el contenedor no monta nada en escritura (regla 3).
# En desarrollo, `BACKUP_AUDIT_EXCLUIR_PREFIJOS="itest"` deja fuera los tenants
# desechables que dejaron los tests de integración (audit_log es append-only y no
# se pueden borrar); NO se define en producción.
EXCLUIR=()
for P in ${BACKUP_AUDIT_EXCLUIR_PREFIJOS:-}; do EXCLUIR+=(--excluir-prefijo "$P"); done

# «NO ANCLAR» no es «ABORTAR EL RESPALDO». `audit_log` solo admite anexar (Ley
# 594/2000 arts. 4 y 11): una rotura histórica no se repara nunca, y si una sola
# fila rota abortara el script, la entidad perdería para siempre la capacidad de
# producir un respaldo completo y sellado (cada noche un `.PARCIAL` en claro y
# sin rotación). Por eso, si la verificación falla:
#   - NO se emite ancla (sellar una cadena rota como extremo de confianza sigue
#     siendo peor que no anclar);
#   - el respaldo SE COMPLETA (cifrado, sello, purga), con
#     `verificacion_auditoria=FALLIDA` en el sello;
#   - el script sale con código 3 AL FINAL, para que la supervisión lo vea.
# Las roturas HISTÓRICAS aprobadas por el responsable se declaran en una línea
# base (BACKUP_AUDIT_ROTURAS_DECLARADAS = fichero `ROTURA<TAB>slug<TAB>id<TAB>nota`);
# solo una rotura NUEVA, un retroceso o un cambio del hash anclado cuentan.
# La línea base viaja por stdin junto al ancla: el contenedor no monta nada (regla 3).
VERIFICACION_AUDITORIA="OK"
ENTRADA_VERIF="$(mktemp)"
{ cat "$ANCLAS"; if [ -n "${BACKUP_AUDIT_ROTURAS_DECLARADAS:-}" ]; then cat "$BACKUP_AUDIT_ROTURAS_DECLARADAS"; fi; } > "$ENTRADA_VERIF" \
    || VERIFICACION_AUDITORIA="FALLIDA"
NUEVAS=""
if [ "$VERIFICACION_AUDITORIA" = "OK" ]; then
    if ! NUEVAS="$("${COMPOSE[@]}" run --rm --no-deps -T \
            -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
            auth-service python -m app.ops.verify_audit_chains \
            --anclas - --emitir-ancla "$FECHA" ${EXCLUIR[@]+"${EXCLUIR[@]}"} < "$ENTRADA_VERIF")"; then
        VERIFICACION_AUDITORIA="FALLIDA"
    fi
fi
rm -f "$ENTRADA_VERIF"
if [ "$VERIFICACION_AUDITORIA" != "OK" ]; then
    NUEVAS=""
    echo "" >&2
    echo "✗ La cadena de auditoría NO está íntegra, retrocedió respecto al último ancla" >&2
    echo "  o la verificación no pudo correr. NO se ancla; el respaldo SE COMPLETA y se" >&2
    echo "  sella con verificacion_auditoria=FALLIDA. El script saldrá con código 3." >&2
    echo "  Si es una rotura histórica ya conocida, decláralo en" >&2
    echo "  BACKUP_AUDIT_ROTURAS_DECLARADAS (acta del responsable)." >&2
fi
if [ -n "$NUEVAS" ]; then printf '%s\n' "$NUEVAS" >> "$ANCLAS"; fi
ANCLADAS="$(awk -F'\t' -v f="$FECHA" '$1==f' "$ANCLAS" 2>/dev/null | wc -l)"
echo "   ancladas $ANCLADAS entidades en $ANCLAS"

# ---------------------------------------------------------------------------
# 4. Cifrado
# ---------------------------------------------------------------------------
#
# Lo que hay en este directorio es, fuera de la aplicación, exactamente lo que
# RF-SEG-08 protege dentro: un radicado reservado se lee abriendo el fichero,
# sin credenciales, sin clearance, sin no-read-up y sin dejar asiento. Y un
# respaldo viaja: a un NAS, a una cinta, a la nube de un tercero, a las manos de
# un proveedor que no tiene ni debe tener nivel de acceso sobre esos documentos.
#
# Por eso el script NO continúa sin una decisión explícita. Las dos son válidas
# y las dos quedan escritas; lo que ya no es posible es escribir en claro sin
# haberlo decidido:
#
#   BACKUP_GPG_RECIPIENT=<id de clave>   cifra a esa clave pública
#   BACKUP_PLANO=1                       acepta dejarlo en claro, a sabiendas
#
# Se cifra el volcado y el árbol del almacén por separado y se borran los
# originales solo si el cifrado tuvo éxito: un cifrado a medias que además borra
# el original convierte un problema de confidencialidad en pérdida de datos.

if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
    echo "── Cifrando el respaldo para $BACKUP_GPG_RECIPIENT..."
    command -v gpg >/dev/null 2>&1 || { echo "ERROR: falta gpg." >&2; exit 1; }

    gpg --batch --yes --trust-model always --encrypt \
        --recipient "$BACKUP_GPG_RECIPIENT" \
        --output "$DESTINO/postgres.dump.gpg" "$DESTINO/postgres.dump"
    [ -s "$DESTINO/postgres.dump.gpg" ] || { echo "ERROR: el cifrado del volcado falló." >&2; exit 1; }
    rm -f "$DESTINO/postgres.dump"

    tar -C "$DESTINO" -cf - minio minio-versiones \
      | gpg --batch --yes --trust-model always --encrypt \
            --recipient "$BACKUP_GPG_RECIPIENT" \
            --output "$DESTINO/minio.tar.gpg"
    [ -s "$DESTINO/minio.tar.gpg" ] || { echo "ERROR: el cifrado del almacén falló." >&2; exit 1; }
    rm -rf "$DESTINO/minio" "$DESTINO/minio-versiones"

    chmod 600 "$DESTINO"/*.gpg
    CIFRADO="gpg:$BACKUP_GPG_RECIPIENT"
    echo "   cifrado con GPG"
elif [ "${BACKUP_PLANO:-}" = "1" ]; then
    CIFRADO="no (BACKUP_PLANO=1)"
    echo "── AVISO: el respaldo queda EN CLARO por decisión explícita (BACKUP_PLANO=1)." >&2
    echo "   Contiene documentos clasificados de todas las entidades. Custódielo" >&2
    echo "   como custodiaría el archivo físico reservado." >&2
else
    echo "" >&2
    echo "ERROR: el respaldo contiene los documentos clasificados de todas las" >&2
    echo "entidades y no se ha indicado cómo protegerlos. Elija una:" >&2
    echo "" >&2
    echo "  BACKUP_GPG_RECIPIENT=<id de clave>   cifrar a esa clave pública" >&2
    echo "  BACKUP_PLANO=1                       dejarlo en claro, a sabiendas" >&2
    echo "" >&2
    exit 1
fi


# ---------------------------------------------------------------------------
# 5. Sellar y purgar
# ---------------------------------------------------------------------------

# El sello describe lo que HAY, que ya no es siempre lo mismo: cifrado, el
# volcado es `postgres.dump.gpg` y el almacén un `minio.tar.gpg`. Leer los
# nombres en claro sin más habría abortado el script justo después de cifrar
# correctamente — bajo `set -e`, un `stat` de un fichero que ya no existe.
if [ -f "$DESTINO/postgres.dump.gpg" ]; then
    VOLCADO="$DESTINO/postgres.dump.gpg"
    OBJETOS_MINIO="(cifrado en minio.tar.gpg)"
else
    VOLCADO="$DESTINO/postgres.dump"
    OBJETOS_MINIO="$(find "$DESTINO/minio" -type f | wc -l)"
fi

{
    echo "fecha=$FECHA"
    echo "db_name=$DB_NAME"
    echo "db_user=$DB_USER"
    echo "postgres_bytes=$(stat -c%s "$VOLCADO")"
    echo "minio_objetos=$OBJETOS_MINIO"
    echo "cifrado=$CIFRADO"
    echo "retencion_capturada=$(grep -c . "$DESTINO/minio-retencion.tsv" 2>/dev/null || echo 0)"
    echo "verificacion_auditoria=$VERIFICACION_AUDITORIA"
    echo "retencion_capturada_completa=$RETENCION_COMPLETA"
    echo "versiones_previas_capturadas=$VERSIONES_PREVIAS_COMPLETAS"
} > "$DESTINO/OK"
chmod 600 "$DESTINO/OK"

trap - EXIT

# La purga va DESPUÉS del sello y solo se alcanza si todo lo anterior pasó: bajo
# `set -e`, cualquier fallo previo aborta el script sin llegar aquí. Ese orden es
# la diferencia entre rotar respaldos y rotar ficheros vacíos.
echo "── Purgando respaldos de más de $RETENTION_DAYS días..."
find "$BACKUP_DIR" -maxdepth 1 -type d -name '20*' -mtime "+$RETENTION_DAYS" \
    -exec rm -rf {} + 2>/dev/null || true

echo "══ Respaldo COMPLETO: $DESTINO ══"
if [ "$VERIFICACION_AUDITORIA" != "OK" ]; then
    echo "✗ ATENCIÓN: respaldo completo y sellado, pero SIN ancla: la verificación de la" >&2
    echo "  cadena de auditoría falló (verificacion_auditoria=FALLIDA). Salida 3." >&2
    exit 3
fi
if [ "$RETENCION_COMPLETA" != "SI" ]; then
    echo "✗ ATENCIÓN: respaldo completo y sellado, pero la RETENCIÓN (Object-Lock) no se" >&2
    echo "  capturó por completo (retencion_capturada_completa=NO). Salida 4." >&2
    exit 4
fi
if [ "$VERSIONES_PREVIAS_COMPLETAS" != "SI" ]; then
    echo "✗ ATENCIÓN: respaldo completo y sellado, pero las versiones PREVIAS bajo COMPLIANCE o" >&2
    echo "  retención legal no se respaldaron por completo (versiones_previas_capturadas=NO). Salida 5." >&2
    exit 5
fi

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
printf 'MC_HOST_orpyca=http://%s:%s@minio:9000\n' \
    "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > "$CREDENCIALES"

# `--user` no es cosmético.
#
# El contenedor corre como root y con su propio umask, así que el `umask 077` de
# arriba no alcanza a lo que él escribe: el árbol del almacén quedaba 0644 y
# propiedad de root, es decir legible por cualquier cuenta del servidor — justo
# lo que este script trata de impedir— y encima no editable ni purgable sin
# privilegios. Se comprobó ejecutándolo: los anexos salían `-rw-r--r-- root`.
# Dárselo al usuario que invoca hace que hereden su umask y su propiedad.
docker run --rm "${RED_ARG[@]}" \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$DESTINO/minio:/respaldo" \
    --env-file "$CREDENCIALES" \
    --entrypoint sh minio/mc -c 'mc mirror --quiet orpyca /respaldo'

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

BUCKETS="$(docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
    --entrypoint mc minio/mc ls --json orpyca 2>/dev/null \
    | python3 -c 'import json,sys
for l in sys.stdin:
    try: d=json.loads(l)
    except ValueError: continue
    k=(d.get("key") or "").rstrip("/")
    if k: print(k)' || true)"

for B in $BUCKETS; do
    MODO="$(docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
        --entrypoint mc minio/mc retention info --default --json "orpyca/$B" 2>/dev/null \
        | tr -d '\r' | tr '\n' ' ' || true)"
    case "$MODO" in
        *COMPLIANCE*) M=COMPLIANCE ;;
        *GOVERNANCE*) M=GOVERNANCE ;;
        *)            M=NINGUNO ;;
    esac
    printf 'BUCKET\t%s\t%s\n' "$B" "$M" >> "$DESTINO/minio-retencion.tsv"
    [ "$M" = "NINGUNO" ] && continue

    # Solo se recorren objeto a objeto los buckets que SÍ tienen bloqueo: en los
    # demás no hay nada que reponer y el recorrido sería gratuito y lento.
    docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
        --entrypoint mc minio/mc ls --json --recursive "orpyca/$B" 2>/dev/null \
        | python3 -c 'import json,sys
for l in sys.stdin:
    try: d=json.loads(l)
    except ValueError: continue
    k=d.get("key")
    if k: print(k)' \
        | while read -r K; do
            # `--json` y no el texto legible: éste dice «expiring in 29 days»,
            # una duración RELATIVA al momento de leerla. Reponerla con eso
            # correría la fecha de vencimiento hacia adelante en cada
            # restauración, alargando en silencio una retención archivística que
            # la entidad fijó una vez. El JSON trae el instante exacto.
            INFO="$(docker run --rm "${RED_ARG[@]}" --env-file "$CREDENCIALES" \
                --entrypoint mc minio/mc retention info --json "orpyca/$B/$K" 2>/dev/null \
                | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: raise SystemExit(0)
m,u=d.get("mode"),d.get("until")
if m and u: print(f"{m}\t{u}")' || true)"
            [ -n "$INFO" ] && printf 'OBJETO\t%s\t%s\n' "$B/$K" "$INFO" \
                >> "$DESTINO/minio-retencion.tsv"
        done
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
echo "── Anclando el extremo de la cadena de auditoría..."
if [ ! -f "$ANCLAS" ]; then
    printf 'fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash\n' > "$ANCLAS"
    chmod 600 "$ANCLAS"
fi
"${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -At -F$'\t' -c "
    SELECT '$FECHA', tenant_slug, count(*),
           max(id),
           (SELECT a2.hash FROM public.audit_log a2
             WHERE a2.tenant_slug = a1.tenant_slug
             ORDER BY a2.id DESC LIMIT 1)
      FROM public.audit_log a1
     GROUP BY tenant_slug
     ORDER BY tenant_slug;" >> "$ANCLAS"
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

    tar -C "$DESTINO" -cf - minio \
      | gpg --batch --yes --trust-model always --encrypt \
            --recipient "$BACKUP_GPG_RECIPIENT" \
            --output "$DESTINO/minio.tar.gpg"
    [ -s "$DESTINO/minio.tar.gpg" ] || { echo "ERROR: el cifrado del almacén falló." >&2; exit 1; }
    rm -rf "$DESTINO/minio"

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

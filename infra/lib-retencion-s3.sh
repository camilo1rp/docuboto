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
# lib-retencion-s3.sh — retención (Object-Lock) y retención legal por objeto:
# respaldarla y reponerla. SE HACE `source` desde backup.sh y restore.sh; NO se
# ejecuta. Necesita que el llamador defina `s3api` (envoltorio del cliente S3), lo
# que además permite probarla con un `s3api` falso (shared/tests/test_retencion_
# respaldo_restauracion.py) sin docker ni almacén real (AREA-08-10).
#
# Formato de `minio-retencion.tsv` (separador TAB; «-» = vacío, porque con IFS=TAB
# los campos vacíos consecutivos se colapsan al leer):
#
#   BUCKET  <bucket>  <COMPLIANCE|GOVERNANCE|NINGUNO>
#   OBJETO  <bucket/clave>  <modo|->  <RetainUntilDate|->  <version_id|->  <ON|OFF>  <1|0 es la versión actual>
#
# Las versiones PREVIAS (IsLatest=0) con COMPLIANCE vigente o retención legal ON se
# respaldan además con su contenido (`respaldar_versiones_previas`) y se anotan en un
# fichero APARTE, `minio-versiones.tsv`, para que el formato anterior se siga leyendo:
#
#   VERSION  <bucket/clave>  <version_id>  <sha256 del contenido>  <LastModified>
#
# El contenido va en `minio-versiones/<bucket>/<sha256(clave TAB version_id)>`.
#
# Los cuatro primeros campos de OBJETO son los del formato anterior: un respaldo
# viejo se sigue leyendo (sin versión ni retención legal: «-» y OFF por defecto).

# _leer_json_s3 <etiqueta> <cmd s3api...>
# Ejecuta la lectura y distingue «SIN configuración» de «FALLÓ» (la lectura
# opcional convierte el error en vacío: un `|| true` tomaba un error transitorio
# del almacén, unas credenciales caídas o un límite de peticiones por «sin
# retención», y la fila no se escribía ni nada se quejaba):
#   - respuesta correcta           -> imprime el JSON, devuelve 0
#   - NoSuchObjectLockConfiguration / ObjectLockConfigurationNotFoundError
#                                  -> imprime `{}` (de verdad no hay retención), 0
#   - cualquier otro fallo, o un JSON vacío/ilegible -> devuelve 1 (y dice por qué)
_leer_json_s3() {
    local ETQ="$1" ERR OUT RC
    shift
    ERR="$(mktemp)"
    OUT="$("$@" 2>"$ERR")" && RC=0 || RC=$?
    if [ "$RC" -eq 0 ]; then
        if printf '%s' "$OUT" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
            printf '%s\n' "$OUT"; rm -f "$ERR"; return 0
        fi
        echo "   ✗ $ETQ: respuesta vacía o ilegible del almacén" >&2
        rm -f "$ERR"; return 1
    fi
    if grep -qE 'NoSuchObjectLockConfiguration|ObjectLockConfigurationNotFoundError' "$ERR"; then
        echo '{}'; rm -f "$ERR"; return 0
    fi
    echo "   ✗ $ETQ: la lectura falló ($(head -c 300 "$ERR" | tr '\n' ' '))" >&2
    rm -f "$ERR"
    return 1
}

# respaldar_retencion_bucket <bucket> <tsv>
# Recorre TODAS las versiones del bucket (no solo la actual) y deja una línea
# OBJETO por cada una con retención o retención legal.
#
# FAIL-CLOSED: devuelve 1 (abortando ESE bucket) si una lectura falla por algo
# distinto de «sin configuración», o si la cuenta de filas no cuadra con las
# versiones listadas. backup.sh no cae por eso: completa el respaldo, lo sella con
# `retencion_capturada_completa=NO` y sale con error al final.
respaldar_retencion_bucket() {
    local B="$1" TSV="$2" K V L INFO HOLD M U RJSON HJSON VERS
    local N_VERS=0 N_OBJ=0 SIN_RETENCION=0
    VERS="$(s3api list-object-versions --bucket "$B" \
        --query 'Versions[].[Key,VersionId,IsLatest]' --output text)" || {
        echo "   ✗ $B: no se pudieron listar las versiones" >&2; return 1; }
    while IFS="$(printf '\t')" read -r K V L; do
        [ -z "$K" ] && continue
        N_VERS=$((N_VERS + 1))
        [ "$V" = "None" ] && V="-"
        local VARG=()
        [ "$V" != "-" ] && VARG=(--version-id "$V")
        # Se guarda el INSTANTE exacto (`RetainUntilDate`, ISO 8601), no una
        # duración: una duración relativa correría la fecha hacia adelante en
        # cada restauración, alargando en silencio una retención archivística
        # que la entidad fijó una sola vez.
        RJSON="$(_leer_json_s3 "retención de $B/$K" \
            s3api get-object-retention --bucket "$B" --key "$K" ${VARG[@]+"${VARG[@]}"} --output json)" || return 1
        INFO="$(printf '%s' "$RJSON" | python3 -c 'import json,sys
d=json.load(sys.stdin)
r=d.get("Retention") or {}
m,u=r.get("Mode"),r.get("RetainUntilDate")
if m and u: print(f"{m}\t{u}")')"
        # La retención LEGAL es independiente de la retención por plazo: un
        # objeto puede tenerla sin tener plazo, y se pierde igual si no se guarda.
        HJSON="$(_leer_json_s3 "retención legal de $B/$K" \
            s3api get-object-legal-hold --bucket "$B" --key "$K" ${VARG[@]+"${VARG[@]}"} --output json)" || return 1
        HOLD="$(printf '%s' "$HJSON" | python3 -c 'import json,sys
d=json.load(sys.stdin)
print((d.get("LegalHold") or {}).get("Status") or "")')"
        [ "$HOLD" = "ON" ] || HOLD="OFF"
        M="-"; U="-"
        if [ -n "$INFO" ]; then M="${INFO%%$(printf '\t')*}"; U="${INFO#*$(printf '\t')}"; fi
        if [ "$M" = "-" ] && [ "$HOLD" = "OFF" ]; then SIN_RETENCION=$((SIN_RETENCION + 1)); continue; fi
        local ES=0; [ "$L" = "True" ] && ES=1
        printf 'OBJETO\t%s\t%s\t%s\t%s\t%s\t%s\n' "$B/$K" "$M" "$U" "$V" "$HOLD" "$ES" >> "$TSV"
        N_OBJ=$((N_OBJ + 1))
    done < <(printf '%s\n' "$VERS" | tr -d '\r' | grep -v '^None$' || true)
    # Un bucket con retención POR DEFECTO no debería tener versiones sin ella: si
    # alguna versión listada no genera fila, o se leyó mal o es una anomalía que
    # el operador debe ver — el restore la dejaría borrable en silencio.
    if [ "$N_OBJ" -ne "$N_VERS" ]; then
        echo "   ✗ $B: $N_VERS versiones listadas pero $N_OBJ con retención capturada ($SIN_RETENCION sin retención ni retención legal)" >&2
        return 1
    fi
    return 0
}

# respaldar_versiones_previas <bucket> <dir> <tsv>
# Copia el CONTENIDO de cada versión previa (IsLatest=0) que tenga COMPLIANCE vigente
# o retención legal ON: el espejo (`s3 sync`) solo copia la versión actual y esa
# evidencia —un índice o un AIP re-sellado sobre la misma clave— se perdía al
# restaurar. Guarda `<dir>/<bucket>/<sha256(clave TAB vid)>` y anota
# `VERSION b/k vid sha256 LastModified` en <tsv>.
# FAIL-CLOSED: devuelve 1 si no pudo leer o descargar alguna, o si la cuenta no cuadra.
# `s3api get-object` escribe en una ruta del anfitrión: el llamador que corre el
# cliente en un contenedor debe montar <dir> en la MISMA ruta (S3_MONTAR).
respaldar_versiones_previas() {
    local B="$1" DIR="$2" TSV="$3" K V L LM RJSON HJSON INFO HOLD M U ID DEST SHA
    local ESPERADAS=0 CAPTURADAS=0 TAB; TAB="$(printf '\t')"
    local VERS
    VERS="$(s3api list-object-versions --bucket "$B" \
        --query 'Versions[].[Key,VersionId,IsLatest,LastModified]' --output text)" || {
        echo "   ✗ $B: no se pudieron listar las versiones previas" >&2; return 1; }
    mkdir -p "$DIR/$B"
    while IFS="$TAB" read -r K V L LM; do
        [ -z "$K" ] && continue
        [ "$L" = "False" ] || continue
        [ "$V" = "None" ] || [ "$V" = "null" ] && continue
        RJSON="$(_leer_json_s3 "retención de $B/$K ($V)" \
            s3api get-object-retention --bucket "$B" --key "$K" --version-id "$V" --output json)" || return 1
        INFO="$(printf '%s' "$RJSON" | python3 -c 'import json,sys
d=json.load(sys.stdin)
r=d.get("Retention") or {}
m,u=r.get("Mode"),r.get("RetainUntilDate")
if m and u: print(f"{m}\t{u}")')"
        HJSON="$(_leer_json_s3 "retención legal de $B/$K ($V)" \
            s3api get-object-legal-hold --bucket "$B" --key "$K" --version-id "$V" --output json)" || return 1
        HOLD="$(printf '%s' "$HJSON" | python3 -c 'import json,sys
d=json.load(sys.stdin)
print((d.get("LegalHold") or {}).get("Status") or "")')"
        M=""; U=""
        if [ -n "$INFO" ]; then M="${INFO%%"$TAB"*}"; U="${INFO#*"$TAB"}"; fi
        # Solo la evidencia que la ley o el plazo protegen: una previa sin ninguna de
        # las dos es borrable por cualquiera y no es motivo para abortar ni copiar.
        if ! { [ "$M" = "COMPLIANCE" ] && [ -n "$(_retencion_vigente "$U")" ]; } && [ "$HOLD" != "ON" ]; then
            continue
        fi
        ESPERADAS=$((ESPERADAS + 1))
        ID="$(printf '%s\t%s' "$K" "$V" | sha256sum | cut -d' ' -f1)"
        DEST="$DIR/$B/$ID"
        if ! s3api get-object --bucket "$B" --key "$K" --version-id "$V" "$DEST" >/dev/null; then
            echo "   ✗ no se pudo descargar la versión previa $B/$K ($V)" >&2; return 1
        fi
        [ -e "$DEST" ] || { echo "   ✗ $B/$K ($V): la descarga no dejó fichero" >&2; return 1; }
        SHA="$(sha256sum "$DEST" | cut -d' ' -f1)"
        printf 'VERSION\t%s\t%s\t%s\t%s\n' "$B/$K" "$V" "$SHA" "${LM:--}" >> "$TSV"
        CAPTURADAS=$((CAPTURADAS + 1))
    done < <(printf '%s\n' "$VERS" | tr -d '\r' | grep -v '^None$' || true)
    # Si la versión ACTUAL de una clave con previas capturadas es un delete marker, el
    # espejo no tiene el fichero y la restauración no sabría si es un borrado o una
    # pérdida: se declara con una línea `MARCADOR b/k vid LastModified`.
    local MARC MK MV MLM
    MARC="$(s3api list-object-versions --bucket "$B" \
        --query 'DeleteMarkers[?IsLatest==`true`].[Key,VersionId,LastModified]' --output text)" || {
        echo "   ✗ $B: no se pudieron listar los delete markers" >&2; return 1; }
    while IFS="$TAB" read -r MK MV MLM; do
        [ -z "$MK" ] && continue
        [ "$MK" = "None" ] && continue
        if grep -qF "VERSION$TAB$B/$MK$TAB" "$TSV" 2>/dev/null; then
            printf 'MARCADOR\t%s\t%s\t%s\n' "$B/$MK" "$MV" "${MLM:--}" >> "$TSV"
        fi
    done < <(printf '%s\n' "$MARC" | tr -d '\r' | grep -v '^None$' || true)
    if [ "$CAPTURADAS" -ne "$ESPERADAS" ]; then
        echo "   ✗ $B: $ESPERADAS versiones previas protegidas pero $CAPTURADAS capturadas" >&2
        return 1
    fi
    [ "$CAPTURADAS" -gt 0 ] && echo "   $B: $CAPTURADAS versiones previas protegidas respaldadas"
    return 0
}

# _segmento_seguro <bucket> <clave> -> 0 solo si NO permite escapar del directorio del
# respaldo: sin vacíos, sin ruta absoluta, sin segmentos `..` y sin caracteres de control.
# El TSV viaja dentro de un respaldo que pudo ser manipulado.
_ruta_segura() {
    local B="$1" K="$2" X
    for X in "$B" "$K"; do
        [ -n "$X" ] || return 1
        case "$X" in /*) return 1 ;; esac
        case "/$X/" in */../*) return 1 ;; esac
        if [[ "$X" =~ [[:cntrl:]] ]]; then return 1; fi
    done
    case "$B" in */*) return 1 ;; esac
    return 0
}

# _version_respaldada <versiones.tsv> <bucket/clave> <version_id> -> 0 si está en el TSV.
_version_respaldada() {
    [ -n "$1" ] && [ -f "$1" ] || return 1
    awk -F'\t' -v o="$2" -v v="$3" '$1=="VERSION" && $2==o && $3==v {f=1} END {exit f?0:1}' "$1"
}

# restaurar_versiones_previas <versiones.tsv> <dir> <retencion.tsv> <mapa> <origen_actual>
# Se llama ANTES del `s3 sync`, con los buckets protegidos ya creados. Sube las
# previas por LastModified ASCENDENTE (la más antigua primero, para que el orden de
# versiones del almacén sea el original), verifica el sha256 de cada fichero contra
# el TSV, y repone la retención sobre el VersionId DEVUELTO por el put (no sobre el
# viejo). Después sube con `put-object` explícito la versión ACTUAL de cada clave
# tocada, desde <origen_actual>/<bucket>/<clave>: si no, el `sync` posterior podría
# saltarla por tamaño y la clave quedaría apuntando a una previa. Escribe en <mapa>
# las líneas `OBJETO b/k vieja nueva` de previas y actuales (acta ADR-027).
# FAIL-CLOSED: devuelve 1 ante cualquier fallo (sha distinto, put, retención).
restaurar_versiones_previas() {
    local VTSV="$1" DIR="$2" RTSV="$3" MAPA="$4" ACTUAL="$5"
    local TAB OBJ VID SHA LM B K ID FICH SHA2 NUEVA INFO MODO HASTA HOLD VIG CLAVES CLAVE VIEJA_ACT N=0
    TAB="$(printf '\t')"
    [ -f "$VTSV" ] || return 0
    CLAVES="$(mktemp)"
    while IFS="$TAB" read -r _ OBJ VID SHA LM; do
        B="${OBJ%%/*}"; K="${OBJ#*/}"
        if ! _ruta_segura "$B" "$K"; then
            echo "   ✗ clave insegura en el respaldo (ruta absoluta, '..' o caracteres de control): rechazada" >&2
            rm -f "$CLAVES"; return 1
        fi
        ID="$(printf '%s\t%s' "$K" "$VID" | sha256sum | cut -d' ' -f1)"
        FICH="$DIR/$B/$ID"
        if [ ! -f "$FICH" ]; then echo "   ✗ falta el contenido de la versión previa $OBJ ($VID)" >&2; rm -f "$CLAVES"; return 1; fi
        SHA2="$(sha256sum "$FICH" | cut -d' ' -f1)"
        if [ "$SHA2" != "$SHA" ]; then
            echo "   ✗ $OBJ ($VID): el sha256 del respaldo NO coincide (esperado $SHA, hay $SHA2)" >&2
            rm -f "$CLAVES"; return 1
        fi
        NUEVA="$(s3api put-object --bucket "$B" --key "$K" --body "$FICH" --query VersionId --output text)" || {
            echo "   ✗ no se pudo subir la versión previa $OBJ ($VID)" >&2; rm -f "$CLAVES"; return 1; }
        if [ -z "$NUEVA" ] || [ "$NUEVA" = "None" ]; then
            echo "   ✗ $OBJ ($VID): el almacén no devolvió VersionId (¿bucket sin versionado?)" >&2
            rm -f "$CLAVES"; return 1
        fi
        INFO="$(awk -F'\t' -v o="$OBJ" -v v="$VID" '$1=="OBJETO" && $2==o && $5==v {print $3 "\t" $4 "\t" $6; exit}' "$RTSV" 2>/dev/null)"
        MODO=""; HASTA=""; HOLD="OFF"
        if [ -n "$INFO" ]; then IFS="$TAB" read -r MODO HASTA HOLD <<< "$INFO"; fi
        VIG=""
        if [ "$MODO" != "-" ] && [ -n "$MODO" ] && [ "$HASTA" != "-" ]; then VIG="$(_retencion_vigente "$HASTA")"; fi
        if [ -n "$VIG" ]; then
            s3api put-object-retention --bucket "$B" --key "$K" --version-id "$NUEVA" \
                --retention "{\"Mode\":\"$MODO\",\"RetainUntilDate\":\"$VIG\"}" >/dev/null || {
                echo "   ✗ no se repuso la retención de la previa $OBJ ($VID -> $NUEVA)" >&2; rm -f "$CLAVES"; return 1; }
        fi
        if [ "$HOLD" = "ON" ]; then
            s3api put-object-legal-hold --bucket "$B" --key "$K" --version-id "$NUEVA" \
                --legal-hold '{"Status":"ON"}' >/dev/null || {
                echo "   ✗ no se repuso la retención legal de la previa $OBJ ($VID -> $NUEVA)" >&2; rm -f "$CLAVES"; return 1; }
        fi
        [ -n "$MAPA" ] && printf 'OBJETO\t%s\t%s\t%s\n' "$OBJ" "$VID" "$NUEVA" >> "$MAPA"
        printf '%s\n' "$OBJ" >> "$CLAVES"
        N=$((N + 1))
    done < <(grep -E "^VERSION$TAB" "$VTSV" | sort -t "$TAB" -k5,5 -s)
    # La versión actual de cada clave tocada se sube la ÚLTIMA y de forma explícita.
    while IFS= read -r CLAVE; do
        B="${CLAVE%%/*}"; K="${CLAVE#*/}"
        if ! _ruta_segura "$B" "$K"; then
            echo "   ✗ clave insegura en el respaldo (ruta absoluta, '..' o caracteres de control): rechazada" >&2
            rm -f "$CLAVES"; return 1
        fi
        # Versión actual = delete marker (declarada por el respaldo): se restaura el borrado
        # tras las previas, no se exige un fichero que nunca existió.
        if grep -qF "MARCADOR$TAB$CLAVE$TAB" "$VTSV" 2>/dev/null; then
            s3api delete-object --bucket "$B" --key "$K" >/dev/null || {
                echo "   ✗ no se pudo restaurar el delete marker de $CLAVE" >&2; rm -f "$CLAVES"; return 1; }
            echo "   $CLAVE: delete marker restaurado (la versión actual era un borrado)"
            continue
        fi
        FICH="$ACTUAL/$B/$K"
        [ -f "$FICH" ] || { echo "   ✗ falta la versión actual de $CLAVE en el respaldo ($FICH)" >&2; rm -f "$CLAVES"; return 1; }
        NUEVA="$(s3api put-object --bucket "$B" --key "$K" --body "$FICH" --query VersionId --output text)" || {
            echo "   ✗ no se pudo subir la versión actual de $CLAVE" >&2; rm -f "$CLAVES"; return 1; }
        VIEJA_ACT="$(awk -F'\t' -v o="$CLAVE" '$1=="OBJETO" && $2==o && $7=="1" {print $5; exit}' "$RTSV" 2>/dev/null)"
        if [ -n "$MAPA" ] && [ -n "$VIEJA_ACT" ] && [ "$VIEJA_ACT" != "-" ] && [ -n "$NUEVA" ] && [ "$NUEVA" != "None" ]; then
            printf 'OBJETO\t%s\t%s\t%s\n' "$CLAVE" "$VIEJA_ACT" "$NUEVA" >> "$MAPA"
        fi
    done < <(sort -u "$CLAVES")
    rm -f "$CLAVES"
    echo "   $N versiones previas restauradas con su retención"
    return 0
}

# _retencion_vigente <HASTA> -> imprime el instante normalizado si sigue en el futuro.
_retencion_vigente() {
    python3 - "$1" <<'PYFECHA'
import datetime, sys
try:
    hasta = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(0)
if hasta > datetime.datetime.now(datetime.UTC):
    print(hasta.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
PYFECHA
}

# reponer_retencion <tsv> [<mapa_version_id.tsv> [<minio-versiones.tsv>]]
#
# FAIL-CLOSED (AREA-08-10): devuelve 0 solo si TODA retención vigente y TODA
# retención legal ACTIVA de la versión actual del respaldo quedó repuesta. Antes
# `put-object-retention ... || true` y un contador informativo: un objeto cuya
# retención no se repuso quedaba borrable y la restauración terminaba en verde.
# Una retención ya vencida se deja como está (reponerla inventaría un plazo).
# Las versiones previas (IsLatest=0) que NO están en `minio-versiones.tsv` (respaldos
# anteriores a B4) no se pueden reponer —el espejo solo copia la versión actual— y se
# REPORTAN, no se callan.
# Si se da `mapa_version_id`, se escribe `OBJETO <b/k> <version vieja> <nueva>`: el
# `preservacion_worm_objeto.version_id` de la BD ya no corresponde a ningún objeto
# tras restaurar, y ADR-027 exige un acta de ese cambio.
reponer_retencion() {
    local TSV="$1" MAPA="${2:-}" VERSIONES="${3:-}"
    local TIPO OBJETO MODO HASTA VERSION HOLD LATEST BK LLAVE VIGENTE NUEVA LINEA
    local ESPERADOS=0 REPUESTOS=0 HOLD_ESPERADOS=0 HOLD_REPUESTOS=0 PREVIAS=0 PREVIAS_CT=0 VIG_PREV
    # MAPA_CONSERVAR=1: el mapa ya lo empezó `restaurar_versiones_previas` (restore.sh).
    if [ -n "$MAPA" ] && [ "${MAPA_CONSERVAR:-0}" != "1" ]; then : > "$MAPA"; fi
    while IFS="$(printf '\t')" read -r TIPO OBJETO MODO HASTA VERSION HOLD LATEST; do
        [ "$TIPO" = "OBJETO" ] || continue
        [ -z "$MODO" ] && MODO="-"; [ -z "$HASTA" ] && HASTA="-"
        [ -z "$HOLD" ] && HOLD="OFF"; [ -z "$LATEST" ] && LATEST=1
        if [ "$LATEST" = "0" ]; then
            # Una previa cuyo contenido SÍ está en `minio-versiones.tsv` ya la repuso
            # `restaurar_versiones_previas` con su retención: no cuenta como perdida.
            if _version_respaldada "$VERSIONES" "$OBJETO" "$VERSION"; then continue; fi
            PREVIAS=$((PREVIAS + 1))
            # Una versión previa bajo COMPLIANCE VIGENTE (p. ej. un índice o un AIP
            # re-sellado sobre la misma clave) es evidencia que la restauración NO
            # recupera: no se calla ni se da por buena.
            if [ "$MODO" = "COMPLIANCE" ] && [ "$HASTA" != "-" ]; then
                VIG_PREV="$(_retencion_vigente "$HASTA")"
                [ -n "$VIG_PREV" ] && PREVIAS_CT=$((PREVIAS_CT + 1))
            fi
            continue
        fi
        BK="${OBJETO%%/*}"
        LLAVE="${OBJETO#*/}"
        VIGENTE=""
        if [ "$MODO" != "-" ] && [ "$HASTA" != "-" ]; then
            VIGENTE="$(_retencion_vigente "$HASTA")"
        fi
        if [ -n "$VIGENTE" ]; then
            ESPERADOS=$((ESPERADOS + 1))
            if s3api put-object-retention --bucket "$BK" --key "$LLAVE" \
                --retention "{\"Mode\":\"$MODO\",\"RetainUntilDate\":\"$VIGENTE\"}" >/dev/null; then
                REPUESTOS=$((REPUESTOS + 1))
            else
                echo "   ✗ no se repuso la retención de $OBJETO ($MODO hasta $VIGENTE)" >&2
            fi
        fi
        if [ "$HOLD" = "ON" ]; then
            HOLD_ESPERADOS=$((HOLD_ESPERADOS + 1))
            if s3api put-object-legal-hold --bucket "$BK" --key "$LLAVE" \
                --legal-hold '{"Status":"ON"}' >/dev/null; then
                HOLD_REPUESTOS=$((HOLD_REPUESTOS + 1))
            else
                echo "   ✗ no se repuso la retención legal de $OBJETO" >&2
            fi
        fi
        if [ -n "$MAPA" ] && { [ -n "$VIGENTE" ] || [ "$HOLD" = "ON" ]; }; then
            NUEVA="$(s3api head-object --bucket "$BK" --key "$LLAVE" --query VersionId --output text 2>/dev/null || true)"
            LINEA="$(printf 'OBJETO\t%s\t%s\t%s' "$OBJETO" "${VERSION:--}" "${NUEVA:--}")"
            grep -qxF -- "$LINEA" "$MAPA" 2>/dev/null || printf '%s\n' "$LINEA" >> "$MAPA"
        fi
    done < "$TSV"
    echo "   $REPUESTOS/$ESPERADOS retenciones y $HOLD_REPUESTOS/$HOLD_ESPERADOS retenciones legales repuestas"
    if [ "$PREVIAS" -gt 0 ]; then
        echo "   AVISO: $PREVIAS versiones previas con retención NO se pueden reponer (no están en minio-versiones.tsv: el espejo solo copia la versión actual)" >&2
    fi
    RETENCION_REPUESTOS="$REPUESTOS"
    RETENCION_PREVIAS_COMPLIANCE="$PREVIAS_CT"
    if [ "$PREVIAS_CT" -gt 0 ]; then
        if [ "${RESTORE_ACEPTAR_PERDIDA_VERSIONES:-}" = "1" ]; then
            echo "   AVISO: $PREVIAS_CT versiones previas bajo COMPLIANCE vigente NO restauradas (aceptado con RESTORE_ACEPTAR_PERDIDA_VERSIONES=1; anótelo en el acta)" >&2
        else
            echo "   ✗ $PREVIAS_CT versiones previas bajo COMPLIANCE vigente NO se pueden restaurar (el espejo solo copia la versión actual)." >&2
            echo "     La evidencia anterior se perdería. Si el responsable lo acepta: RESTORE_ACEPTAR_PERDIDA_VERSIONES=1." >&2
            return 1
        fi
    fi
    if [ "$REPUESTOS" -lt "$ESPERADOS" ] || [ "$HOLD_REPUESTOS" -lt "$HOLD_ESPERADOS" ]; then
        return 1
    fi
    return 0
}

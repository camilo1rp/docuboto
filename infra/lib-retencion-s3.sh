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

# reponer_retencion <tsv> [<mapa_version_id.tsv>]
#
# FAIL-CLOSED (AREA-08-10): devuelve 0 solo si TODA retención vigente y TODA
# retención legal ACTIVA de la versión actual del respaldo quedó repuesta. Antes
# `put-object-retention ... || true` y un contador informativo: un objeto cuya
# retención no se repuso quedaba borrable y la restauración terminaba en verde.
# Una retención ya vencida se deja como está (reponerla inventaría un plazo).
# Las versiones previas (IsLatest=0) NO se pueden reponer —el espejo solo copia la
# versión actual— y se REPORTAN, no se callan.
# Si se da `mapa_version_id`, se escribe `OBJETO <b/k> <version vieja> <nueva>`: el
# `preservacion_worm_objeto.version_id` de la BD ya no corresponde a ningún objeto
# tras restaurar, y ADR-027 exige un acta de ese cambio.
reponer_retencion() {
    local TSV="$1" MAPA="${2:-}"
    local TIPO OBJETO MODO HASTA VERSION HOLD LATEST BK LLAVE VIGENTE NUEVA
    local ESPERADOS=0 REPUESTOS=0 HOLD_ESPERADOS=0 HOLD_REPUESTOS=0 PREVIAS=0 PREVIAS_CT=0 VIG_PREV
    [ -n "$MAPA" ] && : > "$MAPA"
    while IFS="$(printf '\t')" read -r TIPO OBJETO MODO HASTA VERSION HOLD LATEST; do
        [ "$TIPO" = "OBJETO" ] || continue
        [ -z "$MODO" ] && MODO="-"; [ -z "$HASTA" ] && HASTA="-"
        [ -z "$HOLD" ] && HOLD="OFF"; [ -z "$LATEST" ] && LATEST=1
        if [ "$LATEST" = "0" ]; then
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
            printf 'OBJETO\t%s\t%s\t%s\n' "$OBJETO" "${VERSION:--}" "${NUEVA:--}" >> "$MAPA"
        fi
    done < "$TSV"
    echo "   $REPUESTOS/$ESPERADOS retenciones y $HOLD_REPUESTOS/$HOLD_ESPERADOS retenciones legales repuestas"
    if [ "$PREVIAS" -gt 0 ]; then
        echo "   AVISO: $PREVIAS versiones previas con retención NO se pueden reponer (el espejo solo copia la versión actual)" >&2
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

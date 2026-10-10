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
# verificar-ventanilla.sh — AREA-09-07: ¿puede cada entidad recibir radicación pública?
#
# La ventanilla pública responde 503 `ventanilla_no_habilitada` si la entidad no marcó
# una dependencia receptora (`depe_rad_tp2`) o no declaró ningún tipo admisible: es el
# comportamiento correcto (fail-closed), pero se descubría al primer ciudadano. Este
# script consulta `GET /api/v1/public/{tenant}/ventanilla/estado` (sin autenticación) y
# sale con código 1 si ALGUNA entidad no está habilitada, para engancharlo al despliegue.
#
# B2 (ADR-031): también exige `politica_publicada`. Sin política de tratamiento de datos
# (aviso de privacidad, Ley 1581/2012 art. 12) la ventanilla responde 409
# `politica_no_publicada` aunque el organigrama esté listo: es el mismo descubrimiento
# tardío, así que se comprueba aquí y se dice qué falta (`politica_tratamiento_version`
# y `..._url` o `..._texto` en /admin/parametros).
#
# Uso:
#   ./scripts/verificar-ventanilla.sh demo icetex       # entidades por argumento
#   PORTAL_TENANT_SLUG=demo ./scripts/verificar-ventanilla.sh   # o por entorno
#   GATEWAY_URL=https://api.entidad.gov.co ./scripts/verificar-ventanilla.sh demo
#
# Salida: una línea por entidad (`OK` / `NO HABILITADA` / `SIN POLITICA`). Código 0 si todas
# están habilitadas; 1 si alguna no lo está; 2 si no se pudo consultar (red, gateway).

set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:19080}"
SLUGS=("$@")
if [ ${#SLUGS[@]} -eq 0 ] && [ -n "${PORTAL_TENANT_SLUG:-}" ]; then
    SLUGS=("$PORTAL_TENANT_SLUG")
fi
if [ ${#SLUGS[@]} -eq 0 ]; then
    echo "Uso: $0 <slug> [<slug>...]  (o PORTAL_TENANT_SLUG=<slug>)" >&2
    exit 2
fi

estado=0
for slug in "${SLUGS[@]}"; do
    if ! cuerpo="$(curl -fsS --max-time 10 "$GATEWAY_URL/api/v1/public/$slug/ventanilla/estado")"; then
        echo "ERROR  $slug: no se pudo consultar $GATEWAY_URL" >&2
        estado=2
        continue
    fi
    if ! printf '%s' "$cuerpo" | grep -Eq '"habilitada"[[:space:]]*:[[:space:]]*true'; then
        echo "NO HABILITADA  $slug  $cuerpo"
        [ "$estado" -eq 2 ] || estado=1
    elif ! printf '%s' "$cuerpo" | grep -Eq '"politica_publicada"[[:space:]]*:[[:space:]]*true'; then
        echo "SIN POLITICA   $slug  $cuerpo  (publique politica_tratamiento_* en /admin/parametros)"
        [ "$estado" -eq 2 ] || estado=1
    else
        echo "OK             $slug  $cuerpo"
    fi
done
exit "$estado"

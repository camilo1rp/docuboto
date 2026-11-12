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
# seal-secrets.sh — Sella el paquete de secretos (ADR-053 §5).
#
#   ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/seal-secrets.sh
#
# Pide la contraseña dos veces (o la lee de SECRETS_PASSPHRASE_FILE, p. ej. desde
# una memoria USB que se retira después). Esa contraseña NO se guarda en el
# servidor: si se pierde el servidor y con él la contraseña, el paquete no sirve.
#
# Córralo al instalar y después de CADA cambio de secretos (`infra/.env`, sello de
# una entidad nueva, certificado de la TSA, rotación de una KEK). `backup.sh`
# copia el último paquete en cada respaldo y sale con 6 si algún secreto es más
# nuevo que él.
#
# Recuperación (en un servidor nuevo, desde la raíz del proyecto):
#   gpg --pinentry-mode loopback --decrypt secrets-<fecha>.tar.gpg | tar -xf -
#   sha256sum -c MANIFEST && rm MANIFEST

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"
BACKUP_DIR="${BACKUP_DIR:-/srv/orpyca/backups}"
SECRETS_BUNDLE_DIR="${SECRETS_BUNDLE_DIR:-$BACKUP_DIR/secrets}"
MIN_LARGO=16

# shellcheck source=lib-secrets.sh
. "$SCRIPT_DIR/lib-secrets.sh"

command -v gpg >/dev/null 2>&1 || { echo "ERROR: falta gpg." >&2; exit 1; }

if [ -n "${SECRETS_PASSPHRASE_FILE:-}" ]; then
    [ -s "$SECRETS_PASSPHRASE_FILE" ] || { echo "ERROR: '$SECRETS_PASSPHRASE_FILE' está vacío o no existe." >&2; exit 1; }
    CLAVE="$(cat "$SECRETS_PASSPHRASE_FILE")"
else
    [ -t 0 ] || { echo "ERROR: sin terminal; defina SECRETS_PASSPHRASE_FILE." >&2; exit 1; }
    read -r -s -p "Contraseña del paquete de secretos: " CLAVE; echo
    read -r -s -p "Repítala: " OTRA; echo
    [ "$CLAVE" = "$OTRA" ] || { echo "ERROR: las contraseñas no coinciden." >&2; exit 1; }
    unset OTRA
fi
[ "${#CLAVE}" -ge "$MIN_LARGO" ] || { echo "ERROR: la contraseña debe tener al menos $MIN_LARGO caracteres." >&2; exit 1; }

umask 077
SALIDA="$SECRETS_BUNDLE_DIR/secrets-$(date +%Y%m%d_%H%M%S).tar.gpg"
echo "── Sellando: $(secrets_inputs "$PROJECT_ROOT" "$ENV_FILE" | tr '\n' ' ')"
# La contraseña viaja por una tubería (/dev/fd/N), nunca por un fichero ni por argv.
seal_secrets_bundle "$PROJECT_ROOT" "$ENV_FILE" "$SALIDA" <(printf '%s' "$CLAVE")
unset CLAVE
chmod 700 "$SECRETS_BUNDLE_DIR" 2>/dev/null || true
echo "══ Paquete sellado y comprobado: $SALIDA ══"
echo "   Guarde la contraseña fuera de este servidor. Sin ella el paquete no se abre."

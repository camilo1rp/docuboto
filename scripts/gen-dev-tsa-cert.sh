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
# gen-dev-tsa-cert.sh — Genera un par de la TSA (Time-Stamping Authority) de
# DESARROLLO (RSA-2048, auto-firmado, NO ACREDITADO, EKU id-kp-timeStamping
# CRÍTICO) para signature-service (E06, ADR-016, RT-16, Inc.4 — XAdES-T).
#
# A diferencia de gen-dev-signing-cert.sh (sello institucional, POR TENANT),
# la TSA es ÚNICA para todo el despliegue: no hay subcarpeta por tenant. El
# resultado NUNCA es válido para producción: es un certificado auto-firmado
# sin acreditación ONAC — signature-service SIEMPRE reporta
# tsa_acreditado=false para cualquier sello de tiempo producido con este par
# (ver app/core/tsa.py).
#
# Uso:
#   ./scripts/gen-dev-tsa-cert.sh [output-dir] [subdir]
#
#   output-dir   Directorio raíz de la TSA. Por defecto: ./secrets/tsa
#                (montado por docker-compose.yml en /run/secrets/tsa:ro
#                dentro de signature-service).
#   subdir       Subcarpeta bajo output-dir. Por defecto: _dev — el fallback
#                que app/core/keys.py::load_tsa_seal usa cuando
#                settings.tsa_allow_dev_seal=True y no hay par PRINCIPAL
#                montado directamente en <tsa_cert_dir>/tsa.{crt,key}. Pasar
#                "" (vacío) genera el par PRINCIPAL directamente en
#                output-dir, sin subcarpeta.
#
# Ejemplos:
#   ./scripts/gen-dev-tsa-cert.sh                    # secrets/tsa/_dev/tsa.{crt,key} (fallback dev)
#   ./scripts/gen-dev-tsa-cert.sh ./secrets/tsa ""    # secrets/tsa/tsa.{crt,key} (par "principal")
#   ./scripts/gen-dev-tsa-cert.sh /tmp/tsa _dev        # útil para fixtures de test
#
# Requiere: openssl (>= 1.1.1, para -addext).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_ROOT="${1:-$PROJECT_ROOT/secrets/tsa}"
SUBDIR="${2:-_dev}"

if [ -n "$SUBDIR" ]; then
    TARGET_DIR="$OUTPUT_ROOT/$SUBDIR"
else
    TARGET_DIR="$OUTPUT_ROOT"
fi

CERT_PATH="$TARGET_DIR/tsa.crt"
KEY_PATH="$TARGET_DIR/tsa.key"

mkdir -p "$TARGET_DIR"

echo ""
echo "Generando sello de TSA DEV (NO ACREDITADO, EKU timeStamping crítico)"
echo "  Directorio: $TARGET_DIR"
echo ""

openssl req \
    -x509 \
    -newkey rsa:2048 \
    -sha256 \
    -days 3650 \
    -nodes \
    -keyout "$KEY_PATH" \
    -out "$CERT_PATH" \
    -subj "/CN=OrpycaMCP DEV TSA (NO ACREDITADO)/O=orpycamcp" \
    -addext "extendedKeyUsage=critical,timeStamping" \
    -addext "basicConstraints=critical,CA:FALSE"

# Permisos de la clave privada — mismo criterio que `gen-dev-signing-cert.sh`.
#
# El contenedor de `signature-service` corre como uid 999 y este guion escribe
# con el uid de quien lo ejecuta: con la clave en 600 el servicio no puede
# leerla, y el fallo aparece mucho después, al firmar, como un 503 sin causa
# visible. El par `_dev/` es auto-firmado y de pruebas por definición: se deja
# legible para que el entorno funcione sin `sudo`. El de un tenant concreto se
# queda en 600 — si el contenedor no lo puede leer, lo correcto es ajustar la
# propiedad del fichero, no abrir la clave.
if [ "$SUBDIR" = "_dev" ]; then
    chmod 644 "$KEY_PATH"
else
    chmod 600 "$KEY_PATH"
fi
chmod 644 "$CERT_PATH"

echo "  OK  $CERT_PATH"
echo "  OK  $KEY_PATH"
echo ""
echo "Recuerda: este par es SOLO para desarrollo/pruebas. NO lo commitees."
echo "El .gitignore de la raíz debe excluir secrets/ — verifícalo si es la"
echo "primera vez que corres este script en el repo."

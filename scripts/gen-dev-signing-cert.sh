#!/usr/bin/env bash
# gen-dev-signing-cert.sh — Genera un sello de firma XAdES-B de DESARROLLO
# (RSA-2048, auto-firmado, NO ACREDITADO) para signature-service (E06, ADR-016,
# RT-16, Inc.1).
#
# El resultado NUNCA es válido para producción: es un certificado auto-firmado
# sin CA acreditada ONAC — signature-service SIEMPRE reporta
# nivel_conformidad='xades_b_no_acreditado' / acreditado=false para cualquier
# firma producida con este par (ver app/core/keys.py::load_seal).
#
# Uso:
#   ./scripts/gen-dev-signing-cert.sh [tenant-slug] [output-dir]
#
#   tenant-slug   Slug del tenant (ej: demo). Por defecto: _dev (el fallback
#                 que app/core/keys.py usa cuando un tenant no tiene sello
#                 propio montado — cubre TODOS los tenants sin necesidad de
#                 generar un par por cada uno).
#   output-dir    Directorio raíz de sellos. Por defecto: ./secrets/signing
#                 (montado por docker-compose.yml en
#                 /run/secrets/signing:ro dentro de signature-service).
#
# Ejemplos:
#   ./scripts/gen-dev-signing-cert.sh                  # _dev/  (fallback global)
#   ./scripts/gen-dev-signing-cert.sh demo              # demo/  (sello propio del tenant demo)
#   ./scripts/gen-dev-signing-cert.sh _dev /tmp/signing  # útil para fixtures de test
#
# Requiere: openssl.

set -euo pipefail

SLUG="${1:-_dev}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OUTPUT_ROOT="${2:-$PROJECT_ROOT/secrets/signing}"

TARGET_DIR="$OUTPUT_ROOT/$SLUG"
CERT_PATH="$TARGET_DIR/seal.crt"
KEY_PATH="$TARGET_DIR/seal.key"

mkdir -p "$TARGET_DIR"

echo ""
echo "Generando sello de firma DEV (NO ACREDITADO) para tenant: $SLUG"
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
    -subj "/CN=OrpycaMCP DEV Seal (NO ACREDITADO)/O=$SLUG"

# Permisos de la clave privada.
#
# `signature-service` corre en el contenedor como uid 999 (`orpycamcp`) y este
# guion escribe en el HOST con el uid de quien lo ejecuta. Con la clave en 600
# el servicio NO PUEDE LEERLA: `load_seal` levanta
# `PermissionError: /run/secrets/signing/_dev/seal.key`, que sale como 500 y
# deja el índice en `pendiente_firma` sin que nada explique por qué. Es decir:
# el guion generaba material que el servicio no podía usar, y el fallo aparecía
# mucho después, al intentar firmar.
#
# Se distingue por directorio, no por comodidad:
#   * `_dev/`  — el par COMPARTIDO de desarrollo, auto-firmado y no acreditado
#                por definición (lo dice el encabezado de este guion). Ahí la
#                clave queda legible: lo que protege no vale nada, y a cambio el
#                entorno de pruebas funciona sin `sudo`.
#   * un slug  — material de un tenant concreto. Se queda en 600. Si el
#                contenedor no puede leerlo, lo correcto es ajustar la
#                propiedad del fichero, no abrir la clave.
if [ "$SLUG" = "_dev" ]; then
    chmod 644 "$KEY_PATH"
else
    chmod 600 "$KEY_PATH"
fi
chmod 644 "$CERT_PATH"

echo "  OK  $CERT_PATH"
echo "  OK  $KEY_PATH"
echo ""
if [ "$SLUG" = "_dev" ]; then
    echo "  NOTA: la clave de _dev/ queda legible por cualquier usuario del"
    echo "  equipo, a proposito: es un par auto-firmado de pruebas y asi el"
    echo "  contenedor (uid 999) puede leerla sin sudo. Para el sello de un"
    echo "  tenant real, el guion la deja en 600."
    echo ""
fi
echo "Recuerda: este par es SOLO para desarrollo/pruebas. NO lo commitees."
echo "El .gitignore de la raíz debe excluir secrets/ — verifícalo si es la"
echo "primera vez que corres este script en el repo."

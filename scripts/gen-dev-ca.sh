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
# gen-dev-ca.sh — Genera una CA raíz de DESARROLLO (RSA-2048, auto-firmada,
# NO ACREDITADA) y re-emite el sello institucional de un tenant firmado por
# ella, más una CRL vacía firmada por esa misma CA — habilita XAdES-LT/LTA en
# signature-service (E06, ADR-016, RT-16).
#
# LÍNEA ROJA DE SEGURIDAD: la clave privada de la CA (ca.key) se escribe
# SIEMPRE en un directorio SEPARADO del árbol de sellos (`ca-dir`, por
# defecto `./secrets/ca-authority/`) que NINGÚN `docker-compose.yml` de este
# repo monta dentro de signature-service — solo `./secrets/signing` y
# `./secrets/tsa` están montados (:ro). signature-service consume
# EXCLUSIVAMENTE los artefactos PÚBLICOS de esa CA (`ca.crt`, `crl.der`),
# copiados al árbol de sellos junto al par del tenant — jamás la clave.
#
# El resultado NUNCA es válido para producción: la CA es auto-firmada, sin
# acreditación ONAC — signature-service SIEMPRE reporta
# nivel_conformidad='xades_lta_no_acreditado' / acreditado=false para
# cualquier firma que escale a XAdES-LT/LTA con este material (ver
# app/providers/local.py, RF-FIR-15).
#
# Uso:
#   ./scripts/gen-dev-ca.sh [tenant-slug] [signing-dir] [ca-dir]
#
#   tenant-slug   Slug del tenant a re-emitir (ej: demo). Por defecto: _dev
#                 (el fallback global que app/core/keys.py::load_seal usa
#                 cuando un tenant no tiene sello propio montado).
#   signing-dir   Directorio raíz de sellos (el mismo que usa
#                 gen-dev-signing-cert.sh). Por defecto: ./secrets/signing
#                 (montado :ro en /run/secrets/signing dentro de
#                 signature-service). El script exige que
#                 <signing-dir>/<tenant-slug>/seal.{crt,key} YA exista —
#                 corre primero gen-dev-signing-cert.sh si hace falta.
#   ca-dir        Directorio de la CA (clave privada INCLUIDA). Por defecto:
#                 ./secrets/ca-authority — NUNCA lo montes en un contenedor.
#                 Si ya existe una CA en este directorio, se REUTILIZA (no se
#                 regenera) — así varios tenants comparten la misma CA raíz
#                 para ejercitar el aislamiento CA<->tenant en pruebas.
#
# Ejemplos:
#   ./scripts/gen-dev-ca.sh                      # re-emite _dev/ con la CA compartida
#   ./scripts/gen-dev-ca.sh demo                  # re-emite el sello del tenant demo
#   ./scripts/gen-dev-ca.sh tenant-a /tmp/signing /tmp/ca   # útil para fixtures de test
#
# Requiere: openssl.

set -euo pipefail

SLUG="${1:-_dev}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SIGNING_DIR="${2:-$PROJECT_ROOT/secrets/signing}"
CA_DIR="${3:-$PROJECT_ROOT/secrets/ca-authority}"

TARGET_DIR="$SIGNING_DIR/$SLUG"
CA_KEY="$CA_DIR/ca.key"
CA_CERT="$CA_DIR/ca.crt"
CA_DB="$CA_DIR/db"

if [ ! -f "$TARGET_DIR/seal.crt" ] || [ ! -f "$TARGET_DIR/seal.key" ]; then
    echo "ERROR: no existe $TARGET_DIR/seal.{crt,key} — corre primero:" >&2
    echo "  ./scripts/gen-dev-signing-cert.sh $SLUG $SIGNING_DIR" >&2
    exit 1
fi

mkdir -p "$CA_DIR" "$CA_DB"

echo ""
if [ -f "$CA_KEY" ] && [ -f "$CA_CERT" ]; then
    echo "Reutilizando CA de DESARROLLO existente en: $CA_DIR"
else
    echo "Generando CA raíz de DESARROLLO (NO ACREDITADA) en: $CA_DIR"
    echo ""
    openssl req \
        -x509 \
        -newkey rsa:2048 \
        -sha256 \
        -days 3650 \
        -nodes \
        -keyout "$CA_KEY" \
        -out "$CA_CERT" \
        -subj "/CN=OrpycaMCP DEV CA (NO ACREDITADA)/O=orpycamcp" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign"
    chmod 600 "$CA_KEY"
    chmod 644 "$CA_CERT"
fi

: > "$CA_DB/index.txt"
[ -f "$CA_DB/crlnumber" ] || echo 01 > "$CA_DB/crlnumber"
cat > "$CA_DIR/openssl-ca.cnf" <<EOF
[ca]
default_ca = dev_ca

[dev_ca]
certificate     = $CA_CERT
private_key     = $CA_KEY
database        = $CA_DB/index.txt
crlnumber       = $CA_DB/crlnumber
default_crl_days = 3650
default_md      = sha256
EOF

echo ""
echo "Re-emitiendo sello de $SLUG firmado por la CA de DESARROLLO..."
SEAL_KEY="$TARGET_DIR/seal.key"
SEAL_CSR="$TARGET_DIR/seal.csr"
SEAL_CERT="$TARGET_DIR/seal.crt"

# Reutiliza la clave privada del sello YA existente (gen-dev-signing-cert.sh)
# — solo se re-emite el CERTIFICADO, firmado ahora por la CA en vez de
# auto-firmado. La clave del sello NUNCA sale de $TARGET_DIR (ya montado
# :ro en signature-service — sin cambios respecto al flujo Inc.1).
openssl req -new -key "$SEAL_KEY" -out "$SEAL_CSR" \
    -subj "/CN=OrpycaMCP DEV Seal (NO ACREDITADO)/O=$SLUG"
openssl x509 -req -in "$SEAL_CSR" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
    -days 825 -sha256 -out "$SEAL_CERT"
rm -f "$SEAL_CSR"

echo ""
echo "Generando CRL (vacía, 0 revocados) firmada por la CA..."
openssl ca -gencrl \
    -keyfile "$CA_KEY" -cert "$CA_CERT" \
    -config "$CA_DIR/openssl-ca.cnf" \
    -out "$CA_DIR/crl.pem"
openssl crl -in "$CA_DIR/crl.pem" -outform DER -out "$CA_DIR/crl.der"
rm -f "$CA_DIR/crl.pem"

echo ""
echo "Provisionando material PÚBLICO en el directorio del sello ($TARGET_DIR)..."
cp "$CA_CERT" "$TARGET_DIR/ca.crt"
cp "$CA_DIR/crl.der" "$TARGET_DIR/crl.der"
chmod 644 "$TARGET_DIR/ca.crt" "$TARGET_DIR/crl.der"

echo ""
echo "  OK  $TARGET_DIR/seal.crt   (re-emitido, issuer = CA de desarrollo)"
echo "  OK  $TARGET_DIR/ca.crt     (público — ancla de confianza)"
echo "  OK  $TARGET_DIR/crl.der    (público — CRL firmada por la CA)"
echo ""
echo "La clave privada de la CA NUNCA sale de $CA_DIR — no montes ese"
echo "directorio en ningún contenedor. Activa SIGNER_LT_ENABLED=true en"
echo "signature-service para que /sign-xml escale a XAdES-LTA con este"
echo "material."
echo ""
echo "Recuerda: este par es SOLO para desarrollo/pruebas. NO lo commitees."
echo "El .gitignore de la raíz debe excluir secrets/ — verifícalo si es la"
echo "primera vez que corres este script en el repo."

#!/usr/bin/env bash
# gen-dev-ocsp-responder.sh — Genera un par de responder OCSP DELEGADO de
# DESARROLLO (RSA-2048, EKU id-kp-OCSPSigning + extensión
# id-pkix-ocsp-nocheck 1.3.6.1.5.5.7.48.1.5), emitido OFFLINE por la CA local
# de desarrollo (scripts/gen-dev-ca.sh) — habilita OCSP stapled en XAdES-LT
# para signature-service (E06, RF-FIR-15).
#
# LÍNEA ROJA DE SEGURIDAD: igual que gen-dev-ca.sh, la clave privada de la CA
# (ca.key) NUNCA sale de `ca-dir` (por defecto ./secrets/ca-authority/, NO
# montado en ningún contenedor). Este script solo LEE ca.key/ca.crt de ahí
# para firmar el certificado del responder — y copia SOLO el par PÚBLICO del
# responder (ocsp.crt) más su clave (ocsp.key, del propio responder, NO de la
# CA) al árbol de sellos (`signing-dir`), donde signature-service ya monta
# `./secrets/signing` (:ro).
#
# El resultado NUNCA es válido para producción: la CA que emite este
# responder es auto-firmada, sin acreditación ONAC — signature-service
# SIEMPRE reporta acreditado=false para cualquier firma que incluya OCSP
# stapled con este material (ver app/core/ocsp.py, RF-FIR-15).
#
# Requiere: haber corrido primero scripts/gen-dev-ca.sh para el mismo
# `signing-dir`/`ca-dir` (necesita ca.key/ca.crt para firmar el responder).
#
# Uso:
#   ./scripts/gen-dev-ocsp-responder.sh [tenant-slug] [signing-dir] [ca-dir]
#
#   tenant-slug   Slug del tenant a provisionar (ej: demo). Por defecto: _dev
#                 (el fallback global que app/core/keys.py::load_seal usa
#                 cuando un tenant no tiene sello propio montado).
#   signing-dir   Directorio raíz de sellos (el mismo que usa
#                 gen-dev-signing-cert.sh/gen-dev-ca.sh). Por defecto:
#                 ./secrets/signing (montado :ro en /run/secrets/signing
#                 dentro de signature-service). Exige que
#                 <signing-dir>/<tenant-slug>/{seal,ca}.{crt,key} YA
#                 existan (seal de gen-dev-signing-cert.sh, ca.crt de
#                 gen-dev-ca.sh).
#   ca-dir        Directorio de la CA (clave privada INCLUIDA). Por defecto:
#                 ./secrets/ca-authority — el MISMO default que
#                 gen-dev-ca.sh (CA compartida entre tenants salvo que se
#                 pase un ca-dir distinto). NUNCA lo montes en un
#                 contenedor.
#
# Ejemplos:
#   ./scripts/gen-dev-ca.sh                          # primero: CA + sello _dev/
#   ./scripts/gen-dev-ocsp-responder.sh               # luego: responder OCSP para _dev/
#   ./scripts/gen-dev-ocsp-responder.sh demo          # responder para el tenant demo
#
# Requiere: openssl (>= 1.1.1, para -addext).

set -euo pipefail

SLUG="${1:-_dev}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SIGNING_DIR="${2:-$PROJECT_ROOT/secrets/signing}"
CA_DIR="${3:-$PROJECT_ROOT/secrets/ca-authority}"

TARGET_DIR="$SIGNING_DIR/$SLUG"
CA_KEY="$CA_DIR/ca.key"
CA_CERT="$CA_DIR/ca.crt"

if [ ! -f "$CA_KEY" ] || [ ! -f "$CA_CERT" ]; then
    echo "ERROR: no existe $CA_DIR/ca.{key,crt} — corre primero:" >&2
    echo "  ./scripts/gen-dev-ca.sh $SLUG $SIGNING_DIR $CA_DIR" >&2
    exit 1
fi

if [ ! -f "$TARGET_DIR/seal.crt" ]; then
    echo "ERROR: no existe $TARGET_DIR/seal.crt — corre primero:" >&2
    echo "  ./scripts/gen-dev-signing-cert.sh $SLUG $SIGNING_DIR" >&2
    echo "  ./scripts/gen-dev-ca.sh $SLUG $SIGNING_DIR $CA_DIR" >&2
    exit 1
fi

RESPONDER_KEY="$TARGET_DIR/ocsp.key"
RESPONDER_CSR="$TARGET_DIR/ocsp.csr"
RESPONDER_CERT="$TARGET_DIR/ocsp.crt"
EXT_CNF="$TARGET_DIR/.ocsp-responder-ext.cnf"

echo ""
echo "Generando responder OCSP DELEGADO de DESARROLLO (NO ACREDITADO) para $SLUG..."
echo ""

openssl genrsa -out "$RESPONDER_KEY" 2048 2>/dev/null
chmod 600 "$RESPONDER_KEY"

openssl req -new -key "$RESPONDER_KEY" -out "$RESPONDER_CSR" \
    -subj "/CN=OrpycaMCP DEV OCSP Responder (NO ACREDITADO)/O=$SLUG"

# extendedKeyUsage=OCSPSigning (id-kp-OCSPSigning, 1.3.6.1.5.5.7.3.9) +
# id-pkix-ocsp-nocheck (1.3.6.1.5.5.7.48.1.5, valor NULL DER = 05:00) — RFC
# 6960 §4.2.2.2. basicConstraints=CA:FALSE: el responder NUNCA es una CA.
cat > "$EXT_CNF" <<EOF
extendedKeyUsage = OCSPSigning
basicConstraints = CA:FALSE
1.3.6.1.5.5.7.48.1.5 = DER:05:00
EOF

openssl x509 -req -in "$RESPONDER_CSR" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
    -days 397 -sha256 -out "$RESPONDER_CERT" \
    -extfile "$EXT_CNF"

rm -f "$RESPONDER_CSR" "$EXT_CNF"
chmod 644 "$RESPONDER_CERT"

echo ""
echo "  OK  $RESPONDER_CERT   (EKU OCSPSigning + id-pkix-ocsp-nocheck, issuer = CA de desarrollo)"
echo "  OK  $RESPONDER_KEY    (clave privada del RESPONDER — nunca la de la CA)"
echo ""
echo "La clave privada de la CA NUNCA sale de $CA_DIR — no montes ese"
echo "directorio en ningún contenedor. Activa SIGNER_OCSP_ENABLED=true (y ya"
echo "debe estar SIGNER_LT_ENABLED=true, con ca.crt/crl.der montados — ver"
echo "gen-dev-ca.sh) en signature-service para que /sign-xml incluya OCSP"
echo "stapled en XAdES-LT/LTA con este material."
echo ""
echo "Recuerda: este par es SOLO para desarrollo/pruebas. NO lo commitees."
echo "El .gitignore de la raíz debe excluir secrets/ — verifícalo si es la"
echo "primera vez que corres este script en el repo."

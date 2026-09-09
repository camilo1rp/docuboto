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
# gen-user-cert.sh — Emisor OFFLINE de certificados de firma PERSONAL
# por-usuario (E17/F4, Incremento 1, custodia servidor — postura B, marco
# honesto "no acreditado"). Firma un CSR ya generado por auth-service
# (POST /api/v1/auth/me/signing-key/enroll) con una sub-CA de USUARIOS
# propia, DISTINTA de la CA de sellos institucionales de gen-dev-ca.sh —
# nunca se reutiliza la misma raíz para sellos de sistema y certificados de
# persona natural.
#
# LÍNEA ROJA DE SEGURIDAD: este script SOLO se ejecuta FUERA de cualquier
# contenedor Docker — la clave privada de la CA de usuarios (ca-users.key)
# NUNCA se monta en signature-service ni en auth-service. auth-service
# custodia únicamente la clave PRIVADA DEL USUARIO (cifrada en reposo,
# app/core/signing_keys.py) y el certificado PÚBLICO ya emitido — jamás la
# clave de esta CA.
#
# El operador que corre este script DEBE validar el subject del CSR
# (CN/email) contra un canal fuera de banda (p. ej. el ticket de solicitud
# del usuario) ANTES de firmar — este script hace solo una validación
# SINTÁCTICA (CSR bien formado, clave RSA >= 2048 bits, CN presente), NUNCA
# una validación de identidad real.
#
# El resultado NUNCA es acreditado (ONAC): mismo nivel legal que la firma
# electrónica simple (art. 7 Ley 527/1999) — signature-service SIEMPRE
# reporta nivel_conformidad='xades_personal_no_acreditado' / acreditado=false
# para cualquier firma producida con un certificado de esta CA.
#
# Uso:
#   ./scripts/gen-user-cert.sh <csr-file> [ca-dir] [out-dir]
#
#   csr-file   Ruta al CSR PEM devuelto por POST /me/signing-key/enroll
#              (el operador lo obtiene del usuario por un canal fuera de
#              banda — este script NUNCA llama a auth-service para leerlo).
#   ca-dir     Directorio de la CA de usuarios (clave privada INCLUIDA).
#              Por defecto: ./secrets/ca-authority-users — NUNCA lo montes
#              en un contenedor. Si ya existe, se REUTILIZA.
#   out-dir    Directorio de salida del certificado emitido. Por defecto:
#              el mismo directorio que csr-file.
#
# Tras emitir, carga el certificado en auth-service con:
#   curl -X POST http://localhost:19001/internal/signing-key/activate \
#        -H "X-Internal-Token: $SIGNING_KEY_INTERNAL_TOKEN" \
#        -H "X-Tenant-Slug: <slug>" \
#        -H "Content-Type: application/json" \
#        -d "{\"user_id\": \"<uuid>\", \"cert_pem\": $(python3 -c 'import json,sys;print(json.dumps(open(sys.argv[1]).read()))' <out-dir>/user.crt)}"
#
# Requiere: openssl.

set -euo pipefail

CSR_FILE="${1:?Uso: gen-user-cert.sh <csr-file> [ca-dir] [out-dir]}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CA_DIR="${2:-$PROJECT_ROOT/secrets/ca-authority-users}"
OUT_DIR="${3:-$(dirname "$CSR_FILE")}"

CA_KEY="$CA_DIR/ca-users.key"
CA_CERT="$CA_DIR/ca-users.crt"
OUT_CERT="$OUT_DIR/$(basename "${CSR_FILE%.csr}").crt"

if [ ! -f "$CSR_FILE" ]; then
    echo "ERROR: no existe $CSR_FILE" >&2
    exit 1
fi

echo ""
echo "Validando sintaxis del CSR..."
openssl req -in "$CSR_FILE" -noout -verify
KEY_BITS="$(openssl req -in "$CSR_FILE" -noout -text | grep -o 'Public-Key: ([0-9]* bit)' | grep -o '[0-9]*' || true)"
if [ -z "$KEY_BITS" ] || [ "$KEY_BITS" -lt 2048 ]; then
    echo "ERROR: el CSR no lleva una clave RSA de al menos 2048 bits (encontrado: ${KEY_BITS:-desconocido})" >&2
    exit 1
fi
CSR_SUBJECT="$(openssl req -in "$CSR_FILE" -noout -subject)"
if ! echo "$CSR_SUBJECT" | grep -q "CN *="; then
    echo "ERROR: el CSR no lleva Common Name (CN)" >&2
    exit 1
fi
echo "  Subject del CSR: $CSR_SUBJECT"
echo ""
echo ">>> DETENTE: valida este subject contra el canal fuera de banda del"
echo ">>> usuario (ticket de solicitud) ANTES de continuar. Este script NO"
echo ">>> valida identidad real, solo sintaxis."
echo ""

mkdir -p "$CA_DIR" "$OUT_DIR"

# Espejo PÚBLICO de la sub-CA (E17/F4, Incremento 2 — Baja #4): auth-service
# monta SOLO este directorio (./secrets/ca-users-public, ver
# docker-compose.yml -> SIGNING_KEY_CA_USERS_CERT_PATH) para verificar
# leaf.verify_directly_issued_by(sub_ca) en /internal/signing-key/activate —
# NUNCA $CA_DIR (que contiene ca-users.key) se monta en ningún contenedor.
CA_PUBLIC_DIR="$PROJECT_ROOT/secrets/ca-users-public"

if [ -f "$CA_KEY" ] && [ -f "$CA_CERT" ]; then
    echo "Reutilizando sub-CA de USUARIOS existente en: $CA_DIR"
else
    # Guarda contra el fallo SILENCIOSO más caro de este script: si $CA_DIR no
    # contiene una CA, la genera. Con un ca-dir equivocado —un argumento de más,
    # un comando pegado junto al siguiente— eso crea una raíz de confianza
    # PARALELA y, peor, sobrescribe el espejo público que auth-service monta.
    # El sistema deja de confiar en la CA que firmó las credenciales YA activas,
    # y no lo dice: la emisión termina en verde.
    #
    # Ocurrió el 2026-09-05: `gen-user-cert.sh <csr> docker run …` interpretó
    # "docker" como ca-dir, fabricó una CA nueva ahí, firmó con ella y dejó el
    # espejo apuntando a esa — con el certificado ACTIVO de otro usuario colgando
    # de la CA anterior.
    #
    # Regla: si ya hay un espejo público, ya hay una raíz en juego y este script
    # no la reemplaza por iniciativa propia. Crear la primera sigue permitido
    # (es el camino del clon limpio).
    if [ -f "$CA_PUBLIC_DIR/ca-users.crt" ] && [ "${ORPYCA_PERMITIR_NUEVA_CA:-0}" != "1" ]; then
        echo "ERROR: no hay sub-CA de usuarios en '$CA_DIR', pero YA existe un espejo" >&2
        echo "       público en '$CA_PUBLIC_DIR/ca-users.crt'." >&2
        echo "" >&2
        echo "  Generar una CA nueva aquí reemplazaría la raíz de confianza y dejaría" >&2
        echo "  sin validar los certificados ya emitidos. Casi siempre significa que el" >&2
        echo "  ca-dir es incorrecto: revisa el segundo argumento." >&2
        echo "" >&2
        echo "  Si de verdad quieres una raíz nueva: ORPYCA_PERMITIR_NUEVA_CA=1 $0 ..." >&2
        exit 1
    fi
    echo "Generando sub-CA de USUARIOS de DESARROLLO (NO ACREDITADA) en: $CA_DIR"
    echo "(DISTINTA de la CA de sellos institucionales de gen-dev-ca.sh)"
    openssl req \
        -x509 \
        -newkey rsa:2048 \
        -sha256 \
        -days 3650 \
        -nodes \
        -keyout "$CA_KEY" \
        -out "$CA_CERT" \
        -subj "/CN=OrpycaMCP DEV CA Usuarios (NO ACREDITADA)/O=orpycamcp" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign"
    chmod 600 "$CA_KEY"
    chmod 644 "$CA_CERT"
fi

mkdir -p "$CA_PUBLIC_DIR"
cp "$CA_CERT" "$CA_PUBLIC_DIR/ca-users.crt"
chmod 644 "$CA_PUBLIC_DIR/ca-users.crt"
echo "  Espejo público actualizado: $CA_PUBLIC_DIR/ca-users.crt (auth-service la monta desde aquí)"

echo ""
echo "Emitiendo certificado de firma personal (825 días, NO ACREDITADO)..."
openssl x509 -req -in "$CSR_FILE" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" -CAcreateserial \
    -days 825 -sha256 \
    -extfile <(printf 'keyUsage=critical,digitalSignature,nonRepudiation\nextendedKeyUsage=clientAuth\n') \
    -out "$OUT_CERT"

echo ""
echo "  OK  $OUT_CERT"
echo ""
echo "Carga el certificado en auth-service con POST /internal/signing-key/activate"
echo "(ver el encabezado de este script para el ejemplo curl completo)."
echo ""
echo "La clave privada de esta CA NUNCA sale de $CA_DIR — no la montes en"
echo "ningún contenedor. Este par es SOLO para desarrollo/pruebas — NO lo"
echo "commitees. Verifica que el .gitignore de la raíz excluye secrets/."

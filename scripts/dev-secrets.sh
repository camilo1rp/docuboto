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
# dev-secrets.sh — Genera los secretos de DESARROLLO en `.env`, al azar.
#
# ## Por qué existe, y por qué no se pone un valor por defecto en el compose
#
# `signature-service` no arranca sin `SIGNATURE_SECRET`: su guardián rechaza el
# `change-me-in-production` del compose. Eso está BIEN y no se toca. La tentación
# es copiar lo que hace `MCP_PROPOSAL_SECRET`, que trae un default conocido
# (`orpycamcp_dev_proposal_secret`) y por eso sí arranca — pero ese patrón es el
# DÉBIL de los dos:
#
#   * Con `change-me-in-production`, un despliegue que olvide definirlo **no
#     arranca**. Falla cerrado y en voz alta.
#   * Con un default conocido, arranca tan feliz **con un secreto que está en un
#     repositorio público**. Falla abierto y en silencio, que es peor.
#
# Así que en vez de debilitar el guardián, este script pone el valor donde van
# los valores locales: un `.env` que Docker Compose lee solo y que está
# gitignorado. Y lo genera **al azar**, para que ni siquiera el secreto de
# desarrollo sea uno que cualquiera pueda leer en el código.
#
# Es idempotente: no pisa una variable que ya esté en `.env`.
#
#     ./scripts/dev-secrets.sh
#     docker compose up -d

set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=".env"

# Secretos que un entorno de desarrollo necesita para que TODOS los servicios
# arranquen Y funcionen. Si mañana otro servicio suma un guardián, se añade aquí.
#
# ## El FORMATO importa, y equivocarse no se nota hasta que alguien pulsa
#
# Hay dos familias y **no son intercambiables**:
#
#   * `hmac`   — secreto de firma HMAC, se consume como texto crudo
#                (`.encode()`). Cualquier cadena larga vale.
#   * `aes256` — clave de cifrado en reposo. El servicio la decodifica de
#                **base64 y exige exactamente 32 bytes** (AES-256), y se niega
#                fail-closed si no cuadra.
#
# Este script generaba `openssl rand -hex 32` para todo. Para las HMAC está
# bien; para las AES-256 producía 64 caracteres hexadecimales que, leídos como
# base64, decodifican a **48 bytes** — y AES-256 pide 32. Resultado:
# `SIGNING_KEY_ENCRYPTION_KEY` quedaba presente, con pinta correcta, y era
# INSERVIBLE. El fallo no aparecía al arrancar sino al usar la función, como un
# 500 opaco — la credencial de firma personal no se podía enrolar y nadie sabía
# por qué.
SECRETOS=(
  "SIGNATURE_SECRET:hmac"
  "MCP_PROPOSAL_SECRET:hmac"
  "SIGNING_KEY_ENCRYPTION_KEY:aes256"
  # Cifra en reposo el secreto TOTP de cada usuario. Sin ella el segundo factor
  # no se puede enrolar, y como la credencial de firma EXIGE un TOTP activo,
  # faltaba aquí toda la cadena de firma personal.
  "TOTP_ENCRYPTION_KEY:aes256"
)

aleatorio_hmac() {
  # `openssl` si está; si no, Python, que ya es requisito del proyecto.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c "import secrets; print(secrets.token_hex(32))"
  fi
}

aleatorio_aes256() {
  # 32 bytes crudos en base64 — exactamente lo que decodifican los servicios.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32
  else
    python3 -c "import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
  fi
}

# ¿El valor que ya está en `.env` sirve de verdad para su familia? Sin esta
# comprobación la idempotencia se vuelve en contra: una clave mal formada se
# «respeta» para siempre y el arreglo nunca llega a la máquina que lo necesita.
sirve_aes256() {
  local veredicto
  veredicto="$(python3 -c 'import base64, binascii, sys
try:
    print("si" if len(base64.b64decode(sys.argv[1], validate=True)) == 32 else "no")
except (binascii.Error, ValueError):
    print("no")' "$1")"
  [ "$veredicto" = "si" ]
}

touch "$ENV_FILE"
creados=0
for entrada in "${SECRETOS[@]}"; do
  nombre="${entrada%%:*}"
  familia="${entrada##*:}"

  if grep -qE "^${nombre}=" "$ENV_FILE"; then
    actual="$(grep -E "^${nombre}=" "$ENV_FILE" | head -n1 | cut -d= -f2-)"
    if [ "$familia" = "aes256" ] && ! sirve_aes256 "$actual"; then
      # Reemplazar una clave de cifrado da miedo con razón: normalmente dejaría
      # ilegible lo que cifró. Aquí no puede haber cifrado NADA, porque el
      # servicio se niega a usarla mientras no decodifique a 32 bytes.
      # Sustituirla es la única salida; conservarla solo perpetúa el fallo.
      sed -i.bak "s|^${nombre}=.*|${nombre}=$(aleatorio_aes256)|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
      echo "  ↻ ${nombre} estaba en un formato inservible (no son 32 bytes en base64): regenerado"
      creados=$((creados + 1))
      continue
    fi
    echo "  · ${nombre} ya estaba en ${ENV_FILE}, se respeta"
    continue
  fi

  if [ "$familia" = "aes256" ]; then
    valor="$(aleatorio_aes256)"
  else
    valor="$(aleatorio_hmac)"
  fi
  printf '%s=%s\n' "$nombre" "$valor" >> "$ENV_FILE"
  echo "  ✓ ${nombre} generado (${familia})"
  creados=$((creados + 1))
done

if [ "$creados" -gt 0 ]; then
  echo
  echo "Escritos en ${ENV_FILE} (gitignorado: NO se commitea, y no debe)."
  echo "Docker Compose lo lee solo. Levanta o recrea los servicios para que apliquen:"
  echo "    docker compose up -d --force-recreate signature-service mcp-server auth-service"
fi

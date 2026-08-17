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
# arranquen. Si mañana otro servicio suma un guardián, se añade aquí.
SECRETOS=(
  SIGNATURE_SECRET
  MCP_PROPOSAL_SECRET
  SIGNING_KEY_ENCRYPTION_KEY
)

aleatorio() {
  # `openssl` si está; si no, Python, que ya es requisito del proyecto.
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python3 -c "import secrets; print(secrets.token_hex(32))"
  fi
}

touch "$ENV_FILE"
creados=0
for nombre in "${SECRETOS[@]}"; do
  if grep -qE "^${nombre}=" "$ENV_FILE"; then
    echo "  · ${nombre} ya estaba en ${ENV_FILE}, se respeta"
    continue
  fi
  printf '%s=%s\n' "$nombre" "$(aleatorio)" >> "$ENV_FILE"
  echo "  ✓ ${nombre} generado"
  creados=$((creados + 1))
done

if [ "$creados" -gt 0 ]; then
  echo
  echo "Escritos en ${ENV_FILE} (gitignorado: NO se commitea, y no debe)."
  echo "Docker Compose lo lee solo. Levanta o recrea los servicios para que apliquen:"
  echo "    docker compose up -d --force-recreate signature-service mcp-server"
fi

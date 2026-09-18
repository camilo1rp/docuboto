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
# Reúne el esquema OpenAPI de CADA servicio, para que el censo del MCP
# (`scripts/censo_mcp.py`) no herede el punto ciego del agregador del gateway:
# ese compone solo siete de los once, y el censo publicaba porcentajes sobre un
# universo que no incluía, entre otros, el circuito de firma entero.
#
# Se le pregunta a la app de cada servicio, no a un contenedor levantado: así
# funciona con la pila abajo y no depende de que la red esté sana.
#
# El repositorio se monta SOLO LECTURA y el esquema sale por stdout: el
# contenedor corre como root y un montaje de escritura deja ficheros de root en
# el árbol (ver CLAUDE.md, «Contenedores y propiedad de archivos»).
set -euo pipefail

DESTINO="${1:-/tmp/oa}"
SERVICIOS=(api-gateway auth-service tenant-service document-service archive-service
           storage-service workflow-service notification-service signature-service
           mcp-server knowledge-service)

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
mkdir -p "$DESTINO"

# El volcado va INLINE y no como fichero montado: un montaje más es un punto de
# fallo más —y uno silencioso, porque el servicio que no responde se salta—.
VOLCADO='import json, app.main as m; print(json.dumps(m.app.openapi()))'

for svc in "${SERVICIOS[@]}"; do
    if docker compose run --rm --no-deps -T -w /app "$svc" \
         python -c "$VOLCADO" 2>/dev/null > "$DESTINO/$svc.json" \
         && [ -s "$DESTINO/$svc.json" ]; then
        n=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['paths']))" "$DESTINO/$svc.json")
        printf '  %-22s %s rutas\n' "$svc" "$n"
    else
        # Un servicio que no responde se DICE. Si se saltara en silencio, el
        # censo volvería a publicar un porcentaje sobre un universo incompleto,
        # que es justo el defecto que este script viene a quitar.
        rm -f "$DESTINO/$svc.json"
        printf '  %-22s NO RESPONDIÓ — el censo saldrá incompleto\n' "$svc" >&2
    fi
done
echo "Esquemas en $DESTINO"

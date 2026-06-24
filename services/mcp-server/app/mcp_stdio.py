# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Entrypoint stdio del servidor MCP de OrpycaMCP.

Un cliente MCP local (p. ej. escritorio) lanza este proceso y se comunica por
stdio. La sesión se configura por entorno: GATEWAY_URL, MCP_BEARER_TOKEN,
MCP_TENANT_SLUG, MCP_USER_PERMISSIONS, MCP_USER_ROLES.

    python -m app.mcp_stdio
"""

from __future__ import annotations

import anyio
from mcp.server.stdio import stdio_server

from app.mcp_app import server


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    anyio.run(_run)


if __name__ == "__main__":
    main()

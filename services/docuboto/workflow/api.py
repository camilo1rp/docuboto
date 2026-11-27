# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Flow entry that registration calls on the same SQL connection."""

from __future__ import annotations

from typing import Any

import asyncpg

from workflow.services.distribution import auto_distribute


async def abrir_al_radicar(
    conn: asyncpg.Connection, tenant_slug: str, payload: dict[str, Any]
) -> str | None:
    """First assignment, same transaction as the tracking number."""
    return await auto_distribute(conn, tenant_slug, payload)

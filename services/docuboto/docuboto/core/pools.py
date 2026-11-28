# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One database pool (`orpycamcp_common.database`) for every domain.

Events, queues and locks all live in PostgreSQL (ADR-049): there is no second store.
"""

from __future__ import annotations

import asyncpg
from orpycamcp_common import database

from docuboto import config as settings


async def crear_pool() -> asyncpg.Pool:
    return await database.create_pool(
        settings.common.database_url_asyncpg,
        min_size=settings.common.db_pool_min_size,
        max_size=settings.common.db_pool_max_size,
        command_timeout=settings.common.db_command_timeout,
    )


def bind_pool(pool: asyncpg.Pool) -> None:
    database.bind_pool(pool)


def unbind_pool() -> None:
    database.bind_pool(None)

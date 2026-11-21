# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One database pool (`orpycamcp_common.database`) and one Redis client for every domain.

Redis is still bound into each consumer module; ADR-049 retires it.
"""

from __future__ import annotations

import importlib
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from orpycamcp_common import database

from docuboto.core.config import settings

_MODULOS_REDIS = (
    "document.core.redis_client",
    "workflow.core.redis_client",
    "notification.core.redis_client",
    "signature.core.redis_client",
    "knowledge.core.redis_client",
)


async def crear_pool() -> asyncpg.Pool:
    return await database.create_pool(
        settings.database_url_asyncpg,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=settings.db_command_timeout,
    )


def bind_pool(pool: asyncpg.Pool) -> None:
    database.bind_pool(pool)


def unbind_pool() -> None:
    database.bind_pool(None)


def bind_redis(client: aioredis.Redis) -> None:
    for nombre in _MODULOS_REDIS:
        modulo: Any = importlib.import_module(nombre)
        modulo._redis = client


def unbind_redis() -> None:
    for nombre in _MODULOS_REDIS:
        modulo: Any = importlib.import_module(nombre)
        modulo._redis = None

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One database pool and one Redis client, bound into every domain module."""

from __future__ import annotations

import importlib

import asyncpg
import redis.asyncio as aioredis

_MODULOS_DB = (
    "auth.core.database",
    "tenant.core.database",
    "document.core.database",
    "archive.core.database",
    "storage.core.database",
    "workflow.core.database",
    "notification.core.database",
    "signature.core.database",
    "knowledge.core.database",
)

_MODULOS_REDIS = (
    "workflow.core.redis_client",
    "notification.core.redis_client",
    "signature.core.redis_client",
    "knowledge.core.redis_client",
)


def bind_pool(pool: asyncpg.Pool) -> None:
    for nombre in _MODULOS_DB:
        importlib.import_module(nombre)._pool = pool


def unbind_pool() -> None:
    for nombre in _MODULOS_DB:
        importlib.import_module(nombre)._pool = None


def bind_redis(client: aioredis.Redis) -> None:
    for nombre in _MODULOS_REDIS:
        importlib.import_module(nombre)._redis = client


def unbind_redis() -> None:
    for nombre in _MODULOS_REDIS:
        importlib.import_module(nombre)._redis = None

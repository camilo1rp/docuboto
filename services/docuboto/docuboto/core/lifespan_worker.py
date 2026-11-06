# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Worker lifespan: veraPDF, render, LTA. No decryption secrets, no intelligence."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from orpycamcp_common import events, request_log
from orpycamcp_common import secrets as common_secrets

from docuboto.core.config import settings
from docuboto.core.pools import bind_pool, bind_redis, crear_pool, unbind_pool, unbind_redis
from docuboto.core.trabajo import COLA_WORKER
from docuboto.core.trabajo import bucle as bucle_trabajo
from signature.jobs import renovacion_lta
from storage.core.minio_client import init_minio
from storage.main import _format_migration_loop

logger = logging.getLogger(__name__)

_CLAVES_DE_CIFRADO = (
    "TOTP_ENCRYPTION_KEY",
    "SIGNING_KEY_ENCRYPTION_KEY",
    "EMAIL_ENCRYPTION_KEY",
    "WEBHOOK_SECRET_KEK",
    "INTELLIGENCE_CREDENTIAL_KEK",
)


def rechazar_claves_de_cifrado() -> None:
    """The worker image must not receive the keys that unwrap tenant secrets."""
    presentes = [n for n in _CLAVES_DE_CIFRADO if (os.environ.get(n) or "").strip()]
    if presentes:
        raise RuntimeError(
            "worker: no recibe claves de cifrado (" + ", ".join(presentes) + ")"
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    rechazar_claves_de_cifrado()
    common_secrets.require_secrets(
        {
            "MINIO_ACCESS_KEY": settings.storage.minio_access_key,
            "MINIO_SECRET_KEY": settings.storage.minio_secret_key,
        },
        servicio="worker",
    )
    preservation_warnings = settings.storage.preservation_startup_guard()
    if preservation_warnings:
        _NON_PROD = {"development", "dev", "local", "test", "testing", "ci"}
        if settings.storage.app_env.strip().lower() not in _NON_PROD:
            raise RuntimeError(
                "Config de preservación WORM insegura: " + "; ".join(preservation_warnings)
            )
        for warning in preservation_warnings:
            logger.warning("PRESERVATION CONFIG WARNING: %s", warning)

    logger.info("docuboto worker starting up")
    pool = await crear_pool()
    bind_pool(pool)
    app.state.db_pool = pool

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    bind_redis(redis_client)
    app.state.redis = redis_client

    init_minio(
        settings.storage.minio_endpoint,
        settings.storage.minio_access_key,
        settings.storage.minio_secret_key,
        settings.storage.minio_use_ssl,
    )

    tareas: list[asyncio.Task[None]] = []
    if settings.storage.format_migration_enabled:
        tareas.append(asyncio.create_task(_format_migration_loop()))
    if settings.signature.lta_renovacion_intervalo_s > 0:
        tareas.append(
            events.spawn_supervised(renovacion_lta.run, name="renovacion-lta", logger=logger)
        )
    tareas.append(asyncio.create_task(bucle_trabajo(COLA_WORKER)))
    request_log.canal_de_fondo()

    yield
    logger.info("docuboto worker shutting down")
    for tarea in tareas:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea
    await redis_client.aclose()
    unbind_redis()
    await pool.close()
    unbind_pool()
    app.state.db_pool = None

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One lifespan for the process. The intelligence consumer does not start here."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from orpycamcp_common import events, request_log
from orpycamcp_common import secrets as common_secrets

from archive.main import (
    _acta_reconciliation_loop,
    _disposicion_alerta_loop,
    _indice_reconciliation_loop,
)
from auth.jobs.signing_key_maintenance import maintenance_loop
from auth.routers.auth import set_auth_service
from auth.services.auth_service import AuthService
from docuboto.core.config import settings
from docuboto.core.pools import bind_pool, bind_redis, crear_pool, unbind_pool, unbind_redis
from docuboto.core.trabajo import COLA_APP
from docuboto.core.trabajo import bucle as bucle_trabajo
from document.api import registrar_apertura_de_flujo
from document.clients.storage_client import registrar_almacen
from document.core import altcha
from document.services.barrido import bucle_de_barrido
from document.services.email_sondeo import bucle_de_sondeo
from notification.core import webhook_secrets
from notification.workers import stream_worker
from signature.core.config import variables_retiradas_presentes
from signature.workers import document_consumer as signature_consumer
from storage.api import guardar as guardar_en_almacen
from storage.core.minio_client import init_minio
from workflow.api import abrir_al_radicar
from workflow.workers import document_consumer as workflow_consumer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    common_secrets.require_secrets(
        {
            "DOCUMENT_INTERNAL_TOKEN": settings.document.internal_service_token,
            "ALTCHA_HMAC_KEY": settings.document.altcha_hmac_key,
            "SIGNING_KEY_INTERNAL_TOKEN": settings.auth.signing_key_internal_token,
            "MINIO_ACCESS_KEY": settings.storage.minio_access_key,
            "MINIO_SECRET_KEY": settings.storage.minio_secret_key,
            "NOTIFICATION_INTERNAL_TOKEN": settings.notification.internal_service_token,
            "WEBHOOK_SECRET_KEK": settings.notification.webhook_secret_kek,
            "SIGNATURE_SECRET": settings.signature.signature_secret,
        },
        servicio="docuboto",
    )
    common_secrets.reject_published_values(
        {"TENANT_INTERNAL_TOKEN": settings.tenant.internal_service_token},
        servicio="docuboto",
    )
    if len(settings.document.altcha_hmac_key.encode()) < altcha.HMAC_MIN_BYTES:
        raise common_secrets.SecretoNoConfigurado(
            f"docuboto: ALTCHA_HMAC_KEY debe tener al menos "
            f"{altcha.HMAC_MIN_BYTES} bytes (openssl rand -hex 32)"
        )
    webhook_secrets.validate_kek_at_startup()
    for warning in settings.auth.issuer_config_warnings():
        logger.warning(warning)
    for retirada, sustituta in variables_retiradas_presentes():
        logger.error(
            "La variable %s ya NO tiene efecto y se ignora: use %s.",
            retirada,
            sustituta,
        )
    preservation_warnings = settings.storage.preservation_startup_guard()
    if preservation_warnings:
        _NON_PROD = {"development", "dev", "local", "test", "testing", "ci"}
        if settings.storage.app_env.strip().lower() not in _NON_PROD:
            raise RuntimeError(
                "Config de preservación WORM insegura: "
                + "; ".join(preservation_warnings)
            )
        for warning in preservation_warnings:
            logger.warning("PRESERVATION CONFIG WARNING: %s", warning)

    logger.info("docuboto starting up")
    pool = await crear_pool()
    bind_pool(pool)
    app.state.db_pool = pool
    registrar_apertura_de_flujo(abrir_al_radicar)
    registrar_almacen(guardar_en_almacen)

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    bind_redis(redis_client)
    app.state.redis = redis_client

    init_minio(
        settings.storage.minio_endpoint,
        settings.storage.minio_access_key,
        settings.storage.minio_secret_key,
        settings.storage.minio_use_ssl,
    )
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.document.tenant_service_url, timeout=5.0
    )
    auth_service = AuthService()
    set_auth_service(auth_service)
    app.state.auth_service = auth_service
    app.state.trusted_proxies = os.environ.get("TRUSTED_PROXIES", "")

    tareas: list[asyncio.Task[None]] = []
    if settings.auth.signing_key_maintenance_enabled:
        tareas.append(asyncio.create_task(maintenance_loop(pool)))
    if settings.archive.indice_reconciliation_enabled:
        tareas.append(asyncio.create_task(_indice_reconciliation_loop()))
    if settings.archive.acta_reconciliation_enabled:
        tareas.append(asyncio.create_task(_acta_reconciliation_loop()))
    if settings.archive.disposicion_alerta_enabled:
        tareas.append(asyncio.create_task(_disposicion_alerta_loop()))
    if settings.document.email_sondeo_enabled and settings.document.email_sondeo_tenant_slug:
        tareas.append(
            asyncio.create_task(
                bucle_de_sondeo(
                    pool,
                    settings.document.email_sondeo_tenant_slug,
                    app.state.http_client,
                )
            )
        )
    if settings.document.barrido_enabled:
        tareas.append(
            asyncio.create_task(bucle_de_barrido(app.state.redis, app.state.http_client))
        )
    tareas.append(
        events.spawn_supervised(workflow_consumer.run, name="workflow", logger=logger)
    )
    tareas.append(
        events.spawn_supervised(stream_worker.run, name="notification", logger=logger)
    )
    tareas.append(
        events.spawn_supervised(signature_consumer.run, name="signature", logger=logger)
    )
    tareas.append(asyncio.create_task(bucle_trabajo(COLA_APP)))
    request_log.canal_de_fondo()

    yield
    logger.info("docuboto shutting down")
    for tarea in tareas:
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea
    await auth_service.close()
    await app.state.http_client.aclose()
    await redis_client.aclose()
    unbind_redis()
    await pool.close()
    unbind_pool()
    registrar_apertura_de_flujo(None)
    registrar_almacen(None)
    app.state.db_pool = None

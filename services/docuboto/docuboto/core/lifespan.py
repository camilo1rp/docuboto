# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One lifespan for the process, and the one place where loops start.

ADR-047: there is no worker. Its loops (format migration, LTA renewal, the
heavy `worker` queue) run here in the same `BackgroundTasks` group, and the
heavy ones share `heavy_slot()`. The PPI ingestion consumer starts only with
`PPI_PROVIDER_URL` set (ADR-051 §2).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from orpycamcp_common import request_log
from orpycamcp_common import secrets as common_secrets
from orpycamcp_common.client_ip import exigir_proxies_sin_gateway, gateway_de_la_ruta_por_defecto

from archive.main import (
    _acta_reconciliation_loop,
    _disposicion_alerta_loop,
    _indice_reconciliation_loop,
)
from auth.jobs.signing_key_maintenance import maintenance_loop
from auth.routers.auth import set_auth_service
from auth.services.auth_service import AuthService
from docuboto import config as settings
from docuboto.config import variables_retiradas_presentes
from docuboto.core.pools import bind_pool, bind_redis, crear_pool, unbind_pool, unbind_redis
from docuboto.core.tasks import BackgroundTasks, heavy_slot
from docuboto.core.trabajo import COLA_APP, COLA_WORKER
from docuboto.core.trabajo import bucle as bucle_trabajo
from document.api import registrar_apertura_de_flujo
from document.core import altcha
from document.services.barrido import bucle_de_barrido
from document.services.email_sondeo import bucle_de_sondeo
from knowledge.core import salida_http
from knowledge.workers import intelligence_consumer
from notification.core import webhook_secrets
from notification.workers import stream_worker
from signature.jobs import renovacion_lta
from signature.workers import document_consumer as signature_consumer
from storage.core.minio_client import init_minio
from storage.jobs.format_migration import run_once as _format_migration_once
from storage.main import _descargar_para_migracion, _subir_para_migracion
from workflow.api import abrir_al_radicar
from workflow.workers import document_consumer as workflow_consumer

logger = logging.getLogger(__name__)


def _exigir_proxies_de_produccion() -> None:
    """Producción: `TRUSTED_PROXIES` no confía en la puerta de enlace de la red (ARC-B4).

    Lo comprobaba el api-gateway al arrancar; sin él (ADR-047) lo hace la app.
    """
    if os.environ.get("APP_ENV", "").strip().lower() != "production":
        return
    gateway = os.environ.get("GATEWAY_RED_DOCKER_GATEWAY", "").strip() or gateway_de_la_ruta_por_defecto()
    if gateway is None:
        logger.warning(
            "APP_ENV=production pero no se pudo determinar la puerta de enlace de la red: "
            "declare GATEWAY_RED_DOCKER_GATEWAY para que se compruebe TRUSTED_PROXIES"
        )
        return
    exigir_proxies_sin_gateway(os.environ.get("TRUSTED_PROXIES", ""), gateway)


async def _format_migration() -> None:
    """Format migration sweep (E10, RF-PRE-04). Ghostscript: one heavy slot."""
    request_log.canal_de_fondo()
    while True:
        try:
            async with heavy_slot():
                await _format_migration_once(
                    descargar=_descargar_para_migracion,
                    subir_factory=_subir_para_migracion,
                )
        except Exception:
            logger.warning("Fallo en la barrida de migración de formato", exc_info=True)
        await asyncio.sleep(settings.storage.format_migration_interval_seconds)


async def _lta_renewal() -> None:
    """LTA renewal (supervised). Same cadence as `renovacion_lta.run`, one heavy slot."""
    request_log.canal_de_fondo()
    await asyncio.sleep(settings.signature.lta_renovacion_retraso_inicial_s)
    while True:
        try:
            async with heavy_slot():
                resumen = await renovacion_lta.run_once()
            if any(any(v.values()) for v in resumen.values()):
                logger.info("renovación LTA: %s", resumen)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("renovación LTA: pasada fallida")
        await asyncio.sleep(settings.signature.lta_renovacion_intervalo_s)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _exigir_proxies_de_produccion()
    common_secrets.require_secrets(
        {
            "ALTCHA_HMAC_KEY": settings.document.altcha_hmac_key,
            "MINIO_ACCESS_KEY": settings.storage.minio_access_key,
            "MINIO_SECRET_KEY": settings.storage.minio_secret_key,
            "WEBHOOK_SECRET_KEK": settings.notification.webhook_secret_kek,
            "SIGNATURE_SECRET": settings.signature.signature_secret,
        },
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

    redis_client = aioredis.from_url(settings.common.redis_url, decode_responses=True)
    bind_redis(redis_client)
    app.state.redis = redis_client

    init_minio(
        settings.storage.minio_endpoint,
        settings.storage.minio_access_key,
        settings.storage.minio_secret_key,
        settings.storage.minio_use_ssl,
    )
    auth_service = AuthService()
    set_auth_service(auth_service)
    app.state.auth_service = auth_service
    app.state.trusted_proxies = os.environ.get("TRUSTED_PROXIES", "")

    tareas = BackgroundTasks()
    if settings.auth.signing_key_maintenance_enabled:
        tareas.start("signing-key-maintenance", lambda: maintenance_loop(pool))
    if settings.archive.indice_reconciliation_enabled:
        tareas.start("indice-reconciliation", _indice_reconciliation_loop)
    if settings.archive.acta_reconciliation_enabled:
        tareas.start("acta-reconciliation", _acta_reconciliation_loop)
    if settings.archive.disposicion_alerta_enabled:
        tareas.start("disposicion-alerta", _disposicion_alerta_loop)
    if settings.document.email_sondeo_enabled and settings.document.email_sondeo_tenant_slug:
        tareas.start(
            "email-sondeo",
            lambda: bucle_de_sondeo(pool, settings.document.email_sondeo_tenant_slug),
        )
    if settings.document.barrido_enabled:
        tareas.start("barrido", lambda: bucle_de_barrido(app.state.redis))
    tareas.start("workflow", workflow_consumer.run, supervised=True)
    tareas.start("notification", stream_worker.run, supervised=True)
    tareas.start("signature", signature_consumer.run, supervised=True)
    if settings.knowledge.ppi_provider_url.strip():
        tareas.start("intelligence-gateway", intelligence_consumer.run, supervised=True)
    if settings.storage.format_migration_enabled:
        tareas.start("format-migration", _format_migration)
    if settings.signature.lta_renovacion_intervalo_s > 0:
        tareas.start("renovacion-lta", _lta_renewal, supervised=True)
    tareas.start("trabajo-app", lambda: bucle_trabajo(COLA_APP))
    tareas.start("trabajo-worker", lambda: bucle_trabajo(COLA_WORKER))
    app.state.background_tasks = tareas
    request_log.canal_de_fondo()

    yield
    logger.info("docuboto shutting down")
    await tareas.stop()
    await salida_http.cerrar_consulta()
    await auth_service.close()
    await redis_client.aclose()
    unbind_redis()
    await pool.close()
    unbind_pool()
    registrar_apertura_de_flujo(None)
    app.state.db_pool = None

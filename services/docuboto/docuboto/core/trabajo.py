# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Claim-loop handlers for the unified process. App and worker register different sets."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from orpycamcp_common.trabajo import (
    COLA_APP,
    COLA_WORKER,
    TIPO_INTELLIGENCE,
    TIPO_LTA,
    TIPO_MAIL,
    TIPO_RENDER,
    TIPO_VERAPDF,
    TIPO_WEBHOOK,
    Fila,
    SqlRepo,
    recorrer,
)

from document.core.database import acquire_tenant, get_pool
from document.services.barrido import tenants_activos

logger = logging.getLogger(__name__)

_INTERVALO_S = 2.0


async def _webhook(_conn: Any, fila: Fila) -> None:
    from notification.repositories.processed_events_repository import ProcessedEventsRepository
    from notification.services.webhook_service import dispatch

    claimed = await ProcessedEventsRepository(_conn).claim(fila.event_id)
    if not claimed:
        return
    sobre = {
        "event_id": fila.event_id,
        "event_type": fila.payload.get("event_type") or fila.tipo,
        "tenant_slug": fila.tenant_slug,
        "payload": fila.payload,
    }
    await dispatch(sobre)


async def _mail(_conn: Any, fila: Fila) -> None:
    from notification.schemas.notification import NotificationSend
    from notification.services.notification_service import send_notification

    envio = fila.payload.get("send")
    if not isinstance(envio, dict):
        return
    await send_notification(
        NotificationSend.model_validate(envio),
        event_type=fila.payload.get("event_type"),
    )


async def _intelligence(_conn: Any, fila: Fila) -> None:
    """Nothing to do: PPI ingestion runs on the stream consumer (ADR-051 §2).

    `lifespan` starts `intelligence_consumer` when `PPI_PROVIDER_URL` is set,
    and that consumer reads the domain streams. No code enqueues this type
    today; ADR-049 moves ingestion onto this queue and gives it a body.
    """
    del _conn, fila


async def _verapdf(_conn: Any, fila: Fila) -> None:
    from storage.core.database import get_pool as pool_storage
    from storage.services.pdfa_ingest_service import run_ingest_validation_background

    crudo = fila.payload.get("file_id")
    if not crudo:
        return
    await run_ingest_validation_background(pool_storage(), fila.tenant_slug, uuid.UUID(str(crudo)))


async def _render(_conn: Any, fila: Fila) -> None:
    del _conn, fila


async def _lta(_conn: Any, fila: Fila) -> None:
    del _conn, fila


MANEJADORES_APP = {
    TIPO_WEBHOOK: _webhook,
    TIPO_MAIL: _mail,
    TIPO_INTELLIGENCE: _intelligence,
}
MANEJADORES_WORKER = {
    TIPO_VERAPDF: _verapdf,
    TIPO_RENDER: _render,
    TIPO_LTA: _lta,
}


async def bucle(cola: str) -> None:
    """One claim pass per active tenant, forever. The caller cancels the task."""
    manejadores = MANEJADORES_APP if cola == COLA_APP else MANEJADORES_WORKER
    reclamador = cola
    pool = get_pool()
    while True:
        try:
            for slug in await tenants_activos(pool):
                async with acquire_tenant(slug) as conn:
                    await recorrer(
                        SqlRepo(conn),
                        cola=cola,
                        reclamador=reclamador,
                        manejadores=manejadores,
                        conn=conn,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("trabajo: la pasada de %s falló", cola)
        await asyncio.sleep(_INTERVALO_S)


__all__ = ["COLA_APP", "COLA_WORKER", "MANEJADORES_APP", "MANEJADORES_WORKER", "bucle"]

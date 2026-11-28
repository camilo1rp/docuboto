# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Stream worker for notification-service (E16).

Consume dos streams del bus en PostgreSQL (`docuboto.core.bus`, ADR-049):
  - orpycamcp.workflow.events  → process_event
  - orpycamcp.document.events  → process_document_event

Dead-letter / reclamación (R-4, ADR-021 Fase 3):
  - A-1 (confirm-only-on-success): the delivery is confirmed ONLY when the
    handler returns without raising.  A failure leaves it pending; the bus's
    reclaim sweep retries it and after max_deliveries=5 parks it with a durable
    Postgres record.
  - _persist_dead_letter: atomic Postgres write (evento_dead_letter + audit_log
    in the same transaction).  Transient errors propagate — no dead-letter without
    durable trace (closes R-4).
  - R1: events without tenant_slug → immutable trace under SYSTEM_TENANT in
    public.audit_log; function returns normally so the helper can confirm.

Cadence invariant (in `docuboto.core.bus`): min idle 60 s > reclaim 30 s > wait 5 s.
"""

from __future__ import annotations

import logging
from functools import partial

from docuboto.config import notification as settings
from docuboto.core import bus, dead_letter
from notification.core.constants import DLQ_GROUP
from notification.jobs import retention
from notification.services.event_consumer import process_document_event, process_event

logger = logging.getLogger(__name__)

WORKFLOW_STREAM = "orpycamcp.workflow.events"
DOCUMENT_STREAM = "orpycamcp.document.events"
# GROUP doubles as the origin_group written to (and filtered on) the shared
# evento_dead_letter table (ADR-021 Fase 5 Inc.2) — sourced from
# app.core.constants so the worker, the admin router, and the retention job can
# never drift apart. See the module docstring in notification/core/constants.py.
GROUP = DLQ_GROUP
_SERVICE = "notification-service"

# Cadence for the daily housekeeping (dedup retention + durable DLQ retention).
# Read once at import time so tests can patch the module constant independently
# of the settings singleton.
RETENTION_INTERVAL: float = float(settings.retention_interval_seconds)


async def _handle(data: dict[str, str]) -> None:
    """Enruta por tipo de evento: los de flujo a `process_event`, el resto a
    `process_document_event` (los dos verifican la firma del sobre)."""
    tipo = data.get("event_type") or ""
    if tipo.startswith("workflow."):
        await process_event(data)
    else:
        await process_document_event(data)


#: El `on_dead_letter` del bus: la escritura única del aparcamiento (ADR-049 §4).
_persist_dead_letter = partial(dead_letter.persistir, group=GROUP, service=_SERVICE)


async def _mantenimiento_diario() -> None:
    """Retención diaria de la tabla de dedup.

    La caducidad de los eventos del bus y la de los dead-letters terminales no
    viven aquí: las hace el barrido para todas las entidades y todos los grupos
    (`events.purgar`, `dead_letter.purgar_terminales`).
    """
    await retention.run_once()


async def run() -> None:
    """Bucle del grupo `notification-service` sobre el bus en PostgreSQL.

    Con el dedup insert-then-send, reintentar un evento cuyo claim ya se hizo da
    en el ON CONFLICT y se salta en silencio: seguro aun con efectos externos.
    """
    await bus.consumir(
        group=GROUP,
        streams=(WORKFLOW_STREAM, DOCUMENT_STREAM),
        handler=_handle,
        on_dead_letter=_persist_dead_letter,
        status_name="notification",
        periodico=_mantenimiento_diario,
        intervalo_periodico=RETENTION_INTERVAL,
    )

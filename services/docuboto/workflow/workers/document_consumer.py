# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Worker: consume `orpycamcp.document.events` y distribuye automáticamente (E05).

Por cada `document.radicado.created` evalúa las reglas del tenant y autoasigna
el radicado (distribution.auto_distribute). Por cada
`document.radicado.due_date_changed` copia la fecha al `due_at` de los pasos
activos. Usa el sobre canónico de orpycamcp_common y un grupo de consumidores
propio.

El bucle es el del bus en PostgreSQL (`docuboto.core.bus`, ADR-049).

Dead-letter / reclamación (R-4, ADR-021):
  - El bus reclama lo atascado cada 30 s (`events.reclaim_stale`).
  - Lo que supera `max_deliveries=5` se persiste durablemente en
    `evento_dead_letter` + `audit_log` en la misma transacción, y solo entonces
    se confirma (no hay dead-letter sin traza durable).
  - R1: evento sin tenant_slug → traza bajo SYSTEM_TENANT en `public.audit_log`
    (en vez de solo loguear) para que incluso el poison pill malformado deje
    registro inmutable.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from datetime import time as hora
from functools import partial

from orpycamcp_common import events
from orpycamcp_common.database import acquire_tenant

from docuboto.core import bus, dead_letter
from workflow.core.constants import DLQ_GROUP
from workflow.services.distribution import auto_distribute

logger = logging.getLogger(__name__)

DOCUMENT_STREAM = "orpycamcp.document.events"
VENCIMIENTO_CAMBIADO = "document.radicado.due_date_changed"
# Sourced from workflow.core.constants: the same value the dead-letter console filters on.
GROUP = DLQ_GROUP
_SERVICE = "workflow-service"


async def apply_distribution(conn, tenant_slug: str, payload: dict) -> str | None:
    """Core de distribución automática (H3): evalúa reglas y autoasigna.

    ``conn`` debe llegar con el ``search_path`` ya fijado al tenant. Reutilizado
    por el worker (``_handle``) y por el reproceso de la consola (``replay``), de
    modo que el reproceso corre sobre la MISMA conexión/transacción del caller.
    ``auto_distribute`` es idempotente (guard RN-8), así que reprocesar un radicado
    ya distribuido no genera asignación duplicada.

    Devuelve la dependencia asignada, o None si no aplicaba ninguna regla / ya
    estaba distribuido.
    """
    return await auto_distribute(conn, tenant_slug, payload)


async def alinear_vencimiento(conn, payload: dict) -> None:
    """Copia el vencimiento del radicado al `due_at` de los pasos activos.

    Los pasos ya cerrados conservan el plazo con el que se tramitaron. El
    instante es el final de ese día en UTC: el radicado guarda una fecha, y el
    paso un instante.
    """
    rid = payload.get("id")
    if not rid:
        return
    crudo = payload.get("due_date")
    due = None
    if crudo:
        dia = date.fromisoformat(crudo)
        due = datetime.combine(dia, hora(23, 59), tzinfo=UTC)
    await conn.execute(
        """
        UPDATE flow_steps
           SET due_at = $2
         WHERE radicado_id = $1
           AND status IN ('pending', 'in_progress')
        """,
        rid,
        due,
    )


async def _handle(data: dict) -> None:
    envelope = events.parse_fields(data)
    tipo = envelope.get("event_type")
    tenant_slug = envelope.get("tenant_slug")
    payload = envelope.get("payload") or {}
    if not tenant_slug or not payload.get("id"):
        return
    if tipo == "document.radicado.created":
        async with acquire_tenant(tenant_slug) as conn:
            await apply_distribution(conn, tenant_slug, payload)
    elif tipo == VENCIMIENTO_CAMBIADO:
        async with acquire_tenant(tenant_slug) as conn:
            await alinear_vencimiento(conn, payload)


async def replay(
    conn,
    tenant_slug: str,
    *,
    event_id: str,
    event_type: str | None,
    envelope: dict,
    actor: str,
    origin_stream: str | None = None,
) -> dict:
    """Reproceso desde la consola del dead-letter, en la transacción del llamante.

    El mismo despacho que `_handle`, sobre la conexión de la consola: si falla,
    revierte con el cambio de estado y el asiento.
    """
    payload = envelope.get("payload") or {}
    if event_type == VENCIMIENTO_CAMBIADO:
        await alinear_vencimiento(conn, payload)
        return {}
    return {"assigned_to": await apply_distribution(conn, tenant_slug, payload)}


#: El `on_dead_letter` del bus: la escritura única del aparcamiento (ADR-049 §4).
_persist_dead_letter = partial(dead_letter.persistir, group=GROUP, service=_SERVICE)


async def run() -> None:
    """Bucle del grupo `workflow-service` sobre el bus en PostgreSQL. Cancelar para cerrar.

    Confirma solo si `_handle` tuvo éxito; un fallo deja la entrega pendiente y
    la reclamación la reintenta y, a la quinta, la aparca con `_persist_dead_letter`.
    `auto_distribute` es idempotente (guard RN-8), así que reintentar es seguro.
    """
    await bus.consumir(
        group=GROUP,
        streams=(DOCUMENT_STREAM,),
        handler=_handle,
        on_dead_letter=_persist_dead_letter,
        status_name="workflow",
    )

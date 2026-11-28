# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reproceso de un aviso muerto desde la consola del dead-letter (ADR-021, ADR-049).

El efecto de notification es un CORREO (SMTP, externo, no transaccional), así que
el reproceso no puede ser atómico como los de workflow o signature. Es
**claim-then-send**: el envío ocurre dentro de la transacción de la consola, con
la fila bloqueada, y la consola solo la pasa a ``resolved`` si TODOS los envíos
salieron. Si alguno falla se lanza :class:`ReplayFallido`, la transacción se
revierte y el dead-letter queda pendiente. El riesgo aceptado es un doble envío
si fallara el commit tras un envío correcto, preferible a una pérdida silenciosa.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from docuboto.core.dead_letter import ReplayFallido
from notification.repositories.processed_events_repository import ProcessedEventsRepository
from notification.services.buzones import resolver_buzon
from notification.services.event_consumer import (
    build_workflow_notifications,
    construir_envios_documento,
)
from notification.services.notification_service import send_notification
from notification.workers.stream_worker import DOCUMENT_STREAM, WORKFLOW_STREAM

#: El stream decide qué constructor de envíos usa el reproceso, el mismo que usa el
#: consumidor vivo. Un `origin_stream` ausente o desconocido no se adivina.
_STREAMS_CONOCIDOS = frozenset({WORKFLOW_STREAM, DOCUMENT_STREAM})


async def _construir_envios(conn: Any, origin_stream: str, envelope: dict) -> list:
    """Reconstruye los envíos de un evento muerto, por el MISMO camino que el vivo.

    El aviso de flujo necesita resolver el buzón de la dependencia igual que el
    consumidor —`to_dept` es el NOMBRE, no una dirección—; el de documentos usa
    el mismo núcleo (``construir_envios_documento``), con su gate de clearance.
    """
    payload_data = envelope.get("payload") or {}
    if origin_stream == WORKFLOW_STREAM:
        buzon = await resolver_buzon(
            conn, payload_data.get("to_dept_code"), payload_data.get("to_dept")
        )
        return build_workflow_notifications(envelope, buzon_destino=buzon)
    return await construir_envios_documento(conn, envelope)


async def replay(
    conn: Any,
    tenant_slug: str,
    *,
    event_id: str,
    event_type: str | None,
    envelope: dict,
    actor: str,
    origin_stream: str | None = None,
) -> dict:
    """Reenvía los avisos de un evento muerto. ``{"notifications_sent": n}`` o lanza."""
    if origin_stream not in _STREAMS_CONOCIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "unknown_origin_stream",
                "detail": (
                    f"No se puede reprocesar: origin_stream={origin_stream!r} "
                    "no corresponde a un stream conocido"
                ),
                "status": 422,
            },
        )
    envios = await _construir_envios(conn, origin_stream, envelope)
    # La marca de deduplicación se retira antes de reenviar, en la misma
    # transacción: si el envío falla y se revierte, vuelve con ella.
    await ProcessedEventsRepository(conn).delete_one(event_id)
    # `send_notification` no lanza ante un fallo SMTP: devuelve el aviso con
    # `status == "failed"`. Por eso se mira el estado, no la excepción.
    enviados = [await send_notification(envio, event_type=event_type) for envio in envios]
    fallidos = [n for n in enviados if n.status != "sent"]
    if fallidos:
        raise ReplayFallido(
            f"{len(fallidos)} de {len(enviados)} envíos fallaron;"
            " el dead-letter permanece pendiente."
        )
    return {"notifications_sent": len(enviados)}

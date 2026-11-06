# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One `/metrics` for the process. Combines the domain signals."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from orpycamcp_common import deadletter, metricas

from archive.routers.metrics import _SENALES as _WORM
from auth.routers.metrics import _SENALES as _AUDIT
from document.core.database import get_pool
from knowledge.routers.metrics import _SENALES as _INTELIGENCIA
from notification.core.constants import DLQ_GROUP as _DLQ_NOTIFICATION
from signature.core.constants import DLQ_GROUP as _DLQ_SIGNATURE
from workflow.core.constants import DLQ_GROUP as _DLQ_WORKFLOW

router = APIRouter(tags=["metrics"])

_GRUPOS_DLQ = (_DLQ_WORKFLOW, _DLQ_NOTIFICATION, _DLQ_SIGNATURE)


async def _medir_dlq(conn) -> list[metricas.Muestra]:
    n, mas_antiguo = await deadletter.contar_pendientes(
        conn, await metricas.esquemas_a_medir(conn), list(_GRUPOS_DLQ)
    )
    out = [metricas.Muestra("orpycamcp_dlq_pending_events", n)]
    if n > 0 and mas_antiguo is not None:
        out.append(
            metricas.Muestra(
                "orpycamcp_dlq_oldest_pending_timestamp_seconds",
                mas_antiguo.timestamp(),
            )
        )
    return out


_SENALES = (
    metricas.Senal(
        "dlq",
        _medir_dlq,
        familias=frozenset(
            {
                "orpycamcp_dlq_pending_events",
                "orpycamcp_dlq_oldest_pending_timestamp_seconds",
            }
        ),
    ),
    *_AUDIT,
    *_WORM,
    *_INTELIGENCIA,
)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

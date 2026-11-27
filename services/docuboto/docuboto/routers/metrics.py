# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The one `/metrics` of the process (ADR-047): DLQ, audit chain, WORM, intelligence.

Not routed by `edge` (it answers 404 there) and it rejects any request with proxy
headers. See `orpycamcp_common.metricas`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from orpycamcp_common import deadletter, metricas
from orpycamcp_common.database import get_pool

from archive.repositories.metricas_repository import contar_agotados
from auth.core.medidor import _FAMILIAS as _FAMILIAS_AUDIT
from auth.core.medidor import medir_audit_chain
from knowledge.core.medidor import FAMILIAS as _FAMILIAS_INTELIGENCIA
from knowledge.core.medidor import medir as medir_inteligencia
from notification.core.constants import DLQ_GROUP as _DLQ_NOTIFICATION
from signature.core.constants import DLQ_GROUP as _DLQ_SIGNATURE
from workflow.core.constants import DLQ_GROUP as _DLQ_WORKFLOW

router = APIRouter(tags=["metrics"])


async def _medir_worm(conn) -> list[metricas.Muestra]:
    return await contar_agotados(conn, await metricas.esquemas_a_medir(conn))

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
    metricas.Senal("audit_chain", medir_audit_chain, familias=_FAMILIAS_AUDIT),
    metricas.Senal("worm", _medir_worm, familias=frozenset({"orpycamcp_worm_exhausted_artifacts"})),
    metricas.Senal("intelligence", medir_inteligencia, familias=_FAMILIAS_INTELIGENCIA),
)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

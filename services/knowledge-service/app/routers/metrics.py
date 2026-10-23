# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de knowledge-service: la señal de dead-letter (C18, ADR-041).

Solo expone agregados (pendientes del grupo de ingesta y el más antiguo), sin
etiqueta de tenant ni identidad. No lo enruta el gateway (solo reenvía
`/api/v1/`) y rechaza con 404 toda petición con cabeceras de proxy. Ver
`orpycamcp_common.metricas`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from orpycamcp_common import metricas

from app.core.config import settings
from app.core.database import get_pool

router = APIRouter(tags=["metrics"])

# El mismo grupo que filtra su `admin_deadletter` (GROUP del consumidor de ingesta).
_SENALES = (metricas.senal_dlq(settings.knowledge_ingest_group),)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

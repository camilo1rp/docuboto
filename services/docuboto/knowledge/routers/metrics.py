# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de knowledge-service: la señal `intelligence` (ADR-041, ADR-045).

Envíos, rechazos de barrera, respaldos y suspensiones, sumados sobre entidades
y sin etiqueta de identidad. No lo enruta el gateway (solo reenvía `/api/v1/`)
y rechaza con 404 toda petición con cabeceras de proxy.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from orpycamcp_common import metricas

from knowledge.core.database import get_pool
from knowledge.core.medidor import FAMILIAS, medir

router = APIRouter(tags=["metrics"])

_SENALES = (metricas.Senal("intelligence", medir, familias=FAMILIAS),)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

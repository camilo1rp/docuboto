# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de archive-service: la señal `worm` (C18, ADR-041).

Artefactos cuyo reintento de protección/renovación WORM se agotó, por artefacto y
fase, sumados sobre tenants y sin etiqueta de tenant. No lo enruta el gateway
(solo reenvía `/api/v1/`) y rechaza con 404 toda petición con cabeceras de proxy.
Ver `orpycamcp_common.metricas`.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Request, Response
from orpycamcp_common import metricas

from app.core.database import get_pool
from app.repositories.metricas_repository import contar_agotados

router = APIRouter(tags=["metrics"])


async def medir_worm(conn: asyncpg.Connection) -> list[metricas.Muestra]:
    return await contar_agotados(conn, await metricas.esquemas_a_medir(conn))


_SENALES = (
    metricas.Senal("worm", medir_worm, familias=frozenset({"orpycamcp_worm_exhausted_artifacts"})),
)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

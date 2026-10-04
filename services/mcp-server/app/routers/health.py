# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud de mcp-server.

`/health` devolvía un `{"status": "ok"}` **fijo** y era el `healthcheck` del
contenedor. mcp-server no tiene base de datos ni Redis: es cliente del
api-gateway y de nada más (ADR-019), así que su única dependencia real es que el
gateway conteste. Sin esa comprobación, la fachada MCP y el asistente
conversacional se reportaban sanos cuando ninguna herramienta podía ejecutarse.

Se consulta el `/health` del gateway —su sonda de VIVACIDAD, no la de
disponibilidad—: aquí solo se quiere saber si hay alguien al otro lado, no
arrastrar el veredicto de disponibilidad de los diez servicios que hay detrás.

Este servicio SÍ instala `orpycamcp_common` (secretos de arranque, marca de origen
del canal, traza por petición), pero no tiene BD ni auditoría propia: su única
dependencia es el gateway, y esa sonda se escribe aquí. El criterio —estado en una
palabra, detalle solo al log— es el mismo del helper compartido, y es lo que
importa.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_SERVICE = "mcp-server"


@router.get("/health", summary="Vivacidad — ¿responde el proceso?")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": _SERVICE}


async def _gateway_ok() -> str:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            respuesta = await client.get(f"{settings.gateway_url}/health")
            respuesta.raise_for_status()
    except Exception as exc:
        # El detalle NO se publica: el texto de una excepción de red trae el
        # destino (`api-gateway:8080`), y esta ruta no exige autenticación.
        logger.warning("Sonda de disponibilidad 'gateway' falló: %s", exc, exc_info=True)
        return "error"
    return "ok"


@router.get("/health/ready", summary="Disponibilidad — ¿puede atender tráfico ahora?")
async def ready() -> JSONResponse:
    checks = {"gateway": await _gateway_ok()}
    listo = checks["gateway"] == "ok"
    return JSONResponse(
        status_code=200 if listo else 503,
        content={"status": "ready" if listo else "not_ready", "checks": checks},
    )

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sondas de salud del gateway. Tres defectos corregidos a la vez.

**1. La vivacidad dependía de diez servicios.** `/health` consultaba a todos los
servicios aguas abajo y devolvía **503** si alguno fallaba — y ese endpoint es el
`healthcheck` del contenedor. Con notification caído (SMTP inaccesible, por
ejemplo), el orquestador declaraba enfermo al **gateway**, que es lo único capaz
de seguir sirviendo el resto del sistema y de devolver un error decente. Reiniciar
la puerta de entrada porque una habitación está a oscuras apaga el edificio.
Ahora `/health` es vivacidad pura y el diagnóstico agregado vive en
`/health/ready`, que sí puede devolver 503 sin provocar reinicios.

**2. Publicaba la topología interna.** Cada fallo se reportaba como
``f"unreachable: {exc}"``, y el texto de una excepción de httpx incluye la URL
completa: ``ConnectError ... http://document-service:8003/health``. La ruta no
exige autenticación, así que el mapa de nombres y puertos internos estaba
disponible para cualquiera que la consultara con un servicio caído. Ahora el
estado es una palabra y el detalle va al log.

**3. No cubría tres servicios.** La lista tenía siete entradas; el sistema tiene
diez servicios detrás del gateway. signature (firma y sellado), knowledge
(recuperación semántica) y mcp (asistente) podían estar caídos sin que el
diagnóstico lo mencionara — y el diagnóstico se lee justo cuando algo va mal.
La lista se deriva ahora de `_DOWNSTREAM`, que enumera los diez.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Los DIEZ servicios que el gateway enruta. Antes eran siete: faltaban
# signature, knowledge y mcp.
_DOWNSTREAM = [
    ("auth-service", "auth_service_url"),
    ("tenant-service", "tenant_service_url"),
    ("document-service", "document_service_url"),
    ("archive-service", "archive_service_url"),
    ("storage-service", "storage_service_url"),
    ("workflow-service", "workflow_service_url"),
    ("notification-service", "notification_service_url"),
    ("signature-service", "signature_service_url"),
    ("knowledge-service", "knowledge_service_url"),
    ("mcp-server", "mcp_service_url"),
]


async def _check_service(
    client: httpx.AsyncClient,
    name: str,
    base_url: str,
) -> tuple[str, str]:
    """Estado de un servicio, reducido a una palabra.

    El motivo real se registra en el log. Lo que sale por HTTP nunca incluye la
    excepción: su texto trae la URL interna del servicio.
    """
    try:
        r = await client.get(f"{base_url}/health", timeout=5.0)
        if r.status_code < 500:
            return name, "ok"
        logger.warning("Servicio %s respondió HTTP %s en /health", name, r.status_code)
        return name, "error"
    except Exception as exc:
        logger.warning("Servicio %s inalcanzable: %s", name, exc, exc_info=True)
        return name, "unreachable"


@router.get("/health", summary="Vivacidad — ¿responde el gateway?")
async def health_check() -> dict[str, str]:
    """Sin dependencias, a propósito: gobierna los reinicios del contenedor."""
    return {"status": "ok", "service": "api-gateway"}


@router.get("/health/ready", summary="Disponibilidad — estado del gateway y sus diez servicios")
async def ready(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}

    # Redis local (el gateway lo gestiona directamente): sí es dependencia
    # propia — sin él no hay limitación de tasa ni caché de validación.
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        checks["redis"] = "unavailable"
    else:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            logger.warning("Redis del gateway inalcanzable: %s", exc, exc_info=True)
            checks["redis"] = "error"

    # Servicios aguas abajo — llamadas en paralelo.
    client: httpx.AsyncClient = request.app.state.http_client
    tasks = [
        _check_service(client, name, getattr(request.app.state, attr)) for name, attr in _DOWNSTREAM
    ]
    for name, status in await asyncio.gather(*tasks):
        checks[name] = status

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )

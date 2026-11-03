# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente de la búsqueda de texto completo de document-service (ADR-046 §2).

El respaldo `local` de la pasarela no tiene índice propio: consulta
`GET /api/v1/search` COMO EL USUARIO, con su identidad firmada. document-service
aplica su RBAC y su clearance sobre el nivel vivo del radicado, igual que en la
pantalla de consulta, así que aquí no se duplica ninguna barrera.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from orpycamcp_common.usuario import cabeceras_internas, slug_de

from knowledge.core.config import settings

# La ruta que se FIRMA es la misma que se LLAMA (ADR-036): una sola constante.
_RUTA_SEARCH = "/api/v1/search"

def _no_disponible() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "search_unavailable",
            "detail": "La búsqueda de texto completo no está disponible",
            "status": 503,
        },
    )


async def search(request: Request, *, q: str, size: int) -> list[dict[str, Any]]:
    """Hits de `GET /api/v1/search?q=…&size=…` para el usuario de ``request``.

    Fail-closed: cualquier respuesta que no sea 200 o un cuerpo inesperado se
    responde 503 ``search_unavailable``; nunca una lista vacía que parezca «no hay».
    """
    headers = cabeceras_internas(tenant_slug=slug_de(request))
    try:
        async with httpx.AsyncClient(base_url=settings.document_service_url, timeout=10.0) as client:
            resp = await client.get(_RUTA_SEARCH, params={"q": q, "size": size}, headers=headers)
    except httpx.HTTPError as exc:
        raise _no_disponible() from exc
    if resp.status_code != 200:
        raise _no_disponible()
    hits = resp.json().get("hits")
    if not isinstance(hits, list):
        raise _no_disponible()
    return hits

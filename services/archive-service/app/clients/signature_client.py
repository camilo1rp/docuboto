# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del signature-service para firmar el índice al cierre (E15-F3 → E06).

Best-effort: si el signature-service no está disponible, devuelve None y el cierre
del expediente NO se bloquea (el índice queda 'vigente' y puede firmarse luego).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def sign_xml(
    *,
    tenant_slug: str,
    payload_xml: str,
    objeto_id: str,
    firmante_id: str | None,
) -> str | None:
    body = {
        "payload_xml": payload_xml,
        "objeto_tipo": "indice",
        "objeto_id": objeto_id,
        "formato": "XAdES",
    }
    if firmante_id:
        body["firmante_id"] = firmante_id
    headers = {"X-Tenant-Slug": tenant_slug}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=5.0
        ) as client:
            resp = await client.post("/api/v1/signature/sign-xml", json=body, headers=headers)
            if resp.status_code in (200, 201):
                return resp.json().get("firma_id")
            logger.warning("signature-service → HTTP %s al firmar índice", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("signature-service inalcanzable al firmar índice: %s", exc)
    return None

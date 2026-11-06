# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Registro y bytes, mediados por document-service.

Las dos rutas internas vuelven a comprobar el nivel habilitado. Este
cliente no habla con el almacén: sin el token interno no llama.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from knowledge.core.config import settings

#: Tope de lo que esta pasarela retiene para calcular la huella antes de
#: responder. Por encima, no hay cuerpo parcial.
_TOPE = 32 * 1024 * 1024


class DocumentoError(Exception):
    """document-service no entregó un cuerpo usable."""


def _cabeceras(slug: str) -> dict[str, str]:
    token = settings.document_internal_token.strip()
    if not token:
        raise DocumentoError("sin token")
    return {"X-Tenant-Slug": slug, "X-Internal-Token": token}


async def leer_registro(slug: str, radicado_id: uuid.UUID) -> dict[str, Any] | None:
    """La fila oficial, None si document-service responde 404, o lanza."""
    ruta = f"/api/v1/internal/documentos/radicados/{radicado_id}"
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=10.0
        ) as client:
            resp = await client.get(ruta, headers=_cabeceras(slug))
    except httpx.HTTPError as exc:
        raise DocumentoError("red") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise DocumentoError("documento")
    cuerpo = resp.json()
    if not isinstance(cuerpo, dict):
        raise DocumentoError("documento")
    return cuerpo


async def leer_anexo(slug: str, anexo_id: uuid.UUID) -> bytes | None:
    """Los bytes, None si document-service responde 404, o lanza."""
    ruta = f"/api/v1/internal/documentos/anexos/{anexo_id}/contenido"
    headers = _cabeceras(slug)
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=30.0
        ) as client:
            async with client.stream("GET", ruta, headers=headers) as resp:
                if resp.status_code == 404:
                    return None
                if resp.status_code != 200:
                    raise DocumentoError("documento")
                trozos: list[bytes] = []
                total = 0
                async for trozo in resp.aiter_bytes():
                    total += len(trozo)
                    if total > _TOPE:
                        raise DocumentoError("tamano")
                    trozos.append(trozo)
    except httpx.HTTPError as exc:
        raise DocumentoError("red") from exc
    return b"".join(trozos)

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Bytes de un anexo, mediados por document-service.

La ruta interna comprueba de nuevo el nivel habilitado. Este cliente no
habla con el almacén: sin el token interno no llama.
"""

from __future__ import annotations

import uuid

import httpx

from app.core.config import settings

#: Tope de lo que esta pasarela retiene para calcular la huella antes de
#: responder. Por encima, no hay cuerpo parcial.
_TOPE = 32 * 1024 * 1024


class DocumentoError(Exception):
    """document-service no entregó un cuerpo usable."""


async def leer_anexo(slug: str, anexo_id: uuid.UUID) -> bytes | None:
    """Los bytes, None si document-service responde 404, o lanza."""
    token = settings.document_internal_token.strip()
    if not token:
        raise DocumentoError("sin token")
    ruta = f"/api/v1/internal/documentos/anexos/{anexo_id}/contenido"
    headers = {"X-Tenant-Slug": slug, "X-Internal-Token": token}
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

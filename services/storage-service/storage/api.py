# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Write path for stored bytes. Other packages import this module, not the routers.

The unified process binds this from the composition root so document can save
without an HTTP hop. The split storage image still serves `/upload`.
"""

from __future__ import annotations

import uuid
from typing import Any

from storage.core.database import get_pool
from storage.services.storage_service import StorageService


async def guardar(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    content: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Persists `content` in the tenant bucket. Returns the FileResponse as a dict."""
    pool = get_pool()
    async with pool.acquire() as conn:
        try:
            servicio = StorageService(conn, tenant_slug)
            respuesta, _creado, _programa = await servicio.upload(
                filename=filename,
                mime_type=mime_type,
                content=content,
                uploaded_by=uploaded_by,
            )
            return respuesta.model_dump(mode="json")
        finally:
            await conn.execute("SET search_path = public")

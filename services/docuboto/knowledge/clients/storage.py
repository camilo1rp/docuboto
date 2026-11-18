# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lee los bytes de un archivo ya subido. No decide si salen: eso es del punto de paso."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import httpx

from knowledge.core.config import settings

TOPE = 104_857_600


@dataclass(frozen=True)
class Archivo:
    """Metadatos y bytes de un objeto en storage."""

    id: uuid.UUID
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    contenido: bytes


@dataclass(frozen=True)
class Falta:
    """No hay archivo que se pueda enviar."""

    motivo: str


async def leer(file_id: uuid.UUID, *, slug: str) -> Archivo | Falta:
    """Los bytes si caben en el tope del proveedor. Si no, el motivo."""
    token = settings.storage_internal_token.strip()
    if not token:
        return Falta("destino")
    headers = {"X-Tenant-Slug": slug, "X-Internal-Token": token}
    base = settings.storage_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
            meta = await client.get(f"/api/v1/storage/files/{file_id}", headers=headers)
            if meta.status_code == 404:
                return Falta("file_not_found")
            if meta.status_code != 200:
                return Falta("storage")
            cuerpo = meta.json()
            tamano = int(cuerpo.get("file_size") or 0)
            if tamano > TOPE:
                return Falta("too_large")
            contenido = await client.get(
                f"/api/v1/storage/files/{file_id}/content", headers=headers
            )
    except (httpx.HTTPError, ValueError, TypeError):
        return Falta("storage")
    if contenido.status_code == 404:
        return Falta("file_not_found")
    if contenido.status_code != 200:
        return Falta("storage")
    bytes_ = contenido.content
    if len(bytes_) > TOPE:
        return Falta("too_large")
    huella = str(cuerpo.get("sha256") or "").strip().lower()
    if hashlib.sha256(bytes_).hexdigest() != huella:
        return Falta("content_mismatch")
    mime = str(cuerpo.get("mime_type") or "application/octet-stream").strip()
    nombre = str(cuerpo.get("filename") or "documento").strip()
    return Archivo(
        id=file_id,
        filename=nombre,
        media_type=mime,
        size_bytes=len(bytes_),
        sha256=huella,
        contenido=bytes_,
    )

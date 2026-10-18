# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente de storage-service para la RENOVACIÓN del sello de archivo (B10 §8).

Dos operaciones, ambas servicio-a-servicio (`X-Internal-Token`, sin identidad de usuario):

  * `descargar_xml`: el XML firmado vigente de una firma (URL prefirmada del almacén, como hace
    archive-service). Devuelve los BYTES tal cual, sin decodificar: se contrastan con su huella.
  * `renovar_artefacto`: `POST /api/v1/preservacion/renovar-artefacto-internal`. Storage hereda la
    política WORM del XML anterior en el nuevo y deja el evento PREMIS `RENOVACION`. Idempotente
    por `firma_id` + `ordinal`: el job puede reintentarlo.

NO se toca `storage_client.py` (la subida del XML renovado usa `upload_signed_xml` tal cual).
"""

from __future__ import annotations

import uuid

import httpx

from app.core.config import settings


class PreservacionError(RuntimeError):
    """storage-service no pudo (o no quiso) completar la operación."""


def _headers(tenant_slug: str) -> dict[str, str]:
    return {"X-Tenant-Slug": tenant_slug, "X-Internal-Token": settings.storage_internal_token}


async def descargar_xml(*, tenant_slug: str, minio_key: str) -> bytes:
    """Descarga el objeto `minio_key` (`{file_id}/{nombre}`) y devuelve sus bytes."""
    file_id = minio_key.split("/", 1)[0]
    try:
        async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=20.0) as client:
            resp = await client.get(
                f"/api/v1/storage/files/{file_id}/download", headers=_headers(tenant_slug)
            )
            if resp.status_code != 200:
                raise PreservacionError(f"storage-service → HTTP {resp.status_code} al presignar")
            descarga = await client.get(resp.json()["url"])
            if descarga.status_code != 200:
                raise PreservacionError(f"almacén → HTTP {descarga.status_code} al descargar")
            return descarga.content
    except httpx.RequestError as exc:
        raise PreservacionError(f"storage-service inalcanzable: {exc}") from exc


async def renovar_artefacto(
    *,
    tenant_slug: str,
    file_id_anterior: uuid.UUID,
    file_id_nuevo: uuid.UUID,
    sha256_nuevo: str,
    firma_id: uuid.UUID,
    ordinal: int,
) -> dict:
    """Registra la renovación en storage. Devuelve `{protegido, retain_until, evento_id, ...}`."""
    body = {
        "file_id_anterior": str(file_id_anterior),
        "file_id_nuevo": str(file_id_nuevo),
        "sha256_nuevo": sha256_nuevo,
        "firma_id": str(firma_id),
        "ordinal": ordinal,
    }
    try:
        async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=30.0) as client:
            resp = await client.post(
                "/api/v1/preservacion/renovar-artefacto-internal",
                json=body,
                headers=_headers(tenant_slug),
            )
    except httpx.RequestError as exc:
        raise PreservacionError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code != 200:
        raise PreservacionError(
            f"storage-service → HTTP {resp.status_code} al renovar el artefacto: {resp.text[:300]}"
        )
    return resp.json()

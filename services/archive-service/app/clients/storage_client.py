# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del storage-service para descargar el índice YA FIRMADO (E06 Inc.1).

Usado solo por `IndexService.verify` para contrastar la firma XAdES-B contra
el XML realmente sellado (no el XML en claro que persiste `expediente_indice.xml`
— la canonicalización C14N 2.0 de exhibición nunca coincidirá con el
digest exc-c14n de XAdES, ver `app/services/index_builder.py` y el docstring
de `app.providers.local` en signature-service; no se intenta reconciliar).

archive-service NUNCA habla con MinIO directamente (aislamiento por servicio,
igual que signature-service): pide una URL prefirmada a storage-service y
sigue esa URL. Best-effort: cualquier fallo devuelve ``None`` — el llamador
no declara conforme una firma que no pudo verificar (RF-FIR-15).

D-02 (remediación no-read-up en anexos, RF-SEG-08): `GET /files/{id}/download`
en storage-service ahora exige `X-Internal-Token` — este cliente es una
llamada de servicio-a-servicio legítima (descarga el índice ya firmado para
verificar su firma, no un anexo de usuario) y envía el mismo secreto
compartido que storage-service espera (`settings.storage_internal_token`).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def download_signed_xml(*, tenant_slug: str, minio_key_firmado: str) -> str | None:
    """Descarga el XML firmado identificado por `minio_key_firmado`.

    `minio_key_firmado` tiene la forma ``"{file_id}/{filename}"`` (ver
    ``signature-service.app.clients.storage_client.upload_signed_xml``): el
    primer segmento es el ``file_id`` que espera el endpoint de descarga de
    storage-service. Ese endpoint no expone bytes directamente, solo una URL
    prefirmada de MinIO (`GET /files/{file_id}/download`), que se sigue con
    una segunda petición.
    """
    file_id = minio_key_firmado.split("/", 1)[0]
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                f"/api/v1/storage/files/{file_id}/download", headers=headers
            )
            if resp.status_code != 200:
                logger.warning(
                    "storage-service → HTTP %s al presignar el índice firmado",
                    resp.status_code,
                )
                return None
            url = resp.json()["url"]
            download = await client.get(url)
            if download.status_code != 200:
                logger.warning(
                    "MinIO → HTTP %s al descargar el índice firmado", download.status_code
                )
                return None
            return download.text
    except httpx.RequestError as exc:
        logger.warning("storage-service inalcanzable al descargar el índice firmado: %s", exc)
    return None

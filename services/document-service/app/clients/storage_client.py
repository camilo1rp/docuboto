# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente interno del storage-service para mediar la descarga de anexos.

Cierra la fuga de no-read-up (RF-SEG-08) en `GET /storage/files/{id}/download`:
ese endpoint devolvía una presigned URL de MinIO SIN validar el
`nivel_seguridad` del radicado dueño del anexo. document-service es quien SÍ
conoce esa clasificación (`radicados.nivel_seguridad`), así que MEDIA la
descarga: resuelve el clearance del llamante (ver
`app/routers/documents.py::download_anexo`) y solo entonces pide los BYTES a
storage-service — NUNCA una presigned URL, que el cliente podría reenviar sin
pasar de nuevo por el gate.

storage-service protege el endpoint de bytes con `X-Internal-Token` (D-02,
mismo patrón que `knowledge-service./ingest`); este cliente es el único lugar
de document-service que conoce ese secreto. document-service NUNCA habla con
MinIO directamente — mismo aislamiento que archive-service/signature-service.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings

_CHUNK_SIZE = 65536


class StorageContentError(Exception):
    """storage-service no respondió (o respondió con error) al pedir los bytes."""


class StorageContentStream:
    """Envoltorio del stream HTTP hacia storage-service.

    Mantiene abiertos el `httpx.AsyncClient` y la respuesta durante la vida de
    la `StreamingResponse` del router; `aclose()` libera ambos. Trade-off MVP:
    una conexión httpx dedicada por descarga (sin pool compartido) — igual que
    los demás clients del servicio (`tenant_client.py`). El streaming en sí ES
    real de punta a punta: los bytes del anexo NUNCA se acumulan enteros en
    memoria de document-service (a diferencia, por ejemplo, de
    `signature-service.app.clients.storage_client.upload_signed_xml`, que sí
    carga el contenido completo por tratarse de un XML de índice, del orden de
    kilobytes).
    """

    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response

    @property
    def content_type(self) -> str | None:
        return self._response.headers.get("content-type")

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(_CHUNK_SIZE)

    async def aclose(self) -> None:
        await self._response.aclose()
        await self._client.aclose()


async def upload_bytes(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    data: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> dict:
    """Sube `data` a storage-service (`POST /upload`) y devuelve el registro del
    fichero (`file_id`, `sha256`, …). Lo usa la IMPORTACIÓN (E11/INT-05) para
    persistir los binarios del paquete. Va scoped al `tenant_slug` del llamante →
    el objeto aterriza en el bucket de ESE tenant. Lanza `StorageContentError`
    ante fallo de red o HTTP != 2xx (el servicio de import lo traduce a error del
    ítem sin dejar estado parcial en BD)."""
    headers = {"X-Tenant-Slug": tenant_slug}
    if uploaded_by is not None:
        headers["X-User-Id"] = str(uploaded_by)
    files = {"file": (filename, data, mime_type or "application/octet-stream")}
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=60.0) as client:
        try:
            resp = await client.post("/api/v1/storage/upload", headers=headers, files=files)
        except httpx.RequestError as exc:
            raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise StorageContentError(
            f"storage-service → HTTP {resp.status_code} al subir '{filename}': "
            f"{resp.text[:300]!r}"
        )
    return resp.json()


async def open_content_stream(
    *, tenant_slug: str, file_id: uuid.UUID
) -> StorageContentStream | None:
    """Abre un stream de los bytes de `file_id`. `None` si storage-service
    responde 404 (objeto purgado/inexistente pese a que el anexo sigue
    referenciado — no debería ocurrir en operación normal, pero el llamante
    lo traduce al mismo 404 neutro que cualquier otro camino de denegación).

    Lanza `StorageContentError` ante cualquier otro fallo (red o HTTP != 200):
    el router lo traduce en 503, nunca en un 200 con contenido parcial/vacío.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    client = httpx.AsyncClient(base_url=settings.storage_service_url, timeout=30.0)
    try:
        request = client.build_request(
            "GET", f"/api/v1/storage/files/{file_id}/content", headers=headers
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc

    if response.status_code == 404:
        await response.aclose()
        await client.aclose()
        return None
    if response.status_code != 200:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        raise StorageContentError(
            f"storage-service → HTTP {response.status_code} al pedir el contenido "
            f"de {file_id}: {body[:300]!r}"
        )

    return StorageContentStream(client, response)

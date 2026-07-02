# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente HTTP del storage-service (E06 Inc.1 → E07).

Sube el XML firmado (XAdES-B) del índice electrónico al mismo storage donde
viven los demás anexos del tenant (bucket ``orpycamcp-{slug}-documents``,
miniopy-async — que solo vive en storage-service, ADR multi-tenant).
signature-service NUNCA habla con MinIO directamente.

A diferencia de ``archive-service.app.clients.signature_client`` (que trata
todo ``sign-xml`` como best-effort porque el cierre de expediente no debe
bloquearse), esta subida NO es best-effort: si falla, ``POST /sign-xml``
completo debe fallar — no tiene sentido persistir una fila `firma` XAdES sin
que su XML firmado quede accesible en storage. El llamador ya considera
``/sign-xml`` en su conjunto como best-effort (ver contrato RT-16, "Decisión
del usuario": si la firma falla, el índice queda `pendiente_firma`, el cierre
del expediente no se bloquea).
"""

from __future__ import annotations

import uuid

import httpx

from app.core.config import settings


class StorageUploadError(RuntimeError):
    """El storage-service no aceptó (o no respondió a) la subida del XML firmado."""


async def upload_signed_xml(
    *,
    tenant_slug: str,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> str:
    """Sube el XML firmado y devuelve su ``object_key`` en MinIO.

    ``FileResponse`` (storage-service) no expone ``object_key`` directamente —
    solo el ``id`` del archivo. La clave se reconstruye de forma determinista
    con el mismo esquema que usa ``StorageService.upload``:
    ``object_key = f"{file_id}/{filename}"`` (ver
    ``storage-service/app/services/storage_service.py``). Se pasa el MISMO
    ``filename`` que se envía en el multipart para que la reconstrucción sea
    exacta incluso si el archivo se deduplicó (``created=False``): en ese
    caso el `file_id` devuelto es el del archivo YA EXISTENTE, cuyo
    `object_key` real fue calculado con SU PROPIO filename original — si ese
    filename difiere del que enviamos aquí, la clave reconstruida sería
    incorrecta. Como el filename de un índice firmado es siempre determinista
    (``indice-{expediente_id}-v{version}-firmado.xml``), la deduplicación por
    SHA-256 solo ocurre entre re-subidas del MISMO contenido con el MISMO
    nombre (p. ej. un reintento de firma sin cambios), donde la reconstrucción
    sigue siendo válida.
    """
    headers = {"X-Tenant-Slug": tenant_slug}
    if uploaded_by is not None:
        headers["X-User-Id"] = str(uploaded_by)
    files = {"file": (filename, content, "application/xml")}
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=10.0
        ) as client:
            resp = await client.post("/api/v1/storage/upload", files=files, headers=headers)
    except httpx.RequestError as exc:
        raise StorageUploadError(f"storage-service inalcanzable: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise StorageUploadError(
            f"storage-service → HTTP {resp.status_code} al subir el índice firmado: "
            f"{resp.text[:300]}"
        )
    file_id = resp.json()["id"]
    return f"{file_id}/{filename}"

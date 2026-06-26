# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import get_db_conn
from app.schemas.storage import (
    FileResponse,
    IntegrityResponse,
    PresignedUrlResponse,
)
from app.services.storage_service import (
    NotCurrentVersionError,
    StorageService,
    UnsupportedFormatError,
)

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "X-Tenant-Slug header is required",
                "status": 400,
            },
        )
    return x_tenant_slug


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    if x_user_id is None:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        return None


@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
)
async def upload_file(
    file: UploadFile,
    tenant_slug: str = Depends(_require_tenant),
    uploaded_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    content = await file.read()
    service = StorageService(conn, tenant_slug)
    try:
        response, created = await service.upload(
            filename=file.filename or "unknown",
            mime_type=file.content_type,
            content=content,
            uploaded_by=uploaded_by,
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error": "unsupported_media_type",
                "detail": str(exc),
                "status": 415,
            },
        )
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.get(
    "/files/{file_id}/verify",
    response_model=IntegrityResponse,
    summary="Re-verify the SHA-256 of the stored object (RF-DIG-02)",
)
async def verify_file_integrity(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IntegrityResponse:
    service = StorageService(conn, tenant_slug)
    result = await service.verify_integrity(file_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "file_not_found", "detail": str(file_id), "status": 404},
        )
    if not result.integrity_ok:
        # El objeto en MinIO no coincide con la huella registrada: alerta de integridad.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "integrity_check_failed",
                "detail": (
                    f"SHA-256 no coincide: esperado {result.expected_sha256}, "
                    f"obtenido {result.actual_sha256}"
                ),
                "status": 409,
            },
        )
    return result


@router.post(
    "/files/{file_id}/replace",
    response_model=FileResponse,
    summary="Replace a file — creates a new immutable version (RF-DIG-01)",
)
async def replace_file(
    file_id: uuid.UUID,
    file: UploadFile,
    tenant_slug: str = Depends(_require_tenant),
    uploaded_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    content = await file.read()
    service = StorageService(conn, tenant_slug)
    try:
        result = await service.replace(
            file_id,
            filename=file.filename or "unknown",
            mime_type=file.content_type,
            content=content,
            uploaded_by=uploaded_by,
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "unsupported_media_type", "detail": str(exc), "status": 415},
        )
    except NotCurrentVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "not_current_version", "detail": str(exc), "status": 409},
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"File {file_id} not found",
                "status": 404,
            },
        )
    response, created = result
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.get(
    "/files/{file_id}/versions",
    response_model=list[FileResponse],
    summary="List the version chain of a file (RF-DIG-01)",
)
async def list_file_versions(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[FileResponse]:
    service = StorageService(conn, tenant_slug)
    result = await service.list_versions(file_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"File {file_id} not found",
                "status": 404,
            },
        )
    return result


@router.get(
    "/files/{file_id}",
    response_model=FileResponse,
    summary="Get file metadata",
)
async def get_file_metadata(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FileResponse:
    service = StorageService(conn, tenant_slug)
    result = await service.get_metadata(file_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"File {file_id} not found",
                "status": 404,
            },
        )
    return result


@router.get(
    "/files/{file_id}/download",
    response_model=PresignedUrlResponse,
    summary="Get presigned download URL",
)
async def get_presigned_url(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PresignedUrlResponse:
    service = StorageService(conn, tenant_slug)
    result = await service.get_presigned_url(file_id, settings.presigned_url_expiry)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"File {file_id} not found",
                "status": 404,
            },
        )
    return result


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file",
)
async def delete_file(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    deleted_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    service = StorageService(conn, tenant_slug)
    deleted = await service.delete(file_id, deleted_by=deleted_by)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"File {file_id} not found",
                "status": 404,
            },
        )

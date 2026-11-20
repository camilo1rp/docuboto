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
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse

from storage.core.clearance import require_permission
from storage.core.config import settings
from storage.core.database import get_db_conn
from storage.schemas.storage import FileResponse
from storage.services.pdfa_ingest_service import (
    PdfaNonConformantError,
    run_ingest_validation_background,
)
from storage.services.storage_service import StorageService, UnsupportedFormatError

# Los demás dominios no llegan aquí: leen, suben artefactos y purgan por
# `storage.api` (ADR-047 §1). Esta es la puerta del navegador.
router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


def _reject_if_too_large(file: UploadFile) -> None:
    """413 ANTES de `file.read()`, que es lo que copia el fichero a memoria.

    Starlette ya ha escrito el fichero a un `SpooledTemporaryFile` (en disco si
    supera su umbral) y conoce su tamaño, así que este chequeo es gratis. Hacerlo
    después de leer sería rechazar la subida una vez pagado su coste completo — el
    orden es la mitad de la defensa.
    """
    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "detail": (
                    f"El archivo ({file.size} bytes) supera el máximo admitido "
                    f"({settings.max_upload_bytes} bytes)."
                ),
                "status": 413,
            },
        )


def _optional_user(request: Request) -> uuid.UUID | None:
    """Identidad opcional: sale de `request.state.usuario`, no de `X-User-Id`."""
    from orpycamcp_common.usuario import id_opcional

    return id_opcional(request)

@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
    # ÚNICA ruta del almacén expuesta al exterior, y **no tenía ningún gate**:
    # cualquier autenticado de la entidad subía archivos al almacén, con el
    # coste y la superficie que eso abre.
    #
    # `PERM_RADI` es el permiso, no uno inventado: el único consumidor es el
    # flujo de radicación (`frontend/src/routes/(app)/radicar/api/storage`), que
    # sube el anexo antes de radicar. Quien radica ya tiene este permiso, así que
    # el gate **no deja fuera a nadie que hoy trabaje** — que es la comprobación
    # que hay que hacer antes de encender un control que estaba apagado.
    #
    # Nivel 1 a propósito: `POST /documents` exige `PERM_RADI` con el mismo
    # nivel, y pedir aquí más que en el acto que este archivo acompaña sería
    # exigir de más — el defecto simétrico, que además falla en silencio.
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def upload_file(
    file: UploadFile,
    request: Request,
    background_tasks: BackgroundTasks,
    tenant_slug: str = Depends(_require_tenant),
    uploaded_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    _reject_if_too_large(file)
    content = await file.read()
    service = StorageService(conn, tenant_slug)
    try:
        response, created, schedule_pdfa_validation = await service.upload(
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
    except PdfaNonConformantError as exc:
        # Modo estricto opt-in (PRESERVATION_PDFA_INGEST_MODE=strict): el
        # archivo NUNCA se persistió (ni MinIO ni BD) — ver
        # StorageService.upload. 422 sobre non_conformant, sin filtrar
        # detalle interno del validador en la respuesta al cliente.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "pdfa_non_conformant",
                "detail": str(exc),
                "status": 422,
            },
        )
    if schedule_pdfa_validation:
        # E10 C2b follow-up (modo advisory, MVP `BackgroundTask`): corre
        # DESPUÉS de enviar la respuesta al cliente — `tenant_slug` es el
        # MISMO valor validado del header `X-Tenant-Slug` de ESTA petición
        # (nunca del body), capturado aquí explícitamente porque la
        # conexión `conn` (y su search_path) ya se habrá devuelto al pool
        # para cuando este task corra (FastAPI cierra las dependencias
        # `yield` antes de ejecutar los BackgroundTasks) — el worker
        # adquiere su PROPIA conexión del pool y fija su propio search_path.
        background_tasks.add_task(
            run_ingest_validation_background,
            request.app.state.db_pool,
            tenant_slug,
            response.id,
        )
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))

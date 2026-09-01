# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from urllib.parse import quote

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
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.clearance import require_permission
from app.core.config import settings
from app.core.database import get_db_conn
from app.core.internal_auth import require_internal_token as _require_internal_token
from app.repositories.file_repository import FileRepository
from app.schemas.storage import (
    FileHashesRequest,
    FileHashesResponse,
    FileResponse,
    IntegrityResponse,
    PresignedUrlResponse,
    PurgaDisposicionRequest,
    PurgaDisposicionResponse,
)
from app.services.pdfa_ingest_service import (
    PdfaNonConformantError,
    run_ingest_validation_background,
)
from app.services.storage_service import (
    NotCurrentVersionError,
    StorageService,
    UnsupportedFormatError,
    WormLockActiveError,
)

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


# `_require_internal_token` (D-02, remediación no-read-up en anexos): solo
# servicios internos pueden leer bytes/URL/metadata de un archivo directamente
# de storage-service — ver `app/core/internal_auth.py` para el detalle
# (extraído de aquí para que `routers/preservacion.py` lo reutilice, E10
# Increment C). Gatea los endpoints que servirían el CONTENIDO de un anexo si
# se llamaran directamente (presigned download, stream de bytes, metadata) —
# la mediación real del clearance (RF-SEG-08) vive en document-service
# (`GET /documents/{id}/anexos/{file_id}/download`), que reenvía este mismo
# token. `/upload` queda fuera: el frontend sube directo.


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


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    if x_user_id is None:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        return None


@router.post(
    "/internal/artefacto",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a system-produced artifact (internal only)",
    # Los artefactos que produce el SISTEMA no son anexos que sube una persona,
    # y hasta ahora compartían camino: el índice electrónico firmado viajaba por
    # `/upload` y storage lo rechazaba con 415 «Formato no admitido:
    # application/xml». La firma XAdES se producía entera —sello de tiempo
    # incluido— y se perdía al guardarla; el índice quedaba `pendiente_firma`
    # para siempre y ningún expediente podía cerrarse con índice firmado.
    #
    # La salida no era ensanchar la lista de anexos, que es un control sobre lo
    # que entra desde fuera (RF-DIG-04): habría permitido a cualquiera subir XML
    # como anexo para resolver un problema que no es de los anexos. Este
    # endpoint tiene su propia lista (`allowed_system_mime_types`) y está
    # sellado con `X-Internal-Token` (D-02) — el gateway no lo enruta, así que
    # solo lo alcanzan los servicios de la red interna.
    dependencies=[Depends(_require_internal_token)],
)
async def upload_system_artifact(
    file: UploadFile,
    tenant_slug: str = Depends(_require_tenant),
    uploaded_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    _reject_if_too_large(file)
    content = await file.read()
    service = StorageService(conn, tenant_slug)
    try:
        response, created, _ = await service.upload(
            filename=file.filename or "unknown",
            mime_type=file.content_type,
            content=content,
            uploaded_by=uploaded_by,
            formatos_admitidos=settings.allowed_system_mime_set,
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "unsupported_media_type", "detail": str(exc), "status": 415},
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/internal/anexo-externo",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an annex received through the public single window (internal only)",
    # Un anexo que llega por la VENTANILLA PÚBLICA no tiene detrás un usuario
    # autenticado, así que no puede pasar por `/upload` —que exige `PERM_RADI`—
    # y **tampoco** por `/internal/artefacto`: ese endpoint aplica
    # `allowed_system_mime_set`, la lista de lo que produce el propio sistema,
    # que admite XML. Reutilizarlo dejaría que un desconocido subiera XML como
    # anexo, que es exactamente el atajo contra el que advierte el comentario de
    # aquel endpoint.
    #
    # Esta puerta aplica `allowed_mime_set` —la lista de anexos, RF-DIG-04, que
    # es el control sobre lo que entra DESDE FUERA— y un tope propio muy por
    # debajo del general: quien sube aquí no está identificado.
    #
    # Sellada con `X-Internal-Token` (D-02) y no enrutada por el gateway: solo
    # la alcanza document-service, que ya validó el número de ficheros, su
    # tamaño y su contenido antes de llegar hasta aquí. Las dos capas son
    # deliberadas — la de document-service puede fallar y esta sigue siendo la
    # que gobierna el almacén.
    dependencies=[Depends(_require_internal_token)],
)
async def upload_public_annex(
    file: UploadFile,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    if file.size is not None and file.size > settings.max_public_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "detail": (
                    f"El archivo ({file.size} bytes) supera el máximo admitido en la "
                    f"ventanilla pública ({settings.max_public_upload_bytes} bytes)."
                ),
                "status": 413,
            },
        )
    content = await file.read()
    service = StorageService(conn, tenant_slug)
    try:
        response, created, _ = await service.upload(
            filename=file.filename or "anexo",
            mime_type=file.content_type,
            content=content,
            # ANÓNIMO de verdad: no se inventa un actor. Quien radicó por la
            # ventanilla no tiene identidad en el sistema, y atribuir el fichero
            # a un usuario del sistema falsearía la traza.
            uploaded_by=None,
            formatos_admitidos=settings.allowed_mime_set,
        )
    except UnsupportedFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "unsupported_media_type", "detail": str(exc), "status": 415},
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=response.model_dump(mode="json"),
    )


@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
    # ÚNICO endpoint de storage-service que el gateway expone al exterior (los
    # que sirven bytes, URL o metadata están sellados con `X-Internal-Token`,
    # D-02) y **no tenía ningún gate**: cualquier autenticado de la entidad
    # subía archivos al almacén, con el coste y la superficie que eso abre.
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


@router.get(
    "/files/{file_id}/verify",
    response_model=IntegrityResponse,
    # D-02: sellado servicio-a-servicio, igual que sus vecinos /download,
    # /content y la metadata. Ninguno de estos cuatro tiene llamante externo
    # (el gateway solo enruta /api/v1/storage/upload) y su unico control era
    # esa ausencia de ruta — que un salto `..` atravesaba. Sin gate se podia
    # SUSTITUIR el contenido de un anexo o BORRARLO con `deleted_by = None`:
    # corrupcion de evidencia sin actor en la traza.
    dependencies=[Depends(_require_internal_token)],
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
    # D-02: sellado servicio-a-servicio, igual que sus vecinos /download,
    # /content y la metadata. Ninguno de estos cuatro tiene llamante externo
    # (el gateway solo enruta /api/v1/storage/upload) y su unico control era
    # esa ausencia de ruta — que un salto `..` atravesaba. Sin gate se podia
    # SUSTITUIR el contenido de un anexo o BORRARLO con `deleted_by = None`:
    # corrupcion de evidencia sin actor en la traza.
    dependencies=[Depends(_require_internal_token)],
    summary="Replace a file — creates a new immutable version (RF-DIG-01)",
)
async def replace_file(
    file_id: uuid.UUID,
    file: UploadFile,
    tenant_slug: str = Depends(_require_tenant),
    uploaded_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    _reject_if_too_large(file)
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
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )
    response, created = result
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.get(
    "/files/{file_id}/versions",
    response_model=list[FileResponse],
    # D-02: sellado servicio-a-servicio, igual que sus vecinos /download,
    # /content y la metadata. Ninguno de estos cuatro tiene llamante externo
    # (el gateway solo enruta /api/v1/storage/upload) y su unico control era
    # esa ausencia de ruta — que un salto `..` atravesaba. Sin gate se podia
    # SUSTITUIR el contenido de un anexo o BORRARLO con `deleted_by = None`:
    # corrupcion de evidencia sin actor en la traza.
    dependencies=[Depends(_require_internal_token)],
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
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )
    return result


@router.get(
    "/files/{file_id}",
    response_model=FileResponse,
    summary="Get file metadata",
    dependencies=[Depends(_require_internal_token)],
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
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )
    return result


@router.post(
    "/internal/files/hashes",
    response_model=FileHashesResponse,
    summary="SHA-256 of a batch of files (internal only)",
    # Sellado servicio-a-servicio como sus vecinos: no tiene llamante externo
    # (el gateway solo enruta `/api/v1/storage/upload`) y la huella de un anexo
    # dice qué documentos custodia un expediente clasificado. Quien pregunta es
    # archive-service al componer el índice electrónico, no una persona.
    dependencies=[Depends(_require_internal_token)],
)
async def file_hashes_batch(
    payload: FileHashesRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FileHashesResponse:
    hashes = await FileRepository(conn, tenant_slug).hashes_for_ids(payload.file_ids)
    return FileHashesResponse(hashes=hashes)


@router.get(
    "/files/{file_id}/download",
    response_model=PresignedUrlResponse,
    summary="Get presigned download URL",
    dependencies=[Depends(_require_internal_token)],
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
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )
    return result


@router.get(
    "/files/{file_id}/content",
    summary="Stream the raw bytes of a file (internal mediation, D-02)",
    dependencies=[Depends(_require_internal_token)],
)
async def get_file_content(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> StreamingResponse:
    """Hace stream de los bytes crudos de un archivo — SOLO para mediadores
    internos (document-service, RF-SEG-08). Nunca se expone al exterior: la
    URL presigned de `/download` bastaría para un cliente de confianza, pero
    un mediador que decide el clearance por-request necesita los BYTES, no
    una URL que el llamante podría reenviar sin pasar por el gate.
    """
    service = StorageService(conn, tenant_slug)
    result = await service.get_object_stream(file_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )
    record, chunks = result
    # Content-Disposition a prueba de inyección (RFC 6266): filename es controlado
    # por el cliente en la subida; un `"`/CR/LF sin sanear rompe la cabecera.
    _ascii = (
        record.filename.encode("ascii", "ignore")
        .decode("ascii")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
        or "download"
    )
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{_ascii}\"; filename*=UTF-8''{quote(record.filename)}"
        ),
    }
    if record.file_size is not None:
        headers["Content-Length"] = str(record.file_size)
    return StreamingResponse(
        chunks,
        media_type=record.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file",
    # D-02: sellado servicio-a-servicio, igual que sus vecinos /download,
    # /content y la metadata. Sin gate, el borrado ocurría con
    # `deleted_by = None`: la traza registraba la baja de un anexo sin decir
    # quién la ordenó, que es peor que no registrarla.
    dependencies=[Depends(_require_internal_token)],
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
                "detail": "No existe el archivo solicitado",
                "status": 404,
            },
        )


@router.post(
    "/internal/purgar-disposicion",
    response_model=PurgaDisposicionResponse,
    summary="Purgar físicamente archivos por disposición final de TRD (internal only)",
    # La ÚNICA puerta al borrado físico, y llevaba sin puerta desde que se
    # escribió: `StorageService.purge_for_disposition` existía, tenía su gate
    # WORM y su asiento, y ningún router lo alcanzaba. Es el patrón que esta
    # revisión persigue —una capacidad construida y no ofrecida—, aquí sobre el
    # acto más irreversible del sistema.
    #
    # Sellado con `X-Internal-Token` (D-02) y NO enrutado por el gateway: nadie
    # de fuera puede pedir un borrado físico. Quien lo pide es archive-service,
    # ejecutando un lote de eliminación con acta firmada (RF-TRD-07 fase 5).
    dependencies=[Depends(_require_internal_token)],
)
async def purgar_disposicion(
    payload: PurgaDisposicionRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PurgaDisposicionResponse:
    """Borra los objetos de MinIO y sus filas, uno a uno.

    **Por archivo y no en bloque**: el gate WORM se evalúa por expediente y un
    fallo en uno no puede llevarse por delante los demás — quien orquesta
    necesita saber exactamente cuáles se fueron y cuáles no, porque de eso
    depende lo que la lápida podrá afirmar.

    `no_encontrados` no es un error: un archivo ya purgado (reintento tras un
    fallo a medias) es el estado que se buscaba.
    """
    service = StorageService(conn, tenant_slug)
    purgados: list[uuid.UUID] = []
    no_encontrados: list[uuid.UUID] = []
    for file_id in payload.file_ids:
        try:
            ok = await service.purge_for_disposition(
                file_id,
                payload.expediente_id,
                payload.motivo,
                actor=payload.actor,
            )
        except WormLockActiveError as exc:
            # El candado WORM aborta la purga ENTERA del expediente, no solo la
            # de este archivo: si un objeto sigue protegido, eliminar el resto
            # dejaría un expediente medio destruido y un índice que ya no
            # describe nada. Mejor no haber empezado.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "worm_lock_activo",
                    "detail": str(exc),
                    "status": 409,
                },
            ) from exc
        (purgados if ok else no_encontrados).append(file_id)
    return PurgaDisposicionResponse(purgados=purgados, no_encontrados=no_encontrados)

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Document router — radicado CRUD endpoints.

Tenant context comes from X-Tenant-Slug (injected by the api-gateway after JWT
validation). The get_tenant_conn dependency validates the slug and sets the
connection search_path to tenant_{slug} (ADR-012); X-User-Id carries the
authenticated user's UUID for the registered_by field.

Route order: /by-tracking/{tracking_number} is declared before /{document_id}
to avoid FastAPI matching the literal string "by-tracking" as a UUID.
"""

import uuid
from datetime import date
from typing import Any, Literal
from urllib.parse import quote

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.clients import storage_client
from app.clients.tenant_client import TenantServiceClient, TenantServiceError
from app.core.clearance import check_permission, require_permission, resolve_caller_clearance
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.document import (
    AnexoResponse,
    DispositionResponse,
    DispositionUpdate,
    DocumentCreate,
    DocumentResponse,
    DocumentUpdate,
    HistorialAccesoEntry,
    PublicacionRequest,
    PublicacionResponse,
    ReclassifyRequest,
    ReclassifyResponse,
)
from app.services.document_service import (
    AnexoNoDisponibleError,
    DestDeptImmutableError,
    DocumentAnuladoError,
    DocumentNotFoundError,
    DocumentService,
    InvalidDependenciaError,
    InvalidTenantError,
    ReclassifyForbiddenError,
)
from app.services.metadata_service import MetadataValidationError

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


def _tenant_slug(request: Request) -> str:
    # get_tenant_conn ya garantiza que el header está presente y es válido.
    return request.headers.get(_TENANT_HEADER, "")


def _actor_str(request: Request) -> str | None:
    """El actor como texto para `audit.append`, o `None` si no hay uno válido.

    `None` y no `""`: `audit.append` trataría la cadena vacía como un actor
    presente y archivaría el asiento sin poder atribuirlo a nadie, que es peor
    que decir explícitamente que no lo hay.
    """
    actor = _get_registered_by(request)
    return str(actor) if actor is not None else None


def _get_registered_by(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _content_disposition(filename: str) -> str:
    """Cabecera `Content-Disposition` a prueba de inyección (RFC 6266).

    `filename` lo controla el cliente en la subida; sin sanear, un `"` rompe la
    quoted-string (inyecta parámetros) y un CR/LF revienta la cabecera. Se emite
    un fallback ASCII entrecomillado saneado + `filename*=UTF-8''<pct-encoded>`
    para el nombre real (Unicode).
    """
    ascii_fallback = (
        filename.encode("ascii", "ignore")
        .decode("ascii")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
        or "download"
    )
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen de la petición (X-Canal, inyectado server-side por el
    api-gateway, D-03; excluido del whitelist de client request headers en
    `api-gateway/app/core/proxy.py::FORWARDED_REQUEST_HEADERS`, así que un
    cliente no puede forjarlo). Ausente => 'system' (no se audita como
    consulta de un usuario real). Gemelo de
    `archive-service/app/routers/expedientes.py::_optional_canal`."""
    return x_canal or "system"


def _build_service(
    conn: asyncpg.Connection,
    slug: str,
    tenant_client: TenantServiceClient | None = None,
    redis: Any | None = None,
) -> DocumentService:
    try:
        return DocumentService(conn, slug, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new radicado (document)",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def create_document(
    payload: DocumentCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: TenantServiceClient = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request), tenant_client, redis)
    try:
        return await service.create(payload, _get_registered_by(request))
    except ReclassifyForbiddenError as exc:
        # No-write-up: no se puede radicar por encima del propio clearance.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "clasificacion_forbidden", "detail": str(exc), "status": 403},
        )
    except AnexoNoDisponibleError as exc:
        # 422 y mensaje neutro: el mismo que daría un fichero inexistente. Un
        # código distinto para «cuelga de algo que no puedes ver» sería un
        # oráculo sobre el contenido de los radicados reservados.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "anexo_no_disponible", "detail": str(exc), "status": 422},
        )
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "metadata_validation_failed",
                "detail": str(exc),
                "status": 422,
                # Propiedad que incumple (`a.b`), para marcar el control.
                **({"field": exc.field} if exc.field else {}),
            },
        )
    except InvalidDependenciaError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_dependencia",
                "detail": str(exc),
                "status": 400,
            },
        )
    except TenantServiceError as exc:
        # El plazo (RF-RAD-04) o la dependencia no se pudieron resolver vía HTTP.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "tenant_service_unavailable",
                "detail": str(exc),
                "status": 502,
            },
        )


@router.get(
    "",
    response_model=list[DocumentResponse],
    summary="List radicados with optional filters",
)
async def list_documents(
    request: Request,
    response: Response,
    doc_type: str | None = Query(default=None),
    # `status` es REPETIBLE (`?status=distributed&status=in_progress`). Una
    # bandeja de trabajo pendiente no es un estado, son varios, y antes había
    # que pedirla con una llamada por estado y sumar los totales en el cliente
    # —dos ventanas de 20 que no se pueden ordenar entre sí—.
    status_filter: list[str] | None = Query(default=None, alias="status"),
    # Rango de fecha límite (RF-RAD-04), ambos extremos inclusivos. Lo que
    # significa «vencido» lo declara quien pregunta, no el endpoint: ver la nota
    # en `DocumentRepository.find_all`.
    due_from: date | None = Query(default=None),
    due_to: date | None = Query(default=None),
    order_by: Literal["registered_at", "due_date"] = Query(default="registered_at"),
    order_dir: Literal["asc", "desc"] = Query(default="desc"),
    # Dónde está el radicado AHORA (ADR-029): código de la dependencia donde lo
    # dejó el flujo o, si el flujo no lo ha movido, la de radicación. No es
    # `dest_dept_code`: ese es a quién iba dirigido y no cambia al repartir.
    ubicacion_dept_code: int | None = Query(default=None, ge=1, le=999999),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> list[DocumentResponse]:
    service = _build_service(conn, _tenant_slug(request))
    # Filtro por campo de metadato: ?meta.<campo>=valor (E03, índice GIN).
    metadata_filter = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    documents, total = await service.list(
        doc_type=doc_type,
        status=status_filter,
        max_clearance=clearance,
        metadata_filter=metadata_filter or None,
        page=page,
        size=size,
        user_id=_get_registered_by(request),
        canal=canal,
        due_from=due_from,
        due_to=due_to,
        order_by=order_by,
        order_dir=order_dir,
        ubicacion_dept_code=ubicacion_dept_code,
    )
    response.headers["X-Total-Count"] = str(total)
    return documents


@router.get(
    "/by-tracking/{tracking_number:path}",
    response_model=DocumentResponse,
    summary="Get radicado by tracking number (e.g. 2024-ICETEX-E-001234)",
)
async def get_document_by_tracking(
    tracking_number: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_tracking_number(
            tracking_number, clearance, user_id=_get_registered_by(request), canal=canal
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": f"No existe ningún radicado con el número «{tracking_number}»",
                "status": 404,
            },
        )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get radicado by UUID",
)
async def get_document(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> DocumentResponse:
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        return await service.get_by_id(
            document_id, clearance, user_id=_get_registered_by(request), canal=canal
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )


@router.get(
    "/{document_id}/historial-acceso",
    response_model=list[HistorialAccesoEntry],
    summary="Quién leyó y quién descargó este radicado (RF-BUS-10)",
)
async def get_historial_acceso(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[HistorialAccesoEntry]:
    """La traza de acceso existía; lo que faltaba era poder pedirla.

    `document.radicado_consultado` y `document.anexo_descargado` se escriben
    desde siempre, con actor, canal e instante. Pero solo se podían consultar
    desde `/admin/auditoria` —que exige `USUA_PERM_ADMIN` y saber por qué
    acción filtrar— o, si el radicado estaba en un expediente, desde el
    historial de ESE expediente. Un radicado suelto, que son la mayoría, no
    tenía respuesta.

    Gate: **no-read-up dentro del servicio**, comprobado ANTES de tocar
    `audit_log` — si no, el historial sería un oráculo sobre radicados que el
    llamante no puede ver.

    Lo que este docstring decía y NO era cierto: «el mismo del router
    (`PERM_RADI`)». Este router no declara `dependencies`, y las seis lecturas
    que sirve —listar, consultar, anexos, descarga y este historial— no llevan
    permiso: la línea que decide qué se ve es el clearance, dentro de la
    consulta. Es una decisión deliberada y el catálogo del MCP ya la declara
    así; lo que sobraba era la frase, que hacía creer protegido por permiso algo
    que no lo está — y alguien que extienda este router apoyándose en ella daría
    por puesto un control inexistente.

    Solo ACCESOS, no todo lo ocurrido: crear, anular o reclasificar son actos
    sobre el documento, no lecturas de él, y mezclarlos convertiría esto en un
    historial de todo, que responde otra pregunta.
    """
    service = _build_service(conn, _tenant_slug(request))
    try:
        items, total = await service.historial_acceso(
            document_id,
            user_id=_get_registered_by(request),
            page=page,
            size=size,
        )
    except DocumentNotFoundError:
        # Mismo cuerpo que el 404 de `get_document`, y a propósito: un radicado
        # por encima del clearance del llamante tiene que ser INDISTINGUIBLE de
        # uno que no existe, o el historial delataría lo que oculta.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{document_id}/anexos",
    response_model=list[AnexoResponse],
    summary="List a radicado's attachments, gated by clearance (RF-SEG-08)",
)
async def list_anexos(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[AnexoResponse]:
    """Lista los anexos de un radicado (metadatos: nombre, tamaño, tipo…) para
    poblar el panel de anexos; el fichero se obtiene con el endpoint de
    descarga mediada. No-read-up: un radicado inexistente o por encima del
    clearance del llamante devuelve el MISMO 404 neutro (no-oráculo).
    """
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        anexos = await service.list_anexos(document_id, clearance)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return [AnexoResponse.model_validate(a) for a in anexos]


@router.get(
    "/{document_id}/anexos/{file_id}/download",
    summary="Download an attachment, mediated by clearance (RF-SEG-08, D-02)",
)
async def download_anexo(
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    canal: str = Depends(_optional_canal),
) -> StreamingResponse:
    """Único punto de descarga de anexos hacia el exterior.

    document-service MEDIA la descarga: resuelve el clearance del llamante
    contra el `nivel_seguridad` VIGENTE del radicado (sin lag — nunca se
    denormaliza la clasificación al anexo/`files`) y verifica que `file_id`
    es efectivamente un anexo de `document_id`. Solo entonces pide los BYTES
    a storage-service (nunca una presigned URL — el cliente podría reenviarla
    y saltarse este gate) y los retransmite con streaming real.

    Todos los caminos de denegación devuelven el MISMO 404 neutro: radicado
    inexistente, sobre-clearance (no-read-up) y `file_id` ajeno al radicado
    quedan indistinguibles para el llamante — cierra el oráculo cruzado que
    tenía la descarga directa a storage-service.
    """
    tenant_slug = _tenant_slug(request)
    service = _build_service(conn, tenant_slug)
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        anexo = await service.get_anexo_for_download(
            document_id,
            file_id,
            clearance,
            user_id=_get_registered_by(request),
            canal=canal,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )

    try:
        stream = await storage_client.open_content_stream(
            tenant_slug=tenant_slug, file_id=anexo.file_id
        )
    except storage_client.StorageContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "storage_unavailable",
                "detail": str(exc),
                "status": 503,
            },
        )
    if stream is None:
        # El objeto ya no existe en storage (purgado) aunque el anexo siga
        # referenciado — mismo 404 neutro, no se distingue de "no autorizado".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )

    headers = {"Content-Disposition": _content_disposition(anexo.filename)}
    return StreamingResponse(
        stream.aiter_bytes(),
        media_type=anexo.mime_type or "application/octet-stream",
        headers=headers,
        background=BackgroundTask(stream.aclose),
    )


@router.get(
    "/{document_id}/disposition",
    response_model=DispositionResponse,
    summary="Get document disposition metadata (RF-MET-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_disposition(
    document_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DispositionResponse:
    """Lee la disposición final del radicado (RF-MET-08).

    No-read-up (RF-SEG-08): un radicado inexistente y uno por encima del clearance
    del llamante devuelven el MISMO 404 neutro (no-oráculo).
    """
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        disp = await service.get_disposition(document_id, clearance)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return DispositionResponse(radicado_id=document_id, disposition=disp)


@router.post(
    "/{document_id}/publicacion",
    response_model=PublicacionResponse,
    summary="Acto de publicación de oficio (Ley 1712/2014 art. 12)",
    # Mismo permiso que decide la RESERVA (`PERM_RECLASIFICAR`), con nivel de
    # escritura: publicar y clasificar son las dos caras de la misma competencia
    # sobre el régimen de acceso del documento. Repartirlas entre permisos
    # distintos permitiría que quien no puede desclasificar un documento lo
    # publicara igualmente.
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=3))],
)
async def set_publicacion(
    document_id: uuid.UUID,
    payload: PublicacionRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> PublicacionResponse:
    """Publica el radicado de oficio, o retira su publicación (11.1).

    Antes, «público» se DEDUCÍA de la ausencia de reserva: un radicado sin
    clasificar se cosechaba solo por OAI-PMH y se servía por CMIS. El art. 12 de
    la Ley 1712 pide lo contrario — un esquema de publicación, es decir, un acto
    por el que la entidad decide qué difunde. `publicable` arranca en FALSE y
    solo este endpoint lo cambia, dejando asiento.

    404 neutro para «no existe», «te supera el clearance», «no es de nivel
    público» y «está anulado»: distinguirlos convertiría este endpoint en un
    oráculo sobre radicados reservados.
    """
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        resultado = await service.set_publicacion(
            document_id,
            payload.publicar,
            payload.fundamento,
            clearance,
            actor=_actor_str(request),
            canal=request.headers.get("X-Canal") or "system",
            anexos_publicables=payload.anexos_publicables,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    except AnexoNoDisponibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "anexo_no_pertenece", "detail": str(exc), "status": 422},
        ) from exc
    return PublicacionResponse(**resultado)


@router.patch(
    "/{document_id}/disposition",
    response_model=DispositionResponse,
    summary="Set/update document disposition metadata (RF-MET-08)",
    # min_crud=3 (escritura), no el default 1 (lectura). Fijar la disposición
    # decide si un documento oficial se conserva o se DESTRUYE (Ley 594/2000
    # art. 24) y se exigía el mismo nivel que para leerla: cualquiera que
    # pudiera consultar la disposición podía marcar el radicado para
    # eliminación. Mismo umbral que las disposiciones de storage-service
    # (`routers/preservacion.py`), que ya trataban estos actos como de alto
    # privilegio e irreversibles.
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def set_disposition(
    document_id: uuid.UUID,
    payload: DispositionUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DispositionResponse:
    """Fija la disposición final del radicado (RF-MET-08).

    No-read-up (RF-SEG-08) con el mismo 404 neutro que el GET: la disposición
    decide conservación total frente a eliminación (Ley 594/2000 art. 24), así que
    un actor por debajo del nivel del documento no puede leerla NI fijarla.

    El acto queda asentado en `audit_log` con el estado anterior y el nuevo: la
    decisión de eliminar es irreversible, y hasta ahora no dejaba rastro de
    quién la había tomado (11.10).
    """
    service = _build_service(conn, _tenant_slug(request))
    clearance = await resolve_caller_clearance(conn, _get_registered_by(request))
    try:
        disp = await service.set_disposition(
            document_id,
            payload,
            clearance,
            actor=_actor_str(request),
            canal=request.headers.get("X-Canal") or "system",
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
        )
    return DispositionResponse(radicado_id=document_id, disposition=disp)


@router.patch(
    "/{document_id}/security-level",
    response_model=ReclassifyResponse,
    summary="Reclassify the security level of a radicado (E08, RF-SEG-08, Ley 594 art. 28)",
    # `min_crud=3`, no el default 1. Reclasificar decide el RÉGIMEN DE ACCESO de
    # un documento oficial —sube o baja su reserva— y eso es un acto de
    # escritura, no de consulta. Con el default, tener `PERM_RECLASIFICAR` para
    # LEER bastaba para desclasificar un radicado reservado.
    #
    # Sus dos gemelos ya lo tenían y su comentario dice por qué: `publicacion`
    # exige el mismo permiso con nivel 3 porque «publicar y clasificar son las
    # dos caras de la misma competencia sobre el régimen de acceso», y
    # `disposition` lo corrigió antes por la misma razón. Este era el tercer
    # hermano, y el único que se quedó en 1.
    #
    # No deja fuera a nadie que hoy trabaje: quien puede publicar ya necesita
    # nivel 3 sobre este mismo permiso, así que la competencia real ya estaba
    # repartida a ese nivel.
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=3))],
)
async def reclassify_security_level(
    document_id: uuid.UUID,
    payload: ReclassifyRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    redis: Any = Depends(get_redis),
) -> ReclassifyResponse:
    """Change the security level of a radicado.

    Enforces RF-SEG-08 clearance rules:
    - **no-read-up**: returns 404 (neutral) when the actor's clearance is below the
      current level, so under-clearance users cannot distinguish "not found" from
      "exists but classified above your level".
    - **no-write-up**: returns 403 when nuevo_nivel exceeds the actor's own clearance.
    - **idempotent**: 200 with the current state if the level is already at the
      requested value — safe to retry without duplicate audit entries or events.

    Audit: nivel_anterior, nivel_nuevo, and motivo are recorded in audit_log
    (Ley 594/2000 art. 28 — trazabilidad del ciclo de vida del documento).
    """
    service = _build_service(conn, _tenant_slug(request), redis=redis)
    actor = _get_registered_by(request)
    try:
        result = await service.reclassify(
            document_id,
            nuevo_nivel=payload.nivel_seguridad,
            motivo=payload.motivo,
            actor=actor,
            causal_reserva=payload.causal_reserva,
            fundamento_juridico=payload.fundamento_juridico,
            plazo_reserva_meses=payload.plazo_reserva_meses,
        )
        return ReclassifyResponse(**result)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )
    except ReclassifyForbiddenError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "reclassify_forbidden",
                "detail": "No puede clasificar por encima de su propio nivel de acceso",
                "status": 403,
            },
        )


@router.patch(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Update mutable fields of a radicado — tracking_number is immutable",
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> DocumentResponse:
    """Actualiza campos mutables — `tracking_number` es inmutable.

    No-read-up (RF-SEG-08): mismo 404 neutro para inexistente y sobre-clearance.
    El gate es imprescindible porque la respuesta devuelve el radicado COMPLETO.

    **Tipificar exige `crud >= 3`, el resto del PATCH no.** El gate del endpoint es
    `PERM_RADI` a nivel 1 y así se queda: subirlo entero rompería a quien hoy corrige
    un asunto o un número de folios, y alinear ese umbral con el de la radicación es
    una decisión propia de esta épica, no un efecto colateral de este cambio. Pero
    `doc_class` no es un campo más — fija la serie TRD y con ella la retención y la
    disposición final del documento (Ley 594/2000 art. 24) —, así que se comprueba
    aparte y con el umbral de escritura. El acto nuevo entra con su nivel correcto sin
    tocar el de los que ya existían.
    """
    # ADR-028: el estado lo mueve el flujo. Se rechaza EXPLÍCITAMENTE (422): un
    # campo ignorado en silencio haría creer al cliente que el estado cambió.
    if "status" in payload.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "status_managed_by_workflow",
                "detail": (
                    "El estado del radicado lo gobierna el flujo de trámite "
                    "(asignar, marcar leído, finalizar), no se edita con PATCH."
                ),
                "status": 422,
            },
        )
    service = _build_service(conn, _tenant_slug(request))
    actor = _get_registered_by(request)
    if payload.doc_class is not None and not await check_permission(
        conn, actor, "PERM_RADI", 3
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": (
                    "Tipificar un radicado exige PERM_RADI con nivel de creación "
                    "(crud >= 3): fija la serie TRD que gobierna su retención."
                ),
                "status": 403,
            },
        )
    clearance = await resolve_caller_clearance(conn, actor)
    try:
        return await service.update(document_id, payload, clearance, actor=actor)
    except MetadataValidationError as exc:
        # Mismo 422 y mismo cuerpo que en la radicación: retipificar valida contra
        # la plantilla del tipo nuevo, y quien lo intente tiene que recibir el
        # mismo mensaje que si lo hubiera radicado así de entrada. Sin esta
        # cláusula el fallo salía como 500 — un modo de fallo añadido sin su
        # respuesta, que es como una validación se convierte en una caída.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "metadata_validation_failed",
                "detail": str(exc),
                "status": 422,
                # Propiedad que incumple (`a.b`), para marcar el control.
                **({"field": exc.field} if exc.field else {}),
            },
        )
    except DestDeptImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "dest_dept_immutable",
                "detail": (
                    f"El radicado '{exc}' está cerrado: su dependencia de "
                    "radicación es dato histórico y no se modifica por PATCH."
                ),
                "status": 409,
            },
        )
    except DocumentAnuladoError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "radicado_anulado",
                "detail": (
                    f"El radicado '{exc}' está anulado y no admite modificaciones. "
                    "La anulación se decide por el circuito de anulación, no por PATCH."
                ),
                "status": 409,
            },
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "document_not_found",
                "detail": "No existe el radicado solicitado",
                "status": 404,
            },
        )

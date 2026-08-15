# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import date

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

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_db_conn
from app.schemas.archive import (
    AipResponse,
    BatchLinkRequest,
    BatchLinkResult,
    EventoPreservacionResponse,
    ExpedienteCreate,
    ExpedienteDisposicionResult,
    ExpedienteEventoResponse,
    ExpedienteIndice,
    ExpedienteRadicadoResponse,
    ExpedienteResponse,
    ExpedienteSearchHit,
    ExpedienteSearchResponse,
    ExpedienteUpdate,
    HistorialAccesoEntry,
    PublicacionRequest,
    PublicacionResponse,
    RadicadoLink,
    RolAutorizado,
)
from app.services.archive_service import ArchiveService
from app.services.metadata_service import MetadataValidationError

router = APIRouter(prefix="/api/v1/expedientes", tags=["expedientes"])


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
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Gemelo de `indice.py::_optional_canal` (E15 perfil v4, R3-a): canal de
    origen (X-Canal, inyectado server-side por el gateway, D-03). Ausente =>
    'system' (no se audita como consulta de un usuario real)."""
    return x_canal or "system"


@router.post(
    "",
    response_model=ExpedienteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_expediente(
    payload: ExpedienteCreate,
    tenant_slug: str = Depends(_require_tenant),
    opened_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    service = ArchiveService(conn, tenant_slug)
    try:
        return await service.create_expediente(payload, opened_by)
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "metadata_validation_failed",
                "detail": str(exc),
                "status": 422,
            },
        )


@router.get(
    "",
    response_model=list[ExpedienteResponse],
    summary="List expedientes",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_expedientes(
    response: Response,
    exp_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteResponse]:
    # No-read-up (RF-SEG-08): antes este listado no filtraba por `nivel_seguridad`
    # — cualquier portador de USUA_PERM_EXPEDIENTE enumeraba expedientes
    # RESERVADA/CLASIFICADA. Mismo criterio que `get_expediente`/`search_expedientes`.
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_expedientes(
        status=exp_status, page=page, size=size, user_id=user_id, canal=canal
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{expediente_id}/export.zip",
    summary="Exportar el expediente como ZIP (índice XML + manifiesto + anexos)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def export_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    # No-read-up (RF-SEG-08): 404 si el expediente supera el clearance del
    # llamante; si es visible, se exporta su contenido (bytes ensamblados por
    # storage-service). El nombre de archivo usa el `code` del expediente.
    service = ArchiveService(conn, tenant_slug)
    zip_bytes, filename = await service.export_expediente_zip(
        expediente_id, user_id=user_id, canal=canal
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/search",
    response_model=ExpedienteSearchResponse,
    summary="Full-text search of expedientes (E09, ADR-014)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def search_expedientes(
    request: Request,
    response: Response,
    q: str | None = Query(default=None, min_length=2, description="Texto (opcional)"),
    exp_status: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteSearchResponse:
    metadata_filter = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    # Pasa por ArchiveService (antes llamaba al repositorio directo) para dejar
    # la traza AGREGADA de consulta (RF-BUS-10) con el mismo criterio que
    # `list_expedientes` — ver `ArchiveService.search_expedientes`.
    rows, total = await ArchiveService(conn, tenant_slug).search_expedientes(
        q=q,
        status=exp_status,
        date_from=date_from,
        date_to=date_to,
        metadata_filter=metadata_filter or None,
        page=page,
        size=size,
        user_id=user_id,
        canal=canal,
    )
    response.headers["X-Total-Count"] = str(total)
    return ExpedienteSearchResponse(
        query=q,
        total=total,
        hits=[ExpedienteSearchHit(**dict(r)) for r in rows],
        page=page,
        size=size,
    )


@router.post(
    "/{expediente_id}/close",
    response_model=ExpedienteDisposicionResult,
    summary="Close expediente and materialize TRD disposition (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def close_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # H-D: identidad única resuelta en `app.core.actor.require_actor` —
    # X-User-Id es SIEMPRE la fuente de verdad, sin el fallback `or
    # x_username` de antes (código en la práctica inalcanzable, ya que
    # `require_permission(min_crud=3)` ya exige X-User-Id para autorizar, pero
    # que dejaba la puerta abierta a escribir una identidad inconsistente con
    # la de `transferencias.py` -- mismo funcionario, dos identidades según la
    # ruta). `actor.username` queda disponible si se necesitara como atributo
    # descriptivo, nunca como identidad.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteDisposicionResult:
    # `actor_id` es estrictamente X-User-Id (UUID de auth_users.id): es lo que
    # signature-service exige para sellar el índice (E06 Inc.1, RF-FIR-11/12).
    # Sin él la firma no se intenta y el índice queda 'pendiente_firma' — el
    # cierre no se bloquea (decisión del usuario, RT-16). Con `require_actor`
    # fail-closed, `actor.audit_actor` ya nunca es None en este handler.
    #
    # Respuesta estrecha (`ExpedienteDisposicionResult`), NO el expediente
    # completo: la disposición es ortogonal al clearance de lectura del
    # llamante (RF-SEG-08 Opción B) — devolver `ExpedienteResponse` aquí
    # exigiría propagar `user_id` para gatear la lectura, lo que produciría
    # un 404 espurio para quien tiene permiso de disposición pero no
    # clearance de lectura, pese a que el cierre ya se comprometió.
    return await ArchiveService(conn, tenant_slug).close_expediente(
        expediente_id, actor.audit_actor, actor_id=actor.audit_actor
    )


@router.post(
    "/{expediente_id}/publicacion",
    response_model=PublicacionResponse,
    summary="Acto de publicación de oficio del expediente (Ley 1712/2014 art. 12)",
    # Mismo permiso que decide la RESERVA (`PERM_RECLASIFICAR`) con nivel de
    # escritura, igual que el gemelo de document-service: publicar y clasificar
    # son las dos caras de la competencia sobre el régimen de acceso. Repartirlas
    # entre permisos distintos dejaría publicar a quien no puede desclasificar.
    dependencies=[Depends(require_permission("PERM_RECLASIFICAR", min_crud=3))],
)
async def set_publicacion_expediente(
    expediente_id: uuid.UUID,
    payload: PublicacionRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PublicacionResponse:
    """Publica el expediente de oficio, o retira su publicación.

    Este servicio conservaba `_PUBLIC = "nivel_seguridad = 1"` mientras
    document-service ya exigía el acto: el OAI EAD multinivel difundía todo
    expediente no clasificado, que es el valor por defecto. La corrección del
    art. 12 se había aplicado a un canal de tres.
    """
    # `actor.id` (UUID) para resolver el clearance; `actor.audit_actor` (el mismo
    # UUID como texto) para el asiento. La distinción no es cosmética: el helper
    # de identidad existe justo para que el mismo funcionario no acabe con dos
    # identidades según la ruta.
    clearance = await resolve_caller_clearance(conn, actor.id)
    resultado = await ArchiveService(conn, tenant_slug).set_publicacion(
        expediente_id,
        payload.publicar,
        payload.fundamento,
        clearance,
        actor=actor.audit_actor,
        canal=canal,
    )
    return PublicacionResponse(**resultado)


@router.post(
    "/{expediente_id}/transfer",
    response_model=ExpedienteDisposicionResult,
    summary="Transfer expediente (closed → transferred) (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def transfer_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # H-D: mismo helper único que `close_expediente` — ver su comentario.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteDisposicionResult:
    # Misma razón que `close_expediente`: respuesta estrecha, ortogonal al
    # clearance de lectura (RF-SEG-08 Opción B) — ver
    # `schemas.archive.ExpedienteDisposicionResult`.
    #
    # H2: la identidad (X-User-Id, fuente única — H-D) YA la exige
    # `require_permission(min_crud=3)` arriba para autorizar — antes este
    # handler ni siquiera la declaraba y un cambio de custodia irreversible
    # quedaba sin responsable identificado en la hoja de ruta/audit_log.
    return await ArchiveService(conn, tenant_slug).transfer_expediente(
        expediente_id, actor=actor.audit_actor
    )


@router.get(
    "/{expediente_id}/foliado",
    response_model=ExpedienteIndice,
    summary="Expediente foliation (ordered radicados) (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_foliado(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteIndice:
    return await ArchiveService(conn, tenant_slug).get_indice(expediente_id, user_id)


@router.get(
    "/{expediente_id}",
    response_model=ExpedienteResponse,
    summary="Get expediente by ID (includes linked radicados)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_expediente(expediente_id, user_id, canal=canal)


@router.get(
    "/{expediente_id}/aip",
    response_model=AipResponse,
    summary="AIP de preservación del expediente, mediado (no-read-up, E10/RF-SEG-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_aip(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Único punto de acceso EXTERNO al AIP de preservación: `GET
    /preservacion/aip/{id}` en storage-service ahora exige `X-Internal-Token`
    (D-02) — el gateway lo enruta pero NO reenvía ese token, así que una
    llamada externa directa recibe 401; este endpoint es la única vía externa.
    Resuelve el clearance del llamante contra el `nivel_seguridad` VIGENTE del
    expediente (storage-service no lo modela) y SOLO ENTONCES media la lectura
    hacia storage-service — 404 neutro si el expediente no existe, no es
    visible para el llamante, o simplemente no tiene AIP empaquetado
    (RF-SEG-08, ver `ArchiveService.get_aip`). La lectura exitosa deja pista de
    acceso (`archive.aip_consultado`, RF-BUS-10)."""
    service = ArchiveService(conn, tenant_slug)
    aip = await service.get_aip(expediente_id, user_id, canal)
    return AipResponse(**aip)


@router.get(
    "/{expediente_id}/preservacion/eventos",
    response_model=list[EventoPreservacionResponse],
    summary="Eventos PREMIS de preservación del expediente, mediado (no-read-up, E10/RF-SEG-08)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_eventos_preservacion(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[EventoPreservacionResponse]:
    """Único punto de acceso EXTERNO a los eventos de preservación de UN
    expediente: `GET /preservacion/eventos` en storage-service ahora exige
    `X-Internal-Token` (D-02) — el gateway lo enruta pero NO reenvía ese
    token, así que una llamada externa directa recibe 401; este endpoint es
    la única vía externa. Resuelve el clearance del llamante contra el
    `nivel_seguridad` VIGENTE del expediente (storage-service no lo modela) y
    SOLO ENTONCES media la lectura hacia storage-service, filtrando por
    `object_ref=str(expediente_id)` — 404 neutro si el expediente no existe o
    no es visible para el llamante (RF-SEG-08, ver
    `ArchiveService.get_eventos_preservacion`). Una lista vacía es una
    respuesta legítima (expediente visible sin actividad de preservación
    todavía), no un 404. La lectura exitosa deja pista de acceso
    (`archive.preservacion_eventos_consultados`, RF-BUS-10).

    Alcance: solo los eventos keyed por `object_ref=expediente_id`; los
    eventos keyed por `file_id`/`aip` id de documentos de este expediente NO
    se incluyen en este MVP (refinamiento semántico diferido)."""
    service = ArchiveService(conn, tenant_slug)
    eventos = await service.get_eventos_preservacion(expediente_id, user_id, canal)
    return [EventoPreservacionResponse(**e) for e in eventos]


@router.get(
    "/{expediente_id}/historial-acceso",
    response_model=list[HistorialAccesoEntry],
    summary="Historial de acceso (lecturas) al expediente/índice (E15 perfil v4, R3-b)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_historial_acceso(
    expediente_id: uuid.UUID,
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[HistorialAccesoEntry]:
    # Atestado en el índice firmado como `<pistaAuditoria>` (referencia
    # estable, sin contenido) — este endpoint es la consulta real, filtrada
    # por tenant + object_ref y paginada, con no-read-up (404 si el expediente
    # supera el clearance del llamante, ANTES de tocar audit_log).
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.get_historial_acceso(
        expediente_id, user_id, page=page, size=size
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{expediente_id}/roles-autorizados",
    response_model=list[RolAutorizado],
    summary="Snapshot vivo de grupos autorizados a leer el expediente (E15 perfil v4, R4-b)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_roles_autorizados(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[RolAutorizado]:
    # Deliberadamente NO firmado / NO incrustado en el índice (la matriz RBAC
    # cambia con el tiempo) — el índice solo referencia la POLÍTICA estable
    # (`<politicaAcceso clearanceMinimo=…>`), este endpoint expone la matriz
    # VIVA (grupos concretos) fuera de la firma.
    service = ArchiveService(conn, tenant_slug)
    return await service.get_roles_autorizados(expediente_id, user_id)


@router.get(
    "/{expediente_id}/eventos",
    response_model=list[ExpedienteEventoResponse],
    summary="Hoja de ruta append-only del expediente (E02 §9, RF-EXP-09)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_expediente_eventos(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteEventoResponse]:
    # Canal B / hoja de ruta operativa: acotada por la visibilidad del expediente para el
    # clearance del llamante (E08 §9 / RF-SEG-08); NO restringida a administrador.
    service = ArchiveService(conn, tenant_slug)
    return await service.list_eventos(expediente_id, user_id)


@router.patch(
    "/{expediente_id}",
    response_model=ExpedienteResponse,
    summary="Update expediente metadata (name/description/trd_serie_id) — does not transition status",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def update_expediente(
    expediente_id: uuid.UUID,
    payload: ExpedienteUpdate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    # El ciclo de vida (open→closed→transferred) transita EXCLUSIVAMENTE por
    # POST /{id}/close y POST /{id}/transfer (ambos crud>=3) — este PATCH ya no
    # acepta `status` en absoluto (`ExpedienteUpdate` con `extra="forbid"`, ver
    # su docstring): era una ruta alterna sin auditoría, sin disposición TRD
    # materializada, sin índice final y sin la guarda TOCTOU de
    # `transition_expediente` (Ley 594/2000 arts. 22-24; Acuerdo AGN 001/2024
    # art. 4.3.2.4).
    service = ArchiveService(conn, tenant_slug)
    return await service.update_expediente(expediente_id, payload, actor)


@router.post(
    "/{expediente_id}/radicados",
    response_model=ExpedienteRadicadoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a radicado to an expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def link_radicado(
    expediente_id: uuid.UUID,
    payload: RadicadoLink,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteRadicadoResponse:
    # `user_id` (M2, RF-SEG-08): resuelve el clearance real del llamante para
    # acotar la respuesta cuando no puede LEER el expediente — la vinculación
    # en sí SIEMPRE se ejecuta (ortogonal al clearance, ver
    # `ArchiveService.link_radicado`).
    service = ArchiveService(conn, tenant_slug)
    return await service.link_radicado(expediente_id, payload, user_id)


@router.post(
    "/{expediente_id}/radicados/batch",
    response_model=BatchLinkResult,
    summary="Inclusión masiva de radicados a un expediente (paridad legado)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def link_radicados_batch(
    expediente_id: uuid.UUID,
    payload: BatchLinkRequest,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> BatchLinkResult:
    # `user_id` (M2, RF-SEG-08): ver `link_radicado` — misma respuesta acotada.
    return await ArchiveService(conn, tenant_slug).link_radicados_batch(
        expediente_id, payload.radicados, user_id
    )


@router.delete(
    "/{expediente_id}/radicados/{radicado_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Excluir (soft-delete) un radicado del expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def unlink_radicado(
    expediente_id: uuid.UUID,
    radicado_id: uuid.UUID,
    causal: str | None = Query(default=None, max_length=500),
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    # E15 perfil v3 (RT-15 art. 4.3.2.3): la exclusión es SOFT-DELETE — el
    # vínculo permanece marcado 'excluido' para que el índice muestre el
    # hueco trazable, en vez de un DELETE físico. `actor` (X-User-Id de quien
    # excluye) se propaga a la hoja de ruta + audit_log (trazabilidad del
    # agente responsable, Ley 594/2000; Ac. AGN 001/2024 art. 4.3.2.3).
    service = ArchiveService(conn, tenant_slug)
    await service.unlink_radicado(expediente_id, radicado_id, causal=causal, actor=actor)

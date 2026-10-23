# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Preservación digital: plan + eventos PREMIS (E10)."""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.core.clearance import require_permission
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import get_db_conn
from app.core.internal_auth import require_internal_token
from app.core.minio_client import ObjectLockNotEnabledError, get_minio
from app.repositories.preservacion_repository import PreservacionRepository
from app.schemas.preservacion import (
    AipCreate,
    AipReempaquetarRequest,
    AipResponse,
    AipVersionItem,
    EstadoResponse,
    EventoCreate,
    EventoInternalCreate,
    EventoResponse,
    ExportZipCreate,
    FijarRetencionRequest,
    FijarRetencionResponse,
    HoldFijarRequest,
    HoldFijarResponse,
    HoldItem,
    HoldLevantarRequest,
    HoldLevantarResponse,
    MigracionResponse,
    MigrarFormatoRequest,
    PlanCreate,
    PlanResponse,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
    RenovarArtefactoRequest,
    RenovarArtefactoResponse,
    RenovarRetencionRequest,
    RenovarRetencionResponse,
    canonicalize_expediente_id,
)
from app.services.aip_builder import PremisValidationError
from app.services.aip_service import (
    AipAnteriorFixityMismatchError,
    AipNoEncontradoError,
    AipService,
    AipYaExisteError,
    AipYaSustituidoError,
    IndiceNoEsRenovacionError,
)
from app.services.format_migration_service import (
    FileNotFoundForMigrationError,
    FormatMigrationService,
    MigradorNoDisponibleError,
)
from app.services.format_migration_service import (
    # Alias explícito: `preservation_worm_service` exporta otra excepción con
    # el MISMO nombre, y dejar que una tape a la otra haría que un `except`
    # capturara la equivocada sin que nada avisara.
    FixityMismatchError as MigracionFixityMismatchError,
)
from app.services.legal_hold_service import (
    HoldActorRequeridoError,
    HoldMotivoNoCoincideError,
    HoldNoEncontradoError,
    HoldYaLevantadoError,
    LegalHoldService,
)
from app.services.preservation_worm_service import (
    ArtefactoNoEsDeFirmaError,
    FileNotFoundForPreservationError,
    FixityMismatchError,
    InvalidRetentionError,
    PreservationDisabledError,
    PreservationWormService,
    RetencionDiferidaSinHoldError,
    SinRetencionQueRenovarError,
    WormObjectNotFoundError,
    WormSingletonConflictError,
)
from app.services.storage_service import StorageService

router = APIRouter(prefix="/api/v1/preservacion", tags=["preservacion"])


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


def _actor(request: Request) -> str | None:
    """Actor de un evento de preservación: identidad ESTABLE, con el nombre al lado.

    Antes se sellaba el nombre de usuario a secas. No es forjable —lo inyecta el
    gateway desde el token validado— pero sí es MUTABLE en el sentido que importa
    aquí: un nombre se renombra y se reasigna, y estos eventos son metadatos de
    preservación que han de sostenerse durante décadas. Dentro de diez años,
    «jperez» puede ser otra persona.

    Se sella el identificador, que no cambia nunca, y se acompaña del nombre para
    que el registro siga siendo legible sin tener que resolver un UUID a mano.
    """
    uid = request.headers.get("X-User-Id")
    nombre = request.headers.get("X-Username")
    if uid and nombre:
        return f"{uid} ({nombre})"
    return uid or nombre


def _actor_uuid(request: Request) -> uuid.UUID | None:
    """El actor como UUID, para las columnas que lo son.

    `_actor` devuelve TEXTO porque prefiere `X-Username`, que es lo que se lee
    mejor en un evento PREMIS. Aquí hace falta el identificador: un
    `X-Username` no convertible resuelve a `None` en vez de reventar la
    petición — la migración es un acto de preservación y no debe fallar por el
    formato de una cabecera."""
    crudo = request.headers.get("X-User-Id")
    if not crudo:
        return None
    try:
        return uuid.UUID(crudo)
    except ValueError:
        return None


def _evento_response(row) -> EventoResponse:
    d = dict(row)
    raw = d.get("detalle")
    d["detalle"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return EventoResponse(**d)


@router.get(
    "/estado",
    response_model=EstadoResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def get_estado() -> EstadoResponse:
    """¿Está encendida la preservación en esta instalación?

    Es un dato de INSTANCIA (`PRESERVATION_ENABLED`), no de un expediente: no
    consulta ni filtra nada por expediente, así que no abre un oráculo
    no-read-up. Existe para que la pestaña Preservación de la UI diga «está
    desactivada en esta instalación» en vez de «sin AIP o sin visibilidad», que
    es lo que un 404 neutro hace pasar por normal cuando en realidad nada se
    empaqueta jamás. Mismo gate de lectura que `GET /plan` y que el mediador de
    archive-service para el AIP."""
    return EstadoResponse(enabled=settings.preservation_enabled)


@router.get(
    "/plan",
    response_model=PlanResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def get_plan(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PlanResponse:
    row = await PreservacionRepository(conn, tenant_slug).current_plan()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "plan_not_found", "detail": "Sin plan de preservación", "status": 404},
        )
    return PlanResponse(**dict(row))


@router.put(
    "/plan",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def set_plan(
    payload: PlanCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PlanResponse:
    row = await PreservacionRepository(conn, tenant_slug).insert_plan(
        payload.model_dump(), creado_por=_actor(request)
    )
    return PlanResponse(**dict(row))


@router.get(
    "/plan/versions",
    response_model=list[PlanResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def list_plan_versions(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PlanResponse]:
    rows = await PreservacionRepository(conn, tenant_slug).list_plan_versions()
    return [PlanResponse(**dict(r)) for r in rows]


def _map_empaquetar_aip_error(exc: Exception) -> HTTPException:
    """Traduce las excepciones de `AipService.empaquetar_aip` a HTTP —
    compartido por la ruta pública (RBAC) y la interna (D-02, E10 debt) para
    no duplicar el mapeo dos veces, mismo criterio que
    `_map_proteger_indice_error`."""
    if isinstance(exc, PreservationDisabledError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
        )
    if isinstance(exc, FileNotFoundForPreservationError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "file_not_found", "detail": str(exc), "status": 404},
        )
    if isinstance(exc, InvalidRetentionError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_retention", "detail": str(exc), "status": 400},
        )
    if isinstance(exc, FixityMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_mismatch", "detail": str(exc), "status": 409},
        )
    if isinstance(exc, ObjectLockNotEnabledError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "worm_bucket_misconfigured", "detail": str(exc), "status": 500},
        )
    if isinstance(exc, PremisValidationError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "premis_invalid", "detail": str(exc), "status": 500},
        )
    if isinstance(exc, AipYaExisteError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "aip_ya_existe",
                "detail": str(exc),
                "status": 409,
                # AIP vigente del expediente: permite a quien llamó reconciliar.
                "aip_id": str(exc.aip_id) if exc.aip_id is not None else None,
            },
        )
    if isinstance(exc, RetencionDiferidaSinHoldError):
        return _retencion_diferida_sin_hold(exc)
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


@router.post(
    "/aip",
    response_model=AipResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Empaqueta el AIP real (BagIt + PREMIS v3) de un expediente y lo sube "
        "al bucket WORM (E10 Increment B, RF-PRE-05/07)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def empaquetar_aip(
    payload: AipCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Descarga los bytes reales de cada documento, RECALCULA su fixity contra
    `files.sha256`, arma un Bag BagIt real + PREMIS v3 (conformidad plena:
    validado contra el XSD OFICIAL de PREMIS v3 de la LoC — ver
    `schemas/premis-v3-loc.xsd`) y lo sube — como ZIP — al bucket WORM del
    tenant con retención (`Retention`/`legal_hold`) derivada de la TRD del
    llamador. Operación de alto privilegio e irreversible: min_crud=3, igual
    que `/proteger-indice`."""
    service = AipService(conn, tenant_slug)
    try:
        return await service.empaquetar_aip(payload, actor=_actor(request))
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        ObjectLockNotEnabledError,
        PremisValidationError,
        AipYaExisteError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_empaquetar_aip_error(exc) from exc


@router.post(
    "/aip-internal",
    response_model=AipResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Gemelo INTERNO (D-02) de POST /aip -- disparo SISTEMA del "
        "empaquetado del AIP desde archive-service (E10 debt, cableado del "
        "AIP al cierre del expediente)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def empaquetar_aip_internal(
    payload: AipCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Mismo servicio que `POST /aip`, con OTRA puerta de autorización:
    `X-Internal-Token` (D-02) en vez de `require_permission(min_crud=3)` --
    mismo patrón que `/proteger-indice-internal`.

    Existe porque el cableado del AIP (E10 debt) es RECONCILIACIÓN-ONLY
    (descargar todos los bytes + ZIP + subir al bucket WORM es caro: NUNCA se
    intenta síncronamente en el path del cierre del expediente) — el único
    llamador es la 6ª fase de reconciliación de archive-service
    (`run_once_aip`), que actúa con `SYSTEM_RECONCILER_ID` (sin fila en
    `auth_users` de este tenant). `require_permission` la rechazaría SIEMPRE
    con 403, dejando el AIP sin empaquetar de forma permanente para
    CUALQUIER expediente -- mismo motivo exacto que
    `proteger_indice_internal`/`renovar_retencion_internal`. Actor fijo
    (`SYSTEM_RECONCILER_ID`), NUNCA derivado de headers del llamador."""
    service = AipService(conn, tenant_slug)
    try:
        return await service.empaquetar_aip(payload, actor=str(SYSTEM_RECONCILER_ID))
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        ObjectLockNotEnabledError,
        PremisValidationError,
        AipYaExisteError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_empaquetar_aip_error(exc) from exc


@router.get(
    "/aip/{expediente_id}",
    response_model=AipResponse,
    dependencies=[Depends(require_internal_token)],
)
async def get_aip(
    expediente_id: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Endpoint INTERNO (D-02, `X-Internal-Token`) — no enrutado desde el
    exterior por el gateway (sigue bajo el prefijo blanket
    `/api/v1/preservacion/`, pero cualquier llamada externa carece del
    token y recibe 401 automáticamente).

    El AIP embebe `premis_xml` con nombres originales, SHA-256 y tamaños de
    TODOS los documentos del expediente: antes estaba gateado solo por
    `require_permission("USUA_PERM_EXPEDIENTE", min_crud=1)`, que NO conoce
    el `nivel_seguridad` del expediente (storage-service no lo modela, ver
    `migrations/tenant/004_aip.sql`) — cualquier portador del permiso con
    clearance bajo podía cosechar el inventario de un expediente CLASIFICADO
    (hallazgo de auditoría Increment B/C, RF-SEG-08). El no-read-up real
    ahora se aplica AGUAS ARRIBA, en archive-service (dueño de la
    clasificación vigente del expediente):
    `GET /api/v1/expedientes/{expediente_id}/aip`, único punto de acceso
    EXTERNO al AIP — resuelve el clearance del llamante, confirma que el
    expediente es visible para él (404 neutro si no) y SOLO ENTONCES media
    esta llamada con el token interno."""
    # C-6: la versión VIGENTE (cabeza de la cadena `sustituye_a`), no «la más reciente por fecha».
    row = await PreservacionRepository(conn, tenant_slug).get_aip_cabeza(expediente_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "aip_not_found",
                "detail": "No existe ningún AIP para el expediente solicitado",
                "status": 404,
            },
        )
    return AipResponse(**dict(row))


@router.post(
    "/eventos",
    response_model=EventoResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def record_evento(
    payload: EventoCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> EventoResponse:
    row = await PreservacionRepository(conn, tenant_slug).insert_evento(
        payload.model_dump(), agente=_actor(request)
    )
    return _evento_response(row)


@router.post(
    "/eventos-internal",
    response_model=EventoResponse,
    summary="Registra un evento PREMIS desde otro servicio (D-02, AREA-07-05)",
    dependencies=[Depends(require_internal_token)],
)
async def record_evento_internal(
    payload: EventoInternalCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Gemelo INTERNO de `POST /eventos` para el evento que registra otro servicio sin una
    persona detrás de la petición (archive-service: el fixity-check de la recepción de una
    transferencia, que antes solo vivía en el payload de su asiento y no aparecía en
    `GET /expedientes/{id}/preservacion/eventos`).

    Sellado con `X-Internal-Token` y no enrutado por el gateway. Idempotente por
    `dedup_key`: si ya existe un evento del mismo `object_ref` y `tipo` con esa clave se
    devuelve ESE (200) en vez de duplicarlo (201 el primero): la reconciliación de archive
    reintenta y el registro es append-only. La clave se guarda en `detalle.dedup_key`."""
    repo = PreservacionRepository(conn, tenant_slug)
    datos = payload.model_dump(exclude={"agente", "dedup_key"})
    if payload.dedup_key is not None:
        existente = await repo.find_evento_por_dedup(
            object_ref=payload.object_ref, tipo=payload.tipo, dedup_key=payload.dedup_key
        )
        if existente is not None:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_evento_response(existente).model_dump(mode="json"),
            )
        datos["detalle"] = {**datos["detalle"], "dedup_key": payload.dedup_key}
    row = await repo.insert_evento(datos, agente=payload.agente)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_evento_response(row).model_dump(mode="json"),
    )


@router.get(
    "/eventos",
    response_model=list[EventoResponse],
    dependencies=[Depends(require_internal_token)],
)
async def list_eventos(
    # Repetible (`?object_ref=a&object_ref=b`): los eventos de un expediente cuelgan de
    # varios objetos (el expediente, su AIP, los `file_id` de sus anexos: AREA-07-15).
    # Tope por petición: el cliente (archive) pide en tandas de 100.
    object_ref: list[str] | None = Query(default=None, max_length=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[EventoResponse]:
    """Endpoint INTERNO (D-02, `X-Internal-Token`) — mismo patrón que
    `get_aip` (E10 Increment C). Con `object_ref=` devuelve los eventos PREMIS
    (fixity/WORM: tipos + timestamps) de UN objeto (expediente/file/AIP);
    ese inventario de actividad ya estaba gateado solo por
    `require_permission("USUA_PERM_EXPEDIENTE", min_crud=1)`, que NO conoce
    el `nivel_seguridad` del expediente (storage-service no lo modela) —
    cualquier portador del permiso con clearance bajo podía confirmar la
    existencia y ver la actividad de preservación de un expediente
    CLASIFICADO. El no-read-up real ahora se aplica AGUAS ARRIBA, en
    archive-service (dueño de la clasificación vigente):
    `GET /api/v1/expedientes/{expediente_id}/preservacion/eventos`, único
    punto de acceso EXTERNO a los eventos de UN expediente — resuelve el
    clearance del llamante, confirma que el expediente es visible (404
    neutro si no) y SOLO ENTONCES media esta llamada con el token interno.

    Sin `object_ref=` devuelve TODOS los eventos del tenant (enumeración) —
    esa variante tenant-wide queda puramente interna, sin mediador externo
    equivalente (no hay endpoint que agregue "todos los eventos" fuera de
    storage-service en este incremento)."""
    rows = await PreservacionRepository(conn, tenant_slug).list_eventos(object_ref or None)
    return [_evento_response(r) for r in rows]


def _map_proteger_indice_error(exc: Exception) -> HTTPException:
    """Traduce las excepciones de `PreservationWormService.proteger_indice` a
    HTTP — compartido por la ruta pública (RBAC) y la interna (D-02, Inc.1)
    para no duplicar el mapeo dos veces."""
    if isinstance(exc, PreservationDisabledError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
        )
    if isinstance(exc, FileNotFoundForPreservationError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "file_not_found", "detail": str(exc), "status": 404},
        )
    if isinstance(exc, InvalidRetentionError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_retention", "detail": str(exc), "status": 400},
        )
    if isinstance(exc, FixityMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_mismatch", "detail": str(exc), "status": 409},
        )
    if isinstance(exc, WormSingletonConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "worm_singleton_conflict", "detail": str(exc), "status": 409},
        )
    if isinstance(exc, ArtefactoNoEsDeFirmaError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "artefacto_no_es_de_firma", "detail": str(exc), "status": 422},
        )
    if isinstance(exc, ObjectLockNotEnabledError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "worm_bucket_misconfigured", "detail": str(exc), "status": 500},
        )
    if isinstance(exc, RetencionDiferidaSinHoldError):
        return _retencion_diferida_sin_hold(exc)
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


def _retencion_diferida_sin_hold(exc: Exception) -> HTTPException:
    """409: se pidió (o se hereda) una retención diferida y el expediente no tiene hold
    `valoracion_pendiente` activo (C-5/C7). Nada se escribió."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "retencion_diferida_sin_hold", "detail": str(exc), "status": 409},
    )


@router.post(
    "/proteger-indice",
    response_model=ProtegerIndiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Protege bajo WORM (MinIO Object Lock) el índice electrónico firmado "
        "de un expediente (E10 Increment A, RF-PRE-03)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def proteger_indice(
    payload: ProtegerIndiceRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Sube una copia inmutable del índice electrónico firmado ya presente en
    `files` (bucket -documents) al bucket WORM del tenant, con retención
    derivada de la TRD del llamador (archive-service). Operación de alto
    privilegio e irreversible: min_crud=3, igual que otras disposiciones.

    Idempotente (E10 Inc.1): una segunda llamada para el MISMO
    `expediente_id` devuelve 200 con la fila YA protegida (sin re-subir,
    ver `PreservationWormService.proteger_indice`) en vez de 201 — mismo
    patrón que la deduplicación por SHA-256 de `POST /storage/files`.

    Alias FIJO de `tipo='indice'` (E10 Inc.2 wiring: la ruta generalizada es
    `POST /proteger-artefacto`) — `tipo` se fuerza aquí SIEMPRE,
    independientemente de lo que el body declare, para que este endpoint
    conserve su contrato histórico intacto sin importar el llamador."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        response, created = await service.proteger_indice(
            payload.model_copy(update={"tipo": "indice"}), actor=_actor(request)
        )
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        WormSingletonConflictError,
        ObjectLockNotEnabledError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.post(
    "/proteger-indice-internal",
    response_model=ProtegerIndiceResponse,
    summary=(
        "Gemelo INTERNO (D-02) de POST /proteger-indice — disparo SISTEMA del "
        "cableado WORM del índice desde archive-service (E10 Inc.1)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def proteger_indice_internal(
    payload: ProtegerIndiceRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Mismo servicio/idempotencia que `POST /proteger-indice`, con OTRA
    puerta de autorización: `X-Internal-Token` (D-02) en vez de
    `require_permission(min_crud=3)` — mismo patrón que `/internal/sign-indice`
    de signature-service.

    Existe porque el disparo del cableado WORM (`IndexService.
    proteger_worm_indice`, archive-service) tiene DOS orígenes con identidad
    muy distinta: (a) el intento síncrono tras el sellado del índice al
    cierre (`origen="close"`), que SÍ tiene un `X-User-Id` real — el usuario
    que cerró el expediente, ya validado `min_crud=3` por `close_expediente`
    — y llama a la ruta pública de arriba; (b) la 3ra fase de reconciliación
    (`run_once_worm`), que actúa con `SYSTEM_RECONCILER_ID` (sin fila en
    `auth_users` de este tenant) — `require_permission` la rechazaría SIEMPRE
    con 403, dejando cualquier índice degradado a `pendiente` de forma
    permanente. Actor fijo (`SYSTEM_RECONCILER_ID`), NUNCA derivado de
    headers del llamador (`X-Username`/`X-User-Id` no viajan en esta ruta).

    Alias FIJO de `tipo='indice'` (E10 Inc.2 wiring, mismo criterio que
    `POST /proteger-indice` de arriba): `tipo` se fuerza aquí SIEMPRE."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        response, created = await service.proteger_indice(
            payload.model_copy(update={"tipo": "indice"}), actor=str(SYSTEM_RECONCILER_ID)
        )
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        WormSingletonConflictError,
        ObjectLockNotEnabledError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


@router.post(
    "/proteger-artefacto",
    response_model=ProtegerIndiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Protege bajo WORM (MinIO Object Lock) un artefacto firmado de un "
        "expediente -- índice o acta de transferencia (E10 Inc.2 wiring)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def proteger_artefacto(
    payload: ProtegerIndiceRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Generalización de `POST /proteger-indice` (E10 Inc.2 wiring, fast-follow
    declarado en el diseño de Inc.1): mismo servicio/idempotencia, pero
    `payload.tipo` decide el artefacto ('indice' o 'acta_transferencia') en
    vez de fijarlo el router. `/proteger-indice` se conserva INTACTO como
    alias fijo -- ningún llamador existente necesita migrar."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        response, created = await service.proteger_indice(payload, actor=_actor(request))
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        WormSingletonConflictError,
        ObjectLockNotEnabledError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


def _map_renovar_retencion_error(exc: Exception) -> HTTPException:
    """Traduce las excepciones de `PreservationWormService.renovar_retencion`
    a HTTP -- usado por la ruta interna (D-02), mismo patrón que
    `_map_proteger_indice_error`."""
    if isinstance(exc, PreservationDisabledError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
        )
    if isinstance(exc, WormObjectNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "worm_object_not_found", "detail": str(exc), "status": 404},
        )
    if isinstance(exc, InvalidRetentionError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_retention", "detail": str(exc), "status": 400},
        )
    if isinstance(exc, SinRetencionQueRenovarError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "sin_retencion_que_renovar", "detail": str(exc), "status": 409},
        )
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


@router.post(
    "/renovar-retencion-internal",
    response_model=RenovarRetencionResponse,
    summary=(
        "Extiende la retención WORM (MinIO Object Lock) de un artefacto ya "
        "protegido -- índice o acta de transferencia -- de una serie de "
        "disposición Conservación Total (E10 debt, AGN 001/2024 Art. 4.3.2.6). "
        "Disparo SISTEMA únicamente (D-02, X-Internal-Token)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def renovar_retencion_internal(
    payload: RenovarRetencionRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RenovarRetencionResponse:
    """Extiende (nunca acorta) `retain_until` de la fila WORM identificada
    por `(payload.expediente_id, payload.tipo)`. Idempotente: si la fecha
    resuelta (`retain_until` directo, o `retencion_anios` + `fecha_inicio`)
    NO supera estrictamente la ya persistida, responde 200 SIN tocar MinIO
    (`renovado=false` en el body).

    Deliberadamente SIN ruta pública equivalente (remediación de auditoría de
    seguridad, Media): storage-service no modela la disposición TRD, así que
    no puede validar que `payload.expediente_id`/`tipo` correspondan
    realmente a una serie CT -- un portador de `USUA_PERM_EXPEDIENTE
    min_crud=3` con un payload arbitrario podría fijar el lock COMPLIANCE de
    CUALQUIER artefacto ya protegido hasta 100 años (`preservation_max_
    retention_years`), incluido uno de disposición E/S/M, saboteando su
    disposición final (Ley 594/2000 arts. 24-26). Solo `X-Internal-Token`
    (D-02): únicamente archive-service, dueño de la TRD, decide QUÉ artefacto
    es CT y CUÁNDO renovar -- la 5ª fase de reconciliación
    (`run_once_worm_renovacion`), que actúa con `SYSTEM_RECONCILER_ID` (sin
    fila en `auth_users` de este tenant, `require_permission` la rechazaría
    SIEMPRE con 403). Mismo criterio que `/proteger-indice-internal`/
    `/proteger-artefacto-internal`, un paso más estricto (aquellas SÍ
    conservan su gemela pública porque `proteger_indice` recibe `file_id` +
    recalcula fixity -- no puede sobre-fijar un artefacto arbitrario del
    mismo modo)."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        return await service.renovar_retencion(payload, actor=str(SYSTEM_RECONCILER_ID))
    except (
        PreservationDisabledError,
        WormObjectNotFoundError,
        InvalidRetentionError,
        SinRetencionQueRenovarError,
    ) as exc:
        raise _map_renovar_retencion_error(exc) from exc


@router.post(
    "/renovar-artefacto-internal",
    response_model=RenovarArtefactoResponse,
    summary=(
        "Registra la renovación del ArchiveTimeStamp de una firma XAdES-LTA: el XML renovado "
        "(objeto nuevo) hereda la política WORM del anterior y deja el evento PREMIS "
        "RENOVACION (B10 §8). Disparo SISTEMA únicamente (D-02, X-Internal-Token)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def renovar_artefacto_internal(
    payload: RenovarArtefactoRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RenovarArtefactoResponse:
    """Solo signature-service (dueño de la firma y del job de renovación) la llama. Sin gemela
    pública: decide QUÉ objeto sustituye a cuál, y eso hereda una retención COMPLIANCE
    irreversible. Actor fijo ``SYSTEM_RECONCILER_ID``, nunca derivado de cabeceras."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        return await service.renovar_artefacto(payload, actor=str(SYSTEM_RECONCILER_ID))
    except (
        ArtefactoNoEsDeFirmaError,
        FileNotFoundForPreservationError,
        FixityMismatchError,
        ObjectLockNotEnabledError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc


@router.post(
    "/proteger-artefacto-internal",
    response_model=ProtegerIndiceResponse,
    summary=(
        "Gemelo INTERNO (D-02) de POST /proteger-artefacto -- disparo SISTEMA "
        "del cableado WORM de un artefacto (E10 Inc.2 wiring)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def proteger_artefacto_internal(
    payload: ProtegerIndiceRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Generalización de `POST /proteger-indice-internal` -- mismo criterio
    (actor fijo `SYSTEM_RECONCILER_ID`, NUNCA derivado de headers) que su
    gemelo público de arriba, para la 4ª fase de reconciliación
    (`run_once_actas_worm`, archive-service) del acta de transferencia."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        response, created = await service.proteger_indice(
            payload, actor=str(SYSTEM_RECONCILER_ID)
        )
    except (
        PreservationDisabledError,
        FileNotFoundForPreservationError,
        InvalidRetentionError,
        FixityMismatchError,
        WormSingletonConflictError,
        ObjectLockNotEnabledError,
        RetencionDiferidaSinHoldError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


def _map_legal_hold_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PreservationDisabledError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
        )
    if isinstance(exc, HoldNoEncontradoError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "hold_not_found", "detail": str(exc), "status": 404},
        )
    if isinstance(exc, HoldMotivoNoCoincideError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "motivo_no_coincide", "detail": str(exc), "status": 409},
        )
    if isinstance(exc, HoldYaLevantadoError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "hold_ya_levantado", "detail": str(exc), "status": 409},
        )
    if isinstance(exc, HoldActorRequeridoError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "actor_requerido", "detail": str(exc), "status": 422},
        )
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


@router.post(
    "/legal-hold-internal/fijar",
    response_model=HoldFijarResponse,
    summary=(
        "Fija una retención legal POR MOTIVO sobre un expediente (acto explícito, con "
        "fundamento, acta y asiento). Solo SISTEMA (D-02). No exige objetos WORM (C-5/C7)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def legal_hold_fijar(
    payload: HoldFijarRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> JSONResponse:
    """Único camino para declarar un legal hold (AREA-08-07, C-5/C7): no es un campo del body de
    ninguna ruta de protección. Lo orquesta archive-service (dueño del permiso y del acto
    administrativo). 201 si crea el hold; 200 si ya existía el hold de sistema `valoracion_pendiente`
    (idempotente). Actor: el de la identidad firmada si la trae, `SYSTEM_RECONCILER_ID` si no; la
    columna `fijado_por` solo se llena con una persona real (un hold que no es de sistema la exige)."""
    service = LegalHoldService(conn, tenant_slug)
    actor = _actor(request) or str(SYSTEM_RECONCILER_ID)
    try:
        res = await service.fijar(payload, actor=actor, actor_id=_actor_uuid(request))
    except (PreservationDisabledError, HoldActorRequeridoError) as exc:
        raise _map_legal_hold_error(exc) from exc
    http_status = status.HTTP_201_CREATED if res.creado else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=res.model_dump(mode="json"))


@router.post(
    "/legal-hold-internal/{hold_id}/levantar",
    response_model=HoldLevantarResponse,
    summary=(
        "Levanta UN hold por `hold_id`, confirmando su `motivo_tipo`. El almacén se libera solo "
        "si no queda ningún otro hold activo del expediente. Solo SISTEMA (D-02)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def legal_hold_levantar(
    hold_id: uuid.UUID,
    payload: HoldLevantarRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> HoldLevantarResponse:
    service = LegalHoldService(conn, tenant_slug)
    actor = _actor(request) or str(SYSTEM_RECONCILER_ID)
    try:
        return await service.levantar(
            hold_id, payload, actor=actor, actor_id=_actor_uuid(request)
        )
    except (
        PreservationDisabledError,
        HoldNoEncontradoError,
        HoldMotivoNoCoincideError,
        HoldYaLevantadoError,
        HoldActorRequeridoError,
    ) as exc:
        raise _map_legal_hold_error(exc) from exc


@router.get(
    "/legal-hold-internal",
    response_model=list[HoldItem],
    summary="Retenciones legales (activas y levantadas) de un expediente. Solo SISTEMA (D-02)",
    dependencies=[Depends(require_internal_token)],
)
async def legal_hold_listar(
    expediente_id: str = Query(..., min_length=1, max_length=255),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[HoldItem]:
    """Lectura INTERNA: el no-read-up lo aplica archive-service aguas arriba (devuelve el
    fundamento, que puede ser reservado)."""
    return await LegalHoldService(conn, tenant_slug).listar(
        canonicalize_expediente_id(expediente_id)
    )


@router.post(
    "/fijar-retencion-internal",
    response_model=FijarRetencionResponse,
    summary=(
        "Fija COMPLIANCE por PRIMERA vez en las filas `valoracion_pendiente` de un expediente "
        "(valoración CT del Comité, C-5/C7). Solo SISTEMA (D-02)"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def fijar_retencion_internal(
    payload: FijarRetencionRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FijarRetencionResponse:
    """Idempotente: sin filas pendientes responde 200 con `filas_fijadas=0`. Nunca toca una fila
    `calendario` (su retención solo se extiende, por `renovar-retencion-internal`). Actor fijo
    `SYSTEM_RECONCILER_ID`."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        return await service.fijar_retencion_diferida(payload, actor=str(SYSTEM_RECONCILER_ID))
    except (PreservationDisabledError, InvalidRetentionError) as exc:
        raise _map_renovar_retencion_error(exc) from exc


@router.post(
    "/export-zip-internal",
    summary=(
        "Ensambla un ZIP de exportación de expediente (D-02, acto de SISTEMA) — "
        "descarga los bytes de los anexos de MinIO y devuelve el ZIP para streaming"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def export_zip_internal(
    payload: ExportZipCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> StreamingResponse:
    """Gemelo interno (X-Internal-Token, D-02) que materializa el inventario que
    archive-service ya filtró por clearance (no-read-up a nivel de expediente).
    NO persiste nada: descarga bytes + arma el ZIP + lo devuelve. El único
    llamador legítimo es `ArchiveService.export_expediente_zip`; inalcanzable
    desde el gateway y el token interno es la segunda capa fail-closed."""
    from app.services.export_service import (
        ExportDemasiadoGrandeError,
        ExportEspacioInsuficienteError,
        ExportFixityError,
        ExportService,
        remove_quietly,
    )

    # Todo fallo ocurre AQUÍ, antes de servir el primer byte: el ZIP se cierra
    # en un temporal y solo entonces se responde (nunca un 200 truncado).
    try:
        artifact = await ExportService(conn, tenant_slug).build_export_zip(payload)
    except FileNotFoundForPreservationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "anexo_no_encontrado", "detail": str(exc), "status": 404},
        )
    except ExportDemasiadoGrandeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "export_demasiado_grande", "detail": str(exc), "status": 413},
        )
    except ExportFixityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "fixity_fallida",
                "detail": f"el anexo {exc.file_id} no coincide con su SHA-256 registrado",
                "status": 409,
            },
        )
    except ExportEspacioInsuficienteError as exc:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail={"error": "espacio_insuficiente", "detail": str(exc), "status": 507},
        )

    async def _stream():
        try:
            with open(artifact.path, "rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, 1024 * 1024):
                    yield chunk
        finally:
            # También si el cliente corta a mitad: el generador se cierra.
            remove_quietly(artifact.path)

    return StreamingResponse(
        _stream(),
        media_type="application/zip",
        headers={"Content-Length": str(artifact.size)},
        background=BackgroundTask(remove_quietly, artifact.path),
    )


@router.post(
    "/migrar-formato",
    response_model=MigracionResponse,
    summary="Migrar un archivo a PDF/A contra la obsolescencia (RF-PRE-04)",
    # Mismo gate que proteger un artefacto: es un acto de preservación sobre el
    # archivo de un expediente, y produce un artefacto nuevo.
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def migrar_formato(
    payload: MigrarFormatoRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_actor_uuid),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> MigracionResponse:
    """Produce una copia de preservación en PDF/A **sin tocar el original**
    (Decreto 2609/2012 art. 32).

    **`503` si este despliegue no tiene convertidor**, y esa es la respuesta
    correcta: no hay migrador «stub» porque un migrador falso tendría que
    inventarse el archivo. El `503` dice qué falta instalar; fabricar una copia
    de preservación que nadie generó sería mucho peor que no migrar.

    La integridad se verifica **antes y después**, y el derivado se valida
    contra el perfil PDF/A: si no resulta conforme, la migración es `fallido` y
    los bytes se descartan. Si no hay validador, queda `no_verificado` y
    **tampoco** se promueve a copia de preservación.
    """
    service = FormatMigrationService(conn, tenant_slug)
    storage = StorageService(conn, tenant_slug)

    async def _descargar(record) -> bytes:
        minio = get_minio()
        respuesta = await minio.get_object(record.bucket, record.object_key)
        try:
            return await respuesta.read()
        finally:
            close = getattr(respuesta, "close", None)
            if close:
                close()

    async def _subir(origen, contenido: bytes, formato: str) -> uuid.UUID:
        # El derivado entra como ARTEFACTO DE SISTEMA, no como anexo: nadie lo
        # aportó, lo produjo la preservación. Por eso usa la lista de formatos
        # del sistema y no la de anexos.
        nombre = f"{origen.filename.rsplit('.', 1)[0]}-{formato.replace('/', '')}.pdf"
        respuesta, _creado, _ = await storage.upload(
            filename=nombre,
            mime_type="application/pdf",
            content=contenido,
            uploaded_by=actor,
            formatos_admitidos=settings.allowed_system_mime_set,
        )
        return respuesta.id

    try:
        registro = await service.migrar_a_pdfa(
            payload.file_id,
            perfil=payload.perfil,
            actor=actor,
            descargar=_descargar,
            subir=_subir,
        )
    except MigradorNoDisponibleError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "migrador_no_disponible", "detail": str(exc), "status": 503},
        ) from exc
    except FileNotFoundForMigrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "file_not_found",
                "detail": f"No existe el archivo {exc}",
                "status": 404,
            },
        ) from exc
    except MigracionFixityMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_no_coincide", "detail": str(exc), "status": 409},
        ) from exc
    return MigracionResponse(**registro)


@router.get(
    "/migraciones/{file_id}",
    response_model=list[MigracionResponse],
    summary="Cadena de procedencia de las migraciones de un archivo (RF-PRE-04)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def listar_migraciones(
    file_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[MigracionResponse]:
    """Los intentos, no solo los que salieron bien: «se intentó y no se pudo»
    es lo que dice qué documentos siguen esperando una migración que no sale."""
    repo = PreservacionRepository(conn, tenant_slug)
    return [MigracionResponse(**dict(r)) for r in await repo.list_migraciones(file_id)]


@router.get(
    "/aip/{expediente_id}/versiones",
    response_model=list[AipVersionItem],
    dependencies=[Depends(require_internal_token)],
)
async def listar_versiones_aip(
    expediente_id: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[AipVersionItem]:
    """Endpoint INTERNO (D-02): la cadena de versiones del AIP de un expediente (C-6, ADR-042),
    de la primera a la vigente. Sin el PREMIS. El no-read-up lo aplica archive-service aguas
    arriba, igual que `GET /aip/{expediente_id}`."""
    rows = await PreservacionRepository(conn, tenant_slug).listar_versiones_aip(expediente_id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "aip_not_found",
                "detail": "No existe ningún AIP para el expediente solicitado",
                "status": 404,
            },
        )
    return [AipVersionItem(**{k: r[k] for k in AipVersionItem.model_fields}) for r in rows]


@router.post(
    "/aip-reempaquetar-internal",
    response_model=AipResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Reempaqueta el AIP con el índice de sello de archivo renovado (C-6, ADR-042): "
        "versión n+1 construida desde el bag anterior"
    ),
    dependencies=[Depends(require_internal_token)],
)
async def reempaquetar_aip_internal(
    payload: AipReempaquetarRequest,
    response: Response,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Endpoint INTERNO (D-02, `X-Internal-Token`, ruta de CONSERVACIÓN: se atiende también con la
    entidad suspendida, ADR-035 §8). Actor fijo `SYSTEM_RECONCILER_ID`. 201 si crea la versión,
    200 si el sucesor ya existía con el mismo índice (reintento idempotente)."""
    service = AipService(conn, tenant_slug)
    try:
        resultado, creado = await service.reempaquetar_aip(
            payload, actor=str(SYSTEM_RECONCILER_ID)
        )
    except PreservationDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "worm_disabled", "detail": str(exc), "status": 503},
        ) from exc
    except AipNoEncontradoError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "aip_not_found", "detail": str(exc), "status": 404},
        ) from exc
    except AipYaSustituidoError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "aip_ya_sustituido", "detail": str(exc), "status": 409},
        ) from exc
    except (AipAnteriorFixityMismatchError, FixityMismatchError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "aip_anterior_fixity_mismatch", "detail": str(exc), "status": 409},
        ) from exc
    except IndiceNoEsRenovacionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "indice_no_es_renovacion_del_embebido",
                "detail": str(exc),
                "status": 422,
            },
        ) from exc
    except FileNotFoundForPreservationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "file_not_found", "detail": str(exc), "status": 404},
        ) from exc
    except InvalidRetentionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_retention", "detail": str(exc), "status": 400},
        ) from exc
    except RetencionDiferidaSinHoldError as exc:
        raise _retencion_diferida_sin_hold(exc) from exc
    except ObjectLockNotEnabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "worm_bucket_misconfigured", "detail": str(exc), "status": 500},
        ) from exc
    except PremisValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "premis_invalid", "detail": str(exc), "status": 500},
        ) from exc
    except asyncpg.UniqueViolationError as exc:
        # Dos reempaquetados del mismo AIP corriendo a la vez: `ux_aip_sustituye_a` /
        # `ux_aip_expediente_version` deciden; el segundo reintenta y recibe el 200 idempotente.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "aip_ya_sustituido", "detail": str(exc), "status": 409},
        ) from exc
    if not creado:
        response.status_code = status.HTTP_200_OK
    return resultado

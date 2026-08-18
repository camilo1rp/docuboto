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

import json

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response

from app.core.clearance import require_permission
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import get_db_conn
from app.core.internal_auth import require_internal_token
from app.core.minio_client import ObjectLockNotEnabledError
from app.repositories.preservacion_repository import PreservacionRepository
from app.schemas.preservacion import (
    AipCreate,
    AipResponse,
    EventoCreate,
    EventoResponse,
    ExportZipCreate,
    PlanCreate,
    PlanResponse,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
    RenovarRetencionRequest,
    RenovarRetencionResponse,
)
from app.services.aip_builder import PremisValidationError
from app.services.aip_service import AipService
from app.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    InvalidRetentionError,
    PreservationDisabledError,
    PreservationWormService,
    WormObjectNotFoundError,
)

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
    return request.headers.get("X-Username") or request.headers.get("X-User-Id")


def _evento_response(row) -> EventoResponse:
    d = dict(row)
    raw = d.get("detalle")
    d["detalle"] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return EventoResponse(**d)


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
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


@router.post(
    "/aip",
    response_model=AipResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Empaqueta el AIP real (BagIt + PREMIS v3) de un expediente y lo sube "
        "al bucket WORM (E10 Increment B, RF-PRE-05/07)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
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
    row = await PreservacionRepository(conn, tenant_slug).latest_aip(expediente_id)
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


@router.get(
    "/eventos",
    response_model=list[EventoResponse],
    dependencies=[Depends(require_internal_token)],
)
async def list_eventos(
    object_ref: str | None = Query(default=None),
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
    rows = await PreservacionRepository(conn, tenant_slug).list_eventos(object_ref)
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
    if isinstance(exc, ObjectLockNotEnabledError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "worm_bucket_misconfigured", "detail": str(exc), "status": 500},
        )
    raise exc  # pragma: no cover — defensivo, no debería alcanzarse.


@router.post(
    "/proteger-indice",
    response_model=ProtegerIndiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Protege bajo WORM (MinIO Object Lock) el índice electrónico firmado "
        "de un expediente (E10 Increment A, RF-PRE-03)"
    ),
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
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
        ObjectLockNotEnabledError,
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
        ObjectLockNotEnabledError,
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
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
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
        ObjectLockNotEnabledError,
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
    ) as exc:
        raise _map_renovar_retencion_error(exc) from exc


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
        ObjectLockNotEnabledError,
    ) as exc:
        raise _map_proteger_indice_error(exc) from exc
    http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


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
) -> Response:
    """Gemelo interno (X-Internal-Token, D-02) que materializa el inventario que
    archive-service ya filtró por clearance (no-read-up a nivel de expediente).
    NO persiste nada: descarga bytes + arma el ZIP + lo devuelve. El único
    llamador legítimo es `ArchiveService.export_expediente_zip`; inalcanzable
    desde el gateway y el token interno es la segunda capa fail-closed."""
    from app.services.export_service import ExportService

    try:
        zip_bytes = await ExportService(conn, tenant_slug).build_export_zip(payload)
    except FileNotFoundForPreservationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "anexo_no_encontrado", "detail": str(exc), "status": 404},
        )
    return Response(content=zip_bytes, media_type="application/zip")

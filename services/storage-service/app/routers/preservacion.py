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

from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.core.minio_client import ObjectLockNotEnabledError
from app.repositories.preservacion_repository import PreservacionRepository
from app.schemas.preservacion import (
    AipCreate,
    AipResponse,
    EventoCreate,
    EventoResponse,
    PlanCreate,
    PlanResponse,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
)
from app.services.aip_builder import PremisValidationError
from app.services.aip_service import AipService
from app.services.preservation_worm_service import (
    FileNotFoundForPreservationError,
    FixityMismatchError,
    InvalidRetentionError,
    PreservationDisabledError,
    PreservationWormService,
)

router = APIRouter(prefix="/api/v1/preservacion", tags=["preservacion"])


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
    `files.sha256`, arma un Bag BagIt real + PREMIS v3 (validación estructural
    contra un perfil local, namespace oficial — ver `schemas/premis-v3.xsd`)
    y lo sube — como ZIP — al bucket WORM del tenant con retención
    (`Retention`/`legal_hold`) derivada de la TRD del llamador. Operación de
    alto privilegio e irreversible: min_crud=3, igual que `/proteger-indice`."""
    service = AipService(conn, tenant_slug)
    try:
        return await service.empaquetar_aip(payload, actor=_actor(request))
    except PreservationDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
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
    except FixityMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_mismatch", "detail": str(exc), "status": 409},
        ) from exc
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


@router.get(
    "/aip/{expediente_id}",
    response_model=AipResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def get_aip(
    expediente_id: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Requiere `USUA_PERM_EXPEDIENTE` (min_crud=1, lectura): el AIP embebe
    `premis_xml` con nombres originales, SHA-256 y tamaños de TODOS los
    documentos del expediente — sin este gate, cualquier llamador con un JWT
    válido del tenant (cualquier rol) podía cosechar ese inventario, incluido
    el de expedientes clasificados (hallazgo de auditoría Increment B,
    RF-SEG-08)."""
    row = await PreservacionRepository(conn, tenant_slug).latest_aip(expediente_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "aip_not_found", "detail": expediente_id, "status": 404},
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
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=1))],
)
async def list_eventos(
    object_ref: str | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[EventoResponse]:
    rows = await PreservacionRepository(conn, tenant_slug).list_eventos(object_ref)
    return [_evento_response(r) for r in rows]


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
) -> ProtegerIndiceResponse:
    """Sube una copia inmutable del índice electrónico firmado ya presente en
    `files` (bucket -documents) al bucket WORM del tenant, con retención
    derivada de la TRD del llamador (archive-service). Operación de alto
    privilegio e irreversible: min_crud=3, igual que otras disposiciones."""
    service = PreservationWormService(conn, tenant_slug)
    try:
        return await service.proteger_indice(payload, actor=_actor(request))
    except PreservationDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "preservation_disabled", "detail": str(exc), "status": 503},
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
    except FixityMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "fixity_mismatch", "detail": str(exc), "status": 409},
        ) from exc
    except ObjectLockNotEnabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "worm_bucket_misconfigured", "detail": str(exc), "status": 500},
        ) from exc

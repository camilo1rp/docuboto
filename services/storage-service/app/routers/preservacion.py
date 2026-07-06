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
from datetime import UTC, datetime

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
from app.services.aip_builder import AipDoc, aip_sha256, build_bagit, build_premis
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


@router.get("/plan", response_model=PlanResponse)
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


@router.put("/plan", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/plan/versions", response_model=list[PlanResponse])
async def list_plan_versions(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[PlanResponse]:
    rows = await PreservacionRepository(conn, tenant_slug).list_plan_versions()
    return [PlanResponse(**dict(r)) for r in rows]


@router.post("/aip", response_model=AipResponse, status_code=status.HTTP_201_CREATED)
async def empaquetar_aip(
    payload: AipCreate,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    """Empaqueta el AIP (BagIt + PREMIS) del expediente y registra la ingesta."""
    repo = PreservacionRepository(conn, tenant_slug)
    docs = [
        AipDoc(document_id=d.document_id, nombre=d.nombre, valor_huella=d.valor_huella)
        for d in payload.documentos
    ]
    now = datetime.now(tz=UTC)
    bagit = build_bagit(payload.expediente_id, docs, now)
    premis = build_premis(payload.expediente_id, docs, payload.indice_sha256, now)
    sha = aip_sha256(bagit, premis)
    row = await repo.insert_aip(
        {
            "expediente_id": payload.expediente_id,
            "bagit": bagit,
            "premis_xml": premis,
            "aip_sha256": sha,
            "indice_sha256": payload.indice_sha256,
            "num_documentos": len(docs),
        }
    )
    await repo.insert_evento(
        {
            "object_ref": payload.expediente_id,
            "tipo": "INGESTA",
            "resultado": "ok",
            "hash_after": sha,
            "detalle": {"num_documentos": len(docs)},
        },
        agente=_actor(request),
    )
    return AipResponse(**dict(row))


@router.get("/aip/{expediente_id}", response_model=AipResponse)
async def get_aip(
    expediente_id: str,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> AipResponse:
    row = await PreservacionRepository(conn, tenant_slug).latest_aip(expediente_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "aip_not_found", "detail": expediente_id, "status": 404},
        )
    return AipResponse(**dict(row))


@router.post("/eventos", response_model=EventoResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/eventos", response_model=list[EventoResponse])
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

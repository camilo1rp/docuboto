# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Vistos buenos secuenciales sobre un radicado (paridad legado)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.database import get_db_conn
from app.schemas.visto_bueno import CadenaCreate, CadenaResponse, VoBoDecision
from app.services.visto_bueno_service import VistoBuenoService

router = APIRouter(prefix="/api/v1/workflows", tags=["vistos-buenos"])


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


def _actor(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    try:
        return uuid.UUID(x_user_id) if x_user_id else None
    except ValueError:
        return None


@router.post(
    "/{radicado_id}/vistos-buenos",
    response_model=CadenaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear cadena de vistos buenos (orden de revisión)",
)
async def crear_cadena(
    radicado_id: uuid.UUID,
    payload: CadenaCreate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> CadenaResponse:
    return await VistoBuenoService(conn, tenant_slug).crear(radicado_id, payload)


@router.get("/{radicado_id}/vistos-buenos", response_model=CadenaResponse)
async def get_cadena(
    radicado_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> CadenaResponse:
    return await VistoBuenoService(conn, tenant_slug).get(radicado_id)


@router.post(
    "/{radicado_id}/vistos-buenos/decidir",
    response_model=CadenaResponse,
    summary="El revisor de turno da/niega su visto bueno",
)
async def decidir(
    radicado_id: uuid.UUID,
    payload: VoBoDecision,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> CadenaResponse:
    return await VistoBuenoService(conn, tenant_slug).decidir(radicado_id, payload, actor)

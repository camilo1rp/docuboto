# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Transferencias documentales primarias/secundarias (E12)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.actor import Actor, optional_actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    TransferenciaActaResponse,
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
)
from app.services.transferencia_service import TransferenciaService

router = APIRouter(prefix="/api/v1/transferencias", tags=["transferencias"])


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


@router.post(
    "",
    response_model=TransferenciaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def create_transferencia(
    payload: TransferenciaCreate,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).create(payload, actor)


@router.get(
    "",
    response_model=list[TransferenciaResponse],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_transferencias(
    estado: str | None = Query(default=None),
    tipo: str | None = Query(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TransferenciaResponse]:
    return await TransferenciaService(conn, tenant_slug).list(estado=estado, tipo=tipo)


@router.get(
    "/{transferencia_id}",
    response_model=TransferenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_transferencia(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).get(transferencia_id)


@router.post(
    "/{transferencia_id}/enviar",
    response_model=TransferenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def enviar(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # H-C: antes este handler ni siquiera declaraba la identidad del llamante
    # -- un acto de custodia (remisión) quedaba sin responsable posible en la
    # hoja de ruta/audit_log pese a que `require_permission(min_crud=3)` YA
    # exige `X-User-Id` para autorizar la operación.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).enviar(transferencia_id, actor)


@router.post(
    "/{transferencia_id}/recibir",
    response_model=TransferenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def recibir(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).recibir(transferencia_id, actor)


@router.post(
    "/{transferencia_id}/rechazar",
    response_model=TransferenciaResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def rechazar(
    transferencia_id: uuid.UUID,
    payload: TransferenciaRechazo,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaResponse:
    return await TransferenciaService(conn, tenant_slug).rechazar(transferencia_id, payload, actor)


@router.get(
    "/{transferencia_id}/fuid",
    response_model=FuidResponse,
    summary="FUID entregable of the transfer (consumes E17, RF-TRA-02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def transferencia_fuid(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # Lectura, no acto de custodia (RF-SEG-08 bloque (b) de `core/clearance.py`):
    # `optional_actor` no exige `X-User-Id` (anónimo -> clearance PUBLICA).
    actor: Actor | None = Depends(optional_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FuidResponse:
    return await TransferenciaService(conn, tenant_slug).fuid(
        transferencia_id, actor.id if actor else None
    )


@router.get(
    "/{transferencia_id}/acta",
    response_model=TransferenciaActaResponse,
    summary="Acta de entrega CONGELADA al recibir (E12 H-I, Ac. AGN 001/2024 Anexo FUID)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def transferencia_acta(
    transferencia_id: uuid.UUID,
    # H-H (E06/F4): cómputo on-demand del bloque `firma.valida` (descarga del
    # XML firmado + signature-service /verify) -- default False, esta lectura
    # NO re-verifica salvo que se pida explícitamente (los campos baratos de
    # `firma` sí viajan siempre).
    verify: bool = Query(default=False),
    tenant_slug: str = Depends(_require_tenant),
    # Lectura, no acto de custodia (RF-SEG-08, mismo gating que `/fuid`):
    # `optional_actor` no exige `X-User-Id` (anónimo -> clearance PUBLICA).
    actor: Actor | None = Depends(optional_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaResponse:
    return await TransferenciaService(conn, tenant_slug).acta(
        transferencia_id, actor.id if actor else None, verify=verify
    )


@router.post(
    "/{transferencia_id}/acta/firmar",
    response_model=TransferenciaActaResponse,
    summary="Reintento manual del sellado XAdES del acta de entrega (H-H, E06/F4)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def transferencia_acta_firmar(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaResponse:
    return await TransferenciaService(conn, tenant_slug).firmar_acta_retry(
        transferencia_id, actor
    )

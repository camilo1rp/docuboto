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
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.core.actor import Actor, optional_actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    FirmaInobtenibleRequest,
    FirmarPersonalRequest,
    FundamentoLectura,
    FundamentoVerificacion,
    RevocarFirmaInobtenibleRequest,
    SobreFundamentoResponse,
    TransferenciaActaFirmaExcepcion,
    TransferenciaActaFirmaPersonalResponse,
    TransferenciaActaResponse,
    TransferenciaActaVerificacionResponse,
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
    WormReintentoResponse,
)
from app.services.transferencia_service import TransferenciaService

router = APIRouter(prefix="/api/v1/transferencias", tags=["transferencias"])


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


@router.post(
    "/{transferencia_id}/acta/firmar-personal",
    response_model=TransferenciaActaFirmaPersonalResponse,
    summary="Firma PERSONAL PKI de una parte (remitente/receptor) sobre el acta de entrega (E17/F4)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def transferencia_acta_firmar_personal(
    transferencia_id: uuid.UUID,
    payload: FirmarPersonalRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaFirmaPersonalResponse:
    authorization = request.headers.get("Authorization")
    return await TransferenciaService(conn, tenant_slug).firmar_personal(
        transferencia_id, actor, payload.totp_code, authorization, payload.rol
    )


@router.post(
    "/{transferencia_id}/acta/firma-inobtenible",
    response_model=TransferenciaActaFirmaExcepcion,
    status_code=status.HTTP_201_CREATED,
    summary="Declara que una parte del acta no puede firmar: acto administrativo (AREA-07-10)",
    # Acto ADMINISTRATIVO, no de custodia: exige administración a nivel 3, no el
    # permiso de transferencias con el que las partes firman.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible(
    transferencia_id: uuid.UUID,
    payload: FirmaInobtenibleRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaFirmaExcepcion:
    # El bearer se reenvía TAL CUAL a signature-service: verifica y consume el TOTP del
    # declarante contra auth-service con la identidad del bearer (firma personal de la constancia).
    return await TransferenciaService(conn, tenant_slug).declarar_firma_inobtenible(
        transferencia_id, actor, payload, request.headers.get("Authorization")
    )


@router.post(
    "/{transferencia_id}/acta/firma-inobtenible/revocar",
    response_model=TransferenciaActaFirmaExcepcion,
    status_code=status.HTTP_201_CREATED,
    summary="Revoca la excepción de firma de un rol (fila nueva append-only, AREA-07-10)",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_revocar(
    transferencia_id: uuid.UUID,
    payload: RevocarFirmaInobtenibleRequest,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaFirmaExcepcion:
    # Igual que al declarar: el bearer se reenvía TAL CUAL para la firma personal de quien revoca.
    return await TransferenciaService(conn, tenant_slug).revocar_firma_inobtenible(
        transferencia_id, actor, payload, request.headers.get("Authorization")
    )


@router.get(
    "/{transferencia_id}/acta/firma-inobtenible/{excepcion_id}/fundamento/verificacion",
    response_model=FundamentoVerificacion,
    summary="Verifica el compromiso salado del fundamento contra la columna, el XML y el asiento",
    # El fundamento puede referir salud o fallecimiento: solo administración nivel 3.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_fundamento(
    transferencia_id: uuid.UUID,
    excepcion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FundamentoVerificacion:
    return await TransferenciaService(conn, tenant_slug).verificar_fundamento(
        transferencia_id, excepcion_id, actor
    )


@router.get(
    "/{transferencia_id}/acta/firma-inobtenible/{excepcion_id}/fundamento",
    response_model=FundamentoLectura,
    summary="Lee el texto del fundamento de la constancia o de su revocación (con asiento)",
    # Dato de salud/fallecimiento: administración nivel 3 y asiento de lectura en el servicio.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_fundamento_lectura(
    transferencia_id: uuid.UUID,
    excepcion_id: uuid.UUID,
    acto: Literal["constancia", "revocacion"] = Query("constancia"),
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FundamentoLectura:
    return await TransferenciaService(conn, tenant_slug).leer_fundamento(
        transferencia_id, excepcion_id, actor, acto=acto
    )


@router.get(
    "/{transferencia_id}/acta/firma-inobtenible/{excepcion_id}/sobre",
    response_model=SobreFundamentoResponse,
    summary="Sirve el sobre de revelación del fundamento (texto + sal, sellado y bajo Object-Lock)",
    # Único camino de lectura del sobre: texto en claro (puede referir salud o fallecimiento) +
    # sal. Administración nivel 3 y asiento de lectura en el servicio; jamás OAI/EAD/CMIS.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_sobre(
    transferencia_id: uuid.UUID,
    excepcion_id: uuid.UUID,
    acto: Literal["constancia", "revocacion"] = Query("constancia"),
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> SobreFundamentoResponse:
    return await TransferenciaService(conn, tenant_slug).leer_sobre_fundamento(
        transferencia_id, excepcion_id, actor, acto=acto
    )


@router.post(
    "/{transferencia_id}/acta/firma-inobtenible/{excepcion_id}/worm/reintentar",
    response_model=WormReintentoResponse,
    summary="Sale de `agotado` el Object-Lock de la constancia y lo reintenta (A4)",
    # Acto ADMINISTRATIVO sobre la prueba: administración nivel 3, como declarar y revocar.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_worm_reintentar(
    transferencia_id: uuid.UUID,
    excepcion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> WormReintentoResponse:
    return await TransferenciaService(conn, tenant_slug).reintentar_worm_firma_excepcion(
        transferencia_id, excepcion_id, actor, acto="excepcion"
    )


@router.post(
    "/{transferencia_id}/acta/firma-inobtenible/{excepcion_id}/revocacion/worm/reintentar",
    response_model=WormReintentoResponse,
    summary="Sale de `agotado` el Object-Lock de la revocación y lo reintenta (A4)",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def transferencia_acta_firma_inobtenible_revocacion_worm_reintentar(
    transferencia_id: uuid.UUID,
    excepcion_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> WormReintentoResponse:
    return await TransferenciaService(conn, tenant_slug).reintentar_worm_firma_excepcion(
        transferencia_id, excepcion_id, actor, acto="revocacion"
    )


@router.get(
    "/{transferencia_id}/acta/verificacion",
    response_model=TransferenciaActaVerificacionResponse,
    summary="Verificación agregada (sello + firmas personales) del acta de entrega (E17/F4)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def transferencia_acta_verificacion(
    transferencia_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # Lectura, no acto de custodia (RF-SEG-08, mismo gating que `/acta`):
    # `optional_actor` no exige `X-User-Id` (anónimo -> clearance PUBLICA).
    actor: Actor | None = Depends(optional_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TransferenciaActaVerificacionResponse:
    return await TransferenciaService(conn, tenant_slug).acta_verificacion(
        transferencia_id, actor.id if actor else None
    )

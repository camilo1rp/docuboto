# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Índice electrónico del expediente (E15, Acuerdo AGN 001/2024).

Lectura del índice vigente / por versión / XML / historial, `rebuild` (admin) y
el reintento del sellado XAdES-B (`POST /indice/firmar`, E06 Inc.1, RT-16). La
verificación de fixity contra storage (T-14) ya contrasta la firma real.

Todas las rutas de LECTURA exigen `USUA_PERM_EXPEDIENTE` (`require_permission`,
que además fija el search_path del tenant) y aplican no-read-up (RF-SEG-08):
`IndexService` resuelve el clearance real del llamante y devuelve 404 si el
expediente supera su nivel de seguridad, ANTES de servir el índice — cierre de
un gap donde `/indice`, `/indice.xml` y `/indice/versions` no tenían gate
alguno, y `/indice/verify` sí exigía el permiso pero no contrastaba clearance.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import (
    IndiceFirmarResult,
    IndiceResponse,
    IndiceVerifyResponse,
    IndiceVersionSummary,
    RebuildResult,
)
from app.services.index_service import IndexService

router = APIRouter(prefix="/api/v1/expedientes", tags=["indice"])


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
    """X-User-Id como UUID, para resolver el clearance del llamante (RF-SEG-08).

    Gemelo de `expedientes.py::_optional_user`: usado en las lecturas del
    índice (indice, indice.xml, versions, verify) — a diferencia de
    `_actor_id` (str crudo, reenviado a signature-service), aquí se necesita
    el UUID para `ArchiveRepository.caller_clearance`.
    """
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
    """Canal de origen de la petición (X-Canal, inyectado server-side por el
    gateway — D-03, nunca confiado de un cliente directo) — decide si esta
    lectura del índice deja pista de acceso (E15 perfil v4, R3-a). Ausente
    (llamada sin pasar por el gateway, o tests que no lo declaran) => 'system':
    no se audita como consulta de un usuario real."""
    return x_canal or "system"


def _actor_id(x_user_id: str | None = Header(default=None)) -> str | None:
    """X-User-Id crudo (str) para reenviar tal cual a signature-service.

    Se valida el formato aquí (fail-fast, 400) pero se conserva como str: el
    cliente HTTP interno lo envía directamente como cabecera, sin re-serializar.
    """
    if not x_user_id:
        return None
    try:
        uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )
    return x_user_id


@router.get(
    "/{expediente_id}/indice",
    response_model=IndiceResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceResponse:
    return await IndexService(conn, tenant_slug).get_indice(
        expediente_id, version, user_id=user_id, canal=canal
    )


@router.get(
    "/{expediente_id}/indice.xml",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_indice_xml(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    xml = await IndexService(conn, tenant_slug).get_xml(
        expediente_id, version, user_id=user_id, canal=canal
    )
    return Response(content=xml, media_type="application/xml")


@router.get(
    "/{expediente_id}/indice/versions",
    response_model=list[IndiceVersionSummary],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_indice_versions(
    expediente_id: uuid.UUID,
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[IndiceVersionSummary]:
    items, total = await IndexService(conn, tenant_slug).list_versions(
        expediente_id, page=page, size=size, user_id=user_id
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{expediente_id}/indice/verify",
    response_model=IndiceVerifyResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def verify_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceVerifyResponse:
    return await IndexService(conn, tenant_slug).verify(expediente_id, version, user_id=user_id)


@router.post(
    "/{expediente_id}/indice/firmar",
    response_model=IndiceFirmarResult,
    dependencies=[
        Depends(require_permission("USUA_PERM_EXPEDIENTE")),
        Depends(require_permission("PERM_FIRMA")),
    ],
)
async def firmar_indice(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor_id: str | None = Depends(_actor_id),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceFirmarResult:
    """Reintento del sellado XAdES-B del índice (E06 Inc.1, RT-16).

    Requiere USUA_PERM_EXPEDIENTE + PERM_FIRMA (``require_permission``,
    fail-closed): sin identidad resoluble (sin X-User-Id, o malformado) o sin
    ambos permisos, la petición se rechaza con 403 ANTES de tocar el índice —
    un llamador sin autorización nunca puede mutar el estado del índice
    (defensa en profundidad; antes solo el signature-service, aguas abajo,
    aplicaba PERM_FIRMA y un 403 de allí volteaba el índice a
    'pendiente_firma' igualmente).

    Idempotente: 409 si el índice vigente ya está firmado (inmutable). Sin
    X-User-Id (atribución del acto de firma, RF-FIR-11/12) la firma no se
    intenta y responde 503 (el índice queda/permanece 'pendiente_firma').
    """
    signed = await IndexService(conn, tenant_slug).firmar_indice_retry(
        expediente_id, tenant_slug=tenant_slug, actor_id=actor_id
    )
    return IndiceFirmarResult(
        firma_id=signed.firma_id,
        minio_key_firmado=signed.minio_key_firmado,
        nivel_conformidad=signed.nivel_conformidad,
        xades_level=signed.xades_level,
        acreditado=signed.acreditado,
    )


@router.post(
    "/{expediente_id}/indice/rebuild",
    response_model=RebuildResult,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def rebuild_indice(
    expediente_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RebuildResult:
    generado_por = request.headers.get("X-Username") or "system"
    return await IndexService(conn, tenant_slug).regenerate(
        expediente_id, generado_por=generado_por
    )

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lotes de eliminación documental — RF-TRD-07, primera fase (E04).

**Recurso propio y no un apéndice de `/trd`**, por el mismo criterio que la cola
de anulaciones: un lote de eliminación no es una lectura del instrumento, es un
procedimiento con su propio ciclo de vida —`borrador → publicado → avalado →
ejecutado`— y con actos que se auditan uno a uno.

**Nada de lo que hay aquí elimina nada.** Esta fase propone y congela el
inventario; publicar, avalar, firmar el acta y ejecutar el borrado seguro son
las cuatro fases siguientes, y la última todavía no está construida. El gate es
`USUA_PERM_TRD` con `crud>=3` —el mismo que decidir— porque proponer un lote es
el primer acto de un procedimiento que termina en destrucción.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.actor import Actor, require_actor
from app.core.clearance import require_permission
from app.core.database import get_db_conn
from app.schemas.archive import LoteEliminacionCreate, LoteEliminacionResponse
from app.services.archive_service import ArchiveService

router = APIRouter(prefix="/api/v1/eliminacion", tags=["eliminacion"])


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
    "/lotes",
    response_model=LoteEliminacionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Proponer un lote de eliminación (RF-TRD-07, fase 1 de 5)",
    dependencies=[Depends(require_permission("USUA_PERM_TRD", min_crud=3))],
)
async def proponer_lote(
    payload: LoteEliminacionCreate,
    tenant_slug: str = Depends(_require_tenant),
    # Actor fail-closed: proponer un lote es el primer acto imputable de un
    # procedimiento de destrucción.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> LoteEliminacionResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.proponer_lote_eliminacion(
        codigo=payload.codigo,
        expediente_ids=payload.expediente_ids,
        observaciones=payload.observaciones,
        actor=actor.id,
    )


@router.get(
    "/lotes",
    response_model=list[LoteEliminacionResponse],
    summary="Listar lotes de eliminación",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def listar_lotes(
    response: Response,
    estado: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[LoteEliminacionResponse]:
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_lotes_eliminacion(estado=estado, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/lotes/{lote_id}",
    response_model=LoteEliminacionResponse,
    summary="Ver un lote con su inventario congelado",
    dependencies=[Depends(require_permission("USUA_PERM_TRD"))],
)
async def ver_lote(
    lote_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    # Lectura, pero con identidad: el inventario se recorta por el clearance de
    # quien mira, así que sin saber quién es no se puede recortar. Sin
    # `X-User-Id` el clearance cae a PÚBLICA y el inventario sale vacío --
    # fail-closed, no un error.
    actor: Actor = Depends(require_actor),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> LoteEliminacionResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_lote_eliminacion(lote_id, actor=actor.id)

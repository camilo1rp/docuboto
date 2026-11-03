# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Admin view of the outside-work outbox: list failed rows and retry one."""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from orpycamcp_common.trabajo import SqlRepo

from document.core.clearance import require_permission
from document.core.database import get_tenant_conn

router = APIRouter(prefix="/api/v1/trabajo", tags=["trabajo"])


@router.get(
    "",
    summary="Filas de trabajo fuera que fallaron y esperan reintento",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def listar_fallidos(conn: asyncpg.Connection = Depends(get_tenant_conn)) -> list[dict[str, Any]]:
    filas = await SqlRepo(conn).listar_fallidos()
    return [
        {
            "event_id": f.event_id,
            "tipo": f.tipo,
            "cola": f.cola,
            "intentos": f.intentos,
            "ultimo_error": f.ultimo_error,
        }
        for f in filas
    ]


@router.post(
    "/{event_id}/reintentar",
    summary="Suelta una fila fallida para que el bucle la reclame otra vez",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def reintentar(
    event_id: str, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> dict[str, str]:
    ok = await SqlRepo(conn).reintentar(event_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "trabajo_no_encontrado",
                "detail": "No hay una fila pendiente con ese event_id",
                "status": 404,
            },
        )
    return {"event_id": event_id, "estado": "pendiente"}

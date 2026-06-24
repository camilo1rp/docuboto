# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Contexto de dependencia activa del propio usuario (E08, ADR-013).

No es admin: el usuario cambia SU contexto. `get_caller` resuelve la identidad
desde la BD del tenant. El cambio NO reemite token (ADR-013): persiste la
dependencia activa y la audita.
"""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.schemas.urd import ContextResponse, ContextSwitchRequest
from app.services.urd_service import URDService

router = APIRouter(prefix="/api/v1/auth/context", tags=["context"])


@router.get("", response_model=ContextResponse)
async def get_context(
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ContextResponse:
    """Contexto actual del llamante: dependencia activa + dependencias disponibles."""
    return await URDService(conn).context(caller.user_id)


@router.post("/switch", response_model=ContextResponse)
async def switch_context(
    payload: ContextSwitchRequest,
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ContextResponse:
    """Cambia la dependencia activa del llamante (sin reemitir token, ADR-013)."""
    result = await URDService(conn).switch(
        caller.user_id, payload.depe_id, executor_id=caller.user_id
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "no_urd_for_dependency",
                "detail": (
                    f"El usuario no tiene una URD activa en la dependencia {payload.depe_id}"
                ),
                "status": 400,
            },
        )
    return result

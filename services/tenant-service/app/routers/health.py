# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import asyncpg
from fastapi import APIRouter, Depends

from app.core.database import get_db_conn
from app.schemas.tenant import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
)
async def health(
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> HealthResponse:
    db_status = "ok"
    try:
        await conn.fetchval("SELECT 1")
    except Exception:
        db_status = "error"

    overall = "ok" if db_status == "ok" else "degraded"
    return HealthResponse(status=overall, database=db_status)

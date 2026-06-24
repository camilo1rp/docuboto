import asyncpg
from fastapi import APIRouter, Depends

from app.core.database import get_db_conn
from app.schemas.common import HealthResponse

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

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import aiosmtplib
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> JSONResponse:
    checks: dict[str, str] = {}

    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    try:
        smtp = aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port)
        await smtp.connect()
        await smtp.quit()
        checks["smtp"] = "ok"
    except Exception as exc:
        checks["smtp"] = f"error: {exc}"

    # Solo Redis es crítico; SMTP puede estar offline en entornos sin mailserver
    critical_ok = checks.get("redis") == "ok"
    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )

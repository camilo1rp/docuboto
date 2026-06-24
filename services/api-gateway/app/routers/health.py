# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])

_DOWNSTREAM = [
    ("auth-service", "auth_service_url"),
    ("tenant-service", "tenant_service_url"),
    ("document-service", "document_service_url"),
    ("archive-service", "archive_service_url"),
    ("storage-service", "storage_service_url"),
    ("workflow-service", "workflow_service_url"),
    ("notification-service", "notification_service_url"),
]


async def _check_service(
    client: httpx.AsyncClient,
    name: str,
    base_url: str,
) -> tuple[str, str]:
    try:
        r = await client.get(f"{base_url}/health", timeout=5.0)
        if r.status_code < 500:
            return name, "ok"
        return name, f"error: HTTP {r.status_code}"
    except Exception as exc:
        return name, f"unreachable: {exc}"


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}

    # Redis local (el gateway lo gestiona directamente)
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
    else:
        checks["redis"] = "unavailable"

    # Servicios downstream — llamadas en paralelo
    client: httpx.AsyncClient = request.app.state.http_client
    tasks = [
        _check_service(client, name, getattr(request.app.state, attr)) for name, attr in _DOWNSTREAM
    ]
    results = await asyncio.gather(*tasks)
    for name, status in results:
        checks[name] = status

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ok" if all_ok else "degraded",
            "checks": checks,
        },
    )

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Importación interoperable de radicados (E11, RF-INT-05).

`POST /api/v1/import` — AUTENTICADO (no público), gate `USUA_PERM_EXPEDIENTE` +
**no-write-up por clearance** (no se ingiere un radicado por encima del clearance del
importador). Recibe el paquete ZIP de INT-01 como **cuerpo crudo** (`Content-Type:
application/zip`, sin multipart → sin dependencia extra), **verifica la fixity SHA-256
de cada binario y valida el esquema ANTES de ingerir** (todo-o-nada, sin estado
parcial), reingiere preservando el número de radicado, y audita el acto.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.services.import_service import ImportService, PackageValidationError

router = APIRouter(prefix="/api/v1/import", tags=["import"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"
_MAX_UPLOAD = 512 * 1024 * 1024  # 512 MiB de ZIP comprimido (guarda de subida)


def _get_registered_by(request: Request) -> uuid.UUID | None:
    raw = request.headers.get(_USER_HEADER)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


@router.post(
    "",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
    summary="Importar un paquete interoperable de radicados (E11, RF-INT-05)",
)
async def import_paquete(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict:
    tenant_slug = request.headers.get(_TENANT_HEADER, "")
    actor = _get_registered_by(request)
    clearance = await resolve_caller_clearance(conn, actor)

    zip_bytes = await request.body()
    if len(zip_bytes) > _MAX_UPLOAD:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "paquete_demasiado_grande", "detail": "ZIP excede el máximo", "status": 413},
        )

    svc = ImportService(conn, tenant_slug)
    try:
        return await svc.import_package(
            zip_bytes=zip_bytes, max_clearance=clearance, actor=actor
        )
    except PackageValidationError as exc:
        # Fallo de validación (esquema o fixity) → 422; NO se ingirió nada.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "paquete_invalido", "detail": exc.detail, "status": 422},
        )

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Exportación interoperable de radicados (E11, RF-INT-01).

`POST /api/v1/export` — endpoint AUTENTICADO (no público) enrutado por el gateway
(inyecta `X-User-Id`/`X-Tenant-Slug` del JWT). Gate `USUA_PERM_EXPEDIENTE` +
**no-read-up por clearance**: el paquete solo incluye radicados que el llamante
puede leer; los sobre-clearance se omiten y se reportan content-free en el
manifiesto (RF-SEG-08, Ley 1712/2014 arts. 18-19).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.services.export_service import ExportService, ExportTooLargeError

router = APIRouter(prefix="/api/v1/export", tags=["export"])

_TENANT_HEADER = "X-Tenant-Slug"
_USER_HEADER = "X-User-Id"


class ExportRequest(BaseModel):
    comentario: str | None = Field(None, max_length=500)
    year: int | None = Field(None, ge=2000, le=2100)
    doc_type: str | None = Field(None, pattern="^[ESI]$")


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
    summary="Exportar radicados a un paquete interoperable (E11, RF-INT-01)",
)
async def export_radicados(
    payload: ExportRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> Response:
    tenant_slug = request.headers.get(_TENANT_HEADER, "")
    actor = _get_registered_by(request)
    clearance = await resolve_caller_clearance(conn, actor)
    svc = ExportService(conn, tenant_slug)
    try:
        zip_bytes, export_uuid, num, excluidos = await svc.build_export(
            max_clearance=clearance,
            comentario=payload.comentario,
            year=payload.year,
            doc_type=payload.doc_type,
            actor=str(actor) if actor else None,
        )
    except ExportTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "export_demasiado_grande",
                "detail": f"{exc.total} radicados exceden el tope; acote con year/doc_type.",
                "status": 422,
            },
        )
    filename = f"orpyca-export-{export_uuid}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        status_code=status.HTTP_200_OK,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-UUID": export_uuid,
            "X-Export-Count": str(num),
            "X-Export-Excluded": str(excluidos),
        },
    )

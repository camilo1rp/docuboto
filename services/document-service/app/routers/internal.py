# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints internos (D-02, `X-Internal-Token`) — servicio-a-servicio
únicamente, nunca alcanzables desde el gateway (que solo enruta
`/api/v1/documents/*`, ver `api-gateway/app/routers/proxy.py`).

`POST /anexos` (E10 debt, cableado del AIP OAIS al cierre del expediente):
resuelve el `file_id` real de los anexos de un lote de radicados para que
archive-service pueda armar el payload de `POST /preservacion/aip` en
storage-service — SIN el gate de clearance que sí aplica
`GET /documents/{id}/anexos` (no-read-up, RF-SEG-08), porque este es un acto
de SISTEMA que necesita el inventario TOTAL, no lo que el usuario que cerró
el expediente vería. Único llamador legítimo: `archive-service`
(`app/clients/document_client.py`), scoped al tenant de la llamada
(`X-Tenant-Slug`, `get_tenant_conn`).
"""

import asyncpg
from fastapi import APIRouter, Depends, Request

from app.core.database import get_tenant_conn
from app.core.internal_auth import require_internal_token
from app.repositories.document_repository import DocumentRepository
from app.schemas.internal import AnexoInternalItem, AnexosInternalRequest

router = APIRouter(
    prefix="/api/v1/internal/documentos",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "/anexos",
    response_model=dict[str, list[AnexoInternalItem]],
    summary="Inventario TOTAL de anexos de un lote de radicados (acto de sistema, sin gate de clearance)",
)
async def anexos_batch(
    payload: AnexosInternalRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, list[AnexoInternalItem]]:
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    repo = DocumentRepository(conn, tenant_slug)
    grouped = await repo.list_anexos_for_radicados_internal(payload.radicado_ids)
    return {
        str(radicado_id): [
            AnexoInternalItem(file_id=a.file_id, filename=a.filename) for a in anexos
        ]
        for radicado_id, anexos in grouped.items()
    }

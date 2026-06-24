# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta y verificación de la auditoría inmutable (E08, ADR-008).

Exige `USUA_PERM_ADMIN` del llamante (gate a nivel de router, ADR-013). La cadena
de hash es por `tenant_slug`; el tenant viene de `X-Tenant-Slug`.
"""

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response
from orpycamcp_common import audit as audit_lib

from app.core.authz import require_permission
from app.core.database import get_tenant_conn, tenant_schema
from app.repositories.audit_query_repository import AuditQueryRepository
from app.schemas.audit import AuditEntry, AuditVerifyResponse

router = APIRouter(
    prefix="/api/v1/audit",
    tags=["audit"],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)


def _tenant_slug(request: Request) -> str:
    slug = request.headers.get("X-Tenant-Slug", "")
    tenant_schema(slug)  # valida (400 si falta/ inválido)
    return slug


@router.get("/verify", response_model=AuditVerifyResponse)
async def verify_audit_chain(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AuditVerifyResponse:
    """Recalcula la cadena de hash del tenant y reporta si está íntegra."""
    slug = _tenant_slug(request)
    ok, broken_id = await audit_lib.verify_chain(conn, slug)
    return AuditVerifyResponse(tenant_slug=slug, ok=ok, broken_id=broken_id)


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    request: Request,
    response: Response,
    action: str | None = Query(default=None),
    object_type: str | None = Query(default=None),
    object_ref: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[AuditEntry]:
    """Lista entradas de auditoría del tenant (más recientes primero) con filtros."""
    slug = _tenant_slug(request)
    filters = {
        "action": action,
        "object_type": object_type,
        "object_ref": object_ref,
        "actor": actor,
    }
    repo = AuditQueryRepository(conn)
    total = await repo.count(slug, filters)
    entries = await repo.list(slug, filters, limit=size, offset=(page - 1) * size)
    response.headers["X-Total-Count"] = str(total)
    return entries

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

`USUA_PERM_ADMIN` es competencia administrativa, **no** habilitación de seguridad:
administrar el tenant no es lo mismo que estar autorizado a conocer los asuntos
clasificados que en él se tramitan. El listado aplica además no-read-up por
clearance (RF-SEG-08) — de lo contrario la auditoría sería el canal por el que un
administrador sin habilitación conoce la existencia, el número y el trámite de los
documentos que el resto del sistema le niega.
"""

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response
from orpycamcp_common import audit as audit_lib

from app.core.authz import Caller, get_caller, require_permission
from app.core.database import get_tenant_conn, tenant_schema
from app.repositories.audit_query_repository import MAX_LEVEL, AuditQueryRepository
from app.repositories.clearance_repository import ClearanceRepository
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
    """Recalcula la cadena de hash del tenant y reporta si está íntegra.

    A diferencia del listado, la verificación **no** se recorta por clearance: la
    cadena solo tiene sentido probatorio si se recalcula entera, y sobre un
    subconjunto siempre daría "rota". La respuesta es content-free (un booleano y,
    si acaso, el id de la fila donde se rompe), así que no revela nada del objeto.
    """
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
    caller: Caller = Depends(get_caller),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[AuditEntry]:
    """Lista entradas de auditoría del tenant (más recientes primero) con filtros.

    Recortado por el clearance efectivo del llamante: las entradas cuyo objeto es un
    radicado o un expediente por encima de su nivel no se listan **ni se cuentan**
    (`X-Total-Count` refleja el mismo recorte; de lo contrario el total sería un
    oráculo de cuántos documentos clasificados hay). Filtrar por `object_ref` de un
    documento que no se alcanza devuelve cero entradas, indistinguible de que no
    exista.

    **Residuo declarado (RF-FIR-15)**: `id` es una secuencia monótona y va en la
    respuesta, así que los huecos entre ids visibles revelan CUÁNTAS entradas se
    ocultaron y, por los `ts` vecinos, cuándo — inferencia de volumen de trámite
    clasificado, sin contenido. No se cierra porque `id` es la clave que casa con
    `broken_id` de `/audit/verify`; cerrarlo exigiría un identificador opaco por
    entrada y el `id` crudo solo para clearance máximo.
    """
    slug = _tenant_slug(request)
    filters = {
        "action": action,
        "object_type": object_type,
        "object_ref": object_ref,
        "actor": actor,
    }
    max_level = (
        MAX_LEVEL
        if caller.is_root
        else await ClearanceRepository(conn).caller_max_level(caller.user_id)
    )
    repo = AuditQueryRepository(conn)
    total = await repo.count(slug, filters, max_level=max_level)
    entries = await repo.list(
        slug, filters, max_level=max_level, limit=size, offset=(page - 1) * size
    )
    response.headers["X-Total-Count"] = str(total)
    return entries

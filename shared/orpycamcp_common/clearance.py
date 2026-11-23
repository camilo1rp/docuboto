# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El gate de permiso del proceso (ADR-047; RF-SEG-08, RF-BUS-04, ADR-013).

Había siete copias de `require_permission`, una por servicio, con dos ejes que
cambiaban de una a otra. Aquí quedan las tres combinaciones que se usan, con
nombre propio:

* `require_permission`: conexión de tenant; sin persona, 403 sin asiento (las
  sondas anónimas no ensucian la cadena de hash).
* `require_actor_permission`: conexión de tenant; sin persona, 401 (`get_actor`).
* `require_vetted_permission`: conexión ya vetada sin `search_path`; el gate lo
  fija con el slug de la petición antes de leer las tablas RBAC, y sin slug niega.

Las tres usan la MISMA conexión que el endpoint (FastAPI cachea la dependencia
por petición) y `enforce_permission`, que deja el asiento de la denegación.
Política fail-closed: ante cualquier duda, se niega.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from orpycamcp_common import audit
from orpycamcp_common.database import get_tenant_conn, get_vetted_conn, tenant_schema
from orpycamcp_common.rbac import (
    check_permission,
    make_require_any_permission,
    mensaje_denegacion,
    nivel_efectivo,
)
from orpycamcp_common.usuario import id_exigido, id_opcional, slug_de

logger = logging.getLogger(__name__)

SERVICE = "docuboto"


async def get_actor(request: Request) -> uuid.UUID:
    """UUID interno del llamante (`request.state.usuario`); 401 si no hay persona."""
    return id_exigido(request)


def _prohibido(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "forbidden", "detail": detalle, "status": 403},
    )


async def enforce_permission(
    conn: asyncpg.Connection,
    actor: uuid.UUID | None,
    perm_name: str,
    min_crud: int = 1,
    *,
    tenant_slug: str | None,
) -> None:
    """403 si `actor` no tiene `perm_name` a `min_crud`; asienta la denegación de una persona.

    El mismo gate para la ruta HTTP y para la entrada en proceso (`<paquete>.api`):
    sin red de por medio, el permiso en la BD es lo que queda.
    """
    if await check_permission(conn, actor, perm_name, min_crud):
        return
    if actor is not None:
        try:
            await audit.append_denial(
                conn,
                tenant_slug=tenant_slug,
                service=SERVICE,
                actor=str(actor),
                perm_name=perm_name,
                min_crud=min_crud,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append_denial failed", exc_info=True)
    raise _prohibido(mensaje_denegacion(perm_name, min_crud, await nivel_efectivo(conn, actor, perm_name)))


def require_permission(perm_name: str, min_crud: int = 1):  # noqa: ANN201 — dependencia FastAPI
    """Gate con la conexión de tenant; sin persona, 403 sin asiento."""

    async def _dep(request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)) -> None:
        await enforce_permission(
            conn, id_opcional(request), perm_name, min_crud, tenant_slug=slug_de(request) or None
        )

    return _dep


def require_actor_permission(perm_name: str, min_crud: int = 1):  # noqa: ANN201
    """Gate con la conexión de tenant que exige persona (401 si falta)."""

    async def _dep(
        request: Request,
        actor: uuid.UUID = Depends(get_actor),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        await enforce_permission(conn, actor, perm_name, min_crud, tenant_slug=slug_de(request) or None)

    return _dep


def require_vetted_permission(perm_name: str, min_crud: int = 1):  # noqa: ANN201
    """Gate con la conexión vetada: fija el `search_path` antes de leer las tablas RBAC.

    Sin slug no se puede verificar: se niega EXPLÍCITAMENTE, sin depender de que
    la tabla no exista en el `search_path` heredado del pool.
    """

    async def _dep(request: Request, conn: asyncpg.Connection = Depends(get_vetted_conn)) -> None:
        slug = slug_de(request)
        if not slug:
            raise _prohibido("Se requiere X-Tenant-Slug para autorizar")
        await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        await enforce_permission(conn, id_opcional(request), perm_name, min_crud, tenant_slug=slug)

    return _dep


#: Gate any-of (basta UNO de los permisos) con la conexión de tenant.
require_any_permission = make_require_any_permission(service=SERVICE, get_conn=get_tenant_conn)

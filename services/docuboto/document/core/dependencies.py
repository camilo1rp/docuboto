# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Dependencias HTTP propias de document (ADR-047: la conexión es `orpycamcp_common.database`).

La entidad que atiende las rutas públicas (`/public/{tenant}/...`), el doble
inyectable de `tenant.api`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import ModuleType

import asyncpg
from fastapi import HTTPException, Path, Request, status
from orpycamcp_common.tenant import entidad_publica, slug_valido
from orpycamcp_common.tenant import tenant_schema as _schema_de

from tenant import api as tenant_api


def get_tenant_client() -> ModuleType:
    """Calendario, parámetros y organigrama de la entidad: `tenant.api`, en proceso.

    Se inyecta el módulo (sus funciones son la interfaz que los servicios
    llaman) para que las pruebas sustituyan la entidad entera con un doble.
    """
    return tenant_api


def _no_encontrado() -> HTTPException:
    """404 NEUTRO de las rutas públicas: el mismo cuerpo exacto para una entidad
    inexistente, suspendida o aún sin aprovisionar (no es un oráculo de existencia)."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "No encontrado", "status": 404},
    )


@asynccontextmanager
async def public_tenant_conn(
    pool: asyncpg.Pool, slug: str
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión con `search_path` al tenant para una ruta PÚBLICA (sin JWT), ADR-035.

    El slug viaja en el path. Sin estados de ciclo de vida (ADR-047 §3): la
    entidad que existe y tiene schema se sirve; si no, el 404 neutro. Si el
    registro no responde: 503 (fail-closed), que no distingue entidades.
    """
    if not slug_valido(slug):
        raise _no_encontrado()
    async with pool.acquire() as conn:
        try:
            existe = await entidad_publica(conn, slug)
        except Exception as exc:  # noqa: BLE001 — no poder saberlo se niega
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "tenant_estado_no_disponible",
                    "detail": "No se pudo comprobar el estado de la entidad.",
                    "status": 503,
                },
            ) from exc
        if not existe:
            raise _no_encontrado()
        await conn.execute(f'SET search_path = "{_schema_de(slug)}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


async def get_public_tenant_conn(
    request: Request, tenant: str = Path(...)
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Dependencia de las rutas públicas `/public/{tenant}/...` (ver `public_tenant_conn`)."""
    async with public_tenant_conn(request.app.state.db_pool, tenant) as conn:
        yield conn

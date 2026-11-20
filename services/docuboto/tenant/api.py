# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que otros dominios piden a tenant: plazos, parámetros y organigrama.

Eran tres rutas que document llamaba por HTTP al mismo proceso. Las rutas
siguen (las usa el frontend); document llama aquí (ADR-047 §1).
Los nombres de las funciones son los que document ya inyecta como
`tenant_client` (`document.core.database.get_tenant_client`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import asyncpg
from fastapi import HTTPException

from tenant.core.calendar import add_business_days
from tenant.core.database import acquire_tenant
from tenant.repositories.config_repository import ConfigRepository
from tenant.repositories.dependencias_repository import DependenciaRepository
from tenant.schemas.dependencias import DependenciaResponse

logger = logging.getLogger("tenant.api")


class TenantUnavailableError(Exception):
    """No se pudo leer el calendario, los parámetros o el organigrama."""


@asynccontextmanager
async def _tenant_conn(tenant_slug: str) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with acquire_tenant(tenant_slug) as conn:
            yield conn
    except (asyncpg.PostgresError, OSError, HTTPException) as exc:
        # `HTTPException`: slug inválido (`tenant_schema`), que tampoco es «sin datos».
        raise TenantUnavailableError(str(exc)) from exc


async def calculate_due_date(*, tenant_slug: str, from_date: date, business_days: int) -> date:
    """Vencimiento a `business_days` hábiles de `from_date`, con el calendario del tenant."""
    if not 0 <= business_days <= 3650:
        raise TenantUnavailableError(f"plazo fuera de rango: {business_days}")
    async with _tenant_conn(tenant_slug) as conn:
        # Ventana amplia para cubrir fines de semana y festivos del intervalo.
        window_end = from_date + timedelta(days=business_days * 2 + 60)
        non_business = await ConfigRepository(conn).non_business_dates(from_date, window_end)
    return add_business_days(from_date, business_days, non_business)


async def get_dependencia(*, tenant_slug: str, codigo: int) -> dict[str, Any] | None:
    """La dependencia por código (organigrama, E14), o `None` si no existe."""
    async with _tenant_conn(tenant_slug) as conn:
        row = await DependenciaRepository(conn).get_by_codigo(codigo)
    if row is None:
        return None
    return DependenciaResponse(**dict(row)).model_dump(mode="json")


async def get_params_strict(*, tenant_slug: str) -> dict[str, Any]:
    """Parámetros de la entidad (`sgd_param_admin`), **lanzando** ante un fallo.

    Para una política de tratamiento, «no se pudo leer» no es «no hay
    política» (B2, ADR-031): quien la lea responde 503.
    """
    async with _tenant_conn(tenant_slug) as conn:
        return await ConfigRepository(conn).get_params()


async def get_params(*, tenant_slug: str) -> dict[str, Any]:
    """Parámetros de la entidad; `{}` ante cualquier fallo, sin lanzar.

    La asimetría con `get_params_strict` es deliberada: del membrete depende
    que el oficio salga con el nombre de la entidad, no un vencimiento legal.
    Tumbar una radicación por eso sería peor que la falta, que la interfaz
    declara en `faltantes`.
    """
    try:
        return await get_params_strict(tenant_slug=tenant_slug)
    except TenantUnavailableError:
        logger.warning(
            "No se pudieron leer los parametros de la entidad %r: el documento "
            "se produce sin membrete y el campo se reporta como faltante.",
            tenant_slug,
        )
        return {}

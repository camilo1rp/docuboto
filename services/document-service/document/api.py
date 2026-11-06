# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Write path for radicados. Other packages import this module, not the repositories.

`mover_flujo` is self-contained so the split workflow image can copy just this
file. The process binds `abrir_flujo_si_hay` in the unified lifespan; document
tests and the split document image leave it unbound and keep the Redis consumer.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

_Apertura = Callable[[asyncpg.Connection, str, dict[str, Any]], Awaitable[str | None]]
_al_radicar: _Apertura | None = None


def registrar_apertura_de_flujo(fn: _Apertura | None) -> None:
    """The composition root registers workflow here. `None` clears the hook."""
    global _al_radicar
    _al_radicar = fn


async def abrir_flujo_si_hay(
    conn: asyncpg.Connection, tenant_slug: str, payload: dict[str, Any]
) -> str | None:
    """Opens the flow on the same connection as registration, if a hook is bound."""
    if _al_radicar is None:
        return None
    return await _al_radicar(conn, tenant_slug, payload)


async def mover_flujo(
    conn: asyncpg.Connection,
    *,
    actor: str | None,
    radicado_id: uuid.UUID,
    status: str,
    flujo_dept: str | None,
    flujo_dept_code: int | None,
    flujo_usuario: uuid.UUID | None,
) -> None:
    """Writes flow status and location. The caller already holds the row lock.

    `actor` is part of the contract even when this UPDATE does not audit: the
    asiento lives in the workflow acto that called this.
    """
    del actor
    await conn.execute(
        "UPDATE radicados SET status = $2, flujo_dept = $3, flujo_dept_code = $4, "
        "flujo_usuario = $5, updated_at = now() "
        "WHERE id = $1 AND (status IS DISTINCT FROM $2 "
        "OR flujo_dept IS DISTINCT FROM $3 "
        "OR flujo_dept_code IS DISTINCT FROM $4 "
        "OR flujo_usuario IS DISTINCT FROM $5)",
        radicado_id,
        status,
        flujo_dept,
        flujo_dept_code,
        flujo_usuario,
    )

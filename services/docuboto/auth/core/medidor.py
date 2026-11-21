# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Señal `audit_chain` (C18, ADR-041): la última verificación completa de la cadena.

La expone el único `/metrics` del proceso (`docuboto.routers.metrics`).
"""

from __future__ import annotations

import asyncpg
from orpycamcp_common import metricas

_ULTIMA_COMPLETA = (
    "SELECT finished_at, roturas_nuevas, roturas_declaradas, codigo_salida "
    "FROM public.audit_chain_runs WHERE alcance = 'completo' ORDER BY id DESC LIMIT 1"
)

_FAMILIAS = frozenset(
    {
        "orpycamcp_audit_chain_breaks",
        "orpycamcp_audit_chain_declared_breaks",
        "orpycamcp_audit_chain_verification_ok",
        "orpycamcp_audit_chain_last_verification_timestamp_seconds",
    }
)


async def medir_audit_chain(conn: asyncpg.Connection) -> list[metricas.Muestra]:
    fila = await conn.fetchrow(_ULTIMA_COMPLETA)
    if fila is None:
        raise metricas.SinMedicion("ninguna corrida completa de verify_audit_chains")
    return [
        metricas.Muestra("orpycamcp_audit_chain_breaks", fila["roturas_nuevas"]),
        metricas.Muestra("orpycamcp_audit_chain_declared_breaks", fila["roturas_declaradas"]),
        metricas.Muestra(
            "orpycamcp_audit_chain_verification_ok", 1 if fila["codigo_salida"] == 0 else 0
        ),
        metricas.Muestra(
            "orpycamcp_audit_chain_last_verification_timestamp_seconds",
            fila["finished_at"].timestamp(),
        ),
    ]

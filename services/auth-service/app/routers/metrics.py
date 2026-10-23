# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de auth-service: la señal `audit_chain` (C18, ADR-041).

NUNCA ejecuta `verify_chain` en el scrape (recorre el registro entero): lee la
última corrida COMPLETA que dejó `app.ops.verify_audit_chains` (backup, restore o
cron) en `public.audit_chain_runs`. Una corrida parcial (`--slug`,
`--excluir-prefijo`) no cuenta; sin corrida completa la señal sale «no verificada»
(`orpycamcp_signal_measured{signal="audit_chain"} 0`), no con ceros. La
antigüedad la juzga la alarma, no el exportador. No lo enruta el gateway y rechaza
con 404 toda petición con cabeceras de proxy.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Request, Response
from orpycamcp_common import metricas

from app.core.database import get_pool

router = APIRouter(tags=["metrics"])

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


_SENALES = (metricas.Senal("audit_chain", medir_audit_chain, familias=_FAMILIAS),)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return await metricas.responder(request, get_pool(), _SENALES)

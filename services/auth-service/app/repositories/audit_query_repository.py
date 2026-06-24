# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta de la auditoría canónica `public.audit_log` (E08, ADR-008).

Solo lectura: el ÚNICO punto de escritura es `orpycamcp_common.audit`. La cadena
es por `tenant_slug` (columna en la tabla, no por schema). Toda consulta filtra
por el tenant del request.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.schemas.audit import AuditEntry

_COLS = "id, ts, service, actor, canal, action, object_type, object_ref, payload, hash"
_FILTERABLE = ("action", "object_type", "object_ref", "actor")


class AuditQueryRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def _where(self, tenant_slug: str, filters: dict[str, str | None]) -> tuple[str, list[Any]]:
        conds = ["tenant_slug = $1"]
        params: list[Any] = [tenant_slug]
        idx = 2
        for col in _FILTERABLE:
            val = filters.get(col)
            if val is not None:
                conds.append(f"{col} = ${idx}")
                params.append(val)
                idx += 1
        return " AND ".join(conds), params

    async def count(self, tenant_slug: str, filters: dict[str, str | None]) -> int:
        where, params = self._where(tenant_slug, filters)
        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM public.audit_log WHERE {where}", *params
        )
        return total or 0

    async def list(
        self,
        tenant_slug: str,
        filters: dict[str, str | None],
        *,
        limit: int,
        offset: int,
    ) -> list[AuditEntry]:
        where, params = self._where(tenant_slug, filters)
        idx = len(params) + 1
        rows = await self._conn.fetch(
            f"SELECT {_COLS} FROM public.audit_log WHERE {where} "
            f"ORDER BY ts DESC, id DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            limit,
            offset,
        )
        return [_to_entry(r) for r in rows]


def _to_entry(row: asyncpg.Record) -> AuditEntry:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return AuditEntry(
        id=row["id"],
        ts=row["ts"],
        service=row["service"],
        actor=row["actor"],
        canal=row["canal"],
        action=row["action"],
        object_type=row["object_type"],
        object_ref=row["object_ref"],
        payload=payload,
        hash=row["hash"],
    )

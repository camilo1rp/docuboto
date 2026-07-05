# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta de solo-lectura de `public.audit_log` acotada a un expediente (E15
perfil v4, R3-b — historial de acceso).

Espejo de `auth-service/app/repositories/audit_query_repository.py`: el ÚNICO
punto de ESCRITURA de `audit_log` es `orpycamcp_common.audit`; este repositorio
nunca inserta. El filtro `tenant_slug` es defensa en profundidad (aunque
`audit_log` vive en `public`, no en el schema del tenant) — sin él, un llamante
de un tenant podría, en teoría, leer el historial de acceso de un expediente de
OTRO tenant si por azar comparte `object_ref` (el `code` de expediente,
p. ej. "EXP-2024-0001", NO es globalmente único entre tenants).
"""

from __future__ import annotations

import asyncpg

_COLS = "tenant_slug, ts, service, actor, canal, action, object_type, object_ref, payload"
_OBJECT_TYPES = ("expediente", "expediente_indice")


class AuditReadRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def count_for_expediente(self, tenant_slug: str, code: str) -> int:
        total: int = await self._conn.fetchval(
            "SELECT COUNT(*) FROM public.audit_log "
            "WHERE tenant_slug = $1 AND object_ref = $2 AND object_type = ANY($3::text[])",
            tenant_slug,
            code,
            list(_OBJECT_TYPES),
        )
        return total or 0

    async def list_for_expediente(
        self, tenant_slug: str, code: str, *, offset: int, limit: int
    ) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM public.audit_log "
                "WHERE tenant_slug = $1 AND object_ref = $2 AND object_type = ANY($3::text[]) "
                "ORDER BY ts DESC, id DESC LIMIT $4 OFFSET $5",
                tenant_slug,
                code,
                list(_OBJECT_TYPES),
                limit,
                offset,
            )
        )

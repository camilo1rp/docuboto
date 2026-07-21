# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos para OAI-PMH (E11, RF-INT-02). SQL crudo; search_path fijado al
schema del tenant por el router (endpoint público por-path, como el webhook postal).

INVARIANTE DE SEGURIDAD (spec §5 inv. 11 + ajuste tenant-security-auditor,
Ley 1712/2014 arts. 18-19, RF-SEG-08): la cosecha de metadatos SOLO expone lo
**público** (`nivel_seguridad = 1`) y NO anulado. El filtro se aplica AQUÍ, en la
fuente — el resto de la pila (servicio OAI, gateway) nunca ve un registro reservado,
así que la omisión es **indistinguible** por construcción: los conteos
(`completeListSize`), los *sets* y `GetRecord` se derivan todos de este mismo filtro.
"""

from datetime import datetime

import asyncpg

# Un radicado es cosechable SOLO si es público y no está anulado. Fuente única de
# verdad del recorte; cualquier consulta OAI la usa → no hay canal lateral de fuga.
_PUBLIC = "nivel_seguridad = 1 AND status <> 'anulado'"

_RECORD_COLS = "tracking_number, doc_type, subject, origin_dept, registered_at, tenant_code"


class OaiRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def _filters(
        self, from_dt: datetime | None, until_dt: datetime | None, set_spec: str | None
    ) -> tuple[str, list]:
        """Cláusula WHERE (siempre ancla en `_PUBLIC`) + parámetros posicionales."""
        clauses = [_PUBLIC]
        params: list = []
        if from_dt is not None:
            params.append(from_dt)
            clauses.append(f"registered_at >= ${len(params)}")
        if until_dt is not None:
            # Límite superior EXCLUSIVO: el servicio ya expandió `until` a fin-de-rango
            # según su granularidad (día/segundo), así que `<` lo hace inclusivo (Conf #7).
            params.append(until_dt)
            clauses.append(f"registered_at < ${len(params)}")
        if set_spec is not None:
            params.append(set_spec)
            clauses.append(f"doc_type = ${len(params)}")
        return " AND ".join(clauses), params

    async def count_public(
        self, from_dt: datetime | None, until_dt: datetime | None, set_spec: str | None
    ) -> int:
        where, params = self._filters(from_dt, until_dt, set_spec)
        return await self._conn.fetchval(
            f"SELECT count(*) FROM radicados WHERE {where}", *params
        )

    async def list_public(
        self,
        *,
        offset: int,
        limit: int,
        from_dt: datetime | None,
        until_dt: datetime | None,
        set_spec: str | None,
    ) -> list[asyncpg.Record]:
        where, params = self._filters(from_dt, until_dt, set_spec)
        params.extend([limit, offset])
        return await self._conn.fetch(
            f"SELECT {_RECORD_COLS} FROM radicados WHERE {where} "
            f"ORDER BY registered_at, tracking_number LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )

    async def get_public(self, tracking_number: str) -> asyncpg.Record | None:
        """Un registro por su número — SOLO si es público. Un reservado o inexistente
        devuelve None indistinguiblemente (el router lo mapea a `idDoesNotExist`)."""
        return await self._conn.fetchrow(
            f"SELECT {_RECORD_COLS} FROM radicados WHERE tracking_number = $1 AND {_PUBLIC}",
            tracking_number,
        )

    async def sets_public(self) -> list[str]:
        """Los `setSpec` (doc_type) que tienen AL MENOS un registro público — un set
        que solo contenga reservados NO se enumera (canal lateral cerrado)."""
        rows = await self._conn.fetch(
            f"SELECT DISTINCT doc_type FROM radicados WHERE {_PUBLIC} ORDER BY doc_type"
        )
        return [r["doc_type"] for r in rows]

    async def earliest_public(self) -> datetime | None:
        return await self._conn.fetchval(
            f"SELECT min(registered_at) FROM radicados WHERE {_PUBLIC}"
        )

    async def get_public_anexo(self, tracking_number: str) -> asyncpg.Record | None:
        """Primer anexo de un radicado PÚBLICO (para el `getContentStream` de CMIS).
        None si el radicado no es público/no existe o no tiene anexos — mismo recorte
        `_PUBLIC` (aquí cualificado por el JOIN), así el content stream nunca sirve un
        byte de un reservado (indistinguible de "sin contenido")."""
        return await self._conn.fetchrow(
            """
            SELECT a.file_id, a.filename, a.file_size, a.mime_type
              FROM anexos a
              JOIN radicados r ON r.id = a.radicado_id
             WHERE r.tracking_number = $1
               AND r.nivel_seguridad = 1 AND r.status <> 'anulado'
             ORDER BY a.created_at
             LIMIT 1
            """,
            tracking_number,
        )

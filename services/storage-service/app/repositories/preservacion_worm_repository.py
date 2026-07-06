# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de objetos protegidos bajo WORM real (E10 Increment A).

``preservacion_worm_objeto`` es la constancia de que una copia inmutable (MinIO
Object Lock) de un artefacto (hoy: el índice electrónico firmado) fue subida con
una retención derivada de la TRD. Alimenta el gate de disposición final
(``StorageService.purge_for_disposition``): mientras exista una fila activa
(``retain_until`` futuro o ``legal_hold``) para un expediente, su purga física
queda bloqueada.

search_path al tenant en cada método (gemelo de ``PreservacionRepository``).
"""

from __future__ import annotations

from datetime import datetime

import asyncpg

_COLS = (
    "id, expediente_id, tipo, bucket, object_key, version_id, sha256, "
    "retain_until, retention_mode, legal_hold, created_at"
)


class PreservacionWormRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._schema = f"tenant_{tenant_slug.replace('-', '_')}"

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}", public')

    async def insert(
        self,
        *,
        expediente_id: str,
        tipo: str,
        bucket: str,
        object_key: str,
        version_id: str | None,
        sha256: str,
        retain_until: datetime,
        retention_mode: str,
        legal_hold: bool,
    ) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_worm_objeto
                (expediente_id, tipo, bucket, object_key, version_id, sha256,
                 retain_until, retention_mode, legal_hold)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING {_COLS}
            """,
            expediente_id,
            tipo,
            bucket,
            object_key,
            version_id,
            sha256,
            retain_until,
            retention_mode,
            legal_hold,
        )
        assert row is not None
        return row

    async def active_lock_for_expediente(self, expediente_id: str) -> asyncpg.Record | None:
        """Devuelve una fila que SIGUE bloqueando la disposición, si existe.

        "Activa" = ``retain_until`` en el futuro O ``legal_hold = true``. Usada
        exclusivamente por el gate de ``purge_for_disposition`` — su ausencia NO
        prueba que el expediente nunca tuvo protección WORM, solo que ya expiró
        (o nunca se protegió).
        """
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            SELECT {_COLS} FROM preservacion_worm_objeto
            WHERE expediente_id = $1 AND (retain_until > now() OR legal_hold)
            LIMIT 1
            """,
            expediente_id,
        )

    async def list_for_expediente(self, expediente_id: str) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM preservacion_worm_objeto "
                "WHERE expediente_id = $1 ORDER BY created_at DESC",
                expediente_id,
            )
        )

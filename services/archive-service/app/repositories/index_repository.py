# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio del índice electrónico (E15, versionado append-only).

La conexión llega con search_path al tenant. `insert_version` escribe la versión
del índice y su snapshot de items atómicamente. La versión vigente es MAX(version).
"""

from __future__ import annotations

import uuid

import asyncpg

from app.services.index_builder import IndiceItemData

_INDICE_COLS = (
    "id, expediente_id, version, xml, xml_sha256, content_hash, algoritmo, "
    "xsd_version, num_documentos, estado, firma_id, minio_key_firmado, "
    "generado_por, generado_ts"
)


class IndexRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_current(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_INDICE_COLS} FROM expediente_indice "
            "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
            expediente_id,
        )

    async def get_version(self, expediente_id: uuid.UUID, version: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_INDICE_COLS} FROM expediente_indice "
            "WHERE expediente_id = $1 AND version = $2",
            expediente_id,
            version,
        )

    async def list_versions(
        self, expediente_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[asyncpg.Record], int]:
        total = await self._conn.fetchval(
            "SELECT COUNT(*) FROM expediente_indice WHERE expediente_id = $1",
            expediente_id,
        )
        rows = await self._conn.fetch(
            f"SELECT {_INDICE_COLS} FROM expediente_indice WHERE expediente_id = $1 "
            "ORDER BY version DESC LIMIT $2 OFFSET $3",
            expediente_id,
            limit,
            offset,
        )
        return list(rows), total or 0

    async def mark_signed(self, indice_id: uuid.UUID, firma_id: uuid.UUID) -> None:
        """Marca la versión del índice como firmada e inmutable (E15-F3)."""
        await self._conn.execute(
            "UPDATE expediente_indice SET estado='firmado', firma_id=$2 WHERE id=$1",
            indice_id,
            firma_id,
        )

    async def get_items(self, indice_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT document_id, orden, titulo, tipologia, fecha_declaracion, "
                "fecha_archivado, valor_huella, funcion_resumen, formato, tamano_bytes "
                "FROM expediente_indice_item WHERE indice_id = $1 ORDER BY orden ASC",
                indice_id,
            )
        )

    async def insert_version(
        self,
        *,
        expediente_id: uuid.UUID,
        version: int,
        xml: str,
        xml_sha256: str,
        content_hash: str,
        num_documentos: int,
        generado_por: str,
        items: list[IndiceItemData],
    ) -> asyncpg.Record:
        async with self._conn.transaction():
            indice = await self._conn.fetchrow(
                f"""
                INSERT INTO expediente_indice
                    (expediente_id, version, xml, xml_sha256, content_hash,
                     num_documentos, generado_por)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING {_INDICE_COLS}
                """,
                expediente_id,
                version,
                xml,
                xml_sha256,
                content_hash,
                num_documentos,
                generado_por,
            )
            assert indice is not None
            for it in items:
                await self._conn.execute(
                    """
                    INSERT INTO expediente_indice_item
                        (indice_id, document_id, orden, titulo, tipologia,
                         fecha_archivado, valor_huella, funcion_resumen)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    indice["id"],
                    uuid.UUID(it.document_id),
                    it.orden,
                    it.titulo,
                    it.tipologia,
                    it.fecha_archivado,
                    it.valor_huella,
                    it.funcion_resumen,
                )
        return indice

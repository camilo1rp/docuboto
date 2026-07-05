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

from app.services.index_builder import XSD_VERSION, IndiceItemData

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

    async def mark_signed(
        self,
        indice_id: uuid.UUID,
        firma_id: uuid.UUID,
        minio_key_firmado: str | None = None,
    ) -> None:
        """Marca la versión del índice como firmada e inmutable (E06 Inc.1).

        Defensa en profundidad: el `WHERE` excluye explícitamente filas ya
        `'firmado'` — un índice sellado es inmutable y no debe re-escribirse
        (aunque `IndexService.firmar_indice` ya evita llamar aquí dos veces
        sobre el mismo índice, esta cláusula lo garantiza también a nivel BD).
        """
        await self._conn.execute(
            "UPDATE expediente_indice SET estado='firmado', firma_id=$2, "
            "minio_key_firmado=$3 WHERE id=$1 AND estado <> 'firmado'",
            indice_id,
            firma_id,
            minio_key_firmado,
        )

    async def mark_pendiente(self, indice_id: uuid.UUID) -> None:
        """Marca la versión como 'pendiente_firma' (E06 Inc.1, RT-16).

        Decisión del usuario: si el sellado XAdES-B falla, el índice queda en
        este estado (en vez de 'vigente' silencioso, que ocultaba el fallo) y
        el cierre del expediente NO se bloquea. Reintentable e idempotente vía
        `POST /expedientes/{id}/indice/firmar`.

        Defensa en profundidad: el `WHERE` excluye explícitamente filas ya
        `'firmado'` — un índice sellado es inmutable y NUNCA debe degradarse
        a `'pendiente_firma'` (protege la misma invariante que la migración
        015 / `regenerate`, aunque los llamadores actuales solo invocan esto
        sobre índices no firmados).
        """
        await self._conn.execute(
            "UPDATE expediente_indice SET estado='pendiente_firma' WHERE id=$1 "
            "AND estado <> 'firmado'",
            indice_id,
        )

    async def get_items(self, indice_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT document_id, orden, titulo, tipologia, fecha_declaracion, "
                "fecha_archivado, valor_huella, funcion_resumen, formato, tamano_bytes, "
                "folios, orden_documental, nivel_seguridad_snapshot, estado, "
                "dependencia_productora_codigo, dependencia_productora_nombre "
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
                     num_documentos, generado_por, xsd_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {_INDICE_COLS}
                """,
                expediente_id,
                version,
                xml,
                xml_sha256,
                content_hash,
                num_documentos,
                generado_por,
                XSD_VERSION,  # explícito: NO confiar en el DEFAULT 'v1' de la tabla
            )
            assert indice is not None
            for it in items:
                await self._conn.execute(
                    """
                    INSERT INTO expediente_indice_item
                        (indice_id, document_id, orden, titulo, tipologia,
                         fecha_archivado, valor_huella, funcion_resumen,
                         formato, tamano_bytes, folios,
                         orden_documental, nivel_seguridad_snapshot, estado,
                         dependencia_productora_codigo, dependencia_productora_nombre)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    """,
                    indice["id"],
                    uuid.UUID(it.document_id),
                    it.orden,
                    it.titulo,
                    it.tipologia,
                    it.fecha_archivado,
                    it.valor_huella,
                    it.funcion_resumen,
                    it.formato,
                    it.tamano_bytes,
                    it.folios,
                    # v3 (E15 finalización RT-15): snapshot inmutable del
                    # orden_documental ESTABLE, el nivel de seguridad heredado
                    # del expediente y el estado del vínculo EN el momento de
                    # esta versión — permiten reconstruir el hueco trazable de
                    # versiones históricas sin depender del estado actual de
                    # `expediente_radicados`.
                    it.orden,
                    it.nivel_seguridad,
                    it.estado,
                    # v4 (E15 perfil v4, R1): snapshot de la dependencia
                    # productora — NULL si no se declaró al vincular.
                    it.dependencia_productora_codigo,
                    it.dependencia_productora_nombre,
                )
        return indice

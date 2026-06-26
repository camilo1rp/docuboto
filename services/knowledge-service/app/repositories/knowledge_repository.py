# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de la capa de conocimiento (E21). search_path ya fijado por la conexión."""

from __future__ import annotations

import json

import asyncpg


class KnowledgeRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert_chunk(
        self,
        *,
        source_type: str,
        source_ref: str,
        texto: str,
        embedding_literal: str,
        metadata: dict,
        acl: dict,
        nivel_seguridad: int,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, metadata, acl, nivel_seguridad)
            VALUES ($1, $2, $3, $4::vector, $5::jsonb, $6::jsonb, $7)
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            texto,
            embedding_literal,
            json.dumps(metadata or {}),
            json.dumps(acl or {}),
            nivel_seguridad,
        )
        assert row is not None
        return row

    async def search(
        self,
        *,
        embedding_literal: str,
        top_k: int,
        max_clearance: int,
        acl_filter: dict | None,
    ) -> list[asyncpg.Record]:
        """Vecinos más cercanos (coseno) con PRE-FILTRADO ACL.

        El control de acceso se resuelve **en el servidor** (RF-SEG-08 / RF-BUS-04): se
        acota SIEMPRE por la clasificación del chunk (columna ``nivel_seguridad``,
        ``NOT NULL`` con default **CLASIFICADA** — fail-closed, D-02) ≤ el clearance del
        llamante. El ``acl_filter`` opcional del
        cliente es solo NARROW adicional (p. ej. por dependencia); nunca amplía acceso.
        """
        params: list[object] = [embedding_literal]
        conds: list[str] = []

        # D-02: filtro sobre la columna de primera clase, no sobre JSONB.
        params.append(max_clearance)
        conds.append(f"nivel_seguridad <= ${len(params)}")

        if acl_filter:
            params.append(json.dumps(acl_filter))
            conds.append(f"acl @> ${len(params)}::jsonb")

        where = "WHERE " + " AND ".join(conds)
        params.append(top_k)
        return list(
            await self._conn.fetch(
                f"""
                SELECT id, source_type, source_ref, texto, metadata,
                       1 - (embedding <=> $1::vector) AS score
                  FROM knowledge_chunk
                  {where}
                 ORDER BY embedding <=> $1::vector
                 LIMIT ${len(params)}
                """,
                *params,
            )
        )

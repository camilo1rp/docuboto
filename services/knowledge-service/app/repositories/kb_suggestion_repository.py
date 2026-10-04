# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de `kb_suggestions` (E21 Inc.3 — auditoría CC-02/CC-03).

Cada fila es el registro ADVISORIO de una generación del Patrón B (RAG con citas):
qué se sugirió, con qué proveedor/modelo (columnas SEPARADAS — CC-03: a dónde salió
el contenido) y con qué confianza. `aceptada_por`/`aceptada_en` quedan NULL al insertar — son advisory, la aceptación
humana es un acto POSTERIOR (CC-02: la capa sugiere, no decide) que expone
`POST /knowledge/suggestions/{id}/aceptar` (T10); solo la acepta quien la generó
(`creada_por`, migración 012).

search_path ya fijado por la conexión (mismo patrón que `KnowledgeRepository`).
"""

from __future__ import annotations

import json
import uuid

import asyncpg


class KbSuggestionRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        tipo: str,
        objeto_ref: str | None,
        sugerencia: dict,
        proveedor: str,
        modelo: str,
        confianza: float,
        canal: str,
        creada_por: uuid.UUID | None = None,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            """
            INSERT INTO kb_suggestions
                (tipo, objeto_ref, sugerencia, proveedor, modelo, confianza, canal,
                 creada_por)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8)
            RETURNING id, created_at
            """,
            tipo,
            objeto_ref,
            json.dumps(sugerencia, default=str),
            proveedor,
            modelo,
            confianza,
            canal,
            creada_por,
        )
        assert row is not None
        return row

    async def get_for_update(
        self, suggestion_id: uuid.UUID, *, creada_por: uuid.UUID
    ) -> asyncpg.Record | None:
        """Fila bloqueada SOLO si la creó ``creada_por`` (fail-closed: una fila sin
        creador —anterior a la migración 012— o de otra persona no se devuelve).
        Debe llamarse dentro de una transacción."""
        return await self._conn.fetchrow(
            """
            SELECT id, aceptada_por, aceptada_en, sugerencia, proveedor, modelo
              FROM kb_suggestions
             WHERE id = $1 AND creada_por = $2
               FOR UPDATE
            """,
            suggestion_id,
            creada_por,
        )

    async def mark_accepted(
        self, suggestion_id: uuid.UUID, *, aceptada_por: uuid.UUID
    ) -> asyncpg.Record | None:
        """Registra la aceptación humana de una sugerencia (CC-02, advisory).

        Solo la acepta quien la generó (``creada_por``) y una sola vez
        (``aceptada_en IS NULL``): la condición va en el propio ``WHERE``, no en una
        comprobación previa que otra petición pueda adelantar.
        """
        return await self._conn.fetchrow(
            """
            UPDATE kb_suggestions
               SET aceptada_por = $2,
                   aceptada_en  = now()
             WHERE id = $1 AND creada_por = $2 AND aceptada_en IS NULL
            RETURNING id, aceptada_por, aceptada_en
            """,
            suggestion_id,
            aceptada_por,
        )

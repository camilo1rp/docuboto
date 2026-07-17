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

    async def upsert_chunk(
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
        """Ingesta idempotente (E21): usada por el worker de eventos, no por /ingest.

        ``ON CONFLICT (source_type, source_ref)`` (migración 003) hace que un
        redelivery at-least-once del mismo `document.radicado.created` actualice
        el chunk existente en vez de duplicarlo.

        ``nivel_seguridad = GREATEST(knowledge_chunk.nivel_seguridad, EXCLUDED.nivel_seguridad)``
        es un CANDADO DE SEGURIDAD, no un capricho de idempotencia: un evento
        `created` reprocesado TARDE (p. ej. tras un reintento con retraso) nunca debe
        BAJAR el nivel de un chunk que un `reclassified` posterior ya subió — el
        `created` original no conoce la reclasificación que ocurrió después. La ruta
        de reclasificación (``update_nivel_by_source``) es la ÚNICA que puede bajar
        el nivel, porque ahí el nivel nuevo es la fuente de verdad explícita del
        evento, no un valor potencialmente obsoleto.
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, metadata, acl, nivel_seguridad)
            VALUES ($1, $2, $3, $4::vector, $5::jsonb, $6::jsonb, $7)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET texto          = EXCLUDED.texto,
                   embedding      = EXCLUDED.embedding,
                   metadata       = EXCLUDED.metadata,
                   acl            = EXCLUDED.acl,
                   nivel_seguridad = GREATEST(
                       knowledge_chunk.nivel_seguridad, EXCLUDED.nivel_seguridad
                   ),
                   updated_at     = now()
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

    async def update_nivel_by_source(
        self, source_type: str, source_ref: str, nivel_seguridad: int
    ) -> asyncpg.Record:
        """Fija el nivel EXACTO de un chunk indexado — UPSERT-con-centinela.

        Antes era un ``UPDATE`` plano (sin ``ON CONFLICT``): si un
        `document.radicado.reclassified` llegaba ANTES que el `created` original
        (el `created` falló un embed transitorio y `reclaim_stale` lo recuperó
        DESPUÉS), el UPDATE afectaba 0 filas — el nivel del `reclassified` se
        perdía sin dejar rastro, y el `created` tardío insertaba el chunk PLANO a
        su nivel de nacimiento (normalmente PUBLICA), sub-clasificándolo. Es el
        mismo hueco que ``tombstone_by_source`` cierra para la rama de proveedor
        EXTERNO — este UPSERT lo cierra para la rama STUB/local (``nuevo <
        RESERVADA`` o ``provider == "stub"``, ver
        ``ingestion.apply_reclassification``).

        ASIMETRÍA DE SEGURIDAD deliberada entre las dos ramas del UPSERT:
          - Rama INSERT (fila ausente — `reclassified` llegó antes que `created`):
            deja un CENTINELA (``embedding = NULL``, ``texto = ''``,
            ``metadata = '{}'``) al nivel del `reclassified`. ``search`` lo
            excluye (``embedding IS NOT NULL``). Un `created` posterior hace
            ``ON CONFLICT`` contra este centinela; su nivel EFECTIVO =
            ``max(nivel_del_evento, nivel_existente)`` (ver
            ``ingestion.ingest_radicado_created``) preserva el nivel del
            centinela vía ``GREATEST`` en ``upsert_chunk`` — un `created` (siempre
            nivel de nacimiento) NUNCA baja el nivel que dejó un `reclassified`
            explícito.
          - Rama CONFLICT (fila presente): fija el nivel EXACTO — SIN GREATEST, a
            propósito, a diferencia de ``upsert_chunk``. Esta es la ruta de
            reclasificación: el `reclassified` es la fuente de verdad explícita
            del nuevo nivel, y una desclasificación legítima (p. ej.
            RESERVADA→PUBLICA) debe poder BAJAR el nivel de un chunk existente —
            GREATEST lo bloquearía.

        En conjunto con ``upsert_chunk``: un `created` nunca baja un nivel; solo
        un `reclassified` explícito lo hace, llegue en el orden que llegue.
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, metadata, acl, nivel_seguridad)
            VALUES ($1, $2, '', NULL, '{}'::jsonb, '{}'::jsonb, $3)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET nivel_seguridad = EXCLUDED.nivel_seguridad,
                   updated_at      = now()
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            nivel_seguridad,
        )
        assert row is not None
        return row

    async def get_nivel_by_source(self, source_type: str, source_ref: str) -> int | None:
        """Lee el `nivel_seguridad` ACTUAL del chunk, o `None` si no existe aún.

        Usada por `ingestion.ingest_radicado_created` para calcular el nivel
        EFECTIVO (`max(nivel_del_evento, nivel_existente)`) ANTES de decidir si el
        material es embebible — cierra la fuga en la que un `created` reprocesado
        TARDE (después de un `reclassified` que subió el nivel) reencarnaba el
        chunk a su nivel viejo porque no tenía forma de conocer el nivel actual.
        """
        return await self._conn.fetchval(
            "SELECT nivel_seguridad FROM knowledge_chunk WHERE source_type = $1 AND source_ref = $2",
            source_type,
            source_ref,
        )

    async def tombstone_by_source(
        self, source_type: str, source_ref: str, nivel_seguridad: int
    ) -> asyncpg.Record:
        """UPSERT a un TOMBSTONE: conserva la fila-centinela pero vacía el material
        derivado (``embedding = NULL``, ``texto = ''``, ``metadata = '{}'``).

        Reemplaza al antiguo `delete_by_source` en la ruta de reclasificación (R1,
        provider externo, nivel >= RESERVADA). Un DELETE liso rompía el candado de
        seguridad: `upsert_chunk` solo protege el nivel vía `GREATEST` en la rama
        `ON CONFLICT`, que exige que la fila EXISTA. Si `apply_reclassification`
        borraba la fila y luego un `document.radicado.created` REDELIVERED (tardío,
        at-least-once) llegaba sin conflicto, insertaba PLANO al nivel viejo (más
        bajo) — el radicado reclasificado reaparecía en el índice a un nivel
        inferior, recuperable por cualquier clearance, y su texto se reenviaba al
        proveedor externo. El tombstone deja la fila viva (con el nivel más alto
        vía GREATEST) para que ese `created` tardío SIEMPRE encuentre con qué
        comparar — ver también `KnowledgeRepository.get_nivel_by_source` y
        `ingestion.ingest_radicado_created` (nivel EFECTIVO).

        UPSERT (no UPDATE): cubre el reordenamiento inverso — un `reclassified`
        que llega ANTES que el `created` original crea el tombstone desde cero, y
        el `created` tardío que llegue después lo encontrará y calculará su nivel
        efectivo correctamente.

        `KnowledgeRepository.search` excluye los tombstones con
        `AND embedding IS NOT NULL`: nunca son resultado de búsqueda.

        `delete_by_source` no queda huérfano: se conserva para una futura purga
        real en anulación/expurgo del radicado (donde SÍ se quiere borrar la fila,
        no tombstonearla) — pero la reclasificación ya no lo usa.
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, metadata, acl, nivel_seguridad)
            VALUES ($1, $2, '', NULL, '{}'::jsonb, '{}'::jsonb, $3)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET texto           = '',
                   embedding       = NULL,
                   metadata        = '{}'::jsonb,
                   nivel_seguridad = GREATEST(
                       knowledge_chunk.nivel_seguridad, EXCLUDED.nivel_seguridad
                   ),
                   updated_at      = now()
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            nivel_seguridad,
        )
        assert row is not None
        return row

    async def delete_by_source(self, source_type: str, source_ref: str) -> str:
        """Purga DEFINITIVA del chunk indexado (borra la fila).

        NO la usa la reclasificación (ver `tombstone_by_source` y su docstring
        para el porqué: un DELETE anula el candado GREATEST de `upsert_chunk`).
        Queda disponible para un futuro flujo de anulación/expurgo real del
        radicado, donde no retener ni siquiera la fila-centinela es lo correcto.
        """
        tag: str = await self._conn.execute(
            "DELETE FROM knowledge_chunk WHERE source_type = $1 AND source_ref = $2",
            source_type,
            source_ref,
        )
        return tag

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

        ``embedding IS NOT NULL`` excluye los TOMBSTONES (`tombstone_by_source`):
        una fila-centinela sin material derivado nunca es un resultado de búsqueda,
        sea cual sea su `nivel_seguridad`/clearance del llamante.
        """
        params: list[object] = [embedding_literal]
        conds: list[str] = ["embedding IS NOT NULL"]

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

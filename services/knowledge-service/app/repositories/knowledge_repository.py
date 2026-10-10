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
import uuid
from datetime import datetime

import asyncpg

from app.core.clearance import CLASIFICADA


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

        ``voided`` (purga PERMANENTE por anulación, migración 006) queda
        DELIBERADAMENTE fuera de la lista de columnas del INSERT y del SET del
        ``ON CONFLICT``: en el INSERT (fila nueva) toma el DEFAULT de la tabla
        (``false``); en el CONFLICT (fila existente) queda intacta — un `created`
        JAMÁS debe des-voidear un chunk (ver el guard en
        ``ingestion.ingest_radicado_created`` y ``KnowledgeRepository.void_by_source``,
        que sí puede escribir ``voided = true``).
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
        self,
        source_type: str,
        source_ref: str,
        nivel_seguridad: int,
        occurred_at: datetime | None = None,
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

        SEGUNDA RED (AREA-10-08): ``occurred_at`` es el instante del evento. Un replay
        tardío desde la DLQ de un ``reclassified`` VIEJO no debe deshacer una
        reclasificación posterior, y menos bajarla. ``nivel_evento_at`` guarda el
        instante del último evento aplicado: un evento más antiguo se acepta (no falla,
        sale de la DLQ) pero no cambia el nivel. Sin ``occurred_at`` (o no
        interpretable) no hay con qué ordenar: solo puede SUBIR (``GREATEST``).
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk AS k
                (source_type, source_ref, texto, embedding, metadata, acl,
                 nivel_seguridad, nivel_evento_at)
            VALUES ($1, $2, '', NULL, '{}'::jsonb, '{}'::jsonb, $3, $4::timestamptz)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET nivel_seguridad = CASE
                       WHEN EXCLUDED.nivel_evento_at IS NULL
                           THEN GREATEST(k.nivel_seguridad, EXCLUDED.nivel_seguridad)
                       WHEN k.nivel_evento_at IS NULL
                            OR EXCLUDED.nivel_evento_at >= k.nivel_evento_at
                           THEN EXCLUDED.nivel_seguridad
                       ELSE k.nivel_seguridad
                   END,
                   nivel_evento_at = GREATEST(k.nivel_evento_at, EXCLUDED.nivel_evento_at),
                   updated_at      = now()
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            nivel_seguridad,
            occurred_at,
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

    async def get_chunk_state(
        self, source_type: str, source_ref: str
    ) -> asyncpg.Record | None:
        """Lee (``nivel_seguridad``, ``voided``) del chunk ACTUAL, o `None` si no
        existe aún.

        Sustituye a `get_nivel_by_source` como lectura previa de
        `ingestion.ingest_radicado_created`: además del nivel EFECTIVO (mismo cálculo
        que ya hacía `get_nivel_by_source`), necesita saber si el radicado fue
        ANULADO — `voided = true` es un marcador PERMANENTE (migración 006): un
        `created` reprocesado tras la anulación (redelivery at-least-once, en
        cualquier orden respecto al evento `annulled`) NUNCA debe re-indexar el
        chunk. Ver `void_by_source` y el guard en `ingest_radicado_created`.
        """
        return await self._conn.fetchrow(
            "SELECT nivel_seguridad, voided FROM knowledge_chunk "
            "WHERE source_type = $1 AND source_ref = $2",
            source_type,
            source_ref,
        )

    async def tombstone_by_source(
        self,
        source_type: str,
        source_ref: str,
        nivel_seguridad: int,
        occurred_at: datetime | None = None,
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
                (source_type, source_ref, texto, embedding, metadata, acl,
                 nivel_seguridad, nivel_evento_at)
            VALUES ($1, $2, '', NULL, '{}'::jsonb, '{}'::jsonb, $3, $4::timestamptz)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET texto           = '',
                   embedding       = NULL,
                   metadata        = '{}'::jsonb,
                   nivel_seguridad = GREATEST(
                       knowledge_chunk.nivel_seguridad, EXCLUDED.nivel_seguridad
                   ),
                   nivel_evento_at = GREATEST(
                       knowledge_chunk.nivel_evento_at, EXCLUDED.nivel_evento_at
                   ),
                   updated_at      = now()
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            nivel_seguridad,
            occurred_at,
        )
        assert row is not None
        return row

    async def delete_by_source(self, source_type: str, source_ref: str) -> str:
        """Purga DEFINITIVA del chunk indexado (borra la fila).

        NO la usa la reclasificación NI la anulación (ver `tombstone_by_source` /
        `void_by_source` y sus docstrings para el porqué: un DELETE anula el
        candado GREATEST de `upsert_chunk` y, para la anulación, permitiría que un
        `created` redelivered DESPUÉS del DELETE reinserte la fila desde cero SIN
        `voided` — el DELETE borraría el propio marcador permanente que hace la
        purga irreversible). Queda disponible para un futuro expurgo físico real
        (purga total del historial, fuera de este MVP), donde no retener ni
        siquiera la fila-centinela es lo correcto.
        """
        tag: str = await self._conn.execute(
            "DELETE FROM knowledge_chunk WHERE source_type = $1 AND source_ref = $2",
            source_type,
            source_ref,
        )
        return tag

    async def void_by_source(self, source_type: str, source_ref: str) -> asyncpg.Record:
        """Purga PERMANENTE por anulación de radicado (residual "purga-en-anulación",
        E21; hallazgo archival-compliance-auditor): UPSERT-con-centinela GEMELO de
        `tombstone_by_source`, pero con dos diferencias deliberadas:

          1. Fija ``voided = true`` — a diferencia del tombstone por nivel
             (reversible: una desclasificación posterior puede reembeber vía
             `update_nivel_by_source`), la anulación es TERMINAL (Ley 594/2000 — un
             radicado anulado no se des-anula). `ingest_radicado_created` SIEMPRE
             respeta `voided` incondicionalmente (SKIP total, ver su docstring);
             ninguna ruta de creación/reclasificación vuelve a poner `voided=false`.
          2. Fija ``nivel_seguridad = GREATEST(actual, CLASIFICADA)`` — fail-safe al
             nivel MÁXIMO (en vez de recibir el nivel como parámetro, como sí hace
             `tombstone_by_source`): el evento `document.radicado.annulled` no
             transporta `nivel_seguridad` (payload mínimo, ver `document_service.py`
             `_emit_annulled`), así que ante la duda el centinela queda al nivel
             más restrictivo — nunca visible por debajo de CLASIFICADA aunque el
             filtro `NOT voided` de `search` ya lo excluye por completo.

        INSERT (fila ausente — `annulled` llegó antes que `created`): deja un
        centinela `voided=true` desde el nacimiento. CONFLICT (fila presente):
        marca `voided=true` y purga el contenido (texto/embedding/metadata) — el
        material ya pudo haber salido hacia un proveedor externo de embeddings y no
        debe permanecer expuesto a un re-embed futuro. La fila NUNCA se borra
        (ver `delete_by_source`) ni `voided` se limpia — es, junto con `search`
        (``AND NOT voided``) y el guard de `ingest_radicado_created`, la garantía
        de que la purga es permanente sea cual sea el orden de entrega
        created↔annulled.
        """
        row = await self._conn.fetchrow(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, metadata, acl,
                 nivel_seguridad, voided)
            VALUES ($1, $2, '', NULL, '{}'::jsonb, '{}'::jsonb, $3, true)
            ON CONFLICT (source_type, source_ref) DO UPDATE
               SET texto           = '',
                   embedding       = NULL,
                   metadata        = '{}'::jsonb,
                   voided          = true,
                   nivel_seguridad = GREATEST(
                       knowledge_chunk.nivel_seguridad, EXCLUDED.nivel_seguridad
                   ),
                   updated_at      = now()
            RETURNING id, source_type, source_ref, created_at
            """,
            source_type,
            source_ref,
            CLASIFICADA,
        )
        assert row is not None
        return row

    async def list_pending_reembed_ids(self) -> list[uuid.UUID]:
        """Snapshot (una sola consulta) de los `id` con centinela `embedding IS
        NULL` que SÍ tienen contenido real pendiente de re-embeber (E21 Inc.1
        backfill, migración 007).

        Excluye, con la misma condición, dos clases de fila que también tienen
        `embedding IS NULL` pero NUNCA deben re-embeberse:
          - Tombstones reales (`tombstone_by_source`/`update_nivel_by_source`
            rama INSERT) y voided (`void_by_source`): siempre dejan
            `texto = ''` — el filtro `texto <> ''` los excluye sin distinguir
            "tombstone por nivel" de "voided por anulación", ambos
            deliberadamente vacíos de contenido.
          - `voided`: defensa en profundidad explícita (ya cubiertos por
            `texto <> ''`, pero un `voided=true` nunca debe re-indexarse pase
            lo que pase con `texto`, igual que en `search`).

        Deliberadamente un SNAPSHOT de solo-ids (no de filas completas, y sin
        paginar por `embedding IS NULL` a medida que el backfill avanza): si
        alguna fila queda bloqueada por residencia (`is_embeddable` fail-closed,
        ver `app/ops/reembed.py`), seguiría cumpliendo `embedding IS NULL` en
        la siguiente vuelta — paginar sobre esa condición sin cursor fijo
        haría un loop infinito. Iterar sobre esta lista fija en el llamante
        (`app/ops/reembed.py`) converge siempre, la bloquee o no `is_embeddable`.
        """
        rows = await self._conn.fetch(
            """
            SELECT id FROM knowledge_chunk
             WHERE embedding IS NULL AND NOT voided AND texto <> ''
             ORDER BY created_at
            """
        )
        return [r["id"] for r in rows]

    async def fetch_chunks_by_ids(self, ids: list[uuid.UUID]) -> list[asyncpg.Record]:
        """Relee un lote de chunks por `id` (contenido + nivel), re-verificando
        `embedding IS NULL AND NOT voided` EN EL MOMENTO de la lectura — defensa
        contra una carrera con `void_radicado`/una reclasificación concurrente
        entre el snapshot (`list_pending_reembed_ids`) y este fetch (ventana
        corta, pero el backfill puede tardar en tenants grandes)."""
        if not ids:
            return []
        return list(
            await self._conn.fetch(
                """
                SELECT id, source_type, source_ref, texto, nivel_seguridad
                  FROM knowledge_chunk
                 WHERE id = ANY($1::uuid[]) AND embedding IS NULL AND NOT voided
                """,
                ids,
            )
        )

    async def update_embedding(self, chunk_id: uuid.UUID, embedding_literal: str) -> None:
        """Repuebla el `embedding` de un chunk existente (backfill/re-embed).

        NO toca `texto`/`metadata`/`nivel_seguridad`/`acl` — el backfill re-usa
        el `texto` YA persistido (capa derivada, CC-01: no vuelve a la fuente)
        y no reclasifica nada; solo produce el vector con el proveedor actual.
        """
        await self._conn.execute(
            "UPDATE knowledge_chunk SET embedding = $1::vector, updated_at = now() "
            "WHERE id = $2",
            embedding_literal,
            chunk_id,
        )

    async def get_pivot_chunk(
        self, source_type: str, source_ref: str
    ) -> asyncpg.Record | None:
        """Lee el embedding YA indexado de una fuente (E21 Inc.2, búsqueda "por
        radicado" de ``/antecedentes``), para usarlo como PIVOTE de
        ``search`` en vez de re-embeber el texto del radicado desde cero
        (más barato, y este servicio no tiene por qué reconstruir el texto
        fuente fuera de lo que ya indexó).

        ``embedding::text`` produce la MISMA representación textual entre
        corchetes que ``to_pgvector`` (p. ej. ``"[0.1,0.2,...]"``), así el
        literal es reusable tal cual como ``$1::vector`` en ``search``.

        Misma condición que ``search`` (``embedding IS NOT NULL AND NOT
        voided``): un radicado tombstoneado (reclasificado a un nivel para el
        que no hay embedding aún) o anulado nunca sirve de pivote.
        """
        return await self._conn.fetchrow(
            """
            SELECT embedding::text AS embedding_literal, nivel_seguridad
              FROM knowledge_chunk
             WHERE source_type = $1 AND source_ref = $2
               AND embedding IS NOT NULL AND NOT voided
            """,
            source_type,
            source_ref,
        )

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

        ``NOT voided`` excluye además los chunks purgados PERMANENTEMENTE por
        anulación (`void_by_source`) — defensa en profundidad: un chunk voided ya
        queda sin `embedding` (lo excluye la condición anterior), pero esta
        condición lo hace EXPLÍCITO y a prueba de que alguna ruta futura deje un
        `voided=true` con `embedding` no nulo.

        El SELECT incluye `nivel_seguridad` (además de las columnas que ya
        consumían `/search`/`/antecedentes`) para que el endpoint `/rag` (E21
        Inc.3) pueda aplicar la barrera dura `is_generatable` SIN una segunda
        consulta — el pre-filtro ACL de arriba ya limitó el resultado a lo que
        el usuario puede VER; `nivel_seguridad` es lo que decide qué de eso
        puede además SALIR a un proveedor de generación externo. Los llamadores
        que no lo necesitan (`SearchHit`/`Antecedente`) simplemente lo ignoran
        (Pydantic descarta kwargs no declarados por defecto).
        """
        params: list[object] = [embedding_literal]
        conds: list[str] = ["embedding IS NOT NULL", "NOT voided"]

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
                SELECT id, source_type, source_ref, texto, metadata, nivel_seguridad,
                       1 - (embedding <=> $1::vector) AS score
                  FROM knowledge_chunk
                  {where}
                 ORDER BY embedding <=> $1::vector
                 LIMIT ${len(params)}
                """,
                *params,
            )
        )

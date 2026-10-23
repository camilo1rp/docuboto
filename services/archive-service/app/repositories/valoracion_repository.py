# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""SQL crudo de la valoración de la TVD y de los holds `valoracion_pendiente` (C-5 / C7,
ADR-043): tablas `tvd_valoracion` y `expediente_valoracion_hold` (migración 073).

La conexión se usa dentro de la transacción del llamador cuando este abre una; el repositorio
no abre ninguna. Fija el `search_path` en cada método, como `ArchiveRepository` (la conexión de
las rutas viene de `get_db_conn`, sin tenant)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import asyncpg

from app.core.database import tenant_schema

#: «Valorada con disposición S y sin acto de selección»: el hold NO se levanta solo (migración 076).
_ESPERA_SELECCION = "COALESCE(v.disposicion = 'S', FALSE) AND hv.valoracion_id IS NOT NULL AND hv.seleccion IS NULL"


class ValoracionRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}"')

    # -- el acto del Comité -------------------------------------------------

    async def get_valoracion(self, trd_serie_id: uuid.UUID) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            "SELECT * FROM tvd_valoracion WHERE trd_serie_id = $1", trd_serie_id
        )

    async def insert_valoracion(
        self,
        *,
        trd_serie_id: uuid.UUID,
        disposicion: str,
        acta_comite: str,
        fecha_acta: date,
        fundamento: str,
        decidida_por: uuid.UUID,
        metodo_muestreo: str | None = None,
    ) -> asyncpg.Record | None:
        """`None` si la versión ya tiene valoración (UNIQUE: una por versión, no se rehace)."""
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            INSERT INTO tvd_valoracion
                (trd_serie_id, disposicion, acta_comite, fecha_acta, fundamento, decidida_por,
                 metodo_muestreo)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (trd_serie_id) DO NOTHING
            RETURNING *
            """,
            trd_serie_id,
            disposicion,
            acta_comite,
            fecha_acta,
            fundamento,
            decidida_por,
            metodo_muestreo,
        )

    async def amarrar_valoracion(
        self, trd_serie_id: uuid.UUID, valoracion_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """Los expedientes con hold pendiente de ESA versión pasan a «valorada» (fill-once: el
        `WHERE valoracion_id IS NULL` y el trigger de la 073). Devuelve sus ids."""
        await self._set_search_path()
        rows = await self._conn.fetch(
            """
            UPDATE expediente_valoracion_hold
               SET valoracion_id = $2, next_attempt_at = NULL
             WHERE trd_serie_id = $1 AND valoracion_id IS NULL
            RETURNING expediente_id
            """,
            trd_serie_id,
            valoracion_id,
        )
        return [r["expediente_id"] for r in rows]

    async def conteos(self, trd_serie_id: uuid.UUID) -> dict[str, int]:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"""
            SELECT COUNT(*) FILTER (WHERE hv.aplicada_at IS NULL AND NOT hv.agotada
                                      AND NOT ({_ESPERA_SELECCION}))             AS pendientes,
                   COUNT(*) FILTER (WHERE hv.aplicada_at IS NOT NULL)            AS aplicadas,
                   COUNT(*) FILTER (WHERE hv.agotada)                            AS agotadas,
                   COUNT(*) FILTER (WHERE hv.aplicada_at IS NULL AND {_ESPERA_SELECCION})
                                                                                 AS seleccion_pendiente
              FROM expediente_valoracion_hold hv
              LEFT JOIN tvd_valoracion v ON v.id = hv.valoracion_id
             WHERE hv.trd_serie_id = $1
            """,
            trd_serie_id,
        )
        return {
            k: int(row[k]) for k in ("pendientes", "aplicadas", "agotadas", "seleccion_pendiente")
        }

    # -- el hold por expediente ----------------------------------------------

    async def get_hold(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            "SELECT * FROM expediente_valoracion_hold WHERE expediente_id = $1", expediente_id
        )

    async def insert_hold(
        self, *, expediente_id: uuid.UUID, hold_id: uuid.UUID, trd_serie_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """Amarra expediente <-> hold <-> versión. Si la versión YA tiene valoración nace
        «valorada» (el job la aplica en cuanto los artefactos estén protegidos). `None` si el
        expediente ya estaba amarrado (idempotente: el primero gana)."""
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            INSERT INTO expediente_valoracion_hold (expediente_id, hold_id, trd_serie_id, valoracion_id)
            VALUES ($1, $2, $3, (SELECT id FROM tvd_valoracion WHERE trd_serie_id = $3))
            ON CONFLICT (expediente_id) DO NOTHING
            RETURNING *
            """,
            expediente_id,
            hold_id,
            trd_serie_id,
        )

    async def list_pendientes_aplicar(self, limit: int) -> list[asyncpg.Record]:
        """Valoradas, sin aplicar, no agotadas y con backoff cumplido (índice parcial de la 073)."""
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                """
                SELECT hv.expediente_id, hv.hold_id, hv.trd_serie_id, hv.valoracion_id,
                       hv.intentos, hv.next_attempt_at
                  FROM expediente_valoracion_hold hv
                  JOIN tvd_valoracion v ON v.id = hv.valoracion_id
                 WHERE hv.valoracion_id IS NOT NULL
                   AND hv.aplicada_at IS NULL
                   AND NOT hv.agotada
                   AND (v.disposicion <> 'S' OR hv.seleccion IS NOT NULL)
                   AND (hv.next_attempt_at IS NULL OR hv.next_attempt_at <= now())
                 ORDER BY hv.next_attempt_at NULLS FIRST, hv.fijado_at
                 LIMIT $1
                """,
                limit,
            )
        )

    async def get_para_aplicar(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            SELECT hv.expediente_id, hv.hold_id, hv.trd_serie_id, hv.valoracion_id, hv.intentos,
                   v.disposicion, v.acta_comite, v.fecha_acta, hv.seleccion,
                   s.code AS serie_code, s.version AS serie_version,
                   s.archivo_gestion_years, s.archivo_central_years,
                   e.code AS expediente_code
              FROM expediente_valoracion_hold hv
              JOIN tvd_valoracion v ON v.id = hv.valoracion_id
              JOIN trd_series s ON s.id = hv.trd_serie_id
              JOIN expedientes e ON e.id = hv.expediente_id
             WHERE hv.expediente_id = $1 AND hv.aplicada_at IS NULL AND NOT hv.agotada
               AND (v.disposicion <> 'S' OR hv.seleccion IS NOT NULL)
            """,
            expediente_id,
        )

    # -- el acto de selección (disposición S) ---------------------------------

    async def listar_seleccion_pendiente(
        self, trd_serie_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Valorados con disposición S que esperan el acto de selección, y su total."""
        await self._set_search_path()
        total = await self._conn.fetchval(
            f"""
            SELECT COUNT(*) FROM expediente_valoracion_hold hv
              JOIN tvd_valoracion v ON v.id = hv.valoracion_id
             WHERE hv.trd_serie_id = $1 AND hv.aplicada_at IS NULL AND {_ESPERA_SELECCION}
            """,
            trd_serie_id,
        )
        rows = await self._conn.fetch(
            f"""
            SELECT hv.expediente_id, e.code AS expediente_code
              FROM expediente_valoracion_hold hv
              JOIN tvd_valoracion v ON v.id = hv.valoracion_id
              JOIN expedientes e ON e.id = hv.expediente_id
             WHERE hv.trd_serie_id = $1 AND hv.aplicada_at IS NULL AND {_ESPERA_SELECCION}
             ORDER BY e.code
             LIMIT $2 OFFSET $3
            """,
            trd_serie_id,
            limit,
            offset,
        )
        return list(rows), int(total)

    async def bloquear_hold_de_serie(
        self, trd_serie_id: uuid.UUID, expediente_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """El amarre del expediente con la valoración de ESA serie, `FOR UPDATE`. Debe correr
        dentro de la transacción del acto. `None` si no pertenece a esa versión."""
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            SELECT hv.expediente_id, hv.valoracion_id, hv.aplicada_at, hv.agotada, hv.seleccion,
                   v.disposicion, e.code AS expediente_code
              FROM expediente_valoracion_hold hv
              LEFT JOIN tvd_valoracion v ON v.id = hv.valoracion_id
              JOIN expedientes e ON e.id = hv.expediente_id
             WHERE hv.trd_serie_id = $1 AND hv.expediente_id = $2
               FOR UPDATE OF hv
            """,
            trd_serie_id,
            expediente_id,
        )

    async def registrar_seleccion(
        self, expediente_id: uuid.UUID, *, accion: str, fundamento: str, actor: uuid.UUID
    ) -> asyncpg.Record | None:
        """Fill-once (`seleccion IS NULL` en el `WHERE` y el trigger de la 076). Reabre el
        reintento: el backoff y el agotamiento de antes de la selección no existían."""
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            UPDATE expediente_valoracion_hold
               SET seleccion = $2, seleccion_fundamento = $3, seleccion_por = $4,
                   seleccion_at = now(), next_attempt_at = NULL
             WHERE expediente_id = $1 AND seleccion IS NULL AND aplicada_at IS NULL
            RETURNING *
            """,
            expediente_id,
            accion,
            fundamento,
            actor,
        )

    async def artefactos_pendientes(self, expediente_id: uuid.UUID) -> bool:
        """¿Queda un artefacto de CONTENIDO (índice firmado, AIP) por proteger? Mientras sí, la
        valoración NO se aplica: fijar la retención sobre «lo que hay» y levantar el hold dejaría
        sin protección al artefacto que nace después."""
        await self._set_search_path()
        return bool(
            await self._conn.fetchval(
                """
                SELECT EXISTS (SELECT 1 FROM expediente_indice
                                WHERE expediente_id = $1 AND estado = 'firmado'
                                  AND worm_protegido_at IS NULL AND NOT worm_reconcile_agotado)
                    OR EXISTS (SELECT 1 FROM expediente_aip
                                WHERE expediente_id = $1 AND estado = 'pendiente')
                """,
                expediente_id,
            )
        )

    async def marcar_aplicada(
        self, expediente_id: uuid.UUID, resultado: str
    ) -> asyncpg.Record | None:
        """Fill-once: `None` si otra instancia ya la aplicó (no se escribe asiento fantasma)."""
        await self._set_search_path()
        return await self._conn.fetchrow(
            """
            UPDATE expediente_valoracion_hold
               SET aplicada_at = now(), resultado = $2, last_error = NULL, next_attempt_at = NULL
             WHERE expediente_id = $1 AND aplicada_at IS NULL
            RETURNING *
            """,
            expediente_id,
            resultado,
        )

    async def bump_intento(
        self,
        expediente_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotada: bool,
    ) -> None:
        await self._set_search_path()
        await self._conn.execute(
            """
            UPDATE expediente_valoracion_hold
               SET intentos = intentos + 1, next_attempt_at = $2, last_error = $3, agotada = $4
             WHERE expediente_id = $1 AND aplicada_at IS NULL
            """,
            expediente_id,
            proximo_at,
            ultimo_error,
            agotada,
        )

    async def propagar_espejo_retencion(
        self, expediente_id: uuid.UUID, retain_until: datetime
    ) -> None:
        """Tras fijar la retención CT, los espejos locales (índice y AIP) que nacieron con NULL
        pasan a llevar la fecha: la renovación (fase 5) solo selecciona filas con fecha."""
        await self._set_search_path()
        await self._conn.execute(
            "UPDATE expediente_indice SET worm_retain_until = $2 "
            "WHERE expediente_id = $1 AND worm_protegido_at IS NOT NULL AND worm_retain_until IS NULL",
            expediente_id,
            retain_until,
        )
        await self._conn.execute(
            "UPDATE expediente_aip SET worm_retain_until = $2 "
            "WHERE expediente_id = $1 AND estado = 'empaquetado' AND worm_retain_until IS NULL",
            expediente_id,
            retain_until,
        )

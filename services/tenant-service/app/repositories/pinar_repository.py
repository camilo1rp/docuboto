# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos del PINAR (RF-ADM-08, E14). SQL crudo; search_path fijado al
schema del tenant por get_tenant_conn. Sin ORM (ADR-003)."""

import json
import uuid
from datetime import date
from typing import Any

import asyncpg

_PLAN_COLS = (
    "id, version, vigencia_desde, vigencia_hasta, vision, acto_administrativo, "
    "estado, aprobado_por, aprobado_at, created_at"
)
_PROY_COLS = (
    "id, plan_id, objetivo_id, nombre, meta, indicador, responsable, recurso, "
    "fecha_inicio, fecha_fin, estado, avance_pct"
)


class PinarRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # --- auditoría de dominio (admin_audit, E14) ---------------------------
    async def audit(
        self,
        *,
        entity: str,
        entity_id: str | None,
        action: str,
        old_value: Any = None,
        new_value: Any = None,
        actor_id: uuid.UUID | None,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO admin_audit (entity, entity_id, action, old_value, new_value, actor_id)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            """,
            entity,
            entity_id,
            action,
            json.dumps(old_value) if old_value is not None else None,
            json.dumps(new_value) if new_value is not None else None,
            actor_id,
        )

    # --- plan --------------------------------------------------------------
    async def max_version(self) -> int:
        return await self._conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM pinar_plan")

    async def create_plan(
        self,
        *,
        version: int,
        vision: str | None,
        vigencia_desde: date | None,
        vigencia_hasta: date | None,
        acto_administrativo: str | None,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            f"""
            INSERT INTO pinar_plan
                (version, vision, vigencia_desde, vigencia_hasta, acto_administrativo)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING {_PLAN_COLS}
            """,
            version,
            vision,
            vigencia_desde,
            vigencia_hasta,
            acto_administrativo,
        )

    async def get_plan(self, plan_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_PLAN_COLS} FROM pinar_plan WHERE id = $1", plan_id
        )

    async def list_plans(self, estado: str | None) -> list[asyncpg.Record]:
        if estado is not None:
            return await self._conn.fetch(
                f"SELECT {_PLAN_COLS} FROM pinar_plan WHERE estado = $1 ORDER BY version DESC",
                estado,
            )
        return await self._conn.fetch(
            f"SELECT {_PLAN_COLS} FROM pinar_plan ORDER BY version DESC"
        )

    async def update_plan_fields(
        self, plan_id: uuid.UUID, fields: dict[str, Any]
    ) -> asyncpg.Record | None:
        allowed = {"vision", "vigencia_desde", "vigencia_hasta"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return await self.get_plan(plan_id)
        cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
        return await self._conn.fetchrow(
            f"UPDATE pinar_plan SET {cols} WHERE id = $1 RETURNING {_PLAN_COLS}",
            plan_id,
            *sets.values(),
        )

    async def set_estado(
        self,
        plan_id: uuid.UUID,
        *,
        nuevo: str,
        expected: str,
        acto_administrativo: str | None = None,
        aprobado_por: uuid.UUID | None = None,
    ) -> asyncpg.Record | None:
        """Transición guardada (WHERE estado = expected) — evita TOCTOU.

        `$6` (marcar aprobado_at) va como booleano aparte en vez de reutilizar `$3`
        en una comparación con literal texto — así asyncpg deduce un único tipo para
        `$3` (varchar de la columna `estado`) y no un `AmbiguousParameterError`."""
        return await self._conn.fetchrow(
            f"""
            UPDATE pinar_plan
               SET estado = $3,
                   acto_administrativo = COALESCE($4, acto_administrativo),
                   aprobado_por = COALESCE($5, aprobado_por),
                   aprobado_at = CASE WHEN $6 THEN now() ELSE aprobado_at END
             WHERE id = $1 AND estado = $2
            RETURNING {_PLAN_COLS}
            """,
            plan_id,
            expected,
            nuevo,
            acto_administrativo,
            aprobado_por,
            nuevo == "aprobado",
        )

    # --- aspectos críticos -------------------------------------------------
    async def create_aspecto(
        self, plan_id: uuid.UUID, *, descripcion: str, riesgo: str | None, orden: int
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            """
            INSERT INTO pinar_aspecto_critico (plan_id, descripcion, riesgo, orden)
            VALUES ($1, $2, $3, $4)
            RETURNING id, plan_id, descripcion, riesgo, orden
            """,
            plan_id,
            descripcion,
            riesgo,
            orden,
        )

    async def list_aspectos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, plan_id, descripcion, riesgo, orden FROM pinar_aspecto_critico "
            "WHERE plan_id = $1 ORDER BY orden, descripcion",
            plan_id,
        )

    async def get_aspecto(self, aspecto_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, plan_id, descripcion, riesgo, orden FROM pinar_aspecto_critico "
            "WHERE id = $1",
            aspecto_id,
        )

    async def update_aspecto(
        self, aspecto_id: uuid.UUID, fields: dict[str, Any]
    ) -> asyncpg.Record | None:
        allowed = {"descripcion", "riesgo", "orden"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return await self.get_aspecto(aspecto_id)
        cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
        return await self._conn.fetchrow(
            f"UPDATE pinar_aspecto_critico SET {cols} WHERE id = $1 "
            "RETURNING id, plan_id, descripcion, riesgo, orden",
            aspecto_id,
            *sets.values(),
        )

    async def delete_aspecto(self, aspecto_id: uuid.UUID) -> bool:
        return (
            await self._conn.execute("DELETE FROM pinar_aspecto_critico WHERE id = $1", aspecto_id)
        ).endswith("1")

    # --- ejes + priorización ----------------------------------------------
    async def list_ejes(self) -> list[asyncpg.Record]:
        return await self._conn.fetch("SELECT code, nombre FROM pinar_eje ORDER BY code")

    async def replace_priorizacion(
        self, aspecto_id: uuid.UUID, celdas: list[tuple[int, int]]
    ) -> None:
        """Reemplaza la fila de priorización del aspecto (matriz aspecto × eje)."""
        async with self._conn.transaction():
            await self._conn.execute(
                "DELETE FROM pinar_priorizacion WHERE aspecto_id = $1", aspecto_id
            )
            for eje_code, impacto in celdas:
                await self._conn.execute(
                    "INSERT INTO pinar_priorizacion (aspecto_id, eje_code, impacto) "
                    "VALUES ($1, $2, $3)",
                    aspecto_id,
                    eje_code,
                    impacto,
                )

    async def priorizacion_del_plan(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        """Cada aspecto del plan con su prioridad (Σ impactos) y el detalle por eje,
        ordenado por prioridad descendente (define el mapa de ruta)."""
        return await self._conn.fetch(
            """
            SELECT a.id AS aspecto_id,
                   a.descripcion,
                   COALESCE(SUM(p.impacto), 0)::int AS prioridad,
                   COALESCE(
                       jsonb_object_agg(p.eje_code, p.impacto)
                           FILTER (WHERE p.eje_code IS NOT NULL),
                       '{}'::jsonb
                   ) AS impactos
              FROM pinar_aspecto_critico a
              LEFT JOIN pinar_priorizacion p ON p.aspecto_id = a.id
             WHERE a.plan_id = $1
             GROUP BY a.id, a.descripcion
             ORDER BY prioridad DESC, a.descripcion
            """,
            plan_id,
        )

    # --- objetivos ---------------------------------------------------------
    async def create_objetivo(
        self, plan_id: uuid.UUID, *, descripcion: str, orden: int
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            "INSERT INTO pinar_objetivo (plan_id, descripcion, orden) VALUES ($1, $2, $3) "
            "RETURNING id, plan_id, descripcion, orden",
            plan_id,
            descripcion,
            orden,
        )

    async def list_objetivos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, plan_id, descripcion, orden FROM pinar_objetivo "
            "WHERE plan_id = $1 ORDER BY orden, descripcion",
            plan_id,
        )

    async def get_objetivo(self, objetivo_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, plan_id, descripcion, orden FROM pinar_objetivo WHERE id = $1",
            objetivo_id,
        )

    async def update_objetivo(
        self, objetivo_id: uuid.UUID, fields: dict[str, Any]
    ) -> asyncpg.Record | None:
        allowed = {"descripcion", "orden"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return await self.get_objetivo(objetivo_id)
        cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
        return await self._conn.fetchrow(
            f"UPDATE pinar_objetivo SET {cols} WHERE id = $1 "
            "RETURNING id, plan_id, descripcion, orden",
            objetivo_id,
            *sets.values(),
        )

    async def delete_objetivo(self, objetivo_id: uuid.UUID) -> bool:
        return (
            await self._conn.execute("DELETE FROM pinar_objetivo WHERE id = $1", objetivo_id)
        ).endswith("1")

    # --- proyectos ---------------------------------------------------------
    async def create_proyecto(self, plan_id: uuid.UUID, data: dict[str, Any]) -> asyncpg.Record:
        return await self._conn.fetchrow(
            f"""
            INSERT INTO pinar_proyecto
                (plan_id, objetivo_id, nombre, meta, indicador, responsable, recurso,
                 fecha_inicio, fecha_fin)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING {_PROY_COLS}
            """,
            plan_id,
            data.get("objetivo_id"),
            data["nombre"],
            data.get("meta"),
            data.get("indicador"),
            data.get("responsable"),
            data.get("recurso"),
            data.get("fecha_inicio"),
            data.get("fecha_fin"),
        )

    async def list_proyectos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            f"SELECT {_PROY_COLS} FROM pinar_proyecto WHERE plan_id = $1 ORDER BY fecha_inicio "
            "NULLS LAST, nombre",
            plan_id,
        )

    async def get_proyecto(self, proyecto_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_PROY_COLS} FROM pinar_proyecto WHERE id = $1", proyecto_id
        )

    async def update_proyecto(
        self, proyecto_id: uuid.UUID, fields: dict[str, Any]
    ) -> asyncpg.Record | None:
        allowed = {
            "objetivo_id", "nombre", "meta", "indicador", "responsable",
            "recurso", "fecha_inicio", "fecha_fin", "estado",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return await self.get_proyecto(proyecto_id)
        cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
        return await self._conn.fetchrow(
            f"UPDATE pinar_proyecto SET {cols} WHERE id = $1 RETURNING {_PROY_COLS}",
            proyecto_id,
            *sets.values(),
        )

    async def delete_proyecto(self, proyecto_id: uuid.UUID) -> bool:
        return (
            await self._conn.execute("DELETE FROM pinar_proyecto WHERE id = $1", proyecto_id)
        ).endswith("1")

    async def replace_proyecto_aspectos(
        self, proyecto_id: uuid.UUID, aspecto_ids: list[uuid.UUID]
    ) -> None:
        async with self._conn.transaction():
            await self._conn.execute(
                "DELETE FROM pinar_proyecto_aspecto WHERE proyecto_id = $1", proyecto_id
            )
            for aspecto_id in aspecto_ids:
                await self._conn.execute(
                    "INSERT INTO pinar_proyecto_aspecto (proyecto_id, aspecto_id) "
                    "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    proyecto_id,
                    aspecto_id,
                )

    async def list_proyecto_aspectos(self, proyecto_id: uuid.UUID) -> list[uuid.UUID]:
        rows = await self._conn.fetch(
            "SELECT aspecto_id FROM pinar_proyecto_aspecto WHERE proyecto_id = $1", proyecto_id
        )
        return [r["aspecto_id"] for r in rows]

    # --- seguimiento (append-only) ----------------------------------------
    async def add_seguimiento(
        self,
        proyecto_id: uuid.UUID,
        *,
        fecha: date,
        avance_pct: int,
        observacion: str | None,
        registrado_por: uuid.UUID | None,
    ) -> asyncpg.Record:
        """Inserta el registro append-only y PROYECTA el avance del proyecto desde
        el último seguimiento (invariante 3), en la misma transacción."""
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                """
                INSERT INTO pinar_seguimiento
                    (proyecto_id, fecha, avance_pct, observacion, registrado_por)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, proyecto_id, fecha, avance_pct, observacion,
                          registrado_por, created_at
                """,
                proyecto_id,
                fecha,
                avance_pct,
                observacion,
                registrado_por,
            )
            await self._conn.execute(
                "UPDATE pinar_proyecto SET avance_pct = $2 WHERE id = $1",
                proyecto_id,
                avance_pct,
            )
            return row

    async def list_seguimiento(self, proyecto_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, proyecto_id, fecha, avance_pct, observacion, registrado_por, created_at "
            "FROM pinar_seguimiento WHERE proyecto_id = $1 ORDER BY created_at",
            proyecto_id,
        )

    # --- instrumentos (articulación por referencia) -----------------------
    async def create_instrumento(
        self, plan_id: uuid.UUID, *, tipo: str, ref: str, descripcion: str | None
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            "INSERT INTO pinar_instrumento_ref (plan_id, tipo, ref, descripcion) "
            "VALUES ($1, $2, $3, $4) RETURNING id, plan_id, tipo, ref, descripcion",
            plan_id,
            tipo,
            ref,
            descripcion,
        )

    async def list_instrumentos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, plan_id, tipo, ref, descripcion FROM pinar_instrumento_ref "
            "WHERE plan_id = $1 ORDER BY tipo, ref",
            plan_id,
        )

    async def delete_instrumento(self, instrumento_id: uuid.UUID) -> bool:
        return (
            await self._conn.execute(
                "DELETE FROM pinar_instrumento_ref WHERE id = $1", instrumento_id
            )
        ).endswith("1")

    # --- tableros / mapa de ruta ------------------------------------------
    async def tablero_por_objetivo(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            """
            SELECT o.id AS objetivo_id, o.descripcion,
                   COALESCE(AVG(pr.avance_pct), 0)::float AS avance_promedio,
                   COUNT(pr.id)::int AS proyectos
              FROM pinar_objetivo o
              LEFT JOIN pinar_proyecto pr ON pr.objetivo_id = o.id
             WHERE o.plan_id = $1
             GROUP BY o.id, o.descripcion
             ORDER BY o.orden, o.descripcion
            """,
            plan_id,
        )

    async def tablero_por_eje(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        """Avance por eje: promedio del avance de los proyectos que mitigan aspectos
        priorizados sobre ese eje (aspecto → priorización → eje)."""
        return await self._conn.fetch(
            """
            SELECT e.code AS eje_code, e.nombre,
                   COALESCE(AVG(pr.avance_pct), 0)::float AS avance_promedio,
                   COUNT(DISTINCT pr.id)::int AS proyectos
              FROM pinar_eje e
              LEFT JOIN pinar_priorizacion p ON p.eje_code = e.code
              LEFT JOIN pinar_aspecto_critico a
                     ON a.id = p.aspecto_id AND a.plan_id = $1
              LEFT JOIN pinar_proyecto_aspecto pa ON pa.aspecto_id = a.id
              LEFT JOIN pinar_proyecto pr ON pr.id = pa.proyecto_id
             GROUP BY e.code, e.nombre
             ORDER BY e.code
            """,
            plan_id,
        )

    async def mapa_ruta(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            f"SELECT {_PROY_COLS} FROM pinar_proyecto WHERE plan_id = $1 "
            "ORDER BY fecha_inicio NULLS LAST, fecha_fin NULLS LAST, nombre",
            plan_id,
        )

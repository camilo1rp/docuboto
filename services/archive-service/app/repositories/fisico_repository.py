# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio del archivo físico (E17, ADR-017).

Topología recursiva de ubicaciones (ruta vía CTE recursivo), unidades de
conservación y vínculo N:M expediente↔unidad. search_path al tenant por request.
"""

from __future__ import annotations

import uuid

import asyncpg

_UB_COLS = "id, parent_id, nivel, codigo, nombre, tipo_archivo, capacidad_ml, " "activo, created_at"
_UN_COLS = (
    "id, parent_id, ubicacion_id, tipo, codigo, signatura, soporte, "
    "num_folios, activo, created_at"
)


class FisicoRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _sp(self) -> None:
        await self._conn.execute(f"SET search_path TO tenant_{self._tenant_slug}, public")

    # -- ubicaciones ---------------------------------------------------

    async def insert_ubicacion(self, data: dict) -> asyncpg.Record:
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO ubicacion ({', '.join(cols)}) VALUES ({ph}) " f"RETURNING {_UB_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def get_ubicacion(self, ubicacion_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_UB_COLS} FROM ubicacion WHERE id = $1", ubicacion_id
        )

    async def list_ubicaciones(
        self, *, parent_id: uuid.UUID | None, activo: bool | None
    ) -> list[asyncpg.Record]:
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if parent_id is not None:
            params.append(parent_id)
            conds.append(f"parent_id = ${len(params)}")
        if activo is not None:
            params.append(activo)
            conds.append(f"activo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {_UB_COLS} FROM ubicacion {where} ORDER BY codigo", *params
            )
        )

    async def ubicacion_path(self, ubicacion_id: uuid.UUID) -> list[str]:
        """Códigos de la raíz al nodo (CTE recursivo sobre parent_id)."""
        await self._sp()
        rows = await self._conn.fetch(
            """
            WITH RECURSIVE p AS (
                SELECT id, parent_id, codigo, 0 AS depth FROM ubicacion WHERE id = $1
                UNION ALL
                SELECT u.id, u.parent_id, u.codigo, p.depth + 1
                  FROM ubicacion u JOIN p ON u.id = p.parent_id
            )
            SELECT codigo FROM p ORDER BY depth DESC
            """,
            ubicacion_id,
        )
        return [r["codigo"] for r in rows]

    # -- unidades ------------------------------------------------------

    async def insert_unidad(self, data: dict) -> asyncpg.Record:
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO unidad_conservacion ({', '.join(cols)}) VALUES ({ph}) "
            f"RETURNING {_UN_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def get_unidad(self, unidad_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_UN_COLS} FROM unidad_conservacion WHERE id = $1", unidad_id
        )

    async def list_unidades(
        self, *, ubicacion_id: uuid.UUID | None, tipo: str | None
    ) -> list[asyncpg.Record]:
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if ubicacion_id is not None:
            params.append(ubicacion_id)
            conds.append(f"ubicacion_id = ${len(params)}")
        if tipo is not None:
            params.append(tipo)
            conds.append(f"tipo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {_UN_COLS} FROM unidad_conservacion {where} ORDER BY codigo",
                *params,
            )
        )

    # -- vínculo N:M ---------------------------------------------------

    async def link_expediente(
        self,
        *,
        expediente_id: uuid.UUID,
        unidad_id: uuid.UUID,
        folio_inicio: int | None,
        folio_fin: int | None,
    ) -> None:
        await self._sp()
        await self._conn.execute(
            "INSERT INTO expediente_unidad "
            "(expediente_id, unidad_id, folio_inicio, folio_fin) "
            "VALUES ($1, $2, $3, $4)",
            expediente_id,
            unidad_id,
            folio_inicio,
            folio_fin,
        )

    async def units_of_expediente(self, expediente_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT eu.unidad_id, u.signatura, u.ubicacion_id,
                       eu.folio_inicio, eu.folio_fin
                  FROM expediente_unidad eu
                  JOIN unidad_conservacion u ON u.id = eu.unidad_id
                 WHERE eu.expediente_id = $1
                 ORDER BY u.signatura
                """,
                expediente_id,
            )
        )

    async def expedientes_in_unit(self, unidad_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                "SELECT expediente_id, folio_inicio, folio_fin "
                "FROM expediente_unidad WHERE unidad_id = $1 ORDER BY added_at",
                unidad_id,
            )
        )

    # -- préstamos -----------------------------------------------------

    async def active_loan(self, unidad_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            "SELECT id FROM prestamo WHERE unidad_id = $1 AND estado = 'prestado' " "LIMIT 1",
            unidad_id,
        )

    async def insert_prestamo(
        self,
        *,
        unidad_id: uuid.UUID,
        prestado_a: str,
        prestado_por: str | None,
        fecha_devolucion_esperada,
    ) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            "INSERT INTO prestamo "
            "(unidad_id, prestado_a, prestado_por, fecha_devolucion_esperada) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            unidad_id,
            prestado_a,
            prestado_por,
            fecha_devolucion_esperada,
        )
        assert row is not None
        return row

    async def get_prestamo(self, prestamo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow("SELECT * FROM prestamo WHERE id = $1", prestamo_id)

    async def devolver_prestamo(self, prestamo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            "UPDATE prestamo SET estado = 'devuelto', fecha_devolucion_real = now() "
            "WHERE id = $1 AND estado = 'prestado' RETURNING *",
            prestamo_id,
        )

    async def list_prestamos(
        self, *, estado: str | None, unidad_id: uuid.UUID | None
    ) -> list[asyncpg.Record]:
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if unidad_id is not None:
            params.append(unidad_id)
            conds.append(f"unidad_id = ${len(params)}")
        if estado == "vencido":
            conds.append("estado = 'prestado' AND fecha_devolucion_esperada < CURRENT_DATE")
        elif estado in ("prestado", "devuelto"):
            params.append(estado)
            conds.append(f"estado = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT * FROM prestamo {where} ORDER BY fecha_prestamo DESC", *params
            )
        )

    # -- historial de movimientos (append-only) ------------------------

    async def insert_movimiento(
        self,
        *,
        unidad_id: uuid.UUID,
        accion: str,
        ubicacion_before: uuid.UUID | None,
        ubicacion_after: uuid.UUID | None,
        performed_by: str | None,
    ) -> None:
        await self._sp()
        await self._conn.execute(
            "INSERT INTO movimiento_fisico "
            "(unidad_id, accion, ubicacion_before, ubicacion_after, performed_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            unidad_id,
            accion,
            ubicacion_before,
            ubicacion_after,
            performed_by,
        )

    async def list_movimientos(self, unidad_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                "SELECT id, unidad_id, accion, ubicacion_before, ubicacion_after, "
                "performed_by, ts FROM movimiento_fisico WHERE unidad_id = $1 "
                "ORDER BY ts ASC",
                unidad_id,
            )
        )

    # -- FUID (inventario documental, RF-ARF-10) -----------------------

    async def fuid_inventory(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        expediente_id: uuid.UUID | None = None,
    ) -> list[asyncpg.Record]:
        """Inventario derivado: expediente ↔ unidad ↔ signatura ↔ serie TRD."""
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if ubicacion_id is not None:
            params.append(ubicacion_id)
            conds.append(f"u.ubicacion_id = ${len(params)}")
        if trd_serie_id is not None:
            params.append(trd_serie_id)
            conds.append(f"e.trd_serie_id = ${len(params)}")
        if expediente_id is not None:
            params.append(expediente_id)
            conds.append(f"e.id = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"""
                SELECT e.id AS expediente_id, e.code AS expediente_code,
                       e.name AS asunto, e.opened_at AS fecha_inicial,
                       e.closed_at AS fecha_final,
                       s.code AS serie_code, s.name AS serie_name,
                       u.signatura, u.tipo AS unidad_tipo, u.soporte, u.num_folios,
                       eu.folio_inicio, eu.folio_fin
                  FROM expediente_unidad eu
                  JOIN expedientes e ON e.id = eu.expediente_id
                  JOIN unidad_conservacion u ON u.id = eu.unidad_id
                  LEFT JOIN trd_series s ON s.id = e.trd_serie_id
                  {where}
                 ORDER BY u.signatura NULLS LAST, e.code
                """,
                *params,
            )
        )

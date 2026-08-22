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

from app.core.database import tenant_schema

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
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

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

    async def update_ubicacion(self, ubicacion_id: uuid.UUID, data: dict) -> asyncpg.Record | None:
        """UPDATE parcial: solo las columnas que vienen en `data`.

        `codigo` y `parent_id` no llegan nunca aquí —el esquema de entrada no
        los admite—, así que la identidad de la ubicación y su sitio en el árbol
        quedan fuera del alcance de esta corrección.
        """
        await self._sp()
        cols = list(data.keys())
        asignaciones = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
        return await self._conn.fetchrow(
            f"UPDATE ubicacion SET {asignaciones} WHERE id = $1 RETURNING {_UB_COLS}",
            ubicacion_id,
            *data.values(),
        )

    async def get_ubicacion(self, ubicacion_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_UB_COLS} FROM ubicacion WHERE id = $1", ubicacion_id
        )

    async def list_ubicaciones(
        self, *, parent_id: uuid.UUID | None, activo: bool | None, solo_raiz: bool = False
    ) -> list[asyncpg.Record]:
        """Ubicaciones del tenant.

        `parent_id=None` significa **no filtrar por padre**, que es lo natural
        en un listado — no «las raíces». La diferencia importa: quien pinta el
        primer nivel de un árbol necesita pedir las raíces EXPLÍCITAMENTE con
        `solo_raiz=True`, porque `parent_id IS NULL` no se puede expresar como
        valor de un parámetro. Confundir las dos cosas hacía que la pantalla
        listara el archivo entero —cada nodo también como raíz— y el árbol
        dejaba de ser un árbol.
        """
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if solo_raiz:
            conds.append("parent_id IS NULL")
        elif parent_id is not None:
            params.append(parent_id)
            conds.append(f"parent_id = ${len(params)}")
        if activo is not None:
            params.append(activo)
            conds.append(f"activo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        # La ruta (`SED-001 / DEP-001 / EST-003`) se resuelve para TODAS las filas
        # con un solo CTE, no una consulta por fila: es lo que hace legible una
        # ubicación fuera de su contexto — en un desplegable, un código suelto no
        # dice dónde está.
        return list(
            await self._conn.fetch(
                f"""
                WITH RECURSIVE ancestros AS (
                    -- `::text` no sobra: sin él el término no recursivo tipa la
                    -- columna como varchar(30) y el recursivo la concatena a
                    -- varchar sin límite; Postgres rechaza el CTE entero.
                    SELECT id, parent_id, codigo, codigo::text AS ruta FROM ubicacion
                    UNION ALL
                    SELECT a.id, u.parent_id, a.codigo, u.codigo || ' / ' || a.ruta
                      FROM ancestros a JOIN ubicacion u ON u.id = a.parent_id
                )
                SELECT {_UB_COLS},
                       (SELECT ruta FROM ancestros a
                         WHERE a.id = ubicacion.id AND a.parent_id IS NULL) AS ruta
                  FROM ubicacion {where} ORDER BY codigo
                """,
                *params,
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
                SELECT eu.unidad_id, u.tipo, u.codigo, u.signatura, u.ubicacion_id,
                       eu.folio_inicio, eu.folio_fin
                  FROM expediente_unidad eu
                  JOIN unidad_conservacion u ON u.id = eu.unidad_id
                 WHERE eu.expediente_id = $1
                 ORDER BY u.signatura
                """,
                expediente_id,
            )
        )

    async def expedientes_in_unit(
        self, unidad_id: uuid.UUID, *, max_clearance: int
    ) -> list[asyncpg.Record]:
        """Expedientes contenidos en la unidad, filtrados por fila (RF-SEG-08).

        `max_clearance` es OBLIGATORIO y solo por keyword — mismo patrón
        fail-closed que `fuid_inventory`/`get_expediente_by_id`: sin este
        filtro, un portador de `USUA_PERM_EXPEDIENTE` sin clearance
        suficiente enumeraba (existencia de) expedientes RESERVADA/
        CLASIFICADA vía el inventario físico de la unidad.
        """
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT eu.expediente_id, eu.folio_inicio, eu.folio_fin
                  FROM expediente_unidad eu
                  JOIN expedientes e ON e.id = eu.expediente_id
                 WHERE eu.unidad_id = $1 AND e.nivel_seguridad <= $2
                 ORDER BY eu.added_at
                """,
                unidad_id,
                max_clearance,
            )
        )

    # -- préstamos -----------------------------------------------------

    # Un préstamo se lee para saber QUÉ falta del estante, así que las cuatro
    # rutas devuelven el código y la signatura de la unidad, no solo su UUID.
    # El JOIN obliga a prefijar las columnas de `prestamo` en los WHERE.
    _PRES_SELECT = """
        SELECT p.*, u.codigo AS unidad_codigo, u.signatura AS unidad_signatura
          FROM prestamo p
          JOIN unidad_conservacion u ON u.id = p.unidad_id
    """

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
            """
            WITH nuevo AS (
                INSERT INTO prestamo
                    (unidad_id, prestado_a, prestado_por, fecha_devolucion_esperada)
                VALUES ($1, $2, $3, $4) RETURNING *
            )
            SELECT n.*, u.codigo AS unidad_codigo, u.signatura AS unidad_signatura
              FROM nuevo n JOIN unidad_conservacion u ON u.id = n.unidad_id
            """,
            unidad_id,
            prestado_a,
            prestado_por,
            fecha_devolucion_esperada,
        )
        assert row is not None
        return row

    async def get_prestamo(self, prestamo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(f"{self._PRES_SELECT} WHERE p.id = $1", prestamo_id)

    async def devolver_prestamo(self, prestamo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            """
            WITH devuelto AS (
                UPDATE prestamo SET estado = 'devuelto', fecha_devolucion_real = now()
                 WHERE id = $1 AND estado = 'prestado' RETURNING *
            )
            SELECT d.*, u.codigo AS unidad_codigo, u.signatura AS unidad_signatura
              FROM devuelto d JOIN unidad_conservacion u ON u.id = d.unidad_id
            """,
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
            conds.append(f"p.unidad_id = ${len(params)}")
        if estado == "vencido":
            conds.append("p.estado = 'prestado' AND p.fecha_devolucion_esperada < CURRENT_DATE")
        elif estado in ("prestado", "devuelto"):
            params.append(estado)
            conds.append(f"p.estado = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"{self._PRES_SELECT} {where} ORDER BY p.fecha_prestamo DESC", *params
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

    async def entidad_productora(self) -> str:
        """Nombre oficial de la entidad, para la cabecera del FUID (11.11).

        Se resuelve aquí y no se acepta del cliente: encabeza un documento
        archivístico oficial, y un campo libre convertiría el inventario de una
        entidad en el de cualquier otra con solo cambiar un parámetro.
        """
        nombre = await self._conn.fetchval(
            "SELECT name FROM public.tenants WHERE slug = $1", self._tenant_slug
        )
        # Se exige que sea texto no vacío, no solo verdadero: esto encabeza un
        # documento archivístico oficial, y un registro de tenant incompleto
        # debe degradar al slug —que al menos identifica a la entidad— y no
        # colar en la cabecera lo que sea que haya devuelto la consulta.
        return nombre if isinstance(nombre, str) and nombre.strip() else self._tenant_slug

    async def fuid_inventory(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        max_clearance: int,
        expediente_id: uuid.UUID | None = None,
    ) -> list[asyncpg.Record]:
        """Inventario derivado: expediente ↔ unidad ↔ signatura ↔ serie TRD.

        `max_clearance` es OBLIGATORIO y solo por keyword — mismo patrón que
        `ArchiveRepository.get_expediente_by_id` (RF-SEG-08 / RF-BUS-04): el
        FUID expone `asunto`/`serie`/`signatura` del expediente, que es
        CONTENIDO, no metadato de custodia. Sin este filtro, un portador de
        `USUA_PERM_EXPEDIENTE` sin clearance suficiente enumeraba expedientes
        RESERVADA/CLASIFICADA vía el inventario físico.
        """
        await self._sp()
        conds: list[str] = ["e.nivel_seguridad <= $1"]
        params: list[object] = [max_clearance]
        if ubicacion_id is not None:
            params.append(ubicacion_id)
            conds.append(f"u.ubicacion_id = ${len(params)}")
        if trd_serie_id is not None:
            params.append(trd_serie_id)
            conds.append(f"e.trd_serie_id = ${len(params)}")
        if expediente_id is not None:
            params.append(expediente_id)
            conds.append(f"e.id = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}"
        params.append(self._tenant_slug)
        tenant_pos = len(params)
        return list(
            await self._conn.fetch(
                f"""
                SELECT e.id AS expediente_id, e.code AS expediente_code,
                       e.name AS asunto, e.opened_at AS fecha_inicial,
                       e.closed_at AS fecha_final,
                       s.code AS serie_code, s.name AS serie_name,
                       u.signatura, u.tipo AS unidad_tipo, u.codigo AS unidad_codigo,
                       u.soporte, u.num_folios,
                       eu.folio_inicio, eu.folio_fin,
                       CASE
                           WHEN COALESCE(c.consultas, 0) >= 12 THEN 'alta'
                           WHEN COALESCE(c.consultas, 0) >= 3  THEN 'media'
                           ELSE 'baja'
                       END AS frecuencia_consulta
                  FROM expediente_unidad eu
                  JOIN expedientes e ON e.id = eu.expediente_id
                  JOIN unidad_conservacion u ON u.id = eu.unidad_id
                  LEFT JOIN trd_series s ON s.id = e.trd_serie_id
                  -- Columna 9 del FUID (Ac. AGN 042/2002): frecuencia de
                  -- consulta REAL, no estimada. Sale de la pista de acceso que
                  -- el servicio ya escribe (`archive.expediente_consultado`).
                  --
                  -- El filtro por `tenant_slug` NO sobra aunque el resto de la
                  -- consulta viva en el schema del tenant: `audit_log` está en
                  -- `public` y es la ÚNICA tabla compartida entre entidades.
                  -- Sin él, dos entidades con códigos de expediente que
                  -- coincidan —y `code` es un consecutivo, así que coinciden—
                  -- se contarían las consultas la una a la otra.
                  LEFT JOIN LATERAL (
                      SELECT count(*) AS consultas
                        FROM public.audit_log al
                       WHERE al.tenant_slug = $%(tenant)d
                         AND al.object_type = 'expediente'
                         AND al.object_ref = e.code
                         AND al.action LIKE 'archive.expediente_consultado%%'
                         AND al.ts >= now() - interval '12 months'
                  ) c ON TRUE
                  {where}
                 ORDER BY u.signatura NULLS LAST, e.code
                """ % {"tenant": tenant_pos},
                *params,
            )
        )

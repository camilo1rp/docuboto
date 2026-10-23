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
        await self._conn.execute(f'SET search_path TO "{schema}"')

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

    async def lock_arbol(self, unidad_id: uuid.UUID) -> None:
        """Bloqueo de transacción por RAÍZ del árbol de unidades (C11).

        Va en una sentencia APARTE de la comprobación que protege: bajo READ
        COMMITTED una subconsulta de la misma sentencia usa la instantánea
        anterior a la espera y no vería el préstamo que se confirmó mientras
        esperaba el bloqueo. Quien presta, vincula, transfiere o propone una
        eliminación lo toma ANTES de leer. Solo válido dentro de una transacción."""
        await self._sp()
        await self._conn.execute("SELECT unidad_lock_arbol($1::uuid)", unidad_id)

    async def link_expediente(
        self,
        *,
        expediente_id: uuid.UUID,
        unidad_id: uuid.UUID,
        folio_inicio: int | None,
        folio_fin: int | None,
    ) -> bool:
        """Vincula SOLO si ni la unidad, ni un ancestro, ni un descendiente
        está prestado: meter un expediente reservado en una caja que tiene
        alguien sin acceso lo entregaría. `False` = la unidad está fuera (0
        filas). El `WHERE` va en el INSERT; el llamador ya tomó `lock_arbol`."""
        await self._sp()
        fila = await self._conn.fetchrow(
            "INSERT INTO expediente_unidad "
            "(expediente_id, unidad_id, folio_inicio, folio_fin) "
            "SELECT $1::uuid, $2::uuid, $3::int, $4::int "
            " WHERE unidad_con_prestamo_en_arbol($2::uuid) IS NULL "
            "RETURNING expediente_id",
            expediente_id,
            unidad_id,
            folio_inicio,
            folio_fin,
        )
        return fila is not None

    async def units_of_expediente(
        self, expediente_id: uuid.UUID, *, lock_share: bool = False
    ) -> list[asyncpg.Record]:
        """Unidades que contienen al expediente. `lock_share=True` (solo válido dentro
        de una transacción) toma `FOR SHARE` sobre los vínculos `expediente_unidad`:
        quien COTEJA contra la tenencia (`TransferenciaService._validar_ubicaciones`)
        necesita que no cambie hasta el commit."""
        await self._sp()
        bloqueo = " FOR SHARE OF eu" if lock_share else ""
        return list(
            await self._conn.fetch(
                """
                SELECT eu.unidad_id, u.tipo, u.codigo, u.signatura, u.ubicacion_id,
                       eu.folio_inicio, eu.folio_fin
                  FROM expediente_unidad eu
                  JOIN unidad_conservacion u ON u.id = eu.unidad_id
                 WHERE eu.expediente_id = $1
                 ORDER BY u.signatura
                """
                + bloqueo,
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
    # rutas devuelven el código y la signatura de la unidad, no solo su UUID. Y
    # QUIÉN lo tiene: el interno se resuelve por JOIN (la traza es el UUID; el
    # `username` puede cambiar), el externo trae su nombre en la fila.
    # El JOIN obliga a prefijar las columnas de `prestamo` en los WHERE.
    _PRES_SELECT = """
        SELECT p.*, u.codigo AS unidad_codigo, u.signatura AS unidad_signatura,
               COALESCE(au.username, p.tercero_nombre) AS prestatario_nombre
          FROM prestamo p
          JOIN unidad_conservacion u ON u.id = p.unidad_id
          LEFT JOIN auth_users au ON au.id = p.prestatario_user_id
    """

    # Cuerpo común a la proyección de un INSERT/UPDATE ... RETURNING envuelto en CTE.
    _PRES_DE_CTE = """
        SELECT n.*, u.codigo AS unidad_codigo, u.signatura AS unidad_signatura,
               COALESCE(au.username, n.tercero_nombre) AS prestatario_nombre
          FROM {cte} n
          JOIN unidad_conservacion u ON u.id = n.unidad_id
          LEFT JOIN auth_users au ON au.id = n.prestatario_user_id
    """

    @staticmethod
    def _contenido_sobre(umbral: str) -> str:
        """`EXISTS` de contenido del árbol de `$1` por encima de `umbral` (SQL).

        El árbol es la unidad y sus descendientes: prestar una caja saca también
        las carpetas que lleva dentro."""
        return (
            "EXISTS (SELECT 1 FROM expediente_unidad eu "
            "JOIN expedientes e ON e.id = eu.expediente_id "
            "WHERE eu.unidad_id IN (SELECT unidad_arbol($1::uuid)) "
            f"AND e.nivel_seguridad > ({umbral}))"
        )

    # Clearance del usuario interno `$3` (misma expresión que
    # `orpycamcp_common.rbac.resolve_caller_clearance`: root = 3, sin
    # membresías = 1). Un `sub` de Keycloak no existe en `auth_users` => NULL.
    _CLEARANCE_DE_USER3 = (
        "SELECT CASE WHEN au.is_root THEN 3 ELSE COALESCE(("
        "SELECT MAX(rc.max_level) FROM auth_memberships m "
        "JOIN role_clearance rc ON rc.group_id = m.group_id "
        "WHERE m.user_id = au.id), 1) END "
        "FROM auth_users au WHERE au.id = $3::uuid AND au.is_active"
    )
    _EN_TRANSFERENCIA = (
        "EXISTS (SELECT 1 FROM transferencias t "
        "JOIN expediente_unidad eu ON eu.expediente_id = t.expediente_id "
        "WHERE t.estado IN ('preparada','enviada') "
        "AND eu.unidad_id IN (SELECT unidad_arbol($1::uuid)))"
    )
    _EN_LOTE = (
        "EXISTS (SELECT 1 FROM eliminacion_lote_items i "
        "JOIN eliminacion_lotes l ON l.id = i.lote_id "
        "JOIN expediente_unidad eu ON eu.expediente_id = i.expediente_id "
        "WHERE l.estado IN ('borrador','publicado','avalado') "
        "AND eu.unidad_id IN (SELECT unidad_arbol($1::uuid)))"
    )

    async def insert_prestamo(
        self,
        *,
        unidad_id: uuid.UUID,
        prestatario_tipo: str,
        prestatario_user_id: uuid.UUID | None,
        tercero: dict[str, str | None],
        prestado_por: uuid.UUID,
        fecha_devolucion_esperada,
        observaciones: str | None,
        max_clearance: int,
    ) -> asyncpg.Record | None:
        """Inserta el préstamo SOLO si TODAS las reglas pasan, en el `WHERE`
        del `INSERT ... SELECT` (nunca en una comprobación previa):

        1. el contenido del árbol no excede el clearance de QUIEN PRESTA
           (RF-SEG-08, AREA-01-13);
        2. interno => `auth_users` activo; y su clearance cubre el contenido;
           externo => todo el contenido es nivel 1 (pública);
        3. ni la unidad, ni un ancestro, ni un descendiente está prestado;
        4. ninguna transferencia `preparada|enviada` ni lote de eliminación vivo
           toca expedientes del árbol.

        El llamador tomó antes `lock_arbol` (sentencia aparte): sin eso el
        `NOT EXISTS` leería una instantánea anterior al bloqueo. 0 filas =>
        usar `diagnosticar_prestamo` SOLO para elegir el mensaje."""
        await self._sp()
        gate_presta = "NOT " + self._contenido_sobre("$14::int")
        umbral_prestatario = (
            f"CASE WHEN $2::text = 'externo' THEN 1 ELSE ({self._CLEARANCE_DE_USER3}) END"
        )
        return await self._conn.fetchrow(
            f"""
            WITH nuevo AS (
                INSERT INTO prestamo
                    (unidad_id, prestatario_tipo, prestatario_user_id,
                     tercero_nombre, tercero_documento_tipo, tercero_documento_numero,
                     tercero_entidad, tercero_cargo, tercero_correo, tercero_telefono,
                     prestado_por, fecha_devolucion_esperada, observaciones)
                SELECT $1::uuid, $2::text, $3::uuid,
                       $4::text, $5::text, $6::text, $7::text, $8::text, $9::text, $10::text,
                       $11::uuid, $12::date, $13::text
                 WHERE {gate_presta}
                   AND ($2::text = 'externo'
                        OR EXISTS (SELECT 1 FROM auth_users WHERE id = $3::uuid AND is_active))
                   AND NOT {self._contenido_sobre(umbral_prestatario)}
                   AND unidad_con_prestamo_en_arbol($1::uuid) IS NULL
                   AND NOT {self._EN_TRANSFERENCIA}
                   AND NOT {self._EN_LOTE}
                RETURNING *
            )
            {self._PRES_DE_CTE.format(cte="nuevo")}
            """,
            unidad_id,
            prestatario_tipo,
            prestatario_user_id,
            tercero.get("nombre"),
            tercero.get("documento_tipo"),
            tercero.get("documento_numero"),
            tercero.get("entidad"),
            tercero.get("cargo"),
            tercero.get("correo"),
            tercero.get("telefono"),
            prestado_por,
            fecha_devolucion_esperada,
            observaciones,
            max_clearance,
        )

    async def diagnosticar_prestamo(
        self,
        *,
        unidad_id: uuid.UUID,
        prestatario_tipo: str,
        prestatario_user_id: uuid.UUID | None,
        max_clearance: int,
    ) -> asyncpg.Record:
        """Por qué el INSERT dio 0 filas. Solo ELIGE EL MENSAJE: la decisión ya la
        tomó el `WHERE` (con el bloqueo de árbol aún tomado, el estado no cambia
        entre las dos sentencias). El orden de lectura lo fija el servicio, de
        modo que quien no alcanza el contenido no aprenda nada de lo demás."""
        await self._sp()
        umbral = (
            "CASE WHEN $2::text = 'externo' THEN 1 "
            f"ELSE COALESCE(({self._CLEARANCE_DE_USER3}), 1) END"
        )
        prestatario_sin_acceso = self._contenido_sobre(umbral)
        presta_sin_acceso = self._contenido_sobre("$4::int")
        row = await self._conn.fetchrow(
            f"""
            SELECT {presta_sin_acceso} AS presta_sin_acceso,
                   ($2::text = 'interno' AND NOT EXISTS (
                        SELECT 1 FROM auth_users WHERE id = $3::uuid AND is_active)
                   ) AS prestatario_inexistente,
                   unidad_con_prestamo_en_arbol($1::uuid) IS NOT NULL AS ya_prestada,
                   {self._EN_TRANSFERENCIA} AS en_transferencia,
                   {self._EN_LOTE} AS en_lote,
                   {prestatario_sin_acceso} AS prestatario_sin_acceso
            """,
            unidad_id,
            prestatario_tipo,
            prestatario_user_id,
            max_clearance,
        )
        assert row is not None
        return row

    async def list_prestatarios_elegibles(
        self,
        *,
        unidad_id: uuid.UUID,
        max_clearance: int,
        q: str | None,
        page: int,
        size: int,
    ) -> tuple[list[asyncpg.Record], int]:
        """Usuarios activos cuyo clearance cubre el árbol: la UI espeja el gate y
        no ofrece a quien el backend rechazará. Si quien consulta NO alcanza el
        contenido, la lista sale vacía (no revela quién lo alcanza). Nunca
        devuelve el nivel."""
        await self._sp()
        rows = await self._conn.fetch(
            """
            WITH nivel AS (
                SELECT COALESCE(MAX(e.nivel_seguridad), 1) AS n
                  FROM expediente_unidad eu
                  JOIN expedientes e ON e.id = eu.expediente_id
                 WHERE eu.unidad_id IN (SELECT unidad_arbol($1::uuid))
            )
            SELECT u.id AS user_id, u.username, count(*) OVER() AS total
              FROM auth_users u, nivel
             WHERE u.is_active
               -- Unidad inexistente: NADA (no el directorio entero con nivel 1). El servicio
               -- ya responde 404; esto es la defensa en profundidad del propio repositorio.
               AND EXISTS (SELECT 1 FROM unidad_conservacion WHERE id = $1::uuid)
               AND nivel.n <= $2::int
               AND (CASE WHEN u.is_root THEN 3 ELSE COALESCE((
                        SELECT MAX(rc.max_level) FROM auth_memberships m
                          JOIN role_clearance rc ON rc.group_id = m.group_id
                         WHERE m.user_id = u.id), 1) END) >= nivel.n
               AND ($3::text IS NULL OR strpos(lower(u.username), lower($3::text)) > 0)
             ORDER BY u.username, u.id
             LIMIT $4 OFFSET $5
            """,
            unidad_id,
            max_clearance,
            q,
            size,
            (page - 1) * size,
        )
        total = int(rows[0]["total"]) if rows else 0
        return list(rows), total

    async def expediente_unidad_prestada(self, expediente_id: uuid.UUID) -> str | None:
        """Código de la unidad PRESTADA que contiene al expediente (o `None`).
        Toma antes el bloqueo de árbol (la función SQL lo hace en orden estable),
        así que solo es válida dentro de la transacción del acto que protege."""
        await self._sp()
        cod = await self._conn.fetchval("SELECT expediente_unidad_prestada($1::uuid)", expediente_id)
        # La función SQL devuelve `text` o NULL: cualquier otra cosa no es un código.
        return cod if isinstance(cod, str) else None

    async def unidades_prestadas_de_expedientes(
        self, expediente_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """`{expediente_id: código de la unidad prestada}` de los que están fuera.
        Admisión 7 del lote de eliminación; ver también `lock_arboles_de_expedientes`."""
        await self._sp()
        rows = await self._conn.fetch(
            """
            SELECT DISTINCT ON (eu.expediente_id) eu.expediente_id,
                   (SELECT codigo FROM unidad_conservacion
                     WHERE id = unidad_con_prestamo_en_arbol(eu.unidad_id)) AS codigo
              FROM expediente_unidad eu
             WHERE eu.expediente_id = ANY($1::uuid[])
               AND unidad_con_prestamo_en_arbol(eu.unidad_id) IS NOT NULL
             ORDER BY eu.expediente_id
            """,
            expediente_ids,
        )
        return {r["expediente_id"]: str(r["codigo"]) for r in rows}

    async def lock_arboles_de_expedientes(self, expediente_ids: list[uuid.UUID]) -> None:
        """Toma, en orden estable (sin interbloqueo), el bloqueo de la raíz de cada
        unidad que contiene a alguno de los expedientes. Dentro de una transacción."""
        await self._sp()
        await self._conn.execute(
            """
            SELECT unidad_lock_arbol(r)
              FROM (SELECT DISTINCT COALESCE(unidad_raiz(eu.unidad_id), eu.unidad_id) AS r
                      FROM expediente_unidad eu
                     WHERE eu.expediente_id = ANY($1::uuid[])
                     ORDER BY 1) x
            """,
            expediente_ids,
        )

    async def get_prestamo(self, prestamo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(f"{self._PRES_SELECT} WHERE p.id = $1", prestamo_id)

    async def devolver_prestamo(
        self, prestamo_id: uuid.UUID, recibido_por: uuid.UUID
    ) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            WITH devuelto AS (
                UPDATE prestamo
                   SET estado = 'devuelto', fecha_devolucion_real = now(),
                       devuelto_recibido_por = $2::uuid
                 WHERE id = $1 AND estado = 'prestado' RETURNING *
            )
            {self._PRES_DE_CTE.format(cte="devuelto")}
            """,
            prestamo_id,
            recibido_por,
        )

    async def list_prestamos(
        self,
        *,
        estado: str | None,
        unidad_id: uuid.UUID | None,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[asyncpg.Record], int]:
        """Préstamos filtrados, y **cuántos hay**.

        Devolvía la tabla entera sin `LIMIT`. Los préstamos activos y los
        vencidos están acotados por la operación —son los que alguien tiene
        encima de la mesa—, pero `devuelto` es el HISTÓRICO: crece con cada
        préstamo que se cierra y no deja de crecer nunca.

        El total se cuenta con el MISMO `WHERE` que las filas. Cualquier
        recuento construido sobre `items.length` sería el tamaño de la ventana
        haciéndose pasar por el de la consulta — y es la ventana la que se quiere
        eliminar.

        Sin `page`/`size` no se pagina: el listado por unidad lo llama así y ahí
        la cota la pone el objeto (una unidad de conservación no acumula miles
        de préstamos).
        """
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if unidad_id is not None:
            params.append(unidad_id)
            conds.append(f"p.unidad_id = ${len(params)}")
        if estado == "vencido":
            # `< CURRENT_DATE`: el préstamo que vence HOY todavía está en plazo.
            # Es la convención de préstamos; la TRD usa la contraria (`<=`) y
            # unificarlas es una decisión de producto que sigue abierta.
            conds.append("p.estado = 'prestado' AND p.fecha_devolucion_esperada < CURRENT_DATE")
        elif estado in ("prestado", "devuelto"):
            params.append(estado)
            conds.append(f"p.estado = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""

        total: int = (
            await self._conn.fetchval(
                f"SELECT COUNT(*) FROM prestamo p "
                f"JOIN unidad_conservacion u ON u.id = p.unidad_id {where}",
                *params,
            )
            or 0
        )

        limites = ""
        if page is not None and size is not None:
            params.append(size)
            params.append((page - 1) * size)
            limites = f"LIMIT ${len(params) - 1} OFFSET ${len(params)}"

        filas = await self._conn.fetch(
            f"{self._PRES_SELECT} {where} "
            # `id` desempata. `fecha_prestamo` es `TIMESTAMPTZ DEFAULT now()`, y
            # `now()` devuelve el instante de INICIO DE LA TRANSACCIÓN: varios
            # préstamos creados en la misma transacción —un lote, un sembrado—
            # comparten timestamp EXACTO. Sin clave única final, paginar sobre
            # ellos repetiría uno y perdería otro, sin síntoma.
            f"ORDER BY p.fecha_prestamo DESC, p.id DESC {limites}",
            *params,
        )
        return list(filas), total

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

    async def fuid_inventory(  # noqa: PLR0913
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        max_clearance: int,
        expediente_id: uuid.UUID | None = None,
        limite: int,
    ) -> list[asyncpg.Record]:
        """Inventario derivado: expediente ↔ unidad ↔ signatura ↔ serie TRD.

        `limite` es OBLIGATORIO y sin valor por omisión, por la misma razón que
        `max_clearance`: un tope con default es un tope que se olvida, y el
        olvido aquí no falla — devuelve todo el archivo.

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

        # Tope de filas. El inventario no paginaba ni acotaba: sobre un archivo
        # real son decenas de miles de expedientes, cada uno con su recuento de
        # consultas resuelto por una subconsulta correlacionada sobre el registro
        # de auditoría. La variante XML además construye el documento entero en
        # memoria. Una petición barata no puede pedir trabajo sin techo.
        #
        # El tope es alto a propósito: un FUID legítimo es grande, y recortarlo
        # de más produciría un inventario INCOMPLETO con aspecto de correcto, que
        # es peor que negarse. Quien lo supere lo sabrá por `truncado`.
        params.append(limite + 1)
        limite_pos = len(params)

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
                  -- El inventario lo manda el EXPEDIENTE, no la unidad de
                  -- conservación. Mientras la consulta arrancaba en
                  -- `expediente_unidad`, un expediente sin tenencia física
                  -- —el electrónico puro, que `TransferenciaService.create`
                  -- admite explícitamente como «el caso de uso central de un
                  -- DMS moderno»— no producía NINGUNA fila: el acta de
                  -- entrega que se congela al recibir salía con la cabecera
                  -- puesta y cero registros, se le calculaba la huella
                  -- SHA-256, se sellaba en XAdES-T y se presentaba en
                  -- pantalla como «Formato Único de Inventario Documental».
                  -- Un inventario vacío no dice qué se entregó, y el sello
                  -- sobre él es exacto y no prueba nada (Ley 594/2000 arts.
                  -- 15/26; Ac. AGN 001/2024 Anexo FUID).
                  --
                  -- Con LEFT JOIN, filtrar por `u.ubicacion_id` sigue
                  -- comportándose como la unión interna de antes (la
                  -- condición del WHERE descarta las filas sin unidad), así
                  -- que las puertas del archivo físico no cambian.
                  FROM expedientes e
                  LEFT JOIN expediente_unidad eu ON eu.expediente_id = e.id
                  LEFT JOIN unidad_conservacion u ON u.id = eu.unidad_id
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
                 LIMIT $%(limite)d
                """ % {"tenant": tenant_pos, "limite": limite_pos},
                *params,
            )
        )

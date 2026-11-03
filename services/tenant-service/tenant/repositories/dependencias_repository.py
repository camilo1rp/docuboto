# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de dependencias (organigrama) por tenant (E14).

SQL crudo; el search_path lo fija get_tenant_conn al schema del tenant.
La tabla es jerárquica (parent_id autorreferenciado). E01 la consulta por
`codigo` (depe_codi) para numerar radicados por dependencia.
"""

import uuid
from collections.abc import Sequence

import asyncpg


def _ident(nombre: str) -> str:
    """Identificador entrecomillado (comillas internas duplicadas). Solo para nombres
    leídos del catálogo del propio schema, que no admiten `$n`."""
    return '"' + nombre.replace('"', '""') + '"'


_COLS = "id, parent_id, codigo, sigla, nombre, depe_rad_tp2, activo, " "created_at, updated_at"


class DependenciaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def list(
        self, *, activo: bool | None = None, parent_id: uuid.UUID | None = None
    ) -> list[asyncpg.Record]:
        clauses: list[str] = []
        params: list[object] = []
        if activo is not None:
            params.append(activo)
            clauses.append(f"activo = ${len(params)}")
        if parent_id is not None:
            params.append(parent_id)
            clauses.append(f"parent_id = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return await self._conn.fetch(
            f"SELECT {_COLS} FROM dependencias {where} ORDER BY codigo", *params
        )

    async def get(self, dependencia_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM dependencias WHERE id = $1", dependencia_id
        )

    async def get_by_codigo(self, codigo: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM dependencias WHERE codigo = $1", codigo
        )

    async def insert(
        self,
        *,
        codigo: int,
        sigla: str,
        nombre: str,
        parent_id: uuid.UUID | None,
        depe_rad_tp2: bool,
        activo: bool,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            f"""
            INSERT INTO dependencias
                (codigo, sigla, nombre, parent_id, depe_rad_tp2, activo)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_COLS}
            """,
            codigo,
            sigla,
            nombre,
            parent_id,
            depe_rad_tp2,
            activo,
        )

    async def update(
        self,
        dependencia_id: uuid.UUID,
        *,
        fields: dict[str, object],
    ) -> asyncpg.Record | None:
        """Actualiza solo los campos presentes en ``fields``. Devuelve None si no existe."""
        if not fields:
            return await self.get(dependencia_id)
        sets: list[str] = []
        params: list[object] = []
        for col, val in fields.items():
            params.append(val)
            sets.append(f"{col} = ${len(params)}")
        params.append(dependencia_id)
        return await self._conn.fetchrow(
            f"""
            UPDATE dependencias SET {', '.join(sets)}, updated_at = now()
            WHERE id = ${len(params)}
            RETURNING {_COLS}
            """,
            *params,
        )

    async def get_for_update(self, dependencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Código, nombre y estado vigentes, con la fila bloqueada hasta el fin de la tx."""
        return await self._conn.fetchrow(
            "SELECT codigo, nombre, activo FROM dependencias WHERE id = $1 FOR UPDATE",
            dependencia_id,
        )

    async def _columnas_presentes(self, pares: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
        """Pares (tabla, columna) que existen en el schema del tenant.

        Las tablas son de otros servicios y sus migraciones pueden no haber
        corrido todavía en este tenant: consultar una columna ausente rompería
        la petición en vez de contar cero.
        """
        rows = await self._conn.fetch(
            "SELECT c.relname AS table_name, a.attname AS column_name "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
            "AND a.attnum > 0 AND NOT a.attisdropped AND c.relname = ANY($1::text[])",
            sorted({t for t, _ in pares}),
        )
        existentes = {(r["table_name"], r["column_name"]) for r in rows}
        return {par for par in pares if par in existentes}

    # Predicado sobre `workflow_rules.conditions` (jsonb): alguna condición `*_dept_code`
    # cita el código ($1) como valor o dentro de una lista de valores. UNA definición,
    # compartida por `tramites_vivos` (desactivar) y `referencias` (eliminar).
    _CONDITIONS_CITAN_CODIGO = (
        "jsonb_path_exists(conditions, "
        "'$[*] ? (@.field like_regex \"_dept_code$\" && "
        "(@.value == $c || @.value[*] == $c))', "
        "jsonb_build_object('c', $1::int))"
    )

    async def tramites_vivos(self, codigo: int) -> dict[str, int]:
        """Pasos de flujo vivos y reglas que apuntan a la dependencia POR CÓDIGO.

        workflow-service correlaciona `flow_steps.to_dept_code`,
        `workflow_rules.assign_to_dept_code` / `dest_dept_code` y las condiciones
        `*_dept_code` de `workflow_rules.conditions` con `dependencias.codigo`
        (ADR-032). Desactivarla con trámite vivo lo sacaría de la bandeja. Las
        tablas son de otro servicio: se comprueba su presencia.
        """
        pares = (
            ("flow_steps", "to_dept_code"),
            ("workflow_rules", "assign_to_dept_code"),
            ("workflow_rules", "dest_dept_code"),
            ("workflow_rules", "conditions"),
        )
        hay = await self._columnas_presentes(pares)
        pasos = 0
        reglas = 0
        if ("flow_steps", "to_dept_code") in hay:
            pasos = int(
                await self._conn.fetchval(
                    "SELECT count(*) FROM flow_steps "
                    "WHERE to_dept_code = $1 AND status IN ('pending', 'in_progress')",
                    codigo,
                )
                or 0
            )
        clausulas: list[str] = []
        if ("workflow_rules", "assign_to_dept_code") in hay:
            clausulas.append("assign_to_dept_code = $1")
        if ("workflow_rules", "dest_dept_code") in hay:
            clausulas.append("dest_dept_code = $1")
        if ("workflow_rules", "conditions") in hay:
            clausulas.append(self._CONDITIONS_CITAN_CODIGO)
        if clausulas:
            reglas = int(
                await self._conn.fetchval(
                    f"SELECT count(*) FROM workflow_rules WHERE {' OR '.join(clausulas)}",
                    codigo,
                )
                or 0
            )
        return {"pasos_vivos": pasos, "reglas": reglas}

    # Columnas ENTERAS que guardan un código de dependencia. Se descubren por
    # catálogo (no por una lista a mano que se queda corta en silencio cuando otro
    # servicio añade una tabla): nombre `*dept_code`, `*depe_id`, `*depe_codigo`,
    # `*dependencia*_codigo`, `oficina_productora_codigo`, `unidad_administrativa_codigo`.
    # Se excluyen las tablas propias de la dependencia y las de evidencia de los
    # backfills (son copias históricas, no uso vivo).
    _REF_COLUMNAS = (
        r"(dept_code|depe_id|depe_codigo|dependencia\w*_codigo"
        r"|oficina_productora_codigo|unidad_administrativa_codigo)$"
    )
    _REF_EXCLUIDAS = (
        "dependencias",
        "dependencia_codigo_retirado",
        "radicado_ubicacion_recalculo",
        "dependencia_codigo_backfill",
        "dependencia_codigo_backfill_doc",
    )

    async def referencias(self, codigo: int) -> dict[str, int]:
        """Filas, de cualquier servicio, que citan la dependencia por código (>0 solamente).

        Barrido de `pg_catalog` (pg_attribute/pg_class/pg_namespace) del schema del
        tenant sobre tablas base: no falla en tenants a los que aún les faltan las
        migraciones de otro servicio, y recoge las columnas que se añadan mañana.
        Además cuenta el historial de nombres (`dependencia_nombre_historico.codigo`)
        y las condiciones jsonb de `workflow_rules` (citan el código dentro del JSON,
        sin columna entera propia).
        """
        rows = await self._conn.fetch(
            "SELECT c.relname AS table_name, a.attname AS column_name "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
            "  AND a.attnum > 0 AND NOT a.attisdropped "
            "  AND a.atttypid IN ('int2'::regtype, 'int4'::regtype, 'int8'::regtype) "
            "  AND a.attname ~ $1 AND c.relname <> ALL($2::text[]) "
            "ORDER BY c.relname, a.attname",
            self._REF_COLUMNAS,
            list(self._REF_EXCLUIDAS),
        )
        pares = [(r["table_name"], r["column_name"]) for r in rows]
        if ("dependencia_nombre_historico", "codigo") not in pares:
            hay = await self._conn.fetchval(
                "SELECT to_regclass(format('%I.dependencia_nombre_historico', current_schema()))"
            )
            if hay is not None:
                pares.append(("dependencia_nombre_historico", "codigo"))
        cuentas: dict[str, int] = {}
        for tabla, columna in pares:
            # tabla/columna salen del catálogo del propio schema, nunca del cliente.
            n = int(
                await self._conn.fetchval(
                    f"SELECT count(*) FROM {_ident(tabla)} WHERE {_ident(columna)} = $1",  # noqa: S608
                    codigo,
                )
                or 0
            )
            if n:
                cuentas[f"{tabla}.{columna}"] = n
        if ("workflow_rules", "conditions") in await self._columnas_presentes(
            (("workflow_rules", "conditions"),)
        ):
            n = int(
                await self._conn.fetchval(
                    f"SELECT count(*) FROM workflow_rules WHERE {self._CONDITIONS_CITAN_CODIGO}",  # noqa: S608
                    codigo,
                )
                or 0
            )
            if n:
                cuentas["workflow_rules.conditions"] = n
        return cuentas

    async def codigo_retirado(self, codigo: int) -> bool:
        """¿El código perteneció a una dependencia eliminada (o tiene historial huérfano)?"""
        return bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM dependencia_codigo_retirado WHERE codigo = $1) "
                "OR (EXISTS (SELECT 1 FROM dependencia_nombre_historico WHERE codigo = $1) "
                "    AND NOT EXISTS (SELECT 1 FROM dependencias WHERE codigo = $1))",
                codigo,
            )
        )

    async def registrar_retirada(
        self, *, codigo: int, nombre: str, retirado_por: uuid.UUID | None
    ) -> None:
        """Solo inserción: el código eliminado no se reutiliza (misma tx que el DELETE)."""
        await self._conn.execute(
            "INSERT INTO dependencia_codigo_retirado (codigo, nombre, retirado_por) "
            "VALUES ($1, $2, $3)",
            codigo,
            nombre,
            retirado_por,
        )

    async def nombre_de_otra_activa(self, nombre: str, codigo: int) -> bool:
        """¿Otra dependencia ACTIVA lleva hoy ese nombre?"""
        return bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM dependencias "
                "WHERE nombre = $1 AND activo AND codigo <> $2)",
                nombre,
                codigo,
            )
        )

    async def resolucion_actual(self, nombre: str) -> tuple[int | None, int]:
        """(código, candidatos) con que el sistema resuelve hoy `nombre`, incluido el
        historial y los mapeos manuales: misma función SQL que usan los backfills."""
        rows = await self._conn.fetch(
            "SELECT codigo, candidatos FROM resolver_codigo_dependencia($1, true)", nombre
        )
        if not rows:
            return None, 0
        return rows[0]["codigo"], int(rows[0]["candidatos"])

    async def mapeo_hacia_otro_codigo(self, nombre: str, codigo: int) -> bool:
        """¿`nombre` ya tiene un mapeo manual (irreversible) hacia OTRO código?"""
        rows = await self._conn.fetch(
            "SELECT DISTINCT codigo FROM dependencia_nombre_historico "
            "WHERE origen = 'mapeo_manual' AND nombre = $1 AND codigo <> $2",
            nombre,
            codigo,
        )
        return bool(rows)

    async def registrar_nombre_historico(
        self,
        *,
        codigo: int,
        nombre: str,
        origen: str,
        motivo: str | None,
        registrado_por: uuid.UUID | None,
    ) -> None:
        """Solo inserción: el nombre anterior de un renombre o un mapeo manual."""
        await self._conn.execute(
            "INSERT INTO dependencia_nombre_historico "
            "(codigo, nombre, origen, motivo, registrado_por) VALUES ($1, $2, $3, $4, $5)",
            codigo,
            nombre,
            origen,
            motivo,
            registrado_por,
        )

    async def has_children(self, dependencia_id: uuid.UUID) -> bool:
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM dependencias WHERE parent_id = $1 LIMIT 1",
                dependencia_id,
            )
        )

    async def delete(self, dependencia_id: uuid.UUID) -> bool:
        result = await self._conn.execute("DELETE FROM dependencias WHERE id = $1", dependencia_id)
        return result.endswith("1")

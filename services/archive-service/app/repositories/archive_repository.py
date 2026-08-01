# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import asyncpg

from app.core.clearance import resolve_caller_clearance
from app.core.database import tenant_schema
from app.models.archive import Expediente, ExpedienteRadicado, TrdSerie


class ArchiveRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    # ------------------------------------------------------------------
    # Expediente sequences
    # ------------------------------------------------------------------

    async def next_expediente_code(self, year: int) -> str:
        await self._set_search_path()
        # Un ÚNICO INSERT ... ON CONFLICT ... RETURNING (antes: upsert sin
        # RETURNING + SELECT aparte). El read-after-write en dos statements
        # separados dejaba una ventana entre el UPDATE del upsert y el SELECT
        # donde dos creadores concurrentes podían leer el MISMO `last_seq` ->
        # código duplicado `EXP-AAAA-NNNN`. Colapsado a un solo statement
        # (espejo de `BatchArchiveService.process_batch_expedientes`), el
        # row-lock del propio `ON CONFLICT` serializa a los concurrentes; con
        # esto envuelto en la transacción de `create_expediente` el
        # consecutivo queda monótono sin duplicados.
        seq: int = await self._conn.fetchval(
            """
            INSERT INTO expediente_sequences (year, last_seq)
            VALUES ($1, 1)
            ON CONFLICT (year) DO UPDATE SET last_seq = expediente_sequences.last_seq + 1
            RETURNING last_seq
            """,
            year,
        )
        return f"EXP-{year}-{seq:04d}"

    # ------------------------------------------------------------------
    # Expediente CRUD
    # ------------------------------------------------------------------

    async def create_expediente(
        self,
        *,
        code: str,
        name: str,
        description: str | None,
        trd_serie_id: uuid.UUID | None,
        opened_by: uuid.UUID | None,
        metadata: dict,
        metadata_template_id: uuid.UUID | None,
        nivel_seguridad: int = 1,
    ) -> Expediente:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            INSERT INTO expedientes
                (code, name, description, trd_serie_id, opened_by,
                 metadata, metadata_template_id, nivel_seguridad)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
            RETURNING *
            """,
            code,
            name,
            description,
            trd_serie_id,
            opened_by,
            json.dumps(metadata),
            metadata_template_id,
            nivel_seguridad,
        )
        return _row_to_expediente(row)

    async def list_expedientes(
        self,
        *,
        status: str | None,
        offset: int,
        limit: int,
        max_clearance: int,
    ) -> tuple[list[Expediente], int]:
        await self._set_search_path()

        # Control de acceso por clasificación (RF-SEG-08 / RF-BUS-04), no-read-up:
        # solo expedientes cuyo nivel no supere el clearance del llamante. PRIMERA
        # condición — calcado de `search_expedientes` (líneas ~710-712) — para que
        # el filtro entre en `where`, compartido por el COUNT(*) (X-Total-Count) y
        # la query de página. Sin esto, cualquier portador de USUA_PERM_EXPEDIENTE
        # enumeraba expedientes RESERVADA/CLASIFICADA en el listado general.
        conditions = ["nivel_seguridad <= $1"]
        params: list[object] = [max_clearance]
        idx = 2

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM expedientes WHERE {where}", *params
        )

        rows = await self._conn.fetch(
            f"""
            SELECT * FROM expedientes
             WHERE {where}
             ORDER BY opened_at DESC
             LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return [_row_to_expediente(r) for r in rows], total

    async def caller_clearance(self, user_id: uuid.UUID | None) -> int:
        """Clearance del llamante, con el search_path ya fijado al tenant para que el
        resolutor vea las tablas RBAC (RF-SEG-08 / RF-BUS-04)."""
        await self._set_search_path()
        return await resolve_caller_clearance(self._conn, user_id)

    async def list_roles_autorizados(self, nivel_seguridad: int) -> list[asyncpg.Record]:
        """Snapshot VIVO (E15 perfil v4, R4-b) de qué grupos pueden LEER un
        expediente de este `nivel_seguridad` AHORA MISMO — misma condición que
        `require_permission("USUA_PERM_EXPEDIENTE")` + no-read-up: el grupo
        necesita `crud>=1` sobre USUA_PERM_EXPEDIENTE y un `role_clearance.max_level`
        que alcance el nivel del expediente. Deliberadamente NO se firma ni se
        incrusta en el índice — es la matriz RBAC viva, que cambia con el
        tiempo (corrección del arquitecto sobre el residual "control de acceso
        por roles", v3 → v4)."""
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                """
                SELECT g.name AS grupo, rc.max_level AS clearance_max, gp.crud AS crud
                FROM auth_group_permissions gp
                JOIN auth_permissions p ON p.id = gp.permission_id
                JOIN auth_groups g ON g.id = gp.group_id
                JOIN role_clearance rc ON rc.group_id = gp.group_id
                WHERE p.nombre = 'USUA_PERM_EXPEDIENTE'
                  AND gp.crud >= 1
                  AND rc.max_level >= $1
                ORDER BY g.name ASC
                """,
                nivel_seguridad,
            )
        )

    async def insert_expediente_evento(
        self,
        expediente_id: uuid.UUID,
        *,
        numero_expediente: str | None,
        accion: str,
        actor: uuid.UUID | None = None,
        comentario: str | None = None,
        detalles: dict | None = None,
    ) -> None:
        """Inserta un evento en la hoja de ruta append-only del expediente (E02 §9)."""
        await self._set_search_path()
        await self._conn.execute(
            """
            INSERT INTO expediente_eventos
                (expediente_id, numero_expediente, accion, actor, comentario, detalles)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            expediente_id,
            numero_expediente,
            accion,
            actor,
            comentario,
            json.dumps(detalles or {}),
        )

    async def list_expediente_eventos(
        self, expediente_id: uuid.UUID, max_clearance: int = 3
    ) -> list[asyncpg.Record]:
        # Hoja de ruta visible solo si el expediente lo es para el clearance (no read-up,
        # RF-SEG-08); COALESCE a PUBLICA(1) si faltara la fila. max_clearance=3 = sin tope.
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                """
                SELECT id, expediente_id, numero_expediente, accion, actor,
                       comentario, detalles, ts
                FROM expediente_eventos
                WHERE expediente_id = $1
                  AND COALESCE(
                        (SELECT nivel_seguridad FROM expedientes WHERE id = $1), 1
                      ) <= $2
                ORDER BY ts ASC, id ASC
                """,
                expediente_id,
                max_clearance,
            )
        )

    async def get_expediente_by_id(
        self, expediente_id: uuid.UUID, *, max_clearance: int
    ) -> Expediente | None:
        # `max_clearance` es OBLIGATORIO y solo por keyword — a propósito, sin
        # default (RF-SEG-08 / RF-BUS-04): un default "sin restricción" aquí es
        # justo el bug que se está corrigiendo (leak de RESERVADA/CLASIFICADA).
        # El GET de usuario pasa el clearance real del llamante → un expediente
        # por encima devuelve None (el servicio lo traduce a 404, sin revelar su
        # existencia). Los llamantes internos que necesitan "sin restricción" de
        # forma deliberada pasan `core.clearance.CLEARANCE_SIN_RESTRICCION`
        # explícitamente — visible en el código, no oculto en la firma.
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM expedientes WHERE id = $1 AND nivel_seguridad <= $2",
            expediente_id,
            max_clearance,
        )
        if row is None:
            return None
        expediente = _row_to_expediente(row)
        expediente.radicados = await self._get_radicados(expediente_id)
        return expediente

    async def _get_radicados(self, expediente_id: uuid.UUID) -> list[ExpedienteRadicado]:
        """Todos los vínculos del expediente: VINCULADOS y EXCLUIDOS (soft-delete).

        El índice electrónico (E15 perfil v3) necesita ver ambos para poder
        mostrar el hueco trazable de un documento excluido. Los consumidores
        que representan "documentos vigentes" (respuesta del expediente,
        foliado) filtran `estado == 'vinculado'` en `archive_service.py` —
        aquí NO se filtra para no duplicar la query.
        """
        rows = await self._conn.fetch(
            "SELECT * FROM expediente_radicados WHERE expediente_id = $1 "
            "ORDER BY orden_documental ASC NULLS LAST, added_at ASC",
            expediente_id,
        )
        return [_row_to_radicado(r) for r in rows]

    async def update_expediente(
        self,
        expediente_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        trd_serie_id: uuid.UUID | None,
    ) -> tuple[Expediente, dict[str, object]] | None:
        """Actualiza metadatos (name/description/trd_serie_id) — NUNCA `status`/
        `closed_at`: esas columnas las escribe EXCLUSIVAMENTE
        `transition_expediente` (con `expected_status`, H3). Este método era
        antes el segundo escritor no auditado de `status`/`closed_at` — el arma
        que dejaba el PATCH transicionar el ciclo de vida sin las garantías de
        `close_expediente`/`transfer_expediente`; se retiró la capacidad, no solo
        el uso.

        Devuelve `(Expediente, old)`, donde `old` trae los valores PREVIOS
        (`name`/`description`/`trd_serie_id`/`status`) tomados del `FOR UPDATE`
        de la MISMA sentencia — el "before" para la auditoría del llamador debe
        salir de AQUÍ, no de una pre-lectura separada (ventana TOCTOU entre la
        pre-lectura no-read-up del servicio y este UPDATE).
        """
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        row = await self._conn.fetchrow(
            """
            WITH old AS (
                SELECT id, name, description, trd_serie_id, status
                  FROM expedientes
                 WHERE id = $1
                   FOR UPDATE
            )
            UPDATE expedientes e
               SET name = COALESCE($2, e.name),
                   description = COALESCE($3, e.description),
                   trd_serie_id = COALESCE($4, e.trd_serie_id),
                   updated_at = $5
              FROM old
             WHERE e.id = old.id
            RETURNING e.*, old.name AS old_name, old.description AS old_description,
                      old.trd_serie_id AS old_trd_serie_id, old.status AS old_status
            """,
            expediente_id,
            name,
            description,
            trd_serie_id,
            now,
        )
        if row is None:
            return None
        old: dict[str, object] = {
            "name": row["old_name"],
            "description": row["old_description"],
            "trd_serie_id": row["old_trd_serie_id"],
            "status": row["old_status"],
        }
        return _row_to_expediente(row), old

    async def transition_expediente(
        self,
        expediente_id: uuid.UUID,
        *,
        status: str,
        expected_status: str,
        disposition: dict | None = None,
        pin_trd: bool = False,
        new_trd_serie_id: uuid.UUID | None = None,
        new_trd_serie_code: str | None = None,
        trd_revision_requerida: bool = False,
        trd_revision_motivo: str | None = None,
    ) -> Expediente | None:
        """Cambia el estado del ciclo de vida (cierre/transferencia) y, al cerrar,
        materializa la disposición calculada (E02/ADR-015).

        `expected_status` cierra el `WHERE` (`AND status = $N`), que pasa a ser el
        ÚNICO punto serializado por Postgres para decidir la transición (H3): antes
        la guarda de estado (`_assert_transition`/comprobación equivalente) se
        evaluaba FUERA de esta sentencia, dejando una ventana TOCTOU real — dos
        peticiones concurrentes (doble clic, reintento de cliente, dos réplicas del
        gateway) podían leer ambas el mismo `status` origen, pasar la validación, y
        AMBOS `UPDATE` casar el `WHERE` (antes solo por `id`) y commitear: dos
        eventos/asientos de auditoría para una única transición de negocio en la
        hoja de ruta append-only e `audit_log` INDELEBLE. Con la guarda dentro del
        `WHERE`, solo el primer `UPDATE` en llegar casa (`status` ya cambió para el
        segundo) — el `None` que devuelve el perdedor pasa a ser un caso
        SIGNIFICATIVO y alcanzable, no un fail-closed defensivo teórico.

        `pin_trd` (ADR-025 D3, Increment 1): al cerrar, `ArchiveService`
        re-resuelve por `trd_serie_code` la versión VIGENTE (convalidada/
        migrada) y pide congelar `trd_serie_id`/`trd_serie_code` aquí, en la
        MISMA sentencia que la transición — es la ÚLTIMA escritura de esa
        columna en la vida del expediente (el trigger
        `trg_expedientes_trd_pin_immutable` la rechaza después: pasa aquí
        porque `OLD.status` todavía es `'open'`). Cuando `pin_trd=False`
        (transferencia, o cierre sin serie asignada) esas columnas no se
        tocan."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        assignments = ["status = $1", "updated_at = $2"]
        params: list[object] = [status, now]
        idx = 3
        if status == "closed":
            assignments.append(f"closed_at = ${idx}")
            params.append(now)
            idx += 1
        if disposition is not None:
            assignments.append(f"disposition = ${idx}::jsonb")
            params.append(json.dumps(disposition, default=str))
            idx += 1
        if pin_trd:
            assignments.append(f"trd_serie_id = ${idx}")
            params.append(new_trd_serie_id)
            idx += 1
            assignments.append(f"trd_serie_code = ${idx}")
            params.append(new_trd_serie_code)
            idx += 1
            assignments.append(f"trd_revision_requerida = ${idx}")
            params.append(trd_revision_requerida)
            idx += 1
            assignments.append(f"trd_revision_motivo = ${idx}")
            params.append(trd_revision_motivo)
            idx += 1
        id_idx = idx
        params.append(expediente_id)
        status_idx = idx + 1
        params.append(expected_status)
        row = await self._conn.fetchrow(
            f"UPDATE expedientes SET {', '.join(assignments)} "
            f"WHERE id = ${id_idx} AND status = ${status_idx} RETURNING *",
            *params,
        )
        return _row_to_expediente(row) if row is not None else None

    # ------------------------------------------------------------------
    # Expediente-radicado associations
    # ------------------------------------------------------------------

    async def link_radicado(
        self,
        expediente_id: uuid.UUID,
        radicado_id: uuid.UUID,
        tracking_number: str,
        content_hash: str | None = None,
        formato: str | None = None,
        tamano_bytes: int | None = None,
        folios: int | None = None,
        dependencia_productora_codigo: int | None = None,
        dependencia_productora_nombre: str | None = None,
    ) -> ExpedienteRadicado:
        """Vincula un radicado al expediente, asignándole orden_documental ESTABLE.

        El orden se calcula MAX(orden_documental)+1 por expediente de forma
        ATÓMICA: `pg_advisory_xact_lock` serializa dos vínculos concurrentes
        al MISMO expediente (se libera al cerrar la transacción) antes de leer
        el máximo, así nunca colisionan de ordinal. Es inmutable: nunca se
        recalcula por posición ni se reutiliza tras una exclusión (E15 perfil
        v3, RT-15 art. 4.3.2.3).

        Si el radicado YA tenía un vínculo EXCLUIDO (soft-delete) con este
        expediente, re-vincularlo NO reactiva el ordinal anterior — asigna uno
        NUEVO (se trata como una nueva incorporación); el hueco del ordinal
        excluido queda trazado en las versiones ya generadas del índice. La
        fila física es la MISMA (PK compuesta expediente_id+radicado_id), de
        ahí el `ON CONFLICT ... DO UPDATE` en vez de un segundo INSERT.

        Compromiso de trazabilidad conocido (hallazgo Baja, cierre RT-15): el
        `DO UPDATE` sobrescribe `orden_documental` de esa MISMA fila física
        con el nuevo ordinal — el hueco del ordinal EXCLUIDO anterior deja de
        ser visible en el índice VIGENTE/actual (`expediente_radicados` ya no
        lo conserva); solo persiste en las versiones YA GENERADAS del índice
        (`expediente_indice_item`, snapshot inmutable) y en `expediente_eventos`
        / `audit_log`. Es una decisión aceptada del usuario, no un defecto:
        `unlink_radicado` siempre regenera una versión antes de retornar, así
        que el hueco queda congelado en al menos una versión histórica. Si se
        necesitara conservarlo también en el artefacto VIGENTE, habría que
        modelar filas históricas de exclusión — fuera del alcance de este
        incremento.
        """
        await self._set_search_path()
        async with self._conn.transaction():
            await self._conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1::text))", str(expediente_id)
            )
            next_orden: int = await self._conn.fetchval(
                "SELECT COALESCE(MAX(orden_documental), 0) + 1 "
                "FROM expediente_radicados WHERE expediente_id = $1",
                expediente_id,
            )
            row = await self._conn.fetchrow(
                """
                INSERT INTO expediente_radicados
                    (expediente_id, radicado_id, tracking_number, content_hash,
                     formato, tamano_bytes, folios, orden_documental, estado,
                     dependencia_productora_codigo, dependencia_productora_nombre)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'vinculado', $9, $10)
                ON CONFLICT (expediente_id, radicado_id) DO UPDATE SET
                    tracking_number  = EXCLUDED.tracking_number,
                    content_hash     = EXCLUDED.content_hash,
                    formato          = EXCLUDED.formato,
                    tamano_bytes     = EXCLUDED.tamano_bytes,
                    folios           = EXCLUDED.folios,
                    orden_documental = EXCLUDED.orden_documental,
                    estado           = 'vinculado',
                    excluido_at      = NULL,
                    excluido_causal  = NULL,
                    added_at         = now(),
                    dependencia_productora_codigo = EXCLUDED.dependencia_productora_codigo,
                    dependencia_productora_nombre  = EXCLUDED.dependencia_productora_nombre
                 WHERE expediente_radicados.estado = 'excluido'
                RETURNING *
                """,
                expediente_id,
                radicado_id,
                tracking_number,
                content_hash,
                formato,
                tamano_bytes,
                folios,
                next_orden,
                dependencia_productora_codigo,
                dependencia_productora_nombre,
            )
            if row is None:
                # El único camino normal a un conflicto es reactivar un vínculo
                # EXCLUIDO (arriba). Si de todos modos no hay fila (carrera con
                # otro request que ya lo re-vinculó, o ya estaba 'vinculado' —
                # que `radicado_link_exists` debería haber bloqueado con 409
                # antes de llegar aquí), se devuelve el estado actual sin
                # reasignar orden.
                row = await self._conn.fetchrow(
                    "SELECT * FROM expediente_radicados "
                    "WHERE expediente_id = $1 AND radicado_id = $2",
                    expediente_id,
                    radicado_id,
                )
        return _row_to_radicado(row)

    async def radicado_link_exists(self, expediente_id: uuid.UUID, radicado_id: uuid.UUID) -> bool:
        """True solo si hay un vínculo VIGENTE ('vinculado'). Un vínculo EXCLUIDO
        (soft-delete) no bloquea re-vincular — `link_radicado` lo reactiva con un
        orden_documental NUEVO en vez de tratarlo como duplicado."""
        await self._set_search_path()
        val = await self._conn.fetchval(
            """
            SELECT COUNT(*) FROM expediente_radicados
             WHERE expediente_id = $1 AND radicado_id = $2 AND estado = 'vinculado'
            """,
            expediente_id,
            radicado_id,
        )
        return bool(val)

    async def unlink_radicado(
        self,
        expediente_id: uuid.UUID,
        radicado_id: uuid.UUID,
        causal: str | None = None,
    ) -> bool:
        """Excluye el vínculo por SOFT-DELETE (E15 perfil v3, RT-15 art. 4.3.2.3).

        Ya NO borra la fila físicamente: la marca estado='excluido' (+
        excluido_at/excluido_causal) para que el índice electrónico muestre el
        hueco trazable en el orden documental — paridad con el Orfeo legado
        (estado=2, sin DELETE físico). `orden_documental` NUNCA se renumera ni
        se reutiliza. El `WHERE estado = 'vinculado'` hace la operación
        idempotente: excluir dos veces el mismo vínculo devuelve 0 filas la
        segunda vez (el llamador lo traduce a 404, igual que con el DELETE
        físico anterior).
        """
        await self._set_search_path()
        result: str = await self._conn.execute(
            """
            UPDATE expediente_radicados
               SET estado = 'excluido', excluido_at = now(), excluido_causal = $3
             WHERE expediente_id = $1 AND radicado_id = $2 AND estado = 'vinculado'
            """,
            expediente_id,
            radicado_id,
            causal,
        )
        # asyncpg returns "UPDATE <n>" as the command tag
        updated = int(result.split()[-1])
        return updated > 0

    # ------------------------------------------------------------------
    # TRD series CRUD
    # ------------------------------------------------------------------

    async def create_trd_serie(
        self,
        *,
        code: str,
        name: str,
        description: str | None,
        retention_years: int,
        total_retention: int,
        disposition: str,
        parent_id: uuid.UUID | None = None,
        archivo_gestion_years: int | None = None,
        archivo_central_years: int | None = None,
        pdfa_profile: str | None = None,
    ) -> TrdSerie:
        await self._set_search_path()
        # Si no se indican las dos fases, se derivan de los campos previos.
        agest = archivo_gestion_years if archivo_gestion_years is not None else retention_years
        acentral = (
            archivo_central_years
            if archivo_central_years is not None
            else max(total_retention - retention_years, 0)
        )
        row = await self._conn.fetchrow(
            """
            INSERT INTO trd_series (
                code, name, description, retention_years, total_retention,
                disposition, parent_id, archivo_gestion_years, archivo_central_years,
                pdfa_profile
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            code,
            name,
            description,
            retention_years,
            total_retention,
            disposition,
            parent_id,
            agest,
            acentral,
            pdfa_profile,
        )
        return _row_to_trd_serie(row)

    async def list_trd_series(
        self,
        *,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[list[TrdSerie], int]:
        await self._set_search_path()

        conditions = ["1=1"]
        params: list[object] = []
        idx = 1

        if is_active is not None:
            conditions.append(f"is_active = ${idx}")
            params.append(is_active)
            idx += 1

        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM trd_series WHERE {where}", *params
        )

        rows = await self._conn.fetch(
            f"""
            SELECT * FROM trd_series
             WHERE {where}
             ORDER BY code ASC
             LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return [_row_to_trd_serie(r) for r in rows], total

    async def get_trd_serie_by_id(self, serie_id: uuid.UUID) -> TrdSerie | None:
        await self._set_search_path()
        row = await self._conn.fetchrow("SELECT * FROM trd_series WHERE id = $1", serie_id)
        return _row_to_trd_serie(row) if row is not None else None

    async def get_trd_serie_vigente_by_code(self, code: str) -> TrdSerie | None:
        """La versión que gobierna HOY el código de serie (ADR-025 D1/D5):
        `estado IN ('convalidada','migrada') AND valid_to IS NULL`. El índice
        parcial único `ux_trd_series_vigente` garantiza que a lo sumo una fila
        cumple esto por código -- no hay ambigüedad que resolver aquí."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            SELECT * FROM trd_series
             WHERE code = $1 AND estado IN ('convalidada', 'migrada') AND valid_to IS NULL
            """,
            code,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def create_trd_serie_version(
        self,
        *,
        code: str,
        version: int,
        supersede_a: uuid.UUID,
        created_by: uuid.UUID | None,
        name: str,
        description: str | None,
        retention_years: int,
        total_retention: int,
        disposition: str,
        parent_id: uuid.UUID | None,
        archivo_gestion_years: int | None,
        archivo_central_years: int | None,
        pdfa_profile: str | None,
    ) -> TrdSerie:
        """Inserta la versión N+1 como fila NUEVA (ADR-025 D1): nunca un
        `UPDATE` de la fila vigente. `estado` no se especifica -- cae al
        DEFAULT de columna `'borrador'` (037), consistente con `create_trd_serie`."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            INSERT INTO trd_series (
                code, name, description, retention_years, total_retention,
                disposition, parent_id, archivo_gestion_years, archivo_central_years,
                pdfa_profile, version, supersede_a, created_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING *
            """,
            code,
            name,
            description,
            retention_years,
            total_retention,
            disposition,
            parent_id,
            archivo_gestion_years,
            archivo_central_years,
            pdfa_profile,
            version,
            supersede_a,
            created_by,
        )
        return _row_to_trd_serie(row)

    async def update_trd_serie(
        self,
        serie_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        retention_years: int | None,
        total_retention: int | None,
        disposition: str | None,
        pdfa_profile: str | None = None,
    ) -> TrdSerie | None:
        """Edición EN SITIO. Solo llamable por el servicio sobre una fila en
        `borrador` (o, para `pdfa_profile`, en cualquier estado -- ADR-025 D1).
        El trigger `trg_trd_series_append_only` es el cinturón de seguridad
        de motor; este método no repite esa validación, confía en el gate del
        servicio (`ArchiveService.update_trd_serie`) para devolver un 409
        legible en vez de una excepción cruda de Postgres. `is_active` YA NO
        es aceptado aquí (ADR-025 contrato API): la baja de una versión es
        `derogar`, Increment 2 -- ver ADR-025 §"Contratos de API afectados"."""
        await self._set_search_path()

        assignments: list[str] = []
        params: list[object] = []
        idx = 1

        if name is not None:
            assignments.append(f"name = ${idx}")
            params.append(name)
            idx += 1
        if description is not None:
            assignments.append(f"description = ${idx}")
            params.append(description)
            idx += 1
        if retention_years is not None:
            assignments.append(f"retention_years = ${idx}")
            params.append(retention_years)
            idx += 1
        if total_retention is not None:
            assignments.append(f"total_retention = ${idx}")
            params.append(total_retention)
            idx += 1
        if disposition is not None:
            assignments.append(f"disposition = ${idx}")
            params.append(disposition)
            idx += 1
        if pdfa_profile is not None:
            assignments.append(f"pdfa_profile = ${idx}")
            params.append(pdfa_profile)
            idx += 1

        if not assignments:
            return await self.get_trd_serie_by_id(serie_id)

        set_clause = ", ".join(assignments)
        params.append(serie_id)

        row = await self._conn.fetchrow(
            f"""
            UPDATE trd_series
               SET {set_clause}
             WHERE id = ${idx}
            RETURNING *
            """,
            *params,
        )
        return _row_to_trd_serie(row) if row is not None else None

    # ------------------------------------------------------------------
    # Tipos documentales (3.er nivel TRD, RF-MET-07)
    # ------------------------------------------------------------------

    _TD_COLS = (
        "id, trd_serie_id, code, nombre, obligatorio, metadata_template, " "activo, created_at"
    )

    async def create_tipo_documental(self, data: dict) -> asyncpg.Record:
        await self._set_search_path()
        cols = list(data.keys())
        placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO tipos_documentales ({', '.join(cols)}) "
            f"VALUES ({placeholders}) RETURNING {self._TD_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def list_tipos_documentales(
        self, *, trd_serie_id: uuid.UUID | None, activo: bool | None
    ) -> list[asyncpg.Record]:
        await self._set_search_path()
        conds: list[str] = []
        params: list[object] = []
        idx = 1
        if trd_serie_id is not None:
            conds.append(f"trd_serie_id = ${idx}")
            params.append(trd_serie_id)
            idx += 1
        if activo is not None:
            conds.append(f"activo = ${idx}")
            params.append(activo)
            idx += 1
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {self._TD_COLS} FROM tipos_documentales {where} ORDER BY code",
                *params,
            )
        )

    async def get_tipo_documental(self, tipo_id: uuid.UUID) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            f"SELECT {self._TD_COLS} FROM tipos_documentales WHERE id = $1", tipo_id
        )

    async def update_tipo_documental(self, tipo_id: uuid.UUID, data: dict) -> asyncpg.Record | None:
        await self._set_search_path()
        if not data:
            return await self.get_tipo_documental(tipo_id)
        set_clause = ", ".join(f"{c} = ${i}" for i, c in enumerate(data, start=1))
        idx = len(data) + 1
        return await self._conn.fetchrow(
            f"UPDATE tipos_documentales SET {set_clause} "
            f"WHERE id = ${idx} RETURNING {self._TD_COLS}",
            *data.values(),
            tipo_id,
        )

    async def delete_tipo_documental(self, tipo_id: uuid.UUID) -> bool:
        await self._set_search_path()
        result = await self._conn.execute("DELETE FROM tipos_documentales WHERE id = $1", tipo_id)
        return result.endswith("1")

    # ------------------------------------------------------------------
    # Búsqueda full-text de expedientes (E09, ADR-014)
    # ------------------------------------------------------------------

    async def search_expedientes(
        self,
        *,
        q: str | None,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        metadata_filter: dict | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None,
    ) -> tuple[list[asyncpg.Record], int]:
        await self._set_search_path()
        conds: list[str] = []
        params: list[object] = []
        idx = 1
        q_idx: int | None = None

        # Control de acceso por clasificación (RF-SEG-08 / RF-BUS-04): solo expedientes
        # cuyo nivel no supere el clearance del llamante. El search_path ya apunta al
        # tenant, así que el resolutor ve las tablas RBAC. Siempre aplica.
        clearance = await resolve_caller_clearance(self._conn, user_id)
        conds.append(f"nivel_seguridad <= ${idx}")
        params.append(clearance)
        idx += 1

        if q:
            params.append(q)
            q_idx = idx
            idx += 1
            conds.append(
                f"(search_vector @@ websearch_to_tsquery('spanish', ${q_idx}) "
                f"OR code ILIKE '%' || ${q_idx} || '%')"
            )
        if status:
            params.append(status)
            conds.append(f"status = ${idx}")
            idx += 1
        if date_from is not None:
            params.append(date_from)
            conds.append(f"opened_at >= ${idx}")
            idx += 1
        if date_to is not None:
            params.append(date_to)
            conds.append(f"opened_at <= ${idx}")
            idx += 1
        if metadata_filter:
            params.append(json.dumps(metadata_filter))
            conds.append(f"metadata @> ${idx}::jsonb")
            idx += 1

        where = " AND ".join(conds) if conds else "TRUE"
        rank_expr = (
            f"ts_rank(search_vector, websearch_to_tsquery('spanish', ${q_idx}))" if q_idx else "0"
        )
        order_by = "rank DESC, opened_at DESC" if q_idx else "opened_at DESC"

        total: int = (
            await self._conn.fetchval(f"SELECT COUNT(*) FROM expedientes WHERE {where}", *params)
            or 0
        )
        offset = (page - 1) * size
        rows = await self._conn.fetch(
            f"SELECT id, code, name, status, opened_at, {rank_expr} AS rank "
            f"FROM expedientes WHERE {where} ORDER BY {order_by} "
            f"LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            size,
            offset,
        )
        return rows, total


# ------------------------------------------------------------------
# Row mappers
# ------------------------------------------------------------------


def _json_obj(raw: object) -> dict:
    # asyncpg devuelve JSONB como str; toleramos dict por si hay codec.
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _row_to_expediente(row: asyncpg.Record) -> Expediente:
    return Expediente(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row["description"],
        trd_serie_id=row["trd_serie_id"],
        status=row["status"],
        opened_by=row["opened_by"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        updated_at=row["updated_at"],
        metadata=_json_obj(row["metadata"]),
        metadata_template_id=row["metadata_template_id"],
        disposition=(_json_obj(row["disposition"]) if row["disposition"] is not None else None),
        nivel_seguridad=row["nivel_seguridad"],
        trd_serie_code=row["trd_serie_code"],
        trd_revision_requerida=row["trd_revision_requerida"],
        trd_revision_motivo=row["trd_revision_motivo"],
    )


def _row_to_radicado(row: asyncpg.Record) -> ExpedienteRadicado:
    return ExpedienteRadicado(
        expediente_id=row["expediente_id"],
        radicado_id=row["radicado_id"],
        tracking_number=row["tracking_number"],
        content_hash=row["content_hash"],
        added_at=row["added_at"],
        formato=row["formato"],
        tamano_bytes=row["tamano_bytes"],
        folios=row["folios"],
        orden_documental=row["orden_documental"],
        estado=row["estado"],
        excluido_at=row["excluido_at"],
        excluido_causal=row["excluido_causal"],
        dependencia_productora_codigo=row["dependencia_productora_codigo"],
        dependencia_productora_nombre=row["dependencia_productora_nombre"],
    )


def _row_to_trd_serie(row: asyncpg.Record) -> TrdSerie:
    return TrdSerie(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row["description"],
        retention_years=row["retention_years"],
        total_retention=row["total_retention"],
        disposition=row["disposition"],
        is_active=row["is_active"],
        created_at=row["created_at"],
        parent_id=row["parent_id"],
        archivo_gestion_years=row["archivo_gestion_years"],
        archivo_central_years=row["archivo_central_years"],
        version=row["version"],
        valid_from=row["valid_from"],
        pdfa_profile=row["pdfa_profile"],
        estado=row["estado"],
        valid_to=row["valid_to"],
        acto_administrativo=row["acto_administrativo"],
        fecha_aprobacion_comite=row["fecha_aprobacion_comite"],
        fecha_convalidacion=row["fecha_convalidacion"],
        instancia_convalidante=row["instancia_convalidante"],
        rusd_radicado=row["rusd_radicado"],
        fecha_publicacion=row["fecha_publicacion"],
        supersede_a=row["supersede_a"],
        created_by=row["created_by"],
    )

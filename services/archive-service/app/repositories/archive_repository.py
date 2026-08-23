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
from typing import Any

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
        origen: str = "corriente",
        fecha_extrema_inicial: date | None = None,
        fecha_extrema_final: date | None = None,
    ) -> Expediente:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            INSERT INTO expedientes
                (code, name, description, trd_serie_id, opened_by,
                 metadata, metadata_template_id, nivel_seguridad,
                 origen, fecha_extrema_inicial, fecha_extrema_final)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11)
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
            origen,
            fecha_extrema_inicial,
            fecha_extrema_final,
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

    async def set_publicable(
        self,
        expediente_id: uuid.UUID,
        publicable: bool,
        max_clearance: int,
        actor: uuid.UUID | None,
    ) -> asyncpg.Record | None:
        """Marca o retira la publicación de oficio del expediente (Ley 1712 art. 12).

        Espejo de `document_repository.set_publicable`, con sus dos lecciones ya
        incorporadas:

        - El gate va en el `WHERE` del propio `UPDATE`, no en una comprobación
          previa: autorización y mutación son un solo acto, sin ventana TOCTOU.
        - `nivel_seguridad = 1` se exige **solo al publicar**. Publicar algo
          reservado guardaría una afirmación falsa que una futura desclasificación
          convertiría en difusión; retirar no lee de más ni afirma nada, y
          bloquearlo dejaba sin salida justo al expediente reservado que hubiera
          quedado publicado. En la duda, la dirección segura es poder dejar de
          difundir.

        No hay condición de estado: `closed`/`transferred` son estados normales
        del ciclo de vida, no una retirada de la circulación.
        """
        gate_nivel = " AND nivel_seguridad = 1" if publicable else ""
        return await self._conn.fetchrow(
            "UPDATE expedientes SET publicable = $2, "
            "    publicado_at  = CASE WHEN $2 THEN now() ELSE NULL END, "
            "    publicado_por = CASE WHEN $2 THEN $4::uuid ELSE NULL END "
            f"WHERE id = $1 AND nivel_seguridad <= $3{gate_nivel} "
            "RETURNING code, publicable, publicado_at",
            expediente_id,
            publicable,
            max_clearance,
            actor,
        )

    async def update_expediente(
        self,
        expediente_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        trd_serie_id: uuid.UUID | None,
    ) -> tuple[Expediente, dict[str, Any]] | None:
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
        """ADR-026 (Increment 1, Capa 2): lee `v_trd_series`, NUNCA la tabla
        base -- es LA consulta de la clase "filtro olvidado" que motivó el
        diseño de la vista (antes `SELECT * FROM trd_series WHERE 1=1`, sin
        ningún filtro de tipo). El aislamiento entre TRD y TVD deja de
        depender de acordarse de un `WHERE`: es el nombre del objeto
        consultado."""
        return await self._list_instrumento_series("v_trd_series", is_active=is_active, offset=offset, limit=limit)

    async def list_tvd_agrupaciones(
        self,
        *,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[list[TrdSerie], int]:
        """Gemela de `list_trd_series` sobre `v_tvd_agrupaciones` (ADR-026)."""
        return await self._list_instrumento_series(
            "v_tvd_agrupaciones", is_active=is_active, offset=offset, limit=limit
        )

    async def _list_instrumento_series(
        self,
        view: str,
        *,
        is_active: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[list[TrdSerie], int]:
        # `view` NUNCA viene del cliente -- es una constante elegida en este
        # mismo módulo (`"v_trd_series"` / `"v_tvd_agrupaciones"`), así que
        # interpolarla en el nombre del objeto consultado no es una
        # inyección: es exactamente el mecanismo de la Capa 2 (ADR-026 D2).
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
            f"SELECT COUNT(*) FROM {view} WHERE {where}", *params
        )

        rows = await self._conn.fetch(
            f"""
            SELECT * FROM {view}
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

    # ------------------------------------------------------------------
    # TVD agrupaciones (ADR-026, Increment 1): mismo instrumento, discriminador
    # `tipo_instrumento` -- lecturas SIEMPRE por `v_tvd_agrupaciones` (Capa 2).
    # ------------------------------------------------------------------

    async def get_tvd_agrupacion_by_id(self, agrupacion_id: uuid.UUID) -> TrdSerie | None:
        """Gemela de `get_trd_serie_by_id`, pero por la VISTA: un id de TRD
        no "se filtra", directamente no está en `v_tvd_agrupaciones` -- 404,
        no 403 (ADR-026 §"Superficie de API")."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM v_tvd_agrupaciones WHERE id = $1", agrupacion_id
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def get_tvd_agrupacion_vigente_por_code(self, code: str) -> TrdSerie | None:
        """Gemela de `get_trd_serie_vigente_by_code` sobre la vista TVD. En la
        práctica una TVD nunca alcanza `migrada` (D6 no aplica: nace en
        `borrador`) -- se conserva la misma condición por simetría/DRY con el
        circuito compartido, es inocua."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            SELECT * FROM v_tvd_agrupaciones
             WHERE code = $1 AND estado IN ('convalidada', 'migrada') AND valid_to IS NULL
            """,
            code,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def get_tvd_agrupacion_ultima_por_code(self, code: str) -> TrdSerie | None:
        """Gemela de `get_trd_serie_ultima_by_code` (respaldo de
        `create_tvd_agrupacion_version` cuando `code` quedó sin vigente)."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM v_tvd_agrupaciones WHERE code = $1 ORDER BY version DESC LIMIT 1",
            code,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def get_tvd_agrupacion_by_code_version(
        self, code: str, version: int
    ) -> TrdSerie | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM v_tvd_agrupaciones WHERE code = $1 AND version = $2", code, version
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def list_trd_serie_versiones_by_code(self, code: str) -> list[TrdSerie]:
        """`GET /api/v1/trd/{code}/versiones` (ADR-026: gemelo declarado por
        ADR-025 y nunca implementado). Historia completa, cualquier estado,
        más reciente primero."""
        return await self._list_instrumento_versiones_by_code("v_trd_series", code)

    async def list_tvd_agrupacion_versiones_by_code(self, code: str) -> list[TrdSerie]:
        """Gemela de `list_trd_serie_versiones_by_code` sobre la vista TVD."""
        return await self._list_instrumento_versiones_by_code("v_tvd_agrupaciones", code)

    async def _list_instrumento_versiones_by_code(self, view: str, code: str) -> list[TrdSerie]:
        await self._set_search_path()
        rows = await self._conn.fetch(
            f"SELECT * FROM {view} WHERE code = $1 ORDER BY version DESC", code
        )
        return [_row_to_trd_serie(r) for r in rows]

    async def get_ocupante_tipo_por_code(self, code: str) -> str | None:
        """ADR-026 Capa 1: el espacio de `code` es único y COMPARTIDO entre
        TRD y TVD -- antes de reventar con la `UniqueViolationError` cruda de
        `trd_series_code_version_key`, el service necesita saber DE QUÉ TIPO
        es el ocupante para el `409 instrumento_code_en_uso` (cualquier
        versión de `code` sirve, todas comparten tipo por el trigger de
        inmutabilidad de `tipo_instrumento`)."""
        await self._set_search_path()
        return await self._conn.fetchval(
            "SELECT tipo_instrumento FROM trd_series WHERE code = $1 LIMIT 1", code
        )

    async def create_tvd_agrupacion(
        self,
        *,
        code: str,
        name: str,
        description: str | None,
        total_retention: int,
        disposition: str,
        parent_id: uuid.UUID | None,
        fondo_nombre: str,
        fecha_extrema_inicial: date,
        fecha_extrema_final: date,
        justificacion_valoracion: str,
        archivo_central_years: int | None,
        pdfa_profile: str | None,
    ) -> TrdSerie:
        """`POST /api/v1/tvd` (ADR-026, Increment 1). `tipo_instrumento='TVD'`
        fijo -- nunca tomado del payload (fijado por el router/servicio, no
        por el cliente). `archivo_gestion_years` NUNCA se escribe: se deja
        NULL, que satisface `COALESCE(archivo_gestion_years,0)=0`
        (`trd_series_tvd_forma_check`) sin necesitar que el llamante lo
        declare -- la TVD no tiene fase de archivo de gestión, punto."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            INSERT INTO trd_series (
                tipo_instrumento, code, name, description, retention_years,
                total_retention, disposition, parent_id, archivo_central_years,
                pdfa_profile, fondo_nombre, fecha_extrema_inicial,
                fecha_extrema_final, justificacion_valoracion
            )
            VALUES ('TVD', $1, $2, $3, 0, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING *
            """,
            code,
            name,
            description,
            total_retention,
            disposition,
            parent_id,
            archivo_central_years,
            pdfa_profile,
            fondo_nombre,
            fecha_extrema_inicial,
            fecha_extrema_final,
            justificacion_valoracion,
        )
        return _row_to_trd_serie(row)

    async def create_tvd_agrupacion_version(
        self,
        *,
        code: str,
        version: int,
        supersede_a: uuid.UUID,
        created_by: uuid.UUID | None,
        name: str,
        description: str | None,
        total_retention: int,
        disposition: str,
        parent_id: uuid.UUID | None,
        fondo_nombre: str,
        fecha_extrema_inicial: date,
        fecha_extrema_final: date,
        justificacion_valoracion: str,
        archivo_central_years: int | None,
        pdfa_profile: str | None,
    ) -> TrdSerie:
        """Gemela de `create_trd_serie_version` (D1: fila NUEVA, nunca
        `UPDATE` de la vigente). `retention_years=0`/`archivo_gestion_years`
        NULL, mismo motivo que `create_tvd_agrupacion`."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            INSERT INTO trd_series (
                tipo_instrumento, code, name, description, retention_years,
                total_retention, disposition, parent_id, archivo_central_years,
                pdfa_profile, version, supersede_a, created_by, fondo_nombre,
                fecha_extrema_inicial, fecha_extrema_final, justificacion_valoracion
            )
            VALUES ('TVD', $1, $2, $3, 0, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING *
            """,
            code,
            name,
            description,
            total_retention,
            disposition,
            parent_id,
            archivo_central_years,
            pdfa_profile,
            version,
            supersede_a,
            created_by,
            fondo_nombre,
            fecha_extrema_inicial,
            fecha_extrema_final,
            justificacion_valoracion,
        )
        return _row_to_trd_serie(row)

    async def update_tvd_agrupacion(
        self,
        agrupacion_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        total_retention: int | None,
        disposition: str | None,
        pdfa_profile: str | None,
        fondo_nombre: str | None,
        fecha_extrema_inicial: date | None,
        fecha_extrema_final: date | None,
        justificacion_valoracion: str | None,
    ) -> TrdSerie | None:
        """`PATCH /api/v1/tvd/{id}` (ADR-026): gemela de `update_trd_serie`,
        campos propios de TVD además de los compartidos. Igual que la TRD,
        solo llamable por el servicio sobre `borrador` (o `pdfa_profile` en
        cualquier estado) -- el trigger append-only es el cinturón de
        seguridad de motor."""
        await self._set_search_path()

        assignments: list[str] = []
        params: list[object] = []
        idx = 1

        def _add(column: str, value: object) -> None:
            nonlocal idx
            assignments.append(f"{column} = ${idx}")
            params.append(value)
            idx += 1

        if name is not None:
            _add("name", name)
        if description is not None:
            _add("description", description)
        if total_retention is not None:
            _add("total_retention", total_retention)
        if disposition is not None:
            _add("disposition", disposition)
        if pdfa_profile is not None:
            _add("pdfa_profile", pdfa_profile)
        if fondo_nombre is not None:
            _add("fondo_nombre", fondo_nombre)
        if fecha_extrema_inicial is not None:
            _add("fecha_extrema_inicial", fecha_extrema_inicial)
        if fecha_extrema_final is not None:
            _add("fecha_extrema_final", fecha_extrema_final)
        if justificacion_valoracion is not None:
            _add("justificacion_valoracion", justificacion_valoracion)

        if not assignments:
            return await self.get_tvd_agrupacion_by_id(agrupacion_id)

        set_clause = ", ".join(assignments)
        params.append(agrupacion_id)

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

    async def get_trd_serie_ultima_by_code(self, code: str) -> TrdSerie | None:
        """Última versión (por número, CUALQUIER estado) de `code` (ADR-025,
        migración 038 -- hallazgo #3): respaldo de `get_trd_serie_vigente_by_code`
        cuando un código quedó SIN vigente (todas sus versiones `derogada`).
        Sin esta consulta, `create_trd_serie_version` respondería `404`
        permanente y el código de serie quedaría sin ningún camino de vuelta
        -- ni siquiera para abrir un `borrador` nuevo que se pueda convalidar."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM trd_series WHERE code = $1 ORDER BY version DESC LIMIT 1",
            code,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def count_expedientes_open_por_trd_serie_code(self, code: str) -> int:
        """ADR-025, migración 038 -- hallazgo #3: guarda de `derogar` (baja
        explícita SIN sucesora). Un expediente `open` NO está amarrado a una
        fila concreta (D3: `trd_serie_id` FLOTA hasta el cierre) -- lo que
        importa es el CÓDIGO, porque `close_expediente` re-resuelve por
        código en ese instante. Derogar el único código vigente mientras hay
        expedientes abiertos bajo él los dejaría sin ningún instrumento que
        pueda gobernar su cierre.

        `expedientes.trd_serie_code` (denormalización de D3) solo se escribe
        al CERRAR -- mientras `open`, la columna está NULL, así que filtrar
        por ella aquí no encontraría nada. Se resuelve el código con un JOIN
        contra `trd_series` a través de `trd_serie_id` (la fila CONCRETA que
        el expediente trae arrastrada, sea cual sea su versión) en vez de
        filtrar por `trd_serie_code`."""
        await self._set_search_path()
        return await self._conn.fetchval(
            """
            SELECT COUNT(*)
              FROM expedientes e
              JOIN trd_series s ON s.id = e.trd_serie_id
             WHERE s.code = $1 AND e.status = 'open'
            """,
            code,
        )

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
    # Circuito de convalidación (ADR-025, Increment 2): aprobar/convalidar/
    # derogar. Cada `UPDATE` cierra su `WHERE` con el estado de ORIGEN
    # esperado (mismo patrón fail-closed que `transition_expediente`, H3):
    # devuelve `None` si la fila ya no está en ese estado -- otra petición
    # concurrente ganó la carrera -- en vez de aplicar la transición sobre un
    # estado que el llamante ya no observó. El SERVICE valida la transición
    # ANTES (fail-fast, 409 legible); este `WHERE` es el cinturón de
    # seguridad de motor para la carrera, no la primera línea de defensa.
    # ------------------------------------------------------------------

    async def get_trd_serie_by_code_version(
        self, code: str, version: int
    ) -> TrdSerie | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM trd_series WHERE code = $1 AND version = $2", code, version
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def aprobar_trd_serie(
        self,
        serie_id: uuid.UUID,
        *,
        acta_comite: str,
        fecha_aprobacion_comite: date,
    ) -> TrdSerie | None:
        """`borrador -> aprobada` (ADR-025, Increment 2). Migración 038
        (hallazgo #5): escribe el PRIMER acto del circuito (aprobación del
        Comité) -- antes solo cambiaba `estado`, y `fecha_aprobacion_comite`
        quedaba vestigial porque ningún camino de código la poblaba. El
        trigger `trg_trd_series_append_only` no restringe nada sobre una
        fila TODAVÍA en `borrador`."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado                  = 'aprobada',
                   acta_comite              = $2,
                   fecha_aprobacion_comite  = $3
             WHERE id = $1 AND estado = 'borrador'
            RETURNING *
            """,
            serie_id,
            acta_comite,
            fecha_aprobacion_comite,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def devolver_trd_serie_a_borrador(self, serie_id: uuid.UUID) -> TrdSerie | None:
        """`aprobada -> borrador` (ADR-025, migración 038 -- hallazgo #1 del
        dictamen: "devolver con observaciones" es un paso ORDINARIO del
        procedimiento, no un callejón sin salida que empuje a declarar una
        convalidación falsa para desatascar el instrumento). Limpia
        `acta_comite`/`fecha_aprobacion_comite`: la aprobación que se
        devuelve queda VOID -- el contenido se va a editar, así que esa
        aprobación ya no describe lo que finalmente se apruebe/convalide."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado                  = 'borrador',
                   acta_comite              = NULL,
                   fecha_aprobacion_comite  = NULL
             WHERE id = $1 AND estado = 'aprobada'
            RETURNING *
            """,
            serie_id,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def registrar_rusd_trd_serie(
        self,
        serie_id: uuid.UUID,
        *,
        rusd_radicado: str | None,
        fecha_publicacion: date | None,
    ) -> TrdSerie | None:
        """`POST /trd/{code}/versiones/{version}/registrar-rusd` (ADR-025,
        migración 038 -- hallazgo #4). Solo sobre `convalidada`; el `SET`
        usa `COALESCE(columna, $n)` para rellenar EXCLUSIVAMENTE de NULL a
        un valor -- si la columna ya tenía un valor, `COALESCE` la deja
        intacta (no-op silencioso) y es el SERVICE quien detecta ese caso
        ANTES de llamar aquí para devolver un 409 legible (mismo patrón que
        el resto del circuito: el trigger de la 038 es el cinturón de
        seguridad de motor, no la primera línea de defensa)."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET rusd_radicado     = COALESCE(rusd_radicado, $2),
                   fecha_publicacion = COALESCE(fecha_publicacion, $3)
             WHERE id = $1 AND estado = 'convalidada'
            RETURNING *
            """,
            serie_id,
            rusd_radicado,
            fecha_publicacion,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def convalidar_trd_serie_desde_aprobada(
        self,
        serie_id: uuid.UUID,
        *,
        acto_administrativo: str,
        fecha_convalidacion: date,
        instancia_convalidante: str,
        rusd_radicado: str | None,
        fecha_publicacion: date | None,
    ) -> TrdSerie | None:
        """`aprobada -> convalidada` (circuito normal, ADR-025 D2/Increment
        2). `valid_to` permanece NULL: esta es la versión que pasa a ser la
        VIGENTE (`ux_trd_series_vigente`) -- la derogación de la versión
        previa es una fila DISTINTA (`derogar_vigente_por_code`), y debe
        ejecutarse ANTES de este `UPDATE` en la misma transacción para que el
        índice único parcial nunca vea dos filas vigentes simultáneas."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado                  = 'convalidada',
                   acto_administrativo     = $2,
                   fecha_convalidacion     = $3,
                   instancia_convalidante  = $4,
                   rusd_radicado           = $5,
                   fecha_publicacion       = $6
             WHERE id = $1 AND estado = 'aprobada'
            RETURNING *
            """,
            serie_id,
            acto_administrativo,
            fecha_convalidacion,
            instancia_convalidante,
            rusd_radicado,
            fecha_publicacion,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def convalidar_trd_serie_desde_migrada(
        self,
        serie_id: uuid.UUID,
        *,
        acto_administrativo: str,
        fecha_convalidacion: date,
        instancia_convalidante: str,
        rusd_radicado: str | None,
        fecha_publicacion: date | None,
    ) -> TrdSerie | None:
        """`migrada -> convalidada` (D6, ratificación): la MISMA fila, sin
        crear versión nueva ni derogar nada -- el contenido no cambió; lo
        único que se registra es el acto administrativo que ya existía en
        papel."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado                  = 'convalidada',
                   acto_administrativo     = $2,
                   fecha_convalidacion     = $3,
                   instancia_convalidante  = $4,
                   rusd_radicado           = $5,
                   fecha_publicacion       = $6
             WHERE id = $1 AND estado = 'migrada'
            RETURNING *
            """,
            serie_id,
            acto_administrativo,
            fecha_convalidacion,
            instancia_convalidante,
            rusd_radicado,
            fecha_publicacion,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def derogar_vigente_por_code(
        self, code: str, *, exclude_id: uuid.UUID
    ) -> TrdSerie | None:
        """Deroga la versión VIGENTE actual de `code` (si existe) -- usado al
        convalidar la versión N+1 (`aprobada -> convalidada`), SIEMPRE antes
        de promover la nueva a vigente (ver docstring de
        `convalidar_trd_serie_desde_aprobada`). `exclude_id` evita
        auto-derogar la propia fila que se está convalidando en el caso
        límite de que ya compartiera código/estuviera marcada vigente."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado = 'derogada', valid_to = CURRENT_DATE, is_active = false
             WHERE code = $1 AND estado IN ('convalidada', 'migrada')
               AND valid_to IS NULL AND id <> $2
            RETURNING *
            """,
            code,
            exclude_id,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def derogar_trd_serie(self, serie_id: uuid.UUID) -> TrdSerie | None:
        """`convalidada`/`migrada` -> `derogada` (baja explícita del
        instrumento, ADR-025 Increment 2)."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            UPDATE trd_series
               SET estado = 'derogada', valid_to = CURRENT_DATE, is_active = false
             WHERE id = $1 AND estado IN ('convalidada', 'migrada')
            RETURNING *
            """,
            serie_id,
        )
        return _row_to_trd_serie(row) if row is not None else None

    async def repoint_tipos_documentales(
        self, *, old_serie_id: uuid.UUID, new_serie_id: uuid.UUID
    ) -> tuple[int, int]:
        """Cierra el aviso #3 de ADR-025 §"Impacto en lo existente": al
        convalidar la versión N+1 (que deroga la N), `tipos_documentales`
        apunta a la FILA, no al código -- sin este re-apuntado quedarían
        huérfanos de la versión derogada. `UPDATE`, nunca clonación.

        Hallazgo de seguridad (Media): `UNIQUE (trd_serie_id, code)` revienta
        con `UniqueViolationError` si la versión nueva YA tiene un tipo
        documental con el mismo `code` (flujo LEGÍTIMO: alguien definió los
        tipos de la versión nueva antes de convalidarla). El `NOT EXISTS`
        excluye esas filas del `UPDATE` en vez de fallar -- se devuelve
        `(re_apuntados, omitidos_por_colision)` para que el caller lo audite;
        `UniqueViolationError` queda como red de seguridad residual (si
        aparece de todas formas, es un bug de esta condición, no un caso
        esperado) y el SERVICE la traduce a 409."""
        await self._set_search_path()
        moved = await self._conn.execute(
            """
            UPDATE tipos_documentales AS t
               SET trd_serie_id = $2
             WHERE t.trd_serie_id = $1
               AND NOT EXISTS (
                     SELECT 1 FROM tipos_documentales x
                      WHERE x.trd_serie_id = $2 AND x.code = t.code
                   )
            """,
            old_serie_id,
            new_serie_id,
        )
        skipped: int = await self._conn.fetchval(
            """
            SELECT COUNT(*) FROM tipos_documentales t
             WHERE t.trd_serie_id = $1
               AND EXISTS (
                     SELECT 1 FROM tipos_documentales x
                      WHERE x.trd_serie_id = $2 AND x.code = t.code
                   )
            """,
            old_serie_id,
            new_serie_id,
        )
        return int(moved.split()[-1]), int(skipped)

    async def repoint_expediente_metadata_templates(
        self, *, old_serie_id: uuid.UUID, new_serie_id: uuid.UUID
    ) -> tuple[int, int]:
        """Gemela de `repoint_tipos_documentales` para
        `expediente_metadata_templates` (mismo aviso #3 del ADR, mismo
        hallazgo de seguridad): `UNIQUE (trd_serie_id, version)` + el índice
        parcial de la plantilla activa pueden colisionar si la versión nueva
        ya tiene sus propias plantillas."""
        await self._set_search_path()
        moved = await self._conn.execute(
            """
            UPDATE expediente_metadata_templates AS t
               SET trd_serie_id = $2
             WHERE t.trd_serie_id = $1
               AND NOT EXISTS (
                     SELECT 1 FROM expediente_metadata_templates x
                      WHERE x.trd_serie_id = $2 AND x.version = t.version
                   )
            """,
            old_serie_id,
            new_serie_id,
        )
        skipped: int = await self._conn.fetchval(
            """
            SELECT COUNT(*) FROM expediente_metadata_templates t
             WHERE t.trd_serie_id = $1
               AND EXISTS (
                     SELECT 1 FROM expediente_metadata_templates x
                      WHERE x.trd_serie_id = $2 AND x.version = t.version
                   )
            """,
            old_serie_id,
            new_serie_id,
        )
        return int(moved.split()[-1]), int(skipped)

    async def clear_revision_pendiente_por_serie(
        self, serie_id: uuid.UUID, *, motivo: str
    ) -> int:
        """D6 — ratificación (`migrada -> convalidada`): limpia
        `trd_revision_requerida`/`trd_revision_motivo` SOLO de los
        expedientes `closed`/`transferred` amarrados a `serie_id` cuyo
        motivo sea EXACTAMENTE `motivo` (por defecto, el caller pasa
        `'cerrado_bajo_serie_migrada'`) -- otros motivos
        (`cierre_sin_snapshot_de_retencion`, `serie_no_resoluble`) no se
        resuelven con esta ratificación y quedan intactos."""
        await self._set_search_path()
        result = await self._conn.execute(
            """
            UPDATE expedientes
               SET trd_revision_requerida = false, trd_revision_motivo = NULL
             WHERE trd_serie_id = $1
               AND status IN ('closed', 'transferred')
               AND trd_revision_requerida
               AND trd_revision_motivo = $2
            """,
            serie_id,
            motivo,
        )
        return int(result.split()[-1])

    async def list_expedientes_revision_pendiente(
        self, *, offset: int, limit: int, max_clearance: int, tipo: str | None = None
    ) -> tuple[list[asyncpg.Record], int]:
        """`GET /trd/revision-pendiente` (ADR-025, Increment 2; `tipo_instrumento`
        y filtro `tipo` -- ADR-026 D2/D5, Increment 2, aditivo): informe de
        expedientes marcados `trd_revision_requerida` -- cualquier motivo
        (D4/D6), no solo `cerrado_bajo_serie_migrada`.

        Hallazgo de seguridad [Alta, BLOQUEANTE]: este informe filtraba
        `WHERE trd_revision_requerida` a secas -- sin `nivel_seguridad <=
        max_clearance`, cualquier portador de `USUA_PERM_TRD` (permiso
        ORTOGONAL al clearance: tener potestad sobre el instrumento no es
        tener nivel para leer los expedientes que gobierna) enumeraba
        RESERVADA/CLASIFICADA. Mismo criterio, MISMA condición como PRIMERA
        del `WHERE`, compartida por el `COUNT` y la página, que
        `list_expedientes` (ver esa docstring) -- CONSERVADO íntegro, sin
        tocarlo, al añadir el `LEFT JOIN`/`tipo` (ADR-026 lo exige
        explícitamente).

        `LEFT JOIN` (no `INNER`): un `trd_serie_id` huérfano (fila borrada o
        nunca resuelta) no debe hacer desaparecer el expediente del informe
        -- sigue siendo una revisión pendiente, solo que `tipo_instrumento`
        sale `NULL`. Hoy, en la práctica, esta marca la genera únicamente
        TRD (ninguna TVD llega nunca a `migrada`, ADR-026 §"Máquina de
        estados"), pero el campo se expone igual para que el informe no
        quede mudo si eso cambia."""
        await self._set_search_path()
        conditions = ["e.nivel_seguridad <= $1", "e.trd_revision_requerida"]
        params: list[object] = [max_clearance]
        idx = 2
        if tipo is not None:
            conditions.append(f"t.tipo_instrumento = ${idx}")
            params.append(tipo)
            idx += 1
        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"""
            SELECT COUNT(*)
              FROM expedientes e
              LEFT JOIN trd_series t ON t.id = e.trd_serie_id
             WHERE {where}
            """,
            *params,
        )
        rows = await self._conn.fetch(
            f"""
            SELECT e.id, e.code, e.status, e.trd_serie_id, e.trd_serie_code,
                   e.trd_revision_motivo, e.closed_at, t.tipo_instrumento
              FROM expedientes e
              LEFT JOIN trd_series t ON t.id = e.trd_serie_id
             WHERE {where}
             ORDER BY e.closed_at ASC NULLS LAST, e.code ASC
             LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return rows, total

    # ------------------------------------------------------------------
    # Tipos documentales (3.er nivel TRD, RF-MET-07)
    # ------------------------------------------------------------------

    _TD_COLS = (
        "id, trd_serie_id, code, nombre, obligatorio, metadata_template, "
        "activo, admite_ventanilla_publica, created_at"
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
                # Misma configuración que el `search_vector` de la migración
                # tenant/043: consulta e índice tienen que hablar el mismo
                # idioma. Y la MISMA que usa la búsqueda de radicados, para que
                # el mismo texto no dé resultados distintos según la pantalla.
                f"(search_vector @@ websearch_to_tsquery('public.spanish_unaccent'::regconfig, ${q_idx}) "
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
            f"ts_rank(search_vector, websearch_to_tsquery('public.spanish_unaccent'::regconfig, ${q_idx}))" if q_idx else "0"
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
    if not isinstance(raw, (str, bytes, bytearray)):
        # Ni dict, ni None, ni texto: `json.loads` lanzaria un TypeError
        # opaco. Se devuelve vacio, que es lo que esta funcion ya tolera.
        return {}
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
        # ADR-026 (Increment 2, migración 041): `row.get`-style tolerancia
        # para filas leídas antes de la 041 en algún path residual de test
        # -- en producción la columna siempre existe tras la migración.
        origen=row["origen"] if "origen" in row.keys() else "corriente",
        fecha_extrema_inicial=(
            row["fecha_extrema_inicial"] if "fecha_extrema_inicial" in row.keys() else None
        ),
        fecha_extrema_final=(
            row["fecha_extrema_final"] if "fecha_extrema_final" in row.keys() else None
        ),
        # Migración 042. El default es el seguro y la dirección importa: si una
        # consulta futura no trajera la columna, se ve «no publicado», nunca lo
        # contrario. La difusión real no depende de este mapeo — el chokepoint
        # `oai_repository._PUBLIC` lee la columna en su propio SQL.
        publicable=bool(row["publicable"]) if "publicable" in row.keys() else False,
        publicado_at=row["publicado_at"] if "publicado_at" in row.keys() else None,
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
        acta_comite=row["acta_comite"],
        fecha_aprobacion_comite=row["fecha_aprobacion_comite"],
        fecha_convalidacion=row["fecha_convalidacion"],
        instancia_convalidante=row["instancia_convalidante"],
        rusd_radicado=row["rusd_radicado"],
        fecha_publicacion=row["fecha_publicacion"],
        supersede_a=row["supersede_a"],
        created_by=row["created_by"],
        # ADR-026 (Increment 1, migración 039): `row.get` con default para
        # tolerar filas leídas ANTES de la 039 en algún path residual de test
        # -- en producción la columna siempre existe tras la migración.
        tipo_instrumento=row["tipo_instrumento"] if "tipo_instrumento" in row.keys() else "TRD",
        fondo_nombre=row["fondo_nombre"] if "fondo_nombre" in row.keys() else None,
        fecha_extrema_inicial=(
            row["fecha_extrema_inicial"] if "fecha_extrema_inicial" in row.keys() else None
        ),
        fecha_extrema_final=(
            row["fecha_extrema_final"] if "fecha_extrema_final" in row.keys() else None
        ),
        justificacion_valoracion=(
            row["justificacion_valoracion"] if "justificacion_valoracion" in row.keys() else None
        ),
    )

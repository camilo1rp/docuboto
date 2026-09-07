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
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.core.clearance import resolve_caller_clearance
from app.core.database import tenant_schema
from app.models.flow import FlowStep


def _clearance_condition(idx: int) -> str:
    """Predicado no-read-up (RF-SEG-08) correlacionado con `flow_steps.radicado_id`.

    COALESCE a PUBLICA(1) si el radicado no está clasificado/replicado (mismo
    criterio que `list_flow_events`). Factorizado porque lo reutilizan
    `list_pending`, `list_inbox`, `get_history` y `get_current` — así el
    predicado no puede divergir entre call sites.
    """
    return (
        f"COALESCE((SELECT nivel_seguridad FROM radicados "
        f"WHERE id = flow_steps.radicado_id), 1) <= ${idx}"
    )


def _own_scope_condition(idx: int) -> str:
    """Predicado de alcance POR DEFECTO de bandeja/pending (cierre de la
    primitiva de enumeración: `GET /workflows/inbox`/`/pending` sin acotar
    dejaban ver la carga de TODA la entidad a cualquier usuario autenticado).

    Sin `dept`/`assigned_to` explícitos, el llamante ve lo suyo: pasos
    asignados a él (`assigned_to = $idx`) O de una dependencia a la que
    pertenece por URD activa (`urd_usuaroldep`, auth-service) — la
    correlación es por NOMBRE porque `flow_steps.to_dept`/`from_dept` son
    texto libre (VARCHAR(100), sin FK a `dependencias`, ver
    `document-service/migrations/tenant/005_radicado_dept_codes.sql`); es la
    MISMA convención que usa el resto del sistema (el frontend puebla
    `to_dept` con `dependencias.nombre` del catálogo SSR, nunca con el
    código). Ambas tablas viven en el MISMO schema de tenant que `flow_steps`
    (ADR-002/012), igual que `auth_memberships`/`role_clearance` en
    `resolve_caller_clearance` — sin llamada HTTP a otro servicio.

    NULL-safe por construcción: si `$idx` es NULL (llamante sin `X-User-Id`),
    ambas ramas quedan sin match (`assigned_to = NULL` y el `IN` sobre un
    subquery sin filas) y el predicado entero es NULL → ninguna fila pasa el
    `WHERE` (fail-closed), sin necesidad de una rama explícita.

    Inline, sin try/except UndefinedTableError: mismo criterio que
    `_clearance_condition`, que ya referencia sin protección la tabla
    `radicados` de otro servicio — ambas asumen el schema del tenant
    totalmente provisionado.
    """
    return (
        f"(assigned_to = ${idx} OR to_dept IN ("
        f"SELECT d.nombre FROM urd_usuaroldep u "
        f"JOIN dependencias d ON d.codigo = u.depe_id "
        f"WHERE u.user_id = ${idx} AND u.is_active = TRUE AND d.activo = TRUE"
        f"))"
    )


class WorkflowRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def caller_clearance(self, user_id: uuid.UUID | None) -> int:
        """Clearance del llamante, resuelto tras fijar el search_path del tenant."""
        await self._set_search_path()
        return await resolve_caller_clearance(self._conn, user_id)

    async def lock_radicado(self, radicado_id: uuid.UUID) -> None:
        """Serializa las aperturas de paso de un radicado dentro de la transacción.

        `pg_advisory_xact_lock` y no un `SELECT ... FOR UPDATE` porque no hay una
        fila natural que bloquear: lo que se protege es la SECUENCIA de pasos, no
        un registro. Se libera solo al terminar la transacción, y solo afecta a
        este radicado.
        """
        await self._set_search_path()
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", str(radicado_id)
        )

    async def get_next_step_number(self, radicado_id: uuid.UUID) -> int:
        await self._set_search_path()
        val = await self._conn.fetchval(
            "SELECT COALESCE(MAX(step_number), 0) + 1 FROM flow_steps WHERE radicado_id = $1",
            radicado_id,
        )
        return val

    async def get_radicado_tracking_number(self, radicado_id: uuid.UUID) -> str | None:
        """Resuelve el `tracking_number` REAL desde `radicados` (Bloqueante 3).

        `_create_step` (assign/transfer) nunca debe confiar en el `tracking_number`
        del cuerpo: es un campo libre del cliente que termina en `flow_events`
        (append-only) y en `public.audit_log.object_ref` sin más verificación.
        `None` = el radicado no existe (fail-closed: el llamante debe 404).
        """
        await self._set_search_path()
        return await self._conn.fetchval(
            "SELECT tracking_number FROM radicados WHERE id = $1", radicado_id
        )

    async def dependencia_activa(self, nombre: str) -> bool:
        """¿Existe una dependencia ACTIVA del catálogo con este nombre exacto?

        Gemelo de `get_radicado_tracking_number`: `_create_step` (assign/transfer)
        no debe confiar en `to_dept`/`from_dept` como texto libre del cliente
        (`VARCHAR(100)`, sin FK a `dependencias` — ver docstring de
        `_own_scope_condition`, que ya depende de esta misma correlación por
        NOMBRE para acotar `inbox`/`pending`). Sin esta comprobación, un
        `to_dept` con una errata o inventado crea un paso que no coincide con el
        alcance propio de NADIE: el radicado queda `pending` en BD pero invisible
        en toda bandeja — pérdida silenciosa de un trámite oficial.

        `dependencias` vive en el MISMO schema de tenant que `flow_steps`
        (ADR-002/012, mismo criterio que `_own_scope_condition`) — sin llamada
        HTTP a otro servicio. Dependencia INACTIVA cuenta como inexistente a
        propósito: un paso hacia una dependencia dada de baja es igual de
        invisible en la práctica (nadie tiene URD activa apuntando a ella).
        """
        await self._set_search_path()
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM dependencias WHERE nombre = $1 AND activo = TRUE", nombre
            )
        )

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        """Existencia del destino de una reasignación (Hallazgo C).

        `reassign_cascade` aparca los pasos en `to_assigned_to` sin verificar
        nada: un UUID equivocado los deja `pending` pero invisibles en cualquier
        bandeja — denegación de servicio silenciosa del trámite.
        """
        await self._set_search_path()
        return bool(await self._conn.fetchval("SELECT 1 FROM auth_users WHERE id = $1", user_id))

    async def complete_active_step(self, radicado_id: uuid.UUID) -> None:
        """Mark the current pending/in_progress step as completed."""
        await self._set_search_path()
        await self._conn.execute(
            """
            UPDATE flow_steps
               SET status = 'completed', completed_at = $1
             WHERE radicado_id = $2
               AND status IN ('pending', 'in_progress')
            """,
            datetime.now(tz=UTC),
            radicado_id,
        )

    async def create(
        self,
        *,
        radicado_id: uuid.UUID,
        tracking_number: str,
        step_number: int,
        from_dept: str | None,
        to_dept: str,
        assigned_to: uuid.UUID | None,
        assigned_by: uuid.UUID,
        action: str,
        notes: str | None,
        doc_type: str | None = None,
        due_at: datetime | None = None,
    ) -> FlowStep:
        await self._set_search_path()
        # El paso (proyección) y su evento append-only se escriben atómicamente:
        # no hay paso de trámite sin su registro inalterable (RF-FLU-02).
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                """
                INSERT INTO flow_steps (
                    radicado_id, tracking_number, step_number,
                    from_dept, to_dept, assigned_to, assigned_by,
                    action, notes, status, doc_type, due_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10, $11)
                RETURNING *
                """,
                radicado_id,
                tracking_number,
                step_number,
                from_dept,
                to_dept,
                assigned_to,
                assigned_by,
                action,
                notes,
                doc_type,
                due_at,
            )
            await self._insert_flow_event(
                radicado_id=radicado_id,
                tracking_number=tracking_number,
                tipo_tx=action,
                from_dept=from_dept,
                to_dept=to_dept,
                actor=assigned_by,
                comentario=notes,
                detalles={"step_number": step_number},
            )
        return _row_to_model(row)

    async def _insert_flow_event(
        self,
        *,
        radicado_id: uuid.UUID,
        tracking_number: str,
        tipo_tx: str,
        from_dept: str | None,
        to_dept: str | None,
        actor: uuid.UUID | None,
        comentario: str | None,
        detalles: dict[str, Any],
    ) -> None:
        # Asume search_path ya fijado por el método llamante.
        await self._conn.execute(
            """
            INSERT INTO flow_events (
                radicado_id, tracking_number, tipo_tx,
                from_dept, to_dept, actor, comentario, detalles
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            radicado_id,
            tracking_number,
            tipo_tx,
            from_dept,
            to_dept,
            actor,
            comentario,
            json.dumps(detalles or {}),
        )

    async def list_flow_events(
        self, radicado_id: uuid.UUID, max_clearance: int = 3
    ) -> list[asyncpg.Record]:
        # Canal B (E08 §9): la hoja de ruta solo es visible si el radicado lo es para el
        # clearance del llamante (no read-up, RF-SEG-08). El nivel del radicado vive en la
        # tabla `radicados` (document-service, mismo schema tenant); COALESCE a PUBLICA(1)
        # si no hay fila (radicado sin clasificar / dato aún no replicado). max_clearance=3
        # (CLASIFICADA) = sin restricción para llamantes internos/sistema.
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                """
                SELECT id, radicado_id, tracking_number, tipo_tx,
                       from_dept, to_dept, actor, comentario, detalles, ts
                FROM flow_events
                WHERE radicado_id = $1
                  AND COALESCE(
                        (SELECT nivel_seguridad FROM radicados WHERE id = $1), 1
                      ) <= $2
                ORDER BY ts ASC, id ASC
                """,
                radicado_id,
                max_clearance,
            )
        )

    async def get_history(self, radicado_id: uuid.UUID, *, max_clearance: int) -> list[FlowStep]:
        """Hoja de ruta completa de un radicado (RF-SEG-08, no-read-up).

        ``max_clearance`` es **keyword-only y obligatorio** (sin default peligroso,
        gemelo del patrón de archive-service): un radicado por encima del clearance
        del llamante devuelve una hoja de ruta VACÍA, no un error — no hay oráculo.
        Expuesto vía HTTP (`GET /{radicado_id}/history`), sin más llamador interno.
        """
        await self._set_search_path()
        rows = await self._conn.fetch(
            f"""
            SELECT * FROM flow_steps
             WHERE radicado_id = $1
               AND {_clearance_condition(2)}
             ORDER BY step_number ASC
            """,
            radicado_id,
            max_clearance,
        )
        return [_row_to_model(r) for r in rows]

    async def get_current(self, radicado_id: uuid.UUID, *, max_clearance: int) -> FlowStep | None:
        """Paso activo de un radicado (RF-SEG-08, no-read-up).

        ``max_clearance`` es **keyword-only y obligatorio**. Tiene DOS naturalezas de
        llamador (ver `app.core.clearance.CLEARANCE_SIN_RESTRICCION`):
          - `WorkflowService.get_current` (HTTP-facing, `GET /{radicado_id}/current`):
            SIEMPRE resuelve el clearance REAL del llamante.
          - `WorkflowService.rollback`/`devolver` (acción de trámite/tenencia, no
            lectura): pasan `CLEARANCE_SIN_RESTRICCION` a propósito — mover el
            radicado es ortogonal al clearance de lectura, la autorización real vive
            en la verificación de tenencia/estado de esos métodos.
        """
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"""
            SELECT * FROM flow_steps
             WHERE radicado_id = $1
               AND status IN ('pending', 'in_progress')
               AND {_clearance_condition(2)}
             ORDER BY step_number DESC
             LIMIT 1
            """,
            radicado_id,
            max_clearance,
        )
        return _row_to_model(row) if row is not None else None

    async def list_pending(
        self,
        *,
        dept: str | None,
        assigned_to: uuid.UUID | None,
        own_scope_user_id: uuid.UUID | None,
        max_clearance: int,
        offset: int,
        limit: int,
    ) -> tuple[list[FlowStep], int]:
        """``max_clearance`` es keyword-only y obligatorio (RF-SEG-08, no-read-up):
        un radicado clasificado por encima del clearance del llamante no aparece en
        la bandeja NI cuenta en el total — el predicado entra en el ``WHERE``
        compartido por el `COUNT(*)` y la página (`X-Total-Count` no debe delatar
        filas ocultas).

        ``own_scope_user_id``: cierre de la primitiva de enumeración (`GET
        /workflows/pending` sin acotar). El SERVICIO decide su valor: no
        ``None`` cuando la petición NO trae `dept`/`assigned_to` (alcance por
        defecto, `_own_scope_condition`); ``None`` cuando sí los trae (el
        servicio ya exigió `PERM_TRAMITAR`/`USUA_PERM_ADMIN` para ampliar el
        alcance) — ambos ejes son mutuamente excluyentes por request.
        """
        await self._set_search_path()

        conditions = ["status IN ('pending', 'in_progress')"]
        params: list[object] = []
        idx = 1

        if dept is not None:
            conditions.append(f"to_dept = ${idx}")
            params.append(dept)
            idx += 1

        if assigned_to is not None:
            conditions.append(f"assigned_to = ${idx}")
            params.append(assigned_to)
            idx += 1

        if dept is None and assigned_to is None:
            conditions.append(_own_scope_condition(idx))
            params.append(own_scope_user_id)
            idx += 1

        conditions.append(_clearance_condition(idx))
        params.append(max_clearance)
        idx += 1

        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM flow_steps WHERE {where}", *params
        )

        rows = await self._conn.fetch(
            f"""
            SELECT * FROM flow_steps
             WHERE {where}
             ORDER BY created_at ASC
             LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return [_row_to_model(r) for r in rows], total

    async def get_by_id(self, step_id: uuid.UUID) -> FlowStep | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE id = $1",
            step_id,
        )
        return _row_to_model(row) if row is not None else None

    async def complete_step(
        self, step_id: uuid.UUID, notes: str | None, actor: uuid.UUID | None
    ) -> FlowStep | None:
        """Cierra un paso, anclando autorización y mutación en el MISMO `UPDATE`
        (Bloqueantes 1+2): sin ventana TOCTOU entre "¿es tuyo?" y "ciérralo".

        `assigned_to = $4 OR assigned_to IS NULL`: un paso de dependencia sin
        dueño (los que genera `apply_devolucion`) sigue siendo cerrable por
        quien tramita — no se rompe ese caso legítimo. Si el `WHERE` no casa
        (paso ajeno o inexistente), `row` es `None` y el llamante responde el
        MISMO 404 `step_not_found` en ambos casos (no-oráculo, gemelo de
        `rollback`/`devolver`): no se distingue "no es tuyo" de "no existe".

        `actor` (el ejecutor real, no `assigned_to`) queda en `flow_events.actor`
        — antes se asentaba `row["assigned_to"]`, que en una tabla append-only
        con trigger anti-UPDATE/DELETE dejaba la traza acusando para siempre a
        la persona equivocada (o sin actor si `assigned_to` era NULL).
        """
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                """
                UPDATE flow_steps
                   SET status = 'completed',
                       completed_at = $1,
                       notes = COALESCE($2, notes)
                 WHERE id = $3 AND (assigned_to = $4 OR assigned_to IS NULL)
                RETURNING *
                """,
                now,
                notes,
                step_id,
                actor,
            )
            if row is None:
                return None
            await self._insert_flow_event(
                radicado_id=row["radicado_id"],
                tracking_number=row["tracking_number"],
                tipo_tx="complete",
                from_dept=row["to_dept"],
                to_dept=row["to_dept"],
                actor=actor,
                comentario=notes,
                detalles={"step_id": str(step_id)},
            )
        return _row_to_model(row)

    async def list_inbox(
        self,
        *,
        doc_type: str | None,
        dept: str | None,
        assigned_to: uuid.UUID | None,
        own_scope_user_id: uuid.UUID | None,
        max_clearance: int,
        offset: int,
        limit: int,
    ) -> tuple[list[FlowStep], int]:
        """Bandeja tipada: pasos activos filtrados por doc_type (E/S/I), dependencia
        y/o responsable, priorizados por antigüedad (RF-FLU-03).

        ``max_clearance`` es keyword-only y obligatorio (RF-SEG-08, no-read-up):
        mismo predicado y misma garantía COUNT==SELECT que `list_pending`.

        ``own_scope_user_id``: ver docstring de `list_pending` (gemelo exacto,
        `box`/`doc_type` es ortogonal al eje de alcance)."""
        await self._set_search_path()
        conditions = ["status IN ('pending', 'in_progress')"]
        params: list[object] = []
        idx = 1
        if doc_type is not None:
            conditions.append(f"doc_type = ${idx}")
            params.append(doc_type)
            idx += 1
        if dept is not None:
            conditions.append(f"to_dept = ${idx}")
            params.append(dept)
            idx += 1
        if assigned_to is not None:
            conditions.append(f"assigned_to = ${idx}")
            params.append(assigned_to)
            idx += 1
        if dept is None and assigned_to is None:
            conditions.append(_own_scope_condition(idx))
            params.append(own_scope_user_id)
            idx += 1
        conditions.append(_clearance_condition(idx))
        params.append(max_clearance)
        idx += 1
        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM flow_steps WHERE {where}", *params
        )
        rows = await self._conn.fetch(
            f"SELECT * FROM flow_steps WHERE {where} "
            f"ORDER BY created_at ASC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            limit,
            offset,
        )
        return [_row_to_model(r) for r in rows], total

    async def list_overdue_unnotified(self) -> list[FlowStep]:
        """Pasos activos vencidos (due_at < now) aún no alertados (RF-FLU-07)."""
        await self._set_search_path()
        rows = await self._conn.fetch(
            """
            SELECT * FROM flow_steps
             WHERE status IN ('pending', 'in_progress')
               AND due_at IS NOT NULL AND due_at < now()
               AND overdue_notified = false
             ORDER BY due_at ASC
            """
        )
        return [_row_to_model(r) for r in rows]

    async def mark_overdue_notified(self, step_ids: list[uuid.UUID]) -> None:
        if not step_ids:
            return
        await self._set_search_path()
        await self._conn.execute(
            "UPDATE flow_steps SET overdue_notified = true WHERE id = ANY($1::uuid[])",
            step_ids,
        )

    # ------------------------------------------------------------------
    # Transacciones de trámite (RF-FLU-01)
    # ------------------------------------------------------------------

    async def list_transaction_types(self) -> list[asyncpg.Record]:
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                "SELECT code, nombre, descripcion, requires_permission, "
                "target_step_status, activo FROM transaction_types "
                "WHERE activo ORDER BY code"
            )
        )

    async def get_transaction_type(self, code: str) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            "SELECT code, nombre, descripcion, requires_permission, "
            "target_step_status, activo FROM transaction_types WHERE code = $1",
            code,
        )

    async def get_latest_step(self, radicado_id: uuid.UUID) -> FlowStep | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE radicado_id = $1 " "ORDER BY step_number DESC LIMIT 1",
            radicado_id,
        )
        return _row_to_model(row) if row is not None else None

    async def apply_transaction(
        self,
        *,
        latest: FlowStep,
        tipo_tx: str,
        target_step_status: str | None,
        actor: uuid.UUID | None,
        comentario: str | None,
        detalles: dict[str, Any],
    ) -> asyncpg.Record:
        """Aplica una transacción de trámite: registra el evento append-only y, si
        corresponde, cambia el estado del paso activo, atómicamente (RF-FLU-01)."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            if target_step_status is not None and latest.status in (
                "pending",
                "in_progress",
            ):
                completed = now if target_step_status in ("cancelled", "completed") else None
                await self._conn.execute(
                    "UPDATE flow_steps SET status = $1, completed_at = $2 WHERE id = $3",
                    target_step_status,
                    completed,
                    latest.id,
                )
            event = await self._conn.fetchrow(
                """
                INSERT INTO flow_events (
                    radicado_id, tracking_number, tipo_tx,
                    from_dept, to_dept, actor, comentario, detalles
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                RETURNING id, radicado_id, tracking_number, tipo_tx,
                          from_dept, to_dept, actor, comentario, detalles, ts
                """,
                latest.radicado_id,
                latest.tracking_number,
                tipo_tx,
                latest.to_dept,
                latest.to_dept,
                actor,
                comentario,
                json.dumps(detalles or {}),
            )
        assert event is not None
        return event

    # ------------------------------------------------------------------
    # Rollback y reasignación en cascada (RF-FLU-08)
    # ------------------------------------------------------------------

    async def get_previous_step(
        self, radicado_id: uuid.UUID, before_step_number: int
    ) -> FlowStep | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            SELECT * FROM flow_steps
             WHERE radicado_id = $1 AND step_number < $2
             ORDER BY step_number DESC
             LIMIT 1
            """,
            radicado_id,
            before_step_number,
        )
        return _row_to_model(row) if row is not None else None

    async def apply_rollback(
        self,
        *,
        current: FlowStep,
        previous: FlowStep,
        actor: uuid.UUID | None,
        comentario: str | None = None,
    ) -> FlowStep:
        """Cancela el paso actual y reactiva el anterior, atómicamente (RF-FLU-08).

        `comentario` es el motivo de la reversión (`RollbackRequest.motivo`,
        obligatorio desde el router) — queda en `flow_events.comentario`, gemelo
        de `apply_devolucion` (que ya recibe `comentario` para el mismo campo)."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            await self._conn.execute(
                "UPDATE flow_steps SET status='cancelled', completed_at=$1 WHERE id=$2",
                now,
                current.id,
            )
            row = await self._conn.fetchrow(
                """
                UPDATE flow_steps SET status='pending', completed_at=NULL
                 WHERE id=$1 RETURNING *
                """,
                previous.id,
            )
            await self._insert_flow_event(
                radicado_id=current.radicado_id,
                tracking_number=current.tracking_number,
                tipo_tx="rollback",
                from_dept=current.to_dept,
                to_dept=previous.to_dept,
                actor=actor,
                comentario=comentario,
                detalles={"cancelled_step": current.step_number},
            )
        return _row_to_model(row)

    async def apply_devolucion(
        self,
        *,
        current: FlowStep,
        to_dept: str,
        causal: str,
        comentario: str | None,
        actor: uuid.UUID | None,
    ) -> FlowStep:
        """Devuelve el radicado al remitente/origen con causal (RF-FLU): cierra el
        paso actual como 'returned' y crea un paso nuevo hacia `to_dept`, atómicamente."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            await self._conn.execute(
                "UPDATE flow_steps SET status='returned', completed_at=$1 WHERE id=$2",
                now,
                current.id,
            )
            row = await self._conn.fetchrow(
                """
                INSERT INTO flow_steps (
                    radicado_id, tracking_number, step_number,
                    from_dept, to_dept, assigned_to, assigned_by, action, notes, status
                ) VALUES ($1, $2, $3, $4, $5, NULL, $6, 'devolucion', $7, 'pending')
                RETURNING *
                """,
                current.radicado_id,
                current.tracking_number,
                current.step_number + 1,
                current.to_dept,
                to_dept,
                actor,
                comentario,
            )
            await self._insert_flow_event(
                radicado_id=current.radicado_id,
                tracking_number=current.tracking_number,
                tipo_tx="devolucion",
                from_dept=current.to_dept,
                to_dept=to_dept,
                actor=actor,
                comentario=comentario,
                detalles={"causal": causal, "step_number": current.step_number + 1},
            )
        return _row_to_model(row)

    async def reassign_cascade(
        self,
        *,
        from_assigned_to: uuid.UUID | None,
        from_dept: str | None,
        to_assigned_to: uuid.UUID,
        actor: uuid.UUID | None,
    ) -> int:
        """Reasigna en una transacción todos los pasos activos del origen al nuevo
        responsable, sin dejar radicados huérfanos (RF-FLU-08). Devuelve el conteo."""
        await self._set_search_path()
        conds = ["status IN ('pending', 'in_progress')"]
        params: list[object] = []
        idx = 1
        if from_assigned_to is not None:
            conds.append(f"assigned_to = ${idx}")
            params.append(from_assigned_to)
            idx += 1
        if from_dept is not None:
            conds.append(f"to_dept = ${idx}")
            params.append(from_dept)
            idx += 1
        where = " AND ".join(conds)

        async with self._conn.transaction():
            rows = await self._conn.fetch(f"SELECT * FROM flow_steps WHERE {where}", *params)
            for r in rows:
                await self._conn.execute(
                    "UPDATE flow_steps SET assigned_to=$1 WHERE id=$2",
                    to_assigned_to,
                    r["id"],
                )
                await self._insert_flow_event(
                    radicado_id=r["radicado_id"],
                    tracking_number=r["tracking_number"],
                    tipo_tx="reassign",
                    from_dept=r["to_dept"],
                    to_dept=r["to_dept"],
                    actor=actor,
                    comentario=None,
                    detalles={
                        "from_assigned_to": str(r["assigned_to"]) if r["assigned_to"] else None,
                        "to_assigned_to": str(to_assigned_to),
                    },
                )
        return len(rows)


def _row_to_model(row: asyncpg.Record) -> FlowStep:
    return FlowStep(
        id=row["id"],
        radicado_id=row["radicado_id"],
        tracking_number=row["tracking_number"],
        step_number=row["step_number"],
        from_dept=row["from_dept"],
        to_dept=row["to_dept"],
        assigned_to=row["assigned_to"],
        assigned_by=row["assigned_by"],
        action=row["action"],
        notes=row["notes"],
        status=row["status"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        doc_type=row["doc_type"],
        due_at=row["due_at"],
        overdue_notified=row["overdue_notified"],
    )

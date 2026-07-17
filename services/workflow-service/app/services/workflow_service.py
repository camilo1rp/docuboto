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
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit, events

from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.redis_client import get_redis
from app.models.flow import FlowStep
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.workflow import (
    FlowEventResponse,
    FlowHistoryResponse,
    FlowStepComplete,
    FlowStepCreate,
    FlowStepResponse,
    TransactionRequest,
    TransactionTypeResponse,
)


class RollbackError(Exception):
    """No se puede revertir la última asignación (no hay paso activo o es la inicial)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TransactionError(Exception):
    """La transacción de trámite no se puede ejecutar."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_STREAM = "orpycamcp.workflow.events"
_EVT_CREATED = "workflow.step.created"
_EVT_COMPLETED = "workflow.step.completed"

_SERVICE = "workflow-service"

# Traza de LECTURA (RF-BUS-10/E09; Ley 1712/2014, Decreto 1081/2015) — canales que
# representan una consulta real de un usuario (gateway/mcp-server, ADR-019/D-03).
# 'system' (llamadas sin canal explícito, p. ej. jobs) queda excluido para no
# ensuciar la pista de acceso con ruido interno. Gemelo de
# `document_service._CANALES_AUDITABLES` / `ArchiveService._CANALES_AUDITABLES`.
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})

# Traza AGREGADA de la bandeja/pending (`list_pending`/`list_inbox`): constantes
# DEDICADAS, fuera de cualquier object_type de lectura de UN registro concreto
# (p. ej. "radicado"), para no contaminar un futuro historial de acceso por-objeto
# con entradas de listado. Gemelo de `DocumentService._SEARCH_OBJECT_TYPE`.
_BANDEJA_OBJECT_TYPE = "flow_step_busqueda"
_BANDEJA_OBJECT_REF = "bandeja"
_BANDEJA_ACTION = "workflow.bandeja_consultada"

# Traza de la hoja de ruta de UN radicado (`get_history`): gemelo de
# `archive.expediente_consultado`.
_HOJA_RUTA_ACTION = "workflow.hoja_ruta_consultada"

# Traza de la lectura del paso activo / eventos append-only de UN radicado
# (`get_current`, `get_events`): mismo helper que `_HOJA_RUTA_ACTION`, acción
# distinta para no confundir en un futuro reporte "consultó la hoja de ruta
# completa" con "consultó el trámite puntual" (`get_current`/`get_events`).
_TRAMITE_ACTION = "workflow.tramite_consultado"

# Bandejas tipadas (RF-FLU-03): box → doc_type del radicado.
_BOX_TO_DOC_TYPE = {"entrada": "E", "salida": "S", "internos": "I"}
VALID_BOXES = frozenset(_BOX_TO_DOC_TYPE)

logger = logging.getLogger(__name__)


def _semaforo(due_at: datetime | None, now: datetime) -> str | None:
    """Estado del plazo (RF-FLU-07): verde > 3d, amarillo ≤ 3d, rojo ≤ 1d, vencido < 0."""
    if due_at is None:
        return None
    days = (due_at - now).total_seconds() / 86400
    if days < 0:
        return "vencido"
    if days <= 1:
        return "rojo"
    if days <= 3:
        return "amarillo"
    return "verde"


# Actions that require from_dept to be provided by the caller.
_REQUIRES_FROM_DEPT = {"transfer", "return"}


def _no_active_step_error(radicado_id: uuid.UUID) -> HTTPException:
    """404 neutro compartido por `get_current` y `devolver` (no-oráculo, M2).

    Una sola fuente de verdad para el mensaje/forma exacta de "no hay paso
    activo" — así un no-tenedor (`devolver`) es byte-a-byte indistinguible de
    un radicado sin paso activo/inexistente (`get_current`).
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "no_active_step",
            "detail": f"No active flow step found for radicado {radicado_id}",
            "status": 404,
        },
    )


class WorkflowService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = WorkflowRepository(conn, tenant_slug)
        self._tenant_slug = tenant_slug

    async def assign(self, payload: FlowStepCreate, assigned_by: uuid.UUID) -> FlowStepResponse:
        """Create the first step or re-assign a radicado to a dependencia."""
        step = await self._create_step(payload, assigned_by)
        await self._publish(_EVT_CREATED, step)
        return _to_response(step)

    async def transfer(self, payload: FlowStepCreate, assigned_by: uuid.UUID) -> FlowStepResponse:
        """Transfer a radicado from one dependencia to another."""
        if not payload.from_dept:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "missing_from_dept",
                    "detail": "from_dept is required for transfer/return actions",
                    "status": 422,
                },
            )
        step = await self._create_step(payload, assigned_by)
        await self._publish(_EVT_CREATED, step)
        return _to_response(step)

    async def get_history(
        self,
        radicado_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> FlowHistoryResponse:
        """Hoja de ruta completa de un radicado (RF-SEG-08 no-read-up + RF-BUS-10
        traza de lectura). ``user_id``/``canal`` son opcionales (fail-closed a
        PUBLICA sin identidad, sin traza sin canal auditable) — no requieren
        X-User-Id como precondición dura, a diferencia de las escrituras."""
        max_clearance = await self._repo.caller_clearance(user_id)
        steps = await self._repo.get_history(radicado_id, max_clearance=max_clearance)
        tracking_number = steps[0].tracking_number if steps else ""
        await self._audit_hoja_ruta_access(
            radicado_id=radicado_id, user_id=user_id, canal=canal, tracking_number=tracking_number
        )
        return FlowHistoryResponse(
            radicado_id=radicado_id,
            tracking_number=tracking_number,
            steps=[_to_response(s) for s in steps],
        )

    async def get_events(
        self,
        radicado_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        *,
        canal: str = "system",
    ) -> list[FlowEventResponse]:
        """Historial append-only de transacciones del radicado (RF-FLU-02).

        Canal B / hoja de ruta operativa (E08 §9): visible a cualquiera que pueda LEER el
        radicado, acotado por su clearance real (no read-up, RF-SEG-08 — `caller_clearance`
        resuelve el nivel REAL del llamante, igual que `get_current`). Un radicado por
        encima del clearance del llamante devuelve historial vacío (no revela su trámite).

        RF-BUS-10: deja traza best-effort de la consulta (gemelo de `get_current`).
        """
        max_clearance = await self._repo.caller_clearance(user_id)
        rows = await self._repo.list_flow_events(radicado_id, max_clearance)
        await self._audit_hoja_ruta_access(
            radicado_id=radicado_id,
            user_id=user_id,
            canal=canal,
            action=_TRAMITE_ACTION,
            tracking_number=rows[0]["tracking_number"] if rows else None,
        )
        return [_to_event_response(r) for r in rows]

    async def list_transaction_types(self) -> list[TransactionTypeResponse]:
        rows = await self._repo.list_transaction_types()
        return [TransactionTypeResponse(**dict(r)) for r in rows]

    async def execute_transaction(
        self,
        radicado_id: uuid.UUID,
        payload: TransactionRequest,
        actor: uuid.UUID | None,
    ) -> FlowEventResponse:
        """Ejecuta una transacción de trámite (RF-FLU-01): valida el tipo, aplica el
        efecto de estado y registra el evento append-only."""
        tt = await self._repo.get_transaction_type(payload.tipo_tx)
        if tt is None:
            raise TransactionError("unknown_transaction")
        latest = await self._repo.get_latest_step(radicado_id)
        if latest is None:
            raise TransactionError("no_flow")
        event = await self._repo.apply_transaction(
            latest=latest,
            tipo_tx=payload.tipo_tx,
            target_step_status=tt["target_step_status"],
            actor=actor,
            comentario=payload.comentario,
            detalles=payload.detalles,
        )
        return _to_event_response(event)

    async def inbox(
        self,
        *,
        box: str | None,
        dept: str | None,
        assigned_to: uuid.UUID | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[FlowStepResponse], int]:
        """Bandeja tipada (RF-FLU-03): box ∈ entrada|salida|internos → doc_type E|S|I.

        RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta)."""
        doc_type = _BOX_TO_DOC_TYPE.get(box) if box else None
        offset = (page - 1) * size
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_inbox(
            doc_type=doc_type,
            dept=dept,
            assigned_to=assigned_to,
            max_clearance=max_clearance,
            offset=offset,
            limit=size,
        )
        await self._audit_bandeja_access(
            filtros={
                "box": box,
                "dept": dept,
                "assigned_to": str(assigned_to) if assigned_to else None,
            },
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        return [_to_response(s) for s in steps], total

    async def scan_overdue(self) -> int:
        """Detecta pasos vencidos no alertados, emite `workflow.step.overdue` a E16
        y los marca para no duplicar alertas (RF-FLU-07). Devuelve el conteo."""
        steps = await self._repo.list_overdue_unnotified()
        if not steps:
            return 0
        try:
            redis = get_redis()
        except Exception:  # noqa: BLE001 — sin Redis no se emiten alertas
            redis = None

        notified: list[uuid.UUID] = []
        for s in steps:
            if redis is not None:
                try:
                    await events.emit(
                        redis,
                        _STREAM,
                        "workflow.step.overdue",
                        tenant_slug=self._tenant_slug,
                        dedup_key=f"overdue:{s.id}",
                        payload={
                            "radicado_id": str(s.radicado_id),
                            "tracking_number": s.tracking_number,
                            "to_dept": s.to_dept,
                            "assigned_to": str(s.assigned_to) if s.assigned_to else None,
                            "due_at": s.due_at.isoformat() if s.due_at else None,
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("No se pudo emitir alerta de vencimiento", exc_info=True)
                    continue
            notified.append(s.id)
        await self._repo.mark_overdue_notified(notified)
        return len(notified)

    async def rollback(self, radicado_id: uuid.UUID, actor: uuid.UUID | None) -> FlowStepResponse:
        """Revierte la última asignación (solo si el paso sigue pending) (RF-FLU-08).

        `CLEARANCE_SIN_RESTRICCION`: revertir un trámite es una acción de gestión,
        ortogonal al clearance de LECTURA (ver docstring de la constante) — no es
        una lectura expuesta al llamante, es la pre-condición interna de la reversión.

        Autorización por TENENCIA (espejo exacto de `devolver`): `rollback` y
        `devolver` son ambas acciones del TENEDOR ACTUAL del paso activo — difieren
        solo en el destino (`rollback` = paso inmediato anterior; `devolver` = dept
        elegido por el actor con causal), así que comparten la misma autorización.
        Un no-tenedor recibe el MISMO `RollbackError("no_active_step")` que el caso
        "no hay paso activo" (no-oráculo): distinguir "existe pero no es tuyo" de
        "no hay paso" filtraría la existencia/asignación de un radicado clasificado
        a cualquier autenticado del tenant que conozca el UUID.
        """
        current = await self._repo.get_current(radicado_id, max_clearance=CLEARANCE_SIN_RESTRICCION)
        if current is None or current.status != "pending":
            raise RollbackError("no_active_step")
        if current.assigned_to != actor:
            # No-oráculo: mismo código que "no hay paso activo" (ver docstring).
            raise RollbackError("no_active_step")
        previous = await self._repo.get_previous_step(radicado_id, current.step_number)
        if previous is None:
            raise RollbackError("cannot_rollback_initial")
        reactivated = await self._repo.apply_rollback(
            current=current, previous=previous, actor=actor
        )
        return _to_response(reactivated)

    async def devolver(
        self,
        radicado_id: uuid.UUID,
        *,
        to_dept: str,
        causal: str,
        comentario: str | None,
        actor: uuid.UUID | None,
    ) -> FlowStepResponse:
        """Devuelve el radicado a su remitente/origen con causal (re-enrutado).

        Autorización por tenencia (fiel al legado): solo quien tenga el radicado
        asignado actualmente puede devolverlo.  No hay permiso RBAC global para
        esta acción (RF-FLU / Orfeo legacy).

        `CLEARANCE_SIN_RESTRICCION`: la lectura interna del paso activo es
        pre-condición de la acción, no una lectura expuesta — la autorización real
        es la verificación de tenencia de abajo (`assigned_to == actor`).

        No-oráculo (M2/RF-SEG-08, mismo criterio que `rollback`): un no-tenedor
        recibe el MISMO 404 `no_active_step` que el caso "sin paso activo" — YA NO
        se distingue con un 403 `not_current_holder`, porque ese 403 le permitía a
        cualquier autenticado del tenant sondear UUIDs y distinguir "existe un
        paso activo ajeno" (403) de "no hay paso/no existe" (404), filtrando la
        existencia/asignación de un radicado clasificado sin necesitar clearance
        para leerlo. Se pierde el mensaje específico "solo el tenedor puede
        devolverlo" — es el precio del no-oráculo.
        """
        current = await self._repo.get_current(radicado_id, max_clearance=CLEARANCE_SIN_RESTRICCION)
        if current is None or current.assigned_to != actor:
            raise _no_active_step_error(radicado_id)
        new_step = await self._repo.apply_devolucion(
            current=current,
            to_dept=to_dept,
            causal=causal,
            comentario=comentario,
            actor=actor,
        )
        return _to_response(new_step)

    async def reassign_cascade(
        self,
        *,
        from_assigned_to: uuid.UUID | None,
        from_dept: str | None,
        to_assigned_to: uuid.UUID,
        actor: uuid.UUID | None,
    ) -> int:
        """Reasigna todos los pasos activos del origen al nuevo responsable (RF-FLU-08)."""
        return await self._repo.reassign_cascade(
            from_assigned_to=from_assigned_to,
            from_dept=from_dept,
            to_assigned_to=to_assigned_to,
            actor=actor,
        )

    async def get_current(
        self,
        radicado_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> FlowStepResponse:
        """RF-SEG-08 (no-read-up): resuelve el clearance REAL del llamante (a
        diferencia de `rollback`/`devolver`, que usan `CLEARANCE_SIN_RESTRICCION`
        para su pre-condición interna — esta es la lectura HTTP-facing).

        RF-BUS-10: deja traza best-effort de la consulta SOLO tras una lectura
        exitosa (si no hay paso activo, no hay nada que auditar como "consultado").
        """
        max_clearance = await self._repo.caller_clearance(user_id)
        step = await self._repo.get_current(radicado_id, max_clearance=max_clearance)
        if step is None:
            raise _no_active_step_error(radicado_id)
        await self._audit_hoja_ruta_access(
            radicado_id=radicado_id,
            user_id=user_id,
            canal=canal,
            action=_TRAMITE_ACTION,
            tracking_number=step.tracking_number,
        )
        return _to_response(step)

    async def list_pending(
        self,
        *,
        dept: str | None,
        assigned_to: uuid.UUID | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[FlowStepResponse], int]:
        """RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta)."""
        offset = (page - 1) * size
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_pending(
            dept=dept,
            assigned_to=assigned_to,
            max_clearance=max_clearance,
            offset=offset,
            limit=size,
        )
        await self._audit_bandeja_access(
            filtros={"dept": dept, "assigned_to": str(assigned_to) if assigned_to else None},
            total=total,
            page=page,
            size=size,
            user_id=user_id,
            canal=canal,
        )
        return [_to_response(s) for s in steps], total

    async def complete_step(
        self, step_id: uuid.UUID, payload: FlowStepComplete
    ) -> FlowStepResponse:
        step = await self._repo.complete_step(step_id, payload.notes)
        if step is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "step_not_found",
                    "detail": f"Flow step {step_id} not found",
                    "status": 404,
                },
            )
        await self._publish(_EVT_COMPLETED, step)
        return _to_response(step)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _audit_bandeja_access(
        self,
        *,
        filtros: dict[str, Any],
        total: int,
        page: int,
        size: int,
        user_id: uuid.UUID | None,
        canal: str,
    ) -> None:
        """Traza AGREGADA de una consulta de bandeja (`list_pending`/`inbox`,
        RF-BUS-10; Ley 1712/2014, Decreto 1081/2015): UNA sola entrada por consulta
        EXITOSA, NUNCA por fila del resultado (auditar por fila inflaría la cadena
        de hash proporcional al tamaño de página). Gemelo de
        `DocumentService.audit_search_access`/`ArchiveService._audit_search_access`.

        Gate idéntico a esos servicios: solo audita si el canal es uno de los
        auditables (`X-Canal`, inyectado server-side por el gateway, no forjable
        — D-03) Y hay identidad. Best-effort: nunca degrada la lectura a 500.
        """
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action=_BANDEJA_ACTION,
                object_type=_BANDEJA_OBJECT_TYPE,
                object_ref=_BANDEJA_OBJECT_REF,
                actor=str(user_id),
                canal=canal,
                payload={**filtros, "total": total, "page": page, "size": size},
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (%s) failed", _BANDEJA_ACTION, exc_info=True)

    async def _audit_hoja_ruta_access(
        self,
        *,
        radicado_id: uuid.UUID,
        user_id: uuid.UUID | None,
        canal: str,
        action: str = _HOJA_RUTA_ACTION,
        tracking_number: str | None = None,
    ) -> None:
        """Traza de la lectura de UN radicado concreto (RF-BUS-10): UNA entrada por
        consulta, ``object_ref`` = el radicado consultado (gemelo de
        `ArchiveService._audit_read_access` / `archive.expediente_consultado`).
        Mismo gate canal+identidad y mismo best-effort que `_audit_bandeja_access`.

        ``action`` por defecto es la hoja de ruta completa (`get_history`);
        `get_current`/`get_events` pasan `_TRAMITE_ACTION` para distinguir en el
        historial de auditoría una consulta puntual del trámite de la hoja de
        ruta completa.

        ``object_ref`` prefiere el ``tracking_number`` (clave de negocio, gemela de
        `code`/`tracking_number` que usan archive/document — permite correlacionar
        "quién accedió al radicado X" cross-servicio) y cae al UUID solo cuando la
        lectura resultó VACÍA (radicado sin pasos / por encima del clearance), donde
        no hay tracking disponible pero igual se registra el intento.
        """
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        object_ref = tracking_number or str(radicado_id)
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action=action,
                object_type="radicado",
                object_ref=object_ref,
                actor=str(user_id),
                canal=canal,
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (%s) failed", action, exc_info=True)

    async def _create_step(self, payload: FlowStepCreate, assigned_by: uuid.UUID) -> FlowStep:
        step_number = await self._repo.get_next_step_number(payload.radicado_id)
        # Atomicidad (hallazgo 2.1): cerrar el paso activo, abrir el nuevo y dejar la
        # traza forense commitean juntos. Si audit.append falla, el cambio de paso no
        # queda persistido sin auditoría (las transacciones internas de los repos
        # actúan como savepoints dentro de esta externa).
        async with self._conn.transaction():
            # Complete whichever step is currently active before opening a new one.
            await self._repo.complete_active_step(payload.radicado_id)
            step = await self._repo.create(
                radicado_id=payload.radicado_id,
                tracking_number=payload.tracking_number,
                step_number=step_number,
                from_dept=payload.from_dept,
                to_dept=payload.to_dept,
                assigned_to=payload.assigned_to,
                assigned_by=assigned_by,
                action=payload.action,
                notes=payload.notes,
                doc_type=payload.doc_type,
                due_at=payload.due_at,
            )
            # Auditoría forense transversal (E08 §9, Canal A): además del historial
            # operativo flow_events (Canal B), cada transacción de trámite deja traza
            # legal en public.audit_log.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="workflow-service",
                action=f"workflow.{payload.action}",
                object_type="radicado",
                object_ref=payload.tracking_number,
                actor=str(assigned_by) if assigned_by else None,
                payload={"to_dept": payload.to_dept, "from_dept": payload.from_dept},
            )
        return step

    async def _publish(self, event_type: str, step: FlowStep) -> None:
        # Sobre canónico de orpycamcp_common (ADR-010): los campos de dominio
        # viajan en `payload`. Best-effort: un fallo de Redis nunca aborta la
        # operación de escritura (la BD es la fuente de verdad).
        try:
            redis = get_redis()
            await events.emit(
                redis,
                _STREAM,
                event_type,
                tenant_slug=self._tenant_slug,
                actor=str(step.assigned_by) if step.assigned_by else None,
                dedup_key=f"{step.id}:{event_type}",
                payload={
                    "radicado_id": str(step.radicado_id),
                    "tracking_number": step.tracking_number,
                    "step_number": step.step_number,
                    "from_dept": step.from_dept,
                    "to_dept": step.to_dept,
                    "action": step.action,
                    "status": step.status,
                },
            )
        except Exception:
            pass


def _to_event_response(row: asyncpg.Record) -> FlowEventResponse:
    detalles = row["detalles"]
    if isinstance(detalles, str):
        detalles = json.loads(detalles)
    return FlowEventResponse(
        id=row["id"],
        radicado_id=row["radicado_id"],
        tracking_number=row["tracking_number"],
        tipo_tx=row["tipo_tx"],
        from_dept=row["from_dept"],
        to_dept=row["to_dept"],
        actor=row["actor"],
        comentario=row["comentario"],
        detalles=detalles or {},
        ts=row["ts"],
    )


def _to_response(step: FlowStep) -> FlowStepResponse:
    return FlowStepResponse(
        id=step.id,
        radicado_id=step.radicado_id,
        tracking_number=step.tracking_number,
        step_number=step.step_number,
        from_dept=step.from_dept,
        to_dept=step.to_dept,
        assigned_to=step.assigned_to,
        assigned_by=step.assigned_by,
        action=step.action,
        notes=step.notes,
        status=step.status,
        created_at=step.created_at,
        completed_at=step.completed_at,
        doc_type=step.doc_type,
        due_at=step.due_at,
        semaforo=_semaforo(step.due_at, datetime.now(tz=UTC)),
    )

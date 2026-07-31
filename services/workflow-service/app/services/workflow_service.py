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

from app.core.clearance import CLEARANCE_SIN_RESTRICCION, check_permission
from app.core.database import tenant_schema
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

# Cierre de la primitiva de enumeración de `inbox`/`pending`: ampliar el
# alcance por defecto (pasar `dept`/`assigned_to`) exige UNO de estos dos
# permisos — cualquiera de los dos basta, gemelo del criterio ya usado en
# `reassign_cascade`/`scan_overdue` (administración) salvo que aquí
# PERM_TRAMITAR (permiso ORDINARIO de trámite) también habilita, porque
# consultar la carga de otra dependencia concreta es parte del trabajo normal
# de quien tramita — solo el listado SIN acotar (todo el tenant) queda
# cerrado por defecto.
_SCOPE_WIDENING_PERMS = ("PERM_TRAMITAR", "USUA_PERM_ADMIN")

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
        """Ejecuta una transacción de trámite (RF-FLU-01): valida el tipo, exige el
        permiso que el tipo declara, aplica el efecto de estado y registra el evento
        append-only."""
        tt = await self._repo.get_transaction_type(payload.tipo_tx)
        if tt is None:
            raise TransactionError("unknown_transaction")
        # Enforcement RBAC por-tipo (RF-SEG-03): cada transacción declara en
        # `transaction_types.requires_permission` el permiso atómico que exige
        # (`anular`→PERM_ANULAR, `cerrar_exp`→PERM_CERRAR_EXP, `vobo`→PERM_VOBO…).
        # La migración 007 lo daba por "delegado al gateway", pero el gateway sólo
        # AUTENTICA (inyecta X-User-Id/roles) — nunca autoriza por permiso. Éste es
        # el único punto donde el permiso por-tipo puede hacerse cumplir. `NULL` =
        # transacción abierta (no exige permiso). Fail-closed: sin actor
        # identificado, `check_permission` deniega.
        required_perm = tt["requires_permission"]
        if required_perm:
            await self._require_permission(actor, required_perm)
        latest = await self._repo.get_latest_step(radicado_id)
        if latest is None:
            raise TransactionError("no_flow")
        # Auditoría forense (Canal A, cierre de deuda declarada 2026-07-30):
        # object_ref = tracking_number de `latest` (ya resuelto server-side por
        # `_create_step`/`WorkflowRepository._row_to_model` al crear el paso,
        # NUNCA del cuerpo de la petición — mismo criterio que el Bloqueante 3
        # de `_create_step`). `audit.append` va DENTRO de la misma transacción
        # que envuelve `apply_transaction` (savepoint sobre su tx interna,
        # mismo patrón que `rollback`/`complete_step`): si la traza falla, la
        # transacción de trámite entera revierte.
        async with self._conn.transaction():
            event = await self._repo.apply_transaction(
                latest=latest,
                tipo_tx=payload.tipo_tx,
                target_step_status=tt["target_step_status"],
                actor=actor,
                comentario=payload.comentario,
                detalles=payload.detalles,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.transaccion",
                object_type="radicado",
                object_ref=latest.tracking_number,
                actor=str(actor) if actor else None,
                payload={
                    "tipo_tx": payload.tipo_tx,
                    "comentario": payload.comentario,
                    "target_step_status": tt["target_step_status"],
                    "requires_permission": required_perm,
                },
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

        RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta).

        Alcance por dependencia (cierre de la primitiva de enumeración): sin
        `dept`/`assigned_to`, el llamante ve lo suyo (`own_scope_user_id`,
        `_own_scope_condition`); con cualquiera de los dos, amplía el alcance
        y debe tener `PERM_TRAMITAR`/`USUA_PERM_ADMIN` (`_require_scope_
        widening_permission`) — ambos ejes son mutuamente excluyentes por
        request, ver `WorkflowRepository.list_inbox`."""
        doc_type = _BOX_TO_DOC_TYPE.get(box) if box else None
        offset = (page - 1) * size
        widening_scope = dept is not None or assigned_to is not None
        if widening_scope:
            await self._require_scope_widening_permission(user_id)
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_inbox(
            doc_type=doc_type,
            dept=dept,
            assigned_to=assigned_to,
            own_scope_user_id=None if widening_scope else user_id,
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

    async def rollback(
        self, radicado_id: uuid.UUID, actor: uuid.UUID | None, motivo: str
    ) -> FlowStepResponse:
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

        `motivo` (obligatorio, `RollbackRequest`): queda en DOS trazas atómicas
        con la mutación (mismo criterio que `_create_step`, un solo acto) — el
        `comentario` de `flow_events` (Canal B, hoja de ruta operativa) y el
        `payload` de `public.audit_log` (Canal A, traza legal forense). Antes de
        este contrato el frontend recogía el motivo y lo descartaba (no había
        dónde enviarlo): si `audit.append` falla, la reversión entera revierte —
        no queda un rollback sin motivo auditable.
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
        async with self._conn.transaction():
            reactivated = await self._repo.apply_rollback(
                current=current, previous=previous, actor=actor, comentario=motivo
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="workflow-service",
                action="workflow.rollback",
                object_type="radicado",
                object_ref=current.tracking_number,
                actor=str(actor) if actor else None,
                payload={
                    "from_dept": current.to_dept,
                    "to_dept": previous.to_dept,
                    "motivo": motivo,
                },
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
        # Auditoría forense (Canal A, cierre de deuda declarada 2026-07-30):
        # mismo criterio que `rollback` — `audit.append` DENTRO de la misma
        # transacción que envuelve `apply_devolucion` (savepoint sobre su tx
        # interna). Si la traza falla, la devolución entera revierte: no queda
        # un cambio de dependencia sin asiento forense.
        async with self._conn.transaction():
            new_step = await self._repo.apply_devolucion(
                current=current,
                to_dept=to_dept,
                causal=causal,
                comentario=comentario,
                actor=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.devolver",
                object_type="radicado",
                object_ref=current.tracking_number,
                actor=str(actor) if actor else None,
                payload={
                    "from_dept": current.to_dept,
                    "to_dept": to_dept,
                    "causal": causal,
                    "comentario": comentario,
                },
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
        """Reasigna todos los pasos activos del origen al nuevo responsable (RF-FLU-08).

        Hallazgo C: `to_assigned_to` no se verificaba — un UUID equivocado aparcaba
        toda la carga en un usuario inexistente; los pasos seguían `pending` pero no
        aparecían en NINGUNA bandeja (denegación de servicio silenciosa del trámite).
        Fail-closed: 422 si el destino no existe, antes de tocar `flow_steps`.
        """
        if not await self._repo.user_exists(to_assigned_to):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "to_assigned_to_not_found",
                    "detail": f"Usuario {to_assigned_to} no existe",
                    "status": 422,
                },
            )
        # Auditoría forense (Canal A, cierre de deuda declarada 2026-07-30): esto
        # es un barrido transversal, no la acción sobre UN radicado — no hay
        # tracking_number único que fabricar (haciéndolo se inventaría una
        # identidad falsa en una tabla append-only). `object_type="flow_steps"` +
        # `object_ref=to_assigned_to` (el único identificador estable y NO
        # opcional del payload — `from_assigned_to`/`from_dept` pueden venir
        # ambos vacíos) documentan el barrido en sí; el `payload` lleva el
        # conteo REAL de filas movidas (`reassigned`, devuelto por el repo) —
        # una traza de un barrido masivo que no dice cuántas filas tocó es
        # forensemente inútil. `audit.append` va DENTRO de la misma transacción
        # que envuelve la llamada al repo (savepoint sobre su tx interna, mismo
        # patrón que `rollback`/`devolver`/`execute_transaction`): si la traza
        # falla, ninguna reasignación queda persistida.
        async with self._conn.transaction():
            reassigned = await self._repo.reassign_cascade(
                from_assigned_to=from_assigned_to,
                from_dept=from_dept,
                to_assigned_to=to_assigned_to,
                actor=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.reasignacion_cascada",
                object_type="flow_steps",
                object_ref=str(to_assigned_to),
                actor=str(actor) if actor else None,
                payload={
                    "from_assigned_to": str(from_assigned_to) if from_assigned_to else None,
                    "from_dept": from_dept,
                    "to_assigned_to": str(to_assigned_to),
                    "reassigned": reassigned,
                },
            )
        return reassigned

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
        """RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta).

        Alcance por dependencia: gemelo exacto de `inbox`, ver su docstring."""
        offset = (page - 1) * size
        widening_scope = dept is not None or assigned_to is not None
        if widening_scope:
            await self._require_scope_widening_permission(user_id)
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_pending(
            dept=dept,
            assigned_to=assigned_to,
            own_scope_user_id=None if widening_scope else user_id,
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
        self, step_id: uuid.UUID, payload: FlowStepComplete, actor: uuid.UUID
    ) -> FlowStepResponse:
        """Cierra un paso (Bloqueantes 1+2, RF-SEG-03): `actor` es OBLIGATORIO —
        antes este era el único endpoint de mutación del servicio sin identidad,
        lo que permitía a cualquier tenedor de `PERM_TRAMITAR` cerrar el paso
        activo de OTRA dependencia con solo conocer el `step_id` (enumerable vía
        `GET /workflows/pending`, no acotado por dependencia).

        Autorización + mutación en el MISMO `UPDATE` (ver
        `WorkflowRepository.complete_step`, sin ventana TOCTOU). `audit.append`
        (Canal A) queda DENTRO de la misma `conn.transaction()` que la mutación
        (mismo criterio que `_create_step`): si la traza forense falla, el
        cierre entero revierte — no queda un cierre sin auditar.
        """
        async with self._conn.transaction():
            step = await self._repo.complete_step(step_id, payload.notes, actor)
            if step is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "step_not_found",
                        "detail": f"Flow step {step_id} not found",
                        "status": 404,
                    },
                )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.complete",
                object_type="radicado",
                object_ref=step.tracking_number,
                actor=str(actor) if actor else None,
                payload={"step_id": str(step_id), "to_dept": step.to_dept},
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

    async def _require_permission(self, actor: uuid.UUID | None, perm_name: str) -> None:
        """Eleva 403 si `actor` carece de `perm_name` en el tenant (RF-SEG-03).

        Espejo en la capa de servicio de `clearance.require_permission` (que es una
        dependency de router y no sirve para un permiso RESUELTO en runtime, como el
        de `execute_transaction`). Fija el search_path al tenant antes de la consulta
        RBAC — igual que la dependency — porque `get_db_conn` no lo hace, y deja la
        misma traza de denegación (best-effort) para no romper la simetría forense.
        """
        await self._conn.execute(f'SET search_path TO "{tenant_schema(self._tenant_slug)}", public')
        if await check_permission(self._conn, actor, perm_name):
            return
        if audit is not None and actor is not None:
            try:
                await audit.append_denial(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    actor=str(actor),
                    perm_name=perm_name,
                    min_crud=1,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning("audit.append_denial failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": f"Se requiere el permiso {perm_name}",
                "status": 403,
            },
        )

    async def _require_scope_widening_permission(self, actor: uuid.UUID | None) -> None:
        """Eleva 403 si `actor` carece de `PERM_TRAMITAR` Y de `USUA_PERM_ADMIN`
        (RF-SEG-08 extensión "bandeja acotada por dependencia").

        Se invoca SOLO cuando la petición trae `dept` y/o `assigned_to`
        explícitos en `inbox`/`list_pending` — el alcance POR DEFECTO (sin esos
        parámetros) nunca pasa por aquí, ver `_own_scope_condition`. Deniega
        (no ignora) el intento de ampliar el alcance sin permiso: más simple de
        razonar que "ignorar el parámetro en silencio y degradar a lo propio",
        y consistente con el resto del servicio (fail-closed explícito, p. ej.
        `_require_permission`/`require_permission`).

        Mismo patrón que `_require_permission`: fija el search_path (`get_db_conn`
        no lo hace) y dos permisos posibles en vez de uno — no hay variante de
        `check_permission` para "cualquiera de N", así que se prueba en orden y
        se corta en el primero que otorgue.
        """
        await self._conn.execute(f'SET search_path TO "{tenant_schema(self._tenant_slug)}", public')
        for perm_name in _SCOPE_WIDENING_PERMS:
            if await check_permission(self._conn, actor, perm_name):
                return
        if audit is not None and actor is not None:
            try:
                await audit.append_denial(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    actor=str(actor),
                    perm_name="|".join(_SCOPE_WIDENING_PERMS),
                    min_crud=1,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning("audit.append_denial failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": (
                    "Se requiere PERM_TRAMITAR o USUA_PERM_ADMIN para consultar "
                    "la bandeja de otra dependencia o de otro usuario"
                ),
                "status": 403,
            },
        )

    async def _validate_dept_catalog(self, nombre: str, *, field: str) -> None:
        """422 fail-closed si `nombre` no es una dependencia ACTIVA del catálogo.

        Ver `WorkflowRepository.dependencia_activa` para la justificación completa.
        `field` distingue `to_dept`/`from_dept` en el detalle del error, para que
        el cliente sepa cuál de los dos rechazó.
        """
        if await self._repo.dependencia_activa(nombre):
            return
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "dependencia_desconocida",
                "detail": f"'{nombre}' no es una dependencia activa del catálogo ({field})",
                "field": field,
                "status": 422,
            },
        )

    async def _create_step(self, payload: FlowStepCreate, assigned_by: uuid.UUID) -> FlowStep:
        # Bloqueante 3: el tracking_number del CUERPO nunca es de fiar (campo libre
        # del cliente) — se resuelve SIEMPRE desde `radicados`, que además sirve de
        # comprobación de existencia del radicado (`flow_steps` no tiene FK). Sin
        # esto, un `radicado_id` inventado + el `tracking_number` de OTRO radicado
        # generaban un paso fantasma público (el predicado de clearance no
        # encuentra la fila) y una entrada forense atribuyendo un movimiento sobre
        # el radicado de otra persona.
        tracking_number = await self._repo.get_radicado_tracking_number(payload.radicado_id)
        if tracking_number is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "radicado_not_found",
                    "detail": f"Radicado {payload.radicado_id} not found",
                    "status": 404,
                },
            )
        # Agujero de disponibilidad cerrado: `to_dept`/`from_dept` son texto libre
        # del cliente (VARCHAR(100), sin FK — ver `_own_scope_condition`), y hasta
        # ahora nadie los comprobaba contra el catálogo `dependencias` antes de
        # escribir. Un `to_dept` con una errata o inventado no coincide con el
        # alcance propio de NINGÚN usuario (`_own_scope_condition` correlaciona
        # por nombre exacto contra `dependencias.nombre`): el paso queda
        # `pending` en BD pero invisible en toda bandeja — pérdida silenciosa de
        # un radicado dentro del flujo. Fail-closed ANTES de la transacción: no
        # se escribe ninguna fila si el destino (o el origen, en `transfer`) no
        # es una dependencia activa del catálogo.
        await self._validate_dept_catalog(payload.to_dept, field="to_dept")
        if payload.from_dept is not None:
            await self._validate_dept_catalog(payload.from_dept, field="from_dept")
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
                tracking_number=tracking_number,
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
            # legal en public.audit_log. object_ref = tracking_number RESUELTO desde
            # BD, no el del payload (Bloqueante 3).
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="workflow-service",
                action=f"workflow.{payload.action}",
                object_type="radicado",
                object_ref=tracking_number,
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

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

from app.core.clearance import (
    CLEARANCE_SIN_RESTRICCION,
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
)
from app.core.database import tenant_schema
from app.core.dependencias import (
    dependencia_ambigua,
    dependencia_desconocida,
    dependencia_inconsistente,
)
from app.core.redis_client import get_redis
from app.models.flow import FlowStep
from app.repositories.workflow_repository import (
    CascadeResult,
    EstadoCambio,
    WorkflowRepository,
)
from app.schemas.workflow import (
    FlowEventResponse,
    FlowHistoryResponse,
    FlowStepComplete,
    FlowStepCreate,
    FlowStepResponse,
    TransactionRequest,
    TransactionTypeResponse,
)
from app.services.estado_radicado import ESTADOS_TERMINALES


class RollbackError(Exception):
    """No se puede revertir la última asignación (no hay paso activo o es la inicial)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class YaDistribuidoError(Exception):
    """El reparto de SISTEMA llegó tarde: el radicado ya tiene pasos (RN-8). No es
    un fallo: el reparto automático es idempotente y lo trata como «nada que hacer»."""


class TransactionError(Exception):
    """La transacción de trámite no se puede ejecutar."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_STREAM = "orpycamcp.workflow.events"
_EVT_CREATED = "workflow.step.created"
_EVT_COMPLETED = "workflow.step.completed"
_EVT_ESTADO = "workflow.radicado.estado_cambiado"

_SERVICE = "workflow-service"

# Permiso mínimo de cualquier transacción de trámite. Ver `execute_transaction`:
# es el suelo para los tipos que no declaran el suyo, no un permiso adicional.
_PERMISO_SUELO = "PERM_TRAMITAR"

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
            "detail": "El radicado no tiene ningún paso de flujo activo",
            "status": 404,
        },
    )


def _radicado_terminal_error() -> HTTPException:
    """409 de ADR-028: el radicado está `closed`/`anulado` y no admite actos de
    trámite. Se lanza DENTRO de la transacción del acto, así que lo revierte."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": "radicado_terminal",
            "detail": "El radicado está cerrado o anulado y no admite actos de trámite",
            "status": 409,
        },
    )


class WorkflowService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = WorkflowRepository(conn, tenant_slug)
        self._tenant_slug = tenant_slug
        # Cambios de estado del radicado producidos por el acto en curso; se
        # emiten como evento SOLO tras el commit (best-effort).
        self._cambios: list[EstadoCambio] = []

    def _iniciar_acto(self) -> None:
        """Vacía los cambios de estado de un acto anterior. Un acto que revierte
        (409 terminal, fallo de auditoría) deja aquí cambios que NUNCA se
        confirmaron; sin esto, el siguiente acto de la MISMA instancia los
        emitiría como si hubieran ocurrido."""
        self._cambios = []

    async def _sincronizar(
        self,
        radicado_id: uuid.UUID,
        tracking_number: str,
        *,
        finalizar: bool = False,
    ) -> dict[str, str] | None:
        """Mueve `radicados.status` según el paso activo (ADR-028).

        Se llama DENTRO de la `conn.transaction()` del acto, DESPUÉS de mutar el
        paso y ANTES de `audit.append`: si el radicado es terminal se lanza 409 y
        la transacción entera (paso + evento) revierte. Devuelve el
        `{"antes","despues"}` para el payload del asiento, o `None` si no cambió.
        """
        estado = await self._repo.sincronizar_estado_radicado(radicado_id, finalizar=finalizar)
        if estado is None:
            return None
        antes, despues = estado
        if antes in ESTADOS_TERMINALES:
            raise _radicado_terminal_error()
        if antes == despues:
            return None
        self._cambios.append(
            EstadoCambio(
                radicado_id=radicado_id,
                tracking_number=tracking_number,
                antes=antes,
                despues=despues,
                flow_event_id=self._repo.last_flow_event_id,
            )
        )
        return {"antes": antes, "despues": despues}

    async def _emitir_cambios(self, actor: uuid.UUID | None) -> None:
        """Evento `workflow.radicado.estado_cambiado` por cada cambio, tras el
        commit. Best-effort: la BD es la fuente de verdad, un fallo de Redis no
        aborta el acto."""
        cambios, self._cambios = self._cambios, []
        for c in cambios:
            try:
                await events.emit(
                    get_redis(),
                    _STREAM,
                    _EVT_ESTADO,
                    tenant_slug=self._tenant_slug,
                    actor=str(actor) if actor else None,
                    dedup_key=f"{c.radicado_id}:{c.despues}:{c.flow_event_id}",
                    payload={
                        "radicado_id": str(c.radicado_id),
                        "tracking_number": c.tracking_number,
                        "antes": c.antes,
                        "despues": c.despues,
                        **await self._nivel_payload(c.radicado_id),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort tras commit.
                logger.warning("No se pudo emitir %s", _EVT_ESTADO, exc_info=True)

    async def assign(
        self, payload: FlowStepCreate, assigned_by: uuid.UUID, *, sistema: bool = False
    ) -> FlowStepResponse:
        """Create the first step or re-assign a radicado to a dependencia.

        `sistema=True` SOLO lo pasa el reparto automático al radicar
        (`distribution.auto_distribute`): el actor de sistema no es un usuario, no
        tiene clearance ni tenencia que comprobar. Ninguna ruta HTTP puede pasarlo.
        """
        step = await self._create_step(payload, assigned_by, sistema=sistema)
        await self._publish(_EVT_CREATED, step)
        await self._emitir_cambios(assigned_by)
        return _to_response(step)

    async def transfer(self, payload: FlowStepCreate, assigned_by: uuid.UUID) -> FlowStepResponse:
        """Transfer a radicado from one dependencia to another.

        El ORIGEN ya no lo aporta el cliente (antes `missing_from_dept` si faltaba): lo
        deriva el servidor del paso activo (ADR-032) y el cliente solo puede
        CONFIRMARLO (409 `origen_desactualizado` si ya no coincide)."""
        step = await self._create_step(payload, assigned_by)
        await self._publish(_EVT_CREATED, step)
        await self._emitir_cambios(assigned_by)
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
        self._iniciar_acto()
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
        # `NULL` ya NO significa «transacción abierta». Significaba eso, y era el
        # último hueco de esta ruta: `transaction_types` es un catálogo del
        # tenant, así que una fila con `requires_permission` nulo —sembrada por
        # una migración futura, o por alguien con acceso a la base— habría
        # quedado ejecutable por cualquier autenticado de la entidad, mientras
        # `assign`, `transfer` y `complete` exigen `PERM_TRAMITAR`. Hoy los 12
        # tipos sembrados declaran su permiso, de modo que este suelo no cambia
        # nada observable; existe para que dejar la columna vacía no sea una
        # manera silenciosa de abrir la puerta.
        #
        # El suelo es `PERM_TRAMITAR` y no un gate de router porque el permiso
        # aquí es POR TIPO: exigir `PERM_TRAMITAR` a todas rompería a quien tiene
        # `PERM_VOBO` para dar vistos buenos y nada más, que es un reparto
        # legítimo.
        required_perm = tt["requires_permission"] or _PERMISO_SUELO
        await self._require_permission(actor, required_perm)
        # Clearance REAL del actor (RF-SEG-08): va al `WHERE` de cada lectura de
        # paso de abajo. Un radicado por encima de él es «sin flujo» (`no_flow`),
        # igual que uno inexistente: ninguna transacción (con o sin tenencia)
        # devuelve el `tracking_number`/`to_dept` de un reservado ajeno.
        nivel = await self._repo.clearance_para_acto(actor)
        # Auditoría forense (Canal A, cierre de deuda declarada 2026-07-30):
        # object_ref = tracking_number de `latest` (ya resuelto server-side por
        # `_create_step`/`WorkflowRepository._row_to_model` al crear el paso,
        # NUNCA del cuerpo de la petición — mismo criterio que el Bloqueante 3
        # de `_create_step`). `audit.append` va DENTRO de la misma transacción
        # que envuelve `apply_transaction` (savepoint sobre su tx interna,
        # mismo patrón que `rollback`/`complete_step`): si la traza falla, la
        # transacción de trámite entera revierte.
        async with self._conn.transaction():
            # Cerrojo por radicado ANTES de leer el paso: serializa este acto con
            # cualquier otro que mueva el mismo radicado (ADR-028).
            await self._repo.lock_radicado(radicado_id)
            latest = await self._repo.get_latest_step(radicado_id, max_clearance=nivel)
            if latest is None:
                raise TransactionError("no_flow")
            # Tenencia (ADR-028): `requires_holder` cierra el hueco por el que
            # cualquiera con PERM_TRAMITAR podía marcar como leído un radicado
            # ajeno. No poseedor => el MISMO 404 `no_active_step` que "no hay
            # paso activo" (no-oráculo).
            #
            # Toda transacción que MUEVE el paso (`target_step_status`) exige
            # tenencia, lo declare o no su fila del catálogo: el catálogo es
            # configurable por el tenant, y una fila nueva que cancele o cierre un
            # paso sin la bandera permitiría a cualquiera con el permiso mover un
            # trámite ajeno. Además el gate va ANTES de `_sincronizar`: así el 409
            # `radicado_terminal` no se le revela a quien no posee nada (oráculo).
            if (
                tt["requires_holder"] or tt["target_step_status"] is not None
            ) and not await self._repo.es_poseedor(radicado_id, actor, max_clearance=nivel):
                raise _no_active_step_error(radicado_id)
            event = await self._repo.apply_transaction(
                latest=latest,
                tipo_tx=payload.tipo_tx,
                target_step_status=tt["target_step_status"],
                actor=actor,
                comentario=payload.comentario,
                detalles=payload.detalles,
            )
            # Solo las transacciones que mueven el paso (`target_step_status`)
            # sincronizan el estado; informar/agendar siguen permitidas incluso
            # sobre un radicado terminal.
            estado = None
            if tt["target_step_status"] is not None:
                estado = await self._sincronizar(radicado_id, latest.tracking_number)
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
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
            )
        await self._emitir_cambios(actor)
        return _to_event_response(event)

    async def inbox(
        self,
        *,
        box: str | None,
        dept_code: int | None = None,
        dept: str | None = None,
        assigned_to: uuid.UUID | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[FlowStepResponse], int]:
        """Bandeja tipada (RF-FLU-03): box ∈ entrada|salida|internos → doc_type E|S|I.

        RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta).

        Alcance por dependencia (cierre de la primitiva de enumeración): sin
        `dept_code`/`assigned_to`, el llamante ve lo suyo (`own_scope_user_id`,
        `_own_scope_condition`); con cualquiera de los dos, amplía el alcance
        y debe tener `PERM_TRAMITAR`/`USUA_PERM_ADMIN` (`_require_scope_
        widening_permission`) — ambos ejes son mutuamente excluyentes por
        request, ver `WorkflowRepository.list_inbox`."""
        doc_type = _BOX_TO_DOC_TYPE.get(box) if box else None
        offset = (page - 1) * size
        widening_scope = dept_code is not None or dept is not None or assigned_to is not None
        if widening_scope:
            await self._require_scope_widening_permission(user_id)
        # `dept` (nombre) es el ALIAS deprecado de `dept_code` (ADR-032): ambiguo es 422
        # —antes devolvía una lista vacía que parecía «sin trámite»—.
        dept_code = await self._filtro_dependencia(dept_code, dept, field="dept")
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_inbox(
            doc_type=doc_type,
            dept_code=dept_code,
            assigned_to=assigned_to,
            own_scope_user_id=None if widening_scope else user_id,
            max_clearance=max_clearance,
            offset=offset,
            limit=size,
        )
        await self._audit_bandeja_access(
            filtros={
                "box": box,
                "dept_code": dept_code,
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
                            "to_dept_code": s.to_dept_code,
                            "assigned_to": str(s.assigned_to) if s.assigned_to else None,
                            "due_at": s.due_at.isoformat() if s.due_at else None,
                            **await self._nivel_payload(s.radicado_id),
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

        Clearance REAL del actor en el `WHERE` de la lectura del paso (RF-SEG-08):
        tener el paso no basta si el radicado está por encima de su nivel (antes
        iba con `CLEARANCE_SIN_RESTRICCION`). Paso y tenencia se leen DENTRO de la
        transacción, tras el cerrojo, y `apply_rollback` repite la condición.

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
        self._iniciar_acto()
        nivel = await self._repo.clearance_para_acto(actor)
        async with self._conn.transaction():
            await self._repo.lock_radicado(radicado_id)
            # Lectura del paso Y de la tenencia DENTRO de la transacción y tras el
            # cerrojo (antes iban fuera: otro acto podía mover el paso entre la
            # comprobación y la escritura). `apply_rollback` repite la condición.
            current = await self._repo.get_active_step_for_update(radicado_id, max_clearance=nivel)
            if (
                current is None
                or current.status != "pending"
                or not await self._repo.es_poseedor(radicado_id, actor, max_clearance=nivel)
            ):
                # No-oráculo: mismo código que "no hay paso activo" (ver docstring).
                raise RollbackError("no_active_step")
            previous = await self._repo.get_previous_step(radicado_id, current.step_number)
            if previous is None:
                raise RollbackError("cannot_rollback_initial")
            try:
                reactivated = await self._repo.apply_rollback(
                    current=current,
                    previous=previous,
                    actor=actor,
                    comentario=motivo,
                    max_clearance=nivel,
                )
            except asyncpg.CheckViolationError as exc:
                # `ck_flow_steps_vivo_con_codigo`: el paso anterior es histórico y su
                # nombre nunca resolvió a un código (migración 019): reactivarlo dejaría
                # un paso vivo que ninguna bandeja ve. Falla cerrado.
                raise RollbackError("paso_anterior_sin_codigo") from exc
            if reactivated is None:
                raise RollbackError("no_active_step")
            # ADR-028: el paso anterior vuelve a `pending` => `distributed`.
            estado = await self._sincronizar(radicado_id, current.tracking_number)
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
                    "from_dept_code": current.to_dept_code,
                    "to_dept_code": previous.to_dept_code,
                    "motivo": motivo,
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
            )
        await self._emitir_cambios(actor)
        return _to_response(reactivated)

    async def devolver(
        self,
        radicado_id: uuid.UUID,
        *,
        to_dept_code: int | None = None,
        to_dept: str | None = None,
        causal: str,
        comentario: str | None,
        actor: uuid.UUID | None,
    ) -> FlowStepResponse:
        """Devuelve el radicado a su remitente/origen con causal (re-enrutado).

        Autorización por tenencia (fiel al legado): solo quien tenga el radicado
        asignado actualmente puede devolverlo.  No hay permiso RBAC global para
        esta acción (RF-FLU / Orfeo legacy).

        Tenencia = `_holder_condition` (la misma de `finalizar`/`complete_step`: asignado
        al actor, o paso de su dependencia sin dueño) más el clearance REAL del actor
        en el `WHERE` (RF-SEG-08). Se lee dentro de la transacción tras el cerrojo y
        `apply_devolucion` la repite.

        No-oráculo (M2/RF-SEG-08, mismo criterio que `rollback`): un no-tenedor
        recibe el MISMO 404 `no_active_step` que el caso "sin paso activo" — YA NO
        se distingue con un 403 `not_current_holder`, porque ese 403 le permitía a
        cualquier autenticado del tenant sondear UUIDs y distinguir "existe un
        paso activo ajeno" (403) de "no hay paso/no existe" (404), filtrando la
        existencia/asignación de un radicado clasificado sin necesitar clearance
        para leerlo. Se pierde el mensaje específico "solo el tenedor puede
        devolverlo" — es el precio del no-oráculo.
        """
        self._iniciar_acto()
        nivel = await self._repo.clearance_para_acto(actor)
        # Destino por CÓDIGO (ADR-032); el nombre guardado es el del catálogo.
        to_dept_code, to_dept = await self._destino(to_dept_code, to_dept, field="to_dept")
        # Auditoría forense (Canal A, cierre de deuda declarada 2026-07-30):
        # mismo criterio que `rollback` — `audit.append` DENTRO de la misma
        # transacción que envuelve `apply_devolucion` (savepoint sobre su tx
        # interna). Si la traza falla, la devolución entera revierte: no queda
        # un cambio de dependencia sin asiento forense.
        async with self._conn.transaction():
            await self._repo.lock_radicado(radicado_id)
            # Paso y tenencia tras el cerrojo (sin ventana TOCTOU) y con UNA sola
            # definición de poseedor (`_holder_condition`); `apply_devolucion` la repite.
            current = await self._repo.get_active_step_for_update(radicado_id, max_clearance=nivel)
            if current is None or not await self._repo.es_poseedor(
                radicado_id, actor, max_clearance=nivel
            ):
                raise _no_active_step_error(radicado_id)
            new_step = await self._repo.apply_devolucion(
                current=current,
                to_dept=to_dept,
                to_dept_code=to_dept_code,
                causal=causal,
                comentario=comentario,
                actor=actor,
                max_clearance=nivel,
            )
            if new_step is None:
                raise _no_active_step_error(radicado_id)
            # ADR-028: el paso nuevo nace `pending` => `distributed`.
            estado = await self._sincronizar(radicado_id, current.tracking_number)
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
                    "from_dept_code": current.to_dept_code,
                    "to_dept_code": to_dept_code,
                    "causal": causal,
                    "comentario": comentario,
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
            )
        await self._emitir_cambios(actor)
        return _to_response(new_step)

    async def reassign_cascade(
        self,
        *,
        from_assigned_to: uuid.UUID | None,
        from_dept_code: int | None = None,
        from_dept: str | None = None,
        to_assigned_to: uuid.UUID,
        actor: uuid.UUID | None,
    ) -> CascadeResult:
        """Reasigna todos los pasos activos del origen al nuevo responsable (RF-FLU-08).

        Hallazgo C: `to_assigned_to` no se verificaba — un UUID equivocado aparcaba
        toda la carga en un usuario inexistente; los pasos seguían `pending` pero no
        aparecían en NINGUNA bandeja (denegación de servicio silenciosa del trámite).
        Fail-closed: 422 si el destino no existe, antes de tocar `flow_steps`.
        """
        self._iniciar_acto()
        from_dept_code = await self._filtro_dependencia(from_dept_code, from_dept, field="from_dept")
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
            result = await self._repo.reassign_cascade(
                from_assigned_to=from_assigned_to,
                from_dept_code=from_dept_code,
                to_assigned_to=to_assigned_to,
                actor=actor,
            )
            # ADR-028: un asiento `estado_radicado` por radicado cuyo estado cambió
            # (la reasignación devuelve el paso a `pending` => `distributed`).
            for c in result.cambios:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="workflow.estado_radicado",
                    object_type="radicado",
                    object_ref=c.tracking_number,
                    actor=str(actor) if actor else None,
                    payload={
                        "antes": c.antes,
                        "despues": c.despues,
                        "origen": "reasignacion_cascada",
                    },
                    canal=audit.CANAL_PETICION,
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
                    "from_dept_code": from_dept_code,
                    "to_assigned_to": str(to_assigned_to),
                    "reassigned": result.reassigned,
                    "skipped_terminal": result.skipped_terminal,
                },
                canal=audit.CANAL_PETICION,
            )
        self._cambios = list(result.cambios)
        await self._emitir_cambios(actor)
        return result

    async def get_current(
        self,
        radicado_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> FlowStepResponse:
        """RF-SEG-08 (no-read-up): resuelve el clearance REAL del llamante (a
        diferencia del reparto de sistema, que no tiene usuario — esta es la lectura
        HTTP-facing).

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
        dept_code: int | None = None,
        dept: str | None = None,
        assigned_to: uuid.UUID | None,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> tuple[list[FlowStepResponse], int]:
        """RF-SEG-08 (no-read-up) + RF-BUS-10 (traza agregada de consulta).

        Alcance por dependencia: gemelo exacto de `inbox`, ver su docstring."""
        offset = (page - 1) * size
        widening_scope = dept_code is not None or dept is not None or assigned_to is not None
        if widening_scope:
            await self._require_scope_widening_permission(user_id)
        dept_code = await self._filtro_dependencia(dept_code, dept, field="dept")
        max_clearance = await self._repo.caller_clearance(user_id)
        steps, total = await self._repo.list_pending(
            dept_code=dept_code,
            assigned_to=assigned_to,
            own_scope_user_id=None if widening_scope else user_id,
            max_clearance=max_clearance,
            offset=offset,
            limit=size,
        )
        await self._audit_bandeja_access(
            filtros={
                "dept_code": dept_code,
                "assigned_to": str(assigned_to) if assigned_to else None,
            },
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
        not_found = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "step_not_found",
                "detail": "No existe el paso de flujo indicado",
                "status": 404,
            },
        )
        self._iniciar_acto()
        nivel = await self._repo.clearance_para_acto(actor)
        # ADR-028: el radicado se resuelve ANTES de abrir la transacción para poder
        # tomar su cerrojo; un paso inexistente da el mismo 404 de siempre.
        existing = await self._repo.get_by_id(step_id)
        if existing is None:
            raise not_found
        async with self._conn.transaction():
            await self._repo.lock_radicado(existing.radicado_id)
            step = await self._repo.complete_step(step_id, payload.notes, actor, max_clearance=nivel)
            if step is None:
                raise not_found
            # Completar sin marcar leído deja `distributed`: declarado (ADR-028).
            estado = await self._sincronizar(step.radicado_id, step.tracking_number)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.complete",
                object_type="radicado",
                object_ref=step.tracking_number,
                actor=str(actor) if actor else None,
                payload={
                    "step_id": str(step_id),
                    "to_dept": step.to_dept,
                    "to_dept_code": step.to_dept_code,
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
            )
        await self._publish(_EVT_COMPLETED, step)
        await self._emitir_cambios(actor)
        return _to_response(step)

    async def finalizar(
        self,
        radicado_id: uuid.UUID,
        *,
        comentario: str,
        motivo_cierre: str,
        actor: uuid.UUID,
    ) -> FlowStepResponse:
        """«Finalizar trámite» (ADR-028): único acto que cierra un radicado.

        Solo el POSEEDOR del paso activo — o, si no hay paso activo, quien poseía
        el último paso ya completado (completó el suyo y no repartió: exigir un
        paso activo obligaba a inventar un reparto ficticio para poder cerrar).
        Cierra ese paso como `completed` y el radicado pasa a `closed` en la misma
        transacción, con `flow_events` (`tipo_tx='finalizar'`) y asiento. Un
        no-poseedor, o un radicado ya terminal, recibe el MISMO 404
        `no_active_step` (no-oráculo: no se distingue «cerrado» de «no es tuyo»
        sin clearance).

        `motivo_cierre` (tipificado, obligatorio) y la evidencia del cierre
        (`tiene_respuesta`, `fuera_de_plazo`, `en_expediente`, paso y evento) van
        en el evento y en el asiento: quien audite un cierre necesita saber si el
        trámite se respondió y si se cerró a tiempo sin reconstruirlo.
        """
        self._iniciar_acto()
        nivel = await self._repo.clearance_para_acto(actor)
        async with self._conn.transaction():
            await self._repo.lock_radicado(radicado_id)
            current = await self._repo.get_active_step_for_update(radicado_id, max_clearance=nivel)
            if current is None:
                current = await self._repo.ultimo_paso_si_lo_poseia(
                    radicado_id, actor, max_clearance=nivel
                )
                if current is None:
                    raise _no_active_step_error(radicado_id)
            elif not await self._repo.es_poseedor(radicado_id, actor, max_clearance=nivel):
                raise _no_active_step_error(radicado_id)
            step = await self._repo.apply_finalizar(
                current=current,
                actor=actor,
                comentario=comentario,
                motivo_cierre=motivo_cierre,
            )
            flow_event_id = self._repo.last_flow_event_id
            estado = await self._sincronizar(
                radicado_id, current.tracking_number, finalizar=True
            )
            contexto = await self._repo.contexto_cierre(radicado_id)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="workflow.finalizar",
                object_type="radicado",
                object_ref=current.tracking_number,
                actor=str(actor),
                payload={
                    "to_dept": current.to_dept,
                    "to_dept_code": current.to_dept_code,
                    "comentario": comentario,
                    "motivo_cierre": motivo_cierre,
                    "step_id": str(current.id),
                    "flow_event_id": str(flow_event_id) if flow_event_id else None,
                    "tiene_respuesta": contexto["tiene_respuesta"],
                    "en_expediente": contexto["en_expediente"],
                    # El plazo del paso manda si existe; si no, el vencimiento
                    # legal del radicado. `None` solo si no consta ninguno.
                    "fuera_de_plazo": (
                        current.due_at < datetime.now(tz=UTC)
                        if current.due_at
                        else contexto["radicado_vencido"]
                    ),
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
            )
        await self._publish(_EVT_COMPLETED, step)
        await self._emitir_cambios(actor)
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
                # Mismo mensaje veraz que el gate de ruta: aquí el umbral es 1, así
                # que la causa normal es la ausencia del permiso — pero un nivel 0
                # explícito también existe, y decirlo es mejor que insinuar que
                # falta una concesión que sí está.
                "detail": mensaje_denegacion(
                    perm_name, 1, await nivel_efectivo(self._conn, actor, perm_name)
                ),
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

    async def _destino(
        self, codigo: int | None, nombre: str | None, *, field: str
    ) -> tuple[int, str]:
        """(código, nombre del catálogo) del DESTINO de un paso — 422 fail-closed.

        El código manda; el NOMBRE es solo el alias deprecado (ADR-032) y se resuelve con
        el criterio único de tenant-service (`resolver_codigo_dependencia`, solo
        dependencias ACTIVAS con ese nombre ACTUAL): 1 -> su código; >=2 -> 422
        `dependencia_ambigua` (jamás «la de menor código»); 0 -> 422
        `dependencia_desconocida`. Si llegan los dos deben concordar (422
        `dependencia_inconsistente`). El nombre que se guarda es SIEMPRE el del catálogo.

        Un destino inexistente o inactivo crea un paso que no coincide con el alcance de
        NADIE (`pending` en BD pero invisible en toda bandeja): pérdida silenciosa de un
        trámite oficial. Va ANTES de escribir; el 422 revierte la transacción.
        """
        if codigo is None:
            if nombre is None:
                raise dependencia_desconocida(field, None)
            cod, candidatos = await self._repo.resolver_alias(nombre, historico=False)
            if candidatos >= 2:
                raise dependencia_ambigua(field, nombre, candidatos)
            if cod is None:
                raise dependencia_desconocida(field, nombre)
            codigo = cod
        catalogo = await self._repo.dependencia_activa(codigo)
        if catalogo is None:
            raise dependencia_desconocida(field, codigo)
        if nombre is not None and nombre != catalogo:
            raise dependencia_inconsistente(field, codigo, nombre, catalogo)
        return codigo, catalogo

    async def _filtro_dependencia(
        self, codigo: int | None, nombre: str | None, *, field: str
    ) -> int | None:
        """Código de un FILTRO de dependencia (bandeja, cascada). El alias por nombre se
        resuelve también con el historial (una lectura no debe perder los pasos de una
        dependencia renombrada o dada de baja); ambiguo es 422, no una lista vacía."""
        if codigo is None and nombre is None:
            return None
        if nombre is not None:
            cod, candidatos = await self._repo.resolver_alias(nombre, historico=True)
            if candidatos >= 2:
                raise dependencia_ambigua(field, nombre, candidatos)
            if cod is None:
                raise dependencia_desconocida(field, nombre)
            if codigo is not None and cod != codigo:
                raise dependencia_inconsistente(field, codigo, nombre, str(cod))
            return cod
        return codigo

    async def _origen_del_paso(
        self, radicado_id: uuid.UUID, payload: FlowStepCreate
    ) -> tuple[str | None, int | None]:
        """(nombre, código) del ORIGEN de un paso nuevo: la dependencia del paso activo.

        El cliente ya no aporta el origen (antes `from_dept: step.to_dept` leído de un
        estado que podía estar rancio). Si aun así lo envía y el paso activo ya es de
        otra dependencia -> 409 `origen_desactualizado` (control optimista): el traslado
        que el usuario ve en pantalla ya no es el que se haría. Sin paso activo no hay
        origen que confirmar.
        """
        activo = await self._repo.paso_activo(radicado_id)
        if activo is None:
            return None, None
        afirmado = payload.from_dept_code
        if afirmado is None and payload.from_dept is not None:
            cod, _ = await self._repo.resolver_alias(payload.from_dept, historico=True)
            afirmado = cod
            if afirmado is None and payload.from_dept != activo.to_dept:
                afirmado = -1  # nombre que no resuelve y no es el del paso: no concuerda
        if afirmado is not None and afirmado != activo.to_dept_code:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "origen_desactualizado",
                    "detail": (
                        "El radicado ya no está en la dependencia de origen indicada; "
                        "recargue el trámite"
                    ),
                    "status": 409,
                },
            )
        return activo.to_dept, activo.to_dept_code

    async def _create_step(
        self, payload: FlowStepCreate, assigned_by: uuid.UUID, *, sistema: bool = False
    ) -> FlowStep:
        self._iniciar_acto()
        # Clearance REAL del actor (RF-SEG-08): abrir o mover un paso sobre un
        # radicado reservado desde un nivel inferior es un write-up, y el 404 que
        # lo corta debe ser indistinguible de «no existe». Solo el reparto
        # automático (actor de sistema, sin usuario) va sin restricción.
        max_clearance = (
            CLEARANCE_SIN_RESTRICCION
            if sistema
            else await self._repo.clearance_para_acto(assigned_by)
        )
        # El número de paso se resuelve DENTRO de la transacción, más abajo, y no
        # aquí: calcularlo antes deja una ventana en la que dos peticiones
        # simultáneas obtienen el mismo `MAX+1`, cierran el paso activo las dos y
        # abren dos pasos con el mismo número. El radicado acaba con dos pasos
        # activos, y a partir de ahí «quién lo tiene» deja de tener una respuesta.
        # Atomicidad (hallazgo 2.1): cerrar el paso activo, abrir el nuevo y dejar la
        # traza forense commitean juntos. Si audit.append falla, el cambio de paso no
        # queda persistido sin auditoría (las transacciones internas de los repos
        # actúan como savepoints dentro de esta externa).
        async with self._conn.transaction():
            # Cerrojo por RADICADO, dentro de la transacción: serializa las
            # aperturas de paso de ESTE radicado sin bloquear las de los demás.
            # Es el mismo patrón con el que se serializa la cadena de auditoría.
            await self._repo.lock_radicado(payload.radicado_id)
            # Bloqueante 3: el tracking_number del CUERPO nunca es de fiar (campo
            # libre del cliente) — se resuelve SIEMPRE desde `radicados`, que además
            # comprueba existencia (`flow_steps` no tiene FK) y, EN EL MISMO
            # `WHERE`, el no-read-up. Va DENTRO de la transacción y tras el cerrojo:
            # lo que se lee es lo que se escribe, sin ventana entre medias. Sin
            # esto, un `radicado_id` inventado + el `tracking_number` de OTRO
            # radicado generaban un paso fantasma público y una entrada forense
            # atribuyendo un movimiento sobre el radicado de otra persona.
            tracking_number = await self._repo.get_radicado_tracking_number(
                payload.radicado_id, max_clearance=max_clearance
            )
            if tracking_number is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "radicado_not_found",
                        "detail": "No existe el radicado indicado",
                        "status": 404,
                    },
                )
            # Tenencia: con un paso activo, SOLO su poseedor puede repartirlo o
            # trasladarlo. Sin esto B se autoasignaba cualquier radicado ajeno
            # (y luego `finalizar` lo cerraba). Mismo 404 neutro que «no hay paso».
            # Redistribuir el paso de OTRO es la reasignación en cascada
            # (`USUA_PERM_ADMIN`); no se expone por `assign`/`transfer`. Va ANTES
            # de `_sincronizar`, así el 409 `radicado_terminal` tampoco se revela.
            if sistema:
                # RN-8: la idempotencia del reparto automático se decide AQUÍ, tras
                # el cerrojo (la guarda de `auto_distribute` va fuera de él y dos
                # entregas simultáneas del mismo evento la pasaban las dos).
                if await self._repo.get_next_step_number(payload.radicado_id) > 1:
                    raise YaDistribuidoError(str(payload.radicado_id))
            else:
                tenencia = await self._repo.tenencia(payload.radicado_id, assigned_by)
                if tenencia.hay_activo:
                    if not tenencia.poseedor:
                        raise _no_active_step_error(payload.radicado_id)
                # Sin paso activo tampoco es de cualquiera: con historial, el
                # poseedor del último paso; sin ninguno, quien radicó. Mismo 404.
                elif not await self._repo.puede_repartir_sin_paso_activo(
                    payload.radicado_id, assigned_by, max_clearance=max_clearance
                ):
                    raise _no_active_step_error(payload.radicado_id)
            # Agujero de disponibilidad cerrado: `to_dept`/`from_dept` son texto libre
            # del cliente (VARCHAR(100), sin FK — ver `_own_scope_condition`), y hasta
            # ahora nadie los comprobaba contra el catálogo `dependencias` antes de
            # escribir. Un `to_dept` con una errata o inventado no coincide con el
            # alcance propio de NINGÚN usuario (`_own_scope_condition` correlaciona
            # por nombre exacto contra `dependencias.nombre`): el paso queda
            # `pending` en BD pero invisible en toda bandeja — pérdida silenciosa de
            # un radicado dentro del flujo. Fail-closed ANTES de escribir: no
            # se escribe ninguna fila si el destino (o el origen, en `transfer`) no
            # es una dependencia activa del catálogo (el 422 revierte la transacción,
            # que hasta aquí solo ha tomado cerrojos). Va DESPUÉS de resolver el
            # radicado y la tenencia: un radicado inaccesible da siempre el mismo 404,
            # sin importar si el cuerpo trae además una dependencia inválida.
            to_dept_code, to_dept = await self._destino(
                payload.to_dept_code, payload.to_dept, field="to_dept"
            )
            from_dept, from_dept_code = await self._origen_del_paso(payload.radicado_id, payload)
            step_number = await self._repo.get_next_step_number(payload.radicado_id)
            # Complete whichever step is currently active before opening a new one.
            await self._repo.complete_active_step(payload.radicado_id)
            step = await self._repo.create(
                radicado_id=payload.radicado_id,
                tracking_number=tracking_number,
                step_number=step_number,
                from_dept=from_dept,
                to_dept=to_dept,
                from_dept_code=from_dept_code,
                to_dept_code=to_dept_code,
                assigned_to=payload.assigned_to,
                assigned_by=assigned_by,
                action=payload.action,
                notes=payload.notes,
                doc_type=payload.doc_type,
                due_at=payload.due_at,
            )
            # ADR-028: el radicado pasa a `distributed` en la MISMA transacción
            # que el paso (409 y reversión total si es terminal).
            estado = await self._sincronizar(payload.radicado_id, tracking_number)
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
                payload={
                    "to_dept": to_dept,
                    "from_dept": from_dept,
                    "to_dept_code": to_dept_code,
                    "from_dept_code": from_dept_code,
                    **({"estado_radicado": estado} if estado else {}),
                },
                canal=audit.CANAL_PETICION,
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
                    # Ambos campos (ADR-010 aditivo): el consumidor prefiere el CÓDIGO y
                    # cae al nombre solo para eventos anteriores a la migración 019.
                    "from_dept_code": step.from_dept_code,
                    "to_dept_code": step.to_dept_code,
                    "action": step.action,
                    "status": step.status,
                    **await self._nivel_payload(step.radicado_id),
                },
            )
        except Exception:
            pass

    async def _nivel_payload(self, radicado_id: uuid.UUID) -> dict[str, int]:
        """`nivel_seguridad` del radicado en el sobre del evento (vacío si no consta).

        Sin él, el webhook no puede decidir si el tracking y la ruta de
        dependencias de un reservado pueden salir de la entidad: notification
        trata la ausencia como clasificado.
        """
        nivel = await self._repo.nivel_radicado(radicado_id)
        return {"nivel_seguridad": nivel} if nivel is not None else {}


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
        from_dept_code=row["from_dept_code"],
        to_dept_code=row["to_dept_code"],
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
        from_dept_code=step.from_dept_code,
        to_dept_code=step.to_dept_code,
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

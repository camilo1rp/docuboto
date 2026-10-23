# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Desclasificación de un radicado CLASIFICADO con segunda persona (C4, ADR-039).

Bajar el nivel de un radicado desde `NIVEL_DOBLE_FIRMA` (3, información clasificada,
Ley 1712 art. 18) no lo decide una sola persona: una la SOLICITA (con motivo y, si el
destino es reservada, causal/fundamento/plazo) y OTRA, con clearance suficiente y el
permiso de decisión (`PERM_RECLASIFICAR` crud 4), la APRUEBA. **Aprobar ejecuta**: baja
el nivel en la misma transacción, asienta y encola el evento `reclassified`. Ningún otro
acto (solicitar, rechazar, retirar, caducar) emite evento: el acceso no cambia.

Invariantes:
- El clearance va en el `WHERE` de cada lectura y de cada decisión; `None` -> 404 neutro.
- La separación de funciones vive en el `WHERE` del UPDATE Y en un CHECK de la tabla.
- Orden de cerrojos: primero el radicado (`FOR UPDATE`), luego la solicitud; el PATCH y
  la solicitud toman el mismo orden, así que no hay interbloqueo.
- Cada acto asienta en `public.audit_log` DENTRO de la misma transacción.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.causales_reserva import NIVEL_DOBLE_FIRMA, tope_reserva
from app.core.clearance import resolve_caller_clearance
from app.repositories.document_repository import DocumentRepository
from app.repositories.reclasificacion_repository import ReclasificacionRepository
from app.schemas.desclasificacion import (
    DesclasificacionAprobada,
    DesclasificacionPendiente,
    DesclasificacionRequest,
    DesclasificacionResponse,
)
from app.services import outbox

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
_STREAM = "orpycamcp.document.events"


def _error(status: int, codigo: str, detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"error": codigo, "detail": detalle, "status": status}
    )


def _no_encontrado(clave: object) -> HTTPException:
    # El MISMO 404 para «no existe» y «no lo alcanza» (RF-SEG-08): sin oráculo.
    return _error(404, "solicitud_not_found", str(clave))


class DesclasificacionService:
    def __init__(
        self, conn: asyncpg.Connection, tenant_slug: str, redis: Any | None = None
    ) -> None:
        self._conn = conn
        self._slug = tenant_slug
        self._repo = ReclasificacionRepository(conn)
        self._docs = DocumentRepository(conn, tenant_slug)
        self._redis = redis

    # ---- helpers ----------------------------------------------------------------

    async def _asentar(
        self,
        accion: str,
        *,
        tracking: str,
        actor: uuid.UUID | None,
        payload: dict[str, Any],
        canal: str = audit.CANAL_PETICION,
    ) -> str:
        return await audit.append(
            self._conn,
            tenant_slug=self._slug,
            service=_SERVICE,
            action=accion,
            object_type="radicado",
            object_ref=tracking,
            actor=str(actor) if actor else None,
            payload=payload,
            canal=canal,
        )

    @staticmethod
    def _verificar_tope(nivel: int, plazo: int | None, meses_transcurridos: int) -> None:
        """422 si el destino reservado excede los 15 años desde la GENERACIÓN."""
        tope = tope_reserva(nivel, plazo, meses_transcurridos)
        if tope is not None:
            raise _error(422, tope[0], tope[1])

    @staticmethod
    def _payload(fila: asyncpg.Record) -> dict[str, Any]:
        return {
            "solicitud_id": str(fila["id"]),
            "nivel_anterior": fila["nivel_anterior"],
            "nivel_nuevo": fila["nivel_nuevo"],
            "causal_reserva": fila["causal_reserva"],
            "solicitado_por": str(fila["solicitado_por"]),
        }

    async def _caducar(self, radicado_id: uuid.UUID, tracking: str) -> None:
        """Materializa lo vencido y lo asienta (actor NULL, canal sistema)."""
        for fila in await self._repo.caducar_vencidas(radicado_id):
            await self._asentar(
                "document.desclasificacion_caducada",
                tracking=tracking,
                actor=None,
                payload=self._payload(fila),
                canal="system",
            )

    async def _fallo_de_decision(
        self, solicitud_id: uuid.UUID, actor: uuid.UUID, clearance: int, *, otra_persona: bool
    ) -> HTTPException:
        """Explica por qué un UPDATE de decisión no aplicó. SOLO explica: la guarda
        fue el `WHERE`. Lleva el gate de clearance -> 404 neutro si no lo alcanza."""
        fila = await self._repo.diagnostico(solicitud_id, clearance)
        if fila is None:
            return _no_encontrado(solicitud_id)
        if fila["estado"] != "pendiente":
            return _error(409, "solicitud_ya_resuelta", f"La solicitud está {fila['estado']}")
        if fila["vencida"]:
            return _error(409, "solicitud_caducada", "La solicitud venció sin decisión")
        if otra_persona and fila["solicitado_por"] == actor:
            return _error(
                409, "separacion_de_funciones", "Quien solicita no puede decidir la misma solicitud"
            )
        if not otra_persona and fila["solicitado_por"] != actor:
            return _error(409, "solo_el_solicitante", "Solo quien solicitó puede retirarla")
        return _error(409, "nivel_cambio", "El nivel del radicado cambió; la solicitud ya no aplica")

    @staticmethod
    def _identificado(actor: uuid.UUID | None) -> uuid.UUID:
        if actor is None:
            raise _error(403, "forbidden", "Se requiere un usuario identificado")
        return actor

    # ---- actos ------------------------------------------------------------------

    async def solicitar(
        self, document_id: uuid.UUID, payload: DesclasificacionRequest, actor: uuid.UUID | None
    ) -> DesclasificacionResponse:
        solicitante = self._identificado(actor)
        clearance = await resolve_caller_clearance(self._conn, solicitante)
        async with self._conn.transaction():
            ctx = await self._docs.get_security_context_for_update(document_id, clearance)
            if ctx is None:
                raise _error(404, "document_not_found", "No existe el radicado solicitado")
            nivel = int(ctx["nivel_seguridad"])
            if nivel < NIVEL_DOBLE_FIRMA:
                raise _error(
                    409,
                    "no_requiere_aprobacion",
                    "El radicado no está en un nivel que exija aprobación: use PATCH "
                    f"/api/v1/documents/{document_id}/security-level",
                )
            # El destino reservado tampoco puede pasar de 15 años desde la GENERACIÓN.
            self._verificar_tope(
                payload.nivel_seguridad, payload.plazo_reserva_meses,
                int(ctx["meses_desde_generacion"]),
            )
            await self._caducar(document_id, ctx["tracking_number"])
            if await self._repo.pendiente_de(document_id) is not None:
                raise _error(
                    409, "desclasificacion_pendiente", "Ya hay una solicitud pendiente de este radicado"
                )
            try:
                fila = await self._repo.insertar(
                    radicado_id=document_id,
                    nivel_anterior=nivel,
                    nivel_nuevo=payload.nivel_seguridad,
                    motivo=payload.motivo.strip(),
                    causal=payload.causal_reserva,
                    fundamento=payload.fundamento_juridico,
                    plazo_meses=payload.plazo_reserva_meses,
                    solicitado_por=solicitante,
                    norma=payload.fundamento_norma,
                )
            except asyncpg.UniqueViolationError as exc:  # carrera residual: índice único
                raise _error(
                    409, "desclasificacion_pendiente", "Ya hay una solicitud pendiente de este radicado"
                ) from exc
            await self._asentar(
                "document.desclasificacion_solicitada",
                tracking=ctx["tracking_number"],
                actor=solicitante,
                payload={**self._payload(fila), "motivo": payload.motivo.strip()},
            )
        return DesclasificacionResponse(**dict(fila), tracking_number=ctx["tracking_number"])

    async def aprobar(
        self, solicitud_id: uuid.UUID, actor: uuid.UUID | None
    ) -> DesclasificacionAprobada:
        aprobador = self._identificado(actor)
        clearance = await resolve_caller_clearance(self._conn, aprobador)
        radicado_id = await self._repo.radicado_de(solicitud_id)
        if radicado_id is None:
            raise _no_encontrado(solicitud_id)
        async with self._conn.transaction():
            # 1.º el radicado (cerrojo + gate de clearance en el WHERE), 2.º la solicitud.
            ctx = await self._docs.get_security_context_for_update(radicado_id, clearance)
            if ctx is None:
                raise _no_encontrado(solicitud_id)
            fila = await self._repo.aprobar(solicitud_id, aprobador, clearance)
            if fila is None:
                raise await self._fallo_de_decision(solicitud_id, aprobador, clearance, otra_persona=True)
            # El tiempo corre entre pedir y aprobar: se vuelve a medir contra la generación
            # (la excepción revierte la tx y la solicitud sigue pendiente).
            self._verificar_tope(
                int(fila["nivel_nuevo"]), fila["plazo_reserva_meses"],
                int(ctx["meses_desde_generacion"]),
            )
            bajado = await self._docs.set_classification(
                radicado_id,
                fila["nivel_nuevo"],
                nivel_esperado=fila["nivel_anterior"],
                fundamento=fila["fundamento_juridico"],
                causal=fila["causal_reserva"],
                plazo_meses=fila["plazo_reserva_meses"],
                norma=fila["fundamento_norma"],
            )
            if not bajado:
                # El UPDATE de la solicitud ya comprobó el nivel; llegar aquí es una
                # carrera que el cerrojo debería impedir. Se revierte entera.
                raise _error(409, "nivel_cambio", "El nivel del radicado cambió; la solicitud ya no aplica")
            payload = {
                **self._payload(fila),
                "aprobado_por": str(aprobador),
                "fundamento_juridico": fila["fundamento_juridico"],
                "fundamento_norma": fila["fundamento_norma"],
                "plazo_reserva_meses": fila["plazo_reserva_meses"],
            }
            await self._asentar(
                "document.desclasificacion_aprobada",
                tracking=ctx["tracking_number"],
                actor=aprobador,
                payload=payload,
            )
            await self._asentar(
                "document.radicado_reclassified",
                tracking=ctx["tracking_number"],
                actor=aprobador,
                payload={**payload, "nivel_anterior": fila["nivel_anterior"],
                         "nivel_nuevo": fila["nivel_nuevo"], "motivo": fila["motivo"]},
            )
            pendiente = await outbox.encolar(
                self._conn,
                stream=_STREAM,
                event_type=outbox.RECLASIFICADO,
                tenant_slug=self._slug,
                actor=str(aprobador),
                dedup_key=f"{ctx['tracking_number']}:{fila['nivel_nuevo']}:{solicitud_id}",
                payload={
                    "id": str(radicado_id),
                    "tracking_number": ctx["tracking_number"],
                    "nivel_anterior": fila["nivel_anterior"],
                    "nivel_nuevo": fila["nivel_nuevo"],
                    "causal_reserva": fila["causal_reserva"],
                    "fundamento_juridico": fila["fundamento_juridico"],
                    "plazo_reserva_meses": fila["plazo_reserva_meses"],
                    "solicitud_id": str(solicitud_id),
                    "solicitado_por": str(fila["solicitado_por"]),
                    "aprobado_por": str(aprobador),
                },
            )
        await outbox.enviar(self._conn, self._redis, pendiente)
        return DesclasificacionAprobada(
            **dict(fila),
            tracking_number=ctx["tracking_number"],
            nivel_seguridad=int(fila["nivel_nuevo"]),
        )

    async def rechazar(
        self, solicitud_id: uuid.UUID, actor: uuid.UUID | None, motivo: str
    ) -> DesclasificacionResponse:
        decisor = self._identificado(actor)
        clearance = await resolve_caller_clearance(self._conn, decisor)
        radicado_id = await self._repo.radicado_de(solicitud_id)
        if radicado_id is None:
            raise _no_encontrado(solicitud_id)
        async with self._conn.transaction():
            ctx = await self._docs.get_security_context_for_update(radicado_id, clearance)
            if ctx is None:
                raise _no_encontrado(solicitud_id)
            fila = await self._repo.rechazar(solicitud_id, decisor, motivo.strip(), clearance)
            if fila is None:
                raise await self._fallo_de_decision(solicitud_id, decisor, clearance, otra_persona=True)
            await self._asentar(
                "document.desclasificacion_rechazada",
                tracking=ctx["tracking_number"],
                actor=decisor,
                payload={**self._payload(fila), "resolucion_motivo": motivo.strip()},
            )
        return DesclasificacionResponse(**dict(fila), tracking_number=ctx["tracking_number"])

    async def retirar(
        self, solicitud_id: uuid.UUID, actor: uuid.UUID | None, motivo: str | None
    ) -> DesclasificacionResponse:
        solicitante = self._identificado(actor)
        clearance = await resolve_caller_clearance(self._conn, solicitante)
        radicado_id = await self._repo.radicado_de(solicitud_id)
        if radicado_id is None:
            raise _no_encontrado(solicitud_id)
        async with self._conn.transaction():
            ctx = await self._docs.get_security_context_for_update(radicado_id, clearance)
            if ctx is None:
                raise _no_encontrado(solicitud_id)
            fila = await self._repo.retirar(
                solicitud_id, solicitante, motivo.strip() if motivo else None, clearance
            )
            if fila is None:
                raise await self._fallo_de_decision(
                    solicitud_id, solicitante, clearance, otra_persona=False
                )
            await self._asentar(
                "document.desclasificacion_retirada",
                tracking=ctx["tracking_number"],
                actor=solicitante,
                payload=self._payload(fila),
            )
        return DesclasificacionResponse(**dict(fila), tracking_number=ctx["tracking_number"])

    # ---- lecturas ---------------------------------------------------------------

    async def historial(
        self, document_id: uuid.UUID, actor: uuid.UUID | None
    ) -> list[DesclasificacionResponse]:
        clearance = await resolve_caller_clearance(self._conn, actor)
        filas = await self._repo.historial(document_id, clearance)
        return [DesclasificacionResponse(**dict(f)) for f in filas]

    async def cola(
        self, actor: uuid.UUID | None, *, page: int, size: int
    ) -> tuple[list[DesclasificacionPendiente], int]:
        clearance = await resolve_caller_clearance(self._conn, actor)
        filas, total = await self._repo.cola(
            max_clearance=clearance, actor=actor, offset=(page - 1) * size, limit=size
        )
        return [DesclasificacionPendiente(**dict(f)) for f in filas], total

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Anulación de radicado en dos pasos (paridad legado).

Solicitar → (aprobar | rechazar). La ley prohíbe borrar: al aprobar, el radicado
pasa a `anulado` (el número se conserva). A lo sumo una solicitud activa por radicado.

Al APROBAR se publica `document.radicado.annulled` (best-effort, gemelo de
`DocumentService._emit_created`) — knowledge-service (E21) lo consume para purgar
PERMANENTEMENTE el chunk indexado del radicado anulado (residual
"purga-en-anulación": un radicado ANULADO no debe seguir siendo consultable en el
índice semántico). Solicitar/rechazar NO emiten evento: solo aprobar cambia el
estado del radicado.

Auditoría (ADR-008/010): los TRES actos (solicitar/aprobar/rechazar) dejan un
asiento propio en `public.audit_log`, DENTRO de la misma transacción que la
mutación (patrón gemelo de `DocumentService.create`, `document_service.py:214`)
— si `audit.append` falla, la mutación entera revierte (una anulación aprobada
sin traza es peor que una anulación fallida). `object_ref` es SIEMPRE el
`tracking_number` resuelto server-side desde la BD, nunca un campo del body.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core import reglas_tipo
from app.repositories.anulacion_repository import AnulacionRepository
from app.schemas.anulacion import (
    AnulacionDecision,
    AnulacionPendiente,
    AnulacionResponse,
    AnulacionSolicitud,
)
from app.services import outbox

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
_STREAM = "orpycamcp.document.events"


def _resp(row) -> AnulacionResponse:
    return AnulacionResponse(**dict(row))


class AnulacionService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        redis: Any | None = None,
        tenant_slug: str | None = None,
    ) -> None:
        self._conn = conn
        self._repo = AnulacionRepository(conn)
        self._redis = redis
        self._slug = tenant_slug

    def _audit_tenant(self) -> str:
        """Tenant al que se imputa el asiento — fail-closed.

        `tenant_slug` es opcional en el constructor porque `get` (lectura) no
        audita y se construye sin él. Pero en las tres ESCRITURAS es
        obligatorio: `audit.append` hace `tenant_slug or SYSTEM_TENANT`, así
        que un llamante que lo omitiera archivaría la anulación de un
        documento oficial bajo el tenant del sistema — la fila existe, pero
        queda fuera de la vista de auditoría de la entidad y de su cadena de
        hash, que es justo donde un auditor la buscaría. Preferimos que la
        anulación falle a que su traza se archive en el sitio equivocado.
        """
        if not self._slug:
            raise RuntimeError(
                "AnulacionService requiere tenant_slug para auditar una escritura "
                "(imputar el asiento al tenant del sistema lo ocultaría de la "
                "auditoría de la entidad)"
            )
        return self._slug

    async def solicitar(
        self,
        radicado_id: uuid.UUID,
        payload: AnulacionSolicitud,
        solicitado_por: uuid.UUID | None,
        max_clearance: int,
    ) -> AnulacionResponse:
        fila = await self._repo.radicado_estado_y_tipo(radicado_id, max_clearance)
        if fila is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "document_not_found", "detail": str(radicado_id), "status": 404},
            )
        estado_rad, tipo_rad = fila
        # ANTES que el estado y que cualquier permiso: hay actos que sobre cierto
        # tipo de documento no existen. Una Entrada no se anula —el documento
        # entró, y eso no se deja sin efecto—, y eso no depende de lo que tenga
        # concedido quien lo pide: ni ROOT lo salta, porque el bypass de ROOT es
        # sobre autorización, no sobre lo que es posible. Ver
        # `app/core/reglas_tipo.py`.
        reglas_tipo.exigir("anular", tipo_rad)
        if estado_rad == "anulado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "ya_anulado",
                    "detail": "El radicado ya está anulado",
                    "status": 409,
                },
            )
        actor = str(solicitado_por) if solicitado_por else None
        try:
            # La solicitud y su asiento de auditoría son atómicos (ADR-008): si
            # `audit.append` falla, la solicitud entera revierte — ver docstring
            # del módulo.
            async with self._conn.transaction():
                row = await self._repo.insert_solicitud(
                    radicado_id=radicado_id,
                    causal=payload.causal,
                    motivo=payload.motivo,
                    solicitado_por=solicitado_por,
                )
                tracking_number = await self._repo.get_tracking_number(radicado_id)
                await audit.append(
                    self._conn,
                    tenant_slug=self._audit_tenant(),
                    service=_SERVICE,
                    action="document.anulacion_solicitada",
                    object_type="radicado",
                    object_ref=tracking_number,
                    actor=actor,
                    payload={
                        "id": str(radicado_id),
                        "anulacion_id": str(row["id"]),
                        "causal": payload.causal,
                        "motivo": payload.motivo,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_activa",
                    "detail": "Ya hay una solicitud de anulación pendiente",
                    "status": 409,
                },
            )
        return _resp(row)

    async def _decidir(
        self,
        radicado_id: uuid.UUID,
        *,
        aprobar: bool,
        payload: AnulacionDecision,
        decidido_por: uuid.UUID | None,
        max_clearance: int,
    ) -> AnulacionResponse:
        active = await self._repo.get_active(radicado_id, max_clearance)
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_solicitud_activa",
                    "detail": "No hay solicitud de anulación pendiente",
                    "status": 404,
                },
            )
        # ── Segregación de funciones ──────────────────────────────────────
        # Quien pide la anulación no puede aprobarla. La pantalla de solicitud
        # ya se lo dice a la persona —«requiere aprobación posterior por un
        # supervisor»—, pero eso era una promesa que nadie hacía cumplir: el
        # mismo usuario podía solicitar y aprobar seguido, y el radicado quedaba
        # `anulado` sin que ningún tercero hubiera mirado. Anular es irreversible
        # y borra de la circulación un documento oficial (Ley 594/2000): el
        # control que lo justifica es precisamente que lo revise otro.
        #
        # El RECHAZO propio sí se permite, y a propósito: rechazar la solicitud
        # que uno mismo hizo es desistir de ella, no ejecutar el acto. Bloquear
        # también eso dejaría solicitudes vivas que nadie puede retirar.
        if aprobar and decidido_por is not None and active["solicitado_por"] == decidido_por:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "aprobacion_del_propio_solicitante",
                    "detail": (
                        "Usted solicitó esta anulación y no puede aprobarla: la anulación de un "
                        "radicado la decide una persona distinta de quien la pide. Pásela a otro "
                        "funcionario con permiso para resolver anulaciones; si desea desistir, "
                        "puede rechazarla usted mismo."
                    ),
                    "status": 409,
                },
            )

        actor = str(decidido_por) if decidido_por else None
        # object_ref = tracking_number, resuelto SERVER-SIDE desde la BD (nunca
        # de un campo del body). La decisión y su asiento de auditoría son
        # atómicos (ADR-008): `repo.decide` abre su propia transacción interna,
        # que degrada a SAVEPOINT dentro de esta — si `audit.append` falla, la
        # decisión completa (aprobar o rechazar) revierte.
        async with self._conn.transaction():
            row = await self._repo.decide(
                anulacion_id=active["id"],
                radicado_id=radicado_id,
                aprobar=aprobar,
                decidido_por=decidido_por,
                observacion=payload.observacion,
            )
            # Sin fila, la decisión NO ocurrió: alguien la resolvió entre la
            # lectura y la escritura. Se corta ANTES del asiento, porque un
            # asiento de auditoría afirmando una decisión que no se aplicó es
            # peor que no tener ninguno: dice quién decidió qué, y es falso.
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "anulacion_ya_decidida",
                        "detail": (
                            "Esta solicitud de anulación ya fue resuelta por otra "
                            "persona. Vuelva a consultarla antes de decidir."
                        ),
                        "status": 409,
                    },
                )

            tracking_number = await self._repo.get_tracking_number(radicado_id)
            await audit.append(
                self._conn,
                tenant_slug=self._audit_tenant(),
                service=_SERVICE,
                action="document.anulacion_aprobada" if aprobar else "document.anulacion_rechazada",
                object_type="radicado",
                object_ref=tracking_number,
                actor=actor,
                payload={
                    "id": str(radicado_id),
                    "anulacion_id": str(active["id"]),
                    "observacion": payload.observacion,
                },
                canal=audit.CANAL_PETICION,
            )
            # OUTBOX (AREA-09-05): SOLO al aprobar — solicitar/rechazar no cambian el
            # estado del radicado. Misma tx que la decisión y su asiento.
            pendiente = None
            if aprobar and tracking_number and self._slug:
                pendiente = await outbox.encolar(
                    self._conn,
                    stream=_STREAM,
                    event_type="document.radicado.annulled",
                    tenant_slug=self._slug,
                    actor=str(decidido_por) if decidido_por else None,
                    dedup_key=f"{tracking_number}:annulled",
                    payload={"id": str(radicado_id), "tracking_number": tracking_number},
                )
        # Camino rápido tras el commit. El evento ya está en el outbox (encolado
        # dentro de la tx): un fallo de Redis aquí lo deja pendiente para el relé
        # del barrido en vez de perderlo (AREA-09-05).
        if pendiente is not None:
            await outbox.enviar(self._conn, self._redis, pendiente)
        return _resp(row)

    async def aprobar(
        self, radicado_id, payload, decidido_por, max_clearance: int
    ) -> AnulacionResponse:
        return await self._decidir(
            radicado_id,
            aprobar=True,
            payload=payload,
            decidido_por=decidido_por,
            max_clearance=max_clearance,
        )

    async def rechazar(
        self, radicado_id, payload, decidido_por, max_clearance: int
    ) -> AnulacionResponse:
        return await self._decidir(
            radicado_id,
            aprobar=False,
            payload=payload,
            decidido_por=decidido_por,
            max_clearance=max_clearance,
        )

    async def get(self, radicado_id: uuid.UUID, max_clearance: int) -> AnulacionResponse:
        row = await self._repo.get_latest(radicado_id, max_clearance)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_anulacion",
                    "detail": "El radicado no tiene anulaciones",
                    "status": 404,
                },
            )
        return _resp(row)

    async def listar_pendientes(
        self, *, max_clearance: int, page: int, size: int
    ) -> tuple[list[AnulacionPendiente], int]:
        """Cola de solicitudes pendientes de decidir, recortada por clearance.

        El recorte lo hace el repositorio, en la MISMA condición para las filas
        y para el total: contar sin filtrar diría cuántas solicitudes hay sobre
        radicados que el llamante no puede ver, que es justo lo que el
        no-read-up impide (RF-SEG-08).
        """
        filas, total = await self._repo.list_pendientes(
            max_clearance=max_clearance,
            offset=(page - 1) * size,
            limit=size,
        )
        return [AnulacionPendiente(**dict(f)) for f in filas], total

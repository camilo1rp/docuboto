# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Servicio de cadena de firma secuencial (E06, ADR-016, RF-FIR-04/11/12/14).

Invariante de orden: el firmante N+1 solo puede actuar una vez que el N
ha completado su turno. Esto se garantiza por el turno_activo (menor orden
pendiente) y la comprobación firmante_id == actor antes de cualquier escritura.

El firmante se obtiene SIEMPRE del JWT validado (RF-FIR-11); nunca del body.
Cada acto de escritura queda en audit_log (RF-FIR-14).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import asyncpg
from fastapi import HTTPException, status

from app.core.clearance import resolve_caller_clearance
from app.core.config import settings
from app.repositories.cadena_repository import CadenaRepository
from app.repositories.firma_repository import FirmaRepository
from app.schemas.cadena import (
    FirmarRequest,
    LoteResponse,
    PendienteItem,
    ResultadoLoteItem,
    SolicitudCreate,
    SolicitudResponse,
    TurnoItem,
)
from app.services import signer

if TYPE_CHECKING:
    pass

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in all test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"


def _build_response(sol: asyncpg.Record, turnos: list[asyncpg.Record]) -> SolicitudResponse:
    items = [
        TurnoItem(
            orden=t["orden"],
            firmante_id=t["firmante_id"],
            estado=t["estado"],
            firma_id=t["firma_id"],
            comentario=t["comentario"],
            decidido_at=t["decidido_at"],
        )
        for t in turnos
    ]
    return SolicitudResponse(
        solicitud_id=sol["id"],
        objeto_tipo=sol["objeto_tipo"],
        objeto_id=sol["objeto_id"],
        titulo=sol.get("titulo"),
        estado=sol["estado"],
        nivel_seguridad=sol["nivel_seguridad"],
        items=items,
        created_at=sol["created_at"],
    )


class CadenaFirmaService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = CadenaRepository(conn)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_or_404(self, solicitud_id: uuid.UUID) -> asyncpg.Record:
        sol = await self._repo.get_solicitud(solicitud_id)
        if sol is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "solicitud_not_found",
                    "detail": f"Signature request {solicitud_id} not found",
                    "status": 404,
                },
            )
        return sol

    async def _assert_can_read(
        self, sol: asyncpg.Record, actor: uuid.UUID
    ) -> None:
        """Raises 404 when actor's clearance is below sol's nivel_seguridad.

        Uses 404 (not 403) so under-clearance actors cannot distinguish
        "does not exist" from "exists but classified" (no-read-up, RF-SEG-08, D-15).
        Fail-closed: any DB/table error resolves clearance to PUBLICA=1.
        """
        clearance = await resolve_caller_clearance(self._conn, actor)
        if sol["nivel_seguridad"] > clearance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "solicitud_not_found",
                    "detail": "Signature request not found",
                    "status": 404,
                },
            )

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def crear(
        self, payload: SolicitudCreate, actor: uuid.UUID
    ) -> SolicitudResponse:
        """
        Creates a new signature chain.

        409 if a pending chain already exists for the same object.
        Atomically inserts solicitud + turns + audit entry.

        Security-level snapshot (RF-SEG-08, D-15): when objeto_tipo='radicado', the
        nivel_seguridad is read from radicados.nivel_seguridad (authoritative source)
        and persisted in firma_solicitud.nivel_seguridad.  For other object types the
        caller may declare it via payload.nivel_seguridad (defaults to 1=PUBLICA).
        This one-time snapshot avoids cross-service joins on every subsequent read.
        """
        # Resolve nivel snapshot first (authoritative for radicado).
        if payload.objeto_tipo == "radicado":
            nivel: int = (
                await self._repo.resolver_nivel_radicado(payload.objeto_id)
                or payload.nivel_seguridad
                or 1
            )
        else:
            nivel = payload.nivel_seguridad or 1

        # No-read-up on the creation path (RF-SEG-08, D-15 #1): an actor below the
        # object's level must not learn it exists — neither via the 201 (nivel) nor
        # via the 409 (existe_abierta). Same neutral 404 as _assert_can_read, checked
        # before existe_abierta so the conflict path does not leak either.
        clearance = await resolve_caller_clearance(self._conn, actor)
        if nivel > clearance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "solicitud_not_found",
                    "detail": "Signature request not found",
                    "status": 404,
                },
            )

        if await self._repo.existe_abierta(payload.objeto_tipo, payload.objeto_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "cadena_existente",
                    "detail": (
                        f"Object {payload.objeto_tipo}:{payload.objeto_id} "
                        "already has an open signature chain"
                    ),
                    "status": 409,
                },
            )

        async with self._conn.transaction():
            sol = await self._repo.crear_solicitud(
                {
                    "objeto_tipo": payload.objeto_tipo,
                    "objeto_id": payload.objeto_id,
                    "titulo": payload.titulo,
                    "payload_sha256": payload.payload_sha256,
                    "creado_por": actor,
                    "nivel_seguridad": nivel,
                }
            )
            await self._repo.crear_turnos(sol["id"], payload.firmantes)
            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.solicitud_creada",
                    object_type=payload.objeto_tipo,
                    object_ref=payload.objeto_id,
                    actor=str(actor),
                    payload={
                        "solicitud_id": str(sol["id"]),
                        "firmantes": [str(f) for f in payload.firmantes],
                        "nivel_seguridad": nivel,
                    },
                )

        turnos = await self._repo.list_turnos(sol["id"])
        return _build_response(sol, turnos)

    async def get(self, solicitud_id: uuid.UUID, actor: uuid.UUID) -> SolicitudResponse:
        """Returns the full chain: header + items. 404 if not found or above clearance."""
        sol = await self._get_or_404(solicitud_id)
        await self._assert_can_read(sol, actor)
        turnos = await self._repo.list_turnos(solicitud_id)
        return _build_response(sol, turnos)

    async def get_por_objeto(
        self, objeto_tipo: str, objeto_id: str, actor: uuid.UUID
    ) -> SolicitudResponse:
        """Returns the latest open chain for the given object. 404 if none or above clearance."""
        sol = await self._repo.get_solicitud_por_objeto(objeto_tipo, objeto_id)
        if sol is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "cadena_not_found",
                    "detail": f"No open signature chain for {objeto_tipo}:{objeto_id}",
                    "status": 404,
                },
            )
        await self._assert_can_read(sol, actor)
        turnos = await self._repo.list_turnos(sol["id"])
        return _build_response(sol, turnos)

    async def pendientes(
        self, firmante_id: uuid.UUID, page: int, size: int
    ) -> tuple[list[PendienteItem], int]:
        """Returns (items, total) for the signer's inbox (active-turn filter).

        Resolves the actor's clearance first so that the repo query can apply
        nivel_seguridad <= max_clearance — ensuring X-Total-Count does not leak
        the existence of above-clearance chains (no-read-up, RF-SEG-08, D-15).
        """
        max_clearance = await resolve_caller_clearance(self._conn, firmante_id)
        offset = (page - 1) * size
        rows, total = await self._repo.pendientes_de_firmante(
            firmante_id, offset=offset, limit=size, max_clearance=max_clearance
        )
        items = [
            PendienteItem(
                solicitud_id=r["solicitud_id"],
                objeto_tipo=r["objeto_tipo"],
                objeto_id=r["objeto_id"],
                titulo=r.get("titulo"),
                orden=r["orden"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
        return items, total

    async def firmar(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        payload_opcional: FirmarRequest | None = None,
    ) -> SolicitudResponse:
        """
        Signs the active turn for the given solicitud.

        Guards (in order):
        1. 404 — solicitud not found.
        2. 409 — solicitud is not in 'pendiente' state.
        3. 403 no_es_firmante — actor is not in the chain at all.
        4. 403 no_es_su_turno — actor is in the chain but it's not their turn yet
           (invariant: turn N+1 only after turn N is resolved, RF-FIR-04).

        The firma row + turn update + solicitud state change + audit entry
        all commit in a single transaction (RF-FIR-14 atomicity).
        """
        sol = await self._get_or_404(solicitud_id)
        # No-read-up gate: 404 before any other guard so actor cannot infer
        # chain existence from 409/403 response codes (RF-SEG-08, D-15).
        await self._assert_can_read(sol, actor)

        if sol["estado"] != "pendiente":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_no_pendiente",
                    "detail": f"Signature request {solicitud_id} is already '{sol['estado']}'",
                    "status": 409,
                },
            )

        if not await self._repo.es_firmante(solicitud_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_firmante",
                    "detail": "Actor is not a member of this signature chain",
                    "status": 403,
                },
            )

        # Early (non-locking) turn check for a fast 403 before doing the signing work.
        turno = await self._repo.turno_activo(solicitud_id)
        if turno is None:
            # All turns are resolved but solicitud.estado is still 'pendiente' — data inconsistency.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_no_pendiente",
                    "detail": "No active turn found; signature chain may already be complete",
                    "status": 409,
                },
            )
        if turno["firmante_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "It is not this actor's turn in the signature chain (RF-FIR-04)",
                    "status": 403,
                },
            )

        # Server-generated timestamp — never from the client (RF-FIR-12).
        sello_dt = datetime.now(timezone.utc)
        sello_iso = sello_dt.isoformat()

        # Payload priority: solicitud.payload_sha256 > request.payload_xml > identifier fallback.
        # huella_de_contenido=False marca el caso de "sellado de identificador": se firma
        # "objeto_tipo:objeto_id", NO el contenido → no detecta alteración del documento
        # (DUR 1074/2015 art. 2.2.2.47.4.2). Se audita el flag para trazar el grado de prueba.
        if sol["payload_sha256"]:
            payload_bytes = sol["payload_sha256"].encode()
            huella_de_contenido = True
        elif payload_opcional and payload_opcional.payload_xml:
            payload_bytes = payload_opcional.payload_xml.encode()
            huella_de_contenido = True
        else:
            payload_bytes = f"{sol['objeto_tipo']}:{sol['objeto_id']}".encode()
            huella_de_contenido = False

        sign_result = signer.firmar(
            payload_bytes, str(actor), sello_iso, settings.signature_secret
        )

        async with self._conn.transaction():
            # Authoritative turn read under a row lock (TOCTOU guard, RF-FIR-04): a
            # concurrent sign of the same turn blocks here until the first commits,
            # then sees the turn already resolved and fails the marcar_turno guard.
            turno = await self._repo.turno_activo_for_update(solicitud_id)
            if turno is None or turno["firmante_id"] != actor:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "The active turn changed before the signature was committed",
                        "status": 409,
                    },
                )

            firma_row = await FirmaRepository(self._conn).insert(
                {
                    "objeto_tipo": sol["objeto_tipo"],
                    "objeto_id": sol["objeto_id"],
                    "formato_solicitado": None,
                    "firmante_id": actor,
                    "payload_sha256": sign_result["payload_sha256"],
                    "firma_blob": sign_result["firma_blob"],
                    "provider": sign_result["provider"],
                    "sello_tiempo": sello_dt,
                }
            )
            firma_id: uuid.UUID = firma_row["id"]

            ok = await self._repo.marcar_turno(turno["id"], "firmado", firma_id=firma_id)
            if not ok:
                # Lost the race: the turn was already resolved by a concurrent commit.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "This turn was already resolved by another request",
                        "status": 409,
                    },
                )

            # Promote solicitud to 'firmado' when no more pending turns exist.
            next_active = await self._repo.turno_activo(solicitud_id)
            if next_active is None:
                await self._repo.actualizar_estado_solicitud(solicitud_id, "firmado")

            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.turno_firmado",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(actor),
                    payload={
                        "solicitud_id": str(solicitud_id),
                        "orden": turno["orden"],
                        "firma_id": str(firma_id),
                        "huella_de_contenido": huella_de_contenido,
                    },
                )

        return await self.get(solicitud_id, actor)

    async def rechazar(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        motivo: str,
    ) -> SolicitudResponse:
        """
        Rejects the active turn; the entire chain stops (estado='rechazado').

        Same guards as firmar. motivo is mandatory.
        """
        sol = await self._get_or_404(solicitud_id)
        # No-read-up gate before any other guard (RF-SEG-08, D-15).
        await self._assert_can_read(sol, actor)

        if sol["estado"] != "pendiente":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_no_pendiente",
                    "detail": f"Signature request {solicitud_id} is already '{sol['estado']}'",
                    "status": 409,
                },
            )

        if not await self._repo.es_firmante(solicitud_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_firmante",
                    "detail": "Actor is not a member of this signature chain",
                    "status": 403,
                },
            )

        # Early (non-locking) turn check for a fast 403.
        turno = await self._repo.turno_activo(solicitud_id)
        if turno is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_no_pendiente",
                    "detail": "No active turn found",
                    "status": 409,
                },
            )
        if turno["firmante_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "It is not this actor's turn in the signature chain (RF-FIR-04)",
                    "status": 403,
                },
            )

        async with self._conn.transaction():
            # Authoritative turn read under a row lock (TOCTOU guard, RF-FIR-04).
            turno = await self._repo.turno_activo_for_update(solicitud_id)
            if turno is None or turno["firmante_id"] != actor:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "The active turn changed before the rejection was committed",
                        "status": 409,
                    },
                )
            ok = await self._repo.marcar_turno(
                turno["id"], "rechazado", comentario=motivo
            )
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "This turn was already resolved by another request",
                        "status": 409,
                    },
                )
            await self._repo.actualizar_estado_solicitud(solicitud_id, "rechazado")

            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.turno_rechazado",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(actor),
                    payload={
                        "solicitud_id": str(solicitud_id),
                        "orden": turno["orden"],
                        "motivo": motivo,
                    },
                )

        return await self.get(solicitud_id, actor)

    async def firmar_lote(
        self,
        solicitud_ids: list[uuid.UUID],
        actor: uuid.UUID,
    ) -> LoteResponse:
        """
        Batch sign: attempts firmar() for each solicitud_id.

        A failure on one item does NOT abort the rest. Each result carries
        'firmado' on success or 'omitido' with an error detail.
        """
        resultados: list[ResultadoLoteItem] = []
        for sid in solicitud_ids:
            try:
                await self.firmar(sid, actor)
                resultados.append(
                    ResultadoLoteItem(solicitud_id=sid, resultado="firmado")
                )
            except HTTPException as exc:
                detail = exc.detail
                detalle = (
                    detail.get("error", str(detail))
                    if isinstance(detail, dict)
                    else str(detail)
                )
                resultados.append(
                    ResultadoLoteItem(
                        solicitud_id=sid, resultado="omitido", detalle=detalle
                    )
                )
        return LoteResponse(resultados=resultados)

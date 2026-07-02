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

2FA por OTP-correo (E06 Inc.2, RF-FIR-13): cuando settings.firma_2fa_required
es True, firmar() exige un challenge_id+otp válidos (emitidos por
emitir_challenge) antes de producir la firma — ver _gate_2fa. El gate es
además OBLIGATORIO (no desactivable por el flag) cuando la solicitud es
RESERVADA/CLASIFICADA (nivel_seguridad >= 2) — ver firmar(). El sello
institucional del índice (/sign-xml, otro router) NO pasa por este gate.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import HTTPException, status

from app.core.clearance import resolve_caller_clearance
from app.core.config import settings
from app.core.events import publish_challenge_emitido
from app.core.http_clients import (
    OtpEmailDeliveryError,
    SignerEmailError,
    get_signer_email,
    send_otp_email,
)
from app.core.otp import generate_otp, hmac_otp, mask_email, verify_otp
from app.core.redis_client import get_redis
from app.repositories.cadena_repository import CadenaRepository
from app.repositories.challenge_repository import ChallengeRepository
from app.repositories.firma_repository import FirmaRepository
from app.schemas.cadena import (
    ChallengeResponse,
    FirmarRequest,
    LoteItem,
    LoteResponse,
    PendienteItem,
    ResultadoLoteItem,
    SolicitudCreate,
    SolicitudResponse,
    TurnoItem,
)
from app.services import signer

logger = logging.getLogger("signature.cadena")

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

    async def emitir_challenge(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        authorization: str | None,
    ) -> ChallengeResponse:
        """
        Emits a one-time OTP-by-email 2FA challenge for the caller's active turn
        (E06 Inc.2, RF-FIR-13).

        Guards (in order, all under the FOR UPDATE lock of solicitud+turno so a
        concurrent challenge/sign cannot race this check):
        1. 404 solicitud_no_encontrada — solicitud missing OR below the actor's
           clearance (no-read-up, RF-SEG-08 — same neutral 404 as get_solicitud).
        2. 403 no_es_su_turno — no active turn, or it belongs to someone else.
        3. 422 payload_no_fijado — payload_sha256 must be fixed before a 2FA
           challenge can be issued (Q1): the challenge is bound to a stable hash.

        Idempotent reissue: a live challenge (pendiente|verificado) for the same
        (solicitud, firmante, hash_documento) is expired before inserting the
        new one, unless it was created less than otp_reenvio_cooldown_seconds
        ago (429 challenge_cooldown).

        FAIL-CLOSED on the outbound calls (auth-service /me, notification-service
        /send): any failure raises 503 canal_2fa_no_disponible from *inside* the
        transaction, so anything already written (INSERT, expired-replacement)
        rolls back — no orphaned challenge whose OTP was never delivered.
        """
        async with self._conn.transaction():
            sol = await self._repo.get_solicitud_for_update(solicitud_id)
            if sol is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "solicitud_no_encontrada",
                        "detail": "Signature request not found",
                        "status": 404,
                    },
                )
            # No-read-up gate (RF-SEG-08, D-15): below-clearance actors get the
            # same neutral 404 as a missing solicitud (anti-enumeration).
            clearance = await resolve_caller_clearance(self._conn, actor)
            if sol["nivel_seguridad"] > clearance:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "solicitud_no_encontrada",
                        "detail": "Signature request not found",
                        "status": 404,
                    },
                )

            # A closed chain (firmado/rechazado) must not accept a new challenge:
            # rechazar() only resolves the active turn, leaving later turns
            # 'pendiente' — turno_activo_for_update below would still return one
            # of them. Without this guard, a signer further down an already
            # rejected/completed chain could trigger a real OTP email for a
            # challenge that firmar() will unconditionally reject afterwards
            # (409 solicitud_no_pendiente, same check there) — wasted OTP,
            # email noise and a misleading firma.challenge.emitido audit entry.
            if sol["estado"] != "pendiente":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": f"Signature request {solicitud_id} is already '{sol['estado']}'",
                        "status": 409,
                    },
                )

            turno = await self._repo.turno_activo_for_update(solicitud_id)
            if turno is None or turno["firmante_id"] != actor:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "no_es_su_turno",
                        "detail": "It is not this actor's turn in the signature chain (RF-FIR-04)",
                        "status": 403,
                    },
                )

            hash_documento = sol["payload_sha256"]
            if not hash_documento:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "payload_no_fijado",
                        "detail": (
                            "firma_solicitud.payload_sha256 must be set before a 2FA "
                            "challenge can be issued (RF-FIR-13)"
                        ),
                        "status": 422,
                    },
                )

            challenge_repo = ChallengeRepository(self._conn)
            now = datetime.now(timezone.utc)
            activo = await challenge_repo.get_activo(solicitud_id, actor, hash_documento)
            if activo is not None:
                cooldown_hasta = activo["creado_at"] + timedelta(
                    seconds=settings.otp_reenvio_cooldown_seconds
                )
                if now < cooldown_hasta:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail={
                            "error": "challenge_cooldown",
                            "detail": "A challenge was issued too recently; wait before retrying",
                            "status": 429,
                        },
                    )
                # Idempotent replacement (Q1) — the new INSERT below must not
                # collide with uq_firma_challenge_activo.
                await challenge_repo.expirar(activo["challenge_id"])

            try:
                email = await get_signer_email(authorization)
            except SignerEmailError:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "canal_2fa_no_disponible",
                        "detail": "Could not resolve the signer's email (auth-service)",
                        "status": 503,
                    },
                ) from None

            canal_masked = mask_email(email)
            otp = generate_otp(settings.otp_longitud)
            otp_hash = hmac_otp(otp, settings.signature_secret)
            expira_at = now + timedelta(seconds=settings.otp_ttl_seconds)

            challenge = await challenge_repo.crear(
                solicitud_id=solicitud_id,
                firmante_id=actor,
                hash_documento=hash_documento,
                otp_hash=otp_hash,
                max_intentos=settings.otp_max_intentos,
                expira_at=expira_at,
                canal_masked=canal_masked,
                tenant_slug=self._tenant_slug,
            )

            try:
                await send_otp_email(
                    recipient_email=email,
                    tenant_slug=self._tenant_slug,
                    objeto_tipo=sol["objeto_tipo"],
                    solicitud_id=solicitud_id,
                    otp=otp,
                    ttl_seconds=settings.otp_ttl_seconds,
                )
            except OtpEmailDeliveryError:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "canal_2fa_no_disponible",
                        "detail": "Could not deliver the OTP email (notification-service)",
                        "status": 503,
                    },
                ) from None

            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.challenge.emitido",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(actor),
                    payload={
                        "challenge_id": str(challenge["challenge_id"]),
                        "factor_tipo": "otp_email",
                        "solicitud_id": str(solicitud_id),
                        "firmante_id": str(actor),
                        "hash_documento": hash_documento,
                        "expira_at": expira_at.isoformat(),
                        "canal_masked": canal_masked,
                    },
                )

        # Best-effort event — the challenge is already persisted and the email
        # already sent; a Redis failure must not undo either (same criterion as
        # publish_indice_firmado, app/core/events.py).
        try:
            await publish_challenge_emitido(
                get_redis(),
                tenant_slug=self._tenant_slug,
                actor=str(actor),
                challenge_id=str(challenge["challenge_id"]),
                solicitud_id=str(solicitud_id),
                expira_at=expira_at.isoformat(),
            )
        except Exception:
            logger.warning(
                "No se pudo publicar firma.challenge.emitido para challenge_id=%s",
                challenge["challenge_id"],
                exc_info=True,
            )

        return ChallengeResponse(
            challenge_id=challenge["challenge_id"],
            factor_tipo="otp_email",
            canal=canal_masked,
            expira_at=expira_at,
        )

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

        otp_fail: HTTPException | None = None
        consumed_challenge: dict | None = None

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

            # 2FA gate (E06 Inc.2, RF-FIR-13) — must run BEFORE signer.firmar().
            # On failure, _gate_2fa RETURNS (never raises) so any durable state
            # change it already made (attempt counter, expirado/bloqueado
            # transitions) survives the commit below — even though the signature
            # itself is skipped. See _gate_2fa docstring for why raising here
            # would be wrong (it would roll back those changes too).
            #
            # Mandatory (not disable-able via firma_2fa_required) once the
            # object is above PUBLICA (nivel_seguridad >= 2): a global
            # kill-switch must not be able to silently drop the second factor
            # for RESERVADA/CLASIFICADA acts (DUR 1074/2015 Cap. 47 — grado de
            # confiabilidad apropiado a las circunstancias). The flag remains
            # a real switch only at PUBLICA / for test environments.
            if settings.firma_2fa_required or sol["nivel_seguridad"] >= 2:
                otp_fail, consumed_challenge = await self._gate_2fa(
                    solicitud_id, actor, sol, payload_opcional
                )

            if otp_fail is None:
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
                            # Effective hash bound to this act — ties the audit
                            # entry to the exact document without needing to
                            # cross-reference firma_challenge (RF-FIR-13/14).
                            "hash_documento": sign_result["payload_sha256"],
                            # Derived from the challenge row actually
                            # verified-and-consumed by _gate_2fa (never from
                            # settings.firma_2fa_required nor from the
                            # client-supplied body) so the audit reflects what
                            # really happened, not configuration intent.
                            "factor_tipo": (
                                consumed_challenge["factor_tipo"]
                                if consumed_challenge
                                else None
                            ),
                            "challenge_id": (
                                str(consumed_challenge["challenge_id"])
                                if consumed_challenge
                                else None
                            ),
                            "resultado_2fa": (
                                "verificado" if consumed_challenge else None
                            ),
                            "consumido_at": (
                                consumed_challenge["consumido_at"].isoformat()
                                if consumed_challenge
                                else None
                            ),
                        },
                    )
        # The `async with` block above commits unless one of the two genuine
        # concurrency-conflict branches raised (harmless rollback — nothing had
        # been written yet at those points). Crucially, the otp_fail branch never
        # raises inside the block, so any durable challenge state change it made
        # commits normally along with everything else.

        if otp_fail is not None:
            raise otp_fail

        return await self.get(solicitud_id, actor)

    async def _gate_2fa(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        sol: asyncpg.Record,
        body: FirmarRequest | None,
    ) -> tuple[HTTPException | None, dict | None]:
        """
        Verifies and consumes the 2FA challenge for a personal-signature turn
        (E06 Inc.2, RF-FIR-13). MUST run inside the caller's ambient transaction
        (the same one holding the turno FOR UPDATE lock) — see firmar().

        Returns (None, consumed) on success: the challenge row is marked
        'consumido' here, atomically with the marcar_turno() that follows in
        the same ambient transaction (anti-TOCTOU/replay, step d of the
        contract). ``consumed`` carries the authoritative challenge_id,
        factor_tipo and consumido_at — the values firmar() embeds verbatim
        into the firma.turno_firmado audit entry, never the client-supplied
        challenge_id from ``body`` nor a value inferred from configuration
        (defense-in-depth: the audited evidence always reflects the row that
        was actually verified-and-consumed under FOR UPDATE).

        Returns (HTTPException, None) (never raises) on failure — the caller
        raises it AFTER the ambient transaction has committed. This is
        deliberate: some failure branches here mutate firma_challenge (attempt
        counter, 'expirado'/'bloqueado' transitions) and that state MUST
        survive even though the overall sign attempt is aborted. Raising
        immediately here would propagate out of firmar()'s
        `async with self._conn.transaction():` and roll back the whole
        ambient transaction — silently undoing those durable state changes
        along with it.
        """
        challenge_repo = ChallengeRepository(self._conn)

        challenge_id = body.challenge_id if body else None
        otp = body.otp if body else None
        if challenge_id is None or otp is None:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "2fa_requerido",
                    "detail": "challenge_id and otp are required (RF-FIR-13)",
                    "status": 409,
                },
            ), None

        challenge = await challenge_repo.get_for_update(challenge_id)
        if challenge is None:
            # Intentionally indistinguishable in status/shape from the
            # "belongs to someone else" branch below only in spirit, not in
            # code: both are 4xx with a 'challenge_*' error code, per the
            # contract (step b). challenge_id is an unguessable UUID v4,
            # tenant-scoped, so this does not meaningfully aid enumeration —
            # kept as two distinct codes for operator/troubleshooting clarity.
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_invalido",
                    "detail": "Challenge not found",
                    "status": 409,
                },
            ), None
        if challenge["firmante_id"] != actor:
            return HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "challenge_ajeno",
                    "detail": "Challenge does not belong to this actor",
                    "status": 403,
                },
            ), None
        if challenge["solicitud_id"] != solicitud_id:
            # A challenge is bound at emission to the (solicitud, firmante,
            # hash_documento) triple (Q1/Q2). Two distinct solicitudes can
            # legitimately share an identical payload_sha256 (the same
            # document attached to two radicados/objects) with the same
            # active signer on both — without this check, an OTP emitted for
            # act A could be consumed to sign act B. One-time consumption
            # already limits the blast radius to a single substitution, but
            # this closes the gap outright and keeps the audit entry (which
            # names "this" solicitud_id) truthful.
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_acto_distinto",
                    "detail": "Challenge was issued for a different signature request",
                    "status": 409,
                },
            ), None
        if challenge["hash_documento"] != sol["payload_sha256"]:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_documento_distinto",
                    "detail": "Challenge was issued for a different document",
                    "status": 409,
                },
            ), None
        if challenge["estado"] == "consumido":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_consumido",
                    "detail": "Challenge was already used",
                    "status": 409,
                },
            ), None
        if challenge["estado"] == "bloqueado":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_bloqueado",
                    "detail": "Challenge is blocked (too many failed attempts)",
                    "status": 409,
                },
            ), None

        now = datetime.now(timezone.utc)
        if now >= challenge["expira_at"]:
            await challenge_repo.marcar_estado(challenge["challenge_id"], "expirado")
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_expirado",
                    "detail": "Challenge has expired",
                    "status": 409,
                },
            ), None
        if challenge["intentos"] >= challenge["max_intentos"]:
            await challenge_repo.marcar_estado(challenge["challenge_id"], "bloqueado")
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_bloqueado",
                    "detail": "Challenge is blocked (too many failed attempts)",
                    "status": 409,
                },
            ), None

        if not verify_otp(otp, challenge["otp_hash"], settings.signature_secret):
            nuevos_intentos = challenge["intentos"] + 1
            bloqueado = nuevos_intentos >= challenge["max_intentos"]
            nuevo_estado = "bloqueado" if bloqueado else "pendiente"
            await challenge_repo.registrar_intento_fallido(
                challenge["challenge_id"], intentos=nuevos_intentos, estado=nuevo_estado
            )
            intentos_restantes = max(challenge["max_intentos"] - nuevos_intentos, 0)
            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.challenge.fallido",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(actor),
                    payload={
                        "challenge_id": str(challenge["challenge_id"]),
                        "solicitud_id": str(solicitud_id),
                        "intentos": nuevos_intentos,
                        "intentos_restantes": intentos_restantes,
                        "motivo": "otp_incorrecto",
                    },
                )
            return HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "otp_incorrecto",
                    "detail": "The provided OTP is incorrect",
                    "intentos_restantes": intentos_restantes,
                    "status": 401,
                },
            ), None

        # Success — consume now, atomically with marcar_turno() in the same
        # ambient transaction (anti-TOCTOU/replay, RF-FIR-13 step d). `now`
        # (already computed above for the expiry check) becomes consumido_at,
        # shared verbatim between the DB row and the audit payload the caller
        # builds from the returned dict.
        await challenge_repo.consumir(challenge["challenge_id"], now)
        consumed = {
            "challenge_id": challenge["challenge_id"],
            "factor_tipo": challenge["factor_tipo"],
            "hash_documento": challenge["hash_documento"],
            "consumido_at": now,
        }
        return None, consumed

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
        items: list[LoteItem],
        actor: uuid.UUID,
    ) -> LoteResponse:
        """
        Batch sign: attempts firmar() for each item, each with its OWN 2FA
        credential (Q2, E06 Inc.2 — one OTP per document, never reused across
        items even when several turns belong to the same actor).

        A failure on one item does NOT abort the rest. Each result carries
        'firmado' on success or 'omitido' with the error code as detalle
        (includes 2FA failures: 2fa_requerido, otp_incorrecto, challenge_*).
        """
        resultados: list[ResultadoLoteItem] = []
        for item in items:
            try:
                body = FirmarRequest(challenge_id=item.challenge_id, otp=item.otp)
                await self.firmar(item.solicitud_id, actor, body)
                resultados.append(
                    ResultadoLoteItem(solicitud_id=item.solicitud_id, resultado="firmado")
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
                        solicitud_id=item.solicitud_id, resultado="omitido", detalle=detalle
                    )
                )
        return LoteResponse(resultados=resultados)

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

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg
from fastapi import HTTPException, status
from lxml import etree

from app.core.clearance import resolve_caller_clearance
from app.core.config import settings
from app.core.constants import (
    CADENA_NIVEL_AUTORITATIVO,
    CADENA_PAYLOAD_AUTORITATIVO,
    CADENA_PAYLOAD_OBLIGATORIO,
)
from app.core.events import publish_challenge_emitido
from app.core.http_clients import (
    BorradorDesaparecidoError,
    HuellaNoVerificableError,
    OtpEmailDeliveryError,
    SignerEmailError,
    SigningKeyStatusUnavailableError,
    TotpVerifyUnavailableError,
    aprobar_borrador_remote,
    get_huella_borrador,
    get_signer_email,
    get_signing_key_status_remote,
    send_otp_email,
    verify_totp,
)
from app.core.otp import generate_otp, hmac_otp, mask_email, verify_otp
from app.core.redis_client import get_redis
from app.providers.base import SignedDoc
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
from app.services.personal_signing import sign_personal_xml

_PERSONAL_ENVELOPE_NS = "urn:orpyca:firma-personal"

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
            # El `rol` se caía aquí: la consulta lo trae, el esquema lo declara y
            # este constructor no lo pasaba, así que TODOS los turnos salían con
            # el valor por defecto —«firma»— hacia todas las pantallas.
            #
            # Es la peor dirección posible para un defecto de este tipo: el
            # propio esquema advierte que presentarlos igual «atribuiría a un
            # revisor un compromiso que no adquirió». Y de rebote dejaba la
            # mitad de la función inalcanzable: `/firmas` decide con este campo
            # si ofrece «Dar visto bueno» en vez de «Firmar», así que la acción
            # de visto bueno no aparecía nunca, por mucho que el endpoint, el
            # proxy y la pantalla estuvieran escritos.
            rol=t["rol"],
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


def _build_personal_envelope(*, objeto_tipo: str, objeto_id: str, payload_bytes: bytes) -> bytes:
    """Envoltorio XML mínimo, enveloped-firmable, para la firma PERSONAL PKI
    por-usuario (E17/F4, Incremento 1) — la cadena de firma secuencial firma
    payloads que NO son XML en general (un hash hex, o el literal
    "objeto_tipo:objeto_id"; ver ``firmar()``), pero ``LocalSigner``
    (``app.providers.local``) solo sabe producir XAdES enveloped sobre un
    documento XML. Este envoltorio es el punto de contacto entre ambos: el
    ``payload_bytes`` que ya se firma hoy (nativa, HMAC) se embebe TAL CUAL
    como el texto del elemento raíz, así que ``huella_de_contenido`` sigue
    significando exactamente lo mismo para ambos proveedores.
    """
    root = etree.Element(f"{{{_PERSONAL_ENVELOPE_NS}}}acto", nsmap={None: _PERSONAL_ENVELOPE_NS})
    root.set("objetoTipo", objeto_tipo)
    root.set("objetoId", objeto_id)
    root.text = payload_bytes.decode("utf-8", errors="replace")
    return etree.tostring(root, xml_declaration=False, encoding="UTF-8")


@dataclass(slots=True)
class _PersonalSigningStatus:
    """Resolución de tres estados de ``GET /internal/signing-key/status``
    (E17/F4, Incremento 3 — antes colapsaba a ``dict | None``, ver
    ``CadenaFirmaService._resolve_personal_signing_status``).

    - ``body`` no ``None``            -> cert ``activo`` disponible (ruta XAdES).
    - ``body is None, disponible=True``  -> auth-service alcanzable, SIN cert activo
      (actor no enrolado, o ``Authorization`` ausente en la petición entrante).
    - ``body is None, disponible=False`` -> auth-service inalcanzable
      (``SigningKeyStatusUnavailableError``) — canal caído, no "sin cert".
    """

    body: dict | None
    disponible: bool


def _xades_gate_activo(nivel_seguridad: int) -> bool:
    """Predicado del gate ``firma_personal_require_xades`` (E17/F4, Incremento 3).

    Decide SOLO si un cert personal ausente/inaccesible puede caer a nativa
    para este acto — nunca altera la ruta cuando el cert SÍ está activo.
    """
    modo = settings.firma_personal_require_xades
    if modo == "todos":
        return True
    if modo == "clasificados":
        return nivel_seguridad >= 2
    return False  # "off"


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
                    "detail": "No existe la solicitud de firma indicada",
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
                    "detail": "No existe la solicitud de firma",
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

        Instantánea del nivel de seguridad (RF-SEG-08, D-15): si el `objeto_id`
        resuelve contra `radicados`/`expedientes`, manda el nivel REAL de esa fila y
        el `nivel_seguridad` del body se ignora. Solo cuando no resuelve —y el tipo
        declarado no es de los que exigen un documento registrado— vale el nivel que
        declara el autor. La instantánea evita un join cross-servicio en cada lectura
        posterior, y el consumidor de reclasificación la mantiene al día.

        Antes, la decisión de consultar la fuente autoritativa colgaba de
        `payload.objeto_tipo == "radicado"`: texto libre del body. Con `"Radicado"`
        —o declarando `"borrador"` sobre el UUID de un radicado CLASIFICADA— el nivel
        lo elegía el cliente y el gate no-read-up de abajo se evaluaba contra él.
        Ahora la resolución no mira el tipo, y `objeto_tipo` está además acotado por
        `CADENA_OBJETO_TIPOS` en el schema.
        """
        # La resolución NO consulta `objeto_tipo`: si el `objeto_id` apunta a un
        # radicado o a un expediente, manda su nivel real, se haya declarado el tipo
        # que se haya declarado.
        resuelto = await self._repo.resolver_nivel_por_objeto(payload.objeto_id)
        if resuelto is not None:
            nivel: int = resuelto
        elif payload.objeto_tipo in CADENA_NIVEL_AUTORITATIVO:
            # Dice ser un documento registrado y no existe ninguno: mismo 404 neutro
            # que el no-read-up de abajo, para no distinguir "no existe" de "no lo
            # alcanzas" (la creación de cadenas no puede ser un oráculo).
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "solicitud_not_found",
                    "detail": "No existe la solicitud de firma",
                    "status": 404,
                },
            )
        else:
            # Borrador o documento sin registrar: no hay tabla que consultar, así
            # que el nivel que declara su autor es la única verdad disponible.
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
                    "detail": "No existe la solicitud de firma",
                    "status": 404,
                },
            )

        # ── La huella la fija el SERVIDOR donde hay productor ────────────────
        #
        # Gemelo de la resolución del nivel de arriba: para un `borrador`, la
        # huella autoritativa es la que tiene document-service, no la que diga
        # el cliente. Fail-closed — si no se puede resolver, no se abre el
        # circuito: una cadena con la huella equivocada es peor que ninguna,
        # porque parece que protege.
        huella = payload.payload_sha256
        if payload.objeto_tipo in CADENA_PAYLOAD_AUTORITATIVO:
            huella = await self._resolver_huella(payload.objeto_id)

        if await self._repo.existe_abierta(payload.objeto_tipo, payload.objeto_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "cadena_existente",
                    "detail": (
                        f"El objeto de tipo «{payload.objeto_tipo}» ya tiene una "
                        "cadena de firma abierta"
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
                    "payload_sha256": huella,
                    "creado_por": actor,
                    "nivel_seguridad": nivel,
                }
            )
            await self._repo.crear_turnos(sol["id"], payload.firmantes, payload.roles)
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
                    "detail": f"No hay ninguna cadena de firma abierta para el objeto de tipo «{objeto_tipo}»",
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
                rol=r["rol"],
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
        2. 403 no_es_firmante — actor is not in the chain at all (checked BEFORE
           the turn, so the message never tells a non-member to wait their turn).
        2b. 403 no_es_su_turno — actor IS in the chain but the active turn is
           someone else's, or there is no active turn.
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
                        "detail": "No existe la solicitud de firma",
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
                        "detail": "No existe la solicitud de firma",
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
                        "detail": f"La solicitud de firma ya está en estado «{sol['estado']}»",
                        "status": 409,
                    },
                )

            # Pertenencia ANTES que turno, igual que `firmar`. Este guard decía
            # «no es el turno de este actor» también a quien NO está en la
            # cadena, y a ese le afirmaba algo falso: no espera turno, es que
            # nunca le va a llegar. Quien lo lea se quedará esperando —el mismo
            # daño que un filtro silencioso—, y el colapso no protegía nada,
            # porque `firmar` sobre esa misma solicitud ya distingue los dos
            # casos y responde `no_es_firmante`. Un colapso que solo existe en
            # uno de dos caminos no es una defensa: es una incoherencia.
            if not await self._repo.es_firmante(solicitud_id, actor):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "no_es_firmante",
                        "detail": "El actor no pertenece a esta cadena de firma",
                        "status": 403,
                    },
                )

            turno = await self._repo.turno_activo_for_update(solicitud_id)
            if turno is None or turno["firmante_id"] != actor:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "no_es_su_turno",
                        "detail": "No es el turno de este actor en la cadena de firma (RF-FIR-04)",
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
                            "firma_solicitud.payload_sha256 debe fijarse antes de emitir "
                            "un desafío de doble factor (RF-FIR-13)"
                        ),
                        "status": 422,
                    },
                )

            challenge_repo = ChallengeRepository(self._conn)
            now = datetime.now(UTC)
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
                            "detail": "Se emitió un desafío hace muy poco; espere antes de volver a intentarlo",
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
                        "detail": "No se pudo resolver el correo del firmante (auth-service)",
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
                        "detail": "No se pudo enviar el correo con el código (notification-service)",
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

    async def _avisar_circuito_completo(self, sol: dict) -> None:
        """Cerrada la cadena de un borrador, deja el borrador aprobado.

        Solo aplica a `borrador`: es el único objeto cuyo circuito decide un
        estado en otro servicio. Un radicado o un expediente ya existen cuando
        se firman, y su cadena no los promueve a nada.

        No levanta nunca (ver `aprobar_borrador_remote`): el visto bueno ya
        está dado y auditado en este servicio, y revertirlo porque
        document-service no contesta castigaría al firmante por una avería que
        no es suya. Lo que se pierde si falla es un clic, no la garantía.
        """
        if sol["objeto_tipo"] != "borrador":
            return
        await aprobar_borrador_remote(str(sol["objeto_id"]), self._tenant_slug)

    async def _resolver_huella(self, objeto_id: str) -> str:
        """Huella autoritativa de lo que la cadena vincula, desde su productor.

        Para un borrador es la del **TEXTO** (`cuerpo_sha256`), no la del PDF/A.

        Antes se anclaba al documento producido, y eso hacía el circuito
        inalcanzable: el PDF/A de un borrador solo existe al radicar
        —previsualizar no persiste nada, es su razón de ser—, así que el
        circuito solo podía abrirse DESPUÉS de radicar, cuando recoger
        aprobaciones ya no sirve de nada. El requisito es el contrario: varias
        personas aprueban, y solo entonces sale el documento.

        Anclar al texto es lo que corresponde al trámite real. Los turnos de
        visto bueno aprueban un texto —es lo que leen y lo que discuten— y el
        turno de firma es el que produce el documento definitivo con su número.
        La garantía que se pidió se conserva entera y en el mismo sitio: si
        alguien edita el cuerpo, el disparador de la 030 recalcula
        `cuerpo_sha256`, este gate deja de casar y las aprobaciones recogidas
        caen. No hay ventana: la comprobación es síncrona, en el momento de
        resolver el turno.

        `409 borrador_sin_texto` si el cuerpo está vacío: aprobar la nada no
        vincula a nadie.
        """
        try:
            vigente = await get_huella_borrador(str(objeto_id), self._tenant_slug)
        except BorradorDesaparecidoError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "solicitud_not_found",
                    "detail": "No existe la solicitud de firma",
                    "status": 404,
                },
            ) from exc
        except HuellaNoVerificableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "verificacion_documento_no_disponible",
                    "detail": (
                        "No se pudo resolver la huella del documento y sin ella "
                        f"la cadena no vincularia a nadie: {exc}"
                    ),
                    "status": 503,
                },
            ) from exc

        huella = vigente.get("cuerpo_sha256")
        if not huella:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "borrador_sin_texto",
                    "detail": (
                        "Este borrador no tiene texto: no hay nada que aprobar ni "
                        "que firmar. Redacte el cuerpo antes de abrir el circuito."
                    ),
                    "status": 409,
                },
            )
        return str(huella)

    async def _gate_documento_vigente(self, sol) -> None:
        """Lo que se firma tiene que seguir siendo lo que se aprobó (RF-PRO-11).

        **Es la defensa que manda**, y por una razón concreta: la invalidación
        por evento es *at-least-once* y puede retrasarse. Si la garantía viviera
        solo en la cola, existiría una ventana —corta, pero real— en la que un
        firmante resuelve su turno sobre un texto ya modificado. Aquí se relee
        la huella VIGENTE y se compara; el evento se queda como limpieza de
        bandejas, no como garantía.

        Solo aplica a los tipos con fichero propio (`borrador`, `documento`), que
        son los que tienen `payload_sha256` obligatorio. Para los demás no hay
        huella contra la que comparar y este gate no dice nada.

        No poder comprobar **deniega**: si un fallo de red se interpretara como
        «no ha cambiado», bastaría con que document-service estuviera caído para
        poder firmar sobre un texto modificado — el momento en que menos se
        querría poder.
        """
        if sol["objeto_tipo"] not in CADENA_PAYLOAD_OBLIGATORIO:
            return
        esperado = sol["payload_sha256"]
        if not esperado:
            # No debería ocurrir: el schema lo exige al crear. Si aparece, es una
            # cadena anterior a E23 y NO se firma a ciegas.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "cadena_sin_huella",
                    "detail": (
                        "Esta cadena no fija la huella del documento: se creo antes "
                        "de que fuera obligatoria y no se puede comprobar que el "
                        "texto siga siendo el aprobado. Abra una cadena nueva."
                    ),
                    "status": 409,
                },
            )

        try:
            vigente = await get_huella_borrador(str(sol["objeto_id"]), self._tenant_slug)
        except BorradorDesaparecidoError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "documento_desaparecido",
                    "detail": f"El objeto de esta cadena ya no existe: {exc}",
                    "status": 409,
                },
            ) from exc
        except HuellaNoVerificableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "verificacion_documento_no_disponible",
                    "detail": (
                        "No se pudo comprobar que el documento siga siendo el "
                        f"aprobado, y sin esa comprobación no se firma: {exc}"
                    ),
                    "status": 503,
                },
            ) from exc

        if vigente.get("cuerpo_sha256") != esperado:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "documento_modificado",
                    "detail": (
                        "El texto cambió después de abrirse este circuito "
                        f"(versión actual {vigente.get('version')}). Las aprobaciones "
                        "anteriores no se arrastran: hay que abrir el circuito de "
                        "nuevo y volver a recogerlas."
                    ),
                    "status": 409,
                },
            )

    async def firmar(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        payload_opcional: FirmarRequest | None = None,
        authorization: str | None = None,
    ) -> SolicitudResponse:
        """
        Signs the active turn for the given solicitud.

        Guards (in order):
        1. 404 — solicitud not found.
        2. 409 — solicitud is not in 'pendiente' state.
        3. 403 no_es_firmante — actor is not in the chain at all.
        4. 403 no_es_su_turno — actor is in the chain but it's not their turn yet
           (invariant: turn N+1 only after turn N is resolved, RF-FIR-04).

        ``authorization`` is the raw ``Authorization`` header from the
        inbound request — forwarded verbatim to auth-service when the 2FA
        gate needs to verify a TOTP code (see _gate_2fa/verify_totp). Only
        used by the TOTP factor; the OTP-correo factor never needs it.

        The firma row + turn update + solicitud state change + audit entry
        all commit in a single transaction (RF-FIR-14 atomicity).

        --- Firma PERSONAL PKI por-usuario (E17/F4, Incremento 1) ---
        Before the 2FA gate, queries auth-service (``GET
        /internal/signing-key/status``) for the actor's own signing-key
        status. If ``estado == "activo"``, this act is signed with the
        actor's PERSONAL certificate (XAdES-B/T, provider
        ``xades_personal``) INSTEAD of the HMAC nativa — ``_gate_2fa``/
        ``_gate_2fa_totp`` (OTP-correo/local TOTP verify) are bypassed
        entirely for this path: ``body.totp_code`` is required and consumed
        ATOMICALLY at auth-service, together with the signature itself (see
        ``_firmar_personal``/``app.core.http_clients.sign_digest_remote``) —
        not by this service's own TOTP verification.

        --- Gate XAdES (E17/F4, Incremento 3 — cierra Baja #3 del Inc.2) ---
        With ``settings.firma_personal_require_xades == "off"`` (default), a
        missing cert or an unreachable auth-service both degrade to the
        nativa path — byte-identical to Inc.1/2. With the gate ACTIVE for
        this act's ``nivel_seguridad`` (see ``_xades_gate_activo``), the two
        cases are told apart and BOTH deny the act instead of degrading:
        no active cert -> 422 ``firma_xades_requerida``; auth-service
        unreachable -> 503 ``canal_firma_personal_no_disponible``
        (fail-closed). The denial always runs BEFORE ``_firmar_personal``/
        ``_gate_2fa`` — no 2FA factor is ever verified-or-consumed for an
        act the gate is going to reject — and always writes an atomic
        ``firma.xades_requerida_denegada`` audit entry in the same ambient
        transaction.
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
                    "detail": f"La solicitud de firma ya está en estado «{sol['estado']}»",
                    "status": 409,
                },
            )

        await self._gate_documento_vigente(sol)

        if not await self._repo.es_firmante(solicitud_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_firmante",
                    "detail": "El actor no pertenece a esta cadena de firma",
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
                    "detail": "No hay ningún turno activo; puede que la cadena de firma ya esté completa",
                    "status": 409,
                },
            )
        if turno["firmante_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "No es el turno de este actor en la cadena de firma (RF-FIR-04)",
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
                        "detail": "El turno activo cambió antes de registrar la firma",
                        "status": 409,
                    },
                )
            # El gemelo del gate de `dar_visto_bueno`, y es el que faltaba.
            #
            # Estaba puesto solo en una dirección, y precisamente en la inocua:
            # resolver un turno de FIRMA por la vía del visto bueno no
            # compromete a nadie. La que faltaba es la peligrosa — firmar un
            # turno de VISTO BUENO produce una firma electrónica que compromete
            # a la entidad y genera el documento definitivo, por un acto que su
            # autor entendía como «lo he leído y me parece bien».
            #
            # La bandeja lo facilitaba activamente: ofrecía «Ver / Firmar» y la
            # casilla de firma en lote sobre las filas de visto bueno, así que
            # bastaba un «seleccionar todas» para comprometer a la entidad en
            # bloque sin proponérselo.
            if turno["rol"] != "firma":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "turno_es_de_visto_bueno",
                        "detail": (
                            "Este turno es de VISTO BUENO, no de firma: firmarlo "
                            "comprometería a la entidad con un acto que solo era "
                            "una revisión. Use POST "
                            "/signature/requests/{id}/visto-bueno."
                        ),
                        "status": 409,
                    },
                )

            # Payload priority: solicitud.payload_sha256 > request.payload_xml > identifier fallback.
            # huella_de_contenido=False marca el caso de "sellado de identificador": se firma
            # "objeto_tipo:objeto_id", NO el contenido → no detecta alteración del documento
            # (DUR 1074/2015 art. 2.2.2.47.4.2). Se audita el flag para trazar el grado de prueba.
            # Resuelto ANTES del gate porque AMBOS caminos (personal/nativa) lo necesitan.
            if sol["payload_sha256"]:
                payload_bytes = sol["payload_sha256"].encode()
                huella_de_contenido = True
            elif payload_opcional and payload_opcional.payload_xml:
                payload_bytes = payload_opcional.payload_xml.encode()
                huella_de_contenido = True
            else:
                payload_bytes = f"{sol['objeto_tipo']}:{sol['objeto_id']}".encode()
                huella_de_contenido = False

            # Firma PERSONAL PKI por-usuario (E17/F4, Incremento 1) — ver la
            # nota de docstring "Firma PERSONAL...". Resuelto ANTES del gate
            # 2FA nativo: si el actor tiene un cert 'activo', ESTE es el
            # único camino de firma para este acto (bypasea _gate_2fa por
            # completo) — nunca ambos.
            #
            # Gate XAdES (E17/F4, Incremento 3, cierra Baja #3 del Inc.2):
            # decidido ANTES de resolver el estado personal para que la
            # rama de denegación pueda distinguir "sin cert" de "canal
            # caído" — pero la denegación en sí (más abajo) corre ANTES de
            # invocar _firmar_personal/_gate_2fa, así que ningún factor 2FA
            # se consume en un acto que el gate va a rechazar.
            gate_activo = _xades_gate_activo(sol["nivel_seguridad"])
            # NOTA (auditoría E17/F4 Inc.3): esta llamada HTTP a auth-service
            # (GET /internal/signing-key/status, timeout signing_key_sign_timeout_seconds)
            # se ejecuta con el row-lock de turno_activo_for_update ya tomado. Patrón
            # heredado de Inc.1/2 (_gate_2fa ya hacía IO en-tx) — Inc.3 no añade IO
            # nueva en la rama de denegación, solo reutiliza este resultado. No afecta
            # aislamiento de tenant ni autorización; seguimiento fuera de alcance:
            # resolver el estado personal ANTES de abrir la transacción para no
            # extender el hold del lock hasta el timeout bajo lentitud de auth-service.
            personal_res = await self._resolve_personal_signing_status(authorization)
            personal_signed: SignedDoc | None = None

            if personal_res.body is not None:
                totp_code = payload_opcional.totp_code if payload_opcional else None
                if not totp_code:
                    otp_fail = HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "2fa_requerido",
                            "detail": "totp_code es obligatorio para la firma personal (RF-FIR-13)",
                            "status": 409,
                        },
                    )
                else:
                    try:
                        personal_signed = await self._firmar_personal(
                            sol=sol,
                            payload_bytes=payload_bytes,
                            authorization=authorization,
                            totp_code=totp_code,
                            cert_pem=personal_res.body["cert_pem"],
                        )
                    except HTTPException as exc:
                        otp_fail = exc
                    else:
                        consumed_challenge = {
                            "challenge_id": None,
                            "factor_tipo": "totp",
                            "hash_documento": sol["payload_sha256"],
                            "consumido_at": datetime.now(UTC),
                        }
            elif gate_activo:
                # DENEGACIÓN por gate — NO se llama a _firmar_personal ni a
                # _gate_2fa: ningún OTP/TOTP se verifica ni se consume (el
                # contador anti-replay de auth-service nunca se toca en un
                # acto que se va a rechazar). El asiento se escribe AQUÍ,
                # dentro de la txn ambiente, y commitea atómicamente con el
                # resto (igual que firma.turno_firmado) — otp_fail se asigna
                # (nunca raise) para que el `async with` de abajo termine de
                # commitear antes de levantarlo, tras el bloque (:846-847).
                motivo = (
                    "sin_certificado_activo"
                    if personal_res.disponible
                    else "canal_status_no_disponible"
                )
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action="firma.xades_requerida_denegada",
                        object_type=sol["objeto_tipo"],
                        object_ref=sol["objeto_id"],
                        actor=str(actor),
                        payload={
                            "solicitud_id": str(solicitud_id),
                            "nivel_seguridad": sol["nivel_seguridad"],
                            "gate_modo": settings.firma_personal_require_xades,
                            "motivo": motivo,
                            "factor_consumido": False,
                        },
                    )
                if personal_res.disponible:
                    otp_fail = HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "error": "firma_xades_requerida",
                            "detail": (
                                "Este acto exige un certificado XAdES personal según la "
                                "política vigente (firma_personal_require_xades) y usted no "
                                "tiene ninguno activo. Complete la inscripción y expedición "
                                "del certificado personal para poder firmar este acto."
                            ),
                            "status": 422,
                        },
                    )
                else:
                    otp_fail = HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "error": "canal_firma_personal_no_disponible",
                            "detail": (
                                "No se pudo verificar el estado de la credencial de firma "
                                "personal (auth-service); la política de XAdES para este acto "
                                "exige que esa comprobación tenga éxito (falla cerrado)."
                            ),
                            "status": 503,
                        },
                    )
            else:
                # Fallback nativa — comportamiento SIN CAMBIOS desde antes de
                # este incremento (2FA gate, E06 Inc.2, RF-FIR-13). On
                # failure, _gate_2fa RETURNS (never raises) so any durable
                # state change it already made (attempt counter,
                # expirado/bloqueado transitions) survives the commit below
                # — even though the signature itself is skipped. See
                # _gate_2fa docstring for why raising here would be wrong
                # (it would roll back those changes too).
                #
                # Mandatory (not disable-able via firma_2fa_required) once
                # the object is above PUBLICA (nivel_seguridad >= 2): a
                # global kill-switch must not be able to silently drop the
                # second factor for RESERVADA/CLASIFICADA acts (DUR
                # 1074/2015 Cap. 47 — grado de confiabilidad apropiado a las
                # circunstancias). The flag remains a real switch only at
                # PUBLICA / for test environments.
                if settings.firma_2fa_required or sol["nivel_seguridad"] >= 2:
                    otp_fail, consumed_challenge = await self._gate_2fa(
                        solicitud_id, actor, sol, payload_opcional, authorization
                    )

            if otp_fail is None:
                # Server-generated timestamp — never from the client (RF-FIR-12).
                sello_dt = datetime.now(UTC)
                sello_iso = sello_dt.isoformat()

                if personal_signed is not None:
                    sign_result = {
                        "payload_sha256": personal_signed.payload_sha256,
                        "firma_blob": personal_signed.signature_value_b64,
                        "provider": personal_signed.provider,
                    }
                    tsa_timestamp_iso = personal_signed.extra.get("tsa_timestamp")
                    firma_row = await FirmaRepository(self._conn).insert(
                        {
                            "objeto_tipo": sol["objeto_tipo"],
                            "objeto_id": sol["objeto_id"],
                            "formato_solicitado": None,
                            "firmante_id": actor,
                            "payload_sha256": personal_signed.payload_sha256,
                            "firma_blob": personal_signed.signature_value_b64,
                            "provider": personal_signed.provider,
                            "sello_tiempo": sello_dt,
                            "certificado_pem": personal_signed.certificado_pem or None,
                            "nivel_conformidad": personal_signed.nivel_conformidad,
                            "xades_level": personal_signed.xades_level,
                            "tipo_firma": "personal",
                            "certificado_fingerprint": personal_signed.certificado_fingerprint,
                            "signed_xml_sha256": hashlib.sha256(
                                personal_signed.signed_xml
                            ).hexdigest(),
                            "tsa_token": personal_signed.extra.get("tsa_token"),
                            "tsa_timestamp": (
                                datetime.fromisoformat(tsa_timestamp_iso)
                                if tsa_timestamp_iso
                                else None
                            ),
                            "tsa_cert_fingerprint": personal_signed.extra.get(
                                "tsa_cert_fingerprint"
                            ),
                            "tsa_acreditado": personal_signed.extra.get(
                                "tsa_acreditado", False
                            ),
                            # Anclaje de la sub-CA de usuarios (Incremento 2,
                            # Baja #4) — columna EXISTENTE reutilizada, misma
                            # semántica que el ancla LT institucional (nunca
                            # ambas la pueblan a la vez, ver
                            # app.providers.local.LocalSigner.sign). None si
                            # el leaf no tenía sub-CA anclada (grandfather
                            # Incremento 1) — la firma queda igual que antes.
                            "ca_fingerprint": personal_signed.extra.get("ca_fingerprint"),
                        }
                    )
                else:
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
                            "detail": "Otra petición ya resolvió este turno",
                            "status": 409,
                        },
                    )

                # Promote solicitud to 'firmado' when no more pending turns exist.
                next_active = await self._repo.turno_activo(solicitud_id)
                if next_active is None:
                    await self._repo.actualizar_estado_solicitud(solicitud_id, "firmado")
                    await self._avisar_circuito_completo(sol)

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
                            # TOTP has no firma_challenge row — consumed_challenge
                            # carries challenge_id=None for that factor (D1); guard
                            # against str(None) turning into the literal string
                            # "None" here (only stringify when there IS a challenge).
                            "challenge_id": (
                                str(consumed_challenge["challenge_id"])
                                if consumed_challenge and consumed_challenge.get("challenge_id")
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

    # ------------------------------------------------------------------
    # Firma PERSONAL PKI por-usuario (E17/F4, Incremento 1)
    # ------------------------------------------------------------------

    async def _resolve_personal_signing_status(
        self, authorization: str | None
    ) -> _PersonalSigningStatus:
        """Consulta ``GET /internal/signing-key/status`` (auth-service) para
        el ACTOR (identidad derivada del ``authorization`` reenviado, D1) y
        devuelve una resolución de TRES estados (E17/F4, Incremento 3 — antes
        colapsaba "sin cert" y "canal caído" al mismo ``None``, ver
        ``_PersonalSigningStatus``).

        Sin ``Authorization`` reenviado, no hay forma de identificar al actor
        ante auth-service para este canal — se resuelve ``disponible=True,
        body=None`` (mismo bucket que "sin cert activo"): NO es un fallo de
        infraestructura ajeno, es una precondición del propio acto (ver
        ``_xades_gate_activo`` — bajo el gate, el llamante recibe 422
        ``firma_xades_requerida``, no 503).

        Con el gate ``'off'`` (default), AMBOS casos ``body=None`` siguen
        cayendo a la firma nativa en ``firmar()`` — retrocompat exacta con el
        fail-open de Inc.1/2. Con el gate activo para el nivel del acto,
        ``firmar()`` distingue ``disponible`` para elegir 422 vs 503 (ver
        ``app/core/config.py::firma_personal_require_xades``).
        """
        if not authorization:
            return _PersonalSigningStatus(body=None, disponible=True)
        try:
            body = await get_signing_key_status_remote(authorization)
        except SigningKeyStatusUnavailableError:
            logger.warning(
                "No se pudo consultar signing-key/status; fallback a firma nativa "
                "(o denegación bajo gate — ver _xades_gate_activo)",
                exc_info=True,
            )
            return _PersonalSigningStatus(body=None, disponible=False)
        if body.get("estado") != "activo" or not body.get("cert_pem"):
            return _PersonalSigningStatus(body=None, disponible=True)
        return _PersonalSigningStatus(body=body, disponible=True)

    async def _firmar_personal(
        self,
        *,
        sol: asyncpg.Record,
        payload_bytes: bytes,
        authorization: str | None,
        totp_code: str,
        cert_pem: str,
    ) -> SignedDoc:
        """Produce una firma XAdES-B/T PERSONAL (E17/F4, Incremento 1) —
        envuelve ``payload_bytes`` en un XML mínimo (``_build_personal_envelope``)
        y delega en ``LocalSigner`` (proveedor generalizado, ver
        ``app.providers.local``) con un ``SignerRef.remote_sign`` que llama a
        ``sign_digest_remote`` — el TOTP se verifica-y-consume ATÓMICAMENTE
        en auth-service, junto con el uso de la clave, NUNCA por
        ``_gate_2fa`` de este servicio (D1 del contrato de firma personal).

        Lanza ``HTTPException`` (NUNCA otra excepción) en cualquier fallo —
        el llamante (``firmar()``) la captura y la trata exactamente como un
        fallo de ``_gate_2fa`` (devuelto, no lanzado dentro de la
        transacción ambiente).

        Extract-method (E17/F4 Incremento 1, ruta ``POST /sign-personal``):
        el cuerpo criptográfico (construcción del ``SignerRef``/``remote_sign``
        + delegación en ``LocalSigner``, incluido el anclaje de la sub-CA de
        usuarios — Incremento 2, Baja #4) vive ahora en
        ``app.services.personal_signing.sign_personal_xml``, COMPARTIDO con
        ``PersonalSigningService.sign`` (firma personal del acta de
        transferencia, un payload que YA es XML válido, sin el envoltorio
        ``_build_personal_envelope`` que este método SÍ necesita para el
        hash/identificador no-XML de un turno de la cadena). Este método solo
        aporta el envoltorio — el resto es IDÉNTICO byte a byte al
        comportamiento preexistente.
        """
        envelope = _build_personal_envelope(
            objeto_tipo=sol["objeto_tipo"], objeto_id=sol["objeto_id"], payload_bytes=payload_bytes
        )
        return await sign_personal_xml(
            xml_bytes=envelope,
            objeto_tipo=sol["objeto_tipo"],
            objeto_id=sol["objeto_id"],
            authorization=authorization,
            totp_code=totp_code,
            cert_pem=cert_pem,
        )

    async def _gate_2fa(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        sol: asyncpg.Record,
        body: FirmarRequest | None,
        authorization: str | None = None,
    ) -> tuple[HTTPException | None, dict | None]:
        """
        Verifies and consumes the 2FA credential for a personal-signature turn
        (RF-FIR-13), one of two mutually-exclusive factors:

          - TOTP (RFC 6238) — ``body.totp_code``, verified against
            auth-service (``verify_totp``). Preferred when present.
          - OTP-correo (E06 Inc.2) — ``body.challenge_id`` + ``body.otp``,
            verified locally against ``firma_challenge``.

        Deterministic selection (never silently falls through between the
        two): ``totp_code`` present -> ALWAYS the TOTP path, even if
        auth-service reports the credential isn't active (422
        ``totp_no_activo`` — an actionable error, not a silent fallback to
        email); only when ``totp_code`` is absent does ``challenge_id``+
        ``otp`` get considered; neither present -> 409 ``2fa_requerido``.

        MUST run inside the caller's ambient transaction (the same one
        holding the turno FOR UPDATE lock) — see firmar().

        Returns (None, consumed) on success. For OTP-correo, the challenge
        row is marked 'consumido' here, atomically with the marcar_turno()
        that follows in the same ambient transaction (anti-TOCTOU/replay).
        For TOTP, the anti-replay advance already committed durably inside
        auth-service's OWN transaction (D1 of the TOTP contract) before this
        method ever sees the result — there is nothing further to consume
        here. Either way, ``consumed`` carries the authoritative factor_tipo,
        challenge_id (``None`` for TOTP) and consumido_at — the values
        firmar() embeds verbatim into the firma.turno_firmado audit entry,
        never the client-supplied challenge_id from ``body`` nor a value
        inferred from configuration (defense-in-depth: the audited evidence
        always reflects what was actually verified-and-consumed).

        Returns (HTTPException, None) (never raises) on failure — the caller
        raises it AFTER the ambient transaction has committed. For OTP-correo
        this is load-bearing: some failure branches mutate firma_challenge
        (attempt counter, 'expirado'/'bloqueado' transitions) and that state
        MUST survive even though the overall sign attempt is aborted — raising
        immediately would propagate out of firmar()'s
        `async with self._conn.transaction():` and roll back the whole
        ambient transaction, silently undoing those durable state changes
        along with it. For TOTP the counter is already durable in
        auth-service's own transaction regardless of what happens here, so
        this signature-service transaction has nothing local to preserve
        beyond the audit entry below — using the SAME return-then-raise-
        after-commit convention (rather than raising directly) is what lets
        that audit entry ride along with the ambient commit instead of being
        rolled back by an early raise.
        """
        challenge_repo = ChallengeRepository(self._conn)

        totp_code = body.totp_code if body else None
        challenge_id = body.challenge_id if body else None
        otp = body.otp if body else None

        if totp_code is not None and settings.totp_enabled:
            return await self._gate_2fa_totp(solicitud_id, actor, sol, totp_code, authorization)

        if challenge_id is None or otp is None:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "2fa_requerido",
                    "detail": "challenge_id y otp son obligatorios (RF-FIR-13)",
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
                    "detail": "El desafío no existe",
                    "status": 409,
                },
            ), None
        if challenge["firmante_id"] != actor:
            return HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "challenge_ajeno",
                    "detail": "El desafío pertenece a otro actor",
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
                    "detail": "El desafío se emitió para otra solicitud de firma",
                    "status": 409,
                },
            ), None
        if challenge["hash_documento"] != sol["payload_sha256"]:
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_documento_distinto",
                    "detail": "El desafío se emitió para otro documento",
                    "status": 409,
                },
            ), None
        if challenge["estado"] == "consumido":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_consumido",
                    "detail": "El desafío ya se usó",
                    "status": 409,
                },
            ), None
        if challenge["estado"] == "bloqueado":
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_bloqueado",
                    "detail": "El desafío quedó bloqueado tras demasiados intentos fallidos",
                    "status": 409,
                },
            ), None

        now = datetime.now(UTC)
        if now >= challenge["expira_at"]:
            await challenge_repo.marcar_estado(challenge["challenge_id"], "expirado")
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_expirado",
                    "detail": "El desafío expiró",
                    "status": 409,
                },
            ), None
        if challenge["intentos"] >= challenge["max_intentos"]:
            await challenge_repo.marcar_estado(challenge["challenge_id"], "bloqueado")
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "challenge_bloqueado",
                    "detail": "El desafío quedó bloqueado tras demasiados intentos fallidos",
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
                    "detail": "El código indicado es incorrecto",
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

    async def _gate_2fa_totp(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        sol: asyncpg.Record,
        totp_code: str,
        authorization: str | None,
    ) -> tuple[HTTPException | None, dict | None]:
        """
        TOTP branch of ``_gate_2fa`` — verifies ``totp_code`` against
        auth-service's ``POST /internal/totp/verify``, forwarding the
        firmante's own bearer (D1: identity comes from the token, never from
        a body field).

        No local ``firma_challenge`` row exists for this factor — auth-service
        does the anti-replay bookkeeping (``last_timestep``) atomically in its
        OWN transaction, which is already durable by the time this method
        returns, regardless of what happens to signature-service's ambient
        transaction afterwards (accepted atomicity caveat, D1: if THIS
        transaction later rolls back, the TOTP code is "burned" for its
        ~30s window — fail-safe over-consumption, never a replay).
        """
        try:
            result = await verify_totp(authorization, totp_code, solicitud_id=solicitud_id)
        except TotpVerifyUnavailableError:
            return HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "canal_2fa_no_disponible",
                    "detail": "No se pudo verificar el código TOTP (auth-service)",
                    "status": 503,
                },
            ), None

        now = datetime.now(UTC)

        if result.get("valido"):
            consumed = {
                "challenge_id": None,
                "factor_tipo": "totp",
                "hash_documento": sol["payload_sha256"],
                "consumido_at": now,
            }
            return None, consumed

        motivo = result.get("motivo")
        intentos_restantes = result.get("intentos_restantes")
        locked_until = result.get("locked_until")

        if motivo == "no_activo":
            exc = HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "totp_no_activo",
                    "detail": "No tiene un segundo factor (TOTP) activo (RF-FIR-13)",
                    "status": 422,
                },
            )
        elif motivo == "bloqueado":
            exc = HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "error": "totp_bloqueado",
                    "detail": "El TOTP quedó bloqueado tras demasiados intentos fallidos",
                    "locked_until": locked_until,
                    "status": 423,
                },
            )
        else:  # "codigo_incorrecto" | "replay" | anything unexpected from auth-service
            exc = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "totp_incorrecto",
                    "detail": "El código TOTP indicado es incorrecto",
                    "intentos_restantes": intentos_restantes,
                    "status": 401,
                },
            )

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
                    "challenge_id": None,
                    "factor_tipo": "totp",
                    "solicitud_id": str(solicitud_id),
                    "motivo": motivo,
                    "intentos_restantes": intentos_restantes,
                },
            )
        return exc, None

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
                    "detail": f"La solicitud de firma ya está en estado «{sol['estado']}»",
                    "status": 409,
                },
            )

        if not await self._repo.es_firmante(solicitud_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_firmante",
                    "detail": "El actor no pertenece a esta cadena de firma",
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
                    "detail": "No hay ningún turno activo",
                    "status": 409,
                },
            )
        if turno["firmante_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "No es el turno de este actor en la cadena de firma (RF-FIR-04)",
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
                        "detail": "El turno activo cambió antes de registrar el rechazo",
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
                        "detail": "Otra petición ya resolvió este turno",
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

    async def dar_visto_bueno(
        self,
        solicitud_id: uuid.UUID,
        actor: uuid.UUID,
        comentario: str | None = None,
    ) -> SolicitudResponse:
        """Resuelve un turno de `rol='visto_bueno'` (E23, RF-PRO-10).

        ── Qué lo separa de `firmar`, y por qué ───────────────────────────────

        Un visto bueno **no compromete a la entidad**: dice «he leído esto y me
        parece bien», no «esto sale con mi firma». Por eso el turno se resuelve
        sin producir una `firma`, y el documento definitivo lo genera solo el
        turno de `rol='firma'`. Presentar los dos actos igual atribuiría a un
        revisor un compromiso que no adquirió.

        ── Sobre el segundo factor (D-6) ──────────────────────────────────────

        **El anclaje al hash es obligatorio siempre; la PKI y el 2FA no.**
        `_gate_documento_vigente` corre aquí igual que en `firmar`, así que un
        visto bueno siempre sabe QUÉ texto aprobó — que es lo que hoy falta.
        Exigir además credencial de firma y segundo factor a cada revisor es
        viable en una entidad de tres personas y no en una de cuarenta; el
        anclaje al hash más la auditoría inmutable ya prueban qué vio cada uno.
        El firmante final sí firma con PKI: es quien compromete a la entidad.

        Un turno de `rol='firma'` NO se puede resolver por aquí: sería
        exactamente la vía para comprometer a la entidad sin firmar.
        """
        sol = await self._get_or_404(solicitud_id)
        # No-read-up antes que cualquier otra guarda (RF-SEG-08, D-15).
        await self._assert_can_read(sol, actor)

        if sol["estado"] != "pendiente":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_no_pendiente",
                    "detail": f"La solicitud ya está en estado «{sol['estado']}»",
                    "status": 409,
                },
            )

        # El mismo gate que la firma: un visto bueno sobre un texto que ya
        # cambió no dice nada de lo que hay ahora.
        await self._gate_documento_vigente(sol)

        if not await self._repo.es_firmante(solicitud_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_firmante",
                    "detail": "El actor no pertenece a esta cadena",
                    "status": 403,
                },
            )

        async with self._conn.transaction():
            turno = await self._repo.turno_activo_for_update(solicitud_id)
            if turno is None or turno["firmante_id"] != actor:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "El turno activo cambió antes de registrar el visto bueno",
                        "status": 409,
                    },
                )
            if turno["rol"] != "visto_bueno":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "turno_es_de_firma",
                        "detail": (
                            "Este turno es de FIRMA, no de visto bueno: resolverlo "
                            "por aquí comprometeria a la entidad sin firmar. Use "
                            "POST /signature/requests/{id}/sign."
                        ),
                        "status": 409,
                    },
                )

            ok = await self._repo.marcar_turno(
                turno["id"], "firmado", comentario=comentario
            )
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "solicitud_no_pendiente",
                        "detail": "Otra petición ya resolvió este turno",
                        "status": 409,
                    },
                )

            # Si no quedan turnos, la cadena está completa. Solo puede ocurrir
            # cuando el último es de firma —el schema exige al menos uno—, así
            # que aquí siempre queda alguno; se comprueba igualmente para no
            # dejar la cabecera 'pendiente' sobre una cadena sin turnos.
            if await self._repo.turno_activo(solicitud_id) is None:
                await self._repo.actualizar_estado_solicitud(solicitud_id, "firmado")
                await self._avisar_circuito_completo(sol)

            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.visto_bueno_dado",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(actor),
                    payload={
                        "solicitud_id": str(solicitud_id),
                        "orden": turno["orden"],
                        # La huella va en el asiento: es lo que convierte
                        # «aprobó» en «aprobó ESTE texto».
                        "payload_sha256": sol["payload_sha256"],
                        "comentario": comentario,
                    },
                )

        return await self.get(solicitud_id, actor)

    async def firmar_lote(
        self,
        items: list[LoteItem],
        actor: uuid.UUID,
        authorization: str | None = None,
    ) -> LoteResponse:
        """
        Batch sign: attempts firmar() for each item, each with its OWN 2FA
        credential — one OTP-correo pair OR one totp_code per document, never
        reused across items even when several turns belong to the same actor.

        A failure on one item does NOT abort the rest. Each result carries
        'firmado' on success or 'omitido' with the error code as detalle
        (includes 2FA failures: 2fa_requerido, otp_incorrecto, challenge_*,
        totp_incorrecto, totp_no_activo, totp_bloqueado; and, under the
        XAdES gate — E17/F4 Incremento 3 — firma_xades_requerida,
        canal_firma_personal_no_disponible). The gate is re-evaluated
        PER ITEM inside each item's own firmar() call: a certificate
        satisfying one item never covers another, and each denied item
        gets its own atomic audit entry.
        """
        resultados: list[ResultadoLoteItem] = []
        for item in items:
            try:
                body = FirmarRequest(
                    challenge_id=item.challenge_id, otp=item.otp, totp_code=item.totp_code
                )
                await self.firmar(item.solicitud_id, actor, body, authorization)
                resultados.append(
                    ResultadoLoteItem(solicitud_id=item.solicitud_id, resultado="firmado")
                )
            except HTTPException as exc:
                # `object` y no el `str` que declara Starlette: FastAPI permite
                # que `detail` sea un dict (es lo que hacen los routers de este
                # servicio), y la comprobación de abajo cuenta con ello. Sin
                # ensanchar la anotación, `isinstance(detail, dict)` es código
                # que el verificador de tipos considera imposible.
                detail: object = exc.detail
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

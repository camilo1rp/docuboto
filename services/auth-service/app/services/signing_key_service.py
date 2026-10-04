# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
SigningKeyService — orquesta el ciclo de vida de la firma personal PKI
por-usuario (E17/F4, Incremento 1, custodia servidor — postura B).

D1 OWNERSHIP: la clave privada vive en AUTH-SERVICE, junto a ``auth_users`` y
al secreto TOTP — signature-service NUNCA la ve, solo llama a
``POST /internal/signing-key/sign-digest`` (ver ``app/routers/signing_key.py``).

INVARIANTE RECTOR: la clave privada se cifra en reposo (AESGCM) y solo se
descifra en memoria dentro de :meth:`load_and_sign_digest`, para el tiempo
mínimo indispensable de firmar UN digest — se descarta (sale de scope) al
retornar. NINGÚN método de esta clase la devuelve, loguea, audita o publica
jamás — ni una sola vez (a diferencia del secreto TOTP, revelado una vez en
el enroll; ver ``app/services/totp_service.py``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.signing_keys import (
    UserCaNotConfiguredError,
    build_aad,
    build_csr,
    build_subject_name,
    csr_to_pem,
    decrypt_private_key,
    encrypt_private_key,
    generate_keypair,
    load_private_key_pem,
    load_user_ca_certificate,
    private_key_to_pem,
    sign_digest,
)
from app.repositories.rbac_repository import RBACRepository
from app.repositories.signing_key_repository import SigningKeyRepository
from app.schemas.signing_key import (
    SigningKeyEnrollResponse,
    SigningKeyRevocationStatusResponse,
    SigningKeyStatusResponse,
)
from app.services.totp_service import TotpService

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"


def _cert_fingerprint(cert: x509.Certificate) -> str:
    return cert.fingerprint(hashes.SHA256()).hex()


def _require_audit_available() -> None:
    """Fail-closed (E17/F4, Incremento 2 — Baja de auditoría #1): revocar o
    activar una credencial de firma cambia su valor legal (firmable <->
    no firmable), así que estos TRES caminos (``revoke_self``,
    ``revoke_admin``, ``activar_certificado``) se REHÚSAN a operar si la
    librería de auditoría inmutable no está instalada, en vez de saltarse
    el asiento en silencio como antes (``if _AUDIT_AVAILABLE:`` sin
    ``else``). A diferencia de :meth:`SigningKeyService._audit_event`
    (best-effort, usado por ``enroll``/``load_and_sign_digest`` — no
    legal-crítico igual), aquí la ausencia de traza aborta la operación
    ANTES de tocar la fila."""
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "auditoria_no_disponible",
                "detail": (
                    "Auditoría inmutable no disponible — no se puede "
                    "revocar/activar una credencial de firma sin traza"
                ),
                "status": 500,
            },
        )


class SigningKeyService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = SigningKeyRepository(conn)
        self._rbac = RBACRepository(conn)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_cargo(self, user_id: uuid.UUID) -> str | None:
        """Mejor esfuerzo: ``auth_groups`` no modela un "cargo" formal en
        este incremento — se usa el/los nombre(s) de grupo(s) RBAC del
        usuario (p. ej. "operadores, archivistas") como aproximación, o
        ``None`` si no pertenece a ninguno. Nunca bloquea el enroll."""
        rows = await self._conn.fetch(
            "SELECT g.name FROM auth_groups g "
            "JOIN auth_memberships m ON m.group_id = g.id "
            "WHERE m.user_id = $1 ORDER BY g.name",
            user_id,
        )
        names = [r["name"] for r in rows]
        return ", ".join(names) if names else None

    async def _audit_event(
        self,
        *,
        user_id: uuid.UUID,
        tenant_slug: str,
        action: str,
        payload: dict | None = None,
    ) -> None:
        """Best-effort audit — NUNCA incluye la clave privada, su ciphertext
        ni su nonce (solo metadatos públicos: estado, serial, fingerprint)."""
        if not _AUDIT_AVAILABLE:
            return
        try:
            await _audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action=action,
                object_type="signing_key",
                object_ref=str(user_id),
                actor=str(user_id),
                payload=payload or {},
                canal=_audit.CANAL_PETICION,
            )
        except Exception:  # noqa: BLE001 — best-effort, never fails the request.
            logger.warning("signing_key audit append failed (action=%s)", action, exc_info=True)

    # ------------------------------------------------------------------
    # Enroll
    # ------------------------------------------------------------------

    async def enroll(
        self,
        user_id: uuid.UUID,
        tenant_slug: str,
        step_up_totp_code: str | None = None,
    ) -> SigningKeyEnrollResponse:
        """Genera un par RSA-2048 fresco, cifra la clave privada en reposo y
        guarda una fila ``pendiente_emision``. Devuelve el CSR PEM (público) —
        NUNCA la clave privada.

        Un re-enroll (credencial previa ``pendiente_emision`` o ``activo``)
        la revoca atómicamente antes de insertar la nueva — mismo patrón
        idempotente que ``TotpService.enroll``.

        Step-up (E17/F4, Incremento 2 — cierra el residual de Incremento 1):
        re-enrolar sobre una credencial ``activo`` la revoca INMEDIATAMENTE
        (deja ``pendiente_emision``, inútil hasta la emisión offline) —
        aunque la clave nueva no sea firmable de inmediato, la VIEJA sí lo
        era, así que un secuestro de sesión podría auto-provocar una
        denegación de servicio de firma a voluntad. Ahora exige
        ``step_up_totp_code``, verificado-y-consumido vía ``TotpService``
        (igual que :meth:`load_and_sign_digest`) — NUNCA material de la
        propia signing key (no tiene "código"). Re-enrolar sobre
        ``pendiente_emision`` (nunca firmó nada) sigue sin exigir step-up,
        sin cambios.
        """
        existing = await self._repo.get_by_user(user_id)
        user = await self._rbac.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "forbidden", "detail": "El usuario no está aprovisionado en el sistema", "status": 403},
            )

        reenrolling_active = existing is not None and existing["estado"] == "activo"
        if reenrolling_active and not step_up_totp_code:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "signing_key_activa_requiere_step_up",
                    "detail": (
                        "Ya existe una credencial de firma activa: para volver a "
                        "inscribirla hace falta un código TOTP de reautenticación"
                    ),
                    "status": 409,
                },
            )

        cargo = await self._resolve_cargo(user_id)
        key = generate_keypair()
        subject = build_subject_name(
            username=user["username"], email=user.get("email"), cargo=cargo
        )
        csr_pem = csr_to_pem(build_csr(key, subject)).decode()
        private_key_pem = private_key_to_pem(key)

        aad = build_aad(tenant_slug=tenant_slug, user_id=str(user_id))
        # Cifra con la KEK ACTIVA del llavero (rotación de custodia, E17/F4) y
        # persiste su versión — hasta que se rote, activa=1 = comportamiento
        # exacto anterior. El re-wrap converge las filas viejas a esta versión.
        active_version, active_key = settings.signing_key_active_key()
        ciphertext, nonce = encrypt_private_key(private_key_pem, key=active_key, aad=aad)

        sign_fail: HTTPException | None = None
        async with self._conn.transaction():
            if reenrolling_active:
                assert step_up_totp_code is not None
                totp_result = await TotpService(self._conn).verify(
                    user_id, tenant_slug, step_up_totp_code, contexto="signing-key-enroll"
                )
                if not totp_result.valido:
                    sign_fail = _map_totp_failure(totp_result)
            if sign_fail is None:
                if existing is not None:
                    await self._repo.revoke(existing["id"])
                await self._repo.insert_pending(
                    user_id=user_id,
                    csr_pem=csr_pem,
                    private_key_cifrado=ciphertext,
                    private_key_nonce=nonce,
                    subject_dn=subject.rfc4514_string(),
                    cargo=cargo,
                    key_version=active_version,
                )

        if sign_fail is not None:
            raise sign_fail

        action = "signing_key.reenrolado" if existing is not None else "signing_key.enrolado"
        await self._audit_event(user_id=user_id, tenant_slug=tenant_slug, action=action)
        return SigningKeyEnrollResponse(csr_pem=csr_pem)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self, user_id: uuid.UUID) -> SigningKeyStatusResponse:
        row = await self._repo.get_by_user(user_id)
        if row is None:
            return SigningKeyStatusResponse(estado="no_configurado")
        return SigningKeyStatusResponse(
            estado=row["estado"],
            cert_pem=row.get("cert_pem"),
            serial=row.get("serial"),
            subject_dn=row.get("subject_dn"),
            not_before=row.get("not_before"),
            not_after=row.get("not_after"),
            activated_at=row.get("activated_at"),
        )

    # ------------------------------------------------------------------
    # Activation (carga del certificado emitido OFFLINE)
    # ------------------------------------------------------------------

    async def activar_certificado(
        self, user_id: uuid.UUID, cert_pem: str, tenant_slug: str
    ) -> SigningKeyStatusResponse:
        """Pasa la credencial ``pendiente_emision`` a ``activo`` cargando el
        certificado emitido OFFLINE (``scripts/gen-user-cert.sh`` en
        producción; una CA de test en memoria en pytest — ver
        ``tests/test_signing_key.py``).

        Verifica que el certificado corresponde REALMENTE al CSR de esta
        fila (misma clave pública) antes de activarlo — evita que un
        certificado ajeno (de otro CSR) se active por error/malicia sobre
        esta fila.

        ANCLAJE DE LA SUB-CA (E17/F4, Incremento 2, Baja #4): además del
        chequeo leaf↔CSR de arriba, exige que el leaf esté REALMENTE emitido
        por la sub-CA de usuarios montada en este servicio (SOLO su
        certificado PÚBLICO, ``ca-users.crt`` — ver
        ``app.core.signing_keys.load_user_ca_certificate``; ``ca.key`` NUNCA
        se monta aquí) — ``leaf.verify_directly_issued_by(sub_ca)``.
        Fail-closed: si la sub-CA no está configurada, o el leaf no encadena
        con ella, esta activación se RECHAZA (503/422) — nunca se activa una
        credencial cuya emisora este servicio no puede verificar.

        RF-SEG-08 / Ac. AGN 001 (S1): esta transición vuelve la credencial
        FIRMABLE con valor legal, así que el asiento ``signing_key.activada``
        se emite DENTRO de la misma transacción que el ``UPDATE`` de
        :meth:`SigningKeyRepository.activate` — a diferencia de
        :meth:`_audit_event` (best-effort, usado por ``enroll`` y
        ``load_and_sign_digest``), aquí un fallo del asiento debe abortar la
        activación en vez de dejarla sin traza.
        """
        row = await self._repo.get_by_user(user_id)
        if row is None or row["estado"] != "pendiente_emision":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "signing_key_no_pendiente",
                    "detail": "Este usuario no tiene ninguna inscripción de credencial de firma pendiente",
                    "status": 409,
                },
            )
        _require_audit_available()
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
            csr = x509.load_pem_x509_csr(row["csr_pem"].encode())
        except Exception as exc:  # noqa: BLE001 — material ilegible, fail-closed
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "certificado_o_csr_ilegible",
                    "detail": str(exc),
                    "status": 422,
                },
            ) from None

        # Se compara el SubjectPublicKeyInfo en DER, no `public_numbers()`:
        # ese método solo existe en RSA/EC/DSA, así que un CSR con clave
        # Ed25519 o Ed448 reventaba aquí con un AttributeError en vez de
        # comprobarse. `public_bytes` existe en TODOS los tipos de clave
        # pública y compara exactamente la misma identidad de clave.
        _DER = serialization.Encoding.DER
        _SPKI = serialization.PublicFormat.SubjectPublicKeyInfo
        if cert.public_key().public_bytes(_DER, _SPKI) != csr.public_key().public_bytes(
            _DER, _SPKI
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "certificado_no_corresponde_al_csr",
                    "detail": "El certificado no fue emitido para el CSR de esta credencial",
                    "status": 422,
                },
            )

        try:
            sub_ca = load_user_ca_certificate()
        except UserCaNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sub_ca_usuarios_no_configurada",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from None

        try:
            cert.verify_directly_issued_by(sub_ca)
        except Exception as exc:  # noqa: BLE001 — cadena rota, fail-closed
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "certificado_no_emitido_por_sub_ca",
                    "detail": (
                        "El certificado no fue emitido por la sub-CA de "
                        f"usuarios montada en este servicio: {exc}"
                    ),
                    "status": 422,
                },
            ) from None

        serial = format(cert.serial_number, "x")
        fingerprint = _cert_fingerprint(cert)
        user_ca_fingerprint = _cert_fingerprint(sub_ca)

        async with self._conn.transaction():
            updated = await self._repo.activate(
                row["id"],
                cert_pem=cert_pem,
                serial=serial,
                not_before=cert.not_valid_before_utc,
                not_after=cert.not_valid_after_utc,
                certificado_fingerprint=fingerprint,
                user_ca_fingerprint=user_ca_fingerprint,
            )
            # Actor de sistema: no hay Bearer de usuario interactivo detrás
            # de esta ruta (gate SOLO X-Internal-Token) — el operador que
            # emitió el certificado offline ya validó la identidad fuera de
            # banda. Payload SOLO material público (nunca la clave, ni
            # cifrada). `_require_audit_available()` ya garantizó arriba que
            # `_audit` está disponible — sin `if` porque saltarse el asiento
            # en silencio es justo lo que este incremento cierra.
            await _audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="signing_key.activada",
                object_type="signing_key",
                object_ref=str(user_id),
                actor="system:offline-issuer",
                canal="system",
                payload={
                    "serial": serial,
                    "certificado_fingerprint": fingerprint,
                    "user_ca_fingerprint": user_ca_fingerprint,
                    "not_after": cert.not_valid_after_utc.isoformat(),
                },
            )

        return SigningKeyStatusResponse(
            estado=updated["estado"],
            cert_pem=updated.get("cert_pem"),
            serial=updated.get("serial"),
            subject_dn=updated.get("subject_dn"),
            not_before=updated.get("not_before"),
            not_after=updated.get("not_after"),
            activated_at=updated.get("activated_at"),
        )

    # ------------------------------------------------------------------
    # Firma de un digest (internal, service-to-service)
    # ------------------------------------------------------------------

    async def load_and_sign_digest(
        self,
        user_id: uuid.UUID,
        tenant_slug: str,
        digest: bytes,
        totp_code: str,
    ) -> tuple[bytes, str, str, str | None, datetime | None, str | None, str | None]:
        """Verifica-y-consume ``totp_code`` (TOTP, RFC 6238) y, si válido,
        descifra la clave privada EN MEMORIA y firma ``digest`` (ya
        calculado por el llamante — ``Prehashed``), TODO dentro de UNA
        transacción atómica.

        Devuelve ``(signature_der, certificado_pem, certificado_fingerprint,
        serial, not_after, ca_cert_pem, ca_fingerprint)`` — NUNCA la clave
        privada. ``ca_cert_pem``/``ca_fingerprint`` (Incremento 2, Baja #4)
        son el PEM/huella de la sub-CA de usuarios ANCLADA en
        :meth:`activar_certificado` (``row["user_ca_fingerprint"]``),
        releída del filesystem montado (``load_user_ca_certificate``) y
        confirmada bit a bit contra la huella persistida — si la sub-CA
        montada HOY no coincide con la que se ancló entonces (rotación de
        material, o el operador la desmontó), o la fila nunca tuvo una
        (grandfather de Incremento 1), ambos quedan ``None`` y el llamante
        (signature-service) produce una firma personal SIN anclaje, igual
        que antes de este incremento — nunca falla la firma por esto solo.

        Honra ``locked_until`` del TOTP (delegado en
        ``TotpService.verify``, que ya lo comprueba fail-closed ANTES de
        aceptar el código). Igual que ``CadenaFirmaService._gate_2fa``: en
        fallo de 2FA, la excepción se CAPTURA (no se lanza dentro del
        ``async with``) para que el contador de intentos fallidos/bloqueo
        que ``TotpService.verify`` ya escribió (vía el MISMO ``self._conn``,
        como un SAVEPOINT anidado de esta transacción) sobreviva al commit
        aunque la firma se aborte — lanzar inmediatamente arrastraría esa
        escritura al rollback.

        La comprobación de existencia de una clave ``activo`` ocurre ANTES
        de verificar el TOTP: sin clave activa no hay nada que firmar, así
        que no tiene sentido consumir un código TOTP en ese caso.
        """
        sign_fail: HTTPException | None = None
        result: tuple[bytes, str, str, str | None, datetime | None, str | None, str | None] | None = None

        async with self._conn.transaction():
            row = await self._repo.get_by_user_for_update(user_id)
            if row is None or row["estado"] != "activo":
                sign_fail = HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "signing_key_no_activa",
                        "detail": "No tiene una credencial de firma personal activa",
                        "status": 422,
                    },
                )
            elif row["private_key_cifrado"] is None:
                # Defensa en profundidad: una fila con material purgado es
                # SIEMPRE 'revocado' (CHECK chk_auth_user_signing_key_purga) y
                # ya la filtra el `estado != 'activo'` de arriba — este guard es
                # inalcanzable por diseño, pero nunca se intenta descifrar `None`.
                sign_fail = HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "signing_key_no_activa",
                        "detail": "No tiene una credencial de firma personal activa",
                        "status": 422,
                    },
                )
            else:
                totp_result = await TotpService(self._conn).verify(
                    user_id, tenant_slug, totp_code, contexto="signing-key-sign-digest"
                )
                if not totp_result.valido:
                    sign_fail = _map_totp_failure(totp_result)
                else:
                    aad = build_aad(tenant_slug=tenant_slug, user_id=str(user_id))
                    # Selecciona la KEK por la VERSIÓN de la fila (llavero de
                    # rotación) — fail-closed si esa versión no está cargada
                    # (un retiro prematuro aborta la firma, nunca firma con
                    # otra KEK). Sin rotación, key_version=1 = la KEK legacy.
                    # El RuntimeError aquí propaga fuera de la tx → rollback que
                    # también revierte el TOTP recién consumido (asimétrico con
                    # la ruta de fallo-TOTP, que lo preserva). Aceptable: no hay
                    # nada que firmar (KEK ausente) y solo ocurre si el operador
                    # violó el invariante de retirada — una ventana de replay
                    # sobre un acto imposible, no un bypass.
                    private_key_pem = decrypt_private_key(
                        row["private_key_cifrado"],
                        nonce=row["private_key_nonce"],
                        key=settings.signing_key_for_version(row["key_version"]),
                        aad=aad,
                    )
                    private_key = load_private_key_pem(private_key_pem)
                    signature_der = sign_digest(private_key, digest)
                    # Descarte explícito de las referencias locales — salen
                    # de scope al terminar el bloque `with` en cualquier
                    # caso, pero se sueltan aquí mismo apenas se usaron
                    # (REGLA no-fuga: nada de esto debe sobrevivir más de lo
                    # estrictamente necesario).
                    del private_key_pem, private_key

                    cert = x509.load_pem_x509_certificate(row["cert_pem"].encode())

                    ca_cert_pem: str | None = None
                    ca_fingerprint: str | None = None
                    stored_user_ca_fingerprint = row.get("user_ca_fingerprint")
                    if stored_user_ca_fingerprint:
                        try:
                            sub_ca = load_user_ca_certificate()
                        except UserCaNotConfiguredError:
                            sub_ca = None
                        if sub_ca is not None:
                            current_fp = _cert_fingerprint(sub_ca)
                            if current_fp == stored_user_ca_fingerprint:
                                ca_cert_pem = sub_ca.public_bytes(
                                    serialization.Encoding.PEM
                                ).decode()
                                ca_fingerprint = current_fp

                    result = (
                        signature_der,
                        row["cert_pem"],
                        _cert_fingerprint(cert),
                        row.get("serial"),
                        row.get("not_after"),
                        ca_cert_pem,
                        ca_fingerprint,
                    )

        if sign_fail is not None:
            raise sign_fail
        assert result is not None

        await self._audit_event(
            user_id=user_id,
            tenant_slug=tenant_slug,
            action="signing_key.digest_firmado",
            payload={"certificado_fingerprint": result[2]},
        )
        return result

    # ------------------------------------------------------------------
    # Revocación (E17/F4, Incremento 2 — A2: estado en BD + chequeo en
    # línea, sin CRL offline; NUNCA toca ca.key)
    # ------------------------------------------------------------------

    async def revoke_self(
        self,
        user_id: uuid.UUID,
        tenant_slug: str,
        totp_code: str,
        motivo: str | None = None,
    ) -> None:
        """``DELETE /me/signing-key`` — self-revoke, SIEMPRE gateado por
        step-up TOTP (verifica-y-consume vía ``TotpService``, igual que
        :meth:`load_and_sign_digest`). Revoca la credencial NO-revocada del
        titular (``pendiente_emision`` o ``activo``) — 404 si no hay
        ninguna.

        Fail-closed atómico (a diferencia del ``revoke()`` best-effort de
        ``enroll`` en un re-enroll): el asiento ``signing_key.revocada`` se
        emite DENTRO de la MISMA transacción que el ``UPDATE`` — un fallo
        del asiento aborta la revocación (mismo patrón que
        :meth:`activar_certificado`), nunca queda "revocado" sin traza ni
        "aparenta revocado pero no lo está".
        """
        row = await self._repo.get_by_user(user_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "no_signing_key",
                    "detail": "No signing-key credential to revoke",
                    "status": 404,
                },
            )

        _require_audit_available()

        sign_fail: HTTPException | None = None
        async with self._conn.transaction():
            totp_result = await TotpService(self._conn).verify(
                user_id, tenant_slug, totp_code, contexto="signing-key-revoke"
            )
            if not totp_result.valido:
                sign_fail = _map_totp_failure(totp_result)
            else:
                await self._repo.revoke_atomic(row["id"], revocado_por=user_id, motivo=motivo)
                # `_require_audit_available()` ya garantizó arriba que
                # `_audit` está disponible — sin `if` (ver docstring del
                # helper: saltarse el asiento en silencio es lo que este
                # incremento cierra).
                await _audit.append(
                    self._conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="signing_key.revocada",
                    object_type="signing_key",
                    object_ref=str(user_id),
                    actor=str(user_id),
                    payload={"motivo": motivo, "via": "self"},
                    canal=_audit.CANAL_PETICION,
                )

        if sign_fail is not None:
            raise sign_fail

    async def revoke_admin(
        self,
        target_user_id: uuid.UUID,
        tenant_slug: str,
        actor_admin_id: uuid.UUID,
        motivo: str | None = None,
    ) -> None:
        """``POST /admin/users/{user_id}/signing-key/revoke`` — gateado por
        ``USUA_PERM_ADMIN`` a nivel de router (``require_permission``), sin
        step-up TOTP propio: el permiso administrativo ES el gate. Mismo
        patrón fail-closed atómico que :meth:`revoke_self` (el asiento vive
        DENTRO de la transacción del ``UPDATE``), pero SIN el paso de 2FA —
        aquí no hay un `totp_code` del titular que verificar."""
        row = await self._repo.get_by_user(target_user_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "no_signing_key",
                    "detail": "Este usuario no tiene credencial de firma que revocar",
                    "status": 404,
                },
            )

        _require_audit_available()

        async with self._conn.transaction():
            await self._repo.revoke_atomic(
                row["id"], revocado_por=actor_admin_id, motivo=motivo
            )
            # `_require_audit_available()` ya garantizó arriba que `_audit`
            # está disponible — sin `if` (ver docstring del helper).
            await _audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="signing_key.revocada",
                object_type="signing_key",
                object_ref=str(target_user_id),
                actor=str(actor_admin_id),
                payload={"motivo": motivo, "via": "admin"},
                canal=_audit.CANAL_PETICION,
            )

    async def revocation_status(
        self, certificado_fingerprint: str
    ) -> SigningKeyRevocationStatusResponse:
        """``GET /internal/signing-key/revocation-status`` (signature-service,
        D-02) — resuelve el estado de revocación de un certificado LEAF por
        su huella, EN EL TENANT que ya fijó el ``search_path`` de la
        conexión (aislamiento: una huella de otro tenant simplemente no
        aparece en esta consulta). Devuelve SOLO ``{status, revocado_at, motivo_revocacion}`` —
        nunca cert/clave, ni siquiera cifrada.

        ``desconocido`` fail-closed: sin fila (otro tenant, huella forjada,
        o el titular fue borrado por CASCADE) — NUNCA se reporta ``vigente``
        en ese caso. Una fila ``pendiente_emision`` nunca tiene
        ``certificado_fingerprint`` poblado (se asigna solo al activar), así
        que en la práctica esta consulta solo encuentra filas ``activo``
        (-> ``vigente``) o ``revocado`` (-> ``revocado``)."""
        row = await self._repo.get_by_certificado_fingerprint(certificado_fingerprint)
        if row is None:
            return SigningKeyRevocationStatusResponse(status="desconocido")
        if row["estado"] == "revocado":
            return SigningKeyRevocationStatusResponse(
                status="revocado",
                revocado_at=row.get("revocado_at"),
                motivo_revocacion=row.get("motivo_revocacion"),
            )
        if row["estado"] == "activo":
            return SigningKeyRevocationStatusResponse(status="vigente")
        # pendiente_emision con huella poblada no debería ocurrir (ver
        # docstring) — fail-closed: ni vigente ni revocado, desconocido.
        return SigningKeyRevocationStatusResponse(status="desconocido")


def _map_totp_failure(totp_result: Any) -> HTTPException:
    """Traduce ``InternalTotpVerifyResponse`` (fallo) a HTTPException — mismo
    mapeo que ``CadenaFirmaService._gate_2fa_totp`` en signature-service, para
    que el error observado por signature-service (que reenvía este cuerpo)
    sea consistente entre ambos caminos de consumo de TOTP."""
    motivo = totp_result.motivo
    if motivo == "no_activo":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "totp_no_activo",
                "detail": "No tiene un segundo factor (TOTP) activo",
                "status": 422,
            },
        )
    if motivo == "bloqueado":
        return HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": "totp_bloqueado",
                "detail": "El TOTP quedó bloqueado tras demasiados intentos fallidos",
                "locked_until": (
                    totp_result.locked_until.isoformat() if totp_result.locked_until else None
                ),
                "status": 423,
            },
        )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "totp_incorrecto",
            "detail": "El código TOTP indicado es incorrecto",
            "intentos_restantes": totp_result.intentos_restantes,
            "status": 401,
        },
    )

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

import asyncpg
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.signing_keys import (
    build_aad,
    build_csr,
    build_subject_name,
    csr_to_pem,
    decrypt_private_key,
    encrypt_private_key,
    generate_keypair,
    load_private_key_pem,
    private_key_to_pem,
    sign_digest,
)
from app.repositories.rbac_repository import RBACRepository
from app.repositories.signing_key_repository import SigningKeyRepository
from app.schemas.signing_key import SigningKeyEnrollResponse, SigningKeyStatusResponse
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
            )
        except Exception:  # noqa: BLE001 — best-effort, never fails the request.
            logger.warning("signing_key audit append failed (action=%s)", action, exc_info=True)

    # ------------------------------------------------------------------
    # Enroll
    # ------------------------------------------------------------------

    async def enroll(self, user_id: uuid.UUID, tenant_slug: str) -> SigningKeyEnrollResponse:
        """Genera un par RSA-2048 fresco, cifra la clave privada en reposo y
        guarda una fila ``pendiente_emision``. Devuelve el CSR PEM (público) —
        NUNCA la clave privada.

        Un re-enroll (credencial previa ``pendiente_emision`` o ``activo``)
        la revoca atómicamente antes de insertar la nueva — mismo patrón
        idempotente que ``TotpService.enroll``. A diferencia de TOTP, NO
        exige step-up: la nueva clave nace ``pendiente_emision`` (inútil para
        firmar hasta que el emisor OFFLINE cargue un certificado — ver
        :meth:`activar_certificado`), así que un re-enroll no revoca
        capacidad de firma inmediata por sí solo — el verdadero gate de
        seguridad es la emisión offline, no el enroll.
        """
        existing = await self._repo.get_by_user(user_id)
        user = await self._rbac.get_user(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "forbidden", "detail": "Caller is not provisioned", "status": 403},
            )

        cargo = await self._resolve_cargo(user_id)
        key = generate_keypair()
        subject = build_subject_name(
            username=user["username"], email=user.get("email"), cargo=cargo
        )
        csr_pem = csr_to_pem(build_csr(key, subject)).decode()
        private_key_pem = private_key_to_pem(key)

        aad = build_aad(tenant_slug=tenant_slug, user_id=str(user_id))
        ciphertext, nonce = encrypt_private_key(
            private_key_pem, key=settings.signing_key_encryption_key_bytes(), aad=aad
        )

        async with self._conn.transaction():
            if existing is not None:
                await self._repo.revoke(existing["id"])
            await self._repo.insert_pending(
                user_id=user_id,
                csr_pem=csr_pem,
                private_key_cifrado=ciphertext,
                private_key_nonce=nonce,
                subject_dn=subject.rfc4514_string(),
                cargo=cargo,
                key_version=1,
            )

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
                    "detail": "No pending signing-key enrollment for this user",
                    "status": 409,
                },
            )
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

        if cert.public_key().public_numbers() != csr.public_key().public_numbers():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "certificado_no_corresponde_al_csr",
                    "detail": "El certificado no fue emitido para el CSR de esta credencial",
                    "status": 422,
                },
            )

        serial = format(cert.serial_number, "x")
        fingerprint = _cert_fingerprint(cert)

        async with self._conn.transaction():
            updated = await self._repo.activate(
                row["id"],
                cert_pem=cert_pem,
                serial=serial,
                not_before=cert.not_valid_before_utc,
                not_after=cert.not_valid_after_utc,
            )
            if _AUDIT_AVAILABLE:
                # Actor de sistema: no hay Bearer de usuario interactivo
                # detrás de esta ruta (gate SOLO X-Internal-Token) — el
                # operador que emitió el certificado offline ya validó la
                # identidad fuera de banda. Payload SOLO material público
                # (nunca la clave, ni cifrada).
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
    ) -> tuple[bytes, str, str, str | None, object]:
        """Verifica-y-consume ``totp_code`` (TOTP, RFC 6238) y, si válido,
        descifra la clave privada EN MEMORIA y firma ``digest`` (ya
        calculado por el llamante — ``Prehashed``), TODO dentro de UNA
        transacción atómica.

        Devuelve ``(signature_der, certificado_pem, certificado_fingerprint,
        serial, not_after)`` — NUNCA la clave privada.

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
        result: tuple[bytes, str, str, str | None, object] | None = None

        async with self._conn.transaction():
            row = await self._repo.get_by_user_for_update(user_id)
            if row is None or row["estado"] != "activo":
                sign_fail = HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "signing_key_no_activa",
                        "detail": "Caller has no active personal signing key",
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
                    private_key_pem = decrypt_private_key(
                        row["private_key_cifrado"],
                        nonce=row["private_key_nonce"],
                        key=settings.signing_key_encryption_key_bytes(),
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
                    result = (
                        signature_der,
                        row["cert_pem"],
                        _cert_fingerprint(cert),
                        row.get("serial"),
                        row.get("not_after"),
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


def _map_totp_failure(totp_result) -> HTTPException:  # type: ignore[no-untyped-def]
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
                "detail": "Caller has no active TOTP credential",
                "status": 422,
            },
        )
    if motivo == "bloqueado":
        return HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": "totp_bloqueado",
                "detail": "TOTP is locked after too many failed attempts",
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
            "detail": "The provided TOTP code is incorrect",
            "intentos_restantes": totp_result.intentos_restantes,
            "status": 401,
        },
    )

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Veredicto de una firma existente (E06, RF-FIR-15, B10 §8).

Un solo motor para ``POST /verify``, la verificación pública por código y la
entrada en proceso de ``signature.api``: ninguna superficie tiene una versión
recortada de las comprobaciones. El no-read-up lo aplica quien busca la fila.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

import asyncpg

from auth import api as auth_api
from docuboto.config import signature as settings
from signature.core.constants import (
    NIVELES_CONFORMIDAD_ACREDITADOS,
    PROVIDERS_FIRMA_ELECTRONICA,
)
from signature.core.secret_derivation import KEY_VERSION_HKDF
from signature.providers.local import verify_xades
from signature.repositories.firma_repository import FirmaRepository
from signature.schemas.firma import ArchiveTimestampInfo, VerifyResponse
from signature.services import signer

logger = logging.getLogger(__name__)


def _acreditado(row: dict | asyncpg.Record, *, valida: bool) -> bool:
    """``acreditado`` derivado de lo PERSISTIDO al firmar, y solo si la cripto valida.

    La acreditación es un hecho del momento de firmar (qué sello/CA se usó,
    ``app.core.keys._leer_acreditacion``) y quedó en ``firma.nivel_conformidad``.
    Antes se devolvía un literal ``False`` en verificación: una firma hecha con
    un sello ACREDITADO se reportaba como no acreditada, justo donde el
    comprobante tiene que poder decirlo (AREA-05-02). Lista cerrada por
    admisión (``NIVELES_CONFORMIDAD_ACREDITADOS``), no ``endswith``: un nivel
    nuevo no hereda la acreditación por parecerse a uno que la tiene.
    """
    return bool(valida) and row.get("nivel_conformidad") in NIVELES_CONFORMIDAD_ACREDITADOS


def _revocada_despues_de_firmar(
    revocado_at: object, referencia: datetime | None
) -> bool:
    """¿La revocación ocurrió DESPUÉS del instante de la firma? (AREA-04-13.)

    Una revocación dice que la clave ya no es vigente DESDE ``revocado_at``; no
    dice que lo firmado antes sea falso. Se compara contra el instante de la
    firma, no contra ``now()``. Fail-closed ante la duda: sin ``revocado_at`` o
    sin instante de referencia legibles, o con ``revocado_at`` igual o anterior,
    NO se considera posterior.
    """
    if referencia is None or not revocado_at:
        return False
    try:
        rev = (
            revocado_at
            if isinstance(revocado_at, datetime)
            else datetime.fromisoformat(str(revocado_at).replace("Z", "+00:00"))
        )
    except ValueError:
        return False
    if rev.tzinfo is None:
        rev = rev.replace(tzinfo=UTC)
    ref = referencia if referencia.tzinfo else referencia.replace(tzinfo=UTC)
    return rev > ref


async def _sellos_de_la_firma(conn: asyncpg.Connection, firma_id: uuid.UUID) -> list:
    """Cadena de sellos de archivo (B10 §8). Un tenant sin la migración 026 no tiene cadena."""
    try:
        return list(await FirmaRepository(conn).sellos_de(firma_id))
    except asyncpg.UndefinedTableError:
        return []


async def verificar_xades(
    conn: asyncpg.Connection,
    row: dict | asyncpg.Record,
    payload_xml: str,
    tenant_slug: str,
) -> VerifyResponse:
    """Veredicto de una firma XAdES (``xades_local`` | ``xades_personal``) sobre su XML firmado.

    El MISMO motor para ``POST /verify`` (autenticado, el XML lo aporta quien
    pregunta) y para la verificación pública por código (ADR-054 §2, el XML lo
    descarga el servicio de storage): ninguna de las dos superficies tiene una
    versión recortada de las comprobaciones. Ver el docstring de ``verify``.
    """
    provider_name = row["provider"]
    is_personal = provider_name == "xades_personal"
    fingerprint = row.get("certificado_fingerprint")
    if not fingerprint:
        return VerifyResponse(
            firma_id=row["id"],
            valida=False,
            provider=provider_name,
            xades_level=row.get("xades_level"),
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=False,  # no valida -> no acredita (AREA-05-02)
            motivo="firma_sin_huella_de_certificado_registrada",
        )
    # Ata la verificación a ESTA firma_id (migración 010): sin esto, el
    # signed_xml de OTRO índice firmado con el MISMO sello institucional
    # del tenant se reportaría `valida=True` para cualquier firma_id de
    # ese tenant. `signed_xml_sha256` es NULL en filas anteriores a la
    # migración — se degrada al comportamiento previo (solo huella de
    # certificado) para no romper la verificación de firmas históricas.
    expected_xml_sha256 = row.get("signed_xml_sha256")
    # B10 §8: una firma LTA renovada tiene VARIOS XML válidos (el original y cada
    # renovación) y varias TSA ancladas; ambos salen de `firma_sello_archivo`.
    sellos = await _sellos_de_la_firma(conn, row["id"])
    aceptados = {s["xml_sha256"] for s in sellos if s["estado"] in ("vigente", "caducado")}
    if expected_xml_sha256:
        aceptados.add(expected_xml_sha256)
    if aceptados and hashlib.sha256(
        payload_xml.encode()
    ).hexdigest() not in aceptados:
        return VerifyResponse(
            firma_id=row["id"],
            valida=False,
            provider=provider_name,
            xades_level=row.get("xades_level"),
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=False,  # no valida -> no acredita (AREA-05-02)
            motivo="documento_no_corresponde_a_firma_id",
        )
    advertencias: list[str] = []
    outcome = verify_xades(
        payload_xml.encode(),
        fingerprint,
        expected_tsa_fingerprint=row.get("tsa_cert_fingerprint"),
        # `ca_fingerprint` (misma columna, dos semánticas mutuamente
        # excluyentes por `provider`, ver docstring de la función): ancla
        # LT institucional para xades_local, ancla de la sub-CA de
        # usuarios para xades_personal — NUNCA ambos kwargs a la vez.
        expected_ca_fingerprint=(None if is_personal else row.get("ca_fingerprint")),
        expected_ocsp_responder_fingerprint=row.get("ocsp_responder_fingerprint"),
        expected_user_ca_fingerprint=(row.get("ca_fingerprint") if is_personal else None),
        advertencias=advertencias,
        tsa_fingerprints_archivo=[s["tsa_cert_fingerprint"] for s in sellos],
    )

    # `firma_criptograficamente_valida`: el veredicto de `verify_xades`
    # a secas (criptografía + cadena/anclaje), SIN la dimensión de
    # revocación — NUNCA difama la cripto por un canal de revocación
    # caído (ver docstring de `VerifyResponse`).
    firma_criptograficamente_valida = outcome.valida
    valida = outcome.valida
    motivo = outcome.motivo
    revocacion_status: str | None = None
    revocacion_posterior: bool | None = None

    # Revocación en línea (A2, Incremento 2) — SOLO firmas personales
    # cuya cripto+cadena YA validó (revocar no tiene sentido evaluarlo
    # sobre una firma ya inválida por otra causa, y evita una llamada de
    # red innecesaria). `verify_xades` sigue siendo PURA — esta llamada
    # vive aquí, en el router.
    if is_personal and outcome.valida:
        try:
            status_body = await auth_api.signing_key_revocation_status(
                tenant_slug=tenant_slug, fingerprint=fingerprint
            )
            revocacion_status = status_body.get("status", "desconocido")
        except auth_api.AuthUnavailableError:
            logger.warning(
                "No se pudo consultar signing-key/revocation-status para "
                "firma_id=%s; aplicando verify_personal_revocation_require",
                row["id"],
                exc_info=True,
            )
            revocacion_status = "no_verificable"
            if settings.verify_personal_revocation_require:
                valida = False
                motivo = "revocacion_no_verificable"
            # Fail-open (opt-in, NO default): la cripto ya validó, así
            # que `valida` se queda en True — solo se informa el canal
            # caído vía `revocacion_status`, sin invalidar.
        else:
            # Instante de la firma: SOLO el genTime de una TSA verificada
            # es fecha cierta oponible (RFC 3161; DUR 1074 cap. 47-48). El
            # `sello_tiempo` de la fila lo pone el propio servidor: no
            # sirve para conservar la validez ante una revocación.
            instante_cierto = (
                outcome.tsa_timestamp
                if outcome.sello_tiempo_valido and outcome.tsa_timestamp
                else None
            )
            if (
                revocacion_status == "revocado"
                and status_body.get("motivo_revocacion") == "key_compromise"
            ):
                # Compromiso de la clave: lo firmado ANTES también queda en
                # duda (ETSI EN 319 102-1) — invalida hacia atrás.
                valida = False
                motivo = "clave_comprometida"
            elif (
                revocacion_status == "revocado"
                and instante_cierto is None
                and status_body.get("revocado_at")
            ):
                # Sin TSA verificada no hay forma de probar que la
                # revocación fue posterior: estado indeterminado, NO válida
                # (`revocacion_posterior` queda None).
                valida = False
                motivo = "instante_firma_no_cierto"
            elif revocacion_status == "revocado" and _revocada_despues_de_firmar(
                status_body.get("revocado_at"), instante_cierto
            ):
                # La clave se revocó DESPUÉS de una firma con fecha cierta
                # (AREA-04-13) y el motivo no es compromiso: la firma fue
                # válida cuando se hizo; `valida` se queda como la dejó la
                # cripto y se informa aparte.
                revocacion_posterior = True
            elif revocacion_status != "vigente":
                valida = False
                motivo = (
                    "certificado_revocado"
                    if revocacion_status == "revocado"
                    # `desconocido` fail-closed (huella no encontrada en
                    # este tenant — otro tenant, forjada, o el titular
                    # borrado por CASCADE): NUNCA se reporta `vigente`.
                    else "certificado_desconocido"
                )

    return VerifyResponse(
        firma_id=row["id"],
        valida=valida,
        provider=provider_name,
        # El nivel REALMENTE verificado para ESTE documento (RT-16, Pieza
        # 6.4) — nunca el `xades_level` "intencionado" al firmar. Se cae
        # al persistido en la fila solo si `verify_xades` no llegó a
        # evaluar ningún nivel (p. ej. certificado fuera de vigencia).
        xades_level=outcome.xades_level or row.get("xades_level"),
        nivel_conformidad=row.get("nivel_conformidad"),
        acreditado=_acreditado(row, valida=valida),
        motivo=motivo,
        # (Inc.4) None cuando la firma no lleva SignatureTimeStamp (no
        # aplica, no es un fallo) — ver app.providers.local.VerifyOutcome.
        sello_tiempo_valido=outcome.sello_tiempo_valido,
        tsa_timestamp=outcome.tsa_timestamp,
        lt_material_present=outcome.lt_material_present,
        archive_timestamp_present=outcome.archive_timestamp_present,
        lta_vigente=outcome.lta_vigente,
        revocation_provenance=outcome.revocation_provenance,
        crl_next_update=outcome.crl_next_update,
        ocsp_present=outcome.ocsp_present,
        ocsp_cert_status=outcome.ocsp_cert_status,
        firma_criptograficamente_valida=firma_criptograficamente_valida,
        revocacion_status=revocacion_status,
        revocacion_posterior=revocacion_posterior,
        # Solo se declaran sobre una firma que VALIDA: un aviso sobre un
        # veredicto negativo sería ruido encima del motivo real.
        advertencias=advertencias if valida else [],
        archive_timestamps=(
            [ArchiveTimestampInfo.model_validate(a) for a in outcome.archive_timestamps]
            if valida else []
        ),
    )


async def verificar_firma(
    conn: asyncpg.Connection,
    row: dict | asyncpg.Record,
    payload_xml: str,
    tenant_slug: str,
) -> VerifyResponse:
    """Veredicto de la fila ``row`` sobre ``payload_xml`` (ver ``POST /verify``)."""
    if row.get("provider") in PROVIDERS_FIRMA_ELECTRONICA:
        return await verificar_xades(conn, row, payload_xml, tenant_slug)

    firmante_id = str(row["firmante_id"]) if row["firmante_id"] else ""
    sello_tiempo_str = (
        row["sello_tiempo"].astimezone(UTC).isoformat()
        if row["sello_tiempo"]
        else ""
    )

    if row["key_version"] != KEY_VERSION_HKDF:
        # Esquema HMAC retirado en B9 (la fila nativa es anterior a la 020). Se
        # dice EXACTAMENTE eso y no se recomputa: `hmac_no_coincide` acusaría una
        # manipulación que no ocurrió.
        return VerifyResponse(
            firma_id=row["id"],
            valida=False,
            provider=row["provider"],
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=_acreditado(row, valida=False),
            motivo="esquema_hmac_retirado",
            # No se evaluó nada: `None`, no `False` (que afirmaría manipulación).
            firma_criptograficamente_valida=None,
        )

    valida = signer.verificar(
        payload_xml.encode(),
        firmante_id,
        row["firma_blob"],
        sello_tiempo_str,
        settings.signature_secret,
        # Esquema con que se firmó ESTA fila (migración 020): solo 1 (HKDF por
        # entidad) llega aquí; lo demás se corta arriba. La entidad
        # es la de la petición —el schema donde vive la fila—, así una fila
        # copiada a otro tenant deja de verificar.
        key_version=row["key_version"],
        tenant_slug=tenant_slug,
        objeto_tipo=row["objeto_tipo"],
        objeto_id=row.get("objeto_id"),
    )
    return VerifyResponse(
        firma_id=row["id"],
        valida=valida,
        provider=row["provider"],
        nivel_conformidad=row.get("nivel_conformidad"),
        acreditado=_acreditado(row, valida=valida),
        motivo=None if valida else "hmac_no_coincide",
        # 'nativa' no tiene dimensión de revocación (Incremento 2 es
        # exclusivo de xades_personal) — firma_criptograficamente_valida
        # coincide SIEMPRE con valida, revocacion_status no aplica (None).
        firma_criptograficamente_valida=valida,
    )

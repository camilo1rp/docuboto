# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas del signature-service (E06, ADR-016, RF-FIR-11)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.constants import XADES_ELIGIBLE_OBJETO_TIPOS


class SignXmlRequest(BaseModel):
    """
    Solicitud de firma de un payload XML (o hash de documento).

    El firmante NO se acepta en el cuerpo de la petición — se deriva
    exclusivamente del JWT validado (RF-FIR-11, D-01).

    ``indice_id``/``version`` (Inc.1, E06↔E15) identifican la versión concreta
    del índice electrónico que se firma cuando ``objeto_tipo == "indice"`` —
    viajan en el evento ``firma.indice.firmado`` y en el filename subido a
    storage-service. Opcionales: cualquier otro ``objeto_tipo`` (radicado,
    anexo, ...) los deja en ``None`` sin efecto.
    """

    payload_xml: str = Field(..., min_length=1)
    objeto_tipo: str = Field(..., min_length=1, max_length=40)
    objeto_id: str | None = None
    formato: str | None = Field(default="XAdES", max_length=20)
    indice_id: str | None = None
    version: int | None = None


class InternalSignIndiceRequest(BaseModel):
    """Payload de ``POST /internal/sign-indice`` (reconciliación del sellado
    del índice, residual Ac. AGN 001/2024 art. 4.3.2.4; generalizada en H-H
    E06/F4 para el acta de transferencia).

    Gateada SOLO por ``require_internal_token`` (X-Internal-Token, D-02) —
    NUNCA ``PERM_FIRMA``/``X-User-Id``. A diferencia de ``SignXmlRequest``,
    ``objeto_tipo`` está ACOTADO al allow-set institucional
    (``XADES_ELIGIBLE_OBJETO_TIPOS`` — índice/acta de transferencia, validado
    abajo) en vez de aceptar cualquier valor: el llamador NUNCA puede elegir
    un tipo arbitrario, así que estructuralmente esta ruta jamás puede firmar
    la cadena personal ni ningún objeto fuera de ese conjunto. Default
    ``"indice"`` — retrocompat total con el llamador preexistente
    (``archive-service/app/jobs/indice_reconciliation.py``), que nunca envía
    este campo.

    ``indice_id`` es OPCIONAL (antes obligatorio): el acta de transferencia
    no tiene un índice electrónico propio que versionar — solo el índice
    (``objeto_tipo="indice"``) lo puebla.
    """

    payload_xml: str = Field(..., min_length=1)
    objeto_tipo: str = Field(default="indice", max_length=40)
    objeto_id: str
    indice_id: str | None = None
    version: int

    @field_validator("objeto_tipo")
    @classmethod
    def _objeto_tipo_en_allow_set(cls, v: str) -> str:
        if v not in XADES_ELIGIBLE_OBJETO_TIPOS:
            raise ValueError(
                f"objeto_tipo debe ser uno de {sorted(XADES_ELIGIBLE_OBJETO_TIPOS)}"
            )
        return v


class SignPersonalRequest(BaseModel):
    """Solicitud de ``POST /sign-personal`` (E17/F4, Incremento 1 — firma
    PERSONAL PKI por-usuario de un ``payload_xml`` ARBITRARIO, p. ej. el acta
    de entrega de transferencia congelada por archive-service).

    A diferencia de ``SignXmlRequest`` (sello institucional/nativo), este
    payload SIEMPRE se firma con el certificado PERSONAL del actor
    autenticado (``X-User-Id``/JWT — ``Depends(get_actor)``, nunca
    ``require_internal_token``): requiere que el actor tenga una credencial
    de firma personal 'activo' (``GET /internal/signing-key/status``,
    auth-service) y un ``totp_code`` válido, verificado-y-consumido
    ATÓMICAMENTE junto con la firma (D1 del contrato de firma personal, ver
    ``app.services.personal_signing``).

    ``firmante_id`` es OPCIONAL y puramente defensivo: cuando el llamante lo
    declara (p. ej. archive-service, que resuelve su propio ``actor.id`` por
    separado de la cabecera ``Authorization`` que reenvía), el servicio
    exige que coincida con el actor derivado del JWT (403
    ``firmante_no_coincide`` en caso contrario) — nunca sustituye la
    identidad, solo la corrobora (defensa en profundidad ante un bug de
    reenvío de cabeceras aguas arriba).
    """

    payload_xml: str = Field(..., min_length=1)
    objeto_tipo: str = Field(..., min_length=1, max_length=40)
    objeto_id: str = Field(..., min_length=1)
    totp_code: str = Field(..., min_length=1)
    firmante_id: uuid.UUID | None = None
    indice_id: str | None = None
    version: int | None = None


class FirmaResponse(BaseModel):
    firma_id: uuid.UUID
    objeto_tipo: str
    objeto_id: str | None
    formato_solicitado: str | None
    firmante_id: uuid.UUID | None
    # SHA-256 de los BYTES FIRMADOS, que NO siempre son el documento, y que
    # dependen TAMBIÉN del proveedor. Con `provider` nativa: para
    # `borrador`/`documento` se firma el texto hexadecimal de la huella, así que
    # este valor es el hash del hash; para `radicado`/`expediente`, que se firman
    # como conjunto, es el digest de `"{objeto_tipo}:{objeto_id}"`. Con
    # `provider` xades_personal esos mismos bytes van embebidos en el envoltorio
    # XML `<acto xmlns="urn:orpyca:firma-personal" …>` que XAdES necesita (ver
    # `cadena_service._build_personal_envelope`), y el valor es el digest del
    # ENVOLTORIO, no el de los bytes que contiene.
    #
    # Consecuencia práctica, comprobada en la cadena de un expediente: dos
    # turnos del MISMO acto sobre el MISMO objeto publican huellas distintas
    # —una por firmante nativo, otra por firmante con certificado— sin que nada
    # en el nombre lo advierta. Este campo solo es comparable entre firmas del
    # mismo `provider`, y **no sirve para comprobar contra el SHA-256 de un PDF
    # en la mano**. La huella del documento es `firma_solicitud.payload_sha256`.
    payload_sha256: str
    provider: str
    sello_tiempo: datetime
    created_at: datetime

    # --- Campos XAdES-B (Inc.1, E06/ADR-016) — None para firmas 'nativa' ---
    minio_key_firmado: str | None = None
    xades_level: str | None = None
    nivel_conformidad: str | None = None
    tipo_firma: str | None = None
    certificado_fingerprint: str | None = None
    # RF-FIR-15: nunca sobre-declarar conformidad. El material de firma del
    # Incremento 1 (nativa o XAdES-B sin CA acreditada ONAC) SIEMPRE resuelve
    # False — no hay ningún camino en este servicio que produzca True todavía.
    acreditado: bool = False

    # --- Sello de tiempo XAdES-T (Inc.4, RF-FIR-15) ---
    # `tsa_timestamp` es el genTime declarado por la TSA local (None cuando
    # xades_level != "XAdES-T"). `tsa_acreditado` viaja SIEMPRE junto a
    # `xades_level` — regla para consumidores (UI/reportes/eventos): "XAdES-T"
    # nunca se renderiza sin `tsa_acreditado`, y jamás se rotula como "sello
    # de tiempo acreditado"/"fecha cierta cualificada" mientras sea False (la
    # TSA local no está acreditada ante ONAC — camino a TSA acreditada real
    # vía `settings.tsa_url` queda como roadmap explícito, no implementado en
    # este incremento).
    tsa_timestamp: datetime | None = None
    tsa_acreditado: bool = False

    # --- XAdES-LT/LTA (RF-FIR-15) — None/False para firmas que no escalaron ---
    # `lt_material_present`/`archive_timestamp_present` reflejan lo persistido
    # al firmar (migración 013). `revocation_provenance` es SIEMPRE 'dev' en
    # este incremento cuando `lt_material_present=True` (CA local de
    # desarrollo, jamás una PKI acreditada) — nunca se omite, ver
    # `app.providers.local` sección "XAdES-LT/LTA". `crl_next_update` se
    # EXPONE para diagnóstico (una CRL de desarrollo caducada no invalida por
    # sí sola una firma ya producida).
    lt_material_present: bool = False
    archive_timestamp_present: bool = False
    revocation_provenance: str | None = None
    crl_next_update: datetime | None = None

    # --- OCSP stapled en XAdES-LT (RF-FIR-15) — False para firmas que no
    # incluyen xades:OCSPValues (kill-switch signer_ocsp_enabled=False, o
    # sin material de responder OCSP montado — ver
    # firma.ocsp_downgrade_no_responder en audit_log). Por simetría con
    # lt_material_present/archive_timestamp_present, migración 014.
    ocsp_present: bool = False


class VerifyRequest(BaseModel):
    """``payload_xml`` es el XML de entrada a verificar.

    Para firmas ``provider='xades_local'`` debe ser el XML **FIRMADO**
    completo (con el ``<ds:Signature>`` enveloped incluido — el que se
    descargó de ``minio_key_firmado``). Para firmas ``provider='nativa'``
    sigue siendo el payload original sin firmar, como antes del Incremento 1
    (el HMAC no se incrusta en el documento).
    """

    firma_id: uuid.UUID
    payload_xml: str = Field(..., min_length=1)


class VerifyResponse(BaseModel):
    firma_id: uuid.UUID
    valida: bool
    provider: str

    # --- Campos XAdES-B (Inc.1) — None/False para firmas 'nativa' ---
    xades_level: str | None = None
    nivel_conformidad: str | None = None
    acreditado: bool = False
    motivo: str | None = None

    # --- Sello de tiempo XAdES-T (Inc.4, RF-FIR-15) ---
    # None: la firma no lleva xades:SignatureTimeStamp (XAdES-B "puro" o
    # 'nativa') — no aplica, NO es un fallo. True/False: el sello SÍ se
    # evaluó (ver app.core.tsa.verify_timestamp_token). `tsa_timestamp` es el
    # genTime declarado por la TSA, solo presente cuando el sello es válido.
    sello_tiempo_valido: bool | None = None
    tsa_timestamp: datetime | None = None

    # --- XAdES-LT/LTA — reflejan el nivel REALMENTE verificado para ESTE
    # documento (app.providers.local.VerifyOutcome), no el `xades_level`
    # intencionado al firmar. Ver FirmaResponse para la semántica de cada campo.
    lt_material_present: bool = False
    archive_timestamp_present: bool = False
    revocation_provenance: str | None = None
    crl_next_update: datetime | None = None

    # --- OCSP stapled en XAdES-LT (RF-FIR-15) — reflejan el nivel REALMENTE
    # verificado para ESTE documento (app.providers.local.VerifyOutcome):
    # False/None cuando la firma no lleva xades:OCSPValues (retrocompat), no
    # cuando la verificación falló por otra causa.
    ocsp_present: bool = False
    ocsp_cert_status: str | None = None

    # --- Revocación de la firma personal (E17/F4, Incremento 2, A2) ---
    # `firma_criptograficamente_valida` es el veredicto de
    # `app.providers.local.verify_xades` (criptografía + cadena/anclaje),
    # SIN la dimensión de revocación — para NUNCA "difamar la cripto": una
    # revocación no dice que la firma fue falsificada, dice que la clave del
    # firmante ya no está vigente. `valida` (arriba) SÍ incorpora la
    # revocación cuando aplica: para `provider='xades_personal'`,
    # `valida = firma_criptograficamente_valida AND revocacion_status == 'vigente'`.
    # Para `provider != 'xades_personal'` (sello institucional/HMAC nativo),
    # la revocación no aplica — `revocacion_status` queda `None` y
    # `firma_criptograficamente_valida == valida` siempre.
    firma_criptograficamente_valida: bool = False
    # `vigente`/`revocado`/`no_verificable` (canal de revocación caído) /
    # `desconocido` (huella no encontrada — otro tenant, forjada, o el
    # usuario titular fue borrado por CASCADE; fail-closed, NUNCA `vigente`
    # en ese caso) — `None` cuando no aplica (firma no personal).
    revocacion_status: str | None = None

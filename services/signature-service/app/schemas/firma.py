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


class FirmaResponse(BaseModel):
    firma_id: uuid.UUID
    objeto_tipo: str
    objeto_id: str | None
    formato_solicitado: str | None
    firmante_id: uuid.UUID | None
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

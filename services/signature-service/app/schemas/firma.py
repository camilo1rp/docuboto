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

from pydantic import BaseModel, Field


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

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de la constancia de firma simple (E06, ADR-016, ADR-054).

Lo que registra este camino es una CONSTANCIA DE FIRMA SIMPLE (ADR-054 §1): que
este usuario, con este permiso, declaró esta huella en este instante. Íntegra y
atribuible dentro del sistema; no verificable por un tercero. La firma
electrónica verificable vive en signature-service.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: ADR-054 §1: el mismo vocabulario que signature-service (`FirmaResponse.kind`).
SIMPLE_SIGNATURE_RECORD = "simple_signature_record"


class SignatureCreate(BaseModel):
    #: IGNORADO. La huella la resuelve el servidor desde el SHA-256 que storage
    #: calculó al subir el anexo (AREA-05-03-S): una huella que elige el cliente
    #: no prueba nada. Se conserva en el esquema solo para no romper a quien aún
    #: la envía; su valor no se usa.
    content_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="Ignorado: la huella la resuelve el servidor desde el anexo.",
    )
    #: Obligatorio de hecho: sin anexo no hay contenido del que el servidor pueda
    #: resolver la huella (422 `anexo_requerido`).
    anexo_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class SignatureResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    anexo_id: uuid.UUID | None
    signer_id: uuid.UUID | None
    signer_name: str | None
    content_hash: str
    reason: str | None
    signed_at: datetime
    #: `servidor` (huella resuelta en storage) | `cliente_no_verificado` (heredada).
    origen: str = "servidor"
    #: Siempre `simple_signature_record`: este registro nunca es una firma electrónica.
    kind: Literal["simple_signature_record"] = SIMPLE_SIGNATURE_RECORD


class SignatureMatchResponse(BaseModel):
    """¿Esta huella está entre las constancias de firma simple del radicado?

    No responde «¿es válida esta firma?» (ADR-054 §1): esa pregunta es probatoria
    y la responde solo el camino XAdES. `matched` dice que alguien del sistema
    declaró esta huella, no que un tercero pueda comprobar quién.
    """

    content_hash: str
    matched: bool
    kind: Literal["simple_signature_record"] = SIMPLE_SIGNATURE_RECORD
    signatures: list[SignatureResponse]

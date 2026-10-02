# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de firma electrónica (E06, ADR-016)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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


class SignatureVerifyResponse(BaseModel):
    content_hash: str
    valid: bool
    signatures: list[SignatureResponse]

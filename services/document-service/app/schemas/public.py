# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de la VENTANILLA ÚNICA pública: radicación externa y consulta (E13/E20).

La primera versión de esta superficie asumía que lo único que entra sin
autenticar es una PQRS, y fijaba en un `Literal` de Python la taxonomía de la
Ley 1755 (petición/queja/reclamo/sugerencia/denuncia). Pero una **ventanilla
única de radicación** es el punto por el que entra TODA la correspondencia
externa de la entidad: tutelas, oficios de otras entidades, facturas,
invitaciones, hojas de vida. Decidir en código qué recibe cada entidad es
decidir por ella.

El vocabulario sale ahora del catálogo de **tipos documentales** que la entidad
ya mantiene y que gobierna la TRD (opt-in por
`tipos_documentales.admite_ventanilla_publica`), así que lo que entra queda
clasificado desde el primer momento y hereda su retención y su disposición
final (Ley 594/2000 arts. 21-24) en vez de entrar sin serie.

El remitente deja de ser necesariamente una persona: un oficio lo manda una
ENTIDAD con su NIT, y tratarla como «ciudadano» obligaba a escribir el nombre de
la empresa en el campo de una persona natural.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PublicVerifyResponse(BaseModel):
    """Trazabilidad mínima NO sensible expuesta sin autenticación."""

    tracking_number: str
    doc_type: str
    status: str
    registered_at: datetime
    dest_dept: str | None = None


class TipoVentanilla(BaseModel):
    """Un tipo de trámite que la entidad declara recibir por ventanilla pública."""

    code: str
    nombre: str


class VentanillaRadicacion(BaseModel):
    """Lo que alguien de fuera presenta en la ventanilla única.

    `tipo_documental` NO es un `Literal`: es el `code` de un tipo del catálogo
    de la entidad marcado como admisible. Un código no admisible se rechaza con
    422 y su motivo — nunca se acepta en silencio reclasificándolo, porque el
    tipo determina la serie TRD y con ella la retención.
    """

    tipo_documental: str = Field(..., min_length=1, max_length=50)

    # --- Remitente: persona natural o entidad -------------------------------
    # `entidad` es el discriminante real, no un adorno: cuando lo que llega es
    # un oficio de otra entidad, el nombre de la empresa NO va en el campo de
    # la persona natural. `sender_entity` existe en el modelo de radicado desde
    # siempre; la ventanilla pública era la única puerta que lo ignoraba.
    nombre: str = Field(..., min_length=1, max_length=255)
    entidad: str | None = Field(default=None, max_length=255)
    tipo_identificacion: str | None = Field(default=None, max_length=20)
    identificacion: str | None = Field(default=None, max_length=40)

    # --- Datos para notificar la respuesta ----------------------------------
    # Ley 1755/2015 art. 16: la petición debe indicar dónde recibir la
    # respuesta. No se exige aquí un canal concreto porque una petición
    # ANÓNIMA es legítima en varios supuestos; se pide al menos uno cuando hay
    # identificación (ver validador).
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=40)
    direccion: str | None = Field(default=None, max_length=500)

    asunto: str = Field(..., min_length=1, max_length=2000)
    descripcion: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _canal_de_respuesta_si_se_identifica(self) -> "VentanillaRadicacion":
        """Quien se identifica espera respuesta; quien no, puede seguir por código.

        Ley 1755/2015 art. 16 pide que la petición indique dónde recibir la
        respuesta. No se exige SIEMPRE porque una **denuncia anónima** es
        legítima y no espera contestación personal — exigir un canal la
        impediría. Pero aceptar a alguien identificado sin ninguna forma de
        contestarle es garantizar el incumplimiento del término: la entidad
        tendría la obligación y ningún medio.
        """
        if self.identificacion and not (self.email or self.telefono or self.direccion):
            raise ValueError(
                "Indique al menos un medio para recibir la respuesta "
                "(correo, teléfono o dirección). Si prefiere no identificarse, "
                "deje también el documento de identidad en blanco."
            )
        return self


class VentanillaRespuesta(BaseModel):
    """Lo que recibe quien radica, para poder hacer seguimiento."""

    radicado_id: uuid.UUID
    tracking_number: str
    verification_code: str
    dependencia_destino: str | None = None
    mensaje: str = (
        "Su documento fue radicado. Conserve el código de verificación: es lo "
        "único que permite consultar el estado sin identificarse."
    )


class PqrsCreate(BaseModel):
    """LEGADO. Se conserva porque es una superficie PÚBLICA ya publicada.

    Delega en la ventanilla única mapeando `tipo` al tipo documental que la
    entidad haya declarado. No se amplía: lo que crece es
    `VentanillaRadicacion`.
    """

    tipo: Literal["peticion", "queja", "reclamo", "sugerencia", "denuncia"] = "peticion"
    nombre: str = Field(..., min_length=1, max_length=255)
    identificacion: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    asunto: str = Field(..., min_length=1, max_length=2000)
    descripcion: str = Field(..., min_length=1)


class PqrsResponse(BaseModel):
    """Lo que recibe el ciudadano para hacer seguimiento (E13)."""

    radicado_id: uuid.UUID
    tracking_number: str
    verification_code: str
    mensaje: str = "Su petición fue radicada. Conserve el código de verificación."

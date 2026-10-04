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

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: Formato de la `Idempotency-Key` pública: la genera el cliente (un UUID basta).
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _rechazar_honeypot(valor: str | None) -> None:
    """Trampa para bots (AREA-02-07): un campo que ninguna persona ve ni rellena.

    Si llega con contenido, quien envía es un script que rellena todo lo que ve en
    el HTML. Se rechaza en la validación del esquema —ANTES de resolver el tipo, la
    dependencia o consumir un número— y con un mensaje genérico: decir qué campo es
    la trampa le enseñaría al bot a omitirlo.
    """
    if valor is not None and valor.strip():
        raise ValueError("La solicitud no pudo procesarse.")


class PublicVerifyResponse(BaseModel):
    """Trazabilidad mínima NO sensible expuesta sin autenticación."""

    tracking_number: str
    doc_type: str
    status: str
    registered_at: datetime
    dest_dept: str | None = None


class VentanillaEstado(BaseModel):
    """¿Está operativa la ventanilla de esta entidad? (AREA-09-07).

    Un 503 `ventanilla_no_habilitada` es el comportamiento correcto (fail-closed),
    pero se descubría al primer ciudadano. Esto permite comprobarlo ANTES: desde un
    script de despliegue o desde el propio formulario.
    """

    habilitada: bool
    #: Hay al menos una dependencia activa con `depe_rad_tp2` (recibe lo de fuera).
    dependencia_receptora: bool
    #: Cuántos tipos documentales admite la ventanilla pública.
    tipos_admitidos: int


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
    # Tope de longitud, como el resto de campos de la misma clase. Sin él, una
    # entrada ANÓNIMA escribía texto de cualquier tamaño en la tabla de
    # radicados: no hay usuario a quien atribuirlo ni cuota que lo acote, y el
    # asunto —que va al lado— sí lo tenía. 20.000 caracteres es más de lo que
    # ocupa un derecho de petición extenso y deja el abuso fuera.
    descripcion: str = Field(..., min_length=1, max_length=20000)

    # --- Autorización de tratamiento de datos personales (Ley 1581/2012) ------
    # `Literal[True]`: no hay valor «falso» válido —quien no autoriza no radica por
    # esta puerta—. La versión de la política la fija el cliente a partir de la que
    # la entidad publicó; el servidor la persiste con el instante y la IP.
    acepta_tratamiento: Literal[True]
    politica_version: str = Field(..., min_length=1, max_length=50)

    #: Honeypot (AREA-02-07). Invisible en el formulario; debe llegar vacío.
    sitio_web: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _sin_honeypot(self) -> "VentanillaRadicacion":
        _rechazar_honeypot(self.sitio_web)
        return self

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
    # Tope de longitud, como el resto de campos de la misma clase. Sin él, una
    # entrada ANÓNIMA escribía texto de cualquier tamaño en la tabla de
    # radicados: no hay usuario a quien atribuirlo ni cuota que lo acote, y el
    # asunto —que va al lado— sí lo tenía. 20.000 caracteres es más de lo que
    # ocupa un derecho de petición extenso y deja el abuso fuera.
    descripcion: str = Field(..., min_length=1, max_length=20000)
    #: Autorización de tratamiento (Ley 1581/2012) — ver `VentanillaRadicacion`.
    acepta_tratamiento: Literal[True]
    politica_version: str = Field(..., min_length=1, max_length=50)
    #: Honeypot (AREA-02-07).
    sitio_web: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _sin_honeypot(self) -> "PqrsCreate":
        _rechazar_honeypot(self.sitio_web)
        return self


class PqrsResponse(BaseModel):
    """Lo que recibe el ciudadano para hacer seguimiento (E13)."""

    radicado_id: uuid.UUID
    tracking_number: str
    verification_code: str
    mensaje: str = "Su petición fue radicada. Conserve el código de verificación."

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Esquemas de terceros y actores de la comunicación — E01, RF-RAD-05."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TipoTercero = Literal[
    "ciudadano", "empresa", "direccion_libre", "usuario_interno", "entidad_publica"
]


class TerceroCreate(BaseModel):
    """Alta de un tercero en el catálogo.

    `tipo_documento` y `numero_documento` van **juntos o ninguno**: un número
    sin decir de qué tipo no identifica a nadie, y un tipo sin número tampoco.
    Se permite no aportarlos —una `direccion_libre` es el caso honesto de «sé a
    dónde mandarlo y no sé quién es»— pero entonces el tercero **no participa de
    la deduplicación dura**, y eso hay que saberlo al crearlo.
    """

    tipo: TipoTercero
    nombre: str = Field(min_length=2, max_length=255)
    entidad: str | None = Field(default=None, max_length=255)
    tipo_documento: str | None = Field(default=None, max_length=20)
    numero_documento: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=50)
    direccion: str | None = Field(default=None, max_length=500)
    municipio: str | None = Field(default=None, max_length=120)
    departamento: str | None = Field(default=None, max_length=120)
    municipio_codigo: str | None = Field(
        default=None, min_length=5, max_length=5, pattern=r"^[0-9]{5}$"
    )
    departamento_codigo: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^[0-9]{2}$"
    )
    pais: str = Field(default="CO", max_length=2)

    @model_validator(mode="after")
    def _documento_completo(self) -> TerceroCreate:
        if (self.tipo_documento is None) != (self.numero_documento is None):
            raise ValueError(
                "El tipo y el número de documento van juntos o ninguno: uno sin el "
                "otro no identifica a nadie."
            )
        return self


class TerceroResponse(BaseModel):
    id: uuid.UUID
    tipo: str
    nombre: str
    entidad: str | None
    tipo_documento: str | None
    numero_documento: str | None
    email: str | None
    telefono: str | None
    direccion: str | None
    municipio: str | None
    departamento: str | None
    municipio_codigo: str | None
    departamento_codigo: str | None
    pais: str
    activo: bool
    creado_por: uuid.UUID | None
    creado_at: datetime
    # `False` cuando la petición de alta devolvió un tercero que ya existía con
    # ese documento. Se dice en vez de callarlo: quien registra tiene derecho a
    # saber que está reutilizando una ficha y no creando una nueva.
    creado: bool = True


class ActorVincular(BaseModel):
    """Un actor a atar al radicado, con su contacto **para este envío**.

    Los campos de contacto son opcionales y **sobrescriben la ficha solo aquí**:
    al radicar se busca a alguien y se le corrige el correo o la dirección,
    porque pasa. Ese ajuste se congela en el radicado y **no toca el catálogo**
    — vale para este envío y no dice nada sobre dónde vive esa persona.
    """

    tercero_id: uuid.UUID
    rol: Literal["remitente", "destinatario"]
    principal: bool = False
    # Por dónde se le atiende. `None` = todavía sin decidir, y se admite a
    # propósito: obligar a elegirlo al vincular empujaría a poner un canal
    # cualquiera para poder seguir, que es peor que decir que no se sabe.
    # Cada canal exige su dato en la copia congelada (CHECK de la migración 026).
    canal: Literal["email", "fisico", "ambos"] | None = None
    nombre: str | None = Field(default=None, min_length=2, max_length=255)
    entidad: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=50)
    direccion: str | None = Field(default=None, max_length=500)
    municipio: str | None = Field(default=None, max_length=120)
    departamento: str | None = Field(default=None, max_length=120)
    municipio_codigo: str | None = Field(
        default=None, min_length=5, max_length=5, pattern=r"^[0-9]{5}$"
    )
    departamento_codigo: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^[0-9]{2}$"
    )
    pais: str | None = Field(default=None, max_length=2)


class ActoresVincularRequest(BaseModel):
    actores: list[ActorVincular] = Field(min_length=1, max_length=200)


class ActorResponse(BaseModel):
    """Un actor tal y como quedó **congelado** en el radicado.

    Estos datos son los que se usaron en el envío, no los vigentes del
    catálogo: `tercero_id` viaja para poder ir a la ficha actual si alguien la
    quiere, pero lo que dice a dónde se envió es esta copia.
    """

    id: uuid.UUID
    tercero_id: uuid.UUID
    rol: str
    orden: int
    principal: bool
    canal: str | None
    nombre: str
    entidad: str | None
    email: str | None
    telefono: str | None
    direccion: str | None
    municipio: str | None
    departamento: str | None
    municipio_codigo: str | None
    departamento_codigo: str | None
    pais: str | None
    creado_at: datetime


class DepartamentoResponse(BaseModel):
    """Un departamento del catálogo DIVIPOLA (DANE).

    El catálogo es **cerrado** a propósito: mientras el municipio fuera texto
    libre, «Bogotá», «BOGOTA» y «Bogota D.C.» eran tres municipios distintos
    para cualquier consulta y cualquier reporte — la misma plaga de duplicados
    del Orfeo original, vista desde el lado del destino en vez del sujeto.
    """

    codigo: str
    nombre: str


class MunicipioResponse(BaseModel):
    codigo: str
    departamento_codigo: str
    nombre: str

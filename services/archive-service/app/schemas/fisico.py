# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas del archivo físico (E17, ADR-017)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

_NIVEL = Literal[
    "sede",
    "edificio",
    "piso",
    "deposito",
    "modulo",
    "estante",
    "entrepano",
    "gaveta",
    "custodia_externa",
]
_TIPO_UNIDAD = Literal["caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"]


class UbicacionCreate(BaseModel):
    nivel: _NIVEL
    codigo: str = Field(..., min_length=1, max_length=30)
    nombre: str = Field(..., min_length=1)
    parent_id: uuid.UUID | None = None
    tipo_archivo: Literal["gestion", "central", "historico"] | None = None
    capacidad_ml: float | None = Field(default=None, ge=0)


class UbicacionUpdate(BaseModel):
    """Corrección de una ubicación ya creada.

    Existe por un caso concreto: `tipo_archivo` es opcional al crear —el
    formulario ofrece «Sin tipo»— y sin embargo decide si la ubicación puede
    ser origen o destino de una transferencia (Ley 594/2000 art. 23, ciclo
    vital). Una topografía cargada sin ese dato dejaba el archivo físico
    **incapaz de transferir y sin forma de arreglarlo**: no había ninguna ruta
    de edición, así que el descuido era permanente y solo se descubría mucho
    después, al intentar la primera transferencia.

    Lo que se puede corregir es la DESCRIPCIÓN de la ubicación, nunca su
    identidad ni su sitio en el árbol: `codigo` es la referencia que la
    signatura topográfica de las unidades ya tiene impresa, y `parent_id`
    movería de golpe todo lo que cuelga de ella. Ninguno de los dos se toca
    aquí; para eso se crea otra ubicación y se trasladan las unidades, que es
    un acto físico y deja su propio rastro.

    Todos los campos son opcionales: se aplica solo lo que venga, y un cuerpo
    vacío se rechaza en el servicio en vez de producir un asiento de auditoría
    que no describe ningún cambio.
    """

    nombre: str | None = Field(default=None, min_length=1)
    tipo_archivo: Literal["gestion", "central", "historico"] | None = None
    capacidad_ml: float | None = Field(default=None, ge=0)
    activo: bool | None = None


class UbicacionResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    nivel: str
    codigo: str
    nombre: str
    tipo_archivo: str | None
    capacidad_ml: float | None
    activo: bool
    created_at: datetime
    ruta: str | None = None


class UnidadCreate(BaseModel):
    tipo: _TIPO_UNIDAD
    codigo: str = Field(..., min_length=1, max_length=30)
    ubicacion_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    soporte: str | None = Field(default=None, max_length=30)
    num_folios: int | None = Field(default=None, ge=0)


class UnidadResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    ubicacion_id: uuid.UUID | None
    tipo: str
    codigo: str
    signatura: str | None
    soporte: str | None
    num_folios: int | None
    activo: bool
    created_at: datetime


class ExpedienteUnidadLink(BaseModel):
    expediente_id: uuid.UUID
    folio_inicio: int | None = Field(default=None, ge=1)
    folio_fin: int | None = Field(default=None, ge=1)


class UnidadDeExpediente(BaseModel):
    """Dónde está el expediente: unidad + signatura + rango.

    `tipo` y `codigo` NO son adorno: la signatura topográfica dice en qué
    entrepaño mirar, pero lo que el operario tiene que sacar del estante es
    «la caja CAJ-001». Sin ellos la pantalla de localización mostraba dos
    columnas de guiones sobre un dato que la consulta ya traía por el JOIN.
    """

    unidad_id: uuid.UUID
    tipo: str
    codigo: str
    signatura: str | None
    ubicacion_id: uuid.UUID | None
    folio_inicio: int | None
    folio_fin: int | None


class ExpedienteEnUnidad(BaseModel):
    """Qué contiene la unidad: expedientes + rango."""

    expediente_id: uuid.UUID
    folio_inicio: int | None
    folio_fin: int | None


class PrestamoCreate(BaseModel):
    prestado_a: str = Field(..., min_length=1, max_length=255)
    prestado_por: str | None = Field(default=None, max_length=255)
    fecha_devolucion_esperada: date


class PrestamoResponse(BaseModel):
    id: uuid.UUID
    unidad_id: uuid.UUID
    # Un listado de préstamos sin identificar la unidad no dice lo único que
    # hace falta saber: QUÉ falta del estante. El UUID no vale — nadie lo
    # busca en un depósito.
    unidad_codigo: str | None = None
    unidad_signatura: str | None = None
    prestado_a: str
    prestado_por: str | None
    fecha_prestamo: datetime
    fecha_devolucion_esperada: date
    fecha_devolucion_real: datetime | None
    estado: str
    vencido: bool


class MovimientoResponse(BaseModel):
    id: uuid.UUID
    unidad_id: uuid.UUID
    accion: str
    ubicacion_before: uuid.UUID | None
    ubicacion_after: uuid.UUID | None
    performed_by: str | None
    ts: datetime


class FuidRow(BaseModel):
    """Fila del Formato Único de Inventario Documental (AGN 042/2002)."""

    orden: int
    expediente_id: uuid.UUID
    expediente_code: str
    asunto: str
    serie_code: str | None
    serie_name: str | None
    signatura: str | None
    unidad_tipo: str
    # El tipo dice «caja»; el código dice CUÁL caja. El inventario se coteja
    # contra el depósito con el código en la mano.
    unidad_codigo: str
    soporte: str | None
    num_folios: int | None
    folio_inicio: int | None
    folio_fin: int | None
    fecha_inicial: datetime
    fecha_final: datetime | None
    # Columna 9 del formato (Acuerdo AGN 042/2002). En el inventario de papel es
    # una estimación que rellena quien elabora; aquí se DERIVA de las consultas
    # registradas en `audit_log` durante los últimos 12 meses. No es un adorno:
    # la frecuencia de consulta es uno de los criterios de valoración para la
    # transferencia y la disposición final, y una estimación de memoria es
    # justamente lo que un SGDEA puede dejar de necesitar.
    frecuencia_consulta: Literal["alta", "media", "baja"] = "baja"


class FuidCabecera(BaseModel):
    """Cabecera del FUID (Acuerdo AGN 042/2002, Anexo; Ac. 001/2024).

    Faltaba entera: el inventario salía como una lista de filas sin decir de qué
    entidad es, de qué oficina, ni para qué se levantó. Un inventario documental
    sin esos datos no es un FUID — es una consulta.

    `entidad_productora` se resuelve SERVER-SIDE desde `public.tenants`: es la
    identidad institucional que encabeza un documento archivístico oficial y no
    puede depender de lo que escriba el cliente. Los demás son declarativos por
    naturaleza (los rellena quien elabora el inventario, igual que en papel) y
    viajan como parámetros; se marcan como tales para que nadie los lea como
    datos verificados por el sistema.
    """

    entidad_productora: str
    unidad_administrativa: str | None = None
    oficina_productora: str | None = None
    objeto: str | None = None


class FuidResponse(BaseModel):
    cabecera: FuidCabecera
    generado_ts: datetime
    total: int
    rows: list[FuidRow]

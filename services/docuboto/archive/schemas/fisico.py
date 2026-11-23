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
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


_DOC_TIPO = Literal["CC", "CE", "NIT", "PA", "TI", "PEP", "PPT"]


class PrestatarioInterno(BaseModel):
    """Un usuario del sistema. SIEMPRE `auth_users.id`, nunca el `sub` de Keycloak:
    el INSERT lo valida contra `auth_users` (activo) y un `sub` no existe ahí."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["interno"]
    user_id: uuid.UUID


class PrestatarioExterno(BaseModel):
    """Un tercero sin cuenta: instantánea con el identificador MÍNIMO (Ley 1581/2012,
    finalidad de custodia). No se enlaza al directorio de terceros de document-service."""

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["externo"]
    nombre: str = Field(..., min_length=3, max_length=255)
    documento_tipo: _DOC_TIPO
    documento_numero: str = Field(..., min_length=3, max_length=30)
    entidad: str | None = Field(default=None, max_length=255)
    cargo: str | None = Field(default=None, max_length=255)
    correo: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=40)

    @field_validator("nombre", "documento_numero")
    @classmethod
    def _sin_blancos(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("debe tener al menos 3 caracteres no blancos")
        return v


class PrestamoCreate(BaseModel):
    """`prestado_por` NO viene del cuerpo: lo resuelve el servidor (`X-User-Id`)."""

    model_config = ConfigDict(extra="forbid")

    prestatario: PrestatarioInterno | PrestatarioExterno = Field(..., discriminator="tipo")
    fecha_devolucion_esperada: date
    observaciones: str | None = Field(default=None, max_length=1000)

    @field_validator("fecha_devolucion_esperada")
    @classmethod
    def _no_en_el_pasado(cls, v: date) -> date:
        if v < datetime.now(tz=UTC).date():
            raise ValueError("fecha_devolucion_esperada no puede estar en el pasado")
        return v


class PrestamoResponse(BaseModel):
    id: uuid.UUID
    unidad_id: uuid.UUID
    # Un listado de préstamos sin identificar la unidad no dice lo único que
    # hace falta saber: QUÉ falta del estante. El UUID no vale — nadie lo
    # busca en un depósito.
    unidad_codigo: str | None = None
    unidad_signatura: str | None = None
    prestatario_tipo: Literal["interno", "externo"]
    prestatario_user_id: uuid.UUID | None = None
    # Interno: el `username` resuelto por JOIN al leer (la traza es el UUID, el
    # nombre puede cambiar). Externo: el de la fila.
    prestatario_nombre: str | None = None
    tercero_documento_tipo: str | None = None
    tercero_documento_numero: str | None = None
    tercero_entidad: str | None = None
    tercero_cargo: str | None = None
    tercero_correo: str | None = None
    tercero_telefono: str | None = None
    prestado_por: uuid.UUID
    devuelto_recibido_por: uuid.UUID | None = None
    observaciones: str | None = None
    fecha_prestamo: datetime
    fecha_devolucion_esperada: date
    fecha_devolucion_real: datetime | None
    estado: str
    vencido: bool


def _cola(valor: str, visibles: int = 4) -> str:
    return "*" * max(len(valor) - visibles, 3) + valor[-visibles:] if len(valor) > visibles else "***"


def enmascarar_prestatario(p: PrestamoResponse) -> PrestamoResponse:
    """Copia con documento, correo y teléfono del prestatario EXTERNO enmascarados.

    Datos personales (Ley 1581/2012): un listado de custodia (crud 1) dice QUIÉN tiene la
    unidad —nombre, entidad, cargo— pero no cómo contactarlo ni su identificación; eso es del
    registro completo (crud >= 3). Sin contenido que enmascarar devuelve la misma fila."""
    if p.prestatario_tipo != "externo":
        return p
    correo = p.tercero_correo
    if correo:
        local, _, dominio = correo.partition("@")
        correo = f"{local[:1]}***@{dominio}" if dominio else "***"
    return p.model_copy(
        update={
            "tercero_documento_numero": _cola(p.tercero_documento_numero)
            if p.tercero_documento_numero
            else None,
            "tercero_correo": correo,
            "tercero_telefono": _cola(p.tercero_telefono) if p.tercero_telefono else None,
        }
    )


class PrestatarioElegible(BaseModel):
    """Quién puede recibir la unidad: usuario activo cuyo clearance cubre el árbol.
    Nunca expone el nivel."""

    user_id: uuid.UUID
    username: str
    nombre: str | None = None


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
    # Nulos cuando el expediente no tiene unidad de conservación: un
    # expediente electrónico puro no está en ninguna caja, y decir que sí lo
    # está —o dejarlo fuera del inventario— son las dos formas de mentir. El
    # registro aparece con su identificación y sus columnas físicas en blanco,
    # que es exactamente lo que hay.
    unidad_tipo: str | None
    # El tipo dice «caja»; el código dice CUÁL caja. El inventario se coteja
    # contra el depósito con el código en la mano.
    unidad_codigo: str | None
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

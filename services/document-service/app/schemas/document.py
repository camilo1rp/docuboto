# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnexoInput(BaseModel):
    file_id: uuid.UUID
    filename: str = Field(..., min_length=1, max_length=255)
    file_size: int | None = None
    mime_type: str | None = None
    tipo_anexo: str | None = Field(default=None, max_length=50)
    es_principal: bool = False
    folios: int | None = Field(default=None, ge=0)


class DocumentCreate(BaseModel):
    doc_type: Literal["E", "S", "I"]
    subject: str = Field(..., min_length=1, max_length=2000)
    doc_class: str | None = Field(default=None, max_length=100)
    origin_dept: str | None = Field(default=None, max_length=100)
    dest_dept: str | None = Field(default=None, max_length=100)
    # Código (depe_codi) de la dependencia en el organigrama. Si se indica,
    # document-service lo valida contra tenant-service y denormaliza el nombre
    # de la dependencia en origin_dept/dest_dept (RF-RAD: vínculo con dependencias).
    origin_dept_code: int | None = Field(default=None, ge=1, le=999999)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    sender_name: str | None = Field(default=None, max_length=255)
    sender_entity: str | None = Field(default=None, max_length=255)
    pages: int = Field(default=0, ge=0)
    observations: str | None = None
    # Nivel de seguridad / clasificación (RF-SEG-08): 1=PUBLICA, 2=RESERVADA,
    # 3=CLASIFICADA (= security_levels.code). Por defecto PUBLICA. La búsqueda (E09)
    # oculta a cada usuario los radicados por encima de su clearance (RF-BUS-04).
    nivel_seguridad: int = Field(default=1, ge=1, le=3)
    # Fundamentación jurídica de la clasificación/reserva al radicar. OBLIGATORIA
    # cuando nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28: todo acto de reserva
    # debe ser motivado) — sin ella, el radicado entraría al índice de información
    # clasificada y reservada (art. 20) con fundamento vacío. Se persiste como
    # metadata de clasificación vigente (E08) y se motiva en el audit_log.
    clasificacion_fundamento: str | None = Field(default=None, max_length=300)
    clasificacion_causal: str | None = Field(default=None, max_length=200)
    clasificacion_plazo_meses: int | None = Field(default=None, ge=0, le=600)
    # Plazo de respuesta en DÍAS HÁBILES (RF-RAD-04). Si se indica, document-service
    # calcula la fecha de vencimiento vía tenant-service; si se omite, no hay plazo.
    response_days: int | None = Field(default=None, ge=1, le=3650)
    # Metadatos variables del tipo documental (E03, ADR-007). Si el doc_class tiene
    # una plantilla activa, se validan contra su JSON Schema antes de radicar.
    metadata: dict[str, Any] = Field(default_factory=dict)
    anexos: list[AnexoInput] = []

    @model_validator(mode="after")
    def _require_fundamento_when_classified(self) -> "DocumentCreate":
        if self.nivel_seguridad >= 2 and not (
            self.clasificacion_fundamento and self.clasificacion_fundamento.strip()
        ):
            raise ValueError(
                "clasificacion_fundamento es obligatorio al radicar con "
                "nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28: la reserva debe "
                "ser motivada; alimenta el índice del art. 20)"
            )
        return self


class DocumentUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=2000)
    dest_dept: str | None = Field(default=None, max_length=100)
    observations: str | None = None
    status: Literal["registered", "distributed", "in_progress", "closed"] | None = None
    pages: int | None = Field(default=None, ge=0)
    # **Tipificar** un radicado ya creado (E03/E04). Hasta ahora `doc_class` solo
    # se podía fijar AL RADICAR: un documento mal tipificado en ventanilla —o
    # radicado sin tipificar, que el schema permite— se quedaba así para siempre,
    # y con él la serie TRD que gobierna su retención y su disposición final
    # (Ley 594/2000 art. 24). No era una restricción deliberada: no había ningún
    # endpoint que lo hiciera, ni por esta vía ni por otra.
    #
    # Cambiar el tipo **revalida los metadatos** contra la plantilla activa del
    # tipo nuevo (ver `DocumentService.update`): un radicado no puede quedar
    # tipificado como X arrastrando los campos variables de Y.
    doc_class: str | None = Field(default=None, max_length=100)
    # Metadatos variables del tipo documental (E03, ADR-007). Se REEMPLAZAN
    # enteros, no se fusionan: una fusión dejaría campos del tipo anterior
    # sobreviviendo a la retipificación sin que nadie los hubiera pedido, y ese
    # residuo es exactamente lo que hace que un expediente deje de cuadrar.
    metadata: dict[str, Any] | None = None


class AnexoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    file_size: int | None
    mime_type: str | None
    created_at: datetime
    tipo_anexo: str | None = None
    es_principal: bool = False
    folios: int | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tracking_number: str
    doc_type: str
    year: int
    sequence: int
    subject: str
    doc_class: str | None
    origin_dept: str | None
    dest_dept: str | None
    origin_dept_code: int | None = None
    dest_dept_code: int | None = None
    sender_name: str | None
    sender_entity: str | None
    status: str
    pages: int
    nivel_seguridad: int
    observations: str | None
    registered_by: uuid.UUID | None
    registered_at: datetime
    updated_at: datetime
    due_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Estado del acto de publicación (Ley 1712/2014 art. 12). Aditivo: el acto
    # se podía ejecutar (`POST /{id}/publicacion`) pero su resultado no era
    # legible por ninguna vía, así que la entidad no podía acreditar qué había
    # publicado ni la interfaz ofrecer el acto sobre un estado conocido.
    publicable: bool = False
    publicado_at: datetime | None = None
    anexos: list[AnexoResponse] = []


class DispositionUpdate(BaseModel):
    programa: str | None = Field(default=None, max_length=200)
    retention_until: date | None = None
    accion: Literal["conservar", "eliminar", "transferir", "seleccionar"] | None = None
    confirmado: bool | None = None
    marcado_eliminacion: bool | None = None
    # Motivación del acto. La disposición decide entre conservar y DESTRUIR un
    # documento oficial (Ley 594/2000 art. 24), y hasta ahora se podía fijar
    # «eliminar» sin decir por qué y sin dejar rastro. Mismo criterio que la
    # reclasificación, que ya exige fundamento: un acto irreversible sobre el
    # patrimonio documental se motiva o no se hace.
    fundamento: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _exigir_fundamento_al_eliminar(self) -> "DispositionUpdate":
        """El fundamento es obligatorio SOLO en el camino destructivo.

        Marcar «conservar» o fijar el programa no destruye nada y exigirlo ahí
        sería el defecto simétrico: fricción que no protege y que acaba
        rellenándose con una palabra cualquiera, devaluando el campo donde sí
        importa.

        NOTA de alcance: esto NO deriva la disposición de la TRD. La tabla de
        retención vive en archive-service y el radicado no guarda hoy su serie,
        así que derivarla exige un cambio de modelo y un contrato entre
        servicios. Mientras tanto, el fundamento y el asiento en `audit_log` son
        el control compensatorio: la decisión sigue siendo humana, pero deja de
        ser anónima y silenciosa.
        """
        destructivo = self.accion == "eliminar" or self.marcado_eliminacion is True
        if destructivo and not (self.fundamento and self.fundamento.strip()):
            raise ValueError(
                "fundamento es obligatorio al disponer la eliminación de un radicado "
                "(Ley 594/2000 art. 24): la eliminación es irreversible y debe quedar "
                "motivada en el registro de auditoría"
            )
        return self


class PublicacionRequest(BaseModel):
    """Acto de publicación de oficio (11.1, Ley 1712/2014 art. 12).

    `publicar=False` retira la publicación. El fundamento es obligatorio en
    AMBOS sentidos: publicar difunde información de la entidad a cualquiera en
    internet y retirarla la deja de difundir, y las dos decisiones tienen que
    poder explicarse ante quien las revise. Un campo obligatorio solo en un
    sentido invita a usar el otro para deshacer sin dar cuentas.
    """

    publicar: bool
    fundamento: str = Field(..., min_length=1, max_length=500)


class PublicacionResponse(BaseModel):
    tracking_number: str
    publicable: bool
    publicado_at: datetime | None = None


class DispositionResponse(BaseModel):
    radicado_id: uuid.UUID
    disposition: dict[str, Any]


class ReclassifyRequest(BaseModel):
    nivel_seguridad: int = Field(..., ge=1, le=3)
    # Ley 1712/2014 art.19 y 28: todo acto de clasificación/reserva debe ser motivado.
    motivo: str = Field(..., min_length=1, max_length=500)
    # Campos opcionales de fundamentación jurídica (Ley 1712/2014 art.18-19).
    # Se registran en audit_log (hash-chain) como soporte inmutable del acto —
    # no crean columnas nuevas; el JSONB de audit_log es el registro legal.
    causal_reserva: str | None = Field(default=None, max_length=200)
    fundamento_juridico: str | None = Field(default=None, max_length=300)
    plazo_reserva_meses: int | None = Field(default=None, ge=0, le=600)

    @model_validator(mode="after")
    def _require_fundamento_when_classifying(self) -> "ReclassifyRequest":
        # Al reclasificar A nivel >= 2 el fundamento es OBLIGATORIO (Ley 1712 art.
        # 19/28): así el índice del art. 20 nunca publica una reserva sin su
        # fundamento. Desclasificar (nivel 1) no lo exige.
        if self.nivel_seguridad >= 2 and not (
            self.fundamento_juridico and self.fundamento_juridico.strip()
        ):
            raise ValueError(
                "fundamento_juridico es obligatorio al reclasificar a "
                "nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28)"
            )
        return self


class ReclassifyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tracking_number: str
    nivel_seguridad: int


class IndiceClasificadoItem(BaseModel):
    """Una fila del índice de información clasificada y reservada (Ley 1712 art. 20).

    CONTENT-FREE: identifica el radicado por su número y tipo documental y describe
    el acto de clasificación (nivel, excepción legal, fundamento, causal, plazo,
    fecha) — NUNCA el `subject`/contenido del documento reservado.
    """

    numero_radicado: str
    # Dirección del radicado (E=Entrada / S=Salida / I=Interno). NO es la serie
    # documental de la TRD (esa vive a nivel de expediente en archive-service; el
    # índice a nivel de serie es un follow-up declarado). Nombre honesto.
    tipo_radicado: str
    nivel_seguridad: int
    clasificacion: str  # "reservada" (nivel 2) | "clasificada" (nivel 3)
    excepcion_ley_1712: str  # art. 19 (reservada) | art. 18 (clasificada)
    fundamento_juridico: str | None
    causal_reserva: str | None
    plazo_reserva_meses: int | None
    fecha_clasificacion: datetime | None
    fecha_radicacion: datetime


class IndiceClasificadosResponse(BaseModel):
    total: int
    items: list[IndiceClasificadoItem]


class HealthResponse(BaseModel):
    status: str
    database: str

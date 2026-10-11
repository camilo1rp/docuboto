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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.datos_minimos import faltantes, mensaje_faltantes
from app.schemas.tercero import ActorVincular


class AnexoInput(BaseModel):
    file_id: uuid.UUID
    filename: str = Field(..., min_length=1, max_length=255)
    file_size: int | None = None
    mime_type: str | None = None
    tipo_anexo: str | None = Field(default=None, max_length=50)
    es_principal: bool = False
    folios: int | None = Field(default=None, ge=0)


#: Claves de `metadata` que son PROCEDENCIA del sistema, no dato del tipo
#: documental: `from_borrador` (radicado nacido de un borrador), `canal` (vía de
#: entrada; alimenta el gate de publicación de la Ley 1581) y `consentimiento`
#: (constancia de autorización de tratamiento, con valor probatorio). Las pone el
#: servidor por `procedencia`; un cliente (API, MCP, borrador) NO puede aportarlas
#: ni, por PATCH, cambiarlas o borrarlas. Lista ÚNICA para todas las puertas.
CLAVES_RESERVADAS: frozenset[str] = frozenset({"from_borrador", "canal", "consentimiento"})


def rechazar_claves_reservadas(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    reservadas = sorted(CLAVES_RESERVADAS & set(metadata or {}))
    if reservadas:
        raise ValueError(
            f"metadata no admite las claves {reservadas}: las asigna el sistema"
        )
    return metadata


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
    # Remitente y destinatarios, POR REFERENCIA al catálogo de terceros
    # (RF-RAD-05). Van en la creación y no en una llamada posterior porque un
    # radicado sin sus actores es un medio-estado: se vinculan dentro de la
    # MISMA transacción que crea el radicado y su asiento (ver
    # `DocumentService.create`), igual que el proyecto trata mutación y
    # auditoría como un solo acto.
    #
    # `sender_name`/`sender_entity` se siguen llenando —los leen la búsqueda,
    # los reportes, la exportación y el índice electrónico— con el actor
    # principal; estos actores NO los sustituyen, los explican: dicen a QUIÉN
    # del catálogo corresponde ese nombre y con qué contacto se le atendió.
    #
    # Vacío es válido y no es un descuido: un Interno no tiene actores externos,
    # y una Entrada radicada de urgencia puede completarse después con
    # `POST /terceros/radicado/{id}/actores`.
    actores: list[ActorVincular] = Field(default_factory=list, max_length=200)

    @field_validator("metadata")
    @classmethod
    def _metadata_sin_claves_reservadas(cls, v: dict[str, Any]) -> dict[str, Any]:
        return rechazar_claves_reservadas(v) or {}

    @model_validator(mode="after")
    def _require_datos_minimos(self) -> "DocumentCreate":
        """Ac. AGN 060/2001 §5.2 (AREA-09-02): tipo documental y dependencia."""
        falta = faltantes(
            self.doc_type,
            doc_class=self.doc_class,
            origin_dept_code=self.origin_dept_code,
            dest_dept_code=self.dest_dept_code,
        )
        if falta:
            raise ValueError(mensaje_faltantes(self.doc_type, falta))
        return self

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
    #: ALIAS deprecado de `dest_dept_code` (ADR-032): se resuelve a código y el
    #: nombre guardado es el del catálogo. Si llegan los dos deben concordar.
    dest_dept: str | None = Field(default=None, max_length=100)
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    observations: str | None = None
    # ADR-028: el estado del radicado lo mueve el FLUJO (workflow-service), no el
    # PATCH. El campo se conserva SOLO para poder rechazarlo con un error
    # explícito (`status_managed_by_workflow`, ver el router): quitarlo del
    # schema no basta, Pydantic ignoraría el extra y el cliente creería que
    # cambió el estado. Nunca llega al repositorio ni al asiento.
    status: Any = Field(default=None, exclude=True)
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

    @field_validator("metadata")
    @classmethod
    def _metadata_sin_claves_reservadas(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return rechazar_claves_reservadas(v)


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
    # Ubicación actual (ADR-029): dónde está el radicado AHORA. `dest_dept` es la
    # dependencia de RADICACIÓN (instantánea legal) y no cambia al repartir.
    # `ubicacion_*` = `flujo_*` si el flujo lo movió, si no la de radicación.
    # `flujo_usuario` (poseedor del paso) solo viaja en el detalle autenticado.
    flujo_dept_code: int | None = None
    flujo_dept: str | None = None
    flujo_usuario: uuid.UUID | None = None
    ubicacion_dept_code: int | None = None
    ubicacion_dept: str | None = None
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
    #: `anexos.id` de los soportes que SE DIFUNDEN junto al radicado (AREA-07-12).
    #: Por omisión ninguno: publicar el asunto no publica sus soportes. Se ignora
    #: al retirar (se retiran todos).
    anexos_publicables: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class PublicacionResponse(BaseModel):
    tracking_number: str
    publicable: bool
    publicado_at: datetime | None = None
    anexos_publicados: int = 0


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
    #: Área responsable (Dec. 1081/2015 art. 2.1.1.5.): la que RECIBE (E/I) o la que
    #: PRODUCE (S) el documento. Es el nombre de la dependencia, no su contenido.
    dependencia: str | None = None
    #: Quién radicó (identificador opaco de usuario, no su nombre). `None` para lo que
    #: entró sin usuario (ventanilla pública).
    registrado_por: uuid.UUID | None = None


class IndiceClasificadosResponse(BaseModel):
    total: int
    items: list[IndiceClasificadoItem]


class HealthResponse(BaseModel):
    status: str
    database: str


class HistorialAccesoEntry(BaseModel):
    """Un acceso a un radicado: quién, cuándo, y de qué tipo (RF-BUS-10).

    Contrato de SOLO LECTURA sobre `public.audit_log` (ADR-010). No expone
    `id`/`prev_hash`/`hash`: quien mira este historial quiere el HECHO auditado,
    no la mecánica de verificación de la cadena — para eso está
    `/admin/auditoria`.

    Tampoco expone `payload`. El de estos asientos lleva el `nivel_seguridad`
    del radicado y el `file_id` del anexo descargado, y ninguna de las dos cosas
    hace falta para responder «¿quién ha visto esto?». No se filtra al
    serializar: **no se lee de la base**, que es la única forma de que no se
    escape el día que alguien añada un campo al payload sin mirar quién lo pinta.
    """

    ts: datetime
    service: str
    actor: str | None = None
    #: El `username` de quien accedió, resuelto desde el identificador del
    #: asiento. Puede venir vacío —un asiento del propio sistema, o un usuario
    #: dado de baja del catálogo— y entonces la pantalla cae al identificador:
    #: decir menos es aceptable, inventar un nombre no.
    actor_nombre: str | None = None
    canal: str
    #: `document.radicado_consultado` (leyó el metadato) o
    #: `document.anexo_descargado` (bajó un fichero). Son hechos distintos y la
    #: pantalla los distingue: ver la ficha no es llevarse el documento.
    action: str
    object_ref: str

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpedienteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    trd_serie_id: uuid.UUID | None = None
    # Nivel de seguridad / clasificación (RF-SEG-08): 1=PUBLICA, 2=RESERVADA,
    # 3=CLASIFICADA (= security_levels.code). Por defecto PUBLICA. La búsqueda de
    # expedientes oculta a cada usuario los de nivel superior a su clearance.
    nivel_seguridad: int = Field(default=1, ge=1, le=3)
    # Metadatos variables del expediente (E03, ADR-007). Si la serie TRD tiene
    # plantilla activa, se validan contra su JSON Schema al crear.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExpedienteUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trd_serie_id: uuid.UUID | None = None
    status: Literal["open", "closed", "transferred"] | None = None


class RadicadoLink(BaseModel):
    radicado_id: uuid.UUID
    tracking_number: str = Field(..., min_length=1, max_length=50)
    # SHA-256 del documento (E15 T-08): se materializa como valor_huella del índice.
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    # Metadatos RT-15 del índice electrónico (E15 Inc.3, Acuerdo AGN 001/2024
    # art. 4.3.2.3): aportados por quien vincula (Opción A), todos opcionales —
    # NULL/ausente = no declarado, se omite del <documento> del índice v2.
    formato: str | None = Field(default=None, max_length=150)
    tamano_bytes: int | None = Field(default=None, ge=0)
    folios: int | None = Field(default=None, ge=0)
    # E15 perfil v4 (R1, Acuerdo AGN 001/2024 art. 4.3.2.3): dependencia
    # productora del documento, aportada al vincular (Opción A) — grano
    # por-radicado, sin llamada a document-service. Ambos campos son
    # OPCIONALES pero deben aportarse JUNTOS o NINGUNO (ver validador abajo +
    # CHECK de pareja en BD, migración 019) — el índice solo emite
    # <dependenciaProductora> cuando los dos existen.
    dependencia_productora_codigo: int | None = Field(default=None, ge=0)
    dependencia_productora_nombre: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _productora_codigo_y_nombre_van_juntos(self) -> "RadicadoLink":
        has_codigo = self.dependencia_productora_codigo is not None
        has_nombre = self.dependencia_productora_nombre is not None
        if has_codigo != has_nombre:
            raise ValueError(
                "dependencia_productora_codigo y dependencia_productora_nombre "
                "deben aportarse juntos o ninguno (E15 perfil v4, R1)"
            )
        return self


class BatchLinkRequest(BaseModel):
    radicados: list[RadicadoLink] = Field(..., min_length=1, max_length=500)


class BatchLinkResult(BaseModel):
    vinculados: int
    omitidos: int  # ya estaban vinculados
    total: int


class ExpedienteRadicadoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    radicado_id: uuid.UUID
    tracking_number: str
    added_at: datetime


class ExpedienteEventoResponse(BaseModel):
    """Evento de la hoja de ruta append-only del expediente (E02 §9)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    expediente_id: uuid.UUID
    numero_expediente: str | None = None
    accion: str
    actor: uuid.UUID | None = None
    comentario: str | None = None
    detalles: dict[str, Any] = Field(default_factory=dict)
    ts: datetime


class ExpedienteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    trd_serie_id: uuid.UUID | None
    status: str
    opened_by: uuid.UUID | None
    opened_at: datetime
    closed_at: datetime | None
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    disposition: dict[str, Any] | None = None
    radicados: list[ExpedienteRadicadoResponse] = []


class ExpedienteDisposicionResult(BaseModel):
    """Respuesta de `POST /{id}/close` y `POST /{id}/transfer`.

    Deliberadamente NO es `ExpedienteResponse`: la disposición (cierre/
    transferencia, crud>=3) es ortogonal al clearance de lectura del llamante
    (RF-SEG-08 Opción B, validada por archival-compliance-auditor — Ley
    594/2000 arts. 22-24/27, Ley 1712/2014 arts. 18-19). Un archivista de
    custodia con permiso de disposición pero SIN clearance para leer el
    contenido de un expediente RESERVADA/CLASIFICADA puede cerrarlo o
    transferirlo — pero la respuesta de esa operación no debe filtrarle ese
    contenido. Por eso este modelo trae solo metadatos de ciclo de vida y
    omite deliberadamente `metadata`, `description`, `radicados` y
    `opened_by`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    status: str
    disposition: dict[str, Any] | None = None
    closed_at: datetime | None = None
    transferred_at: datetime | None = None


# Disposición: códigos AGN (CT/E/S/M) + valores antiguos por compatibilidad.
_DISPOSITION = Literal["CT", "E", "S", "M", "eliminate", "conserve", "digitize", "select"]


class TrdSerieCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1)
    description: str | None = None
    retention_years: int = 5
    total_retention: int = 10
    disposition: _DISPOSITION = "E"
    # E04/ADR-015: subserie (parent_id) + retención en dos fases.
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = Field(default=None, ge=0)
    archivo_central_years: int | None = Field(default=None, ge=0)


class TrdSerieUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    retention_years: int | None = None
    total_retention: int | None = None
    disposition: str | None = None
    is_active: bool | None = None


class TrdSerieResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    retention_years: int
    total_retention: int
    disposition: str
    is_active: bool
    created_at: datetime
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = None
    archivo_central_years: int | None = None
    version: int = 1
    valid_from: date | None = None


class IndiceItem(BaseModel):
    folio: int
    radicado_id: uuid.UUID
    tracking_number: str
    added_at: datetime


class ExpedienteIndice(BaseModel):
    expediente_id: uuid.UUID
    code: str
    total_folios: int
    items: list[IndiceItem]


class IndiceItemResponse(BaseModel):
    document_id: uuid.UUID
    orden: int
    titulo: str | None = None
    tipologia: str | None = None
    # Nombrado para alinear con <fechaIncorporacion> del XML v2 (E15 Inc.3,
    # RT-15 art. 4.3.2.3) — mismo valor que `fecha_archivado`/`added_at`
    # internos, renombrado solo en la vista consultable.
    fecha_incorporacion: datetime | None = None
    valor_huella: str | None = None
    funcion_resumen: str = "SHA-256"
    # Metadatos RT-15 (E15 Inc.3); NULL = no declarado al vincular.
    formato: str | None = None
    tamano_bytes: int | None = None
    folios: int | None = None
    # v3 (E15 finalización RT-15): visibilidad del hueco trazable + ACL en la
    # vista consultable JSON — antes solo el XML probatorio los mostraba, y
    # `num_documentos` (solo vigentes) quedaba sin explicación frente a
    # `len(items)` (vigentes + excluidos). `estado`/`nivel_seguridad` vienen
    # del snapshot ya proyectado por `IndexRepository.get_items`
    # (`estado`/`nivel_seguridad_snapshot`); NULL en versiones anteriores a
    # v3, que no capturaban estos campos por item.
    estado: str | None = None
    nivel_seguridad: int | None = None
    # E15 perfil v4 (R1): snapshot de la dependencia productora en esta
    # versión del índice; NULL si no se declaró al vincular o en versiones
    # anteriores a v4 (columna no proyectada, ver `.get` tolerante).
    dependencia_productora_codigo: int | None = None
    dependencia_productora_nombre: str | None = None


class IndiceResponse(BaseModel):
    id: uuid.UUID
    expediente_id: uuid.UUID
    version: int
    estado: str
    num_documentos: int
    xml_sha256: str
    algoritmo: str
    firma_id: uuid.UUID | None = None
    generado_ts: datetime
    items: list[IndiceItemResponse] = []


class IndiceVersionSummary(BaseModel):
    version: int
    estado: str
    num_documentos: int
    xml_sha256: str
    generado_ts: datetime
    generado_por: str | None = None


class RebuildResult(BaseModel):
    version: int
    xml_sha256: str
    num_documentos: int
    unchanged: bool = False
    # xsdVersion de la versión devuelta (la nueva si se regeneró, o la ya
    # persistida si `unchanged`) — fuente para que el llamador (p. ej. el
    # asiento de auditoría del cierre) no asuma ciegamente la constante del
    # módulo cuando `regenerate` no creó versión nueva.
    xsd_version: str | None = None


class IndiceVerifyDoc(BaseModel):
    document_id: uuid.UUID
    huella_indice: str | None
    huella_actual: str | None
    coincide: bool


class IndiceVerifyFirma(BaseModel):
    presente: bool
    valida: bool
    # E06 Inc.1 (RF-FIR-15): SIEMPRE False en este incremento — no hay CA
    # acreditada ONAC; ningún response debe sugerir firma cualificada.
    nivel_conformidad: str | None = None
    acreditado: bool = False
    # Explica por qué `valida=False` (p. ej. "signed_xml_unavailable",
    # "certificado_fuera_de_vigencia", "fingerprint_no_coincide_sello_tenant").
    motivo: str | None = None


class IndiceVerifyResponse(BaseModel):
    version: int
    valido: bool
    # Fixity del XML del índice en sí (re-hash de la columna `xml` almacenada
    # vs `xml_sha256` persistido) — distinto de `documentos[].coincide`, que
    # solo contrasta la huella de cada documento referenciado.
    huella_indice_ok: bool = True
    documentos: list[IndiceVerifyDoc]
    firma: IndiceVerifyFirma
    fecha_verificacion: datetime


class IndiceFirmarResult(BaseModel):
    """Resultado del sellado (o reintento) XAdES-B del índice (E06 Inc.1, RT-16)."""

    firma_id: uuid.UUID
    estado: str = "firmado"
    minio_key_firmado: str | None = None
    nivel_conformidad: str | None = None
    xades_level: str | None = None
    acreditado: bool = False


class RetentionSchedule(BaseModel):
    serie_id: uuid.UUID
    code: str
    closed_at: date
    archivo_gestion_years: int
    archivo_central_years: int
    fin_archivo_gestion: date
    fin_archivo_central: date
    disposition_code: str
    disposition_label: str


class TipoDocumentalCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    nombre: str = Field(..., min_length=1)
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool = False
    metadata_template: str | None = Field(default=None, max_length=100)
    activo: bool = True


class TipoDocumentalUpdate(BaseModel):
    nombre: str | None = None
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool | None = None
    metadata_template: str | None = None
    activo: bool | None = None


class TipoDocumentalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trd_serie_id: uuid.UUID | None
    code: str
    nombre: str
    obligatorio: bool
    metadata_template: str | None
    activo: bool
    created_at: datetime


class ExpedienteSearchHit(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    opened_at: datetime
    rank: float


class ExpedienteSearchResponse(BaseModel):
    query: str | None = None
    total: int
    hits: list[ExpedienteSearchHit]
    page: int
    size: int


class HealthResponse(BaseModel):
    status: str
    database: str


class HistorialAccesoEntry(BaseModel):
    """Entrada de `public.audit_log` filtrada por expediente (E15 perfil v4, R3-b).

    Contrato ESTABLE de solo-lectura (ADR-010): mismas columnas que
    `auth-service`'s `AuditEntry` (mismo storage), sin exponer `id`/`prev_hash`/
    `hash` internos de la cadena — el consumidor de este endpoint quiere el
    HECHO auditado, no la mecánica de verificación de la cadena.
    """

    tenant_slug: str
    ts: datetime
    service: str
    actor: str | None = None
    canal: str
    action: str
    object_type: str
    object_ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RolAutorizado(BaseModel):
    """Snapshot VIVO (no firmado) de qué grupos pueden leer el expediente ahora
    (E15 perfil v4, R4-b) — deliberadamente FUERA del índice firmado: la matriz
    RBAC cambia con el tiempo y no debe congelarse en un artefacto probatorio."""

    model_config = ConfigDict(populate_by_name=True)

    grupo: str
    clearance_max: int = Field(alias="clearanceMax")
    crud: int

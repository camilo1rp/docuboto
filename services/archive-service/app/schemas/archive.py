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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def validar_forma_fondo_acumulado(
    *,
    origen: str,
    trd_serie_id: uuid.UUID | None,
    fecha_extrema_inicial: date | None,
    fecha_extrema_final: date | None,
) -> None:
    """Regla de forma de ADR-026 D4, compartida por la creación unitaria
    (`ExpedienteCreate`) y la carga masiva (`BatchExpedienteCreate`).

    Vive aquí como función y no duplicada en cada schema porque gobierna la
    **fecha base** de un calendario de retención que termina en un
    `retain_until` COMPLIANCE **irreversible**: dos copias de esta regla se
    separan en cuanto una de las dos rutas cambie, y la que se quede atrás
    dejaría entrar un fondo acumulado sin `fecha_extrema_final` — el
    escenario exacto contra el que D4 previene. Es espejo del par de CHECK de
    la migración 041 (`expedientes_fondo_forma_check` /
    `expedientes_corriente_forma_check`); se ejecuta antes de tocar la base
    para dar un 422 legible en vez de una violación de restricción opaca.

    Lanza ``ValueError`` (Pydantic lo convierte en `422`); no devuelve nada.
    """
    if origen == "fondo_acumulado":
        if fecha_extrema_final is None:
            raise ValueError(
                "fecha_extrema_final es obligatoria cuando origen='fondo_acumulado' "
                "(ADR-026 D4: es la fecha base del calendario de retención)."
            )
        if fecha_extrema_inicial is not None and fecha_extrema_inicial > fecha_extrema_final:
            raise ValueError("fecha_extrema_inicial no puede ser posterior a fecha_extrema_final.")
        if trd_serie_id is None:
            raise ValueError(
                "trd_serie_id es obligatorio cuando origen='fondo_acumulado' "
                "(debe apuntar a una TVD, ADR-026 D3)."
            )
    elif fecha_extrema_inicial is not None or fecha_extrema_final is not None:
        # Negación declarada, espejo de `expedientes_corriente_forma_check`
        # (migración 041): un expediente corriente no carga fechas de un
        # fondo que no es.
        raise ValueError(
            "fecha_extrema_inicial/fecha_extrema_final solo aplican con origen='fondo_acumulado'."
        )


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
    # ADR-026 (Increment 2): amarre de fondo acumulado. `origen` decide bajo
    # qué instrumento puede cerrarse este expediente (Capa 4, migración 041)
    # -- fijado aquí, en la creación; no hay ruta de edición posterior
    # (`ExpedienteUpdate` no lo incluye a propósito, ver esa clase). Un fondo
    # acumulado exige `trd_serie_id` (de una TVD) y `fecha_extrema_final`
    # DESDE la creación: es la fecha base del calendario (ADR-026 D4) y el
    # trigger de la Capa 4 rechazaría igualmente un `trd_serie_id` de TRD.
    origen: Literal["corriente", "fondo_acumulado"] = "corriente"
    fecha_extrema_inicial: date | None = None
    fecha_extrema_final: date | None = None

    @model_validator(mode="after")
    def _validate_fondo_acumulado(self) -> ExpedienteCreate:
        validar_forma_fondo_acumulado(
            origen=self.origen,
            trd_serie_id=self.trd_serie_id,
            fecha_extrema_inicial=self.fecha_extrema_inicial,
            fecha_extrema_final=self.fecha_extrema_final,
        )
        return self


class ExpedienteUpdate(BaseModel):
    """Metadatos del expediente. NO transiciona el ciclo de vida (Ley 594/2000
    arts. 22/24; Acuerdo AGN 001/2024 art. 4.3.2.4): `status` fue retirado a
    propósito — era una ruta alterna a `POST /{id}/close` y `POST /{id}/transfer`
    que ejecutaba la MISMA transición sin auditoría, sin disposición TRD
    materializada, sin índice electrónico final y sin la guarda TOCTOU
    (`expected_status`) de `transition_expediente`. `extra="forbid"` es
    OBLIGATORIO, no cosmético: sin él, un `{"status": "closed"}` se ignoraría en
    silencio (default de Pydantic v2) y el llamante creería haber cerrado el
    expediente sin haberlo hecho (fail-open semántico, peor que el bug
    original). Con `forbid`, cualquier intento de enviar `status` (incluido
    `null`) responde 422 ANTES de tocar la base de datos — oracle-free: no
    revela si el expediente existe ni su clearance."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    trd_serie_id: uuid.UUID | None = None


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
    def _productora_codigo_y_nombre_van_juntos(self) -> RadicadoLink:
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
    # `None` (M2, RF-SEG-08): la mutación SIEMPRE ocurre, pero cuando el
    # llamante no puede LEER el expediente (nivel_seguridad > su clearance) se
    # acota la respuesta a `None` para no filtrar el volumen vinculado/omitido
    # — oráculo de pertenencia y tamaño del conjunto documental. `total` se
    # conserva SIEMPRE: es eco del propio input que el llamante ya envió, no
    # es sensible.
    vinculados: int | None
    omitidos: int | None  # ya estaban vinculados
    total: int


class ExpedienteRadicadoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    radicado_id: uuid.UUID
    tracking_number: str
    # `None` (M2, RF-SEG-08): cuando el llamante no puede LEER el expediente,
    # `link_radicado` responde idempotente-silencioso (no revela si ya estaba
    # vinculado, ni el instante real de vinculación previo) — ver
    # `ArchiveService.link_radicado`.
    added_at: datetime | None


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
    # ADR-025 (Increment 1, aditivo): denormalización de clasificación
    # (sobrevive a la versión concreta) + marca honesta de revisión pendiente
    # (D4/D6) -- ver `TrdSerieResponse.estado`/`disposition["serie_estado"]`
    # para el detalle de bajo qué instrumento se cerró.
    trd_serie_code: str | None = None
    trd_revision_requerida: bool = False
    trd_revision_motivo: str | None = None
    # ADR-026 (Increment 2, aditivo).
    origen: str = "corriente"
    fecha_extrema_inicial: date | None = None
    fecha_extrema_final: date | None = None
    # Estado del acto de publicación (Ley 1712/2014 art. 12). Aditivo: sin esto
    # el acto se podría ejecutar pero no consultar, y la entidad no podría
    # acreditar qué expedientes difunde de oficio. Lección ya pagada en el
    # gemelo de radicados.
    publicable: bool = False
    publicado_at: datetime | None = None


class PublicacionRequest(BaseModel):
    """Acto de publicación de oficio del expediente (Ley 1712/2014 art. 12).

    Espejo de `document-service`. `publicar=False` retira la publicación, y el
    fundamento es obligatorio en AMBOS sentidos: difundir un expediente y dejar
    de difundirlo son dos decisiones que la entidad tiene que poder explicar ante
    quien las revise. Un campo obligatorio solo en un sentido invita a usar el
    otro para deshacer sin dar cuentas.
    """

    publicar: bool
    fundamento: str = Field(..., min_length=1, max_length=500)


class PublicacionResponse(BaseModel):
    code: str
    publicable: bool
    publicado_at: datetime | None = None


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

# E10 debt "perfil PDF/A por serie": MISMA whitelist que `_VALID_FLAVOURS`
# (`storage-service/app/services/pdfa/subprocess_verapdf.py`) -- duplicada a
# propósito (servicios independientes, archive-service no importa de
# storage-service). Defensa Pydantic (422 legible) ANTES del CHECK de BD
# (migración 035), que es la última línea fail-closed.
_PDFA_PROFILES = frozenset({"1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"})


def _validate_pdfa_profile(value: str | None) -> str | None:
    if value is not None and value not in _PDFA_PROFILES:
        raise ValueError(f"pdfa_profile debe ser uno de {sorted(_PDFA_PROFILES)} o null")
    return value


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
    # E10 debt: perfil PDF/A de la serie (RF-FIR-15) -- NULL = sin política
    # propia, el AIP cae al default global de storage-service.
    pdfa_profile: str | None = None

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)


class TrdSerieUpdate(BaseModel):
    """`PATCH /api/v1/trd/{serie_id}` (ADR-025, Increment 1): solo aplicable EN
    SITIO cuando la versión está en `borrador` -- cualquier otro estado
    responde `409 trd_version_inmutable` (una modificación sustantiva exige
    `POST /trd/{code}/versiones`). `pdfa_profile` es la ÚNICA excepción: sigue
    aceptándose en cualquier estado (D1, política de preservación técnica, no
    contenido convalidado). `is_active` YA NO se acepta -- la baja de una
    versión es `derogar` (Increment 2), nunca un efecto colateral del PATCH."""

    name: str | None = None
    description: str | None = None
    retention_years: int | None = None
    total_retention: int | None = None
    disposition: str | None = None
    pdfa_profile: str | None = None

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)


class TrdSerieVersionCreate(BaseModel):
    """`POST /api/v1/trd/{code}/versiones` (ADR-025, Increment 1, nuevo):
    clona la versión VIGENTE (convalidada/migrada) del código como una fila
    NUEVA `version+1`, estado `borrador`. Solo los campos enviados se
    sobre-escriben; el resto se copia de la versión vigente."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    retention_years: int | None = None
    total_retention: int | None = None
    disposition: _DISPOSITION | None = None
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = Field(default=None, ge=0)
    archivo_central_years: int | None = Field(default=None, ge=0)
    pdfa_profile: str | None = None

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)


class TrdSerieAprobar(BaseModel):
    """`POST /api/v1/trd/{code}/versiones/{version}/aprobar` (ADR-025 D2,
    migración 038 -- hallazgo #5 del dictamen). Antes este endpoint no
    recibía body: el sistema registraba el SEGUNDO acto del circuito
    (convalidación) y nunca el PRIMERO (aprobación del Comité Institucional
    de Gestión y Desempeño, Ac. AGN 001/2024 que compila 004/2019) --
    `fecha_aprobacion_comite` existía en el modelo desde la 037 pero ningún
    camino de código la escribía. Ambos campos son obligatorios: una
    aprobación sin acta ni fecha es la misma afirmación no verificada que
    este proyecto rechaza en la convalidación (RF-FIR-15)."""

    acta_comite: str = Field(..., min_length=1)
    fecha_aprobacion_comite: date


class TrdSerieDevolver(BaseModel):
    """`POST /api/v1/trd/{code}/versiones/{version}/devolver` (ADR-025,
    migración 038 -- hallazgo #1 del dictamen). `aprobada -> borrador`: la
    instancia convalidante (o el propio Comité) devuelve el instrumento con
    observaciones para ajustes -- paso ORDINARIO del procedimiento (Ac. AGN
    001/2024), no una excepción. `motivo` es obligatorio: sin él, el asiento
    `archive.trd_version_devuelta` no explicaría por qué un instrumento
    aprobado volvió a edición."""

    motivo: str = Field(..., min_length=1)


class TrdSerieRegistrarRusd(BaseModel):
    """`POST /api/v1/trd/{code}/versiones/{version}/registrar-rusd` (ADR-025,
    migración 038 -- hallazgo #4 del dictamen). El Registro Único de Series
    Documentales (RUSD) y la publicación tienen, por norma, hasta 30 días
    hábiles DESPUÉS de la convalidación para completarse -- el único momento
    en que el sistema los aceptaba era `POST .../convalidar`, y luego el
    `PATCH` queda cerrado para cualquier estado que no sea `borrador`
    (append-only, D1). Al menos un campo debe venir; ambos quedan opcionales
    entre sí porque el RUSD y la publicación no siempre llegan juntos."""

    rusd_radicado: str | None = Field(default=None, min_length=1)
    fecha_publicacion: date | None = None

    @model_validator(mode="after")
    def _al_menos_un_campo(self) -> TrdSerieRegistrarRusd:
        if self.rusd_radicado is None and self.fecha_publicacion is None:
            raise ValueError("registrar-rusd exige al menos rusd_radicado o fecha_publicacion")
        return self


class TrdSerieConvalidar(BaseModel):
    """`POST /api/v1/trd/{code}/versiones/{version}/convalidar` (ADR-025 D2,
    Increment 2). Exige el acto: `acto_administrativo`, `fecha_convalidacion`
    e `instancia_convalidante` son obligatorios en el body -- el CHECK
    `trd_series_convalidada_acto_check` (migración 037) ya lo exige a nivel
    BD, pero el 422 de Pydantic es más legible que el 500/23514 crudo de
    Postgres. `rusd_radicado`/`fecha_publicacion` quedan opcionales: el
    registro RUSD (Ac. AGN 001/2024, que compila el 004/2019) puede llegar
    después de la convalidación del Consejo -- ver `TrdSerieRegistrarRusd`
    para el camino que lo captura cuando llega tarde (hallazgo #4)."""

    acto_administrativo: str = Field(..., min_length=1)
    fecha_convalidacion: date
    instancia_convalidante: str = Field(..., min_length=1)
    rusd_radicado: str | None = None
    fecha_publicacion: date | None = None


class TvdAgrupacionCreate(BaseModel):
    """`POST /api/v1/tvd` (ADR-026, Increment 1). Espejo de `TrdSerieCreate`
    salvo lo que ADR-026 D2 (Capa 3) exige/prohíbe para el instrumento de
    valoración: NO acepta `archivo_gestion_years` (la TVD no tiene fase de
    archivo de gestión, siempre NULL) ni `retention_years` (siempre 0 para
    el instrumento -- la retención de un fondo acumulado la fija
    `archivo_central_years` desde `fecha_extrema_final`, D4, Increment 2) ni
    `estado`/`tipo_instrumento` (nunca del cliente, fijado por el router).
    `fondo_nombre`/`fecha_extrema_inicial`/`fecha_extrema_final`/
    `justificacion_valoracion` son OBLIGATORIOS: son el objeto del
    instrumento (D5), no un dato secundario."""

    code: str = Field(..., min_length=1, max_length=20)
    name: str = Field(..., min_length=1)
    description: str | None = None
    disposition: _DISPOSITION = "E"
    parent_id: uuid.UUID | None = None
    total_retention: int = Field(default=10, ge=0)
    archivo_central_years: int | None = Field(default=None, ge=0)
    pdfa_profile: str | None = None
    fondo_nombre: str = Field(..., min_length=1)
    fecha_extrema_inicial: date
    fecha_extrema_final: date
    justificacion_valoracion: str = Field(..., min_length=1)

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)

    @model_validator(mode="after")
    def _fechas_extremas_coherentes(self) -> TvdAgrupacionCreate:
        if self.fecha_extrema_final < self.fecha_extrema_inicial:
            raise ValueError(
                "fecha_extrema_final debe ser >= fecha_extrema_inicial "
                "(trd_series_tvd_forma_check, ADR-026)"
            )
        return self


class TvdAgrupacionUpdate(BaseModel):
    """`PATCH /api/v1/tvd/{id}` (ADR-026): solo aplicable EN SITIO cuando la
    versión está en `borrador` (+ `pdfa_profile` en cualquier estado), mismo
    gate que `TrdSerieUpdate`. `retention_years`/`archivo_gestion_years`
    quedan fuera a propósito: son 0/NULL por construcción para TVD."""

    name: str | None = None
    description: str | None = None
    total_retention: int | None = Field(default=None, ge=0)
    disposition: str | None = None
    pdfa_profile: str | None = None
    fondo_nombre: str | None = Field(default=None, min_length=1)
    fecha_extrema_inicial: date | None = None
    fecha_extrema_final: date | None = None
    justificacion_valoracion: str | None = Field(default=None, min_length=1)

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)


class TvdAgrupacionVersionCreate(BaseModel):
    """`POST /api/v1/tvd/{code}/versiones` (ADR-026): clona la vigente como
    `borrador` `version+1`. Espejo de `TrdSerieVersionCreate` sin
    `archivo_gestion_years`/`retention_years`, con los campos propios de TVD
    opcionales (se copian de la vigente cuando se omiten)."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    disposition: _DISPOSITION | None = None
    parent_id: uuid.UUID | None = None
    total_retention: int | None = Field(default=None, ge=0)
    archivo_central_years: int | None = Field(default=None, ge=0)
    pdfa_profile: str | None = None
    fondo_nombre: str | None = Field(default=None, min_length=1)
    fecha_extrema_inicial: date | None = None
    fecha_extrema_final: date | None = None
    justificacion_valoracion: str | None = Field(default=None, min_length=1)

    @field_validator("pdfa_profile")
    @classmethod
    def _check_pdfa_profile(cls, value: str | None) -> str | None:
        return _validate_pdfa_profile(value)


class ExpedienteRevisionPendienteResponse(BaseModel):
    """Fila de `GET /api/v1/trd/revision-pendiente` (ADR-025 D4/D6,
    Increment 2): expedientes con `trd_revision_requerida=true`, cualquier
    motivo -- `cerrado_bajo_serie_migrada` (D6, se limpia al ratificar),
    `cierre_sin_snapshot_de_retencion` y `serie_no_resoluble` (D4, ninguno de
    los dos se resuelve ratificando: ver ADR-025 §"Plan de backfill")."""

    id: uuid.UUID
    code: str
    status: str
    trd_serie_id: uuid.UUID | None
    trd_serie_code: str | None
    trd_revision_motivo: str | None
    closed_at: datetime | None
    # ADR-026 (Increment 2, aditivo): instrumento que gobierna -- 'TRD' salvo
    # la fila fuera huérfana (`trd_serie_id` sin resolver, `None`). Ninguna
    # TVD genera hoy esta marca (no hay `migrada` en TVD, ADR-026
    # §"Máquina de estados"), pero el campo va igual para que el informe sea
    # legible cuando eso cambie.
    tipo_instrumento: str | None = None


class LoteEliminacionCreate(BaseModel):
    """`POST /api/v1/eliminacion/lotes` (RF-TRD-07). Propone un lote."""

    codigo: str = Field(min_length=3, max_length=60)
    expediente_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    observaciones: str | None = Field(default=None, max_length=4000)


class LoteEliminacionItem(BaseModel):
    """Una fila del inventario **congelado** — ver la cabecera de la migración
    047. No se lee del expediente en vivo: el expediente va a dejar de
    existir, y lo que se publicó debe seguir siendo legible después."""

    expediente_id: uuid.UUID
    expediente_code: str
    expediente_nombre: str | None
    trd_serie_code: str | None
    disposition_code: str
    fecha_vencimiento: date
    fecha_extrema_inicial: date | None
    fecha_extrema_final: date | None
    nivel_seguridad: int
    radicados_incluidos: int | None
    worm_retain_until: datetime | None


class LoteEliminacionRechazo(BaseModel):
    """Un expediente que NO entró, y por qué.

    Existe porque descartar en silencio sería lo peor que puede hacer aquí una
    interfaz: quien propone un lote de 40 y recibe uno de 31 tiene que poder
    ver **cuáles nueve** faltan y qué le pasa a cada uno — si no, la única
    salida es ir probando de uno en uno."""

    expediente_id: uuid.UUID
    expediente_code: str | None
    motivo: str
    detalle: str


class LoteEliminacionResponse(BaseModel):
    """El lote propuesto, con su inventario y **el informe de lo rechazado**."""

    id: uuid.UUID
    codigo: str
    estado: str
    observaciones: str | None
    creado_por: uuid.UUID
    creado_at: datetime
    publicado_at: datetime | None = None
    publicado_hasta: date | None = None
    acta_comite: str | None = None
    fecha_aval: date | None = None
    acto_administrativo: str | None = None
    ejecutado_at: datetime | None = None
    anulado_motivo: str | None = None
    total_items: int
    items: list[LoteEliminacionItem] = []
    rechazados: list[LoteEliminacionRechazo] = []
    # `ejecutado` es siempre False hoy: el borrado seguro (RF-TRD-07 §5.4) no
    # está construido. Va explícito por el mismo motivo que en la decisión --
    # un 201 sobre «lote de eliminación» invita a creer que algo se eliminó.
    ejecutado: bool = False
    nota: str = (
        "Lote propuesto. NADA se ha eliminado: falta publicar el inventario, "
        "obtener el aval del Comité, firmar el acta y ejecutar el borrado seguro, "
        "que todavía no está construido."
    )


class LotePublicar(BaseModel):
    """`POST /api/v1/eliminacion/lotes/{id}/publicar` (RF-TRD-07, fase 2).

    `publicado_hasta` es el plazo para oponerse, y **no tiene default**: no se
    inventa cuántos días da una entidad para que un tercero se oponga a que
    destruyan un documento. Quien publica lo declara."""

    publicado_hasta: date

    @model_validator(mode="after")
    def _plazo_util(self) -> LotePublicar:
        if self.publicado_hasta <= date.today():
            raise ValueError(
                "El plazo de publicidad debe terminar después de hoy: publicar con "
                "el plazo ya vencido no da a nadie ocasión de oponerse."
            )
        return self


class LoteAval(BaseModel):
    """`POST /api/v1/eliminacion/lotes/{id}/avalar` (RF-TRD-07, fase 3 de 5).

    El **aval del Comité Institucional de Gestión y Desempeño**: la decisión
    archivística que autoriza destruir (Ac. AGN 001/2024 art. 4.3.2.5). Las
    tres constancias son obligatorias por el mismo criterio que la
    convalidación de una versión de TRD --un aval sin acta ni fecha ni acto
    administrativo es una afirmación no verificada--, y el CHECK de la
    migración 047 las exige también en la base, no solo aquí.

    El sistema **no decide**: registra que un Comité decidió, y con qué acto.
    """

    acta_comite: str = Field(min_length=3, max_length=200)
    fecha_aval: date
    acto_administrativo: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _fecha_no_futura(self) -> LoteAval:
        if self.fecha_aval > date.today():
            raise ValueError(
                "La fecha del aval no puede ser futura: se registra un acta que ya "
                "existe, no una reunión que aún no ha ocurrido."
            )
        return self


class LoteAnulacion(BaseModel):
    """`POST /api/v1/eliminacion/lotes/{id}/anular` (RF-TRD-07, fase 3 de 5).

    Retirar un lote del procedimiento. El motivo es obligatorio y de longitud
    mínima porque es la constancia de por qué NO se eliminó -- y si el lote
    llegó a publicarse, esa constancia queda a la vista de quien leyó el
    inventario y pudo estar esperando la oposición que presentó."""

    motivo: str = Field(min_length=10, max_length=500)


class LoteEliminacionItemPublico(BaseModel):
    """Una fila del inventario **publicado**.

    `expediente_nombre` viaja vacío y `nombre_reservado` en `True` cuando el
    expediente está clasificado: la fila SÍ se publica —omitirla dejaría de
    cumplir la publicidad previa justo sobre lo que más importa que alguien
    pueda objetar— pero su DESCRIPCIÓN se retiene, que es donde vive el
    contenido. La pantalla lo dice; no deja un hueco mudo."""

    expediente_code: str
    expediente_nombre: str | None
    nombre_reservado: bool
    trd_serie_code: str | None
    disposition_code: str
    fecha_vencimiento: date
    fecha_extrema_inicial: date | None
    fecha_extrema_final: date | None
    radicados_incluidos: int | None


class LoteEliminacionPublico(BaseModel):
    """Un lote de eliminación tal y como lo ve cualquiera, sin autenticar."""

    id: uuid.UUID
    codigo: str
    estado: str
    publicado_at: datetime | None
    publicado_hasta: date | None
    fecha_aval: date | None
    acto_administrativo: str | None
    ejecutado_at: datetime | None
    observaciones: str | None = None
    # Un lote publicado y luego RETIRADO sigue siendo público, con su motivo:
    # quien leyó que esos expedientes iban a eliminarse tiene que poder ver en
    # qué quedó.
    anulado_motivo: str | None = None
    total_items: int
    items: list[LoteEliminacionItemPublico] = []


class DisposicionDecisionCreate(BaseModel):
    """Cuerpo de `POST /api/v1/trd/disposicion-vencida/{id}/decidir`.

    **Lo que NO se acepta del cliente** es tan importante como lo que sí: la
    fecha del vencimiento y el código de disposición NO viajan aquí, salen del
    snapshot congelado en la base. Si viajaran, confirmar una eliminación sería
    cuestión de mandar la fecha adecuada."""

    fase: Literal["gestion", "central"]
    decision: Literal["confirmada", "aplazada"]
    # Obligatorio al aplazar (validado abajo y por el CHECK de la migración
    # 046, que es quien manda): un aplazamiento sin motivo y sin fecha de
    # regreso no es una decisión, es quitar el expediente de la vista.
    motivo: str | None = Field(default=None, max_length=2000)
    revisar_de_nuevo_el: date | None = None

    @model_validator(mode="after")
    def _aplazar_exige_motivo_y_fecha(self) -> DisposicionDecisionCreate:
        if self.decision != "aplazada":
            return self
        if not self.motivo or len(self.motivo.strip()) < 10:
            raise ValueError(
                "Aplazar exige un motivo de al menos 10 caracteres: es lo que "
                "leerá quien retome el expediente."
            )
        if self.revisar_de_nuevo_el is None:
            raise ValueError(
                "Aplazar exige una fecha para volver a revisarlo: un "
                "aplazamiento sin fecha de regreso es un olvido con mejor nombre."
            )
        if self.revisar_de_nuevo_el <= date.today():
            raise ValueError("La fecha de revisión debe ser posterior a hoy.")
        return self


class ExpedienteDisposicionDecisionResponse(BaseModel):
    """Resultado de decidir sobre un vencimiento (RF-TRD-06/08).

    `ejecutada` es **siempre `False`** hoy, y va explícito a propósito: una
    respuesta `200` sobre un endpoint llamado «decidir» invita a creer que algo
    ocurrió. Lo que ocurrió es que la decisión quedó registrada en una tabla
    append-only y en la cadena de auditoría inmutable; la ejecución de la
    disposición final (RF-TRD-07) no está construida."""

    id: uuid.UUID
    expediente_id: uuid.UUID
    fase: str
    decision: str
    fecha_vencimiento: date
    disposition_code: str | None
    motivo: str | None
    revisar_de_nuevo_el: date | None
    decidido_por: uuid.UUID
    decidido_at: datetime
    ejecutada: bool
    nota: str


class ExpedienteDisposicionVencidaResponse(BaseModel):
    """Fila de `GET /api/v1/trd/disposicion-vencida` (RF-TRD-08, 2026-08-28):
    un expediente cuyo calendario de retención ya venció.

    Todo sale del **snapshot congelado al cerrar** (`expedientes.disposition`),
    no de la fila viva de la serie: el calendario que gobierna a un expediente
    es el que se congeló, y una convalidación posterior no adelanta ni retrasa
    un vencimiento ya fijado (ADR-025 D3/D4).

    `gobierna_eliminacion` responde **exactamente lo mismo** que el gate
    `assert_disposicion_final_autorizada` responderá cuando llegue el brazo
    ejecutor: ambos llaman al mismo predicado. No es un adorno informativo —
    si la pantalla dijera «se puede eliminar» sobre un expediente que el gate
    va a rechazar, el funcionario descubriría el límite fallando.

    `accion`: qué toca hacer, derivado del código de disposición del propio
    snapshot. **Un vencimiento no es una orden de destruir**: sobre `CT`
    (conservación total) el vencimiento del archivo central significa
    transferir al histórico y conservar para siempre. Distinguirlo aquí evita
    que la cola se lea como «lista de lo que hay que borrar»."""

    id: uuid.UUID
    code: str
    status: str
    nivel_seguridad: int
    trd_serie_code: str | None
    closed_at: datetime | None
    fase: Literal["gestion", "central"]
    fecha_vencimiento: date
    dias_vencido: int
    disposition_code: str | None
    disposition_label: str | None
    serie_estado: str | None
    acto_administrativo: str | None
    gobierna_eliminacion: bool
    accion: str
    # Estado de la decisión sobre ESTE vencimiento (RF-TRD-06). `None` = nadie
    # la ha mirado todavía; es el caso que la alerta persigue.
    decision: str | None = None
    decision_motivo: str | None = None
    revisar_de_nuevo_el: date | None = None
    decidido_por: uuid.UUID | None = None
    decidido_at: datetime | None = None


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
    pdfa_profile: str | None = None
    # ADR-025 (Increment 1): `version`/`valid_from` dejan de ser vestigiales
    # (RF-FIR-15) -- `estado` es la fuente de verdad del ciclo de vida del
    # instrumento; los campos del acto son NULL salvo en `convalidada`.
    estado: str = "borrador"
    valid_to: date | None = None
    acto_administrativo: str | None = None
    acta_comite: str | None = None
    fecha_aprobacion_comite: date | None = None
    fecha_convalidacion: date | None = None
    instancia_convalidante: str | None = None
    rusd_radicado: str | None = None
    fecha_publicacion: date | None = None
    supersede_a: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    # ADR-026 (Increment 1): discriminador TVD, aditivo -- una TRD responde
    # `tipo_instrumento="TRD"` y las cuatro columnas de contenido TVD en
    # `None` (negación declarada por CHECK, ver `trd_series_trd_forma_check`).
    tipo_instrumento: str = "TRD"
    fondo_nombre: str | None = None
    fecha_extrema_inicial: date | None = None
    fecha_extrema_final: date | None = None
    justificacion_valoracion: str | None = None


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
    # `version`/`xml_sha256`/`num_documentos` → `None` (M2, RF-SEG-08): la
    # mutación (`IndexService.regenerate`) SIEMPRE ocurre, pero el endpoint
    # `POST /{id}/indice/rebuild` acota la respuesta a un ack minimalista
    # cuando el llamante no puede LEER el expediente — de lo contrario
    # filtraría volumen documental (`num_documentos`) y huella
    # (`xml_sha256`/`version`) a quien no tiene clearance. La redacción vive en
    # el HANDLER del router (`routers/indice.py`), NUNCA dentro de
    # `IndexService.regenerate`: ese método lo reutiliza `close_expediente`
    # (canal 'system') para su propio asiento de auditoría, que necesita los
    # valores REALES sin importar clearance de lectura.
    version: int | None
    xml_sha256: str | None
    num_documentos: int | None
    # `unchanged` es un oráculo débil de "hubo cambios en el conjunto
    # documental" — también se redacta a un valor fijo (`False`) cuando el
    # llamante no puede leer, en vez de conservarlo.
    unchanged: bool = False
    # xsdVersion de la versión devuelta (la nueva si se regeneró, o la ya
    # persistida si `unchanged`) — fuente para que el llamador (p. ej. el
    # asiento de auditoría del cierre) no asuma ciegamente la constante del
    # módulo cuando `regenerate` no creó versión nueva. NO es una constante
    # global fija: varía por expediente según cuándo se regeneró por última
    # vez su índice (retrocompat v1/v2/v4, RT-15 Inc.3) — por eso, a
    # diferencia de lo que decía este comentario antes, SÍ se redacta a
    # `None` en la respuesta acotada de `POST /{id}/indice/rebuild` cuando el
    # llamante no puede leer el expediente (`routers/indice.py::
    # rebuild_indice`, hallazgo #1): dejarlo poblado solo en la rama "open"
    # y `None` solo en la rama "closed/transferred" era, por sí mismo, un
    # oráculo de ciclo de vida — aunque el resto de campos ya fueran
    # uniformes. `IndexService.regenerate`/`close_expediente` (canal
    # 'system') siguen viendo el valor real; esto solo afecta el ack HTTP
    # acotado.
    xsd_version: str | None = None


class IndiceVerifyDoc(BaseModel):
    document_id: uuid.UUID
    huella_indice: str | None
    huella_actual: str | None
    coincide: bool
    # Un radicado sin contenido binario (registro de metadatos, lo normal en
    # ventanilla) no tiene huella que resumir: ni en el índice ni ahora. Eso
    # NO es una discrepancia de integridad — no hay nada que haya cambiado—,
    # y contarlo como fallo bloqueaba la recepción de cualquier transferencia
    # cuyos radicados no llevaran adjuntos, con un mensaje que a un archivista
    # le suena a corrupción del acervo. Se separa y se DECLARA: la fixity se
    # predica de lo que tiene huella, y lo que falta se dice, no se esconde
    # (ISO 16363; Ley 594/2000 art. 16).
    sin_huella_declarada: bool = False


class IndiceFixityResult(BaseModel):
    """Fixity PURA-BD del índice electrónico (E12 H-G): compara
    `documentos[].coincide` (huella de cada documento vigente/excluido vs su
    `content_hash` actual) y `huella_indice_ok` (re-hash de la fila del
    índice) -- SIN verificación de firma (HTTP) y sin gate de lectura cuando
    `IndexService.fixity` se invoca con `CLEARANCE_SIN_RESTRICCION`.

    Reutilizado por `IndexService.verify` (con el clearance real del
    llamante) y por `TransferenciaService.recibir` (acto de custodia
    ortogonal al clearance de lectura, `core.clearance` bloque (c)) -- un
    único sitio para la comparación de huellas evita que ambos consumidores
    diverjan si cambia el algoritmo o la semántica de exclusión."""

    version: int
    documentos: list[IndiceVerifyDoc]
    # `documentos_ok` = ninguna huella DECLARADA discrepa. Los documentos sin
    # huella no lo tumban; se cuentan aparte, en `documentos_sin_huella`, para
    # que quien recibe sepa exactamente de qué se le está dando fe.
    documentos_ok: bool
    documentos_sin_huella: int = 0
    huella_indice_ok: bool


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


class IndicePendienteFirma(BaseModel):
    """Fila de `GET /expedientes/indices/pendientes-firma` (residual Ac. AGN
    001/2024 art. 4.3.2.4, reconciliación) — visibilidad operacional
    complementaria a `audit_log` para el archivista, no-read-up (RF-SEG-08)."""

    indice_id: uuid.UUID
    expediente_id: uuid.UUID
    expediente_code: str
    version: int
    reconcile_intentos: int
    reconcile_agotado: bool
    reconcile_ultimo_error: str | None = None


class IndicePendienteRenovacion(BaseModel):
    """Fila de `GET /expedientes/indices/pendientes-renovacion` (E10 debt --
    renovación de retención WORM para series CT, AGN 001/2024 Art. 4.3.2.6)
    -- visibilidad operacional gemela de `IndicePendienteFirma`, para que
    ningún artefacto CT (índice, acta de transferencia o AIP) quede
    silenciado en `audit_log` mientras su renovación está AGOTADA o EN
    RIESGO de vencer sin renovar.

    `tipo` distingue el artefacto (`indice` | `acta_transferencia` | `aip` --
    este último, E10 debt, fast-follow del cableado del AIP) -- UN endpoint
    combinado en vez de tres gemelos, mismo criterio de no proliferación que
    `POST /proteger-artefacto` (storage-service, E10 Inc.2 wiring) generalizó
    sobre `POST /proteger-indice`."""

    expediente_id: uuid.UUID
    expediente_code: str
    tipo: Literal["indice", "acta_transferencia", "aip"]
    worm_retain_until: datetime | None
    worm_renovacion_intentos: int
    worm_renovacion_agotado: bool
    worm_renovacion_ultimo_error: str | None = None


class RetentionSchedule(BaseModel):
    serie_id: uuid.UUID
    code: str
    # ADR-026 (Increment 2, D4): renombrado de `closed_at` -- este campo ya
    # no presupone que la base del calendario sea la fecha de cierre. Para
    # una TRD SÍ lo es (`base_date_origen="closed_at"`); para una TVD es
    # `fecha_extrema_final` del expediente y NUNCA `closed_at` -- ver ese
    # campo. Ningún consumidor leía la clave `closed_at` del snapshot
    # (verificado: solo `expedientes.closed_at`, columna distinta), así que
    # el renombre es seguro.
    base_date: date
    archivo_gestion_years: int
    archivo_central_years: int
    fin_archivo_gestion: date
    fin_archivo_central: date
    disposition_code: str
    disposition_label: str
    # ADR-025 (Increment 1): procedencia del instrumento que gobernó el
    # cálculo -- viaja dentro del snapshot (`expedientes.disposition`) como
    # PRUEBA oponible (no como integridad referencial, ver ADR-025 D3).
    serie_version: int = 1
    serie_estado: str = "borrador"
    acto_administrativo: str | None = None
    fecha_convalidacion: date | None = None
    instancia_convalidante: str | None = None
    rusd_radicado: str | None = None
    snapshot_at: datetime | None = None
    # ADR-026 (Increment 2, D4/D5): declaración explícita de la BASE del
    # calendario -- "la declaración viaja con la prueba, igual que
    # serie_estado". Se escribe SIEMPRE, para ambos instrumentos (nunca solo
    # cuando es la rara): un snapshot que solo declara la base cuando es
    # 'fecha_extrema_final' no permite distinguir "es la fecha de cierre" de
    # "alguien olvidó escribirlo".
    base_date_origen: Literal["closed_at", "fecha_extrema_final"] = "closed_at"
    # Fundamento de la eliminación de un fondo acumulado (D5): NO es la
    # retención cumplida sino la VALORACIÓN -- por eso viaja congelado, y es
    # lo que se citaría en el acta de eliminación. `None` en TRD.
    tipo_instrumento: str = "TRD"
    fondo_nombre: str | None = None
    justificacion_valoracion: str | None = None


class TipoDocumentalCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    nombre: str = Field(..., min_length=1)
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool = False
    metadata_template: str | None = Field(default=None, max_length=100)
    activo: bool = True
    #: ¿Se ofrece este tipo en la VENTANILLA ÚNICA pública (radicación sin
    #: autenticar)? Opt-in deliberado: un tipo interno —«Acta de comité»— no
    #: debe aparecer en un formulario público solo porque exista en el catálogo.
    #: Sin este campo en el contrato, la única forma de habilitarlo sería un
    #: UPDATE a mano, y cerrar una puerta sin dejar la manija es media
    #: corrección.
    admite_ventanilla_publica: bool = False


class TipoDocumentalUpdate(BaseModel):
    nombre: str | None = None
    trd_serie_id: uuid.UUID | None = None
    obligatorio: bool | None = None
    metadata_template: str | None = None
    activo: bool | None = None
    admite_ventanilla_publica: bool | None = None


class TipoDocumentalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trd_serie_id: uuid.UUID | None
    code: str
    nombre: str
    obligatorio: bool
    metadata_template: str | None
    activo: bool
    admite_ventanilla_publica: bool
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


class AipResponse(BaseModel):
    """Espejo del `AipResponse` de storage-service (E10), reexpuesto por el
    endpoint mediado `GET /expedientes/{id}/aip` — único punto de acceso
    EXTERNO al AIP tras el cierre del no-read-up (RF-SEG-08, E10 Increment C):
    `GET /preservacion/aip/{id}` en storage-service ahora exige
    `X-Internal-Token` y ya no es alcanzable desde el gateway.

    Mismos campos que `storage-service.app.schemas.preservacion.AipResponse`
    (deserializados del JSON que devuelve `app.clients.storage_client.get_aip`) —
    duplicado deliberadamente (no se comparte el paquete de schemas entre
    servicios, ADR-002) en vez de reexportar `dict[str, Any]` sin tipar, para
    que un cambio de forma en storage-service rompa la validación aquí en vez
    de filtrarse silenciosamente al cliente del gateway.
    """

    id: uuid.UUID
    expediente_id: str
    aip_sha256: str
    indice_sha256: str | None = None
    num_documentos: int
    estado: str
    created_at: datetime
    premis_xml: str
    bucket: str | None = None
    object_key: str | None = None
    aip_bytes: int | None = None
    formato: str
    retain_until: datetime | None = None
    retention_mode: str | None = None
    legal_hold: bool


class EventoPreservacionResponse(BaseModel):
    """Espejo del `EventoResponse` de storage-service (E10), reexpuesto por el
    endpoint mediado `GET /expedientes/{id}/preservacion/eventos` — único
    punto de acceso EXTERNO a los eventos PREMIS de UN expediente tras el
    cierre del no-read-up (RF-SEG-08, continuación de E10 Increment C):
    `GET /preservacion/eventos` en storage-service ahora exige
    `X-Internal-Token` y ya no es alcanzable desde el gateway.

    Mismos campos que `storage-service.app.schemas.preservacion.EventoResponse`
    (deserializados del JSON que devuelve
    `app.clients.storage_client.list_eventos`) — duplicado deliberadamente
    (no se comparte el paquete de schemas entre servicios, ADR-002), mismo
    criterio que `AipResponse`."""

    id: uuid.UUID
    object_ref: str
    tipo: str
    resultado: str
    hash_before: str | None = None
    hash_after: str | None = None
    detalle: dict[str, Any] = Field(default_factory=dict)
    agente: str | None = None
    ts: datetime

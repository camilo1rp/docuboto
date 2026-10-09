# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de preservación digital (E10, ADR/AGN 001/2024)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.preservacion_tipos import MULTI_ARTEFACTO_TIPOS

_TIPO = Literal[
    "INGESTA", "FIJACION", "MIGRACION", "VALIDACION_PDFA", "WORM", "REPLICA", "RENOVACION"
]

_EXPEDIENTE_ID_WS_RE = re.compile(r"\s+")

# `nombre` se usa LITERALMENTE como ruta de payload dentro del Bag
# (`data/{nombre}`, ver `aip_builder.build_bag`) y como línea de texto en
# `manifest-sha256.txt`/`tagmanifest-sha256.txt`. Rechaza separadores de ruta
# ('/', '\\') y caracteres de control/nueva línea (incluye CR/LF) — así se
# cierra en el origen la posibilidad de zip-slip (rutas fuera de `data/`) y de
# inyectar líneas falsas en el manifiesto (hallazgo de auditoría Increment B).
_SAFE_NOMBRE_RE = re.compile(r"^[^/\\\x00-\x1f]+$")

# Tipo MIME `tipo/subtipo` (RFC 6838 §4.2, juego de caracteres restringido a lo
# que de verdad aparece en un archivo documental). Se valida en el schema y no
# solo se guarda porque un valor mal escrito —"msword", "application/ msword"—
# NO casa con ningún archivo y el barrido no convertiría nada, con la política
# apareciendo como declarada en la pantalla. Es justo el fallo que más importa
# evitar aquí: lo peligroso no es convertir de más, es creer que se convirtió.
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$")


def normalizar_mimes(valores: list[str]) -> list[str]:
    """Normaliza y valida una lista de tipos MIME declarada por la entidad.

    Minúsculas (los tipos MIME son case-insensitive, la comparación del barrido
    es exacta), sin espacios y sin repetidos, conservando el orden en que la
    entidad los escribió.
    """
    vistos: list[str] = []
    for crudo in valores:
        valor = crudo.strip().lower()
        if not valor:
            continue
        if not _MIME_RE.match(valor):
            raise ValueError(
                f"{valor!r} no es un tipo MIME válido; se espera la forma tipo/subtipo "
                "(por ejemplo application/msword)"
            )
        if valor not in vistos:
            vistos.append(valor)
    return vistos


def canonicalize_expediente_id(value: str) -> str:
    """Normaliza espacios en blanco de `expediente_id`.

    El gate de purga (`StorageService.purge_for_disposition` →
    `PreservacionWormRepository.active_lock_for_expediente`) llavea por
    IGUALDAD EXACTA de este string contra el valor grabado al proteger el
    índice (`proteger_indice`). Protección y disposición son dos llamadas
    HTTP independientes (la primera vía este schema; la disposición final la
    ejecuta archive-service con `POST /lotes/{id}/ejecutar`, que llega a
    `StorageService.purge_for_disposition`);
    si el string difiere por espacios incidentales entre ambas, el gate no
    encuentra el candado y la purga procedería sin bloquear (hallazgo de
    auditoría Increment A). Esta función es la ÚNICA fuente de canonicalización
    y debe aplicarse en ambos extremos del gate.
    """
    canonical = _EXPEDIENTE_ID_WS_RE.sub(" ", value.strip())
    if not canonical:
        raise ValueError("expediente_id no puede quedar vacío tras normalizar espacios")
    return canonical


class PlanCreate(BaseModel):
    formatos_destino: list[str] = Field(default_factory=list)
    # RF-PRE-04: qué formatos declara la entidad en riesgo de obsolescencia —
    # el otro extremo de `formatos_destino`, que ya decía a qué se convierte.
    # Vacío = política no declarada en el plan (el barrido no migra nada de
    # esta entidad, salvo que el despliegue traiga un valor por defecto).
    formatos_origen: list[str] = Field(default_factory=list, max_length=64)
    num_copias: int = Field(default=1, ge=1)
    periodicidad_fixity_dias: int = Field(default=180, ge=1)
    politica_migracion: str | None = None
    contingencia: str | None = None

    @field_validator("formatos_origen")
    @classmethod
    def _normalizar_origen(cls, v: list[str]) -> list[str]:
        return normalizar_mimes(v)


class EstadoResponse(BaseModel):
    """Estado de INSTANCIA de la preservación (no por expediente)."""

    enabled: bool


class PlanResponse(BaseModel):
    id: uuid.UUID
    version: int
    formatos_destino: list[str]
    formatos_origen: list[str]
    num_copias: int
    periodicidad_fixity_dias: int
    politica_migracion: str | None
    contingencia: str | None
    creado_por: str | None
    created_at: datetime


class EventoCreate(BaseModel):
    object_ref: str = Field(..., min_length=1, max_length=255)
    tipo: _TIPO
    # "no_evaluado" (E10 Increment C2a, migración 008): estado HONESTO para
    # VALIDACION_PDFA cuando el validador real no corrió (RF-FIR-15) — nunca
    # implica conformidad ni no-conformidad, distinto de "ok"/"fallo".
    resultado: Literal["ok", "fallo", "no_evaluado"] = "ok"
    hash_before: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    hash_after: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    detalle: dict[str, Any] = Field(default_factory=dict)


class EventoInternalCreate(EventoCreate):
    """Cuerpo de `POST /eventos-internal` (AREA-07-05): un evento PREMIS que registra OTRO
    servicio (archive: el fixity-check de la recepción de una transferencia).

    A diferencia de `POST /eventos` (permiso de usuario), aquí no hay persona detrás de la
    petición HTTP: el llamador declara el `agente` —el humano que ejecutó el acto— y el
    token interno prueba que llama un servicio de la red. `dedup_key` hace el POST
    idempotente: la reconciliación de archive reintenta (at-least-once) y no debe duplicar
    el evento en un registro append-only."""

    agente: str | None = Field(default=None, max_length=255)
    dedup_key: str | None = Field(default=None, min_length=1, max_length=255)


class RetentionMixin(BaseModel):
    """Contrato de retención WORM compartido por `ProtegerIndiceRequest` (índice,
    Increment A) y `AipCreate` (AIP real, Increment B) — misma regla en ambos
    extremos (RF-PRE-03): o `retain_until` directo, o `retencion_anios` +
    `fecha_inicio` (nunca ambos). `storage-service` no conoce la TRD; el
    llamador (archive-service) aporta el plazo ya resuelto o los insumos.

    `legal_hold` YA NO es un campo del body (AREA-08-07): un freeze de litigio
    fijado al proteger no tiene acto inverso en el contrato de protección y
    bloquea la disposición para siempre. Se declara SOLO por el acto interno
    `POST /legal-hold-internal` (con motivo y asiento), que también lo levanta.
    Enviarlo aquí da 422 en vez de ignorarse en silencio (sin `extra="forbid"`
    general: el resto de campos ajenos, p. ej. un `tenant_slug` hostil, se
    siguen ignorando como antes).
    """

    @model_validator(mode="before")
    @classmethod
    def _rechaza_legal_hold(cls, data: Any) -> Any:
        if isinstance(data, dict) and "legal_hold" in data:
            raise ValueError(
                "legal_hold no se declara al proteger: use el acto interno "
                "POST /legal-hold-internal (con motivo y acta_ref)"
            )
        return data

    retain_until: datetime | None = None
    retencion_anios: int | None = Field(default=None, ge=1, le=100)
    fecha_inicio: date | None = None

    @property
    def legal_hold(self) -> bool:
        """Toda protección nace SIN legal hold (ver docstring de la clase)."""
        return False

    @model_validator(mode="after")
    def _check_retention_source(self) -> RetentionMixin:
        directo = self.retain_until is not None
        derivado_anios = self.retencion_anios is not None
        derivado_fecha = self.fecha_inicio is not None
        if directo and (derivado_anios or derivado_fecha):
            raise ValueError(
                "Especifique retain_until O (retencion_anios + fecha_inicio), no ambos"
            )
        if not directo and not (derivado_anios and derivado_fecha):
            raise ValueError(
                "Debe especificar retain_until, o retencion_anios y fecha_inicio juntos"
            )
        return self

    def resolve_retain_until(self) -> datetime:
        """Fecha de retención resuelta a un `datetime` UTC-aware.

        La rama derivada usa aritmética de AÑOS CALENDARIO (``date.replace(year=...)``),
        no `timedelta(days=365*n)`: un plazo TRD de "5 años desde 2026-01-01" debe
        vencer el 2031-01-01, y no un día antes por culpa de un año bisiesto (2028)
        en medio del intervalo.
        """
        if self.retain_until is not None:
            dt = self.retain_until
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        assert self.fecha_inicio is not None and self.retencion_anios is not None
        start = self.fecha_inicio
        try:
            end_date = start.replace(year=start.year + self.retencion_anios)
        except ValueError:
            # 29 de febrero + N años que no caen en bisiesto → 28 de febrero
            # (convención civil estándar para este desbordamiento de fecha).
            end_date = start.replace(month=2, day=28, year=start.year + self.retencion_anios)
        return datetime.combine(end_date, time.min, tzinfo=UTC)


class AipDocInput(BaseModel):
    """Un documento a incluir en el AIP.

    `file_id` localiza el objeto REAL en `files` (bucket -documents) — storage
    resuelve `object_key`/`sha256` registrado a partir de él. `valor_huella` NO
    existe en este esquema: la fixity del AIP real (Increment B) SIEMPRE se
    recalcula sobre los bytes descargados de MinIO, nunca se confía en un hash
    aportado por el llamador (hallazgo evitado desde el diseño, a diferencia
    del stub del AIP que sí lo aceptaba).
    """

    document_id: str = Field(..., min_length=1)
    nombre: str = Field(..., min_length=1, max_length=255)
    file_id: uuid.UUID

    @field_validator("nombre")
    @classmethod
    def _validate_nombre(cls, value: str) -> str:
        if value in (".", ".."):
            raise ValueError("nombre no puede ser '.' ni '..'")
        if not _SAFE_NOMBRE_RE.match(value):
            raise ValueError(
                "nombre no puede contener '/', '\\\\' ni caracteres de "
                "control/nueva línea"
            )
        return value


def _safe_arcname(value: str) -> str:
    """Sanea una ruta relativa dentro del ZIP (permite subcarpetas, bloquea
    zip-slip): cada segmento no puede ser vacío, '.', '..' ni contener '\\' o
    caracteres de control. Devuelve la ruta normalizada con '/'."""
    segments: list[str] = []
    for raw in value.replace("\\", "/").split("/"):
        seg = raw.strip()
        if not seg or seg in (".", ".."):
            raise ValueError(f"arcname inválido (segmento {raw!r})")
        if any(ord(c) < 32 for c in seg):
            raise ValueError("arcname no puede contener caracteres de control")
        segments.append(seg)
    if not segments:
        raise ValueError("arcname vacío")
    return "/".join(segments)


class ExportZipItem(BaseModel):
    """Un anexo a incluir en el ZIP de exportación del expediente (E02/export).

    `file_id` localiza el objeto real en `files`; `arcname` es la ruta relativa
    (con subcarpetas) dentro del ZIP — saneada contra zip-slip."""

    file_id: uuid.UUID
    arcname: str = Field(..., min_length=1, max_length=400)

    @field_validator("arcname")
    @classmethod
    def _validate_arcname(cls, value: str) -> str:
        return _safe_arcname(value)


class ExportExtraFile(BaseModel):
    """Un archivo generado por archive (índice XML, manifiesto CSV) a incrustar
    en el ZIP. `contenido_b64` son los bytes en base64."""

    arcname: str = Field(..., min_length=1, max_length=400)
    contenido_b64: str

    @field_validator("arcname")
    @classmethod
    def _validate_arcname(cls, value: str) -> str:
        return _safe_arcname(value)


class ExportZipCreate(BaseModel):
    """Ensambla un ZIP de exportación de expediente a partir de un inventario de
    anexos (`file_id`) + archivos extra generados por archive. NO persiste nada:
    descarga los bytes de MinIO, arma el ZIP y lo devuelve para streaming."""

    items: list[ExportZipItem] = Field(default_factory=list)
    extra_files: list[ExportExtraFile] = Field(default_factory=list)


class AipCreate(RetentionMixin):
    """Empaqueta un AIP (BagIt real + PREMIS v3) y lo sube al bucket WORM.

    `indice_file_id` es opcional: si se aporta, el índice electrónico firmado
    (ya presente en `files`) se embebe en el Bag — como tag file
    (`indice-firmado.xml`) cuando `documentos` no está vacío, o como PAYLOAD
    (`data/indice-firmado.xml`) cuando `documentos` SÍ está vacío (E10 AIP
    wiring follow-up, expedientes 100% físicos — ver
    `app.services.aip_builder._is_indice_only_mode`) — con su propia fixity
    recalculada.

    `documentos` admite lista VACÍA (`min_length=0`, desde el follow-up
    índice-only): un expediente sin anexos digitales sigue siendo
    empaquetable en un AIP OAIS legítimo cuyo único objeto sustantivo es el
    índice electrónico firmado. `_check_at_least_one_payload` (abajo) es la
    única restricción real — un AIP totalmente vacío (sin documentos NI
    índice) se rechaza con 422: no sería un paquete OAIS, sería un Bag
    empty-payload inválido por RFC 8493 (`aip_builder.EmptyPayloadBagError`
    es la defensa en profundidad del lado del builder para esta misma regla).
    """

    expediente_id: str = Field(..., min_length=1, max_length=255)
    documentos: list[AipDocInput] = Field(..., min_length=0)
    indice_file_id: uuid.UUID | None = None
    # E10 debt "perfil PDF/A por serie" (RF-FIR-15): resuelto por
    # archive-service a partir de `trd_series.pdfa_profile` -- cruza la
    # frontera de confianza archive-service -> storage-service, ver
    # `AipService._resolve_pdfa_profile`. Deliberadamente PERMISIVO aquí (sin
    # whitelist Pydantic): un valor fuera de `_VALID_FLAVOURS`
    # (`app/services/pdfa/subprocess_verapdf.py`) NO debe rechazar el AIP
    # completo con un 422 -- el contrato fail-closed (RF-FIR-15, invalid !=
    # default) es que el validador PDF/A degrade ESOS documentos a
    # `not_evaluated`, no que el empaquetado entero aborte. `None` = sin
    # política de serie, cae al default global (`settings.pdfa_profile`).
    pdfa_profile: str | None = Field(default=None, max_length=32)

    @field_validator("expediente_id")
    @classmethod
    def _canonicalize_expediente_id(cls, value: str) -> str:
        return canonicalize_expediente_id(value)

    @model_validator(mode="after")
    def _check_unique_nombres(self) -> AipCreate:
        # `nombre` es la clave de payload dentro del Bag (`data/{nombre}`) —
        # un duplicado colapsaría dos documentos en una sola entrada del ZIP
        # mientras `num_files`/`Payload-Oxum`/PREMIS seguirían contando ambos
        # (hallazgo de auditoría Increment B: AIP inconsistente/incompleto).
        # Con `documentos=[]` (índice-only) la lista de nombres es vacía —
        # `len([]) == len(set())` es trivialmente cierto, no hace falta un
        # caso especial.
        nombres = [doc.nombre for doc in self.documentos]
        if len(nombres) != len(set(nombres)):
            raise ValueError(
                "Los 'nombre' de 'documentos' deben ser únicos dentro del AIP"
            )
        return self

    @model_validator(mode="after")
    def _check_at_least_one_payload(self) -> AipCreate:
        # E10 AIP wiring follow-up (índice-only): `documentos` ahora admite
        # lista vacía, pero un AIP completamente vacío (sin documentos NI
        # índice) no es un paquete OAIS válido -- sería un Bag empty-payload,
        # inválido por RFC 8493 (ver `aip_builder.EmptyPayloadBagError`, la
        # defensa en profundidad equivalente del lado del builder). Rechazar
        # aquí, en la capa Pydantic, evita siquiera intentar el empaquetado.
        if not self.documentos and self.indice_file_id is None:
            raise ValueError(
                "AipCreate requiere al menos uno de 'documentos' (no vacío) "
                "o 'indice_file_id' -- un AIP sin documentos ni índice no es "
                "un paquete OAIS válido"
            )
        return self


class AipResponse(BaseModel):
    """Respuesta del AIP real (Increment B).

    Reemplaza el stub previo: ya NO devuelve `bagit` como texto (el Bag es un
    ZIP binario en el bucket WORM, no un string) — `aip_sha256` ahora es la
    huella del ZIP completo serializado, no de un texto sintético. `premis_xml`
    se conserva (sigue siendo texto, útil para el llamador).

    `bucket`/`object_key`/`aip_bytes`/`retain_until`/`retention_mode` son
    OPCIONALES: la migración 007 es aditiva/nullable y preserva como válidas
    las filas `aip` insertadas por el stub del Increment A (esas columnas
    quedan NULL en esas filas). Declararlos NOT NULL rompía `GET
    /aip/{expediente_id}` con un 500 cuando el AIP vigente de un expediente
    era todavía un stub (hallazgo de auditoría Increment B).

    `replicado`/`replica_target`/`replica_copias_hechas` (E10 Increment C1b,
    RF-FIR-15) NO se persisten en la tabla `aip` (SIN migración en este
    incremento, PIEZA 5: la réplica solo deja rastro en
    `preservacion_evento` tipo=REPLICA) — llevan default para que `GET
    /aip/{expediente_id}` (construido desde la fila cruda de BD, que nunca
    tiene estas columnas) siga validando; solo la respuesta DIRECTA de
    `POST /aip` (que construye `AipService.empaquetar_aip` a mano, no desde
    la fila) los rellena con el resultado real de la réplica intentada en
    esa misma llamada.
    """

    id: uuid.UUID
    expediente_id: str
    aip_sha256: str
    indice_sha256: str | None
    num_documentos: int
    estado: str
    created_at: datetime
    premis_xml: str
    bucket: str | None
    object_key: str | None
    aip_bytes: int | None
    formato: str
    retain_until: datetime | None
    retention_mode: str | None
    legal_hold: bool
    replicado: bool = False
    replica_target: Literal["local", "cross-site"] | None = None
    replica_copias_hechas: int | None = 1


class EventoResponse(BaseModel):
    id: uuid.UUID
    object_ref: str
    tipo: str
    resultado: str
    hash_before: str | None
    hash_after: str | None
    detalle: dict[str, Any]
    agente: str | None
    ts: datetime


# ── WORM (E10 Increment A, RF-PRE-03) ─────────────────────────────────


class ProtegerIndiceRequest(RetentionMixin):
    """Protege bajo WORM un artefacto YA FIRMADO subido a `files` — el índice
    electrónico (`tipo='indice'`, Increment A) o, desde E10 Inc.2 wiring
    (fast-follow declarado en el diseño de Inc.1), el acta de transferencia
    (`tipo='acta_transferencia'`). Nombre de la clase conservado por
    compatibilidad histórica (`/proteger-indice` sigue siendo un alias fijo a
    `tipo='indice'`) aunque hoy sirve a ambos artefactos vía `/proteger-artefacto`.

    El `object_key`/bucket/hash conocido se resuelven internamente a partir de
    `file_id` (la fila de `files` en el bucket -documents) — el llamador NO puede
    declarar un hash arbitrario; storage-service recalcula el SHA-256 y lo
    contrasta contra el registrado al momento de la subida original (fixity).

    La retención (`retain_until` directo, o `retencion_anios` + `fecha_inicio`,
    nunca ambos) es el contrato compartido de `RetentionMixin` — el mismo que
    usa `AipCreate` (Increment B).

    Contrato `file_id` ↔ `expediente_id` (hallazgo de auditoría Increment A):
    `files` NO tiene, en este incremento, un enlace a expediente/documento —
    storage-service no puede verificar por sí mismo que `file_id` sea
    efectivamente el artefacto firmado DEL `expediente_id` declarado. Ese
    vínculo es responsabilidad exclusiva del llamador: **solo archive-service**
    invoca estas rutas — lo hace cumplir el gateway (no enruta
    `/proteger-*` ni `/aip`) y `X-Internal-Token` en storage (AREA-08-16) — y
    garantiza (a) `file_id` referencia el artefacto firmado correspondiente al
    `tipo` declarado (E15/E06 para 'indice', H-H/E06 para 'acta_transferencia')
    y (b) `expediente_id` es el identificador CANÓNICO que también usará al
    llamar la disposición final (mismo string, ver `canonicalize_expediente_id`).
    Como COMPLIANCE es irreversible, un error aquí no es corregible después.

    Unicidad por `(expediente_id, tipo)` (índices únicos parciales, migraciones
    009/010): tanto el índice como el acta son, en la práctica, SINGLETON por
    expediente — el ciclo de vida `open -> closed -> transferred` (TERMINAL) y
    `ux_transferencias_activa_por_expediente` (archive-service, migración 024)
    garantizan que un expediente alcanza acta congelada A LO SUMO UNA VEZ.
    """

    file_id: uuid.UUID
    expediente_id: str = Field(..., min_length=1, max_length=255)
    # E10 Inc.2 wiring: generaliza el artefacto protegido -- 'indice' es el
    # default para no romper ningún llamador existente (`/proteger-indice`
    # NUNCA declara `tipo` en el body, lo fija el router antes de invocar el
    # servicio). 'aip' NO es un valor válido aquí a propósito: el AIP real usa
    # su propio schema `AipCreate` (Increment B), con su propia semántica de
    # NO-singleton (re-empaquetable), ver la migración 009.
    tipo: Literal[
        "indice",
        "acta_transferencia",
        "acta_eliminacion",
        # Firmas PERSONALES del acta de transferencia, una por rol (AREA-07-10, mig. 018).
        "acta_firma_elabora",
        "acta_firma_entrega",
        "acta_firma_recibe",
        # Constancias de «firma inobtenible» (AREA-07-10, mig. 019): sello y firma personal.
        "acta_firma_excepcion_elabora",
        "acta_firma_excepcion_entrega",
        "acta_firma_excepcion_recibe",
        "acta_firma_excepcion_personal_elabora",
        "acta_firma_excepcion_personal_entrega",
        "acta_firma_excepcion_personal_recibe",
        "acta_firma_revocacion_elabora",
        "acta_firma_revocacion_entrega",
        "acta_firma_revocacion_recibe",
        "acta_firma_revocacion_personal_elabora",
        "acta_firma_revocacion_personal_entrega",
        "acta_firma_revocacion_personal_recibe",
        "acta_firma_excepcion_sobre_elabora",
        "acta_firma_excepcion_sobre_entrega",
        "acta_firma_excepcion_sobre_recibe",
        "acta_firma_revocacion_sobre_elabora",
        "acta_firma_revocacion_sobre_entrega",
        "acta_firma_revocacion_sobre_recibe",
    ] = "indice"
    # De QUÉ es `expediente_id` (migración 014). El acta de eliminación
    # pertenece a un LOTE —cubre N expedientes que además van a dejar de
    # existir—, y el CHECK de la base impone la pareja: un `acta_eliminacion`
    # siempre de un lote, todo lo demás siempre de un expediente. Aquí se
    # valida antes para dar un 422 legible en vez de una violación opaca.
    objeto_tipo: Literal["expediente", "lote"] = "expediente"

    @model_validator(mode="after")
    def _pareja_tipo_propietario(self) -> ProtegerIndiceRequest:
        if (self.tipo == "acta_eliminacion") != (self.objeto_tipo == "lote"):
            raise ValueError(
                "El acta de eliminación pertenece a un lote, y todo lo demás a un "
                "expediente: `tipo` y `objeto_tipo` no casan."
            )
        return self

    @field_validator("expediente_id")
    @classmethod
    def _canonicalize_expediente_id(cls, value: str) -> str:
        return canonicalize_expediente_id(value)


class ProtegerIndiceResponse(BaseModel):
    id: uuid.UUID
    expediente_id: str
    tipo: str
    objeto_tipo: str = "expediente"
    bucket: str
    object_key: str
    version_id: str | None
    sha256: str
    retain_until: datetime
    retention_mode: str
    legal_hold: bool
    created_at: datetime
    # `None` solo para filas insertadas ANTES de la migración 009 (Inc.1) —
    # cualquier fila nueva SIEMPRE lo trae (`ProtegerIndiceRequest.file_id` es
    # obligatorio).
    file_id: uuid.UUID | None = None


# ── Renovación de retención WORM para series CT (E10 debt) ────────────


class RenovarRetencionRequest(RetentionMixin):
    """Extiende (NUNCA acorta) la retención WORM ya registrada para un
    artefacto -- índice o acta de transferencia -- de series de disposición
    Conservación Total (CT), cuyo objeto no puede vencer nunca aunque el lock
    COMPLIANCE original sí lo haga.

    Identifica la fila POR ``(expediente_id, tipo)`` (el mismo par que la
    unicidad parcial de las migraciones 009/010) -- NO por ``worm_object_id``,
    para que el llamador (archive-service) no tenga que resolver ni cachear
    ese UUID interno de storage-service; ya conoce ``expediente_id``/``tipo``
    desde su propio espejo (``expediente_indice.worm_retain_until`` /
    ``transferencia_acta_firma.worm_retain_until``).

    Reusa el contrato de retención de `RetentionMixin` (``retain_until``
    directo, o ``retencion_anios`` + ``fecha_inicio``) -- archive-service
    SIEMPRE usa la segunda forma con ``fecha_inicio=hoy`` (ventana rodante:
    ``now() + total_TRD``, NUNCA una fecha fija lejana/9999 -- ver
    ``PreservationWormService.renovar_retencion``).

    Semántica MONOTÓNICA: la fecha resuelta debe ser ESTRICTAMENTE mayor que
    el ``retain_until`` ya persistido, o la llamada es un no-op idempotente
    (200, SIN tocar MinIO). El ``legal_hold`` NO existe aquí --
    la renovación es puramente de fecha; el freeze de
    litigio se gestiona por el acto aparte `POST /legal-hold-internal`
    (`active_lock_for_expediente` lo trata como bloqueo incondicional
    aparte de `retain_until`).

    `tipo='aip'` (E10 debt: extensión de la renovación al AIP OAIS, fast-follow
    del cableado del AIP): a diferencia de `indice`/`acta_transferencia`
    (SINGLETON por expediente, migraciones 009/010), el AIP admite N filas
    `preservacion_worm_objeto` por expediente (re-empaquetado legítimo,
    migración 012) -- `get_by_expediente_and_tipo` ya resuelve la MÁS
    RECIENTE (`ORDER BY created_at DESC LIMIT 1`), así que renovar siempre
    extiende el AIP vigente, sin cambio de comportamiento en el repositorio.

    `tipo='acta_firma_{elabora,entrega,recibe}'` (E10 debt, AREA-07-10): el XML de cada firma
    PERSONAL del acta (migración 018, un tipo por rol, SINGLETON por (expediente, tipo)) se
    renueva igual que el índice; sin cambio en el servicio ni en el repositorio -- la fila se
    identifica por `(expediente_id, tipo)` y la extensión sigue siendo monotónica."""

    expediente_id: str = Field(..., min_length=1, max_length=255)
    tipo: Literal[
        "indice",
        "acta_transferencia",
        "aip",
        "acta_firma_elabora",
        "acta_firma_entrega",
        "acta_firma_recibe",
        "acta_firma_excepcion_elabora",
        "acta_firma_excepcion_entrega",
        "acta_firma_excepcion_recibe",
        "acta_firma_excepcion_personal_elabora",
        "acta_firma_excepcion_personal_entrega",
        "acta_firma_excepcion_personal_recibe",
        "acta_firma_revocacion_elabora",
        "acta_firma_revocacion_entrega",
        "acta_firma_revocacion_recibe",
        "acta_firma_revocacion_personal_elabora",
        "acta_firma_revocacion_personal_entrega",
        "acta_firma_revocacion_personal_recibe",
        "acta_firma_excepcion_sobre_elabora",
        "acta_firma_excepcion_sobre_entrega",
        "acta_firma_excepcion_sobre_recibe",
        "acta_firma_revocacion_sobre_elabora",
        "acta_firma_revocacion_sobre_entrega",
        "acta_firma_revocacion_sobre_recibe",
    ]
    # FIRMA-2: los tipos multi-artefacto (constancias de firma inobtenible y sus revocaciones)
    # acumulan N objetos por (expediente, tipo): la fila se identifica por `file_id` y es
    # OBLIGATORIO. Sin él, `ORDER BY created_at DESC LIMIT 1` extendería solo la última.
    # Para los demás tipos se ignora (siguen identificándose por (expediente, tipo)).
    file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _file_id_obligatorio_si_multi_artefacto(self) -> RenovarRetencionRequest:
        if self.tipo in MULTI_ARTEFACTO_TIPOS and self.file_id is None:
            raise ValueError(
                f"`file_id` es obligatorio para tipo={self.tipo}: hay N artefactos por "
                "(expediente, tipo)."
            )
        return self

    @field_validator("expediente_id")
    @classmethod
    def _canonicalize_expediente_id(cls, value: str) -> str:
        return canonicalize_expediente_id(value)


class RenovarRetencionResponse(BaseModel):
    id: uuid.UUID
    expediente_id: str
    tipo: str
    bucket: str
    object_key: str
    retain_until: datetime
    retention_mode: str
    # `True` solo si ESTA llamada extendió `retain_until` (la nueva fecha
    # resuelta era estrictamente mayor que la persistida y MinIO confirmó la
    # extensión). `False` = no-op idempotente: `retain_until` ya cubría (o
    # superaba) la fecha resuelta, no se llamó a MinIO.
    renovado: bool
    renovaciones: int
    renovado_at: datetime | None


class MigrarFormatoRequest(BaseModel):
    """`POST /api/v1/preservacion/migrar-formato` (E10, RF-PRE-04).

    `perfil` se acota a las variantes **`b`** de PDF/A: las `a`/`u` exigen texto
    etiquetado o Unicode garantizado, y una conversión automática no puede
    prometerlo — ofrecerlas sería declarar una conformidad que el convertidor no
    alcanza."""

    file_id: uuid.UUID
    perfil: Literal["1b", "2b", "3b"] = "2b"


class MigracionResponse(BaseModel):
    """Un registro de la cadena de procedencia.

    `file_id_destino` y `sha256_destino` viajan en `None` cuando la migración no
    produjo una copia admisible — que es la mayor parte de la información útil
    cuando algo va mal."""

    id: uuid.UUID
    file_id_origen: uuid.UUID
    file_id_destino: uuid.UUID | None
    formato_origen: str
    formato_destino: str
    sha256_origen: str
    sha256_destino: str | None
    herramienta: str
    estado: str
    motivo: str | None
    iniciada_por: uuid.UUID | None
    iniciada_at: datetime


# ── Legal hold (freeze de litigio) — acto interno (AREA-08-07) ─────────


class LegalHoldRequest(BaseModel):
    """Fija o levanta el legal hold de TODOS los objetos WORM de un expediente.

    Solo por la ruta interna (`X-Internal-Token`): lo orquesta archive-service,
    que es quien decide el permiso y comprueba el acto administrativo. `motivo`
    y `acta_ref` son OBLIGATORIOS y viajan al asiento de auditoría: un freeze
    que bloquea la disposición final (Ley 594/2000) sin acto que lo respalde es
    un bloqueo arbitrario.
    """

    model_config = ConfigDict(extra="forbid")

    expediente_id: str = Field(..., min_length=1, max_length=255)
    activo: bool
    motivo: str = Field(..., min_length=10, max_length=2000)
    acta_ref: str = Field(..., min_length=1, max_length=255)

    @field_validator("expediente_id")
    @classmethod
    def _canonicalize_expediente_id(cls, value: str) -> str:
        return canonicalize_expediente_id(value)


class LegalHoldResponse(BaseModel):
    expediente_id: str
    activo: bool
    objetos_afectados: int
    objetos_sin_cambio: int

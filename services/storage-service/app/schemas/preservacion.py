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

from pydantic import BaseModel, Field, field_validator, model_validator

_TIPO = Literal["INGESTA", "FIJACION", "MIGRACION", "VALIDACION_PDFA", "WORM", "REPLICA"]

_EXPEDIENTE_ID_WS_RE = re.compile(r"\s+")

# `nombre` se usa LITERALMENTE como ruta de payload dentro del Bag
# (`data/{nombre}`, ver `aip_builder.build_bag`) y como línea de texto en
# `manifest-sha256.txt`/`tagmanifest-sha256.txt`. Rechaza separadores de ruta
# ('/', '\\') y caracteres de control/nueva línea (incluye CR/LF) — así se
# cierra en el origen la posibilidad de zip-slip (rutas fuera de `data/`) y de
# inyectar líneas falsas en el manifiesto (hallazgo de auditoría Increment B).
_SAFE_NOMBRE_RE = re.compile(r"^[^/\\\x00-\x1f]+$")


def canonicalize_expediente_id(value: str) -> str:
    """Normaliza espacios en blanco de `expediente_id`.

    El gate de purga (`StorageService.purge_for_disposition` →
    `PreservacionWormRepository.active_lock_for_expediente`) llavea por
    IGUALDAD EXACTA de este string contra el valor grabado al proteger el
    índice (`proteger_indice`). Protección y disposición son dos llamadas
    HTTP independientes (hoy: la primera vía este schema; la disposición
    final es un roadmap item — ver `StorageService.purge_for_disposition`);
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
    num_copias: int = Field(default=1, ge=1)
    periodicidad_fixity_dias: int = Field(default=180, ge=1)
    politica_migracion: str | None = None
    contingencia: str | None = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    version: int
    formatos_destino: list[str]
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


class RetentionMixin(BaseModel):
    """Contrato de retención WORM compartido por `ProtegerIndiceRequest` (índice,
    Increment A) y `AipCreate` (AIP real, Increment B) — misma regla en ambos
    extremos (RF-PRE-03): o `retain_until` directo, o `retencion_anios` +
    `fecha_inicio` (nunca ambos). `storage-service` no conoce la TRD; el
    llamador (archive-service) aporta el plazo ya resuelto o los insumos.
    """

    retain_until: datetime | None = None
    retencion_anios: int | None = Field(default=None, ge=1, le=100)
    fecha_inicio: date | None = None
    legal_hold: bool = False

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


class AipCreate(RetentionMixin):
    """Empaqueta un AIP (BagIt real + PREMIS v3) y lo sube al bucket WORM.

    `indice_file_id` es opcional: si se aporta, el índice electrónico firmado
    (ya presente en `files`) se embebe en el Bag como tag file
    (`indice-firmado.xml`), con su propia fixity recalculada — redundante con
    la protección directa de `POST /proteger-indice` (Increment A), correcto
    para preservación (PIEZA 4).
    """

    expediente_id: str = Field(..., min_length=1, max_length=255)
    documentos: list[AipDocInput] = Field(..., min_length=1)
    indice_file_id: uuid.UUID | None = None

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
        nombres = [doc.nombre for doc in self.documentos]
        if len(nombres) != len(set(nombres)):
            raise ValueError(
                "Los 'nombre' de 'documentos' deben ser únicos dentro del AIP"
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
    """Protege bajo WORM el índice electrónico firmado ya subido a `files`.

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
    efectivamente el índice electrónico firmado DEL `expediente_id` declarado.
    Ese vínculo es responsabilidad exclusiva del llamador: el contrato del
    gateway establece que **solo archive-service** invoca este endpoint, y que
    garantiza (a) `file_id` referencia el índice firmado (E15/E06) y (b)
    `expediente_id` es el identificador CANÓNICO que también usará al llamar
    la disposición final (mismo string, ver `canonicalize_expediente_id`).
    Como COMPLIANCE es irreversible, un error aquí no es corregible después.
    """

    file_id: uuid.UUID
    expediente_id: str = Field(..., min_length=1, max_length=255)

    @field_validator("expediente_id")
    @classmethod
    def _canonicalize_expediente_id(cls, value: str) -> str:
        return canonicalize_expediente_id(value)


class ProtegerIndiceResponse(BaseModel):
    id: uuid.UUID
    expediente_id: str
    tipo: str
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

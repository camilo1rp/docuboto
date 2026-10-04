# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas del endpoint interno D-02 (E10 debt, cableado del AIP al cierre)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AnexosInternalRequest(BaseModel):
    """Lote de radicados cuyos anexos se necesitan resolver a `file_id` real.

    Batch deliberado (un solo round-trip): archive-service ya conoce TODOS
    los `radicado_id` vinculados al expediente que va a empaquetar, y
    resolverlos uno a uno sería N llamadas HTTP por expediente.
    """

    radicado_ids: list[uuid.UUID] = Field(..., min_length=1)
    # None (AIP, acto de sistema) = inventario TOTAL sin gate de clearance.
    # Con valor (exportación de expediente, RF-SEG-08) = filtra por
    # `nivel_seguridad <= max_clearance` y omite los radicados sobre-clearance
    # (incluida su existencia). Cierra la fuga read-up por-radicado del export.
    max_clearance: int | None = Field(default=None, ge=1, le=3)


class AnexoInternalItem(BaseModel):
    file_id: uuid.UUID
    filename: str


class RadicadoMetaItem(BaseModel):
    """Metadata de un radicado para el manifiesto de exportación (E02/export) y
    para la resolución EN EL SERVIDOR de la dependencia productora y el formato
    al vincularlo a un expediente (E15 perfil v4 R1, Ac. AGN 001/2024 art.
    4.3.2.3).

    Los cuatro campos añadidos son OPCIONALES y su ausencia es una respuesta
    legítima: un radicado puede no declarar dependencia de origen (ingesta
    antigua, ventanilla ciudadana) y puede no tener ningún anexo. Rellenarlos
    con un valor inventado sería peor que dejarlos vacíos — el manifiesto y el
    índice electrónico son artefactos probatorios."""

    subject: str
    doc_type: str
    # Número de radicado y tipología (`doc_class`) tal como los guarda ESTE
    # servicio (AREA-07-02 / AREA-06-08): archive-service los coteja al vincular
    # en vez de sellar en el índice lo que escriba quien vincula. `doc_class`
    # puede ser None (radicado sin tipología asignada) y su ausencia es una
    # respuesta legítima, no un hueco que rellenar.
    tracking_number: str | None = None
    doc_class: str | None = None
    # Dependencia PRODUCTORA del documento: la de origen del radicado, no la de
    # quien lo archiva después. Nombre y código viajan JUNTOS porque el índice
    # electrónico solo emite <dependenciaProductora> cuando tiene los dos.
    origin_dept: str | None = None
    origin_dept_code: int | None = None
    # Formato (MIME) y tamaño del documento principal = su PRIMER anexo por
    # `created_at`. Un radicado sin anexos no tiene formato que declarar.
    formato: str | None = None
    tamano_bytes: int | None = None


class PurgaInternalRequest(BaseModel):
    """`POST /api/v1/internal/documentos/purgar` (E04, RF-TRD-07 fase 5).

    **No lleva ningún interruptor.** Nada de `dry_run`, `forzar` ni
    `omitir_worm`: un acto irreversible no debe tener parámetros que cambien lo
    que hace, porque el día que uno se pase mal la pregunta «¿qué pidió
    exactamente quien llamó?» ya no tendrá respuesta comprobable.
    """

    radicado_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    # Llave del gate WORM en storage-service; no es un dato decorativo.
    expediente_id: str = Field(min_length=1, max_length=200)
    motivo: str = Field(min_length=3, max_length=500)
    actor: uuid.UUID | None = None


class PurgaInternalResponse(BaseModel):
    """Recuento REAL de lo destruido — el que la lápida va a afirmar para
    siempre. Se devuelve lo que ocurrió, nunca lo que se pidió."""

    radicados_eliminados: int
    anexos_eliminados: int

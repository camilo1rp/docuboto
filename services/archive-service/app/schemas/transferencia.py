# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de transferencias documentales (E12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TransferenciaCreate(BaseModel):
    tipo: Literal["primaria", "secundaria"]
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None = None
    ubicacion_destino_id: uuid.UUID | None = None
    observacion: str | None = Field(default=None, max_length=2000)


class TransferenciaRechazo(BaseModel):
    """Payload de `rechazar` (E12 H-K). El rechazo es una decisión de fondo
    (estado TERMINAL) y su motivo es el insumo de la subsanación -- a
    diferencia de `TransferenciaDecision` (retirado), `motivo` es
    OBLIGATORIO: antes se admitía un rechazo inmotivado (`observacion` con
    default `None`), lo que dejaba el porqué del rechazo sin registro
    reconstruible."""

    motivo: str = Field(min_length=3, max_length=2000)

    @field_validator("motivo")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("motivo de rechazo requerido (mín. 3 caracteres)")
        return v


class TransferenciaResponse(BaseModel):
    id: uuid.UUID
    tipo: str
    estado: str
    expediente_id: uuid.UUID
    ubicacion_origen_id: uuid.UUID | None
    ubicacion_destino_id: uuid.UUID | None
    creada_por: str | None
    observacion: str | None
    created_at: datetime
    enviada_at: datetime | None
    # `enviada_por` (migración 020, H-B/H-C): responsable de la REMISIÓN —
    # gemelo de `creada_por`/`decidida_por`, poblado desde `Actor.audit_actor`
    # (X-User-Id), nunca desde un username (RF-SEG-08 / Ac. AGN 001/2024 art.
    # 4.3.2.2, ver `app/core/actor.py`).
    enviada_por: str | None
    recibida_at: datetime | None
    decidida_por: str | None
    # `motivo_rechazo`/`rechazada_at` (migración 021, H-K/H-J): motivo
    # OBLIGATORIO del rechazo en columna propia (nunca pisa `observacion`,
    # el dato de preparación) + sello temporal propio del acto, gemelo de
    # `enviada_at`/`recibida_at`.
    motivo_rechazo: str | None = None
    rechazada_at: datetime | None = None


class TransferenciaActaFirmaInfo(BaseModel):
    """Estado del sellado XAdES del acta de entrega (H-H, E06/F4) --
    aditivo/retrocompat sobre `TransferenciaActaResponse` (migración 026,
    `transferencia_acta_firma`, tabla MUTABLE hermana del acta append-only).

    `presente=True` cuando `estado='firmado'`. `valida` es SIEMPRE `None`
    salvo que el llamante pida `?verify=true` (cómputo on-demand: descarga
    el XML firmado + `signature-service /verify` -- ver
    `TransferenciaService._build_firma_info`); por defecto esta lectura NO
    re-verifica, mismos campos baratos que ya persiste `transferencia_acta_firma`."""

    presente: bool
    valida: bool | None = None
    xades_level: str | None = None
    acreditado: bool = False
    motivo: str | None = None


class TransferenciaActaResponse(BaseModel):
    """Acta de entrega CONGELADA (E12 H-I, Ac. AGN 001/2024 Anexo FUID) --
    devuelta tal cual se escribió al recibir la transferencia, nunca
    regenerada (ver `TransferenciaService.acta`). Los tres responsables
    (`elaborado_por`/`entregado_por`/`recibido_por`) son el snapshot de las
    columnas de identidad de `transferencias` en el instante del traspaso."""

    transferencia_id: uuid.UUID
    expediente_id: uuid.UUID
    acta_fuid_xml: str
    acta_fuid_sha256: str
    elaborado_por: str | None
    entregado_por: str | None
    recibido_por: str | None
    elaborado_at: datetime | None
    entregado_at: datetime | None
    recibido_at: datetime
    # H-H (parcial): versión del índice electrónico pinneada dentro de la tx
    # de recepción (`fx.version`) -- el pin completo (`indice_firma_id` +
    # validación) queda diferido en roadmap.
    indice_version: int | None = None
    congelada_at: datetime
    # H-H (E06/F4): estado del sellado XAdES del acta -- aditivo, SIEMPRE
    # presente en la respuesta (calculado por el servicio, nunca un campo
    # crudo de `transferencia_acta`).
    firma: TransferenciaActaFirmaInfo

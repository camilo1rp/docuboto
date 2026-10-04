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
    # `objeto` (migración 052, Anexo FUID del Ac. AGN 001/2024): el propósito de
    # la transferencia, en la cabecera del acta de entrega. OBLIGATORIO y a
    # diferencia de `observacion` -- que es una nota de trabajo y puede faltar
    # -- este es uno de los tres campos con los que el Anexo FUID abre. Lo
    # escribe quien prepara porque es un dato HUMANO: componerlo a partir del
    # expediente y las ubicaciones produciría siempre la misma frase, que es
    # tanto como dejarlo vacío con más letras.
    objeto: str = Field(min_length=3, max_length=500)

    @field_validator("objeto")
    @classmethod
    def _objeto_no_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("objeto de la transferencia requerido (mín. 3 caracteres)")
        return v


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
    # Cabecera del FUID (migración 052): `objeto` lo escribe quien prepara; la
    # oficina productora y su unidad administrativa son el SNAPSHOT de la
    # dependencia activa de esa misma persona en ese instante. `None` en las
    # transferencias anteriores a la migración y cuando la dependencia no se
    # pudo resolver -- vacío honesto, nunca un valor compuesto.
    objeto: str | None = None
    oficina_productora_codigo: int | None = None
    oficina_productora_nombre: str | None = None
    unidad_administrativa_codigo: int | None = None
    unidad_administrativa_nombre: str | None = None


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
    # Resueltos al LEER (`TransferenciaRepository.get_acta`), no congelados: el
    # acta guarda el identificador —es probatoria y no se toca— pero una ficha
    # que dice quién elaboró, entregó y recibió no puede contestarlo con un
    # UUID. `None` cuando el responsable no es un usuario del catálogo.
    elaborado_por_nombre: str | None = None
    entregado_por_nombre: str | None = None
    recibido_por_nombre: str | None = None
    elaborado_at: datetime | None
    entregado_at: datetime | None
    recibido_at: datetime
    # H-H (parcial): versión del índice electrónico pinneada dentro de la tx
    # de recepción (`fx.version`) -- el pin completo (`indice_firma_id` +
    # validación) queda diferido en roadmap.
    indice_version: int | None = None
    # AREA-07-04 (migración 053): el sello que ampara el índice entregado y la huella
    # del XML que cubre, leídos de la MISMA fila que la fixity. `None` en las actas
    # anteriores a esa migración (no se inventa un vínculo en un acta ya sellada).
    indice_firma_id: uuid.UUID | None = None
    indice_sha256: str | None = None
    congelada_at: datetime
    # AREA-07-11: derivado al LEER, nunca guardado. `False` cuando el XML congelado trae
    # vacía alguna de las tres entradas de la cabecera del Anexo FUID (unidad
    # administrativa, oficina productora, objeto) -- las actas anteriores a la
    # migración 052 se sellaron así y no se rehacen (son probatorias); se DECLARA el
    # hueco en vez de esconderlo.
    cabecera_completa: bool = True
    # H-H (E06/F4): estado del sellado XAdES del acta -- aditivo, SIEMPRE
    # presente en la respuesta (calculado por el servicio, nunca un campo
    # crudo de `transferencia_acta`).
    firma: TransferenciaActaFirmaInfo


class FirmarPersonalRequest(BaseModel):
    """Body de `POST /transferencias/{id}/acta/firmar-personal` (E17/F4) -- el
    `totp_code` de la propia PARTE (remitente o receptor), verificado-y-
    consumido ATÓMICAMENTE por auth-service junto con la firma (D1 del
    contrato de firma personal, signature-service).

    `rol` es OPCIONAL y solo desempata el AUTO-TRASLADO (misma persona envió y
    recibió): NUNCA concede un rol -- el rol efectivo se deriva de la identidad
    congelada (`enviada_por`/`decidida_por`) en el servicio. Fuera del
    auto-traslado se ignora si coincide con la identidad y se rechaza (403) si
    no."""

    totp_code: str = Field(min_length=1, max_length=32)
    rol: Literal["entrega", "recibe", "elabora"] | None = None


class TransferenciaActaFirmaPersonalResponse(BaseModel):
    """Firma PERSONAL PKI por-usuario de un ROL del acta (E17/F4 Inc.1) --
    fila de `transferencia_acta_firma_personal` (migración 027), ADITIVA
    sobre el sello institucional (`TransferenciaActaFirmaInfo`)."""

    transferencia_id: uuid.UUID
    rol: Literal["recibe", "entrega", "elabora"]
    firmante_id: uuid.UUID
    firma_id: uuid.UUID
    minio_key_firmado: str | None = None
    xades_level: str | None = None
    # RF-FIR-15: SIEMPRE False en este incremento (art. 7 Ley 527/1999, no
    # acreditada ONAC) -- nunca insinúa firma digital cualificada (art. 28).
    acreditado: bool = False
    creada_en: datetime


class TransferenciaActaFirmaPersonalVerif(BaseModel):
    """Bloque por-rol de `GET /acta/verificacion` -- cripto+revocación
    verificadas ON-DEMAND (nunca cacheadas), gemelo del bloque `sello`."""

    rol: Literal["recibe", "entrega", "elabora"]
    firmante_id: uuid.UUID
    # `None` cuando no se pudo verificar (XML firmado no descargable, o
    # signature-service inalcanzable) -- NUNCA se infiere `True` por defecto.
    valida: bool | None = None
    # `vigente`/`revocado`/`no_verificable`/`desconocido` (ver
    # signature-service `VerifyResponse.revocacion_status`) -- `None` si la
    # cripto ya falló (no tiene sentido evaluar revocación) o el canal no
    # respondió.
    revocacion_status: str | None = None


class TransferenciaActaVerificacionResponse(BaseModel):
    """`GET /transferencias/{id}/acta/verificacion` (E17/F4) -- compone la
    verificación del sello institucional (siempre presente si el acta se
    selló) y de cada firma personal presente. `completitud` refleja la
    presencia FACTUAL de las firmas personales de las dos partes del FUID
    (remitente 'entrega' + receptor 'recibe'), que se firman en cualquier
    orden (Inc.2)."""

    sello: TransferenciaActaFirmaInfo
    firmas_personales: list[TransferenciaActaFirmaPersonalVerif]
    # 'sellada' -- solo el sello institucional (gate en 'off', o ninguna firma
    #   personal producida todavía y el gate no la exige).
    # 'firma_personal_pendiente' -- el gate (`acta_firma_personal_require`)
    #   exige firma personal para el nivel_seguridad de este expediente y
    #   NINGUNA parte ha firmado todavía.
    # 'firmada_receptor' -- solo el receptor ('recibe') firmó personalmente.
    # 'firmada_remitente' -- solo el remitente ('entrega') firmó personalmente.
    # 'firmada_completa' -- AMBAS partes firmaron personalmente (bajo el gate
    #   `clasificados`/`todos`, el veredicto de conformidad exige este estado).
    completitud: Literal[
        "sellada",
        "firma_personal_pendiente",
        "firmada_receptor",
        "firmada_remitente",
        "firmada_completa",
    ]
    # Cripto+revocación de TODO lo presente (sello + firmas personales
    # presentes) -- vacuously True si no hay nada que verificar más allá del
    # sello. `False` si el sello no está presente/válido, o si CUALQUIER
    # firma personal presente no valida.
    valida: bool
    acreditado: bool = False
    # Auto-traslado: la MISMA identidad envió y recibió la transferencia
    # (`enviada_por == decidida_por`). Si además `completitud=='firmada_completa'`,
    # ambas firmas personales son de la misma persona -> SIN control dual
    # (separación de funciones). Se expone como OBSERVACIÓN de conformidad
    # (dictamen del auditor archivístico) -- el par de roles del FUID no exige
    # personas naturales distintas, así que no se impide, solo se señala.
    auto_traslado: bool = False
    # Firma personal del ELABORADOR ("Elaborado por" del FUID = quien creó la
    # transferencia, Inc.3). ORTOGONAL a `completitud`: el elaborador es un
    # firmante ADITIVO, no una parte del traspaso, así que su firma NO
    # condiciona `firmada_completa` (entrega+recibe) -- se reporta aparte.
    elaborador_firmado: bool = False

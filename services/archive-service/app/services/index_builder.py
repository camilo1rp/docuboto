# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constructor del índice electrónico (E15, Acuerdo AGN 001/2024).

Genera el XML del índice (namespace urn:orpycamcp:indice:v1) con la cabecera y un
elemento `documento` por radicado vinculado (orden original). Usa la librería
estándar (`xml.etree.ElementTree`) y su canonicalización C14N para una huella
estable; calcula `xml_sha256` (huella del índice) y `content_hash` (hash del
conjunto documento+huella+orden, base de la idempotencia del versionado).

La validación estricta contra XSD (lxml) queda como evolutivo; aquí el XML se
construye con todos los campos normativos disponibles en archive-service.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

NS = "urn:orpycamcp:indice:v1"


@dataclass
class IndiceItemData:
    document_id: str
    orden: int
    titulo: str | None = None
    tipologia: str | None = None
    fecha_archivado: datetime | None = None
    valor_huella: str | None = None
    funcion_resumen: str = "SHA-256"
    formato: str | None = None
    tamano_bytes: int | None = None


@dataclass
class IndiceData:
    expediente_id: str
    code: str
    name: str
    serie_id: str | None
    fecha_creacion: datetime
    items: list[IndiceItemData]


def _sub(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def build_xml(data: IndiceData) -> str:
    root = ET.Element(f"{{{NS}}}indice", attrib={"xsdVersion": "v1"})

    cab = ET.SubElement(root, f"{{{NS}}}cabecera")
    _sub(cab, f"{{{NS}}}identificadorSistema", data.expediente_id)
    _sub(cab, f"{{{NS}}}fechaCreacion", data.fecha_creacion.isoformat())
    exp = ET.SubElement(cab, f"{{{NS}}}expediente")
    _sub(exp, f"{{{NS}}}identificador", data.expediente_id)
    _sub(exp, f"{{{NS}}}numero", data.code)
    _sub(exp, f"{{{NS}}}nombre", data.name)
    _sub(exp, f"{{{NS}}}serie", data.serie_id or "")

    docs = ET.SubElement(root, f"{{{NS}}}documentos")
    for it in data.items:
        d = ET.SubElement(docs, f"{{{NS}}}documento")
        _sub(d, f"{{{NS}}}identificador", it.document_id)
        _sub(d, f"{{{NS}}}titulo", it.titulo or "")
        _sub(d, f"{{{NS}}}tipologia", it.tipologia or "")
        _sub(
            d,
            f"{{{NS}}}fechaArchivado",
            it.fecha_archivado.isoformat() if it.fecha_archivado else "",
        )
        _sub(d, f"{{{NS}}}valorHuella", it.valor_huella or "")
        _sub(d, f"{{{NS}}}funcionResumen", it.funcion_resumen)
        _sub(d, f"{{{NS}}}orden", str(it.orden))

    raw = ET.tostring(root, encoding="unicode")
    # Canonicalización C14N → huella estable independiente de espaciado/atributos.
    return ET.canonicalize(raw)


def xml_sha256(xml: str) -> str:
    return hashlib.sha256(xml.encode("utf-8")).hexdigest()


def content_hash(items: list[IndiceItemData]) -> str:
    """Hash del conjunto (orden, documento, huella) — base de la idempotencia."""
    canon = "\n".join(f"{it.orden}|{it.document_id}|{it.valor_huella or ''}" for it in items)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()

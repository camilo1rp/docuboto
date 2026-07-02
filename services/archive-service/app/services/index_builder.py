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

Genera el XML del índice (namespace urn:orpycamcp:indice:v2, E15 Inc.3) con la
cabecera y un elemento `documento` por radicado vinculado (orden original). Usa
la librería estándar (`xml.etree.ElementTree`) y su canonicalización C14N para
una huella estable; calcula `xml_sha256` (huella del índice) y `content_hash`
(hash del conjunto documento+huella+orden+metadatos RT-15, base de la
idempotencia del versionado).

v2 (E15 Inc.3, RT-15 art. 4.3.2.3) añade formato/tamaño/folios por documento
(opcionales, se omiten si no se declararon al vincular) y renombra
`fechaArchivado` → `fechaIncorporacion`. Es un perfil "ampliado (RT-15
parcial)": quedan fuera orden_documental estable, listaControlAcceso, el
bloque <contexto>, historial de acceso y fecha de declaración (incrementos
futuros) — NO se declara "conforme completo" (RF-FIR-15).

La validación estricta contra XSD (lxml) queda como evolutivo; aquí el XML se
construye con todos los campos normativos disponibles en archive-service.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

NS = "urn:orpycamcp:indice:v2"
XSD_VERSION = "v2"  # única fuente de verdad: atributo XML raíz + fila de BD


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
    folios: int | None = None


@dataclass
class IndiceData:
    expediente_id: str
    code: str
    name: str
    serie_id: str | None
    fecha_creacion: datetime
    items: list[IndiceItemData]


def _sub(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    """Crea el subelemento SIEMPRE (campos core, aunque el valor sea vacío)."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _sub_opt(parent: ET.Element, tag: str, text: str | None) -> None:
    """Crea el subelemento SOLO si `text` no es None (campos RT-15 opcionales,
    v2): un metadato no declarado al vincular se OMITE del XML — no se emite
    vacío, para no sugerir un valor "cero"/"desconocido" declarado."""
    if text is not None:
        ET.SubElement(parent, tag).text = text


def build_xml(data: IndiceData) -> str:
    root = ET.Element(f"{{{NS}}}indice", attrib={"xsdVersion": XSD_VERSION})

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
        # Orden fijo de hijos (la canonicalización C14N depende de él): campos
        # core primero (identificador/titulo/tipologia, siempre presentes),
        # luego los opcionales RT-15 (formato/tamano/folios/fechaIncorporacion,
        # omitidos si no se declararon), y por último huella/función/orden.
        _sub(d, f"{{{NS}}}identificador", it.document_id)
        _sub(d, f"{{{NS}}}titulo", it.titulo or "")
        _sub(d, f"{{{NS}}}tipologia", it.tipologia or "")
        _sub_opt(d, f"{{{NS}}}formato", it.formato)
        _sub_opt(d, f"{{{NS}}}tamano", str(it.tamano_bytes) if it.tamano_bytes is not None else None)
        _sub_opt(d, f"{{{NS}}}folios", str(it.folios) if it.folios is not None else None)
        _sub_opt(
            d,
            f"{{{NS}}}fechaIncorporacion",
            it.fecha_archivado.isoformat() if it.fecha_archivado else None,
        )
        _sub(d, f"{{{NS}}}valorHuella", it.valor_huella or "")
        _sub(d, f"{{{NS}}}funcionResumen", it.funcion_resumen)
        _sub(d, f"{{{NS}}}orden", str(it.orden))

    raw = ET.tostring(root, encoding="unicode")
    # Canonicalización C14N → huella estable independiente de espaciado/atributos.
    return ET.canonicalize(raw)


def xml_sha256(xml: str) -> str:
    return hashlib.sha256(xml.encode("utf-8")).hexdigest()


def _field(value: str) -> str:
    """Prefija cada campo con su longitud en bytes UTF-8 (estilo netstring).

    `formato` es texto libre (hasta 150 chars, aportado por quien vincula) y
    podría contener el delimitador '|' o un salto de línea, desplazando o
    colisionando segmentos del canon (idempotencia únicamente — la
    atestación real es `xml_sha256`/`valor_huella` por documento). El
    length-prefix hace la codificación inyectiva sin necesidad de escapar."""
    encoded = value.encode("utf-8")
    return f"{len(encoded)}:{value}"


def content_hash(items: list[IndiceItemData]) -> str:
    """Hash del conjunto (orden, documento, huella, metadatos RT-15) — base de
    la idempotencia del versionado. Los campos NULL se representan como cadena
    vacía en su posición, para que declarar/corregir un metadato en un
    expediente abierto dispare una versión nueva."""
    canon = "\n".join(
        "|".join(
            _field(v)
            for v in (
                str(it.orden),
                it.document_id,
                it.valor_huella or "",
                it.formato or "",
                str(it.tamano_bytes) if it.tamano_bytes is not None else "",
                str(it.folios) if it.folios is not None else "",
                it.fecha_archivado.isoformat() if it.fecha_archivado else "",
            )
        )
        for it in items
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()

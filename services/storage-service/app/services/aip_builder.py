# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constructor de AIP: empaquetado BagIt + manifiesto PREMIS (E10, OAIS).

Genera, con la librería estándar, el contenido textual del Bag (bagit.txt +
manifest-sha256.txt + bag-info.txt) y un manifiesto PREMIS (objetos con su fijación
SHA-256 + un evento de ingesta). La huella del AIP es el SHA-256 de su contenido
canónico. La escritura WORM y la validación PDF/A son pasos de infraestructura
posteriores; aquí se producen los artefactos verificables.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

_NS = "urn:orpycamcp:premis:v1"


@dataclass
class AipDoc:
    document_id: str
    nombre: str
    valor_huella: str | None


def build_bagit(expediente_id: str, docs: list[AipDoc], generado: datetime) -> str:
    manifest = "\n".join(f"{d.valor_huella or 'PENDIENTE':<64}  data/{d.nombre}" for d in docs)
    bag_info = (
        f"Source-Organization: OrpycaMCP\n"
        f"Bagging-Date: {generado.date().isoformat()}\n"
        f"External-Identifier: {expediente_id}\n"
        f"Payload-Oxum: 0.{len(docs)}\n"
    )
    return (
        "=== bagit.txt ===\n"
        "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
        "=== manifest-sha256.txt ===\n"
        f"{manifest}\n"
        "=== bag-info.txt ===\n"
        f"{bag_info}"
    )


def build_premis(
    expediente_id: str, docs: list[AipDoc], indice_sha256: str | None, generado: datetime
) -> str:
    root = ET.Element(f"{{{_NS}}}premis", attrib={"version": "3.0"})
    for d in docs:
        obj = ET.SubElement(root, f"{{{_NS}}}object")
        ET.SubElement(obj, f"{{{_NS}}}objectIdentifier").text = d.document_id
        fix = ET.SubElement(obj, f"{{{_NS}}}fixity")
        ET.SubElement(fix, f"{{{_NS}}}messageDigestAlgorithm").text = "SHA-256"
        ET.SubElement(fix, f"{{{_NS}}}messageDigest").text = d.valor_huella or ""
    if indice_sha256:
        idx = ET.SubElement(root, f"{{{_NS}}}object", attrib={"tipo": "indice"})
        ET.SubElement(idx, f"{{{_NS}}}objectIdentifier").text = f"indice:{expediente_id}"
        fix = ET.SubElement(idx, f"{{{_NS}}}fixity")
        ET.SubElement(fix, f"{{{_NS}}}messageDigestAlgorithm").text = "SHA-256"
        ET.SubElement(fix, f"{{{_NS}}}messageDigest").text = indice_sha256
    ev = ET.SubElement(root, f"{{{_NS}}}event")
    ET.SubElement(ev, f"{{{_NS}}}eventType").text = "ingest"
    ET.SubElement(ev, f"{{{_NS}}}eventDateTime").text = generado.isoformat()
    ET.SubElement(
        ev, f"{{{_NS}}}eventDetail"
    ).text = f"Empaquetado AIP del expediente {expediente_id}"
    return ET.canonicalize(ET.tostring(root, encoding="unicode"))


def aip_sha256(bagit: str, premis: str) -> str:
    return hashlib.sha256((bagit + premis).encode("utf-8")).hexdigest()

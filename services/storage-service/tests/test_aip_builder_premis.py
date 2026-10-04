# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests unitarios de conformidad PREMIS v3 del builder (E10 Increment C1a).

Prueba directamente ``build_premis_v3``/``validate_premis_xml`` contra el XSD
OFICIAL vendorizado de la Library of Congress (``schemas/premis-v3-loc.xsd``)
— sin pasar por MinIO/HTTP, para aislar la conformidad de emisión XML del
resto del flujo de empaquetado (que ya cubre ``test_aip_real.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from lxml import etree

from app.services.aip_builder import (
    _PREMIS_NS,
    _XSI_NS,
    AipDocMaterial,
    PremisValidationError,
    build_premis_v3,
    validate_premis_xml,
)


def _doc(nombre: str = "oficio.pdf") -> AipDocMaterial:
    return AipDocMaterial(
        document_id="11111111-1111-1111-1111-111111111111",
        nombre=nombre,
        content=b"contenido de prueba",
        sha256="a" * 64,
        mime_type="application/pdf",
    )


def test_build_premis_v3_valida_contra_el_xsd_oficial_de_la_loc() -> None:
    """El PREMIS real emitido por el builder pasa `assertValid` contra el
    schema OFICIAL — no un perfil local propio."""
    xml_text = build_premis_v3(
        expediente_id="EXP-1",
        docs=[_doc()],
        indice=None,
        generado=datetime.now(UTC),
        actor="user-1",
    )
    validate_premis_xml(xml_text)  # no debe lanzar


def test_object_usa_xsi_type_file_sin_elemento_objectcategory() -> None:
    """PREMIS v3 (a diferencia de v2) no tiene elemento `objectCategory`: la
    categoría del objeto se expresa con `xsi:type` sobre `<object>`."""
    xml_text = build_premis_v3(
        expediente_id="EXP-1",
        docs=[_doc()],
        indice=None,
        generado=datetime.now(UTC),
        actor=None,
    )
    root = etree.fromstring(xml_text.encode("utf-8"))
    (obj,) = root.findall(f"{{{_PREMIS_NS}}}object")
    assert obj.get(f"{{{_XSI_NS}}}type") == "premis:file"
    assert obj.find(f"{{{_PREMIS_NS}}}objectCategory") is None


def test_premis_malformado_sin_objectcharacteristics_falla_la_validacion() -> None:
    """`objectCharacteristics` es obligatorio en el complexType `file` del
    schema oficial — un `object` sin él debe seguir siendo rechazado
    (red de seguridad de `assertValid`, no solo bien-formado)."""
    malformed = (
        f'<premis xmlns="{_PREMIS_NS}" xmlns:premis="{_PREMIS_NS}" '
        f'xmlns:xsi="{_XSI_NS}" version="3.0">'
        '<object xsi:type="premis:file">'
        "<objectIdentifier>"
        "<objectIdentifierType>document_id</objectIdentifierType>"
        "<objectIdentifierValue>doc-1</objectIdentifierValue>"
        "</objectIdentifier>"
        "</object>"
        "</premis>"
    )
    with pytest.raises(PremisValidationError):
        validate_premis_xml(malformed)


# AREA-08-05 — formatRegistry/PRONOM declarado.


def _formato(xml_text: str) -> etree._Element:
    root = etree.fromstring(xml_text.encode("utf-8"))
    return root.find(
        f"{{{_PREMIS_NS}}}object/{{{_PREMIS_NS}}}objectCharacteristics/{{{_PREMIS_NS}}}format"
    )


def test_pdf_lleva_formatregistry_pronom_y_valida_contra_el_xsd() -> None:
    xml_text = build_premis_v3(
        expediente_id="EXP-1",
        docs=[_doc()],
        indice=None,
        generado=datetime.now(UTC),
        actor=None,
    )
    validate_premis_xml(xml_text)
    fmt = _formato(xml_text)
    ns = f"{{{_PREMIS_NS}}}"
    assert fmt.findtext(f"{ns}formatRegistry/{ns}formatRegistryName") == "PRONOM"
    assert fmt.findtext(f"{ns}formatRegistry/{ns}formatRegistryKey") == "fmt/276"
    # el PUID es DECLARADO por MIME y el AIP lo dice, no lo presenta como identificado
    assert "declarado" in fmt.findtext(f"{ns}formatNote")
    # orden del XSD: designation, registry, note
    assert [c.tag for c in fmt] == [
        f"{ns}formatDesignation",
        f"{ns}formatRegistry",
        f"{ns}formatNote",
    ]


def test_mime_desconocido_no_recibe_puid_inventado() -> None:
    doc = AipDocMaterial(
        document_id="22222222-2222-2222-2222-222222222222",
        nombre="raro.bin",
        content=b"x",
        sha256="b" * 64,
        mime_type="application/x-formato-que-no-existe",
    )
    xml_text = build_premis_v3(
        expediente_id="EXP-1", docs=[doc], indice=None, generado=datetime.now(UTC), actor=None
    )
    validate_premis_xml(xml_text)
    fmt = _formato(xml_text)
    assert fmt.find(f"{{{_PREMIS_NS}}}formatRegistry") is None


def test_formatregistry_fuera_de_orden_lo_rechaza_el_xsd() -> None:
    """Control del control: si el registro se emitiera ANTES de la designación, el XSD
    oficial lo rechaza (la prueba de arriba no pasa por casualidad)."""
    xml_text = build_premis_v3(
        expediente_id="EXP-1", docs=[_doc()], indice=None, generado=datetime.now(UTC), actor=None
    )
    root = etree.fromstring(xml_text.encode("utf-8"))
    fmt = root.find(
        f"{{{_PREMIS_NS}}}object/{{{_PREMIS_NS}}}objectCharacteristics/{{{_PREMIS_NS}}}format"
    )
    registry = fmt.find(f"{{{_PREMIS_NS}}}formatRegistry")
    fmt.remove(registry)
    fmt.insert(0, registry)
    with pytest.raises(PremisValidationError):
        validate_premis_xml(etree.tostring(root).decode())

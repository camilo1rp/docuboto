# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests unitarios del modo AIP índice-only del builder (E10 AIP wiring
follow-up, expedientes 100% físicos): ``docs == [] and indice is not None``
--> el índice electrónico firmado pasa a ``data/{nombre}`` (PAYLOAD del Bag)
en vez de tag file, ver ``app.services.aip_builder._is_indice_only_mode``.

Prueba directamente ``build_bag``/``build_premis_v3`` (sin pasar por MinIO/
HTTP, mismo aislamiento que ``test_aip_builder_premis.py`` para PREMIS y
``test_aip_real.py`` para el flujo HTTP completo, ver
``test_aip_real_indice_only.py`` para ese lado).

**Invariante clave (RFC 8493): validado con la librería de REFERENCIA
``bagit`` (Python), no solo por inspección de nombres/strings.** Un Bag se
extrae a un directorio temporal REAL (``bagit.Bag`` opera sobre un
directorio, no un ZIP en memoria) y se llama a ``bagit.Bag(path).is_valid()``
— la misma verificación empírica que ``orfeo-architect`` hizo al diseñar este
modo (ver ``.claude/agent-memory/orfeo-architect/
project_aip-indice-only-fisicos-e10.md``): un Bag con ``data/`` vacío
(empty-payload) es RECHAZADO por esa librería con "No manifest files found",
mientras que el índice como único payload es válido.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import bagit
import pytest

from app.services.aip_builder import (
    AipDocMaterial,
    EmptyPayloadBagError,
    build_bag,
    build_premis_v3,
)

TENANT_SLUG = "demo"
_INDICE_CONTENT = b"<indice xmlns='urn:orpycamcp:indice:v1'>firmado, expediente fisico</indice>"


def _indice_material(
    nombre: str = "indice-firmado.xml", content: bytes = _INDICE_CONTENT
) -> AipDocMaterial:
    import hashlib

    return AipDocMaterial(
        document_id="indice:EXP-1",
        nombre=nombre,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/xml",
    )


def _doc_material(nombre: str = "oficio.pdf", content: bytes = b"contenido real") -> AipDocMaterial:
    import hashlib

    return AipDocMaterial(
        document_id="R-1",
        nombre=nombre,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        mime_type="application/pdf",
    )


def _extract_bag(zip_bytes: bytes, dest: Path) -> Path:
    """Extrae el ZIP del Bag a un directorio REAL — ``bagit.Bag`` opera sobre
    un directorio en disco, nunca sobre bytes/ZIP en memoria."""
    with zipfile.ZipFile(zipfile_bytes_io(zip_bytes)) as zf:
        zf.extractall(dest)
    return dest


def zipfile_bytes_io(zip_bytes: bytes):
    import io

    return io.BytesIO(zip_bytes)


# ---------------------------------------------------------------------------
# 1a. Modo índice-only: Bag VÁLIDO por la librería de referencia bagit.
# ---------------------------------------------------------------------------


def test_build_bag_indice_only_is_valid_per_reference_bagit_library(tmp_path: Path) -> None:
    """``docs=[] + indice`` produce un Bag con el índice como ÚNICO payload
    (``data/indice-firmado.xml``) — conforme RFC 8493 según la librería de
    referencia, NUNCA un empty-payload."""
    indice = _indice_material()

    artifact = build_bag(
        expediente_id="EXP-FISICO-1",
        tenant_slug=TENANT_SLUG,
        docs=[],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor="user-1",
    )

    bag_dir = _extract_bag(artifact.zip_bytes, tmp_path / "bag")
    bag = bagit.Bag(str(bag_dir))
    assert bag.is_valid(), "Bag índice-only INVÁLIDO por RFC 8493 según la librería de referencia"

    # --- Estructura: el índice ES el payload, no se duplica como tag file ---
    zf = zipfile.ZipFile(zipfile_bytes_io(artifact.zip_bytes))
    names = set(zf.namelist())
    assert names == {
        "data/indice-firmado.xml",
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
        "tagmanifest-sha256.txt",
        "premis.xml",
    }
    assert zf.read("data/indice-firmado.xml") == _INDICE_CONTENT

    manifest = zf.read("manifest-sha256.txt").decode()
    assert f"{indice.sha256}  data/indice-firmado.xml" in manifest
    assert len(manifest.strip().splitlines()) == 1  # único archivo de payload

    bag_info = zf.read("bag-info.txt").decode()
    assert f"Payload-Oxum: {len(_INDICE_CONTENT)}.1" in bag_info

    assert artifact.total_bytes == len(_INDICE_CONTENT)
    assert artifact.num_files == 1


def test_build_bag_indice_only_tagmanifest_never_lists_indice_as_tag_file(tmp_path: Path) -> None:
    """El nombre fijo ``indice-firmado.xml`` NUNCA aparece en
    ``tagmanifest-sha256.txt`` en modo índice-only — solo existe bajo
    ``data/``, cubierto por ``manifest-sha256.txt`` (payload manifest, que sí
    es un tag file y por tanto SÍ debe listarse en el tagmanifest, RFC 8493
    §2.2.1)."""
    indice = _indice_material()
    artifact = build_bag(
        expediente_id="EXP-FISICO-1",
        tenant_slug=TENANT_SLUG,
        docs=[],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor=None,
    )
    zf = zipfile.ZipFile(zipfile_bytes_io(artifact.zip_bytes))
    tagmanifest = zf.read("tagmanifest-sha256.txt").decode()

    assert "data/indice-firmado.xml" not in tagmanifest  # es payload, no tag file
    assert "\nindice-firmado.xml" not in tagmanifest
    assert not tagmanifest.startswith("indice-firmado.xml")
    assert "manifest-sha256.txt" in tagmanifest  # el manifiesto de payload SÍ es tag file
    assert "premis.xml" in tagmanifest

    _extract_bag(artifact.zip_bytes, tmp_path / "bag")
    bag = bagit.Bag(str(tmp_path / "bag"))
    assert bag.is_valid()


# ---------------------------------------------------------------------------
# 1b. Empty-payload total (docs=[] Y indice=None) -- SIEMPRE rechazado antes
# de intentar construir nada, nunca se emite un Bag inválido.
# ---------------------------------------------------------------------------


def test_build_bag_raises_empty_payload_error_when_docs_and_indice_both_absent() -> None:
    with pytest.raises(EmptyPayloadBagError):
        build_bag(
            expediente_id="EXP-VACIO",
            tenant_slug=TENANT_SLUG,
            docs=[],
            indice=None,
            generado=datetime.now(tz=UTC),
            actor=None,
        )


def test_hypothetical_empty_payload_bag_is_rejected_by_reference_bagit_library(
    tmp_path: Path,
) -> None:
    """Verificación EMPÍRICA (no solo la excepción propia del builder): si se
    construyera a mano un Bag con ``data/`` vacío (bypasseando
    ``build_bag``/``EmptyPayloadBagError``), la librería de referencia
    ``bagit`` lo rechaza — confirma que la excepción del builder no es
    paranoia sino la traducción de una regla RFC 8493 real, verificable
    externamente."""
    from app.services.aip_builder import (
        bag_info_txt,
        bagit_txt,
        build_zip,
        manifest_sha256_txt,
        tagmanifest_sha256_txt,
    )

    now = datetime.now(tz=UTC)
    tag_files = {
        "bagit.txt": bagit_txt(),
        "bag-info.txt": bag_info_txt(
            expediente_id="EXP-VACIO",
            tenant_slug=TENANT_SLUG,
            generado=now,
            total_bytes=0,
            num_files=0,
        ),
        "manifest-sha256.txt": manifest_sha256_txt([]),  # vacío
    }
    files = dict(tag_files)
    files["tagmanifest-sha256.txt"] = tagmanifest_sha256_txt(tag_files)
    zip_bytes = build_zip(files)  # sin entradas 'data/' -- empty-payload a propósito

    bag_dir = _extract_bag(zip_bytes, tmp_path / "empty-bag")
    bag = bagit.Bag(str(bag_dir))
    assert not bag.is_valid()


# ---------------------------------------------------------------------------
# 1c. Modo normal (docs no-vacío) INTACTO -- regresión: índice sigue siendo
# tag file y el Bag sigue siendo bagit-válido.
# ---------------------------------------------------------------------------


def test_build_bag_normal_mode_with_docs_keeps_indice_as_tag_file_and_stays_valid(
    tmp_path: Path,
) -> None:
    doc = _doc_material()
    indice = _indice_material()

    artifact = build_bag(
        expediente_id="EXP-CON-ANEXOS",
        tenant_slug=TENANT_SLUG,
        docs=[doc],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor="user-1",
    )

    zf = zipfile.ZipFile(zipfile_bytes_io(artifact.zip_bytes))
    names = set(zf.namelist())
    assert names == {
        "data/oficio.pdf",
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
        "tagmanifest-sha256.txt",
        "premis.xml",
        "indice-firmado.xml",  # sigue siendo TAG FILE, no payload
    }
    manifest = zf.read("manifest-sha256.txt").decode()
    assert "indice-firmado.xml" not in manifest  # NUNCA en el manifiesto de payload
    assert f"{doc.sha256}  data/oficio.pdf" in manifest

    bag_dir = _extract_bag(artifact.zip_bytes, tmp_path / "bag")
    bag = bagit.Bag(str(bag_dir))
    assert bag.is_valid(), "El modo normal (con anexos) debe seguir siendo bagit-válido"


def test_build_bag_normal_mode_without_indice_still_bagit_valid(tmp_path: Path) -> None:
    """Sin índice en absoluto (``indice=None``, docs no-vacío) -- caso más
    básico, también validado con la librería de referencia (complementa
    ``test_aip_real.py::test_empaquetar_aip_without_indice_excludes_indice_tag_file``,
    que solo inspecciona nombres/strings, no corre `bagit`)."""
    doc = _doc_material()
    artifact = build_bag(
        expediente_id="EXP-SIN-INDICE",
        tenant_slug=TENANT_SLUG,
        docs=[doc],
        indice=None,
        generado=datetime.now(tz=UTC),
        actor=None,
    )
    bag_dir = _extract_bag(artifact.zip_bytes, tmp_path / "bag")
    bag = bagit.Bag(str(bag_dir))
    assert bag.is_valid()


# ---------------------------------------------------------------------------
# 1d. PREMIS: `contentLocation` sigue la ubicación REAL dentro del Bag.
# ---------------------------------------------------------------------------


def test_premis_content_location_is_data_path_in_indice_only_mode() -> None:
    from app.services.aip_builder import _PREMIS_NS

    indice = _indice_material()
    xml_text = build_premis_v3(
        expediente_id="EXP-FISICO-1",
        docs=[],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor=None,
    )
    from lxml import etree

    root = etree.fromstring(xml_text.encode("utf-8"))
    (obj,) = root.findall(f"{{{_PREMIS_NS}}}object")
    loc_value = obj.find(
        f"{{{_PREMIS_NS}}}storage/{{{_PREMIS_NS}}}contentLocation/{{{_PREMIS_NS}}}contentLocationValue"
    )
    assert loc_value is not None
    assert loc_value.text == "data/indice-firmado.xml"


def test_premis_content_location_is_tag_path_in_normal_mode_with_docs() -> None:
    from app.services.aip_builder import _PREMIS_NS

    doc = _doc_material()
    indice = _indice_material()
    xml_text = build_premis_v3(
        expediente_id="EXP-CON-ANEXOS",
        docs=[doc],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor=None,
    )
    from lxml import etree

    root = etree.fromstring(xml_text.encode("utf-8"))
    objects = root.findall(f"{{{_PREMIS_NS}}}object")
    assert len(objects) == 2  # documento + índice

    indice_obj = None
    for obj in objects:
        original_name = obj.find(f"{{{_PREMIS_NS}}}originalName")
        if original_name is not None and original_name.text == "indice-firmado.xml":
            indice_obj = obj
            break
    assert indice_obj is not None
    loc_value = indice_obj.find(
        f"{{{_PREMIS_NS}}}storage/{{{_PREMIS_NS}}}contentLocation/{{{_PREMIS_NS}}}contentLocationValue"
    )
    assert loc_value is not None
    assert loc_value.text == "indice-firmado.xml"  # tag file, NO 'data/...'


def test_premis_indice_only_still_validates_against_official_premis_v3_xsd() -> None:
    """El PREMIS emitido en modo índice-only sigue conforme al XSD OFICIAL de
    la LoC (E10 Increment C1a) -- el cambio de `contentLocation` no rompe la
    conformidad plena."""
    from app.services.aip_builder import validate_premis_xml

    indice = _indice_material()
    xml_text = build_premis_v3(
        expediente_id="EXP-FISICO-1",
        docs=[],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor="user-1",
    )
    validate_premis_xml(xml_text)  # no debe lanzar


# ---------------------------------------------------------------------------
# 1e. Remediación auditoría "media" de conformidad: el PREMIS índice-only es
# honesto por omisión (nunca fabrica un `object` de documento inexistente)
# pero debe ser además AUTO-DESCRIPTIVO -- el evento `ingestion` lleva una
# nota afirmativa explícita de que el expediente es físico/índice-only, para
# que una institución receptora no lo lea como "expediente sin anexos".
# ---------------------------------------------------------------------------


def _ingestion_event_outcome_detail_note(xml_text: str) -> str | None:
    from app.services.aip_builder import _PREMIS_NS

    root = etree_fromstring(xml_text)
    for event in root.findall(f"{{{_PREMIS_NS}}}event"):
        event_type = event.find(f"{{{_PREMIS_NS}}}eventType")
        if event_type is None or event_type.text != "ingestion":
            continue
        note = event.find(
            f"{{{_PREMIS_NS}}}eventOutcomeInformation/"
            f"{{{_PREMIS_NS}}}eventOutcomeDetail/"
            f"{{{_PREMIS_NS}}}eventOutcomeDetailNote"
        )
        return note.text if note is not None else None
    raise AssertionError("evento 'ingestion' no encontrado en el PREMIS")


def etree_fromstring(xml_text: str):
    from lxml import etree

    return etree.fromstring(xml_text.encode("utf-8"))


def test_premis_indice_only_ingestion_event_carries_affirmative_conformance_note() -> None:
    """Modo índice-only: el evento `ingestion` declara AFIRMATIVAMENTE que el
    expediente es físico/índice-only -- no basta con la ausencia de un
    `object` de documento fabricado (honesto por omisión); debe ser legible
    sin ambigüedad por una institución receptora del AIP."""
    from app.services.aip_builder import _INDICE_ONLY_INGESTION_NOTE, validate_premis_xml

    indice = _indice_material()
    xml_text = build_premis_v3(
        expediente_id="EXP-FISICO-1",
        docs=[],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor="user-1",
    )
    validate_premis_xml(xml_text)  # sigue validando contra el XSD oficial

    note = _ingestion_event_outcome_detail_note(xml_text)
    assert note == _INDICE_ONLY_INGESTION_NOTE
    assert "FÍSICOS" in note
    assert "índice" in note.lower()


def test_premis_normal_mode_ingestion_event_has_no_affirmative_note() -> None:
    """Modo normal (con anexos digitales): la nota NO aplica -- el
    expediente sí tiene documentos-objeto reales en el Bag, así que no hay
    nada que aclarar sobre soporte físico. El evento `ingestion` sigue
    validando, simplemente sin `eventOutcomeDetail`."""
    from app.services.aip_builder import validate_premis_xml

    doc = _doc_material()
    indice = _indice_material()
    xml_text = build_premis_v3(
        expediente_id="EXP-CON-ANEXOS",
        docs=[doc],
        indice=indice,
        generado=datetime.now(tz=UTC),
        actor="user-1",
    )
    validate_premis_xml(xml_text)  # el modo normal sigue intacto/válido

    assert _ingestion_event_outcome_detail_note(xml_text) is None

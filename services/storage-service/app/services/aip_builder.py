# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constructor del AIP real: BagIt (RFC 8493) + PREMIS v3 (E10 Increment B, OAIS).

Reemplaza el stub textual del Increment A (un string con secciones
"=== bagit.txt ===", sin bytes reales, sin Bag conforme, PREMIS con un
namespace inventado). Este módulo:

- construye un Bag REAL: ``data/`` con los BYTES de cada documento,
  ``bagit.txt``, ``bag-info.txt``, ``manifest-sha256.txt`` (huella de cada
  archivo de payload) y ``tagmanifest-sha256.txt`` (huella de los propios tag
  files, incluido ``premis.xml``);
- serializa el Bag como un ZIP (biblioteca estándar — más portable/streamable
  hacia MinIO que un tar);
- genera un manifiesto PREMIS v3 con el namespace OFICIAL
  (``http://www.loc.gov/premis/v3``) con entidades OBJECT/EVENT/AGENT,
  validado ESTRUCTURALMENTE contra un perfil LOCAL de PREMIS v3 (no el XSD
  completo de la Library of Congress — ver ``schemas/premis-v3.xsd`` para el
  alcance exacto de ese perfil y el roadmap de vendorización del XSD oficial).

La fixity de cada documento SIEMPRE llega ya recalculada por el llamador
(``AipService`` descarga los bytes de MinIO y contrasta contra ``files.sha256``
ANTES de invocar este módulo) — aquí solo se construyen artefactos a partir de
datos que ya se saben íntegros.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from lxml import etree

_PREMIS_NS = "http://www.loc.gov/premis/v3"
_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "premis-v3.xsd"

_INDICE_TAG_FILENAME = "indice-firmado.xml"
_PREMIS_TAG_FILENAME = "premis.xml"
_BAGIT_FILENAME = "bagit.txt"
_BAG_INFO_FILENAME = "bag-info.txt"
_MANIFEST_FILENAME = "manifest-sha256.txt"
_TAGMANIFEST_FILENAME = "tagmanifest-sha256.txt"

# Memoización perezosa del XSD compilado (parsear el schema en cada AIP sería
# desperdiciado) — mismo patrón de singleton de módulo que `minio_client.py`.
_schema_cache: etree.XMLSchema | None = None


class PremisValidationError(Exception):
    """El PREMIS generado no valida contra ``schemas/premis-v3.xsd``.

    No debería ocurrir en producción (la estructura la arma este mismo
    módulo) — es una red de seguridad ante una regresión futura, para NUNCA
    subir al bucket WORM un AIP con un PDI (metadato de preservación)
    inválido.
    """


@dataclass(slots=True)
class AipDocMaterial:
    """Un documento con sus BYTES reales y su fixity ya recalculada/verificada.

    ``sha256`` es el hash RECALCULADO sobre ``content`` por el llamador — nunca
    un valor confiado ciegamente del request (ver `AipService`).
    """

    document_id: str
    nombre: str
    content: bytes
    sha256: str
    mime_type: str | None = None


@dataclass(slots=True)
class BagArtifact:
    """Resultado de empaquetar el Bag: los bytes del ZIP + metadatos derivados."""

    zip_bytes: bytes
    total_bytes: int
    num_files: int
    premis_xml: str


def _schema() -> etree.XMLSchema:
    # Se compila una vez por proceso (import-time sería prematuro en tests que
    # no tocan este módulo) y se memoiza en el módulo para no pagar el costo
    # de parseo del XSD en cada AIP.
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = etree.XMLSchema(etree.parse(str(_SCHEMA_PATH)))
    return _schema_cache


def validate_premis_xml(xml_text: str) -> None:
    """Valida ``xml_text`` contra el XSD local de PREMIS v3.

    Levanta :class:`PremisValidationError` con el detalle del esquema si no
    valida — nunca deja pasar un PREMIS inválido hacia el Bag/WORM.
    """
    try:
        doc = etree.fromstring(xml_text.encode("utf-8"))
        _schema().assertValid(doc)
    except etree.XMLSyntaxError as exc:
        raise PremisValidationError(f"PREMIS mal formado: {exc}") from exc
    except etree.DocumentInvalid as exc:
        raise PremisValidationError(f"PREMIS no conforme al XSD v3: {exc}") from exc


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{_PREMIS_NS}}}{tag}")
    if text is not None:
        el.text = text
    return el


def _build_object_element(
    root: etree._Element,
    *,
    identifier_type: str,
    identifier_value: str,
    sha256: str,
    size: int,
    mime_type: str | None,
    original_name: str,
    content_location: str,
) -> None:
    obj = _sub(root, "object")
    oid = _sub(obj, "objectIdentifier")
    _sub(oid, "objectIdentifierType", identifier_type)
    _sub(oid, "objectIdentifierValue", identifier_value)
    _sub(obj, "objectCategory", "file")
    chars = _sub(obj, "objectCharacteristics")
    _sub(chars, "compositionLevel", "0")
    fixity = _sub(chars, "fixity")
    _sub(fixity, "messageDigestAlgorithm", "SHA-256")
    _sub(fixity, "messageDigest", sha256)
    _sub(chars, "size", str(size))
    fmt = _sub(chars, "format")
    designation = _sub(fmt, "formatDesignation")
    _sub(designation, "formatName", mime_type or "application/octet-stream")
    _sub(obj, "originalName", original_name)
    storage = _sub(obj, "storage")
    loc = _sub(storage, "contentLocation")
    _sub(loc, "contentLocationType", "bag-path")
    _sub(loc, "contentLocationValue", content_location)


def _build_event_element(
    root: etree._Element,
    *,
    event_id: str,
    event_type: str,
    generado: datetime,
    detail: str,
    outcome: Literal["success", "failure"],
    linked_object_ids: list[str],
) -> None:
    ev = _sub(root, "event")
    eid = _sub(ev, "eventIdentifier")
    _sub(eid, "eventIdentifierType", "UUID")
    _sub(eid, "eventIdentifierValue", event_id)
    _sub(ev, "eventType", event_type)
    _sub(ev, "eventDateTime", generado.isoformat())
    detail_info = _sub(ev, "eventDetailInformation")
    _sub(detail_info, "eventDetail", detail)
    outcome_info = _sub(ev, "eventOutcomeInformation")
    _sub(outcome_info, "eventOutcome", outcome)
    linking_agent = _sub(ev, "linkingAgentIdentifier")
    _sub(linking_agent, "linkingAgentIdentifierType", "software")
    _sub(linking_agent, "linkingAgentIdentifierValue", "OrpycaMCP-storage-service")
    for object_id in linked_object_ids:
        linking_obj = _sub(ev, "linkingObjectIdentifier")
        _sub(linking_obj, "linkingObjectIdentifierType", "document_id")
        _sub(linking_obj, "linkingObjectIdentifierValue", object_id)


def _build_agent_element(
    root: etree._Element,
    *,
    identifier_type: str,
    identifier_value: str,
    name: str | None,
    agent_type: str,
) -> None:
    agent = _sub(root, "agent")
    aid = _sub(agent, "agentIdentifier")
    _sub(aid, "agentIdentifierType", identifier_type)
    _sub(aid, "agentIdentifierValue", identifier_value)
    if name is not None:
        _sub(agent, "agentName", name)
    _sub(agent, "agentType", agent_type)


def build_premis_v3(
    *,
    expediente_id: str,
    docs: list[AipDocMaterial],
    indice: AipDocMaterial | None,
    generado: datetime,
    actor: str | None,
    fixity_outcome: Literal["success", "failure"] = "success",
) -> str:
    """Construye el manifiesto PREMIS v3 (objetos + eventos + agentes).

    Entidades:
      - OBJECT: uno por documento (y uno más si se aporta ``indice``), con
        fixity SHA-256 recalculada, tamaño real y ubicación dentro del Bag.
      - EVENT: un evento ``ingestion`` (empaquetado del AIP) y un evento
        ``fixity check`` por objeto, con ``eventOutcome`` derivado del
        contraste real hecho por el llamador (``fixity_outcome`` — en la
        práctica siempre "success" aquí, porque un mismatch aborta el
        empaquetado ANTES de llegar a este builder; el parámetro existe para
        que el vocabulario de outcomes sea comprobable con un test directo).
      - AGENT: el software (OrpycaMCP storage-service) y, si se conoce, el
        actor humano/sistema de la petición.
    """
    root = etree.Element(f"{{{_PREMIS_NS}}}premis", attrib={"version": "3.0"}, nsmap={None: _PREMIS_NS})

    linked_ids: list[str] = []
    for doc in docs:
        _build_object_element(
            root,
            identifier_type="document_id",
            identifier_value=doc.document_id,
            sha256=doc.sha256,
            size=len(doc.content),
            mime_type=doc.mime_type,
            original_name=doc.nombre,
            content_location=f"data/{doc.nombre}",
        )
        linked_ids.append(doc.document_id)

    if indice is not None:
        _build_object_element(
            root,
            identifier_type="document_id",
            identifier_value=f"indice:{expediente_id}",
            sha256=indice.sha256,
            size=len(indice.content),
            mime_type=indice.mime_type or "application/xml",
            original_name=indice.nombre,
            content_location=f"{_INDICE_TAG_FILENAME}",
        )
        linked_ids.append(f"indice:{expediente_id}")

    _build_event_element(
        root,
        event_id=f"ingest:{expediente_id}",
        event_type="ingestion",
        generado=generado,
        detail=f"Empaquetado AIP del expediente {expediente_id}",
        outcome="success",
        linked_object_ids=linked_ids,
    )
    _build_event_element(
        root,
        event_id=f"fixity:{expediente_id}",
        event_type="fixity check",
        generado=generado,
        detail=f"Verificación de fijación SHA-256 del expediente {expediente_id}",
        outcome=fixity_outcome,
        linked_object_ids=linked_ids,
    )

    _build_agent_element(
        root,
        identifier_type="software",
        identifier_value="OrpycaMCP-storage-service",
        name="OrpycaMCP storage-service",
        agent_type="software",
    )
    if actor:
        _build_agent_element(
            root,
            identifier_type="user_id",
            identifier_value=actor,
            name=None,
            agent_type="person",
        )

    xml_text = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    ).decode("utf-8")
    return xml_text


def bagit_txt() -> bytes:
    return b"BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"


def bag_info_txt(
    *, expediente_id: str, tenant_slug: str, generado: datetime, total_bytes: int, num_files: int
) -> bytes:
    text = (
        f"Source-Organization: OrpycaMCP ({tenant_slug})\n"
        f"Bagging-Date: {generado.date().isoformat()}\n"
        f"External-Identifier: {expediente_id}\n"
        f"Payload-Oxum: {total_bytes}.{num_files}\n"
    )
    return text.encode("utf-8")


def _bagpath_encode(path: str) -> str:
    """Percent-encode CR, LF y ``%`` en una ruta de manifiesto (RFC 8493 §2.1.3).

    Se aplica SOLO al texto de la línea del manifiesto/tagmanifest — nunca al
    nombre de entrada real dentro del ZIP (el Bag en disco puede contener
    cualquier byte; lo que debe seguir siendo parseable línea-por-línea es el
    archivo de manifiesto). ``%`` se codifica PRIMERO para no re-escapar los
    ``%`` introducidos por la codificación de CR/LF.
    """
    return path.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def manifest_sha256_txt(docs: list[AipDocMaterial]) -> bytes:
    lines = [f"{doc.sha256}  {_bagpath_encode(f'data/{doc.nombre}')}" for doc in docs]
    return ("\n".join(lines) + "\n" if lines else "").encode("utf-8")


def tagmanifest_sha256_txt(tag_files: dict[str, bytes]) -> bytes:
    lines = [
        f"{hashlib.sha256(content).hexdigest()}  {_bagpath_encode(name)}"
        for name, content in sorted(tag_files.items())
    ]
    return ("\n".join(lines) + "\n" if lines else "").encode("utf-8")


def build_zip(files: dict[str, bytes]) -> bytes:
    """Serializa el Bag como ZIP (orden determinista de entradas)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            zf.writestr(name, files[name])
    return buf.getvalue()


def aip_sha256(zip_bytes: bytes) -> str:
    """Huella del AIP: SHA-256 del ZIP completo serializado.

    Cambio de significado deliberado respecto al stub del Increment A (que
    hasheaba dos strings textuales sintéticos): ahora es la huella del
    artefacto binario real que se sube al bucket WORM — verificable
    descargando el objeto y recalculando.
    """
    return hashlib.sha256(zip_bytes).hexdigest()


def build_bag(
    *,
    expediente_id: str,
    tenant_slug: str,
    docs: list[AipDocMaterial],
    indice: AipDocMaterial | None,
    generado: datetime,
    actor: str | None,
    fixity_outcome: Literal["success", "failure"] = "success",
) -> BagArtifact:
    """Arma el Bag REAL (RFC 8493) completo y lo serializa como ZIP.

    Estructura:
      data/{nombre}              — bytes de cada documento (payload)
      bagit.txt                  — declaración de versión BagIt
      bag-info.txt                — Payload-Oxum real, fecha, organización
      manifest-sha256.txt        — fixity de cada archivo de payload
      premis.xml                  — PDI: manifiesto PREMIS v3 (tag file)
      indice-firmado.xml         — PDI opcional: índice electrónico firmado
      tagmanifest-sha256.txt     — fixity de los tag files anteriores (incluido
                                    el propio manifest-sha256.txt, RFC 8493
                                    §2.2.1: el manifiesto de payload ES UN tag
                                    file)

    Lanza :class:`PremisValidationError` si el PREMIS generado no valida
    contra el XSD local — el Bag NUNCA se serializa con un PDI inválido.
    """
    premis_xml = build_premis_v3(
        expediente_id=expediente_id,
        docs=docs,
        indice=indice,
        generado=generado,
        actor=actor,
        fixity_outcome=fixity_outcome,
    )
    validate_premis_xml(premis_xml)

    files: dict[str, bytes] = {}
    for doc in docs:
        files[f"data/{doc.nombre}"] = doc.content

    # Payload-Oxum/num_files se derivan del conjunto REAL de entradas `data/`
    # ya escritas en el Bag, no de `len(docs)`/`sum(len(d.content) ...)` — la
    # capa Pydantic (`AipCreate._check_unique_nombres`) ya rechaza nombres
    # duplicados antes de llegar aquí, pero de ocurrir una colisión (llamada
    # directa a este builder, fuera del router) el conteo nunca se infla por
    # encima de lo que el ZIP contiene de verdad (hallazgo de auditoría
    # Increment B).
    total_bytes = sum(len(content) for content in files.values())
    num_files = len(files)

    manifest_bytes = manifest_sha256_txt(docs)

    tag_files: dict[str, bytes] = {
        _BAGIT_FILENAME: bagit_txt(),
        _BAG_INFO_FILENAME: bag_info_txt(
            expediente_id=expediente_id,
            tenant_slug=tenant_slug,
            generado=generado,
            total_bytes=total_bytes,
            num_files=num_files,
        ),
        _PREMIS_TAG_FILENAME: premis_xml.encode("utf-8"),
        # Se incluye AQUÍ (antes de calcular el tagmanifest) porque el
        # manifiesto de payload es en sí mismo un tag file que debe quedar
        # cubierto por `tagmanifest-sha256.txt` (RFC 8493 §2.2.1).
        _MANIFEST_FILENAME: manifest_bytes,
    }
    if indice is not None:
        tag_files[_INDICE_TAG_FILENAME] = indice.content

    tagmanifest_bytes = tagmanifest_sha256_txt(tag_files)

    files.update(tag_files)
    files[_TAGMANIFEST_FILENAME] = tagmanifest_bytes

    zip_bytes = build_zip(files)
    return BagArtifact(
        zip_bytes=zip_bytes,
        total_bytes=total_bytes,
        num_files=num_files,
        premis_xml=premis_xml,
    )

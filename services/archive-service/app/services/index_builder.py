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

Genera el XML del índice (namespace urn:orpycamcp:indice:v3, E15 finalización
RT-15) con la cabecera y un elemento `documento` por radicado vinculado
(orden documental original). Usa la librería estándar
(`xml.etree.ElementTree`) y su canonicalización C14N para una huella estable;
calcula `xml_sha256` (huella del índice) y `content_hash` (hash del conjunto
documento+huella+orden+metadatos RT-15, base de la idempotencia del
versionado).

v2 (E15 Inc.3, RT-15 art. 4.3.2.3) añadió formato/tamaño/folios por documento
(opcionales, se omiten si no se declararon al vincular) y renombró
`fechaArchivado` → `fechaIncorporacion`.

v3 (E15 finalización RT-15, RF-FIR-15) es el perfil "CONFORME EXTENDIDO":
añade `orden_documental` ESTABLE (asignado una sola vez al vincular —
`MAX+1` por expediente, inmutable, NUNCA se renumera ni se reutiliza),
`<listaControlAcceso>` por documento (nivel de seguridad heredado del
expediente) y el bloque `<contexto>` de cabecera (serie/subserie por nombre +
fechaPrimerUso). Un documento excluido del expediente (soft-delete, ver
`app.repositories.archive_repository.unlink_radicado`) se emite como
`<documento excluido="true">` conservando su `orden_documental` original +
`<fechaExclusion>`/`<causalExclusion>`, para que el HUECO en el orden quede
trazable en vez de renumerarse o desaparecer; `num_documentos` (ver
`count_vigentes`) cuenta solo los vinculados.

Este incremento NO alcanza "conforme completo" (RF-FIR-15, crítico): quedan 4
residuales SIN fuente de datos en archive-service, que NO deben declararse
resueltos en ningún lado:
  1. historial de acceso por documento — no existe un log de accesos por-doc.
  2. fecha de declaración — la columna existe desde 008_expediente_indice.sql
     pero no hay fuente que la pueble.
  3. control de acceso por roles/dependencias — vive en auth-service, no
     integrado aquí; solo se emite el NIVEL de seguridad del expediente.
  4. dependencia productora — no existe el campo en el modelo `Expediente`.

La validación estricta contra XSD (lxml) queda como evolutivo; aquí el XML se
construye con todos los campos normativos disponibles en archive-service.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

NS = "urn:orpycamcp:indice:v3"
XSD_VERSION = "v3"  # única fuente de verdad: atributo XML raíz + fila de BD

# Escala de `expedientes.nivel_seguridad` (013_expediente_security_level.sql;
# security_levels.code en auth-service): 1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA.
_NIVEL_SEGURIDAD_LABELS = {1: "PUBLICA", 2: "RESERVADA", 3: "CLASIFICADA"}


@dataclass
class IndiceItemData:
    document_id: str
    # v3: orden_documental ESTABLE del vínculo (ya NO un enumerate temporal —
    # ver `IndexService._items_from`), inmutable desde que se vinculó.
    orden: int
    titulo: str | None = None
    tipologia: str | None = None
    fecha_archivado: datetime | None = None
    valor_huella: str | None = None
    funcion_resumen: str = "SHA-256"
    formato: str | None = None
    tamano_bytes: int | None = None
    folios: int | None = None
    # v3 (E15 finalización RT-15): nivel heredado del expediente
    # (<listaControlAcceso>) y estado del vínculo ('vinculado'/'excluido').
    # Un excluido se emite con el atributo `excluido="true"` + fecha/causal.
    nivel_seguridad: int = 1
    estado: str = "vinculado"
    fecha_exclusion: datetime | None = None
    causal_exclusion: str | None = None


@dataclass
class IndiceData:
    expediente_id: str
    code: str
    name: str
    serie_id: str | None
    fecha_creacion: datetime
    items: list[IndiceItemData]
    # v3: bloque <contexto> de cabecera (RT-15 art. 4.3.2.3). `serie_nombre`/
    # `subserie_nombre` son los NOMBRES legibles resueltos por TRD (el UUID
    # sigue emitiéndose aparte en <expediente><serie> como identificador);
    # `fecha_primer_uso` es la fecha de la primera incorporación vigente.
    # Todos None-ables: se omiten del XML si no hay dato (expediente sin serie
    # TRD asignada, o sin documentos vinculados aún).
    serie_nombre: str | None = None
    subserie_nombre: str | None = None
    fecha_primer_uso: datetime | None = None


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
    # v3: <contexto> — nombres de serie/subserie (el UUID va arriba, como
    # identificador) + fecha de primera incorporación vigente. Se omite por
    # completo si no hay ningún dato (expediente sin TRD asignada y sin
    # documentos vinculados aún) — no sugerir un contexto vacío declarado.
    if data.serie_nombre is not None or data.subserie_nombre is not None or data.fecha_primer_uso is not None:
        ctx = ET.SubElement(cab, f"{{{NS}}}contexto")
        _sub_opt(ctx, f"{{{NS}}}serie", data.serie_nombre)
        _sub_opt(ctx, f"{{{NS}}}subserie", data.subserie_nombre)
        _sub_opt(
            ctx,
            f"{{{NS}}}fechaPrimerUso",
            data.fecha_primer_uso.isoformat() if data.fecha_primer_uso else None,
        )

    docs = ET.SubElement(root, f"{{{NS}}}documentos")
    # orden_documental ESTABLE decide la posición de emisión (vinculados y
    # excluidos intercalados por su ordinal) — nunca se renumera/comprime el
    # hueco de un excluido, aunque el llamador ya entregue `data.items`
    # pre-ordenado (ver `ArchiveRepository._get_radicados`).
    for it in sorted(data.items, key=lambda x: x.orden):
        excluido = it.estado == "excluido"
        d = ET.SubElement(docs, f"{{{NS}}}documento", attrib={"excluido": "true"} if excluido else {})
        # Orden fijo de hijos (la canonicalización C14N depende de él): campos
        # core primero (identificador/titulo/tipologia, siempre presentes),
        # luego los opcionales RT-15 (formato/tamano/folios/fechaIncorporacion,
        # omitidos si no se declararon), orden/exclusión, y por último el
        # control de acceso (v3).
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
        if excluido:
            _sub_opt(
                d,
                f"{{{NS}}}fechaExclusion",
                it.fecha_exclusion.isoformat() if it.fecha_exclusion else None,
            )
            _sub_opt(d, f"{{{NS}}}causalExclusion", it.causal_exclusion)
        lca = ET.SubElement(d, f"{{{NS}}}listaControlAcceso")
        nivel_el = _sub(
            lca,
            f"{{{NS}}}nivelSeguridad",
            _NIVEL_SEGURIDAD_LABELS.get(it.nivel_seguridad, "DESCONOCIDA"),
        )
        nivel_el.set("codigo", str(it.nivel_seguridad))

    raw = ET.tostring(root, encoding="unicode")
    # Canonicalización C14N → huella estable independiente de espaciado/atributos.
    return ET.canonicalize(raw)


def count_vigentes(items: list[IndiceItemData]) -> int:
    """Cuenta solo los documentos VINCULADOS (vigentes).

    Los excluidos (soft-delete) permanecen en el XML como hueco trazable —
    `<documento excluido="true">` — pero no deben inflar `num_documentos`,
    que representa el conjunto documental VIGENTE del expediente.
    """
    return sum(1 for it in items if it.estado != "excluido")


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
    """Hash del conjunto (orden, documento, huella, metadatos RT-15 + v3) —
    base de la idempotencia del versionado. Los campos NULL se representan
    como cadena vacía en su posición, para que declarar/corregir un metadato
    en un expediente abierto dispare una versión nueva. v3 incorpora
    `nivel_seguridad` y `estado` al canon: reclasificar el expediente o
    excluir/re-vincular un radicado (sin que cambie ningún otro campo)
    también debe disparar version+1."""
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
                str(it.nivel_seguridad),
                it.estado,
            )
        )
        for it in items
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()

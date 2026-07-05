# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constructor del índice electrónico (E15, Acuerdo AGN 001/2024) — perfil v4
"CONFORME COMPLETO" (a nivel de CAPACIDAD; la completitud real es per-instancia,
ver `<conformidad>` más abajo).

Genera el XML del índice (namespace urn:orpycamcp:indice:v4) con la cabecera y
un elemento `documento` por radicado vinculado (orden documental original). Usa
la librería estándar (`xml.etree.ElementTree`) y su canonicalización C14N para
una huella estable; calcula `xml_sha256` (huella del índice) y `content_hash`.

PRINCIPIO RECTOR (v4): `content_hash` cubre TODO el documento canónico —
literalmente `sha256(ET.canonicalize(xml_completo))`, igual que `xml_sha256`.
A diferencia de v1-v3 (una concatenación de campos SOLO por-documento, que por
construcción no podía representar campos de CABECERA como `pistaAuditoria`/
`politicaAcceso`/`conformidad`), v4 hashea el árbol completo — cualquier
elemento nuevo entra automáticamente al hash de idempotencia sin tener que
enumerarlo a mano. Consecuencia (mejora): el hash ya NO depende del ORDEN DE
ENTRADA de `items` en la lista Python — `build_xml` siempre serializa por
`orden_documental` (el ordinal estable), así que dos regeneraciones con el
mismo conjunto documental producen el mismo XML sin importar en qué orden
llegaron los objetos `IndiceItemData`.

Por el mismo principio: SOLO entra al XML lo ESTABLE-por-expediente; lo
mutable/operacional (matriz RBAC viva, entradas de auditoría en crecimiento) se
ATESTA (una referencia fija, no su contenido) o se expone por ENDPOINT fuera de
la firma (`GET /roles-autorizados`, `GET /historial-acceso`).

Historial de perfiles:
  v2 (E15 Inc.3, RT-15 art. 4.3.2.3): formato/tamaño/folios por documento +
    `fechaIncorporacion` (renombre de `fechaArchivado`).
  v3 (E15 finalización RT-15, "CONFORME EXTENDIDO"): `orden_documental` estable
    e inmutable, `<documento excluido="true">` (soft-delete trazable),
    `<contexto>` de cabecera (serie/subserie + fechaPrimerUso).
  v4 (este perfil, "CONFORME COMPLETO" — cierra los 4 residuales de v3):
    1. Historial de acceso: YA NO se incrusta (creado/movido a Diseño B, ver
       abajo) — se ATESTA con `<pistaAuditoria>` (referencia estable a
       `public.audit_log`, sin contenido mutable) y se consulta por endpoint
       (`GET /expedientes/{id}/historial-acceso`).
    2. Fecha de declaración: `<fechaDeclaracion>` por documento, derivada de
       `fecha_archivado`/`added_at` (equivalencia declaracion==incorporacion;
       Orfeo SGD_EXP_FECH vs fech_radi) — NO una columna nueva que poblar.
    3. Control de acceso por roles: `<politicaAcceso>` (modelo + permiso +
       clearance mínimo, ESTABLE) entra al índice; la matriz RBAC VIVA
       (qué grupos concretos, que cambia con el tiempo) se mueve FUERA de la
       firma (`GET /expedientes/{id}/roles-autorizados`) — corrección del
       arquitecto sobre el borrador inicial de v4 (no incrustar la matriz viva).
    4. Dependencia productora: `<dependenciaProductora>` por documento (grano
       por-radicado, aportada al vincular — Opción A, sin llamada HTTP a
       document-service), solo quyo AMBOS `codigo`/`nombre` están declarados.

  `<listaControlAcceso>` (nivelSeguridad + politicaAcceso) se MUEVE de
  por-documento (v3, duplicado N veces sin necesidad — el nivel es del
  EXPEDIENTE, no del documento) a la CABECERA (una sola vez).

  `<conformidad perfil="v4" productoraPresente="…" pistaAccesoDesde="…"/>`:
  marcador per-instancia HONESTO (RF-FIR-15, no sobre-declarar) — v4 es
  "conforme completo" a nivel de CAPACIDAD del sistema, pero la completitud
  REAL de un expediente concreto depende de si TODOS sus documentos vigentes
  declararon productora (expedientes creados antes de este incremento pueden
  no tenerla) y desde cuándo existe pista de acceso verificable para él.

La validación estricta contra XSD (lxml) queda como evolutivo; aquí el XML se
construye con todos los campos normativos disponibles en archive-service.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

NS = "urn:orpycamcp:indice:v4"
XSD_VERSION = "v4"  # única fuente de verdad: atributo XML raíz + fila de BD

# Escala de `expedientes.nivel_seguridad` (013_expediente_security_level.sql;
# security_levels.code en auth-service): 1=PUBLICA, 2=RESERVADA, 3=CLASIFICADA.
_NIVEL_SEGURIDAD_LABELS = {1: "PUBLICA", 2: "RESERVADA", 3: "CLASIFICADA"}

# Fecha en que se instrumentó el historial de acceso (R3-a, `audit.append` en
# el éxito de las lecturas de expediente/índice) — antes de esta fecha no
# existe pista de acceso verificable para NINGÚN expediente, sin importar
# cuándo se creó. `<conformidad pistaAccesoDesde="…">` usa el MAYOR entre esta
# constante y la fecha de creación del expediente (ver `IndexService._data`) —
# nunca `datetime.now()` en tiempo de ejecución del builder (debe ser
# determinista para no romper la idempotencia del content_hash).
PISTA_ACCESO_INSTRUMENTACION_DATE = date(2026, 7, 5)


@dataclass
class IndiceItemData:
    document_id: str
    # orden_documental ESTABLE del vínculo (v3), inmutable desde que se vinculó.
    orden: int
    titulo: str | None = None
    tipologia: str | None = None
    fecha_archivado: datetime | None = None
    valor_huella: str | None = None
    funcion_resumen: str = "SHA-256"
    formato: str | None = None
    tamano_bytes: int | None = None
    folios: int | None = None
    # Snapshot del nivel heredado del expediente y del estado del vínculo — ya
    # NO se emiten como `<listaControlAcceso>` POR DOCUMENTO en v4 (movido a
    # cabecera, una sola vez); se conservan aquí para el snapshot en BD
    # (`expediente_indice_item.nivel_seguridad_snapshot`/`estado`) y para
    # decidir el atributo `excluido="true"`.
    nivel_seguridad: int = 1
    estado: str = "vinculado"
    fecha_exclusion: datetime | None = None
    causal_exclusion: str | None = None
    # v4 (R1): dependencia productora del documento, aportada al vincular
    # (grano por-radicado, Opción A). Solo se emite `<dependenciaProductora>`
    # cuando AMBOS están presentes — sin centinela para filas legacy.
    dependencia_productora_codigo: int | None = None
    dependencia_productora_nombre: str | None = None


@dataclass
class IndiceData:
    expediente_id: str
    code: str
    name: str
    serie_id: str | None
    fecha_creacion: datetime
    items: list[IndiceItemData]
    # <contexto> de cabecera (RT-15 art. 4.3.2.3, v3): nombres de serie/
    # subserie (resueltos por TRD) + fecha de primera incorporación vigente.
    serie_nombre: str | None = None
    subserie_nombre: str | None = None
    fecha_primer_uso: datetime | None = None
    # v4: control de acceso a nivel de EXPEDIENTE (antes por-documento, v3) —
    # `clearanceMinimo` de `<politicaAcceso>` reutiliza este MISMO valor (no
    # una lectura/derivación aparte, ver contrato R4).
    nivel_seguridad: int = 1
    # v4 (R1 + marcador de conformidad): true solo si HAY AL MENOS UN
    # documento VIGENTE y TODOS declararon dependencia productora — false
    # (no vacuamente true) cuando el expediente no tiene ningún vigente
    # (versión 0 / todos excluidos), ver `IndexService._data`.
    productora_presente: bool = False
    # v4 (R3 + marcador de conformidad): fecha desde la que hay pista de
    # acceso verificable para este expediente (ver `PISTA_ACCESO_INSTRUMENTACION_DATE`).
    pista_acceso_desde: date = field(default_factory=lambda: PISTA_ACCESO_INSTRUMENTACION_DATE)


def _sub(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    """Crea el subelemento SIEMPRE (campos core, aunque el valor sea vacío)."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _sub_opt(parent: ET.Element, tag: str, text: str | None) -> None:
    """Crea el subelemento SOLO si `text` no es None (campos opcionales: un
    metadato no declarado al vincular se OMITE del XML — no se emite vacío,
    para no sugerir un valor "cero"/"desconocido" declarado)."""
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
    # <contexto> (v3): nombres de serie/subserie (el UUID va arriba, como
    # identificador) + fecha de primera incorporación vigente. Se omite por
    # completo si no hay ningún dato disponible.
    if data.serie_nombre is not None or data.subserie_nombre is not None or data.fecha_primer_uso is not None:
        ctx = ET.SubElement(cab, f"{{{NS}}}contexto")
        _sub_opt(ctx, f"{{{NS}}}serie", data.serie_nombre)
        _sub_opt(ctx, f"{{{NS}}}subserie", data.subserie_nombre)
        _sub_opt(
            ctx,
            f"{{{NS}}}fechaPrimerUso",
            data.fecha_primer_uso.isoformat() if data.fecha_primer_uso else None,
        )

    # v4: control de acceso a nivel de EXPEDIENTE (antes por-documento, v3) —
    # nivelSeguridad + politicaAcceso ESTABLE (modelo/permiso/clearance mínimo).
    # La matriz RBAC VIVA (qué grupos concretos) NO se incrusta — corrección
    # del arquitecto — se consulta por `GET /roles-autorizados` (fuera de la firma).
    lca = ET.SubElement(cab, f"{{{NS}}}listaControlAcceso")
    nivel_el = _sub(
        lca, f"{{{NS}}}nivelSeguridad", _NIVEL_SEGURIDAD_LABELS.get(data.nivel_seguridad, "DESCONOCIDA")
    )
    nivel_el.set("codigo", str(data.nivel_seguridad))
    politica_el = ET.SubElement(lca, f"{{{NS}}}politicaAcceso")
    politica_el.set("modelo", "no-read-up")
    politica_el.set("requiere", "USUA_PERM_EXPEDIENTE")
    # MISMA fuente que nivelSeguridad (no se deriva aparte, contrato R4).
    politica_el.set("clearanceMinimo", str(data.nivel_seguridad))

    # v4 (R3): atestación ESTABLE del historial de acceso — una referencia fija
    # a dónde verificarlo (public.audit_log), NUNCA su contenido mutable (sin
    # timestamps/conteos/anclas por-generación: rompería la idempotencia del
    # content_hash y, peor, incrustaría un dato que sigue creciendo).
    pista_el = ET.SubElement(cab, f"{{{NS}}}pistaAuditoria")
    pista_el.set("disponible", "true")
    pista_el.set("registro", "public.audit_log")
    pista_el.set("objetoTipo", "expediente")
    pista_el.set("objetoRef", data.code)
    pista_el.set("cadenaVerificable", "true")

    # v4: marcador de conformidad PER-INSTANCIA (RF-FIR-15, no sobre-declarar).
    conformidad_el = ET.SubElement(cab, f"{{{NS}}}conformidad")
    conformidad_el.set("perfil", "v4")
    conformidad_el.set("productoraPresente", "true" if data.productora_presente else "false")
    conformidad_el.set("pistaAccesoDesde", data.pista_acceso_desde.isoformat())

    docs = ET.SubElement(root, f"{{{NS}}}documentos")
    # orden_documental ESTABLE decide la posición de emisión (vinculados y
    # excluidos intercalados por su ordinal) — nunca se renumera/comprime el
    # hueco de un excluido, con independencia del orden en que `data.items`
    # llegue a esta función (ver PRINCIPIO RECTOR del content_hash).
    for it in sorted(data.items, key=lambda x: x.orden):
        excluido = it.estado == "excluido"
        d = ET.SubElement(docs, f"{{{NS}}}documento", attrib={"excluido": "true"} if excluido else {})
        # Orden fijo de hijos (la canonicalización C14N depende de él).
        _sub(d, f"{{{NS}}}identificador", it.document_id)
        _sub(d, f"{{{NS}}}titulo", it.titulo or "")
        _sub(d, f"{{{NS}}}tipologia", it.tipologia or "")
        _sub_opt(d, f"{{{NS}}}formato", it.formato)
        _sub_opt(d, f"{{{NS}}}tamano", str(it.tamano_bytes) if it.tamano_bytes is not None else None)
        _sub_opt(d, f"{{{NS}}}folios", str(it.folios) if it.folios is not None else None)
        # v4 (R1): SOLO cuando ambos codigo/nombre existen (sin centinela).
        if it.dependencia_productora_codigo is not None and it.dependencia_productora_nombre is not None:
            dep_el = _sub(d, f"{{{NS}}}dependenciaProductora", it.dependencia_productora_nombre)
            dep_el.set("codigo", str(it.dependencia_productora_codigo))
        _sub_opt(
            d,
            f"{{{NS}}}fechaIncorporacion",
            it.fecha_archivado.isoformat() if it.fecha_archivado else None,
        )
        # v4 (R2): equivalencia declaracion == incorporacion (Orfeo
        # SGD_EXP_FECH vs fech_radi) — MISMO valor que fechaIncorporacion, no
        # una fuente de datos aparte (`expediente_indice_item.fecha_declaracion`
        # queda deprecada, ver migración 019).
        _sub_opt(
            d,
            f"{{{NS}}}fechaDeclaracion",
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


def content_hash(xml: str) -> str:
    """Hash de idempotencia del versionado — v4: PRINCIPIO RECTOR, cubre TODO
    el documento canónico (idéntico cómputo a `xml_sha256`, sobre el mismo
    texto ya canonicalizado por `build_xml`/`ET.canonicalize`).

    Reemplaza el diseño v1-v3 (concatenación de campos SOLO por-item, que por
    construcción no podía representar elementos de CABECERA como
    `pistaAuditoria`/`politicaAcceso`/`conformidad`): al hashear el árbol
    completo, cualquier cambio en CUALQUIER elemento del índice — incluidos
    los nuevos de cabecera y `dependenciaProductora`/`fechaDeclaracion`
    por-documento — dispara version+1 automáticamente, sin enumerar campos a
    mano. Las dos columnas (`content_hash`, `xml_sha256`) pueden coincidir en
    valor para v4 — se mantienen separadas por semántica (una decide el
    version-bump; la otra es la fixity del XML almacenado) y por si divergen
    en un perfil futuro.
    """
    return hashlib.sha256(xml.encode("utf-8")).hexdigest()

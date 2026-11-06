# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Paquete de entrega de la transferencia secundaria (B4, ADR-030 §5).

El AIP se sella al cerrar el expediente, ANTES de que existan las constancias de «firma
inobtenible», sus revocaciones y los sobres de revelación del fundamento; y el acta FUID
congelada es probatoria y no se reescribe. Por eso lo que viaja al archivo histórico es un
paquete aparte, generado a petición y reproducible desde componentes que YA están bajo
Object-Lock:

    acta/acta-fuid.xml                      acta congelada (tal cual se firmó)
    acta/acta-fuid.xades.xml                sello institucional
    acta/firma-personal-<rol>.xml           firmas personales del acta
    indice/indice-electronico.xades.xml     índice electrónico FIRMADO (el de la versión entregada)
    clasificado/constancias/<rol>-<n>.xml   cadena COMPLETA, también las revocadas
    clasificado/revocaciones/<rol>-<n>.xml
    clasificado/sobres/<rol>-<n>-<acto>.xml texto + sal del fundamento (dato sensible)
    nota-entrega.xml                        anotación del FUID: referencia el acta y el AIP
    checksums.txt                           SHA-256 de todo lo anterior

El AIP del expediente (BagIt + PREMIS, con los documentos) NO viaja dentro del ZIP: su volumen
lo hace inviable y ya está sellado bajo Object-Lock. La nota lo REFERENCIA por bucket, clave y
huella SHA-256 (`<aip>`): quien recibe lo obtiene del almacén y lo coteja contra esa huella.
El inventario FUID es el propio acta congelada (`acta/acta-fuid.xml`).

El material `clasificado/` es información clasificada (Ley 1712/2014 art. 18 lit. a: datos de
salud/fallecimiento): viaja solo a la entidad receptora y se declara como tal en la nota.

Este módulo es PURO (sin BD ni red): recibe componentes ya descargados y devuelve bytes. La
nota es DETERMINISTA —no lleva hora ni autor, que viven en el asiento de auditoría—, así su
huella es estable entre dos generaciones del mismo paquete.
"""

from __future__ import annotations

import hashlib
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import IO

NS_NOTA = "urn:orpycamcp:nota-entrega-transferencia:v1"
FUNDAMENTO_CLASIFICACION = "Ley 1712/2014 art. 18 lit. a"
ACCESO_CLASIFICADO = "restringido a la entidad receptora"
#: Bytes en memoria antes de pasar a disco: son XML pequeños.
_SPOOL_MAX = 8 * 1024 * 1024


@dataclass(slots=True)
class Componente:
    """Un fichero del paquete. `clasificado` marca el material de la cadena de excepciones;
    `sobre` (solo en constancias y revocaciones) es `"presente"` o `"ausente"` (v2/legadas)."""

    ruta: str
    contenido: bytes
    acto: str  # acta | sello_acta | firma_personal_acta | constancia | revocacion | sobre
    rol: str | None = None
    ordinal: int | None = None
    clasificado: bool = False
    sobre: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.contenido).hexdigest()


def construir_nota(
    *,
    transferencia_id: str,
    tipo: str,
    expediente_code: str,
    acta_fuid_sha256: str,
    componentes: list[Componente],
    aip: dict[str, str | int | None] | None = None,
) -> bytes:
    """`nota-entrega.xml`: referencia el acta por su huella y lista cada componente."""
    ET.register_namespace("", NS_NOTA)
    raiz = ET.Element(f"{{{NS_NOTA}}}nota-entrega", {"version": "1"})
    ET.SubElement(
        raiz,
        f"{{{NS_NOTA}}}transferencia",
        {"id": transferencia_id, "tipo": tipo, "expediente": expediente_code},
    )
    ET.SubElement(raiz, f"{{{NS_NOTA}}}acta", {"acta_fuid_sha256": acta_fuid_sha256})
    ET.SubElement(raiz, f"{{{NS_NOTA}}}anotacion").text = (
        "Anotación del FUID: el acta congelada es probatoria y no se reescribe; esta nota la "
        "referencia por su huella y declara los componentes posteriores a su congelación "
        "(constancias de firma inobtenible, revocaciones y sobres de revelación)."
    )
    if aip is not None:
        ET.SubElement(
            raiz,
            f"{{{NS_NOTA}}}aip",
            {k: str(v) for k, v in sorted(aip.items()) if v is not None}
            | {"incluido_en_paquete": "no"},
        )
    lista = ET.SubElement(raiz, f"{{{NS_NOTA}}}componentes")
    for c in sorted(componentes, key=lambda x: x.ruta):
        attrs = {"ruta": c.ruta, "sha256": c.sha256, "acto": c.acto}
        if c.rol is not None:
            attrs["rol"] = c.rol
        if c.ordinal is not None:
            attrs["ordinal"] = str(c.ordinal)
        if c.sobre is not None:
            attrs["sobre"] = c.sobre
        if c.clasificado:
            attrs["clasificacion"] = "clasificada"
            attrs["fundamento"] = FUNDAMENTO_CLASIFICACION
            attrs["acceso"] = ACCESO_CLASIFICADO
        else:
            attrs["clasificacion"] = "ninguna"
        ET.SubElement(lista, f"{{{NS_NOTA}}}componente", attrs)
    return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)


def empaquetar(componentes: list[Componente], nota: bytes) -> tuple[IO[bytes], int]:
    """ZIP en un fichero temporal (en memoria hasta `_SPOOL_MAX`): `(fichero rebobinado, tamaño)`.
    El llamante lo cierra. Incluye `nota-entrega.xml` y `checksums.txt`."""
    ficheros = {c.ruta: c.contenido for c in componentes}
    ficheros["nota-entrega.xml"] = nota
    checksums = "".join(
        f"{hashlib.sha256(b).hexdigest()}  {ruta}\n" for ruta, b in sorted(ficheros.items())
    ).encode("utf-8")
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX)  # noqa: SIM115 — lo cierra el llamante
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta, datos in sorted(ficheros.items()):
            zf.writestr(ruta, datos)
        zf.writestr("checksums.txt", checksums)
    tamano = spool.tell()
    spool.seek(0)
    return spool, tamano

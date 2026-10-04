# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Tabla estática MIME -> PUID de PRONOM (AREA-08-05).

PRONOM (The National Archives, UK) es el registro de formatos que PREMIS v3 espera
en `formatRegistry`. Aquí el PUID es **declarado a partir del tipo MIME**, no
identificado por firma: un `application/pdf` puede ser PDF 1.4, 1.7 o PDF/A y esta
tabla no lo distingue (se fija el de la versión más común). La identificación real
—DROID/Siegfried— es roadmap, y por eso el AIP lo dice en un `formatNote`
(`FORMAT_NOTE`) en lugar de presentar el PUID como un hecho comprobado.

Un MIME que no está en la tabla NO recibe `formatRegistry`: ningún PUID inventado.
"""

from __future__ import annotations

REGISTRY_NAME = "PRONOM"

FORMAT_NOTE = (
    "PUID de PRONOM declarado por tipo MIME (tabla estática del servicio); no es una "
    "identificación por firma (DROID/Siegfried) y no distingue versiones del formato."
)

# MIME (en minúsculas, sin parámetros) -> PUID. Cubre `allowed_mime_types` y los
# artefactos del sistema (XML del índice, ZIP del AIP).
_PUID_POR_MIME: dict[str, str] = {
    "application/pdf": "fmt/276",  # PDF 1.7 (ISO 32000-1)
    "application/msword": "fmt/40",  # Word 97-2003
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "fmt/412",
    "application/vnd.ms-excel": "fmt/61",  # Excel 97-2003
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "fmt/214",
    "application/vnd.ms-powerpoint": "fmt/126",  # PowerPoint 97-2003
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "fmt/215",
    "application/vnd.oasis.opendocument.text": "fmt/291",  # ODF 1.2
    "application/vnd.oasis.opendocument.spreadsheet": "fmt/295",  # ODF 1.2
    "image/jpeg": "fmt/43",  # JFIF 1.01
    "image/png": "fmt/13",  # PNG 1.2
    "image/tiff": "fmt/353",  # TIFF
    "image/gif": "fmt/4",  # GIF 89a
    "text/plain": "x-fmt/111",
    "text/csv": "x-fmt/18",
    "application/zip": "x-fmt/263",
    "application/xml": "fmt/101",
    "text/xml": "fmt/101",
}


def puid_for_mime(mime_type: str | None) -> str | None:
    """PUID declarado para un MIME, o ``None`` si la tabla no lo conoce."""
    if not mime_type:
        return None
    return _PUID_POR_MIME.get(mime_type.split(";", 1)[0].strip().lower())

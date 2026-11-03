# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Only the document package writes `radicados`. workflow goes through document.api."""

from __future__ import annotations

import re

from tests._compose import RAIZ, glob_src

SERVICIOS = RAIZ / "services"
_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+radicados\b",
    re.IGNORECASE,
)


def _sin_comentarios(texto: str) -> str:
    lineas = []
    for linea in texto.splitlines():
        recortada = linea.lstrip()
        if recortada.startswith("#") or recortada.startswith("--"):
            continue
        lineas.append(linea)
    return "\n".join(lineas)


def test_nadie_escribe_radicados_fuera_de_document() -> None:
    culpables: list[str] = []
    for ruta in glob_src(SERVICIOS, "*/app/**/*.py"):
        if "tests" in ruta.parts:
            continue
        partes = ruta.relative_to(SERVICIOS).parts
        if len(partes) >= 2 and partes[1] == "document":
            continue
        if _WRITE.search(_sin_comentarios(ruta.read_text(encoding="utf-8"))):
            culpables.append(str(ruta.relative_to(RAIZ)))
    assert not culpables, (
        "El estado y la ubicación del radicado se escriben en document.api. "
        "Una escritura directa desde otro paquete salta el único camino de "
        "escritura:\n  " + "\n  ".join(culpables)
    )


def test_document_api_es_quien_mueve_el_flujo() -> None:
    api = RAIZ / "services" / "document-service" / "document" / "api.py"
    texto = api.read_text(encoding="utf-8")
    assert "UPDATE radicados SET status" in texto
    assert "flujo_dept" in texto

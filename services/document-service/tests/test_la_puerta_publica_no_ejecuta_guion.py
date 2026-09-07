# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un anexo servido por la puerta pública no puede ejecutarse en el navegador.

El tipo lo declara quien subió el archivo, y esa puerta es pública y sin
autenticar. Servir en línea con el tipo declarado significaba que un anexo HTML
—o un SVG, que también ejecuta guion— se renderizaba en el origen del sitio.
"""

import pytest

from app.routers.cmis import _tipo_y_disposicion


@pytest.mark.parametrize(
    "mime",
    ["text/html", "image/svg+xml", "application/xhtml+xml", "application/javascript"],
)
def test_lo_que_puede_ejecutar_se_descarga(mime):
    tipo, disposicion = _tipo_y_disposicion(mime, "a.html")

    assert tipo == "application/octet-stream", f"{mime} se sirve con su tipo declarado"
    assert disposicion.startswith("attachment"), f"{mime} se sirve en línea"


@pytest.mark.parametrize("mime", ["application/pdf", "image/png", "image/jpeg"])
def test_lo_que_se_puede_mostrar_sigue_mostrandose(mime):
    """El recorte no puede dejar inservible la consulta pública que protege."""
    tipo, disposicion = _tipo_y_disposicion(mime, "a.pdf")

    assert tipo == mime
    assert disposicion.startswith("inline")


def test_un_tipo_invalido_no_llega_a_la_cabecera():
    tipo, _ = _tipo_y_disposicion("text/html\r\nX-Inyectado: 1", "a")

    assert "\r" not in tipo and "\n" not in tipo
    assert tipo == "application/octet-stream"

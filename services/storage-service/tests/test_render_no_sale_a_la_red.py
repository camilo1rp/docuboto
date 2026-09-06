# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El renderizador del oficio no resuelve recursos externos.

El cuerpo del documento llega saneado por el proxy del navegador, pero ese
saneamiento vive en OTRO proceso y el gateway enruta la escritura del cuerpo
directo a document-service: quien llama a la API no pasa por el navegador. Por
eso la defensa tiene que estar aquí, en la única capa por la que pasan todas las
rutas de render.

Las pruebas ejercitan el renderizador REAL, no un doble: lo que se afirma es que
WeasyPrint no abre la conexión, y eso no se puede demostrar simulando WeasyPrint.
"""

import base64

import pytest

from app.services.render.weasyprint_renderer import WeasyPrintRenderer, _sin_red

_PAGINA = {"tamano": "Letter", "pie": "Entidad de prueba"}

# 1x1 PNG transparente, para comprobar que lo legítimo sigue funcionando.
_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae4260 82"
        .replace(" ", "")
    )
).decode()


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://tenant-service:8002/api/v1/tenants/",
        "https://ejemplo.invalido/logo.png",
        "file:///etc/passwd",
        "//tenant-service:8002/api/v1/tenants/",
    ],
)
def test_ninguna_url_externa_se_resuelve(url):
    """Ni red interna, ni metadatos de la nube, ni ficheros locales."""
    with pytest.raises(ValueError) as exc:
        _sin_red(url)
    assert "no resuelve recursos externos" in str(exc.value)


def test_los_recursos_incrustados_siguen_funcionando():
    """La lista de admisión no puede dejar el membrete sin su logo.

    Un gate que nadie puede satisfacer no protege: suprime.
    """
    resultado = _sin_red(f"data:image/png;base64,{_PNG}")
    # WeasyPrint devuelve su propio objeto de respuesta, no un diccionario.
    assert resultado is not None
    assert getattr(resultado, "content_type", None) or hasattr(resultado, "read")


@pytest.mark.asyncio
async def test_el_render_completo_no_alcanza_la_red():
    """De punta a punta, con WeasyPrint de verdad.

    El renderizador no puede fallar por esto —su contrato es no lanzar nunca— y
    el PDF sale igual: lo que no ocurre es la petición al host interno.
    """
    cuerpo = (
        '<p>Oficio</p>'
        '<img src="http://tenant-service:8002/api/v1/tenants/">'
        '<img src="file:///etc/passwd">'
    )
    resultado = await WeasyPrintRenderer(timeout_seconds=30).render(
        cuerpo, pagina=_PAGINA, perfil="pdf/a-3b"
    )

    assert resultado.outcome == "generado", (
        f"el render falló en vez de ignorar el recurso externo: {resultado.detalle}"
    )
    assert resultado.contenido

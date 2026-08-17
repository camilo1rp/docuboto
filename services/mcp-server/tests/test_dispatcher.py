# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Preparación de la llamada: reparto de argumentos y escape de la ruta.

El test central de este módulo es el del traversal, y no es higiene: con la
interpolación cruda anterior, un `document_id` con `../../auth/me` hacía que el
mcp-server invocara **otro endpoint con el token del usuario**. Es el mismo
defecto de normalización que se cerró en el api-gateway, vivo un salto más allá.
"""

from __future__ import annotations

import pytest

from app.services.dispatcher import DispatchError, build_request
from app.tools.catalog import Param, Tool

_UUID = "11111111-1111-1111-1111-111111111111"


def _tool(method: str = "GET", **kw) -> Tool:
    base = {
        "name": "t",
        "description": "",
        "method": method,
        "path": "/api/v1/cosas/{cosa_id}",
        "path_params": ("cosa_id",),
        "params": (
            Param(name="cosa_id", required=True, location="path"),
            Param(name="nota", type="string"),
        ),
    }
    return Tool(**{**base, **kw})


def test_el_parametro_de_ruta_se_escapa() -> None:
    """El traversal que estaba abierto: `../../auth/me` no debe cambiar de ruta.

    Se comprueba sobre la ruta resultante, no sobre el código: es lo único que
    distingue el arreglo de la interpolación cruda.
    """
    # Sin `format="uuid"` para aislar el escape de la validación: son dos capas
    # distintas y cada una debe sostenerse sola.
    tool = _tool(
        params=(Param(name="cosa_id", required=True, location="path"),),
    )
    prep = build_request(tool, {"cosa_id": "../../auth/me"})

    assert prep.path == "/api/v1/cosas/..%2F..%2Fauth%2Fme"
    assert "/api/v1/auth/me" not in prep.path
    assert prep.path.count("/") == 4  # no se añadieron segmentos


def test_un_uuid_invalido_ni_siquiera_llega_al_escape() -> None:
    """La primera capa: `coerce_args` rechaza antes de construir la ruta."""
    tool = _tool(
        params=(Param(name="cosa_id", required=True, location="path", format="uuid"),),
    )
    with pytest.raises(DispatchError) as exc:
        build_request(tool, {"cosa_id": "../../auth/me"})
    assert exc.value.status == 400


def test_el_metodo_decide_donde_van_los_argumentos() -> None:
    """GET/DELETE llevan los argumentos en la query; el resto, en el cuerpo."""
    for metodo, en_query in (("GET", True), ("DELETE", True), ("POST", False), ("PATCH", False)):
        prep = build_request(_tool(metodo), {"cosa_id": _UUID, "nota": "x"})
        assert prep.method == metodo
        if en_query:
            assert prep.query == {"nota": "x"}
        else:
            assert prep.body == {"nota": "x"}


def test_delete_nunca_lleva_cuerpo() -> None:
    """Hay proxies y servidores que descartan el cuerpo de un DELETE en silencio.

    Una petición cuyo significado depende de algo que puede desaparecer por el
    camino no es una petición fiable.
    """
    prep = build_request(_tool("DELETE"), {"cosa_id": _UUID, "nota": "x"})
    assert prep.body is None
    assert prep.query == {"nota": "x"}


def test_los_metodos_de_escritura_estan_soportados() -> None:
    """Antes, todo lo que no era GET caía en un POST incondicional.

    La mayoría de las escrituras del sistema son PATCH: sin esto, el catálogo no
    podía expresar ni la mitad de las operaciones.
    """
    for metodo in ("POST", "PUT", "PATCH", "DELETE"):
        assert build_request(_tool(metodo), {"cosa_id": _UUID}).method == metodo


def test_falta_un_parametro_de_ruta() -> None:
    with pytest.raises(DispatchError) as exc:
        build_request(_tool(), {"nota": "x"})
    # `coerce_args` lo detecta antes por ser obligatorio; el mensaje nombra el
    # parámetro, que es lo que permite corregirse sin adivinar.
    assert exc.value.status == 400


def test_una_clave_no_declarada_aborta_el_despacho() -> None:
    """El dispatcher no envía lo que el catálogo no declara.

    Antes esto solo lo filtraba el asistente, de modo que los otros dos
    transportes despachaban cualquier clave que les llegara.
    """
    with pytest.raises(DispatchError) as exc:
        build_request(_tool(), {"cosa_id": _UUID, "acl": {"max": 3}})
    assert exc.value.code == "parametro_no_declarado"


def test_la_ruta_sin_plantilla_se_deja_intacta() -> None:
    tool = Tool(
        name="t",
        description="",
        method="GET",
        path="/api/v1/search",
        params=(Param(name="q", type="string"),),
    )
    prep = build_request(tool, {"q": "tutela"})
    assert prep.path == "/api/v1/search"
    assert prep.query == {"q": "tutela"}


def test_el_numero_de_radicado_tambien_se_escapa() -> None:
    """`by-tracking` es la ÚNICA ruta del sistema con convertidor `:path`.

    Un `:path` casa con barras a propósito, así que es el sitio donde un
    parámetro sin escapar cambia de endpoint con más facilidad que en ningún
    otro. El número de radicado no lleva barras —solo dígitos, letras y
    guiones—, así que escaparlo no le quita nada y le quita todo a quien
    intente colar una ruta dentro.

    Se comprueba sobre la ruta construida, contra la tool REAL del catálogo, no
    contra una de laboratorio: el escape podría estar bien en el dispatcher y
    esta tool podría no llegar a usarlo.
    """
    from app.tools.catalog import get_tool

    tool = get_tool("consultar_por_numero")
    assert tool is not None

    prep = build_request(tool, {"tracking_number": "2026-DEMO-E-000001"})
    assert prep.path == "/api/v1/documents/by-tracking/2026-DEMO-E-000001"

    hostil = build_request(tool, {"tracking_number": "../../auth/me"})
    assert "/api/v1/auth/me" not in hostil.path
    assert hostil.path == "/api/v1/documents/by-tracking/..%2F..%2Fauth%2Fme"

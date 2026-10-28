# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Toda tool del catálogo MCP tiene que ser alcanzable a través del gateway.

**El fallo que este test existe para impedir ya ocurrió.** La carga masiva
(`/api/v1/batch/`) se construyó entera en el frontend contra endpoints que
existían en document-service y archive-service, y el gateway no tenía ese prefijo
en su tabla de ruteo: las dos secciones devolvían `404 route_not_found`. Nada
falló al construirlas; el hueco apareció al usarlas.

El mcp-server es cliente HTTP del gateway (ADR-019), así que una tool cuya ruta
no esté enrutada es una capacidad que se ofrece, se propone, se confirma… y
devuelve 404. Peor que no ofrecerla.

**Vive aquí y no en ninguno de los dos servicios** porque es un invariante que
cruza dos: el contenedor de mcp-server no tiene el código del gateway y el del
gateway no tiene el catálogo. `shared/` es el único carril de CI que ve el
repositorio entero.

Ambas listas se **derivan del código** —el catálogo por sus `Tool(path=...)`, la
tabla del gateway por sus literales—, nunca se copian: una tercera lista a mano
sería otra cosa más que se queda atrás, que es el modo de fallo que estamos
persiguiendo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_CATALOGO = _RAIZ / "services" / "mcp-server" / "app" / "tools" / "catalog.py"
_PROXY = _RAIZ / "services" / "api-gateway" / "app" / "routers" / "proxy.py"


def _rutas_del_catalogo() -> list[tuple[str, str]]:
    """(nombre_de_tool, ruta) de cada `Tool(...)` del catálogo."""
    arbol = ast.parse(_CATALOGO.read_text(encoding="utf-8"))
    salida: list[tuple[str, str]] = []
    for nodo in ast.walk(arbol):
        if not (isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "Tool"):
            continue
        campos = {
            kw.arg: kw.value.value
            for kw in nodo.keywords
            if isinstance(kw.value, ast.Constant) and kw.arg
        }
        if "path" in campos:
            salida.append((str(campos.get("name", "?")), str(campos["path"])))
    return salida


def _prefijos_del_gateway() -> list[str]:
    """Los prefijos de `_route_to_upstream`, leídos de su propia lista."""
    arbol = ast.parse(_PROXY.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if not (isinstance(nodo, ast.AnnAssign) and getattr(nodo.target, "id", "") == "routing"):
            continue
        assert isinstance(nodo.value, ast.List)
        return [
            e.elts[0].value
            for e in nodo.value.elts
            if isinstance(e, ast.Tuple) and isinstance(e.elts[0], ast.Constant)
        ]
    raise AssertionError("no se encontró la tabla `routing` en el proxy del gateway")


def _enruta(ruta: str, prefijos: list[str]) -> bool:
    """Réplica de la regla del gateway: prefijo, o coincidencia exacta sin barra.

    Es la única línea duplicada, y a sabiendas: importar `_route_to_upstream`
    arrastraría FastAPI, httpx y la configuración del gateway a un test que solo
    quiere comparar dos listas de cadenas. Si esa regla cambiara, este test
    seguiría pasando — por eso lo que fija es la ALCANZABILIDAD, que es lo que
    duele, y no la implementación del ruteo, que tiene sus propios tests.
    """
    return any(ruta.startswith(p) or ruta == p.rstrip("/") for p in prefijos)


def test_todas_las_tools_pasan_por_el_gateway() -> None:
    prefijos = _prefijos_del_gateway()
    assert prefijos, "la tabla de ruteo salió vacía: el parseo se rompió"

    catalogo = _rutas_del_catalogo()
    assert catalogo, "el catálogo salió vacío: el parseo se rompió"

    huerfanas = [(n, r) for n, r in catalogo if not _enruta(r, prefijos)]
    assert not huerfanas, (
        "estas tools apuntan a rutas que el gateway NO enruta y devolverían "
        f"404 route_not_found al ejecutarlas: {huerfanas}"
    )


@pytest.mark.parametrize(
    "ruta",
    [
        "/api/v1/tenants/",  # retirada del ruteo por fuga entre entidades
        "/api/v1/storage/download",  # sellada: la descarga la media document-service
        "/api/v1/knowledge/ingest",  # retirada en D0 (ADR-046 §4)
        "/api/v1/inventado/",
    ],
)
def test_el_control_distingue_lo_que_no_esta_enrutado(ruta: str) -> None:
    """Control negativo: sin él, un parseo que devolviera prefijos de más
    —o un `_enruta` que dijera que sí a todo— dejaría el test verde para
    siempre y sin avisar. Las tres primeras rutas NO están enrutadas **a
    propósito**, y eso las convierte en el mejor control disponible."""
    assert not _enruta(ruta, _prefijos_del_gateway())


# Prefijos del propio mcp-server, ahora que el gateway SÍ los enruta.
_SUPERFICIE_PROPIA = ("/api/v1/mcp",)


def test_ninguna_tool_apunta_a_la_superficie_del_propio_mcp() -> None:
    """El catálogo no puede llamarse a sí mismo.

    Hasta ahora esto no podía ocurrir por accidente: el gateway **no enrutaba**
    `/api/v1/mcp`, así que una tool que apuntara ahí habría muerto en un 404. Al
    exponer el protocolo a clientes externos esa protección de hecho desaparece,
    y lo que quedaba implícito hay que escribirlo.

    Dos cosas rompe una tool así, y ninguna es teórica:

    - **Recursión.** `invoke` despacha una tool que invoca `invoke`, con el token
      del usuario en cada salto y sin nada que corte la profundidad.
    - **Salto del gate de escritura.** El gate vive en `invoke`/`call`: una
      escritura devuelve una propuesta y no ejecuta. Una tool que llamara a
      `/api/v1/mcp/proposals/execute` ejecutaría una propuesta como si fuera una
      lectura cualquiera del catálogo — la confirmación seguiría existiendo, y ya
      no significaría nada.
    """
    infractoras = [
        (n, r)
        for n, r in _rutas_del_catalogo()
        if any(r.startswith(p) for p in _SUPERFICIE_PROPIA)
    ]
    assert not infractoras, (
        "estas tools apuntan a la superficie del propio mcp-server: abren recursión "
        f"y saltan el gate de escritura: {infractoras}"
    )


def test_el_control_reconoce_una_ruta_de_la_superficie_propia() -> None:
    """Sin esto, un `_SUPERFICIE_PROPIA` mal escrito dejaría el test de arriba
    verde para siempre — el mismo modo de fallo que los tests que filtran antes
    de afirmar."""
    assert any(
        "/api/v1/mcp/proposals/execute".startswith(p) for p in _SUPERFICIE_PROPIA
    )
    assert not any("/api/v1/documents/".startswith(p) for p in _SUPERFICIE_PROPIA)

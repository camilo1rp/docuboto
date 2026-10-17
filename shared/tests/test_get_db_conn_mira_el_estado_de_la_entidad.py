# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""archive, workflow y storage miran el estado de la entidad en `get_db_conn` (B9-N3).

No tienen `get_tenant_conn`: la conexión la entrega `get_db_conn` (`core/database.py`), por
la que pasa toda petición con tenant, también las lecturas que no piden `require_permission`
(en vivo, `GET /workflows/inbox` respondía 200 con la entidad suspendida mientras el gate
estuvo solo en `require_permission`). Guardián por FORMA (AST): el veredicto se espera, se
evalúa y se levanta ANTES de ceder la conexión.
"""

import ast
from pathlib import Path

import pytest

_SERVICIOS = Path(__file__).resolve().parents[2] / "services"
_necesita_arbol = pytest.mark.skipif(
    not _SERVICIOS.is_dir(), reason="el árbol de servicios no está montado"
)
SERVICIOS = ["archive", "workflow", "storage"]


def _get_db_conn(servicio: str) -> ast.AsyncFunctionDef:
    archivo = _SERVICIOS / f"{servicio}-service" / "app" / "core" / "database.py"
    for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.AsyncFunctionDef) and nodo.name == "get_db_conn":
            return nodo
    raise AssertionError(f"{servicio}: database.py no define get_db_conn")


def _veredicto(n: ast.AST) -> bool:
    return (
        isinstance(n, ast.NamedExpr)
        and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and isinstance(n.value.value.func, ast.Name)
        and n.value.value.func.id == "veredicto_entidad"
    )


@_necesita_arbol
@pytest.mark.parametrize("servicio", SERVICIOS)
def test_get_db_conn_espera_el_veredicto_antes_de_ceder_la_conexion(servicio: str) -> None:
    fn = _get_db_conn(servicio)
    veredicto = [n.lineno for n in ast.walk(fn) if _veredicto(n)]
    cesion = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Yield)]
    assert veredicto, f"{servicio}: get_db_conn no espera el veredicto de la entidad"
    assert cesion
    assert min(veredicto) < min(cesion), f"{servicio}: el veredicto debe ir ANTES del yield"


@_necesita_arbol
@pytest.mark.parametrize("servicio", SERVICIOS)
def test_el_veto_se_levanta_no_se_descarta(servicio: str) -> None:
    fn = _get_db_conn(servicio)
    condiciones = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.If) and any(_veredicto(x) for x in ast.walk(n.test))
    ]
    assert condiciones, f"{servicio}: el veredicto debe evaluarse en un if"
    assert any(isinstance(x, ast.Raise) for c in condiciones for x in ast.walk(c)), (
        f"{servicio}: el veto no se levanta"
    )


@_necesita_arbol
@pytest.mark.parametrize("servicio", SERVICIOS)
def test_require_permission_no_duplica_el_gate(servicio: str) -> None:
    """Un solo punto: `require_permission` usa `get_db_conn`, así que ya pasó por el gate;
    repetirlo costaría una consulta más por petición sin cerrar nada nuevo."""
    clearance = _SERVICIOS / f"{servicio}-service" / "app" / "core" / "clearance.py"
    assert "veredicto_entidad" not in clearance.read_text(encoding="utf-8")

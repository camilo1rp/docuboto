# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una base de datos para el proceso: `orpycamcp_common.database` (ADR-047).

Había nueve `core/database.py`, uno por servicio, y dos guardianes que
comprobaban, copia por copia, que cada `get_*_conn` mirara el estado de la
entidad antes de fijar el schema. Con una sola definición se vigila:

* que ningún dominio vuelve a tener su `core/database.py` ni define su propia
  dependencia de conexión de tenant (las de document son públicas y van por
  `entidad_publica`);
* que `get_tenant_conn` y `get_vetted_conn` piden el veredicto ANTES de ceder la
  conexión, y `get_tenant_conn` antes de fijar el `search_path`;
* que el veto se levanta como HTTP y no se traga.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from orpycamcp_common import database
from tests._compose import APP

FUENTE = Path(database.__file__).read_text(encoding="utf-8")


def _funcion(nombre: str) -> ast.AsyncFunctionDef:
    for n in ast.parse(FUENTE).body:
        if isinstance(n, ast.AsyncFunctionDef) and n.name == nombre:
            return n
    raise AssertionError(f"{nombre} no existe")


def test_ningun_dominio_tiene_su_database() -> None:
    copias = sorted(p.relative_to(APP).as_posix() for p in APP.glob("*/core/database.py"))
    assert copias == [], copias


def test_ningun_dominio_define_su_conexion_de_tenant() -> None:
    propias: list[str] = []
    for f in APP.glob("*/**/*.py"):
        if "tests" in f.parts or "docuboto" == f.relative_to(APP).parts[0]:
            continue
        for n in ast.parse(f.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.AsyncFunctionDef) and n.name in {
                "get_tenant_conn",
                "get_db_conn",
                "get_vetted_conn",
                "acquire_tenant",
            }:
                propias.append(f"{f.relative_to(APP)}::{n.name}")
    assert propias == [], propias


@pytest.mark.parametrize("nombre", ["get_tenant_conn", "get_vetted_conn"])
def test_el_veto_va_antes_de_ceder_la_conexion(nombre: str) -> None:
    texto = ast.unparse(_funcion(nombre))
    assert "veredicto_entidad" in texto
    assert texto.index("veredicto_entidad") < texto.index("yield conn")


def test_el_veto_va_antes_del_search_path() -> None:
    texto = ast.unparse(_funcion("get_tenant_conn"))
    assert texto.index("veredicto_entidad") < texto.index("SET search_path = \"")


class _Pool:
    def __init__(self, conn: object) -> None:
        self.conn = conn

    def acquire(self) -> "_Pool":
        return self

    async def __aenter__(self) -> object:
        return self.conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _peticion(slug: str, conn: object) -> MagicMock:
    from orpycamcp_common.usuario import Usuario

    req = MagicMock()
    req.state.usuario = Usuario(id=None, tenant_slug=slug)
    req.app.state.db_pool = _Pool(conn)
    return req


def test_el_veto_se_levanta_como_http_y_no_fija_el_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock(execute=AsyncMock())
    monkeypatch.setattr(database, "veredicto_entidad", AsyncMock(return_value=(403, "x", "y")))

    async def _correr() -> None:
        gen = database.get_tenant_conn(_peticion("demo", conn))
        await gen.__anext__()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_correr())
    assert exc.value.status_code == 403
    conn.execute.assert_not_awaited()


def test_slug_invalido_es_400_y_value_error() -> None:
    with pytest.raises(HTTPException) as exc:
        database.tenant_schema("../x")
    assert exc.value.status_code == 400
    assert isinstance(exc.value, ValueError)

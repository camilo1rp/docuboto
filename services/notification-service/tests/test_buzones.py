# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Buzones de dependencia: el defecto concreto, y el censo que impide su vuelta.

El defecto era `f"{to_dept}@orpycamcp.local"` con `to_dept` = «Oficina
Jurídica». Comprobarlo una vez no basta: mientras interpolar texto libre en una
dirección sea posible, vuelve en el siguiente `f"...@..."` que alguien escriba.
Por eso el último test **censa el árbol** en vez de mirar el sitio conocido.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.buzones import (
    BUZON_VENTANILLA,
    buzon_de_codigo,
    buzon_de_codigo_o_ventanilla,
    resolver_buzon,
    resolver_buzon_dependencia,
)

APP = Path(__file__).resolve().parents[1] / "app"


def test_buzon_de_codigo_produce_parte_local_ascii():
    assert buzon_de_codigo(902) == "dependencia-902@orpycamcp.local"


@pytest.mark.parametrize(
    "codigo",
    [
        "Oficina Jurídica",  # el nombre que producía el fallo real
        "Ventanilla Única de Radicación",
        "con espacio",
        "tildé",
        "",
    ],
)
def test_buzon_de_codigo_se_niega_a_fabricar_una_direccion_invalida(codigo):
    """El caso exacto que rompía: nombre humano en la parte local."""
    with pytest.raises(ValueError):
        buzon_de_codigo(codigo)


def test_variante_tolerante_cae_a_ventanilla_en_vez_de_reventar():
    """Reventar aquí pararía el consumidor del stream: peor que un mal encamine."""
    assert buzon_de_codigo_o_ventanilla("Oficina Jurídica") == BUZON_VENTANILLA
    assert buzon_de_codigo_o_ventanilla(None) == BUZON_VENTANILLA
    assert buzon_de_codigo_o_ventanilla(7) == "dependencia-7@orpycamcp.local"


@pytest.mark.asyncio
async def test_resolver_traduce_el_nombre_al_buzon_del_catalogo():
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"codigo": 902, "candidatos": 1}
    assert await resolver_buzon_dependencia(conn, "Oficina Jurídica") == (
        "dependencia-902@orpycamcp.local"
    )
    # Función única del catálogo, con histórico (nombres anteriores a un renombre).
    sql, arg = conn.fetchrow.await_args.args
    assert "resolver_codigo_dependencia($1, true)" in sql
    assert arg == "Oficina Jurídica"


@pytest.mark.asyncio
@pytest.mark.parametrize("nombre", ["Dependencia Que No Existe", None, ""])
async def test_resolver_cae_a_ventanilla_si_el_nombre_no_resuelve(nombre):
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = None
    assert await resolver_buzon_dependencia(conn, nombre) == BUZON_VENTANILLA


def _conn_catalogo(filas: list[dict]) -> AsyncMock:
    """Conexión sin la función SQL (tenant sin migrar): solo la tabla `dependencias`."""
    conn = AsyncMock()
    conn.fetchval.return_value = False
    conn.fetch.return_value = filas
    return conn


@pytest.mark.asyncio
async def test_homonimos_van_a_la_ventanilla_no_a_una_fila_arbitraria():
    """B5/N2: con dos «Archivo» la fila elegida dependía del orden del heap."""
    for orden in ([{"codigo": 901}, {"codigo": 902}], [{"codigo": 902}, {"codigo": 901}]):
        conn = _conn_catalogo(orden)
        assert await resolver_buzon(conn, None, "Archivo") == BUZON_VENTANILLA


@pytest.mark.asyncio
async def test_homonimos_con_la_funcion_del_catalogo_tambien_van_a_ventanilla():
    conn = AsyncMock()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"codigo": None, "candidatos": 2}
    assert await resolver_buzon(conn, None, "Archivo") == BUZON_VENTANILLA


@pytest.mark.asyncio
async def test_nombre_unico_sin_la_funcion_resuelve_por_el_catalogo():
    conn = _conn_catalogo([{"codigo": 901}])
    assert await resolver_buzon(conn, None, "Archivo") == "dependencia-901@orpycamcp.local"


@pytest.mark.asyncio
async def test_el_codigo_del_evento_manda_sobre_el_nombre():
    """Renombrada la dependencia, el nombre del evento puede ser de otra: gana el código."""
    conn = AsyncMock()
    assert await resolver_buzon(conn, 901, "Nombre Que Ya No Es") == (
        "dependencia-901@orpycamcp.local"
    )
    conn.fetchrow.assert_not_called()
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_evento_viejo_con_nombre_renombrado_llega_a_la_dependencia():
    """La función del catálogo resuelve el nombre ANTERIOR vía histórico (p_historico=true)."""
    conn = AsyncMock()
    conn.fetchval.return_value = True

    async def _fila(sql, nombre):
        assert "resolver_codigo_dependencia($1, true)" in sql  # histórico activado
        return {"codigo": 901, "candidatos": 1} if nombre == "Archivo" else None

    conn.fetchrow.side_effect = _fila
    assert await resolver_buzon(conn, None, "Archivo") == "dependencia-901@orpycamcp.local"


def _direcciones_construidas(ruta: Path) -> list[int]:
    """Líneas de `ruta` donde se COMPONE una dirección de correo.

    Busca f-strings y concatenaciones cuyas partes literales contengan «@»:
    esa es la forma exacta del defecto —`f"{to_dept}@orpycamcp.local"`—, y
    deja en paz una dirección constante como el remitente `smtp_from`, que no
    depende de ningún dato.

    Se mira la FORMA, no una lista de sitios permitidos: una lista escrita a
    mano deja fuera lo que nadie recordó, que es como llegó aquí el defecto.
    """
    lineas: list[int] = []
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    for nodo in ast.walk(arbol):
        partes: list[ast.expr] = []
        if isinstance(nodo, ast.JoinedStr):
            partes = list(nodo.values)
        elif isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
            partes = [nodo.left, nodo.right]
        else:
            continue
        interpolado = any(not isinstance(p, ast.Constant) for p in partes)
        literal = "".join(
            p.value for p in partes if isinstance(p, ast.Constant) and isinstance(p.value, str)
        )
        if interpolado and "@" in literal:
            lineas.append(nodo.lineno)
    return lineas


def test_el_censo_ve_el_defecto_original():
    """Control positivo: sin esto, un extractor roto aprobaría en silencio."""
    muestra = APP.parent / "tests" / "_muestra_buzon.py"
    muestra.write_text('to_dept = "x"\ndireccion = f"{to_dept}@orpycamcp.local"\n')
    try:
        assert _direcciones_construidas(muestra) == [2]
    finally:
        muestra.unlink()


def test_ninguna_direccion_se_compone_fuera_de_buzones_py():
    """CENSO: `app/` no puede volver a fabricar una dirección por su cuenta."""
    culpables: list[str] = []
    revisados = 0
    for ruta in sorted(APP.rglob("*.py")):
        revisados += 1
        if ruta.relative_to(APP).as_posix() == "services/buzones.py":
            continue
        for linea in _direcciones_construidas(ruta):
            culpables.append(f"{ruta.relative_to(APP)}:{linea}")

    # Anti-vacuidad: si el recorrido se rompe y no lee nada, el test debe
    # fallar en voz alta en vez de aprobar por no haber mirado.
    assert revisados > 10, f"el censo solo leyó {revisados} archivos: extractor roto"

    assert not culpables, (
        "una dirección de correo se compone fuera de `app/services/buzones.py`: "
        f"{culpables}. Toda dirección pasa por ese módulo — es lo que impide volver "
        "a interpolar el NOMBRE de una dependencia en la parte local."
    )

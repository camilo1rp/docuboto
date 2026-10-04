# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-01-17: `check_permission` y `resolve_caller_clearance` se definen UNA vez.

Estaban copiadas byte a byte en nueve servicios. Un gemelo que diverja de la política
fail-closed en una sola copia es un hueco de autorización que ningún test del servicio
sano ve. La definición vive en `orpycamcp_common.rbac`; los servicios la importan.
"""

import ast
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from orpycamcp_common import rbac

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"
_UNICAS = {"check_permission", "resolve_caller_clearance", "nivel_efectivo", "mensaje_denegacion"}

_necesita_arbol = pytest.mark.skipif(
    not _SERVICIOS.is_dir(), reason="el árbol de servicios no está montado"
)


def _definiciones() -> list[tuple[str, str]]:
    halladas: list[tuple[str, str]] = []
    for archivo in sorted(_SERVICIOS.glob("*/app/**/*.py")):
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name in _UNICAS:
                halladas.append((str(archivo.relative_to(_RAIZ)), nodo.name))
    return halladas


@_necesita_arbol
def test_ningun_servicio_redefine_el_gate() -> None:
    copias = _definiciones()
    assert not copias, (
        "Estas funciones viven SOLO en orpycamcp_common.rbac; importa, no copies: "
        f"{copias}"
    )


@_necesita_arbol
def test_el_censo_ve_a_los_servicios_que_la_importan() -> None:
    """Anti-vacuidad: el guardián de arriba pasaría igual si el glob no hallara nada."""
    importan = [
        p
        for p in _SERVICIOS.glob("*/app/core/*.py")
        if "from orpycamcp_common.rbac import" in p.read_text(encoding="utf-8")
    ]
    assert len(importan) >= 8, [p.parent.parent.parent.name for p in importan]


@_necesita_arbol
def test_la_unica_definicion_existe_en_la_libreria() -> None:
    nombres = {
        n.name
        for n in ast.walk(ast.parse((_RAIZ / "shared/orpycamcp_common/rbac.py").read_text()))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert _UNICAS <= nombres


# --------------------------------------------------------------------------- comportamiento
# La política fail-closed ya no se prueba en nueve suites de servicio: se prueba aquí.


@pytest.mark.asyncio
async def test_sin_identidad_no_hay_permiso_ni_clearance() -> None:
    conn = AsyncMock()
    assert await rbac.check_permission(conn, None, "PERM_X") is False
    assert await rbac.resolve_caller_clearance(conn, None) == rbac.PUBLICA
    conn.fetchval.assert_not_called()
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_tablas_rbac_ausentes_es_fail_closed() -> None:
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.UndefinedTableError("x")
    conn.fetchrow.side_effect = asyncpg.UndefinedTableError("x")
    uid = uuid.uuid4()
    assert await rbac.check_permission(conn, uid, "PERM_X") is False
    assert await rbac.resolve_caller_clearance(conn, uid) == rbac.PUBLICA


@pytest.mark.asyncio
async def test_usuario_inexistente_es_fail_closed() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    uid = uuid.uuid4()
    assert await rbac.check_permission(conn, uid, "PERM_X") is False
    assert await rbac.resolve_caller_clearance(conn, uid) == rbac.PUBLICA


@pytest.mark.asyncio
async def test_root_accede_a_todo_y_el_nivel_es_el_maximo_de_la_matriz() -> None:
    conn = AsyncMock()
    uid = uuid.uuid4()
    conn.fetchrow.return_value = {"is_root": True, "max_level": None}
    assert await rbac.resolve_caller_clearance(conn, uid) == rbac.CLASIFICADA
    conn.fetchrow.return_value = {"is_root": False, "max_level": 2}
    assert await rbac.resolve_caller_clearance(conn, uid) == 2
    conn.fetchrow.return_value = {"is_root": False, "max_level": None}
    assert await rbac.resolve_caller_clearance(conn, uid) == rbac.PUBLICA
    conn.fetchval.return_value = True
    assert await rbac.check_permission(conn, uid, "PERM_X", 3) is True
    # el umbral viaja al SQL: el nivel exigido no se puede ignorar en silencio
    assert conn.fetchval.call_args.args[-1] == 3


def test_el_mensaje_distingue_no_lo_tiene_de_lo_tiene_corto() -> None:
    assert "Se requiere el permiso PERM_X" in rbac.mensaje_denegacion("PERM_X", 2, None)
    assert "con nivel 1" in rbac.mensaje_denegacion("PERM_X", 3, 1)

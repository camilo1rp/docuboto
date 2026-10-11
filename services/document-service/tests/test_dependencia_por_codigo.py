# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La dependencia se correlaciona por CÓDIGO (B5, ADR-032) — contrato del alias.

Unitarias (conn simulado) del criterio de `normalizar_dependencia`; la parte que
exige una base real (backfill 046, homónimas, renombre) vive en
`tests/integration/test_dependencia_codigo_046_integration.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from app.core.dependencias import (
    exigir_concordancia,
    marcar_alias_deprecado,
    normalizar_dependencia,
    usa_alias,
)


def _conn(*, catalogo: str | None = None, candidatos: int = 0, codigo: int | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = catalogo
    conn.fetchrow.return_value = {"codigo": codigo, "candidatos": candidatos}
    return conn


async def _error(coro) -> dict:
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 422
    return exc.value.detail


@pytest.mark.asyncio
async def test_el_codigo_manda_y_el_nombre_guardado_es_el_del_catalogo() -> None:
    conn = _conn(catalogo="Gestión Documental")
    assert await normalizar_dependencia(
        conn, campo="dest_dept", codigo=901, nombre=None
    ) == ("Gestión Documental", 901, False)
    conn.fetchrow.assert_not_awaited()  # con código no se resuelve ningún nombre


@pytest.mark.asyncio
async def test_codigo_y_nombre_discordantes_422() -> None:
    d = await _error(
        normalizar_dependencia(
            _conn(catalogo="Gestión Documental"), campo="dest_dept", codigo=901, nombre="Archivo"
        )
    )
    assert d["error"] == "dependencia_inconsistente"


@pytest.mark.asyncio
async def test_codigo_inexistente_o_inactivo_422() -> None:
    d = await _error(normalizar_dependencia(_conn(), campo="dest_dept", codigo=999, nombre=None))
    assert d["error"] == "dependencia_desconocida"


@pytest.mark.asyncio
async def test_alias_unico_se_resuelve_y_se_marca() -> None:
    conn = _conn(candidatos=1, codigo=901)
    assert await normalizar_dependencia(
        conn, campo="dest_dept", codigo=None, nombre="Archivo"
    ) == ("Archivo", 901, True)
    # Solo ACTIVAS con ese nombre ACTUAL (historico = false): el alias no revive nombres viejos.
    assert conn.fetchrow.await_args.args[1:] == ("Archivo",)
    assert "resolver_codigo_dependencia($1, false)" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_alias_ambiguo_422_y_nunca_elige_la_de_menor_codigo() -> None:
    """Mutación: devolver `codigo` con 2 candidatos (o `ORDER BY codigo LIMIT 1`) cae aquí."""
    d = await _error(
        normalizar_dependencia(
            _conn(candidatos=2, codigo=None), campo="dest_dept", codigo=None, nombre="Archivo"
        )
    )
    assert d["error"] == "dependencia_ambigua" and d["candidatos"] == 2


@pytest.mark.asyncio
async def test_alias_desconocido_estricto_422_y_laxo_conserva_el_texto() -> None:
    d = await _error(
        normalizar_dependencia(
            _conn(candidatos=0), campo="dest_dept", codigo=None, nombre="Nadie", estricto=True
        )
    )
    assert d["error"] == "dependencia_desconocida"
    assert await normalizar_dependencia(
        _conn(candidatos=0), campo="dest_dept", codigo=None, nombre="Un tercero", estricto=False
    ) == ("Un tercero", None, True)


@pytest.mark.asyncio
async def test_sin_nada_no_consulta() -> None:
    conn = _conn()
    assert await normalizar_dependencia(conn, campo="d", codigo=None, nombre=None) == (None, None, False)
    conn.fetchval.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_exigir_concordancia() -> None:
    await exigir_concordancia(_conn(catalogo="A"), campo="d", codigo=1, nombre="A")
    await exigir_concordancia(_conn(catalogo="A"), campo="d", codigo=None, nombre="B")  # alias: no aplica
    await exigir_concordancia(_conn(catalogo=None), campo="d", codigo=1, nombre="B")  # no existe: lo dice quien radica
    d = await _error(exigir_concordancia(_conn(catalogo="A"), campo="d", codigo=1, nombre="B"))
    assert d["error"] == "dependencia_inconsistente"


def test_el_alias_deja_cabecera_deprecation() -> None:
    r = Response()
    marcar_alias_deprecado(r, "PATCH /documents/{id}")
    assert r.headers["Deprecation"] == "true"
    assert 'rel="deprecation"' in r.headers["Link"]
    assert usa_alias(None, "Archivo") and not usa_alias(901, "Archivo") and not usa_alias(None, None)

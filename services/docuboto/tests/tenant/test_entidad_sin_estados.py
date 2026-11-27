# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una instalación, una entidad, sin estados de ciclo de vida (ADR-047 §3).

Lo único que decide si se atiende a una entidad es que exista su fila en
`public.tenants` y su schema. No hay `suspended`, `pending` ni `inactive`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from orpycamcp_common.tenant import (
    EstadoEntidad,
    entidad_publica,
    estado_entidad,
    veredicto_entidad,
)


def _conn(valor: object) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = valor
    return conn


@pytest.mark.parametrize(
    ("fila", "esperado"),
    [
        (True, EstadoEntidad.OPERATIVA),
        (False, EstadoEntidad.NO_PROVISIONADA),
        (None, EstadoEntidad.INACTIVA),
    ],
)
async def test_el_estado_sale_de_la_fila_y_del_schema(fila: object, esperado: EstadoEntidad) -> None:
    assert await estado_entidad(_conn(fila), "demo") is esperado


async def test_slug_malformado_no_consulta() -> None:
    conn = _conn(True)
    assert await estado_entidad(conn, "../x") is EstadoEntidad.INACTIVA
    conn.fetchval.assert_not_awaited()


async def test_la_consulta_no_lee_columnas_de_estado() -> None:
    conn = _conn(True)
    await estado_entidad(conn, "demo")
    sql = conn.fetchval.await_args.args[0]
    assert "status" not in sql


async def test_veredicto_niega_si_el_registro_no_responde() -> None:
    conn = AsyncMock()
    conn.fetchval.side_effect = OSError("caído")
    assert (await veredicto_entidad(conn, "demo"))[0] == 503
    assert await veredicto_entidad(_conn(True), "demo") is None
    assert (await veredicto_entidad(_conn(None), "demo"))[0] == 403


async def test_publica_solo_si_operativa() -> None:
    assert await entidad_publica(_conn(True), "demo") is True
    assert await entidad_publica(_conn(False), "demo") is False
    assert await entidad_publica(_conn(None), "demo") is False

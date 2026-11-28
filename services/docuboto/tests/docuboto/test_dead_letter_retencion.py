# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retención de los dead-letters terminales: una política para todos los grupos.

La purga retira lo resuelto o descartado de más de N días, de los cuatro grupos a
la vez, y la llama el barrido de cada entidad. Lo pendiente no se toca: el
``WHERE`` exige estado terminal (lo prueba el carril de integración).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from docuboto.core import dead_letter

pytestmark = pytest.mark.asyncio


async def test_purgar_terminales_cubre_todos_los_grupos() -> None:
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 4"
    assert await dead_letter.purgar_terminales(conn, "demo", dias=90) == 4
    sql, tenant, grupo, dias = conn.execute.await_args.args
    assert "status IN ('resolved', 'discarded')" in sql
    assert (tenant, grupo, dias) == ("demo", None, 90)


async def test_un_grupo_concreto_sigue_pudiendo_acotarse() -> None:
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 1"
    repo = dead_letter.DeadLetterRepository(conn)
    assert await repo.purge_terminal(30, "demo", "workflow-service") == 1
    assert conn.execute.await_args.args[2] == "workflow-service"


@pytest.mark.parametrize("dias", [0, -1])
async def test_una_retencion_absurda_no_purga_nada(dias) -> None:
    conn = AsyncMock()
    with pytest.raises(ValueError):
        await dead_letter.purgar_terminales(conn, "demo", dias=dias)
    conn.execute.assert_not_awaited()

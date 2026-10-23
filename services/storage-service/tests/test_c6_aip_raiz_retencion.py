# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (H4): la identidad WORM de un AIP versionado es su RAÍZ, no la fila más reciente.

El tier con Postgres real (`tests/integration/test_c6_aip_versiones_real_sql.py`) es el que ejerce
el SQL; aquí se fija lo que el mock sí puede afirmar: la forma de la consulta por tipo.
Mutación: quitar `renueva_a IS NULL` / volver a `ORDER BY created_at DESC` en la rama `aip`
hace caer estos tests Y los de integración.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories.preservacion_worm_repository import PreservacionWormRepository


def _repo() -> tuple[PreservacionWormRepository, AsyncMock]:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    return PreservacionWormRepository(conn, "demo"), conn


@pytest.mark.asyncio
async def test_aip_se_resuelve_por_la_raiz() -> None:
    repo, conn = _repo()
    await repo.get_by_expediente_and_tipo("EXP-1", "aip")
    sql = conn.fetchrow.await_args.args[0]
    assert "renueva_a IS NULL" in sql
    assert "DESC" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", ["indice", "acta_transferencia"])
async def test_el_resto_de_tipos_conserva_la_mas_reciente(tipo: str) -> None:
    repo, conn = _repo()
    await repo.get_by_expediente_and_tipo("EXP-1", tipo)
    sql = conn.fetchrow.await_args.args[0]
    assert "created_at DESC" in sql and "renueva_a IS NULL" not in sql

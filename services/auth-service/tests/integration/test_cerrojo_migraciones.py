# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El cerrojo global de migraciones contra Postgres REAL (C19, ADR-041).

Dos sesiones, un cerrojo: la primera lo toma, la segunda espera hasta su plazo y falla
con un error legible, y cuando la primera lo suelta la segunda entra. **No aplica
migraciones ni escribe datos** (no ensucia la pila de desarrollo). Vive en el carril de
auth-service porque `shared/` no tiene tier de integración propio en CI.

Mutaciones que deben caer (descritas, no ejecutadas): cambiar `pg_try_advisory_lock` por
una constante distinta en cada llamada (la segunda sesión «entra» con la primera dentro) o
quitar el `pg_advisory_unlock` del `finally` (la segunda nunca entra tras salir la primera).
"""

from __future__ import annotations

import asyncpg
import pytest
from orpycamcp_common import migrations as ledger

from tests.integration.conftest import RUN_INTEGRATION, _require_dsn

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]


async def _dos_sesiones() -> tuple[asyncpg.Connection, asyncpg.Connection]:
    dsn = _require_dsn()
    return await asyncpg.connect(dsn=dsn), await asyncpg.connect(dsn=dsn)


async def test_la_segunda_sesion_espera_y_falla_con_un_error_legible() -> None:
    a, b = await _dos_sesiones()
    try:
        async with ledger.cerrojo(a, timeout_s=5):
            with pytest.raises(RuntimeError, match="otra migración tiene el cerrojo") as exc:
                async with ledger.cerrojo(b, timeout_s=1):
                    pytest.fail("la segunda sesión entró con la primera dentro")  # pragma: no cover
            assert "pid" in str(exc.value) and "MIGRATIONS_LOCK_TIMEOUT_S" in str(exc.value)
            # control positivo: el cerrojo es visible en pg_locks mientras A lo tiene
            visible = await b.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND ((classid::bigint << 32) | objid::bigint) = $1",
                ledger.CERROJO_MIGRACIONES,
            )
            assert visible == 1
    finally:
        await a.close()
        await b.close()


async def test_al_soltarlo_la_segunda_sesion_entra() -> None:
    a, b = await _dos_sesiones()
    try:
        async with ledger.cerrojo(a, timeout_s=5):
            pass
        async with ledger.cerrojo(b, timeout_s=1):
            dentro = True
        assert dentro
    finally:
        await a.close()
        await b.close()


async def test_se_suelta_aunque_el_cuerpo_falle() -> None:
    a, b = await _dos_sesiones()
    try:
        with pytest.raises(ZeroDivisionError):
            async with ledger.cerrojo(a, timeout_s=5):
                1 / 0  # noqa: B018
        async with ledger.cerrojo(b, timeout_s=1):
            pass
    finally:
        await a.close()
        await b.close()

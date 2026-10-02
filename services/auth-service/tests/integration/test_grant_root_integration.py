# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`grant_root` contra Postgres REAL (AREA-13-07).

El script concede el privilegio más alto del sistema fuera de banda. Lo que hay
que probar es lo que un mock no puede: que el UPDATE de `is_root` y el asiento
viajan en UNA transacción (si el asiento falla, NO queda un ROOT sin traza), que
la segunda ejecución no deja otro asiento y que el dry-run no escribe.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.ops import grant_root
from migrations import runner
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]

_ACTION = "rbac.root_otorgado_fuera_de_banda"


async def _ensure_audit_log(conn: asyncpg.Connection) -> None:
    for path in runner._sql_files(runner.GLOBAL_DIR):
        await conn.execute(path.read_text())


async def _user(conn: asyncpg.Connection, username: str) -> None:
    await conn.execute("INSERT INTO auth_users (username) VALUES ($1)", username)


async def _is_root(conn: asyncpg.Connection, username: str) -> bool:
    return bool(await conn.fetchval("SELECT is_root FROM auth_users WHERE username = $1", username))


async def _asientos(conn: asyncpg.Connection, slug: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT actor, object_ref FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
        slug,
        _ACTION,
    )


async def test_la_concesion_deja_root_y_un_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)
    await _user(conn, "ROOTEABLE")

    rc = await grant_root._grant(conn, tenant=slug, username="rooteable", dry_run=False)

    assert rc == 0
    assert await _is_root(conn, "ROOTEABLE") is True
    asientos = await _asientos(conn, slug)
    assert len(asientos) == 1
    assert asientos[0]["actor"] == "system:ops-grant-root"


async def test_segunda_ejecucion_es_idempotente_sin_asiento_nuevo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)
    await _user(conn, "DOS_VECES")

    await grant_root._grant(conn, tenant=slug, username="dos_veces", dry_run=False)
    rc = await grant_root._grant(conn, tenant=slug, username="dos_veces", dry_run=False)

    assert rc == 0
    assert len(await _asientos(conn, slug)) == 1


async def test_dry_run_no_escribe_nada(tenant_ctx: tuple[str, asyncpg.Connection]) -> None:
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)
    await _user(conn, "SOLO_MIRAR")

    rc = await grant_root._grant(conn, tenant=slug, username="solo_mirar", dry_run=True)

    assert rc == 0
    assert await _is_root(conn, "SOLO_MIRAR") is False
    assert await _asientos(conn, slug) == []


async def test_si_el_asiento_falla_no_queda_un_root_sin_traza(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback conjunto: el UPDATE y el asiento son UNA transacción."""
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)
    await _user(conn, "SIN_TRAZA")

    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(grant_root._audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await grant_root._grant(conn, tenant=slug, username="sin_traza", dry_run=False)

    assert await _is_root(conn, "SIN_TRAZA") is False
    assert await _asientos(conn, slug) == []


async def test_slug_con_guion_resuelve_el_schema_con_guion_bajo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El fixture usa `itest-<hex>`: el schema real es `tenant_itest_<hex>`."""
    slug, conn = tenant_ctx
    assert "-" in slug
    assert grant_root._schema_for(slug) == f"tenant_{slug.replace('-', '_')}"
    await _ensure_audit_log(conn)
    await _user(conn, "CON_GUION")
    assert await grant_root._grant(conn, tenant=slug, username="con_guion", dry_run=False) == 0
    assert await _is_root(conn, "CON_GUION") is True


@pytest.mark.parametrize("malo", ["", "Mayus", "con espacio", "x;drop", "-empieza"])
async def test_tenant_invalido_aborta(malo: str) -> None:
    with pytest.raises(SystemExit):
        grant_root._schema_for(malo)

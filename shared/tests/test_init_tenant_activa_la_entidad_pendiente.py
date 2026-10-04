# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-10-13: `POST /tenants` deja la entidad `pending`; `init_tenant` la activa.

Si esta rama se pierde, una entidad registrada por la API queda `pending` para
siempre y TODOS los jobs (que filtran `provisioning_status='active'`) la ignoran
sin error alguno.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "init_tenant.py"


def _modulo() -> Any:
    pytest.importorskip("asyncpg")
    spec = importlib.util.spec_from_file_location("init_tenant_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    def __init__(self, existing: dict[str, Any] | None, status: str = "UPDATE 1") -> None:
        self.existing = existing
        self.status = status
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrows: list[str] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.fetchrows.append(sql)
        if "SELECT id, slug" in sql:
            return self.existing
        return {"id": "nuevo"}

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return self.status


@pytest.mark.asyncio
async def test_entidad_pendiente_pasa_a_active() -> None:
    conn = _Conn({"id": "x", "slug": "demo"})
    await _modulo().register_tenant(conn, "demo", "Demo", "DEMO")
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "provisioning_status = 'active'" in sql and "<> 'active'" in sql
    assert args == ("demo",)


@pytest.mark.asyncio
async def test_entidad_ya_activa_no_se_reescribe_ni_inserta() -> None:
    conn = _Conn({"id": "x", "slug": "demo"}, status="UPDATE 0")
    await _modulo().register_tenant(conn, "demo", "Demo", "DEMO")
    assert not any("INSERT" in s for s in conn.fetchrows)


@pytest.mark.asyncio
async def test_entidad_nueva_se_inserta_sin_update() -> None:
    conn = _Conn(None)
    await _modulo().register_tenant(conn, "nueva", "Nueva", "NUEVA")
    assert conn.executed == []
    assert any("INSERT INTO public.tenants" in s for s in conn.fetchrows)

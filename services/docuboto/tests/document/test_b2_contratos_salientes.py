# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contratos salientes de B2 (ADR-031): procedencia en storage y lectura estricta en tenant."""

from __future__ import annotations

import pytest


def _repo_params(monkeypatch, *, error: Exception | None = None, params: dict | None = None):
    """`tenant.api` sin BD: conexión de mentira y repositorio con el resultado dado."""
    from contextlib import asynccontextmanager

    from tenant import api as tenant_api

    @asynccontextmanager
    async def _conn(slug):
        yield object()

    class _Repo:
        def __init__(self, conn):
            pass

        async def get_params(self):
            if error is not None:
                raise error
            return params or {}

    monkeypatch.setattr(tenant_api, "acquire_tenant", _conn)
    monkeypatch.setattr(tenant_api, "ConfigRepository", _Repo)
    return tenant_api


@pytest.mark.asyncio
async def test_params_estricto_lanza_donde_get_params_traga(monkeypatch) -> None:
    import asyncpg

    tenant_api = _repo_params(monkeypatch, error=asyncpg.PostgresError("caída"))
    with pytest.raises(tenant_api.TenantUnavailableError):
        await tenant_api.get_params_strict(tenant_slug="demo")
    assert await tenant_api.get_params(tenant_slug="demo") == {}


@pytest.mark.asyncio
async def test_params_estricto_devuelve_los_parametros(monkeypatch) -> None:
    tenant_api = _repo_params(monkeypatch, params={"a": "b"})
    assert await tenant_api.get_params_strict(tenant_slug="demo") == {"a": "b"}

# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B10 ítem 3 (unitarias): orquestación del barrido y docstring de ``ingestion.py``."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.jobs import reconciliacion_nivel
from app.services import ingestion


@pytest.mark.asyncio
async def test_el_fallo_de_un_tenant_no_aborta_el_resto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reconciliacion_nivel, "get_pool", MagicMock())
    monkeypatch.setattr(
        reconciliacion_nivel, "list_active_tenants", AsyncMock(return_value=["a", "b", "c"])
    )

    @contextlib.asynccontextmanager
    async def _tenant(slug):
        yield slug

    monkeypatch.setattr(reconciliacion_nivel, "acquire_tenant", _tenant)

    async def _reconciliar(conn, slug):
        if slug == "b":
            raise RuntimeError("tenant roto")
        return {"subidos": 1, "tombstoneados": 0, "sobreclasificados": 0}

    monkeypatch.setattr(reconciliacion_nivel, "reconciliar_tenant", _reconciliar)

    resumen = await reconciliacion_nivel.reconciliar_una_vez()

    assert sorted(resumen) == ["a", "c"]


def test_el_docstring_de_ingestion_ya_no_niega_la_red_de_occurred_at() -> None:
    """Decía «NO compara occurred_at» y «no existe aún un barrido»: ambas cosas existen."""
    doc = ingestion.__doc__ or ""
    assert "red adicional NO construida" not in doc
    assert "no existe aún un barrido" not in doc
    assert "reconciliacion_nivel" in doc

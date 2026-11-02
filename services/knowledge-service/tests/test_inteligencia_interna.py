# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las rutas internas exigen el token y no salen por el gateway."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.internal_auth import require_internal_token
from app.routers import inteligencia


class _Peticion:
    def __init__(self, slug: str) -> None:
        self.headers = {"X-Tenant-Slug": slug}


def test_sin_token_configurado_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "intelligence_internal_token", "")
    with pytest.raises(HTTPException) as exc:
        require_internal_token("algo")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"] == "internal_access_not_configured"


def test_un_token_distinto_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "intelligence_internal_token", "secreto")
    with pytest.raises(HTTPException) as exc:
        require_internal_token("otro")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_la_purga_interna_no_repite_niveles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ppi_provider_url", "")

    class _Pedido:
        reason = "host_request"
        actor = uuid.uuid4()
        levels = [2, 2]

    with pytest.raises(HTTPException) as exc:
        await inteligencia.purge(_Pedido(), _Peticion("demo"), object())
    assert exc.value.status_code == 422

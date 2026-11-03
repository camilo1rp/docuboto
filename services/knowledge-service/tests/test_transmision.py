# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /transmission` nombra la ruta antes de que salga una pregunta."""

from __future__ import annotations

from typing import Any

import pytest

from knowledge.core.aceptacion import Declaracion
from knowledge.core.paso import Politica
from knowledge.routers import knowledge as rutas
from tests.conftest import TENANT_HEADERS

_APAGADA = {"transmits": False, "provider": None, "levels": []}


def _politica(motivo: str | None, niveles: tuple[int, ...] = (1, 2)) -> Politica:
    return Politica(motivo, niveles, False, motivo is None, "ppi", motivo == "suspendido", True)


def _vista() -> dict[str, Any]:
    return {
        "provider": {"id": "example-provider", "name": "Example Provider"},
        "subprocessors": [{"id": "host-a", "name": "Example Host"}],
        "routing": [
            {
                "level": "public",
                "hosting": "installation",
                "model_aliases": ["triage-small"],
                "subprocessors": [],
            },
            {
                "level": "reserved",
                "hosting": "hosted",
                "model_aliases": ["triage-large"],
                "subprocessors": ["host-a"],
            },
        ],
        "sha256": "ab" * 32,
    }


def _fijar(
    monkeypatch: pytest.MonkeyPatch,
    politica: Politica,
    vista: dict[str, Any] | None,
) -> list[int]:
    llamadas: list[int] = []

    async def leer_politica(*_args: Any, **_kwargs: Any) -> Politica:
        return politica

    async def leer(_cliente: Any) -> Declaracion:
        llamadas.append(1)
        if vista is None:
            return Declaracion(False, "proveedor", None)
        return Declaracion(True, "ok", vista)

    monkeypatch.setattr(rutas.paso, "leer_politica", leer_politica)
    monkeypatch.setattr(rutas.aceptacion, "leer", leer)
    return llamadas


async def test_nombra_solo_la_ruta_del_nivel_habilitado(client_with_conn, monkeypatch) -> None:
    client, _conn = client_with_conn
    _fijar(monkeypatch, _politica(None, (1,)), _vista())
    resp = await client.get("/api/v1/knowledge/transmission", headers=TENANT_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "transmits": True,
        "provider": {"id": "example-provider", "name": "Example Provider"},
        "levels": [
            {
                "level": 1,
                "hosting": "installation",
                "model_aliases": ["triage-small"],
                "subprocessors": [],
            }
        ],
    }


async def test_en_local_no_llama_al_proveedor(client_with_conn, monkeypatch) -> None:
    client, _conn = client_with_conn
    politica = Politica("local", (1,), False, True, "local", False, True)
    llamadas = _fijar(monkeypatch, politica, _vista())
    resp = await client.get("/api/v1/knowledge/transmission", headers=TENANT_HEADERS)
    assert resp.json() == _APAGADA
    assert llamadas == []


async def test_suspendida_no_llama_al_proveedor(client_with_conn, monkeypatch) -> None:
    client, _conn = client_with_conn
    llamadas = _fijar(monkeypatch, _politica("suspendido"), _vista())
    resp = await client.get("/api/v1/knowledge/transmission", headers=TENANT_HEADERS)
    assert resp.json() == _APAGADA
    assert llamadas == []


async def test_sin_declaracion_no_inventa_la_ruta(client_with_conn, monkeypatch) -> None:
    client, _conn = client_with_conn
    _fijar(monkeypatch, _politica(None), None)
    resp = await client.get("/api/v1/knowledge/transmission", headers=TENANT_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "ruta_no_disponible"

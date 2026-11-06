# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La suspensión se levanta cuando cada nivel habilitado tiene la huella."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from knowledge.core.aceptacion import Declaracion, aceptar, rutas_de, vista

SHA = "ab" * 32
OTRA = "cd" * 32


def _statement() -> dict[str, Any]:
    return {
        "provider": {"id": "example-provider", "name": "Example Provider"},
        "subprocessors": [{"id": "model-host-a", "name": "Example Model Host"}],
        "routing": [
            {
                "level": "public",
                "hosting": "installation",
                "model_aliases": ["triage-small"],
                "subprocessors": [],
                "prompt_and_output_retention": "none",
                "trains_on_customer_data": False,
            }
        ],
    }


def test_rutas_de_deja_solo_el_nivel_habilitado() -> None:
    statement = _statement()
    statement["routing"].append(
        {
            "level": "reserved",
            "hosting": "hosted",
            "model_aliases": ["triage-large"],
            "subprocessors": ["model-host-a"],
            "prompt_and_output_retention": "none",
            "trains_on_customer_data": False,
        }
    )
    normal = vista(statement)
    assert normal is not None
    assert rutas_de(normal, [1]) == [
        {
            "level": 1,
            "hosting": "installation",
            "model_aliases": ["triage-small"],
            "subprocessors": [],
        }
    ]
    reservada = rutas_de(normal, [2])
    assert reservada[0]["level"] == 2
    assert reservada[0]["subprocessors"] == [{"id": "model-host-a", "name": "Example Model Host"}]


def test_una_ruta_a_medias_no_es_vista() -> None:
    statement = _statement()
    assert vista(statement) is not None
    statement["routing"][0].pop("hosting")
    assert vista(statement) is None


class _Conn:
    def __init__(self, niveles: list[int]) -> None:
        self.niveles = niveles
        self.aceptados: dict[int, str] = {}
        self.fijado: str | None = None
        self.en_tx = False

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None, None]:
        self.en_tx = True
        try:
            yield
        finally:
            self.en_tx = False

    async def execute(self, sql: str, *args: object) -> str:
        assert self.en_tx
        if "INSERT INTO intelligence_level_acceptance" in sql:
            self.aceptados[int(args[0])] = str(args[1])  # type: ignore[arg-type]
        elif "intelligence_state" in sql:
            self.fijado = str(args[0])
        return "OK"

    async def fetchrow(self, sql: str, *_args: object) -> dict[str, Any]:
        assert self.en_tx and "public.tenants" in sql
        return {"intelligence_levels": self.niveles}

    async def fetch(self, sql: str, *_args: object) -> list[dict[str, Any]]:
        assert self.en_tx and "intelligence_level_acceptance" in sql
        return [
            {"level": nivel, "capabilities_sha256": huella}
            for nivel, huella in self.aceptados.items()
        ]


def _leer(sha: str, niveles: tuple[str, ...] = ("public", "reserved")) -> Any:
    async def _impl(_conn: object, _slug: str, _cliente: object) -> Declaracion:
        return Declaracion(
            True,
            "ok",
            {
                "sha256": sha,
                "routing": [{"level": nivel} for nivel in niveles],
            },
        )

    return _impl


@pytest.mark.asyncio
async def test_no_levanta_hasta_que_cada_nivel_habilitado_coincide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("knowledge.core.aceptacion.leer", _leer(SHA))
    conn = _Conn([1, 2])
    assert await aceptar(conn, "demo", object(), nivel=1, sha256=SHA) == ""
    assert conn.fijado is None
    assert await aceptar(conn, "demo", object(), nivel=2, sha256=SHA) == ""
    assert conn.fijado == SHA


@pytest.mark.asyncio
async def test_una_huella_distinta_no_se_anota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("knowledge.core.aceptacion.leer", _leer(OTRA))
    conn = _Conn([1])
    assert await aceptar(conn, "demo", object(), nivel=1, sha256=SHA) == "distinta"
    assert conn.aceptados == {}
    assert conn.fijado is None

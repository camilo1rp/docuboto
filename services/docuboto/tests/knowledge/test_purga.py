# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El borrado de lo ya transmitido no mira el interruptor (ADR-045 §4)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from knowledge.core import paso
from knowledge.core.seudonimo import referenciar

ACTOR = uuid.UUID("00000000-0000-4000-8000-000000000001")
CLAVE = b"k" * 32


class _Conn:
    def __init__(self) -> None:
        self.libro: list[tuple[Any, ...]] = []

    async def fetchrow(self, *_a: object) -> None:
        raise AssertionError("la purga no lee el interruptor")

    async def execute(self, sql: str, *a: object) -> str:
        assert "intelligence_send_log" in sql
        self.libro.append(a)
        return "INSERT 1"


class _Cliente:
    destino = "http://ppi.internal/v1/ingest"
    destino_purga = "http://ppi.internal/v1/ingest/purge"

    def __init__(self, *, falla: bool = False) -> None:
        self.falla = falla
        self.cuerpos: list[dict[str, Any]] = []
        self.capacidades_llamadas = 0

    async def purgar(self, cuerpo: dict[str, Any]) -> dict[str, str]:
        if self.falla:
            raise RuntimeError("caído")
        self.cuerpos.append(cuerpo)
        return {"purge_ref": "p-1"}

    async def capacidades(self) -> dict[str, str]:
        self.capacidades_llamadas += 1
        return {"statement": {}}


@pytest.fixture
def clave(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _abrir(_conn: object, _slug: str) -> bytes:
        return CLAVE

    monkeypatch.setattr(paso, "abrir_seudonimo", _abrir)


@pytest.mark.asyncio
async def test_retirar_borra_toda_la_entidad_aunque_este_en_local(clave: None) -> None:
    conn = _Conn()
    cliente = _Cliente()
    respuesta = await paso.purgar(
        conn, "demo", cliente, razon="withdrawn", actor=ACTOR, niveles=None
    )
    assert respuesta.sale is True
    assert respuesta.cuerpo == {"purge_ref": "p-1"}
    assert "levels" not in cliente.cuerpos[0]
    assert cliente.cuerpos[0]["reason"] == "withdrawn"
    assert cliente.cuerpos[0]["requested_by"] == referenciar(CLAVE, ACTOR)
    assert conn.libro[0][0] == "purge"
    assert conn.libro[0][1] == "tenant"
    assert conn.libro[0][2] == 1
    assert conn.libro[0][5] == "accepted"


@pytest.mark.asyncio
async def test_deshabilitar_borra_solo_ese_nivel(clave: None) -> None:
    conn = _Conn()
    cliente = _Cliente()
    respuesta = await paso.purgar(
        conn, "demo", cliente, razon="host_request", actor=ACTOR, niveles=(2,)
    )
    assert respuesta.sale is True
    assert cliente.cuerpos[0]["levels"] == ["reserved"]
    assert conn.libro[0][1] == "level:reserved"
    assert conn.libro[0][2] == 2


@pytest.mark.asyncio
async def test_si_el_proveedor_cae_el_libro_anota_el_fallo(clave: None) -> None:
    conn = _Conn()
    respuesta = await paso.purgar(
        conn, "demo", _Cliente(falla=True), razon="withdrawn", actor=ACTOR, niveles=None
    )
    assert respuesta.sale is False
    assert respuesta.motivo == "proveedor"
    assert conn.libro[0][5] == "error"


@pytest.mark.asyncio
async def test_sin_url_no_hay_llamada() -> None:
    class _Vacio:
        destino = ""
        destino_purga = ""

        async def purgar(self, _cuerpo: dict[str, Any]) -> None:
            raise AssertionError("no debía llamar")

    respuesta = await paso.purgar(
        _Conn(), "demo", _Vacio(), razon="withdrawn", actor=ACTOR, niveles=None
    )
    assert respuesta.sale is False
    assert respuesta.motivo == "destino"


@pytest.mark.asyncio
async def test_leer_la_declaracion_no_exige_el_interruptor() -> None:
    cliente = _Cliente()
    respuesta = await paso.leer_declaracion(object(), "demo", cliente)
    assert respuesta.sale is True
    assert cliente.capacidades_llamadas == 1

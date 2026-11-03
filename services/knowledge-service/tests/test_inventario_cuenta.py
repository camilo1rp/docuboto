# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El conteo del inventario no mira el interruptor y no transmite registros.

La pantalla de administración lo pide con la entidad en ``local``. Cuenta
radicados, no anexos, y se detiene si el cursor no termina.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from knowledge.core.paso import contar_radicados
from knowledge.routers import inteligencia


class _Cliente:
    def __init__(self, paginas: list[dict]) -> None:
        self.paginas = list(paginas)
        self.destino = "http://ppi.example/v1/ingest"
        self.llamadas: list[tuple[str | None, int]] = []

    async def inventario(self, *, cursor: str | None, limite: int) -> dict:
        self.llamadas.append((cursor, limite))
        return self.paginas.pop(0)


def _pagina(items: list[dict], cursor: str | None) -> dict:
    return {"items": items, "next_cursor": cursor}


@pytest.mark.asyncio
async def test_cuenta_radicados_y_no_anexos() -> None:
    cliente = _Cliente(
        [
            _pagina(
                [{"type": "record"}, {"type": "attachment"}, {"type": "record"}],
                "c1",
            ),
            _pagina([{"type": "attachment"}], None),
        ]
    )
    resultado = await contar_radicados(cliente)
    assert resultado.sale is True
    assert resultado.cuerpo == {"records": 2, "complete": True}
    assert cliente.llamadas == [(None, 1000), ("c1", 1000)]


@pytest.mark.asyncio
async def test_sin_destino_no_llama() -> None:
    cliente = _Cliente([])
    cliente.destino = ""
    resultado = await contar_radicados(cliente)
    assert resultado.sale is False
    assert resultado.motivo == "destino"
    assert cliente.llamadas == []


@pytest.mark.asyncio
async def test_un_cursor_repetido_no_sigue() -> None:
    cliente = _Cliente([_pagina([{"type": "record"}], "c1"), _pagina([], "c1")])
    resultado = await contar_radicados(cliente)
    assert resultado.sale is False
    assert resultado.motivo == "proveedor"


@pytest.mark.asyncio
async def test_el_tope_deja_la_cuenta_incompleta() -> None:
    cliente = _Cliente(
        [
            _pagina([{"type": "record"}], "c1"),
            _pagina([{"type": "record"}], "c2"),
            _pagina([{"type": "record"}], "c3"),
        ]
    )
    resultado = await contar_radicados(cliente, tope_paginas=2)
    assert resultado.cuerpo == {"records": 2, "complete": False}
    assert len(cliente.llamadas) == 2


@pytest.mark.asyncio
async def test_una_pagina_mal_formada_no_cuenta() -> None:
    cliente = _Cliente([{"items": [{"type": "nota"}], "next_cursor": None}])
    resultado = await contar_radicados(cliente)
    assert resultado.sale is False


class _Peticion:
    def __init__(self) -> None:
        self.headers = {"X-Tenant-Slug": "demo"}


@pytest.mark.asyncio
async def test_la_ruta_interna_devuelve_la_cuenta(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _Cliente([_pagina([{"type": "record"}], None)])
    monkeypatch.setattr(inteligencia, "_cliente", lambda: cliente)
    cuerpo = await inteligencia.inventory(_Peticion(), object())
    assert cuerpo == {"records": 1, "complete": True}


@pytest.mark.asyncio
async def test_la_ruta_interna_no_inventa_un_cero(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _Cliente([])
    cliente.destino = ""
    monkeypatch.setattr(inteligencia, "_cliente", lambda: cliente)
    with pytest.raises(HTTPException) as exc:
        await inteligencia.inventory(_Peticion(), object())
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "inventario_no_disponible"

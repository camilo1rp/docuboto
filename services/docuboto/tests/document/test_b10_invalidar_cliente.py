# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 ítem 2: la invalidación pide a firma la huella VIGENTE como predicado.

Se invalida lo que NO recae sobre ella; sin la huella un aviso tardío tiraría el
circuito abierto sobre el texto que sí está vigente. Y es best-effort: un fallo
devuelve -1, nunca impide editar.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from signature import api as signature_api

HUELLA = "c" * 64


@asynccontextmanager
async def _conn(*_a, **_k):
    yield MagicMock()


@pytest.mark.asyncio
async def test_invalidar_circuitos_pasa_la_huella_vigente(monkeypatch) -> None:
    nucleo = AsyncMock(return_value=2)
    monkeypatch.setattr(signature_api, "_tenant_conn", _conn)
    monkeypatch.setattr(signature_api.invalidacion, "invalidar_por_contenido", nucleo)

    n = await signature_api.invalidar_circuitos(
        borrador_id="b-1", tenant_slug="demo", cuerpo_sha256_vigente=HUELLA
    )

    assert n == 2
    kw = nucleo.await_args.kwargs
    assert kw["objeto_tipo"] == "borrador" and kw["objeto_id"] == "b-1"
    assert kw["vigente"] == HUELLA and kw["origen"] == "sincrono"


@pytest.mark.asyncio
async def test_invalidar_circuitos_es_best_effort(monkeypatch) -> None:
    monkeypatch.setattr(signature_api, "_tenant_conn", _conn)
    monkeypatch.setattr(
        signature_api.invalidacion,
        "invalidar_por_contenido",
        AsyncMock(side_effect=RuntimeError("BD caída")),
    )
    assert (
        await signature_api.invalidar_circuitos(
            borrador_id="b-1", tenant_slug="demo", cuerpo_sha256_vigente=HUELLA
        )
        == -1
    )

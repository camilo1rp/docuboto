# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 ítem 2: el cliente de invalidación manda la huella VIGENTE en el cuerpo.

signature-service responde 422 sin ella (``cuerpo_sha256_vigente`` es obligatorio),
así que el cliente —best-effort— no puede omitirla.
"""

from __future__ import annotations

import httpx
import pytest

from app.clients import signature_client

HUELLA = "c" * 64


@pytest.mark.asyncio
async def test_invalidar_circuitos_manda_la_huella_vigente_en_el_cuerpo(monkeypatch) -> None:
    vistos: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json={"invalidadas": 2})

    transporte = httpx.MockTransport(_handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        signature_client.httpx,
        "AsyncClient",
        lambda **kw: real(transport=transporte, **kw),
    )

    n = await signature_client.invalidar_circuitos("b-1", "demo", HUELLA)

    assert n == 2
    assert len(vistos) == 1
    assert vistos[0].url.path.endswith("/internal/circuitos/borrador/b-1/invalidar")
    assert vistos[0].read() == b'{"cuerpo_sha256_vigente":"' + HUELLA.encode() + b'"}'

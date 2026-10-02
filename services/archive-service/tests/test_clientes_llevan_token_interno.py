# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Contrato saliente de archive hacia el sello y el WORM (AREA-04-07, AREA-08-16).

signature-service exige `X-Internal-Token` en `/sign-xml` para el sello
institucional y storage-service en `/proteger-indice`, `/proteger-artefacto` y
`/aip`. Si archive dejara de enviarlo, el sellado del índice al cerrar caería a
`pendiente_firma` (best-effort) y la reconciliación lo repetiría sin fin: un
fallo permanente disfrazado de transitorio. Este test mira la PETICIÓN
SALIENTE, no el método aislado.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.clients import signature_client, storage_client
from app.core.config import settings

_SIG = "tok-firma"
_STO = "tok-storage"


@pytest.fixture
def capturadas(monkeypatch):
    peticiones: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        return httpx.Response(500, json={})

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "signature_internal_token", _SIG)
    monkeypatch.setattr(settings, "storage_internal_token", _STO)
    return peticiones


@pytest.mark.asyncio
async def test_sign_xml_lleva_token_interno_e_identidad(capturadas) -> None:
    await signature_client.sign_xml(
        tenant_slug="demo", actor_id="u", payload_xml="<x/>",
        objeto_id="e", indice_id="i", version=1,
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/sign-xml")
    assert req.headers["X-Internal-Token"] == _SIG
    assert "X-User-Id" in req.headers


@pytest.mark.asyncio
async def test_sign_xml_outcome_lleva_token_interno(capturadas) -> None:
    await signature_client.sign_xml_outcome(
        tenant_slug="demo", actor_id="u", payload_xml="<x/>",
        objeto_id="e", indice_id=None, version=1, objeto_tipo="acta_transferencia",
    )
    req = capturadas[-1]
    assert req.headers["X-Internal-Token"] == _SIG


@pytest.mark.asyncio
async def test_proteger_indice_worm_lleva_token_y_no_declara_legal_hold(capturadas) -> None:
    await storage_client.proteger_indice_worm(
        tenant_slug="demo", actor_id="u", file_id="f", expediente_id="e",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/proteger-indice")
    assert req.headers["X-Internal-Token"] == _STO
    assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_proteger_artefacto_worm_lleva_token(capturadas) -> None:
    await storage_client.proteger_artefacto_worm(
        tenant_slug="demo", actor_id="u", file_id="f", expediente_id="e",
        tipo="acta_transferencia", retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/proteger-artefacto")
    assert req.headers["X-Internal-Token"] == _STO
    assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_gemelas_internas_llevan_token_y_ninguna_declara_legal_hold(capturadas) -> None:
    """Los 3 caminos de SISTEMA (reconciliacion): token en la peticion y sin
    `legal_hold` (storage lo rechaza con 422: un cliente que lo enviara
    quedaria en 422 permanente disfrazado de transitorio)."""
    await storage_client.proteger_indice_worm_internal(
        tenant_slug="demo", file_id="f", expediente_id="e",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    await storage_client.proteger_artefacto_worm_internal(
        tenant_slug="demo", file_id="f", expediente_id="e", tipo="acta_transferencia",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    await storage_client.empaquetar_aip_internal(
        tenant_slug="demo", expediente_id="e",
        documentos=[{"document_id": "R", "nombre": "a.pdf", "file_id": "f"}],
        indice_file_id=None, retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    assert len(capturadas) == 3
    for req in capturadas:
        assert req.url.path.endswith("-internal"), req.url.path
        assert req.headers["X-Internal-Token"] == _STO
        assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_sign_indice_internal_lleva_token(capturadas) -> None:
    await signature_client.sign_indice_internal(
        tenant_slug="demo", payload_xml="<x/>", objeto_id="e", indice_id=None, version=1,
    )
    assert capturadas[-1].headers["X-Internal-Token"] == _SIG


@pytest.mark.asyncio
async def test_fijar_legal_hold_lleva_token_identidad_y_va_a_la_ruta_interna(capturadas) -> None:
    with pytest.raises(storage_client.StorageLegalHoldError):
        await storage_client.fijar_legal_hold_internal(
            tenant_slug="demo",
            actor_id="u",
            expediente_id="e",
            activo=True,
            motivo="Orden del juzgado 12",
            acta_ref="A-1",
        )
    (req,) = capturadas
    assert req.url.path == "/api/v1/preservacion/legal-hold-internal"
    assert req.headers["x-internal-token"] == _STO
    assert "x-user-id" in req.headers and "x-tenant-slug" in req.headers
    assert json.loads(req.content)["acta_ref"] == "A-1"

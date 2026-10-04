# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato saliente de archive hacia document-service al VINCULAR (T6, AREA-07-02).

El carril unit sustituye `fetch_radicado_metadata_internal` por un doble
(fixture autouse de `conftest.py`): eso prueba la lógica del cotejo, pero deja
sin probar el cable. Si el cliente real dejara de enviar `X-Internal-Token`, o
document-service respondiera 401/500, el cotejo caería a 503 PERMANENTE y
vincular quedaría bloqueado para todos: un fallo permanente que ningún test con
el doble vería. Aquí se usa el cliente REAL contra un `MockTransport`, y el
servicio real encima del cliente real.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.clients import document_client
from app.core.config import settings
from app.schemas.archive import RadicadoLink
from app.services.archive_service import ArchiveService

# Capturada al importar el módulo: el autouse de conftest solo parchea durante
# cada test, así que esta es la función REAL.
_REAL = document_client.fetch_radicado_metadata_internal

_TOKEN = "tok-documentos"
_RID = uuid.uuid4()
_TRACKING = "2026-DEMO-E-000042"


@pytest.fixture
def cable(monkeypatch):
    """Sustituye el transporte HTTP; devuelve (peticiones, fijar_respuesta)."""
    peticiones: list[httpx.Request] = []
    estado: dict = {"resp": httpx.Response(200, json={})}

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        r = estado["resp"]
        if isinstance(r, Exception):
            raise r
        return r

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "document_internal_token", _TOKEN)

    def fijar(resp):
        estado["resp"] = resp

    return peticiones, fijar


@pytest.mark.asyncio
async def test_el_cliente_lleva_token_interno_tenant_y_sin_recorte_de_clearance(cable) -> None:
    peticiones, fijar = cable
    fijar(httpx.Response(200, json={str(_RID): {"tracking_number": _TRACKING}}))

    out = await _REAL(
        tenant_slug="demo", radicado_ids=[_RID], max_clearance=3
    )

    req = peticiones[-1]
    assert req.url.path == "/api/v1/internal/documentos/metadata"
    assert req.headers["X-Internal-Token"] == _TOKEN
    assert req.headers["X-Tenant-Slug"] == "demo"
    cuerpo = json.loads(req.content)
    assert cuerpo == {"radicado_ids": [str(_RID)], "max_clearance": 3}
    assert out[str(_RID)]["tracking_number"] == _TRACKING


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [
    httpx.Response(401, json={}),
    httpx.Response(500, json={}),
    httpx.ConnectError("caido"),
])
async def test_fallo_del_cable_es_error_del_cliente_y_vincular_responde_503(cable, resp) -> None:
    """401 (token mal puesto), 500 y red caída: todos `DocumentClientError`, y el
    servicio REAL —sin el doble— responde 503, no 500 ni un vínculo sin cotejar."""
    _, fijar = cable
    fijar(resp)
    with pytest.raises(document_client.DocumentClientError):
        await _REAL(tenant_slug="demo", radicado_ids=[_RID], max_clearance=3)

    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal", _REAL
    ), patch.object(svc, "_huellas_por_radicado", AsyncMock(return_value={})):
        with pytest.raises(HTTPException) as exc:
            await svc._resolver_metadatos_vinculo(
                [RadicadoLink(radicado_id=_RID, tracking_number=_TRACKING)],
                expediente_code="EXP-1",
                actor=uuid.uuid4(),
                clearance=3,
            )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "document_service_no_disponible"


@pytest.mark.asyncio
async def test_una_respuesta_de_un_document_service_anterior_sin_tracking_es_503(cable) -> None:
    """Orden de despliegue: archive nuevo contra document-service viejo (sin
    `tracking_number`) falla cerrado; NO cae a «lo que diga el cuerpo»."""
    _, fijar = cable
    fijar(httpx.Response(200, json={str(_RID): {"subject": "s", "doc_type": "E"}}))
    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal", _REAL
    ), patch.object(svc, "_huellas_por_radicado", AsyncMock(return_value={})):
        with pytest.raises(HTTPException) as exc:
            await svc._resolver_metadatos_vinculo(
                [RadicadoLink(radicado_id=_RID, tracking_number=_TRACKING)],
                expediente_code="EXP-1",
                actor=None,
                clearance=3,
            )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_con_la_respuesta_real_de_document_service_se_resuelve_todo(cable) -> None:
    """Respuesta con la forma exacta de `RadicadoMetaItem` de document-service:
    el cotejo resuelve número, tipología, productora, formato y tamaño."""
    _, fijar = cable
    fijar(httpx.Response(200, json={str(_RID): {
        "subject": "s", "doc_type": "E", "tracking_number": _TRACKING,
        "doc_class": "COR-PET", "origin_dept": "Juridica", "origin_dept_code": 120,
        "formato": "application/pdf", "tamano_bytes": 2048,
    }}))
    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal", _REAL
    ), patch.object(svc, "_huellas_por_radicado", AsyncMock(return_value={str(_RID): "ab" * 32})):
        out = await svc._resolver_metadatos_vinculo(
            [RadicadoLink(radicado_id=_RID, tracking_number=_TRACKING)],
            expediente_code="EXP-1",
            actor=None,
            clearance=3,
        )
    r = out[str(_RID)]
    assert (r["tracking_number"], r["tipologia"], r["dep_codigo"], r["dep_nombre"]) == (
        _TRACKING, "COR-PET", 120, "Juridica",
    )
    assert (r["formato"], r["tamano_bytes"], r["content_hash"]) == ("application/pdf", 2048, "ab" * 32)

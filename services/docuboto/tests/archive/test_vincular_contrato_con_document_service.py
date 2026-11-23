# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato de archive con document al VINCULAR (T6, AREA-07-02).

El carril unit sustituye `document.api.fetch_radicado_metadata` por un doble
(fixture autouse de `conftest.py`): eso prueba la lógica del cotejo, pero deja
sin probar la función real. Si document dejara de devolver el número, o fallara,
el cotejo caería a 503 PERMANENTE y vincular quedaría bloqueado para todos. Aquí
se usa la función REAL de `document.api` sobre un repositorio doblado, y el
servicio real encima.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from archive.schemas.archive import RadicadoLink
from archive.services.archive_service import ArchiveService
from document import api as document_api

# Capturada al importar el módulo: el autouse de conftest solo parchea durante
# cada test, así que esta es la función REAL.
_REAL = document_api.fetch_radicado_metadata

_RID = uuid.uuid4()
_TRACKING = "2026-DEMO-E-000042"


@pytest.fixture
def cable(monkeypatch):
    """Repositorio de document doblado; devuelve (llamadas, fijar_respuesta)."""
    llamadas: list[tuple] = []
    estado: dict = {"resp": {}}

    @asynccontextmanager
    async def _acquire(slug: str):
        yield object()

    async def _meta(self, radicado_ids, *, max_clearance):
        llamadas.append((radicado_ids, max_clearance))
        r = estado["resp"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(document_api, "acquire_tenant", _acquire)
    monkeypatch.setattr(document_api.DocumentRepository, "list_radicado_metadata_internal", _meta)

    def fijar(resp):
        estado["resp"] = resp

    return llamadas, fijar


@pytest.mark.asyncio
async def test_la_funcion_pide_el_lote_con_su_clearance(cable) -> None:
    llamadas, fijar = cable
    fijar({_RID: {"subject": "s", "doc_type": "E", "tracking_number": _TRACKING}})

    out = await _REAL(tenant_slug="demo", radicado_ids=[_RID], max_clearance=3)

    assert llamadas[-1] == ([_RID], 3)
    assert out[str(_RID)]["tracking_number"] == _TRACKING


@pytest.mark.asyncio
@pytest.mark.parametrize("resp", [asyncpg.PostgresError("caída"), OSError("sin conexión")])
async def test_un_fallo_de_document_es_503_al_vincular(cable, resp) -> None:
    """Un fallo de document sube como `DocumentUnavailableError`, y el servicio
    REAL —sin el doble— responde 503, no 500 ni un vínculo sin cotejar."""
    _, fijar = cable
    fijar(resp)
    with pytest.raises(document_api.DocumentUnavailableError):
        await _REAL(tenant_slug="demo", radicado_ids=[_RID], max_clearance=3)

    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "document.api.fetch_radicado_metadata", _REAL
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
async def test_un_radicado_sin_tracking_es_503(cable) -> None:
    """Sin `tracking_number` el cotejo falla cerrado; NO cae a «lo que diga el cuerpo»."""
    _, fijar = cable
    fijar({_RID: {"subject": "s", "doc_type": "E"}})
    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "document.api.fetch_radicado_metadata", _REAL
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
async def test_con_la_respuesta_real_de_document_se_resuelve_todo(cable) -> None:
    """Fila con la forma exacta de `RadicadoMetaItem`: el cotejo resuelve número,
    tipología, productora, formato y tamaño."""
    _, fijar = cable
    fijar({_RID: {
        "subject": "s", "doc_type": "E", "tracking_number": _TRACKING,
        "doc_class": "COR-PET", "origin_dept": "Juridica", "origin_dept_code": 120,
        "formato": "application/pdf", "tamano_bytes": 2048,
    }})
    svc = ArchiveService(AsyncMock(), "demo")
    with patch(
        "document.api.fetch_radicado_metadata", _REAL
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

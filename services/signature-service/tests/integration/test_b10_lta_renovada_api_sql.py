# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §8 — la firma LTA RENOVADA verifica por la API (`POST /verify`) y el XML original sigue
verificando; `GET /firmas/{id}` expone el artefacto vigente.

Lo que prueba: `/verify` acepta cualquier XML `vigente` de la cadena (no solo `signed_xml_sha256`)
y ancla los sellos renovados a las TSA registradas en `firma_sello_archivo`.

Mutaciones: volver a comparar solo contra `signed_xml_sha256` (cae
`test_el_xml_renovado_verifica_por_la_api`); no pasar `tsa_fingerprints_archivo` (cae el mismo, por
`archive_timestamp_tsa_no_anclada`); no exponer el artefacto vigente (cae
`test_get_firma_expone_el_artefacto_vigente`)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.jobs import renovacion_lta
from app.main import app
from app.repositories.firma_repository import FirmaRepository
from tests.integration.test_b10_lta_renovacion_sql import (  # noqa: F401  (fixtures y helpers)
    _sembrar,
    _tsa_larga,
    storage,
)
from tests.test_xmldsig_conformidad import _tsa_local  # noqa: F401  (autouse)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(tenant_ctx):
    slug, conn = tenant_ctx

    async def _dep():
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, {"X-Tenant-Slug": slug}
    app.dependency_overrides.clear()


async def _renovada(tenant_ctx, monkeypatch, storage):  # noqa: F811
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)
    assert (await renovacion_lta.renovar_tenant(conn, slug))["renovadas"] == 1
    return firma_id, xml, storage.subir.await_args.kwargs["content"]


async def test_el_xml_renovado_verifica_por_la_api(tenant_ctx, api, monkeypatch, storage) -> None:  # noqa: F811
    firma_id, original, renovado = await _renovada(tenant_ctx, monkeypatch, storage)
    client, headers = api

    for xml in (renovado, original):
        r = await client.post(
            "/api/v1/signature/verify",
            json={"firma_id": str(firma_id), "payload_xml": xml.decode()},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        assert cuerpo["valida"] is True, cuerpo
        assert cuerpo["xades_level"] == "XAdES-LTA"

    ajeno = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": renovado.decode() + " "},
        headers=headers,
    )
    assert ajeno.json()["valida"] is False
    assert ajeno.json()["motivo"] == "documento_no_corresponde_a_firma_id"


async def test_get_firma_expone_el_artefacto_vigente(tenant_ctx, api, monkeypatch, storage) -> None:  # noqa: F811
    firma_id, _o, _r = await _renovada(tenant_ctx, monkeypatch, storage)
    slug, conn = tenant_ctx
    client, headers = api
    sellos = await FirmaRepository(conn).sellos_de(firma_id)

    r = await client.get(f"/api/v1/signature/firmas/{firma_id}", headers=headers)

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["minio_key_vigente"] == sellos[1]["minio_key"]
    assert cuerpo["archive_timestamp_ordinal"] == 1
    assert cuerpo["minio_key_firmado"] == sellos[0]["minio_key"], "el original no se toca"


async def test_un_eslabon_caducado_sigue_siendo_el_artefacto_y_su_xml_se_acepta(
    tenant_ctx, api, monkeypatch, storage  # noqa: F811
) -> None:
    """El `caducado` no se esconde: la vista lo sigue sirviendo y /verify acepta su XML (con
    `lta_vigente` honesto). Mutación: quitar `'caducado'` del IN de la vista -> cae el ordinal."""
    firma_id, _o, renovado = await _renovada(tenant_ctx, monkeypatch, storage)
    slug, conn = tenant_ctx
    client, headers = api
    await conn.execute(
        "UPDATE firma_sello_archivo SET estado = 'caducado' WHERE firma_id = $1 AND ordinal = 1", firma_id
    )

    assert (await FirmaRepository(conn).artefacto_vigente(firma_id))["ordinal"] == 1
    r = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": renovado.decode()},
        headers=headers,
    )
    assert r.json()["valida"] is True and r.json()["lta_vigente"] is True


async def test_verify_degrada_a_lt_cuando_el_ultimo_sello_ya_no_prueba(
    tenant_ctx, api, monkeypatch, storage  # noqa: F811
) -> None:
    """ARCH H2 por la API: /verify publica `lta_vigente=false` y el nivel realmente verificado."""
    from tests.test_vigencia_del_certificado_y_sellos import _reloj_adelantado

    slug, conn = tenant_ctx
    client, headers = api
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    ok = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": xml.decode()}, headers=headers,
    )
    assert ok.json()["lta_vigente"] is True and ok.json()["xades_level"] == "XAdES-LTA"  # control

    _reloj_adelantado(monkeypatch, 400)  # la TSA de pruebas dura 365 d
    r = await client.post(
        "/api/v1/signature/verify",
        json={"firma_id": str(firma_id), "payload_xml": xml.decode()}, headers=headers,
    )
    cuerpo = r.json()
    assert cuerpo["valida"] is True, cuerpo
    assert cuerpo["lta_vigente"] is False and cuerpo["xades_level"] == "XAdES-LT"
    assert "archive_timestamp_caducado" in cuerpo["advertencias"]

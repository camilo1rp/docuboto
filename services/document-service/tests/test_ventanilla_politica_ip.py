# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B2 (ADR-031): política de tratamiento por entidad e IP firmada en la ventanilla.

Lo que fijan, en orden de importancia:

1. **La IP de la constancia sale de la firma del gateway**, nunca de
   `X-Forwarded-For` ni de un `X-Real-Ip` suelto: los escribe el cliente.
2. **La política la pone el servidor.** Versión vieja o ausente => 409 SIN gastar
   consecutivo; tenant-service caído => 503 (un error no es «sin política»).
3. Huella y URL de la constancia son del servidor aunque el cliente las mande.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from orpycamcp_common import identidad

from app.core.politica import huella_politica
from app.services.document_service import DocumentService
from tests.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)

POLITICA = f"{BASE}/politica"
SECRETO = "secreto-de-prueba-identidad"


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", SECRETO)


def _preparar(conn) -> None:
    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow_por_consulta()


async def _radicar(client, conn, headers=None, **cuerpo):
    _preparar(conn)
    with patch.object(
        DocumentService, "create", AsyncMock(return_value=_resultado_radicado())
    ) as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(**cuerpo)), headers=headers or {})
    return resp, crear


def _consultas(conn) -> list[str]:
    return [str(c.args[0]) for c in conn.fetchval.await_args_list if c.args]


# ---------------------------------------------------------------- IP firmada


@pytest.mark.asyncio
async def test_ip_firmada_por_el_gateway_queda_verificada(client_with_conn) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(
        client, conn, identidad.firmar_ip_publica("203.0.113.9", BASE)
    )
    assert resp.status_code == 201, resp.text
    c = crear.await_args.kwargs["procedencia"]["consentimiento"]
    assert (c["ip"], c["ip_verificada"]) == ("203.0.113.9", True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cabeceras",
    [
        {"X-Forwarded-For": "6.6.6.6, 10.0.0.1"},
        {"X-Real-Ip": "6.6.6.6"},
        {"X-Real-Ip": "6.6.6.6", "X-Real-Ip-Ts": "1", "X-Public-Ip-Signature": "00" * 32},
    ],
    ids=["xff_crudo", "x_real_ip_suelto", "firma_falsa"],
)
async def test_sin_firma_valida_la_ip_es_nula_y_no_bloquea(client_with_conn, cabeceras) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(client, conn, cabeceras)
    assert resp.status_code == 201, resp.text
    c = crear.await_args.kwargs["procedencia"]["consentimiento"]
    assert (c["ip"], c["ip_verificada"]) == (None, False)


@pytest.mark.asyncio
async def test_la_firma_de_otra_ruta_no_sirve(client_with_conn) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(
        client, conn, identidad.firmar_ip_publica("203.0.113.9", f"{BASE}/otra")
    )
    assert resp.status_code == 201
    assert crear.await_args.kwargs["procedencia"]["consentimiento"]["ip"] is None


# ------------------------------------------------------------------- Política


@pytest.mark.asyncio
async def test_la_huella_y_la_url_las_pone_el_servidor(client_with_conn) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(
        client, conn, politica_huella="x" * 64, politica_url="https://evil.example"
    )
    assert resp.status_code == 201, resp.text
    c = crear.await_args.kwargs["procedencia"]["consentimiento"]
    assert c["politica_url"] == "https://entidad.example/privacidad"
    assert c["politica_huella"] == huella_politica(
        "2026-01", "Aviso de privacidad de prueba", "https://entidad.example/privacidad"
    )


@pytest.mark.asyncio
async def test_version_vieja_es_409_y_no_gasta_consecutivo(client_with_conn) -> None:
    client, conn = client_with_conn
    resp, crear = await _radicar(client, conn, politica_version="2025-01")
    assert resp.status_code == 409
    d = resp.json()["detail"]
    assert d["error"] == "politica_desactualizada"
    assert d["politica_vigente"] == "2026-01"
    crear.assert_not_awaited()
    assert not any("tracking_sequences" in q for q in _consultas(conn))


@pytest.mark.asyncio
async def test_entidad_sin_politica_es_409_no_publicada(client_with_deps) -> None:
    client, conn, entidad = client_with_deps
    entidad.params = {"politica_tratamiento_version": "", "politica_tratamiento_url": "",
                      "politica_tratamiento_texto": ""}
    resp, crear = await _radicar(client, conn)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "politica_no_publicada"
    crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_caido_es_503_no_verificable_y_no_409(client_with_deps) -> None:
    """Un ERROR no es «sin política»: con `get_params` (que traga el fallo) esto
    sería 409 politica_no_publicada y el ciudadano creería que la entidad no tiene aviso."""
    client, conn, entidad = client_with_deps
    entidad.params_error = True
    resp, crear = await _radicar(client, conn)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "politica_no_verificable"
    crear.assert_not_awaited()
    assert not any("tracking_sequences" in q for q in _consultas(conn))


@pytest.mark.asyncio
async def test_pqrs_tambien_exige_la_politica_vigente(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    with patch.object(DocumentService, "create", AsyncMock()) as crear:
        resp = await client.post(
            "/api/v1/public/demo/pqrs",
            json={
                "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
                "acepta_tratamiento": True, "politica_version": "vieja",
            },
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "politica_desactualizada"
    crear.assert_not_awaited()


# ------------------------------------------------------------ GET /politica


@pytest.mark.asyncio
async def test_get_politica_publicada_con_huella_del_servidor(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get(POLITICA)
    assert resp.status_code == 200
    j = resp.json()
    assert j["publicada"] is True and j["version"] == "2026-01"
    assert j["huella"] == huella_politica(
        "2026-01", "Aviso de privacidad de prueba", "https://entidad.example/privacidad"
    )


@pytest.mark.asyncio
async def test_get_politica_sin_publicar(client_with_deps) -> None:
    client, _, entidad = client_with_deps
    # Versión sin URL ni texto: un aviso que no apunta a nada no informa.
    entidad.params = {
        "politica_tratamiento_version": "v1",
        "politica_tratamiento_url": "",
        "politica_tratamiento_texto": "",
    }
    j = (await client.get(POLITICA)).json()
    assert j["publicada"] is False and j["huella"] is None


@pytest.mark.asyncio
async def test_get_politica_con_tenant_caido_es_503(client_with_deps) -> None:
    client, _, entidad = client_with_deps
    entidad.params_error = True
    resp = await client.get(POLITICA)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "politica_no_verificable"


@pytest.mark.asyncio
async def test_get_politica_con_slug_invalido_es_400(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.get("/api/v1/public/Mal_Slug!/ventanilla/politica")
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_estado_informa_si_hay_politica_publicada(client_with_deps) -> None:
    client, conn, entidad = client_with_deps
    conn.fetchval.side_effect = lambda sql, *a, **k: True if "EXISTS" in sql else 1
    j = (await client.get(f"{BASE}/estado")).json()
    assert j["politica_publicada"] is True
    entidad.params = {"politica_tratamiento_version": ""}
    j = (await client.get(f"{BASE}/estado")).json()
    assert j["politica_publicada"] is False


# ------------------------------------------------- URL segura / tenant inexistente


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>", "//evil.example",
     "ftp://x.example", "https://a b", "https://x.example/<script>"],
)
async def test_una_url_no_http_se_trata_como_ausente(client_with_deps, url) -> None:
    """La URL se enlaza en la página pública: un `javascript:` sería XSS."""
    client, _, entidad = client_with_deps
    entidad.params = {"politica_tratamiento_url": url, "politica_tratamiento_texto": ""}
    j = (await client.get(POLITICA)).json()
    assert j["url"] == ""
    assert j["publicada"] is False  # sin URL válida ni texto, no informa


@pytest.mark.asyncio
async def test_una_url_https_pasa_y_con_texto_la_politica_sigue_publicada(client_with_deps) -> None:
    client, _, entidad = client_with_deps
    entidad.params = {"politica_tratamiento_url": "javascript:alert(1)"}  # texto por defecto
    j = (await client.get(POLITICA)).json()
    assert (j["url"], j["publicada"]) == ("", True)
    entidad.params = {"politica_tratamiento_url": "http://entidad.example/p"}
    assert (await client.get(POLITICA)).json()["url"] == "http://entidad.example/p"


@pytest.mark.asyncio
async def test_politica_de_tenant_inexistente_es_neutra_y_no_503(client_with_deps) -> None:
    """Existente → 200 y inexistente → 503 enumeraba entidades."""
    client, conn, entidad = client_with_deps
    conn.fetchval.side_effect = lambda sql, *a, **k: None  # to_regclass: no hay schema
    entidad.params_error = True  # ni siquiera se debe preguntar a tenant-service
    resp = await client.get(POLITICA)
    assert resp.status_code == 200
    assert resp.json()["publicada"] is False
    assert entidad.estricto_calls == 0

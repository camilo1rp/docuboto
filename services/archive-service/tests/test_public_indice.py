# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Índice público de información clasificada y reservada POR SERIE (C12, ADR-039).

Lo que se fija:

* **Lista blanca exacta** de claves por fila, y NADA que identifique un radicado: ni número,
  ni asunto, ni id, ni conteos, ni el `fundamento_juridico` libre.
* Una entidad inexistente es INDISTINGUIBLE de una sin clasificados (lista vacía, mismo cuerpo).
* Es una obligación legal: se sirve aunque `public_interop_enabled` esté apagado.
* document-service caído => 503, nunca una lista parcial presentada como completa.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.clients import document_client
from app.core.config import settings
from app.schemas.indice_clasificados import IndiceSeriePublico
from app.services.indice_clasificados_service import SIN_SERIE, componer_indice

RUTA = "/api/v1/public/archive/indice-clasificados/demo"

CAUSALES = [
    {"codigo": "art18_a_intimidad", "articulo": "Ley 1712/2014 art. 18", "literal": "a",
     "descripcion": "El derecho de toda persona a la intimidad", "nivel": 3, "plazo_max_meses": None},
    {"codigo": "art19_i_salud_publica", "articulo": "Ley 1712/2014 art. 19", "literal": "i",
     "descripcion": "La salud pública", "nivel": 2, "plazo_max_meses": 180},
]

CLAVES = {
    "serie_codigo", "serie_nombre", "subserie_codigo", "subserie_nombre", "categoria",
    "causal_codigo", "articulo", "literal", "causal_descripcion", "excepcion",
    "fecha_calificacion_desde", "fecha_calificacion_hasta", "plazo_maximo_meses",
    "responsable_informacion", "fecha_generacion_desde", "fecha_generacion_hasta",
    "fundamento_norma",
}


def _rad(nivel=3, causal="art18_a_intimidad", plazo=None, dia=1, norma=None, gen=None):
    return {
        "clasificacion_norma": norma,
        "fecha_generacion": gen or datetime(2025, 1, dia, 9, tzinfo=UTC),
        "radicado_id": uuid.uuid4(),
        "nivel_seguridad": nivel,
        "clasificacion_causal": causal,
        "clasificacion_plazo_meses": plazo,
        "clasificado_at": datetime(2026, 3, dia, 10, tzinfo=UTC),
    }


def _serie(rad, *, cod="CON-100", nombre="Contratos", sub=None, subnombre=None, dep="Jurídica"):
    return {
        "radicado_id": rad["radicado_id"], "serie_codigo": cod, "serie_nombre": nombre,
        "subserie_codigo": sub, "subserie_nombre": subnombre, "dependencia_responsable": dep,
    }


def test_la_fila_tiene_exactamente_las_claves_de_la_lista_blanca() -> None:
    assert set(IndiceSeriePublico.model_fields) == CLAVES


def test_agrupa_por_serie_subserie_y_causal_sin_conteos_ni_identificadores() -> None:
    a, b, c = _rad(dia=5), _rad(dia=20), _rad(nivel=2, causal="art19_i_salud_publica", plazo=24)
    series = [_serie(a), _serie(b), _serie(c, sub="CON-110", subnombre="Contratos de salud")]
    filas = componer_indice([a, b, c], series, CAUSALES)

    assert len(filas) == 2, "a y b caen en el mismo grupo: una fila, no dos"
    art18 = next(f for f in filas if f.categoria == "clasificada")
    assert (art18.fecha_calificacion_desde.day, art18.fecha_calificacion_hasta.day) == (5, 20)
    assert art18.plazo_maximo_meses is None, "art. 18: mientras subsista el derecho"
    assert art18.articulo == "Ley 1712/2014 art. 18" and art18.literal == "a"
    assert art18.excepcion == "total" and art18.responsable_informacion == "Jurídica"
    art19 = next(f for f in filas if f.categoria == "reservada")
    assert art19.subserie_codigo == "CON-110" and art19.plazo_maximo_meses == 24

    texto = json.dumps([f.model_dump(mode="json") for f in filas])
    for rad in (a, b, c):
        assert str(rad["radicado_id"]) not in texto
    for prohibido in ("tracking", "subject", "fundamento_juridico", "count", "radicado_id", "numero"):
        assert prohibido not in texto.lower()
    for f in filas:
        assert set(f.model_dump()) == CLAVES


def test_el_plazo_de_un_art18_nunca_se_publica_aunque_la_fila_lo_traiga() -> None:
    a = _rad(plazo=60)
    (fila,) = componer_indice([a], [_serie(a)], CAUSALES)
    assert fila.plazo_maximo_meses is None


def test_sin_serie_se_publica_honestamente_y_en_dos_expedientes_aparece_en_dos_series() -> None:
    sola, doble = _rad(), _rad()
    series = [_serie(doble, cod="A-1", nombre="Serie A"), _serie(doble, cod="B-1", nombre="Serie B")]
    filas = componer_indice([sola, doble], series, CAUSALES)
    nombres = sorted(f.serie_nombre for f in filas)
    assert nombres == sorted([SIN_SERIE, "Serie A", "Serie B"])
    sin = next(f for f in filas if f.serie_codigo is None)
    assert sin.serie_nombre == SIN_SERIE and sin.responsable_informacion is None
    assert filas[-1].serie_codigo is None, "«sin serie» va al final"


def test_por_clasificar_se_publica_tal_cual_sin_inventar_articulo() -> None:
    a = _rad(nivel=3, causal="por_clasificar")
    (fila,) = componer_indice([a], [_serie(a)], CAUSALES)
    assert fila.causal_codigo == "por_clasificar" and fila.causal_descripcion == "Pendiente de motivar"
    assert fila.articulo is None and fila.literal is None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _patch_composicion(radicados, series):
    return (
        patch("app.routers.public_indice._tenant_operativo", new=AsyncMock(return_value=True)),
        patch(
            "app.services.indice_clasificados_service.document_client.fetch_clasificados_internal",
            new=AsyncMock(return_value={"causales": CAUSALES, "radicados": radicados}),
        ),
        patch(
            "app.services.indice_clasificados_service.series_de_radicados",
            new=AsyncMock(return_value=series),
        ),
    )


@pytest.mark.asyncio
async def test_endpoint_publico_devuelve_filas_sin_autenticar_y_sin_el_interruptor_de_cosecha(
    client_with_conn, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "public_interop_enabled", False)
    client, _ = client_with_conn
    a = _rad()
    p1, p2, p3 = _patch_composicion([a], [_serie(a)])
    with p1, p2, p3:
        resp = await client.get(RUTA)
    assert resp.status_code == 200, resp.text
    (fila,) = resp.json()
    assert set(fila) == CLAVES and fila["serie_codigo"] == "CON-100"


@pytest.mark.asyncio
async def test_entidad_inexistente_es_indistinguible_de_una_sin_clasificados(client_with_conn) -> None:
    client, _ = client_with_conn
    with patch("app.routers.public_indice._tenant_operativo", new=AsyncMock(return_value=False)):
        inexistente = await client.get("/api/v1/public/archive/indice-clasificados/no-existe")
    p1, p2, p3 = _patch_composicion([], [])
    with p1, p2, p3:
        vacia = await client.get(RUTA)
    assert inexistente.status_code == vacia.status_code == 200
    assert inexistente.json() == vacia.json() == []
    assert inexistente.headers["content-type"] == vacia.headers["content-type"]


@pytest.mark.asyncio
async def test_document_service_caido_es_503_no_una_lista_parcial(client_with_conn) -> None:
    client, _ = client_with_conn
    with (
        patch("app.routers.public_indice._tenant_operativo", new=AsyncMock(return_value=True)),
        patch(
            "app.services.indice_clasificados_service.document_client.fetch_clasificados_internal",
            new=AsyncMock(side_effect=document_client.DocumentClientError("caído")),
        ),
    ):
        resp = await client.get(RUTA)
    assert resp.status_code == 503 and resp.json()["detail"]["error"] == "indice_no_disponible"


@pytest.mark.asyncio
async def test_csv_lleva_el_mismo_contenido_y_neutraliza_formulas(client_with_conn) -> None:
    client, _ = client_with_conn
    a = _rad()
    p1, p2, p3 = _patch_composicion([a], [_serie(a, nombre="=HYPERLINK(\"x\")")])
    with p1, p2, p3:
        resp = await client.get(RUTA + ".csv")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    lineas = resp.text.strip().splitlines()
    assert lineas[0].split(",") == list(IndiceSeriePublico.model_fields)
    assert "'=HYPERLINK" in lineas[1] and ",=HYPERLINK" not in lineas[1]


@pytest.mark.asyncio
async def test_la_consulta_de_series_solo_cuenta_vinculos_vigentes_y_no_trae_datos_del_radicado() -> None:
    from app.repositories.clasificados_serie_repository import series_de_radicados

    conn = AsyncMock()
    conn.fetch.return_value = []
    await series_de_radicados(conn, [uuid.uuid4()])
    sql = str(conn.fetch.await_args.args[0])
    assert "er.estado = 'vinculado'" in sql
    assert "tracking_number" not in sql and "subject" not in sql
    assert await series_de_radicados(conn, []) == []


@pytest.mark.asyncio
async def test_el_cliente_pide_con_token_interno_y_tenant(monkeypatch) -> None:
    vistas: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200, json={"causales": [], "radicados": []})

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "document_internal_token", "tok-doc")
    await document_client.fetch_clasificados_internal(tenant_slug="demo")
    req = vistas[-1]
    assert req.url.path == "/api/v1/internal/documentos/clasificados"
    assert req.headers["X-Internal-Token"] == "tok-doc" and req.headers["X-Tenant-Slug"] == "demo"


def test_el_rango_de_generacion_y_la_cita_normativa_salen_por_grupo() -> None:
    """Decreto 103/2015 art. 40: rango de generación y fundamento legal publicable; la cita
    parte el grupo (dos citas distintas no se mezclan) y el fundamento libre no sale."""
    a = _rad(dia=5, norma="Ley 1581 de 2012, art. 5", gen=datetime(2020, 2, 3, tzinfo=UTC))
    b = _rad(dia=9, norma="Ley 1581 de 2012, art. 5", gen=datetime(2021, 7, 9, tzinfo=UTC))
    c = _rad(dia=9, norma=None)
    filas = componer_indice([a, b, c], [_serie(a), _serie(b), _serie(c)], CAUSALES)

    con = next(f for f in filas if f.fundamento_norma)
    assert con.fundamento_norma == "Ley 1581 de 2012, art. 5"
    assert (con.fecha_generacion_desde.year, con.fecha_generacion_hasta.year) == (2020, 2021)
    assert len(filas) == 2
    assert "fundamento_juridico" not in set(IndiceSeriePublico.model_fields)

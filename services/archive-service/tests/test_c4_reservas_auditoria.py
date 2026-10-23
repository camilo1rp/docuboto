# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""C-4, reservas de las dos auditorías (ADR-040): ratificación con productora (A1), cuadro «según lo
convalidado» (A4), retención mínima (A7) y frontera de lectura del circuito (S1)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.services.cuadro_clasificacion import armar_cuadro
from tests.conftest import (
    TENANT_HEADERS,
    declara_productoras,
    make_trd_serie_record,
    responde_series,
)

_CONV = {
    "acto_administrativo": "Resolución 1",
    "fecha_convalidacion": "2026-03-01",
    "instancia_convalidante": "Consejo Departamental",
}


def _audits(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ── A1: la ratificación `migrada → convalidada` captura la productora ──────────────────


def _migrada_y_convalidada(code: str = "MG"):
    migrada = make_trd_serie_record(code=code, estado="migrada")
    convalidada = make_trd_serie_record(
        serie_id=migrada["id"], code=code, estado="convalidada", acto_administrativo="R"
    )
    return migrada, convalidada


@pytest.mark.asyncio
async def test_ratificar_una_migrada_declara_la_productora_en_la_misma_transaccion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 0"
    migrada, convalidada = _migrada_y_convalidada()
    mock_conn.fetchrow.side_effect = [migrada, convalidada]

    async def _fetch(sql: str, *a: Any, **k: Any) -> list[dict[str, Any]]:
        if "FROM trd_serie_dependencia_productora" in sql:
            # 1.ª lectura (¿ya tiene?): vacía; 2.ª (gate, tras el UPDATE): la declarada;
            # 3.ª+ (armar la respuesta): la declarada otra vez
            return _fetch.rondas.pop(0) if len(_fetch.rondas) > 1 else _fetch.rondas[0]
        if "FROM dependencias WHERE codigo = ANY" in sql:
            return [{"codigo": 110, "nombre": "Subdirección Administrativa", "activo": True}]
        if "sube" in sql:
            from tests.conftest import filas_organigrama

            return filas_organigrama([110])
        return []

    _fetch.rondas = [  # type: ignore[attr-defined]
        [],
        [{"trd_serie_id": convalidada["id"], "dependencia_codigo": 110,
          "dependencia_nombre": "x", "ruta_convalidada": None}],
    ]
    mock_conn.fetch.side_effect = _fetch

    resp = await client.post(
        "/api/v1/trd/MG/versiones/1/convalidar",
        json={**_CONV, "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    inserts = [c for c in mock_conn.executemany.call_args_list if "productora" in c.args[0]]
    assert [f[1] for f in inserts[0].args[1]] == [110]
    acciones = [a[5] for a in _audits(mock_conn)]
    assert acciones == ["archive.trd_productora_declarada", "archive.trd_version_convalidada"]
    declarada = json.loads(_audits(mock_conn)[0][8])
    assert declarada["dependencias_productoras"] == [110]
    assert "preexistente" in declarada["motivo"]


@pytest.mark.asyncio
async def test_ratificar_sin_productora_sigue_siendo_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    migrada, convalidada = _migrada_y_convalidada()
    mock_conn.fetchrow.side_effect = [migrada, convalidada]
    declara_productoras(mock_conn, convalidada["id"], [])

    resp = await client.post("/api/v1/trd/MG/versiones/1/convalidar", json=_CONV, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_serie_sin_productora"


@pytest.mark.asyncio
async def test_no_se_reemplaza_una_productora_ya_declarada_al_ratificar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    migrada, _ = _migrada_y_convalidada()
    mock_conn.fetchrow.side_effect = [migrada]
    declara_productoras(mock_conn, migrada["id"], [100])

    resp = await client.post(
        "/api/v1/trd/MG/versiones/1/convalidar",
        json={**_CONV, "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "productora_ya_declarada"
    assert not mock_conn.executemany.called


@pytest.mark.asyncio
async def test_la_productora_en_el_body_solo_vale_para_ratificar_una_migrada(client_with_conn) -> None:
    """En el circuito normal (`aprobada`) la productora se declaró en el borrador."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        make_trd_serie_record(code="AP", estado="aprobada", acta_comite="A", fecha_aprobacion_comite=date(2026, 1, 1))
    ]

    resp = await client.post(
        "/api/v1/trd/AP/versiones/1/convalidar",
        json={**_CONV, "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "productora_solo_en_ratificacion"


@pytest.mark.asyncio
async def test_la_tvd_no_recibe_productora_al_convalidar(client_with_conn) -> None:
    from tests.conftest import make_tvd_agrupacion_record

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        make_tvd_agrupacion_record(estado="aprobada", acta_comite="A", fecha_aprobacion_comite=date(2026, 1, 1))
    ]
    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/convalidar",
        json={**_CONV, "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "productora_solo_en_serie"


# ── A4: el cuadro ubica según lo CONVALIDADO ─────────────────────────────────────────


def _nodo(codigo, sigla, nombre, ruta):
    return {"codigo": codigo, "sigla": sigla, "nombre": nombre, "activo": True,
            "ruta": [{"codigo": c, "sigla": s, "nombre": n} for c, s, n in ruta]}


_DG, _SA, _SF = (100, "DG", "Dirección General"), (110, "SA", "Subdirección Administrativa"), (120, "SF", "Subdirección Financiera")
# Organigrama VIVO tras una reestructuración: 110 pasó a colgar de 120.
_VIVO = {
    100: _nodo(*_DG, [_DG]),
    120: _nodo(*_SF, [_DG, _SF]),
    110: _nodo(*_SA, [_DG, _SF, _SA]),
}
_CONGELADA = [{"codigo": 100, "sigla": "DG", "nombre": "x"}, {"codigo": 110, "sigla": "SA", "nombre": "x"}]


def _cuadro(segun: str):
    s = {"id": uuid.uuid4(), "code": "100-25", "name": "Actas", "version": 2, "estado": "convalidada",
         "disposition": "CT", "archivo_gestion_years": 2, "archivo_central_years": 8}
    return armar_cuadro(
        series=[s], subseries=[], productoras={s["id"]: [{"dependencia_codigo": 110, "ruta_convalidada": _CONGELADA}]},
        nodos=_VIVO, fondo={"nombre": "F", "sigla": None},
        generado_at=datetime(2026, 10, 20, tzinfo=UTC), segun=segun,
    )


def test_por_omision_la_serie_convalidada_se_ubica_donde_la_aprobo_el_consejo() -> None:
    c = _cuadro("convalidado")
    sub = c.secciones[0].subsecciones[0]
    assert (sub.codigo, [x.code for x in sub.series]) == (110, ["100-25"])  # 100 > 110, no 100 > 120 > 110
    # y la reorganización se DICE
    assert c.secciones[0].subsecciones[0].series[0].ruta_difiere_de_la_convalidada is True
    assert [a.tipo for a in c.avisos] == ["organigrama_reorganizado_tras_convalidacion"]


def test_segun_vivo_la_ubica_en_la_ruta_actual() -> None:
    c = _cuadro("vivo")
    sf = c.secciones[0].subsecciones[0]
    assert sf.codigo == 120 and sf.subsecciones[0].codigo == 110
    assert [x.code for x in sf.subsecciones[0].series] == ["100-25"]


def test_sin_instantanea_solo_hay_ruta_viva_en_ambos_modos() -> None:
    s = {"id": uuid.uuid4(), "code": "M", "name": "m", "version": 1, "estado": "migrada",
         "disposition": "CT", "archivo_gestion_years": 1, "archivo_central_years": 1}
    for segun in ("convalidado", "vivo"):
        c = armar_cuadro(series=[s], subseries=[], productoras={s["id"]: [{"dependencia_codigo": 110, "ruta_convalidada": None}]},
                         nodos=_VIVO, fondo={"nombre": "F"}, generado_at=datetime(2026, 1, 1, tzinfo=UTC), segun=segun)
        assert c.secciones[0].subsecciones[0].subsecciones[0].codigo == 110


def test_una_misma_dependencia_puede_aparecer_bajo_dos_caminos_tras_una_reorganizacion() -> None:
    """Una serie con instantánea (camino viejo) y otra migrada (camino vivo): el nodo se identifica
    por su CAMINO, no por su código, o la segunda quedaría colgada del padre equivocado."""
    a = {"id": uuid.uuid4(), "code": "A", "name": "a", "version": 1, "estado": "convalidada",
         "disposition": "CT", "archivo_gestion_years": 1, "archivo_central_years": 1}
    b = {**a, "id": uuid.uuid4(), "code": "B", "estado": "migrada"}
    c = armar_cuadro(
        series=[a, b], subseries=[],
        productoras={a["id"]: [{"dependencia_codigo": 110, "ruta_convalidada": _CONGELADA}],
                     b["id"]: [{"dependencia_codigo": 110, "ruta_convalidada": None}]},
        nodos=_VIVO, fondo={"nombre": "F"}, generado_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    hijos = {n.codigo: n for n in c.secciones[0].subsecciones}
    assert [x.code for x in hijos[110].series] == ["A"]
    assert [x.code for x in hijos[120].subsecciones[0].series] == ["B"]


@pytest.mark.asyncio
async def test_el_parametro_segun_viaja_y_se_valida(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    ok = await client.get("/api/v1/trd/cuadro?segun=vivo", headers=TENANT_HEADERS)
    assert ok.status_code == 200
    malo = await client.get("/api/v1/trd/cuadro?segun=ayer", headers=TENANT_HEADERS)
    assert malo.status_code == 422


# ── A7: retención mínima ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crear_con_cero_mas_cero_es_422_legible(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.post(
        "/api/v1/trd",
        json={"code": "Z", "name": "z", "archivo_gestion_years": 0, "archivo_central_years": 0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert "al menos 1 año" in resp.text
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_la_version_nueva_con_cero_mas_cero_es_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [make_trd_serie_record(code="100", estado="convalidada")]
    resp = await client.post(
        "/api/v1/trd/100/versiones",
        json={"archivo_gestion_years": 0, "archivo_central_years": 0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "trd_retencion_minima"


# ── S1: la frontera de lectura del circuito ──────────────────────────────────────────


def _estados_pedidos(mock_conn) -> list[Any]:
    return [c.args for c in mock_conn.fetch.call_args_list if "FROM v_trd_series" in c.args[0]]


@pytest.mark.asyncio
async def test_el_listado_por_omision_es_el_instrumento_publicado(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # sin USUA_PERM_TRD
    responde_series(mock_conn, [make_trd_serie_record(code="100", estado="convalidada",
                                                      acta_comite="Acta 1", fecha_aprobacion_comite=date(2026, 1, 1),
                                                      created_by=uuid.uuid4())])
    resp = await client.get("/api/v1/trd", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    (fila,) = resp.json()
    assert (fila["acta_comite"], fila["fecha_aprobacion_comite"], fila["created_by"]) == (None, None, None)
    sql, *params = _estados_pedidos(mock_conn)[0]
    assert "estado = ANY(" in sql and ["convalidada", "migrada"] in params


@pytest.mark.asyncio
async def test_incluir_en_tramite_exige_el_permiso_del_circuito(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get("/api/v1/trd?incluir_en_tramite=true", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert not _estados_pedidos(mock_conn)


@pytest.mark.asyncio
async def test_con_el_permiso_se_sirve_todo_el_circuito(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    responde_series(mock_conn, [make_trd_serie_record(code="100", estado="borrador", acta_comite="Acta 1")])
    resp = await client.get("/api/v1/trd?incluir_en_tramite=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["acta_comite"] == "Acta 1"
    sql, *params = _estados_pedidos(mock_conn)[0]
    assert "estado = ANY(" not in sql  # sin filtro de estado


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["borrador", "aprobada"])
async def test_un_borrador_por_id_no_existe_para_quien_no_ve_el_circuito(client_with_conn, estado: str) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    mock_conn.fetchrow.return_value = make_trd_serie_record(estado=estado)
    resp = await client.get(f"/api/v1/trd/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    # y con el permiso, sí
    mock_conn.fetchval.return_value = True
    ok = await client.get(f"/api/v1/trd/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_el_detalle_publicado_no_lleva_acta_ni_autor(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        estado="convalidada", acta_comite="Acta 1", created_by=uuid.uuid4(), acto_administrativo="Res 1"
    )
    cuerpo = (await client.get(f"/api/v1/trd/{uuid.uuid4()}", headers=TENANT_HEADERS)).json()
    assert cuerpo["acta_comite"] is None and cuerpo["created_by"] is None
    assert cuerpo["acto_administrativo"] == "Res 1"  # el acto de convalidación es público


@pytest.mark.asyncio
async def test_el_cuadro_con_borradores_exige_el_permiso_del_circuito(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get("/api/v1/trd/cuadro?incluir_borradores=true", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    sin = await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)
    assert sin.status_code == 200
    mock_conn.fetchval.return_value = True
    con = await client.get("/api/v1/trd/cuadro?incluir_borradores=true", headers=TENANT_HEADERS)
    assert con.status_code == 200


# ── S4: la dependencia se bloquea mientras se escribe la productora ─────────────────────


@pytest.mark.asyncio
async def test_la_comprobacion_de_la_dependencia_es_for_share(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.side_effect = None
    mock_conn.fetch.return_value = [{"codigo": 110, "nombre": "SA", "activo": True}]
    mock_conn.fetchrow.return_value = make_trd_serie_record(code="100-25")
    await client.post(
        "/api/v1/trd",
        json={"code": "100-25", "name": "n", "archivo_gestion_years": 1, "archivo_central_years": 1,
              "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )
    sql = next(c.args[0] for c in mock_conn.fetch.call_args_list if "FROM dependencias" in c.args[0])
    assert sql.rstrip().endswith("FOR SHARE")

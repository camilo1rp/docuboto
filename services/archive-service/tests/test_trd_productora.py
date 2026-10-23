# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""C-4/C8 y tope de dos niveles (ADR-040): dependencia productora de la serie TRD.

Contrato HTTP con la conexión mockeada; lo que un mock no puede decir (los triggers de la
069/070, la ruta congelada, el ciclo del organigrama) está en `tests/integration/`."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import asyncpg
import pytest

from tests.conftest import (
    ARBOL_PRUEBA,
    TENANT_HEADERS,
    declara_productoras,
    filas_organigrama,
    make_trd_serie_record,
)

_ALTA = {
    "code": "100-25",
    "name": "Actas",
    "disposition": "CT",
    "archivo_gestion_years": 2,
    "archivo_central_years": 8,
}


def _audits(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


def catalogo(mock_conn, deps: dict[int, tuple[str, bool]]) -> None:
    """`dependencias` del catálogo vivo: código -> (nombre, activo)."""

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "trd_serie_dependencia_productora" in sql:
            return []
        if "FROM dependencias WHERE codigo = ANY" in sql:
            return [
                {"codigo": c, "nombre": n, "activo": a}
                for c, (n, a) in deps.items()
                if c in (args[0] or [])
            ]
        return []

    mock_conn.fetch.side_effect = _fetch


# ── Alta ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_alta_con_productoras_las_guarda_y_las_asienta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    catalogo(mock_conn, {110: ("Subdirección Administrativa", True), 100: ("Dirección General", True)})
    mock_conn.fetchrow.return_value = make_trd_serie_record(code="100-25")

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "dependencias_productoras": [110, 100]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201, resp.text
    # el nombre lo pone el SERVIDOR (instantánea del catálogo), no el cliente
    inserts = [c for c in mock_conn.executemany.call_args_list if "productora" in c.args[0]]
    assert len(inserts) == 1
    filas = inserts[0].args[1]
    assert sorted((f[1], f[2]) for f in filas) == [
        (100, "Dirección General"),
        (110, "Subdirección Administrativa"),
    ]
    assert [d["dependencia_codigo"] for d in resp.json()["dependencias_productoras"]] == [110, 100]
    (asiento,) = _audits(mock_conn)
    assert json.loads(asiento[8])["dependencias_productoras"] == [110, 100]


@pytest.mark.asyncio
async def test_una_dependencia_inexistente_es_422_y_no_escribe(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    catalogo(mock_conn, {})

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "dependencias_productoras": [999]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_desconocida"
    assert resp.json()["detail"]["codigos"] == [999]
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_una_dependencia_inactiva_no_puede_ser_productora(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    catalogo(mock_conn, {130: ("Oficina cerrada", False)})

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "dependencias_productoras": [130]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_inactiva"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_codigos_repetidos_se_rechazan_en_el_esquema(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "dependencias_productoras": [110, 110]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_sin_catalogo_de_dependencias_es_503_no_una_productora_sin_comprobar(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.side_effect = asyncpg.UndefinedTableError('relation "dependencias" does not exist')

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "dependencias_productoras": [110]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "catalogo_dependencias_no_disponible"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_una_subserie_no_lleva_productora(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd",
        json={**_ALTA, "parent_id": str(uuid.uuid4()), "dependencias_productoras": [110]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "productora_solo_en_serie"
    mock_conn.fetchrow.assert_not_called()


# ── Tope de dos niveles ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_padre_no_puede_ser_a_su_vez_una_subserie(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    abuelo = uuid.uuid4()
    padre = make_trd_serie_record(code="100-25", parent_id=abuelo)  # ya es subserie
    mock_conn.fetchrow.return_value = padre

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "parent_id": str(padre["id"])}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "trd_jerarquia_dos_niveles"
    assert not any("INSERT INTO trd_series" in c.args[0] for c in mock_conn.fetchrow.call_args_list)


@pytest.mark.asyncio
async def test_el_padre_inexistente_es_422_no_una_violacion_de_fk_en_crudo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/trd", json={**_ALTA, "parent_id": str(uuid.uuid4())}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "trd_padre_no_existe"


@pytest.mark.asyncio
async def test_una_serie_con_subseries_no_puede_pasar_a_ser_subserie(client_with_conn) -> None:
    """Versión nueva con `parent_id`: las subseries cuelgan de la FILA-versión del padre,
    así que se pregunta por el CÓDIGO (defensa contra los tres niveles por la puerta de las
    versiones)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_trd_serie_record(code="100")
    nuevo_padre = make_trd_serie_record(code="200")
    # vigente, (luego) nuevo padre; `code_tiene_subseries` -> fetchval True
    mock_conn.fetchrow.side_effect = [vigente, nuevo_padre]
    mock_conn.fetchval.side_effect = [True, True]  # gate RBAC, tiene subseries

    resp = await client.post(
        "/api/v1/trd/100/versiones",
        json={"parent_id": str(nuevo_padre["id"])},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "trd_jerarquia_dos_niveles"


# ── PATCH: la productora es contenido de la versión ──────────────────────────────


@pytest.mark.asyncio
async def test_patch_reemplaza_la_lista_en_borrador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, estado="borrador")
    mock_conn.fetchrow.side_effect = [existing, existing]
    catalogo(mock_conn, {100: ("Dirección General", True)})

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}", json={"dependencias_productoras": [100]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert any("DELETE FROM trd_serie_dependencia_productora" in c.args[0] for c in mock_conn.execute.call_args_list)
    (asiento,) = _audits(mock_conn)
    cambios = json.loads(asiento[8])["changes"]
    assert cambios["dependencias_productoras"] == {"before": [], "after": [100]}


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["aprobada", "convalidada", "migrada", "derogada"])
async def test_patch_de_la_productora_fuera_de_borrador_es_409(client_with_conn, estado: str) -> None:
    """Es contenido convalidado por el Consejo, no política técnica como `pdfa_profile`:
    no hay vía en sitio. La vía es versión nueva -> aprobar -> convalidar."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(serie_id=serie_id, estado=estado)

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}", json={"dependencias_productoras": [100]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_version_inmutable"


@pytest.mark.asyncio
async def test_patch_de_la_productora_en_una_subserie_es_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        serie_id=serie_id, estado="borrador", parent_id=uuid.uuid4()
    )

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}", json={"dependencias_productoras": [100]}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "productora_solo_en_serie"


# ── Versión nueva: clona la productora ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_version_nueva_clona_las_productoras_de_la_base(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_trd_serie_record(code="100", version=1, estado="convalidada")
    nueva = make_trd_serie_record(code="100", version=2, estado="borrador")
    mock_conn.fetchrow.side_effect = [vigente, nueva]

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "FROM trd_serie_dependencia_productora" in sql:
            return [
                {
                    "trd_serie_id": vigente["id"],
                    "dependencia_codigo": 110,
                    "dependencia_nombre": "Subdirección Administrativa",
                    "ruta_convalidada": None,
                }
            ]
        if "FROM dependencias WHERE codigo = ANY" in sql:
            return [{"codigo": 110, "nombre": "Subdirección Administrativa", "activo": True}]
        return []

    mock_conn.fetch.side_effect = _fetch

    resp = await client.post("/api/v1/trd/100/versiones", json={}, headers=TENANT_HEADERS)

    assert resp.status_code == 201, resp.text
    inserts = [c for c in mock_conn.executemany.call_args_list if "productora" in c.args[0]]
    assert [f[1] for f in inserts[0].args[1]] == [110]
    (asiento,) = _audits(mock_conn)
    assert json.loads(asiento[8])["dependencias_productoras"] == [110]


@pytest.mark.asyncio
async def test_una_productora_que_dejo_de_estar_activa_no_se_hereda_en_silencio(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_trd_serie_record(code="100", version=1, estado="convalidada")
    mock_conn.fetchrow.side_effect = [vigente]

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "FROM trd_serie_dependencia_productora" in sql:
            return [
                {
                    "trd_serie_id": vigente["id"],
                    "dependencia_codigo": 130,
                    "dependencia_nombre": "Oficina cerrada",
                    "ruta_convalidada": None,
                }
            ]
        if "FROM dependencias WHERE codigo = ANY" in sql:
            return [{"codigo": 130, "nombre": "Oficina cerrada", "activo": False}]
        return []

    mock_conn.fetch.side_effect = _fetch

    resp = await client.post("/api/v1/trd/100/versiones", json={}, headers=TENANT_HEADERS)

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_inactiva"
    assert resp.json()["detail"]["codigos"] == [130]
    assert not any("INSERT INTO trd_series" in c.args[0] for c in mock_conn.fetchrow.call_args_list)


# ── Gates del circuito ───────────────────────────────────────────────────────────


_APROBAR = {"acta_comite": "Acta 1", "fecha_aprobacion_comite": "2026-02-01"}
_CONVALIDAR = {
    "acto_administrativo": "Resolución 1",
    "fecha_convalidacion": "2026-03-01",
    "instancia_convalidante": "Consejo Departamental",
}


@pytest.mark.asyncio
async def test_aprobar_sin_productora_es_409(client_with_conn) -> None:
    """El Comité no aprueba un CCD que no dice quién produce."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="SP", estado="borrador")
    aprobada = make_trd_serie_record(code="SP", estado="aprobada")
    mock_conn.fetchrow.side_effect = [existing, aprobada]
    declara_productoras(mock_conn, aprobada["id"], [])

    resp = await client.post("/api/v1/trd/SP/versiones/1/aprobar", json=_APROBAR, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_serie_sin_productora"
    assert _audits(mock_conn) == []  # el 409 sale ANTES del asiento (y la tx revierte)


@pytest.mark.asyncio
async def test_aprobar_una_subserie_no_exige_productora(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    padre = uuid.uuid4()
    existing = make_trd_serie_record(code="SS", estado="borrador", parent_id=padre)
    aprobada = make_trd_serie_record(code="SS", estado="aprobada", parent_id=padre)
    mock_conn.fetchrow.side_effect = [existing, aprobada]

    resp = await client.post("/api/v1/trd/SS/versiones/1/aprobar", json=_APROBAR, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_convalidar_sin_productora_es_409_tambien_en_la_ratificacion(client_with_conn) -> None:
    """Una `migrada` se ratifica sin pasar por `aprobar`: el gate tiene que estar aquí."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    migrada = make_trd_serie_record(code="MG", estado="migrada")
    convalidada = make_trd_serie_record(code="MG", estado="convalidada", acto_administrativo="R")
    mock_conn.fetchrow.side_effect = [migrada, convalidada]
    declara_productoras(mock_conn, convalidada["id"], [])

    resp = await client.post("/api/v1/trd/MG/versiones/1/convalidar", json=_CONVALIDAR, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_serie_sin_productora"


@pytest.mark.asyncio
async def test_convalidar_congela_la_ruta_del_organigrama_vivo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 1"
    existing = make_trd_serie_record(
        code="CV", version=1, estado="aprobada", acta_comite="A", fecha_aprobacion_comite=date(2026, 2, 1)
    )
    convalidada = make_trd_serie_record(
        code="CV", version=1, estado="convalidada", acto_administrativo="R", fecha_convalidacion=date(2026, 3, 1)
    )
    mock_conn.fetchrow.side_effect = [existing, None, convalidada]
    declara_productoras(mock_conn, convalidada["id"], [110])

    resp = await client.post("/api/v1/trd/CV/versiones/1/convalidar", json=_CONVALIDAR, headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    fijar = [c.args for c in mock_conn.execute.call_args_list if "SET ruta_convalidada" in c.args[0]]
    assert len(fijar) == 1
    assert "ruta_convalidada IS NULL" in fijar[0][0]  # fill-once también en la consulta
    assert fijar[0][2] == 110
    ruta = json.loads(fijar[0][3])
    assert [n["codigo"] for n in ruta] == [100, 110]  # de la raíz a la productora
    asiento = next(a for a in _audits(mock_conn) if a[5] == "archive.trd_version_convalidada")
    cuerpo = json.loads(asiento[8])
    assert cuerpo["dependencias_productoras"] == [110]
    assert cuerpo["rutas_convalidadas"] == {"110": [100, 110]}


@pytest.mark.asyncio
async def test_un_ciclo_en_el_organigrama_impide_convalidar(client_with_conn) -> None:
    """Si el árbol tiene un ciclo, la convalidación ENTERA falla (409): una ruta congelada
    sobre un organigrama corrupto sería una afirmación falsa con el sello del Consejo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(
        code="CY", version=1, estado="aprobada", acta_comite="A", fecha_aprobacion_comite=date(2026, 2, 1)
    )
    convalidada = make_trd_serie_record(code="CY", version=1, estado="convalidada", acto_administrativo="R")
    mock_conn.fetchrow.side_effect = [existing, None, convalidada]

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "trd_serie_dependencia_productora" in sql:
            return [
                {
                    "trd_serie_id": convalidada["id"],
                    "dependencia_codigo": 110,
                    "dependencia_nombre": "x",
                    "ruta_convalidada": None,
                }
            ]
        filas = filas_organigrama([110])
        filas[-1]["es_ciclo"] = True
        return filas

    mock_conn.fetch.side_effect = _fetch

    resp = await client.post("/api/v1/trd/CY/versiones/1/convalidar", json=_CONVALIDAR, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "organigrama_con_ciclo"
    assert resp.json()["detail"]["codigos"] == [110]
    assert not any("SET ruta_convalidada" in c.args[0] for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_sin_catalogo_de_dependencias_no_se_convalida(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(
        code="NC", version=1, estado="aprobada", acta_comite="A", fecha_aprobacion_comite=date(2026, 2, 1)
    )
    convalidada = make_trd_serie_record(code="NC", version=1, estado="convalidada", acto_administrativo="R")
    mock_conn.fetchrow.side_effect = [existing, None, convalidada]

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "trd_serie_dependencia_productora" in sql:
            return [
                {
                    "trd_serie_id": convalidada["id"],
                    "dependencia_codigo": 110,
                    "dependencia_nombre": "x",
                    "ruta_convalidada": None,
                }
            ]
        raise asyncpg.UndefinedTableError('relation "dependencias" does not exist')

    mock_conn.fetch.side_effect = _fetch

    resp = await client.post("/api/v1/trd/NC/versiones/1/convalidar", json=_CONVALIDAR, headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "catalogo_dependencias_no_disponible"


def test_el_arbol_de_prueba_es_el_que_dicen_los_tests() -> None:
    """Anti-vacuidad del fixture: la 110 cuelga de la 100."""
    assert [c for c, _, _ in ARBOL_PRUEBA[110]] == [110, 100]

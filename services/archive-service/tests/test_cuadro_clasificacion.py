# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Cuadro de clasificación derivado (C-4/C8, ADR-040): `armar_cuadro` pura + `GET /trd/cuadro`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from app.services.cuadro_clasificacion import (
    OrganigramaConCicloError,
    OrganigramaDemasiadoProfundoError,
    armar_cuadro,
    nodos_desde_arbol,
)
from tests.conftest import TENANT_HEADERS, filas_organigrama

AHORA = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
FONDO = {"nombre": "Fondo de Desarrollo de Cundinamarca", "sigla": "FDC"}


def _nodo(codigo: int, sigla: str, nombre: str, ruta: list[tuple[int, str, str]], activo: bool = True):
    return {
        "codigo": codigo,
        "sigla": sigla,
        "nombre": nombre,
        "activo": activo,
        "ruta": [{"codigo": c, "sigla": s, "nombre": n} for c, s, n in ruta],
    }


DG = (100, "DG", "Dirección General")
SA = (110, "SA", "Subdirección Administrativa")
SF = (120, "SF", "Subdirección Financiera")
TH = (111, "TH", "Talento Humano")

NODOS = {
    100: _nodo(*DG, [DG]),
    110: _nodo(*SA, [DG, SA]),
    111: _nodo(*TH, [DG, SA, TH]),  # tres niveles: el organigrama manda, no un tope de 2
    120: _nodo(*SF, [DG, SF]),
}


def _serie(code: str, *, version: int = 1, estado: str = "convalidada", **kw: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "code": code,
        "name": f"Serie {code}",
        "version": version,
        "estado": estado,
        "disposition": "CT",
        "archivo_gestion_years": 2,
        "archivo_central_years": 8,
        **kw,
    }


def _sub(code: str, padre: str, *, estado: str = "convalidada", version: int = 1) -> dict[str, Any]:
    return {**_serie(code, estado=estado, version=version), "parent_code": padre}


def _cuadro(series, subseries=(), productoras=None, nodos=NODOS, fondo=FONDO, **kw):
    return armar_cuadro(
        series=series,
        subseries=list(subseries),
        productoras=productoras or {},
        nodos=nodos,
        fondo=fondo,
        generado_at=AHORA,
        **kw,
    )


def _p(codigo: int, ruta=None) -> dict[str, Any]:
    return {"dependencia_codigo": codigo, "ruta_convalidada": ruta}


def _ruta(*tramos) -> list[dict[str, Any]]:
    return [{"codigo": c, "sigla": s, "nombre": n} for c, s, n in tramos]


# ── Derivación ─────────────────────────────────────────────────────────────────


def test_una_serie_cuelga_de_la_ultima_dependencia_de_su_ruta() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110)]})

    assert c.fondo.nombre == "Fondo de Desarrollo de Cundinamarca"
    (seccion,) = c.secciones
    assert (seccion.codigo, seccion.nivel, seccion.series) == (100, "seccion", [])
    (sub,) = seccion.subsecciones
    assert (sub.codigo, sub.nivel) == (110, "subseccion")
    assert [x.code for x in sub.series] == ["100-25"]
    assert c.sin_seccion == [] and c.avisos == []


def test_si_la_productora_es_raiz_la_serie_cuelga_de_la_seccion() -> None:
    s = _serie("100-01")
    c = _cuadro([s], productoras={s["id"]: [_p(100)]})

    assert [x.code for x in c.secciones[0].series] == ["100-01"]
    assert c.secciones[0].subsecciones == []


def test_la_profundidad_es_la_del_organigrama_no_una_fija() -> None:
    s = _serie("TH-1")
    c = _cuadro([s], productoras={s["id"]: [_p(111)]})

    sec = c.secciones[0]
    assert [n.codigo for n in (sec, sec.subsecciones[0], sec.subsecciones[0].subsecciones[0])] == [100, 110, 111]
    assert sec.subsecciones[0].subsecciones[0].nivel == "subseccion"


def test_una_serie_con_dos_productoras_aparece_bajo_cada_una_marcada_compartida() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110), _p(120)]})

    colocadas = [x for sub in c.secciones[0].subsecciones for x in sub.series]
    assert sorted(x.code for x in colocadas) == ["100-25", "100-25"]
    assert all(x.compartida for x in colocadas)


def test_una_serie_con_una_productora_no_es_compartida() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110)]})
    assert c.secciones[0].subsecciones[0].series[0].compartida is False


def test_las_subseries_se_agrupan_por_el_codigo_del_padre_no_por_su_version() -> None:
    """Las subseries apuntan a la fila-versión que era vigente al crearlas; al versionar la
    serie siguen colgando de la anterior. El cuadro agrupa por CÓDIGO (hallazgo §0.6)."""
    s_v3 = _serie("100-25", version=3)
    hija = _sub("100-25-01", "100-25")  # su parent_id apuntaría a la v1, pero el código es el mismo
    c = _cuadro([s_v3], [hija], {s_v3["id"]: [_p(110)]})

    serie = c.secciones[0].subsecciones[0].series[0]
    assert serie.version == 3
    assert [h.code for h in serie.subseries] == ["100-25-01"]


def test_una_subserie_cuya_serie_no_esta_en_el_cuadro_se_dice_no_se_omite() -> None:
    c = _cuadro([], [_sub("X-01", "X")])
    assert [(m.code, m.motivo) for m in c.sin_seccion] == [("X-01", "serie_padre_no_vigente")]


def test_una_subserie_vigente_no_cuelga_de_un_borrador_de_su_serie() -> None:
    vigente = _serie("A", version=1)
    borrador = _serie("A", version=2, estado="borrador")
    hija = _sub("A-1", "A", estado="convalidada")
    c = _cuadro(
        [vigente, borrador],
        [hija],
        {vigente["id"]: [_p(110)], borrador["id"]: [_p(110)]},
    )

    por_version = {x.version: x for x in c.secciones[0].subsecciones[0].series}
    assert [h.code for h in por_version[1].subseries] == ["A-1"]
    assert por_version[2].subseries == []


# ── Sin sección ──────────────────────────────────────────────────────────────────


def test_una_serie_sin_productora_va_a_sin_seccion() -> None:
    s = _serie("200-10", estado="migrada")
    c = _cuadro([s])
    assert c.secciones == []
    assert [(x.code, x.estado, x.motivo) for x in c.sin_seccion] == [("200-10", "migrada", "sin_productora")]


def test_una_productora_que_ya_no_existe_se_avisa_y_la_serie_no_desaparece() -> None:
    s = _serie("300-02")
    c = _cuadro([s], productoras={s["id"]: [_p(999)]})
    assert [(x.code, x.motivo) for x in c.sin_seccion] == [("300-02", "productora_desconocida")]
    assert [(a.tipo, a.dependencia_codigo, a.series) for a in c.avisos] == [
        ("productora_desconocida", 999, ["300-02"])
    ]


# ── Avisos ────────────────────────────────────────────────────────────────────────


def test_una_productora_inactiva_sigue_mostrandose_y_se_avisa() -> None:
    nodos = {**NODOS, 120: _nodo(*SF, [DG, SF], activo=False)}
    s = _serie("300-02")
    c = _cuadro([s], productoras={s["id"]: [_p(120)]}, nodos=nodos)

    sub = c.secciones[0].subsecciones[0]
    assert (sub.codigo, sub.activo) == (120, False)
    assert [x.code for x in sub.series] == ["300-02"]
    assert [(a.tipo, a.dependencia_codigo, a.series) for a in c.avisos] == [
        ("productora_inactiva", 120, ["300-02"])
    ]


def test_si_el_organigrama_cambio_tras_convalidar_se_dice_y_no_se_reescribe() -> None:
    """Reestructuración: la ruta viva (100 > 110) ya no es la que aprobó el Consejo
    (100 > 120 > 110 al convalidar). El sistema lo DICE; la versión convalidada no se toca."""
    s = _serie("100-25")
    convalidada = _ruta(DG, SF, SA)
    c = _cuadro([s], productoras={s["id"]: [_p(110, convalidada)]}, segun="vivo")

    colocada = c.secciones[0].subsecciones[0].series[0]
    assert colocada.ruta_difiere_de_la_convalidada is True
    assert [(a.tipo, a.dependencia_codigo, a.series) for a in c.avisos] == [
        ("organigrama_reorganizado_tras_convalidacion", 110, ["100-25"])
    ]


def test_si_la_ruta_coincide_con_la_convalidada_no_hay_aviso() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110, _ruta(DG, SA))]})
    assert c.secciones[0].subsecciones[0].series[0].ruta_difiere_de_la_convalidada is False
    assert c.avisos == []


def test_sin_instantanea_la_comparacion_es_null_no_falso() -> None:
    """Migrada/borrador: no hay ruta convalidada contra la que comparar. `false` afirmaría
    una concordancia que nadie comprobó."""
    s = _serie("100-25", estado="borrador")
    c = _cuadro([s], productoras={s["id"]: [_p(110, None)]})
    assert c.secciones[0].subsecciones[0].series[0].ruta_difiere_de_la_convalidada is None


def test_un_cambio_de_nombre_no_cuenta_como_reorganizacion() -> None:
    """Se compara por CÓDIGOS: renombrar una dependencia es esperable y no invalida lo aprobado."""
    s = _serie("100-25")
    antigua = _ruta((100, "DG", "Dirección General (nombre viejo)"), (110, "SA", "Nombre viejo"))
    c = _cuadro([s], productoras={s["id"]: [_p(110, antigua)]})
    assert c.secciones[0].subsecciones[0].series[0].ruta_difiere_de_la_convalidada is False
    assert c.avisos == []
    # y el cuadro muestra el nombre VIGENTE
    assert c.secciones[0].nombre == "Dirección General"


@pytest.mark.parametrize("nombre", [None, "", "   "])
def test_sin_nombre_de_entidad_no_se_inventa_el_fondo(nombre) -> None:
    c = _cuadro([], fondo={"nombre": nombre, "sigla": None})
    assert c.fondo.nombre is None
    assert [a.tipo for a in c.avisos] == ["entidad_sin_nombre"]


# ── Poda ──────────────────────────────────────────────────────────────────────────


def test_las_ramas_sin_series_se_podan_por_omision() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110)]})
    codigos = [n.codigo for n in c.secciones[0].subsecciones]
    assert codigos == [110]  # 111 y 120 no aparecen


def test_con_incluir_vacias_aparece_todo_el_organigrama() -> None:
    s = _serie("100-25")
    c = _cuadro([s], productoras={s["id"]: [_p(110)]}, incluir_vacias=True)

    def recorre(n):
        yield n.codigo
        for h in n.subsecciones:
            yield from recorre(h)

    assert sorted(x for r in c.secciones for x in recorre(r)) == [100, 110, 111, 120]


def test_el_orden_es_estable_por_codigo() -> None:
    a, b = _serie("B-1"), _serie("A-1")
    c = _cuadro([a, b], productoras={a["id"]: [_p(100)], b["id"]: [_p(100)]})
    assert [x.code for x in c.secciones[0].series] == ["A-1", "B-1"]


# ── El árbol: ciclo y profundidad no se omiten en silencio ───────────────────────────


def test_nodos_desde_el_arbol_arma_la_ruta_de_la_raiz_a_la_propia_dependencia() -> None:
    nodos = nodos_desde_arbol(filas_organigrama([110, 100]), profundidad_max=32)
    assert [n["codigo"] for n in nodos[110]["ruta"]] == [100, 110]
    assert [n["codigo"] for n in nodos[100]["ruta"]] == [100]


def test_un_ciclo_se_lanza_no_se_trunca() -> None:
    filas = filas_organigrama([110])
    filas[-1]["es_ciclo"] = True
    with pytest.raises(OrganigramaConCicloError) as exc:
        nodos_desde_arbol(filas, profundidad_max=32)
    assert exc.value.codigos == [110]


def test_una_cadena_cortada_por_el_tope_se_lanza() -> None:
    filas = filas_organigrama([110])
    filas[-1]["tiene_padre"] = True  # la recursión paró con un padre pendiente
    with pytest.raises(OrganigramaDemasiadoProfundoError):
        nodos_desde_arbol(filas, profundidad_max=32)


# ── Endpoint ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_endpoint_deriva_el_cuadro_en_una_transaccion_repeatable_read(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    s = {
        "id": uuid.uuid4(), "code": "100-25", "name": "Actas", "version": 3, "estado": "convalidada",
        "disposition": "CT", "archivo_gestion_years": 2, "archivo_central_years": 8,
    }

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "trd_serie_dependencia_productora" in sql:
            return [{
                "trd_serie_id": s["id"], "dependencia_codigo": 110,
                "dependencia_nombre": "Subdirección Administrativa", "ruta_convalidada": None,
            }]
        if "FROM dependencias" in sql and "sube" in sql:
            return filas_organigrama([100, 110])
        if "JOIN v_trd_series p" in sql:
            return []
        if "FROM v_trd_series" in sql:
            return [s]
        return []

    mock_conn.fetch.side_effect = _fetch
    mock_conn.fetchval.return_value = "Fondo de Desarrollo de Cundinamarca"  # leer_param

    resp = await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["fondo"]["nombre"] == "Fondo de Desarrollo de Cundinamarca"
    assert cuerpo["secciones"][0]["subsecciones"][0]["series"][0]["code"] == "100-25"
    assert "generado_at" in cuerpo
    # Una sola instantánea para series + productoras + árbol.
    mock_conn.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)


@pytest.mark.asyncio
async def test_el_endpoint_responde_a_cualquier_usuario_autenticado_del_tenant(client_with_conn) -> None:
    """Mismo gate que `GET /trd`: el CCD es instrumento público (Ley 1712 art. 13) y no
    expone expedientes. Sin `USUA_PERM_TRD` también responde."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get("/api/v1/trd/cuadro", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_la_ruta_cuadro_no_la_captura_serie_id(client_with_conn) -> None:
    """`/cuadro` va ANTES de `/{serie_id}`; si no, "cuadro" caería ahí y reventaría con 422."""
    client, _ = client_with_conn
    resp = await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)
    assert resp.status_code != 422


@pytest.mark.asyncio
async def test_un_ciclo_en_el_organigrama_es_409_con_los_codigos(client_with_conn) -> None:
    client, mock_conn = client_with_conn

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "FROM dependencias" in sql and "sube" in sql:
            filas = filas_organigrama([110])
            filas[-1]["es_ciclo"] = True
            return filas
        return []

    mock_conn.fetch.side_effect = _fetch

    resp = await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "organigrama_con_ciclo"
    assert resp.json()["detail"]["codigos"] == [110]


@pytest.mark.asyncio
async def test_sin_catalogo_de_dependencias_es_503_no_un_cuadro_vacio(client_with_conn) -> None:
    client, mock_conn = client_with_conn

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "FROM dependencias" in sql:
            raise asyncpg.UndefinedTableError('relation "dependencias" does not exist')
        return []

    mock_conn.fetch.side_effect = _fetch

    resp = await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "catalogo_dependencias_no_disponible"


@pytest.mark.asyncio
async def test_incluir_borradores_viaja_a_la_consulta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # con USUA_PERM_TRD: sin él, incluir_borradores es 403
    visto: list[Any] = []

    async def _fetch(sql: str, *args: Any, **kw: Any) -> list[dict[str, Any]]:
        if "FROM v_trd_series s" in sql:
            visto.append(args[0])
        return []

    mock_conn.fetch.side_effect = _fetch

    await client.get("/api/v1/trd/cuadro?incluir_borradores=true", headers=TENANT_HEADERS)
    await client.get("/api/v1/trd/cuadro", headers=TENANT_HEADERS)

    assert visto[:2] == [True, True] and visto[2:] == [False, False]

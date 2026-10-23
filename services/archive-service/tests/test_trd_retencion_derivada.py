# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""C-4/C6 (ADR-040, antes AREA-06-07): la retención de una serie tiene UNA fuente, las dos
fases (`archivo_gestion_years` / `archivo_central_years`).

`retention_years` / `total_retention` ya no son columnas ni se aceptan en la escritura
(422 `extra_forbidden`): la respuesta los DERIVA (gestión / gestión + central), de modo que no
hay dos números que puedan quedar en desacuerdo. La premisa anterior de este archivo —«dos
juegos que el servicio mantiene de acuerdo»— desapareció con la columna."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.archive import TrdSerie
from app.schemas.archive import (
    TrdSerieCreate,
    TrdSerieUpdate,
    TrdSerieVersionCreate,
    TvdAgrupacionCreate,
    TvdAgrupacionUpdate,
    TvdAgrupacionVersionCreate,
)
from tests.conftest import NOW, TENANT_HEADERS, make_trd_serie_record

_LEGADOS = ("retention_years", "total_retention")


# ── Escritura: el juego legado ya no existe ──────────────────────────────────────


@pytest.mark.parametrize("campo", _LEGADOS)
@pytest.mark.parametrize(
    "esquema, base",
    [
        (TrdSerieCreate, {"code": "X", "name": "n", "archivo_gestion_years": 1, "archivo_central_years": 2}),
        (TrdSerieUpdate, {}),
        (TrdSerieVersionCreate, {}),
        (
            TvdAgrupacionCreate,
            {
                "code": "F",
                "name": "n",
                "archivo_central_years": 2,
                "fondo_nombre": "f",
                "fecha_extrema_inicial": "1990-01-01",
                "fecha_extrema_final": "1999-01-01",
                "justificacion_valoracion": "j",
            },
        ),
        (TvdAgrupacionUpdate, {}),
        (TvdAgrupacionVersionCreate, {}),
    ],
)
def test_ningun_esquema_de_escritura_acepta_el_juego_legado(esquema, base, campo) -> None:
    """422 `extra_forbidden`, no un silencio: un cliente viejo que envía `retention_years`
    tiene que enterarse de que NO fijó la retención."""
    with pytest.raises(ValidationError) as exc:
        esquema(**base, **{campo: 5})
    assert any(e["type"] == "extra_forbidden" for e in exc.value.errors())


@pytest.mark.parametrize("faltante", ["archivo_gestion_years", "archivo_central_years"])
def test_el_alta_de_una_serie_exige_las_dos_fases(faltante: str) -> None:
    base = {"code": "X", "name": "n", "archivo_gestion_years": 1, "archivo_central_years": 2}
    del base[faltante]
    with pytest.raises(ValidationError) as exc:
        TrdSerieCreate(**base)
    assert any(faltante in e["loc"] for e in exc.value.errors())


def test_cero_es_valido_en_una_fase_pero_no_en_las_dos_ni_negativo() -> None:
    TrdSerieCreate(code="X", name="n", archivo_gestion_years=0, archivo_central_years=1)
    TrdSerieCreate(code="X", name="n", archivo_gestion_years=3, archivo_central_years=0)
    with pytest.raises(ValidationError, match="al menos 1 año"):
        TrdSerieCreate(code="X", name="n", archivo_gestion_years=0, archivo_central_years=0)
    with pytest.raises(ValidationError):
        TrdSerieCreate(code="X", name="n", archivo_gestion_years=-1, archivo_central_years=0)


@pytest.mark.asyncio
async def test_el_alta_por_http_con_el_juego_legado_es_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "X", "name": "n", "retention_years": 5, "total_retention": 10},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert {e["type"] for e in resp.json()["detail"]} >= {"extra_forbidden", "missing"}
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_el_patch_con_el_juego_legado_es_422_y_no_toca_la_base(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.patch(
        f"/api/v1/trd/{uuid.uuid4()}", json={"retention_years": 99}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_la_version_nueva_con_el_juego_legado_es_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd/100/versiones", json={"total_retention": 99}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


# ── Lectura: el derivado sale de las fases, siempre ───────────────────────────────


@pytest.mark.asyncio
async def test_la_respuesta_deriva_gestion_y_gestion_mas_central(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_trd_serie_record(serie_id=serie_id, archivo_gestion_years=7, archivo_central_years=5)
    ]

    resp = await client.get(f"/api/v1/trd/{serie_id}", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert (cuerpo["retention_years"], cuerpo["total_retention"]) == (7, 12)


def test_el_derivado_esta_documentado_como_solo_lectura_en_el_openapi() -> None:
    from app.main import app

    props = app.openapi()["components"]["schemas"]["TrdSerieResponse"]["properties"]
    assert props["retention_years"]["readOnly"] is True
    assert props["total_retention"]["readOnly"] is True


def _serie(**kw) -> TrdSerie:
    base = {
        "id": uuid.uuid4(),
        "code": "100",
        "name": "n",
        "description": None,
        "disposition": "CT",
        "is_active": True,
        "created_at": NOW,
    }
    return TrdSerie(**{**base, **kw})


def test_una_tvd_sin_fase_de_gestion_deriva_cero_y_el_central() -> None:
    """La TVD no tiene fase de gestión (CHECK de la 039): 0 es honesto ahí."""
    s = _serie(tipo_instrumento="TVD", archivo_gestion_years=None, archivo_central_years=10)
    assert (s.retention_years, s.total_retention) == (0, 10)


def test_una_fase_central_ausente_no_se_convierte_en_cero() -> None:
    """Un 0 silencioso es «sin retención»: el dato corrupto falla en voz alta."""
    s = _serie(archivo_gestion_years=3, archivo_central_years=None)
    with pytest.raises(ValueError, match="archivo_central_years"):
        _ = s.total_retention


def test_el_modelo_ya_no_tiene_el_juego_legado_como_campos() -> None:
    """No se pueden pasar al constructor: la única forma de verlos es derivarlos."""
    with pytest.raises(TypeError):
        TrdSerie(  # type: ignore[call-arg]
            id=uuid.uuid4(), code="1", name="n", description=None, disposition="CT",
            is_active=True, created_at=NOW, retention_years=5, total_retention=10,
        )


# ── Calendario: sin fase central no hay calendario ─────────────────────────────────


@pytest.mark.asyncio
async def test_sin_fase_central_el_calendario_no_se_calcula_con_cero(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        serie_id=serie_id, archivo_gestion_years=3, archivo_central_years=None
    )

    resp = await client.get(
        f"/api/v1/trd/{serie_id}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "trd_sin_fase_central"


def test_el_total_para_worm_sin_fase_central_no_es_cero_sino_ninguno() -> None:
    from app.services.retention_math import total_trd_years

    assert total_trd_years(3, None) is None  # el llamador pospone (fail-closed)
    assert total_trd_years(None, 10) == 10  # TVD: sin gestión es legítimo
    assert total_trd_years(0, 0) is None  # fuera de [1, 100]
    assert total_trd_years(60, 50) is None  # techo
